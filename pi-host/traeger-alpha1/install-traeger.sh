#!/bin/bash
# install-traeger.sh – Softwarepaket „K2-CM5-Träger Alpha 1“ auf dem Pi einspielen
#
# Spielt ein (idempotent, jede geänderte Datei vorher gesichert):
#   a) /boot/firmware/config.txt: Block „K2-TRAEGER-ALPHA1“ (config-txt-traeger.txt),
#      alte gleichnamige Overlay-Zeilen außerhalb des Blocks auskommentiert,
#      Overlay k2-traeger-ina226.dtbo (dtc), i2c-dev beim Start laden
#   b) can0: systemd-networkd (1 Mbit/s), udev txqueuelen 1024, NetworkManager ignoriert can0
#   d) Stromausfall-Wächter k2_stromausfall (+ Dienst, /etc/default nur wenn fehlt)
#   e) tägliche Konfig-Sicherung (Skript, Dienst, Timer) + Werkzeug k2_readonly.sh
# NICHT: Klipper-Konfig (klipper-ausschnitte.cfg, von Hand), EEPROM-Einstellung.
# Read-only-Umstellung NUR mit --readonly (fragt nach, ruft k2_readonly.sh ein).
#
# Aufruf auf dem Pi im Paketordner:
#   sudo ./install-traeger.sh --trocken        # nur anzeigen, nichts ändern
#   sudo ./install-traeger.sh                  # einspielen
#   sudo ./install-traeger.sh --readonly       # zusätzlich Root read-only (Warnung!)
# Optionen: --ohne-apt  --benutzer NAME  --boot DIR (Standard /boot/firmware)
#           --ja (Rückfrage bei --readonly überspringen)  --kein-pi (Plattform egal)
set -u

TROCKEN=0; READONLY=0; JA=0; APT=1; KEIN_PI=0
BOOT=/boot/firmware; BENUTZER=""
while [ $# -gt 0 ]; do
  case "$1" in
    --trocken) TROCKEN=1 ;;
    --readonly) READONLY=1 ;;
    --ja) JA=1 ;;
    --ohne-apt) APT=0 ;;
    --kein-pi) KEIN_PI=1 ;;
    --boot) [ $# -ge 2 ] || { echo "FEHLER: --boot braucht einen Wert" >&2; exit 2; }; BOOT=$2; shift ;;
    --benutzer) [ $# -ge 2 ] || { echo "FEHLER: --benutzer braucht einen Wert" >&2; exit 2; }; BENUTZER=$2; shift ;;
    -h|--help) sed -n '2,22p' "$0"; exit 0 ;;
    *) echo "FEHLER: unbekannte Option $1" >&2; exit 2 ;;
  esac
  shift
done

PKG=$(cd "$(dirname "$0")" && pwd)
STAMP=$(date +%Y%m%d-%H%M%S)
BK=/var/backups/k2-traeger/$STAMP
CFG=$BOOT/config.txt
GEAENDERT=0

