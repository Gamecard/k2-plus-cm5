# K2 Plus – Installationspaket für den Pi-Host

> **ALPHA – Stand 24.10.2026.** Was am echten Gerät lief und was nur in der Attrappe geprüft ist, steht in §6
> („Prüfstand“). Kurz: Schritte 1–6 + 8 (Erstinstallation, Wiederholung mit Serial-Pfaden, Material-DB,
> remain_material, Stock-Kalibrierung) sind am 22.10.2026 auf dem Pi des Autors gelaufen (EXIT 0). Der neue
> Schritt 7 (Start-Wächter, Creality-Brücke, nginx, Moonraker), `--bewegung-gesperrt`, die Übernahme der
> Freigaben und der Rückbau `uninstall.sh` sind **nur in der Attrappe** geprüft; die drei Dienst-/Web-Teile
> laufen am Pi des Autors seit 23./24.10. – dort aber **von Hand** eingerichtet.
> Einstieg für Nachbauer: [`SCHNELLSTART-K2-PLUS.md`](../../SCHNELLSTART-K2-PLUS.md) (Repo-Wurzel).

Richtet einen Pi (CM5, MainsailOS/Debian 13, aarch64) in **einem Schritt** ein: Upstream-Klipper auf den geprüften Stand `ee2c6fb` (ein neuerer Stand bleibt, §4), K2-Module nach `klippy/extras/`, Config nach `~/printer_data/config/`, Offline-Tests + Batch-Prüfung auf dem Pi, dazu (Standard an, abschaltbar) Start-Wächter, Creality-Brücke mit nginx-Upload-Block und die Moonraker-Einstellung für OrcaSlicer (§4e). Bewegt nichts, startet Klipper nur auf Wunsch. Grundlage: `pi-host/config-pi/README.md` §3 (Installation) und §4 (Inbetriebnahme).

## 1. Dateien

| Datei | Läuft auf | Zweck |
|---|---|---|
| `pack.sh` | Arbeits-PC | baut `k2-pi-paket-<datum>.tar.gz` (+ `.sha256`) aus dem aktuellen Stand |
| `install.sh` | Pi, als `pi` | Installation in 8 Schritten (s. §4) |
| `uninstall.sh` | Pi, als `pi` | Rückbau aus `~/k2-pi-backup/` |
| `k2cfg.py` | Pi | Hilfswerkzeug: serial-Pfade/SAVE_CONFIG beim erneuten Installieren übernehmen, Platzhalter melden (mit Adapter-Hinweis), unplausible `--*-serial`-Werte warnen, Kalibrierwerte aus dem SAVE_CONFIG-Block der Stock-`printer.cfg` prüfen/übernehmen (`stock-pruefen`/`stock-uebernehmen`, §4d) |
| `test_k2cfg.py` | Arbeits-PC/Pi | Offline-Test zur Stock-Übernahme (synthetische Daten; mit `KLIPPER_SRC` zusätzlich Abgleich gegen Upstream-`configfile.py`/`bed_mesh.py`/`shaper_defs.py`) |
| `k2-identify.py` | Pi, mit `~/klippy-env/bin/python` | MCU-Erkennung, **nur lesend**: fragt je seriellem Port das Klipper-Identify ab und sagt „Main-MCU“/„Düsen-MCU“ (§4b) |
| `test_k2_identify.py` | Arbeits-PC/Pi | Offline-Test zu `k2-identify.py` (Fake-`serialhdl`; optional echtes `serialhdl` gegen Fake-MCU am pty) |
| `k2-bootstart.py` | Pi, mit `~/klippy-env/bin/python` | Geräte aus dem Bootloader in die App starten, **vor** Klipper (nur Start, nie Update; Logik `nachbau/bootstart.py`) – **Gerätetest offen** (§4c) |
| `test_k2_bootstart.py` | Arbeits-PC/Pi | Offline-Test zu `k2-bootstart.py` + `nachbau/bootstart.py` (Fake-Bus/-UART, Sendeschutz, CLI, Paket) |
| `k2-bootstart.service` | Pi (Vorlage) | systemd-Einheit (oneshot, `Before=klipper.service`), nur über `install.sh --bootstart` |
| `k2_startwaechter.py` + `k2-startwaechter.service` | Pi, `/usr/bin/python3` (Vorlage) | Start-Wächter: `FIRMWARE_RESTART` über Moonraker, wenn der Drucker nach dem Pi bzw. bei laufendem Pi aus- und wieder eingeschaltet wurde oder `k2_box` nach dem Start nicht bereit wird; nie bei Sicherheitsabschaltung oder Druck (§4e) |
| `test_k2_startwaechter.py` | Arbeits-PC/Pi | Offline-Test der Entscheidungslogik + Abläufe „Drucker aus/an“ und „Motoren beim Einschalten verpasst (key798)“ |
| `k2_creality_bruecke.py` + `k2-creality-bruecke.service` | Pi, `~/moonraker-env/bin/python` (tornado, Vorlage) | Creality-Brücke: nimmt Orcas „CrealityPrint“-Druckstart (WebSocket Port 9999) an und startet die Datei über Moonraker (§4e) |
| `test_k2_creality_bruecke.py` | Arbeits-PC/Pi | Offline-Test der Pfad-/Auftragsauswertung (ohne tornado) |
| `k2web.py` | Pi | Hilfswerkzeug: Moonraker `[authorization] enable_api_key: False` und nginx-Upload-Block setzen/entfernen, idempotent (§4e) |
| `test_k2web.py` | Arbeits-PC/Pi | Offline-Test zu `k2web.py` + den beiden Dienst-Vorlagen |
| `test_k2_install.py` | Arbeits-PC (Pi möglich) | **Attrappe** für `install.sh`/`uninstall.sh`: Fake-HOME mit git-Klipper, Fake-`/etc`, Fake-`sudo`/`systemctl`/`nginx`/`id`; Schritt 7, Idempotenz, `--ohne-*`, `--bewegung-gesperrt`, Freigaben, Klipper-Rückschritt, Rückbau (§6). Braucht `git` |

## 2. Paket bauen (Arbeits-PC)

```bash
pi-host/install/pack.sh            # Ausgabe: lokal/pi-paket/ (git-ignoriert)
pi-host/install/pack.sh --tests    # vorher alle test_k2_*.py im Paket laufen lassen
pi-host/install/pack.sh -o /pfad   # anderer Ausgabeordner
pi-host/install/pack.sh --dicts ORDNER   # MCU-Dictionaries aus ORDNER (auch K2_MCU_DICT_DIR)
pi-host/install/pack.sh --ohne-dicts     # ohne MCU-Dictionaries (s. u.)
```

**MCU-Dictionaries:** Die beiden F008-Dictionaries (`mcu-main-F008-GD32F303RET6.dict.json`, `noz-duese-F008-GD32F303CBT6.dict.json`) stammen aus der Creality-MCU-Firmware und liegen **nicht** im öffentlichen Repo. `pack.sh` sucht sie standardmäßig im git-ignorierten Ordner `lokal/mcu-dicts/`, abweichend per `--dicts ORDNER` bzw. `K2_MCU_DICT_DIR`. Ohne sie: `--ohne-dicts` – dann überspringt `install.sh` die Batch-Prüfung (Warnung, die Config wird erst beim Klipper-Start geprüft) und die Dictionary-Tests (`test_k2_lis2dw`, `test_k2_safety`, Teile von `test_k2_identify`) melden SKIP.

