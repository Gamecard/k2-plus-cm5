# k2_safety – Sicherheits-Nachrüstungen (L1 fakeshutdown, L4 Temperaturgrenzen)

Stand 18.10.2026 – **ENTWURF, am Gerät UNGETESTET.** Modul `pi-host/k2_safety.py`,
Abschnitt `[k2_safety]` in `config-pi/k2-module.cfg` (aktiv, Defaults).
Lücken **L1** und **L4** aus dem Vergleich Creality-Klippy ↔ Upstream.
Belege: `CR:` = Creality-Klippy der Live-FW 1.1.7.0 (auf dem Original-Drucker unter
`/usr/share/klipper/klippy/`),
`UP:` = Upstream-Klipper 9cb9975.

## 1. L1 – `fakeshutdown` der Creality-MCU-FW

**Problem.** Bei einem ADC außerhalb des Bereichs (Thermistor abgezogen, Kurzschluss)
sendet die Creality-MCU-FW (Main und Düse) **keinen** `shutdown`, sondern die eigene
Antwort `fakeshutdown clock=%u static_string_id=%hu`. Die MCU läuft weiter.
- Dictionaries F008 (Main GD32F303RET6, Düse GD32F303CBT6): Antwort-ID 77
  `fakeshutdown clock=%u static_string_id=%hu`, `static_string_id` 30 =
  `mcu0 ADC out of range` (Main) bzw. `noz0 ADC out of range` (Düse).
- Stock-Host: merkt sich die Meldung (CR:serialhdl.py:314-326), prüft alle 3 s die
  Temperaturen und ruft bei < 0 oder > 500 °C `TURN_OFF_HEATERS` + Fehler `key509-518`
  (CR:mcu.py:650-711, `TURN_OFF_HEATERS` :705).
- Upstream: kein Handler → `serialhdl.handle_default` schreibt nur `got {...}` ins Log
  (UP:serialhdl.py `handle_default`). Bis `verify_heater` greift, vergehen 20 s (Düse)
  bzw. 60 s (Bett) bei voller Leistung.

**Lösung.**
- Bei `klippy:mcu_identify` (Dictionary geladen) prüft k2_safety je MCU aus `mcus`
  mit `mcu.check_valid_response(...)`, ob das Dictionary die Antwort im exakten Format
  kennt, und registriert dann über die Upstream-API `mcu.register_serial_response`
  (`AsyncResponseWrapper`, aktiv nach dem Config-Senden der MCU). Kennt das Dictionary
  `fakeshutdown` nicht (z. B. Upstream-MCU-FW), wird nichts registriert; Status
  `kein fakeshutdown im Dict`.
- Reaktion (ohne G-Code-Queue):
  1. **sofort im Serial-Thread:** `set_temp(0)` für alle Heizer (Heater-Lock, thread-sicher;
     wirkt wie `TURN_OFF_HEATERS`);
  2. **im Reactor:** `printer.invoke_shutdown(<Meldung>)`. Der Upstream-Shutdown schickt
     `emergency_stop` an **alle** MCUs, die Ausgänge gehen auf ihre Shutdown-Werte.
- Weitere `fakeshutdown` setzen die Heizer erneut auf 0. Es gibt keinen zweiten Shutdown.
- Meldung (Mainsail/Konsole `!! …` + Shutdown-Grund), Beispiel:
  `k2_safety: MCU 'nozzle_mcu' meldet fakeshutdown "noz0 ADC out of range" (Creality-MCU-FW: Thermistor/ADC außerhalb des Bereichs; Stock-Host: TURN_OFF_HEATERS + key509-518, CR:serialhdl.py:322-326, CR:mcu.py:650-711) [key509 nozzle extruder_temp:-14.20] – alle Heizungen aus, Shutdown. Fühler/Stecker prüfen, dann FIRMWARE_RESTART.`
  Den Stock-Schlüssel in `[...]` gibt k2_safety nur an, wenn der zugehörige Fühler
  im Moment der Meldung schon < 0 bzw. > 500 °C zeigt. Die Zuordnung ist dieselbe wie
  in CR:mcu.py:650-711: noz0 → `extruder` (key509/515), mcu0 → `chamber_temp`
  (key511/517) und `mcu_temp` (key512/518).
