# Umstieg auf den K2-CM5-Träger Alpha 1 – Softwarepaket und Anleitung

> **ALPHA – am Gerät UNGETESTET (Stand 24.10.2026).** Der Träger ist noch nicht gefertigt. Alles hier ist offline
> geprüft (`test_traeger_config.py`, `test_k2_stromausfall.py`, `../test_k2_485.py`), nicht am Drucker.
> Hardware: `hardware/k2-pi-cm5-traeger/` (README, KONZEPT, Schaltplan-Generator `generator/k2_traeger.py`).
> Stand des Layouts: **Umbau „Weg A“** – kein Toolhead-Stecker J9, keine eFuse, GPIO6/GPIO26 frei, Eingangssicherung
> F1 = 3 A, INA226 nur noch 0x40 und 0x41.

## Inhalt des Ordners

| Datei | Zweck | Ziel auf dem Pi (macht `install-traeger.sh`) |
|---|---|---|
| `config-txt-traeger.txt` | Block für `config.txt` (UARTs, RS485, CAN, I2C, INA226, Display) | `/boot/firmware/config.txt` (Block zwischen Markern) |
| `k2-traeger-ina226-overlay.dts` | eigenes Overlay: INA226 0x40/0x41 als hwmon mit Shunt-Werten | `/boot/firmware/overlays/k2-traeger-ina226.dtbo` (mit `dtc`) |
| `can0/80-k2-can0.network`, `can0/80-k2-can0.rules`, `can0/k2-can0-nm.conf` | can0 mit 1 Mbit/s, Warteschlange 1024, NetworkManager hält sich raus | `/etc/systemd/network/`, `/etc/udev/rules.d/`, `/etc/NetworkManager/conf.d/` |
| `klipper-ausschnitte.cfg` | Klipper-Änderungen (serial-Pfade, `[k2_485]` über UART0, Vorlage `[mcu toolhead]`) | **von Hand** (Schritt 7) |
| `k2_stromausfall.py`, `k2-stromausfall.service`, `k2_stromausfall.default` | Stromausfall-Wächter (INA226-ALERT → GPIO27) | `/usr/local/sbin/`, `/etc/systemd/system/`, `/etc/default/k2_stromausfall` |
| `k2_readonly.sh` | read-only Root mit Overlay + Datenpartition | `/usr/local/sbin/k2_readonly.sh` (nur Werkzeug, schaltet nichts) |
| `k2_config_sicherung.sh`, `k2-config-sicherung.service`/`.timer`, `k2_config_sicherung.default` | tägliche Sicherung von `printer_data/config` | `/usr/local/sbin/`, `/etc/systemd/system/`, `/etc/default/` |
| `install-traeger.sh` | spielt alles außer der Klipper-Konfig ein; `--trocken`, `--readonly` | – |
| `test_traeger_config.py`, `test_k2_stromausfall.py` | Offline-Prüfungen (Schaltplan ↔ Konfig, Stromausfall-Logik) | – |

Geändert außerhalb des Ordners: `pi-host/k2_485.py` (neue Option `rs485_de`) und `pi-host/test_k2_485.py`
(Prüfungen dazu). Sicherungen der alten Fassungen liegen lokal beim Autor.

---

## GPIO-Zuordnung (aus dem Schaltplan, von `test_traeger_config.py` geprüft)

| GPIO | CM5-Pin | Netz im Schaltplan | Funktion | eingeschaltet durch | Linux |
|---|---|---|---|---|---|
| 0 | 36 (ID_SD) | DSI_SDA | I2C des Displays (MIPI1) | `vc4-kms-dsi-7inch` (i2c_csi_dsi) | i2c-0 |
| 1 | 35 (ID_SC) | DSI_SCL | I2C des Displays (MIPI1) | `vc4-kms-dsi-7inch` | i2c-0 |
| 2 | 58 | I2C1_SDA | INA226 0x40/0x41 | `dtparam=i2c_arm=on` | i2c-1 |
| 3 | 56 | I2C1_SCL | INA226 0x40/0x41 | `dtparam=i2c_arm=on` | i2c-1 |
| 4 | 54 | UART2_TX | → Puffer U10 → 1 kΩ → Breakout 26 → **Düsen-MCU** | `uart2-pi5` | `/dev/ttyAMA2` |
| 5 | 34 | UART2_RX | ← Puffer U11 ← 1 kΩ ← Breakout 28 | `uart2-pi5` | `/dev/ttyAMA2` |
| 7 | – | (offen) | SPI0 CE1, unbenutzt (vom SPI0-Treiber belegt) | `mcp251xfd` | – |
| 8 | 39 | CAN_CS | MCP2518FD nCS (SPI0 CE0) | `mcp251xfd,spi0-0` | can0 |
| 9 | 40 | CAN_MISO | SPI0 MISO | `mcp251xfd` | can0 |
| 10 | 44 | CAN_MOSI | SPI0 MOSI | `mcp251xfd` | can0 |
| 11 | 38 | CAN_SCK | SPI0 SCLK | `mcp251xfd` | can0 |
| 12 | 31 | UART4_TX | → Puffer U12 → 1 kΩ → Breakout 30 → **Main-MCU** | `uart4-pi5` | `/dev/ttyAMA4` |
| 13 | 28 | UART4_RX | ← Puffer U13 ← 1 kΩ ← Breakout 32 | `uart4-pi5` | `/dev/ttyAMA4` |
| 14 | 55 | UART0_TX | THVD1450 D (RS485 senden) | `uart0-pi5,rs485` | `/dev/ttyAMA0` |
| 15 | 51 | UART0_RX | THVD1450 R (RS485 empfangen) | `uart0-pi5,rs485` | `/dev/ttyAMA0` |
| 16 | – | (offen) | UART0 CTS, durch `rs485` mit belegt, unbenutzt | `uart0-pi5,rs485` | – |
| 17 | 50 | RS485_DE | THVD1450 DE + ~RE (= UART0-RTS, Kernel schaltet) | `uart0-pi5,rs485` | – |
| 25 | 41 | CAN_INT | MCP2518FD nINT (10 kΩ Pull-up R18) | `mcp251xfd,interrupt=25` | – |
| 27 | 48 | PWR_FAIL_N | INA226 0x40 ALERT, aktiv low, 10 kΩ Pull-up R50, Messpad TP12 | `k2_stromausfall` (libgpiod) | – |
| 6, 26 | 30, 24 | – | **frei** (früher Toolhead-eFuse, mit Weg A entfallen) | – | – |