**Lokaler Arbeitsordner `lokal/`** (Repo-Wurzel, git-ignoriert): `lokal/pi-paket/` (Paket-Ausgabe), `lokal/mcu-dicts/` (die beiden F008-Dictionaries), `lokal/upstream-klipper/` (Klon von Upstream-Klipper; `--tests` setzt ihn als `KLIPPER_SRC`), optional `lokal/leak-muster.txt` (eigene Leak-Muster, eine ERE pro Zeile; fehlt die Datei, prüft `pack.sh` nur die allgemeinen Muster).

Inhalt: `k2cfg.py` + `test_k2cfg.py`, `k2-identify.py` + `test_k2_identify.py`, `k2-bootstart.py` + `test_k2_bootstart.py` + `k2-bootstart.service`, Start-Wächter, Creality-Brücke, `k2web.py` (je mit Test, §1), `test_k2_install.py`, `pi-host/k2_*.py` (alle, z. B. `k2_konsole.py` – Pflicht für die übrigen Module –, `k2_dateipruefung.py`, `k2_resume.py`, `k2_cfs_sensor.py`, `k2_heater_pid.py`, `k2_lis2dw.py`), `pi-host/test_k2_*.py` (+ `prtouch_mcu_formats.json`, Modul-READMEs), `pi-host/config-pi/` (cfg + README), `nachbau/` (+ `ref-hi/`), `belt_mdl-live.py` (GPLv3-Original für den Belt-Vergleichstest), `mcu-dicts/` (F008-Dictionaries Main/Düse für Batch + lis2dw-Test, s. o.), Skripte, `PAKET-INFO.txt`, `SHA256SUMS`. `pack.sh` prüft außerdem, dass die Dienst-Einheiten Vorlagen ohne feste Pfade/Benutzer sind.
**Nicht** enthalten: Logs, Arbeits-/Zugangsnotizen, **Creality-Materialdatenbank** (`material_database*.json`, s. §4a), **Düsen-Material-Zustand** (`remain_material*.json`, s. §4a). `pack.sh` bricht bei Zugangsdaten-/IP-Mustern im Inhalt ab, ebenso wenn eine Material-DB dem Namen nach (Paketordner und Tarball) oder dem Inhalt nach (JSON mit `meterialType`/`printerIntName`/`kvParam`) hineingeriete; dasselbe für `remain_material*.json` (Name bzw. JSON mit Schlüssel `"remain_material"`) und für eine **Stock-`printer.cfg`** (Datei mit SAVE_CONFIG-Abschnitt `#*# [auto_addr]` = Geräte-Kennungen, s. §4d).

## 3. Auf den Pi kopieren und starten

```bash
scp lokal/pi-paket/k2-pi-paket-<datum>.tar.gz pi@<pi-host>:~
ssh pi@<pi-host>
tar xzf k2-pi-paket-<datum>.tar.gz
cd k2-pi-paket-<datum>
./install.sh                     # Standard: printer.cfg bleibt, K2-Datei als printer-k2.cfg
```

Typischer Ablauf am Drucker (Variante B: Main-/Düsen-MCU an den Pi-UARTs über das FFC-Breakout, Overlays `uart4-pi5`/`uart2-pi5` in `/boot/firmware/config.txt`; RS485 über USB-Adapter – s. `PI-ANSCHLUSS-LOWERBOARD.md`, `SCHNELLSTART-K2-PLUS.md` §3):

```bash
ls -l /dev/ttyAMA* /dev/serial/by-id/            # Namen ablesen
sudo systemctl stop klipper                      # Ports freigeben
~/klippy-env/bin/python ./k2-identify.py /dev/ttyAMA4 /dev/ttyAMA2   # welche MCU an welchem Port? (nur lesend, §4b)
./install.sh --printer-cfg-ersetzen --bewegung-gesperrt \
  --main-serial  /dev/ttyAMA4 \
  --duese-serial /dev/ttyAMA2 \
  --rs485-serial /dev/serial/by-id/usb-...-if00-port0
sudo systemctl start klipper                     # oder gleich mit --starten
```

Variante A (Rückfallweg, Main-MCU über USB-RS232-Konverter an J22): `--main-serial /dev/serial/by-id/usb-FTDI_...-if00-port0`.

## 4. Was `install.sh` tut

| Schritt | Inhalt | Abbruch bei |
|---|---|---|
| 1 Vorprüfungen | nicht root, `aarch64`, `SHA256SUMS`, `~/klipper` ist git-Checkout, `~/printer_data/config` da, ≥ 200 MB frei, Python (`~/klippy-env`), `sudo -n`, Dienst `klipper.service`; mit `--material-db`: Datei lesbar, gültiges JSON, Struktur (`nachbau/material_db.py --struktur`, ≥ 1 Eintrag); mit `--remain-material`: Datei lesbar, gültiges JSON, Struktur + Farbformat (§4a); mit `--stock-printer-cfg`: SAVE_CONFIG-Block vorhanden und lesbar, mindestens ein Abschnitt übernehmbar, Ausgabe je Abschnitt (§4d); Backup-Ordner `~/k2-pi-backup/<stamp>/` | jeder Fehlschlag (vor jeder Änderung) |
| 2 Klipper | HEAD ≠ `ee2c6fb` → nur bei **sauberem** Checkout `git checkout --detach ee2c6fb` (fehlt der Commit: `git fetch origin`); eigene Dateien zählen nicht als Änderung. Ist HEAD **neuer** und enthält `ee2c6fb` (z. B. nach einem Klipper-Update mit dem PID-Fix `e761a2c76`), bleibt er (Warnung; Rückschritt nur mit `--klipper-auf-zielstand`). `.git/info/exclude` bekommt `klippy/extras/k2_*.py` + `k2_nachbau/` (Moonraker sieht den Checkout als sauber). | lokale Änderungen (Liste wird gezeigt) |
| 3 Dienst | `sudo systemctl stop klipper` (Zustand gemerkt); danach – nur mit `--main-serial auto`/`--duese-serial auto`/`--serial-erkennen` – MCU-Erkennung per `k2-identify.py` (nur `identify`, Protokoll `<backup>/identify.txt`) | stop scheitert; `auto` ohne eindeutigen Treffer (vor Modulen/Config, Dienst bleibt gestoppt) |
| 4 Module | Backup vorhandener `extras/k2_*.py` + `k2_nachbau/`; `pi-host/k2_*.py` → `extras/` (**`k2_motor_init.py` → `k2_motor.py`**, Dateiname = Abschnitt `[k2_motor]`), `nachbau/*.py` → `extras/k2_nachbau/`; jede Kopie per `cmp` geprüft. Mitinstalliert werden auch `k2_lis2dw.py` (Pflicht, `[k2_lis2dw]` in printer.cfg) und `k2_zalign.py` (nur aktiv, wenn `[k2_zalign]` in zalign.cfg einkommentiert wird) | Kopierfehler |
| 5 Config | `~/printer_data/config` **komplett** als `config.tar.gz` gesichert; alle `config-pi/*.cfg` außer `printer.cfg` kopiert; `printer.cfg`: keine vorhanden → wird `printer.cfg`; vorhanden → nur mit `--printer-cfg-ersetzen` ersetzt, sonst `printer-k2.cfg` daneben + Hinweis; mit `--stock-printer-cfg` danach Kalibrierwerte in den SAVE_CONFIG-Block dieser Hauptdatei (§4d, Protokoll `<backup>/stock-import.txt`); mit `--bewegung-gesperrt` danach `homing.cfg` `_PI_FREIGABE variable_allow_move: False`; Material-DB s. §4a | Stock-Übernahme scheitert (Ziel-Block beschädigt) |
| 6 Prüfung | alle `test_k2_*.py` (mit `KLIPPER_SRC=~/klipper`) + `test_k2_identify.py` + `test_k2_bootstart.py` + `test_k2cfg.py` + `test_k2web.py` + `test_k2_startwaechter.py` + `test_k2_creality_bruecke.py`, nachbau-Selbsttests (inkl. `bootstart`), Batch-Lauf `klippy.py <cfg> -i /dev/null -d <dicts>` (wie Lauf F der Offline-Prüfung) gegen die **installierten** Module + Config; Logs in `<backup>/tests/`, `<backup>/batch/` | ein Test rot / Batch ≠ EXIT 0 → Dienst bleibt gestoppt |
| 7 Dienste/Web | (§4e, je abschaltbar) Moonraker `enable_api_key: False` (+ Neustart nur bei Änderung); nginx-Upload-Block vor `location /websocket` (Sicherung, `nginx -t`, `reload`); Start-Wächter + Creality-Brücke: Programm nach `~/k2-tools/`, Einheit aus Vorlage nach `/etc/systemd/system/`, `enable`, (Neu-)Start nur bei Änderung oder wenn sie nicht laufen. Vorher-Zustand in `state.env`, Kopien in `<backup>/{moonraker,nginx,dienste,k2-tools}/` | `nginx -t` scheitert (Original wird zurückgespielt), Einheit/`enable`/`restart` scheitert |
| 8 Abschluss | nur mit `--bootstart`: Trockenlauf mit den Ports aus der Config, Werkzeug nach `~/k2-bootstart/`, Einheit nach `/etc/systemd/system/` + `enable` (§4c); mit `--bootstart-aus`: `disable`. Dienst nur mit `--starten`; Zusammenfassung, `ls -l /dev/serial/by-id/`, verbleibende Platzhalter, nächste Schritte §4 | `--bootstart`, aber in der Config nur Platzhalter-Ports (nichts an der Einheit verändert) |