- **Strenger als Stock.** Stock schaltet nur die Heizer ab und meldet den Fehler; k2_safety
  löst einen echten Shutdown aus (Druck abgebrochen, danach `FIRMWARE_RESTART`). Grund: Ein
  Thermistorfehler macht die Temperaturregelung blind. Ob die MCU-FW im
  `fakeshutdown`-Pfad die Heizausgänge selbst abschaltet, ist **offen**.
- **Abschalten** nur mit `fakeshutdown_guard: False`. Dann gilt das Upstream-Verhalten
  (nur Log), und bei jedem Start erscheint eine WARNUNG. Nur zur Fehlersuche gedacht.

**Noch offen (Gerätetest):** Bei kaltem Gerät den Thermistorstecker der Kammer abziehen.
Im Log muss `fakeshutdown` stehen, danach der Shutdown mit der Meldung oben.

## 2. L4 – Obergrenzen der Solltemperaturen

**Stock.** `PrinterHeaters.set_temperature` kappt jede Sollvorgabe auf
`[gcode_macro product_param]` (CR:extras/heaters.py:614-625). Werte aus F008
`printer_params.cfg` (gleich in der Live-Config): `variable_nozzle_temp: 350`,
`variable_bed_temp: 120`, `variable_chamber_temp: 60`. M140/M190 kappen zusätzlich auf
`max_temp − 15` (CR:extras/heater_bed.py:47-48); mit dem Pi-`max_temp` 135 ergibt das
ebenfalls 120. Upstream kennt nur `max_temp` (Pi: Düse 390, Bett 135, Kammer 80).

**Umsetzung.** Bei `klippy:connect` umhüllt k2_safety `set_temp` **und** `alter_target`
der drei Heizer-**Objekte** (Instanz-Attribut). Damit sind alle Wege erfasst, ohne
G-Code-Handler zu ersetzen (kein Konflikt mit Makros oder `rename_existing`):

| Weg | Upstream-Pfad | gekappt |
|---|---|---|
| M104/M109 | extruder.py `cmd_M104` → `set_temperature` → `set_temp` | ja |
| M140/M190 | heater_bed.py → `set_temperature` → `set_temp` | ja |
| SET_HEATER_TEMPERATURE (auch die Makros M141/M191) | heaters.py → `set_temperature` → `set_temp` | ja |
| PID_CALIBRATE | `set_temperature` + `ControlAutoTune.alter_target` | ja |
| Module mit direktem `heater.set_temp` | `set_temp` | ja |

**Entscheidung: kappen + Warnung (wie Stock), Default `over_limit: clamp`.** Ein
Slicer-G-Code mit z. B. `M104 S360` läuft wie beim Stock-Gerät weiter, nur mit 350 °C.
Ausgegeben wird `// [k2_safety] Solltemperatur 360.0 für 'extruder' auf 350.0 gekappt …`.
`over_limit: error` weist solche Befehle stattdessen mit Fehler ab: Im Druck bricht
das den Druck ab. `alter_target` (Regelschleife) kappt in beiden Modi und wirft nie
einen Fehler. Die Upstream-Prüfung `min_temp`/`max_temp` bleibt unverändert dahinter.

**Hinweis Makro M191 (macros.cfg, nicht Teil dieses Moduls):** `M191 S70` setzt das Ziel
auf 60 (gekappt), wartet danach aber mit `TEMPERATURE_WAIT … MINIMUM={s - 1}` auf 69 °C.
Das wartet ohne Ende. Empfehlung für das Makro: `s` vor dem Warten auf 60 begrenzen.
M141 ist davon nicht betroffen.

