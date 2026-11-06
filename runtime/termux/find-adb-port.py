#!/usr/bin/env python3
"""Escáner RÁPIDO del puerto adb (depuración inalámbrica) usando asyncio.
Primero prueba los puertos prioritarios (5555 y el último guardado), luego escanea
el rango completo con sockets no bloqueantes. Distingue adbd (responde CNXN/AUTH)."""
import asyncio, socket, sys, os

CNXN = b'CNXN\x00\x00\x00\x01\x00\x10\x00\x00\x07\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00'

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

def prio_ports():
    ps = [5555]
    cfg = os.path.expanduser('~/.config/rd-adb-port')
    try:
        ps.append(int(open(cfg).read().strip()))
    except Exception:
        pass
    return ps

async def main():
    host = sys.argv[1] if len(sys.argv) > 1 else '127.0.0.1'
    lo, hi = 32000, 48000
    if len(sys.argv) >= 4:
        lo, hi = int(sys.argv[2]), int(sys.argv[3])
    # 1) puertos prioritarios (instantáneo)
    for p in prio_ports():
        if await is_adb(host, p):
            print(p); return
    # 2) escaneo masivo rápido
    sem = asyncio.Semaphore(400)
    async def bounded(p):
        async with sem:
            return await is_adb(host, p)
    tasks = [asyncio.create_task(bounded(p)) for p in range(lo, hi + 1)]
    for t in asyncio.as_completed(tasks):
        r = await t
        if r:
            print(r)
            for t2 in tasks:
                t2.cancel()
            return

if __name__ == '__main__':
    asyncio.run(main())