**Erneut ausführen** ist ausdrücklich vorgesehen (z. B. nach neuem Paket): eingetragene `serial:`-Pfade (`[mcu]`, `[mcu nozzle_mcu]`, `[k2_485]`) werden aus den bisherigen Dateien übernommen, ebenso von Hand gesetzte **Inbetriebnahme-Schalter** (`allow_write`/`allow_move` in `[k2_box]`, `[k2_motor]`, `[k2_prtouch]`, `[k2_belt mdlx/mdly]`, `variable_allow_move` in `[gcode_macro _PI_FREIGABE]`, `[k2_motor] auto_init`/`stall_monitor`; jede Übernahme wird gemeldet) – ein neuer Lauf setzt einen in Betrieb genommenen Drucker also nicht auf „gesperrt“ zurück. Andere Handänderungen in `config-pi`-Dateien (Makros, Grenzen …) werden durch den Paketstand ersetzt (alte Datei im Backup); beim Ersetzen einer bisherigen K2-`printer.cfg` bleibt deren SAVE_CONFIG-Block (Kalibrierung) erhalten, außer mit `--save-config-aus-paket`. Kalibrierwerte der **Stock**-`printer.cfg` kommen nur mit `--stock-printer-cfg` hinzu (§4d); im Pi abweichend kalibrierte Werte bleiben dabei, außer mit `--stock-ueberschreiben`. Jeder Lauf legt ein neues Backup an (zwei Läufe in derselben Sekunde: Suffix `-2`, `-3` …; vor dem 17.10.2026 überschrieb der zweite das erste).

### Optionen

| Option | Wirkung |
|---|---|
| `--klipper-nicht-anfassen` | Checkout nicht umstellen (nur Warnung, Stand dann ungeprüft) |
| `--klipper-auf-zielstand` | auch einen **neueren** Checkout auf `ee2c6fb` zurückstellen (Standard: neuerer Stand bleibt) |
| `--printer-cfg-ersetzen` | vorhandene `printer.cfg` ersetzen (Original im Backup) |
| `--save-config-aus-paket` | SAVE_CONFIG-Block des Pakets statt des bisherigen |
| `--main-serial` / `--duese-serial` / `--rs485-serial PFAD` | Pfade direkt eintragen: Variante B `/dev/ttyAMA4` (Main) / `/dev/ttyAMA2` (Düse), RS485 `/dev/serial/by-id/…` (Hinweis bei `ttyUSBn`/`ttyACMn`, bei RS485 an `ttyAMA` und bei by-id-`[mcu]` ohne `FTDI`) |
| `--main-serial auto` / `--duese-serial auto` | Port per `k2-identify.py` ermitteln und eintragen; ohne eindeutigen Treffer Abbruch (§4b) |
| `--serial-erkennen` | `k2-identify.py` laufen lassen, Ergebnis + Vorschlag zeigen, **nichts** eintragen |
| `--material-db DATEI` | Creality-Materialdatenbank nach `~/printer_data/config/k2/material_database.json` kopieren (§4a) |
| `--remain-material DATEI` | Stock-`remain_material_data.json` (Material in der Düse) als Startwert nach `~/printer_data/config/k2/remain_material.json` kopieren (§4a) |
| `--stock-printer-cfg DATEI` | Kalibrierwerte aus dem SAVE_CONFIG-Block der **Original**-`printer.cfg` in den SAVE_CONFIG-Block der neuen Pi-`printer.cfg` übernehmen: `[box] cut_pos_x` → `[k2_box]`, `[input_shaper]`, `[bed_mesh <profil>]`; `[auto_addr]`, Heizungs-/prtouch-Werte und Unbekanntes werden ausgelassen und gemeldet (§4d). Prüfung in Schritt 1 |
| `--stock-ueberschreiben` | nur mit `--stock-printer-cfg`: gleichnamige Abschnitte mit **anderen** Werten im Pi-Block durch die Stock-Werte ersetzen (Standard: Pi-Werte behalten + Warnung) |
| `--bootstart` | `k2-bootstart.service` installieren und **aktivieren** (läuft bei jedem Boot vor Klipper, §4c). **Gerätetest offen – nur mit ausdrücklicher Freigabe**, erst nach einem erfolgreichen Handlauf. Standard: AUS |
| `--bootstart-aus` | `k2-bootstart.service` deaktivieren (Datei bleibt liegen) |
| `--ohne-startwaechter` | Start-Wächter nicht einrichten/ändern (Standard: einrichten, §4e) |
| `--ohne-creality-bruecke` | Creality-Brücke **und** nginx-Upload-Block nicht einrichten/ändern (Standard: einrichten, §4e) |
| `--ohne-moonraker-anpassung` | `moonraker.conf` nicht ändern (Standard: `enable_api_key: False`, §4e) |
| `--bewegung-gesperrt` | `homing.cfg` `_PI_FREIGABE variable_allow_move: False` (G28/Bewegung gesperrt; Freigabe zur Laufzeit mit `ALLOW_MOTION`). Empfohlen für die **Erst**-Inbetriebnahme – das Paket liefert `True` (Stand des Autors nach der Inbetriebnahme). Bleibt bei späteren Läufen erhalten |
| `--dienst NAME` | anderer systemd-Dienst als `klipper` |
| `--starten` | Klipper am Ende starten |
| `--ohne-tests` / `--ohne-batch` | Prüfungen auslassen |
| `--arch-egal` | nur für Probeläufe auf x86_64 |

