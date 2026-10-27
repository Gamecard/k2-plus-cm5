# k2_chamber – Schutzlogik der PTC-Kammerheizung (L6)

Stand 18.10.2026 – **ENTWURF, am Gerät UNGETESTET.** Modul `pi-host/k2_chamber.py`,
Abschnitt `[k2_chamber]` in `config-pi/printer.cfg` (direkt unter
`[heater_fan chamber_fan]`, Defaults). Lücke **L6** aus dem Vergleich Creality-Klippy ↔
Upstream. Belege: `CR:` = Creality-Klippy der Live-FW 1.1.7.0 (auf dem Original-Drucker
unter `/usr/share/klipper/klippy/`), `UP:` = Upstream-Klipper 9cb9975.

## 1. Stock-Verhalten (belegt)

| Funktion | Stock | Beleg |
|---|---|---|
| Lüfter-Tacho | `[fan_feedback] fan0_pin: PC6 # PTC Fan`, ohne `^` (kein Pull-up); MCU-Befehle `config_fancheck`/`query_fancheck`, Abfrage alle 2 s (Druck 3 s), Wert `fan0_speed` | Stock-`printer_params.cfg`:12-17, CR:extras/fan_feedback.py |
| Stillstand → Abschaltung | Timer 1 s: `heating` und PWM > 0 und Ziel und `fan0_speed == 0` → 15 × 1 s weiterprüfen; alle 15 still → `stop_heating = True`, Fehler `key519` „PTC fan_speed is 0, turn off PTC heaters“, `M141 S0`. **Kein Shutdown**, Druck läuft weiter. Sperre fällt bei jedem neuen Ziel > 40. | CR:extras/heaters.py:76-101, :185-189, :311-312 |
| Bett-Vorrang | Watermark der Kammer: solange `heater_bed_state == 1` (Bett-Ziel > 0 und Bett mehr als `max_delta` = 2 °C neben dem Ziel bzw. Steigung > 0,5 °C/s) → `temp_coff = 0` (PTC aus); fortlaufend, also auch, wenn das Bett im Druck absackt | CR:extras/heaters.py:277-283, :431-447 |
| 50-%-Anlauf | erste 200 Regelzyklen (≈ 60 s bei 0,3 s) jeder Heizphase `temp_coff = 0.5`; Zähler 0 bei Watermark aus oder Bett-Vorrang | CR:extras/heaters.py:284-287, :314-322 |
| PTC-Lüfter | `is_ptc_fan: 1`: 30 %, nur bei PTC-PWM > 0 und erst nach ≈ 6 s Heizen | CR:extras/heater_fan.py:36-50 |

## 2. Umsetzung auf dem Pi

**Tacho (Upstream-Mittel).** `[heater_fan chamber_fan]` bekommt `tachometer_pin: PC6`
(UP:extras/fan.py `FanTachometer` → `pulse_counter.FrequencyCounter`,
`config_counter`/`query_counter`). Das Main-MCU-Dictionary (F008 GD32F303RET6)
kennt `config_counter oid=%c pin=%u pull_up=%c` und `counter_state`. Batch-Lauf P:
`config_counter oid=2 pin=PC6 pull_up=0`. PC6 ist in der Pi-Config sonst frei.
Polarität: Zähler zählt Flanken, Polarität egal. Pull-up wie Stock **aus** (ANNAHME:
Pull-up auf der Platine oder in Creality-FW intern; Stock-Config setzt keinen).
`tachometer_ppr: 2` = Upstream-Default (ANNAHME; Stock prüft nur „0“, ppr ändert nur
die rpm-Anzeige).

**PTC-Lüfter läuft immer mit (Upstream-Mittel).** UP:extras/heater_fan.py schaltet den
Lüfter mit `fan_speed: 1.0` ein, sobald `chamber_heater` ein Ziel > 0 hat – also schon
**vor** dem ersten PWM-Impuls und auch in der Bett-Vorrang-Phase. Abweichung von Stock
(30 %, verzögert) bewusst: mehr Luft ist sicherer. `enable_pin: PB2` ist zugleich
`ptc_power` (Stock identisch): Lüfter 0 → PB2 0 → PTC-Versorgung aus. Das Modul prüft
beim Start, dass der Lüfter-Abschnitt `heater: chamber_heater` hat und einen Tacho.

