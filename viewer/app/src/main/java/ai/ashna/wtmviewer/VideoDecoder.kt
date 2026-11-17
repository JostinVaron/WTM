package ai.ashna.wtmviewer

import android.media.MediaCodec
import android.media.MediaCodecList
import android.media.MediaFormat
import android.view.Surface
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer

/**
 * Decodificador HEVC/H264 -> Surface directo (issue #16).
 *
 * v1.1-perf3 — COLA ACOTADA CON DESCARTE DELTA (latest-wins real):
 * el log perf2 de campo (230 s, 4K60, 35 Mbps) demostro que la cola de
 * entrada crecio hasta 75 frames (~1,2 s de video viajando viejo) sin
 * disparar UN solo render-drop: la politica perf1 comparaba contra pts
 * SINTETICO local, que siempre avanza en orden — el atraso que buscaba
 * nunca existia por construccion. Bug de diseno, probado por datos.
 *
 * perf3 acota la cola de entrada a QUEUE_MAX (8 frames ~ 133 ms) y al
 * superarla descarta el delta MAS VIEJO (los keyframes y el csd jamas
 * se descartan — sin referencia el stream se rompe). Al descartar se
 * pide keyframe fresco al server ({"t":"idr"}, el mismo recovery que
 * el cliente web) para que el decoder re-sincronice en <1 s en vez de
 * reproducir 1,2 s de atasco frame a frame. La ventana de corrupcion
 * hasta que llega el IDR es el costo aceptado — es el tradeoff que hacen
 * todos los pipeline de baja latencia (scrcpy incluido).
 */
