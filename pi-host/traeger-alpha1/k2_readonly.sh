#!/bin/bash
# k2_readonly.sh – schreibgeschütztes System für den K2-CM5-Träger Alpha 1
#
#   Root (/) read-only mit RAM-Overlay (Paket overlayroot, eingerichtet über
#   „raspi-config nonint enable_overlayfs“), /boot/firmware read-only,
#   beschreibbare Datenpartition (ext4, Label K2DATEN) unter /daten mit
#   printer_data (Konfig, Moonraker-DB, Logs, gcodes) als Bind-Mount.
#
# Befehle (alle mit --trocken = nur anzeigen):
#   status                       Zustand anzeigen (ändert nichts)
#   pc-partitionieren GERÄT      NUR AM LINUX-PC, eMMC per rpiboot als GERÄT
#                                (z. B. /dev/sdX): Root auf 16 GiB verkleinern,
#                                Partition 3 = K2DATEN anlegen (README Schritt 3)
#   umziehen                     auf dem Pi: printer_data -> /daten, fstab,
#                                Bind-Mount, Klipper/Moonraker warten auf /daten
#   ein                          Overlay EIN (+ /boot/firmware ro) – Neustart nötig
#   aus                          Overlay AUS (zweistufig, s. Ausgabe) – Neustart nötig
#   update                       zeigt den Ablauf für Updates
#
# Umgebung: K2_BENUTZER (Standard: User= aus klipper.service, sonst SUDO_USER),
#           K2_DATEN=/daten, K2_LABEL=K2DATEN, K2_BOOT=/boot/firmware,
#           K2_ROOT_GIB=16 (nur pc-partitionieren)
#
# Mount-Optionen der Datenpartition (Begründung README-traeger-alpha1.md):
#   noatime,data=journal,commit=1,errors=remount-ro,nofail
set -u

TROCKEN=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --trocken) TROCKEN=1 ;;
    *) ARGS+=("$a") ;;
  esac
done
set -- "${ARGS[@]+"${ARGS[@]}"}"

DATEN=${K2_DATEN:-/daten}
LABEL=${K2_LABEL:-K2DATEN}
BOOT=${K2_BOOT:-/boot/firmware}
CMDLINE=$BOOT/cmdline.txt
ROOT_GIB=${K2_ROOT_GIB:-16}
MOUNT_OPT="defaults,noatime,data=journal,commit=1,errors=remount-ro,nofail,x-systemd.device-timeout=20s"
DIENSTE="klipper moonraker"
STAMP=$(date +%Y%m%d-%H%M%S)

say()  { printf '%s\n' "$*"; }
ok()   { printf 'OK   %s\n' "$*"; }
warn() { printf 'WARN %s\n' "$*" >&2; }
die()  { printf 'FEHLER %s\n' "$*" >&2; exit 1; }
tu() {
  if [ "$TROCKEN" = 1 ]; then printf 'TROCKEN: %s\n' "$*"; return 0; fi
  "$@"
}
tu_sh() {   # Befehl als Shell-Text (Umleitungen)
  if [ "$TROCKEN" = 1 ]; then printf 'TROCKEN: %s\n' "$1"; return 0; fi
  sh -c "$1"
}

benutzer() {
  local u="${K2_BENUTZER:-}"
  if [ -z "$u" ]; then
    u=$(systemctl show -p User --value klipper.service 2>/dev/null || true)
  fi
  [ -z "$u" ] && u="${SUDO_USER:-}"
  [ -z "$u" ] && die "Benutzer unbekannt – K2_BENUTZER=<name> setzen"
  printf '%s' "$u"
}
heim() { getent passwd "$1" | cut -d: -f6; }
root_noetig() { [ "$(id -u)" = 0 ] || [ "$TROCKEN" = 1 ] || die "als root starten (sudo)"; }
overlay_jetzt() { grep -q "overlayroot=" /proc/cmdline 2>/dev/null; }
overlay_konf()  { grep -q "overlayroot=" "$CMDLINE" 2>/dev/null; }

