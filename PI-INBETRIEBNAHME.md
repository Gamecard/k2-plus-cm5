# Pi-Inbetriebnahme K2 Plus – Fahrplan (Upper-Board T113 → Raspberry Pi CM5)

> **ENTWURF, Stand 19.10.2026 (Phase 0/1 auf Anschluss-Variante B umgestellt). Nichts davon ist bisher am echten
> Gerät mit dem Pi gelaufen.**
> Dieser Fahrplan ordnet alle offenen „Am Gerät“-/„Gerätetest offen“-Punkte in eine sichere Reihenfolge:
> erst verbinden und lesen, dann Geräte starten, Motor-Init, Homing, PrTouch, Heizen, Kammer, CFS, Belt,
> z_align und zuletzt der erste Druck. Befehle, Makronamen, Parameter und Erwartungswerte stammen aus den
> unten genannten Quellen. Wo die Quellen keinen Befehl nennen, steht **„(Befehl noch festzulegen)“**.
> Zugangsdaten, Geräteadressen und Kennungen stehen bewusst nicht hier. Platzhalter: `<pi-ip>`,
> `<uart-main>` (**bestätigt 22.10.2026: Pi-UART4, `/dev/ttyAMA4`**), `<uart-duese>` (**bestätigt: Pi-UART2,
> `/dev/ttyAMA2`**), `/dev/serial/by-id/<rs485-adapter>`; nur für Anhang E (Variante A):
> `/dev/serial/by-id/<rs232-adapter>`, `/dev/serial/by-id/<rs232-adapter-duese>`.

**Anschluss (gewählt 19.10.2026): Anschluss-Variante B.** Der Pi ersetzt nur das T113-Board. Das Original-Flachkabel
(50 Pole, 0,5 mm) bleibt in **J14** des Bridge-Boards `F008-PC_V15`; sein T113-Ende steckt in einem FFC-Breakout
(50P 0,5 mm → 2,54 mm). Die Pi-UARTs (3,3 V) speisen dort die TTL-Seite der beiden RS232-Wandler ein:
**U6** (3232E) → J10 ↔ J22 → Main-MCU, **U7** (3232E) → 14-pol. Toolhead-Kabel → Düsen-MCU. J10/J22-Kabel,
Toolhead-Kabel, SYNC und 24 V bleiben original. RS485 über einen eigenen USB-RS485-Adapter, Kamera direkt an Pi-USB.
Rückfallweg mit RS232-Konvertern: **Anhang E (Variante A)**.

**Quellen (nur gelesen):** [`PI-ANSCHLUSS-LOWERBOARD.md`](PI-ANSCHLUSS-LOWERBOARD.md) (§8–§8.5: Bridge-Board, J14,
abgeleitete J22-Belegung, Messplan B1–B4), [`pi-host/install/README.md`](pi-host/install/README.md),
`pi-host/install/install.sh` + `k2cfg.py` (Serial-Optionen), [`pi-host/README.md`](pi-host/README.md) (k2_motor),
`pi-host/README-k2_*.md`, `pi-host/config-pi/*.cfg`, [`pi-host/config-pi/README.md`](pi-host/config-pi/README.md),
[`BOOTSTART-WERKZEUG.md`](BOOTSTART-WERKZEUG.md) (Nachtrag 18.10.2026: F1/F2); Erwartungswerte aus der
Protokollanalyse und aus Live-Mitschnitten am Original-Drucker.
**Extern (Raspberry Pi, nur gelesen, Stand 19.10.2026):** Overlay-README `boot/overlays/README` im Repo
`raspberrypi/firmware` (Abschnitte `uart2-pi5`, `uart4-pi5`); Doku „Configure UARTs“
(`raspberrypi/documentation`, `computers/configuration/interfaces.adoc`); `pinctrl`-Quellen (`raspberrypi/utils`,
`pinctrl/gpiochip_rp1.c` = RP1-Funktionstabelle, `pinctrl/gpiolib.c` = 40-Pol-Header-Zuordnung); Kernel
`raspberrypi/linux` Branch `rpi-6.18.y` (`bcm2712-rpi.dtsi` Aliases, `drivers/tty/serial/amba-pl011.c`). Befund: 0.3.

**Abkürzungen:** **cfg-README** = `pi-host/config-pi/README.md`.
**install-README** = `pi-host/install/README.md`. **N1/N2/N3** = Not-Aus-Stufen (§2 Regel 2).
**PI-ANSCHLUSS** = `PI-ANSCHLUSS-LOWERBOARD.md`. **Anschluss-Variante A/B** = Pi-Anschluss (PI-ANSCHLUSS §8.2);
**zalign-Variante B** = Modul `k2_zalign` in Phase 11 (zalign.cfg) – nicht verwechseln.
**U6/U7 Pin n** = Pin des SOIC-16-Wandlers 3232E in Standard-3232-Belegung (11/10 = T1IN/T2IN, 12/9 = R1OUT/R2OUT,
14/7 = T1OUT/T2OUT, 13/8 = R1IN/R2IN, 16 = VCC, 15 = GND; am Datenblatt TPT3232E gegenprüfen, PI-ANSCHLUSS §8).
**TxIN/RxOUT** = TTL-Seite des Wandlers (TxIN: Pi sendet hinein, RxOUT: MCU-Daten kommen heraus).

---

## 1. Kurzüberblick

| Phase | Inhalt | Höchste Gefahrenstufe | Neu freigegebene Schalter |
|---|---|---|---|
| 0 | Vorbereitung: Stock-Daten sichern, Paket installieren, Pi-UARTs aktivieren, Bridge-Board messen (B2–B4), Breakout an J14, RS485 messen, Pi ↔ Breakout verdrahten | lesen (Messgerät, teils unter Spannung) + Handgriff stromlos | – (nur Pi-Overlays, kein Schutzschalter) |
| 1 | Verbinden und lesen: Main-/Düsen-MCU an den Pi-UARTs erkennen, Klipper ready, Sensoren, lis2dw im Stand | lesen | – |
| 2 | RS485-Bus lesend, Geräte aus dem Bootloader in der App? | lesen | – |
| 3 | Motor-Init (Trockenlauf → lesend → schreibend), CFS-Adressvergabe | Bus-Schreiben | `[k2_motor] allow_write`, `auto_init`; `[k2_box] allow_write` (nur 3.6) |
| 4 | X/Y-Homing, Stall-Polarität, Fahrgrenzen X/Y, Schneidposition | Bewegung | `_PI_FREIGABE allow_move` (Laufzeit) |
| 5 | PrTouch, Z-Homing, z_tilt, Bett-Mesh, Z-Grenzen | Bewegung | `[k2_prtouch] allow_move` |
| 6 | Heizen und Sicherheitsabschaltungen (k2_safety, PID, Temperaturkompensation) | Heizen | – |
| 7 | Kammerheizung und PTC-Lüfter (k2_chamber) | Heizen | – |
| 8 | Input-Shaper (optional) | Bewegung | – |
| 9 | CFS außerhalb eines Drucks: lesen, Kopffahrten, Laden/Entladen (Slots A–D), Spülen, Preload; externe Spule (9.8) | Bus-Schreiben + Bewegung + Heizen | `[k2_box] allow_move`, `allow_write`; optional `extern_rfid` (9.8) |
| 10 | Belt (Riemenspannung mdlx/mdly) | Bus-Schreiben + Bewegung | `[k2_belt …] allow_write`/`allow_move` (je Achse, danach zurück) |
| 11 | z_align / Dual-Z (A10, T3–T8) | Bewegung | `[k2_zalign] allow_move` (nur T5–T7) |
| 12 | Erster Druck und Druckfälle (PAUSE/RESUME, CFS-Abbruch, CANCEL, Restmaterial, Runout, Stall `act`) | alles | `resume_redo_change`, `runout_refill`, `stall_monitor: act` (je einzeln) |

Jede Phase setzt voraus, dass die vorige **grün** ist (alle Schritte mit Ergebnis „wie erwartet“ im Protokoll, Anhang D).
Innerhalb einer Phase setzt jeder Schritt den vorigen voraus, sofern beim Schritt keine andere Voraussetzung steht.
Optionale Schritte dürfen entfallen, ohne dass die Phase rot wird.

---

## 2. Allgemeine Regeln (gelten in jeder Phase)

1. **Immer eine Person am Netzschalter**, sobald sich etwas bewegen oder heizen kann. Bett leer, Düse sauber,
   nichts im Fahrweg. Jeder Schritt mit Bewegung nur mit Einzelfreigabe (cfg-README §4).
2. **Not-Aus-Stufen:**
   - **N1 Netzschalter** des Druckers: bei jeder unerwarteten Bewegung, Kollision, Geräusch/Blockade, Geruch oder
     Heizung außer Kontrolle. Hängt der Pi am Drucker-Netzteil (Buck), geht er mit aus –
     Sicherheit geht vor. In der Testphase hat der Pi ein **eigenes USB-C-Netzteil** (0.10) und läuft nach N1 weiter
     (Klipper verliert dann die MCUs); danach Klipper stoppen bzw. den Pi herunterfahren (Regel 4, Rückspeisung).
   - **N2 `M112`**: Klipper-Not-Halt (Shutdown, `emergency_stop` an alle MCUs). „`M112` bleibt der Not-Aus“
     (README-k2_box §17).
   - **N3 `FIRMWARE_RESTART`**: nach einem Shutdown oder Fehler, wenn nichts Gefährliches passiert ist. Danach den
     Zustand neu prüfen.
3. **T113 und Pi nie gleichzeitig an J14** (allgemein: Upper-Board und Pi nie gleichzeitig an derselben Leitung).
   Pi-Betrieb (Anschluss-Variante B) heißt: Original-Flachkabel aus dem T113-Board gezogen und ins Breakout gesteckt;
   das Kabel bleibt in J14, J10↔J22- und Toolhead-Kabel bleiben original. Rückweg: erst alle Pi-Leitungen weg, dann
   das Flachkabel zurück ins T113 (Anhang C).
4. **J14/Breakout und RS232-Seite:**
   - Nur bei ausgeschaltetem Drucker **und** ausgeschaltetem Pi stecken oder ziehen; GND zuerst verbinden.
   - Flachkabel nur mit geöffnetem Klappbügel bewegen, nicht knicken; **Halteohren nicht abschneiden**, Original-
     Kabel nicht zerschneiden (Rückweg zum Stock).
   - **5V-Stifte des Breakouts nie an den Pi** (und nie an andere Stifte). Der Pi hat sein eigenes Netzteil.
   - **RX-Leitungen zum Pi immer über 1 kΩ in Reihe.** Zusätzlich 2 kΩ nach GND (Spannungsteiler 5 V → 3,3 V)
     **nur**, wenn U6/U7 Pin 16 in B2 (0.4) ≈ 5 V zeigt – je Wandler getrennt entscheiden.
   - **Nie RS232-Leitungen an Pi-Pins oder USB-TTL-Adapter:** TX7/RX7 (J10), TX1/RX1 (Toolhead-Stecker),
     J22 Pin 4/10, U6/U7 Pin 14/7/13/8 – dort liegen ±5,5 V. Ebenso nie U6/U7 Pin 2/6 (Ladungspumpe) berühren.
   - **J22 Pin 12** (+5,2 V, 5V-IN des Bridge-Boards) nie anschließen. **Kein USB-A↔USB-A-Kabel** zwischen Pi und
     Bridge-Board (J3/J18 sind Hub-Ausgänge; PI-ANSCHLUSS §8.3).
   - **Rückspeisung (eigene Ableitung, offen):** Läuft der Pi bei ausgeschaltetem Drucker, liegen seine TX-Pins
     (Ruhepegel High, 3,3 V) an den Eingängen der stromlosen 3232E. Empfehlung: auch in die beiden TX-Leitungen
     1 kΩ in Reihe (begrenzt den Strom; bei 230400 Baud unkritisch – ANNAHME: Bitzeit ≈ 4,3 µs gegen RC ≈ 1 kΩ × einige
     10 pF) und den Pi nicht lange laufen lassen, während der Drucker aus ist. Reihenfolge: Drucker an → Pi an;
     Pi herunterfahren → Drucker aus.
   - Multimeter auf DC-Volt (nicht V~/VFD/LoZ). Am Bridge-Board schwarz an den Testpunkt „GND“ neben J14, am
     Mainboard an −V der 24-V-Klemme (PI-ANSCHLUSS §6, §8.3). Spitzen nur auf Pads/IC-Beine, 24-V-Bereich des
     Bridge-Boards (2-pol. Eingang, Toolhead-Stecker Reihe „24V“) nicht berühren.
5. **24 V nie auf Logik- oder Busleitungen.** RS485 immer mit GND, zunächst **ohne** 120-Ω-Abschluss. Die zwei
   Buffer-Leitungen der CFS unverändert durchführen, nicht auf RS485 legen.
6. **Schutzschalter:** Alle `allow_move`/`allow_write` sind ab Werk `False` (cfg-README §1). Ein Schalter wird erst
   in der Phase auf `True` gestellt, in der er gebraucht wird, und danach wieder zurück. Ausnahme: Schalter, die
   spätere Phasen voraussetzen, bleiben stehen (Tabelle §3). Am Ende jedes Testtags den Schalter der gerade
   laufenden Phase zurückstellen. Nach jeder Config-Änderung `FIRMWARE_RESTART`.
7. **Gesperrt und nicht Teil dieses Fahrplans:**
   - Motor-Kalibrierung (Motor-Befehle **0x03** Encoder, **0x04** Elektrik-Offset, **0x07** Flash-Parameter),
   - jedes **Bootloader-Flashen** (fun 0xF0 Sub 01 und Datenblöcke, `mcu_util`-, `mcu_util_485`- und SWD-Wege),
   - **CFS-DB-Erase** (`F0 [06]`, Page 0x0807F800),
   - außerdem 0x50 an die CFS, Adresse 0xF0 bzw. fun 0xF2, `FF 0B [00]`, `FF 01`.
   Der Sendeschutz (`check_tx_frame`, `k2_transport`) sperrt diese Frames. Er wird nicht
   umgangen. `k2-bootstart.py` (2.5) sendet keinen dieser Frames. Es hat eine eigene, engere Whitelist zusätzlich zum
   Sendeschutz und ersetzt nur dessen Sperre der Loader-Adressen für F0 `[00]`/`[03]`/`[02]` (BOOTSTART-WERKZEUG §5).
   `KTWO_BELT_CALI`/`KTWO_BELT_MOVE` gehören ebenfalls nicht zum Fahrplan (nur bei Bedarf mit
   Einzelfreigabe, README-k2_belt §6 Punkt 6).
8. **Nach jedem Schritt `klippy.log` prüfen:** SHUTDOWN, Timeouts, Config-Fehler, Tracebacks, „Timer too close“
   (Bus-Zugriffe laufen im Reaktor, cfg-README §6.2). Ergebnis ins Protokoll (Anhang D).
9. **Nach jedem Neustart des Klipper-Dienstes sofort `FIRMWARE_RESTART`.** Ohne MCU-Reset bleibt der
   Extruder-Motor (transparent über die Düsen-MCU) stumm → key798.
10. **Heizungen gehen nie automatisch aus** (`[idle_timeout] timeout: 99999999`, printer.cfg). Nach jedem
    Heizschritt `TURN_OFF_HEATERS`.
11. **Ein Schritt = eine Änderung.** Abbruch heißt STOP: nichts nachjustieren, Log sichern, Ursache klären, Schritt neu
    beginnen. Ist die Ursache nicht schnell klärbar: Rückweg zum Stock (Anhang C).
12. **Vorsichtsregel dieses Fahrplans (eigene Ableitung):** `k2-identify.py` immer mit ausdrücklicher Portangabe
    aufrufen (`<uart-main>`, `<uart-duese>`). Ohne Angabe fragt das Skript nur `/dev/serial/by-id/*` ab
    (install-README §4b): Die Pi-UARTs sind keine USB-Geräte und stehen dort voraussichtlich nicht; steckt der
    USB-RS485-Adapter und ist `[k2_485] serial` noch ein Platzhalter, gingen Klipper-`identify`-Frames auf den
    RS485-Bus. Aus demselben Grund `install.sh --main-serial`/`--duese-serial` mit Pfad statt `auto` (1.3).

---

## 3. Schalterstand je Phase

| Schalter | Ort | Auf `True` in | Danach |
|---|---|---|---|
| Pi-Overlays `dtoverlay=uart2-pi5`, `dtoverlay=uart4-pi5` (kein Schutzschalter) | Pi: `/boot/firmware/config.txt` | 0.3 | bleiben (Pi-UARTs für Main-/Düsen-MCU) |
| `[k2_motor] allow_write` | k2-motor.cfg | 3.3 | bleibt `True` (Voraussetzung für X/Y-Homing ab Phase 4); bei Problemen zurück |
| `[k2_motor] auto_init` | k2-motor.cfg | 3.5 | bleibt `True` |
| `[k2_motor] stall_monitor` | k2-motor.cfg | ab Werk `report`; `act` erst in 12.8 | – |
| `_PI_FREIGABE allow_move` | homing.cfg (`variable_allow_move`), Laufzeit `ALLOW_MOTION` | 4.2; **seit 23.10.2026 Standard `True`** (Inbetriebnahme abgeschlossen, Nutzerwunsch) | für Fehlersuche/erneute Inbetriebnahme wieder `False` |
| `[k2_prtouch] allow_move` | printer.cfg | 5.2 | bleibt `True` (G28 Z braucht es) |
| `[k2_prtouch] diag_readout` | printer.cfg | 5.2 (optional) | nach 5.3 zurück `False` |
| `[k2_prtouch] pres_check` | printer.cfg | 9.7 (optional) | danach zurück `False` |
| `[k2_box] allow_write` | k2-module.cfg | 3.6 (nur Adressvergabe), dann ab 9.3 | nach 3.6 zurück `False`; ab 9.3 bleibt `True` |
| `[k2_box] allow_move` | k2-module.cfg | 9.2 | bleibt `True` (Reinigung in G28/START_PRINT, Wechsel) |
| `[k2_box] extern_rfid` + `[gcode_button extern_spule]` | k2-module.cfg + printer.cfg (einkommentieren) | 9.8 (optional) | nach Entscheidung; ab Werk `False`/auskommentiert |
| `[k2_belt mdlx]`/`[k2_belt mdly]` `allow_write` + `allow_move` | k2-module.cfg | 10.4 bzw. 10.5 (je eine Achse) | **seit 23.10.2026 dauerhaft `True`** für das Makro `BELT_TENSION` |
| `[k2_zalign] allow_move` | zalign.cfg (zalign-Variante B) | 11.3 | nach Phase 11 zurück `False` (Entscheidung T8) |
| `[k2_box] resume_redo_change` | k2-module.cfg | 12.5 (zuletzt) | zurück `False`, falls nicht dauerhaft gewollt |
| `[k2_box] runout_refill` | k2-module.cfg | 12.7 | nach Entscheidung |
| `[k2_safety] fakeshutdown_guard` | k2-module.cfg | immer `True` | nie abschalten (nur Fehlersuche) |

---

## Phase 0 – Vorbereitung (Stock-System noch intakt bzw. Drucker stromlos)

Ablauf: 0.1–0.3 ohne Eingriff am Drucker; 0.4 misst am laufenden Original (T113 noch dran); ab 0.5 ist das T113 vom
Flachkabel getrennt und das Breakout steckt. Die Messschritte B1–B4 stammen aus PI-ANSCHLUSS §8.3 (B1 erledigt:
J14 = 50 Kontakte, 0,5 mm, §8.5). Benötigte Teile: FFC-Breakout 50P 0,5 mm → 2,54 mm mit Haltenase, Stiftleisten,
Steckbrücken Buchse-Buchse, Widerstände (Sortiment 1/4 W mit 1 kΩ/2 kΩ).

**0.1 Stock-Daten sichern** · Gefahr: lesen
- **Ziel:** Creality-Materialdatenbank, Düsen-Material-Zustand und Kalibrierwerte des Stock-Systems als Startwerte
  für den Pi.
- **Stand 19.10.2026:** Eine Stock-Sicherung liegt vor (nur lokal, **nie ins Repo**): Config-Ordner mit der aktuellen
  `printer.cfg` samt `SAVE_CONFIG`-Block (u. a. `cut_pos_x` −7,80, Input-Shaper, Bett-Mesh `default`) und die
  Box-Benutzerdaten, darin `material_database.json` und `remain_material_data.json`.
- **Voraussetzung:** nur für ein Nachholen: Stock-Upper-Board läuft noch, Drucker im Netz.
- **Befehl:** Nur nötig, wenn seit der Sicherung am Stock gedruckt oder Material gewechselt wurde:
  `remain_material_data.json` (bei Bedarf auch `material_database.json`) frisch per `scp` holen (Pfade und Befehle:
  install-README §4a). Dateien nur lokal ablegen.
- **Kalibrierwerte:** Die Übernahme der Stock-`SAVE_CONFIG`-Werte in die Pi-Config ist als install.sh-Option
  `--stock-printer-cfg` **vorgesehen** (in Arbeit, noch nicht im Paket). Bis dahin gilt das bisherige
  `SAVE_CONFIG`-Verhalten von install.sh (install-README §4).
- **Erwartet:** zwei gültige JSON-Dateien; remain_material im Format `{"remain_material": {"color": "0RRGGBB", "type": "…"}}`.
- **Abbruch → tun:** Datei fehlt → ohne sie weiter (k2_box nutzt Festwerte bzw. spült beim ersten Wechsel pauschal
  90 + 70 mm); später nachholbar, solange das Stock-Board existiert.