class VideoDecoder(
    private val surface: Surface,
    private val onStatus: (String) -> Unit,
    private val onFirstFrame: () -> Unit,
    private val onNeedKey: () -> Unit
) {
    companion object {
        private const val SILENCE_TICKS = 3
        private const val KEY_NALS = setOf(19, 20, 21)
        private val START_CODE = byteArrayOf(0, 0, 0, 1)
        private const val DROP_LAG_US = 120_000L   // seguridad inerte (pts sintetico); la cota real es QUEUE_MAX
        private const val QUEUE_MAX = 8            // ~133 ms a 60 fps: cota dura de video en vuelo
        private const val KEY_REQ_PERIOD = 20L     // pedir IDR cada N descartes (anti-spam)
    }

    class Stats(
        val decoder: String,
        val rendered: Long,
        val renderDrops: Long,
        val queueDrops: Long,
        val queueDepth: Int
    )

    private class Pending(val data: ByteArray, val pts: Long, val isCsd: Boolean, val isKey: Boolean)

    private val lock = Any()
    private var codec: MediaCodec? = null
    @Volatile private var configured = false
    @Volatile private var released = false
    @Volatile private var exhausted = false
    @Volatile private var activeName = ""
    private var session = 0L

    private var framesFed = 0L
    private var framesOut = 0L
    private var renderDrops = 0L
    private var queueDrops = 0L
    private var frameCounter = 0L
    private var maxSeenPts = 0L
    private var lastErrorLog = 0L

    private val freeInputs = ArrayDeque<Int>()
    private val pending = ArrayDeque<Pending>()

    private var lastMime = ""
    private var lastW = 0
    private var lastH = 0
    private var annexbCsd: ByteArray = ByteArray(0)
    private var keyAu: ByteArray? = null

    private val decoderNames = mutableListOf<String>()
    private var decoderIdx = 0

    /** Snapshot para el HUD (thread-safe). */
    fun stats(): Stats = synchronized(lock) {
        Stats(activeName, framesOut, renderDrops, queueDrops, pending.size)
    }

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
            onStatus("error: codec no soportado: $codecName")
            return
        }
        synchronized(lock) {
            decoderNames.clear()
            decoderIdx = 0
            exhausted = false
            keyAu = null
            frameCounter = 0
            maxSeenPts = 0
            queueDrops = 0
        }
        lastMime = mime
        lastW = width
        lastH = height
        annexbCsd = buildAnnexbCsd(mime, csd)
        if (annexbCsd.isEmpty()) {
            onStatus("error: csd no parseable (codec $codecName)")
            return
        }
        openDecoder(null)
        onNeedKey()
    }

    /** csd Annex-B: NALs del hvcC/avcC unidos con start codes. */
    private fun buildAnnexbCsd(mime: String, csd: ByteArray): ByteArray {
        val out = ByteArrayOutputStream()
        if (mime == MediaFormat.MIMETYPE_VIDEO_HEVC) {
            for (nal in nalsFromHvcC(csd)) {
                out.write(START_CODE); out.write(nal)
            }
        } else {
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
            renderDrops = 0
            queueDrops = 0
            maxSeenPts = 0
            released = false
            session += 1
            mySession = session

            val format = MediaFormat.createVideoFormat(lastMime, lastW, lastH).apply {
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
                            // RENDER-DROP de seguridad (inerte con pts sintetico;
                            // la cota real de latencia la pone QUEUE_MAX).
                            val lag = maxSeenPts - info.presentationTimeUs
                            if (framesOut > 3 && lag > DROP_LAG_US) {
                                m.releaseOutputBuffer(index, false)
                                renderDrops++
                                return
                            }
                            if (info.presentationTimeUs > maxSeenPts) maxSeenPts = info.presentationTimeUs
                            m.releaseOutputBuffer(index, true)
                            framesOut++
                            if (framesOut == 1L) onFirstFrame()
                        } catch (e: Exception) {
                            logRateLimited("error al renderizar frame: ${e.message}")
                        }
                    }

                    override fun onOutputFormatChanged(m: MediaCodec, format: MediaFormat) {}

                    override fun onError(m: MediaCodec, e: MediaCodec.CodecException) {
                        if (released) return
                        logRateLimited("decoder '$activeName' error code=${e.errorCode}: ${e.diagnosticInfo}")
                    }
                })
                c.configure(format, surface, null, 0)
                c.start()
            } catch (e: Exception) {
                onStatus("error: fallo abrir decoder (${e.message})")
                return
            }
            codec = c
            configured = true
            activeName = try { c.codecInfo.name } catch (_: Exception) { name ?: "default" }
            if (decoderNames.isEmpty()) {
                decoderNames.add(activeName)
                fillDecoderListLocked()
            }

            pending.addLast(Pending(annexbCsd, 0L, true, true))
            keyAu?.let { pending.addLast(Pending(it, 0L, false, true)) }
        }
        startWatchdog(mySession)
    }

    /** Lista de decoders del mime, para el fallback automatico. */
    private fun fillDecoderListLocked() {
        try {
            val others = MediaCodecList(MediaCodecList.REGULAR_CODECS).codecInfos
                .filter { info -> !info.isEncoder &&
                    info.supportedTypes.any { it.equals(lastMime, ignoreCase = true) } }
                .map { it.name }
                .filter { it != activeName }
            decoderNames.addAll(others)
        } catch (_: Exception) {}
    }

    /**
     * Encola un frame convertido a ANNEX-B con pts local. Los keyframes
     * llevan VPS/SPS/PPS in-band (estilo scrcpy) y se guardan para
     * re-inyeccion tras una rotacion de decoder.
     *
     * Cota de latencia (perf3): si la cola supera QUEUE_MAX se descarta
     * el delta MAS VIEJO y se pide keyframe fresco — nunca se toca un
     * keyframe ni el csd.
     */
    fun feed(data: ByteArray, ts: Long) {
        if (released || !configured) return
        val c = codec ?: return

        val nal = firstNalType(data)
        val isKey = nal != null && KEY_NALS.contains(nal)
        val ab = toAnnexB(data) ?: return
        val payload = if (isKey) {
            val b = ByteArrayOutputStream()
            b.write(annexbCsd)
            b.write(ab)
            b.toByteArray()
        } else ab

        val pts = synchronized(lock) { ++frameCounter * 33333L }
        if (isKey) keyAu = payload

        synchronized(lock) {
            pending.addLast(Pending(payload, pts, false, isKey))
            while (pending.size > QUEUE_MAX) {
                var removed = false
                var i = 0
                while (i < pending.size) {
                    val p = pending.elementAt(i)
                    if (!p.isCsd && !p.isKey) {
                        pending.removeAt(i)
                        queueDrops++
                        removed = true
                        break
                    }
                    i++
                }
                if (!removed) break   // cola llena de keys/csd: caso patologico, no tocar
            }
            if (queueDrops == 1L || (queueDrops > 0 && queueDrops % KEY_REQ_PERIOD == 0L)) {
                onNeedKey()   // delta descartado -> referencia rota -> IDR fresco YA
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
                i++
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
            return Pair(sps, csd.copyOfRange(i, i + ppsLen))
        } catch (_: Exception) { return null }
    }

    private fun logRateLimited(msg: String) {
        val now = System.currentTimeMillis()
        if (now - lastErrorLog > 5000) {
            lastErrorLog = now
            onStatus(msg)
        }
    }

    /** Llena inputs libres; items isCsd van con BUFFER_FLAG_CODEC_CONFIG. */
    private fun pumpLocked(c: MediaCodec) {
        while (freeInputs.isNotEmpty() && pending.isNotEmpty()) {
            val idx = freeInputs.removeFirst()
            val p = pending.removeFirst()
            try {
                val buf = c.getInputBuffer(idx)
                if (buf == null || p.data.size > buf.capacity()) continue
                buf.clear()
                buf.put(p.data)
                val flags = if (p.isCsd) MediaCodec.BUFFER_FLAG_CODEC_CONFIG else 0
                c.queueInputBuffer(idx, 0, p.data.size, p.pts, flags)
                framesFed++
            } catch (e: Exception) {
                logRateLimited("error de entrada al decoder: ${e.message}")
                return
            }
        }
    }

    /** Watchdog silencioso: actua solo si hay input fluyendo y CERO frames. */
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
            }
        }.apply { isDaemon = true }.start()
    }

    /** Fallback: rota al siguiente decoder y pide keyframe fresco. */
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
                onStatus("error: ningun decoder produce frames — revisa el relay")
                return
            }
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
