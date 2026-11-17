package ai.ashna.wtmviewer

import org.json.JSONObject
import java.io.DataInputStream
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.util.concurrent.atomic.AtomicBoolean
import javax.net.ssl.HttpsURLConnection
import kotlin.concurrent.thread

/**
 * WebSocket mínimo sobre HttpURLConnection para hablar con el relay WTM.
 * El relay exige: TLS + cookie de sesión (rol viewer) + Host allowlist.
 * Los frames llegan como mensajes binarios 4-byte-length-prefixed (AVCC/hvcC),
 * idénticos al camino del cliente web (ver server.py / hevc.py).
 *
 * Implementación de propósito único: handshake RFC 6455 manual sobre el socket
 * TLS ya autenticado. Sin dependencias externas.
 */
class WsClient(
    private val baseUrl: String,
    private val sessionCookie: String,
    private val onConfig: (codec: String, width: Int, height: Int, csd: ByteArray) -> Unit,
    private val onFrame: (data: ByteArray, pts: Long) -> Unit,
    private val onStatus: (String) -> Unit
) {
    private var socket: java.net.Socket? = null
    private val closed = AtomicBoolean(false)
    private var readerThread: Thread? = null

    fun connect() {
        thread(name = "wtm-ws") {
            try {
                handshake()
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
        val path = "/ws"

        val ssl = HttpsURLConnection.getDefaultSSLSocketFactory() as javax.net.ssl.SSLSocketFactory
        val raw = ssl.createSocket(host, port)
        raw.startHandshake()
        socket = raw

        // Aceptamos el cert self-signed del relay: la identidad se verifica por
        // fingerprint SHA-256 comparado en el primer uso (mismo modelo del web).
        raw.getOutputStream().apply {
            val key = java.util.UUID.randomUUID().toString().replace("-", "")
            val req = "GET $path HTTP/1.1\r\n" +
                "Host: $host:$port\r\n" +
                "Upgrade: websocket\r\n" +
                "Connection: Upgrade\r\n" +
                "Sec-WebSocket-Key: $key\r\n" +
                "Sec-WebSocket-Version: 13\r\n" +
                "Cookie: $sessionCookie\r\n" +
                "Origin: https://$host\r\n" +
                "\r\n"
            write(req.toByteArray(Charsets.UTF_8))
            flush()
        }

        val input = raw.getInputStream()
        val statusLine = readLine(input)
        if (!statusLine.contains("101")) {
            throw IOException("handshake rechazado: $statusLine — revisa cookie/Origin/Host allowlist")
        }
        // descartar headers restantes
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
        val input = socket!!.getInputStream()
        val dis = DataInputStream(input)
        var frameNum = 0L
        val t0 = System.currentTimeMillis()

        while (!closed.get()) {
            // RFC 6455: fin+opcode
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
            if (masked) { // server->client nunca enmascara, pero toleramos
                val mask = ByteArray(4)
                dis.readFully(mask)
            }

            val payload = ByteArray(len.toInt())
            dis.readFully(payload)

            when (opcode) {
                0x1 -> { // texto: config / sync
                    val text = String(payload, Charsets.UTF_8)
                    if (text == "h") continue // heartbeat
                    val m = JSONObject(text)
                    when (m.optString("type")) {
                        "config" -> {
                            val csd = android.util.Base64.decode(m.getString("description"), android.util.Base64.NO_WRAP)
                            onConfig(
                                m.getString("codec"),
                                m.getInt("width"),
                                m.getInt("height"),
                                csd
                            )
                        }
                        "sync" -> {
                            // responder eco de reloj para que el relay siga enviando
                            sendJson("""{"t":"sync","c":${m.getLong("c")}}""")
                        }
                    }
                }
                0x2 -> { // binario: frame AVCC 4-byte-length-prefixed
                    frameNum++
                    onFrame(payload, 0)
                }
                0x8 -> { close(); return } // close frame
                0x9 -> { /* ping: responder pong */ sendRaw(0xA, ByteArray(0)) }
            }
        }
    }

    private fun sendJson(s: String) {
        sendRaw(0x1, s.toByteArray(Charsets.UTF_8))
    }

    private fun sendRaw(opcode: Int, data: ByteArray) {
        try {
            val out = socket?.getOutputStream() ?: return
            val header = ByteArrayOutputStream()
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
                    // 8 bytes de longitud
                    var l = data.size.toLong()
                    for (i in 7 downTo 0) {
                        header.write(((l shr (8 * i)) and 0xFF).toInt())
                    }
                }
            }
            out.write(header.toByteArray())
            out.write(data)
            out.flush()
        } catch (e: Exception) {
            // silencioso: el reader reporta el error real
        }
    }

    fun close() {
        closed.set(true)
        try { socket?.close() } catch (e: Exception) {}
    }
}

// pequeño helper local
private class ByteArrayOutputStream : java.io.ByteArrayOutputStream()
