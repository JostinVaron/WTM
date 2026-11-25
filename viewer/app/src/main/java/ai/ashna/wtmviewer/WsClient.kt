package ai.ashna.wtmviewer

import org.json.JSONObject
import java.io.DataInputStream
import java.io.IOException
import java.net.URI
import java.net.URL
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocket
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager
import java.security.SecureRandom
import java.security.cert.X509Certificate
import kotlin.concurrent.thread

/**
 * WebSocket minimo (RFC 6455) sobre socket TLS para hablar con el relay WTM.
 * Mismo protocolo que el cliente web (server.py / hevc.py).
 *
 * v1.2-cap (F4b) — NEGOCIACION cap/cap_ack: tras el login se envia el cap
 * del hardware del receptor (StreamProfile.capJson); el relay clampa al
 * techo del emisor, aplica y VERIFICA contra el cmdline del scrcpy vivo, y
 * responde cap_ack. onCapAck lleva el veredicto al HUD (jamas verde falso).
 * Compatibilidad: si el relay es viejo y no conoce "cap", simplemente no
 * responde — el HUD queda en "sin respuesta del relay" y el stream sigue.
 *
 * v1.3-pen (F5) — CANAL DE INPUT: sendInput() expone el mismo WebSocket al
 * PenForwarder (pd/pm/pu/ph, un mensaje por puntero con presion/tilt reales).
 */
