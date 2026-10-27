#!/bin/sh
# k2_config_sicherung.sh – tägliche Sicherung von printer_data/config
# (K2-CM5-Träger Alpha 1). Aufruf durch k2-config-sicherung.timer, von Hand:
#   sudo k2_config_sicherung.sh [--trocken]
# Einstellungen: /etc/default/k2_config_sicherung (Vorlage im Paket)
#   K2_BENUTZER            Klipper-Benutzer (Standard: User= aus klipper.service)
#   K2_SICHERUNG_ZIEL      Standard /daten/sicherung/config (Datenpartition)
#   K2_SICHERUNG_ANZAHL    so viele Sicherungen behalten (Standard 30)
#   K2_SICHERUNG_RSYNC_ZIEL  optional: Kopie auf einen PC per rsync/ssh,
#                          VORLAGE, z. B. <pc-benutzer>@<pc-name>:<ordner>/
# Unveränderte Konfig -> keine neue Datei (Prüfsumme über alle Dateien).
set -eu
TROCKEN=0
[ "${1:-}" = "--trocken" ] && TROCKEN=1
CONF=/etc/default/k2_config_sicherung
# shellcheck disable=SC1090
[ -r "$CONF" ] && . "$CONF"

U=${K2_BENUTZER:-$(systemctl show -p User --value klipper.service 2>/dev/null || true)}
[ -n "$U" ] || { echo "FEHLER: K2_BENUTZER unbekannt" >&2; exit 2; }
H=$(getent passwd "$U" | cut -d: -f6)
QUELLE=${K2_SICHERUNG_QUELLE:-$H/printer_data/config}
ZIEL=${K2_SICHERUNG_ZIEL:-/daten/sicherung/config}
ANZAHL=${K2_SICHERUNG_ANZAHL:-30}
RSYNC_ZIEL=${K2_SICHERUNG_RSYNC_ZIEL:-}

[ -d "$QUELLE" ] || { echo "FEHLER: $QUELLE fehlt" >&2; exit 2; }
case "$ZIEL" in
  /daten/*)
    if ! mountpoint -q /daten; then
      echo "WARN: /daten nicht eingehängt – Sicherung nach /var/backups/k2-config" >&2
      ZIEL=/var/backups/k2-config
    fi ;;
esac

SUMME=$(cd "$QUELLE" && find . -type f -print0 | sort -z | xargs -0 -r sha256sum | sha256sum | cut -c1-64)
LETZTE=$(cat "$ZIEL/.letzte-summe" 2>/dev/null || true)
STAMP=$(date +%Y%m%d-%H%M%S)
if [ "$SUMME" = "$LETZTE" ]; then
  echo "unverändert seit der letzten Sicherung – keine neue Datei"
else
  DATEI="$ZIEL/config-$STAMP.tar.gz"
  if [ "$TROCKEN" = 1 ]; then
    echo "TROCKEN: tar -czf $DATEI -C $(dirname "$QUELLE") $(basename "$QUELLE")"
  else
    mkdir -p "$ZIEL"
    tar -czf "$DATEI.tmp" -C "$(dirname "$QUELLE")" "$(basename "$QUELLE")"
    sync "$DATEI.tmp"
    mv "$DATEI.tmp" "$DATEI"
    printf '%s\n' "$SUMME" > "$ZIEL/.letzte-summe"
    sync "$ZIEL"
    echo "gesichert: $DATEI"
  fi
fi

# alte Sicherungen löschen (neueste $ANZAHL bleiben)
if [ "$TROCKEN" != 1 ] && [ -d "$ZIEL" ]; then
  ls -1 "$ZIEL"/config-*.tar.gz 2>/dev/null | sort -r | tail -n +"$((ANZAHL + 1))" \
    | while read -r alt; do rm -f -- "$alt"; echo "gelöscht: $alt"; done
fi

# optional: Kopie auf einen PC (Fehler hier lassen den Lauf nicht scheitern)
if [ -n "$RSYNC_ZIEL" ]; then
  if [ "$TROCKEN" = 1 ]; then
    echo "TROCKEN: rsync -a $ZIEL/ $RSYNC_ZIEL"
  elif rsync -a -e "ssh -o BatchMode=yes -o ConnectTimeout=10" "$ZIEL/" "$RSYNC_ZIEL"; then
    echo "rsync zum PC ok"
  else
    echo "WARN: rsync zum PC fehlgeschlagen (PC aus? ssh-Schlüssel?)" >&2
  fi
fi
