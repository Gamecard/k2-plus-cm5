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
2. Nachsehen, was gespeichert ist: `KTWO_RESUME_STAND` (Datei, Grund, Schicht, Kopfposition, wie lange her).
3. Datei erzeugen: **`PRINT_FROM_LAYER`** ohne Parameter. Das nimmt den letzten Abbruch und steigt an der
   **genauen Stelle** ein, an der der Kopf stand (Abschnitt 2a). Die Datei heißt `<name>_ab_schicht_<n>_genau.gcode`.
   - Nur wenn das nicht geht (kein gespeicherter Stand, andere Datei): `PRINT_FROM_LAYER FILE=<datei> LAYER=<n>`
     (bzw. `Z=<mm>`). Das ist der alte Einstieg **am Schichtanfang** – der schon gedruckte Teil dieser Schicht wird
     dabei noch einmal überfahren; die Meldung warnt davor.
4. Meldung lesen (Modus, Fahrhöhe, genaue Stelle, ggf. Antastpunkt und **WARNUNG**), dann die neue Datei in Mainsail
   drucken (oder gleich `START=1`).
5. Wird die Fortsetzung nicht gebraucht: `KTWO_RESUME_VERWERFEN` (vergisst den Stand, schaltet das warm gehaltene Bett
   aus).

## 2a. Druckstand und genaue Stelle (23.11.2026)

**Anlass:** Am 23.11.2026 brach ein Druck (PLA, 425 Schichten) in Schicht 24 durch einen Klick auf den Not-Aus ab.
Nach dem Neustart kannte Klipper die Abbruchstelle nicht mehr; die Fortsetzung begann deshalb am **Anfang** von
Schicht 24. Davon waren aber schon 59 % gedruckt: Die Düse fuhr in Schichthöhe durch den vorhandenen Kunststoff, dazu
war das Bett inzwischen auf 38 °C abgekühlt – das Teil wurde zerstört. Daraus folgen vier Änderungen:

**A – Druckstand mitschreiben.** Während eines Drucks misst `k2_resume` alle 0,5 s

- den **Lesestand** der Datei (`virtual_sdcard.file_position` – wie weit Klipper die Datei gelesen hat) und
- die **gemessene Kopfposition** (`motion_report.live_position` – wo der Kopf in diesem Moment wirklich ist,
  umgerechnet in Datei-Koordinaten, also ohne den Z-Versatz aus `SET_GCODE_OFFSET`).

Alle 2 s (`state_interval`) sowie bei Pause, Abbruch, Ende und Not-Aus schreibt es das nach
`config/k2/druckstand.json` (atomar, mit `fsync`, damit auch ein Stromausfall eine lesbare Datei hinterlässt).

*Warum zwei Werte?* Klipper **liest voraus**: Der Lesestand liegt bis zu etwa 2 s vor der Zeile, die der Kopf gerade
abfährt. Aus dem Lesestand allein lässt sich die ausgeführte Zeile also nicht bestimmen. Gespeichert wird deshalb ein
**Suchfenster** – Lesestand jetzt (`pos_hi`) und Lesestand vor 3 s (`pos_lo`) – plus die echte Kopfposition.

*Eingefroren:* Ein neuer Stand entsteht nur, solange die Datei weiterläuft. Steht sie (z. B. `END_PRINT` nach einem
Abbruch fährt zum Messer, Warten auf eine Heizung, CFS-Wechsel), bleibt der letzte Stand erhalten – die Kopfposition
in diesen Makros gehört nicht zur Datei.

Gründe im Stand: `laufend`, `pause`, `abgebrochen`, `fehler`, `fertig`, `notaus` (Not-Aus-Knopf bzw. `M112`),
`stoerung` (jede andere Abschaltung, z. B. Heizung oder MCU-Verbindung), `unterbrochen` (Klipper/Pi neu gestartet,
während der Druck lief – Stromausfall, Absturz), `verworfen`. Fortsetzen geht bei allen außer `laufend`, `fertig`,
`beendet` und `verworfen`.

**B – Einstieg an der genauen Stelle.** `PRINT_FROM_LAYER` sucht im Suchfenster die Zeile (`G0`–`G3`), deren Bahn
durch die gemessene Kopfposition läuft (höchstens 0,5 mm daneben; über 2 mm gilt die Stelle als nicht gefunden).
Kreuzt sich die Bahn im Fenster, gilt der **früheste** Treffer: lieber ein kurzes Stück doppelt als eine Lücke. Die
neue Datei beginnt genau an dieser Zeile. Der Vorspann stellt dafür zusätzlich her:

- **Rückzug:** Nach dem Vordrucken am Schacht ist das Filament 0,8 mm zurückgezogen. Die Datei erwartet an der Stelle
  ihren eigenen Rückzugsstand (0 mitten in einer Bahn, z. B. 0,8 mm in einer Leerfahrt). Der Unterschied wird mit
  einem `G1 E…` ausgeglichen.
- **Absolute E-Werte** (`M82`): Zähler mit `G92 E<wert>` auf den Stand der Datei.
- **Objekt:** Lag die Stelle in einem Objekt, öffnet der Vorspann es wieder (`EXCLUDE_OBJECT_START NAME=…`) –
  sonst meldet Klipper beim nächsten `EXCLUDE_OBJECT_END` „does not match“.
- Der Kopf fährt auf sicherer Höhe zum **Anfang** dieser Bahn und senkt sich erst dort auf ihre Höhe ab.

