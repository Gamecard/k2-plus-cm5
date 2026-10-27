> **ENTWURF – am echten Gerät UNGETESTET (Stand 16.10.2026).** Klipper-Zusatzmodul für Upstream-Klipper auf einem Pi-Host; nur offline mit simulierter MCU/Motoren getestet (`python3 test_k2_motor.py`, 17/17 seit Sensorless-Homing §9) und im Batch-Modus gegen Upstream-Klipper 9cb9975 ohne Testpatches (config-pi/README.md §5, Lauf F–I). Upstream-Fix 16.10.2026 abends: §8. Entwurf durch einen weiteren KI-Agenten, Review und drei Korrekturen (Pins vor Connect, gültige print_time, query_gpio_status) durch uns.

# Klipper-Zusatzmodul [k2_motor] – Bericht (Entwurf, 16.10.2026)

> **Kalibrierungen** (warum, welche Fehler sie beheben, Reihenfolge, Risiken): `KALIBRIERUNG.md`

Gearbeitet AUSSCHLIESSLICH auf Deutsch,
ohne Netzwerk, ohne ssh/curl, ohne Zugriff auf den 3D-Drucker. Neue Dateien NUR
in ``pi-host/``, alles andere
nur gelesen.

## 1. Ergebnis-Übersicht

| Datei | Zweck | Stand |
|---|---|---|
| `k2_transport.py` | Zwei Transporte mit gleicher Schnittstelle `transact(addr, fun, data, timeout, retries) → (status, fun, data)`: `Rs485Transport` (X/Y/Z/Z1) + `TransparentTransport` (E via Düsen-MCU, volle Frames mit `serial485.build_frame`/`parse_frame`, CRC + Absenderprüfung, retries=0 wie Stock) | neu, getestet |
| `k2_motor_init.py` | Klipper-Modul `[k2_motor]`: Config wie Stock-`[motor_control]` + `allow_write`/`shutdown_on_failure`/`auto_init`/`nozzle_mcu`/`main_mcu`; Ablauf P0–P9 wie Stock; E-Param via transparent 0x81 (Stock-Bug 0x85 NICHT nachgebaut, Guard im Code); Fehler analog key798–803 (Shutdown nur per Option); Event `k2_motor:ready`; G-Codes `KTWO_MOTOR_INIT`/`KTWO_MOTOR_STATUS`, Kalibrierwerte sichern/prüfen `KTWO_MOTOR_CAL_BACKUP`/`_CHECK` (nur lesend, §13), Kalibrieren `KTWO_MOTOR_CAL_RUN`/`_RESTORE`/`_ENABLE` (Default aus, §14) | neu, getestet |
| `test_k2_motor.py` | `FakeMcu` (transparent + digital_out) + `FakeRs485Bus` (0x81–0x84 inkl. Broadcast/Strapping-Guard, Param mit Echo) → kompletter Init-Lauf; Plan-Vergleich gegen `motor_init.build_init_plan`; E-nur-transparent; allow_write=False; Live-Frame-CRC; dazu `FakePrinter`-Lebenszyklus (Review 1–3: Config-vor-connect, print_time>0, query+Fallback) | neu, **9/9 ok** (s. §6) |
| `README.md` | diese Datei (inkl. §6 Review-Korrekturen) | neu |

Testbefehl (ohne Klipper, ohne Hardware):

```bash
cd pi-host
python3 test_k2_motor.py
```

Ergebnis am 16.10.2026 (Review-Stand): `ERGEBNIS: ok (9/9 ok)` – Details in §5 (Erststand 6/6) + §6 (Review).

## 2. Architektur

```
printer.cfg [k2_motor] ──► PrinterK2Motor (Klipper-Hülle, load_config)
                               │ parse_klipper_config() → parse_options() (rein, testbar)
                               ▼
K2MotorController (reine Logik P0–P9, ohne Klipper importierbar)
   ├─► Rs485Transport ──► bus (serial485-artig, /dev/ttyS5, 230400)
   │       transact() → (status, fun, data), bevorzugt transact_full()
   ├─► TransparentTransport ──► TransparentMcuAdapter.exchange()
   │       build_frame() → transparent_send(timeout_ms) → parse_frame()
   │       bus = Objekt 'k2_485' (Modul k2_485.py, EIN Port für Motoren/CFS/Belt)
   └─► GpioOps (set_pin/query_pin) ──► KlippyGpio (pins.setup_pin digital_out,
           max_duration 0 wie Stock; optional query_gpio_status, wenn
           Creality-MCU-FW vorhanden)
```

**Wiederverwendung statt Neuerfindung:** Phasen, Reihenfolge, Payloads, Pausen
(0,2/0,1/0,05/1,0/0,1/1,0/5 s), Timeouts (0E/0C 0,05 s, sonst 1,0 s,
transparent Broadcast ≈2 s), Versuche (Stall ≤3, Param lesen/schreiben ≤3,
GPIO-Resend 3, Runden ≤retries=4), Toleranz (`isclose(rel_tol=1e-9,
abs_tol=1e-6)`), Filter (`configurable ∩ Config` + immer 9/10/25, nie 226,
nicht-Float nur lesen) und Strapping-Formel (`0x81 + 2·STEP + DIR`) sind ein
direkter Port von `nachbau/motor_init.py` / `motor.py` (dort belegt). Der
Test beweist die Gleichheit (39 ttyS5- + 9 transparent-Frames plan-identisch).

**Transporte:**

- `Rs485Transport(bus)`: reiner Wrapper, öffnet den Port NICHT selbst
  (Upstream-Hinweis: EIN Port, EINE Queue, Halbduplex-Lock über die ganze
  Transaktion – CFS/Belt teilen sich ttyS5). Nutzt `transact_full()` wenn
  vorhanden (STATUS-Byte bleibt erhalten), sonst `transact()` (Status 0x00
  angenommen – als ANNAHME markiert).
- `TransparentTransport(mcu_adapter)`: baut IMMER den vollen Frame
  (`F7 addr len status fun data crc`, Status 0x00 wie live), `timeout_ms =
  int(timeout*1000)`, `retries+1` Versuche (Aufrufer gibt 0 wie Stock).
  `b''` (leer) → TimeoutError (Stock: Broadcast + `0C [05]` bleiben
  leer/unbeantwortet – Aufrufer wertet das als „erwartet“). Danach
  `parse_frame()` (CRC), Absender- (`read[1] == addr`, Broadcast beliebig)
  und fun-Prüfung, Rückgabe `(status=read[3], fun, data)`.

**Controller:** `K2MotorController(cfg, rs485, transparent, gpio, pause,
logger, send_event)` implementiert P0–P9 exakt wie Stock (s.
Modul-Docstring). `describe_plan()` gibt den Trockenlauf-Text aus (kein Bus).
`run_init(dry_run=False)` führt aus; bei `allow_write=False` (Default) nur
lesende Schritte + Plan (kein GPIO-Setzen, kein Broadcast/Stall/Clear/
Write/Update). `motor_status()` liest Version/Schutz/Feedback (nur lesend).
Guard: E NIE als 0x85 (Exception statt Senden).

**G-Codes:** `KTWO_MOTOR_INIT [DRY_RUN=1] [ALLOW_WRITE=1]` (Default: Plan +
Diagnose; Schreiben nur mit `allow_write: True` UND ohne DRY_RUN),
`KTWO_MOTOR_STATUS` (0x0F-Version dekodiert, 0x0C-[0b] via
`decode_protection_local`, 0x08 idx 5/6 als Float – nur lesend).

**Events:** Nach P9 `send_event("k2_motor:ready")` (statt Stock-
`auto_addr:start`; CFS-Auto-Addr wartet darauf – gleiche Reihenfolge wie
Stock wegen gemeinsamer ttyS5-Queue).

## 3. Einbindung in Upstream-Klipper

