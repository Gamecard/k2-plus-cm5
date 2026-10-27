# K2 Plus CM5 – Creality K2 Plus mit Upstream-Klipper auf einem Raspberry Pi CM5

> **ALPHA – Stand 24.10.2026 – Nachbau und Betrieb auf eigene Gefahr.** Bisher an **einem** Drucker erprobt
> (Stock-Firmware 1.1.7.0). Unsichere Punkte sind in den Dokumenten mit **ANNAHME** bzw. **offen** markiert.

Dieses Repo ersetzt das Upper-Board des Creality K2 Plus (Allwinner T113 in der Display-Einheit) durch einen
**Raspberry Pi Compute Module 5 (CM5)** mit **Upstream-Klipper**, Moonraker und Mainsail. Bridge-Board, Mainboard,
Düsen-Platine, Closed-Loop-Motoren, CFS-Box und Riemen-Module bleiben im Gerät und behalten ihre
**Creality-Firmware**. Eigene Klipper-Module (`pi-host/k2_*.py`) sprechen deren Protokolle und bilden die
geschlossenen Creality-Teile nach:

- RS485-Bus (`k2_485`) mit Motor-Init (`k2_motor`), CFS-Box (`k2_box`), Riemenspannung (`k2_belt`),
- Kraftsensor-Antastung PrTouch (`k2_prtouch`) und Beschleunigungssensor (`k2_lis2dw`) über die Düsen-MCU,
- Sicherheits- und Komfortfunktionen des Creality-Hosts (`k2_safety`, `k2_chamber`, `k2_limits`,
  `k2_nozzle_tempcomp`, `k2_heater_pid`, `k2_zalign`, `k2_resume`, `k2_dateipruefung`, `k2_konsole`, `k2_cfs_sensor`).

Der Protokoll-Nachbau in Python liegt in `nachbau/`; der Installer kopiert ihn auf dem Pi nach
`~/klipper/klippy/extras/k2_nachbau/`. Bedient wird über Mainsail im Browser, das Original-Touchdisplay bleibt ohne
Funktion.

## Status

| Teil | Stand |
|---|---|
| Software (`pi-host/`, `nachbau/`) | läuft auf **CM5 + Waveshare CM5-IO-BASE-B** mit Breakout-Verdrahtung am Original-Flachkabel. Am Gerät erprobt (22.–23.10.2026): Klipper „ready“ mit beiden MCUs, Motor-Init, X/Y-Homing, Kraftsensor-Antastung, Bett ausrichten und Mesh, PID, Input Shaper, Riemenspannung, CFS-Laden/-Entladen/-Wechsel, einfarbiger Druck und ein Mehrfarbdruck (Creality-Print-7-Datei, 182 Wechsel) |
| Offen | u. a. Dual-Z-Lichtschranken, einige Druckfälle (PAUSE/RESUME, Restmaterial, Stall-Überwachung), `k2-bootstart` am Gerät – siehe [`SCHNELLSTART-K2-PLUS.md`](SCHNELLSTART-K2-PLUS.md) §9 und [`PI-INBETRIEBNAHME.md`](PI-INBETRIEBNAHME.md) Anhang D |
| Trägerplatine „K2-CM5-Träger Alpha 1“ (`hardware/k2-pi-cm5-traeger/`) | Layout fertig (DRC/ERC 0 Fehler), **noch nicht gefertigt und nicht am Drucker geprüft** |
| Softwarepaket zur Trägerplatine (`pi-host/traeger-alpha1/`) | nur offline geprüft, **am Gerät ungetestet** |

## Zwei Aufbauwege

**A – CM5 + IO-Board + Verdrahtung (heute erprobt).** CM5 auf einem IO-Board mit 40-Pol-Leiste (bei uns Waveshare
CM5-IO-BASE-B), FFC-Breakout am Original-Flachkabel, vier UART-Leitungen mit 1 kΩ, USB-RS485-Adapter für den Bus.

1. [`SCHNELLSTART-K2-PLUS.md`](SCHNELLSTART-K2-PLUS.md) – der Weg vom unveränderten Drucker bis zum Druck, kurz gefasst;
   Verkabelung in Abschnitt 3, Verdrahtungsbild: [`bilder/verdrahtung-weg-a.png`](bilder/verdrahtung-weg-a.png).
2. [`PI-ANSCHLUSS-LOWERBOARD.md`](PI-ANSCHLUSS-LOWERBOARD.md) – Bridge-Board, J14-/J22-Belegung, Messprotokoll.
3. [`PI-INBETRIEBNAHME.md`](PI-INBETRIEBNAHME.md) – Fahrplan Phase 0–12 mit Erwartungswerten, Not-Aus-Stufen,
   Schalterstand und Rückweg zum Original.

