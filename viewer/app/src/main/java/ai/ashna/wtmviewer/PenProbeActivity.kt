package ai.ashna.wtmviewer

import android.app.Activity
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.Typeface
import android.os.Bundle
import android.os.SystemClock
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import java.util.Locale
import kotlin.math.abs
import kotlin.math.roundToInt

/**
 * PenProbe (F6): app de verificacion que corre EN EL TELEFONO, sobre el
 * display virtual, y pinta en vivo lo que Android recibe de verdad:
 * toolType, presion, tilt, orientacion, flags y numero de punteros.
 *
 * Se lanza sin tocar el flujo normal:
 *   adb shell am start --display <id> -n ai.ashna.wtmviewer/.PenProbeActivity
 *
 * Sirve para distinguir tres fallos que antes eran invisibles:
 *   - el evento llega como FINGER (el UHID no se clasifico como stylus);
 *   - la presion llega plana (el descriptor HID no mapeo ABS_PRESSURE);
 *   - tilt/orientacion no llegan (falta ABS_TILT_X/Y o el signo es otro).
 * El trazo se dibuja con ancho proporcional a la presion: si la curva no
 * cambia, el problema esta en el camino, no en la app de dibujo.
 */
class PenProbeActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(PenProbeView(this))
    }
}

private data class ProbePoint(
    val x: Float,
    val y: Float,
    val pressure: Float,
    val tool: Int,
    val hover: Boolean
)

private class PenProbeView(context: android.content.Context) : View(context) {
    private val bg = Paint().apply { color = Color.rgb(12, 18, 24) }
    private val grid = Paint().apply {
        color = Color.rgb(28, 40, 50)
        strokeWidth = 1f
    }
    private val trail = Paint().apply {
        style = Paint.Style.STROKE
        strokeCap = Paint.Cap.ROUND
        strokeJoin = Paint.Join.ROUND
        isAntiAlias = true
    }
    private val dot = Paint().apply {
        style = Paint.Style.FILL
        isAntiAlias = true
    }
    private val text = Paint().apply {
        color = Color.rgb(220, 235, 245)
        textSize = 30f
        typeface = Typeface.MONOSPACE
        isAntiAlias = true
    }
    private val small = Paint().apply {
        color = Color.rgb(150, 175, 190)
        textSize = 24f
        typeface = Typeface.MONOSPACE
        isAntiAlias = true
    }
    private val badge = Paint().apply {
        color = Color.rgb(20, 30, 40)
        style = Paint.Style.FILL
        isAntiAlias = true
    }
    private val badgeText = Paint().apply {
        textSize = 44f
        typeface = Typeface.create(Typeface.MONOSPACE, Typeface.BOLD)
        isAntiAlias = true
    }

    private val points = ArrayList<ProbePoint>(512)

    private var actionLabel = "-"
    private var toolLabel = "-"
    private var toolValue = -1
    private var pressure = 0f
    private var tilt = 0f
    private var orientation = 0f
    private var px = 0f
    private var py = 0f
    private var pointerCount = 0
    private var buttons = 0
    private var flags = 0
    private var pointerId = -1
    private var events = 0
    private var lastEventMs = 0L
    private var eventsPerSec = 0f
    private var rateWindowStart = 0L
    private var rateWindowCount = 0
    private var hover = false
    private var historySize = 0
    private var pointerTools = IntArray(0)
    private var pointerPressures = FloatArray(0)
    private var pointerIds = IntArray(0)

    init {
        setBackgroundColor(Color.rgb(12, 18, 24))
    }

    override fun onTouchEvent(ev: MotionEvent): Boolean {
        process(ev, false)
        return true
    }

    override fun onGenericMotionEvent(ev: MotionEvent): Boolean {
        val a = ev.actionMasked
        if (a == MotionEvent.ACTION_HOVER_MOVE ||
            a == MotionEvent.ACTION_HOVER_ENTER ||
            a == MotionEvent.ACTION_HOVER_EXIT
        ) {
            process(ev, true)
            return true
        }
        return super.onGenericMotionEvent(ev)
    }

