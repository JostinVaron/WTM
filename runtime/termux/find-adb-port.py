#!/usr/bin/env python3
"""Escáner RÁPIDO del puerto adb (depuración inalámbrica) usando asyncio.

Android moderno: el adbd de depuración inalámbrica escucha en la IP de la red
Wi-Fi, no en 127.0.0.1. Este escáner prueba varios hosts candidatos:

  1. 127.0.0.1 (por si el dispositivo expone adbd en loopback)
  2. la IP local detectada vía socket UDP (mismo truco que install.sh; sin
     netlink, que está bloqueado para apps en Android moderno)
  3. la IP guardada en ~/.config/rd-adb-port (host:port de la última corrida)

Primero prueba los puertos prioritarios (5555 y el último guardado), luego
escanea el rango completo con sockets no bloqueantes. Distingue adbd
(responde CNXN/AUTH). Imprime "host:port" por cada adbd encontrado y guarda
host:port en ~/.config/rd-adb-port para que la próxima corrida sea instantánea.
"""
import asyncio, socket, sys, os

CNXN = b'CNXN\x00\x00\x00\x01\x00\x10\x00\x00\x07\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'

async def is_adb(host, port):
    try:
        r, w = await asyncio.open_connection(host, port)
        w.write(CNXN)
        await w.drain()
        try:
            data = await asyncio.wait_for(r.read(24), timeout=1.0)
        except asyncio.TimeoutError:
            data = b''
        w.close()
        return port if data[:4] in (b'CNXN', b'AUTH') else None
    except Exception:
        return None

def local_ip():
    """IP local del teléfono sin netlink: socket UDP que no envía paquetes."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip if ip and not ip.startswith("0.") and ip != "127.0.0.1" else None
    except Exception:
        return None

def saved_entry():
    """host:port guardado de la última corrida exitosa, si existe."""
    cfg = os.path.expanduser('~/.config/rd-adb-port')
    try:
        return open(cfg).read().strip() or None
    except Exception:
        return None

def candidate_hosts():
    """Hosts donde buscar adbd, en orden de prioridad."""
    hosts = ["127.0.0.1"]
    ip = local_ip()
    if ip:
        hosts.append(ip)
    saved = saved_entry()
    if saved and ":" in saved:
        h = saved.rsplit(":", 1)[0]
        if h not in hosts:
            hosts.append(h)
    return hosts

def save_entry(host, port):
    path = os.path.expanduser('~/.config/rd-adb-port')
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(f"{host}:{port}\n")
    except Exception:
        pass

async def main():
    lo, hi = 32000, 48000
    if len(sys.argv) >= 3:
        lo, hi = int(sys.argv[1]), int(sys.argv[2])
    hosts = candidate_hosts()
    # 1) puertos prioritarios (instantáneo): 5555 y el último guardado en cada host
    saved = saved_entry()
    prio_ports = [5555]
    if saved and ":" in saved:
        try:
            prio_ports.append(int(saved.rsplit(":", 1)[1]))
        except ValueError:
            pass
    for host in hosts:
        for p in prio_ports:
            if await is_adb(host, p):
                print(f"{host}:{p}")
                save_entry(host, p)
                return
    # 2) escaneo masivo rápido en todos los hosts candidatos
    sem = asyncio.Semaphore(400)
    async def bounded(host, p):
        async with sem:
            return (host, p) if await is_adb(host, p) else None
    tasks = [asyncio.create_task(bounded(h, p)) for h in hosts for p in range(lo, hi + 1)]
    for t in asyncio.as_completed(tasks):
        r = await t
        if r:
            host, port = r
            print(f"{host}:{port}")
            save_entry(host, port)
            for t2 in tasks:
                t2.cancel()
            return

if __name__ == '__main__':
    asyncio.run(main())
