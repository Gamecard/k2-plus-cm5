#!/bin/bash
# =====================================================================
# K2 Plus – Pi-Host-Installation (Upstream-Klipper + K2-Module + Config)
# Läuft auf dem Pi als Benutzer "pi" aus dem entpackten Paketordner:
#   ./install.sh [Optionen]        (Hilfe: ./install.sh --hilfe)
# Bewegt NICHTS, startet Klipper nur mit --starten. Idempotent: mehrfach
# aufrufbar, jeder Lauf legt ein eigenes Backup unter ~/k2-pi-backup/ an.
# =====================================================================
set -eu
set -o pipefail

ZIEL_COMMIT=${K2_ZIEL_COMMIT:-ee2c6fb6b00b2b5d097ef70d0868df7d74943b51}   # geprüfter Upstream-Stand (Umgebung nur für Attrappen-Tests)
MIN_FREI_KB=204800                                     # 200 MB

PKG=$(cd "$(dirname "$0")" && pwd)
KLIPPER_DIR=${KLIPPER_DIR:-$HOME/klipper}
CONFIG_DIR=${CONFIG_DIR:-$HOME/printer_data/config}
KLIPPY_ENV=${KLIPPY_ENV:-$HOME/klippy-env}
BACKUP_ROOT=${BACKUP_ROOT:-$HOME/k2-pi-backup}

KLIPPER_ANFASSEN=1
KLIPPER_ERZWINGEN=0      # --klipper-auf-zielstand: auch einen NEUEREN Checkout auf den Zielstand zurückstellen
PRINTER_ERSETZEN=0
SAVE_AUS_PAKET=0
STARTEN=0
TESTS=1
BATCH=1
ARCH_EGAL=0
DIENST=""
SET_ARGS=()
MAIN_AUTO=0
DUESE_AUTO=0
ERKENNEN=0
RS485_SET=""
MATDB_QUELLE=""
# Materialdatenbank (Creality, NICHT im Paket): fester Zielpfad, den [k2_box]
# in k2-module.cfg liest. Quelle am Stock-Drucker siehe MATDB_STOCK_*.
MATDB_REL=k2/material_database.json
MATDB_STOCK_BENUTZER=/mnt/UDISK/creality/userdata/box/material_database.json
MATDB_STOCK_DEFAULT=/etc/sysConfig/defData/material/material_database.json
REMAIN_QUELLE=""
# Material in der Düse (Stock RemainMaterial, nur color/type): fester Zielpfad =
# Default von [k2_box] remain_material_file. k2_box schreibt die Datei im Betrieb
# selbst – install.sh überschreibt sie NUR mit --remain-material.
REMAIN_REL=k2/remain_material.json
REMAIN_STOCK=/mnt/UDISK/creality/userdata/box/remain_material_data.json
# Kalibrierwerte aus dem SAVE_CONFIG-Block der ORIGINAL-printer.cfg (Stock):
# nur mit --stock-printer-cfg, Abbildung/Prüfung in k2cfg.py (README §4d).
STOCK_CFG=""
STOCK_UEBER=0
STOCK_CFG_PFAD=/mnt/UDISK/printer_data/config/printer.cfg
# k2-bootstart (Geräte aus dem Bootloader starten, vor Klipper): Standard AUS.
# 0 = nichts ändern, 1 = --bootstart (installieren + aktivieren), 2 = --bootstart-aus
BOOTSTART=0
BOOTSTART_DIR=${BOOTSTART_DIR:-$HOME/k2-bootstart}
BS_UNIT=${BOOTSTART_UNIT:-/etc/systemd/system/k2-bootstart.service}   # Umgebung nur für Attrappen-Tests
# Dienste/Web (Schritt 7, Standard AN, je abschaltbar): Start-Wächter, Creality-Brücke
# (+ nginx-Upload-Block), Moonraker [authorization] enable_api_key: False
STARTWAECHTER=1
BRUECKE=1
BEWEGUNG_SPERREN=0        # --bewegung-gesperrt: _PI_FREIGABE allow_move False (Erst-Inbetriebnahme)
MOONRAKER_ANPASSEN=1
K2_TOOLS_DIR=${K2_TOOLS_DIR:-$HOME/k2-tools}
MOONRAKER_CONF=${MOONRAKER_CONF:-$CONFIG_DIR/moonraker.conf}
MOONRAKER_ENV=${MOONRAKER_ENV:-$HOME/moonraker-env}
MOONRAKER_DIENST=${MOONRAKER_DIENST:-moonraker}
NGINX_SITE=${NGINX_SITE:-/etc/nginx/sites-available/mainsail}
NGINX_BIN=${NGINX_BIN:-nginx}
NGINX_DIENST=${NGINX_DIENST:-nginx}
SYSTEMD_DIR=${SYSTEMD_DIR:-/etc/systemd/system}
DIENSTE_EIGEN="k2-startwaechter:k2_startwaechter.py k2-creality-bruecke:k2_creality_bruecke.py"

hilfe() {
  cat <<'EOF'
Aufruf: ./install.sh [Optionen]

  --klipper-nicht-anfassen   Klipper-Checkout NICHT auf ee2c6fb umstellen (nur prüfen/warnen)
                             Ein Checkout, der NEUER ist und ee2c6fb enthält (z. B. nach einem
                             Klipper-Update mit PID-Fix e761a2c76), bleibt ohnehin unverändert.
  --klipper-auf-zielstand    auch einen neueren Checkout auf ee2c6fb zurückstellen (Rückschritt!)
  --printer-cfg-ersetzen     vorhandene printer.cfg durch die K2-printer.cfg ersetzen
                             (sonst landet sie als printer-k2.cfg daneben)
  --save-config-aus-paket    beim Ersetzen den SAVE_CONFIG-Block des Pakets nehmen
                             (Standard: Kalibrierblock einer bisherigen K2-printer.cfg behalten)
  --main-serial PFAD         Port der Main-MCU -> [mcu] serial
                             (Variante B, Pi-UART am Breakout: /dev/ttyAMA4 mit Overlay uart4-pi5;
                             Variante A, USB-RS232-Konverter an J22: /dev/serial/by-id/usb-FTDI_...)
  --main-serial auto         Port der Main-MCU per k2-identify.py ermitteln und eintragen
                             (nur 'identify', nach dem Stoppen von Klipper; ohne eindeutigen
                             Treffer Abbruch vor jeder Änderung an Modulen/Config)
  --duese-serial PFAD|auto   Port der Düsen-MCU -> [mcu nozzle_mcu] serial
                             (Variante B: /dev/ttyAMA2 mit Overlay uart2-pi5)
  --serial-erkennen          k2-identify.py laufen lassen und nur einen VORSCHLAG zeigen
                             (nichts eintragen); Protokoll im Backup-Ordner
  --rs485-serial PFAD        /dev/serial/by-id/... des USB-RS485   -> [k2_485] serial
  --material-db DATEI        Creality-Materialdatenbank (JSON, vorher vom Stock-Drucker geholt)
                             nach CONFIG_DIR/k2/material_database.json kopieren
                             (geprüft: JSON + Struktur; vorhandene Datei kommt ins Backup).
                             Ohne Option bleibt eine vorhandene Datei unverändert.
  --remain-material DATEI    Stock-Datei remain_material_data.json (Material in der Düse,
                             vorher vom Stock-Drucker geholt) als Startwert nach
                             CONFIG_DIR/k2/remain_material.json kopieren (geprüft: JSON,
                             Struktur, Farbe 0RRGGBB/null; vorhandene Datei kommt ins Backup).
                             Ohne Option bleibt eine vorhandene Datei unverändert
                             (k2_box schreibt sie im Betrieb selbst).
  --stock-printer-cfg DATEI  Kalibrierwerte aus dem SAVE_CONFIG-Block der ORIGINAL-printer.cfg
                             (vorher vom Stock-Drucker geholt) in den SAVE_CONFIG-Block der
                             neuen Pi-printer.cfg übernehmen: [box] cut_pos_x -> [k2_box],
                             [input_shaper], [bed_mesh <profil>]. [auto_addr] (Geräte-
                             Kennungen), Heizungs-/prtouch-Werte und Unbekanntes werden
                             ausgelassen und gemeldet. Geprüft in Schritt 1 (vor jeder Änderung).
  --stock-ueberschreiben     mit --stock-printer-cfg: gleichnamige Abschnitte mit ANDEREN
                             Werten im Pi-Block durch die Stock-Werte ersetzen
                             (Standard: Pi-Block behalten, nur melden)
  --bootstart               k2-bootstart.service installieren und AKTIVIEREN: startet bei jedem
                             Boot VOR Klipper Main-/Düsen-MCU und RS485-Geräte aus dem Bootloader
                             (nur Start, nie Update; Ports aus printer.cfg/k2-module.cfg).
                             GERÄTETEST OFFEN – nur mit ausdrücklicher Freigabe, erst nach einem
                             erfolgreichen Handlauf von k2-bootstart.py (README §4c).
  --bootstart-aus            k2-bootstart.service deaktivieren (Datei bleibt liegen)
  --ohne-startwaechter       Start-Wächter NICHT einrichten (Standard: k2_startwaechter.py nach
                             ~/k2-tools/, k2-startwaechter.service installieren, aktivieren, starten;
                             verbindet Klipper nach dem Einschalten des Druckers selbst neu)
  --ohne-creality-bruecke    Creality-Brücke NICHT einrichten (Standard: k2_creality_bruecke.py nach
                             ~/k2-tools/, k2-creality-bruecke.service mit ~/moonraker-env/bin/python,
                             nginx-Block "location ~ ^/upload/" im Mainsail-Server vor /websocket,
                             nginx -t + reload; für Orca-Profile mit CrealityPrint-Agent)
  --ohne-moonraker-anpassung moonraker.conf NICHT ändern (Standard: [authorization]
                             enable_api_key: False, sonst lehnt Moonraker OrcaSlicer mit 401 ab;
                             Moonraker wird nur bei einer Änderung neu gestartet)
  --bewegung-gesperrt        nach dem Kopieren in homing.cfg _PI_FREIGABE variable_allow_move: False
                             setzen (G28/Bewegung gesperrt, Freigabe zur Laufzeit mit ALLOW_MOTION).
                             Für die ERST-Inbetriebnahme empfohlen – das Paket liefert True (Stand
                             nach abgeschlossener Inbetriebnahme). Bleibt bei späteren Läufen erhalten
                             (Freigaben allow_* werden wie serial-Pfade übernommen); dauerhaft
                             freigeben: in homing.cfg von Hand variable_allow_move: True.
  --dienst NAME              systemd-Dienst (Standard: klipper, automatisch erkannt)
  --starten                  Klipper-Dienst am Ende starten (Standard: bleibt gestoppt)
  --ohne-tests               Offline-Tests überspringen
  --ohne-batch               Batch-Prüfung der Config überspringen
  --arch-egal                nur für Probeläufe auf Nicht-aarch64-Rechnern
  --hilfe                    diese Hilfe

Umgebung (nur bei abweichender Installation): KLIPPER_DIR, CONFIG_DIR, KLIPPY_ENV, BACKUP_ROOT,
  K2_TOOLS_DIR, MOONRAKER_CONF, MOONRAKER_ENV, MOONRAKER_DIENST, NGINX_SITE, NGINX_BIN, NGINX_DIENST,
  SYSTEMD_DIR, BOOTSTART_DIR
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --klipper-nicht-anfassen) KLIPPER_ANFASSEN=0 ;;
    --klipper-auf-zielstand)  KLIPPER_ERZWINGEN=1 ;;
    --printer-cfg-ersetzen)   PRINTER_ERSETZEN=1 ;;
    --save-config-aus-paket)  SAVE_AUS_PAKET=1 ;;
    --main-serial)  [ $# -ge 2 ] || { echo "FEHLER: $1 braucht einen Wert" >&2; exit 2; }
                    if [ "$2" = auto ]; then MAIN_AUTO=1; else SET_ARGS+=(--set "mcu=$2"); fi; shift ;;
    --duese-serial) [ $# -ge 2 ] || { echo "FEHLER: $1 braucht einen Wert" >&2; exit 2; }
                    if [ "$2" = auto ]; then DUESE_AUTO=1; else SET_ARGS+=(--set "mcu nozzle_mcu=$2"); fi; shift ;;
    --serial-erkennen) ERKENNEN=1 ;;
    --rs485-serial) [ $# -ge 2 ] || { echo "FEHLER: $1 braucht einen Wert" >&2; exit 2; }; SET_ARGS+=(--set "k2_485=$2"); RS485_SET=$2; shift ;;
    --material-db)  [ $# -ge 2 ] || { echo "FEHLER: $1 braucht einen Wert" >&2; exit 2; }; MATDB_QUELLE=$2; shift ;;
    --remain-material) [ $# -ge 2 ] || { echo "FEHLER: $1 braucht einen Wert" >&2; exit 2; }; REMAIN_QUELLE=$2; shift ;;
    --stock-printer-cfg) [ $# -ge 2 ] || { echo "FEHLER: $1 braucht einen Wert" >&2; exit 2; }; STOCK_CFG=$2; shift ;;
    --stock-ueberschreiben) STOCK_UEBER=1 ;;
    --bootstart)    BOOTSTART=1 ;;
    --bootstart-aus) BOOTSTART=2 ;;
    --ohne-startwaechter)       STARTWAECHTER=0 ;;
    --ohne-creality-bruecke)    BRUECKE=0 ;;
    --ohne-moonraker-anpassung) MOONRAKER_ANPASSEN=0 ;;
    --bewegung-gesperrt)        BEWEGUNG_SPERREN=1 ;;
    --dienst)       [ $# -ge 2 ] || { echo "FEHLER: $1 braucht einen Wert" >&2; exit 2; }; DIENST=$2; shift ;;
    --starten)      STARTEN=1 ;;
    --ohne-tests)   TESTS=0 ;;
    --ohne-batch)   BATCH=0 ;;
    --arch-egal)    ARCH_EGAL=1 ;;
    --hilfe|-h|--help) hilfe; exit 0 ;;
    *) echo "FEHLER: unbekannte Option: $1 (--hilfe)" >&2; exit 2 ;;
  esac
  shift