**B – Trägerplatine (ungetestet).** Der Träger sitzt an den Befestigungspunkten des T113, trägt den CM5
direkt und nimmt das Original-Flachkabel auf; versorgt wird er aus den 24 V des Druckers. Breakout, Steckbrücken und
USB-RS485-Adapter entfallen.

1. [`hardware/k2-pi-cm5-traeger/README.md`](hardware/k2-pi-cm5-traeger/README.md) – Platine, Einkauf, Aufbau und Inbetriebnahme
   ([`AUFBAU-UND-INBETRIEBNAHME.md`](hardware/k2-pi-cm5-traeger/AUFBAU-UND-INBETRIEBNAHME.md)).
2. [`pi-host/traeger-alpha1/README-traeger-alpha1.md`](pi-host/traeger-alpha1/README-traeger-alpha1.md) –
   Softwarepaket und Umstieg (RS485 über UART0, CAN, Stromausfall-Wächter, `config.txt`).

## Einstieg

Mit [`SCHNELLSTART-K2-PLUS.md`](SCHNELLSTART-K2-PLUS.md) anfangen. Vor dem Umbau am **laufenden Original** die
Kalibrierwerte, die Materialdatenbank und das Düsenmaterial sichern (Schnellstart §2) – diese Dateien enthalten
Geräte- und Cloud-Kennungen und gehören **nie** in ein Repo oder Paket.

Kurzform der Installation (Einzelheiten: [`pi-host/install/README.md`](pi-host/install/README.md)):

```bash
git clone https://github.com/Gamecard/k2-plus-cm5.git
cd k2-plus-cm5
pi-host/install/pack.sh --tests --ohne-dicts      # oder --dicts ORDNER mit den MCU-Dictionaries der Creality-Firmware
scp lokal/pi-paket/k2-pi-paket-<datum>.tar.gz pi@<pi-ip>:~
ssh pi@<pi-ip>
tar xzf k2-pi-paket-<datum>.tar.gz && cd k2-pi-paket-<datum>
./install.sh --printer-cfg-ersetzen --bewegung-gesperrt   # weitere Optionen: Schnellstart §4
```

Der Ordner `lokal/` ist git-ignoriert und nimmt alles Gerätebezogene auf (gebaute Pakete, MCU-Dictionaries, optional
eine eigene Leak-Muster-Liste für `pack.sh`).

## Ordnerübersicht

| Pfad | Inhalt |
|---|---|
| [`SCHNELLSTART-K2-PLUS.md`](SCHNELLSTART-K2-PLUS.md) | Einstieg: Voraussetzungen, Sichern, Verkabelung, Installation, Kalibrierung, Slicer, Betrieb, Rückweg |
| [`PI-ANSCHLUSS-LOWERBOARD.md`](PI-ANSCHLUSS-LOWERBOARD.md) | Anschluss an Bridge- und Mainboard, Messwerte, Belegungen |
| [`PI-INBETRIEBNAHME.md`](PI-INBETRIEBNAHME.md) | Fahrplan der Inbetriebnahme (Phase 0–12, Anhänge Rückweg, Protokoll, Variante A) |
| [`BOOTSTART-WERKZEUG.md`](BOOTSTART-WERKZEUG.md) | `k2-bootstart`: Geräte aus dem Bootloader in die App starten (Gerätetest offen, Standard aus) |
| [`pi-host/`](pi-host/README.md) | Klipper-Module `k2_*.py`, Offline-Tests `test_k2_*.py`, je Modul eine `README-k2_*.md` |
| [`pi-host/config-pi/`](pi-host/config-pi/README.md) | Klipper-Konfiguration des K2 Plus (`printer.cfg`, Makros, Module, Homing, Z-Ausrichtung) mit Bedienhinweisen |
| [`pi-host/install/`](pi-host/install/README.md) | `pack.sh` (Paket bauen), `install.sh`/`uninstall.sh`, `k2-identify.py`, `k2-bootstart.py`, Start-Wächter, Creality-Brücke, Web-Anpassungen, Tests |
| [`pi-host/traeger-alpha1/`](pi-host/traeger-alpha1/README-traeger-alpha1.md) | Softwarepaket für die Trägerplatine (ungetestet) |
| `nachbau/` | Python-Nachbau der Creality-Protokolle (RS485-Bus, CFS-Box, Motoren, Adressvergabe, Bootstart, Spülen, Materialdatenbank); `ref-hi/` = Testvektoren |
| [`hardware/k2-pi-cm5-traeger/`](hardware/k2-pi-cm5-traeger/README.md) | Trägerplatine Alpha 1: Generator, KiCad-Projekt, Fertigungsdaten in `ausgabe/`, Bestell- und Aufbauanleitung |
| [`hardware/LIZENZ-UND-HAFTUNG.md`](hardware/LIZENZ-UND-HAFTUNG.md) | Lizenz (CC BY-NC-SA 4.0) und Haftungsausschluss der Hardware |
| `belt_mdl-live.py` | Creality-Original des Riemenmoduls (GPLv3), Vergleichsreferenz für `pi-host/test_k2_belt.py` |
| [`NOTICES.md`](NOTICES.md), [`LICENSE`](LICENSE) | Herkunfts- und Lizenzhinweise, GPL-3.0 |

