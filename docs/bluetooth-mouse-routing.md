# Bluetooth mouse routing to the virtual display (historical subsystem)

**Status: documentation of a removed subsystem.** The code below was recovered
from the historical work copy on the development machine
(`remote-display/`, last modified 2026-11-04) and from the deployed prototype
scripts. It was **never published** in this repository, and the hardened
runtime (`runtime/termux/`) intentionally removed the mouse-routing control.
This document records how it worked, why it was removed, and what a
reimplementation would need.

## 1. Purpose

When the scrcpy virtual display ("DeX") is active on the S25, a Bluetooth
mouse paired to the **phone** normally drives the cursor on the phone's
physical screen (display 0), not on the virtual display. The removed
subsystem re-associated the physical mouse with the virtual display so that:

- the real Samsung cursor (rendered by Android inside the virtual display)
  moved inside the DeX desktop seen on the tablet;
- the cursor arrived visually through the video stream itself — no fake SVG
  cursor in the browser;
- Android itself re-targeted the input device, so there was no ghost input
  on the phone screen.

## 2. Mechanism (verified from sources)

Two hidden Android APIs, called through `app_process` (the same launcher
mechanism scrcpy-server uses), implemented the association:

1. **`IInputManager.addUniqueIdAssociationByDescriptor(String descriptor, String uniqueId)`**
   (Android 14+ hidden API) — binds the input device descriptor to the
   virtual display's unique ID, so Android delivers the physical device's
   events to that display.
2. **`IInputManager.setDisplayIdForPointerIcon(int displayId)`** (hidden API)
   — pins the pointer icon (cursor) to the virtual display's numeric
   display ID.

Both calls were invoked via reflection against the system `input` service
(`ServiceManager.getService("input")` → `IInputManager.Stub.asInterface`),
which avoids compile-time dependencies on hidden APIs.

Recovered sources:

- `InputAssoc.java` (1.3 KB): reflects over `IInputManager$Stub`, lists every
  method whose name contains "ssociate", then calls
  `addUniqueIdAssociationByDescriptor(descriptor, uniqueId)`, falling back to
  a logged `NoSuchMethodException` when the API is absent.
- `InputFocus.java` (1.2 KB): reads `getDisplayIdForPointerIcon()`, calls
  `setDisplayIdForPointerIcon(displayId)`, and re-reads the value to log the
  change.
- `route-input.sh` (2.4 KB): orchestrates the flow (see §3).

## 3. Orchestration flow (`route-input.sh`)

```text
1. Find the phone serial among adb devices by model (SM-S931B).
2. Read the virtual display's uniqueId from `dumpsys display`
   (pattern: uniqueId='virtual:…scrcpy…').
3. Read its numeric displayId (pattern: type=VIRTUAL … displayId=N).
4. Push jvm/inputassoc.zip to /data/local/tmp/inputassoc.jar (chmod 400 —
   Android 14+ rejects dex files with writable permission bits).
5. Run: CLASSPATH=$JAR app_process / InputAssoc '<DESCRIPTOR>' '<UNIQUE_ID>'
6. Push jvm/inputfocus.zip and run: InputFocus <DID>
7. Done: the physical mouse now drives the DeX desktop.
```

The descriptor used by the prototype was hard-coded as the default:

```text
f5820ed7fb974c9d1da7fb559da7d5301b9186fe   # BT5.2 Mouse (CURSOR)
```

It was overridable via the `MOUSE_DESCRIPTOR` environment variable. The
descriptor is device-pair specific: it comes from `dumpsys input` /
`/proc/bus/input/devices`-style enumeration of the paired Bluetooth mouse on
the phone, not from the tablet.

## 4. Where it was invoked

- `start-all.sh` (one-tap flow): after scrcpy-server and the bridge were up,
  it ran `./route-input.sh` up to 10 times, 1 s apart, tolerating failure.
