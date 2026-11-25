package ai.ashna.wtmviewer

import android.app.Activity
import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.util.TypedValue
import android.view.Gravity
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import android.content.Context
import android.content.SharedPreferences
import java.net.URL
import java.security.SecureRandom
import java.security.cert.X509Certificate
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

/**
 * WTM Viewer — viewer nativo del relay (issue #16).
 *
 * v1.2-cap (F4b) — PANTALLA COMPLETA ABSOLUTA + NEGOCIACION cap.
 *
 * v1.3-pen (F5) — S PEN Y MULTI-TOUCH COMPLETOS: TODO lo que toca el panel
 * viaja al S25 (PenForwarder): presion real (4096 niveles), muestras
 * historicas (~120 Hz), tilt/orientacion del stylus, rechazo de palma,
 * multi-touch con pid estable y hover de la pluma. El HUD se alterna con
 * un toque en la esquina superior derecha (zona del propio HUD — consumida
 * localmente) o con el boton del S Pen (plumas que lo tienen): ningun
 * trazo dibujante se roba.
 */
class MainActivity : Activity() {

    companion object {
        const val BUILD = "1.3-pen-r2"
    }

    private lateinit var prefs: SharedPreferences
    private var wsClient: WsClient? = null
    private var decoder: VideoDecoder? = null
    private var overlay: TextView? = null
    private var hud: TextView? = null
    private var frameRoot: FrameLayout? = null
    private var backBtn: Button? = null
    private val logBuf = StringBuilder()
    private val t0 = System.currentTimeMillis()
    private var prevHandler: Thread.UncaughtExceptionHandler? = null
    private var hudVisible = false
    @Volatile private var hudRunning = false
    private var lastRendered = 0L
    private var lastRenderedAt = 0L
    private var measurePersist = 0

    // F5: dimensiones del stream (para mapear tablet -> pantalla del S25).
    @Volatile private var streamW = 0
    @Volatile private var streamH = 0