1. Dateien kopieren (vollständiger Weg: `config-pi/README.md` §3):
   - `k2_transport.py` → `klippy/extras/k2_transport.py`
   - `k2_motor_init.py` → `klippy/extras/k2_motor.py`
   - `k2_485.py` → `klippy/extras/k2_485.py` + `nachbau/*.py` →
     `klippy/extras/k2_nachbau/` (RS485-Bus für X/Y/Z/Z1; für reines
     `DRY_RUN` nicht nötig – k2_transport hat Fallback-Frame-Helfer)
   - `k2_safety.py` → `klippy/extras/k2_safety.py` (Abschnitt `[k2_safety]`
     in `config-pi/k2-module.cfg`, aktiv). Das Modul schaltet bei
     `fakeshutdown` („ADC out of range“) der Creality-MCU-FW alle Heizungen
     ab und löst einen Shutdown aus. Außerdem kappt es Solltemperaturen wie
     Stock (Düse 350, Bett 120, Kammer 60). Doku: `README-k2_safety.md`
     (18.10.2026)
   - `k2_nozzle_tempcomp.py` → `klippy/extras/k2_nozzle_tempcomp.py` (Abschnitt `[k2_nozzle_tempcomp]` in `config-pi/k2-module.cfg`, AN wie Stock): Düsen-Temperaturkompensation (Z-Zuschlag nach Stock-Formel, nur im Druck mit Netz). Doku: `README-k2_nozzle_tempcomp.md` (18.10.2026)
   - `k2_dateipruefung.py` → `klippy/extras/k2_dateipruefung.py` (Abschnitt `[k2_dateipruefung]` in `config-pi/k2-module.cfg`, AN; Makro `SDCARD_PRINT_FILE` mit `rename_existing: SDCARD_PRINT_FILE_BASE` in `config-pi/macros.cfg`): prüft jede Druckdatei vor dem Start – fremdes `printer_model` (z. B. „Bambu Lab X1 Carbon“) oder Bambu-Befehle (`M1006`, `G380`, `M620` …) ohne `printer_model` → Abbruch, bevor die Datei geöffnet wird; liest nur Ende (512 KiB) und Anfang. Abschalten bis Neustart: `KTWO_DATEI_PRUEFUNG AKTIV=0`. Doku: `README-k2_dateipruefung.md` (Vorfall 23.10.2026)
2. `printer.cfg`-Beispiel (Pins wie Werks-`motor_control.cfg` F008, Werte
   gekürzt – volle Param-Liste aus `motor_control.cfg` übernehmen):

```ini
[k2_motor]
switch: 1
motor_closed_loop: x,y,z,z1,e
retries: 4
allow_write: False          # Default: nur lesen + Plan (für echten Init True)
shutdown_on_failure: False  # True → SHUTDOWN wie Stock-key798 (sonst nur Meldung)
auto_init: True
main_mcu: mcu
nozzle_mcu: nozzle_mcu
motor_x_dir: PB9,0
motor_x_step: PB10,0
motor_x_stall: PB11
motor_y_dir: !PB7,0
motor_y_step: PB8,0
motor_y_stall: PB12
motor_z_dir: PB5,0
motor_z_step: PB6,1
motor_z_stall: PB13
motor_z1_dir: PA1,1
motor_z1_step: PB15,1
motor_z1_stall: PA10
motor_e_stall: nozzle_mcu:PB12
x_controller_pos_loop_pid_param_kp: 100
x_controller_spd_loop_pid_param_kp: 0.028
x_controller_spd_loop_pid_param_ki: 6.0
# ... restliche <motor>_<param> aus motor_control.cfg ...
# ID 226 (stall_pos_err_rad_for_slicer) DARF in der Config stehen, wird aber
# NIE gesendet (reiner Host-Parameter, Motor antwortet 0 B – wie Stock-Plan,
# der ihn bewusst auslässt).

[duplicate_pin_override]
# NÖTIG für das Zeitmultiplex: [k2_motor] nutzt dieselben DIR/STEP-Pins wie
# [stepper_*] (Strapping vor dem Broadcast, danach wieder Stepper-Betrieb).
# Synchronisation per M400 (wie Stock L3096/3164).

[k2_485]
# gemeinsamer RS485-Bus (Stock /dev/ttyS5) – X/Y/Z/Z1, CFS, Belt
serial: /dev/serial/by-id/usb-…-if00-port0
baud: 230400

[mcu]
# ... Main-MCU (Empfehlung: Creality-FW mit query_gpio_status + transparent_*,
# s. §4) ...

[mcu nozzle_mcu]
# ... Düsen-MCU mit config_transparent (Creality-FW) ...
```

3. Ablauf prüfen:
   - `KTWO_MOTOR_INIT DRY_RUN=1` → Plan ausgeben, nichts senden.
   - `allow_write: True` + `KTWO_MOTOR_INIT` → echter Init (Pins → Broadcast →
     Runden → Stall → Schutz → Clear → Restore → Param → `k2_motor:ready`).
   - `KTWO_MOTOR_STATUS` → Version/Schutz/Feedback je Motor.
4. CFS-Auto-Addr auf `k2_motor:ready` warten lassen (statt `auto_addr:start`).

## 4. Offene Punkte / Annahmen (alles Unbelegte ist als ANNAHME markiert)

**Aus der Protokollanalyse übernommene V-Punkte (V = vermutet):**

- Aufrufer `klippy._connect` (Frame-Tag) vs. `klippy:ready` – hier ANNAHME:
  Auto-Init auf `klippy:connect` (wie Stock nach Connect, transparent-Lookup
  L3089 erst dort möglich). Per `auto_init: False` abschaltbar.
- `switch=0`-Zweig endet nach Normalisierung (V) – so implementiert.
- Adresswert in `0E [02]`-Runde ungeprüft (V) – so implementiert (truthy zählt).
- `06 [03]`-Ergebnis ungeprüft (V) – gesendet, Fehler nur geloggt.
- Schutz-Seitenzweig (`FORCE_MOVE`/`M84` bei Fehlerbits, V) – NICHT
  implementiert (nur geloggt; Druckbetrieb gehört nicht in die Init).
- `motor_params_config`-Import wirkungslos (V) – weggelassen.
- Shutdown-Argumente key798 (V) – eigene Meldung + Option statt Shutdown.
- `pin108 = STEP` (V, aber alle 4 Adressen gehen auf) – Formel wie Stock.
- Restore-Zweck „wieder normale STEP/DIR-Eingänge“ (V) – so dokumentiert.

**Neue ANNAHMEN (Upstream ohne vorliegenden Quellbaum):**

- Klipper-API-Signaturen (`register_config_callback`, `create_oid`,
  `add_config_cmd("config_transparent oid=%d")`,
  `lookup_query_command("transparent_send oid=%c write=%*s timeout_ms=%u",
  "transparent_response oid=%c read=%*s", oid=...)`,
  `register_event_handler("klippy:connect"/"klippy:ready")`,
  `register_command`, `reactor.pause`, `config_digital_out`/
  `update_digital_out`, `ppins`/`output_pin`, `[duplicate_pin_override]`) –
  gegen die bekannte Upstream-API geschrieben, Kapselung in
  `KlippyTransparentAdapter`/`KlippyGpio`, im Code je Stelle mit ANNAHME
  kommentiert. Vor dem ersten echten Lauf gegen den echten Quellbaum prüfen.
- Empfohlene Variante Creality-MCU-FW + Upstream-Klipper:
  `query_gpio_status` + `transparent_*` vorhanden → Rücklesung aktiv. Mit
  reiner Upstream-MCU-FW entfällt die Rücklesung (`query_pin → None`,
  Prüfung übersprungen) UND `transparent_*` muss erst in die Firmware
  portiert werden (deutlich mehr Aufwand).
- RS485-Port teilt sich mit CFS/Belt (eine Queue, ein Lock) – hier nur als
  injizierter `bus` + Hinweis umgesetzt; Port-Manager fehlt noch.
- Timeouts 0x11/0x0F/0x08 je 1,0 s UNBELEGT (live nie Timeout beobachtet,
  `TIMEOUT_LONG`) – übernommen, im Code markiert.
- `allow_write`-Semantik (Default False → nur lesen + Plan) ist eine
  SICHERHEITS-ANNAHME dieses Entwurfs (Stock schreibt immer). Diagnose ohne
  Broadcast liefert erwartbar key798 (RS485 braucht Broadcast) – kein Fehler
  des Moduls, sondern der Diagnose-Modus.
- Neue Config-Optionen (`allow_write`, `shutdown_on_failure`, `auto_init`,
  `nozzle_mcu`, `main_mcu`) sind Erweiterungen (Defaults stock-kompatibel
  bzw. sicher). `<motor>_<param>`-Sammlung via `get_options()`/Fallback ist
  als ANNAHME implementiert (mehrere Wrapper-Formen versucht).
