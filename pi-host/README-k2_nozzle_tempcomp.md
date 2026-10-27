# [k2_nozzle_tempcomp] – Düsen-Temperaturkompensation wie Stock (L7)

> **ENTWURF 18.10.2026 – am Gerät UNGETESTET.** Nur offline getestet
> (`test_k2_nozzle_tempcomp.py`, 69/69) und im Batch gegen Upstream-Klipper
> 9cb9975 (Batch-Modus, lokale Testumgebung). Lücke L7 aus dem Vergleich
> Creality-Klippy ↔ Upstream.

## 1. Was Stock macht

Creality-`prtouch_v3` (`get_z_now_comp`) hebt die Düse im Druck um einen
kleinen Betrag an, weil sich die heiße Düse gegenüber dem Antasten
(bei 140 °C) ausdehnt:

- ΔT = Düsen-**Soll**temperatur − Referenz. Die Referenz ist die geglättete
  Düsen-**Ist**temperatur beim letzten `G28 Z` bzw. `BED_MESH_CALIBRATE`.
- `comp = 0` für ΔT ≤ 0, sonst `min(0.181, a·ΔT² + b·ΔT + c)`, danach fest auf
  0 … 0,2 mm begrenzt. a = 5.1e-06, b = 0, c = 0.017316.
- Wirkt nur bei **geladenem Netz** und im Zustand **printing/paused**.
- Blendet mit dem Netz-Fade aus (voll bis 5 mm, 0 ab 50 mm).
- Neu gerechnet wird nur, wenn sich die Solltemperatur ändert.
- Nichts wird gespeichert.

Typische Werte (Referenz ≈ 140 °C): 220 °C → +0,050 mm, 240 °C → +0,069 mm.
Alle 17 Werte-Tupel aus den Live-Logs rechnen sich exakt nach (Test).

## 2. Umsetzung auf dem Pi

- Modul `k2_nozzle_tempcomp.py` → `klippy/extras/k2_nozzle_tempcomp.py`.
- Abschnitt `[k2_nozzle_tempcomp]` in `config-pi/k2-module.cfg`.
- **G-Code-Bewegungs-Transform** nach Upstream-Muster `z_thermal_adjust`:
  - `move()` addiert `comp · Fade-Faktor`.
  - `get_position()` zieht es wieder ab.
  - Der Fade-Faktor kommt aus `bed_mesh.get_z_factor()`.
- **Platz in der Kette:** Das Modul hängt sich beim Connect **direkt über
  `bed_mesh`** ein. Die Reihenfolge der Abschnitte ist egal (geprüft in
  beiden Reihenfolgen):

  ```
  gcode_move -> [k2_limits] ZGuardTransform -> k2_nozzle_tempcomp -> bed_mesh -> toolhead
  ```

  - Der Z-Wächter von `[k2_limits]` sieht dadurch das **unkompensierte** Z.
    Z < −2 wird außerhalb des Drucks (auch in der Pause) weiter abgelehnt.
  - `exclude_object` hängt sich zur Laufzeit oben ein und wieder aus. Das
    stört nicht.
  - Ohne `bed_mesh` landet das Modul oben in der Kette und bleibt wirkungslos,
    weil ein Netz Pflicht ist.
- **Referenz:**
  - Bei `homing:home_rails_end` mit Z wird `smoothed_temp` übernommen und
    die Neurechnung erzwungen. Upstream sendet das Event auch beim `G28 Z` über
    `probe:z_virtual_endstop`.
  - Nach **erfolgreichem** `BED_MESH_CALIBRATE` wird nur die Referenz gesetzt,
    wie Stock. Dazu wird der Befehl beim Connect umhüllt. Ein
    `gcode_macro BED_MESH_CALIBRATE` mit `rename_existing` funktioniert
    weiterhin.
- **Freigabe:** `enable` + `print_stats.state` ∈ {printing, paused} +
  `bed_mesh.get_mesh()` ≠ None + vorhandene Referenz. Sonst ist comp = 0.
- Ändert sich comp (Solltemperatur, ENABLE, Druckende), wirkt das ab der
  **nächsten Bewegung**, wie bei Stock.

## 3. Config

| Option | Default | Bedeutung |
|---|---|---|
| `enable` | True | AN wie Stock (Stock nutzt es live). `False` = nie aktiv |
| `a`, `b`, `c` | 5.1e-06, 0.0, 0.017316 | Stock-Koeffizienten |
| `max_comp` | 0.181 | Obergrenze der Kennlinie (0 … 0,2). Die Endklemme 0 … 0,2 ist **fest** |
| `fade_with_mesh` | True | wie Stock mit dem Netz-Fade ausblenden; False = konstanter Zuschlag |
| `ref_on_mesh_calibrate` | True | Referenz auch nach `BED_MESH_CALIBRATE` |
| `heater` | extruder | Düsen-Heizung |

