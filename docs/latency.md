# Latencia de la sesión S Pen — medición y plan

Estado: **primera medición instrumentada el 2026-11-25** en el par S25
(SM-S931B) + Galaxy Tab S6 Lite (SM-P613), hotspot del S25, perfil
2000x1200@60 y 35 Mbps. Es una medición de ingeniería, no una certificación.

## 1. Qué mide el HUD

El viewer muestra:

```
60 fps  lat 4.4 ms  dec 13.2 ms  rtt 4 ms | OMX.qcom.video.decoder.hevc | render 264  rdrop 0  qdrop 0  cola 0
perfil: 2000x1200@60 @ 35 Mbps — aplicado y verificado
```

| Métrica | Definición | Incluye | No incluye |
|---|---|---|---|
| `lat` | PTS del frame scrcpy → llegada al viewer | captura/encode, relay, red, TLS | decodificación, render, scanout |
| `dec` | llegada al decoder → `releaseOutputBuffer(render=true)` | cola del decoder, decode HW, submit | scanout del panel (0–16.7 ms a 60 Hz) |
| `rtt` | mejor RTT del heartbeat WS | ida/vuelta de red + relay | — |
| `cola` | frames en la cola de entrada del decoder | — | — |
| `rdrop` | frames descartados al renderizar | — | — |

`lat` usa el offset de reloj estimado con las muestras `sync` (mejor RTT), no
una simple resta de relojes. `dec` es local al tablet: no depende de relojes
entre dispositivos.

**No medido todavía**: el tramo táctil→WS del tablet, el dibujo de la app
antes de que scrcpy capture, y el scanout del panel. Por eso el total
`lat + dec` es una **cota inferior** del pen-to-photon, no el valor final.

## 2. Primera medición

Condiciones: PenProbe en el display virtual, HUD visible, trazo de 3 s
inyectado en la tablet con `input stylus swipe 300 300 1700 900 3000`
(120 Hz de muestras, presión 1.0). Captura durante el movimiento.

| Escena | fps | `lat` | `dec` | rtt | rdrop | qdrop | cola |
|---|---|---|---|---|---|---|---|
| Reposo | 0 | 4.2 ms | 19.0 ms | 6 ms | 0 | 0 | 0 |
| Trazo 3 s | **60** | **4.4 ms** | **13.2 ms** | **4 ms** | **0** | **0** | **0** |

- `lat + dec` ≈ **17.6 ms** durante el trazo (captura→render submit).
- Añadiendo el scanout (0–16.7 ms) la estimación queda en **~18–34 ms**.
- La cola del decoder está en 0 y no hay drops a 60 fps con 35 Mbps, así que
  la cota `QUEUE_MAX=8` (133 ms) **no** está aportando latencia en este caso.
- El `dec` en reposo (19 ms) incluye el último frame renderizado; es menos
  representativo que el valor en movimiento.

## 3. Qué se aplicó ya

- `SurfaceView` + `releaseOutputBuffer(index, true)` (cero copias).
- `tcpNoDelay = true` en el socket del viewer (Nagle off para el input).
- `KEY_LOW_LATENCY=1` **solo si** el codec declara `FEATURE_LowLatency`
  (nunca rompe `configure`); el decoder activo es
  `OMX.qcom.video.decoder.hevc`.
- Métrica `dec` por frame (media de los últimos 40) y `lat` corregida por
  `sync`.
- Decoder con cola acotada y descarte del delta más viejo + petición de IDR.

## 4. Backlog priorizado (medir antes de cambiar)

1. **A/B de `KEY_LOW_LATENCY`**: el hint se aplicó en el mismo build que la
   métrica `dec`, así que no hay un "antes" comparable. Construir una variante
   sin el hint y comparar `dec` en el mismo trazo. Si no hay diferencia,
   retirarlo para reducir superficie.
2. **`QUEUE_MAX` 8 → 4**: con `cola=0` y 0 drops en el hotspot actual, bajar
   la cota solo cambia el peor caso (ráfagas). Medir `qdrop`/`cola` con el
   trazo y con una escena de alto movimiento; aceptar solo si no degrada.
3. **Encoder**: comprobar si el encoder HEVC del S25 usa B-frames/reorder y
   si scrcpy permite `video_codec_options` (p. ej. `bitrate-mode`). Medir
   `lat` y su varianza, no solo la media. `IFRAME_INTERVAL=2` ya es
   configurable y reduce picos de bitrate; no cambia el estado estacionario.
4. **Red**: el `rtt` de 4–6 ms es bueno. Verificar que el tablet no esté en
   power-save de Wi-Fi y que el canal del hotspot no tenga interferencia;
   medir jitter, no solo RTT medio.
5. **Input táctil→relay**: añadir un timestamp de envío a los mensajes de
   `PenForwarder` y registrarlo en el relay. Solo si el video no domina el
   presupuesto.
6. **Ground truth**: cámara de alta velocidad o fotodiodo para medir
   pen-to-photon real (incluye scanout y dibujo de la app). Sin eso, no
   declarar una cifra final.

## 5. Objetivo

Objetivo de ingeniería: **pen-to-photon < 50 ms** de forma sostenida en el
hotspot, con 0 drops y jitter bajo. La cota inferior medida (~18–34 ms) deja
margen; la cifra definitiva requiere el ground truth del punto 6.

## 6. Reproducir

```sh
# 1. HUD visible: tocar la esquina superior derecha de la tablet
adb -s <tablet> shell input tap 1950 40

# 2. Trazo de 3 s (stylus) y captura en movimiento
adb -s <tablet> shell input stylus swipe 300 300 1700 900 3000 &
sleep 1.6
adb -s <tablet> exec-out screencap -p > hud.png

# 3. Alternativa sin tocar la tablet: PenProbe + trazo sintetico UHID
#    (PenTest widget o python3 ~/.config/wtm/bin/pen_test.py)
```

El HUD también se registra en `last_console` (prefs del viewer) como
`hud: ...`, útil para series largas sin capturas.
