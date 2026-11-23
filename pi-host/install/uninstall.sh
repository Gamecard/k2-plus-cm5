#!/bin/bash
# =====================================================================
# K2 Plus – Rückbau einer install.sh-Installation aus ~/k2-pi-backup/
#   ./uninstall.sh [--backup DIR | --aelteste] [--klipper-lassen] [--starten] [--ja]
# Standard: letztes Backup (~/k2-pi-backup/letzte). Nach mehreren
# install.sh-Läufen führt --aelteste in einem Schritt zum Zustand VOR der
# ersten Installation zurück.
# =====================================================================
set -eu
set -o pipefail

BACKUP_ROOT=${BACKUP_ROOT:-$HOME/k2-pi-backup}
BK=""
AELTESTE=0
KLIPPER_LASSEN=0
STARTEN=0
JA=0
ZIEL_COMMIT=ee2c6fb6b00b2b5d097ef70d0868df7d74943b51
MATDB_REL=k2/material_database.json   # wie install.sh
REMAIN_REL=k2/remain_material.json    # wie install.sh (Material in der Düse, k2_box)

hilfe() {
  cat <<'EOF'
Aufruf: ./uninstall.sh [Optionen]
  Stellt Module, Config (inkl. moonraker.conf), Klipper-Checkout, k2-bootstart,
  Start-Wächter, Creality-Brücke, den nginx-Upload-Block, die Filament-Seite /cfs/
  und die Mainsail-Abfragen vor Not-Aus/Abbrechen auf den Stand vor dem
  gewählten install.sh-Lauf zurück. Ebenso die Anzeige (install.sh --kiosk: Dienste
  k2-kiosk/k2-display-ruhe, ~/k2-kiosk, Anmeldung auf tty1) und den Webcam-Eintrag
  (install.sh --kamera); crowsnest.conf kommt mit dem Config-Ordner zurück.
  NICHT zurückgebaut wird die Trägerplatine (install.sh --traeger: config.txt-Block,
  can0, Stromausfall-Wächter, Konfig-Sicherung) – uninstall.sh nennt dafür nur die
  Sicherung von install-traeger.sh. Die mit --kiosk installierten Pakete bleiben.
  --backup DIR      bestimmtes Backup (~/k2-pi-backup/<stamp>) zurückspielen
  --aelteste        ältestes Backup = Zustand vor der ersten Installation
  --klipper-lassen  Klipper-Checkout NICHT auf den vorherigen Stand zurückstellen
  --starten         Klipper-Dienst danach starten (Standard: bleibt gestoppt)
  --ja              ohne Rückfrage
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --backup) [ $# -ge 2 ] || { echo "FEHLER: --backup braucht einen Wert" >&2; exit 2; }; BK=$2; shift ;;
    --aelteste) AELTESTE=1 ;;
    --klipper-lassen) KLIPPER_LASSEN=1 ;;
    --starten) STARTEN=1 ;;
    --ja) JA=1 ;;
    --hilfe|-h|--help) hilfe; exit 0 ;;
    *) echo "FEHLER: unbekannte Option: $1 (--hilfe)" >&2; exit 2 ;;
  esac
  shift
done

SCHRITT=""
schritt() { SCHRITT=$1; echo; echo "=== [$1] $2 ==="; }
ok()     { echo "  ok    $*"; }
info()   { echo "  info  $*"; }
warn()   { echo "  WARN  $*"; }
fehler() { echo; echo "FEHLER in Schritt [$SCHRITT]: $*" >&2; exit 1; }

schritt 1 "Backup wählen"
[ "$(id -u)" -ne 0 ] || fehler "nicht als root/mit sudo starten – als Benutzer pi ausführen"
if [ -z "$BK" ]; then
  if [ $AELTESTE -eq 1 ]; then
    for d in $(find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -name '2*' 2>/dev/null | sort); do
      grep -q '^KLIPPER_GEAENDERT=' "$d/state.env" 2>/dev/null && { BK=$d; break; }
    done
  else
    BK=$(readlink -f "$BACKUP_ROOT/letzte" 2>/dev/null || true)
  fi
