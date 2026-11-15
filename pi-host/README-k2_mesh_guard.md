# k2_mesh_guard – Ausreißer-Wächter für die Bettvermessung

Stand 15.11.2026 – **ENTWURF, am Gerät UNGETESTET.** Modul `pi-host/k2_mesh_guard.py`,
Abschnitt `[k2_mesh_guard]` (Vorschlag: `config-pi/k2-module.cfg`). Tests:
`python3 test_k2_mesh_guard.py` (ohne Klipper, ohne Hardware).
Idee aus dem Projekt K2 OpenKlipper (GPLv3), kein Code übernommen.

## 1. Zweck

Beim K2 Plus ist die Düse der Taster (`[k2_prtouch]` = Objekt `probe`). Hängt an der
Düse ein Materialrest, liefert ein einzelner Punkt der Bettvermessung einen zu hohen
Wert, und die erste Schicht liegt dort falsch. Das Modul prüft nach dem letzten
Messpunkt jeder `BED_MESH_CALIBRATE` (auch adaptiv / `_PI_MESH_DRUCKBEREICH`), ob ein
Punkt stark vom **Median seiner direkten Nachbarn** (links/rechts/vorn/hinten)
abweicht:

- `mode: report` (Default): Ausreißer nur **melden**. Nichts wird neu angetastet,
  kein Wert geändert.
- `mode: act`: jeden Ausreißer erneut anfahren (gleiche XY-Position, Hubhöhe und
  Geschwindigkeit wie bei der ursprünglichen Messung), `repeats`-mal antasten und
  den **Median aus altem und neuen Werten** übernehmen. Danach rechnet `bed_mesh`
  unverändert weiter.
- `mode: off`: Modul hängt sich nicht ein.

Mehr als `max_points` Ausreißer: nichts neu antasten, nur Meldung „Bett/Düse prüfen“.

Meldungen erscheinen in der Konsole als `// [k2_mesh_guard] …` (je Ausreißer:
Position, alter Wert, Nachbar-Median, Abweichung; in `act` zusätzlich neue Werte und
bestätigter Wert) und im `klippy.log`.

## 2. Optionen

| Option | Default | Bereich | Bedeutung |
|---|---|---|---|
| `mode` | `report` | `off`, `report`, `act` | aus / nur melden / Ausreißer neu antasten |
| `threshold` | `0.25` | 0.05 – 2 | mm Abweichung vom Nachbar-Median, ab der ein Punkt als Ausreißer gilt |
| `min_neighbours` | `2` | 1 – 4 | so viele Nachbarn braucht ein Punkt, sonst wird er nicht bewertet (2 = auch Ecken) |
| `max_points` | `6` | 1 – 20 | mehr Ausreißer → nicht neu antasten, nur melden |
| `repeats` | `2` | 1 – 5 | Nachmessungen je Ausreißer (bei 1 ist der bestätigte Wert das Mittel aus alt und neu) |
| `max_shift` | `1.0` | 0.05 – 5 | mm; verschiebt sich ein Wert durch das Nachmessen weiter als das, gibt es eine Warnung (der Wert wird trotzdem übernommen) |
| `edge_trend` | `True` | `True`, `False` | Rand-Fortsetzung, s. Abschnitt 5 |

Werte außerhalb des Bereichs führen zu einem Config-Fehler mit Klartext, z. B.
`[k2_mesh_guard] Option 'threshold' = 3 liegt außerhalb des erlaubten Bereichs 0.05 bis 2.0 (…)`.

## 3. Config-Ausschnitt (k2-module.cfg)

```ini
# ---------------------------------------------------------------------
# Ausreißer-Wächter der Bettvermessung (pi-host/k2_mesh_guard.py,
# README-k2_mesh_guard.md). Materialrest an der Düse -> einzelner
# Mesh-Punkt zu hoch. report = nur melden, act = Punkt neu antasten.
# Status: KTWO_MESH_GUARD_STATUS
# ---------------------------------------------------------------------
[k2_mesh_guard]
mode: report
#threshold: 0.25       # mm Abweichung vom Median der Nachbarn
#min_neighbours: 2
#max_points: 6         # mehr Ausreißer -> nur Meldung
#repeats: 2            # Nachmessungen je Ausreißer (nur mode: act)
#max_shift: 1.0        # mm, Warnung bei größerer Verschiebung
```

Reihenfolge der Abschnitte ist egal; `[bed_mesh]` und die Sonde müssen vorhanden sein.
`install.sh` kopiert `pi-host/k2_*.py` nach `klippy/extras/`, das Modul kommt also mit.

Status: `printer.k2_mesh_guard` (`mode`, `hooked`, `last_action`, `last_points`,
`last_outlier_count`, `last_outliers`, `last_remaining_count`, `counters`, Optionen).
`last_action`: `none` (kein Ausreißer), `reported`, `reprobed`, `too_many`, `blocked`
(Nachmessen nicht möglich), `skipped`, `error`, `off`.
G-Code: `KTWO_MESH_GUARD_STATUS`.

## 4. Einführungsreihenfolge

1. **`mode: report`** eintragen, Klipper neu starten. `KTWO_MESH_GUARD_STATUS` muss
   `eingehängt=ja` zeigen. Steht kurz nach dem Start `// [k2_mesh_guard] nicht aktiv – …`
   in der Konsole, passt die Einhängestelle nicht zum Klipper-Stand (Vermessung läuft
   dann unverändert, nur ohne Prüfung).