done
if [ $STOCK_UEBER -eq 1 ] && [ -z "$STOCK_CFG" ]; then
  echo "FEHLER: --stock-ueberschreiben nur zusammen mit --stock-printer-cfg DATEI" >&2; exit 2
fi

SCHRITT=""
schritt() { SCHRITT=$1; echo; echo "=== [$1] $2 ==="; }
ok()      { echo "  ok    $*"; }
info()    { echo "  info  $*"; }
warn()    { echo "  WARN  $*"; }
fehler()  { echo; echo "FEHLER in Schritt [$SCHRITT]: $*" >&2; exit 1; }
trap 'rc=$?; if [ $rc -ne 0 ]; then echo "ABBRUCH (Exit $rc) in Schritt [${SCHRITT:-?}] – nichts weiter verändert. Backup (falls angelegt): ${BK:-keins}" >&2; fi' EXIT

# Pfade in Klipper, die NUR von diesem Paket stammen (für Sauberkeitsprüfung + .git/info/exclude)
EIGENE_MUSTER='^klippy/extras/(k2_[A-Za-z0-9_]+\.py|k2_nachbau/)'

klipper_fremdaenderungen() {
  # git status ohne unsere eigenen, installierten Dateien
  git -C "$KLIPPER_DIR" status --porcelain --untracked-files=all \
    | awk '{ p=substr($0,4); print p }' | grep -vE "$EIGENE_MUSTER" | grep -v '__pycache__/' || true
}

# ---------------------------------------------------------------------
schritt 1 "Vorprüfungen"
[ "$(id -u)" -ne 0 ] || fehler "nicht als root/mit sudo starten – als Benutzer pi ausführen"
ARCH=$(uname -m)
if [ "$ARCH" = aarch64 ]; then ok "Architektur aarch64"
elif [ $ARCH_EGAL -eq 1 ]; then warn "Architektur $ARCH (--arch-egal, Probelauf)"
else fehler "Architektur $ARCH statt aarch64 – falsches Gerät?"; fi

[ -f "$PKG/SHA256SUMS" ] || fehler "SHA256SUMS fehlt im Paket ($PKG)"
( cd "$PKG" && sha256sum --quiet -c SHA256SUMS ) || fehler "Prüfsummen stimmen nicht – Paket beschädigt/verändert"
ok "Prüfsummen ($(wc -l < "$PKG/SHA256SUMS") Dateien)"
[ -f "$PKG/PAKET-INFO.txt" ] && info "Paket: $(sed -n 's/^Paket: //p' "$PKG/PAKET-INFO.txt")"

[ -f "$KLIPPER_DIR/klippy/klippy.py" ] || fehler "Klipper nicht gefunden: $KLIPPER_DIR/klippy/klippy.py"
git -C "$KLIPPER_DIR" rev-parse --git-dir >/dev/null 2>&1 || fehler "$KLIPPER_DIR ist kein git-Checkout"
ok "Klipper unter $KLIPPER_DIR"
[ -d "$CONFIG_DIR" ] || fehler "Config-Ordner fehlt: $CONFIG_DIR"
ok "Config-Ordner $CONFIG_DIR"
MATDB_ZIEL=$CONFIG_DIR/$MATDB_REL
REMAIN_ZIEL=$CONFIG_DIR/$REMAIN_REL

# Materialdatenbank prüfen: Exit 0 = ok, Ausgabe = Zählwerte (material_db.py --struktur)
matdb_pruefen() {
  python3 -I -m json.tool "$1" >/dev/null 2>&1 || { echo "kein gültiges JSON"; return 1; }
  local r
  r=$(python3 -I "$PKG/nachbau/material_db.py" --struktur "$1" 2>&1) \
    || { echo "Struktur ungültig: $(printf '%s\n' "$r" | tail -1)"; return 1; }
  local n; n=$(printf '%s\n' "$r" | sed -n 's/^eintraege: //p')
  [ -n "$n" ] && [ "$n" -gt 0 ] 2>/dev/null || { echo "result.list leer"; return 1; }
  printf '%s\n' "$r"
}
matdb_wert() { printf '%s\n' "$1" | sed -n "s/^$2: //p"; }

# remain_material-Datei prüfen (Stock-Format {"remain_material": {"color": "0RRGGBB"|null,
# "type": "<id>"|null}}, Zusatzschlüssel erlaubt). Exit 0 = ok, Ausgabe "color: …"/"type: …",
# sonst Ausgabe = Grund.
remain_pruefen() {
  python3 -I - "$1" <<'PY'
import json, re, sys
def nein(grund):
    print(grund); sys.exit(1)
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        d = json.load(f)
except Exception as e:
    nein("kein gültiges JSON (%s)" % type(e).__name__)
if not isinstance(d, dict):
    nein("oberste Ebene ist kein JSON-Objekt")
if "remain_material" not in d:
    nein("Schlüssel remain_material fehlt")
r = d["remain_material"]
if not isinstance(r, dict):
    nein("remain_material ist kein Objekt")
for k in ("color", "type"):
    if k not in r:
        nein("remain_material.%s fehlt" % k)
c, t = r["color"], r["type"]
if c is not None and not (isinstance(c, str) and re.fullmatch(r"0[0-9A-Fa-f]{6}", c)):
    nein("remain_material.color weder null noch 0RRGGBB: %r" % (c,))
if t is not None and not (isinstance(t, str) and t.strip()):
    nein("remain_material.type weder null noch Text: %r" % (t,))
print("color: %s" % (c if c is not None else "null"))
print("type: %s" % (t if t is not None else "null"))
PY
}

