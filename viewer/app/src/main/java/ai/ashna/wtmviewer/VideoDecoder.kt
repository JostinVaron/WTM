package ai.ashna.wtmviewer

import android.media.MediaCodec
import android.media.MediaFormat
import android.view.Surface

/**
 * Decodificador -> Surface directo (issue #16, stage 1).
 *
 * diag6: MediaCodec en MODO ASINCRONO. El log diag5 demostro que el
 * decoder recibe un IDR HEVC valido (707 KB, framing byte-exacto) y
 * produce CERO frames en 13 segundos — sin error visible, sin
 * format-changed, sin nada. En modo sincronico un codec puede tragarse
 * los fallos; en modo async TODOS los callbacks quedan logueados:
 *   - onError: errorCode + diagnosticInfo del codec (el canal silencioso)
 *   - onOutputFormatChanged: el formato REAL que reporta el decoder
 *   - onOutputBufferAvailable: cada frame renderizado
 * y un watchdog imprime in=out/pendientes cada 2 s SIEMPRE — antes los
 * contadores de %300 nunca disparaban con un stream escaso (pantalla
 * estatica = pocos frames = ceros informacion).
 *
 * pts ahora viaja en MICROSEGUNDOS (MediaCodec espera us; antes ibamos
 * a ms por un comentario equivocado).
 */
class VideoDecoder(
    private val surface: Surface,
    private val onStatus: (String) -> Unit
) {
    private val lock = Any()
    private var codec: MediaCodec? = null
    @Volatile private var configured = false
    @Volatile private var needsReinit = false
    private var framesFed = 0L
    private var framesOut = 0L
    private var drops = 0L
    private var released = false

    private val freeInputs = ArrayDeque<Int>()
    private val pendingFrames = ArrayDeque<Pair<ByteArray, Long>>()

    // ultima config: para auto-reinit tras error del codec (issue #16 stage 3)
    private var lastCodecName = ""
    private var lastW = 0
    private var lastH = 0
    private var lastCsd: ByteArray = ByteArray(0)

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
            teardownLocked()
            freeInputs.clear()
            pendingFrames.clear()
            framesFed = 0
            framesOut = 0
            drops = 0
            needsReinit = false
            released = false
            lastCodecName = codecName
            lastW = width
            lastH = height
            lastCsd = csd

            onStatus("decoder: abrir $codecName como $mime ${width}x${height} csd=${csd.size}B (async)")

            val format = MediaFormat.createVideoFormat(mime, width, height).apply {
                setByteBuffer("csd-0", java.nio.ByteBuffer.wrap(csd))
            }
            val c: MediaCodec
            try {
                c = MediaCodec.createDecoderByType(mime)
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
                                onStatus("PRIMER FRAME RENDERIZADO al surface (${info.size}B pts=${info.presentationTimeUs}us flags=0x" +
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
            onStatus("decoder arrancado (async)")
        }

        // Watchdog: imprime el estado cada 2 s SIEMPRE (con stream escaso
        // tambien), y ejecuta el re-init tras errores del codec.
        Thread {
            while (configured && !released) {
                Thread.sleep(2000)
                if (!configured || released) break
                if (needsReinit) {
                    onStatus("decoder: re-init automatico tras error del codec")
                    synchronized(lock) {
                        teardownLocked()
                        configured = false
                        needsReinit = false
                    }
                    configure(lastCodecName, lastW, lastH, lastCsd)
                } else {
                    val p: Int
                    val f: Int
                    synchronized(lock) {
                        p = pendingFrames.size
                        f = freeInputs.size
                    }
                    onStatus("decoder vivo: in=$framesFed out=$framesOut pend=$p libres=$f drops=$drops")
                }
            }
        }.apply { isDaemon = true }.start()
    }

    /** Encola un frame; usa un input libre si hay, si no queda pendiente. */
    fun feed(data: ByteArray, pts: Long) {
        if (released || !configured) return
        val c = codec ?: return
        synchronized(lock) {
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
                    onStatus("primer buffer al decoder: ${data.size}B pts=$pts us")
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
