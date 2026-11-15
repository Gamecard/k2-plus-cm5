# k2_fanwatch – Tachometer-Überwachung für Hotend- und Bauteillüfter

Stand 15.11.2026 – **ENTWURF, am Gerät UNGETESTET.** Modul `pi-host/k2_fanwatch.py`
(→ `klippy/extras/k2_fanwatch.py`), Abschnitt `[k2_fanwatch]`. Der Abschnitt steht noch
in **keiner** Config; ohne ihn ist das Modul nicht geladen.
Idee aus dem Projekt K2 OpenKlipper (GPLv3), kein Code übernommen.

## 1. Zweck

- **Hotend-Lüfter steht** → Wärme staut sich im Kühlkörper, Filament wird oberhalb der
  Schmelzzone weich, die Düse verstopft.
- **Bauteillüfter steht** → schlechtes Druckbild (Überhänge, Brücken).

Beide Lüfter haben einen Tachometer-Ausgang an der Düsen-MCU (`nozzle_mcu:PA12`
Hotend, `nozzle_mcu:PC13` Bauteil, laut Stock-Config), der bisher nicht ausgewertet
wird. Das Modul zählt die Impulse mit dem Upstream-Zähler
(`pulse_counter.FrequencyCounter`, MCU-Befehle `config_counter`/`query_counter`, wie
`tachometer_pin` am `[fan]`) und meldet einen stehenden Lüfter.

## 2. Bewertung (je Lüfter)

1. Bewertet wird nur, wenn der Lüfter mindestens mit `hotend_min_speed` bzw.
   `part_min_speed` angesteuert ist. Darunter gibt es **keine** Bewertung (Zustand `aus`).
   Grund: Die Lüfter werden über ihre PWM versorgt, der Tacho ist bei Teillast
   ungenau. Die Drosselung des Hotend-Lüfters beim Antasten (0,6) liegt unter 0,95 und
   löst deshalb nie einen Alarm aus.
2. Nach dem Einschalten oder Erhöhen gilt `spinup_time` Schonfrist (Zustand `anlauf`).
3. Fehler erst, wenn die Drehzahl `fault_time` lang durchgehend 0 ist (Zustand
   `wartet` → `fehler`). Nach dem Einschalten zählt diese Zeit erst ab dem Ende der
   Schonfrist: Default 5 s + 5 s = 10 s.
4. Dreht der Lüfter wieder → Entwarnung. Wird er im Fehlerfall abgeschaltet oder
   gedrosselt, endet der Fehlerfall als „ungeklärt“ und wird beim nächsten Einschalten
   neu geprüft. Es gibt keine Verriegelung und keinen Reset-Befehl.
5. Liefert der Zähler keine Daten (Zustand `keine_messung`), wird **nie** ein
   Stillstand angenommen; es kommt ein einmaliger Hinweis.

Stärke der Ansteuerung: `[heater_fan …]`/`[fan]` aus `speed`, `[output_pin …]` aus
`value`. Der Bauteillüfter gilt nur als angesteuert, wenn auch `part_enable`
(`output_pin fan0_en`) > 0 ist.

## 3. Reaktion (`mode`)

| `mode` | Verhalten |
|---|---|
| `off` | nichts: kein Zähler auf der MCU, kein Timer |
| `report` (**Default**) | `!! [k2_fanwatch] … STEHT …` einmal je Fehlerfall, Wiederholung alle `repeat_interval`, `// … dreht wieder …` als Entwarnung |
| `act` | wie `report`, zusätzlich: im Druck (`print_stats.state == printing`, nicht pausiert) einmal je Fehlerfall `PAUSE`. Beim **Hotend-Lüfter** außerdem Extruder-Ziel 0 – auch außerhalb eines Drucks und so lange der Fehlerfall andauert (ein neues Ziel wird wieder 0 gesetzt). |

- Im Druck läuft zuerst die `PAUSE` (das PAUSE-Makro merkt sich dabei die
  Drucktemperatur für `RESUME`), **danach** wird das Ziel 0 gesetzt – auch die
  Pausentemperatur des Makros. Hängt die `PAUSE` länger als 20 s in der Warteschlange,
  wird das Ziel trotzdem 0 gesetzt.