- **Verweis:** PI-ANSCHLUSS §6 letzter Punkt; install-README §4a (`install.sh --remain-material` als Startwert).

**0.2 Paket bauen und auf dem Pi installieren (noch ohne Drucker)** · Gefahr: lesen
- **Ziel:** Upstream-Klipper 9cb9975 + K2-Module + Config auf dem Pi; alle Offline-Tests grün.
- **Voraussetzung:** 0.1.
- **Befehl:** Arbeits-PC: `pi-host/install/pack.sh --tests` (Ausgabeordner: install-README §2, oder `-o <ordner>`).
  Dann `scp k2-pi-paket-<datum>.tar.gz pi@<pi-ip>:~`, auf dem Pi `tar xzf …`, `cd k2-pi-paket-<datum>`,
  `./install.sh --material-db material_database.json --remain-material remain_material_data.json`
  (ohne `--starten`; Serial-Pfade erst in 1.3; `--stock-printer-cfg …` erst, wenn vorhanden – 0.1).
- **Erwartet:** Schritt 6 von install.sh grün: alle `test_k2_*.py` (darunter `test_k2_485` mit dem Sendeschutz
  F0/F2/0xEA–0xEB), nachbau-Selbsttests, Batch-Lauf EXIT 0. Dienst bleibt gestoppt.
- **Abbruch → tun:** ein Test rot oder Batch ≠ EXIT 0 → **nicht** an den Drucker; Ursache offline klären.
- **Verweis:** install-README §2–§4, §4a; Sendeschutz hier nur als Voraussetzung über `test_k2_485`.

**0.3 Pi-UARTs aktivieren (Pi allein, nicht mit dem Drucker verbunden)** · Gefahr: lesen (Pi-Konfiguration)
- **ERGEBNIS Pi-Test 22.10.2026 (k2-identify, PI-ANSCHLUSS §8.6): Main-MCU an Pi-UART4 `/dev/ttyAMA4` (Breakout 30/32), Düsen-MCU an Pi-UART2 `/dev/ttyAMA2` (Breakout 26/28)** – also umgekehrt zur ursprünglichen Planung; überall unten gilt: `<uart-main>` = `/dev/ttyAMA4`, `<uart-duese>` = `/dev/ttyAMA2`. Beide Bootloader starten nach Kaltstart ohne Upper-Board selbst die App. FALLE config.txt: Kommentarzeile > ~80 Zeichen ließ die folgende `dtoverlay`-Zeile verschwinden → kurze Zeilen.
- **Ziel (ursprünglich):** zwei Hardware-UARTs am Pi: **UART2 → Main-MCU**, **UART4 → Düsen-MCU** (ursprünglicher
  Vorschlag, gegen die Raspberry-Pi-Quellen geprüft – Tabelle unten).
- **Voraussetzung:** 0.2; das Base-Board des CM5 führt GPIO 4, 5, 12, 13 an einen Header (Aufdruck/Doku des
  Base-Boards prüfen; die Header-Pinnummern unten gelten für die Standard-40-Pol-Belegung).
  Stand 21.10.2026: Base-Board = **Waveshare CM5-IO-BASE-B** (85 × 56 mm, 40-Pin-GPIO-Leiste „für HAT/HAT+“ =
  Standardbelegung). **Wichtig:** Das Board hat eine Wahl der GPIO-Spannung **IO-VREF 3,3 V (Werkseinstellung) /
  1,8 V** – sie muss auf **3,3 V** stehen (Bridge-Board-Ausgänge 28/32 liefern 3,3 V). Kontrolle vor dem Verdrahten:
  UART2-TX (Header-Pin 7) nach dem Aktivieren des Overlays gegen GND messen → ≈ 3,3 V im Ruhezustand, nicht 1,8 V.
- **Befehl (Pi):** in `/boot/firmware/config.txt` am Ende, nach einer Zeile `[all]`, eintragen:
  `dtoverlay=uart2-pi5` und `dtoverlay=uart4-pi5` (je eine Zeile, **ohne** Parameter `ctsrts` – sonst belegt UART2
  zusätzlich GPIO 6/7 und UART4 GPIO 14/15). Neustart (`sudo reboot`). Danach `ls -l /dev/ttyAMA*`,
  `dmesg | grep ttyAMA`, `pinctrl get 4,5,12,13`, `cat /proc/cmdline` (Konsole).
- **Erwartet:** zwei neue Geräte, erwartet `/dev/ttyAMA2` (UART2) und `/dev/ttyAMA4` (UART4) → als `<uart-main>` bzw.
  `<uart-duese>` notieren. `pinctrl` zeigt GPIO 4/5 = `TXD2`/`RXD2` und GPIO 12/13 = `TXD4`/`RXD4` (Alt-Funktion
  a2). Keines der beiden Geräte ist in `/proc/cmdline` als `console=` eingetragen. Der Benutzer, unter dem Klipper
  läuft, hat Lese-/Schreibrecht (`ls -l` Besitzer/Gruppe gegen `id` prüfen).
- **Optional Loopback (eigene Ableitung, Befehl nicht aus den Quellen):** Steckbrücke Pin 7 ↔ Pin 29 (nur Pi-Pins,
  Drucker nicht verbunden), dann
  `~/klippy-env/bin/python -c "import serial; s=serial.Serial('<uart-main>',230400,timeout=1); s.write(b'K2TEST'); print(s.read(6))"`
  → `b'K2TEST'`; ohne Brücke `b''`. Dasselbe mit Pin 32 ↔ Pin 33 und `<uart-duese>`. Brücken danach entfernen.
- **Abbruch → tun:** kein neues Gerät, andere Nummer oder andere Funktion an den GPIOs → Overlay-Zeilen, `[all]` und
  Konflikte prüfen (kein weiteres Overlay auf GPIO 4/5/12/13, z. B. `w1-gpio` mit Standard-GPIO 4 oder PWM auf
  GPIO 12/13). Eine andere Gerätenummer ist in Ordnung, wenn sie per `dmesg` eindeutig UART2/UART4 zugeordnet ist.
  Loopback ohne Echo → STOP vor jeder Verdrahtung.
- **Befund Pi-UARTs (Quellen geprüft 19.10.2026, nur lesend):**

  | Punkt | Ergebnis | Quelle |
  |---|---|---|
  | Overlay UART2 | `uart2-pi5`: „Enable uart 2 on GPIOs 4-5. Pi 5 only.“; `ctsrts` → GPIO 6-7 | `raspberrypi/firmware`, `boot/overlays/README` |
  | Overlay UART4 | `uart4-pi5`: „Enable uart 4 on GPIOs 12-13. Pi 5 only.“; `ctsrts` → GPIO 14-15 | dito |
  | Nicht verwenden | `uart2`/`uart4` ohne `-pi5` = „BCM2711 only“ (Pi 4: GPIO 0-3 bzw. 8-11) | dito |
  | CM5 | Pi 5, 500, 500+ und CM5: UART0–UART4 + UART10 (Debug), alle PL011, standardmäßig aus; `[cm5]` in config.txt sieht auch `[pi5]`-Inhalte | Doku „Configure UARTs“ (`computers/configuration/interfaces.adoc`); `computers/config_txt/conditional.adoc` |
  | Konsole | Primär-UART des Pi 5 = UART10 (Debug-Header); CM5 steht in dieser Tabelle nicht ausdrücklich → `/proc/cmdline` prüfen | `interfaces.adoc` |
  | TX/RX je GPIO | GPIO 4 = `TXD2`, GPIO 5 = `RXD2`, GPIO 12 = `TXD4`, GPIO 13 = `RXD4` (RP1, Spalte a2) | `raspberrypi/utils`, `pinctrl/gpiochip_rp1.c` |
  | Header-Pins | Pin 7 = GPIO 4, Pin 29 = GPIO 5, Pin 32 = GPIO 12, Pin 33 = GPIO 13 (Standard-40-Pol-Header) | `raspberrypi/utils`, `pinctrl/gpiolib.c` |
  | Gerätenamen | Kernel-DT: Alias `serial2` = UART2, `serial4` = UART4; der PL011-Treiber nummeriert `ttyAMA<n>` nach dem `serial`-Alias → **erwartet** `/dev/ttyAMA2`/`/dev/ttyAMA4`. Die Doku nennt ausdrücklich nur `ttyAMA0` (UART0) und `ttyAMA10` (Debug) → **auf dem Pi prüfen** | `raspberrypi/linux` `rpi-6.18.y`: `arch/arm64/boot/dts/broadcom/bcm2712-rpi.dtsi`, `drivers/tty/serial/amba-pl011.c`; `interfaces.adoc` |
  | Pegel | „All UARTs operate at 3.3 V; connecting to 5 V systems causes damage.“ → Spannungsteiler-Regel (§2 Regel 4) | `interfaces.adoc` |
  | GND | Pin 6 (weitere GND-Pins des Headers möglich) | Standard-Header, nicht aus obigen Quellen → am Base-Board bestätigen |

- **Verweis:** PI-ANSCHLUSS §8.3 („Pi-Seite (zu prüfen)“); install-README §4b.

**0.4 Messung B2: Versorgung und Ruhepegel von U6/U7 (Original läuft)** · Gefahr: lesen (Messgerät **unter
Spannung**, Display-Einheit offen)
- **Ziel:** Entscheiden, ob die RX-Leitungen zum Pi einen Spannungsteiler brauchen.
- **Voraussetzung:** Stock-System läuft (T113 steckt noch am Flachkabel), Bridge-Board zugänglich.
- **Handgriff:** schwarz an den Testpunkt „GND“ neben J14. Rot nacheinander an **U6 Pin 16** und **U7 Pin 16**
  (VCC), dann je Wandler Pin 11, 12, 10, 9 im Ruhezustand. Pin 15 (GND) liegt direkt neben Pin 16 – nicht abrutschen;
  Pin 2/6 (Ladungspumpe) und die RS232-Pins 14/7/13/8 nicht berühren.
- **Erwartet:** Pin 16 ≈ 3,3 V oder ≈ 5 V (beides möglich, je Wandler notieren). Der RxOUT-Pin des belegten Kanals
  (12 oder 9 – welcher, klärt 0.6) liegt in Ruhe ungefähr auf dem Pin-16-Wert (UART-Ruhepegel High).
- **Folge:** Pin 16 ≈ 5 V → für diesen Wandler 1 kΩ in Reihe **plus** 2 kΩ nach GND in der RX-Leitung (0.11);
  Pin 16 ≈ 3,3 V → nur 1 kΩ in Reihe.
- **Abbruch → tun:** Pin 16 weder ≈ 3,3 V noch ≈ 5 V, oder ein TTL-Pin (9–12) über dem Pin-16-Wert/negativ → STOP,
  nichts anschließen, klären.
- **Verweis:** PI-ANSCHLUSS §8.3 B2.

**0.5 Breakout an das Original-Flachkabel stecken (Rest von B1)** · Gefahr: Handgriff, stromlos
- **Ziel:** T113 abgesteckt, Original-Flachkabel endet im Breakout; J14-Seite bleibt unberührt.
- **Voraussetzung:** 0.1, 0.4; Drucker **aus**, Netzstecker gezogen; Pi nicht verbunden.
- **Handgriff:** Lage und Kontaktseite des Flachkabels an J14 und am T113 fotografieren/notieren (B1). Am T113-Board
  den Klappbügel des Steckers öffnen, Kabel herausziehen (Halteohren nicht abschneiden, nicht knicken). Das Kabel
  bleibt in J14. Freies Ende mit **derselben Kontaktseite wie am T113** ins Breakout schieben, Haltenase in die
  Halteohren rasten lassen, Bügel schließen. Stiftleisten nur am Breakout einlöten (falls nicht vorbestückt), nie am
  Bridge-Board. Das T113-Board bleibt sonst unberührt (sein Flachkabel zum Display bleibt dran).
- **Erwartet:** Kabel sitzt gerade, rastet, nichts verkantet. Breakout liegt isoliert (keine Stifte auf Metall).
- **Abbruch → tun:** Kabel rastet nicht, Kontaktseite unklar → nicht mit Gewalt; zurück in den Originalzustand
  (Anhang C) und klären. **Stiftnummern nicht abzählen** (Kabel kann die Zählung spiegeln) – die Zuordnung liefert
  erst 0.6.
- **Verweis:** PI-ANSCHLUSS §8.3 B1, §8.5.

**0.6 Messung B3: Durchgang U6/U7 → Breakout-Stifte** · Gefahr: lesen (Messgerät, stromlos)
- **Ziel:** Die vier Daten-Stifte des Breakouts eindeutig finden: Main-TxIN, Main-RxOUT (U6), Düse-TxIN, Düse-RxOUT
  (U7).
- **Voraussetzung:** 0.5; Drucker aus, Netzstecker gezogen.
- **Handgriff (Durchgang/Ω):**
  1. Welcher Wandler-Kanal ist belegt: U6 Pin 14 bzw. 7 (T1OUT/T2OUT) → Testpad **TX7**, Pin 13 bzw. 8 (R1IN/R2IN) →
     Testpad **RX7**; U7 Pin 14/7 → **TX1**, Pin 13/8 → **RX1** (Testpads/Aufdruck Rückseite, PI-ANSCHLUSS §8.4).
     Kanal 1 = 11/12/14/13, Kanal 2 = 10/9/7/8.
  2. TTL-Seite des belegten Kanals: U6 Pin 11 bzw. 10 (TxIN) und 12 bzw. 9 (RxOUT) → jeden Breakout-Stift;
     ebenso U7.
  3. Der zweite Kanal von U6 (TX9/RX9, am Mainboard unbelegt) und von U7: Treffer nur notieren, nicht verwenden.
- **Erwartet:** je Wandler genau ein Kanal zu TX7/RX7 bzw. TX1/RX1; dessen TxIN und RxOUT mit ≈ 0 Ω (oder einem festen
  Serienwiderstand – Wert notieren) zu je einem Breakout-Stift. Ergebnis als Tabelle im Protokoll (Stift-Nr. → Signal).
- **Abbruch → tun:** TxIN/RxOUT erreichen keinen Breakout-Stift (Signal endet auf dem Bridge-Board oder geht über
  aktive Bauteile) → STOP, Anschluss-Variante B nicht weiter; Anhang E (Variante A) prüfen.
- **Verweis:** PI-ANSCHLUSS §8.3 B3, §8.4.

**0.7 Messung B4: übrige J14-Stifte** · Gefahr: lesen (Messgerät, stromlos)
- **Ziel:** wissen, was das T113 sonst über J14 liefert oder steuert, und welche Stifte tabu sind.
- **Handgriff (Durchgang/Ω, jeder noch nicht zugeordnete Breakout-Stift):** gegen Testpunkt „GND“ (→ GND-Stifte),
  Testpunkt „5V“ (→ 5V-Stifte), J10-Lötpunkte **485_A/485_B** (Rückseite, PI-ANSCHLUSS §8.4), USB des GL852G
  (Upstream D+/D−, Reset) und CAM-T (VBUS0/DP/DM), Gate des `NCEP40P80K` über Q28. Zusätzlich (eigene Ergänzung):
  Testpads **SYNC** und **SD**.
- **Erwartet:** Belegungsliste J14 im Protokoll: GND-Stifte, 5V-Stifte, ggf. 485_A/485_B, USB-Paare, ggf. Steuersignale.
- **Folge:** 5V-Stifte sichtbar markieren und frei lassen (§2 Regel 4). Liegen 485_A/485_B auf J14 → Kandidat für den
  RS485-Anschluss (0.9). Führt ein Stift zu Q28/Gate, SD oder einem Reset → Steuersignal des T113 → Pegel in 0.8
  prüfen, Ersatz durch den Pi **(noch festzulegen, F13)**.
- **Abbruch → tun:** mehrdeutige Treffer → klären, bevor 0.8 unter Spannung misst.
- **Verweis:** PI-ANSCHLUSS §8.3 B4, §8.4 (Nachtrag CAM-T/Hub).

**0.8 Breakout unter Spannung nur messen (Pi noch nicht verbunden)** · Gefahr: lesen (Messgerät **unter Spannung**)
- **Ziel:** Vor dem ersten Pi-Kontakt sicherstellen, dass an den vier Daten-Stiften nur TTL-Pegel liegen und der
  Toolhead ohne T113 versorgt ist.
- **Voraussetzung:** 0.5–0.7; an den Breakout-Stiften hängt nichts.
- **Handgriff:** Drucker an; schwarz an einen GND-Stift (aus 0.7). Messen: die vier Daten-Stifte, die 5V-Stifte, das
  Testpad **24V** am Toolhead-Stecker, ggf. Steuersignal-Stifte aus 0.7. Danach Drucker aus.
- **Erwartet:** RxOUT-Stifte (Main/Düse) ≈ Pin-16-Wert aus 0.4 (Ruhe High); TxIN-Stifte: Wert notieren (ohne T113
  nicht getrieben); **kein** Daten-Stift negativ oder über dem Pin-16-Wert; 5V-Stifte ≈ 5 V; 24V-Testpad ≈ 24 V
  auch ohne T113.
- **Abbruch → tun:** negative Spannung oder > 5,5 V an einem Daten-Stift (RS232-Seite erwischt) → STOP, 0.6
  wiederholen. 24 V fehlt ohne T113 (Toolhead-Versorgung vom T113 geschaltet?) → STOP, F13.
- **Verweis:** PI-ANSCHLUSS §8.2 (offen: ob das T113 die Toolhead-24 V schaltet), §8.3 B2/B4.

**0.9 RS485-/CFS-Anschluss bestimmen und messen** · Gefahr: lesen
- **Ziel:** Anschlusspunkt, A/B-Polarität und 24-V-Pin sicher kennen, bevor der USB-RS485-Adapter angeschlossen wird.
- **Handgriff:** Kandidaten: Breakout-Stifte 485_A/485_B (falls 0.7 sie auf J14 findet; der RS485-Treiber sitzt
  vermutlich auf dem T113-Board, A/B laufen über J14 durch – ANNAHME, PI-ANSCHLUSS §8.4), „485“-Stecker am Mainboard
  (HY2.0-5P), CFS-6-Pol-Stecker, J22 Pin 7 (A) / Pin 1 (B) (bei Anschluss-Variante B steckt dort das J10-Kabel – nur
  über einen Zwischenadapter erreichbar). Messen: 24 V gegen GND, A/B in Ruhe; Steckertyp am Gerät bestimmen.
  Genauer Anschlusspunkt: **(noch festzulegen nach 0.7–0.9, F4)**.
- **Erwartet:** Fremdmessung (anderes Gerät, gleiche CFS): Pin 1 RS485-A ≈ 1,75 V, Pin 2/3 Buffer, Pin 4 +24 V,
  Pin 5 GND, Pin 6 RS485-B ≈ 1,74 V; Regel „Ruhe: A hoch, B tief“. Referenz J22 vom 17.10. (Kabel zur
  Display-Einheit an J22 abgezogen): Pin 7 (A) 3,2 V, Pin 1 (B) 0,23 V. Alle Leitungen ≈ 0,01 V = Transceiver evtl.
  durch ESD/Hot-Plug beschädigt.
- **Abbruch → tun:** 24 V auf einem Signalpin oder widersprüchliche Messung → nicht anschließen, klären.
- **Verweis:** PI-ANSCHLUSS §7, §8.4.

**0.10 Pi-Versorgung prüfen** · Gefahr: lesen
- **Ziel:** Pi sicher und getrennt vom Bridge-Board versorgen.
- **Handgriff:** Testphase: eigenes **USB-C-Netzteil 5 V / 5 A** an eigener Steckdose. Später
  ggf. Buck aus dem 24-V-Druckernetzteil: Ausgang **vor** dem Anschluss messen. Nie aus den 5V-Stiften des Breakouts,
  aus J22 Pin 12 oder vom Bridge-Board versorgen.
- **Erwartet:** Pi bootet stabil, keine Unterspannungswarnung (`dmesg`). Buck später: 5,0–5,2 V (ANNAHME).
- **Abbruch → tun:** Unterspannung bzw. Buck-Spannung außerhalb → Pi nicht anschließen.
- **Verweis:** §2 Regel 2 (N1) und Regel 4 (Rückspeisung).

**0.11 Pi ↔ Breakout verdrahten** · Gefahr: Handgriff, stromlos (Drucker **und** Pi aus)
- **Ziel:** UART2 ↔ U6 (Main-MCU), UART4 ↔ U7 (Düsen-MCU), gemeinsame Masse.
- **Voraussetzung:** 0.3 (Gerätenamen, Loopback), 0.4 (Pin-16-Werte), 0.6–0.8 grün.
- **Handgriff (Steckbrücken Buchse-Buchse; Widerstände fest verbunden und isoliert, keine losen Beine – Aufbau F14):**
  1. **GND zuerst:** GND-Stift (0.7) ↔ Pi Pin 6.
  2. **Main TX:** Pi Pin 7 (GPIO 4, TXD2) → Stift Main-TxIN (U6).
  3. **Main RX:** Stift Main-RxOUT (U6) → **1 kΩ** → Pi Pin 29 (GPIO 5, RXD2). Nur wenn U6 Pin 16 ≈ 5 V (0.4):
     zusätzlich **2 kΩ** vom Pi-seitigen Ende des 1 kΩ nach GND (5 V × 2/3 ≈ 3,3 V).
  4. **Düse TX:** Pi Pin 32 (GPIO 12, TXD4) → Stift Düse-TxIN (U7).
  5. **Düse RX:** Stift Düse-RxOUT (U7) → **1 kΩ** → Pi Pin 33 (GPIO 13, RXD4); nur wenn U7 Pin 16 ≈ 5 V zusätzlich
     **2 kΩ** nach GND.
  6. Sonst nichts: 5V-Stifte und alle übrigen Stifte frei. Empfehlung (offen, §2 Regel 4): auch in Schritt 2 und 4
     je 1 kΩ in Reihe.
  Der USB-RS485-Adapter bleibt in Phase 1 **abgezogen**. Kamera (optional, nicht Teil dieses Fahrplans): Kabel von
  CAM-T abziehen und über ein Adapterkabel 4-pol. → USB-A direkt an einen Pi-USB-Port (PI-ANSCHLUSS §8.4 Nachtrag).