cmd_status() {
  local u h
  say "== K2-Träger: Schreibschutz-Status"
  if overlay_jetzt; then ok "Overlay AKTIV (Root schreibgeschützt, Änderungen nur im RAM)"
  else say "--   Overlay nicht aktiv (Root beschreibbar)"; fi
  if overlay_konf; then say "--   cmdline.txt: $(grep -o 'overlayroot=[^ ]*' "$CMDLINE")"
  else say "--   cmdline.txt: kein overlayroot"; fi
  if overlay_konf && ! grep -q 'overlayroot=[^ ]*recurse=0' "$CMDLINE"; then
    warn "overlayroot OHNE recurse=0 – dann landet auch /daten im RAM-Overlay! 'k2_readonly.sh ein' erneut ausführen"
  fi
  findmnt -n -o TARGET,SOURCE,FSTYPE,OPTIONS / 2>/dev/null | sed 's/^/--   /'
  findmnt -n -o TARGET,FSTYPE,OPTIONS "$BOOT" 2>/dev/null | sed 's/^/--   /'
  if findmnt -n "$DATEN" >/dev/null 2>&1; then
    findmnt -n -o TARGET,SOURCE,FSTYPE,OPTIONS "$DATEN" | sed 's/^/OK   /'
  else
    say "--   $DATEN nicht eingehängt (Partition $LABEL: $(blkid -L "$LABEL" 2>/dev/null || echo fehlt))"
  fi
  u=$(K2_BENUTZER=${K2_BENUTZER:-} benutzer 2>/dev/null) || u=""
  if [ -n "$u" ]; then
    h=$(heim "$u")
    if findmnt -n "$h/printer_data" >/dev/null 2>&1; then
      ok "$h/printer_data ist Bind-Mount: $(findmnt -n -o SOURCE "$h/printer_data")"
    else
      say "--   $h/printer_data liegt noch auf dem Root-Dateisystem"
    fi
  fi
}

# ---------------------------------------------------------------------
# NUR am PC: eMMC (per rpiboot als USB-Massenspeicher) umpartitionieren
# ---------------------------------------------------------------------
cmd_pc_partitionieren() {
  local dev="${1:-}" p1 p2 p3 n groesse_b
  [ -n "$dev" ] || die "Gerät fehlt: k2_readonly.sh pc-partitionieren /dev/sdX [--trocken]"
  [ -b "$dev" ] || die "$dev ist kein Blockgerät"
  root_noetig
  [ -e /proc/device-tree/model ] && grep -qi raspberry /proc/device-tree/model \
    && die "läuft auf dem Pi – das geht NUR am PC mit der eMMC per rpiboot"
  case "$dev" in *[0-9]) p1=${dev}p1; p2=${dev}p2; p3=${dev}p3 ;;
                 *) p1=${dev}1; p2=${dev}2; p3=${dev}3 ;; esac
  n=$(lsblk -n -o NAME "$dev" | tail -n +2 | wc -l)
  [ "$n" = 2 ] || die "$dev hat $n Partitionen (erwartet 2: boot + root) – abgebrochen"
  [ "$(lsblk -n -o FSTYPE "$p1")" = vfat ] || die "$p1 ist nicht vfat (boot)"
  [ "$(lsblk -n -o FSTYPE "$p2")" = ext4 ] || die "$p2 ist nicht ext4 (root)"
  lsblk -n -o MOUNTPOINTS "$dev" | grep -q . && die "Partitionen von $dev sind eingehängt – erst aushängen"
  groesse_b=$(blockdev --getsize64 "$dev")
  [ "$groesse_b" -gt $(( (ROOT_GIB + 8) * 1073741824 )) ] || die "$dev zu klein für $ROOT_GIB GiB Root + Daten"
  say "Gerät $dev ($(( groesse_b / 1073741824 )) GiB): $(lsblk -n -o MODEL "$dev" | head -1)"
  lsblk "$dev"
  if [ "$TROCKEN" != 1 ]; then
    printf 'ACHTUNG: %s wird umpartitioniert. Zum Bestätigen den Gerätenamen eintippen: ' "$dev"
    read -r best
    [ "$best" = "$dev" ] || die "nicht bestätigt"
  fi
  tu e2fsck -f -y "$p2"
  tu resize2fs "$p2" "$(( ROOT_GIB - 1 ))G"
  tu_sh "echo ', ${ROOT_GIB}GiB' | sfdisk -N 2 --no-reread $dev"
  tu partprobe "$dev"
  tu resize2fs "$p2"
  tu e2fsck -f -y "$p2"
  tu_sh "echo ',+,L' | sfdisk --append --no-reread $dev"
  tu partprobe "$dev"
  tu mkfs.ext4 -L "$LABEL" -E lazy_itable_init=0,lazy_journal_init=0 "$p3"
  tu tune2fs -o journal_data "$p3"
  tu e2fsck -f -y "$p3"
  [ "$TROCKEN" = 1 ] || lsblk -o NAME,SIZE,FSTYPE,LABEL "$dev"
  ok "fertig – eMMC zurück in den Träger, dann auf dem Pi: sudo k2_readonly.sh umziehen"
}

