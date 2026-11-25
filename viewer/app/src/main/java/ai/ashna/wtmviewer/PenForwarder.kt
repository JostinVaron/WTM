package ai.ashna.wtmviewer

import android.view.MotionEvent
import android.view.View
import java.util.Locale
import kotlin.math.roundToInt

/**
 * Captura TODOS los punteros del panel (S Pen + dedos) y los reenvia al
 * relay como mensajes pd/pm/pu/ph (F5).
 *
 * Mapeo a la accion del protocolo scrcpy (un mensaje por puntero, como
 * parte los MotionEvents el cliente scrcpy original):
 *   DOWN / POINTER_DOWN -> pd  a=0   (el puntero nace con su pid estable)
 *   MOVE  (con muestras historicas ~120 Hz) -> pm a=2
 *   UP / POINTER_UP     -> pu  a=1
 *   CANCEL              -> pu  a=3
 *   HOVER_MOVE (stylus levantado, via onGenericMotion) -> ph a=7
 *
 * Fidelidad del S Pen que preserva:
 *  - presion real (4096 niveles): viaja como "p" 0.0..1.0 SIN cuantizar
 *  - eventos historicos: el digitizador muestrea mas rapido que el frame
 *    rate — se reenvia CADA muestra, el trazo llega suave aunque el video
 *    corra a 60 fps
 *  - tilt ("tl") y orientacion ("or"): hoy el touch del scrcpy no las
 *    inyecta, pero viajan en el mensaje para el backend X11 futuro
 *  - S Pen en MODO BORRADOR (TOOL_TYPE_ERASER) se reenvia como stylus
 *  - multi-touch: un pid por puntero, estable durante todo el gesto
 *
 * Rechazo de palma: lo hace el digitizador/framework de Samsung ANTES de
 * entregar el evento (no existe TOOL_TYPE_PALM en el SDK). Aqui solo se
 * reenvian stylus/eraser/finger; mouse y desconocidos se descartan.
 *
 * HUD: se alterna tocando la esquina superior derecha (la zona del
 * propio HUD — ese gesto se consume localmente, NO viaja al S25) o con
 * el boton lateral del S Pen en plumas que lo tengan. Todo lo demas
 * viaja integro: ningun trazo se roba.
 */
