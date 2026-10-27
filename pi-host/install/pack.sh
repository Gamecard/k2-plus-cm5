#!/bin/bash
# =====================================================================
# K2 Plus – Installationspaket für den Pi bauen (läuft auf dem Arbeits-PC)
#   pi-host/install/pack.sh [-o AUSGABEORDNER] [--tests] [--dicts ORDNER | --ohne-dicts]
# Sammelt den AKTUELLEN Stand von pi-host/ (Module, Tests, config-pi),
# nachbau/, MCU-Dictionaries und die Installationsskripte in
#   k2-pi-paket-<datum>.tar.gz  (+ .sha256)
# Standard-Ausgabe: lokal/pi-paket/  (Ordner lokal/ ist git-ignoriert)
# Keine Verbindung zum Pi/Drucker.
# MCU-Dictionaries (Creality-Firmware, NICHT im öffentlichen Repo): --dicts ORDNER
# bzw. K2_MCU_DICT_DIR (Standard: lokal/mcu-dicts/); ohne sie nur mit --ohne-dicts
# (Batch-Prüfung in install.sh entfällt dann, Dictionary-Tests melden SKIP).
# =====================================================================
set -eu
set -o pipefail

HIER=$(cd "$(dirname "$0")" && pwd)
REPO=$(cd "$HIER/../.." && pwd)
OUT=$REPO/lokal/pi-paket
TESTS=0
DICTS=${K2_MCU_DICT_DIR:-$REPO/lokal/mcu-dicts}
OHNE_DICTS=0
DICT_DATEIEN="mcu-main-F008-GD32F303RET6.dict.json noz-duese-F008-GD32F303CBT6.dict.json"
# Werkzeuge der Protokollanalyse (nicht in diesem Repo) – falls lokal vorhanden, NIE ins Pi-Paket:
AUSGESCHLOSSEN="bustest_lesend.py emu_prtouch_stock.py prtouch_fingertest.py prtouch_lesetest.py prtouch_vorabtest.py"

while [ $# -gt 0 ]; do
  case "$1" in
    -o) [ $# -ge 2 ] || { echo "FEHLER: -o braucht einen Ordner" >&2; exit 2; }; OUT=$2; shift ;;
    --tests) TESTS=1 ;;
    --dicts) [ $# -ge 2 ] || { echo "FEHLER: --dicts braucht einen Ordner" >&2; exit 2; }; DICTS=$2; shift ;;
    --ohne-dicts) OHNE_DICTS=1 ;;
    -h|--hilfe|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "FEHLER: unbekannte Option $1" >&2; exit 2 ;;
  esac
  shift
done

fehler() { echo "FEHLER: $*" >&2; exit 1; }
[ -d "$REPO/pi-host" ] && [ -d "$REPO/nachbau" ] || fehler "Repo-Struktur nicht gefunden unter $REPO"
if [ $OHNE_DICTS -eq 0 ]; then
  for d in $DICT_DATEIEN; do [ -f "$DICTS/$d" ] || fehler "MCU-Dictionary fehlt: $DICTS/$d (--dicts ORDNER oder --ohne-dicts)"; done
fi

STAMP=$(date +%Y%m%d-%H%M)
NAME=k2-pi-paket-$STAMP
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
P=$TMP/$NAME
mkdir -p "$P/pi-host/config-pi" "$P/nachbau/ref-hi" "$P/mcu-dicts"

# --- Inhalte ---------------------------------------------------------
cp "$HIER/install.sh" "$HIER/uninstall.sh" "$HIER/k2cfg.py" "$HIER/test_k2cfg.py" "$HIER/README.md" "$P/"
cp "$HIER/k2-identify.py" "$HIER/test_k2_identify.py" "$P/"          # MCU-Erkennung (nur lesend) + Offline-Test
# Bootstart (Geräte aus dem Bootloader starten, vor Klipper; Logik in nachbau/bootstart.py)
cp "$HIER/k2-bootstart.py" "$HIER/test_k2_bootstart.py" "$HIER/k2-bootstart.service" "$P/"
# Dienste (Start-Wächter, Creality-Brücke) + Web-Werkzeug (nginx/Moonraker) + Offline-Tests
DIENST_DATEIEN="k2_startwaechter.py k2-startwaechter.service test_k2_startwaechter.py
  k2_creality_bruecke.py k2-creality-bruecke.service test_k2_creality_bruecke.py
  k2web.py test_k2web.py test_k2_install.py"
