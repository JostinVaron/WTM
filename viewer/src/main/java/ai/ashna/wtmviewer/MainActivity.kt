package ai.ashna.wtmviewer

import android.annotation.SuppressLint
import android.app.Activity
import android.os.Bundle
import android.view.SurfaceHolder
import android.view.SurfaceView
import android.view.View
import android.view.WindowManager
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Button
import android.content.Context
import android.content.SharedPreferences

/**
 * Stage 1 (issue #16): skeleton del viewer nativo.
 * - Pantalla de config simple (URL del relay + cookie de sesión viewer)
 * - SurfaceView a pantalla completa cuando conecta
 * La lógica pesada vive en WsClient y VideoDecoder.
 */
class MainActivity : Activity() {

    private lateinit var prefs: SharedPreferences
    private var wsClient: WsClient? = null
    private var decoder: VideoDecoder? = null

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = getSharedPreferences("wtm", Context.MODE_PRIVATE)
        buildConfigUI()
    }

    private fun buildConfigUI() {
        val pad = (resources.displayMetrics.density * 12).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }
        val label = TextView(this).apply { text = "WTM Viewer — conectar al relay" }
        val urlInput = EditText(this).apply {
            hint = "https://192.168.x.x:8443"
            setText(prefs.getString("base_url", "https://192.168.43.1:8443"))
        }
        val cookieInput = EditText(this).apply {
            hint = "Cookie de sesión viewer (pega de /login en Chrome)"
            setText(prefs.getString("session_cookie", ""))
        }
        val status = TextView(this).apply { text = "Estado: desconectado" }
        val connectBtn = Button(this).apply { text = "Conectar" }

        connectBtn.setOnClickListener {
            val url = urlInput.text.toString().trim()
            val cookie = cookieInput.text.toString().trim()
            prefs.edit().putString("base_url", url).putString("session_cookie", cookie).apply()
            startStream(url, cookie, status)
        }

        root.addView(label)
        root.addView(urlInput)
        root.addView(cookieInput)
        root.addView(connectBtn)
        root.addView(status)
        setContentView(root)
    }

    private fun startStream(url: String, cookie: String, status: TextView) {
        status.text = "Estado: conectando…"
        val surfaceView = SurfaceView(this)
        val holder = surfaceView.holder
        holder.setKeepScreenOn(true)

        holder.addCallback(object : SurfaceHolder.Callback {
            override fun surfaceCreated(holder: SurfaceHolder) {
                runOnUiThread { status.text = "Estado: superficie lista" }
                decoder = VideoDecoder(holder.surface) { msg ->
                    runOnUiThread { status.text = "Estado: $msg" }
                }
                wsClient = WsClient(
                    baseUrl = url,
                    sessionCookie = cookie,
                    onConfig = { codec, width, height, csd ->
                        decoder?.configure(codec, width, height, csd)
                        runOnUiThread { status.text = "Estado: decodificando ${width}x${height}" }
                    },
                    onFrame = { data, pts ->
                        decoder?.feed(data, pts)
                    },
                    onStatus = { msg ->
                        runOnUiThread { status.text = "Estado: $msg" }
                    }
                )
                wsClient?.connect()
            }

            override fun surfaceChanged(holder: SurfaceHolder, format: Int, width: Int, height: Int) {}
            override fun surfaceDestroyed(holder: SurfaceHolder) {
                decoder?.release()
                decoder = null
            }
        })

        window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
        setContentView(surfaceView)
    }

    override fun onDestroy() {
        wsClient?.close()
        decoder?.release()
        super.onDestroy()
    }
}
