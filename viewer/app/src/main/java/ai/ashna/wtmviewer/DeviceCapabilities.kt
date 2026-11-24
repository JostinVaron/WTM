package ai.ashna.wtmviewer

import android.content.Context
import android.media.MediaCodecList
import android.net.wifi.WifiManager
import android.os.Build
import android.view.Display
import android.view.WindowManager

/**
 * Lee las capacidades REALES del receptor (plan F1, F4b).
 *
 * Regla del proyecto: nada viene de una tabla de modelos — todo es API de
 * Android en vivo, porque la metadata del fabricante puede mentir (la Tab
 * S6 Lite declara UHD@30 pero sostiene 2000x1200@60 real, verificado en
 * campo durante perf3).
 *
 * - Panel: Display.getSupportedModes() -> modo de mayor area; refreshRate
 *   es Float, asi que 59.94 Hz NTSC NO se redondea a 60 (matriz T6).
 * - Decoder: MediaCodecList(REGULAR_CODECS) -> techo bitrateRange.upper del
 *   decoder hardware; es el MISMO nombre que el HUD ya mostraba
 *   (c2.qti.hevc.decoder / OMX.qcom.video.decoder.hevc).
 * - WiFi: linkSpeed negociado (PHY). 0 = sin dato -> el algoritmo no
 *   clampa por enlace (validado en la matriz: techo util gana).
 */
class DeviceCapabilities(private val ctx: Context) {

    data class Report(
        val panelW: Int,
        val panelH: Int,
        val refreshExact: Double,
        val refreshMax: Double,
        val rotation: Int,
        val decoderName: String,
        val decoderBitrateMax: Long,
        val hwDecoders: Int,
        val linkMbps: Int,
    )

    fun read(): Report {
        val wm = ctx.getSystemService(Context.WINDOW_SERVICE) as WindowManager
        val display: Display = wm.defaultDisplay
        var panelW = 0
        var panelH = 0
        var bestArea = -1L
        var refreshMax = 0.0
        var refreshExact = 60.0
        for (mode in display.supportedModes) {
            val area = mode.physicalWidth.toLong() * mode.physicalHeight.toLong()
            if (area > bestArea) {
                bestArea = area
                panelW = mode.physicalWidth
                panelH = mode.physicalHeight
                refreshMax = mode.refreshRate.toDouble()
                refreshExact = mode.refreshRate.toDouble()
            }
        }
        if (panelW == 0) {
            // fallback: al menos el modo activo (API rara vez llega aqui)
            val m = display.mode
            panelW = m.physicalWidth
            panelH = m.physicalHeight
            refreshMax = m.refreshRate.toDouble()
            refreshExact = m.refreshRate.toDouble()
        }

        var decoderName = "none"
        var decoderBitrateMax = 0L
        var hwDecoders = 0
        try {
            val list = MediaCodecList(MediaCodecList.REGULAR_CODECS)
            for (info in list.codecInfos) {
                if (info.isEncoder) continue
                if (Build.VERSION.SDK_INT >= 29 && !info.isHardwareAccelerated) continue
                val type = info.supportedTypes.firstOrNull {
                    it == "video/hevc" || it == "video/avc"
                } ?: continue
                try {
                    val vc = info.getCapabilitiesForType(type).videoCapabilities ?: continue
                    val upper = vc.bitrateRange.upper.toLong()
                    hwDecoders++
                    if (upper > decoderBitrateMax) {
                        decoderBitrateMax = upper
                        decoderName = info.name
                    }
                } catch (_: Exception) {
                }
            }
        } catch (_: Exception) {
        }
        if (decoderBitrateMax <= 0L) {
            // Sin dato de la API (permisos/ROM): techo declarado generoso.
            // La negociacion igual clampa por techo util y techo del emisor.
            decoderBitrateMax = 200_000_000L
        }

        var linkMbps = 0
        try {
            @Suppress("DEPRECATION")
            val wifi = ctx.applicationContext
                .getSystemService(Context.WIFI_SERVICE) as? WifiManager
            @Suppress("DEPRECATION")
            val speed = wifi?.connectionInfo?.linkSpeed ?: 0
            if (speed > 0) linkMbps = speed
        } catch (_: Exception) {
        }

        return Report(
            panelW = panelW,
            panelH = panelH,
            refreshExact = refreshExact,
            refreshMax = refreshMax,
            rotation = display.rotation,
            decoderName = decoderName,
            decoderBitrateMax = decoderBitrateMax,
            hwDecoders = hwDecoders,
            linkMbps = linkMbps,
        )
    }
}
