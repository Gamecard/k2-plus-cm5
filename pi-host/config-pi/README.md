> **ENTWURF – Stand 16.10.2026, am Gerät UNGETESTET.** Upstream-Klipper-Konfiguration des K2 Plus für den Raspberry-Pi-CM5-Host. MCU-Firmware bleibt die originale Creality-Firmware. Nur offline geprüft (Upstream-Klipper Commit 9cb9975, Batch-Modus, §5). Alles mit **ANNAHME** ist nicht belegt.

# Pi-Host-Konfiguration K2 Plus (`pi-host/config-pi/`)

## 1. Dateien

| Datei | Inhalt |
|---|---|
| `printer.cfg` | Hauptdatei: MCUs (by-id-Platzhalter), `[k2_prtouch]` (Probe), Kinematik, Stepper, `duplicate_pin_override`, Extruder, Bett, Kammer, Lüfter, Sensoren, `z_tilt`, `bed_mesh`, `input_shaper`, Allgemeines, SAVE_CONFIG-Block (Shaper-Werte + Stock-Mesh als Referenzprofil) |
| `k2-motor.cfg` | `[k2_motor]` mit allen Pins und aktiven Parametern aus `motor_control.cfg` |
| `k2-module.cfg` | `[k2_485]` (gemeinsamer RS485-Bus, by-id-Platzhalter), `[k2_box]`, `[k2_belt mdlx]`/`[k2_belt mdly]` (gesperrt) |
| `homing.cfg` | `[homing_override]` + Freigabe-Makro `_PI_FREIGABE` + Stall-Mode-Haken |
| `macros.cfg` | Grundbetrieb: M106/M107, M141/M191, M900, M205, START/END_PRINT (+ PRINT_START/PRINT_END), PAUSE/RESUME/CANCEL_PRINT, T0–T15 + M8200-Kette (CFS-Wechsel über `[k2_box]`, §7.1), externe Spule `SET_FILAMENT_SOURCE SOURCE=EXTERNAL|CFS`/`LOAD_FILAMENT`/`UNLOAD_FILAMENT`/`SET_EXTERNAL_MATERIAL` (README-k2_box.md §18), G29, BEDPID, NOZZLE_PID, NOZZLE_PID_HIGH, ALLOW_MOTION, PARK_AT_PURGE, Z_AXIS_CALIBRATION, INPUTSHAPER (Resonanzmessung mit `[k2_lis2dw]`, `ACHSE=X|Y`, `SPEICHERN=1`; 26.10.2026), TUNOFFINPUTSHAPER, HEATER_STABILITY_TEST (Kalibrier-Leiste für Mainsail, 22.10.2026) |
| `zalign.cfg` | Dual-Z-Lichtschranken PA15/PA8: Variante L aktiv (rein lesend, `[gcode_button zalign_z/zalign_z1]`, `ZALIGN_STATUS`), Variante B `[k2_zalign]` auskommentiert + gesperrt, `ZALIGN_STOCK` |
| `cutcal.cfg` | `CALIBRATE_CUT_POS` (Schneidposition kalibrieren, §7.5) |
| `README.md` | diese Datei |

Reihenfolge in `printer.cfg`: `[mcu]`, `[mcu nozzle_mcu]`, `[k2_prtouch]`, dann die Includes, dann der Rest. Seit dem Modul-Fix (§5.3) ist die Reihenfolge der Abschnitte **egal** (Lauf I: `[k2_prtouch]` ans Ende verschoben, fehlerfrei, `statistics` aus der Standardbibliothek); `[k2_prtouch]` muss nur wegen SAVE_CONFIG (`z_offset`) in der Hauptdatei stehen.

**Sicherheits-Defaults (alles gesperrt):** `[k2_prtouch] allow_move: False`, `[k2_motor] allow_write: False` + `auto_init: False`, `[k2_box] allow_write: False`, `[k2_belt mdlx/mdly] allow_write: False` + `allow_move: False`, `[k2_box] allow_move: False`, `[k2_zalign]` auskommentiert (aktiviert: `allow_move: False` + `_PI_FREIGABE` + Motor-Init), `_PI_FREIGABE allow_move: False` (`xy_stall_ok` ist nur noch Not-Sperre, Default True; X/Y-Homing braucht zusätzlich `[k2_motor] allow_write: True`, §7.6) (G28 bricht ohne jede Bewegung ab – im Batch-Test nachgewiesen, §5).

## 2. Zuordnung Stock → Pi

Quelle: Stock-Configs des Druckers, Stand 16.10.2026 (printer.cfg mit SAVE_CONFIG, sensorless.cfg, gcode_macro.cfg, printer_params.cfg, box.cfg, motor_control.cfg). Legende: **Ü** = übernommen (1:1, sofern nicht anders vermerkt), **M** = ersetzt durch eigenes Modul, **E** = entfällt, **O** = offen.

### 2.1 printer.cfg

| Stock-Abschnitt | Pi | Bemerkung |
|---|---|---|
| `[mcu]` ttyS2 / `[mcu nozzle_mcu]` ttyS3, 230400, `restart_method: command` | Ü | Serial → `/dev/serial/by-id/usb-ANNAHME_…` (Platzhalter), Baud bleibt 230400 (MCU-Konstante `SERIAL_BAUD`). Main-MCU: RS232 an J22 Pin 10/4 über USB-RS232-Konverter (FTDI, `usb-FTDI_…`), Düsen-Leitung noch unbekannt; Zuordnung per `k2-identify.py` (install/README §4b) |
| `[mcu rpi]` + `[bl24c16f]` (EEPROM i2c.1, Power-Loss) | E | EEPROM sitzt am T113-Upper-Board, das entfällt. Keine Linux-Host-MCU nötig → kein eigenes Dictionary. Power-Loss-Resume entfällt |
| `[verify_heater extruder/heater_bed/chamber_heater]` | Ü | |
| `[idle_timeout] timeout: 99999999` | Ü | Achtung: Heizungen werden nie automatisch aus (§6) |
| `[virtual_sdcard]` | Ü | Pfad `~/printer_data/gcodes` (ANNAHME Moonraker-Standard); `forced_leveling` (Creality) E |
| `[gcode_arcs]`, `[pause_resume]`, `[exclude_object]`, `[display_status]` | Ü | |
| `[temperature_sensor mcu_temp]` | E | Upstream-`temperature_mcu` kennt `gd32f303xe` nicht (`config_unknown` → „MCU temperature not supported“, statisch geprüft; im Batch-Modus übersprungen, §5.4) |
| `[stepper_x/y/z/z1]` | Ü + **L3** | z1 ohne `endstop_pin` (Upstream hängt alle Z an den Endstop von stepper_z). **Seit 18.10.2026 enge Stock-Betriebsgrenzen**: X min −7,8 (= `cut_pos_x`), Y max 352, Z max 350,2 (Stock `SET_Z_LIMIT` nach G28 Z); Aufweiten für Schacht/Schneiden über `[k2_limits]` (`../README-k2_limits.md`) |
| `SET_LIMITS`/`RESTORE_LIMITS`/`SET_Z_LIMIT`, Z-Klemmung −2 in `G1` (Creality-gcode_move) | M `[k2_limits]` | `KTWO_LIMITS_WIDE`/`KTWO_LIMITS_RESTORE` (nur gehomt, Rücksetzen auch bei jedem G-Code-Fehler), Z < −2 außerhalb des Drucks wird **abgelehnt** (Transform) |
| `[z_tilt]` | Ü | nutzt die Probe aus `[k2_prtouch]` |
| `[thermistor my_thermistor]`, `[extruder]` | Ü | Creality-Optionen `high_temp_value`, `pid_k*_high_temp` E (kein zweiter PID-Satz in Upstream, als Kommentar erhalten) |
| `[heater_bed]` | Ü | |
| `[filament_switch_sensor filament_sensor]` | Ü | `runout_gcode` (E30 + `BOX_CHECK_MATERIAL_REFILL`) E → nur Pause |
| `[multi_pin heater_fans]`, `[heater_fan hotend_fan]`, `[output_pin fan0_en/fan0/fan1/fan2/extruder_fan]`, `[multi_pin heater_fan2]` | Ü | Lüfter bleiben `output_pin` mit `scale: 255` (Makros M106/M107 wie Stock) |
| `[heater_fan chamber_fan]` | Ü | `is_ptc_fan: 1` (Creality) E – Upstream-heater_fan läuft bei Ziel > 0 ohnehin (ANNAHME gleiche Wirkung, §6) |
| `[temperature_sensor chamber_temp]`, `[heater_generic chamber_heater]`, `[temperature_fan chamber_fan]` | Ü | PC5 dreifach als ADC, PA0 doppelt (fan1 + temperature_fan) – wie Stock |
| `[multi_pin LED_pin]`, `[output_pin LED/power/ptc_power]` | Ü | |
| `[duplicate_pin_override]` | Ü | 1:1 Stock-Liste. Korrektur 22.10.2026: Das Stock-`PB1` in `heater_fans` ist der **Main**-Pin (Klipper liest jeden Pin einzeln, ohne Präfix = `mcu`); die frühere Ergänzung `nozzle_mcu:PB1` (Düsen-PB1 doppelt) entfällt. Folge des alten Fehlers: der Main-PB1-Lüfter lief nie, Düsen-PB1 (`extruder_fan`) lief mit dem Hotend-Lüfter |
| `[motor_control]` (in printer.cfg auskommentiert, aktiv in motor_control.cfg) | M `[k2_motor]` | §2.2 |
| `[lis2dw]`, `[resonance_tester]` | M | Upstream-`[lis2dw]` scheitert an der Creality-FW (altes Protokoll, „Command format mismatch“, Batch-Lauf C) → ersetzt durch `[k2_lis2dw]` (pi-host/k2_lis2dw.py, §7.8); `[resonance_tester] accel_chip: k2_lis2dw` |
| `[prtouch_v3]` | M `[k2_prtouch]` | Übernommen: `z_offset 0`, `speed 5`, `samples 1`, `samples_result average`, `samples_tolerance 0.5`, `samples_tolerance_retries 5`, `step_swap_pin ^!PC7` (Pull-Up wie Stock-Endzustand; Creality `pull_up=0` = Pull-Down, README-k2_prtouch A.8), `pres_swap_pin nozzle_mcu:PA15`, `pres_cs0_pin PB13/PB14`, `pres_tri_hold 4000,10000,500`, `pres_cfg_regs 60`; ergänzt (Stock-Defaults, BELEGT per Emulation): `pres_acq_tkms 0.78125`, `pres_tri_fter 5,1,0.8`, `pres_ded_tkms 128`, `step_acq_tkms 0.390625`. **E/O:** `quick_lift_speed 100`, `prth_clr_probe_pos 150,355`, `prth_msg_show`, `enable_not_linear_comp`/`high_low_temp_leveling` (Temperaturkompensation), `prth_max_chps 8`, `prth_tri_zacc 1000`, `prth_min_fans 0.6`, `bed_mesh_fall_comp_diagonal/axis` (Mesh-Kompensation), `need_steady_regional 0.8` |
| `[bed_mesh]` | Ü | `move_check_distance` 1 → **3** (Upstream-Minimum 3.0, sonst Config-Fehler) |
| `[printer]` | Ü | `max_accel_to_decel: 30000` gibt es in Upstream nicht mehr → `minimum_cruise_ratio: 0.0` (gleichwertig, da = max_accel) |
| `[z_align]` (Lichtschranken PA15/PA8, `config_z_align`) | Ü lesend / M (O) | `zalign.cfg` Variante L `[gcode_button]` `^!PA15`/`^!PA8` (Stock `zes_untrig 1` = Pull-up, ausgelöst = 0); Ausrichtung `[k2_zalign]` (Upstream-Endstops + Etappen, gesperrt, auskommentiert); `START_PRINT` holt `Z_TILT_ADJUST` nach, solange `z_tilt` nicht `applied` ist (A10a) – §7.7 |
| `[belt_mdl mdlx/mdly]` | M `[k2_belt mdlx/mdly]` | Stock-Werte 1:1 (`ch_max_dis 5`, `ch_best_n 140`, `ch_best_error 0.15`), dazu `allow_write: False`, `allow_move: False`, `stock_aliases: False` (README-k2_belt.md) |
| `[input_shaper]` (leer) + SAVE_CONFIG `mzv 42.0 / mzv 42.4` | Ü | im SAVE_CONFIG-Block |
| SAVE_CONFIG `[bed_mesh default]` | Ü umbenannt | als `[bed_mesh stock_referenz_2026-10-16]` – Upstream lädt es dadurch NICHT automatisch; nur Vergleich, vor dem Druck neu messen (`G29`) |
| SAVE_CONFIG `[auto_addr] mb_addr_table_uniids` | E | echte UUIDs – bewusst NICHT übernommen |
| SAVE_CONFIG `[box] cut_pos_x = -7.80` | E | im Paket-Block nicht enthalten; `[k2_box]`-Default −7,80 = dieser Wert (k2-module.cfg-Kommentar) |

Die Kalibrierwerte der **eigenen** Stock-`printer.cfg` (`[box] cut_pos_x` → `[k2_box]`, `[input_shaper]`, `[bed_mesh <profil>]` unter gleichem Namen, `[auto_addr]` nie) übernimmt `install.sh --stock-printer-cfg` in den SAVE_CONFIG-Block (`install/README.md` §4d).

### 2.2 motor_control.cfg, sensorless.cfg, printer_params.cfg, box.cfg