fi
[ -n "$BK" ] && [ -f "$BK/state.env" ] || fehler "kein gültiges Backup gefunden (${BK:-$BACKUP_ROOT}) – ggf. --backup DIR oder --aelteste"
[ -f "$BK/config.tar.gz" ] || warn "$BK/config.tar.gz fehlt (Installation brach vor Schritt 5 ab) – Config bleibt unverändert"
# state.env nur als Daten lesen (keine Ausführung)
st() { sed -n "s/^$1=//p" "$BK/state.env" | tail -1; }
KLIPPER_DIR=$(st KLIPPER_DIR); CONFIG_DIR=$(st CONFIG_DIR); DIENST=$(st DIENST)
HEAD_VORHER=$(st KLIPPER_HEAD_VORHER); BRANCH_VORHER=$(st KLIPPER_BRANCH_VORHER)
GEAENDERT=$(st KLIPPER_GEAENDERT); EXCL_ANGELEGT=$(st EXCLUDE_ANGELEGT)
ZC=$(st ZIEL_COMMIT); [ -n "$ZC" ] && ZIEL_COMMIT=$ZC   # Zielstand des gewählten Laufs
[ -n "$KLIPPER_DIR" ] && [ -n "$CONFIG_DIR" ] && [ -n "$DIENST" ] || fehler "state.env unvollständig"
ok "Backup $BK"
info "Klipper $KLIPPER_DIR, Config $CONFIG_DIR, Dienst $DIENST"
if [ $JA -eq 0 ]; then
  printf '  Zurückspielen? Aktueller Config-Ordner wird vorher zusätzlich gesichert. [j/N] '
  read -r a; case "$a" in j|J|ja|Ja) ;; *) echo "abgebrochen"; exit 1 ;; esac
fi

schritt 2 "Klipper-Dienst stoppen"
if systemctl is-active --quiet "$DIENST"; then
  sudo -n systemctl stop "$DIENST" || fehler "systemctl stop $DIENST fehlgeschlagen"; ok "$DIENST gestoppt"
else ok "$DIENST lief nicht"; fi

schritt 3 "Module zurück"
E=$KLIPPER_DIR/klippy/extras
if [ ! -f "$BK/extras-vorher.txt" ]; then
  info "Installation brach vor Schritt 4 ab – Module unverändert gelassen"