- **Prüfen (stromlos, vor dem Einschalten):** Pi Pin 29 ↔ Main-RxOUT-Stift ≈ 1 kΩ, Pi Pin 33 ↔ Düse-RxOUT-Stift
  ≈ 1 kΩ; Pi Pin 6 ↔ GND-Stift ≈ 0 Ω; **kein** Durchgang von einem Pi-Pin zu einem 5V-Stift.
- **Erwartet:** feste, isolierte Verbindung. Einschalten: erst Drucker, dann Pi (§2 Regel 4).
- **Abbruch → tun:** wackelige Kontakte, falscher Widerstandswert → erst sichere Verbindung herstellen.
- **Verweis:** PI-ANSCHLUSS §8.3.

---

## Phase 1 – Verbinden und lesen (keine Bewegung, RS485-Adapter noch nicht gesteckt)

**1.1 Main-MCU erkennen (UART2 → U6 → J10/J22; zugleich Autostart-Test des Main-Bootloaders)** · Gefahr: lesen
- **Ziel:** Main-MCU antwortet an `<uart-main>` mit Klipper-`identify`.
- **Voraussetzung:** Phase 0 grün (insbesondere 0.3, 0.8, 0.11); Drucker frisch eingeschaltet (Kaltstart, kein T113,
  das die MCU starten könnte), danach Pi gestartet.
- **Befehl (Pi):** `sudo systemctl stop klipper`, dann
  `~/klippy-env/bin/python ./k2-identify.py <uart-main>`.
- **Erwartet:** `MCU` = `gd32f303xe` → „Main-MCU“, `SERIAL_BAUD` 230400, `CLOCK_FREQ` 120000000, 75 Befehle /
  32 Antworten, Version `1.1.0.48-293-g493f9a0f`, Exit 0. Antwort nach Kaltstart heißt zugleich: Der Main-Bootloader
  springt ohne Stock-Board selbst in die App.
- **Abbruch → tun:** keine Antwort → Pi herunterfahren, Drucker aus; Verdrahtung gegen das B3-Ergebnis (0.6) prüfen
  (Pi-TX → TxIN, RxOUT → 1 kΩ → Pi-RX). **Nicht** blind TX/RX tauschen: vertauscht treibt der Pi-TX gegen den
  RxOUT-Ausgang des Wandlers (begrenzt nur, wenn ein Widerstand in diesem Weg liegt – Empfehlung 1 kΩ auch in TX,
  §2 Regel 4). Loopback (0.3) und Pegel am RxOUT-Stift (0.8) wiederholen; Baud
  (install-README §4b). Weiter Stille → MCU steht vermutlich im Bootloader. Werkzeug dafür: `k2-bootstart.py`
  (install-README §4c, `BOOTSTART-WERKZEUG.md`, Anhang A F1).
  **Gerätetest offen – nur mit ausdrücklicher Freigabe.** Reihenfolge:
  1. `~/klippy-env/bin/python ./k2-bootstart.py --dry-run --main <uart-main>` (öffnet nichts).
  2. `… --nur-erkennen --main …` (identify, `75`, `00 FF`; erwartet Version `mcu0_…`). Danach sofort Schritt 3 oder
     Drucker aus/an.
  3. `… --main …` (Start `02 FD`, danach identify).

  Exit 1 in Schritt 2 heißt „im Bootloader“ und ist dort erwartet.
  Ohne Freigabe, bei Exit 2/3/4, bei Exit ≠ 0 in Schritt 3 oder bei unerwarteter Version: STOP, nichts improvisieren,
  Rückweg zum Stock (Anhang C). Das Stock-Board startet die MCU selbst.
- **Verweis:** PI-ANSCHLUSS §8.4 (Main = Kanal „7“).

**1.2 Düsen-MCU erkennen (UART4 → U7 → Toolhead-Kabel)** · Gefahr: lesen
> Ersetzt den früheren Schritt „Düsen-MCU-Leitung finden (Paar 6↔7, USB-TTL an J22)“ – seit 19.10.2026 überholt:
> J22 Pin 6 = SYNC, Pin 7 = RS485-A; die Düsen-MCU hängt per RS232 über U7 am Toolhead-Kabel (PI-ANSCHLUSS §8.4).
- **Ziel:** Düsen-MCU (GD32F303CBT6) an `<uart-duese>` per `identify` finden.
- **Voraussetzung:** 1.1 grün; 0.8: 24 V am Toolhead auch ohne T113.
- **Befehl (Pi, Klipper gestoppt):** `~/klippy-env/bin/python ./k2-identify.py <uart-duese>` (oder beide Ports in einem
  Lauf: `~/klippy-env/bin/python ./k2-identify.py <uart-main> <uart-duese>`).
- **Erwartet:** `MCU` = `gd32f303xb` → „Düsen-MCU“, 230400, Version `1.1.0.48-293-g493f9a0f`.
- **Abbruch → tun:** keine Antwort → wie 1.1 (Verdrahtung U7, Pegel, Loopback). Ist die Leitung sicher richtig, kann
  die Düsen-MCU im Bootloader stehen: dann wie 1.1 mit `k2-bootstart.py … --duese <uart-duese>` (erwartet Version
  `noz0_…`). **Gerätetest offen – nur mit ausdrücklicher Freigabe.** Ohne Düsen-MCU startet Klipper mit dieser Config
  nicht → STOP, Vorgehen F3.
- **Verweis:** PI-ANSCHLUSS §8.4 (Düse = Kanal „1“, U7, Toolhead-Kabel).

**1.3 Serial-Pfade eintragen und Klipper starten** · Gefahr: lesen (MCU-Konfiguration, keine Ausgänge aktiv)
- **Ziel:** Klipper verbindet sich mit beiden MCUs.
- **Voraussetzung:** 1.1 und 1.2 grün.
- **Befehl:** `./install.sh --printer-cfg-ersetzen --main-serial <uart-main> --duese-serial <uart-duese>` (Pfade aus 0.3;
  **nicht** `auto` – das sucht nur unter `/dev/serial/by-id`, §2 Regel 12; `--stock-printer-cfg …` erst, wenn
  vorhanden – 0.1). Dann `sudo systemctl start klipper` und sofort `FIRMWARE_RESTART`.
- **Erwartet:** install.sh meldet für beide Pfade den Hinweis „… ist kein /dev/serial/by-id/-Pfad …“ – bei Pi-UARTs
  **gewollt** (Plausibilitätshinweis von `k2cfg.py`, kein Abbruch; der `ttyAMA`-Name hängt am DT-Alias, nicht an
  der USB-Reihenfolge). install.sh Schritt 6 grün; `Klipper ready`; im Log `Loaded MCU 'mcu' … 1.1.0.48-293-g493f9a0f`
  und dasselbe für `nozzle_mcu`; kein „format mismatch“. Erwartete Meldungen wegen des fehlenden RS485-Adapters:
  `[k2_485] Port … nicht zu öffnen (…)` – kein Absturz, kein Shutdown. `[k2_motor]` macht keinen Init
  (`auto_init: False`).
- **Abbruch → tun:** kein Connect, falsche MCU-Version, Config-Fehler → UART/Baud prüfen, STOP.
- **Verweis:** cfg-README §4 Schritt 1, §6.2; install-README §3/§4.

**1.4 Grundprüfung nach „ready“** · Gefahr: lesen
- **Befehle:** Temperaturen ansehen; `QUERY_ENDSTOPS`; `QUERY_PROBE`; `QUERY_FILAMENT_SENSOR SENSOR=filament_sensor`;
  `KTWO_BUS_STATUS`; `G28`.
- **Erwartet:** Düse, Bett, Kammer ≈ Raumtemperatur; `QUERY_ENDSTOPS` x/y/z „open“; `QUERY_PROBE` „open“;
  `KTWO_BUS_STATUS` `offen=False` mit Fehlertext (Adapter fehlt); `G28` bricht ab mit
  „G28 gesperrt: _PI_FREIGABE allow_move=False (README §4). Keine Bewegung.“
- **Abbruch → tun:** unplausible Temperaturen, Endstop „TRIGGERED“ ohne Grund, `G28` bewegt etwas → N1/N2, STOP.
- **Verweis:** cfg-README §4 Schritt 1; homing.cfg.

**1.5 Lüfter kurz** · Gefahr: lesen (nur Lüfterausgang)
- **Befehle:** `M106 P0 S128`, dann `M107`.
- **Erwartet:** Bauteillüfter hörbar, danach aus.
- **Verweis:** cfg-README §4 Schritt 1.

**1.6 Dual-Z-Lichtschranken lesen (A10 T1)** · Gefahr: lesen
- **Befehl:** `ZALIGN_STATUS`; dann Schranke links mit Papier/Karte unterbrechen (falls zugänglich – ANNAHME),
  dann rechts.
- **Erwartet:** beide `RELEASED` (Bett nicht ganz unten); beim Unterbrechen Meldung `zalign_z … AUSGELÖST` bzw.
  `zalign_z1`; Zuordnung links/rechts notieren (stepper_z = links ist ANNAHME). Bett sichtbar ganz unten → mindestens
  eine `PRESSED`.
- **Abbruch → tun:** dauernd `PRESSED` bei freier Gabel (Polarität/Pull-up, Pegel PA15/PA8 messen), keine Reaktion
  (Kabel), Zuordnung vertauscht (`pin_z`/`pin_z1` tauschen) → STOP, nicht weiter mit Phase 11.
- **Verweis:** zalign.cfg (Variante L, `ZALIGN_STATUS`); Phase 11.

**1.7 Beschleunigungssensor im Stand (lis2dw-Erstlauf 1–4)** · Gefahr: lesen
- **Befehle:** nach `FIRMWARE_RESTART`: `ACCELEROMETER_DEBUG_READ REG=0x0f`; `KTWO_ACCEL_STATUS`;
  `ACCELEROMETER_QUERY` (3×, Motoren still); `ACCELEROMETER_MEASURE`, 2 s warten, `ACCELEROMETER_MEASURE NAME=stand`;
  `MEASURE_AXES_NOISE`.
- **Erwartet:** Chip-ID **0x44**; Log `k2_lis2dw 'k2_lis2dw' dev_id: 44`; `KTWO_ACCEL_STATUS` → `messung=False`;
  Drucker-z ≈ ±9800, x/y |·| ≲ 1000 (`axes_map x,z,y`); CSV (Ablageort: README-k2_lis2dw §7) mit **≈ 625 Samples/s** (Korrektur 22.10.2026: die Düsen-Firmware liest nur 1 Sample je Timer, 1600 sind nicht erreichbar; Stock ≈ 400, bisheriger Host ≈ 385 – README-k2_lis2dw §9),
  Zeitstempel monoton ohne Lücken; Rauschwerte notieren (numpy ist nur für `MEASURE_AXES_NOISE` nötig).
- **Abbruch → tun:** anderer Chip-ID-Wert, „Timeout on wait for 'lis2dw_status'“, „No accelerometer measurements
  found“, konstante Nullwerte, „Invalid lis2dw id“ → STOP (Verdrahtung/Pins/SPI-Modus bzw. Firmware/OID).
- **Verweis:** README-k2_lis2dw §7 Schritte 1–4.

**1.8 Sicherheitsmodul lesen** · Gefahr: lesen
- **Befehl:** `KTWO_SAFETY_STATUS`.
- **Erwartet:** fakeshutdown-Überwachung für `mcu` und `nozzle_mcu` aktiv (Antwort im Dictionary), ausgelöst: nein,
  Grenzen Düse **350** / Bett **120** / Kammer **60**, `over_limit: clamp`, keine fehlenden Heizer.
- **Abbruch → tun:** „kein fakeshutdown im Dict“ oder fehlende Heizer → STOP vor jedem Heizen (Phase 6).
- **Verweis:** README-k2_safety §1–§3.

---

## Phase 2 – RS485-Bus lesend: Sind die Geräte in der App?

**2.1 USB-RS485-Adapter anschließen, Bus öffnen** · Gefahr: lesen
- **Ziel:** ein gemeinsamer Bus für Motoren X/Y/Z/Z1, CFS und Belts.
- **Voraussetzung:** Phase 1 grün; Anschlusspunkt und Polarität aus 0.9.
- **Handgriff/Befehl:** Drucker **aus** (Pi heruntergefahren, §2 Regel 4). GND zuerst, dann A/B (laut 0.9; am
  Breakout nur die in 0.7 gefundenen 485_A/485_B- und GND-Stifte, nie 5V-Stifte), kein 120 Ω, 24 V nie über den
  Adapter.
  USB an den Pi. `./install.sh --rs485-serial /dev/serial/by-id/<rs485-adapter>` (übernimmt die bisherigen
  serial-Pfade) oder `[k2_485] serial:` in k2-module.cfg eintragen. Drucker an, Klipper starten, `FIRMWARE_RESTART`,
  `KTWO_BUS_STATUS`.
- **Erwartet:** `offen=True`; Zähler ohne Timeouts/CRC-Fehler.
- **Abbruch → tun:** `[k2_485] Port … nicht zu öffnen` → Pfad/Adapter. Port offen, aber jede Abfrage stumm →
  A/B vertauscht (kein Schaden, Bus bleibt nur stumm): Drucker aus, A/B tauschen, wiederholen.
- **Verweis:** cfg-README §6.2 (ein RS485-Port, eine gemeinsame Queue).

**2.2 CFS lesend** · Gefahr: lesen
- **Voraussetzung:** 2.1; seit dem Einschalten mindestens 30 s vergangen (CFS-Bootloader-Fenster, danach startet die
  CFS-App selbst).
- **Befehl:** `KTWO_BOX_STATUS`.
- **Erwartet:** frischer 0x0a-Poll, Bericht der Form `Zustand=STATE_IDLE Hub=0x00 Temp=… Slots=…`; Temperatur/Feuchte
  plausibel (live z. B. 21 °C / 52 %). Antwortet die Box noch nicht, kann das an der fehlenden Adressvergabe liegen
  (die läuft erst in 3.6) → Ergebnis notieren, **kein** Abbruchgrund.
- **Abbruch → tun:** Hub-Maske ≠ 0x00, obwohl kein Filament im Hub steckt → mechanischer Rest im Hub (Kanal-A-Fall,
  blockiert auch die anderen Slots) → vor jedem Laden beheben.
- **Verweis:** README-k2_box §3.

**2.3 Motoren lesend** · Gefahr: lesen
- **Befehl:** `KTWO_MOTOR_STATUS`.
- **Erwartet:** je Motor Version (0x0F), Schutz (0C [0b] dekodiert), Feedback idx 5/6 – X/Y/Z/Z1 über `k2_485`, E über
  transparent. Vor dem Init nach einem Kaltstart ist offen, ob die Motoren schon an 0x81–0x84 antworten (der Stock
  findet sie beim Boot im Bootloader an 0x85–0x88) → Antwort oder Timeout je Motor notieren.
- **Abbruch → tun:** Shutdown/Traceback → STOP. Timeouts allein sind hier kein Abbruch (Bewertung in 2.5/3.3).
- **Verweis:** pi-host/README §2, §8.

**2.4 Belts lesend** · Gefahr: lesen
- **Befehle:** `KTWO_BELT_INFO MDL_NAME=mdlx`, `KTWO_BELT_INFO MDL_NAME=mdly`, `KTWO_BELT_TEST MDL_NAME=mdlx`.
- **Erwartet:** Version `0102`/`0304`, idl/full plausibel (nicht `0xFFFFFFFF`, signed angezeigt), Kraft ≈ 130–140 N
  (live 12.10.); `KTWO_BELT_TEST` → `target_error 0`. Belt-App-Adressen 0x21/0x22 sind per Lötbrücke fest.
- **Abbruch → tun:** key712/713 (Kommunikation) → Belt steht vermutlich im Bootloader (dort 0x23/0x24) → Bewertung
  2.5; Phase 10 entfällt bis geklärt. Belts sind für den Druck nicht nötig.
- **Verweis:** README-k2_belt §1, §6 Schritt 2.

**2.5 Bewertung: Bootloader-Autostart aller Geräteklassen** · Gefahr: lesen
- **Ziel:** entscheiden, ob Phase 3 möglich ist.
- **Handgriff:** Ergebnisse 1.1 (Main), 1.2 (Düse), 2.2 (CFS), 2.3 (Motoren), 2.4 (Belts) zusammenfassen.
  - **Direkte Bootloader-Abfrage** der Motoren/Belts/CFS (Klipper gestoppt):
    `~/klippy-env/bin/python ./k2-bootstart.py --nur-erkennen --rs485 /dev/serial/by-id/<rs485-adapter>`.
    Es gehen nur A1 an 0xFD/0xFC/0xFE raus, gemeldet wird das mode-Byte je Gerät (1 = Bootloader). Kein Start, kein A0/F0.
  - **Start:** `… ./k2-bootstart.py --rs485 …`, zuerst z. B. mit `--ohne-cfs`. A0 + F0 `[00]`/`[03]`/`[02]` gehen nur an
    Geräte mit mode 1; Geräte in der App werden nicht angefasst.
  - Werkzeug, Whitelist, Reihenfolge: install-README §4c, `BOOTSTART-WERKZEUG.md` §7 (Anhang A F2).
    **Gerätetest offen – nur mit ausdrücklicher Freigabe.**
  - Erwartung (V, BOOTSTART-WERKZEUG §6): Belts nach dem Einschalten im Loader, Motoren offen.
  - Die CFS holt der Pi sonst später per `0x0B [01]` an 0xFF aus dem Loader (k2_box/auto_addr, Schritt 3.6). Das
    RFID-Gerät 0x11 nutzt der Pi nicht (`[filament_rack]` entfällt).
- **Erwartet:** MCUs antworten (1.1/1.2). Für die Motoren ist der endgültige Nachweis 3.3 (Adressrunden).
- **Abbruch → tun:** MCU stumm, oder in 3.3 antworten die Motoren in keiner Runde (key798) → STOP. Keine
  Start-/Bootloader-Frames außer über `k2-bootstart.py` mit ausdrücklicher Freigabe (Start-Lauf mit Exit ≠ 0 → STOP;
  `--nur-erkennen` meldet Exit 1, sobald etwas im Loader steht), sonst Rückweg zum Stock.
- **Verweis:** install-README §4c; BOOTSTART-WERKZEUG.md.
- **Ergebnis 22.10.2026 (Pi, Variante B):** `--nur-erkennen` mit 9 Frames, 0 CRC-Fehler, Exit 0. Motoren an 0xFD in
  3 Runden ohne Antwort, Belts an 0xFC ohne Antwort (laufen in der App, 2.4), CFS an 0xFE mode 0 = App ohne Adresse.
  **Nichts steht im Loader → kein Start-Lauf nötig.** Dass die Motoren auch an 0x81–0x84 schweigen (2.3), ist
  erwartet: Ihre Adresse entsteht erst beim Strapping-Broadcast `FF 0E 01`, den bisher das
  T113 bei jedem Start gesendet hat; der Dispatcher nimmt sonst nur die eigene Adresse oder 0xFF an.
  `[output_pin power]` PC9 ist die Bett-Leistung, nicht die Motorversorgung. → Phase 3 möglich; der Nachweis für
  die Motoren ist 3.3.

**2.6 CFS-DB-Page beobachten (läuft über alle weiteren Phasen)** · Gefahr: lesen
- **Ziel:** Folgen davon erkennen, dass der Pi die CFS-DB-Page nie löscht (Stock sendet bei jedem Boot `F0 [06]`).
- **Handgriff:** Kein Befehl. Auffälligkeiten der Box (Preload-Verhalten, Fehlercodes, Restmengen) im Protokoll mit
  dem Vermerk „DB-Page“ notieren.
- **Erwartet:** offen.

---

## Phase 3 – Motor-Init

**3.1 Trockenlauf** · Gefahr: lesen
- **Befehl:** `KTWO_MOTOR_INIT DRY_RUN=1`.
- **Erwartet:** Plan P0–P9 mit **167 Schritten**; Strapping X (DIR 0, STEP 0) = **0x81**, Y **0x82**, Z **0x83**,
  Z1 **0x84**; E nur transparent an 0x81, nie 0x85; ID 226 wird nie gesendet.
- **Abbruch → tun:** Plan weicht von der Erwartung ab (Pins/IDs) → k2-motor.cfg korrigieren, STOP.
- **Verweis:** cfg-README §4 Schritt 2.
- **Ergebnis 22.10.2026:** grün – Strapping X 0x81, Y 0x82, Z 0x83, Z1 0x84 alle „ok“, P0–P9 wie erwartet
  (P8: 63 Lesen, 48 bedingte Writes; E nur transparent 0x81). Das Modul gibt keine Schrittsumme aus; Gegenzählung
  mit `nachbau/motor_init.py --dry-run` und der Stock-Config = 167 Schritte.

