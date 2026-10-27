#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_box.py – Klipper-Zusatzmodul [k2_box] für die CFS-Box (K2 Plus).

Offener Ersatz für Crealitys ``box_wrapper.so`` (Stock, armhf, proprietär),
soweit der Box-Förderpfad betroffen ist. Gegenstück zu ``k2_motor_init.py``
(``[k2_motor]``): gleiche Stilregeln – reine Logik ohne Klipper testbar,
MCU-/Port-Zugriffe gekapselt, Schreiben nur mit ``allow_write``.

Grundlagen (nur gelesen, nichts gesendet):
- nachbau/box.py (ParseData/BoxState/BoxAction – WIEDERVERWENDET, nicht neu
  erfunden: RFID/Remain/Messrad/Box-State/Version/Buffer/Sensor/Hardware-
  Status, CfsStatusError/StatusBytes, FUNC_RETRIES, preload_force_timeout)
- nachbau/cfsseq.py (Extrude-/Retrude-Live-Sequenzen – WIEDERVERWENDET:
  build_extrude_sequence/build_retrude_sequence, EXTRUDE_LIVE_CMDS_B,
  RETRUDE_LIVE_CMDS_A, EXTRUDE_MODE_*/RETRUDE_P_*, slot_to_mask)
- nachbau/boxmini.py (has_filament-Semantik: remain 0 = unbekannt, nie False)
- nachbau/auto_addr.py (Adressvergabe – WIEDERVERWENDET via Adapter)
- nachbau/serial485.py (build_frame/parse_frame/CRC-8, Bus-Lock MAX_BUS_HOLD_S)
- Protokollanalyse RS485 (Func-Tabelle CFS-App R153, Live-Gegenprobe: Retry-/
  Timeout-Tabelle, Extrude/Retrude live, 0x0a-Layout,
  Schutzmechanismen; Sicherheitsregeln: nie 0x50/0x06, keine Bursts,
  ein gemeinsamer RS485-Transport/Lock für Motoren+CFS+Belt auf ttyS5)
- Analyse der CFS-Firmware
  (0x0d op 0-3, 0x10 8 Modi, 0x11 p 0/1, 0x0a-Bytes Temp/Feuchte/Block/Hub,
  Status-Codes 0x0c/0x16/0x19/0x30/0x50/0x51/0x52, 0x06-FW-Bug, 0xEA/0xEB)
- Live-Abfragen 16.10.2026 (nur Motor – hier als Negativbeleg: Box braucht
  keinen Transparent-Pfad, nur RS485 0x01)
- Live-Test Kanal A (echter Live-Ablauf Preload/Extrude/
  Retrude + Fehler 0x0c bei ~245 mm und 0x19 beim Rückzug – als Testvorbild)
- pi-host/k2_transport.py (Rs485Transport-Schnittstelle + nachbau-Suche
  ensure_nachbau_path/check_nachbau_module; Bus-Lock-Teilung: EIN Port, EINE Queue, EIN Lock)
- Original-Makros der Stock-Firmware box.cfg + gcode_macro.cfg
  (BOX_LOAD_MATERIAL-Kette HEATING→CUT→RETRUDE→EXTRUDE→FLUSH→END,
  BOX_QUIT_MATERIAL-Kette, M8200 P/C/R/L/W/F/O → CR_BOX_PRE_OPT/CUT/
  RETRUDE/EXTRUDE/WASTE/FLUSH/END_OPT, BOX_CUT_MATERIAL) sowie
  die Live-box.cfg des Druckers ([box] bus:serial485, Tn_retrude -10,
  Tn_extrude 140/220 °C/360, buffer_empty_len 30 – reine Doku, keine
  Toolhead-Bewegung hier)

Ablauf (wie Stock/Box-Live, nur Box-Seite – kein Toolhead/Motor):
  A0 Adressvergabe via AutoAddr (erst nach ``k2_motor:ready`` – gleiche
     Reihenfolge wie Stock: Motor-Init → auto_addr:start; hier Event)
  A1 zyklischer Status-Poll 0x0a (retries=0, alle ~4 s wie Original)
  A2 lesend jederzeit: RFID (0x02), Remain (0x03), Version/SN (0x14),
     Hardware-Status (0x15), Sensor (0x08), Buffer (0x05), Messrad (0x0e [01])
  A3 Preload 0x0d op 0-3 (op 2/3 lange Timeouts + retries=0, keine Bursts)
  A4 Extrude (Load, Box-Seite): 0x04 [00 01] IDLE → 0x0f [01] an →
     0x08 [00] Sensor → 0x10 [m 00 00] Stage1 → 0x10 [m 04 00] Start →
     0x10 [m 05 00] x5 Odometer-Polls → 0x10 [m 06 00] Stopp/Buffer →
     0x10 [m 07 03] Extruder-Check (p=3 mm) → 0x05 Buffer →
     0x04 [m 00] PRINT → 0x0f [00] aus. Byte-identisch zu
     cfsseq.build_extrude_sequence(mask, pushes=5) == EXTRUDE_LIVE_CMDS_B
     (Beleg Live-Mitschnitt, cfsseq-Selbsttest).
     Abbruch bei Status != 0 mit CfsStatusError-Text (z.B. 0x0c).
  A5 Retrude (Unload, Box-Seite): 0x04 [00 01] → 0x08 [00] →
     0x11 [m 00] x4 → 0x11 [m 01] (Beleg RETRUDE_LIVE_CMDS_A gefiltert,
     cfsseq.build_retrude_sequence; 0x0e-/0x03-Zwischenabfragen sind
     Diagnose und nicht Teil der Sequenz).
  A6 Cut als Platzhalter (braucht Motor/Toolhead – sendet NICHTS).

Sicherheit (aus der Protokoll- und Firmware-Analyse):
- Schreiben (0x0d/0x04/0x0f/0x10/0x11, Auto-Addr 0xA0, GPIO gibt es hier
  nicht) nur mit ``allow_write: True`` (Default False → nur lesen + Plan).
  Lesend (0x02/0x03/0x0a/0x0e-lesen/0x14/0x15/0x05/0x08) jederzeit.
- NIE 0x50 (Config #6 schreiben + Reset) und NIE 0x06-Feeder senden
  (0x50 resettet die Box, 0x06 hat FW-Bug Maske aus r7 → immer Status 1;
  Beleg Func-Tabelle). Guard im Code + Test.
- Keine Bursts: Mechanik-Kommandos (0x10/0x11, 0x0d op 2/3) mit retries=0
  und langen Timeouts (Box antwortet erst nach dem Vorgang; ein Retransmit
  würde ihn doppelt starten; die Box-FW hat keine Duplikaterkennung).
  Original-Retries für den Rest via box.func_retries (0x0a Poll retries=0).
- Bus-Lock mit [k2_motor] teilen: EIN gemeinsamer RS485-Transport/Lock
  (ttyS5, 230400, Halbduplex, Lock über die ganze Transaktion, vgl.
  serial485 MAX_BUS_HOLD_S). Dieses Modul öffnet den Port NICHT selbst,
  sondern bekommt ``bus`` injiziert – dieselbe bus-Instanz wie [k2_motor].

Klipper-Einbindung (Dateien kopieren, s. pi-host/README.md): ``k2_box.py`` nach
``klippy/extras/`` (als ``k2_box.py``), ``[k2_box]`` in printer.cfg.
Alle Bus-Zugriffe laufen über BoxAction (gekapselt, Tests mit Fake-Bus).
Ohne Klipper importierbar (reine Logik + Fakes, s. test_k2_box.py).

G-Codes:
- ``KTWO_BOX_STATUS`` – frischer 0x0a-Poll (lesend) + Bericht.
- ``KTWO_BOX_LOAD SLOT=A..D [DRY_RUN=1]`` – Box-Load (Extrude-Ablauf A4).
  Default: Plan ausgeben, nichts senden (ohne allow_write bzw. mit DRY_RUN).
- ``KTWO_BOX_UNLOAD SLOT=A..D [DRY_RUN=1]`` – Box-Unload (Retrude-Ablauf A5).
  LOAD/UNLOAD (18.10.2026): Düse unter min_extrude_temp (bzw. can_extrude
  False, unlesbar) -> G-Code-Fehler, NICHTS gesendet (heizen nicht selbst).
- ``KTWO_BOX_PRELOAD SLOT=A..D OP=0..3 [DRY_RUN=1]`` – 0x0d op (0 disable,
  1 enable, 2 Force mit RFID, 3 Force ohne RFID).

Filamentwechsel (Ergänzung 16.10.2026, Ersatz für CR_BOX_* / M8200-Kette /
Stock-Tn aus box_wrapper.so – Details s. README-k2_box.md §8 und
config-pi/README.md). Jeder Schritt ist einzeln aufrufbar und ein
Glied der Makrokette (config-pi/macros.cfg: T0–T15, M8200):

- ``KTWO_BOX_PRE_OPT [TNN=T1A] [Z_HOP=mm] [SOURCE=T]`` (= M8200 P /
  CR_BOX_PRE_OPT): Kette beginnen, Kopfsensor sperren, Zustand sichern,
  Z-Hop. SOURCE=T = T<n>-Befehl (nachholbar, s. unten).
- ``KTWO_BOX_CUT`` (= M8200 C / CR_BOX_CUT): Schneidfahrt
  pre_cut_pos → cut_pos_x (SAVE_CONFIG-Wert) → zurück.
- ``KTWO_BOX_GO_EXTRUDE_POS`` / ``KTWO_BOX_SAFE_POS``: in den / aus dem
  Abfallschacht (Stock BOX_GO_TO_EXTRUDE_POS / BOX_MOVE_TO_SAFE_POS).
- ``KTWO_BOX_RETRUDE [SLOT=] [LENGTH=]`` (= M8200 R): Box 0x11 p0×4 + p1,
  dazwischen Extruder-Rückzug Tn_retrude (live 3× −10 mm).
- ``KTWO_BOX_EXTRUDE TNN=T1A|SLOT=A`` (= M8200 L I0): Box-Einzug bis zum
  Kopfsensor, Stage 7 (Extruder greift), Box PRINT.
- ``KTWO_BOX_FLUSH [TNN=] [TOOL=n] [LENGTH=] [VELOCITY=] [TEMP=|BASE_TEMP=]``
  (= M8200 F): Spülen wie Stock (17.10.2026):
  Menge farbabhängig (nachbau/flush.py, unbekannt -> 90 + 70 mm),
  Abschnitte cal_flush_list, 20 % v + 80 % 1,2·v, Temperatur/Geschwindig-
  keit aus der Material-DB (``material_database``, aus = bisher), Blasen,
  Blech nach jedem Abschnitt, Silikon-Kreise nur am Ende (README §12).
  ``flush_mode: matrix`` (Default seit 18.10.2026, E4 = Stock): im Druck
  Slicer-Spülmatrix aus der Druckdatei, Index TOOL= (README §12.4); der
  ERSTE Einzug eines Drucks nimmt wie Stock den Farbweg (altes Werkzeug in
  diesem Druck unbekannt, E5/A4). Nach Erfolg Düsen-Material
  merken, mit ``remain_material_file`` atomar gespeichert (nur bei
  Änderung; Stock remain_material.save).
- ``KTWO_BOX_REMAIN [COLOR=] [TYPE=] [TNN=] [CLEAR=1]`` (Nicht-Stock):
  Düsen-Material anzeigen/korrigieren.
- Restmaterial (17.10.2026,
  README-k2_box.md §12.5): meldet die Box im Druck Status 0x50 (Spule leer,
  Rest im Schlauch), spült ``KTWO_BOX_PRE_OPT`` den Rest wie Stock
  extrusion_all_materials aus (80-mm-Stücke mit max_vol/2,4·60, Blasen +
  Blech, bis Kopfsensor leer + 1 Stück, max. ``max_tube_length``) statt
  Schnitt + Rückzug (``ending_material_purge``, Default AN; Kopfsensor
  unbekannt = Pause, nie blind). ``KTWO_BOX_ENDING [SET=1 SLOT=] [CLEAR=1]
  [PURGE=1]`` zeigt/setzt/löscht den Fall bzw. spült von Hand.
- ``KTWO_BOX_END_PRINT [UNLOAD=0|1]`` (= Stock BOX_END, aus END_PRINT):
  Restmaterial ausspülen; mit ``end_print_unload`` (Default AN seit
  18.10.2026, Stock entlädt immer) heizen, schneiden, in die Box zurück. Kein Spülen am Druckende; Fehler nur
  Meldung (keine Pause, END_PRINT läuft weiter).
- ``KTWO_BOX_PRINT_START`` / ``KTWO_BOX_PRINT_END`` (18.10.2026, = Stock
  BOX_START_PRINT / BOX_END_PRINT, A2): aus
  START_PRINT bzw. END_PRINT (nach KTWO_BOX_END_PRINT), nur mit Freigabe.
  Start 0x0d [0f 00] + 0x0f [01], Ende 0x0d [0f 01] + 0x0f [00]; dazwischen
  schaltet der Einzug 0x0f nicht um. Fehler nur Meldung.
- Druckabbruch (18.10.2026, C2/C3, README §17):
  ``KTWO_BOX_END_PRINT CANCEL=1`` (aus CANCEL_PRINT) entlädt auch bei
  offenem Wechsel-Fehler, wenn sicher (Fehler bei EXTRUDE/FLUSH/WASTE +
  Kopfsensor Filament), sonst Meldung; ``KTWO_BOX_CANCEL_RESET [START=1]``
  (nach CANCEL_PRINT_BASE) bzw. KTWO_BOX_PRINT_START setzen chain_error/
  aborted zurück – nie in einer Pause. ``cancel_interrupt``: Webhook
  pause_resume/cancel eingehüllt, lange Warteschleifen enden ohne PAUSE.
- Live-Abgleich 18.10.2026 (A1/A3): T<n>-Kette bei sicher leerem Kopf und
  leerem Hub -> nur laden (kein Schnitt/Rückzug, Stock-Erstladen);
  Extruder-Rückzug je p0 mit der Spülgeschwindigkeit des Düsenmaterials
  (Material-DB, live F350), sonst tn_retrude_velocity (600).
- ``KTWO_BOX_WIPE`` (Abstreifen, Stock-T0-Beispiel 10.1–10.3),
  ``KTWO_BOX_WASTE`` (= M8200 W, hier nur Hinweis),
  ``KTWO_BOX_END_OPT [RETURN=0|1]`` (= M8200 O), ``KTWO_BOX_WAIT``
  (Box-Zustand abwarten, Stock BOX_MODE_WAIT), ``KTWO_BOX_ERROR_CLEAR
  [HEAD_LOADED=1]``,
  ``KTWO_BOX_RESUME_FIX [CLEAR=1]`` (aus RESUME, Z nach Abbruch im
  Wechsel; CLEAR=1 aus PAUSE = Z steckt im Rückfahrziel, s. unten).

Abbruch im laufenden Druck (17.10.2026, config-pi/README.md):
``PAUSE CFS_RETURN_X/Y/Z=<Druckposition vor dem Wechsel>`` statt der
Fehlerposition (z. B. am Schneidhebel X -7,8 / Y200 oder im Schacht) – das
PAUSE-Makro merkt sie als Rückfahrziel, RESUME fährt dorthin und setzt die
Upstream-PAUSE_STATE darauf (RESUME_BASE = Nullfahrt). Fahrten in den Schacht
verlassen den Schneidbereich (X < pre_cut_pos_x) zuerst nur in X. Hatte der
Wechsel schon auf die Wechsel-Temperatur geheizt (M109, max(Ziel, 220)),
bekommt PAUSE zusätzlich ``CFS_TEMP=<Druck-Zieltemperatur vor dem Wechsel>``
– das Makro merkt sie statt des aktuellen Ziels als hotend_temp, RESUME
heizt also wieder auf die Drucktemperatur (Fix 17.10.2026 spät).

RESUME nach abgebrochenem Wechsel (17.10.2026 spät, README-k2_box.md §11):

- ``_fail`` merkt den Abbruch (Schritt, Ziel-TNN, Herkunft – Stock
  ``recode_err``/``save_resume_tnn``); Status ``change_aborted``,
  ``resume_tnn``, ``head_filament``.
- ``KTWO_BOX_RESUME_CHECK [MOVE=0|1] [TEMP=]`` (aus dem RESUME-Makro, vor
  jeder Bewegung): Kopf-Filamentsensor meldet Filament -> RESUME frei;
  LEER -> RESUME gesperrt (Druck bleibt pausiert, Meldung: T<n> in der
  Pause, dann RESUME); unbekannt (kein Sensor) -> gesperrt bis
  ``KTWO_BOX_ERROR_CLEAR HEAD_LOADED=1``. Mit ``resume_redo_change: True``
  (Default False) + beiden Freigaben + MOVE=1 holt die vorhandene Kette
  einen abgebrochenen T<n>-Wechsel nach (Stock ``error_resume_process``),
  jeder Fehler sperrt RESUME (Stock ``resume_err``).

Auto-Nachladen bei Runout (A7, 17.10.2026 – Bedienung/ANNAHMEN in
config-pi/README.md, README-k2_box.md §9):

- ``KTWO_BOX_RUNOUT [SLOT=A..D] [TEMP=] [DRY_RUN=1]`` (= Stock
  BOX_CHECK_MATERIAL_REFILL aus printer.cfg runout_gcode): leerer Slot ->
  Ersatz-Slot (Gleich-Material-Gruppe oder RFID gleiches Material + Farbe,
  Filament am Einlauf) -> Kette PRE_OPT/E30/EXTRUDE/FLUSH/END_OPT -> RESUME.
  Nur mit ``runout_refill`` (Default AUS) UND beiden Freigaben; sonst bzw.
  bei jedem Fehler bleibt der Druck pausiert (Meldung, nie Shutdown).
- ``KTWO_BOX_AUTO_REFILL [ENABLE=0|1]`` (= Stock BOX_ENABLE_AUTO_REFILL),
  ``KTWO_BOX_SAME_MATERIAL [LIST=T1A,T1C/T1B,T1D] [CLEAR=1]`` (= Stock
  BOX_UPDATE_SAME_MATERIAL_LIST, Format ANNAHME).

Externe Spule (19.10.2026, README-k2_box.md §18):

- ``KTWO_BOX_SOURCE [SOURCE=EXTERN|CFS]`` – Filamentquelle anzeigen/setzen
  (Makros SET_FILAMENT_SOURCE SOURCE=EXTERNAL|CFS). Der Schlauch wird von Hand umgesteckt;
  der Pi kann das nicht erkennen -> Zustand vom User, persistent in
  ``filament_source_file`` (fehlt/kaputt -> CFS). Bei Quelle EXTERN lehnt
  k2_box JEDE CFS-Aktion mit Box-Schreibbefehl oder Filamentbewegung der CFS
  ab (nichts gesendet): KTWO_BOX_LOAD/UNLOAD/PRELOAD, PRE_OPT/RETRUDE/
  EXTRUDE/FLUSH (T<n>-/M8200-Kette), Runout-Nachladen, AUTO_REFILL ENABLE=1,
  Nachholen im RESUME, Ausspülen (ENDING PURGE=1), END_PRINT-Entladen
  und 0x04 aus ERROR_CLEAR/Reset. Erlaubt bleiben lesendes Polling (0x0a),
  KTWO_BOX_RFID und – wie Stock live – KTWO_BOX_PRINT_START/END mit
  genau 0x0d [0f 00/01] + 0x0f [01/00] (EXTERN_ALLOWED_FRAMES).
- ``KTWO_BOX_EXTERN_TOOL T=<n>`` (aus T<n>/M8200 bei Quelle EXTERN): erstes
  Werkzeug eines Drucks = kein Wechsel, ANDERES Werkzeug im Druck -> PAUSE.
- ``KTWO_BOX_EXTERN_MATERIAL [MATERIAL=] [TYPE=] [COLOR=] [TEMP=] [CLEAR=1]``
  – gespeichertes externes Material (Stock-Display: Material + Temperatur).
- Lade-/Entlade-Bausteine (Makros LOAD_FILAMENT/UNLOAD_FILAMENT = Stock
  LOAD_MATERIAL/QUIT_MATERIAL): ``KTWO_BOX_EXTERN_BEGIN OP=LADEN|ENTLADEN``
  (Prüfungen, Material/Temperatur, Zustand sichern), ``KTWO_BOX_EXTERN_FLUSH``
  (Spülen wie CFS-Wechsel, Vorgänger = remain_material), ``KTWO_BOX_EXTERN_
  HEAT``, ``KTWO_BOX_EXTERN_RETRACT`` (E-10 F360), ``KTWO_BOX_EXTERN_END``
  (Abkühlen wie Stock SET_COOL_TEMP, Zustand zurück).
- ``KTWO_EXTERN_RFID`` (optional, ``extern_rfid``, Default AUS): Etikett der
  externen Spule lesen (0x11 [02], Auslöser !PA5 per [gcode_button]).

Freigaben: Box-Frames nur mit ``allow_write``, Kopf-/Extruder-Bewegung und
Heizen nur mit ``allow_move`` (beide Default False → nur Plan). Im Druck
(print_stats printing/paused) braucht ein Wechsel BEIDE Freigaben, sonst
Pause. Fehler im Druck → PAUSE (kein Shutdown, kein Druckabbruch),
Folgeschritte der Kette werden übersprungen; außerhalb eines Drucks →
G-Code-Fehler. Lange Box-Antworten (mode 0 ~4,5 s, 0x11 p1 bis 23 s) laufen
in einem Hilfs-Thread, der Reactor pausiert derweil (ReactorBusProxy).

Konsole (23.10.2026, k2_konsole): Alle Start-Meldungen (Connect: Dateien
Düsen-Material/Slot-Handwerte/Quelle, Material-DB, Bus fehlt, Auto-Addr nach
k2_motor:ready) kamen vorher nie in Mainsail an – jetzt gepuffert bis kurz
nach „ready“. Neu aus dem 0x0a-Poll-Timer (vorher still verschluckt):
``// [k2_box] CFS-Box antwortet nicht …`` nach POLL_WARN_FAILS Fehlschlägen
in Folge (höchstens alle 30 min, Entwarnung „antwortet wieder“) und
``!! [k2_box] CFS meldet Fehler 0x.. …`` beim Wechsel in den Box-Zustand
ERROR außerhalb eines bereits gemeldeten Wechsel-Fehlers.
"""

import json
import logging
import math
import os
import re
import struct
import threading
import time

# Import-Regel (Fix 16.10.2026, s. k2_transport.py): eigener Ordner NIE in
# sys.path; Geschwister relativ (Klipper-Paket extras), Fallback absolut für
# die Offline-Tests; nachbau/ hängt k2_transport.ensure_nachbau_path() an.
try:
    from . import k2_transport as _K2T
except ImportError:
    import k2_transport as _K2T
try:
    from . import k2_konsole as _K2K
except ImportError:
    import k2_konsole as _K2K
_NACHBAU = _K2T.ensure_nachbau_path()

try:
    import box as _BOX  # noqa: F401
    _K2T.check_nachbau_module(_BOX)
    from box import (BoxAction, CfsStatusError, StatusBytes,  # noqa: F401
                     func_retries, preload_force_timeout, status_name,
                     parse_box_state, get_rfid, get_remain_len,
                     get_measuring_wheel, parse_version_sn,
                     parse_buffer_state, parse_filament_sensor_state,
                     parse_get_hardware_status, get_box_status,
                     BOX_STATE_NAMES, BOX_STATE_IDLE, BOX_STATE_ERROR,
                     PRELOAD_DISABLE, PRELOAD_ENABLE, PRELOAD_FORCE_READ,
                     PRELOAD_FORCE_TENSION_ONLY)
    _HAVE_BOX = True
except Exception as _e:  # pragma: no cover
    raise ImportError("nachbau/box.py wird benötigt (gleicher Stil wie "
                      "k2_motor_init.py -> k2_transport.py): %s" % _e)

try:
    import cfsseq as _SEQ  # noqa: F401
    _K2T.check_nachbau_module(_SEQ)
    from cfsseq import (slot_to_mask, RETRUDE_TO_BUFFER_REPEATS,  # noqa: F401
                        EXTRUDE_MODE_STAGE1, EXTRUDE_MODE_FEED_START,
                        EXTRUDE_MODE_QUERY, EXTRUDE_MODE_STOP_BUFFER,
                        EXTRUDE_MODE_EXTRUDER_CHECK, EXTRUDER_CHECK_MM,
                        RETRUDE_P_TO_BUFFER, RETRUDE_P_PULL_HUB,
                        EXTRUDE_LIVE_CMDS_B, RETRUDE_LIVE_CMDS_A,
                        build_extrude_sequence, build_retrude_sequence,
                        requests_from_log_cmds, filter_funs)
    _HAVE_SEQ = True
except Exception as _e:  # pragma: no cover
    raise ImportError("nachbau/cfsseq.py wird benötigt: %s" % _e)

try:
    import auto_addr as _AA  # noqa: F401
    _K2T.check_nachbau_module(_AA)
    _HAVE_AA = True
except Exception as _e:  # pragma: no cover
    raise ImportError("nachbau/auto_addr.py wird benötigt: %s" % _e)

try:
    import serial485 as _S485  # noqa: F401
    _K2T.check_nachbau_module(_S485)
    _HAVE_S485 = True
except Exception:  # pragma: no cover
    _S485 = None
    _HAVE_S485 = False

# Spül-Rechnung + Material-DB (17.10.2026). WEICH importiert: fehlen sie
# (alte Installation), gilt das bisherige Verhalten (feste Spülmenge
# [90, 70], max(Ziel, Tn_extrude_temp), feste Geschwindigkeiten) + Meldung.
try:
    import flush as _FLUSH  # noqa: F401
    _K2T.check_nachbau_module(_FLUSH)
except Exception:  # pragma: no cover
    _FLUSH = None
try:
    import material_db as _MDB  # noqa: F401
    _K2T.check_nachbau_module(_MDB)
except Exception:  # pragma: no cover
    _MDB = None
# Externe Spule (19.10.2026): RFID-Antwort wie Stock filament_rack send_data
# (nachbau/filament_rack.rfid_from_response). WEICH importiert: fehlt es,
# dekodiert k2_box die Antwort selbst gleich (ret[5:-1] als ASCII).
try:
    import filament_rack as _RACK  # noqa: F401
    _K2T.check_nachbau_module(_RACK)
except Exception:  # pragma: no cover
    _RACK = None

# =====================================================================
# Konstanten (Belege s. Docstring; Werte aus box.py/cfsseq.py wiederverwendet)
# =====================================================================

BOX_ADDR_DEFAULT = _BOX.BOX_ADDR  # 0x01 (Protokollanalyse)
SLOT_LETTERS = "ABCD"
SLOT_MASKS = (0x01, 0x02, 0x04, 0x08)
MASK_ALL = 0x0F

# NIE senden (Guard, Beleg Func-Tabelle der Protokollanalyse):
# - 0x50 schreibt Config #6 + speichert + resettet (gleicher Wert -> Status 3).
# - 0x06 Feeder hat FW-Bug (Maske aus r7 = Tick-Wert, praktisch immer Status 1).
FORBIDDEN_FUNS = (0x50, 0x06)

FUNC_GET_BOX_STATE = _BOX.FUNC_GET_BOX_STATE  # 0x0a
FUNC_SET_BOX_MODE = _BOX.FUNC_SET_BOX_MODE  # 0x04
FUNC_PRELOAD_INHIBIT = _BOX.FUNC_PRELOAD_INHIBIT  # 0x0f
FUNC_EXTRUDE = _BOX.FUNC_EXTRUDE  # 0x10
FUNC_RETRUDE = _BOX.FUNC_RETRUDE  # 0x11

POLL_INTERVAL_DEFAULT = 4.0  # s, Original-Poll alle ~4 s (Live-Mitschnitt, 10 560 Polls)
POLL_WARN_FAILS = 3          # Poll-Fehlschläge in Folge -> Konsolenwarnung
POLL_WARN_PAUSE = 1800.      # s, gleiche Poll-Warnung höchstens so oft
# Box nach Stromverlust neu anmelden: Auto-Addr-Durchlauf frühestens wieder
# nach auto_addr.next_interval (Stock process_all: 1 s, im Druck/Pause 10 s)
ODO_POLLS_DEFAULT = 5  # Auftrag A4: 5 Odometer-Polls (Beleg EXTRUDE_LIVE_CMDS_B)
RETRUDE_REPEATS_DEFAULT = RETRUDE_TO_BUFFER_REPEATS  # 4 (Live-Mitschnitt, 16 von 26)
# Stock-„Rückzug ohne Box“ (M8200 R E<x> / CR_BOX_RETRUDE LENGTH≠0 ->
# BoxAction.break_box_extruder_retrude, box_wrapper Z.4010–4018 @0xd9da8):
# Extruder-Schritt mit check_and_extrude(length, 300) = G0 E<length> F300.
BREAK_RETRUDE_VELOCITY = 300.0

PRELOAD_OP_NAMES = {
    PRELOAD_DISABLE: "disable (Insert-Reading/Preload aus)",
    PRELOAD_ENABLE: "enable (Insert-Reading/Preload an)",
    PRELOAD_FORCE_READ: "force-read (Forced Preload MIT RFID)",
    PRELOAD_FORCE_TENSION_ONLY: "tension-only (Forced Preload OHNE RFID)",
}


# ---------------------------------------------------------------------
# Filamentwechsel: Stock-Werte (Optionen im Stock-Stil, Namen wie [box])
# ---------------------------------------------------------------------
# Belege: Stock-box.cfg des Druckers (16.10.2026, [box]-Abschnitt +
# kommentiertes T0-Beispiel), printer.cfg SAVE_CONFIG ``[box] cut_pos_x =
# -7.80`` (Cut-Kalibrierung 16.10.2026),
# Live-Wechsel Live-Log 2026-10-12 00:24:49–00:26:35 (Unload+Load T1A:
# 3x "extrude = -10.0, velocity (600,)", "length: [90, 70]", "extrude =
# 160, velocity 360, temp 220"), Live-Log 2026-10-13 02:31 ("timeout =
# 90" beim Einzug, stage7). Kleinbuchstaben, weil Klipper Optionsnamen
# klein schreibt (Stock "Tn_retrude" -> "tn_retrude").
CHANGE_DEFAULTS = {
    # Schneiden (box.cfg + SAVE_CONFIG; T0-Beispiel Schritt 3-5)
    "pre_cut_pos_x": 10.0,
    "pre_cut_pos_y": 200.0,
    "cut_pos_x": -7.80,          # SAVE_CONFIG-Wert (kalibriert 16.10.2026)
    "cut_pos_y": 200.0,
    "cut_velocity": 30000.0,     # mm/min
    "cut_accel": 5000.0,         # T0-Beispiel SET_VELOCITY_LIMIT ACCEL=5000
    # ANNAHME: kurz am Anschlag verweilen. Batch-Lauf 16.10.2026: mit
    # Input-Shaper (mzv 42 Hz) kehrt der Kopf ohne Halt schon bei X -7,69
    # statt -7,80 um (dekodierte MCU-Schritte, Offline-Batch-Test).
    "cut_dwell": 0.1,            # s
    "check_cut_pos_x_min": -9.5,
    "check_cut_pos_x_max": -5.5,
    # Extruder (box.cfg)
    "tn_retrude": -10.0,         # mm je Schritt (live 3x)
    "tn_retrude_velocity": 600.0,
    "tn_extrude_temp": 220.0,    # Fallback-Temperatur (Stock Tn_extrude_temp)
    # Stock-Fallback der Spülmenge ohne bekannte Farben (get_flush_len gibt
    # Tn_extrude ROH zurück, cal_flush_list(140) = [90, 70])
    "tn_extrude": 140.0,
    "tn_extrude_velocity": 360.0,
    "buffer_empty_len": 30.0,    # Schneide -> Extruderzahnrad
    # Schacht / Abstreifer (box.cfg + T0-Beispiel Schritt 6, 10, 11)
    "extrude_pos_x": 133.0,
    "extrude_pos_y": 378.0,
    "extrude_entry_pos_x": 139.0,  # T0-Beispiel 6.1 "G0 X139"
    "safe_pos_x": 225.0,
    "safe_pos_y": 345.0,
    "clean_left_pos_x": 135.0,
    "clean_left_pos_y": 378.0,
    "clean_right_pos_x": 160.0,
    "clean_right_pos_y": 378.0,
    "clean_velocity": 12000.0,
    "clean_pos_left_x": 160.0,
    "clean_pos_right_x": 170.0,
    "clean_pos_middle_y": 374.0,
    "wipe_circle_radius": 4.0,   # T0-Beispiel G2 I4 / G3 I-4
    "wipe_circle_velocity": 10000.0,
    "wipe_accel": 5000.0,
    "wipe_shakes": 3,            # T0-Beispiel 10.3 (3x 160 <-> 133)
    "box_first_clean_length": 90.0,  # live "length: [90, 70]"
    "box_need_clean_length": 70.0,
    # Spülen wie Stock (BoxCfg-Defaults):
    # cal_flush_list-Höchstlänge je Abschnitt, Düsenvolumen, Faktoren
    "box_need_clean_length_max": 100.0,
    "nozzle_volume": 183.0,
    "flush_multiplier": 1.0,
    "diff_material_type_min_flush_len": 370.0,
    "flush_velocity_factor": 1.2,    # 80 %-Teil je Abschnitt (außer letzter)
    "max_temp_flush_times": 3,
    "baseline_nozzle_diameter": 0.4,
    # Bauteillüfter beim Spülen (Stock M106 P0 S50 + blow: fan0 5 s voll)
    "flush_fan_start": 50.0,         # M106 P0 S<..> zu Beginn
    "flush_blow_time": 5.0,          # s
    # Reinigung (Stock nozzle_clean): Kreis-Startversatz monotonic() % 3,
    # SET_VELOCITY_LIMIT ACCEL=5000 ACCEL_TO_DECEL=5000 -> Upstream-
    # Äquivalent MINIMUM_CRUISE_RATIO=0 (accel_to_decel == accel)
    "flush_wipe_offset": 3.0,        # mm, 0 = immer dieselbe Stelle
    "wipe_minimum_cruise_ratio": 0.0,
    # Verstopfungsprüfung (Stock key845, |Messrad-Summe| < |diff_length|)
    "flush_clog_min_mm": 10.0,
    "travel_velocity": 12000.0,  # T0-Beispiel F12000
    "z_velocity": 600.0,         # T0-Beispiel G0 Z.. F600
    "change_z_hop": 0.4,         # T0-Beispiel "G0 Z50.4" bei Z50
    # Wartezeiten (live belegt bzw. ANNAHME)
    "extrude_sensor_timeout": 90.0,  # live "timeout = 90"
    "extrude_poll_interval": 2.0,    # live 0x10 mode 5 alle ~2 s
    "stage7_delay": 2.0,             # ANNAHME (live 3,7-3,8 s bis mode 7)
    # = preload_force_timeout(1 Slot) 380 s (Firmware-Analyse:
    # Box-Auto-Preload 1 Slot bis 364 s; vorher 200 s = zu kurz)
    "box_wait_timeout": float(_BOX.preload_force_timeout(0x01)),
    "box_wait_interval": 1.0,
    # Auto-Nachladen bei Runout (A7, config-pi/README.md): Stock-
    # printer.cfg runout_gcode "G91 / G0 E30 F600 / G90" (nur wenn heiß) –
    # Restfilament durch die Zahnräder (= buffer_empty_len 30), hier im
    # Abfallschacht statt am Parkpunkt.
    "runout_tail_length": 30.0,
    "runout_tail_velocity": 600.0,
    # Restmaterial: Stock
    # BoxCfg max_tube_length (int, Default 3000 mm) = Obergrenze beim
    # Ausspülen des Schlauch-Rests; Zeitlimit = Pi-Zusatz (Stock: keins).
    "max_tube_length": 3000,
    "ending_material_timeout": 3600.0,
}
CHANGE_INT_KEYS = ("wipe_shakes", "max_temp_flush_times", "max_tube_length")
# Restmaterial-Ausspülen (Stock BoxAction.extrusion_all_materials Z.1783–
# 1824): Stücke zu 80 mm (K80 @0x1c781c), danach set_cool_temp (M104 S140)
# – Abkühlen nur außerhalb einer Wechselkette (s.
# PrinterK2Box._ending_purge).
ENDING_PIECE_MM = 80.0
ENDING_COOL_TEMP = 140.0
FLUSH_MODES = ("color", "fixed", "matrix")
# Düsen-Material persistent:
# Default-Pfad (leer = aus); Stock: /mnt/UDISK/creality/userdata/box/
# remain_material_data.json, gleiches Format.
REMAIN_MATERIAL_FILE_DEFAULT = "~/printer_data/config/k2/remain_material.json"
# Manuelle Slot-Farbe/-Material (KTWO_BOX_SLOT_SET, Ersatz Stock
# BOX_MODIFY_TN_DATA, 18.10.2026): Default-Pfad (leer = aus, nur Speicher).
# Stock persistiert Tn_data ebenfalls (BoxState.modify_Tn_data ->
# modify_tn_save_data/update_tn_save_data -> creality/userdata/box/
# tn_data.json, box_wrapper @0x1afce0/@0x1afdf8, README-k2_box.md §15).
SLOT_MATERIAL_FILE_DEFAULT = "~/printer_data/config/k2/slot_material.json"
# Slicer-Spülmatrix: so viel
# von Kopf und Ende der laufenden G-Code-Datei durchsuchen. 512 KiB wie
# Stock metadata.py READ_SIZE = 512*1024 (Kopf UND Fuß, CR:metadata.py:
# 1046-1058; bis 18.10.2026: 256 KiB).
MATRIX_SCAN_BYTES = 512 * 1024
FLUSH_CLEAN_MODES = ("stock", "legacy")
# Stock-Fallback der RESUME-Vorextrusion ohne Materialdaten:
# [filament_rack] velocity 360 (OHNE ·60) · 0,8 = 288 mm/min (B.3/B.5)
PRIME_FALLBACK_VELOCITY = 360.0
TEMP_LIMIT_MARGIN = 10.0  # K unter [extruder] max_temp (DB-Werte kappen)

# Box-Fehler, die nach einem Runout erwartet und vor dem Nachladen per
# 0x04 [00 01] (IDLE, löscht ERROR – live belegt) quittiert werden dürfen:
# 0x50 Runout im Druck-Slot, 0x51 Buffer-Nachschub zu langsam, 0x52 kein
# Vorschub (CFS-Firmware, print_check-Thread: „außer es lag schon ein Runout
# (0x50) vor“). ANNAHME: Stock quittiert sie beim Auto-Nachladen ebenso.
RUNOUT_CLEARABLE_ERRORS = (_BOX.STATUS_RUNOUT, _BOX.STATUS_SPEED_ERR,
                           _BOX.STATUS_NO_FEED)

CHAIN_STATE_NAME = "_ktwo_box_chain"
STEP_STATE_NAME = "_ktwo_box_step"
# L3: Halter-Name für [k2_limits] (Fahrgrenzen aufweiten, k2_limits.py)
LIMITS_OWNER = "k2_box"
# Eigener Halter für die ganze M8200-Kette (Creality Print 7, 22.10.2026):
# Die Datei fährt zwischen „M8200 P“ und „M8200 O“ SELBST zum Schacht
# (G0 Y378) – live „Move out of range: 139.000 378.000“ beim ersten
# Einzug. Stock weitet die Grenzen für den Wechsel auf (box_wrapper
# set_flushing_sign -> SET_LIMITS); hier von PRE_OPT (ohne SOURCE=T) bis
# END_OPT bzw. Kettenabbruch. Die T<n>-Kette fährt nur über eigene Schritte
# (je Schritt aufgeweitet) und bleibt unverändert.
CHAIN_LIMITS_OWNER = "k2_box_m8200"


class BoxChangeError(RuntimeError):
    """Wechsel-Fehler ohne Box-Statuscode (Timeout, Sensor, Zustand)."""


class BoxCancelled(BoxChangeError):
    """C2 (18.10.2026): Druckabbruch (Webhook
    pause_resume/cancel) während einer langen Warteschleife – Schritt sauber
    beendet, KEINE Pause und kein Wechsel-Fehler (CANCEL_PRINT folgt)."""


def tnn_from_index(index):
    """Werkzeug-Index 0..15 -> TNN wie Stock-M8200 L (box.cfg):
    addr = I/4 + 1, Slot = 'ABCD'[I % 4] -> 0 = T1A, 1 = T1B, 4 = T2A,
    15 = T4D (Beleg box.cfg + M8200-Sequenzen der Protokollanalyse)."""
    if isinstance(index, bool):
        raise ValueError("Werkzeug-Index muss 0..15 sein: %r" % (index,))
    try:
        i = int(str(index).strip().lstrip("="))
    except ValueError:
        raise ValueError("Werkzeug-Index muss 0..15 sein: %r" % (index,))
    if not 0 <= i <= 15:
        raise ValueError("Werkzeug-Index muss 0..15 sein: %r" % (index,))
    return "T%d%s" % (i // 4 + 1, SLOT_LETTERS[i % 4])


def parse_tnn(tnn):
    """'T1A'..'T4D' -> (box_addr 1..4, slot_index 0..3, letter, 'T1A')."""
    s = str(tnn).strip().upper().strip('"').strip("'")
    if (len(s) == 3 and s[0] == "T" and s[1] in "1234"
            and s[2] in SLOT_LETTERS):
        idx = SLOT_LETTERS.index(s[2])
        return int(s[1]), idx, s[2], s
    raise ValueError("TNN muss T1A..T4D sein (Box 1-4, Slot A-D): %r" % (tnn,))


def _n(value):
    """Zahl für G-Code (max. 3 Nachkommastellen, ohne Nullen)."""
    txt = ("%.3f" % float(value)).rstrip("0").rstrip(".")
    return "0" if txt in ("-0", "") else txt


def gcode_extruder_move(length, velocity):
    """Relativer Extruder-Schritt (G91/G90 wie Stock-QUIT_MATERIAL)."""
    return ["G91", "G1 E%s F%s" % (_n(length), _n(velocity)), "G90"]


def gcode_go_extrude_pos(c):
    """BOX_GO_TO_EXTRUDE_POS (Stock-T0-Beispiel Schritt 6–6.3):
    G0 Y345 F12000 / G0 X139 / G0 Y378 / G0 X133."""
    f = _n(c["travel_velocity"])
    return ["G90",
            "G0 Y%s F%s" % (_n(c["safe_pos_y"]), f),
            "G0 X%s F%s" % (_n(c["extrude_entry_pos_x"]), f),
            "G0 Y%s F%s" % (_n(c["extrude_pos_y"]), f),
            "G0 X%s F%s" % (_n(c["extrude_pos_x"]), f)]


def gcode_leave_cut_zone(c, pos):
    """Kopf im Schneidbereich (X < pre_cut_pos_x, Hebel-Anschlag links, z. B.
    nach einem abgebrochenen Schnitt): zuerst NUR X auf pre_cut_pos_x, keine
    Y-Bewegung in Hebelnähe. Beleg Stock box.cfg:13 ``pre_cut_pos_x: 10
    #预撞刀的位置，保证垂直撞刀`` (Vor-Schneidposition, Hebel senkrecht
    anfahren) und Rückweg des Schnitts ``G0 X10`` + ``M400`` (gcode_cut,
    live „cut to return OK“); erst danach Y (T0-Beispiel 6.: ``G0 Y345`` bei
    X10). Das M400 hält dort an: ohne Halt zöge der Input-Shaper die folgende
    Y-Fahrt schon vor X10 an (Batch resumecut: 0,03 mm Y bei X < 9,4).
    Sonst [] (pos None = unbekannt -> nichts)."""
    if pos is None or float(pos[0]) >= float(c["pre_cut_pos_x"]) - 0.01:
        return []
    return ["G0 X%s F%s" % (_n(c["pre_cut_pos_x"]), _n(c["travel_velocity"])),
            "M400"]


def gcode_safe_pos(c):
    """Schacht verlassen (Stock-T0-Beispiel Schritt 11): G0 X160 / G0 Y345."""
    f = _n(c["travel_velocity"])
    return ["G90",
            "G0 X%s F%s" % (_n(c["clean_right_pos_x"]), f),
            "G0 Y%s F%s" % (_n(c["safe_pos_y"]), f)]


def gcode_cut(c, restore_accel=None):
    """Schneidfahrt (T0-Beispiel Schritt 3–5 + CR_BOX_CUT):
    Vor-Schneidposition X10 Y200 (F12000) → ACCEL 5000 → Hebel gegen den
    Anschlag fahren bis cut_pos_x (SAVE_CONFIG -7.80) mit cut_velocity →
    G4 cut_dwell (ANNAHME, s. CHANGE_DEFAULTS) → zurück auf pre_cut_pos_x →
    Beschleunigung zurück → M400.
    ANNAHME: Rückweg mit travel_velocity (Stock-Geschwindigkeit unbelegt)."""
    f = _n(c["travel_velocity"])
    lines = ["G90",
             "G0 X%s Y%s F%s" % (_n(c["pre_cut_pos_x"]),
                                 _n(c["pre_cut_pos_y"]), f),
             "SET_VELOCITY_LIMIT ACCEL=%s" % _n(c["cut_accel"]),
             "G0 X%s Y%s F%s" % (_n(c["cut_pos_x"]), _n(c["cut_pos_y"]),
                                 _n(c["cut_velocity"]))]
    if c.get("cut_dwell", 0) > 0:
        lines.append("G4 P%s" % _n(float(c["cut_dwell"]) * 1000.0))
    lines.append("G0 X%s F%s" % (_n(c["pre_cut_pos_x"]), f))
    if restore_accel is not None:
        lines.append("SET_VELOCITY_LIMIT ACCEL=%s" % _n(restore_accel))
    lines.append("M400")
    return lines


def gcode_circle(start_x, start_y, center_dx, radius, clockwise, feed,
                 segments=16):
    """Vollkreis als G1-Polygon (Ersatz für Stock "G2/G3 I±4 J0 P1"; Upstream-
    gcode_arcs kennt P nicht und Start == Ziel ist dort mehrdeutig)."""
    cx = start_x + center_dx
    a0 = math.atan2(0.0, start_x - cx)
    out = []
    for i in range(1, segments + 1):
        step = 2.0 * math.pi * i / segments
        a = a0 - step if clockwise else a0 + step
        out.append("G1 X%s Y%s F%s" % (_n(cx + radius * math.cos(a)),
                                       _n(start_y + radius * math.sin(a)),
                                       _n(feed)))
    return out


def gcode_wipe(c, restore_accel=None):
    """Abstreifen am Schacht (Stock-T0-Beispiel 10.1–10.3):
    Blech-Schnitt X160→X135 (Y378), Kreis am Silikon um (164|374) und
    (166|374), dann 3x Schütteln X160↔X133 auf Y378 (endet an extrude_pos)."""
    cv = _n(c["clean_velocity"])
    r = float(c["wipe_circle_radius"])
    my = c["clean_pos_middle_y"]
    lines = ["G90", "SET_VELOCITY_LIMIT ACCEL=%s" % _n(c["wipe_accel"]),
             "G0 X%s F%s" % (_n(c["clean_right_pos_x"]), cv),
             "G0 X%s F%s" % (_n(c["clean_left_pos_x"]), cv),
             "G0 X%s Y%s F%s" % (_n(c["clean_pos_left_x"]), _n(my), cv)]
    lines += gcode_circle(c["clean_pos_left_x"], my, r, r, True,
                          c["wipe_circle_velocity"])
    lines.append("G0 X%s Y%s F%s" % (_n(c["clean_pos_right_x"]), _n(my), cv))
    lines += gcode_circle(c["clean_pos_right_x"], my, -r, r, False,
                          c["wipe_circle_velocity"])
    for _ in range(int(c["wipe_shakes"])):
        lines.append("G0 X%s Y%s F%s" % (_n(c["clean_right_pos_x"]),
                                         _n(c["clean_right_pos_y"]), cv))
        lines.append("G0 X%s Y%s F%s" % (_n(c["extrude_pos_x"]),
                                         _n(c["extrude_pos_y"]), cv))
    if restore_accel is not None:
        lines.append("SET_VELOCITY_LIMIT ACCEL=%s" % _n(restore_accel))
    return lines


def flush_segments(c, length=None):
    """Spül-Abschnitte: live [box_first_clean_length, box_need_clean_length]
    = [90, 70] (Summe 160 = "extrude = 160"). LENGTH > 0 ersetzt das durch
    Abschnitte von höchstens box_first_clean_length (ANNAHME)."""
    if length is None:
        segs = [float(c["box_first_clean_length"]),
                float(c["box_need_clean_length"])]
        return [s for s in segs if s > 0]
    total = float(length)
    if total <= 0:
        return []
    size = max(float(c["box_first_clean_length"]), 1.0)
    out = []
    while total > 1e-9:
        part = min(size, total)
        out.append(part)
        total -= part
    return out


def gcode_flush_segment(length, velocity, factor=1.0):
    """Ein Spül-Abschnitt, live 20 %/80 % geteilt (18+72 bzw. 14+56 mm);
    der 80 %-Teil mit velocity·factor (Stock flush_velocity_factor 1.2)."""
    first = round(float(length) * 0.2, 3)
    return ["G91",
            "G1 E%s F%s" % (_n(first), _n(velocity)),
            "G1 E%s F%s" % (_n(float(length) - first),
                            _n(float(velocity) * float(factor))),
            "G90"]


# ---------------------------------------------------------------------
# Material-DB + Spülen wie Stock (17.10.2026) – reine Bausteine
# ---------------------------------------------------------------------
# Grundlage: Stock-Spüllogik (Menge, Geschwindigkeit, Temperatur,
# Abschnitte/Ablauf, Reinigung/Blasen) und die DB-Felder der Creality-
# Material-Datenbank. Rechnung über
# nachbau/flush.py bzw. nachbau/material_db.py (nur importiert).

def load_material_db(path):
    """material_database.json laden -> (MaterialDB oder None, Meldung).

    Leerer Pfad = aus (None, ""). Fehlende Datei/kaputtes JSON/fehlendes
    material_db-Modul -> None + Meldung; der Aufrufer nutzt dann das
    bisherige Verhalten (Tn_extrude_temp, feste Geschwindigkeiten)."""
    raw = str(path or "").strip()
    if not raw:
        return None, ""
    p = os.path.expanduser(raw)
    if _MDB is None:
        return None, ("Material-DB %s: nachbau/material_db.py fehlt – "
                      "Fallback Tn_extrude_temp/feste Geschwindigkeiten" % p)
    try:
        db = _MDB.MaterialDB.load(p)
    except Exception as exc:
        return None, ("Material-DB %s nicht ladbar (%s) – Fallback "
                      "Tn_extrude_temp/feste Geschwindigkeiten" % (
                          p, exc.__class__.__name__ + ": " + str(exc)))
    return db, "Material-DB %s geladen (%d Einträge)" % (p, len(db))


def material_params(db, key, fallback_temp):
    """Stock-Abfragen für einen Material-Schlüssel (RFID mat_id, 6-stellig,
    DB-ID = mat_id[1:6], material_db.db_id_from_key) -> dict.

    known = Eintrag gefunden; temp/max_temp = int(kvParam.nozzle_temperature)
    bzw. int(base.maxTemp), nicht lesbar/unplausibel -> fallback_temp (Stock
    Tn_extrude_temp 220, temp_known/max_known False); vol =
    kvParam.filament_max_volumetric_speed (mm³/s) oder None; mtype =
    base.meterialType; name nur für Meldungen."""
    fb = float(fallback_temp)
    out = {"key": key, "known": False, "temp": fb, "temp_known": False,
           "max_temp": fb, "max_known": False, "vol": None, "mtype": None,
           "name": None}
    if db is None or not key:
        return out
    entry = db.find(key)
    if entry is None:
        return out
    out["known"] = True

    def _plaus(t):
        return t is not None and 100 <= t <= 450

    t = db.nozzle_temp(key, fallback=None)
    if _plaus(t):
        out["temp"], out["temp_known"] = float(t), True
    mt = db.max_temp(key, fallback=None)
    if _plaus(mt):
        out["max_temp"], out["max_known"] = float(mt), True
    vol = db.max_volumetric_speed(key)
    if vol is not None and vol > 0:
        out["vol"] = float(vol)
    out["mtype"] = db.material_type(key)
    try:
        nm = entry.get("base", {}).get("name")
        out["name"] = nm if isinstance(nm, str) else None
    except Exception:
        out["name"] = None
    return out


def flush_cfg(c):
    """k2_box-Optionen -> Schlüssel von nachbau/flush.py (Stock-[box]-Namen)."""
    return {
        "nozzle_volume": float(c["nozzle_volume"]),
        "flush_multiplier": float(c["flush_multiplier"]),
        "diff_material_type_min_flush_len":
            float(c["diff_material_type_min_flush_len"]),
        "box_first_clean_length": float(c["box_first_clean_length"]),
        "box_need_clean_length": float(c["box_need_clean_length"]),
        "box_need_clean_length_max": float(c["box_need_clean_length_max"]),
        "Tn_extrude": float(c["tn_extrude"]),
        "Tn_extrude_velocity": [float(c["tn_extrude_velocity"])],
        "max_temp_flush_times": int(c["max_temp_flush_times"]),
        "flush_velocity_factor": float(c["flush_velocity_factor"]),
    }


def plan_flush_lengths(c, length=None, src_color=None, dst_color=None,
                       different=False, matrix=None):
    """Spül-Abschnitte -> (Liste mm, Begründung).

    flush_mode color (Stock MCMBW.get_flush_len + BoxAction.get_flush_len +
    cal_flush_list): Farbvolumen get_flushing_volume(alt, neu) + nozzle_volume
    -> /2,4 · flush_multiplier; anderes Material ×2, mindestens
    diff_material_type_min_flush_len (370); sonst mindestens box_first +
    box_need (160). Farbe alt oder neu unbekannt -> Tn_extrude ROH (140,
    Stock) -> cal_flush_list -> [90, 70] = bisheriges Ergebnis. LENGTH> 0 ->
    cal_flush_list(LENGTH) (Stock teilt jede Länge so).
    flush_mode fixed (bzw. flush.py fehlt / box_*_clean_length 0): wie bis
    17.10. (flush_segments).
    flush_mode matrix: matrix = {"len": Rohlänge M[alt][neu]/2,4·m, "why":
    Text} aus slicer_matrix_flush (nur im Druck, sonst None) -> dieselbe
    Stock-Nachbearbeitung finish_flush_len (anders ×2/min 370, sonst min
    160) -> cal_flush_list; matrix None -> wie color (Stock-Rückfall)."""
    mode = c.get("flush_mode", "matrix")
    first = float(c["box_first_clean_length"])
    need = float(c["box_need_clean_length"])
    if mode not in ("color", "matrix") or _FLUSH is None or first <= 0 \
            or need <= 0:
        if mode not in ("color", "matrix"):
            why = "flush_mode fixed"
        elif _FLUSH is None:
            why = "nachbau/flush.py fehlt – fest"
        else:
            why = "box_first/need_clean_length 0 – fest"
        return flush_segments(c, length), why
    fc = flush_cfg(c)
    if length is not None:
        if float(length) <= 0:
            return [], "LENGTH=0"
        return ([float(x) for x in _FLUSH.cal_flush_list(float(length), fc)],
                "LENGTH=%s (cal_flush_list)" % _n(length))

    def _parse(cv):
        try:
            return _FLUSH.parse_color_value(cv)
        except (TypeError, ValueError):  # Stock convert_scv -> False
            return None

    if mode == "matrix" and matrix is not None:
        raw = float(matrix["len"])
        total = _FLUSH.finish_flush_len(raw, bool(different), fc)
        why = ("%s -> %s mm%s -> %s mm" % (
            matrix.get("why") or "Slicer-Matrix", _n(raw),
            " (anderes Material: ×2, min %s)" % _n(
                fc["diff_material_type_min_flush_len"]) if different
            else " (min %s)" % _n(fc["box_first_clean_length"]
                                  + fc["box_need_clean_length"]),
            _n(total)))
        return [float(x) for x in _FLUSH.cal_flush_list(float(total), fc)], why
    scv, tcv = _parse(src_color), _parse(dst_color)
    if scv is None or tcv is None:
        total = fc["Tn_extrude"]
        why = ("Farbe %s unbekannt -> Tn_extrude %s mm (Stock-Fallback)" % (
            "alt" if scv is None else "neu", _n(total)))
    else:
        vol, fl = _FLUSH.color_flush_len(scv, tcv, fc)
        total = _FLUSH.finish_flush_len(fl, bool(different), fc)
        why = ("Farbe %s -> %s: Volumen %d mm³ + Düse %s -> %s mm%s -> %s mm"
               % (src_color, dst_color, vol, _n(fc["nozzle_volume"]), _n(fl),
                  " (anderes Material: ×2, min %s)" % _n(
                      fc["diff_material_type_min_flush_len"])
                  if different else "", _n(total)))
    return [float(x) for x in _FLUSH.cal_flush_list(float(total), fc)], why


# ---------------------------------------------------------------------
# Slicer-Spülmatrix aus der laufenden G-Code-Datei (flush_mode matrix)
# ---------------------------------------------------------------------
# Beleg: Creality-klippy (Stock
# get_flush_length_from_gcode Z.4357–4406; Matrix flach, zeilenweise, n²,
# Index [alt][neu] = Slicer-Werkzeug T<n>, Länge = V/2,4·flush_multiplier
# OHNE nozzle_volume). Stock liest sie über Crealitys metadata.py
# (virtual_sdcard.get_gcode_flush_para) – Upstream hat das nicht, k2_box
# liest die Kommentarzeilen selbst. Zeilenform (Orca-CONFIG_BLOCK)
# ``; flush_volumes_matrix = 0,569,...`` / ``; flush_multiplier = 1.3`` –
# am Orca-Mehrfarbdruck 18.10.2026 belegt (Live-Mitschnitt: 0,82,73,0 /
# 0.3 = Stock flush_para).
_MATRIX_RE = re.compile(r"^[ \t]*;[ \t]*flush_volumes_matrix[ \t]*=[ \t]*"
                        r"([^\r\n]*)$", re.M)
_MULT_RE = re.compile(r"^[ \t]*;[ \t]*flush_multiplier[ \t]*=[ \t]*"
                      r"([^\r\n]*)$", re.M)


def parse_flush_matrix_text(text):
    """Text (Kopf oder Ende einer G-Code-Datei) -> (matrix, multiplier).

    matrix = flache Liste float (letzte Fundstelle) oder None;
    multiplier = float (erster Wert, falls kommagetrennt) oder None.
    ValueError bei unlesbaren Zahlen."""
    mat = mult = None
    found = _MATRIX_RE.findall(text or "")
    if found:
        raw = found[-1].strip().strip('"')
        vals = [v.strip() for v in raw.replace(";", ",").split(",")]
        vals = [v for v in vals if v]
        try:
            mat = [float(v) for v in vals]
        except ValueError:
            raise ValueError("flush_volumes_matrix nicht lesbar: %r"
                             % (raw[:60],))
        if any(not math.isfinite(v) or v < 0 for v in mat):
            raise ValueError("flush_volumes_matrix enthält ungültige Werte")
    found = _MULT_RE.findall(text or "")
    if found:
        raw = found[-1].strip().strip('"').split(",")[0].strip()
        try:
            mult = float(raw)
        except ValueError:
            raise ValueError("flush_multiplier nicht lesbar: %r" % (raw[:30],))
        if not math.isfinite(mult) or mult < 0:
            raise ValueError("flush_multiplier ungültig: %r" % (raw,))
    return mat, mult


def read_flush_matrix(path, scan_bytes=MATRIX_SCAN_BYTES):
    """Spülmatrix aus Kopf UND Ende einer G-Code-Datei -> dict
    {matrix, multiplier, n, where}; ValueError/OSError bei Fehlern.

    Liest höchstens scan_bytes vom Anfang und vom Ende (kleine Dateien
    ganz). Ende hat Vorrang (Orca-CONFIG_BLOCK), sonst Kopf; Multiplikator
    aus derselben Stelle, sonst aus der anderen, sonst None (= 1 wie Stock).
    Prüfung wie Stock: Matrix vorhanden + Länge Quadratzahl."""
    size = os.path.getsize(path)
    with open(path, "rb") as fh:
        if size <= 2 * scan_bytes:
            head, tail = fh.read(), b""
        else:
            head = fh.read(scan_bytes)
            fh.seek(size - scan_bytes)
            tail = fh.read(scan_bytes)
            # angeschnittene Zeilen an den Fenstergrenzen verwerfen
            head = head[:head.rfind(b"\n") + 1] if b"\n" in head else b""
            tail = tail[tail.find(b"\n") + 1:] if b"\n" in tail else b""
    th = head.decode("utf-8", "replace")
    tt = tail.decode("utf-8", "replace")
    m_t, k_t = parse_flush_matrix_text(tt)
    m_h, k_h = parse_flush_matrix_text(th)
    if m_t is not None:
        mat, where = m_t, "Dateiende" if tail else "Datei"
        mult = k_t if k_t is not None else k_h
    elif m_h is not None:
        mat, where = m_h, "Dateikopf" if tail else "Datei"
        mult = k_h if k_h is not None else k_t
    else:
        raise ValueError("keine '; flush_volumes_matrix =' in Kopf/Ende "
                         "(je %d KiB)" % (scan_bytes // 1024))
    if not mat:
        raise ValueError("flush_volumes_matrix leer (Stock: gcode has no "
                         "flush parameters)")
    n = int(round(math.sqrt(len(mat))))
    if n * n != len(mat):
        raise ValueError("flush_volumes_matrix mit %d Werten nicht quadratisch "
                         "(Stock: flushing parameter format is abnormal)"
                         % len(mat))
    return {"matrix": mat, "multiplier": mult, "n": n, "where": where}


def slicer_matrix_flush(info, old_idx, new_idx):
    """Rohlänge aus der Matrix (Stock get_flush_length_from_gcode, Rechnung
    nachbau/flush.py gcode_matrix_flush_len) -> (Länge, Text); ValueError
    bei Index außerhalb (Stock: box address does not comply)."""
    if _FLUSH is None:
        raise ValueError("nachbau/flush.py fehlt")
    n = int(info["n"])
    for name, i in (("alt", old_idx), ("neu", new_idx)):
        if not isinstance(i, int) or isinstance(i, bool) or not 0 <= i < n:
            raise ValueError("Werkzeug %s T%s außerhalb der %dx%d-Matrix"
                             % (name, i, n, n))
    mult = info.get("multiplier")
    ln = _FLUSH.gcode_matrix_flush_len(info["matrix"], old_idx, new_idx, mult)
    if ln is None:  # pragma: no cover – oben schon geprüft
        raise ValueError("Matrix-Formfehler")
    vol = info["matrix"][old_idx * n + new_idx]
    why = ("Slicer-Matrix [%d][%d] = %s mm³ / 2,4 × %s (%s)" % (
        old_idx, new_idx, _n(vol), _n(1 if mult is None else mult),
        info.get("where", "Datei")))
    return float(ln), why


# ---------------------------------------------------------------------
# Düsen-Material persistent (remain_material_file)
# ---------------------------------------------------------------------
# Beleg: Stock
# RemainMaterial liest die Datei einmal beim Start und schreibt nach jedem
# erfolgreichen Spülen {"remain_material": {"color": "0RRGGBB"|null,
# "type": "<id>"|null}}. Hier robuster: jeder Lesefehler = Warnung +
# unbekannt (nie Config-Fehler), kaputte Datei wird NICHT überschrieben,
# sondern nach <datei>.defekt-<JJJJMMTT-hhmmss> umbenannt; Schreiben atomar.
REMAIN_COLOR_RE = re.compile(r"^0[0-9A-Fa-f]{6}$")
_REMAIN_TYPE_RE = re.compile(r"^[0-9A-Za-z_.-]{1,32}$")
# Stock ignore_ops (MCMBW.get_flush_len): solche Farben = unbekannt
_REMAIN_IGNORE = ("-1", "none", "unknown", "")


def remain_path(raw):
    """Option -> absoluter Pfad ('' = aus), Tilde aufgelöst."""
    raw = str(raw or "").strip()
    return os.path.abspath(os.path.expanduser(raw)) if raw else ""


def normalize_remain_color(value):
    """'0RRGGBB' / 'RRGGBB' / '#RRGGBB' -> '0RRGGBB' (groß); None/'-1'/
    'none'/'unknown'/'' -> None; sonst ValueError."""
    if value is None:
        return None
    s = str(value).strip()
    if s.lower() in _REMAIN_IGNORE:
        return None
    if REMAIN_COLOR_RE.match(s):
        return s.upper()
    if re.match(r"^#?[0-9A-Fa-f]{6}$", s):
        return "0" + s.lstrip("#").upper()
    raise ValueError("Farbe muss 0RRGGBB sein: %r" % (value,))


def normalize_remain_type(value):
    """Material-ID (Stock material_type bzw. RFID mat_id) -> str oder None."""
    if value is None:
        return None
    s = str(value).strip()
    if s.lower() in ("", "none", "null"):
        return None
    if not _REMAIN_TYPE_RE.match(s):
        raise ValueError("Typ muss 1-32 Zeichen [0-9A-Za-z_.-] sein: %r"
                         % (value,))
    return s


def _remain_quarantine(path, now=None):
    """Kaputte Datei beiseitelegen (nie überschreiben) -> neuer Name/None."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    dst = "%s.defekt-%s" % (path, stamp)
    k = 1
    while os.path.exists(dst):
        k += 1
        dst = "%s.defekt-%s-%d" % (path, stamp, k)
    try:
        os.rename(path, dst)
        return dst
    except OSError:
        return None


def load_remain_material(path):
    """remain_material_file lesen -> (remain dict oder None, Status, Text).

    Status: 'aus' (Pfad leer), 'fehlt', 'geladen', 'fehler'. remain =
    {"type", "color", "tnn"} (tnn optional, Stock-Datei ohne -> "").
    Fehler (unlesbar, kein JSON, falsche Struktur, ungültige Farbe/Typ) ->
    None + Warnungstext; Inhaltsfehler -> Datei umbenannt (.defekt-…)."""
    p = remain_path(path)
    if not p:
        return None, "aus", ""
    if not os.path.exists(p):
        return None, "fehlt", ("Düsen-Material-Datei %s fehlt – Material in "
                               "der Düse unbekannt (wird nach dem nächsten "
                               "Spülen angelegt)" % p)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return None, "fehler", ("Düsen-Material-Datei %s nicht lesbar (%s) – "
                                "Material unbekannt" % (
                                    p, exc.__class__.__name__))
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(
                data.get("remain_material"), dict):
            raise ValueError("Struktur muss {\"remain_material\": {\"color\": "
                             "…, \"type\": …}} sein")
        rm = data["remain_material"]
        if "color" not in rm or "type" not in rm:
            raise ValueError("Schlüssel color/type fehlen")
        color = normalize_remain_color(rm.get("color"))
        mtype = normalize_remain_type(rm.get("type"))
    except ValueError as exc:   # json.JSONDecodeError ist ValueError
        moved = _remain_quarantine(p)
        return None, "fehler", (
            "Düsen-Material-Datei %s ungültig (%s) – Material unbekannt; %s" % (
                p, str(exc)[:120], ("beiseitegelegt als %s" % moved) if moved
                else "Umbenennen fehlgeschlagen, Datei bleibt unverändert"))
    tnn, note = "", ""
    raw_tnn = rm.get("tnn")
    if raw_tnn not in (None, ""):
        try:
            tnn = parse_tnn(raw_tnn)[3]
        except ValueError:
            note = ", tnn %r ignoriert" % (raw_tnn,)
    return ({"type": mtype, "color": color, "tnn": tnn}, "geladen",
            "Düsen-Material aus %s: Farbe %s, Typ %s%s%s" % (
                p, color or "unbekannt", mtype or "unbekannt",
                (", %s" % tnn) if tnn else "", note))


def remain_key(remain):
    """Vergleichswert der persistierten Felder (Schreiben nur bei Änderung)."""
    r = remain or {}
    col = r.get("color")
    return ((str(col).upper() if col else None), r.get("type") or None,
            r.get("tnn") or "")


def save_remain_material(path, remain):
    """Atomar schreiben (tmp im selben Verzeichnis, flush+fsync, os.replace,
    Verzeichnis-fsync, makedirs). Andere Top-Level-Schlüssel einer gültigen
    Datei bleiben (wie Stock read-modify-write); eine kaputte vorhandene
    Datei wird vorher beiseitegelegt. Ausnahmen gehen an den Aufrufer."""
    p = remain_path(path)
    if not p:
        raise ValueError("remain_material_file ist aus")
    d = os.path.dirname(p)
    os.makedirs(d, exist_ok=True)
    data = {}
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                old = json.load(fh)
        except (OSError, ValueError):
            old = None
        if isinstance(old, dict):
            data = old
        elif _remain_quarantine(p) is None:
            # kaputte Datei NIE überschreiben (Umbenennen unmöglich)
            raise OSError("vorhandene Datei ungültig und nicht umbenennbar")
    col, mtype, tnn = remain_key(remain)
    try:
        col = normalize_remain_color(col)
    except ValueError:
        col = None
    try:
        mtype = normalize_remain_type(mtype)
    except ValueError:
        mtype = None
    inner = {"color": col, "type": mtype}
    if tnn:
        inner["tnn"] = tnn
    data["remain_material"] = inner
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)   # Stock-Form (json.dump ohne indent)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        fd = os.open(d, getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass
    return p


# ---------------------------------------------------------------------
# Slot-Material: RFID-Etikett + manuelle Werte (KTWO_BOX_SLOT_SET), 18.10.
# ---------------------------------------------------------------------
# Stock (Display-App, box_wrapper):
# BOX_MODIFY_TN_DATA ADDR NUM PART=color_value|
# material_type DATA -> cmd_modify_Tn_data (@0x79c84, Py 5218–5222) ->
# box_state.modify_Tn_data(...) – EIN Feld je Aufruf, gleiche Tn_data-
# Felder, die auch die GET_RFID-Antwort beschreibt (process_msg @0x1cff60 ff.:
# vender/color_value/material_type über modify_Tn_data, mit Vergleichen
# gegen "unknown"/"-1"). Also: „letzter Schreiber gewinnt“ – ein späteres
# RFID-Lesen überschreibt den Handwert. Pi-Regel (ANNAHME für den Zweig
# „Etikett unlesbar“, dort Stock-Zweigdetails nur V): ein LESBARES Etikett
# löscht den Handwert des Slots; „unknown“/„busy“/leer lassen ihn stehen
# (Handwerte sind gerade für Slots ohne lesbares Etikett gedacht).
SLOT_MATERIAL_FIELDS = ("type", "color")


def merge_tag(tag, manual):
    """RFID-(mat_id, col) + Handwerte {type, color} -> (mat, col) je Feld
    (Hand vor RFID, wie Stock je PART) oder None (beides unbekannt)."""
    mat, col = (tag[0], tag[1]) if tag else (None, None)
    man = manual or {}
    if man.get("type"):
        mat = man["type"]
    if man.get("color"):
        col = man["color"]
    if not mat and not col:
        return None
    return (mat or None, col or None)


def same_material_key(a, b):
    """Material-Schlüssel gleich? Direkt oder über die DB-ID (6-stelliger
    RFID-Code ↔ 5-stellige base.id, material_db.db_id_from_key)."""
    if not a or not b:
        return False
    if str(a).upper() == str(b).upper():
        return True
    if _MDB is None:
        return False
    da, db_ = _MDB.db_id_from_key(str(a)), _MDB.db_id_from_key(str(b))
    return da is not None and da == db_


def find_material_by_name(db, name):
    """MATERIAL=<Name> -> base.id (5 Zeichen) aus der Material-DB.

    Exakter Name (ohne Groß/klein) gewinnt, sonst eindeutiger Teilstring;
    ValueError bei keinem/mehrdeutigem Treffer oder ohne DB."""
    if db is None:
        raise ValueError("MATERIAL= braucht [k2_box] material_database "
                         "(sonst TYPE=<id>)")
    want = str(name or "").strip().strip('"').lower()
    if not want:
        raise ValueError("MATERIAL leer")
    exact, part = [], []
    for e in getattr(db, "list", []):
        if not isinstance(e, dict) or not isinstance(e.get("base"), dict):
            continue
        nm, dbid = e["base"].get("name"), e["base"].get("id")
        if not isinstance(nm, str) or not isinstance(dbid, str):
            continue
        if nm.strip().lower() == want:
            exact.append((nm, dbid))
        elif want in nm.lower():
            part.append((nm, dbid))
    hits = exact or part
    ids = sorted({h[1] for h in hits})
    if not hits:
        raise ValueError("Material %r nicht in der Material-DB" % (name,))
    if len(ids) > 1:
        raise ValueError("Material %r mehrdeutig: %s" % (name, ", ".join(
            "%s (%s)" % h for h in hits[:6]) + (" …" if len(hits) > 6 else "")))
    return ids[0]


def _atomic_write_json(p, data):
    """JSON atomar schreiben (tmp + fsync + os.replace + Verzeichnis-fsync)."""
    d = os.path.dirname(p)
    os.makedirs(d, exist_ok=True)
    tmp = p + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        fd = os.open(d, getattr(os, "O_DIRECTORY", 0) | os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass
    return p


def _norm_slot_entries(raw):
    """{"T1A": {"type", "color"}} prüfen/normalisieren -> dict; ValueError."""
    if not isinstance(raw, dict):
        raise ValueError("\"slots\" muss ein Objekt sein")
    out = {}
    for k, v in raw.items():
        tnn = parse_tnn(k)[3]
        if not isinstance(v, dict):
            raise ValueError("%s: Eintrag muss ein Objekt sein" % tnn)
        ent = {"type": normalize_remain_type(v.get("type")),
               "color": normalize_remain_color(v.get("color"))}
        if ent["type"] or ent["color"]:
            out[tnn] = ent
    return out


def load_slot_material(path):
    """slot_material_file lesen -> (dict TNN->{type,color} oder None,
    Status aus|fehlt|geladen|fehler, Text). Fehler = Warnung, Datei
    beiseitegelegt (.defekt-…), nie Config-Fehler (wie remain_material)."""
    p = remain_path(path)
    if not p:
        return None, "aus", ""
    if not os.path.exists(p):
        return None, "fehlt", ""
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return None, "fehler", ("Slot-Material-Datei %s nicht lesbar (%s) – "
                                "Handwerte unbekannt" % (
                                    p, exc.__class__.__name__))
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or "slots" not in data:
            raise ValueError("Struktur muss {\"slots\": {\"T1A\": {\"color\": "
                             "…, \"type\": …}}} sein")
        slots = _norm_slot_entries(data["slots"])
    except ValueError as exc:
        moved = _remain_quarantine(p)
        return None, "fehler", (
            "Slot-Material-Datei %s ungültig (%s) – Handwerte verworfen; %s" % (
                p, str(exc)[:120], ("beiseitegelegt als %s" % moved) if moved
                else "Umbenennen fehlgeschlagen, Datei bleibt unverändert"))
    return slots, "geladen", ("Slot-Handwerte aus %s: %s" % (
        p, ", ".join("%s %s/%s" % (k, v["type"] or "-", v["color"] or "-")
                     for k, v in sorted(slots.items())) or "keine"))


def save_slot_material(path, slots):
    """Handwerte atomar schreiben ({"slots": {...}}, andere Top-Level-
    Schlüssel einer gültigen Datei bleiben, kaputte Datei wird vorher
    beiseitegelegt). Ausnahmen gehen an den Aufrufer."""
    p = remain_path(path)
    if not p:
        raise ValueError("slot_material_file ist aus")
    data = {}
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                old = json.load(fh)
        except (OSError, ValueError):
            old = None
        if isinstance(old, dict):
            data = old
        elif _remain_quarantine(p) is None:
            raise OSError("vorhandene Datei ungültig und nicht umbenennbar")
    data["slots"] = {k: {"type": v.get("type") or None,
                         "color": v.get("color") or None}
                     for k, v in sorted((slots or {}).items())
                     if v.get("type") or v.get("color")}
    return _atomic_write_json(p, data)


# ---------------------------------------------------------------------
# Externe Spule: Filamentquelle + externes Material (19.10.2026)
# ---------------------------------------------------------------------
# Belege: Live-Mitschnitt externe Spule 19.10.2026 (Laden, Mechanik:
# Schlauch von Hand umgesteckt, Filament bis zur LED am Kopf geschoben;
# Entladen, Stock-Makros LOAD_MATERIAL*/QUIT_MATERIAL*, zweites
# Laden ohne Eingabe = gespeichertes Material, kein erneutes Homing,
# Spülen wie CFS-Wechsel) und Stock-Filament-Rack-Logik (RFID-
# Leser 0x11, Material-/Heiz-/Lüfterlogik). Die CFS wird beim externen
# Weg NICHT angesprochen. Den umgesteckten Schlauch kann der Pi nicht
# erkennen -> Zustand „Quelle“ vom User (KTWO_BOX_SOURCE), persistent wie
# das zuletzt geladene externe Material (Stock-Display merkt es ebenso).
SOURCE_CFS = "cfs"
SOURCE_EXTERN = "extern"
SOURCES = (SOURCE_CFS, SOURCE_EXTERN)
# Default-Pfad (leer = aus: nur im Speicher, nach dem Start Quelle CFS)
FILAMENT_SOURCE_FILE_DEFAULT = "~/printer_data/config/k2/filament_source.json"
# chain["source"] eines externen Lade-/Entladevorgangs (KTWO_BOX_EXTERN_*)
EXTERN_CHAIN = "EXTERN"
# chain["source"] des M8200-Blocks einer Creality-Print-Datei (macros.cfg
# M8200 P -> KTWO_BOX_PRE_OPT SOURCE=M8200)
M8200_CHAIN = "M8200"
EXTERN_OPS = ("LADEN", "ENTLADEN")
# Stock QUIT_MATERIAL_RETRUDE_MATERIAL (gcode_macro.cfg): bei Kopfsensor
# G91 / G0 E-10 F360 / G90 / M400
EXTERN_RETRACT_MM = 10.0
EXTERN_RETRACT_VELOCITY = 360.0
# Obergrenze TEMP=, wenn weder [extruder] max_temp noch [k2_safety] lesbar
# (Stock product_param nozzle 350 = k2_safety max_nozzle_temp)
EXTERN_TEMP_FALLBACK_MAX = 350.0
# RFID-Leser am externen Halter (Stock filament_rack: data_send
# 11 03 ff 02, Timeout 0,5 s; Sendeschutz serial485: 0x11 nur 0x02/0x14)
EXTERN_RFID_ADDR = 0x11
EXTERN_RFID_FUN = 0x02
EXTERN_RFID_TIMEOUT = 0.5
EXTERN_RFID_STATUS = 0xFF
# Ausnahme vom Schreibschutz bei Quelle EXTERN (Stock
# live): Stock sendet beim Druck mit externer Spule am SD-Start 0x0d [0f 00]
# + 0x0f [01] und am Druckende 0x0d [0f 01] + 0x0f [00] – genau wie bei
# CFS-Drucken (reine Box-Einstellung, keine Filamentbewegung). NUR diese vier
# Frames (fun, Daten) sind erlaubt; 0x04, 0x10/0x11, Preload einzelner Slots
# usw. bleiben gesperrt (K2BoxController._check_write_block).
EXTERN_ALLOWED_FRAMES = ((0x0D, (0x0F, 0x00)), (0x0D, (0x0F, 0x01)),
                         (0x0F, (0x00,)), (0x0F, (0x01,)))
_EXTERN_NAME_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z +._()/-]{0,39}$")
_EXTERN_ORIGINS = ("hand", "rfid")


def normalize_source(value):
    """'EXTERN'/'extern'/'CFS'/'cfs' -> SOURCE_*; sonst ValueError."""
    s = str(value if value is not None else "").strip().strip('"') \
        .strip("'").lower()
    if s in SOURCES:
        return s
    raise ValueError("Quelle muss EXTERN oder CFS sein: %r" % (value,))


def normalize_extern_name(value):
    """Materialbezeichnung (Handeingabe ohne Material-DB bzw. DB-Name) ->
    str (1-40 Zeichen) oder None; ValueError bei ungültigen Zeichen."""
    if value is None:
        return None
    s = str(value).strip().strip('"').strip("'").strip()
    if not s or s.lower() in ("none", "null"):
        return None
    if not _EXTERN_NAME_RE.match(s):
        raise ValueError("Materialname muss 1-40 Zeichen [0-9A-Za-z +._()/-] "
                         "sein: %r" % (value,))
    return s


def normalize_extern_temp(value):
    """Gespeicherte Hand-Temperatur -> float (100..450 °C) oder None. Die
    Grenzen des Druckers (min_extrude_temp, max_temp, k2_safety) prüft die
    Klipper-Hülle bei der Eingabe und vor jeder Nutzung."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Temperatur ungültig: %r" % (value,))
    try:
        t = float(value)
    except (TypeError, ValueError):
        raise ValueError("Temperatur ungültig: %r" % (value,))
    if not math.isfinite(t) or not 100.0 <= t <= 450.0:
        raise ValueError("Temperatur muss 100..450 °C sein: %r" % (value,))
    return t


def normalize_extern_material(raw):
    """Gespeichertes externes Material -> {"type", "color", "name", "temp",
    "origin"} oder None (kein Material: weder Typ noch Name). ValueError
    bei ungültigen Feldern. Nur Material-ID, Farbe, Name, Temperatur –
    KEINE Etikett-Rohdaten."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("extern_material muss ein Objekt sein")
    origin = str(raw.get("origin") or "hand")
    if origin not in _EXTERN_ORIGINS:
        raise ValueError("origin muss hand|rfid sein: %r" % (origin,))
    out = {"type": normalize_remain_type(raw.get("type")),
           "color": normalize_remain_color(raw.get("color")),
           "name": normalize_extern_name(raw.get("name")),
           "temp": normalize_extern_temp(raw.get("temp")),
           "origin": origin}
    if not out["type"] and not out["name"]:
        return None
    return out


def extern_material_label(mat):
    """Kurztext für Meldungen: 'Name (ID), Farbe …[, Handwert … °C]'."""
    if not mat:
        return "keines gespeichert"
    what = mat.get("name") or mat.get("type") or "?"
    if mat.get("name") and mat.get("type"):
        what = "%s (%s)" % (mat["name"], mat["type"])
    parts = [what, "Farbe %s" % (mat.get("color") or "unbekannt")]
    if mat.get("temp"):
        parts.append("Handwert %s °C" % _n(mat["temp"]))
    if mat.get("origin") == "rfid":
        parts.append("aus RFID")
    return ", ".join(parts)


def load_filament_source(path):
    """filament_source_file lesen -> (state oder None, Status, Text).

    state = {"source": "cfs"|"extern", "material": dict|None}. Status:
    'aus' (Pfad leer), 'fehlt', 'geladen', 'fehler'. Jeder Fehler (nicht
    lesbar, kein JSON, falsche Struktur, unbekannte Quelle, ungültiges
    Material) -> None = Quelle CFS + Warnung; Inhaltsfehler -> Datei
    beiseitegelegt (.defekt-…), nie Config-Fehler (wie remain_material)."""
    p = remain_path(path)
    if not p:
        return None, "aus", ""
    if not os.path.exists(p):
        return None, "fehlt", ("Quellen-Datei %s fehlt – Quelle CFS, kein "
                               "externes Material gespeichert" % p)
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return None, "fehler", ("Quellen-Datei %s nicht lesbar (%s) – Quelle "
                                "CFS angenommen (Schlauch prüfen!)" % (
                                    p, exc.__class__.__name__))
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or "source" not in data:
            raise ValueError("Struktur muss {\"source\": \"cfs\"|\"extern\", "
                             "\"extern_material\": {…}|null} sein")
        src = normalize_source(data["source"])
        mat = normalize_extern_material(data.get("extern_material"))
    except ValueError as exc:
        moved = _remain_quarantine(p)
        return None, "fehler", (
            "Quellen-Datei %s ungültig (%s) – Quelle CFS angenommen (Schlauch "
            "prüfen!); %s" % (p, str(exc)[:120], ("beiseitegelegt als %s"
                                                  % moved) if moved else
                              "Umbenennen fehlgeschlagen, Datei bleibt "
                              "unverändert"))
    return ({"source": src, "material": mat}, "geladen",
            "Filamentquelle aus %s: %s; externes Material: %s" % (
                p, src.upper(), extern_material_label(mat)))


def save_filament_source(path, source, material):
    """Quelle + externes Material atomar schreiben (andere Top-Level-
    Schlüssel einer gültigen Datei bleiben, kaputte Datei wird vorher
    beiseitegelegt). Ausnahmen gehen an den Aufrufer."""
    p = remain_path(path)
    if not p:
        raise ValueError("filament_source_file ist aus")
    data = {}
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as fh:
                old = json.load(fh)
        except (OSError, ValueError):
            old = None
        if isinstance(old, dict):
            data = old
        elif _remain_quarantine(p) is None:
            raise OSError("vorhandene Datei ungültig und nicht umbenennbar")
    data["source"] = normalize_source(source)
    mat = normalize_extern_material(material) if material else None
    data["extern_material"] = mat
    return _atomic_write_json(p, data)


def find_extern_material(db, raw):
    """MATERIAL=<ID|Name|Typ> -> (Schlüssel, DB-Name) aus der Material-DB.

    Reihenfolge: (1) ID (5-stellige base.id bzw. 6-stelliger RFID-Code,
    material_db.find), (2) exakter Name (ohne Groß/klein), (3) Material-
    typ base.meterialType (z. B. PLA): eine ID -> diese; mehrere -> der
    Eintrag „Generic <Typ>“, falls eindeutig; (4) eindeutiger Teilstring
    des Namens. Sonst ValueError (kein/mehrdeutiger Treffer, ohne DB)."""
    if db is None:
        raise ValueError("MATERIAL= über die Material-DB braucht [k2_box] "
                         "material_database")
    want = str(raw if raw is not None else "").strip().strip('"') \
        .strip("'").strip()
    if not want:
        raise ValueError("MATERIAL leer")
    if len(want) in (5, 6):
        entry = db.find(want)
        if entry is not None:
            nm = (entry.get("base") or {}).get("name")
            return want, (nm if isinstance(nm, str) else None)
    low = want.lower()
    exact, typ, part = [], [], []
    for e in getattr(db, "list", []):
        if not isinstance(e, dict) or not isinstance(e.get("base"), dict):
            continue
        b = e["base"]
        nm, dbid, mt = b.get("name"), b.get("id"), b.get("meterialType")
        if not isinstance(dbid, str):
            continue
        nm = nm if isinstance(nm, str) else ""
        if nm and nm.strip().lower() == low:
            exact.append((nm, dbid))
        if isinstance(mt, str) and mt.strip().lower() == low:
            typ.append((nm, dbid))
        if nm and low in nm.lower():
            part.append((nm, dbid))

    def _one(hits, what):
        ids = sorted({h[1] for h in hits})
        if len(ids) == 1:
            return ids[0], next(h[0] for h in hits if h[1] == ids[0]) or None
        raise ValueError("%s %r mehrdeutig: %s – exakten Namen oder TYPE=<id> "
                         "angeben" % (what, raw, ", ".join(
                             "%s (%s)" % h for h in hits[:6])
                             + (" …" if len(hits) > 6 else "")))
    if exact:
        return _one(exact, "Material")
    if typ:
        if len({h[1] for h in typ}) == 1:
            return _one(typ, "Materialtyp")
        gen = [h for h in typ if h[0].strip().lower() in (
            "generic " + low, "generic-" + low, "generic_" + low)]
        if gen:
            return _one(gen, "Materialtyp")
        return _one(typ, "Materialtyp")
    if part:
        return _one(part, "Material")
    raise ValueError("Material %r nicht in der Material-DB – Namen, Typ oder "
                     "TYPE=<id> angeben" % (raw,))


def extern_rfid_tag(raw_frame=None, data=None):
    """Antwort des RFID-Lesers 0x11 -> (mat_id, Farbe) oder None.

    Wie Stock filament_rack send_data (Z.170-178): rfid = ''.join(chr(i)
    for i in ret[5:-1]) (nachbau/filament_rack.rfid_from_response), 40
    Zeichen, Feldlayout wie der CFS-vender (rfid_material: mat_id 11..16,
    Farbe 17..23). Unlesbar/leer -> None. Gibt NUR Material-ID und Farbe
    zurück (keine Etikett-Rohdaten nach außen)."""
    rfid = None
    try:
        if raw_frame is not None:
            if _RACK is not None:
                rfid = _RACK.rfid_from_response(bytes(raw_frame))
            else:  # pragma: no cover – gleiche Rechnung ohne Nachbau
                rfid = "".join(chr(i) for i in bytes(raw_frame)[5:-1])
        elif data is not None:
            rfid = bytes(data).decode("ascii", "replace")
    except (TypeError, ValueError):
        return None
    if not isinstance(rfid, str) or len(rfid) != _BOX.RFID_VENDOR_LEN:
        return None
    return rfid_material(rfid)


def flush_velocity_base(c, vol=None, nozzle=None):
    """Stock get_flush_velocity: min(360, vol/2,4·60) (vol = Material-
    max_volumetric_speed; ohne -> Tn_extrude_velocity) · Düse/0,4 -> mm/min."""
    base = float(c["baseline_nozzle_diameter"])
    nz = float(nozzle) if nozzle else base
    if _FLUSH is None:
        return float(c["tn_extrude_velocity"])
    v, _p = _FLUSH.flush_velocity(vol, nz, base, cfg=flush_cfg(c))
    return float(v[0])


def flush_segment_lines(c, seg, i, n, velocity, temp, max_temp):
    """Ein Spül-Abschnitt wie Stock check_flush_temp_and_extrude (Z.1336–
    1359, Rechnung flush.segment_plan): Index = i, letzter Abschnitt (n > 1)
    = max_temp_flush_times; Index 0 + max_temp > temp: M104 S<max> +
    TEMPERATURE_WAIT MINIMUM=<max-5> (Stock quickly_wait_heating(max-5),
    Upstream-M109 würde beim Abkühlen warten); Index == max_temp_flush_times
    -> M104 S<temp> („restore target temp“) und Faktor 1, sonst
    20 % · v + 80 % · v · flush_velocity_factor."""
    fc = flush_cfg(c)
    idx = fc["max_temp_flush_times"] if (i != 0 and i == n - 1) else i
    boost = float(max_temp) > float(temp) + 0.01
    if _FLUSH is not None:
        steps = _FLUSH.segment_plan(float(seg), idx, float(temp),
                                    float(max_temp), [float(velocity)], fc)
    else:  # pragma: no cover - bisheriges Profil
        steps = [("E", float(seg) * 0.2, float(velocity)),
                 ("E", float(seg) * 0.8, float(velocity))]
    lines, moves = [], []
    for st in steps:
        if st[0] == "WAIT":
            if boost:
                lines += ["M104 S%s" % _n(max_temp),
                          "TEMPERATURE_WAIT SENSOR=extruder MINIMUM=%s"
                          % _n(float(max_temp) - 5.0)]
        elif st[0] == "M104":
            if boost and abs(float(st[1]) - float(max_temp)) > 0.01:
                lines.append("M104 S%s" % _n(st[1]))
        else:
            moves.append("G1 E%s F%s" % (_n(st[1]), _n(st[2])))
    return lines + ["G91"] + moves + ["G90"]


def gcode_blow(fan, seconds, restore_value, full=255.0):
    """Stock BoxAction.blow (Z.2733–2740): fan0 5 s voll, dann alter Wert."""
    return ["SET_PIN PIN=%s VALUE=%s" % (fan, _n(full)),
            "G4 P%s" % _n(float(seconds) * 1000.0), "M400",
            "SET_PIN PIN=%s VALUE=%s" % (fan, _n(restore_value))]


def _velocity_limit(accel, mcr=None):
    line = "SET_VELOCITY_LIMIT ACCEL=%s" % _n(accel)
    if mcr is not None:
        line += " MINIMUM_CRUISE_RATIO=%s" % _n(mcr)
    return line


def gcode_nozzle_clean(c, circle, offset=0.0, restore_accel=None,
                       restore_mcr=None, arcs=True):
    """Stock BoxAction.nozzle_clean(circle) (Z.3149–3182)
    ab extrude_pos: ACCEL wipe_accel (+ MINIMUM_CRUISE_RATIO, Upstream-
    Ersatz für ACCEL_TO_DECEL=ACCEL, nur wenn der alte Wert bekannt ist) ->
    Blech X clean_pos_right_x (170) -> X clean_left_pos_x (135) -> nur mit
    circle: Silikon-Kreise ab X clean_pos_left_x + offset bzw.
    clean_pos_right_x − offset (Y clean_pos_middle_y, G2 I+r / G3 I−r J0;
    ohne [gcode_arcs] 16-Eck) -> 3× X clean_right_pos_x/Y clean_right_pos_y
    <-> X extrude_pos_x/Y clean_left_pos_y -> nur mit circle: Schacht
    verlassen X clean_right_pos_x -> Y safe_pos_y -> M400 -> alte Werte.
    Ohne circle bleibt die Düse im Schacht (extrude_pos). Der Stock-Zweig
    „Kopf außerhalb (Y < safe_pos_y)“ entfällt: k2_box fährt vorher immer
    in den Schacht."""
    cv = _n(c["clean_velocity"])
    r = float(c["wipe_circle_radius"])
    wv = c["wipe_circle_velocity"]
    my = c["clean_pos_middle_y"]
    mcr = c.get("wipe_minimum_cruise_ratio") if restore_mcr is not None \
        else None
    lines = ["G90", _velocity_limit(c["wipe_accel"], mcr),
             "G0 X%s F%s" % (_n(c["clean_pos_right_x"]), cv),
             "G0 X%s F%s" % (_n(c["clean_left_pos_x"]), cv)]
    if circle:
        sx = float(c["clean_pos_left_x"]) + float(offset)
        ex = float(c["clean_pos_right_x"]) - float(offset)
        lines.append("G0 X%s Y%s F%s" % (_n(sx), _n(my),
                                         _n(c["travel_velocity"])))
        if arcs:
            lines.append("G2 I%s J0 F%s" % (_n(r), _n(wv)))
        else:
            lines += gcode_circle(sx, my, r, r, True, wv)
        lines.append("G0 X%s Y%s F%s" % (_n(ex), _n(my), _n(wv)))
        if arcs:
            lines.append("G3 I%s J0 F%s" % (_n(-r), _n(wv)))
        else:
            lines += gcode_circle(ex, my, -r, r, False, wv)
    for _ in range(int(c["wipe_shakes"])):
        lines.append("G0 X%s Y%s F%s" % (_n(c["clean_right_pos_x"]),
                                         _n(c["clean_right_pos_y"]), cv))
        lines.append("G0 X%s Y%s F%s" % (_n(c["extrude_pos_x"]),
                                         _n(c["clean_left_pos_y"]), cv))
    if circle:
        lines.append("G0 X%s F%s" % (_n(c["clean_right_pos_x"]), cv))
        lines.append("G0 Y%s F%s" % (_n(c["safe_pos_y"]), cv))
    lines.append("M400")
    if restore_accel is not None:
        lines.append(_velocity_limit(restore_accel, restore_mcr))
    return lines


def preload_block_reason(op, mask, head, printing, force=False):
    """Stock-Schutz von communication_set_pre_loading
    (Firmware-Analyse) -> None = senden erlaubt, sonst Klartext.
    - op 2/3 mit Maske 0: Fehlgebrauch (NUM als Slotindex, [00 02]-Bursts).
    - Druck läuft/pausiert: nichts senden, außer force (Stock force=True).
    - op 2/3 nur bei LEEREM Kopf-Filamentsensor (head False); Filament oder
      unbekannt (kein Sensor) -> nein (Stock: get_filament_sensor_detect)."""
    force_op = op in (PRELOAD_FORCE_READ, PRELOAD_FORCE_TENSION_ONLY)
    if force_op and int(mask) == 0:
        return ("Maske 0 bei op %d abgelehnt (Fehlgebrauch NUM=0 als Slot – "
                "Stock-[00 02]-Bursts)" % op)
    if printing and not force:
        return ("Druck läuft/pausiert – Preload nur mit FORCE=1 (Stock "
                "force=True)")
    if force_op and head is not False:
        return ("op %d nur bei leerem Kopf-Filamentsensor (Kopfsensor %s; "
                "Stock sendet bei Filament im Kopf nicht)" % (
                    op, "meldet Filament" if head else "unbekannt"))
    return None


def retrude_extruder_steps(c, length=None):
    """Extruder-Rückzug je 0x11-p0 (live 3x -10 mm = buffer_empty_len 30).
    LENGTH (Stock "M8200 R E…"/CR_BOX_RETRUDE LENGTH) = Gesamtlänge,
    aufgeteilt in |tn_retrude|-Schritte (ANNAHME)."""
    step = abs(float(c["tn_retrude"]))
    total = abs(float(c["buffer_empty_len"] if length is None else length))
    if step <= 0 or total <= 0:
        return []
    out = []
    while total > 1e-9:
        part = min(step, total)
        out.append(-part)
        total -= part
    return out


# ---------------------------------------------------------------------
# Auto-Nachladen bei Runout (A7) – reine Bausteine
# ---------------------------------------------------------------------
# Stock (box_wrapper.so, nur Strings/Status belegt, Ablauf unbelegt):
# BOX_CHECK_MATERIAL_REFILL -> check_material_refill / material_auto_refill /
# is_material_available; Ersatz kommt aus der Gleich-Material-Liste
# (BOX_UPDATE_SAME_MATERIAL_LIST, Status 'same_material', Log
# "same_material_list: %s, same_tnn_list: %s", "same_tnn_list len smaller
# than 2", "no tray with ingredients found", Schalter 'auto_refill' /
# BOX_ENABLE_AUTO_REFILL). Diese Liste schickt Creality Print an die Stock-
# Oberfläche, NICHT im G-Code (Orca-K2-Plus-Profile: nur M8200) – auf dem
# Pi fehlt die Quelle. Daher: manuelle Liste (KTWO_BOX_SAME_MATERIAL) ODER
# Vergleich über das RFID-Etikett (mat_id + col aus dem 40-Zeichen-vender,
# box.split_rfid_vendor; vgl. Vanilla box.py runout_recovery: gleiches
# Material UND gleiche Farbe, Slot belegt).

def rfid_material(value):
    """RFID-Wert eines Slots (40-Zeichen-vender) -> (mat_id, col) oder None
    ("unknown"/"busy"/leer/kaputt = unbekannt)."""
    if not isinstance(value, str) or len(value.strip()) != _BOX.RFID_VENDOR_LEN:
        return None
    try:
        f = _BOX.split_rfid_vendor(value.strip())
    except ValueError:
        return None
    mat, col = f.get("mat_id", ""), f.get("col", "").upper()
    if not mat.strip("0") or not col:
        return None
    return mat, col


def parse_same_material(text, box_addr=BOX_ADDR_DEFAULT):
    """KTWO_BOX_SAME_MATERIAL LIST= -> [[Slotbuchstaben], ...].

    Format (ANNAHME, Stock-Format von BOX_UPDATE_SAME_MATERIAL_LIST
    unbelegt): Gruppen mit '/' getrennt, Mitglieder mit ',' – z. B.
    ``T1A,T1C/T1B,T1D``. Nur Box ``box_addr``; jede Gruppe >= 2 Slots
    (Stock: "same_tnn_list len smaller than 2" = kein Nachladen), jeder
    Slot höchstens in einer Gruppe."""
    groups, seen = [], set()
    for part in str(text).strip().strip('"').split("/"):
        part = part.strip()
        if not part:
            continue
        letters = []
        for tok in part.split(","):
            tok = tok.strip()
            if not tok:
                continue
            box, _idx, letter, tnn = parse_tnn(tok)
            if box != int(box_addr):
                raise ValueError("%s liegt in Box %d, konfiguriert ist nur "
                                 "Box %d" % (tnn, box, int(box_addr)))
            if letter in seen:
                raise ValueError("%s steht in mehr als einer Gruppe" % tnn)
            if letter not in letters:
                letters.append(letter)
        if len(letters) < 2:
            raise ValueError("Gruppe %r hat weniger als 2 Slots" % (part,))
        seen.update(letters)
        groups.append(letters)
    if not groups:
        raise ValueError("LIST leer (Format: T1A,T1C/T1B,T1D)")
    return groups


def select_runout_slot(current, inlet_mask, rfid, same_groups=None,
                       require_color=True, manual=None):
    """Ersatz-Slot für den leer gelaufenen Slot ``current`` (Buchstabe).

    Regel (s. Block oben): Kandidaten nur mit Filament am Einlauf (0x08 [00]
    Sensor A), nie der leere Slot selbst, Reihenfolge ab dem Slot NACH dem
    leeren (A→B→C→D→A, ANNAHME; Stock-Reihenfolge unbelegt).
    1. Gleich-Material-Liste gesetzt -> nur Mitglieder der Gruppe des leeren
       Slots (in Listenreihenfolge ab dem leeren Slot, zyklisch).
    2. Sonst RFID: gleiche mat_id UND (require_color) gleiche Farbe wie das
       Etikett des leeren Slots; Slots ohne lesbares Etikett nie.
       manual = {Buchstabe: {type, color}} (KTWO_BOX_SLOT_SET) ergänzt bzw.
       ersetzt je Feld das Etikett (merge_tag, 18.10.2026).
    Gibt (Buchstabe oder None, Begründung) zurück."""
    cur = str(current).upper()
    if cur not in SLOT_LETTERS:
        raise ValueError("Slot muss A..D sein: %r" % (current,))
    inlet = int(inlet_mask) & MASK_ALL

    def present(letter):
        return bool(inlet & SLOT_MASKS[SLOT_LETTERS.index(letter)])

    if same_groups:
        group = next((g for g in same_groups if cur in g), None)
        if not group:
            return None, ("Slot %s steht in keiner Gleich-Material-Gruppe "
                          "(KTWO_BOX_SAME_MATERIAL)" % cur)
        k = group.index(cur)
        order = [x for x in group[k + 1:] + group[:k]]
        for letter in order:
            if present(letter):
                return letter, ("Gleich-Material-Gruppe %s, Slot %s hat "
                                "Filament am Einlauf" % ("+".join(group),
                                                         letter))
        return None, ("kein Slot der Gleich-Material-Gruppe %s hat Filament "
                      "am Einlauf (Stock „no tray with ingredients found“)"
                      % "+".join(group))
    def tag(letter):
        return merge_tag(rfid_material((rfid or {}).get(letter)),
                         (manual or {}).get(letter))

    ref = tag(cur)
    if ref is None or not ref[0] or (require_color and not ref[1]):
        return None, ("Material/Farbe von Slot %s unbekannt (kein lesbares "
                      "RFID-Etikett, kein KTWO_BOX_SLOT_SET) – Gruppe per "
                      "KTWO_BOX_SAME_MATERIAL festlegen" % cur)
    k = SLOT_LETTERS.index(cur)
    order = SLOT_LETTERS[k + 1:] + SLOT_LETTERS[:k]
    for letter in order:
        if not present(letter):
            continue
        m = tag(letter)
        if m is None or not same_material_key(m[0], ref[0]):
            continue
        if require_color and (not m[1] or m[1].upper() != ref[1].upper()):
            continue
        return letter, ("RFID gleiches Material %s%s, Filament am Einlauf" % (
            ref[0], (" + Farbe %s" % ref[1]) if require_color else ""))
    return None, ("kein belegter Slot mit gleichem Material %s%s (RFID) – "
                  "Stock „no tray with ingredients found“" % (
                      ref[0], (" und Farbe %s" % ref[1]) if require_color
                      else ""))


def check_not_forbidden(fun):
    """Guard: 0x50/0x06 dürfen nie gesendet werden (Beleg s. FORBIDDEN_FUNS)."""
    if fun in FORBIDDEN_FUNS:
        raise ValueError("fun 0x%02x ist verboten (0x50 resettet, 0x06 FW-Bug)" % fun)
    return fun


def parse_slot(slot):
    """SLOT A..D (Auftrag) -> (index 0..3, letter, mask).

    Akzeptiert Buchstaben A-D (case-insensitiv) und Indizes 0-3 (Komfort,
    ANNAHME – Original-G-Code NUM=0..3 numerisch, Beleg Protokollanalyse:
    NUM=A wirft key60). Masken (0x01/0x02/0x04/0x08) werden NICHT akzeptiert
    (Verwechslungsgefahr) – dafür slot_to_mask() verwenden.
    """
    if isinstance(slot, bool):
        raise ValueError("SLOT muss A..D oder 0..3 sein: %r" % (slot,))
    if isinstance(slot, int) and slot in range(4):
        idx = int(slot)
        return idx, SLOT_LETTERS[idx], SLOT_MASKS[idx]
    if isinstance(slot, str):
        s = slot.strip().upper()
        if len(s) == 1 and s in SLOT_LETTERS:
            idx = SLOT_LETTERS.index(s)
            return idx, s, SLOT_MASKS[idx]
        if s in ("0", "1", "2", "3"):
            idx = int(s)
            return idx, SLOT_LETTERS[idx], SLOT_MASKS[idx]
    raise ValueError("SLOT muss A..D (oder 0..3) sein: %r" % (slot,))


def parse_preload_op(op):
    """OP 0..3 -> int (s. PRELOAD_OP_NAMES)."""
    if isinstance(op, bool):
        raise ValueError("OP muss 0..3 sein: %r" % (op,))
    if isinstance(op, int) and op in PRELOAD_OP_NAMES:
        return int(op)
    if isinstance(op, str):
        s = op.strip()
        if s in ("0", "1", "2", "3"):
            return int(s)
        low = s.lower()
        for code, name in PRELOAD_OP_NAMES.items():
            if low == name.split(" ")[0]:
                return code
    raise ValueError("OP muss 0..3 sein (0 disable, 1 enable, 2 force-read, "
                     "3 tension-only): %r" % (op,))


# =====================================================================
# Config (rein + Klipper-Hülle, gleicher Stil wie k2_motor_init.py)
# =====================================================================

def parse_options(opts):
    """Reines Dict (keys klein, Werte Strings) -> strukturierte Config.

    Unterstützte Optionen (ANNAHME für neue Keys, Defaults sicher):
    - box_addr (Default 1), allow_write (Default False),
      shutdown_on_failure (Default False), auto_init (Default True),
      poll_interval (Default 4.0 s).
    - Filamentwechsel: allow_move (Default False – Bewegung/Heizen),
      pause_on_failure (Default True), filament_sensor (Default
      "filament_sensor"), cut_sensor (Default leer) und alle Stock-[box]-
      Werte aus CHANGE_DEFAULTS (Positionen, Längen, Temperaturen,
      Geschwindigkeiten; cut_pos_x Default = SAVE_CONFIG -7.80).
    - Runout/RESUME: runout_refill, runout_require_color, runout_resume,
      resume_redo_change (Default False, s. cmd_resume_check).
    - Material/Spülen (17.10.2026, README-k2_box.md §12): material_database
      (Pfad, Default leer = aus), flush_mode color|fixed|matrix (Default
      matrix seit 18.10.2026 = Stock: Slicer-Spülmatrix aus der Druckdatei
      ab dem ersten Wechsel im Druck, sonst color),
      remain_material_file (Pfad, Default
      ~/printer_data/config/k2/remain_material.json, leer = aus),
      slot_material_file (Pfad, Default
      ~/printer_data/config/k2/slot_material.json, leer = aus; README §15),
      flush_clean stock|legacy (Default stock), flush_blow (Default True),
      flush_blow_fan (Default fan0), flush_clog_check (Default False) und
      die Stock-[box]-Werte nozzle_volume, flush_multiplier, … (s. oben).
    - Restmaterial/Druckende (17.10.2026, README-k2_box.md §12.5):
      ending_material_purge (Default True), max_tube_length (3000 mm),
      ending_material_timeout (3600 s), end_print_unload (Default True
      seit 18.10.2026, Stock entlädt immer).
    - Druckabbruch (18.10.2026, C2):
      cancel_interrupt (Default True, s. PrinterK2Box._hook_cancel).
    """
    o = {str(k).strip().lower(): v for k, v in dict(opts).items()}

    def _int(key, default):
        try:
            return int(str(o.get(key, default)).strip())
        except ValueError:
            raise ValueError("%s muss int sein: %r" % (key, o.get(key)))

    def _float(key, default):
        try:
            return float(str(o.get(key, default)).strip())
        except ValueError:
            raise ValueError("%s muss Zahl sein: %r" % (key, o.get(key)))

    def _bool(key, default):
        v = str(o.get(key, str(default))).strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off"):
            return False
        raise ValueError("%s muss bool sein: %r" % (key, o.get(key)))

    def _str(key, default=""):
        return str(o.get(key, default) or "").strip()

    cfg = {
        "box_addr": _int("box_addr", BOX_ADDR_DEFAULT),
        "allow_write": _bool("allow_write", False),
        "shutdown_on_failure": _bool("shutdown_on_failure", False),
        "auto_init": _bool("auto_init", True),
        "poll_interval": _float("poll_interval", POLL_INTERVAL_DEFAULT),
        # Filamentwechsel (Ergänzung 16.10.2026)
        "allow_move": _bool("allow_move", False),
        "pause_on_failure": _bool("pause_on_failure", True),
        # Stock [box] filament_sensor: filament_sensor (Kopfsensor PA11)
        "filament_sensor": _str("filament_sensor", "filament_sensor"),
        # Stock [box] switch_pin: !nozzle_mcu:PB9 (Schneidhebel) – hier nur
        # als Name eines [filament_switch_sensor …] (leer = keine Prüfung)
        "cut_sensor": _str("cut_sensor", ""),
        # Auto-Nachladen bei Runout (A7, config-pi/README.md): Default AUS;
        # wirkt zusätzlich nur mit allow_write UND allow_move.
        "runout_refill": _bool("runout_refill", False),
        "runout_require_color": _bool("runout_require_color", True),
        "runout_resume": _bool("runout_resume", True),
        # RESUME nach abgebrochenem T<n>-Wechsel bei LEEREM Kopf: Wechsel
        # automatisch nachholen (Stock error_resume_process).
        # Default AUS – dann sperrt RESUME nur
        # (cmd_resume_check); wirkt zusätzlich nur mit allow_write UND
        # allow_move.
        "resume_redo_change": _bool("resume_redo_change", False),
        # Material-DB (Creality material_database.json, NICHT im Repo; von
        # install.sh vom Drucker kopiert). Leer = aus: bisheriges Verhalten.
        "material_database": _str("material_database", ""),
        # Spülmenge: color = Stock (Farbvolumen flush.py, unbekannte Farben
        # -> Tn_extrude 140 -> [90, 70] wie bisher); fixed = immer
        # [box_first, box_need] bzw. LENGTH= in box_first-Stücken (bis 17.10.);
        # matrix (Default seit 18.10.2026, E4 = Stock)
        # = Slicer-Matrix ab dem ersten Wechsel im Druck, sonst color
        "flush_mode": _str("flush_mode", "matrix").lower(),
        # Düsen-Material über Neustarts (Stock remain_material_data.json).
        # Leer = aus (nur im Speicher).
        "remain_material_file": _str("remain_material_file",
                                     REMAIN_MATERIAL_FILE_DEFAULT),
        # Slot-Handwerte (KTWO_BOX_SLOT_SET, Stock tn_data.json). Leer = aus
        # (nur im Speicher bis zum Neustart).
        "slot_material_file": _str("slot_material_file",
                                   SLOT_MATERIAL_FILE_DEFAULT),
        # Reinigung je Abschnitt: stock = Blech zwischen den Abschnitten,
        # Silikon-Kreise nur am Ende; legacy = volles Abstreifen nach jedem
        "flush_clean": _str("flush_clean", "stock").lower(),
        "flush_blow": _bool("flush_blow", True),
        "flush_blow_fan": _str("flush_blow_fan", "fan0"),
        "flush_clog_check": _bool("flush_clog_check", False),
        # Restmaterial (Spule im Druck leer, Rest im Schlauch; Stock
        # is_use_ending_material): beim nächsten Wechsel / M8200 P /
        # Druckende ausspülen statt Schnitt + Rückzug (Default AN wie Stock).
        "ending_material_purge": _bool("ending_material_purge", True),
        # Druckende wie Stock box_end: heizen, schneiden, in die Box zurück
        # (KTWO_BOX_END_PRINT aus END_PRINT). Default AN seit 18.10.2026
        # (Stock entlädt immer, 2x belegt: 12.10., 18.10.,
        # A5); False = Filament bleibt geladen.
        "end_print_unload": _bool("end_print_unload", True),
        # C2 (18.10.2026): Webhook
        # pause_resume/cancel einhüllen – lange Warteschleifen (Einzug bis
        # Kopfsensor, Box-Zustand, Spülen/Ausspülen je Abschnitt) enden bei
        # einem Druckabbruch sauber ohne PAUSE. False = Upstream-Verhalten
        # (CANCEL wartet, bis der laufende Befehl fertig ist).
        "cancel_interrupt": _bool("cancel_interrupt", True),
        # Externe Spule (19.10.2026, README-k2_box.md §18): Quelle cfs|extern
        # + zuletzt geladenes externes Material über Neustarts. Leer = aus
        # (nur Speicher, nach dem Start Quelle CFS).
        "filament_source_file": _str("filament_source_file",
                                     FILAMENT_SOURCE_FILE_DEFAULT),
        # RFID-Leser der externen Spule (0x11, nur Lesebefehl 0x02; Auslöser
        # !PA5 per [gcode_button] -> KTWO_EXTERN_RFID). Default AUS.
        "extern_rfid": _bool("extern_rfid", False),
    }
    for key, default in CHANGE_DEFAULTS.items():
        if key in CHANGE_INT_KEYS:
            cfg[key] = _int(key, default)
        else:
            cfg[key] = _float(key, default)
    if not 0x01 <= cfg["box_addr"] <= 0x04:
        raise ValueError("box_addr muss 1..4 sein (Auto-Addr-Tabelle MB): %r"
                         % (cfg["box_addr"],))
    if not cfg["poll_interval"] > 0:
        raise ValueError("poll_interval muss > 0 sein: %r" % (cfg["poll_interval"],))
    # Stock-Kalibriergrenzen (box.cfg check_cut_pos_x_min/max)
    if not (cfg["check_cut_pos_x_min"] <= cfg["cut_pos_x"]
            <= cfg["check_cut_pos_x_max"]):
        raise ValueError("cut_pos_x %.2f außerhalb check_cut_pos_x_min/max "
                         "[%.2f, %.2f] (Stock-Kalibriergrenzen)" % (
                             cfg["cut_pos_x"], cfg["check_cut_pos_x_min"],
                             cfg["check_cut_pos_x_max"]))
    for key in ("cut_velocity", "cut_accel", "tn_retrude_velocity",
                "tn_extrude_velocity", "clean_velocity", "travel_velocity",
                "z_velocity", "wipe_circle_velocity", "wipe_accel",
                "extrude_sensor_timeout", "extrude_poll_interval",
                "box_wait_timeout", "box_wait_interval",
                "runout_tail_velocity"):
        if not cfg[key] > 0:
            raise ValueError("%s muss > 0 sein: %r" % (key, cfg[key]))
    if cfg["cut_dwell"] > 2.0:
        raise ValueError("cut_dwell > 2 s unplausibel: %r" % (cfg["cut_dwell"],))
    for key in ("buffer_empty_len", "tn_extrude_temp", "change_z_hop", "cut_dwell",
                "stage7_delay", "box_first_clean_length",
                "box_need_clean_length", "wipe_circle_radius", "wipe_shakes"):
        if cfg[key] < 0:
            raise ValueError("%s darf nicht negativ sein: %r" % (key, cfg[key]))
    if cfg["tn_retrude"] > 0:
        raise ValueError("tn_retrude muss <= 0 sein (Rückzug, Stock -10): %r"
                         % (cfg["tn_retrude"],))
    if not 0.0 <= cfg["runout_tail_length"] <= 100.0:
        raise ValueError("runout_tail_length muss 0..100 mm sein (Stock 30): %r"
                         % (cfg["runout_tail_length"],))
    if not 80 <= cfg["max_tube_length"] <= 10000:
        raise ValueError("max_tube_length muss 80..10000 mm sein (Stock 3000): "
                         "%r" % (cfg["max_tube_length"],))
    if not cfg["ending_material_timeout"] > 0:
        raise ValueError("ending_material_timeout muss > 0 s sein: %r"
                         % (cfg["ending_material_timeout"],))
    if cfg["change_z_hop"] > 5.0:
        raise ValueError("change_z_hop > 5 mm unplausibel (Stock 0.4): %r"
                         % (cfg["change_z_hop"],))
    if cfg["flush_mode"] not in FLUSH_MODES:
        raise ValueError("flush_mode muss %s sein: %r" % (
            "|".join(FLUSH_MODES), cfg["flush_mode"]))
    if cfg["flush_clean"] not in FLUSH_CLEAN_MODES:
        raise ValueError("flush_clean muss %s sein: %r" % (
            "|".join(FLUSH_CLEAN_MODES), cfg["flush_clean"]))
    # Stock-Grenzen (BoxCfg.__init__ getX min/max)
    for key, lo, hi in (("nozzle_volume", 0.0, 300.0),
                        ("flush_multiplier", 0.0, 10.0),
                        ("flush_velocity_factor", 1.0, 5.0),
                        ("max_temp_flush_times", 0, 10),
                        ("diff_material_type_min_flush_len", 0.0, 1000.0),
                        ("box_need_clean_length_max", 0.0, 600.0),
                        ("tn_extrude", 0.0, 200.0),
                        ("flush_fan_start", 0.0, 255.0),
                        ("flush_blow_time", 0.0, 30.0),
                        ("flush_wipe_offset", 0.0, 10.0),
                        ("flush_clog_min_mm", 0.0, 100.0)):
        if not lo <= cfg[key] <= hi:
            raise ValueError("%s muss %s..%s sein: %r" % (key, lo, hi, cfg[key]))
    if not 0.0 <= cfg["wipe_minimum_cruise_ratio"] < 1.0:
        raise ValueError("wipe_minimum_cruise_ratio muss 0..<1 sein (Upstream "
                         "MINIMUM_CRUISE_RATIO): %r"
                         % (cfg["wipe_minimum_cruise_ratio"],))
    if not cfg["baseline_nozzle_diameter"] > 0:
        raise ValueError("baseline_nozzle_diameter muss > 0 sein: %r"
                         % (cfg["baseline_nozzle_diameter"],))
    return cfg


def parse_klipper_config(config):
    """Klipper-ConfigWrapper -> parse_options (ANNAHME zur API).

    Versucht get_options()/get wie in k2_motor_init.parse_klipper_config.
    """
    keys = []
    try:
        if hasattr(config, "get_options"):
            keys = list(config.get_options())
        elif hasattr(config, "get_prefix_options"):
            # Upstream-ConfigWrapper (configfile.py): alle Optionen des
            # Abschnitts; jede gelesene Option gilt als benutzt.
            keys = list(config.get_prefix_options(""))
        elif hasattr(config, "_options"):
            keys = list(dict(config._options).keys())
        elif isinstance(config, dict):
            keys = list(config.keys())
    except Exception:
        keys = []
    opts = {}

    def _g(name, default=None):
        try:
            if hasattr(config, "get"):
                return config.get(name, default)
        except Exception:
            pass
        if isinstance(config, dict):
            return config.get(name, default)
        return default

    if not keys and isinstance(config, dict):
        return parse_options(config)
    for k in keys:
        opts[k.lower()] = _g(k, "")
    for k in (("box_addr", "allow_write", "shutdown_on_failure", "auto_init",
               "poll_interval", "allow_move", "pause_on_failure",
               "filament_sensor", "cut_sensor", "runout_refill",
               "runout_require_color", "runout_resume",
               "resume_redo_change", "material_database", "flush_mode",
               "remain_material_file", "flush_clean", "flush_blow", "flush_blow_fan",
               "flush_clog_check", "ending_material_purge", "end_print_unload",
               "cancel_interrupt", "filament_source_file", "extern_rfid")
              + tuple(CHANGE_DEFAULTS)):
        if k not in opts:
            v = _g(k, None)
            if v is not None:
                opts[k] = v
    return parse_options(opts)


# =====================================================================
# Auto-Addr-Adapter (Box-Seite, erst nach k2_motor:ready)
# =====================================================================


def reactor_sleep(reactor):
    """Relative Pause für Klipper: reactor.pause() erwartet eine ABSOLUTE
    Weckzeit (upstream reactor.py: pause(waketime)). Ohne Reactor -> time.sleep."""
    if reactor is None or not hasattr(reactor, "pause") \
            or not hasattr(reactor, "monotonic"):
        return time.sleep
    return lambda s: reactor.pause(reactor.monotonic() + float(s))


class ReactorBusProxy:
    """Bus-Aufrufe in einem Hilfs-Thread; der Klipper-Reactor pausiert
    derweil in kleinen Schritten (andere Timer laufen weiter).

    Grund: Box-Antworten kommen erst NACH dem Vorgang (0x10 mode 0 live
    4,3–5,4 s, 0x11 p1 0,8–22,4 s, 0x0d op 2 31–37 s; Beleg
    Live-Mitschnitt). Ein blockierender Aufruf im Reactor würde Heizungs-
    PWM-Updates (max_duration) und die MCU-Uhr-Synchronisation aufhalten –
    bei heißer Düse droht ein MCU-Shutdown. Der Bus-Lock (serial485,
    threading.Lock) serialisiert weiterhin alle Teilnehmer.
    Callbacks des Controllers (Sensor, Extruder-Bewegung) laufen NICHT im
    Thread, sondern im Reactor (nur transact/transact_full sind verlagert).
    """

    def __init__(self, bus, reactor, step=0.02):
        self.bus = bus
        self.reactor = reactor
        self.step = float(step)

    def _run(self, name, *args, **kwargs):
        res = {}

        def _work():
            try:
                res["v"] = getattr(self.bus, name)(*args, **kwargs)
            except BaseException as exc:  # an den Aufrufer weiterreichen
                res["e"] = exc

        t = threading.Thread(target=_work, name="k2_box-bus")
        t.daemon = True
        t.start()
        while t.is_alive():
            self.reactor.pause(self.reactor.monotonic() + self.step)
        t.join()
        if "e" in res:
            raise res["e"]
        return res.get("v")

    def transact_full(self, *args, **kwargs):
        return self._run("transact_full", *args, **kwargs)

    def transact(self, *args, **kwargs):
        return self._run("transact", *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self.bus, name)

class BoxAutoAddrSerial:
    """Adapter: auto_addr-Serial-API um einen Box-Bus (serial485-artig).

    auto_addr.AutoAddr braucht ``cmd_send_data_with_response(data, timeout,
    retransmit)`` mit Wire-Bytes OHNE Head/CRC ([addr][len][status][fun]
    [daten...]) und liefert den VOLLEN Antwort-Frame ([F7]...[crc]) oder
    None bei Timeout (Beleg auto_addr.py send_package/data_handler).

    Umsetzung hier: Wire zerlegen -> bus.transact_full(addr, fun, payload,
    timeout, retries=0, status, expect_fun=fun, expect_addr=None) – mit
    expect_addr=None, weil Broadcasts (0xFE) mit Geräte- ODER neuer Adresse
    antworten (Beleg Live-Mitschnitt: A1 an 0xFE, A0-Antwort von neuer
    Adresse, 3x). retries=0 wie Original (Auto-Addr/Broadcasts).
    """

    def __init__(self, bus, allow_write=True):
        if bus is None or not hasattr(bus, "transact_full"):
            raise ValueError("BoxAutoAddrSerial braucht bus.transact_full()")
        self.bus = bus
        self.allow_write = bool(allow_write)
        self.calls = []  # [(addr, fun, tuple(payload))] – Diagnose

    def cmd_send_data_with_response(self, data, timeout, retransmit):
        wire = bytes(data)
        if len(wire) < 4:
            return None
        addr, _length, status, fun = wire[0], wire[1], wire[2], wire[3]
        payload = list(wire[4:])
        self.calls.append((addr, fun, tuple(payload)))
        # 0xA0 (Adressvergabe) ist schreibend – ohne allow_write blocken.
        if fun == 0xA0 and not self.allow_write:
            return None
        check_not_forbidden(fun)
        try:
            _raddr, _rstatus, _rfun, _rdata, rraw = self.bus.transact_full(
                addr, fun, payload, timeout=timeout, retries=0,
                status=status, expect_fun=fun, expect_addr=None)
        except (TimeoutError, ValueError):
            return None
        return bytes(rraw)


# =====================================================================
# Controller – reine Box-Logik (ohne Klipper importierbar)
# =====================================================================

class K2BoxController:
    """Box-Abläufe gegen den Bus (vgl. BoxAction.communication_*).

    ``bus`` ist serial485-artig (transact/transact_full, z.B.
    serial485.Serial485 für /dev/ttyS5 – DIESELBE Instanz wie [k2_motor],
    damit EIN Lock den Halbduplex-Bus serialisiert). Alle BoxAction-Aufrufe
    laufen darüber (Statusprüfung via CfsStatusError, s. box.BoxAction).
    """

    def __init__(self, cfg, bus, auto_addr_obj=None, pause=None,
                 logger=None, send_event=None):
        self.cfg = dict(cfg)
        if bus is None or not hasattr(bus, "transact_full"):
            # Fallback auf transact (Status dann unbekannt – wie BoxAction).
            if bus is None or not hasattr(bus, "transact"):
                raise ValueError("K2BoxController braucht bus.transact_full() "
                                 "(serial485-artig, geteilt mit [k2_motor])")
        self.bus = bus
        self.action = BoxAction(bus, addr=self.cfg.get("box_addr", BOX_ADDR_DEFAULT))
        self.auto_addr = auto_addr_obj
        self._addr_store = {}
        self.pause = pause if pause is not None else time.sleep
        self.log = logger if logger is not None else self._print_log()
        self.send_event = send_event
        self.motor_ready = False
        self.is_ready = False
        self.last_poll = None  # parse_box_state-Dict (Cache für Moonraker)
        self.last_poll_raw = None  # StatusBytes
        self.box_fw = None  # z.B. "153" aus 0x14 (für parse_box_state)
        self.slot_cache = {}  # letter -> {rfid, remain}
        self._stop = threading.Event()
        self._thread = None
        # Filamentwechsel: zuletzt geladener Slot (None = leer/unbekannt),
        # busy = langer Box-Vorgang läuft (Poll-Timer pausiert dann).
        self.loaded_slot = None
        self.busy = False
        # Stock-Retry-Schritte des letzten do_extrude (auch bei Abbruch)
        self.last_extrude_retries = []
        self.clock = time.monotonic
        # Auto-Nachladen (A7): Slot, den die Box im PRINT-Zustand zuletzt im
        # Hub gemeldet hat (0x0a b4), und letztes LESBARES RFID-Etikett je
        # Slot (die leere Spule liefert evtl. nur noch "unknown").
        self.print_slot = None
        self.tag_cache = {}
        # callable(letter) – ein LESBARES Etikett kam an (Hülle löscht dann
        # den Handwert des Slots, KTWO_BOX_SLOT_SET). Fehler darin stören nie.
        self.on_rfid_tag = None
        # Restmaterial (Stock send_data -> is_use_ending_material): die
        # Klipper-Hülle wertet jeden 0x0a-Poll aus (Status 0x50 im Druck);
        # callable(parsed) oder None. Fehler darin stören den Poll nie.
        self.on_poll = None
        # Druckweiter Box-Zustand (A2, Live-Abgleich 18.10.2026):
        # True = KTWO_BOX_PRINT_START hat 0x0d [0f 00] + 0x0f [01]
        # gesendet (Auto-Preload aus + Sperre an), bis KTWO_BOX_PRINT_END.
        # Solange schaltet der Einzug 0x0f NICHT um (Stock T0 im Druck).
        self.print_hold = False
        # C2 (18.10.2026): callable() -> True, wenn ein Druckabbruch wartet
        # (Klipper-Hülle: Webhook pause_resume/cancel). Lange Warteschleifen
        # (Einzug bis Kopfsensor, Box-Zustand abwarten) enden dann sauber
        # mit BoxCancelled. None = aus. Einzelne Bus-Transaktionen (0x11 p1,
        # 0x0d op 2/3) werden NIE mittendrin verlassen.
        self.cancel_check = None
        # Externe Spule (19.10.2026): callable(frames) -> Grund (str) oder
        # None; frames = Liste (fun, Daten-Tupel) des Ablaufs bzw. None
        # (Ablauf mit Filamentbewegung/Modus). Gesetzt (Klipper-Hülle: Quelle
        # EXTERN) -> do_preload/do_extrude/do_retrude senden NICHTS
        # (PermissionError); do_print_state nur, weil seine Frames genau
        # EXTERN_ALLOWED_FRAMES sind (Stock live). Lesende Abfragen
        # (0x0a-Poll, RFID, Sensoren) bleiben erlaubt.
        self.write_block = None

    def _check_write_block(self, name, frames=None):
        """Quelle EXTERN o. Ä.: schreibenden Box-Ablauf ablehnen, bevor ein
        Frame gesendet wird. ``frames`` = genau die Frames des Ablaufs
        (fun, Daten) – nur damit kann die Sperre eine enge Ausnahme machen."""
        fn = self.write_block
        if fn is None:
            return
        try:
            why = fn(None if frames is None else
                     [(int(f), tuple(int(b) for b in d)) for f, d in frames])
        except Exception:
            why = "Sperrprüfung fehlgeschlagen"
        if why:
            raise PermissionError("%s gesperrt: %s – nichts gesendet"
                                  % (name, why))

    def _cancel_wait(self, what):
        """C2: Abbruch-Flag in einer Warteschleife abfragen -> BoxCancelled."""
        fn = self.cancel_check
        if fn is None:
            return
        try:
            hit = fn()
        except Exception:
            hit = False
        if hit:
            raise BoxCancelled("%s: Druckabbruch (CANCEL) – Warten beendet"
                               % (what,))

    class _print_log:
        def info(self, msg):
            print(msg)

        def error(self, msg):
            print("FEHLER: %s" % msg)

    # --- Helfer -------------------------------------------------------
    def _require_write(self, name):
        """Schreib-Guard: ohne allow_write -> PermissionError, NICHTS gesendet."""
        if not self.cfg.get("allow_write"):
            raise PermissionError(
                "%s braucht allow_write: True (Default False → nur lesen + "
                "Plan, nichts gesendet)" % name)
        return True

    def _odo_mm(self, raw4):
        """4 B Odometer-Float BE (0x10 mode 5) -> mm (Betrag, positiv)."""
        raw = bytes(raw4)
        if len(raw) != 4:
            raise ValueError("Odometer-Antwort muss 4 Bytes haben: %r" % (raw,))
        (neg,) = struct.unpack(">f", raw)
        return -float(neg)

    # --- Plan-Text (Trockenlauf, kein Bus) ------------------------------
    def describe_plan(self):
        out = ["K2-Box-Plan (Trockenlauf, nichts gesendet)"]
        out.append("box_addr=0x%02X allow_write=%s poll_interval=%.1f s" % (
            self.cfg.get("box_addr", BOX_ADDR_DEFAULT),
            self.cfg.get("allow_write"), self.cfg.get("poll_interval",
                                                      POLL_INTERVAL_DEFAULT)))
        out.append("A0 Adressvergabe (Auto-Addr 0xA0-0xA3, erst nach k2_motor:ready)")
        out.append("A1 Status-Poll 0x0a (retries=0, alle ~%.0f s wie Original)" % (
            self.cfg.get("poll_interval", POLL_INTERVAL_DEFAULT),))
        out.append("A2 lesend: 0x02 RFID / 0x03 Remain / 0x14 Version / 0x15 HW / "
                   "0x08 Sensor / 0x05 Buffer / 0x0e Messrad (retries wie Original)")
        out.append("A3 Preload 0x0d op 0 disable / 1 enable / 2 Force+RFID / "
                   "3 Force ohne RFID (op 2/3: Timeout >=380 s, retries=0, keine Bursts)")
        out.append("A4 Extrude (Load): 04 IDLE → 0f an → 08 Sensor → 10/00 → 10/04 → "
                   "10/05 x%d → 10/06 → 10/07 p=3 → 05 → 04 PRINT → 0f aus" % ODO_POLLS_DEFAULT)
        out.append("A5 Retrude (Unload): 04 IDLE → 08 Sensor → 11 p0 x%d → 11 p1" % (
            RETRUDE_REPEATS_DEFAULT,))
        out.append("A6 Cut: Kopfbewegung pre_cut_pos -> cut_pos_x %s -> zurück "
                   "(KTWO_BOX_CUT, allow_move; sendet KEIN Box-Frame)" % (
                       _n(self.cfg.get("cut_pos_x", CHANGE_DEFAULTS["cut_pos_x"])),))
        out.append("Verboten: nie 0x50 (Reset) / nie 0x06-Feeder (FW-Bug); "
                   "Bus-Lock mit [k2_motor] teilen (eine ttyS5-Instanz).")
        if not self.cfg.get("allow_write"):
            out.append("Hinweis: allow_write=False → nur lesen + Plan (keine "
                       "0x0d/0x04/0x0f/0x10/0x11, kein 0xA0).")
        return "\n".join(out)

    # --- A0 Adressvergabe ------------------------------------------------
    def on_motor_ready(self):
        """Event k2_motor:ready → Adressvergabe starten (s. PrinterK2Box)."""
        self.motor_ready = True
        return self.ensure_addr(motor_ready=True)

    def ensure_addr(self, motor_ready=None):
        """Auto-Addr-Ablauf (startup + ein Online-Zyklus).

        Erst nach k2_motor:ready (Beleg Stock-Bootsequenz
        Schritt 11: Motor-Init → auto_addr:start). Ohne motor_ready →
        Status wait_motor, NICHTS gesendet. Ohne allow_write → nur lesende
        Anteile? Entwurf: ganz übersprungen (0xA0 ist schreibend), Status
        skipped – lesende Polls laufen trotzdem (Adresse meist schon vergeben).
        """
        ready = self.motor_ready if motor_ready is None else bool(motor_ready)
        if not ready:
            info = "wartet auf k2_motor:ready (nichts gesendet)"
            try:
                self.log.info(info)
            except Exception:
                pass
            return {"status": "wait_motor", "log": info, "ready": False}
        if not self.cfg.get("allow_write"):
            info = ("allow_write=False: keine Adressvergabe (0xA0 schreibend) – "
                    "nur lesende Polls")
            try:
                self.log.info(info)
            except Exception:
                pass
            return {"status": "skipped", "log": info, "ready": False}
        if self.auto_addr is None:
            serial = BoxAutoAddrSerial(self.bus, allow_write=True)
            self.auto_addr = _AA.AutoAddr(serial, store=self._addr_store)
        try:
            self.auto_addr.startup()
            txt = self.auto_addr.online_once()
        except Exception as exc:
            try:
                self.log.error("Auto-Addr: %s" % exc)
            except Exception:
                pass
            return {"status": "addr_error", "log": str(exc), "ready": False}
        self.is_ready = True
        try:
            if self.send_event is not None:
                self.send_event("k2_box:ready")
        except Exception as exc:
            try:
                self.log.info("Event k2_box:ready nicht sendbar: %s" % exc)
            except Exception:
                pass
        return {"status": "ready", "log": txt, "ready": True}

    def readdr(self, printing=False):
        """EIN Auto-Addr-Durchlauf (Stock process_all) für die Wiederanmeldung
        einer Box nach Stromverlust/Wiedereinstecken: 0xA1-Broadcast -> die
        neu gestartete Box meldet ihre UID („slave restarted“) -> 0xA0 ->
        online; steht sie im Bootloader, erst 0x0B [01] an 0xFF. None = nicht
        möglich (erste Adressvergabe fehlt oder allow_write=False – 0xA0 ist
        schreibend)."""
        if self.auto_addr is None or not self.cfg.get("allow_write"):
            return None
        return self.auto_addr.online_once(printing)

    # --- A1 Status-Poll (lesend, retries=0) -------------------------------
    def poll_status(self):
        """0x0a-Poll (retries=0 wie Original), Cache für Moonraker.

        Lesend – auch ohne allow_write erlaubt (Herzschlag alle ~4 s, Beleg
        Live-Mitschnitt: 10 560 Polls, retries=0). Gibt
        parse_box_state-Dict zurück (mit temp_c/rh/hub_mask/state).
        """
        raw = self.action.communication_get_box_state()
        self.last_poll_raw = raw
        try:
            parsed = parse_box_state(raw, box_fw=self.box_fw)
        except Exception:
            parsed = parse_box_state(raw)
        self.last_poll = parsed
        if parsed.get("kind") == "state" \
                and parsed.get("state") == _BOX.BOX_STATE_PRINT \
                and parsed.get("hub_mask") in SLOT_MASKS:
            self.print_slot = SLOT_LETTERS[SLOT_MASKS.index(
                parsed.get("hub_mask"))]
        if self.on_poll is not None:
            try:
                self.on_poll(parsed)
            except Exception:
                pass
        return parsed

    def start_background(self, interval=None):
        """Hintergrund-Poll (Threading, offline/Test) statt Reactor-Timer."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        wait = float(interval if interval is not None
                     else self.cfg.get("poll_interval", POLL_INTERVAL_DEFAULT))

        def loop():
            while not self._stop.is_set():
                try:
                    self.poll_status()
                except Exception:
                    pass
                if self._stop.wait(wait):
                    break

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop_background(self):
        self._stop.set()

    # --- A2 lesende Abfragen (auch ohne allow_write) ----------------------
    def get_rfid(self, slot):
        idx, letter, _mask = parse_slot(slot)
        mask = SLOT_MASKS[idx]
        raw = self.action.communication_get_rfid(mask)
        parsed = get_rfid(raw)
        val = parsed.get(letter)
        self._note_rfid(letter, val)
        return val

    def _note_rfid(self, letter, val):
        """RFID-Wert in Slot-Cache/Etikett-Cache übernehmen (+ Rückruf)."""
        self.slot_cache.setdefault(letter, {})["rfid"] = val
        if rfid_material(val) is not None:
            self.tag_cache[letter] = val
            if self.on_rfid_tag is not None:
                try:
                    self.on_rfid_tag(letter)
                except Exception:
                    pass

    def get_rfid_all(self):
        """RFID aller Slots mit EINEM 0x02 [0f] (lesend, Beleg Stock-box_wrapper
        communication_get_rfid(1, 15)) -> {Buchstabe: Wert}."""
        raw = self.action.communication_get_rfid(MASK_ALL)
        parsed = get_rfid(raw)
        for letter, val in parsed.items():
            self._note_rfid(letter, val)
        return dict(parsed)

    def inlet_mask(self):
        """Einlauf-Sensoren (0x08 [00] = Sensor A, Filament je Slot) – lesend."""
        return int(self.get_sensor(0x00)) & MASK_ALL

    def get_remain(self, mask=MASK_ALL):
        if isinstance(mask, bool) or not isinstance(mask, int) \
                or not 0 <= mask <= MASK_ALL:
            raise ValueError("Maske muss 0x00..0x0f sein: %r" % (mask,))
        raw = self.action.communication_get_remain_len(mask)
        parsed = get_remain_len(bytes([mask]), raw)
        for letter, val in parsed.items():
            # nur angefragte Slots übernehmen (0xff außerhalb der Maske
            # = „nicht gefragt“, nicht „unbekannt“ – 18.10.2026)
            if letter in SLOT_LETTERS and \
                    mask & SLOT_MASKS[SLOT_LETTERS.index(letter)]:
                self.slot_cache.setdefault(letter, {})["remain"] = val
        return parsed

    def get_version_sn(self):
        raw = self.action.communication_get_version_sn()
        ver, sn = parse_version_sn(raw)
        self.box_fw = ver
        return ver, sn

    def get_hardware_status(self):
        raw = self.action.communication_get_hardware_status()
        return parse_get_hardware_status(raw)

    def get_sensor(self, pos=0x00):
        raw = self.action.communication_get_filament_sensor_state(pos)
        return parse_filament_sensor_state(raw)

    def get_buffer(self):
        raw = self.action.communication_get_buffer_state()
        return parse_buffer_state(raw)

    def get_wheel(self):
        return self.action.communication_measuring_wheel()

    # --- A3 Preload -------------------------------------------------------
    def do_preload(self, slot, op, timeout=None, dry_run=False, guard=None):
        """0x0d [Maske][op] (op 0-3, Beleg CFS-Firmware-Analyse).
        ANMERKUNG N5: Stock sendet nach jedem
        (Re-)Connect 0x0d [0f 01]/[0f 00] (preloading_en) – hier bewusst
        NICHT (offener Gerätetest: Auto-Preload nach Box-Reset ohne Host-
        [0f 01]?).

        op 2/3: Antwort erst nach dem Vorgang → langer Timeout
        (preload_force_timeout) + retries=0 (kein Burst).
        Schreibend → allow_write nötig (auch op 0/1 ändern PRE[1] global).
        guard (17.10.2026, Stock-Schutz, s. preload_block_reason): dict
        head (Kopfsensor True/False/None), printing (Druck/Pause), force
        (FORCE=1) – gesperrt -> PermissionError, NICHTS gesendet. Die
        Klipper-Hülle (KTWO_BOX_PRELOAD) übergibt es immer; ohne guard
        (Controller-Tests, Werkzeuge) nur die Maske-0-Sperre.
        """
        idx, letter, mask = parse_slot(slot)
        op = parse_preload_op(op)
        if dry_run or not self.cfg.get("allow_write"):
            if dry_run:
                return {"status": "dry_run", "slot": letter, "op": op,
                        "plan": "0x0d [%02x %02x] (%s)" % (
                            mask, op, PRELOAD_OP_NAMES[op])}
            self._require_write("Preload SLOT=%s OP=%d" % (letter, op))
        self._check_write_block("Preload SLOT=%s OP=%d" % (letter, op))
        g = dict(guard) if guard is not None else {"head": False,
                                                   "printing": False}
        why = preload_block_reason(op, mask, g.get("head"),
                                   bool(g.get("printing")),
                                   bool(g.get("force")))
        if why:
            raise PermissionError("Preload SLOT=%s OP=%d gesperrt: %s – "
                                  "nichts gesendet" % (letter, op, why))
        check_not_forbidden(_BOX.FUNC_SET_PRE_LOADING)
        raw = self.action.communication_set_pre_loading(mask, op,
                                                       timeout=timeout)
        return {"status": "ok", "slot": letter, "op": op, "raw": bytes(raw)}

    # --- Druckweiter Box-Zustand (A2, 18.10.2026) ---------------------------
    def do_print_state(self, start):
        """Druckbeginn/-ende wie Stock BOX_START_PRINT / BOX_END_PRINT
        (Live-Abgleich 18.10.2026; Live 16:45:36
        ``0x0d [0f 00]`` + ``0x0f [01]``, 17:44:08 ``0x0d [0f 01]`` +
        ``0x0f [00]``):
        start=True  -> 0x0d [0f 00] (Auto-Preload/Einlesen aus, alle Slots)
                       + 0x0f [01] (Sperre an); print_hold = True
        start=False -> 0x0d [0f 01] + 0x0f [00]; print_hold = False
        Maske fest MASK_ALL (nie 0); op 0/1 antworten sofort
        (Original-Retries). Stock sendet beides mit force=True auch im
        Druck – preload_block_reason(force=True) erlaubt op 0/1. Schreibend
        -> allow_write nötig. Am Ende wird print_hold auch bei einem
        Fehler zurückgesetzt (der Druck ist vorbei; Einzüge senden danach
        wieder ihr 0x0f-Paar)."""
        op = PRELOAD_DISABLE if start else PRELOAD_ENABLE
        mask = MASK_ALL
        if not self.cfg.get("allow_write"):
            self._require_write("Druck%s 0x0d/0x0f" % (
                "beginn" if start else "ende"))
        self._check_write_block("Druck%s 0x0d/0x0f" % (
            "beginn" if start else "ende"),
            [(_BOX.FUNC_SET_PRE_LOADING, (mask, op)),
             (FUNC_PRELOAD_INHIBIT, (1 if start else 0,))])
        why = preload_block_reason(op, mask, None, True, force=True)
        if why or not mask:
            raise PermissionError("Box-Druckzustand gesperrt: %s" % (
                why or "Maske 0"))
        check_not_forbidden(_BOX.FUNC_SET_PRE_LOADING)
        check_not_forbidden(FUNC_PRELOAD_INHIBIT)
        frames = []
        try:
            self.action.communication_set_pre_loading(mask, op)
            frames.append("0x0d [%02x %02x]" % (mask, op))
            self.action.communication_set_preload_inhibit(bool(start))
            frames.append("0x0f [%02x]" % (1 if start else 0))
            if start:
                self.print_hold = True
        finally:
            if not start:
                self.print_hold = False
        return {"status": "ok", "start": bool(start), "frames": frames}

    # --- A4 Extrude (Load, Box-Seite) --------------------------------------
    def do_extrude(self, slot, odo_polls=ODO_POLLS_DEFAULT, dry_run=False,
                   sensor_fn=None, sensor_timeout=90.0, poll_interval=2.0,
                   sensor_check=0.1, stage7_fn=None, stage7_delay=0.0,
                   retry_fn=None, inhibit=True):
        """Box-Extrude A4 (Byte-folge == cfsseq.build_extrude_sequence).

        ``inhibit`` (18.10.2026, A2): False = 0x0f [01] vor und 0x0f [00]
        nach dem Einzug weglassen – im Druck hält KTWO_BOX_PRINT_START die
        Sperre druckweit (Stock T0 im Druck: 04 [00 01] -> 08 [00] -> 10 …,
        kein 0x0f, Live-Abgleich 18.10.2026); ein
        0x0f [00] würde sie mitten im Druck aufheben. Default True =
        bisherige Folge (außerhalb eines Drucks).

        Abbruch bei Status != 0: BoxAction wirft CfsStatusError (mit
        Status-Name, z.B. 0x0c Odometer-Stillstand) – hier mit Slot-Kontext
        angereichert und propagiert (Auftrag: „Abbruch mit CfsStatusError-Text“).

        Stock-Retry bei 0x0c (18.10.2026, Abgleich mit gitstonelabs;
        Beleg K2-Stock-Live-Log 2026-10-12 + Hi-Mitschnitt,
        Folge = cfsseq.build_extrude_failure_path): meldet ein mode-5-Reply
        (bzw. mode 4) 0x0c, wird mode 4 EINMAL neu gestartet (vorher
        ``retry_fn("restart", exc)`` – K2-Stock „move to retry“ =
        Düsenreinigung); erneut 0x0c -> 0x04 [00 01] + 0x11 [m 01]
        (``retry_fn("full", exc)``) und EIN kompletter Neuversuch ab mode 0
        (ohne 0x0f/0x08-Präfix, wieder mit 1x mode-4-Neustart). Scheitert
        auch er: Abbruch mit dem 0x0c ohne weiteres 04/11 (Box bleibt ERROR
        wie Stock, KTWO_BOX_ERROR_CLEAR). 0x09 auf mode 0 (Odometer steht in
        feed_top_to_hub): 0x04 [00 01] + 0x11 [m 01] wie Stock, dann Abbruch
        (Hi versucht danach erneut – hier bewusst nicht). Andere Status:
        sofortiger Abbruch wie bisher. Grenzen fest (cfsseq.EXTRUDE_RETRY_MAX
        = 1, EXTRUDE_FEED_RESTARTS = 1) -> höchstens 4 Förderphasen; das
        Sensor-Budget ``sensor_timeout`` gilt je Förderphase (Stock
        „timeout = 90“ nach jedem mode 4).

        Wechsel-Erweiterung (16.10.2026, Beleg Live-Wechsel 12./13.10.):
        - ``sensor_fn`` (callable -> bool, Kopf-Filamentsensor): statt fester
          ``odo_polls`` wird mode 5 alle ``poll_interval`` s abgefragt, bis
          der Kopfsensor Filament meldet (live: mode 6 kommt aus dem
          Sensor-Ereignis, „timeout = 90“). Kein Sensor nach
          ``sensor_timeout`` s -> Box per 0x04 [00 01] auf IDLE (Fördern
          stoppen, ANNAHME) + BoxChangeError.
        - ``stage7_fn`` (callable): zwischen mode 6 und mode 7 (live Ereignis
          ``extrude_process_stage7``) – Drucker lässt den Extruder greifen;
          danach ``stage7_delay`` s warten, dann mode 7 p=3 (Box prüft, dass
          das Odometer > p mm in 300 ms läuft, sonst 0x0e).
        Ohne beide Callbacks bleibt die Frame-Folge byte-identisch zu
        EXTRUDE_LIVE_CMDS_B.
        """
        idx, letter, mask = parse_slot(slot)
        if isinstance(odo_polls, bool) or not isinstance(odo_polls, int) \
                or odo_polls < 1:
            raise ValueError("odo_polls muss int >= 1 sein: %r" % (odo_polls,))
        if dry_run or not self.cfg.get("allow_write"):
            if dry_run:
                return {"status": "dry_run", "slot": letter,
                        "plan": self.describe_plan()}
            self._require_write("Extrude SLOT=%s" % letter)
        self._check_write_block("Extrude SLOT=%s" % letter)
        for fun in (FUNC_SET_BOX_MODE, FUNC_PRELOAD_INHIBIT, FUNC_EXTRUDE,
                    FUNC_RETRUDE):
            check_not_forbidden(fun)
        odo_trace = []
        retries = []
        stall = _BOX.STATUS_ODOMETER_STALL
        hub_no_move = _BOX.STATUS_HUB_NO_MOVE
        max_full = int(getattr(_SEQ, "EXTRUDE_RETRY_MAX", 1))
        max_restart = int(getattr(_SEQ, "EXTRUDE_FEED_RESTARTS", 1))

        def _run(mode, p, label):
            try:
                raw = self.action.communication_extrude(mask, mode, p)
            except CfsStatusError as exc:
                raise CfsStatusError(
                    exc.status,
                    "%s SLOT=%s: %s" % (label, letter, exc.name),
                    exc.data, exc.fun)
            if mode == _SEQ.EXTRUDE_MODE_QUERY:
                try:
                    odo_trace.append(self._odo_mm(bytes(raw)))
                except ValueError:
                    pass
            return raw

        def _stop_feed():
            # Best effort: IDLE stoppt/löscht (live 0x04 [00 01] aus ERROR).
            try:
                self.action.communication_set_box_mode_feed()
            except Exception:
                pass

        def _short_retrude():
            # Stock-Kurzform nach Abbruch: 04 [00 01] -> 11 [m 01]
            self.action.communication_set_box_mode_feed()
            self.action.communication_retrude(mask, RETRUDE_P_PULL_HUB)

        def _feed():
            # Eine Förderphase: mode 4 + mode-5-Polls (fest bzw. bis Kopf).
            _run(_SEQ.EXTRUDE_MODE_FEED_START, 0x00, "Feed-Start (mode 4)")
            if sensor_fn is None:
                for _ in range(odo_polls):
                    _run(_SEQ.EXTRUDE_MODE_QUERY, 0x00, "Odometer-Poll (mode 5)")
                return
            t0 = self.clock()
            _run(_SEQ.EXTRUDE_MODE_QUERY, 0x00, "Odometer-Poll (mode 5)")
            last = self.clock()
            while not sensor_fn():
                try:
                    self._cancel_wait("Einzug SLOT=%s" % letter)
                except BoxCancelled:
                    # wie beim Sensor-Timeout: Box per 0x04 [00 01] auf IDLE
                    # (Fördern stoppen), dann sauber raus
                    _stop_feed()
                    raise
                now = self.clock()
                if now - t0 > float(sensor_timeout):
                    _stop_feed()
                    raise BoxChangeError(
                        "Einzug SLOT=%s: Kopf-Filamentsensor nach %.0f s "
                        "nicht erreicht (Odometer %s mm) – Box auf IDLE" % (
                            letter, float(sensor_timeout),
                            round(odo_trace[-1], 1) if odo_trace else "?"))
                if now - last >= float(poll_interval):
                    _run(_SEQ.EXTRUDE_MODE_QUERY, 0x00,
                         "Odometer-Poll (mode 5)")
                    last = self.clock()
                self.pause(float(sensor_check))

        def _attempt():
            # Ein Versuch: mode 0, dann Förderphase mit höchstens
            # max_restart mode-4-Neustarts bei 0x0c.
            try:
                _run(_SEQ.EXTRUDE_MODE_STAGE1, 0x00, "Stage1 (mode 0)")
            except CfsStatusError as exc:
                if exc.status == hub_no_move:
                    # Stock (Hi-Mitschnitt): 04 [00 01] + 11 [m 01]
                    try:
                        _short_retrude()
                    except Exception:
                        pass
                    retries.append("0x09-abbruch")
                raise
            restarts = 0
            while True:
                try:
                    _feed()
                    return
                except CfsStatusError as exc:
                    if exc.status != stall or restarts >= max_restart:
                        raise
                    restarts += 1
                    retries.append("mode4-neustart")
                    if retry_fn is not None:
                        retry_fn("restart", exc)

        # C2: Abbruch schon vor dem ersten Frame -> gar nicht erst fördern
        self._cancel_wait("Einzug SLOT=%s (vor dem Start)" % letter)
        self.busy = True
        try:
            self.action.communication_set_box_mode_feed()
            if inhibit:
                self.action.communication_set_preload_inhibit(True)
            self.action.communication_get_filament_sensor_state(0x00)
            full = 0
            while True:
                try:
                    _attempt()
                    break
                except CfsStatusError as exc:
                    if exc.status != stall or full >= max_full:
                        if full:
                            raise CfsStatusError(
                                exc.status, "%s (nach Stock-Neuversuch)"
                                % (exc.name,), exc.data, exc.fun)
                        raise
                    full += 1
                    retries.append("neuversuch")
                    if retry_fn is not None:
                        retry_fn("full", exc)
                    _short_retrude()
            _run(_SEQ.EXTRUDE_MODE_STOP_BUFFER, 0x00, "Stopp/Buffer (mode 6)")
            if stage7_fn is not None:
                stage7_fn()
                if stage7_delay:
                    self.pause(float(stage7_delay))
            _run(_SEQ.EXTRUDE_MODE_EXTRUDER_CHECK, EXTRUDER_CHECK_MM,
                 "Extruder-Check (mode 7 p=3)")
            self.action.communication_get_buffer_state()
            self.action.communication_set_box_mode_print(mask)
            if inhibit:
                self.action.communication_set_preload_inhibit(False)
        finally:
            self.busy = False
            self.last_extrude_retries = list(retries)
        self.loaded_slot = letter
        return {"status": "ok", "slot": letter, "odo_mm": list(odo_trace),
                "retries": list(retries)}

    def do_load(self, slot, dry_run=False, odo_polls=ODO_POLLS_DEFAULT,
                **extrude_kw):
        """Box-Load = Extrude-Ablauf A4 (Toolhead-Heizen/Fahren macht der
        Aufrufer per Makro bzw. KTWO_BOX_EXTRUDE – hier nur Box-Förderung)."""
        if dry_run:
            idx, letter, _m = parse_slot(slot)
            return {"status": "dry_run", "slot": letter,
                    "plan": self.describe_plan()}
        return self.do_extrude(slot, odo_polls=odo_polls, **extrude_kw)

    # --- A5 Retrude (Unload, Box-Seite) ------------------------------------
    def do_retrude(self, slot,
                   to_buffer_repeats=RETRUDE_REPEATS_DEFAULT, dry_run=False,
                   after_p0=None):
        """Box-Retrude A5. ``after_p0(i)`` (optional) läuft nach der i-ten
        0x11-p0-Antwort (0-basiert) – Wechsel: Extruder-Rückzug tn_retrude
        (live 3x "extrude = -10.0" je nach p0 1–3, dann p0 4 + p1)."""
        idx, letter, mask = parse_slot(slot)
        if isinstance(to_buffer_repeats, bool) or not isinstance(
                to_buffer_repeats, int) or to_buffer_repeats < 0:
            raise ValueError("to_buffer_repeats muss int >= 0 sein: %r"
                             % (to_buffer_repeats,))
        if dry_run or not self.cfg.get("allow_write"):
            if dry_run:
                return {"status": "dry_run", "slot": letter,
                        "plan": self.describe_plan()}
            self._require_write("Retrude SLOT=%s" % letter)
        self._check_write_block("Retrude SLOT=%s" % letter)
        for fun in (FUNC_SET_BOX_MODE, FUNC_RETRUDE):
            check_not_forbidden(fun)
        self.busy = True
        try:
            self.action.communication_set_box_mode_feed()
            if to_buffer_repeats:
                self.action.communication_get_filament_sensor_state(0x00)
                for i in range(to_buffer_repeats):
                    self.action.communication_retrude(
                        mask, RETRUDE_P_TO_BUFFER)
                    if after_p0 is not None:
                        after_p0(i)
            self.action.communication_retrude(mask, RETRUDE_P_PULL_HUB)
        except CfsStatusError as exc:
            raise CfsStatusError(
                exc.status, "Retrude SLOT=%s: %s" % (letter, exc.name),
                exc.data, exc.fun)
        finally:
            self.busy = False
        if self.loaded_slot in (None, letter):
            self.loaded_slot = None
        return {"status": "ok", "slot": letter}

    def do_break_retrude(self, slot, dry_run=False):
        """Box-Teil von Stock BoxAction.break_box_extruder_retrude
        (box_wrapper Z.4010–4018 @0xd9da8, Aufruf aus cmd_CR_BOX_RETRUDE
        Z.5778–5783 bei LENGTH≠0 – Creality Print 7 „M8200 R E-30“ VOR dem
        Schnitt): Box IDLE (0x04 [00 01]) -> 0x11 [Maske 00] (Feeder +
        Hauptmotor zurück bis Buffer-Sensor, schafft Platz für den Extruder-
        Rückzug) -> Box IDLE. KEIN pull_back_hub (0x11 p1): das Filament
        bleibt im Kopf, Slot bleibt geladen. Den Extruder-Schritt macht die
        Klipper-Hülle (KTWO_BOX_BREAK_RETRUDE)."""
        idx, letter, mask = parse_slot(slot)
        if dry_run or not self.cfg.get("allow_write"):
            if dry_run:
                return {"status": "dry_run", "slot": letter,
                        "plan": "0x04 [00 01] -> 0x11 [%02x 00] -> 0x04 [00 01]"
                        % (mask,)}
            self._require_write("Break-Retrude SLOT=%s" % letter)
        self._check_write_block("Break-Retrude SLOT=%s" % letter)
        for fun in (FUNC_SET_BOX_MODE, FUNC_RETRUDE):
            check_not_forbidden(fun)
        self.busy = True
        try:
            self.action.communication_set_box_mode_feed()
            self.action.communication_retrude(mask, RETRUDE_P_TO_BUFFER)
            self.action.communication_set_box_mode_feed()
        except CfsStatusError as exc:
            raise CfsStatusError(
                exc.status, "Break-Retrude SLOT=%s: %s" % (letter, exc.name),
                exc.data, exc.fun)
        finally:
            self.busy = False
        return {"status": "ok", "slot": letter}

    def do_unload(self, slot, dry_run=False,
                  to_buffer_repeats=RETRUDE_REPEATS_DEFAULT):
        if dry_run:
            idx, letter, _m = parse_slot(slot)
            return {"status": "dry_run", "slot": letter,
                    "plan": self.describe_plan()}
        return self.do_retrude(slot, to_buffer_repeats=to_buffer_repeats,
                               dry_run=False)

    # --- A6 Cut (reine Kopfbewegung, kein Box-Frame) -------------------------
    def do_cut(self, slot=None, restore_accel=None):
        """Schneidfahrt als G-Code-Plan (sendet NICHTS an die Box).

        Beleg: Live-Cut 12.10. 00:24:50–00:24:53 und 16.10. 22:02:35–22:02:39
        – zwischen „[box] cut sensor detected“ und „cut to return OK“ nur
        0x0a-Polls/0x0e, KEIN Box- oder Motor-Frame → Cut = Kopfbewegung
        gegen den Schneidhebel (+ Schaltersensor switch_pin PB9). Positionen
        aus box.cfg/SAVE_CONFIG (s. gcode_cut). Ausführen macht die
        Klipper-Hülle (KTWO_BOX_CUT, nur mit allow_move).
        """
        if slot is not None:
            parse_slot(slot)  # nur Plausibilität (Cut ist slot-unabhängig)
        return {"status": "plan", "gcode": gcode_cut(self.cfg, restore_accel)}

    # --- Wechsel: Wartebedingungen (lesend) ----------------------------------
    def hub_mask(self):
        """Hub-Sensor (0x08 [01] = Sensor B, live auch 0x0a b4) – lesend."""
        return int(self.get_sensor(0x01)) & MASK_ALL

    def wait_box_ready(self, timeout=CHANGE_DEFAULTS["box_wait_timeout"],
                       interval=1.0):
        """Box-Zustand abwarten (Stock BOX_MODE_WAIT): 0x0a pollen, bis
        IDLE oder PRINT. PRELOAD/RELOAD/0x30-Ereignis -> weiter warten
        (Preload live 31–37 s je Slot); ERROR -> CfsStatusError mit
        gespeichertem Fehlercode; Timeout -> BoxChangeError. Lesend."""
        t0 = self.clock()
        last = None
        while True:
            st = self.poll_status()
            last = st
            kind = st.get("kind")
            state = st.get("state")
            if kind in ("state", "state_v113"):
                if state == _BOX.BOX_STATE_ERROR:
                    code = st.get("error_code") or st.get("frame_status") or 0
                    raise CfsStatusError(
                        int(code), "Box im Zustand ERROR (Fehler 0x%02x %s) – "
                        "KTWO_BOX_ERROR_CLEAR" % (int(code), status_name(int(code))),
                        b"", FUNC_GET_BOX_STATE)
                if state in (BOX_STATE_IDLE, _BOX.BOX_STATE_PRINT):
                    return st
            self._cancel_wait("Box-Zustand abwarten (lesend)")
            if self.clock() - t0 > float(timeout):
                raise BoxChangeError(
                    "Box nach %.0f s nicht bereit (Zustand %s)" % (
                        float(timeout), (last or {}).get("state_name", "?")))
            self.pause(float(interval))

    # --- Status-Objekt für Moonraker (kein Bus) ------------------------------
    def get_status(self, eventtime=None):
        """Moonraker-Status (nur Cache, kein Bus – Klipper ruft oft auf).

        Keys: ready, box_addr, state, state_name, hub_mask, temp_c, rh,
        slots ({A..D: {rfid, remain}}), box_fw. Vor dem ersten Poll:
        state unknown.
        """
        _ = eventtime
        base = {"ready": bool(self.is_ready),
                "box_addr": self.cfg.get("box_addr", BOX_ADDR_DEFAULT),
                "box_fw": self.box_fw,
                "loaded_slot": self.loaded_slot,
                "busy": bool(self.busy),
                "print_hold": bool(self.print_hold),
                "state": None, "state_name": "unknown",
                "hub_mask": None, "temp_c": None, "rh": None,
                "slots": {k: dict(v) for k, v in self.slot_cache.items()}}
        poll = self.last_poll or {}
        if poll.get("kind") == "state":
            base.update({"state": poll.get("state"),
                         "state_name": poll.get("state_name", "unknown"),
                         "hub_mask": poll.get("hub_mask"),
                         "temp_c": poll.get("temp_c"),
                         "rh": poll.get("rh")})
        elif poll.get("kind") == "event":
            base.update({"state": None, "state_name": "event",
                         "hub_mask": None,
                         "events": dict(poll.get("events", {}))})
        return base


# =====================================================================
# Klipper-Hülle [k2_box] (ANNAHMEN zur Upstream-API)
# =====================================================================

class PrinterK2Box:
    """Klipper-Objekt [k2_box] – verdrahtet Controller mit Klipper."""

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.konsole = _K2K.Konsole(self.printer, "k2_box", self.gcode)
        self._poll_fails = 0          # 0x0a-Poll: Fehlschläge in Folge
        self._poll_err_code = None    # zuletzt gemeldeter Box-ERROR-Code
        self._readdr_ab = 0.          # Wiederanmeldung frühestens ab (eventtime)
        self._readdr_laeufe = 0       # Wiederanmelde-Durchläufe seit Ausfall
        try:
            self.cfg = parse_klipper_config(config)
        except Exception as exc:
            raise config.error("[k2_box] Config-Fehler: %s" % exc)
        # Material-DB (17.10.2026): Fehler beim Laden = KEIN Config-Fehler,
        # sondern Fallback auf das bisherige Verhalten + Meldung (Connect).
        self.material_db, self.material_db_info = load_material_db(
            self.cfg.get("material_database"))
        if self.material_db_info:
            # Log sofort, Konsole gepuffert bis kurz nach „ready“
            self.konsole.melde(self.material_db_info,
                               _K2K.INFO if self.material_db is not None
                               else _K2K.WARNUNG)
        # Material in der Düse (Stock box_action.remain_material: color/type
        # nach jedem erfolgreichen Spülen, remain_material.save). Persistent
        # über remain_material_file (Stock remain_material_data.json):
        # gelesen EINMAL bei klippy:connect,
        # geschrieben nur nach erfolgreichem FLUSH (bzw. KTWO_BOX_REMAIN)
        # und nur bei Änderung. Leer = aus: nach dem Start unbekannt.
        self.remain_material = {"type": None, "color": None, "tnn": ""}
        self.remain_file = remain_path(self.cfg.get("remain_material_file"))
        self.remain_load = "aus" if not self.remain_file else "ungelesen"
        self.remain_info = ""
        self._remain_saved = None      # remain_key() des Dateiinhalts
        self._remain_loaded = False
        # Slot-Handwerte (KTWO_BOX_SLOT_SET = Stock BOX_MODIFY_TN_DATA,
        # README-k2_box.md §15): TNN -> {"type", "color"}; gilt je Feld vor
        # dem RFID-Etikett, bis ein LESBARES Etikett des Slots gelesen wird.
        # Persistent über slot_material_file (Stock tn_data.json), gelesen
        # EINMAL bei klippy:connect.
        self.slot_manual = {}
        self.slot_file = remain_path(self.cfg.get("slot_material_file"))
        self.slot_load = "aus" if not self.slot_file else "ungelesen"
        self.slot_info = ""
        self._slot_loaded = False
        self.rfid_last = ""
        self._mat_names = {}
        # Slicer-Spülmatrix (flush_mode matrix): Cache (Pfad, Größe, mtime)
        # -> Ergebnis/Fehlertext; Reset bei virtual_sdcard:reset_file.
        self._matrix_cache = None
        # E5/A4 (Live-Abgleich 18.10.2026): Slot
        # des letzten erfolgreichen Spülens IM LAUFENDEN Druck + Druck-
        # Kennung (print_stats.print_start_time). Stock kennt beim
        # Druckstart kein altes Werkzeug (last_tnn None) -> erster Einzug
        # über den Farbweg, erst ab dem ersten Wechsel im Druck die Matrix.
        # Zurückgesetzt bei virtual_sdcard:reset_file (neue Datei, Druck-
        # ende, Abbruch), KTWO_BOX_PRINT_START und KTWO_BOX_CANCEL_RESET.
        self._print_flush_tnn = ""
        self._print_flush_ident = None
        self.flush_last = ""
        self._bus = None  # ANNAHME: geteilte ttyS5-Instanz (s. _ensure_bus)
        self.controller = None
        self.printer.register_event_handler("klippy:connect",
                                            self.handle_connect)
        # Upstream virtual_sdcard sendet das bei jedem Datei-Reset (neue
        # Datei, Druckende, Abbruch) -> Matrix-Cache leeren.
        try:
            self.printer.register_event_handler("virtual_sdcard:reset_file",
                                                self._matrix_reset)
        except Exception:
            pass
        # Adressvergabe erst nach Motor-Init (gleiche Reihenfolge wie Stock).
        try:
            self.printer.register_event_handler("k2_motor:ready",
                                                self.handle_motor_ready)
        except Exception:
            pass
        self.gcode.register_command("KTWO_BOX_STATUS", self.cmd_status,
                                    desc="K2-Box-Status (0x0a-Poll, lesend)")
        self.gcode.register_command("KTWO_BOX_LOAD", self.cmd_load,
                                    desc="K2-Box-Load SLOT=A..D [DRY_RUN=1]")
        self.gcode.register_command("KTWO_BOX_UNLOAD", self.cmd_unload,
                                    desc="K2-Box-Unload SLOT=A..D [DRY_RUN=1]")
        self.gcode.register_command("KTWO_BOX_PRELOAD", self.cmd_preload,
                                    desc="K2-Box-Preload SLOT=A..D OP=0..3 "
                                         "[FORCE=1] (op 2/3 nur Kopf leer)")
        # Filamentwechsel (T0–T15 / M8200-Kette, s. Modul-Docstring)
        self.chain = self._new_chain()
        self.chain_error = ""
        # Schnittschutz (22.10.2026): Grund-Text, solange seit dem letzten
        # Spülen/Druckbeginn kein Schnitt kam ("" = frei). Einzel-RETRUDE/
        # UNLOAD lehnen dann ab (_cut_missing): ohne Schnitt bildet das lange
        # geschmolzene Ende beim Rückzug eine Verdickung -> live Box-Status
        # 0x19 (Pull-back: Motorblock > 10 s). Stock schneidet vor jedem
        # Rückzug. Nur im Speicher: nach einem Neustart unbekannt -> frei
        # (wie vor dem Schutz; ein Merker auf Platte würde nach Eingriffen von
        # Hand – Schnitt/Entladen bei ausgeschaltetem Klipper – falsch sperren).
        self.cut_needed = ""
        # Faden im Kopf abgeschnitten (Live 26.10.2026): nach einem echten
        # Schnitt meldet der Kopfsensor weiter Filament – das Stück über dem
        # Messer hängt aber nicht mehr am Extruder. Erst ein Rückzug, Entladen
        # oder Einzug macht den Kopf wieder eindeutig. RESUME nach einem
        # Abbruch prüft das (cmd_resume_check). Nur im Speicher.
        self.head_cut = False
        # Abbruch mitten im M8200-Block der Datei (Creality Print): Ziel-TNN,
        # bis RESUME die Kette wieder öffnet bzw. M8200 O/Druckende
        self._m8200_offen = ""
        self.pending_resume_z = None
        # Startposition eines im Druck abgebrochenen Wechsels – nur für ein
        # Wiederholungs-T<n> IN DERSELBEN PAUSE (s. cmd_pre_opt).
        self._keep_start_pos = None
        # Position vor einem Einzelschritt im laufenden Druck ohne Kette
        # (z. B. M8200 C ohne P) -> Rückfahrziel bei dessen Abbruch.
        self._step_start = None
        # Extruder-Ziel vor einem solchen Einzelschritt (PAUSE CFS_TEMP, s.
        # _pause_temp); in der Kette übernimmt das chain["orig_target"].
        self._step_target = None
        # Abgebrochener Wechsel im Druck (_fail, Pausenpfad): RESUME prüft
        # dann den Kopfsensor (cmd_resume_check) – leer = gesperrt bzw.
        # Nachholen (resume_redo_change). Dict step/tnn/source oder None.
        # Stock-Gegenstück: BoxSave.recode_err -> save_resume_tnn.
        # Lebt bis zum erfolgreichen RESUME
        # (KTWO_BOX_RESUME_FIX) bzw. bis zu einer NEUEN Pause, die nicht
        # aus _fail stammt (KTWO_BOX_RESUME_FIX CLEAR=1, s. _pause_from_fail).
        self.aborted = None
        self._pause_from_fail = False
        # Auto-Nachladen bei Runout (A7): Laufzeit-Schalter (Start = Config
        # runout_refill, Stock BOX_ENABLE_AUTO_REFILL), Gleich-Material-Liste
        # (Stock BOX_UPDATE_SAME_MATERIAL_LIST, hier manuell), letzte Meldung.
        self.auto_refill = bool(self.cfg.get("runout_refill"))
        self.same_material = []
        self.runout_last = ""
        # Restmaterial (Stock-Flag
        # is_use_ending_material): dict slot/tnn/why oder None. Gesetzt aus
        # dem 0x0a-Poll (Status 0x50 = Einlauf/Hub des Druck-Slots frei) nur
        # im Druck/in der Pause (_note_ending), gelöscht nach dem Ausspülen,
        # nach einem erfolgreichen Einzug, im Runout-Nachladen oder per
        # KTWO_BOX_ENDING CLEAR=1. Nur im Speicher (Stock ebenso).
        self.ending_material = None
        self.ending_last = ""
        # Kein Ausspülen in PRE_OPT (Runout-Nachladen, Nachholen im RESUME):
        # Grund als Text, "" = erlaubt
        self._ending_off = ""
        self._ending_probe = False
        # KTWO_BOX_END_PRINT läuft: Fehler -> Meldung statt PAUSE (s. _fail)
        self._end_print = False
        # Schritt, dessen Fehler chain_error gesetzt hat (C3: Entladen beim
        # Abbruch nur nach "sicheren" Schritten, s. _cancel_unload_block)
        self._chain_error_step = ""
        # C2 (18.10.2026): Druckabbruch angefordert (Webhook pause_resume/
        # cancel, Zeitpunkt) – gesetzt nur, solange der eingehüllte Webhook
        # auf das G-Code-Mutex wartet bzw. CANCEL_PRINT läuft (finally ->
        # None). cancel_hook = Ergebnis der Einhüllung (Status/Meldung).
        self._cancel_req = None
        self.cancel_hook = "nicht eingehüllt (noch kein klippy:connect)"
        # Externe Spule (19.10.2026, README-k2_box.md §18): Filamentquelle
        # cfs|extern (vom User gesetzt, KTWO_BOX_SOURCE) + zuletzt geladenes
        # externes Material (Stock-Display merkt es ebenso). Persistent über
        # filament_source_file, gelesen EINMAL bei klippy:connect; fehlt/
        # kaputt -> CFS. Bis dahin CFS.
        self.source = SOURCE_CFS
        self.extern_material = None
        self.source_file = remain_path(self.cfg.get("filament_source_file"))
        self.source_load = "aus" if not self.source_file else "ungelesen"
        self.source_info = ""
        self._source_loaded = False
        # T<n> bei Quelle EXTERN: (Druck-Kennung, Werkzeug) des ersten T<n>
        # im laufenden Druck (KTWO_BOX_EXTERN_TOOL); zurückgesetzt wie
        # _print_flush_tnn (reset_file, PRINT_START, CANCEL_RESET) und beim
        # Umschalten der Quelle.
        self._extern_tool = None
        self.extern_last = ""
        self.extern_rfid_last = ""
        for name, func, desc in (
                ("KTWO_BOX_PRE_OPT", self.cmd_pre_opt,
                 "Wechsel beginnen (= M8200 P) [TNN=T1A] [Z_HOP=mm]"),
                ("KTWO_BOX_CUT", self.cmd_cut,
                 "Filament schneiden (= M8200 C), Kopfbewegung"),
                ("KTWO_BOX_GO_EXTRUDE_POS", self.cmd_go_extrude_pos,
                 "Kopf in den Abfallschacht (Stock BOX_GO_TO_EXTRUDE_POS)"),
                ("KTWO_BOX_SAFE_POS", self.cmd_safe_pos,
                 "Abfallschacht verlassen (Stock BOX_MOVE_TO_SAFE_POS)"),
                ("KTWO_BOX_RETRUDE", self.cmd_retrude,
                 "Filament in die Box zurück (= M8200 R) [SLOT=] [LENGTH=]"),
                ("KTWO_BOX_BREAK_RETRUDE", self.cmd_break_retrude,
                 "Extruder-Rückzug ohne Box-Rückzug (= M8200 R E<x>, Stock "
                 "break_box_extruder_retrude) LENGTH=<mm, Vorzeichen wie E>"),
                ("KTWO_BOX_EXTRUDE", self.cmd_extrude,
                 "Filament einziehen (= M8200 L I..) TNN=T1A|SLOT=A"),
                ("KTWO_BOX_FLUSH", self.cmd_flush,
                 "Spülen + Abstreifen (= M8200 F) [TNN=] [TOOL=n] [LENGTH=] "
                 "[VELOCITY=] [TEMP=]"),
                ("KTWO_BOX_WIPE", self.cmd_wipe,
                 "Düse am Schacht abstreifen (Stock-T0-Beispiel 10.1-10.3)"),
                ("KTWO_BOX_WASTE", self.cmd_waste,
                 "Abfallschacht-Prüfung (= M8200 W, hier nur Hinweis)"),
                ("KTWO_BOX_END_OPT", self.cmd_end_opt,
                 "Wechsel abschließen (= M8200 O) [RETURN=0|1]"),
                ("KTWO_BOX_WAIT", self.cmd_wait,
                 "Box-Zustand abwarten (IDLE/PRINT) [TIMEOUT=s]"),
                ("KTWO_BOX_ERROR_CLEAR", self.cmd_error_clear,
                 "Wechsel-Fehler quittieren (+ Box 0x04 IDLE mit allow_write)"),
                ("KTWO_BOX_RESUME_FIX", self.cmd_resume_fix,
                 "Nach RESUME: Z auf Höhe vor dem abgebrochenen Wechsel "
                 "[CLEAR=1] (CLEAR aus PAUSE: Z steckt im Rückfahrziel)"),
                ("KTWO_BOX_RESUME_CHECK", self.cmd_resume_check,
                 "RESUME-Prüfung nach abgebrochenem Wechsel: Kopf leer -> "
                 "gesperrt bzw. nachholen (resume_redo_change) [MOVE=0|1] "
                 "[TEMP=] (aus RESUME)"),
                ("KTWO_BOX_RUNOUT", self.cmd_runout,
                 "Runout: aus der CFS nachladen (= Stock BOX_CHECK_MATERIAL_"
                 "REFILL) [SLOT=A..D] [TEMP=] [DRY_RUN=1]"),
                ("KTWO_BOX_AUTO_REFILL", self.cmd_auto_refill,
                 "Auto-Nachladen an/aus (= Stock BOX_ENABLE_AUTO_REFILL) "
                 "[ENABLE=0|1]"),
                ("KTWO_BOX_REMAIN", self.cmd_remain,
                 "Material in der Düse anzeigen/korrigieren (Nicht-Stock) "
                 "[COLOR=0RRGGBB] [TYPE=<id>] [TNN=T1A] [CLEAR=1]"),
                ("KTWO_BOX_RFID", self.cmd_rfid,
                 "RFID + Restmenge neu lesen (= Stock BOX_INFO_REFRESH, nur "
                 "lesend, ohne Preload) [SLOT=T1A..T1D|A..D|ALL] [FORCE=1]"),
                ("KTWO_BOX_SLOT_SET", self.cmd_slot_set,
                 "Slot-Farbe/-Material von Hand (= Stock BOX_MODIFY_TN_DATA) "
                 "SLOT=T1A [COLOR=RRGGBB] [TYPE=<id>|MATERIAL=<Name>] "
                 "[CLEAR=1]"),
                ("KTWO_BOX_SAME_MATERIAL", self.cmd_same_material,
                 "Gleich-Material-Gruppen (= Stock BOX_UPDATE_SAME_MATERIAL_"
                 "LIST) [LIST=T1A,T1C/T1B,T1D] [CLEAR=1]"),
                ("KTWO_BOX_ENDING", self.cmd_ending,
                 "Restmaterial-Fall anzeigen/setzen/löschen bzw. Rest jetzt "
                 "ausspülen (PURGE=1 = Stock BOX_EXTRUSION_ALL_MATERIALS) "
                 "[SET=1 SLOT=A] [CLEAR=1] [PURGE=1]"),
                ("KTWO_BOX_END_PRINT", self.cmd_end_print,
                 "Druckende (= Stock BOX_END): Restmaterial ausspülen, mit "
                 "end_print_unload schneiden + in die Box zurück [UNLOAD=0|1]"),
                ("KTWO_BOX_PRINT_START", self.cmd_print_start,
                 "Box-Druckzustand an (= Stock BOX_START_PRINT): 0x0d [0f 00] "
                 "+ 0x0f [01] (aus START_PRINT)"),
                ("KTWO_BOX_PRINT_END", self.cmd_print_end,
                 "Box-Druckzustand aus (= Stock BOX_END_PRINT): 0x0d [0f 01] "
                 "+ 0x0f [00] (aus END_PRINT)"),
                ("KTWO_BOX_CANCEL_RESET", self.cmd_cancel_reset,
                 "Wechsel-Fehlerzustand nach Druckende zurücksetzen (aus "
                 "CANCEL_PRINT nach CANCEL_PRINT_BASE; = Stock BOX_END_PRINT "
                 "clear_resume_tnn) [START=1] – nie in einer Pause"),
                # Externe Spule (19.10.2026, README-k2_box.md §18)
                ("KTWO_BOX_SOURCE", self.cmd_source,
                 "Filamentquelle anzeigen/setzen [SOURCE=EXTERN|CFS] – bei "
                 "EXTERN sind alle CFS-Aktionen gesperrt"),
                ("KTWO_BOX_EXTERN_MATERIAL", self.cmd_extern_material,
                 "Externes Material anzeigen/setzen [MATERIAL=<Name|Typ|ID>] "
                 "[TYPE=<id>] [COLOR=RRGGBB] [TEMP=°C|0] [CLEAR=1]"),
                ("KTWO_BOX_EXTERN_TOOL", self.cmd_extern_tool,
                 "T<n> bei Quelle EXTERN (aus T<n>/M8200): erstes Werkzeug = "
                 "kein Wechsel, anderes im Druck -> PAUSE  T=<0..15>"),
                ("KTWO_BOX_EXTERN_BEGIN", self.cmd_extern_begin,
                 "Externe Spule laden/entladen beginnen (LOAD_FILAMENT/"
                 "UNLOAD_FILAMENT) OP=LADEN|ENTLADEN [MATERIAL=] [TYPE=] "
                 "[COLOR=] [TEMP=]"),
                ("KTWO_BOX_EXTERN_FLUSH", self.cmd_extern_flush,
                 "Externe Spule: Spülen wie CFS-Wechsel (Stock "
                 "FILAMENT_RACK_FLUSH, nur in LOAD_FILAMENT)"),
                ("KTWO_BOX_EXTERN_HEAT", self.cmd_extern_heat,
                 "Externe Spule: auf Materialtemperatur heizen (Stock "
                 "FILAMENT_RACK_SET_TEMP, nur in UNLOAD_FILAMENT)"),
                ("KTWO_BOX_EXTERN_RETRACT", self.cmd_extern_retract,
                 "Externe Spule: Extruder E-10 F360 nach dem Schnitt (Stock "
                 "QUIT_MATERIAL_RETRUDE_MATERIAL)"),
                ("KTWO_BOX_EXTERN_END", self.cmd_extern_end,
                 "Externe Spule: Vorgang abschließen (Abkühlen wie Stock "
                 "SET_COOL_TEMP, Zustand zurück)"),
                ("KTWO_EXTERN_RFID", self.cmd_extern_rfid,
                 "RFID-Etikett der externen Spule lesen (0x11 [02], nur mit "
                 "[k2_box] extern_rfid: True; Auslöser !PA5)")):
            self.gcode.register_command(name, func, desc=desc)

    def _ensure_bus(self):
        # ANNAHME: EIN geteilter RS485-Bus (dieselbe Instanz wie [k2_motor],
        # Halbduplex-Lock über die ganze Transaktion). Betreiber stellt ihn
        # als printer-Objekt 'k2_485' bereit (Upstream hat keinen zentralen
        # 485-Manager); ohne Bus bleibt das Modul im Diagnose-Modus.
        if self._bus is not None:
            return self._bus
        for name in ("k2_485", "k2_rs485", "serial485"):
            try:
                self._bus = self.printer.lookup_object(name)
                return self._bus
            except Exception:
                continue
        raise RuntimeError("RS485-Bus nicht verdrahtet: Abschnitt [k2_485] fehlt "
                           "in printer.cfg (printer-Objekt 'k2_485', dieselbe "
                           "Instanz wie [k2_motor]/[k2_belt])")

    def _ensure_controller(self):
        if self.controller is not None:
            return self.controller
        bus = self._ensure_bus()
        if hasattr(self.reactor, "pause") and hasattr(self.reactor, "monotonic"):
            # Lange Box-Antworten nicht im Reactor blockieren (s. Proxy).
            bus = ReactorBusProxy(bus, self.reactor)
        self.controller = K2BoxController(
            self.cfg, bus, pause=reactor_sleep(self.reactor),
            logger=_K2K.LogAdapter("k2_box"),
            send_event=getattr(self.printer, "send_event", None))
        self.controller.on_poll = self._note_ending
        self.controller.on_rfid_tag = self._rfid_seen
        self.controller.cancel_check = self._cancel_requested
        self.controller.write_block = self._extern_write_block
        if hasattr(self.reactor, "monotonic"):
            self.controller.clock = self.reactor.monotonic
        return self.controller

    def _load_remain(self):
        """remain_material_file einmal lesen (klippy:connect) – nie Fehler."""
        if self._remain_loaded:
            return
        self._remain_loaded = True
        try:
            rm, st, info = load_remain_material(self.remain_file)
        except Exception as exc:  # pragma: no cover – Sicherheitsnetz
            rm, st, info = None, "fehler", "Düsen-Material-Datei: %s" % exc
        self.remain_load, self.remain_info = st, info
        if rm is not None:
            self.remain_material = dict(rm)
            self._remain_saved = remain_key(rm)
        if info:
            # Connect-Zeit: Konsole puffert bis kurz nach „ready“
            self.konsole.melde("%s%s" % ("WARNUNG " if st == "fehler" else "",
                                         info),
                               _K2K.WARNUNG if st == "fehler" else _K2K.INFO)

    def _persist_remain(self, gcmd=None):
        """Nach erfolgreichem FLUSH (bzw. KTWO_BOX_REMAIN): Datei atomar
        schreiben – nur wenn an und geändert. Fehler = nur Meldung."""
        if not self.remain_file:
            return False
        key = remain_key(self.remain_material)
        if key == self._remain_saved:
            return False
        try:
            save_remain_material(self.remain_file, self.remain_material)
        except Exception as exc:
            msg = ("[k2_box] WARNUNG Düsen-Material nicht gespeichert (%s: %s) "
                   "– Wert gilt bis zum Neustart" % (
                       self.remain_file, self._exc_text(exc)))
            try:
                logging.warning(msg)
            except Exception:
                pass
            try:
                (gcmd or self.gcode).respond_info(msg)
            except Exception:
                pass
            return False
        self._remain_saved = key
        try:
            logging.info("[k2_box] Düsen-Material gespeichert: color %s, "
                         "type %s, tnn %s" % key)
        except Exception:
            pass
        return True

    def _matrix_reset(self, *args):
        self._matrix_cache = None
        # Externe Spule: erstes Werkzeug gilt nur je Druck
        self._extern_tool = None
        # neuer Druck / Druckende / Abbruch -> altes Werkzeug im Druck
        # unbekannt (E5/A4)
        self._print_flush_reset()

    def _print_flush_reset(self):
        self._print_flush_tnn = ""
        self._print_flush_ident = None

    def _print_ident(self):
        """Kennung des laufenden Drucks: Upstream print_stats.print_start_
        time (bleibt über Pausen gleich, neu bei jedem Druck) – None, wenn
        nicht verfügbar (dann zählt nur das Zurücksetzen per Ereignis)."""
        ps = self._lookup("print_stats")
        val = getattr(ps, "print_start_time", None) if ps is not None \
            else None
        return val if isinstance(val, (int, float)) \
            and not isinstance(val, bool) else None

    def _print_old_tnn(self):
        """E5/A4: alter Slot für die Matrix -> (tnn, Grund). tnn nur, wenn
        das Düsen-Material in DIESEM Druck gespült wurde (Slot gleich und
        Druck-Kennung unverändert), sonst '' + Grund."""
        cur = self.remain_material.get("tnn") or ""
        if not self._print_flush_tnn:
            return "", "erster Einzug im Druck"
        ident = self._print_ident()
        if self._print_flush_ident is not None and ident is not None \
                and ident != self._print_flush_ident:
            self._print_flush_reset()
            return "", "erster Einzug im Druck (neuer Druck laut print_stats)"
        if cur != self._print_flush_tnn:
            return "", ("Düsen-Material %s ≠ im Druck gespült %s "
                        "(KTWO_BOX_REMAIN?)" % (cur or "ohne Slot",
                                                self._print_flush_tnn))
        return cur, ""

    # --- Slot-Handwerte (KTWO_BOX_SLOT_SET), 18.10.2026 -----------------
    def _load_slots(self):
        """slot_material_file einmal lesen (klippy:connect) – nie Fehler."""
        if self._slot_loaded:
            return
        self._slot_loaded = True
        try:
            sl, st, info = load_slot_material(self.slot_file)
        except Exception as exc:  # pragma: no cover – Sicherheitsnetz
            sl, st, info = None, "fehler", "Slot-Material-Datei: %s" % exc
        self.slot_load, self.slot_info = st, info
        if sl is not None:
            self.slot_manual = dict(sl)
        if info:
            if st == "fehler":
                self.konsole.warnung("WARNUNG %s" % info)
            else:
                try:
                    logging.info("[k2_box] %s" % info)
                except Exception:
                    pass

    def _persist_slots(self, gcmd=None):
        """Handwerte atomar schreiben (wenn an). Fehler = nur Meldung."""
        if not self.slot_file:
            return False
        try:
            save_slot_material(self.slot_file, self.slot_manual)
        except Exception as exc:
            msg = ("[k2_box] WARNUNG Slot-Handwerte nicht gespeichert (%s: %s) "
                   "– gelten bis zum Neustart" % (self.slot_file,
                                                   self._exc_text(exc)))
            try:
                logging.warning(msg)
            except Exception:
                pass
            try:
                (gcmd or self.gcode).respond_info(msg)
            except Exception:
                pass
            return False
        return True

    # --- Externe Spule: Quelle + Material persistent (19.10.2026) -------
    def _load_source(self):
        """filament_source_file einmal lesen (klippy:connect) – nie Fehler;
        fehlt/kaputt -> Quelle CFS (+ Warnung bei kaputt)."""
        if self._source_loaded:
            return
        self._source_loaded = True
        try:
            st, status, info = load_filament_source(self.source_file)
        except Exception as exc:  # pragma: no cover – Sicherheitsnetz
            st, status, info = None, "fehler", "Quellen-Datei: %s" % exc
        self.source_load, self.source_info = status, info
        if st is not None:
            self.source = st["source"]
            self.extern_material = st["material"]
        if info:
            if status == "fehler" or self.source == SOURCE_EXTERN:
                self.konsole.melde(
                    "%s%s" % ("WARNUNG " if status == "fehler" else "", info),
                    _K2K.WARNUNG if status == "fehler" else _K2K.INFO)
            else:
                try:
                    logging.info("[k2_box] %s" % info)
                except Exception:
                    pass

    def _persist_source(self, gcmd=None):
        """Quelle + externes Material atomar schreiben (wenn an). Fehler =
        nur Meldung (Wert gilt bis zum Neustart)."""
        if not self.source_file:
            return False
        try:
            save_filament_source(self.source_file, self.source,
                                 self.extern_material)
        except Exception as exc:
            msg = ("[k2_box] WARNUNG Filamentquelle nicht gespeichert (%s: %s) "
                   "– gilt bis zum Neustart (danach Quelle CFS)" % (
                       self.source_file, self._exc_text(exc)))
            try:
                logging.warning(msg)
            except Exception:
                pass
            try:
                (gcmd or self.gcode).respond_info(msg)
            except Exception:
                pass
            return False
        self.source_load = "geladen"
        return True

    def _box_tnn(self, letter):
        return "T%d%s" % (int(self.cfg.get("box_addr", BOX_ADDR_DEFAULT)),
                          letter)

    def _manual(self, letter):
        return self.slot_manual.get(self._box_tnn(letter)) if letter else None

    def _manual_letters(self):
        """{Buchstabe: Handwert} der konfigurierten Box (Runout-Auswahl)."""
        out = {}
        for letter in SLOT_LETTERS:
            m = self._manual(letter)
            if m:
                out[letter] = dict(m)
        return out

    def _rfid_seen(self, letter):
        """Lesbares Etikett gelesen -> Handwert des Slots verwerfen (Stock:
        GET_RFID schreibt dieselben Tn_data-Felder, s. merge_tag-Block)."""
        tnn = self._box_tnn(letter)
        if tnn not in self.slot_manual:
            return
        old = self.slot_manual.pop(tnn)
        msg = ("[k2_box] %s: RFID-Etikett gelesen – Handwert %s/%s verworfen "
               "(RFID hat Vorrang)" % (tnn, old.get("type") or "-",
                                       old.get("color") or "-"))
        try:
            self.gcode.respond_info(msg)
        except Exception:
            pass
        self._persist_slots()

    def _mat_label(self, key):
        """'Name, Typ, Temp °C' aus der Material-DB (memo) oder ''."""
        if not key or self.material_db is None:
            return ""
        mk = (id(self.material_db), key)
        if mk not in self._mat_names:
            mp = self._mat(key)
            if not mp.get("known"):
                txt = "nicht in der Material-DB"
            else:
                txt = ", ".join(x for x in (
                    mp.get("name") or "", mp.get("mtype") or "",
                    ("%s °C" % _n(mp["temp"])) if mp.get("temp_known")
                    else "") if x)
            self._mat_names[mk] = txt
        return self._mat_names[mk]

    def _slot_material_status(self):
        """Je Slot der Box: wirksames Material/Farbe + Quelle (ohne Bus)."""
        ctl = self.controller
        out = {}
        for letter in SLOT_LETTERS:
            tag = None
            remain = None
            if ctl is not None:
                tag = rfid_material(ctl.tag_cache.get(letter)) or \
                    rfid_material((ctl.slot_cache.get(letter) or {})
                                  .get("rfid"))
                remain = (ctl.slot_cache.get(letter) or {}).get("remain")
            man = self._manual(letter) or {}
            eff = merge_tag(tag, man)
            src = []
            if tag and (not man.get("type") or not man.get("color")):
                src.append("rfid")
            if man:
                src.append("manuell")
            out[letter] = {
                "tnn": self._box_tnn(letter),
                "type": eff[0] if eff else None,
                "color": eff[1] if eff else None,
                "source": "+".join(src) if eff else "",
                "manual": dict(man),
                "material": self._mat_label(eff[0]) if eff and eff[0] else "",
                "remain": remain}
        return out

    def handle_connect(self):
        self._load_remain()
        self._load_slots()
        self._load_source()
        self._hook_cancel()
        # Meldungen hier (Connect) puffert self.konsole bis kurz nach „ready“
        try:
            self._ensure_controller()
        except Exception as exc:
            self.konsole.fehler("Connect ohne Bus: %s – CFS-Box nicht nutzbar"
                                % exc)
            return
        # Poll-Timer erst nach k2_motor:ready starten (Adresse!). ANNAHME
        # Upstream-API: reactor.register_timer(cb, reactor.NOW).
        if self.cfg.get("auto_init"):
            try:
                logging.info("[k2_box] bereit (warte auf k2_motor:ready für "
                             "Auto-Addr)")
            except Exception:
                pass

    # --- C2: Druckabbruch während langer Warteschleifen (18.10.2026) -----
    # Stock (Creality-klippy gcode.py:431-442/516):
    # invoke_action setzt cancel_pending -> laufende Befehle laufen leer,
    # dann fast_stop_complete -> SAVE_ENDPRINT -> CANCEL_PRINT. Upstream
    # (pause_resume.py _handle_cancel_request) ruft nur
    # gcode.run_script("CANCEL_PRINT") und wartet auf das G-Code-Mutex: ein
    # T<n> (m5-Poll bis 90 s je Förderphase, Box-Zustand bis 380 s) oder
    # ein Ausspülen (bis 3600 s) hält den Abbruch so lange auf. Ein
    # cancel_pending gibt es in Upstream nicht (Mutex/Befehlsschleife
    # unverändert lassen) -> nur ein Flag, das die k2_box-eigenen
    # Wartepunkte abfragen. Einhüllung über webhooks._endpoints (privat,
    # Upstream 9cb9975: dict path -> callback; register_endpoint lehnt einen
    # zweiten Eintrag ab). Fehlt das -> nur Meldung, Upstream-Verhalten.
    _CANCEL_PATH = "pause_resume/cancel"

    def _hook_cancel(self):
        if not self.cfg.get("cancel_interrupt"):
            self.cancel_hook = "aus ([k2_box] cancel_interrupt: False)"
            return
        wh = self._lookup("webhooks")
        eps = getattr(wh, "_endpoints", None) if wh is not None else None
        if not isinstance(eps, dict):
            self.cancel_hook = ("nicht möglich (webhooks._endpoints fehlt) – "
                                "Upstream-Verhalten")
            return
        orig = eps.get(self._CANCEL_PATH)
        if orig is None or not callable(orig):
            self.cancel_hook = ("nicht möglich (%s nicht registriert) – "
                                "Upstream-Verhalten" % (self._CANCEL_PATH,))
            return
        if getattr(orig, "_k2_box_cancel", False):
            self.cancel_hook = "eingehüllt"
            return

        def _cancel_wrapper(web_request, _orig=orig):
            # Flag VOR dem (blockierenden) Original setzen; das Original
            # wartet auf das Mutex und führt CANCEL_PRINT aus. finally: Flag
            # immer weg, auch wenn CANCEL_PRINT scheitert.
            self._cancel_req = self._now()
            try:
                return _orig(web_request)
            finally:
                self._cancel_req = None
        _cancel_wrapper._k2_box_cancel = True
        eps[self._CANCEL_PATH] = _cancel_wrapper
        self.cancel_hook = "eingehüllt"

    def _cancel_requested(self):
        """C2: True = Druckabbruch wartet (Webhook), Druck läuft/pausiert und
        es läuft NICHT gerade KTWO_BOX_END_PRINT (das Entladen am Druckende
        gehört zum Abbruch und wird nie abgebrochen)."""
        return (self._cancel_req is not None and not self._end_print
                and self._print_active())

    def _check_cancel(self, what):
        if self._cancel_requested():
            raise BoxCancelled("%s: Druckabbruch (CANCEL) – Schritt beendet"
                               % (what,))

    def _cancel_skip(self, gcmd, name, text=""):
        """C2: Schritt wegen Druckabbruch übersprungen/beendet – KEINE Pause,
        kein Wechsel-Fehler, Folgeschritte der Kette überspringen."""
        ch = self.chain
        if ch.get("active") and not ch.get("skip"):
            ch["skip"] = True
            ch["skip_reason"] = "Druckabbruch (CANCEL)"
        gcmd.respond_info(
            "[k2_box] %s %s – Druckabbruch (CANCEL) wartet: keine Pause, "
            "kein Wechsel-Fehler; CANCEL_PRINT folgt (END_PRINT entlädt, "
            "wenn der Kopf Filament meldet)%s" % (
                name, "beendet" if text else "übersprungen",
                (" (%s)" % text) if text else ""))
        return None

    def handle_motor_ready(self, *args):
        # k2_motor:ready kommt meist noch im Connect (Auto-Init) -> Konsole
        # puffert bis kurz nach „ready“.
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            self.konsole.fehler("kein Bus für Auto-Addr: %s" % exc)
            return
        res = ctl.on_motor_ready()
        status = res.get("status") if isinstance(res, dict) else None
        if status == "addr_error":
            self.konsole.fehler("Auto-Addr fehlgeschlagen: %s – CFS-Box nicht "
                                "bereit (Kabel/Strom der Box prüfen, dann "
                                "FIRMWARE_RESTART)" % (res.get("log"),))
        else:
            self.konsole.info("Auto-Addr: %s" % (res,))
        # Zyklischer 0x0a-Poll (ANNAHME Reactor-Timer, Intervall poll_interval).
        try:
            if hasattr(self.reactor, "register_timer"):
                interval = float(self.cfg.get("poll_interval",
                                              POLL_INTERVAL_DEFAULT))

                def _poll(eventtime):
                    if not ctl.busy:  # kein Poll während langer Box-Vorgänge
                        try:
                            parsed = ctl.poll_status()
                        except Exception as exc:
                            self._poll_failed(exc)
                            self._readdr_versuch(ctl, eventtime)
                        else:
                            self._poll_ok(parsed)
                    return eventtime + interval

                self.reactor.register_timer(
                    _poll, getattr(self.reactor, "NOW", 0))
        except Exception as exc:
            self.konsole.fehler("Box-Poll-Timer nicht startbar (%s) – Box-"
                                "Zustand/Klima werden nicht aktualisiert"
                                % (exc,))

    def _poll_failed(self, exc):
        """0x0a-Poll im Timer gescheitert – nie werfen; Konsole erst nach
        POLL_WARN_FAILS in Folge (einzelne Aussetzer sind normal)."""
        try:
            self._poll_fails += 1
            if self._poll_fails >= POLL_WARN_FAILS:
                self.konsole.warnung(
                    "CFS-Box antwortet nicht (%d Abfragen in Folge, zuletzt "
                    "%s) – Kabel/Strom der Box prüfen; Wechsel/Spülen gehen "
                    "so nicht" % (self._poll_fails, self._exc_text(exc)),
                    schluessel="poll", pause=POLL_WARN_PAUSE)
        except Exception:
            pass

    def _readdr_versuch(self, ctl, eventtime):
        """Box nach Stromverlust/Wiedereinstecken neu anmelden (Live
        26.10.2026: Box im Wechsel abgezogen und wieder eingesteckt – sie
        blieb bis FIRMWARE_RESTART stumm, weil der Pi Adressen nur beim Start
        vergab). Stock (auto_addr_wrapper process_all) läuft dauernd; der Pi
        startet denselben Durchlauf erst, wenn der 0x0a-Poll POLL_WARN_FAILS-
        mal in Folge scheitert, und wiederholt ihn im Stock-Takt. Wirft nie."""
        try:
            if self._poll_fails < POLL_WARN_FAILS \
                    or eventtime < self._readdr_ab:
                return
            txt = ctl.readdr(self._print_active())
            if txt is None:
                return
            nxt = float(getattr(ctl.auto_addr, "next_interval", 10.) or 10.)
            self._readdr_ab = eventtime + nxt
            self._readdr_laeufe += 1
            logging.info("[k2_box] Wiederanmeldung %d: %s",
                         self._readdr_laeufe, txt.replace("\n", " | "))
            if self._readdr_laeufe == 1:
                self.konsole.info(
                    "CFS-Box: Wiederanmeldung läuft (Auto-Addr wie Stock: "
                    "0xA1-Broadcast, 0xA0; alle %s s, bis die Box antwortet)"
                    % (_n(max(nxt, float(self.cfg.get(
                        "poll_interval", POLL_INTERVAL_DEFAULT)))),))
        except Exception:
            self._readdr_ab = eventtime + 10.
            try:
                logging.exception("[k2_box] Wiederanmeldung")
            except Exception:
                pass

    def _poll_ok(self, parsed):
        """0x0a-Poll erfolgreich: Entwarnung + Box-ERROR-Wechsel melden."""
        try:
            if self._poll_fails >= POLL_WARN_FAILS:
                self.konsole.info("CFS-Box antwortet wieder (nach %d "
                                  "Fehlschlägen%s)" % (
                                      self._poll_fails,
                                      "; neu angemeldet" if self._readdr_laeufe
                                      else ""))
                self.konsole.zuruecksetzen("poll")
            self._poll_fails = 0
            self._readdr_laeufe = 0
            self._readdr_ab = 0.
            st = parsed if isinstance(parsed, dict) else {}
            if st.get("kind") not in ("state", "state_v113"):
                return              # Ereignis (0x30) – Zustand unbekannt
            if st.get("state") != _BOX.BOX_STATE_ERROR:
                self._poll_err_code = None
                return
            code = int(st.get("error_code") or st.get("frame_status") or 0)
            if code == self._poll_err_code:
                return
            self._poll_err_code = code
            if self.chain_error or self.aborted:
                return              # Wechsel-Fehler schon rot gemeldet
            self.konsole.fehler(
                "CFS meldet Fehler 0x%02x %s (Box leuchtet rot) – Ursache "
                "beheben, dann KTWO_BOX_ERROR_CLEAR"
                % (code, status_name(code)))
        except Exception:
            try:
                logging.exception("[k2_box] Poll-Auswertung")
            except Exception:
                pass

    def get_status(self, eventtime=None):
        if self.controller is None:
            st = {"ready": False, "state_name": "unknown",
                  "loaded_slot": None, "busy": False}
        else:
            st = self.controller.get_status(eventtime)
        loaded = st.get("loaded_slot")
        st.update({
            "allow_write": bool(self.cfg.get("allow_write")),
            "allow_move": bool(self.cfg.get("allow_move")),
            # Wechsel vollständig freigegeben (macros.cfg T0: sonst Einfarb)
            "change_enabled": bool(self.cfg.get("allow_write")
                                   and self.cfg.get("allow_move")),
            "loaded_tnn": ("T%d%s" % (self.cfg.get("box_addr", 1), loaded)
                           if loaded else ""),
            "chain_active": bool(self.chain.get("active")),
            "chain_tnn": self.chain.get("tnn") or "",
            "chain_skip": bool(self.chain.get("skip")),
            "chain_error": self.chain_error or "",
            "chain_error_step": self._chain_error_step or "",
            "cut_needed": self.cut_needed or "",
            "head_cut": bool(self.head_cut),
            "pending_resume_z": self.pending_resume_z,
            # C2: Webhook-Einhüllung + wartender Druckabbruch
            "cancel_hook": self.cancel_hook or "",
            "cancel_pending": self._cancel_req is not None,
            # RESUME nach abgebrochenem Wechsel (cmd_resume_check):
            # change_aborted -> RESUME-Makro ruft KTWO_BOX_RESUME_CHECK;
            # resume_tnn = Ziel des abgebrochenen Wechsels (Stock-Name);
            # head_filament = Kopfsensor True/False/None (None = unbekannt)
            "change_aborted": bool(self.aborted),
            "resume_tnn": (self.aborted or {}).get("tnn") or "",
            "head_filament": self._head_filament(),
            "resume_redo_change": bool(self.cfg.get("resume_redo_change")),
            # Auto-Nachladen (Stock-Statusnamen 'auto_refill'/'same_material')
            "auto_refill": 1 if self.auto_refill else 0,
            "same_material": [["T%d%s" % (self.cfg.get("box_addr", 1), x)
                               for x in g] for g in self.same_material],
            "runout_last": self.runout_last or "",
            # Material/Spülen (17.10.2026): prime_speed > 0 = RESUME-
            # Vorextrusion aus der Material-DB (macros.cfg _PI_RESUME_PRIME),
            # 0 = Makro-Wert resume_prime_speed (DB aus)
            "material_db": self.material_db is not None,
            "material_db_info": self.material_db_info or "",
            "remain_material": dict(self.remain_material),
            # Persistenz (remain_material_file): Pfad ('' = aus), Ladeergebnis
            # aus|ungelesen|fehlt|geladen|fehler, Meldung
            "remain_material_file": self.remain_file or "",
            "remain_material_load": self.remain_load,
            "remain_material_info": self.remain_info or "",
            # Slot-Material (18.10.2026, README §15): wirksam = Handwert vor RFID
            # je Feld; source rfid|manuell|rfid+manuell|''
            "slot_material": self._slot_material_status(),
            "slot_material_file": self.slot_file or "",
            "slot_material_load": self.slot_load,
            "slot_material_info": self.slot_info or "",
            "rfid_last": self.rfid_last or "",
            "prime_speed": self._prime_speed(),
            "flush_mode": self.cfg.get("flush_mode", "matrix"),
            "flush_last": self.flush_last or "",
            # E5/A4: Slot des letzten Spülens IM LAUFENDEN Druck ('' = im
            # Druck noch nicht gespült -> nächstes Spülen über Farbweg)
            "flush_print_tnn": self._print_flush_tnn or "",
            # Restmaterial / Druckende (README §12.5): ending_material = Fall
            # offen (macros.cfg END_PRINT ruft dann KTWO_BOX_END_PRINT)
            "ending_material": bool(self.ending_material),
            "ending_material_tnn": (self.ending_material or {}).get("tnn")
            or "",
            "ending_material_purge": bool(self.cfg.get(
                "ending_material_purge")),
            "ending_last": self.ending_last or "",
            "end_print_unload": bool(self.cfg.get("end_print_unload")),
            # Druckweiter Box-Zustand (A2): KTWO_BOX_PRINT_START gesendet,
            # Einzug schaltet 0x0f im Druck nicht um
            "print_hold": bool(self.controller is not None
                               and self.controller.print_hold),
            # Externe Spule (19.10.2026, README §18): source cfs|extern (Makros
            # T0..T15/M8200/EXTERN_*), gespeichertes externes Material,
            # erstes Werkzeug des laufenden Drucks (-1 = keins)
            "source": self.source,
            "source_file": self.source_file or "",
            "source_load": self.source_load,
            "source_info": self.source_info or "",
            "extern_material": dict(self.extern_material or {}),
            "extern_material_label": extern_material_label(
                self.extern_material),
            "extern_tool": (self._extern_tool[1] if self._extern_tool
                            else -1),
            "extern_active": bool(self.chain.get("active")
                                  and self.chain.get("source") == EXTERN_CHAIN),
            "extern_op": (self.chain.get("op") or "")
            if self.chain.get("source") == EXTERN_CHAIN else "",
            "extern_last": self.extern_last or "",
            "extern_rfid": bool(self.cfg.get("extern_rfid")),
            "extern_rfid_last": self.extern_rfid_last or "",
        })
        return st

    # -- G-Code-Helfer ---------------------------------------------------
    @staticmethod
    def _slot_param(gcmd):
        for key in ("SLOT", "slot"):
            try:
                val = gcmd.get(key, None)
            except Exception:
                val = None
            if val is not None:
                return val
        raise ValueError("SLOT=A..D fehlt (z.B. KTWO_BOX_LOAD SLOT=B)")

    def cmd_status(self, gcmd):
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            gcmd.respond_info("[k2_box] kein Bus: %s" % exc)
            return
        try:
            poll = ctl.poll_status()
        except Exception as exc:
            gcmd.respond_info("[k2_box] Poll-Fehler: %s" % exc)
            return
        st = ctl.get_status()
        gcmd.respond_info(
            "[k2_box] Zustand=%s Hub=0x%02x Temp=%s °C Feuchte=%s %% Slots=%s "
            "Quelle=%s" % (
                st.get("state_name"), st.get("hub_mask") or 0,
                st.get("temp_c"), st.get("rh"), st.get("slots"),
                self.source.upper()))

    # --- Temperatur-Guard Einzelbefehle (18.10.2026, Abgleich mit gitstonelabs) ----
    # Der Box-Motor fördert/zieht am Klipper-Kaltextrusionsschutz vorbei:
    # KTWO_BOX_LOAD/UNLOAD (heizen NICHT selbst) senden bei kalter Düse
    # weder 0x10 noch 0x11 (gar nichts). Ketten, die selbst heizen
    # (KTWO_BOX_EXTRUDE: M109 vor do_extrude), sind nicht betroffen.
    MIN_EXTRUDE_TEMP_DEFAULT = 170.0  # Upstream-Default [extruder]

    def _min_extrude_temp(self):
        obj = self._lookup("extruder")
        heater = getattr(obj, "heater", None) if obj is not None else None
        val = getattr(heater, "min_extrude_temp", None)
        if val is None:
            try:
                val = self._obj_status("configfile")["settings"][
                    "extruder"]["min_extrude_temp"]
            except Exception:
                val = None
        try:
            return float(val)
        except (TypeError, ValueError):
            return self.MIN_EXTRUDE_TEMP_DEFAULT

    def _cold_reason(self):
        """None = Düse heiß genug, sonst Grund (Text). Vorrang hat
        ``can_extrude`` (Upstream-Extruder-Status, berücksichtigt auch
        min_extrude_temp 0), sonst Ist-Temperatur gegen min_extrude_temp;
        beides nicht lesbar -> gesperrt (sicher)."""
        st = self._obj_status("extruder")
        min_t = self._min_extrude_temp()
        temp = st.get("temperature")
        try:
            temp = None if temp is None else float(temp)
        except (TypeError, ValueError):
            temp = None
        if "can_extrude" in st:
            ok = bool(st["can_extrude"])
        elif temp is not None:
            ok = temp >= min_t
        else:
            return ("Extruder-Temperatur nicht lesbar ([extruder] fehlt?) – "
                    "Box-Motor würde den Kaltextrusionsschutz umgehen")
        if ok:
            return None
        return ("Düse %s °C unter min_extrude_temp %s °C (Box-Motor würde den "
                "Kaltextrusionsschutz umgehen) – erst heizen, z. B. M109 S220"
                % ("?" if temp is None else _n(round(temp, 1)), _n(min_t)))

    def cmd_load(self, gcmd):
        if self._extern_block(gcmd, "KTWO_BOX_LOAD (CFS-Einzug)"):
            return
        slot = self._slot_param(gcmd)
        try:
            dry = bool(gcmd.get_int("DRY_RUN", 0))
        except Exception:
            dry = False
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            gcmd.respond_info("[k2_box] kein Bus: %s" % exc)
            return
        if dry or not self.cfg.get("allow_write"):
            ctl2 = ctl
            try:
                res = ctl2.do_load(slot, dry_run=True)
                gcmd.respond_info("[k2_box] Trockenlauf (nichts gesendet): %s"
                                  % (res.get("plan"),))
            except Exception as exc:
                gcmd.respond_info("[k2_box] %s" % exc)
            if not dry:
                gcmd.respond_info("[k2_box] ALLOW_WRITE=0 (allow_write=False): "
                                  "nur gelesen, nichts geschrieben.")
            return
        cold = self._cold_reason()
        if cold:
            msg = ("[k2_box] Load SLOT=%s abgelehnt: %s – nichts gesendet "
                   "(weder 0x10 noch 0x11)" % (slot, cold))
            gcmd.respond_info(msg)
            raise gcmd.error(msg) if hasattr(gcmd, "error") else RuntimeError(msg)
        try:
            res = ctl.do_load(slot, inhibit=self._extrude_inhibit(ctl))
        except CfsStatusError as exc:
            msg = ("[k2_box] Load SLOT=%s abgebrochen: Box-Status 0x%02x (%s)" % (
                slot, exc.status, exc.name))
            gcmd.respond_info(msg)
            if self.cfg.get("shutdown_on_failure"):
                raise gcmd.error(msg) if hasattr(gcmd, "error") else RuntimeError(msg)
            return
        except (ValueError, PermissionError) as exc:
            gcmd.respond_info("[k2_box] %s" % exc)
            return
        except (RuntimeError, OSError) as exc:
            # RS485-Bus/Port ([k2_485] Port fehlt/gezogen, Timeout): G-Code-
            # Fehler (bricht Makro/Druck ab) statt "Internal error"+Shutdown.
            msg = "[k2_box] Load SLOT=%s: RS485-Bus-Fehler: %s" % (slot, exc)
            gcmd.respond_info(msg)
            raise gcmd.error(msg) if hasattr(gcmd, "error") else RuntimeError(msg)
        self.head_cut = False
        gcmd.respond_info("[k2_box] Load SLOT=%s ok (Odometer %s mm)" % (
            res.get("slot"), res.get("odo_mm")))

    def cmd_unload(self, gcmd):
        return self._with_head_sensor_off(lambda: self._cmd_unload(gcmd))

    def _cmd_unload(self, gcmd):
        if self._extern_block(gcmd, "KTWO_BOX_UNLOAD (CFS-Rückzug)"):
            return
        slot = self._slot_param(gcmd)
        try:
            dry = bool(gcmd.get_int("DRY_RUN", 0))
        except Exception:
            dry = False
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            gcmd.respond_info("[k2_box] kein Bus: %s" % exc)
            return
        if dry or not self.cfg.get("allow_write"):
            try:
                res = ctl.do_unload(slot, dry_run=True)
                gcmd.respond_info("[k2_box] Trockenlauf (nichts gesendet): %s"
                                  % (res.get("plan"),))
            except Exception as exc:
                gcmd.respond_info("[k2_box] %s" % exc)
            if not dry:
                gcmd.respond_info("[k2_box] ALLOW_WRITE=0 (allow_write=False): "
                                  "nur gelesen, nichts geschrieben.")
            return
        cold = self._cold_reason()
        if cold:
            msg = ("[k2_box] Unload SLOT=%s abgelehnt: %s – nichts gesendet "
                   "(weder 0x10 noch 0x11)" % (slot, cold))
            gcmd.respond_info(msg)
            raise gcmd.error(msg) if hasattr(gcmd, "error") else RuntimeError(msg)
        why = self._cut_missing(gcmd, "UNLOAD")
        if why:
            msg = "[k2_box] " + why
            gcmd.respond_info(msg)
            raise gcmd.error(msg) if hasattr(gcmd, "error") else RuntimeError(msg)
        try:
            res = ctl.do_unload(slot)
        except CfsStatusError as exc:
            msg = ("[k2_box] Unload SLOT=%s abgebrochen: Box-Status 0x%02x (%s)"
                   % (slot, exc.status, exc.name))
            gcmd.respond_info(msg)
            if self.cfg.get("shutdown_on_failure"):
                raise gcmd.error(msg) if hasattr(gcmd, "error") else RuntimeError(msg)
            return
        except (ValueError, PermissionError) as exc:
            gcmd.respond_info("[k2_box] %s" % exc)
            return
        except (RuntimeError, OSError) as exc:
            # RS485-Bus/Port ([k2_485] Port fehlt/gezogen, Timeout): G-Code-
            # Fehler (bricht Makro/Druck ab) statt "Internal error"+Shutdown.
            msg = "[k2_box] Unload SLOT=%s: RS485-Bus-Fehler: %s" % (slot, exc)
            gcmd.respond_info(msg)
            raise gcmd.error(msg) if hasattr(gcmd, "error") else RuntimeError(msg)
        self.cut_needed = ""                # Filament ist zurück in der Box
        self.head_cut = False
        gcmd.respond_info("[k2_box] Unload SLOT=%s ok" % (res.get("slot"),))

    def cmd_preload(self, gcmd):
        if self._extern_block(gcmd, "KTWO_BOX_PRELOAD (CFS-Preload)"):
            return
        slot = self._slot_param(gcmd)
        try:
            op = gcmd.get_int("OP", None)
        except Exception:
            op = None
        if op is None:
            try:
                op = gcmd.get("OP", None)
            except Exception:
                op = None
        try:
            dry = bool(gcmd.get_int("DRY_RUN", 0))
        except Exception:
            dry = False
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            gcmd.respond_info("[k2_box] kein Bus: %s" % exc)
            return
        if dry or not self.cfg.get("allow_write"):
            try:
                res = ctl.do_preload(slot, op if op is not None else 1,
                                     dry_run=True)
                gcmd.respond_info("[k2_box] Trockenlauf (nichts gesendet): %s"
                                  % (res.get("plan"),))
            except Exception as exc:
                gcmd.respond_info("[k2_box] %s" % exc)
            if not dry:
                gcmd.respond_info("[k2_box] ALLOW_WRITE=0 (allow_write=False): "
                                  "nur gelesen, nichts geschrieben.")
            return
        try:
            force = bool(gcmd.get_int("FORCE", 0))
        except Exception:
            force = False
        # Stock-Schutz (preload_block_reason): op 2/3 nur bei leerem
        # Kopfsensor, im Druck/Pause nur mit FORCE=1
        guard = {"head": self._head_filament(),
                 "printing": self._print_active(), "force": force}
        try:
            res = ctl.do_preload(slot, op, guard=guard)
        except CfsStatusError as exc:
            gcmd.respond_info("[k2_box] Preload abgebrochen: Box-Status 0x%02x "
                              "(%s)" % (exc.status, exc.name))
            return
        except (ValueError, PermissionError) as exc:
            gcmd.respond_info("[k2_box] %s" % exc)
            return
        except (RuntimeError, OSError) as exc:
            # RS485-Bus/Port ([k2_485] Port fehlt/gezogen, Timeout): G-Code-
            # Fehler (bricht Makro/Druck ab) statt "Internal error"+Shutdown.
            msg = "[k2_box] Preload SLOT=%s: RS485-Bus-Fehler: %s" % (slot, exc)
            gcmd.respond_info(msg)
            raise gcmd.error(msg) if hasattr(gcmd, "error") else RuntimeError(msg)
        gcmd.respond_info("[k2_box] Preload SLOT=%s OP=%s ok" % (
            res.get("slot"), res.get("op")))

    # =================================================================
    # Filamentwechsel – Klipper-Seite (T0–T15 / M8200-Kette)
    # =================================================================
    # Aufteilung (Beleg Live-Wechsel 12.10. 00:24–00:26, box.cfg T0-Beispiel):
    #   Box (RS485, allow_write): 0x04/0x0f/0x08/0x10/0x11/0x05 – Retrude,
    #     Einzug, Box-Zustand.
    #   Drucker (allow_move): Z-Hop, Schneidfahrt, Schacht, Heizen (M109),
    #     Extruder-Rückzug je p0, Stage-7-Greifen, Spülen, Abstreifen,
    #     Rückfahrt. Lüfter sichern/zurück machen die Makros (macros.cfg).
    @staticmethod
    def _new_chain():
        return {"active": False, "skip": False, "skip_reason": "",
                "tnn": "", "start_pos": None, "hop": 0.0,
                "paused_at_start": False, "source": "",
                "state_saved": False, "sensor_was_enabled": None,
                "orig_target": None, "temp_changed": False,
                "last_heat": None,
                "ending_purged": False, "empty_load": False,
                # Externe Spule (source EXTERN_CHAIN): LADEN|ENTLADEN +
                # {key, col, temp, override, label}
                "op": "", "extern": None}

    def _now(self):
        try:
            return self.reactor.monotonic()
        except Exception:
            return time.monotonic()

    def _lookup(self, name):
        try:
            return self.printer.lookup_object(name)
        except Exception:
            return None

    def _obj_status(self, name):
        obj = self._lookup(name)
        if obj is None or not hasattr(obj, "get_status"):
            return {}
        try:
            return dict(obj.get_status(self._now()))
        except Exception:
            return {}

    def _print_active(self):
        # Pausiert zählt mit (auch ohne virtual_sdcard, z. B. PAUSE aus dem
        # Runout-Handler vor KTWO_BOX_RUNOUT).
        return (self._obj_status("print_stats").get("state") in (
            "printing", "paused") or self._is_paused())

    def _is_paused(self):
        return bool(self._obj_status("pause_resume").get("is_paused"))

    def _released(self):
        """Wechsel-Schritte im Druck freigegeben? CFS: allow_write UND
        allow_move. Quelle EXTERN (19.10.2026): nur allow_move – dort sendet
        k2_box ohnehin keinen Box-Frame (Kopfbewegung/Heizen der externen
        Bausteine in der Pause)."""
        if not self.cfg.get("allow_move"):
            return False
        return bool(self.cfg.get("allow_write")) or self.source == SOURCE_EXTERN

    # --- Externe Spule: Sperre aller CFS-Aktionen (19.10.2026) ----------
    def _extern_write_block(self, frames=None):
        """Controller-Sperre (K2BoxController.write_block): Grund oder None.
        Quelle EXTERN: alles gesperrt AUSSER einem Ablauf, dessen Frames
        sämtlich in EXTERN_ALLOWED_FRAMES liegen (0x0d [0f 00/01], 0x0f
        [01/00] – Druckstart/-ende wie Stock live)."""
        if self.source != SOURCE_EXTERN:
            return None
        if frames and all(tuple(f) in EXTERN_ALLOWED_FRAMES for f in frames):
            return None
        return ("Quelle EXTERN (externe Spule, CFS-Schlauch abgesteckt) – "
                "erst SET_FILAMENT_SOURCE SOURCE=CFS")

    def _extern_block(self, gcmd, name, hint=""):
        """Quelle EXTERN -> CFS-Aktion ``name`` ablehnen: NICHTS gesendet,
        nichts bewegt. None = Quelle CFS (frei); True = erledigt (Kette
        übersprungen bzw. Druck pausiert). Druck läuft (nicht pausiert,
        z. B. Befehl aus der Druckdatei) -> !!-Meldung + PAUSE (ein G-Code-
        Fehler bräche den Druck ab); sonst G-Code-Fehler (Makro bricht ab).
        Eine schon übersprungene Kette (gesperrtes PRE_OPT) -> nur Hinweis."""
        if self.source != SOURCE_EXTERN:
            return None
        ch = self.chain
        if ch.get("active") and ch.get("skip"):
            gcmd.respond_info("[k2_box] %s übersprungen (%s)" % (
                name, ch.get("skip_reason") or "Kette übersprungen"))
            return True
        msg = ("[k2_box] %s gesperrt: Quelle ist EXTERN (externe Spule, "
               "CFS-Schlauch abgesteckt) – nichts gesendet, nichts bewegt%s. "
               "CFS nutzen: Schlauch an die CFS stecken, dann SET_FILAMENT_SOURCE SOURCE=CFS" % (
                   name, (" (%s)" % hint) if hint else ""))
        self.extern_last = msg
        if self._print_active() and not self._is_paused():
            if ch.get("active"):
                ch["skip"] = True
                ch["skip_reason"] = "Quelle extern"
            self._respond_error(msg + " – Druck wird PAUSIERT (kein Abbruch)")
            try:
                self._script(["PAUSE"])
            except Exception as exc:
                self._respond_error("[k2_box] PAUSE fehlgeschlagen: %s" % (exc,))
            return True
        if ch.get("active"):
            # Makro bricht ab: offene Kette nicht-mechanisch aufräumen
            self._chain_cleanup(gcmd)
        if hasattr(gcmd, "error"):
            raise gcmd.error(msg)
        raise RuntimeError(msg)

    def _sensor_state(self, name):
        """[filament_switch_sensor <name>] -> True/False, None = fehlt."""
        if not name:
            return None
        st = self._obj_status("filament_switch_sensor %s" % (name,))
        if "filament_detected" not in st:
            return None
        return bool(st["filament_detected"])

    def _head_filament(self):
        state = self._sensor_state(self.cfg.get("filament_sensor"))
        if state is False:
            self.head_cut = False       # kein (abgeschnittener) Faden mehr
        return state

    def _respond_error(self, msg):
        # "!! [k2_box] …" + logging.error, wirft nie
        self.konsole.fehler(msg)

    def _restore_warn(self, what, exc):
        """Rückbau in einem Fehler-/Aufräumpfad gescheitert – der eigentliche
        Fehler wird ohnehin gemeldet, das hier blieb früher still."""
        self.konsole.warnung("WARNUNG %s nicht zurückgesetzt (%s) – bitte "
                             "prüfen" % (what, self._exc_text(exc)))

    def _script(self, lines):
        self.gcode.run_script_from_command("\n".join(lines))

    def _move(self, gcmd, title, lines, wrap=True):
        """Bewegungs-/Heiz-Zeilen ausführen (nur allow_move) – sonst Plan.

        wrap: in SAVE/RESTORE_GCODE_STATE einpacken (G90/G91 des Aufrufers
        bleiben erhalten; RESTORE ohne MOVE korrigiert die E-Basis)."""
        if not lines:
            return False
        if not self.cfg.get("allow_move"):
            gcmd.respond_info("[k2_box] PLAN %s (allow_move: False – nichts "
                              "bewegt/geheizt):\n  %s" % (title,
                                                          "\n  ".join(lines)))
            return False
        body = list(lines)
        if wrap:
            body = (["SAVE_GCODE_STATE NAME=%s" % STEP_STATE_NAME] + body
                    + ["RESTORE_GCODE_STATE NAME=%s" % STEP_STATE_NAME])
        # L3 (18.10.2026): Fahrgrenzen wie Stock. Führt der Schritt aus dem
        # Betriebsfenster ([k2_limits]: X ab cut_pos_x, Y bis 352) – Schacht
        # Y 374-378, Wischen, Schneid-Kalibrierung –, weitet k2_box sie
        # (Stock box_wrapper set_flushing_sign -> SET_LIMITS) NUR für diesen
        # Schritt und gibt sie im finally zurück (Stock reset_flushing_sign
        # -> RESTORE_LIMITS), auch bei Fehler. Ohne [k2_limits] unverändert.
        lim = self._limits()
        widened = False
        if lim is not None and lim.needs_wide(body):
            lim.widen(LIMITS_OWNER, title)   # LimitsError: X/Y nicht gehomt
            widened = True
        try:
            self._script(body)
        except Exception:
            if wrap:
                try:
                    self._script(["RESTORE_GCODE_STATE NAME=%s"
                                  % STEP_STATE_NAME])
                except Exception:
                    pass
            raise
        finally:
            if widened:
                lim.release(LIMITS_OWNER)
        return True

    def _chain_limits_widen(self, gcmd):
        """M8200-Kette: Fahrgrenzen bis END_OPT aufweiten (s. CHAIN_LIMITS_
        OWNER). Ohne Freigabe/[k2_limits] nichts; nicht referenziert ->
        nur Meldung (die Datei-Fahrt scheitert dann wie bisher)."""
        if not self.cfg.get("allow_move"):
            return
        lim = self._limits()
        if lim is None:
            return
        try:
            lim.widen(CHAIN_LIMITS_OWNER, "M8200-Kette (Slicer fährt zum "
                      "Schacht)")
            self.chain["limits_wide"] = True
        except Exception as exc:
            gcmd.respond_info("[k2_box] M8200-Kette: Fahrgrenzen nicht "
                              "aufgeweitet (%s)" % (self._exc_text(exc),))

    def _chain_limits_release(self):
        lim = self._limits()
        if lim is None:
            return
        try:
            lim.release(CHAIN_LIMITS_OWNER)
        except Exception as exc:
            # Fahrgrenzen blieben weit -> rot (Kopf darf sonst in den Schacht)
            self.konsole.fehler("Fahrgrenzen der M8200-Kette nicht "
                                "freigegeben (%s) – KTWO_LIMITS_RESTORE bzw. "
                                "FIRMWARE_RESTART" % (self._exc_text(exc),))

    def _limits(self):
        """[k2_limits]-Objekt (verbunden) oder None."""
        try:
            lim = self.printer.lookup_object("k2_limits", None)
        except Exception:
            return None
        if lim is None or not getattr(lim, "available", lambda: False)():
            return None
        return lim

    def _plan_note(self):
        if self.source == SOURCE_EXTERN:
            # Quelle EXTERN: kein Box-Frame, es zählt nur allow_move
            return self._extern_plan_note()
        if self.cfg.get("allow_move") and self.cfg.get("allow_write"):
            return ""
        return " (Teil-Plan: allow_write=%s, allow_move=%s)" % (
            self.cfg.get("allow_write"), self.cfg.get("allow_move"))

    def _box_plan(self, gcmd, title, text):
        gcmd.respond_info("[k2_box] PLAN Box %s (allow_write: False – nichts "
                          "gesendet): %s" % (title, text))

    def _homed_ok(self):
        homed = str(self._obj_status("toolhead").get("homed_axes", ""))
        return all(a in homed for a in "xyz")

    def _gcode_pos(self):
        pos = self._obj_status("gcode_move").get("gcode_position")
        if pos is None:
            return None
        try:
            return [float(pos[0]), float(pos[1]), float(pos[2])]
        except Exception:
            return None

    def _max_accel(self):
        try:
            return float(self._obj_status("toolhead").get("max_accel"))
        except Exception:
            return None

    # --- Material (RFID-Etikett -> Material-DB), 17.10.2026 -------------
    def _cached_tag(self, letter):
        """(mat_id, col) aus dem Etikett-Cache + Handwert (KTWO_BOX_SLOT_SET,
        je Feld vorrangig), OHNE Bus (get_status). Ein Feld kann None sein,
        wenn nur das andere bekannt ist."""
        if not letter:
            return None
        ctl = self.controller
        m = None
        if ctl is not None:
            m = rfid_material(ctl.tag_cache.get(letter))
            if m is None:
                m = rfid_material((ctl.slot_cache.get(letter) or {})
                                  .get("rfid"))
        return merge_tag(m, self._manual(letter))

    def _slot_tag(self, letter):
        """(mat_id, col) des Slots: Cache/Handwert, sonst RFID lesen (0x02,
        lesend); None = unbekannt (kein Bus, kein/kaputtes Etikett). Mit
        Handwert wird NICHT gelesen (ein lesbares Etikett würde ihn sonst
        mitten im Wechsel verwerfen)."""
        if not letter:
            return None
        m = self._cached_tag(letter)
        if m is not None:
            return m
        try:
            return merge_tag(rfid_material(
                self._ensure_controller().get_rfid(letter)),
                self._manual(letter))
        except Exception:
            return None

    def _nozzle_key(self):
        """Material in der Düse: remain_material.type (Stock, nach dem
        letzten Spülen), sonst Etikett des geladenen Slots (Cache)."""
        if self.remain_material.get("type"):
            return self.remain_material["type"]
        ctl = self.controller
        m = self._cached_tag(ctl.loaded_slot) if ctl is not None else None
        return m[0] if m else None

    def _mat(self, key):
        return material_params(self.material_db, key,
                               self.cfg["tn_extrude_temp"])

    def _temp_limit(self):
        """[extruder] max_temp − 10 K (DB-Temperaturen kappen), None = ?"""
        try:
            mt = float(self._obj_status("configfile")["settings"]
                       ["extruder"]["max_temp"])
        except Exception:
            return None
        return mt - TEMP_LIMIT_MARGIN

    def _base_temp(self, gcmd):
        """Basis der Wechsel-Temperatur: BASE_TEMP= (Nachholen/Runout:
        Drucktemperatur vor der Pause) oder aktuelles Extruder-Ziel."""
        b = gcmd.get_float("BASE_TEMP", None, minval=0.)
        if b is not None:
            return float(b)
        try:
            return float(self._obj_status("extruder").get("target") or 0.0)
        except Exception:
            return 0.0

    def _db_temp(self, mp, label, notes, field="temp"):
        """Material-Temperatur aus der DB (gekappt), sonst Tn_extrude_temp
        (Stock-Fallback) + Hinweis in notes (nur mit geladener DB)."""
        fb = float(self.cfg["tn_extrude_temp"])
        if self.material_db is None:
            return fb
        ok = mp.get("temp_known") if field == "temp" else mp.get("max_known")
        if not ok:
            notes.append("%s: Material %s – Fallback Tn_extrude_temp %s °C "
                         "(Stock)" % (label, (
                             "unbekannt (kein RFID-Etikett)" if not mp.get("key")
                             else "%s nicht in der Material-DB" % mp["key"]
                             if not mp.get("known") else
                             "%s ohne %s" % (mp["key"], "nozzle_temperature"
                                             if field == "temp" else "maxTemp")),
                             _n(fb)))
            return fb
        t = float(mp[field])
        lim = self._temp_limit()
        if lim is not None and t > lim:
            notes.append("%s: DB-Temperatur %s °C über [extruder] max_temp−%s "
                         "-> %s °C" % (label, _n(t), _n(TEMP_LIMIT_MARGIN),
                                       _n(lim)))
            t = lim
        return t

    def _respond_notes(self, gcmd, notes):
        for n in notes:
            try:
                gcmd.respond_info("[k2_box] HINWEIS %s" % (n,))
            except Exception:
                pass

    def _change_temp(self, gcmd, key=None, label="Wechsel"):
        """Wechsel-Temperatur (Rückzug/Einzug): TEMP= (fest) oder
        max(Basis, Material-Temperatur). Basis = BASE_TEMP= bzw. aktuelles
        Extruder-Ziel; Material-Temperatur = kvParam.nozzle_temperature aus
        der Material-DB ([k2_box] material_database), Schlüssel = RFID-
        material_type (mat_id) des Slots (Stock get_material_target_temp,
        live „get next material temp: 220“). Ohne DB bzw. unbekanntes
        Material: Tn_extrude_temp 220 (Stock-Fallback, mit DB + Hinweis).
        Die Temperatur steht NICHT im RFID-Etikett – nur der Schlüssel."""
        t = gcmd.get_float("TEMP", None)
        if t is not None:
            return float(t)
        notes = []
        mt = self._db_temp(self._mat(key), label, notes)
        self._respond_notes(gcmd, notes)
        return max(self._base_temp(gcmd), mt)

    def _prime_speed(self):
        """RESUME-Vorextrusion wie Stock RESUME_EXTERNAL
        (remain_material_velocity · 0,8 = vol/2,4·60·0,8, live 460 / 280):
        nur mit Material-DB; unbekanntes Material -> 360·0,8 = 288 (Stock-
        Fallback). 0.0 = DB aus -> macros.cfg resume_prime_speed (460)."""
        if self.material_db is None:
            return 0.0
        mp = self._mat(self._nozzle_key())
        if mp.get("vol"):
            return float(mp["vol"]) / 2.4 * 60.0 * 0.8
        return PRIME_FALLBACK_VELOCITY * 0.8

    def _grab_velocity(self, key):
        """Stage-7-Greifen (Extruder buffer_empty_len): tn_extrude_velocity,
        mit DB gedeckelt auf vol/2,4·60 (Stock-Formel,
        TPU 50 mm/min). Pi-Erweiterung – Stock-Geschwindigkeit unbelegt."""
        v = float(self.cfg["tn_extrude_velocity"])
        if self.material_db is None:
            return v
        mp = self._mat(key)
        if mp.get("vol"):
            return min(v, float(mp["vol"]) / 2.4 * 60.0)
        return v

    def _nozzle_diameter(self):
        try:
            return float(self._obj_status("configfile")["settings"]
                         ["extruder"]["nozzle_diameter"])
        except Exception:
            return float(self.cfg["baseline_nozzle_diameter"])

    def _min_cruise_ratio(self):
        try:
            v = self._obj_status("toolhead").get("minimum_cruise_ratio")
            return None if v is None else float(v)
        except Exception:
            return None

    def _pin(self, name):
        """[output_pin <name>] -> (Wert 0..1, scale) oder None (fehlt)."""
        if not name:
            return None
        obj = self._lookup("output_pin %s" % (name,))
        if obj is None:
            return None
        try:
            val = float(obj.get_status(self._now()).get("value") or 0.0)
        except Exception:
            val = 0.0
        scale = getattr(obj, "scale", None)
        try:
            scale = float(scale) if scale else None
        except Exception:
            scale = None
        return val, scale

    def _extruder_target(self):
        """Aktuelles Extruder-Ziel in °C, None = nicht lesbar."""
        st = self._obj_status("extruder")
        if "target" not in st:
            return None
        try:
            return float(st.get("target") or 0.0)
        except Exception:
            return None

    def _heat(self, gcmd, temp):
        # orig_target = Ziel VOR dem ersten Heizen der Kette (vor M109, damit
        # es auch bei einem Abbruch während M109 stimmt – M109 setzt das Ziel
        # vor dem Warten). END_OPT stellt es wieder her, _fail gibt es an
        # PAUSE (CFS_TEMP, s. _pause_temp). Nicht lesbar -> 0.0 (= unbekannt).
        ch = self.chain
        if ch.get("active") and ch.get("orig_target") is None:
            t = self._extruder_target()
            ch["orig_target"] = 0.0 if t is None else t
        if self._move(gcmd, "Heizen", ["M109 S%s" % _n(temp)], wrap=False):
            if ch.get("active"):
                ch["temp_changed"] = True
                ch["last_heat"] = float(temp)

    def _cut_missing(self, gcmd, name):
        """Schnittschutz (22.10.2026) für Einzel-RETRUDE/UNLOAD AUSSERHALB
        einer Kette (dort schneidet CUT vorher): seit dem letzten Spülen bzw.
        Druckbeginn kein Schnitt -> Ablehnungstext, sonst None. Live: RETRUDE
        direkt nach FLUSH -> p0 ×4 liefen, p1 brach mit Box-Status 0x19
        (Pull-back: Motorblock > 10 s) ab – das geschmolzene Ende verdickt
        beim Rückzug. Nur mit allow_write (ohne gibt es keinen Box-Rückzug,
        Plan-Pfade bleiben unverändert); FORCE=1 übergeht mit Warnung."""
        if not self.cut_needed or self.chain.get("active") \
                or not self.cfg.get("allow_write"):
            return None
        try:
            force = bool(gcmd.get_int("FORCE", 0))
        except Exception:
            force = False
        if force:
            gcmd.respond_info("[k2_box] WARNUNG %s ohne Schnitt seit %s "
                              "(FORCE=1) – Box kann mit 0x19 Motorblock "
                              "abbrechen" % (name, self.cut_needed))
            return None
        return ("%s abgelehnt: seit %s kein Schnitt – erst KTWO_BOX_CUT "
                "(Stock schneidet vor jedem Rückzug; ohne Schnitt Box-Status "
                "0x19 Motorblock, live 22.10.2026). Übergehen mit FORCE=1 – "
                "nichts gesendet" % (name, self.cut_needed))

    def _with_head_sensor_off(self, fn):
        """Einzelbefehl AUSSERHALB einer Wechselkette (KTWO_BOX_RETRUDE/
        UNLOAD von Hand): Kopfsensor für die Dauer sperren wie in der Kette
        (Stock disable_filament_sensor). Sonst wertet Upstream das Abfallen
        beim Rückzug als Runout und pausiert (pause_on_runout, idle_timeout
        „Printing“, solange Befehle laufen) – beobachtet 22.10.2026 bei
        KTWO_BOX_RETRUDE ohne Druck: PAUSE parkt den Kopf und senkt das
        Düsenziel. In einer Kette sperrt PRE_OPT selbst."""
        if self.chain.get("active"):
            return fn()
        sens = self._obj_status("filament_switch_sensor %s"
                                % (self.cfg.get("filament_sensor"),))
        was = bool(sens.get("enabled")) if "enabled" in sens else False
        if was:
            self._set_head_sensor(False)
        try:
            return fn()
        finally:
            if was:
                self._set_head_sensor(True)

    def _set_head_sensor(self, enable):
        name = self.cfg.get("filament_sensor")
        if not name or self._lookup("filament_switch_sensor %s" % name) is None:
            return
        try:
            self._script(["SET_FILAMENT_SENSOR SENSOR=%s ENABLE=%d"
                          % (name, 1 if enable else 0)])
        except Exception as exc:
            self._restore_warn("Kopfsensor %s (ENABLE=%d)"
                               % (name, 1 if enable else 0), exc)

    def _check_tnn(self, raw):
        box, idx, letter, tnn = parse_tnn(raw)
        if box != int(self.cfg.get("box_addr", BOX_ADDR_DEFAULT)):
            raise BoxChangeError(
                "%s liegt in Box %d, konfiguriert ist nur Box %d ([k2_box] "
                "box_addr) – weitere Boxen ANNAHME/ungetestet" % (
                    tnn, box, int(self.cfg.get("box_addr", 1))))
        return box, idx, letter, tnn

    def _already_loaded(self, idx, letter):
        """Slot schon geladen? Hub-Sensor (0x08 [01], lesend) + Kopfsensor."""
        try:
            ctl = self._ensure_controller()
        except Exception:
            return False
        try:
            hub = ctl.hub_mask()
        except Exception:
            hub = None
        head = self._head_filament()
        if hub is None:
            return ctl.loaded_slot == letter and head is not False
        if hub == SLOT_MASKS[idx] and head is not False:
            ctl.loaded_slot = letter
            self._remember_tag(ctl, letter)
            return True
        if hub == 0:
            ctl.loaded_slot = None
        return False

    @staticmethod
    def _remember_tag(ctl, letter):
        """RFID-Etikett des geladenen Slots merken (lesend, best effort) –
        für das Auto-Nachladen, falls die leere Spule später nichts liefert."""
        try:
            ctl.get_rfid(letter)
        except Exception:
            pass

    def _go_extrude_pos(self, gcmd):
        c = self.cfg
        pos = self._gcode_pos()
        if pos is not None and abs(pos[0] - c["extrude_pos_x"]) < 0.5 \
                and abs(pos[1] - c["extrude_pos_y"]) < 0.5:
            return False
        lines = gcode_go_extrude_pos(c)
        # Kopf noch am Schneidhebel (abgebrochener Schnitt, Schritt von Hand
        # wiederholt): erst nur X heraus, sonst zöge "G0 Y345" den Kopf
        # seitlich am Hebel entlang.
        lead = gcode_leave_cut_zone(c, pos)
        if lead:
            lines = lines[:1] + lead + lines[1:]
        return self._move(gcmd, "Abfallschacht (BOX_GO_TO_EXTRUDE_POS)", lines)

    @staticmethod
    def _exc_text(exc):
        if isinstance(exc, CfsStatusError):
            return "Box-Status 0x%02x (%s)" % (exc.status, exc.name)
        txt = str(exc)
        return txt if txt else type(exc).__name__

    def _chain_cleanup(self, gcmd=None):
        """Nicht-mechanisches Aufräumen: Kopfsensor, G-Code-Zustand,
        Fahrgrenzen der M8200-Kette."""
        ch = self.chain
        self._chain_limits_release()
        if ch.get("sensor_was_enabled") is not None:
            self._set_head_sensor(ch["sensor_was_enabled"])
        if ch.get("state_saved"):
            try:
                self._script(["RESTORE_GCODE_STATE NAME=%s" % CHAIN_STATE_NAME])
            except Exception:
                pass
        self.chain = self._new_chain()

    def _resume_target(self):
        """Rückfahrziel für RESUME nach einem Wechsel-Fehler im LAUFENDEN
        Druck: die Druckposition, an der die Kette begann (PRE_OPT, vor dem
        Z-Hop) bzw. vor dem fehlgeschlagenen Einzelschritt (ohne Kette, z. B.
        M8200 C ohne P). Upstream-PAUSE_STATE hielte sonst die Fehlerposition
        (am Schneidhebel, im Schacht) – RESUME_BASE führe gerade dorthin und
        der Druck ginge dort weiter. None = kein eigenes Ziel nötig: Kette in
        einer Pause gestartet (PAUSE_STATE ist dann schon die Druckposition),
        Position unbekannt, oder Kopf steht noch dort (±0,01 mm)."""
        ch = self.chain
        if ch.get("active") and self._m8200_chain(ch) \
                and not ch.get("hop") and self._step_start is not None:
            # M8200-Block (Creality Print, Live 26.10.2026): nach RESUME
            # läuft die Datei mit dem Rest des Blocks weiter (eigene
            # Vorextrusion G1 E40, Spülen, Wischen im Schacht) – der Kopf muss
            # dort stehen, wo die Datei ihn vor dem gescheiterten Schritt
            # hatte (Stock: RESUME_BASE = Fehlerposition). Die Druckposition
            # vor dem Block wäre falsch: dort landeten Vorextrusion und
            # Spülmenge auf dem Teil.
            sp = self._step_start
        elif ch.get("active"):
            sp = None if ch.get("paused_at_start") else ch.get("start_pos")
        else:
            sp = self._step_start
        if sp is None:
            return None
        sp = [float(v) for v in sp[:3]]
        pos = self._gcode_pos()
        if pos is not None and max(abs(pos[i] - sp[i]) for i in range(3)) < 0.01:
            return None
        return sp

    @staticmethod
    def _m8200_chain(ch):
        """Kette aus dem M8200-Block der Datei (M8200 P, SOURCE=M8200) –
        nicht T<n>, nicht von Hand, nicht externe Spule."""
        return ch.get("source") == M8200_CHAIN

    def _loaded_tnn(self):
        """Geladenes Werkzeug wie get_status loaded_tnn ("" = unbekannt)."""
        letter = getattr(self.controller, "loaded_slot", None) \
            if self.controller is not None else None
        return ("T%d%s" % (self.cfg.get("box_addr", 1), letter)
                if letter else "")

    def _m8200_wieder_oeffnen(self, gcmd):
        """Ende eines RESUME mitten im M8200-Block (Creality Print): die
        Datei läuft mit dem Rest des Blocks weiter (Schacht-Fahrten bis Y378,
        T<n>, Spülen, Wischen) bis „M8200 O“. Live 26.10.2026: T1 in der
        Pause hatte die Kette geschlossen -> „Move out of range“ bei der
        Wischfahrt auf Y374, Druck abgebrochen. Ebenso setzt jedes RESUME die
        Fahrgrenzen zurück (Reinigung/Rückfahrt: KTWO_LIMITS_RESTORE =
        restore_all), ein G-Code-Fehler ebenso ([k2_limits] _on_error).

        Daher: offene M8200-Kette (bzw. eine in der Pause geschlossene,
        _m8200_offen) -> Kette offen halten bzw. wieder öffnen, Kopfsensor
        wie PRE_OPT sperren (M8200 O gibt ihn frei), Fahrgrenzen weiten.
        Ist das Ziel schon geladen (Wechsel in der Pause erledigt), laufen
        die restlichen Schritte des Blocks leer (skip) – sonst schnitte M8200
        C das frisch geladene Filament bzw. zöge M8200 R es zurück."""
        tnn = self._m8200_offen
        self._m8200_offen = ""
        ch = self.chain
        still = ch.get("active") and self._m8200_chain(ch)
        if not (tnn or still) or not self._print_active():
            return
        if still:
            tnn = ch.get("tnn") or tnn
            what = "Kette %s noch offen" % (tnn or "ohne TNN",)
        else:
            if ch.get("active"):
                self._chain_cleanup(gcmd)
            ch = self._new_chain()
            ch.update({"active": True, "tnn": tnn, "source": M8200_CHAIN})
            self.chain = ch
            what = "Kette %s wieder geöffnet" % (tnn or "ohne TNN",)
        loaded = self._loaded_tnn()
        if tnn and loaded == tnn and not ch.get("skip"):
            ch["skip"] = True
            ch["skip_reason"] = ("%s schon geladen (Wechsel in der Pause "
                                 "erledigt)" % (tnn,))
            what += ", restliche Wechsel-Schritte laufen leer"
        sens = self._obj_status("filament_switch_sensor %s"
                                % (self.cfg.get("filament_sensor"),))
        if "enabled" in sens:
            if ch.get("sensor_was_enabled") is None:
                ch["sensor_was_enabled"] = bool(sens["enabled"])
            if sens["enabled"]:
                self._set_head_sensor(False)
        self._chain_limits_widen(gcmd)
        gcmd.respond_info(
            "[k2_box] RESUME: Datei steht noch im M8200-Wechselblock (bis "
            "M8200 O) – %s, Fahrgrenzen zum Schacht %s" % (
                what, "aufgeweitet" if ch.get("limits_wide")
                else "NICHT aufgeweitet"))

    def _pause_temp(self):
        """Druck-Zieltemperatur für ``PAUSE CFS_TEMP`` nach einem Abbruch
        im LAUFENDEN Druck -> (temp, hinweis).

        Hintergrund: das PAUSE-Makro merkt ``printer.extruder.target`` als
        hotend_temp (wie Stock PAUSE_EXTERNAL, live
        Stock-gcode_macro.cfg) und RESUME heizt
        darauf (macros.cfg _PI_RESUME_PREPARE M109, Stock RESUME_EXTERNAL
        ebd. :885-892). Hat der Wechsel schon geheizt (_heat -> M109
        Wechsel-Temperatur max(Ziel, Material-/Tn_extrude_temp)), wäre das die
        Wechsel- statt der Drucktemperatur. temp = Ziel vor dem ersten
        Heizen (Kette: orig_target; Einzelschritt: _step_target), None =
        kein CFS_TEMP nötig/möglich:
        - noch nicht geheizt bzw. Ziel unverändert (±0,01 °C) -> PAUSE merkt
          ohnehin das richtige Ziel;
        - Ziel vor dem Wechsel 0/nicht lesbar -> UNBEKANNT: PAUSE merkt wie
          bisher das aktuelle Ziel (lieber auf Wechsel-Temperatur als kalt
          weiterdrucken), hinweis erklärt das."""
        ch = self.chain
        orig = ch.get("orig_target") if ch.get("active") else self._step_target
        if orig is None:
            return None, ""
        cur = self._extruder_target()
        if orig <= 0.0:
            if cur is not None and cur > 0.0:
                return None, (
                    " Druck-Zieltemperatur vor dem Wechsel unbekannt (0) – "
                    "RESUME heizt auf das aktuelle Ziel %s °C; ggf. vor "
                    "RESUME SET_GCODE_VARIABLE MACRO=_PI_PARAM "
                    "VARIABLE=hotend_temp VALUE=<°C>." % (_n(cur),))
            return None, ""
        if cur is not None and abs(cur - orig) < 0.01:
            return None, ""
        return float(orig), ""

    @staticmethod
    def _tool_of(tnn):
        """'T1B' -> 'T1' (Werkzeugnummer wie macros.cfg T0..T15)."""
        try:
            box, idx, _l, _t = parse_tnn(tnn)
        except ValueError:
            return ""
        return "T%d" % ((box - 1) * len(SLOT_LETTERS) + idx)

    def _record_abort(self, gcmd, name):
        """Abgebrochenen Wechsel für RESUME merken (Stock recode_err ->
        save_resume_tnn): Schritt, Ziel-TNN, Herkunft der Kette. Ein
        Einzelschritt ohne eigene Kette/TNN (z. B. KTWO_BOX_CUT von Hand in
        der Pause) behält Ziel und Herkunft des früheren Abbruchs."""
        ch = self.chain
        old = self.aborted or {}
        if ch.get("active") and ch.get("tnn"):
            tnn, src = ch["tnn"], ch.get("source") or ""
        else:
            tnn = ""
            try:
                raw = gcmd.get("TNN", None)
            except Exception:
                raw = None
            if raw:
                try:
                    tnn = parse_tnn(raw)[3]
                except ValueError:
                    tnn = ""
            if not tnn:
                tnn = old.get("tnn") or ""
            src = (ch.get("source") if ch.get("active") else "") or (
                old.get("source", "") if tnn and tnn == old.get("tnn") else "")
        self.aborted = {"step": name, "tnn": tnn, "source": src or ""}

    def _fail(self, gcmd, name, text, record=True):
        """Fehler im Wechsel: im Druck PAUSE (kein Shutdown, kein Abbruch),
        Folgeschritte überspringen; sonst Aufräumen + G-Code-Fehler.

        Im Druck merkt ``_record_abort`` den abgebrochenen Wechsel (Ziel-TNN):
        RESUME prüft dann den Kopfsensor (cmd_resume_check, leer = gesperrt
        bzw. Nachholen). ``record=False`` nur für eine Ablehnung ohne jede
        Aktion (PRE_OPT bei offenem Fehler).

        Löst DIESER Fehler die Pause aus (Druck lief), bekommt PAUSE das
        Rückfahrziel ``CFS_RETURN_X/Y/Z`` (= _resume_target, Z ohne Hop):
        das PAUSE-Makro (macros.cfg) merkt es statt der Fehlerposition und
        quittiert mit ``KTWO_BOX_RESUME_FIX CLEAR=1`` (Z ist Teil des
        Ziels); ohne dieses Makro (Upstream-PAUSE ignoriert die Parameter)
        bleibt der alte Z-Fallback pending_resume_z. In einer schon
        bestehenden Pause (Wiederholung von Hand, Runout-Kette) weder Ziel
        noch Z-Fallback – PAUSE_STATE ist dort bereits die Druckposition.

        Temperatur (Fix 17.10.2026 spät): hatte der Wechsel schon geheizt,
        bekommt die NEUE Pause ``CFS_TEMP=<Ziel vor dem Wechsel>``
        (_pause_temp) – das PAUSE-Makro merkt es als hotend_temp statt der
        Wechsel-Temperatur. In einer bestehenden Pause kein CFS_TEMP:
        hotend_temp stammt dort schon aus der ersten Pause."""
        msg = "[k2_box] %s fehlgeschlagen: %s" % (name, text)
        check = self._check_hint(text)
        if self._print_active() and self.cfg.get("pause_on_failure") \
                and self._cancel_requested():
            # C2: echter Fehler, während ein Druckabbruch wartet -> keine
            # PAUSE (CANCEL_PRINT folgt). chain_error/Schritt aber merken:
            # KTWO_BOX_END_PRINT CANCEL=1 entlädt dann nur nach "sicheren"
            # Schritten (_cancel_unload_block); KTWO_BOX_CANCEL_RESET löscht
            # ihn nach CANCEL_PRINT_BASE.
            if not self.chain_error:
                self.chain_error = msg
                self._chain_error_step = name
            if record:
                self._record_abort(gcmd, name)
            ch = self.chain
            if ch.get("active") and not ch.get("skip"):
                ch["skip"] = True
                ch["skip_reason"] = "Fehler bei wartendem Druckabbruch"
            self._respond_error("%s – Druckabbruch (CANCEL) wartet: keine "
                                "Pause, CANCEL_PRINT folgt" % (msg,))
            return None
        # KTWO_BOX_END_PRINT (aus END_PRINT): keine PAUSE am Druckende –
        # Aufräumen + Fehler, den cmd_end_print abfängt und meldet (END_PRINT
        # läuft weiter und schaltet die Heizungen aus).
        if self._print_active() and self.cfg.get("pause_on_failure") \
                and not self._end_print:
            if not self.chain_error:
                self.chain_error = msg
                self._chain_error_step = name
            if record:
                self._record_abort(gcmd, name)
            ch = self.chain
            was_paused = self._is_paused()
            if record and name != "END_OPT" and ch.get("active") \
                    and self._m8200_chain(ch):
                self._m8200_offen = ch.get("tnn") or self._m8200_offen
            if ch.get("start_pos") is not None:
                self._keep_start_pos = list(ch["start_pos"])
            target = None if was_paused else self._resume_target()
            if not was_paused and ch.get("hop") and ch.get("active") \
                    and ch.get("start_pos") is not None:
                self.pending_resume_z = float(ch["start_pos"][2])
            # Drucktemperatur vor dem Wechsel (nur für eine NEUE Pause: in
            # einer bestehenden hat PAUSE hotend_temp schon gemerkt – ein
            # Wiederholungs-T<n> heizt von der Pausentemperatur aus)
            temp, temp_hint = (None, "") if was_paused else self._pause_temp()
            hint = ""
            if target is not None:
                hint = (" Rückfahrziel für RESUME = Druckposition vor dem "
                        "Wechsel X%s Y%s Z%s (nicht die Fehlerposition)."
                        % tuple(_n(v) for v in target))
            if temp is not None:
                hint += (" RESUME heizt auf die Drucktemperatur vor dem "
                         "Wechsel %s °C (nicht die Wechsel-Temperatur %s °C)."
                         % (_n(temp), _n(self._extruder_target() or 0.0)))
            hint += temp_hint
            if was_paused and ch.get("active") and ch.get("temp_changed") \
                    and ch.get("orig_target") is not None:
                # Abbruch IN einer Pause (Wiederholung von Hand, Nachholen im
                # RESUME, Runout-Kette): Düse zurück auf das Ziel vor diesem
                # Wechsel (= Pausentemperatur, macros.cfg PAUSE M104 S140) wie
                # END_OPT im Erfolgsfall – sonst stünde sie die ganze Pause
                # auf Wechsel-Temperatur. RESUME heizt aus hotend_temp.
                try:
                    if self._move(gcmd, "Zieltemperatur zurück (Abbruch in "
                                  "der Pause)", ["M104 S%s" % _n(
                                      ch["orig_target"])], wrap=False):
                        ch["temp_changed"] = False
                        hint += (" Düse zurück auf %s °C (Ziel vor diesem "
                                 "Wechsel)." % (_n(ch["orig_target"]),))
                except Exception as exc:
                    hint += " Zieltemperatur zurück fehlgeschlagen: %s." % (exc,)
            # Reihenfolge (Fix 17.10. spät): ein T<n> bei offenem Fehler lehnt
            # PRE_OPT ab -> erst quittieren, dann wiederholen. RESUME prüft
            # danach den Kopfsensor (leer = gesperrt, cmd_resume_check).
            self._respond_error(
                "%s – Druck wird PAUSIERT (kein Abbruch). Ursache beheben, "
                "KTWO_BOX_ERROR_CLEAR, Wechsel (T<n>) bzw. Schritt von Hand "
                "wiederholen, dann RESUME (gesperrt, solange der "
                "Kopf-Filamentsensor leer meldet).%s" % (msg, hint))
            if check:
                self._respond_error("[k2_box] %s" % (check,))
            if not was_paused:
                line = "PAUSE"
                if target is not None:
                    line += (" CFS_RETURN_X=%s CFS_RETURN_Y=%s CFS_RETURN_Z=%s"
                             % tuple(_n(v) for v in target))
                if temp is not None:
                    line += " CFS_TEMP=%s" % (_n(temp),)
                # PAUSE-Makro ruft KTWO_BOX_RESUME_FIX CLEAR=1: diese Pause
                # stammt aus _fail -> den gerade gemerkten Abbruch behalten
                self._pause_from_fail = True
                try:
                    self._script([line])
                except Exception as exc:
                    self._respond_error("[k2_box] PAUSE fehlgeschlagen: %s"
                                        % (exc,))
                finally:
                    self._pause_from_fail = False
            return None
        ch = self.chain
        if ch.get("active") and ch.get("source") == EXTERN_CHAIN \
                and ch.get("temp_changed"):
            # Externe Spule außerhalb eines Drucks: Heizung aus wie Stock
            # SET_COOL_TEMP (idle_timeout schaltet am Pi nie ab)
            try:
                self._script(["M104 S0"])
                msg += " – Düse aus (M104 S0)"
            except Exception as exc:
                msg += (" – Düse aus (M104 S0) FEHLGESCHLAGEN (%s), Düse von "
                        "Hand ausschalten" % (self._exc_text(exc),))
        self._chain_cleanup(gcmd)
        if check:
            msg += " – " + check
        if hasattr(gcmd, "error"):
            raise gcmd.error(msg)
        raise RuntimeError(msg)

    @staticmethod
    def _check_hint(text):
        """Prüfhinweis (box.status_check_hint) zum ersten 'Box-Status 0x..'
        in einem Fehlertext, sonst '' (alter Nachbau ohne Tabelle -> '')."""
        m = re.search(r"Box-Status 0x([0-9a-fA-F]{2})", str(text))
        fn = getattr(_BOX, "status_check_hint", None)
        if not m or fn is None:
            return ""
        return fn(int(m.group(1), 16))

    def _run_step(self, gcmd, name, body, chain_step=True, need_release=True):
        ch = self.chain
        if self._cancel_requested():
            # C2: wie Stock cancel_pending – Befehl läuft leer
            return self._cancel_skip(gcmd, name)
        if chain_step and ch.get("active") and (ch.get("skip")
                                                or self.chain_error):
            why = (ch.get("skip_reason") if ch.get("skip")
                   else "Wechsel-Fehler offen")
            gcmd.respond_info("[k2_box] %s übersprungen (%s)" % (name, why))
            return None
        if need_release and self._print_active() and not self._released():
            return self._fail(gcmd, name, (
                "Filamentwechsel im Druck nicht freigegeben ([k2_box] "
                "allow_write=%s, allow_move=%s) – nichts ausgeführt" % (
                    self.cfg.get("allow_write"), self.cfg.get("allow_move"))))
        # Einzelschritt ohne Kette im laufenden Druck: Position merken
        # (Rückfahrziel, falls der Schritt abbricht – s. _resume_target),
        # ebenso das Extruder-Ziel (Drucktemperatur für PAUSE CFS_TEMP).
        single = (not ch.get("active") and self._print_active()
                  and not self._is_paused())
        # M8200-Kette (Creality Print): Rückfahrziel = Position vor DIESEM
        # Schritt (s. _resume_target)
        m8200 = (ch.get("active") and self._m8200_chain(ch)
                 and self._print_active() and not self._is_paused())
        self._step_start = self._gcode_pos() if (single or m8200) else None
        self._step_target = self._extruder_target() if single else None
        try:
            return body(gcmd)
        except BoxCancelled as exc:
            if self._cancel_requested():
                return self._cancel_skip(gcmd, name, self._exc_text(exc))
            return self._fail(gcmd, name, self._exc_text(exc))
        except Exception as exc:
            return self._fail(gcmd, name, self._exc_text(exc))
        finally:
            self._step_start = None
            self._step_target = None

    # -- G-Codes ---------------------------------------------------------
    def cmd_pre_opt(self, gcmd):
        if self.chain.get("active"):  # alte Kette ohne END_OPT
            old = self.chain
            if self._m8200_chain(old) and self._is_paused() \
                    and self._print_active():
                # T<n>/Runout-Kette in der Pause mitten im M8200-Block der
                # Datei: der Block läuft nach RESUME weiter (s.
                # _m8200_wieder_oeffnen)
                self._m8200_offen = old.get("tnn") or self._m8200_offen
            self._chain_cleanup(gcmd)
        if self.source == SOURCE_EXTERN:
            # Quelle EXTERN: keine CFS-Kette. Im Druck -> PAUSE und die
            # Folgeschritte (CUT/RETRUDE/EXTRUDE/FLUSH) der Kette überspringen
            # (skip), END_OPT räumt auf; sonst G-Code-Fehler.
            ch = self._new_chain()
            ch.update({"active": True,
                       "source": str(gcmd.get("SOURCE", "") or "").strip()
                       .upper()})
            self.chain = ch
            res = self._extern_block(gcmd, "KTWO_BOX_PRE_OPT (CFS-Wechsel)",
                                     "externe Spule: LOAD_FILAMENT/"
                                     "UNLOAD_FILAMENT")
            if self.chain is ch and ch.get("active"):
                ch["skip"] = True
                ch["skip_reason"] = "Quelle extern – kein CFS-Wechsel"
            return res
        ch = self._new_chain()
        ch["active"] = True
        # Herkunft der Kette: T = T<n>-Befehl (macros.cfg _KTWO_TOOL_CHANGE
        # bzw. Nachholen im RESUME) – nur dieser Wechsel wird beim RESUME
        # nachgeholt (Stock: resume_tnn nur für Tn_action-Fehler, nicht für
        # die M8200-Kette); leer = M8200/Runout/
        # von Hand.
        ch["source"] = str(gcmd.get("SOURCE", "") or "").strip().upper()
        self.chain = ch
        if self.chain_error:
            # Ablehnung ohne jede Aktion: den gemerkten Abbruch (Ziel-TNN)
            # NICHT durch diesen leeren Versuch ersetzen (record=False)
            return self._fail(gcmd, "PRE_OPT", (
                "Wechsel-Fehler offen (%s) – erst KTWO_BOX_ERROR_CLEAR, dann "
                "den Wechsel wiederholen" % (self.chain_error,)), record=False)
        if ch["source"] != "T":
            self._chain_limits_widen(gcmd)

        def body(gcmd):
            c = self.cfg
            hop = gcmd.get_float("Z_HOP", c["change_z_hop"], minval=0.,
                                 maxval=5.)
            tnn_raw = gcmd.get("TNN", None)
            if tnn_raw:
                _b, idx, letter, tnn = self._check_tnn(tnn_raw)
                ch["tnn"] = tnn
                if self._already_loaded(idx, letter):
                    ch["skip"] = True
                    ch["skip_reason"] = "%s ist bereits geladen" % (tnn,)
                    gcmd.respond_info("[k2_box] PRE_OPT: %s bereits geladen – "
                                      "kein Wechsel" % (tnn,))
                    return
            if c.get("allow_move") and not self._homed_ok():
                raise BoxChangeError("Achsen nicht referenziert (homed_axes=%r)"
                                     " – erst G28" % (self._obj_status(
                                         "toolhead").get("homed_axes", ""),))
            # Kopfsensor sperren (Stock disable_filament_sensor): beim
            # Rückzug fällt er ab -> Upstream würde sonst pausieren.
            sens = self._obj_status("filament_switch_sensor %s"
                                    % (c.get("filament_sensor"),))
            if "enabled" in sens:
                ch["sensor_was_enabled"] = bool(sens["enabled"])
                self._set_head_sensor(False)
            # Startposition: Wiederholungs-T<n> IN der Pause nach einem
            # Abbruch -> Startposition des abgebrochenen Wechsels; sonst (Druck
            # läuft, kein Druck) die aktuelle Position und eine alte Start-
            # position verwerfen (Fix 17.10.: nach CANCEL/RESUME hätte END_OPT
            # sonst zur Position eines früheren Wechsels zurückgefahren).
            ch["paused_at_start"] = self._is_paused()
            if ch["paused_at_start"] and self._keep_start_pos:
                ch["start_pos"] = list(self._keep_start_pos)
            else:
                self._keep_start_pos = None
                ch["start_pos"] = self._gcode_pos()
            self._script(["SAVE_GCODE_STATE NAME=%s" % CHAIN_STATE_NAME])
            ch["state_saved"] = True
            if hop > 0:
                zmax = None
                try:
                    zmax = float(self._obj_status("toolhead")
                                 .get("axis_maximum")[2])
                except Exception:
                    zmax = None
                pos = self._gcode_pos()
                if zmax is not None and pos is not None:
                    hop = max(0.0, min(hop, zmax - pos[2]))
                if hop > 0 and self._move(gcmd, "Z-Hop", [
                        "G91", "G1 Z%s F%s" % (_n(hop), _n(c["z_velocity"])),
                        "G90"]):
                    ch["hop"] = hop
            gcmd.respond_info("[k2_box] Wechsel %s begonnen (allow_write=%s, "
                              "allow_move=%s)" % (ch.get("tnn") or "(ohne TNN)",
                                                  c.get("allow_write"),
                                                  c.get("allow_move")))
        res = self._run_step(gcmd, "PRE_OPT", body, chain_step=False)
        # Restmaterial (Stock cmd_CR_BOX_PRE_OPT Z.5761 bzw. Tn_action
        # Z.4614): Rest ausspülen statt Schnitt + Rückzug
        if self.chain is ch and ch.get("active") and not ch.get("skip") \
                and not self.chain_error:
            self._maybe_ending_purge(gcmd)
        return res

    def _ending_skip(self, gcmd, name):
        """CUT/RETRUDE in einer Kette nach dem Restmaterial-Ausspülen: Kopf
        und Schlauch sind leer -> nichts zu schneiden/zurückzuziehen (Stock
        Tn_action/box_end: last_cmd None, Kopfsensor leer)."""
        if self.chain.get("active") and self.chain.get("ending_purged"):
            gcmd.respond_info("[k2_box] %s übersprungen (Restmaterial "
                              "ausgespült – Kopf und Schlauch leer)" % (name,))
            return True
        if self.ending_material and name == "RETRUDE":
            gcmd.respond_info(
                "[k2_box] WARNUNG Restmaterial-Fall offen (%s): der Rückzug "
                "zieht einen Rest ohne Ende in der Box zurück (ANNAHME: kann "
                "den Hub verstopfen). Stock spült ihn aus – Kette mit "
                "KTWO_BOX_PRE_OPT beginnen bzw. KTWO_BOX_ENDING PURGE=1"
                % (self.ending_material.get("tnn") or "?",))
        return False

    def _empty_load(self, gcmd):
        """A1 (Live-Abgleich 18.10.2026): T<n>-Wechsel bei
        sicher LEEREM Kopf (Kopfsensor False) UND leerem Hub (0x08 [01] = 0,
        lesend) -> Stock lädt nur (live 16:46:26: „filament_sensor false“,
        0x08 [01] -> 0, kein Cut, kein Rückzug; Erst-Laden zu Druckbeginn).
        Nur in einer T<n>-Kette (SOURCE=T, Stock Tn_action) mit beiden
        Freigaben; M8200 C/R, Einzelschritte und Plan/Trockenlauf (zeigen
        die volle Kette) bleiben wie bisher. Kopfsensor unbekannt/Filament,
        Hub belegt oder unlesbar -> False (bisheriges sicheres Verhalten:
        schneiden, zurückziehen). Ergebnis in chain["empty_load"]."""
        ch = self.chain
        if not ch.get("active") or ch.get("skip") or self.chain_error \
                or ch.get("source") != "T" or not self._released():
            return False
        if ch.get("empty_load"):
            return True
        if self._head_filament() is not False:
            return False
        try:
            hub = self._ensure_controller().hub_mask()
        except Exception:
            return False
        if hub != 0:
            return False
        ch["empty_load"] = True
        ctl = self.controller
        if ctl is not None:
            ctl.loaded_slot = None
        gcmd.respond_info("[k2_box] Kopf und Hub leer – nur laden (kein "
                          "Schnitt, kein Rückzug; Stock-Erstladen)")
        return True

    def cmd_cut(self, gcmd):
        if self._ending_skip(gcmd, "CUT"):
            return None
        if self._empty_load(gcmd):
            gcmd.respond_info("[k2_box] CUT übersprungen (Kopf und Hub leer)")
            return None

        def body(gcmd):
            c = self.cfg
            old = self._max_accel()
            cs = c.get("cut_sensor")
            if c.get("allow_move") and cs and self._sensor_state(cs) is False:
                raise BoxChangeError("Schneidhebel-Sensor %s meldet vor dem "
                                     "Schnitt nicht bereit" % (cs,))
            # pessimistisch VOR der Fahrt: scheitert das Skript nach dem
            # Hebelstoß, ist trotzdem geschnitten
            was_cut = self.head_cut
            self.head_cut = True
            try:
                if self._move(gcmd, "Schneiden (CR_BOX_CUT)",
                              gcode_cut(c, old)):
                    self.cut_needed = ""    # nur bei echtem Schnitt (kein Plan)
                else:
                    self.head_cut = was_cut     # Plan/Trockenlauf
            except Exception:
                if c.get("allow_move") and old is not None:
                    try:
                        self._script(["SET_VELOCITY_LIMIT ACCEL=%s" % _n(old)])
                    except Exception as rexc:
                        self._restore_warn("Beschleunigung (SET_VELOCITY_LIMIT)",
                                           rexc)
                raise
            if c.get("allow_move") and cs and self._sensor_state(cs) is False:
                raise BoxChangeError("Schneidhebel-Sensor %s nach dem Schnitt "
                                     "nicht zurück (Stock „cut to return“)"
                                     % (cs,))
            gcmd.respond_info("[k2_box] CUT: Schneidfahrt X%s -> X%s -> X%s%s"
                              % (_n(c["pre_cut_pos_x"]), _n(c["cut_pos_x"]),
                                 _n(c["pre_cut_pos_x"]), self._plan_note()))
        return self._run_step(gcmd, "CUT", body)

    def cmd_go_extrude_pos(self, gcmd):
        def body(gcmd):
            self._go_extrude_pos(gcmd)
        return self._run_step(gcmd, "GO_EXTRUDE_POS", body)

    def cmd_safe_pos(self, gcmd):
        def body(gcmd):
            self._move(gcmd, "Schacht verlassen (BOX_MOVE_TO_SAFE_POS)",
                       gcode_safe_pos(self.cfg))
        return self._run_step(gcmd, "SAFE_POS", body)

    def _retrude_velocity(self):
        """A3 (Live-Abgleich 18.10.2026): Extruder-Rückzug je
        p0 mit der Spülgeschwindigkeit des Düsenmaterials (Stock live
        „extrude = -10.0, velocity: [350.0]“ = flush_velocity base 350 aus
        max_vol 14, 16.10. + 18.10.) -> flush_velocity_base(max_vol,
        Düse). Ohne Material-DB bzw. ohne max_vol: tn_retrude_velocity
        (Stock 600 ohne Materialdaten, 12.10.) -> (mm/min, Begründung)."""
        c = self.cfg
        if self.material_db is not None:
            key = self._nozzle_key()
            mp = self._mat(key)
            if mp.get("vol"):
                return (flush_velocity_base(c, mp["vol"],
                                            self._nozzle_diameter()),
                        "Spülgeschwindigkeit %s (max_vol %s)" % (
                            key, _n(mp["vol"])))
        return float(c["tn_retrude_velocity"]), "tn_retrude_velocity"

    def cmd_retrude(self, gcmd):
        if self._extern_block(gcmd, "KTWO_BOX_RETRUDE (CFS-Rückzug)"):
            return None
        if self._ending_skip(gcmd, "RETRUDE"):
            return None
        if self.chain.get("active") and self.chain.get("empty_load"):
            gcmd.respond_info("[k2_box] RETRUDE übersprungen (Kopf und Hub "
                              "leer – nichts zurückzuziehen)")
            return None
        why = self._cut_missing(gcmd, "RETRUDE")
        if why:
            if self._print_active():
                # im Druck wie jede andere Ablehnung: PAUSE (_fail)
                return self._fail(gcmd, "RETRUDE", why)
            msg = "[k2_box] " + why
            gcmd.respond_info(msg)
            raise gcmd.error(msg) if hasattr(gcmd, "error") else RuntimeError(msg)

        def body(gcmd):
            c = self.cfg
            slot = gcmd.get("SLOT", None)
            length = gcmd.get_float("LENGTH", None, minval=0.)
            steps = retrude_extruder_steps(c, length)
            vel, _vwhy = self._retrude_velocity()
            self._go_extrude_pos(gcmd)
            head_empty = self._head_filament() is False
            if not head_empty:  # Stock: nur mit Filament
                # Material in der Düse (remain_material bzw. geladener Slot)
                self._heat(gcmd, self._change_temp(
                    gcmd, self._nozzle_key() if self.material_db is not None
                    else None, "Rückzug"))
            # Live 26.10.2026: Kopf leer (Faden schon am Sensor vorbei) und
            # Düse kalt (Pause 140 °C) -> die Extruder-Schritte scheiterten mit
            # „Extrude below minimum temp“, der Wechsel brach ab. Ohne Faden
            # im Kopf gibt es für den Extruder nichts zu fördern -> auslassen.
            skip_extr = head_empty and c.get("allow_move") \
                and self._obj_status("extruder").get("can_extrude") is False
            if skip_extr:
                gcmd.respond_info("[k2_box] RETRUDE: Kopf leer und Düse unter "
                                  "der Mindesttemperatur – Extruder-Rückzug "
                                  "ausgelassen (nur Box-Rückzug)")

            def _extr(i):
                if i < len(steps) and not skip_extr:
                    self._move(gcmd, "Extruder-Rückzug %d/%d" % (i + 1, len(steps)),
                               gcode_extruder_move(steps[i], vel) + ["M400"])

            if not c.get("allow_write"):
                self._box_plan(gcmd, "RETRUDE", (
                    "0x08 [01] Hub -> 0x04 [00 01] -> 0x08 [00] -> 0x11 [m 00] "
                    "x%d (nach p0 1-%d je Extruder %s mm F%s) -> 0x11 [m 01]"
                    % (RETRUDE_REPEATS_DEFAULT, len(steps), _n(c["tn_retrude"]),
                       _n(vel))))
                for i in range(len(steps)):
                    _extr(i)
                return
            ctl = self._ensure_controller()
            ctl.wait_box_ready(c["box_wait_timeout"], c["box_wait_interval"])
            if slot is None:
                hub = ctl.hub_mask()
                if hub == 0:
                    ctl.loaded_slot = None
                    gcmd.respond_info("[k2_box] RETRUDE: Hub leer – kein "
                                      "Box-Rückzug nötig")
                    return
                if hub not in SLOT_MASKS:
                    raise BoxChangeError("Hub meldet mehrere Slots (0x%02x)"
                                         % (hub,))
                slot = SLOT_LETTERS[SLOT_MASKS.index(hub)]
            res = ctl.do_retrude(slot, after_p0=_extr)
            self.cut_needed = ""            # Filament ist zurück in der Box
            self.head_cut = False
            for i in range(RETRUDE_REPEATS_DEFAULT, len(steps)):
                _extr(i)
            if self._head_filament() is True:
                gcmd.respond_info("[k2_box] WARNUNG: Kopfsensor meldet nach dem "
                                  "Rückzug noch Filament")
            gcmd.respond_info("[k2_box] RETRUDE Slot %s ok (Hub frei)"
                              % (res.get("slot"),))
        return self._with_head_sensor_off(
            lambda: self._run_step(gcmd, "RETRUDE", body))

    def cmd_break_retrude(self, gcmd):
        """M8200 R E<x> (Stock CR_BOX_RETRUDE LENGTH≠0, Creality Print 7
        vor dem Schnitt). Stock cmd_CR_BOX_RETRUDE (Z.5778–5786): length =
        LENGTH (Default 0); length≠0 -> NUR break_box_extruder_retrude
        (bzw. bei leerem last_cmd nur ein Log „hys: length …“) und Ende –
        KEIN box_retrude_material. break_box_extruder_retrude: Box IDLE,
        0x11 [Maske 00] (bis Buffer), Box IDLE, dann check_and_extrude(
        length, 300) = G91 / G0 E<length> F300 / G90 / M400. Slot = Hub-
        Sensor (Stock: last_cmd = geladenes Tnn); Hub leer -> nur Meldung.
        Keine Kopfbewegung, kein Heizen, Schnittschutz unberührt (der Schnitt
        folgt erst danach)."""
        if self._extern_block(gcmd, "KTWO_BOX_BREAK_RETRUDE (CFS-Rückzug)"):
            return None
        if self._ending_skip(gcmd, "BREAK_RETRUDE"):
            return None

        def body(gcmd):
            c = self.cfg
            length = gcmd.get_float("LENGTH")
            if not length:
                raise ValueError("LENGTH fehlt bzw. 0 – für den vollen "
                                 "Rückzug KTWO_BOX_RETRUDE (= M8200 R)")
            move = gcode_extruder_move(length, BREAK_RETRUDE_VELOCITY) \
                + ["M400"]
            if not c.get("allow_write"):
                self._box_plan(gcmd, "BREAK_RETRUDE", (
                    "0x08 [01] Hub -> 0x04 [00 01] -> 0x11 [m 00] -> "
                    "0x04 [00 01], dann Extruder E%s F%s"
                    % (_n(length), _n(BREAK_RETRUDE_VELOCITY))))
                self._move(gcmd, "Extruder-Rückzug (Stock check_and_extrude)",
                           move)
                return
            cold = self._cold_reason()
            if cold:
                raise BoxChangeError("Extruder kalt: %s" % (cold,))
            ctl = self._ensure_controller()
            ctl.wait_box_ready(c["box_wait_timeout"], c["box_wait_interval"])
            hub = ctl.hub_mask()
            if hub == 0:
                gcmd.respond_info("[k2_box] BREAK_RETRUDE: kein Slot geladen "
                                  "(Hub leer) – nichts zu tun (Stock: leeres "
                                  "last_cmd -> nur Log)")
                return
            if hub not in SLOT_MASKS:
                raise BoxChangeError("Hub meldet mehrere Slots (0x%02x)"
                                     % (hub,))
            slot = SLOT_LETTERS[SLOT_MASKS.index(hub)]
            res = ctl.do_break_retrude(slot)
            self._move(gcmd, "Extruder-Rückzug (Stock check_and_extrude)",
                       move)
            gcmd.respond_info("[k2_box] BREAK_RETRUDE Slot %s: Box bis Buffer "
                              "(0x11 p0), Extruder E%s F%s – Filament bleibt "
                              "im Kopf (Stock break_box_extruder_retrude)"
                              % (res.get("slot"), _n(length),
                                 _n(BREAK_RETRUDE_VELOCITY)))
        return self._with_head_sensor_off(
            lambda: self._run_step(gcmd, "BREAK_RETRUDE", body))

    def cmd_extrude(self, gcmd):
        if self._extern_block(gcmd, "KTWO_BOX_EXTRUDE (CFS-Einzug)",
                              "externe Spule: LOAD_FILAMENT"):
            return None

        def body(gcmd):
            c = self.cfg
            tnn_raw = gcmd.get("TNN", None)
            slot = gcmd.get("SLOT", None)
            if tnn_raw:
                _b, idx, letter, tnn = self._check_tnn(tnn_raw)
            elif slot is not None:
                idx, letter, _m = parse_slot(slot)
                tnn = "T%d%s" % (int(c.get("box_addr", 1)), letter)
            else:
                raise ValueError("TNN=T1A..T1D oder SLOT=A..D fehlt")
            mask = SLOT_MASKS[idx]
            # Ziel-Material nur mit Material-DB nachschlagen (sonst kein
            # zusätzlicher RFID-Lesezugriff, Verhalten wie bisher)
            key = None
            if self.material_db is not None:
                tag = self._slot_tag(letter)
                key = tag[0] if tag else None
            self._go_extrude_pos(gcmd)
            self._heat(gcmd, self._change_temp(gcmd, key, "Einzug %s" % tnn))
            grab = gcode_extruder_move(c["buffer_empty_len"],
                                       self._grab_velocity(key))
            if not c.get("allow_write"):
                self._box_plan(gcmd, "EXTRUDE %s" % tnn, (
                    "0x04 [00 01] -> 0x0f [01] -> 0x08 [00] -> 0x10 [%02x 00 00] "
                    "-> [%02x 04 00] -> [%02x 05 00] alle %s s bis Kopfsensor "
                    "(max %s s) -> [%02x 06 00] -> Stage 7 Extruder +%s mm -> "
                    "[%02x 07 03] -> 0x05 -> 0x04 [%02x 00] -> 0x0f [00] "
                    "(bei 0x0c Stock-Retry: 1x mode 4 neu, dann 04/11 + 1 "
                    "Neuversuch ab mode 0)" % (
                        mask, mask, mask, _n(c["extrude_poll_interval"]),
                        _n(c["extrude_sensor_timeout"]), mask,
                        _n(c["buffer_empty_len"]), mask, mask)))
                self._move(gcmd, "Stage 7: Extruder greift (Trockenlauf)",
                           grab + ["M400"])
                return
            ctl = self._ensure_controller()
            ctl.wait_box_ready(c["box_wait_timeout"], c["box_wait_interval"])
            hub = ctl.hub_mask()
            head = self._head_filament()
            if hub and hub != mask:
                other = (SLOT_LETTERS[SLOT_MASKS.index(hub)]
                         if hub in SLOT_MASKS else "0x%02x" % hub)
                raise BoxChangeError("Hub belegt durch Slot %s – erst "
                                     "KTWO_BOX_RETRUDE (CFS blockiert sonst "
                                     "alle Einzüge)" % (other,))
            if head is True:
                if hub == mask:
                    ctl.loaded_slot = letter
                    gcmd.respond_info("[k2_box] EXTRUDE: %s bereits geladen"
                                      % (tnn,))
                    return
                raise BoxChangeError("Kopfsensor meldet noch Filament – erst "
                                     "KTWO_BOX_CUT + KTWO_BOX_RETRUDE")
            sensor_fn = None
            if head is not None:
                sensor_fn = lambda: self._head_filament() is True  # noqa: E731
            else:
                gcmd.respond_info("[k2_box] WARNUNG: kein Kopfsensor "
                                  "(filament_sensor) – feste %d Odometer-Polls "
                                  "(ANNAHME)" % (ODO_POLLS_DEFAULT,))

            def _stage7():
                # Ohne M400: die Bewegung läuft, während mode 7 prüft.
                self._move(gcmd, "Stage 7: Extruder greift", grab)

            def _retry(kind, exc):
                # Stock-Retry bei 0x0c (do_extrude): vor dem mode-4-Neustart
                # K2-Stock „move to retry“ = Düsenreinigung (temp_pos, Kopf
                # bleibt im Schacht), vor dem Neuversuch nur die Meldung.
                if kind == "restart":
                    gcmd.respond_info(
                        "[k2_box] EXTRUDE %s: %s – Stock-Retry: Düsenreinigung, "
                        "mode 4 einmal neu" % (tnn, self._exc_text(exc)))
                    self._move(gcmd, "Retry-Reinigung (Stock move to retry)",
                               gcode_nozzle_clean(c, False, 0.0,
                                                  self._max_accel()))
                else:
                    gcmd.respond_info(
                        "[k2_box] EXTRUDE %s: erneut %s – Stock-Retry: "
                        "0x04 [00 01] + 0x11 [%02x 01], ein Neuversuch ab "
                        "mode 0" % (tnn, self._exc_text(exc), mask))

            inhibit = self._extrude_inhibit(ctl)
            if not inhibit:
                gcmd.respond_info("[k2_box] EXTRUDE %s: Box-Druckzustand aktiv "
                                  "– ohne 0x0f-Umschaltung (Stock)" % (tnn,))
            res = ctl.do_extrude(
                letter, sensor_fn=sensor_fn,
                sensor_timeout=c["extrude_sensor_timeout"],
                poll_interval=c["extrude_poll_interval"],
                stage7_fn=_stage7, stage7_delay=c["stage7_delay"],
                retry_fn=_retry, inhibit=inhibit)
            self._move(gcmd, "Bewegungen abwarten", ["M400"], wrap=False)
            self._remember_tag(ctl, letter)
            # frisch geladenes Ende (nur Stage 7 in der Düse): Rückzug ohne
            # Schnitt geht (live 22.10.2026, 9.3 alle Slots) -> Schutz frei
            self.cut_needed = ""
            self.head_cut = False
            if self.ending_material:
                # neues Filament bis zum Kopf: der alte Rest ist durch
                self._ending_clear("Einzug %s erfolgreich" % (tnn,))
            odo = res.get("odo_mm") or []
            gcmd.respond_info("[k2_box] EXTRUDE %s ok (Odometer %s mm, %d Polls)"
                              % (tnn, round(odo[-1], 1) if odo else "?",
                                 len(odo)))
        return self._run_step(gcmd, "EXTRUDE", body)

    # --- Spülen wie Stock (17.10.2026) ----------------------------------
    # Stock MCMBW.material_change_flush (Z.4494–4517): M106 P0 S50 ->
    # Spülmenge get_flush_len -> Geschwindigkeit get_flush_velocity ->
    # Temperaturen get_flush_temp/get_flush_max_temp -> quickly_wait_heating
    # -> Tn_Extrude: je Abschnitt (cal_flush_list) Extrusion 20 %/80 % ->
    # blow -> nozzle_clean(circle=False), letzter Abschnitt nozzle_clean()
    # mit Kreisen -> Messrad-Prüfung (key845) -> remain_material.save.
    def _flush_target(self, gcmd):
        """Ziel-Slot des Spülens: TNN= oder zuletzt geladener Slot."""
        raw = gcmd.get("TNN", None)
        if raw:
            _b, _idx, letter, tnn = self._check_tnn(raw)
            return letter, tnn
        ctl = self.controller
        letter = ctl.loaded_slot if ctl is not None else None
        if letter:
            return letter, "T%d%s" % (int(self.cfg.get("box_addr", 1)), letter)
        return None, ""

    def _tool_param(self, gcmd, tnn):
        """TOOL=<n> (Slicer-Werkzeug aus T<n>/M8200 L I<n>) -> int oder
        None; muss zu TNN passen (feste Zuordnung tnn_from_index)."""
        raw = gcmd.get("TOOL", None)
        if raw is None or str(raw).strip() == "":
            return None
        tool_tnn = tnn_from_index(raw)          # ValueError bei 0..15-Verstoß
        tool = int(str(raw).strip().lstrip("="))
        if tnn and tool_tnn != tnn:
            raise BoxChangeError("TOOL=%d (= %s) passt nicht zu TNN=%s"
                                 % (tool, tool_tnn, tnn))
        return tool

    @staticmethod
    def _tool_of_tnn(tnn):
        """Feste Zuordnung TNN -> Werkzeug (T1A=0 … T4D=15), None = ?"""
        if not tnn:
            return None
        try:
            box, idx, _l, _t = parse_tnn(tnn)
        except ValueError:
            return None
        return (box - 1) * 4 + idx

    def _flush_matrix_info(self):
        """Matrix der laufenden Druckdatei (Cache Pfad/Größe/mtime) -> dict;
        ValueError mit Grund, wenn nicht nutzbar."""
        vsd = self._lookup("virtual_sdcard")
        path = None
        if vsd is not None:
            try:
                path = vsd.file_path()
            except Exception:
                path = self._obj_status("virtual_sdcard").get("file_path")
        if not path:
            raise ValueError("keine laufende Druckdatei (virtual_sdcard)")
        try:
            stt = os.stat(path)
        except OSError as exc:
            raise ValueError("Druckdatei nicht lesbar (%s)"
                             % exc.__class__.__name__)
        key = (path, stt.st_size, stt.st_mtime)
        cache = self._matrix_cache
        if cache is None or cache[0] != key:
            try:
                res = read_flush_matrix(path)
            except (OSError, ValueError) as exc:
                res = str(exc) or exc.__class__.__name__
            cache = self._matrix_cache = (key, res)
        if isinstance(cache[1], str):
            raise ValueError(cache[1])
        return cache[1]

    def _flush_matrix(self, gcmd, tnn, length, notes):
        """flush_mode matrix: Rohlänge aus der Slicer-Matrix -> dict
        {len, why, tool} oder None (Rückfall Farbweg, Meldung in notes).
        Wie Stock (BW Z.4429) nur bei print_stats 'printing' und bekanntem
        alten Werkzeug; Index direkt aus TOOL= (sonst feste TNN-Zuordnung).
        E5/A4: altes Werkzeug nur aus einem Spülen in DIESEM Druck – der
        erste Einzug eines Drucks nimmt wie Stock den Farbweg (Hinweis,
        keine Warnung; Quelle = Düsen-Material aus remain_material)."""
        if self.cfg.get("flush_mode") != "matrix" or length is not None:
            return None
        state = self._obj_status("print_stats").get("state")
        if state != "printing":
            notes.append("Spülen: Slicer-Matrix nur im laufenden Druck "
                         "(Zustand %s) – Farbweg" % (state or "unbekannt"))
            return None
        old_tnn, why_unknown = self._print_old_tnn()
        if not old_tnn:
            notes.append("Spülen: %s – altes Werkzeug in diesem Druck "
                         "unbekannt -> Farbweg (= Stock, last_tnn None)"
                         % why_unknown)
            return None
        try:
            new_idx = self._tool_param(gcmd, tnn)
            if new_idx is None:
                new_idx = self._tool_of_tnn(tnn)
            old_idx = self._tool_of_tnn(old_tnn)
            if new_idx is None:
                raise ValueError("Ziel-Werkzeug unbekannt (TOOL=/TNN= fehlt)")
            if old_idx is None:
                raise ValueError("altes Werkzeug unbekannt (Düsen-Material "
                                 "ohne Slot)")
            info = self._flush_matrix_info()
            ln, why = slicer_matrix_flush(info, old_idx, new_idx)
        except (ValueError, BoxChangeError) as exc:
            msg = ("WARNUNG Slicer-Matrix nicht nutzbar (%s) – Rückfall "
                   "Farbweg" % (self._exc_text(exc),))
            notes.append(msg)
            try:
                logging.warning("[k2_box] %s" % msg)
            except Exception:
                pass
            return None
        return {"len": ln, "why": why, "tool": new_idx}

    def _flush_plan(self, gcmd, letter, length, velocity, tnn="", extern=None):
        """Spül-Plan (rein rechnend + RFID lesen) -> dict.

        extern (19.10.2026, externe Spule, KTWO_BOX_EXTERN_FLUSH): dict
        {key, col, temp, override} des externen Materials statt eines CFS-
        Slots. Menge/Abschnitte/maxTemp-Stufe DIESELBE Stock-Rechnung wie
        beim CFS-Wechsel (plan_flush_lengths Farbweg, Vorgänger =
        remain_material; live externe Spule 19.10.2026: (V+183)/2,4
        -> [90, 70], 240 °C dann 220 °C), Geschwindigkeit wie Stock
        FILAMENT_RACK_FLUSH (s. _flush_plan_extern), keine Slicer-Matrix,
        Temperatur des neuen Materials = extern["temp"] (Handwert TEMP= oder
        Material-DB, schon geprüft), Basis 0 (kein Druck-Ziel)."""
        c = self.cfg
        db = self.material_db
        notes = []
        if extern is not None:
            new_key, new_col = extern.get("key"), extern.get("col")
        else:
            new_tag = self._slot_tag(letter) if (letter and (
                c["flush_mode"] in ("color", "matrix") or db is not None)) \
                else None
            new_key = new_tag[0] if new_tag else None
            new_col = new_tag[1] if new_tag else None
        old_key = self.remain_material.get("type")
        old_col = self.remain_material.get("color")
        new_p, old_p = self._mat(new_key), self._mat(old_key)
        different = False
        if db is not None and old_key and new_key:
            try:
                different = bool(db.is_different_type(old_key, new_key))
            except Exception:
                different = False
        mx = None if extern is not None else self._flush_matrix(
            gcmd, tnn, length, notes)
        segs, why = plan_flush_lengths(c, length, old_col, new_col, different,
                                       matrix=mx)
        if extern is not None:
            return self._flush_plan_extern(
                c, db, notes, segs, why, new_key, new_col, new_p, old_key,
                old_p, different, extern)
        # Geschwindigkeit (Stock get_flush_velocity, Material = neu)
        if velocity is not None:
            v = float(velocity)
            vwhy = "VELOCITY="
        else:
            vol = new_p.get("vol") if db is not None else None
            v = flush_velocity_base(c, vol, self._nozzle_diameter())
            vwhy = ("min(360, %s/2,4·60)" % _n(vol)) if vol else (
                "Tn_extrude_velocity, max 360")
            if db is not None and not vol:
                notes.append("Spülen: Geschwindigkeit des Materials %s "
                             "unbekannt – %s mm/min (Stock-Fallback)"
                             % (new_key or "(ohne Etikett)", _n(v)))
        # Temperaturen (Stock get_flush_temp/get_flush_max_temp, Z.4507)
        t_param = gcmd.get_float("TEMP", None)
        if t_param is not None:
            temp = max_temp = float(t_param)
            twhy = "TEMP= (fest, ohne maxTemp-Stufe)"
        else:
            base = self._base_temp(gcmd)
            t_old = self._db_temp(old_p, "Spülen alt", notes)
            t_new = self._db_temp(new_p, "Spülen neu", notes)
            temp = max(base, t_old, t_new)
            max_temp = temp
            if db is not None and t_old >= t_new:
                mx_old = self._db_temp(old_p, "Spülen alt", [], "max_temp")
                mx_new = self._db_temp(new_p, "Spülen neu", [], "max_temp")
                max_temp = max(temp, mx_old, mx_new)
            twhy = ("max(Ziel %s, alt %s, neu %s)%s" % (
                _n(base), _n(t_old), _n(t_new),
                (", Spitze maxTemp %s (alt >= neu)" % _n(max_temp))
                if max_temp > temp + 0.01 else ""))
        return {"segs": segs, "why": why, "v": v, "vwhy": vwhy,
                "temp": temp, "max_temp": max_temp, "twhy": twhy,
                "new_key": new_key, "new_col": new_col, "different": different,
                "notes": notes}

    def _flush_plan_extern(self, c, db, notes, segs, why, new_key, new_col,
                           new_p, old_key, old_p, different, extern):
        """Temperatur/Geschwindigkeit des externen Spülens (s. _flush_plan).

        Neu = extern["temp"]. Alt (Düsen-Material, remain_material): gleiches
        Material wie extern -> dieselbe Temperatur (ein Handwert gilt auch für
        den Rest in der Düse), sonst Material-DB bzw. Tn_extrude_temp (Stock-
        Fallback). Spültemperatur max(alt, neu); maxTemp-Stufe wie beim CFS
        (nur mit DB und alt >= neu): neu = DB-maxTemp (sonst neu), alt = DB-
        maxTemp (bei gleichem Material wie neu). Geschwindigkeit Stock
        FILAMENT_RACK_FLUSH: max_vol/2,4·60 des neuen Materials ohne 360-
        Grenze (live 575), ohne Wert 360."""
        t_new = float(extern["temp"])
        same = bool(new_key and old_key and same_material_key(old_key,
                                                              new_key))
        t_old = t_new if same else self._db_temp(old_p, "Spülen alt (Düse)",
                                                 notes)
        temp = max(t_old, t_new)
        max_temp = temp
        if db is not None and t_old >= t_new:
            mx_new = t_new
            if new_p.get("max_known"):
                mx_new = max(t_new, self._db_temp(new_p, "Spülen neu", [],
                                                  "max_temp"))
            mx_old = mx_new if same else self._db_temp(old_p, "Spülen alt",
                                                       [], "max_temp")
            max_temp = max(temp, mx_old, mx_new)
        # Geschwindigkeit: Stock FILAMENT_RACK_FLUSH nimmt get_material_
        # target_speed = max_vol/2,4·60 OHNE 360-Grenze und ohne Düsen-
        # Skalierung (Stock filament_rack; live extern 18 @ 575, 72 @
        # 690 = ×1,2, Rest @ 575 bei max_vol 23) – anders als der CFS-Wechsel (get_flush_velocity, min 360);
        # ohne Wert [filament_rack] velocity 360 (ohne ·60).
        vol = new_p.get("vol") if db is not None else None
        if vol:
            v = float(vol) / 2.4 * 60.0
            vwhy = ("%s/2,4·60 ohne 360-Grenze wie Stock FILAMENT_RACK_FLUSH"
                    % _n(vol))
        else:
            v = PRIME_FALLBACK_VELOCITY
            vwhy = "[filament_rack] velocity 360 (Stock-Fallback)"
            if db is not None:
                notes.append("Spülen: Geschwindigkeit des Materials %s "
                             "unbekannt – %s mm/min (Stock-Fallback)" % (
                                 new_key or "(ohne ID)", _n(v)))
        twhy = ("max(alt %s, neu %s = %s)%s" % (
            _n(t_old), _n(t_new), extern.get("twhy") or "extern",
            (", Spitze maxTemp %s (alt >= neu)" % _n(max_temp))
            if max_temp > temp + 0.01 else ""))
        return {"segs": segs, "why": why, "v": v, "vwhy": vwhy,
                "temp": temp, "max_temp": max_temp, "twhy": twhy,
                "new_key": new_key, "new_col": new_col, "different": different,
                "notes": notes}

    def cmd_flush(self, gcmd):
        if self._extern_block(gcmd, "KTWO_BOX_FLUSH (CFS-Spülen)",
                              "externe Spule: LOAD_FILAMENT"):
            return None

        def body(gcmd):
            length = gcmd.get_float("LENGTH", None, minval=0.)
            vel = gcmd.get_float("VELOCITY", None, above=0.)
            letter, tnn = self._flush_target(gcmd)
            ch = self.chain
            if length is None and tnn and tnn == self._m8200_offen \
                    and ch.get("active") and ch.get("source") == "T" \
                    and self._is_paused() and self._print_active():
                # Live 26.10.2026: T<n> in der Pause mitten im M8200-Block –
                # nach RESUME spült die Datei selbst (G1 E40 + 90/70/70 mm im
                # Schacht), unser Spülen (160 mm) war doppelt
                gcmd.respond_info(
                    "[k2_box] FLUSH %s ausgelassen: die Datei steht noch im "
                    "M8200-Wechselblock und spült nach RESUME selbst im "
                    "Schacht" % (tnn,))
                return
            self._flush_exec(gcmd, letter, tnn, length, vel)
        return self._run_step(gcmd, "FLUSH", body)

    def _flush_exec(self, gcmd, letter, tnn, length, vel, extern=None):
        """Spülen ausführen (cmd_flush bzw. KTWO_BOX_EXTERN_FLUSH, extern =
        dict des externen Materials, s. _flush_plan): Plan, Schacht, Lüfter,
        Heizen, Abschnitte, Blasen, Reinigung, remain_material."""
        c = self.cfg
        pl = self._flush_plan(gcmd, letter, length, vel, tnn, extern=extern)
        label = tnn or ("extern" if extern is not None
                        else "(Ziel unbekannt)")
        segs, v, temp, max_temp = (pl["segs"], pl["v"], pl["temp"],
                                   pl["max_temp"])
        n = len(segs)
        factor = float(c["flush_velocity_factor"])
        legacy = c["flush_clean"] == "legacy"
        self._respond_notes(gcmd, pl["notes"])
        gcmd.respond_info(
            "[k2_box] FLUSH-Plan %s: %s mm (%s; %s), F%s (%s, 80 %%-Teil "
            "×%s außer dem letzten), %s °C (%s)" % (
                label, _n(sum(segs)),
                " + ".join(_n(x) for x in segs) or "-", pl["why"], _n(v),
                pl["vwhy"], _n(factor), _n(temp), pl["twhy"]))
        if not segs:
            return
        self._go_extrude_pos(gcmd)
        blow_fan = c.get("flush_blow_fan") if c.get("flush_blow") else ""
        fan = self._pin(blow_fan) if blow_fan else None
        if blow_fan and fan is None and c.get("allow_move"):
            gcmd.respond_info("[k2_box] FLUSH: Blasen übersprungen – "
                              "[output_pin %s] fehlt" % (blow_fan,))
        fan_en = self._pin(blow_fan + "_en") if fan is not None else None
        if fan is not None and c["flush_fan_start"] > 0:
            self._move(gcmd, "Bauteillüfter (Stock M106 P0 S50)",
                       ["M106 P0 S%s" % _n(c["flush_fan_start"]), "M400"],
                       wrap=False)
        self._heat(gcmd, temp)
        old_accel = self._max_accel()
        old_mcr = self._min_cruise_ratio()
        arcs = self._lookup("gcode_arcs") is not None
        # Messrad der CFS bewegt sich beim externen Spülen nicht -> keine
        # Verstopfungsprüfung (sonst immer Warnung; extern kein Box-Frame)
        clog = (c.get("flush_clog_check") and c.get("allow_move")
                and c.get("allow_write") and extern is None)
        wheel_sum, wheel_last, ran = 0.0, None, False
        if clog:
            try:
                wheel_last = float(self._ensure_controller().get_wheel())
            except Exception as exc:
                clog = False
                gcmd.respond_info("[k2_box] FLUSH: Messrad nicht lesbar "
                                  "(%s) – keine Verstopfungsprüfung"
                                  % (self._exc_text(exc),))
        try:
            for i, seg in enumerate(segs):
                last = i == n - 1
                # C2: Druckabbruch -> nach dem laufenden Abschnitt Schluss
                self._check_cancel("FLUSH vor Abschnitt %d/%d" % (i + 1, n))
                ran |= bool(self._move(
                    gcmd, "Spülen %d/%d (%s mm)" % (i + 1, n, _n(seg)),
                    flush_segment_lines(c, seg, i, n, v, temp, max_temp)
                    + ["M400"]))
                if ran:
                    # Schnittschutz (auch bei späterem Abbruch): das Ende
                    # steht geschmolzen in der Düse -> Einzel-RETRUDE/UNLOAD
                    # erst nach KTWO_BOX_CUT (_cut_missing)
                    self.cut_needed = "dem Spülen %s" % (label,)
                if clog:
                    cur = float(self._ensure_controller().get_wheel())
                    wheel_sum += cur - wheel_last
                    wheel_last = cur
                if fan is not None:
                    # Stock: alter Wert = fan0.last_value · scale
                    # (nach M106 P0 S50), voll = scale (Pi 255)
                    cur = self._pin(blow_fan) or fan
                    sc = fan[1] or 255.0
                    self._move(gcmd, "Blasen %d/%d (Stock blow)" % (i + 1, n),
                               gcode_blow(blow_fan, c["flush_blow_time"],
                                          cur[0] * sc, sc), wrap=False)
                if legacy:
                    self._move(gcmd, "Abstreifen %d/%d" % (i + 1, n),
                               gcode_wipe(c, old_accel))
                    continue
                off = 0.0
                if last and c["flush_wipe_offset"] > 0:
                    # Stock clean_offset_pos = reactor.monotonic() % 3
                    off = self._now() % float(c["flush_wipe_offset"])
                self._move(gcmd, "Reinigung %d/%d (%s)" % (
                    i + 1, n, "Blech + Silikon-Kreise, Versatz %s mm"
                    % _n(off) if last else "Blech, bleibt im Schacht"),
                    gcode_nozzle_clean(c, last, off, old_accel, old_mcr,
                                       arcs))
            if max_temp > temp + 0.01 and n == 1:
                # nur ein Abschnitt: Stock stellt nie zurück -> hier wie
                # bei mehreren Abschnitten auf die Spültemperatur
                self._move(gcmd, "Spültemperatur zurück",
                           ["M104 S%s" % _n(temp)], wrap=False)
        except Exception:
            if c.get("allow_move") and old_accel is not None:
                try:
                    self._script([_velocity_limit(old_accel, old_mcr)])
                except Exception as rexc:
                    self._restore_warn("Beschleunigung (SET_VELOCITY_LIMIT)", rexc)
            raise
        finally:
            if fan is not None and c.get("allow_move"):
                # Stock material_flush -> box_save.restore_fan
                lines = []
                if fan_en is not None:
                    lines.append("SET_PIN PIN=%s VALUE=%s" % (
                        blow_fan + "_en", _n(fan_en[0] * (fan_en[1] or 1.0))))
                lines.append("SET_PIN PIN=%s VALUE=%s" % (
                    blow_fan, _n(fan[0] * (fan[1] or 255.0))))
                try:
                    self._move(gcmd, "Bauteillüfter zurück", lines,
                               wrap=False)
                except Exception as rexc:
                    self._restore_warn("Bauteillüfter", rexc)
        if clog and ran and abs(wheel_sum) < abs(c["flush_clog_min_mm"]):
            gcmd.respond_info(
                "[k2_box] WARNUNG FLUSH: Messrad bewegte sich nur %s mm "
                "(< %s mm) – Düse verstopft? (Stock key845 „the nozzle is "
                "blocked“; hier nur Warnung, flush_clog_check)" % (
                    _n(wheel_sum), _n(c["flush_clog_min_mm"])))
        if ran:
            # Stock remain_material.save(color, type) nach Erfolg –
            # NUR hier (nicht bei Cut/Rückzug/Laden/Cancel), Datei nur
            # bei Änderung (remain_material_file, atomar)
            self.remain_material = {"type": pl["new_key"],
                                    "color": pl["new_col"], "tnn": tnn}
            self._persist_remain(gcmd)
            # E5/A4: im Druck gespült -> nächster Wechsel kennt das
            # alte Werkzeug (Matrix); außerhalb eines Drucks nicht
            if self._print_active() and tnn:
                self._print_flush_tnn = tnn
                self._print_flush_ident = self._print_ident()
        self.flush_last = ("%s: %s mm (%s), F%s, %s °C" % (
            tnn or ("extern" if extern is not None else "?"), _n(sum(segs)), " + ".join(_n(x) for x in segs),
            _n(v), _n(temp)))
        gcmd.respond_info("[k2_box] FLUSH %s mm (%s) mit F%s ok%s" % (
            _n(sum(segs)), " + ".join(_n(x) for x in segs), _n(v),
            self._plan_note()))

    def cmd_wipe(self, gcmd):
        def body(gcmd):
            self._go_extrude_pos(gcmd)
            self._move(gcmd, "Abstreifen", gcode_wipe(self.cfg, self._max_accel()))
        return self._run_step(gcmd, "WIPE", body)

    def cmd_waste(self, gcmd):
        def body(gcmd):
            gcmd.respond_info("[k2_box] WASTE: Abfallschacht-Prüfung (Stock "
                              "CR_BOX_WASTE, Kamera/[load_ai]) gibt es auf dem "
                              "Pi nicht – übersprungen")
        return self._run_step(gcmd, "WASTE", body, need_release=False)

    def cmd_end_opt(self, gcmd):
        ch = self.chain
        # Grenzen der M8200-Kette zurück; eigene Fahrten (Schacht verlassen)
        # weitet _move je Schritt selbst auf
        self._chain_limits_release()
        if (not ch.get("active") or self._m8200_chain(ch)) \
                and not self._is_paused():
            self._m8200_offen = ""          # M8200 O: Block der Datei zu Ende
        if not ch.get("active"):
            gcmd.respond_info("[k2_box] END_OPT: kein Wechsel aktiv")
            return
        ret = gcmd.get_int("RETURN", 1)
        if ch.get("skip"):
            gcmd.respond_info("[k2_box] END_OPT: %s – nichts zu tun"
                              % (ch.get("skip_reason"),))
        elif self.chain_error:
            gcmd.respond_info("[k2_box] END_OPT: Wechsel nach Fehler beendet "
                              "(keine Bewegung) – %s" % (self.chain_error,))
        else:
            try:
                c = self.cfg
                pos = self._gcode_pos()
                if pos is not None and pos[1] > c["safe_pos_y"] + 0.5:
                    self._move(gcmd, "Schacht verlassen", gcode_safe_pos(c))
                sp = ch.get("start_pos")
                if ret and sp is not None:
                    lines = ["G90", "G0 X%s Y%s F%s" % (
                        _n(sp[0]), _n(sp[1]), _n(c["travel_velocity"]))]
                    cur = self._gcode_pos()
                    if self._is_paused() and cur is not None \
                            and float(sp[2]) < cur[2] + 0.001:
                        # In der Pause senkt nur RESUME (X/Y in Pausenhöhe,
                        # dann Z senkrecht) – sonst stünde die Düse auf
                        # Druckhöhe über dem Objekt und RESUME führe von dort
                        # quer zum Schacht.
                        gcmd.respond_info(
                            "[k2_box] END_OPT: Druck pausiert – Z bleibt auf "
                            "%s (Start-Z %s senkt RESUME)" % (_n(cur[2]),
                                                             _n(sp[2])))
                    else:
                        lines.append("G0 Z%s F%s" % (_n(sp[2]),
                                                     _n(c["z_velocity"])))
                    self._move(gcmd, "Rückfahrt zur Startposition", lines)
                cur_t = self._extruder_target()
                if ch.get("temp_changed") and ch.get("orig_target") is not None \
                        and not ret and ch.get("last_heat") is not None \
                        and cur_t is not None \
                        and abs(cur_t - ch["last_heat"]) > 0.5:
                    # M8200 O (RETURN=0): die Datei hat nach unserem letzten
                    # Heizen selbst eine Temperatur gesetzt (Creality Print 7:
                    # M104 S240 vor „M8200 L“, M104 S220 nach dem eigenen
                    # Spülen) – die gilt, kein Zurücksetzen auf das alte Ziel
                    if abs(cur_t - ch["orig_target"]) > 0.5:
                        gcmd.respond_info(
                            "[k2_box] END_OPT: Zieltemperatur %s °C aus der "
                            "Datei bleibt (nicht zurück auf %s °C)"
                            % (_n(cur_t), _n(ch["orig_target"])))
                elif ch.get("temp_changed") \
                        and ch.get("orig_target") is not None:
                    self._move(gcmd, "Zieltemperatur zurück",
                               ["M104 S%s" % _n(ch["orig_target"])], wrap=False)
                self._keep_start_pos = None
                gcmd.respond_info("[k2_box] Wechsel %s abgeschlossen%s"
                                  % (ch.get("tnn") or "", self._plan_note()))
            except Exception as exc:
                self._fail(gcmd, "END_OPT", self._exc_text(exc))
        if self.chain.get("active"):
            self._chain_cleanup(gcmd)

    def cmd_wait(self, gcmd):
        def body(gcmd):
            c = self.cfg
            timeout = gcmd.get_float("TIMEOUT", c["box_wait_timeout"], above=0.)
            ctl = self._ensure_controller()
            st = ctl.wait_box_ready(timeout, c["box_wait_interval"])
            gcmd.respond_info("[k2_box] Box bereit: %s" % (st.get("state_name"),))
        return self._run_step(gcmd, "WAIT", body, need_release=False)

    def cmd_error_clear(self, gcmd):
        old = self.chain_error
        self.chain_error = ""
        self._chain_error_step = ""
        msg = "[k2_box] Wechsel-Fehler quittiert" + (" (%s)" % old if old else "")
        if self.source == SOURCE_EXTERN:
            msg += "; kein Box-Frame (Quelle EXTERN – 0x04 unterdrückt)"
        elif self.cfg.get("allow_write"):
            try:
                ctl = self._ensure_controller()
                ctl.action.communication_set_box_mode_feed()  # 0x04 [00 01]
                msg += "; Box 0x04 [00 01] IDLE (löscht Box-ERROR, live belegt)"
            except Exception as exc:
                msg += "; Box nicht erreichbar: %s" % (exc,)
        # HEAD_LOADED=1: Bestätigung von Hand, dass der Kopf geladen ist –
        # NUR wirksam, wenn der Kopfsensor keinen Zustand liefert (nicht
        # konfiguriert/fehlt). Meldet er "leer", hat der Sensor Vorrang.
        if gcmd.get_int("HEAD_LOADED", 0) and self.aborted:
            head = self._head_filament()
            if head is False:
                msg += ("; HEAD_LOADED abgelehnt: Kopfsensor %s meldet KEIN "
                        "Filament (Sensor hat Vorrang) – erst laden (T<n>)"
                        % (self.cfg.get("filament_sensor"),))
            else:
                self.aborted = None
                self.head_cut = False
                msg += ("; Kopf als geladen bestätigt (HEAD_LOADED=1, "
                        "Kopfsensor %s) – RESUME prüft den Kopf nach diesem "
                        "Abbruch nicht mehr" % (
                            "unbekannt" if head is None else "meldet Filament",))
        elif self.aborted:
            msg += ("; RESUME prüft den Kopf-Filamentsensor (abgebrochener "
                    "Wechsel %s): leer = gesperrt%s" % (
                        self.aborted.get("tnn") or "ohne Ziel-TNN",
                        "; der Faden im Kopf ist abgeschnitten – auch mit "
                        "Filament am Sensor gesperrt, bis zurückgezogen bzw. "
                        "neu geladen" if self.head_cut else ""))
        gcmd.respond_info(msg)

    def cmd_resume_fix(self, gcmd):
        # CLEAR=1 (aus dem PAUSE-Makro bei JEDER neuen Pause): ein Rückfahrziel
        # mit Z (PAUSE CFS_RETURN_Z) ersetzt die Z-Korrektur; zugleich keine
        # Altlast aus einer per CANCEL beendeten Pause (sonst führe ein
        # späteres RESUME auf ein fremdes Z).
        if gcmd.get_int("CLEAR", 0):
            if self.pending_resume_z is not None:
                gcmd.respond_info("[k2_box] RESUME_FIX: Z-Korrektur auf %s "
                                  "verworfen (Rückfahrziel der Pause enthält "
                                  "Z bzw. neue Pause)"
                                  % (_n(self.pending_resume_z),))
            self.pending_resume_z = None
            # Neue Pause, die NICHT aus _fail stammt (Hand-PAUSE, Runout,
            # neuer Druck nach CANCEL): ein alter Abbruch-Merker ist veraltet
            if self.aborted and not self._pause_from_fail:
                self.aborted = None
            return
        # Ohne CLEAR = Ende eines RESUME (nach RESUME_BASE): der Abbruch ist
        # erledigt (RESUME hatte den Kopf geprüft bzw. nachgeholt)
        self.aborted = None
        self._m8200_wieder_oeffnen(gcmd)
        z = self.pending_resume_z
        if z is None:
            return
        self.pending_resume_z = None
        try:
            self._move(gcmd, "Z nach abgebrochenem Wechsel zurück",
                       ["G90", "G1 Z%s F%s" % (_n(z), _n(self.cfg["z_velocity"]))])
        except Exception as exc:
            raise gcmd.error("[k2_box] RESUME_FIX: %s" % (exc,))

    # =================================================================
    # RESUME nach abgebrochenem Wechsel: Kopfsensor prüfen / nachholen
    # (17.10.2026 spät, README-k2_box.md §11)
    # =================================================================
    # Stock (box_wrapper.so error_resume_process @0x7cd28): CFS an,
    # pausiert, resume_tnn gesetzt, Kopfsensor LEER -> go_to_extrude_pos,
    # box_retrude_material, box_extrude_material, extruder_extrude,
    # material_change_flush; Fehler -> resume_err. Kopfsensor belegt ->
    # nichts. Pi: Kopf leer -> RESUME GESPERRT (Default); mit
    # resume_redo_change + allow_write + allow_move + MOVE=1 holt die
    # vorhandene Kette den Wechsel nach (PRE_OPT Z_HOP=0 -> Schacht/Heizen
    # -> RETRUDE (nur wenn der Hub belegt ist) -> EXTRUDE -> WASTE -> FLUSH
    # -> END_OPT RETURN=0); jeder Fehler -> chain_error, RESUME gesperrt,
    # Druck bleibt pausiert, keine Rückfahrt.
    def _abort_text(self, ab):
        tnn = ab.get("tnn") or ""
        tool = self._tool_of(tnn) if tnn else ""
        return "%s fehlgeschlagen%s" % (
            ab.get("step") or "?",
            ("; Ziel %s = %s" % (tnn, tool)) if tnn else "; Ziel-TNN unbekannt")

    def _resume_block(self, gcmd, ab, head_txt, todo, why=""):
        msg = ("RESUME gesperrt nach abgebrochenem CFS-Wechsel (%s): %s. "
               "Druck bleibt PAUSIERT. %s%s" % (
                   self._abort_text(ab), head_txt, todo,
                   (" Kein automatisches Nachholen: %s." % (why,))
                   if why else ""))
        raise gcmd.error(msg)

    def _redo_block_reason(self, gcmd, ab):
        """None = Nachholen erlaubt, sonst Klartext, warum nicht."""
        c = self.cfg
        if self.source == SOURCE_EXTERN:
            return ("Quelle EXTERN – kein CFS-Nachholen (Filament der externen "
                    "Spule von Hand laden: LOAD_FILAMENT)")
        if not c.get("resume_redo_change"):
            return "aus ([k2_box] resume_redo_change: False)"
        if not self._released():
            return ("CFS-Wechsel nicht freigegeben ([k2_box] allow_write=%s, "
                    "allow_move=%s)" % (c.get("allow_write"),
                                        c.get("allow_move")))
        if not gcmd.get_int("MOVE", 0):
            return ("RESUME darf nicht fahren (_PI_FREIGABE allow_move, "
                    "Referenz X/Y/Z oder Pausenposition fehlt)")
        if ab.get("source") != "T":
            return ("Abbruch stammt nicht aus einem T<n>-Wechsel (M8200-Kette, "
                    "Runout oder Einzelschritt) – Stock holt nur T<n>-Wechsel "
                    "nach")
        if not ab.get("tnn"):
            return "Ziel-TNN des abgebrochenen Wechsels unbekannt"
        if self.chain.get("active"):
            return "Wechselkette noch offen (%s)" % (
                self.chain.get("tnn") or "ohne TNN",)
        if not self._homed_ok():
            return "X/Y/Z nicht referenziert"
        return None

    def cmd_resume_check(self, gcmd):
        """Aus dem RESUME-Makro (nur pausiert, nur bei change_aborted), VOR
        jeder Bewegung: Kopfsensor meldet Filament -> frei; leer -> gesperrt
        bzw. Nachholen (resume_redo_change); unbekannt -> gesperrt, bis
        KTWO_BOX_ERROR_CLEAR HEAD_LOADED=1. MOVE=1: RESUME darf danach
        fahren (macros.cfg). Fehler = gcmd.error -> RESUME bricht ab."""
        ab = self.aborted
        if not ab:
            gcmd.respond_info("[k2_box] RESUME-Prüfung: kein abgebrochener "
                              "Wechsel offen")
            return
        if not self._is_paused():
            gcmd.respond_info("[k2_box] RESUME-Prüfung: nicht pausiert – "
                              "nichts zu prüfen")
            return
        if self.chain_error:
            raise gcmd.error(
                "RESUME gesperrt – CFS-Wechsel fehlgeschlagen: %s. Ursache "
                "beheben, KTWO_BOX_ERROR_CLEAR, Wechsel (T<n>) bzw. Schritt "
                "von Hand wiederholen, dann RESUME." % (self.chain_error,))
        sens = self.cfg.get("filament_sensor") or "(nicht konfiguriert)"
        tnn = ab.get("tnn") or ""
        tool = self._tool_of(tnn) if tnn else ""
        load = ("%s (lädt %s)" % (tool, tnn) if tool
                else "T<n> des gewünschten Werkzeugs")
        if ab.get("source") == EXTERN_CHAIN or self.source == SOURCE_EXTERN:
            load = ("LOAD_FILAMENT (Filament der externen Spule bis zur LED am "
                    "Druckkopf schieben)")
        head = self._head_filament()
        # Live 26.10.2026: Box im Wechsel nach dem Schnitt stromlos -> der
        # Rückzug scheitert, der Kopfsensor meldet weiter den abgeschnittenen
        # Faden. RESUME druckte damit ohne Förderung weiter (Stock prüft das
        # nicht) -> wie „Kopf leer“ behandeln.
        cut_strand = head is True and self.head_cut
        if head is True and not cut_strand:
            gcmd.respond_info(
                "[k2_box] RESUME-Prüfung: Kopfsensor %s meldet Filament nach "
                "dem abgebrochenen Wechsel (%s) – RESUME frei (ob es das "
                "Ziel-Filament ist, prüft der Pi nicht; Stock ebenso)."
                % (sens, self._abort_text(ab)))
            return
        if head is None:
            name = self.cfg.get("filament_sensor")
            what = ("Kopf-Filamentsensor (nicht konfiguriert, [k2_box] "
                    "filament_sensor leer) liefert keinen Zustand" if not name
                    else "Kopf-Filamentsensor %s liefert keinen Zustand "
                    "([filament_switch_sensor %s] fehlt)" % (name, name))
            return self._resume_block(
                gcmd, ab, "%s – ob der Kopf leer ist, ist UNBEKANNT (RESUME "
                "könnte ohne Filament weiterdrucken)" % (what,),
                "Kopf von Hand prüfen bzw. laden (%s), dann "
                "KTWO_BOX_ERROR_CLEAR HEAD_LOADED=1 und RESUME." % (load,),
                "ohne Kopfsensor nie (Stock prüft ihn ebenso)")
        why = self._redo_block_reason(gcmd, ab)
        if why:
            hint = ""
            if self.chain.get("active"):
                hint = (" Offene Kette (M8200): alternativ %sKTWO_BOX_EXTRUDE "
                        "TNN=%s als Kettenschritt%s." % (
                            "KTWO_BOX_RETRUDE, dann " if cut_strand else "",
                            tnn or "T1A..T1D", "e" if cut_strand else ""))
            if cut_strand:
                hint += (" Faden nach Prüfung von Hand doch in Ordnung: "
                         "KTWO_BOX_ERROR_CLEAR HEAD_LOADED=1.")
            if cut_strand:
                head_txt = (
                    "Kopf-Filamentsensor %s meldet Filament, aber es ist der "
                    "beim Wechsel abgeschnittene Faden (seit dem Schnitt "
                    "weder zurückgezogen noch neu geladen) – RESUME würde "
                    "ohne Förderung weiterdrucken" % (sens,))
            else:
                head_txt = ("Kopf-Filamentsensor %s meldet KEIN Filament – "
                            "RESUME würde ohne Filament weiterdrucken"
                            % (sens,))
            return self._resume_block(
                gcmd, ab, head_txt,
                "In der Pause nachholen: %s, dann RESUME.%s" % (load, hint),
                why)
        return self._redo_change(gcmd, ab)

    def _redo_change(self, gcmd, ab):
        c = self.cfg
        tnn = ab["tnn"]
        temp, tparam = self._runout_temp(gcmd, tnn)
        gcmd.respond_info(
            "[k2_box] RESUME: Kopf leer nach abgebrochenem Wechsel (%s) – "
            "Wechsel wird NACHGEHOLT (resume_redo_change, Stock "
            "error_resume_process): Schacht, %s °C, Rückzug nur bei belegtem "
            "Hub, Einzug %s, Spülen; danach RESUME wie üblich"
            % (self._abort_text(ab), _n(temp), tnn))
        # Stock error_resume_process prüft das Restmaterial-Flag nicht
        self._ending_off = "Nachholen im RESUME"
        try:
            self._script(["KTWO_BOX_PRE_OPT TNN=%s Z_HOP=0 SOURCE=T" % (tnn,)])
        finally:
            self._ending_off = ""
        ch = self.chain
        if ch.get("active") and not ch.get("skip") and not self.chain_error:
            def heat(gcmd):
                # Schacht zuerst und ankommen (M400: Tropfen beim Aufheizen
                # fallen in den Schacht), dann heizen: auch ein Box-Rückzug
                # mit Extruder-E-10 braucht die heiße Düse (RETRUDE heizt
                # nur, wenn der Kopfsensor Filament meldet).
                if self._go_extrude_pos(gcmd):
                    self._move(gcmd, "Schacht erreicht", ["M400"], wrap=False)
                self._heat(gcmd, temp)
            self._run_step(gcmd, "REDO_HEAT", heat)
        self._script([
            "KTWO_BOX_RETRUDE",
            "KTWO_BOX_EXTRUDE TNN=%s %s" % (tnn, tparam),
            "KTWO_BOX_WASTE",
            "KTWO_BOX_FLUSH TNN=%s %s" % (tnn, tparam),
            "KTWO_BOX_END_OPT RETURN=0"])
        if self._cancel_requested():
            # C2: Abbruch wartet – nie weiterdrucken
            raise gcmd.error(
                "RESUME abgebrochen: Druckabbruch (CANCEL) während des "
                "Nachholens von %s – Druck bleibt pausiert, CANCEL_PRINT "
                "folgt" % (tnn,))
        if self.chain_error:
            raise gcmd.error(
                "RESUME gesperrt: Nachholen des Wechsels %s fehlgeschlagen "
                "(%s) – Druck bleibt PAUSIERT, keine Rückfahrt (Stock "
                "resume_err). Ursache beheben, KTWO_BOX_ERROR_CLEAR, dann "
                "RESUME (holt erneut nach) oder %s von Hand."
                % (tnn, self.chain_error, self._tool_of(tnn)))
        if self._head_filament() is not True:
            raise gcmd.error(
                "RESUME gesperrt: nach dem Nachholen von %s meldet der "
                "Kopfsensor %s kein Filament – Druck bleibt PAUSIERT. Kopf "
                "prüfen, %s von Hand, dann RESUME." % (
                    tnn, c.get("filament_sensor"), self._tool_of(tnn)))
        gcmd.respond_info("[k2_box] RESUME: Wechsel auf %s nachgeholt "
                          "(Kopfsensor meldet Filament) – weiter mit Schacht/"
                          "Vorextrusion/Rückfahrt" % (tnn,))
        return None

    # =================================================================
    # Auto-Nachladen bei Runout (A7) – Ersatz für Stock
    # BOX_CHECK_MATERIAL_REFILL (printer.cfg runout_gcode)
    # =================================================================
    # Ablauf (Bedienung + ANNAHMEN: config-pi/README.md):
    #   Upstream pausiert VOR runout_gcode (pause_on_runout: True,
    #   filament_switch_sensor._runout_event_handler: "PAUSE\n" + Vorlage).
    #   KTWO_BOX_RUNOUT prüft lesend (Freigaben, Kopfsensor, Box-Zustand
    #   PRINT/0x50, aktueller Slot, Hub + Einlauf leer, RFID/Gruppe), wählt
    #   den Ersatz-Slot (select_runout_slot) und fährt die vorhandene Kette:
    #   PRE_OPT (ohne Z-Hop, PAUSE hat schon gehoben) -> Schacht + Heizen +
    #   Restfilament E30 F600 (Stock) -> EXTRUDE -> WASTE -> FLUSH ->
    #   END_OPT RETURN=1 -> RESUME. Kein Rückzug/Schnitt: Hub und Kopf sind
    #   leer (der Spulenrest ist schon durch). Jeder Abbruch: Druck bleibt
    #   pausiert + Meldung (nie Shutdown).
    def _runout_stop(self, gcmd, text, dry=False):
        msg = "[k2_box] RUNOUT: kein automatisches Nachladen – %s" % (text,)
        self.runout_last = msg
        if dry:
            gcmd.respond_info(msg + " (DRY_RUN)")
            return None
        if self._print_active():
            self._respond_error(
                "%s – Druck bleibt PAUSIERT. Filament von Hand nachlegen bzw. "
                "wechseln (z. B. T<n>), dann RESUME." % (msg,))
            if not self._is_paused():
                try:
                    self._script(["PAUSE"])
                except Exception as exc:
                    self._respond_error("[k2_box] PAUSE fehlgeschlagen: %s"
                                        % (exc,))
            return None
        if hasattr(gcmd, "error"):
            raise gcmd.error(msg)
        raise RuntimeError(msg)

    def _runout_base(self, gcmd):
        """Drucktemperatur vor der Pause (_PI_PARAM.hotend_temp, macros.cfg
        PAUSE), sonst aktuelles Ziel – Basis für Nachholen/Runout."""
        base = 0.0
        try:
            base = float(self._obj_status("gcode_macro _PI_PARAM")
                         .get("hotend_temp") or 0.0)
        except Exception:
            base = 0.0
        if base <= 0.0:
            try:
                base = float(self._obj_status("extruder").get("target") or 0.0)
            except Exception:
                base = 0.0
        return base

    def _runout_temp(self, gcmd, tnn=None):
        """Lade-/Heiztemperatur (Nachholen/Runout) -> (temp, Kettenparameter).

        TEMP= -> fest (an EXTRUDE/FLUSH als TEMP= weitergereicht). Sonst
        max(_runout_base, Material-Temperatur des Ziels) wie _change_temp
        (Material-DB, sonst Tn_extrude_temp 220); EXTRUDE/FLUSH bekommen
        dann BASE_TEMP=<Basis>, damit sie selbst wie Stock rechnen (FLUSH:
        max(alt, neu) + maxTemp-Stufe). Ohne DB ergibt das wie bisher
        max(Drucktemperatur, 220)."""
        t = gcmd.get_float("TEMP", None, above=0.)
        if t is not None:
            return float(t), "TEMP=%s" % _n(t)
        base = self._runout_base(gcmd)
        key = None
        if self.material_db is not None and tnn:
            try:
                tag = self._slot_tag(parse_tnn(tnn)[2])
            except ValueError:
                tag = None
            key = tag[0] if tag else None
        notes = []
        mt = self._db_temp(self._mat(key), "Nachladen %s" % (tnn or "?"),
                           notes)
        self._respond_notes(gcmd, notes)
        return max(base, mt), "BASE_TEMP=%s" % _n(base)

    def _runout_analyse(self, gcmd):
        """Nur lesend: darf/kann nachgeladen werden? -> Plan-Dict, sonst
        BoxChangeError mit Klartext (Bus-Fehler als RuntimeError)."""
        c = self.cfg
        if not self.auto_refill:
            raise BoxChangeError("Auto-Nachladen aus ([k2_box] runout_refill: "
                                 "False bzw. KTWO_BOX_AUTO_REFILL ENABLE=0)")
        if not self._released():
            raise BoxChangeError(
                "CFS-Wechsel nicht freigegeben ([k2_box] allow_write=%s, "
                "allow_move=%s)" % (c.get("allow_write"), c.get("allow_move")))
        if self.chain.get("active") or self.chain_error:
            raise BoxChangeError("Wechsel aktiv oder Wechsel-Fehler offen (%s)"
                                 % (self.chain_error or self.chain.get("tnn"),))
        if self._head_filament() is True:
            raise BoxChangeError("Kopfsensor meldet Filament – kein Runout "
                                 "(Fehlauslösung?)")
        ctl = self._ensure_controller()
        st = ctl.poll_status()
        if st.get("kind") not in ("state", "state_v113"):
            st = ctl.poll_status()  # Ereignis (0x30) -> einmal nachfragen
        state = st.get("state")
        code = int(st.get("error_code") or st.get("frame_status") or 0)
        clear = False
        if state == _BOX.BOX_STATE_ERROR:
            if code not in RUNOUT_CLEARABLE_ERRORS:
                raise BoxChangeError("Box im Zustand ERROR (0x%02x %s) – "
                                     "KTWO_BOX_ERROR_CLEAR" % (
                                         code, status_name(code)))
            clear = True
        elif state != _BOX.BOX_STATE_PRINT:
            raise BoxChangeError(
                "CFS druckt nicht (Box-Zustand %s) – Runout einer externen "
                "Spule? (ANNAHME: CFS-Druck = Box im PRINT)" % (
                    st.get("state_name", "?"),))
        slot = gcmd.get("SLOT", None) or ctl.loaded_slot or ctl.print_slot
        if not slot:
            raise BoxChangeError("aktiver CFS-Slot unbekannt (kein Wechsel "
                                 "seit dem Start; SLOT=A..D angeben)")
        idx, letter, mask = parse_slot(slot)
        hub = ctl.hub_mask()
        if hub & mask:
            raise BoxChangeError(
                "Hub meldet noch Filament von Slot %s – Spule nicht leer "
                "(Bruch/Verhakung zwischen Hub und Kopf?)" % (letter,))
        if hub:
            raise BoxChangeError("Hub durch anderen Slot belegt (0x%02x)" % hub)
        inlet = ctl.inlet_mask()
        if inlet & mask:
            raise BoxChangeError(
                "Einlauf von Slot %s meldet noch Filament – keine leere Spule "
                "(Bruch zwischen Box und Kopf?)" % (letter,))
        rfid = ctl.get_rfid_all()
        if rfid_material(rfid.get(letter)) is None \
                and letter in ctl.tag_cache:
            rfid[letter] = ctl.tag_cache[letter]  # Etikett vom Laden
        try:
            remain = ctl.get_remain(MASK_ALL)
        except Exception:
            remain = {}
        target, why = select_runout_slot(letter, inlet, rfid,
                                         self.same_material,
                                         c["runout_require_color"],
                                         manual=self._manual_letters())
        if target is None:
            raise BoxChangeError(why)
        return {"slot": letter, "target": target, "why": why,
                "clear_error": clear, "code": code, "inlet": inlet,
                "remain": remain}

    def cmd_runout(self, gcmd):
        dry = bool(gcmd.get_int("DRY_RUN", 0))
        if self.source == SOURCE_EXTERN:
            # Quelle EXTERN: kein CFS-Nachladen, normale Pause (Upstream hat
            # vor runout_gcode schon pausiert) – nichts gesendet/bewegt
            return self._runout_stop(gcmd, (
                "Quelle EXTERN (externe Spule leer/gerissen?) – kein CFS-"
                "Nachladen, nichts gesendet. Spule von Hand wechseln: "
                "UNLOAD_FILAMENT, neue Spule, Filament bis zur LED am "
                "Druckkopf schieben, LOAD_FILAMENT"), dry)
        try:
            plan = self._runout_analyse(gcmd)
        except Exception as exc:
            return self._runout_stop(gcmd, self._exc_text(exc), dry)
        c = self.cfg
        box = int(c.get("box_addr", BOX_ADDR_DEFAULT))
        old_tnn = "T%d%s" % (box, plan["slot"])
        tnn = "T%d%s" % (box, plan["target"])
        head = ("[k2_box] RUNOUT: %s leer -> Ersatz %s (%s; Einlauf 0x%02x, "
                "Rest %s)" % (old_tnn, tnn, plan["why"], plan["inlet"],
                              plan["remain"]))
        if dry:
            self.runout_last = head + " (DRY_RUN)"
            gcmd.respond_info(head + " – DRY_RUN: nichts bewegt/gesendet")
            return None
        gcmd.respond_info(head)
        temp, tparam = self._runout_temp(gcmd, tnn)
        paused = self._is_paused()
        if self.ending_material:
            # Stock check_material_refill: mit Flag -> Kopfsensor aus +
            # material_auto_refill (kein extrusion_all_materials); der Rest
            # ist am Kopfsensor vorbei, E30 schiebt ihn hier durch
            self._ending_clear("Runout-Nachladen %s -> %s (Rest am Kopfsensor "
                               "vorbei, E30 im Schacht)" % (old_tnn, tnn))
        if plan["clear_error"]:
            try:
                self._ensure_controller().action.communication_set_box_mode_feed()
                gcmd.respond_info("[k2_box] RUNOUT: Box-Fehler 0x%02x per 0x04 "
                                  "[00 01] quittiert" % (plan["code"],))
            except Exception as exc:
                return self._runout_stop(gcmd, "Box-Fehler 0x%02x nicht "
                                         "quittierbar: %s" % (
                                             plan["code"], self._exc_text(exc)))
        self._ending_off = "Runout-Nachladen"
        try:
            self._script(["KTWO_BOX_PRE_OPT TNN=%s Z_HOP=%s" % (
                tnn, "0" if paused else _n(c["change_z_hop"]))])
        finally:
            self._ending_off = ""
        if self.chain.get("active") and not self.chain.get("skip"):
            def tail(gcmd):
                # Stock runout_gcode "G0 E30 F600" (wenn heiß): Rest durch die
                # Zahnräder – hier im Schacht, nach dem Heizen.
                self._go_extrude_pos(gcmd)
                self._heat(gcmd, temp)
                self._move(gcmd, "Runout: Restfilament durch den Extruder "
                           "(Stock E30 F600)", gcode_extruder_move(
                               c["runout_tail_length"],
                               c["runout_tail_velocity"]) + ["M400"])
            if c["runout_tail_length"] > 0:
                self._run_step(gcmd, "RUNOUT_TAIL", tail)
        self._script([
            "KTWO_BOX_EXTRUDE TNN=%s %s" % (tnn, tparam),
            "KTWO_BOX_WASTE",
            "KTWO_BOX_FLUSH TNN=%s %s" % (tnn, tparam),
            "KTWO_BOX_END_OPT RETURN=1"])
        if self._cancel_requested():
            # C2: Abbruch wartet – kein RESUME
            self.runout_last = ("[k2_box] RUNOUT: Nachladen %s -> %s wegen "
                                "Druckabbruch (CANCEL) beendet – kein RESUME"
                                % (old_tnn, tnn))
            gcmd.respond_info(self.runout_last)
            return None
        if self.chain_error:
            self.runout_last = ("[k2_box] RUNOUT: Nachladen %s -> %s "
                                "fehlgeschlagen (%s) – Druck bleibt pausiert"
                                % (old_tnn, tnn, self.chain_error))
            self._respond_error(self.runout_last)
            return None
        self.runout_last = "[k2_box] RUNOUT: %s leer -> %s nachgeladen" % (
            old_tnn, tnn)
        gcmd.respond_info(self.runout_last)
        if paused and self._is_paused():
            if c["runout_resume"]:
                try:
                    self._script(["RESUME"])
                except Exception as exc:
                    self._respond_error(
                        "[k2_box] RUNOUT: RESUME fehlgeschlagen (%s) – Druck "
                        "bleibt pausiert, von Hand fortsetzen" % (exc,))
            else:
                gcmd.respond_info("[k2_box] RUNOUT: runout_resume: False – "
                                  "Druck bleibt pausiert, RESUME von Hand")
        return None

    def cmd_auto_refill(self, gcmd):
        en = gcmd.get_int("ENABLE", None)
        if en and self.source == SOURCE_EXTERN:
            msg = ("[k2_box] KTWO_BOX_AUTO_REFILL ENABLE=1 gesperrt: Quelle ist "
                   "EXTERN – kein CFS-Nachladen (erst SET_FILAMENT_SOURCE SOURCE=CFS)")
            if hasattr(gcmd, "error"):
                raise gcmd.error(msg)
            raise RuntimeError(msg)
        if en is not None:
            self.auto_refill = bool(en)
        gcmd.respond_info(
            "[k2_box] Auto-Nachladen: %s (wirkt nur mit allow_write=%s UND "
            "allow_move=%s; Regel: %s, Farbe %s)" % (
                "AN" if self.auto_refill else "AUS",
                self.cfg.get("allow_write"), self.cfg.get("allow_move"),
                ("Gruppen %s" % self.get_status()["same_material"])
                if self.same_material else "RFID gleiches Material",
                "muss passen" if self.cfg.get("runout_require_color")
                else "egal") + (" – Quelle EXTERN: kein CFS-Nachladen"
                                if self.source == SOURCE_EXTERN else ""))

    def cmd_remain(self, gcmd):
        """KTWO_BOX_REMAIN [COLOR=0RRGGBB|none] [TYPE=<id>|none] [TNN=T1A]
        [CLEAR=1] – Material in der Düse anzeigen bzw. von Hand setzen.

        NICHT-Stock (Stock hat kein Reset-Kommando):
        Ersatz für die Korrektur nach Handarbeit an der Düse.
        Ohne Parameter nur Anzeige; gesetzte Werte gehen in die Spülmenge des
        NÄCHSTEN Wechsels ein und werden (wenn an) gespeichert. Bewegt nichts,
        sendet nichts – daher ohne allow_write/allow_move."""
        try:
            clear = gcmd.get_int("CLEAR", 0)
            col_raw = gcmd.get("COLOR", None)
            typ_raw = gcmd.get("TYPE", None)
            tnn_raw = gcmd.get("TNN", None)
            new = dict(self.remain_material)
            if clear:
                new = {"type": None, "color": None, "tnn": ""}
            if col_raw is not None:
                new["color"] = normalize_remain_color(col_raw)
            if typ_raw is not None:
                new["type"] = normalize_remain_type(typ_raw)
            if tnn_raw is not None:
                new["tnn"] = (parse_tnn(tnn_raw)[3]
                              if str(tnn_raw).strip().lower()
                              not in ("", "none") else "")
        except ValueError as exc:
            raise gcmd.error("[k2_box] KTWO_BOX_REMAIN: %s" % (exc,))
        changed = clear or col_raw is not None or typ_raw is not None \
            or tnn_raw is not None
        if changed:
            self.remain_material = new
            self._persist_remain(gcmd)
        r = self.remain_material
        gcmd.respond_info(
            "[k2_box] Düsen-Material%s: Farbe %s, Typ %s, Slot %s; Datei %s "
            "(%s)" % (" gesetzt" if changed else "", r.get("color") or "unbekannt",
                      r.get("type") or "unbekannt", r.get("tnn") or "-",
                      self.remain_file or "aus", self.remain_load))

    # =================================================================
    # RFID neu lesen + Slot-Handwerte (18.10.2026, Stock-Display-App:
    # BOX_INFO_REFRESH, BOX_MODIFY_TN_DATA;
    # README-k2_box.md §15)
    # =================================================================
    def _slot_letters(self, raw, allow_all=True):
        """SLOT=T1A..T4D|A..D|0..3|ALL -> [Buchstaben] der konfigurierten Box.
        Stock-NUM ist eine Maske (NUM=0 = Maske 0)
        – hier gibt es nur Slots, eine Maske 0 entsteht nie."""
        txt = str(raw if raw is not None else "").strip().strip('"').upper()
        if txt in ("", "ALL") and allow_all:
            return list(SLOT_LETTERS)
        if txt == "ALL":
            raise ValueError("SLOT=ALL hier nicht erlaubt – ein Slot T1A..T1D")
        if not txt:
            raise ValueError("SLOT=T1A..T1D fehlt")
        if txt.startswith("T"):
            box, _i, letter, tnn = parse_tnn(txt)
            if box != int(self.cfg.get("box_addr", BOX_ADDR_DEFAULT)):
                raise ValueError("%s liegt in Box %d, konfiguriert ist nur "
                                 "Box %d" % (tnn, box, int(self.cfg.get(
                                     "box_addr", BOX_ADDR_DEFAULT))))
            return [letter]
        return [parse_slot(txt)[1]]

    def _rfid_block_reason(self, ctl, force):
        if self.chain.get("active"):
            return ("Wechsel läuft (%s) – RFID erst nach der Kette lesen"
                    % (self.chain.get("tnn") or "?",))
        if ctl is not None and ctl.busy:
            return "Box-Vorgang läuft (busy)"
        if self._print_active() and not force:
            return ("Druck läuft/pausiert – RFID-Lesen nur außerhalb des "
                    "Drucks (FORCE=1 liest trotzdem, nie in einer Kette)")
        return ""

    def _slot_line(self, letter, info, val=None, read=False):
        """Meldezeile eines Slots (Material aus DB, Farbe, Rest, Quelle)."""
        parts = []
        if read:
            if rfid_material(val) is not None:
                parts.append("Etikett gelesen")
            else:
                parts.append("kein lesbares Etikett (%s)" % (
                    val if val else "keine Antwort"))
        if info.get("type") or info.get("color"):
            parts.append("Material %s%s, Farbe %s" % (
                info.get("type") or "unbekannt",
                (" (%s)" % info["material"]) if info.get("material") else "",
                info.get("color") or "unbekannt"))
            parts.append("Quelle %s" % (info.get("source") or "-"))
        else:
            parts.append("Material/Farbe unbekannt")
        rem = info.get("remain")
        parts.append("Rest %s" % (("%d %%" % rem) if isinstance(rem, int)
                                  else "unbekannt"))
        return "%s: %s" % (info.get("tnn") or letter, ", ".join(parts))

    def cmd_rfid(self, gcmd):
        """KTWO_BOX_RFID [SLOT=T1A..T1D|A..D|ALL] [FORCE=1] – RFID-Etikett
        und Restmenge neu lesen (Ersatz Stock BOX_INFO_REFRESH).

        Nur lesende Frames: 0x02 [m] GET_RFID, dann 0x03 [m] GET_REMAIN_LEN
        mit m = Slotbit (A=01 … D=08) bzw. 0f für ALL – nie Maske 0. Der
        Stock-Makroschritt BOX_SET_PRE_LOADING … ACTION=RUN (0x0d, bewegt
        Filament) entfällt bewusst. Ohne allow_write erlaubt. Gesperrt in
        einer laufenden Wechselkette/bei busy, im Druck/Pause nur mit
        FORCE=1. Ein lesbares Etikett verwirft den Handwert des Slots."""
        try:
            letters = self._slot_letters(gcmd.get("SLOT", None))
            force = bool(gcmd.get_int("FORCE", 0))
        except ValueError as exc:
            raise gcmd.error("[k2_box] KTWO_BOX_RFID: %s" % (exc,))
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            raise gcmd.error("[k2_box] KTWO_BOX_RFID: kein Bus: %s" % (exc,))
        why = self._rfid_block_reason(ctl, force)
        if why:
            raise gcmd.error("[k2_box] KTWO_BOX_RFID gesperrt: %s" % (why,))
        if len(letters) == len(SLOT_LETTERS):
            mask = MASK_ALL
        else:
            mask = SLOT_MASKS[SLOT_LETTERS.index(letters[0])]
        if not mask or mask & ~MASK_ALL:   # pragma: no cover – Schutz
            raise gcmd.error("[k2_box] KTWO_BOX_RFID: Maske 0x%02x ungültig"
                             % mask)
        try:
            if mask == MASK_ALL:
                vals = ctl.get_rfid_all()
            else:
                vals = {letters[0]: ctl.get_rfid(letters[0])}
            ctl.get_remain(mask)
        except CfsStatusError as exc:
            raise gcmd.error("[k2_box] KTWO_BOX_RFID: Box-Status 0x%02x (%s)"
                             % (exc.status, exc.name))
        except (RuntimeError, OSError, ValueError) as exc:
            raise gcmd.error("[k2_box] KTWO_BOX_RFID: RS485-Bus-Fehler: %s"
                             % (self._exc_text(exc),))
        st = self._slot_material_status()
        lines = [self._slot_line(x, st[x], vals.get(x), read=True)
                 for x in letters]
        self.rfid_last = "; ".join(lines)
        gcmd.respond_info("[k2_box] RFID neu gelesen (0x02/0x03 Maske "
                          "0x%02x):\n  %s" % (mask, "\n  ".join(lines)))

    def cmd_slot_set(self, gcmd):
        """KTWO_BOX_SLOT_SET SLOT=T1A [COLOR=RRGGBB|none] [TYPE=<id>|none |
        MATERIAL=<Name>] [CLEAR=1] – Farbe/Material eines Slots von Hand
        (Ersatz Stock BOX_MODIFY_TN_DATA PART=color_value|material_type).

        Nur Pi-Speicher/Status (+ slot_material_file), sendet nichts. Je
        Feld vor dem Etikett, bis ein lesbares Etikett des Slots gelesen
        wird (KTWO_BOX_RFID, Laden, Runout). Ohne COLOR/TYPE/MATERIAL/CLEAR
        nur Anzeige. Gesperrt während einer Wechselkette (Spülmenge/
        Temperatur der Kette sollen nicht mitten drin wechseln)."""
        try:
            letters = self._slot_letters(gcmd.get("SLOT", None),
                                         allow_all=False)
            letter = letters[0]
            clear = bool(gcmd.get_int("CLEAR", 0))
            col_raw = gcmd.get("COLOR", None)
            typ_raw = gcmd.get("TYPE", None)
            mat_raw = gcmd.get("MATERIAL", None)
            if typ_raw is not None and mat_raw is not None:
                raise ValueError("TYPE= und MATERIAL= nicht zusammen")
            tnn = self._box_tnn(letter)
            new = {} if clear else dict(self.slot_manual.get(tnn) or {})
            if col_raw is not None:
                new["color"] = normalize_remain_color(col_raw)
            if typ_raw is not None:
                new["type"] = normalize_remain_type(typ_raw)
            if mat_raw is not None:
                new["type"] = find_material_by_name(self.material_db, mat_raw)
        except ValueError as exc:
            raise gcmd.error("[k2_box] KTWO_BOX_SLOT_SET: %s" % (exc,))
        changed = clear or col_raw is not None or typ_raw is not None \
            or mat_raw is not None
        if changed and self.chain.get("active"):
            raise gcmd.error("[k2_box] KTWO_BOX_SLOT_SET gesperrt: Wechsel "
                             "läuft (%s)" % (self.chain.get("tnn") or "?",))
        if changed:
            new = {k: new.get(k) for k in SLOT_MATERIAL_FIELDS
                   if new.get(k)}
            if new:
                self.slot_manual[tnn] = new
            else:
                self.slot_manual.pop(tnn, None)
            self._persist_slots(gcmd)
        info = self._slot_material_status()[letter]
        gcmd.respond_info("[k2_box] Slot%s %s; Handwert %s; Datei %s (%s)" % (
            " gesetzt" if changed else "", self._slot_line(letter, info),
            ("%s/%s" % (info["manual"].get("type") or "-",
                        info["manual"].get("color") or "-"))
            if info["manual"] else "keiner",
            self.slot_file or "aus", self.slot_load))

    def cmd_same_material(self, gcmd):
        if gcmd.get_int("CLEAR", 0):
            self.same_material = []
        else:
            raw = gcmd.get("LIST", None)
            if raw is not None:
                try:
                    self.same_material = parse_same_material(
                        raw, self.cfg.get("box_addr", BOX_ADDR_DEFAULT))
                except ValueError as exc:
                    msg = "[k2_box] SAME_MATERIAL: %s" % (exc,)
                    if hasattr(gcmd, "error"):
                        raise gcmd.error(msg)
                    raise RuntimeError(msg)
        groups = self.get_status()["same_material"]
        gcmd.respond_info("[k2_box] Gleich-Material-Gruppen: %s" % (
            groups if groups else "keine (Ersatz per RFID-Vergleich)",))


    # =================================================================
    # Restmaterial („ending material“) + Druckende (17.10.2026,
    # README-k2_box.md §12.5)
    # =================================================================
    # Stock: Box meldet im Druck „Filament-Ende“ (Spule leer, Rest steckt im
    # Schlauch) -> Flag is_use_ending_material. Beim nächsten Wechsel
    # (Tn_action Z.4614), bei M8200 P (CR_BOX_PRE_OPT Z.5761) und am
    # Druckende (box_end Z.5111) wird der Rest dann AUSGESPÜLT
    # (extrusion_all_materials Z.1783–1824) statt geschnitten und
    # zurückgezogen – ein Rest ohne Ende in der Box lässt sich nicht sinnvoll
    # zurückziehen. Pi: Auslöser = 0x0a-Status 0x50 im Druck (_note_ending),
    # Ausspülen in PRE_OPT (_maybe_ending_purge) bzw. KTWO_BOX_END_PRINT.
    # Bewusst NICHT übernommen: Stock reactor.pause(t − 0.5) ohne
    # monotonic() (kehrt sofort zurück) – hier wartet je Stück
    # ein M400; dazu Zeitlimit, Obergrenze ohne Überschreiten, Kopfsensor
    # unbekannt = Abbruch (Stock fördert dann blind bis 3000 mm).
    def _note_ending(self, st):
        """0x0a-Poll -> Restmaterial-Fall merken (Stock BoxAction.send_data
        Z.2145–2155: Box-Filamentfehler bei print_stats printing/paused und
        Flag noch nicht gesetzt -> is_use_ending_material = True). Pi-
        Auslöser: Status 0x50 im PRINT (bzw. gespeicherter Fehler 0x50 im
        ERROR) = Einlauf oder Hub des Druck-Slots frei (CFS-Firmware-
        Analyse). Setzt nur den Merker + Meldung, bewegt/sendet nichts."""
        if self.ending_material is not None or not isinstance(st, dict) \
                or st.get("kind") != "state":
            return
        if self.source == SOURCE_EXTERN:
            # Quelle EXTERN: gedruckt wird nicht aus der CFS – ein 0x50 betrifft
            # keinen Schlauch zum Kopf, kein Ausspülen (nur lesendes Polling)
            return
        # Während einer Wechselkette nur der frische Poll aus PRE_OPT
        # (_maybe_ending_purge): spätere Polls (wait_box_ready vor dem
        # Einzug) sähen noch das 0x50 des alten Slots.
        if self.chain.get("active") and not self._ending_probe:
            return
        state = st.get("state")
        if state == _BOX.BOX_STATE_PRINT:
            code = st.get("frame_status")
        elif state == _BOX.BOX_STATE_ERROR:
            code = st.get("error_code")
        else:
            return
        if int(code or 0) != _BOX.STATUS_RUNOUT or not self._print_active():
            return
        ctl = self.controller
        letter = (ctl.print_slot or ctl.loaded_slot) if ctl is not None \
            else None
        tnn = ("T%d%s" % (int(self.cfg.get("box_addr", BOX_ADDR_DEFAULT)),
                          letter)) if letter else ""
        self.ending_material = {"slot": letter or "", "tnn": tnn,
                                "why": "Box-Status 0x50 im Druck"}
        self.ending_last = (
            "[k2_box] Restmaterial: Spule %s im CFS leer (Box-Status 0x50) – "
            "der Druck läuft mit dem Rest im Schlauch weiter; beim nächsten "
            "Wechsel bzw. am Druckende wird der Rest %s" % (
                tnn or "(Slot unbekannt)",
                "ausgespült (ending_material_purge)"
                if self.cfg.get("ending_material_purge") else
                "wie bisher geschnitten und zurückgezogen "
                "(ending_material_purge: False)"))
        try:
            logging.info(self.ending_last)
        except Exception:
            pass
        try:
            self.gcode.respond_info(self.ending_last)
        except Exception:
            pass

    def _ending_clear(self, why):
        """Restmaterial-Fall erledigt (Stock Z.3901 Flag = False)."""
        em = self.ending_material
        if not em:
            return
        self.ending_material = None
        self.ending_last = "[k2_box] Restmaterial %s erledigt: %s" % (
            em.get("tnn") or "(Slot unbekannt)", why)
        try:
            logging.info(self.ending_last)
        except Exception:
            pass

    def _ending_velocity(self, key):
        """Stock Z.1789–1794: get_material_max_extrusion_speed(last_cmd)·60
        = max_vol/2,4·60 – OHNE ×0,8 und OHNE 360-Grenze des Normal-
        Spülens; ohne Wert Tn_extrude_velocity[0] (360). Pi ohne Material-DB
        bzw. ohne max_vol: Tn_extrude_velocity (bisherige Spülgeschwindig-
        keit ohne DB). -> (mm/min, Begründung)."""
        if self.material_db is not None:
            mp = self._mat(key)
            if mp.get("vol"):
                return (float(mp["vol"]) / 2.4 * 60.0,
                        "max_vol %s/2,4·60, ohne ×0,8 und 360-Grenze"
                        % _n(mp["vol"]))
            return (float(self.cfg["tn_extrude_velocity"]),
                    "Tn_extrude_velocity, Material %s ohne max_vol"
                    % (key or "unbekannt"))
        return (float(self.cfg["tn_extrude_velocity"]),
                "Tn_extrude_velocity, ohne Material-DB")

    def _ending_temp(self, gcmd, key, notes):
        """Stock Z.1784–1796: get_material_target_temp(last_cmd), in der
        Pause die gemerkte Drucktemperatur (PRINTER_PARAM.hotend_temp). Pi
        wie _change_temp: TEMP= fest, sonst max(Basis, Material-Temperatur
        aus der DB bzw. Tn_extrude_temp); Basis in der Pause _PI_PARAM.
        hotend_temp, sonst BASE_TEMP= bzw. Extruder-Ziel."""
        t = gcmd.get_float("TEMP", None, above=0.)
        if t is not None:
            return float(t)
        base = self._runout_base(gcmd) if self._is_paused() \
            else self._base_temp(gcmd)
        return max(base, self._db_temp(self._mat(key), "Restmaterial", notes))

    def _ending_purge(self, gcmd, origin):
        """Rest im Schlauch ausspülen (Stock extrusion_all_materials) ->
        True = ausgeführt, False = nur Plan (Freigaben fehlen); Fehler ->
        Ausnahme (Aufrufer: _run_step -> _fail = PAUSE im Druck).

        Ablauf: Kopfsensor aus (in einer Kette schon durch PRE_OPT) ->
        Schacht -> Heizen -> Schleife: G1 E80 F<v> + M400, Blasen (flush_blow,
        fan0 flush_blow_time s), Blech OHNE Kreise (bleibt im Schacht) -> Ende,
        sobald der Kopfsensor nach einem Stück LEER meldet, plus genau ein
        weiteres Stück (Sensor -> Düse); Obergrenze max_tube_length (letztes
        Stück gekürzt, nie darüber; Kopf dann noch voll = Fehler);
        ending_material_timeout = Zeitlimit; Kopfsensor unbekannt (vorher
        oder unterwegs) = Abbruch, nie blind fördern. Danach: außerhalb
        einer Kette Abkühlen M104 S140 (Stock set_cool_temp; IN der Kette
        nicht – EXTRUDE heizt gleich wieder, END_OPT stellt das Ziel zurück),
        Box 0x04 [00 01] IDLE, Slot vergessen (Stock last_cmd = None).
        remain_material bleibt (Stock: kein save; in der Düse ist weiter
        dasselbe Material)."""
        c = self.cfg
        em = self.ending_material or {}
        ch = self.chain
        in_chain = bool(ch.get("active"))
        key = self._nozzle_key()
        if not key and em.get("slot"):
            tag = self._cached_tag(em["slot"])
            key = tag[0] if tag else None
        v, vwhy = self._ending_velocity(key)
        piece = ENDING_PIECE_MM
        limit = float(c["max_tube_length"])
        what = em.get("tnn") or "(Slot unbekannt)"
        plan = ("Restmaterial %s ausspülen (%s): Kopfsensor aus, Schacht, "
                "Heizen, je Stück G1 E%s F%s (%s) + Blasen + Blech ohne "
                "Kreise, bis der Kopfsensor leer meldet + 1 Stück, höchstens "
                "%s mm; danach %sBox 0x04 [00 01] IDLE, Slot vergessen" % (
                    what, origin, _n(piece), _n(v), vwhy, _n(limit),
                    "" if in_chain else "M104 S%s, " % _n(ENDING_COOL_TEMP)))
        if not self._released():
            gcmd.respond_info("[k2_box] PLAN %s (allow_write=%s, allow_move=%s"
                              " – nichts bewegt/gesendet)" % (
                                  plan, c.get("allow_write"),
                                  c.get("allow_move")))
            return False
        if self._head_filament() is None:
            raise BoxChangeError(
                "Restmaterial %s: Kopf-Filamentsensor %s liefert keinen "
                "Zustand – der Rest wird NICHT blind bis %s mm gefördert. "
                "Schlauch/Kopf von Hand leeren, dann KTWO_BOX_ENDING CLEAR=1"
                % (what, c.get("filament_sensor") or "(nicht konfiguriert)",
                   _n(limit)))
        gcmd.respond_info("[k2_box] %s" % (plan,))
        sensor_restore = None
        if not in_chain:
            sens = self._obj_status("filament_switch_sensor %s"
                                    % (c.get("filament_sensor"),))
            if "enabled" in sens:
                sensor_restore = bool(sens["enabled"])
                self._set_head_sensor(False)
        old_accel = self._max_accel()
        old_mcr = self._min_cruise_ratio()
        arcs = self._lookup("gcode_arcs") is not None
        blow_fan = c.get("flush_blow_fan") if c.get("flush_blow") else ""
        fan = self._pin(blow_fan) if blow_fan else None
        length, n, done = 0.0, 0, False
        t0 = self._now()
        try:
            if self._go_extrude_pos(gcmd):
                self._move(gcmd, "Schacht erreicht", ["M400"], wrap=False)
            notes = []
            temp = self._ending_temp(gcmd, key, notes)
            self._respond_notes(gcmd, notes)
            self._heat(gcmd, temp)
            stop_next = False
            while True:
                seg = min(piece, limit - length)
                if seg <= 1e-6:
                    break
                # C2: Druckabbruch -> nach dem laufenden Stück Schluss
                self._check_cancel("Restmaterial %s nach %s mm" % (
                    what, _n(length)))
                self._go_extrude_pos(gcmd)
                n += 1
                self._move(gcmd, "Restmaterial %d (%s mm, gesamt %s mm)" % (
                    n, _n(seg), _n(length + seg)),
                    gcode_extruder_move(seg, v) + ["M400"])
                length += seg
                if fan is not None:
                    cur = self._pin(blow_fan) or fan
                    sc = fan[1] or 255.0
                    self._move(gcmd, "Blasen %d (Stock blow)" % n,
                               gcode_blow(blow_fan, c["flush_blow_time"],
                                          cur[0] * sc, sc), wrap=False)
                self._move(gcmd, "Reinigung %d (Blech, bleibt im Schacht)" % n,
                           gcode_nozzle_clean(c, False, 0.0, old_accel,
                                              old_mcr, arcs))
                if stop_next:
                    done = True
                    break
                if length >= limit - 1e-6:
                    break
                if self._now() - t0 > float(c["ending_material_timeout"]):
                    raise BoxChangeError(
                        "Restmaterial %s: Zeitlimit %s s überschritten nach "
                        "%s mm (ending_material_timeout) – abgebrochen"
                        % (what, _n(c["ending_material_timeout"]),
                           _n(length)))
                head = self._head_filament()
                if head is None:
                    raise BoxChangeError(
                        "Restmaterial %s: Kopf-Filamentsensor liefert nach "
                        "%s mm keinen Zustand mehr – abgebrochen (nicht "
                        "blind weiter)" % (what, _n(length)))
                stop_next = head is False
            if not done:
                head = self._head_filament()
                if head is not False:
                    raise BoxChangeError(
                        "Restmaterial %s: Obergrenze max_tube_length %s mm "
                        "erreicht, Kopfsensor meldet %s – Schlauch/Sensor "
                        "prüfen" % (what, _n(limit), "weiter Filament" if head
                                    else "keinen Zustand"))
                gcmd.respond_info(
                    "[k2_box] WARNUNG Restmaterial %s: Kopfsensor erst an der "
                    "Obergrenze %s mm leer – das letzte Stück (Sensor -> "
                    "Düse) fehlt" % (what, _n(limit)))
        except Exception:
            if old_accel is not None:
                try:
                    self._script([_velocity_limit(old_accel, old_mcr)])
                except Exception as rexc:
                    self._restore_warn("Beschleunigung (SET_VELOCITY_LIMIT)", rexc)
            raise
        finally:
            if sensor_restore is not None:
                self._set_head_sensor(sensor_restore)
        if not in_chain:
            self._move(gcmd, "Abkühlen (Stock set_cool_temp)",
                       ["M104 S%s" % _n(ENDING_COOL_TEMP)], wrap=False)
        ctl = self._ensure_controller()
        ctl.action.communication_set_box_mode_feed()   # 0x04 [00 01] IDLE
        ctl.loaded_slot = None
        ctl.print_slot = None
        self.cut_needed = ""                # Kopf und Schlauch leer
        self.head_cut = False
        self._ending_clear("%s mm in %d Stücken ausgespült (%s)" % (
            _n(length), n, origin))
        gcmd.respond_info("%s – Box IDLE, Slot vergessen, Düsen-Material "
                          "unverändert" % (self.ending_last,))
        return True

    def _maybe_ending_purge(self, gcmd):
        """Nach PRE_OPT (Kette aktiv, kein Skip/Fehler): Restmaterial-Fall
        offen -> ausspülen; CUT/RETRUDE der Kette entfallen dann. Vorher ein
        frischer 0x0a-Poll (lesend, nur im Druck mit Freigaben), damit ein
        0x50 seit dem letzten Timer-Poll nicht verloren geht."""
        if self._ending_off:
            return
        c = self.cfg
        if c.get("ending_material_purge") and self._released() \
                and self._print_active():
            self._ending_probe = True
            try:
                self._ensure_controller().poll_status()
            except Exception:
                pass
            finally:
                self._ending_probe = False
        em = self.ending_material
        if not em:
            return
        if not c.get("ending_material_purge"):
            gcmd.respond_info(
                "[k2_box] HINWEIS Restmaterial %s: ending_material_purge: "
                "False – Schnitt + Rückzug wie bisher (Rest ohne Ende in der "
                "Box wird zurückgezogen)" % (em.get("tnn") or "?",))
            self._ending_clear("ending_material_purge aus – Schnitt + Rückzug")
            return

        def body(gcmd):
            origin = ("Druckende" if self._end_print else "Wechsel %s" % (
                self.chain.get("tnn") or "M8200 P",))
            if self._ending_purge(gcmd, origin):
                self.chain["ending_purged"] = True
        self._run_step(gcmd, "ENDING_PURGE", body)

    def cmd_ending(self, gcmd):
        """KTWO_BOX_ENDING [SET=1 [SLOT=A]] [CLEAR=1] [PURGE=1] – Restmaterial-
        Fall anzeigen, von Hand setzen (Nicht-Stock, z. B. Spule von Hand
        leer erkannt) bzw. löschen (Stock use_ending_material_flag_clear, dort
        ohne Aufrufer). PURGE=1 = Stock BOX_EXTRUSION_ALL_MATERIALS (läuft
        unabhängig vom Merker): nur außerhalb eines Drucks oder in der Pause,
        nicht während einer Kette; braucht allow_write + allow_move (sonst
        Plan)."""
        try:
            if gcmd.get_int("CLEAR", 0):
                if self.ending_material:
                    self._ending_clear("KTWO_BOX_ENDING CLEAR=1 (von Hand)")
                else:
                    self.ending_last = ("[k2_box] Restmaterial: kein Fall "
                                        "offen (CLEAR=1)")
            elif gcmd.get_int("SET", 0):
                raw = gcmd.get("SLOT", None)
                ctl = self.controller
                letter = parse_slot(raw)[1] if raw else (
                    ctl.loaded_slot if ctl is not None else None)
                tnn = ("T%d%s" % (int(self.cfg.get("box_addr", 1)), letter)
                       if letter else "")
                self.ending_material = {"slot": letter or "", "tnn": tnn,
                                        "why": "von Hand (KTWO_BOX_ENDING "
                                               "SET=1)"}
                self.ending_last = ("[k2_box] Restmaterial %s von Hand gesetzt"
                                    % (tnn or "(Slot unbekannt)",))
        except ValueError as exc:
            raise gcmd.error("[k2_box] KTWO_BOX_ENDING: %s" % (exc,))
        if gcmd.get_int("PURGE", 0):
            if self._extern_block(gcmd, "KTWO_BOX_ENDING PURGE=1 (CFS-"
                                  "Restmaterial ausspülen)"):
                return None
            if self._print_active() and not self._is_paused():
                raise gcmd.error("[k2_box] KTWO_BOX_ENDING PURGE=1 nur außerhalb "
                                 "eines Drucks oder in der Pause")
            if self.chain.get("active"):
                raise gcmd.error("[k2_box] KTWO_BOX_ENDING PURGE=1: Wechsel"
                                 "kette offen (%s)" % (
                                     self.chain.get("tnn") or "ohne TNN",))

            def body(gcmd):
                self._ending_purge(gcmd, "KTWO_BOX_ENDING PURGE=1")
            return self._run_step(gcmd, "ENDING_PURGE", body, chain_step=False)
        em = self.ending_material
        gcmd.respond_info(
            "[k2_box] Restmaterial: %s; ending_material_purge=%s, "
            "max_tube_length=%s mm%s" % (
                ("Fall offen %s (%s)" % (em.get("tnn") or "(Slot unbekannt)",
                                         em.get("why") or "")) if em
                else "kein Fall offen", bool(self.cfg.get(
                    "ending_material_purge")),
                _n(self.cfg["max_tube_length"]),
                ("; zuletzt: %s" % self.ending_last) if self.ending_last
                else ""))
        return None

    # --- Druckweiter Box-Zustand (A2, Live-Abgleich 18.10.2026) -----
    def _extrude_inhibit(self, ctl):
        """0x0f-Paar im Einzug senden? False nur, wenn KTWO_BOX_PRINT_START
        die Sperre hält UND ein Druck läuft/pausiert (Stock T0 im Druck ohne
        0x0f). Hält sie ohne Druck (Druck ohne END_PRINT beendet) -> wie
        bisher senden (0x0f [00] am Ende hebt die alte Sperre auf)."""
        return not (getattr(ctl, "print_hold", False)
                    and self._print_active())

    def _print_state(self, gcmd, start):
        name = "KTWO_BOX_PRINT_%s" % ("START" if start else "END")
        plan = ("0x0d [0f 00] + 0x0f [01]" if start
                else "0x0d [0f 01] + 0x0f [00]")
        if not self.cfg.get("allow_write"):
            self._box_plan(gcmd, name, plan)
            return None
        try:
            ctl = self._ensure_controller()
            res = ctl.do_print_state(start)
        except Exception as exc:
            # Nie den Druck anhalten: ohne Druckzustand sendet der Einzug
            # sein 0x0f-Paar wie bisher (sicherer Rückfall).
            self._respond_error(
                "[k2_box] %s fehlgeschlagen: %s – Druck läuft weiter%s" % (
                    name, self._exc_text(exc),
                    " (Einzug schaltet 0x0f wie bisher selbst)" if start
                    else " (Box-Preload ggf. von Hand: KTWO_BOX_PRINT_END)"))
            return None
        gcmd.respond_info("[k2_box] %s: %s gesendet" % (
            name, " + ".join(res.get("frames") or [])))
        return None

    def cmd_print_start(self, gcmd):
        """KTWO_BOX_PRINT_START (aus START_PRINT, nur change_enabled) = Stock
        BOX_START_PRINT: 0x0d [0f 00] + 0x0f [01] – gilt den ganzen Druck
        (Live-Abgleich 18.10.2026, Live 16:45:36/16:45:37).
        C3 (18.10.2026): neuer Druck -> Wechsel-Fehlerzustand eines früheren
        Drucks zurücksetzen (Rückfall, falls CANCEL_PRINT ihn nicht
        erreicht hat) – nur ohne Pause (START_PRINT: CLEAR_PAUSE davor)."""
        # E5/A4: neuer Druck -> altes Werkzeug im Druck unbekannt (erster
        # Einzug über den Farbweg wie Stock)
        self._print_flush_reset()
        self._extern_tool = None
        # Schnittschutz: der Druck schmilzt Filament in der Düse
        self.cut_needed = "dem Druckbeginn"
        if self._is_paused():
            gcmd.respond_info("[k2_box] PRINT_START: Druck pausiert – "
                              "Wechsel-Fehlerzustand bleibt")
        else:
            self._reset_print_state(gcmd, "neuer Druck (KTWO_BOX_PRINT_START)")
        # Quelle EXTERN: Stock sendet 0x0d [0f 00] + 0x0f [01] ebenso (live
        # beobachtet) – enge Ausnahme vom Schreibschutz
        return self._print_state(gcmd, True)

    # --- C3: Wechsel-Fehlerzustand am Druckende (18.10.2026) -------------
    # Stock: cmd_box_end_print (BOX_END_PRINT, aus END_PRINT – auch beim
    # Abbruch) ruft box_save.clear_resume_tnn(); Stock box_end prüft
    # keinen offenen Fehler und entlädt bei gemeldetem Filament. Pi: der Zustand (chain_error, aborted,
    # pending_resume_z) sperrt RESUME und das nächste T<n> – er wird NUR
    # nach dem Druckende zurückgesetzt (KTWO_BOX_CANCEL_RESET nach
    # CANCEL_PRINT_BASE bzw. KTWO_BOX_PRINT_START), nie in einer Pause.
    # Schritte, nach deren Fehler das Entladen beim Abbruch als sicher gilt:
    # Einzug/Spülen/Schacht-Hinweis (Schnitt, Rückzug, Schachtfahrten,
    # Heizen und Ausspülen sind selbst Teil des Entladens bzw. unklar).
    _CANCEL_UNLOAD_OK = ("EXTRUDE", "FLUSH", "WASTE")

    def _cancel_unload_block(self):
        """None = Entladen beim Abbruch trotz offenem Wechsel-Fehler sicher,
        sonst Grund (Klartext)."""
        step = self._chain_error_step or (self.aborted or {}).get("step") \
            or ""
        if step not in self._CANCEL_UNLOAD_OK:
            return ("Fehler im Schritt %s – Schnitt/Rückzug/Fahrt selbst "
                    "betroffen oder unklar; nur nach %s wird automatisch "
                    "entladen" % (step or "(unbekannt)",
                                  "/".join(self._CANCEL_UNLOAD_OK)))
        head = self._head_filament()
        if head is not True:
            return ("Kopf-Filamentsensor meldet %s" % (
                "kein Filament – nichts zu entladen" if head is False
                else "keinen Zustand"))
        return None

    def _reset_print_state(self, gcmd, why):
        """Wechsel-Fehlerzustand eines beendeten Drucks löschen: chain_error,
        Abbruch-Merker (aborted), Z-Korrektur, gemerkte Startposition,
        offene Kette (nicht-mechanisch aufgeräumt); war ein Wechsel-Fehler
        offen und allow_write an, zusätzlich Box 0x04 [00 01] wie
        KTWO_BOX_ERROR_CLEAR (keine Bewegung). Meldung nur, wenn etwas offen
        war."""
        parts = []
        if self.chain_error:
            parts.append("Wechsel-Fehler (%s)" % (self.chain_error,))
        if self.aborted:
            parts.append("Abbruch-Merker %s" % (self._abort_text(self.aborted),))
        if self.pending_resume_z is not None:
            parts.append("Z-Korrektur %s" % (_n(self.pending_resume_z),))
        if self.chain.get("active"):
            parts.append("offene Kette %s" % (self.chain.get("tnn")
                                              or "ohne TNN",))
            self._chain_cleanup(gcmd)
        had_error = bool(self.chain_error)
        self.chain_error = ""
        self._chain_error_step = ""
        self.aborted = None
        self._m8200_offen = ""
        self._pause_from_fail = False
        self.pending_resume_z = None
        self._keep_start_pos = None
        if had_error and self.cfg.get("allow_write") \
                and self.source != SOURCE_EXTERN:
            # wie KTWO_BOX_ERROR_CLEAR: 0x04 [00 01] löscht einen Box-ERROR
            # (live belegt) – sonst lehnte das nächste T<n> schon im
            # Box-Zustand-Abwarten ab. Fehler nur Meldung.
            try:
                self._ensure_controller().action.communication_set_box_mode_feed()
                parts.append("Box 0x04 [00 01] IDLE (löscht Box-ERROR)")
            except Exception as exc:
                parts.append("Box nicht erreichbar (%s) – ggf. "
                             "KTWO_BOX_ERROR_CLEAR" % (self._exc_text(exc),))
        if parts:
            gcmd.respond_info("[k2_box] %s: zurückgesetzt – %s (= Stock "
                              "clear_resume_tnn; nächstes T<n> startet "
                              "normal)" % (why, "; ".join(parts)))
        return bool(parts)

    def cmd_cancel_reset(self, gcmd):
        """KTWO_BOX_CANCEL_RESET [START=1] – aus CANCEL_PRINT NACH
        CANCEL_PRINT_BASE (Druck ist beendet): Wechsel-Fehlerzustand
        zurücksetzen. Nie in einer Pause; ohne START=1 auch nicht, solange
        print_stats printing/paused meldet. START=1 = Druckbeginn
        (START_PRINT ohne CFS-Freigabe; dort läuft der Druck schon)."""
        start = bool(gcmd.get_int("START", 0))
        state = self._obj_status("print_stats").get("state")
        if self._is_paused() or state == "paused" or (
                not start and state == "printing"):
            gcmd.respond_info(
                "[k2_box] CANCEL_RESET: Druck %s – nichts zurückgesetzt "
                "(RESUME-Sperre/Wechsel-Fehler bleiben bis zum Druckende)"
                % ("pausiert" if self._is_paused() or state == "paused"
                   else "läuft"))
            return None
        self._print_flush_reset()   # E5/A4
        self._extern_tool = None
        if start:
            # Schnittschutz: der Druck schmilzt Filament in der Düse
            self.cut_needed = "dem Druckbeginn"
        self._reset_print_state(gcmd, "Druckbeginn (START_PRINT)" if start
                                else "Druckabbruch (CANCEL_PRINT)")
        return None

    def cmd_print_end(self, gcmd):
        """KTWO_BOX_PRINT_END (aus END_PRINT nach KTWO_BOX_END_PRINT) =
        Stock BOX_END_PRINT: 0x0d [0f 01] + 0x0f [00] (Live
        17:44:08 – nach dem Rückzug in die Box). Fehler nur Meldung.
        Quelle EXTERN: genauso (Stock live:
        Z+2, 0x0d [0f 01], 0x0f [00], kein Schnitt/Entladen)."""
        return self._print_state(gcmd, False)

    def cmd_end_print(self, gcmd):
        """KTWO_BOX_END_PRINT [UNLOAD=0|1] aus END_PRINT (= Stock BOX_END,
        box_end Z.5104–5131): offener Restmaterial-Fall -> ausspülen
        (ending_material_purge); UNLOAD (Default end_print_unload) und Kopf
        meldet Filament -> Schacht, Heizen (Material-Temperatur), Schneiden,
        Rückzug in die Box (KTWO_BOX_CUT/RETRUDE), Schacht verlassen. KEIN
        Spülen am Druckende (Stock ebenso). remain_material bleibt (Düse
        voll, Stock). Läuft über die vorhandene Kette PRE_OPT Z_HOP=0 …
        END_OPT RETURN=0. Fehler/fehlende Freigaben -> nur Meldung, NIE
        PAUSE oder G-Code-Fehler (END_PRINT muss weiterlaufen und die
        Heizungen ausschalten)."""
        c = self.cfg
        try:
            unload = bool(gcmd.get_int("UNLOAD", 1 if c.get("end_print_unload")
                                       else 0))
        except Exception:
            unload = bool(c.get("end_print_unload"))
        try:
            cancel = bool(gcmd.get_int("CANCEL", 0))
        except Exception:
            cancel = False
        purge = bool(self.ending_material) and bool(c.get(
            "ending_material_purge"))
        tag = "[k2_box] END_PRINT"
        if self.source == SOURCE_EXTERN:
            # Quelle EXTERN (19.10.2026): kein Schnitt, kein Entladen in die
            # CFS, kein Ausspülen – Filament der externen Spule bleibt im Kopf
            # (Stock live). Den Box-Druckzustand
            # setzt danach KTWO_BOX_PRINT_END zurück (wie Stock).
            gcmd.respond_info("%s: Quelle EXTERN – kein Entladen in die CFS "
                              "(kein Schnitt, kein Rückzug); Filament der "
                              "externen Spule bleibt im Kopf (von Hand: "
                              "UNLOAD_FILAMENT)" % tag)
            return None
        if not unload and not purge:
            gcmd.respond_info("%s: nichts zu tun (end_print_unload aus, kein "
                              "Restmaterial-Fall offen)" % tag)
            return None
        if not self._released():
            gcmd.respond_info("%s: übersprungen – nicht freigegeben "
                              "(allow_write=%s, allow_move=%s); Filament "
                              "bleibt im Kopf" % (tag, c.get("allow_write"),
                                                  c.get("allow_move")))
            return None
        held = None
        if cancel and (self.chain.get("active") or self.chain_error):
            # C3 (18.10.2026): Abbruch (CANCEL_PRINT -> END_PRINT CANCEL=1)
            # mit offener Kette/offenem Wechsel-Fehler: der Druck endet ->
            # offene Kette nicht-mechanisch aufräumen; Entladen wie Stock
            # box_end nur, wenn sicher (_cancel_unload_block). chain_error
            # bleibt dabei GESETZT (nur für die Dauer des Entladens
            # beiseitegelegt, s. finally) – gelöscht wird er erst nach
            # CANCEL_PRINT_BASE (KTWO_BOX_CANCEL_RESET).
            if self.chain.get("active"):
                gcmd.respond_info("%s: Druckabbruch – offene Kette %s "
                                  "aufgeräumt" % (tag, self.chain.get("tnn")
                                                  or "ohne TNN"))
                self._chain_cleanup(gcmd)
            if self.chain_error:
                why = self._cancel_unload_block()
                if why:
                    self._respond_error(
                        "%s: Druckabbruch mit offenem Wechsel-Fehler (%s) – "
                        "NICHT entladen: %s. Kopf/Filament nach dem Abbruch "
                        "von Hand prüfen (z. B. KTWO_BOX_CUT + KTWO_BOX_UNLOAD "
                        "SLOT=<Slot>)." % (tag, self.chain_error, why))
                    return None
                held = (self.chain_error, self._chain_error_step)
                gcmd.respond_info(
                    "%s: Druckabbruch mit offenem Wechsel-Fehler (%s) – "
                    "Kopfsensor meldet Filament, Fehler im Schritt %s -> "
                    "entladen wie Stock box_end" % (tag, self.chain_error,
                                                     held[1] or "?"))
                self.chain_error = ""
        if self.chain.get("active") or self.chain_error:
            gcmd.respond_info("%s: übersprungen – Wechsel aktiv oder "
                              "Wechsel-Fehler offen (%s)" % (
                                  tag, self.chain_error
                                  or self.chain.get("tnn") or "Kette"))
            return None
        self._end_print = True
        try:
            self._script(["KTWO_BOX_PRE_OPT Z_HOP=0"])
            ch = self.chain
            if not ch.get("active") or self.chain_error:
                raise BoxChangeError("PRE_OPT nicht ausgeführt")
            if unload and not ch.get("ending_purged"):
                head = self._head_filament()
                if head is True:
                    def heat(g):
                        # Stock box_end Z.5117/5119: Schacht, dann
                        # Material-Temperatur, dann schneiden
                        if self._go_extrude_pos(g):
                            self._move(g, "Schacht erreicht", ["M400"],
                                       wrap=False)
                        self._heat(g, self._change_temp(
                            g, self._nozzle_key() if self.material_db
                            is not None else None, "Druckende"))
                    self._run_step(gcmd, "END_HEAT", heat)
                    self._script(["KTWO_BOX_CUT", "KTWO_BOX_RETRUDE"])
                    gcmd.respond_info("%s: geschnitten und in die Box "
                                      "zurückgezogen (end_print_unload)" % tag)
                elif head is False:
                    gcmd.respond_info("%s: Kopf-Filamentsensor leer – nichts "
                                      "zu entladen" % tag)
                else:
                    gcmd.respond_info(
                        "%s: Kopf-Filamentsensor liefert keinen Zustand – "
                        "kein Entladen (Stock entlädt nur bei gemeldetem "
                        "Filament)" % tag)
            elif unload:
                gcmd.respond_info("%s: Restmaterial ausgespült – kein "
                                  "Entladen nötig" % tag)
            self._script(["KTWO_BOX_END_OPT RETURN=0"])
        except Exception as exc:
            self._respond_error(
                "%s fehlgeschlagen: %s – Druckende läuft weiter (Heizungen "
                "aus); Kopf/Filament von Hand prüfen" % (tag, self._exc_text(
                    exc)))
            if self.chain.get("active"):
                self._chain_cleanup(gcmd)
        finally:
            self._end_print = False
            if held is not None:
                # Sperre zurück (gilt bis KTWO_BOX_CANCEL_RESET nach
                # CANCEL_PRINT_BASE – scheitert der Abbruch, bleibt RESUME
                # gesperrt)
                self.chain_error, self._chain_error_step = held
        return None

    # =================================================================
    # Externe Spule (19.10.2026, README-k2_box.md §18)
    # =================================================================
    # Stock (gcode_macro.cfg LOAD_MATERIAL*/QUIT_MATERIAL*, live belegt):
    #   Laden:    SAVE_GCODE_STATE, BOX_GO_TO_EXTRUDE_POS (G28 X Y nur bei
    #             Bedarf), Lüfter sichern, bei Kopfsensor heizen, Spülen wie
    #             CFS-Wechsel (FILAMENT_RACK_FLUSH), Lüfter zurück,
    #             SET_COOL_TEMP, BOX_MOVE_TO_SAFE_POS, RESTORE_GCODE_STATE.
    #   Entladen: SAVE_GCODE_STATE, BOX_GO_TO_EXTRUDE_POS, bei Kopfsensor
    #             heizen, BOX_MOVE_TO_CUT, bei Kopfsensor G0 E-10 F360,
    #             BOX_MOVE_TO_SAFE_POS, SET_COOL_TEMP, RESTORE_GCODE_STATE.
    # Pi: Makros LOAD_FILAMENT/UNLOAD_FILAMENT (macros.cfg) aus den
    # vorhandenen Bausteinen + KTWO_BOX_EXTERN_*. Der Vorgang läuft als
    # „Kette“ mit source EXTERN: Fehler -> außerhalb eines Drucks Aufräumen
    # (Kopfsensor/G-Code-Zustand zurück, Düse aus) + G-Code-Fehler, in der
    # Pause chain_error (RESUME gesperrt bis KTWO_BOX_ERROR_CLEAR). Die CFS
    # wird nie angesprochen (kein Box-Frame).
    def _extern_temp_limits(self):
        """(untere, obere) Grenze für TEMP=/Material-Temperaturen: unten
        min_extrude_temp (Laden/Entladen fördert), oben [extruder]
        max_temp − 10 K und [k2_safety]-Grenze der Düse (das Kleinere),
        ohne beides 350 °C (Stock product_param)."""
        lo = self._min_extrude_temp()
        his = []
        lim = self._temp_limit()
        if lim is not None:
            his.append(float(lim))
        try:
            v = (self._obj_status("k2_safety").get("limits") or {}).get(
                "extruder")
            if v is not None:
                his.append(float(v))
        except Exception:
            pass
        return float(lo), (min(his) if his else EXTERN_TEMP_FALLBACK_MAX)

    def _check_extern_temp(self, t, label="TEMP="):
        lo, hi = self._extern_temp_limits()
        try:
            t = float(t)
        except (TypeError, ValueError):
            raise ValueError("%s %r ist keine Zahl" % (label, t))
        if not math.isfinite(t) or not lo <= t <= hi:
            raise ValueError(
                "%s %s °C außerhalb %s..%s °C (min_extrude_temp bzw. "
                "[extruder] max_temp−%s / [k2_safety])" % (
                    label, _n(t), _n(lo), _n(hi), _n(TEMP_LIMIT_MARGIN)))
        return t

    def _extern_material_temp(self, mat):
        """Temperatur des externen Materials -> (°C oder None, Grund).
        Handwert (TEMP=, gespeichert) vor Material-DB (kvParam.nozzle_
        temperature, gekappt auf die Obergrenze); unbekannt -> None."""
        if not mat:
            return None, ""
        if mat.get("temp"):
            return (self._check_extern_temp(mat["temp"], "gespeicherte TEMP"),
                    "Handwert TEMP=")
        key = mat.get("type")
        if key and self.material_db is not None:
            mp = self._mat(key)
            if mp.get("temp_known"):
                lo, hi = self._extern_temp_limits()
                t = min(float(mp["temp"]), hi)
                if t < lo:
                    raise ValueError(
                        "DB-Temperatur %s °C von %s unter min_extrude_temp %s "
                        "°C – TEMP=<°C> angeben" % (_n(mp["temp"]), key, _n(lo)))
                return t, "Material-DB %s" % (key,)
        return None, ""

    def _extern_material_params(self, gcmd, store_temp):
        """MATERIAL=/TYPE=/COLOR=/(TEMP=) -> (Material-dict oder None,
        geändert). MATERIAL=/TYPE= setzt ein NEUES Material (Farbe/Handwert
        zurück, wie eine neue Spule); ohne beides gilt das gespeicherte.
        TEMP= wird nur mit store_temp übernommen (LOAD_FILAMENT, KTWO_BOX_
        SET_EXTERNAL_MATERIAL; TEMP=0 = zurück auf den DB-Wert). ValueError."""
        raw_mat = gcmd.get("MATERIAL", None)
        raw_type = gcmd.get("TYPE", None)
        raw_col = gcmd.get("COLOR", None)
        if raw_mat is not None and raw_type is not None:
            raise ValueError("MATERIAL= und TYPE= nicht zusammen")
        cur = dict(self.extern_material) if self.extern_material else None
        changed = False
        if raw_mat is not None or raw_type is not None:
            new = {"type": None, "color": None, "name": None, "temp": None,
                   "origin": "hand"}
            if raw_type is not None:
                new["type"] = normalize_remain_type(raw_type)
                if not new["type"]:
                    raise ValueError("TYPE leer")
                if self.material_db is not None:
                    new["name"] = _clean_extern_name(
                        self._mat(new["type"]).get("name"))
            elif self.material_db is not None:
                key, nm = find_extern_material(self.material_db, raw_mat)
                new["type"], new["name"] = key, _clean_extern_name(nm)
            else:
                new["name"] = normalize_extern_name(raw_mat)
                if not new["name"]:
                    raise ValueError("MATERIAL leer")
            cur, changed = new, True
        if raw_col is not None:
            if cur is None:
                raise ValueError("COLOR= braucht ein Material (MATERIAL=… "
                                 "bzw. gespeichertes)")
            cur["color"] = normalize_remain_color(raw_col)
            changed = True
        if store_temp:
            t = gcmd.get_float("TEMP", None)
            if t is not None:
                if cur is None:
                    raise ValueError("TEMP= braucht ein Material – "
                                     "MATERIAL=<Name|Typ|ID> mit angeben")
                cur["temp"] = None if t == 0 else self._check_extern_temp(t)
                changed = True
        return cur, changed

    def _extern_op_temp(self, gcmd, op, mat):
        """Temperatur des Vorgangs -> (°C, Grund); ValueError ohne Wert.
        LADEN: Material (Handwert/DB). ENTLADEN: TEMP= (nur dieses
        Entladen) > gespeichertes externes Material > Düsen-Material
        (remain_material, Stock FILAMENT_RACK_SET_TEMP) – sonst Fehler."""
        if op == "ENTLADEN":
            t = gcmd.get_float("TEMP", None)
            if t is not None:
                return (self._check_extern_temp(t),
                        "TEMP= (nur dieses Entladen)")
        tm, why = self._extern_material_temp(mat)
        if tm is not None:
            return tm, why
        if op == "ENTLADEN":
            key = self.remain_material.get("type")
            if key and self.material_db is not None:
                mp = self._mat(key)
                if mp.get("temp_known"):
                    lo, hi = self._extern_temp_limits()
                    t = min(float(mp["temp"]), hi)
                    if t >= lo:
                        return t, ("Düsen-Material %s (Material-DB, Stock "
                                   "FILAMENT_RACK_SET_TEMP)" % (key,))
        if mat is None:
            raise ValueError(
                "kein externes Material gespeichert – MATERIAL=<Name|Typ|ID> "
                "angeben (z. B. MATERIAL=PLA)%s" % (
                    " oder TEMP=<°C>" if op == "ENTLADEN" else ""))
        raise ValueError(
            "Temperatur für %s unbekannt (%s) – TEMP=<°C> angeben" % (
                extern_material_label(mat),
                "nicht in der Material-DB" if self.material_db is not None
                else "keine Material-DB, [k2_box] material_database"))

    def _extern_plan_note(self):
        if self.cfg.get("allow_move"):
            return ""
        return " (PLAN: [k2_box] allow_move: False – nichts bewegt/geheizt)"

    def _extern_chain(self, gcmd, name, op):
        """Laufender externer Vorgang ``op`` (nach KTWO_BOX_EXTERN_BEGIN)."""
        ch = self.chain
        if ch.get("active") and ch.get("source") == EXTERN_CHAIN \
                and ch.get("op") == op:
            return ch
        raise gcmd.error("[k2_box] %s nur innerhalb EXTERN_%s (nach "
                         "KTWO_BOX_EXTERN_BEGIN OP=%s) – nichts ausgeführt"
                         % (name, op, op))

    def _extern_sensor_off(self):
        """Kopfsensor für die Dauer des Vorgangs sperren (wie PRE_OPT: beim
        Spülen/Schneiden/Rückzug würde Upstream sonst ein Runout melden –
        idle_timeout „Printing“ auch außerhalb eines Drucks). Zurück in
        _chain_cleanup (Erfolg wie Fehler)."""
        ch = self.chain
        if not self.cfg.get("allow_move") \
                or ch.get("sensor_was_enabled") is not None:
            return
        sens = self._obj_status("filament_switch_sensor %s"
                                % (self.cfg.get("filament_sensor"),))
        if "enabled" in sens:
            ch["sensor_was_enabled"] = bool(sens["enabled"])
            self._set_head_sensor(False)

    def cmd_source(self, gcmd):
        """KTWO_BOX_SOURCE [SOURCE=EXTERN|CFS] – Filamentquelle anzeigen bzw.
        setzen (Makros SET_FILAMENT_SOURCE SOURCE=EXTERNAL|CFS). Sendet nichts. Umschalten
        nur außerhalb eines Drucks oder in der Pause, nie während einer
        Kette/eines Box-Vorgangs."""
        raw = gcmd.get("SOURCE", None)
        if raw is None:
            gcmd.respond_info(
                "[k2_box] Filamentquelle: %s; externes Material: %s; Datei %s "
                "(%s)%s" % (self.source.upper(), extern_material_label(
                    self.extern_material), self.source_file or "aus",
                    self.source_load, (" – CFS-Aktionen gesperrt"
                                       if self.source == SOURCE_EXTERN
                                       else "")))
            return None
        try:
            new = normalize_source(raw)
        except ValueError as exc:
            raise gcmd.error("[k2_box] KTWO_BOX_SOURCE: %s" % (exc,))
        if new == self.source:
            gcmd.respond_info("[k2_box] Filamentquelle bleibt %s"
                              % (new.upper(),))
            return None
        ctl = self.controller
        why = ""
        if self.chain.get("active"):
            why = "Wechsel/Vorgang läuft (%s)" % (
                self.chain.get("tnn") or self.chain.get("op") or "Kette",)
        elif ctl is not None and ctl.busy:
            why = "Box-Vorgang läuft (busy)"
        elif self._print_active() and not self._is_paused():
            why = ("Druck läuft – nur außerhalb eines Drucks oder in der Pause "
                   "(Schlauch umstecken nur im Stillstand)")
        if why:
            raise gcmd.error("[k2_box] KTWO_BOX_SOURCE gesperrt: %s – Quelle "
                             "bleibt %s" % (why, self.source.upper()))
        self.source = new
        self._extern_tool = None
        hints = []
        head = self._head_filament()
        if new == SOURCE_EXTERN:
            text = ("Filamentquelle = EXTERN: CFS-Laden/-Entladen/-Preload, "
                    "T<n>-Wechsel aus der CFS, Runout-Nachladen und Entladen am "
                    "Druckende sind gesperrt (nichts wird an die CFS gesendet). "
                    "Externe Spule: Filament bis zur LED am Druckkopf schieben, "
                    "dann LOAD_FILAMENT [MATERIAL=…] [TEMP=…]")
            if head is True:
                hints.append("Kopfsensor meldet Filament – stammt es aus der "
                             "CFS, vorher entladen (unter SET_FILAMENT_SOURCE SOURCE=CFS)")
            if ctl is not None and ctl.print_hold and not self._print_active():
                hints.append("Box-Druckzustand (0x0d/0x0f) noch aktiv – "
                             "KTWO_BOX_PRINT_END setzt ihn zurück (auch bei "
                             "Quelle EXTERN)")
        else:
            text = ("Filamentquelle = CFS: Wechsel/Laden wieder über die CFS "
                    "(Schlauch an der CFS?)")
            if head is True:
                hints.append("Kopfsensor meldet Filament – externes Filament "
                             "vorher mit UNLOAD_FILAMENT entladen und "
                             "herausziehen, sonst blockiert es den CFS-Einzug")
        saved = self._persist_source(gcmd)
        self.extern_last = "[k2_box] %s" % (text,)
        gcmd.respond_info("[k2_box] %s%s; %s" % (
            text, "".join("\n  HINWEIS %s" % h for h in hints),
            ("gespeichert (%s)" % self.source_file) if saved else (
                "nur bis zum Neustart (filament_source_file aus)"
                if not self.source_file else "NICHT gespeichert")))
        return None

    def cmd_extern_material(self, gcmd):
        """KTWO_BOX_EXTERN_MATERIAL [MATERIAL=<Name|Typ|ID>] [TYPE=<id>]
        [COLOR=RRGGBB] [TEMP=°C|0] [CLEAR=1] – gespeichertes externes
        Material anzeigen/setzen (Stock-Display: Material + Temperatur von
        Hand). Sendet/bewegt nichts. Gesperrt während eines Vorgangs."""
        clear = bool(gcmd.get_int("CLEAR", 0))
        has = clear or any(gcmd.get(k, None) is not None
                           for k in ("MATERIAL", "TYPE", "COLOR", "TEMP"))
        if has and self.chain.get("active"):
            raise gcmd.error("[k2_box] KTWO_BOX_EXTERN_MATERIAL gesperrt: "
                             "Vorgang läuft (%s)" % (
                                 self.chain.get("op") or self.chain.get("tnn")
                                 or "Kette",))
        if clear:
            self.extern_material = None
            self._persist_source(gcmd)
        elif has:
            try:
                mat, changed = self._extern_material_params(gcmd, True)
            except ValueError as exc:
                raise gcmd.error("[k2_box] KTWO_BOX_EXTERN_MATERIAL: %s"
                                 % (exc,))
            if changed:
                self.extern_material = mat
                self._persist_source(gcmd)
        mat = self.extern_material
        try:
            t, why = self._extern_material_temp(mat)
            ttxt = ("%s °C (%s)" % (_n(t), why)) if t is not None else (
                "unbekannt – LOAD_FILAMENT braucht TEMP=<°C>" if mat else "-")
        except ValueError as exc:
            ttxt = "ungültig (%s)" % (exc,)
        gcmd.respond_info("[k2_box] Externes Material%s: %s; Temperatur %s; "
                          "Quelle %s" % (" gesetzt" if has else "",
                                         extern_material_label(mat), ttxt,
                                         self.source.upper()))
        return None

    def cmd_extern_tool(self, gcmd):
        """KTWO_BOX_EXTERN_TOOL T=<0..15> – aus T<n>/M8200 bei Quelle EXTERN
        (macros.cfg). Im laufenden Druck: erstes T<n> = kein Wechsel
        (Info), dasselbe = Info, ein ANDERES -> PAUSE mit Meldung (Datei
        verlangt einen Werkzeugwechsel, die Spule wechselt der User von
        Hand). Außerhalb eines Drucks/in der Pause: G-Code-Fehler (kein CFS-
        Wechsel). Sendet nie etwas an die CFS."""
        raw = gcmd.get("T", None)
        try:
            tnn_from_index(raw)
            i = int(str(raw).strip().lstrip("="))
        except (ValueError, TypeError) as exc:
            raise gcmd.error("[k2_box] KTWO_BOX_EXTERN_TOOL: %s" % (exc,))
        if self.source != SOURCE_EXTERN:
            gcmd.respond_info("[k2_box] T%d: Quelle CFS – KTWO_BOX_EXTERN_TOOL "
                              "ohne Wirkung (CFS-Wechsel über T%d)" % (i, i))
            return None
        state = self._obj_status("print_stats").get("state")
        if state != "printing" or self._is_paused():
            raise gcmd.error(
                "[k2_box] T%d gesperrt: Quelle ist EXTERN – kein CFS-"
                "Werkzeugwechsel (nichts gesendet, nichts bewegt). Externe "
                "Spule: UNLOAD_FILAMENT/LOAD_FILAMENT; CFS: Schlauch an die "
                "CFS, SET_FILAMENT_SOURCE SOURCE=CFS" % (i,))
        ident = self._print_ident()
        cur = self._extern_tool
        if cur is not None and cur[0] is not None and ident is not None \
                and cur[0] != ident:
            cur = None   # neuer Druck laut print_stats
        if cur is None:
            self._extern_tool = (ident, i)
            gcmd.respond_info(
                "[k2_box] T%d: Quelle EXTERN – erstes Werkzeug dieses Drucks, "
                "kein Wechsel (gedruckt wird mit der externen Spule: %s)"
                % (i, extern_material_label(self.extern_material)))
            return None
        if cur[1] == i:
            gcmd.respond_info("[k2_box] T%d: Quelle EXTERN – Werkzeug "
                              "unverändert, kein Wechsel" % (i,))
            return None
        old = cur[1]
        self._extern_tool = (ident, i)
        msg = ("[k2_box] Datei verlangt Werkzeugwechsel (T%d -> T%d), Quelle ist "
               "extern – Druck wird PAUSIERT, kein CFS-Befehl gesendet. Spule "
               "von Hand wechseln: UNLOAD_FILAMENT, Spule für T%d aufsetzen, "
               "Filament bis zur LED am Druckkopf schieben, LOAD_FILAMENT "
               "[MATERIAL=…], dann RESUME" % (old, i, i))
        self.extern_last = msg
        self._respond_error(msg)
        try:
            self._script(["PAUSE"])
        except Exception as exc:
            self._respond_error("[k2_box] PAUSE fehlgeschlagen: %s" % (exc,))
        return None

    def cmd_extern_begin(self, gcmd):
        """KTWO_BOX_EXTERN_BEGIN OP=LADEN|ENTLADEN [MATERIAL=] [TYPE=]
        [COLOR=] [TEMP=] – erster Baustein von LOAD_FILAMENT/UNLOAD_FILAMENT
        (Stock LOAD_/QUIT_MATERIAL_HEATING-Anfang). Prüft VOR jeder Bewegung:
        Quelle EXTERN, kein laufender Druck (Pause erlaubt, dann mit
        allow_move), kein offener Wechsel-Fehler, kein Box-Vorgang;
        Material/Temperatur (MATERIAL= setzt und speichert, ohne = das
        gespeicherte; TEMP= Handwert, beim Laden gespeichert, beim Entladen
        nur dieses Mal); LADEN nur, wenn der Kopfsensor Filament meldet.
        Danach: Vorgang beginnen (Kette source EXTERN), G-Code-Zustand
        sichern. Ablehnung = G-Code-Fehler, nichts bewegt/gesendet."""
        op = str(gcmd.get("OP", "") or "").strip().upper()
        if op not in EXTERN_OPS:
            raise gcmd.error("[k2_box] KTWO_BOX_EXTERN_BEGIN: OP=LADEN|ENTLADEN "
                             "fehlt (%r)" % (op,))
        name = "EXTERN_%s" % (op,)

        def _deny(text):
            raise gcmd.error("[k2_box] %s gesperrt: %s – nichts bewegt, nichts "
                             "gesendet" % (name, text))
        if self.source != SOURCE_EXTERN:
            _deny("Quelle ist CFS – erst den Schlauch der externen Spule "
                  "anstecken, dann SET_FILAMENT_SOURCE SOURCE=EXTERNAL")
        if self._print_active() and not self._is_paused():
            _deny("Druck läuft – nur außerhalb eines Drucks oder in der Pause")
        if self._print_active() and not self.cfg.get("allow_move"):
            _deny("in der Pause nur mit [k2_box] allow_move: True")
        if self.chain_error:
            _deny("Wechsel-Fehler offen (%s) – erst KTWO_BOX_ERROR_CLEAR"
                  % (self.chain_error,))
        ctl = self.controller
        if ctl is not None and ctl.busy:
            _deny("Box-Vorgang läuft (busy)")
        try:
            mat, changed = self._extern_material_params(
                gcmd, store_temp=(op == "LADEN"))
            temp, twhy = self._extern_op_temp(gcmd, op, mat)
        except ValueError as exc:
            raise gcmd.error("[k2_box] %s: %s – nichts bewegt, nichts gesendet"
                             % (name, exc))
        head = self._head_filament()
        sens = self.cfg.get("filament_sensor") or "(nicht konfiguriert)"
        if op == "LADEN":
            if head is None:
                _deny("Kopf-Filamentsensor %s liefert keinen Zustand – Laden der "
                      "externen Spule nur mit Kopfsensor" % (sens,))
            if head is False:
                _deny("kein Filament am Kopfsensor – Filament bis zur LED am "
                      "Druckkopf schieben (Schlauch der externen Spule "
                      "gesteckt?), dann LOAD_FILAMENT erneut")
        if self.chain.get("active"):
            gcmd.respond_info("[k2_box] %s: alter Vorgang %s ohne Abschluss "
                              "aufgeräumt" % (name, self.chain.get("op")
                                              or self.chain.get("tnn")
                                              or "(Kette)"))
            self._chain_cleanup(gcmd)
        if changed:
            self.extern_material = mat
            self._persist_source(gcmd)
        ext = {"key": (mat or {}).get("type"), "col": (mat or {}).get("color"),
               "temp": float(temp), "twhy": twhy,
               "label": extern_material_label(mat) if mat else twhy}
        ch = self._new_chain()
        ch.update({"active": True, "source": EXTERN_CHAIN, "op": op,
                   "extern": ext})
        self.chain = ch
        self._script(["SAVE_GCODE_STATE NAME=%s" % CHAIN_STATE_NAME])
        ch["state_saved"] = True
        rm = self.remain_material
        if op == "LADEN":
            what = ("Schacht, Spülen wie CFS-Wechsel (Vorgänger = Düsen-"
                    "Material %s/%s), Abkühlen" % (rm.get("type") or "unbekannt",
                                                  rm.get("color") or
                                                  "unbekannt"))
        else:
            what = ("Schacht, %sSchnitt, %sAbkühlen – danach Filament von "
                    "Hand herausziehen" % (
                        "" if head is False else "Heizen, ",
                        "" if head is False else "E-%s F%s, " % (
                            _n(EXTERN_RETRACT_MM),
                            _n(EXTERN_RETRACT_VELOCITY))))
        gcmd.respond_info(
            "[k2_box] %s: Material %s, %s °C (%s), Kopfsensor %s – %s%s" % (
                name, extern_material_label(mat), _n(temp), twhy,
                {True: "meldet Filament", False: "leer"}.get(head, "unbekannt"),
                what, self._extern_plan_note()))
        return None

    def cmd_extern_flush(self, gcmd):
        """KTWO_BOX_EXTERN_FLUSH – Spülen der externen Spule (Stock
        FILAMENT_RACK_FLUSH): dieselbe Rechnung wie beim CFS-Wechsel
        (_flush_plan extern), Vorgänger = remain_material, danach
        remain_material = externes Material (ohne Slot). Nur in
        LOAD_FILAMENT. Kein Box-Frame (auch keine Messrad-Prüfung)."""
        ch = self._extern_chain(gcmd, "KTWO_BOX_EXTERN_FLUSH", "LADEN")

        def body(gcmd):
            self._extern_sensor_off()
            self._flush_exec(gcmd, None, "", None, None,
                             extern=dict(ch.get("extern") or {}))
            self.head_cut = False   # neues Filament durch die Düse gespült
        return self._run_step(gcmd, "EXTERN_FLUSH", body)

    def cmd_extern_heat(self, gcmd):
        """KTWO_BOX_EXTERN_HEAT – Entladen: bei Filament am Kopfsensor auf
        die Materialtemperatur heizen (Stock QUIT_MATERIAL_HEATING ->
        FILAMENT_RACK_SET_TEMP). Kopfsensor leer -> nicht heizen (Stock),
        unbekannt -> heizen. Sperrt den Kopfsensor für den Vorgang."""
        ch = self._extern_chain(gcmd, "KTWO_BOX_EXTERN_HEAT", "ENTLADEN")

        def body(gcmd):
            self._extern_sensor_off()
            head = self._head_filament()
            if head is False:
                gcmd.respond_info("[k2_box] EXTERN_HEAT: Kopfsensor meldet kein "
                                  "Filament – kein Heizen (Stock); der Schnitt "
                                  "folgt trotzdem")
                return
            if head is None:
                gcmd.respond_info("[k2_box] EXTERN_HEAT: Kopfsensor ohne "
                                  "Zustand – heize vorsichtshalber")
            self._heat(gcmd, float((ch.get("extern") or {})["temp"]))
        return self._run_step(gcmd, "EXTERN_HEAT", body)

    def cmd_extern_retract(self, gcmd):
        """KTWO_BOX_EXTERN_RETRACT – Entladen nach dem Schnitt: bei Filament
        am Kopfsensor E-10 F360 + M400 (Stock QUIT_MATERIAL_RETRUDE_
        MATERIAL). Kopfsensor leer -> nichts."""
        self._extern_chain(gcmd, "KTWO_BOX_EXTERN_RETRACT", "ENTLADEN")

        def body(gcmd):
            if self._head_filament() is False:
                gcmd.respond_info("[k2_box] EXTERN_RETRACT: Kopfsensor leer – "
                                  "kein Rückzug (Stock)")
                return
            self._move(gcmd, "Extruder-Rückzug (Stock QUIT_MATERIAL: E-%s F%s)"
                       % (_n(EXTERN_RETRACT_MM), _n(EXTERN_RETRACT_VELOCITY)),
                       gcode_extruder_move(-EXTERN_RETRACT_MM,
                                           EXTERN_RETRACT_VELOCITY) + ["M400"])
        return self._run_step(gcmd, "EXTERN_RETRACT", body)

    def cmd_extern_end(self, gcmd):
        """KTWO_BOX_EXTERN_END – letzter Baustein von LOAD_FILAMENT/
        UNLOAD_FILAMENT (Stock LOAD_/QUIT_MATERIAL_END): Abkühlen wie Stock
        SET_COOL_TEMP (Pause -> Ziel vor dem Vorgang bzw. 140 °C, sonst
        M104 S0; nur wenn der Vorgang geheizt hat), Kopfsensor und G-Code-
        Zustand zurück, Vorgang beenden. Nach einem Fehler (Pause) nur
        aufräumen."""
        ch = self.chain
        if not (ch.get("active") and ch.get("source") == EXTERN_CHAIN):
            gcmd.respond_info("[k2_box] EXTERN_END: kein externer Lade-/"
                              "Entladevorgang aktiv")
            return None
        op = ch.get("op") or "?"
        ext = ch.get("extern") or {}
        if ch.get("skip") or self.chain_error:
            gcmd.respond_info("[k2_box] EXTERN_%s nach Fehler beendet (keine "
                              "Bewegung) – %s" % (op, ch.get("skip_reason")
                                                  or self.chain_error))
        else:
            try:
                if ch.get("temp_changed"):
                    if self._is_paused():
                        t = ch.get("orig_target")
                        t = float(t) if t else ENDING_COOL_TEMP
                        self._move(gcmd, "Pausentemperatur (Stock SET_COOL_"
                                   "TEMP)", ["M104 S%s" % _n(t)], wrap=False)
                    else:
                        self._move(gcmd, "Heizung aus (Stock SET_COOL_TEMP)",
                                   ["M104 S0"], wrap=False)
                    ch["temp_changed"] = False
                if op == "LADEN":
                    rm = self.remain_material
                    text = ("Externe Spule geladen (%s); Düsen-Material %s/%s"
                            % (ext.get("label") or "?", rm.get("type") or "-",
                               rm.get("color") or "-"))
                else:
                    text = ("Externe Spule entladen – Filament jetzt von Hand "
                            "herausziehen")
                self.extern_last = "[k2_box] %s" % (text,)
                gcmd.respond_info("[k2_box] %s%s" % (
                    text, self._extern_plan_note()))
            except Exception as exc:
                self._fail(gcmd, "EXTERN_END", self._exc_text(exc))
        if self.chain.get("active"):
            self._chain_cleanup(gcmd)
        return None

    def cmd_extern_rfid(self, gcmd):
        """KTWO_EXTERN_RFID – RFID-Etikett der externen Spule lesen (Stock
        filament_rack send_data: 0x11 [02] bei Auslösung !PA5). Nur mit
        [k2_box] extern_rfid: True (Default AUS). Genau EIN lesender Frame
        an 0x11 (fun 0x02, ohne Daten, retries=0, 0,5 s), über den Sende-
        schutz serial485.check_tx_frame. Gültiges Etikett -> gespeichertes
        externes Material = Material-ID + Farbe (Handwert TEMP= verworfen).
        Gemeldet/gespeichert werden NUR Material-ID und Farbe."""
        if not self.cfg.get("extern_rfid"):
            gcmd.respond_info("[k2_box] KTWO_EXTERN_RFID: aus ([k2_box] "
                              "extern_rfid: False) – nichts gesendet")
            return None
        if self.chain.get("active"):
            gcmd.respond_info("[k2_box] KTWO_EXTERN_RFID: Vorgang läuft – "
                              "Etikett danach lesen (nichts gesendet)")
            return None
        try:
            ctl = self._ensure_controller()
        except Exception as exc:
            gcmd.respond_info("[k2_box] KTWO_EXTERN_RFID: kein Bus: %s" % (exc,))
            return None
        if ctl.busy:
            gcmd.respond_info("[k2_box] KTWO_EXTERN_RFID: Box-Vorgang läuft – "
                              "Etikett danach lesen (nichts gesendet)")
            return None
        raw = data = None
        try:
            if _S485 is not None:
                _S485.check_tx_frame(EXTERN_RFID_ADDR, EXTERN_RFID_FUN, [])
            _a, _st, _f, data, raw = ctl.bus.transact_full(
                EXTERN_RFID_ADDR, EXTERN_RFID_FUN, [],
                timeout=EXTERN_RFID_TIMEOUT, retries=0,
                status=EXTERN_RFID_STATUS, expect_fun=EXTERN_RFID_FUN,
                expect_addr=EXTERN_RFID_ADDR)
        except TimeoutError:
            self.extern_rfid_last = "keine Antwort vom RFID-Leser 0x11"
        except Exception as exc:
            # Ausnahmetext nicht ausgeben (kann Rohdaten enthalten)
            self.extern_rfid_last = "Lesefehler (%s)" % (
                exc.__class__.__name__,)
        tag = None
        if raw is not None or data is not None:
            tag = extern_rfid_tag(raw_frame=raw) if raw else \
                extern_rfid_tag(data=data)
            if tag is None:
                self.extern_rfid_last = "Etikett nicht lesbar/leer"
        if tag is None:
            gcmd.respond_info("[k2_box] KTWO_EXTERN_RFID: %s – gespeichertes "
                              "externes Material bleibt (%s)" % (
                                  self.extern_rfid_last, extern_material_label(
                                      self.extern_material)))
            return None
        mat_id, col = tag
        name, note = None, ""
        if self.material_db is not None:
            mp = self._mat(mat_id)
            if mp.get("known"):
                name = _clean_extern_name(mp.get("name"))
            else:
                note = (" – nicht in der Material-DB (LOAD_FILAMENT braucht "
                        "dann TEMP=<°C>)")
        self.extern_material = {"type": mat_id, "color": col, "name": name,
                                "temp": None, "origin": "rfid"}
        self._persist_source(gcmd)
        self.extern_rfid_last = "Material %s, Farbe %s" % (mat_id, col)
        gcmd.respond_info("[k2_box] Externe Spule: Etikett gelesen – Material "
                          "%s%s, Farbe %s gespeichert%s" % (
                              mat_id, (" (%s)" % name) if name else "", col,
                              note))
        return None


def _clean_extern_name(nm):
    """DB-Name -> zulässige Bezeichnung (normalize_extern_name) oder None."""
    if not isinstance(nm, str):
        return None
    s = re.sub(r"[^0-9A-Za-z +._()/-]", "", nm).strip()
    s = s.lstrip(" +._()/-")[:40].strip()
    try:
        return normalize_extern_name(s)
    except ValueError:
        return None


def load_config(config):
    """Klipper-Einstieg [k2_box] (Upstream-Muster)."""
    return PrinterK2Box(config)