| Stock | Pi | Bemerkung |
|---|---|---|
| `[motor_control]` (switch, retries, overcurrent_switch, cut_pos_offset, motor_closed_loop, motor_*_dir/step/stall, alle aktiven `<motor>_<param>`) | M `[k2_motor]` (k2-motor.cfg) | 1:1; zusätzlich `allow_write: False`, `shutdown_on_failure: False`, `auto_init: False`, `main_mcu: mcu`, `nozzle_mcu: nozzle_mcu` (Modul sucht zuerst das Upstream-Objekt `mcu nozzle_mcu`; der volle Name geht ebenfalls) |
| sensorless.cfg `[force_move]` | Ü | nötig für `SET_KINEMATIC_POSITION` in der Inbetriebnahme |
| sensorless.cfg `[homing_override]` + `xyz_ready`, `_IF_HOME_Z`, `_IF_MOVE_XY`, `_HOME_X`, `_HOME_Y`, `_HOME_Z` | ersetzt durch homing.cfg | Reihenfolge Y→X→Z, Freifahren +10/−10, Z in Mesh-Mitte (175/175), M204-Werte wie Stock. `MOTOR_STALL_MODE`/`MOTOR_SYS_PARAM`/`MOTOR_CHECK_PROTECTION_AFTER_HOME` → Haken `_PI_VOR_HOMING`/`_PI_NACH_HOMING` (`KTWO_MOTOR_HOMING PHASE=BEGIN/END`, §7.6). `BOX_GO_TO_EXTRUDE_POS`/`BOX_NOZZLE_CLEAN` vor Z → `_PI_NOZZLE_CLEAN` (§7.2), `PRES_CHECK` E. `FORCE_MOVE stepper_y -3` Ü (§7.6). `BED_MESH_PROFILE LOAD="default"` nach jedem Homing E |
| sensorless.cfg `LEAVE_FLUSH_AREA` | E | Display-Hilfsmakro (FORCE_MOVE) |
| printer_params.cfg `[fan_feedback]` | E | Creality (`config_fancheck`); Drehzahlüberwachung O (optional) |
| printer_params.cfg `[custom_macro]` | ersetzt | Werte in `_PI_PARAM` (default_bed_temp 50, default_extruder_temp 220, g28_ext_temp 140) |
| printer_params.cfg `[gcode_macro product_param]` | E | nur Display-Info |
| box.cfg `[serial_485 serial485]` ttyS5 | M `[k2_485]` | Modul `k2_485.py`: EINE `nachbau/serial485.Serial485`-Instanz für Motoren X/Y/Z/Z1 + CFS + Belt; Port by-id-Platzhalter in k2-module.cfg (ANNAHME) |
| box.cfg `[auto_addr]`, `[box]` | M `[k2_box]` | Auto-Addr nach `k2_motor:ready`; `[box]`-Positionen/Temperaturen als Kommentar (gehören in künftige Wechsel-Makros) |
| box.cfg `[filament_rack]` (externe Spule) | M `[k2_box]` | ersetzt durch die Filamentquelle `KTWO_BOX_SOURCE` (persistent, `filament_source_file`) + Makros `SET_FILAMENT_SOURCE SOURCE=EXTERNAL|CFS`, `LOAD_FILAMENT`/`UNLOAD_FILAMENT` (= Stock `LOAD_MATERIAL`/`QUIT_MATERIAL`), `SET_EXTERNAL_MATERIAL`; bei Quelle EXTERN sind alle CFS-Aktionen gesperrt – nur der Box-Druckzustand `0x0d [0f 00/01]` + `0x0f [01/00]` an Druckstart/-ende wird wie Stock (live) weiter gesendet; END_PRINT entlädt nicht. RFID-Leser 0x11 optional: `KTWO_EXTERN_RFID` + `[gcode_button extern_spule]` `!PA5` (printer.cfg, auskommentiert; `[k2_box] extern_rfid: False`) – README-k2_box.md §18 |
| box.cfg `[load_ai]` | E | AI-Kamera Creality |

### 2.3 Makros

**Übernommen/angepasst (macros.cfg):** `M106`/`M107` (Stock-Abbildung S→fanN_min…255, fan0 schaltet `fan0_en`; ohne Qmode), `M141`/`M191` (Stock-Logik ohne `SET_TEMPERATURE_FAN_SWITCH`; M191 wartet per `TEMPERATURE_WAIT`), `M900`, `M205`, `START_PRINT` (Gerüst: Bett + Düse 140 °C, G28, zweites G28 Z mit warmem Bett, Profil `default` laden falls vorhanden, Drucktemperatur) + `PRINT_START`, `END_PRINT` (Stock-Ablauf ohne BOX/Qmode, Z sicher wie `END_PRINT_POINT`) + `PRINT_END`, `wait_temp`/`WAIT_TEMP_START`/`WAIT_TEMP_END` (als `_wait_temp`/`_WAIT_TEMP_*`), `PAUSE` (Stock-z_safe-Logik, Düse 140 °C, Parken), `RESUME` (Temperatur/Lüfter zurück), `CANCEL_PRINT`, `G29` (vereinfacht: G28 → Z_TILT_ADJUST → G28 Z → BED_MESH_CALIBRATE), `BEDPID [TEMP=100]`, `NOZZLE_PID [TEMP=220] [FAN=0]` (ohne automatisches SAVE_CONFIG; seit 22.10.2026 Bauteillüfter standardmäßig AUS – mit Lüfter gemessene Werte pendelten ohne Lüfter, M109 hing), `T0` (kein Wechsel). Neu 25.10.2026: `PARK_AT_PURGE` (Kopf zur Reinigungsposition am Abfallschacht und dort stehen lassen = Hinweg von `_PI_NOZZLE_CLEAN ONLY=GO`). Ebenfalls 25.10.2026: **Bett nur im Druckbereich vermessen** – `START_PRINT` ruft `_PI_MESH_DRUCKBEREICH` (Objekte aus `EXCLUDE_OBJECT_DEFINE` → `ADAPTIVE=1`; sonst `PRINT_MIN`/`PRINT_MAX` des Slicers + 10 mm Rand, Punktabstand wie im vollen Mesh, min. 3 × 3; ohne beides ganzes Bett) – Creality Print schreibt keine Objekte, vorher wurde jedes Mal 9 × 9 gemessen. Die Hülle `BED_MESH_CALIBRATE` (rename_existing `_BED_MESH_CALIBRATE_BASE`, darunter die Hülle von `[k2_nozzle_tempcomp]`) überspringt im Druck den zweiten `BED_MESH_CALIBRATE ADAPTIVE=1`, den Creality Print nach `START_PRINT` in die Datei schreibt (Merker `_PI_MESH_STATUS.druck_mesh`, END_PRINT setzt zurück). Lösung bewusst nur im Drucker, keine Slicer-Änderung nötig. `_PI_NOZZLE_CLEAN` bricht bei unbekanntem `ONLY` (z. B. `GO.`) ohne Bewegung ab. Neu 22.10.2026: `ALLOW_MOTION` (setzt `_PI_FREIGABE allow_move` für die Sitzung), `Z_AXIS_CALIBRATION` (G28 + Z_TILT_ADJUST nach Neustart/Not-Halt), `HEATER_STABILITY_TEST [TEMP=220] [ZEIT=120] [AUS=1]` (Min/Max der Düse, „ruhig“ bei ≤ 1 °C); dazu `SET_HEATER_PID` aus `k2_heater_pid.py` (`[k2_heater_pid]` in printer.cfg) zum Umschalten ohne Neustart.

**Neu (Sperren/Hilfen):** `T0`–`T15` und `M8200` → CFS-Wechselkette über `KTWO_BOX_*` (nur Box 1, ohne Freigaben nur Plan; Fehler im Druck → PAUSE; §7.1; bei Quelle EXTERN → `KTWO_BOX_EXTERN_TOOL`: erstes Werkzeug kein Wechsel, anderes → PAUSE), externe Spule (19.10.2026, README-k2_box.md §18): `SET_FILAMENT_SOURCE SOURCE=EXTERNAL|CFS` (Filamentquelle, sperrt bei EXTERN alle CFS-Aktionen), `LOAD_FILAMENT [MATERIAL=] [TYPE=] [COLOR=] [TEMP=]` (= Stock `LOAD_MATERIAL`: Kopfsensor muss Filament melden, Spülen wie CFS-Wechsel), `UNLOAD_FILAMENT` (= Stock `QUIT_MATERIAL`: heizen, schneiden, E−10), `SET_EXTERNAL_MATERIAL` (gespeichertes externes Material/Temperatur), `_PI_FREIGABE`, `_PI_VOR_HOMING`, `_PI_NACH_HOMING`, `_PI_MESH_LADEN`, `_PI_PARAM`.

### Bettvermessung beim Druckstart (25.10.2026)

Creality Print schreibt kein `EXCLUDE_OBJECT_DEFINE`; Klippers `ADAPTIVE=1` hat deshalb bei jedem Druck das ganze Bett
gemessen (9 × 9, ca. 2 min) – und danach ein zweites Mal, weil die Datei nach `START_PRINT` selbst noch
`BED_MESH_CALIBRATE ADAPTIVE=1` enthält (mit heißer Düse). Lösung nur im Drucker, keine Slicer-Einstellung nötig.

**Ablauf im Druck**
1. `START_PRINT` setzt den Merker `_PI_MESH_STATUS.druck_mesh` auf 0 und ruft `_PI_MESH_DRUCKBEREICH` mit
   `PRINT_MIN`/`PRINT_MAX` aus der Startzeile:

   | Fall | Messung | Konsole |
   |---|---|---|
   | Datei enthält Objekte (`EXCLUDE_OBJECT_DEFINE`, z. B. Orca „Objekte beschriften“) | Klippers `ADAPTIVE=1` | „Bett nur unter N Objekt(en) vermessen (ADAPTIVE)“ |
   | keine Objekte, `PRINT_MIN`/`PRINT_MAX` vorhanden (Creality Print) | Rechteck Druckbereich + 10 mm (`RAND`), mind. 20 mm (`MIN_GROESSE`), Punktabstand wie im vollen Mesh (≈ 42,5 mm), mind. 3 × 3; `bicubic` ab 4 Punkten je Achse, sonst `lagrange` | „Bett nur im Druckbereich vermessen: X …, Y … (n × m Punkte statt 9 × 9)“ |
   | weder noch | ganzes Bett wie früher | „keine Objekte und kein PRINT_MIN/PRINT_MAX – ganzes Bett vermessen“ |

   Ergebnis im Profil `adaptiv` (nur für die Sitzung); das gespeicherte `default` bleibt unberührt. Danach Merker = 1.
   Mit `START_PRINT … ADAPTIVE=0` wird nur `default` geladen (Merker ebenfalls 1).
2. Jeder weitere `BED_MESH_CALIBRATE` trifft auf die gleichnamige Hülle (`rename_existing: _BED_MESH_CALIBRATE_BASE`):
   **Druck läuft (`printing`/`paused`) und Merker = 1 → übersprungen** („… aus der Druckdatei übersprungen“), das Mesh
   aus Schritt 1 bleibt aktiv. Sonst unverändert durchgereicht.
3. `END_PRINT` (auch über `CANCEL_PRINT`) setzt den Merker auf 0; Neustart/Not-Aus ebenso.

**Von Hand (kein Druck):** nichts wird übersprungen – `G29` (ganzes Bett, Profil `default`, danach ggf. `SAVE_CONFIG`)
und `BED_MESH_CALIBRATE …` wirken wie gewohnt.

**Sicherheit:** Die Hülle von `[k2_nozzle_tempcomp]` (Bezugstemperatur beim Vermessen) liegt *unter* der Makro-Hülle,
weil `k2-module.cfg` vor `macros.cfg` geladen wird (prüfbar: `/printer/gcode/help` zeigt für `BED_MESH_CALIBRATE` die
Makro-Beschreibung). Ein übersprungener Aufruf verstellt die Bezugstemperatur daher nicht. Fehlen Objekte und
`PRINT_MIN`/`PRINT_MAX`, misst er wie früher das ganze Bett – nie schlechter als vorher.

**Weggelassen (Grund):**