- `watchdog.sh`: after a detected scrcpy-server death, it restarted scrcpy
  and re-ran `route-input.sh` (mouse re-association had to be re-applied
  after every server restart because the association is per-display and the
  display ID changes when the server restarts).
- `server.py` (old, unpublished version): the `route_mouse` control action
  ran `cd $HERE && ./route-input.sh >/dev/null 2>&1` through
  `create_subprocess_shell`; `start_dex` chained a `route-input.sh` call after
  launching scrcpy.
- `panel.html` (old, unpublished version): a "Enrutar mouse" button sent
  `{"action": "route_mouse"}` to `/api/control`.

## 5. Dependencies

| Dependency | Role |
|---|---|
| Termux `adb` | Device discovery, file push, shell commands |
| `dumpsys display` | Virtual display uniqueId + numeric ID |
| `app_process` | Launches the Java helpers with the system UID (shell) |
| Java helpers compiled to dex (R8/D8, `--min-api 26`) | Call the hidden `IInputManager` methods |
| Android 14+ (API 34+) | `addUniqueIdAssociationByDescriptor` availability |
| scrcpy virtual display already created | Association target |
| Reflection over `IInputManager$Stub` | No compile-time hidden API dependency |

Build notes from the recovered `jvm/build.sh`: sources were compiled with
`javac -source 8 -target 8` against an Android stubs jar (`android34.jar`)
and dexed with R8's D8 (`--min-api 26`), producing `classes.dex` inside a zip
deployed as `/data/local/tmp/*.jar` with mode `400`.

## 6. Why it was removed from the published runtime

1. **Security review requirement.** The published hardening removed shell
   construction from request handling and restricted `/api/control` to two
   fixed actions (`start_dex`, `stop_dex`). The old `route_mouse` action used
   `create_subprocess_shell`, which violates the "no shell string routing"
   rule adopted before publication.
2. **Hidden-API fragility.** `addUniqueIdAssociationByDescriptor` and
   `setDisplayIdForPointerIcon` are hidden, undocumented APIs. Availability
   is Android-version-dependent (Android 14+ observed), and the helper
   tolerates absence only by logging a `NoSuchMethodException` — it cannot
   guarantee behavior across devices or Android updates.
3. **Device-specific descriptor.** The default descriptor in `route-input.sh`
   belongs to one specific BT5.2 mouse paired to the S25. Publishing it as a
   default is misleading for other hardware; a reimplementation must discover
   the descriptor dynamically.
4. **Review surface.** Keeping the subsystem out of the published runtime
   reduced the review surface of the release candidate. The historical work
   copy was excluded from the repository on purpose; only this documentation
   is published.

## 7. Reimplementation guidance (for future contributors)

- Discover the mouse descriptor dynamically (enumerate input devices, filter
  by `SourceClass: pointer` / `isExternal` in `dumpsys input`), never
  hard-code it.
- Keep the helper read-only from the perspective of the server: a fixed
  allowlisted action that invokes a single script via
  `create_subprocess_exec` (no shell string interpolation), admin role only.
- Verify availability of the hidden APIs at runtime with reflection and
  report a structured result (associated / API missing / display missing)
  instead of the old best-effort logging.
- Re-apply the association after every scrcpy restart (the watchdog behavior
  above), because the virtual display ID changes.
- Test matrix: Android version, mouse vendor/model, scrcpy server version,
  whether `setDisplayIdForPointerIcon` exists (it can vary independently of
  `addUniqueIdAssociationByDescriptor`).

## 8. References inside this repository

- `docs/architecture.md` §5 — historical description of the helper
  ("input-manager internals … Android hidden APIs … experimental").
- `docs/dependencies.md` — "Bluetooth mouse association helper —
  Experimental Java/app_process helper in active scripts".
- `docs/runtime-release-candidate.md` — "Mouse-routing control removed
  (server no longer supports it)".

Recovery date: 2026-11-07. Sources recovered from the development machine's
`remote-display/` work copy; nothing was invented. The deployed prototype on
the phone still contains its own copies of these scripts, which this document
does not modify.