say()  { printf '%s\n' "$*"; }
ok()   { printf 'OK   %s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*" >&2; }
die()  { printf 'FEHLER %s\n' "$*" >&2; exit 1; }
tu() {
  if [ "$TROCKEN" = 1 ]; then printf 'TROCKEN: %s\n' "$*"; return 0; fi
  "$@" || die "fehlgeschlagen: $*"
}
sichern() {   # $1 = vorhandene Datei -> $BK/<pfad> (einmal je Lauf)
  [ -e "$1" ] || return 0
  [ -e "$BK$1" ] && return 0
  if [ "$TROCKEN" = 1 ]; then printf 'TROCKEN: sichere %s -> %s\n' "$1" "$BK$1"; return 0; fi
  mkdir -p "$(dirname "$BK$1")" && cp -a "$1" "$BK$1" || die "Sicherung von $1 fehlgeschlagen"
}
lege_ab() {   # $1 Quelle, $2 Ziel, $3 Modus – nur wenn anders
  if [ -e "$2" ] && cmp -s "$1" "$2"; then ok "unverändert: $2"; return 0; fi
  sichern "$2"
  tu install -D -m "$3" "$1" "$2"
  GEAENDERT=1
  [ "$TROCKEN" = 1 ] || ok "abgelegt: $2"
}
lege_ab_wenn_fehlt() {
  if [ -e "$2" ]; then ok "vorhanden, nicht überschrieben: $2"; return 0; fi
  tu install -D -m "$3" "$1" "$2"
  [ "$TROCKEN" = 1 ] || ok "angelegt: $2"
}

# ---------------------------------------------------------------- Prüfungen
for f in config-txt-traeger.txt k2-traeger-ina226-overlay.dts can0/80-k2-can0.network \
         can0/80-k2-can0.rules can0/k2-can0-nm.conf k2_stromausfall.py k2-stromausfall.service \
         k2_stromausfall.default k2_config_sicherung.sh k2-config-sicherung.service \
         k2-config-sicherung.timer k2_config_sicherung.default k2_readonly.sh; do
  [ -f "$PKG/$f" ] || die "Paketdatei fehlt: $f"
done
if [ "$TROCKEN" != 1 ] && [ "$(id -u)" != 0 ]; then die "als root starten (sudo)"; fi
MODELL=$( { tr -d '\0' < /proc/device-tree/model; } 2>/dev/null || echo "?")
case "$MODELL" in
  *"Compute Module 5"*) ok "Plattform: $MODELL" ;;
  *) if [ "$KEIN_PI" = 1 ] || [ "$TROCKEN" = 1 ]; then warn "Plattform: $MODELL (kein CM5) – fortgesetzt (--kein-pi/--trocken)"
     else die "Plattform: $MODELL – kein Compute Module 5 (Abbruch; --kein-pi erzwingt)"; fi ;;
esac
[ -f "$CFG" ] || { [ "$TROCKEN" = 1 ] && warn "$CFG fehlt (Trockenlauf)" || die "$CFG fehlt (--boot?)"; }
if [ -z "$BENUTZER" ]; then
  BENUTZER=$(systemctl show -p User --value klipper.service 2>/dev/null || true)
fi
say "Paket: $PKG"
say "Sicherungen: $BK"
say "Klipper-Benutzer: ${BENUTZER:-unbekannt}"
[ "$TROCKEN" = 1 ] && say "=== TROCKENLAUF: es wird NICHTS verändert ==="

# ---------------------------------------------------------------- Pakete
if [ "$APT" = 1 ]; then
  FEHLT=""
  for p in python3-libgpiod i2c-tools device-tree-compiler rsync; do
    dpkg -s "$p" >/dev/null 2>&1 || FEHLT="$FEHLT $p"
  done
  if [ -n "$FEHLT" ]; then
    tu apt-get install -y $FEHLT
  else
    ok "Pakete vorhanden (python3-libgpiod i2c-tools device-tree-compiler rsync)"
  fi
else
  say "--   apt übersprungen (--ohne-apt)"
fi

# ---------------------------------------------------------------- a) config.txt
BOOT_RO=0
if findmnt -n -o OPTIONS "$BOOT" 2>/dev/null | grep -q '^ro'; then
  BOOT_RO=1
  tu mount -o remount,rw "$BOOT"
fi
if [ -f "$CFG" ]; then
  NEU=$(mktemp)
  python3 - "$CFG" "$PKG/config-txt-traeger.txt" "$NEU" <<'PY'
import re, sys
cfg, block_datei, aus = sys.argv[1:4]
alt = open(cfg, encoding="utf-8", errors="surrogateescape").read().splitlines()
block = open(block_datei, encoding="utf-8").read().rstrip("\n").splitlines()
ANF, END = "# >>> K2-TRAEGER-ALPHA1", "# <<< K2-TRAEGER-ALPHA1"
assert block[0].startswith(ANF) and block[-1].startswith(END)
konflikt = re.compile(r"^\s*dtoverlay=(uart[0-5]-pi5|mcp251xfd|vc4-kms-dsi-7inch|"
                      r"k2-traeger-ina226)\s*(,|$)")
