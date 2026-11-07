import java.lang.reflect.Method;

/**
 * InputAssoc — asocia un dispositivo de entrada físico (por descriptor) a un
 * display virtual (por uniqueId) usando la API oculta de Android 14+:
 *   IInputManager.addUniqueIdAssociationByDescriptor(String, String)
 *
 * Reconstrucción del helper histórico del subsistema de enrutado de mouse
 * (docs/bluetooth-mouse-routing.md §2). Corre vía
 *   CLASSPATH=<jar> app_process / InputAssoc <descriptor> <uniqueId>
 * con UID shell (mismo mecanismo que scrcpy-server). Sin dependencias de
 * compilación contra APIs ocultas: todo por reflexión.
 *
 * Salida estructurada (guía de reimplementación §7):
 *   stdout "ASSOC_OK <descriptor> -> <uniqueId>"  exit 0  (asociado)
 *   stderr "ASSOC_API_MISSING ..."               exit 3  (API ausente)
 *   stderr "ASSOC_FAIL ..."                       exit 1  (otros errores)
 *   stderr "uso: ..."                             exit 2  (argumentos)
 */
public class InputAssoc {
    public static void main(String[] args) {
        if (args.length != 2) {
            System.err.println("uso: InputAssoc <descriptor> <uniqueId>");
            System.exit(2);
        }
        String descriptor = args[0];
        String uniqueId = args[1];
        try {
            Class<?> stub = Class.forName("android.hardware.input.IInputManager$Stub");
            Object binder = Class.forName("android.os.ServiceManager")
                    .getMethod("getService", String.class)
                    .invoke(null, "input");
            Object input = stub
                    .getMethod("asInterface", Class.forName("android.os.IBinder"))
                    .invoke(null, binder);
            // Diagnóstico de versión: enumerar los métodos "...ssociate..."
            // disponibles ayuda a detectar cambios de firma entre versiones de
            // Android (la API es oculta y puede variar).
            for (Method m : input.getClass().getMethods()) {
                if (m.getName().contains("ssociate")) {
                    System.err.println("disponible: " + m);
                }
            }
            Method call = input.getClass().getMethod(
                    "addUniqueIdAssociationByDescriptor", String.class, String.class);
            call.invoke(input, descriptor, uniqueId);
            System.out.println("ASSOC_OK " + descriptor + " -> " + uniqueId);
        } catch (NoSuchMethodException e) {
            System.err.println("ASSOC_API_MISSING " + e);
            System.exit(3);
        } catch (Exception e) {
            System.err.println("ASSOC_FAIL " + e);
            System.exit(1);
        }
    }
}
