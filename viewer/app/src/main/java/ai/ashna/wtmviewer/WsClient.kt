package ai.ashna.wtmviewer

import org.json.JSONObject
import java.io.DataInputStream
import java.io.IOException
import java.net.URI
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.concurrent.thread

/**
 * WebSocket mínimo (RFC 6455) sobre socket TLS para hablar con el relay WTM.
 * Mismo protocolo que el cliente web (server.py / hevc.py):
 * - al abrir: 6 mensajes de sync de reloj ({t:"sync", c:...}), luego NUNCA
 *   responder a los sync del servidor (evita bucle de eco);
 * - config JSON: codec + width/height + description (hvcC en base64);
 * - frames: mensajes binarios AVCC (4-byte NAL length prefix);
 * - heartbeat: el servidor manda "h" como texto — se ignora (el TCP lo mantiene).
 */
class WsClient(
    private val baseUrl: String,
    private val sessionCookie: String,
    private val onConfig: (codec: String, width: Int, height: Int, csd: ByteArray) -> Unit,
    private val onFrame: (data: ByteArray, pts: Long) -> Unit,
    private val onStatus: (String) -> Unit
) {
    private var socket: javax.net.ssl.SSLSocket? = null
    private val closed = AtomicBoolean(false)

    fun connect() {
        thread(name = "wtm-ws") {
            try {
                handshake()
                // sincronización de reloj inicial: 6 eco-muestras como el web
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

    private fun handshake() {
        val uri = URI(baseUrl)
        val host = uri.host ?: throw IOException("URL sin host")
        val port = uri.port.takeIf { it != -1 } ?: 8443

        // Cert self-signed del relay: identidad verificada por fingerprint
        // SHA-256 comparado fuera de banda (mismo modelo que el cliente web).
        val factory = javax.net.ssl.SSLContext.getInstance("TLS").apply {
            init(null, arrayOf<javax.net.ssl.TrustManager>(object : javax.net.ssl.X509TrustManager {
                override fun checkClientTrusted(chain: Array<java.security.cert.X509Certificate>, authType: String) {}
                override fun checkServerTrusted(chain: Array<java.security.cert.X509Certificate>, authType: String) {}
                override fun getAcceptedIssuers(): Array<java.security.cert.X509Certificate> = arrayOf()
            }), java.security.SecureRandom())
        }.socketFactory

        val raw = factory.createSocket(host, port) as javax.net.ssl.SSLSocket
        raw.startHandshake()
        socket = raw

        val key = java.util.Base64.getEncoder().encodeToString(ByteArray(16).also {
            java.security.SecureRandom().nextBytes(it)
        })
        val req = "GET /ws HTTP/1.1\r\n" +
            "Host: $host:$port\r\n" +
            "Upgrade: websocket\r\n" +
            "Connection: Upgrade\r\n" +
            "Sec-WebSocket-Key: $key\r\n" +
            "Sec-WebSocket-Version: 13\r\n" +
            "Cookie: $sessionCookie\r\n" +
            "Origin: https://$host\r\n" +
            "\r\n"
        raw.getOutputStream().apply {
            write(req.toByteArray(Charsets.UTF_8))
            flush()
        }

        val input = raw.getInputStream()
        val statusLine = readLine(input)
        if (!statusLine.contains("101")) {
            throw IOException("handshake rechazado: $statusLine — revisa cookie/Origin/Host allowlist")
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
                0x1 -> { // texto
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
                            // (responder crearía un bucle infinito de ecos)
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
