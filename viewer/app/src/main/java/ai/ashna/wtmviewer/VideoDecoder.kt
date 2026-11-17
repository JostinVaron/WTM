package ai.ashna.wtmviewer

import android.media.MediaCodec
import android.media.MediaFormat
import android.view.Surface
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Decodificador HEVC -> Surface directo (issue #16, stage 1).
 * El relay envía frames AVCC/hvcC (4-byte length prefix) + hvcC como csd:
 * el formato que MediaCodec espera — sin conversión Annex-B.
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
    private var lastW = 0
    private var lastH = 0
    private var lastCsd: ByteArray? = null

    fun configure(codecName: String, width: Int, height: Int, csd: ByteArray) {
        synchronized(this) {
            lastW = width; lastH = height; lastCsd = csd
            try {
                codec?.run { stop(); release() }
            } catch (_: Exception) {}
            val format = MediaFormat.createVideoFormat(MediaFormat.MIMETYPE_VIDEO_HEVC, width, height).apply {
                setByteBuffer("csd-0", java.nio.ByteBuffer.wrap(csd))
            }
            val c = MediaCodec.createDecoderByType(MediaFormat.MIMETYPE_VIDEO_HEVC)
            c.configure(format, surface, null, 0)
            c.start()
            codec = c
            configured = true
            released.set(false)
            onStatus("decoder configurado: ${width}x${height}")
        }
    }

    fun feed(data: ByteArray, pts: Long) {
        if (released.get() || !configured) return
        val c = codec ?: return
        framesFed++
        if (framesFed % 300 == 0L) onStatus("frames: $framesFed")
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