- `12 02`-UUID gehört NICHT zur Init (belegt) – nicht implementiert (nur
  Hinweis: Webhook `motor_dev_uuid` bei Bedarf separat).
- `MOTOR_FLASH_PARAM` (0x07) beim Boot ungenutzt – nicht implementiert.
- X/Y-Stall erst beim Homing (belegt) – P4 nur Z/Z1/E.
- E hat keine Dir/Step-Pins (nur Stall) – so modelliert.
- Float32-Rundung < abs_tol (belegt) – `_float_to_bytes` ohne Stock-Toleranz-
  Sonderfälle; non-Float-Writes bewusst inaktiv (Schreibformat unbelegt).

**Noch zu tun vor Hardware-Lauf (kein Druckerzugriff in diesem Auftrag):**

- Gegen echten Upstream-Quellbaum kompilieren (`ppins.setup_pin`-Signatur,
  `ConfigWrapper`-Methoden, `GCodeCommand.get_int` verifizieren).
- `duplicate_pin_override`-Syntax für die konkrete Klipper-Version prüfen.
- Live: Pegel vor Broadcast gegen `MOTOR_READ_ALL_PIN_IO` prüfen (Stock tut
  es per `query_gpio_status` – hier implementiert, aber live noch offen).
- Transparent-Pfad für E-Param live verifizieren (hier korrekt auf 0x81,
  Stock-Bug 0x85 entfernt – Gegenprobe am Log: Stock-0x85-Frames kommen im
  Test nie vor).
- `auto_addr`-Reihenfolge (`k2_motor:ready` → CFS) am laufenden System testen.

## 5. Testergebnis (16.10.2026, ohne Hardware, ohne Klipper)

`python3 test_k2_motor.py` → `ERGEBNIS: ok (6/6 ok)`:

1. **Transparent-Format vs. Live (CRC) – ok:** 3 Live-Requests byte-identisch
   (`f7 ff 04 00 0e 01 89` Broadcast, `f7 81 04 00 0e 02 80` E-Addr-Read,
   `f7 81 04 00 11 02 14` E-Stall) + 2 Antworten (`f7 81 04 00 0e 81 00`,
   `f7 81 0b 00 0c 00×8 fb` Schutz-8x00); `build_frame`/`parse_frame` CRC ok,
   Roundtrip via `TransparentTransport`+FakeMcu mit live-identischen
   Request-Bytes. (Gegenprobe gegen `serial485.build_frame` wenn importierbar.)
2. **Kompletter Init-Lauf – ok:** Fake-Werte mit 2 gezielten Abweichungen
   (X ID 70 300→100 wie live) → Status `ready`, `is_ready=True`, Event
   `k2_motor:ready`, 16 GPIO-Sets (8 Strapping + 8 Restore), 0 Fehler.
3. **Plan-Vergleich – ok:** Gesendete Folge == `motor_init.build_init_plan`
   (gleiche Config): **ttyS5 39 + transparent 9 Frames plan-identisch**
   (Reihenfolge + Payloads, bedingte Writes via isclose gefiltert).
4. **Extruder via transparent – ok:** E 8 Frames via transparent an 0x81
   (0E/11/0C/06 dabei); RS485-Addrs `{0xFF,0x81,0x82,0x83,0x84}` – **nie 0x85**.
5. **allow_write=False – ok:** Keine schreibenden Frames (kein Broadcast/
   Stall/Clear/Write/Update, kein GPIO-Setzen); nur Reads (16 RS485- + 4
   transparent-Versuche) + Plan-Text mit `allow_write=False`-Hinweis.
   (Erwarteter Nebeneffekt im Diagnose-Modus: key798, da RS485 ohne
   Broadcast nicht antwortet – geloggt, kein Shutdown bei Default.)
6. **KTWO_MOTOR_STATUS – ok:** 5 Motoren je Version `1.9.7.81` (live-Werte),
   Schutz ok (8x00 dekodiert), pos_fdb/spd_fdb 0.0.

Hinweis zur Ausgabe: Der Diagnose-Lauf loggt erwartbar
`FEHLER: [key798] ...` (Diagnose ohne Broadcast) – das ist die korrekte
Fehlermeldung analog Stock, kein Testfehler (Endzeile `6/6 ok` zählt).

## 6. Review-Korrekturen (16.10.2026)

Gleiche Regeln wie oben (Deutsch, kein Netzwerk/ssh/curl/Drucker). Die drei
verbindlichen REVIEW-Befunde sind vollständig umgesetzt; `python3
test_k2_motor.py` meldet jetzt `ERGEBNIS: ok (9/9 ok)` (6 alte + 3 neue
Review-Tests).

### 1. Pins zu spät angelegt – FIX (Review 1)

- Fehler: `GpioOps._output_pin` legte `ppins.setup_pin("digital_out", pin)`
  lazy zur Laufzeit an (nach `klippy:connect`). Upstream-Klipper verlangt
  MCU-Objekte/Pins im Konstruktor bzw. während des Config-Ladens
  (Konfigurationsbefehle gehen vor dem Connect an die MCU).
- Fix: `KlippyGpio.__init__(printer, main_mcu_name, pin_specs)` legt ALLE
  Strapping-Pins (aus `motor_<m>_dir/_step` für x/y/z/z1 plus Enable `!PA9`,
  wie Stock) SOFORT per `ppins.setup_pin` an; MCU-Fremdpins
  (z.B. `nozzle_mcu:PB12` E-Stall) werden übersprungen. Das alte Lazy-
  `_output_pin` ist entfernt – `set_pin` wirft `KeyError`, wenn der Pin nicht
  in `load_config` angelegt wurde. Zur Laufzeit nur noch setzen.
- Transparent analog (gleicher Lebenszyklus-Fehler): `PrinterK2Motor.__init__`
  macht `nozzle_mcu.create_oid()` + `register_config_callback(
  _build_transparent_config)` VOR connect (wie Stock-`_build_config` @0xa04fc);
  `add_config_cmd("config_transparent oid=%d")` läuft im Config-Callback vor
  finalize. `handle_connect` macht nur noch Lookups (`_tadapter.setup()` +
  `_gpio.handle_connect()`), danach optional Auto-Init.

### 2. `set_digital(0, level)` – FIX (Review 2, Entscheidung begründet)

- Fehler: `pin.set_digital(0, level)` mit `print_time=0` ist falsch –
  `MCU_digital_out.set_digital(print_time, value)` braucht eine gültige Zeit.
- Entscheidung: Variante (a) gequeue'tes `digital_out` mit gültiger
  `print_time` + M400 – NICHT Variante (b) direkter MCU-Befehl
  `set_digital_out pin=%u value=%c` (obwohl in der Creality-MCU-FW vorhanden,
  Upstream-Befehl, sofort wirksam, ohne oid). Begründung: (a) entspricht dem
  Stock (`update_digital_out` via `set_digital` + Lookahead +
  `M400`, `set_digital_out` im `.so` negativ belegt ohne einzige Referenz),
  respektiert das `duplicate_pin_override`-Zeitmultiplex mit `config_stepper`
  (M400 synchronisiert Stepper-Queue und Strapping – sofortiges Setzen würde
  die Move-Queue umgehen). Im Code als Kommentar bei `KlippyGpio` dokumentiert.
- Implementierung (gekapselt hinter `GpioOps`): `set_pin` holt
  `toolhead.get_last_move_time()` (Fallback
  `mcu.estimated_print_time(reactor.monotonic()) + 0,100 s`), prüft `> 0` und
  ruft `pin.set_digital(print_time, level)`; neu `sync()` macht
  `toolhead.wait_moves()` (Fallback `gcode.run_script("M400")`). Der
  Controller ruft `sync()` nach jedem Setzen (P1, Resend, Restore, switch=0),
  bevor Broadcast/Rücklesung folgt. Pin-Nummer für (b) wäre über
  `ppins.lookup_pin` / MCU-Enumeration `pin` auflösbar (s. §3) – nicht
  implementiert, da (a) gewählt.

### 3. `query_pin`-Stub – FIX (Review 3)