Weitere Zuordnungen ohne GPIO: Quarz Y1 **40 MHz** am MCP2518FD (`oscillator=40000000`); INA226 **0x40** (A1 = A0 = GND,
Shunt R23 **5 mΩ**, 24-V-Eingang hinter F1), **0x41** (A1 = GND, A0 = VS, Shunt R24 **10 mΩ**, CM5-5V); **kein HAT-EEPROM**
auf dem Träger (KONZEPT §7.1 – die Overlays stehen fest in `config.txt`); DSI an **MIPI1** (CM5-Pins 175–189, 2 Lanes +
Takt) = Linux **DSI1**; USB: Kamera an USB3-0 (USB-2-Teil, CM5 134/136 → J1 37/38), Hub des Bridge-Boards an USB3-1
(163/165 → J1 34/35), Notfall-USB-C J10 an USB-OTG (103/105); Ethernet CM5-Paare 0 und 1 über Übertrager T1 → J1 1/2 und
4/5 (100 Mbit/s, 2 Paare); LEDs: D11 3V3, D12 ACT (LED_nACT, Pin 21), D13/D14 Ethernet (nLED3/nLED2, Pin 15/17), D10 rot =
Drucker-5V am Flachkabel (Flachkabel richtig herum).

---

## Teil A – Vorbereitung (CM5 noch auf dem IO-Board, alles läuft wie heute)

### Schritt 1 – Paket auf den Pi kopieren

Am PC im Ordner `pi-host/` (Platzhalter `<pi-name>` = Name oder Adresse deines Pi):

```
scp -r traeger-alpha1 k2_485.py <pi-benutzer>@<pi-name>:~/
```

### Schritt 2 – Trockenlauf

Auf dem Pi:

```
cd ~/traeger-alpha1
sudo ./install-traeger.sh --trocken
```

Erwartet: Liste mit `TROCKEN: …`-Zeilen, die Änderung an `config.txt` als Diff (neuer Block am Ende, die alten Zeilen
`dtoverlay=uart2-pi5` und `dtoverlay=uart4-pi5` werden zu `#K2T-alt# …`), keine `FEHLER`-Zeile. Es wird **nichts**
verändert.

### Schritt 3 – Neues Modul `k2_485.py` einspielen

Die Option `rs485_de: kernel` kennt nur die neue `k2_485.py`. Klipper vorher anhalten:

```
sudo systemctl stop klipper
cp ~/k2_485.py ~/klipper/klippy/extras/k2_485.py
```

(Klipper bleibt gestoppt; Schritt 8 schaltet den Autostart für den ersten Start auf dem Träger aus.)

### Schritt 4 – Paket einspielen

```
sudo ./install-traeger.sh
```

Erwartet am Ende: `OK fertig. Sicherungen geänderter Dateien: /var/backups/k2-traeger/<zeit>` und
`Neustart nötig`. **Nicht** neu starten. Auf dem IO-Board schadet der neue `config.txt`-Block nicht (CAN-Chip, INA226 und
Display fehlen dort; der Kernel meldet nur, dass er sie nicht findet). `k2-stromausfall` ist aktiviert, wird auf dem
IO-Board aber nie scharf (kein INA226 lesbar).

### Schritt 5 – Bootloader: 5-A-Versorgung erlauben (ANNAHME, empfohlen)

Der Träger liefert 5,2 V/5 A ohne USB-PD-Aushandlung. Ohne Einstellung nimmt der CM5 ein schwaches Netzteil an
(KONZEPT, CC1/CC2).

```
sudo rpi-eeprom-config --edit
```

Im Editor eine Zeile `PSU_MAX_CURRENT=5000` ergänzen, speichern. `usb_max_current_enable=1` steht schon im
`config.txt`-Block.

### Schritt 6 – Konfiguration sichern

```
sudo k2_config_sicherung.sh
```

Erwartet: `gesichert: /var/backups/k2-config/config-<zeit>.tar.gz` (vor der Datenpartition landet die Sicherung dort).
Zusätzlich eine Kopie auf den PC holen (am PC):

```
scp <pi-benutzer>@<pi-name>:'/var/backups/k2-config/config-*.tar.gz' .
```

### Schritt 7 – Klipper-Konfig umstellen

In `~/printer_data/config/` (Mainsail → Maschine, oder `nano`) nach `klipper-ausschnitte.cfg` ändern:

| Datei | Abschnitt | heute | Träger |
|---|---|---|---|
| `printer.cfg` | `[mcu]` | `serial: /dev/ttyAMA4` | `serial: /dev/ttyAMA4` (gleich; steht dort noch ein by-id-Pfad, ersetzen) |
| `printer.cfg` | `[mcu nozzle_mcu]` | `serial: /dev/ttyAMA2` | `serial: /dev/ttyAMA2` (gleich) |
| `k2-module.cfg` | `[k2_485]` | `serial: /dev/serial/by-id/usb-…` (USB-Adapter) | `serial: /dev/ttyAMA0` und neue Zeile `rs485_de: kernel` |
| `printer.cfg` | `[mcu toolhead]` | – | nur die auskommentierte Vorlage (erst mit CAN-Toolhead) |

Ab jetzt hat der Pi auf dem IO-Board **keinen RS485-Bus** mehr (gewollt, er wird gleich ausgeschaltet).

### Schritt 8 – Klipper-Autostart für den ersten Start aus

```
sudo systemctl disable klipper
```

So startet Klipper auf dem Träger erst, wenn die Prüfungen aus Schritt 15 grün sind.

### Schritt 9 – Ausschalten (vollständige Reihenfolge)

