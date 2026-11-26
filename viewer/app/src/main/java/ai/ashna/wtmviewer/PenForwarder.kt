package ai.ashna.wtmviewer

import android.os.Build
import android.os.SystemClock
import android.util.Log
import android.view.MotionEvent
import android.view.View
import java.util.Locale
import kotlin.math.roundToInt

/**
 * Captura TODOS los punteros del panel (S Pen + dedos) y los reenvia al
 * relay como mensajes pd/pm/pu/ph (F5/F6).
 *
 * Mapeo a la accion del protocolo scrcpy (un mensaje por puntero, como
 * parte los MotionEvents el cliente scrcpy original):
 *   DOWN / POINTER_DOWN -> pd  a=0   (el puntero nace con su pid estable)
 *   MOVE  (con muestras historicas ~120 Hz) -> pm a=2
 *   UP / POINTER_UP     -> pu  a=1
 *   CANCEL              -> pu  a=3
 *   HOVER_MOVE (stylus levantado, via onGenericMotion) -> ph a=7
 *   HOVER_EXIT (stylus sale del digitizador)           -> ph a=10
 *
 * Fidelidad del S Pen que preserva:
 *  - presion real (4096 niveles): viaja como "p" 0.0..1.0 SIN cuantizar
 *  - eventos historicos: el digitizador muestrea mas rapido que el frame
 *    rate — se reenvia CADA muestra, el trazo llega suave aunque el video
 *    corra a 60 fps
 *  - tilt ("tl") y orientacion ("or"): el relay los convierte a X/Y Tilt
 *    del dispositivo HID UHID (toolType=STYLUS real en el S25)
 *  - S Pen en MODO BORRADOR (TOOL_TYPE_ERASER): viaja como tool="eraser"
 *  - multi-touch: un pid por puntero, estable durante todo el gesto
 *
 * Rechazo de palma (F6, cookbook oficial de Android):
 *  1. TOOL_TYPE_PALM (valor 5, @hide en el SDK) se descarta siempre.
 *  2. FLAG_CANCELED (Android 13+) en ACTION_CANCEL / ACTION_POINTER_UP:
 *     el puntero se cancela (a=3), no se convierte en un UP normal.
 *  3. Mientras la pluma esta EN RANGO (toca o esta en hover a <~1-2 cm,
 *     como una Galaxy Tab con EMR), los dedos se descartan y los que ya
 *     estaban abajo se cancelan. El rango se conoce por HOVER_ENTER/MOVE
 *     y se libera con HOVER_EXIT (o un timeout de seguridad si el exit se
 *     pierde). Al tocar, ademas hay una ventana de 250 ms tras el ultimo
 *     contacto. La palma real la siguen filtrando TOOL_TYPE_PALM y
 *     FLAG_CANCELED.
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

        /** MotionEvent.TOOL_TYPE_PALM es @hide; su valor es estable (5). */
        private const val TOOL_TYPE_PALM = 5

        /** Ventana de gracia tras el ultimo contacto del stylus (250 ms). */
        private const val STYLUS_HOLDOFF_MS = 250L

        /** Red de seguridad si HOVER_EXIT se pierde (rango estatico). */
        private const val PEN_RANGE_TIMEOUT_MS = 3000L
    }

    private var prevStylusBtn = false

    /** pid del gesto que esta siendo consumido por la zona HUD (no viaja). */
    private var suppressPid = -1

    /** Ultimo instante (elapsedRealtime) en que el stylus toco el panel. */
    private var stylusActiveUntil = 0L

    /** Pluma dentro del rango del digitizador (touch o hover). */
    private var penInRange = false

    /** Ultimo evento de pluma (para el timeout de seguridad). */
    private var lastPenEventAt = 0L

    /** Dedos abajo: pid -> ultima posicion en coordenadas del stream. */
    private val activeFingers = HashMap<Int, Pair<Int, Int>>()

    override fun onTouch(v: View, ev: MotionEvent): Boolean {
        // Despacho sin buffer: menor latencia en trazos rapidos.
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

        // ¿El stylus esta TOCANDO en este evento?
        var stylusTouching = false
        for (i in 0 until ev.pointerCount) {
            if (isPenTool(ev.getToolType(i))) { stylusTouching = true; break }
        }
        if (stylusTouching) {
            val now = SystemClock.elapsedRealtime()
            if (!penInRange) Log.d("WTM", "penInRange=true (touch)")
            penInRange = true
            lastPenEventAt = now
            stylusActiveUntil = now + STYLUS_HOLDOFF_MS
            releaseActiveFingers()
        }

        // FLAG_CANCELED (API 33+): el puntero que sube fue involuntario
        // (palma). Se manda cancel, no un UP que el S25 interpretaria como
        // un toque real.
        val canceled = Build.VERSION.SDK_INT >= 33 &&
            (ev.flags and MotionEvent.FLAG_CANCELED) != 0

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
            MotionEvent.ACTION_POINTER_UP ->
                emit(ev, ev.actionIndex, -1, if (canceled) 3 else 1, sw, sh)
            MotionEvent.ACTION_UP -> emit(ev, 0, -1, 1, sw, sh)
            MotionEvent.ACTION_CANCEL ->
                for (i in 0 until ev.pointerCount) emit(ev, i, -1, 3, sw, sh)
        }
        return true
    }

    /** Hover del stylus: generic motion (enter/move/exit). */
    fun onGeneric(ev: MotionEvent): Boolean {
        checkStylusButton(ev)
        val masked = ev.actionMasked
        val sw = streamW()
        val sh = streamH()
        if (sw <= 0 || sh <= 0) return true
        when (masked) {
            MotionEvent.ACTION_HOVER_MOVE,
            MotionEvent.ACTION_HOVER_ENTER -> {
                for (i in 0 until ev.pointerCount) {
                    if (isPenTool(ev.getToolType(i))) {
                        if (!penInRange) Log.d("WTM", "penInRange=true (hover)")
                        penInRange = true
                        lastPenEventAt = SystemClock.elapsedRealtime()
                        // Si la palma ya estaba apoyada cuando entra la pluma,
                        // se cancela AHORA (EMR real: la palma no debe quedar).
                        releaseActiveFingers()
                        emit(ev, i, -1, 7, sw, sh)
                    }
                }
            }
            MotionEvent.ACTION_HOVER_EXIT -> {
                // a=10 -> el relay saca la pluma de rango al instante.
                // Mientras este en rango, Android suprime tactil y mouse
                // en el display virtual (igual que una Galaxy Tab real).
                if (penInRange) Log.d("WTM", "penInRange=false (hover exit)")
                penInRange = false
                lastPenEventAt = SystemClock.elapsedRealtime()
                for (i in 0 until ev.pointerCount) {
                    if (isPenTool(ev.getToolType(i))) {
                        emit(ev, i, -1, 10, sw, sh)
                    }
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
     * Suelta los dedos que ya estaban abajo cuando empezo el stylus.
     * Se manda a=3 (CANCEL) con la ultima posicion conocida de cada uno:
     * sin esto, el S25 se queda con un puntero fantasma hasta el proximo
     * UP real (que el rechazo de palma ya no reenviaria).
     */
    private fun releaseActiveFingers() {
        if (activeFingers.isEmpty()) return
        for ((pid, xy) in activeFingers) {
            send("{\"t\":\"pu\",\"a\":3,\"pid\":$pid," +
                "\"x\":${xy.first},\"y\":${xy.second}," +
                "\"p\":0.0000,\"pt\":\"finger\",\"tool\":\"finger\"," +
                "\"tl\":0.0000,\"or\":0.0000}")
        }
        activeFingers.clear()
    }

    /**
     * Un pointer de una muestra -> un mensaje pd/pm/pu/ph segun "act".
     * h >= 0: muestra historica (solo MOVE). act: accion scrcpy 0/1/2/3/7.
     */
    private fun emit(ev: MotionEvent, i: Int, h: Int, act: Int, sw: Int, sh: Int) {
        val tool = ev.getToolType(i)
        if (tool == TOOL_TYPE_PALM) return          // palma: nunca viaja

        val pen = isPenTool(tool)
        val finger = tool == MotionEvent.TOOL_TYPE_FINGER ||
            tool == MotionEvent.TOOL_TYPE_UNKNOWN
        if (!pen && !finger) return

        val pid = ev.getPointerId(i)

        // Politica stylus-activo: mientras la pluma esta EN RANGO (hover o
        // contacto) o dentro de la ventana de 250 ms, los dedos no llegan al
        // S25. Un dedo ya abajo se cancela una vez. Timeout de seguridad por
        // si HOVER_EXIT se pierde: nunca dejar el tactil bloqueado para siempre.
        val now = SystemClock.elapsedRealtime()
        if (penInRange && now - lastPenEventAt > PEN_RANGE_TIMEOUT_MS) {
            penInRange = false
            Log.d("WTM", "penInRange=false (timeout)")
        }
        if (finger && (penInRange || now < stylusActiveUntil)) {
            activeFingers.remove(pid)?.let { xy ->
                send("{\"t\":\"pu\",\"a\":3,\"pid\":$pid," +
                    "\"x\":${xy.first},\"y\":${xy.second}," +
                    "\"p\":0.0000,\"pt\":\"finger\",\"tool\":\"finger\"," +
                    "\"tl\":0.0000,\"or\":0.0000}")
            }
            return
        }

        val vw = view.width.coerceAtLeast(1)
        val vh = view.height.coerceAtLeast(1)
        val rawX = if (h >= 0) ev.getHistoricalX(i, h) else ev.getX(i)
        val rawY = if (h >= 0) ev.getHistoricalY(i, h) else ev.getY(i)
        // Panel -> pantalla del S25 (con fullscreen el aspect coincide 1:1).
        val x = (rawX * sw / vw).roundToInt()
        val y = (rawY * sh / vh).roundToInt()

        if (finger) {
            when (act) {
                0, 2 -> activeFingers[pid] = x to y
                1, 3 -> activeFingers.remove(pid)
            }
        }

        var p = if (h >= 0) ev.getHistoricalPressure(i, h) else ev.getPressure(i)
        if (act == 1 || act == 3) p = 0f          // up/cancel
        if (act == 7 || act == 10) p = 0f          // hover/exit: sin contacto
        if (!p.isFinite() || p < 0f) p = 0f
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
            7, 10 -> "ph"
            else -> "pm"
        }
        val pt = if (pen) "stylus" else "finger"
        val toolName = when {
            tool == MotionEvent.TOOL_TYPE_ERASER -> "eraser"
            pen -> "stylus"
            else -> "finger"
        }
        send("{\"t\":\"$t\",\"a\":$act,\"pid\":$pid," +
            "\"x\":$x,\"y\":$y,\"p\":${fmt(p)},\"pt\":\"$pt\"," +
            "\"tool\":\"$toolName\"," +
            "\"tl\":${fmt(tl)},\"or\":${fmt(or)}}")
    }

    private fun fmt(v: Float): String = String.format(Locale.US, "%.4f", v)
}
