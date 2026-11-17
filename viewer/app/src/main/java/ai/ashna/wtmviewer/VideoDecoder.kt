package ai.ashna.wtmviewer

import android.media.MediaCodec
import android.media.MediaFormat
import android.view.Surface

/**
 * Decodificador -> Surface directo (issue #16, stage 1).
 *
 * diag7: tres armas contra el "decoder silencioso" que el log diag6
 * demostro (in=95 out=0, sin error, sin formato, sin frames; csd y AU
 * verificados byte-exactos contra el server):
 *
 *  1. pts LOCAL monotónico (contador*33333us). El ts del server es
 *     epoch-µs (~1.79e15) y el cliente web JAMAS alimenta epoch a
 *     WebCodecs (usa media-time pequeño) — hay decoders hw con aritmetica
 *     interna que se ahogan con valores tan grandes. Se elimina la
 *     variable de raiz.
 *  2. nombre del decoder elegido queda logueado (getName()).
 *  3. watchdog: si el decoder consume y NO produce en 6 s, cambia solo
 *     a el decoder SOFTWARE (c2.android.hevc/avc.decoder) y re-inyecta
 *     el IDR capturado. Software Main-profile siempre decodifica: si
 *     produce frames, el culpable era el codec hw de la tablet (y se ve
 *     video en la MISMA prueba); si tampoco produce, queda impreso que
 *     el problema son los BYTES del stream, no el codec.
 *
 * KEY_MAX_INPUT_SIZE=4MB: el IDR de ~700KB debe caber tambien en los
 * buffers del decoder software.
 */