fstab_zeile_setzen() {   # $1 = Ziel (2. Feld), $2 = ganze Zeile
  if awk -v z="$1" '$1 !~ /^#/ && $2 == z {f=1} END {exit !f}' /etc/fstab; then
    ok "fstab: Eintrag für $1 schon vorhanden"
  else
    tu_sh "printf '%s\n' '$2' >> /etc/fstab"
  fi
}

cmd_umziehen() {
  local u h pd dropin d
  root_noetig
  u=$(benutzer); h=$(heim "$u"); pd=$h/printer_data
  [ -n "$h" ] && [ -d "$h" ] || die "Home von $u nicht gefunden"
  overlay_jetzt && die "Overlay ist aktiv – erst 'k2_readonly.sh aus' + Neustart"
  blkid -L "$LABEL" >/dev/null 2>&1 || die "Partition mit Label $LABEL fehlt (README Schritt 3: pc-partitionieren)"
  tu cp -a /etc/fstab "/etc/fstab.vor-k2daten-$STAMP"
  tu mkdir -p "$DATEN"
  fstab_zeile_setzen "$DATEN" "LABEL=$LABEL  $DATEN  ext4  $MOUNT_OPT  0  2"
  tu systemctl daemon-reload
  findmnt -n "$DATEN" >/dev/null 2>&1 || tu mount "$DATEN"
  if findmnt -n "$pd" >/dev/null 2>&1; then
    ok "$pd ist schon eingehängt – nichts umzuziehen"
  else
    [ -d "$pd" ] || die "$pd fehlt"
    for d in $DIENSTE; do tu systemctl stop "$d"; done
    tu mkdir -p "$DATEN/printer_data" "$DATEN/sicherung"
    tu rsync -aHAX "$pd/" "$DATEN/printer_data/"
    tu chown "$u:$u" "$DATEN/printer_data"
    tu mv "$pd" "$pd.vor-umzug-$STAMP"
    tu install -d -o "$u" -g "$u" "$pd"
  fi
  fstab_zeile_setzen "$pd" "$DATEN/printer_data  $pd  none  bind,nofail,x-systemd.requires-mounts-for=$DATEN  0  0"
  for d in $DIENSTE; do
    dropin=/etc/systemd/system/$d.service.d
    tu mkdir -p "$dropin"
    tu_sh "printf '[Unit]\n# k2_readonly: erst starten, wenn printer_data auf %s liegt\nRequiresMountsFor=%s\n' '$DATEN' '$pd' > $dropin/k2-daten.conf"
  done
  tu systemctl daemon-reload
  findmnt -n "$pd" >/dev/null 2>&1 || tu mount "$pd"
  if [ "$TROCKEN" != 1 ]; then
    [ -f "$pd/config/printer.cfg" ] || die "$pd/config/printer.cfg nach dem Umzug nicht sichtbar – Dienste NICHT gestartet"
  fi
  for d in $DIENSTE; do tu systemctl start "$d"; done
  ok "printer_data liegt auf $DATEN; alte Kopie: $pd.vor-umzug-$STAMP (nach Prüfung löschen)"
}

