# k2_resume – Wiedereinstieg ab Schicht (+ Bedienschutz TURN_OFF_HEATERS)

Stand 23.10.2026 · Modul `k2_resume.py` (`[k2_resume]` in printer.cfg) · Makros in `config-pi/macros.cfg`
(`PRINT_FROM_LAYER`, `_PI_WIEDEREINSTIEG_BOX_START`, `_PI_WIEDEREINSTIEG_VORDRUCK`) · Antastpunkt-Übernahme in
`config-pi/homing.cfg` · Tests `test_k2_resume.py` (104 Prüfungen offline; echte Dateien optional über
`K2_RESUME_ECHTDATEIEN`).

## 1. Wozu

Stock (Creality) setzt einen Druck nach Stromausfall fort (Power-Loss). Upstream-Klipper kann das nicht. Bricht ein
Druck ab (Fehler, versehentliches „Heizungen aus“, `CANCEL_PRINT`, Not-Halt), liegt das halbe Teil auf dem Bett.
`k2_resume` erzeugt eine **neue Datei** `<name>_ab_schicht_<n>.gcode`, die ab Schicht n weiterdruckt:

- **Vorspann**: Zustand der Originaldatei an der Schichtgrenze – Düse/Bett/Kammer-Soll, Werkzeug (`T<n>` bzw. letztes
  `M8200 L I<n>`), E-Modus, Lüfter (`M106` je P), `M220`/`M221`, Beschleunigung (`SET_VELOCITY_LIMIT` bzw. `M204`),
  letzter Vorschub.
- **Rest**: die Originaldatei ab dem Marker `;LAYER_CHANGE` der Schicht n, **byte-genau** (inkl. Fuß mit
  Slicer-Konfiguration). Kopf (inkl. Vorschaubild) und `EXCLUDE_OBJECT_DEFINE` werden vorangestellt.

Schichtmarker `;LAYER_CHANGE` + `;Z:<mm>` – geprüft an OrcaSlicer-, Creality-Print-7- und den eigenen
Generator-Dateien.

## 2. Ablauf für den Nutzer

**Das Teil bleibt auf dem Bett. Nichts anfassen, nicht am Bett ziehen.**

1. Klipper läuft, kein Druck aktiv (abgebrochen ist ok; **pausiert → `RESUME`**, nicht Wiedereinstieg).
2. Schichten ansehen: `KTWO_RESUME_INFO FILE=<datei>` (optional `LAYER=<n>` für den Zustand an Schicht n). Wurde die
   Datei gerade abgebrochen und kennt Klipper die Dateiposition noch, steht dort „Abbruch lag in Schicht …“.
3. Datei erzeugen: `PRINT_FROM_LAYER FILE=<datei> LAYER=<n>` (oder `LAYER=auto` = Abbruchschicht, oder `Z=<mm>`).
   - Empfehlung: die **unterbrochene Schicht selbst** (sie wird über dem Teilstück erneut gedruckt). Eine Schicht
     weiter fehlt sonst ein Stück.
4. Meldung lesen (Modus, Fahrhöhe, ggf. Antastpunkt und **WARNUNG**), dann die neue Datei in Mainsail drucken
   (oder gleich `START=1`).

## 3. Z-Referenz – die beiden Modi

| Modus | Wann | Was passiert |
|---|---|---|
| `keep` | Klipper **nicht** neu gestartet, kein Not-Halt, Motoren an (`homed_axes = xyz`) – z. B. nach „Extrude below minimum temp“ oder `CANCEL_PRINT` | **kein Homing**. Erst NUR Z auf sichere Höhe (höchster gedruckter Punkt + 10 mm), dann alles Weitere |
| `probe` | nach Neustart/Not-Halt (Bett abgesackt, Referenz verloren) | `G28 X Y`; Z-Antasten an einem **freien Punkt P neben dem Teil** (`KTWO_RESUME_ZHOME`, homing.cfg nimmt P statt der Bettmitte); Netz `default`; Z-Bezug von P auf die Bettmitte umrechnen (`KTWO_RESUME_ZFIX`: `SET_GCODE_OFFSET Z_ADJUST = m(Mitte) − m(P)`, Netzwerte) |

`MODE=auto` (Standard) wählt nach dem aktuellen Referenzzustand. Die Datei prüft beim Start erneut
(`KTWO_RESUME_BEGIN`): `keep` ohne Referenz bricht **vor jeder Bewegung** ab; `probe` verlangt
`ALLOW_MOTION`.

**Wahl von P** (Belegungsraster 5 mm aus allen bisher gedruckten Bahnen auf dem Bett – Spülen am Schacht zählt
nicht, Bögen werden entlang ihrer Bahn erfasst):

- unter der Düse (Radius 10 mm) **keinerlei** Gedrucktes (Messgenauigkeit),
- unter dem Kopf (Radius `probe_margin`, 50 mm) nichts höher als 1 mm (Vorreinigungslinien sind ok) – das Bett fährt
  beim Antasten ganz hoch,
- unter der X-Brücke (Band ±30 mm um Y von P, ganze Breite) nichts höher als 25 mm (**ANNAHME** Luft unter der
  Brücke),
- X möglichst = Mitte der Objekte: Die Dual-Z-Schräglage wirkt nur in X, so bleibt ihr Einfluss über dem Teil klein.

**Bett-Ausrichtung (Z_TILT_ADJUST)** nach einem Neustart: nur, wenn die Antastpunkte (X5/X345, Y175) und die
Fahrstrecke auf `horizontal_move_z` 5 mm frei sind (sonst führe der Kopf durchs Teil; höher geht nicht, weil
PrTouch außerhalb G28 höchstens 8 mm weit antastet). Sonst:

