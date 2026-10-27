# Kalibrierung des K2 Plus mit Pi-Host

Stand der Quellen: 26.10.2026.

Diese Anleitung gilt für den Creality K2 Plus, bei dem das Original-Mainboard (T113) durch einen
Raspberry Pi CM5 mit Upstream-Klipper und den Modulen `pi-host/k2_*.py` ersetzt ist. Die Firmware von
Haupt- und Düsenplatine, Closed-Loop-Motoren, CFS und Riemenspann-Modulen bleibt die originale.

Die Datei beschreibt jede Kalibrierung: wozu sie dient, welche Fehler sie behebt, wie sie abläuft,
welche Freigaben sie braucht und was schiefgehen kann. Die vollständige Befehlsreferenz steht in den
Modul-READMEs (Abschnitt 7).

**Kennzeichnung im Text**

| Kennzeichen | Bedeutung |
|---|---|
| ohne Zusatz | im Projekt belegt: Modulcode, Konfiguration oder dokumentierte Messung |
| **allgemein** | übliches Fachwissen (Klipper, 3D-Druck, Regelungstechnik), im Projekt nicht eigens nachgewiesen |
| **Annahme** | nicht belegt, Vermutung |
| **an einem Gerät gemessen** | Einzelmessung bei der Inbetriebnahme; ein Richtwert, nicht auf andere Drucker übertragbar |

## Inhalt