## Sicherheit und Verbote

**Was dieses Projekt nie tut – und was auch Nutzer nicht tun sollten:**

- **Keine Motor-Kalibrierung:** Die Motor-Schreibbefehle **0x03** (Encoder), **0x04** (Elektrik-Offset) und
  **0x07** (Flash-Parameter) sind im Sendeschutz gesperrt. Den Sendeschutz nicht umgehen.
- **Kein Bootloader-Einstieg und kein Flashen** von Motoren, CFS, Riemen-Modulen oder MCUs (u. a. `FF 0B [00]`,
  fun 0xF0 Sub 01, `mcu_util`-/`mcu_util_485`-/SWD-Wege) und **kein CFS-DB-Erase** (`F0 [06]`). `k2-bootstart`
  startet nur, es aktualisiert nie.
- **Main-MCU (Lower-Board) und Düsen-MCU NICHT mit generischem Klipper flashen.** Die Module sind auf die originale
  Creality-MCU-Firmware abgestimmt (erprobt: `1.1.0.48-293-g493f9a0f`). Weil nichts geflasht wird, bleibt der
  Rückweg zum Original jederzeit möglich (Flachkabel zurück an das T113).

**Elektrik und Mechanik:**

- Arbeiten an 24 V und an der Drucker-Elektronik erfolgen **auf eigene Gefahr**. Auf dem Bridge-Board liegen
  RS232-Pegel von ±5,5 V, die Pi-UARTs vertragen höchstens 3,3 V. Stecken und Ziehen nur bei ausgeschaltetem Drucker
  **und** Pi, GND zuerst. Die 5-V-Stifte des Breakouts nie an den Pi.
- Ab der Bewegungsfreigabe kann der Kopf in den Rahmen, die Düse ins Bett fahren: immer eine Person am Netzschalter,
  Bett leer. Bus-Schreiben und Bewegung sind ab Werk gesperrt (`allow_write`/`allow_move`) und werden schrittweise
  nach dem Fahrplan freigegeben.
- Die Heizungen gehen **nie automatisch** aus (`[idle_timeout]` wie Stock): nach jedem Heizschritt
  `TURN_OFF_HEATERS`.
- Der Eingriff kann Garantie- bzw. Gewährleistungsansprüche gegenüber dem Hersteller beeinträchtigen. Es gibt keine
  Gewährleistung und keinen Support.

## Lizenzen

- **Software** (`pi-host/`, `nachbau/`, Skripte, Dokumentation außerhalb von `hardware/`): **GPL-3.0-or-later**
  ([`LICENSE`](LICENSE)). Die Module sind Erweiterungen für Klipper (GPL-3.0).
- `belt_mdl-live.py` ist ein **Creality-Original** unter **GPLv3** (Lizenzhinweis im Dateikopf).
- `nachbau/ref-hi/` stammt aus **gitstonelabs/creality-cfs-klipper** (GPL-3.0).
- **Hardware** (`hardware/`): **CC BY-NC-SA 4.0**, Alpha, nur für Testzwecke, keine kommerzielle Nutzung –
  Einzelheiten und Haftungsausschluss in [`hardware/LIZENZ-UND-HAFTUNG.md`](hardware/LIZENZ-UND-HAFTUNG.md).
- Weitere Herkunftsangaben: [`NOTICES.md`](NOTICES.md).

## Hinweis

Dieses Projekt ist unabhängig und **nicht mit Creality verbunden**. „Creality“, „K2 Plus“, „CFS“, „Raspberry Pi“
und alle anderen genannten Marken gehören ihren jeweiligen Inhabern.
