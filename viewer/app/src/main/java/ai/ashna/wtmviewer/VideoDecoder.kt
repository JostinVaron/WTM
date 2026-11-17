package ai.ashna.wtmviewer

import android.media.MediaCodec
import android.media.MediaCodecList
import android.media.MediaFormat
import android.view.Surface
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer

/**
 * Decodificador -> Surface directo (issue #16, stage 1).
 *
 * diag10: LA PISTA DEFINITIVA. Con "?hw=prefer-hardware" el cliente web
 * muestra VIDEO en la misma tablet — o sea Chrome decodifica este stream
 * via hardware. Mi app usaba el mismo MediaCodec y out=0. La diferencia
 * estructural que queda: Chrome alimenta el decoder en formato ANNEX-B
 * (start codes 00 00 00 01, csd-0 como VPS/SPS/PPS con start codes),
 * mientras mi app alimentaba length-prefixed + hvcC en csd-0. Es tambien
 * exactamente el formato que usa scrcpy (probado en Samsunges de todo
 * pelaje). diag10 convierte TODO a Annex-B antes del decoder:
 *
 *  - csd-0 = VPS+SPS+PPS del hvcC re-empaquetados con start codes
 *  - cada AU: prefijos de 4 bytes -> 00 00 00 01 (NAL por NAL)
 *  - keyframes llevan VPS/SPS/PPS in-band al inicio (estilo scrcpy)
 *  - el IDR capturado se guarda en Annex-B para re-inyeccion
 *
 * Se conserva todo diag8/9: rotacion valida (boot completo + IDR
 * re-inyectado por decoder), "t":"idr" fresco en cada rotacion, log de
 * tipo NAL, pts local monotónico, saneador (defensa barata aunque los
 * AUs llegaban limpios).
 */