Umgebungsvariablen für abweichende Installationen: `KLIPPER_DIR`, `CONFIG_DIR`, `KLIPPY_ENV`, `BACKUP_ROOT`, `BOOTSTART_DIR` (Werkzeugordner für `--bootstart`, Standard `~/k2-bootstart`), `K2_TOOLS_DIR` (Standard `~/k2-tools`), `MOONRAKER_CONF` (Standard `~/printer_data/config/moonraker.conf`), `MOONRAKER_ENV` (`~/moonraker-env`), `MOONRAKER_DIENST` (`moonraker`), `NGINX_SITE` (`/etc/nginx/sites-available/mainsail`), `NGINX_BIN`, `NGINX_DIENST` (`nginx`), `SYSTEMD_DIR` (`/etc/systemd/system`). Nur für die Attrappe: `BOOTSTART_UNIT`, `K2_ZIEL_COMMIT`.

### Wo die Port-Namen hinkommen

| Gerät | Port (Variante B) | Datei | Abschnitt |
|---|---|---|---|
| Main-MCU (Pi-UART4, Breakout 30/32 ↔ Pi-Pin 32/33) | `/dev/ttyAMA4` | `printer.cfg` | `[mcu] serial:` |
| Düsen-MCU (Pi-UART2, Breakout 26/28 ↔ Pi-Pin 7/29) | `/dev/ttyAMA2` | `printer.cfg` | `[mcu nozzle_mcu] serial:` |
| USB-RS485-Adapter | `/dev/serial/by-id/usb-…` | `k2-module.cfg` | `[k2_485] serial:` |
| nur Variante A: USB-RS232-Konverter (FTDI) an J22 Pin 10/4 = Main-MCU | `/dev/serial/by-id/usb-FTDI_…` | `printer.cfg` | `[mcu] serial:` |

`baud: 230400` bleibt überall. Welcher Port welche MCU ist: `k2-identify.py` (§4b); ohne Antwort hilft nacheinander anstecken und `ls -l /dev/serial/by-id/` vergleichen.

## 4b. MCU-Erkennung (`k2-identify.py`)

```bash
sudo systemctl stop klipper                       # sonst sind die Ports belegt
~/klippy-env/bin/python ./k2-identify.py          # alle /dev/serial/by-id/*
~/klippy-env/bin/python ./k2-identify.py /dev/serial/by-id/usb-FTDI_...-if00-port0
~/klippy-env/bin/python ./k2-identify.py --baud 230400,250000,115200 --timeout 3
```

- **Nur lesend:** je Port/Baud wird über Klippers `serialhdl` ausschließlich `identify` gesendet (wie beim Verbindungsaufbau). Kein `get_config`/`config`, kein `reset`, keine stk500-/DTR-Reset-Sequenz (anders als Klippers `connect_uart`). Port wie bei Klipper exklusiv geöffnet, danach geschlossen; Prozessende per `os._exit`.
- **Belegt:** Hat ein anderer Prozess den Port offen (`/proc/*/fd`, z. B. laufender Klipper) oder ist er gesperrt, wird er **nicht** angefasst → Meldung „BELEGT … Klipper läuft?“.
- **Ausgabe je Port:** Baud, `MCU`, `CLOCK_FREQ`, `SERIAL_BAUD`, Firmware-Version, Anzahl Befehle/Antworten, Erkennung mit Gründen. Am Ende Zusammenfassung + Vorschlag `./install.sh --main-serial … --duese-serial …` (nichts eingetragen).
- **Erkennung:** Konstante `MCU` (`gd32f303xe` = Main GD32F303RET6, `gd32f303xb` = Düse GD32F303CBT6), Pins (`PD*` nur beim 64-Pin-Main), Build-Version und `INITIAL_PINS` gegen die bekannten Dictionaries (`mcu-dicts/`, `pi-host/prtouch_mcu_formats.json`, eingebaute F008-Werte). Der **Befehlssatz trennt nicht**: Main- und Düsen-Firmware haben dieselben 75 Befehle (inkl. prtouch, z_align, Stepper) – er dient nur als K2-Plausibilität (Fremd-Firmware → „UNKLAR“). Widersprüchliche Merkmale → „UNKLAR“ mit Hinweis.
- **Keine Antwort:** Hinweise auf Kontakt, gekreuztes TX/RX (Konverter-RXD ← Pin 10, TXD → Pin 4, GND 2/8/11), Pegel (J22 = RS232, kein TTL-Adapter), Baudrate.
- **Grenzen:** `--timeout` je Versuch (Standard 5 s), `--gesamt-timeout` (Standard 90 s, danach Exit 3). Exit 0 = MCU erkannt, 1 = keine Antwort/nichts eindeutig, 2 = Aufruf-/Umgebungsfehler. Mit `/usr/bin/python3` gestartet, wechselt das Skript selbst auf `~/klippy-env/bin/python`, falls Klipper-Module fehlen.
- `install.sh --serial-erkennen` zeigt dasselbe im Installationslauf (nach dem Stoppen von Klipper); `--main-serial auto` trägt einen **eindeutigen** Treffer ein (`--ergebnis`-Datei im Backup), sonst Abbruch, bevor Module/Config angefasst werden.
- Geprüft offline (`test_k2_identify.py`): Fake-`serialhdl` (Main/Düse/stumm/gesperrt/belegt/Fremd/Baudliste/Gesamt-Timeout/`--ausgabe-rolle`/`--ergebnis`) und echtes Upstream-`serialhdl` gegen eine Fake-MCU am pty (empfängt nachweislich nur `identify`). Am echten Konverter noch UNGETESTET.

## 4c. Geräte aus dem Bootloader starten (`k2-bootstart.py`)

> **GERÄTETEST OFFEN – nur mit ausdrücklicher Freigabe.** Offline geprüft (`test_k2_bootstart.py`), am Drucker nie
> gelaufen. Hintergrund und Belege: [`BOOTSTART-WERKZEUG.md`](../../BOOTSTART-WERKZEUG.md) (Repo-Wurzel).

Das Stock-Upper-Board startet bei **jedem** Boot vor Klipper alle Geräte aus dem Bootloader in die App:
- Main- und Düsen-MCU per UART (`75`, `00 FF`, `02 FD`).
- Motoren, Belts und CFS per RS485 (A1-Discovery, A0-Adressvergabe, F0 `[00]`/`[03]`/`[02]`).

`k2-bootstart.py` ist der Nachbau **nur dieses Start-Teils**. Es wird nie geflasht, gelöscht oder in den Bootloader
geschickt. Ob die Geräte ohne Stock-Board von selbst in die App springen, ist offen.

```bash
sudo systemctl stop klipper
~/klippy-env/bin/python ./k2-bootstart.py --dry-run --main <port> --duese <port> --rs485 <port>  # nur Plan + Bytes
~/klippy-env/bin/python ./k2-bootstart.py --nur-erkennen --rs485 <port>     # nur A1: wer steht im Loader?
~/klippy-env/bin/python ./k2-bootstart.py --main <port> --duese <port> --rs485 <port>
~/klippy-env/bin/python ./k2-bootstart.py --aus-config ~/printer_data/config  # Ports aus printer.cfg/k2-module.cfg
```

- **UART (je `--main`/`--duese`):**
  1. Zuerst Klipper-`identify` @230400 (wie `k2-identify.py`). Antwortet die App, geht an diesen Port **nichts** weiter.
  2. Sonst @115200: `75` → `75`, `00 FF` → 25 Byte + `~Σ`.
  3. Nur bei gültiger Version mit Präfix `mcu0_`/`noz0_`: `02 FD` → `75 8A`.
  4. Danach erneut identify.
