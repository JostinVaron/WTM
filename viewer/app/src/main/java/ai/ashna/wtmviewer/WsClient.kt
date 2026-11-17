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
 * WebSocket mínimo (RFC 6455) sobre socket TLS para hablar con el relay WTM.
 * Mismo protocolo que el cliente web (server.py / hevc.py):
 *
 * 1. LOGIN: POST /api/auth con {"token": <viewer>} y Origin same-site —
 *    exactamente lo que hace el /login del navegador. El relay responde con
 *    la cookie de sesión (rol viewer); la app la guarda y la usa sola.
 * 2. WEBSOCKET: handshake con la cookie; al abrir, 6 mensajes de sync de
 *    reloj ({t:"sync", c:...}) y luego NUNCA responder a los sync del
 *    servidor (evita bucle de eco).
 * 3. VIDEO: config JSON (codec + width/height + hvcC base64) y frames
 *    binarios AVCC (4-byte NAL length prefix).
 * 4. HEARTBEAT: el servidor manda "h" como texto — se ignora (el TCP lo mantiene).
 *
 * Confianza TLS: cert self-signed del relay con SANs de la IP original;
 * aceptamos cadena y hostname (mismo modelo de confianza que el cliente
 * web). La identidad real la da el token de sesión.
 *
 * Cookie de sesión: el VALOR de la cookie es el propio token viewer
 * (session_role() compara request.cookies contra auth_tokens).
 *
 * Origin same-site EXACTO: origin_is_same_site() del server compara
 * Origin == f"https://{request.host}" byte a byte, donde request.host es
 * el header Host verbatim. Chrome manda el Origin CON puerto. Aquí
 * escribimos nosotros el Host (con puerto), así que el Origin lleva el
 * puerto también — idénticos siempre.
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
                handshake()
                // sincronización de reloj inicial: 6 muestras como el cliente web
                for (i in 0 until 6) {
                    sendJson("""{"t":"sync","c":${System.currentTimeMillis()}}""")
                    Thread.sleep(120)
                }
                readLoop()
            } catch (e: Exception) {
                if (!closed.get()) onStatus("error: ${e.message}")
            }
        }
    }

    /** Cert self-signed del relay: cadena + hostname aceptados (ver nota de clase). */
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
     * El relay responde 200 + Set-Cookie con la sesión viewer. Si el token
     * esta mal da 401; tras varios intentos fallidos el relay throttlea (429).
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

        // Extracción robusta: escanear TODAS las cabeceras sin importar
        // capitalización (getHeaderField("Set-Cookie") puede no verla).
        val setCookieValues = conn.headerFields
            ?.entries
            ?.filter { it.key?.equals("set-cookie", ignoreCase = true) == true }
            ?.flatMap { it.value }
            .orEmpty()
        conn.disconnect()

        sessionCookie = when {
            setCookieValues.isNotEmpty() ->
                // "wtm_session=abc...; Path=/; Max-Age=...; Secure; HttpOnly"
                setCookieValues.first().split(";").first().trim()
            else ->
                // Fallback documentado del server.py: el valor de la cookie
                // de sesión ES el token (session_role compara contra auth_tokens).
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

        // origin_is_same_site() del server: Origin == "https://" + Host verbatim.
        // Host y Origin se escriben aquí, así que van idénticos byte a byte,
        // ambos con puerto (como los manda Chrome).
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
            // Mostrar el MOTIVO real del server (cuerpo del 401/403), no una
            // suposición: leer cabeceras y hasta 200 bytes de cuerpo.
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

    private fun readLoop() {
        val dis = DataInputStream(socket!!.inputStream)
        var frames = 0L

        while (!closed.get()) {
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
                    val text = String(payload, Charsets.UTF_8)
                    if (text == "h") continue // heartbeat del servidor: ignorar
                    val m = JSONObject(text)
                    when (m.optString("type")) {
                        "config" -> {
                            val csd = android.util.Base64.decode(
                                m.getString("description"), android.util.Base64.NO_WRAP
                            )
                            onConfig(m.getString("codec"), m.getInt("width"), m.getInt("height"), csd)
                        }
                        "sync" -> {
                            // eco de reloj del servidor: medir offset, NO responder
                        }
                    }
                }
                0x2 -> { frames++; onFrame(payload, 0) }
                0x8 -> { close(); return }
                0x9 -> sendRaw(0xA, ByteArray(0))
            }
        }
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