else
N=0
for f in "$E"/k2_*.py; do [ -e "$f" ] || continue; rm -f "$f"; N=$((N+1)); done
[ -d "$E/k2_nachbau" ] && { rm -rf "$E/k2_nachbau"; N=$((N+1)); }
find "$E/__pycache__" -maxdepth 1 -name 'k2_*.pyc' -delete 2>/dev/null || true
ok "$N K2-Einträge entfernt"
R=0
if [ -d "$BK/extras" ]; then
  for f in "$BK/extras"/* ; do [ -e "$f" ] || continue; cp -a "$f" "$E/"; R=$((R+1)); done
fi
ok "$R Einträge aus dem Backup wiederhergestellt"
fi

schritt 3b "Bootstart-Einheit (k2-bootstart.service)"
# Zustand dieses Backups herstellen: BOOTSTART_VORHER fehlt/leer (älteres Backup oder
# Einheit gab es vor dem Lauf nicht) -> eine Paket-Einheit entfernen; sonst Kopie zurück.
BS_UNIT=${BOOTSTART_UNIT:-/etc/systemd/system/k2-bootstart.service}   # wie install.sh
BS_VORHER=$(st BOOTSTART_VORHER); BS_DIR=$(st BOOTSTART_DIR)
[ -n "$BS_DIR" ] || BS_DIR=$HOME/k2-bootstart
if [ -z "$BS_VORHER" ] || [ "$BS_VORHER" = fehlt ]; then
  if [ -e "$BS_UNIT" ] && grep -q '^# k2-pi-paket: k2-bootstart' "$BS_UNIT"; then
    sudo -n systemctl disable k2-bootstart.service >/dev/null 2>&1 || true
    sudo -n rm -f "$BS_UNIT" || fehler "$BS_UNIT nicht entfernbar"
    sudo -n systemctl daemon-reload || warn "systemctl daemon-reload fehlgeschlagen"
    rm -rf "$BS_DIR"
    ok "k2-bootstart.service deaktiviert und entfernt (gab es vor diesem Lauf nicht), $BS_DIR gelöscht"
  else
    ok "keine k2-bootstart-Einheit des Pakets – nichts zu tun"
  fi
elif [ -f "$BK/bootstart/k2-bootstart.service" ]; then
  sudo -n install -m 0644 "$BK/bootstart/k2-bootstart.service" "$BS_UNIT" || fehler "$BS_UNIT nicht zurückspielbar"
  if [ -d "$BK/bootstart/tools" ]; then rm -rf "$BS_DIR"; cp -a "$BK/bootstart/tools" "$BS_DIR"; fi
  sudo -n systemctl daemon-reload || warn "systemctl daemon-reload fehlgeschlagen"
  case "$BS_VORHER" in
    enabled)  sudo -n systemctl enable k2-bootstart.service >/dev/null 2>&1 || warn "enable fehlgeschlagen" ;;
    disabled) sudo -n systemctl disable k2-bootstart.service >/dev/null 2>&1 || warn "disable fehlgeschlagen" ;;
  esac
  ok "k2-bootstart.service auf den Stand vor diesem Lauf zurückgesetzt ($BS_VORHER)"
else
  warn "BOOTSTART_VORHER=$BS_VORHER, aber keine Kopie im Backup – k2-bootstart.service unverändert"
fi

schritt 3c "Dienste Start-Wächter / Creality-Brücke"
# Zustand VOR dem gewählten Lauf herstellen (wie bei k2-bootstart): Einheit gab es vorher
# nicht (bzw. älteres Backup ohne Eintrag) -> Paket-Einheit (mit Marke) stoppen, deaktivieren,
# entfernen, samt Programm in ~/k2-tools; sonst gesicherte Einheit/Programm + Aktivierung zurück.
# Eine fremde Einheit ohne Paketmarke bleibt unberührt.
SD=$(st SYSTEMD_DIR); [ -n "$SD" ] || SD=${SYSTEMD_DIR:-/etc/systemd/system}
TD=$(st K2_TOOLS_DIR); [ -n "$TD" ] || TD=${K2_TOOLS_DIR:-$HOME/k2-tools}
RELOAD=0
for paar in k2-startwaechter:k2_startwaechter.py k2-creality-bruecke:k2_creality_bruecke.py; do
  u=${paar%%:*}; f=${paar#*:}; unit=$SD/$u.service
  UV=$(st "UNIT_VORHER_${u//-/_}"); TV=$(st "TOOL_VORHER_${f//./_}")
  if [ -z "$UV" ] || [ "$UV" = fehlt ]; then
    if [ -e "$unit" ] && grep -q "^# k2-pi-paket: $u" "$unit"; then
      sudo -n systemctl stop "$u.service" >/dev/null 2>&1 || true
      sudo -n systemctl disable "$u.service" >/dev/null 2>&1 || true
      sudo -n rm -f "$unit" || fehler "$unit nicht entfernbar"
      RELOAD=1
      if [ "$TV" != vorhanden ] && [ -e "$TD/$f" ]; then rm -f "$TD/$f"; fi
      ok "$u.service gestoppt, deaktiviert und entfernt (gab es vor diesem Lauf nicht)"
    elif [ -e "$unit" ]; then
      info "$u.service ohne Paketmarke (von Hand eingerichtet) – bleibt unverändert"
    else
      [ "$TV" = fehlt ] && [ -e "$TD/$f" ] && rm -f "$TD/$f" && info "entfernt: $TD/$f"
      ok "keine $u-Einheit des Pakets – nichts zu tun"
    fi
  elif [ -f "$BK/dienste/$u.service" ]; then
    sudo -n install -m 0644 "$BK/dienste/$u.service" "$unit" || fehler "$unit nicht zurückspielbar"
    RELOAD=1
    if [ "$TV" = vorhanden ] && [ -f "$BK/k2-tools/$f" ]; then mkdir -p "$TD"; cp -p "$BK/k2-tools/$f" "$TD/$f"
    elif [ "$TV" = fehlt ]; then rm -f "$TD/$f"; fi
    sudo -n systemctl daemon-reload || warn "systemctl daemon-reload fehlgeschlagen"
    case "$UV" in
      enabled)  sudo -n systemctl enable "$u.service" >/dev/null 2>&1 || warn "enable $u fehlgeschlagen" ;;
      disabled) sudo -n systemctl disable "$u.service" >/dev/null 2>&1 || warn "disable $u fehlgeschlagen" ;;
    esac
    sudo -n systemctl try-restart "$u.service" >/dev/null 2>&1 || true
    ok "$u.service + $f auf den Stand vor diesem Lauf zurückgesetzt ($UV)"
  else
    warn "UNIT_VORHER_${u//-/_}=$UV, aber keine Kopie im Backup – $u.service unverändert"
  fi
done
[ $RELOAD -eq 1 ] && { sudo -n systemctl daemon-reload || warn "systemctl daemon-reload fehlgeschlagen"; }
rmdir "$TD" 2>/dev/null && info "leerer Ordner $TD entfernt" || true

schritt 3d "nginx-Upload-Block (Creality-Brücke)"
NS=$(st NGINX_SITE); [ -n "$NS" ] || NS=${NGINX_SITE:-/etc/nginx/sites-available/mainsail}
NB=$(st NGINX_BIN); [ -n "$NB" ] || NB=${NGINX_BIN:-nginx}
ND=$(st NGINX_DIENST); [ -n "$ND" ] || ND=${NGINX_DIENST:-nginx}
NV=$(st NGINX_VORHER)
K2WEB=""
for k in "$(dirname "$0")/k2web.py" "$BK/k2web.py"; do [ -f "$k" ] && { K2WEB=$k; break; }; done
if [ "$NV" = vorhanden ]; then
  ok "Upload-Block stand schon vor diesem Lauf in $NS – bleibt"
elif [ ! -r "$NS" ] || ! grep -q '^[[:space:]]*# k2-pi-paket BEGIN creality-upload' "$NS"; then
  ok "kein Upload-Block des Pakets in $NS – nichts zu tun"
else
  mkdir -p "$BK/nginx"
  cp "$NS" "$BK/nginx/site.vor-rueckbau"
  if [ -n "$K2WEB" ]; then
    python3 -I "$K2WEB" nginx-entfernen "$NS" --aus "$BK/nginx/site.rueckbau" >/dev/null || fehler "Upload-Block nicht entfernbar"
  else
    awk 'BEGIN{s=0} /^[ \t]*# k2-pi-paket BEGIN creality-upload/{s=1;next}
         s==1{ if ($0 ~ /^[ \t]*# k2-pi-paket END creality-upload/) s=2; next }
         s==2{ s=0; if ($0 ~ /^[ \t\r]*$/) next } {print}' "$NS" > "$BK/nginx/site.rueckbau"
  fi
  sudo -n install -m 0644 "$BK/nginx/site.rueckbau" "$NS" || fehler "$NS nicht schreibbar"
  if sudo -n "$NB" -t >/dev/null 2>&1; then
    sudo -n systemctl reload "$ND" || warn "systemctl reload $ND fehlgeschlagen"
    ok "Upload-Block aus $NS entfernt, nginx -t ok, neu geladen"
  else
    sudo -n install -m 0644 "$BK/nginx/site.vor-rueckbau" "$NS" || true
    warn "nginx -t nach dem Entfernen fehlgeschlagen – $NS unverändert gelassen (Block bitte von Hand entfernen)"
  fi
fi

schritt 3d2 "Filament-Seite /cfs/ und Mainsail-Abfragen"
CV=$(st CFS_NGINX_VORHER)
if [ "$CV" = vorhanden ] || [ "$CV" = vorhanden_ohne_marke ]; then
  ok "nginx-Block /cfs/ stand schon vor diesem Lauf in $NS – bleibt"
elif [ ! -r "$NS" ] || ! grep -q '^[[:space:]]*# k2-pi-paket BEGIN cfs-faecher' "$NS"; then
  ok "kein nginx-Block /cfs/ des Pakets in $NS – nichts zu tun"
elif [ -z "$K2WEB" ]; then
  warn "k2web.py fehlt – nginx-Block /cfs/ bitte von Hand aus $NS entfernen"
else
  mkdir -p "$BK/cfs"
  cp "$NS" "$BK/cfs/site.vor-rueckbau"
  python3 -I "$K2WEB" nginx-cfs-entfernen "$NS" --aus "$BK/cfs/site.rueckbau" >/dev/null || fehler "nginx-Block /cfs/ nicht entfernbar"
  sudo -n install -m 0644 "$BK/cfs/site.rueckbau" "$NS" || fehler "$NS nicht schreibbar"
  if sudo -n "$NB" -t >/dev/null 2>&1; then
    sudo -n systemctl reload "$ND" || warn "systemctl reload $ND fehlgeschlagen"
    ok "nginx-Block /cfs/ aus $NS entfernt, nginx -t ok, neu geladen"
  else
    sudo -n install -m 0644 "$BK/cfs/site.vor-rueckbau" "$NS" || true
    warn "nginx -t nach dem Entfernen fehlgeschlagen – $NS unverändert gelassen"
  fi
fi
CD=$(st CFS_DIR)
case "$(st CFS_DIR_VORHER)" in
  fehlt)     if [ -n "$CD" ] && [ -d "$CD" ]; then rm -f "$CD/index.html"; rmdir "$CD" 2>/dev/null || true; ok "Filament-Seite $CD entfernt"; fi ;;
  vorhanden) if [ -d "$BK/cfs/seite" ]; then rm -rf "$CD"; cp -a "$BK/cfs/seite" "$CD"; ok "Filament-Seite $CD = Stand vor dem Lauf"; fi ;;
esac
NK=$(st NOTAUS_GESETZT)
if [ -n "$NK" ] && [ -n "$K2WEB" ]; then
  MU2=$(st MOONRAKER_URL); [ -n "$MU2" ] || MU2=${MOONRAKER_URL:-http://127.0.0.1:7125}
  R=$(python3 -I "$K2WEB" notaus-entfernen "$MU2" --keys "$NK" 2>&1 || true)
  case "$R" in
    *"STATUS: entfernt"*|*"STATUS: nicht_vorhanden"*) ok "Mainsail-Abfragen dieses Laufs zurückgenommen ($NK)" ;;
    *) warn "Moonraker nicht erreichbar – Mainsail-Abfragen ($NK) bleiben (Mainsail: Einstellungen → Benutzeroberfläche)" ;;
  esac
fi

schritt 3e "Anzeige (Kiosk), Webcam-Eintrag, Trägerplatine"
# Anzeige: nur zurückbauen, was der gewählte Lauf eingerichtet hat (KIOSK_INSTALLIERT=1)
if [ "$(st KIOSK_INSTALLIERT)" = 1 ]; then
  KD=$(st KIOSK_DIR); [ -n "$KD" ] || KD=${KIOSK_DIR:-$HOME/k2-kiosk}
  GD=$(st GETTY_DIENST); [ -n "$GD" ] || GD=getty@tty1
  KRELOAD=0; KWEG=0
  for u in k2-kiosk k2-display-ruhe; do
    unit=$SD/$u.service
    UV=$(st "KIOSK_UNIT_VORHER_${u//-/_}")
    if [ -z "$UV" ] || [ "$UV" = fehlt ]; then
      if [ -e "$unit" ]; then
        sudo -n systemctl stop "$u.service" >/dev/null 2>&1 || true
        sudo -n systemctl disable "$u.service" >/dev/null 2>&1 || true
        sudo -n rm -f "$unit" || fehler "$unit nicht entfernbar"
        KRELOAD=1; KWEG=1
        ok "$u.service gestoppt, deaktiviert und entfernt (gab es vor diesem Lauf nicht)"
      else
        ok "$u.service nicht vorhanden – nichts zu tun"
      fi
    elif [ -f "$BK/kiosk/$u.service.vorher" ]; then
      sudo -n install -m 0644 "$BK/kiosk/$u.service.vorher" "$unit" || fehler "$unit nicht zurückspielbar"
      sudo -n systemctl daemon-reload || warn "systemctl daemon-reload fehlgeschlagen"
      case "$UV" in
        enabled)  sudo -n systemctl enable "$u.service" >/dev/null 2>&1 || warn "enable $u fehlgeschlagen" ;;
        disabled) sudo -n systemctl disable "$u.service" >/dev/null 2>&1 || warn "disable $u fehlgeschlagen" ;;
      esac
      sudo -n systemctl try-restart "$u.service" >/dev/null 2>&1 || true
      ok "$u.service auf den Stand vor diesem Lauf zurückgesetzt ($UV)"
    else
      warn "KIOSK_UNIT_VORHER_${u//-/_}=$UV, aber keine Kopie im Backup – $u.service unverändert"
    fi
  done
  [ $KRELOAD -eq 1 ] && { sudo -n systemctl daemon-reload || warn "systemctl daemon-reload fehlgeschlagen"; }
  for f in kiosk-start.sh display-ruhe.py; do
    FV=$(st "KIOSK_DATEI_VORHER_${f//[.-]/_}")
    if [ "$FV" = vorhanden ] && [ -f "$BK/kiosk/dateien/$f" ]; then
      mkdir -p "$KD"; cp -p "$BK/kiosk/dateien/$f" "$KD/$f"; info "zurückgespielt: $KD/$f"
    elif [ "$FV" = fehlt ] && [ -e "$KD/$f" ]; then
      rm -f "$KD/$f"; info "entfernt: $KD/$f"
    fi
  done
  rmdir "$KD" 2>/dev/null && info "leerer Ordner $KD entfernt" || true
  # Anmeldung auf tty1 wieder an, wenn sie vor dem Lauf an war
  if [ "$(st GETTY_VORHER)" = enabled ]; then
    sudo -n systemctl enable "$GD.service" >/dev/null 2>&1 || warn "systemctl enable $GD fehlgeschlagen"
    if [ $KWEG -eq 1 ]; then sudo -n systemctl start "$GD.service" >/dev/null 2>&1 || warn "systemctl start $GD fehlgeschlagen"; fi
    ok "Anmeldung auf tty1 ($GD) wieder aktiviert"
  fi
  info "die mit --kiosk installierten Pakete (cage, chromium, fonts-dejavu-core, python3-evdev) bleiben installiert"
else
  ok "Anzeige: vom gewählten Lauf nicht eingerichtet – nichts zu tun"
fi
# Webcam-Eintrag in Moonraker: nur entfernen, wenn der gewählte Lauf ihn angelegt hat
WC=$(st KAMERA_WEBCAM_ANGELEGT)
if [ -n "$WC" ]; then
  MU=$(st MOONRAKER_URL); [ -n "$MU" ] || MU=${MOONRAKER_URL:-http://127.0.0.1:7125}
  if command -v curl >/dev/null 2>&1 \
     && curl -s --max-time 10 -X DELETE -G "$MU/server/webcams/item" --data-urlencode "name=$WC" 2>/dev/null | grep -q '"webcam"'; then
    ok "Moonraker: Webcam \"$WC\" entfernt (von diesem Lauf angelegt)"
  else
    warn "Moonraker: Webcam \"$WC\" nicht entfernbar (Moonraker nicht erreichbar?) – in Mainsail unter Einstellungen > Webcams löschen"
  fi
else
  ok "Webcam: vom gewählten Lauf nicht angelegt – nichts zu tun"
fi
# Trägerplatine: KEIN automatischer Rückbau (Systemdateien außerhalb dieses Backups)
if [ "$(st TRAEGER_GELAUFEN)" = 1 ]; then
  warn "Trägerplatine: install-traeger.sh lief in diesem Lauf – wird NICHT zurückgebaut."
  info "  Sicherung der geänderten Systemdateien (config.txt, can0, Dienste): $(st TRAEGER_SICHERUNG)"
  info "  Von Hand: Dateien von dort zurückkopieren bzw. den Block K2-TRAEGER-ALPHA1 aus der config.txt"
  info "  entfernen, Dienste k2-stromausfall und k2-config-sicherung.timer abschalten, dann sudo reboot."
  info "  Die Anschlüsse in der Klipper-Config kommen mit dem Config-Ordner zurück (Schritt 4)."
else
  ok "Trägerplatine: vom gewählten Lauf nicht eingerichtet – nichts zu tun"
fi

schritt 4 "Config zurück"
MRC=$(st MOONRAKER_CONF); MRD=$(st MOONRAKER_DIENST); [ -n "$MRD" ] || MRD=moonraker
mr_hash() { [ -n "$MRC" ] && [ -f "$MRC" ] && sha256sum < "$MRC" || echo fehlt; }
MR_VOR=$(mr_hash)
if [ -f "$BK/config.tar.gz" ]; then
SICH="$(dirname "$BK")/vor-rueckbau-$(date +%Y%m%d-%H%M%S).tar.gz"
tar -czf "$SICH" -C "$(dirname "$CONFIG_DIR")" "$(basename "$CONFIG_DIR")"
ok "aktueller Stand zusätzlich gesichert: $SICH"
if [ -f "$BK/config-installiert.txt" ]; then
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    if ! grep -qxF "$f" "$BK/config-vorher.txt"; then rm -f "$CONFIG_DIR/$f"; info "entfernt: $f"; fi
  done < "$BK/config-installiert.txt"
fi
tar -xzf "$BK/config.tar.gz" -C "$(dirname "$CONFIG_DIR")"
ok "Config-Ordner aus $BK/config.tar.gz wiederhergestellt"
# Material-DB und remain_material: Zustand dieses Backups herstellen (auch bei
# --aelteste, wenn sie erst ein späterer Lauf bzw. k2_box im Betrieb angelegt hat)
datei_auf_backupstand() {   # REL BACKUPDATEI NAME
  local mz=$CONFIG_DIR/$1
  if [ -f "$2" ]; then
    if ! cmp -s "$2" "$mz"; then
      mkdir -p "$(dirname "$mz")"; cp -a "$2" "$mz"
    fi
    ok "$3 $1 = Backup-Stand ($(dirname "$2")/)"
  elif [ -f "$BK/config-vorher.txt" ] && ! grep -qxF "$1" "$BK/config-vorher.txt"; then
    if [ -e "$mz" ]; then rm -f "$mz"; info "entfernt: $1 (gab es vor diesem Lauf nicht)"; fi
    if ! grep -qxF "$(dirname "$1")" "$BK/config-vorher.txt"; then
      rmdir "$CONFIG_DIR/$(dirname "$1")" 2>/dev/null && info "entfernt: leerer Ordner $(dirname "$1")/" || true
    fi
  fi
}
datei_auf_backupstand "$MATDB_REL" "$BK/material-db/material_database.json" Material-DB
datei_auf_backupstand "$REMAIN_REL" "$BK/remain-material/remain_material.json" remain_material
else
  info "kein Config-Backup in diesem Lauf – übersprungen"
fi
# moonraker.conf außerhalb des Config-Ordners: eigene Sicherung dieses Laufs zurück
# (im Config-Ordner hat das config.tar.gz oben sie schon zurückgebracht)
if [ -n "$MRC" ] && [ "$(st MOONRAKER_GEAENDERT)" = 1 ] && [ -f "$BK/moonraker/moonraker.conf" ]; then
  case "$MRC" in
    "$CONFIG_DIR"/*) ;;
    *) cp -p "$BK/moonraker/moonraker.conf" "$MRC" && ok "moonraker.conf ($MRC) aus dem Backup zurück" ;;
  esac
fi
if [ "$(mr_hash)" != "$MR_VOR" ]; then
  if sudo -n systemctl restart "$MRD"; then ok "moonraker.conf geändert -> $MRD neu gestartet"
  else warn "moonraker.conf geändert, systemctl restart $MRD fehlgeschlagen – von Hand neu starten"; fi
fi

schritt 5 "Klipper-Checkout"
if [ "$GEAENDERT" != 1 ]; then
  ok "Installation hatte den Checkout nicht verändert"
elif [ $KLIPPER_LASSEN -eq 1 ]; then
  info "--klipper-lassen: bleibt auf $(git -C "$KLIPPER_DIR" rev-parse --short=9 HEAD)"
else
  CUR=$(git -C "$KLIPPER_DIR" rev-parse HEAD)
  DIRTY=$(git -C "$KLIPPER_DIR" status --porcelain --untracked-files=no)
  if [ "$CUR" != "$ZIEL_COMMIT" ]; then
    warn "Checkout steht inzwischen auf ${CUR:0:9} (nicht ${ZIEL_COMMIT:0:9}) – nicht angefasst"
  elif [ -n "$DIRTY" ]; then
    warn "lokale Änderungen im Klipper-Checkout – nicht angefasst:"; echo "$DIRTY" | sed 's/^/        /'
  elif [ -n "$BRANCH_VORHER" ]; then
    git -C "$KLIPPER_DIR" checkout --quiet "$BRANCH_VORHER"
    [ "$(git -C "$KLIPPER_DIR" rev-parse HEAD)" = "$HEAD_VORHER" ] \
      || warn "Branch $BRANCH_VORHER zeigt jetzt auf $(git -C "$KLIPPER_DIR" rev-parse --short=9 HEAD), vorher ${HEAD_VORHER:0:9}"
    ok "zurück auf Branch $BRANCH_VORHER"
  else
    git -C "$KLIPPER_DIR" checkout --quiet --detach "$HEAD_VORHER"; ok "zurück auf ${HEAD_VORHER:0:9}"
  fi
fi
EXCL="$(git -C "$KLIPPER_DIR" rev-parse --git-path info/exclude)"
case "$EXCL" in /*) ;; *) EXCL="$KLIPPER_DIR/$EXCL" ;; esac
if [ "$EXCL_ANGELEGT" = 1 ] && [ -f "$EXCL" ] && grep -q '^# k2-pi-paket BEGIN' "$EXCL"; then
  sed -i '/^# k2-pi-paket BEGIN$/,/^# k2-pi-paket END$/d' "$EXCL"; ok "K2-Einträge aus .git/info/exclude entfernt"
fi

schritt 6 "Abschluss"
if [ $STARTEN -eq 1 ]; then sudo -n systemctl start "$DIENST"; ok "$DIENST gestartet"
else ok "$DIENST bleibt gestoppt (sudo systemctl start $DIENST)"; fi
echo "ZURUECKGESPIELT=$(date +%Y%m%d-%H%M%S)" >> "$BK/state.env"
# 'letzte' entfernen, damit ein weiterer Aufruf nicht versehentlich einen älteren Installationsstand zurückholt
[ "$(readlink -f "$BACKUP_ROOT/letzte" 2>/dev/null)" = "$(readlink -f "$BK")" ] && rm -f "$BACKUP_ROOT/letzte"
[ -L "$BACKUP_ROOT/letzte" ] && [ $AELTESTE -eq 1 ] && rm -f "$BACKUP_ROOT/letzte"
echo
echo "Rückbau aus $BK fertig. Das Backup selbst bleibt liegen (manuell löschen: rm -rf $BACKUP_ROOT)."