**Modul `k2_chamber`.**
- Umhüllt `heater.set_pwm` der Kammerheizung (wirkt auf jede Regelung, auch
  `PID_CALIBRATE`). Reihenfolge: Sperre → 0; Bett-Vorrang → 0; sonst die ersten
  `soft_start_time` s jeder Heizphase × `soft_start_power`.
- Bett-Vorrang: Bett-Ziel > 0 und Bett-Temperatur < Ziel − `bed_tolerance` (2 °C =
  Stock-`max_delta`). Ohne Stock-Steigungskriterium (vermeidet Flattern; Bett über dem
  Ziel blockiert wie Stock nicht).
- Lüfter-Überwachung (Timer 1 s): Kammer-Ziel > 0 **oder** PWM > 0, und rpm <
  `min_rpm` (oder kein Messwert) ununterbrochen ≥ `fan_fault_time` (15 s) → Fehler.
  Strenger als Stock (zählt schon ab Ziel > 0, nicht erst bei PWM > 0), weil der
  Upstream-Lüfter ab Ziel > 0 läuft.
- Reaktion `fan_fault_action`:
  - `heater_off` (**Default**, Stock-nah): Kammer-Ziel sofort 0, PWM 0, Meldung
    `!! [k2_chamber] PTC-Lüfter steht … (Stock key519) – gesperrt bis KTWO_CHAMBER_RESET`.
    **Strenger als Stock:** Die Sperre hält, bis `KTWO_CHAMBER_RESET` – jedes neue
    Ziel wird im nächsten Timer-Takt wieder 0 gesetzt (die PWM bleibt ohnehin 0),
    `M141/M191 S>40` werden mit Fehler abgewiesen. Der Druck läuft weiter (wie Stock).
  - `shutdown`: Ziel 0 + Klipper-Shutdown mit derselben Meldung (Druck bricht ab).
  - Begründung Default: Beide Varianten nehmen der PTC sofort die Leistung (PWM 0,
    Ziel 0 → Lüfter-Abschaltung → PB2 aus); die Sperre verhindert Wiedereinschalten
    ohne Prüfung. Ein Shutdown bringt für die PTC keine zusätzliche Sicherheit, würde
    aber jeden Druck verwerfen. Wer es härter will: `fan_fault_action: shutdown`.
- `M191` wartet über `KTWO_CHAMBER_WAIT MINIMUM=…` statt `TEMPERATURE_WAIT` – bricht
  bei Lüfterfehler, Ziel 0 oder Shutdown mit Fehler ab (sonst würde M191 nach der
  Abschaltung endlos warten).

## 3. Optionen (`[k2_chamber]`)

| Option | Default | Bedeutung |
|---|---|---|
| `heater` | `chamber_heater` | Kammerheizer |
| `fan` | `heater_fan chamber_fan` | Lüfter-Abschnitt mit `tachometer_pin` |
| `bed_heater` | `heater_bed` | Bett für den Vorrang |
| `fan_fault_time` | 15 (2–60) | Stillstand in s bis zur Abschaltung (Stock 15) |
| `min_rpm` | 100 | darunter gilt der Lüfter als stehend (ANNAHME; Stock: „0“). 0 schaltet die Erkennung praktisch ab |
| `fan_fault_action` | `heater_off` | `heater_off` oder `shutdown` |
| `bed_priority` | True | Bett-Vorrang an/aus |
| `bed_tolerance` | 2.0 | °C unter Bett-Ziel, ab denen die PTC wartet |
| `soft_start_time` | 60 | s mit reduzierter Leistung je Heizphase (Stock 200 × 0,3 s) |
| `soft_start_power` | 0.5 | Faktor in der Anlaufphase (Stock 0,5) |