for f in $DIENST_DATEIEN; do cp "$HIER/$f" "$P/"; done
chmod 0755 "$P/install.sh" "$P/uninstall.sh" "$P/k2-identify.py" "$P/k2-bootstart.py"
for f in "$REPO"/pi-host/k2_*.py "$REPO"/pi-host/test_k2_*.py; do cp "$f" "$P/pi-host/"; done
cp "$REPO/pi-host/prtouch_mcu_formats.json" "$P/pi-host/"          # von test_k2_prtouch gebraucht
cp "$REPO"/pi-host/README*.md "$P/pi-host/"                          # Doku zu den Modulen
cp "$REPO"/pi-host/config-pi/*.cfg "$REPO"/pi-host/config-pi/*.md "$P/pi-host/config-pi/"
cp "$REPO"/nachbau/*.py "$P/nachbau/"
cp "$REPO"/nachbau/ref-hi/* "$P/nachbau/ref-hi/"                      # Testvektoren der Selbsttests
cp "$REPO/belt_mdl-live.py" "$P/"                                     # Original (GPLv3) für test_k2_belt-Vergleich
if [ $OHNE_DICTS -eq 0 ]; then
  for d in $DICT_DATEIEN; do cp "$DICTS/$d" "$P/mcu-dicts/"; done
else
  rmdir "$P/mcu-dicts"; echo "  Hinweis: ohne MCU-Dictionaries (--ohne-dicts) – install.sh überspringt die Batch-Prüfung"
fi
for x in $AUSGESCHLOSSEN; do rm -f "$P/pi-host/$x"; done
# Creality-Materialdatenbank NIE ins Paket (Firmware-Datei bzw. Benutzer-Kopie mit
# Cloud-Kennungen) – install.sh --material-db holt sie getrennt vom Drucker.
find "$P" -iname '*material_database*' -print -delete | sed 's#^#  ausgeschlossen: #'
# Ebenso nie den Düsen-Material-Zustand (remain_material*.json: Stock-Datei bzw.
# von k2_box geschriebener Laufzeitzustand) – install.sh --remain-material.
find "$P" -iname 'remain_material*.json' -print -delete | sed 's#^#  ausgeschlossen: #'
find "$P" -name '__pycache__' -prune -exec rm -rf {} +
[ -f "$P/pi-host/k2_motor_init.py" ] || fehler "pi-host/k2_motor_init.py fehlt"
[ -f "$P/k2-identify.py" ] || fehler "k2-identify.py fehlt"
for f in k2-bootstart.py test_k2_bootstart.py k2-bootstart.service nachbau/bootstart.py $DIENST_DATEIEN \
         pi-host/k2_konsole.py pi-host/k2_dateipruefung.py pi-host/k2_resume.py; do
  [ -f "$P/$f" ] || fehler "$f fehlt"
done
# Einheiten sind Vorlagen: keine festen Benutzer-/Home-Pfade
if grep -nE '/home/|^User=[^@]' "$P"/k2-*.service; then fehler "feste Pfade/Benutzer in einer Einheit (oben) – Vorlage mit @USER@/@TOOLS@/@PY@ nötig"; fi

# --- Prüfungen -------------------------------------------------------
# Geheimnisse/Zugangsdaten/Netz (Paket bleibt privat, trotzdem nichts davon hinein)
# Persönliche Muster (Passwörter, Mail …) stehen NICHT hier, sondern optional lokal in
# lokal/leak-muster.txt (eine ERE pro Zeile, git-ignoriert, nicht im Repo).
MUSTER='passw(or)?d *[:=]|PRIVATE KEY|192\.168\.[0-9]+\.[0-9]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z.]{2,}|PI-ZUGANG'
LM="$REPO/lokal/leak-muster.txt"
if [ -f "$LM" ]; then
  while IFS= read -r z; do [ -n "$z" ] && MUSTER="$MUSTER|$z"; done < "$LM"
else
  echo "Hinweis: $LM fehlt – nur allgemeine Leak-Muster geprüft" >&2
fi
if grep -rnIiE "$MUSTER" "$P"; then
  fehler "Leak-Muster im Paketinhalt (oben) – Paket NICHT gebaut"
fi
# Material-DB: weder dem Namen noch dem Inhalt nach (Kennfelder der Creality-DB in JSON)
if find "$P" -iname '*material_database*' | grep .; then fehler "Material-DB im Paket (oben) – Paket NICHT gebaut"; fi
if find "$P" -type f -iname '*.json' -exec grep -lE '"(meterialType|printerIntName|kvParam)"' {} + | grep .; then
  fehler "JSON mit Material-DB-Inhalt im Paket (oben) – Paket NICHT gebaut"
fi
# remain_material: weder dem Namen noch dem Inhalt nach (JSON mit Schlüssel "remain_material")
if find "$P" -iname 'remain_material*.json' | grep .; then fehler "remain_material-Datei im Paket (oben) – Paket NICHT gebaut"; fi
if find "$P" -type f -iname '*.json' -exec grep -lE '"remain_material"[[:space:]]*:' {} + | grep .; then
  fehler "JSON mit remain_material-Inhalt im Paket (oben) – Paket NICHT gebaut"
fi
# Stock-printer.cfg (SAVE_CONFIG mit [auto_addr] = Geräte-Kennungen) nie ins Paket –
# install.sh --stock-printer-cfg liest sie getrennt (README §4d)
if grep -rlE '^#\*# \[auto_addr\]' "$P" | grep .; then
  fehler "Datei mit Stock-SAVE_CONFIG-Abschnitt [auto_addr] im Paket (oben) – Paket NICHT gebaut"
fi
for f in "$P"/*.md "$P"/pi-host/*.md "$P"/pi-host/config-pi/*.md; do
  case "$(basename "$f")" in [U]EBERGABE*|CHECKLISTE*|PI-ZUGANG*|PROTOKOLL*) fehler "unerwünschte Datei $f";; esac
done
bash -n "$P/install.sh"; bash -n "$P/uninstall.sh"
python3 - "$P" <<'PY'
import ast, os, sys
n = 0
for root, _d, files in os.walk(sys.argv[1]):
    for f in files:
        if f.endswith(".py"):
            p = os.path.join(root, f)
            ast.parse(open(p, encoding="utf-8").read(), p)
            n += 1
print("  Python-Syntax ok: %d Dateien" % n)
PY
if [ $TESTS -eq 1 ]; then
  echo "  Tests im Paket (KLIPPER_SRC=lokal/upstream-klipper):"
  for t in "$P"/pi-host/test_k2_*.py; do
    ( cd "$P/pi-host" && KLIPPER_SRC="$REPO/lokal/upstream-klipper" K2_MCU_DICT_DIR="$P/mcu-dicts" \
      python3 -B "$t" ) > "$TMP/t.log" 2>&1 || { tail -20 "$TMP/t.log"; fehler "$(basename "$t") rot"; }
    echo "    $(basename "$t"): $(grep -E 'ERGEBNIS|BESTANDEN|PASS' "$TMP/t.log" | tail -1)"
  done
  # MCU-Erkennung: Teil C (echtes serialhdl am pty) braucht greenlet/cffi/pyserial + gebauten
  # chelper – lokal das venv des Batch-Aufbaus (lokal/upstream-test/), sonst python3 (Teil C dann SKIP)
  IPY=python3; IKS=$REPO/lokal/upstream-klipper
  UT=$REPO/lokal/upstream-test
  if [ -x "$UT/venv/bin/python" ] && [ -f "$UT/klipper-clean/klippy/chelper/c_helper.so" ]; then
    IPY=$UT/venv/bin/python; IKS=$UT/klipper-clean
  fi
  ( cd "$P" && KLIPPER_SRC="$IKS" K2_MCU_DICT_DIR="$P/mcu-dicts" \
    "$IPY" -B "$P/test_k2_identify.py" ) > "$TMP/t.log" 2>&1 || { tail -20 "$TMP/t.log"; fehler "test_k2_identify.py rot"; }
  echo "    test_k2_identify.py: $(grep -E 'ERGEBNIS' "$TMP/t.log" | tail -1)$(grep -q 'SKIP' "$TMP/t.log" && echo ' (Teil C SKIP)')"
  ( cd "$P" && python3 -B "$P/test_k2_bootstart.py" ) > "$TMP/t.log" 2>&1 || { tail -20 "$TMP/t.log"; fehler "test_k2_bootstart.py rot"; }
  echo "    test_k2_bootstart.py: $(grep -E 'ERGEBNIS' "$TMP/t.log" | tail -1)"
  # Stock-SAVE_CONFIG-Übernahme (k2cfg.py); Teil C gleicht mit Upstream-configfile/bed_mesh ab
  ( cd "$P" && KLIPPER_SRC="$IKS" python3 -B "$P/test_k2cfg.py" ) > "$TMP/t.log" 2>&1 || { tail -20 "$TMP/t.log"; fehler "test_k2cfg.py rot"; }
  echo "    test_k2cfg.py: $(grep -E 'ERGEBNIS' "$TMP/t.log" | tail -1)$(grep -q 'SKIP' "$TMP/t.log" && echo ' (mit SKIP)')"
  # Dienste/Web: Logik-Tests + Attrappe für install.sh/uninstall.sh (Schritt 7, Rückbau)
  for t in test_k2web.py test_k2_startwaechter.py test_k2_creality_bruecke.py test_k2_install.py; do
    ( cd "$P" && python3 -B "$P/$t" ) > "$TMP/t.log" 2>&1 || { tail -30 "$TMP/t.log"; fehler "$t rot"; }
    echo "    $t: $(grep -E 'ERGEBNIS' "$TMP/t.log" | tail -1)"
  done
fi

# --- Info + Prüfsummen + Tarball ------------------------------------
{
  echo "Paket: $NAME"
  echo "Erstellt: $(date '+%Y-%m-%d %H:%M:%S %z')"
  echo "Klipper-Ziel: ee2c6fb6b00b2b5d097ef70d0868df7d74943b51 (Upstream, geprüfter Stand)"
  echo "Inhalt: pi-host/k2_*.py, test_k2_*.py, config-pi/, nachbau/, mcu-dicts/, install.sh, uninstall.sh, k2cfg.py (+ test_k2cfg.py), k2-identify.py, k2-bootstart.py (+ .service), k2_startwaechter.py (+ .service), k2_creality_bruecke.py (+ .service), k2web.py, test_k2web.py, test_k2_install.py"
  echo "Quelldateien (sha256, Kurzform):"
  ( cd "$P" && { find pi-host nachbau -name '*.py' -o -name '*.cfg'; echo k2-identify.py; echo k2-bootstart.py; echo k2-bootstart.service;
    echo k2_startwaechter.py; echo k2-startwaechter.service; echo k2_creality_bruecke.py; echo k2-creality-bruecke.service; echo k2web.py; } | sort | xargs sha256sum | awk '{printf "  %s  %s\n", substr($1,1,12), $2}' )
} > "$P/PAKET-INFO.txt"
( cd "$P" && find . -type f ! -name SHA256SUMS | sed 's#^\./##' | LC_ALL=C sort | xargs sha256sum > SHA256SUMS )
mkdir -p "$OUT"
tar --sort=name --owner=0 --group=0 --numeric-owner -czf "$OUT/$NAME.tar.gz" -C "$TMP" "$NAME"
if tar -tzf "$OUT/$NAME.tar.gz" | grep -iE 'material_database|remain_material[^/]*\.json$'; then
  rm -f "$OUT/$NAME.tar.gz"; fehler "Material-DB/remain_material im Tarball (oben) – Paket gelöscht"
fi
( cd "$OUT" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256" )

echo
echo "Paket:    $OUT/$NAME.tar.gz ($(du -h "$OUT/$NAME.tar.gz" | cut -f1), $(wc -l < "$P/SHA256SUMS") Dateien)"
echo "Prüfsumme: $(cut -d' ' -f1 "$OUT/$NAME.tar.gz.sha256")"
echo
echo "Auf den Pi (siehe pi-host/install/README.md):"
echo "  scp $OUT/$NAME.tar.gz pi@<pi-host>:~"
echo "  ssh pi@<pi-host> 'tar xzf $NAME.tar.gz && cd $NAME && ./install.sh'"
