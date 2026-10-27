# k2_konsole – Warnungen und Fehler der K2-Module in der Mainsail-Konsole

Stand: 23.10.2026 (Konsolen-Audit aller `pi-host/k2_*.py`).

## Warum

Viele Warnungen und Fehler der K2-Module standen nur im `klippy.log`. Dazu kam
ein grundsätzliches Problem: Meldungen aus der Startphase (`klippy:connect`,
z. B. Motor-Init, Box-Auto-Addr, RS485-Port) wurden zwar mit `respond_info`
ausgegeben, kamen aber nie in Mainsail an, weil Moonraker die Konsole erst nach
„ready“ abonniert. Zwei Controller (Motor, CFS-Box) bekamen außerdem das
gcode-Objekt als `logger` – dessen `info`/`error` gibt es nicht, die Aufrufe
liefen still ins Leere (nicht einmal ins Log).

## Regeln (gelten für alle K2-Module)

| Art | Konsole | Log |
|---|---|---|
| Fehler | `!! [modul] …` (Mainsail rot) | `logging.error` |
| Warnung | `// [modul] …` | `logging.warning` |
| Hinweis (Start) | `// [modul] …` | `logging.info` |

- `k2_konsole.Konsole(printer, "modul").melde(text, stufe, …)` wirft **nie**
  (auch nicht ohne gcode-Objekt, im Timer oder im Test).
- **Startphase:** Meldungen vor `klippy:ready` werden gepuffert (max. 50, ein
  Überlauf wird gezählt und gemeldet) und **5 s nach „ready“** ausgegeben.
  Ausnahme `sofort=True` für Shutdown-Pfade (danach kommt kein „ready“ mehr).
- **Entprellung:** Timer-/Poll-Meldungen tragen einen `schluessel` und eine
  `pause` (s). Dieselbe Meldung erscheint höchstens einmal je Pause; unterdrückte
  Wiederholungen werden beim nächsten Durchlass als „(N× seit der letzten
  Meldung)“ angehängt.
- **Timer werfen nie** (sonst „Unhandled exception during run“ → Shutdown).
  Sicherheitsrelevante Timer (Kammerheizung) sperren bei einem internen Fehler
  stattdessen sicher und melden rot.
- **Keine Konsole aus Hintergrund-Threads** (Serial-Thread, Hilfs-Thread des
  `ReactorBusProxy`): `gcode.respond_*` ist nicht threadsicher. Solche Stellen
  zählen nur mit; ein Reactor-Timer meldet.
- `LogAdapter("modul")` ersetzt den falschen gcode-`logger` der Controller
  (→ klippy.log); `KonsolenLog(konsole, stufe)` schreibt Controller-Meldungen in
  die Konsole (PrTouch-Warnungen).

## Audit-Tabelle