class VideoDecoder(
    private val surface: Surface,
    private val onStatus: (String) -> Unit
) {
    private val lock = Any()
    private var codec: MediaCodec? = null
    @Volatile private var configured = false
    @Volatile private var needsReinit = false
    @Volatile private var session = 0L
    @Volatile private var swTried = false
    @Volatile private var swAlsoSilentReported = false
    private var framesFed = 0L
    private var framesOut = 0L
    private var drops = 0L
    private var released = false
    private var frameCounter = 0L

    private val freeInputs = ArrayDeque<Int>()
    private val pendingFrames = ArrayDeque<Pair<ByteArray, Long>>()

    // ultima config: para re-init tras error del codec
    private var lastCodecName = ""
    private var lastW = 0
    private var lastH = 0
    private var lastCsd: ByteArray = ByteArray(0)

    // primer AU (IDR): se guarda para re-inyectarlo tras cualquier reinicio
    private var firstAu: ByteArray? = null

    /** "avc1.*" -> H.264, "hvc1.*"/"hev1.*" -> HEVC; otro -> error visible. */
    private fun mimeOf(codecName: String): String? = when {
        codecName.startsWith("avc1") -> MediaFormat.MIMETYPE_VIDEO_AVC
        codecName.startsWith("hvc1") || codecName.startsWith("hev1") ->
            MediaFormat.MIMETYPE_VIDEO_HEVC
        else -> null
    }

    private fun swName(mime: String): String? = when (mime) {
        MediaFormat.MIMETYPE_VIDEO_AVC -> "c2.android.avc.decoder"
        MediaFormat.MIMETYPE_VIDEO_HEVC -> "c2.android.hevc.decoder"
        else -> null
    }

    fun configure(codecName: String, width: Int, height: Int, csd: ByteArray,
                  forceSw: Boolean = false) {
        val mime = mimeOf(codecName)
        if (mime == null) {
            onStatus("codec NO soportado por el viewer: $codecName")
            return
        }
        var mySession = 0L
        synchronized(lock) {
            teardownLocked()
            freeInputs.clear()
            pendingFrames.clear()
            framesFed = 0
            framesOut = 0
            drops = 0
            needsReinit = false
            released = false
            frameCounter = 0
            session += 1
            mySession = session
            lastCodecName = codecName
            lastW = width
            lastH = height
            lastCsd = csd

            onStatus("decoder: abrir $codecName como $mime ${width}x${height} " +
                "csd=${csd.size}B (${if (forceSw) "software" else "auto"})")

            val format = MediaFormat.createVideoFormat(mime, width, height).apply {
                setByteBuffer("csd-0", java.nio.ByteBuffer.wrap(csd))
                // el IDR de pantalla completa pesa ~700KB: que el input
                // del decoder software tambien lo admita
                setInteger(MediaFormat.KEY_MAX_INPUT_SIZE, 4 * 1024 * 1024)
            }
            val c: MediaCodec
            try {
                c = if (forceSw) {
                    val name = swName(mime)
                    try {
                        MediaCodec.createDecoderByName(name!!)
                    } catch (e: Exception) {
                        onStatus("decoder software '$name' no disponible (${e.message}) — por tipo")
                        MediaCodec.createDecoderByType(mime)
                    }
                } else {
                    MediaCodec.createDecoderByType(mime)
                }
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
                                onStatus("PRIMER FRAME RENDERIZADO al surface (${info.size}B " +
                                    "pts=${info.presentationTimeUs}us flags=0x" +
                                    Integer.toHexString(info.flags) + ")")
                            } else if (framesOut % 300 == 0L) {
                                onStatus("render: $framesOut frames al surface")
                            }
                        } catch (e: Exception) {
                            onStatus("decoder error al renderizar frame: ${e.message}")
                            needsReinit = true
                        }
                    }

                    override fun onOutputFormatChanged(m: MediaCodec, format: MediaFormat) {
                        if (released) return
                        onStatus("decoder formato real: $format")
                    }

                    override fun onError(m: MediaCodec, e: MediaCodec.CodecException) {
                        if (released) return
                        onStatus("decoder ONERROR code=${e.errorCode} " +
                            "recoverable=${e.isRecoverable} transitorio=${e.isTransient}: ${e.diagnosticInfo}")
                        needsReinit = true
                    }
                })
                c.configure(format, surface, null, 0)
                c.start()
            } catch (e: Exception) {
                onStatus("decoder: FALLO abrir ($mime): ${e.message}")
                return
            }
            codec = c
            configured = true
            onStatus("decoder arrancado: ${c.name} (${if (forceSw) "SW" else "HW"})")

            // re-inyectar el IDR capturado tras cualquier reinicio: un
            // decoder nuevo necesita un keyframe para empezar a producir
            firstAu?.let { au ->
                val pts = ++frameCounter * 33333L
                pendingFrames.addLast(Pair(au, pts))
                onStatus("re-inyectando IDR capturado (${au.size}B) al decoder recien armado")
            }
        }

        // Watchdog cada 2 s: estado siempre visible + cambio HW->SW si
        // el decoder consume en silencio (el hallazgo central de diag6).
        Thread {
            var tick = 0
            while (configured && !released && session == mySession) {
                Thread.sleep(2000)
                if (!configured || released || session != mySession) break
                tick++
                if (needsReinit) {
                    onStatus("decoder: re-init tras error del codec")
                    synchronized(lock) {
                        teardownLocked()
                        configured = false
                        needsReinit = false
                    }
                    configure(lastCodecName, lastW, lastH, lastCsd, forceSw = swTried)
                    break
                }
                val p: Int
                val f: Int
                synchronized(lock) {
                    p = pendingFrames.size
                    f = freeInputs.size
                }
                val silent = framesOut == 0L && framesFed > 0L
                if (silent && !swTried && tick >= 3) {
                    swTried = true
                    onStatus("watchdog: HW consume $framesFed AUs y produce 0 frames en " +
                        "${tick * 2}s — cambio automatico a decoder SOFTWARE")
                    synchronized(lock) {
                        teardownLocked()
                        configured = false
                    }
                    configure(lastCodecName, lastW, lastH, lastCsd, forceSw = true)
                    break
                } else if (silent && swTried && tick >= 8 && !swAlsoSilentReported) {
                    swAlsoSilentReported = true
                    onStatus("watchdog: el decoder SOFTWARE tambien consume sin producir " +
                        "($framesFed AUs) — el problema son los BYTES del stream, no el codec")
                } else {
                    onStatus("decoder vivo [${if (swTried) "sw" else "hw"}]: " +
                        "in=$framesFed out=$framesOut pend=$p libres=$f drops=$drops")
                }
            }
        }.apply { isDaemon = true }.start()
    }

    /**
     * Encola un frame. pts LOCAL monotónico (contador*33333us): el ts del
     * server (epoch-µs, ~1.79e15) queda descartado como causa — el cliente
     * web nunca alimenta epoch a WebCodecs y hay decoders hw que se ahogan
     * con aritmetica interna de 32 bits sobre valores tan grandes.
     */
    fun feed(data: ByteArray, ts: Long) {
        if (released || !configured) return
        val c = codec ?: return
        synchronized(lock) {
            val pts = ++frameCounter * 33333L
            if (firstAu == null) {
                firstAu = data
                onStatus("IDR capturado y guardado (${data.size}B) para re-inyeccion")
            }
            pendingFrames.addLast(Pair(data, pts))
            if (pendingFrames.size > 90) {
                pendingFrames.removeFirst()
                drops++
                if (drops == 1L)
                    onStatus("decoder: cola de input saturada — descartando frames")
            }
            pumpLocked(c)
        }
    }

    /** Llena inputs libres con frames pendientes. Llamar bajo lock. */
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
                c.queueInputBuffer(idx, 0, data.size, pts, 0)
                framesFed++
                if (framesFed == 1L)
                    onStatus("primer buffer al decoder: ${data.size}B pts=$pts us (local, no epoch)")
            } catch (e: Exception) {
                onStatus("decoder error entrada: ${e.message} — marcado para re-init")
                needsReinit = true
                return
            }
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
