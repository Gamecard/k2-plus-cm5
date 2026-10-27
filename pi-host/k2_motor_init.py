#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_motor_init.py – Klipper-Zusatzmodul [k2_motor] für K2-Plus-Motoren.

Offener Ersatz für Crealitys ``motor_control_wrapper.so`` (Stock, armhf,
proprietär, mit Bugs). Initialisiert die Closed-loop-Motoren X/Y/Z/Z1/E auf
einem Raspberry-Pi-Host unter UPSTREAM-Klipper – so wie Stock, aber ohne
dessen Bugs und mit sicherem Default (nur lesen).

Grundlagen (nur gelesen, nichts gesendet):
- Analyse des Stock-Motor-Inits (maßgeblich: Init-Ablauf P0–P9 aus
  ``set_motor_pin`` @0xf87d8, Zeiten, Fehler-Keys 798–803, Param-Abgleich
  ``verify_sys_param`` @0xc163c mit isclose abs_tol 1e-6, Strapping
  ``Adresse = 0x81 + 2·STEP + DIR``)
- Analyse der GPIO-Phase (MCU-Befehle: ``set_digital_out``,
  ``config_digital_out``/``update_digital_out``, ``query_gpio_status``,
  ``config_transparent``/``transparent_send``/``transparent_response``,
  ``[duplicate_pin_override]``)
- nachbau/motor_init.py (Plan/Trockenlauf/compare – LOGIK-VORLAGE, hier als
  eigenständiger Port wiederverwendet statt neu erfunden; gleiche Phasen,
  gleiche Reihenfolge/Payloads, gleiche Pausen/Timeouts/Versuche)
- nachbau/motor.py (Frame-Builder/Decoder, Param-Tabelle 227 Einträge)
- nachbau/serial485.py (build_frame, parse_frame, CRC-8/ATM)
- Live-Log 15.10.2026 (Boot; transparent-
  Frames inkl. F7+CRC; RS485-Wire ohne Head/CRC; Extruder antwortet über die
  Düsen-MCU unter 0x81)

Ablauf (wie Stock, P0–P9):
  P0 transparent-Kanal zur Düsen-MCU (``config_transparent`` + Lookup
     ``transparent_send`` – erst nach dem Connect möglich)
  P1 GPIO-Strapping: Dir/Step-Pegel laut Config über die Main-MCU setzen
     (VOR dem Broadcast!), optional per ``query_gpio_status`` zurücklesen
     (wenn Befehl vorhanden), 0,2 s, sonst key799-analog
  P2 Adress-Runde: ``FF 0E [01]`` auf RS485 + transparent (keine Antwort
     erwartet), 0,1 s, je Motor ``0E [02]`` (je 0,05 s davor, E transparent
     0x81); bis ``retries`` Runden (Default 4), 1,0 s Pause; sonst
     key798-analog (Shutdown NUR per Option)
  P3 „All motors are normal“
  P4 Stall-Mode ``11 [02]`` an Z/Z1 (RS485) + E (transparent), je ≤3
     Versuche (key801/802-analog); X/Y erst beim Homing (wie Stock)
  P5 Schutzabfrage ``0C [0b]`` an alle (E transparent), 8 B dekodiert
  P6 Fehler löschen ``0C [05]`` an alle, UNBEDINGT (live nie beantwortet)
  P7 GPIO-Restore: alle Dir/Step physisch 0, 0,1 s, zurücklesen
     (key800-analog), 1,0 s – läuft auch im Fehlerfall
  P8 Param-Abgleich je Motor mit Stall-Pin: IDs 9/10/25 lesen; IDs
     ``configurable ∩ Config`` lesen ``06 [02,id]`` (≤3), Vergleich
     ``isclose(rel_tol=1e-9, abs_tol=1e-6)``, bei Abweichung ``06 [01,id]+<f``
     (≤3, Echo prüfen), am Ende ``06 [03]`` je Motor. ID 226 wird NIE
     gesendet (reiner Host-Parameter, Motor antwortet 0 B). Nicht-Float-IDs
     werden nur gelesen (Schreibformat unbelegt).
  P9 bereit: Event ``k2_motor:ready`` (statt Stock-``auto_addr:start``;
     CFS-Auto-Addr kann darauf warten – gleiche Reihenfolge wie Stock:
     gemeinsame ttyS5-Queue erst nach der Motor-Init)

Stock-Bug FIX (belegt): Der Extruder-Param-Abgleich läuft KORREKT
über TransparentTransport an 0x81 auf dem Düsenbus – NIE als 0x85 auf RS485
(Stock adressiert ``verify_sys_param`` für E als num=5 → 0x85 auf ttyS5, wo
niemand antwortet, ≈9 s Timeouts ohne Wirkung). Vorbild ist der ungenutzte
``motor_extruder_sys_param_init``. Dieses Modul sendet NIEMALS an 0x85
(Guard im Code + Test).

Sicherheit: Schreibende Bus-Schritte (Broadcast, Stall-Mode, 0C [05],
Param-Schreiben, 06 [03]) sowie das GPIO-Setzen laufen NUR mit
``allow_write: True`` (Default False → nur lesen + Plan ausgeben). Der
G-Code ``KTWO_MOTOR_INIT`` ohne ``ALLOW_WRITE=1`` gibt ebenfalls nur den Plan
aus. Der Broadcast läuft zusätzlich nur nach gesetzten Strapping-Pegeln
(P1) – ohne Pins bekämen alle Motoren dieselbe Adresse.

Klipper-Einbindung (Dateien kopieren, s. pi-host/README.md): ``k2_transport.py`` +
``k2_motor_init.py`` nach ``klippy/extras/`` (als ``k2_motor.py`` bzw. als
Paket), ``[k2_motor]`` in printer.cfg. Alle MCU-Zugriffe sind in den kleinen
Adapterklassen gekapselt (``KlippyGpio``, ``KlippyTransparentAdapter`` aus
k2_transport), damit Tests mit Fake-MCU/Fake-RS485 laufen (s.
test_k2_motor.py). Ohne Klipper importierbar (reine Logik + Fakes).

G-Codes:
- ``KTWO_MOTOR_INIT [DRY_RUN=1] [ALLOW_WRITE=1]`` – Init-Lauf (Default:
  Plan ausgeben, nichts senden)
- ``KTWO_MOTOR_STATUS`` – liest je Motor Version (0x0F), Schutz (0x0C [0b]
  dekodiert) und Feedback idx 5/6 (0x08, pos_fdb/spd_fdb) – nur lesend.

Stall-Überwachung (18.10.2026, README §11): ``stall_monitor: off|report|act``
(Default report), buttons auf motor_<m>_stall, ``KTWO_MOTOR_CLEAR [MOTOR=…]``
(0C [05], allow_write), Status ``printer.k2_motor.stall_monitor``.

Sensorless-Homing X/Y (16.10.2026 spät, config-pi/README.md; Frames
live belegt, s. Konstanten STALL_MODE_ENDSTOP ff.):
- ``KTWO_MOTOR_HOMING PHASE=BEGIN|END`` – Stock-Folge vor/nach G28 X/Y:
  BEGIN = 0x11 [01] an x, y + ID 70 (pos_kp) = ``homing_pos_kp`` (300) an x;
  END = 0x11 [02] an x, y + 0C [0b] an alle + ID 70 = Config-Kp (100) an x.
  BEGIN-Fehler -> Rückbau + G-Code-Fehler (G28 bricht ohne Bewegung ab);
  END läuft immer komplett, Fehler danach als G-Code-Fehler. Bricht ein
  G-Code zwischen BEGIN und END ab (Ereignis ``gcode:command_error``),
  setzt das Modul X/Y selbst zurück.
- ``KTWO_MOTOR_STALL_MODE [MOTOR=x|y|xy|z|z1] [DATA=1|2]`` – ohne DATA lesen
  (0x11 [04]), mit DATA setzen (nur x/y).
- ``KTWO_MOTOR_SYS_PARAM MOTOR=x ID=<n> [VALUE=<f>]`` – ohne VALUE lesen
  (0x06 [02,id]), mit VALUE flüchtig schreiben (0x06 [01,id]+<f, nur
  SYS_PARAM_WRITABLE_IDS, kein 06 [03]).
- ``KTWO_MOTOR_PROTECTION [STRICT=1]`` – 0C [0b] an alle (lesend).
Alle schreibenden Varianten NUR mit ``allow_write: True`` in der Config
(kein G-Code-Override), vorher ``toolhead.wait_moves()``; Fehler = G-Code-
Fehler, nie Shutdown. Status für Makros: ``printer.k2_motor`` mit
``allow_write``, ``homing_active``, ``stall_mode``, ``homing_pos_kp``,
``last_error``, ``cut_cal``.

Schnittpositions-Kalibrierung (16.10.2026 Nacht, config-pi/README.md;
Frames live belegt, s. Konstanten CUT_CAL_*):
- ``KTWO_MOTOR_CUT_CAL [DRY_RUN=1]`` – Ersatz für Stock ``MOTOR_CHECK_CUT_POS``
  samt Kp-Rahmen des Stock-Makros ``CALIBRATE_CUT_POS``: ID 70 = 300 an x,
  0x11 [04] lesen, 0x11 [03] an x, Anstoß-Folge gegen den Schneidhebel
  (X-Endstop PB11 = Stall-Pin von Motor x), ``cut_pos_x`` = 2. Treffer +
  ``cut_pos_offset`` -> ``configfile.set('k2_box', 'cut_pos_x')`` (nur
  vorgemerkt, SAVE_CONFIG macht der Nutzer bzw. das Makro), 0x11 [02], Kp
  zurück. Nur mit ``allow_write: True`` UND ``_PI_FREIGABE allow_move``;
  Rückbau (Mode 2, Kp, Beschleunigung, Kopf weg vom Hebel) IMMER.

Konsole (23.10.2026, k2_konsole): Der Auto-Init läuft bei klippy:connect –
dort ausgegebene Zeilen kamen nie in Mainsail an. Jetzt: Fehler des Init
(z. B. key798, transparent-Setup, Bus fehlt) als ``!! [k2_motor] …`` und
eine Abschlusszeile „Motor-Init (Start): Status …“, gepuffert bis kurz nach
„ready“; die Info-Zeilen des Auto-Init nur ins klippy.log (manueller
KTWO_MOTOR_INIT zeigt wie bisher alle Zeilen). Stall-Fehlercodes und
Aktionen bei Fehlern (M84/Shutdown/PAUSE) rot, reine Warnungen ``//``.
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
# sys.path (unter Klipper = klippy/extras/, verdeckte "statistics" u. a.);
# k2_transport relativ (Klipper-Paket extras), Fallback absolut (Tests).
try:
    from . import k2_transport as _K2T
except ImportError:
    try:
        import k2_transport as _K2T
    except ImportError as _e:  # pragma: no cover
        raise ImportError("k2_transport.py wird benötigt (gleicher Ordner): %s" % _e)
try:
    from . import k2_konsole as _K2K
except ImportError:
    import k2_konsole as _K2K
Rs485Transport = _K2T.Rs485Transport
TransparentTransport = _K2T.TransparentTransport
KlippyTransparentAdapter = _K2T.KlippyTransparentAdapter

# Nachbau-Helfer optional (für Decoder-Vergleich, kein Muss – unten vendored).
try:
    _M = _K2T.import_nachbau("motor")
    _HAVE_MOTOR = True
except Exception:
    _M = None
    _HAVE_MOTOR = False

# =====================================================================
# Konstanten (Port von nachbau/motor_init.py + motor.py, Belege dort)
# =====================================================================

NUM_BY_NAME_INIT = {"x": 1, "y": 2, "z": 3, "z1": 4, "e": 1}

# Gemeinsamer RS485-Bus (Abschnitt/Objekt aus k2_485.py)
BUS_SECTION = "k2_485"
MOTOR_NAME_TO_NUM = {"x": 1, "y": 2, "z": 3, "z1": 4, "e": 5}
VERIFY_ORDER = ("x", "y", "z", "z1", "e")
PIN_MOTORS = ("x", "y", "z", "z1")
ALWAYS_READ = (9, 10, 25)
HOST_ONLY_IDS = (226,)
ABS_TOL = 1e-6
REL_TOL = 1e-9

BROADCAST_ADDR = 0xFF
TRANSPARENT_ADDR = 0x81
STOCK_E_ADDR = 0x85  # Stock-Fehlgriff – wird NIE gesendet (Guard + Test).

FUNC_SYS_PARAM = 0x06
FUNC_PROTECTION = 0x0C
FUNC_ADDR = 0x0E
FUNC_VERSION = 0x0F
FUNC_SET_MODE = 0x11
FUNC_VARIABLE = 0x08

SUB_SYS_WRITE = 0x01
SUB_SYS_READ = 0x02
SUB_SYS_UPDATE = 0x03
SUB_PROT_CLEAR = 0x05
SUB_PROT_CHECK = 0x0B
SUB_ADDR_SET = 0x01
SUB_ADDR_READ = 0x02
SUB_VERSION_GET = 0x00
STALL_MODE_EMERGENCY = 0x02

# --- Sensorless-Homing X/Y (Stock sensorless.cfg [homing_override]) ----------
# Belege (Live-Log 16.10.2026, G28 X Y 14:06:21–14:06:31;
# G28 14:08-14:09 / 14:21-14:22 identisch):
#   vor dem Homing  MOTOR_STALL_MODE DATA=1   -> 81 04 00 11 01, 82 04 00 11 01
#                   (Echo f7 8x 04 00 11 01 1d)
#                   MOTOR_SYS_PARAM NUM=1 DATA=1 ID=70 PARAMS=300 PARAMS_TYPE=float
#                   -> 81 09 00 06 01 46 00 00 96 43 (Echo f7 81 07 00 06 00 00 96 43 09)
#   nach dem Homing MOTOR_STALL_MODE DATA=2   -> 81/82 04 00 11 02 (Echo ... 02 14)
#                   MOTOR_CHECK_PROTECTION_AFTER_HOME DATA=11 -> 0C [0b] an
#                   81, 82, 83, 84 + E transparent (f7 81 04 00 0c 0b 95)
#                   MOTOR_SYS_PARAM NUM=1 ... PARAMS=100 -> 81 09 00 06 01 46 00 00 c8 42
# KEIN 06 [03] (Übernahme) und KEINE Pause zwischen den Frames (Stock-Abstand
# ~20 ms = Bus-Laufzeit). DATA=1/2 gehen NUR an X/Y (0x81/0x82), Z/Z1 bleiben
# auf ihrem Boot-Modus 2 (alle Logs 05.–16.10.). ID 70 =
# controller_pos_loop_pid_param_kp, NUM=1 -> nur Motor x (0x81); Motor y hat
# laut motor_control.cfg ohnehin Kp 300 -> beim Homing beide CoreXY-Motoren 300.
# Hilfetext Stock (Motor-Wrapper-Analyse): Stall-Mode 1 = Endschalter-/
# Blockiersignal (PB11/PB12), 2 = Notfall-Schutzausgang.
STALL_MODE_ENDSTOP = 0x01
STALL_MODES_SETTABLE = (STALL_MODE_ENDSTOP, STALL_MODE_EMERGENCY)  # 03 nur Cut-Check
SUB_STALL_GET = 0x04       # 0x11 [04] -> [mode, 00] (Live-Log 2026-10-12)
STALL_SET_MOTORS = ("x", "y")      # Stock MOTOR_STALL_MODE: nur 0x81/0x82
STALL_READ_MOTORS = ("x", "y", "z", "z1")
SYS_PARAM_MOTORS = ("x", "y", "z", "z1")  # RS485; E (transparent) hier nicht
HOMING_PARAM_ID = 70       # controller_pos_loop_pid_param_kp
HOMING_PARAM_MOTORS = ("x",)  # Stock NUM=1
HOMING_POS_KP_DEFAULT = 300.0  # Stock PARAMS=300 (sensorless.cfg:145)
HOMING_POS_KP_RESTORE_DEFAULT = 100.0  # Stock PARAMS=100, sonst Config-Wert
# Schreibbar per KTWO_MOTOR_SYS_PARAM: nur die IDs, die Stock per
# [motor_control] setzt (SYS_PARAM_STOCK_CONFIGURED ohne Host-ID 226).
SYS_PARAM_WRITABLE_IDS = (70, 86, 87, 102, 103, 116, 118, 119, 120, 121, 187, 188)

# --- Schnittpositions-Kalibrierung (Ersatz Stock CALIBRATE_CUT_POS) ----------
# Live-Beleg: Display-Kalibrierung 16.10.2026 22:03:17–22:04:24 (Log-Uhr),
# Live-Mitschnitt (Zeilennummern Z. = Mitschnitt):
#   Z.945  81 09 00 06 01 46 00 00 96 43 -> f7 81 07 00 06 00 00 96 43 09 (ID 70 = 300,
#          Stock-Makro MOTOR_SYS_PARAM ... PARAMS=300 vor MOTOR_CHECK_CUT_POS)
#   Z.957  81 04 00 11 04 -> f7 81 05 00 11 02 00 0e  (Stall-Mode lesen: 2)
#   Z.981  81 04 00 11 03 -> f7 81 04 00 11 03 13     (Stall-Mode 3 an x)
#   22:03:18,6–22:04:22,4 Suchfahrt: KEIN Frame an 0x81; Treffer nur als Host-
#          Meldung "motor_x 81 check_protection_code" (22:04:18,404 + 22:04:20,246)
#          = Stall-Pin-Ereignis (motor_x_stall PB11), kein Bus-Polling
#   Z.1471 "final pos:[200.0, 200.0, 175.0, 86.19] x:-8.2 cut_pos_offset:0.4
#          cut_pos:-8.2+0.4=-7.79…" (Ausgangsposition X 200 / Y 200 = cut_pos_y)
#   Z.1472 save_config: set [box] cut_pos_x = -7.80   (Format %.2f), Z.1474 SAVE_CONFIG
#   Z.1476 81 04 00 11 02 -> f7 81 04 00 11 02 14     (zurück auf den gelesenen Wert 2)
#   Z.1489 81 09 00 06 01 46 00 00 c8 42 -> … c8 42 d4 (ID 70 = 100, Stock-Makro)
# Zeitbild der Suchfahrt (belegt): 2 Treffer 1,84 s auseinander, ~60 s für
# X -5,0 … -8,2 = 33 Positionen à 1,84 s -> Anstoßen in 0,1-mm-Schritten ab
# box.cfg pre_cut_cal_pos_x (-5), Ergebnis = 2. Treffer (-8,2) + 0,4. Deckt
# sich mit der Vanilla-Rekonstruktion (motor_control.py MotorCutCalibration:
# Park X 50 / Anstoß F10000 / Park F12000 / Ausgang X 200 – Werte dort
# ANNAHME, nur X 200 live belegt). REA (motor_check_cut_pos @0xe270c):
# SET_VELOCITY_LIMIT ACCEL=25000; "Mode 4" dort = 0x11 [04] LESEN (live).
CUT_CAL_MODE = 0x03                # 0x11 [03] an x (live Z.981)
CUT_CAL_MOTOR = "x"                # nur 0x81 (Stall-Pin PB11 = X-Endstop)
CUT_CAL_SECTION = "k2_box"         # configfile.set-Ziel (Stock: [box])
CUT_CAL_OPTION = "cut_pos_x"
CUT_CAL_FORMAT = "%.2f"            # live "cut_pos_x = -7.80"
CUT_CAL_EPS = 1e-6
# Stock-Werte (box.cfg / motor_control.cfg / gcode_macro.cfg:796 / Vanilla)
CUT_CAL_DEFAULTS = {
    "start_x": -5.0,        # box.cfg pre_cut_cal_pos_x
    "step": 0.1,            # Zeitbild live (s. oben)
    "x_min": -9.5,          # box.cfg check_cut_pos_x_min (Vorrang: [k2_box])
    "x_max": -5.5,          # box.cfg check_cut_pos_x_max (Vorrang: [k2_box])
    "y": 200.0,             # box.cfg cut_pos_y (Vorrang: [k2_box]); live Y 200
    "offset": 0.4,          # motor_control.cfg cut_pos_offset
    "park_x": 50.0,         # Vanilla CUT_CAL_PARK_X (ANNAHME)
    "exit_x": 200.0,        # live final pos X 200
    "probe_speed": 10000.0 / 60.0,  # Vanilla F10000 (ANNAHME)
    "park_speed": 12000.0 / 60.0,   # Vanilla F12000 (ANNAHME)
    "accel": 25000.0,       # REA SET_VELOCITY_LIMIT ACCEL=25000
    "settle": 0.1,          # Nachlauf-Prüfung Stall-Pin (ANNAHME)
    "pos_kp": 300.0,        # gcode_macro.cfg:796 PARAMS=300
}
# HARTE Grenzen (nicht per Config aufweichbar). X: nie tiefer als die Stock-
# position_min -10 und nie über 0 (Hebel liegt bei negativem X).
CUT_CAL_X_HARD_MIN = -10.0
CUT_CAL_X_HARD_MAX = 0.0
CUT_CAL_LIMITS = {          # key: (min, max)
    "start_x": (CUT_CAL_X_HARD_MIN, CUT_CAL_X_HARD_MAX),
    "step": (0.05, 0.5),
    "x_min": (CUT_CAL_X_HARD_MIN, CUT_CAL_X_HARD_MAX),
    "x_max": (CUT_CAL_X_HARD_MIN, CUT_CAL_X_HARD_MAX),
    "y": (50.0, 350.0),
    "offset": (0.0, 2.0),
    "park_x": (0.0, 100.0),
    "exit_x": (0.0, 340.0),
    "probe_speed": (5.0, 200.0),   # Anstoß: max 200 mm/s (Stock ~167)
    "park_speed": (5.0, 250.0),
    "accel": (500.0, 25000.0),     # nie über Stock 25000 (und nie über max_accel)
    "settle": (0.0, 0.5),
    "pos_kp": (1.0, 10000.0),
}
CUT_CAL_MAX_PROBES = 100       # harte Obergrenze Anstöße (Stock: 46)
CUT_CAL_MIN_PARK_GAP = 5.0     # park_x mind. 5 mm vor start_x
CUT_CAL_OPTIONS = {            # [k2_motor]-Option -> Parameter
    "cut_cal_start_x": "start_x", "cut_cal_step": "step",
    "cut_cal_park_x": "park_x", "cut_cal_exit_x": "exit_x",
    "cut_cal_probe_speed": "probe_speed", "cut_cal_park_speed": "park_speed",
    "cut_cal_accel": "accel", "cut_cal_settle": "settle",
    "cut_cal_pos_kp": "pos_kp", "cut_pos_offset": "offset",
}

# Stall-/Notfall-Überwachung im Betrieb (18.10.2026): die fünf Notfall-Leitungen per Upstream-[buttons] (ereignisgesteuert,
# MCU tastet alle 2 ms ab). Stock: button_stall_pin_init @MC 0x21758.
STALL_MONITOR_MODES = ("off", "report", "act")
STALL_MONITOR_DEFAULT = "report"     # Einführungsphase: nur melden
STALL_AXIS_MOTORS = ("x", "y", "z", "z1")
STALL_HOMING_MOTORS = ("x", "y")     # Stock is_homing nur in _x/_y-Handler
STALL_GUARD_AFTER = 0.05             # Stock MOTOR_STALL_MODE DATA=2: pause +0,05 s
STALL_READ_TRIES = 2                 # 0C [0b] je Flanke (jeder Fehlversuch gemeldet)
DUP_PIN_SECTION = "duplicate_pin_override"
TIMEOUT_ADDR = 0.05
TIMEOUT_STALL = 1.0
TIMEOUT_PROTECTION = 0.05
TIMEOUT_SYS_PARAM = 1.0
TIMEOUT_VERSION = 1.0
TIMEOUT_VARIABLE = 1.0
TIMEOUT_TRANSPARENT = 2.0

PAUSE_AFTER_GPIO = 0.2
PAUSE_AFTER_BROADCAST = 0.1
PAUSE_BEFORE_ADDR = 0.05
PAUSE_ROUND = 1.0
PAUSE_AFTER_RESTORE = 0.1
PAUSE_RESTORE_DONE = 1.0
PAUSE_STALL_RETRY = 0.05

STALL_TRIES = 3
PARAM_READ_TRIES = 3
PARAM_WRITE_TRIES = 3
GPIO_RESEND_TRIES = 3

WRITING_ARTS = ("broadcast", "stall", "clear", "param_write", "param_update")

# Fehlertexte analog key798–803 (Stock-Codes beibehalten für Nachvollzug;
# Shutdown NUR per Option shutdown_on_failure, sonst nur Fehlermeldung).
ERR_KEY798 = ("[key798] Motor-Verbindung fehlgeschlagen, max. Retry-Anzahl "
              "(retries=%d) überschritten. Fehlend: %s. "
              "Stall-/Param-Phase entfallen.")
ERR_KEY799 = ("[key799] GPIO-Prüfung (Strapping) fehlgeschlagen: Pin %s "
              "erwartet phys=%d, gelesen=%s (%d/%d Versuche).")
ERR_KEY800 = ("[key800] GPIO-Restore-Prüfung fehlgeschlagen: Pin %s "
              "erwartet phys=0, gelesen=%s.")
ERR_KEY801 = ("[key801] Stall-Mode (Z/Z1) fehlgeschlagen: Motor %s "
              "antwortet nicht auf 0x11 [02] (%d Versuche).")
ERR_KEY802 = ("[key802] Stall-Mode (E) fehlgeschlagen: Extruder antwortet "
              "nicht auf 0x11 [02] via transparent (%d Versuche).")
# key803 gehört zum NICHT genutzten zweiten Param-Pfad (nur Doku, kein Code).

# --- Param-Tabelle (vendored aus motor.py SYS_PARAM_NAMES, 227 IDs) -----------
# Namen identisch zu motor.SYS_PARAM_NAMES (inkl. Tippfehler standar/calibtate/
# parma); Index = Param-ID. Quelle: nachbau/motor.py (live flash_param-Enum).
_SYS_PARAM_NAMES = (
    "param_flash_param_version", "param_boot_key",
    "param_flash_key_write_retries_num", "param_system_startup_delay_ms",
    "param_pwm_period_s", "param_task_period_s", "param_use_default_param",
    "param_singleturn_control_mode",
    "param_elec_angle_calibration_standar_deg", "param_elec_offset",
    "param_phase_order_invert", "param_emergency_pin_mode", "param_stall_mode",
    "param_stall_cur_A", "param_stall_pos_err_rad", "param_calibration_dir",
    "param_encoder_cal_count_max", "param_encoder_read_count",
    "param_delay_ms_per_step", "param_delay_ms_per_read", "param_ud_cal_set",
    "param_motion_dir", "param_encoder_calibtate_official_cal_count_max",
    "param_encoder_calibtate_official_ud_cal_set",
    "param_encoder_calibration_round_count", "param_elec_offset_err_deg",
    "driver_board_param_power_supply_V", "driver_board_param_adc_ref_V",
    "driver_board_param_amp_gain",
    "driver_board_param_power_voltage_sample_gain",
    "driver_board_param_R_of_current_sample_ohm",
    "driver_board_param_adc_calibration_start_index",
    "driver_board_param_adc_calibration_buffer_size",
    "driver_board_param_led_toggle_period_s", "driver_board_param_current_dir",
    "driver_board_param_pwm_output_en", "driver_board_param_mos_Ron",
    "driver_board_param_mos_deadtime", "driver_board_param_mos_rise_fall_time",
    "driver_board_param_mos_max_peak_current",
    "driver_board_param_mos_max_continuous_current", "motor_param_R",
    "motor_param_L", "motor_param_Ke", "motor_param_Kt", "motor_param_mass",
    "motor_param_J", "motor_param_B", "motor_param_stepping_angle_degree",
    "motor_param_phase_nums", "motor_param_pole_pair",
    "motor_param_power_supply", "motor_param_rated_voltage",
    "motor_param_rated_current", "motor_param_maximum_holding_torque",
    "encoder_param_direction", "encoder_param_encoder_bit",
    "encoder_param_encoder_max_pulse", "encoder_param_fake_encoder_en",
    "encoder_param_fake_encoder_step", "mcu_param_sysclk_hz",
    "mcu_param_HSE_hz", "mcu_param_uart_baudrate",
    "mcu_param_input_pulse_counter_trigger_mode",
    "mcu_param_input_pulse_counter_filter_level",
    "controller_param_control_algorithm",
    "controller_param_en_feedforward_idiq",
    "controller_param_en_feedforward_uduq", "controller_pos_loop_pid_param_en",
    "controller_pos_loop_pid_param_bypass", "controller_pos_loop_pid_param_kp",
    "controller_pos_loop_pid_param_ki", "controller_pos_loop_pid_param_kd",
    "controller_pos_loop_pid_param_kc",
    "controller_pos_loop_pid_ud_filter_param_en",
    "controller_pos_loop_pid_ud_filter_param_fc",
    "controller_pos_loop_pid_param_limit_ui",
    "controller_pos_loop_pid_parma_limit_out",
    "controller_pos_loop_pid_fal_param_en",
    "controller_pos_loop_pid_fal_param_a",
    "controller_pos_loop_pid_fal_param_d",
    "controller_pos_loop_pid_fal_param_zoom",
    "controller_pos_loop_pid_fal_param_section_nums",
    "controller_pos_loop_pid_fal_param_inteval_gain",
    "controller_spd_loop_pid_param_en", "controller_spd_loop_pid_param_bypass",
    "controller_spd_loop_pid_param_kp", "controller_spd_loop_pid_param_ki",
    "controller_spd_loop_pid_param_kd", "controller_spd_loop_pid_param_kc",
    "controller_spd_loop_pid_ud_filter_param_en",
    "controller_spd_loop_pid_ud_filter_param_fc",
    "controller_spd_loop_pid_param_limit_ui",
    "controller_spd_loop_pid_parma_limit_out",
    "controller_spd_loop_pid_fal_param_en",
    "controller_spd_loop_pid_fal_param_a",
    "controller_spd_loop_pid_fal_param_d",
    "controller_spd_loop_pid_fal_param_zoom",
    "controller_spd_loop_pid_fal_param_section_nums",
    "controller_spd_loop_pid_fal_param_inteval_gain",
    "controller_cur_loop_pid_param_en", "controller_cur_loop_pid_param_bypass",
    "controller_cur_loop_pid_param_kp", "controller_cur_loop_pid_param_ki",
    "controller_cur_loop_pid_param_kd", "controller_cur_loop_pid_param_kc",
    "controller_cur_loop_pid_ud_filter_param_en",
    "controller_cur_loop_pid_ud_filter_param_fc",
    "controller_cur_loop_pid_param_limit_ui",
    "controller_cur_loop_pid_parma_limit_out",
    "controller_cur_loop_pid_fal_param_en",
    "controller_cur_loop_pid_fal_param_a",
    "controller_cur_loop_pid_fal_param_d",
    "controller_cur_loop_pid_fal_param_zoom",
    "controller_cur_loop_pid_fal_param_section_nums",
    "controller_cur_loop_pid_fal_param_inteval_gain",
    "controller_leso_param_b0k", "controller_leso_param_b0",
    "controller_leso_param_z3k", "controller_leso_param_wp",
    "controller_leso_param_ws", "controller_leso_param_wd",
    "controller_fwc_param_en", "controller_fwc_param_I_max",
    "controller_fwc_param_L", "controller_fwc_param_phi_e",
    "controller_fwc_param_pole_pair", "controller_fwc_param_wm_base",
    "controller_cur_filter_param_en", "controller_cur_filter_param_Ts",
    "controller_cur_filter_param_fc",
    "controller_z3_filter_binary_second_order_param_en",
    "controller_z3_filter_binary_second_order_param_fc",
    "controller_z3_filter_binary_second_order_param_depth",
    "controller_z3_filter_binary_second_order_param_zeta_0N",
    "controller_z3_filter_binary_second_order_param_zeta_0D",
    "controller_spd_out_filter_binary_second_order_param_en",
    "controller_spd_out_filter_binary_second_order_param_fc",
    "controller_spd_out_filter_binary_second_order_param_depth",
    "controller_spd_out_filter_binary_second_order_param_zeta_0N",
    "controller_spd_out_filter_binary_second_order_param_zeta_0D",
    "controller_td_param_en", "controller_td_param_h0_gain_h",
    "controller_td_fhan_param_f0", "controller_td_param_d_gain",
    "controller_td_param_dd_gain",
    "controller_spd_out_filter_notch_0_param_en",
    "controller_spd_out_filter_notch_0_param_f0",
    "controller_spd_out_filter_notch_0_param_f_width",
    "controller_spd_out_filter_notch_0_param_depth",
    "controller_spd_out_filter_notch_1_param_en",
    "controller_spd_out_filter_notch_1_param_f0",
    "controller_spd_out_filter_notch_1_param_f_width",
    "controller_spd_out_filter_notch_1_param_depth",
    "controller_spd_out_filter_notch_2_param_en",
    "controller_spd_out_filter_notch_2_param_f0",
    "controller_spd_out_filter_notch_2_param_f_width",
    "controller_spd_out_filter_notch_2_param_depth",
    "controller_motor_ripple_compensator_en",
    "controller_motor_ripple_compensator_sin_coeffs_0_term_A",
    "controller_motor_ripple_compensator_sin_coeffs_0_term_n",
    "controller_motor_ripple_compensator_sin_coeffs_0_term_phi",
    "controller_motor_ripple_compensator_sin_coeffs_1_term_A",
    "controller_motor_ripple_compensator_sin_coeffs_1_term_n",
    "controller_motor_ripple_compensator_sin_coeffs_1_term_phi",
    "controller_motor_ripple_compensator_sin_coeffs_2_term_A",
    "controller_motor_ripple_compensator_sin_coeffs_2_term_n",
    "controller_motor_ripple_compensator_sin_coeffs_2_term_phi",
    "flash_manager_param_checksum_restore_en", "logger_param_en",
    "logger_param_continuous_send", "logger_param_sample_interval_n",
    "logger_param_n_pack_to_send", "protection_param_protect_en",
    "protection_param_protect_report", "protection_param_err_code_mask",
    "protection_param_warning_code_mask",
    "protection_param_encoder_mutation_coefficient_upper",
    "protection_param_encoder_mutation_coefficient_lower",
    "protection_param_prt_peak_cur_A", "protection_param_prt_continuous_cur_A",
    "protection_param_prt_continuous_time_s",
    "protection_param_prt_over_speed_rad_s",
    "protection_param_prt_over_speed_time_s",
    "protection_param_pos_over_limit_max_rad",
    "protection_param_pos_over_limit_min_rad",
    "protection_param_prt_cmd_mutation_turn",
    "protection_param_prt_track_max_err",
    "protection_param_prt_track_err_time",
    "protection_param_power_voltage_min", "protection_param_mcu_temp_max",
    "protection_err_code_backup", "protection_warning_code_backup",
    "chopper_param_chopper_mode", "chopper_param_svpwm_mode",
    "chopper_param_base_pwm", "chopper_param_offset_pwm_en",
    "chopper_param_offset_pwm", "chopper_param_hybrid_decay_ratio",
    "step_controller_param_pulse_count_max",
    "step_controller_param_subdivision", "step_controller_param_pulse_trim_en",
    "step_controller_param_trim_action_period",
    "step_controller_param_extra_trim_pulse", "cmd_int_param_char_cmd_support",
    "system_id_RL_param_param_uin_startup",
    "system_id_RL_param_param_uin_calibrate",
    "system_id_RL_param_param_current_stable_err_condition",
    "system_id_RL_param_param_current_buff_size",
    "system_id_RL_param_param_current_err_size",
    "system_id_RL_param_param_prepare_time",
    "system_id_RL_param_param_normal_R_range_min",
    "system_id_RL_param_param_normal_R_range_max",
    "system_id_RL_param_param_normal_L_range_min",
    "system_id_RL_param_normal_L_range_max",
    "motor_phase_check_param_run_on_startup",
    "motor_phase_check_param_motor_resistance_tolerance_standar_percent",
    "zazen_param_zazen_en", "zazen_param_zazen_trigger_time_s",
    "zazen_param_zazen_gain_pos_kp", "zazen_param_zazen_gain_spd_kp",
    "zazen_param_zazen_gain_spd_ki", "zazen_param_zazen_gain_cur_kp",
    "zazen_param_zazen_gain_cur_ki", "param_flash_key", "param_flash_checksum",
    "stall_pos_err_rad_for_slicer",
)
SYS_PARAM_COUNT = 227
SYS_PARAM_CONFIGURABLE = frozenset((
    9, 10, 12, 13, 14, 20, 21, 23, 25, 41, 42, 43, 66, 67, 70, 86, 87, 89,
    94, 95, 97, 102, 103, 105, 110, 111, 113, 116, 118, 119, 120, 121, 122,
    123, 127, 128, 130, 141, 142, 143, 144, 145, 173, 179, 180, 181, 182,
    183, 187, 188, 189, 190, 200, 204, 217, 218, 220, 221, 222, 223, 226))
