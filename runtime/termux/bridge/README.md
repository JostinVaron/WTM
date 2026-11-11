# WTM bridge (rama `bridge`)

Canal de diagnóstico remoto para WTM: despacha misiones de diagnostico al S25
desde el chat de IA, y recupera la evidencia real (outputs) al repositorio.
**Operativo solo en esta rama** — no se empaqueta en el tarball de releases y
CI no corre aqui (corre en `main` y PRs).

## Archivos

- `runtime/termux/bridge/agent.sh` — agente en Termux. Poll cada 15 s a
  `commands.sh` (rama `bridge`); si cambia y el commit es del autor
  autorizado, ejecuta la mision con timeout y sube el output.
- `runtime/termux/bridge/commands.sh` — cola de misiones (UNA activa, la
  ultima). La mision termina SIEMPRE con la linea `### BRIDGE MARKER ###`.
- `out/` (remoto) — evidencia: `last.txt` (ultima mision), `index.txt`
  (historia de misiones), `status.txt` (estado del agente).

## Despliegue (una vez, en Termux)

```sh
mkdir -p ~/.config/wtm/bridge && cd ~/.config/wtm/bridge
# traer el agente:
curl -L https://raw.githubusercontent.com/JostinVaron/WTM/bridge/runtime/termux/bridge/agent.sh -o agent.sh
chmod +x agent.sh
bash agent.sh   # mantener Termux en primer plano
```

Requisitos: `gh` autenticado, `adb` (pkg install android-tools), `timeout`
(coreutils). No requiere root ni modifica el runtime.

## Formato de una mision

```
MISSION-ID: W1-SMOKE
TIMEOUT: 300
<cuerpo bash - puede usar $WTM_DEV (device adb auto-detectado)>
### BRIDGE MARKER ###
```

## Comandos operativos (van como cuerpo, antes de la marca)

- `STOP_LOOP=yes` — el agente se detiene y publica estado `detenido`.

## Seguridad

- Solo misiones cuyo ultimo commit en `commands.sh` sea de `JostinVaron`.
- Sin tokens, IPs fijas ni secretos: el agente usa el `gh` del usuario y
  auto-detecta el dispositivo. El barrido fail-closed de secrets de CI
  aplica igual a esta rama si algun dia entra por PR a `main`.

## Limitaciones conocidas

- Doze: Termux en segundo plano congela el agente. Durante misiones,
  mantener Termux en primer plano (el wake-lock se toma solo mientras
  corre una mision).
- Latencia minima de 15 s por poll + duracion de la mision.
- La rama `bridge` es un canal auxiliar: la fuente de verdad del runtime
  sigue siendo `main`.
