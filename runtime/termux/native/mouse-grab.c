/* mouse-grab.c — toma posesión EXCLUSIVA (EVIOCGRAB) del nodo de mouse
 * /dev/input/eventN y reenvía cada evento como registro canónico de 8 bytes:
 *     type (u16 LE) | code (u16 LE) | value (s32 LE)
 *
 * Por qué existe: Android no permite asociar un periférico físico a un display
 * virtual, y copiar eventos (getevent) deja un "movimiento fantasma" en la
 * pantalla del teléfono. Con EVIOCGRAB el kernel entrega los eventos SOLO a
 * este proceso: Android deja de verlos por completo — no hay fantasma, ni
 * clics perdidos en el teléfono, ni la pantalla despierta sola.
 *
 * Ciclo de vida: mientras viva, el mouse físico solo controla el display
 * virtual (DeX). Al morir, el kernel libera el grab automáticamente y el
 * mouse vuelve a su comportamiento normal sin intervención.
 *
 * Seguridad de liberación (triple):
 *  - el consumidor (mouse-route.py) lo termina al detenerse el DeX;
 *  - si el consumidor muere primero, el heartbeat provoca SIGPIPE y este
 *    proceso muere al instante (fd cerrado = grab liberado);
 *  - si la conexión adb se corta, adbd mata este proceso remoto.
 *
 * Códigos de salida: 2 uso incorrecto; 1 no se pudo abrir el nodo (el mouse
 * está apagado/desconectado); 3 EVIOCGRAB denegado (permisos del dispositivo)
 * — el llamador cae entonces al modo legacy (getevent, con fantasma).
 *
 * Compilación (Termux): clang -O2 -static -o mouse-grab mouse-grab.c
 * Ejecución: adb shell /data/local/tmp/wtm-mouse-grab /dev/input/event13
 */
#include <errno.h>
#include <fcntl.h>
#include <linux/input.h>
#include <signal.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/select.h>
#include <unistd.h>

#define HEARTBEAT_TYPE 0xFFFF   /* tipo inválido: nunca colisiona con eventos reales */

static void emit(unsigned short type, unsigned short code, int value) {
    unsigned char rec[8];
    rec[0] = (unsigned char)(type & 0xff);
    rec[1] = (unsigned char)((type >> 8) & 0xff);
    rec[2] = (unsigned char)(code & 0xff);
    rec[3] = (unsigned char)((code >> 8) & 0xff);
    memcpy(rec + 4, &value, 4);
    if (write(STDOUT_FILENO, rec, 8) != 8) {
        /* el consumidor murió: salir cierra el fd y libera el grab */
        _exit(0);
    }
}

int main(int argc, char **argv) {
    if (argc != 2) {
        fprintf(stderr, "uso: mouse-grab /dev/input/eventN\n");
        return 2;
    }
    signal(SIGPIPE, SIG_DFL);   /* sin consumidor: morir al primer write */

    int fd = open(argv[1], O_RDWR);
    if (fd < 0) {
        fprintf(stderr, "no se pudo abrir %s: %s\n", argv[1], strerror(errno));
        return 1;
    }
    if (ioctl(fd, EVIOCGRAB, (void *)1) != 0) {
        fprintf(stderr, "EVIOCGRAB denegado en %s: %s\n", argv[1], strerror(errno));
        return 3;
    }
    fprintf(stderr, "grab activo en %s\n", argv[1]);

    for (;;) {
        fd_set rfds;
        FD_ZERO(&rfds);
        FD_SET(fd, &rfds);
        struct timeval tv = {0, 250000};
        int r = select(fd + 1, &rfds, NULL, NULL, &tv);
        if (r == 0) {
            emit(HEARTBEAT_TYPE, 0, 0);   /* mantiene vivo el pipe y detecta huérfanos */
            continue;
        }
        if (r < 0) {
            if (errno == EINTR) continue;
            break;
        }
        struct input_event ev;
        ssize_t n = read(fd, &ev, sizeof(ev));
        if (n == (ssize_t)sizeof(ev)) {
            emit(ev.type, ev.code, ev.value);
        } else if (n == 0) {
            break;   /* nodo desapareció (mouse desconectado) */
        } else if (n < 0 && errno != EINTR) {
            break;
        }
        /* lecturas parciales no ocurren en evdev: siempre eventos completos */
    }
    return 0;
}