command -v python3 >/dev/null || fehler "python3 fehlt (für k2cfg.py / Material-DB-Prüfung)"
if [ -n "$MATDB_QUELLE" ]; then
  [ -f "$MATDB_QUELLE" ] && [ -r "$MATDB_QUELLE" ] || fehler "--material-db: Datei nicht lesbar: $MATDB_QUELLE"
  MATDB_QUELLE=$(cd "$(dirname "$MATDB_QUELLE")" && pwd)/$(basename "$MATDB_QUELLE")
  MATDB_REP=$(matdb_pruefen "$MATDB_QUELLE") || fehler "--material-db $MATDB_QUELLE: $MATDB_REP – nichts verändert"
  ok "Material-DB $MATDB_QUELLE: $(matdb_wert "$MATDB_REP" eintraege) Einträge, $(matdb_wert "$MATDB_REP" ids_doppelt) Doppel-IDs"
  [ "$(matdb_wert "$MATDB_REP" keine_objekte)" = 0 ] || warn "Material-DB: $(matdb_wert "$MATDB_REP" keine_objekte) Listeneinträge sind keine Objekte (werden ignoriert)"
  [ "$(matdb_wert "$MATDB_REP" unlesbar_kv.nozzle_temperature)" = 0 ] \
    || info "Material-DB: $(matdb_wert "$MATDB_REP" unlesbar_kv.nozzle_temperature) Einträge ohne lesbare nozzle_temperature (Stock-Fallback 220 °C)"
fi
if [ -n "$REMAIN_QUELLE" ]; then
  [ -f "$REMAIN_QUELLE" ] && [ -r "$REMAIN_QUELLE" ] || fehler "--remain-material: Datei nicht lesbar: $REMAIN_QUELLE"
  REMAIN_QUELLE=$(cd "$(dirname "$REMAIN_QUELLE")" && pwd)/$(basename "$REMAIN_QUELLE")
  REMAIN_REP=$(remain_pruefen "$REMAIN_QUELLE") || fehler "--remain-material $REMAIN_QUELLE: $REMAIN_REP – nichts verändert"
  ok "remain_material $REMAIN_QUELLE: color $(matdb_wert "$REMAIN_REP" color), type $(matdb_wert "$REMAIN_REP" type)"
  [ "$(matdb_wert "$REMAIN_REP" color)$(matdb_wert "$REMAIN_REP" type)" != nullnull ] \
    || info "remain_material: color und type null (Stock-Grundzustand) – Düsenmaterial bleibt unbekannt"
fi
if [ -n "$STOCK_CFG" ]; then
  [ -f "$STOCK_CFG" ] && [ -r "$STOCK_CFG" ] || fehler "--stock-printer-cfg: Datei nicht lesbar: $STOCK_CFG"
  STOCK_CFG=$(cd "$(dirname "$STOCK_CFG")" && pwd)/$(basename "$STOCK_CFG")
  # Nur lesen: SAVE_CONFIG-Block + Abbildung prüfen, Grenzen aus der Paket-Config
  if STOCK_REP=$(python3 -I "$PKG/k2cfg.py" stock-pruefen "$STOCK_CFG" \
                   --referenz "$PKG/pi-host/config-pi/printer.cfg" 2>&1); then SRC=0; else SRC=$?; fi
  printf '%s\n' "$STOCK_REP" | grep -vE '^  (stock PRUEFUNG|FEHLER) ' || true
  case $SRC in
    0) ok "Stock-printer.cfg $STOCK_CFG: $(printf '%s\n' "$STOCK_REP" | sed -n 's/^  stock PRUEFUNG: //p') (Übernahme in Schritt 5$([ $STOCK_UEBER -eq 1 ] && echo ', mit --stock-ueberschreiben'))" ;;
    3) fehler "--stock-printer-cfg $STOCK_CFG: kein Abschnitt übernehmbar (oben) – nichts verändert" ;;
    *) GRUND=$(printf '%s\n' "$STOCK_REP" | sed -n 's/^  FEHLER //p' | head -1)
       fehler "--stock-printer-cfg $STOCK_CFG: ${GRUND:-$(printf '%s\n' "$STOCK_REP" | tail -1)} (Exit $SRC) – nichts verändert" ;;
  esac
fi

FREI=$(df -Pk "$HOME" | awk 'NR==2 {print $4}')
[ "$FREI" -ge "$MIN_FREI_KB" ] || fehler "zu wenig Platz in $HOME: $((FREI/1024)) MB frei, nötig $((MIN_FREI_KB/1024)) MB"
ok "freier Platz $((FREI/1024)) MB"

if [ -x "$KLIPPY_ENV/bin/python" ]; then PY=$KLIPPY_ENV/bin/python
else PY=$(command -v python3) || fehler "kein Python gefunden"; warn "kein $KLIPPY_ENV – nutze $PY (Batch braucht Klipper-Abhängigkeiten)"; fi
ok "Python $("$PY" -c 'import sys; print(sys.version.split()[0])') ($PY)"

if [ $MAIN_AUTO -eq 1 ] || [ $DUESE_AUTO -eq 1 ] || [ $ERKENNEN -eq 1 ]; then
  [ -f "$PKG/k2-identify.py" ] || fehler "k2-identify.py fehlt im Paket (für --*-serial auto / --serial-erkennen)"
  ok "k2-identify.py vorhanden (MCU-Erkennung nach dem Stoppen von Klipper)"
fi
if [ $BOOTSTART -eq 1 ]; then
  for f in k2-bootstart.py k2-bootstart.service k2-identify.py k2cfg.py nachbau/bootstart.py nachbau/serial485.py; do
    [ -f "$PKG/$f" ] || fehler "--bootstart: $f fehlt im Paket"
  done
  ok "k2-bootstart-Dateien vorhanden (--bootstart, Einbau in Schritt 8)"
fi

sudo -n true 2>/dev/null || fehler "sudo ohne Passwort nicht möglich (nötig für systemctl stop/start)"
if [ -z "$DIENST" ]; then
  if systemctl cat klipper.service >/dev/null 2>&1; then DIENST=klipper
  else fehler "Dienst klipper.service nicht gefunden – mit --dienst NAME angeben"; fi
fi
systemctl cat "$DIENST.service" >/dev/null 2>&1 || fehler "Dienst $DIENST.service nicht gefunden"
ok "Dienst $DIENST.service"

# Pi-UARTs (Variante B): nur Hinweis, nichts geändert
if [ -r /boot/firmware/config.txt ]; then
  for o in uart2-pi5 uart4-pi5; do
    grep -qE "^[[:space:]]*dtoverlay=$o([[:space:]]|$)" /boot/firmware/config.txt \
      || info "/boot/firmware/config.txt ohne dtoverlay=$o (nur nötig für Variante B: Main/Düse an Pi-UARTs)"
  done
fi

# Dienste/Web für Schritt 7 – alles hier prüfen, vor jeder Änderung
k2st() { printf '%s\n' "$1" | sed -n 's/^STATUS: //p' | tail -1; }
MR_RESTART=1
if [ $STARTWAECHTER -eq 1 ] || [ $BRUECKE -eq 1 ]; then
  [ -d "$SYSTEMD_DIR" ] || fehler "systemd-Ordner fehlt: $SYSTEMD_DIR"
fi
if [ $STARTWAECHTER -eq 1 ]; then
  for f in k2_startwaechter.py k2-startwaechter.service; do
    [ -f "$PKG/$f" ] || fehler "$f fehlt im Paket (oder --ohne-startwaechter)"
  done
  SYS_PY=$(command -v python3)
  ok "Start-Wächter: Dateien vorhanden, läuft mit $SYS_PY (Einbau in Schritt 7)"
fi
if [ $BRUECKE -eq 1 ]; then
  for f in k2_creality_bruecke.py k2-creality-bruecke.service k2web.py; do
    [ -f "$PKG/$f" ] || fehler "$f fehlt im Paket (oder --ohne-creality-bruecke)"
  done
  [ -x "$MOONRAKER_ENV/bin/python" ] || fehler "Creality-Brücke: $MOONRAKER_ENV/bin/python fehlt (Moonraker-venv? MOONRAKER_ENV=…) – oder --ohne-creality-bruecke"
  "$MOONRAKER_ENV/bin/python" -c 'import tornado' >/dev/null 2>&1 \
    || fehler "Creality-Brücke: tornado fehlt in $MOONRAKER_ENV – oder --ohne-creality-bruecke"
  [ -f "$NGINX_SITE" ] && [ -r "$NGINX_SITE" ] \
    || fehler "Creality-Brücke: nginx-Site $NGINX_SITE nicht lesbar (Mainsail? NGINX_SITE=…) – oder --ohne-creality-bruecke"
  NG_PRUEF=$(python3 -I "$PKG/k2web.py" nginx "$NGINX_SITE" 2>&1) \
    || fehler "Creality-Brücke: Upload-Block in $NGINX_SITE nicht einfügbar ($(k2st "$NG_PRUEF")) – von Hand (README §4e) oder --ohne-creality-bruecke"
  sudo -n "$NGINX_BIN" -v >/dev/null 2>&1 || fehler "Creality-Brücke: nginx nicht aufrufbar (sudo -n $NGINX_BIN -v)"
  case $(k2st "$NG_PRUEF") in
    geaendert) ok "Creality-Brücke: tornado ok; nginx-Upload-Block fehlt in $NGINX_SITE (Einbau in Schritt 7)" ;;
    vorhanden) ok "Creality-Brücke: tornado ok; nginx-Upload-Block schon vorhanden" ;;
    *)         ok "Creality-Brücke: tornado ok; nginx: location ~ ^/upload/ steht schon (ohne Paketmarke) – bleibt" ;;
  esac
fi
if [ $MOONRAKER_ANPASSEN -eq 1 ]; then
  [ -f "$MOONRAKER_CONF" ] && [ -r "$MOONRAKER_CONF" ] && [ -w "$MOONRAKER_CONF" ] \
    || fehler "moonraker.conf nicht les-/schreibbar: $MOONRAKER_CONF (MOONRAKER_CONF=…) – oder --ohne-moonraker-anpassung"
  [ -f "$PKG/k2web.py" ] || fehler "k2web.py fehlt im Paket"
  MR_PRUEF=$(python3 -I "$PKG/k2web.py" moonraker "$MOONRAKER_CONF" 2>&1) \
    || fehler "moonraker.conf: $(printf '%s\n' "$MR_PRUEF" | head -1)"
  case $(k2st "$MR_PRUEF") in
    geaendert)    ok "Moonraker: enable_api_key wird in Schritt 7 auf False gesetzt ($MOONRAKER_CONF)" ;;
    unveraendert) ok "Moonraker: enable_api_key steht schon auf False" ;;
    *)            warn "Moonraker: kein [authorization] in $MOONRAKER_CONF – wird NICHT geändert (README §4e)" ;;
  esac
  if ! systemctl cat "$MOONRAKER_DIENST.service" >/dev/null 2>&1; then
    MR_RESTART=0; warn "Dienst $MOONRAKER_DIENST.service nicht gefunden – nach einer Änderung Moonraker von Hand neu starten"
  fi
