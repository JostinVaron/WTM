package ai.ashna.wtmviewer

import android.media.MediaCodec
import android.media.MediaCodecList
import android.media.MediaFormat
import android.view.Surface

/**
 * Decodificador -> Surface directo (issue #16, stage 1).
 *
 * diag7/diag8: rotacion automatica por todos los decoders del mime tras
 * SILENCE_MS de consumo sin produccion (el log diag7 demostro que hw
 * Qualcomm, software c2 y OMX.google consumen y producen CERO frames).
 * csd via format csd-0 + via input CODEC_CONFIG + VPS/SPS/PPS in-band
 * (parseados del hvcC) antes del primer AU; pts 0-based (rebase del ts
 * del server). Si el stack esta sano (autotest diag8) y aun asi out=0,
 * los bytes del stream son el problema, no el codec.
 */
class VideoDecoder(
    private val surface: Surface,
    private val onStatus: (String) -> Unit
) {
    companion object {
        private const val SILENCE_MS = 6000L
    }

    private val lock = Any()
    private var codec: MediaCodec? = null
    @Volatile private var configured = false
    @Volatile private var released = false
    @Volatile private var activeName = ""
    @Volatile private var exhausted = false
    @Volatile private var ptsOffset = -1L

    private var framesFed = 0L
    private var framesOut = 0L
    private var drops = 0L
    private var realFed = 0L

    private val freeInputs = ArrayDeque<Int>()
    private val pendingFrames = ArrayDeque<Pair<ByteArray, Long>>()

    private var lastCodecName = ""
    private var lastMime = ""
    private var lastW = 0
    private var lastH = 0
    private var lastCsd: ByteArray = ByteArray(0)

    private val decoderNames = mutableListOf<String>()
    private var decoderIdx = 0
    private var configuredAt = 0L
    private var fedBoot = false
    private var bootAu: ByteArray? = null

    /** "avc1.*" -> H.264, "hvc1.*"/"hev1.*" -> HEVC; otro -> error visible. */
    private fun mimeOf(codecName: String): String? = when {
        codecName.startsWith("avc1") -> MediaFormat.MIMETYPE_VIDEO_AVC
        codecName.startsWith("hvc1") || codecName.startsWith("hev1") ->
            MediaFormat.MIMETYPE_VIDEO_HEVC
        else -> null
    }

    fun configure(codecName: String, width: Int, height: Int, csd: ByteArray) {
        val mime = mimeOf(codecName)
        if (mime == null) {
            onStatus("codec NO soportado por el viewer: $codecName")
            return
        }
        synchronized(lock) {
            decoderNames.clear()
            decoderIdx = 0
            exhausted = false
            ptsOffset = -1L
        }
        lastCodecName = codecName
        lastMime = mime
        lastW = width
        lastH = height
        lastCsd = csd
        openDecoder(null)
    }

    /** Abre (o reabre) un decoder. name=null -> el preferido del sistema. */
    private fun openDecoder(name: String?) {
        synchronized(lock) {
            teardownLocked()
            freeInputs.clear()
            pendingFrames.clear()
            framesFed = 0
            framesOut = 0
            drops = 0
            realFed = 0
            fedBoot = false
            bootAu = null
            released = false
            configuredAt = System.currentTimeMillis()

            val format = MediaFormat.createVideoFormat(lastMime, lastW, lastH).apply {
                setByteBuffer("csd-0", java.nio.ByteBuffer.wrap(lastCsd))
            }
            val c: MediaCodec
            try {
                c = if (name == null) MediaCodec.createDecoderByType(lastMime)
                     else MediaCodec.createByCodecName(name)
                c.setCallback(object : MediaCodec.Callback() {
                    override fun onInputBufferAvailable(m: MediaCodec, index: Int) {
                        if (released) return
                        synchronized(lock) {
                            freeInputs.addLast(index)
                            pumpLocked(m)
                        }
                    }

                    override fun onOutputBufferAvailable(m: MediaCodec, index: Int, info: MediaCodec.BufferInfo) {
                        if (released) return
                        try {
                            m.releaseOutputBuffer(index, true)
                            framesOut++
                            if (framesOut == 1L) {
                                onStatus("PRIMER FRAME RENDERIZADO al surface (decoder='$activeName', ${info.size}B)")
                            } else if (framesOut % 300 == 0L) {
                                onStatus("render: $framesOut frames al surface ('$activeName')")
                            }
                        } catch (e: Exception) {
                            onStatus("decoder error al renderizar: ${e.message}")
                        }
                    }

                    override fun onOutputFormatChanged(m: MediaCodec, format: MediaFormat) {
                        if (released) return
                        onStatus("decoder formato real ('$activeName'): $format")
                    }

                    override fun onError(m: MediaCodec, e: MediaCodec.CodecException) {
                        if (released) return
                        onStatus("decoder ONERROR ('$activeName') code=${e.errorCode} " +
                            "recoverable=${e.isRecoverable} transitorio=${e.isTransient}: ${e.diagnosticInfo}")
                    }
                })
                c.configure(format, surface, null, 0)
                c.start()
            } catch (e: Exception) {
                onStatus("decoder: FALLO abrir '${name ?: "default"}' ($lastMime): ${e.message}")
                return
            }
            codec = c
            configured = true
            activeName = c.codecInfo.name
            if (decoderNames.isEmpty()) decoderNames.add(activeName)
            if (decoderNames.size == 1) fillDecoderListLocked()
            onStatus("decoder activo: '$activeName' — $lastMime ${lastW}x${lastH} csd=${lastCsd.size}B")
        }
        startWatchdog()
    }

    /** Lista completa de decoders para el mime (para la auto-rotacion). */
    private fun fillDecoderListLocked() {
        try {
            val others = MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos
                .filter { info -> !info.isEncoder &&
                    info.supportedTypes.any { it.equals(lastMime, ignoreCase = true) } }
                .map { it.name }
                .filter { it != activeName }
            decoderNames.addAll(others)
            onStatus("decoders $lastMime: [" + decoderNames.joinToString(", ") + "]")
        } catch (e: Exception) {
            onStatus("lista de decoders fallo: ${e.message}")
        }
    }

    /**
     * Encola un frame con pts 0-based. Antes del primero alimenta el
     * boot del decoder: csd via input (CODEC_CONFIG) + params in-band.
     */
    fun feed(data: ByteArray, ts: Long) {
        if (released || !configured) return
        val c = codec ?: return
        if (ptsOffset < 0) ptsOffset = ts
        val feedPts = (ts - ptsOffset).coerceAtLeast(0)
        synchronized(lock) {
            if (!fedBoot && lastCsd.isNotEmpty()) {
                fedBoot = true
                pendingFrames.addLast(Pair(lastCsd, 0L))
                val nals = nalsFromConfig(lastCsd)
                if (nals.isNotEmpty()) {
                    val au = java.io.ByteArrayOutputStream()
                    for (n in nals) {
                        au.write((n.size shr 24) and 0xFF)
                        au.write((n.size shr 16) and 0xFF)
                        au.write((n.size shr 8) and 0xFF)
                        au.write(n.size and 0xFF)
                        au.write(n)
                    }
                    val auBytes = au.toByteArray()
                    bootAu = auBytes
                    pendingFrames.addLast(Pair(auBytes, 0L))
                }
            }
            pendingFrames.addLast(Pair(data, feedPts))
            if (pendingFrames.size > 90) {
                pendingFrames.removeFirst()
                drops++
                if (drops == 1L)
                    onStatus("decoder: cola de input saturada — descartando frames")
            }
            pumpLocked(c)
        }
    }

    /** Parsea VPS/SPS/PPS de un hvcC (HEVC) — [] si no es parseable. */
    private fun nalsFromConfig(csd: ByteArray): List<ByteArray> {
        val out = mutableListOf<ByteArray>()
        if (lastMime != MediaFormat.MIMETYPE_VIDEO_HEVC) return out
        try {
            if (csd.size < 23 || csd[0].toInt() != 1) return out
            val numArrays = csd[22].toInt() and 0xFF
            var i = 23
            for (a in 0 until numArrays) {
                if (i + 3 > csd.size) return out
                i++ // array_completeness | nal_type
                val numNalus = ((csd[i].toInt() and 0xFF) shl 8) or (csd[i + 1].toInt() and 0xFF)
                i += 2
                for (n in 0 until numNalus) {
                    if (i + 2 > csd.size) return out
                    val len = ((csd[i].toInt() and 0xFF) shl 8) or (csd[i + 1].toInt() and 0xFF)
                    i += 2
                    if (i + len > csd.size) return out
                    out.add(csd.copyOfRange(i, i + len))
                    i += len
                }
            }
        } catch (_: Exception) {}
        return out
    }

    /** Llena inputs libres; loguea csd/params/primer AU por identidad. */
    private fun pumpLocked(c: MediaCodec) {
        while (freeInputs.isNotEmpty() && pendingFrames.isNotEmpty()) {
            val idx = freeInputs.removeFirst()
            val (data, pts) = pendingFrames.removeFirst()
            try {
                val buf = c.getInputBuffer(idx)
                if (buf == null || data.size > buf.capacity()) {
                    onStatus("decoder: frame de ${data.size}B no cabe en input de ${buf?.capacity() ?: 0}B — descartado")
                    continue
                }
                buf.clear()
                buf.put(data)
                c.queueInputBuffer(idx, 0, data.size, pts,
                    if (data === lastCsd) MediaCodec.BUFFER_FLAG_CODEC_CONFIG else 0)
                framesFed++
                val ba = bootAu
                when {
                    data === lastCsd ->
                        onStatus("csd via input (CODEC_CONFIG): ${data.size}B enviado")
                    ba != null && data === ba ->
                        onStatus("params in-band (VPS/SPS/PPS): ${data.size}B enviados")
                    else -> {
                        realFed++
                        if (realFed == 1L)
                            onStatus("primer AU real: ${data.size}B pts=$pts us (decoder='$activeName')")
                    }
                }
            } catch (e: Exception) {
                onStatus("decoder error entrada: ${e.message}")
                return
            }
        }
    }

    /** Watchdog cada 2 s; auto-rota el decoder tras SILENCE_MS de silencio. */
    private fun startWatchdog() {
        Thread {
            while (configured && !released) {
                Thread.sleep(2000)
                if (!configured || released) break
                val silent = framesOut == 0L && framesFed > 0L &&
                    System.currentTimeMillis() - configuredAt > SILENCE_MS
                if (silent && !exhausted) {
                    val hasNext = synchronized(lock) { decoderIdx + 1 < decoderNames.size }
                    if (hasNext) {
                        rotateNext()
                        if (!configured) {
                            exhausted = true
                            onStatus("decoder: NINGUN decoder de la lista pudo abrirse")
                        }
                        break  // el watchdog del decoder nuevo toma el relevo
                    } else {
                        exhausted = true
                        onStatus("decoder: AGOTADOS ${decoderNames.size} decoders " +
                            "[${decoderNames.joinToString(", ")}] — input fluye, out=0 en todos")
                    }
                } else {
                    val p: Int
                    val f: Int
                    synchronized(lock) {
                        p = pendingFrames.size
                        f = freeInputs.size
                    }
                    onStatus("decoder vivo ('$activeName'): in=$framesFed out=$framesOut pend=$p libres=$f drops=$drops")
                }
            }
        }.apply { isDaemon = true }.start()
    }

    /** Rota al siguiente decoder; si no abre, sigue con el que sigue. */
    private fun rotateNext() {
        while (true) {
            val next: String? = synchronized(lock) {
                if (decoderIdx + 1 < decoderNames.size) decoderNames[decoderIdx + 1] else null
            }
            if (next == null) return
            decoderIdx++
            onStatus("decoder: '$activeName' traga input y suelta 0 frames — ROTANDO a '$next'")
            openDecoder(next)
            if (configured) return
        }
    }

    fun release() {
        synchronized(lock) {
            released = true
            configured = false
            teardownLocked()
        }
    }

    private fun teardownLocked() {
        try { codec?.stop() } catch (_: Exception) {}
        try { codec?.release() } catch (_: Exception) {}
        codec = null
        freeInputs.clear()
        pendingFrames.clear()
    }
}