1. [Was „Kalibrieren“ bei diesem Drucker bedeutet](#1-was-kalibrieren-bei-diesem-drucker-bedeutet)
2. [Übersicht aller Kalibrierungen](#2-übersicht-aller-kalibrierungen)
3. [Empfohlene Reihenfolge](#3-empfohlene-reihenfolge)
4. [Die Kalibrierungen im Einzelnen](#4-die-kalibrierungen-im-einzelnen)
5. [Was man nicht oder nur im Ausnahmefall kalibriert](#5-was-man-nicht-oder-nur-im-ausnahmefall-kalibriert)
6. [Symptom → zuerst prüfen](#6-symptom--zuerst-prüfen)
7. [Belege](#7-belege)
8. [Begriffe erklärt](#8-begriffe-erklärt)

---

## 1. Was „Kalibrieren“ bei diesem Drucker bedeutet

### 1.1 Drei Arten von Arbeit

Unter „Kalibrierung“ fallen hier drei verschiedene Dinge. Die Unterscheidung ist wichtig, weil nur die
erste Art etwas dauerhaft ändert.

| Art | Was passiert | Beispiele |
|---|---|---|
| **Einmessen** | Eine Messfahrt oder Messreihe ermittelt einen Wert, der danach gespeichert wird | Bettnetz, PID, Input Shaper, Schneidposition, Riemenspannung |
| **Ausgleichen ohne Speichern** | Der Drucker stellt etwas physisch ein oder rechnet laufend nach, ohne einen Wert zu speichern | `Z_TILT_ADJUST` (Spindeln angleichen), Düsen-Temperaturkompensation |
| **Prüfen** | Messen ohne Änderung, als Vorbedingung oder zur Fehlersuche | `KTWO_PRTOUCH_READ`, `PROBE_ACCURACY`, `KTWO_BELT_TEST`, `KTWO_MOTOR_CAL_CHECK`, `HEATER_STABILITY_TEST`, `MEASURE_AXES_NOISE` |

Grundregel: **erst prüfen, dann kalibrieren.** Viele Symptome (Schleppfehler, Ruhetest-Abbrüche,
schiefe erste Schicht) haben eine mechanische oder thermische Ursache. Eine Kalibrierung überdeckt sie
dann nur oder speichert sogar einen falschen Wert.

### 1.2 Wo Kalibrierwerte liegen: im Gerät oder in Klipper

Ein Teil der Werte wurde im Werk ermittelt und liegt in den Geräten selbst. Der andere Teil gehört zur
Klipper-Konfiguration auf dem Pi.

| Speicherort | Werte | Wer schreibt | Bemerkung |
|---|---|---|---|
| **Flash jedes Closed-Loop-Motors** (X, Y, Z, Z1, Extruder) | Elektrowinkel-Versatz `elec_offset`, Phasenfolge `phase_order_invert`, Fehlerwinkel `elec_offset_err_deg`, dazu alle Motorparameter (77 IDs) | Werk; am Pi nur `KTWO_MOTOR_CAL_RUN`/`_RESTORE` (Standard gesperrt, §5.1) | bleibt bei Pi-Tausch, Neuinstallation und Rückweg zum Original erhalten |
| **Werks-Sicherungskopie im Motor** | dieselben drei Kalibrierwerte | Werk; laut Firmware-Analyse auch ein *bestandener* ELEC-Lauf | über den Bus **nicht lesbar** (README §13); die Motor-Firmware holt die Werte beim Einschalten von dort zurück, wenn die Hauptwerte unplausibel sind |
| **Encoder-Baustein im Motor** | Linearisierung des magnetischen Winkelgebers | Werk; nur ein ENCODER-Lauf (§5.1) | |
| **Flash der Riemenspann-Module** `mdlx`/`mdly` | Kennlinie des Kraftsensors (`idl_adc`, `full_adc`), Schieberposition | `KTWO_BELT_CALI` (Kennlinie, Werksthema), `KTWO_BELT_SET`/`BELT_TENSION` (Position) | README-k2_belt §1 |
| **SAVE_CONFIG-Block der `printer.cfg`** | `[k2_prtouch] z_offset`, Bettnetz-Profile, `[input_shaper]`, PID-Werte, `[k2_box] cut_pos_x`, Hochtemperatur-PID `[k2_heater_pid] high_pid_*` | `SAVE_CONFIG` | Klipper-seitig; beim erneuten Lauf von `install.sh` bleibt der Block erhalten |
| **Moduldateien** `~/printer_data/k2-motor-cal/` | Sicherungen der Motor-Kalibrierwerte, Zustandsdatei eines Kalibrierlaufs | `KTWO_MOTOR_CAL_*` | Ordner 700, Dateien 600, enthalten die Motor-Kennung |
| **nur zur Laufzeit** | Spindel-Angleichung nach `Z_TILT_ADJUST`, Netzprofil `adaptiv`, Bezugstemperatur der Temperaturkompensation, `SET_HEATER_PID` ohne `SAVE=1` | Klipper | geht bei jedem Neustart bzw. Stromlos-Zustand verloren |
| **Konfigurationsdateien** (von Hand) | Antast-Parameter des Kraftsensors, Koeffizienten der Temperaturkompensation, Soll-Riemenkraft, Regelparameter der Motoren | Stock-Werte, nicht kalibrieren (§5.3) | |

Der Pi liest die Werte in den Geräten, schreibt sie im Normalbetrieb aber nicht: Der Sendeschutz in
`k2_transport` und im RS485-Treiber sperrt die Kalibrier-Befehle der Motoren und lässt an die
Riemenmodule nur die bekannten Lese-, Schreib- und Fahrbefehle durch (README §14, README-k2_belt §4a).

**Übernahme vom Original:** `install.sh --stock-printer-cfg` überträgt die am Original-Drucker
gemessenen Werte aus dessen SAVE_CONFIG-Block: Schneidposition (`[box] cut_pos_x` → `[k2_box]`),
`[input_shaper]` und Bettnetz-Profile. Geräte-Kennungen (`[auto_addr]`) werden nie übernommen,
Creality-eigene Heizungs- und Kraftsensor-Werte ebenfalls nicht (config-pi/README.md §2.1).

### 1.3 `SAVE_CONFIG` hat hier Nebenwirkungen

`SAVE_CONFIG` startet Klipper neu. Beim K2 Plus hat das Folgen:

- **Alle Achsmotoren werden stromlos.** X, Y, Z und Z1 teilen sich die Enable-Leitung `!PA9`
  (README §14). Das schwere Bett treibt die Z-Spindeln mit 8 mm Steigung rückwärts an und sinkt nach
  unten (macros.cfg, Kommentar zu `INPUTSHAPER`). An einem Gerät gemessen: in weniger als 20 s ganz
  nach unten, links und rechts ungleich. Damit sind die Z-Referenz und die Spindel-Angleichung verloren.
- **Danach** entweder `Z_AXIS_CALIBRATION` ausführen, oder `START_PRINT` gleicht beim nächsten Druck
  selbst aus (`_PI_ZTILT_WENN_NOETIG`, §4.4).
- **Steht das Bett nicht unten,** fällt es ungebremst und unter Umständen einseitig. Nach einer
  Input-Shaper-Messung auf Z 175 trat beim nächsten Antasten ein Schleppfehler (`key789`) an Z und Z1
  auf (an einem Gerät beobachtet). Achtung Richtung: kleines Z = Bett oben an der Düse, großes Z = Bett
  unten. `INPUTSHAPER` und `G29` fahren deshalb am Ende ganz nach unten (`max_z − 5`, Z 345); vor
  jedem anderen Neustart von Hand ebenso (`G1 Z345 F600`).
- **Upstream verweigert `SAVE_CONFIG`** für Werte, die schon in einer Include-Datei stehen („conflicts
  with included value“). Deshalb steht `[k2_prtouch]` (wegen `z_offset`) in `printer.cfg`, `cut_pos_x`
  steht nicht in `k2-module.cfg`, und `high_pid_*` werden nicht von Hand in `[k2_heater_pid]`
  eingetragen.
- Nach einem reinen Neustart des Klipper-Dienstes kann der erste Motor-Init `key798` für den Extruder
  melden. `FIRMWARE_RESTART` behebt das; der Start-Wächter macht es automatisch (README §15).

Folgerung: Kalibrierungen, die mit `SAVE_CONFIG` enden, sammelt man am besten vor der Z-Ausrichtung
und dem Bettnetz oder richtet danach neu aus (§3).

### 1.4 Freigaben (Schutzschalter)

**Worum es geht.** Der Pi steuert hier Hardware, die er erst nach und nach kennengelernt hat: Motoren,
CFS-Box, Riemenmodule, Kraftsensor. Damit bei der Inbetriebnahme oder nach einem Fehler nichts
unbeabsichtigt fährt oder Werte in den Geräten überschreibt, haben die Module **Schutzschalter**. Ein
solcher Schalter heißt hier **Freigabe**. Es ist eine Zeile in einer Konfigurationsdatei, die entweder
`True` (erlaubt) oder `False` (verboten) ist, zum Beispiel in `k2-motor.cfg`:

```
[k2_motor]
allow_write: True
```

Es gibt zwei Arten von Freigaben:

- **`allow_move`** („darf fahren“) erlaubt Bewegungen: Kopf, Bett, Extruder.
- **`allow_write`** („darf schreiben“) erlaubt Befehle, die in einem Gerät etwas verändern: Motoren
  einstellen, die CFS-Box laden oder entladen, Werte dauerhaft im Speicher eines Riemenmoduls ablegen.
  Rein lesende Befehle (Status, Temperatur, Messwerte abfragen) funktionieren immer, auch ohne Freigabe.

**Was passiert, wenn eine Freigabe aus ist.** Je nach Befehl eines von drei Dingen, immer **bevor**
sich etwas bewegt oder etwas gesendet wird:

1. **Abbruch mit Fehlermeldung.** Der Befehl tut gar nichts und meldet in der Konsole, welche Freigabe
   fehlt. Beispiel: `_PI_FREIGABE allow_move` steht auf `False`, du tippst `G28`. Dann erscheint
   `!! G28 gesperrt: _PI_FREIGABE allow_move=False (README §4). Keine Bewegung.` und der Drucker
   rührt sich nicht.
2. **Nur „Plan“ statt Ausführung** (Probelauf). Der Befehl läuft durch, sendet aber nichts an das Gerät,
   sondern schreibt in die Konsole, **was er senden würde**. Beispiel bei der CFS-Box mit
   `allow_write: False`: `[k2_box] PLAN Box RETRUDE (allow_write: False – nichts gesendet): 0x08 [01]
   Hub -> 0x04 [00 01] -> …`. So kann man einen Ablauf gefahrlos nachlesen. Manche Befehle bieten
   denselben Probelauf auch bei erteilter Freigabe mit dem Zusatz `DRY_RUN=1` an (z. B.
   `KTWO_BELT_SET … DRY_RUN=1`).
3. **Nur Hinweis, Rest läuft.** Bei Nebensachen wird nur der freigabepflichtige Teil ausgelassen. Ein
   Beispiel ist die Düsenreinigung vor dem Antasten: Ohne `[k2_box] allow_move` kommt eine Meldung, das
   Antasten selbst läuft trotzdem.

**Wie man eine Freigabe ändert.** In der genannten Datei den Wert ändern (`True`/`False`) und Klipper
neu starten (`FIRMWARE_RESTART`, vorher das Bett nach unten fahren, §1.3). Nur `allow_move` der
zentralen Bewegungsfreigabe lässt sich auch ohne Neustart einschalten: mit dem Makro `ALLOW_MOTION`
(gilt bis zum nächsten Neustart).

**Stand an diesem Drucker.** Nach der Inbetriebnahme sind alle Freigaben für den normalen Betrieb an
(Drucken, Homing, Antasten, CFS-Wechsel, Riemenspannen). Bewusst **aus** sind nur die drei Freigaben
für die Motor-Kalibrierung (`allow_motor_calibration`, `allow_encoder_calibration`,
`allow_z_calibration`), weil die im Ausnahmefall gebraucht wird und Schaden anrichten kann (§5.1). Im
Alltag merkt man von den Freigaben deshalb nichts.

**Welche Freigabe wofür:**

| Schalter | Datei | wirkt auf |
|---|---|---|
| `_PI_FREIGABE variable_allow_move` (Laufzeit: `ALLOW_MOTION`) | `homing.cfg` | jede Fahrt: `G28`, Schneidpositions-Kalibrierung, Motor-Kalibrierung, Lichtschranken-Ausrichtung |
| `[k2_motor] allow_write` | `k2-motor.cfg` | Motor-Init, X/Y-Homing (Stall-Modus), Schneidposition, Fehler löschen |
| `[k2_motor] allow_motor_calibration`, `allow_encoder_calibration`, `allow_z_calibration` | `k2-motor.cfg` | nur Motor-Kalibrierung (§5.1), **Standard aus** |
| `[k2_prtouch] allow_move` | `printer.cfg` | jedes Antasten (`PROBE`, `G28 Z`, `Z_TILT_ADJUST`, Bettnetz) |
| `[k2_belt mdlx/mdly] allow_write`, `allow_move` | `k2-module.cfg` | Flash der Riemenmodule, Schieber, Rüttel-Zyklus |
| `[k2_box] allow_move` | `k2-module.cfg` | Düsenreinigung vor dem Antasten (ohne Freigabe nur Meldung, das Antasten läuft trotzdem) |
| `[k2_zalign] allow_move` | `zalign.cfg` | Lichtschranken-Ausrichtung (Abschnitt auskommentiert) |

Die Lieferwerte stehen in config-pi/README.md §1. `install.sh` übernimmt von Hand gesetzte Schalter
bei jedem weiteren Lauf.

---

## 2. Übersicht aller Kalibrierungen

Dauerangaben ohne Zusatz stammen aus Konfiguration oder Code; „Gerät“ heißt an einem Gerät gemessen.

| Kalibrierung | Befehl / Makro | stellt ein | typische Symptome ohne | wann nötig | Dauer | Bewegung | Risiko |
|---|---|---|---|---|---|---|---|
| Motor-Werte sichern/prüfen (§4.1) | `KTWO_MOTOR_CAL_BACKUP`, `KTWO_MOTOR_CAL_CHECK` | nichts, nur Sicherungsdatei | keine (Diagnose) | einmal als Referenz; bei Motorfehlern zum Vergleich | 14 s / 13 s für alle 5 Motoren (Gerät) | nein | gering |
| Motor-Schutz prüfen (§4.1) | automatisch nach jedem Motor-Init | nichts | Motor ohne Überstrom-/Schleppfehler-Schutz, ohne Meldung | nach jedem Einschalten | – | nein | – |
| Schneidposition (§4.2) | `CALIBRATE_CUT_POS [SAVE=1]` | `[k2_box] cut_pos_x`, damit auch X-min | Filament nicht sauber getrennt, Rückzug in die Box klemmt (**Annahme**) | Erstinbetriebnahme, nach Arbeiten am Kopf oder Schneidhebel (**Annahme**) | Original ≈ 60 s; am Pi nicht gemessen | ja, X/Y | mittel |
| Riemenspannung (§4.3) | `BELT_TENSION [AXES=XY\|X\|Y]` | Schieberposition im Riemenmodul | Kraft außerhalb 119–161 N; **allgemein**: Nachschwingen, Maßabweichungen | wenn `KTWO_BELT_TEST` außerhalb liegt, nach Riemenwechsel | ≈ 30 s je Achse plus Homing (Gerät) | ja, X/Y ±200 mm **ohne Endschalterschutz** | mittel |
| Z-Ausrichtung (§4.4) | `Z_AXIS_CALIBRATION`, `Z_TILT_ADJUST`; automatisch in `START_PRINT` | Höhe Spindel Z gegen Z1 (flüchtig) | erste Schicht links und rechts verschieden, Düse schleift einseitig | nach jedem Stromlos-Zustand | Homing + 2 Messrunden | ja, Z | mittel, Kollision ab ≈ 11,5 mm Schräglage |
| Kraftsensor-Taster / Z-Nullpunkt (§4.5) | Prüfungen `KTWO_PRTOUCH_READ`, `PROBE_ACCURACY`; optional `PROBE_CALIBRATE`, `Z_OFFSET_APPLY_PROBE` | `z_offset` (Standard 0) | erste Schicht überall zu hoch/zu tief, Ruhetest-Abbrüche | `z_offset` normalerweise nie; Prüfung nach Arbeiten an der Düse | Sekunden | `PROBE_ACCURACY`: ja, Z | gering |
| Bettnetz (§4.6) | `G29`; in `START_PRINT` nur der Druckbereich | Höhenkarte `default` bzw. `adaptiv` | erste Schicht stellenweise zu dünn oder zu dick | nach der Inbetriebnahme, nach Arbeiten am Bett | `G29` ≈ 6 min (Gerät) | ja | gering |
| Düsen-Temperaturkompensation (§4.7) | keine Messfahrt; `KTWO_NOZZLE_TEMPCOMP` | Z-Zuschlag im Druck (gerechnet) | erste Schicht bei hoher Düsentemperatur zu stark angedrückt (**Annahme**) | nie (Stock-Koeffizienten) | – | nein | gering |
| PID Düse (§4.8) | `NOZZLE_PID [TEMP=220] [FAN=0]`, `SET_HEATER_PID`, `HEATER_STABILITY_TEST` | `pid_Kp/Ki/Kd` des Extruders | Temperatur pendelt, `M109` wird nicht fertig, Überschwingen | nach Teiletausch am Hotend, bei unruhiger Temperatur | ≈ 2 min + 120 s Test (Gerät) | nein (Kopf ggf. über den Abfallschacht) | gering |
| PID Hochtemperatur (§4.8) | `NOZZLE_PID_HIGH [TEMP=300]` | `high_pid_*` in `[k2_heater_pid]` | Pendeln ab 280 °C | nur wenn über 280 °C gedruckt wird | ≈ 2 min | nein | mittel (nur mit leerer Düse) |
| PID Bett (§4.8) | `BEDPID [TEMP=100]` | `pid_Kp/Ki/Kd` des Betts | Betttemperatur pendelt | bei unruhiger Temperatur, nach Teiletausch | ≈ 10 min | nein | gering |
| Input Shaper (§4.9) | `INPUTSHAPER [ACHSE=X\|Y] [SPEICHERN=1]` | `shaper_type`/`shaper_freq` X und Y | Nachschwingen (Ringing) an Kanten | nach der Inbetriebnahme, nach Riemenspannen oder Umbau | 307 s inkl. Homing (Gerät) | ja, laut | gering |
| Motor-Elektrowinkel (§5.1) | `KTWO_MOTOR_CAL_RUN … MODE=ELEC` | `elec_offset`, `phase_order_invert` im Motor-Flash | **laut Firmware-Analyse**: Drehmomentverlust, Schwingen, Überstrom, Schleppfehler | nur Ausnahmefall; **Standard gesperrt** | bis 45 s (Gerät: 15,3 s) | ja, open-loop **ohne Schutz** | hoch |
| Encoder (§5.1) | `KTWO_MOTOR_CAL_RUN … MODE=ENCODER` | Encoder-Baustein, danach ELEC Pflicht | wie oben | nur Ausnahmefall; **Standard gesperrt** | bis 120 s | ja, bis ≈ 63 Umdrehungen **ohne Schutz** | sehr hoch |
| Kraftsensor der Riemenmodule (§5.2) | `KTWO_BELT_CALI DIR=0\|1\|2` | Kennlinie im Modul-Flash | `key714/715` (nicht kalibriert) | Werksthema, nur mit definierter Prüfkraft | – | nein | hoch (überschreibt Kennlinie) |
| Lichtschranken-Ausrichtung (§5.4) | `KTWO_ZALIGN`, `ZALIGN_STOCK` | – (Ausrichtung auf Lichtschranken) | – | nur wenn `Z_TILT_ADJUST` nicht reicht; Modul gesperrt und am Gerät ungetestet | – | ja | hoch |

Die Kammerheizung hat keine PID-Regelung (`control: watermark`); dort gibt es nichts zu kalibrieren
(§4.8).

---

## 3. Empfohlene Reihenfolge

Bei der Erstinbetriebnahme oder nach einem Umbau. Jeder Schritt nur, wenn der vorige bestanden ist;
nach jedem Schritt `klippy.log` bzw. die Konsole prüfen. Hand am Netzschalter, sobald sich etwas
bewegen kann.

| # | Schritt | Begründung der Stelle |
|---|---|---|
| 0 | Motor-Init fertig, `protect_en` aller Motoren ≠ 0, einmal `KTWO_MOTOR_CAL_BACKUP MOTOR=all` | Ohne Init antworten die Motoren nicht. Die Sicherung hält den Werkszustand fest, bevor irgendetwas verändert wird (nur lesend). |
| 1 | X/Y-Homing funktioniert (`G28 X Y`) | Grundlage für alle folgenden Fahrten; Schneidposition und Riemenspannen brauchen referenziertes X/Y. |
| 2 | Riemen prüfen (`KTWO_BELT_TEST`), bei Bedarf `BELT_TENSION` | Mechanik zuerst. **Allgemein:** Die Riemenkraft bestimmt die Steifigkeit und damit die Resonanzfrequenzen; Input Shaper deshalb erst danach. `BELT_TENSION` endet mit `M84` – auch Z wird stromlos, eine vorherige Z-Ausrichtung wäre verloren. |
| 3 | Schneidposition `CALIBRATE_CUT_POS SAVE=1` (ohne Filament im Kopf) | Bewegt nur X/Y. Das Ergebnis setzt zugleich die X-Untergrenze aller Fahrten (`[k2_limits]`). Endet mit `SAVE_CONFIG` → Neustart, deshalb vor der Z-Ausrichtung. |
| 4 | Kraftsensor prüfen: `KTWO_PRTOUCH_READ`, erste Antastung, `PROBE_ACCURACY`, vollständiges `G28` | Alle Z-Kalibrierungen messen mit der Düse. Erst muss feststehen, dass der Taster sauber auslöst und reproduzierbar misst. |
| 5 | Düsen- und Bett-PID (`NOZZLE_PID`, prüfen, `SAVE=1`; `BEDPID`), dann `SAVE_CONFIG` | Das Antasten verlangt eine ruhige Düsentemperatur: Wärmedrift lässt den Ruhetest des Kraftsensors abbrechen (homing.cfg `_PI_DUESE_STABIL`). `G29` wartet zudem mit `M109`/`M190`. Endet mit `SAVE_CONFIG`. |
| 6 | Z-Ausrichtung `Z_AXIS_CALIBRATION` | Nach den Neustarts der Schritte 3 und 5 ist das Bett abgesackt. Das Netz setzt ein angeglichenes Bett voraus. |
| 7 | Bettnetz `G29`, dann `SAVE_CONFIG` | `G29` enthält selbst `Z_TILT_ADJUST` und ein neues `G28 Z`. |
| 8 | Input Shaper `INPUTSHAPER SPEICHERN=1` | Nach dem Riemenspannen (Schritt 2). Vom Bettnetz unabhängig; endet mit `SAVE_CONFIG`. |
| 9 | `Z_AXIS_CALIBRATION` oder direkt drucken | `START_PRINT` gleicht nach einem Stromlos-Zustand selbst aus. |
| 10 | Erste Schicht beobachten | Hier zeigt sich die Düsen-Temperaturkompensation (bei 220 °C +0,050 mm gegenüber „aus“). Erst wenn sie dauerhaft zu hoch oder zu tief ist, über den Z-Nullpunkt nachdenken (§4.5). |

Bei der dokumentierten Inbetriebnahme lief die Reihenfolge anders: Schneidposition → Kraftsensor →
Z-Ausrichtung → PID → Bettnetz → Input Shaper → Riemen. Die Riemen lagen dort schon im Sollbereich und
wurden nur nachgezogen. Die Reihenfolge oben ist die aus den Abhängigkeiten begründete.

---

## 4. Die Kalibrierungen im Einzelnen

### 4.1 Motor-Kalibrierwerte sichern und prüfen

**Zweck.** Den Kalibrierzustand der fünf Closed-Loop-Motoren festhalten und später vergleichen, ohne
irgendetwas zu verändern. Diese Befehle sind auch die Vorstufe für eine Motor-Kalibrierung im
Ausnahmefall (§5.1).

**Hintergrund.** Jeder Motor ist ein Antrieb mit eigenem Regler und magnetischem Winkelgeber
(Encoder, 32 768 Schritte je Umdrehung). Der Regler stellt den Strom so ein, dass das Magnetfeld im
Motor dem Rotor um eine feste elektrische Winkelstellung vorauseilt (**allgemein:** feldorientierte
Regelung). Dafür muss er wissen, bei welcher Encoder-Stellung der elektrische Winkel 0 liegt
(`elec_offset`) und in welcher Reihenfolge die Phasen angeschlossen sind (`phase_order_invert`). Laut
Firmware-Analyse hat der Motor 50 Polpaare: Eine elektrische Periode entspricht 1/50 Umdrehung,
also 655,36 Encoder-Schritten. Schon ein kleiner Fehler im Versatz kostet deshalb Drehmoment.

**Was gelesen wird** (README §13): Version, Regelungszustand, Fehlerbits, Flash-Prüfsumme, Messwerte
(Encoder-Stellung, Temperatur, Versorgungsspannung), alle 77 Parameter in RAM und Flash. Die
Kalibrierwerte im Einzelnen:

| ID | Name | Bedeutung |
|---|---|---|
| 9 | `elec_offset` | elektrischer Nullpunkt in Encoder-Schritten |
| 10 | `phase_order_invert` | Phasenfolge 0/1 |
| 25 | `elec_offset_err_deg` | Streuung der letzten Kalibrierung in elektrischen Grad |
| 8 | `elec_angle_calibration_standar_deg` | Grenzwinkel: darunter gilt eine Kalibrierung als bestanden |
| 20, 23 | `ud_cal_set`, `encoder_calibtate_official_ud_cal_set` | Spannung während der Kalibrierung |
| 21 | `motion_dir` | Drehrichtung |
| 7 | `singleturn_control_mode` | Regelmodus |

**Befehle.**

| Befehl | Wirkung |
|---|---|
| `KTWO_MOTOR_CAL_BACKUP MOTOR=x\|y\|z\|z1\|e\|all` | liest alles und schreibt `~/printer_data/k2-motor-cal/<motor>-JJJJMMTT-hhmmss.json` |
| `KTWO_MOTOR_CAL_CHECK MOTOR=… [BACKUP=<datei>]` | liest neu, bewertet und vergleicht mit der neuesten passenden Sicherung desselben Motors |

**Bewertung durch `CHECK`.**
- Plausibel wie die Startprüfung der Motor-Firmware: `phase_order_invert` 0 oder 1,
  |`elec_offset`| < 1310, 0 ≤ `elec_offset_err_deg` < 360.
- **ALARM** (G-Code-Fehler): unplausible Werte oder geändertes `phase_order_invert`.
- **Warnung:** sonstige geänderte Kalibrierwerte, RAM ≠ Flash, Fehlerbits, abweichende Prüfsumme,
  Lesefehler, Sicherung von einer anderen Motoreinheit.
- **Normal:** RAM ≠ Flash bei den Laufzeitwerten (Stall-/Notfallmodus ID 11/12, Homing-Verstärkung
  ID 70, `protect_en` ID 173) und bei Werten, die der Motor-Init bei jedem Start aus `k2-motor.cfg` in
  den RAM schreibt. An einem Gerät gemessen: ID 70/86/87/188 aus der Config, ID 11 = 2, ID 173 im RAM
  255, im Flash 1.

**Motor-Schutz `protect_en` (README §15).** Die Motor-Firmware setzt nach dem Einschalten den
Schalter `protect_en` (ID 173) über einen Kopiervorgang aus dem Encoder-Baustein. Bricht dieser
Transfer ab, bleibt er 0: Dann sind **alle Schutzfunktionen des Motors aus** (Überstrom, Schleppfehler,
Stall), ohne jede Meldung. Das gilt auch für die Original-Firmware. `[k2_motor]` liest den Wert nach
jedem Motor-Init (nur lesend):
- 0 → rote Meldung „Motor-Schutz AUS bei …“. Abhilfe: Drucker aus- und wieder einschalten.
  `FIRMWARE_RESTART` reicht nicht, weil die Motor-Firmware ihre Prüfung nur nach dem Einschalten
  ausführt.
- Sonst nur eine Logzeile; Werte in `KTWO_MOTOR_STATUS` und `printer.k2_motor.protect_en`.

**Voraussetzungen und Sperren.** Läuft auch mit `allow_write: False`. Gesperrt während Druck oder
Pause, während der Schneidpositions-Kalibrierung und vor abgeschlossenem Motor-Init (die Lesefolge
belegt den Bus einige Sekunden je Motor). Antwortet ein Motor nicht, bricht die Folge für diesen Motor
ab; die übrigen werden gesichert.

**Ergebnis.** Sicherungsdateien im Ordner `cal_backup_dir` (Standard `~/printer_data/k2-motor-cal`).
Die Motor-Kennung steht nur in der Datei, nicht in Konsole oder Log. Ausnahme: Beim Extruder kann sie
nach einem Shutdown direkt danach im `klippy.log` stehen (README §13).

**Risiken.** Keine Bewegung, kein Schreiben. Der Bus ist während der Lesefolge belegt
(Klipper-Hauptthread bis 1 s je Timeout).

**Stock ↔ Pi.** Das Original hat keine Sicherungsfunktion. Der Motor-Init liest beim Start nur die
IDs 9/10/25 und schreibt sie nie.

---

### 4.2 Schneidposition (`CALIBRATE_CUT_POS`)

**Zweck.** Den X-Wert finden, bei dem der Druckkopf den Schneidhebel am linken Rand sicher betätigt.
Der Kopf trennt das Filament, indem er den Hebel gegen einen Anschlag drückt (README-k2_box §8). Das
geschieht vor jedem Rückzug in die CFS-Box: beim Farbwechsel, beim Entladen und am Druckende.

**Hintergrund.** Die Closed-Loop-Motoren melden über eine eigene Leitung, wenn sie blockieren
(„Stall“): Der Strom überschreitet eine Schwelle oder der Lagefehler wird zu groß. Beim X/Y-Homing
dient diese Leitung als Endschalter (Stall-Modus 1); im Betrieb meldet sie Notfälle (Modus 2). Zum
Kalibrieren schaltet das Modul Motor x in **Modus 3** und erhöht die Positionsverstärkung (ID 70) von
100 auf 300, wie das Original. Dann tastet der Kopf den Hebel in 0,1-mm-Schritten ab, von X −5,0 bis
höchstens −9,5. Ab dem zweiten Treffer gilt:

`cut_pos_x = 2. Treffer + cut_pos_offset (0,4 mm)`

Beispiel aus einem Mitschnitt am Original: zweiter Treffer −8,2 → `cut_pos_x` = −7,80.

**Symptome und Fehler.**
- **Annahme:** Liegt `cut_pos_x` falsch, wird das Filament nicht sauber getrennt. Belegt ist der
  Folgefehler eines fehlenden Schnitts: Ein Rückzug ohne Schnitt nach dem Spülen klemmte live in der
  Box (Status 0x19, Rückzug blockiert länger als 10 s), weil das geschmolzene Ende eine Verdickung
  bildet (README-k2_box §8). Deshalb sperrt `k2_box` Rückzüge, bis wirklich geschnitten wurde.
- `cut_pos_x` legt zugleich die **X-Untergrenze im Betrieb** fest: `[k2_limits]` zieht `[stepper_x]`
  beim Start auf den aktuellen Wert nach (README-k2_limits §3). Ohne diese Grenze fuhr `G1 X-9` den
  Hebel über die kalibrierte Position hinaus.
- Meldungen der Kalibrierung: „kein Stall bis X=-9.50 … Abbruch nach max. Weg“, „nur ein Stall“,
  „Stall-Pin schon vor der Fahrt aktiv“, Ergebnis außerhalb −9,5…−5,5. Im Makro zusätzlich „Filament im
  Kopf erkannt – erst entladen“ (Stock `key854`).

**Wann nötig.** Bei der Erstinbetriebnahme, wenn der Wert nicht vom Original übernommen wurde.
**Annahme:** nach Arbeiten am Druckkopf, am Schneidhebel oder an der X-Mechanik. Nicht nötig ohne
Anlass: Der vom Original übernommene Wert (−7,80 an einem Gerät) bleibt gültig.

**Ablauf** (cutcal.cfg, README §10, config-pi/NOTIZ-cutcal.md):
1. Vorher: `KTWO_MOTOR_CUT_CAL DRY_RUN=1` zeigt den Plan (46 Anstöße, keine Bewegung, nichts gesendet).
2. `CALIBRATE_CUT_POS` prüft die Sperren, referenziert X/Y bei Bedarf (`G28 X Y`) und weitet die
   Fahrgrenzen auf X −10 (`KTWO_LIMITS_WIDE`).
3. `KTWO_MOTOR_CUT_CAL`: Beschleunigung ≤ 25 000, Kp 300, Kopf auf X 50 / Y 200, Stall-Modus 3.
   Jeder Anstoß ist eine Homing-Fahrt gegen die Stall-Leitung (PB11): Die MCU stoppt X/Y sofort beim
   Stall. Nach dem zweiten Treffer: Ausgang X 200, Wert vormerken.
4. Rückbau **immer**, auch bei Fehlern: Kopf weg vom Hebel, Modus 2, Kp 100, Beschleunigung zurück,
   Fahrgrenzen eng (`KTWO_LIMITS_RESTORE`).
5. Meldung mit beiden Treffern und Anzahl der Anstöße. `SAVE=1` ruft direkt `SAVE_CONFIG` auf.

**Voraussetzungen und Sperren.** `_PI_FREIGABE allow_move`, `[k2_motor] allow_write: True`,
X/Y-Homing funktioniert, kein laufendes Homing, kein Filament im Kopf. Harte Grenzen, die keine
Config-Option aufweicht: X nie unter −9,5 bzw. −10 bzw. `position_min`, höchstens 100 Anstöße,
Anstoß ≤ 200 mm/s.

**Ergebnis prüfen und speichern.** Plausibel ist ein Wert nahe am bisherigen (±0,1–0,2 mm,
config-pi/NOTIZ-cutcal.md §6). Speichern mit `SAVE_CONFIG` → `[k2_box] cut_pos_x` im SAVE_CONFIG-Block.
`k2-module.cfg` darf die Zeile `cut_pos_x:` nicht enthalten. Danach Neustart (§1.3).

**Risiken.** Bewegung an der linken Endlage. Der Stall-Pegel in Modus 3 ist am Gerät noch nicht
gesondert belegt (config-pi/NOTIZ-cutcal.md §7); das Modul bricht aber ab, wenn die Leitung schon vor
der Fahrt aktiv ist. Mit Filament im Kopf würde der Hebel das Filament während der Messung schneiden.

**Stock ↔ Pi.** Das Original misst mit einem eigenen Host-Latch und fährt jeden Anstoß bis zum Ziel.
Der Pi stoppt über die MCU sofort beim Stall. Kp, Stall-Modus und Beschleunigung stellt das Modul in
einem Befehl ein, damit der Rückbau garantiert läuft. Das Original speichert immer sofort (mit
Neustart); der Pi nur mit `SAVE=1`. Das Original referenziert alle Achsen, der Pi nur X/Y.

---

### 4.3 Riemenspannung (`BELT_TENSION`)

**Zweck.** Beide XY-Riemen auf die Soll-Kraft von 140 N bringen. Der K2 Plus hat je Riemen ein
Spannmodul (`mdlx`, `mdly`) mit Kraftsensor und kleinem Motor, der einen Spannschieber verfährt.

**Hintergrund.** Im Modul misst ein Dehnmessstreifen die Riemenkraft; ein 24-Bit-Wandler (CS1237)
liefert den Rohwert. Die Umrechnung in Newton macht der Host: Er legt eine Gerade durch zwei
Kalibrierpunkte aus dem Modul-Flash, `idl_adc` bei 140 N und `full_adc` bei 160 N
(README-k2_belt §1). An einem Gerät gemessen: rund −14 000 Wandlerschritte je Newton. Die Regelung
spannt nur (Richtung 1), Toleranz ±2 %, höchstens 200 Schritte. Danach folgt ein „Rüttel-Zyklus“:
Die Motoren X und Y fahren je ±200 mm, zweimal hintereinander.

**Allgemein:** Bei CoreXY bewegen beide Riemen gemeinsam den Kopf. Ungleiche oder zu geringe
Spannung macht die Kinematik weich und ungleichmäßig (Nachschwingen, Maß- und Winkelabweichungen); zu
hohe Spannung belastet Lager und Motoren. Den Zweck des Rüttel-Zyklus nennen die Quellen nicht.
**Annahme:** Er verteilt die Spannung über alle Riementrume, bevor die letzte Messung erfolgt.

**Symptome und Fehler.**
- `KTWO_BELT_TEST` meldet die Kraft außerhalb `ch_best_n · (1 ± ch_best_error)` = 119–161 N
  (`target_error: 1`).
- Hinweis „Riemen locker“ unter 50 N bzw. „Kraft < 0“. Der Pi rechnet den Wandlerwert mit Vorzeichen.
  Das Original deutet ihn ohne Vorzeichen und meldet bei lockerem Riemen eine Phantomkraft von rund
  3·10⁵ N mit `key716/717` (README-k2_belt §4a).
- Fehlercodes (gerade = mdlx, ungerade = mdly): `key712/713` Kommunikation, `key714/715` nicht
  kalibriert (Flash leer, §5.2), `key716/717` Kraft unplausibel (außerhalb −300…600 N), `key718/719`
  Regelung braucht mehr als 200 Schritte, `key720/721` Kalibrierwert unbrauchbar.

**Wann nötig.** Wenn `KTWO_BELT_TEST` außerhalb des Bereichs liegt, nach einem Riemenwechsel und vor
einer Input-Shaper-Messung. Nicht nötig, wenn beide Achsen im Bereich liegen. An einem Gerät lagen
beide Riemen schon im Bereich (vorher 136,9 N und 134,6 N, nach dem Spannen 137,4 N und 137,3 N).

**Ablauf** (macros.cfg `BELT_TENSION`):
1. Sperre im Druck; ist Z referenziert, fährt das Bett ganz nach unten (Z 345 – der Rüttel-Zyklus endet
   mit `M84`, danach fiele es sonst, §1.3); `BED_MESH_CLEAR`.
2. Je Achse: `G28 X Y`, Kopf auf X 225 / Y 345, `KTWO_BELT_INFO`, `KTWO_BELT_SET`, `KTWO_BELT_TEST`.
3. `KTWO_BELT_SET`: Regelung → 2 × (Rüttel-Zyklus + Regelung) → finale Regelung → Position in den
   Modul-Flash. Der Rüttel-Zyklus endet mit `M84`, deshalb vor der zweiten Achse erneut `G28 X Y`.

Von der Startposition X 225 / Y 345 bleiben die Rüttel-Fahrten in X 125–325 / Y 145–345. Sie laufen
**ohne Endschalterschutz** (`FORCE_MOVE`). Für einen eigenen Ablauf vorher den Plan ansehen:
`KTWO_BELT_SET MDL_NAME=mdlx DRY_RUN=1` gibt Ist-Kraft, Ziel, ersten Schritt und alle Frames aus,
ohne etwas zu senden.

**Voraussetzungen und Sperren.** `[k2_belt mdlx/mdly] allow_write` und `allow_move` (seit der
Inbetriebnahme `True` für dieses Makro), `[force_move] enable_force_move: True`, Bewegungsfreigabe,
Achsen frei.

**Ergebnis prüfen und speichern.** `KTWO_BELT_TEST` nach dem Spannen: Kraft im Bereich,
`target_error 0`. Die Position steht im Modul-Flash; Klipper speichert nichts.

**Risiken.**
- Rüttel-Fahrten ohne Endschalterschutz: Nur aus der vorgesehenen Startposition fahren.
- Die Regelung lockert nie. Ist ein Riemen zu straff, meldet sie nach einer Messung „fertig“ und
  ändert nichts (README-k2_belt §4). Lockern ginge nur mit `KTWO_BELT_MOVE` von Hand.
- Danach sind alle Motoren aus → Bett sackt ab → neu referenzieren und ausrichten (§1.3).
- Die Einheit der Schieberposition ist nicht belegt (1 µm oder 0,01 mm). Ein Fahrbefehl ist auf 50
  Einheiten begrenzt, weil die Modul-Firmware keine Endlagen kennt.

**Stock ↔ Pi.** Gleicher Rechenweg und gleiche Regelung wie das Original (`belt_mdl.py`). Abweichungen:
Wandlerwerte mit Vorzeichen; nach jeder Schieberfahrt wird die erste Messung verworfen; 5 ms Busruhe
vor jedem Modul-Befehl; strengere Antwortprüfung; ohne vorheriges Lesen wird nie geschrieben (das
Original konnte so die Kalibrierung mit `0xFFFFFFFF` überschreiben). Der Rüttel-Zyklus nutzt keine
Creality-Befehle. Das Original-Makro hieß ebenfalls `BELT_TENSION`.

---

### 4.4 Z-Ausrichtung (`Z_AXIS_CALIBRATION`, `Z_TILT_ADJUST`)

**Zweck.** Die beiden Z-Spindeln (stepper_z links, stepper_z1 rechts; Zuordnung laut zalign.cfg eine
**Annahme**, durch das Konvergieren von `Z_TILT_ADJUST` am Gerät plausibel) auf gleiche Höhe bringen,
sodass das Bett parallel zur Bewegungsebene der Düse steht.

**Hintergrund.** Das Bett hängt an zwei Spindeln mit je eigenem Motor. Werden die Motoren stromlos
(Einschalten, Neustart, `SAVE_CONFIG`, `M84`, Not-Aus), sinkt das Bett, und zwar links und rechts
unterschiedlich (§1.3). Das Original fängt das über zwei Gabellichtschranken unten an den Spindeln ab
(„ZDOWN“): Beim ersten `G28 Z` fährt die MCU beide Seiten einzeln auf ihre Schranke und richtet so
mechanisch aus (config-pi/NOTIZ-zalign.md §1). Der Pi nutzt die Lichtschranken nur lesend und richtet
mit `Z_TILT_ADJUST` aus: Die Düse tastet bei X 5 und X 345 (Y 175) an. Klipper rechnet daraus mit den
Spindelpositionen X −15 / 375 die Korrektur je Spindel und wiederholt, bis die Differenz ≤ 0,1 mm ist
(höchstens 10 Runden). Die Korrektur wird nicht gespeichert, sondern direkt gefahren.

**Symptome und Fehler.**
- Erste Schicht links und rechts verschieden: auf einer Seite gequetscht oder schleifend, auf der
  anderen haftet sie schlecht. Das gespeicherte Netz passt nicht mehr, weil es auf einem ausgerichteten
  Bett gemessen wurde (config-pi/NOTIZ-zalign.md §3).
- An einem Gerät gemessen: nach einem Neustart erste Runde 0,78 mm Differenz, zweite Runde 0,005 mm.
- Meldungen: „Retries aborting … increasing“ (Fehler wächst → Zuordnung oder Richtung falsch, STOP),
  „Too many retries“, kein Kontakt innerhalb `probe_max_travel_mm`.

**Grenzen** (config-pi/NOTIZ-zalign.md §3): Nach `G28 Z` in der Bettmitte fährt die Düse für die
Messpunkte auf Z 5 und tastet höchstens 8 mm tief.
- Spindeldifferenz unter ≈ 6,9 mm: sicher.
- ab ≈ 6,9 mm: Abbruch „kein Kontakt“ an der tieferen Seite (sicher, aber ohne Ergebnis).
- ab ≈ 11,5 mm: **Kollisionsgefahr** – die Düse fährt bei der XY-Fahrt auf Z 5 in die höhere Seite.

**Wann nötig.** Nach jedem Stromlos-Zustand. `START_PRINT` macht es automatisch, solange
`printer.z_tilt.applied` falsch ist (Makro `_PI_ZTILT_WENN_NOETIG`; Klipper setzt das Flag bei jedem
Start und bei `motor_off` zurück). Von Hand vor `G29` und nach jedem `SAVE_CONFIG`. Nicht nötig,
solange die Motoren seit der letzten Ausrichtung bestromt waren (`[idle_timeout]` schaltet sie wie im
Original praktisch nie ab).

**Ablauf** `Z_AXIS_CALIBRATION [BED_TEMP=50] [AUS=1]` (macros.cfg):
1. Sperre im Druck.
2. Warm (Standard): Bett auf `BED_TEMP`, Düse auf 140 °C.
3. `G28`, warten auf Temperatur, `Z_TILT_ADJUST`.
4. Warm: Düse am Silikonstreifen reinigen (nur mit `[k2_box] allow_move`).
5. `G28 Z` – die Z-Referenz kommt nach dem Angleichen neu aus der Bettmitte.
6. Warm: Heizungen aus (außer `AUS=0`).

Kalt: `Z_AXIS_CALIBRATION BED_TEMP=0`.

**Voraussetzungen.** Bewegungsfreigabe, `[k2_prtouch] allow_move: True`, X/Y-Homing. Vor der allerersten
Ausrichtung nach einem Umbau sieht config-pi/NOTIZ-zalign.md §5 (T3) eine Kollisions-Vorprüfung vor:
Abstand Düse–Bett links und rechts bei Z 15 messen, bei mehr als 4 mm Unterschied nicht ausrichten.

**Ergebnis prüfen.** Die letzte Runde meldet eine Spannweite ≤ 0,1 mm. Ein zweiter Aufruf sollte schon
in Runde 0 unter 0,1 liegen.

**Risiken.** Kollision bei sehr schiefem Bett (siehe Grenzen). Die Lage hält nur, solange die Motoren
bestromt sind.

**Stock ↔ Pi.** Das Original richtet beim ersten `G28 Z` nach dem Start über die Lichtschranken aus
(Restschräglage danach ≈ 0,065 mm) und führt `Z_TILT_ADJUST` in `G29` und `Z_AXIS_CALIBRATION` aus.
Der Pi verlässt sich allein auf `Z_TILT_ADJUST`, holt es aber in `START_PRINT` automatisch nach. Ein
Ersatz der Lichtschranken-Fahrt (`KTWO_ZALIGN`) liegt gesperrt bei (§5.4).

---

### 4.5 Kraftsensor-Taster (PrTouch) und Z-Nullpunkt

**Zweck.** Die Düse selbst ist der Taster: Ein Kraftsensor (Wägezelle mit Dehnmessstreifen) am
Düsenhalter merkt, wann die Düse das Bett berührt. Damit bestimmt der Drucker die Z-Referenz
(`G28 Z`), richtet das Bett aus und misst das Netz. Eine eigene Kalibrierung gibt es im Normalfall
nicht – der **Abstand Taster–Düse ist null** (`z_offset: 0.0`, wie im Original). Hier geht es darum,
dass der Taster sauber und reproduzierbar misst, und um den seltenen Fall, dass doch ein
`z_offset` nötig wird.

**Hintergrund** (README-k2_prtouch A.1, A.8):
- Ein 24-Bit-Wandler (CS1237) liefert etwa 1280 Messwerte je Sekunde (Abstand 0,78 ms) an die
  Düsen-MCU.
- Ein Hochpass entfernt langsame Drift; die Düse vergleicht mit dem Mittel von vor 37–49 ms.
- Steigt die gefilterte Kraft über die Auslöseschwelle, zieht die Düsen-MCU die Leitung PA15 auf 0.
  Mit den Stock-Werten (`pres_tri_hold` 4000/10 000/500) löst sie bei drei nicht fallenden Werten
  ab 4000–5000 Zählwerten aus, unabhängig vom Vorzeichen (README-k2_prtouch B.2).
- Die Haupt-MCU sieht das an PC7 und stoppt Z sofort (normaler Klipper-Endschalter mit `trsync`).
- Nach dem Scharfschalten gilt eine Totzeit von 128 Messwerten. Deshalb wartet das Modul 0,2 s
  (Pflicht ≥ 0,15 s).
- Der Kraftanstieg muss steil genug sein, sonst löst die Düse nie aus. Daher die Antastgeschwindigkeit
  2,5–5 mm/s (Untergrenze **Annahme** aus dem Firmware-Modell).
- Laut Modell beträgt der Überfahrweg nach dem Kontakt höchstens ≈ 51 µm.

**Warum Temperatur, Sauberkeit und Lüfter zählen.**
- **Wärmedrift:** Heizt die Düse beim Antasten noch, wandert der Dehnmessstreifen thermisch, und der
  Ruhetest bricht ohne Bewegung ab. `_PI_DUESE_STABIL` wartet deshalb vor jedem `G28 Z` auf die
  Solltemperatur (homing.cfg).
- **Antasttemperatur 140 °C** (`g28_ext_temp`): warm genug für den Betrieb, kühl genug, dass nichts
  tropft. Über 150 °C kommt eine Warnung (`hot_probe_warn`). Beim heißen Antasten lag die Störung vor
  dem Kontakt schon knapp unter der Schwelle (README-k2_prtouch B.6).
- **Kunststoffreste** an der Düse verfälschen den Kontakt. Vor dem ersten Z-Antasten reinigt
  `_PI_NOZZLE_CLEAN` die Düse am Silikonstreifen.
- **Lüfter** stören den Sensor. Ohne Drosselung brach der Ruhetest bei laufendem Hotend-Lüfter immer
  ab. Das Modul drosselt deshalb beim Antasten wie das Original auf 60 % (`fan_damping`,
  README-k2_prtouch A.11).

**Prüfungen** (nicht Kalibrierungen):

| Befehl | was | erwartet |
|---|---|---|
| `KTWO_PRTOUCH_TEST_SWAP` | Signalweg PA15 → PC7, Versionen | `swap_ok` |
| `KTWO_PRTOUCH_READ` | scharf schalten, Totzeit, Ruhetest (Rohwerte, gefilterter Kanal, Messabstand), PC7 frei, entschärfen – **ohne Bewegung** | `READ ok`, `tick_median_ms` ≈ 0,79, `filt_max_abs` unter 2000 (= 0,5 × `min_hold`) |
| `QUERY_PROBE` | Zustand des Tasters | `open` |
| `PROBE_ACCURACY SAMPLES=5` (bzw. 10) | Wiederholgenauigkeit, mit Bewegung | Richtwert < 0,02 mm; an einem Gerät gemessen: Spannweite 0,005 mm |
| `KTWO_PRTOUCH_CHECK` (optional, `pres_check: True`) | dynamischer Sensortest wie Stock-`PRES_CHECK`: reagiert der Sensor auf Last? | Standard aus; Kriterium **Annahme** |

Vor **jeder** Antastung prüft das Modul automatisch: Freigabe, Geschwindigkeit, Signalweg, Ruhetest,
„noch kein Kontakt“. Nach jeder Antastung entschärft es, auch bei Fehlern.

**Symptome und Fehler.**
- Erste Schicht überall gleichmäßig zu hoch oder zu tief → erst Düse reinigen, `PROBE_ACCURACY`, die
  Temperaturkompensation prüfen (§4.7). Erst dann `z_offset` (siehe unten).
- Ruhetest-Abbruch („Ruhetest …“, Spannweite oder gefilterter Wert zu groß) → Düsentemperatur stabil?
  Lüfter? Düse berührt etwas? Seltene Einzelausreißer wiederholt das Modul selbst (`rest_retries: 2`).
- „Düse meldet bereits Kontakt VOR der Fahrt“ → Düse liegt auf oder Signalweg gestört.
- `swap_error` (Stock-Text `PR_ERR_CODE_SWAP_PIN_DETECTI`) → Leitung PA15/PC7 prüfen.
- Kein Kontakt innerhalb `probe_max_travel_mm` (8 mm) → Bett zu tief oder zu schief (§4.4).
- `PR_ERR_CODE_PRES_NOT_BE_SENSED` (nur `KTWO_PRTOUCH_CHECK`, Ersatz für Stock `key769`) → Sensor
  reagiert nicht.

**Z-Nullpunkt (`z_offset`) – nur im Ausnahmefall.** `[k2_prtouch]` ist eine normale Upstream-Probe.
Deshalb funktionieren `PROBE_CALIBRATE` und `Z_OFFSET_APPLY_PROBE`; der Wert landet per `SAVE_CONFIG`
in `[k2_prtouch] z_offset` in `printer.cfg`. **Am Gerät nicht erprobt.** Das Original addiert zu
jedem Messwert, ausgehend vom tiefsten Punkt bei der Auslösung, einen kleinen Korrekturwert (`zoft`,
im Mitschnitt 0,010–0,040 mm); der Pi-Hauptweg nimmt den Auslösepunkt direkt. Ob das eine sichtbare
Abweichung der ersten Schicht ergibt, ist **nicht belegt**. **Allgemein (Upstream-Klipper):** Man stellt im ersten Layer mit
`SET_GCODE_OFFSET Z_ADJUST=…` (in Mainsail „Z-Offset“) nach und übernimmt das mit
`Z_OFFSET_APPLY_PROBE` + `SAVE_CONFIG`. Ein größerer `z_offset` bringt die Düse näher ans Bett.

**Was man hier nicht kalibriert:** die Antast-Parameter (`pres_*`, §5.3) und die Geschwindigkeiten.
Das Modul prüft sie beim Laden und lehnt unsichere Werte ab.

**Stock ↔ Pi.** Gleiche Firmware, gleiche Schwellen, gleicher Stopp über PC7. Nicht übernommen:
Düse auf das Bett drücken und abziehen (`NOZZLE_CLEAR`), `zoft`-Korrektur, Ausreißerprüfung,
Kammer-/Fall-Kompensation im Taster. Der dynamische Test `PRES_CHECK` läuft im Original bei jedem
`G28` mit Z; am Pi ist er optional und standardmäßig aus.

---

### 4.6 Bettnetz (`G29`, `BED_MESH_CALIBRATE`)

**Zweck.** Die Unebenheit des Betts vermessen und im Druck ausgleichen. Klipper hebt bzw. senkt die
Düse je nach XY-Position um den Wert der Höhenkarte.

**Hintergrund.** Gemessen werden 9 × 9 Punkte zwischen X/Y 5 und 345, bikubisch auf ein feineres
Raster interpoliert (`mesh_pps 2`, `bicubic_tension 0.2`). Der Ausgleich wirkt voll bis 5 mm Höhe und
läuft bis 50 mm aus (`fade_start`/`fade_end`). **Allgemein:** Ab einigen Schichten gleicht das Druckbild
die Unebenheit selbst aus; danach druckt der Kopf wieder plan. Die Betttemperatur verändert die Form:
An einem Gerät war das Bett schüsselförmig (Ränder höher als die Mitte), warm flacher als kalt.

**Symptome.** Erste Schicht stellenweise zu dünn (durchsichtig, Riefen) oder zu dick (gequetscht,
Wülste), ohne klares Links-rechts-Muster (das wäre §4.4).

**Wann nötig.**
- Nach der Inbetriebnahme, nach Arbeiten am Bett oder an der Z-Mechanik.
- `START_PRINT` misst ohnehin vor jedem Druck den Druckbereich (Profil `adaptiv`, nur für diesen Druck).
  Dafür nutzt es die Objekte aus der Druckdatei oder `PRINT_MIN`/`PRINT_MAX` des Slicers plus 10 mm
  Rand, mindestens 3 × 3 Punkte. Ohne beides misst es das ganze Bett (config-pi/README.md, Abschnitt
  „Bettvermessung beim Druckstart“).
- Das gespeicherte Profil `default` braucht man für `START_PRINT ADAPTIVE=0` und als Grundlage nach
  jedem Z-Homing (`_PI_MESH_LADEN`).

**Ablauf** `G29 [BED_TEMP=50]` (macros.cfg):
1. Düse auf 140 °C, Bett auf `BED_TEMP`, `G28`, warten.
2. `Z_TILT_ADJUST`, Düse reinigen, `G28 Z`.
3. `BED_MESH_CALIBRATE PROFILE=default`.
4. Z auf 175, Heizungen aus, Meldung „mit SAVE_CONFIG speichern“.

Dauer an einem Gerät: `G29` ≈ 6 min; das reine 9 × 9-Netz je nach Messung 2 bis 4,5 min.

**Voraussetzungen.** Z ausgerichtet (macht `G29` selbst), Kraftsensor geprüft, PID ruhig.

**Ergebnis prüfen und speichern.** Plausibilisieren gegen das Vergleichsprofil
`stock_referenz_2026-10-16` im SAVE_CONFIG-Block (Netz des Original-Druckers, Spannweite etwa
−0,07…0,23 mm). Es wird nicht automatisch geladen. Speichern mit `SAVE_CONFIG`.

**Risiken.**
- Ein anschließendes `SAVE_CONFIG` macht die Motoren stromlos, und das Bett fällt. `G29` fährt deshalb
  am Ende ganz nach unten (Z 345, Stock: Z 175), dort kann es kaum noch absacken (§1.3).
- Ein im Druck aus der Datei kommender zweiter `BED_MESH_CALIBRATE` wird übersprungen, wenn
  `START_PRINT` schon gemessen hat (Makro-Hülle, Merker `_PI_MESH_STATUS`). Von Hand außerhalb eines
  Drucks wird nie übersprungen.

**Stock ↔ Pi.**
- Das Original misst ein regionales Netz über eine eigene Funktion und rechnet `zoft` je Punkt ein.
- Bei Betttemperatur über 80 °C wendet das Original eine „Fall-Kompensation“ (Durchhang) auf das Netz
  an. Sie ist am Pi **nicht** nachgebaut (README-k2_nozzle_tempcomp §5).
- Upstream verlangt `move_check_distance` ≥ 3 (Original 1).
- Die Stock-„Temperaturkompensation des Netzes“ ist in Wahrheit eine Düsen-Korrektur, siehe §4.7.

---

### 4.7 Düsen-Temperaturkompensation (`[k2_nozzle_tempcomp]`)

**Zweck.** Die Düse wird beim Antasten mit 140 °C vermessen, gedruckt wird aber heißer. Hotend und
Düse dehnen sich dabei aus, die Düsenspitze kommt dem Bett näher. Das Modul hebt die Düse im Druck um
den passenden Betrag an (README-k2_nozzle_tempcomp §1).

**Hintergrund (Formel wie Original).**
- ΔT = Düsen-**Soll**temperatur − Bezugstemperatur. Die Bezugstemperatur ist die geglättete
  Düsen-**Ist**temperatur beim letzten `G28 Z` bzw. nach erfolgreichem `BED_MESH_CALIBRATE`.
- Zuschlag = 0 für ΔT ≤ 0, sonst `min(0,181; a·ΔT² + b·ΔT + c)` mit a = 5,1·10⁻⁶, b = 0,
  c = 0,017316 mm, fest begrenzt auf 0…0,2 mm.
- Beispiele bei Bezug ≈ 140 °C: 220 °C → +0,050 mm, 240 °C → +0,069 mm.
- Wirkt nur bei geladenem Netz und im Zustand „printing“/„paused“, blendet mit dem Netz aus (voll bis
  5 mm, 0 ab 50 mm) und wird nur bei Änderung der Solltemperatur neu gerechnet.

**Eine Kalibrierung im engeren Sinn gibt es nicht.** Die Koeffizienten sind die des Originals; die
Quellen beschreiben kein Verfahren, sie selbst zu bestimmen (**nicht belegt**).

**Symptome.** **Annahme:** Ohne Kompensation wäre die erste Schicht bei hoher Drucktemperatur zu stark
angedrückt. Bei 220 °C beträgt der Unterschied zu „aus“ 0,05 mm.

**Befehle.** `KTWO_NOZZLE_TEMPCOMP` zeigt Status, Grund, Zuschlag, Bezug und Kette. Parameter: `A= B= C=`
(Koeffizienten zur Laufzeit), `RESET=1` (Werkswerte), `ENABLE=0|1`, `COMP_Z=` (Wert erzwingen),
`REF=` (Bezug von Hand). Nichts davon wird gespeichert; dauerhaft nur über `k2-module.cfg`.

**Prüfen.** Erste Schicht bei 220 °C beobachten. Mit `GET_POSITION` liegt die Toolhead-Z im Druck um
Zuschlag × Fade über der G-Code-Z. Nach einem Neustart ist der Bezug leer, bis das nächste `G28 Z`
läuft; bis dahin ist der Zuschlag 0.

**Stock ↔ Pi.** Gleiche Formel und Freigabebedingungen. Abweichungen: ohne Bezug kein Zuschlag;
`COMP_Z` wird ebenfalls begrenzt. Nicht nachgebaut, weil im Original inaktiv: lineare Variante,
Heizstunden-Korrektur, Kammerkompensation.

---

### 4.8 PID der Heizungen

**Zweck.** Die Regelparameter für Düse und Bett so einstellen, dass die Temperatur schnell, ohne großes
Überschwingen und ruhig auf dem Sollwert steht.

**Hintergrund (allgemein).** Ein PID-Regler berechnet die Heizleistung aus der Abweichung (P), ihrer
Summe über die Zeit (I) und ihrer Änderungsrate (D). Heizpatrone, Block und Fühler sind träge und
verzögert. Zu scharfe Werte lassen die Temperatur dauerhaft pendeln, zu weiche machen sie langsam.
`PID_CALIBRATE` lässt die Heizung kontrolliert um den Sollwert schwingen und leitet die Werte aus
Schwingungsdauer und -amplitude ab (Ziegler-Nichols, printer.cfg).

**Warum es hier wichtig ist.**
- `M109` gilt erst als fertig, wenn die Abweichung höchstens 1 °C **und** die Änderung höchstens
  0,1 °C/s beträgt (macros.cfg). Eine dauernd pendelnde Düse lässt `M109` endlos warten. Das
  blockierte live einen CFS-Rückzug.
- Das Antasten braucht eine ruhige Düse (§4.5).
- Der Bauteillüfter kühlt die Düse stark. Mit 100 % Lüfter gemessene Werte pendelten ohne Lüfter
  zwischen 218,6 und 221,5 °C (an einem Gerät). `NOZZLE_PID` misst deshalb standardmäßig **ohne**
  Bauteil- und Seitenlüfter.
- Ein Klipper-Fehler in der Formel (volle statt halbe Heizleistung) machte die Werte etwa doppelt so
  scharf; behoben in Upstream-Commit `e761a2c76` (printer.cfg). Mit dem korrigierten Klipper an einem
  Gerät gemessen: 9,773 / 0,931 / 25,654 (Düse 220 °C). Stock-Werte: 14,296 / 1,271 / 40,206.

**Symptome.** Temperatur schwingt mit gleichmäßiger Periode (an einem Gerät ≈ 10 s), `M109` wird nicht
fertig, Überschwingen beim Aufheizen, `verify_heater`-Fehler.

**Wann nötig.** Nach dem Tausch von Heizpatrone, Fühler, Heizblock oder Düse (**allgemein**), wenn
`HEATER_STABILITY_TEST` „UNRUHIG“ meldet, nach einem Klipper-Update mit Änderungen an der
PID-Berechnung. Ohne Anlass nicht.

**Ablauf Düse.**
1. `NOZZLE_PID [TEMP=220] [FAN=0]`: Sperre im Druck. Über 260 °C nur mit leerer Düse oder
   `FILAMENT_OK=1` (PLA/PETG verkokt sonst). Ist alles referenziert, fährt der Kopf über den
   Abfallschacht (Tropfen). Lüfter aus, `PID_CALIBRATE HEATER=extruder TARGET=…`, Heizung aus. Die
   Werte erscheinen in der Konsole und sind **noch nicht aktiv**.
2. Prüfen ohne Neustart: `SET_HEATER_PID HEATER=extruder KP=… KI=… KD=…` (Modul `k2_heater_pid`; der
   I-Anteil wird umgerechnet, damit die Heizleistung nicht springt), dann `HEATER_STABILITY_TEST
   [TEMP=220] [ZEIT=120]`. „ruhig“ bei höchstens 1 °C Abweichung.
3. Speichern: `SET_HEATER_PID … SAVE=1`, dann `SAVE_CONFIG` (§1.3).

**Hochtemperatur-Satz.** Das Original schaltet ab 280 °C auf einen zweiten PID-Satz um; Upstream kennt
das nicht. `[k2_heater_pid]` bildet es nach: Sind `high_pid_kp/ki/kd` gesetzt, schaltet ein Timer
(alle 0,5 s) ab Ziel ≥ `high_temp` (280) auf den Hochsatz, zurück unter `high_temp − high_hysteresis`
(270). Messen mit `NOZZLE_PID_HIGH [TEMP=300]` (nur mit leerer Düse), prüfen mit
`SET_HEATER_PID HEATER=extruder SET=HIGH KP=… KI=… KD=…` und `HEATER_STABILITY_TEST TEMP=300`,
speichern mit `SAVE=1` + `SAVE_CONFIG`. Die Werte landen im SAVE_CONFIG-Block, nicht von Hand in
`[k2_heater_pid]`. Ohne Hochsatz gibt es keine Umschaltung.

**Ablauf Bett.** `BEDPID [TEMP=100]` (≈ 10 min, Sperre im Druck), dann `SAVE_CONFIG`.

**Kammer.** Die Kammerheizung (PTC) läuft mit Zweipunktregelung (`control: watermark`) und braucht
keine PID-Kalibrierung. Ihre Absicherung (Lüfter-Drehzahl, Bett-Vorrang, 50-%-Anlauf) übernimmt
`[k2_chamber]`. Das ist eine Schutzfunktion, keine Kalibrierung (README-k2_chamber).

**Voraussetzungen und Sperren.** Kein Druck. `[k2_safety]` begrenzt auch das Ziel von `PID_CALIBRATE`:
Düse 350, Bett 120, Kammer 60 °C (README-k2_safety). Während `PID_CALIBRATE` lehnt `SET_HEATER_PID` ab.

**Risiken.** Heiße Düse ohne Lüfter (Tropfen, Verkoken bei falschem Material). `SAVE_CONFIG` → Bett
sackt ab.

**Stock ↔ Pi.** Das Original misst mit Bauteillüfter (Düse 230 °C, Hochsatz 320 °C); den Hochsatz
speichert es sofort. Am Pi misst `NOZZLE_PID` ohne Lüfter, aktiviert erst nach Prüfung und speichert
nur von Hand.
Die Creality-eigene „Bett-Leistungskalibrierung je Netzspannung“ wird nicht übernommen.

---

### 4.9 Input Shaper (`INPUTSHAPER`, Beschleunigungssensor `[k2_lis2dw]`)

**Zweck.** Die mechanischen Eigenschwingungen des Kopfs in X und Y messen und Klippers
Schwingungsunterdrückung darauf einstellen.

**Hintergrund (allgemein).** Kopf, Riemen und Rahmen bilden ein Feder-Masse-System. Jeder Ruck bei
einer Richtungsänderung regt es zum Schwingen an; im Druckbild sieht man das als Wellen hinter Kanten
und Löchern („Ringing“, „Ghosting“). Der Input Shaper formt die Bewegungsbefehle so, dass sich die
angeregte Schwingung bei der eingestellten Frequenz selbst auslöscht. Die Shaper-Typen unterscheiden
sich darin, wie viele Resonanzen und welche Frequenzbreite sie abdecken und wie stark sie Ecken
verrunden („Glättung“). Daraus empfiehlt Klipper eine höchste sinnvolle Beschleunigung.

**Messung.** Ein Beschleunigungssensor LIS2DW in der Düse, angeschlossen an die Düsen-MCU. Das
Upstream-Modul `[lis2dw]` scheitert an der Creality-Firmware („Command format mismatch“);
`[k2_lis2dw]` spricht das alte Protokoll (README-k2_lis2dw).
- Die Düsen-Firmware liest je Timer genau einen Messwert. Mit `read_rest_ms: 1.5` liefert sie
  ≈ 625 Werte/s; das Original lief mit ≈ 385/s.
- `bandwidth: 4` setzt den Tiefpass des Chips auf 400 Hz gegen Aliasing.
- Ausgewertet wird bis 1,5 × `max_freq` = 180 Hz. Die halbe Abtastrate (≈ 312 Hz) liegt darüber, die
  K2-Resonanzen (Original-Messungen 42–51 Hz) weit darunter.
- `[resonance_tester]`: Messpunkt Bettmitte 175/175/175, 20–120 Hz, `accel_per_hz: 100`, Messung als
  Frequenzdurchlauf (Upstream-Standard). Das Original misst einzelne Frequenzen; zum Vergleich
  `sweeping_period: 0`.

**Symptome.** Wellenmuster hinter Kanten, Schriftzügen und Löchern, besonders bei hoher
Beschleunigung.

**Wann nötig.** Nach der Inbetriebnahme (die Vorlage `printer.cfg` enthält die Stock-Werte mzv 42,0 /
42,4 Hz als Startpunkt), nach dem Riemenspannen, nach Umbauten am Kopf oder an der Mechanik.

**Vorab prüfen (ohne Bewegung, README-k2_lis2dw §7):**

| Befehl | erwartet |
|---|---|
| `ACCELEROMETER_DEBUG_READ REG=0x0f` | `0x44` (Chip-Kennung); sonst Verdrahtung/Pins prüfen, STOP |
| `KTWO_ACCEL_STATUS` | Antwort mit `messung=False`, Lesetakt 1,5 ms, keine Überläufe |
| `ACCELEROMETER_QUERY` (Motoren still) | Drucker-Z ≈ ±9800 mm/s² (Erdbeschleunigung), X und Y nahe 0. Wegen `axes_map: x,z,y` liegt Chip-Y auf Drucker-Z |
| `MEASURE_AXES_NOISE` | kleines Rauschen; an einem Gerät 1,1 / 2,6 / 2,5 mm/s² |

**Ablauf** `INPUTSHAPER [ACHSE=X|Y] [SPEICHERN=1]` (macros.cfg):
1. Sperre im Druck.
2. Filamentsensor aus (die Schwingungen lösen ihn sonst aus).
3. Referenzfahrt, falls nötig; Bauteillüfter aus.
4. `SHAPER_CALIBRATE` (beide Achsen oder eine).
5. Z auf 10.
6. Filamentsensor wieder an.
7. Mit `SPEICHERN=1` `SAVE_CONFIG`, sonst gelten die Werte bis zum Neustart.

Laut. Dauer an einem Gerät 307 s inklusive Homing.

**Ergebnis prüfen und speichern.**
- An einem Gerät gemessen:
  - X: zwei Resonanzen bei ≈ 45 und ≈ 108 Hz → `3hump_ei` 90,6 Hz, empfohlene Beschleunigung
    ≤ 4800–5500 mm/s².
  - Y: eine Spitze bei ≈ 40 Hz → `mzv` 40,2 Hz, empfohlen ≤ 4200–4800 mm/s².
- Die Stock-Werte (mzv 42,0 / 42,4) dämpfen bei X nur die 45-Hz-Spitze.
- Die empfohlenen Beschleunigungen sind nur Ausgabe; `max_accel` in `printer.cfg` bleibt der
  Stock-Wert.
- Diagramme: `~/klipper/scripts/calibrate_shaper.py` mit den CSV-Dateien, die `SHAPER_CALIBRATE`
  schreibt.
- Ausschalten zum Vergleich bis zum Neustart: `TUNOFFINPUTSHAPER`.

**Risiken.** Starke Vibration. Die Messung endet mit dem Bett auf Z 175; von dort fiel es beim folgenden
Neustart, und beim nächsten Antasten trat ein Schleppfehler an Z/Z1 auf (§1.3). Das Makro fährt das
Bett deshalb am Ende ganz nach unten (Z 345).

**Stock ↔ Pi.** Original: Makro `INPUTSHAPER` mit eigener Shaper-Auswahl (`AUTOTUNE_SHAPERS`),
Einzelfrequenzen, ≈ 385 Werte/s, speichert sofort. Pi: Upstream-`SHAPER_CALIBRATE` prüft alle
Shaper-Typen selbst, Frequenzdurchlauf, ≈ 625 Werte/s, speichert nur mit `SPEICHERN=1`.

---

## 5. Was man nicht oder nur im Ausnahmefall kalibriert

### 5.1 Motor-Elektrowinkel und Encoder (`KTWO_MOTOR_CAL_RUN` / `_RESTORE` / `_ENABLE`)

**Standardmäßig aus.** `allow_motor_calibration` steht in `k2-motor.cfg` nur als Kommentar und ist
damit `False`. Die
Quellen nennen **keinen Routinefall** für eine Neukalibrierung. Ein Testlauf am Extruder bestätigte die
Werkskalibrierung (unten). **Annahme:** sinnvoll höchstens nach Tausch oder Reparatur von Motor,
Magnet oder Encoder.

**Was die Läufe tun.**

| Modus | Befehl an den Motor | Bewegung | Ergebnis |
|---|---|---|---|
| `ELEC` | Elektrowinkel-Kalibrierung | etwa eine Umdrehung vor und zurück, langsam, **open-loop** mit festem Spannungsvektor | neuer `elec_offset` (Mittelwert) und `elec_offset_err_deg` (Streuung). Bestanden, wenn die Streuung unter dem Grenzwinkel (ID 8, typisch 15°) liegt; nur dann werden die Werte übernommen |
| `ENCODER` | Magnetfeld-Kalibrierung des Encoders | Dauerdrehung bis ≈ 63 Umdrehungen mit ≈ 2,5 U/s | der Encoder-Baustein linearisiert sich und brennt das Ergebnis in seinen eigenen Speicher. Danach passt der alte `elec_offset` nicht mehr → **ELEC ist Pflicht** |

Beide Modi prüfen zuerst die Phasenfolge mit vier kleinen Schritten und schreiben `phase_order_invert`
**immer** in den Flash, auch wenn der Lauf danach scheitert.

**Warum gesperrt (Risiken).**
1. **Kein Schutz, kein Abbruch.** Während des Laufs sind die Schutzfunktionen des Motors aus, und
   weder Bus noch Enable-Leitung wirken. Not-Aus nur über den Netzschalter.
2. **Unkontrollierte Bewegung.** An einer montierten Achse wären 63 Umdrehungen ein Crash in den
   Rahmen. Deshalb Pflicht `AUSGEKUPPELT=1`: X/Y Riemen ab, Z Spindel getrennt, Extruder ohne
   Filament. Eine einzelne Z-Spindel würde das Bett kippen (eigene Freigabe
   `allow_z_calibration`).
3. **Dauerhafte Fehlkalibrierung.** Ein blockierter Rotor oder ein Encoder-Wert kurz vor dem Umbruch
   (32 767 → 0) täuscht eine vertauschte Phasenfolge vor. Dann steht `phase_order_invert = 1` im Flash.
   **Laut Firmware-Analyse** regelt der Motor danach mit Mitkopplung: Er geht durch, meldet
   Schleppfehler oder Überstrom (`key783/784/789`) – bis zu einer erneuten Kalibrierung unter freien
   Bedingungen. Ein schlechter `elec_offset` kostet Drehmoment und lässt den Motor schwingen.
4. **Flüchtige Werte werden dauerhaft.** Die Firmware schreibt die ganze erste Flash-Seite aus dem RAM
   neu. Werte, die sonst nur bis zum Ausschalten gelten (Config-Werte des Motor-Inits, Stall-Modus,
   `protect_en`), stehen danach im Flash. Die Flash-Prüfsumme weicht danach ab; laut Firmware-Analyse
   prüft der Motor sie beim Start nicht.
5. **Werks-Sicherung.** Laut Firmware-Analyse überschreibt ein *bestandener* ELEC-Lauf auch die
   Werks-Sicherungskopie im Motor. Ihr alter Inhalt ist über den Bus nicht lesbar und lässt sich
   deshalb vorher nicht sichern; `KTWO_MOTOR_CAL_BACKUP` sichert nur den Haupt-Parameterblock.
6. **Positionsverlust.** Nach dem Lauf ist der Motor stromlos. Der Homing-Bezug ist ungültig.

**Freigaben (alle nötig, README §14).** `allow_write: True`, `allow_motor_calibration: True`; für
ENCODER zusätzlich `allow_encoder_calibration: True`; für Z/Z1 `allow_z_calibration: True`;
`_PI_FREIGABE allow_move`; `AUSGEKUPPELT=1`; kein Druck, kein Homing, keine
Schneidpositions-Kalibrierung; Motor-Init fertig. Je Aufruf genau ein Motor, kein Broadcast, keine
Wiederholung.

**Vorbedingungen (sonst wird nichts gesendet).**
- Snapshot vollständig; nach `M84` ist der Motor gesperrt (EN 1, Regler 0, PWM 0); keine Fehlerbits.
- Versorgung 22–26 V; Motor-MCU unter 70 °C.
- `ud_cal_set` (ID 20/23) 0,03–0,25 und Grenzwinkel (ID 8) 5–30° in RAM und Flash.
- **Encoder-Stellung nicht im Umbruch-Fenster** (0…1000 bzw. 31 767…32 767 Schritte).
- `protect_en` im RAM 1…255; Homing-Kp (ID 70) = Config-Wert; Stall-/Notfallmodus nicht vorübergehend.
- Der Plan listet alle RAM-Werte, die dauerhaft würden.

**Ablauf.**
1. `KTWO_MOTOR_CAL_RUN MOTOR=<m> MODE=ELEC|ENCODER AUSGEKUPPELT=1` → nur lesen, Plan, Vorbedingungen
   und ein Bestätigungscode (120 s gültig, einmal verwendbar).
2. Derselbe Befehl mit `CONFIRM=<code>` → `M84` → frische Sicherung → Vorbedingungen erneut →
   **genau ein** Kalibrier-Frame. Der Sendeschutz gibt nur diesen einen Frame an genau diese Adresse
   frei (2 s, höchstens 10 s) und löscht die Freigabe direkt nach dem Senden. Laufzeit: ELEC bis
   45 s, ENCODER bis 120 s.
3. Prüfung danach, Ergebnisdatei `<m>-<zeit>-lauf.json`, Zustand in `kalibrier-zustand.json`.

**Zustände** (überdauern Neustarts):

| Zustand | Bedeutung | weiter mit |
|---|---|---|
| `laeuft` | Frame gesendet | warten |
| `gesperrt_unbekannt` | keine oder kaputte Antwort | Drucker aus/ein, dann `KTWO_MOTOR_CAL_ENABLE MOTOR=<m> EINGEKUPPELT=1 STROMLOS=1` |
| `phase_verdacht` | ALARM: `phase_order_invert` geändert oder Schutz im Flash aus | `KTWO_MOTOR_CAL_RESTORE` |
| `elec_pflicht` | nach ENCODER | Drucker aus/ein, dann `MODE=ELEC` |
| `homing_noetig` / `z_ausrichtung_noetig` | Lauf in Ordnung | einkuppeln, `ENABLE`, `FIRMWARE_RESTART`, `G28` (Z: zusätzlich ausrichten und Bettnetz neu) |

**Harte Sperre.** In den Zuständen `laeuft`, `gesperrt_unbekannt`, `phase_verdacht` und `elec_pflicht`
löst jede Stepper-Freigabe der betroffenen Enable-Gruppe einen Klipper-Shutdown aus. X/Y/Z/Z1 teilen
`!PA9`, der Extruder hat eine eigene Leitung. Das deckt alle Wege ab: `G1 E`, CFS-Laden,
`SET_STEPPER_ENABLE`, Z-Ausrichten. `KTWO_MOTOR_INIT` ist gesperrt, solange ein Lauf noch laufen kann
(höchstens 150 s).

**Wiederherstellen.** `KTWO_MOTOR_CAL_RESTORE MOTOR=<m> BACKUP=<datei> [CONFIRM=<code>]` schreibt
`phase_order_invert`, `elec_offset` und `elec_offset_err_deg` aus einer Sicherung (§4.1) zurück: erst in
den RAM, dann alle RAM-Werte in den Flash. Keine Bewegung. Die Firmware wandelt Fließkommawerte über
Text mit 6 Nachkommastellen um; gegengelesen wird mit dieser Toleranz. In `elec_pflicht` gesperrt,
weil der alte `elec_offset` nach einer Encoder-Kalibrierung nicht mehr passt. Die Werks-Sicherungskopie
bleibt unverändert.

**Freigeben.** `KTWO_MOTOR_CAL_ENABLE MOTOR=<m> EINGEKUPPELT=1` prüft nur lesend und legt eine neue
Referenz-Sicherung an. Danach: `FIRMWARE_RESTART`, `G28` bzw. erstes Extrudieren (die Regelung geht
mit der nächsten Enable-Flanke an), dann `KTWO_MOTOR_CAL_CHECK` – erwartet EN 0, Regler 1, PWM 1.

**Beispiel (an einem Gerät).** Extruder ohne Filament, `MODE=ELEC`:
- Lauf 15,3 s, **nicht bestanden** (Streuung 70,8° ≥ 15°).
- Der Mittelwert lag nur ≈ 1,5 Encoder-Schritte neben dem Werkswert; die Werkskalibrierung ist damit
  bestätigt.
- `phase_order_invert` blieb unverändert, der Flash übernahm wie angekündigt den Stall-Modus und
  `protect_en` aus dem RAM.
- Vermutete Ursache des Scheiterns: Der eingebaute Motor dreht das Fördergetriebe mit, bei kleiner
  Kalibrierspannung streuen die Messpunkte.
- Folgerung (README §14): **den Extruder eingebaut nicht kalibrieren.**

**Stock ↔ Pi.** Das Original kennt dieselben Läufe als G-Codes (Encoder-Kalibrierung,
Elektro-Nullpunkt einzeln und für alle vier Achsen). Es prüft keine Vorbedingung, schaltet danach
nichts wieder ein und ruft die Läufe nie automatisch auf.

### 5.2 Kraftsensor der Riemenmodule (`KTWO_BELT_CALI`)

- `DIR=1` bzw. `DIR=2` speichert den aktuellen Wandlerwert als Kalibrierpunkt für 140 N bzw. 160 N
  (`cali_n1`/`cali_n2` bzw. `TEN=`).
- `DIR=0` setzt die Schieberposition auf 0.

Das setzt voraus, dass am Sensor in diesem Moment genau die Referenzkraft anliegt. **Wie diese Kraft
aufgebracht wird, ist nicht belegt.** Der Original-Code vermerkt „vor allem ohne montierten Riemen“,
`k2-module.cfg` „nur OHNE Riemen“ (README-k2_belt §5). Ein falscher Kalibrierpunkt verschiebt jede
spätere Kraftmessung und damit die Riemenspannung. Deshalb: **Werksthema, nicht kalibrieren.**

Einziger Anlass wäre `key714/715` (Flash leer, `0xFFFFFFFF`) oder `key720/721`, und dann nur mit
definierter Prüfkraft. Der Pi lehnt einen gemessenen Wandlerwert von −1 ab, weil er als „nicht
kalibriert“ gespeichert würde. Ohne vorheriges Lesen schreibt er nie.

### 5.3 Werte, die man nicht anfasst

| Werte | Ort | Warum nicht |
|---|---|---|
| Antast-Parameter des Kraftsensors (`pres_cfg_regs`, `pres_tri_hold`, `pres_tri_fter`, `pres_ded_tkms`, `arm_settle_s`) | `printer.cfg` `[k2_prtouch]` | Stock-Werte. Das Modul prüft sie beim Laden: Sind z. B. `min_hold` und `max_hold` gleich, löst die Düse **nie** aus → Crash ins Bett. Schwellen, die eine Antastgeschwindigkeit über 5 mm/s erfordern würden, werden abgelehnt (README-k2_prtouch A.8) |
| Antastgeschwindigkeit `speed`, `homing_speed`/`second_homing_speed` von `[stepper_z]` | `printer.cfg` | nur 2,5–5 mm/s. Langsamer bringt laut Modell keine Genauigkeit und zehrt die Reserve zum schwächsten Kontakt auf (README-k2_prtouch B.6) |
| Regelparameter der Motoren (`<motor>_controller_*`, `*_protection_param_*`) | `k2-motor.cfg` | Stock-Werte. Der Motor-Init schreibt sie bei jedem Start flüchtig in den Motor-RAM; sie sind keine Kalibrierung |
| Homing-Positionen X 352,5 / Y −6,2 und `FORCE_MOVE STEPPER=stepper_y DISTANCE=-3` nach dem XY-Homing | `printer.cfg`, `homing.cfg` | So hat Creality die Positionen eingemessen. Ohne den Versatz lägen alle Drucke um 1,5/1,5 mm verschoben (homing.cfg) |
| `homing_speed` X/Y 100 mm/s | `printer.cfg` | Bei 30 mm/s ratterte der Motor am Anschlag (an einem Gerät beobachtet); 100 bleibt |
| Koeffizienten der Temperaturkompensation | `k2-module.cfg` | Stock-Kennlinie (§4.7) |
| Soll-Riemenkraft `ch_best_n` 140 N, Toleranz `ch_best_error` 0,15 | `k2-module.cfg` | Stock-Werte |
| Optionen der Schneidpositions-Kalibrierung `cut_cal_*` | `[k2_motor]` | Stock-Defaults mit harten Grenzen (config-pi/NOTIZ-cutcal.md §2) |

### 5.4 Lichtschranken-Ausrichtung (`KTWO_ZALIGN`, `ZALIGN_STOCK`)

`[k2_zalign]` bildet die Stock-Fahrt auf die Lichtschranken mit Upstream-Mitteln nach: Jede Seite
fährt auf ihre Schranke und stoppt einzeln; die Fahrt erfolgt in Etappen mit harter Grenze für die
Wegdifferenz. Danach folgen `G28 Z` → `Z_TILT_ADJUST` → `G28 Z` (`ZALIGN_STOCK`). Ausgeliefert ist nur
die lesende Variante (`ZALIGN_STATUS`). Der Ausrichtungs-Abschnitt ist auskommentiert, zusätzlich
gesperrt und **am Gerät ungetestet** (zalign.cfg, config-pi/NOTIZ-zalign.md §4–§5). Nur sinnvoll, falls
`Z_TILT_ADJUST` allein nicht reicht. Das Kriterium dafür steht in config-pi/NOTIZ-zalign.md §5 (T8).

### 5.5 Nicht Teil dieses Projekts

- **Pressure Advance, Flussrate, Extruder-Schritte:** In `printer.cfg` steht der Stock-Wert
  `pressure_advance: 0.038`. Setzen geht mit `M900` bzw. `SET_PRESSURE_ADVANCE`. Eigene Kalibrierungen
  dafür gibt es im Projekt nicht; das bleibt Slicer- bzw. Upstream-Sache.
- **Stock-`PRINT_CALIBRATION`** (Creality-Druck-Nivellierkalibrierung) ist ein geschlossenes
  Creality-Modul und wurde nicht übernommen (config-pi/README.md §2.3).

---

## 6. Symptom → zuerst prüfen

| Symptom / Meldung | zuerst prüfen | dann ggf. |
|---|---|---|
| Erste Schicht links und rechts verschieden | `Z_AXIS_CALIBRATION` | `G29` + `SAVE_CONFIG` |
| Erste Schicht stellenweise zu dünn oder zu dick | Netz geladen? (`START_PRINT` misst den Druckbereich) | `G29` |
| Erste Schicht überall zu hoch oder zu tief | Düse sauber? `PROBE_ACCURACY`; `KTWO_NOZZLE_TEMPCOMP` (aktiv, Bezug gesetzt?) | `z_offset` über `Z_OFFSET_APPLY_PROBE` (am Gerät nicht erprobt, §4.5) |
| Ruhetest-Abbruch beim Antasten | Düsentemperatur stabil? Lüfter? Düse berührt etwas? `KTWO_PRTOUCH_READ` | keine Kalibrierung; Ursache beseitigen |
| „Düse meldet bereits Kontakt VOR der Fahrt“, `swap_error` | Düse frei? `KTWO_PRTOUCH_TEST_SWAP` (Leitung PA15 → PC7) | – |
| Kein Kontakt innerhalb `probe_max_travel_mm` bei `Z_TILT_ADJUST` | Bett stark schief (≥ ≈ 6,9 mm)? Abstand Düse–Bett links und rechts messen (§4.4) | nicht erzwingen; §4.4 Grenzen |
| „Retries aborting … increasing“ | Zuordnung `stepper_z`/`stepper_z1` und `z_positions` | STOP |
| Wellen hinter Kanten (Ringing) | `KTWO_BELT_TEST` | `BELT_TENSION`, danach `INPUTSHAPER SPEICHERN=1` |
| `KTWO_BELT_TEST` außerhalb 119–161 N, „Riemen locker“ | Riemen sichtbar beschädigt oder abgesprungen? | `BELT_TENSION` |
| `key714/715` | – | Riemenmodul nicht kalibriert: Werks-/Servicefall, kein `KTWO_BELT_CALI` ohne Prüfkraft (§5.2) |
| `key712/713` (Kommunikation) | RS485-Verbindung, `KTWO_BUS_STATUS` | – |
| `key716/717` (Kraft unplausibel) | `KTWO_BELT_INFO` (Rohwert, Kennlinie), Modul und Sensor | – |
| `key718/719` (Regelung > 200 Schritte) | Schieber am Ende? Riemen gerissen? | STOP |
| Temperatur pendelt, `M109` wird nicht fertig, Überschwingen | `HEATER_STABILITY_TEST`; Lüfter beim Messen an? | `NOZZLE_PID` (ohne Lüfter), prüfen, speichern |
| Unruhig nur über 280 °C | Hochsatz vorhanden? (`printer.k2_heater_pid`) | `NOZZLE_PID_HIGH` |
| Betttemperatur pendelt | – | `BEDPID` |
| Filament nach Wechsel nicht sauber getrennt, Rückzug klemmt (Box-Status 0x19) | Schneidhebel mechanisch frei? | `CALIBRATE_CUT_POS` (**Annahme**: Ursache Schneidposition) |
| `CALIBRATE_CUT_POS`: „kein Stall …“, „nur ein Stall“ | Hebel, Stall-Leitung PB11 | nicht wiederholen ohne Ursache |
| `key789` (Schleppfehler) an Z und Z1 nach einem Neustart | Bett abgesackt oder verkantet? Bett ganz nach unten, `G28` | `Z_AXIS_CALIBRATION`; **keine** Motor-Kalibrierung |
| Motor-Fehlercodes `key781`–`key797` | `KTWO_MOTOR_STATUS`, Mechanik, Versorgung; `KTWO_MOTOR_CAL_CHECK` gegen die Sicherung | Motor-Kalibrierung nur im Ausnahmefall (§5.1) |
| `KTWO_MOTOR_CAL_CHECK`: ALARM `phase_order_invert` geändert | – | `KTWO_MOTOR_CAL_RESTORE` mit der Sicherung |
| „Motor-Schutz AUS bei …“ (`protect_en` = 0) | – | Drucker aus- und wieder einschalten |
| `key798` nach einem Neustart | – | `FIRMWARE_RESTART` (macht der Start-Wächter); kein Kalibrierproblem |
| „Command format mismatch: query_lis2dw“ | Upstream-`[lis2dw]` statt `[k2_lis2dw]` eingetragen | – |
| `ACCELEROMETER_DEBUG_READ` ≠ `0x44`, „No accelerometer measurements found“ | Verdrahtung, Pins, SPI-Modus | STOP, kein `INPUTSHAPER` |

---

## 7. Belege

| Thema | Datei |
|---|---|
| Motor-Init, Kalibrierwerte sichern/prüfen, Motor-Kalibrierung, `protect_en`, Schneidposition | `pi-host/README.md` §10, §13, §14, §15; Modul `k2_motor_init.py` (als `k2_motor` geladen), `k2_transport.py` |
| Schneidposition | `pi-host/config-pi/cutcal.cfg`, `pi-host/config-pi/NOTIZ-cutcal.md`, `pi-host/README-k2_limits.md` |
| Riemenspannung | `pi-host/README-k2_belt.md`, Modul `k2_belt.py`, `pi-host/config-pi/k2-module.cfg`, Makro `BELT_TENSION` in `pi-host/config-pi/macros.cfg` |
| Z-Ausrichtung, Lichtschranken | `pi-host/config-pi/zalign.cfg`, `pi-host/config-pi/NOTIZ-zalign.md`, Modul `k2_zalign.py`, Makros `Z_AXIS_CALIBRATION`, `_PI_ZTILT_WENN_NOETIG` |
| Kraftsensor, Antasten | `pi-host/README-k2_prtouch.md`, Modul `k2_prtouch.py`, `pi-host/config-pi/homing.cfg` |
| Bettnetz | `pi-host/config-pi/printer.cfg` (`[bed_mesh]`), `pi-host/config-pi/macros.cfg` (`G29`, `START_PRINT`, `_PI_MESH_DRUCKBEREICH`), `pi-host/config-pi/README.md` |
| Düsen-Temperaturkompensation | `pi-host/README-k2_nozzle_tempcomp.md`, Modul `k2_nozzle_tempcomp.py` |
| PID, Hochtemperatur-Satz, Grenzen | `pi-host/config-pi/printer.cfg`, `pi-host/config-pi/macros.cfg`, Modul `k2_heater_pid.py`, `pi-host/README-k2_safety.md`, `pi-host/README-k2_chamber.md` |
| Input Shaper | `pi-host/README-k2_lis2dw.md`, Modul `k2_lis2dw.py`, `pi-host/config-pi/NOTIZ-accel.md`, Makro `INPUTSHAPER` |
| Freigaben, Stock-Zuordnung, Übernahme vom Original | `pi-host/config-pi/README.md` §1, §2 |

---

## 8. Begriffe erklärt

Fachbegriffe, die in dieser Datei vorkommen, ausführlich erklärt.

**Freigabe / gesperrt.** Ein Schutzschalter in einer Konfigurationsdatei (`True` = erlaubt, `False` =
verboten), siehe §1.4. „Gesperrt“ heißt: Die nötige Freigabe ist aus, oder eine Sicherheitsprüfung
lässt den Befehl gerade nicht zu (z. B. „nicht während eines Drucks“). Ein gesperrter Befehl bricht
immer **vor** der ersten Bewegung bzw. dem ersten gesendeten Befehl ab und sagt in der Konsole, warum.

**Plan / Probelauf / `DRY_RUN=1`.** Der Befehl führt nichts aus, sondern listet in der Konsole auf,
was er tun würde: welche Fahrten, welche Befehle an welches Gerät. Dient zum gefahrlosen Nachlesen.

**Referenzieren / Homing (`G28`).** Nach dem Einschalten weiß der Drucker nicht, wo Kopf und Bett
stehen. Beim Referenzieren fährt er jede Achse an einen bekannten Punkt: X und Y gegen den Anschlag
(der Motor merkt den Widerstand), Z durch Antasten der Düse aufs Bett. Erst danach sind Positionen
wie „X175 Y175“ eindeutig. „Nicht referenziert“ = Position unbekannt, viele Befehle verweigern dann.

**Z-Richtung bei diesem Drucker.** Der Kopf fährt nur in X/Y, das Bett fährt auf und ab. **Kleines Z =
Bett oben an der Düse** (Z 0 = Düse berührt das Bett), **großes Z = Bett unten** (Z 345 ≈ ganz unten).

**Stromlos / `M84`.** Die Motoren bekommen keinen Strom mehr und halten nichts fest. Beim K2 Plus
heißt das: Das schwere Bett rutscht die Spindeln hinunter (§1.3), und der Extruder lässt das Filament
los. Auch jeder Klipper-Neustart macht alle Motoren kurz stromlos.

**Closed-Loop-Motor (geregelter Motor).** Die Motoren dieses Druckers haben einen eigenen Winkelgeber
und eine eigene Regelung. Sie merken selbst, wenn sie nicht dort sind, wo sie sein sollen, und gleichen
aus bzw. melden einen Fehler. Bestromt halten sie ihre Position sehr fest (deshalb lässt sich
Filament bei bestromtem Extruder nicht von Hand herausziehen).

**Open-Loop.** Der Motor wird ohne Rückmeldung angesteuert („dreh dich so weit“), ohne zu prüfen, ob
er dort ankommt. Kommt hier nur bei der Motor-Kalibrierung vor (§5.1) und ist deshalb riskant.

**Enable-Leitung.** Das Signal, das einen Motor einschaltet (bestromt). X, Y, Z und Z1 teilen sich eine
Leitung, der Extruder hat eine eigene. Deshalb kann man den Extruder allein stromlos schalten, ohne dass
das Bett absackt, aber nicht X allein.

**Schleppfehler (`key789`).** Der Motor hängt zu weit hinter der Sollposition zurück, z. B. weil die
Achse klemmt oder blockiert ist. Der Motor meldet das und schaltet seine Regelung ab, damit nichts
beschädigt wird.

**`key…`-Codes.** Die Fehlernummern der Original-Firmware (z. B. `key798` = Motor antwortet beim Start
nicht, `key789` = Schleppfehler). Der Pi verwendet dieselben Nummern, damit man sie mit Creality-
Unterlagen vergleichen kann.

**Ruhetest (Kraftsensor).** Bevor die Düse aufs Bett tastet, prüft der Pi kurz, ob der Kraftsensor
still ist. Rauscht er zu stark (Vibration, Lüfter, Wärme), könnte er fälschlich
„Berührung“ melden. Dann wird der Test wiederholt; scheitert er, wird nicht angetastet.

**Flash und RAM.** Zwei Arten von Speicher in den Motoren und Modulen. **RAM** ist der Arbeitsspeicher:
Werte darin gelten nur bis zum Ausschalten. **Flash** ist der Dauerspeicher: Werte darin bleiben auch
ohne Strom erhalten (wie eine Festplatte). Kalibrierwerte der Motoren liegen im Flash. Etwas „dauerhaft
schreiben“ heißt: in den Flash schreiben.

**`SAVE_CONFIG`.** Klipper-Befehl, der gemessene Werte (Bettnetz, PID, Input Shaper, Z-Offset,
Schneidposition) unten in die `printer.cfg` schreibt und Klipper danach **neu startet** (§1.3).

**Fahrgrenzen / Betriebsfenster.** Die Bereiche, in denen der Kopf fahren darf. Normal: X ab −7,8 mm,
Y bis 352 mm. Für den Filamentwechsel muss der Kopf weiter: zum Abfallschacht hinten (Y 374–378), bei
der Schneidpositions-Kalibrierung auch etwas weiter nach links. Dafür werden die Grenzen kurz
**aufgeweitet** (Y bis 400, X ab −10) und danach wieder eng gestellt. Fährt ein Befehl außerhalb der Grenzen, kommt `Move out of range` und der Befehl bricht ab.

**Halter (bei Fahrgrenzen).** Wer die Grenzen aufweitet, meldet sich als „Halter“ an (z. B. der laufende
Filamentwechsel). Die Grenzen bleiben weit, solange mindestens ein Halter angemeldet ist, und werden
eng, sobald sich der letzte abgemeldet hat. Nach einem Fehler werden alle Halter entfernt.

**Sendeschutz.** Eine Liste im RS485-Treiber, welche Befehle an welche Geräte überhaupt hinausgehen
dürfen. Gefährliche Befehle (z. B. Motor-Kalibrierung, Bootloader, Flash löschen) werden dort
abgefangen, selbst wenn ein Programmfehler sie senden wollte.

**Snapshot / Sicherung.** Ein Abbild aller wichtigen Werte eines Motors (aus RAM und Flash) zu einem
Zeitpunkt, als Datei gespeichert (`KTWO_MOTOR_CAL_BACKUP`). Damit kann man später vergleichen, ob sich
etwas verändert hat, und alte Werte zurückschreiben.

**Bestätigungscode (`CONFIRM=`).** Bei gefährlichen Befehlen (Motor-Kalibrierung, Werte
zurückschreiben) zeigt der erste Aufruf nur den Plan und einen kurzen Code. Erst ein zweiter Aufruf mit
`CONFIRM=<Code>` innerhalb von 120 s führt aus. Das verhindert, dass ein vertippter oder versehentlich
wiederholter Befehl sofort etwas anrichtet.

**Zustandsdatei.** Eine kleine Datei, in der sich der Pi merkt, in welchem Schritt eine
Motor-Kalibrierung gerade steht (z. B. „läuft“, „fertig“, „Alarm“). Sie überdauert Neustarts, damit
nach einem Absturz klar ist, dass ein Motor noch geprüft werden muss.

