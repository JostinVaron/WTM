package ai.ashna.wtmviewer

import kotlin.math.ceil
import kotlin.math.min

/**
 * Espejo Kotlin del algoritmo ya validado en stream_profile.py (23/23 en
 * Termux, matriz F3). Misma semantica, mismas constantes:
 *
 * receiverBitrate = min(decoder, link*0.5, W*H*fps*0.1bpp)   [techo util]
 * Cap del viewer -> JSON {"t":"cap", w, h, fps, bitrate_max, link_mbps, device}
 *
 * El clamo al techo del EMISOR lo hace el relay (stream_profile.py); el
 * viewer solo reporta lo que su hardware puede. Misma regla de honestidad:
 * el HUD solo pinta verde "aplicado y verificado" si el cap_ack lo dice.
 */
object StreamProfile {

    const val BPP = 0.1                       // umbral visualmente lossless (HEVC)
    private const val CAP_W_MIN = 320
    private const val CAP_W_MAX = 4096
    private const val CAP_FPS_MIN = 24
    private const val CAP_FPS_MAX = 240
    private const val CAP_BITRATE_MAX = 200_000_000L

    data class Cap(
        val w: Int,
        val h: Int,
        val fps: Double,
        val bitrateMax: Long,
        val linkMbps: Int,
        val device: String,
    )

    /** Techo de bitrate del receptor: min(decoder, link/2, util). */
    fun receiverBitrate(w: Int, h: Int, fps: Double, decoderMax: Long, linkMbps: Int): Long {
        val useful = (w * h * fps * BPP).toLong()
        val link = if (linkMbps > 0) linkMbps * 1_000_000L / 2 else Long.MAX_VALUE
        return min(min(decoderMax, link), useful)
    }

    /** Construye el mensaje cap desde el report del hardware. */
    fun buildCap(report: DeviceCapabilities.Report): Cap {
        val w = report.panelW.coerceIn(CAP_W_MIN, CAP_W_MAX)
        val h = report.panelH.coerceIn(CAP_W_MIN, CAP_W_MAX)
        val fps = report.refreshExact.coerceIn(CAP_FPS_MIN.toDouble(), CAP_FPS_MAX.toDouble())
        val bitrate = receiverBitrate(w, h, fps, report.decoderBitrateMax, report.linkMbps)
        return Cap(
            w = w,
            h = h,
            fps = fps,
            bitrateMax = bitrate,
            linkMbps = report.linkMbps,
            device = "tab-s6-lite",
        )
    }

    /** JSON {"t":"cap",...} listo para el WS (el relay hace el clamp). */
    fun capJson(cap: Cap): String {
        // fps con 2 decimales: 59.94 NTSC viaja exacto, no redondeado
        val fpsStr = String.format(java.util.Locale.ROOT, "%.2f", cap.fps)
        return """{"t":"cap","w":${cap.w},"h":${cap.h},"fps":$fpsStr,""" +
            """"bitrate_max":${cap.bitrateMax},"link_mbps":${cap.linkMbps},"device":"${cap.device}"}"""
    }

    /** Parseo del cap_ack del relay: {"t":"cap_ack","applied":..,"profile":{w,h,fps,bitrate},"reason":..} */
    data class CapAck(
        val applied: Boolean,
        val w: Int,
        val h: Int,
        val fps: Int,
        val bitrate: Long,
        val reason: String,
    )

    fun parseCapAck(json: org.json.JSONObject): CapAck {
        val profile = json.optJSONObject("profile")
        return CapAck(
            applied = json.optBoolean("applied", false),
            w = profile?.optInt("w", 0) ?: 0,
            h = profile?.optInt("h", 0) ?: 0,
            fps = profile?.optInt("fps", 0) ?: 0,
            bitrate = profile?.optLong("bitrate", 0L) ?: 0L,
            reason = json.optString("reason", ""),
        )
    }

    /** Linea de HUD: verde si aplicado+verificado, rojo si no. */
    fun hudLine(ack: CapAck): String {
        return if (ack.applied && ack.w > 0) {
            "perfil: ${ack.w}x${ack.h}@${ack.fps} @ ${ack.bitrate / 1_000_000} Mbps — aplicado y verificado"
        } else {
            "perfil NO aplicado: ${ack.reason.ifEmpty { "sin respuesta del relay" }}"
        }
    }
}