fi

STAMP=$(date +%Y%m%d-%H%M%S)
BK=$BACKUP_ROOT/$STAMP
# zwei Läufe in derselben Sekunde dürfen sich das Backup nicht teilen
N_BK=1; while [ -e "$BK" ]; do N_BK=$((N_BK+1)); BK=$BACKUP_ROOT/$STAMP-$N_BK; done
STAMP=$(basename "$BK")
mkdir -p "$BK"
cp "$PKG/uninstall.sh" "$BK/uninstall.sh"
{
  echo "STAMP=$STAMP"
  echo "PAKET=$PKG"
  echo "KLIPPER_DIR=$KLIPPER_DIR"
  echo "CONFIG_DIR=$CONFIG_DIR"
  echo "DIENST=$DIENST"
  echo "ZIEL_COMMIT=$ZIEL_COMMIT"
} > "$BK/state.env"
ok "Backup-Ordner $BK"
# k2-bootstart.service: Zustand VOR diesem Lauf merken (uninstall.sh stellt ihn wieder her)
if [ -e "$BS_UNIT" ]; then
  mkdir -p "$BK/bootstart"
  cp -a "$BS_UNIT" "$BK/bootstart/k2-bootstart.service"
  [ -d "$BOOTSTART_DIR" ] && cp -a "$BOOTSTART_DIR" "$BK/bootstart/tools"
  BS_VORHER=$(systemctl is-enabled k2-bootstart.service 2>/dev/null || true)
  echo "BOOTSTART_VORHER=${BS_VORHER:-unbekannt}" >> "$BK/state.env"
else
  echo "BOOTSTART_VORHER=fehlt" >> "$BK/state.env"