**3.2 Lesender Lauf** · Gefahr: lesen
- **Voraussetzung:** 3.1; `[k2_motor] allow_write: False`.
- **Befehl:** `KTWO_MOTOR_INIT` → nur Plan. Optional `KTWO_MOTOR_INIT ALLOW_WRITE=1` bei weiter `allow_write: False`
  in der Config → Diagnose-Modus.
- **Erwartet:** „ALLOW_WRITE=0 (allow_write=False): nur gelesen, nichts geschrieben.“ bzw. im Diagnose-Modus
  „nur lesende Schritte (kein Broadcast/Stall/Clear/Write/Update, kein GPIO-Setzen)“. Ein Ende wie key798 ist dort
  erwartbar (ohne Broadcast keine Adressen) und **kein** Modulfehler.
- **Abbruch → tun:** Shutdown mit anderer Ursache, Framing-/CRC-Fehler → Bus prüfen, STOP.
- **Verweis:** pi-host/README §4.
- **Ergebnis 22.10.2026:** grün – ohne ALLOW_WRITE nur Plan + „nichts geschrieben“; Diagnose-Modus
  (`ALLOW_WRITE=1`, Config `allow_write: False`): kein Broadcast, keine Pins gesetzt, 4 Adressrunden `0E [02]` an
  0x81–0x84 und E (transparent) ohne Antwort → key798, kein Shutdown, Klipper bleibt ready. Erwartet: ohne Broadcast
  keine Adressen.

**3.3 Schreibender Lauf** · Gefahr: **Bus-Schreiben** (Strapping-Pegel, Broadcast, Stall-Mode, Sys-Params flüchtig)
- **Voraussetzung:** 3.2 grün; Drucker frisch eingeschaltet (Kaltstart); `[k2_motor] allow_write: True`,
  `auto_init: False`; `FIRMWARE_RESTART`.
- **Befehl:** `KTWO_MOTOR_INIT`.
- **Erwartet (Ablauf und Stock-Werte aus 10 Live-Boots):** P1 Strapping-Pegel setzen und per `query_gpio_status`
  zurücklesen (16/16 wie geplant) → P2 Broadcast `FF 0E 01` (RS485 + transparent) → Adressrunden `0E 02` (≤ 4; am
  Kaltstart live 2: Runde 1 nur Z und E) → „All motors are normal“ → `11 [02]` an Z/Z1/E → `0C [0b]` ohne Fehlerbits →
  `0C [05]` → alle Pins physisch 0, Rücklesen → Param-Abgleich: am Kaltstart **11 Writes** (ID 70 300 → 100 an X, Z,
  Z1; ID 188 0,01 → 0,1 an X/Y/Z/Z1; ID 86 0,028 → 0,035 und ID 87 6 → 2 an Z/Z1), Echo passend → Event
  `k2_motor:ready`. Ein zweiter Lauf ohne Stromlos: 0 Writes. E-Param nur über transparent 0x81; `transparent_send`
  genau einmal.
- **Abbruch → tun:** key798 (Adressrunde unvollständig → Strapping/Pegel prüfen, Motoren evtl. im Bootloader, 2.5:
  Klipper stoppen, `k2-bootstart.py --nur-erkennen --rs485 …`, bei mode 1 Start mit `k2-bootstart.py --rs485 …`, dann
  Kaltstart-Bedingung beachten; **Gerätetest offen – nur mit ausdrücklicher Freigabe**),
  key799/800 (GPIO-Rücklesung → Pins), key801/802 (Stall-Mode Z/Z1 bzw. E), STATUS-Bits ≠ 0, `0C`-Fehlerbits
  (key781+n) → STOP, **kein Homing**. E stumm → einmal `FIRMWARE_RESTART`, sonst STOP.
- **Verweis:** cfg-README §4 Schritt 4.
- **Ergebnis 22.10.2026:** grün (Kaltstart Drucker, Pi am eigenen Netzteil). Runde 1: nur E antwortet (Stock-Kaltstart:
  Z und E), Runde 2: X 0x81, Y 0x82, Z 0x83, Z1 0x84, E 0x81 → „All motors are normal“. Stall-Mode `11 [02]` Z/Z1/E ok,
  `0C [0b]` alle 8 × 00, `0C [05]` unbeantwortet wie live. Param-Abgleich genau **11 Writes** wie vorhergesagt (ID 70
  300 → 100 an X/Z/Z1, ID 188 0,01 → 0,1 an X/Y/Z/Z1, ID 86 0,028 → 0,035 und ID 87 6 → 2 an Z/Z1), alle mit
  passendem Echo, `06 [03]` je Motor ok, E nur transparent. Event `k2_motor:ready`, Status ready (Fehler 0). Bus:
  98 gesendet, 85 empfangen, 13 Timeouts (erwartete Runde-1-/`0C [05]`-Lücken), 0 CRC-Fehler. `[k2_box]` meldet
  Auto-Addr „skipped“ (allow_write False, richtig bis 3.6). G28 bleibt über `_PI_FREIGABE allow_move: False` gesperrt.

**3.4 Ruhepegel der Stall-Leitungen (Mode 2) und Stall-Mode lesen** · Gefahr: lesen
- **Befehle:** `KTWO_MOTOR_STATUS`; `KTWO_MOTOR_STALL_MODE`.
- **Erwartet:** Zusammenfassung `stall_monitor`: Flanken 0, keine Aktion (Ruhepegel 0 in Mode 2; E darf vor dem Init
  high gewesen sein, bewertet wird erst nach `is_ready`); Stall-Mode x/y = 2.
- **Abbruch → tun:** Flanken ohne Ursache → Ausgangsart/Pegel der Leitung offen → STOP;
  Notausweg `stall_monitor: off`.
- **Verweis:** pi-host/README §11; cfg-README.
- **Ergebnis 22.10.2026:** grün mit Korrektur der Erwartung. Alle 5 Motoren FW 1.9.7.81 (cmd 1.3, protection 1.3,
  flash_param 2.75, logger 1.0), Schutz 8 × 00, pos_fdb/spd_fdb 0; Stall-Überwachung `report`, Flanken an allen 5
  Leitungen 0. **Stall-Mode x/y = 1, nicht 2:** Nach dem Kaltstart-Init ist das der Werkszustand der Motoren – der Init
  setzt wie Stock nur Z/Z1/E auf Mode 2 (X/Y erst beim Homing); Mode 2 an X/Y also erst nach
  `KTWO_MOTOR_HOMING PHASE=END` (4.1/4.2) erwarten. Kein Eingriff nötig.

**3.5 Automatischer Init beim Start** · Gefahr: Bus-Schreiben
- **Voraussetzung:** 3.3/3.4 grün.
- **Handgriff/Befehl:** `[k2_motor] auto_init: True`; Drucker aus und wieder an (Kaltstart); Klipper verbindet.
- **Erwartet:** Init läuft beim Connect von selbst mit demselben Bild wie 3.3 (`[k2_motor] …`-Meldungen, Event
  `k2_motor:ready`).
- **Abbruch → tun:** „Auto-Init abgebrochen“ → wie 3.3.
- **Verweis:** cfg-README §4 Schritt 4.
- **Ergebnis 22.10.2026:** grün – nach Kaltstart lief der Init beim Connect von selbst, Bild identisch mit 3.3 (Runde 1
  nur E, Runde 2 alle, 11 Writes mit Echo, `k2_motor:ready`). Nach `FIRMWARE_RESTART` ohne Stromlos: alle Motoren
  schon in Runde 1, **0 Writes** (wie erwartet). Beim Restart melden Z/Z1/E vor `is_ready` je eine steigende Flanke
  („ignoriert (nicht bereit)“), die nach dem Init wieder fällt; Codes leer, keine Aktion.

**3.6 CFS-Adressvergabe und Loader → App** · Gefahr: **Bus-Schreiben** (0xA1/0xA0/0xA2, ggf. `0x0B [01]` an 0xFF)
- **Voraussetzung:** 3.5 grün.
- **Handgriff/Befehl:** `[k2_box] allow_write: True` (`allow_move` bleibt `False`; **keine** `KTWO_BOX_LOAD/UNLOAD/
  PRELOAD`, kein `T<n>`), `FIRMWARE_RESTART`. Nach `k2_motor:ready` läuft `startup()` + `online_once()` von selbst.
  Danach `KTWO_BOX_STATUS`.
- **Erwartet:** Status `ready`, Event `k2_box:ready`; Bericht wie in 2.2. Beobachten und notieren, welche Geräteklassen
  (Motor/Belt/RFID/CFS) sich im Loader melden und ob `0x0B [01]` gesendet wird.
- **Abbruch → tun:** `addr_error`, Box-Status ERROR → STOP, kein Wechsel; Log sichern.
- **Danach:** `[k2_box] allow_write: False` bis 9.3 (siehe aber den Hinweis in 9.1).
- **Verweis:** README-k2_box §2 (`ensure_addr`).
- **Ergebnis 22.10.2026:** grün – `Auto-Addr` Status `ready`: Box 1 an Adresse 0x01 mapped/online/acked, lost 0,
  mode 0 (App); 0x02–0x04 leer (nur eine Box). Kein Gerät im Loader, `0x0B [01]` nicht nötig. `KTWO_BOX_STATUS`:
  STATE_IDLE, Hub 0x08, 22 °C, 47 %. Danach `allow_write: False` + `FIRMWARE_RESTART`: Auto-Addr „skipped“, die Box
  antwortet trotzdem an 0x01 (Adresse bleibt bis zum Ausschalten der Box). Nach jedem Kaltstart braucht sie also
  wieder eine Adressvergabe (`allow_write: True`).

---

## Phase 4 – X/Y-Homing, Stall, Fahrgrenzen X/Y, Schneidposition

**4.1 Stall-Polarität PB11/PB12 im Stand** · Gefahr: Bus-Schreiben (Stall-Mode, keine Fahrt)
- **Voraussetzung:** Phase 3 grün (`[k2_motor] allow_write: True`).
- **Befehle:** `KTWO_MOTOR_HOMING PHASE=BEGIN`; mehrmals `QUERY_ENDSTOPS`; Wagen per Hand gegen den Anschlag drücken
  bzw. leicht blockieren; erneut `QUERY_ENDSTOPS`; `KTWO_MOTOR_HOMING PHASE=END`.
  (Alternative: `KTWO_MOTOR_STALL_MODE DATA=1` … `KTWO_MOTOR_STALL_MODE DATA=2`.)
- **Erwartet:** in Ruhe „open“, beim Blockieren x bzw. y „TRIGGERED“. `PHASE=END` setzt Mode 2, `0C [0b]` ohne
  Schutzbits, Kp ID 70 zurück auf 100.
- **Abbruch → tun:** dauernd „TRIGGERED“ oder nie → Polarität falsch; Änderung der `endstop_pin`-Polarität
  **(noch festzulegen)**; STOP, kein G28.
- **Verweis:** pi-host/README §9; cfg-README §4 Schritt 6.
- **Teilergebnis 22.10.2026 (ohne Handgriff):** X/Y stehen nach dem Init ohnehin in Mode 1 (3.4); `QUERY_ENDSTOPS`
  3 × in Ruhe: `stepper_x:open stepper_y:open` → Ruhepegel/Polarität passt (kein `!` nötig). Blockier-Test offen.
  Hinweis: Alle Achsen teilen `enable_pin: !PA9`; bei abgeschalteten Motoren läuft kein Regler – ob ein Hand-Blockieren
  dann überhaupt einen Stall meldet, ist offen (FW@0xb842 wertet iq_ref bzw. Lagefehler aus). Aussagekräftig ist der
  Test mit bestromten Motoren (`SET_STEPPER_ENABLE`), das ist kein Fahrbefehl, aber die Motoren halten dann.
- **Ergebnis Blockier-Test 22.10.2026:** `PHASE=BEGIN` (x/y `0x11 [01]` Echo 01, Kp ID 70 = 300 Echo ok), Motoren per
  `SET_STEPPER_ENABLE` bestromt (in Mainsail eingegeben), Kopf von Hand je ≈ 3 s links/rechts/hinten/vorne
  gedrückt, Mitschrift `query_endstops` alle ≈ 0,3 s: **kein „TRIGGERED“**, Stall-Flanken x/y 0. `PHASE=END`: Mode 2
  (Echo 02), `0C [0b]` alle 8 × 00, Kp 100. Bewertung: Handkraft gegen einen haltenden Closed-loop-Motor erreicht die
  Stall-Schwelle nicht (Kriterium |iq_ref| > `stall_cur_A` bzw. Lagefehler > `stall_pos_err_rad`) –
  der Test ist so **nicht aussagekräftig** für „löst aus“. Belegt ist nur: Ruhe = open (keine verdrehte Polarität),
  Moduswechsel und Schutz ok. Den Auslöse-Nachweis liefert erst 4.2; dafür `homing_speed` X/Y für den Ersttest
  30 statt 100 mm/s (printer.cfg, vorher gesichert), danach zurück auf Stock 100.
- **Falle (22.10.2026):** Ein `query_endstops/status`-Abruf über Moonraker während `RESTART`/Verbindungsaufbau führt in
  Upstream-Klipper zu „Internal Error on WebRequest“ → **Shutdown** (`_query_cmd` noch None). Mitschreiber/Polls vor
  jedem Neustart beenden – ein per ssh im Hintergrund gestartetes Skript läuft auf dem Pi weiter, wenn nur die lokale
  ssh beendet wird (per PID beenden).

**4.2 Erstes X/Y-Homing** · Gefahr: **Bewegung**
- **Voraussetzung:** 4.1 grün; Hand am Netzschalter.
- **Befehle:** `SET_GCODE_VARIABLE MACRO=_PI_FREIGABE VARIABLE=allow_move VALUE=True`; `G28 Y`; dann `G28 X`; dann
  `G28 X Y`.
- **Erwartet:** Ablauf homing.cfg: M204 S500 → `KTWO_MOTOR_HOMING PHASE=BEGIN` → Y (M204 S100, `G28 Y`, `G1 Y10`) →
  X (`G28 X`, `G1 X-10`) → `FORCE_MOVE STEPPER=stepper_y DISTANCE=-3 VELOCITY=10` → `PHASE=END`. Position nach
  `G28 X Y`: **X 342,5 / Y 3,8** (Batch-Test).
- **Abbruch → tun:** Schrittverlust, Blockade, Schleifgeräusch → N1. „No trigger“ → das Modul setzt Mode 2/Kp 100
  selbst zurück; STOP.
- **Verweis:** homing.cfg; pi-host/README §9; cfg-README §4 Schritt 6.
- **Ergebnis 22.10.2026:** grün (Befehle in Mainsail eingegeben). Y fährt nach **vorne**, X nach **rechts**.
  Erster Versuch mit `homing_speed` 30: Anschlag sicher erkannt, Wiederholung 0,025 mm, aber **Rattern** am Anschlag und
  10–11 Stall-Flanken je Motor – bei langsamer Fahrt drückt der Motor zu lange, bis die Strom-Schwelle erreicht ist.
  Mit Stock **100 mm/s**: kein Rattern, 1–2 Flanken je Motor. → `homing_speed` **100 bleibt** (nicht senken).
  `G28 X` und `G28 X Y`: beide Endlage **X 342,5 / Y 3,7875** (Schrittrundung), Unterschied der Motorschritte zwischen
  beiden Läufen 13 bzw. 1 Schritt → Abweichung < 0,1 mm. Schutz nach jedem Homing 8 × 00, Mode 2, Kp 100, keine
  Stall-Meldung (Flanken nur gezählt, während `homing_active` gesperrt).
- **Vorfall 22.10.2026 abends – Fehlauslösung nach Neustart:** `systemctl restart klipper` (X/Y stromlos, Kopf am Schacht
  X133/Y378), dann `G28` → nach ≈ 1,5 s „Endstop stepper_y still triggered after retract“; beobachtet: Kopf „nach hinten
  geknallt, sofort gestoppt“. Deutung: Y löste beim Start sofort aus, der Rückzug (+10 mm = nach hinten) fuhr den Kopf
  vom Schacht gegen die Rückwand. Erstmals überhaupt (kein Treffer in den Logs seit 16.10.); 20 min vorher lief dasselbe
  aus derselben Lage fehlerfrei. Vermutete Ursache: Klipper bestromt die Motoren erst mit dem ersten Schritt, also nach
  dem Scharfschalten des Endstops – eine kurze Stall-Meldung des Closed-Loop-Motors beim Bestromen zählt dann als
  Anschlag. **Schutz** (homing.cfg `_PI_XY_BEREIT`, nach `_PI_VOR_HOMING`): X/Y vorher bestromen
  (`SET_STEPPER_ENABLE`), 0,5 s warten, `QUERY_ENDSTOPS` (Schlüssel live `stepper_x`/`stepper_y`, nicht `x`/`y` – erste Fassung prüfte die falschen Namen); meldet X oder Y dann noch Stall → Abbruch **ohne Bewegung**
  (Rückbau X/Y über `gcode:command_error` wie bei jedem Homing-Abbruch).

**4.3 Fahrgrenzen X/Y** · Gefahr: lesen
- **Befehl:** `KTWO_LIMITS_STATUS`.
- **Erwartet:** enges Fenster: X min **−7,8** (= `cut_pos_x`), Y max **352**, `wide` = False, keine Halter.
- **Abbruch → tun:** weite Werte im Normalzustand → STOP (k2_limits prüfen).
- **Verweis:** README-k2_limits §2/§3/§7.
- **Ergebnis 22.10.2026:** grün – `eng`, X −7,80 … 352,50, Y −6,20 … 352,00, weit X −10 / Y 400 nicht aktiv, Z max
  350,20, Z außerhalb Druck ≥ −2, keine Halter, 0 Ablehnungen.

**4.4 Stall-Überwachung im Modus „report“** · Gefahr: **Bewegung**
- **Befehle:** `G28` (X/Y) mehrmals, danach `KTWO_MOTOR_STATUS`. Ohne Druck mit aktiven Motoren eine Achse leicht
  blockieren bzw. einen Schleppfehler provozieren (genaues Verfahren **noch festzulegen**), danach `KTWO_MOTOR_CLEAR`
  und `KTWO_MOTOR_STATUS`.
- **Erwartet:** beim Homing keine Stall-Meldung von X/Y (Sperre greift). Beim Provozieren: Flanke → `0C [0b]` →
  Meldung `key789` im Stock-Format → Leitung wieder frei. Nach `KTWO_MOTOR_CLEAR` keine Codes mehr.
- **Abbruch → tun:** Meldungen beim normalen Homing, Codes bleiben nach CLEAR → STOP, `stall_monitor` bleibt `report`.
- **Verweis:** pi-host/README §11; cfg-README.

**4.5 Schneidposition kalibrieren** · Gefahr: **Bewegung** + Bus-Schreiben (Stall-Mode 3, Kp flüchtig)
- **Voraussetzung:** 4.2 grün; **kein Filament im Kopf**. Steckt Filament im Kopf: 4.5 nach 9.3 (Entladen)
  verschieben, bis dahin gilt der Stock-Wert −7,80.
- **Befehle:** `KTWO_MOTOR_CUT_CAL DRY_RUN=1`; `KTWO_MOTOR_STALL_MODE` (liest 2); `CALIBRATE_CUT_POS`; dann
  `SAVE_CONFIG` (oder gleich `CALIBRATE_CUT_POS SAVE=1`).
- **Erwartet:** Plan mit 46 Anstößen −5,0 … −9,5; Ergebnis nahe dem Stock-Wert **−7,80** (± 0,1–0,2 mm plausibel),
  innerhalb `check_cut_pos_x_min/max` −9,5/−5,5; Stall-Überwachung zählt nur `cut_edges`. Danach zieht `[k2_limits]`
  X min auf den neuen `cut_pos_x` nach.
- **Abbruch → tun:** PB11 nach Mode 3 schon aktiv (Modul bricht selbst ab), keine zwei Stalls bis `x_min`, Ergebnis
  außerhalb des Bereichs → Rückbau läuft automatisch (Kopf weg vom Hebel, Mode 2, Kp 100); STOP.
- **Verweis:** pi-host/README §10; cutcal.cfg; cfg-README.

---

## Phase 5 – PrTouch, Z-Homing, z_tilt, Bett-Mesh

**5.1 PrTouch ohne Bewegung** · Gefahr: lesen (Düse kurz „scharf“, keine Bewegung)
- **Voraussetzung:** Phase 4 grün; `[k2_prtouch] allow_move: False`.
- **Befehle:** `KTWO_PRTOUCH_TEST_SWAP`; `KTWO_PRTOUCH_READ`; `QUERY_PROBE`; `KTWO_PRTOUCH_STATUS`; `PROBE`;
  `KTWO_PRTOUCH_PROBE_STEPS DRY_RUN=1`.
- **Erwartet:** `READ ok (Vorabprüfung ohne Bewegung)` mit `tick_median_ms` ≈ 0,79 (Vorabtest am Stock 0,791;
  Toleranz 0,78 ms ± 10 %) und `filt_max_abs` deutlich unter 2000 (Vorabtest 364–497). Damit ist auch der
  Signalweg PA15 → PC7 über die Main-MCU geprüft (SYNC: Toolhead → Bridge-Board → J10/J22 Pin 6, bei
  Anschluss-Variante B unverändert original). `QUERY_PROBE` „open“. `PROBE` bricht mit „allow_move“-Fehler ohne
  Bewegung ab. `PROBE_STEPS DRY_RUN=1` liefert Plan und Ruhewerte, Z bewegt sich nicht.