    private fun process(ev: MotionEvent, isHover: Boolean) {
        if (ev.pointerCount == 0) {
            actionLabel = actionNameOf(ev.actionMasked)
            invalidate()
            return
        }
        events++
        val now = SystemClock.elapsedRealtime()
        lastEventMs = now
        if (rateWindowStart == 0L) rateWindowStart = now
        rateWindowCount++
        val window = now - rateWindowStart
        if (window >= 500) {
            eventsPerSec = rateWindowCount * 1000f / window
            rateWindowStart = now
            rateWindowCount = 0
        }

        actionLabel = actionNameOf(ev.actionMasked)
        flags = ev.flags
        buttons = ev.buttonState
        pointerCount = ev.pointerCount
        historySize = ev.historySize
        hover = isHover || ev.actionMasked == MotionEvent.ACTION_HOVER_MOVE ||
            ev.actionMasked == MotionEvent.ACTION_HOVER_ENTER

        val i = if (ev.actionMasked == MotionEvent.ACTION_POINTER_DOWN ||
            ev.actionMasked == MotionEvent.ACTION_POINTER_UP
        ) ev.actionIndex else 0
        val idx = i.coerceIn(0, ev.pointerCount - 1)
        pointerId = ev.getPointerId(idx)
        toolValue = ev.getToolType(idx)
        toolLabel = toolNameOf(toolValue)
        pressure = ev.getPressure(idx)
        tilt = ev.getAxisValue(MotionEvent.AXIS_TILT, idx)
        orientation = ev.getAxisValue(MotionEvent.AXIS_ORIENTATION, idx)
        px = ev.getX(idx)
        py = ev.getY(idx)

        pointerTools = IntArray(ev.pointerCount) { ev.getToolType(it) }
        pointerPressures = FloatArray(ev.pointerCount) { ev.getPressure(it) }
        pointerIds = IntArray(ev.pointerCount) { ev.getPointerId(it) }

        // Trazo: solo contacto real (no hover) y tool de pluma/dedo.
        val touching = !hover && (ev.actionMasked == MotionEvent.ACTION_DOWN ||
            ev.actionMasked == MotionEvent.ACTION_MOVE ||
            ev.actionMasked == MotionEvent.ACTION_POINTER_DOWN)
        if (touching) {
            // muestras historicas: el S Pen muestrea mas rapido que el frame
            for (h in 0 until ev.historySize) {
                addPoint(ev.getHistoricalX(idx, h), ev.getHistoricalY(idx, h),
                    ev.getHistoricalPressure(idx, h), toolValue)
            }
            addPoint(px, py, pressure, toolValue)
        } else if (hover) {
            addPoint(px, py, 0f, toolValue)
        }

        invalidate()
    }

    private fun addPoint(x: Float, y: Float, p: Float, tool: Int) {
        points.add(ProbePoint(x, y, p.coerceIn(0f, 1f), tool, hover))
        if (points.size > 600) points.removeAt(0)
    }