fi
echo "BOOTSTART_DIR=$BOOTSTART_DIR" >> "$BK/state.env"
# Start-Wächter/Creality-Brücke/nginx/Moonraker: Zustand VOR diesem Lauf (uninstall.sh)
[ -f "$PKG/k2web.py" ] && cp "$PKG/k2web.py" "$BK/k2web.py"
{
  echo "SYSTEMD_DIR=$SYSTEMD_DIR"
  echo "K2_TOOLS_DIR=$K2_TOOLS_DIR"
  echo "NGINX_SITE=$NGINX_SITE"
  echo "NGINX_BIN=$NGINX_BIN"
  echo "NGINX_DIENST=$NGINX_DIENST"
  echo "MOONRAKER_CONF=$MOONRAKER_CONF"
  echo "MOONRAKER_DIENST=$MOONRAKER_DIENST"
} >> "$BK/state.env"
for paar in $DIENSTE_EIGEN; do
  u=${paar%%:*}; f=${paar#*:}
  if [ -e "$SYSTEMD_DIR/$u.service" ]; then
    mkdir -p "$BK/dienste"; cp "$SYSTEMD_DIR/$u.service" "$BK/dienste/$u.service"
    v=$(systemctl is-enabled "$u.service" 2>/dev/null || true)
    echo "UNIT_VORHER_${u//-/_}=${v:-unbekannt}" >> "$BK/state.env"
  else
    echo "UNIT_VORHER_${u//-/_}=fehlt" >> "$BK/state.env"
  fi
  if [ -e "$K2_TOOLS_DIR/$f" ]; then
    mkdir -p "$BK/k2-tools"; cp -p "$K2_TOOLS_DIR/$f" "$BK/k2-tools/$f"
    echo "TOOL_VORHER_${f//./_}=vorhanden" >> "$BK/state.env"
  else
    echo "TOOL_VORHER_${f//./_}=fehlt" >> "$BK/state.env"
  fi
done
if [ -r "$NGINX_SITE" ] && [ -f "$PKG/k2web.py" ]; then
  NGV=$(python3 -I "$PKG/k2web.py" nginx "$NGINX_SITE" 2>&1 || true)
  echo "NGINX_VORHER=$(k2st "$NGV")" >> "$BK/state.env"
else
  echo "NGINX_VORHER=keine_datei" >> "$BK/state.env"
fi

# ---------------------------------------------------------------------
schritt 2 "Klipper-Checkout auf ${ZIEL_COMMIT:0:7}"
HEAD_VORHER=$(git -C "$KLIPPER_DIR" rev-parse HEAD)
BRANCH_VORHER=$(git -C "$KLIPPER_DIR" symbolic-ref -q --short HEAD || true)
{ echo "KLIPPER_HEAD_VORHER=$HEAD_VORHER"; echo "KLIPPER_BRANCH_VORHER=$BRANCH_VORHER"; } >> "$BK/state.env"
info "aktuell ${HEAD_VORHER:0:9} (${BRANCH_VORHER:-detached})"
FREMD=$(klipper_fremdaenderungen)
GEAENDERT=0
if [ "$HEAD_VORHER" = "$ZIEL_COMMIT" ]; then
  ok "bereits auf dem geprüften Stand"
  [ -z "$FREMD" ] || warn "lokale Änderungen im Klipper-Checkout (Stand damit NICHT der geprüfte):"$'\n'"$FREMD"
elif [ $KLIPPER_ANFASSEN -eq 0 ]; then
  warn "NICHT auf ${ZIEL_COMMIT:0:7} – wegen --klipper-nicht-anfassen unverändert gelassen (ungeprüfter Stand!)"
elif [ $KLIPPER_ERZWINGEN -eq 0 ] && git -C "$KLIPPER_DIR" merge-base --is-ancestor "$ZIEL_COMMIT" "$HEAD_VORHER" 2>/dev/null; then
  warn "Checkout ${HEAD_VORHER:0:9} ist NEUER als der geprüfte Stand ${ZIEL_COMMIT:0:7} (enthält ihn) – kein Rückschritt, bleibt"
  warn "(Offline-Tests/Batch in Schritt 6 prüfen diesen Stand; Rückschritt nur mit --klipper-auf-zielstand)"
  [ -z "$FREMD" ] || warn "lokale Änderungen im Klipper-Checkout:"$'\n'"$FREMD"
else
  if [ -n "$FREMD" ]; then
    echo "$FREMD" | sed 's/^/        /'
    fehler "Klipper-Checkout hat lokale Änderungen (oben) – nichts umgestellt. Aufräumen oder --klipper-nicht-anfassen"
  fi
  if ! git -C "$KLIPPER_DIR" cat-file -e "$ZIEL_COMMIT^{commit}" 2>/dev/null; then
    info "Commit fehlt lokal – git fetch origin"
    git -C "$KLIPPER_DIR" fetch --quiet origin || fehler "git fetch fehlgeschlagen (Netz?)"
    git -C "$KLIPPER_DIR" cat-file -e "$ZIEL_COMMIT^{commit}" 2>/dev/null || fehler "Commit $ZIEL_COMMIT auch nach fetch nicht vorhanden"
  fi
  git -C "$KLIPPER_DIR" checkout --quiet --detach "$ZIEL_COMMIT" || fehler "git checkout fehlgeschlagen"
  GEAENDERT=1
  ok "umgestellt auf ${ZIEL_COMMIT:0:9} (detached HEAD; Moonraker zeigt das als 'detached')"
fi
echo "KLIPPER_GEAENDERT=$GEAENDERT" >> "$BK/state.env"
ln -sfn "$STAMP" "$BACKUP_ROOT/letzte"   # ab hier wird verändert -> Rückbau-Ziel
# Eigene Dateien für git/Moonraker unsichtbar machen (nur lokal, .git/info/exclude)
EXCL="$(git -C "$KLIPPER_DIR" rev-parse --git-path info/exclude)"
case "$EXCL" in /*) ;; *) EXCL="$KLIPPER_DIR/$EXCL" ;; esac
mkdir -p "$(dirname "$EXCL")"; touch "$EXCL"
if grep -q '^# k2-pi-paket BEGIN' "$EXCL"; then ok ".git/info/exclude enthält die K2-Einträge bereits"
else
  printf '%s\n' '# k2-pi-paket BEGIN' '/klippy/extras/k2_*.py' '/klippy/extras/k2_nachbau/' '# k2-pi-paket END' >> "$EXCL"
  echo "EXCLUDE_ANGELEGT=1" >> "$BK/state.env"
  ok "K2-Dateien in .git/info/exclude eingetragen (Checkout bleibt für Moonraker 'sauber')"
fi

# ---------------------------------------------------------------------
schritt 3 "Klipper-Dienst stoppen"
if systemctl is-active --quiet "$DIENST"; then
  sudo -n systemctl stop "$DIENST" || fehler "systemctl stop $DIENST fehlgeschlagen"
  echo "DIENST_WAR_AKTIV=1" >> "$BK/state.env"
  ok "$DIENST gestoppt"
else
  echo "DIENST_WAR_AKTIV=0" >> "$BK/state.env"
  ok "$DIENST lief nicht"
fi

# MCU-Erkennung (nur lesend: 'identify'), erst jetzt, weil Klipper die Ports sonst belegt
IDENT_LOG=""
ERK_MAIN=""
ERK_DUESE=""
if [ $MAIN_AUTO -eq 1 ] || [ $DUESE_AUTO -eq 1 ] || [ $ERKENNEN -eq 1 ]; then
  IDENT_LOG=$BK/identify.txt
  IDENT_ERG=$BK/identify-ergebnis.txt
  : > "$IDENT_ERG"
  info "k2-identify.py: fragt je /dev/serial/by-id/*-Port NUR das Identify ab (Protokoll $IDENT_LOG)"
  set +e
  # RS485-Port nie abfragen: --rs485-serial bzw. [k2_485] aus k2-module.cfg (liest k2-identify selbst)
  IOHNE=()
  [ -n "$RS485_SET" ] && IOHNE=(--ohne "$RS485_SET")
  KLIPPER_DIR="$KLIPPER_DIR" CONFIG_DIR="$CONFIG_DIR" timeout 300 "$PY" -B "$PKG/k2-identify.py" \
    --klipper "$KLIPPER_DIR" --ergebnis "$IDENT_ERG" "${IOHNE[@]}" > "$IDENT_LOG" 2>&1
  IRC=$?
  set -e
  sed 's/^/        /' "$IDENT_LOG"
  ERK_MAIN=$(sed -n 's/^main=//p' "$IDENT_ERG")
  ERK_DUESE=$(sed -n 's/^duese=//p' "$IDENT_ERG")
  echo "IDENTIFY_EXIT=$IRC" >> "$BK/state.env"
  if [ $MAIN_AUTO -eq 1 ]; then
    [ -n "$ERK_MAIN" ] || fehler "--main-serial auto: Main-MCU nicht eindeutig erkannt (k2-identify Exit $IRC, $IDENT_LOG) – Module/Config unverändert; $DIENST ist gestoppt (sudo systemctl start $DIENST)"
    SET_ARGS+=(--set "mcu=$ERK_MAIN")
    ok "--main-serial auto -> $ERK_MAIN"
  fi
  if [ $DUESE_AUTO -eq 1 ]; then
    [ -n "$ERK_DUESE" ] || fehler "--duese-serial auto: Düsen-MCU nicht eindeutig erkannt (k2-identify Exit $IRC, $IDENT_LOG) – Module/Config unverändert; $DIENST ist gestoppt (sudo systemctl start $DIENST)"
    SET_ARGS+=(--set "mcu nozzle_mcu=$ERK_DUESE")
    ok "--duese-serial auto -> $ERK_DUESE"
  fi
  if [ $ERKENNEN -eq 1 ]; then
    info "--serial-erkennen: nur Vorschlag, NICHTS eingetragen (Main: ${ERK_MAIN:-nicht erkannt}, Düse: ${ERK_DUESE:-nicht erkannt})"
  fi
fi

# ---------------------------------------------------------------------
schritt 4 "Module nach klippy/extras/"
E=$KLIPPER_DIR/klippy/extras
mkdir -p "$BK/extras"
VORH=0
for f in "$E"/k2_*.py; do [ -e "$f" ] || continue; cp -a "$f" "$BK/extras/"; VORH=$((VORH+1)); done
[ -d "$E/k2_nachbau" ] && { cp -a "$E/k2_nachbau" "$BK/extras/"; VORH=$((VORH+1)); }
( cd "$BK/extras" && find . -mindepth 1 -maxdepth 1 | sed 's#^\./##' | sort ) > "$BK/extras-vorher.txt"
ok "Backup vorhandener K2-Dateien: $VORH Einträge -> $BK/extras/"
: > "$BK/extras-installiert.txt"
for src in "$PKG"/pi-host/k2_*.py; do
  b=$(basename "$src")
  [ "$b" = k2_motor_init.py ] && dst=k2_motor.py || dst=$b   # Dateiname = Abschnitt [k2_motor]
  install -m 0644 "$src" "$E/$dst"
  cmp -s "$src" "$E/$dst" || fehler "Kopie $dst fehlerhaft"
  echo "$dst" >> "$BK/extras-installiert.txt"
  info "$b -> extras/$dst"
done
rm -rf "$E/k2_nachbau"
mkdir -p "$E/k2_nachbau"
for src in "$PKG"/nachbau/*.py; do
  install -m 0644 "$src" "$E/k2_nachbau/"
  cmp -s "$src" "$E/k2_nachbau/$(basename "$src")" || fehler "Kopie nachbau/$(basename "$src") fehlerhaft"
done
echo "k2_nachbau" >> "$BK/extras-installiert.txt"
ok "$(($(wc -l < "$BK/extras-installiert.txt")-1)) Module + k2_nachbau/ ($(ls "$E/k2_nachbau" | wc -l) Dateien) installiert und verglichen"

# ---------------------------------------------------------------------
schritt 5 "Config nach $CONFIG_DIR"
tar -czf "$BK/config.tar.gz" -C "$(dirname "$CONFIG_DIR")" "$(basename "$CONFIG_DIR")"
( cd "$CONFIG_DIR" && find . -mindepth 1 | sed 's#^\./##' | sort ) > "$BK/config-vorher.txt"
ok "kompletter Config-Ordner gesichert: $BK/config.tar.gz ($(wc -l < "$BK/config-vorher.txt") Einträge)"
: > "$BK/config-installiert.txt"
for src in "$PKG"/pi-host/config-pi/*.cfg; do
  b=$(basename "$src")
  [ "$b" = printer.cfg ] && continue
  python3 "$PKG/k2cfg.py" merge "$src" "$CONFIG_DIR/$b" --alt "$CONFIG_DIR/$b" "${SET_ARGS[@]}"
  chmod 0644 "$CONFIG_DIR/$b"
  echo "$b" >> "$BK/config-installiert.txt"
  info "$b"
done
if [ ! -e "$CONFIG_DIR/printer.cfg" ]; then
  PZIEL=printer.cfg; info "keine printer.cfg vorhanden – K2-printer.cfg wird printer.cfg"
elif [ $PRINTER_ERSETZEN -eq 1 ]; then
  PZIEL=printer.cfg; info "printer.cfg wird ersetzt (--printer-cfg-ersetzen, Original im Backup)"
else
  PZIEL=printer-k2.cfg
fi
SAVE_OPT=()
[ $SAVE_AUS_PAKET -eq 1 ] && SAVE_OPT=(--save-config-aus-paket)
python3 "$PKG/k2cfg.py" merge "$PKG/pi-host/config-pi/printer.cfg" "$CONFIG_DIR/$PZIEL" \
  --alt "$CONFIG_DIR/$PZIEL" "${SET_ARGS[@]}" "${SAVE_OPT[@]}"
# Kalibrierwerte der Stock-printer.cfg in den (eben gebildeten) SAVE_CONFIG-Block;
# Includes sind schon installiert -> Doppelungsprüfung gegen die echte Config
STOCK_STATUS="nicht angefordert (Option --stock-printer-cfg, README §4d)"
if [ -n "$STOCK_CFG" ]; then
  STOCK_OPT=()
  [ $STOCK_UEBER -eq 1 ] && STOCK_OPT=(--ueberschreiben)
  if python3 -I "$PKG/k2cfg.py" stock-uebernehmen "$STOCK_CFG" "$CONFIG_DIR/$PZIEL" "${STOCK_OPT[@]}" \
       > "$BK/stock-import.txt" 2>&1; then SRC=0; else SRC=$?; fi
  grep -vE '^  (stock ERGEBNIS|FEHLER) ' "$BK/stock-import.txt" || true
  STOCK_STATUS=$(sed -n 's/^  stock ERGEBNIS: //p' "$BK/stock-import.txt")
  echo "STOCK_IMPORT_EXIT=$SRC" >> "$BK/state.env"
  case $SRC in
    0) ok "Stock-Kalibrierwerte -> $PZIEL (SAVE_CONFIG): $STOCK_STATUS (Protokoll $BK/stock-import.txt)" ;;
    3) STOCK_STATUS="nichts übernommen"
       warn "Stock-Kalibrierwerte: nichts übernehmbar – $PZIEL unverändert ($BK/stock-import.txt)" ;;
    *) GRUND=$(sed -n 's/^  FEHLER //p' "$BK/stock-import.txt" | head -1)
       fehler "Stock-Kalibrierwerte nicht übernommen: ${GRUND:-$(tail -1 "$BK/stock-import.txt")} (Exit $SRC) – $PZIEL steht wie nach dem Merge; Rückbau: $BK/uninstall.sh" ;;
  esac
  if grep -q 'BEHALTEN' "$BK/stock-import.txt"; then
    warn "abweichende Werte im Pi-Block BEHALTEN (oben) – Stock-Werte nur mit --stock-ueberschreiben"
  fi
fi
chmod 0644 "$CONFIG_DIR/$PZIEL"
echo "$PZIEL" >> "$BK/config-installiert.txt"
if [ $BEWEGUNG_SPERREN -eq 1 ]; then
  python3 -I "$PKG/k2cfg.py" setze "$CONFIG_DIR/homing.cfg" "gcode_macro _PI_FREIGABE" variable_allow_move False \
    || fehler "homing.cfg: _PI_FREIGABE variable_allow_move nicht gefunden"
  ok "homing.cfg: _PI_FREIGABE allow_move False (--bewegung-gesperrt; Freigabe zur Laufzeit: ALLOW_MOTION)"
fi
FREIGABE=$(sed -n '/^\[gcode_macro _PI_FREIGABE\]/,/^\[/s/^variable_allow_move:[[:space:]]*//p' "$CONFIG_DIR/homing.cfg" 2>/dev/null | head -1)
echo "PRINTER_ZIEL=$PZIEL" >> "$BK/state.env"
ok "$(wc -l < "$BK/config-installiert.txt") cfg-Dateien abgelegt, Hauptdatei: $PZIEL"
if [ "$PZIEL" = printer-k2.cfg ]; then
  warn "vorhandene printer.cfg NICHT ersetzt. Klipper lädt weiter printer.cfg!"
  warn "Zum Umstellen: erneut mit --printer-cfg-ersetzen, oder von Hand: mv printer-k2.cfg printer.cfg"
fi
if [ "$PZIEL" = printer.cfg ] && [ -e "$CONFIG_DIR/printer-k2.cfg" ]; then
  info "printer-k2.cfg aus einem früheren Lauf liegt noch daneben (wird nicht geladen, kann gelöscht werden)"
fi
# Materialdatenbank (nie im Paket; nur mit --material-db kopiert)
MATDB_STATUS=fehlt
if [ -e "$MATDB_ZIEL" ]; then
  mkdir -p "$BK/material-db"
  cp -a "$MATDB_ZIEL" "$BK/material-db/material_database.json"
  echo "MATDB_BACKUP=1" >> "$BK/state.env"
fi
if [ -n "$MATDB_QUELLE" ]; then
  if [ -f "$MATDB_ZIEL" ] && cmp -s "$MATDB_QUELLE" "$MATDB_ZIEL"; then
    MATDB_STATUS=unveraendert; chmod 0644 "$MATDB_ZIEL"
    ok "Material-DB $MATDB_REL identisch mit --material-db – unverändert"
  else
    [ -e "$MATDB_ZIEL" ] && MATDB_STATUS=ersetzt || MATDB_STATUS=neu
    mkdir -p "$(dirname "$MATDB_ZIEL")"
    install -m 0644 "$MATDB_QUELLE" "$MATDB_ZIEL.k2tmp"
    mv -f "$MATDB_ZIEL.k2tmp" "$MATDB_ZIEL"
    cmp -s "$MATDB_QUELLE" "$MATDB_ZIEL" || fehler "Kopie Material-DB fehlerhaft"
    echo "$MATDB_REL" >> "$BK/config-installiert.txt"
    ok "Material-DB -> $MATDB_REL ($MATDB_STATUS$([ $MATDB_STATUS = ersetzt ] && echo ", alte Datei: $BK/material-db/"), 0644)"
  fi
elif [ -e "$MATDB_ZIEL" ]; then
  MATDB_STATUS=vorhanden
  if R=$(matdb_pruefen "$MATDB_ZIEL"); then
    ok "Material-DB $MATDB_REL vorhanden, bleibt ($(matdb_wert "$R" eintraege) Einträge)"
  else
    MATDB_STATUS=defekt
    warn "Material-DB $MATDB_REL vorhanden, aber unbrauchbar ($R) – k2_box nutzt dann Festwerte. Neu holen, s. Zusammenfassung"
  fi
else
  info "keine Material-DB ($MATDB_REL) – k2_box nutzt Festwerte; Holen s. Zusammenfassung"
fi
echo "MATDB_STATUS=$MATDB_STATUS" >> "$BK/state.env"
# Material in der Düse (nie im Paket; nur mit --remain-material kopiert, sonst
# gehört die Datei k2_box, das sie im Betrieb schreibt)
REMAIN_STATUS=fehlt
if [ -e "$REMAIN_ZIEL" ]; then
  mkdir -p "$BK/remain-material"
  cp -a "$REMAIN_ZIEL" "$BK/remain-material/remain_material.json"
  echo "REMAIN_BACKUP=1" >> "$BK/state.env"
fi
if [ -n "$REMAIN_QUELLE" ]; then
  if [ -f "$REMAIN_ZIEL" ] && cmp -s "$REMAIN_QUELLE" "$REMAIN_ZIEL"; then
    REMAIN_STATUS=unveraendert; chmod 0644 "$REMAIN_ZIEL"
    ok "remain_material $REMAIN_REL identisch mit --remain-material – unverändert"
  else
    [ -e "$REMAIN_ZIEL" ] && REMAIN_STATUS=ersetzt || REMAIN_STATUS=neu
    [ $REMAIN_STATUS = ersetzt ] && warn "vorhandene $REMAIN_REL (ggf. von k2_box im Betrieb geschrieben) wird durch --remain-material ersetzt"
    mkdir -p "$(dirname "$REMAIN_ZIEL")"
    install -m 0644 "$REMAIN_QUELLE" "$REMAIN_ZIEL.k2tmp"
    mv -f "$REMAIN_ZIEL.k2tmp" "$REMAIN_ZIEL"
    cmp -s "$REMAIN_QUELLE" "$REMAIN_ZIEL" || fehler "Kopie remain_material fehlerhaft"
    echo "$REMAIN_REL" >> "$BK/config-installiert.txt"
    ok "remain_material -> $REMAIN_REL ($REMAIN_STATUS$([ $REMAIN_STATUS = ersetzt ] && echo ", alte Datei: $BK/remain-material/"), 0644)"
  fi
elif [ -e "$REMAIN_ZIEL" ]; then
  REMAIN_STATUS=vorhanden
  if R=$(remain_pruefen "$REMAIN_ZIEL"); then
    ok "remain_material $REMAIN_REL vorhanden, bleibt (color $(matdb_wert "$R" color), type $(matdb_wert "$R" type))"
  else
    REMAIN_STATUS=defekt
    warn "remain_material $REMAIN_REL vorhanden, aber unbrauchbar ($R) – bleibt unverändert; k2_box startet dann ohne bekanntes Düsenmaterial"
  fi
else
  info "keine $REMAIN_REL – Düsenmaterial nach Start unbekannt (erste Spülung pauschal); Holen s. Zusammenfassung"
fi
echo "REMAIN_STATUS=$REMAIN_STATUS" >> "$BK/state.env"
if [ -e "$CONFIG_DIR/mainsail.cfg" ]; then
  info "mainsail.cfg vorhanden, wird von der K2-printer.cfg absichtlich NICHT eingebunden"
  info "(eigene PAUSE/RESUME/CANCEL_PRINT + [virtual_sdcard]/[pause_resume]/[display_status] in der K2-Config)"
fi

# ---------------------------------------------------------------------
schritt 6 "Offline-Tests und Batch-Prüfung (ohne Hardware)"
FEHL=0
if [ $TESTS -eq 1 ]; then
  mkdir -p "$BK/tests"
  for t in "$PKG"/pi-host/test_k2_*.py; do
    n=$(basename "$t" .py)
    if ( cd "$PKG/pi-host" && KLIPPER_SRC="$KLIPPER_DIR" K2_MCU_DICT_DIR="$PKG/mcu-dicts" \
         timeout 600 "$PY" -B "$t" ) > "$BK/tests/$n.log" 2>&1; then
      ok "$n: $(grep -E 'ERGEBNIS|BESTANDEN|PASS' "$BK/tests/$n.log" | tail -1)"
    else
      warn "$n FEHLGESCHLAGEN – $BK/tests/$n.log"; FEHL=$((FEHL+1))
    fi
  done
  if [ -f "$PKG/test_k2_identify.py" ]; then
    if ( cd "$PKG" && KLIPPER_SRC="$KLIPPER_DIR" K2_MCU_DICT_DIR="$PKG/mcu-dicts" \
         timeout 600 "$PY" -B "$PKG/test_k2_identify.py" ) > "$BK/tests/test_k2_identify.log" 2>&1; then
      ok "test_k2_identify: $(grep -E 'ERGEBNIS' "$BK/tests/test_k2_identify.log" | tail -1)"
    else
      warn "test_k2_identify FEHLGESCHLAGEN – $BK/tests/test_k2_identify.log"; FEHL=$((FEHL+1))
    fi
  fi
  if [ -f "$PKG/test_k2_bootstart.py" ]; then
    if ( cd "$PKG" && timeout 600 "$PY" -B "$PKG/test_k2_bootstart.py" ) > "$BK/tests/test_k2_bootstart.log" 2>&1; then
      ok "test_k2_bootstart: $(grep -E 'ERGEBNIS' "$BK/tests/test_k2_bootstart.log" | tail -1)"
    else
      warn "test_k2_bootstart FEHLGESCHLAGEN – $BK/tests/test_k2_bootstart.log"; FEHL=$((FEHL+1))
    fi
  fi
  if [ -f "$PKG/test_k2cfg.py" ]; then
    if ( cd "$PKG" && KLIPPER_SRC="$KLIPPER_DIR" timeout 600 "$PY" -B "$PKG/test_k2cfg.py" ) > "$BK/tests/test_k2cfg.log" 2>&1; then
      ok "test_k2cfg: $(grep -E 'ERGEBNIS' "$BK/tests/test_k2cfg.log" | tail -1)"
    else
      warn "test_k2cfg FEHLGESCHLAGEN – $BK/tests/test_k2cfg.log"; FEHL=$((FEHL+1))
    fi
  fi
  # reine Offline-Tests der Hilfswerkzeuge/Dienste (ohne Klipper, ohne Netz)
  for t in test_k2web test_k2_startwaechter test_k2_creality_bruecke; do
    [ -f "$PKG/$t.py" ] || continue
    if ( cd "$PKG" && timeout 300 "$PY" -B "$PKG/$t.py" ) > "$BK/tests/$t.log" 2>&1; then
      ok "$t: $(grep -E 'ERGEBNIS' "$BK/tests/$t.log" | tail -1)"
    else
      warn "$t FEHLGESCHLAGEN – $BK/tests/$t.log"; FEHL=$((FEHL+1))
    fi
  done
  for m in serial485 box boxmini cfsseq motor motor_mini auto_addr bootstart; do
    if ( cd "$PKG/nachbau" && timeout 600 "$PY" -B "$m.py" ) > "$BK/tests/nachbau-$m.log" 2>&1; then
      ok "nachbau/$m Selbsttest: $(grep -viE '^\s*$' "$BK/tests/nachbau-$m.log" | tail -1 | cut -c1-70)"
    else
      warn "nachbau/$m Selbsttest FEHLGESCHLAGEN – $BK/tests/nachbau-$m.log"; FEHL=$((FEHL+1))
    fi
  done
else
  info "Tests übersprungen (--ohne-tests)"
fi
if [ $BATCH -eq 1 ] && ! [ -f "$PKG/mcu-dicts/mcu-main-F008-GD32F303RET6.dict.json" ]; then
  BATCH=0; BATCH_OHNE_DICTS=1
  warn "Batch-Prüfung übersprungen: Paket ohne MCU-Dictionaries (pack.sh --ohne-dicts) – Config wird erst beim Klipper-Start geprüft"
fi
if [ $BATCH -eq 1 ]; then
  BO=$BK/batch; mkdir -p "$BO"
  set +e
  ( cd "$BO" && timeout 300 "$PY" "$KLIPPER_DIR/klippy/klippy.py" "$CONFIG_DIR/$PZIEL" \
      -i /dev/null -o "$BO/out.serial" -v \
      -d "$PKG/mcu-dicts/mcu-main-F008-GD32F303RET6.dict.json" \
      -d "nozzle_mcu=$PKG/mcu-dicts/noz-duese-F008-GD32F303CBT6.dict.json" \
      -l "$BO/klippy.log" ) > "$BO/ausgabe.txt" 2>&1
  BRC=$?
  set -e
  if [ $BRC -eq 0 ] && ! grep -qE '^(Config error|Internal error)|Unhandled exception' "$BO/klippy.log" "$BO/ausgabe.txt"; then
    ok "Batch-Prüfung $PZIEL: EXIT 0, keine Config-/Internal-Fehler ($BO/klippy.log)"
  else
    warn "Batch-Prüfung FEHLGESCHLAGEN (EXIT $BRC) – $BO/klippy.log, $BO/ausgabe.txt:"
    grep -hE '^(Config error|Internal error)|Unhandled exception|[a-z]+\.[Ee]rror: ' "$BO/klippy.log" "$BO/ausgabe.txt" 2>/dev/null | head -5 | sed 's/^/        /'
    FEHL=$((FEHL+1))
  fi
else
  [ -n "${BATCH_OHNE_DICTS:-}" ] || info "Batch-Prüfung übersprungen (--ohne-batch)"
fi
[ $FEHL -eq 0 ] || fehler "$FEHL Prüfung(en) fehlgeschlagen – Dienst bleibt gestoppt. Logs: $BK/tests, $BK/batch. Rückbau: $BK/uninstall.sh"

# ---------------------------------------------------------------------
schritt 7 "Dienste und Web (Start-Wächter, Creality-Brücke, Moonraker)"
# Alles idempotent: unveränderte Dateien werden nicht neu geschrieben, Dienste nur bei
# Änderung (oder wenn sie nicht laufen) neu gestartet. Vorher-Zustand steht in state.env.
dienst_einbauen() {   # EINHEIT PROGRAMM PYTHON
  local u=$1 f=$2 py=$3 unit=$SYSTEMD_DIR/$1.service neu=0
  mkdir -p "$K2_TOOLS_DIR" "$BK/dienste"
  if ! cmp -s "$PKG/$f" "$K2_TOOLS_DIR/$f"; then
    install -m 0644 "$PKG/$f" "$K2_TOOLS_DIR/$f.k2tmp"
    mv -f "$K2_TOOLS_DIR/$f.k2tmp" "$K2_TOOLS_DIR/$f"
    cmp -s "$PKG/$f" "$K2_TOOLS_DIR/$f" || fehler "Kopie $f fehlerhaft"
    neu=1
  fi
  sed -e "s#@USER@#$(id -un)#g" -e "s#@PY@#$py#g" -e "s#@TOOLS@#$K2_TOOLS_DIR#g" \
      -e "s#@DIENST@#$DIENST#g" -e "s#@MOONRAKER_DIENST@#$MOONRAKER_DIENST#g" \
      "$PKG/$u.service" > "$BK/dienste/$u.service.neu"
  ! grep -qE '@[A-Z_]+@' "$BK/dienste/$u.service.neu" || fehler "Platzhalter in $u.service nicht ersetzt"
  if ! cmp -s "$BK/dienste/$u.service.neu" "$unit"; then
    sudo -n install -m 0644 "$BK/dienste/$u.service.neu" "$unit" || fehler "Einheit nicht installierbar ($unit)"
    sudo -n systemctl daemon-reload || fehler "systemctl daemon-reload fehlgeschlagen"
    neu=1
  fi
  sudo -n systemctl enable "$u.service" >/dev/null 2>&1 || fehler "systemctl enable $u.service fehlgeschlagen"
  echo "UNIT_INSTALLIERT_${u//-/_}=1" >> "$BK/state.env"
  if [ $neu -eq 1 ] || ! systemctl is-active --quiet "$u.service"; then
    sudo -n systemctl restart "$u.service" || fehler "systemctl restart $u.service fehlgeschlagen"
    ok "$u: $f -> $K2_TOOLS_DIR, Einheit $unit, aktiviert + (neu) gestartet"
    DSTATUS="installiert, aktiviert, läuft"
  else
    ok "$u: unverändert, aktiviert, läuft"
    DSTATUS="unverändert, aktiviert, läuft"
  fi
}
# 7a Moonraker: [authorization] enable_api_key: False
MR_STATUS="nicht angefasst (--ohne-moonraker-anpassung)"
if [ $MOONRAKER_ANPASSEN -eq 1 ]; then
  mkdir -p "$BK/moonraker"
  cp -p "$MOONRAKER_CONF" "$BK/moonraker/moonraker.conf"
  R=$(python3 -I "$PKG/k2web.py" moonraker "$MOONRAKER_CONF" --aus "$MOONRAKER_CONF" 2>&1) \
    || fehler "moonraker.conf nicht geändert: $(printf '%s\n' "$R" | head -1)"
  case $(k2st "$R") in
    geaendert)
      echo "MOONRAKER_GEAENDERT=1" >> "$BK/state.env"
      MR_STATUS="enable_api_key: False gesetzt (vorher: $BK/moonraker/moonraker.conf)"
      ok "Moonraker: $MR_STATUS"
      if [ $MR_RESTART -eq 1 ]; then
        if sudo -n systemctl restart "$MOONRAKER_DIENST"; then ok "$MOONRAKER_DIENST neu gestartet"; MR_STATUS="$MR_STATUS, neu gestartet"
        else warn "systemctl restart $MOONRAKER_DIENST fehlgeschlagen – von Hand neu starten"; MR_STATUS="$MR_STATUS, NEUSTART FEHLT"; fi
      else
        MR_STATUS="$MR_STATUS, Neustart von Hand nötig"
      fi ;;
    unveraendert) MR_STATUS="enable_api_key steht schon auf False – unverändert"; ok "Moonraker: $MR_STATUS" ;;
    *) MR_STATUS="kein [authorization] – nicht geändert"; warn "Moonraker: $MR_STATUS ($MOONRAKER_CONF)" ;;
  esac