1. Auf dem Pi: `sudo poweroff`.
2. Warten, bis die grüne ACT-LED des IO-Boards 10 s lang aus ist.
3. Pi-Netzteil (USB-C am IO-Board) aus der Steckdose ziehen.
4. Drucker am Hauptschalter ausschalten.
5. 30 s warten.

---

## Teil B – Umbau auf den Träger

### Schritt 10 – Verbindungen des IO-Boards lösen

Alle Handgriffe stromlos (Schritt 9 erledigt). Heute gesteckt (PI-ANSCHLUSS-LOWERBOARD §8.6, PI-INBETRIEBNAHME 0.x;
**ANNAHME** – falls bei dir anders gesteckt, deine tatsächlichen Enden notieren):

| Verbindung heute | Ende 1 | Ende 2 | Handgriff |
|---|---|---|---|
| Main-MCU TX | IO-Board Header Pin 32 (GPIO12) | Breakout Stift 30 (über 1 kΩ) | abziehen |
| Main-MCU RX | IO-Board Header Pin 33 (GPIO13) | Breakout Stift 32 (über 1 kΩ) | abziehen |
| Düsen-MCU TX | IO-Board Header Pin 7 (GPIO4) | Breakout Stift 26 (über 1 kΩ) | abziehen |
| Düsen-MCU RX | IO-Board Header Pin 29 (GPIO5) | Breakout Stift 28 (über 1 kΩ) | abziehen |
| Masse | IO-Board Header Pin 39 (GND) | Breakout GND-Stift | abziehen |
| RS485 A | USB-RS485-Adapter Klemme A | Breakout Stift 20 (485_A) | abziehen |
| RS485 B | USB-RS485-Adapter Klemme B | Breakout Stift 19 (485_B) | abziehen |
| RS485-Adapter USB | USB-RS485-Adapter | IO-Board USB-A | abziehen |
| Kamera | Kamerakabel (über Adapter 4-pol. → USB-A) | IO-Board USB-A | abziehen, Kamerakabel **zurück an CAM-T am Bridge-Board** |
| Flachkabel | Breakout-Platine | Bridge-Board J14 (Original-Flachkabel) | Breakout vom Flachkabel abziehen |
| Versorgung | Pi-Netzteil USB-C | IO-Board USB-C | ist schon gezogen |

### Schritt 11 – CM5 umsetzen

1. CM5 vom IO-Board abziehen (gleichmäßig an beiden Längsseiten hebeln).
2. Kühlkörper so wählen, dass Kühlkörper + Wärmeleitpad **≤ 4,96 mm** hoch sind (Hardware-README „Höhenbilanz“).
3. CM5 auf den Träger (zwei DF40-Steckverbinder) drücken, mit Abstandshaltern M2,5 und flachen Schrauben (Kopf ≤ 5,5 mm)
   befestigen.
4. Jumper prüfen: **JP_BOOT offen** (kein Jumper), **JP_CAN_TERM gesteckt**, **JP_485_TERM offen**.

### Schritt 12 – Messungen am Träger, stromlos

Messart: **Widerstand (Ω)**, Drucker aus, **kein Kabel** am Träger gesteckt (J1, J7, J12 frei).

| Messung | **rote** Spitze an | **schwarze** Spitze an | erwartet |
|---|---|---|---|
| 24-V-Eingang kein Kurzschluss | J7 Pin 2 (+24 V, unten) | J7 Pin 1 (GND, oben) | > 1 kΩ (Wert steigt langsam, Kondensatoren laden) |
| Sicherung F1 ganz | F1 Pad Seite J7 | F1 Pad andere Seite | < 1 Ω |
| 5-V-Schiene kein Kurzschluss | Messpunkt „5V“ | Messpunkt „GND“ | > 100 Ω |
| 3,3-V-Schiene kein Kurzschluss | Messpunkt „3V3“ | Messpunkt „GND“ | > 100 Ω |
| CAN-Abschluss | J9b Kontakt „H“ | J9b Kontakt „L“ | ≈ 120 Ω (JP_CAN_TERM gesteckt) |
| RS485 ohne Abschluss | J11 Kontakt „A“ | J11 Kontakt „B“ | > 1 kΩ (kein 120 Ω; JP_485_TERM offen) |

Abweichung → **nicht einschalten**, melden.

### Schritt 13 – 24-V-Kabel prüfen, dann erster Spannungstest ohne Flachkabel

Messart: **Gleichspannung (V DC)**. Das 24-V-Kabel (XT30) ist am Netzteil angeschlossen, **noch nicht** am Träger.

Einschalten: Drucker am Hauptschalter einschalten.

| Messung | **rote** Spitze an | **schwarze** Spitze an | erwartet |
|---|---|---|---|
| Polarität 24-V-Kabel | XT30-Kabelstecker Kontakt „+“ | XT30-Kabelstecker Kontakt „−“ | +23 … +25 V (bei −24 V: Kabel falsch, **nicht stecken**) |

Ausschalten: Drucker am Hauptschalter aus, 30 s warten. Dann das 24-V-Kabel in **J7** stecken (nur J7, kein
Flachkabel, kein Display). Einschalten: Drucker am Hauptschalter ein. Der CM5 startet (ohne Flachkabel ohne
Druckerfunktion, das ist hier gewollt).

| Messung | **rote** Spitze an | **schwarze** Spitze an | erwartet |
|---|---|---|---|
| 24 V am Träger | rechter Clip der Sicherung F1 (Oberseite; Messpad TP10 „24 V“ liegt unten und ist eingebaut nicht erreichbar) | Messpunkt „GND“ | 23 … 25 V |
| Wandler 5,2 V | Messpunkt „5V“ | Messpunkt „GND“ | 5,15 … 5,25 V |
| 3,3 V vom CM5 | Messpunkt „3V3“ | Messpunkt „GND“ | 3,25 … 3,35 V |
| Stromausfall-Leitung | Messpad TP12 „PWR_FAIL“ (Unterseite – nur vor dem Einbau messbar) | Messpunkt „GND“ | 3,2 … 3,35 V (high = keine Meldung) |