- **RS485 (`--rs485`, 230400):**
  1. A1 an 0xFD (Motoren), 0xFC (Belts), 0xFE (CFS), je bis 3 Runden à 2 s.
  2. **Nur Geräte mit mode 1 (Loader):** A0 mit den Stock-Loader-Adressen (Motor 0x85–0x88, Belt 0x23/0x24, CFS 0x01–0x04),
     dann F0 `[00]` Version, F0 `[03]` Sektor, F0 `[02]` Start (nur bei gültiger Version).
  3. Nachkontrolle F0 `[00]`.
  4. Geräte in der App (mode 0) werden nicht angefasst. `--ohne-motoren`/`--ohne-belts`/`--ohne-cfs` lassen eine Gruppe aus.
- **Sendeschutz:** eigene, enge Whitelist zusätzlich zum unveränderten Standard-Sendeschutz.
  - RS485: nur A1 `[g g]`, A0 an Loader-Adressen für in diesem Lauf gemeldete Loader-UIDs, F0 `[00]/[03]/[02]` an in
    diesem Lauf bestätigte Adressen.
  - Hart gesperrt: F0 `[01]`, Länge, Datenblöcke, F0 `[06]`, fun 0xF2, Ziel 0xF0, 0x0B, 0x01, 0x03/0x04/0x07.
  - UART: nur `75`, `00 FF`, `02 FD`.
  - `--nur-erkennen` sperrt zusätzlich `02 FD`, A0 und F0.
- **Vorprüfung:** Ports vorhanden (`--warte-auf-ports S`), nicht belegt (Klipper läuft?), nicht doppelt. Sonst Exit 2,
  ohne irgendetwas zu senden.
- **Exit:** 0 = alles in der App, 1 = etwas nicht gestartet/stumm bzw. (mit `--nur-erkennen`) im Loader,
  2 = Aufruf-/Umgebungsfehler, 3 = Gesamt-Timeout (`--gesamt-timeout`, Standard 240 s), 4 = Sendeschutz (interner Fehler).
- **Automatisch beim Boot (Standard AUS):** `./install.sh --bootstart [übrige Optionen]`.
  - Kopiert `k2-bootstart.py`, `k2-identify.py`, `k2cfg.py`, `nachbau/bootstart.py` und `nachbau/serial485.py` nach
    `~/k2-bootstart/`.
  - Prüft vorher per Trockenlauf, dass in der Config mindestens ein echter Port steht.
  - Installiert und aktiviert `/etc/systemd/system/k2-bootstart.service` (oneshot, `Before=klipper.service`, liest die
    Ports bei jedem Boot aus der Config, wartet bis 30 s auf die Ports).
  - Ein Fehlschlag hält Klipper nicht auf. Log: `journalctl -u k2-bootstart`. Abschalten: `./install.sh --bootstart-aus`.
  - Rückbau über `uninstall.sh` (§5).
- **Reihenfolge am Gerät:** PI-INBETRIEBNAHME 1.1/2.5 und `BOOTSTART-WERKZEUG.md` §7. Erst `--dry-run`, dann
  `--nur-erkennen`, dann ein echter Lauf je Teil. `--bootstart` erst danach.

## 4a. Materialdatenbank (`material_database.json`)

`[k2_box]` liest Temperatur/Geschwindigkeit je Material aus der Creality-Datenbank (wie Stock). Die Datei ist eine Creality-Datei bzw. eine Benutzer-Kopie mit Cloud-Kennungen und kommt deshalb **nie ins Paket/Repo**. Sie wird einmal vom Stock-Drucker geholt (solange das Stock-Upper-Board noch läuft) und bei der Installation mitgegeben:

```bash
# am Arbeits-PC oder Pi, Stock-Drucker im Netz:
scp root@<drucker>:/mnt/UDISK/creality/userdata/box/material_database.json .
#   (diese Benutzer-Kopie liest der Stock-Host-Code; fehlt sie, den Firmware-Default
#    /etc/sysConfig/defData/material/material_database.json holen)
./install.sh --material-db material_database.json [übrige Optionen]
```

- Ziel fest: `~/printer_data/config/k2/material_database.json` (Rechte 0644, Ordner `k2/` wird bei Bedarf angelegt). Diesen Pfad trägt `k2-module.cfg` bei `[k2_box]` ein.
- Prüfung in Schritt 1, also **vor jeder Änderung**: gültiges JSON, Struktur über `nachbau/material_db.py --struktur` (`result.list` vorhanden, ≥ 1 Eintrag); Zählwerte (Einträge, Doppel-IDs, Einträge ohne `nozzle_temperature`) werden angezeigt. Kaputte Datei → Abbruch, nichts verändert.
- Eine vorhandene DB wird nach `<backup>/material-db/` gesichert (zusätzlich zu `config.tar.gz`); identische Datei → bleibt unverändert.
- **Ohne** `--material-db` bleibt eine vorhandene DB unverändert (sie wird nur geprüft, kaputt → Warnung). Fehlt sie, meldet die Zusammenfassung den `scp`-Befehl; `k2_box` arbeitet dann mit den Festwerten.
- Ein von Hand geänderter Wert `material_database:` in `[k2_box]` (`k2-module.cfg`) bleibt bei Wiederholung erhalten (`k2cfg.py`), solange die Paket-Datei die Zeile aktiv enthält.
- Rückbau: Die DB wird auf den Stand des gewählten Backups gebracht – gesicherte Datei zurück, bzw. entfernt (+ leerer Ordner `k2/`), wenn es sie vor diesem Lauf nicht gab (auch mit `--aelteste`).

### Material in der Düse (`remain_material.json`)

Stock merkt sich nach jedem erfolgreichen Spülen Farbe und Typ des Materials in der Düse (`RemainMaterial`). `[k2_box]` liest/schreibt dasselbe Format über die Option `remain_material_file` (Default `~/printer_data/config/k2/remain_material.json`). Fehlt die Datei, ist das Düsenmaterial nach dem Start unbekannt und der erste Wechsel spült pauschal (`Tn_extrude`). Den letzten Stock-Stand kann man einmalig als Startwert übernehmen:

```bash
scp root@<drucker>:/mnt/UDISK/creality/userdata/box/remain_material_data.json .
./install.sh --remain-material remain_material_data.json [übrige Optionen]
```

- Format (Stock, eine Zeile): `{"remain_material": {"color": "0RRGGBB"|null, "type": "<id>"|null}}`. Ziel fest: `~/printer_data/config/k2/remain_material.json` (anderer Dateiname als am Drucker, Inhalt 1:1, Rechte 0644, Ordner `k2/` bei Bedarf angelegt).
- Prüfung in Schritt 1, **vor jeder Änderung**: gültiges JSON, oberste Ebene Objekt mit `remain_material` als Objekt, darin `color` und `type` vorhanden; `color` = `null` oder `^0[0-9A-Fa-f]{6}$`, `type` = `null` oder nicht-leerer Text; Zusatzschlüssel (z. B. `tnn`) erlaubt. Sonst Abbruch, nichts verändert. `color`/`type` werden angezeigt; beide `null` (Stock-Grundzustand) ist gültig, bringt aber nichts.
- Eine vorhandene Datei wird nach `<backup>/remain-material/` gesichert; identische Datei → unverändert; abweichende → ersetzt (mit Warnung, weil sie im Betrieb von `k2_box` geschrieben sein kann).
- **Ohne** `--remain-material` wird eine vorhandene Datei **nie** angefasst – sie gehört im Betrieb `k2_box` (nur geprüft, unbrauchbar → Warnung). Fehlt sie oder ist sie unbrauchbar, nennt die Zusammenfassung den `scp`-Befehl. Die Option ist also nur für den Umstieg sinnvoll; ein späterer Lauf mit derselben alten Stock-Datei würde den inzwischen von `k2_box` gespeicherten Stand zurückdrehen.
- Kommt nie ins Paket (s. §2).
- Rückbau: wie bei der DB auf den Stand des gewählten Backups (gesicherte Datei zurück bzw. entfernt, wenn es sie vor diesem Lauf nicht gab). ACHTUNG: Das gilt auch für eine Datei, die `k2_box` erst im Betrieb angelegt hat – `--aelteste` entfernt sie.

