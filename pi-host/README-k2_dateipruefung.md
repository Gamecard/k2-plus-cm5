# k2_dateipruefung – Druckdatei vor dem Start prüfen

Stand 23.10.2026 · Modul `k2_dateipruefung.py` (`[k2_dateipruefung]` in `config-pi/k2-module.cfg`) · Makro
`SDCARD_PRINT_FILE` in `config-pi/macros.cfg` · Tests `test_k2_dateipruefung.py` (offline; Teil B zusätzlich gegen
echtes Upstream-Klipper 9cb9975, falls vorhanden).

## 1. Wozu – Vorfall vom 23.10.2026

In OrcaSlicer wurde eine MakerWorld-3MF **geöffnet** statt importiert. Orca übernahm dabei das Druckerprofil des
Projekts (Bambu Lab X1 Carbon). Die Datei wurde in Mainsail gestartet und führte Bambu-Startcode aus: `M1006`-Töne,
`G380`, `M620`/`M621` (AMS), `M109 S250` und `G1 E50` als Spülung über dem Bett, PrTouch heiß bei 205 °C. Erst bei
`M221 S ; push soft endstop status` brach Klipper mit „unable to parse“ ab.

Solche Dateien starten jetzt gar nicht erst. Die Prüfung läuft, **bevor** die Datei geöffnet wird – nichts heizt,
nichts fährt.

## 2. Einbindung

```
SDCARD_PRINT_FILE FILENAME="…"            (Mainsail/Moonraker, Orca „Hochladen und drucken“,
  │                                        PRINT_FROM_LAYER … START=1)
  ├─ KTWO_DATEI_PRUEFEN {rawparams}       Fehler → Makro bricht hier ab
  └─ SDCARD_PRINT_FILE_BASE {rawparams}   = Original von virtual_sdcard (rename_existing)
```

- Moonraker sendet `SDCARD_PRINT_FILE FILENAME="<pfad>"` (Anführungszeichen im Namen als `\"`). `rawparams` ist der
  Parameterteil **genau wie empfangen** und geht unverändert an beide Befehle. Beide zerlegen ihn mit demselben
  Klipper-Parser wie das Original – Leerzeichen, Umlaute, `"`, `'`, `;`, `#` im Namen kommen identisch an (Teil B
  der Tests mit echtem `gcode.py`/`gcode_macro.py`/`virtual_sdcard.py`).
- Weitere Parameter (z. B. `ISCONTINUEPRINT=1`) gehen unverändert an das Original.
- Der Pfad wird wie bei `SDCARD_PRINT_FILE` bestimmt: relativ zu `[virtual_sdcard] path`, führendes `/` entfernt,
  Unterordner erlaubt, sonst ohne Groß/Klein in der Dateiliste; nur Endungen `gcode`/`g`/`gco`.
- Ist `[k2_dateipruefung]` nicht geladen, meldet das Makro „nicht geladen – Datei NICHT geprüft“ und startet trotzdem.
- `M23` + `M24` (Datei wählen, starten) laufen **nicht** über das Makro und werden nicht geprüft.

## 3. Regeln

| Fall | Ergebnis |
|---|---|
| a) `; printer_model = …` vorhanden, passt zu keinem Eintrag aus `erlaubte_modelle` | **Abbruch** |
| b) kein (oder leeres) `printer_model`, Bambu-Befehl in einer **Befehlszeile** am Anfang | **Abbruch**, Befehle werden genannt |
| c) `printer_model` passt | Druck startet, Zeile „geprüft … – ok“ |
| c) weder `printer_model` noch Bambu-Befehl | Druck startet, Hinweis „Druckerprofil unbekannt“ |

Meldungen (Konsole):

```
!! [k2_dateipruefung] Datei ist für ‚Bambu Lab X1 Carbon‘ gesliced, nicht für den K2 Plus – im Slicer Drucker
   ‚Creality K2 Plus 0.4 nozzle - Pi‘ wählen und neu slicen (MakerWorld-3MF mit Strg+I importieren statt öffnen).
   Druck nicht gestartet.
!! [k2_dateipruefung] Datei enthält Bambu-Lab-Befehle (M1006, M620, G380, …) und kein Druckerprofil
   (printer_model) – … Druck nicht gestartet.
// [k2_dateipruefung] Hinweis: ‚<datei>‘ – Druckerprofil unbekannt (…); Datei wird trotzdem gedruckt
```

(Mainsail zeigt die rote Zeile zweimal: Klipper meldet einen Fehler aus einem Makro einmal für den inneren und
einmal für den äußeren Befehl.)

- **Modellvergleich:** Teilstring ohne Groß/Klein, Treffer am Wortanfang. `K2` passt zu „Creality K2 Plus“ (Orca,
  Creality Print), „K2 Plus“ (eigener Generator), „K2Plus“ – nicht zu „Original Prusa MK2.5S“.
