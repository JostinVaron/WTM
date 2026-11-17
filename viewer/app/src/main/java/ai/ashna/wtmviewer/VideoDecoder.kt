package ai.ashna.wtmviewer

import android.media.MediaCodec
import android.media.MediaFormat
import android.view.Surface
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Decodificador -> Surface directo (issue #16, stage 1).
 *
 * diag3: cada paso del MediaCodec queda visible en la consola: el mime que
 * se abre segun el codec del config, el formato REAL que reporta el decoder
 * (INFO_OUTPUT_FORMAT_CHANGED), el PRIMER frame renderizado, contadores
 * in/out y los errores con mensaje completo. "Decodificando" ya no se
 * declara: se MIDE (framesOut es lo unico que prueba video).
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
    private var starved = 0L

    /** "avc1.*" -> H.264, "hvc1.*"/"hev1.*" -> HEVC; otro -> error visible. */
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
                onStatus("codec NO soportado por el viewer: $codecName")
                return
            }
            onStatus("decoder: abrir $codecName como $mime ${width}x${height} csd=${csd.size}B")
            try {
                codec?.run { stop(); release() }
            } catch (_: Exception) {}
            val format = MediaFormat.createVideoFormat(mime, width, height).apply {
                setByteBuffer("csd-0", java.nio.ByteBuffer.wrap(csd))
            }
            val c = try {
                MediaCodec.createDecoderByType(mime).also {
                    it.configure(format, surface, null, 0)
                    it.start()
                }
            } catch (e: Exception) {
                onStatus("decoder: FALLO abrir ($mime): ${e.message}")
                return
            }
            codec = c
            configured = true
            released.set(false)
            framesFed = 0
            framesOut = 0
            starved = 0
            Thread {
                while (configured && !released.get()) {
                    drainOutputs()
                    Thread.sleep(2)
                }
            }.start()
        }
    }

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
                        if (framesOut == 1L) onStatus("PRIMER FRAME RENDERIZADO al surface")
                        else if (framesOut % 600 == 0L) onStatus("render: $framesOut frames al surface")
                    }
                    idx == MediaCodec.INFO_TRY_AGAIN_LATER -> return
                    idx == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED ->
                        onStatus("decoder formato real: " + c.outputFormat.toString())
                    else -> {}
                }
            }
        } catch (e: Exception) {
            onStatus("decoder error salida: ${e.message} — re-init")
            reinit()
        }
    }

    fun feed(data: ByteArray, pts: Long) {
        if (released.get() || !configured) return
        val c = codec ?: return
        framesFed++
        if (framesFed == 1L) onStatus("primer buffer al decoder: ${data.size}B pts=$pts")
        if (framesFed % 300 == 0L)
            onStatus("decoder: in=$framesFed out=$framesOut sin-buffer=$starved")
        try {
            val idx = c.dequeueInputBuffer(10_000)
            if (idx >= 0) {
                val buf = c.getInputBuffer(idx) ?: return
                buf.clear()
                buf.put(data)
                c.queueInputBuffer(idx, 0, data.size, pts, 0)
            } else {
                starved++
            }
        } catch (e: Exception) {
            onStatus("decoder error entrada: ${e.message} — re-init")
            reinit()
        }
    }

    /** Re-init programatico — lo que Chrome no puede hacer (issue #16, stage 3). */
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