## 4d. Kalibrierwerte aus der Stock-`printer.cfg` (`--stock-printer-cfg`)

Der SAVE_CONFIG-Block der Original-`printer.cfg` enthält die am Stock-Drucker kalibrierten Werte. `install.sh` übernimmt sie auf Wunsch in den SAVE_CONFIG-Block der neuen Pi-`printer.cfg` (bisher ging das nur von einer früheren Pi-Installation). Die Datei einmal vom Stock-Drucker holen, solange das Stock-Upper-Board noch läuft:

```bash
scp root@<drucker>:/mnt/UDISK/printer_data/config/printer.cfg stock-printer.cfg
./install.sh --printer-cfg-ersetzen --stock-printer-cfg stock-printer.cfg [übrige Optionen]
```

Die Stock-Datei enthält Geräte-Kennungen (`[auto_addr]`). Sie gehört weder ins Repo noch ins Paket (`pack.sh` bricht bei einem `#*# [auto_addr]`-Abschnitt ab). `install.sh` liest sie nur.

**Abbildung (Upstream-Klipper, ursprünglich gegen `9cb9975` erstellt):**

| Stock (SAVE_CONFIG) | Pi (SAVE_CONFIG der Hauptdatei) | Prüfung / Grund |
|---|---|---|
| `[box] cut_pos_x` | `[k2_box] cut_pos_x` | Zahl innerhalb `check_cut_pos_x_min/max` aus `[k2_box]` (`k2-module.cfg`, −9,5 … −5,5) und innerhalb `[k2_limits] x_min_wide … 0`. Sonst würde `k2_box` bzw. `k2_limits` den Start verweigern. Weitere `[box]`-Optionen werden nur gemeldet. |
| `[input_shaper]` | `[input_shaper]` | Nur Upstream-Optionen (`shaper_type`, `shaper_type_x/y`, `shaper_freq_x/y`, `damping_ratio_x/y`) und Upstream-Typen (`zv`, `mzv`, `zvd`, `ei`, `2hump_ei`, `3hump_ei`; `damping_ratio` bis zum Typ-Maximum). Sonst entfällt der ganze Abschnitt. |
| `[bed_mesh <profil>]` | `[bed_mesh <profil>]` (gleicher Name) | Das Profilformat des Creality-Forks gleicht dem von Upstream (Version 1, dieselben zehn Felder). Geprüft wird: <ul><li>Version 1, alle Felder vorhanden, keine unbekannten Felder;</li><li>Ganzzahlen wie Upstream-`getint`;</li><li>`x_count`/`y_count` ≥ 3;</li><li>`points` = `y_count` Zeilen × `x_count` Zahlen;</li><li>`algo` lagrange/bicubic/direct mit den Upstream-Regeln (bicubic ≥ 4, lagrange ≤ 6 Punkte, direct nur mit pps 0);</li><li>`tension` 0 … 2, min < max.</li></ul> Sonst entfällt das Profil. Ein abweichendes Raster gegenüber `[bed_mesh]` der Pi-Config ergibt nur einen Hinweis. |
| `[auto_addr]` | – | **Nie.** Geräte-Kennungen (UIDs); der Pi vergibt die Adressen selbst. Werte werden nicht ausgegeben. |
| `[heater_bed]`, `[extruder]`, `[prtouch…]` | – | Creality-eigene Kalibrierung (Bett-Leistungskalibrierung je Netzspannung, zweiter Hochtemperatur-PID-Satz bzw. `user_nozzle_diameter`, Kraftsensor). Nicht 1:1 abbildbar. Die Pi-Werte stehen in `printer.cfg`; bei Bedarf am Pi `PID_CALIBRATE` bzw. `PROBE_CALIBRATE`. |
| alles andere | – | Wird als „unbekannter Abschnitt“ mit Optionsnamen gemeldet, nicht übernommen. |

Ebenfalls ausgelassen wird ein Wert, der schon im Hauptteil oder einem `[include]` steht, z. B. eine von Hand eingetragene aktive `cut_pos_x:`-Zeile in `k2-module.cfg`. Upstream würde den SAVE_CONFIG-Wert sonst beim Laden verwerfen, und `SAVE_CONFIG` bräche ab.

**Ablauf:**

1. **Schritt 1, vor jeder Änderung:**
   - Geprüft wird: Datei lesbar, SAVE_CONFIG-Block vorhanden und nach den Upstream-Regeln lesbar (nur `#*# `-Zeilen), mindestens ein Abschnitt übernehmbar.
   - Bezug sind die Grenzen der Paket-Config.
   - Ausgabe je Abschnitt (übernehmbar bzw. ausgelassen + Grund). Sonst Abbruch, nichts verändert.
2. **Schritt 5, nach dem Zusammenführen der Hauptdatei:**
   - Ziel ist der Block, der danach gilt: ein bisheriger Pi-Block bzw. mit `--save-config-aus-paket` der des Pakets.
   - Zieldatei ist `printer.cfg` bzw. ohne `--printer-cfg-ersetzen` `printer-k2.cfg`.
   - Je Abschnitt:
     - fehlt im Pi-Block → **neu**;
     - gleiche Werte (numerisch verglichen) → **gleich**;
     - andere Werte → **behalten** + Warnung.
   - Mit `--stock-ueberschreiben` wird **ersetzt**: `[input_shaper]` und Mesh-Profile als ganzer Abschnitt, bei `[k2_box]` nur `cut_pos_x`.
   - Der Hauptteil bleibt unberührt; ohne Änderung bleibt die Datei bytegleich.
   - Protokoll in `<backup>/stock-import.txt`, Zusammenfassung `Stock-Kal: neu=… gleich=… behalten=… ueberschrieben=… ausgelassen=…`.
3. **Prüfung:** Die Batch-Prüfung in Schritt 6 lädt die fertige Hauptdatei samt übernommenem Block.
4. **Sicherung/Rückbau:** Der Stand vorher liegt wie immer in `<backup>/config.tar.gz`; Rückbau über `uninstall.sh` (§5).

**Hinweise:**

- **Wiederholung:** Ein weiterer Lauf mit derselben Datei ändert nichts. Im Pi neu kalibrierte Werte (z. B. `SHAPER_CALIBRATE` + `SAVE_CONFIG`) bleiben ohne `--stock-ueberschreiben` erhalten.
- **Mesh-Profil `default`:** Upstream lädt es nicht von selbst, die Pi-Config aber schon: nach jedem Z-Homing und in `START_PRINT` (`_PI_MESH_LADEN`, wie Stock). Mit dem Import druckt der Pi also mit dem **am Stock gemessenen** Netz; die Ausgabe warnt davor. Die mitgelieferte Config enthält dasselbe Netz nur als Vergleichsprofil `stock_referenz_2026-10-16` (`config-pi/README.md` §2.1). Nach der Inbetriebnahme mit `G29` neu messen und `SAVE_CONFIG`.
- `[input_shaper]` der Stock-Datei entspricht in der Regel dem schon mitgelieferten Block und erscheint dann als „gleich“.
- Werkzeug direkt: `python3 k2cfg.py stock-pruefen DATEI [--referenz printer.cfg]` (nur lesen) bzw. `stock-uebernehmen DATEI ZIEL [--ueberschreiben]`. Exit 0 = mindestens ein Abschnitt übernehmbar, 2 = Datei/Block unlesbar, 3 = nichts übernehmbar.