## 4. Befehle

- `KTWO_CHAMBER_STATUS` – Ziel, PWM, Lüfter-Soll, rpm, Stillstandszeit, Bett-Vorrang,
  Anlauf, Fehler. Status auch als `printer.k2_chamber` (`fault`, `rpm`, `bed_blocking`,
  `soft_start` …).
- `KTWO_CHAMBER_RESET` – Sperre nach Prüfung des Lüfters aufheben.
- `KTWO_CHAMBER_WAIT MINIMUM=<°C> [TIMEOUT=<s>]` – Warten auf die Kammertemperatur (von M191).

## 5. Tests

- `pi-host/test_k2_chamber.py` (73 Prüfungen, echte Upstream-`Heater.set_pwm`,
  `ControlBangBang`, `ControlAutoTune`, `FanTachometer`, `ConfigWrapper`).
- Batch gegen Upstream-Klipper (Batch-Modus):
  - P: Auslieferung + Test-G-Code → EXIT 0, `config_counter … pin=PC6 pull_up=0`.
  - Q: `fan_fault_time: 3`, Test-G-Code mit `M191 S50` (Tacho im Batch 0)
    → nach 3 s „PTC-Lüfter steht … Kammerheizung AUS“, M191 bricht ab, Status
    `Fehler=True`, Ziel 0, EXIT 255, 0 Internal error.
  - R: wie Q mit `fan_fault_action: shutdown` → Shutdown mit Meldung, EXIT 255.
  - Läufe F–O unverändert.
- Batch-Hinweis: Im Batch liefert der Tacho keine Impulse; `M191` mit `[k2_chamber]`
  endet dort nach `fan_fault_time` mit Fehler (Upstream-`TEMPERATURE_WAIT` kehrt im
  Batch sofort zurück).

## 6. Am Gerät prüfen (Reihenfolge)

1. `M141 S45`, nach 3 s `KTWO_CHAMBER_STATUS`: rpm deutlich > 100. Bei rpm 0 trotz
   drehendem Lüfter: **sofort** `M141 S0`, dann `tachometer_pin: ^PC6` (Pull-up) testen;
   bei unplausibler rpm `tachometer_ppr` anpassen.
2. Lüfter-Stecker abziehen (Kammer kalt, Ziel 45): nach ≈ 15 s Meldung, Ziel 0,
   PWM 0; `M141 S45` → Fehler; Stecker zurück, `KTWO_CHAMBER_RESET`.
3. `M140 S100` + `M141 S50`: `KTWO_CHAMBER_STATUS` zeigt Bett-Vorrang=True bis das Bett
   ≈ 98 °C hat, danach Anlauf-50%=True für 60 s.
4. `verify_heater chamber_heater`: lange Bett-Vorrang-Phasen sind unkritisch, solange
   die Kammer noch im ersten Anfahren ist (`check_gain_time` 345600). Fällt die Kammer
   nach Erreichen des Ziels > 15 °C ab (Bett-Vorrang mitten im Druck), zählt der
   Upstream-Fehlerzähler (Stock pausierte ihn bei PWM 0, CR:extras/verify_heater.py:59-63).

## Konsole (Audit 23.10.2026)

- Fehler einheitlich `!! [k2_chamber] …` (Lüfterfehler, Sperre).
- Drehzahl nicht lesbar: `// [k2_chamber] PTC-Lüfter-Drehzahl nicht lesbar …`
  höchstens alle 10 min (zählt weiter als Stillstand).
- Unerwartete Ausnahme im Überwachungs-Timer: **kein** Klipper-Shutdown mehr,
  stattdessen Sperre wie bei einem Lüfterfehler (Ziel 0, `KTWO_CHAMBER_RESET`)
  und `!! [k2_chamber] Lüfter-Überwachung fehlgeschlagen …`.

Regeln, Format und Gesamttabelle: `README-k2_konsole.md`.
