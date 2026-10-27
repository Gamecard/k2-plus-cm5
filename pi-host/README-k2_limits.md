# `[k2_limits]` – Fahrgrenzen wie Stock (L3, 18.10.2026)

> Am Gerät UNGETESTET. Offline (`test_k2_limits.py`, `test_k2_box.py`) und
> im Batch gegen Upstream-Klipper 9cb9975 geprüft (§6).
> Lücke L3 aus dem Vergleich Creality-Klippy ↔ Upstream. Belege: `CR:` =
> Creality-Klippy der Live-FW 1.1.7.0 (auf dem Original-Drucker unter
> `/usr/share/klipper/klippy/`), `ST` = Stock-Config des Original-Druckers.

## 1. Warum

Bis 17.10.2026 standen in `printer.cfg` die Roh-Werte der Stock-Config
(X −10…352,5, Y −6,2…400, Z −10…360). Upstream setzt beim Homing genau
diese Werte. Der Pi stand damit **dauerhaft** im aufgeweiteten Zustand:

- `G1 X−9` fuhr den Schneidhebel über die kalibrierte Schneidposition hinaus,
- Y 352–400 führte über den Spülschacht statt über das Bett,
- `G1 Z355…360` fuhr das Bett in den unteren Anschlag (echter Weg ≈ 352,2),
- außerhalb des Drucks drückte `G1 Z−5` die Düse 5 mm ins Bett.

## 2. Stock-Werte (Belege)

| Grenze | Wert | Beleg |
|---|---|---|
| X min im Betrieb | `[box] cut_pos_x` (Pi: `[k2_box] cut_pos_x`, Default −7,80 = SAVE_CONFIG 16.10.2026) | CR:stepper.py:332-333, 368-377 (`get_range()` liest `box_action.boxcfg.cut_pos_x`) |
| Y max im Betrieb | 352.0 | CR:stepper.py:326-328 (`PRINTER_PARAM variable_max_y_position`), ST `gcode_macro.cfg`:8 |
| `SET_LIMITS` (weit) | X ab −10, Y bis 400 = Roh-Config `[stepper_x/y]` | CR:extras/gcode_move.py:96-101 → CR:kinematics/corexy.py:71-77 |
| `RESTORE_LIMITS` | nur gehomte X/Y zurück auf `rail.get_range()` | CR:extras/gcode_move.py:103-104 → CR:kinematics/corexy.py:78-87 |
| Z max nach jedem `G28 Z` | 360 − 10 + 0 + 0,2 = **350,2** | Aufruf CR:extras/homing.py:425/442/452, Formel CR:extras/gcode_move.py:72-90 (`zmax_safe_pox_diff` 10, ST `printer.cfg`:399). `fade_target` ist dabei 0: vor dem Z-Homing läuft `BED_MESH_CLEAR` (CR:extras/homing.py:407), das setzt `fade_target = 0` (CR:extras/bed_mesh.py:199) |
| `G1 Z` außerhalb des Drucks | ≥ −2 (Stock **klemmt** auf −2) | CR:extras/gcode_move.py:301-308 |

## 3. Umsetzung (Upstream-verträglich)

**Enge Grenzen in der Config** (`printer.cfg`): `[stepper_x] position_min: -7.8`,
`[stepper_y] position_max: 352`, `[stepper_z] position_max: 350.2`. Upstream
setzt beim Homing `kin.limits[i] = rail.get_range()` (UP:kinematics/corexy.py
`set_position`). Die Z-Grenze gilt damit wie bei Stock nach dem Z-Homing.

**Modul `k2_limits.py`** (`[k2_limits]` in printer.cfg):

- Beim Laden prüft es die engen Werte: Y max ≤ 352 und Z max ≤ 350,2. Weiter
  als Stock geht nicht. Die Weit-Werte sind ebenfalls hart begrenzt:
  `x_min_wide` ≥ −10, `y_max_wide` ≤ 400, `z_min_outside_print` ≥ −2.
- Beim Connect zieht es X min auf `[k2_box] cut_pos_x` nach (Stock-Semantik:
  nach `CALIBRATE_CUT_POS` + `SAVE_CONFIG` gilt der neue Wert). Status
  `axis_minimum.x` wird mitgezogen.
- **Aufweiten** (`KTWO_LIMITS_WIDE [REASON=]`, Python `widen(owner)`) setzt
  `rail.position_min/max` (X auf −10, Y auf 400). Für **gehomte** Achsen setzt
  es zusätzlich `kin.limits` = `rail.get_range()`. Erlaubt nur, wenn X **und**
  Y gehomt sind. Sonst gibt es einen Fehler, und es wird nichts geändert. Der
  Stock-Nebeneffekt „ungehomte Achse gilt danach als gehomt“ ist bewusst
  **nicht** nachgebaut. Rails und `kin.limits` bleiben immer gleich. Auch
  `KTWO_MOTOR_CUT_CAL` (prüft gegen `rail.get_range()`) und ein G28 im weiten
  Zustand sehen dasselbe Fenster.