| Modul | Stelle | vorher | jetzt |
|---|---|---|---|
| k2_485 | Port offen / nicht zu öffnen / Batch (Connect) | `respond_info` beim Connect (verloren) | gepuffert; Fehler `!!` |
| k2_485 | Port im Betrieb ausgefallen (`OSError`) | nur Ausnahme an den Aufrufer | Statistik-Timer meldet `!!` (einmal), Rückkehr `//` |
| k2_485 | Bus-Zähler (Timeouts/CRC/Wiederholungen) | nur `KTWO_BUS_STATUS` | **neu:** Statistik-Wächter, Warnung `//` (s. u.) |
| k2_belt | Bus fehlt (Connect), Webhook nicht registriert | `respond_info` beim Start (verloren) | gepuffert `// [k2_belt <name>]` |
| k2_box | Material-DB, Düsen-Material-/Slot-/Quellen-Datei (Connect) | Log bzw. `respond_info` beim Connect (verloren) | gepuffert, kaputt = `// … WARNUNG` |
| k2_box | Bus fehlt (Connect), Auto-Addr (k2_motor:ready) | `respond_info` beim Connect (verloren) | gepuffert; Bus fehlt/Auto-Addr-Fehler `!!` |
| k2_box | 0x0a-Poll-Timer: Box antwortet nicht | still verschluckt | `//` nach 3 Fehlschlägen in Folge, höchstens alle 30 min; „antwortet wieder“ |
| k2_box | Box im Zustand ERROR (rote LEDs) | nur `KTWO_BOX_STATUS` | `!! … CFS meldet Fehler 0x.. …` einmal je neuem Code (nicht, wenn ein Wechsel-Fehler schon gemeldet ist) |
| k2_box | Poll-Timer nicht startbar | still | `!!` |
| k2_box | Rückbau in Fehlerpfaden (Beschleunigung, Bauteillüfter, Kopfsensor) | still | `// … WARNUNG … nicht zurückgesetzt` |
| k2_box | Fahrgrenzen der M8200-Kette nicht freigegeben | still | `!!` (Kopf dürfte sonst weiter in den Schacht) |
| k2_box | `M104 S0` nach Fehler (externe Spule) gescheitert | still | in derselben roten Meldung „FEHLGESCHLAGEN“ |
| k2_box | Controller-`logger` | gcode (lief ins Leere) | `LogAdapter` → klippy.log |
| k2_cfs_sensor | Lesefehler im Abtast-Timer | nur Log | `//` höchstens alle 30 min |
| k2_cfs_sensor | gültig → ungültig (offline/veraltet/außerhalb) | nur Status | `//` höchstens alle 30 min (nie, wenn von Anfang an leer) |
| k2_chamber | PTC-Lüfterfehler, Sperre | `!! k2_chamber: …` | `!! [k2_chamber] …` (Format vereinheitlicht) |
| k2_chamber | Drehzahl nicht lesbar | nur Log (je Sekunde Traceback) | `//` höchstens alle 10 min |
| k2_chamber | Ausnahme im Überwachungs-Timer | Wurf → Klipper-Shutdown | kein Wurf: Sperre + Ziel 0 + `!!` |
| k2_heater_pid | Fehler im Umschalt-Timer | nur Log | `!!` höchstens alle 10 min |
| k2_limits | `axis_minimum` nicht nachgezogen (Connect) | nur Log | gepuffert `//` |
| k2_motor | transparent-Setup, Auto-Init übersprungen/abgebrochen (Connect) | `respond_info` beim Connect (verloren) | gepuffert `!!` |
| k2_motor | Auto-Init-Ergebnis (z. B. key798) | alle Zeilen `respond_info` beim Connect (verloren) | Fehlerzeilen `!!` + Abschlusszeile „Motor-Init (Start): Status …“ (`!!` wenn nicht bereit); Info-Zeilen nur ins Log |
| k2_motor | Stall-Fehlercodes / Aktionen bei Fehlern | `// [k2_motor] Stall …` | Fehler und M84/Shutdown/PAUSE-Aktionen `!!`, reine Warnungen `//` |
| k2_motor | Controller-`logger` | gcode (lief ins Leere) | `LogAdapter` → klippy.log (live, mit Zeitstempel) |
| k2_nozzle_tempcomp | kein print_stats, bed_mesh nicht in der Kette (Connect) | nur Log | gepuffert `//` |
| k2_prtouch | Query-Lookup / apax-Lookup (Connect) | `respond_info` beim Connect (verloren) | gepuffert `!!` bzw. `//` |
| k2_prtouch | Temperatur-/Notfall-Config-Warnungen beim Rückfall-Antasten | `logger=gcode` (lief ins Leere) | `// [k2_prtouch] Warnung: …` |
| k2_safety | fakeshutdown | `!! k2_safety: …` | `!! [k2_safety] …` sofort; nennt Heizer, deren Abschalten fehlschlug |
| k2_safety | Kappung, fehlender Heizer, `fakeshutdown_guard: False` | teils nur Log / beim Start verloren | `//` (Start-Meldungen gepuffert) |
| k2_zalign | Aufräumen nach Abbruch gescheitert | nur Log | in der roten Abbruchmeldung „ACHTUNG: Z-Homing-Status nicht zurückgesetzt …“ |
| k2_lis2dw, k2_resume, k2_transport | – | Fehler bereits als G-Code-Fehler, keine Timer/Start-Meldungen | unverändert |

Nicht geändert (bewusst): stille `except: pass` für reine Kosmetik/Diagnose
(z. B. Odometer-Spur, Konsolen-Fallbacks), in Serial-Thread-Handlern
(PrTouch apax/feed) und dort, wo eine Rücklesung den Fehler ohnehin meldet
(GPIO-`sync` im Motor-Init).

## Bus-Statistik-Wächter `[k2_485]`

Reactor-Timer alle 60 s ab „ready“ (kein Bus-Verkehr, liest nur die Zähler).
Die ersten 180 s werden nicht gewertet (Suchläufe beim Start). Erreicht die
Zunahme innerhalb des Fensters eine Grenze, kommt **eine** Warnung mit den
Zahlen; danach beginnt das Fenster neu, höchstens alle 30 min:

```
// [k2_485] RS485-Bus: in den letzten 10 min 41 Timeouts, 0 CRC-Fehler,
//   12 Wiederholungen bei 380 Sendungen (Grenze erreicht: timeouts) – …
```

| Option | Default | Bedeutung |
|---|---|---|
| `warn_window` | 600 | Fenster in s |
| `warn_timeouts` | 30 | Timeouts je Fenster (0 = aus) |
| `warn_crc_errors` | 10 | CRC-Fehler je Fenster (0 = aus) |
| `warn_retries` | 30 | Wiederholungen je Fenster (0 = aus) |

Alle drei Grenzen 0 → kein Timer. Bei ausgeschalteter CFS-Box laufen deren
Abfragen ins Leere – dann warnen Bus und Box (Box: „antwortet nicht“).
Die Grenzen sind geschätzt (keine Live-Zählerstände im Normalbetrieb); nach
einigen Drucken mit `KTWO_BUS_STATUS` prüfen und ggf. anpassen.

## Einbau

`k2_konsole.py` ist kein Klipper-Abschnitt (kein `[k2_konsole]` in der Config),
muss aber neben den anderen Modulen in `klippy/extras/` liegen – `install.sh`
kopiert alle `pi-host/k2_*.py` und erfasst die Datei damit automatisch. Bei
Handkopie: `k2_konsole.py` **mit** kopieren, sonst startet Klipper mit
„No module named k2_konsole“ nicht. Wirksam nach `sudo systemctl restart
klipper` (Modulcode; `FIRMWARE_RESTART` lädt Python-Module nicht neu).

Tests: `python3 test_k2_konsole.py` (Helfer + Prüfung, dass jedes Modul mit
Warn-/Fehler-Log den Helfer einbindet und kein Controller mehr gcode als
`logger` bekommt) sowie die Konsolen-Tests in den Modul-Tests.