fi
# 7b nginx: Upload-Block für Orcas CrealityPrint-Agenten (vor location /websocket)
NG_STATUS="nicht angefasst (--ohne-creality-bruecke)"
if [ $BRUECKE -eq 1 ]; then
  mkdir -p "$BK/nginx"
  cp "$NGINX_SITE" "$BK/nginx/site.vorher"
  R=$(python3 -I "$PKG/k2web.py" nginx "$NGINX_SITE" --aus "$BK/nginx/site.neu" 2>&1) \
    || fehler "nginx-Upload-Block nicht einfügbar ($(k2st "$R")) – $NGINX_SITE unverändert"
  case $(k2st "$R") in
    geaendert)
      sudo -n install -m 0644 "$BK/nginx/site.neu" "$NGINX_SITE" || fehler "$NGINX_SITE nicht schreibbar"
      if ! sudo -n "$NGINX_BIN" -t > "$BK/nginx/nginx-t.txt" 2>&1; then
        sudo -n install -m 0644 "$BK/nginx/site.vorher" "$NGINX_SITE" \
          || fehler "nginx -t fehlgeschlagen UND Original nicht zurückspielbar – von Hand: sudo cp $BK/nginx/site.vorher $NGINX_SITE"
        fehler "nginx -t nach dem Einfügen fehlgeschlagen ($BK/nginx/nginx-t.txt) – Original zurückgespielt, nginx nicht neu geladen"
      fi
      echo "NGINX_GEAENDERT=1" >> "$BK/state.env"
      if sudo -n systemctl reload "$NGINX_DIENST"; then NG_STATUS="Upload-Block eingefügt, nginx -t ok, neu geladen"
      else NG_STATUS="Upload-Block eingefügt, nginx -t ok, RELOAD FEHLGESCHLAGEN"; warn "systemctl reload $NGINX_DIENST fehlgeschlagen"; fi
      ok "nginx: $NG_STATUS ($NGINX_SITE)" ;;
    vorhanden) NG_STATUS="Upload-Block schon vorhanden – unverändert"; ok "nginx: $NG_STATUS" ;;
    *) NG_STATUS="location ~ ^/upload/ ohne Paketmarke vorhanden – unverändert"; ok "nginx: $NG_STATUS" ;;
  esac