- **Halter:** Python-Aufrufer (`k2_box`) melden sich mit eigenem Namen an und
  ab. Eng wird es erst, wenn kein Halter mehr da ist. `KTWO_LIMITS_RESTORE`
  ist wie Stock-`RESTORE_LIMITS` **unbedingt**: alle Halter weg, Rails und
  gehomte Achsen eng.
- **Rücksetzen im Fehlerfall, automatisch:**
  - bei **jedem** G-Code-Fehler (Upstream-Event `gcode:command_error`; feuert
    in `_process_commands` auch für verschachtelte Makro-Fehler),
  - bei `stepper_enable:motor_off` und beim Shutdown,
  - in `k2_box` zusätzlich im `finally` jedes Schritts.
- `SET_KINEMATIC_POSITION` wird nicht verwendet.

**Z < −2 außerhalb des Drucks: Ablehnen über einen Bewegungs-Transform.**

- `[k2_limits]` hängt sich beim Connect mit
  `gcode_move.set_move_transform(self, force=True)` vor den bisherigen
  Transform, z. B. bed_mesh. Das ist dasselbe Upstream-Muster wie
  `exclude_object`, das sich später darüber einreiht.
- Ein G-Code-Ziel Z < −2 wird **abgelehnt**, wenn alle drei Bedingungen gelten:
  - Z ist gehomt,
  - `print_stats.state` ist nicht `printing`,
  - die Fahrt führt tiefer als die aktuelle Höhe.
- Bei einer Ablehnung gibt es einen G-Code-Fehler und keine Bewegung.
  `gcode_move.reset_last_position()` hält die G-Code-Position synchron.

Warum **Ablehnen statt Klemmen:**
- Stock fährt still auf −2. Die Düse geht trotzdem ins Bett, und der fehlerhafte
  Befehl bleibt unbemerkt.
- Der Fehler bricht das Makro ab.

Warum **Transform statt G1-Wrapper:**
- Der Transform deckt G0, G1, G2/G3 (`gcode_arcs` ruft `cmd_G1` direkt) und
  `RESTORE_GCODE_STATE MOVE=1` ab.
- G1 muss nicht umbenannt werden. Das vermeidet Konflikte mit anderen
  `rename_existing`-Makros.

Interne Fahrten bleiben wie bei Stock unberührt: Probe, Homing, `k2_zalign`,
Cut-Kalibrierung. Sie laufen über `toolhead`, nicht über `gcode_move`.

**Druck-Schutz: G-Code-Z < 0,05 im Druck wird angehoben** (18.10.2026, kein
Stock-Vorbild).

- Anlass: Orcas K2-Vorlage `change_filament_gcode` endet mit
  `G1 Z{z_after_toolchange} F600`. Vor der ersten Schicht ist
  `z_after_toolchange` = 0. Nach einem Werkzeugwechsel vor der ersten Schicht
  fährt die Düse auf Z0 und kratzt übers Bett (bekannt aus KAMP-K2, README
  Punkt 3). Die Dateien senden kein `SET_PRINT_STATS_INFO`, eine Schichtinfo
  gibt es also nicht.
- Im selben Transform: Bei `print_stats.state == "printing"` und gehomtem Z
  wird ein Ziel mit G-Code-Z < `print_min_z` (0,05) nicht gefahren, sondern
  auf G-Code-Z `print_low_z_raise` (2,0) angehoben. Eine Meldung je Druck
  (`k2_limits: G1 Z0.000 im Druck → auf Z2.00 angehoben (Orca
  z_after_toolchange=0?) …`, Schlüssel `print_stats.print_start_time`).
- **G-Code-Ebene:** Schwelle und Ziel gelten vor `SET_GCODE_OFFSET`/`G92`
  (`newpos.z − gcode_move.base_position.z`). Ein Z-Offset verschiebt also
  beides mit; tempcomp und bed_mesh liegen darunter in der Kette und wirken
  auf die angehobene Höhe wie auf jede andere.
- Die G-Code-Position (`gcode_move.last_position`) bleibt beim angeforderten
  Wert. Folgefahrten ohne Z (XY, E) bleiben dadurch ebenfalls angehoben, bis
  eine Fahrt mit Z ≥ 0,05 kommt (erste Schicht). Ohne das würde die nächste
  XY-Fahrt wieder auf Z0 fallen. `get_position` liefert die echte Lage; nur
  ein `reset_last_position` übernimmt sie in die G-Code-Position.
