package ai.ashna.wtmviewer

import android.graphics.SurfaceTexture
import android.media.MediaCodec
import android.media.MediaCodecInfo
import android.media.MediaCodecList
import android.media.MediaFormat
import android.os.Build
import android.view.Surface

/**
 * Autotest OFFLINE del stack de codecs de la tablet (diag8).
 *
 * El log diag7 demostro que los TRES decoders (hw qcom, software c2,
 * OMX.google) consumen AUs y producen CERO frames sin error. Cuando hasta
 * el software falla, quedan dos mundos: el stack de codecs del
 * dispositivo esta roto, o los bytes del stream real son rechazados por
 * parsers estrictos (la prueba de "Chrome lo decodifica" ocurrio en el
 * S25, OTRO dispositivo — esta tablet nunca decodifico ni un frame en
 * ningun log). Este test separa ambos mundos SIN relay y SIN conectar:
 * la app genera su propio frame con el ENCODER local (color solido via
 * Surface), lo decodifica de vuelta, en los dos codecs (hevc/avc), en
 * dos tamanos (640x480 basico y 2000x1200 igual al stream real), y en
 * los dos modos de salida (bytebuffer y surface con SurfaceTexture
 * dummy — el modo exacto del viewer).
 *
 * Matriz de lectura:
 *  - hevc 640x480 = 0 frames         -> stack ROTO -> remedio: relay en H.264
 *  - hevc 640 OK, 2000x1200 = 0      -> fallo de resolucion/nivel en la tablet
 *  - todo OK                         -> stack SANO -> los BYTES del stream real
 *                                       son el problema (siguiente: dump+ffprobe)
 *  - bytebuffer OK pero surface = 0  -> el render a surface es lo roto
 */
object CodecSelfTest {

    /** Info del dispositivo + capacidades declaradas + loopback encode/decode. */
    fun runAll(log: (String) -> Unit) {
        log("dispositivo: ${Build.MANUFACTURER} ${Build.MODEL} — Android ${Build.VERSION.RELEASE} (API ${Build.VERSION.SDK_INT})")
        report("video/hevc", log)
        report("video/avc", log)
        for (mime in listOf(MediaFormat.MIMETYPE_VIDEO_HEVC, MediaFormat.MIMETYPE_VIDEO_AVC)) {
            val label = if (mime == MediaFormat.MIMETYPE_VIDEO_HEVC) "hevc" else "avc"
            for ((w, h) in listOf(640 to 480, 2000 to 1200)) {
                val enc = encodeTestFrame(mime, w, h, log) ?: continue
                val bb = decodeTest(mime, w, h, enc, useSurface = false, log)
                val sf = decodeTest(mime, w, h, enc, useSurface = true, log)
                log("RESULTADO $label ${w}x${h}: bytebuffer=${if (bb < 0) "ERROR" else "$bb frames"} " +
                    "surface=${if (sf < 0) "ERROR" else "$sf frames"} (csd=${enc.first.size}B au=${enc.second.size}B)")
            }
        }
        log("selftest completo")
    }

    /** Capacidades DECLARADAS de cada decoder para ese mime. */
    private fun report(mime: String, log: (String) -> Unit) {
        try {
            val infos = MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos
            for (info in infos) {
                if (info.isEncoder) continue
                if (info.supportedTypes.none { it.equals(mime, ignoreCase = true) }) continue
                try {
                    val vc = info.getCapabilitiesForType(mime).videoCapabilities
                    log("caps '${info.name}' $mime: 640x480=${vc.isSizeSupported(640, 480)} " +
                        "2000x1200=${vc.isSizeSupported(2000, 1200)}")
                } catch (e: Exception) {
                    log("caps '${info.name}' $mime: fallo — ${e.message}")
                }
            }
        } catch (e: Exception) {
            log("reporte de decoders fallo: ${e.message}")
        }
    }