- **Abbruch → tun:** Signalweg-/Ruhetest-Fehler, Kontakt in Ruhe → STOP; Werte notieren.
- **Verweis:** README-k2_prtouch A.6 Schritte 1–2, §5 Punkt 3.
- **Ergebnis 22.10.2026:** grün mit einem Ausreißer. `TEST_SWAP` pres/step je `swap_ok` (PRES/STEP-Ver 71), `QUERY_PROBE`
  open, `PROBE_STEPS DRY_RUN=1` Plan ok. `KTWO_PRTOUCH_READ` 1. Versuch **fehlgeschlagen** („nur 1 gültige Rohwerte –
  Sampling nicht gestartet/Puffer leer?“, keine Bewegung), danach **8 × ok**, auch direkt nach `FIRMWARE_RESTART` bzw.
  `RESTART` und nach `TEST_SWAP`: tick_median 0,7908–0,7910 ms (Vorabtest 0,791), filt_max_abs 196–410 (< 2000), Roh-
  Spannweite 605–1037 bei Nullpunkt ≈ −1,5177 M. Ausreißer nicht reproduziert (beim Fehlversuch waren X/Y bestromt,
  sonst nicht – offen, beobachten; der Ruhetest bricht dann ohne Bewegung ab). `PROBE` mit `allow_move: False` noch
  zu prüfen (Bewegungsbefehl).

**5.2 Erste Antastung** · Gefahr: **Bewegung** (Z)
- **Voraussetzung:** 5.1 grün; Bett leer, Düse von Hand sauber und kalt; Hand am Netzschalter.
- **Handgriff/Befehle:** `[k2_prtouch] allow_move: True` (optional `diag_readout: True`), `FIRMWARE_RESTART`
  (Motor-Init läuft automatisch), Freigabe setzen, `G28 X Y`, Kopf über Bettmitte (175/175 wie homing.cfg).
  Düse auf ≈ 3 mm über dem Bett bringen – Verfahren in den Quellen nicht beschrieben **(noch festzulegen)**.
  `SET_KINEMATIC_POSITION Z=10` (nur für diesen Test), `PROBE`, `KTWO_PRTOUCH_STATUS`.
- **Erwartet:** Fahrt nach unten mit ≤ 5 mm/s, Stopp beim Kontakt über Endstop PC7/trsync, Ergebnis-Z; keine
  Meldung „Düse meldet bereits Kontakt VOR der Fahrt“; Weg höchstens `probe_max_travel_mm` (8). Mit
  `diag_readout`: eingefrorener Ring in `KTWO_PRTOUCH_STATUS` → Anstieg (Counts/Sample bei 5 mm/s) notieren.
- **Abbruch → tun:** kein Stopp bei sichtbarem Kontakt → sofort N1/N2. Fehler „max_travel“, Ruhetest, Entschärfen →
  STOP, keine Wiederholung mit erzwungenem `allow_move`.
- **Verweis:** README-k2_prtouch A.1/A.6 Schritt 3.
- **Ergebnis 22.10.2026:** grün – **erste Antastung mit Bewegung am Pi.** Verfahren (jetzt festgelegt): Bett ganz unten
  (beide `zalign` PRESSED), `G28 X Y`, `G1 X175 Y175`, `SET_KINEMATIC_POSITION Z=350` (bewusst < 352,2 → echter
  Abstand ≥ Anzeige), `G1 Z20 F600`, `G1 Z3 F300`, Abstand mit Münzen geprüft (2-Euro-Münze ≈ 2,2 mm), bei Bedarf
  `SET_KINEMATIC_POSITION Z=10` + `G1 Z5` näher; bis eine Münze mit Luft passt, zwei nicht. Dann
  `SET_KINEMATIC_POSITION Z=10`, `PROBE`: Kontakt bei z = 5,95 (also 4,05 mm Weg), Bett stoppte **sofort
  bei Berührung**. Vorab: Signalweg (True, False), ack pres/step err 0, Ruhetest dif 863 / filt 214 / Tick 0,7907 ms;
  danach `tri_chxs` 1, Ring eingefroren (ftr/src/step je 64). Beobachtung: Der echte Weg von ganz unten war ≈ 6 mm
  länger als 352,2 (Bett steht unter dem Schaltpunkt der Lichtschranke). Nach `PROBE` bleibt die Düse am Bett (Upstream
  hebt nach einem einzelnen `PROBE` nicht an) → danach `G1 Z10 F300`.

**5.3 Streuung** · Gefahr: **Bewegung**
- **Befehl:** `PROBE_ACCURACY SAMPLES=5`.
- **Erwartet:** kleine Streuung (README-k2_prtouch §5 Punkt 6 nennt als Richtwert < 0,02).
- **Abbruch → tun:** große Streuung → Düse reinigen, STOP; `diag_readout` danach wieder `False`.
- **Verweis:** README-k2_prtouch A.6 Schritt 4.
- **Ergebnis 22.10.2026:** grün – `PROBE_ACCURACY SAMPLES=5` in Bettmitte (175/175), kalt: 5,945 / 5,950 / 5,9475 /
  5,950 / 5,9475 → **Spannweite 0,005 mm, Standardabweichung 0,0019 mm** (Richtwert < 0,02). Einzel-`PROBE` davor 5,950.
  `diag_readout` bleibt bis nach 5.4 an (wird dann mit dem nächsten Neustart entfernt).

**5.4 Erstes vollständiges G28** · Gefahr: **Bewegung**
- **Befehl:** `G28`.
- **Erwartet:** X/Y wie 4.2, dann Z in Bettmitte mit 5 mm/s. `_PI_NOZZLE_CLEAN` meldet nur (ohne `[k2_box]
  allow_move` keine Reinigungsfahrt), das Antasten läuft trotzdem. Danach `_PI_MESH_LADEN`.
- **Abbruch → tun:** „kein Kontakt“ oder zu tiefes Z zuerst bei der **ersten** Z-Fahrt nach Neustart suchen;
  `bst_zoft` > 0,06 mm = weicher Kontakt → Düse reinigen. Kein Stopp → N1/N2.
- **Verweis:** homing.cfg.
- **Ergebnis 22.10.2026:** grün – `G28` (vorher `G1 Z20`, Z war per `SET_KINEMATIC_POSITION` gesetzt → Reinigung
  übersprungen): X/Y wie 4.2, Bettmitte, Z angetastet (2 Antastungen, alle Ruhetests ok, dif 653 / filt 325), Endlage
  175/175/Z 5, `homed_axes` xyz, Mesh-Profil `default` geladen, Schutz 8 × 00, Z max 350,2. Ohne Auffälligkeiten.

**5.5 Kollisions-Vorprüfung Dual-Z vor dem ersten Z_TILT_ADJUST** · Gefahr: **Bewegung**
- **Befehle:** `G1 Z15 F600`, `G1 X5 Y175 F3000` → Abstand Düse–Bett messen (d_links, Fühlerlehre/Lineal),
  `G1 X345 Y175` → d_rechts.
- **Erwartet:** |d_links − d_rechts| ≤ 4 mm.
- **Abbruch → tun:** > 4 mm → STOP, kein `Z_TILT_ADJUST`; erst Phase 11 (zalign-Variante B) oder Rücksprache.
- **Verweis:** 11.1 (T3).
- **Ergebnis 22.10.2026:** **ausgelassen** (bewusst). Ränder im geschlossenen Gehäuse von Hand nicht
  messbar; Ersatz per `PROBE` links/rechts als überflüssig verworfen. Risiko gering: Antastung Mitte sauber,
  Stock-Korrektur zuletzt 0,06 mm, `horizontal_move_z` 5. Ergebnis von 5.6: Spannweite links/rechts 0,000 mm.

**5.6 z_tilt und Mesh (kalt)** · Gefahr: **Bewegung**
- **Befehle:** `G1 Z5`, `Z_TILT_ADJUST`, `G28 Z`, `BED_MESH_CALIBRATE PROFILE=default`, `SAVE_CONFIG`.
- **Erwartet:** z_tilt konvergiert (`retry_tolerance` 0,1, höchstens 10 Runden); Mesh plausibel gegen das
  Referenzprofil `stock_referenz_2026-10-16` (−0,067 … 0,226).
- **Abbruch → tun:** „Retries aborting … increasing“ (Zuordnung/Richtung falsch), „Too many retries“,
  Korrekturrunde > 3 mm, „kein Kontakt innerhalb probe_max_travel“ → STOP.
- **Verweis:** cfg-README §4 Schritt 7; Abbruchliste wie 11.1 (T3).
- **Ergebnis 22.10.2026:** grün (kalt). `Z_TILT_ADJUST`: Retries 0/10, Spannweite links/rechts **0,000** (Korrektur
  z und z1 je +0,100 = nur Gesamtversatz). `G28 Z`, `BED_MESH_CALIBRATE PROFILE=default` (9 × 9, ≈ 4,5 min): −0,030 …
  0,355 (Spannweite 0,385, Schüssel: Ränder hoch, Mitte tief) – gleiche Form wie `stock_referenz_2026-10-16` (−0,067 …
  0,226); Differenz nach Abzug des Mittelwerts (0,072) −0,066 … +0,097, Std 0,042 (Ränder ≈ 0,05–0,1 höher). Plausibel
  (kalt gemessen; Stock addiert je Punkt `zoft`, der Hauptweg nicht). `SAVE_CONFIG` (Backup
  der printer.cfg mit dem alten `default`), `diag_readout` dabei wieder entfernt.

**5.7 Z-Grenzen** · Gefahr: lesen / Bewegung
- **Befehle:** `KTWO_LIMITS_STATUS`; außerhalb eines Drucks `G1 Z-3` (aus Z ≥ 10, mit niedrigem Vorschub,
  Hand am Netzschalter).
- **Erwartet:** Z max **350,2**; `G1 Z-3` wird **ohne Bewegung abgelehnt** (Grenze außerhalb des Drucks −2).
- **Abbruch → tun:** Z fährt nach unten → sofort N1/N2, STOP.
- **Verweis:** README-k2_limits §2/§7.
- **Ergebnis 22.10.2026:** `KTWO_LIMITS_STATUS` Z max 350,20, Z außerhalb Druck ≥ −2 aktiv. `G1 Z-3`-Probe nicht
  gemacht (Sperren nicht per Fahrbefehl testen; nach `SAVE_CONFIG` ohnehin nicht referenziert) – offen, optional.

**5.8 Optional: Rückfallweg Schrittfahrt** · Gefahr: **Bewegung**
- **Wann:** nur falls der Hauptweg (`mode: probe`) Probleme macht.
- **Befehle:** zuerst `KTWO_PRTOUCH_PROBE_STEPS DRY_RUN=1` (5.1), dann über leerem Bett mit kleinem
  `probe_max_travel_mm` (für den allerersten Versuch 1,0) `KTWO_PRTOUCH_PROBE_STEPS`.
- **Erwartet:** Status `ok`; `mcu_triggered`, `best_indx`, `zoft` notieren; im Log kein `emergency`/`mcu_contact`.
- **Abbruch → tun:** `emergency`, `max_travel`, `timeout`, `mcu_contact` → STOP, Ursache (README-k2_prtouch §4).
- **Verweis:** README-k2_prtouch §5 Punkt 4, B.1.

---

## Phase 6 – Heizen und Sicherheitsabschaltungen

**6.1 fakeshutdown-Test mit dem Kammer-Thermistor** · Gefahr: lesen (löst bewusst einen Shutdown aus)
- **Voraussetzung:** Gerät kalt, alle Heizziele 0.
- **Handgriff:** Thermistorstecker der Kammer (PC5, Main-MCU) abziehen.
- **Erwartet:** im Log `fakeshutdown` mit „mcu0 ADC out of range“; k2_safety setzt alle Heizer auf 0 und löst den
  Shutdown aus: „k2_safety: MCU 'mcu' meldet fakeshutdown "mcu0 ADC out of range" … – alle Heizungen aus, Shutdown.
  Fühler/Stecker prüfen, dann FIRMWARE_RESTART.“
- **Danach:** Drucker aus, Stecker zurück, Drucker an, `FIRMWARE_RESTART`, `KTWO_SAFETY_STATUS`.
- **Abbruch → tun:** kein Shutdown → nicht heizen, `fakeshutdown_guard`/Dictionary prüfen, STOP.
- **Verweis:** README-k2_safety §1 („Noch offen (Gerätetest)“).
- **Stand 22.10.2026:** noch offen – Lage des Kammer-Thermistor-Steckers (PC5) am Gerät unbekannt; vor 6.3 nachholen.

**6.2 Kappung der Solltemperatur** · Gefahr: **Heizen** (kurz)
- **Befehle:** `M140 S130`, sofort danach `M140 S0`.
- **Erwartet:** Meldung „k2_safety: Solltemperatur 130.0 für 'heater_bed' auf 120.0 gekappt …“ (wie Batch-Lauf L).
- **Abbruch → tun:** keine Kappung → `M140 S0`, STOP, kein weiteres Heizen.
- **Verweis:** README-k2_safety §2/§4.
- **Ergebnis 22.10.2026:** grün – „k2_safety: Solltemperatur 130.0 für 'heater_bed' auf 120.0 gekappt“, sofort `M140 S0`
  (target 0, power 0, Bett 24 °C).

**6.3 Aufheizen und PID** · Gefahr: **Heizen**
- **Befehle:** `NOZZLE_PID` (PID_CALIBRATE Düse 230 °C, Bauteillüfter an); `BEDPID` (Bett 100 °C); `SAVE_CONFIG` von
  Hand (die Makros speichern nicht selbst); danach `TURN_OFF_HEATERS`.
- **Erwartet:** keine `verify_heater`-Fehler, stabile Temperaturen.
- **Abbruch → tun:** Temperatur läuft weg, `verify_heater` → `TURN_OFF_HEATERS` bzw. N1.
- **Verweis:** cfg-README §4 Schritt 8; macros.cfg (`BEDPID`, `NOZZLE_PID`).
- **Ergebnis 22.10.2026:** grün (6.1 noch offen; `verify_heater` aktiv). `NOZZLE_PID` 230 °C mit
  Bauteillüfter: ≈ 2 min, `pid_Kp=17.713 pid_Ki=1.874 pid_Kd=41.847` (Stock 14.296 / 1.271 / 40.206). `BEDPID` 100 °C:
  ≈ 10 min, `pid_Kp=67.892 pid_Ki=2.647 pid_Kd=435.358` (Stock 37 / 0.18 / 280 – Creality-Werte, Klipper-Autotune liegt
  für große Betten typisch so). Keine `verify_heater`-Fehler. `SAVE_CONFIG`: Werte im Autosave-Block, die Stock-Werte
  im Hauptteil hat Klipper auskommentiert; aktiv geprüft.
- **Nachtrag 22.10.2026 – Düsen-PID ohne Bauteillüfter:** Mit den 230-°C-Werten (Lüfter 100 %) pendelte die Düse
  ohne Lüfter 218,6↔221,5 °C, und `M109` kam nie an (Klipper `check_busy`: |Δ| ≤ 1 °C **und** ≤ 0,1 °C/s) → zurück auf
  Stock, dann `PID_CALIBRATE HEATER=extruder TARGET=220` mit allen Bauteil-/Seitenlüftern aus (≈ 2 min):
  **17.015 / 1.436 / 50.407** – fast gleich Stock `pid_*_high_temp` (16.443 / 1.370 / 49.329). Vergleich mit
  `HEATER_STABILITY_TEST` (120 s bei 220 °C, Lüfter aus; Umschalten ohne Neustart mit `SET_HEATER_PID` aus
  `k2_heater_pid.py`):

  | Werte Kp / Ki / Kd | größte Abweichung | Befund |
  |---|---|---|
  | 17.015 / 1.436 / 50.407 (PID_CALIBRATE ohne Lüfter) | 2,02 °C (218,68–222,02) | Dauerschwingung, Periode ≈ 9,8 s |
  | 14.296 / 1.271 / 40.206 (Stock) | 1,42 °C | unruhig |
  | **10 / 0.8 / 40** | **0,50 °C** | ruhig → im Autosave-Block des Pi |

  PID_CALIBRATE (Ziegler-Nichols) ist für dieses Hotend zu scharf; neue Messwerte immer mit `SET_HEATER_PID` +
  `HEATER_STABILITY_TEST` prüfen, bevor sie gespeichert werden.
- **Makros für die Mainsail-Leiste (22.10.2026, macros.cfg „Kalibrierung“):**

  | Makro | Was es tut |
  |---|---|
  | `NOZZLE_PID [TEMP=220] [FAN=0]` | Druck-Sperre; wenn referenziert, Kopf über den Abfallschacht; alle Bauteil-/Seitenlüfter aus (FAN > 0 = Lüfter wie Stock); `PID_CALIBRATE`; Heizung aus; Meldung |
  | `BEDPID [TEMP=100]` | Druck-Sperre; `PID_CALIBRATE` Bett (≈ 10 min); Bett aus; Meldung |
  | `NOZZLE_PID_HIGH [TEMP=300]` | wie `NOZZLE_PID` für den Hochsatz (Stock `NOZZLE_PID_HIGH`, dort 320 °C mit Lüfter); Werte mit `SET_HEATER_PID SET=HIGH` prüfen |
  | `ALLOW_MOTION` | setzt `_PI_FREIGABE allow_move` bis zum nächsten Neustart (nur nötig, wenn `homing.cfg` die Bewegung sperrt) |
  | `Z_AXIS_CALIBRATION [BED_TEMP=50] [AUS=1]` | `G28` + `Z_TILT_ADJUST` + `G28 Z`; Standard warm wie Stock (Bett heizen, Düse 140 °C, Silikon-Reinigung, danach Heizungen aus), kalt mit `BED_TEMP=0` (bis 25.10.2026 `Z_AXIS_CALIBRATION`) |
  | `TUNOFFINPUTSHAPER` | Stock-Name: Input Shaper aus bis zum Neustart |
  | `HEATER_STABILITY_TEST [TEMP=220] [ZEIT=120] [AUS=1]` | Kopf über den Schacht, `M109`, alle 5 s messen, Meldung Min/Max/größte Abweichung („ruhig“ bei ≤ 1 °C) |
  | `START_PRINT … ADAPTIVE=1` | Stock `BED_MESH_CALIBRATE_START_PRINT`: Mesh nur über den Objekten (Profil `adaptiv`, `default` bleibt) |

  Schutz: `NOZZLE_PID`, `NOZZLE_PID_HIGH` und `HEATER_STABILITY_TEST` heizen über 260 °C nur bei leerem Kopf oder mit
  `FILAMENT_OK=1` (PLA/PETG verkokt sonst in der Düse). Noch **nicht** in Mainsail (erst nach Test am Gerät):
  `BELT_TENSION` (10.4/10.5, Spannmotoren bewegen sich), `INPUTSHAPER`/`AUTOTUNE_SHAPERS` (LIS2DW-Rate ≈ 385 statt
  1600 Hz). Bewusst nie: Motor-Kalibrierung der Closed-Loop-Motoren. Unbekannt: Stock `PRINT_CALIBRATION`
  (`CX_PRINT_LEVELING_CALIBRATION`, geschlossenes Creality-Modul).

  Gespeichert wird nie automatisch: `SAVE_CONFIG` startet Klipper neu (Motoren stromlos, Bett sackt ab) → danach
  `ALLOW_MOTION` + `Z_AXIS_CALIBRATION`.

**6.4 Mesh warm** · Gefahr: **Heizen** + Bewegung
- **Befehl:** `G29` (Düse 140 °C, Bett 50 °C, `G28`, `Z_TILT_ADJUST`, Reinigung nur mit `[k2_box] allow_move`,
  `G28 Z`, `BED_MESH_CALIBRATE PROFILE=default`, Heizungen aus), dann `SAVE_CONFIG`.
- **Erwartet:** Meldung „G29 fertig – Profil 'default' mit SAVE_CONFIG speichern …“; Mesh plausibel wie 5.6.
- **Verweis:** macros.cfg (`G29`); cfg-README §4 Schritt 7.
- **Stand 22.10.2026:** 1. Versuch brach beim ersten `G28 Z` ohne Bewegung ab (Ruhetest, Düse heizte gerade). Ursachen
  und Fixes: `_PI_DUESE_STABIL` (homing.cfg, wartet vor `G28 Z` auf die Düsentemperatur), Stock-Lüfterdämpfung
  (`fan_damping`, README-k2_prtouch A.11), `[multi_pin heater_fans]` wie Stock (Düse PB7 + **Main** PB1),
  `rest_max_dif_probe`, `rest_retries`. Danach heiß (140 °C): `G28` ok, `PROBE_ACCURACY SAMPLES=10` **Spannweite
  0,005 mm, Std 0,0015 mm**, keine Wiederholung nötig. `G29` selbst noch zu wiederholen.
  Hinweis Konsole: Nach `M109` erst weitertippen, wenn es fertig ist – spätere Befehle warten sonst in der
  Warteschlange und laufen danach automatisch los.