Ausschalten (vollständig): 1. 90 s nach dem Einschalten warten (CM5 hochgefahren, per WLAN erreichbar wie bisher).
2. Per `ssh` anmelden, `sudo poweroff`. 3. Warten, bis D12 (ACT) 10 s aus ist. 4. Drucker am Hauptschalter aus.
5. 30 s warten. (Ist der CM5 per WLAN nicht erreichbar: nach 3 min Drucker am Hauptschalter aus – Klipper ist aus,
Schritt 8; danach Fehlersuche.)

### Schritt 14 – Alle Kabel stecken

Drucker ist aus (Schritt 13 beendet).

| Verbindung | Ende am Träger | anderes Ende | Hinweis |
|---|---|---|---|
| Flachkabel 50-pol. | **J1** (Kante A, Pin 1 unten) | Bridge-Board **J14** (Original-Flachkabel, vorher am Breakout bzw. an der T113 CON2) | Kontakte wie bisher; D10 zeigt später, ob richtig herum |
| 24 V | **J7** XT30, Pin 2 = +24 V, Pin 1 = GND | Netzteil-Abgriff 24 V (wie in Schritt 13 gemessen) | steckt schon |
| Display (optional) | **J12** (Kante C, 22-pol.) | Waveshare 4.3inch DSI LCD **Rev 2.2**, 22-pol. Seite des Kabels | Pi-5-Displaykabel passt ohne Verdrehen |
| Kamera | – (läuft über das Flachkabel, J1 37/38) | Kamerakabel an **CAM-T** des Bridge-Boards | aus Schritt 10 |
| CAN | **J9b** H/L/G | – | **nicht stecken** (erst mit CAN-Toolhead) |
| RS485-Abgriff | **J11** A/B/G | – | **nicht stecken** |
| Notfall-USB-C | **J10** | – | **nicht stecken** |

### Schritt 15 – Erster Start und Prüfbefehle

Einschalt-Reihenfolge: 1. Drucker am Hauptschalter einschalten. 2. Sofort auf **D10 (rot)** schauen: leuchtet =
Flachkabel richtig herum. Leuchtet D10 **nicht**: Drucker sofort aus, Flachkabel prüfen. 3. ACT-LED D12 blinkt, nach
≈ 60–90 s ist der CM5 per Netzwerk erreichbar (WLAN wie bisher; Ethernet über das Flachkabel mit 100 Mbit/s).

Per `ssh` anmelden und prüfen:

| Befehl | erwartet |
|---|---|
| `tr -d '\0' < /proc/device-tree/model` | `Raspberry Pi Compute Module 5 …` |
| `ls -l /dev/ttyAMA*` | `ttyAMA0`, `ttyAMA2`, `ttyAMA4` (dazu ggf. `ttyAMA10` = Debug-UART) |
| `ls -l /dev/serial0; cat /proc/cmdline` | keine `console=` auf `ttyAMA0/2/4` (serial0 → ttyAMA10 ist in Ordnung) |
| `pinctrl get 14-17` | GPIO14/15 UART0 TX/RX, GPIO16 CTS, GPIO17 **RTS** (Funktion a4) |
| `pinctrl get 4,5,12,13,27` | UART2/UART4 TX/RX; GPIO27 Eingang |
| `dmesg \| grep -iE 'ttyAMA\|rs485\|pl011'` | drei PL011-Ports, keine Fehler |
| `ip -details link show can0` | `can0` vorhanden, `state ERROR-ACTIVE` oder `UP`, `bitrate 1000000`, `qlen 1024` (ohne Teilnehmer am Bus auch `ERROR-PASSIVE` möglich) |
| `dmesg \| grep -iE 'mcp251xfd\|spi0\|can0'` | `mcp251xfd spi0.0 can0: MCP2518FD rev… successfully initialized` (Takt 40 MHz) |
| `sudo i2cdetect -y 1` | `UU` bei **40** und **41** (vom hwmon-Treiber belegt) |
| `sudo i2cdetect -y 0` (nur mit Display) | Geräte des Displays (Adressen laut Waveshare, z. B. 0x45/0x14 – **ANNAHME**) |
| `grep -H . /sys/bus/i2c/devices/1-0040/hwmon/hwmon*/{name,in1_input,in1_lcrit,update_interval}` | `ina226`, `in1_input` ≈ 24000 (mV), `in1_lcrit` = 20000, `update_interval` = 2 |
| `grep -H . /sys/bus/i2c/devices/1-0041/hwmon/hwmon*/{in1_input,curr1_input}` | ≈ 5200 mV, Strom des CM5 in mA |
| `gpioinfo \| grep -iE 'GPIO27\|GPIO17'` | GPIO27 `input` mit Verbraucher `k2_stromausfall`, Flanken `both-edges` |
| `sudo k2_stromausfall --status` | Spannung ≈ 24000 mV, `GPIO27 … Pegel 1`, Datenpartition (noch keine) |
| `systemctl status k2-stromausfall` | `active (running)`, im Log `scharf (GPIO27 high, 24.xx V)` |
| `dmesg \| grep -iE 'dsi\|panel\|ft5406\|goodix'` (nur mit Display) | DSI1-Panel erkannt; Bild am Display |
| `sudo ethtool eth0 \| grep -E 'Speed\|Link'` (nur mit LAN-Kabel am Drucker) | `Speed: 100Mb/s`, `Link detected: yes`; ohne Link: `sudo ethtool -s eth0 advertise 0x008` |
| `lsusb -t` | Kamera und Hub des Bridge-Boards erkannt |

Erst wenn alles passt, Klipper einschalten:

```
sudo systemctl enable --now klipper
```

Klipper-Status prüfen:

| Prüfung | erwartet |
|---|---|
| `tail -n 50 ~/printer_data/logs/klippy.log` | `Loaded MCU 'mcu'` und `Loaded MCU 'nozzle_mcu'` (Version wie bisher), `[k2_485] RS485-Port /dev/ttyAMA0 offen (230400 Baud, DE: Kernel-RS485 (Device-Tree))` |
| `curl -s localhost:7125/printer/info` | `"state": "ready"` |
| Mainsail-Konsole: `KTWO_BUS_STATUS` | `offen=True … de=kernel/Device-Tree`, `timeouts` steigen nicht laufend |
| Mainsail-Konsole: `KTWO_MOTOR_STATUS` | Antworten von X/Y/Z/Z1 wie am IO-Board (über RS485) |