- Fehler: `query_pin` gab immer None zurück.
- Dictionary-Prüfung (Main-MCU-Dictionary `mcu-main-F008-GD32F303RET6.dict.json`
  der Creality-Firmware, nur gelesen): Befehle `query_gpio_status oid=%c gpio_pin=%c` (75) und Antwort
  `gpio_status oid=%c status=%c` (-20) vorhanden; `config_digital_out oid=%c
  pin=%u …` (24), `update_digital_out` (21), `set_digital_out pin=%u value=%c`
  (20), `config_transparent` (74), `transparent_send` (73) ebenfalls da. Aber
  KEIN passender `config_*`-Befehl für das Query-oid – daher ANNAHME: kein
  config-Befehl nötig, eigenes Query-OID via `create_oid()` FRÜH (Konstruktor),
  für alle Pins geteilt. Das Stock-Log zeigt `oid:
  11` für alle 8 Pins in beiden Phasen – ebenfalls geteilt; ob das das oid
  eines `config_digital_out` oder eines eigenen Objekts ist, bleibt als ANNAHME
  markiert (hier eigenes oid, ebenfalls geteilt – Antwort-`oid` zählt nur als
  Echo).
- Implementierung: `handle_connect` löst `lookup_query_command(
  "query_gpio_status oid=%c gpio_pin=%c", "gpio_status oid=%c status=%c",
  oid=query_oid)` auf (bevorzugt `try_lookup_query_command`); `query_pin`
  sendet `[query_oid, pin_num]` und gibt `status` (phys-Pegel, `!`-Pins bereits
  hardwareseitig berücksichtigt: `!PB7` logisch 0 → status 1, im Stock-Log
  belegt) zurück. `pin_num` via `ppins.lookup_pin` (mehrere Rückgabeformen)
  oder `mcu.get_enumeration('pin', …)` / `get_enumerations()['pin']` mit
  Basis+Offset-Logik (`PA0 [0,16]` → `PB9` = 16+9 = 25, Helfer
  `pin_number_from_enum`, ANNAHME Upstream-Muster). Falls der Befehl im
  MCU-Dictionary fehlt (reine Upstream-FW ohne Creality-Query), bleibt
  `_query=None` und `query_pin` fällt auf None zurück (Prüfung übersprungen,
  optional – wie bisher dokumentiert).

### Tests (Review-Vorgabe)

`FakePrinter`/`FakeConfigMcu`/`FakePpins`/`FakeOutputPin`/`FakeToolhead` neu in
`test_k2_motor.py` (Klipper-Lebenszyklus nachgebaut, ohne Klipper/Hardware):

- (a) Config VOR connect: `FakePpins.setup_pin`, `create_oid`,
  `add_config_cmd`, `register_config_callback` werfen NACH `fire_connect()`
  `RuntimeError` wie Klipper; `lookup_query_command` wirft VOR connect.
  Test `REVIEW 1` legt 9 Pins + Query-OID 1 + transparent-OID/Callback vor
  connect an, feuert connect (Callback liefert `config_transparent oid=1`),
  löst Query auf und prüft alle 4 Nach-Connect-Verbote.
- (b) `print_time<=0` ablehnen: `FakeOutputPin.set_digital` wirft bei
  `print_time<=0` (wie Klipper „Timer too close“); Test `REVIEW 2` prüft
  `last_print_time=10,0 (>0)` aus `toolhead.get_last_move_time()`,
  `sync()→wait_moves (1×)`, direkter `set_digital(0, …)`-Bug wird abgelehnt,
  `!PB7` logisch 0 → phys 1.
- (c) `query_gpio_status` simuliert: `FakeGpioQuery.send([oid, pin_num])`
  antwortet `{"oid","status"}` aus den `FakeOutputPin`-Pegeln (nie gesetzte
  Pins: 0); Test `REVIEW 3` prüft `PB9=0`, `!PB7=1`, `PB6=1`, Nummern 25/23
  und Fallback `None` bei `supports_query_gpio=False`.
- Integration gegengeprüft (nicht als eigener Test, manuell): Controller mit
  `KlippyGpio`+`FakePrinter` läuft `ready`, `wait_moves 2×` (P1+Restore),
  Restore-Query überall 0.

Ergebnis 16.10.2026: `ERGEBNIS: ok (9/9 ok)` – 1.–6. wie bisher (§5), 7.
Config-vor-connect, 8. print_time, 9. query+Fallback. Geändert:
`k2_motor_init.py` (`KlippyGpio` neu, `GpioOps.sync`, Controller-`sync()`-
Aufrufe, `PrinterK2Motor`-Frühconfig + `ENABLE_PIN_RAW`/`pin_number_from_enum`),
`k2_transport.py` (nur `KlippyTransparentAdapter`-Docstring: Lebenszyklus
config-vor-connect/lookup-danach), `test_k2_motor.py` (Fakes + 3 Tests).

## 8. Upstream-Fix (16.10.2026 abends, Batch-Lauf F ohne Testpatches)

- **G-Codes** `KTWO_MOTOR_INIT`/`KTWO_MOTOR_STATUS` (Upstream lehnt `K2_*` ab).
- **`pins`** statt `ppins`; Pin-Nummer für `query_gpio_status` aus der
  MCU-Enumeration (nicht `pins.lookup_pin`, das nur Namen liefert und den Pin
  als belegt markiert).
- **Config-Callback** `_build_transparent_config(self)` ohne Argument.
- **MCU-Namen** über `_lookup_mcu` (`nozzle_mcu` → Objekt `mcu nozzle_mcu`).
- **Strapping-Pins `max_duration = 0`** (`setup_max_duration(0.0)`, BELEGT
  als Stock-Aufruf in `motor_x/y/z/z1_init` + `enable_pin_pa9`). Vorher
  Upstream-Default 2 s → MCU-Shutdown, sobald ein Strapping-Pegel länger als
  2 s ansteht (Init dauert ~10 s). Batch: `config_digital_out … max_duration=0`.
- **Abfrage-OID:** Der Firmware-Handler von `query_gpio_status` (0x08003424)
  liest nur `gpio_pin` (GD32 `GPIOx_OCTL`, Ausgangs-Latch) und spiegelt die
  OID unverändert in `gpio_status` – kein `oid_lookup`, kein `config_*`.
  Daher keine `create_oid` mehr (Konstante `GPIO_QUERY_OID = 0`); die
  Rücklesung bestätigt den ausgegebenen Pegel, nicht die Leitung.
  `try_lookup_command(fmt)` (Upstream-Signatur, ein Argument) prüft vorab,
  ob der Befehl im Dictionary steht.
- **RS485 X/Y/Z/Z1** über das Objekt `k2_485` (`Rs485Transport(bus)`):
  bei vorhandenem `[k2_485]` per `printer.load_object` beim Laden, sonst
  Suche beim Connect; fehlt es → klare Meldung „Abschnitt [k2_485] … anlegen“.
  `KTWO_MOTOR_STATUS` baut den Controller bei Bedarf selbst (nur lesend).
  Bus-/Port-Fehler → G-Code-Fehler statt Shutdown.
- **Importe:** kein `sys.path.insert` mehr; `from . import k2_transport`
  (Fallback absolut für die Tests), nachbau nur angehängt (k2_transport).
- Tests: `test_k2_motor.py` 11/11 (Fakes jetzt Upstream-nah: `pins`,
  `mcu nozzle_mcu`, `cb()`, `try_lookup_command(fmt)`, `max_duration`,
  Namensregel; neu: Hülle mit/ohne `k2_485`), `test_k2_485.py` 32/32.

## 9. Sensorless-Homing X/Y (16.10.2026 spät)

Ziel: `G28 X`/`G28 Y` auf dem Pi wie Stock – die Closed-loop-Motoren X/Y
liefern ihr Stall-Signal auf PB11 (Motor x, 0x81 → `[stepper_x] endstop_pin`)
und PB12 (Motor y, 0x82 → `[stepper_y]`), aber nur in **Stall-Mode 1**.
Bedienhinweise: `config-pi/README.md`.

**Stock-Folge (live am Original-Drucker mitgeschnitten, `G28 X Y`; drei
Homing-Läufe identisch):**

