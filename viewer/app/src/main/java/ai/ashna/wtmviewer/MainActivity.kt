package ai.ashna.wtmviewer

import android.app.Activity
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
import android.text.method.ScrollingMovementMethod
import android.util.TypedValue
import android.view.Gravity
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.ViewGroup
import android.view.WindowManager
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.LinearLayout
import android.widget.TextView
import android.content.Context
import android.content.SharedPreferences

/**
 * Stage 1 (issue #16): viewer nativo.
 *
 * diag3: consola multilinea en pantalla. Las versiones anteriores tenian un
 * overlay de UNA linea que se sobreescribia a si mismo: el silencio era
 * indistinguible de varias fallas distintas, y "decodificando" se pintaba al
 * recibir la config — antes de que existiera un solo frame — sin probar nada.
 * Ahora cada evento queda visible con timestamp, y los eventos clave se
 * reportan tambien al POST /log del relay (consola del S25).
 */
class MainActivity : Activity() {

    companion object {
        const val BUILD = "diag3"
    }

    private lateinit var prefs: SharedPreferences
    private var wsClient: WsClient? = null
    private var decoder: VideoDecoder? = null
    private var overlay: TextView? = null
    private val logBuf = StringBuilder()
    private val t0 = System.currentTimeMillis()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("wtm", Context.MODE_PRIVATE)
        buildConfigUI("")
    }

    private fun buildConfigUI(initialStatus: String) {
        val pad = dp(12)
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }
        val label = TextView(this).apply { text = "WTM Viewer build=$BUILD — conectar al relay" }
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
            setTextColor(if (initialStatus.startsWith("ERROR")) Color.parseColor("#FF6A6A") else Color.WHITE)
        }
        val connectBtn = Button(this).apply { text = "Conectar" }

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

        val surfaceView = SurfaceView(this)
        val statusOverlay = TextView(this).apply {
            text = "viewer build=$BUILD"
            setTextColor(Color.parseColor("#7FFF7F"))
            typeface = Typeface.MONOSPACE
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 11f)
            setBackgroundColor(Color.argb(0xCC, 0x00, 0x00, 0x00))
            val pad = dp(6)
            setPadding(pad, pad, pad, pad)
            maxLines = 18
            movementMethod = ScrollingMovementMethod()
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
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP
            )
        )
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(frame)
        showStatus("viewer build=$BUILD")
        showStatus("conectando a $url")

        surfaceView.holder.setKeepScreenOn(true)
        surfaceView.holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(holder: SurfaceHolder) {
                decoder = VideoDecoder(holder.surface) { msg -> showStatus(msg) }
                wsClient = WsClient(
                    baseUrl = url,
                    viewerToken = token,
                    // config del server TAL CUAL: el codec queda registrado en
                    // la consola — avc1 (H.264) o hvc1 (HEVC), con evidencia
                    onConfig = { codec, width, height, csd ->
                        showStatus("config del server: codec=$codec ${width}x${height} csd=${csd.size}B")
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

    /** Consola: cada evento appendea con timestamp; error → volver a config. */
    private fun showStatus(msg: String) {
        val secs = (System.currentTimeMillis() - t0) / 1000
        val line = "[" + secs + "s] " + msg
        val text: String
        synchronized(logBuf) {
            logBuf.append(line).append('\n')
            val lines = logBuf.toString().split('\n')
            if (lines.size > 120) {
                val keep = lines.takeLast(120).joinToString("\n")
                logBuf.setLength(0)
                logBuf.append(keep)
            }
            text = logBuf.toString()
        }
        runOnUiThread {
            overlay?.let { tv ->
                tv.text = text
                tv.post { tv.scrollTo(0, tv.lineCount * tv.lineHeight) }
            }
            if (msg.startsWith("error:")) {
                wsClient?.close()
                decoder?.release()
                buildConfigUI("ERROR: " + msg.removePrefix("error: ").removePrefix("error:"))
            }
        }
    }

    private fun dp(v: Int): Int =
        (resources.displayMetrics.density * v).toInt()

    override fun onDestroy() {
        wsClient?.close()
        decoder?.release()
        super.onDestroy()
    }
}