    /** Encode local de 2 frames solidos -> (csd, primer AU). Null si falla. */
    private fun encodeTestFrame(mime: String, w: Int, h: Int,
                                log: (String) -> Unit): Pair<ByteArray, ByteArray>? {
        var enc: MediaCodec? = null
        var input: Surface? = null
        try {
            val fmt = MediaFormat.createVideoFormat(mime, w, h).apply {
                setInteger(MediaFormat.KEY_COLOR_FORMAT,
                    MediaCodecInfo.CodecCapabilities.COLOR_FormatSurface)
                setInteger(MediaFormat.KEY_BIT_RATE, 2_000_000)
                setInteger(MediaFormat.KEY_FRAME_RATE, 30)
                setInteger(MediaFormat.KEY_I_FRAME_INTERVAL, 1)
            }
            val c = MediaCodec.createEncoderByType(mime)
            enc = c
            c.configure(fmt, null, null, MediaCodec.CONFIGURE_FLAG_ENCODE)
            val inp = c.createInputSurface()
            input = inp
            c.start()
            repeat(2) { i ->
                val canvas = inp.lockCanvas(null) ?: throw IllegalStateException("lockCanvas null")
                canvas.drawColor(if (i == 0) -0x33ff88 else -0xff6600)
                inp.unlockCanvasAndPost(canvas)
                Thread.sleep(60)
            }
            c.signalEndOfInputStream()

            var csd: ByteArray? = null
            var au: ByteArray? = null
            val info = MediaCodec.BufferInfo()
            val deadline = System.currentTimeMillis() + 4000
            while (System.currentTimeMillis() < deadline && (csd == null || au == null)) {
                val idx = c.dequeueOutputBuffer(info, 10_000)
                if (idx >= 0) {
                    val buf = c.getOutputBuffer(idx)
                    if (buf != null && info.size > 0) {
                        val data = ByteArray(info.size)
                        buf.position(info.offset)
                        buf.get(data)
                        if (info.flags and MediaCodec.BUFFER_FLAG_CODEC_CONFIG != 0) {
                            if (csd == null) csd = data
                        } else if (au == null) {
                            au = data
                        }
                    }
                    c.releaseOutputBuffer(idx, false)
                }
            }
            if (csd == null || au == null) {
                log("encoder $mime ${w}x${h}: no produjo csd/au " +
                    "(csd=${csd?.size ?: -1}B au=${au?.size ?: -1}B)")
                return null
            }
            return Pair(csd!!, au!!)
        } catch (e: Exception) {
            log("encoder $mime ${w}x${h}: FALLO — ${e.message}")
            return null
        } finally {
            try { input?.release() } catch (_: Exception) {}
            try { enc?.stop() } catch (_: Exception) {}
            try { enc?.release() } catch (_: Exception) {}
        }
    }

    /**
     * Decodifica (csd, au) en modo bytebuffer o en modo surface
     * (SurfaceTexture dummy — el modo exacto del viewer). Devuelve frames
     * producidos, -1 si hubo error. Un AU de keyframe debe producir >= 1.
     */
    private fun decodeTest(mime: String, w: Int, h: Int, enc: Pair<ByteArray, ByteArray>,
                           useSurface: Boolean, log: (String) -> Unit): Int {
        var dec: MediaCodec? = null
        var surf: Surface? = null
        var tex: SurfaceTexture? = null
        val mode = if (useSurface) "surface" else "bytebuffer"
        return try {
            if (useSurface) {
                tex = SurfaceTexture(0)
                tex.setDefaultBufferSize(w, h)
                surf = Surface(tex)
            }
            val fmt = MediaFormat.createVideoFormat(mime, w, h).apply {
                setByteBuffer("csd-0", java.nio.ByteBuffer.wrap(enc.first))
            }
            val d = MediaCodec.createDecoderByType(mime)
            dec = d
            d.configure(fmt, surf, null, 0)
            d.start()

            val info = MediaCodec.BufferInfo()
            var fed = false
            var frames = 0
            val deadline = System.currentTimeMillis() + 4000
            while (System.currentTimeMillis() < deadline) {
                if (!fed) {
                    val inIdx = d.dequeueInputBuffer(50_000)
                    if (inIdx >= 0) {
                        val ib = d.getInputBuffer(inIdx) ?: continue
                        ib.clear()
                        ib.put(enc.second)
                        d.queueInputBuffer(inIdx, 0, enc.second.size, 0, 0)
                        fed = true
                    }
                    continue
                }
                val oIdx = d.dequeueOutputBuffer(info, 50_000)
                if (oIdx >= 0) {
                    try {
                        d.releaseOutputBuffer(oIdx, useSurface)
                    } catch (e: Exception) {
                        log("decoder $mime $mode: frame producido pero render fallo — ${e.message}")
                    }
                    frames++
                    break
                }
            }
            if (frames == 0)
                log("decoder $mime ${w}x${h} $mode: 0 frames — SILENCIO")
            frames
        } catch (e: Exception) {
            log("decoder $mime ${w}x${h} $mode: FALLO — ${e.message}")
            -1
        } finally {
            try { dec?.stop() } catch (_: Exception) {}
            try { dec?.release() } catch (_: Exception) {}
            try { surf?.release() } catch (_: Exception) {}
            try { tex?.release() } catch (_: Exception) {}
        }
    }
}