- **Bambu-Befehle (Default):** `M1006 G380 M620 M621 M1002 M975 M960 G29.1 M142 M977`, ergänzt um `M622 M623 M624
  M625 M970 M974 M982 M991 M1007 G392`. Keiner davon ist in Klipper oder der Creality-Firmware belegt. Gezählt wird
  nur das Befehlswort am Zeilenanfang (`G29` ≠ `G29.1`, `M6200` ≠ `M620`); Kommentare, angehängte `; …` und Texte
  wie `M117 M620` zählen nicht.
- **Wo steht `printer_model`?** Orca und Creality Print schreiben den Konfigblock ans Dateiende (≈ 20–30 KiB),
  Bambu Studio an den Anfang – beides wird gelesen.

## 4. Lesen (große Dateien)

Nie die ganze Datei (13–20 MB sind normal):

1. Dateiende per `seek`: letzte 512 KiB. Steht dort `printer_model`, ist die Prüfung fertig.
2. Sonst der Anfang in Blöcken zu 64 KiB, bis 64 KiB **Befehlszeilen** gesehen sind (Vorschaubilder und ein
   vorangestellter Konfigblock sind Kommentare und zählen nicht), höchstens 1 MiB.

Angeschnittene Zeilen an den Blockgrenzen werden verworfen. Lesen und Auswerten laufen im eigenen Thread
(`aio_executor` wie bei `virtual_sdcard`); der Reactor wartet nur. Gemessen (Test, PC): 20-MB-Datei 512 KiB gelesen,
≈ 2–3 ms; schlimmster Fall ≈ 1,5 MiB.

## 5. Optionen `[k2_dateipruefung]`

| Option | Default | Bedeutung |
|---|---|---|
| `aktiv` | `True` | `False` = nicht prüfen (je Druckstart eine Warnung) |
| `erlaubte_modelle` | `K2` | Komma-Liste, Teilstring ohne Groß/Klein am Wortanfang |
| `slicer_profil` | `Creality K2 Plus 0.4 nozzle - Pi` | Profilname, den die Fehlermeldung nennt |
| `bambu_befehle` | s. §3 | Komma-Liste, ersetzt den Default komplett |

Status `printer["k2_dateipruefung"]`: `aktiv`, `erlaubte_modelle`, `letzte` (`datei`, `ergebnis` =
`ok`/`abgelehnt`/`unbekannt`/`aus`/`fehler`, `modell`, `bambu`, `gelesen`, `dauer_ms`).

## 6. Abschalten

- **Bis zum Neustart:** `KTWO_DATEI_PRUEFUNG AKTIV=0` (Warnung in der Konsole), wieder an mit `AKTIV=1`.
  `KTWO_DATEI_PRUEFUNG` ohne Parameter zeigt Zustand und letzte Prüfung.
- **Dauerhaft:** `aktiv: False` in `[k2_dateipruefung]`, dann `FIRMWARE_RESTART`.
- Von Hand prüfen ohne zu drucken: `KTWO_DATEI_PRUEFEN FILENAME="<datei>"`.

Fehlt die Datei oder ist sie nicht lesbar, bricht der Start mit klarer Meldung ab. Ein interner Fehler der Prüfung
ist ein normaler G-Code-Fehler (kein Klipper-Shutdown) und nennt `KTWO_DATEI_PRUEFUNG AKTIV=0` als Ausweg.

## 7. Einbau

- `k2_dateipruefung.py` → `klippy/extras/k2_dateipruefung.py` (`install.sh` kopiert alle `pi-host/k2_*.py`
  automatisch; braucht `k2_konsole.py` daneben).
- `config-pi/k2-module.cfg` (Abschnitt) und `config-pi/macros.cfg` (Makro) → `printer_data/config/`.
- Wirksam nach `sudo systemctl restart klipper` (neues Python-Modul; `FIRMWARE_RESTART` lädt Module nicht neu).

## 8. Grenzen

- `printer_model` ist eine Slicer-Angabe. Eine Datei mit K2-Modell, aber von Hand eingefügtem Bambu-Startcode läuft
  durch (Regel a entscheidet allein).
- Slicer ohne `printer_model` (z. B. Cura) und ohne Bambu-Befehle: nur Hinweis, keine weitere Prüfung.
- `M23`/`M24` umgehen die Prüfung (s. §2).

## Nachtrag 23.10.2026: allgemeine Klipper-Profile (Regel d)

Dateien aus einem allgemeinen Klipper-Profil (z. B. OrcaSlicer „MyKlipper“,
`printer_model = Generic Klipper Printer`) werden **mit Warnung gedruckt**,
wenn alle drei Bedingungen gelten:

- `; gcode_flavor = klipper` im Slicer-Konfigblock,
- der Anfang ruft `START_PRINT` oder `PRINT_START` auf,
- kein Bambu-Befehl am Anfang.

Die Warnung nennt die Bettgröße laut Datei (der K2 hat 350 × 350 mm, Objekte
liegen sonst evtl. nicht mittig), ob die Düse schon **vor** START_PRINT
aufgeheizt wird (tropft beim Referenzieren, PrTouch tastet heiß), und dass
K2-Geschwindigkeiten und der CFS-Mehrfarbwechsel fehlen. Fehlt eine
Bedingung, wird wie bisher abgelehnt. Abschalten: `klipper_generisch: False`.
