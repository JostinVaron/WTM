package ai.ashna.wtmviewer

import android.view.MotionEvent
import android.view.View
import java.util.Locale
import kotlin.math.roundToInt

/**
 * Captura TODOS los punteros del panel (S Pen + dedos) y los reenvia al
 * relay como mensajes pd/pm/pu/ph (F5).
 *
 * Fidelidad del S Pen que preserva:
 *  - presion real (4096 niveles): viaja como "p" 0.0..1.0 sin cuantizar
 *  - eventos historicos: el digitizador muestrea ~120 Hz aunque los frames
 *    lleguen a 60 — se reenvia CADA muestra, el trazo llega suave
 *  - tilt ("tl") y orientacion ("or") del stylus: hoy el touch del scrcpy
 *    no las inyecta, pero viajan en el mensaje para el backend X11
 *  - rechazo de palma: TOOL_TYPE_PALM se descarta (el digitizador Samsung
 *    ya lo marca); solo stylus y dedos dibujan
 *  - multi-touch: un pid por puntero (estable durante todo el gesto)
 *  - "a" es la accion enmascarada de MotionEvent (0/1/2/3/5/6/10): el
 *    relay queda stateless y no hay estado sucio tras reconectar
 *
 * HUD: se alterna con el BOTON LATERAL del S Pen (BUTTON_STYLUS_PRIMARY);
 * ningun toque se consume localmente — todo lo que toca la pantalla viaja
 * al S25.
 */
class PenForwarder(
    private val view: View,
    private val streamW: () -> Int,
    private val streamH: () -> Int,
    private val send: (String) -> Unit,
    private val onStylusButton: () -> Unit
) : View.OnTouchListener {

    private var prevStylusBtn = false

    override fun onTouch(v: View, ev: MotionEvent): Boolean {
        // Despacho sin buffer: menor latencia en trazos rapidos.
        try { v.requestUnbufferedDispatch(ev) } catch (_: Exception) {}

        val sw = streamW()
        val sh = streamH()
        if (sw <= 0 || sh <= 0) return true   // aun no llega el config del stream

        val masked = ev.actionMasked
        val hover = masked == MotionEvent.ACTION_HOVER_MOVE ||
            masked == MotionEvent.ACTION_HOVER_ENTER

        // Boton lateral del S Pen: alterna el HUD sin robar el trazo.
        val stylusBtn = (ev.buttonState and MotionEvent.BUTTON_STYLUS_PRIMARY) != 0
        if (stylusBtn && !prevStylusBtn) onStylusButton()
        prevStylusBtn = stylusBtn

        // 1) Muestras historicas (~120 Hz): solo son movimiento.
        if (!hover) {
            for (h in 0 until ev.historySize) {
                for (i in 0 until ev.pointerCount) emit(ev, i, h, "pm", 2, sw, sh)
            }
        }

        // 2) Evento actual.
        when (masked) {
            MotionEvent.ACTION_DOWN, MotionEvent.ACTION_POINTER_DOWN -> {
                for (i in 0 until ev.pointerCount) {
                    if (i == ev.actionIndex) emit(ev, i, -1, "pd", masked, sw, sh)
                    else emit(ev, i, -1, "pm", 2, sw, sh)
                }
            }
            MotionEvent.ACTION_MOVE ->
                for (i in 0 until ev.pointerCount) emit(ev, i, -1, "pm", 2, sw, sh)
            MotionEvent.ACTION_POINTER_UP ->
                emit(ev, ev.actionIndex, -1, "pu", masked, sw, sh)
            MotionEvent.ACTION_UP ->
                emit(ev, 0, -1, "pu", masked, sw, sh)
            MotionEvent.ACTION_CANCEL ->
                for (i in 0 until ev.pointerCount) emit(ev, i, -1, "pu", 1, sw, sh)
            MotionEvent.ACTION_HOVER_MOVE, MotionEvent.ACTION_HOVER_ENTER ->
                for (i in 0 until ev.pointerCount) {
                    if (ev.getToolType(i) == MotionEvent.TOOL_TYPE_STYLUS) {
                        emit(ev, i, -1, "ph", 10, sw, sh)
                    }
                }
        }
        return true
    }

    /** Un pointer de una muestra -> un mensaje. h >= 0: muestra historica. */
    private fun emit(ev: MotionEvent, i: Int, h: Int, t: String, act: Int, sw: Int, sh: Int) {
        val tool = ev.getToolType(i)
        if (tool == MotionEvent.TOOL_TYPE_PALM) return   // rechazo de palma
        if (tool != MotionEvent.TOOL_TYPE_STYLUS && tool != MotionEvent.TOOL_TYPE_FINGER) return

        val vw = view.width.coerceAtLeast(1)
        val vh = view.height.coerceAtLeast(1)
        val rawX = if (h >= 0) ev.getHistoricalX(i, h) else ev.getX(i)
        val rawY = if (h >= 0) ev.getHistoricalY(i, h) else ev.getY(i)
        // Panel -> pantalla del S25 (con fullscreen el aspect coincide 1:1).
        val x = (rawX * sw / vw).roundToInt()
        val y = (rawY * sh / vh).roundToInt()

        var p = if (h >= 0) ev.getHistoricalPressure(i, h) else ev.getPressure(i)
        if (act == 1 || act == 3) p = 0f
        if (!p.isFinite() || p < 0f) p = 0f
        if (p > 1f) p = 1f

        val stylus = tool == MotionEvent.TOOL_TYPE_STYLUS
        var tl = 0f
        var or = 0f
        if (stylus) {
            tl = if (h >= 0) ev.getHistoricalAxisValue(MotionEvent.AXIS_TILT, i, h)
                 else ev.getAxisValue(MotionEvent.AXIS_TILT, i)
            or = if (h >= 0) ev.getHistoricalAxisValue(MotionEvent.AXIS_ORIENTATION, i, h)
                 else ev.getAxisValue(MotionEvent.AXIS_ORIENTATION, i)
            if (!tl.isFinite()) tl = 0f
            if (!or.isFinite()) or = 0f
        }

        val pt = if (stylus) "stylus" else "finger"
        send("{\"t\":\"$t\",\"a\":$act,\"pid\":${ev.getPointerId(i)}," +
            "\"x\":$x,\"y\":$y,\"p\":${fmt(p)},\"pt\":\"$pt\"," +
            "\"tl\":${fmt(tl)},\"or\":${fmt(or)}}")
    }

    private fun fmt(v: Float): String = String.format(Locale.US, "%.4f", v)
}