fi
# 7c Programme + Einheiten
SW_STATUS="nicht angefasst (--ohne-startwaechter)"
if [ $STARTWAECHTER -eq 1 ]; then
  DSTATUS=""; dienst_einbauen k2-startwaechter k2_startwaechter.py "$SYS_PY"; SW_STATUS=$DSTATUS
fi
BR_STATUS="nicht angefasst (--ohne-creality-bruecke)"
if [ $BRUECKE -eq 1 ]; then
  DSTATUS=""; dienst_einbauen k2-creality-bruecke k2_creality_bruecke.py "$MOONRAKER_ENV/bin/python"; BR_STATUS=$DSTATUS
fi
for paar in $DIENSTE_EIGEN; do
  u=${paar%%:*}
  case "$u" in
    k2-startwaechter)    [ $STARTWAECHTER -eq 1 ] && continue ;;
    k2-creality-bruecke) [ $BRUECKE -eq 1 ] && continue ;;
  esac
  [ -e "$SYSTEMD_DIR/$u.service" ] && info "$u.service aus einem früheren Lauf: $(systemctl is-enabled "$u.service" 2>/dev/null || echo '?'), unverändert (abschalten: sudo systemctl disable --now $u)"
done

# ---------------------------------------------------------------------
schritt 8 "Abschluss"
# k2-bootstart.service (Standard AUS; nur mit --bootstart / --bootstart-aus angefasst)
if [ $BOOTSTART -eq 1 ]; then
  mkdir -p "$BK/bootstart"
  # Probe ohne Hardware VOR jeder Änderung: Ports aus der Config + Plan (öffnet keinen
  # Port, sendet nichts); nur Platzhalter -> Abbruch, nichts an der Einheit verändert
  "$PY" -B "$PKG/k2-bootstart.py" --aus-config "$CONFIG_DIR" --dry-run \
    > "$BK/bootstart/trockenlauf.txt" 2>&1 \
    || fehler "k2-bootstart --dry-run fehlgeschlagen: $(grep -m1 '^FEHLER' "$BK/bootstart/trockenlauf.txt" || echo "s. $BK/bootstart/trockenlauf.txt") – Einheit NICHT installiert (Module/Config sind installiert)"
  ok "Trockenlauf mit den Ports aus $CONFIG_DIR: $BK/bootstart/trockenlauf.txt"
  mkdir -p "$BOOTSTART_DIR/nachbau"
  install -m 0755 "$PKG/k2-bootstart.py" "$PKG/k2-identify.py" "$BOOTSTART_DIR/"
  install -m 0644 "$PKG/k2cfg.py" "$BOOTSTART_DIR/"
  install -m 0644 "$PKG/nachbau/bootstart.py" "$PKG/nachbau/serial485.py" "$BOOTSTART_DIR/nachbau/"
  ok "k2-bootstart.py + nachbau/bootstart.py -> $BOOTSTART_DIR"
  sed -e "s#@USER@#$(id -un)#g" -e "s#@PY@#$PY#g" -e "s#@TOOLS@#$BOOTSTART_DIR#g" \
      -e "s#@CONFIG_DIR@#$CONFIG_DIR#g" -e "s#@KLIPPER_DIR@#$KLIPPER_DIR#g" -e "s#@DIENST@#$DIENST#g" \
      "$PKG/k2-bootstart.service" > "$BK/bootstart/k2-bootstart.service.neu"
  ! grep -qE '@[A-Z_]+@' "$BK/bootstart/k2-bootstart.service.neu" || fehler "Platzhalter in der Einheit nicht ersetzt"
  sudo -n install -m 0644 "$BK/bootstart/k2-bootstart.service.neu" "$BS_UNIT" || fehler "Einheit nicht installierbar ($BS_UNIT)"
  sudo -n systemctl daemon-reload || fehler "systemctl daemon-reload fehlgeschlagen"
  sudo -n systemctl enable k2-bootstart.service || fehler "systemctl enable k2-bootstart.service fehlgeschlagen"
  echo "BOOTSTART_INSTALLIERT=1" >> "$BK/state.env"
  BOOTSTART_STATUS="installiert + aktiviert (läuft ab dem nächsten Boot vor $DIENST; Log: journalctl -u k2-bootstart)"
  warn "k2-bootstart.service AKTIV – Gerätetest offen, nur mit ausdrücklicher Freigabe (BOOTSTART-WERKZEUG.md)"
