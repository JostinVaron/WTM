package ai.ashna.wtmviewer

import android.app.Activity
import android.content.Intent
import android.graphics.Color
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
 * Produccion (post diag10): la consola de diagnostico YA NO se muestra
 * sobre el video — desaparece sola en cuanto el primer frame llega al
 * surface (la franja grisacea que se veia arriba era ese overlay). El
 * pipeline Annex-B (el fix que hizo funcionar el video: length-prefixed
 * -> start codes, csd-0 como VPS/SPS/PPS con start codes, params in-band
 * en keyframes) vive en VideoDecoder.kt.
 *
 * Se conserva del arsenal de diagnostico SOLO lo util para el usuario:
 * estado de conexion en pantalla hasta que hay video, errores con boton
 * Volver, crash-report al relay, y "Compartir log" en la pantalla de
 * configuracion (el log completo sigue persistiendose a disco).
 */
class MainActivity : Activity() {

    companion object {
        const val BUILD = "1.0"
    }

    private lateinit var prefs: SharedPreferences
    private var wsClient: WsClient? = null
    private var decoder: VideoDecoder? = null
    private var overlay: TextView? = null
    private var frameRoot: FrameLayout? = null
    private var backBtn: Button? = null
    private val logBuf = StringBuilder()
    private val t0 = System.currentTimeMillis()
    private var prevHandler: Thread.UncaughtExceptionHandler? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("wtm", Context.MODE_PRIVATE)
        prevHandler = Thread.getDefaultUncaughtExceptionHandler()
        Thread.setDefaultUncaughtExceptionHandler { t, e ->
            persistCrash(t, e)
            prevHandler?.uncaughtException(t, e)
        }
        buildConfigUI("")
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
    }

    private fun startStream(url: String, token: String) {
        synchronized(logBuf) { logBuf.setLength(0) }
        persistLog()
        backBtn = null

        val surfaceView = SurfaceView(this)
        // Estado de conexion: MINIMAL y desaparece en cuanto hay video.
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
        frameRoot = frame
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(frame)
        showStatus("viewer v$BUILD — conectando a $url")

        surfaceView.holder.setKeepScreenOn(true)
        surfaceView.holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(holder: SurfaceHolder) {
                // Cerrar la sesion previa ANTES de crear otra: el server es
                // single-session y la conexion vieja recibia el close 4000.
                wsClient?.close()
                wsClient = null
                decoder?.release()
                decoder = null
                decoder = VideoDecoder(
                    holder.surface,
                    { msg -> showStatus(msg) },
                    { runOnUiThread {
                        overlay?.visibility = View.GONE   // video fluye: overlay fuera
                        backBtn?.visibility = View.GONE
                        Toast.makeText(this@MainActivity, "WTM: video activo", Toast.LENGTH_SHORT).show()
                    } },
                    { wsClient?.requestKey() }
                )
                wsClient = WsClient(
                    baseUrl = url,
                    viewerToken = token,
                    onConfig = { codec, width, height, csd ->
                        showStatus("stream: $codec ${width}x${height}")
                        decoder?.configure(codec, width, height, csd)
                    },
                    onFrame = { data, pts -> decoder?.feed(data, pts) },
                    onStatus = { msg -> showStatus(msg) }
                )
                wsClient?.connect()
            }

            override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {}
            override fun surfaceDestroyed(holder: SurfaceHolder) {
                decoder?.release()
                decoder = null
            }
        })
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
            if (lines.size > 120) {
                val keep = lines.takeLast(120).joinToString("\n")
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

    override fun onPause() { super.onPause() }
    override fun onStop() { super.onStop() }
    override fun onDestroy() { super.onDestroy() }

    private fun dp(v: Int): Int =
        (resources.displayMetrics.density * v).toInt()
}