_PARAM_TYPES_NONFLOAT = {
    10: "int16_t", 12: "uint16_t", 66: "uint16_t", 67: "uint16_t",
    94: "uint16_t", 110: "uint16_t", 122: "uint16_t", 128: "uint16_t",
    141: "uint16_t", 173: "uint16_t", 200: "uint16_t", 204: "uint16_t",
    217: "uint16_t"}
_TYPE_FORMATS = {"float": "<f", "uint16_t": "<H", "int16_t": "<h",
                 "uint32_t": "<I", "int32_t": "<i"}

# --- Kalibrier-Sicherung/-Prüfung (26.10.2026, NUR LESEND) -------------------
# KTWO_MOTOR_CAL_BACKUP / KTWO_MOTOR_CAL_CHECK. Belege: Motor-FW-Analyse
# 25.10.2026 (Lesebefehle, Flash-Layout, Boot-Plausibilität). Die
# Kalibrierung selbst (0x03/0x04) bleibt im Sendeschutz gesperrt; hier geht
# kein schreibender Frame raus und nichts bewegt sich.
FUNC_CONTROL = 0x05
SUB_CONTROL_STATUS = 0x04      # -> [EN-Pin, Regler, PWM]; einzige Sub mit Antwort
FUNC_FLASH_PARAM = 0x07
SUB_FLASH_CHECKSUM = 0x02      # -> [ok, Summe berechnet u16, Summe Flash u16] LE
SUB_FLASH_READ = 0x06          # [06, id] -> Flash-Wert der ID (2/4 B LE)
FUNC_DEV_UUID = 0x12
SUB_UUID_READ = 0x02           # -> 8 B; NUR in die Sicherungsdatei, nie Konsole/Log
FUNC_ERR_DETAIL = 0x13         # 74 B; statische Felder = Gegenprobe zum RAM
ERR_DETAIL_REQUEST = (0x02,)   # wie der Stock-Host (FW wertet die Payload nicht aus)
CAL_ERR_DETAIL_HEAD = "<I8BfHf"  # err_code, dev_uuid, elec_offset, phase, err_deg
TIMEOUT_CAL_READ = 1.0
CAL_READ_TRIES = 2             # nur lesend -> eine Wiederholung ist unkritisch
# RS485 blockiert den Reactor bis zum Timeout: nach so vielen Lesungen ohne
# Antwort in Folge wird abgebrochen (stummer Motor -> nicht 326 Timeouts)
CAL_MAX_FEHL_FOLGE = 3
# Kalibrierwerte: (ID, Name, struct-Format laut FW-Typtabelle)
CAL_PARAMS = (
    (7, "singleturn_control_mode", "<H"),
    (8, "elec_angle_calibration_standar_deg", "<f"),
    (9, "elec_offset", "<f"),
    (10, "phase_order_invert", "<h"),
    (20, "ud_cal_set", "<f"),
    (21, "motion_dir", "<f"),
    (23, "encoder_calibtate_official_ud_cal_set", "<f"),
    (25, "elec_offset_err_deg", "<f"),
)
CAL_PARAM_IDS = tuple(pid for pid, _n, _f in CAL_PARAMS)
# In der Motor-FW registrierte IDs (77; Flash-Layout in ID-Reihenfolge).
# ID 227 gibt es nur in der FW (Host-Tabelle 0..226).
CAL_FW_PARAM_IDS = tuple(
    list(range(0, 15)) + [20, 21, 23, 25, 26, 28, 30, 41, 42, 43, 63, 64, 66, 67,
                          70, 86, 87, 89, 94, 95, 97, 102, 103, 105, 110, 111, 113]
    + list(range(116, 124)) + [125, 127, 128, 130, 168, 173]
    + list(range(179, 184)) + list(range(187, 193)) + [200, 217, 218]
    + list(range(220, 226)) + [227])
CAL_FW_ONLY_NAMES = {227: "protect_continuous_overcurrent_clear_time_s"}
# Laufzeitwerte, bei denen RAM ≠ Flash normal ist (Stall-/Notfallmodus,
# Homing-Kp, protect_en) – nur gemeldet. ID 173 protect_en (= obj+0x3f4,
# uint16): live 26.10.2026 bei allen 5 Motoren RAM 255 / Flash 1. Ursache
# (Motor-FW-Analyse 26.10.): die Encoder-Registerprüfung nach dem Boot sichert
# nur das untere Byte und überschreibt es durch einen zu langen Kopiervorgang
# mit dem SPI-Empfangsbyte (0xFF); bricht der Transfer ab, wäre es 0 =
# Motor-Schutz AUS. Wirksam ist nur ≠ 0 (255 = 1).
CAL_RAM_TEMP_IDS = (11, 12, 70, 173)
CAL_PROTECT_EN_ID = 173
# 0x08-Messwerte für die Sicherung: (idx, Name, Format)
CAL_VARIABLES = ((15, "encoder_single_turn", "<i"),
                 (17, "board_detect_temperature_C", "<f"),
                 (18, "board_detect_power_supply_V", "<f"))
# Boot-Plausibilität der FW: phase_order_invert ≤ 1, |elec_offset| < 2·32768/50,
# elec_offset_err_deg < 360 (sonst holt der Boot den Werks-Record zurück). Die
# FW teilt ganzzahlig (div, dann fcvt.s.w) -> Grenze 1310, nicht 1310,72.
CAL_ELEC_OFFSET_MAX = float(2 * 32768 // 50)
CAL_ERR_DEG_MAX = 360.0
CAL_BACKUP_DIR_DEFAULT = "~/printer_data/k2-motor-cal"
CAL_FILE_FORMAT = "k2-motor-cal/1"
CAL_FILE_RE = re.compile(r"^(x|y|z|z1|e)-(\d{8}-\d{6})(?:-([2-9]))?\.json$")
CAL_HINWEIS_RECORD = ("Der Werks-Record des Motors ist über den Bus nicht lesbar – "
                      "gesichert ist der Haupt-Parameterblock (RAM + Flash).")

# --- Kalibrierlauf RUN / RESTORE / ENABLE (26.10.2026 abends, SCHREIBEND) -----
# Spezifikation: Motor-FW-Analyse 25.10.2026 (§8). Standardmäßig AUS
# (allow_motor_calibration: False); zusätzlich allow_write, Bewegungsfreigabe
# _PI_FREIGABE allow_move, zweistufig mit Bestätigungscode. Gesendet wird je
# Lauf GENAU EIN Kalibrier-Frame (retries 0) über die Einmalfreigabe des
# Sendeschutzes (k2_transport.tx_kalibrier_freigabe).
FUNC_ENCODER_CALIBRATE = 0x03
FUNC_ELEC_OFFSET_CALIBRATE = 0x04
CAL_RUN_PAYLOAD = (0x00,)
FLASH_SAVE_PAYLOAD = (0x03, 0x00)  # 0x07 [03, 00]: alle Parameter RAM -> Flash
# Modus -> (fun, Host-Timeout T_max s, Antwortlänge, Bewegungsumfang)
CAL_MODES = {
    "ELEC": (FUNC_ELEC_OFFSET_CALIBRATE, 45.0, 9,
             "etwa 1 Umdrehung vor und zurück, open-loop, ohne Schutz"),
    "ENCODER": (FUNC_ENCODER_CALIBRATE, 120.0, 1,
                "Dauerdrehung bis ≈ 63 Umdrehungen mit ≈ 2,5 U/s, ohne Schutz"),
}
CAL_ENCODER_OK = 0x0C              # 0x03-Antwort: 1 B, 0x0C = gelungen
TIMEOUT_FLASH_SAVE = 10.0          # 0x07 [03]: ≈ 3,7 s Flash-Schreiben
CAL_FREIGABE_S = 2.0               # Gültigkeit der Einmalfreigabe (Prüfungen laufen sofort)
CAL_TOKEN_S = 120.0                # Bestätigungscode gültig
CAL_LAUF_NACHLAUF_S = 30.0         # Sperrdatei: T_max + Nachlauf
# Umbruch-Fenster am Encoder (Single-Turn): ¾ el. Periode (491) + Ausricht-
# sprung auf el. 0° (≤ ½ Periode, 327) = 818 Counts -> mit Reserve 1000
CAL_WRAP_COUNTS = 1000
CAL_ENCODER_COUNTS = 32768
CAL_SUPPLY_V = (22.0, 26.0)
CAL_TEMP_MAX_C = 70.0
CAL_UD_FENSTER = (0.03, 0.25)      # ID 20 ud_cal_set, ID 23 ..._official_ud_cal_set
CAL_WINKEL_FENSTER = (5.0, 30.0)   # ID 8 elec_angle_calibration_standar_deg
CAL_KP_ID = 70
CAL_MODE_IDS = (11, 12)            # stall_mode / emergency_pin_mode
CAL_ZUSTAND_DATEI = "kalibrier-zustand.json"
CAL_AUFSCHUB_MAX_S = 120.0 + 30.0  # Init-Aufschub höchstens T_max(ENCODER) + Nachlauf
# Harte Sperre an Klippers Stepper-Freigabe (stepper_enable): in diesen Zuständen
# darf die Regelung nicht angehen -> Shutdown statt Freigabe
CAL_STATUS_HART = ("laeuft", "gesperrt_unbekannt", "phase_verdacht", "elec_pflicht")
CAL_STEPPER_NAMEN = {"x": "stepper_x", "y": "stepper_y", "z": "stepper_z",
                     "z1": "stepper_z1", "e": "extruder"}
# Zustände je Motor (Datei überdauert Klipper-Neustarts):
#   laeuft             Kalibrier-Frame gesendet, Antwort steht aus
#   gesperrt_unbekannt keine/kaputte Antwort – Zustand unbekannt
#   phase_verdacht     phase_order_invert geändert / Schutz im Flash aus -> RESTORE
#   elec_pflicht       nach ENCODER: Drucker aus/ein, dann MODE=ELEC
#   homing_noetig      Lauf ok -> wieder einkuppeln, ENABLE
#   z_ausrichtung_noetig  wie homing_noetig, zusätzlich Z ausrichten
CAL_STATUS_TEXT = {
    "laeuft": "Kalibrierlauf läuft",
    "gesperrt_unbekannt": "Zustand unbekannt (keine Antwort) – Drucker aus/ein, dann ENABLE STROMLOS=1",
    "phase_verdacht": "phase_order_invert/Schutz verdächtig – erst KTWO_MOTOR_CAL_RESTORE",
    "elec_pflicht": "nach Encoder-Kalibrierung: Drucker aus/ein, dann MODE=ELEC",
    "homing_noetig": "wieder einkuppeln, dann KTWO_MOTOR_CAL_ENABLE",
    "z_ausrichtung_noetig": "wieder einkuppeln, ENABLE, dann Z ausrichten + Bettnetz",
}

MOTOR_ERROR_BITS = {
    0: ("key781", "Encoder-Sprung"), 1: ("key782", "Encoder-Lesefehler"),
    2: ("key783", "SW-Spitzenüberstrom"), 3: ("key784", "SW-Dauerüberstrom"),
    4: ("key785", "HW-Überstrom"), 5: ("key786", "Drehzahl über Grenze"),
    6: ("key787", "Positionsgrenze"), 7: ("key788", "Positions-Sollwertsprung"),
    8: ("key789", "Schleppfehler"), 9: ("key790", "R/L-Fehler"),
    10: ("key791", "instabil"), 11: ("key792", "Phase fehlt"),
    12: ("key793", "Phasenwiderstand-Differenz"), 13: ("key794", "Sonstige"),
}
MOTOR_WARNING_BITS = {
    0: ("key795", "Kommunikation"), 1: ("key796", "Unterspannung"),
    2: ("key797", "MCU-Übertemperatur"),
}


def default_param_table():
    """227 Einträge {id, name, configurable, value_type} (Port aus Nachbau)."""
    table = []
    for pid, name in enumerate(_SYS_PARAM_NAMES):
        conf = pid in SYS_PARAM_CONFIGURABLE
        vt = _PARAM_TYPES_NONFLOAT.get(pid, "float") if conf else None
        table.append({"id": pid, "name": name, "configurable": conf,
                      "value_type": vt})
    return table


def parse_pin_level(spec):
    """'!PB7,0' -> {raw, pin, invert, level, phys} (phys = level ^ invert)."""
    if spec is None:
        return None
    parts = [p.strip() for p in str(spec).split(",")]
    if len(parts) != 2 or parts[1] not in ("0", "1"):
        raise ValueError("Pin-Angabe muss 'PIN,0|1' sein: %r" % (spec,))
    pin = parts[0]
    inv = pin.startswith("!")
    lvl = int(parts[1])
    return {"raw": pin, "pin": pin.lstrip("!^~"), "invert": inv,
            "level": lvl, "phys": lvl ^ int(inv)}


def strapping_address(dir_phys, step_phys):
    """Motor-FW 0x0E Sub 01: addr = 0x81 + 2·STEP + DIR (Byte)."""
    for v in (dir_phys, step_phys):
        if v not in (0, 1):
            raise ValueError("Pegel muss 0/1 sein: %r" % (v,))
    return 0x81 + 2 * int(step_phys) + int(dir_phys)


def _isclose(a, b):
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def _float_to_bytes(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Sys-Param-Wert muss Zahl sein: %r" % (value,))
    if not math.isfinite(value):
        raise ValueError("Sys-Param-Wert muss endlich sein: %r" % (value,))
    try:
        return struct.pack("<f", float(value))
    except Exception as exc:
        raise ValueError("passt nicht in float32: %r (%s)" % (value, exc))


def decode_protection_local(data):
    """8 B von 0x0C [0b] -> dict (vendored aus motor.py, dort belegt)."""
    raw = bytes(data)
    if len(raw) != 8:
        raise ValueError("0x0C: %d statt 8 Bytes" % len(raw))
    err, warn = struct.unpack("<II", raw)
    def _bits(code, table):
        out, bit = [], 0
        while code >> bit:
            if code >> bit & 1:
                key, text = table.get(bit, (None, "unbekannt"))
                out.append((bit, key, text))
            bit += 1
        return out
    return {"err_code": err, "warning_code": warn,
            "errors": _bits(err, MOTOR_ERROR_BITS),
            "warnings": _bits(warn, MOTOR_WARNING_BITS),
            "ok": err == 0 and warn == 0, "raw": raw}


def decode_version_local(data):
    """12 B von 0x0F [00] -> dict (Feldnamen = Stock-Strings)."""
    raw = bytes(data)
    if len(raw) != 12:
        raise ValueError("0x0F: %d statt 12 Bytes" % len(raw))
    fields = (("fireware_ver", 4), ("cmd_system_ver", 2),
              ("protection_ver", 2), ("flash_param_ver", 2), ("logger_ver", 2))
    out, pos = {}, 0
    for name, n in fields:
        out[name] = ".".join(str(b) for b in raw[pos:pos + n])
        pos += n
    out["raw"] = raw
    return out


# --- Kalibrier-Sicherung/-Prüfung: reine Funktionen ---------------------------

def cal_param_name(pid):
    if pid in CAL_FW_ONLY_NAMES:
        return CAL_FW_ONLY_NAMES[pid]
    if 0 <= pid < len(_SYS_PARAM_NAMES):
        return _SYS_PARAM_NAMES[pid]
    return "id_%d" % pid


def _cal_unpack(raw, fmt):
    """Rohbytes -> Zahl (None bei falscher Länge; nicht endliche Floats als
    Text, damit die JSON-Datei gültig bleibt)."""
    raw = bytes(raw)
    if struct.calcsize(fmt) != len(raw):
        return None
    v = struct.unpack(fmt, raw)[0]
    if isinstance(v, float) and not math.isfinite(v):
        return repr(v)
    return v


def _cal_unpack_hex(h, fmt):
    return None if h is None else _cal_unpack(bytes.fromhex(h), fmt)


def cal_id_wert(pid, h):
    """Rohbytes (hex) einer beliebigen ID -> Zahl: 4 B float, 2 B laut
    Host-Typ (int16 nur ID 10), sonst None."""
    if h is None:
        return None
    raw = bytes.fromhex(h)
    if len(raw) == 4:
        return _cal_unpack(raw, "<f")
    if len(raw) == 2:
        return _cal_unpack(raw, _TYPE_FORMATS.get(_PARAM_TYPES_NONFLOAT.get(pid, "uint16_t"),
                                                  "<H"))
    return None


def _cal_gleich(a, b):
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= 1e-4 * max(1.0, abs(a))
    return a == b


def _cal_fein_gleich(a, b):
    """Gleich bis auf den Text-Umweg der FW beim Schreiben (Float -> Text mit
    6 Nachkommastellen -> atof) und float32-Rundung."""
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) <= max(2e-6, 2e-7 * max(abs(a), abs(b)))
    return a == b


def _cal_zahl(v):
    if isinstance(v, float):
        return "%.6g" % v
    return "-" if v is None else str(v)


def cal_err_detail(raw, kal):
    """0x13-Antwort -> dict mit elec_offset/phase/err und Gegenprobe zum RAM.
    Die Rohbytes enthalten die UUID (nur Datei)."""
    raw = bytes(raw)
    out = {"raw": raw.hex(), "laenge": len(raw)}
    if len(raw) < struct.calcsize(CAL_ERR_DETAIL_HEAD):
        out["passt_zu_ram"] = None
        return out
    v = struct.unpack_from(CAL_ERR_DETAIL_HEAD, raw)
    off, ph, err = v[9], v[10], v[11]
    out.update(elec_offset=_cal_unpack(struct.pack("<f", off), "<f"),
               phase_order_invert=ph,
               elec_offset_err_deg=_cal_unpack(struct.pack("<f", err), "<f"))
    r9, r10, r25 = (kal[str(i)]["ram"] for i in (9, 10, 25))
    if r9 is None or r10 is None or r25 is None:
        out["passt_zu_ram"] = None          # RAM-Seite nicht gelesen
        return out
    out["passt_zu_ram"] = ((int(r10) & 0xFFFF) == ph   # 0x13: u16, ID 10: i16
                           and _cal_gleich(r9, out["elec_offset"])
                           and _cal_gleich(r25, out["elec_offset_err_deg"]))
    return out


def cal_auswerten(snap):
    """Ergänzt plausibel/plausi_gruende/ram_ungleich_flash/vollstaendig."""
    kal = snap["kalibrierung"]
    gruende = []
    for wo in ("ram", "flash"):
        ph, off, err = (kal[str(i)][wo] for i in (10, 9, 25))
        if ph is None or off is None or err is None:
            gruende.append("%s: Kalibrierwerte nicht vollständig gelesen" % wo.upper())
            continue
        if ph not in (0, 1):
            gruende.append("%s: phase_order_invert = %s (erlaubt 0/1)" % (wo.upper(), ph))
        if not (isinstance(off, float) and abs(off) < CAL_ELEC_OFFSET_MAX):
            gruende.append("%s: elec_offset = %s (|Wert| < %g)"
                           % (wo.upper(), _cal_zahl(off), CAL_ELEC_OFFSET_MAX))
        if not (isinstance(err, float) and 0.0 <= err < CAL_ERR_DEG_MAX):
            gruende.append("%s: elec_offset_err_deg = %s (0 … < 360)"
                           % (wo.upper(), _cal_zahl(err)))
    snap["plausibel"] = not gruende
    snap["plausi_gruende"] = gruende
    snap["ram_ungleich_flash"] = sorted(
        int(k) for k, e in snap["alle_ids"].items()
        if e["ram"] is not None and e["flash"] is not None and e["ram"] != e["flash"])
    snap["vollstaendig"] = not snap["fehler"]
    return snap


def cal_kurztext(snap):
    """Eine Konsolenzeile je Motor – OHNE UUID."""
    kal = snap["kalibrierung"]
    teile = ["%s (%s, %s)" % (snap["motor"], snap["adresse"], snap["weg"])]
    ver = snap.get("version") or {}
    if ver.get("fireware_ver"):
        teile.append("FW %s" % ver["fireware_ver"])
    for pid in (9, 10, 25, 20, 23):
        e = kal[str(pid)]
        txt = "%s %s" % (e["name"], _cal_zahl(e["ram"]))
        if e["ram_raw"] is not None and e["flash_raw"] is not None \
                and e["ram_raw"] != e["flash_raw"]:
            txt += " (Flash %s!)" % _cal_zahl(e["flash"])
        teile.append(txt)
    teile.append("plausibel" if snap["plausibel"] else "UNPLAUSIBEL")
    sch = snap.get("schutz") or {}
    if "ok" in sch:
        teile.append("Schutz ok" if sch["ok"] else "Schutz err=%s warn=%s" % (
            sch.get("fehler"), sch.get("warnungen")))
    fp = snap.get("flash_pruefsumme") or {}
    if "berechnet" in fp:
        teile.append("Prüfsumme %s" % ("ok" if fp["berechnet"] == fp["flash"]
                                        else "ABWEICHEND"))
    reg = snap.get("regelung") or {}
    if "en_pin" in reg:
        teile.append("EN=%d Regler=%d PWM=%d" % (reg["en_pin"], reg["regler"], reg["pwm"]))
    rf = snap.get("ram_ungleich_flash", [])
    teile.append("%d IDs, RAM≠Flash: %s" % (
        len(snap["alle_ids"]), ", ".join(str(i) for i in rf) if rf else "keine"))
    return " | ".join(teile)


def cal_vergleich(jetzt, alt, config=None):
    """Prüfung gegen eine Sicherung -> (alarme, warnungen, infos), ohne UUID.
    alt = None: nur der aktuelle Zustand wird bewertet. config = {Param-Name:
    Wert} aus [k2_motor] für diesen Motor: RAM ≠ Flash ist dort normal, wenn
    der RAM-Wert dem Config-Wert entspricht (der Init schreibt ihn bei jedem
    Start flüchtig, wie Stock)."""
    alarme, warn, info = [], [], []
    if not jetzt["plausibel"]:
        alarme += ["unplausibel – " + g for g in jetzt["plausi_gruende"]]
    for pid in (9, 10, 25):
        e = jetzt["kalibrierung"][str(pid)]
        if e["ram_raw"] is not None and e["flash_raw"] is not None \
                and e["ram_raw"] != e["flash_raw"]:
            warn.append("%s: RAM %s ≠ Flash %s" % (e["name"], _cal_zahl(e["ram"]),
                                                 _cal_zahl(e["flash"])))
    pe = cal_id_wert(CAL_PROTECT_EN_ID,
                     (jetzt["alle_ids"].get(str(CAL_PROTECT_EN_ID)) or {}).get("ram"))
    if pe == 0:
        alarme.append("protect_en (ID 173) im RAM = 0 – Motor-Schutzfunktionen AUS "
                      "(Fehler der Encoder-Prüfung beim Boot?) – Drucker aus/ein")
    temp = [i for i in jetzt["ram_ungleich_flash"] if i in CAL_RAM_TEMP_IDS]
    if temp:
        info.append("RAM ≠ Flash bei Laufzeitwerten (normal): IDs %s"
                    % ", ".join(str(i) for i in temp))
    rest = [i for i in jetzt["ram_ungleich_flash"]
            if i not in CAL_RAM_TEMP_IDS and i not in (9, 10, 25)]
    cfg_ok = []
    for i in list(rest):
        name = cal_param_name(i)
        if config and name in config:
            ram = cal_id_wert(i, jetzt["alle_ids"][str(i)]["ram"])
            try:
                soll = float(config[name])
            except (TypeError, ValueError):
                continue
            if isinstance(ram, (int, float)) and not isinstance(ram, bool) \
                    and abs(float(ram) - soll) <= 1e-4 * max(1.0, abs(soll)):
                cfg_ok.append(i)
                rest.remove(i)
    if cfg_ok:
        info.append("RAM ≠ Flash bei Werten aus der Config (Init, normal): IDs %s"
                    % ", ".join(str(i) for i in cfg_ok))
    if rest:
        warn.append("RAM ≠ Flash: IDs %s" % ", ".join(str(i) for i in rest))
    ed = jetzt.get("fehlerdetail_0x13")
    if isinstance(ed, dict) and ed.get("passt_zu_ram") is False:
        warn.append("Gegenprobe 0x13 passt nicht zum RAM (elec_offset %s, phase %s, err %s)"
                    % (_cal_zahl(ed.get("elec_offset")), ed.get("phase_order_invert"),
                       _cal_zahl(ed.get("elec_offset_err_deg"))))
    sch = jetzt.get("schutz") or {}
    if sch.get("fehler") or sch.get("warnungen"):
        warn.append("Fehlerbits err=%s warn=%s" % (sch.get("fehler"), sch.get("warnungen")))
    fp = jetzt.get("flash_pruefsumme") or {}
    if "berechnet" in fp and fp["berechnet"] != fp["flash"]:
        warn.append("Flash-Prüfsumme: berechnet 0x%04X, gespeichert 0x%04X"
                    % (fp["berechnet"], fp["flash"]))
    if jetzt["fehler"]:
        warn.append("%d Lesefehler: %s" % (len(jetzt["fehler"]), "; ".join(jetzt["fehler"][:3])))
    if alt is None:
        info.append("keine Sicherung zum Vergleich – zuerst KTWO_MOTOR_CAL_BACKUP")
        return alarme, warn, info
    if alt.get("uuid") and jetzt.get("uuid") and alt["uuid"] != jetzt["uuid"]:
        warn.append("Sicherung stammt von einer anderen Motor-Einheit (UUID "
                    "abweichend) – Vergleich übersprungen, neue Sicherung anlegen")
        return alarme, warn, info
    if alt.get("vollstaendig") is False or alt.get("plausibel") is False:
        warn.append("Sicherung vom %s ist %s – Vergleich nur eingeschränkt "
                    "aussagekräftig" % (alt.get("zeit", "?"), " und ".join(
                        t for t, b in (("unvollständig", alt.get("vollstaendig") is False),
                                       ("unplausibel", alt.get("plausibel") is False)) if b)))
    av = (alt.get("version") or {}).get("fireware_ver")
    jv = (jetzt.get("version") or {}).get("fireware_ver")
    if av and jv and av != jv:
        warn.append("Firmware %s → %s seit der Sicherung" % (av, jv))
    akal = alt.get("kalibrierung") or {}
    for pid, name, _fmt in CAL_PARAMS:
        a, j = akal.get(str(pid)) or {}, jetzt["kalibrierung"][str(pid)]
        for wo in ("ram", "flash"):
            ar, jr = a.get(wo + "_raw"), j.get(wo + "_raw")
            if ar is None or jr is None:
                warn.append("%s (%s): nicht vergleichbar (%s nicht gelesen)" % (
                    name, wo.upper(), "Sicherung" if ar is None else "jetzt"))
                continue
            if ar == jr or _cal_fein_gleich(a.get(wo), j.get(wo)):
                continue
            txt = "%s (%s): Sicherung %s → jetzt %s" % (name, wo.upper(),
                                                         _cal_zahl(a.get(wo)),
                                                         _cal_zahl(j.get(wo)))
            (alarme if pid == 10 else warn).append(txt)
    aids = alt.get("alle_ids") or {}
    geaendert = sorted(
        int(k) for k, e in jetzt["alle_ids"].items()
        if int(k) not in CAL_PARAM_IDS and k in aids and e["flash"] is not None
        and aids[k].get("flash") is not None and e["flash"] != aids[k]["flash"])
    if geaendert:
        warn.append("Flash geändert seit der Sicherung: IDs %s"
                    % ", ".join(str(i) for i in geaendert))
    if not alarme and not warn:
        info.append("Kalibrierwerte unverändert gegenüber der Sicherung")
    return alarme, warn, info


def cal_sicherung_schreiben(verz, snap):
    """Schreibt die Sicherung als <motor>-JJJJMMTT-hhmmss.json (Ordner 700,
    Datei 600, atomar). -> Pfad. Datei existiert schon -> FileExistsError."""
    verz = os.path.expanduser(verz)
    os.makedirs(verz, mode=0o700, exist_ok=True)
    os.chmod(verz, 0o700)                 # auch einen schon vorhandenen Ordner
    for n in [""] + ["-%d" % i for i in range(2, 10)]:   # gleiche Sekunde: -2 … -9
        name = "%s-%s%s.json" % (snap["motor"], snap["zeit_datei"], n)
        pfad = os.path.join(verz, name)
        if not os.path.exists(pfad):
            break
    else:
        raise FileExistsError("%s existiert schon (1 s warten)" % name)
    tmp = pfad + ".tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(snap, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())         # Stromausfall direkt danach: Datei bleibt
        os.replace(tmp, pfad)
    except BaseException:
        try:
            os.unlink(tmp)                # keine Reste mit UUID liegen lassen
        except OSError:
            pass
        raise
    dfd = os.open(verz, os.O_RDONLY)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return pfad


def cal_sicherungen(verz, motor):
    """Sicherungsdateien eines Motors, neueste zuerst -> [Dateiname]."""
    verz = os.path.expanduser(verz)
    try:
        namen = os.listdir(verz)
    except OSError:
        return []
    out = []
    for n in namen:
        m = CAL_FILE_RE.match(n)
        if m and m.group(1) == motor:
            out.append((m.group(2), int(m.group(3) or 1), n))
    return [n for _z, _i, n in sorted(out, reverse=True)]


def cal_sicherung_laden(verz, name):
    """Lädt eine Sicherung (nur Dateinamen aus dem Sicherungsordner) -> dict."""
    if not CAL_FILE_RE.match(str(name)):
        raise ValueError("BACKUP=%s: erwartet <motor>-JJJJMMTT-hhmmss.json im "
                         "Sicherungsordner" % (name,))
    with open(os.path.join(os.path.expanduser(verz), name), encoding="utf-8") as fh:
        alt = json.load(fh)
    if not isinstance(alt, dict) or not isinstance(alt.get("kalibrierung"), dict) \
            or not isinstance(alt.get("alle_ids"), dict):
        raise ValueError("%s: kein Sicherungsinhalt" % name)
    if alt.get("format") != CAL_FILE_FORMAT:
        raise ValueError("%s: unbekanntes Format %r" % (name, alt.get("format")))
    return alt


def cal_sicherung_suchen(verz, motor, uuid):
    """Sicherung zum Vergleich, neueste zuerst: 1. gleiche UUID, vollständig
    und plausibel; 2. gleiche UUID; 3. sonst die neueste MIT UUID (der
    Vergleich meldet dann die andere Einheit). Dateien ohne UUID werden nie
    automatisch gewählt. -> (name, dict) oder (None, None)."""
    gut = gleich = fremd = None
    for name in cal_sicherungen(verz, motor):
        try:
            alt = cal_sicherung_laden(verz, name)
        except (OSError, ValueError):
            continue
        if not alt.get("uuid"):
            continue
        if uuid and alt["uuid"] == uuid:
            if gleich is None:
                gleich = (name, alt)
            if gut is None and alt.get("vollstaendig") and alt.get("plausibel"):
                gut = (name, alt)
        elif fremd is None:
            fremd = (name, alt)
    return gut or gleich or fremd or (None, None)


def cal_run_vorbedingungen(snap, config=None, en_pruefen=True, art="run"):
    """Vorbedingungen für RUN (art="run") bzw. RESTORE (art="restore", ohne
    Kalibrierparameter-Fenster und Umbruch-Fenster) aus einem frischen
    Snapshot -> (probleme, hinweise). probleme nicht leer = nichts senden."""
    probleme, hinweise = [], []
    if not snap.get("vollstaendig"):
        probleme.append("Snapshot unvollständig (%d Lesefehler)" % len(snap.get("fehler", [])))
    reg = snap.get("regelung") or {}
    if en_pruefen and (reg.get("en_pin"), reg.get("regler"), reg.get("pwm")) != (1, 0, 0):
        probleme.append("Motor nicht abgeschaltet: 0x05 [04] = %s statt EN=1 Regler=0 PWM=0 "
                        "(M84)" % (reg.get("raw"),))
    sch = snap.get("schutz") or {}
    if not sch.get("ok"):
        probleme.append("Fehler/Warnungen im Motor (err=%s warn=%s) – Ursache klären, "
                        "dann KTWO_MOTOR_CLEAR" % (sch.get("fehler"), sch.get("warnungen")))
    mw = snap.get("messwerte") or {}
    v = (mw.get("board_detect_power_supply_V") or {}).get("wert")
    if not (isinstance(v, float) and CAL_SUPPLY_V[0] <= v <= CAL_SUPPLY_V[1]):
        probleme.append("Versorgung %s V außerhalb %g–%g V" % (_cal_zahl(v), *CAL_SUPPLY_V))
    t = (mw.get("board_detect_temperature_C") or {}).get("wert")
    if not (isinstance(t, float) and t < CAL_TEMP_MAX_C):
        probleme.append("Motor-MCU %s °C (Grenze < %g °C)" % (_cal_zahl(t), CAL_TEMP_MAX_C))
    kal = snap.get("kalibrierung") or {}
    fenster = ((20, CAL_UD_FENSTER), (23, CAL_UD_FENSTER), (8, CAL_WINKEL_FENSTER)) \
        if art == "run" else ()
    for pid, (lo, hi) in fenster:
        for wo in ("ram", "flash"):
            w = (kal.get(str(pid)) or {}).get(wo)
            if not (isinstance(w, float) and lo <= w <= hi):
                probleme.append("ID %d %s (%s) = %s außerhalb %g–%g – Parameter verändert?"
                                % (pid, (kal.get(str(pid)) or {}).get("name"), wo.upper(),
                                   _cal_zahl(w), lo, hi))
    st = (mw.get("encoder_single_turn") or {}).get("wert")
    if art != "run":
        pass
    elif not isinstance(st, int) or isinstance(st, bool) or not 0 <= st < CAL_ENCODER_COUNTS:
        probleme.append("Encoder-Position (0x08 idx 15) nicht lesbar: %s" % (st,))
    elif st <= CAL_WRAP_COUNTS or st >= CAL_ENCODER_COUNTS - 1 - CAL_WRAP_COUNTS:
        probleme.append("Encoder-Position %d im Umbruch-Fenster (0…%d / %d…%d) – sonst "
                        "droht ein falsches phase_order_invert: Welle von Hand ≈ 10° drehen "
                        "und neu starten" % (st, CAL_WRAP_COUNTS,
                                             CAL_ENCODER_COUNTS - 1 - CAL_WRAP_COUNTS,
                                             CAL_ENCODER_COUNTS - 1))
    ids = snap.get("alle_ids") or {}
    kp_name = cal_param_name(CAL_KP_ID)
    if config and kp_name in config and str(CAL_KP_ID) in ids:
        kp = cal_id_wert(CAL_KP_ID, ids[str(CAL_KP_ID)]["ram"])
        try:
            soll = float(config[kp_name])
        except (TypeError, ValueError):
            soll = None
        if soll is not None and not (isinstance(kp, float) and abs(kp - soll) <= 1e-4 * max(1.0, soll)):
            probleme.append("Homing-Kp aktiv (ID 70 RAM %s statt Config %g) – würde dauerhaft"
                            % (_cal_zahl(kp), soll))
    pe = cal_id_wert(CAL_PROTECT_EN_ID, (ids.get(str(CAL_PROTECT_EN_ID)) or {}).get("ram"))
    if not isinstance(pe, int) or isinstance(pe, bool) or not 1 <= pe <= 255:
        probleme.append("protect_en (ID 173) im RAM = %s – erlaubt 1…255 (0 = Motor-Schutz "
                        "aus; ≥ 256 würde der Lauf als 0 speichern)" % (pe,))
    for pid in CAL_MODE_IDS:
        e = ids.get(str(pid)) or {}
        ram, fl = cal_id_wert(pid, e.get("ram")), cal_id_wert(pid, e.get("flash"))
        if ram is not None and ram != fl and ram != 2:
            probleme.append("ID %d %s = %s (vorübergehender Modus, Flash %s) – würde dauerhaft"
                            % (pid, cal_param_name(pid), ram, fl))
    rf = snap.get("ram_ungleich_flash") or []
    if rf:
        hinweise.append("Diese RAM-Werte werden dabei DAUERHAFT in den Flash geschrieben: "
                        + ", ".join("%d %s (%s → Flash bisher %s)" % (
                            i, cal_param_name(i),
                            _cal_zahl(cal_id_wert(i, ids[str(i)]["ram"])),
                            _cal_zahl(cal_id_wert(i, ids[str(i)]["flash"]))) for i in rf))
    return probleme, hinweise


def cal_run_antwort(mode, data):
    """Antwort des Kalibrier-Frames -> dict. ELEC: [pass u8, err f32, avg f32];
    ENCODER: 1 B, 0x0C = gelungen. Falsche Länge -> ValueError."""
    data = bytes(data)
    _fun, _t, laenge, _txt = CAL_MODES[mode]
    if len(data) != laenge:
        raise ValueError("Antwort %d statt %d Bytes (%s)" % (len(data), laenge, data.hex()))
    if mode == "ELEC":
        ok, err, avg = struct.unpack("<Bff", data)
        return {"raw": data.hex(), "pass": ok != 0, "pass_byte": ok,
                "err": _cal_unpack(struct.pack("<f", err), "<f"),
                "avg": _cal_unpack(struct.pack("<f", avg), "<f")}
    return {"raw": data.hex(), "pass": data[0] == CAL_ENCODER_OK, "status": data[0]}


def cal_nach_lauf(vorher, nachher, mode, antwort, motor):
    """Prüfung nach RUN (Spezifikation §8.6) -> (alarme, warnungen, infos, status)."""
    alarme, warn, info = [], [], []
    reg = nachher.get("regelung") or {}
    if (reg.get("en_pin"), reg.get("regler"), reg.get("pwm")) != (1, 0, 0):
        warn.append("0x05 [04] nach dem Lauf = %s (erwartet EN=1 Regler=0 PWM=0)" % reg.get("raw"))
    sch = nachher.get("schutz") or {}
    if not sch.get("ok"):
        warn.append("Fehlerbits nach dem Lauf: err=%s warn=%s – vor ENABLE prüfen und mit "
                    "KTWO_MOTOR_CLEAR löschen" % (sch.get("fehler"), sch.get("warnungen")))
    vk, nk = vorher["kalibrierung"], nachher["kalibrierung"]
    for wo in ("ram", "flash"):
        if vk["10"][wo + "_raw"] != nk["10"][wo + "_raw"]:
            alarme.append("phase_order_invert (%s) %s → %s – Umbruch- oder Blockierfehler der "
                          "FW, KTWO_MOTOR_CAL_RESTORE nötig" % (wo.upper(), _cal_zahl(vk["10"][wo]),
                                                                _cal_zahl(nk["10"][wo])))
    vids, nids = vorher["alle_ids"], nachher["alle_ids"]
    for wo in ("ram", "flash"):
        pe = (nids.get(str(CAL_PROTECT_EN_ID)) or {}).get(wo)
        if pe is not None and cal_id_wert(CAL_PROTECT_EN_ID, pe) == 0:
            alarme.append("protect_en (ID 173) im %s = 0 – Motor-Schutzfunktionen AUS%s"
                          % (wo.upper(), " (auch nach dem nächsten Einschalten)"
                             if wo == "flash" else ""))
    if mode == "ELEC" and antwort.get("pass"):
        for pid, soll in ((9, antwort.get("avg")), (25, antwort.get("err"))):
            e = nk[str(pid)]
            if not (_cal_gleich(e["ram"], soll) and _cal_gleich(e["flash"], soll)):
                warn.append("%s nach „pass“: RAM %s / Flash %s, Antwort %s"
                            % (e["name"], _cal_zahl(e["ram"]), _cal_zahl(e["flash"]),
                               _cal_zahl(soll)))
        info.append("Phasen-Kalibrierung bestanden: elec_offset %s → %s, Fehlerwinkel %s → %s°"
                    % (_cal_zahl(vk["9"]["flash"]), _cal_zahl(nk["9"]["flash"]),
                       _cal_zahl(vk["25"]["flash"]), _cal_zahl(nk["25"]["flash"])))
    elif mode == "ELEC":
        for pid in (9, 25):
            if vk[str(pid)]["flash_raw"] != nk[str(pid)]["flash_raw"]:
                warn.append("%s im Flash geändert, obwohl „failed“" % nk[str(pid)]["name"])
        warn.append("Phasen-Kalibrierung NICHT bestanden (Fehlerwinkel %s° ≥ Grenze %s° oder "
                    "keine Bewegung) – alte Werte bleiben" % (_cal_zahl(antwort.get("err")),
                                                              _cal_zahl(vk["8"]["flash"])))
    else:
        (info if antwort.get("pass") else warn).append(
            "Encoder-Kalibrierung: Status 0x%02X (%s)" % (
                antwort.get("status", 0), "gelungen" if antwort.get("pass") else "fehlgeschlagen"))
    # Erwartet: der Flash übernimmt den RAM-Stand von vorher (Seite-0-
    # Neuschreiben; protect_en nur das untere Byte). Alles andere ist auffällig.
    erwartet, fremd = [], []
    for k, e in sorted(nids.items(), key=lambda kv: int(kv[0])):
        if int(k) in (9, 10, 25) or k not in vids:
            continue
        neu_f, alt_f, alt_r = e.get("flash"), vids[k].get("flash"), vids[k].get("ram")
        if neu_f is None or alt_f is None or neu_f == alt_f:
            continue
        soll = alt_r
        if int(k) == CAL_PROTECT_EN_ID and alt_r is not None:
            soll = struct.pack("<H", cal_id_wert(CAL_PROTECT_EN_ID, alt_r) & 0xFF).hex()
        (erwartet if neu_f == soll else fremd).append(int(k))
    if erwartet:
        info.append("Flash übernimmt den RAM-Stand von vorher (wie angekündigt): IDs %s"
                    % ", ".join(str(i) for i in erwartet))
    if fremd:
        warn.append("Flash unerwartet geändert: IDs %s" % ", ".join(str(i) for i in fremd))
    if alarme:
        status = "phase_verdacht"
    elif mode == "ENCODER":
        status = "elec_pflicht"
    elif motor in ("z", "z1"):
        status = "z_ausrichtung_noetig"
    else:
        status = "homing_noetig"
    return alarme, warn, info, status


def cal_zustand_laden(verz):
    """Zustandsdatei -> {"motoren": {m: {...}}}. Fehlt sie: leer. Ist sie da,
    aber unlesbar/kaputt: {"motoren": {}, "fehler": text} – die Hülle
    behandelt das als „alle Motoren unbekannt“ (lieber gesperrt als frei)."""
    pfad = os.path.join(os.path.expanduser(verz), CAL_ZUSTAND_DATEI)
    if not os.path.exists(pfad):
        return {"motoren": {}}
    try:
        with open(pfad, encoding="utf-8") as fh:
            z = json.load(fh)
        if isinstance(z, dict) and isinstance(z.get("motoren"), dict) \
                and all(isinstance(e, dict) for e in z["motoren"].values()):
            return z
        fehler = "Inhalt ungültig"
    except (OSError, ValueError) as exc:
        fehler = "%s: %s" % (type(exc).__name__, exc)
    return {"motoren": {}, "fehler": "%s (%s)" % (pfad, fehler)}


def cal_zustand_schreiben(verz, z):
    """Atomar mit fsync (Datei 600, Ordner 700)."""
    verz = os.path.expanduser(verz)
    os.makedirs(verz, mode=0o700, exist_ok=True)
    os.chmod(verz, 0o700)
    pfad = os.path.join(verz, CAL_ZUSTAND_DATEI)
    tmp = pfad + ".tmp"
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(z, fh, indent=1, ensure_ascii=False)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, pfad)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    dfd = os.open(verz, os.O_RDONLY)       # Umbenennung dauerhaft (Netzschalter!)
    try:
        os.fsync(dfd)
    finally:
        os.close(dfd)
    return pfad