elif [ $BOOTSTART -eq 2 ]; then
  if [ -e "$BS_UNIT" ]; then
    sudo -n systemctl disable k2-bootstart.service || fehler "systemctl disable k2-bootstart.service fehlgeschlagen"
    echo "BOOTSTART_DEAKTIVIERT=1" >> "$BK/state.env"
    BOOTSTART_STATUS="deaktiviert (--bootstart-aus; $BS_UNIT bleibt liegen)"
    ok "k2-bootstart.service deaktiviert"
  else
    BOOTSTART_STATUS="nicht installiert (--bootstart-aus ohne Wirkung)"
    info "k2-bootstart.service nicht installiert – nichts zu deaktivieren"
  fi
elif [ -e "$BS_UNIT" ]; then
  BOOTSTART_STATUS="vorhanden ($(systemctl is-enabled k2-bootstart.service 2>/dev/null || echo '?')), unverändert"
  info "k2-bootstart.service aus einem früheren Lauf: $BOOTSTART_STATUS (aus: --bootstart-aus)"
else
  BOOTSTART_STATUS="nicht installiert (Standard; Option --bootstart)"
fi
if [ $STARTEN -eq 1 ]; then
  sudo -n systemctl start "$DIENST" || fehler "systemctl start $DIENST fehlgeschlagen"
  ok "$DIENST gestartet (--starten) – klippy.log beobachten"
else
  ok "$DIENST bleibt GESTOPPT (Start: sudo systemctl start $DIENST oder erneut mit --starten)"
fi
echo "FERTIG=1" >> "$BK/state.env"

cat <<EOF

--------------------------------------------------------------------
ZUSAMMENFASSUNG
  Klipper:   $(git -C "$KLIPPER_DIR" rev-parse --short=9 HEAD) (Ziel ${ZIEL_COMMIT:0:9})$([ $GEAENDERT -eq 1 ] && echo ", umgestellt von ${HEAD_VORHER:0:9}")
  Module:    $E/k2_*.py + k2_nachbau/
  Config:    $CONFIG_DIR (Hauptdatei $PZIEL)
  Mat.-DB:   $MATDB_REL: $MATDB_STATUS
  Düse:      $REMAIN_REL: $REMAIN_STATUS
  Stock-Kal: $STOCK_STATUS
  Backup:    $BK   (Rückbau: $BK/uninstall.sh  bzw.  ./uninstall.sh)
  Prüfungen: Tests $([ $TESTS -eq 1 ] && echo grün || echo übersprungen), Batch $([ $BATCH -eq 1 ] && echo 'EXIT 0' || echo übersprungen)
  Dienst:    $([ $STARTEN -eq 1 ] && echo gestartet || echo gestoppt)
  Bootstart: k2-bootstart.service $BOOTSTART_STATUS
  Wächter:   k2-startwaechter.service: $SW_STATUS
  Brücke:    k2-creality-bruecke.service: $BR_STATUS
  nginx:     $NG_STATUS
  Moonraker: $MR_STATUS
--------------------------------------------------------------------
EOF

echo "SERIELLE PORTS (by-id):"
if [ -d /dev/serial/by-id ]; then ls -l /dev/serial/by-id/ | sed '1d; s/^/  /'
else echo "  /dev/serial/by-id/ fehlt – noch kein USB-UART/RS485-Adapter erkannt (Lower-Board/Adapter anstecken)"; fi
echo
echo "Noch Platzhalter (ANNAHME) eintragen:"
if python3 "$PKG/k2cfg.py" platzhalter "$CONFIG_DIR/$PZIEL" "$CONFIG_DIR/k2-module.cfg"; then
  echo "  keine – alle drei serial-Pfade gesetzt"
else
  cat <<EOF
  Zuordnung: Main-MCU  (Variante B: Pi-UART4 /dev/ttyAMA4; Variante A: USB-RS232 an J22, usb-FTDI_...)
                            -> $PZIEL      [mcu]             serial:
             Düsen-MCU (Variante B: Pi-UART2 /dev/ttyAMA2)
                            -> $PZIEL      [mcu nozzle_mcu]  serial:
             USB-RS485      -> k2-module.cfg [k2_485]        serial: (/dev/serial/by-id/usb-...)
  Erkennen:  $PY ./k2-identify.py   (Klipper vorher stoppen; nur 'identify', zeigt Vorschlag)
  Bequem:    ./install.sh --main-serial auto|/dev/ttyAMA4 --duese-serial auto|/dev/ttyAMA2 --rs485-serial /dev/serial/by-id/...
             (Werte bleiben bei späteren Läufen erhalten), baud bleibt 230400.
             Verkabelung: PI-ANSCHLUSS-LOWERBOARD.md bzw. SCHNELLSTART-K2-PLUS.md (Repo-Wurzel).
EOF
fi
if [ -n "$IDENT_LOG" ]; then
  echo
  echo "MCU-Erkennung (k2-identify, $IDENT_LOG):"
  echo "  Main-MCU:  ${ERK_MAIN:-nicht eindeutig erkannt}$([ $MAIN_AUTO -eq 1 ] && echo '   (eingetragen, --main-serial auto)')"
  echo "  Düsen-MCU: ${ERK_DUESE:-nicht eindeutig erkannt}$([ $DUESE_AUTO -eq 1 ] && echo '   (eingetragen, --duese-serial auto)')"
  if [ $ERKENNEN -eq 1 ] && [ -n "$ERK_MAIN$ERK_DUESE" ]; then
    echo "  Vorschlag (NICHT eingetragen): ./install.sh$([ -n "$ERK_MAIN" ] && echo " --main-serial $ERK_MAIN")$([ -n "$ERK_DUESE" ] && echo " --duese-serial $ERK_DUESE") [übrige Optionen]"
  fi
fi
case "$MATDB_STATUS" in fehlt|defekt)
  cat <<EOF

Material-DB ($MATDB_STATUS): wird NICHT mitgeliefert (Creality-Datei). Vom Stock-Drucker
holen (solange das Stock-Upper-Board läuft), dann erneut installieren:
  scp root@<drucker>:$MATDB_STOCK_BENUTZER .
  ./install.sh --material-db material_database.json [übrige Optionen]
  Fehlt dort die Benutzer-Kopie: Firmware-Default $MATDB_STOCK_DEFAULT
EOF
;; esac
case "$REMAIN_STATUS" in fehlt|defekt)
  cat <<EOF

remain_material ($REMAIN_STATUS): Material in der Düse (Farbe/Typ) als Startwert vom
Stock-Drucker übernehmen (optional; sonst spült der erste Wechsel pauschal):
  scp root@<drucker>:$REMAIN_STOCK .
  ./install.sh --remain-material remain_material_data.json [übrige Optionen]
EOF
;; esac
cat <<EOF

NÄCHSTE SCHRITTE (Repo: SCHNELLSTART-K2-PLUS.md §5, Einzelheiten PI-INBETRIEBNAHME.md):
  1. Verkabelung prüfen (PI-ANSCHLUSS-LOWERBOARD.md), dann Klipper starten:
     klippy.log "Klipper ready", beide MCUs 1.1.0.48-293-g493f9a0f, Temperaturen ≈ Raum.
  2. Bewegungsfreigabe _PI_FREIGABE allow_move = ${FREIGABE:-?} (homing.cfg)$([ "${FREIGABE:-}" = True ] && echo " – G28 ist OHNE weitere Freigabe erlaubt;
     Erst-Inbetriebnahme besser mit --bewegung-gesperrt, dann ALLOW_MOTION zur Laufzeit")
  3. Lesend zuerst: QUERY_ENDSTOPS, QUERY_PROBE, KTWO_BUS_STATUS, KTWO_MOTOR_STATUS, KTWO_BOX_STATUS.
  4. Alles Bewegende nur Schritt für Schritt: Bett leer, Hand am Not-Aus.
  Hinweis: ~/k2-test ist veraltet und wird nicht benutzt (bei Bedarf selbst löschen).
EOF