| Schritt | Stock-G-Code (sensorless.cfg) | Frame (Request → Echo) |
|---|---|---|
| vor | `MOTOR_STALL_MODE DATA=1` | `81 04 00 11 01` → `f7 81 04 00 11 01 1d`, dann `82 …` |
| vor | `MOTOR_SYS_PARAM NUM=1 DATA=1 ID=70 PARAMS=300` | `81 09 00 06 01 46 00 00 96 43` → `f7 81 07 00 06 00 00 96 43 09` |
| nach | `MOTOR_STALL_MODE DATA=2` | `81 04 00 11 02` → `f7 81 04 00 11 02 14`, dann `82 …` |
| nach | `MOTOR_CHECK_PROTECTION_AFTER_HOME DATA=11` | `0C [0b]` an 81/82/83/84 (→ 8×00), E transparent `f7 81 04 00 0c 0b 95` |
| nach | `MOTOR_SYS_PARAM … PARAMS=100` | `81 09 00 06 01 46 00 00 c8 42` → `… c8 42 d4` |

ID 70 = `controller_pos_loop_pid_param_kp` (Positionsregler-Kp), NUM=1 =
nur Motor x; Motor y hat laut `motor_control.cfg` ohnehin 300. Kein `06 [03]`,
keine Pausen, retries 0, 0x11/0x06 an Z/Z1 nie beim Homing.

**Neue G-Codes** (alle schreibenden NUR mit `allow_write: True` in der
Config – kein G-Code-Override –, vorher `toolhead.wait_moves()`, Fehler =
G-Code-Fehler, nie Shutdown):

| G-Code | Wirkung |
|---|---|
| `KTWO_MOTOR_HOMING PHASE=BEGIN` | 0x11 [01] an x, y; ID 70 = `homing_pos_kp` (Default 300) an x. Fehler → Rückbau (Mode 2, Kp) + Fehler „… – keine Bewegung“ |
| `KTWO_MOTOR_HOMING PHASE=END` | 0x11 [02] an x, y; 0C [0b] an alle; ID 70 = `x_controller_pos_loop_pid_param_kp` (100). Läuft immer komplett; Schutzbits/Schreibfehler → Fehler danach, Schutz-Timeout nur Hinweis |
| `KTWO_MOTOR_STALL_MODE [MOTOR=x\|y\|xy\|z\|z1] [DATA=1\|2]` | ohne DATA lesen (0x11 [04] → `[mode,00]`), mit DATA setzen (nur x/y; 3/4 abgelehnt) |
| `KTWO_MOTOR_SYS_PARAM MOTOR=x ID=<n> [VALUE=<f>]` | ohne VALUE lesen (0x06 [02,id]), mit VALUE flüchtig schreiben – nur IDs 70, 86, 87, 102, 103, 116, 118–121, 187, 188 (Stock-Config-IDs), Echo-Prüfung |
| `KTWO_MOTOR_PROTECTION [STRICT=1]` | 0C [0b] an alle (lesend; bei Mode 2 quittiert die FW damit die Notfall-Leitung, wie Stock) |

**Sicherheitsnetz:** Bricht ein G-Code zwischen BEGIN und END ab (z. B.
`G28 Y` „No trigger“), setzt das Modul X/Y über das Upstream-Ereignis
`gcode:command_error` selbst zurück (Mode 2, Kp 100; einmalig). Status für
Makros: `printer.k2_motor.{allow_write, homing_active, stall_mode,
homing_pos_kp, last_error}`. Neue Option `homing_pos_kp` (Default 300).

**homing.cfg:** `G28`/`G28 X Y`: Sperren prüfen (`_PI_FREIGABE allow_move`,
`printer.k2_motor.allow_write`, Not-Sperre `xy_stall_ok`) → `M204 S500` →
`KTWO_MOTOR_HOMING PHASE=BEGIN` → Y (M204 S100, ggf. Freifahren, `G28 Y`,
`G1 Y10`) → X (`G28 X`, `G1 X-10`) → `FORCE_MOVE STEPPER=stepper_y
DISTANCE=-3 VELOCITY=10` (Stock-Bedingungen, übernommen: Stock-Koordinaten) →
`PHASE=END` → ggf. Z wie bisher. Ohne Freigabe: Abbruch beim Rendern, KEIN
Frame, KEINE Bewegung.

**Tests:** `test_k2_motor.py` 17/17 (neu 6: Frames = Live-Log inkl. Echo-CRCs,
Kp aus Config, Sperren ohne Bus-Zugriff, Rückbau, END best effort, Hülle mit
Auto-Rückbau + Batch-Meldung). **Batch** (Upstream-Klipper im Batch-Modus):
Auslieferung EXIT 0; Sperre ohne
allow_write und Lauf ohne Port brechen ohne Schritt-Befehle ab (EXIT 255,
klare Meldung); mit Test-Fake-Bus läuft `G28 X Y` komplett (10 RS485-Frames in
Stock-Reihenfolge, FORCE_MOVE, EXIT 0).

## 10. Schnittpositions-Kalibrierung (16.10.2026 Nacht)

Ersatz für Stock `CALIBRATE_CUT_POS`/`MOTOR_CHECK_CUT_POS`. Bedienhinweise:
`config-pi/README.md`. Makro:
`config-pi/cutcal.cfg` (per `[include cutcal.cfg]` einbinden).

**Live-Folge** (Display-Kalibrierung am Original-Drucker, 16.10.2026):
`81 09 00 06 01 46 00 00 96 43` (Kp 300) → `81 04 00 11 04` (lesen → 2) →
`81 04 00 11 03` (Mode 3) → ~60 s Suchfahrt ohne Bus-Frame, Treffer nur über
den Stall-Pin PB11 („motor_x 81 check_protection_code“, 2× im Abstand 1,84 s) →
„x:-8.2 cut_pos_offset:0.4“ → `save_config: set [box] cut_pos_x = -7.80` →
`81 04 00 11 02` → `81 09 00 06 01 46 00 00 c8 42` (Kp 100). Zeitbild: 0,1-mm-
Anstöße ab `pre_cut_cal_pos_x` −5,0, Ergebnis = 2. Treffer + 0,4.

| G-Code | Wirkung |
|---|---|
| `KTWO_MOTOR_CUT_CAL DRY_RUN=1` | Plan (Parameter, Frames, Anstöße) – nichts gesendet, keine Bewegung |
| `KTWO_MOTOR_CUT_CAL` | komplette Folge (Beschleunigung ≤ 25000, Kp 300, Park X50/Y200, Mode 3, Anstöße am X-Endstop PB11 per `HomingMove` + Nachlauf-Pegelabfrage, Ergebnis-Prüfung, Ausgang X200, `configfile.set('k2_box','cut_pos_x')`, Mode 2, Kp 100); Rückbau IMMER |
| `CALIBRATE_CUT_POS [SAVE=1]` (cutcal.cfg) | Sperren (allow_move, allow_write, Homing, Filament), ggf. `G28 X Y`, `KTWO_MOTOR_CUT_CAL`, Meldung, optional `SAVE_CONFIG` |

**Sicherheit:** nur mit `allow_write: True` UND `_PI_FREIGABE allow_move`
(prüft auch das Modul selbst); harte Grenzen X ≥ `check_cut_pos_x_min` (−9,5)
und ≥ −10/`position_min`, ≤ 100 Anstöße, Anstoß ≤ 200 mm/s, Beschleunigung ≤
25000/`max_accel`; ohne zwei Stalls bis `x_min` Abbruch; Ergebnis muss in
`check_cut_pos_x_min/max` liegen; Fehler → G-Code-Fehler mit Rückbau (Kopf weg
vom Hebel, Mode 2, Kp 100, Beschleunigung), nie Shutdown. Status:
`printer.k2_motor.cut_cal.{active, result, first_hit, second_hit, probes,
saved, error}`. Neue optionale Optionen `cut_cal_*` (Defaults = Stock).

**Achtung SAVE_CONFIG:** solange `k2-module.cfg` `[k2_box] cut_pos_x:` enthält,
verweigert Upstream das Speichern („conflicts with included value“) –
Zeile entfernen.

**Tests:** `test_k2_motor.py` 23/23 (neu 6: Frames = Live inkl. Echo-CRCs und
Ablauf Bus↔Bewegung, 9 Fehlerfälle mit Rückbau, harte Grenzen, Sperren,
Hülle, Klipper-Adapter). **Batch** (Upstream-Klipper im Batch-Modus): Sperren
ohne Frame/Schritt, Fake-Bus-Erfolg
mit Live-Frame-Folge, Grenzfall mit Rückbau, SAVE_CONFIG-Konflikt/-Erfolg.