cmd_ein() {
  local u h mem
  root_noetig
  u=$(benutzer); h=$(heim "$u")
  overlay_jetzt && { ok "Overlay ist schon aktiv"; return 0; }
  findmnt -n "$DATEN" >/dev/null 2>&1 || die "$DATEN nicht eingehängt – erst 'k2_readonly.sh umziehen'"
  findmnt -n -o OPTIONS "$DATEN" | grep -q 'rw' || die "$DATEN ist nicht beschreibbar"
  findmnt -n "$h/printer_data" >/dev/null 2>&1 \
    || die "$h/printer_data liegt nicht auf $DATEN – sonst wäre die Konfig nach jedem Neustart weg"
  mem=$(awk '/MemTotal/{print $2}' /proc/meminfo)
  [ "$mem" -ge 1000000 ] || die "zu wenig RAM für das Overlay ($mem kB)"
  command -v raspi-config >/dev/null 2>&1 || die "raspi-config fehlt"
  tu mkdir -p "$DATEN/sicherung/system-$STAMP"
  tu cp -a "$CMDLINE" /etc/fstab "$DATEN/sicherung/system-$STAMP/"
  # 1) overlayroot installieren + cmdline (raspi-config, Bookworm/Trixie)
  tu raspi-config nonint enable_overlayfs
  # 2) recurse=0: sonst legt overlayroot auch über /daten ein RAM-Overlay
  #    (Standard recurse=1) – Daten wären nach dem Neustart weg
  tu mount -o remount,rw "$BOOT"
  tu sed -i -e 's/overlayroot=tmpfs\([^:]\|$\)/overlayroot=tmpfs:recurse=0\1/' "$CMDLINE"
  if [ "$TROCKEN" != 1 ]; then
    grep -q 'overlayroot=tmpfs:recurse=0' "$CMDLINE" || die "cmdline.txt ohne overlayroot=tmpfs:recurse=0 – bitte prüfen"
  fi
  # 3) Journal nur im RAM (Root ist ohnehin RAM-Overlay), begrenzt
  tu mkdir -p /etc/systemd/journald.conf.d
  tu_sh "printf '[Journal]\nStorage=volatile\nRuntimeMaxUse=64M\n' > /etc/systemd/journald.conf.d/k2-readonly.conf"
  # 4) Auslagerungsdatei im RAM-Overlay ist sinnlos
  if systemctl list-unit-files dphys-swapfile.service >/dev/null 2>&1; then
    tu systemctl disable dphys-swapfile.service
  fi
  # 5) /boot/firmware read-only (fstab), zuletzt
  tu raspi-config nonint enable_bootro
  ok "Overlay eingerichtet – jetzt neu starten: sudo reboot; danach: k2_readonly.sh status"
}

cmd_aus() {
  root_noetig
  if overlay_jetzt; then
    tu mount -o remount,rw "$BOOT"
    tu sed -i -e 's/overlayroot=[^ ]* \{0,1\}//' "$CMDLINE"
    ok "Overlay beim NÄCHSTEN Start aus. Jetzt: sudo reboot – danach 'k2_readonly.sh aus' NOCHMAL (macht /boot/firmware beschreibbar)"
    return 0
  fi
  if overlay_konf; then
    tu mount -o remount,rw "$BOOT"
    tu sed -i -e 's/overlayroot=[^ ]* \{0,1\}//' "$CMDLINE"
  fi
  tu raspi-config nonint disable_bootro
  tu mount -o remount,rw "$BOOT"
  ok "Root und /boot/firmware sind beschreibbar (Overlay aus). Updates jetzt möglich; danach: k2_readonly.sh ein + Neustart"
}

cmd_update() {
  cat <<EOF
Updates bei schreibgeschütztem Root (jeder Schritt mit sudo):
  1. k2_readonly.sh aus      -> sudo reboot
  2. k2_readonly.sh aus      (zweites Mal: /boot/firmware beschreibbar)
  3. apt update && apt full-upgrade; Klipper/Moonraker/Mainsail über die
     Update-Verwaltung in Mainsail; K2-Paket (install.sh) bei Bedarf
  4. k2_readonly.sh ein      -> sudo reboot
  5. k2_readonly.sh status   (Overlay AKTIV, $DATEN rw)
Kleine Änderungen an /etc ohne Neustart: sudo overlayroot-chroot
(Änderungen dort bleiben erhalten; danach exit).
EOF
}

case "${1:-}" in
  status) cmd_status ;;
  pc-partitionieren) shift; cmd_pc_partitionieren "${1:-}" ;;
  umziehen) cmd_umziehen ;;
  ein) cmd_ein ;;
  aus) cmd_aus ;;
  update) cmd_update ;;
  *) sed -n '2,24p' "$0"; exit 2 ;;
esac