class VideoDecoder(
    private val surface: Surface,
    private val onStatus: (String) -> Unit,
    private val onNeedKey: () -> Unit
) {
    companion object {
        private const val SILENCE_TICKS = 3          // 3 ticks de 2s = 6s de out=0
        private val KEY_NALS = setOf(19, 20, 21)      // IDR_W_RADL, IDR_N_LP, CRA_NUT
        private val START_CODE = byteArrayOf(0, 0, 0, 1)
    }

    private class Pending(val data: ByteArray, val pts: Long, val isCsd: Boolean)

    private val lock = Any()
    private var codec: MediaCodec? = null
    @Volatile private var configured = false
    @Volatile private var released = false
    @Volatile private var exhausted = false
    @Volatile private var activeName = ""
    private var session = 0L

    private var framesFed = 0L
    private var framesOut = 0L
    private var drops = 0L
    private var realFed = 0L
    private var frameCounter = 0L
    private var loggedAus = 0
    private var configuredAt = 0L

    private val freeInputs = ArrayDeque<Int>()
    private val pending = ArrayDeque<Pending>()

    private var lastCodecName = ""
    private var lastMime = ""
    private var lastW = 0
    private var lastH = 0
    private var lastCsd: ByteArray = ByteArray(0)   // hvcC crudo del server
    private var annexbCsd: ByteArray = ByteArray(0) // VPS/SPS/PPS con start codes
    private var keyAu: ByteArray? = null            // IDR en Annex-B

    private val decoderNames = mutableListOf<String>()
    private var decoderIdx = 0

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
            keyAu = null
            loggedAus = 0
            frameCounter = 0
        }
        lastCodecName = codecName
        lastMime = mime
        lastW = width
        lastH = height
        lastCsd = csd
        annexbCsd = buildAnnexbCsd(csd)
        if (annexbCsd.isEmpty()) {
            onStatus("csd: no se pudieron extraer los NALs (hvcC/avcC raro) — abortando")
            return
        }
        onStatus("csd Annex-B: ${annexbCsd.size}B (VPS/SPS/PPS con start codes, estilo scrcpy)")
        openDecoder(null)
        onStatus("pidiendo IDR fresco al server ({\"t\":\"idr\"} — como el cliente web)")
        onNeedKey()
    }

    /**
     * csd Annex-B para el formato: NALs del hvcC/avcC unidos con start
     * codes 00 00 00 01 — asi configuran csd-0 scrcpy/VLC y Chrome-Android
     * para AVC (SPS+PPS) y HEVC (VPS+SPS+PPS).
     */
    private fun buildAnnexbCsd(csd: ByteArray): ByteArray {
        val out = ByteArrayOutputStream()
        if (lastMime == MediaFormat.MIMETYPE_VIDEO_HEVC) {
            for (nal in nalsFromHvcC(csd)) {
                out.write(START_CODE); out.write(nal)
            }
        } else {
            // avcC: sps en csd-0, pps en csd-1 — aqui unimos ambos en csd-0
            val (sps, pps) = nalsFromAvcC(csd) ?: return ByteArray(0)
            out.write(START_CODE); out.write(sps)
            out.write(START_CODE); out.write(pps)
        }
        return out.toByteArray()
    }

    /** Abre (o reabre) un decoder. name=null -> el preferido del sistema. */
    private fun openDecoder(name: String?) {
        var mySession = 0L
        synchronized(lock) {
            teardownLocked()
            freeInputs.clear()
            pending.clear()
            framesFed = 0
            framesOut = 0
            realFed = 0
            drops = 0
            released = false
            session += 1
            mySession = session
            configuredAt = System.currentTimeMillis()

            val format = MediaFormat.createVideoFormat(lastMime, lastW, lastH).apply {
                // csd-0 ANNEX-B (no el hvcC crudo): el formato que OMX/c2
                // Samsung mastican de buena gana (scrcpy/VLC/Chrome lo usan)
                setByteBuffer("csd-0", ByteBuffer.wrap(annexbCsd))
                setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 4 * 1024 * 1024)
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
                                onStatus("PRIMER FRAME RENDERIZADO al surface " +
                                    "(decoder='$activeName', ${info.size}B)")
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
            activeName = try { c.codecInfo.name } catch (_: Exception) { name ?: "default" }
            if (decoderNames.isEmpty()) {
                decoderNames.add(activeName)
                fillDecoderListLocked()
            }
            onStatus("decoder activo: '$activeName' — $lastMime ${lastW}x${lastH} modo ANNEX-B")

            // boot del decoder: csd via input + IDR re-inyectado (si hay)
            pending.addLast(Pending(annexbCsd, 0L, true))
            val k = keyAu
            if (k != null) {
                pending.addLast(Pending(k, 0L, false))
                onStatus("re-inyectando IDR capturado (${k.size}B, Annex-B) al decoder nuevo")
            } else {
                onStatus("sin IDR capturado aun — esperando el keyframe del server")
            }
        }
        startWatchdog(mySession)
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
     * Encola un frame convertido a ANNEX-B con pts local. Los keyframes
     * (NAL tipo 19/20/21) llevan VPS/SPS/PPS in-band al inicio — estilo
     * scrcpy, que funciona en Samsunges de todo pelaje. El IDR se guarda
     * en Annex-B para re-inyeccion en rotaciones.
     */
    fun feed(data: ByteArray, ts: Long) {
        if (released || !configured) return
        val c = codec ?: return

        val nal = firstNalType(data)
        val isKey = nal != null && KEY_NALS.contains(nal)
        val ab = toAnnexB(data) ?: return
        val payload = if (isKey) {
            val b = ByteArrayOutputStream()
            b.write(annexbCsd)          // params in-band antes del keyframe
            b.write(ab)
            b.toByteArray()
        } else ab

        val pts = synchronized(lock) { ++frameCounter * 33333L }
        if (loggedAus < 3 && nal != null) {
            loggedAus++
            val kind = if (isKey) "KEY" else "delta"
            onStatus("AU #$loggedAus: ${data.size}B -> Annex-B ${payload.size}B " +
                "primer NAL=tipo$nal ($kind${if (isKey) "+params in-band" else ""})")
        }
        if (isKey) keyAu = payload

        synchronized(lock) {
            pending.addLast(Pending(payload, pts, false))
            if (pending.size > 90) {
                pending.removeFirst()
                drops++
                if (drops == 1L)
                    onStatus("decoder: cola de input saturada — descartando frames")
            }
            pumpLocked(c)
        }
    }

    /** Tipo NAL del primer NAL de un AU length-prefixed; null si no parsea. */
    private fun firstNalType(au: ByteArray): Int? {
        if (au.size < 5) return null
        val len = ((au[0].toInt() and 0xFF) shl 24) or ((au[1].toInt() and 0xFF) shl 16) or
            ((au[2].toInt() and 0xFF) shl 8) or (au[3].toInt() and 0xFF)
        if (len < 2 || len + 4 > au.size) return null
        return (au[4].toInt() and 0xFF) shr 1 and 0x3F
    }

    /** AU length-prefixed -> Annex-B: cada NAL con start code 00 00 00 01. */
    private fun toAnnexB(au: ByteArray): ByteArray? {
        var i = 0
        var nals = 0
        val out = ByteArrayOutputStream()
        while (i + 4 <= au.size) {
            val len = ((au[i].toInt() and 0xFF) shl 24) or ((au[i + 1].toInt() and 0xFF) shl 16) or
                ((au[i + 2].toInt() and 0xFF) shl 8) or (au[i + 3].toInt() and 0xFF)
            if (len <= 0 || i + 4 + len > au.size) break
            out.write(START_CODE)
            out.write(au, i + 4, len)
            i += 4 + len
            nals++
        }
        if (nals == 0) return null
        return out.toByteArray()
    }

    /** Parsea los NALs de un hvcC (HEVC) — [] si no es parseable. */
    private fun nalsFromHvcC(csd: ByteArray): List<ByteArray> {
        val out = mutableListOf<ByteArray>()
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

    /** SPS y PPS de un avcC (H.264) — null si no es parseable. */
    private fun nalsFromAvcC(csd: ByteArray): Pair<ByteArray, ByteArray>? {
        try {
            if (csd.size < 7 || csd[0].toInt() != 1) return null
            var i = 6
            val spsLen = ((csd[i].toInt() and 0xFF) shl 8) or (csd[i + 1].toInt() and 0xFF)
            i += 2
            if (i + spsLen > csd.size) return null
            val sps = csd.copyOfRange(i, i + spsLen)
            i += spsLen
            val ppsCount = csd[i].toInt() and 0xFF
            if (ppsCount < 1 || i + 3 > csd.size) return null
            i += 1
            val ppsLen = ((csd[i].toInt() and 0xFF) shl 8) or (csd[i + 1].toInt() and 0xFF)
            i += 2
            if (i + ppsLen > csd.size) return null
            val pps = csd.copyOfRange(i, i + ppsLen)
            return Pair(sps, pps)
        } catch (_: Exception) { return null }
    }

    /** Llena inputs libres; items isCsd van con BUFFER_FLAG_CODEC_CONFIG. */
    private fun pumpLocked(c: MediaCodec) {
        while (freeInputs.isNotEmpty() && pending.isNotEmpty()) {
            val idx = freeInputs.removeFirst()
            val p = pending.removeFirst()
            try {
                val buf = c.getInputBuffer(idx)
                if (buf == null || p.data.size > buf.capacity()) {
                    onStatus("decoder: buffer de ${p.data.size}B no cabe (${buf?.capacity() ?: 0}B) — descartado")
                    continue
                }
                buf.clear()
                buf.put(p.data)
                val flags = if (p.isCsd) MediaCodec.BUFFER_FLAG_CODEC_CONFIG else 0
                c.queueInputBuffer(idx, 0, p.data.size, p.pts, flags)
                framesFed++
                if (p.isCsd) {
                    onStatus("csd Annex-B via input (CODEC_CONFIG): ${p.data.size}B")
                } else {
                    realFed++
                    if (realFed == 1L)
                        onStatus("primer AU Annex-B: ${p.data.size}B pts=${p.pts} us (decoder='$activeName')")
                }
            } catch (e: Exception) {
                onStatus("decoder error entrada: ${e.message}")
                return
            }
        }
    }

    /** Watchdog cada 2 s; rota el decoder tras 6 s de out=0 con input fluyendo. */
    private fun startWatchdog(mySession: Long) {
        Thread {
            var tick = 0
            while (configured && !released && session == mySession) {
                Thread.sleep(2000)
                if (!configured || released || session != mySession) break
                tick++
                if (framesOut == 0L && framesFed > 0L && !exhausted && tick >= SILENCE_TICKS) {
                    rotateNext()
                    break
                }
                val p: Int
                val f: Int
                synchronized(lock) {
                    p = pending.size
                    f = freeInputs.size
                }
                onStatus("decoder vivo ('$activeName'): in=$framesFed out=$framesOut " +
                    "pend=$p libres=$f drops=$drops")
            }
        }.apply { isDaemon = true }.start()
    }

    /**
     * Rota al siguiente decoder. Cada rotacion pide un IDR FRESCO al server
     * (recovery del cliente web) y re-inyecta el boot completo al decoder
     * nuevo — asi la prueba de cada decoder es valida.
     */
    private fun rotateNext() {
        while (true) {
            val next: String? = synchronized(lock) {
                if (decoderIdx + 1 < decoderNames.size) {
                    decoderIdx++
                    decoderNames[decoderIdx]
                } else null
            }
            if (next == null) {
                exhausted = true
                onStatus("decoder: AGOTADOS ${decoderNames.size} decoders en modo Annex-B " +
                    "[${decoderNames.joinToString(", ")}] — todos con csd+IDR+params in-band")
                return
            }
            onStatus("decoder: '$activeName' consume sin producir — ROTANDO a '$next' " +
                "+ IDR fresco del server")
            onNeedKey()
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
        pending.clear()
    }
}