Steht `de=kernel/gesetzt`, war der Device-Tree-Teil nicht aktiv (`k2_485` hat nachgeholfen) – `pinctrl get 17` muss dann
trotzdem RTS zeigen, sonst kommt nichts auf den Bus.

### Schritt 16 – Messungen am laufenden System

Messart: **Gleichspannung (V DC)**, Drucker an, Klipper läuft, **kein Druck**.

| Messung | **rote** Spitze an | **schwarze** Spitze an | erwartet |
|---|---|---|---|
| Stromausfall-Leitung | TP12 „PWR_FAIL“ | Messpunkt „GND“ | 3,2 … 3,35 V |
| RS485 Ruhe | J11 Kontakt „A“ | J11 Kontakt „B“ | > +0,2 V (Ruhevorspannung, A über B) |
| CAN rezessiv | J9b Kontakt „H“ | J9b Kontakt „G“ | ≈ 2,3 V (wie J9b „L“ gegen „G“, ohne Teilnehmer) |
| Drucker-5V am Flachkabel | unteres Pad von R38 (Oberseite; Messpad TP11 „BR5V“ liegt unten) | Messpunkt „GND“ | ≈ 5 V |

### Schritt 17 – Stromausfall-Test

1. `sudo k2_stromausfall --probelauf --trocken` → zeigt den Ablauf (einfrieren, Log, sync, Schutz) **ohne** etwas zu tun.
2. Echter Test **ohne Druck**: Ausschalten nur mit dem Hauptschalter des Druckers (das ist der Test).
3. Einschalten: Drucker am Hauptschalter ein, 90 s warten, anmelden.
4. `cat /var/log/k2_stromausfall.log` (nach dem Umzug in Teil C: `cat ~/printer_data/logs/k2_stromausfall.log`).
   Erwartet zwei Zeilen: `STROMAUSFALL erkannt …` und `sync fertig nach … ms`. Fehlt die zweite Zeile, reichte die
   Haltezeit nicht bis nach dem `sync` – melden (Uhrzeit der ersten Zeile + was der Drucker gerade tat).

**Ausschalten im Alltag** (vollständig): 1. Druck beendet/abgebrochen. 2. `sudo poweroff` (oder Mainsail → Herunterfahren).
3. Warten, bis D12 (ACT) 10 s aus ist. 4. Drucker am Hauptschalter aus. Ein hartes Ausschalten ohne Schritt 2 fängt der
Stromausfall-Wächter ab, ist aber nicht der Normalweg.

---

## Teil C – Datenpartition und schreibgeschütztes System (optional, später)

### Schritt 18 – Warum

Ein Hart-Aus mitten im Schreiben kann das Dateisystem beschädigen. Lösung: Root (`/`) wird schreibgeschützt (Änderungen
landen nur im RAM, Paket `overlayroot`, eingeschaltet über `raspi-config`), `/boot/firmware` read-only, und alles, was sich
im Betrieb ändert – `printer_data` (Konfig, Moonraker-Datenbank, Logs, gcodes) –, liegt auf einer eigenen ext4-
Datenpartition `K2DATEN` unter `/daten`.

**Mount-Optionen der Datenpartition** (`k2_readonly.sh` schreibt sie in `/etc/fstab`):