## 4e. Dienste und Web (Schritt 7): Start-Wächter, Creality-Brücke, Moonraker

Drei Teile, die am Pi des Autors am 23./24.10.2026 **von Hand** eingerichtet wurden und seitdem laufen. `install.sh` richtet sie jetzt selbst ein – Standard **an**, je abschaltbar. Alle Vorprüfungen laufen in Schritt 1 (vor jeder Änderung), der Einbau in Schritt 7 (nach grünen Tests). Jeder Teil ist idempotent: ein zweiter Lauf ändert nichts, startet nichts neu und fügt nichts doppelt ein.

| Teil | Was `install.sh` tut | Warum | Abschalten |
|---|---|---|---|
| **Start-Wächter** | `k2_startwaechter.py` → `~/k2-tools/`; Einheit aus der Vorlage `k2-startwaechter.service` (`@USER@`, `@PY@` = `python3`, `@TOOLS@`, `@DIENST@`, `@MOONRAKER_DIENST@`) → `/etc/systemd/system/`; `daemon-reload`, `enable`, (Neu-)Start | Klipper bleibt sonst nach „Drucker nach dem Pi eingeschaltet“ bzw. „Drucker bei laufendem Pi aus/an“ in `error`/`shutdown` (Lost communication). Der Wächter löst dann `FIRMWARE_RESTART` aus: 3 schnelle Versuche, danach alle 120 s, bis der Drucker wieder da ist; ebenso, wenn `k2_box` 90 s nach `ready` nicht bereit ist (Motor-Init unvollständig). Antworten die Motoren nach dem Einschalten noch nicht (Motor-Init key798), meldet er das sofort in der Konsole (mit Restzeit) und startet genau 30 s nach `ready` neu; nach jedem Neustart fragt er alle 3 s nach, statt blind 75 s zu warten (25.10.2026). **Nie** bei Sicherheitsabschaltung (Heizfehler, M112 …) oder laufendem/pausiertem Druck. Meldungen in der Mainsail-Konsole (`_PI_MELDUNG`) und `journalctl -u k2-startwaechter`; warnt zusätzlich bei Pi-Unterspannung (`vcgencmd get_throttled`) | `--ohne-startwaechter` |
| **Creality-Brücke** | `k2_creality_bruecke.py` → `~/k2-tools/`; Einheit aus `k2-creality-bruecke.service` mit `~/moonraker-env/bin/python` (braucht `tornado`, Prüfung in Schritt 1); dazu im Mainsail-Server-Block (`/etc/nginx/sites-available/mainsail`) **vor** `location /websocket` der Block `location ~ ^/upload/ { rewrite ^ /server/files/upload break; proxy_pass http://apiserver; … proxy_set_header Authorization ""; … }` zwischen den Marken `# k2-pi-paket BEGIN/END creality-upload`; Sicherung, `sudo nginx -t` (scheitert es: Original zurück, Abbruch), `systemctl reload nginx` | Orca-Druckerprofile, die vom Creality-K2-Profil erben, benutzen den „CrealityPrint“-Agenten: Upload per `POST /upload/<datei>` (→ nginx → Moonraker `/server/files/upload`), Druckstart per WebSocket an Port 9999 (→ Brücke → Moonraker `/printer/print/start`, also über `SDCARD_PRINT_FILE` samt Dateiprüfung). Nur Druckstart; `colorMatch` wird quittiert und ignoriert | `--ohne-creality-bruecke` (betrifft Dienst **und** nginx) |
| **Moonraker** | In `moonraker.conf` im Abschnitt `[authorization]` `enable_api_key: False` (eingefügt bzw. ersetzt, mit Kommentarzeile); Sicherung; `systemctl restart moonraker` **nur** bei einer Änderung. Fehlt `[authorization]`, ändert das Werkzeug nichts (Warnung) | Moonraker 0.11 lehnt Orca sonst mit HTTP 401 „Invalid API Key“ ab | `--ohne-moonraker-anpassung` |

- **Schon von Hand eingerichtet** (wie am Pi des Autors): ein vorhandenes `location ~ ^/upload/` ohne Paketmarke bleibt unverändert (nichts doppelt); `enable_api_key: False` → unverändert, kein Neustart; vorhandene Einheiten/Programme werden durch den Paketstand ersetzt, die alten Dateien + Aktivierungszustand liegen im Backup (`<backup>/dienste/`, `<backup>/k2-tools/`, `state.env`).
- **Ein früher eingerichteter Teil** bleibt bei `--ohne-…` unverändert stehen (die Zusammenfassung nennt ihn). Abschalten von Hand: `sudo systemctl disable --now k2-startwaechter` bzw. `k2-creality-bruecke`; Rückbau über `uninstall.sh` (§5).
- **Prüfen am Gerät:** `systemctl status k2-startwaechter k2-creality-bruecke`, `journalctl -u k2-startwaechter -n 50`, `sudo nginx -t`, `ss -ltn | grep 9999`; Orca: Upload + Druckstart einer kleinen Testdatei.
- **Werkzeug direkt:** `python3 k2web.py moonraker DATEI [--aus AUS]`, `python3 k2web.py nginx DATEI [--aus AUS]`, `python3 k2web.py nginx-entfernen DATEI [--aus AUS]` (ohne `--aus` nur Prüfung; letzte Zeile `STATUS: …`).

## 5. Rückbau

```bash
./uninstall.sh              # letztes Backup (~/k2-pi-backup/letzte), mit Rückfrage
./uninstall.sh --aelteste   # Zustand vor der ERSTEN Installation
./uninstall.sh --backup ~/k2-pi-backup/<stamp> --ja
```

Stoppt Klipper, entfernt alle `extras/k2_*.py` + `k2_nachbau/` und spielt die gesicherten zurück, sichert den aktuellen Config-Ordner zusätzlich (`~/k2-pi-backup/vor-rueckbau-*.tar.gz`), entfernt neu angelegte cfg-Dateien und entpackt `config.tar.gz`, stellt den Klipper-Checkout auf Branch/Commit von vorher zurück (nur wenn er noch sauber auf `ee2c6fb` steht; `--klipper-lassen` unterdrückt das), entfernt die `.git/info/exclude`-Einträge.
`k2-bootstart.service` kommt auf den Stand vor dem gewählten Lauf:
- Gab es die Einheit vorher nicht (auch bei Backups älterer Pakete), wird eine Paket-Einheit deaktiviert und entfernt,
  samt `~/k2-bootstart/`.
- Sonst kommen die gesicherte Einheit, der Werkzeugordner und der Aktivierungszustand zurück.
- Eine fremde Einheit ohne Paketmarke bleibt unberührt.

Start-Wächter und Creality-Brücke (§4e) ebenso: gab es die Einheit vor dem gewählten Lauf nicht, wird eine Paket-Einheit (Marke `# k2-pi-paket: …` in Zeile 1) gestoppt, deaktiviert und entfernt, samt Programm in `~/k2-tools/` (leerer Ordner wird gelöscht); sonst kommen Einheit, Programm und Aktivierungszustand aus dem Backup zurück. Von Hand eingerichtete Einheiten ohne Marke bleiben unberührt. Der nginx-Upload-Block wird entfernt, wenn er vor dem gewählten Lauf nicht da war (nur der Block mit Paketmarken; danach `nginx -t` + `reload`, bei Fehler bleibt die Datei unverändert + Warnung). `moonraker.conf` liegt im Config-Ordner und kommt mit `config.tar.gz` zurück (außerhalb: eigene Sicherung des Laufs); hat sie sich dadurch geändert, wird Moonraker neu gestartet.

