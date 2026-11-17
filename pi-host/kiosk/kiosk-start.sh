#!/bin/sh
# Mainsail im Vollbild auf dem DSI-Display (Waveshare 4,3", 800x480).
# Gestartet von k2-kiosk.service innerhalb von cage.
#
# K2_KIOSK_URL  Adresse von Mainsail (Standard http://localhost/).
#               Bei HTTPS mit eigener CA: https://<Hostname aus dem Zertifikat>/
# ~/k2-kiosk/k2-ca.crt  optional: CA-Zertifikat; muss zusätzlich im NSS-Speicher
#               von Chromium (~/.pki/nssdb) eingetragen sein (siehe LIESMICH.md).
URL="${K2_KIOSK_URL:-http://localhost/}"
CA="$HOME/k2-kiosk/k2-ca.crt"
PREF="$HOME/.config/chromium/Default/Preferences"
# Nach hartem Ausschalten keine "Wiederherstellen?"-Leiste zeigen
if [ -f "$PREF" ]; then
  sed -i "s/\"exited_cleanly\":false/\"exited_cleanly\":true/; s/\"exit_type\":\"[A-Za-z]*\"/\"exit_type\":\"Normal\"/" "$PREF"
fi
# warten, bis der Webserver antwortet (max. 60 s)
if [ -f "$CA" ]; then PRUEF="--cacert $CA"; else PRUEF=""; fi
i=0; while [ $i -lt 60 ] && ! curl -s $PRUEF -o /dev/null "$URL"; do sleep 1; i=$((i+1)); done
exec /usr/bin/chromium --ozone-platform=wayland --kiosk --noerrdialogs --disable-infobars \
  --no-first-run --password-store=basic --disable-session-crashed-bubble \
  --disable-features=Translate,TranslateUI --overscroll-history-navigation=0 \
  --check-for-update-interval=31536000 --touch-events=enabled "$URL"