| Option | Begründung |
|---|---|
| `data=journal` | Daten **und** Metadaten gehen zuerst ins Journal: nach einem Hart-Aus keine Dateien mit altem/zufälligem Inhalt, auch nicht bei Programmen ohne `fsync` (Moonraker-Datenbank, `SAVE_CONFIG`, Logs). Preis: doppelte Schreiblast und kein verzögertes Zuordnen – für Konfig, Datenbank und Logs unerheblich, gcode-Uploads dauern etwas länger. Zusätzlich per `tune2fs -o journal_data` als Standard der Partition gesetzt. Quelle: [ext4-Doku](https://docs.kernel.org/admin-guide/ext4.html) |
| `commit=1` | Journal jede Sekunde festschreiben statt alle 5 s: höchstens ≈ 1 s Verlust statt 5 s. eMMC-Last bleibt klein (nur wenn sich etwas geändert hat). |
| `noatime` | keine Schreibzugriffe nur fürs Lesen (schont die eMMC). |
| `errors=remount-ro` | bei einem Dateisystemfehler schreibgeschützt weiter statt Schaden zu vergrößern. |
| `nofail`, `x-systemd.device-timeout=20s` | fehlt die Partition, startet das System trotzdem (per SSH erreichbar); Klipper/Moonraker starten dann **nicht** (Drop-in `RequiresMountsFor=`), statt mit leerer Konfig zu laufen. |
| fsck Feld 6 = `2` | Prüfung beim Start nach einem Hart-Aus. |

### Schritt 19 – Partition anlegen (am Linux-PC, CM5 per rpiboot)

Die eMMC ist heute ganz von `/` belegt; ext4 lässt sich nur ausgehängt verkleinern → am PC.

1. **Ausschalten** (vollständig): `sudo poweroff`, warten bis D12 10 s aus ist, Drucker am Hauptschalter aus, 30 s warten.
2. CM5 vom Träger auf das IO-Board setzen und wie beim Flashen gewohnt in den USB-Boot bringen (Boot-Schalter/Jumper
   des IO-Boards auf rpiboot, USB-C des IO-Boards an den PC, am PC `rpiboot` aus dem usbboot-Ordner starten) → die eMMC
   erscheint als `/dev/sdX` (mit `lsblk` nachsehen; **Größe ≈ 58 GiB, zwei Partitionen**). Alternative auf dem Träger:
   Jumper JP_BOOT stecken, Notfall-USB-C J10 an den PC (Drucker aus) – nicht erprobt.
3. Erst trocken: `sudo bash k2_readonly.sh pc-partitionieren /dev/sdX --trocken` – zeigt die Befehle
   (`e2fsck`, `resize2fs` auf 15 GiB, Partition 2 auf 16 GiB, Partition 3 = Rest, `mkfs.ext4 -L K2DATEN`,
   `tune2fs -o journal_data`).
4. Echt: `sudo bash k2_readonly.sh pc-partitionieren /dev/sdX` – fragt nach dem Gerätenamen als Bestätigung.
5. rpiboot beenden, USB-C ab, IO-Board-Boot-Schalter zurück, CM5 wieder auf den Träger (Schritt 11), Kabel wie Schritt 14.
6. **Einschalten**: Drucker am Hauptschalter ein, D10 rot prüfen, 90 s warten.

### Schritt 20 – printer_data umziehen (auf dem Pi)

```
sudo k2_readonly.sh umziehen --trocken
sudo k2_readonly.sh umziehen
sudo k2_readonly.sh status
```

Erwartet: `/daten` ext4 mit `data=journal,commit=1`, `~/printer_data ist Bind-Mount`, Klipper läuft wieder. Die alte
Kopie `~/printer_data.vor-umzug-<zeit>` nach einer Woche ohne Probleme löschen. Danach sichert der Timer täglich nach
`/daten/sicherung/config/` (`systemctl list-timers k2-config-sicherung.timer`).

### Schritt 21 – Root schreibgeschützt (nur mit eigenem Schalter)

```
sudo ./install-traeger.sh --readonly --trocken
sudo ./install-traeger.sh --readonly
```

Das Skript warnt und verlangt `JA`. Es prüft vorher: `/daten` eingehängt und beschreibbar, `printer_data` liegt dort.
Dann: `raspi-config nonint enable_overlayfs`, **`overlayroot=tmpfs:recurse=0`** in `cmdline.txt` (ohne `recurse=0` legt
overlayroot auch über `/daten` ein RAM-Overlay – die Daten wären nach jedem Neustart weg), Journal nur im RAM,
`dphys-swapfile` aus, `/boot/firmware` read-only. Danach **Neustart** (vollständig: `sudo reboot`, 90 s warten) und
`sudo k2_readonly.sh status` → `Overlay AKTIV`, `/daten … rw`.

### Schritt 22 – Updates bei schreibgeschütztem Root

```
sudo k2_readonly.sh aus      # dann: sudo reboot
sudo k2_readonly.sh aus      # zweites Mal: /boot/firmware beschreibbar
sudo apt update && sudo apt full-upgrade
#   Klipper/Moonraker/Mainsail über die Update-Verwaltung in Mainsail
sudo k2_readonly.sh ein      # dann: sudo reboot
sudo k2_readonly.sh status
```

Kleine Änderungen an `/etc` ohne Neustart: `sudo overlayroot-chroot` (Änderungen bleiben), danach `exit`.

### Konfig-Sicherung auf einen PC (Vorlage)

In `/etc/default/k2_config_sicherung` die Zeile `K2_SICHERUNG_RSYNC_ZIEL=<pc-benutzer>@<pc-name>:<zielordner>/`
eintragen; root braucht einen SSH-Schlüssel ohne Passwort für den PC (`sudo ssh-keygen`, `sudo ssh-copy-id …`).
Schlägt die Kopie fehl (PC aus), bleibt die Sicherung auf `/daten` trotzdem bestehen.

---

## Teil D – Rückweg zum IO-Board

1. Auf dem Pi: `sudo systemctl disable k2-stromausfall` (auf dem IO-Board ist GPIO27 offen; der Wächter wird ohne
   INA226 zwar nie scharf, abgeschaltet ist es eindeutig).
2. **Ausschalten** (vollständig): `sudo poweroff`, warten bis D12 (ACT) 10 s aus ist, Drucker am Hauptschalter aus,
   30 s warten.
3. Kabel vom Träger lösen: J7 (24 V), J1 (Flachkabel), J12 (Display).
4. CM5 vom Träger auf das IO-Board setzen.
5. Verbindungen der Tabelle in Schritt 10 **wieder herstellen**, jede Zeile einzeln: Main-MCU TX IO-Board Pin 32 ↔
   Breakout 30, Main-MCU RX Pin 33 ↔ Breakout 32, Düsen-MCU TX Pin 7 ↔ Breakout 26, Düsen-MCU RX Pin 29 ↔ Breakout 28
   (alle über 1 kΩ), GND Pin 39 ↔ Breakout-GND, RS485-Adapter A ↔ Breakout 20, B ↔ Breakout 19, Adapter-USB ↔ IO-Board
   USB-A, Breakout ↔ Original-Flachkabel (Bridge-Board J14), Kamera über den Adapter an IO-Board USB-A.
6. **Einschalten**: Drucker am Hauptschalter ein, 30 s warten, Pi-Netzteil (USB-C) am IO-Board einstecken, 90 s warten.
7. Klipper-Konfig zurück: in `k2-module.cfg` `[k2_485]` wieder `serial: /dev/serial/by-id/usb-…` (Adapter, Name per
   `ls -l /dev/serial/by-id/`) und die Zeile `rs485_de: kernel` löschen (oder `rs485_de: adapter`). `[mcu]`/`[mcu nozzle_mcu]`
   bleiben `/dev/ttyAMA4`/`/dev/ttyAMA2`.
8. `config.txt` darf bleiben (UART2/UART4 sind dieselben). Wer ihn entfernen will: in `/boot/firmware/config.txt` den Block
   zwischen `# >>> K2-TRAEGER-ALPHA1` und `# <<< K2-TRAEGER-ALPHA1` löschen und die `#K2T-alt# `-Zeilen wieder
   einkommentieren (Original: `/var/backups/k2-traeger/<zeit>/boot/firmware/config.txt`). Read-only-System und Datenpartition
   funktionieren auf dem IO-Board unverändert (die eMMC wandert mit).
9. `sudo systemctl restart klipper`, `KTWO_BUS_STATUS` → `de=adapter`, `offen=True`.

---

## Teil E – Später: CAN-Toolhead

- Die **24 V des Toolheads kommen nicht vom Träger**, sondern **direkt vom Netzteil** (freie Klemme bzw. Y-Abzweig) mit
  einer **eigenen Kabelsicherung 5 A** (z. B. fliegender Halter), Leitung 18 AWG (Hardware-README „Toolhead-Versorgung“).
- **CAN über J9b** (JST GH, stehend: H = CANH, L = CANL, G = GND). Abschluss: JP_CAN_TERM auf dem Träger gesteckt + 120 Ω am
  Toolhead-Ende.

| Verbindung | Ende am Träger | anderes Ende |
|---|---|---|
| CANH | J9b Kontakt „H“ | Toolhead-Board CANH |
| CANL | J9b Kontakt „L“ | Toolhead-Board CANL |
| GND (CAN-Bezug) | J9b Kontakt „G“ | Toolhead-Board GND |
| +24 V Toolhead | – (nicht vom Träger) | Netzteil +24 V über 5-A-Kabelsicherung → Toolhead-Board +24 V |
| GND Toolhead | – | Netzteil GND → Toolhead-Board GND |

- UUID (Klipper gestoppt): `~/klippy-env/bin/python ~/klipper/scripts/canbus_query.py can0`, dann die Vorlage
  `[mcu toolhead]` aus `klipper-ausschnitte.cfg` einkommentieren und die UUID eintragen.

---

## config.txt – Zeile für Zeile, mit Quellen

Hauptquelle: [Overlay-README, rpi-6.18.y](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm/boot/dts/overlays/README).
Die Kommentare im Block sind absichtlich kurz: eine Kommentarzeile > ≈ 80 Zeichen ließ am IO-Board schon einmal die
folgende `dtoverlay`-Zeile verschwinden (PI-INBETRIEBNAHME 0.3).

| Zeile | Wirkung | Quelle / Bewertung |
|---|---|---|
| `force_eeprom_read=0` | Firmware sucht kein HAT-EEPROM auf ID_SD/ID_SC (dort hängt das Display) | [config.txt-Doku](https://www.raspberrypi.com/documentation/computers/config_txt.html); schadet ohne HAT nicht |
| `usb_max_current_enable=1` | USB-Ports voller Strom, obwohl kein PD-Netzteil erkannt wird | config.txt-Doku; dazu `PSU_MAX_CURRENT=5000` im EEPROM (Schritt 5, ANNAHME) |
| `dtparam=i2c_arm=on` | I2C1 auf GPIO2/3 → `/dev/i2c-1` | Overlay-README „i2c_arm“ |
| `dtoverlay=k2-traeger-ina226` | INA226 0x40 (5 mΩ) und 0x41 (10 mΩ) als hwmon | eigenes Overlay; Bindung `shunt-resistor` in µΩ: [ina2xx.rst](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/Documentation/hwmon/ina2xx.rst). Das Standard-Overlay `i2c-sensor` kennt nur INA238, nicht INA226 |
| `dtoverlay=uart2-pi5` | UART2 auf GPIO4/5 → `/dev/ttyAMA2` (Düse) | README „uart2-pi5: Enable uart 2 on GPIOs 4-5“; **ohne** `ctsrts` (würde GPIO6/7 belegen); am IO-Board live bestätigt (22.10.2026) |
| `dtoverlay=uart4-pi5` | UART4 auf GPIO12/13 → `/dev/ttyAMA4` (Main) | README „uart4-pi5: … GPIOs 12-13“; **ohne** `ctsrts` (würde GPIO14/15 = UART0 belegen) |
| `dtoverlay=uart0-pi5,rs485` | UART0 auf GPIO14/15 → `/dev/ttyAMA0`; `rs485` setzt `linux,rs485-enabled-at-boot-time` und schaltet RTS auf GPIO17 (dazu CTS auf GPIO16) | README „uart0-pi5 … rs485: Enable RS485 mode for using the RTS line to drive the OE pin … also enables the UARTx ctsrts parameter“; [uart0-pi5-overlay.dts](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm/boot/dts/overlays/uart0-pi5-overlay.dts). RTS standardmäßig **aktiv high** (`rs485_invert_rts` nicht setzen) |
| `dtoverlay=mcp251xfd,spi0-0,oscillator=40000000,interrupt=25` | MCP2518FD an SPI0 CE0, Quarz 40 MHz, nINT GPIO25 → `can0` | README „mcp251xfd“; [mcp251xfd-overlay.dts](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/arch/arm/boot/dts/overlays/mcp251xfd-overlay.dts) (Interrupt pegelgesteuert low). SPI0 belegt dabei auch GPIO7 (CE1, auf dem Träger offen) |
| `display_auto_detect=0` | keine automatische Display-Erkennung, das Overlay steht fest | config.txt-Doku; **ANNAHME**: verhindert ein doppeltes Laden |
| `dtoverlay=vc4-kms-dsi-7inch` | DSI1 (= MIPI1, J12) + I2C `i2c_csi_dsi` = auf dem CM5 mit CM5IO-Device-Tree „i2c-0 on 0 & 1“ = ID_SD/ID_SC | README „vc4-kms-dsi-7inch“ und „i2c_csi_dsi … CM5 on CM5IO: i2c-0 on 0 & 1“; [Waveshare-Wiki 4.3inch DSI LCD](https://www.waveshare.com/wiki/4.3inch_DSI_LCD): „DSI1 Use: dtoverlay=vc4-kms-dsi-7inch“, für Pi 5 **Rev 2.2**. Braucht `dtoverlay=vc4-kms-v3d` (Standard in Raspberry Pi OS) |

### RS485: DE in Hardware (Kernel), nicht per GPIO in Software

- Der THVD1450 hat DE und ~RE zusammen an **GPIO17**; GPIO17 ist **UART0-RTS** des RP1. Mit `dtoverlay=uart0-pi5,rs485`
  schaltet der PL011-Treiber RTS selbst: vor dem ersten Bit high, nach dem Leeren des Schieberegisters sofort low
  (`pl011_rs485_tx_start`/`pl011_rs485_tx_stop` in
  [amba-pl011.c](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/drivers/tty/serial/amba-pl011.c), auch für die
  RP1-UARTs „PL011 AXI“, die `uart_get_rs485_mode` aufrufen). Im RS485-Modus ignoriert der Serial-Core RTS-Befehle von
  pyserial (`uart_update_mctrl` in
  [serial_core.c](https://github.com/raspberrypi/linux/blob/rpi-6.18.y/drivers/tty/serial/serial_core.c)) – `k2_485`,
  `k2-bootstart` und `k2-identify` brauchen deshalb keine Sonderbehandlung.
- `rs485_de: kernel` in `[k2_485]` prüft beim Öffnen per `TIOCGRS485` und setzt notfalls per `TIOCSRS485` (ENABLED +
  RTS_ON_SEND, ohne RTS_AFTER_SEND). Das ersetzt **nicht** das Overlay: ohne `rs485` liegt RTS gar nicht auf GPIO17.
- **DE per GPIO in Software** wurde verworfen: Bei 230400 Baud ist ein Zeichen ≈ 43 µs lang; DE muss danach sofort fallen,
  sonst überfährt der Pi die Antwort des Motors. `tcdrain` wartet im Kernel in Jiffy-Schritten (≥ 4 ms), und `k2_485`
  läuft in einem Python-Hilfs-Thread (GIL-Wechsel bis 5 ms) – nicht verlässlich.
- Ohne CM5 hält R13 (10 kΩ) DE low → der Träger hört nur zu, stört den Bus nicht.

### Stromausfall-Wächter – Details

- INA226 0x40, Bus-Unterspannung **20 V**: Alert-Limit 0x07 = 20000 mV / 1,25 mV = **16000 = 0x3E80**, Mask/Enable 0x06 mit
  BUL-Bit **0x1000** (Hardware-README „Stromausfall-Erkennung“, [INA226-Datenblatt](https://www.ti.com/lit/ds/symlink/ina226.pdf)).
  Mit dem Overlay schreibt der Linux-Treiber das selbst (`in1_lcrit`). **Wichtig:** Der Treiber stellt beim Laden
  **ALERT-Latch EIN** (`ina2xx.c`, `ina2xx_init`) – ALERT bleibt nach einem Einbruch low, bis Register 0x06 gelesen wird.
  Die Hardware-README nennt „ohne Latch“; der Wächter rechnet mit beidem.
- Mittelung auf 1 (≈ 2,2 ms je Messung, `update_interval` = 2) statt Treiber-Standard 16 (≈ 35 ms).
- Scharf erst mit GPIO27 high **und** lesbarem INA226 **und** ≥ 21 V seit 2 s. An der Notfall-USB-C-Buchse ohne 24 V
  (ALERT dauerhaft low) und auf dem IO-Board (kein INA226) wird er nie scharf.
- Ablauf im Ernstfall (gemessen wird jeder Schritt, Ergebnis im Journal): Flanke bestätigen (1 ms) → Klipper und Moonraker
  einfrieren (`cgroup.freeze`) → Logzeile → `sync` → Logzeile mit Zeit + `fsync` → `/daten` `remount,ro`, bei `EBUSY`
  (eingefrorene Prozesse halten Dateien offen) **FIFREEZE** (schreibt alles, leert das Journal, hält weitere Schreiber an)
  → optional `sysrq s+u` (`K2SA_SYSRQ=1`).
- Kommt die Spannung zurück (selten – meist ist der CM5 dann schon aus): nur eine Meldung im Journal; nichts wird
  aufgetaut oder neu gestartet. Weiter: `sudo k2_stromausfall --freigeben && sudo reboot`.
- Die Haltezeit (24 V fällt unter 20 V → 5,2-V-Wandler setzt aus) ist **nicht bekannt** (ANNAHME: einige 10 ms, hängt von
  den Kondensatoren im Netzteil und der Last ab) – Schritt 17 misst sie indirekt.

---

## Offene Annahmen

1. Gerätenamen `ttyAMA0/2/4` folgen dem DT-Alias (am IO-Board für 2/4 bestätigt, 0 erwartet).
2. CM5 auf dem Träger bootet mit dem Standard-Device-Tree `bcm2712-rpi-cm5-cm5io` (dann `i2c_csi_dsi` = GPIO0/1 wie
   verdrahtet). Prüfen: `sudo i2cdetect -l` zeigt i2c-0 auf GPIO0/1, Display-I2C antwortet.
3. Touch des Waveshare 4.3": Waveshare nennt einen Goodix-Controller, das Overlay bringt den FT5406-Treiber mit – ob der
   Touch ohne Zusatz läuft, am Muster prüfen (Bild sollte gehen).
4. `display_auto_detect=0` nötig/sinnvoll (sonst evtl. doppeltes Display-Overlay).
5. `PSU_MAX_CURRENT=5000` ohne PD-Netzteil sinnvoll (KONZEPT).
6. overlayroot `recurse=0` lässt `/daten` beschreibbar – aus Sekundärquellen
   ([atomicobject](https://spin.atomicobject.com/protecting-ubuntu-root-filesystem/),
   [Launchpad-Merge „recursive“](https://code.launchpad.net/~smoser/cloud-initramfs-tools/recursive/+merge/116351)); nach
   Schritt 21 mit `findmnt /daten` (ext4, rw) prüfen.
7. Haltezeit für den Stromausfall-Ablauf (Schritt 17).
8. Verkabelung heute (Schritt 10) laut Unterlagen – vor dem Abbau mit dem tatsächlichen Aufbau vergleichen.
9. Ethernet handelt auf 2 Paaren 100 Mbit/s aus (sonst `ethtool -s eth0 advertise 0x008`).

## Tests

```
python3 traeger-alpha1/test_traeger_config.py     # Schaltplan ↔ config.txt ↔ Klipper ↔ Dienste
python3 traeger-alpha1/test_k2_stromausfall.py    # Logik des Stromausfall-Wächters
python3 test_k2_485.py                            # inkl. rs485_de
```
