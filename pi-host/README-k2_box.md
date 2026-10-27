> **ENTWURF – am echten Gerät UNGETESTET (Stand 16.10.2026).** Klipper-Modul [k2_box] für Upstream-Klipper; offline getestet (`python3 test_k2_box.py`, 28/28 inkl. Filamentwechsel §8, Auto-Nachladen §9 und RESUME nach Wechsel-Abbruch §10). Entwurf durch einen weiteren KI-Agenten, von uns geprüft.

# Klipper-Modul [k2_box] + serial485-Fixes (Entwurf, 16.10.2026)

> **ENTWURF – am echten Gerät UNGETESTET (Stand 16.10.2026).** Klipper-Zusatzmodul
> für Upstream-Klipper auf einem Pi-Host; nur offline mit simulierter Box
> getestet (`python3 test_k2_box.py`, 9/9) plus serial485-Selbsttest
> (`cd nachbau && python3 -B serial485.py`, 72/72).

Unbelegtes ist unten als **ANNAHME** markiert.

## 1. Ergebnis-Übersicht

| Datei | Zweck | Stand |
|---|---|---|
| `k2_box.py` | Klipper-Modul `[k2_box]`: Config wie `[k2_motor]`-Stil + `box_addr`/`poll_interval`; Controller A0–A6 (Auto-Addr nach `k2_motor:ready`, 0x0a-Poll retries=0, RFID/Remain/Version/HW lesend, Preload 0x0d op 0–3, Extrude/Retrude live-identisch, Cut-Platzhalter); Guards (allow_write, nie 0x50/0x06, keine Bursts, ein Lock mit `[k2_motor]`); G-Codes `KTWO_BOX_STATUS/LOAD/UNLOAD/PRELOAD`; `get_status` für Moonraker | neu, getestet |
| `test_k2_box.py` | `FakeBoxBus` (Zustände, Hub-Maske, Odometer +50 mm/Poll, Fehler 0x0c bei ~245 mm für Slot A wie Kanal-A-Defekt, RFID/Remain/Version/HW, Auto-Addr 0xA0–0xA3) + `FakeMotorBus` (gleicher Lock) + `FakePrinter` → 9 Prüfungen (Load, Fehlerabbruch, Unload, allow_write, Live-Frames, Lock, Lesen/Poll/Status, Preload/Addr/Guards, G-Codes) | neu, **9/9 ok** (s. §5) |
| `nachbau/serial485.py` | zwei Fixes (B.1 `_done`-Begrenzung, B.2 Bg-Race) + 7 neue Selbsttest-Checks (§6) | **72/72 ok** (65 bestehend + 7 neu) |
| `README-k2_box.md` | diese Datei | neu |

Testbefehle (ohne Klipper, ohne Hardware, ohne Netzwerk):

```bash
cd pi-host
python3 test_k2_box.py
cd ../nachbau
python3 -B serial485.py
```

## 2. Architektur

```
printer.cfg [k2_box] ──► PrinterK2Box (Klipper-Hülle, load_config)
│                            │ parse_klipper_config() → parse_options() (rein)
│                            │ Events: klippy:connect, k2_motor:ready
│                            │ G-Codes: STATUS/LOAD/UNLOAD/PRELOAD
│                            ▼
K2BoxController (reine Logik A0–A6, ohne Klipper importierbar)
   ├─► BoxAction (nachbau/box.py – WIEDERVERWENDET)
   │       communication_get_box_state (0x0a, retries=0) ──┐
   │       communication_get_rfid/remain/version/hw/…     │ lesend
   │       communication_set_pre_loading (0x0d, op 2/3    │ schreibend
   │         Timeout >=380 s, retries=0)                   │ (nur allow_write)
   │       communication_extrude/retrude (0x10/0x11,      ┘
   │         retries=0, lange Timeouts) + set_box_mode/feed/print
   │         + preload_inhibit (0x0f) + sensor/buffer
   ├─► BoxAutoAddrSerial ──► AutoAddr (nachbau/auto_addr.py)
   │       cmd_send_data_with_response um transact_full
   │       (expect_addr=None für 0xFE-Broadcasts)
   └─► bus (serial485-artig, /dev/ttyS5, 230400)
           DIESELBE Instanz wie [k2_motor] → EIN Lock (Halbduplex)
```

**Wiederverwendung statt Neuerfindung:** Keine Frame-Bytes hart codiert.
Alle Sequenzen/Timeouts/Retries/Parser kommen aus den geprüften Modulen:
`box.BoxAction` (+ `CfsStatusError`/`StatusBytes`, `func_retries`,
`preload_force_timeout`, `parse_box_state`, `get_rfid`, …),
`cfsseq.build_extrude_sequence`/`build_retrude_sequence` (+ `slot_to_mask`,
`EXTRUDE_LIVE_CMDS_B`, `RETRUDE_LIVE_CMDS_A`, `requests_from_log_cmds`,
`filter_funs`), `auto_addr.AutoAddr`, `serial485.build_frame` (nur für
Test-Vergleiche). Der Test beweist Byte-Gleichheit (15 + 7 Frames).

**Controller-Methoden (Umfang Teil A):**

- `describe_plan()` – Trockenlauf-Text (kein Bus).
- `ensure_addr()` / `on_motor_ready()` – A0 Adressvergabe (AutoAddr
  `startup()` + `online_once()`), erst nach `k2_motor:ready` (gleiche
  Reihenfolge wie beim Stock-Boot). Ohne ready → `wait_motor`,
  ohne allow_write → `skipped` (0xA0 schreibend).
- `readdr(printing)` – EIN weiterer Auto-Addr-Durchlauf (`online_once()`)
  für die Wiederanmeldung einer Box nach Stromverlust (s. „Wiederanmeldung
  nach Box-Stromverlust“); `None` ohne erste Adressvergabe oder ohne
  allow_write.
- `poll_status()` – A1 0x0a-Poll (retries=0 wie Original),
  Cache für Moonraker. Lesend, auch ohne allow_write.
- `get_rfid(slot)` / `get_remain(mask)` / `get_version_sn()` /
  `get_hardware_status()` / `get_sensor(pos)` / `get_buffer()` /
  `get_wheel()` – A2 lesend (retries wie Original via `func_retries`).
- `do_preload(slot, op)` – A3 0x0d op 0–3 (op 2/3 langer Timeout + retries=0,
  keine Bursts).
- `do_extrude(slot, odo_polls=5)` / `do_load(...)` – A4 Box-Load:
  `04 IDLE → 0f an → 08 Sensor → 10/00 → 10/04 → 10/05 x5 → 10/06 →
  10/07 p=3 → 05 → 04 PRINT → 0f aus`. Abbruch bei Status != 0 mit
  `CfsStatusError`-Text (Slot-Kontext). Byte-identisch zu Live (s. §5).
- `do_retrude(slot, repeats=4)` / `do_unload(...)` – A5 Box-Unload:
  `04 IDLE → 08 Sensor → 11 p0 x4 → 11 p1` (wie cfsseq).
- `do_cut(...)` – A6 ~~Platzhalter~~ seit 16.10.2026: liefert den G-Code-Plan
  der Schneidfahrt, sendet NICHTS an die Box (Ausführung `KTWO_BOX_CUT`, §8).
- `get_status(eventtime)` – Moonraker-Objekt (nur Cache, kein Bus):
  Zustand, Hub-Maske, Temp/Feuchte, Slots, FW, ready.
- `start_background()` / `stop_background()` – Threading-Poll (offline/Test;
  in Klipper Reactor-Timer, ANNAHME).

**Sicherheitsregeln:**

- Schreiben nur mit `allow_write: True` (Default False → nur lesen + Plan).
  Der Guard wirft `PermissionError`, bevor irgendetwas gesendet wird
  (Test beweist: null mechanische Frames).