class WsClient(
    private val baseUrl: String,
    private val viewerToken: String,
    private val onConfig: (codec: String, width: Int, height: Int, csd: ByteArray) -> Unit,
    private val onFrame: (data: ByteArray, pts: Long) -> Unit,
    private val onStatus: (String) -> Unit,
    /** F4b: veredicto de la negociacion cap (aplicado+verificado / rechazado). */
    private val onCapAck: (org.json.JSONObject) -> Unit = {}
) {
    companion object {
        private const val HB_MS = 25L          // como el cliente web
        private const val SYNC_MIN = 5         // muestras validas antes de reportar lat
    }

    private var socket: SSLSocket? = null
    private val closed = AtomicBoolean(false)
    private var sessionCookie: String = ""
    private var dbg = 0

    /** Mensaje cap listo para enviar (F4b); se envia una vez tras el login. */
    @Volatile private var pendingCapJson: String? = null

    /** MainActivity lo fija antes de connect(): el cap del hardware real. */
    fun setCap(json: String) { pendingCapJson = json }

    /** F5: canal de input (pd/pm/pu/ph) por el mismo WebSocket del stream. */
    fun sendInput(json: String) {
        if (dbg < 30) {
            dbg++
            android.util.Log.d("WTM", "sendInput socket=${socket != null} $json")
        }
        sendJson(json)
    }

    // --- reloj/latencia (v1.1-perf1) ---
    @Volatile private var offsetMs = 0.0
    @Volatile private var bestRtt = -1.0       // ms; -1 = sin muestra aun
    private var syncSamples = 0
    private val recentLat = ArrayDeque<Double>()

    /** Media de latencia captura->llegada de los ultimos ~40 frames (ms). */
    fun latencyAvg(): Double = synchronized(recentLat) {
        if (recentLat.isEmpty()) 0.0 else recentLat.reduce { a, b -> a + b } / recentLat.size
    }

    /** Mejor RTT observado (ms); -1 = sin muestra todavia. */
    fun rttMs(): Double = bestRtt

    fun connect() {
        thread(name = "wtm-ws") {
            try {
                login()
                handshake()
                for (i in 0 until 6) {
                    sendJson("""{"t":"sync","c":${System.currentTimeMillis()}}""")
                    Thread.sleep(120)
                }
                // F4b: el cap va justo tras el login+sync — es la primera
                // cosa que el relay negocia en la sesion.
                pendingCapJson?.let { sendJson(it) }
                startHeartbeat()
                readLoop()
            } catch (e: Exception) {
                if (!closed.get()) {
                    onStatus("error: ${e.message}")
                }
            }
        }
    }

    /**
     * Heartbeat + sync periodico: mantiene la radio WiFi despierta (25 ms)
     * y recalibra el offset de reloj cada ~1 s, como el cliente web.
     */
    private fun startHeartbeat() {
        thread(name = "wtm-hb", isDaemon = true) {
            var n = 0
            while (!closed.get()) {
                try {
                    sendJson("""{"t":"hb"}""")
                    if (n % 40 == 0) {
                        sendJson("""{"t":"sync","c":${System.currentTimeMillis()}}""")
                    }
                    n++
                } catch (_: Exception) {}
                Thread.sleep(HB_MS)
            }
        }
    }

    /** Pide un keyframe fresco al encoder vivo — recovery del cliente web. */
    fun requestKey() {
        sendJson("""{"t":"idr"}""")
    }

    /** Cert self-signed del relay: cadena + hostname aceptados. */
    private fun trustAllFactory(): SSLSocketFactory {
        val ctx = SSLContext.getInstance("TLS")
        ctx.init(null, arrayOf<TrustManager>(object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}
            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
        }), SecureRandom())
        return ctx.socketFactory
    }

    private val acceptHostname = HostnameVerifier { _, _ -> true }

    private fun login() {
        val uri = URI(baseUrl)
        val host = uri.host ?: throw IOException("URL sin host")
        val url = URL(baseUrl.trimEnd('/') + "/api/auth")
        val conn = url.openConnection() as HttpsURLConnection
        conn.sslSocketFactory = trustAllFactory()
        conn.hostnameVerifier = acceptHostname
        conn.requestMethod = "POST"
        conn.doOutput = true
        conn.connectTimeout = 8000
        conn.readTimeout = 8000
        conn.setRequestProperty("Content-Type", "application/json")
        conn.setRequestProperty("Origin", "https://$host")
        val body = """{"token":"$viewerToken"}"""
        conn.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) }
        val code = conn.responseCode
        if (code != 200) {
            conn.disconnect()
            val motivo = when (code) {
                401 -> "token viewer incorrecto"
                403 -> "Origin rechazado — la URL debe ser https://<ip>:8443 exacta"
                429 -> "demasiados intentos fallidos: el relay te throttleo, espera un minuto"
                else -> "HTTP $code"
            }
            throw IOException("login fallido: $motivo")
        }

        val setCookieValues = conn.headerFields
            ?.entries
            ?.filter { it.key?.equals("set-cookie", ignoreCase = true) == true }
            ?.flatMap { it.value }
            .orEmpty()
        conn.disconnect()

        sessionCookie = when {
            setCookieValues.isNotEmpty() ->
                setCookieValues.first().split(";").first().trim()
            else ->
                "wtm_session=$viewerToken"
        }
    }

    private fun handshake() {
        val uri = URI(baseUrl)
        val host = uri.host ?: throw IOException("URL sin host")
        val port = uri.port.takeIf { it != -1 } ?: 8443

        val raw = trustAllFactory().createSocket(host, port) as SSLSocket
        raw.startHandshake()
        socket = raw
        raw.tcpNoDelay = true   // Nagle off: latencia sobre throughput

        val origin = "https://$host:$port"
        val key = java.util.Base64.getEncoder().encodeToString(ByteArray(16).also {
            SecureRandom().nextBytes(it)
        })
        val req = "GET /ws HTTP/1.1\r\n" +
            "Host: $host:$port\r\n" +
            "Upgrade: websocket\r\n" +
            "Connection: Upgrade\r\n" +
            "Sec-WebSocket-Key: $key\r\n" +
            "Sec-WebSocket-Version: 13\r\n" +
            "Cookie: $sessionCookie\r\n" +
            "Origin: $origin\r\n" +
            "\r\n"
        raw.getOutputStream().apply {
            write(req.toByteArray(Charsets.UTF_8))
            flush()
        }

        val input = raw.getInputStream()
        val statusLine = readLine(input)
        if (!statusLine.contains("101")) {
            val detail = StringBuilder()
            try {
                raw.soTimeout = 1500
                while (true) {
                    val line = readLine(input)
                    if (line.isEmpty()) break
                }
                val avail = input.available()
                if (avail > 0) {
                    val body = ByteArray(minOf(avail, 200))
                    input.read(body)
                    detail.append(String(body, Charsets.UTF_8).trim())
                }
            } catch (_: Exception) {}
            val extra = detail.toString().trim()
            throw IOException(
                if (extra.isEmpty()) "handshake rechazado: $statusLine"
                else "handshake rechazado: $statusLine — $extra"
            )
        }
        while (true) {
            val line = readLine(input)
            if (line.isEmpty()) break
        }
    }

    private fun readLine(input: java.io.InputStream): String {
        val sb = StringBuilder()
        while (true) {
            val b = input.read()
            if (b == -1) throw IOException("stream cerrado")
            if (b == '\r'.code) continue
            if (b == '\n'.code) break
            sb.append(b.toChar())
        }
        return sb.toString()
    }

    private fun readLoop() {
        val dis = DataInputStream(socket!!.inputStream)

        while (!closed.get()) {
            val b0 = dis.readUnsignedByte()
            val opcode = b0 and 0x0F
            val b1 = dis.readUnsignedByte()
            val masked = (b1 and 0x80) != 0
            var len = (b1 and 0x7F).toLong()
            if (len == 126L) {
                len = dis.readUnsignedShort().toLong()
            } else if (len == 127L) {
                len = dis.readLong().toLong()
            }
            if (masked) throw IOException("server no debe enmascarar frames")

            val payload = ByteArray(len.toInt())
            dis.readFully(payload)

            when (opcode) {
                0x1 -> {
                    val text = String(payload, Charsets.UTF_8)
                    val m = try { JSONObject(text) } catch (_: Exception) { null }
                    when (m?.optString("type") ?: m?.optString("t") ?: "") {
                        "config" -> {
                            val csd = android.util.Base64.decode(
                                m!!.getString("description"), android.util.Base64.NO_WRAP
                            )
                            onConfig(m.getString("codec"), m.getInt("width"), m.getInt("height"), csd)
                        }
                        "cap_ack" -> {
                            // F4b: veredicto de la negociacion (jamas verde falso:
                            // applied solo si el relay VERIFICO el cmdline del scrcpy).
                            onCapAck(m ?: JSONObject())
                        }
                        "sync" -> {
                            // Server: {"type":"sync","s":server_ms,"c":echo}.
                            // rtt = ahora - c ; offset = s - (c + rtt/2).
                            val now = System.currentTimeMillis()
                            val c = m!!.optDouble("c", -1.0)
                            val s = m.optDouble("s", -1.0)
                            if (c > 0 && s > 0) {
                                val rtt = now - c
                                if (rtt in 0.0..10000.0 &&
                                    (bestRtt < 0 || rtt <= bestRtt + 2.0)) {
                                    if (bestRtt < 0 || rtt < bestRtt) bestRtt = rtt
                                    offsetMs = s - (c + rtt / 2.0)
                                    syncSamples++
                                }
                            }
                        }
                        "h" -> { /* heartbeat del server */ }
                    }
                }
                0x2 -> {
                    if (payload.size <= 13) continue
                    val serverTsMs = readU64(payload, 1) / 1000.0
                    if (syncSamples >= SYNC_MIN) {
                        val lat = System.currentTimeMillis() - (serverTsMs - offsetMs)
                        synchronized(recentLat) {
                            recentLat.addLast(lat)
                            if (recentLat.size > 40) recentLat.removeFirst()
                        }
                    }
                    onFrame(payload.copyOfRange(13, payload.size), readU64(payload, 1))
                }
                0x8 -> {
                    val code = if (payload.size >= 2)
                        ((payload[0].toInt() and 0xFF) shl 8) or (payload[1].toInt() and 0xFF)
                    else -1
                    val reason = if (payload.size > 2)
                        String(payload.copyOfRange(2, payload.size), Charsets.UTF_8)
                    else ""
                    onStatus("error: sesion cerrada por el server (code=$code $reason)")
                    close()
                    return
                }
                0x9 -> sendRaw(0xA, ByteArray(0))
            }
        }
    }

    /** u64 big-endian en payload[offset..offset+7]. */
    private fun readU64(p: ByteArray, offset: Int): Long {
        var v = 0L
        for (i in 0 until 8) {
            v = (v shl 8) or (p[offset + i].toLong() and 0xFF)
        }
        return v
    }

    private fun sendJson(s: String) = sendRaw(0x1, s.toByteArray(Charsets.UTF_8))

    /**
     * Escritura serializada del socket. El hilo de sync/heartbeat y el hilo
     * de UI (PenForwarder) escriben en el MISMO socket: sin este lock los
     * frames se intercalan (cabecera de uno + payload de otro), el server
     * recibe UTF-8 invalido y cierra con 1007. Bug medido en campo 2026-11-25:
     * el primer toque o trazo mataba la sesion.
     */
    @Synchronized
    private fun sendRaw(opcode: Int, data: ByteArray) {
        try {
            val out = socket?.getOutputStream() ?: return
            val header = java.io.ByteArrayOutputStream()
            header.write(0x80 or opcode)
            when {
                data.size < 126 -> header.write(data.size)
                data.size < 65536 -> {
                    header.write(126)
                    header.write(data.size shr 8)
                    header.write(data.size and 0xFF)
                }
                else -> {
                    header.write(127)
                    var l = data.size.toLong()
                    for (i in 7 downTo 0) header.write(((l shr (8 * i)) and 0xFF).toInt())
                }
            }
            out.write(header.toByteArray())
            out.write(data)
            out.flush()
        } catch (e: Exception) {
            if (dbg < 40) { dbg++; android.util.Log.d("WTM", "sendRaw error: $e") }
        }
    }

    fun close() {
        closed.set(true)
        try { socket?.close() } catch (_: Exception) {}
    }
}
