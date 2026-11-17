# Mainsail-Kiosk auf dem DSI-Display

Das Waveshare 4,3" DSI LCD (800 × 480, Touch) an der Trägerplatine (Stecker J12) zeigt nach dem Start Mainsail im
Vollbild. Ohne diese Einrichtung zeigt das Display nur die Textkonsole (tty1).

Dazu gehört die **Display-Ruhe**: Nach 10 Minuten ohne Berührung geht das Hintergrundlicht aus, die erste Berührung
schaltet es wieder ein.

Stand 17.11.2026: am Träger Alpha 1 in Betrieb (Raspberry Pi OS Lite, Debian 13, CM5). Abdunkeln geprüft; Aufwecken
per Touch noch nicht bestätigt.

## Dateien

| Datei hier | Ziel auf dem Pi | Aufgabe |
|---|---|---|
| `kiosk-start.sh` | `/home/pi/k2-kiosk/kiosk-start.sh` | wartet auf den Webserver, verhindert die Leiste „Wiederherstellen?“ nach hartem Ausschalten, startet Chromium im Kiosk-Modus |
| `k2-kiosk.service` | `/etc/systemd/system/` | startet den Kiosk-Compositor `cage` als User `pi` auf tty1 (ersetzt `getty@tty1`), startet nach Absturz neu |
| `display-ruhe.py` | `/home/pi/k2-kiosk/display-ruhe.py` | Wächter für die Display-Ruhe (Python, `python3-evdev`), läuft als root |
| `k2-display-ruhe.service` | `/etc/systemd/system/` | startet den Wächter beim Booten, nach Absturz neu |

Begriffe:

- **Kiosk-Modus:** Der Browser zeigt nur die eine Seite im Vollbild, ohne Adressleiste, Tabs oder Menüs.
- **cage:** ein Wayland-Compositor, der genau ein Programm im Vollbild zeigt, ohne Desktop, Fenster oder Leisten.
  Er braucht keinen Desktop und kein X11.
- **Hintergrundlicht:** Die LED-Beleuchtung hinter dem LCD. Ist sie aus, ist das Display schwarz, der Inhalt läuft
  aber weiter. Gesteuert über `/sys/class/backlight/0-0045/brightness` (0 = aus, 255 = voll; 0x45 ist die
  I2C-Adresse des Reglers im Display).

## Einrichtung

```sh
sudo apt-get install -y --no-install-recommends cage chromium fonts-dejavu-core python3-evdev
mkdir -p ~/k2-kiosk
cp kiosk-start.sh display-ruhe.py ~/k2-kiosk/
chmod +x ~/k2-kiosk/kiosk-start.sh ~/k2-kiosk/display-ruhe.py
sudo cp k2-kiosk.service k2-display-ruhe.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl disable --now getty@tty1
sudo systemctl enable --now k2-kiosk k2-display-ruhe
```

Voraussetzung: Der User `pi` ist in den Gruppen `video`, `render` und `input` (Standard bei Raspberry Pi OS),
in `config.txt` stehen die Display-Overlays (siehe `hardware/k2-pi-cm5-traeger/README.md`, Abschnitt „DSI-Display“).

### Adresse von Mainsail

Standard ist `http://localhost/`. Andere Adresse per Drop-in, Beispiel:

```sh
sudo mkdir -p /etc/systemd/system/k2-kiosk.service.d
printf '[Service]\nEnvironment=K2_KIOSK_URL=https://<hostname>/\n' | \
  sudo tee /etc/systemd/system/k2-kiosk.service.d/adresse.conf
sudo systemctl daemon-reload && sudo systemctl restart k2-kiosk
```

**Bei HTTPS mit eigener CA** (selbst ausgestelltes Zertifikat): Die Adresse muss genau ein Name aus dem Zertifikat
sein (`localhost` passt meist nicht, dann zeigt Chromium eine Warnung). Die CA trägt man in den Zertifikatsspeicher von
Chromium ein, statt die Prüfung abzuschalten:

```sh
sudo apt-get install -y --no-install-recommends libnss3-tools
mkdir -p ~/.pki/nssdb
certutil -d sql:$HOME/.pki/nssdb -N --empty-password      # nur beim ersten Mal
certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n "Lokale CA" -i ca.crt
cp ca.crt ~/k2-kiosk/k2-ca.crt                            # für die Warteschleife in kiosk-start.sh
```

## Display-Ruhe: wie der Wächter arbeitet

1. Er sucht das Touch-Gerät am Namen (`ft5x06`, Touch-Controller des Displays an i2c-0, Adresse 0x38) und merkt sich
   die Zeit der letzten Berührung.
2. **Abdunkeln:** Nach der Ruhezeit merkt er sich die aktuelle Helligkeit und schreibt `0` in
   `/sys/class/backlight/0-0045/brightness`. Gleichzeitig greift er das Touch-Gerät **exklusiv** (Linux
   `EVIOCGRAB`). Solange dieser Griff besteht, bekommt nur der Wächter die Berührungen, cage und Chromium bekommen
   nichts.
3. **Aufwecken:** Bei der ersten Berührung schreibt er die gemerkte Helligkeit zurück. Den Griff löst er erst, wenn
   der Finger wieder oben ist („Finger los“ = `BTN_TOUCH 0` bzw. `ABS_MT_TRACKING_ID −1`, danach 0,3 s Ruhe; kommen
   gar keine Meldungen mehr, nach 1 s). **Der Aufweck-Tipper kommt deshalb nicht in Mainsail an** und drückt
   versehentlich keinen Knopf. Erst die nächste Berührung bedient Mainsail.
4. Wenn der Dienst beendet wird oder das Touch-Gerät verschwindet, schaltet er das Licht wieder ein. So bleibt das
   Display nie dunkel hängen.

Als Aktivität zählt **nur eine Berührung des Displays**. Bedienung von Mainsail am PC oder Handy weckt das Display
nicht. Auch ein laufender Druck hält es nicht hell.

## Bedienung

| Aufgabe | Befehl |
|---|---|
| Kiosk neu starten | `sudo systemctl restart k2-kiosk` |
| Log Kiosk | `journalctl -u k2-kiosk -b` |
| Log Display-Ruhe | `journalctl -u k2-display-ruhe -b` (Einträge „dunkel (Helligkeit war …)“, „aufgeweckt“) |
| Ruhezeit ändern | in `k2-display-ruhe.service` die Zeile `Environment=K2_DISPLAY_RUHE_S=600` (Sekunden) anpassen, `sudo systemctl daemon-reload && sudo systemctl restart k2-display-ruhe` |
| Licht von Hand an | `echo 255 \| sudo tee /sys/class/backlight/0-0045/brightness` |
| Bildschirmfoto | `sudo apt-get install grim`, dann `XDG_RUNTIME_DIR=/run/user/1000 WAYLAND_DISPLAY=wayland-0 grim /tmp/kiosk.png` |
| Display-Ruhe aus (Licht immer an) | `sudo systemctl disable --now k2-display-ruhe` |
| Kiosk aus, Textkonsole zurück | `sudo systemctl disable --now k2-kiosk && sudo systemctl enable --now getty@tty1` |

Kurztest der Display-Ruhe, ohne die Einstellung dauerhaft zu ändern (`/run` ist flüchtig, gilt also nur bis zum
nächsten Neustart):

```sh
sudo mkdir -p /run/systemd/system/k2-display-ruhe.service.d
printf '[Service]\nEnvironment=K2_DISPLAY_RUHE_S=15\n' | \
  sudo tee /run/systemd/system/k2-display-ruhe.service.d/test.conf
sudo systemctl daemon-reload && sudo systemctl restart k2-display-ruhe
# beenden:
sudo rm /run/systemd/system/k2-display-ruhe.service.d/test.conf
sudo systemctl daemon-reload && sudo systemctl restart k2-display-ruhe
```