Startet Klipper nur mit `--starten`. Eine Kopie von `uninstall.sh` und `k2web.py` liegt in jedem Backup-Ordner.

## 6. Hinweise / ANNAHMEN

- `mainsail.cfg` wird von der K2-`printer.cfg` **nicht** eingebunden (eigene PAUSE/RESUME/CANCEL_PRINT, `[virtual_sdcard]`, `[pause_resume]`, `[display_status]`). Doppelte Makro-Abschnitte würden sich still vermischen.
- Detached HEAD: Moonraker meldet Klipper als „detached“; ein Update über den Update-Manager verlässt den geprüften Stand – danach `install.sh` erneut laufen lassen (Module bleiben als ungetrackte Dateien erhalten).
- `~/k2-test` (alter Testordner) wird nicht benutzt.
- Klipper-Stand: Geprüfter Stand ist seit 24.10.2026 Upstream `ee2c6fb` (enthält den PID-Fix `e761a2c76`); auf diesem Stand läuft der Pi des Autors seit 23.10.2026 mit allen K2-Modulen (Drucke inklusive Mehrfarbe). Ein älterer Checkout wird auf `ee2c6fb` gestellt, ein neuerer bleibt stehen (Schritt 2).

### Prüfstand (24.10.2026, ehrlich)

| Teil | Am echten Gerät (Pi des Autors) | Nur Attrappe/offline |
|---|---|---|
| Schritte 1–6 + Abschluss: Erstinstallation mit `--material-db`, `--remain-material`, `--stock-printer-cfg` | **ja**, 22.10.2026, Paket 20261022-1323: EXIT 0, Tests + Batch grün, Stock-Kal. neu=2 gleich=1 | – |
| Wiederholung mit `--main-serial /dev/ttyAMA4 --duese-serial /dev/ttyAMA2` (Serial + SAVE_CONFIG übernommen) | **ja**, 22.10.2026 (Paket 20261022-1331), danach Klipper `ready` | – |
| `--rs485-serial` | nein (am 22.10. von Hand in `k2-module.cfg` eingetragen) | Attrappe 17.10. |
| `--main-serial auto`, `--serial-erkennen` | nein | `test_k2_identify.py` |
| `--bootstart` | nein (Gerätetest offen) | `test_k2_bootstart.py` |
| Schritt 7: Start-Wächter, Creality-Brücke + nginx-Block, Moonraker `enable_api_key` | Funktion **ja** (seit 23./24.10. von Hand eingerichtet und in Betrieb) – Einbau **durch install.sh nein** | `test_k2_install.py` (Fälle A–G), `test_k2web.py`, `test_k2_startwaechter.py`, `test_k2_creality_bruecke.py` |
| `--bewegung-gesperrt`, Übernahme der Freigaben `allow_*`, kein Klipper-Rückschritt | nein | `test_k2_install.py` (C, K), `test_k2cfg.py` Teil D |
| Rückbau `uninstall.sh` (inkl. Dienste/nginx/Moonraker) | **nein** | `test_k2_install.py` (E, F, G3, K), Attrappe 17.10. |

Seit dem 22.10.2026 wurde der Pi des Autors **von Hand** weiterentwickelt (Module direkt nach `extras/` kopiert, Config-Dateien geändert, Dienste eingerichtet). Ob ein Neuinstallations-Lauf mit dem heutigen Paket exakt diesen Stand ergibt, ist erst nach einem Abgleich Pi ↔ `config-pi/` belegt (am 24.10. nicht möglich, Pi nicht erreichbar).

- Geprüft (17.10.2026) nur in einer Attrappe auf x86_64 (`--arch-egal`): Fake-HOME mit Klon des Upstream-Klippers (Fake-Commit über ee2c6fb als „Image-Stand“), Fake-`systemctl`/`sudo` über PATH, Klipper-venv des Batch-Aufbaus. Abgedeckt: Erstinstallation, Wiederholung mit `--printer-cfg-ersetzen` + serial-Optionen, Übernahme von Hand-Einträgen + SAVE_CONFIG, Batch-Fehler wird erkannt, dirty Checkout / falsche Prüfsumme / root / Architektur brechen ab, Rückbau `letzte` und `--aelteste` → Config, extras, `.git/info/exclude` und HEAD/Branch identisch zum Ausgangszustand. Material-DB (17.10.2026, synthetische Mini-DB): Neuanlage, Ersetzen mit Backup, unverändert ohne Option, identische Datei, kaputtes JSON / falsche Struktur / fehlende Datei → Abbruch ohne Änderung, Rückbau `letzte`/`--aelteste`, `pack.sh` schließt DB aus. remain_material (17.10.2026, synthetische Dateien): Neuanlage, Ersetzen mit Backup, unverändert ohne Option (auch wenn von „k2_box“ geändert), identische Datei, Zusatzschlüssel/`null`, Negativfälle (kaputtes JSON, leer, Liste, Schlüssel fehlt, `remain_material` kein Objekt, `color`/`type` fehlt, Farbe ohne `0`/zu kurz/kein Hex, `type` leer/Zahl, fehlende Datei) → Abbruch ohne Änderung, defekte Datei ohne Option → Warnung + `scp`-Hinweis, Rückbau `--backup`/`--aelteste`, `pack.sh` schließt Datei aus. Stock-Übernahme (19.10.2026):
  - Attrappe: Negativfälle (`--stock-ueberschreiben` allein, Datei fehlt, ohne SAVE_CONFIG, nur `[auto_addr]`) → Abbruch ohne Änderung; Erstinstallation (neu=2, gleich=1, Batch EXIT 0); Wiederholung bytegleich; abweichende Werte behalten bzw. mit `--stock-ueberschreiben` ersetzt (Batch EXIT 0); `--save-config-aus-paket`; Ziel `printer-k2.cfg`; keine UIDs in Ausgabe/Block.
  - Upstream-Batch: Profil `default` per `BED_MESH_PROFILE LOAD` geladen, `[k2_box] cut_pos_x` aus SAVE_CONFIG wirksam (`k2_limits` X-min); Upstream-`SAVE_CONFIG` schreibt den Block ohne Konflikt und inhaltsgleich zurück.

  Seit 22.10.2026 auch am echten Pi gelaufen (Tabelle oben).
- Attrappe als Test im Paket (24.10.2026): `test_k2_install.py` (Fake-HOME mit git-Klipper, Fake-`/etc`, Fake-`sudo`/`systemctl`/`nginx`/`id`, ohne Root/Netz): A Erstinstallation (Einheiten aus Vorlage, `~/k2-tools`, nginx-Block genau einmal vor `/websocket`, `enable_api_key` genau einmal, je ein Reload/Neustart), B Wiederholung bytegleich ohne Neustarts, C `--bewegung-gesperrt` + Freigabe-Übernahme, D alles `--ohne-*` (auch ohne `moonraker-env`), E Rückbau `--aelteste` bytegleich, F Handeinrichtung wie am Pi des Autors (Hand-Block/-Option bleiben, Hand-Einheit gesichert und zurückgespielt), G Abbrüche (tornado fehlt, nginx ohne `/websocket`, `nginx -t` scheitert → Original zurück, ohne `[authorization]`, `moonraker.conf` fehlt), K Klipper neuer/älter als Zielstand. `pack.sh --tests` lässt ihn mitlaufen; `install.sh` selbst nicht (braucht `git`, verschachtelter Lauf).
