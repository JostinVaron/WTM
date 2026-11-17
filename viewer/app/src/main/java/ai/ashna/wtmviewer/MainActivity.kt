package ai.ashna.wtmviewer

import android.app.Activity
import android.graphics.Color
import android.graphics.Typeface
import android.os.Bundle
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
 * - La app pide SOLO: URL del relay + token viewer
 * - La app hace login sola (POST /api/auth) y recibe la cookie
 * - El video va en SurfaceView a pantalla completa, con un overlay de
 *   estado SIEMPRE visible encima (lecciones del primer APK: el swap de
 *   setContentView escondia el estado tras la pantalla negra y ante datos
 *   malos el usuario no veia nada).
 * - Ante error: vuelve a la pantalla de config mostrando el motivo.
 */
class MainActivity : Activity() {

    private lateinit var prefs: SharedPreferences
    private var wsClient: WsClient? = null
    private var decoder: VideoDecoder? = null
    private var overlay: TextView? = null

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
        val label = TextView(this).apply { text = "WTM Viewer — conectar al relay" }
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
        // video abajo, estado SIEMPRE visible arriba
        val surfaceView = SurfaceView(this)
        val statusOverlay = TextView(this).apply {
            text = "conectando…"
            setTextColor(Color.parseColor("#7FFF7F"))
            typeface = Typeface.MONOSPACE
            setTextSize(TypedValue.COMPLEX_UNIT_SP, 13f)
            setBackgroundColor(Color.argb(0x88, 0x00, 0x00, 0x00))
            val pad = dp(8)
            setPadding(pad, pad, pad, pad)
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
                Gravity.TOP or Gravity.START
            ).apply { setMargins(dp(6), dp(6), 0, 0) }
        )
        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(frame)

        surfaceView.holder.setKeepScreenOn(true)
        surfaceView.holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(holder: SurfaceHolder) {
                decoder = VideoDecoder(holder.surface) { msg -> showStatus(msg) }
                wsClient = WsClient(
                    baseUrl = url,
                    viewerToken = token,
                    onConfig = { _, width, height, csd ->
                        decoder?.configure("hevc", width, height, csd)
                        showStatus("decodificando ${width}x${height}")
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

    /** Estado al overlay; si es error, vuelve a config con el motivo. */
    private fun showStatus(msg: String) {
        runOnUiThread {
            overlay?.text = msg
            if (msg.startsWith("error:")) {
                wsClient?.close()
                decoder?.release()
                buildConfigUI("ERROR: ${msg.removePrefix("error: ").removePrefix("error:")}")
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