neu, im_block = [], False
for z in alt:
    if z.strip().startswith(ANF):
        im_block = True
        continue
    if im_block:
        if z.strip().startswith(END):
            im_block = False
        continue
    neu.append("#K2T-alt# " + z if konflikt.match(z) else z)
while neu and not neu[-1].strip():
    neu.pop()
neu += [""] + block
open(aus, "w", encoding="utf-8", errors="surrogateescape").write("\n".join(neu) + "\n")
PY
  if cmp -s "$CFG" "$NEU"; then
    ok "config.txt: Block aktuell"
  else
    say "config.txt – Änderungen:"
    diff -u "$CFG" "$NEU" | sed 's/^/     /'
    sichern "$CFG"
    if [ "$TROCKEN" = 1 ]; then say "TROCKEN: config.txt würde wie oben geändert"
    else cat "$NEU" > "$CFG" || die "config.txt nicht schreibbar"; GEAENDERT=1; ok "config.txt geschrieben"; fi
  fi
  rm -f "$NEU"
  grep -Eq '^\s*dtoverlay=vc4-kms-v3d' "$CFG" || warn "config.txt ohne dtoverlay=vc4-kms-v3d – Display braucht es (Standard in Raspberry Pi OS)"
  grep -Eq '^\s*(console|enable_uart)' "$CFG" && warn "config.txt enthält console/enable_uart – Konsole darf NICHT auf ttyAMA0/2/4 liegen (README)"
fi
if [ -r "$BOOT/cmdline.txt" ] && grep -Eq 'console=(ttyAMA[024]|serial0)' "$BOOT/cmdline.txt"; then
  warn "cmdline.txt: $(grep -Eo 'console=(ttyAMA[024]|serial0)[^ ]*' "$BOOT/cmdline.txt" | head -1) – prüfen: serial0 muss auf ttyAMA10 zeigen (ls -l /dev/serial0), sonst entfernen"
fi
# Overlay übersetzen
DTBO=$BOOT/overlays/k2-traeger-ina226.dtbo
if command -v dtc >/dev/null 2>&1 || [ "$TROCKEN" = 1 ]; then
  TMPDTBO=$(mktemp)
  if [ "$TROCKEN" = 1 ] && ! command -v dtc >/dev/null 2>&1; then
    say "TROCKEN: dtc -@ -I dts -O dtb -o $DTBO k2-traeger-ina226-overlay.dts"
  else
    dtc -q -@ -I dts -O dtb -o "$TMPDTBO" "$PKG/k2-traeger-ina226-overlay.dts" || die "dtc: Overlay nicht übersetzbar"
    lege_ab "$TMPDTBO" "$DTBO" 0644
  fi
  rm -f "$TMPDTBO"
else
  die "dtc fehlt (apt-get install device-tree-compiler)"
fi
MODCONF=$(mktemp); printf '# K2-CM5-Traeger Alpha 1: /dev/i2c-1 fuer i2cdetect/i2ctransfer\ni2c-dev\n' > "$MODCONF"
lege_ab "$MODCONF" /etc/modules-load.d/k2-traeger.conf 0644; rm -f "$MODCONF"
[ "$BOOT_RO" = 1 ] && tu mount -o remount,ro "$BOOT"

# ---------------------------------------------------------------- b) can0
lege_ab "$PKG/can0/80-k2-can0.network" /etc/systemd/network/80-k2-can0.network 0644
lege_ab "$PKG/can0/80-k2-can0.rules" /etc/udev/rules.d/80-k2-can0.rules 0644
if [ -d /etc/NetworkManager ] || [ "$TROCKEN" = 1 ]; then
  lege_ab "$PKG/can0/k2-can0-nm.conf" /etc/NetworkManager/conf.d/k2-can0.conf 0644
fi
if systemctl is-enabled systemd-networkd.service >/dev/null 2>&1; then
  ok "systemd-networkd schon aktiviert"
else
  tu systemctl enable systemd-networkd.service
fi
# networkd-wait-online würde ohne „online“-Link den Start bremsen (Klipper docs/CANBUS.md)
if systemctl is-enabled systemd-networkd-wait-online.service >/dev/null 2>&1; then
  tu systemctl disable systemd-networkd-wait-online.service
