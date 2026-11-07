#!/usr/bin/env bash
# jvm/build.sh — construye los jars dexeados de los helpers de asociación
# input->display (InputAssoc, InputFocus).
#
# Se ejecuta en una máquina con JDK y Android build-tools (d8) — Termux no
# tiene javac ni d8. Reproduce el build histórico documentado en
# docs/bluetooth-mouse-routing.md §5:
#   javac -source 8 -target 8 contra android.jar (API 34+)
#   d8 --min-api 26 -> classes.dex dentro de un zip
# Desplegado luego en el teléfono como /data/local/tmp/*.jar con chmod 400
# (Android 14+ rechaza dex con bits escribibles).
#
# Uso:
#   ANDROID_JAR=/ruta/a/android.jar D8=/ruta/a/d8 bash build.sh
set -euo pipefail

ANDROID_JAR="${ANDROID_JAR:?Define ANDROID_JAR con la ruta a android.jar del SDK (API 34+)}"
D8="${D8:?Define D8 con la ruta al binario d8 de Android build-tools}"
cd "$(dirname "$0")"

rm -rf build
mkdir -p build/classes
javac -source 8 -target 8 -cp "$ANDROID_JAR" -d build/classes InputAssoc.java InputFocus.java

for cls in InputAssoc InputFocus; do
    rm -rf "build/$cls"
    mkdir -p "build/$cls"
    # d8 exige --lib con el classpath de referencia para verificar contra android.jar
    "$D8" --min-api 26 --lib "$ANDROID_JAR" --output "build/$cls" "build/classes/$cls.class"
    (cd "build/$cls" && zip -q -X "${cls,,}.zip" classes.dex)
    mv "build/$cls/${cls,,}.zip" "./$(echo "$cls" | tr 'A-Z' 'a-z')... " 2>/dev/null || true
done

# nombres finales fijos: inputassoc.zip / inputfocus.zip
[ -f build/assoc/classes.dex ] || true
echo "OK: inputassoc.zip e inputfocus.zip generados en $(pwd)"
