import java.lang.reflect.Method;

/**
 * InputFocus — fija el display al que Android dibuja el icono del puntero,
 * con la API oculta:
 *   IInputManager.setDisplayIdForPointerIcon(int)
 * Lee el valor actual, lo cambia y lo relee para verificar (igual que el
 * helper histórico; docs/bluetooth-mouse-routing.md §2).
 *
 * Corre vía
 *   CLASSPATH=<jar> app_process / InputFocus <displayId|0>
 *
 * Con displayId=0 devuelve el icono del puntero al display físico: lo usa
 * stop-dex.sh para no dejar el puntero clavado a un display virtual muerto.
 *
 * Salida estructurada:
 *   stdout "FOCUS_OK <antes> -> <después>"  exit 0
 *   stderr "FOCUS_API_MISSING ..."          exit 3
 *   stderr "FOCUS_FAIL ..."                  exit 1
 */
public class InputFocus {
    public static void main(String[] args) {
        if (args.length != 1) {
            System.err.println("uso: InputFocus <displayId|0>");
            System.exit(2);
        }
        int displayId;
        try {
            displayId = Integer.parseInt(args[0].trim());
        } catch (NumberFormatException e) {
            System.err.println("uso: InputFocus <displayId|0>  (entero)");
            System.exit(2);
            return;
        }
        try {
            Class<?> stub = Class.forName("android.hardware.input.IInputManager$Stub");
            Object binder = Class.forName("android.os.ServiceManager")
                    .getMethod("getService", String.class)
                    .invoke(null, "input");
            Object input = stub
                    .getMethod("asInterface", Class.forName("android.os.IBinder"))
                    .invoke(null, binder);
            Method get = input.getClass().getMethod("getDisplayIdForPointerIcon");
            Method set = input.getClass().getMethod("setDisplayIdForPointerIcon", int.class);
            Object before = get.invoke(input);
            set.invoke(input, displayId);
            Object after = get.invoke(input);
            System.out.println("FOCUS_OK " + before + " -> " + after);
        } catch (NoSuchMethodException e) {
            System.err.println("FOCUS_API_MISSING " + e);
            System.exit(3);
        } catch (Exception e) {
            System.err.println("FOCUS_FAIL " + e);
            System.exit(1);
        }
    }
}