## 4. G-Code `KTWO_NOZZLE_TEMPCOMP` (Ersatz für `SET_TEMP_NOT_LINEAR`)

| Aufruf | Wirkung |
|---|---|
| ohne Parameter | nur Status: aktiv/Grund, comp, angewandt, Soll, Referenz, a/b/c, Kette |
| `A= B= C=` | Koeffizienten setzen (nicht angegebene bleiben) und sofort neu rechnen |
| `RESET=1` | Werkswerte a/b/c/max_comp |
| `ENABLE=0` / `ENABLE=1` | zur Laufzeit aus- bzw. einschalten (wird nicht gespeichert) |
| `COMP_Z=<mm>` | Wert erzwingen bis zur nächsten Solländerung (auf 0 … 0,2 begrenzt) |
| `REF=<°C>` | Referenz von Hand setzen (wie G28: Neurechnung) |

Status-Objekt `printer.k2_nozzle_tempcomp`: `enabled`, `active`, `reason`,
`comp`, `applied` (zuletzt addiert, inkl. Fade), `target`, `ref_temperature`,
`ref_source`, `a`, `b`, `c`, `max_comp`, `fade_with_mesh`, `forced`, `recalcs`.

Im klippy.log steht bei jeder Neurechnung die Stock-Logzeile
`get_z_now_comp now_comp:… target_temp:… G28_temp:…`. Sie lässt sich direkt mit
der entsprechenden Zeile im Stock-klippy.log vergleichen.

## 5. Bewusste Abweichungen vom Stock

1. **Ohne Referenz** (seit dem Start kein G28 Z) ist comp = 0. Stock rechnet
   dann gegen den Startwert von `g28_temp`.
2. **Ohne Parameter** gibt `KTWO_NOZZLE_TEMPCOMP` nur den Status aus. Stock
   `SET_TEMP_NOT_LINEAR` setzt ohne Parameter auf Werkswerte zurück. Der Reset
   geht hier ausdrücklich mit `RESET=1`.
3. `COMP_Z` wird ebenfalls auf 0 … 0,2 begrenzt. Bei Stock ist es ungeklemmt.
4. Nicht nachgebaut, weil live inaktiv:
   - lineare Variante (`enable_not_linear_comp: False`)
   - `SET_CUMULATIVE_HEATING_TIME`
   - Kammerkompensation
   - Fall-Kompensation (eigener Punkt)

## 6. Abschalten / Fehlersuche

- Dauerhaft: `enable: False` im Abschnitt.
- Zur Laufzeit: `KTWO_NOZZLE_TEMPCOMP ENABLE=0`.
- Prüfen: `KTWO_NOZZLE_TEMPCOMP` zeigt Grund und Kette. Mit `GET_POSITION`
  ist die Toolhead-Z im Druck um comp·Fade höher als die G-Code-Z (plus Netz).
- **Erste Prüfung am Gerät:** Erste Schicht bei 220 °C beobachten. Der
  Unterschied zu „aus“ beträgt +0,05 mm.

## 7. Tests

- `python3 test_k2_nozzle_tempcomp.py` (69 Prüfungen):
  - Formel gegen 17 Log-Tupel
  - CompCore
  - Freigabe
  - Referenz G28 vs. Mesh
  - Fade
  - Transform-Kette mit Fake-bed_mesh (Upstream-Formeln) und echtem
    `k2_limits` (beide Reihenfolgen, ohne bed_mesh)
  - G-Code
  - Status
  - Config
  - Python 3.9
- Batch (Upstream-Klipper, Modul-Läufe): EXIT-Bild unverändert, 0 Internal
  error.
- Batch (Kompensations-Lauf): G28 → Netz → M104 → Druckzustände,
  8× Toolhead-Z exakt, k2_limits-Ablehnung in der Pause.

## Konsole (Audit 23.10.2026)

Die Connect-Warnungen „kein [print_stats]/[virtual_sdcard] – Kompensation nie
aktiv“ und „bed_mesh nicht in der Transform-Kette gefunden – oben eingehängt“
erscheinen als `// [k2_nozzle_tempcomp] …` kurz nach „ready“ (vorher nur Log).

Regeln, Format und Gesamttabelle: `README-k2_konsole.md`.