- Nie 0x50 (Config #6 + Reset) / nie 0x06-Feeder (FW-Bug Maske aus r7)
  – `check_not_forbidden()` in jedem Sendepfad + Test.
- Keine Bursts: 0x10/0x11 + 0x0d op 2/3 mit retries=0 (ein Retransmit würde
  die Mechanik doppelt starten; Box hat keine Duplikaterkennung).
  Rest via `box.func_retries` (0x0a-Poll retries=0, im Live-Mitschnitt
  10 560 Polls).
- Bus-Lock teilen: Modul öffnet den Port NICHT, sondern bekommt `bus`
  injiziert – dieselbe Instanz wie `[k2_motor]` (EIN Port ttyS5, EINE Queue,
  EIN Lock über die ganze Transaktion, vgl. `MAX_BUS_HOLD_S`). Test mit
  gemeinsamem `threading.Lock` beweist keine Überlappung (8 Transaktionen).

**G-Codes:** `KTWO_BOX_STATUS` (frischer Poll + Bericht, lesend),
`KTWO_BOX_LOAD SLOT=A..D [DRY_RUN=1]`, `KTWO_BOX_UNLOAD SLOT=… [DRY_RUN=1] [FORCE=1]` (Schnittschutz §8.3),
`KTWO_BOX_PRELOAD SLOT=… OP=0..3 [DRY_RUN=1]`. Ohne allow_write bzw. mit
DRY_RUN nur Plan (nichts gesendet). Fehler als `CfsStatusError`-Klartext
(z.B. 0x0c Odometer-Stillstand); Shutdown nur per `shutdown_on_failure`.

## 3. Einbindung in Upstream-Klipper

1. Dateien kopieren (vollständiger Weg: `config-pi/README.md` §3):
   - `k2_box.py`, `k2_transport.py`, `k2_485.py` → `klippy/extras/`
   - `nachbau/*.py` (mind. `box.py`, `cfsseq.py`, `auto_addr.py`,
     `serial485.py`) → `klippy/extras/k2_nachbau/`. k2_box fügt seinen
     Ordner NICHT in `sys.path` ein; `k2_transport.ensure_nachbau_path()`
     hängt `k2_nachbau/` nur an und prüft die Herkunft der Module
     (Fix 16.10.2026, Import-Falle `statistics`).
2. `printer.cfg`-Beispiel (zusammen mit `[k2_motor]`, Pins gekürzt):

```ini
[k2_motor]
switch: 1
motor_closed_loop: x,y,z,z1,e
retries: 4
allow_write: False
shutdown_on_failure: False
auto_init: True
main_mcu: mcu
nozzle_mcu: nozzle_mcu
motor_x_dir: PB9,0
motor_x_step: PB10,0
motor_x_stall: PB11
# ... restliche Pins/Params aus motor_control.cfg ...

[k2_box]
box_addr: 1
allow_write: False          # True für echte Loads/Unloads/Preloads
shutdown_on_failure: False  # True → SHUTDOWN bei Box-Fehler (sonst nur Meldung)
auto_init: True
poll_interval: 4.0          # s, Original-Herzschlag alle ~4 s

# Geteilter Bus: Modul k2_485.py stellt EINE Serial485-Instanz als Objekt
# 'k2_485' bereit (EIN Lock, Halbduplex) – für [k2_motor], [k2_box], [k2_belt].
[k2_485]
serial: /dev/serial/by-id/usb-…-if00-port0
baud: 230400
```

3. Ablauf prüfen:
   - `KTWO_BOX_STATUS` → Poll + `Zustand=STATE_IDLE Hub=0x00 Temp=… Slots=…`
     (geht auch mit `allow_write: False`).
   - `KTWO_BOX_LOAD SLOT=B DRY_RUN=1` → Plan, nichts gesendet.
   - `allow_write: True` + `KTWO_BOX_LOAD SLOT=B` → echter Box-Load
     (Heizen/Fahren per Makro M8200/BOX_LOAD_MATERIAL davor – hier nur Box).
   - Auto-Addr läuft nach `k2_motor:ready` von selbst (vorher `wait_motor`).

## 4. Annahmen (Unbelegtes – alles mit ANNAHME im Code markiert)

- **Upstream-API ohne Quellbaum:** `register_event_handler("klippy:connect"
/"k2_motor:ready")`, `register_command`, `get_options/get`, `reactor.pause/
register_timer`, `send_event`, `gcode.respond_info/error`, `GCodeCommand.
get_int` – gegen die bekannte API geschrieben (Muster `k2_motor_init.py`),
Kapselung in Controller/Hülle, vor Hardware-Lauf gegen echten Quellbaum prüfen.
- **Geteilter Bus:** Upstream hat keinen 485-Manager – seit 16.10.2026 liefert
  `[k2_485]` (`k2_485.py`) das `printer`-Objekt `'k2_485'` mit derselben
  `serial485.Serial485`-Instanz für `[k2_motor]` (`Rs485Transport`),
  `[k2_box]` (`BoxAction`) und `[k2_belt]`. Ohne `[k2_485]` Diagnose-Modus
  („Abschnitt [k2_485] fehlt“); Port-/Bus-Fehler bei LOAD/UNLOAD/PRELOAD →
  G-Code-Fehler (kein Shutdown). Lock-Teilung im Test mit einem `Lock` bewiesen.
- **Auto-Addr-Persistenz:** Uniids nur in-memory (`_addr_store`); Stock speichert
  in `printer.cfg` (`mb_addr_table_uniids`) – Übernahme in `configfile` fehlt
  (Entwurf). `expect_addr=None` für 0xFE-Broadcasts.
- **Cut:** ~~Platzhalter~~ seit 16.10.2026 abends umgesetzt (§8): reine
  Kopfbewegung `KTWO_BOX_CUT` (Beleg: im Live-Cut kein Box-/Motor-Frame);
  Motor-Boards nur bei der Cut-KALIBRIERUNG (Kp 300/Stall 3, nicht auf dem Pi).
- **Toolhead-Seite von Load/Unload:** ~~nur Original-Makros~~ seit 16.10.2026
  abends als Wechsel-Schritte im Modul (§8: Heizen, Schacht X133 Y378,
  Spülen, Abstreifen, Stage 7). `KTWO_BOX_LOAD/UNLOAD` bleiben reine
  Box-Förderung mit fester Poll-Zahl (`odo_polls=5`, Beleg
  `EXTRUDE_LIVE_CMDS_B`); `KTWO_BOX_EXTRUDE` pollt bis zum Kopfsensor.
- **Neue Optionen** (`box_addr`, `allow_write`, `shutdown_on_failure`,
  `auto_init`, `poll_interval`) sind Entwurfs-Erweiterungen (Defaults sicher).
  `SLOT=A..D` plus Komfort `0..3` (Original NUM numerisch:
  NUM=A → key60); Masken als SLOT abgelehnt (Verwechslung).
- **0x0a-Cache:** `get_status` liefert letzten Poll (kein Bus) – vor erstem Poll
  `unknown` (Klipper ruft oft auf). Temp/Feuchte aus AHT30,
  Hub-Maske = Sensor B, `inlet_tag_mask` nicht in `get_status`
  (Zweck des Sperrbits nur vermutet).

## 5. Testergebnisse (16.10.2026, ohne Hardware, ohne Klipper)

`python3 test_k2_box.py` → `ERGEBNIS: ok (9/9 ok)`:

1. **Kompletter Load-Ablauf (B) – ok:** Odometer 250,0 mm (5×50), Hub B belegt.
2. **Fehlerabbruch 0x0c mit Meldung (A ~245 mm) – ok:** `CfsStatusError`
   `0x0c (Odometer-Poll (mode 5) SLOT=A: Odometer-Stillstand 500 ms)`,
   danach `STATE_ERROR` + `error_code 0x0c` (Vorbild: am Original-Drucker
   beobachteter Kanal-A-Defekt, Abbruch nach ≈240–250 mm → key836).
3. **Unload (B) – ok:** Hub frei nach Retrude.
4. **allow_write=False schreibt nichts – ok:** alle 5 mechanischen Aufrufe mit
   `PermissionError`, null Frames mit 0x0d/0x04/0x0f/0x10/0x11/0xA0; 4 lesende
   Calls ok; Cut-Platzhalter wirft wie verlangt.
5. **Frame-Folge == Live-Sequenzen – ok:** Extrude 15 Frames ==
   `build_extrude_sequence(0x02, pushes=5)` == `EXTRUDE_LIVE_CMDS_B`;
   Retrude 7 Frames == `build_retrude_sequence(0x01)` ==
   `RETRUDE_LIVE_CMDS_A` gefiltert (04/08/11; 0x0e-Zwischenabfragen sind
   Diagnose, Beleg cfsseq-Docstring).
6. **Gemeinsamer Lock (keine Überlappung) – ok:** 8 Transaktionen (4 Box-0x0a
   + 4 Motor-0x0C) mit je 15 ms Arbeit über EIN `Lock` – Intervalle
   überlappungsfrei.
7. **Lesen + Poll retries=0 + Moonraker-Status – ok:** 0x0a-Call mit
   `retries=0` (Original), Temp 22 °C/46 %, RFID `TAGB…`,
   Remain B=29, Version `153`, HW `all_ok`, Sensor/Buffer, `get_status`
   mit Zustand/Hub/Temp/Feuchte/Slots.
8. **Preload/Addr/Guards – ok:** 0x0d op 0/1 mit retries=4 (Original),
   op 2/3 mit retries=0 + Timeout ≥380 s (Beleg `preload_force_timeout`);
   0x10/0x11 überall retries=0 (keine Bursts); `ensure_addr` vor ready ohne
   einen Frame (`wait_motor`), nach ready `ready` mit 0xA1/0xA2; alle
   SLOT/OP-Guards + `check_not_forbidden(0x50/0x06)` senden nichts.
9. **G-Codes via FakePrinter – ok:** connect → `k2_box:ready`-Warten,
   `handle_motor_ready` mit Auto-Addr, STATUS/LOAD/UNLOAD/PRELOAD antworten
   `ok`, DRY_RUN sendet nichts (`Trockenlauf`), `get_status` mit Hub/Slots.

## 6. Teil B – serial485-Patch

Beide Fixes sind in `nachbau/serial485.py` enthalten.

**B.1 `_done` wächst unbegrenzt:** `send_queue_process`
und `cmd_send_data_with_response` legten jede Antwort in `_done` ab – direkt
gedrainte Einträge (`add_send_data` + `send_queue_process`) und verwaiste
Fremd-Antworten (Thread crashed) blieben ewig. Fix: `_done_time` pro Eintrag,
`_store_done_locked()` (TTL 60 s + harte Grenze 128, älteste IDs zuerst raus),
`_take_done_locked()` (verfallene gilt als fehlend), öffentlich
`cleanup_done()` → `(entfernt, übrig)`, `stats()` meldet `done_len`.
Alte Aufrufer unverändert (gleiche Rückgaben bei kleinen Mengen).

**B.2 Bg-Thread schnappt `get_response` Antworten weg:**
Lücke zwischen `raw_send` und `get_response` – bg erwarb `_lock` zuerst, las
den Antwort-Frame und dispatchte ihn, `get_response` lief ins Timeout. Fix:
`raw_send(..., expect_response=True [Default], expect_timeout, expect_fun,
expect_addr)` registriert atomar mit dem Schreiben eine Erwartung
(`_pending_expect`); bg parkt passende Frames in `_resp_queue` statt zu
dispatchen; `get_response` prüft die Queue zuerst (dann seriell wie bisher
inkl. Dispatch). `expect_response=False` = altes Verhalten (für echtes
Fire-and-forget, z.B. `cmd_485_send_data`). Alles unter `_lock`, TTLs
(2 s Erwartung / 5 s Queue, max. 32) gegen Verwaisten.

`cd nachbau && python3 -B serial485.py` → `SELBSTTEST: 72/72 ok`
(65 bestehende + 7 neue):

- B.1 `_done` begrenzt (MAX 128 bei 150 Einträgen), älteste verdrängt/neueste
  da, `cleanup_done` räumt künstlich gealterte ab, Einzel-Call danach ok.
- B.2 `get_response` findet geparkte Antwort trotz 120-ms-Lücke (kein Timeout,
  genau einmal dispatcht); ohne Erwartung dispatcht bg wie bisher (Negativprobe).

## 7. Grundlagen

- `nachbau/box.py`, `cfsseq.py`, `boxmini.py`, `auto_addr.py`, `serial485.py`
- Protokollanalyse des RS485-Busses (Funktionstabelle, Live-Gegenprobe zu
  Retries/Timeouts, 0x0d, 0x10/0x11/0x04/0x08, 0x0a, Schutzmechanismen,
  Bursts, Auto-Addr, Kanal A) – nicht Teil dieses Repos
- `pi-host/k2_transport.py` (Rs485-Schnittstelle + nachbau-Pfadsuche),
  `pi-host/k2_motor_init.py` (Stilvorbild: testbare Logik, allow_write,
  Klipper-Hülle, G-Codes, FakePrinter-Tests)
- Stock-Konfiguration des Druckers: `box.cfg` (BOX_LOAD/QUIT-Ketten,
  M8200-Kette, CR_BOX_*, BOX_CUT_MATERIAL, `[box]`-Parameter),
  `gcode_macro.cfg` (BOX_*-Makroverdrahtung)

## 8. Filamentwechsel T0–T15 / M8200-Kette (Ergänzung 16.10.2026 abends)

> **ENTWURF – am Gerät UNGETESTET.** Offline: `test_k2_box.py` 20/20,
> Batch gegen Upstream 9cb9975. Bedienung/Inbetriebnahme: `config-pi/README.md`.

### 8.1 Belegter Stock-Ablauf

| Quelle | Beleg |
|---|---|
| Stock-`box.cfg` (Stand 16.10.2026) | `[box]`-Werte (pre_cut 10/200, cut_pos_y 200, cut_velocity 30000, Tn_retrude -10 @600, Tn_extrude_temp 220, Tn_extrude 140 @360, buffer_empty_len 30, extrude_pos 133/378, clean_* 135/160/378, 160/170/374, clean_velocity 12000, box_need_clean_length 70, safe_pos 225/345, check_cut_pos_x -9.5…-5.5, switch_pin !nozzle_mcu:PB9); M8200 P/C/R/L/W/F/O → CR_BOX_PRE_OPT/CUT/RETRUDE/EXTRUDE/WASTE/FLUSH/END_OPT; TNN `T<I/4+1><ABCD[I%4]>`; kommentiertes T0-Beispiel (Z+0.4, `G0 X10 Y200 F12000`, ACCEL 5000, M8200 C, `G0 Y345 / X139 / Y378 / X133`, M8200 R, M104, M8200 L I0, `G0 E90 F600`, Blech X160/X135, Kreise `G2 I4`/`G3 I-4` bei Y374, 3× Schütteln 160↔133, `G0 X160 / Y345`, M8200 O, Z zurück) |
| Stock-`printer.cfg` SAVE_CONFIG | `[box] cut_pos_x = -7.80` (Kalibrierung 16.10.2026) – UUID-Zeilen dort bewusst NICHT übernommen |
| Stock-`gcode_macro.cfg` | `LOAD_MATERIAL_CLOSE_FAN2`/`RESTORE_FAN2` (fan2 aus/zurück), `QUIT_MATERIAL_RETRUDE_MATERIAL` (`G91 / G0 E-10 F360 / G90`); T0…T15 sind KEINE Makros (kommen aus `box_wrapper.so`) |
| Live-Mitschnitt `klippy.log` 12.10.2026 00:24:49–00:26:35 | vollständiger Wechsel T1A (Unload + Load): Heizen 220 → „[box] cut sensor detected“ → „cut to return OK“ (3 s, **kein** Box-/Motor-Frame) → 0x08 [01], 0x08 [00], 0x04 [00 01] → **4× 0x11 [01 00], nach p0 1–3 je „extrude = -10.0, velocity (600,)“** → 0x11 [01 01] (20 s) → Einzug 0x04/0x0f/0x08/0x10 m0/m4/m5×5 (alle 2 s)/m6 (aus Sensor-Ereignis) → `extrude_process_stage7 tnn: T1A` → m7 p3 → 0x05 → 0x04 [01 00] → 0x0f [00] → Spülen „length: [90, 70]“, `check_and_extrude` 18 + 72 mm (240 °C, ×1,2) und 14 + 56 mm (220 °C) |
| Live-Mitschnitt `klippy.log` 13.10.2026 02:30–02:31 | zweiter Einzug: gleiche Folge, **„timeout = 90“** Countdown während m5 |
| Live-Mitschnitt Cut-Kalibrierung 16.10.2026 22:02 | Cut + Retrude 16.10.: Reihenfolge 0x04 [00 01] → 0x08 [00] → p0 (+E-10, hier F350 aus Material) ×3 → p0 → p1, danach „filament_sensor false“ |
| Live-Messung Kanal A 16.10.2026 | Preload/Einzug/Rückzug-Zeiten (m0 4,4 s, p1 22,4 s, op2 31 s) |
| Protokollanalyse „M8200-Sequenzen“ | Load P→L→F→O, Unload P→C→R→O, `CR_BOX_PRE_OPT` Pflicht vor EXTRUDE (key60), I=n → TnX; Box blockiert Einzüge, solange ein Slot im Hub steckt |
| Firmware-Analyse CFS, `nachbau/cfsseq.py` | 0x10-Modi (m7 = Odometer > p mm in 300 ms, sonst 0x0e), 0x11 p0/p1, 0x04 [00 01] löscht ERROR |

**Aufteilung:** Box (RS485) = Rückzug 0x11, Einzug 0x10 + 0x04/0x0f/0x08/0x05,
Zustand 0x0a. Drucker = Z-Hop, Schneiden (Kopf drückt den Hebel gegen den
Anschlag), Schacht, Heizen, Extruder-Rückzug zwischen den p0, Stage 7
(Extruder greift, während die Box mit m7 prüft), Spülen, Abstreifen,
Lüfter, Rückfahrt.

### 8.2 Neue G-Codes (`k2_box.py`, Präfix `KTWO_`, Upstream-Namensregel geprüft)

| G-Code | Stock | Inhalt |
|---|---|---|
| `KTWO_BOX_PRE_OPT [TNN=] [Z_HOP=] [SOURCE=T]` | M8200 P | Kette starten (`SOURCE=T` aus `_KTWO_TOOL_CHANGE` = nachholbarer T<n>-Wechsel, §11); schon geladen (Hub-Sensor + Kopfsensor) → Rest überspringen; Kopfsensor sperren (`SET_FILAMENT_SENSOR … ENABLE=0`, sonst pausiert Upstream beim Rückzug); `SAVE_GCODE_STATE`; Z-Hop 0,4 |
| `KTWO_BOX_CUT` | M8200 C | `G0 X10 Y200 F12000`, `ACCEL=5000`, `G0 X-7.8 Y200 F30000`, `G4 P100`, `G0 X10`, Accel zurück, `M400`; optional Schneidhebel-Sensor (`cut_sensor`); in der T<n>-Kette bei Kopf sicher leer + Hub leer übersprungen (§16, A1) |
| `KTWO_BOX_GO_EXTRUDE_POS` / `KTWO_BOX_SAFE_POS` | BOX_GO_TO_EXTRUDE_POS / BOX_MOVE_TO_SAFE_POS | T0-Beispiel Schritt 6 / 11 |
| `KTWO_BOX_RETRUDE [SLOT=] [LENGTH=] [FORCE=1]` | M8200 R (ohne E) | Schacht, M109 (nur mit Filament am Kopf), Box bereit abwarten, Slot aus Hub-Sensor, `do_retrude(after_p0=…)`: p0 → E-10 (F = Spülgeschwindigkeit des Düsenmaterials mit DB, sonst `tn_retrude_velocity` 600, §16 A3) → … → p1 |
| `KTWO_BOX_BREAK_RETRUDE LENGTH=<mm>` | M8200 R E<x> (x≠0) | seit 22.10.2026 (§19): **kein** Box-Rückzug – Stock `break_box_extruder_retrude`: 0x04 [00 01] → 0x11 [m 00] → 0x04 [00 01], dann `G91` / `G1 E<x> F300` / `G90` / `M400`; Slot aus Hub-Sensor, Hub leer → nur Meldung; keine Fahrt, kein Heizen. Creality Print 7 schickt `M8200 R E-30` vor dem Schnitt |
| `KTWO_BOX_EXTRUDE TNN=\|SLOT=` | M8200 L I | Hub frei/gleicher Slot prüfen, Kopf leer prüfen, M109, `do_extrude(sensor_fn, stage7_fn)`: m5 alle 2 s bis Kopfsensor (max 90 s), m6, Extruder `G1 E30 F360` (ohne M400), 2 s, m7 p3, PRINT |
| `KTWO_BOX_FLUSH [TNN=] [LENGTH=] [VELOCITY=] [TEMP=\|BASE_TEMP=]` | M8200 F | seit 17.10. abends wie Stock (§12): Menge farbabhängig (unbekannt 90 + 70 mm), je 20 % v / 80 % 1,2·v, Blasen + Blech je Abschnitt, Silikon-Kreise nur am Ende |
| `KTWO_BOX_WIPE`, `KTWO_BOX_WASTE` | T0-Bsp. 10.1–10.3, M8200 W | Abstreifen; WASTE = nur Hinweis (Stock-Kamera fehlt) |
| `KTWO_BOX_END_OPT [RETURN=0\|1]` | M8200 O | Schacht verlassen, ggf. Rückfahrt XY + Z, Zieltemperatur zurück, Zustand/Kopfsensor zurück |
| `KTWO_BOX_WAIT [TIMEOUT=]` | BOX_MODE_WAIT | 0x0a bis IDLE/PRINT, ERROR → Fehler |
| `KTWO_BOX_ERROR_CLEAR [HEAD_LOADED=1]` / `KTWO_BOX_RESUME_FIX` | BOX_ERROR_CLEAR | Fehler quittieren (+ 0x04 [00 01]); `HEAD_LOADED=1` = Kopf von Hand als geladen bestätigt (nur ohne Kopfsensor-Zustand, §11) / Z-Hop nach Pause zurücknehmen (aus RESUME) |
| `KTWO_BOX_RESUME_CHECK [MOVE=0\|1] [TEMP=]` | BOX_ERROR_RESUME_PROCESS | aus RESUME nach einem abgebrochenen Wechsel: Kopfsensor leer → RESUME gesperrt bzw. Nachholen (`resume_redo_change`, §11) |

Controller-Erweiterungen (wiederverwendet, Frame-Folgen unverändert):
`do_extrude(sensor_fn, sensor_timeout, poll_interval, stage7_fn, stage7_delay)`,
`do_retrude(after_p0)`, `do_cut()` (= G-Code-Plan, sendet nichts),
`wait_box_ready()`, `hub_mask()`, `loaded_slot`; reine Bausteine
`gcode_cut/go_extrude_pos/safe_pos/wipe/flush_segment/extruder_move`,
`flush_segments`, `retrude_extruder_steps`, `tnn_from_index`, `parse_tnn`.
Neue Optionen (Stock-Namen, Defaults = box.cfg): s. `CHANGE_DEFAULTS` und
`config-pi/k2-module.cfg`.

### 8.3 Sicherheitslogik

- Box-Frames nur mit `allow_write`, Kopf/Extruder/Heizen nur mit
  `allow_move` (beide Default False → Plan-Text). `allow_move` ohne
  `allow_write` = Trockenlauf mit Bewegung (nur außerhalb eines Drucks).
- Im Druck (print_stats printing/paused) braucht jeder Schritt BEIDE
  Freigaben, sonst Pause. T0 ohne Freigaben = Einfarbbetrieb (Makro).
- Fehler im Druck → `PAUSE`, Meldung `!! …`, Folgeschritte der Kette
  übersprungen, END_OPT räumt nur nicht-mechanisch auf; `RESUME` gesperrt
  bis `KTWO_BOX_ERROR_CLEAR`; danach senkt `KTWO_BOX_RESUME_FIX` den Z-Hop.
  Außerhalb eines Drucks → G-Code-Fehler + Aufräumen. Nie „Internal
  error“/Shutdown (alle Ausnahmen werden abgefangen).
- Seit 17.10.2026 (§10): Abbruch im laufenden Druck → `PAUSE
  CFS_RETURN_X/Y/Z=<Druckposition vor dem Wechsel>`; RESUME fährt dorthin
  statt zur Fehlerposition (Schneidhebel/Schacht).
- Seit 17.10.2026 spät (§11): RESUME nach einem Abbruch ist GESPERRT,
  solange der Kopf-Filamentsensor leer (oder unbekannt) meldet; optional
  Nachholen des T<n>-Wechsels (`resume_redo_change`, Default AUS).
  Reihenfolge von Hand: `KTWO_BOX_ERROR_CLEAR` → `T<n>` → `RESUME`.
- Box 2–4 (T4–T15) abgelehnt, solange nicht konfiguriert; Hub durch anderen
  Slot belegt → Ablehnung vor jedem Frame; Kopfsensor zeigt Filament vor
  dem Einzug → Ablehnung.
- `ReactorBusProxy`: lange Box-Antworten blockieren den Reactor nicht
  (Heizungs-PWM/MCU-Takt laufen weiter); Poll-Timer pausiert bei `busy`.
- Schnittschutz (22.10.2026): Nach dem Spülen (`KTWO_BOX_FLUSH`, auch
  extern) und ab Druckbeginn (`KTWO_BOX_PRINT_START` bzw.
  `KTWO_BOX_CANCEL_RESET START=1`) lehnen Einzel-`KTWO_BOX_RETRUDE` und
  `KTWO_BOX_UNLOAD` AUSSERHALB einer Kette ab, bis `KTWO_BOX_CUT` wirklich
  geschnitten hat – nichts gesendet, keine Bewegung; im Druck → PAUSE wie
  jede andere Ablehnung. Grund: Live lief `KTWO_BOX_RETRUDE` direkt nach dem
  Spülen in Box-Status 0x19 (Pull-back: Motorblock > 10 s), weil das lange
  geschmolzene Ende beim Rückzug eine Verdickung bildet; Stock schneidet vor
  jedem Rückzug. Frei geben: echter Schnitt, erfolgreicher Rückzug/Unload,
  erfolgreicher Einzug (frisches Ende – Rückzug ohne Schnitt live belegt) und
  Restmaterial-Ausspülen (Kopf leer). `FORCE=1` übergeht mit Warnung. In der
  Kette (CUT vor RETRUDE) und ohne `allow_write` (Plan) ohne Wirkung. Merker
  nur im Speicher (nach Neustart frei); sichtbar als `cut_needed` in
  `printer["k2_box"]`. Reihenfolge von Hand nach dem Spülen:
  `KTWO_BOX_CUT` → `KTWO_BOX_RETRUDE SLOT=<Slot>`.

### 8.4 Tests und Batch

`test_k2_box.py` +11 Prüfungen (20/20): Bausteine == box.cfg/Live (Schnitt,
Schacht, Kreise, 18/72 + 14/56, 3× −10, TNN-Mapping, Optionsgrenzen),
Einzug bis Kopfsensor + Stage 7 (0x0e ohne Extruder, Timeout → 0x04 IDLE),
Rückzug mit Haken (Frames == `build_retrude_sequence`), Box-Wartebedingung,
volle Kette T1B in Reihenfolge (Hop → Schnitt → Schacht → p0/E-10 ×3 → p1 →
m6 → E30 → m7 → PRINT → Spülen/Abstreifen → Rückfahrt), Sperren, Fehler im
Druck (0x0c → Pause) und ohne Druck, Proxy, Makros T0–T15/M8200/RESUME per
Jinja2 gerendert, Python-3.9-Syntax.

Batch gegen Upstream-Klipper: Auslieferung
ohne Freigaben → 0 Schritte, nur Pläne, EXIT 0; `allow_move` → dekodierte
MCU-Schritte bestätigen die Bahn (89/89 Ecken, Anschlag X −7,800, Z 20,4
beim Schnitt, Extruder −30/+120/+70 mit 10/6 mm/s nur im Schacht); beide
Freigaben ohne Port → `RETRUDE fehlgeschlagen: [k2_485] Batch-Modus …`,
kein Shutdown; T5 → „Box 2 … nur Box 1“.

### 8.5 ANNAHMEN (Wechsel)

Stage-7-Extruderlänge (`buffer_empty_len` 30) + `stage7_delay` 2 s,
Rückweg-Geschwindigkeit nach dem Schnitt, `cut_dwell` 0,1 s (Input-Shaper),
Bedeutung des Schneidhebel-Sensors, ~~Material-Temperaturen (hier
`max(Ziel, 220)`), Spül-Erhöhung auf Max-Temperatur/×1,2 entfällt,
`box_wait_timeout` 200 s, Lüfter fan0/„Blasen“ beim Spülen nicht
nachgebaut~~ (seit 17.10.2026 abends umgesetzt, §12), Verhalten bei
Sensor-Timeout (0x04 IDLE), `safe_pos_x` 225 ungenutzt, Box 2–4, Z nach
RESUME mitten im Wechsel. Bedienung: `config-pi/README.md`.

## 9. Auto-Nachladen bei Runout (A7, Ergänzung 17.10.2026)

> **ENTWURF – am Gerät UNGETESTET.** Offline: `test_k2_box.py` 25/25 (5
> Runout-Prüfungen), Batch gegen Upstream 9cb9975. Bedienung:
> `config-pi/README.md`.

- Stock: `pause_on_runout: true` + `runout_gcode` (E30 F600 wenn heiß +
  `BOX_CHECK_MATERIAL_REFILL` aus `box_wrapper.so`; Strings `auto_refill`,
  `same_material_list`/`same_tnn_list`, „no tray with ingredients found“).
  Kein echter Runout in den Live-Logs → Frame-Folge unbelegt.
- Pi: `printer.cfg` `runout_gcode: KTWO_BOX_RUNOUT`, `pause_on_runout: True`
  (Upstream pausiert vor dem runout_gcode). `KTWO_BOX_RUNOUT` prüft lesend
  (0x0a PRINT/0x50, 0x08 [01] Hub leer, 0x08 [00] Einlauf leer, 0x02 [0f]
  RFID, 0x03 [0f]), wählt den Ersatz (`select_runout_slot`: Gleich-Material-
  Gruppe oder RFID gleiche mat_id + Farbe, Filament am Einlauf, zyklisch ab
  dem Nachbarslot) und fährt PRE_OPT (Z_HOP=0) → Schacht/Heizen/E30 F600 →
  EXTRUDE → WASTE → FLUSH → END_OPT RETURN=1 → RESUME.
- Neue Befehle `KTWO_BOX_RUNOUT`, `KTWO_BOX_AUTO_REFILL`,
  `KTWO_BOX_SAME_MATERIAL`; Optionen `runout_refill` (False),
  `runout_require_color` (True), `runout_resume` (True),
  `runout_tail_length`/`_velocity` (30/600); Status `auto_refill`,
  `same_material`, `runout_last`.
- Sicherheit: nur mit `runout_refill` + `allow_write` + `allow_move`; jeder
  Abbruch lässt den Druck pausiert (Meldung), Kettenfehler sperren RESUME
  bis `KTWO_BOX_ERROR_CLEAR`, nie Shutdown. `_print_active()` zählt jetzt
  auch `pause_resume.is_paused`.
- Nebenbei (A6): `cut_pos_x` steht nicht mehr in `k2-module.cfg` (SAVE_CONFIG-
  Konflikt); Default −7.80 aus `CHANGE_DEFAULTS` bzw. SAVE_CONFIG-Block.

## 10. RESUME nach abgebrochenem Wechsel im Schneidbereich (Ergänzung 17.10.2026)

> **ENTWURF – am Gerät UNGETESTET.** Offline: `test_k2_box.py` 28/28 (3 neue
> Prüfungen), Batch gegen Upstream 9cb9975 (7 Läufe, 83/83).
> Bedienung: `config-pi/README.md`.

- Problem: Upstream-`PAUSE_STATE` hielt nach einem Wechsel-Abbruch die
  Fehlerposition (z. B. X −7,80/Y200 am Schneidhebel); `RESUME_BASE` fuhr
  gerade dorthin zurück (Hebel erneut betätigt, Druck an falscher Stelle).
- `_fail` (Pause wird durch den Fehler ausgelöst): `PAUSE CFS_RETURN_X/Y/Z`
  = `start_pos` der Kette (vor dem Z-Hop) bzw. Position vor einem
  Einzelschritt ohne Kette (`_step_start`); kein Ziel, wenn der Kopf noch
  dort steht oder die Kette in einer Pause begann. `pending_resume_z` nur
  noch, wenn die Pause durch den Fehler entsteht.
- `KTWO_BOX_RESUME_FIX CLEAR=1` (aus dem PAUSE-Makro): Z-Korrektur verwerfen
  (Z steckt im Rückfahrziel; keine Altlast einer per CANCEL beendeten Pause).
- `gcode_leave_cut_zone(c, pos)`: Kopf bei X < `pre_cut_pos_x` → `G0 X10`
  + `M400` vor jeder Schachtfahrt (`_go_extrude_pos`), Stock box.cfg:13.
- `cmd_pre_opt`: `paused_at_start`; `_keep_start_pos` nur für ein `T<n>` in
  derselben Pause (Fix: sonst Rückfahrt zur Position eines früheren
  Wechsels). `cmd_end_opt` in einer Pause: Z nicht unter die Pausenhöhe.
- Makros (`macros.cfg`): PAUSE übernimmt das Ziel (`pause_rebase`), parkt
  vom Hebel nur über X, aus dem Schacht über X160 → Y345; RESUME sperrt vor
  jeder Bewegung (Ziel am Hebel, fehlende Freigabe bei CFS-Abbruch), fährt
  vom Hebel nur in X, und `_PI_RESUME_REBASE` setzt `PAUSE_STATE` an der
  Druckposition neu (`RESTORE_GCODE_STATE` + `SAVE_GCODE_STATE`, ohne Fahrt)
  → `RESUME_BASE` = Nullfahrt, G-Code-E wie vor dem Wechsel.

### 10.1 Bewusste Abweichung vom Stock (Ergänzung 17.10.2026 spät)

Stock-Fehlerpfad (aus der Firmware-Analyse von `box_wrapper.so`):
`BoxError.handle_event` nimmt den Z-Hop an der Fehlerposition zurück
(`G0 Z-{move_z}`), fährt `BOX_MOVE_TO_SAFE_POS` (**erst `G0 Y345`, dann
`G0 X225`**) und ruft dann `PAUSE` → PAUSE_STATE = **X225/Y345 auf Druck-Z**;
`RESUME_BASE` fährt dorthin zurück (ANNAHME: wie Upstream). Der Pi weicht
bewusst ab:

| Punkt | Stock | Pi | Grund |
|---|---|---|---|
| Rückfahrziel | X225/Y345, Druck-Z (gerade Fahrt) | Druckposition vor dem Wechsel, X/Y in Pausenhöhe, Z senkrecht | Stock senkt bei X225/Y345 auf Druck-Z, danach fährt der Slicer auf Druck-Z zum Objekt (vermutet) |
| Schneidbereich | kein Sonderfall, Y zuerst | X < `pre_cut_pos_x` nur in X | einziger Beleg `box.cfg:13` „垂直撞刀“; Y am Hebel unbelegt |
| Schacht | Z zurück auf Druck-Z, dann Y345/X225 | Z-Hub, X160 → Y345 (T0-Beispiel 11), Park | nie auf Druck-Z quer über das Bett |
| Nachholen | automatisch (§11) | gesperrt; optional `resume_redo_change` | §11 |

## 11. RESUME nach abgebrochenem Wechsel: Kopfsensor-Sperre und Nachholen (Ergänzung 17.10.2026 spät)

> **ENTWURF – am Gerät UNGETESTET.** Offline: `test_k2_box.py` 34/34 (6 neue
> Prüfungen), Batch gegen Upstream-Klipper (11 Läufe, 151/151), Gesamtlauf
> unverändert. Bedienung: `config-pi/README.md`. Alles gesperrt
> ausgeliefert (`allow_write`/`allow_move`/`resume_redo_change` False).

**Stock (aus der Firmware-Analyse):** `BoxSave.recode_err` speichert `resume_tnn` = Ziel-TNN nur für Fehler im T<n>-Pfad
(nicht `macro_error_list` = M8200-Kette). `RESUME` → `BOX_ERROR_RESUME_PROCESS`
→ `error_resume_process`: CFS an, pausiert, `resume_tnn` gesetzt;
`clear_err()`; Kopfsensor belegt → Ende ohne Wechsel (live `12:88007`);
sonst `go_to_extrude_pos` → `box_retrude_material` → `box_extrude_material`
→ `extruder_extrude` → `material_change_flush`; Fehler → `resume_err`,
`move_to_safe_pos`. Danach `RESUME_EXTERNAL` (Prime, Reinigung) → `RESUME_BASE`.

**Pi:**

- `_fail` merkt den Abbruch (`_record_abort`: Schritt, Ziel-TNN, Herkunft;
  Status `change_aborted`, `resume_tnn`, `head_filament`,
  `resume_redo_change`). Herkunft `SOURCE=T` setzt nur `_KTWO_TOOL_CHANGE`
  (T<n>-Befehl) – nur dieser Wechsel ist nachholbar (wie Stock). Merker lebt
  bis zum Ende eines RESUME (`KTWO_BOX_RESUME_FIX`) oder bis zu einer neuen
  Pause, die nicht aus `_fail` stammt (`RESUME_FIX CLEAR=1`,
  `_pause_from_fail`).
- RESUME-Makro: bei `change_aborted` zuerst `KTWO_BOX_RESUME_CHECK MOVE=<1|0>`
  (vor `_PI_RESUME_PREPARE`, vor jeder Bewegung). Kopfsensor über
  Upstream-`filament_switch_sensor` (`filament_detected`, auch bei
  ENABLE=0 aktuell):
  - **Filament** → RESUME frei (wie Stock: kein Wechsel, alter Faden).
  - **leer** → `RESUME gesperrt nach abgebrochenem CFS-Wechsel (…): …
    meldet KEIN Filament … Druck bleibt PAUSIERT. In der Pause nachholen:
    T1 (lädt T1B), dann RESUME.` (gcode-Fehler, RESUME_BASE läuft nicht).
  - **unbekannt** (`filament_sensor` leer/Abschnitt fehlt) → gesperrt
    (sicherer Default), Freigabe nur `KTWO_BOX_ERROR_CLEAR HEAD_LOADED=1`
    (abgelehnt, wenn der Sensor „leer“ meldet).
- **Nachholen** (`resume_redo_change: True` + `allow_write` + `allow_move`
  + `MOVE=1` + Herkunft T + Ziel bekannt + Kette geschlossen +
  referenziert): `PRE_OPT TNN=<Ziel> Z_HOP=0 SOURCE=T` → Schacht + `M400` +
  Heizen (max(Drucktemperatur vor der Pause, Material-DB bzw. 220); EXTRUDE/
  FLUSH mit `BASE_TEMP=`, §12) → `RETRUDE` (nur bei
  belegtem Hub) → `EXTRUDE` → `WASTE` → `FLUSH` → `END_OPT RETURN=0`; kein
  Schnitt; danach muss der Kopfsensor Filament melden; weiter mit dem
  normalen RESUME (Schacht/Vorextrusion/Rückfahrt). Fehler → `chain_error`,
  RESUME gesperrt, keine Rückfahrt, keine zweite Pause (= Stock
  `resume_err`).
- Abweichungen: Fehler quittiert der Pi nicht im RESUME (Stock
  `clear_err()`), sondern weiter per `KTWO_BOX_ERROR_CLEAR`; Nachholen
  Default AUS (Stock immer); Kopfsensor unbekannt → gesperrt.
- Korrigiert: Anleitung in `_fail`/RESUME-Sperre jetzt `KTWO_BOX_ERROR_CLEAR`
  → `T<n>` → `RESUME` (ein `T<n>` bei offenem Fehler lehnt PRE_OPT ab).
- Neue Option `resume_redo_change` (Default False, `k2-module.cfg`), neuer
  Befehl `KTWO_BOX_RESUME_CHECK`, `KTWO_BOX_ERROR_CLEAR HEAD_LOADED=1`,
  `KTWO_BOX_PRE_OPT … SOURCE=`.
- **Temperatur (Fix 17.10.2026 spät):** hatte der abgebrochene
  Wechsel schon auf die Wechsel-Temperatur geheizt (`M109 max(Ziel, 220)`),
  übergibt `_fail` der neuen Pause `CFS_TEMP=<Druckziel vor dem Aufheizen>`;
  das PAUSE-Makro merkt es als `hotend_temp` (statt `extruder.target`), RESUME
  heizt darauf. Kein `CFS_TEMP` bei Abbruch vor dem Heizen, Druckziel
  ≥ Wechsel-Temperatur oder Ziel vorher 0/unbekannt (dann wie bisher
  aktuelles Ziel + Meldung). Abbruch einer Wiederholung IN der Pause: keine
  neue PAUSE (`hotend_temp` der ersten bleibt), Düse zurück auf das Ziel vor
  diesem Wechsel (Pausentemperatur).

## 12. Spülen wie Stock + Material-Datenbank + Preload-Schutz (Ergänzung 17.10.2026 abends)

> **ENTWURF – am Gerät UNGETESTET.** Bedienung: `config-pi/README.md`.
> Rechnung über `nachbau/flush.py`
> und `nachbau/material_db.py` (in `extras/k2_nachbau/`, weich importiert:
> fehlen sie, gilt das bisherige Verhalten). Offline: `test_k2_box.py` 47/47.

### 12.1 Neue Optionen `[k2_box]` (Defaults; alle in `k2-module.cfg` kommentiert)

| Option | Default | Wirkung / Fallback |
|---|---|---|
| `material_database` | leer (aus) | Pfad zur Creality-`material_database.json` (Zielpfad von `install.sh`: `~/printer_data/config/k2/material_database.json`, nicht im Repo). Leer/fehlt/kaputt → bisheriges Verhalten + Meldung beim Connect, kein Config-Fehler |
| `flush_mode` | `matrix` (seit 18.10.2026; bis dahin `color`) | `color` = Stock-Spülmenge aus alter/neuer RFID-Farbe (unbekannt → `Tn_extrude` 140 → 90 + 70 mm = bisher), `LENGTH=` über `cal_flush_list`; `fixed` = immer `box_first + box_need`, `LENGTH=` in 90er-Stücken (Stand bis 17.10.); **`matrix`** (§12.4, Stock) = ab dem ersten Wechsel im laufenden Druck die Slicer-Spülmatrix der G-Code-Datei, erster Einzug eines Drucks und sonst wie `color` |
| `remain_material_file` | `~/printer_data/config/k2/remain_material.json` | **neu (§12.4):** Düsen-Material über Neustarts (Stock `remain_material_data.json`, gleiches Format). Leer = aus (nur im Speicher, Stand bis 17.10.). Tilde wird aufgelöst |
| `slot_material_file` | `~/printer_data/config/k2/slot_material.json` | **neu (§15, 18.10.2026):** Slot-Handwerte von `KTWO_BOX_SLOT_SET` (Stock `tn_data.json`). Leer = aus (nur Speicher) |
| `flush_clean` | `stock` | `stock` = Blech zwischen den Abschnitten, Silikon-Kreise + Schacht verlassen nur am Ende; `legacy` = volles Abstreifen (`gcode_wipe`) nach jedem Abschnitt |
| `flush_wipe_offset` | 3 | Kreisstart-Versatz `monotonic() % 3` mm (Stock `clean_offset_pos`); 0 = immer dieselbe Stelle (Tests) |
| `wipe_minimum_cruise_ratio` | 0 | Upstream-Ersatz für Stock `ACCEL_TO_DECEL=ACCEL` (9cb9975 kennt nur `MINIMUM_CRUISE_RATIO`); danach alter Wert, nur wenn lesbar |
| `flush_blow` / `flush_blow_fan` | `True` / `fan0` | Stock `M106 P0 S50` zu Beginn, je Abschnitt Lüfter `flush_blow_time` s voll, am Ende Lüfter (+ `<fan>_en`) wie vorher; ohne `[output_pin <fan>]` übersprungen |
| `flush_fan_start` / `flush_blow_time` | 50 / 5 s | Stock-Werte |
| `flush_velocity_factor` | 1,2 | 80 %-Teil jedes Abschnitts außer dem letzten; 1 = eine Geschwindigkeit (bisher) |
| `max_temp_flush_times` | 3 | Stock: Index des letzten Abschnitts (Faktor 1, Temperatur zurück) |
| `box_need_clean_length_max`, `nozzle_volume`, `flush_multiplier`, `diff_material_type_min_flush_len`, `baseline_nozzle_diameter` | 100, 183, 1, 370, 0,4 | Stock-`[box]`-Defaults |
| `flush_clog_check` / `flush_clog_min_mm` | `False` / 10 | Stock key845 (Messrad-Summe < 10 mm) – hier nur WARNUNG |
| `box_wait_timeout` | **380** (vorher 200) | = `preload_force_timeout(1 Slot)` |
| `Tn_extrude` | 140 | jetzt genutzt: Stock-Fallback der Spülmenge |
| `ending_material_purge`, `max_tube_length`, `ending_material_timeout`, `end_print_unload` | True, 3000, 3600, **True** (seit 18.10., vorher False) | Restmaterial / Druckende – §12.5 |

### 12.2 Verhalten

- **Temperaturen** (`_change_temp`): `TEMP=` fest; sonst
  `max(Basis, Material-Temperatur)` – Basis = `BASE_TEMP=` (Nachholen/
  Runout: Drucktemperatur vor der Pause) bzw. Extruder-Ziel; Material =
  `kvParam.nozzle_temperature` der DB, Schlüssel = RFID-`material_type` des
  Slots (Rückzug: Material in der Düse, Einzug: Ziel). Ohne DB/unbekannt:
  `Tn_extrude_temp` 220 (+ `HINWEIS` mit DB). Kappung `[extruder] max_temp − 10`.
  Kommentar korrigiert: die Temperatur steht nicht im RFID-Tag.
- **Spülen** (`cmd_flush`): Menge `plan_flush_lengths`, Geschwindigkeit
  `min(360, vol/2,4·60)·Düse/0,4` (ohne DB `Tn_extrude_velocity`),
  Temperatur `max(Basis, T_alt, T_neu)`, mit DB bei `T_alt ≥ T_neu` Spitze
  `max(maxTemp)` (`M104` + `TEMPERATURE_WAIT MINIMUM=max−5`, zurück im
  letzten Abschnitt), Abschnitte `flush_segment_lines`, Blasen
  `gcode_blow`, Reinigung `gcode_nozzle_clean` (G2/G3 mit `[gcode_arcs]`,
  sonst 16-Eck). Nach ausgeführtem Spülen `remain_material` = Ziel
  (Typ/Farbe/Slot; mit `remain_material_file` auch nach Neustart bekannt,
  §12.4, ohne → unbekannt → 90 + 70 mm).
- **RESUME-Vorextrusion**: Status `prime_speed` = `vol/2,4·60·0,8` des
  Materials in der Düse (unbekannt 288), 0 ohne DB → `_PI_RESUME_PRIME`
  nimmt dann `resume_prime_speed` 460.
- **Stage-7-Greifen** (Pi-Erweiterung): mit DB
  `min(Tn_extrude_velocity, vol/2,4·60)` (TPU 50 mm/min).
- **Preload** (`KTWO_BOX_PRELOAD … [FORCE=1]`): op 2/3 nur bei
  leerem Kopfsensor, Druck/Pause nur mit `FORCE=1`, Maske 0 bei op 2/3
  abgelehnt (`preload_block_reason`) – gesperrt = nichts gesendet.
  Die Preload-Freigabe `0x0d [0f 01]` beim Connect ist bewusst NICHT eingebaut
  – offener Gerätetest.
- **Status** neu: `material_db`, `material_db_info`, `remain_material`,
  `prime_speed`, `flush_mode`, `flush_last`; ab §12.4 zusätzlich
  `remain_material_file`, `remain_material_load`, `remain_material_info`;
  ab 18.10.2026 `flush_print_tnn` (Slot des letzten Spülens im laufenden
  Druck, `''` = nächstes Spülen = erster Einzug → Farbweg).

### 12.3 Offen / am Gerät

- Spülbahn am Gerät (Blech ohne Kreise zwischen den Abschnitten, Kreise am
  Ende, 5-s-Blasen), DB-Temperaturen (PETG 250, PLA→PLA Spitze 240).
- ~~`remain_material` wird nicht persistiert~~ → §12.4 (`remain_material_file`).
- ~~Slicer-Spülmatrix nicht umgesetzt~~ → §12.4 (`flush_mode: matrix`, Default seit 18.10.2026).
- `KTWO_BOX_WIPE`/`_PI_NOZZLE_CLEAN` (G28/START_PRINT/G29/RESUME) unverändert
  (erstes X 160 statt Stock 170, ohne Versatz) – Bahn bleibt == `_PI_CLEAN_PATH`.
- Preload-Freigabe `[0f 01]` nach Box-Reset am Gerät prüfen.

### 12.4 Düsen-Material persistent, Slicer-Spülmatrix, M600 (Ergänzung 17.10.2026)

> **ENTWURF – am Gerät UNGETESTET.** Rechnung
> `nachbau/flush.py` (`gcode_matrix_flush_len`, `finish_flush_len`).
> Offline: `test_k2_box.py` 52/52; Batch gegen Upstream-Klipper 26/26.

**`remain_material_file`** (Default `~/printer_data/config/k2/remain_material.json`, leer = aus)

- Format Stock-kompatibel: `{"remain_material": {"color": "0RRGGBB"|null,
  "type": "<id>"|null}}`; k2_box schreibt zusätzlich `"tnn": "T1A"` (Slot
  des gespülten Filaments, Stock ignoriert ihn), liest weitere Schlüssel
  tolerant. Die Stock-Datei `/mnt/UDISK/creality/userdata/box/
  remain_material_data.json` kann 1:1 als Startwert dienen (ohne Slot;
  `flush_mode: matrix` nutzt die Matrix ohnehin erst ab dem ersten Wechsel
  im Druck, s. u.). Farben
  `-1`/`none`/`unknown` = unbekannt (Stock `ignore_ops`). Hinweis: Stock
  speichert als `type` das `material_type` von `Tn_data`, k2_box die
  RFID-`mat_id` – gleiche Rolle (Material-DB-Schlüssel), Stock-Werte werden
  unverändert übernommen.
- **Lesen** genau einmal bei `klippy:connect`. Fehlt → Hinweis (Datei wird
  nach dem nächsten Spülen angelegt). Unlesbar, kein JSON, falsche Struktur
  (`{}`, `null`, Liste, fehlende `color`/`type`), Farbe ≠ `^0[0-9A-Fa-f]{6}$`,
  ungültiger Typ → **nur WARNUNG**, Material unbekannt (90 + 70 mm wie
  bisher), **nie Config-Fehler**. Eine inhaltlich kaputte Datei wird **nicht
  überschrieben**, sondern nach `<datei>.defekt-<JJJJMMTT-hhmmss>` umbenannt
  (Inhalt bleibt zur Diagnose). Stock dagegen überschreibt sie mit `null`
  bzw. bricht den Klippy-Start ab.
- **Schreiben** nur nach erfolgreichem `KTWO_BOX_FLUSH` (wirklich gespült,
  `allow_move`) und nur, wenn Farbe/Typ/Slot sich ändern – **nicht** bei
  Cut, Rückzug/Entladen, Einzug, Spülfehler, Abbruch/Pause, Plan ohne
  `allow_move` (wie Stock: einziger `save`-Aufruf nach dem Spülen).
  Atomar: `<datei>.tmp` im selben Verzeichnis, `flush` + `fsync`,
  `os.replace`, `fsync` des Verzeichnisses, fehlende Verzeichnisse werden
  angelegt; andere Top-Level-Schlüssel einer gültigen Datei bleiben.
  Schreibfehler = WARNUNG, der Wert gilt bis zum Neustart, der Wechsel läuft
  weiter.
- **`KTWO_BOX_REMAIN [COLOR=0RRGGBB|#RRGGBB|none] [TYPE=<id>|none]
  [TNN=T1A] [CLEAR=1]`** – **Nicht-Stock** (Stock hat kein Reset-Kommando):
  ohne Parameter Anzeige; sonst Korrektur nach Handarbeit an der Düse
  (gilt für den nächsten Wechsel, wird gespeichert, wenn an). Bewegt und
  sendet nichts. Eine Form `KTWO_BOX_REMAIN SET …` ist
  unter Upstream nicht möglich (Wort ohne `=` = „Malformed command“) – daher
  nur `KEY=VALUE`.
- **Status:** `remain_material_file` (absoluter Pfad, `''` = aus),
  `remain_material_load` (`aus`/`ungelesen`/`fehlt`/`geladen`/`fehler`),
  `remain_material_info` (Meldung).
- Install-Hinweis: Den Holschritt der Stock-Datei baut `install.sh
  --remain-material` (eigener Arbeitsgang, Zielpfad = obiger Default).

**`flush_mode: matrix`** (Default seit 18.10.2026 = Stock; bis dahin `color`,
Rückkehr mit `flush_mode: color`)

- k2_box liest die Slicer-Spülmatrix selbst aus der laufenden Druckdatei
  (Upstream `virtual_sdcard.file_path()`): die letzten **und** ersten
  512 KiB (wie Stock metadata.py READ_SIZE, bis 18.10.2026: 256 KiB; kleine Dateien ganz) nach `; flush_volumes_matrix = …` und
  `; flush_multiplier = …` (Ende hat Vorrang = Orca-CONFIG_BLOCK; Zeilenform
  am Orca-Mehrfarbdruck 18.10.2026 belegt: `0,82,73,0` / `0.3` = Stock
  `flush_para`). Cache nach Pfad +
  Größe + mtime, geleert bei `virtual_sdcard:reset_file`.
- Index wie Stock `[alt][neu]` = Slicer-Werkzeugnummer, aber **direkt
  durchgereicht** statt rückwärts gesucht: `KTWO_BOX_FLUSH … TOOL=<n>`
  (`_KTWO_TOOL_CHANGE T=<n>` und `M8200 L I<n>` → `M8200 F` reichen `n`
  durch); ohne `TOOL=` feste Zuordnung aus `TNN`. Altes Werkzeug = Slot des
  zuletzt gespülten Filaments, **aber nur, wenn es in diesem Druck gespült
  wurde** (18.10.2026).
- **Erster Einzug eines Drucks = Farbweg wie Stock** (Stock `last_tnn` ist
  beim Druckstart `None`, „Try to get the resize length from the file“ erst
  ab dem ersten Wechsel): Quelle = Düsen-Farbe aus `remain_material` (auch
  aus dem Vordruck/der Datei), Ziel = Slot-Farbe; HINWEIS „erster Einzug im
  Druck – altes Werkzeug in diesem Druck unbekannt -> Farbweg“, keine
  Warnung. Zurückgesetzt bei `virtual_sdcard:reset_file` (jeder
  `SDCARD_PRINT_FILE`, Druckende, Abbruch), `KTWO_BOX_PRINT_START`,
  `KTWO_BOX_CANCEL_RESET` und wenn sich `print_stats.print_start_time`
  (Druckkennung) geändert hat. Spülen außerhalb eines Drucks zählt nicht;
  Düsen-Material von Hand per `KTWO_BOX_REMAIN` auf einen anderen Slot
  gesetzt → ebenfalls Farbweg (Hinweis).
- Live-Beleg (Druck 3, 18.10.2026): Erst-Einzug `01B04AE → 0CE58F8` =
  368 mm³ + 183 → 229,58 mm → `[90, 70, 70]`; Wechsel im Druck `[1][0]=73`
  / `[0][1]=82` × 0,3 → 9,1/10,3 mm → min 160 → `[90, 70]` (Test
  `test_matrix_erster_einzug_live`). Mit `color` wären es bei A → B
  229,6 mm statt 160 (13 × ≈ 905 mm mehr).
- Nur bei `print_stats.state == "printing"` (wie Stock; in der Pause,
  beim Nachholen/Runout und von Hand → Farbweg mit HINWEIS), ohne `LENGTH=`
  und mit bekanntem altem Werkzeug. Länge = `M[alt][neu] / 2,4 ×
  flush_multiplier` (fehlt → 1, **ohne** `nozzle_volume`), danach
  `finish_flush_len` (anderes Material ×2/min 370, sonst min 160).
- Rückfall auf `color` mit **WARNUNG** (kein Fehler, keine Pause): keine
  Druckdatei, keine Matrix, leer, nicht quadratisch, Index ≥ n (z. B.
  Creality-Print-Dateien mit `[0]`), `TOOL` passt nicht zu `TNN`. (Altes
  Werkzeug im Druck unbekannt = erster Einzug → Farbweg mit HINWEIS, s. o.) Der FLUSH-Plan nennt die Quelle („Slicer-Matrix [i][j] =
  V mm³ / 2,4 × m“).

**`M600`** (`macros.cfg`): Stock-`virtual_sdcard` macht aus `M600` im
Dateistrom `PAUSE` (dort nur mit Power-Loss-Schalter). Upstream
kennt kein `M600` (geprüft) → Makro `M600` → `PAUSE` (unser PAUSE-Makro:
Parken, 140 °C, RESUME wie gewohnt), nur im laufenden, nicht pausierten
Druck; sonst nur Meldung (kein Parken außerhalb eines Drucks). Kein
`rename_existing` nötig, kein Konflikt.

**Offen / am Gerät:** erste Speicherung + Neustart (Datei im Mainsail-Editor
sichtbar, Inhalt), Zeilenform der Orca-Matrix in einer echten Mehrfarbdatei
und Spülmengen gegen Orca-Anzeige, `M600` aus einer Orca-Datei (Pause,
RESUME), Stock-Datei als Startwert nach `install.sh --remain-material`.

### 12.5 Restmaterial („ending material“) + Druckende (Ergänzung 17.10.2026)

> **ENTWURF – am Gerät UNGETESTET.** Stock-Vorbild: Flag
> `is_use_ending_material`, `extrusion_all_materials`, `box_end`, CFS-Status
> 0x50 (aus der Firmware-Analyse). Offline: `test_k2_box.py` 59/59; Batch
> gegen Upstream-Klipper 34/34.

**Optionen `[k2_box]`** (in `k2-module.cfg` kommentiert)

| Option | Default | Wirkung |
|---|---|---|
| `ending_material_purge` | `True` (= Stock) | Restmaterial-Fall beim nächsten Wechsel / `M8200 P` / Druckende ausspülen statt Schnitt + Rückzug; `False` = bisher (Schnitt + Rückzug, Hinweis) |
| `max_tube_length` | 3000 (int, 80..10000) | Stock-`BoxCfg`-Obergrenze in mm; das letzte Stück wird gekürzt (Stock: 38 × 80 = 3040) |
| `ending_material_timeout` | 3600 s | Zeitlimit des Ausspülens (Pi-Zusatz, Stock: keins) |
| `end_print_unload` | `True` (seit 18.10.2026, = Stock; vorher `False`) | Druckende wie Stock `box_end`: heizen, schneiden, in die Box zurück (Stock entlädt immer, live 12.10. + 18.10.); `False` = Filament bleibt im Kopf |

**Auslöser (Stock `send_data`):** Jeder 0x0a-Poll (Timer ~4 s,
`KTWO_BOX_STATUS`, Box-Warten) mit Status **0x50** im PRINT (bzw. gespeichert
im ERROR) = Einlauf oder Hub des Druck-Slots frei setzt – nur bei
`print_stats` printing/paused – den Merker (`printer.k2_box.ending_material`,
`ending_material_tnn`, Meldung einmal). `PRE_OPT` pollt vorher noch einmal
frisch (lesend, nur im Druck mit beiden Freigaben). Polls während einer
laufenden Kette setzen ihn nicht (sie sähen das 0x50 des alten Slots).
Gelöscht nach dem Ausspülen, nach erfolgreichem `EXTRUDE`, in
`KTWO_BOX_RUNOUT` (Rest ist am Kopfsensor vorbei; Stock
`check_material_refill` spült dann nicht) und per `KTWO_BOX_ENDING
CLEAR=1`. Nur im Speicher (Stock ebenso). `KTWO_BOX_RUNOUT` und das
Nachholen im RESUME (`resume_redo_change`) spülen nie aus.

**Ausspülen (Stock `extrusion_all_materials`)** – nach `KTWO_BOX_PRE_OPT`
(Kette aktiv, kein Skip) als eigener Schritt `ENDING_PURGE`:

1. nur mit `allow_write` UND `allow_move` (sonst PLAN, im Druck PAUSE wie
   jeder Wechsel); Kopfsensor ohne Zustand → **Abbruch, PAUSE + Meldung,
   nichts gefördert** (Stock förderte blind bis 3000 mm);
2. Kopfsensor aus (PRE_OPT), Schacht, Heizen auf max(Basis, Material-
   Temperatur) (Basis in der Pause `_PI_PARAM.hotend_temp`, Stock
   PRINTER_PARAM; ohne DB `Tn_extrude_temp` 220);
3. Schleife: `G1 E80 F<v>` + `M400` (statt Stock `reactor.pause(t − 0.5)`
   **ohne** `monotonic()` – Creality-Fehler, nicht übernommen), Blasen
   (`flush_blow`, fan0 5 s), Blech **ohne** Kreise (bleibt im Schacht);
   `v` = `max_vol/2,4·60` des Materials in der Düse (Hyper PLA 575, PLA14
   350) **ohne ×0,8, ohne 360-Grenze**; ohne DB bzw. ohne `max_vol`
   `Tn_extrude_velocity` 360 (= bisherige Spülgeschwindigkeit ohne DB);
4. Ende: Kopfsensor meldet nach einem Stück LEER → genau **ein** weiteres
   Stück (Stock; Kopf schon beim Start leer → 2 Stücke). Obergrenze
   erreicht und Kopf noch voll → Fehler/PAUSE (Stock: Warnung + weiter);
   erst an der Grenze leer → WARNUNG. Sensor fällt unterwegs aus oder
   Zeitlimit → Fehler/PAUSE. Merker bleibt bei jedem Fehler;
5. danach: Box `0x04 [00 01]` IDLE, `loaded_slot` vergessen (Stock
   `last_cmd = None`), Merker gelöscht. In der Kette entfallen `CUT` und
   `RETRUDE` („übersprungen (Restmaterial ausgespült)“), `EXTRUDE` lädt das
   neue Filament. **Kein Abkühlen in der Kette** (Stock `set_cool_temp`
   M104 S140 – hier nur beim Ausspülen von Hand; in der Kette heizt
   EXTRUDE gleich wieder und END_OPT stellt das Ziel zurück).
   `remain_material` bleibt (Stock: kein `save`; in der Düse ist weiter
   dasselbe Material).

`CUT`/`RETRUDE` ohne Kette bei offenem Fall: nur WARNUNG (Rückzug eines
Rests ohne Ende in der Box, ANNAHME Hub-Verstopfung).

**`KTWO_BOX_ENDING [SET=1 SLOT=A] [CLEAR=1] [PURGE=1]`** (Nicht-Stock
außer PURGE): Anzeige; Merker von Hand setzen/löschen (Stock
`use_ending_material_flag_clear` hat keinen Aufrufer); `PURGE=1` = Stock
`BOX_EXTRUSION_ALL_MATERIALS` (unabhängig vom Merker, nur außerhalb eines
Drucks oder in der Pause, nicht während einer Kette; Sensor aus/zurück,
danach M104 S140, Kopf bleibt im Schacht wie Stock).

**Druckende `KTWO_BOX_END_PRINT [UNLOAD=0|1]`** (= Stock `BOX_END`, aus
`END_PRINT` in `macros.cfg`, nur bei `change_enabled` UND
(`end_print_unload` ODER `ending_material`) UND referenzierten Achsen, seit
18.10.2026 direkt nach Z +2 und VOR Extruder-Rückzug/Z-Hub auf max_z/2
(`_PI_END_PARK`, §16 A6), vor `TURN_OFF_HEATERS`): Kette `PRE_OPT Z_HOP=0` →
(offener Fall: Ausspülen) → mit `UNLOAD` (Default `end_print_unload`) und
Kopfsensor „Filament“: Schacht, Heizen auf die Material-Temperatur, `CUT`,
`RETRUDE` (Box 0x11 p0×4 + E−10 + p1) → `END_OPT RETURN=0` (Schacht
verlassen, Ziel zurück). Kopf leer/unbekannt → kein Entladen (Meldung).
**Kein Spülen am Druckende** (Stock ebenso). `remain_material` bleibt.
Fehler (auch Kopfsensor unbekannt beim Ausspülen) → nur `!!`-Meldung,
Kette aufgeräumt, **keine PAUSE, kein G-Code-Fehler** – `END_PRINT` läuft
weiter und schaltet die Heizungen aus. Ohne Freigaben bzw. bei offener
Kette/offenem Fehler: übersprungen mit Meldung.

**Status** neu: `ending_material`, `ending_material_tnn`,
`ending_material_purge`, `ending_last`, `end_print_unload`.

**Offen / am Gerät:** echtes 0x50 im Druck (Zeitpunkt, Slot im Poll),
Länge Kopfsensor → Düse (< 80 mm? sonst reicht das Zusatzstück nicht),
Ausspülen einer leer gelaufenen Spule beim nächsten T<n> (Menge, Abfall im
Schacht, Box nach IDLE), Entladen am Druckende (Schnitt + Rückzug,
Rückzugslänge bis in die Box), mechanische Folge des bisherigen Rückzugs
bei leerem Einlauf (Vergleich).

## 13. Stock-Retry bei 0x0c + Temperatur-Guard (Ergänzung 18.10.2026)

Quelle: Abgleich mit `gitstonelabs/creality-cfs-klipper` (Hi-Mitschnitt).

- **Stock-Retry im Einzug** (`do_extrude`, auch `KTWO_BOX_EXTRUDE`/T<n>):
  0x0c (Odometer-Stillstand) im mode-5-Reply → Düsenreinigung (Stock
  „move to retry“, Kopf bleibt im Schacht) und **einmal** `10[m 04 00]` neu;
  erneut 0x0c → `04[00 01]` + `11[m 01]` und **ein** kompletter Neuversuch
  ab `10[m 00 00]` (ohne 0f/08-Präfix, wieder mit einem mode-4-Neustart);
  scheitert auch er → Abbruch wie bisher (Box bleibt ERROR 0x0c wie Stock,
  `_fail` → PAUSE/RESUME-Sperre, `KTWO_BOX_ERROR_CLEAR`). 0x09 auf mode 0
  → `04[00 01]` + `11[m 01]`, dann Abbruch (Hi versucht danach erneut – hier
  bewusst nicht). Andere Status → sofort Abbruch. Höchstens 4 Förderphasen;
  Sensor-Budget (`extrude_sensor_timeout`) je Förderphase wie Stock
  („timeout = 90“ nach jedem mode 4). Folge belegt durch K2-Stock-Live-Log
  vom 12.10.2026 **und** Hi-Mitschnitt;
  Builder `cfsseq.build_extrude_failure_path`, Konstanten
  `EXTRUDE_RETRY_MAX = EXTRUDE_FEED_RESTARTS = 1`.
- **Temperatur-Guard** `KTWO_BOX_LOAD/UNLOAD` (heizen nicht selbst): Düse
  unter `min_extrude_temp` (Vorrang `can_extrude` aus dem Extruder-Status,
  sonst Ist-Temperatur gegen heater/configfile/170 °C; unlesbar = gesperrt)
  → G-Code-Fehler, **kein Frame** (weder 0x10 noch 0x11). `DRY_RUN` bleibt
  frei. Die Wechselkette heizt vorher selbst (M109) und ist nicht betroffen.
- Tests: `test_k2_box.py` +3 (Retry Controller == Live-Log inkl. Abbruch,
  Dauer-Stillstand begrenzt, 0x09, Fremdstatus; Hülle mit Reinigung/PAUSE;
  Guard). Offen am Gerät: echter 0x0c-Fall mit Neuversuch.

## 14. Fahrgrenzen wie Stock – `[k2_limits]` (Ergänzung 18.10.2026, L3)

Das Betriebsfenster ist jetzt eng wie bei Stock: X ab `cut_pos_x`, Y bis 352, Z bis 350,2 (`printer.cfg`). Die Belege und das Modul stehen in `README-k2_limits.md`. Für `k2_box` heißt das:

- **`_move`** (alle Bewegungsschritte: Schacht, Abstreifen, Reinigung, Spülen, Restmaterial, Rückfahrt) prüft mit `k2_limits.needs_wide()`, ob die Zeilen einen XY-Punkt außerhalb des Fensters anfahren. Nur dann weitet es die Grenzen **für diesen Schritt** auf: Halter `k2_box`, X ab −10, Y bis 400. Stock macht dasselbe mit `box_wrapper set_flushing_sign` → `SET_LIMITS`.
- Im `finally` gibt `_move` die Grenzen wieder zurück, auch bei Fehlern. Stock: `reset_flushing_sign` → `RESTORE_LIMITS`.
- Zwischen den Schritten steht das Fenster wieder eng. E-Fahrten im Schacht und reine X-Fahrten bei Y378 bleiben erlaubt, weil Upstream nur bewegte Achsen prüft.
- **Der Schnitt** (`gcode_cut`) endet genau auf `cut_pos_x` = enge X-Grenze und braucht keine Aufweitung.
- **Ungehomtes X/Y:** Das Aufweiten wird verweigert (`LimitsError`). Der Schritt scheitert ohne Fahrt (`_fail` → im Druck PAUSE).
- **Ohne `[k2_limits]`:** unverändert (kein Aufweiten).
- Bricht ein G-Code im aufgeweiteten Zustand ab, setzt `[k2_limits]` die Grenzen selbst zurück (`gcode:command_error`).
- **Tests:** `test_k2_box.py` „L3 Fahrgrenzen [k2_limits] im k2_box-Schritt“, `test_k2_limits.py` (alle Fahrwege mit den `k2-module.cfg`-Werten gegen beide Fenster).
- **Batch** (Wechsel-, Spül- und RESUME-Läufe gegen Upstream-Klipper): 0 „Move out of range“.
- Die PAUSE-Parkposition ist jetzt X342,5 **Y176** (Stock: `axis_maximum.y` 352 / 2).

## 15. RFID neu lesen + Slot-Farbe/-Material von Hand (Ergänzung 18.10.2026)

Grundlage: Stock-Befehle `BOX_INFO_REFRESH` und `BOX_MODIFY_TN_DATA`
(Display/App), Stock `box.cfg:131–138`, Firmware-Analyse von
`box_wrapper.so` (NUM = Slotmaske, NUM=0 → Maske-0-Bursts).

### 15.1 `KTWO_BOX_RFID [SLOT=T1A..T1D|A..D|0..3|ALL] [FORCE=1]` (= Stock `BOX_INFO_REFRESH`)

- Stock-Makro: `BOX_SET_PRE_LOADING ADDR NUM ACTION=RUN` → `BOX_GET_RFID` →
  `BOX_GET_REMAIN_LEN`, NUM jeweils als **Maske** (UI schickt NUM=0 → Maske
  0, Timeout 0, 5 Sendungen = die `[00 02]`-Bursts).
- Pi: **nur lesende Frames** über die vorhandenen Controller-Funktionen:
  `0x02 [m]` (`get_rfid`/`get_rfid_all`), dann `0x03 [m]` (`get_remain`),
  m = Slotbit A=01, B=02, C=04, D=08 bzw. `0f` für ALL/ohne SLOT. **Kein
  0x0d** (der Stock-Preload-RUN bewegt Filament) und **nie Maske 0** – es
  gibt nur Slots, keinen NUM-Parameter.
- Nur Box `box_addr` (T2A… → Fehler ohne Frame). Ohne `allow_write` erlaubt.
- Sperren: Wechselkette aktiv bzw. Box `busy` → G-Code-Fehler; im
  Druck/in der Pause nur mit `FORCE=1` (nie in einer Kette).
- Danach: `slot_cache` (rfid/remain), Etikett-Cache, Status
  `slot_material` aktualisiert; Meldung je Slot mit Material aus der
  Material-DB (Name, Typ, Temperatur), Farbe, Rest, Quelle. Einzel-Remain
  überschreibt die anderen Slots nicht mehr (0xff außerhalb der Maske =
  „nicht gefragt“; `K2BoxController.get_remain` übernimmt nur Maskenslots).

### 15.2 `KTWO_BOX_SLOT_SET SLOT=T1A [COLOR=RRGGBB|none] [TYPE=<id>|none | MATERIAL=<Name>] [CLEAR=1]` (= Stock `BOX_MODIFY_TN_DATA`)

- Stock (B): Display/App/Web senden `BOX_MODIFY_TN_DATA ADDR NUM
  PART=color_value|material_type DATA` → `cmd_modify_Tn_data`
  (ADDR, NUM/PART/DATA aus den Parametern) →
  `box_state.modify_Tn_data`, **ein Feld je Aufruf**. `modify_Tn_data`
  persistiert über `modify_tn_save_data`/`update_tn_save_data`
  (→ `creality/userdata/box/tn_data.json`). Die GET_RFID-Antwort
  (`process_msg`) schreibt **dieselben** Felder
  vender/color_value/material_type über `modify_Tn_data` (Vergleiche
  gegen „unknown“/„-1“) → „letzter Schreiber gewinnt“, ein späteres
  RFID-Lesen überschreibt den Handwert.
- Pi: Handwert je Feld im Speicher (`slot_manual`, TNN-Schlüssel), wirkt
  **vor** dem Etikett (`merge_tag`). Ein **lesbares** Etikett des Slots
  (KTWO_BOX_RFID, Laden `_remember_tag`, Runout-Lesen) verwirft den
  Handwert (Meldung „Handwert … verworfen (RFID hat Vorrang)“); „unknown“/
  „busy“/leer lassen ihn stehen. **ANNAHME:** Verhalten des Stock-Zweigs
  „Etikett unlesbar“ nur V – auf dem Pi bewusst so, weil Handwerte gerade
  für Spulen ohne lesbares Etikett gedacht sind. Mit Handwert liest
  `_slot_tag` nicht selbst nach (sonst verwürfe ein Wechsel ihn mitten
  drin).
- `COLOR` wie `KTWO_BOX_REMAIN`: `RRGGBB`/`0RRGGBB` → `0RRGGBB` (im G-Code
  **ohne `#`** – Upstream-shlex wertet `#` als Kommentar); `none` löscht nur
  dieses Feld. `TYPE` = Material-ID (6-stelliger RFID-Code wie `101001`
  oder 5-stellige DB-`base.id`). `MATERIAL=<Name>` (Material-DB nötig, mit
  Leerzeichen in Anführungszeichen) → exakter Name, sonst eindeutiger
  Teilstring → `base.id`. `CLEAR=1` löscht den Handwert. Ohne Parameter nur
  Anzeige. Sendet nichts (ohne allow_write/allow_move); Ändern während
  einer Wechselkette gesperrt.
- **Wirkung konsistent über `_cached_tag`/`_slot_tag`:** Spülmenge (Farbe
  alt→neu, anderes Material ×2/min 370), Spül-/Einzugs-/Nachlade-
  Temperatur und Geschwindigkeit aus der Material-DB, Restmaterial-
  Geschwindigkeit, Runout-Ersatzwahl (`select_runout_slot(manual=…)`,
  Material über die DB-ID verglichen: `101001` ≙ `01001`). Nach dem Spülen
  steht der Handwert als Düsen-Material in `remain_material`.

### 15.3 Persistenz `slot_material_file`

- Option `[k2_box] slot_material_file` (Default
  `~/printer_data/config/k2/slot_material.json`, leer = nur Speicher).
  Entscheidung **persistent wie Stock** (`tn_data.json`, s. oben): ein von
  Hand gesetzter Slot ohne Etikett soll einen Neustart überstehen.
- Format `{"slots": {"T1A": {"type": "<id>"|null, "color": "0RRGGBB"|null}}}`;
  gelesen einmal bei `klippy:connect`, geschrieben bei jeder Änderung
  (auch beim Verwerfen durch RFID) atomar wie `remain_material_file`
  (tmp + fsync + `os.replace` + Verzeichnis-fsync, andere Schlüssel/Boxen
  bleiben). Kaputt → WARNUNG + `.defekt-<zeit>`, nie Config-Fehler;
  Schreibfehler → WARNUNG, Wert gilt bis zum Neustart.
- Restrisiko: Spule ohne Etikett getauscht → alter Handwert bleibt, bis
  `KTWO_BOX_SLOT_SET … CLEAR=1` oder neuer Wert (Stock ebenso).

### 15.4 Status, Tests, Batch

- `get_status()`: `slot_material` je Slot `{tnn, type, color, source
  (rfid|manuell|rfid+manuell|''), manual, material, remain}`,
  `slot_material_file`, `slot_material_load` (aus|ungelesen|fehlt|geladen|
  fehler), `slot_material_info`, `rfid_last`.
- Nebenbei: `MATRIX_SCAN_BYTES` 256 →
  **512 KiB** wie Stock `metadata.py` `READ_SIZE` (Kopf und Ende).
- `test_k2_box.py` +3 (RFID-Befehl, SLOT_SET mit Vorrang/Wirkung,
  Datei); Matrix-Test um 512 KiB/„300 KiB tief“ ergänzt.
- Batch gegen Upstream-Klipper (44 Prüfungen): Frame-Folge nur
  `0x02/0x03` mit `[0f]`/`[02]`/`[01]`, Handwert im FLUSH-Plan, RFID
  lässt/verwirft, Datei, gewollter Fehler `SLOT=T2A`.
- Offen am Gerät: Remain-Werte FW 113 (100 = „kein RFID-Wert“) in der
  Meldung nicht umgedeutet; Stock-Zweig „Etikett unlesbar“ (V).

## 16. Live-Abgleich Einfarbdruck (Ergänzung 18.10.2026)

Echter Stock-Druck (T1A, Orca, 57 min) gegen die Kette gelesen; umgesetzt:

| Nr. | Stock live (Beleg) | Pi neu | Rückfall |
|---|---|---|---|
| A1 | `T0` bei leerem Kopf + leerem Hub: `filament_sensor false`, `0x08 [01]` → 0, **kein Cut, kein Rückzug**, nur Einzug (16:46:26) | `_empty_load`: in der **T<n>-Kette** (`SOURCE=T`) mit Kopfsensor **False** UND Hub 0 (`0x08 [01]`, lesend) → `CUT` + `RETRUDE` übersprungen, `EXTRUDE` lädt | Kopfsensor unbekannt/Filament, Hub belegt/unlesbar, M8200-Kette, Einzelschritt → schneiden/zurückziehen wie bisher |
| A2 | Dateistart `0x0d [0f 00]` + `0x0f [01]` (16:45:36/37), Druckende `0x0d [0f 01]` + `0x0f [00]` (17:44:08); im `T0` **kein** 0x0f | `KTWO_BOX_PRINT_START` (aus `START_PRINT`) / `KTWO_BOX_PRINT_END` (aus `END_PRINT` nach `KTWO_BOX_END_PRINT`), nur mit `change_enabled`; Controller `do_print_state`, `print_hold`; `do_extrude(inhibit=False)` solange `print_hold` UND Druck | ohne Freigabe nur Plan; Fehler nur `!!`-Meldung (Druck läuft weiter, Einzug sendet dann sein 0x0f-Paar); außerhalb eines Drucks bisheriges 0x0f-Paar |
| A3 | Rückzug je p0 `extrude = -10.0, velocity: [350.0]` (= Spülgeschwindigkeit, max_vol 14; 16.10. + 18.10.) | `_retrude_velocity`: mit Material-DB `flush_velocity_base(max_vol des Düsenmaterials, Düse)` | ohne DB/max_vol `tn_retrude_velocity` 600 (Stock ohne Materialdaten, 12.10.) |
| A5 | Druckende entlädt immer (12.10., 18.10.) | `end_print_unload` Default **True** | `False` in `k2-module.cfg` |
| A6 | `END_PRINT_Z_SAFE` nur **Z +2**, dann Cut + Rückzug, dann `END_PRINT_POINT` (Z 172,96) | `END_PRINT`: Z +2 (nur wenn < max_z − 2) → `KTWO_BOX_END_PRINT` → `KTWO_BOX_PRINT_END` → `_PI_END_PARK` (E−1/E−2, Z auf max_z/2, Position frisch ausgewertet) | ohne Referenz keine Bewegung (wie bisher) |

Sicherheit: `do_print_state` sendet nur op 0/1 mit Maske `0x0f` (nie 0,
`preload_block_reason(force=True)` wie Stock force=True), schreibend nur mit
`allow_write`. Die Schachtfahrt am Druckende läuft 2 mm über der letzten
Schicht (höher ragt kein Teil); X/Y-Grenzen weitet k2_box wie bisher nur
für den Schachtschritt (`[k2_limits]`, §14), Z bleibt ≤ 350.
Status neu: `print_hold`. Tests: `test_k2_box.py` „Live-Abgleich 18.10.“.
Offen am Gerät: Box nach `0x0d [0f 00]` im Druck (Einlegen einer Spule
wird nicht eingelesen – Stock gleich), Erst-Laden ohne Schnitt.


## 17. Druckabbruch (Ergänzung 18.10.2026)

Stock-Abbruch = Druckende (live beobachtet: `SAVE_ENDPRINT` → `END_PRINT` →
`BOX_END` entlädt → `BOX_END_PRINT`); der Pi-Pfad `CANCEL_PRINT` →
`END_PRINT` bildet das seit dem ersten Live-Abgleich ab. Neu:

| Nr. | Problem | Pi neu | Beleg |
|---|---|---|---|
| C3 | Abbruch aus einer Pause mit offenem Wechsel-Fehler: `KTWO_BOX_END_PRINT` übersprang, `chain_error`/`aborted` überlebten das Druckende → das erste `T<n>` des nächsten Drucks scheiterte in PRE_OPT und pausierte sofort | `CANCEL_PRINT` = `END_PRINT CANCEL=1` → `CANCEL_PRINT_BASE` → **`KTWO_BOX_CANCEL_RESET`**. `KTWO_BOX_END_PRINT CANCEL=1` räumt eine offene Kette (nicht-mechanisch) auf und **entlädt trotz Fehler nur, wenn sicher**: Fehler im Schritt `EXTRUDE`/`FLUSH`/`WASTE` UND Kopfsensor meldet Filament (dann wie Stock `box_end`: Schacht, Heizen, Schnitt, Rückzug); sonst (`CUT`, `RETRUDE`, Schachtfahrten, Heizen, Ausspülen, unbekannt; Kopf leer/unbekannt) nur `!!`-Meldung „NICHT entladen …“. `chain_error` bleibt dabei gesetzt (für das Entladen nur beiseitegelegt) und wird erst **nach** `CANCEL_PRINT_BASE` gelöscht (`KTWO_BOX_CANCEL_RESET`: `chain_error`, Abbruch-Merker, Z-Korrektur, Startposition, offene Kette; war ein Fehler offen + `allow_write`: Box `0x04 [00 01]` wie `KTWO_BOX_ERROR_CLEAR`). Rückfall: `KTWO_BOX_PRINT_START` (neuer Druck) setzt ebenso zurück; `START_PRINT` ohne Freigabe ruft `KTWO_BOX_CANCEL_RESET START=1`. | Stock `cmd_box_end_print` (= `BOX_END_PRINT`, beim Abbruch 2× gelaufen) ruft `box_save.clear_resume_tnn()`; Stock `box_end` prüft keinen Fehler, entlädt bei gemeldetem Filament |
| C4 | Druckende über `on_error_gcode` ohne `KTWO_BOX_PRINT_END` (Box blieb in `0x0d op 0` + `0x0f [01]`) | `_PI_RESET_STATE` ruft `KTWO_BOX_PRINT_END`, nur wenn `print_hold` (= `KTWO_BOX_PRINT_START` hatte den Bus); Fehler nur Meldung | Live-Abbruch am Stock-Drucker |
| C2 | Upstream-Cancel wartet auf das G-Code-Mutex – ein laufendes `T<n>` (m5-Poll bis 90 s je Förderphase, Box-Zustand bis 380 s) oder Ausspülen (bis 3600 s) hält den Abbruch auf | `[k2_box] cancel_interrupt` (Default **True**): beim `klippy:connect` wird der Webhook `pause_resume/cancel` eingehüllt (`webhooks._endpoints`, Upstream 9cb9975; fehlt das → Status `cancel_hook` „nicht möglich“, Upstream-Verhalten). Das Flag steht nur, solange das Original (`run_script("CANCEL_PRINT")`) wartet/läuft (finally → weg). Abgefragt: vor jedem k2_box-Schritt (`_run_step`, = Stock `cancel_pending`: Schritt läuft leer), im Einzug vor dem ersten Frame und je Sensor-Poll (dann `0x04 [00 01]` IDLE wie beim Sensor-Timeout), in `wait_box_ready`, vor jedem Spül-Abschnitt und jedem Ausspül-Stück. Folge: **keine PAUSE, kein Wechsel-Fehler**, Kette übersprungen, dann läuft `CANCEL_PRINT` (Entladen bei gemeldetem Filament). Nachholen im RESUME / Runout-Nachladen setzen danach nie fort. Ein echter Fehler bei wartendem Abbruch: keine PAUSE, Fehler gemerkt (für die C3-Entlade-Prüfung). | Upstream `gcode.py`, `pause_resume.py` |
| C5 | Bauteillüfter am Ende 100 % statt Stock ≈ 55 % | `END_PRINT`: nach `_WAIT_TEMP_START` `M106 P0 S127` (Stock-Reihenfolge) | Stock-`END_PRINT` |
| C6 | Z+2-Grenze `< max_z − 2` | `< max_z − 5` wie Stock `END_PRINT_Z_SAFE` | Stock-`END_PRINT_Z_SAFE` |

**Nicht unterbrechbar (dokumentiert, Upstream ohne `cancel_pending`):**
einzelne Bus-Transaktionen (Box antwortet erst nach dem Vorgang: `0x10`
mode 0 ≈ 4,5 s, `0x11` p1 bis ≈ 23 s, `0x0d` op 2/3 31–37 s je Slot –
mittendrin aufgeben ließe den Bus-Thread weiterlaufen) und Upstream-Befehle
innerhalb eines Schritts (`M109`, `M400`); der nächste Wartepunkt danach
greift. Der Rückzug (`RETRUDE`, p0 ×4 + p1) wird nie mittendrin
abgebrochen (halb zurückgezogenes Filament wäre schlechter). `M112` bleibt
der Not-Aus.

**Sicherheit RESUME-Sperre:** unverändert in jeder Pause –
`KTWO_BOX_CANCEL_RESET` und `KTWO_BOX_PRINT_START` setzen in einer Pause
nichts zurück (Meldung), `CANCEL_RESET` ohne `START=1` auch nicht bei
`print_stats` printing. Scheitert `CANCEL_PRINT` vor `CANCEL_PRINT_BASE`,
bleibt die Sperre stehen.

Status neu: `chain_error_step`, `cancel_hook`, `cancel_pending`.
Tests: `test_k2_box.py` „Druckabbruch C3/C4–C6/C2“; Batch
gegen Upstream 9cb9975 (Pause aus RETRUDE → CANCEL ohne
Entladen → Reset nach `CANCEL_PRINT_BASE` → neuer Druck, `T1` beginnt
normal). Offen am Gerät: Abbruch während eines Einzugs (Box nach
`0x04 [00 01]` mitten in mode 5), Entladen beim Abbruch nach Fehler beim
Spülen.


## 18. Externe Spule (Ergänzung 19.10.2026)

Grundlage: Live-Mitschnitt am Stock-Drucker (Laden, Mechanik, Entladen,
Stock-Makros `LOAD_MATERIAL*`/`QUIT_MATERIAL*`, zweites Laden),
Firmware-Analyse `[filament_rack]` (RFID-Leser 0x11, Material-, Heiz- und
Spüllogik), `nachbau/filament_rack.py`. Ersetzt den
bisher als „entfällt“ geführten Stock-Abschnitt `[filament_rack]`.
**Am Gerät getestet 26.10.2026** (Laden, Druck, Entladen – §18.9).

### 18.1 Prinzip: Filamentquelle vom User

- Für die externe Spule wird der Schlauch **von Hand** von der CFS auf die
  externe Spule umgesteckt; das Filament schiebt der User bis zur **LED am
  Druckkopf** (Kopfsensor `[filament_switch_sensor filament_sensor]`). Den
  umgesteckten Schlauch kann der Pi nicht erkennen → Zustand **Quelle**
  (`cfs` | `extern`), vom User gesetzt:
  `KTWO_BOX_SOURCE [SOURCE=EXTERN|CFS]` bzw. die Makros **`SET_FILAMENT_SOURCE SOURCE=EXTERNAL`** /
  **`SET_FILAMENT_SOURCE SOURCE=CFS`** (Knöpfe in Mainsail/Fluidd). Ohne Parameter nur Anzeige.
- Umschalten sendet nichts und ist nur **außerhalb eines Drucks oder in der
  Pause** erlaubt (nie während einer Wechselkette, eines externen Vorgangs
  oder eines Box-Vorgangs). Hinweise: `SET_FILAMENT_SOURCE SOURCE=EXTERNAL` mit Filament im Kopf
  („stammt es aus der CFS, vorher entladen“), `SET_FILAMENT_SOURCE SOURCE=CFS` mit Filament im
  Kopf („externes Filament vorher mit UNLOAD_FILAMENT entladen“).
- **Persistent** über `[k2_box] filament_source_file` (Default
  `~/printer_data/config/k2/filament_source.json`, leer = nur Speicher):
  `{"source": "cfs"|"extern", "extern_material": {type, color, name, temp,
  origin}|null}`, gelesen einmal bei `klippy:connect`, atomar bei jeder
  Änderung geschrieben (andere Schlüssel bleiben). **Fehlt oder kaputt →
  Quelle CFS** (kaputte Datei + WARNUNG → `.defekt-<zeit>`, nie
  Config-Fehler). Restrisiko: geht die Datei verloren, während der Schlauch
  extern steckt, sind CFS-Aktionen wieder frei – die Startmeldung nennt die
  Quelle.

### 18.2 Sperren bei Quelle EXTERN (nichts an die CFS gesendet)

| Aktion | Verhalten bei Quelle EXTERN |
|---|---|
| `KTWO_BOX_LOAD`/`UNLOAD`/`PRELOAD` (auch `DRY_RUN=1`) | G-Code-Fehler „gesperrt: Quelle ist EXTERN … nichts gesendet“ |
| `KTWO_BOX_PRE_OPT`/`RETRUDE`/`EXTRUDE`/`FLUSH` (T<n>-/M8200-Kette, von Hand) | außerhalb eines Drucks bzw. in der Pause: G-Code-Fehler (offene Kette aufgeräumt); Druck läuft (Befehl aus der Datei): `!!`-Meldung + **PAUSE**, Folgeschritte der Kette übersprungen, `END_OPT` räumt auf |
| `T0`–`T15`, `M8200` (macros.cfg) | → `KTWO_BOX_EXTERN_TOOL` (§18.3), keine Kette |
| `KTWO_BOX_RUNOUT` (runout_gcode) | normale Pause, **kein CFS-Nachladen** (Meldung: Spule von Hand wechseln) |
| `KTWO_BOX_AUTO_REFILL ENABLE=1` | G-Code-Fehler (`ENABLE=0` erlaubt) |
| RESUME-Nachholen (`resume_redo_change`) | gesperrt mit Grund „Quelle EXTERN“, Hinweis `LOAD_FILAMENT` |
| `KTWO_BOX_ENDING PURGE=1`, Restmaterial-Auslöser 0x50 im Poll | G-Code-Fehler bzw. ignoriert |
| `KTWO_BOX_END_PRINT` (END_PRINT) | nur Meldung, **kein Schnitt/Entladen**, Filament bleibt im Kopf (Stock live bestätigt) |
| `KTWO_BOX_PRINT_START`/`PRINT_END` | **wie bei CFS-Drucken gesendet**: `0x0d [0f 00]` + `0x0f [01]` am Druckstart, `0x0d [0f 01]` + `0x0f [00]` am Druckende (Stock live, reine Box-Einstellung) – die einzige Ausnahme vom Schreibschutz |
| `KTWO_BOX_ERROR_CLEAR`, Reset am Druckende | Fehler quittiert, **kein** `0x04 [00 01]` |
| Controller `do_extrude`/`do_retrude`/`do_preload` | `PermissionError` vor dem ersten Frame (`write_block`, zweite Sicherung); `do_print_state` darf, weil seine Frames genau `EXTERN_ALLOWED_FRAMES` sind (`0x0d [0f 00/01]`, `0x0f [00/01]`) – jeder andere Frame (`0x04`, `0x10`/`0x11`, `0x0d` mit Slot-Maske, …) bleibt gesperrt |

Erlaubt bleiben: `0x0a`-Poll, `KTWO_BOX_STATUS` (zeigt `Quelle=…`),
`KTWO_BOX_RFID` (lesend), `KTWO_BOX_SLOT_SET`, `KTWO_BOX_REMAIN`,
Auto-Addr und der Box-Druckzustand am Druckstart/-ende. Stock live beim
Druck mit externer Spule: Druckstart nur lesend (`0x08 [01]`), SD-
Start `0x0d [0f 00]` + `0x0f [01]`, Druckende Z+2, `0x0d [0f 01]` + `0x0f
[00]`, kein `0x04`, kein Einzug, kein Schnitt – der Pi sendet dieselben
vier Einstellbefehle an denselben Stellen (START_PRINT/END_PRINT). Freigabe der Kopf-Bausteine im Druck/in der Pause: bei Quelle
EXTERN genügt **`allow_move`** (kein Box-Frame); bei Quelle CFS unverändert
`allow_write` UND `allow_move`.

### 18.3 Werkzeugwechsel aus der Druckdatei (T<n>, M8200)

- `T0`–`T15`/`_KTWO_TOOL_CHANGE` und `M8200` (`P S<n>` bzw. `L I<n>`) rufen
  bei Quelle EXTERN `KTWO_BOX_EXTERN_TOOL T=<n>` statt der CFS-Kette; die
  übrigen M8200-Buchstaben (C/R/W/F/O) werden mit Meldung übersprungen.
- Im laufenden Druck (`print_stats` printing): **erstes** T<n> des Drucks =
  kein Wechsel (Info), dasselbe T<n> = Info, ein **anderes** T<n> →
  `!!` „Datei verlangt Werkzeugwechsel (T0 -> T1), Quelle ist extern …“ +
  **PAUSE**; der User wechselt die Spule von Hand (`UNLOAD_FILAMENT`, Spule
  tauschen, `LOAD_FILAMENT`) und setzt mit `RESUME` fort; danach gilt das neue
  Werkzeug. Druck-Kennung `print_start_time`; zurückgesetzt bei Datei-Reset,
  `KTWO_BOX_PRINT_START`, `KTWO_BOX_CANCEL_RESET` und beim Quellwechsel.
- Außerhalb eines Drucks bzw. in der Pause (von Hand) → G-Code-Fehler.

### 18.4 `LOAD_FILAMENT` / `UNLOAD_FILAMENT` (= Stock `LOAD_MATERIAL` / `QUIT_MATERIAL`)

Aus den vorhandenen Bausteinen plus `KTWO_BOX_EXTERN_*`:

| Makro | Folge |
|---|---|
| `LOAD_FILAMENT [MATERIAL=] [TYPE=] [COLOR=] [TEMP=]` | `KTWO_BOX_EXTERN_BEGIN OP=LADEN` → `G28 X Y` **nur ohne Referenz** (wie Stock) → Z mindestens `nozzle_clean_z_min` (5 mm) → `KTWO_BOX_GO_EXTRUDE_POS` → `_KTWO_FAN_SAVE` → `KTWO_BOX_EXTERN_FLUSH` → `KTWO_BOX_SAFE_POS` → `KTWO_BOX_EXTERN_END` → `_KTWO_FAN_RESTORE` |
| `UNLOAD_FILAMENT [MATERIAL=] [TYPE=] [COLOR=] [TEMP=]` | `KTWO_BOX_EXTERN_BEGIN OP=ENTLADEN` → `G28 X Y`/Z wie oben → `KTWO_BOX_GO_EXTRUDE_POS` → `KTWO_BOX_EXTERN_HEAT` → `KTWO_BOX_SAFE_POS` → `KTWO_BOX_CUT` → `KTWO_BOX_EXTERN_RETRACT` (E−10 F360) → `KTWO_BOX_SAFE_POS` → `KTWO_BOX_EXTERN_END`; danach Filament von Hand herausziehen |

- **`KTWO_BOX_EXTERN_BEGIN`** prüft **vor jeder Bewegung**: Quelle EXTERN;
  kein laufender Druck (Pause erlaubt, dann mit `[k2_box] allow_move`); kein
  offener Wechsel-Fehler; Box nicht busy; Material/Temperatur (unten);
  **Laden nur, wenn der Kopfsensor Filament meldet** – sonst „Filament bis
  zur LED am Druckkopf schieben“, ohne Sensorzustand gesperrt. Ablehnung =
  G-Code-Fehler, nichts bewegt, nichts gespeichert. Danach: Vorgang beginnt
  (Kette mit Herkunft `EXTERN`), `SAVE_GCODE_STATE`.
- **Material** (Stock-Display: zuletzt geladenes externes Material bleibt
  gespeichert, Material und Temperatur von Hand): ohne `MATERIAL=`/`TYPE=`
  das **gespeicherte** externe Material (zweites Laden ohne Eingabe);
  keines gespeichert → Meldung „MATERIAL=… angeben“. `MATERIAL=` bzw.
  `TYPE=<id>` setzt und speichert ein **neues** Material (Farbe und
  Hand-Temperatur zurück, wie eine neue Spule), `COLOR=RRGGBB` (ohne `#`)
  die Farbe. Lookup in der Material-DB: ID (5-stellige `base.id` bzw.
  6-stelliger RFID-Code) → exakter Name → Materialtyp `base.meterialType`
  (mehrere IDs → Eintrag „Generic <Typ>“, sonst Fehler „mehrdeutig“) →
  eindeutiger Namens-Teilstring. **Ohne Material-DB** wird nur die
  Bezeichnung gespeichert und `TEMP=` ist Pflicht (bewusst kein 220-°C-
  Rückfall für das neue Material).
- **Temperatur:** `TEMP=` überschreibt die DB-Temperatur; Grenzen
  `min_extrude_temp` … min(`[extruder] max_temp` − 10, `[k2_safety]`-
  Düsengrenze), ohne beides 350 °C; außerhalb → G-Code-Fehler. Beim Laden
  wird `TEMP=` mit dem Material gespeichert (`SET_EXTERNAL_MATERIAL TEMP=0` = zurück
  auf den DB-Wert), beim Entladen gilt es **nur für dieses Entladen**.
  Entladen ohne `TEMP=`: gespeichertes externes Material, sonst
  Düsen-Material (`remain_material`, Stock `FILAMENT_RACK_SET_TEMP`), sonst
  Meldung „MATERIAL=… oder TEMP=… angeben“. DB-Temperaturen werden auf die
  Obergrenze gekappt.
- **Spülen (`KTWO_BOX_EXTERN_FLUSH`)** = dieselbe Rechnung wie beim
  CFS-Wechsel (`plan_flush_lengths`, Farbweg, `nachbau/flush.py`), Vorgänger
  = `remain_material`; live belegt: (147 + 183)/2,4 → `[90, 70]` und bei
  gleicher Farbe V 60 → 101,25 → 160 mm = `[90, 70]` (zweites Laden). Spültemperatur
  max(alt, neu) mit maxTemp-Stufe wie beim CFS (mit DB, alt ≥ neu; live 240 →
  220 °C); gleiches Material in der Düse übernimmt die Hand-Temperatur.
  **Geschwindigkeit wie Stock `FILAMENT_RACK_FLUSH`**: max_vol/2,4·60 **ohne**
  360-Grenze (live 18 mm @ 575, 72 mm @ 690, Rest @ 575), ohne Wert 360 –
  anders als der CFS-Wechsel (min 360). Abschnitte 20 %/80 %·1,2, Blasen,
  Blech, Silikon-Kreise am Ende wie §12; keine Slicer-Matrix, keine
  Messrad-Prüfung (CFS bewegt sich nicht). Danach `remain_material` =
  externes Material (ohne Slot).
- **Kopfsensor** ab `EXTERN_FLUSH`/`EXTERN_HEAT` gesperrt (Upstream meldet
  sonst auch außerhalb eines Drucks ein Runout – `idle_timeout` „Printing“),
  am Ende bzw. bei jedem Fehler wieder wie vorher.
- **`KTWO_BOX_EXTERN_HEAT`** heizt nur bei Filament am Kopfsensor (Stock),
  unbekannt → heizt; **`KTWO_BOX_EXTERN_RETRACT`** `G91 / G1 E-10 F360 / G90 /
  M400` nur bei Filament (Stock `QUIT_MATERIAL_RETRUDE_MATERIAL`); der
  Schnitt läuft immer (Stock). **Abweichung:** vor dem Schnitt verlässt der
  Kopf den Schacht (X160 → Y345, wie PAUSE) – `KTWO_BOX_CUT` fährt sonst
  schräg vom Schacht zum Schneidhebel.
- **`KTWO_BOX_EXTERN_END`** = Stock `SET_COOL_TEMP`: in der Pause zurück auf
  das Ziel vor dem Vorgang (Pausentemperatur 140 °C), sonst `M104 S0` (nur
  wenn der Vorgang geheizt hat); Kopfsensor und G-Code-Zustand zurück.
  **Abweichung:** `_PI_PARAM.hotend_temp` bleibt der Wert aus PAUSE (Stock
  übernimmt das aktuelle Ziel) – RESUME heizt auf die Drucktemperatur der
  Datei.
- **Fehler:** außerhalb eines Drucks Düse aus (`M104 S0`), Kopfsensor und
  G-Code-Zustand zurück, G-Code-Fehler; in der Pause `chain_error` (RESUME
  gesperrt bis `KTWO_BOX_ERROR_CLEAR`, danach Kopfsensor-Prüfung mit Hinweis
  `LOAD_FILAMENT`), Düse auf die Pausentemperatur.
- **Freigaben** unverändert: `[k2_box] allow_move` (sonst nur PLAN, kein
  G28), `_PI_FREIGABE allow_move` (sonst Fehler vor jeder Bewegung); kein
  `allow_write` nötig.

### 18.5 Externes Material von Hand: `SET_EXTERNAL_MATERIAL` / `KTWO_BOX_EXTERN_MATERIAL`

`[MATERIAL=<Name|Typ|ID>] [TYPE=<id>] [COLOR=RRGGBB] [TEMP=°C|0] [CLEAR=1]` –
anzeigen (Material, Farbe, wirksame Temperatur mit Herkunft) bzw. setzen wie
beim Laden; sendet und bewegt nichts; während eines Vorgangs gesperrt.

### 18.6 RFID der externen Spule (optional, Default AUS)

- Stock: Sensor `!PA5` (Haupt-MCU) am externen Halter → **ein** Lesebefehl
  `0x11 [02]` an den RFID-Leser 0x11 (Firmware-Analyse, live beim Laden
  bestätigt).
- Pi: `[k2_box] extern_rfid: True` **und** in `printer.cfg` den
  auskommentierten `[gcode_button extern_spule]` (`pin: !PA5`, `press_gcode:
  KTWO_EXTERN_RFID`) einkommentieren. **`KTWO_EXTERN_RFID`** sendet genau
  einen Frame `0x11` fun `0x02` ohne Daten (Status 0xFF, `retries=0`, 0,5 s)
  über den unveränderten Sendeschutz (`check_tx_frame`: an 0x11 nur
  0x02/0x14). Antwort wie Stock `ret[5:-1]` (`nachbau/filament_rack.
  rfid_from_response`), 40 Zeichen, Felder wie der CFS-vender
  (`rfid_material`). Gültig → gespeichertes externes Material = Material-ID +
  Farbe (Name aus der DB, Hand-Temperatur verworfen); unlesbar/leer/Timeout →
  Meldung, Material bleibt. Gemeldet/gespeichert werden **nur** Material-ID
  und Farbe. Ohne Freigabe nur Meldung „aus“, während eines Vorgangs nichts.
- **ANNAHME:** Pegel `!PA5` ohne Pull-up wie Stock (am Gerät mit
  `QUERY_BUTTON` prüfen).

### 18.7 Status (`printer.k2_box`)

`source`, `source_file`, `source_load` (aus|ungelesen|fehlt|geladen|fehler),
`source_info`, `extern_material` (dict), `extern_material_label`,
`extern_tool` (−1 = keins), `extern_active`, `extern_op`, `extern_last`,
`extern_rfid`, `extern_rfid_last`.

### 18.8 Tests und Batch

- `test_k2_box_extern.py` (neu, 14 Prüfungen): Quellen-Datei, `KTWO_BOX_SOURCE`,
  alle Sperren mit „nichts gesendet“ (Fake-Bus-Frames gezählt), Bus-Mitschnitt
  eines Drucks mit externer Spule (genau `0x0d [0f 00]`, `0x0f [01]`, `0x0a`,
  `0x0d [0f 01]`, `0x0f [00]`; Ausnahme nur für diese Frames), T<n>-Logik,
  Makros (Jinja2), Laden/Entladen als ausgeführte Makro-Skriptfolge mit den
  Live-Werten, Kopfsensor-Prüfung, Material-Lookup, `TEMP=`-Grenzen,
  ohne DB, Plan, Fehler, Pause, RFID. `test_k2_box.py` unverändert 74/74.
- Lokaler Upstream-Batch (Upstream 9cb9975, simulierte Motoren + RFID-Leser):
  Laden/Entladen/RFID/Quellwechsel + Sperrprobe sowie ein SD-Druck mit
  `KTWO_BOX_PRINT_START`, `T0`, `T0`, `M8200 L I0`, `END_PRINT`, `T1` (Box-
  Frames genau wie Stock live, kein Entladen, T1 → PAUSE) → alle Prüfungen
  grün, 0 Internal error; Upstream nimmt `[gcode_button extern_spule]`
  `!PA5` an.

### 18.9 Live am Gerät (26.10.2026) und Offenes

Ablauf mit echtem Schlauch (Kopf vorher leer, Quelle CFS, ohne Material-DB):

| Schritt | Ergebnis |
|---|---|
| `SET_FILAMENT_SOURCE SOURCE=EXTERNAL` | Quelle `extern`, Meldung mit allen Sperren; nichts gesendet, keine Bewegung |
| Schlauch von Hand von der CFS auf die externe Spule, PLA bis zur LED geschoben | Kopfsensor `filament_detected: true` |
| `LOAD_FILAMENT MATERIAL=PLA TEMP=230` | `G28 X Y` (nicht referenziert), Schacht, Spülplan 160 mm (90 + 70; Vorgänger unbekannt → Stock-Fallback 140 mm), F360 (ohne DB Stock-Fallback `[filament_rack] velocity`), 230 °C; „Externe Spule geladen (PLA, Farbe unbekannt, Handwert 230 °C)“ |
| Einfarb-Druck (eine Schicht, 4,5 min) | `KTWO_BOX_PRINT_START` 0x0d [0f 00] + 0x0f [01]; `G28 Z` wartete, bis die Düse vom Laden (189 °C) auf 140 °C abgekühlt war; Z-Ausgleich und adaptives Netz normal; `T0` aus der Datei: „Quelle EXTERN – erstes Werkzeug dieses Drucks, kein Wechsel“; Druck fertig |
| `END_PRINT` | „Quelle EXTERN – kein Entladen in die CFS (kein Schnitt, kein Rückzug)“, `KTWO_BOX_PRINT_END` 0x0d [0f 01] + 0x0f [00] – wie Stock |
| `UNLOAD_FILAMENT` | Schacht, 230 °C (Handwert), Schnitt, E−10 F360, Abkühlen, „Filament jetzt von Hand herausziehen“ – ließ sich aber **nicht** herausziehen: der bestromte Closed-Loop-Extruder hält seine Position, die Zahnräder klemmen das Filament. Nach `SET_STEPPER_ENABLE STEPPER=extruder ENABLE=0` sofort heraus (Kopfsensor leer, `head_cut` dadurch zurückgesetzt). **Fix:** `UNLOAD_FILAMENT` gibt am Ende den Extruder frei (nur ihn – eigene Freigabeleitung, X/Y/Z bleiben bestromt, das Bett sackt nicht ab); beim nächsten Extrudieren schaltet Klipper ihn wieder ein |
| `SET_FILAMENT_SOURCE SOURCE=CFS` (Schlauch zurück an der CFS) | Quelle `cfs`, nichts gesendet |

Hinweis: In Orca Slicer ist für die externe Spule nichts einzustellen – die
Quelle wird am Drucker gewählt, jedes `T<n>` der Datei geht auf die externe
Spule (einfarbig: durchgehend; mehrfarbig: PAUSE beim ersten anderen
Werkzeug). Gedruckt wird mit der Temperatur aus der Datei; der Handwert gilt
nur für Laden/Spülen/Entladen.

**Nicht geplant:** Mehrfarb-Dateien mit der externen Spule (Spule bei jedem
Werkzeugwechsel von Hand tauschen). Die PAUSE beim ersten anderen Werkzeug
(§18.3) bleibt nur als Schutz bestehen – ohne sie liefe eine Mehrfarb-Datei
stillschweigend einfarbig durch –, wird aber nicht weiter ausgebaut und nicht
am Gerät getestet. Mehrfarbig wird über die CFS gedruckt.

Offen: Spülgeschwindigkeit 575/690 mit der echten Material-DB (hier ohne DB:
F360), Sperrprobe `KTWO_BOX_LOAD` bei Quelle EXTERN (kein Frame), optional
RFID über `!PA5`.

## 19. Creality-Print-7-Dateien (Ergänzung 22.10.2026)

Creality Print 7.3 (Profil K2 Plus, CFS) schreibt jeden Wechsel als Kette
`G1 X0 Y245` → `M8200 P S<n>` → `M8200 R E-30` → Fahrt `X10 Y200` →
`M8200 C S0` → Fahrt zum Schacht → `M8200 R` → `M104 S240` → `M8200 L I<n>` →
`G1 E40` → `T<n>` → **eigenes Spülen der Datei** (`; FLUSH_START` …
`; FLUSH_END`, Blech/Kreise am Schacht, am Ende `M104 S220`) → `G0 X160` /
`G0 Y345` → `M8200 O`. Startblock genauso, aber ohne `M8200 R E-30` und mit
leerem Kopf (Schnitt ins Leere wie Stock).

| Befehl | vorher (bis 21.10.) | jetzt | Beleg |
|---|---|---|---|
| `M8200 R E-30` | `KTWO_BOX_RETRUDE LENGTH=30` = Schacht, M109, p0 ×4 + **p1 (voller Rückzug VOR dem Schnitt → live 0x19-Gefahr)** | `KTWO_BOX_BREAK_RETRUDE LENGTH=-30` = 0x04 IDLE, 0x11 [m 00], 0x04 IDLE, `G1 E-30 F300` – Filament bleibt im Kopf | Stock `cmd_CR_BOX_RETRUDE`: LENGTH≠0 → nur `break_box_extruder_retrude` und Ende |
| `M8200 R` | voller Rückzug (Hub war durch E-30 schon leer → übersprungen) | voller Rückzug nach dem Schnitt (p0 ×4 + E-10, p1) | wie bisher |
| `T<n>` nach `M8200 L I<n>` | `PRE_OPT` räumte die offene M8200-Kette ab („bereits geladen“) | `_KTWO_TOOL_CHANGE`: Kette offen, Ziel = Kettenziel = geladen, kein Fehler → nur Meldung, Kette bleibt bis `M8200 O` | Stock `Tn_action` (last_cmd == tnn) |
| `M8200 O` | `END_OPT RETURN=0` setzte die Zieltemperatur auf das Ziel vor dem ersten Heizen zurück (Startblock: 240 statt der von der Datei gesetzten 220) | Hat die Datei nach dem letzten Heizen der Kette selbst ein anderes Ziel gesetzt, bleibt es (nur `RETURN=0`; T<n>-Kette unverändert) | Datei-Ablauf |

Unverändert: Orca-Dateien (`T<n>` ohne M8200), alle übrigen M8200-Fälle,
Schnittschutz (die Kette schneidet vor dem vollen Rückzug), WASTE nur Hinweis.
Kein zusätzliches Spülen durch k2_box (kein `M8200 F` in CP7-Dateien) – die
Datei spült selbst; `remain_material` wird dabei nicht aktualisiert.
Test: `test_cp7_wechselblock` (Start- und Wechselblock 1:1, Jinja-Makros +
Modul, Fake-Box; zusätzlich Einzelbefehl: Hub leer, LENGTH 0, kalt, Plan).
Fake-Box seit 22.10.: 0x11 p0 räumt den Hub nicht mehr (nur p1, wie die
CFS-Firmware).


## 20. CFS-Klima in Mainsail (Ergänzung 22.10.2026)

Die CFS-Box misst Temperatur und relative Luftfeuchte der **Luft in der Box**
(nicht des Filaments). k2_box liest beide Werte ohnehin beim 0x0a-Poll
(`temp_c` = int8 °C, `rh` = %; `KTWO_BOX_STATUS` „Temp=… Feuchte=…“). Das
eigene Modul `k2_cfs_sensor.py` macht daraus einen Klipper-Temperatursensor –
**ohne zusätzlichen Bus-Verkehr**, nur mit den zuletzt gepollten Werten:

```
[k2_cfs_sensor]            # registriert sensor_type k2_cfs – VOR dem Sensor
#box_object: k2_box
#report_time:              # leer = poll_interval von [k2_box] (4 s)
#max_age: 60               # s ohne frischen Poll -> veraltet

[temperature_sensor cfs_box]
sensor_type: k2_cfs
min_temp: -10
max_temp: 80
```

- **Eigenes Modul statt Registrierung in k2_box:** Der Sensortyp muss beim
  Laden des temperature_sensor-Abschnitts bekannt sein. Hinge er an
  `[k2_box]`, bräche ein Klipper-Start ohne `[k2_box]` mit „Unknown
  temperature sensor“ ab. So startet Klipper auch ohne Box.
- **Kein gültiger Wert** (Box offline/nicht gepollt, Poll älter als `max_age`,
  Temperatur außerhalb min/max, Feuchte > 100 %): Der Klipper-Callback wird
  nicht aufgerufen – keine Ersatz-Temperatur, **kein Shutdown** (der Sensor
  regelt nichts; ein falsch gelesenes Statusbyte soll keinen Druck abbrechen).
  Der Sensor zeigt dann seinen letzten Wert; Zustand und Grund stehen in
  `printer["k2_cfs_sensor"].sensors.cfs_box` (`valid`, `reason`, `age`).
- **Feuchte in Mainsail:** Mainsail zeigt Zusatzwerte nur für Objekte
  `<typ> <name>` mit typ aus aht10/aht1x/aht2x/aht3x/bme280/htu21d/sgp40/
  sht3x/temperature_combined. Darum registriert der Sensor zusätzlich
  **`aht10 cfs_box` – einen reinen Anzeige-Alias** (kein AHT10-Chip, kein
  Treiber) mit `temperature` + `humidity`; bei ungültigem Wert leer. Gibt es
  das Objekt schon, entfällt der Alias.
- Deploy: `k2_cfs_sensor.py` nach `klippy/extras/`, beide Abschnitte in die
  `printer.cfg` des Pi (nach `[include k2-module.cfg]`), `systemctl restart
  klipper` (neues Modul).
- Test: `test_k2_cfs_sensor.py` (25 Prüfungen: Factory, Takt, Wert + Alias,
  ohne Box/Poll, Lesefehler, außerhalb min/max, veraltet, Alias-Kollision,
  Py 3.9, keine `stats`-Methode).

## Konsole (Audit 23.10.2026)

- Start-Meldungen (Material-DB, Düsen-Material-/Slot-/Quellen-Datei, Bus
  fehlt, Auto-Addr nach `k2_motor:ready`) kamen beim Connect nie in Mainsail an
  → jetzt kurz nach „ready“; kaputte Dateien `// [k2_box] WARNUNG …`, Bus fehlt
  bzw. Auto-Addr-Fehler `!! [k2_box] …`.
- 0x0a-Poll-Timer (vorher Fehler still verschluckt):
  - `// [k2_box] CFS-Box antwortet nicht (N Abfragen in Folge …)` nach
    `POLL_WARN_FAILS` = 3 Fehlschlägen in Folge, höchstens alle 30 min,
    Entwarnung „CFS-Box antwortet wieder“.
  - `!! [k2_box] CFS meldet Fehler 0x.. <Name> (Box leuchtet rot) – Ursache
    beheben, dann KTWO_BOX_ERROR_CLEAR` beim Wechsel in den Box-Zustand ERROR,
    einmal je neuem Code; still, wenn ein Wechsel-Fehler schon gemeldet ist.
- Aufräumen in Fehlerpfaden: scheitert das Zurücksetzen von Beschleunigung,
  Bauteillüfter oder Kopfsensor → `// [k2_box] WARNUNG … nicht zurückgesetzt`;
  scheitert die Freigabe der M8200-Fahrgrenzen → `!!`; scheitert `M104 S0` nach
  einem Fehler mit externer Spule → „FEHLGESCHLAGEN, Düse von Hand ausschalten“
  in derselben roten Meldung.
- `K2BoxController` bekommt statt des gcode-Objekts einen `LogAdapter`
  (dessen `log.info/error` liefen vorher ins Leere).
- `[k2_cfs_sensor]`: Lesefehler und Wechsel gültig → ungültig als
  `// [k2_cfs_sensor] …` (höchstens alle 30 min; ein Sensor ohne Box von Anfang
  an meldet nichts).

Regeln, Format und Gesamttabelle: `README-k2_konsole.md`.

## Wiederanmeldung nach Box-Stromverlust (26.10.2026)

**Befund am Gerät:** CFS-Box während eines Wechsels (Rückzug A → B)
abgezogen und wieder eingesteckt. Pause, Rückfahrziel und RESUME-Sperren
arbeiteten wie vorgesehen, die Box blieb aber bis zum nächsten Klipper-Start
stumm (`addr 0x01 fun 0x0a: keine Antwort`, 60+ Abfragen). Ursache: Nach dem
Stromverlust startet die Box neu (zuerst im Bootloader) und hat keine
Bus-Adresse mehr. Stock (`auto_addr_wrapper` `process_all`) fragt laufend per
0xA1-Broadcast nach Geräten, erkennt die bekannte UID als „slave restarted“
und vergibt die Adresse per 0xA0 neu; der Pi lief `online_once()` bisher nur
einmal nach `k2_motor:ready`.

**Umsetzung:** Scheitert der 0x0a-Poll `POLL_WARN_FAILS` (3) Mal in Folge,
startet der Poll-Timer denselben Auto-Addr-Durchlauf (`readdr()`): 0xA2 an die
bekannte Adresse (nach mehr als 3 Fehlversuchen „offline“), 0xA1-Broadcast an
0xFE, 0xA0 mit der gespeicherten UID, steht die Box im Bootloader 0x0B [01] an
0xFF (die Motor-App ignoriert [01]). Takt wie Stock: höchstens alle 1 s, im
Druck oder in der Pause alle 10 s – praktisch mit jedem Poll (4 s) bzw. alle
10 s. Nicht während langer Box-Vorgänge (`busy`), ohne allow_write nie (0xA0
ist schreibend). Konsole:

- `// [k2_box] CFS-Box: Wiederanmeldung läuft (Auto-Addr wie Stock: …; alle
  N s, bis die Box antwortet)` – einmal je Ausfall,
- `// [k2_box] CFS-Box antwortet wieder (nach N Fehlschlägen; neu
  angemeldet)`.

Jeder Durchlauf steht mit Tabellenzustand im klippy.log
(`[k2_box] Wiederanmeldung N: …`).

**Live 26.10.2026 (Leerlauf):** Box abgezogen → nach 3 Abfragen Warnung und
Beginn der Wiederanmeldung; Durchläufe 1–3 Verlustzähler 1–3, Durchlauf 4
„offline“, Durchlauf 5 Antwort im Bootloader (`mode 1`) → 0xA0 + 0x0B [01],
Durchlauf 6 App-Modus, online → „CFS-Box antwortet wieder (nach 8
Fehlschlägen; neu angemeldet)“ etwa 20 s nach dem Einstecken, ohne Neustart.
`KTWO_BOX_STATUS` danach: IDLE, Temperatur/Feuchte normal.

**Abbruchtest im Druck (26.10.2026, vor diesem Fix):** Box beim Rückzug zu B
abgezogen → nach dem 150-s-Timeout des Rückzugs (0x11, retries 0) PAUSE mit
Rückfahrziel = Druckposition vor dem Wechsel; `RESUME` mit offenem
Wechsel-Fehler gesperrt; nach `KTWO_BOX_ERROR_CLEAR` weiter gesperrt
(Kopfsensor leer, `resume_redo_change: False`) – jeweils ohne Bewegung. Der
pausierte Druck ließ sich ohne die Wiederanmeldung nicht fortsetzen
(Neustart nötig) und wurde abgebrochen.

**Abbruchtest im Druck mit Wiederanmeldung (26.10.2026, Teil 2):** Box beim
Wechsel A → B abgezogen → PAUSE nach dem Rückzug-Timeout; Box eingesteckt →
„antwortet wieder (nach 12 Fehlschlägen; neu angemeldet)“ in der Pause, ohne
Neustart; `KTWO_BOX_ERROR_CLEAR`, `T1` in der Pause (A zurück, B geladen,
gespült), `RESUME` (Vorextrusion, Reinigung, Rückfahrt) – danach brach der
Druck mit „Move out of range 160/374“ ab. Ursache und Fix: nächster Abschnitt.

## Abbruch im M8200-Block (Creality Print, 26.10.2026)

Creality-Print-Dateien wechseln in einem Block `M8200 P` … `M8200 O`; darin
fährt die Datei selbst in den Schacht (Y374–378: eigene Vorextrusion
`G1 E40`, `T<n>`, Spülen, Wischen). k2_box hält dafür die Fahrgrenzen der
M8200-Kette bis `M8200 O` weit. Drei Lücken (Live-Befund oben):

1. **Fahrgrenzen nach RESUME:** Die Pause kam mitten im Block. `T1` in der
   Pause (eigene Kette `SOURCE=T`) schloss die M8200-Kette samt Fahrgrenzen
   (ebenso setzt jeder G-Code-Fehler sie zurück, `[k2_limits]`). Nach RESUME
   lief die Datei im Block weiter → die eigene Wischfahrt auf Y374 lag außerhalb
   des Betriebsfensters → Druck abgebrochen. Zusätzlich setzt JEDES RESUME
   die Fahrgrenzen zurück (Reinigung/Rückfahrt rufen `KTWO_LIMITS_RESTORE`
   = alle Halter weg) – auch eine Hand-PAUSE im Schacht-Teil des Blocks
   endete so. **Fix:** Das Makro `M8200 P` kennzeichnet seine Kette
   (`KTWO_BOX_PRE_OPT … SOURCE=M8200`). Bricht sie im laufenden Druck ab oder
   schließt ein T<n>/Runout in der Pause sie, merkt k2_box das Ziel
   (`_m8200_offen`). Am Ende JEDES RESUME (`KTWO_BOX_RESUME_FIX`) mit noch
   offener bzw. gemerkter M8200-Kette: Kette offen halten bzw. mit diesem
   Ziel wieder öffnen, Kopfsensor wie PRE_OPT sperren (`M8200 O` gibt ihn
   frei), Fahrgrenzen weiten. Ist das Ziel schon geladen (Wechsel in der
   Pause erledigt), laufen die restlichen Schritte des Blocks leer (`skip`) –
   sonst schnitte `M8200 C` das frisch geladene Filament bzw. zöge `M8200 R`
   es zurück; das `T<n>` der Datei läuft ebenfalls leer („schon
   eingezogen“), `M8200 O` schließt wie immer. Ein `END_OPT` in der Pause
   (Kette von Hand geschlossen) löscht den Merker nicht, ein Fehler in
   `END_OPT` setzt ihn nicht; Druckabbruch/-beginn löschen ihn. Konsole:
   `// [k2_box] RESUME: Datei steht noch im M8200-Wechselblock (bis M8200 O)
   – Kette T1B wieder geöffnet, restliche Wechsel-Schritte laufen leer,
   Fahrgrenzen zum Schacht aufgeweitet`.
2. **Rückfahrziel:** Bisher fuhr RESUME zur Druckposition VOR dem Block
   (X0 Y245). Die Datei macht aber mit den Schacht-Zeilen weiter – wäre der
   Abbruch beim Einzug (`M8200 L`) gekommen, lägen `G1 E40` und die
   Spülmenge auf dem Teil. **Fix:** In einer M8200-Kette ist das Rückfahrziel
   die Position vor dem gescheiterten Schritt (dort, wo die Datei den Kopf
   erwartet; Stock: RESUME_BASE = Fehlerposition), z. B. X10 Y200 bei einem
   Abbruch im Schnitt (`M8200 C`). Nur ohne Z-Hop (M8200 P hat `Z_HOP=0`).
   Steht der Kopf dort noch
   (z. B. Rückzug im Schacht), bekommt PAUSE kein eigenes Ziel; RESUME fährt
   über die vorhandene Schacht-Einfahrt (`_PI_RESUME_RETURN`, Y345 → X139 →
   Y → X). T<n>-Ketten (Orca) unverändert: Druckposition vor dem Wechsel.
3. **Abgeschnittener Faden:** Nach einem Schnitt meldet der Kopfsensor
   weiter Filament, solange der Rückzug fehlt – das Stück über dem Messer hängt
   aber nicht mehr am Extruder. Live meldete der Sensor nach dem Abbruch
   „Filament“; `KTWO_BOX_ERROR_CLEAR` + `RESUME` hätte ohne Förderung
   weitergedruckt (Stock prüft das ebenso wenig). **Fix:** `head_cut` (im
   Speicher, `get_status`): an schon VOR der Schneidfahrt (scheitert das
   Skript nach dem Hebelstoß, ist trotzdem geschnitten; im Trockenlauf
   zurück), aus nach Rückzug, Entladen, Einzug (`KTWO_BOX_EXTRUDE`/`LOAD`),
   Ausspülen, Spülen der externen Spule, `KTWO_BOX_ERROR_CLEAR
   HEAD_LOADED=1` und sobald der Kopfsensor leer meldet. RESUME nach einem
   Abbruch behandelt „Filament + head_cut“ wie „Kopf leer“ (gesperrt bzw.
   Nachholen mit `resume_redo_change`); die Sperrmeldung nennt `T<n>`, als
   Kettenschritte `KTWO_BOX_RETRUDE` + `KTWO_BOX_EXTRUDE` und nach Prüfung
   von Hand `KTWO_BOX_ERROR_CLEAR HEAD_LOADED=1`. Folge für den bisherigen
   Fall „Abbruch am Schneidhebel, Kopf voll“: jetzt gesperrt statt frei.

Test: `test_m8200_abbruch_resume` (Datei-Ablauf über macros.cfg mit echter
Fahrgrenzen-Logik): Box beim Rückzug stromlos → Sperre, `T1` in der Pause,
RESUME (inkl. zurückgesetzter Grenzen), Rest des Blocks ohne Box-Frame bis
`M8200 O`, Sensor wieder frei; Abbruch im Schnitt → Ziel X10 Y200, kein
zweiter Schnitt/Wechsel; Hand-PAUSE im Block; END_OPT in der Pause bzw.
END_OPT-Fehler. Gegenprobe: ohne Leerlauf bzw. ohne Aufweitung schlägt der
Test an.

4. **Kalter Rückzug in der Pause:** Live beim ersten `T1` in der Pause: Der
   Faden war beim Abziehen schon am Kopfsensor vorbei, der Kopf also leer –
   `RETRUDE` heizt dann nicht (Stock: nur mit Filament), führte aber die
   Extruder-Schritte aus → bei 140 °C Pausentemperatur „Extrude below minimum
   temp“, Wechsel abgebrochen. **Fix:** Kopf leer und Düse unter der
   Mindesttemperatur (`can_extrude` False) → nur der Box-Rückzug, ohne
   Extruder-Schritte (`// [k2_box] RETRUDE: Kopf leer und Düse unter der
   Mindesttemperatur – Extruder-Rückzug ausgelassen`). Im laufenden Druck
   (Düse heiß) unverändert. Test `test_retrude_kopf_leer_kalt`.
5. **Doppeltes Spülen:** Live wurde B in der Erholung viermal gefördert:
   Spülen des `T1` in der Pause (160 mm), Vorextrusion des RESUME (82 mm),
   danach `G1 E40` und das eigene Spülen der Datei (90 + 70 + 70 mm). **Fix:**
   Lädt ein `T<n>` in der Pause dasselbe Ziel wie der unterbrochene
   M8200-Block, lässt `KTWO_BOX_FLUSH` das eigene Spülen aus (`// [k2_box]
   FLUSH T1B ausgelassen: die Datei … spült nach RESUME selbst im Schacht`).
   Anderes Ziel bzw. `LENGTH=` von Hand: Spülen wie bisher. Die Vorextrusion
   des RESUME bleibt (wie Stock nach jeder Pause).

**Live 26.10.2026 (Abbruchtest Teil 2, Fix 1–3 aktiv):** Box beim Wechsel A →
B abgezogen → PAUSE ohne Rückfahrziel zur Druckposition (Kopf stand im
Schacht); Box eingesteckt → neu angemeldet; `RESUME` bei leerem Kopf
gesperrt; `T1` in der Pause (erster Versuch: Fehler 4, nach `M109 S220`
erfolgreich); `RESUME` → Rückfahrt in den Schacht X133 Y378, „Kette T1B wieder
geöffnet, restliche Wechsel-Schritte laufen leer, Fahrgrenzen … aufgeweitet“,
`T1` der Datei „schon eingezogen“, eigenes Spülen/Wischen der Datei auf
Y374/378 ohne Fehler, `M8200 O`: „T1B schon geladen … nichts zu tun“,
Fahrgrenzen wieder eng, Kopfsensor wieder an → Druck lief auf dem Teil weiter,
folgende Wechsel normal. Fix 4–5 danach offline (Tests), live noch offen.