    override fun onDraw(canvas: Canvas) {
        super.onDraw(canvas)
        canvas.drawRect(0f, 0f, width.toFloat(), height.toFloat(), bg)

        // rejilla de referencia (200 px)
        var gx = 0f
        while (gx < width) {
            canvas.drawLine(gx, 0f, gx, height.toFloat(), grid)
            gx += 200f
        }
        var gy = 0f
        while (gy < height) {
            canvas.drawLine(0f, gy, width.toFloat(), gy, grid)
            gy += 200f
        }

        // estela: ancho segun presion y color segun herramienta
        var prev: ProbePoint? = null
        for (pt in points) {
            if (prev != null && !pt.hover && !prev.hover) {
                trail.color = toolColor(pt.tool)
                trail.strokeWidth = 1.5f + 14f * pt.pressure
                canvas.drawLine(prev.x, prev.y, pt.x, pt.y, trail)
            }
            prev = pt
        }

        // punto actual (hover incluido)
        dot.color = toolColor(toolValue)
        val r = 6f + 26f * pressure
        canvas.drawCircle(px, py, r, dot)
        if (hover) {
            dot.style = Paint.Style.STROKE
            dot.strokeWidth = 2f
            canvas.drawCircle(px, py, 30f, dot)
            dot.style = Paint.Style.FILL
        }

        // panel de datos
        val left = 28f
        var y = 48f
        val line = 38f
        text.color = Color.rgb(230, 240, 250)
        text.textSize = 34f
        canvas.drawText("WTM PenProbe  —  evento #$events  (${fmt(eventsPerSec)} Hz)",
            left, y, text)
        y += line + 8f

        text.textSize = 30f
        canvas.drawText("action: $actionLabel   pointers: $pointerCount   pid: $pointerId",
            left, y, text); y += line
        canvas.drawText("tool: $toolLabel ($toolValue)   hover: $hover",
            left, y, text); y += line
        canvas.drawText("pressure: ${fmt(pressure)}   (${(pressure * 4095).roundToInt()} / 4095)",
            left, y, text); y += line
        canvas.drawText("tilt: ${fmt(tilt)} rad  (${fmt(Math.toDegrees(tilt.toDouble()).toFloat())}°)",
            left, y, text); y += line
        canvas.drawText("orientation: ${fmt(orientation)} rad  (${fmt(Math.toDegrees(orientation.toDouble()).toFloat())}°)",
            left, y, text); y += line
        canvas.drawText("x,y: ${px.roundToInt()}, ${py.roundToInt()}   history: $historySize",
            left, y, text); y += line
        val canceled = (flags and MotionEvent.FLAG_CANCELED) != 0
        canvas.drawText("buttons: 0x${Integer.toHexString(buttons)}   flags: 0x${Integer.toHexString(flags)}   CANCELED: $canceled",
            left, y, text); y += line

        // tabla de punteros si hay mas de uno (palma + pluma)
        if (pointerCount > 1) {
            y += 8f
            small.color = Color.rgb(180, 200, 215)
            for (i in 0 until pointerCount) {
                val t = toolNameOf(pointerTools.getOrElse(i) { 0 })
                val p = fmt(pointerPressures.getOrElse(i) { 0f })
                canvas.drawText("  #$i pid=${pointerIds.getOrElse(i) { -1 }} $t p=$p", left, y, small)
                y += 30f
            }
        }

        // insignia grande del tool actual
        val bw = 330f
        val bh = 76f
        val bx = width - bw - 30f
        val by = 30f
        badge.color = toolColor(toolValue).let { c ->
            Color.argb(70, Color.red(c), Color.green(c), Color.blue(c))
        }
        canvas.drawRoundRect(bx, by, bx + bw, by + bh, 14f, 14f, badge)
        badgeText.color = toolColor(toolValue)
        badgeText.textSize = 44f
        val label = when (toolValue) {
            2 -> "STYLUS"
            4 -> "ERASER"
            1 -> "FINGER"
            5 -> "PALM"
            else -> toolLabel
        }
        canvas.drawText(label, bx + 24f, by + 54f, badgeText)

        small.color = Color.rgb(140, 165, 180)
        canvas.drawText("Dibuja con el S Pen · apoya la palma · prueba el boton lateral",
            left, height - 24f, small)
    }

    private fun toolColor(tool: Int): Int = when (tool) {
        2 -> Color.rgb(80, 220, 255)     // stylus: cyan
        4 -> Color.rgb(255, 170, 60)     // eraser: naranja
        1 -> Color.rgb(120, 230, 130)    // finger: verde
        5 -> Color.rgb(255, 80, 80)      // palm: rojo
        else -> Color.rgb(200, 200, 200)
    }

    private fun toolNameOf(tool: Int): String = when (tool) {
        0 -> "UNKNOWN"
        1 -> "FINGER"
        2 -> "STYLUS"
        3 -> "MOUSE"
        4 -> "ERASER"
        5 -> "PALM"
        else -> "TOOL_$tool"
    }

    private fun actionNameOf(action: Int): String = when (action) {
        MotionEvent.ACTION_DOWN -> "DOWN"
        MotionEvent.ACTION_UP -> "UP"
        MotionEvent.ACTION_MOVE -> "MOVE"
        MotionEvent.ACTION_CANCEL -> "CANCEL"
        MotionEvent.ACTION_POINTER_DOWN -> "POINTER_DOWN"
        MotionEvent.ACTION_POINTER_UP -> "POINTER_UP"
        MotionEvent.ACTION_HOVER_MOVE -> "HOVER_MOVE"
        MotionEvent.ACTION_HOVER_ENTER -> "HOVER_ENTER"
        MotionEvent.ACTION_HOVER_EXIT -> "HOVER_EXIT"
        else -> "0x${Integer.toHexString(action)}"
    }

    private fun fmt(v: Float): String =
        String.format(Locale.US, if (abs(v) >= 100f) "%.1f" else "%.4f", v)
}