def cal_ergebnis_schreiben(verz, name, daten):
    """Ergebnisdatei eines Laufs (<motor>-<zeit>-lauf/-restore.json, 600). Der
    Name passt bewusst NICHT zu CAL_FILE_RE – CHECK nimmt sie nie als Sicherung."""
    verz = os.path.expanduser(verz)
    os.makedirs(verz, mode=0o700, exist_ok=True)
    pfad = os.path.join(verz, name)
    fd = os.open(pfad, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(daten, fh, indent=1, ensure_ascii=False)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    return pfad


# =====================================================================
# Config (rein + Klipper-Hülle)
# =====================================================================

def parse_options(opts):
    """Reines Dict (keys klein, Werte Strings) -> strukturierte Config.

    Unterstützt Stock-[motor_control] plus neue Optionen (ANNAHME, mit
    Stock-kompatiblem Default):
    - allow_write (Default False), shutdown_on_failure (Default False),
      auto_init (Default True), nozzle_mcu (Default 'nozzle_mcu'),
      main_mcu (Default 'mcu').
    """
    o = {str(k).strip().lower(): v for k, v in dict(opts).items()}
    if "switch" not in o:
        raise ValueError("[k2_motor] switch fehlt (Pflichtoption, wie Stock)")
    try:
        switch = int(str(o["switch"]).strip())
    except ValueError:
        raise ValueError("switch muss 0/1 sein: %r" % (o["switch"],))
    closed_raw = str(o.get("motor_closed_loop", "") or "")
    closed = [s.strip() for s in closed_raw.replace(";", ",").split(",") if s.strip()]
    for m in closed:
        if m not in MOTOR_NAME_TO_NUM:
            raise ValueError("motor_closed_loop: unbekannter Motor %r" % (m,))
    def _int(key, default):
        try:
            return int(str(o.get(key, default)).strip())
        except ValueError:
            raise ValueError("%s muss int sein: %r" % (key, o.get(key)))
    def _bool(key, default):
        v = str(o.get(key, str(default))).strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off"):
            return False
        raise ValueError("%s muss bool sein: %r" % (key, o.get(key)))
    cfg = {
        "switch": switch,
        "motor_closed_loop": closed,
        "retries": _int("retries", 4),
        "allow_write": _bool("allow_write", False),
        "shutdown_on_failure": _bool("shutdown_on_failure", False),
        "auto_init": _bool("auto_init", True),
        # FORCE_MOVE vor abgeschlossenem Motor-Init (bzw. vor X/Y-Homing)
        # sperren (Abgleich mit Creality-klippy, niedrig): Closed-Loop-Motoren
        # erst nach is_ready bewegen.
        "force_move_lock": _bool("force_move_lock", True),
        "nozzle_mcu": str(o.get("nozzle_mcu", "nozzle_mcu")).strip(),
        "main_mcu": str(o.get("main_mcu", "mcu")).strip(),
        # Ordner der Kalibrier-Sicherungen (KTWO_MOTOR_CAL_BACKUP/_CHECK)
        "cal_backup_dir": str(o.get("cal_backup_dir", CAL_BACKUP_DIR_DEFAULT)
                              or CAL_BACKUP_DIR_DEFAULT).strip(),
        # Kalibrierlauf/Rückschreiben (KTWO_MOTOR_CAL_RUN/_RESTORE): Default AUS
        "allow_motor_calibration": _bool("allow_motor_calibration", False),
        "allow_encoder_calibration": _bool("allow_encoder_calibration", False),
        "allow_z_calibration": _bool("allow_z_calibration", False),
        "homing_pos_kp": HOMING_POS_KP_DEFAULT,
        "stall_monitor": str(o.get("stall_monitor", STALL_MONITOR_DEFAULT)
                             or STALL_MONITOR_DEFAULT).strip().lower(),
        "pins": {}, "params": {},
    }
    if cfg["stall_monitor"] not in STALL_MONITOR_MODES:
        raise ValueError("stall_monitor muss off|report|act sein: %r"
                         % (o.get("stall_monitor"),))
    if cfg["retries"] < 1:
        raise ValueError("retries muss >= 1 sein")
    if o.get("homing_pos_kp") not in (None, ""):
        try:
            hk = float(str(o["homing_pos_kp"]).strip())
        except ValueError:
            raise ValueError("homing_pos_kp muss Zahl sein: %r" % (o["homing_pos_kp"],))
        if not math.isfinite(hk) or not 0.0 < hk <= 10000.0:
            raise ValueError("homing_pos_kp muss 0 < Wert <= 10000 sein: %r" % (hk,))
        cfg["homing_pos_kp"] = hk
    # Schnittpositions-Kalibrierung: Optionen mit Stock-Default, harte Grenzen
    cfg["cut_cal"] = {}
    for opt, key in CUT_CAL_OPTIONS.items():
        val = CUT_CAL_DEFAULTS[key]
        if o.get(opt) not in (None, ""):
            try:
                val = float(str(o[opt]).strip())
            except ValueError:
                raise ValueError("%s muss Zahl sein: %r" % (opt, o[opt]))
        lo, hi = CUT_CAL_LIMITS[key]
        if not math.isfinite(val) or not lo <= val <= hi:
            raise ValueError("%s muss %g..%g sein: %r" % (opt, lo, hi, val))
        cfg["cut_cal"][key] = val
    for m in VERIFY_ORDER:
        cfg["pins"][m] = {
            "dir": parse_pin_level(o.get("motor_%s_dir" % m)),
            "step": parse_pin_level(o.get("motor_%s_step" % m)),
            "stall": (str(o.get("motor_%s_stall" % m)).strip()
                      if o.get("motor_%s_stall" % m) not in (None, "") else None),
        }
        cfg["params"][m] = {}
    known = {"switch", "motor_closed_loop", "retries", "allow_write",
             "shutdown_on_failure", "auto_init", "nozzle_mcu", "main_mcu",
             "overcurrent_switch", "cut_pos_offset", "homing_pos_kp",
             "stall_monitor", "force_move_lock"}
    known.update(CUT_CAL_OPTIONS)
    import re as _re
    by_name = {p["name"]: p for p in default_param_table()}
    for key, val in o.items():
        if key in known or _re.match(r"^motor_(x|y|z|z1|e)_(dir|step|stall)$", key):
            continue
        m = _re.match(r"^(z1|x|y|z|e)_(.+)$", key)
        if not m:
            continue  # unbekannte Option ignorieren (wie Stock stille Filter)
        motor, pname = m.group(1), m.group(2)
        if motor not in cfg["params"]:
            continue
        if pname not in by_name:
            continue  # unbekannter Param – ignorieren (Stock: still)
        if not by_name[pname]["configurable"]:
            continue  # nicht configurable – still ignorieren (wie Stock)
        try:
            import ast as _ast
            v = _ast.literal_eval(str(val).strip())
        except Exception:
            raise ValueError("Config-Wert nicht auswertbar: %s=%r" % (key, val))
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ValueError("Config-Wert muss Zahl sein: %s=%r" % (key, val))
        cfg["params"][motor][pname] = v
    return cfg


def parse_klipper_config(config):
    """Klipper-ConfigWrapper -> parse_options (ANNAHME zur API).

    Versucht der Reihe nach: get_options() für freie <motor>_<param>-Keys,
    sonst getlist/getint/getfloat/getboolean/get. FakeConfig in Tests bietet
    dieselben Methoden.
    """
    keys = []
    try:
        if hasattr(config, "get_options"):
            keys = list(config.get_options())
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
    # Pflicht/Fix-Keys (Typen tolerant lesen – Klipper bietet getint etc.)
    def _gi(name, default):
        try:
            if hasattr(config, "getint"):
                return config.getint(name, default)
        except Exception:
            pass
        try:
            return int(str(_g(name, default)).strip())
        except Exception:
            return default
    def _gb(name, default):
        try:
            if hasattr(config, "getboolean"):
                return config.getboolean(name, default)
        except Exception:
            pass
        return _g(name, str(default))
    if not keys and isinstance(config, dict):
        return parse_options(config)
    for k in keys:
        opts[k.lower()] = _g(k, "")
    # Falls get_options leer war (manche Wrapper), Fix-Keys direkt holen:
    for k in ("switch", "motor_closed_loop", "retries", "allow_write",
              "shutdown_on_failure", "auto_init", "nozzle_mcu", "main_mcu",
              "overcurrent_switch", "cut_pos_offset", "homing_pos_kp",
              "stall_monitor", "force_move_lock", "cal_backup_dir",
              "allow_motor_calibration", "allow_encoder_calibration",
              "allow_z_calibration") \
            + tuple(sorted(CUT_CAL_OPTIONS)):
        if k not in opts:
            v = _g(k, None)
            if v is not None:
                opts[k] = v
    # Motor-Pins/Params, falls nicht in keys enthalten:
    import re as _re
    probe = []
    for m in VERIFY_ORDER:
        for tail in ("dir", "step", "stall"):
            probe.append("motor_%s_%s" % (m, tail))
    # Param-Keys aus der Tabelle (nur configurable, wie Stock relevant):
    for p in default_param_table():
        if p["configurable"]:
            for m in VERIFY_ORDER:
                probe.append("%s_%s" % (m, p["name"]))
    for k in probe:
        if k not in opts:
            v = _g(k, None)
            if v is not None and str(v) != "":
                opts[k] = v
    if "switch" not in opts:
        # getint-Variante (Klipper-Style, Pflichtoption)
        try:
            if hasattr(config, "getint"):
                opts["switch"] = config.getint("switch")
        except Exception as exc:
            raise ValueError("[k2_motor] switch fehlt (Pflichtoption): %s" % exc)
    if "motor_closed_loop" not in opts:
        try:
            if hasattr(config, "getlist"):
                opts["motor_closed_loop"] = ",".join(
                    config.getlist("motor_closed_loop", []))
        except Exception:
            pass
    _ = _gi; _ = _gb
    return parse_options(opts)


# =====================================================================
# Schnittpositions-Kalibrierung – Parameter (rein, testbar)
# =====================================================================

def build_cut_cal_params(cut_cfg, box_cfg=None):
    """[k2_motor]-cut_cal-Werte + Prüfbereich/Y aus [k2_box] -> Parameter.

    ``box_cfg`` = ``printer.lookup_object('k2_box').cfg`` (dict) oder None.
    Vorrang für x_min/x_max/y: [k2_box] check_cut_pos_x_min/_max, cut_pos_y
    (dieselben Grenzen prüft k2_box beim Laden von cut_pos_x – ein Ergebnis
    außerhalb würde den Neustart nach SAVE_CONFIG verhindern)."""
    p = dict(CUT_CAL_DEFAULTS)
    p.update(dict(cut_cfg or {}))
    bc = dict(box_cfg or {})
    for key, bkey in (("x_min", "check_cut_pos_x_min"),
                      ("x_max", "check_cut_pos_x_max"), ("y", "cut_pos_y")):
        if bc.get(bkey) is not None:
            try:
                p[key] = float(bc[bkey])
            except (TypeError, ValueError):
                raise K2MotorError("[k2_box] %s keine Zahl: %r" % (bkey, bc[bkey]))
    validate_cut_cal_params(p)
    return p


def validate_cut_cal_params(p):
    """Harte Grenzen + Zusammenhänge; K2MotorError bei Verstoß."""
    for key, (lo, hi) in CUT_CAL_LIMITS.items():
        v = p.get(key)
        if isinstance(v, bool) or not isinstance(v, (int, float)) \
                or not math.isfinite(v) or not lo <= v <= hi:
            raise K2MotorError("Cut-Kalibrierung: %s=%r außerhalb %g..%g"
                               % (key, v, lo, hi))
    if not p["x_min"] < p["x_max"]:
        raise K2MotorError("Cut-Kalibrierung: x_min %.2f muss < x_max %.2f sein"
                           % (p["x_min"], p["x_max"]))
    if not p["start_x"] > p["x_min"]:
        raise K2MotorError("Cut-Kalibrierung: start_x %.2f muss > x_min %.2f sein"
                           % (p["start_x"], p["x_min"]))
    if p["park_x"] < p["start_x"] + CUT_CAL_MIN_PARK_GAP:
        raise K2MotorError("Cut-Kalibrierung: park_x %.1f muss >= start_x + %g sein"
                           % (p["park_x"], CUT_CAL_MIN_PARK_GAP))
    if p["exit_x"] < p["park_x"]:
        raise K2MotorError("Cut-Kalibrierung: exit_x %.1f muss >= park_x %.1f sein"
                           % (p["exit_x"], p["park_x"]))
    n = len(cut_cal_positions(p, check=False))
    if n > CUT_CAL_MAX_PROBES:
        raise K2MotorError("Cut-Kalibrierung: %d Anstöße > Obergrenze %d (step/start_x/"
                           "x_min prüfen)" % (n, CUT_CAL_MAX_PROBES))
    return p


def cut_cal_positions(p, check=True):
    """Anstoß-Ziele start_x, start_x-step, … bis einschließlich x_min (nie tiefer)."""
    if check:
        validate_cut_cal_params(p)
    out = []
    i = 0
    while True:
        x = round(p["start_x"] - i * p["step"], 4)
        if x < p["x_min"] - CUT_CAL_EPS or i > 10 * CUT_CAL_MAX_PROBES:
            break
        out.append(max(x, p["x_min"]))
        i += 1
    return out


def _req_hex(addr, fun, data):
    """Request wie im Stock-Log ("retries = 0, cmd = …"): addr len 00 fun data."""
    d = list(data)
    return " ".join("%02x" % b for b in [addr, len(d) + 3, 0x00, fun] + d)


def describe_cut_cal(p, restore_kp):
    """Plan-Text für DRY_RUN (nichts gesendet, nichts bewegt)."""
    xs = cut_cal_positions(p)
    kp = _req_hex(0x81, FUNC_SYS_PARAM, [SUB_SYS_WRITE, HOMING_PARAM_ID]
                  + list(_float_to_bytes(p["pos_kp"])))
    kb = _req_hex(0x81, FUNC_SYS_PARAM, [SUB_SYS_WRITE, HOMING_PARAM_ID]
                  + list(_float_to_bytes(restore_kp)))
    return "\n".join([
        "Cut-Kalibrierung Plan (Trockenlauf, nichts gesendet, keine Bewegung)",
        "1. Beschleunigung <= %g mm/s^2 (Stock SET_LIMITS / ACCEL=25000)" % p["accel"],
        "2. Kp x ID 70 = %g: %s" % (p["pos_kp"], kp),
        "3. Park X%.2f Y%.2f mit %.1f mm/s" % (p["park_x"], p["y"], p["park_speed"]),
        "4. Stall-Mode x lesen: %s, setzen 3: %s" % (
            _req_hex(0x81, FUNC_SET_MODE, [SUB_STALL_GET]),
            _req_hex(0x81, FUNC_SET_MODE, [CUT_CAL_MODE])),
        "5. %d Anstöße X%.2f…X%.2f (Schritt %.2f, %.1f mm/s, Endstop PB11, "
        "Nachlauf %.2f s), je zurück auf Park; Treffer zählen ab X<=%.2f" % (
            len(xs), xs[0], xs[-1], p["step"], p["probe_speed"], p["settle"], p["x_max"]),
        "6. cut_pos_x = 2. Treffer + %.2f, gültig %.2f..%.2f -> configfile.set "
        "[%s] %s (SAVE_CONFIG separat)" % (p["offset"], p["x_min"], p["x_max"],
                                          CUT_CAL_SECTION, CUT_CAL_OPTION),
        "7. Ausgang X%.2f Y%.2f, Stall-Mode 2: %s, Kp %g: %s, Beschleunigung zurück" % (
            p["exit_x"], p["y"], _req_hex(0x81, FUNC_SET_MODE, [STALL_MODE_EMERGENCY]),
            restore_kp, kb),
    ])


# =====================================================================
# GPIO-Adapter (klein, testbar) – REVIEW 1–3 eingearbeitet (16.10.2026)
# =====================================================================

# Enable-Pin, fest im Stock-Modul (Analyse des Stock-Moduls: Strings 'enable_pin_pa9',
# '!PA9', 'force stop,set !PA9 value=0'). Wird hier FRÜH mit angelegt
# (Review 1), vom Controller derzeit nicht aktiv getrieben (nur Doku;
# künftige Enable-Steuerung nutzt denselben Pfad).
ENABLE_PIN_RAW = "!PA9"


def _clean_pin_name(pin_raw):
    """'!PB7' / 'mcu:PB7' -> 'PB7' (Invert- und MCU-Präfix entfernen)."""
    s = str(pin_raw).strip()
    if ":" in s:
        s = s.split(":", 1)[1]
    return s.lstrip("!^~").strip()


def pin_number_from_enum(enum_pin, pin_name):
    """Klipper-Pinnummer aus der MCU-Enumeration 'pin' auflösen.

    Dictionary-Beleg (mcu-main-F008-GD32F303RET6.dict.json): nur Basis-Einträge
    ``PA0 [0,16], PB0 [16,16], PC0 [32,16], PD0 [48,16]`` – [Basis, Anzahl].
    ``PB9`` = Basis PB0 (16) + 9 = 25. Direkte Einträge (z.B. ADC_TEMPERATURE)
    werden direkt zurückgegeben. ANNAHME: Offset-Logik für PxN (Upstream-Muster).
    """
    clean = _clean_pin_name(pin_name)
    if not isinstance(enum_pin, dict):
        raise ValueError("Pin-Enumeration fehlt")
    if clean in enum_pin:
        v = enum_pin[clean]
        if isinstance(v, (list, tuple)):
            return int(v[0])
        return int(v)
    # Basis + Offset (z.B. PB9 über PB0 [16,16]):
    import re as _re
    m = _re.match(r"^([A-Z]+)(\d+)$", clean)
    if m:
        base_key = "%s0" % m.group(1)
        if base_key in enum_pin:
            v = enum_pin[base_key]
            base = int(v[0]) if isinstance(v, (list, tuple)) else int(v)
            return base + int(m.group(2))
    raise ValueError("Pin %r nicht in Enumeration" % (pin_name,))



def reactor_sleep(reactor):
    """Relative Pause für Klipper: reactor.pause() erwartet eine ABSOLUTE
    Weckzeit (upstream reactor.py: pause(waketime)). Ohne Reactor -> time.sleep."""
    if reactor is None or not hasattr(reactor, "pause"):
        return time.sleep
    return lambda s: reactor.pause(reactor.monotonic() + float(s))

def _mcu_chip_name(name):
    """Config-/Objektname -> Pin-Chip-Name ('mcu nozzle_mcu' -> 'nozzle_mcu')."""
    name = str(name).strip()
    return name[4:].strip() if name.startswith("mcu ") else name


def _lookup_mcu(printer, name):
    """MCU-Objekt nach Config-Namen (wie k2_prtouch._lookup_mcu, Fix 16.10.2026).

    Upstream registriert '[mcu]' als 'mcu' und '[mcu nozzle_mcu]' als
    'mcu nozzle_mcu' (mcu.py add_printer_objects/get_printer_mcu). Config-Wert
    'nozzle_mcu' wird daher zuerst als 'mcu nozzle_mcu' gesucht; der volle
    Objektname ('mcu nozzle_mcu') und 'mcu' gehen direkt. Fallback: wörtlich."""
    name = str(name).strip()
    if name != "mcu" and not name.startswith("mcu "):
        try:
            return printer.lookup_object("mcu " + name)
        except Exception:
            pass
    return printer.lookup_object(name)


class GpioOps:
    """Protokoll: Strapping-Pins setzen + optional zurücklesen."""

    def set_pin(self, pin_raw, level):
        raise NotImplementedError

    def sync(self):
        """Queue leeren (M400-Äquivalent) – Default: nichts zu tun (Fake)."""
        return None

    def query_pin(self, pin_raw):
        """-> phys-Pegel (0/1) oder None (wenn Rücklesung nicht vorhanden)."""
        return None


class KlippyGpio(GpioOps):
    """Echte GPIO-Anbindung (ANNAHMEN, Review-Korrekturen).

    REVIEW 1 (Pins zu spät – FIX): Alle Strapping-Pins werden im KONSTRUKTOR
    (load_config-Phase, vor klippy:connect) per ``pins.setup_pin("digital_out",
    pin)`` angelegt (Upstream-Objekt ``pins``). ``setup_pin`` registriert intern
    ``mcu.register_config_callback`` + ``create_oid`` + ``config_digital_out``
    (Upstream-Muster inkl. ``[duplicate_pin_override]`` für das Zeitmultiplex
    mit ``config_stepper``). Zur Laufzeit wird nur noch gesetzt – kein Lazy-
    Anlegen mehr (altes ``_output_pin`` entfernt).

    REVIEW 2 (print_time=0 – FIX, Entscheidung begründet): Variante (a)
    gequeue'tes ``digital_out`` mit gültiger print_time + M400 – NICHT Variante
    (b) direkter ``set_digital_out pin=%u value=%c``. Begründung: (a) entspricht
    dem Stock (Analyse der GPIO-Phase, B: ``update_digital_out`` via ``set_digital`` +
    Lookahead + ``M400``), respektiert das ``duplicate_pin_override``-
    Zeitmultiplex mit ``config_stepper`` (M400 synchronisiert Stepper-Queue und
    Strapping – sofortiges ``set_digital_out`` würde die Move-Queue umgehen),
    und ``set_digital_out`` ist im Stock-``.so`` negativ belegt (keine einzige
    Referenz). ``set_pin`` holt daher
    ``toolhead.get_last_move_time()`` (Fallback
    ``mcu.estimated_print_time(reactor.monotonic()) + 0,100 s``) und ruft
    ``pin.set_digital(print_time, level)``; ``sync()`` macht
    ``toolhead.wait_moves()`` (Fallback ``gcode.run_script("M400")``) – der
    Controller ruft ``sync()`` nach jedem Setzen vor Broadcast/Weiterlesen.
    Gekapselt hinter GpioOps (Tests mit FakePrinter prüfen print_time>0).

    REVIEW 3 (query_stub – FIX) + Firmware-Analyse 16.10.2026 (Abfrage-OID):
    ``query_gpio_status oid=%c gpio_pin=%c`` → ``gpio_status oid=%c status=%c``
    per ``mcu.lookup_query_command``. Dictionary (mcu-main-F008-
    GD32F303RET6.dict.json): Befehl 75 + Antwort -20 vorhanden, KEIN
    zugehöriger ``config_*``-Befehl. Disassembly des Main-MCU-Images
    (Stock ``mcu0_140_G32-mcu0_022_000.bin``, Basis 0x08003000, Befehlstabelle
    @0x0800743C, 12-Byte-Einträge; die Handler-Adresse zu einem Info-Wort
    (Byte0 = msgid) steht 12 Byte WEITER – eine naiv erzeugte Handler-Liste
    ordnet deshalb um einen Eintrag verschoben zu; gegengeprüft an
    config_digital_out → 0x08006050 (gpio_out_setup + oid_alloc) und
    config_transparent → 0x080036AC (oid_alloc + USART-Init)):
    Handler query_gpio_status = 0x08003424: liest ``args[1]`` (Pin), Port =
    Pin>>4 aus der Tabelle 0x0800987C (GPIOA..E 0x40010800…), Bit = Pin&15,
    liest Register-Offset 0x0C (GD32 GPIOx_OCTL = Ausgangs-Latch, NICHT
    ISTAT) und antwortet ``gpio_status`` mit ``args[0]`` UNVERÄNDERT als oid.
    KEIN oid_lookup/oid_alloc → die OID ist reine Antwort-Kennung. Daher wird
    KEINE OID per ``create_oid`` verbraucht (vorher: eigenes OID ohne
    config-Befehl), sondern die Konstante ``GPIO_QUERY_OID`` (0) genutzt –
    das Upstream-Antwort-Routing filtert nur nach (``gpio_status``, oid), und
    nur dieses Modul sendet ``query_gpio_status``. Stock nutzte oid 11 für alle
    Pins (Log ``:2259–2266``) – ebenfalls nur Kennung. Folge der OCTL-Lesung:
    die Rücklesung bestätigt den von der MCU AUSGEGEBENEN Pegel (wie Stock
    key799/800), keinen Leitungsfehler. Fehlt der Befehl im Dictionary
    (Upstream-FW), liefert ``handle_connect`` keinen Query und ``query_pin``
    None (Prüfung übersprungen).

    STRAPPING-PINS ``max_duration`` (Fix 16.10.2026): Upstream-
    ``MCU_digital_out`` hat Default ``max_duration = 2 s`` – steht ein Pin
    länger auf Nicht-Default (z. B. Z-STEP 1, Z1 DIR/STEP 1, Y-DIR !PB7 phys 1
    während der ~10 s Init), schaltet die MCU ab. Stock ruft für JEDEN
    Dir/Step-Pin (``motor_x/y/z/z1_init``, 0x73ba8/0x73c54 …) und für
    ``enable_pin_pa9`` (0xce8bc) ``setup_max_duration(0.0)`` auf (BELEGT:
    PYOBJ 'setup_max_duration' + CONST float 0.0 direkt nach 'digital_out'/
    'motor_<m>_dir|step', aus der Analyse des Stock-Wrappers).
    Wir machen es genauso (= Upstream-Muster für statische Pins, vgl.
    output_pin.py ``setup_max_duration(0.)``, stepper_enable). Start- und
    Shutdown-Wert bleiben Upstream-Default (= Invertierung → phys. 0, bei
    ``!PA9`` phys. 1 = Treiber aus).
    """

    QUERY_FMT = "query_gpio_status oid=%c gpio_pin=%c"
    QUERY_RESP = "gpio_status oid=%c status=%c"
    # Firmware wertet die OID nicht aus (s. Docstring) -> keine create_oid.
    GPIO_QUERY_OID = 0
    # Stock: setup_max_duration(0.0) für alle Strapping-Pins (s. Docstring).
    STRAPPING_MAX_DURATION = 0.0

    def __init__(self, printer, main_mcu_name="mcu", pin_specs=None):
        self.printer = printer
        self.main_mcu_name = main_mcu_name
        self._pins = {}
        self._pin_numbers = {}
        self._query = None
        self._query_available = None
        self._query_oid = None
        specs = list(pin_specs or [])
        # Deduplizieren, Reihenfolge behalten; leere/None überspringen.
        seen, uniq = set(), []
        for raw in specs:
            if raw is None or str(raw) == "":
                continue
            if raw not in seen:
                seen.add(raw)
                uniq.append(raw)
        # FRÜH (vor connect): pins.setup_pin für jeden Main-MCU-Pin
        # (Upstream-Objekt 'pins', pins.py:138; Stock ebenso 'pins').
        # Pins anderer MCUs (z.B. 'nozzle_mcu:PB12' E-Stall) werden übersprungen
        # – sie gehören nicht zum Strapping und haben eigene MCU-Objekte.
        ppins = printer.lookup_object("pins")
        chip = _mcu_chip_name(main_mcu_name)
        for raw in uniq:
            if ":" in str(raw):
                prefix = str(raw).split(":", 1)[0].lstrip("!^~").strip()
                if prefix not in (main_mcu_name, chip):
                    continue
            # Upstream-Signatur: setup_pin('digital_out', pin_desc) -> MCU_digital_out
            pin = ppins.setup_pin("digital_out", raw)
            # Statischer Pin wie Stock: max_duration 0 (kein MCU-Shutdown, wenn
            # der Strapping-Pegel länger als 2 s ansteht, s. Docstring).
            if hasattr(pin, "setup_max_duration"):
                pin.setup_max_duration(self.STRAPPING_MAX_DURATION)
            self._pins[raw] = pin
        # Pin-Nummern (gpio_pin-Byte) – das Dictionary (Enumeration 'pin') ist
        # erst nach dem Connect da; hier nur versuchen, sonst lazy in query_pin.
        for raw in list(self._pins.keys()):
            try:
                self._pin_numbers[raw] = self._resolve_pin_number(raw)
            except Exception:
                pass
        # Antwort-Kennung ohne create_oid (Firmware wertet die OID nicht aus).
        self._query_oid = self.GPIO_QUERY_OID

    def _resolve_pin_number(self, pin_raw):
        # Upstream: pins.lookup_pin liefert nur den Pin-NAMEN (pin_params
        # ['pin'] = 'PB9') und registriert den Pin als belegt – daher NICHT
        # verwendet. Nummer aus der MCU-Enumeration 'pin' (Dictionary, erst
        # nach dem Connect verfügbar).
        # 1) mcu.get_enumeration('pin', name) / get_enumerations()['pin'].
        try:
            mcu = _lookup_mcu(self.printer, self.main_mcu_name)
            if hasattr(mcu, "get_enumeration"):
                return int(mcu.get_enumeration("pin", _clean_pin_name(pin_raw)))
            if hasattr(mcu, "get_enumerations"):
                enum = dict(mcu.get_enumerations()).get("pin", {})
                return pin_number_from_enum(enum, pin_raw)
        except Exception:
            pass
        # 2) Direkte Enumeration über das pins-Objekt (nur Test-Helper).
        try:
            ppins = self.printer.lookup_object("pins")
            if hasattr(ppins, "get_enumerations"):
                enum = dict(ppins.get_enumerations()).get("pin", {})
                return pin_number_from_enum(enum, pin_raw)
        except Exception:
            pass
        raise ValueError("Pin-Nummer für %r nicht auflösbar" % (pin_raw,))

    def handle_connect(self):
        """Query-Befehl nach klippy:connect auflösen (optional, sonst None)."""
        try:
            mcu = _lookup_mcu(self.printer, self.main_mcu_name)
        except Exception:
            self._query = None
            self._query_available = False
            return
        try:
            # Upstream: try_lookup_command(msgformat) -> None, wenn der Befehl
            # im Dictionary fehlt (mcu.py:1206); erst dann den Query anlegen.
            if hasattr(mcu, "try_lookup_command"):
                if mcu.try_lookup_command(self.QUERY_FMT) is None:
                    self._query = None
                    self._query_available = False
                    return
            self._query = mcu.lookup_query_command(
                self.QUERY_FMT, self.QUERY_RESP, oid=self._query_oid)
            self._query_available = True
        except Exception:
            self._query = None
            self._query_available = False

    def set_pin(self, pin_raw, level):
        if pin_raw not in self._pins:
            raise KeyError(
                "Pin %r nicht in load_config angelegt (setup_pin fehlt vor "
                "connect – Review 1)" % (pin_raw,))
        pin = self._pins[pin_raw]
        # Gültige print_time (Review 2, Variante a – s. Docstring).
        try:
            toolhead = self.printer.lookup_object("toolhead")
            print_time = toolhead.get_last_move_time()
        except Exception:
            mcu = _lookup_mcu(self.printer, self.main_mcu_name)
            reactor = self.printer.get_reactor()
            print_time = mcu.estimated_print_time(reactor.monotonic()) + 0.100
        if print_time is None or float(print_time) <= 0:
            raise RuntimeError(
                "Ungültige print_time %r für set_digital (Review 2)" % (print_time,))
        pin.set_digital(float(print_time), int(level))

    def sync(self):
        """M400-Äquivalent: Queue leeren, bevor Broadcast gesendet wird."""
        try:
            toolhead = self.printer.lookup_object("toolhead")
            if hasattr(toolhead, "wait_moves"):
                toolhead.wait_moves()
                return
        except Exception:
            pass
        try:
            gcode = self.printer.lookup_object("gcode")
            if hasattr(gcode, "run_script"):
                gcode.run_script("M400")
                return
        except Exception:
            pass

    def query_pin(self, pin_raw):
        if self._query is None or self._query_available is False:
            return None
        try:
            pin_num = self._pin_numbers.get(pin_raw)
            if pin_num is None:
                pin_num = self._resolve_pin_number(pin_raw)
                self._pin_numbers[pin_raw] = pin_num
            res = self._query.send([int(self._query_oid), int(pin_num)])
            if not isinstance(res, dict) or "status" not in res:
                return None
            return int(res["status"])
        except Exception:
            return None


# =====================================================================
# Controller – reine Init-Logik (ohne Klipper importierbar)
# =====================================================================

class K2MotorController:
    """Führt P0–P9 über Rs485Transport + TransparentTransport + GpioOps aus."""

    def __init__(self, cfg, rs485, transparent, gpio, pause=None,
                 logger=None, send_event=None, clock=None):
        self.cfg = cfg
        # Uhr für die Stall-Sperrfenster (Klipper: reactor.monotonic – gleiche
        # Zeitbasis wie die eventtime der buttons-Callbacks)
        self.clock = clock if clock is not None else time.monotonic
        self.rs485 = rs485
        self.transparent = transparent
        self.gpio = gpio
        self.pause = pause if pause is not None else time.sleep
        self.log = logger if logger is not None else self._print_log()
        self.send_event = send_event
        self.is_ready = False
        self.pin_levels = {}  # pin_raw -> level (für Diagnose/Tests)
        # Sensorless-Homing-Zustand (KTWO_MOTOR_HOMING / _STALL_MODE):
        # zuletzt per Echo bestätigter Stall-Mode je Motor (None = unbekannt)
        self.stall_mode = {m: None for m in STALL_READ_MOTORS}
        self.homing_active = False  # BEGIN ok, END noch nicht gelaufen
        self.last_error = None
        # Schnittpositions-Kalibrierung (KTWO_MOTOR_CUT_CAL)
        self.cut_cal_active = False
        self.cut_cal_last = None  # dict des letzten Laufs (Status/Makro)
        # Stall-Überwachung: X/Y-Sperre (Stock is_homing) – True ab dem Senden
        # von Mode 1/3, frei erst nach bestätigtem Mode 2 + STALL_GUARD_AFTER.
        # Fehlgeschlagener Wechsel auf 2 lässt die Sperre stehen (Zustand
        # unbekannt -> lieber blind als Fehlauslösung; Status zeigt es).
        self.stall_guard = {m: False for m in STALL_HOMING_MOTORS}
        self.stall_guard_until = {m: 0.0 for m in STALL_HOMING_MOTORS}
        self._table = default_param_table()
        self._by_name = {p["name"]: p for p in self._table}
        # Kalibrierlauf aktiv (Motorname) – Stall-Überwachung schweigt solange
        self.cal_aktiv = None
        # protect_en (ID 173) je Motor nach dem Init: Zahl oder Fehlertext
        self.protect_en = {}

    class _print_log:
        def info(self, msg):
            print(msg)
        def error(self, msg):
            print("FEHLER: %s" % msg)

    # --- Helfer -------------------------------------------------------
    def _transport_for(self, motor):
        if motor == "e":
            return self.transparent, TRANSPARENT_ADDR
        num = NUM_BY_NAME_INIT[motor]
        addr = 0x80 + num
        if addr == STOCK_E_ADDR:
            raise RuntimeError("Guard: 0x85 darf nie gesendet werden (Stock-Bug)")
        return self.rs485, addr

    def _timeout_for(self, motor, default):
        if motor == "e":
            return TIMEOUT_TRANSPARENT if default in (TIMEOUT_ADDR,) else default
        return default

    def _transact(self, motor, fun, payload, timeout, retries=0):
        tr, addr = self._transport_for(motor)
        if addr == STOCK_E_ADDR:
            raise RuntimeError("Guard: 0x85 blockiert (Stock-Bug, nutze transparent 0x81)")
        return tr.transact(addr, fun, list(payload), timeout=timeout, retries=retries)

    def _set_strapping(self, phys_map):
        """phys_map: [(pin_raw, level)] – nur mit allow_write."""
        for pin_raw, level in phys_map:
            self.gpio.set_pin(pin_raw, int(level))
            self.pin_levels[pin_raw] = int(level)
        # M400-Äquivalent (Review 2): Queue leeren, bevor Broadcast folgt.
        try:
            self.gpio.sync()
        except Exception:
            pass

    def _query_all_pins(self, pin_list):
        """-> {pin_raw: phys oder None} (None = keine Rücklesung möglich)."""
        out = {}
        for _m, _kind, spec in pin_list:
            try:
                out[spec["raw"]] = self.gpio.query_pin(spec["raw"])
            except Exception:
                out[spec["raw"]] = None
        return out

    def _pin_list(self):
        lst = []
        for m in PIN_MOTORS:
            p = self.cfg["pins"].get(m) or {}
            for kind in ("dir", "step"):
                if p.get(kind):
                    lst.append((m, kind, p[kind]))
        return lst

    def _strapping_rows(self):
        rows = []
        for m in PIN_MOTORS:
            p = self.cfg["pins"].get(m) or {}
            if not p.get("dir") or not p.get("step"):
                continue
            addr = strapping_address(p["dir"]["phys"], p["step"]["phys"])
            exp = 0x80 + NUM_BY_NAME_INIT[m]
            rows.append((m, p["dir"], p["step"], addr, exp, addr == exp))
        return rows

    # --- Plan-Text (Trockenlauf, kein Bus) ------------------------------
    def describe_plan(self):
        cfg = self.cfg
        out = ["K2-Motor-Init-Plan (Trockenlauf, nichts gesendet)"]
        out.append("switch=%d retries=%d closed=%s allow_write=%s" % (
            cfg["switch"], cfg["retries"], ",".join(cfg["motor_closed_loop"]),
            cfg["allow_write"]))
        for m, d, s, addr, exp, ok in self._strapping_rows():
            out.append("Strapping %s DIR %s=%d STEP %s=%d -> 0x%02X (erwartet 0x%02X) %s" % (
                m, d["raw"], d["phys"], s["raw"], s["phys"], addr, exp,
                "ok" if ok else "FALSCH"))
        out.append("P0 transparent-Kanal (Düsen-MCU %s)" % cfg.get("nozzle_mcu"))
        out.append("P1 %d GPIO-Pegel + Rücklesen, 0,2 s" % len(self._pin_list()))
        out.append("P2 Broadcast FF 0E [01] (RS485 + transparent), 0,1 s; "
                   "0E [02] je Motor (E transparent 0x81); max. %d Runden" % cfg["retries"])
        out.append("P3 All motors are normal")
        out.append("P4 Stall-Mode 11 [02] an Z/Z1/E (je <=3)")
        out.append("P5 Schutz 0C [0b] an alle (8 B dekodiert)")
        out.append("P6 Clear 0C [05] unbedingt (live unbeantwortet)")
        out.append("P7 Restore alle Pins phys 0, 0,1 s + Rücklesen, 1,0 s")
        n_r = n_w = 0
        for m in VERIFY_ORDER:
            if not (self.cfg["pins"].get(m) or {}).get("stall"):
                continue
            for p in self._table:
                if not p["configurable"]:
                    continue
                configured = p["name"] in self.cfg["params"].get(m, {})
                if p["id"] not in ALWAYS_READ and not configured:
                    continue
                if p["id"] in HOST_ONLY_IDS:
                    continue
                n_r += 1
                if p["id"] not in ALWAYS_READ and p["value_type"] == "float":
                    n_w += 1
        out.append("P8 Param-Abgleich: %d Lesen, %d bedingte Writes, je Motor 06 [03]; "
                   "E via transparent 0x81 (nie 0x85); ID 226 nie" % (n_r, n_w))
        out.append("P9 is_ready + Event k2_motor:ready")
        if not cfg["allow_write"]:
            out.append("Hinweis: allow_write=False → nur lesen + Plan (keine "
                       "schreibenden Frames: Broadcast/Stall/Clear/Write/Update, kein GPIO-Setzen)")
        return "\n".join(out)

    # --- Hauptlauf ------------------------------------------------------
    def run_init(self, dry_run=False):
        """P0–P9. Rückgabe: dict(status, log[], errors[], ready)."""
        cfg = self.cfg
        logs, errors = [], []
        def info(msg):
            logs.append(("info", msg))
            try:
                self.log.info(msg)
            except Exception:
                pass
        def err(msg):
            errors.append(msg)
            logs.append(("error", msg))
            try:
                self.log.error(msg)
            except Exception:
                pass
        if dry_run or not cfg["allow_write"]:
            info(self.describe_plan())
            if dry_run:
                return {"status": "dry_run", "log": logs, "errors": errors,
                        "ready": False}
            info("allow_write=False: Diagnose-Modus – nur lesende Schritte "
                 "(kein Broadcast/Stall/Clear/Write/Update, kein GPIO-Setzen)")
        # Strapping-Guard (wie motor_init.build_init_plan: falsche Pegel → Abbruch)
        for m, d, s, addr, exp, ok in self._strapping_rows():
            if not ok:
                err("Strapping %s: DIR=%d STEP=%d -> 0x%02X, erwartet 0x%02X" % (
                    m, d["phys"], s["phys"], addr, exp))
                return {"status": "strapping_error", "log": logs, "errors": errors,
                        "ready": False}
        for m in cfg["motor_closed_loop"]:
            if m != "e" and (not (cfg["pins"].get(m) or {}).get("dir")
                             or not (cfg["pins"].get(m) or {}).get("step")):
                err("Motor %s ohne Dir/Step-Pins – keine Strapping-Adresse" % m)
                return {"status": "config_error", "log": logs, "errors": errors,
                        "ready": False}
        if cfg["switch"] == 0:
            info("switch=0: nur Pins normal (physisch 0), danach Ende")
            if cfg["allow_write"]:
                for _m, _kind, spec in self._pin_list():
                    lvl = 1 if spec["invert"] else 0
                    try:
                        self.gpio.set_pin(spec["raw"], lvl)
                        self.pin_levels[spec["raw"]] = lvl
                    except Exception as exc:
                        err("GPIO-Setzen %s: %s" % (spec["raw"], exc))
                try:
                    self.gpio.sync()  # M400-Äquivalent (Review 2)
                except Exception:
                    pass
                self.pause(PAUSE_AFTER_RESTORE)
            info("switch=0 set_pin_io 0")
            self.is_ready = False
            return {"status": "switch0", "log": logs, "errors": errors, "ready": False}

        can_write = bool(cfg["allow_write"] and not dry_run)
        pin_list = self._pin_list()

        # P0
        info("P0 transparent-Kanal: Düsen-MCU %s (Extruder 0x81)" % cfg.get("nozzle_mcu"))

        # P1
        if can_write:
            info("P1 Strapping-Pegel setzen (%d Pins)" % len(pin_list))
            for m, kind, spec in pin_list:
                try:
                    self.gpio.set_pin(spec["raw"], spec["level"])
                    self.pin_levels[spec["raw"]] = spec["level"]
                except Exception as exc:
                    err("GPIO-Setzen %s=%d: %s" % (spec["raw"], spec["level"], exc))
                    return {"status": "gpio_error", "log": logs, "errors": errors,
                            "ready": False}
            try:
                self.gpio.sync()  # M400-Äquivalent (Review 2): erst dann lesen/senden
            except Exception:
                pass
            self.pause(PAUSE_AFTER_GPIO)
            back = self._query_all_pins(pin_list)
            bad = [(spec["raw"], spec["phys"], back.get(spec["raw"]))
                   for _m, _k, spec in pin_list
                   if back.get(spec["raw"]) is not None and back.get(spec["raw"]) != spec["phys"]]
            for attempt in range(GPIO_RESEND_TRIES):
                if not bad:
                    break
                info("P1 Resend %d/%d" % (attempt + 1, GPIO_RESEND_TRIES))
                for raw, _phys, _got in bad:
                    lvl = next(s["level"] for _m, _k, s in pin_list if s["raw"] == raw)
                    try:
                        self.gpio.set_pin(raw, lvl)
                    except Exception:
                        pass
                try:
                    self.gpio.sync()  # M400-Äquivalent (Review 2)
                except Exception:
                    pass
                self.pause(0.2)
                back = self._query_all_pins(pin_list)
                bad = [(s["raw"], s["phys"], back.get(s["raw"]))
                       for _m, _k, s in pin_list
                       if back.get(s["raw"]) is not None and back.get(s["raw"]) != s["phys"]]
            if bad:
                for raw, phys, got in bad:
                    err(ERR_KEY799 % (raw, phys, got, GPIO_RESEND_TRIES, GPIO_RESEND_TRIES))
                self._restore_pins(pin_list, can_write, info, err)
                self._maybe_shutdown(info, err, "key799")
                return {"status": "key799", "log": logs, "errors": errors, "ready": False}
            info("initial set_pin_io done")
        else:
            info("P1 (Diagnose): Pins werden NICHT gesetzt (allow_write=False); "
                 "Rücklesung (falls vorhanden):")
            back = self._query_all_pins(pin_list)
            for _m, _k, spec in pin_list:
                got = back.get(spec["raw"])
                info("  %s erwartet phys %d, gelesen %s" % (
                    spec["raw"], spec["phys"], "–" if got is None else got))

        # P2 Runden
        closed = list(cfg["motor_closed_loop"])
        ok_all = False
        for rnd in range(1, cfg["retries"] + 1):
            if can_write:
                for bus_name in ("ttyS5", "transparent"):
                    try:
                        if bus_name == "ttyS5":
                            self.rs485.transact(BROADCAST_ADDR, FUNC_ADDR, [SUB_ADDR_SET],
                                                timeout=TIMEOUT_ADDR, retries=0)
                        else:
                            self.transparent.transact(BROADCAST_ADDR, FUNC_ADDR, [SUB_ADDR_SET],
                                                      timeout=TIMEOUT_TRANSPARENT, retries=0)
                        info("P2 Runde %d: Broadcast (%s) unerwartet beantwortet" % (rnd, bus_name))
                    except (TimeoutError, ValueError):
                        pass  # erwartet: keine Antwort / leer
                self.pause(PAUSE_AFTER_BROADCAST)
            else:
                info("P2 Runde %d (Diagnose): kein Broadcast (allow_write=False)" % rnd)
            ok, need = set(), set(closed)
            for m in closed:
                self.pause(PAUSE_BEFORE_ADDR)
                try:
                    _st, _fn, data = self._transact(
                        m, FUNC_ADDR, [SUB_ADDR_READ],
                        TIMEOUT_TRANSPARENT if m == "e" else TIMEOUT_ADDR, 0)
                    if len(bytes(data)) == 1:
                        ok.add(m)
                        info("P2 Runde %d: %s antwortet 0x%02X" % (rnd, m, bytes(data)[0]))
                    else:
                        info("P2 Runde %d: %s unerwartete Länge %d" % (rnd, m, len(bytes(data))))
                except (TimeoutError, ValueError) as exc:
                    info("P2 Runde %d: %s Timeout (%s)" % (rnd, m, exc))
            info("broadcast retries:%d" % rnd)
            if ok == need:
                ok_all = True
                if rnd > 1 or can_write:
                    pass
                break
            self.pause(PAUSE_ROUND)
        if not ok_all:
            missing = sorted(set(closed) - ok)
            err(ERR_KEY798 % (cfg["retries"], ",".join(missing) or "?"))
            self._restore_pins(pin_list, can_write, info, err)
            self._maybe_shutdown(info, err, "key798")
            return {"status": "key798", "log": logs, "errors": errors, "ready": False}

        # P3
        info("All motors are normal")

        # P4 Stall-Mode (nur Z/Z1/E, wie Stock; X/Y erst beim Homing)
        if can_write:
            for m in ("z", "z1", "e"):
                if m not in closed:
                    continue
                done = False
                for _t in range(STALL_TRIES):
                    try:
                        _st, _fn, data = self._transact(
                            m, FUNC_SET_MODE, [STALL_MODE_EMERGENCY],
                            TIMEOUT_TRANSPARENT if m == "e" else TIMEOUT_STALL, 0)
                        if bytes(data) == bytes([STALL_MODE_EMERGENCY]):
                            done = True
                            break
                    except (TimeoutError, RuntimeError, ValueError):
                        pass
                    self.pause(PAUSE_STALL_RETRY)
                if not done:
                    if m == "e":
                        err(ERR_KEY802 % STALL_TRIES)
                    else:
                        err(ERR_KEY801 % (m, STALL_TRIES))
                    self._restore_pins(pin_list, can_write, info, err)
                    self._maybe_shutdown(info, err, "key801/802")
                    return {"status": "key801_802", "log": logs, "errors": errors,
                            "ready": False}
                info("stall mode change motor_%s: 0x11 [02] ok" % m)
        else:
            info("P4 (Diagnose): kein Stall-Mode (allow_write=False)")

        # P5 Schutzabfrage (lesend – auch in Diagnose)
        for m in closed:
            try:
                _st, _fn, data = self._transact(m, FUNC_PROTECTION, [SUB_PROT_CHECK],
                                                TIMEOUT_TRANSPARENT if m == "e" else TIMEOUT_PROTECTION, 0)
                try:
                    dec = decode_protection_local(bytes(data))
                    if dec["ok"]:
                        info("P5 %s 0C [0b]: ok (8x00)" % m)
                    else:
                        info("P5 %s 0C [0b]: err=%s warn=%s" % (m, dec["errors"], dec["warnings"]))
                except ValueError as exc:
                    info("P5 %s 0C [0b]: %d B (%s)" % (m, len(bytes(data)), exc))
            except (TimeoutError, ValueError) as exc:
                info("P5 %s 0C [0b]: Timeout (%s)" % (m, exc))

        # P6 Clear (schreibend – nur mit allow_write)
        if can_write:
            for m in closed:
                try:
                    self._transact(m, FUNC_PROTECTION, [SUB_PROT_CLEAR],
                                   TIMEOUT_TRANSPARENT if m == "e" else TIMEOUT_PROTECTION, 0)
                    info("P6 %s 0C [05]: beantwortet (unerwartet, ok)" % m)
                except (TimeoutError, ValueError):
                    info("P6 %s 0C [05]: keine Antwort (live ebenso, ok)" % m)
        else:
            info("P6 (Diagnose): kein Clear 0C [05] (allow_write=False)")

        # P7 Restore (auch im Fehlerfall; Setzen nur mit allow_write)
        self._restore_pins(pin_list, can_write, info, err)

        # P8 Param-Abgleich
        p8 = self._param_phase(closed, can_write, info, err)
        if p8.get("fatal"):
            self._maybe_shutdown(info, err, "param")
            return {"status": p8.get("status", "param_error"), "log": logs,
                    "errors": errors, "ready": False}

        # P9
        self.is_ready = True
        info("is_ready=True; Event k2_motor:ready (CFS-Auto-Addr erst jetzt)")
        try:
            if self.send_event is not None:
                self.send_event("k2_motor:ready")
        except Exception as exc:
            info("Event k2_motor:ready nicht sendbar: %s" % exc)
        return {"status": "ready", "log": logs, "errors": errors, "ready": True}

    def _restore_pins(self, pin_list, can_write, info, err):
        if can_write:
            for m, kind, spec in pin_list:
                lvl = 1 if spec["invert"] else 0
                try:
                    self.gpio.set_pin(spec["raw"], lvl)
                    self.pin_levels[spec["raw"]] = lvl
                except Exception as exc:
                    err("GPIO-Restore %s: %s" % (spec["raw"], exc))
            try:
                self.gpio.sync()  # M400-Äquivalent (Review 2)
            except Exception:
                pass
            self.pause(PAUSE_AFTER_RESTORE)
            back = self._query_all_pins(pin_list)
            for _m, _k, spec in pin_list:
                got = back.get(spec["raw"])
                if got is not None and got != 0:
                    err(ERR_KEY800 % (spec["raw"], got))
            info("restore set_pin_io done")
            self.pause(PAUSE_RESTORE_DONE)
        else:
            info("P7 (Diagnose): kein Restore-Setzen (allow_write=False)")

    def _maybe_shutdown(self, info, err, key):
        if self.cfg.get("shutdown_on_failure"):
            msg = "SHUTDOWN (shutdown_on_failure=True, nach %s)" % key
            info(msg)
            raise RuntimeError(msg)
        info("Kein Shutdown (shutdown_on_failure=False) – nur Fehlermeldung")

    def _param_phase(self, closed, can_write, info, err):
        reads = {}
        for m in VERIFY_ORDER:
            if m not in closed:
                continue
            if not (self.cfg["pins"].get(m) or {}).get("stall"):
                continue
            info("update_flash_param stepper:%d (%s)" % (MOTOR_NAME_TO_NUM[m], m))
            for p in self._table:
                pid = p["id"]
                if not p["configurable"]:
                    continue
                configured = p["name"] in self.cfg["params"].get(m, {})
                if pid not in ALWAYS_READ and not configured:
                    continue
                if pid in HOST_ONLY_IDS:
                    continue  # ID 226: Host-Parameter, nie senden
                vt = p["value_type"] or "float"
                cval = self.cfg["params"][m].get(p["name"])
                # Lesen (≤3)
                raw = None
                for _t in range(PARAM_READ_TRIES):
                    try:
                        _st, _fn, data = self._transact(
                            m, FUNC_SYS_PARAM, [SUB_SYS_READ, pid], TIMEOUT_SYS_PARAM, 0)
                        raw = bytes(data)
                        break
                    except (TimeoutError, ValueError):
                        continue
                if raw is None:
                    info("  %s ID %d %s: result is None (Timeout, weiter)" % (m, pid, p["name"]))
                    reads[(m, pid)] = None
                    continue
                if pid in ALWAYS_READ:
                    info("  %s ID %d %s: read success (%d B)" % (m, pid, p["name"], len(raw)))
                    reads[(m, pid)] = self._decode_param(raw, vt)
                    continue
                val = self._decode_param(raw, vt)
                reads[(m, pid)] = val
                if val is None or isinstance(val, bool) or not isinstance(val, (int, float)):
                    info("  %s ID %d %s: kein Vergleichswert (%r)" % (m, pid, p["name"], val))
                    continue
                if cval is None:
                    continue
                if _isclose(val, cval):
                    continue
                info("  %s ID %d %s is not close: gelesen %r, config %r" % (m, pid, p["name"], val, cval))
                if not can_write:
                    info("    (Diagnose: kein Write, allow_write=False)")
                    continue
                if vt != "float":
                    info("    Schreibformat für %s unbelegt – nicht geschrieben" % vt)
                    continue
                payload = [SUB_SYS_WRITE, pid] + list(_float_to_bytes(cval))
                done = None
                for _t in range(PARAM_WRITE_TRIES):
                    try:
                        _st, _fn, data = self._transact(
                            m, FUNC_SYS_PARAM, payload, TIMEOUT_SYS_PARAM, 0)
                        if bytes(data) == bytes(payload[2:]):
                            done = float(cval)
                            break
                        # Echo prüfen (Antwort = 4 Float-Bytes)
                        try:
                            echo = struct.unpack("<f", bytes(data))[0]
                            if _isclose(echo, float(cval)):
                                done = echo
                                break
                        except Exception:
                            pass
                    except (TimeoutError, RuntimeError, ValueError):
                        continue
                if done is not None:
                    info("    %s ID %d write success (Echo ok)" % (m, pid))
                else:
                    info("    %s ID %d write failed" % (m, pid))
            if can_write:
                try:
                    _st, _fn, data = self._transact(m, FUNC_SYS_PARAM, [SUB_SYS_UPDATE],
                                                    TIMEOUT_SYS_PARAM, 0)
                    info("  %s Updata param 06 [03] ok" % m)
                except (TimeoutError, RuntimeError, ValueError) as exc:
                    info("  %s Updata param 06 [03]: %s (Stock ignoriert Ergebnis)" % (m, exc))
            else:
                info("  %s Updata param 06 [03]: übersprungen (allow_write=False)" % m)
        return {}

    def _decode_param(self, raw, vt):
        raw = bytes(raw)
        if len(raw) == 0:
            return "leer"
        fmt = _TYPE_FORMATS.get(vt or "float", "<f")
        if struct.calcsize(fmt) != len(raw):
            return raw.hex()
        return struct.unpack(fmt, raw)[0]

    # --- Status (nur lesend) ------------------------------------------------
    def motor_status(self):
        """Je Motor: Version 0x0F, Schutz 0x0C [0b] dekodiert, Feedback 0x08 idx 5/6."""
        out = {}
        for m in self.cfg["motor_closed_loop"]:
            entry = {"motor": m}
            try:
                _st, _fn, data = self._transact(m, FUNC_VERSION, [SUB_VERSION_GET],
                                                TIMEOUT_VERSION, 0)
                try:
                    entry["version"] = decode_version_local(bytes(data))
                except ValueError as exc:
                    entry["version_error"] = str(exc)
            except Exception as exc:
                entry["version_error"] = "%s" % exc
            try:
                _st, _fn, data = self._transact(m, FUNC_PROTECTION, [SUB_PROT_CHECK],
                                                TIMEOUT_PROTECTION, 0)
                try:
                    entry["protection"] = decode_protection_local(bytes(data))
                except ValueError as exc:
                    entry["protection_error"] = str(exc)
            except Exception as exc:
                entry["protection_error"] = "%s" % exc
            for idx, key in ((5, "pos_fdb"), (6, "spd_fdb")):
                try:
                    _st, _fn, data = self._transact(m, FUNC_VARIABLE, [idx],
                                                    TIMEOUT_VARIABLE, 0)
                    raw = bytes(data)
                    if len(raw) != 4:
                        entry[key + "_error"] = "%d statt 4 Bytes" % len(raw)
                    else:
                        entry[key] = struct.unpack("<f", raw)[0]
                except Exception as exc:
                    entry[key + "_error"] = "%s" % exc
            out[m] = entry
        return out

    # --- Kalibrier-Sicherung/-Prüfung (NUR LESEND) ----------------------------
    def _cal_read(self, motor, fun, payload, what):
        """Ein lesender Frame, bis zu CAL_READ_TRIES Versuche -> bytes.
        Danach kurz an den Reactor abgeben (andere Busnutzer, Timer)."""
        timeout = TIMEOUT_TRANSPARENT if motor == "e" else TIMEOUT_CAL_READ
        last = None
        for _t in range(CAL_READ_TRIES):
            try:
                _st, _fn, data = self._transact(motor, fun, payload, timeout, 0)
                self.pause(0.0)
                return bytes(data)
            except (TimeoutError, ValueError) as exc:
                last = exc
                self.pause(0.0)           # nach jedem Timeout an den Reactor
            except (RuntimeError, OSError) as exc:  # Port weg/Batch: Abbruch
                raise K2CalBusError("%s: %s" % (what, exc))
        raise K2MotorError("%s: %s" % (what, last))

    def cal_snapshot(self, motor):
        """Liest alles für KTWO_MOTOR_CAL_BACKUP/_CHECK. NUR LESEND (0x0F,
        0x12 [02], 0x05 [04], 0x0C [0b], 0x07 [02]/[06,id], 0x06 [02,id], 0x08,
        0x13) – nichts geschrieben, keine Bewegung. -> dict; enthält die UUID
        (nur für die Datei, nie ausgeben). Lesefehler -> snap["fehler"];
        Port-/Busausfall -> K2MotorError."""
        if motor not in VERIFY_ORDER:
            raise K2MotorError("Motor %r unbekannt (x/y/z/z1/e)" % (motor,))
        _tr, addr = self._transport_for(motor)
        snap = {"format": CAL_FILE_FORMAT, "motor": motor, "adresse": "0x%02X" % addr,
                "weg": "Düsenkanal" if motor == "e" else "RS485",
                "hinweis": CAL_HINWEIS_RECORD, "fehler": []}

        folge = [0]   # Lesungen ohne Antwort in Folge

        def lies(what, fun, payload):
            try:
                raw = self._cal_read(motor, fun, list(payload), what)
            except K2CalBusError:
                raise
            except K2MotorError as exc:
                snap["fehler"].append(str(exc))
                folge[0] += 1
                if folge[0] >= CAL_MAX_FEHL_FOLGE:
                    raise K2CalBusError("%s (0x%02X): %d Lesungen in Folge ohne "
                                        "Antwort – abgebrochen" % (motor, addr, folge[0]))
                return None
            folge[0] = 0
            return raw

        try:
            raw = self._cal_read(motor, FUNC_VERSION, [SUB_VERSION_GET], "Version 0x0F")
        except K2CalBusError:
            raise
        except K2MotorError as exc:
            raise K2CalBusError("%s (0x%02X) antwortet nicht (%s) – nichts gesichert"
                                % (motor, addr, exc))
        if raw is not None:
            try:
                ver = decode_version_local(raw)
                ver["raw"] = raw.hex()
                snap["version"] = ver
            except ValueError as exc:
                snap["version"] = {"raw": raw.hex(), "dekodierfehler": str(exc)}
        raw = lies("UUID 0x12", FUNC_DEV_UUID, [SUB_UUID_READ])
        snap["uuid"] = None
        if raw is not None:
            if len(raw) == 8:
                snap["uuid"] = raw.hex()
            else:
                snap["fehler"].append("UUID 0x12: %d statt 8 Bytes" % len(raw))
        raw = lies("Regelung 0x05 [04]", FUNC_CONTROL, [SUB_CONTROL_STATUS])
        if raw is not None:
            snap["regelung"] = {"raw": raw.hex()}
            if len(raw) == 3:
                snap["regelung"].update(en_pin=raw[0], regler=raw[1], pwm=raw[2])
        raw = lies("Schutz 0x0C [0b]", FUNC_PROTECTION, [SUB_PROT_CHECK])
        if raw is not None:
            try:
                pr = decode_protection_local(raw)
                snap["schutz"] = {"raw": raw.hex(), "ok": bool(pr["ok"]),
                                  "fehler": [k for _b, k, _t in pr["errors"]],
                                  "warnungen": [k for _b, k, _t in pr["warnings"]]}
            except ValueError as exc:
                snap["schutz"] = {"raw": raw.hex(), "dekodierfehler": str(exc)}
        raw = lies("Flash-Prüfsumme 0x07 [02]", FUNC_FLASH_PARAM, [SUB_FLASH_CHECKSUM])
        if raw is not None:
            snap["flash_pruefsumme"] = {"raw": raw.hex()}
            if len(raw) == 5:
                ok, calc, flash = struct.unpack("<BHH", raw)
                snap["flash_pruefsumme"].update(ok=ok, berechnet=calc, flash=flash)
        snap["messwerte"] = {}
        for idx, name, fmt in CAL_VARIABLES:
            raw = lies("Messwert 0x08 idx %d" % idx, FUNC_VARIABLE, [idx])
            if raw is not None:
                snap["messwerte"][name] = {"idx": idx, "raw": raw.hex(),
                                           "wert": _cal_unpack(raw, fmt)}
        ids = {}
        for pid in CAL_FW_PARAM_IDS:
            e = {"name": cal_param_name(pid)}
            raw = lies("ID %d RAM 0x06 [02]" % pid, FUNC_SYS_PARAM, [SUB_SYS_READ, pid])
            e["ram"] = raw.hex() if raw is not None else None
            raw = lies("ID %d Flash 0x07 [06]" % pid, FUNC_FLASH_PARAM, [SUB_FLASH_READ, pid])
            e["flash"] = raw.hex() if raw is not None else None
            ids[str(pid)] = e
        snap["alle_ids"] = ids
        kal = {}
        for pid, name, fmt in CAL_PARAMS:
            e = ids[str(pid)]
            kal[str(pid)] = {"name": name, "ram_raw": e["ram"], "flash_raw": e["flash"],
                             "ram": _cal_unpack_hex(e["ram"], fmt),
                             "flash": _cal_unpack_hex(e["flash"], fmt)}
        snap["kalibrierung"] = kal
        if motor == "e":
            snap["fehlerdetail_0x13"] = "nicht über den Düsenkanal abgefragt (74 B)"
        else:
            raw = lies("Fehlerdetail 0x13", FUNC_ERR_DETAIL, ERR_DETAIL_REQUEST)
            if raw is not None:
                snap["fehlerdetail_0x13"] = cal_err_detail(raw, kal)
        return cal_auswerten(snap)

    def protect_en_lesen(self, motors=None):
        """ID 173 protect_en (RAM) je Motor -> {motor: int | Fehlertext}. NUR
        LESEND (0x06 [02, 173]). 0 = Motor-Schutzfunktionen AUS: die Encoder-
        Registerprüfung der Motor-FW nach dem Einschalten setzt protect_en aus
        einem SPI-Empfangsbyte (live 255); bricht der Transfer ab, bleibt 0
        (Motor-FW-Analyse 26.10.2026)."""
        out = {}
        for m in (motors if motors is not None else self.cfg["motor_closed_loop"]):
            try:
                raw = self._cal_read(m, FUNC_SYS_PARAM, [SUB_SYS_READ, CAL_PROTECT_EN_ID],
                                     "protect_en %s" % m)
                v = cal_id_wert(CAL_PROTECT_EN_ID, raw.hex())
                out[m] = v if isinstance(v, int) else "Antwort %s" % (raw.hex() or "leer")
            except K2MotorError as exc:
                out[m] = str(exc)
        self.protect_en = out
        return out

    # --- Kalibrierlauf / Rückschreiben (SCHREIBEND – nur über die Hülle) -------
    def cal_lauf_senden(self, motor, mode, runner=None):
        """Sendet GENAU EINEN Kalibrier-Frame (0x04 bzw. 0x03 [00], retries 0)
        über die Einmalfreigabe des Sendeschutzes und wartet bis T_max auf die
        Antwort. KEINE Vorbedingungsprüfung hier (macht die Hülle). runner(fn)
        führt den Busaufruf aus (Klipper: Hilfs-Thread für RS485, damit der
        Reactor weiterläuft). -> (dict Antwort, Dauer s). Keine/kaputte
        Antwort -> TimeoutError/ValueError (NICHT neu senden)."""
        fun, t_max, _n, _txt = CAL_MODES[mode]
        _tr, addr = self._transport_for(motor)
        runner = runner if runner is not None else (lambda fn: fn())
        start = self.clock()
        try:
            self.cal_aktiv = motor
            _K2T.tx_kalibrier_freigabe(addr, fun, CAL_RUN_PAYLOAD, CAL_FREIGABE_S)
            _st, _fn, data = runner(lambda: self._transact(
                motor, fun, list(CAL_RUN_PAYLOAD), t_max, 0))
        finally:
            _K2T.tx_kalibrier_freigabe_loeschen()
            self.cal_aktiv = None
        return cal_run_antwort(mode, data), float(self.clock()) - start

    def cal_werte_zurueckschreiben(self, motor, sicherung, runner=None):
        """RESTORE (Spezifikation §8.8): phase_order_invert, elec_offset,
        elec_offset_err_deg als Rohbytes der Sicherung per 0x06 [01] in den
        RAM, gegenlesen, dann 0x07 [03 00] (alle Parameter RAM -> Flash) über
        die Einmalfreigabe, Flash gegenlesen. -> Logzeilen. Abweichung ->
        K2MotorError. KEINE Vorbedingungsprüfung hier (macht die Hülle)."""
        runner = runner if runner is not None else (lambda fn: fn())
        kal = sicherung["kalibrierung"]
        log = []
        _tr, addr = self._transport_for(motor)
        fmt = dict((pid, f) for pid, _n, f in CAL_PARAMS)
        ram_ist = {}
        for pid in (10, 9, 25):
            raw = bytes.fromhex(kal[str(pid)]["flash_raw"])
            _st, _fn, echo = self._bus_call(motor, FUNC_SYS_PARAM, [SUB_SYS_WRITE, pid] + list(raw),
                                            TIMEOUT_TRANSPARENT if motor == "e" else TIMEOUT_SYS_PARAM,
                                            "RESTORE %s ID %d schreiben" % (motor, pid))
            if bytes(echo) != raw:
                raise K2MotorError("RESTORE %s ID %d: Echo %s statt %s – abgebrochen vor dem "
                                   "Flash-Schreiben" % (motor, pid, bytes(echo).hex(), raw.hex()))
            ist = self._cal_read(motor, FUNC_SYS_PARAM, [SUB_SYS_READ, pid],
                                 "RESTORE %s ID %d gegenlesen" % (motor, pid))
            # FW schreibt über Text (6 Nachkommastellen) -> Floats nur fast gleich
            if ist != raw and not _cal_fein_gleich(_cal_unpack(ist, fmt[pid]),
                                                   _cal_unpack(raw, fmt[pid])):
                raise K2MotorError("RESTORE %s ID %d: RAM %s statt %s – abgebrochen vor dem "
                                   "Flash-Schreiben" % (motor, pid, ist.hex(), raw.hex()))
            ram_ist[pid] = ist
            log.append("ID %d %s RAM = %s" % (pid, kal[str(pid)]["name"],
                                             _cal_zahl(kal[str(pid)]["flash"])))
        try:
            self.cal_aktiv = motor
            _K2T.tx_kalibrier_freigabe(addr, FUNC_FLASH_PARAM, FLASH_SAVE_PAYLOAD,
                                       CAL_FREIGABE_S)
            _st, _fn, data = runner(lambda: self._transact(
                motor, FUNC_FLASH_PARAM, list(FLASH_SAVE_PAYLOAD), TIMEOUT_FLASH_SAVE, 0))
        finally:
            _K2T.tx_kalibrier_freigabe_loeschen()
            self.cal_aktiv = None
        log.append("0x07 [03 00] Flash schreiben: Antwort %s (immer 01, kein Erfolgsbeweis)"
                   % bytes(data).hex())
        self.pause(0.2)
        for pid in (10, 9, 25):
            ist = self._cal_read(motor, FUNC_FLASH_PARAM, [SUB_FLASH_READ, pid],
                                 "RESTORE %s ID %d Flash gegenlesen" % (motor, pid))
            if ist != ram_ist[pid]:                # Flash = RAM nach dem Schreiben
                raise K2MotorError("RESTORE %s ID %d: Flash %s statt RAM %s – Flash-"
                                   "Schreiben gescheitert" % (motor, pid, ist.hex(),
                                                              ram_ist[pid].hex()))
        log.append("Flash gegengelesen: ID 10/9/25 = RAM (Werte der Sicherung)")
        try:
            pr = self._cal_read(motor, FUNC_FLASH_PARAM, [SUB_FLASH_CHECKSUM],
                                "RESTORE %s Flash-Prüfsumme" % motor)
            if len(pr) == 5:
                ok, calc, fl = struct.unpack("<BHH", pr)
                log.append("Flash-Prüfsumme 0x07 [02]: berechnet 0x%04X, gespeichert 0x%04X%s"
                           % (calc, fl, "" if calc == fl else " – ABWEICHEND"))
        except K2MotorError as exc:
            log.append("Flash-Prüfsumme nicht lesbar: %s" % exc)
        return log

    # --- Sensorless-Homing X/Y (Stock sensorless.cfg, Belege s. Konstanten) --
    # Alle schreibenden Methoden prüfen allow_write ZUERST (K2MotorError ohne
    # Bus-Zugriff). Bus-/Port-Fehler (RuntimeError aus [k2_485], TimeoutError,
    # ValueError bei Absender/fun-Mismatch, OSError) werden als K2MotorError
    # gemeldet – die Klipper-Hülle macht daraus G-Code-Fehler (kein Shutdown).

    def _require_write(self, what):
        if not self.cfg.get("allow_write"):
            raise K2MotorError(
                "%s gesperrt: [k2_motor] allow_write=False – nichts gesendet "
                "(erst nach KTWO_MOTOR_INIT-Prüfung allow_write: True setzen)" % what)

    def _bus_call(self, motor, fun, payload, timeout, what):
        """Ein Frame, retries=0 wie Stock; Fehler -> K2MotorError."""
        try:
            return self._transact(motor, fun, payload, timeout, 0)
        except K2MotorError:
            raise
        except (RuntimeError, TimeoutError, ValueError, OSError) as exc:
            raise K2MotorError("%s: %s" % (what, exc))

    def set_stall_mode(self, motors, mode):
        """0x11 [mode] an ``motors`` (Reihenfolge wie übergeben, Stock x→y).

        Nur Modus 1 (Endschalter, vor dem Homing) und 2 (Notfall-Schutz,
        danach); nur x/y (Z/Z1 setzt der Init in P4). Antwort = Echo [mode]
        (live ``f7 81 04 00 11 01 1d``), sonst K2MotorError. Bricht beim
        ersten Fehler ab; ``self.stall_mode`` zeigt, was bestätigt ist.
        Rückgabe: Logzeilen (Stock-Wortlaut "stall mode change motor81")."""
        if isinstance(mode, bool) or mode not in STALL_MODES_SETTABLE:
            raise K2MotorError("Stall-Mode muss 1 (Endschalter) oder 2 (Notfall-"
                               "Schutz) sein: %r" % (mode,))
        motors = list(motors)
        for m in motors:
            if m not in STALL_SET_MOTORS:
                raise K2MotorError("Stall-Mode setzen nur für x/y (Stock); %r "
                                   "abgelehnt" % (m,))
        self._require_write("Stall-Mode setzen (0x11)")
        out = []
        for m in motors:
            _tr, addr = self._transport_for(m)
            if mode != STALL_MODE_EMERGENCY:
                self._stall_guard_set(m)  # VOR dem Frame (Stock is_homing=True)
            try:
                _st, _fn, data = self._bus_call(
                    m, FUNC_SET_MODE, [mode], TIMEOUT_STALL,
                    "Stall-Mode %s (0x%02X) 0x11 [%02x]" % (m, addr, mode))
            except K2MotorError:
                self.stall_mode[m] = None  # Frame evtl. angekommen -> unbekannt
                raise
            if bytes(data) != bytes([mode]):
                self.stall_mode[m] = None
                raise K2MotorError("Stall-Mode %s (0x%02X): Echo %s statt %02x"
                                   % (m, addr, bytes(data).hex() or "leer", mode))
            self.stall_mode[m] = mode
            if mode == STALL_MODE_EMERGENCY:
                self._stall_guard_release(m)
            out.append("stall mode change motor%02x: send:%02x recv:%02x"
                       % (addr, mode, mode))
        return out

    def _stall_guard_set(self, motor):
        if motor in self.stall_guard:
            self.stall_guard[motor] = True

    def _stall_guard_release(self, motor):
        """Mode 2 bestätigt: Sperre fällt nach STALL_GUARD_AFTER (Stock:
        reactor.pause(+0,05 s), dann is_homing=False, MC@0xe1a44)."""
        if motor in self.stall_guard:
            self.stall_guard[motor] = False
            self.stall_guard_until[motor] = float(self.clock()) + STALL_GUARD_AFTER

    def stall_guarded(self, motor, eventtime):
        """True, wenn eine Flanke von ``motor`` zur Zeit ``eventtime`` in die
        X/Y-Sperre fällt (Homing, Mode 1/3, Nachlauf 0,05 s)."""
        if motor not in self.stall_guard:
            return False
        return (self.homing_active or self.stall_guard[motor]
                or float(eventtime) < self.stall_guard_until[motor])

    def clear_protection(self, motors):
        """0C [05] an ``motors`` (Stock MOTOR_CLEAR_ERR_WARN_CODE NUM=n DATA=5;
        NUM=0 = alle inkl. E transparent). SCHREIBEND -> allow_write. Live nie
        beantwortet -> Timeout = ok. Danach 0C [0b] zur Kontrolle (lesend; gibt
        die Notfall-Leitung frei). -> (log, errors, verify{motor: dict|str})"""
        self._require_write("Fehlercodes löschen (0C [05])")
        log, errors = [], []
        for m in motors:
            _tr, addr = self._transport_for(m)
            try:
                self._transact(m, FUNC_PROTECTION, [SUB_PROT_CLEAR],
                               TIMEOUT_TRANSPARENT if m == "e" else TIMEOUT_PROTECTION, 0)
                log.append("%s (0x%02X) 0C [05]: beantwortet (unerwartet, ok)" % (m, addr))
            except TimeoutError:
                log.append("%s (0x%02X) 0C [05]: gesendet, keine Antwort (live ebenso)"
                           % (m, addr))
            except (RuntimeError, ValueError, OSError) as exc:
                errors.append("%s 0C [05]: %s" % (m, exc))
        return log, errors, self.check_protection(motors)

    def get_stall_mode(self, motor):
        """0x11 [04] -> int (Antwort [mode, 00], Live-Log 2026-10-12). LESEND."""
        if motor not in STALL_READ_MOTORS:
            raise K2MotorError("Stall-Mode lesen nur für x/y/z/z1: %r" % (motor,))
        _tr, addr = self._transport_for(motor)
        _st, _fn, data = self._bus_call(motor, FUNC_SET_MODE, [SUB_STALL_GET],
                                        TIMEOUT_STALL,
                                        "Stall-Mode lesen %s (0x%02X)" % (motor, addr))
        raw = bytes(data)
        if len(raw) != 2:
            raise K2MotorError("Stall-Mode lesen %s: %d statt 2 Bytes (%s)"
                               % (motor, len(raw), raw.hex()))
        self.stall_mode[motor] = raw[0]
        return raw[0]

    def read_sys_param(self, motor, pid):
        """0x06 [02, id] -> Wert (float bzw. Typ laut Tabelle). LESEND."""
        if motor not in SYS_PARAM_MOTORS:
            raise K2MotorError("Sys-Param nur für x/y/z/z1: %r" % (motor,))
        if isinstance(pid, bool) or not isinstance(pid, int) or not 0 <= pid < len(self._table):
            raise K2MotorError("Param-ID muss 0..%d sein: %r" % (len(self._table) - 1, pid))
        if pid in HOST_ONLY_IDS:
            raise K2MotorError("ID %d ist ein reiner Host-Parameter (Motor antwortet 0 B)" % pid)
        p = self._table[pid]
        _st, _fn, data = self._bus_call(motor, FUNC_SYS_PARAM, [SUB_SYS_READ, pid],
                                        TIMEOUT_SYS_PARAM,
                                        "Sys-Param lesen %s ID %d" % (motor, pid))
        return self._decode_param(bytes(data), p["value_type"] or "float")

    def write_sys_param(self, motor, pid, value):
        """0x06 [01, id] + <f – FLÜCHTIG (bis Kaltstart), KEIN 06 [03] (wie
        Stock beim Homing). Nur IDs aus SYS_PARAM_WRITABLE_IDS, Echo = die 4
        Float-Bytes (live ``f7 81 07 00 06 00 00 96 43 09``). -> float32-Wert."""
        if motor not in SYS_PARAM_MOTORS:
            raise K2MotorError("Sys-Param schreiben nur für x/y/z/z1: %r" % (motor,))
        if isinstance(pid, bool) or pid not in SYS_PARAM_WRITABLE_IDS:
            raise K2MotorError("ID %r nicht freigegeben (schreibbar nur %s)"
                               % (pid, ",".join(str(i) for i in SYS_PARAM_WRITABLE_IDS)))
        try:
            raw = _float_to_bytes(value)
        except ValueError as exc:
            raise K2MotorError("Sys-Param ID %d: %s" % (pid, exc))
        f32 = struct.unpack("<f", raw)[0]
        if not math.isfinite(f32) or (value != 0 and f32 == 0.0):
            raise K2MotorError("Sys-Param ID %d: %r nicht als float32 darstellbar" % (pid, value))
        self._require_write("Sys-Param schreiben (0x06 [01])")
        _st, _fn, data = self._bus_call(motor, FUNC_SYS_PARAM,
                                        [SUB_SYS_WRITE, pid] + list(raw),
                                        TIMEOUT_SYS_PARAM,
                                        "Sys-Param schreiben %s ID %d" % (motor, pid))
        if bytes(data) != raw:
            raise K2MotorError("Sys-Param %s ID %d: Echo %s statt %s"
                               % (motor, pid, bytes(data).hex() or "leer", raw.hex()))
        return f32

    def check_protection(self, motors=None):
        """0C [0b] an alle (Stock MOTOR_CHECK_PROTECTION_AFTER_HOME DATA=11,
        E transparent). -> {motor: dict|str}; dict = decode_protection_local,
        str = Fehlertext (Timeout/Port). Seiteneffekt laut Firmware-Analyse: bei
        Stall-Mode 2 quittiert [0b] die Notfall-Leitung (Codes bleiben) – so
        auch Stock direkt nach DATA=2."""
        out = {}
        for m in (motors if motors is not None else self.cfg["motor_closed_loop"]):
            try:
                _st, _fn, data = self._transact(
                    m, FUNC_PROTECTION, [SUB_PROT_CHECK],
                    TIMEOUT_TRANSPARENT if m == "e" else TIMEOUT_PROTECTION, 0)
                out[m] = decode_protection_local(bytes(data))
            except (RuntimeError, TimeoutError, ValueError, OSError) as exc:
                out[m] = "%s" % exc
        return out

    def _restore_kp_value(self):
        return float(self.cfg["params"].get("x", {}).get(
            "controller_pos_loop_pid_param_kp", HOMING_POS_KP_RESTORE_DEFAULT))

    def homing_begin(self):
        """Stock-Folge VOR dem X/Y-Homing: 0x11 [01] an x, y; dann ID 70 an x
        = homing_pos_kp (300). Fehler -> Rückbau (best effort: Stall-Mode 2,
        Kp zurück) und K2MotorError, damit das Homing OHNE Bewegung abbricht."""
        self._require_write("Homing-Vorbereitung (Stall-Mode 1 + Kp)")
        log = []
        self.homing_active = False
        kp = float(self.cfg.get("homing_pos_kp", HOMING_POS_KP_DEFAULT))
        stage = "stall"
        try:
            log.extend(self.set_stall_mode(STALL_SET_MOTORS, STALL_MODE_ENDSTOP))
            stage = "kp"
            for m in HOMING_PARAM_MOTORS:
                v = self.write_sys_param(m, HOMING_PARAM_ID, kp)
                log.append("sys param %s ID %d (pos_kp) = %g (Echo ok)"
                           % (m, HOMING_PARAM_ID, v))
        except K2MotorError as exc:
            rb = self._rollback(stage)
            self.last_error = "Homing-Vorbereitung: %s" % exc
            raise K2MotorError("Homing-Vorbereitung fehlgeschlagen: %s | Rückbau: %s"
                               " – keine Bewegung" % (exc, "; ".join(rb) or "nichts nötig"))
        self.homing_active = True
        self.last_error = None
        return log

    def _rollback(self, stage):
        """Best effort nach Fehler in homing_begin (Fehler nur gemeldet)."""
        out = []
        for m in STALL_SET_MOTORS:  # beide, auch den fehlgeschlagenen (Zustand unbekannt)
            try:
                self.set_stall_mode([m], STALL_MODE_EMERGENCY)
                out.append("%s Stall-Mode 2 ok" % m)
            except K2MotorError as exc:
                out.append("%s Stall-Mode 2: %s" % (m, exc))
        if stage == "kp":
            for m in HOMING_PARAM_MOTORS:
                try:
                    self.write_sys_param(m, HOMING_PARAM_ID, self._restore_kp_value())
                    out.append("%s Kp zurück ok" % m)
                except K2MotorError as exc:
                    out.append("%s Kp zurück: %s" % (m, exc))
        return out

    def homing_end(self, protection=True):
        """Stock-Folge NACH dem Homing: 0x11 [02] an x, y; 0C [0b] an alle
        (inkl. E transparent); ID 70 an x = Config-Kp (100). Läuft IMMER
        komplett durch (best effort), Rückgabe (log, errors). Schutzbits
        gesetzt oder Stall/Kp fehlgeschlagen -> errors; Timeout der
        Schutzabfrage nur als Hinweis im Log (lesend, Stock wertet es nicht)."""
        self._require_write("Homing-Abschluss (Stall-Mode 2 + Kp)")
        self.homing_active = False
        log, errors = [], []
        for m in STALL_SET_MOTORS:
            try:
                log.extend(self.set_stall_mode([m], STALL_MODE_EMERGENCY))
            except K2MotorError as exc:
                errors.append(str(exc))
        if protection:
            for m, res in self.check_protection().items():
                if isinstance(res, dict):
                    if res["ok"]:
                        log.append("Schutz %s 0C [0b]: ok (8x00)" % m)
                    else:
                        errors.append("Schutz %s: err=%s warn=%s" % (
                            m, [k for _b, k, _t in res["errors"]],
                            [k for _b, k, _t in res["warnings"]]))
                else:
                    log.append("Schutz %s 0C [0b]: keine Antwort (%s)" % (m, res))
        kp = self._restore_kp_value()
        for m in HOMING_PARAM_MOTORS:
            try:
                v = self.write_sys_param(m, HOMING_PARAM_ID, kp)
                log.append("sys param %s ID %d (pos_kp) = %g (Echo ok)"
                           % (m, HOMING_PARAM_ID, v))
            except K2MotorError as exc:
                errors.append(str(exc))
        self.last_error = "; ".join(errors) if errors else None
        return log, errors

    # --- Schnittpositions-Kalibrierung (Stock MOTOR_CHECK_CUT_POS) --------
    def _set_cut_mode(self, mode):
        """0x11 [mode] NUR an x – intern für die Cut-Kalibrierung (Mode 3 ist
        über KTWO_MOTOR_STALL_MODE bewusst nicht erreichbar). Echo-Prüfung
        wie set_stall_mode (live ``f7 81 04 00 11 03 13``)."""
        self._require_write("Stall-Mode %d (Cut-Kalibrierung)" % mode)
        m = CUT_CAL_MOTOR
        _tr, addr = self._transport_for(m)
        if mode != STALL_MODE_EMERGENCY:
            self._stall_guard_set(m)
        try:
            _st, _fn, data = self._bus_call(
                m, FUNC_SET_MODE, [mode], TIMEOUT_STALL,
                "Stall-Mode %s (0x%02X) 0x11 [%02x]" % (m, addr, mode))
        except K2MotorError:
            self.stall_mode[m] = None
            raise
        if bytes(data) != bytes([mode]):
            self.stall_mode[m] = None
            raise K2MotorError("Stall-Mode %s (0x%02X): Echo %s statt %02x"
                               % (m, addr, bytes(data).hex() or "leer", mode))
        self.stall_mode[m] = mode
        if mode == STALL_MODE_EMERGENCY:
            self._stall_guard_release(m)
        return "set_stall_mode motor_%02x: send:%02x recv:%02x" % (addr, mode, mode)

    def cut_calibrate(self, motion, p, save):
        """Stock-Folge CALIBRATE_CUT_POS/MOTOR_CHECK_CUT_POS (Belege s. CUT_CAL_*).

        ``motion``: Adapter (KlippyCutMotion bzw. Test-Fake) mit homed_axes(),
        x_range(), get_position(), get_limits(), set_limits(v, a),
        wait_moves(), move(x, y, speed), probe_x(x, speed) -> dict
        (triggered, late, trig_x, no_movement), stall_pin_active().
        ``save(wert)``: merkt cut_pos_x vor (Hülle: configfile.set).

        Ablauf: Sperren -> Beschleunigung begrenzen -> Kp (ID 70) = pos_kp ->
        Park (X park_x, Y y) -> 0x11 [04] lesen -> 0x11 [03] -> Stall-Pin
        muss frei sein -> Anstöße start_x, start_x-step, … bis x_min (je
        Endstop-überwachte Fahrt + Nachlauf-Prüfung, danach zurück auf Park);
        Treffer zählen erst ab x <= x_max; Ergebnis = 2. Treffer + offset, muss
        in [x_min, x_max] liegen -> Ausgang X exit_x -> save() -> Rückbau.
        Rückbau IMMER (auch bei Fehler): Kopf auf Park (nur bei Fehler),
        0x11 [02], Kp = Config-Wert (100), Beschleunigung zurück. Fehler ->
        K2MotorError (Hülle: G-Code-Fehler, kein Shutdown)."""
        self._require_write("Cut-Kalibrierung (Stall-Mode 3 + Kp)")
        if self.homing_active:
            raise K2MotorError("Cut-Kalibrierung gesperrt: X/Y-Homing aktiv "
                               "(KTWO_MOTOR_HOMING PHASE=END fehlt) – nichts gesendet")
        if self.cut_cal_active:
            raise K2MotorError("Cut-Kalibrierung läuft bereits")
        validate_cut_cal_params(p)
        homed = str(motion.homed_axes()).lower()
        if "x" not in homed or "y" not in homed:
            raise K2MotorError("Cut-Kalibrierung gesperrt: X/Y nicht referenziert "
                               "(homed_axes=%r) – nichts gesendet, keine Bewegung" % homed)
        rmin, rmax = motion.x_range()
        if p["x_min"] < rmin - CUT_CAL_EPS or p["exit_x"] > rmax + CUT_CAL_EPS:
            raise K2MotorError("Cut-Kalibrierung: X %.2f..%.2f außerhalb [stepper_x] "
                               "%.2f..%.2f – nichts gesendet" % (
                                   p["x_min"], p["exit_x"], rmin, rmax))
        xs = cut_cal_positions(p)
        restore_kp = self._restore_kp_value()
        log = []
        res = {"result": None, "first_hit": None, "second_hit": None,
               "trig_x": [], "ignored": [], "probes": 0, "orig_mode": None,
               "saved": False, "error": None}
        state = {"limits": None, "kp": False, "mode": False}
        error = None
        self.cut_cal_active = True
        try:
            motion.wait_moves()
            lim = motion.get_limits()
            state["limits"] = (float(lim[0]), float(lim[1]))
            accel = min(float(p["accel"]), state["limits"][1])
            motion.set_limits(None, accel)
            log.append("Beschleunigung %g mm/s^2 (vorher %g)" % (accel, state["limits"][1]))
            state["kp"] = True
            v = self.write_sys_param(CUT_CAL_MOTOR, HOMING_PARAM_ID, p["pos_kp"])
            log.append("sys param x ID %d (pos_kp) = %g (Echo ok)" % (HOMING_PARAM_ID, v))
            motion.move(x=p["park_x"], y=p["y"], speed=p["park_speed"])
            motion.wait_moves()
            orig = self.get_stall_mode(CUT_CAL_MOTOR)
            res["orig_mode"] = orig
            log.append("get_stall_mode motor_x: %02x" % orig)
            state["mode"] = True
            log.append(self._set_cut_mode(CUT_CAL_MODE))
            if motion.stall_pin_active():
                raise K2MotorError("Stall-Pin X (PB11) schon VOR der ersten Fahrt aktiv "
                                   "(Mode 3) – Polarität/Verdrahtung prüfen, keine Anstöße")
            first = second = None
            for x in xs:
                r = motion.probe_x(x, p["probe_speed"])
                res["probes"] += 1
                if r.get("no_movement"):
                    raise K2MotorError("Stall-Pin X bei X=%.2f schon vor der Bewegung "
                                       "aktiv – Abbruch" % x)
                hit = bool(r.get("triggered") or r.get("late"))
                motion.move(x=p["park_x"], speed=p["park_speed"])
                if not hit:
                    continue
                how = "Endstop" if r.get("triggered") else "Nachlauf"
                if r.get("trig_x") is not None:
                    res["trig_x"].append(round(float(r["trig_x"]), 4))
                if x > p["x_max"] + CUT_CAL_EPS:
                    res["ignored"].append(x)
                    log.append("Stall bei X=%.2f (%s) über x_max %.2f – ignoriert"
                               % (x, how, p["x_max"]))
                    continue
                if first is None:
                    first = x
                    log.append("1. Treffer X=%.2f (%s)" % (x, how))
                else:
                    second = x
                    log.append("2. Treffer X=%.2f (%s)" % (x, how))
                    break
            res["first_hit"], res["second_hit"] = first, second
            if second is None:
                raise K2MotorError(
                    "%s bis X=%.2f (%d Anstöße) – Abbruch nach max. Weg, kein Ergebnis" % (
                        "kein Stall" if first is None else
                        "nur ein Stall (X=%.2f)" % first, xs[-1], res["probes"]))
            result = round(second + p["offset"], 2)
            log.append("cut_pos: %.2f+%.2f=%.2f" % (second, p["offset"], result))
            if not p["x_min"] - CUT_CAL_EPS <= result <= p["x_max"] + CUT_CAL_EPS:
                raise K2MotorError("Ergebnis cut_pos_x %.2f außerhalb check_cut_pos_x "
                                   "%.2f..%.2f – nicht gespeichert" % (
                                       result, p["x_min"], p["x_max"]))
            res["result"] = result
            motion.move(x=p["exit_x"], y=p["y"], speed=p["park_speed"])
            motion.wait_moves()
            save(result)
            res["saved"] = True
            log.append("save_config: set [%s] %s = %s (SAVE_CONFIG ausstehend)" % (
                CUT_CAL_SECTION, CUT_CAL_OPTION, CUT_CAL_FORMAT % result))
        except K2MotorError as exc:
            error = str(exc)
        except Exception as exc:  # Bewegungs-/Bus-Fehler der Adapter
            error = "%s: %s" % (type(exc).__name__, exc)
        # --- Rückbau: IMMER ---------------------------------------------
        rb_err, rb_log = [], []
        if error is not None:
            try:  # Kopf weg vom Schneidhebel
                pos = motion.get_position()
                if pos[0] < p["park_x"] - 0.5:
                    motion.move(x=p["park_x"], speed=p["park_speed"])
                    motion.wait_moves()
                    rb_log.append("Kopf auf Park X%.1f" % p["park_x"])
            except Exception as exc:
                rb_err.append("Kopf auf Park: %s" % exc)
        if state["mode"]:
            try:
                line = self._set_cut_mode(STALL_MODE_EMERGENCY)
                log.append(line)
                rb_log.append("Stall-Mode 2 ok")
            except Exception as exc:
                rb_err.append("Stall-Mode 2: %s" % exc)
        if state["kp"]:
            try:
                v = self.write_sys_param(CUT_CAL_MOTOR, HOMING_PARAM_ID, restore_kp)
                log.append("sys param x ID %d (pos_kp) = %g (Echo ok)" % (HOMING_PARAM_ID, v))
                rb_log.append("Kp %g ok" % v)
            except Exception as exc:
                rb_err.append("Kp zurück: %s" % exc)
        if state["limits"] is not None:
            try:
                motion.set_limits(state["limits"][0], state["limits"][1])
                rb_log.append("Beschleunigung %g" % state["limits"][1])
            except Exception as exc:
                rb_err.append("Beschleunigung zurück: %s" % exc)
        self.cut_cal_active = False
        res["log"] = log
        res["error"] = error or ("; ".join(rb_err) if rb_err else None)
        self.cut_cal_last = dict(res)
        for line in log:
            logging.info("k2_motor cut_cal: %s", line)
        if error is not None:
            self.last_error = "Cut-Kalibrierung: %s" % error
            raise K2MotorError("Cut-Kalibrierung fehlgeschlagen: %s | Rückbau: %s%s – "
                               "nichts gespeichert" % (
                                   error, "; ".join(rb_log) or "nichts nötig",
                                   (" | Rückbau-FEHLER: " + "; ".join(rb_err)) if rb_err else ""))
        if rb_err:
            self.last_error = "Cut-Kalibrierung Rückbau: %s" % "; ".join(rb_err)
            raise K2MotorError("Cut-Kalibrierung: cut_pos_x=%s vorgemerkt, aber Rückbau "
                               "fehlerhaft: %s" % (CUT_CAL_FORMAT % res["result"],
                                                   "; ".join(rb_err)))
        return res


class K2MotorError(RuntimeError):
    """Fehler der Homing-/Stall-/Param-Befehle (-> G-Code-Fehler, kein Shutdown)."""


class K2CalBusError(K2MotorError):
    """Port/Bus weg während KTWO_MOTOR_CAL_* – Lesefolge wird abgebrochen."""


# =====================================================================
# Klipper-Hülle [k2_motor] (ANNAHMEN zur Upstream-API)
# =====================================================================

class _TriggerRecorder(object):
    """Hülle um den X-Endstop: merkt sich das Ergebnis von home_wait()
    (> 0 = ausgelöst), sonst reine Weiterleitung (HomingMove ruft
    get_steppers/home_start/home_wait)."""

    def __init__(self, mcu_endstop):
        self._es = mcu_endstop
        self.trigger_time = None

    def __getattr__(self, name):
        return getattr(self._es, name)

    def home_wait(self, home_end_time):
        t = self._es.home_wait(home_end_time)
        self.trigger_time = t
        return t


class KlippyCutMotion(object):
    """Bewegungs-Adapter der Cut-Kalibrierung für Upstream-Klipper.

    Stall-Erkennung: Stock latcht das Stall-Pin-Ereignis von Motor x (PB11,
    ``motor_x_stall``) host-seitig ("motor_x 81 check_protection_code").
    Hier: dieselbe Leitung über den vorhandenen X-Endstop (``[stepper_x]
    endstop_pin: PB11``) – jeder Anstoß ist eine Upstream-``HomingMove``
    mit ``probe_pos=True``/``check_triggered=False``: die MCU tastet PB11 ab
    (trsync) und stoppt X/Y sofort beim Auslösen; danach kurzer Nachlauf
    (``settle``) mit Pegel-Abfrage (QUERY_ENDSTOPS-Weg), falls der Stall erst
    nach dem letzten Schritt kommt. Kein neues MCU-Objekt, keine Config-
    Änderung. Fehler -> K2MotorError."""

    def __init__(self, printer, settle=0.0, homing_module=None):
        self.printer = printer
        self.toolhead = printer.lookup_object("toolhead")
        self.settle = float(settle)
        self._homing = homing_module

    def _call(self, what, fn, *a, **kw):
        try:
            return fn(*a, **kw)
        except K2MotorError:
            raise
        except Exception as exc:
            raise K2MotorError("%s: %s" % (what, exc))

    def _homing_mod(self):
        if self._homing is None:
            try:
                from . import homing as _h  # klippy/extras/homing.py
            except ImportError:  # pragma: no cover
                import homing as _h
            self._homing = _h
        return self._homing

    def _rail_x(self):
        kin = self.toolhead.get_kinematics()
        rails = getattr(kin, "rails", None)
        if not rails:
            raise K2MotorError("Kinematik ohne rails – [stepper_x] nicht gefunden")
        return rails[0]

    def _x_endstop(self):
        es = self._rail_x().get_endstops()
        if not es:
            raise K2MotorError("[stepper_x] ohne Endstop (PB11) – keine Stall-Erkennung")
        return es[0]

    def homed_axes(self):
        st = self.toolhead.get_status(self.printer.get_reactor().monotonic())
        return str(st.get("homed_axes", ""))

    def x_range(self):
        lo, hi = self._rail_x().get_range()
        return float(lo), float(hi)

    def get_position(self):
        return list(self.toolhead.get_position())

    def get_limits(self):
        v, a = self.toolhead.get_max_velocity()
        return float(v), float(a)

    def set_limits(self, velocity, accel):
        self._call("Beschleunigung setzen", self.toolhead.set_max_velocities,
                   velocity, accel, None, None)

    def wait_moves(self):
        self._call("M400", self.toolhead.wait_moves)

    def move(self, x=None, y=None, speed=50.0):
        self._call("Fahrt X%s Y%s" % (x, y), self.toolhead.manual_move,
                   [x, y, None], float(speed))

    def stall_pin_active(self):
        mcu_es, _name = self._x_endstop()
        pt = self.toolhead.get_last_move_time()
        return bool(self._call("Stall-Pin abfragen", mcu_es.query_endstop, pt))

    def probe_x(self, x, speed):
        h = self._homing_mod()
        mcu_es, name = self._x_endstop()
        rec = _TriggerRecorder(mcu_es)
        hmove = h.HomingMove(self.printer, [(rec, name)], self.toolhead)
        pos = list(self.toolhead.get_position())
        pos[0] = float(x)
        trig = self._call("Anstoß X%.2f" % x, hmove.homing_move, pos, float(speed),
                          probe_pos=True, triggered=True, check_triggered=False)
        triggered = bool(rec.trigger_time is not None and rec.trigger_time > 0.)
        no_move = bool(triggered and hmove.check_no_movement() is not None)
        late = False
        if not triggered and self.settle > 0.:
            self._call("Nachlauf", self.toolhead.dwell, self.settle)
            late = self.stall_pin_active()
        return {"triggered": triggered, "late": late, "no_movement": no_move,
                "trig_x": float(trig[0]) if triggered else None}


class K2StallMonitor(object):
    """Überwachung der Notfall-/Stall-Leitungen im Betrieb (Ersatz für Stock
    ``button_stall_pin_init`` + ``_button_handler_<m>`` +
    ``check_protection_code``; 18.10.2026).

    Ohne Klipper testbar: alle Wirkungen laufen über Hooks.
    - ``get_ctl()`` -> K2MotorController oder None
    - ``schedule(fn)``: fn() später im Reactor (Klipper: register_callback)
    - ``respond(msg)``, ``run_script(script)``, ``shutdown(msg)``
    - ``print_state()`` -> print_stats.state, ``is_paused()`` -> bool

    Filter in ``on_edge`` (synchron, ohne Bus): nur steigende Flanke; nur
    nach ``is_ready``; X/Y/Z/Z1 während Cut-Kalibrierung nur ``cut_state``
    (Stock Z.1378); X/Y nicht während Homing/Mode 1/3 und 0,05 s danach
    (Stock is_homing); Reentrancy: höchstens EINE Auswertung gleichzeitig,
    weitere Motoren warten in ``pending`` (Stock: motor_cancel_print_start).

    Auswertung (``_handle``): 0C [0b] lesen (gibt in Mode 2 die Leitung frei),
    key781–797 melden. Modus ``report``: nur melden. Modus ``act``:
    - X/Y/Z/Z1 mit Fehlerbits: im Druck ``shutdown`` (Not-Stopp wie Stock-
      Abbruch, Meldung mit key-Codes), sonst ``M84`` + Meldung
    - X/Y/Z/Z1 nur Warnbits: im Druck ``PAUSE``, sonst nur Meldung
    - E (Fehler oder Warnung): im Druck ``PAUSE`` + ``M400``, immer
      Extruder aus (Stock extruder_motor_err_clear)
    - Codes 0 oder nicht lesbar: nur Meldung (Stock: keine Reaktion ohne
      Antwort)
    Codes werden NIE automatisch gelöscht (KTWO_MOTOR_CLEAR)."""

    def __init__(self, mode, pins, get_ctl, schedule, respond, run_script,
                 shutdown, print_state, is_paused, respond_error=None):
        if mode not in STALL_MONITOR_MODES:
            raise ValueError("stall_monitor muss off|report|act sein: %r" % (mode,))
        self.mode = mode
        self.pins = dict(pins)            # motor -> Pin-String (Config)
        self.get_ctl = get_ctl
        self.schedule = schedule
        self.respond = respond
        self.respond_error = respond_error or respond   # rote Zeilen (!!)
        self.run_script = run_script
        self.shutdown = shutdown
        self.print_state = print_state
        self.is_paused = is_paused
        self.edges = {m: 0 for m in self.pins}
        self.last_edge = None
        self.codes = {}
        self.last_action = None
        self.actions = []                 # Verlauf (für Status/Tests)
        self.ignored = 0
        self.cut_state = False
        self.cut_edges = 0
        self.busy = False
        self.pending = []
        self._scheduled = False
        self.halted = False

    # --- Callback der buttons (Reactor-Hauptthread) ---------------------
    def on_edge(self, motor, eventtime, state):
        state = 1 if state else 0
        rec = {"motor": motor, "pin": self.pins.get(motor), "state": state,
               "eventtime": float(eventtime), "result": None}
        self.last_edge = rec
        self.edges[motor] = self.edges.get(motor, 0) + 1
        rec["result"] = self._filter(motor, eventtime, state)
        if rec["result"] != "auswerten":
            if state:
                self.ignored += 1
                logging.info("k2_motor stall: %s steigend ignoriert (%s)",
                             motor, rec["result"])
            return rec["result"]
        if motor not in self.pending:
            self.pending.append(motor)
        if self.busy:
            rec["result"] = "wartet (Auswertung läuft)"
        elif not self._scheduled:
            self._scheduled = True
            self.schedule(self._process)
        return rec["result"]

    def _filter(self, motor, eventtime, state):
        if not state:
            return "fallend"
        if self.halted:
            return "nach Shutdown"
        ctl = self.get_ctl()
        if ctl is None or not ctl.is_ready:
            return "nicht bereit"
        if motor in STALL_AXIS_MOTORS and ctl.cut_cal_active:
            self.cut_state = True          # Stock: nur cut_state = True
            self.cut_edges += 1
            return "cut_state"
        if ctl.stall_guarded(motor, eventtime):
            return "gesperrt (Homing/Mode 1/3/Nachlauf)"
        if getattr(ctl, "cal_aktiv", None):
            return "gesperrt (Kalibrierlauf)"
        return "auswerten"

    def _process(self, *_a):
        self._scheduled = False
        if self.busy:
            return
        self.busy = True
        try:
            while self.pending and not self.halted:
                m = self.pending.pop(0)
                try:
                    self._handle(m)
                except Exception as exc:  # nie aus dem Reactor-Callback werfen
                    logging.exception("k2_motor stall: Auswertung %s", m)
                    self._act(m, "interner Fehler: %s" % exc, error=True)
        finally:
            self.busy = False
        if self.halted:
            self.pending = []

    # --- Auswertung -----------------------------------------------------
    def _act(self, motor, text, error=False):
        self.last_action = "%s: %s" % (motor, text)
        self.actions.append(self.last_action)
        self._say(self.last_action, error=error)

    def _say(self, msg, error=False):
        (logging.error if error else logging.warning)("k2_motor stall: %s", msg)
        try:
            (self.respond_error if error else self.respond)(
                "[k2_motor] Stall %s" % msg)
        except Exception:
            pass

    def _script(self, motor, script):
        try:
            self.run_script(script)
            return True
        except Exception as exc:
            self._say("%s: %s fehlgeschlagen: %s" % (motor, script.split()[0], exc),
                      error=True)
            return False

    def _handle(self, m):
        ctl = self.get_ctl()
        if getattr(ctl, "cal_aktiv", None):
            return                       # Lauf hält den Bus – kein 0C-Lesen im Reactor
        _tr, addr = ctl._transport_for(m)
        where = "%s (%s0x%02X, Pin %s)" % (m, "transparent " if m == "e" else "",
                                           addr, self.pins.get(m))
        res, last = None, None
        for i in range(STALL_READ_TRIES):
            r = ctl.check_protection([m]).get(m)
            if isinstance(r, dict):
                res = r
                break
            last = r
            self._say("%s: 0C [0b] Versuch %d/%d ohne Antwort (%s)"
                      % (where, i + 1, STALL_READ_TRIES, r))
        if res is None:
            self.codes[m] = {"read_error": str(last)}
            self._act(m, "Notfall-Leitung %s aktiv, Codes NICHT lesbar – keine "
                         "Reaktion (wie Stock). Leitung bleibt gelatcht: weitere "
                         "Fehler dieses Motors melden sich erst nach erfolgreichem "
                         "0C [0b] (KTWO_MOTOR_PROTECTION)" % where, error=True)
            return
        errs = [k or "bit%d" % b for b, k, _t in res["errors"]]
        warns = [k or "bit%d" % b for b, k, _t in res["warnings"]]
        self.codes[m] = {"err_code": res["err_code"], "warning_code": res["warning_code"],
                         "errors": errs, "warnings": warns}
        for b, k, t in res["errors"]:
            self._say('{"code":"%s","msg":"error_code %s motor_%s: %s (Bit %d)"}'
                      % (k, res["err_code"], m, t, b), error=True)
        for b, k, t in res["warnings"]:
            self._say('{"code":"%s","msg":"warning_code %s motor_%s: %s (Bit %d)"}'
                      % (k, res["warning_code"], m, t, b))
        codes = "Fehler %s, Warnungen %s (err_code=0x%08X warning_code=0x%08X)" % (
            ",".join(errs) or "-", ",".join(warns) or "-",
            res["err_code"], res["warning_code"])
        if res["ok"]:
            self._act(m, "Flanke %s ohne Code (8x00) – nur gemeldet" % where)
            return
        if self.mode != "act":
            self._act(m, "%s: %s – nur gemeldet (stall_monitor: report)" % (where, codes),
                      error=bool(errs))
            return
        state = self.print_state()
        printing = state == "printing"
        paused = bool(self.is_paused())
        if m == "e":
            done = []
            if printing and not paused:
                if self._script(m, "PAUSE"):
                    done.append("PAUSE")
                self._script(m, "M400")
            if self._script(m, "SET_STEPPER_ENABLE STEPPER=extruder ENABLE=0"):
                done.append("Extruder aus")
            self._act(m, "%s: %s – %s (Druckstatus %s; Codes bleiben, "
                         "KTWO_MOTOR_CLEAR)" % (where, codes, " + ".join(done) or
                                                "keine Aktion möglich", state),
                      error=True)
            return
        if errs:
            if printing:
                self.halted = True
                msg = ("k2_motor: Notfall-Leitung Motor %s im Druck – %s. Not-Stopp "
                       "wie Stock-Abbruch (stall_monitor: act). Codes NICHT gelöscht "
                       "(FIRMWARE_RESTART -> Init P6 bzw. KTWO_MOTOR_CLEAR)" % (where, codes))
                self.last_action = "%s: Shutdown (%s)" % (m, codes)
                self.actions.append(self.last_action)
                logging.error(msg)
                self.shutdown(msg)
                return
            ok = self._script(m, "M84")
            self._act(m, "%s: %s – %s (kein Druck, Status %s; Codes bleiben, "
                         "KTWO_MOTOR_CLEAR)" % (where, codes, "Motoren aus (M84)" if ok
                                                else "M84 FEHLGESCHLAGEN", state),
                      error=True)
            return
        if printing and not paused:
            ok = self._script(m, "PAUSE")
            self._act(m, "%s: %s – nur Warnung -> %s" % (
                where, codes, "PAUSE" if ok else "PAUSE FEHLGESCHLAGEN"))
            return
        self._act(m, "%s: %s – nur Warnung, kein Druck -> nur gemeldet" % (where, codes))

    def status(self):
        return {
            "mode": self.mode,
            "pins": dict(self.pins),
            "edges": dict(self.edges),
            "last_edge": dict(self.last_edge) if self.last_edge else None,
            "codes": {m: dict(v) for m, v in self.codes.items()},
            "last_action": self.last_action,
            "ignored": self.ignored,
            "busy": self.busy,
            "pending": list(self.pending),
            "cut_state": self.cut_state,
            "cut_edges": self.cut_edges,
            "halted": self.halted,
        }


class PrinterK2Motor:
    """Klipper-Objekt [k2_motor] – verdrahtet Controller mit Klipper."""

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.konsole = _K2K.Konsole(self.printer, "k2_motor", self.gcode)
        self._ctl_log = _K2K.LogAdapter("k2_motor")   # Controller -> klippy.log
        try:
            self.cfg = parse_klipper_config(config)
        except Exception as exc:
            raise config.error("[k2_motor] Config-Fehler: %s" % exc)
        self.main_mcu_name = self.cfg.get("main_mcu", "mcu")
        self.nozzle_mcu_name = self.cfg.get("nozzle_mcu", "nozzle_mcu")
        # MCU-Namen robust auflösen (Fix 16.10.2026): 'nozzle_mcu' ->
        # Upstream-Objekt 'mcu nozzle_mcu'; voller Name geht weiterhin.
        try:
            self.mcu = _lookup_mcu(self.printer, self.main_mcu_name)
            self.nozzle_mcu = _lookup_mcu(self.printer, self.nozzle_mcu_name)
        except Exception as exc:
            raise config.error("[k2_motor] MCU nicht gefunden (main_mcu=%r, "
                               "nozzle_mcu=%r): %s" % (self.main_mcu_name,
                                                       self.nozzle_mcu_name, exc))
        # REVIEW 1 (FIX): Alles Konfigurierende VOR klippy:connect.
        # - Strapping-Pins (motor_<m>_dir/_step + Enable) sofort per
        #   pins.setup_pin anlegen (KlippyGpio-Konstruktor). Danach nur setzen.
        # - Transparent-OID früh per create_oid + register_config_callback
        #   (config_transparent läuft im Config-Callback vor finalize, wie
        #   Stock-_build_config @0xa04fc). Lookup erst nach connect.
        self._gpio = KlippyGpio(
            self.printer, self.main_mcu_name,
            pin_specs=self._strapping_pin_specs())
        self._oid = self.nozzle_mcu.create_oid()
        self.nozzle_mcu.register_config_callback(self._build_transparent_config)
        self._tadapter = KlippyTransparentAdapter(self.nozzle_mcu, self._oid)
        self._transparent = None
        # RS485-Bus X/Y/Z/Z1 = gemeinsames Objekt 'k2_485' (Modul k2_485.py,
        # EIN Port/Lock für Motoren + CFS + Belt). Steht [k2_485] in der Config,
        # wird es hier sofort geladen (printer.load_object) – die Reihenfolge
        # der Abschnitte ist damit egal; sonst Suche beim Connect.
        self._bus = None
        self._rs485 = None
        try:
            if hasattr(config, "has_section") and config.has_section(BUS_SECTION):
                self._bus = self.printer.load_object(config, BUS_SECTION)
        except Exception as exc:
            raise config.error("[k2_motor] [%s] nicht ladbar: %s" % (BUS_SECTION, exc))
        self.controller = None
        # Ergebnis des letzten Motor-Inits (z. B. "ready", "key798"), für
        # get_status/Start-Wächter (25.10.2026); None = noch keiner gelaufen
        self.init_status = None
        # Stall-/Notfall-Überwachung (18.10.2026): wie Stock
        # zur CONFIG-Zeit per buttons registrieren (button_stall_pin_init im
        # __init__, MC@0xce6f0); Reaktion erst nach is_ready.
        self.stall = K2StallMonitor(
            self.cfg.get("stall_monitor", STALL_MONITOR_DEFAULT),
            self._stall_pins(), lambda: self.controller,
            schedule=lambda fn: self.reactor.register_callback(lambda et: fn()),
            respond=lambda t: self.konsole.warnung(t, log=False),
            respond_error=lambda t: self.konsole.fehler(t, log=False),
            run_script=self.gcode.run_script,
            shutdown=self._stall_shutdown,
            print_state=self._print_state, is_paused=self._is_paused)
        if self.stall.mode != "off":
            self._register_stall_buttons(config)
        self.printer.register_event_handler("klippy:connect", self.handle_connect)
        # G-Code-Namen: Upstream verbietet Buchstabe+Ziffer am Anfang
        # (gcode.py register_command) -> KTWO_ statt K2_ (Fix 16.10.2026).
        self.gcode.register_command("KTWO_MOTOR_INIT", self.cmd_init,
                                    desc="K2-Motor-Init (DRY_RUN=1 nur Plan)")
        self.gcode.register_command("KTWO_MOTOR_STATUS", self.cmd_status,
                                    desc="K2-Motor-Status (Version/Schutz/Feedback)")
        # Sensorless-Homing X/Y (Stock MOTOR_STALL_MODE / MOTOR_SYS_PARAM /
        # MOTOR_CHECK_PROTECTION_AFTER_HOME) – Namen ohne Ziffern (Upstream-
        # Parser trennt an Ziffern).
        self.gcode.register_command("KTWO_MOTOR_STALL_MODE", self.cmd_stall_mode,
                                    desc="Stall-Mode X/Y lesen bzw. setzen (DATA=1|2)")
        self.gcode.register_command("KTWO_MOTOR_SYS_PARAM", self.cmd_sys_param,
                                    desc="Motor-Sys-Param lesen bzw. flüchtig schreiben")
        self.gcode.register_command("KTWO_MOTOR_PROTECTION", self.cmd_protection,
                                    desc="Schutzabfrage 0C [0b] an alle Motoren")
        self.gcode.register_command("KTWO_MOTOR_CLEAR", self.cmd_clear,
                                    desc="Motor-Fehler/Warncodes löschen 0C [05] (allow_write)")
        self.gcode.register_command("KTWO_MOTOR_HOMING", self.cmd_homing,
                                    desc="Stock-Folge vor/nach X/Y-Homing (PHASE=BEGIN|END)")
        # Schnittpositions-Kalibrierung (Stock MOTOR_CHECK_CUT_POS + Kp-Rahmen
        # des Makros CALIBRATE_CUT_POS; Makro dazu: config-pi/cutcal.cfg)
        self.gcode.register_command("KTWO_MOTOR_CUT_CAL", self.cmd_cut_cal,
                                    desc="Schnittposition kalibrieren (DRY_RUN=1 nur Plan)")
        # Kalibrierwerte sichern/prüfen (NUR LESEND; Kalibrieren bleibt gesperrt)
        self.gcode.register_command("KTWO_MOTOR_CAL_BACKUP", self.cmd_cal_backup,
                                    desc="Kalibrierwerte sichern, nur lesend (MOTOR=x|y|z|z1|e|all)")
        self.gcode.register_command("KTWO_MOTOR_CAL_CHECK", self.cmd_cal_check,
                                    desc="Kalibrierwerte prüfen/mit Sicherung vergleichen, nur lesend")
        # Kalibrierlauf/Rückschreiben/Freigabe (Default AUS: allow_motor_calibration)
        self.gcode.register_command("KTWO_MOTOR_CAL_RUN", self.cmd_cal_run,
                                    desc="Motor kalibrieren (ohne CONFIRM nur Plan; bewegt!)")
        self.gcode.register_command("KTWO_MOTOR_CAL_RESTORE", self.cmd_cal_restore,
                                    desc="Kalibrierwerte aus einer Sicherung zurückschreiben")
        self.gcode.register_command("KTWO_MOTOR_CAL_ENABLE", self.cmd_cal_enable,
                                    desc="Motor nach Kalibrierung wieder freigeben")
        self._cal_token = None
        self._cal_cache = {}
        # Auto-Rückbau: bricht ein G-Code (z.B. G28 Y "No trigger") nach
        # PHASE=BEGIN ab, setzt das Modul X/Y zurück (Stall-Mode 2, Kp).
        self.printer.register_event_handler("gcode:command_error",
                                            self._handle_command_error)

    # --- FORCE_MOVE-Sperre --------------------------------------------------
    # Nur FORCE_MOVE (bewegt Stepper direkt); SET_KINEMATIC_POSITION bewegt
    # nichts und wird von Homing-Makros gebraucht -> nicht gesperrt.
    FORCE_MOVE_LOCK_CMDS = ("FORCE_MOVE",)

    def _xy_homed(self):
        """True, wenn X und Y gehomt sind (Motoren haben sich dann ohnehin
        schon bewegt; homing.cfg fährt danach wie Stock FORCE_MOVE stepper_y)."""
        try:
            th = self.printer.lookup_object("toolhead")
            st = th.get_status(self.reactor.monotonic())
            ha = str(st.get("homed_axes", ""))
            return "x" in ha and "y" in ha
        except Exception:
            return False

    def _motor_ready(self):
        if self.controller is not None and getattr(self.controller, "is_ready", False):
            return True
        return self._xy_homed()

    def _install_force_move_lock(self):
        """Umhüllt FORCE_MOVE (Upstream-Modul force_move): vor abgeschlossenem
        Motor-Init (is_ready) und vor X/Y-Homing abgelehnt, danach unverändert. Upstream gcode.register_command(cmd, None) gibt
        den alten Handler zurück. Ohne [force_move] bzw. mit
        force_move_lock: False passiert nichts."""
        if not self.cfg.get("force_move_lock", True):
            return
        if self.printer.lookup_object("force_move", None) is None:
            return
        for cmd in self.FORCE_MOVE_LOCK_CMDS:
            old = self.gcode.register_command(cmd, None)
            if old is None:
                continue

            def wrapped(gcmd, _old=old, _cmd=cmd):
                if not self._motor_ready():
                    raise gcmd.error("k2_motor: %s gesperrt – Motor-Init noch "
                                     "nicht abgeschlossen" % (_cmd,))
                gesperrt = self._cal_bewegung_gesperrt()
                if gesperrt:
                    raise gcmd.error("k2_motor: %s gesperrt – %s" % (_cmd, gesperrt))
                return _old(gcmd)
            self.gcode.register_command(
                cmd, wrapped, desc="%s (k2_motor: erst nach Motor-Init)" % cmd)

    # --- Stall-Überwachung -------------------------------------------------
    def _stall_pins(self):
        """motor -> Pin-String (unverändert aus der Config, ohne ^/! wie Stock)
        für alle Motoren aus motor_closed_loop mit motor_<m>_stall."""
        out = {}
        for m in VERIFY_ORDER:
            pin = (self.cfg.get("pins", {}).get(m) or {}).get("stall")
            if m in self.cfg.get("motor_closed_loop", []) and pin:
                out[m] = pin
        return out

    def _register_stall_buttons(self, config):
        """buttons.register_buttons([pin], cb) je Motor (Upstream-API
        extras/buttons.py; Callback (eventtime, state)). PB11/PB12 sind zugleich
        endstop_pin -> [duplicate_pin_override] wird VORHER geladen (sonst je
        nach Abschnitts-Reihenfolge 'pin used multiple times')."""
        if not self.stall.pins:
            return
        try:
            if hasattr(config, "has_section") and config.has_section(DUP_PIN_SECTION):
                self.printer.load_object(config, DUP_PIN_SECTION)
            buttons = self.printer.load_object(config, "buttons")
            for m, pin in self.stall.pins.items():
                buttons.register_buttons(
                    [pin], (lambda et, st, m=m: self.stall.on_edge(m, et, st)))
        except Exception as exc:
            raise config.error("[k2_motor] Stall-Überwachung (buttons) nicht "
                               "einrichtbar: %s (stall_monitor: off schaltet sie ab)"
                               % (exc,))

    def _stall_shutdown(self, msg):
        self.printer.invoke_shutdown(msg)

    def _print_state(self):
        ps = self.printer.lookup_object("print_stats", None)
        if ps is None:
            return "standby"
        try:
            return str(ps.get_status(self.reactor.monotonic()).get("state", ""))
        except Exception:
            return "unbekannt"

    def _is_paused(self):
        pr = self.printer.lookup_object("pause_resume", None)
        if pr is None:
            return False
        try:
            return bool(pr.get_status(self.reactor.monotonic()).get("is_paused"))
        except Exception:
            return False

    def _strapping_pin_specs(self):
        """Alle früh anzulegenden Pins: Dir/Step (x/y/z/z1) + Enable."""
        specs = []
        for m in PIN_MOTORS:
            for kind in ("dir", "step"):
                spec = (self.cfg.get("pins", {}).get(m) or {}).get(kind)
                if spec and spec.get("raw") and spec["raw"] not in specs:
                    specs.append(spec["raw"])
        if ENABLE_PIN_RAW not in specs:
            specs.append(ENABLE_PIN_RAW)
        return specs

    def _build_transparent_config(self):
        # Config-Callback (vor finalize): Düsen-Kaskade anmelden. Upstream ruft
        # Config-Callbacks OHNE Argument auf (mcu.py _send_config: cb()).
        # Creality-MCU-FW kennt config_transparent (Dictionary-ID 74).
        self.nozzle_mcu.add_config_cmd("config_transparent oid=%d" % self._oid)

    def _ensure_transports(self):
        """RS485-Transport für X/Y/Z/Z1 aus dem Objekt 'k2_485' bauen.

        Klare Fehlermeldung, wenn [k2_485] fehlt. Ob der Port wirklich offen
        ist, zeigt sich erst beim ersten Frame (k2_485 öffnet faul/beim
        Connect und meldet Port-Fehler selbst)."""
        if self._rs485 is not None:
            return self._rs485
        bus = self._bus
        if bus is None:
            try:
                bus = self.printer.lookup_object(BUS_SECTION)
            except Exception:
                bus = None
        if bus is None:
            raise RuntimeError(
                "[k2_motor] RS485-Bus fehlt: Abschnitt [%s] (serial: <by-id-Pfad>, "
                "baud: 230400) in printer.cfg anlegen – ohne ihn sind X/Y/Z/Z1 "
                "nicht erreichbar (nur KTWO_MOTOR_INIT DRY_RUN=1 geht)" % BUS_SECTION)
        self._bus = bus
        self._rs485 = Rs485Transport(bus, name=BUS_SECTION)
        return self._rs485

    def _ensure_controller(self):
        """Controller ohne Init-Lauf bauen (für lesende Befehle)."""
        if self.controller is None:
            self._ensure_transports()
            if self._transparent is None:
                self._transparent = TransparentTransport(self._tadapter, oid=self._oid)
            self.controller = K2MotorController(
                self.cfg, self._rs485, self._transparent, self._gpio,
                pause=reactor_sleep(self.reactor), logger=self._ctl_log,
                send_event=getattr(self.printer, "send_event", None),
                clock=self.reactor.monotonic)
        return self.controller

    def _cal_stepper_sperre_einrichten(self):
        """Harte Sperre: in einem Zustand aus CAL_STATUS_HART darf die Regelung
        nicht angehen. Klipper ruft register_state_callback vor jedem
        set_enable – hier Shutdown statt Freigabe (G1 E, CFS-Laden,
        SET_STEPPER_ENABLE, Z-Ausrichten … alle Wege). X/Y/Z/Z1 teilen die
        Enable-Leitung, der Extruder hat eine eigene."""
        se = self.printer.lookup_object("stepper_enable", None)
        if se is None or not hasattr(se, "lookup_enable"):
            self.konsole.warnung("stepper_enable fehlt – Kalibrier-Sperre nur über "
                                 "G-Code-Sperren")
            return
        for m, name in CAL_STEPPER_NAMEN.items():
            if m not in self.cfg.get("motor_closed_loop", []):
                continue
            try:
                et = se.lookup_enable(name)
            except Exception:
                continue
            et.register_state_callback(
                lambda print_time, state, _m=m: self._cal_freigabe_waechter(_m, state))
        try:
            self._cal_zustand()
        except Exception:
            pass

    def _cal_freigabe_waechter(self, motor, state):
        if not state:
            return
        gruppe = ("e",) if motor == "e" else ("x", "y", "z", "z1")
        cache = getattr(self, "_cal_cache", {}) or {}
        offen = ["%s (%s)" % (m, cache[m]) for m in gruppe
                 if cache.get(m) in CAL_STATUS_HART]
        if offen:
            self.printer.invoke_shutdown(
                "k2_motor: Stepper-Freigabe gesperrt – Kalibrier-Zustand %s; die Regelung "
                "darf nicht angehen (README §14)" % ", ".join(offen))

    def handle_connect(self):
        # Nur noch Lookups nach dem Connect (Stock L3089: transparent-Lookup
        # erst hier möglich; GPIO-Query ebenso optional). Config-Befehle
        # liefen bereits im Config-Callback (Review 1).
        # Meldungen hier (Connect) puffert self.konsole bis kurz nach „ready“
        # – direkt ausgegeben kamen sie nie in Mainsail an.
        self._install_force_move_lock()
        self._cal_stepper_sperre_einrichten()
        try:
            self._tadapter.setup()
        except Exception as exc:
            self.konsole.fehler("transparent-Setup: %s – Extruder-Motor ohne "
                                "Verbindung, kein Motor-Init" % exc)
            return
        try:
            self._gpio.handle_connect()
        except Exception as exc:
            self.konsole.warnung("gpio-query optional: %s" % exc)
        if self.cfg.get("auto_init"):
            try:
                self._ensure_transports()
            except Exception as exc:
                self.konsole.fehler("Auto-Init übersprungen: %s" % exc)
                return
            warte = self._cal_init_aufschub()
            if warte > 0 and hasattr(self.reactor, "register_timer"):
                # Ein Kalibrierlauf kann noch laufen (Klipper-Neustart mitten im
                # Lauf): der Init-Rundruf darf den Motor nicht erreichen.
                self.konsole.warnung("Kalibrierlauf kann noch laufen – Motor-Init erst "
                                     "in %.0f s" % warte)
                self.reactor.register_timer(self._cal_init_spaeter,
                                            self.reactor.monotonic() + warte)
                return
            try:
                self._run(auto=True)
            except (RuntimeError, OSError) as exc:
                self.konsole.fehler("Auto-Init abgebrochen: %s" % exc)

    def _cal_init_aufschub(self):
        """Sekunden bis kein Kalibrierlauf mehr laufen kann (Zustandsdatei)."""
        try:
            z = cal_zustand_laden(self._cal_verz())
        except Exception:
            return 0.0
        bis = [float(e.get("laeuft_bis") or 0) for e in z["motoren"].values()
               if isinstance(e, dict) and e.get("status") in ("laeuft", "gesperrt_unbekannt")]
        # gedeckelt: falsche Uhrzeit nach dem Einschalten darf nicht ewig sperren
        return min(CAL_AUFSCHUB_MAX_S, max([0.0] + [b - time.time() for b in bis]))

    def _cal_init_spaeter(self, eventtime):
        try:
            self._ensure_transports()
            self._run(auto=True)
        except Exception as exc:
            self.konsole.fehler("Auto-Init (nach Kalibrierlauf) abgebrochen: %s" % exc)
        return getattr(self.reactor, "NEVER", 9.0e99)

    def _run(self, auto=False):
        self._ensure_transports()
        if self._transparent is None:
            self._transparent = TransparentTransport(self._tadapter, oid=self._oid)
        pause = reactor_sleep(self.reactor)  # relative Sekunden -> absolute Weckzeit
        send_event = getattr(self.printer, "send_event", None)
        old = self.controller
        self.controller = K2MotorController(
            self.cfg, self._rs485, self._transparent, self._gpio,
            pause=pause, logger=self._ctl_log, send_event=send_event,
            clock=self.reactor.monotonic)
        if old is not None:  # Homing-/Stall-Zustand nicht verlieren
            self.controller.stall_mode.update(old.stall_mode)
            self.controller.homing_active = old.homing_active
            self.controller.stall_guard.update(old.stall_guard)
            self.controller.stall_guard_until.update(old.stall_guard_until)
            self.controller.cut_cal_last = old.cut_cal_last
        res = self.controller.run_init()
        self.init_status = res.get("status")
        # Fehlerzeilen rot; beim Auto-Init (Connect) Info-Zeilen nur ins Log
        # (sonst ~30 Zeilen bei jedem Start), dafür eine Abschlusszeile.
        # (klippy.log schreibt der Controller selbst über self._ctl_log)
        for lvl, msg in res.get("log", []):
            if lvl == "error":
                self.konsole.fehler(msg, log=False)
            elif not auto:
                self.konsole.info(msg, log=False)
        if auto:
            n_err = len(res.get("errors", []))
            text = ("Motor-Init (Start): Status %s, Fehler: %d%s"
                    % (res.get("status"), n_err,
                       "" if res.get("ready") else
                       " – Motoren NICHT bereit (FIRMWARE_RESTART bzw. "
                       "KTWO_MOTOR_INIT; Details im klippy.log)"))
            if res.get("ready") and not n_err:
                self.konsole.info(text)
            else:
                self.konsole.fehler(text)
        if res.get("ready"):
            self._protect_en_pruefen(auto)
        return res

    def _protect_en_pruefen(self, auto=False):
        """Nach jedem erfolgreichen Motor-Init: protect_en aller Motoren lesen;
        0 = Schutz AUS -> rote Meldung (Abhilfe: Drucker aus/ein, damit die
        Motor-FW ihre Prüfung neu durchläuft; FIRMWARE_RESTART reicht nicht)."""
        ctl = self.controller
        if ctl is None or not hasattr(ctl, "protect_en_lesen"):
            return {}
        try:
            pe = ctl.protect_en_lesen()
        except Exception as exc:  # nie den Init-Abschluss stören
            self.konsole.warnung("protect_en nicht geprüft: %s" % exc)
            return {}
        aus = sorted(m for m, v in pe.items() if v == 0)
        fehlt = sorted(m for m, v in pe.items() if not isinstance(v, int))
        if aus:
            self.konsole.fehler(
                "Motor-Schutz AUS bei %s (protect_en = 0 – Fehler der Motor-FW beim "
                "Einschalten): kein Überstrom-/Schleppfehler-/Stall-Schutz im Motor. "
                "Drucker aus- und wieder einschalten (FIRMWARE_RESTART reicht nicht), "
                "dann KTWO_MOTOR_STATUS" % ", ".join(aus))
        if fehlt:
            self.konsole.warnung("protect_en nicht lesbar: %s" % "; ".join(
                "%s: %s" % (m, pe[m]) for m in fehlt))
        text = "Motor-Schutz (protect_en, 0 = aus): %s" % ", ".join(
            "%s=%s" % (m, pe[m] if isinstance(pe[m], int) else "?") for m in sorted(pe))
        if auto:
            self._ctl_log.info(text)
        else:
            self.konsole.info(text)
        return pe

    def cmd_init(self, gcmd):
        dry = bool(gcmd.get_int("DRY_RUN", 0))
        warte = 0.0 if dry else self._cal_init_aufschub()
        if warte > 0:
            raise gcmd.error("[k2_motor] Motor-Init gesperrt: ein Kalibrierlauf kann noch "
                             "%.0f s laufen (Rundruf würde danach ausgeführt) – nichts "
                             "gesendet" % warte)
        allow = bool(gcmd.get_int("ALLOW_WRITE", 1 if self.cfg.get("allow_write") else 0))
        if dry:
            ctl = K2MotorController(self.cfg, self._rs485, self._transparent,
                                    self._gpio, pause=lambda s: None, logger=self._ctl_log)
            gcmd.respond_info(ctl.describe_plan())
            return
        if not allow:
            ctl = K2MotorController(self.cfg, self._rs485, self._transparent,
                                    self._gpio, pause=lambda s: None, logger=self._ctl_log)
            gcmd.respond_info(ctl.describe_plan())
            gcmd.respond_info("ALLOW_WRITE=0 (allow_write=False): nur gelesen, nichts geschrieben.")
            return
        try:
            self._ensure_transports()
            res = self._run()
        except (RuntimeError, OSError) as exc:
            # Bus/Port-Fehler ([k2_485]) als G-Code-Fehler, kein Shutdown.
            msg = str(exc)
            raise gcmd.error(msg if msg.startswith("[") else "[k2_motor] " + msg)
        gcmd.respond_info("[k2_motor] Status: %s (Fehler: %d)" % (
            res.get("status"), len(res.get("errors", []))))

    def cmd_status(self, gcmd):
        try:
            ctl = self._ensure_controller()
        except RuntimeError as exc:
            raise gcmd.error(str(exc))
        st = ctl.motor_status()
        for m, e in st.items():
            ver = e.get("version", e.get("version_error"))
            pro = e.get("protection", e.get("protection_error"))
            gcmd.respond_info("[k2_motor] %s: version=%s schutz=%s pos_fdb=%s spd_fdb=%s" % (
                m, ver, pro, e.get("pos_fdb", e.get("pos_fdb_error")),
                e.get("spd_fdb", e.get("spd_fdb_error"))))
        pe = ctl.protect_en_lesen()
        gcmd.respond_info("[k2_motor] Motor-Schutz protect_en (0 = AUS): %s%s" % (
            ", ".join("%s=%s" % (m, pe[m] if isinstance(pe[m], int) else "?") for m in sorted(pe)),
            " – AUS bei %s: Drucker aus/ein" % ", ".join(sorted(m for m, v in pe.items() if v == 0))
            if any(v == 0 for v in pe.values()) else ""))
        sm = self.stall.status()
        gcmd.respond_info("[k2_motor] Stall-Überwachung: %s, Pins %s, Flanken %s, "
                          "letzte Aktion: %s" % (sm["mode"], sm["pins"] or "-",
                                                 sm["edges"], sm["last_action"] or "-"))


    # --- Sensorless-Homing X/Y ------------------------------------------
    def _motors_arg(self, gcmd, default, allowed):
        raw = str(gcmd.get("MOTOR", default)).strip().lower()
        motors = ["x", "y"] if raw == "xy" else [s.strip() for s in raw.split(",") if s.strip()]
        for m in motors:
            if m not in allowed:
                raise gcmd.error("[k2_motor] MOTOR=%s ungültig (erlaubt: %s)"
                                 % (raw, ", ".join(allowed + (("xy",) if "y" in allowed else ()))))
        return motors

    def _wait_moves(self):
        """Bewegungen abwarten, bevor der Motor-Modus wechselt (Sicherheit;
        Stock-Wrapper wartet vermutlich nicht – ANNAHME)."""
        try:
            self.printer.lookup_object("toolhead").wait_moves()
        except Exception:
            pass

    def _ctl_or_error(self, gcmd):
        try:
            return self._ensure_controller()
        except RuntimeError as exc:
            msg = str(exc)
            raise gcmd.error(msg if msg.startswith("[") else "[k2_motor] " + msg)

    def cmd_stall_mode(self, gcmd):
        """KTWO_MOTOR_STALL_MODE [MOTOR=x|y|xy|z|z1] [DATA=1|2]
        Ohne DATA: lesen (0x11 [04]). Mit DATA: setzen (nur x/y, allow_write)."""
        data = gcmd.get_int("DATA", None)
        if data is None:
            ctl = self._ctl_or_error(gcmd)
            for m in self._motors_arg(gcmd, "xy", STALL_READ_MOTORS):
                try:
                    gcmd.respond_info("[k2_motor] %s: stall_mode=%d" % (m, ctl.get_stall_mode(m)))
                except K2MotorError as exc:
                    raise gcmd.error("[k2_motor] %s" % exc)
            return
        motors = self._motors_arg(gcmd, "xy", STALL_SET_MOTORS)
        if data not in STALL_MODES_SETTABLE:
            raise gcmd.error("[k2_motor] DATA=%d ungültig (1 = Endschalter, 2 = Notfall-Schutz)" % data)
        if not self.cfg.get("allow_write"):
            raise gcmd.error("[k2_motor] Stall-Mode setzen gesperrt: allow_write=False – nichts gesendet")
        ctl = self._ctl_or_error(gcmd)
        self._wait_moves()
        try:
            for line in ctl.set_stall_mode(motors, data):
                gcmd.respond_info("[k2_motor] %s" % line)
        except K2MotorError as exc:
            raise gcmd.error("[k2_motor] %s" % exc)

    def cmd_sys_param(self, gcmd):
        """KTWO_MOTOR_SYS_PARAM MOTOR=x ID=70 [VALUE=300]
        Ohne VALUE: lesen (0x06 [02,id]). Mit VALUE: flüchtig schreiben
        (0x06 [01,id]+<f, nur Stock-IDs, allow_write; kein 06 [03])."""
        motors = self._motors_arg(gcmd, "x", SYS_PARAM_MOTORS)
        pid = gcmd.get_int("ID", minval=0, maxval=len(_SYS_PARAM_NAMES) - 1)
        value = gcmd.get_float("VALUE", None)
        if value is not None and not self.cfg.get("allow_write"):
            raise gcmd.error("[k2_motor] Sys-Param schreiben gesperrt: allow_write=False – nichts gesendet")
        ctl = self._ctl_or_error(gcmd)
        name = _SYS_PARAM_NAMES[pid]
        try:
            for m in motors:
                if value is None:
                    gcmd.respond_info("[k2_motor] %s ID %d %s = %r" % (
                        m, pid, name, ctl.read_sys_param(m, pid)))
                else:
                    self._wait_moves()
                    v = ctl.write_sys_param(m, pid, value)
                    gcmd.respond_info("[k2_motor] %s ID %d %s = %g geschrieben (Echo ok, "
                                      "flüchtig)" % (m, pid, name, v))
        except K2MotorError as exc:
            raise gcmd.error("[k2_motor] %s" % exc)

    def cmd_protection(self, gcmd):
        """KTWO_MOTOR_PROTECTION [STRICT=1] – 0C [0b] an alle (lesend; bei
        Stall-Mode 2 quittiert die FW damit die Notfall-Leitung)."""
        strict = bool(gcmd.get_int("STRICT", 0))
        ctl = self._ctl_or_error(gcmd)
        bad = []
        for m, res in ctl.check_protection().items():
            if isinstance(res, dict):
                txt = "ok" if res["ok"] else "err=%s warn=%s" % (
                    [k for _b, k, _t in res["errors"]], [k for _b, k, _t in res["warnings"]])
                if not res["ok"]:
                    bad.append(m)
            else:
                txt = "keine Antwort (%s)" % res
                if strict:
                    bad.append(m)
            gcmd.respond_info("[k2_motor] Schutz %s: %s" % (m, txt))
        if strict and bad:
            raise gcmd.error("[k2_motor] Schutzabfrage: Problem bei %s" % ", ".join(bad))

    def cmd_clear(self, gcmd):
        """KTWO_MOTOR_CLEAR [MOTOR=all|x|y|z|z1|e|x,y,…] – Ersatz für Stock
        MOTOR_CLEAR_ERR_WARN_CODE NUM=n DATA=5 (NUM=0 = alle inkl. E): 0C [05],
        danach 0C [0b] zur Kontrolle. SCHREIBEND: nur mit allow_write: True
        (kein G-Code-Override). Bleiben Codes stehen -> G-Code-Fehler."""
        raw = str(gcmd.get("MOTOR", "all")).strip().lower()
        closed = list(self.cfg.get("motor_closed_loop", []))
        if raw in ("all", "0", "alle"):
            motors = closed
        else:
            motors = self._motors_arg(gcmd, "all", tuple(VERIFY_ORDER))
            for m in motors:
                if m not in closed:
                    raise gcmd.error("[k2_motor] MOTOR=%s nicht in motor_closed_loop" % m)
        if not self.cfg.get("allow_write"):
            raise gcmd.error("[k2_motor] Codes löschen gesperrt: allow_write=False – "
                             "nichts gesendet")
        ctl = self._ctl_or_error(gcmd)
        try:
            log, errors, verify = ctl.clear_protection(motors)
        except K2MotorError as exc:
            raise gcmd.error("[k2_motor] %s" % exc)
        for line in log:
            gcmd.respond_info("[k2_motor] %s" % line)
        still = []
        for m, res in verify.items():
            if isinstance(res, dict):
                if res["ok"]:
                    gcmd.respond_info("[k2_motor] Kontrolle %s 0C [0b]: ok (8x00)" % m)
                else:
                    still.append("%s err=%s warn=%s" % (
                        m, [k for _b, k, _t in res["errors"]],
                        [k for _b, k, _t in res["warnings"]]))
                self.stall.codes[m] = {
                    "err_code": res["err_code"], "warning_code": res["warning_code"],
                    "errors": [k for _b, k, _t in res["errors"]],
                    "warnings": [k for _b, k, _t in res["warnings"]], "cleared": True}
            else:
                gcmd.respond_info("[k2_motor] Kontrolle %s 0C [0b]: keine Antwort (%s)"
                                  % (m, res))
        if errors or still:
            raise gcmd.error("[k2_motor] Codes löschen: %s" % " | ".join(
                errors + (["Codes bestehen weiter (Ursache besteht?): " + "; ".join(still)]
                          if still else [])))

    def cmd_homing(self, gcmd):
        """KTWO_MOTOR_HOMING PHASE=BEGIN|END – Stock-Folge rund um G28 X/Y."""
        phase = str(gcmd.get("PHASE")).strip().upper()
        if phase not in ("BEGIN", "END"):
            raise gcmd.error("[k2_motor] PHASE muss BEGIN oder END sein: %r" % phase)
        if not self.cfg.get("allow_write"):
            raise gcmd.error("[k2_motor] Homing-%s gesperrt: allow_write=False – nichts "
                             "gesendet, keine Bewegung" % phase)
        ctl = self._ctl_or_error(gcmd)
        if phase == "BEGIN":
            gesperrt = self._cal_bewegung_gesperrt()
            if gesperrt:
                raise gcmd.error("[k2_motor] Homing gesperrt – %s – keine Bewegung" % gesperrt)
        self._wait_moves()
        if phase == "BEGIN":
            try:
                log = ctl.homing_begin()
            except K2MotorError as exc:
                raise gcmd.error("[k2_motor] %s" % exc)
            for line in log:
                gcmd.respond_info("[k2_motor] %s" % line)
            return
        log, errors = ctl.homing_end()
        for line in log:
            gcmd.respond_info("[k2_motor] %s" % line)
        if errors:
            raise gcmd.error("[k2_motor] Homing-Abschluss mit Fehlern: %s" % " | ".join(errors))

    def _handle_command_error(self):
        """gcode:command_error – lief BEGIN, aber END nicht (Homing-Abbruch),
        X/Y zurücksetzen (Stall-Mode 2, Kp). Fehler nur melden."""
        ctl = self.controller
        if ctl is None or not ctl.homing_active:
            return
        try:
            log, errors = ctl.homing_end(protection=False)
        except Exception as exc:  # nie aus dem Fehlerpfad heraus werfen
            log, errors = [], [str(exc)]
        try:
            self.gcode.respond_info(
                "[k2_motor] Homing abgebrochen – Rückbau X/Y: %s%s" % (
                    "; ".join(log) or "-",
                    (" | FEHLER: " + " | ".join(errors)) if errors else ""))
        except Exception:
            pass

    # --- Schnittpositions-Kalibrierung -----------------------------------
    def _cut_cal_params(self):
        """[k2_motor]-Optionen + Prüfbereich/Y aus [k2_box] (falls geladen)."""
        box = self.printer.lookup_object(CUT_CAL_SECTION, None)
        box_cfg = getattr(box, "cfg", None) if box is not None else None
        return build_cut_cal_params(self.cfg.get("cut_cal"),
                                    box_cfg if isinstance(box_cfg, dict) else None)

    def _freigabe(self):
        """(ok, text): Bewegungs-Freigabe aus Makro _PI_FREIGABE (homing.cfg).
        Fehlt das Makro, gilt die Bewegung als NICHT freigegeben."""
        obj = self.printer.lookup_object("gcode_macro _PI_FREIGABE", None)
        if obj is None:
            return False, "Makro _PI_FREIGABE fehlt (homing.cfg)"
        try:
            vs = obj.get_status(self.reactor.monotonic())
        except Exception:
            vs = getattr(obj, "variables", {}) or {}
        if vs.get("allow_move") is not True:
            return False, "_PI_FREIGABE allow_move=%s" % (vs.get("allow_move"),)
        return True, "allow_move=True"

    def _make_cut_motion(self, p):
        return KlippyCutMotion(self.printer, settle=p["settle"])

    def cmd_cut_cal(self, gcmd):
        """KTWO_MOTOR_CUT_CAL [DRY_RUN=1] – Schnittposition kalibrieren."""
        try:
            p = self._cut_cal_params()
        except K2MotorError as exc:
            raise gcmd.error("[k2_motor] %s" % exc)
        restore_kp = float(self.cfg["params"].get("x", {}).get(
            "controller_pos_loop_pid_param_kp", HOMING_POS_KP_RESTORE_DEFAULT))
        if gcmd.get_int("DRY_RUN", 0):
            gcmd.respond_info(describe_cut_cal(p, restore_kp))
            return
        if not self.cfg.get("allow_write"):
            raise gcmd.error("[k2_motor] Cut-Kalibrierung gesperrt: allow_write=False – "
                             "nichts gesendet, keine Bewegung")
        ok, why = self._freigabe()
        if not ok:
            raise gcmd.error("[k2_motor] Cut-Kalibrierung gesperrt: %s – nichts "
                             "gesendet, keine Bewegung" % why)
        ctl = self._ctl_or_error(gcmd)
        configfile = self.printer.lookup_object("configfile")

        def _save(value):
            configfile.set(CUT_CAL_SECTION, CUT_CAL_OPTION, CUT_CAL_FORMAT % value)

        try:
            res = ctl.cut_calibrate(self._make_cut_motion(p), p, _save)
        except K2MotorError as exc:
            last = ctl.cut_cal_last or {}
            for line in last.get("log", []):
                gcmd.respond_info("[k2_motor] %s" % line)
            raise gcmd.error("[k2_motor] %s" % exc)
        for line in res["log"]:
            gcmd.respond_info("[k2_motor] %s" % line)
        gcmd.respond_info(
            "[k2_motor] Cut-Kalibrierung ok: Treffer X=%.2f/X=%.2f (%d Anstöße), "
            "cut_pos_x = %s -> SAVE_CONFIG ausführen (übernimmt [%s] %s)" % (
                res["first_hit"], res["second_hit"], res["probes"],
                CUT_CAL_FORMAT % res["result"], CUT_CAL_SECTION, CUT_CAL_OPTION))

    # --- Kalibrier-Sicherung/-Prüfung (NUR LESEND) ----------------------------
    def _cal_motors(self, gcmd):
        raw = str(gcmd.get("MOTOR")).strip().lower()
        closed = list(self.cfg.get("motor_closed_loop", []))
        if raw in ("all", "alle"):
            return closed
        motors = [x.strip() for x in raw.split(",") if x.strip()]
        for m in motors:
            if m not in closed:
                raise gcmd.error("[k2_motor] MOTOR=%s ungültig (erlaubt: %s, all)"
                                 % (raw, ", ".join(closed)))
        if not motors:
            raise gcmd.error("[k2_motor] MOTOR fehlt (x, y, z, z1, e oder all)")
        return motors

    def _cal_sperre(self):
        """Grund, warum jetzt nicht gelesen wird, sonst None. Die Lesefolge
        belegt den Bus einige Sekunden je Motor – nicht während eines Drucks."""
        st = self._print_state()
        if st in ("printing", "paused") or self._is_paused():
            return "Druck läuft oder ist pausiert (%s)" % st
        ctl = self.controller
        if ctl is not None and ctl.cut_cal_active:
            return "Cut-Kalibrierung läuft"
        return None

    def _cal_homing_offen(self, gcmd, was):
        """Offener Homing-Rahmen: NICHT per gcmd.error abbrechen – ein G-Code-
        Fehler löst den Homing-Rückbau aus (_handle_command_error), der
        schreibt. Nur melden. -> True = abgelehnt."""
        ctl = self.controller
        if ctl is not None and ctl.homing_active:
            gcmd.respond_info("[k2_motor] %s gesperrt: Homing läuft – nichts "
                              "gesendet" % was)
            return True
        return False

    def _cal_lesen(self, gcmd, motors, was):
        """Generator: je Motor (motor, snap, fehlertext) – sofort, damit eine
        Sicherung schon geschrieben ist, wenn ein späterer Motor scheitert."""
        why = self._cal_sperre()
        if why:
            raise gcmd.error("[k2_motor] %s gesperrt: %s – nichts gesendet" % (was, why))
        ctl = self._ctl_or_error(gcmd)
        if not getattr(ctl, "is_ready", False):
            raise gcmd.error("[k2_motor] %s gesperrt: Motor-Init nicht abgeschlossen – "
                             "Motoren ohne Adresse (erst KTWO_MOTOR_INIT) – nichts "
                             "gesendet" % was)
        self._wait_moves()
        for m in motors:
            try:
                snap = ctl.cal_snapshot(m)
            except K2MotorError as exc:
                yield m, None, str(exc)
                continue
            jetzt = time.localtime()
            snap["zeit"] = time.strftime("%Y-%m-%d %H:%M:%S", jetzt)
            snap["zeit_datei"] = time.strftime("%Y%m%d-%H%M%S", jetzt)
            yield m, snap, None

    def cmd_cal_backup(self, gcmd):
        """KTWO_MOTOR_CAL_BACKUP MOTOR=x|y|z|z1|e|all – Kalibrierwerte und alle
        77 Parameter (RAM + Flash) in je eine Datei sichern. NUR LESEND, auch
        mit allow_write=False. Die UUID steht nur in der Datei."""
        motors = self._cal_motors(gcmd)
        if self._cal_homing_offen(gcmd, "Kalibrier-Sicherung"):
            return
        verz = self.cfg.get("cal_backup_dir") or CAL_BACKUP_DIR_DEFAULT
        probleme = []
        try:
            for m, snap, fehler in self._cal_lesen(gcmd, motors, "Kalibrier-Sicherung"):
                if snap is None:
                    probleme.append("%s nicht gesichert: %s" % (m, fehler))
                    gcmd.respond_info("[k2_motor] Sicherung %s abgebrochen: %s" % (m, fehler))
                    continue
                try:
                    pfad = cal_sicherung_schreiben(verz, snap)
                except OSError as exc:
                    probleme.append("%s nicht geschrieben: %s" % (m, exc))
                    continue
                gcmd.respond_info("[k2_motor] Sicherung %s" % cal_kurztext(snap))
                gcmd.respond_info("[k2_motor]   Datei: %s" % pfad)
                if not snap["vollstaendig"]:
                    probleme.append("%s unvollständig (%d Lesefehler)" % (
                        m, len(snap["fehler"])))
                    for t in snap["fehler"][:5]:
                        gcmd.respond_info("[k2_motor]   Lesefehler: %s" % t)
                if not snap["plausibel"]:
                    probleme.append("%s unplausibel" % m)
                    for t in snap["plausi_gruende"]:
                        gcmd.respond_info("[k2_motor]   unplausibel: %s" % t)
        except gcmd.error:
            raise
        except Exception as exc:  # nie „Internal error“ (= Shutdown) aus einem Lesebefehl
            raise gcmd.error("[k2_motor] Kalibrier-Sicherung: unerwarteter Fehler %s: %s"
                             % (type(exc).__name__, exc))
        gcmd.respond_info("[k2_motor] %s Nichts geschrieben, nichts bewegt."
                          % CAL_HINWEIS_RECORD)
        if probleme:
            raise gcmd.error("[k2_motor] Sicherung mit Problemen: %s"
                             % "; ".join(probleme))

    def cmd_cal_check(self, gcmd):
        """KTWO_MOTOR_CAL_CHECK MOTOR=x|y|z|z1|e|all [BACKUP=<datei>] – aktuellen
        Stand lesen, bewerten und mit der Sicherung desselben Motors (gleiche
        UUID, vollständig + plausibel bevorzugt) bzw. der angegebenen Datei
        vergleichen. NUR LESEND. ALARM (phase_order_invert geändert,
        unplausibel) -> G-Code-Fehler."""
        motors = self._cal_motors(gcmd)
        datei = str(gcmd.get("BACKUP", "")).strip()
        if datei and len(motors) != 1:
            raise gcmd.error("[k2_motor] BACKUP= nur zusammen mit genau einem MOTOR")
        if self._cal_homing_offen(gcmd, "Kalibrier-Prüfung"):
            return
        verz = self.cfg.get("cal_backup_dir") or CAL_BACKUP_DIR_DEFAULT
        alt_fest = None
        if datei:
            try:
                alt_fest = cal_sicherung_laden(verz, datei)
            except (OSError, ValueError) as exc:
                raise gcmd.error("[k2_motor] Sicherung nicht lesbar: %s" % exc)
            if alt_fest.get("motor") != motors[0]:
                raise gcmd.error("[k2_motor] %s gehört zu Motor %s, nicht %s"
                                 % (datei, alt_fest.get("motor"), motors[0]))
        alarm, fehlt = [], []
        try:
            for m, snap, fehler in self._cal_lesen(gcmd, motors, "Kalibrier-Prüfung"):
                if snap is None:
                    fehlt.append("%s: %s" % (m, fehler))
                    gcmd.respond_info("[k2_motor] Prüfung %s abgebrochen: %s" % (m, fehler))
                    continue
                if alt_fest is not None:
                    name, alt = datei, alt_fest
                else:
                    name, alt = cal_sicherung_suchen(verz, m, snap.get("uuid"))
                alarme, warn, info = cal_vergleich(
                    snap, alt, self.cfg.get("params", {}).get(m, {}))
                gcmd.respond_info("[k2_motor] Prüfung %s" % cal_kurztext(snap))
                if alt is not None:
                    gcmd.respond_info("[k2_motor]   verglichen mit %s (%s)"
                                      % (name, alt.get("zeit", "?")))
                for t in alarme:
                    gcmd.respond_info("[k2_motor]   ALARM: %s" % t)
                for t in warn:
                    gcmd.respond_info("[k2_motor]   Warnung: %s" % t)
                for t in info:
                    gcmd.respond_info("[k2_motor]   %s" % t)
                if alarme:
                    alarm.append(m)
        except gcmd.error:
            raise
        except Exception as exc:  # nie „Internal error“ (= Shutdown) aus einem Lesebefehl
            raise gcmd.error("[k2_motor] Kalibrier-Prüfung: unerwarteter Fehler %s: %s"
                             % (type(exc).__name__, exc))
        if alarm or fehlt:
            raise gcmd.error("[k2_motor] Kalibrier-Prüfung: %s" % " | ".join(
                (["ALARM bei %s – Motor nicht kalibrieren/benutzen, bevor die "
                  "Ursache geklärt ist" % ", ".join(alarm)] if alarm else [])
                + (["nicht gelesen: %s" % "; ".join(fehlt)] if fehlt else [])))

    # --- Kalibrierlauf / Rückschreiben / Freigabe (SCHREIBEND) ----------------
    def _cal_verz(self):
        return self.cfg.get("cal_backup_dir") or CAL_BACKUP_DIR_DEFAULT

    def _cal_zustand(self):
        """Zustandsdatei; ein „laeuft“ ohne Abschluss nach Ablauf = unbekannt.
        Kaputte Datei -> alle Motoren „gesperrt_unbekannt“ (fail-closed)."""
        z = cal_zustand_laden(self._cal_verz())
        if z.get("fehler"):
            z["motoren"] = dict((m, {"status": "gesperrt_unbekannt", "laeuft_bis": 0,
                                     "datei_fehler": z["fehler"]})
                                for m in self.cfg.get("motor_closed_loop", []))
        for e in z["motoren"].values():
            if isinstance(e, dict) and e.get("status") == "laeuft" \
                    and float(e.get("laeuft_bis") or 0) < time.time():
                e["status"] = "gesperrt_unbekannt"
        self._cal_cache = dict((m, e.get("status")) for m, e in z["motoren"].items()
                               if isinstance(e, dict))
        return z

    def _cal_status(self, motor):
        return (self._cal_zustand()["motoren"].get(motor) or {}).get("status")

    def _cal_status_setzen(self, motor, status, **extra):
        z = cal_zustand_laden(self._cal_verz())
        z.pop("fehler", None)
        if status is None:
            z["motoren"].pop(motor, None)
        else:
            e = {"status": status, "seit": time.strftime("%Y-%m-%d %H:%M:%S")}
            e.update(extra)
            z["motoren"][motor] = e
        cal_zustand_schreiben(self._cal_verz(), z)
        self._cal_zustand()                   # Cache für die Stepper-Sperre

    def _cal_bewegung_gesperrt(self):
        """Text, wenn eine Achse nach einem Kalibrierlauf noch nicht wieder
        freigegeben ist, sonst None (Datei fehlt/kaputt -> None)."""
        try:
            z = self._cal_zustand()
        except Exception:
            return None
        offen = ["%s: %s" % (m, CAL_STATUS_TEXT.get(e.get("status"), e.get("status")))
                 for m, e in sorted(z["motoren"].items())
                 if m in PIN_MOTORS and isinstance(e, dict) and e.get("status")]
        return ("Kalibrier-Zustand offen (%s)" % "; ".join(offen)) if offen else None

    def _cal_ein_motor(self, gcmd):
        m = str(gcmd.get("MOTOR")).strip().lower()
        if m not in self.cfg.get("motor_closed_loop", []):
            raise gcmd.error("[k2_motor] MOTOR=%s ungültig (genau einer: %s)"
                             % (m, ", ".join(self.cfg.get("motor_closed_loop", []))))
        return m

    def _cal_runner(self, motor):
        """RS485: Busaufruf im Hilfs-Thread, Reactor pausiert in 50-ms-Schritten
        (wie ReactorBusProxy in k2_box; der Bus-Lock hält alle anderen Teilnehmer
        fern). Extruder: direkt (Düsenkanal wartet ohnehin im Reactor)."""
        if motor == "e" or not hasattr(self.reactor, "pause"):
            return lambda fn: fn()

        def run(fn):
            res = {}

            def work():
                try:
                    res["v"] = fn()
                except BaseException as exc:
                    res["e"] = exc
            t = threading.Thread(target=work, name="k2_motor-cal")
            t.daemon = True
            t.start()
            while t.is_alive():
                self.reactor.pause(self.reactor.monotonic() + 0.05)
            t.join()
            if "e" in res:
                raise res["e"]
            return res.get("v")
        return run

    def _cal_schreib_sperre(self, gcmd, motor, was, bewegt):
        """Alle Freigaben vor einem schreibenden Kalibrier-Befehl, ohne Bus.
        -> Controller. (Homing-Rahmen vorher per _cal_homing_offen prüfen.)"""
        if not self.cfg.get("allow_write"):
            raise gcmd.error("[k2_motor] %s gesperrt: allow_write=False – nichts gesendet" % was)
        if not self.cfg.get("allow_motor_calibration"):
            raise gcmd.error("[k2_motor] %s gesperrt: [k2_motor] allow_motor_calibration "
                             "ist False (Default) – nichts gesendet" % was)
        if motor in ("z", "z1") and not self.cfg.get("allow_z_calibration"):
            raise gcmd.error("[k2_motor] %s %s gesperrt: allow_z_calibration=False – Z/Z1 "
                             "auszukuppeln ist aufwendig, eine Spindel allein kippt das "
                             "Bett" % (was, motor))
        why = self._cal_sperre()
        if why:
            raise gcmd.error("[k2_motor] %s gesperrt: %s – nichts gesendet" % (was, why))
        if bewegt:
            ok, grund = self._freigabe()
            if not ok:
                raise gcmd.error("[k2_motor] %s gesperrt: %s – nichts gesendet, keine "
                                 "Bewegung" % (was, grund))
        for m, e in self._cal_zustand()["motoren"].items():
            if isinstance(e, dict) and e.get("status") == "laeuft":
                raise gcmd.error("[k2_motor] %s gesperrt: Kalibrierlauf an %s läuft noch "
                                 "(bis %s)" % (was, m, time.strftime(
                                     "%H:%M:%S", time.localtime(float(e.get("laeuft_bis") or 0)))))
        ctl = self._ctl_or_error(gcmd)
        if not getattr(ctl, "is_ready", False):
            raise gcmd.error("[k2_motor] %s gesperrt: Motor-Init nicht abgeschlossen – "
                             "nichts gesendet" % was)
        return ctl

    def _cal_token_neu(self, motor, aktion):
        tok = os.urandom(3).hex()
        self._cal_token = {"token": tok, "motor": motor, "aktion": aktion,
                           "bis": self.reactor.monotonic() + CAL_TOKEN_S}
        return tok

    def _cal_token_pruefen(self, gcmd, motor, aktion, tok):
        t, self._cal_token = self._cal_token, None      # nur einmal verwendbar
        if not t or t["token"] != str(tok).strip().lower() or t["motor"] != motor \
                or t["aktion"] != aktion or self.reactor.monotonic() > t["bis"]:
            raise gcmd.error("[k2_motor] Bestätigungscode ungültig, abgelaufen oder für "
                             "einen anderen Befehl – zuerst ohne CONFIRM aufrufen (Plan), "
                             "dann innerhalb von %.0f s mit dem neuen Code – nichts "
                             "gesendet" % CAL_TOKEN_S)

    def _cal_m84(self, ctl):
        """Alle Stepper aus (EN = 1, Homing gelöscht), dann kurz warten."""
        self._wait_moves()
        self.gcode.run_script_from_command("M84")
        self._wait_moves()
        ctl.pause(0.5)

    def _cal_frisch(self, gcmd, ctl, motor, zweck):
        """Frischer Snapshot + Sicherungsdatei -> (snap, dateiname)."""
        try:
            snap = ctl.cal_snapshot(motor)
        except K2MotorError as exc:
            raise gcmd.error("[k2_motor] %s: Lesen gescheitert (%s) – nichts gesendet"
                             % (zweck, exc))
        jetzt = time.localtime()
        snap["zeit"] = time.strftime("%Y-%m-%d %H:%M:%S", jetzt)
        snap["zeit_datei"] = time.strftime("%Y%m%d-%H%M%S", jetzt)
        snap["zweck"] = zweck
        try:
            pfad = cal_sicherung_schreiben(self._cal_verz(), snap)
        except OSError as exc:
            raise gcmd.error("[k2_motor] %s: Sicherung nicht geschrieben (%s) – nichts "
                             "gesendet" % (zweck, exc))
        return snap, os.path.basename(pfad)

    @staticmethod
    def _cal_ist_sendeschutz(exc):
        return type(exc).__name__ in ("TxGuardError", "_LocalTxGuardError")

    def cmd_cal_run(self, gcmd):
        """KTWO_MOTOR_CAL_RUN MOTOR=<m> MODE=ELEC|ENCODER AUSGEKUPPELT=1 [CONFIRM=<code>]
        Ohne CONFIRM: nur lesen, Plan + Vorbedingungen + Code. Mit Code: M84,
        frische Sicherung, alle Vorbedingungen, GENAU EIN Kalibrier-Frame,
        Prüfung danach. BEWEGT den Motor open-loop ohne Schutz, kein Abbruch."""
        motor = self._cal_ein_motor(gcmd)
        mode = str(gcmd.get("MODE", "ELEC")).strip().upper()
        if mode not in CAL_MODES:
            raise gcmd.error("[k2_motor] MODE=%s ungültig (ELEC oder ENCODER)" % mode)
        if self._cal_homing_offen(gcmd, "Kalibrierlauf"):
            return
        if gcmd.get_int("AUSGEKUPPELT", 0) != 1:
            raise gcmd.error("[k2_motor] AUSGEKUPPELT=1 fehlt – die Achse muss mechanisch "
                             "frei sein (X/Y Riemen ab, Z Spindel getrennt, Extruder ohne "
                             "Filament) – nichts gesendet")
        ctl = self._cal_schreib_sperre(gcmd, motor, "Kalibrierlauf", bewegt=True)
        if mode == "ENCODER" and not self.cfg.get("allow_encoder_calibration"):
            raise gcmd.error("[k2_motor] MODE=ENCODER gesperrt: allow_encoder_calibration="
                             "False – nichts gesendet")
        status = self._cal_status(motor)
        if status in ("laeuft", "gesperrt_unbekannt", "phase_verdacht"):
            raise gcmd.error("[k2_motor] Kalibrierlauf %s gesperrt: %s – nichts gesendet"
                             % (motor, CAL_STATUS_TEXT[status]))
        if status == "elec_pflicht" and mode != "ELEC":
            raise gcmd.error("[k2_motor] %s: %s – nichts gesendet"
                             % (motor, CAL_STATUS_TEXT[status]))
        fun, t_max, _n, umfang = CAL_MODES[mode]
        config = self.cfg.get("params", {}).get(motor, {})
        tok = str(gcmd.get("CONFIRM", "")).strip()
        if not tok:
            try:
                snap = ctl.cal_snapshot(motor)
            except K2MotorError as exc:
                raise gcmd.error("[k2_motor] Plan %s: Lesen gescheitert (%s)" % (motor, exc))
            probleme, hinweise = cal_run_vorbedingungen(snap, config, en_pruefen=False)
            gcmd.respond_info(
                "[k2_motor] PLAN Kalibrierlauf %s (%s), MODE=%s: ein Frame 0x%02X [00], "
                "Antwort nach bis zu %.0f s. Bewegung: %s. Während des Laufs KEIN Abbruch "
                "über Bus oder EN – Not-Aus nur Netzschalter. Ablauf: M84 (alle Stepper aus, "
                "Homing weg) → frische Sicherung → Vorbedingungen → Lauf → Prüfung."
                % (motor, snap["adresse"], mode, fun, t_max, umfang))
            gcmd.respond_info("[k2_motor]   jetzt: %s" % cal_kurztext(snap))
            for t in hinweise:
                gcmd.respond_info("[k2_motor]   Hinweis: %s" % t)
            if mode == "ENCODER":
                gcmd.respond_info("[k2_motor]   Hinweis: danach Drucker aus/ein und MODE=ELEC "
                                  "(Pflicht)")
            if probleme:
                for t in probleme:
                    gcmd.respond_info("[k2_motor]   NICHT erfüllt: %s" % t)
                raise gcmd.error("[k2_motor] Kalibrierlauf %s jetzt nicht möglich (%d Punkte) "
                                 "– nichts gesendet" % (motor, len(probleme)))
            code = self._cal_token_neu(motor, "RUN:" + mode)
            gcmd.respond_info("[k2_motor] Ausführen (innerhalb %.0f s): KTWO_MOTOR_CAL_RUN "
                              "MOTOR=%s MODE=%s AUSGEKUPPELT=1 CONFIRM=%s"
                              % (CAL_TOKEN_S, motor, mode, code))
            return
        self._cal_token_pruefen(gcmd, motor, "RUN:" + mode, tok)
        self._cal_m84(ctl)
        snap, sicherung = self._cal_frisch(gcmd, ctl, motor, "vor KTWO_MOTOR_CAL_RUN MODE=%s" % mode)
        probleme, hinweise = cal_run_vorbedingungen(snap, config, en_pruefen=True)
        if probleme:
            for t in probleme:
                gcmd.respond_info("[k2_motor]   NICHT erfüllt: %s" % t)
            raise gcmd.error("[k2_motor] Kalibrierlauf %s abgebrochen vor dem Senden (%d "
                             "Punkte) – nichts gesendet; Sicherung %s" % (motor, len(probleme), sicherung))
        for t in hinweise:
            gcmd.respond_info("[k2_motor]   Hinweis: %s" % t)
        lauf_ende = time.time() + t_max + CAL_LAUF_NACHLAUF_S
        self._cal_status_setzen(motor, "laeuft", modus=mode, sicherung=sicherung,
                                laeuft_bis=lauf_ende)
        gcmd.respond_info("[k2_motor] LÄUFT: %s %s – bis zu %.0f s, kein Abbruch möglich "
                          "(Not-Aus nur Netzschalter). Sicherung: %s"
                          % (motor, mode, t_max, sicherung))
        try:
            antwort, dauer = ctl.cal_lauf_senden(motor, mode, self._cal_runner(motor))
        except Exception as exc:
            if self._cal_ist_sendeschutz(exc):
                self._cal_status_setzen(motor, status)
                raise gcmd.error("[k2_motor] Sendeschutz hat den Frame abgelehnt (%s) – "
                                 "nichts gesendet" % exc)
            self._cal_status_setzen(motor, "gesperrt_unbekannt", modus=mode, sicherung=sicherung,
                                    laeuft_bis=max(lauf_ende, time.time() + CAL_LAUF_NACHLAUF_S))
            raise gcmd.error(
                "[k2_motor] Kalibrierlauf %s: keine gültige Antwort (%s) – NICHT erneut "
                "senden. Zustand unbekannt: nach %.0f s Drucker aus/ein, dann "
                "KTWO_MOTOR_CAL_CHECK MOTOR=%s und KTWO_MOTOR_CAL_ENABLE MOTOR=%s "
                "EINGEKUPPELT=1 STROMLOS=1 (bei ALARM KTWO_MOTOR_CAL_RESTORE)"
                % (motor, exc, CAL_LAUF_NACHLAUF_S, motor, motor))
        ctl.pause(0.2)
        try:
            nach = ctl.cal_snapshot(motor)
        except K2MotorError as exc:
            self._cal_status_setzen(motor, "gesperrt_unbekannt", modus=mode, sicherung=sicherung,
                                    laeuft_bis=time.time())
            raise gcmd.error("[k2_motor] Kalibrierlauf %s: Antwort da, Prüfung danach "
                             "gescheitert (%s) – Zustand unbekannt" % (motor, exc))
        alarme, warn, info, neu = cal_nach_lauf(snap, nach, mode, antwort, motor)
        self._cal_status_setzen(motor, neu, modus=mode, sicherung=sicherung)
        jetzt = time.localtime()
        name = "%s-%s-lauf.json" % (motor, time.strftime("%Y%m%d-%H%M%S", jetzt))
        try:
            cal_ergebnis_schreiben(self._cal_verz(), name, {
                "format": "k2-motor-cal-lauf/1", "motor": motor, "modus": mode,
                "zeit": time.strftime("%Y-%m-%d %H:%M:%S", jetzt), "dauer_s": round(dauer, 2),
                "antwort": antwort, "sicherung": sicherung, "vorher": snap, "nachher": nach,
                "alarme": alarme, "warnungen": warn, "infos": info, "status": neu})
        except OSError as exc:
            warn.append("Ergebnisdatei nicht geschrieben: %s" % exc)
        gcmd.respond_info("[k2_motor] Kalibrierlauf %s %s fertig nach %.1f s: Antwort %s"
                          % (motor, mode, dauer, antwort["raw"]))
        gcmd.respond_info("[k2_motor]   danach: %s" % cal_kurztext(nach))
        for t in alarme:
            gcmd.respond_info("[k2_motor]   ALARM: %s" % t)
        for t in warn:
            gcmd.respond_info("[k2_motor]   Warnung: %s" % t)
        for t in info:
            gcmd.respond_info("[k2_motor]   %s" % t)
        gcmd.respond_info("[k2_motor]   Zustand %s: %s; Ergebnis %s"
                          % (motor, CAL_STATUS_TEXT[neu], name))
        if alarme:
            raise gcmd.error("[k2_motor] Kalibrierlauf %s: ALARM – Motor gesperrt, "
                             "KTWO_MOTOR_CAL_RESTORE MOTOR=%s BACKUP=%s" % (motor, motor, sicherung))

    def cmd_cal_restore(self, gcmd):
        """KTWO_MOTOR_CAL_RESTORE MOTOR=<m> BACKUP=<datei> [CONFIRM=<code>] –
        phase_order_invert/elec_offset/elec_offset_err_deg der Sicherung
        zurück in RAM und Flash (0x06 [01] + 0x07 [03 00]). Ohne CONFIRM nur
        Plan. Keine Bewegung; der Werks-Record bleibt unverändert."""
        motor = self._cal_ein_motor(gcmd)
        datei = str(gcmd.get("BACKUP", "")).strip()
        if not datei:
            raise gcmd.error("[k2_motor] BACKUP=<datei> fehlt (Sicherung aus %s)" % self._cal_verz())
        if self._cal_homing_offen(gcmd, "Rückschreiben"):
            return
        ctl = self._cal_schreib_sperre(gcmd, motor, "Rückschreiben", bewegt=False)
        z = self._cal_zustand()["motoren"].get(motor) or {}
        if z.get("status") == "gesperrt_unbekannt" and float(z.get("laeuft_bis") or 0) > time.time():
            raise gcmd.error("[k2_motor] Rückschreiben %s gesperrt: Lauf kann noch laufen" % motor)
        if z.get("status") == "elec_pflicht":
            raise gcmd.error("[k2_motor] Rückschreiben %s gesperrt: %s – der alte elec_offset "
                             "passt nach einer Encoder-Kalibrierung nicht mehr"
                             % (motor, CAL_STATUS_TEXT["elec_pflicht"]))
        nach_encoder = z.get("modus") == "ENCODER"
        try:
            sich = cal_sicherung_laden(self._cal_verz(), datei)
        except (OSError, ValueError) as exc:
            raise gcmd.error("[k2_motor] Sicherung nicht lesbar: %s" % exc)
        if sich.get("motor") != motor:
            raise gcmd.error("[k2_motor] %s gehört zu Motor %s, nicht %s" % (datei, sich.get("motor"), motor))
        if not (sich.get("vollstaendig") and sich.get("plausibel") and sich.get("uuid")):
            raise gcmd.error("[k2_motor] %s ist unvollständig, unplausibel oder ohne UUID – "
                             "für RESTORE nicht verwendbar" % datei)
        for pid in (10, 9, 25):
            e = sich["kalibrierung"].get(str(pid)) or {}
            if not e.get("flash_raw") or e.get("ram_raw") != e.get("flash_raw"):
                raise gcmd.error("[k2_motor] %s: ID %d in der Sicherung RAM ≠ Flash – nicht "
                                 "eindeutig, kein RESTORE" % (datei, pid))
        config = self.cfg.get("params", {}).get(motor, {})
        tok = str(gcmd.get("CONFIRM", "")).strip()
        if not tok:
            try:
                snap = ctl.cal_snapshot(motor)
            except K2MotorError as exc:
                raise gcmd.error("[k2_motor] Plan %s: Lesen gescheitert (%s)" % (motor, exc))
            if snap.get("uuid") != sich.get("uuid"):
                raise gcmd.error("[k2_motor] %s stammt von einer anderen Motor-Einheit – kein "
                                 "RESTORE" % datei)
            probleme, hinweise = cal_run_vorbedingungen(snap, config, en_pruefen=False, art="restore")
            gcmd.respond_info("[k2_motor] PLAN Rückschreiben %s aus %s (%s): M84, frische "
                              "Sicherung, dann ID 10/9/25 in den RAM und 0x07 [03 00] = ALLE "
                              "RAM-Werte in den Flash. Keine Bewegung."
                              % (motor, datei, sich.get("zeit", "?")))
            for pid in (10, 9, 25):
                a, j = sich["kalibrierung"][str(pid)], snap["kalibrierung"][str(pid)]
                gcmd.respond_info("[k2_motor]   %s: jetzt RAM %s / Flash %s → %s"
                                  % (a["name"], _cal_zahl(j["ram"]), _cal_zahl(j["flash"]),
                                     _cal_zahl(a["flash"])))
            for t in hinweise:
                gcmd.respond_info("[k2_motor]   Hinweis: %s" % t)
            if probleme:
                for t in probleme:
                    gcmd.respond_info("[k2_motor]   NICHT erfüllt: %s" % t)
                raise gcmd.error("[k2_motor] Rückschreiben %s jetzt nicht möglich – nichts "
                                 "gesendet" % motor)
            code = self._cal_token_neu(motor, "RESTORE:" + datei)
            gcmd.respond_info("[k2_motor] Ausführen (innerhalb %.0f s): KTWO_MOTOR_CAL_RESTORE "
                              "MOTOR=%s BACKUP=%s CONFIRM=%s" % (CAL_TOKEN_S, motor, datei, code))
            return
        self._cal_token_pruefen(gcmd, motor, "RESTORE:" + datei, tok)
        self._cal_m84(ctl)
        snap, vorher = self._cal_frisch(gcmd, ctl, motor, "vor KTWO_MOTOR_CAL_RESTORE")
        if snap.get("uuid") != sich.get("uuid"):
            raise gcmd.error("[k2_motor] %s stammt von einer anderen Motor-Einheit – nichts "
                             "gesendet" % datei)
        probleme, hinweise = cal_run_vorbedingungen(snap, config, en_pruefen=True, art="restore")
        if probleme:
            for t in probleme:
                gcmd.respond_info("[k2_motor]   NICHT erfüllt: %s" % t)
            raise gcmd.error("[k2_motor] Rückschreiben %s abgebrochen vor dem Senden – nichts "
                             "gesendet" % motor)
        try:
            log = ctl.cal_werte_zurueckschreiben(motor, sich, self._cal_runner(motor))
        except Exception as exc:
            if not self._cal_ist_sendeschutz(exc):
                self._cal_status_setzen(motor, "phase_verdacht", sicherung=datei)
            raise gcmd.error("[k2_motor] Rückschreiben %s gescheitert: %s – Zustand bleibt "
                             "gesperrt; Sicherung vom Stand davor: %s" % (motor, exc, vorher))
        for t in log:
            gcmd.respond_info("[k2_motor]   %s" % t)
        try:
            nach = ctl.cal_snapshot(motor)
        except K2MotorError as exc:
            raise gcmd.error("[k2_motor] Rückschreiben %s: Prüfung danach gescheitert (%s)"
                             % (motor, exc))
        alarme, warn, info = cal_vergleich(nach, sich, config)
        neu = "phase_verdacht" if alarme else "elec_pflicht" if nach_encoder else (
            "z_ausrichtung_noetig" if motor in ("z", "z1") else "homing_noetig")
        self._cal_status_setzen(motor, neu, sicherung=datei,
                                **({"modus": "ENCODER"} if nach_encoder else {}))
        jetzt = time.localtime()
        name = "%s-%s-restore.json" % (motor, time.strftime("%Y%m%d-%H%M%S", jetzt))
        try:
            cal_ergebnis_schreiben(self._cal_verz(), name, {
                "format": "k2-motor-cal-restore/1", "motor": motor, "quelle": datei,
                "zeit": time.strftime("%Y-%m-%d %H:%M:%S", jetzt), "vorher": snap,
                "nachher": nach, "log": log, "alarme": alarme, "warnungen": warn,
                "infos": info, "status": neu})
        except OSError as exc:
            warn.append("Ergebnisdatei nicht geschrieben: %s" % exc)
        gcmd.respond_info("[k2_motor] Rückschreiben %s fertig: %s" % (motor, cal_kurztext(nach)))
        for t in alarme:
            gcmd.respond_info("[k2_motor]   ALARM: %s" % t)
        for t in warn:
            gcmd.respond_info("[k2_motor]   Warnung: %s" % t)
        for t in info:
            gcmd.respond_info("[k2_motor]   %s" % t)
        gcmd.respond_info("[k2_motor]   Zustand %s: %s" % (motor, CAL_STATUS_TEXT[neu]))
        if alarme:
            raise gcmd.error("[k2_motor] Rückschreiben %s: ALARM – Motor bleibt gesperrt" % motor)

    def cmd_cal_enable(self, gcmd):
        """KTWO_MOTOR_CAL_ENABLE MOTOR=<m> EINGEKUPPELT=1 [STROMLOS=1] – Motor nach
        Kalibrierlauf/Rückschreiben wieder freigeben: liest und prüft (nichts
        Schreibendes an den Motor), legt eine neue Referenz-Sicherung an und
        löscht den Kalibrier-Zustand. Die Regelung geht beim nächsten Stepper-
        Enable (G28 bzw. erstes Extrudieren) über die EN-Flanke an."""
        motor = self._cal_ein_motor(gcmd)
        if self._cal_homing_offen(gcmd, "Freigabe"):
            return
        z = self._cal_zustand()["motoren"].get(motor) or {}
        status = z.get("status")
        if status is None:
            gcmd.respond_info("[k2_motor] %s: kein offener Kalibrier-Zustand – nichts zu tun"
                              % motor)
            return
        if gcmd.get_int("EINGEKUPPELT", 0) != 1:
            raise gcmd.error("[k2_motor] EINGEKUPPELT=1 fehlt – erst Riemen/Spindel wieder "
                             "montieren bzw. Filament einlegen")
        if status in ("laeuft", "phase_verdacht", "elec_pflicht"):
            raise gcmd.error("[k2_motor] Freigabe %s nicht möglich: %s"
                             % (motor, CAL_STATUS_TEXT[status]))
        if status == "gesperrt_unbekannt":
            if float(z.get("laeuft_bis") or 0) > time.time():
                raise gcmd.error("[k2_motor] Freigabe %s: Lauf kann noch laufen" % motor)
            if gcmd.get_int("STROMLOS", 0) != 1:
                raise gcmd.error("[k2_motor] Freigabe %s: Zustand unbekannt – Drucker einmal "
                                 "aus/ein, dann STROMLOS=1" % motor)
        why = self._cal_sperre()
        if why:
            raise gcmd.error("[k2_motor] Freigabe gesperrt: %s" % why)
        ctl = self._ctl_or_error(gcmd)
        if not getattr(ctl, "is_ready", False):
            raise gcmd.error("[k2_motor] Freigabe gesperrt: Motor-Init nicht abgeschlossen")
        try:
            snap = ctl.cal_snapshot(motor)
        except K2MotorError as exc:
            raise gcmd.error("[k2_motor] Freigabe %s: Lesen gescheitert (%s)" % (motor, exc))
        if not snap["vollstaendig"] or not snap["plausibel"]:
            raise gcmd.error("[k2_motor] Freigabe %s: Werte unvollständig/unplausibel – %s"
                             % (motor, "; ".join(snap["plausi_gruende"] + snap["fehler"][:2])))
        if not (snap.get("schutz") or {}).get("ok"):
            raise gcmd.error("[k2_motor] Freigabe %s: Fehlerbits gesetzt (%s) – Ursache klären, "
                             "KTWO_MOTOR_CLEAR MOTOR=%s" % (motor, (snap.get("schutz") or {}).get("fehler"), motor))
        if status == "gesperrt_unbekannt" and z.get("sicherung"):
            try:
                alt = cal_sicherung_laden(self._cal_verz(), z["sicherung"])
            except (OSError, ValueError) as exc:
                raise gcmd.error("[k2_motor] Freigabe %s: Sicherung %s nicht lesbar (%s)"
                                 % (motor, z["sicherung"], exc))
            alarme, _w, _i = cal_vergleich(snap, alt, self.cfg.get("params", {}).get(motor, {}))
            if alarme:
                self._cal_status_setzen(motor, "phase_verdacht", sicherung=z["sicherung"])
                raise gcmd.error("[k2_motor] Freigabe %s: ALARM %s – KTWO_MOTOR_CAL_RESTORE "
                                 "MOTOR=%s BACKUP=%s" % (motor, "; ".join(alarme), motor, z["sicherung"]))
        jetzt = time.localtime()
        snap["zeit"] = time.strftime("%Y-%m-%d %H:%M:%S", jetzt)
        snap["zeit_datei"] = time.strftime("%Y%m%d-%H%M%S", jetzt)
        snap["zweck"] = "Referenz nach KTWO_MOTOR_CAL_ENABLE"
        try:
            pfad = cal_sicherung_schreiben(self._cal_verz(), snap)
        except OSError as exc:
            raise gcmd.error("[k2_motor] Freigabe %s: Referenz-Sicherung nicht geschrieben (%s)"
                             % (motor, exc))
        if status == "gesperrt_unbekannt" and z.get("modus") == "ENCODER":
            # Encoder kann kalibriert worden sein -> elec_offset neu bestimmen
            self._cal_status_setzen(motor, "elec_pflicht", modus="ENCODER",
                                    sicherung=z.get("sicherung"))
            gcmd.respond_info("[k2_motor] %s: Werte plausibel, aber der unbekannte Lauf war "
                              "eine Encoder-Kalibrierung – %s (Referenz: %s)"
                              % (motor, CAL_STATUS_TEXT["elec_pflicht"], os.path.basename(pfad)))
            return
        self._cal_status_setzen(motor, None)
        gcmd.respond_info("[k2_motor] %s freigegeben: %s" % (motor, cal_kurztext(snap)))
        gcmd.respond_info("[k2_motor]   neue Referenz: %s" % os.path.basename(pfad))
        gcmd.respond_info(
            "[k2_motor]   Nächste Schritte: FIRMWARE_RESTART (Motor-Init setzt die Config-Werte "
            "neu in den RAM), dann %s; danach KTWO_MOTOR_CAL_CHECK MOTOR=%s – erwartet EN=0 "
            "Regler=1 PWM=1.%s" % (
                "erstes Extrudieren (Regelung geht mit der EN-Flanke an)" if motor == "e"
                else "G28 (Homing-Bezug war ungültig)", motor,
                " Danach Z ausrichten und Bettnetz neu." if status == "z_ausrichtung_noetig" else ""))

    def get_status(self, eventtime=None):
        ctl = self.controller
        last = (ctl.cut_cal_last if ctl is not None else None) or {}
        return {
            "allow_write": bool(self.cfg.get("allow_write")),
            "cal_status": dict(getattr(self, "_cal_cache", {}) or {}),
            "protect_en": dict(getattr(ctl, "protect_en", {}) or {}) if ctl is not None else {},
            # Motor-Init fertig (is_ready) + Ergebnis des letzten Inits
            "ready": bool(getattr(ctl, "is_ready", False)) if ctl is not None else False,
            "init_status": self.init_status,
            "homing_pos_kp": float(self.cfg.get("homing_pos_kp", HOMING_POS_KP_DEFAULT)),
            "homing_active": bool(ctl.homing_active) if ctl is not None else False,
            "stall_mode": dict(ctl.stall_mode) if ctl is not None
            else {m: None for m in STALL_READ_MOTORS},
            "last_error": ctl.last_error if ctl is not None else None,
            "stall_monitor": self.stall.status(),
            "cut_cal": {
                "active": bool(ctl.cut_cal_active) if ctl is not None else False,
                "result": last.get("result"),
                "first_hit": last.get("first_hit"),
                "second_hit": last.get("second_hit"),
                "probes": last.get("probes", 0),
                "saved": bool(last.get("saved", False)),
                "error": last.get("error"),
            },
        }


def load_config(config):
    """Klipper-Einstieg [k2_motor] (Upstream-Muster)."""
    return PrinterK2Motor(config)