class PenForwarder(
    private val view: View,
    private val streamW: () -> Int,
    private val streamH: () -> Int,
    private val send: (String) -> Unit,
    private val onHudToggle: () -> Unit
) : View.OnTouchListener {

    companion object {
        /** Zona HUD: esquina superior derecha, ~56dp. */
        private const val HUD_DP = 56

        /** Diagnostico acotado del pipeline de input (logcat, primeras 30). */
        private var dbgCount = 0
    }

    private var prevStylusBtn = false

    /** pid del gesto que esta siendo consumido por la zona HUD (no viaja). */
    private var suppressPid = -1

    override fun onTouch(v: View, ev: MotionEvent): Boolean {
        // Despacho sin buffer: menor latencia en trazos rapidos.
        if (dbgCount < 30) {
            dbgCount++
            android.util.Log.d("WTM", "onTouch masked=${ev.actionMasked} " +
                "x=${ev.x.toInt()} y=${ev.y.toInt()} tool=${ev.getToolType(0)} " +
                "sw=${streamW()} sh=${streamH()}")
        }
        try { v.requestUnbufferedDispatch(ev) } catch (_: Exception) {}

        checkStylusButton(ev)

        val masked = ev.actionMasked

        // Zona HUD: DOWN de un solo puntero dentro de la esquina sup-derecha
        // -> toggle local; el gesto completo se consume (no viaja al S25).
        if (suppressPid >= 0) {
            val pidStill = (0 until ev.pointerCount)
                .any { ev.getPointerId(it) == suppressPid }
            when {
                masked == MotionEvent.ACTION_UP ||
                    masked == MotionEvent.ACTION_CANCEL -> { suppressPid = -1; return true }
                !pidStill -> suppressPid = -1   // el pid desaparecio: seguir normal
                else -> return true             // resto del gesto consumido
            }
        }
        if (masked == MotionEvent.ACTION_DOWN) {
            val hudPx = view.resources.displayMetrics.density * HUD_DP
            if (ev.x >= view.width - hudPx && ev.y <= hudPx) {
                suppressPid = ev.getPointerId(0)
                onHudToggle()
                return true
            }
        }

        val sw = streamW()
        val sh = streamH()
        if (sw <= 0 || sh <= 0) return true   // aun no llega el config del stream

        // 1) Muestras historicas (~120 Hz) — solo en MOVE: el conjunto de
        //    punteros no cambia, getHistorical* es valido para todos.
        if (masked == MotionEvent.ACTION_MOVE) {
            for (h in 0 until ev.historySize) {
                for (i in 0 until ev.pointerCount) emit(ev, i, h, 2, sw, sh)
            }
        }

        // 2) Evento actual -> mensajes por puntero.
        when (masked) {
            MotionEvent.ACTION_DOWN -> emit(ev, 0, -1, 0, sw, sh)
            MotionEvent.ACTION_POINTER_DOWN -> emit(ev, ev.actionIndex, -1, 0, sw, sh)
            MotionEvent.ACTION_MOVE ->
                for (i in 0 until ev.pointerCount) emit(ev, i, -1, 2, sw, sh)
            MotionEvent.ACTION_POINTER_UP -> emit(ev, ev.actionIndex, -1, 1, sw, sh)
            MotionEvent.ACTION_UP -> emit(ev, 0, -1, 1, sw, sh)
            MotionEvent.ACTION_CANCEL ->
                for (i in 0 until ev.pointerCount) emit(ev, i, -1, 3, sw, sh)
        }
        return true
    }

    /** Hover del stylus (pluma levantada cerca del panel): generic motion. */
    fun onGeneric(ev: MotionEvent): Boolean {
        checkStylusButton(ev)
        val masked = ev.actionMasked
        if (masked == MotionEvent.ACTION_HOVER_MOVE ||
            masked == MotionEvent.ACTION_HOVER_ENTER) {
            val sw = streamW()
            val sh = streamH()
            if (sw <= 0 || sh <= 0) return true
            for (i in 0 until ev.pointerCount) {
                if (isPenTool(ev.getToolType(i))) {
                    emit(ev, i, -1, 7, sw, sh)
                }
            }
        }
        return true
    }

    /** Stylus o borrador: las dos puntas del S Pen dibujan. */
    private fun isPenTool(tool: Int) =
        tool == MotionEvent.TOOL_TYPE_STYLUS || tool == MotionEvent.TOOL_TYPE_ERASER

    /** Boton lateral del S Pen (plumas que lo tienen): toggle del HUD. */
    private fun checkStylusButton(ev: MotionEvent) {
        val b = (ev.buttonState and MotionEvent.BUTTON_STYLUS_PRIMARY) != 0
        if (b && !prevStylusBtn) onHudToggle()
        prevStylusBtn = b
    }

    /**
     * Un pointer de una muestra -> un mensaje pd/pm/pu/ph segun "act".
     * h >= 0: muestra historica (solo MOVE). act: accion scrcpy 0/1/2/3/7.
     */
    private fun emit(ev: MotionEvent, i: Int, h: Int, act: Int, sw: Int, sh: Int) {
        val tool = ev.getToolType(i)
        val pen = isPenTool(tool)
        // Solo dibujan stylus/eraser/finger; el rechazo de palma ya lo hizo
        // el framework de Samsung antes de entregar el evento.
        if (dbgCount < 60) {
            dbgCount++
            android.util.Log.d("WTM", "emit act=$act tool=$tool pen=$pen")
        }
        if (!pen && tool != MotionEvent.TOOL_TYPE_FINGER &&
            tool != MotionEvent.TOOL_TYPE_UNKNOWN) return

        val vw = view.width.coerceAtLeast(1)
        val vh = view.height.coerceAtLeast(1)
        val rawX = if (h >= 0) ev.getHistoricalX(i, h) else ev.getX(i)
        val rawY = if (h >= 0) ev.getHistoricalY(i, h) else ev.getY(i)
        // Panel -> pantalla del S25 (con fullscreen el aspect coincide 1:1).
        val x = (rawX * sw / vw).roundToInt()
        val y = (rawY * sh / vh).roundToInt()

        var p = if (h >= 0) ev.getHistoricalPressure(i, h) else ev.getPressure(i)
        if (act == 1 || act == 3) p = 0f          // up/cancel
        if (act == 7) p = 1f                       // hover
        if (!p.isFinite() || p < 0f) p = if (act == 7) 1f else 0f
        if (p > 1f) p = 1f

        var tl = 0f
        var or = 0f
        if (pen) {
            tl = if (h >= 0) ev.getHistoricalAxisValue(MotionEvent.AXIS_TILT, i, h)
                 else ev.getAxisValue(MotionEvent.AXIS_TILT, i)
            or = if (h >= 0) ev.getHistoricalAxisValue(MotionEvent.AXIS_ORIENTATION, i, h)
                 else ev.getAxisValue(MotionEvent.AXIS_ORIENTATION, i)
            if (!tl.isFinite()) tl = 0f
            if (!or.isFinite()) or = 0f
        }

        val t = when (act) {
            0 -> "pd"
            1, 3 -> "pu"
            7 -> "ph"
            else -> "pm"
        }
        val pt = if (pen) "stylus" else "finger"
        send("{\"t\":\"$t\",\"a\":$act,\"pid\":${ev.getPointerId(i)}," +
            "\"x\":$x,\"y\":$y,\"p\":${fmt(p)},\"pt\":\"$pt\"," +
            "\"tl\":${fmt(tl)},\"or\":${fmt(or)}}")
    }

    private fun fmt(v: Float): String = String.format(Locale.US, "%.4f", v)
}