- `TILT=auto` (Standard): Abbruch mit Erklärung und geschätztem Fehler,
- `TILT=0`: Datei ohne Ausrichten, mit **WARNUNG** (Schätzung aus der live gemessenen Schräglage nach Neustart,
  0,55–0,59 mm über 340 mm; über einem 50 mm breiten Teil um ±0,04–0,08 mm),
- `TILT=1`: Ausrichten erzwingen – nur wenn die Prüfung frei meldet, sonst Abbruch.

Die eigentliche Lösung für den Neustart-Fall ist die Lichtschranken-Ausrichtung wie Stock (Phase 11, `[k2_zalign]`).

## 4. Vorspann (Reihenfolge)

`SET_PRINT_STATS_INFO` → `KTWO_RESUME_BEGIN` → `CLEAR_PAUSE`, `G90` → (probe: `M140`, Düse 140 °C, `G28 X Y`,
`KTWO_RESUME_ZHOME`, sofort `G1 Z<sicher>`, Bett/Düse warten, ggf. `Z_TILT_ADJUST` + erneut ZHOME,
`SET_GCODE_OFFSET Z=0`, Netz laden, `KTWO_RESUME_ZFIX`) bzw. (keep: **zuerst** `G1 Z<sicher>`) → Kammer, Düse,
Bett warten → `_PI_WIEDEREINSTIEG_BOX_START` (CFS-Druckzustand wie START_PRINT) → `T<n>` (k2_box-Kette:
schneiden/zurück/einziehen/spülen, oder „bereits geladen“) → `G1 Z<sicher>` → `M109` →
`_PI_WIEDEREINSTIEG_VORDRUCK` (am Abfallschacht 25 mm vordrucken, Silikon, Schacht verlassen; ohne
`[k2_box] allow_move` übersprungen) → Zustand → **auf sicherer Höhe** zum ersten Punkt der Schicht → absenken →
Rest der Datei.

## 5. Grenzen / offene Punkte

- Die Erzeugung läuft in einem eigenen Prozess (Klipper blockiert nicht; 15-MB-Datei ≈ 0,4 s).
- Nicht für einen pausierten Druck (dafür `RESUME`).
- Höhe der X-Brücke über der Düse (25 mm) und Kopf-Umriss (50 mm) sind **ANNAHMEN** – bei hohen Teilen erst messen.
- `LAYER=auto` geht nur, solange `virtual_sdcard` die Dateiposition des Abbruchs noch kennt (nach Neustart nicht).
- Ein Teil der unterbrochenen Schicht wird doppelt gedruckt (Düse fährt in Schichthöhe über das Teilstück).
- Adaptives Netz des Originaldrucks (`ADAPTIVE=1`, Profil `adaptiv`) wird im Modus `probe` nicht wiederhergestellt
  (es gilt `default`).

## 6. Bedienschutz gegen versehentliche Abbrüche (23.10.2026)

Anlass: 22.10. abends zwei Abbrüche über Mainsail am Handy (Not-Aus, „Heizungen aus“).

**TURN_OFF_HEATERS** (macros.cfg, `rename_existing: TURN_OFF_HEATERS_BASE`): Während eines Drucks (`printing`/
`paused`) wird **nicht** sofort abgeschaltet, sondern nach dem Druckende (Prüfung alle 30 s über
`[delayed_gcode _PI_HEIZUNG_NACH_DRUCK_AUS]`), Meldung in der Konsole. Sofort aus: `TURN_OFF_HEATERS FORCE=1` oder den
Druck abbrechen. `END_PRINT` und `_PI_RESET_STATE` (läuft im Zustand printing bzw. nach Fehler) rufen
`TURN_OFF_HEATERS FORCE=1`. `G29` und `Z_AXIS_CALIBRATION` laufen nur außerhalb eines Drucks (`_PI_KEIN_DRUCK`).
Slicer-Dateien (Orca, Creality Print 7, eigener Generator geprüft) rufen `TURN_OFF_HEATERS` nicht auf.

`M104 S0`/`M140 S0` bzw. das Temperaturfeld in Mainsail (`SET_HEATER_TEMPERATURE`) bleiben **unverändert**: Slicer
nutzen sie legitim (Werkzeugwechsel, Druckende). Ein Schutz dort würde Dateien brechen.

**Not-Aus** (`M112`/Moonraker `printer/emergency_stop`) lässt sich in Klipper nicht abfangen. Mainsail 2.17 hat eine
Bestätigungsabfrage: Einstellung `uiSettings.confirmOnEmergencyStop` (Moonraker-Datenbank, Namespace `mainsail`;
im Menü Einstellungen → Allgemein). Setzen per API (nicht automatisch ausgeführt):

```
curl -X POST http://localhost:7125/server/database/item \
  -H "Content-Type: application/json" \
  -d '{"namespace": "mainsail", "key": "uiSettings.confirmOnEmergencyStop", "value": true}'
```

Analog `uiSettings.confirmOnCancelJob` (Abfrage vor „Druck abbrechen“). Offene Mainsail-Seiten danach neu laden.

## 7. Deploy

1. `k2_resume.py` nach `~/klipper/klippy/extras/`.
2. `config-pi/macros.cfg` und `config-pi/homing.cfg` auf den Pi; `[k2_resume]` in die Pi-`printer.cfg` (vor dem
   `#*#`-Block).
3. `sudo systemctl restart klipper` (neues Modul). Bett sackt ab → Nutzer vorher informieren; ggf. bei key798
   `FIRMWARE_RESTART`.
4. Prüfen: `KTWO_RESUME_INFO FILE=<datei>`; `TURN_OFF_HEATERS` außerhalb eines Drucks schaltet sofort ab.