- **Niemals:** Motoren abschalten, Klipper-Shutdown. Ein Fehler im Modul selbst wird
  nur gemeldet (`// [k2_fanwatch] Überwachung fehlgeschlagen …`).

## 4. Optionen (`[k2_fanwatch]`)

| Option | Default | Bereich | Bedeutung |
|---|---|---|---|
| `mode` | `report` | `off`, `report`, `act` | siehe 3. |
| `hotend_tach_pin` | `nozzle_mcu:PA12` | Pin oder leer | Tacho des Hotend-Lüfters; leer = nicht überwachen. Pull-up mit `^` möglich |
| `part_tach_pin` | `nozzle_mcu:PC13` | Pin oder leer | Tacho des Bauteillüfters; leer = nicht überwachen |
| `tach_ppr` | 2 | 1–12 | Impulse je Umdrehung (nur für die Anzeige in U/min) |
| `hotend_fan` | `heater_fan hotend_fan` | Abschnittsname | Lüfter-Objekt (Stärke aus `speed`) |
| `part_fan` | `output_pin fan0` | Abschnittsname | Lüfter-Objekt (Stärke aus `value`) |
| `hotend_min_speed` | 0.95 | > 0 bis 1 | Hotend-Lüfter erst ab dieser Stärke bewerten |
| `part_min_speed` | 0.5 | > 0 bis 1 | Bauteillüfter erst ab dieser Stärke bewerten |
| `spinup_time` | 5 | 0–120 s | Schonfrist nach Einschalten/Erhöhen |
| `fault_time` | 5 | 2–600 s | durchgehend 0 U/min bis zum Fehler |
| `poll_interval` | 1 | 0,2–10 s, ≤ `fault_time` | Abfragetakt |
| `repeat_interval` | 300 | 0 oder 10–86400 s | Abstand der Wiederholmeldungen; 0 = keine |
| `part_enable` | `output_pin fan0_en` | Abschnittsname oder leer | Freigabe des Bauteillüfters; leer oder nicht vorhanden = nur die Stärke zählt |
| `hotend_heater` | `extruder` | Heizername | Heizer, dessen Ziel im Modus `act` 0 gesetzt wird |
| `min_rpm` | 0 | 0–5000 | bis zu dieser Drehzahl gilt der Lüfter als stehend |
| `tach_poll_interval` | 0.0015 | > 0 bis 0,05 s | Abtastung des Tacho-Pins auf der MCU (Upstream-Default) |

Ungültige Werte brechen den Start mit einer deutschen Meldung ab
(`[k2_fanwatch] Option 'fault_time' = 1 ist zu klein (mindestens 2)`).
Fehlt ein Lüfter-Abschnitt, wird nur dieser Lüfter nicht überwacht (eine Meldung).

## 5. Config-Ausschnitt (für `config-pi/k2-module.cfg`)

```ini
# Tachometer-Überwachung Hotend-/Bauteillüfter (Modul k2_fanwatch.py,
# README-k2_fanwatch.md). report = nur melden; act = im Druck PAUSE,
# beim Hotend-Lüfter zusätzlich Extruder-Ziel 0. Status: KTWO_FANWATCH_STATUS.
[k2_fanwatch]
mode: report
hotend_tach_pin: nozzle_mcu:PA12
part_tach_pin: nozzle_mcu:PC13
tach_ppr: 2
hotend_fan: heater_fan hotend_fan
part_fan: output_pin fan0
part_enable: output_pin fan0_en
hotend_heater: extruder
hotend_min_speed: 0.95
part_min_speed: 0.5
spinup_time: 5
fault_time: 5
poll_interval: 1
repeat_interval: 300
min_rpm: 0
tach_poll_interval: 0.0015
```

## 6. Befehl und Status

- `KTWO_FANWATCH_STATUS` – Modus, je Lüfter Zustand, U/min, Ansteuerung, Stillstandszeit,
  Fehler.