| Stock-Makro | Grund |
|---|---|
| `BOX_LOAD_MATERIAL*`, `BOX_QUIT_MATERIAL*`, `BOX_INFO_REFRESH`, `M8200`-Kette (`CR_BOX_*`) | Creality-Box-Befehle (`BOX_*`, `CR_BOX_*`); ersetzt durch die `KTWO_BOX_*`-Kette (§7.1) |
| `LOAD_MATERIAL_CLOSE_FAN2/RESTORE_FAN2`, `FILAMENT_RACK_*`, `SET_COOL_TEMP` | Creality-Befehle; `LOAD_MATERIAL*`/`QUIT_MATERIAL*` sind jetzt als `LOAD_FILAMENT`/`UNLOAD_FILAMENT` ersetzt (s. „Neu“) |
| `Qmode`, `Qmode_exit`, `M204`-Override | Leisemodus mit Creality-`SET_QMODE_FLAG`; M204 = Upstream |
| `IF_NEED_HOME`, `PRINT_TEMP_SET`, `PRINT_PREPARED`, `PRINT_PREPARE_CLEAR`, `MAINTENANCE_ITEM(_PARAM)`, `MOTOR_CANCEL_PRINT` | Display-/App-Hilfen bzw. `BOX_SET_CURRENT_BOX_IDLE_MODE` |
| `PRINT_CALIBRATION`, `ACCURATE_G28`, `BED_MESH_CALIBRATE_START_PRINT` | `CX_PRINT_LEVELING_CALIBRATION`, `ACCURATE_HOME_Z`, regionales Mesh mit `GCODE_FILE` (prtouch_v3) |
| `FIRST_FLOOR_PAUSE(_POSITION)`, `FIRST_FLOOR_RESUME` | Erstschicht-Prüfung über Display/Kamera |
| `ZDOWN_SWITCH_SET`, `BED_MANUAL_CAL(_START/_END)`, `Z_AXIS_CALIBRATION` | `ZDOWN_SWITCH`/`SET_G29_FLAG`/`NEXT_HOMEZ_NACCU` (`[z_align]`/prtouch_v3) | Ersatzablauf `ZALIGN_STOCK` (zalign.cfg, braucht Variante B) |
| `CALIBRATE_CUT_POS` | `MOTOR_SYS_PARAM`/`MOTOR_CHECK_CUT_POS` – braucht k2_motor-G-Codes (O) |
| `BELT_TENSION` | `BELT_MDL_*`; Ersatz-Beispiel `KTWO_BELT_TENSION` (ruft `KTWO_BELT_*`) in README-k2_belt.md – seit dem Modul-Fix gültig, aber bewusst nicht aktiv (fährt G28 X Y + spannt; erst nach Freigabe README-k2_belt.md §6) |
| `AUTOTUNE_SHAPERS` | Creality-Variable (Shaper-Auswahl); Upstream-`SHAPER_CALIBRATE` prüft alle Typen selbst. `INPUTSHAPER` ist seit 26.10.2026 nachgebaut (macros.cfg, speichert nur mit `SPEICHERN=1` statt `CXSAVE_CONFIG`) |
| `NOZZLE_PID_HIGH` | zweiter PID-Satz (Creality) |
| `TUNOFFINPUTSHAPER` | trivial (`SET_INPUT_SHAPER SHAPER_FREQ_X=0 SHAPER_FREQ_Y=0` direkt nutzbar) |
| `SET_CHAMBER_FAN`, `CANCEL_CHAMBER_FAN_SWITCH` | Watermark-Hack bzw. `SET_TEMPERATURE_FAN_SWITCH` (Creality) |
| `SET_PIN`-Override (fan0_en) | Logik steckt jetzt in M106/M107 (direktes `SET_PIN PIN=fan0` schaltet fan0_en NICHT mit) |
| `WAIT_BED_STABLE_POS`, `EXTRUDE_TEST`, `TEST_HOME`, `A_TriPress_STEP1/2`, `A_G28_test`, `START_TIMER`, `END_TIMER` | Werks-/Debug-Tests, teils `BOX_*`/`TEST_REPROBE` |
| `PAUSE_EXTERNAL`, `RESUME_EXTERNAL(_PROCESS)`, `END_PRINT_POINT*`, `END_PRINT_Z_SAFE` | in PAUSE/RESUME/END_PRINT zusammengefasst; `BOX_*`, `BOX_ERROR_RESUME_PROCESS`, `filament_rack`-Geschwindigkeit, `CLEAR_EEPROM_INFO`, `EXCLUDE_OBJECT_RESET` (Creality; Upstream setzt beim Datei-Reset selbst zurück) entfernt |