- G0/G1, G2/G3 (Segmente über `cmd_G1`), `RESTORE_GCODE_STATE MOVE=1` und
  `SET_GCODE_OFFSET MOVE=1` laufen alle durch den Transform. Normale
  Schichtfahrten (≥ 0,05) sind unverändert.
- **Pause (`paused`):** kein Eingriff, es gilt die alte −2-Regel. Ausnahme:
  Stand der Kopf beim Pausieren gerade durch den Schutz angehoben
  (`low_z_holding`), dann wird auch die RESUME-Rückfahrt
  (`_PI_RESUME_RETURN` `G1 Z{pause_z}`, Pausen-Z = G-Code-Z 0) angehoben.
  Sonst setzte RESUME die Düse an der Pausenposition aufs Bett.
- **Z ungehomt:** kein Eingriff. `START_PRINT` fährt vor `G28 Z` XY mit
  ungehomtem Z; ein Anheben wäre dort eine Z-Fahrt und Upstream bräche mit
  „Must home axis first“ ab.
- Außerhalb des Drucks: unverändert (−2-Regel). Homing, Probe, `z_tilt`,
  `k2_zalign` (intern über `toolhead`) bleiben unberührt.

## 4. Wo aufgeweitet wird

| Stelle | Wie | Rücksetzen |
|---|---|---|
| `k2_box` – jeder Bewegungsschritt (`_move`): Schacht, Wischen, Reinigung, Spülen, Restmaterial, Rückweg | nur wenn die Zeilen einen XY-Punkt außerhalb des engen Fensters anfahren (`needs_wide` → `xy_points`: G90/G91, G2/G3-Hülle) | `finally` (auch bei Fehler); der Schnitt an `cut_pos_x` liegt genau auf der Grenze und weitet nicht |
| `_PI_CLEAN_PATH` PART=GO / WIPE (macros.cfg, G-Code-Weg der Reinigung) | `KTWO_LIMITS_WIDE` vor der Einfahrt | `KTWO_LIMITS_RESTORE` am Ende des Teils; LEAVE führt ins Fenster |
| `_PI_RESUME_RETURN` – Pausenposition im Schacht (Y > 352) | `KTWO_LIMITS_WIDE` nur in diesem Zweig | `KTWO_LIMITS_RESTORE` direkt danach |
| `CALIBRATE_CUT_POS` (cutcal.cfg) | `KTWO_LIMITS_WIDE` vor `KTWO_MOTOR_CUT_CAL` (Anstöße bis −9,5) | `KTWO_LIMITS_RESTORE` nach `M400`; Fehler → automatisch |

Ohne Aufweitung (geprüft):
- **PAUSE:** verlässt Hebel und Schacht nur in Richtung Fenster und parkt bei
  X342,5 Y176. Das entspricht Stock: `axis_maximum.y / 2` mit 352. Vorher war
  es Y200.
- **Homing-Rückzüge:** X342,5 / Y3,8.
- **START_PRINT, G29, END_PRINT:** Z ≤ 350 (`max_z_position`).
- **E-, X- oder Z-Fahrten im Schacht nach dem Rücksetzen:** Upstream prüft nur
  bewegte Achsen.

## 5. Optionen `[k2_limits]`

| Option | Default | Grenze |
|---|---|---|
| `x_min_wide` | −10 | ≥ −10, ≤ 0 |
| `y_max_wide` | 400 | 352…400 |
| `x_min_from_cut_pos` | True | X min = `[k2_box] cut_pos_x` (muss −10…0 sein) |
| `z_guard` | True | False = kein Transform |
| `z_min_outside_print` | −2 | −2…0 |
| `print_low_z_guard` | True | False = Druck-Schutz aus (Transform bleibt bei `z_guard`) |
| `print_min_z` | 0.05 | 0…1 (G-Code-Z) |
| `print_low_z_raise` | 2.0 | 0,2…20, mindestens `print_min_z` + 0,1 |

G-Codes: `KTWO_LIMITS_WIDE [REASON=]`, `KTWO_LIMITS_RESTORE`, `KTWO_LIMITS_STATUS`.
Status `printer.k2_limits`: `wide`, `holders`, `x_min`/`x_max`/`y_min`/`y_max`
(eng), `x_min_wide`, `y_max_wide`, `z_max`, `z_min_outside_print`, `z_rejects`,
`print_low_z_guard`, `print_min_z`, `print_low_z_raise`, `low_z_raises`,
`low_z_holding`.

