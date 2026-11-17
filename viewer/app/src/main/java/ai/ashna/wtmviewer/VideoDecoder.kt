package ai.ashna.wtmviewer

import android.media.MediaCodec
import android.media.MediaFormat
import android.view.Surface
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Decodificador -> Surface directo (issue #16, stage 1).
 * El relay envía frames AVCC/hvcC (4-byte length prefix) + csd — el formato
 * que MediaCodec espera, sin conversión Annex-B.
 *
 * El codec lo decide el SERVER via el mensaje config: "avc1.*" es H.264
 * (backend scrcpy por defecto), "hvc1.*"/"hev1.*" es HEVC (issue #4 A/B).
 * Hardcodear HEVC con stream H.264 produce pantalla negra silenciosa:
 * MediaCodec acepta los buffers pero nunca produce un frame.
 * Stage 3 (pendiente): re-init automático ante corrupción sostenida.
 */
class VideoDecoder(
    private val surface: Surface,
    private val onStatus: (String) -> Unit
) {
    private var codec: MediaCodec? = null
    @Volatile private var configured = false
    private val released = AtomicBoolean(false)
    private var framesFed = 0L
    private var framesOut = 0L
    private var lastW = 0
    private var lastH = 0
    private var lastCsd: ByteArray? = null
    private var lastMime = ""

    /** "avc1.*" -> H.264, "hvc1.*"/"hev1.*" -> HEVC. Desconocido -> error visible. */
    private fun mimeOf(codecName: String): String? = when {
        codecName.startsWith("avc1") -> MediaFormat.MIMETYPE_VIDEO_AVC
        codecName.startsWith("hvc1") || codecName.startsWith("hev1") ->
            MediaFormat.MIMETYPE_VIDEO_HEVC
        else -> null
    }

    fun configure(codecName: String, width: Int, height: Int, csd: ByteArray) {
        synchronized(this) {
            val mime = mimeOf(codecName)
            if (mime == null) {
                onStatus("codec no soportado: $codecName — reportar")
                return
            }
            lastW = width; lastH = height; lastCsd = csd; lastMime = mime
            try {
                codec?.run { stop(); release() }
            } catch (_: Exception) {}
            val format = MediaFormat.createVideoFormat(mime, width, height).apply {
                setByteBuffer("csd-0", java.nio.ByteBuffer.wrap(csd))
            }
            val c = MediaCodec.createDecoderByType(mime)
            c.configure(format, surface, null, 0)
            c.start()
            codec = c
            configured = true
            released.set(false)
            framesFed = 0
            framesOut = 0
            // Drenar salidas en un hilo propio: sin esto los frames decodificados
            // se acumulan y el decoder se bloquea tras pocos frames.
            Thread {
                while (configured && !released.get()) {
                    drainOutputs()
                    Thread.sleep(2)
                }
            }.start()
            onStatus("decoder ${codecName} ${width}x${height}")
        }
    }

    /**
     * Drenar buffers de salida. Cuenta los frames REALES que salen del decoder
     * (no solo los que entran) — la unica prueba de que hay video.
     */
    private fun drainOutputs() {
        val c = codec ?: return
        try {
            val info = MediaCodec.BufferInfo()
            while (true) {
                val idx = c.dequeueOutputBuffer(info, 0)
                when {
                    idx >= 0 -> {
                        c.releaseOutputBuffer(idx, true)
                        framesOut++
                        if (framesOut % 120 == 1L) onStatus("video ${lastW}x${lastH} — $framesOut frames al surface")
                    }
                    idx == MediaCodec.INFO_TRY_AGAIN_LATER -> return
                    else -> {} // INFO_OUTPUT_FORMAT_CHANGED etc.
                }
            }
        } catch (e: Exception) {
            onStatus("decoder error: ${e.message} — re-inicializando")
            reinit()
        }
    }

    fun feed(data: ByteArray, pts: Long) {
        if (released.get() || !configured) return
        val c = codec ?: return
        framesFed++
        if (framesFed % 300 == 0L && framesOut == 0L)
            onStatus("frames in: $framesFed, out: 0 — decoder sin producir video")
        try {
            val idx = c.dequeueInputBuffer(10_000)
            if (idx >= 0) {
                val buf = c.getInputBuffer(idx) ?: return
                buf.clear()
                buf.put(data)
                c.queueInputBuffer(idx, 0, data.size, pts, 0)
            } else {
                Thread.yield()
            }
        } catch (e: Exception) {
            onStatus("decoder error: ${e.message} — re-inicializando")
            reinit()
        }
    }

    /** Re-init programático — lo que Chrome no puede hacer (issue #16, stage 3). */
    fun reinit() {
        synchronized(this) {
            try {
                codec?.run { stop(); release() }
            } catch (_: Exception) {}
            configured = false
            onStatus("decoder re-inicializado — esperando config")
        }
    }

    fun release() {
        synchronized(this) {
            released.set(true)
            configured = false
            try {
                codec?.run { stop(); release() }
            } catch (_: Exception) {}
            codec = null
        }
    }
}