Namensregel (Upstream, `gcode.py`): Ein Befehl darf nicht mit Buchstabe + Ziffer beginnen (`register_command` lehnt `K2_*` ab: „Can't register … invalid name“) und sollte überhaupt **keine Ziffern** enthalten: der Parser (`_process_commands`, `args_r = ([A-Z_]+|[A-Z*])`) kappt den Namen an der ersten Ziffer (`_K2_X` → `_K2`, `KTWO_485_STATUS` → `KTWO_485`, beides „Unknown command“, obwohl die Registrierung klappt – Batch-Probe G). Deshalb: Hilfsmakros `_PI_*`, Modulbefehle `KTWO_*` (z. B. `KTWO_MOTOR_INIT`, `KTWO_PRTOUCH_READ`, `KTWO_BOX_STATUS`, `KTWO_BELT_TEST`, `KTWO_BUS_STATUS`). Webhook-Endpunkte (`k2_belt_test`) prüft Upstream nicht (`webhooks.register_endpoint`) – sie behalten `k2_`. Config-Abschnitte bleiben `[k2_*]`.

## 3. Installation (Pi, ANNAHME Standard-Klipper-Installation unter `~/klipper`)

Alles liegt in `~/klipper/klippy/extras/` (ein Ziel, keine Pfad-Tricks):

```bash
E=~/klipper/klippy/extras
cp pi-host/k2_transport.py pi-host/k2_485.py pi-host/k2_box.py \
   pi-host/k2_prtouch.py pi-host/k2_belt.py pi-host/k2_limits.py  $E/
cp pi-host/k2_motor_init.py $E/k2_motor.py          # Dateiname = Abschnitt [k2_motor]!
mkdir -p $E/k2_nachbau && cp nachbau/*.py $E/k2_nachbau/
cp pi-host/k2_chamber.py $E/      # Pflicht: [k2_chamber] in printer.cfg (L6, 18.10.2026)
cp pi-host/k2_lis2dw.py $E/       # [k2_lis2dw] in printer.cfg (§7.8)
# optional, nur für zalign.cfg Variante B (A10, ab Testschritt T4, §7.7):
# cp pi-host/k2_zalign.py $E/
# install.sh kopiert ALLE pi-host/k2_*.py (k2_motor_init.py als k2_motor.py)
# und nachbau/ – die Liste oben ist nur der Kern.
```

**Ein-Schritt-Installation (17.10.2026):** `pi-host/install/` baut mit `pack.sh` ein Paket und richtet den Pi mit `install.sh` genau nach diesem Abschnitt ein (Klipper-Checkout 9cb9975, Module inkl. `k2_lis2dw.py`/`k2_zalign.py`, Config mit Backup, Offline-Tests + Batch wie §5, Dienst bleibt gestoppt); Rückbau `uninstall.sh`. Bedienung: `pi-host/install/README.md`.

1. **Module** wie oben. `k2_transport.py` und `k2_nachbau/` sind keine Abschnitte, sondern Helfer.
2. **`nachbau/` = `klippy/extras/k2_nachbau/`** (Festlegung 16.10.2026). Pflicht für `[k2_485]` und `[k2_box]` (serial485, box, cfsseq, auto_addr; motor optional). Suchreihenfolge in `k2_transport.find_nachbau_dir()` (erster Ordner mit `serial485.py`): `$K2_NACHBAU_DIR` → `<extras>/k2_nachbau/` → `<extras>/../nachbau/` (Repo-Layout `pi-host/../nachbau`, Offline-Tests) → `<extras>/../../nachbau/` (alter Weg `~/klipper/nachbau/`, weiter gültig). Der Ordner wird nur **angehängt** (`sys.path.append`), die Module fügen ihren eigenen Ordner **nie** in `sys.path` ein (Geschwister per `from . import k2_transport`); `import_nachbau`/`check_nachbau_module` brechen mit klarer Meldung ab, falls ein gleichnamiges Fremdmodul (z. B. pip-Paket `box`) gefunden wird. Batch-Lauf F/I: `serial485`, `box`, `cfsseq`, `auto_addr`, `motor` laut `python -v` aus `extras/k2_nachbau/`, `statistics` aus der Standardbibliothek.
3. **pyserial** muss im Klipper-venv vorhanden sein (Upstream-Abhängigkeit, i. d. R. schon installiert); fehlt es, meldet `[k2_485]` das beim Connect (kein Absturz).
4. Config-Dateien nach `~/printer_data/config/`.
5. by-id-Pfade der MCU-Adapter in `[mcu]`/`[mcu nozzle_mcu]` eintragen (Main-MCU = USB-RS232-Konverter an J22 Pin 10/4, `usb-FTDI_…`; welcher Port welche MCU ist, zeigt `k2-identify.py`, nur lesend), den des USB-RS485-Adapters in `[k2_485] serial:` (k2-module.cfg).
6. Moonraker-Update-Manager: die kopierten Dateien machen den Klipper-Checkout „dirty“ (ANNAHME: wie bei jedem extras-Zusatzmodul) – nach `git pull` erneut kopieren.

## 4. Inbetriebnahme-Reihenfolge

Jeder Schritt nur, wenn der vorige bestanden ist; nach jedem Schritt `klippy.log` prüfen (SHUTDOWN, Timeouts, Config-Fehler). Hand am Not-Aus, sobald sich etwas bewegen kann. Befehlsnamen seit dem Modul-Fix `KTWO_*` (§2.3 Namensregel).

0. **Vorbereitung:** Verkabelung nach `../../PI-ANSCHLUSS-LOWERBOARD.md` (3,3-V-UARTs, RS485 A/B gemessen, GND zuerst; Gesamtablauf: `../../PI-INBETRIEBNAHME.md`). Alle Sperren wie geliefert (§1).
1. **Nur MCU-Verbindung, ohne Bewegung** (Abweichung vom Plan: k2_prtouch/k2_box/k2_motor bleiben geladen, aber gesperrt – ohne `[k2_prtouch]` gäbe es keinen Z-Endstop). Erwartet: `Klipper ready`, im Log `Loaded MCU 'mcu' … 1.1.0.48-293-g493f9a0f` und dasselbe für `nozzle_mcu`, `FIRMWARE_RESTART` sauber. Prüfen: Temperaturen (Düse, Bett, Kammer ≈ Raumtemperatur), `QUERY_ENDSTOPS` (x/y/z „open“), `QUERY_PROBE` („open“), `M106 P0 S128` / `M107` (Lüfter hörbar), `G28` → muss mit „G28 gesperrt“ abbrechen. `ZALIGN_STATUS` → `zalign_z: RELEASED` / `zalign_z1: RELEASED` (Bett nicht ganz unten). **Abbruch** bei: kein Connect, falsche MCU-Version, Config-Fehler → UART/Baud prüfen, STOP.
2. **Motor-Init Trockenlauf:** `KTWO_MOTOR_INIT DRY_RUN=1` → Plan P0–P9 (167 Schritte), Strapping X 0x81/Y 0x82/Z 0x83/Z1 0x84. Abweichung → Config korrigieren, STOP.
3. **RS485-Bus + Motor-Init lesend:** `KTWO_BUS_STATUS` → `offen=True` (sonst Port/Adapter prüfen; Meldung `[k2_485] Port … nicht zu öffnen` beim Connect). Dann `KTWO_MOTOR_STATUS` (Version/Schutz/Feedback X/Y/Z/Z1 über `k2_485`, E über transparent) und `KTWO_MOTOR_INIT` mit `allow_write: False` – nur Plan, nichts gesendet. `KTWO_BOX_STATUS` (lesend) ebenso.
4. **Motor-Init schreibend:** `allow_write: True`, danach `auto_init: True` → „All motors are normal“, Stall Z/Z1/E, `0C [0b]` 8×00, Param-Abgleich, Event `k2_motor:ready`. Abbruch bei key798–802, STATUS-Bits ≠ 0, `0C`-Fehlerbits → STOP, kein Homing.
5. **PrTouch ohne Bewegung:** `KTWO_PRTOUCH_TEST_SWAP`, `KTWO_PRTOUCH_READ`, `QUERY_PROBE` (open), `KTWO_PRTOUCH_STATUS`; `PROBE` muss mit „allow_move“-Fehler abbrechen (k2_prtouch README A.6). `KTWO_PRTOUCH_READ` schaltet die Düse kurz scharf (Vorabprüfung ohne Bewegung: Ruhetest mit Tickabstand/Rohwert/gefiltertem Kanal) und entschärft wieder – `tick_median_ms`/`filt_max_abs` notieren.
6. **Homing X/Y:** `[k2_motor] allow_write: True`; vorher `KTWO_MOTOR_HOMING PHASE=BEGIN` im Stand + `QUERY_ENDSTOPS` (Polarität PB11/PB12 prüfen, §7.6), dann `KTWO_MOTOR_HOMING PHASE=END`. Danach zur Laufzeit `SET_GCODE_VARIABLE MACRO=_PI_FREIGABE VARIABLE=allow_move VALUE=True`, `G28 Y`, dann `G28 X` (einzeln, Hand am Not-Aus). Erwartet nach G28 X Y: X 342,5 / Y 3,8 (im Batch-Test so). Abbruch bei Schrittverlust/Blockade.
7. **Z mit Probe:** `[k2_prtouch] allow_move: True`; Düse ~3 mm über dem Bett, `SET_KINEMATIC_POSITION Z=10`, `PROBE` (Stopp beobachten), `PROBE_ACCURACY SAMPLES=5`, dann `G28` (Z in Bettmitte, ≤ 5 mm/s), `Z_TILT_ADJUST`, `G28 Z`, `BED_MESH_CALIBRATE PROFILE=default` (bzw. `G29`), Ergebnis gegen `stock_referenz_2026-10-16` (−0,067 … 0,226) plausibilisieren, `SAVE_CONFIG`.
7a. **A10 Dual-Z** nach §7.7 (Testprotokoll; mindestens T3 vor dem ersten Druck).
8. **Heizen/Kammer:** Düse/Bett aufheizen, ggf. `NOZZLE_PID`/`BEDPID`; `M141 S45` → PTC-Lüfter (!PB14/PB2) MUSS sofort laufen, sonst sofort `M141 S0` (§6).
9. **Erstdruck einfarbig** (Slicer: CFS aus; `START_PRINT EXTRUDER_TEMP=… BED_TEMP=…`).
10. Optional je Modulreife (alle über `k2_485`): Belt lesend (`KTWO_BELT_INFO`/`KTWO_BELT_TEST`), dann `allow_write`/`allow_move` laut README-k2_belt.md §6 (CALI nur ohne Riemen); CFS lesend → schreibend (`[k2_box] allow_write: True`, Wechsel-Makros).

## 5. Offline-Validierung (Upstream-Klipper, Batch-Modus, ohne Hardware)

### 5.1 Aufbau

- Upstream-Klipper Commit 9cb9975 als Kopie OHNE `.git` in einem git-ignorierten Arbeitsordner (hier `lokal/upstream-klipper/`, Referenz-Klon unverändert daneben).
- Läufe A–E (Stand 16.10.2026 19:44): Module → `klipper/klippy/extras/`, `nachbau/*.py` → `klipper/nachbau/`; B mit Test-Patches.
- **Läufe F–I (Modul-Fix 16.10.2026 abends, OHNE Testpatches):** frische Kopie des Referenz-Klons, darin NUR unsere Module nach dem Installationsweg §3 (`extras/k2_*.py`, `k2_motor_init.py` als `extras/k2_motor.py`, `nachbau/*.py` → `extras/k2_nachbau/`).
- Lokales venv `lokal/venv` (`--system-site-packages`, nachinstalliert nur `greenlet 3.3.2` + `pyserial`; cffi 2.1.1/Jinja2 3.1.6 vom System). Python 3.14.8.
- MCU-Dictionaries der Creality-Firmware (NICHT im Repo, hier `lokal/mcu-dicts/`; Roh-JSON, ohne Anpassung lesbar): Main `gd32f303xe`, Düse `gd32f303xb`, beide `1.1.0.48-293-g493f9a0f`.
- Befehl (aus dem Repo-Wurzelordner, `KLIPPER` = Klipper-Kopie mit installierten Modulen):

```bash
KLIPPER=lokal/upstream-klipper; D=lokal/mcu-dicts; PY=lokal/venv/bin/python
$PY $KLIPPER/klippy/klippy.py pi-host/config-pi/printer.cfg \
  -i /dev/null -o lauf-X/out.serial -v \
  -d $D/mcu-main-F008-GD32F303RET6.dict.json \
  -d nozzle_mcu=$D/noz-duese-F008-GD32F303CBT6.dict.json \
  -l lauf-X/klippy.log
# Dekodieren:
$PY $KLIPPER/klippy/parsedump.py $D/mcu-main-F008-GD32F303RET6.dict.json lauf-X/out.serial
$PY $KLIPPER/klippy/parsedump.py $D/noz-duese-F008-GD32F303CBT6.dict.json lauf-X/out.serial-nozzle_mcu
```

(`-d <datei>` = Main-MCU, `-d <mcu-name>=<datei>` = weitere MCU, siehe `arg_dictionary` in `klippy.py`; Ausgabe der Zusatz-MCU in `<-o>-nozzle_mcu`.)

### 5.2 Ergebnisse

| Lauf | Inhalt | Ergebnis |
|---|---|---|
| **A** | Auslieferung, ORIGINAL-Module | **Abbruch:** `Can't register 'K2_PRTOUCH_PROBE_STEPS' as it is an invalid name` |
| (während der Erstellung) | dito, nacheinander | `Can't register 'K2_BOX_STATUS' …`; k2_motor: `Unknown config object 'nozzle_mcu'` (→ Config: `mcu nozzle_mcu`), danach `Unknown config object 'ppins'` |
| **B** | Auslieferung, Module mit Test-Patches (§5.3) | **EXIT 0, fehlerfrei**: Config geparst, keine unbekannten Optionen, MCU-Konfiguration für beide MCUs erzeugt (`Configured MCU 'mcu' (500 moves)`, dito `nozzle_mcu`). Beim Connect erwartungsgemäß: `[k2_box] Connect ohne Bus …`, `[k2_belt mdlx]`/`[k2_belt mdly] RS485-Bus nicht verdrahtet …` (kein `k2_485`, §6.2) |
| B-makros/-homing/-luefter/-druck | Test-G-Code (`test-*.gcode`) | Sperren greifen: `G28 gesperrt: _PI_FREIGABE allow_move=False`, `G28 X/Y gesperrt: … xy_stall_ok=False`, G28 Z → `[k2_prtouch] … allow_move: False … Keine Bewegung`; `T0` ok, `T1`/`M8200` → CFS-Fehler; Homing X/Y mit Freigabe: zuerst Y-Endstop (oid PB12, `pin_value=1`), dann X (PB11), Position danach 342,5/3,8; `PAUSE`/`RESUME` ok; `QUERY_PROBE` → `probe: open`; Lüfter dekodiert korrekt (z.B. `M106 P0 S128` → fan0_en an, PB15 invertiert 539054/1200000 Ticks; `M106 P1 S100` → PA0 613610) |
| C | + Stock-`[lis2dw]` | `Command format mismatch: query_lis2dw oid=%c rest_ticks=%u vs query_lis2dw oid=%c clock=%u rest_ticks=%u` → lis2dw entfällt |
| C2 | + Stock-`mcu_temp` | im Batch-Modus kein Fehler, weil `temperature_mcu` mit `-o` die MCU-Prüfung überspringt (`debugoutput`-Zweig) – statisch: `gd32f303xe` → `config_unknown` → Abbruch am Gerät |
| D | `duplicate_pin_override` ohne `nozzle_mcu:PB1` | `pin PB1 used multiple times in config` → damals Ergänzung; **überholt 22.10.2026:** Ursache war `heater_fans` mit Düsen-PB1 statt Main-PB1 – mit Stock-Belegung (`nozzle_mcu:PB7, PB1`) keine Doppelnutzung, Klipper am Gerät ready |
| E | `[k2_prtouch] allow_move: True` + G28 | Signalweg-Test schlägt fehl (`ausgelöst=False bei sta_swap=0`, keine MCU-Antworten im Batch) → **keine** Z-Fahrt; Z/Probe/Mesh offline nicht prüfbar |
| **F** | Auslieferung, **gefixte Module ohne Testpatches** (`klipper-clean/`), `[k2_485]` aktiv | **EXIT 0, fehlerfrei** (kein Config-/Internal error, kein Traceback außer den üblichen `.version`/`device-tree`-Hinweisen). Connect: `[k2_485] Batch-Modus: RS485-Port … wird nicht geöffnet`, `[k2_box] bereit (warte auf k2_motor:ready …)`; `[k2_belt]` findet den Bus (keine „nicht verdrahtet“-Meldung mehr) |
| F-makros/-homing/-luefter/-druck | Test-G-Code wie B | Fehlerbild identisch zu B (nur `K2_`→`KTWO_`); EXIT 255 bei makros/homing/druck = die gewollten Sperr-Fehler (`G28 gesperrt …`, CFS-Sperre) im Batch, luefter EXIT 0 |
| **G** | wie F + `test-485.gcode` (`KTWO_BUS_STATUS`, `KTWO_MOTOR_INIT DRY_RUN=1`, `KTWO_MOTOR_STATUS`, `KTWO_BOX_STATUS`, `KTWO_BELT_TEST`, `KTWO_PRTOUCH_STATUS`) – `[k2_485]` ohne vorhandenen Port | kein Absturz, kein Shutdown: `KTWO_BUS_STATUS` → `offen=False batch=True fehler=Batch-Modus …`; Plan P0–P9 mit Strapping 0x81–0x84 ok; `KTWO_MOTOR_STATUS` X/Y/Z/Z1 → `[k2_485] Batch-Modus …`, E → `transparent … keine Antwort`; Box `Poll-Fehler: [k2_485] …`; Belt → G-Code-Fehler `RS485-Bus nicht verfügbar …`. (Vor dem Belt-/Box-Härtungsfix führte genau das zu `Internal error` + Shutdown, und der erste Name `KTWO_485_STATUS` zu `Unknown command:"KTWO_485"` → umbenannt) |
| A10 | Dual-Z-Lichtschranken (zalign.cfg L/B) | 13 Läufe, 28/28: L/B EXIT 0, Z nur über trsync(PA15), Z1 nur über trsync(PA8), 370 mm/148 Etappen/10 mm/s, Sperren ohne Z-Schritt, Konflikt L+B erkannt, Erfolgsweg mit Test-Fake |
| RESUME-Schnitt | RESUME nach CFS-Abbruch (§7.2) | 11 Läufe, Bahnprüfung 151/151 aus MCU-Schritten (17.10. spät: + Kopf leer/Nachholen/ohne Sensor): nach CFS-Abbruch Hebel nur in X und einmal angefahren, nie unter Pausen-Z, Endlage = Druckposition vor dem Wechsel, E unverändert; Sperr-Läufe ohne Bewegung (EXIT 255 gewollt) |
| **H** | wie G, aber **ohne** `[k2_485]` | kein Absturz: `[k2_motor] RS485-Bus fehlt: Abschnitt [k2_485] (serial: <by-id-Pfad>, baud: 230400) in printer.cfg anlegen …`, Box/Belt „Abschnitt [k2_485] fehlt in printer.cfg“, `KTWO_BUS_STATUS` unbekannt (erwartet), DRY_RUN-Plan geht |
| **I** | wie F, `[k2_prtouch]` ans Ende von printer.cfg verschoben, `python -v` | EXIT 0, fehlerfrei; `statistics` aus `/usr/lib/python3.14/statistics.py`, `extras/statistics.py` nie geladen; nachbau-Module aus `extras/k2_nachbau/` → Reihenfolge egal |

**Stichproben aus Lauf F (parsedump, gefixte Module):**

- `allocate_oids count=37` (Lauf B: 38 – das Query-OID ohne config-Befehl entfällt, §5.3 Punkt 6).
- Endstop `config_endstop oid=0 pin=PC7 pull_up=1` (seit 17.10.2026 `^!PC7`) + `config_trsync oid=1`; `config_prtouch_step oid=10 oid_xstp=4 oid_ystp=7 oid_zstp=9 swp_pin=PC7`.
- Strapping-Pins `config_digital_out` oid 11–19: PB9, PB10, PB7 (value 1 = `!PB7`), PB8, PB5, PB6, PA1, PB15, PA9 (value 1 = `!PA9`) – **alle `max_duration=0`** (Lauf B: `max_duration=240000000` = 2 s); danach `config_stepper oid=4 PB8/PB7`, `oid=7 PB10/PB9`, `oid=8 PB6/PB5`, `oid=9 PB15/PA1`, alle `step_pulse_ticks=240`; stepper_enable PA9 als oid 36 (ebenfalls `max_duration=0`).
- Düse: `config_prtouch_pres oid=2 idx=0 swp_pin=PA15 clk_pin=PB13 sdo_pin=PB14`, `config_transparent oid=0`, `config_stepper oid=1 step_pin=PB5 dir_pin=PB4`.
- Kein `query_gpio_status`/`transparent_send` im Batch (Init läuft nur auf Befehl / `auto_init`).

**Stichproben aus Lauf B (parsedump):**

- Stepper Main: `config_stepper oid=5 step_pin=PB8 dir_pin=PB7` (X, `!` Host-seitig), `oid=8 PB10/PB9` (Y), `oid=9 PB6/PB5` (Z), `oid=10 PB15/PA1` (Z1); alle `step_pulse_ticks=240` (2 µs @ 120 MHz, Upstream-Default).
- Endstops: `config_endstop oid=0 pin=PC7 pull_up=1` (seit 17.10.2026 `^!PC7`) (Probe, Invertierung `!PC7` Host-seitig) + `config_trsync oid=1`; `PB11` (oid 3), `PB12` (oid 6).
- `config_prtouch_step oid=11 oid_xstp=5 oid_ystp=8 oid_zstp=10 swp_pin=PC7` (zstp = stepper_z1, wie Stock).
- Düse: `config_transparent oid=0` (k2_motor), `config_prtouch_pres oid=2 idx=0 swp_pin=PA15 clk_pin=PB13 sdo_pin=PB14`, `config_stepper oid=1 step_pin=PB5 dir_pin=PB4`, Heizung PB8 (PWM 12 000 000 Ticks = 0,1 s), ADC PA0, Filamentsensor `buttons_add pin=PA11 pull_up=1` + `invert=1`.
- k2_motor-Strapping-Pins als `config_digital_out` PB9, PB10, PB7 (value 1 = `!PB7` logisch 0), PB8, PB5, PB6, PA1, PB15, PA9 (value 1 = `!PA9` aus) – vor den `config_stepper` derselben Pins (Zeitmultiplex wie Stock).
- PC5 dreifach `config_analog_in`, PA0 zweifach PWM (temperature_fan + fan1), PB2 zweifach (heater_fan-enable + ptc_power), Düsen-PB1 zweifach – alles wie Stock.

### 5.3 Gefundene Modul-Probleme – BEHOBEN 16.10.2026 abends (Lauf F–I ohne Testpatches)

Ursprünglich nur per Test-Patch umgangen (Lauf B).

1. **G-Code-Namen `K2_*` ungültig** (`gcode.py register_command`: 2. Zeichen Ziffer) → alle registrierten Befehle in k2_motor, k2_box, k2_prtouch, k2_belt auf `KTWO_*` umbenannt (Mux-Befehle von k2_belt ebenso; Stock-Aliase `BELT_MDL_*` waren schon gültig). Webhook-Endpunkte `k2_belt_test`/`belt_mdl_test` bleiben (Upstream prüft Endpunkt-Namen nicht). Neu `KTWO_BUS_STATUS` ([k2_485]). Tests prüfen jetzt jeden registrierten Namen gegen die Upstream-Regel inkl. Parser-Zerlegung (test_k2_prtouch mit der echten `GCodeDispatch.register_command` + `args_r`).
2. **k2_motor `lookup_object("ppins")`** → `"pins"` (`KlippyGpio`); die Pin-Nummer für `query_gpio_status` kommt aus der MCU-Enumeration (Upstream-`pins.lookup_pin` liefert nur Namen und markiert den Pin als belegt – wird nicht mehr aufgerufen). k2_prtouch-Altcode ebenfalls auf `pins`.
3. **k2_motor `_build_transparent_config(self)`** ohne Argument (Upstream ruft `cb()`).
4. **k2_motor `nozzle_mcu`** wird wie in k2_prtouch aufgelöst (`_lookup_mcu`: erst `"mcu " + name`, dann wörtlich) → Config wieder `nozzle_mcu: nozzle_mcu`.
5. **Import-Falle `sys.path.insert(0, <klippy/extras>)`** beseitigt: kein Modul fügt seinen Ordner in `sys.path` ein, Geschwister per `from . import k2_transport` (Fallback `import k2_transport` nur offline), `nachbau/` per `k2_transport.ensure_nachbau_path()` nur angehängt (§3). Die Workaround-Reihenfolge „[k2_prtouch] vor den Includes“ ist damit unnötig (Lauf I).
6. **Strapping-Pins `max_duration`:** jetzt `setup_max_duration(0.0)` für alle Dir/Step-Pins + `!PA9` – wie Stock: das Stock-`.so` ruft in `motor_x/y/z/z1_init` und für `enable_pin_pa9` `setup_max_duration` mit Konstante `0.0` direkt nach `setup_pin('digital_out', …)` auf. Entspricht dem Upstream-Muster für statische Pins (`output_pin`, `stepper_enable`).
7. **Abfrage-OID `query_gpio_status`:** Der Firmware-Handler liest nur `gpio_pin` (GD32 `OCTL` = Ausgangs-Latch) und schickt `gpio_status` mit der OID **unverändert** zurück – kein `oid_lookup`, kein `config_*` nötig. Deshalb keine `create_oid` mehr (Konstante 0, `allocate_oids` 38 → 37).
8. **Neu [k2_485]** (Punkt 4 des Auftrags): `pi-host/k2_485.py`, s. §6.2. Zusätzlich gehärtet (Batch-Probe G): k2_belt (`_xfer`) und k2_box (`KTWO_BOX_LOAD/UNLOAD/PRELOAD`), k2_motor (`KTWO_MOTOR_INIT`/Auto-Init) melden Bus-/Port-Fehler (`RuntimeError`/`OSError`) als G-Code-Fehler statt „Internal error“ + Klipper-Shutdown.

### 5.4 Offline prinzipiell nicht prüfbar

RS485-Port und alles darüber (`[k2_485]` öffnet im Batch-Modus bewusst nie; Motor-Init, Box, Belt nur über Fake-Seriell in den Offline-Tests), Antworten der MCUs (`query_gpio_status`, `transparent_*`, `*_prtouch`, Endstop-Zustände, ADC-Werte → Heizregelung, Watermark, heater_fan-Reaktion), Stopp unter Bewegung (trsync PC7), `temperature_mcu`-Prüfung (im `-o`-Modus übersprungen), tatsächliche Pin-Pegel/Polaritäten, Zeitverhalten.

## 6. Offene Punkte und ANNAHMEN

1. **Modul-Fixes §5.3** – erledigt 16.10.2026 abends (Lauf F–I). Am Gerät ungetestet.
2. **RS485-Bus `[k2_485]`** (`pi-host/k2_485.py`): öffnet GENAU EINE `nachbau/serial485.Serial485`-Instanz (`serial:` Pflicht, `baud:` Default 230400, `timeout:` 1,0 s). Klipper legt das Objekt beim Laden unter `k2_485` ab (`load_object` = Wirkung von `add_object`); `[k2_motor]` lädt es bei Bedarf selbst vor (`has_section` + `load_object`) und nutzt es für X/Y/Z/Z1 (`Rs485Transport`), `[k2_box]`/`[k2_belt]` holen es per `lookup_object('k2_485')` → Reihenfolge egal. Port: Öffnen erst bei `klippy:connect`, faul erneut bei jedem Zugriff, Schließen bei `klippy:disconnect`/Ausfall (`OSError`, nicht `TimeoutError`). **Ohne Port:** Batch-Modus → nie geöffnet, Meldung `Batch-Modus: RS485-Port … wird nicht geöffnet`; Normalbetrieb → Meldung `[k2_485] Port … nicht zu öffnen (…)` beim Connect, kein Absturz/Shutdown, `get_status`/`KTWO_BUS_STATUS` zeigen `offen=False` + Fehler, Zugriffe der Module → G-Code-Fehler. **Fehlt `[k2_485]`:** `[k2_motor] RS485-Bus fehlt: Abschnitt [k2_485] … anlegen` (nur `DRY_RUN=1` geht), Box/Belt analog. ANNAHME: USB-RS485-Adapter mit Auto-DE (Halbduplex ohne RTS-Steuerung, wie `serial485.py`). OFFEN: Bus-Zugriffe sind blockierend (Serial485-Lock + pyserial) und laufen im Klipper-Reaktor-Thread (bei Box-Poll/Init kurzzeitige Blockade, Stock-Wrapper ähnlich) – am Gerät auf „Timer too close“ achten.
3. **ERLEDIGT 16.10.2026 (§7.6):** Stall-Mode für X/Y-Homing wie Stock (vor dem Homing `MOTOR_STALL_MODE DATA=1` = Endschalter-Signal auf PB11/PB12, danach `DATA=2` + Schutzprüfung + Kp ID 70 300→100) über `KTWO_MOTOR_HOMING PHASE=BEGIN/END` in `_PI_VOR_HOMING`/`_PI_NACH_HOMING`.
4. **Stock-`FORCE_MOVE STEPPER=stepper_y DISTANCE=-3` nach dem XY-Homing:** seit 16.10.2026 spät übernommen (§7.6; CoreXY: Versatz je 1,5 mm in X/Y ohne Positionsupdate, die Stock-`position_endstop`-Werte gelten mit diesem Versatz). Koordinaten am Gerät gegen Stock prüfen (Purge/Wischposition Y 374–378!), ggf. `position_endstop` anpassen.
5. **Düsenreinigung vor dem Antasten:** seit 16.10.2026 spät `_PI_NOZZLE_CLEAN` (Stock `BOX_NOZZLE_CLEAN` am Silikonstreifen, §7.2). Stock-`NOZZLE_CLEAR` (Düse aufs Bett drücken) bewusst nicht übernommen.
6. **prtouch_v3-Kompensationen** (Temperatur nichtlinear, `bed_mesh_fall_comp_*`, `need_steady_regional`, `prth_min_fans`) fehlen in k2_prtouch.
7. **PTC-Lüfter:** ANNAHME, dass `is_ptc_fan` nur „läuft, solange die Kammerheizung ein Ziel hat“ bedeutet – am Gerät prüfen (Schritt 8), sonst Überhitzungsgefahr.
8. **ERLEDIGT 16.10.2026 (`[k2_lis2dw]`, §7.8):** Beschleunigungssensor über eigenes Modul für das alte Creality-lis2dw-Protokoll; am Gerät ungetestet, Erstlauf `../README-k2_lis2dw.md` §7.
9. **MCU-Temperatur** (gd32f303xe/xb) nicht in Upstream; optional eigenes Mini-Modul.
10. **Hochtemperatur-PID** (Stock ab 280 °C eigener Satz) fehlt.
11. **`[idle_timeout]` 99999999** wie Stock: Heizungen gehen nie automatisch aus – bewusst entscheiden.
12. **ERLEDIGT 16.10.2026 (§7.1, nur Box 1):** CFS-Wechsel (`T1–T15`, `M8200`) über die `KTWO_BOX_*`-Kette, Schneidposition über `CALIBRATE_CUT_POS` (§7.5).
13. **`[k2_belt]`** eingetragen, aber gesperrt; Rüttel-Zyklus (Default-`cycle_gcode`) fährt X/Y per FORCE_MOVE ±200 mm ohne Endschalterschutz – erst nach X/Y-Homing-Freigabe.
14. **`[z_align]`-Ersatz:** lesend aktiv, `[k2_zalign]` als Entwurf gesperrt (§7.7), Gerätetest offen; `START_PRINT` holt `Z_TILT_ADJUST` nach Stromlos nach (A10a).
15. **ANNAHMEN:** by-id-Namen; RS485-Adapterpfad; `~/printer_data/gcodes`; Bedeutung `power` PC9; PA0 doppelt angesteuert (fan1 + temperature_fan, wie Stock) unkritisch; Düsen-PB1 nur `extruder_fan` (Korrektur 22.10.2026: Stock-`PB1` in `heater_fans` = Main-PB1); `minimum_cruise_ratio 0.0` ≙ `max_accel_to_decel = max_accel`; `move_check_distance 3` statt 1 unkritisch; Upstream-Default `step_pulse_duration` 2 µs passt zu den Closed-loop-Treibern; kein `M84` am Druckende (wie Stock).

## 7. Bedienhinweise

Alles ENTWURF und am Gerät weitgehend UNGETESTET. Grundregeln für jeden Schritt mit Bewegung: Bett leer, Hand am Not-Aus, nach jedem Schritt `klippy.log` prüfen, Freigaben einzeln setzen (§1 Sicherheits-Defaults). Bricht etwas ab: STOP, Ursache klären, nichts „nachjustieren“.

| Thema | Dateien | Kurz |
|---|---|---|
| CFS-Farbwechsel | §7.1, `macros.cfg`, `k2-module.cfg`, `../README-k2_box.md` §8 | T0–T15 + M8200 P/C/R/L/W/F/O als `KTWO_BOX_*`-Kette, nur Box 1, ohne Freigaben nur Plan, Fehler im Druck → PAUSE mit Rückfahrziel = Druckposition vor dem Wechsel (§7.2) |
| Düsenreinigung + PAUSE/RESUME | §7.2, `macros.cfg`, `homing.cfg` | `_PI_NOZZLE_CLEAN` (Silikonstreifen wie Stock `BOX_NOZZLE_CLEAN`) vor dem ersten Z-Antasten, in START_PRINT und G29; RESUME: Schacht → Aufheizen → Vorextrusion → Reinigung → Rückfahrt erst X/Y, dann Z (nie unter Pausen-Z); nach CFS-Abbruch RESUME-Sperre bei leerem Kopf, optional Nachholen `resume_redo_change` (Default AUS) |
| Spülen + Material-Datenbank | §7.3, `k2-module.cfg`, `../README-k2_box.md` §12 | Spülmenge aus RFID-Farbe bzw. Slicer-Spülmatrix, Temperaturen/Geschwindigkeiten aus `material_database.json` (optional) |
| CFS-Nachladen bei leerer Spule | §7.4, `k2-module.cfg`, `printer.cfg` (Filamentsensor), `../README-k2_box.md` §9 | `runout_gcode: KTWO_BOX_RUNOUT`, Ersatz-Slot gleiches Material + Farbe (RFID) oder eigene Gruppe; Default aus (`runout_refill: False`), sonst PAUSE |
| Schneidposition kalibrieren | §7.5, `cutcal.cfg`, `../README.md` §10 | `CALIBRATE_CUT_POS [SAVE=1]` → `KTWO_MOTOR_CUT_CAL` (Stock-Folge Kp 300, Stall-Mode 3, Anstöße an PB11, +0,4, Mode 2, Kp 100); Wert über SAVE_CONFIG in `[k2_box] cut_pos_x` |
| Sensorless X/Y-Homing + Stall-Überwachung | §7.6, `homing.cfg`, `k2-motor.cfg`, `../README.md` §9/§11 | Stock-Frame-Folge (Stall-Mode 1/2 an X/Y, Kp ID 70 300↔100, Schutzabfrage) über `KTWO_MOTOR_HOMING PHASE=BEGIN/END`; `FORCE_MOVE stepper_y -3` wie Stock; `stall_monitor` report/act |
| Dual-Z-Lichtschranken (17.10.2026) | §7.7, `zalign.cfg`, `../k2_zalign.py` | lesend `[gcode_button]`; Ausrichtung `KTWO_ZALIGN` / `ZALIGN_STOCK` gesperrt; Testprotokoll T0–T8; `START_PRINT` → `_PI_ZTILT_WENN_NOETIG` |
| Beschleunigungssensor | §7.8, `printer.cfg`, `../README-k2_lis2dw.md` | `[k2_lis2dw]` spricht das alte Creality-Protokoll, liefert Upstream-Datenstruktur; Erstlauf im Stand (`ACCELEROMETER_DEBUG_READ REG=0x0f` → 0x44) |
| Fahrgrenzen wie Stock (18.10.2026, L3) | `../README-k2_limits.md`, `printer.cfg` (`[stepper_*]`, `[k2_limits]`), `macros.cfg`, `cutcal.cfg` | Betriebsfenster X ab `cut_pos_x`, Y bis 352, Z bis 350,2; Aufweiten nur gehomt für Schacht/Wischen/Schneid-Kalibrierung/RESUME-Schacht, immer mit Rücksetzen; `G1 Z<-2` außerhalb des Drucks abgelehnt; PAUSE parkt Y176 (wie Stock) |
| Transparent-Kanal E-Motor (18.10.2026, L2) | `../k2_transport.py`, `../test_k2_485.py` | `transparent_send` genau EINMAL (eigener Sende-Helfer statt Upstream-Wiederholung), Warten bis 60 s wie Stock `ExtruderRetryAsyncCommand`; Upstream-`is_async` allein sendet alle 0,5 s neu |
| Kammer-Obergrenze in M141/M191 (18.10.2026) | `macros.cfg` | Soll vor Setzen/Warten auf `[k2_safety]`-Grenze (`printer.k2_safety.limits.chamber_heater`, sonst 60) begrenzt – sonst wartete `M191 S70` endlos auf 69 °C |
| PTC-Kammerheizung absichern (18.10.2026, L6) | `../README-k2_chamber.md`, `printer.cfg` (`[heater_fan chamber_fan]` + `[k2_chamber]`), `macros.cfg` (M141/M191) | Tacho `tachometer_pin: PC6` (Stock `fan_feedback fan0_pin`, ohne Pull-up); PTC-Lüfter < 100 rpm für 15 s bei Kammer-Ziel/PWM > 0 → Kammerheizung sofort aus + `!!`-Meldung, Sperre bis `KTWO_CHAMBER_RESET` (Stock: `M141 S0` + key519, kein Shutdown; Option `fan_fault_action: shutdown`); Bett-Vorrang (PTC 0, solange Bett > 2 °C unter Ziel); 50-%-Anlauf 60 s je Heizphase; M191 wartet über `KTWO_CHAMBER_WAIT` (bricht bei Lüfterfehler ab). Batch-Läufe P/Q/R |
| Externe Spule (19.10.2026) | `macros.cfg`, `k2-module.cfg` (`filament_source_file`, `extern_rfid`), `printer.cfg` (`[gcode_button extern_spule]` auskommentiert), `../README-k2_box.md` §18 | Quelle CFS/EXTERN vom User (Schlauch von Hand umgesteckt), persistent; bei EXTERN alle CFS-Aktionen gesperrt (nichts gesendet; einzige Ausnahme wie Stock: `0x0d [0f 00/01]` + `0x0f [01/00]` aus START_PRINT/END_PRINT, kein Entladen am Druckende), T<n> im Druck: erstes kein Wechsel, anderes → PAUSE; `LOAD_FILAMENT`/`UNLOAD_FILAMENT` nach Stock-Ablauf aus den k2_box-Bausteinen; Material/Temperatur gespeichert wie am Stock-Display; RFID 0x11 optional (Default AUS) |

### 7.1 CFS-Filamentwechsel (T0–T15, M8200)

Dateien: `macros.cfg` (`T0`–`T15` → `_KTWO_TOOL_CHANGE T=n`, `M8200` P/C/R/L/W/F/O wie Stock-box.cfg, `RESUME`-Sperren), `k2-module.cfg` `[k2_box]` (Freigaben `allow_write`/`allow_move`, beide False; `pause_on_failure: True`; alle Stock-`[box]`-Werte), Modul `../k2_box.py`. Technik: `../README-k2_box.md` §8.

- **Zuordnung** (Stock box.cfg `M8200 L`): Werkzeug `n` → Box `n/4+1`, Slot `"ABCD"[n%4]` – `T0..T3 = T1A..T1D`. **Nur Box 1 ist unterstützt** (`box_addr: 1`); T4–T15 werden mit Meldung abgelehnt.
- **Ablauf eines `T<n>`** (= Stock-T0-Beispiel aus box.cfg):
  1. `PRE_OPT`: schon geladen (Hub-Sensor + Kopfsensor)? → fertig. Sonst Kopfsensor sperren, G-Code-Zustand sichern, Z +0,4.
  2. `CUT`: X10 Y200 → X −7,80 (Schneidhebel) → X10. Entfällt, wenn der Kopfsensor sicher LEER meldet UND der Hub leer ist (Erst-Laden zu Druckbeginn).
  3. `RETRUDE`: Schacht (Y345 → X139 → Y378 → X133), Wechsel-Temperatur (max(Ziel, 220) bzw. Material-DB), Rückzug in die Box.
  4. `EXTRUDE`: Box fördert, bis der Kopfsensor anspricht (max. 90 s), Extruder greift `E+30`, Box wieder im Druckzustand.
  5. `WASTE`: nur Hinweis. 6. `FLUSH`: Spülen (§7.3).
  7. `END_OPT`: Schacht verlassen (X160 → Y345), zurück auf Start-XY, Z runter, Zieltemperatur zurück, Kopfsensor + Zustand zurück.
- **M8200-Kette** (Creality Print schickt entweder `T<n>` oder `M8200 P / C / R / L I<n> / F / O`): bei der Kette macht `PRE_OPT` keinen Z-Hop und `END_OPT` keine Rückfahrt – das erledigt der Slicer. `M8200 F … L…` löst KEINEN zusätzlichen Einzug aus.
- **Box-Druckzustand:** `START_PRINT` ruft `KTWO_BOX_PRINT_START`, `END_PRINT` nach dem Entladen `KTWO_BOX_PRINT_END` (beides nur mit Freigabe, Fehler nur Meldung). Endet ein Druck ohne `END_PRINT` (z. B. Klipper-Fehler), von Hand `KTWO_BOX_PRINT_END`.
- **Restmaterial + Druckende:** Meldet die Box im Druck „Spule leer“ (Rest noch im Schlauch), spült der nächste Wechsel den Rest aus statt zu schneiden und zurückzuziehen (`ending_material_purge`, Default AN; höchstens `max_tube_length` 3000 mm; Kopfsensor ohne Zustand → Pause, nie blind fördern). Am Druckende entlädt `END_PRINT` über `KTWO_BOX_END_PRINT` (heizen, schneiden, zurück in die Box – kein Spülen), wenn `end_print_unload` (Default AN, Stock entlädt immer) gesetzt oder ein Restmaterial-Fall offen ist. Von Hand: `KTWO_BOX_ENDING [SET=1 SLOT=] [CLEAR=1] [PURGE=1]`.

**Inbetriebnahme** (Reihenfolge, Hand am Not-Aus). Voraussetzung: Homing + Bewegung freigegeben (§4), `[k2_485]` mit echtem Port, `KTWO_BOX_STATUS` liefert Zustand/Hub/Temperatur.

1. Lesend: `KTWO_BOX_STATUS`, `KTWO_BOX_WAIT`, `QUERY_FILAMENT_SENSOR SENSOR=filament_sensor`. `T1` → nur Plan (alles gesperrt), Plan lesen.
2. `allow_move: True` (Box noch gesperrt), ohne Filament im Kopf: `KTWO_BOX_CUT` einzeln – prüfen, dass der Kopf den Schneidhebel trifft (X −7,80 aus der Stock-Kalibrierung; Y200). Dann `KTWO_BOX_GO_EXTRUDE_POS`, `KTWO_BOX_WIPE`, `KTWO_BOX_SAFE_POS` (Kollisionen mit Schacht/Silikon?).
3. `allow_write: True`: `KTWO_BOX_RETRUDE` (Slot aus Hub-Sensor), `KTWO_BOX_EXTRUDE TNN=T1B`, `KTWO_BOX_FLUSH` einzeln, Düse auf 220 °C.
4. Erst dann ganze Wechsel `T1`, `T0`, … außerhalb eines Drucks, zuletzt im Druck (Creality Print, CFS an).

**Fehler im Wechsel:**

- Im Druck: `!! [k2_box] <Schritt> fehlgeschlagen: …` → Druck PAUSIERT (kein Abbruch/Shutdown), restliche Schritte werden übersprungen, Kopfsensor und G-Code-Zustand werden zurückgesetzt, der Kopf bleibt stehen. Weiter: Ursache beheben (z. B. Filament im Hub, Box-Fehler 0x0c = Odometer-Stillstand), **erst `KTWO_BOX_ERROR_CLEAR`** (setzt auch die Box auf IDLE), **dann** Schritt bzw. ganzen Wechsel `T<n>` von Hand wiederholen, dann `RESUME` (§7.2). Ein `T<n>` bei offenem Fehler lehnt `PRE_OPT` ab – es passiert nichts.
- Ein Einzug bricht bei 0x0c nicht sofort ab (wie Stock: Düsenreinigung + Neuversuch); erst danach der Fehler oben. `KTWO_BOX_LOAD/UNLOAD` von Hand verweigern bei kalter Düse (unter `min_extrude_temp`) – erst `M109 S220`.
- **Druck abbrechen statt fortsetzen:** `CANCEL_PRINT` in der Pause mit offenem Wechsel-Fehler → `END_PRINT CANCEL=1` entlädt nur, wenn sicher (Fehler beim Einzug/Spülen + Kopfsensor meldet Filament), sonst Meldung „NICHT entladen“ (dann nach dem Abbruch von Hand: `KTWO_BOX_CUT`, `KTWO_BOX_UNLOAD SLOT=…`). Danach setzt `KTWO_BOX_CANCEL_RESET` den Fehlerzustand zurück – das nächste `T<n>` startet normal. In der Pause selbst ändert sich nichts an der RESUME-Sperre.
- Abbruch WÄHREND eines Wechsels (Mainsail/Fluidd „Cancel“, `[k2_box] cancel_interrupt`, Default an): die Warteschleifen enden, die übrigen Schritte laufen leer – keine Pause, kein Wechsel-Fehler, dann `CANCEL_PRINT`. Einzelne Box-Antworten (Rückzug bis ≈ 23 s, Preload) und `M109` laufen zu Ende.
- Ohne Druck: normaler G-Code-Fehler, keine Bewegung danach (der Kopf bleibt z. B. im Schacht). Ohne RS485-Port: `… fehlgeschlagen: [k2_485] …`.

**Sicherheitshinweise:**

- **Nach dem Spülen nie ohne Schnitt zurückziehen.** Nach `KTWO_BOX_FLUSH` und ab Druckbeginn lehnen einzelne `KTWO_BOX_RETRUDE`/`KTWO_BOX_UNLOAD` (außerhalb einer Kette) ab, bis `KTWO_BOX_CUT` wirklich geschnitten hat – das lange geschmolzene Ende bildet beim Rückzug eine Verdickung und blockiert die Box. Reihenfolge von Hand: `KTWO_BOX_CUT` → `KTWO_BOX_RETRUDE SLOT=<Slot>`. `FORCE=1` übergeht den Schutz (nur mit Warnung). Details `../README-k2_box.md` §8.3.
- **Z-Hop 0,4 mm** wie Stock: bei hohen Objekten nahe Y200/X10 bzw. auf dem Weg zum Schacht Kollisionsgefahr wie im Stock.
- **Preload** `KTWO_BOX_PRELOAD`: op 2/3 nur bei leerem Kopf-Filamentsensor (Filament oder kein Sensor → gesperrt, auch mit FORCE), im Druck/Pause nur mit `FORCE=1`, Maske 0 abgelehnt.
- Am Gerät ungeprüft (ANNAHME): Lage des Kopfsensors relativ zu Schneider/Zahnrädern, Schneidhebel-Bereich (nur aus Stock-Werten abgeleitet), lange Box-Antworten neben Heizung/MCU, mehrere Boxen.

**Weitere Befehle:** `KTWO_BOX_RFID [SLOT=T1A|ALL] [FORCE=1]` (Etiketten neu lesen, gesperrt in Kette, im Druck nur mit FORCE=1), `KTWO_BOX_SLOT_SET SLOT=T1A [COLOR=RRGGBB] [TYPE=<id>|MATERIAL=<Name>] [CLEAR=1]` (Slot-Farbe/-Material von Hand, persistent in `slot_material_file`), `KTWO_BOX_REMAIN [COLOR=] [TYPE=] [TNN=] [CLEAR=1]` (Material in der Düse, persistent in `remain_material_file`), `M600` aus der Druckdatei → `PAUSE` (außerhalb eines Drucks nur Meldung). Externe Spule: `SET_FILAMENT_SOURCE SOURCE=EXTERNAL|CFS`, `LOAD_FILAMENT`, `UNLOAD_FILAMENT`, `SET_EXTERNAL_MATERIAL` (`../README-k2_box.md` §18).

### 7.2 Düsenreinigung, PAUSE und RESUME

**Düsenreinigung `_PI_NOZZLE_CLEAN`** (Ersatz Stock `BOX_NOZZLE_CLEAN`):

- Bahn = Stock-T0-Beispiel, alle Werte aus `[k2_box]`: Schacht `Y345 → X139 → Y378 → X133` (`KTWO_BOX_GO_EXTRUDE_POS`), Abstreifen (Blech, zwei Kreise am Silikon, 3× Schütteln; `KTWO_BOX_WIPE`), Schacht verlassen `X160 → Y345` (`KTWO_BOX_SAFE_POS`). Parameter `ONLY=GO`, `LEAVE=0`, `VIA=AUTO|BOX|GCODE`, `WHY=`.
- Aufrufe: `homing.cfg` (vor dem ersten Z-Antasten, nur wenn Z noch nicht referenziert), `START_PRINT` und `G29` (vor dem maßgeblichen `G28 Z`, bei 140 °C), `START_PRINT`-Ende, `RESUME`.
- Freigaben: `_PI_FREIGABE allow_move` UND `[k2_box] allow_move`, X/Y referenziert; sonst Meldung `… übersprungen: <Grund> – keine Bewegung.` und weiter (G28 tastet dann OHNE Reinigung an). Nie Fehler, nie Shutdown. Global abschaltbar: `_PI_PARAM nozzle_clean_enable: False`.
- Ist Z referenziert und tiefer als 5 mm, zuerst `G1 Z5` (Bett runter). Kein Heizen, kein Extrudieren.
- Im Druck (oder bei offener Wechselkette) fährt die Reinigung dieselbe Bahn als reinen G-Code (`VIA=GCODE`, `_PI_CLEAN_PATH`), weil k2_box im Druck für jeden Schritt `allow_write` UND `allow_move` verlangt und sonst pausiert.
- Bewusst nicht übernommen: Stock-`PRES_CHECK` (k2_prtouch prüft vor JEDER Antastung Signalweg und Ruhe selbst) und Stock-`NOZZLE_CLEAR` (drückt die Düse aktiv unter die Bettoberfläche – erst nachrüstbar, wenn `PROBE`/`PROBE_ACCURACY` am Gerät stabil sind).

**PAUSE** (Stock-Logik: Rückzug, Z-Hub `z_safe`, Düse 140 °C, Parken) merkt Pausenposition und -temperatur. **RESUME** (nur wenn pausiert):

1. Offener Wechsel-Fehler → `RESUME gesperrt …` bis `KTWO_BOX_ERROR_CLEAR`.
2. Pause aus einem abgebrochenen CFS-Wechsel → zuerst `KTWO_BOX_RESUME_CHECK` (unten), vor jeder Bewegung.
3. `_PI_RESUME_PREPARE` (Voraussetzungen: X/Y/Z referenziert, `_PI_FREIGABE allow_move`, `[k2_box] allow_move`, Pausenposition bekannt; sonst nur `M109 S<Pausentemperatur>` + Meldung, Rückweg über Upstream-`RESUME_BASE`): in den Abfallschacht → `M109` auf die Pausentemperatur → Vorextrusion im Schacht (nur wenn die Düse im Schacht steht, `can_extrude` und ≥ max(180 °C, `min_extrude_temp`); Länge Pausen-Rückzug + 80 mm, Geschwindigkeit mit Material-DB aus `printer.k2_box.prime_speed`, sonst 460 mm/min) → 3 s Lüfter → Abstreifen → Schacht verlassen → X/Y in Pausenhöhe zur Pausenposition, dann Z senkrecht (nie unter Pausen-Z).
4. Lüfter wie vor der Pause, `RESUME_BASE`, `KTWO_BOX_RESUME_FIX`.

**Nach einem im Druck abgebrochenen CFS-Wechsel:**

- PAUSE bekommt von k2_box das Rückfahrziel = **Druckposition vor dem Wechsel** (`PAUSE CFS_RETURN_X/Y/Z`) statt der Fehlerposition (Meldung „Fehlerposition … Rückfahrziel …“) und die Drucktemperatur vor dem Aufheizen (`PAUSE CFS_TEMP`) – RESUME heizt also auf die Drucktemperatur, nicht auf die Wechsel-Temperatur.
- PAUSE verlässt den Schneidbereich (X < `pre_cut_pos_x` 10) zuerst NUR in X, den Schacht nach dem Z-Hub über X160 → Y345, dann Park.
- RESUME fährt zur Druckposition vor dem Wechsel (X/Y in Pausenhöhe, Z senkrecht) und setzt PAUSE_STATE dort neu (`_PI_RESUME_REBASE`) – der Druck läuft an der Stelle weiter, an der `T<n>` begann. Ohne Bewegungsfreigabe/Referenz, bei Ziel am Schneidhebel (X ≤ `check_cut_pos_x_max`) oder offenem Wechsel-Fehler wird RESUME **verweigert** (Druck bleibt pausiert, keine Bewegung).
- **RESUME-Sperre bei leerem Kopf** (`KTWO_BOX_RESUME_CHECK`):
  - Filament erkannt → RESUME frei (ob es das Ziel-Filament ist, prüft der Pi nicht – im Zweifel vorher `T<n>`).
  - LEER → `RESUME gesperrt …`, Druck bleibt pausiert. In der Pause `T<n>` (die Meldung nennt `T<n>` und Ziel-TNN), dann `RESUME`. Bei offener M8200-Kette alternativ `KTWO_BOX_EXTRUDE TNN=…`.
  - unbekannt (kein Kopfsensor) → gesperrt; nach Sichtprüfung `KTWO_BOX_ERROR_CLEAR HEAD_LOADED=1`, dann `RESUME`.
- Reihenfolge von Hand: `KTWO_BOX_ERROR_CLEAR` → `T<n>` → `RESUME`.
- Optional `[k2_box] resume_redo_change: True` (Default AUS, nur mit `allow_write` + `allow_move`): RESUME holt einen abgebrochenen `T<n>`-Wechsel selbst nach (Schacht, Heizen, Rückzug nur bei belegtem Hub, Einzug, Spülen, dann normales RESUME). Nie für M8200-Kette, Runout oder Einzelschritte. Jeder Fehler sperrt RESUME bis `KTWO_BOX_ERROR_CLEAR`.

**Am Gerät prüfen** (Hand am Not-Aus, alles ungeprüft):

1. Schacht-/Silikon-Positionen (Stock-Werte, wegen `FORCE_MOVE stepper_y -3` erst langsam): `KTWO_BOX_GO_EXTRUDE_POS`, `KTWO_BOX_WIPE`, `KTWO_BOX_SAFE_POS` einzeln (§7.1 Schritt 2), dann `_PI_NOZZLE_CLEAN`, dann `G28`.
2. Abbruch im Schacht provozieren (z. B. Box stromlos während `T1`), Parkweg und RESUME-Bahn beobachten; den Hebelfall nur mit `KTWO_BOX_CUT` außerhalb eines Drucks + manuellem `G1 X10` prüfen.
3. Danach Kopf leeren (Filament von Hand ziehen) → RESUME muss verweigern; `T1` in der Pause → RESUME läuft.
4. Druck mit < 220 °C, Abbruch provozieren → Meldung `CFS_TEMP`, nach RESUME Ziel = Drucktemperatur.
5. Nachholen (`resume_redo_change`) erst nach erfolgreichen Einzelschritten (§7.1) testen.

ANNAHME: Der Kopfsensor meldet „leer“, sobald das Filament nach dem Abbruch aus dem Kopf ist; nach einem Schnitt OHNE Rückzug steckt das obere Stück noch im Sensor → „voll“ → RESUME frei.

### 7.3 Spülen und Material-Datenbank

- `KTWO_BOX_FLUSH [TNN=] [LENGTH=] [VELOCITY=] [TEMP=|BASE_TEMP=]` spült wie Stock: Menge aus alter/neuer RFID-Farbe (unbekannt → 90 + 70 mm) bzw. im laufenden Druck aus der Slicer-Spülmatrix der Druckdatei (`flush_mode: matrix`, Default), Abschnitte je 20 % v + 80 % 1,2·v (letzter ×1), Lüfter blasen, Blech zwischen den Abschnitten, Silikon-Kreise und Schacht verlassen nur nach dem letzten. Optionen: `../README-k2_box.md` §12, `k2-module.cfg`.
- **Material-Datenbank** `[k2_box] material_database` (Creality-`material_database.json`; `install.sh --material-db` legt sie unter `~/printer_data/config/k2/` ab, nicht im Repo; in `k2-module.cfg` auskommentiert ausgeliefert): Wechsel-/Spültemperatur, Spül-/Einzugs- und RESUME-Vorextrusions-Geschwindigkeit je Material (Schlüssel = RFID-`material_type` des Slots). Fehlt/kaputt → feste Werte (220 °C, 360 mm/min) + Meldung beim Connect. DB-Temperaturen werden auf `[extruder] max_temp − 10` gekappt.
- Verstopfungsprüfung `flush_clog_check`: nur Warnung, Default AUS.
- **Am Gerät** (Hand am Not-Aus): `KTWO_BOX_FLUSH TNN=…` einzeln bei `allow_move` – Blech ohne Kreise zwischen den Abschnitten, Kreise am Ende auf dem Silikon (Versatz 0–3 mm), Lüfter 5 s; dann mit Datenbank: PETG-Slot → 250 °C, PLA→PLA → Spitze 240 °C (Hyper PLA).

### 7.4 Auto-Nachladen bei leerer Spule (Runout)

`printer.cfg`: `pause_on_runout: True` + `runout_gcode: KTWO_BOX_RUNOUT`. Upstream pausiert den Druck ZUERST und führt dann `PAUSE` + `runout_gcode` aus – der Druck steht also sicher, bevor `KTWO_BOX_RUNOUT` läuft. Technik: `../README-k2_box.md` §9.

`KTWO_BOX_RUNOUT [SLOT=A..D] [TEMP=] [DRY_RUN=1]`:

1. Prüft lesend (jede Störung → Pause bleibt + Klartext): `runout_refill`/`KTWO_BOX_AUTO_REFILL` an, `allow_write` UND `allow_move`, kein Wechsel aktiv/offen, Kopfsensor leer (sonst „Fehlauslösung“), Bus da, Box im Druckzustand, Hub leer, Einlauf des Slots leer (Spule wirklich leer, kein Bruch).
2. Ersatz-Slot: mit Gleich-Material-Gruppe (`KTWO_BOX_SAME_MATERIAL`) das nächste Gruppenmitglied mit Filament am Einlauf; sonst per RFID gleiches Material UND gleiche Farbe (`runout_require_color: False` = Farbe egal), Reihenfolge ab dem Slot nach dem leeren. Slots ohne lesbares Etikett nie. Kein Kandidat → Pause.
3. Nachladen: Schacht, Heizen auf max(Drucktemperatur vor der Pause, 220), E30 (Rest durch die Zahnräder), Einzug, Spülen, zurück zum Parkpunkt, `RESUME` (`runout_resume: False` = pausiert lassen). Kein Schnitt, kein Rückzug.
4. Fehler in der Kette → Pause bleibt, `RESUME` gesperrt bis `KTWO_BOX_ERROR_CLEAR`. Abbrüche der Vorprüfung sperren RESUME NICHT (Filament von Hand nachlegen, dann `RESUME`). Nie Shutdown.

Weitere Befehle: `KTWO_BOX_AUTO_REFILL [ENABLE=0|1]` (Laufzeit, Start = `runout_refill`), `KTWO_BOX_SAME_MATERIAL LIST=T1A,T1C/T1B,T1D` bzw. `CLEAR=1` (nur Box 1, Gruppen ≥ 2 Slots). Status `printer.k2_box`: `auto_refill`, `same_material`, `runout_last`, `ending_material`.

| Option (`k2-module.cfg` `[k2_box]`) | Default | Bedeutung |
|---|---|---|
| `runout_refill` | False | Auto-Nachladen; wirkt nur mit `allow_write` + `allow_move` |
| `runout_require_color` | True | RFID-Regel: Farbe muss passen |
| `runout_resume` | True | nach Erfolg automatisch RESUME |
| `runout_tail_length` / `_velocity` | 30 / 600 | Stock `G0 E30 F600`; 0 = aus |
| `ending_material_purge` | True | Restmaterial beim Wechsel/Druckende ausspülen (§7.1) |
| `max_tube_length` / `ending_material_timeout` | 3000 / 3600 | Obergrenze (mm) / Zeitlimit (s) des Ausspülens |

**Inbetriebnahme:** 1. Wechsel (§7.1) muss sicher laufen. 2. `KTWO_BOX_RUNOUT SLOT=A DRY_RUN=1` mit leerem Slot A: zeigt Ersatz und Begründung, bewegt nichts. 3. `runout_refill: True`, Testdruck mit fast leerer Spule, Hand am Not-Aus. Am Gerät ungeprüft: RESUME aus dem `runout_gcode` heraus. Mehrere Boxen und externe Spule → immer Pause.

### 7.5 Schneidposition kalibrieren (`cutcal.cfg`)

`CALIBRATE_CUT_POS [SAVE=1]` (Ersatz Stock): `G28 X Y` (falls nötig) → `SAVE_GCODE_STATE` → `KTWO_LIMITS_WIDE` → `KTWO_MOTOR_CUT_CAL` (Kp 300, Stall-Mode 3, Anstöße in 0,1-mm-Schritten ab X −5,0 bis höchstens −9,5 am X-Endstop PB11, Ergebnis = 2. Treffer + 0,4, Ausgang X200/Y200, Rückbau Mode 2/Kp 100/Beschleunigung) → `M400` → `KTWO_LIMITS_RESTORE` → `RESTORE_GCODE_STATE`. Das Ergebnis wird als `[k2_box] cut_pos_x` VORGEMERKT; `SAVE=1` ruft `SAVE_CONFIG` (Upstream startet dabei neu). `KTWO_MOTOR_CUT_CAL DRY_RUN=1` zeigt den Plan (nichts gesendet). Technik: `../README.md` §10.

- **Sperren** (Fehler OHNE Frame und OHNE Bewegung): `[k2_motor] allow_write: True` UND `_PI_FREIGABE allow_move`, kein aktives X/Y-Homing, X/Y referenziert, kein zweiter Lauf; im Makro zusätzlich Filamentsensor (kein Filament im Kopf).
- **Harte Grenzen** (Config kann sie nicht aufweichen): X nie unter `check_cut_pos_x_min` und nie unter −10 bzw. `[stepper_x] position_min`; max. 100 Anstöße. Ohne `KTWO_LIMITS_WIDE` lehnt das Modul die Anstöße ab („außerhalb [stepper_x] … nichts gesendet“).
- **Rückbau immer** (Kopf auf Park, Mode 2, Kp 100, Beschleunigung, Fahrgrenzen); Fehler → G-Code-Fehler mit Rückbau-Bericht, nie Shutdown.
- `cut_pos_x` NICHT in `k2-module.cfg` eintragen – Upstream-`SAVE_CONFIG` bricht sonst ab („conflicts with included value“).

**Inbetriebnahme:**

1. `[include cutcal.cfg]` in printer.cfg (so ausgeliefert).
2. `KTWO_MOTOR_CUT_CAL DRY_RUN=1` → Plan prüfen (46 Anstöße −5,0…−9,5).
3. Voraussetzung: sensorless X/Y-Homing läuft (§7.6), Filament entladen, Hand am Not-Aus.
4. Polarität PB11 in Mode 3 (ANNAHME: high-aktiv wie Mode 1): `KTWO_MOTOR_STALL_MODE` liest 2; die Kalibrierung bricht selbst ab, wenn PB11 nach Mode 3 schon aktiv ist.
5. `CALIBRATE_CUT_POS` → Ergebnis mit dem Stock-Wert −7,80 vergleichen (±0,1–0,2 mm plausibel), dann `SAVE_CONFIG` (oder gleich `SAVE=1`). `[k2_limits]` zieht X min danach auf den neuen Wert nach.

### 7.6 Sensorless-Homing X/Y und Stall-Überwachung

`homing.cfg` (`[homing_override]`):

1. Sperren beim Rendern (vor jeder Bewegung, kein Frame): `_PI_FREIGABE allow_move` (alle Achsen); für X/Y zusätzlich `[k2_motor] allow_write: True` und die Not-Sperre `xy_stall_ok` (Default True).
2. `KTWO_MOTOR_HOMING PHASE=BEGIN` (Stall-Mode 1 an X/Y = Endstop-Signal auf PB11/PB12, Kp 300; Fehler → Rückbau, Abbruch ohne Bewegung).
3. `G28 Y` (vorher ggf. freifahren), `G1 Y10`; `G28 X`, `G1 X-10` (Beschleunigung 100 mm/s² wie Stock).
4. `FORCE_MOVE STEPPER=stepper_y DISTANCE=-3 VELOCITY=10` unter den Stock-Bedingungen – CoreXY: Düse physisch X −1,5 / Y +1,5 mm ohne Koordinatenänderung; die Stock-`position_endstop`-Werte (X 352.5 / Y −6.2) gelten MIT diesem Versatz.
5. `KTWO_MOTOR_HOMING PHASE=END` (Mode 2, Schutzabfrage, Kp 100), ggf. Z wie bisher.

G-Codes in `[k2_motor]` (`../README.md` §9): `KTWO_MOTOR_HOMING PHASE=BEGIN|END`, `KTWO_MOTOR_STALL_MODE`, `KTWO_MOTOR_SYS_PARAM`, `KTWO_MOTOR_PROTECTION`, `KTWO_MOTOR_CLEAR`. Schreiben NUR mit `allow_write: True` in `k2-motor.cfg` (bewusst kein `ALLOW_WRITE=1`-Override). Fehler → G-Code-Fehler, nie Shutdown.

**Inbetriebnahme:**

1. `KTWO_MOTOR_STALL_MODE` (lesen, geht ohne allow_write) → x/y = 2.
2. `allow_write: True` (nach Init-Prüfung, §4), Neustart.
3. `KTWO_MOTOR_STALL_MODE DATA=1` → `QUERY_ENDSTOPS` mehrmals, Wagen per Hand gegen den Anschlag drücken bzw. leicht blockieren → PB11/PB12 müssen „TRIGGERED“ zeigen; `KTWO_MOTOR_STALL_MODE DATA=2` zurück.
4. `SET_GCODE_VARIABLE MACRO=_PI_FREIGABE VARIABLE=allow_move VALUE=True`, Hand am Not-Aus, `G28 Y`, dann `G28 X`, dann `G28`.
5. Nach `G28` die Position eines Referenzpunkts mit Stock vergleichen (Vorzeichen/Achszuordnung des FORCE_MOVE-Versatzes ist ANNAHME).

**Stall-/Notfall-Überwachung im Betrieb** (`k2-motor.cfg` `stall_monitor`): Im Druck stehen alle Motoren auf Mode 2; die Leitungen melden eine Änderung der gespeicherten Fehler-/Warncodes (inkl. Schleppfehler). `off` = keine Überwachung, `report` (Default) = nur melden, `act` = X/Y/Z/Z1-Fehler im Druck → Not-Stopp (Shutdown), ohne Druck → `M84`; nur Warnung → `PAUSE` im Druck; E → `PAUSE` + Extruder aus. Codes werden NIE automatisch gelöscht – `KTWO_MOTOR_CLEAR` (braucht `allow_write`). **Gerätetest, bevor `act` gesetzt wird:**

1. Nach `KTWO_MOTOR_INIT`: `KTWO_MOTOR_STATUS` → Flanken 0, keine Aktion.
2. `G28` mehrmals: keine Stall-Meldung von X/Y; `CALIBRATE_CUT_POS`: nur `cut_edges` steigt.
3. Ohne Druck, Motoren aktiv: Achse leicht blockieren bzw. Schleppfehler provozieren → Meldung `key789`, danach `KTWO_MOTOR_CLEAR`.
4. Erst dann `stall_monitor: act`, Neustart; Probedruck mit Hand am Not-Aus.

### 7.7 Dual-Z-Lichtschranken (`zalign.cfg`, A10)

Stock richtet beim ersten `G28 Z` nach dem Einschalten das Bett über zwei Gabellichtschranken unten an den Z-Spindeln (PA15 → stepper_z, PA8 → stepper_z1) mechanisch aus. Auf dem Pi:

- **Variante L (aktiv):** rein lesend über `[gcode_button]`; Meldung bei jedem Wechsel, `ZALIGN_STATUS` / `QUERY_BUTTON` jederzeit.
- **Variante B (`[k2_zalign]`, auskommentiert + gesperrt):** Ausrichtung mit Upstream-Homing-Mechanik (je Schranke ein Endstop für genau einen Z-Stepper, in Etappen mit hartem `safe_dist`): `KTWO_ZALIGN`, `KTWO_ZALIGN_STATUS`, `KTWO_ZALIGN DRY_RUN=1`, `ZALIGN_STOCK`. Z ist danach NICHT referenziert (`G28 Z` mit der Düse folgt). **Nie L und B gleichzeitig** („pin PA15 used multiple times in config“).
- **`START_PRINT`** ruft `_PI_ZTILT_WENN_NOETIG` → `Z_TILT_ADJUST`, solange `z_tilt` nach Einschalten/`M84`/`FIRMWARE_RESTART`/Not-Aus nicht angewendet ist (A10a).
- **Risiko ohne Lichtschranken:** Nach jedem Stromlos-Zustand ist die Höhendifferenz Δ der beiden Spindeln unbekannt (ANNAHME: das Bett kann einseitig absinken). Im `Z_TILT_ADJUST` gilt: Δ < 6,9 mm sicher; ab 6,9 mm Abbruch „kein Kontakt“ (tiefere Seite); ab **11,5 mm Kollisionsgefahr** (höhere Seite bei der XY-Fahrt auf Z 5). Ist die Zuordnung stepper_z ↔ Bettseite vertauscht (ANNAHME: stepper_z = links), korrigiert z_tilt in die falsche Richtung und bricht erst nach zwei Anstiegen ab.

**Testprotokoll am Gerät** (Bett leer, Hand am Not-Aus, nach jedem Schritt `klippy.log` prüfen, jeder Schritt mit Bewegung nur mit Einzelfreigabe; Abbruch → STOP, nichts nachjustieren; Werte in die Tabelle unten):

- **T0 Voraussetzung:** §4 Schritt 1 bestanden, `zalign.cfg` wie ausgeliefert (Variante L).
- **T1 Lesen ohne Bewegung (L):** `ZALIGN_STATUS` → beide `RELEASED` (sofern das Bett nicht ganz unten steht). Schranke links mit Papier/Karte unterbrechen (falls zugänglich) → `zalign_z … AUSGELÖST`, rechts → `zalign_z1`; Zuordnung links/rechts notieren. Bett sichtbar ganz unten → mindestens eine `PRESSED`. **Abbruch:** dauernd `PRESSED` bei freier Gabel (Polarität/Pull-up falsch, Pegel an PA15/PA8 messen) · keine Reaktion (Kabel) · Zuordnung vertauscht (dann `pin_z`/`pin_z1` tauschen, NICHT weiter).
- **T2 Vorbereitung:** §4 Schritte 2–7 (Motor-Init, X/Y-Homing, Probe) bestanden.
- **T3 Referenz OHNE Lichtschranken (z_tilt):** Kaltstart (Drucker ≥ 10 min aus), `ZALIGN_STATUS`, Freigabe, `G28`. **Kollisions-Vorprüfung:** `G1 Z15 F600`, `G1 X5 Y175 F3000` → Abstand Düse–Bett messen (d_links), `G1 X345 Y175` → d_rechts; **|d_links − d_rechts| > 4 mm → STOP** (erst Variante B oder Rücksprache). Dann `G1 Z5`, `Z_TILT_ADJUST` (beide `probe at`-Werte, Korrekturen, Runden notieren), erneut (erwartet range < 0,1 in Runde 0), 30 min stehen lassen → `G28 Z` + `Z_TILT_ADJUST` (Drift), `M84` 5 min + Wiederholung; gesamt ≥ 3 Kaltstart-Zyklen. **Abbruch:** „kein Kontakt innerhalb probe_max_travel“ · „Retries aborting … increasing“ (Zuordnung/Richtung falsch) · „Too many retries“ · eine Korrekturrunde > 3 mm · Geräusch/Blockade.
- **T4 Variante B lesend:** `cp pi-host/k2_zalign.py ~/klipper/klippy/extras/` (falls nicht schon per `install.sh`), in `zalign.cfg` Block L (zwei `[gcode_button …]`) auskommentieren, Block B einkommentieren (nur die `#` vor den Optionszeilen entfernen, `allow_move: False` lassen), `FIRMWARE_RESTART`. `QUERY_ENDSTOPS` → `zalign_z:open zalign_z1:open`; `KTWO_ZALIGN_STATUS` → GESPERRT + Gründe; `KTWO_ZALIGN DRY_RUN=1` → Plan; `KTWO_ZALIGN` → „gesperrt … Keine Bewegung.“ **Abbruch:** Config-Fehler (`pin … used multiple times` = Block L noch aktiv) · `TRIGGERED` bei freier Gabel.
- **T5 Erste Ausrichtung (Einzelfreigabe):** Motor-Init fertig, `G28`, `G1 Z30`, Z_TILT aus T3 angewendet. `[k2_zalign] allow_move: True`, `FIRMWARE_RESTART`, Motor-Init, `SET_GCODE_VARIABLE MACRO=_PI_FREIGABE VARIABLE=allow_move VALUE=True`. `KTWO_ZALIGN` beobachten: beide Seiten fahren mit 10 mm/s **nach unten**, jede stoppt an ihrer Schranke, 5 mm hoch, langsam runter; Meldungen notieren. **Sofort Not-Aus** bei Bewegung **nach oben** in Phase A · wenn eine Seite nach dem Stopp der anderen sichtbar > 5 mm weiterfährt · Verkanten/Geräusch · Fahne läuft auf Anschlag.
- **T6 Z-max messen:** nach T5 `GET_POSITION` (`stepper_z:<A>`), `G28 Z` (tastet von ganz unten, ~70 s), `GET_POSITION` (`stepper_z:<B>`) → zmax = (A − B) × 0,0025 mm (Vorzeichen prüfen; Stock ≈ 352,2 mm). 3× wiederholen. Erst danach über `zmax_position` entscheiden (konservativ, z. B. Messwert − 5 mm).
- **T7 Vergleich MIT Lichtschranken-Vorausrichtung:** wie T3 (≥ 3 Kaltstarts, `M84`-Zyklen), aber `G28 X Y` → `ZALIGN_STOCK` (= `KTWO_ZALIGN` → `G28 Z` → `Z_TILT_ADJUST` → `G28 Z`).
- **T8 Entscheidung:** z_tilt allein reicht, wenn in allen ≥ 3 Kaltstarts Δ aus der ersten Z_TILT-Runde (|z345 − z5| × 390/340) < 3 mm, Konvergenz in ≤ 3 Runden, keine Abbrüche, Drift nach 30 min < 0,05 mm. Sonst `KTWO_ZALIGN` vor `Z_TILT_ADJUST` beim ersten Homing nach Stromlos – Einbau in macros.cfg/homing.cfg erst nach Abstimmung.

| Nr | Datum/Zeit | Variante | Kaltstart/M84? | Bettlage vorher (Sicht/ZALIGN_STATUS) | d_links/d_rechts (T3) | Phase-A z1−z [mm] | Delta C [mm] | ZALIGN Dauer [s] | z_tilt R0 z5 / z345 | Korrektur z / z1 | Runden bis ≤0,1 | Drift 30 min | zmax (T6) | Abbruch/Bemerkung |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|

### 7.8 Beschleunigungssensor (`[k2_lis2dw]`)

`[k2_lis2dw]` (Modul `../k2_lis2dw.py`, Doku `../README-k2_lis2dw.md`) spricht das alte Creality-lis2dw-Protokoll der Düsen-MCU und meldet sich bei Upstream als normaler Beschleunigungssensor (`accel_chip`). Der Block steht in `printer.cfg` (Pins/Takt/Achsen 1:1 Stock) samt `[resonance_tester] accel_chip: k2_lis2dw`; das Modul muss nach `~/klipper/klippy/extras/` (§3). **Kein** zusätzliches `[lis2dw]` eintragen – das Upstream-Modul bricht mit „Command format mismatch: query_lis2dw“ ab.

Erstlauf im Stand (nach §4 Schritt 1, ohne Bewegung): `ACCELEROMETER_DEBUG_READ REG=0x0f` (= 0x44), `KTWO_ACCEL_STATUS`, `ACCELEROMETER_QUERY`. `SHAPER_CALIBRATE` erst nach freigegebenem Homing (§4 Schritte 6/7). Upstream misst per Sweep, Creality-Stock per Einzelfrequenzen – gleiche Methode wie Stock: `sweeping_period: 0` in `[resonance_tester]`.

Gemeinsame Batch-Prüfung aller Module mit dieser Config (Upstream 9cb9975, Live-Dictionaries): **EXIT 0**,
MCU-Ausgabe u. a. `config_endstop pin=PC7`, `config_prtouch_pres … clk_pin=PB13 sdo_pin=PB14`,
`config_lis2dw oid=… spi_oid=…`, Strapping-Pins `max_duration=0`. Am Gerät ist alles UNGETESTET.

## Makronamen (umgestellt 25.10.2026)

Eigene Makros heißen jetzt wie in der Klipper-/Mainsail-Welt üblich (Englisch,
`SET_…`-Stil, Creality-Originalnamen, wo es sie gibt). Jedes sichtbare Makro hat
eine deutsche `description:` – Mainsail zeigt sie als Tooltip.
`LOAD_FILAMENT`/`UNLOAD_FILAMENT` erkennt Mainsail selbst und zeigt im
Extruder-Feld die Knöpfe „Laden/Entladen“.

| Alter Name | Neuer Name |
|---|---|
| `EXTERN_LADEN` | `LOAD_FILAMENT` |
| `EXTERN_ENTLADEN` | `UNLOAD_FILAMENT` |
| `QUELLE_EXTERN` / `QUELLE_CFS` | `SET_FILAMENT_SOURCE SOURCE=EXTERNAL` / `SOURCE=CFS` |
| `EXTERN_MATERIAL` | `SET_EXTERNAL_MATERIAL` |
| `BETT_AUSRICHTEN` (kalt) | `Z_AXIS_CALIBRATION` (Standard warm 50 °C, kalt mit `BED_TEMP=0`) |
| `ZALIGN_STOCKART` | `ZALIGN_STOCK` (`zalign.cfg`) |
| `WIEDEREINSTIEG` | `PRINT_FROM_LAYER` |
| `TEMP_RUHETEST` | `HEATER_STABILITY_TEST` |
| `BEWEGUNG_FREIGEBEN` | `ALLOW_MOTION` (nur bei gesperrter Bewegung nötig) |

Die alten Namen gibt es seit 25.10.2026 nicht mehr (kurz als Weiterleitung
vorhanden, dann entfernt). Die `KTWO_…`-Befehle der Python-Module bleiben
unverändert (Klipper-Muster `MODUL_AKTION`).
