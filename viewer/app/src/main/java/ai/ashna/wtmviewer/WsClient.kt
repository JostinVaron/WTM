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
 * Mismo protocolo que el cliente web (server.py / hevc.py):
 * login /api/auth con token viewer -> cookie; handshake con cookie y Origin
 * identico al Host (byte a byte, con puerto); frames binarios con prefijo
 * de 13 bytes (u8 flags | u64 ts | u32 enc) antes del AU length-prefixed.
 *
 * diag8: agrega requestKey() — manda {"t":"idr"}, el MISMO mensaje de
 * recovery que usa el cliente web cuando pierde decodificacion, para que
 * el server pida un keyframe fresco al encoder vivo (el server lo aplica
 * con rate-limit via request_key()). El decoder de la app lo usa al
 * configurarse y en cada rotacion de decoder.
 *
 * Telemetria de dos canales: consola en pantalla + POST /log del relay.
 */
class WsClient(
    private val baseUrl: String,
    private val viewerToken: String,
    private val onConfig: (codec: String, width: Int, height: Int, csd: ByteArray) -> Unit,
    private val onFrame: (data: ByteArray, pts: Long) -> Unit,
    private val onStatus: (String) -> Unit
) {
    private var socket: SSLSocket? = null
    private val closed = AtomicBoolean(false)
    private var sessionCookie: String = ""

    fun connect() {
        thread(name = "wtm-ws") {
            try {
                login()
                relayLog("viewer login OK")
                handshake()
                relayLog("viewer ws conectado (cookie+origin OK)")
                // sincronizacion de reloj inicial: 6 muestras como el cliente web
                for (i in 0 until 6) {
                    sendJson("""{"t":"sync","c":${System.currentTimeMillis()}}""")
                    Thread.sleep(120)
                }
                readLoop()
            } catch (e: Exception) {
                if (!closed.get()) {
                    onStatus("error: ${e.message}")
                    relayLog("viewer error: ${e.message}")
                }
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

    /**
     * Login con el token viewer: mismo POST /api/auth que hace el navegador.
     * 401 token malo, 403 Origin, 429 throttle; 200 + Set-Cookie.
     */
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
                // Fallback documentado del server.py: la cookie de sesion
                // lleva el valor del token (session_role compara contra auth_tokens).
                "wtm_session=$viewerToken"
        }
        onStatus("login OK — sesion viewer activa")
    }

    private fun handshake() {
        val uri = URI(baseUrl)
        val host = uri.host ?: throw IOException("URL sin host")
        val port = uri.port.takeIf { it != -1 } ?: 8443

        val raw = trustAllFactory().createSocket(host, port) as SSLSocket
        raw.startHandshake()
        socket = raw

        // Origin == "https://" + Host verbatim, ambos con puerto (como Chrome).
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
        onStatus("websocket conectado")
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

    /**
     * POST /log del relay — log_handler imprime "[client] <texto>" en la
     * consola del S25. Fire-and-forget: si falla, la consola en pantalla
     * sigue siendo el canal primario.
     */
    private fun relayLog(text: String) {
        if (sessionCookie.isEmpty()) return
        thread(name = "wtm-log", isDaemon = true) {
            try {
                val uri = URI(baseUrl)
                val host = uri.host ?: return@thread
                val url = URL(baseUrl.trimEnd('/') + "/log")
                val conn = url.openConnection() as HttpsURLConnection
                conn.sslSocketFactory = trustAllFactory()
                conn.hostnameVerifier = acceptHostname
                conn.requestMethod = "POST"
                conn.doOutput = true
                conn.connectTimeout = 5000
                conn.readTimeout = 5000
                conn.setRequestProperty("Content-Type", "text/plain")
                conn.setRequestProperty("Cookie", sessionCookie)
                conn.setRequestProperty("Origin", "https://$host")
                conn.outputStream.use { it.write(text.toByteArray(Charsets.UTF_8)) }
                conn.responseCode
                conn.disconnect()
            } catch (_: Exception) {}
        }
    }

    private fun readLoop() {
        val dis = DataInputStream(socket!!.inputStream)
        var textMsgs = 0L
        var binMsgs = 0L
        var lastSummary = System.currentTimeMillis()

        while (!closed.get()) {
            if (System.currentTimeMillis() - lastSummary >= 5000) {
                onStatus("ws resumen: texto=$textMsgs binario=$binMsgs")
                relayLog("viewer ws resumen: text=$textMsgs bin=$binMsgs")
                lastSummary = System.currentTimeMillis()
            }
            val b0 = dis.readUnsignedByte()
            val opcode = b0 and 0x0F
            val b1 = dis.readUnsignedByte()
            val masked = (b1 and 0x80) != 0
            var len = (b1 and 0x7F).toLong()
            if (len == 126L) {
                len = dis.readUnsignedShort().toLong()
            } else if (len == 127L) {
                len = dis.readLong()
            }
            if (masked) throw IOException("server no debe enmascarar frames")

            val payload = ByteArray(len.toInt())
            dis.readFully(payload)

            when (opcode) {
                0x1 -> {
                    textMsgs++
                    val text = String(payload, Charsets.UTF_8)
                    if (textMsgs <= 5)
                        onStatus("ws texto #$textMsgs (${payload.size}B): ${text.take(90)}")
                    val m = try { JSONObject(text) } catch (_: Exception) { null }
                    when (m?.optString("type") ?: "") {
                        "config" -> {
                            val csd = android.util.Base64.decode(
                                m!!.getString("description"), android.util.Base64.NO_WRAP
                            )
                            relayLog("viewer config: codec=" + m.getString("codec") +
                                " " + m.getInt("width") + "x" + m.getInt("height") +
                                " csd=" + csd.size + "B")
                            onConfig(m.getString("codec"), m.getInt("width"), m.getInt("height"), csd)
                        }
                        "sync" -> { /* eco de reloj: medir offset, NO responder */ }
                    }
                }
                0x2 -> {
                    binMsgs++
                    if (binMsgs == 1L) {
                        val headHex = if (payload.size > 13)
                            payload.copyOfRange(13, minOf(37, payload.size))
                                .joinToString(" ") { String.format("%02x", it) }
                        else "(prefijo incompleto)"
                        onStatus("PRIMER BINARIO: ${payload.size}B flags=0x" +
                            String.format("%02x", payload[0]) + " head=" + headHex)
                        relayLog("viewer primer binario: ${payload.size}B head=$headHex")
                    }
                    if (payload.size <= 13) continue
                    val tsUs = readU64(payload, 1)
                    onFrame(payload.copyOfRange(13, payload.size), tsUs)
                }
                0x8 -> {
                    val code = if (payload.size >= 2)
                        ((payload[0].toInt() and 0xFF) shl 8) or (payload[1].toInt() and 0xFF)
                    else -1
                    val reason = if (payload.size > 2)
                        String(payload.copyOfRange(2, payload.size), Charsets.UTF_8)
                    else ""
                    onStatus("ws CERRADO por el server: code=$code razon='$reason'")
                    relayLog("viewer ws close: code=$code razon='$reason'")
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
        } catch (_: Exception) {}
    }

    fun close() {
        closed.set(true)
        try { socket?.close() } catch (_: Exception) {}
    }
}