## 6. Tests

- `test_k2_limits.py` (103 Prüfungen):
  - Core (eng/weit, Halter, ungehomt, Homing im weiten Zustand, motor_off)
  - Upstream-`check_move`-Nachbau
  - `xy_points`
  - alle k2_box-Fahrwege gegen beide Fenster
  - Cut-Kalibrierung, PAUSE-Park und Homing-Rückzüge
  - Modul mit Fake-Klipper (Config-Sperren, Connect, G-Codes, Auto-Rücksetzen,
    Z-Wächter)
  - Makros gerendert und mit Grenzprüfung abgefahren (0 „Move out of range“,
    Gegenprobe ohne WIDE scheitert)
  - M141/M191-Kammergrenze
  - Druck-Schutz: Z0 im Druck → Z2 + Meldung einmal je Druck, Folgefahrten
    angehoben, Z0,2/Z0,05 unverändert, Offsets (G-Code-Ebene), außerhalb des
    Drucks −2-Regel, Pause ohne Eingriff bzw. RESUME-Rückfahrt beim Halten,
    Option aus, eigene Werte/Config-Fehler, Z ungehomt, Status; Kette
    k2_limits → k2_nozzle_tempcomp → bed_mesh (beide Connect-Reihenfolgen)
- `test_k2_box.py` „L3 Fahrgrenzen“: Schacht weit und zurück; Schnitt ohne
  Aufweitung; Fehler mitten im Schacht → eng; ungehomt → keine Fahrt.
- Batch (Upstream-Klipper im Batch-Modus, lokale Testumgebung):
  - Grenzen-Läufe:
    - `ok` EXIT 0
    - `y360` → „Move out of range“
    - `z3` → abgelehnt
    - `z2` → Z−2 fährt, Z351 „out of range“
    - `wfehl` → nach Fehler automatisch eng
    - `orca` (Druck über virtual_sdcard: START_PRINT, T0, Purge Z0,2,
      Wechsel-Ende `G2 Z0.4 …`/`G1 X0 Y245`/`G1 Z0`, erste Schicht Z0,2) →
      EXIT 0, 1 Meldung, Toolhead-Z ab START_PRINT-Ende min 0,2 (0 Fahrten
      < 0,05); Gegenprobe `orca-aus` (Option False): min 0,0, 3 Fahrten < 0,05
  - übrige Batch-Läufe (Module, RESUME nach Schnitt, Spülen, A4/A5,
    Cut-Kalibrierung): 0 „Move out of range“, 0 Internal error.

## 7. Offen / am Gerät

- Die Parkposition der PAUSE liegt jetzt bei Y176 (wie Stock) statt Y200.
- `zalign.cfg`/`k2_zalign`: Der Z-Bereich ist jetzt −10…350,2 (Kommentar dort
  nennt noch 360/370).
  - Phase A fährt in Etappen ab `axis_minimum` und ist nicht betroffen.
  - Setzt `zmax_position` Z über 350,2 (z. B. 352,2), sind danach nur Fahrten
    nach unten möglich. Das ist gewollt.
- Am Gerät prüfen:
  - `KTWO_LIMITS_STATUS` nach `G28`,
  - Spülen und Schacht im Druck,
  - `CALIBRATE_CUT_POS`,
  - `G1 Z-3` außerhalb des Drucks (muss abgelehnt werden).
  - Orca-Datei mit Werkzeugwechsel vor der ersten Schicht: Meldung
    „im Druck → auf Z2.00 angehoben“, Düse bleibt 2 mm über dem Bett bis zur
    ersten Schicht.
- Druck-Schutz, bewusst nicht abgedeckt: Fahrten mit G-Code-Z < 0,05, die
  während `paused` entstehen, ohne dass der Schutz vorher gehalten hat
  (alte −2-Regel). Ein Druck, der absichtlich unter G-Code-Z 0,05 druckt
  (z. B. mit stark negativem Z-Offset im Slicer statt `SET_GCODE_OFFSET`),
  wird angehoben → dann `print_min_z` senken oder Option aus.

## Konsole (Audit 23.10.2026)

Scheitert beim Connect das Nachziehen der Statusanzeige `axis_minimum.x`,
erscheint `// [k2_limits] Statusanzeige axis_minimum.x nicht … nachgezogen`
kurz nach „ready“ (die Fahrgrenzen selbst gelten trotzdem). Neu in `k2_box`:
Scheitert die Freigabe der M8200-Fahrgrenzen, meldet `k2_box` das rot
(`KTWO_LIMITS_RESTORE`).

Regeln, Format und Gesamttabelle: `README-k2_konsole.md`.