## 11. Stall-/Notfall-Überwachung im Betrieb (18.10.2026)

Nachbau des Stock-Ablaufs. ENTWURF – am Gerät UNGETESTET.

- **Registrierung zur Config-Zeit** (wie Stock `button_stall_pin_init`): je
  Motor aus `motor_closed_loop` mit `motor_<m>_stall` ein
  `buttons.register_buttons([pin], cb)` (Upstream `extras/buttons.py`,
  Callback `(eventtime, state)`, MCU-Abtastung 2 ms). Pins unverändert aus
  `k2-motor.cfg` (PB11, PB12, PB13, PA10, `nozzle_mcu:PB12`; ohne `^`/`!`,
  aktiv high). Ist `[duplicate_pin_override]` vorhanden, wird es VORHER
  geladen (PB11/PB12 sind zugleich `endstop_pin`). Batch-Lauf F/O: Upstream
  akzeptiert, `buttons_add … pull_up=0`, kein „pin used multiple times“.
- **Option** `stall_monitor: off|report|act` (Default **report**).
- **Filter** (synchron im Callback, ohne Bus): nur steigende Flanke; nur nach
  `is_ready` (Init P9); X/Y/Z/Z1 während `KTWO_MOTOR_CUT_CAL` nur
  `cut_state` (wie Stock); X/Y gesperrt während Homing, ab dem Senden von
  Mode 1/3 bis bestätigtem Mode 2 **+0,05 s** (Stock `is_homing`, Vergleich
  mit der Flanken-Zeit); Z/Z1/E ohne Homing-Sperre (wie Stock). Fehlschlag des
  Wechsels auf Mode 2 lässt die Sperre stehen (Status zeigt es).
- **Reentrancy:** höchstens eine Auswertung (Reactor-Callback); weitere
  Flanken warten in `pending` und werden danach abgearbeitet.
- **Auswertung:** `0C [0b]` an den Motor (E transparent 0x81; gibt in Mode 2
  die Leitung frei), bis 2 Versuche, jeder Fehlversuch gemeldet; key781–797
  im Stock-Format `{"code":"key789",…}` gemeldet.
- **Reaktion nur bei `act`:**

| Fall | Im Druck (`print_stats.state == printing`) | Sonst |
|---|---|---|
| X/Y/Z/Z1 mit Fehlerbits | `printer.invoke_shutdown` mit Motor, Pin, key-Codes, err/warning_code | `M84` + Meldung |
| X/Y/Z/Z1 nur Warnbits | `PAUSE` (falls nicht pausiert) | nur Meldung |
| E (Fehler oder Warnung) | `PAUSE` + `M400`, dann `SET_STEPPER_ENABLE STEPPER=extruder ENABLE=0` | nur Extruder aus |
| Codes 0 / nicht lesbar | nur Meldung (Stock: keine Reaktion ohne Antwort) | dito |

  Bewusst nicht nachgebaut: volle Abschalt-Folge bei reinen Warnungen,
  `FORCE_MOVE stepper_y 0.01`, E-Auto-Retry/CFS-Event (hängt an `k2_box`).
- **Kein automatisches Löschen:** `KTWO_MOTOR_CLEAR [MOTOR=all|x|y|z|z1|e|x,y…]`
  = Stock `MOTOR_CLEAR_ERR_WARN_CODE NUM=n DATA=5` (`0C [05]`, live nie
  beantwortet), danach `0C [0b]` zur Kontrolle; bleiben Codes → G-Code-Fehler.
  Nur mit `allow_write: True` (kein Override). Nach einem Shutdown löscht der
  Init (P6) beim Neustart ohnehin.
- **Status** `printer.k2_motor.stall_monitor`: `mode, pins, edges, last_edge
  {motor, pin, state, eventtime, result}, codes{m: err_code, warning_code,
  errors, warnings | read_error}, last_action, ignored, busy, pending,
  cut_state, cut_edges, halted`; `KTWO_MOTOR_STATUS` zeigt eine Zusammenfassung.
- **Gerätetest, bevor `act` gesetzt wird:**
  1. Nach `KTWO_MOTOR_INIT`: `KTWO_MOTOR_STATUS` → Flanken 0, keine Aktion
     (Ruhepegel 0 in Mode 2). E darf vor dem Init high sein – vor
     `is_ready` wird nichts bewertet.
  2. `G28` mehrmals: keine Stall-Meldung von X/Y (Sperre greift);
     `CALIBRATE_CUT_POS`: nur `cut_edges` steigt.
  3. Ohne Druck, Motoren aktiv: Achse leicht blockieren bzw. Schleppfehler
     provozieren → Meldung `key789`, danach `KTWO_MOTOR_CLEAR`.
  4. Erst dann `stall_monitor: act`, Neustart; Probedruck mit Hand am Not-Aus.

**Tests:** `test_k2_motor.py` 30/30 (neu 6: Registrierung, Filter/Homing-
Sperre/Cut-Cal, report, act, Reentrancy, KTWO_MOTOR_CLEAR). **Batch**
(Upstream-Klipper): Lauf F (buttons in der Auslieferung) und O
(`stall_monitor: act`).

## 13. Motor-Kalibrierwerte sichern und prüfen (26.10.2026)

Vorstufe für eine spätere, abgesicherte Motor-Kalibrierung. **Die
Kalibrierung selbst (0x03/0x04) bleibt im Sendeschutz gesperrt.** Beide
Befehle senden ausschließlich lesende Frames, bewegen nichts und laufen
auch mit `allow_write: False`.

| G-Code | Wirkung |
|---|---|
| `KTWO_MOTOR_CAL_BACKUP MOTOR=x\|y\|z\|z1\|e\|all` | liest je Motor Version, UUID, Regelungszustand (0x05 [04]), Fehlerbits (0x0C [0b]), Flash-Prüfsumme (0x07 [02]), Messwerte (0x08 idx 15/17/18), **alle 77 Parameter in RAM (0x06 [02,id]) und Flash (0x07 [06,id])** und die Gegenprobe 0x13; schreibt `~/printer_data/k2-motor-cal/<motor>-JJJJMMTT-hhmmss.json` (Ordner 700, Datei 600) |
| `KTWO_MOTOR_CAL_CHECK MOTOR=… [BACKUP=<datei>]` | liest dasselbe neu, bewertet es und vergleicht mit der neuesten Sicherung desselben Motors (gleiche UUID) bzw. der angegebenen Datei aus dem Sicherungsordner |

- **Kalibrierwerte:** `elec_offset` (ID 9), `phase_order_invert` (10),
  `elec_offset_err_deg` (25), `elec_angle_calibration_standar_deg` (8),
  `ud_cal_set` (20), `motion_dir` (21), `encoder_calibtate_official_ud_cal_set`
  (23), `singleturn_control_mode` (7) – Rohbytes und Wert, RAM und Flash.
- **Plausibel** wie die Boot-Prüfung der Motor-FW: `phase_order_invert` 0/1,
  |`elec_offset`| < 1310 (die FW teilt ganzzahlig), 0 ≤ `elec_offset_err_deg` < 360.
- **CHECK meldet:** ALARM (G-Code-Fehler) bei unplausiblen Werten oder
  geändertem `phase_order_invert`; Warnung bei sonstigen geänderten
  Kalibrierwerten, RAM ≠ Flash (außer Laufzeitwerten ID 11/12/70/173 und
  Werten, die der Init bei jedem Start aus der Config setzt), geänderten
  Flash-IDs, Fehlerbits, Prüfsummen-Abweichung, Lesefehlern oder einer
  Sicherung von einer anderen Motor-Einheit.
- **Datenschutz:** Die UUID steht nur in der Datei, nie in Konsole oder Log.
  Restrisiko Extruder: Dessen Antworten laufen über das Klipper-Protokoll der
  Düsen-MCU; bei einem Shutdown direkt danach schreibt Klipper die letzten
  empfangenen Nachrichten ins `klippy.log` – dort kann dann die E-UUID stehen.
- **Nicht lesbar:** Der Werks-Record des Motors (Flash-Sicherungskopie) ist
  über den Bus nicht erreichbar – gesichert wird der Haupt-Parameterblock.