    // F4b: veredicto de la negociacion cap para el HUD (jamas verde falso).
    @Volatile private var capHudLine = "perfil: esperando respuesta del relay..."

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("wtm", Context.MODE_PRIVATE)
        prevHandler = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { t, e ->
            persistCrash(t, e)
            prevHandler?.uncaughtException(t, e)
        }
        // Auto-conexion (2026-11-25): si ya hay URL + token validos guardados,
        // la sesion arranca sola en vez de obligar a pulsar "Conectar" en cada
        // apertura. Con error, el boton "Volver" devuelve a la configuracion.
        val savedUrl = prefs.getString("base_url", null)
        val savedToken = prefs.getString("viewer_token", null)
        if (!savedUrl.isNullOrEmpty() && !savedToken.isNullOrEmpty() &&
            savedToken.length == 64 && savedUrl.startsWith("https://")) {
            startStream(savedUrl, savedToken)
        } else {
            buildConfigUI("")
        }
    }

    /**
     * PANTALLA COMPLETA ABSOLUTA (F4b): immersive sticky. Oculta status bar
     * Y la gesture/navigation bar inferior (la barra fina de abajo), y
     * expande la app bajo ellas. Re-aplicar en onWindowFocusChanged porque
     * el sistema las restaura tras teclado/gestos/notifications shade.
     */
    private fun enterImmersive() {
        window.decorView.systemUiVisibility =
            View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY or
            View.SYSTEM_UI_FLAG_FULLSCREEN or
            View.SYSTEM_UI_FLAG_HIDE_NAVIGATION or
            View.SYSTEM_UI_FLAG_LAYOUT_STABLE or
            View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN or
            View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        if (hasFocus) enterImmersive()
    }

    /** Log -> disco, sincrono: sobrevive a cualquier muerte del proceso. */
    private fun persistLog() {
        try {
            synchronized(logBuf) {
                prefs.edit().putString("last_console", logBuf.toString()).commit()
            }
        } catch (_: Exception) {}
    }

    private fun persistCrash(t: Thread, e: Throwable) {
        val text = "CRASH ${t.name}: $e\n" + e.stackTraceToString().take(3000)
        try { prefs.edit().putString("last_crash", text).commit() } catch (_: Exception) {}
        val url = prefs.getString("base_url", null) ?: return
        val token = prefs.getString("viewer_token", null) ?: return
        val reporter = Thread {
            try {
                val host = java.net.URI(url).host ?: return@Thread
                val ctx = SSLContext.getInstance("TLS")
                ctx.init(null, arrayOf<TrustManager>(object : X509TrustManager {
                    override fun checkClientTrusted(c: Array<X509Certificate>, a: String) {}
                    override fun checkServerTrusted(c: Array<X509Certificate>, a: String) {}
                    override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
                }), SecureRandom())
                val conn = URL(url.trimEnd('/') + "/log").openConnection() as HttpsURLConnection
                conn.sslSocketFactory = ctx.socketFactory
                conn.hostnameVerifier = HostnameVerifier { _, _ -> true }
                conn.requestMethod = "POST"
                conn.doOutput = true
                conn.connectTimeout = 4000
                conn.readTimeout = 4000
                conn.setRequestProperty("Content-Type", "text/plain")
                conn.setRequestProperty("Cookie", "wtm_session=$token")
                conn.setRequestProperty("Origin", "https://$host")
                conn.outputStream.use { it.write(("viewer $BUILD " + text).toByteArray(Charsets.UTF_8)) }
                conn.responseCode
                conn.disconnect()
            } catch (_: Exception) {}
        }
        try { reporter.start(); reporter.join(4000) } catch (_: Exception) {}
    }

    private fun buildConfigUI(initialStatus: String) {
        val pad = dp(12)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }
        val label = TextView(this).apply { text = "WTM Viewer v$BUILD — conectar al relay" }
        val urlInput = EditText(this).apply {
            hint = "https://<ip-del-S25>:8443"
            setText(prefs.getString("base_url", ""))
        }
        val tokenInput = EditText(this).apply {
            hint = "Token viewer (64 caracteres, auth-tokens.json)"
            setText(prefs.getString("viewer_token", ""))
        }
        val status = TextView(this).apply {
            text = initialStatus.ifEmpty { "Estado: desconectado" }
            setTextColor(if (initialStatus.startsWith("error")) Color.parseColor("#FF6A6A") else Color.WHITE)
        }
        val connectBtn = Button(this).apply { text = "Conectar" }

        val lastCrash = prefs.getString("last_crash", null)
        if (lastCrash != null) {
            root.addView(TextView(this).apply {
                text = "ULTIMO CRASH:\n${lastCrash.take(1200)}"
                setTextColor(Color.parseColor("#FF6A6A"))
                setTextSize(TypedValue.COMPLEX_UNIT_SP, 10f)
            })
            prefs.edit().remove("last_crash").apply()
        }

        val lastConsole = prefs.getString("last_console", null)
        if (!lastConsole.isNullOrEmpty()) {
            val shareBtn = Button(this).apply { text = "Compartir log de la ultima sesion" }
            shareBtn.setOnClickListener {
                val send = Intent(Intent.ACTION_SEND).apply {
                    type = "text/plain"
                    putExtra(Intent.EXTRA_TEXT,
                        "WTM viewer v$BUILD — log de la ultima sesion:\n\n$lastConsole")
                }
                startActivity(Intent.createChooser(send, "Compartir log"))
            }
            root.addView(shareBtn)
        }

        connectBtn.setOnClickListener {
            val url = urlInput.text.toString().trim()
            val token = tokenInput.text.toString().trim()
            when {
                url.isEmpty() || token.isEmpty() ->
                    { status.text = "Estado: falta URL o token"; status.setTextColor(Color.WHITE) }
                !url.startsWith("https://") ->
                    { status.text = "Estado: la URL debe ser https://<ip>:8443"; status.setTextColor(Color.WHITE) }
                token.length != 64 ->
                    { status.text = "Estado: el token debe tener 64 caracteres (tiene ${token.length})"; status.setTextColor(Color.WHITE) }
                else -> {
                    prefs.edit().putString("base_url", url).putString("viewer_token", token).apply()
                    startStream(url, token)
                }
            }
        }

        root.addView(label)
        root.addView(urlInput)
        root.addView(tokenInput)
        root.addView(connectBtn)
        root.addView(status)
        setContentView(root)
        enterImmersive()   // F4b: fullscreen tambien en la pantalla de config
    }

    private fun startStream(url: String, token: String) {
        synchronized(logBuf) { logBuf.setLength(0) }
        persistLog()
        backBtn = null
        streamW = 0
        streamH = 0
        capHudLine = "perfil: esperando respuesta del relay..."

        val surfaceView = SurfaceView(this)
        val statusOverlay = TextView(this).apply {
            text = "conectando..."
            setTextColor(Color.WHITE)
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 12f)
            setBackgroundColor(Color.argb(0x80, 0x00, 0x00, 0x00))
            val pad = dp(8)
            setPadding(pad, pad, pad, pad)
            visibility = View.VISIBLE
        }
        overlay = statusOverlay

        // HUD de perf: esquina superior derecha. F5: ahora se alterna
        // tocando esa misma esquina (PenForwarder) o con el boton del S Pen.
        val hudView = TextView(this).apply {
            setTextColor(Color.parseColor("#7FFF7F"))
            typeface = Typeface.MONOSPACE
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 10f)
            setBackgroundColor(Color.argb(0x66, 0x00, 0x00, 0x00))
            val pad = dp(6)
            setPadding(pad, pad, pad, pad)
            visibility = View.GONE
        }
        hud = hudView

        val frame = FrameLayout(this)
        frame.addView(
            surfaceView,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT
            )
        )
        frame.addView(
            statusOverlay,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.CENTER
            )
        )
        frame.addView(
            hudView,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP or Gravity.END
            ).apply { topMargin = dp(8); rightMargin = dp(8) }
        )
        frameRoot = frame
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(frame)
        enterImmersive()   // F4b: fullscreen ABSOLUTO en el estado de video
        showStatus("viewer v$BUILD — conectando a $url")

        // F5: TODA la pantalla reenvia trazos al S25 (S Pen con presion,
        // multi-touch, hover). El HUD se alterna desde la esquina sup-derecha
        // (zona del propio HUD, consumida localmente) o con el boton del S Pen.
        val penFwd = PenForwarder(
            view = surfaceView,
            streamW = { streamW },
            streamH = { streamH },
            send = { json -> wsClient?.sendInput(json) },
            onHudToggle = {
                hudVisible = !hudVisible
                runOnUiThread { hud?.visibility = if (hudVisible) View.VISIBLE else View.GONE }
            }
        )
        surfaceView.setOnTouchListener(penFwd)
        surfaceView.setOnGenericMotionListener { _, ev -> penFwd.onGeneric(ev) }

        // F4b: leer el hardware REAL del receptor y armar el cap.
        val capReport = DeviceCapabilities(this).read()
        val cap = StreamProfile.buildCap(capReport)
        logMeasure("cap: panel ${capReport.panelW}x${capReport.panelH}@${capReport.refreshExact} " +
            "decoder=${capReport.decoderName} techo=${capReport.decoderBitrateMax / 1_000_000}M " +
            "link=${capReport.linkMbps}Mbps -> cap w=${cap.w} h=${cap.h} fps=${cap.fps} " +
            "bitrate_max=${cap.bitrateMax}")

        surfaceView.holder.setKeepScreenOn(true)
        surfaceView.holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(holder: SurfaceHolder) {
                wsClient?.close()
                wsClient = null
                decoder?.release()
                decoder = null
                decoder = VideoDecoder(
                    holder.surface,
                    { msg -> showStatus(msg) },
                    { runOnUiThread {
                        overlay?.visibility = View.GONE
                        backBtn?.visibility = View.GONE
                        Toast.makeText(this@MainActivity, "WTM: video activo", Toast.LENGTH_SHORT).show()
                        startHudLoop()
                    } },
                    { wsClient?.requestKey() }
                )
                wsClient = WsClient(
                    baseUrl = url,
                    viewerToken = token,
                    onConfig = { codec, width, height, csd ->
                        streamW = width    // F5: mapa de tablet -> pantalla S25
                        streamH = height
                        showStatus("stream: $codec ${width}x${height}")
                        decoder?.configure(codec, width, height, csd)
                    },
                    onFrame = { data, pts -> decoder?.feed(data, pts) },
                    onStatus = { msg -> showStatus(msg) },
                    // F4b: veredicto del cap_ack -> HUD. Jamas verde falso.
                    onCapAck = { json ->
                        val ack = StreamProfile.parseCapAck(json)
                        capHudLine = StreamProfile.hudLine(ack)
                        logMeasure("cap_ack: applied=${ack.applied} " +
                            "profile=${ack.w}x${ack.h}@${ack.fps} ${ack.bitrate}bps " +
                            "reason=${ack.reason}")
                        runOnUiThread {
                            hud?.setTextColor(
                                if (ack.applied && ack.w > 0) Color.parseColor("#7FFF7F")
                                else Color.parseColor("#FF6A6A")
                            )
                        }
                    }
                )
                // F4b: el cap del hardware real se fija ANTES de connect().
                wsClient?.setCap(StreamProfile.capJson(cap))
                wsClient?.connect()
            }

            override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {}
            override fun surfaceDestroyed(holder: SurfaceHolder) {
                hudRunning = false
                decoder?.release()
                decoder = null
                persistLog()   // asegurar que las ultimas mediciones lleguen a disco
            }
        })
    }

    /**
     * Linea de medicion -> log persistido, SIN tocar la UI ni el
     * pipeline de video. Persistencia en disco cada 5 lineas (~5 s).
     */
    private fun logMeasure(msg: String) {
        val line = "[${(System.currentTimeMillis() - t0) / 1000} s] $msg"
        synchronized(logBuf) {
            logBuf.append(line).append('\n')
            val lines = logBuf.toString().split('\n')
            if (lines.size > 800) {
                val keep = lines.takeLast(800).joinToString("\n")
                logBuf.setLength(0)
                logBuf.append(keep)
            }
        }
        measurePersist++
        if (measurePersist % 5 == 0) persistLog()
    }

    /** Refresca el HUD 2 veces/seg; cada ~1 s la medicion va al log. */
    private fun startHudLoop() {
        if (hudRunning) return
        hudRunning = true
        lastRendered = 0L
        lastRenderedAt = System.currentTimeMillis()
        Thread {
            var tick = 0
            while (hudRunning) {
                Thread.sleep(500)
                if (!hudRunning) break
                val d = decoder?.stats() ?: continue
                val ws = wsClient
                val now = System.currentTimeMillis()
                val dt = (now - lastRenderedAt) / 1000.0
                val fps = if (dt > 0) (d.rendered - lastRendered) / dt else 0.0
                lastRendered = d.rendered
                lastRenderedAt = now
                val lat = ws?.latencyAvg() ?: 0.0
                val rtt = ws?.rttMs() ?: -1.0
                val txt = String.format(
                    "%.0f fps  lat %.1f ms  dec %.1f ms  rtt %.0f ms | %s | render %d  rdrop %d  qdrop %d  cola %d\n%s",
                    fps, lat, d.decodeMs, rtt, d.decoder, d.rendered, d.renderDrops, d.queueDrops, d.queueDepth,
                    capHudLine   // F4b: linea de perfil (verde=aplicado y verificado)
                )
                tick++
                if (tick % 2 == 1) logMeasure("hud: $txt")
                runOnUiThread { hud?.text = txt }
            }
        }.apply { isDaemon = true }.start()
    }

    /**
     * Log interno persistido + estado en pantalla SOLO mientras no hay
     * video. "error:" corta la sesion y muestra el boton Volver.
     */
    private fun showStatus(msg: String) {
        val line = "[${(System.currentTimeMillis() - t0) / 1000} s] $msg"
        synchronized(logBuf) {
            logBuf.append(line).append('\n')
            val lines = logBuf.toString().split('\n')
            if (lines.size > 800) {
                val keep = lines.takeLast(800).joinToString("\n")
                logBuf.setLength(0)
                logBuf.append(keep)
            }
        }
        persistLog()
        runOnUiThread {
            overlay?.let { tv ->
                if (tv.visibility != View.GONE || msg.startsWith("error:")) {
                    tv.visibility = View.VISIBLE
                    tv.text = msg
                }
            }
            if (msg.startsWith("error:")) {
                wsClient?.close()
                decoder?.release()
                showBackButton()
            }
        }
    }

    /** Boton Volver sobre el mensaje de error. */
    private fun showBackButton() {
        if (backBtn != null) return
        overlay?.visibility = View.VISIBLE
        val b = Button(this).apply { text = "Volver" }
        b.setOnClickListener {
            backBtn = null
            frameRoot = null
            buildConfigUI("")
        }
        backBtn = b
        frameRoot?.addView(
            b,
            FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM or Gravity.CENTER_HORIZONTAL
            ).apply { bottomMargin = dp(24) }
        )
    }

    override fun onPause() { persistLog(); super.onPause() }
    override fun onStop() { persistLog(); super.onStop() }
    override fun onDestroy() { persistLog(); super.onDestroy() }

    private fun dp(v: Int): Int =
        (resources.displayMetrics.density * v).toInt()
}