## 3. Optionen `[k2_safety]`

| Option | Default | Bedeutung |
|---|---|---|
| `fakeshutdown_guard` | `True` | L1 an. `False` = Upstream-Verhalten (nur Log) + WARNUNG |
| `mcus` | `mcu, nozzle_mcu` | überwachte MCUs (Name wie `[mcu <name>]`) |
| `max_nozzle_temp` | `350` | Grenze Düse (Stock `nozzle_temp`) |
| `max_bed_temp` | `120` | Grenze Bett (Stock `bed_temp`) |
| `max_chamber_temp` | `60` | Grenze Kammer (Stock `chamber_temp`) |
| `over_limit` | `clamp` | `clamp` = kappen + Warnung, `error` = abweisen |
| `nozzle_heater` | `extruder` | Pflicht, sonst Config-Fehler |
| `bed_heater` | `heater_bed` | Pflicht, sonst Config-Fehler |
| `chamber_heater` | `chamber_heater` | optional; fehlt er, gibt es eine Log-Warnung und `missing_heaters` |

G-Code `KTWO_SAFETY_STATUS` (nur lesend, auch im Shutdown) zeigt: MCU-Überwachung,
ausgelöst ja/nein, Grenzen, Anzahl Kappungen. Status-Objekt `printer["k2_safety"]` hat die
Felder `fakeshutdown_guard`, `monitored`, `triggered`, `event_count`, `last_event`,
`over_limit`, `limits`, `missing_heaters`, `clamp_count`, `last_clamp`.

## 4. Tests

- `pi-host/test_k2_safety.py` (64 Prüfungen). Echte Upstream-Teile:
  - ConfigWrapper, MessageParser mit den F008-Dictionaries
  - `MCU.check_valid_response`/`register_serial_response` + `AsyncResponseWrapper`
  - `SerialReader.handle_default`
  - Heater/PrinterHeaters/heater_bed/extruder/pid_calibrate

  Die Nachricht `fakeshutdown` wird aus dem Dictionary kodiert und dekodiert und aus
  einem Fremd-Thread unter dem Serial-Lock verteilt, wie `serialhdl._bg_thread`.
- Batch gegen Upstream-Klipper (Batch-Modus, lokale Testumgebung):
  - Lauf F: EXIT 0. Die übrigen Läufe zeigen das bekannte EXIT-Bild, kein Internal error.
  - **L** (Test-G-Code): Kappung M104 400→350, M140 130→120,
    SET_HEATER_TEMPERATURE/M141 Kammer 75/70→60, EXIT 0.
  - **M** (Test-G-Code; ein nur-Test-Modul, nicht im Repo,
    ruft den registrierten Serial-Handler aus einem Fremd-Thread auf): Heizer 0 →
    Shutdown → `emergency_stop` an beide MCUs (parsedump), EXIT 255 (Shutdown).
  - **N** (`over_limit: error`): M104 S400 abgewiesen, EXIT 255.

## 5. Installation

`k2_safety.py` fällt unter `pi-host/k2_*.py`. `install.sh` kopiert es nach
`klippy/extras/`, `pack.sh` packt es mit ein; `test_k2_safety.py` läuft in Schritt 6
mit. Ein eigener Schritt ist nicht nötig.

## Konsole (Audit 23.10.2026)

- fakeshutdown: `!! [k2_safety] MCU '…' meldet fakeshutdown …` – **sofort**
  (vor dem Shutdown, nicht gepuffert). Scheitert das Abschalten eines Heizers,
  steht in derselben Meldung „ACHTUNG: Abschalten fehlgeschlagen für … –
  Netzschalter!“. Aus dem Serial-Thread wird nie in die Konsole geschrieben.
- Kappung, fehlender optionaler Heizer und `fakeshutdown_guard: False`:
  `// [k2_safety] …` (Start-Meldungen kurz nach „ready“).

Regeln, Format und Gesamttabelle: `README-k2_konsole.md`.