- **Sperren:** nicht während Druck/Pause, Cut-Kalibrierung oder vor
  abgeschlossenem Motor-Init (die Lesefolge belegt den Bus einige Sekunden je
  Motor). Bei offenem Homing-Rahmen nur eine Meldung, **kein** G-Code-Fehler –
  der würde den (schreibenden) Homing-Rückbau auslösen. Extruder über den
  Düsenkanal (0x81), ohne 0x13.
- **Stummer Motor:** Antwortet die Versionsabfrage nicht oder bleiben 3
  Lesungen in Folge ohne Antwort, bricht die Folge für diesen Motor ab (RS485
  blockiert je Timeout den Klipper-Hauptthread bis 1 s). Die übrigen Motoren
  werden trotzdem gesichert; danach G-Code-Fehler mit Liste.
- **Datei:** atomar mit `fsync` (Datei und Ordner), bei Fehlern keine
  `.tmp`-Reste. CHECK wählt automatisch die neueste vollständige und plausible
  Sicherung derselben UUID, sonst die neueste derselben UUID; Dateien ohne UUID
  nie automatisch.
- **Option:** `cal_backup_dir` in `[k2_motor]` (Default
  `~/printer_data/k2-motor-cal`).

**Tests:** `test_k2_motor.py` 36/36 (neu 4: Lesefolge nur lesend und durch
den Sendeschutz, Plausibilität/Vergleich inkl. Grenze 1310 und Lücken in der
Sicherung, Extruder/stummer Motor/Portausfall, G-Code-Hülle mit Datei,
Rechten, Sperren, Dateiauswahl und Ausnahmen).

**Live 26.10.2026** (Drucker frisch eingeschaltet, Motor-Init ready, nichts
bewegt): BACKUP `MOTOR=all` 14 s, CHECK 13 s, alle 5 Motoren vollständig
(77 IDs, keine Lesefehler), plausibel, Schutz ok, Prüfsumme ok, FW 1.9.7.81.
- `0x05 [04]` erstmals live: `EN=1 Regler=0 PWM=0` bei abgeschalteten
  Motoren (nach dem Init, vor dem Homing) – bestätigt EN-Pegel 1 = gesperrt.
- Versorgung 24,4–24,6 V; MCU-Temperatur Achsen ≈ 37 °C, Extruder ≈ 24 °C.
- `phase_order_invert` X/Y/Z/Z1 = 0, Extruder = 1 (RAM = Flash; der Motor
  läuft, die Phasenfolge passt also zu seiner Verdrahtung).
- RAM ≠ Flash: ID 70/86/87/188 = Werte aus `[k2_motor]` (Init, flüchtig),
  ID 11 = Notfallmodus 2 (Laufzeit), ID 173 `protect_en` RAM 255 / Flash 1
  bei allen 5 Motoren (Setzer nicht belegt). Vor einer späteren Kalibrierung
  wichtig: Ein Kalibrierlauf würde genau diese RAM-Werte dauerhaft in den
  Flash schreiben.

## 15. Motor-Schutz nach dem Init prüfen (protect_en, 26.10.2026)

Die Motor-FW setzt nach dem Einschalten in ihrer Encoder-Registerprüfung den
Schalter `protect_en` (Parameter-ID 173) durch einen zu langen Kopiervorgang
auf ein SPI-Empfangsbyte (live an allen Motoren 255 = an). Bricht dieser
Transfer ab, bleibt **0 = alle Schutzfunktionen des Motors aus** (kein
Überstrom-, Schleppfehler- oder Stall-Schutz) – ohne jede Meldung. Das gilt
auch für die Original-Firmware (Motor-FW-Analyse 26.10.2026).

- Nach jedem erfolgreichen Motor-Init liest `[k2_motor]` `0x06 [02, 173]` an
  allen Motoren (Extruder über den Düsenkanal), nur lesend.
- **0 → rote Meldung** „Motor-Schutz AUS bei …“ mit Abhilfe: Drucker aus- und
  wieder einschalten (die Motor-FW läuft ihre Prüfung nur nach dem Einschalten
  neu durch; `FIRMWARE_RESTART` reicht nicht). Nicht lesbar → Warnung.
- Sonst nur eine Logzeile (Auto-Init) bzw. Konsolenzeile (`KTWO_MOTOR_INIT`).
- `KTWO_MOTOR_STATUS` zeigt die Werte, `printer.k2_motor.protect_en` enthält
  sie (Zahl oder Fehlertext je Motor).
- Test: `test_protect_en_nach_init` (`test_k2_motor.py` 44/44).
- **Live 26.10.2026:** nach Klipper-Neustart x/y/z/z1/e = 255 (Schutz an),
  Logzeile „Motor-Schutz (protect_en, 0 = aus): e=255, x=255, …“, Status
  gefüllt. Hinweis: Nach einem reinen Host-Neustart (`systemctl restart
  klipper`) meldet der erste Init key798 nur für den Extruder (Düsen-MCU nicht
  zurückgesetzt); der Start-Wächter löst das mit einem FIRMWARE_RESTART.

## 14. Motor kalibrieren: RUN / RESTORE / ENABLE (26.10.2026)

**Standardmäßig AUS.** Ein Kalibrierlauf dreht den Motor open-loop **ohne
Schutzfunktionen**; währenddessen gibt es keinen Abbruch über Bus oder
Enable-Leitung – Not-Aus nur über den Netzschalter. Die Motor-FW schreibt
danach Werte dauerhaft in ihren Flash (Seite 0 komplett aus dem RAM).

| G-Code | Wirkung |
|---|---|
| `KTWO_MOTOR_CAL_RUN MOTOR=<m> MODE=ELEC\|ENCODER AUSGEKUPPELT=1` | ohne `CONFIRM`: nur lesen, Plan, Vorbedingungen, Bestätigungscode (120 s gültig, einmal verwendbar) |
| `… CONFIRM=<code>` | `M84` → frische Sicherung → alle Vorbedingungen → **genau ein** Frame `0x04 [00]` (ELEC, ≤ 45 s) bzw. `0x03 [00]` (ENCODER, ≤ 120 s), retries 0 → Prüfung danach, Ergebnisdatei `<m>-<zeit>-lauf.json` |
| `KTWO_MOTOR_CAL_RESTORE MOTOR=<m> BACKUP=<datei> [CONFIRM=<code>]` | `phase_order_invert`, `elec_offset`, `elec_offset_err_deg` aus der Sicherung per `0x06 [01]` in den RAM, gegenlesen, dann `0x07 [03 00]` (alle RAM-Werte in den Flash), Flash gegenlesen. Keine Bewegung |
| `KTWO_MOTOR_CAL_ENABLE MOTOR=<m> EINGEKUPPELT=1 [STROMLOS=1]` | prüft (nur lesend), legt eine neue Referenz-Sicherung an und gibt den Motor frei |

**Freigaben (alle nötig):** `allow_write: True`, `allow_motor_calibration:
True`, für `MODE=ENCODER` zusätzlich `allow_encoder_calibration: True`, für
Z/Z1 `allow_z_calibration: True` (eine Spindel allein kippt das Bett),
`_PI_FREIGABE allow_move`, `AUSGEKUPPELT=1`, kein Druck/Homing/Cut-Cal,
Motor-Init fertig. Je Aufruf genau ein Motor, kein Broadcast, keine
Wiederholung.

**Vorbedingungen (vor dem Senden, sonst nichts gesendet):** Snapshot
vollständig; nach `M84` `0x05 [04]` = EN 1/Regler 0/PWM 0; keine Fehlerbits;
Versorgung 22–26 V; Motor-MCU < 70 °C; ID 20/23 (`ud_cal_set`) 0,03–0,25 und
ID 8 5–30° in RAM und Flash; Encoder-Position nicht im Umbruch-Fenster
(0…1000 / 31767…32767 = ¾ el. Periode + Ausrichtsprung + Reserve – sonst speichert die FW ein falsches
`phase_order_invert`); `protect_en` (ID 173) im RAM 1…255; Homing-Kp (ID 70)
= Config; Stall-/Notfallmodus (ID 11/12) nicht vorübergehend (1/3). Der Plan
listet alle RAM-Werte, die **dauerhaft** werden (z. B. Config-Werte des
Inits). RESTORE prüft dasselbe ohne die Kalibrier-Fenster und ohne
Umbruch-Fenster.