- **Ergebnis G29 22.10.2026 17:07–17:14:** durchgelaufen (≈ 6 min). `_PI_DUESE_STABIL` wartete bei 134 °C,
  Lüfterdämpfung aktiv. `Z_TILT_ADJUST` Runde 1 Spannweite **0,78 mm** (z +0,486 / z1 −0,409), Runde 2 0,005 mm. Mesh
  warm 0,003 … 0,263 (Spannweite 0,26), glatt, keine Ausreißer; gegen kalt (5.6) flacher (Ränder bis 0,09 tiefer,
  Std 0,036), gegen `stock_referenz` Std 0,025 / ±0,05 → näher am Stock. 3 Ruhetest-Wiederholungen (gefiltert 3 828 /
  2 834 / 3 960), jede beim 2. Versuch ok. „Unknown command RESPOND“ (kein `[respond]` eingebunden) → Meldung jetzt
  über `_PI_MELDUNG`. **Beobachtung:** Das Bett sinkt offenbar nach unten, wenn Klipper neu startet (Motoren
  stromlos) – nach jedem Neustart melden beide `zalign` PRESSED, und die 0,78 mm Schiefstand passen zu einem
  ungleichen Absinken. Stock fängt das mit ZDOWN/Lichtschranken ab; bei uns gleicht `Z_TILT_ADJUST` es aus.

**6.5 Temperaturkompensation lesen** · Gefahr: lesen
- **Befehl:** `KTWO_NOZZLE_TEMPCOMP`.
- **Erwartet:** aktiv; Referenz = geglättete Düsentemperatur beim letzten `G28 Z`/Mesh (≈ 140 °C); wirkt nur im
  Druck mit geladenem Netz. Prüfung im Druck: 12.1.
- **Verweis:** README-k2_nozzle_tempcomp §1–§4.
- **Ergebnis 22.10.2026:** Modul an (a = 5,1e-06, c = 0,017316, max 0,181), inaktiv (kein Druck), Referenz „–“, weil seit
  dem `SAVE_CONFIG`-Neustart kein `G28 Z` lief – die Referenz setzt erst das nächste `G28 Z`. Wirkung erst im Druck (12.1).
  Warmes Mesh aus 6.4 mit `SAVE_CONFIG` gespeichert. **Bett sinkt bei stromlosen Motoren** (Neustart, `M84`, Leerlauf-
  Abschaltung) in < 20 s ganz nach unten, links/rechts ungleich (Sichtprüfung) → vor jedem Druck Z-Ausgleich
  nötig. `idle_timeout` steht wie Stock auf 99 999 999 s (Motoren werden nie automatisch stromlos – vermutlich genau
  deshalb), Heizungen gehen damit aber auch nie automatisch aus.

---

## Phase 7 – Kammerheizung und PTC-Lüfter

**7.1 Tacho** · Gefahr: **Heizen**
- **Befehle:** `M141 S45`, nach 3 s `KTWO_CHAMBER_STATUS`.
- **Erwartet:** PTC-Lüfter (!PB14/PB2) läuft **sofort**; rpm deutlich > 100.
- **Abbruch → tun:** Lüfter läuft nicht → **sofort** `M141 S0`. rpm 0 trotz drehendem Lüfter → `M141 S0`, dann
  `tachometer_pin: ^PC6` (Pull-up) testen; unplausible rpm → `tachometer_ppr` anpassen.
- **Verweis:** README-k2_chamber §6 Punkt 1; cfg-README §4 Schritt 8.
- **Ergebnis 22.10.2026:** grün – Lüfter sofort an (Soll 1,0), **rpm 3 270–3 720** (ohne Pull-up), PTC-PWM 0,50
  (Anlauf-50 % aktiv), kein Fehler; `M141 S0` → Leistung 0.

**7.2 Lüfterausfall → Abschaltung** · Gefahr: **Heizen**
- **Handgriff/Befehle:** Kammer kalt, Ziel 45: Lüfterstecker abziehen; warten; `M141 S45`; Stecker zurück;
  `KTWO_CHAMBER_RESET`.
- **Erwartet:** nach **≈ 15 s** Meldung „!! k2_chamber: PTC-Lüfter steht … (Stock key519) – gesperrt bis
  KTWO_CHAMBER_RESET“, Ziel 0, PWM 0; `M141 S45` wird mit Fehler abgewiesen; nach Reset wieder frei.
- **Abbruch → tun:** keine Abschaltung nach ≈ 15 s → `M141 S0`, N1, STOP.
- **Verweis:** README-k2_chamber §6 Punkt 2.

**7.3 Bett-Vorrang und 50-%-Anlauf** · Gefahr: **Heizen**
- **Befehle:** `M140 S100`, `M141 S50`, mehrfach `KTWO_CHAMBER_STATUS`; danach `TURN_OFF_HEATERS`.
- **Erwartet:** Bett-Vorrang = True, bis das Bett ≈ 98 °C hat; danach Anlauf-50 % = True für 60 s.
- **Abbruch → tun:** PTC heizt während Bett-Vorrang → `TURN_OFF_HEATERS`, STOP.
- **Verweis:** README-k2_chamber §6 Punkte 3–4.
- **Ergebnis 22.10.2026:** grün – `M140 S100` + `M141 S50`: Bett-Vorrang = True ab 6 s bis Bett 98,5 °C (≈ 350 s, PTC-PWM
  durchgehend 0), dann Anlauf-50 % (PWM 0,50) ≈ 60 s, danach PWM 1,0; kein Fehler; `TURN_OFF_HEATERS`.

**7.4 Optional: Kammer-Obergrenze** · Gefahr: **Heizen**
- **Befehle:** `M141 S70`, dann `M141 S0`.
- **Erwartet:** „M141: Kammer-Soll 70 auf 60 °C begrenzt (Stock chamber_temp)“.
- **Verweis:** macros.cfg (`M141`); README-k2_safety §2.
- **Ergebnis 22.10.2026:** grün – „M141: Kammer-Soll 70 auf 60 °C begrenzt (Stock chamber_temp)“, danach `M141 S0`.

---

## Phase 8 – Input-Shaper (optional)

**8.1 Resonanzmessung** · Gefahr: **Bewegung** (Vibrationen)
- **Voraussetzung:** 1.7, Phase 4 und 5 grün (vollständig gehomt).
- **Befehle:** `TEST_RESONANCES AXIS=X` (Kopf fährt zu 175,175,175), dann `SHAPER_CALIBRATE`; erst danach
  `SAVE_CONFIG`.
- **Erwartet:** Werte vergleichbar mit Stock mzv **42,0 / 42,4 Hz** (gegebenenfalls zusätzlich mit
  `sweeping_period: 0` wie die Stock-Methode).
- **Abbruch → tun:** ungewöhnliche Geräusche → N1/N2; abweichende Werte → nicht speichern.
- **Verweis:** README-k2_lis2dw §7 Schritt 5.

---

## Phase 9 – CFS außerhalb eines Drucks

**9.1 CFS lesend** · Gefahr: lesen
- **Voraussetzung:** Phasen 3–5 grün. Antwortet die Box nur nach einer Adressvergabe (Ergebnis 2.2/3.6), diesen
  Schritt mit `[k2_box] allow_write: True` durchführen, aber **keine** schreibenden `KTWO_BOX_*`-Befehle und kein
  `T<n>` absetzen.
- **Befehle:** `KTWO_BOX_STATUS`; `KTWO_BOX_WAIT`; `QUERY_FILAMENT_SENSOR SENSOR=filament_sensor`; `T1` (nur Plan);
  `KTWO_BOX_RFID` (nur `0x02`/`0x03`, kein 0x0d); `KTWO_BOX_SLOT_SET SLOT=T1A` (ohne Parameter nur Anzeige; mit
  `COLOR=`/`TYPE=` sendet es nichts); `KTWO_BOX_RUNOUT SLOT=A DRY_RUN=1` mit leerem Slot A;
  `KTWO_BOX_PRELOAD SLOT=A OP=2 DRY_RUN=1`.
- **Erwartet:** Zustand/Hub/Temp wie 2.2; `T1` nur Plan; RFID-Werte je Slot mit Material aus der DB, Restlänge 255 =
  „unbekannt“ bei Slots ohne RFID-Länge; Handwert gilt, bis ein **lesbares** Etikett gelesen wird (Verhalten bei
  unlesbarem Etikett = ANNAHME → beobachten); RUNOUT-Trockenlauf zeigt Ersatz und Begründung ohne Bewegung.
- **Abbruch → tun:** Box ERROR, Hub belegt ohne Filament → STOP.
- **Verweis:** README-k2_box §15; cfg-README.
- **Ergebnis 22.10.2026:** grün (lesend; die Box behielt die Adresse aus 3.6, daher `allow_write: False`). `KTWO_BOX_WAIT`
  bereit (STATE_IDLE); `filament_sensor` detected; `KTWO_BOX_SLOT_SET SLOT=T1A` nur Anzeige; `KTWO_BOX_RUNOUT … DRY_RUN=1`:
  kein Auto-Nachladen (`runout_refill: False`); `KTWO_BOX_PRELOAD … DRY_RUN=1`: `0x0d [01 02]`, nichts gesendet;
  `KTWO_BOX_RFID`: alle 4 Slots ohne Fehler, Etiketten „unbekannt“ (Box liest sie erst beim Preload). `T1` bewusst
  nicht gesendet (Fahrbefehl).

**9.2 Kopffahrten ohne Box-Frames** · Gefahr: **Bewegung**
- **Voraussetzung:** 9.1; `[k2_box] allow_move: True`, `allow_write: False`; Kopf leer; `G28`.
- **Befehle (einzeln):** `KTWO_BOX_CUT`; `KTWO_BOX_GO_EXTRUDE_POS`; `KTWO_BOX_WIPE`; `KTWO_BOX_SAFE_POS`; danach
  `_PI_NOZZLE_CLEAN`, dann `G28`. Hebelfall: `KTWO_BOX_CUT` außerhalb eines Drucks und danach manuell `G1 X10`.
  „Langsam“ abfahren verlangen die Quellen; das Verfahren dafür ist nicht festgelegt **(noch festzulegen)**.
- **Erwartet:** Schnitt trifft den Hebel bei X −7,80 / Y 200 und fährt zurück nach X 10; Schacht X 133 / Y 378,
  sichere Position X 225 / Y 345; keine Kollision mit Schacht/Silikon. Wegen `FORCE_MOVE stepper_y -3` liegt die Düse
  physisch 1,5/1,5 mm versetzt zur Koordinate (wie Stock).
- **Abbruch → tun:** Kopf trifft Schacht, Silikon oder Hebel falsch → N1, Positionen (Stock-Werte in k2-module.cfg)
  prüfen, STOP.
- **Verweis:** k2-module.cfg (Positionen); cfg-README.
- **Ergebnis 22.10.2026:** durchgelaufen (Filament D steckte im Kopf, alle 4 Fächer PLA): `G28` 93 s inkl. Reinigung
  über die Box (Schacht → Silikon → raus), `KTWO_BOX_GO_EXTRUDE_POS` → X133/Y378, `KTWO_BOX_WIPE`, `KTWO_BOX_SAFE_POS`
  → **X160/Y345** (Stock-T0-Ablauf `clean_right_pos_x`, nicht 225 wie oben erwartet), `_PI_NOZZLE_CLEAN`, `KTWO_BOX_CUT`
  X10 → X−7,8 → X10 bei Y200. Keine Fehlermeldung; Sichtprüfung siehe Protokoll.

**9.3 Box-Einzelschritte: Entladen und Laden** · Gefahr: **Bus-Schreiben** + Bewegung + Heizen
- **Voraussetzung:** 9.2 grün; `[k2_box] allow_write: True`; Düse auf 220 °C (`M109 S220`).
- **Befehle (einzeln):** `KTWO_BOX_RETRUDE` (nur wenn Filament im Kopf/Hub; Slot aus dem Hub-Sensor);
  `KTWO_BOX_EXTRUDE TNN=T1B` (Kopf leer, Hub frei); bei Bedarf `KTWO_BOX_WAIT`.
- **Alle Slots (19.10.2026):** Laden und Entladen **einmal je Slot A, B, C, D** (Masken 0x01/0x02/0x04/0x08).
  Live mit Stock belegt sind bisher nur A und B; C/D sind aus Firmware und Code abgeleitet – Ergebnis je Slot protokollieren.
- **Erwartet Laden (Stock live):** `0x04 [00 01]`, `0x08`, m0 (Antwort nach ≈ 4,4 s), m4, m5 alle 2 s bis zum
  Kopfsensor (höchstens 90 s), m6, Stage 7 Extruder `E30` (Stage-7-Extruderweg beobachten), nach 2 s m7 p=3, `0x05`,
  `0x04` PRINT.
- **Erwartet Entladen:** p0 ×4 mit je E−10 (F350 mit Material-DB, ohne DB F600), dann p1 (Antwort ≈ 16 s);
  ≈ 1,89 m Filament zurück in die Box.
- **Abbruch → tun:** Box-Status ≠ 0 (z. B. 0x0c Odometer-Stillstand → Stock-Retry einmal, danach Fehler), Kopfsensor
  meldet nicht innerhalb 90 s, Motorblock 0x19 → STOP, `KTWO_BOX_ERROR_CLEAR`, Hub/Pfad mechanisch prüfen.
  Kalte Düse → LOAD/UNLOAD werden verweigert (erst `M109 S220`).
- **Verweis:** README-k2_box §8.2, §13; cfg-README.
- **Ergebnis 22.10.2026:** grün (4. Lauf, Fach für Fach mit Sichtprüfung). Je Slot `KTWO_BOX_EXTRUDE TNN=T1x`, dann
  `KTWO_BOX_RETRUDE SLOT=x`, Düse 220 °C:

  | Slot | Laden | Hub nach Laden | Entladen | Hub danach |
  |---|---|---|---|---|
  | A | ok, Odometer ≈ −164 mm | 0x01 | ok | 0x00 |
  | B | ok, 25 s, Odometer −169,5 mm | 0x02 | ok, 28 s | 0x00 |
  | C | ok, 24 s, Odometer −150,1 mm | 0x04 | ok, 26 s | 0x00 |
  | D | ok, 29 s, Odometer −152,8 mm | 0x08 | – (bleibt geladen) | – |

  Kopfsensor nach jedem Laden True, nach jedem Entladen False; Box nach dem Laden `STATE_PRINT`, nach dem Entladen
  `STATE_IDLE`; keine Pause. Damit sind C und D jetzt auch live belegt (Masken wie aus der Firmware abgeleitet).
  Vorherige Läufe scheiterten an zwei Fehlern, die jetzt behoben sind:
  - Runout-PAUSE beim Einzel-Entladen. Fix: `KTWO_BOX_RETRUDE`/`KTWO_BOX_UNLOAD` sperren den Kopfsensor währenddessen.
  - `M109` hing wegen pendelnder Düsen-PID. Fix: alte PID-Werte zurück.
  - Lehre: Nach einem Not-Halt im `STATE_PRINT` schob die Box ein Filament selbst nach (0x0c Odometer-Stillstand);
    `KTWO_BOX_ERROR_CLEAR` löste es. Steckt fremdes Filament im Kopf, meldet `EXTRUDE` „bereits geladen“ (Hub + Kopfsensor).

**9.4 Spülbahn, DB-Temperaturen, Düsen-Material** · Gefahr: **Heizen** + Bewegung + Bus-Schreiben
- **Befehle:** `KTWO_BOX_FLUSH TNN=…` einzeln (PLA-Slot, dann ein PETG-Slot, falls vorhanden); `KTWO_BOX_REMAIN`;
  `FIRMWARE_RESTART`; erneut `KTWO_BOX_REMAIN`.
- **Erwartet:** zwischen den Abschnitten nur Blech, Silikon-Kreise nur am Ende (Versatz 0–3 mm), Lüfter je Abschnitt
  5 s; Menge `[90, 70]` bei unbekannter oder gleicher Farbe; PLA (max_vol 14) F350 / F420; Temperatur PLA → PLA
  Spitze 240 °C, dann 220 °C; PETG-Slot 250 °C. Nach erfolgreichem Spülen zeigt `KTWO_BOX_REMAIN` Farbe/Typ, und der
  Wert übersteht den Neustart (Datei aus `remain_material_file`).
- **Abbruch → tun:** Kreise zwischen den Abschnitten, Temperatur über der DB-Spitze, Kopf verlässt Schacht/Blech →
  N2, STOP.
- **Verweis:** README-k2_box §12/§12.4; cfg-README.
- **Ergebnis 22.10.2026:** teilweise grün. `KTWO_BOX_FLUSH TNN=T1D` (D im Kopf, außerhalb eines Drucks) → Hinweis
  „Slicer-Matrix nur im laufenden Druck … Farbweg“, Plan 160 mm (90 + 70; Farbe unbekannt → Stock-Fallback
  `Tn_extrude` 140 mm), F360, **220 °C** (Material unbekannt: alle Fächer ohne RFID, daher keine DB-Spitze 240 °C), ok
  nach 41 s, Kopf danach X160/Y345. `KTWO_BOX_REMAIN` → „Farbe unbekannt, Typ unbekannt, Slot T1D“ (Datei
  geschrieben). **Offen:** DB-Temperaturen (braucht bekanntes Material, z. B. `KTWO_BOX_SLOT_SET`), PETG-Slot,
  Neustart-Probe von `remain_material` (beim nächsten ohnehin fälligen Neustart, weil das Bett beim Neustart absackt).
- **Lehre 22.10.2026 – nach dem Spülen nie ohne Schnitt zurückziehen:** `KTWO_BOX_RETRUDE SLOT=D` direkt nach dem
  Spülen → p0 ×4 (E−10) liefen, p1 brach ab mit **Box-Status 0x19 „Pull-back: Motorblock > 10 s“** (Box `STATE_ERROR`,
  Hub 0x08, Kopfsensor Filament). Ursache: Das 40 s lang geschmolzene Ende bildete beim Rückzug eine Verdickung; Stock
  schneidet vor jedem Rückzug (Kette CUT → RETRUDE, `box_end` ebenso). Rettung (grün): `KTWO_BOX_ERROR_CLEAR` →
  `M83`, `G1 E40 F300` (Rückzug rückgängig, Ende schmilzt wieder) → `G1 E15 F300` → `KTWO_BOX_CUT` →
  `KTWO_BOX_RETRUDE SLOT=D` ok nach 32 s, Hub 0x00, Kopfsensor False. Der abgeschnittene Rest bleibt in der Düse und
  wird beim nächsten Einzug ausgedrückt (wie Stock).

**9.5 Preload und Preload-Sperren** · Gefahr: **Bus-Schreiben** (Box fördert)
- **Befehle:** mit Filament im Kopf `KTWO_BOX_PRELOAD SLOT=A OP=2` → muss gesperrt sein; mit leerem Kopf
  `KTWO_BOX_PRELOAD SLOT=A OP=2`. N5: Box zurücksetzen und prüfen, ob sie ohne `0x0d [0f 01]` selbst vorlädt –
  Verfahren für „Box stromlos/Reset“ **(noch festzulegen)**; RS485-Transceiver sind empfindlich gegen ESD/Hot-Plug.
- **Erwartet:** gesperrter Fall: Meldung, **nichts** gesendet; Maske 0 wird abgelehnt; im Druck nur mit `FORCE=1`.
  Freier Fall: Antwort nach **31–37 s**, Status 0 (live). op 3 war live nie zu sehen (nur bei Bedarf testen).
- **Abbruch → tun:** Frame trotz Sperre, Motorblock → STOP.
- **Verweis:** README-k2_box §12.3; cfg-README.
- **Ergebnis 22.10.2026 (Sperrfall):** grün – Filament D im Kopf (Kopfsensor True, Hub 0x08), `KTWO_BOX_PRELOAD SLOT=A
  OP=2` → „gesperrt: op 2 nur bei leerem Kopf-Filamentsensor … – nichts gesendet“ nach 0,8 s; Box-Status vorher und
  nachher unverändert (`STATE_PRINT`, Hub 0x08).
- **Ergebnis 22.10.2026 (freier Fall):** grün – Kopf und Hub leer, `KTWO_BOX_PRELOAD SLOT=A OP=2` → „ok“ nach **31,8 s**
  (live Stock 31–37 s), danach `STATE_IDLE`, Hub 0x00 (Preload schiebt nicht bis ins Sammelstück). N5 (Box-Reset) offen.

**9.6 Ganze Wechsel außerhalb eines Drucks** · Gefahr: alles
- **Voraussetzung:** 9.3–9.5 grün.
- **Befehle:** bei leerem Kopf und leerem Hub `T1`; danach `T0`, dann wieder `T1`.
- **Erwartet:** erstes Laden **ohne Schnitt und ohne Rückzug** (A1); jeder Wechsel: Schnitt → Rückzug → Einzug →
  Spülen; außerhalb eines Drucks spült `flush_mode: matrix` nach dem Farbweg (Hinweis). Richtwert Stock: ≈ 112,6 s
  je Wechsel im Druck.
- **Abbruch → tun:** außerhalb eines Drucks wird ein Fehler zum G-Code-Fehler mit Aufräumen → STOP, Log.
- **Verweis:** README-k2_box §16 (A1); cfg-README.
- **Ergebnis 22.10.2026:** grün (außerhalb eines Drucks, alle Fächer PLA ohne RFID, Düse 220 °C):

  | Befehl | Dauer | Ablauf laut Meldungen | Danach |
  |---|---|---|---|
  | `T1` (Kopf + Hub leer) | 64,7 s | „Kopf und Hub leer – nur laden“: CUT + RETRUDE übersprungen (A1), EXTRUDE T1B, WASTE übersprungen (kein `[load_ai]`), FLUSH 160 mm (90 + 70) F360 | Hub 0x02, Kopfsensor True |
  | `T0` | 102,0 s | CUT (X10 → X−7,8 → X10), RETRUDE B, EXTRUDE T1A, FLUSH 160 mm | Hub 0x01 |
  | `T1` | 92,0 s | CUT, RETRUDE A, EXTRUDE T1B, FLUSH 160 mm | Hub 0x02 |

  Spülen im Farbweg (Hinweis „Slicer-Matrix nur im laufenden Druck“), Menge immer Stock-Fallback 140 mm → `[90, 70]`,
  weil die Farben unbekannt sind. Kopf am Ende zurück an der Startposition (X133/Y378, `END_OPT RETURN=1`). Keine
  Pause, kein Box-Fehler. `KTWO_BOX_REMAIN` danach „Slot T1B“. Richtwert Stock im Druck ≈ 112,6 s je Wechsel.