- `printer.k2_fanwatch`: `mode`, `fault` (einer der Lüfter im Fehlerfall), `hotend` und
  `part` mit `rpm`, `speed`, `state`, `fault`, `fault_count`, `stalled_for`, `enabled`,
  `reason`; dazu `pause_count`, `heater_off_count`, `last_heater_target`,
  `last_message`. Beispiel im Makro: `{% if printer.k2_fanwatch.hotend.fault %}`.

## 7. Einführung (Reihenfolge)

1. Modul nach `klippy/extras/` kopieren, Abschnitt mit `mode: report` eintragen,
   Neustart. Startet Klipper nicht (MCU kennt den Zähler nicht, Pin belegt): Abschnitt
   wieder entfernen.
2. Düse heizen (Hotend-Lüfter läuft mit 1,0) → `KTWO_FANWATCH_STATUS`: Drehzahl deutlich
   über 0 (fremde Messung: etwa 8000 U/min). Steht dort 0 trotz drehendem Lüfter:
   Pull-up testen (`^nozzle_mcu:PA12`).
3. Bauteillüfter in Stufen (`M106 S64`, `S128`, `S191`, `S255`), je Stufe
   `KTWO_FANWATCH_STATUS` (fremde Messung bei voller Leistung: etwa 7500 U/min). Die
   kleinste Stufe, die sicher eine Drehzahl > 0 zeigt, bestimmt `part_min_speed`.
4. Antasten/Bett-Netz fahren: Es darf keine Meldung kommen (Drosselung 0,6).
5. Einen Lüfter-Stecker ziehen (Düse kalt lassen bzw. nur kurz): nach etwa 10 s rote
   Meldung, nach dem Einstecken Entwarnung.
6. Einige Drucke in `report` beobachten. Erst wenn keine Fehlmeldung auftritt:
   `mode: act`.

## 8. Grenzen und Annahmen

- ANNAHME: Die Tacho-Pins `nozzle_mcu:PA12`/`PC13` und `tach_ppr` 2 stammen aus der
  Stock-Config bzw. sind Upstream-Default; am Gerät nicht gemessen. Ohne Pull-up wie in
  der Stock-Config.
- ANNAHME: Die Düsen-MCU verträgt zwei zusätzliche Zähler mit 1,5 ms Abtastung. Bei
  Zeitfehlern der MCU `tach_poll_interval` vergrößern (die Drehzahlanzeige wird dann
  ungenau, „0 = steht“ bleibt gültig).
- ANNAHME: Der Upstream-Zähler hält `_last_time = None`, bis die MCU die erste Antwort
  geschickt hat (daran wird „keine Messung“ erkannt).
- Bei Teillast unter `*_min_speed` wird ein stehender Lüfter **nicht** erkannt.
- Ändert der Slicer die Lüfterstärke häufiger als alle `spinup_time` Sekunden nach
  oben, bleibt kein Bewertungsfenster.
- `act` bei defektem Tacho (Lüfter dreht, Zähler zeigt 0): Druck pausiert, Heizen der
  Düse wird immer wieder abgebrochen → `mode: report` setzen.
- `act`, Hotend-Lüfter steht weiter und der Druck wird fortgesetzt: Das Ziel wird
  erneut 0 gesetzt; ein wartendes `M109` kehrt dann nicht von selbst zurück
  (Upstream-Verhalten bei Ziel 0) und muss abgebrochen werden. Vor `RESUME` den Lüfter
  prüfen.
- Der Bauteillüfter wird durch `PAUSE` abgeschaltet (Fehlerfall endet „ungeklärt“);
  dreht er nach `RESUME` weiterhin nicht, folgt nach etwa 10 s die nächste `PAUSE`.
- Der Wechsel von/nach `mode: off` braucht einen Neustart (Zähler in der
  MCU-Konfiguration).

## 9. Tests

`cd pi-host && python3 test_k2_fanwatch.py` – ohne Klipper und ohne Hardware
(Attrappen für Printer, Reactor, Lüfter, Zähler, Heizer, Druckzustand). Nicht
abgedeckt: der echte MCU-Zähler, die echten Drehzahlen, das Zusammenspiel mit dem
PAUSE-Makro am Gerät.
