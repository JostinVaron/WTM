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
 * diag9: LA PISTA. Aritmetica de todas las sesiones (diag5-8): cada AU
 * termina con EXACTAMENTE 4 bytes de mas tras el NAL declarado (707078
 * vs 707074; 706886 vs 706882; 706983 vs 706979; 707005 vs 707001) — un
 * prefijo de NAL de longitud 0, residuo del ensamblado en el server.
 * El selftest de diag8 demostro que el stack de la tablet esta SANO
 * (HEVC/AVC, 640x480 y 2000x1200, bytebuffer y surface: 8/8 loopbacks
 * con frames). Un AU malformado es descartado en silencio por parsers
 * estrictos de MediaCodec (out=0 sin error en los TRES decoders) y
 * tolerado por Chrome (por eso el cliente web funciona). diag9 SANEA el
 * AU antes de alimentar el decoder: recorre la estructura NAL por NAL
 * y descarta todo trailer invalido.
 *
 * Se conserva todo diag8 (b453b90): rotacion valida (boot completo + IDR
 * re-inyectado por decoder), pedido de IDR fresco ("t":"idr", el recovery
 * del cliente web), log del tipo NAL real, pts local monotónico.
 */
class VideoDecoder(
    private val surface: Surface,
    private val onStatus: (String) -> Unit,
    private val onNeedKey: () -> Unit
) {
    companion object {
        private const val SILENCE_TICKS = 3          // 3 ticks de 2s = 6s de out=0
        private val KEY_NALS = setOf(19, 20, 21)      // IDR_W_RADL, IDR_N_LP, CRA_NUT
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
    private var sanitized = 0L
    private var bytesStripped = 0L
    private var badAus = 0L
    private var configuredAt = 0L

    private val freeInputs = ArrayDeque<Int>()
    private val pending = ArrayDeque<Pending>()

    private var lastCodecName = ""
    private var lastMime = ""
    private var lastW = 0
    private var lastH = 0
    private var lastCsd: ByteArray = ByteArray(0)
    private var keyAu: ByteArray? = null

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
            sanitized = 0
            bytesStripped = 0
            badAus = 0
        }
        lastCodecName = codecName
        lastMime = mime
        lastW = width
        lastH = height
        lastCsd = csd
        openDecoder(null)
        onStatus("pidiendo IDR fresco al server ({\"t\":\"idr\"} — como el cliente web)")
        onNeedKey()
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
                setByteBuffer("csd-0", ByteBuffer.wrap(lastCsd))
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
            onStatus("decoder activo: '$activeName' — $lastMime ${lastW}x${lastH} csd=${lastCsd.size}B")

            pending.addLast(Pending(lastCsd, 0L, true))
            val paramsAu = paramsAuFromCsd()
            if (paramsAu != null) {
                pending.addLast(Pending(paramsAu, 0L, true))
            }
            val k = keyAu
            if (k != null) {
                pending.addLast(Pending(k, 0L, false))
                onStatus("re-inyectando IDR capturado (${k.size}B) al decoder nuevo")
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
     * SANEADOR (diag9): recorre el AU NAL por NAL y corta en el primer
     * prefijo invalido (longitud 0 o incompleta). El server deja un
     * trailer de 4 bytes (prefijo de NAL vacio) en CADA AU — la causa mas
     * probable del out=0 silencioso en parsers estrictos de MediaCodec.
     * Devuelve null si el AU no tiene ni un NAL resolvible.
     */
    private fun sanitizeAu(au: ByteArray): ByteArray? {
        var i = 0
        var end = 0
        var nals = 0
        while (i + 4 <= au.size) {
            val len = ((au[i].toInt() and 0xFF) shl 24) or ((au[i + 1].toInt() and 0xFF) shl 16) or
                ((au[i + 2].toInt() and 0xFF) shl 8) or (au[i + 3].toInt() and 0xFF)
            if (len <= 0 || i + 4 + len > au.size) break
            i += 4 + len
            end = i
            nals++
        }
        if (end == au.size) return au                      // AU limpio
        if (end == 0) return null                          // nada resolvible
        return au.copyOfRange(0, end)
    }

    /**
     * Encola un frame saneado con pts LOCAL monotónico. Los primeros 3
     * AUs se loguean con su tipo NAL real (IDR=19/20, CRA=21).
     */
    fun feed(data: ByteArray, ts: Long) {
        if (released || !configured) return
        val c = codec ?: return

        val clean = sanitizeAu(data)
        if (clean == null) {
            badAus++
            if (badAus <= 3)
                onStatus("AU descartado: sin NAL resolvible (${data.size}B)")
            return
        }
        if (clean.size != data.size) {
            sanitized++
            bytesStripped += (data.size - clean.size)
            if (sanitized == 1L) {
                onStatus("AU SANEADO: ${data.size}B -> ${clean.size}B — descartado " +
                    "trailer invalido de ${data.size - clean.size}B (prefijo de NAL vacio " +
                    "del server; la causa probable del silencio del decoder)")
            } else if (sanitized % 300 == 0L) {
                onStatus("saneados: $sanitized AUs ($bytesStripped B de trailers descartados)")
            }
        }

        val pts = synchronized(lock) { ++frameCounter * 33333L }
        val nal = firstNalType(clean)
        if (loggedAus < 3 && nal != null) {
            loggedAus++
            val kind = if (KEY_NALS.contains(nal)) "KEY" else "delta"
            onStatus("AU #$loggedAus: ${clean.size}B primer NAL=tipo$nal ($kind)")
        }
        if (nal != null && KEY_NALS.contains(nal)) {
            keyAu = clean
        }
        synchronized(lock) {
            pending.addLast(Pending(clean, pts, false))
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

    /** VPS/SPS/PPS del hvcC re-empaquetados como AU length-prefixed. */
    private fun paramsAuFromCsd(): ByteArray? {
        if (lastMime != MediaFormat.MIMETYPE_VIDEO_HEVC) return null
        val nals = nalsFromConfig(lastCsd)
        if (nals.isEmpty()) return null
        val au = ByteArrayOutputStream()
        for (n in nals) {
            au.write((n.size shr 24) and 0xFF)
            au.write((n.size shr 16) and 0xFF)
            au.write((n.size shr 8) and 0xFF)
            au.write(n.size and 0xFF)
            au.write(n)
        }
        return au.toByteArray()
    }

    /** Parsea los NALs de un hvcC (HEVC) — [] si no es parseable. */
    private fun nalsFromConfig(csd: ByteArray): List<ByteArray> {
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
                    onStatus("csd/params via input (CODEC_CONFIG): ${p.data.size}B")
                } else {
                    realFed++
                    if (realFed == 1L)
                        onStatus("primer AU real: ${p.data.size}B pts=${p.pts} us (decoder='$activeName')")
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
                    "pend=$p libres=$f drops=$drops saneados=$sanitized")
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
                onStatus("decoder: AGOTADOS ${decoderNames.size} decoders " +
                    "[${decoderNames.joinToString(", ")}] — todos recibieron csd+IDR saneados y out=0")
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