Doppelt gefahren wird so höchstens die eine Bahn, auf der der Kopf beim Abbruch war. Nach einem Stromausfall ist der
Stand bis zu 2 s alt – dann wird entsprechend etwas mehr doppelt gefahren.

**C – Bett warm halten.** Nach **Abbrechen** schaltet `END_PRINT` die Heizungen aus und stellt danach das Bett wieder
auf seinen Sollwert (`KTWO_RESUME_BETT_HALTEN`) – für `bed_hold_min` Minuten (Standard 60), damit das Teil haftet.
Danach geht das Bett von selbst aus (nicht, wenn inzwischen ein neuer Druck läuft). Nach einem **Not-Aus** ist alles
aus; startet Klipper danach neu, heizt `k2_resume` das Bett für die restliche Haltezeit wieder – **nur** wenn der
Grund der Not-Aus-Knopf war, **nie** nach einer Störung (Heizung, Verbindung …). Die Düse bleibt immer aus. Sofort
aus: `KTWO_RESUME_VERWERFEN` oder `TURN_OFF_HEATERS`.

**D – Pause bleibt Pause.** `k2_abbruch` behandelt eine Pause während der Druckvorbereitung als Abbruch. Die
Vorbereitung endete bisher nur in `START_PRINT` – eine Fortsetzungsdatei ruft das nicht auf, also wurde dort **jede**
Pause zum Abbruch (live 23.11.2026). Der Vorspann meldet jetzt am Ende `_PI_ABBRUCH_PUNKT AKTION=START_FERTIG`.

**E – Abfragen in Mainsail.** Not-Aus und „Druck abbrechen“ fragen nach (`uiSettings.confirmOnEmergencyStop`,
`uiSettings.confirmOnCancelJob`; `install.sh` setzt beide, s. Abschnitt 6).

**Filamentende (25.11.2026).** Anlass: Nach einem Filamentende steckte der Nutzer neues Filament ein und drückte
dreimal „Fortsetzen“ – das Filament war aber nicht bis in den Kopf geladen, der Drucker druckte drei Schichten Luft.
Klipper meldet ein Filamentende nur im Moment, in dem der Sensor von „voll“ auf „leer“ wechselt; ist der Kopf beim
Fortsetzen schon leer, kommt keine neue Meldung. Dagegen jetzt drei Teile:

- **A – Fortsetzen lädt selbst** (`k2_box`, Option `runout_resume_load`, Standard an): Meldet der Kopfsensor beim
  `RESUME` LEER, lädt der Drucker das Filament aus dem Fach des Filamentendes bis in den Kopf (Kette wie `T<n>`:
  Schacht, Einzug, Spülen) und setzt erst dann fort. Geht das nicht (Fach noch leer, Quelle EXTERN, keine Freigabe),
  bleibt der Druck pausiert – mit Meldung. Beim Filamentende öffnet sich in Mainsail und auf der Anzeige ein Fenster
  mit den drei Schritten (Spule einsetzen, Filament einziehen lassen, dann „Fortsetzen“) und einem Fortsetzen-Knopf.
- **B – Luftdruck-Wächter** (`head_empty_pause_mm`, Standard 20 mm, 0 = aus): Meldet der Kopfsensor im laufenden Druck
  LEER und hat der Extruder seitdem 20 mm gefördert, pausiert der Drucker. Ausgenommen: Pause, CFS-Wechsel
  (`chain_active`) und Druckvorbereitung (`k2_abbruch`).
- **C – Stand bleibt an der letzten Stelle mit Filament:** Solange der Kopfsensor LEER meldet, entsteht kein neuer
  Druckstand. Pausiert der Druck bei leerem Kopf, heißt der Grund `filamentende`. Auch nach Luftdrucken setzt
  `PRINT_FROM_LAYER` an der richtigen Stelle an.

**Grenze:** Nach einem Not-Aus ist die Z-Referenz verloren (Motoren stromlos, das Bett sackt ab). Die Fortsetzung tastet
dann neben dem Teil neu an; kann sie das Bett nicht ausrichten (`TILT=0`), bleibt ein Höhenfehler von bis zu etwa
±0,2 mm über dem Teil. Am genauesten ist eine Fortsetzung nach **Abbrechen** (Motoren bleiben an, Modus `keep`).

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
- Ohne gespeicherten Druckstand (z. B. Druck vor dem 23.11.2026 gestartet) geht nur der Einstieg am Schichtanfang;
  dabei wird ein Teil der unterbrochenen Schicht doppelt gedruckt (Düse fährt in Schichthöhe über das Teilstück).
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
Seit 23.11.2026 setzt `install.sh` beide (nur wenn sie noch nie gesetzt waren; abschaltbar mit
`--ohne-notaus-abfrage`), vorher geschah das nicht automatisch – der Not-Aus-Klick vom 23.11.2026 lief deshalb ohne
Abfrage durch.

## 7. Deploy

1. `k2_resume.py` nach `~/klipper/klippy/extras/`.
2. `config-pi/macros.cfg` und `config-pi/homing.cfg` auf den Pi; `[k2_resume]` in die Pi-`printer.cfg` (vor dem
   `#*#`-Block).
3. `sudo systemctl restart klipper` (neues Modul). Bett sackt ab → Nutzer vorher informieren; ggf. bei key798
   `FIRMWARE_RESTART`.
4. Prüfen: `KTWO_RESUME_INFO FILE=<datei>`; `TURN_OFF_HEATERS` außerhalb eines Drucks schaltet sofort ab.