**9.7 Optional: Sensor-Selbsttest der Düse** · Gefahr: **Heizen** + Bus-Schreiben (Extruder)
- **Voraussetzung:** Filament geladen; Düse ≥ 170 °C; `[k2_prtouch] pres_check: True`, `FIRMWARE_RESTART`.
- **Befehl:** `KTWO_PRTOUCH_CHECK EXTRUDE=<mm>` (höchstens `pres_check_extrude_max_mm` 5).
- **Erwartet:** „SUCCESS: Strain gauge dynamic detection passed.“; `dif` ≥ 15000 ist ANNAHME → Werte und
  `espd`-Vorzeichen notieren.
- **Danach:** `pres_check: False`.
- **Verweis:** README-k2_prtouch A.10.

**9.8 Externe Spule: Quelle, Laden, Entladen, Sperrprobe** · Gefahr: **Heizen** + Bewegung (kein Box-Frame)
- **Voraussetzung:** 9.2 und 9.4 grün; `[k2_box] allow_move: True` (`allow_write` egal – extern sendet nichts an die
  CFS); `_PI_FREIGABE allow_move`; kein Druck. CFS-Filament vorher entladen (Kopf leer), dann den **Schlauch von Hand**
  von der CFS auf die externe Spule umstecken.
- **Befehle (einzeln):** `SET_FILAMENT_SOURCE SOURCE=EXTERNAL` (bzw. `KTWO_BOX_SOURCE SOURCE=EXTERN`) → Meldung „Filamentquelle = EXTERN“;
  **Sperrprobe** `KTWO_BOX_LOAD SLOT=A` → muss mit „gesperrt: Quelle ist EXTERN … nichts gesendet“ abbrechen (im
  Bus-Log/`KTWO_BUS_STATUS`-Zähler **kein** Frame an 0x01); `LOAD_FILAMENT` **ohne** Filament am Kopf → Meldung „Filament
  bis zur LED am Druckkopf schieben“, keine Bewegung; Filament von Hand bis zur LED am Druckkopf schieben
  (`QUERY_FILAMENT_SENSOR SENSOR=filament_sensor` → detected), dann `LOAD_FILAMENT MATERIAL=<Name|Typ|ID> [TEMP=…]`
  (erstes Mal; ohne Material-DB ist `TEMP=` Pflicht); ein zweites `LOAD_FILAMENT` ohne Parameter (nimmt das gespeicherte
  Material, kein Homing); `UNLOAD_FILAMENT`, danach Filament von Hand herausziehen; `FIRMWARE_RESTART` →
  `KTWO_BOX_SOURCE` zeigt weiter EXTERN + Material; Schlauch zurück an die CFS, `SET_FILAMENT_SOURCE SOURCE=CFS`.
- **Erwartet Laden (Stock live):** G28 X Y nur ohne Referenz; Schacht X 133 / Y 378;
  Heizen auf die Materialtemperatur (PLA 220 °C); Spülen wie CFS-Wechsel, gleiche Farbe `[90, 70]` = 160 mm, 1.
  Abschnitt bei Max-Temp (PLA 240 °C) mit 18 mm @ F575 + 72 mm @ F690 (Hyper PLA max_vol 23), Rest 14 + 56 mm @ F575
  bei 220 °C; Blasen, Blech, Silikon-Kreise am Ende; X 160 / Y 345; Heizung aus; `KTWO_BOX_REMAIN` zeigt das
  externe Material. **Kein** Frame an die CFS (nur das lesende 0x0a-Polling).
- **Erwartet Entladen (Stock live):** Schacht, Heizen auf die Materialtemperatur (nur mit Filament am Kopf), Kopf
  verlässt den Schacht (X 160 → Y 345), Schnitt X 10 → X −7,80 → X 10, E−10 F360, X 160 / Y 345, Heizung aus.
- **Kurzer Druck mit externer Spule** (Quelle EXTERN, beide Freigaben): am Bus nur das lesende 0x0a und – wie Stock
  live – `0x0d [0f 00]` + `0x0f [01]` beim Start, `0x0d [0f 01]` + `0x0f [00]` am Ende;
  kein `0x04`, kein Einzug, kein Schnitt/Entladen am Druckende; ein zweites Werkzeug in der Datei → PAUSE.
- **Optional RFID:** `[k2_box] extern_rfid: True` + `[gcode_button extern_spule]` (printer.cfg) einkommentieren,
  `FIRMWARE_RESTART`; `QUERY_BUTTON BUTTON=extern_spule` mit/ohne Spule (Pegel `!PA5` = ANNAHME); Spule aufsetzen →
  genau ein Frame `0x11 [02]`, Meldung „Etikett gelesen – Material …, Farbe …“; `SET_EXTERNAL_MATERIAL` zeigt es.
- **Abbruch → tun:** Frame an 0x01 außer 0x0a, Kopf trifft Schacht/Hebel beim Wechsel Schacht → Schnitt, Düse heizt
  ohne Filament am Sensor beim Laden, Kopfsensor bleibt nach dem Vorgang gesperrt
  (`QUERY_FILAMENT_SENSOR`/Status `enabled`) → STOP, Log.
- **Verweis:** README-k2_box §18.

---

## Phase 10 – Belt (Riemenspannung)

**10.1 Lesen mit Ausgabe** · Gefahr: lesen
- **Handgriff/Befehle:** `[k2_belt mdlx]`/`[k2_belt mdly]` mit `allow_write: False`, `allow_move: False`,
  `verbose: True`; `FIRMWARE_RESTART`; `KTWO_BELT_INFO MDL_NAME=mdlx`, dann `mdly`; `KTWO_BELT_TEST MDL_NAME=…`.
- **Erwartet:** wie 2.4. Hinweis „Riemen locker“ bei < 50 N bzw. „Kraft < 0“ ist mit der signed-Auswertung normal
  möglich.
- **Abbruch → tun:** key714/715 (nicht kalibriert, Flash `0xFFFFFFFF`), key716/717 (Kraft unplausibel) → STOP.
- **Verweis:** README-k2_belt §1, §4a, §6 Schritte 1–2.
- **Ergebnis 22.10.2026:** grün – mdlx HW 0102 / SW 0304, Position 147, **136,8 N**; mdly Position 138, **136,6 N**;
  `KTWO_BELT_TEST` beide OK (Soll 119–161 N), `target_error` 0. Flash-Kennlinien plausibel (nicht 0xFFFFFFFF).

**10.2 Gemeinsamer Bus unter Last** · Gefahr: lesen
- **Befehle:** Belt-Abfragen wiederholen, während der CFS-Poll läuft; `KTWO_BUS_STATUS`.
- **Erwartet:** keine Timeouts/CRC-Fehler in der `k2_485`-Statistik, CFS-Poll läuft weiter.
- **Verweis:** README-k2_belt §6 Schritt 3.
- **Ergebnis 22.10.2026:** grün – Belt-Abfragen während des CFS-Polls: Timeouts unverändert (nur die 5 aus dem
  Motor-Init), 0 CRC-Fehler.

**10.3 Plan der Spannfahrt** · Gefahr: lesen
- **Befehl:** `KTWO_BELT_SET MDL_NAME=mdlx DRY_RUN=1`.
- **Erwartet:** Text „NICHTS gesendet …“, Ist-Kraft, Ziel 140 N ± 2 %, erster Schritt (fun 8) mit Frame,
  2 Rüttel-Zyklen (Motor-0x05 und FORCE_MOVE je ± 200 mm **ohne** Endschalterschutz).
- **Abbruch → tun:** Wege des `cycle_gcode` passen nicht zur Maschine → anpassen (Inhalt **noch festzulegen**),
  STOP.
- **Verweis:** README-k2_belt §1, §6 Schritt 4; cfg-README §6 Punkt 13.
- **Ergebnis 22.10.2026:** Plan ok („NICHTS gesendet“), Ist 136,8 N, Ziel 140 N ± 2 %, erster Schritt fun 8. Die
  Rüttel-Zyklen fahren per `FORCE_MOVE` ± 200 mm je Motor **ohne Endschalterschutz** – vor 10.4 Wege und Startposition
  festlegen. Spannen derzeit nicht nötig (beide Riemen im Prüfbereich).

**10.4 Eine Achse spannen (mdlx)** · Gefahr: **Bus-Schreiben + Bewegung** (Belt-Flash Position, Schieber, X/Y)
- **Voraussetzung:** 10.3 grün; X/Y gehomt; Riemen frei; Kopf in sicherer Position; Hand am Netzschalter.
- **Handgriff/Befehle:** nur `[k2_belt mdlx]` `allow_write: True` + `allow_move: True`, `FIRMWARE_RESTART`;
  `KTWO_BELT_SET MDL_NAME=mdlx`; danach `KTWO_BELT_INFO MDL_NAME=mdlx` und `KTWO_BELT_TEST MDL_NAME=mdlx`.
- **Erwartet:** Kraft im Bereich `ch_best_n` 140 N · (1 ± 0,15); `target_error 0`; Position im Flash.
- **Abbruch → tun:** key718/719 (Regelung > 200 Schritte), Geräusch/Blockade → N1, STOP, Freigaben zurück.
- **Danach:** `[k2_belt mdlx]` `allow_write`/`allow_move` zurück `False`.
- **Verweis:** README-k2_belt §6 Schritt 5.
- **Ergebnis 23.10.2026 (10.4 + 10.5):** grün. Freigabe beider Module, `FIRMWARE_RESTART`, `G28 X Y`, Startposition
  **X 225 / Y 345** (Stock-Safe-Pos; von dort bleiben die Rüttel-FORCE_MOVEs in X 125–325 / Y 145–345), je Achse
  `KTWO_BELT_INFO` → `KTWO_BELT_SET` (≈ 30 s, 2 Rüttel-Zyklen, Flash geschrieben) → `KTWO_BELT_TEST`:

  | Achse | vorher | Position | nach SET | Test danach | Bereich |
  |---|---|---|---|---|---|
  | mdlx | 136,85 N | 147 → 151 | 137,37 N | 136,99 N | OK (119–161) |
  | mdly | 134,63 N | 138 → 170 | 137,31 N | 135,33 N | OK |

  Kein Anschlagen, keine Fehlermeldung. Danach beide Module wieder `allow_write/allow_move: False`, `FIRMWARE_RESTART`.
  Hinweis: Das Beispiel-Makro `KTWO_BELT_TENSION` (README-k2_belt) fährt vor `M84` noch **keine** sichere Position an –
  vor einer Aktivierung `G1 X225 Y345` ergänzen.

**10.5 Zweite Achse (mdly)** · Gefahr: **Bus-Schreiben + Bewegung**
- **Wie 10.4** mit `MDL_NAME=mdly`; danach beide Achsen wieder gesperrt.
- **Verweis:** README-k2_belt §6 Schritte 5–6.

---

## Phase 11 – z_align / Dual-Z (A10)

T1 lief in 1.6, T2 (Motor-Init, X/Y-Homing, Probe) entspricht den Phasen 3–5. Werte ins Protokoll (Anhang D).

**11.1 T3 – Referenz ohne Lichtschranken (z_tilt), mindestens 3 Kaltstarts** · Gefahr: **Bewegung**
- **Befehle je Kaltstart:** Drucker ≥ 10 min aus, an, `ZALIGN_STATUS` (Bettlage notieren); Freigabe; `G28`;
  Kollisions-Vorprüfung wie 5.5; `G1 Z5`, `Z_TILT_ADJUST` (Messwerte der ersten Runde, Korrekturen, Runden notieren);
  `Z_TILT_ADJUST` erneut; 30 min stehen lassen (Motoren an), `G28 Z`, `Z_TILT_ADJUST` (Drift); `M84`, 5 min, Bett per
  Sicht prüfen, dann wiederholen.
- **Erwartet:** zweite Ausgleichsrunde range < 0,1 in Runde 0, Korrekturen ≈ 0.
- **Abbruch → tun (Abbruchliste T3):** kein Kontakt, „Retries aborting … increasing“, „Too many retries“,
  Korrektur > 3 mm, Geräusch → STOP.
- **Verweis:** zalign.cfg; cfg-README §4 Schritt 7.

**11.2 T4 – zalign-Variante B lesend** · Gefahr: lesen
- **Handgriff/Befehle:** `k2_zalign.py` hat install.sh bereits installiert (install-README §4 Schritt 4). In
  zalign.cfg Block L (zwei `[gcode_button …]`) auskommentieren, Block B einkommentieren, `allow_move: False` lassen,
  `FIRMWARE_RESTART`. Dann `QUERY_ENDSTOPS`, `KTWO_ZALIGN_STATUS`, `KTWO_ZALIGN DRY_RUN=1`, `KTWO_ZALIGN`.
- **Erwartet:** `zalign_z:open zalign_z1:open`; Status GESPERRT mit Gründen; Plan; `KTWO_ZALIGN` → „gesperrt …
  Keine Bewegung.“
- **Abbruch → tun:** `pin … used multiple times` (Block L noch aktiv), `TRIGGERED` bei freier Gabel → STOP.
- **Verweis:** zalign.cfg (Block B, `[k2_zalign]`).

**11.3 T5 – Erste Ausrichtung (Einzelfreigabe)** · Gefahr: **Bewegung**
- **Voraussetzung:** Motor-Init fertig, `G28`, `G1 Z30`, z_tilt aus T3 angewendet.
- **Handgriff/Befehle:** `[k2_zalign] allow_move: True`, `FIRMWARE_RESTART`, Freigabe setzen, `KTWO_ZALIGN`.
- **Erwartet:** beide Seiten fahren mit 10 mm/s **nach unten**, jede stoppt an ihrer Schranke, 5 mm hoch, langsam
  wieder runter; Phase-A-Differenz < 1 mm nach z_tilt (ANNAHME), Delta ≤ 0,025 mm.
- **Abbruch → tun:** sofort N1/N2 bei Bewegung **nach oben** in Phase A, wenn eine Seite nach dem Stopp der anderen
  sichtbar > 5 mm weiterfährt, bei Verkanten/Geräusch oder wenn die Fahne auf Anschlag läuft.
- **Verweis:** zalign.cfg (Block B, `[k2_zalign]`).

**11.4 T6 – Z-max messen** · Gefahr: **Bewegung**
- **Befehle:** nach T5: `GET_POSITION` (Wert A von `stepper_z`), `G28 Z`, `GET_POSITION` (Wert B);
  zmax = (A − B) × 0,0025 mm; 3× wiederholen.
- **Erwartet:** nahe Stock 352,24 / 352,16; Streuung notieren. Erst danach über `zmax_position` entscheiden
  (konservativ, Messwert − 5 mm).
- **Verweis:** README-k2_limits §7.

**11.5 T7 – Vergleich mit Lichtschranken-Vorausrichtung** · Gefahr: **Bewegung**
- **Befehle:** wie 11.1 (≥ 3 Kaltstarts), aber `G28 X Y` → `ZALIGN_STOCK`.
- **Erwartet:** erste z_tilt-Runde range ≈ 0,065 mm (Stock).
- **Verweis:** zalign.cfg (`ZALIGN_STOCK`).

**11.6 T8 – Entscheidung** · Gefahr: –
- **Kriterium „z_tilt reicht“:** in allen ≥ 3 Kaltstarts Δ aus der ersten z_tilt-Runde < 3 mm, Konvergenz in
  ≤ 3 Runden, keine Abbrüche, Drift nach 30 min < 0,05 mm (und `START_PRINT` führt nach Stromlos z_tilt aus).
  Sonst `KTWO_ZALIGN` vor `Z_TILT_ADJUST` beim ersten Homing nach Stromlos (Einbau erst nach Abstimmung).
- **Danach:** `[k2_zalign] allow_move: False` bzw. Variante nach Entscheidung.

---

## Phase 12 – Erster Druck und Druckfälle

**12.1 Erstdruck einfarbig, ohne CFS-Wechsel** · Gefahr: alles
- **Voraussetzung:** Phasen 0–11 grün. Slicer: CFS aus. Filament steckt aus 9.3/9.6 im Kopf (anderer Weg, Filament
  einzuführen: **noch festzulegen**). Für diesen Druck `[k2_box] allow_write`/`allow_move` auf `False` (dann ist `T0`
  Einfarbbetrieb, keine Box-Aktion, Reinigung nur als Meldung – Düse von Hand säubern).
- **Befehl:** Druck aus Mainsail starten; die Datei ruft `START_PRINT EXTRUDER_TEMP=… BED_TEMP=…` auf.
- **Erwartet:** START_PRINT: Bett und Düse 140 °C, `G28` beim Aufheizen, `_PI_ZTILT_WENN_NOETIG`, `G28 Z`, Mesh laden,
  Drucktemperatur. Erste Schicht: Temperaturkompensation bei 220 °C **+0,050 mm** (`KTWO_NOZZLE_TEMPCOMP`); keine
  Stall-Meldungen (report). END_PRINT: Z +2, Parken, Bauteillüfter S127 bis zum Abkühlen.
- **Abbruch → tun:** erste Schicht zu tief/hoch → `CANCEL_PRINT`; Fehlverhalten → N2/N1.
- **Verweis:** cfg-README §4 Schritt 9; macros.cfg (`START_PRINT`, `END_PRINT`); README-k2_nozzle_tempcomp.

**12.2 Einfarbdruck aus der CFS** · Gefahr: alles
- **Voraussetzung:** 12.1 grün; `[k2_box] allow_write`/`allow_move` wieder `True`; Slicer: CFS an.
- **Erwartet:** START_PRINT → `KTWO_BOX_PRINT_START` (`0x0d [0f 00]` + `0x0f [01]`, gilt den ganzen Druck); `T0`
  bei leerem Kopf und Hub lädt ohne Schnitt; Spülen im Schacht mit Aufweiten/Rücksetzen der Fahrgrenzen. Im Druck
  wird eine neu eingelegte Spule nicht eingelesen (Stock gleich). Druckende: Z +2 → `KTWO_BOX_END_PRINT` entlädt
  (`end_print_unload: True`: heizen, schneiden, ≈ 1,89 m zurück in die Box) → `KTWO_BOX_PRINT_END`
  (`0x0d [0f 01]` + `0x0f [00]`).
- **Abbruch → tun:** Fehler im Wechsel → PAUSE mit `!!`-Meldung (siehe 12.5); sonst N2/N1.
- **Verweis:** README-k2_box §16 (A1/A2/A5/A6).

- **Ergebnis 22.10.2026 (erster Druck überhaupt am Pi):** Variante „mit Box“ gewählt – faktisch 12.2 ohne
  `T<n>`: Filament B (PLA) steckte aus 9.6 im Kopf, Datei ohne Werkzeugbefehl. Datei selbst erzeugt (Kalibrierwürfel
  20 mm in Bettmitte, 100 Schichten 0,25 + 99 × 0,2 mm, 220/60 °C, ≈ 1,26 m Filament).
  START_PRINT ≈ 2,8 min: Reinigung über die Box, Antasten (ein Ruhetest-Retry, dann ok), „z_tilt seit dem letzten
  Stromlos bereits ausgeglichen“, Aufheizen am Schacht, Endreinigung. Druck **941 s, Zustand complete**, Düse während des
  Drucks 219,8–220,2 °C (PID 10/0.8/40), Bett 59,8–60,4 °C, keine Pause, keine Stall-/Box-Meldung. END_PRINT: Schnitt
  X10 → X−7,8 → X10, `RETRUDE Slot B ok`, `KTWO_BOX_PRINT_END` (`0x0d [0f 01]` + `0x0f [00]`). Sichtprüfung durch den
  Sichtprüfung: Würfel „sehr gut“ (Maße nicht gemessen).

**12.3 Mehrfarbdruck (Orca, Werkzeugwechsel vor der ersten Schicht)** · Gefahr: alles
- **Erwartet:** erster Einzug im Druck über den **Farbweg** (live 229,6 mm `[90, 70, 70]`), Wechsel im Druck über die
  **Slicer-Matrix** (live 160 mm `[90, 70]`); Spülmengen mit der Orca-Anzeige vergleichen. Orca-Z0-Schutz: genau eine
  Meldung „im Druck → auf Z2.00 angehoben“, die Düse bleibt 2 mm über dem Bett bis zur ersten Schicht.
- **Abbruch → tun:** Düse fährt vor der ersten Schicht auf Z0 → N2.
- **Verweis:** README-k2_limits §7; README-k2_box.

- **Ergebnis 22./23.10.2026:** **grün** – erster Mehrfarbdruck am Pi, Datei aus **Creality Print 7.3** (nicht Orca),
  2 Farben (Fach A/B, PLA), 180 Schichten, **182 Werkzeugwechsel fehlerfrei**, Druckzeit **10 h 28 min**, Zustand complete.
  Wechselkette der Datei: `M8200 P` → `M8200 R E-30` (wie Stock nur Extruder-Rückzug, `KTWO_BOX_BREAK_RETRUDE`) →
  Schnitt → `M8200 R` (Box-Rückzug) → `M8200 L I<n>` → `T<n>` (übersprungen) → Spülen durch die Datei → `M8200 O`;
  Fahrgrenzen von P bis O aufgeweitet. Orca-Z0-Schutz griff einmal wie vorgesehen
  („G1 Z0.000 im Druck → auf Z2.00 angehoben“). Vorläufe: 1. Start „Move out of range Y378“ (Grenzen-Fix), 2. Start
  Y-Homing-Aussetzer nach vielen Klipper-Neustarts (Vorprüfung wieder entfernt, Z-Schleppfehler gelöscht), dann zwei
  versehentliche Abbrüche über das Handy (Not-Aus, „Heizungen aus“ → seitdem Bestätigungsabfragen in Mainsail).
  Orca-Mehrfarbdatei im Druck noch offen (Orca-Weg mit T<n> außerhalb eines Drucks in 9.6 grün).