fi
tu udevadm control --reload

# ---------------------------------------------------------------- d) Stromausfall
lege_ab "$PKG/k2_stromausfall.py" /usr/local/sbin/k2_stromausfall.py 0755
lege_ab "$PKG/k2-stromausfall.service" /etc/systemd/system/k2-stromausfall.service 0644
lege_ab_wenn_fehlt "$PKG/k2_stromausfall.default" /etc/default/k2_stromausfall 0644
if [ ! -e /usr/local/sbin/k2_stromausfall ] || [ "$TROCKEN" = 1 ]; then
  tu ln -sfn /usr/local/sbin/k2_stromausfall.py /usr/local/sbin/k2_stromausfall
fi

# ---------------------------------------------------------------- e) Sicherung + Werkzeug
lege_ab "$PKG/k2_config_sicherung.sh" /usr/local/sbin/k2_config_sicherung.sh 0755
lege_ab "$PKG/k2-config-sicherung.service" /etc/systemd/system/k2-config-sicherung.service 0644
lege_ab "$PKG/k2-config-sicherung.timer" /etc/systemd/system/k2-config-sicherung.timer 0644
lege_ab_wenn_fehlt "$PKG/k2_config_sicherung.default" /etc/default/k2_config_sicherung 0644
if [ -n "$BENUTZER" ] && ! grep -q '^K2_BENUTZER=' /etc/default/k2_config_sicherung 2>/dev/null; then
  if [ "$TROCKEN" = 1 ]; then say "TROCKEN: K2_BENUTZER=$BENUTZER in /etc/default/k2_config_sicherung"
  else printf 'K2_BENUTZER=%s\n' "$BENUTZER" >> /etc/default/k2_config_sicherung; fi
fi
lege_ab "$PKG/k2_readonly.sh" /usr/local/sbin/k2_readonly.sh 0755

tu systemctl daemon-reload
tu systemctl enable k2-stromausfall.service
tu systemctl enable --now k2-config-sicherung.timer
if [ -d /sys/bus/i2c/devices/1-0040/hwmon ]; then
  tu systemctl restart k2-stromausfall.service
  ok "k2-stromausfall gestartet (INA226 0x40 vorhanden)"
else
  say "--   k2-stromausfall aktiviert, startet nach dem Neustart auf dem Träger (INA226 0x40 jetzt nicht da)"
fi

# ---------------------------------------------------------------- read-only (nur mit Schalter)
if [ "$READONLY" = 1 ]; then
  cat >&2 <<'EOF'
======================================================================
WARNUNG: --readonly macht das Root-Dateisystem schreibgeschützt.
 - Alles außerhalb von /daten (Datenpartition) ist nach jedem Neustart
   wieder im alten Zustand (apt, Klipper-Updates, ~/klipper, ~/.ssh ...).
 - Voraussetzung: Datenpartition K2DATEN, printer_data umgezogen
   (k2_readonly.sh umziehen, README Schritt 12/13) – wird geprüft.
 - Updates nur nach „k2_readonly.sh aus“ + Neustart.
======================================================================
EOF
  if [ "$JA" != 1 ] && [ "$TROCKEN" != 1 ]; then
    printf 'Zum Fortfahren JA eintippen: ' >&2
    read -r antwort
    [ "$antwort" = "JA" ] || die "abgebrochen – nichts schreibgeschützt"
  fi
  if [ "$TROCKEN" = 1 ]; then
    K2_BENUTZER=$BENUTZER /bin/bash "$PKG/k2_readonly.sh" ein --trocken
  else
    K2_BENUTZER=$BENUTZER /usr/local/sbin/k2_readonly.sh ein || die "k2_readonly.sh ein fehlgeschlagen"
  fi
fi

say ""
ok "fertig. Sicherungen geänderter Dateien: $BK"
[ "$GEAENDERT" = 1 ] && say "Neustart nötig (config.txt/Overlays): sudo reboot"
say "Danach: Prüfbefehle aus README-traeger-alpha1.md (Schritt „Nach dem ersten Start“)"