2. Mehrere normale Drucke und mindestens ein volles 9×9-Mesh laufen lassen. Erwartet:
   keine Meldung. Kommen Meldungen bei sauberer Düse (vor allem an Ecken/Rändern),
   `threshold` anheben oder die Werte mit `BED_MESH_OUTPUT` vergleichen.
3. Gegenprobe: ein Stück Klebeband o. Ä. auf einen Messpunkt legen (ersetzt den
   Materialrest) → der Punkt muss gemeldet werden.
4. Erst dann **`mode: act`**, erste Läufe **unter Aufsicht** (Hand am Not-Aus): die
   Düse fährt nach dem letzten Messpunkt noch einmal zu den gemeldeten Punkten.
   Kontrolle: Konsole zeigt neue Werte und bestätigten Wert; `BED_MESH_OUTPUT`.

## 5. Arbeitsweise, Grenzen, Annahmen

**Einhängestelle** (Klipper-Stand des Druckers): `bed_mesh.bmc.probe_mgr.probe_helper`
(`bed_mesh.py:93`, `:335`, `:823`) ist der `ProbePointsHelper`; sein Attribut
`finalize_callback` (`probe.py:431`, gelesen in `probe.py:471`) wird nach
`klippy:connect` umhüllt. In `act` zusätzlich `start_probe` (`probe.py:480`), nur um
den G-Code-Befehl der laufenden Vermessung zu kennen. Nachgemessen wird über die noch
offene Sonden-Sitzung `probe.probe_session` (`probe.py:318`), angefahren mit
`_raise_tool()`/`_move_next()` des Helfers (`probe.py:460`, `:473`). Upstream-Dateien
werden nicht geändert.

**Sicherheit.** Fehlt ein erwartetes Attribut oder tritt im Wächter eine Ausnahme auf,
wird das einmal gemeldet und `bed_mesh` bekommt die **unveränderten** Messwerte. Nur
wenn die Sonde beim Nachmessen selbst einen Fehler wirft (oder Klipper bei der Anfahrt
einen G-Code-Fehler), bricht die Vermessung ab – wie bei jedem anderen Messpunkt.
Werte werden erst nach vollständig gelungenem Nachmessen ersetzt, alle auf einmal.

**Rand-Fortsetzung (`edge_trend`).** Abweichung von der reinen Median-Regel: fehlt der
gegenüberliegende Nachbar (Rand, Ecke, Lücke), wird der Nachbarwert linear über den
Punkt hinaus fortgesetzt (2·n1 − n2). Grund: eine Ecke hat nur zwei Nachbarn, die
beide „innen“ liegen. Beispiel 9×9, Wölbung 0,8 mm Mitte–Ecke plus leichte Schräge:
Form-Abweichung der Ecke ohne Fortsetzung 0,17 mm, mit 0,05 mm. Eine reine Schräge
ergibt mit Fortsetzung überall 0. Ein erkannter Ausreißer dient danach nicht mehr als
Vergleichswert für seine Nachbarn.

**Grenzen.**
- Hängt der Materialrest **dauerhaft** an der Düse, sind alle Punkte gleich versetzt –
  das erkennt kein Nachbar-Vergleich. Erkannt wird ein Rest, der nur einzelne Punkte
  verfälscht (fällt ab, wird plattgedrückt, liegt auf dem Bett).
- Bleibt der Rest beim Nachmessen an der Düse, bestätigt die Nachmessung den falschen
  Wert. Dann meldet das Modul „Ausreißer bleiben auch nach dem Nachmessen“ – der Wert
  bleibt, der Druck wird **nicht** angehalten.
- Mehrere benachbarte Fehlwerte (2×2 und größer) sehen wie Bettform aus und können
  durchrutschen.
- Grobe Raster mit starker Wölbung (z. B. 5×5 über das ganze Bett bei mehr als ca.
  1 mm Mitte–Ecke) können an den Ecken Fehlmeldungen geben → `threshold` anheben
  oder `min_neighbours: 3`.
- `[bed_mesh] faulty_region_*`: Prüfung wird übersprungen (Meldung). Ein Nullpunkt
  außerhalb des Rasters (`zero_reference_position`) wird nicht bewertet. Schnell-Scan
  (`METHOD=rapid_scan`) und manuelle Vermessung: kein Nachmessen.
- `act` verlängert die Vermessung je Ausreißer um `repeats` Antastungen.

**Am Drucker zu prüfen (ANNAHMEN).**
1. `KTWO_MESH_GUARD_STATUS` → `eingehängt=ja` (Attributnamen passen zum installierten
   Klipper; geprüft nur gegen eine Kopie der Dateien).
2. Die Ergebnisobjekte der Sonde haben `bed_x`/`bed_y`/`bed_z` und `_replace`
   (`ProbeResult`); `probe.probe_session` hat `hw_probe_session`, `run_probe`,
   `pull_probed_results`. Sonst meldet `act` „Nachmessen nicht möglich – …“.
3. Verhalten von `[k2_prtouch]` bei zusätzlichen Antastungen in derselben Sitzung
   (Lüfter-Dämpfung, Ruhe-Prüfung, Temperatur) – offline nur mit Attrappen geprüft.
4. Zusammenspiel mit Sofort-Abbruch (`[k2_abbruch]`) während des Nachmessens.
5. `threshold 0.25` gegen echte Messwerte des Betts (Streuung der Sonde, Bettform bei
   Betriebstemperatur); adaptive Raster mit kleinem Punktabstand.
6. `max_shift`: Auslegung als Verschiebung „bestätigter minus alter Wert“ (Warnung,
   Wert wird übernommen) – bei Bedarf anders festlegen.