**12.4 PAUSE/RESUME und M600** · Gefahr: alles
- **Befehle:** im Druck `PAUSE`, dann `RESUME`; Orca-Datei mit `M600`.
- **Erwartet:** PAUSE parkt bei Y 176, Düse 140 °C. RESUME: Schacht → Aufheizen → Vorextrusion (460 mm/min bzw.
  Prime-Speed aus der Material-DB, erst ab 180 °C) → Reinigung → Rückfahrt erst X/Y, dann Z (nie unter Pausen-Z).
  `M600` → PAUSE, danach RESUME.
- **Verweis:** cfg-README; macros.cfg (`PAUSE`/`RESUME`, `_PI_PARAM`).

**12.5 CFS-Abbruch im Schacht** · Gefahr: alles (Hand am Netzschalter)
- **a) Park- und RESUME-Bahn:** Box stromlos während `T1` (Verfahren **noch festzulegen**, Hot-Plug beachten).
  Erwartet: PAUSE mit Rückfahrziel = Druckposition vor dem Wechsel; Schneidbereich nur in X, Schacht über X160 → Y345,
  nie unter Pausen-Z. Weiter: `KTWO_BOX_ERROR_CLEAR` → `T1` → `RESUME`.
- **b) RESUME-Sperre bei leerem Kopf:** wie a), dann Faden von Hand aus dem Kopf → `RESUME` muss verweigern
  („RESUME gesperrt …“); `T1` in der Pause → `RESUME` läuft. Erst danach `[k2_box] resume_redo_change: True` testen.
- **c) Temperatur:** Druck < 220 °C, Abbruch im Schacht → Meldung nennt `CFS_TEMP`, Pause 140 °C, nach `RESUME`
  ist das Ziel die Drucktemperatur (`M105`/Moonraker).
- **Abbruch → tun:** Kopf fährt in den Schneidhebel oder unter Pausen-Z → N1.
- **Verweis:** cfg-README; macros.cfg (`PAUSE`/`RESUME`).

**12.6 Druckabbruch während eines Wechsels** · Gefahr: alles
- **a)** `CANCEL` (Mainsail) mitten im Einzug. Erwartet: keine PAUSE, kein Wechsel-Fehler, Kette übersprungen,
  Box per `0x04 [00 01]` auf IDLE (Box-Reaktion in mode 5 beobachten), dann `CANCEL_PRINT` mit Entladen bei
  gemeldetem Filament; Stock-Richtwert: Box nach ≈ 36 s fertig. Der nächste Druck beginnt normal mit `T<n>`.
- **b)** Entladen beim Abbruch nach einem Spülfehler. Wie man den Spülfehler provoziert: **(noch festzulegen)**.
  Erwartet: Entladen nur bei Fehler in `EXTRUDE`/`FLUSH`/`WASTE` und gemeldetem Filament, sonst nur
  „NICHT entladen …“-Meldung.
- **Verweis:** README-k2_box §17 (C2/C3).

**12.7 Restmaterial und Runout** · Gefahr: alles
- **a) Restmaterial:** Druck mit fast leerer Spule. Beobachten: kommt Status 0x50 im Druck? Weg Kopfsensor → Düse
  < 80 mm? Menge/Abfall. Erwartet (`ending_material_purge: True`): Ausspülen in 80-mm-Stücken, bis der Kopfsensor leer
  meldet + 1 Stück, höchstens `max_tube_length` 3000; danach Box IDLE.
- **b) Runout:** `[k2_box] runout_refill: True`, Testdruck mit fast leerer Spule. Erwartet: Ersatz-Slot mit gleichem
  Material und gleicher Farbe, danach `RESUME` direkt aus dem `runout_gcode`. Laufzeit-Schalter:
  `KTWO_BOX_AUTO_REFILL ENABLE=0|1`.
- **Verweis:** README-k2_box §9, §12.5; cfg-README.

**12.8 Stall-Überwachung scharf schalten** · Gefahr: alles
- **Voraussetzung:** 3.4, 4.4, 4.5 grün.
- **Handgriff:** `[k2_motor] stall_monitor: act`, Neustart, Probedruck mit Hand am Netzschalter.
- **Erwartet:** im normalen Druck keine Flanken. (Bei echten X/Y/Z/Z1-Fehlerbits im Druck: Not-Stopp/Shutdown mit
  key-Codes; nur Warnbits: PAUSE; E: PAUSE + Extruder aus. Codes nur per `KTWO_MOTOR_CLEAR` quittieren.)
- **Abbruch → tun:** Fehlauslösungen → zurück auf `report`, STOP.
- **Verweis:** pi-host/README §11; cfg-README.

---

## Anhang A – Fehlende Befehle und Verfahren („noch festzulegen“)

| Nr. | Wo | Was fehlt |
|---|---|---|
| F1 | 1.1, 1.2, 2.5 | ~~Bootloader-Abfrage/-Start der Main-/Düsen-MCU – nur Konzept~~ **Werkzeug vorhanden (18.10.2026):** `k2-bootstart.py --main/--duese` (identify @230400 zuerst; sonst `75`, `00 FF`, `02 FD`; `--nur-erkennen` ohne Start), install-README §4c, `BOOTSTART-WERKZEUG.md`. Ports bei Anschluss-Variante B: `<uart-main>`/`<uart-duese>`. **Gerätetest offen – nur mit ausdrücklicher Freigabe** |
| F2 | 2.5, 3.3 | ~~Bootloader-Abfrage Motoren/Belts und Start `F0 [02]` – kein Werkzeug~~ **Werkzeug vorhanden (18.10.2026):** `k2-bootstart.py --rs485` (A1 an 0xFD/0xFC/0xFE, nur mode-1-Geräte: A0, F0 `[00]`/`[03]`/`[02]`; `--nur-erkennen` = nur A1), optional `install.sh --bootstart` (Standard AUS). **Gerätetest offen – nur mit ausdrücklicher Freigabe** |
| F3 | 0.8, 1.2 | ~~Vorgehen, falls die Düsen-MCU nicht an J22 Pin 6/7 liegt~~ (überholt 19.10.2026: Düse über U7/Toolhead-Kabel). Neu: Vorgehen, falls die Düsen-MCU an `<uart-duese>` stumm bleibt, obwohl Verdrahtung (0.6/0.11), Loopback (0.3) und 24 V am Toolhead (0.8) stimmen |
| F4 | 0.7, 0.9 | genauer RS485-Anschlusspunkt (Breakout-Stifte 485_A/485_B, falls auf J14; „485“-Stecker am Mainboard; CFS-Stecker; J22 Pin 7/1 nur über Zwischenadapter) |
| F5 | 4.1 | Änderung der Endstop-Polarität, falls PB11/PB12 falsch melden |
| F6 | 4.4 | gezieltes Provozieren eines Schleppfehlers/Stalls |
| F7 | 5.2 | Herstellen von „Düse ≈ 3 mm über dem Bett“ vor der ersten PROBE |
| F8 | 9.2 | „langsames“ Abfahren der Schacht-/Reinigungspositionen |
| F9 | 9.5, 12.5 | „Box stromlos“ / Box-Reset (Hot-Plug-Empfindlichkeit der RS485-Transceiver) |
| F10 | 10.3 | Inhalt eines angepassten `cycle_gcode` für die Belt-Rüttelzyklen |
| F11 | 12.1 | Filament einführen ohne CFS-Ladung |
| F12 | 12.6 | Spülfehler gezielt provozieren |
| F13 | 0.7, 0.8 | Ersatz für Steuersignale, die das T113 über J14 liefert (z. B. Gate `NCEP40P80K`/Q28 = Toolhead-24 V?, SD, Hub-Reset) – nur falls B4 solche Leitungen findet; Pegel und Pi-GPIO noch festzulegen |
| F14 | 0.11 | mechanischer Aufbau der Widerstände (1 kΩ/2 kΩ) zwischen Steckbrücken bzw. Breakout, dauerhaft und isoliert; Entscheidung über 1 kΩ auch in den TX-Leitungen (§2 Regel 4) |

---

## Anhang C – Rückweg zum Stock

Gilt für Anschluss-Variante B (Breakout an J14). Rückweg aus Variante A: Anhang E.

1. Klipper am Pi stoppen (`sudo systemctl stop klipper`), Heizungen sind aus. Pi herunterfahren und sein Netzteil
   ziehen, dann Drucker **aus** (Netzschalter, §2 Regel 4).
2. Alle Pi-Leitungen trennen: Steckbrücken/Widerstände vom Breakout (Daten-Leitungen zuerst, GND zuletzt),
   USB-RS485-Adapter vom Bus (Breakout-Stifte bzw. Stecker aus 0.9). Kamera: Adapterkabel vom Pi ab, Kamerakabel
   wieder an CAM-T. Am Breakout und am Bus darf danach nichts vom Pi mehr hängen (Regel 3).
3. Klappbügel am Breakout öffnen, Original-Flachkabel herausziehen (Halteohren schonen), Breakout weglegen. Kabel mit
   der in 0.5 notierten Kontaktseite wieder in den Stecker am T113-Board schieben, Bügel schließen. Das andere Ende
   steckt unverändert in J14 (Sichtprüfung: gerade, gerastet). J10↔J22- und Toolhead-Kabel waren nicht gelöst –
   Sichtprüfung. CFS-/485-Verkabelung in den Originalzustand.
4. Drucker einschalten. Erwartet: Das Stock-Board startet wie gewohnt. Der Pi hat nichts geflasht, also findet der
   Boot-Check gleiche Versionen und startet die Geräte nur. Die CFS-DB-Page löscht Stock wie bei jedem Kaltstart.
   Die Motor-Sys-Params waren flüchtig und kommen aus dem Stock-Init.
5. Kontrolle am Display: Klipper ready, Motoren, CFS. Antwortet der Extruder-Motor nicht (key798), einmal
   `FIRMWARE_RESTART`.
6. Pi-seitig optional zurückbauen: `./uninstall.sh` (letztes Backup) bzw. `./uninstall.sh --aelteste`
   (install-README §5). Die Overlay-Zeilen in `/boot/firmware/config.txt` (0.3) stören den Stock nicht; bei Bedarf von
   Hand entfernen.

Quellen: PI-ANSCHLUSS §6, §8.5 (Halteohren, Rückweg).

---

## Anhang D – Protokoll zum Abhaken

Ergebnis: **grün** (wie erwartet) · **gelb** (Abweichung, erklärt) · **rot** (Abbruch). Bei Rot: Log sichern, STOP.

| Schritt | Datum | Ergebnis | Bemerkung |
|---|---|---|---|
| 0.1 Stock-Daten sichern | | | |
| 0.2 Paket bauen/installieren, Tests grün | | | |
| 0.3 Pi-UARTs: Overlays, Gerätenamen (`<uart-main>`/`<uart-duese>`), pinctrl, Loopback | | | |
| 0.4 B2: U6/U7 Pin 16 (3,3 V/5 V), Ruhepegel | | | U6: … V · U7: … V |
| 0.5 Breakout am Original-Flachkabel, T113 ab (Kontaktseite notiert) | | | |
| 0.6 B3: Stifte Main-TxIN/-RxOUT, Düse-TxIN/-RxOUT, Kanal 1/2 | | | |
| 0.7 B4: GND-/5V-Stifte, 485, USB, Steuersignale | | | |
| 0.8 Breakout unter Spannung, 24 V Toolhead ohne T113 | | | |
| 0.9 RS485-/CFS-Anschluss messen | | | |
| 0.10 Pi-Versorgung (USB-C 5 V/5 A) | | | |
| 0.11 Pi ↔ Breakout verdrahtet, Widerstände geprüft | | | 2 kΩ: U6 ja/nein · U7 ja/nein |
| 1.1 Main-MCU identify an UART2 (gd32f303xe) | | | |
| 1.2 Düsen-MCU identify an UART4 (gd32f303xb) | | | |
| 1.3 Serial-Pfade, Klipper ready | | | |
| 1.4 Grundprüfung (Temp, Endstops, Probe, G28 gesperrt) | | | |
| 1.5 Lüfter kurz | | | |
| 1.6 ZALIGN T1 | | | |
| 1.7 lis2dw 0x44 / ≈ 625 Samples/s (§9 README-k2_lis2dw) | | | |
| 1.8 KTWO_SAFETY_STATUS | | | |
| 2.1 RS485 offen=True | | | |
| 2.2 CFS lesend | | | |
| 2.3 Motoren lesend | | | |
| 2.4 Belts lesend | | | |
| 2.5 Bewertung Bootloader-Autostart | | | |
| 2.6 CFS-DB-Page (laufend) | | | |
| 3.1 Motor-Init DRY_RUN (167 Schritte) | | | |
| 3.2 Motor-Init lesend | | | |
| 3.3 Motor-Init schreibend (k2_motor:ready) | | | |
| 3.4 Stall-Ruhepegel Mode 2 | | | |
| 3.5 auto_init nach Kaltstart | | | |
| 3.6 CFS-Adressvergabe (k2_box:ready) | | | |
| 4.1 Stall-Polarität PB11/PB12 | | | |
| 4.2 G28 Y / G28 X (X 342,5 / Y 3,8) | | | |
| 4.3 KTWO_LIMITS_STATUS X/Y | | | |
| 4.4 Stall report, key789, KTWO_MOTOR_CLEAR | | | |
| 4.5 CALIBRATE_CUT_POS (≈ −7,80) | | | |
| 5.1 PrTouch ohne Bewegung | | | |
| 5.2 erste PROBE | | | |
| 5.3 PROBE_ACCURACY | | | |
| 5.4 erstes vollständiges G28 | | | |
| 5.5 Kollisions-Vorprüfung Dual-Z | | | |
| 5.6 Z_TILT_ADJUST + Mesh kalt | | | |
| 5.7 Z max 350,2 / G1 Z-3 abgelehnt | | | |
| 5.8 optional PROBE_STEPS | | | |
| 6.1 fakeshutdown Kammer-Thermistor | | | |
| 6.2 Kappung 130 → 120 | | | |
| 6.3 PID Düse/Bett | | | |
| 6.4 G29 warm | | | |
| 6.5 KTWO_NOZZLE_TEMPCOMP | | | |
| 7.1 Tacho PC6 | | | |
| 7.2 Lüfterausfall → Abschaltung ≈ 15 s | | | |
| 7.3 Bett-Vorrang / 50-%-Anlauf | | | |
| 7.4 optional Kammer-Obergrenze 60 | | | |
| 8.1 optional Input-Shaper | | | |
| 9.1 CFS lesend (RFID, SLOT_SET, Pläne) | | | |
| 9.2 Kopffahrten (CUT, Schacht, Wischen, Hebelfall) | | | |
| 9.3 Einzelschritte Entladen/Laden (je Slot A, B, C, D) | | | A: · B: · C: · D: |
| 9.4 Spülbahn, DB-Temperaturen, remain_material | | | |
| 9.5 Preload + Sperren + N5 | | | |
| 9.6 ganze Wechsel außerhalb Druck | | | |
| 9.7 optional KTWO_PRTOUCH_CHECK | | | |
| 9.8 Externe Spule: Quelle, Sperrprobe, Laden/Entladen, kurzer Druck, optional RFID | | | |
| 10.1 Belt lesen | | | |
| 10.2 gemeinsamer Bus unter Last | | | |
| 10.3 KTWO_BELT_SET DRY_RUN | | | |
| 10.4 mdlx spannen | | | |
| 10.5 mdly spannen | | | |
| 11.1 T3 Kaltstart 1 | | | |
| 11.1 T3 Kaltstart 2 | | | |
| 11.1 T3 Kaltstart 3 | | | |
| 11.2 T4 zalign-Variante B lesend | | | |
| 11.3 T5 erste Ausrichtung | | | |
| 11.4 T6 Z-max | | | |
| 11.5 T7 Vergleich | | | |
| 11.6 T8 Entscheidung | | | |
| 12.1 Erstdruck einfarbig | | | |
| 12.2 Einfarbdruck aus CFS | | | |
| 12.3 Mehrfarbdruck Orca | | | |
| 12.4 PAUSE/RESUME, M600 | | | |
| 12.5a CFS-Abbruch: Park-/RESUME-Bahn | | | |
| 12.5b RESUME-Sperre leerer Kopf, resume_redo_change | | | |
| 12.5c CFS_TEMP < 220 °C | | | |
| 12.6a CANCEL im Einzug | | | |
| 12.6b Entladen nach Spülfehler | | | |
| 12.7a Restmaterial 0x50 | | | |
| 12.7b Runout-Nachladen | | | |
| 12.8 stall_monitor act | | | |
| E Variante A (nur bei Bedarf, statt 0.4–0.8/0.11) | | | Grund: |

---

## Anhang E – Variante A (Rückfallweg): RS232-Konverter an J22 und am Toolhead-Kabel

Nur nutzen, wenn Anschluss-Variante B scheitert (z. B. 0.6 findet TxIN/RxOUT nicht auf J14, J14 führt unverzichtbare
Steuersignale (F13), oder die MCUs bleiben an den Pi-UARTs trotz richtiger Verdrahtung stumm). Stand: Plan, am Gerät nie
gelaufen. Quellen: PI-ANSCHLUSS §2, §4–§6, §8.2, §8.4.

- **Prinzip:** Der Pi spricht die MCUs auf der RS232-Seite an; die Wandler U6/U7 des Bridge-Boards werden umgangen.
  - Main: isolierter USB-RS232-Konverter (Waveshare USB TO RS232/485 (B), **nur RS232-Klemmen**, A/B frei) an J22:
    RXD ← Pin 10 (RX7, Main-MCU sendet, Ruhe −5,5 V), TXD → Pin 4 (TX7), GND → Pin 2.
  - Düse: **zweiter** RS232-Konverter (der (B) hat nur einen RS232-Kanal) über einen 2×7-Zwischenadapter (PHB 2,0 mm)
    am Toolhead-Kabel: 24 V, GND, SYNC, VBUS2/D2± 1:1 durch, nur RX1/TX1 zum Konverter.
- **Achtung (abgeleitete Belegung, PI-ANSCHLUSS §8.4):** Das J10↔J22-Kabel führt außer TX7/RX7 auch SYNC (J22 Pin 6,
  PrTouch-Signal zur Main-MCU), RS485 A/B (Pin 7/1) und 5V-IN des Bridge-Boards (Pin 12). Wird es nur abgezogen,
  fehlen diese Leitungen (Bridge-Board ohne 5 V, PrTouch ohne SYNC). Deshalb auch an J22 ein 2×6-Zwischenadapter: alle
  Adern 1:1, **nur TX7/RX7 getrennt** und zum Konverter. Aufbau **(noch festzulegen)**. Das T113 auch hier vom
  Flachkabel trennen (wie 0.5, ohne Breakout), damit es nicht über J14 auf RS485 oder andere Leitungen wirkt (Regel 3).
- **Regeln:** J22 Pin 12 (+5,2 V) nie anschließen; Pin 10 und Pin 12 nie an TTL-Adapter oder Pi-Pins; nie USB-TTL an
  TX1/RX1; Pin 10 darf Pin 11/12 nicht berühren; Notlösung 2-mm-Jumper ohne Hülse, isoliert und fixiert, bis
  PHB-2,0-mm-Buchsen da sind; stromlos stecken, GND zuerst; Original-Kabel nicht zerschneiden.
- **Messung vorher:** J22-Referenz 17.10. (Kabel zur Display-Einheit an J22 abgezogen, gegen −V): Pin 4 0 V
  (4,9 kΩ), Pin 10 −5,5 V, Pin 12 +5,2 V, Pin 2/8/11 GND (PI-ANSCHLUSS §2). Pin 1/5/6/7 sind per Aufdruck geklärt
  (485_B/SD/SYNC/485_A). Am Toolhead-Zwischenadapter 24 V, GND, TX1, RX1 per Durchgang zuordnen.
- **Ablauf wie Phase 1, andere Ports:** `~/klippy-env/bin/python ./k2-identify.py /dev/serial/by-id/<rs232-adapter>`
  (Main, `gd32f303xe`) bzw. `… /dev/serial/by-id/<rs232-adapter-duese>` (Düse, `gd32f303xb`); Bootloader-Fall
  `k2-bootstart.py --main/--duese` mit diesen Ports (nur mit Freigabe); dann
  `./install.sh --printer-cfg-ersetzen --main-serial /dev/serial/by-id/<rs232-adapter> --duese-serial /dev/serial/by-id/<rs232-adapter-duese>`
  (hier ginge auch `auto`, weil USB-Konverter unter by-id stehen; Regel 12 beachten). Die Overlays aus 0.3 werden nicht
  gebraucht.
- **Rückweg:** Konverter und Zwischenadapter ab, J10↔J22- und Toolhead-Kabel wieder direkt stecken (Rastnasen),
  Flachkabel zurück ins T113 (Anhang C Schritt 3), dann Anhang C ab Schritt 4.
