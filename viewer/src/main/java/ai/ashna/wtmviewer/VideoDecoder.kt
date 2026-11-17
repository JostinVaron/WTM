package ai.ashna.wtmviewer

import android.media.MediaCodec
import android.media.MediaFormat
import android.view.Surface
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Decodificador HEVC -> Surface directo (issue #16, stage 1).
 *
 * El relay envía frames ya en formato AVCC/hvcC (4-byte NAL length prefix)
 * — el mismo formato que MediaCodec espera — y el hvcC completo como csd.
 * No hay conversión Annex-B necesaria: la "pregunta empírica" del issue #16
 * queda resuelta por diseño del relay (ver hevc.py build_hvcc).
 *
 * Stage 3 (pendiente): re-init automático del decoder ante corrupción
 * sostenida — reemplaza el workaround del reload del navegador.
 */
class VideoDecoder(
    private val surface: Surface,
    private val onStatus: (String) -> Unit
) {
    private var codec: MediaCodec? = null
    private var configured = false
    private var released = AtomicBoolean(false)
    private val pending = ArrayDeque<Pair<ByteArray, Long>>()
    private var framesFed = 0L
    private var lastStatusReport = 0L

    fun configure(codecName: String, width: Int, height: Int, csd: ByteArray) {
        synchronized(this) {
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
            pending.clear()
            onStatus("decoder configurado: $width x $height")
        }
    }

    /** data: frame AVCC (length-prefixed NALs). pts: 0 aún (stage 2 agregará timestamps). */
    fun feed(data: ByteArray, pts: Long) {
        if (released.get() || !configured) return
        val c = codec ?: return

        // reporte de progreso ligero cada 300 frames
        framesFed++
        if (framesFed % 300 == 0L) {
            onStatus("frames: $framesFed")
        }

        try {
            val idx = c.dequeueInputBuffer(10_000) // 10 ms
            if (idx >= 0) {
                val buf = c.getInputBuffer(idx) ?: return
                buf.clear()
                buf.put(data)
                c.queueInputBuffer(idx, 0, data.size, pts, 0)
            } else {
                // decoder ocupado: pausa mínima y reintento único en stage 2 con cola
                Thread.yield()
            }
        } catch (e: Exception) {
            onStatus("decoder error: ${e.message} — re-inicializando")
            reinit()
        }
    }

    /** Re-init programático — lo que Chrome no puede hacer y motiva el issue #16. */
    fun reinit() {
        synchronized(this) {
            try {
                codec?.run { stop(); release() }
            } catch (_: Exception) {}
            configured = false
            onStatus("decoder re-inicializado — esperando próximo config")
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