**Sendeschutz:** Die Frames bleiben gesperrt. Einzige Ausnahme ist eine
Einmalfreigabe (`k2_transport.tx_kalibrier_freigabe`, in beiden Schichten)
für genau den einen Frame an genau eine Adresse 0x81–0x84, 2 s (höchstens
10 s), direkt nach dem Senden gelöscht.

**Während des Laufs:** RS485-Lauf im Hilfs-Thread (Bus-Lock hält alle
anderen Busnutzer fern, Klipper bleibt bedienbar); Extruder über den
Düsenkanal (Host-Wartezeit je Aufruf verlängert). Die Stall-Überwachung
schweigt. Startet Klipper mitten im Lauf neu, wartet der Auto-Init, bis der
Lauf vorbei sein kann (Zustandsdatei).

**Prüfung danach:** ALARM bei geändertem `phase_order_invert` (RAM/Flash)
oder `protect_en` = 0; Hinweise bei ELEC „failed“, abweichendem Ergebnis,
Fehlerbits, unerwarteten Flash-Änderungen (erwartet ist: Flash = RAM von
vorher, `protect_en` nur das untere Byte, also 1 → 255).

**Zustände** (Datei `kalibrier-zustand.json` im Sicherungsordner, überdauert
Neustarts):

| Zustand | Bedeutung | weiter mit |
|---|---|---|
| `laeuft` | Frame gesendet | warten |
| `gesperrt_unbekannt` | keine/kaputte Antwort | Drucker aus/ein, dann `ENABLE … STROMLOS=1` (vergleicht mit der Sicherung; war es ein ENCODER-Lauf → `elec_pflicht`) |
| `phase_verdacht` | ALARM | `RESTORE` |
| `elec_pflicht` | nach ENCODER | Drucker aus/ein, dann `MODE=ELEC` |
| `homing_noetig` / `z_ausrichtung_noetig` | Lauf ok | einkuppeln, `ENABLE`, `FIRMWARE_RESTART`, G28 (Z: ausrichten + Bettnetz) |

Solange X/Y/Z/Z1 einen Zustand haben, sind `KTWO_MOTOR_HOMING PHASE=BEGIN`
und `FORCE_MOVE` gesperrt. **Harte Sperre:** In den Zuständen `laeuft`,
`gesperrt_unbekannt`, `phase_verdacht` und `elec_pflicht` löst jede
Stepper-Freigabe der betroffenen Enable-Gruppe einen Klipper-Shutdown aus
(Klipper `stepper_enable`, Callback vor dem Setzen der Enable-Leitung; X/Y/Z/Z1
teilen `!PA9`, der Extruder hat eine eigene Leitung). Das deckt alle Wege ab
(G1 E, CFS-Laden, `SET_STEPPER_ENABLE`, Z-Ausrichten …). Eine fehlende
Zustandsdatei heißt „kein Zustand“; eine vorhandene, aber kaputte gilt als
`gesperrt_unbekannt` für alle Motoren. `KTWO_MOTOR_INIT` ist gesperrt, solange
ein Lauf noch laufen kann (Aufschub höchstens 150 s, auch bei falscher Uhr).
RESTORE ist in `elec_pflicht` gesperrt (der alte `elec_offset` passt nach einer
Encoder-Kalibrierung nicht mehr). Die Regelung geht nach ENABLE erst mit der
nächsten Stepper-Freigabe (EN-Flanke) wieder an; `0x05 [02]` wird nie
verwendet. Danach `KTWO_MOTOR_CAL_CHECK` – erwartet EN 0/Regler 1/PWM 1.

**Extruder:** „ausgekuppelt“ = kein Filament im Fördergetriebe.

**RESTORE und Text-Umweg der FW:** Die FW wandelt einen per `0x06 [01]`
geschriebenen Float über Text mit 6 Nachkommastellen um (z. B. 3,86712345 →
3,867123). Gegengelesen wird deshalb mit dieser Toleranz; der Flash muss
danach exakt dem gelesenen RAM-Wert entsprechen. Zusätzlich wird die
Flash-Prüfsumme (`0x07 [02]`) gemeldet.

**Tests:** `test_k2_motor.py` 42/42 (neu 6: Controller – genau ein Frame,
Freigabe danach weg, Extruder; Vorbedingungen; Prüfung danach; Hülle RUN/
ENABLE; ALARM/RESTORE/Timeout/ENCODER; Sperren/Text-Umweg/kaputte Datei),
`test_k2_485.py` 138/138 (Einmalfreigabe in beiden Schichten),
`serial485.py` 123/123. Zwei unabhängige Prüfberichte eingearbeitet;
`test_cal_optionen_upstream` (43/43) prüft, dass jede Option auch ohne
`get_options()` abgefragt wird (sonst meldet Klipper „Option … is not
valid“ – live passiert und behoben).

**Live 26.10.2026 – Extruder ohne Filament, `MODE=ELEC`:** Lauf 15,3 s,
Antwort `00 c4918d42 c9eaffc1` = **nicht bestanden** (Fehlerwinkel 70,8° ≥
15°). Der Mittelwert (−31,99 ≡ 623,4 mod 655,36) liegt nur ≈ 1,5 Counts
neben dem Werkswert 621,85 – die Werkskalibrierung ist damit bestätigt.
`phase_order_invert` blieb 1, kein Alarm; der Flash übernahm wie angekündigt
ID 11 (2) und ID 173 (255). Vermutete Ursache: im eingebauten Zustand dreht
der Motor das Fördergetriebe mit, bei kleiner Kalibrierspannung
(`ud_cal_set` 0,06) streuen die Messpunkte. → Den Extruder eingebaut nicht
kalibrieren. Nebeneffekt: die Flash-Prüfsumme (`0x07 [02]`) weicht danach um
genau die geänderten Bytes ab (FW schreibt Seite 0 ohne die Prüfsumme auf
Seite 1); laut Befund prüft der Motor sie beim Boot nicht.

## 7. Quellen (alle nur gelesen)

- `nachbau/motor_init.py`, `nachbau/motor.py`, `nachbau/serial485.py`
- Live-Mitschnitte am Original-Drucker (Boot, Homing, Cut-Kalibrierung) und
  dessen Konfiguration (`motor_control.cfg` mit Pins + F008-Params,
  `sensorless.cfg`, `gcode_macro.cfg`, `box.cfg`) – nicht im Repo

## 12. Konsole (Audit 23.10.2026)

- Der Auto-Init läuft bei `klippy:connect` – dort ausgegebene Zeilen kamen nie
  in Mainsail an. Jetzt (gepuffert bis kurz nach „ready“): Fehlerzeilen des
  Init (z. B. `[key798] …`), transparent-Setup, „Auto-Init übersprungen/
  abgebrochen“ als `!! [k2_motor] …` und eine Abschlusszeile `Motor-Init
  (Start): Status <status>, Fehler: <n>` (`!!`, wenn die Motoren nicht bereit
  sind). Die Info-Zeilen des Auto-Init stehen nur im klippy.log; ein manueller
  `KTWO_MOTOR_INIT` zeigt wie bisher alle Zeilen.
- `K2MotorController` bekommt statt des gcode-Objekts einen `LogAdapter`
  (`log.info/error` liefen vorher still ins Leere) – die Init-Zeilen stehen
  jetzt live mit Zeitstempel im klippy.log.
- Stall-Überwachung: Fehlercodes und Aktionen bei Fehlern (M84, Shutdown,
  PAUSE + Extruder aus, unlesbare Codes, Skriptfehler) als `!! [k2_motor] Stall
  …`, reine Warnungscodes weiter `// [k2_motor] Stall …`.
- `[k2_485]` (gemeinsamer Bus): Connect-Meldungen gepuffert, Port-Ausfall im
  Betrieb rot, neuer Bus-Statistik-Wächter (Optionen `warn_window`,
  `warn_timeouts`, `warn_crc_errors`, `warn_retries`).
- `[k2_heater_pid]`: Fehler im Umschalt-Timer `!! [k2_heater_pid] …`
  (höchstens alle 10 min). `[k2_zalign]`: gescheitertes Aufräumen nach einem
  Abbruch steht in der roten Abbruchmeldung.

Regeln, Format und Gesamttabelle: `README-k2_konsole.md`.
