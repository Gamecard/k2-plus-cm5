#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_prtouch.py – Klipper-Zusatzmodul [k2_prtouch] für PrTouch v3 (K2 Plus).

Offener Ersatz für Crealitys ``prtouch_v3_wrapper.so`` (Stock, armhf,
proprietär, Cython) auf einem Pi-Host unter UPSTREAM-Klipper – mit der
UNVERÄNDERTEN Creality-MCU-Firmware (Main GD32F303RET6 + Düse GD32F303CBT6,
Basis 0x08003000). Nutzt den EINGEBAUTEN Kraftsensor (Düse als Taster).

Grundlagen (nur gelesen, nichts gesendet):
- Analyse von prtouch_v3_wrapper.so (Architektur, Live-Mitschnitt 99 Tastungen,
  Host-Rechenweg best_indx→step_bst_indx→zoft, Stopp-Weg, Basis 0x08003000)
- statische Analyse des Stopp-Wegs (Ergebnis: MCU stoppt NICHT
  selbst; Swap = nur Zeitsync Düse HIGH / Main liest einmal; Host bricht ab;
  kein trsync/endstop_home/stepper_stop in prtouch-Pfaden)
- Bit-Spezifikation aus der Protokollanalyse (Befehle/Formate/Konstanten,
  Minimal-Variante; Befehlsformate/Dictionary-Inhalte/Live-Werte)
- Live-Gegenprüfung (Live-Rechenweg, Pseudocode, Fenster
  PRES_N=64/STEP_N=64, SPEED=5mm/s, HUB=5mm, Lüfter 0.6, TEST_SWAP,
  can_brk-Ausreißer T58→T59 versetzt ±0.25mm)
- Host-Analyse (Host-Module, G-Codes, TEST_SWAP/PRES_CHECK,
  Wellen SRC/FTR/CAL; „apax nicht im Normalpfad“ gilt nur für AUTO_APAX/
  WAVE_APAX – Stock-PRES_CHECK bei G28 Z nutzt den apax-Strom, Korrektur
  17.10.2026)
- MCU-Dictionaries mcu-main-F008-GD32F303RET6.dict.json +
  noz-duese-F008-GD32F303CBT6.dict.json (exakte Formatstrings inkl.
  Creality-Schreibweisen „aqc_tick“, „resault_*“)
- Stock-printer.cfg des Druckers ([prtouch_v3] Pins/Schwellen,
  [bed_mesh] 9x9, [stepper_z] 0.0025mm/Microstep)
- Live-Mitschnitt Auto-Nivellierung (99 Tastungen,
  PRES_TIME 64×-0.0498..0.0, STEP_TIME 64×-0.0244..0.0, best 53-60,
  zoft 0.010-0.040, tri_time immer 0.000)

Stil wie pi-host/k2_motor_init.py und k2_box.py:
- Reine Logik ohne Klipper testbar (K2PrtouchController + pure Helfer).
- MCU-/Toolhead-Zugriffe gekapselt (PrtouchMcuOps / ToolheadOps;
  Klippy-Adapter nur in der Hülle, Fakes nur in den Tests).
- Bewegung nur mit Freigabe (allow_move, Default False).

HAUPTWEG (mode: probe, Default, Stand 16.10.2026 abends):
  [k2_prtouch] registriert sich als Upstream-Probe (Objekt 'probe',
  Pseudo-Pin probe:z_virtual_endstop). Stopp über einen normalen
  MCU_endstop auf step_swap_pin (Default ^!PC7, Main-MCU) mit trsync – wie
  Stock (BELEGT: Emulation + Fingerdruck-Test 19/19). Vor jeder Fahrt:
  Signalweg-Test (sta_swap 0/1 über den Endstop), Stock-probe_prepare
  (cont_prtouch_step -> start_prtouch_pres mit ack-Prüfung ->
  start_prtouch_step), Ruhetest, PC7 nicht ausgelöst; nach jeder Fahrt
  (auch bei Ausnahme): stop_prtouch_step + stop_prtouch_pres sta_swap=1.
  Damit gehen PROBE, QUERY_PROBE, PROBE_ACCURACY, PROBE_CALIBRATE,
  G28 Z (endstop_pin: probe:z_virtual_endstop), BED_MESH_CALIBRATE,
  Z_TILT_ADJUST usw. Geprüft gegen Upstream-Klipper (Commit s. README).
  Klassen: K2PrtouchProbeCore (rein, testbar), K2PrtouchPrinterProbe /
  K2PrtouchEndstopWrapper / K2DescendHelper / K2ProbeOffsets (Hülle).

RÜCKFALL (mode: steps bzw. KTWO_PRTOUCH_PROBE_STEPS): zeitgesteuerte
Schrittfahrt K2PrtouchController.probe_point mit hostseitiger Auswertung.

Sicherheit (verbindlich, im Code durchgesetzt + getestet):
  S0 Probe-Hauptweg: Fahrt nur mit allow_move: True, Antastgeschwindigkeit
     hart <= 5 mm/s (auch PROBE_SPEED=/G28-homing_speed), Weg außerhalb G28
     <= probe_max_travel_mm (Default 8, max 10), Entschärfen bei jeder
     Ausnahme, Abbruch VOR der Fahrt bei ack err != 0, Signalweg-/Ruhetest-
     Fehler oder schon ausgelöstem Endstop.
     Seit 17.10.2026 (Analyse der Düsen-Firmware): Config-Prüfung der Antast-
     Parameter (min_hold != max_hold, ned_hftr != 0, ned_lftr, lmt_dead >=
     64, cfg_regs 60/56/44, …), arm_settle_s >= 1,5 × Totzeit, Mindest-
     Antastgeschwindigkeit (ANNAHME 2,5 mm/s bei Stock-Schwellen), Ruhetest
     mit Tickabstand/-1/24 Bit/gefiltertem Kanal, Blocklesen idx += n,
     Endstop-Pin '^!PC7' (Pull-Up wie Stock).
  S1 MCU stoppt nicht selbst → Abwärtsfahrt NUR in kurzen Schritten
     (step_mm Default 0.01 seit 18.10.2026, vorher 0.02; Maximum 0.05)
     mit niedriger Geschwindigkeit
     (Default 5mm/s, Maximum 5mm/s), nach JEDEM Schritt wait_moves
     (M400-Äquivalent), dann lesen/auswerten, bei Kontakt SOFORT stoppen
     und anheben. (Gilt für den Rückfall-Schrittweg.)
  S2 Harte Grenzen: max_travel_mm pro Antastung (Default 5mm Stock-Hub),
     Kraft-Notgrenze (Roh-Abweichung vom Nullpunkt > emergency_thresh,
     Default 27500 > Live-Stock-Spitze 21 815, max 40000, Live-Abgleich
     A7 + E1 → sofort Abbruch + Fehler), Timeout; bei jedem Fehler: Sampling
     stoppen, Z anheben (z_lift_mm), klare Meldung, kein Weiterprobe (außer
     genau einmaliger can_brk-Wiederholung versetzt – seit 18.10.2026 NUR im
     Mesh-Kontext wie Stock, can_brk_scope, Live-Abgleich A8).
  S3 Bewegung nur mit allow_move: True (Default False → nur
     Selbsttest/TEST_SWAP/Sensor lesen, keine Z-Bewegung);
     G-Code KTWO_PRTOUCH_PROBE_STEPS [DRY_RUN=1].
  S4 Vor jeder Antastung TEST_SWAP-Äquivalent + Sensor-Ruhetest
     (Rauschen/Drift, analog PRES_CHECK); sonst Abbruch VOR Bewegung.
  S5 Temperaturen (Düse 140/Bett 50) nur als Empfehlung prüfen (Warnung),
     nicht selbst heizen. Zusätzlich (Live-Abgleich E2) Warnung beim
     Scharfschalten, wenn die Düse > temp_nozzle_warn + hot_probe_margin
     ist (hot_probe_warn, Default an, nur Meldung, nie Abbruch).

G-Codes (PrinterK2Prtouch):
- mode probe: Upstream-Befehle (PROBE, QUERY_PROBE, PROBE_ACCURACY,
  PROBE_CALIBRATE, Z_OFFSET_APPLY_PROBE) + G28 Z/BED_MESH_CALIBRATE/… .
- KTWO_PRTOUCH_PROBE_STEPS [X=.. Y=.. DRY_RUN=1 CONTEXT=single|home|mesh]
  – Rückfall-Einzelantastung (beide Modi; can_brk nur bei CONTEXT=mesh).
- nur mode steps: KTWO_PRTOUCH_HOME_Z [DRY_RUN=1], KTWO_PRTOUCH_MESH
  [COUNT=.. DRY_RUN=1] (eigene Ersatzbefehle wie bisher).
- KTWO_PRTOUCH_TEST_SWAP – Sync-/Versionscheck ohne Bewegung.
- KTWO_PRTOUCH_READ – Sensor lesen ohne Bewegung.
- KTWO_PRTOUCH_STATUS – letzter Status (auch Moonraker via get_status).
- KTWO_PRTOUCH_CHECK [DURATION=s | EXTRUDE=mm SPEED=mm/s | MACRO=name]
  [REQUIRE_DIF=0] [STOP=1] – optionaler Sensor-Selbsttest über den apax-
  Strom (Ersatz Stock-PRES_CHECK), NUR mit pres_check: True (Default False:
  kein config_prtouch_apax, kein Befehl). Gegensperre arm() <-> apax,
  stop_prtouch_apax immer im finally, Fehlschlag = command_error (kein
  Shutdown). Buslast ≈ 12,4 kB/s (≈ 54 % der Düsenleitung).

ANNAHMEN sind im Code mit „ANNAHME:“ markiert und in README-k2_prtouch.md
(A.5) gelistet.

Konsole (23.10.2026, k2_konsole): Connect-Fehler (Query-Lookup, apax-Lookup)
als ``!! [k2_prtouch] …`` bzw. ``// …`` kurz nach „ready“ – vorher beim Start
verloren. Die Temperatur-/Notfall-Config-Warnungen des Rückfall-Antastens
(K2PrtouchController) liefen über ``logger=gcode`` ins Leere (gcode hat kein
``info``) und erscheinen jetzt als ``// [k2_prtouch] Warnung: …``.
"""

import math
import os
import statistics
import struct
import sys
import time

try:
    from . import k2_konsole as _K2K
except ImportError:
    import k2_konsole as _K2K

# =====================================================================
# Exakte MCU-Formatstrings (BELEGT via Dictionaries, inkl. Tippfehler)
# =====================================================================
# Main-Dict mcu-main-F008-GD32F303RET6.dict.json + Düsen-Dict identisch:
#   commands: config_prtouch_* (49/45/40), start_* (48/44/39),
#             read_*/cont_*/stop_* (46/42/41/47/43/38)
#   responses: ack_prtouch (95), resault_prtouch_pres/step/step_cnt/apax
#              (-30/-31/-32/94)
# Schreibweisen „aqc_tick“ und „resault_*“ sind Firmware-Realität und
# werden exakt übernommen (Bit-Spezifikation, Stopp-Analyse).

CMD_CONFIG_PRES = ("config_prtouch_pres oid=%c idx=%c swp_pin=%u "
                   "clk_pin=%u sdo_pin=%u")
CMD_CONFIG_STEP = ("config_prtouch_step oid=%c oid_xstp=%u oid_ystp=%u "
                   "oid_zstp=%u swp_pin=%u")
CMD_CONFIG_APAX = "config_prtouch_apax oid=%c oid_estp=%c"
CMD_START_PRES = ("start_prtouch_pres oid=%c cfg_regs=%c acq_tick=%u "
                  "ned_tftr=%c ned_hftr=%c ned_lftr=%u "
                  "min_hold=%i max_hold=%i add_hold=%i lmt_dead=%u")
CMD_START_STEP = "start_prtouch_step oid=%c aqc_tick=%u"
CMD_START_APAX = "start_prtouch_apax oid=%c cfg_regs=%c acq_tick=%u"
CMD_READ_PRES = "read_prtouch_pres oid=%c is_src=%c ch=%c idx=%c len=%c"
CMD_READ_STEP = "read_prtouch_step oid=%c idx=%c len=%c"
CMD_CONT_STEP = "cont_prtouch_step oid=%c"
CMD_STOP_PRES = "stop_prtouch_pres oid=%c sta_swap=%c"
CMD_STOP_STEP = "stop_prtouch_step oid=%c"
CMD_STOP_APAX = "stop_prtouch_apax oid=%c"
RESP_ACK = "ack_prtouch oid=%c err=%c expar0=%u expar1=%u"
RESP_PRES = ("resault_prtouch_pres oid=%c tri_chxs=%c buf_len=%c ch=%c "
             "idx=%c len=%c ticks=%.*s datas=%.*s")
RESP_STEP = ("resault_prtouch_step oid=%c buf_len=%c idx=%c len=%c "
             "ticks=%.*s datas=%.*s")
RESP_STEP_CNT = "resault_prtouch_step_cnt oid=%c cnt_x=%i cnt_y=%i cnt_z=%i"
RESP_APAX = ("resault_prtouch_apax oid=%c ch=%c len=%c ticks=%.*s "
             "datas=%.*s espds=%.*s")

# Alle Befehle/Antworten als Dict zum Test (f) gegen die JSON-Dictionaries.
ALL_FORMATS = {
    "config_prtouch_pres": CMD_CONFIG_PRES,
    "config_prtouch_step": CMD_CONFIG_STEP,
    "config_prtouch_apax": CMD_CONFIG_APAX,
    "start_prtouch_pres": CMD_START_PRES,
    "start_prtouch_step": CMD_START_STEP,
    "start_prtouch_apax": CMD_START_APAX,
    "read_prtouch_pres": CMD_READ_PRES,
    "read_prtouch_step": CMD_READ_STEP,
    "cont_prtouch_step": CMD_CONT_STEP,
    "stop_prtouch_pres": CMD_STOP_PRES,
    "stop_prtouch_step": CMD_STOP_STEP,
    "stop_prtouch_apax": CMD_STOP_APAX,
    "ack_prtouch": RESP_ACK,
    "resault_prtouch_pres": RESP_PRES,
    "resault_prtouch_step": RESP_STEP,
    "resault_prtouch_step_cnt": RESP_STEP_CNT,
    "resault_prtouch_apax": RESP_APAX,
}

# =====================================================================
# Konstanten (BELEGT aus Live-Mitschnitt + Config; Lücken = ANNAHME)
# =====================================================================

CLOCK_FREQ = 120000000  # BELEGT Dict :82 + Stats freq=119998498/222
PRES_N = 64  # BELEGT PRES_TIME 64 Punkte
STEP_N = 64  # BELEGT STEP_TIME 64 Punkte
PRES_DT_S = 0.00078125  # BELEGT pres_acq_tkms 0.78125
# BELEGT 16.10.2026 (Emulation Stock-probe_prepare): step_acq_tkms
# 0.390625 ms -> aqc_tick 46875 (alte ANNAHME 0.0244s/64 bzw. 45700 FALSCH).
STEP_DT_S = 0.000390625

# Stock-Konfigwerte (Stock-printer.cfg [prtouch_v3]) und ihre Abbildung auf
# die MCU-Parameter – BELEGT 16.10.2026 per Emulation des Stock-Moduls
# (Stock-probe_prepare/home_start mitgelesen, Zuordnung durch gezielte
# Variation der Konfigwerte eindeutig). Umrechnung: stock_to_mcu_params().
STOCK_CFG_REGS = 60  # pres_cfg_regs
STOCK_PRES_ACQ_TKMS = 0.78125  # pres_acq_tkms [ms]
STOCK_STEP_ACQ_TKMS = 0.390625  # step_acq_tkms [ms]
STOCK_TRI_FTER = (5, 1, 0.8)  # pres_tri_fter
STOCK_TRI_HOLD = (4000, 10000, 500)  # pres_tri_hold
STOCK_DED_TKMS = 128.0  # pres_ded_tkms
STOCK_ACQ_TICK = 93750  # = round(0.78125/1000 * 120 MHz)
STOCK_AQC_TICK = 46875  # = round(0.390625/1000 * 120 MHz)
STOCK_NED = (5, 1000, 800)  # MCU-Werte ned_tftr/hftr/lftr (BELEGT)
STOCK_HOLDS = (4000, 10000, 500)  # MCU-Werte min/max/add_hold (BELEGT)
STOCK_LMT_DEAD = 128  # MCU-Wert lmt_dead = int(pres_ded_tkms) (BELEGT)
STOCK_SPEED = 5.0  # BELEGT STEP_DATA-Fall + liveconfig speed
STOCK_HUB = 5.0  # BELEGT horizontal_move_z + PRTOUCH_MOVE Z5.00
STOCK_FANS = 0.6  # BELEGT prth_min_fans + DISTUURB_CTL
# Lüfterdämpfung (fan_damping): Wartezeit nach dem Drosseln, bis der
# Hotend-Lüfter langsamer dreht (ANNAHME; Stock drosselt bei
# multi_probe_begin, danach folgt erst die XY-Fahrt zum Punkt).
FAN_DAMPING_SETTLE_DEFAULT_S = 1.5
FAN_DAMPING_SETTLE_MAX_S = 10.0
STOCK_TEMP_NOZZLE = 140.0  # BELEGT G29-Ablauf
STOCK_TEMP_BED = 50.0  # BELEGT G29-Ablauf
STOCK_Z_STEP_MM = 0.0025  # BELEGT rotation 8/200/16 + halt_step_dist
STOCK_VERSIONS = {"PY": 78, "PRES": 71, "STEP": 71}  # BELEGT Live-Mitschnitt
STOCK_CONTACT_THRESH = 3000  # ANNAHME: TEST_PRES_AMPLITUDE threshld 3000
# (FTR ±600 Ruhe vs -9489 Spitze) – als Kontakt-Schwelle plausibel.
# Kraft-Notgrenze (nur Host, Rückfallweg mode steps; die MCU hat keine,
# Analyse der Düsen-Firmware). Bis 18.10.2026: 15000 (= Stock pres_chk_hold, Rolle nur
# ANNAHME, Kommentar „> normale Spitze ~9500“ war FALSCH).
# Live-Abgleich 18.10.2026 A7 (35 Stock-Antastungen, R): normale
# Kontaktspitzen gefiltert 6 005 … 20 056 Counts (Bereichs-Mesh 12 698 …
# 20 056, Median ≈ 17 500) – Stock fährt diese Kräfte bei JEDEM Druck an,
# sie sind also unschädlich. 15000 läge mitten im Normalbereich.
# 18.10.2026: 25000 = Live-Maximum 20 056 + ~25 % Reserve (ANNAHME Reserve).
# Live-Abgleich 18.10.2026 E1 (77 Stock-Antastungen, 3 Drucke, R):
# neues Maximum 21 815 (G28_RE_CHECK, ≈ 140 °C) → 25000 nur noch 14,6 %
# darüber, und ein 0,01-mm-Kontaktschritt (≈ 22 700) läge ~10 % darunter.
# Neu: 27500 = 21 815 + ~26 % (gleiche Reservelogik, Reserve = ANNAHME).
# Obergrenze 40000 bleibt (≈ 1,8 × Live-Maximum; bewusst NICHT auf 2 × =
# 43 630 mitgezogen – eine höhere Obergrenze würde nur die zulässige
# Höchstkraft lockern, der Default braucht sie nicht).
EMERGENCY_THRESH_DEFAULT = 27500
EMERGENCY_THRESH_MAX = 40000
LIVE_PEAK_MAX = 21815  # BELEGT/R: größte normale Stock-Spitze (G28_RE_CHECK)
# Kraftanstieg je µm nach Kontakt, DYNAMISCH aus den Stock-Wellen (5 mm/s,
# R + ANNAHME, statische Steifigkeit am Gerät noch nicht gemessen):
# bis 18.10. 59 … 1 945, seit Live-Abgleich E1 bis 2 270 Counts/µm.
# Dient nur für die Plausibilitätswarnung „ein Kontaktschritt kann die
# Notgrenze reißen“.
LIVE_STIFF_MAX_PER_UM = 2270.0
# Reserve, unter der die Plausibilitätswarnung „knapp“ meldet (ANNAHME):
# Kontaktschritt bzw. Live-Spitze >= 90 % der Notgrenze.
EMERGENCY_TIGHT_FRAC = 0.9
# Heiß-Antastung (Live-Abgleich E2): Stock-Reinigungsfahrt bei
# 157–188 °C zeigte Störung VOR dem Kontakt bis 3 985 = 0,4 % unter
# min_hold 4000 → Fehlauslösung möglich. Pi-Ablauf tastet bei
# g28_ext_temp (140 °C = temp_nozzle_warn) an. Warnung ab Referenz + 10.
HOT_PROBE_MARGIN_DEFAULT = 10.0
HOT_PROBE_MARGIN_MAX = 100.0
STOCK_EMERGENCY_THRESH = EMERGENCY_THRESH_DEFAULT  # alter Name (Kompat.)
STOCK_REST_MAX_DIF = 3000  # ANNAHME: gleiche Schwelle für Ruhetest
REST_MAX_DIF_PROBE_DEFAULT = 16000  # Probe-Hauptweg, s. parse_options
REST_RETRIES_DEFAULT = 2  # Ruhetest-Wiederholungen im Probe-Hauptweg
REST_RETRY_PAUSE_S = 0.3  # ANNAHME: Pause zwischen Entschärfen und neuem Scharfschalten
# (analog PRES_CHECK „dynamic detection“; BELEGT pres_chk_hold Existenz).
# can_brk-Geltungsbereich + Antast-Kontexte (Live-Abgleich A8):
CAN_BRK_SCOPES = ("mesh", "all", "off")
PROBE_CONTEXTS = ("mesh", "home", "single")
STOCK_RETRY_DX = -0.25  # BELEGT T58→T59: 132.5→132.25
STOCK_RETRY_DY = 0.25  # BELEGT T58→T59: 47.5→47.75

# Host-Kontaktkriterium im Rückfallweg (mode steps), README-k2_prtouch B.2/B.3.
# 'window' (Default, bisher): Nullpunkt = Median der ersten 32 Samples des
#   JEWEILIGEN Fensters, Kontakt = rest_min_consec Samples > contact_thresh.
# 'cumulative' (optional, ANNAHME): zusätzlich (ODER) die „Treppe“ über
#   mehrere Schritte – je Schritt ein Stufenpegel (Median der jüngsten
#   CONTACT_TAIL_N Rohwerte nach M400), Kontakt, wenn mind. contact_steps
#   aufeinanderfolgende Stufen gleichsinnig um je >= contact_step_min steigen
#   und die Summe seit der letzten unbelasteten Stufe > contact_thresh ist.
#   Begründung/Herleitung: README B.3 (Treppen-Effekt B.2 Punkt 5).
CONTACT_CRITERIA = ("window", "cumulative")
CONTACT_TAIL_N = 16  # ANNAHME: 16 Samples ≈ 12,5 ms jüngster Ruhezustand
CONTACT_STEPS_DEFAULT = 2  # ANNAHME (s. README B.3)
CONTACT_STEPS_MIN, CONTACT_STEPS_MAX = 2, 10
CONTACT_STEP_MIN_DEFAULT = 250.0  # ANNAHME: Counts je Schritt (Rauschboden)

# Harte Sicherheits-Maxima (verbindlich, S1):
# step_mm (nur mode steps) seit 18.10.2026 Default 0,01 (vorher 0,02):
# Größter Kraftsprung eines Kontaktschritts ≈ step_mm × Steifigkeit; mit
# der dynamischen Live-Spitze 1 945 Counts/µm (ANNAHME) sind das bei 0,02 mm
# bis ≈ 38 900 Counts (> Notgrenze 25 000 → legitimer Kontakt endet als
# 'emergency' ohne Messwert), bei 0,01 mm ≈ 19 450 (< 25 000, ~22 % Reserve)
# und der Überfahrweg nach Kontakt halbiert sich. Live-Abgleich A7.
STEP_MM_DEFAULT = 0.01
STEP_MM_MAX = 0.05
SPEED_DEFAULT = 5.0
SPEED_MAX = 5.0
MAX_TRAVEL_MAX = 10.0  # Review 16.10.: harte Obergrenze, Stock-Hub 5 mm
MAX_TRAVEL_DEFAULT = 5.0  # Stock-Hub
# Probe-Hauptweg (mode: probe): maximaler Abwärtsweg je Antastung außerhalb
# von G28 (PROBE, BED_MESH_CALIBRATE, Z_TILT_ADJUST …). Default 8 mm deckt
# horizontal_move_z 5 + Bettwelligkeit ab; harte Grenze wie MAX_TRAVEL_MAX.
PROBE_MAX_TRAVEL_DEFAULT = 8.0
# Wartezeit nach dem Scharfschalten. Untergrenze seit 17.10.2026 an die
# Totzeit gekoppelt (min_arm_settle_s, Analyse der Düsen-Firmware):
# Stock (lmt_dead 128, 1280 Hz) -> Minimum 0,15 s; Default 0,2 s.
ARM_SETTLE_DEFAULT_S = 0.2
ARM_SETTLE_MAX_S = 2.0
MODES = ("probe", "steps")
Z_LIFT_DEFAULT = 2.0  # wie sample_retract_dist 2.0
TIMEOUT_DEFAULT_S = 30.0  # wie z_align timeout 30s

# Blocklesen read_prtouch_pres/_step (BELEGT Analyse der Düsen-Firmware,
# Düse 0x08003F60 ff.): idx = Index im 64er-SCHIEBEREGISTER (0 = ältestes,
# 63 = neuestes, kein Ring), eine Antwort endet, sobald ticks+datas > 41 Byte
# (0x0800400E, Überlauf-Sample ist mit drin) -> 7-9 Samples je Antwort; das
# Antwortfeld 'len' ist bei vollständig passendem Bereich um 1 zu groß
# (0x08004014). Deshalb: angefragt wird wie Stock len = 64 - idx, der nächste
# idx ist idx + n (n = Anzahl im gepackten Puffer) – NICHT feste 8er-Schritte
# und NICHT das len-Feld (read_ring_blocks).
RING_N = 64

# =====================================================================
# Absicherung nach Analyse der Düsen-Firmware (17.10.2026)
# =====================================================================
# Kennzeichnung: B = Beobachtung im Image (Adresse),
# F = Folgerung, ANNAHME = nicht belegt.

# CS1237-Konfigregister cfg_regs -> (Datenrate Hz, PGA). BELEGT nur diese
# drei Werte: Stock-printer.cfg:343 „60（1280/128）44（640/128）56（1280/64）“,
# Bit 5:4 Rate, 3:2 PGA, 1:0 Kanal A; live 0,7907 ms bei 60 (≈ 1/1280 Hz).
# Im Bitbang-Modus bestimmt NUR cfg_regs die Abtastrate – acq_tick ist dort
# bloß ein Ein-Schalter (B 0x08007BB6 -> 0x08007DC4).
CS1237_CFG = {60: (1280.0, 128), 56: (1280.0, 64), 44: (640.0, 128)}
# Totzeit lmt_dead: Sample-Anzahl, die Firmware hebt < 64 auf 64 an
# (B 0x08004310-0x0800431E) -> kleinere Werte lehnen wir ab.
LMT_DEAD_MIN = 64
# F (Memset aller Puffer bei start 0x080043xx + Hochpass-Fenster M[0..15]
# aus roh[20]): gefiltert[63] ist erst ab Sample 64 gültig, alle 64 Einträge
# des gefilterten Schieberegisters erst ab Sample 128 (= Stock-lmt_dead).
# Davor stehen Rohwert-nahe Einträge (≈ -1,43e6) im Register: Richtungs-
# erkennung „gefiltert[0] > gefiltert[63]“ (0x0800800E) und der Ruhetest am
# gefilterten Kanal wären verfälscht.
PRES_FILTER_WARMUP = 128
# ANNAHME Reservefaktor 1,5 auf die Totzeit (Vorschlag der Firmware-Analyse):
# deckt Oszillator-Abweichung (live +1,2 %), Konfig-Handshake nach start
# (Schreiben/Rücklesen, B 0x08007DF4) und Schedulingschwankungen ab.
ARM_SETTLE_RESERVE = 1.5
# Mindest-Antastgeschwindigkeit (nur mode: probe, MCU-Trigger stoppt Z).
# Herleitung (alles ANNAHME bis zur Messung am Gerät):
#  - B/F Modell: Rampe < ~70-80 Counts/Sample löst NIE aus (Hochpass
#    gegen Mittel von vor 48-63 Samples -> Ausgang ≈ 55 × Steigung; nötig
#    L[4] >= min_hold + 2*add_hold = 5000).
#  - Live (Stock 5 mm/s): zoft 0,010-0,040 mm = 2,6-10 Samples vom
#    Kontaktbeginn bis zur Auslösung -> laut Modell (300 -> 17, 1000 -> 6
#    Samples) Rampe >= ~500 Counts/Sample bei 5 mm/s.
#  - Rampe ∝ Geschwindigkeit -> Grenze ≈ 5 × 80/500 ≈ 0,8 mm/s; Faktor ~3
#    Reserve (Steifigkeit Bett/Düse/Temperatur unbekannt) -> 2,5 mm/s.
#  - Skaliert mit (min_hold + 2*add_hold)/5000 × Rate/1280 × 128/PGA
#    (Schwelle in Counts, Hochpassfenster in Samples, Counts ∝ PGA);
#    nie kleiner als 2,5 mm/s. 2,5 = Upstream-Default second_homing_speed
#    (homing_speed/2) bei homing_speed 5.
SPEED_MIN_BASE = 2.5
STOCK_TRIGGER_TOP = 5000  # min_hold + 2*add_hold (Stock 4000 + 2*500)
STOCK_RATE_HZ = 1280.0
STOCK_PGA = 128
# min_hold: F Modell – max |gefiltert| in reiner Ruhe 1 002 Counts ->
# eine Schwelle darunter löst schon durch Rauschen aus.
MIN_HOLD_MIN = 1000
# Ruhetest (S4) – Erweiterungen 17.10.2026 (Firmware-Analyse):
REST_MIN_SAMPLES = 32  # ANNAHME: mind. halbes Fenster gültiger Samples
REST_TICK_TOL = 0.10  # ANNAHME: Median ±10 % um 1/Rate (live +1,2 %);
# Fehlerbilder „Sensor hängt“ (alle 5 ms, B 0x08005224) und „DOUT dauernd
# low“ (~55 µs) liegen um Faktor >= 6 daneben, falsche Rate (×2) auch.
REST_TICK_MIN_FRAC = 0.5  # ANNAHME: kein Einzelabstand < 0,5 × Periode.
# Bewusst weit: ein verspätet gelesenes Sample (Leerlauf-Polling mit
# freigegebenen IRQs, B 0x08007BA8) ergibt einen langen und einen kurzen
# Abstand – das ist Jitter, kein Fehler.
REST_FILT_MAX_FRAC_DEFAULT = 0.5  # ANNAHME: max |gefiltert| in Ruhe
# <= 0,5 × min_hold (live/Modell ≈ 1 000 bei min_hold 4 000 -> Faktor 2
# Reserve für Lüfter/Heizung); 1,0 = absolute Grenze (Auslöseniveau).
ADC24_MIN, ADC24_MAX = -(1 << 23), (1 << 23) - 1  # B 0x080051F6 (s32 aus 24 Bit)
ADC_STUCK_VALUE = -1  # F: DOUT dauernd high -> nach 5 ms 27 × '1' >> 3 = -1
# PC7 (step_swap_pin): Pull-Semantik der CREALITY-Firmware (B GPIO-Kern
# 0x08004734-0x08004766): pull_up 1 -> Pull-Up, 0 -> Pull-DOWN, sonst
# (-1 = Upstream '~') -> schwebend. Upstream: 0 = schwebend.
CREALITY_PULL = {1: "Pull-Up", 0: "Pull-Down", -1: "schwebend"}
STEP_SWAP_PIN_DEFAULT = "^!PC7"  # Pull-Up wie Stock-Endzustand (s. README)

HOST_PY_VER = 1  # eigene Host-Version (nicht Stock-78; ANNAHME: nur
# geloggt, kein exakter Match nötig – FW-Updates ändern PRES/STEP_VER).

# =====================================================================
# Sensor-Selbsttest KTWO_PRTOUCH_CHECK (Ersatz Stock-PRES_CHECK, optional)
# =====================================================================
# Grundlage: apax-Analyse 17.10.2026 (B = Beobachtung, F =
# Folgerung): Stock-PRES_CHECK nutzt den apax-Strom – config_prtouch_apax
# oid=<pres-oid> oid_estp=<Extruder-Stepper> (Config), start_prtouch_apax
# oid cfg_regs=60 acq_tick=93750 (ack expar1 = acq_tick), Strom
# resault_prtouch_apax mit der PRES-oid (S[0]), stop_prtouch_apax (ack 0).
# Jedes CS1237-Rohsample (≈ 1280/s, ohne Filter) + Extruder-Schrittintervall
# (espd, 0 = steht). GEFAHR (B): falsches oid_estp -> oid_lookup schlägt
# beim ERSTEN Sample nach start fehl -> MCU-Shutdown. Daher wird oid_estp
# NUR über die Klipper-API ermittelt (Stepper-Objekt des Extruders auf
# derselben MCU), sonst Config-Fehler – nie geraten.
# Buslast (B, live): ≈ 12,4 kB/s ≈ 54 % der 230 400-Bd-Düsenleitung.
PRES_CHECK_HOLD_DEFAULT = 15000  # ANNAHME Kriterium dif >= pres_chk_hold
# (Stock live 15000, alle Live-dif 37 535…324 487 bestanden; Kriterium nur
# aus der Namensspur).
PRES_CHECK_DURATION_DEFAULT_S = 3.0  # reine Messung ohne Last (DURATION=)
PRES_CHECK_MAX_S_DEFAULT = 15.0  # Strom höchstens so lange (Buslast)
PRES_CHECK_MAX_S_LIMIT = 60.0  # harte Obergrenze für pres_check_max_s
PRES_CHECK_EXTRUDE_MAX_DEFAULT = 5.0  # mm, „kleine Menge“
PRES_CHECK_EXTRUDE_MAX_LIMIT = 20.0
PRES_CHECK_EXTRUDE_SPEED_DEFAULT = 4.0  # mm/s Filament (Stock AUTO_APAX
# rechnet mit ≈ 0,9…8,4 mm/s E – ANNAHME)
PRES_CHECK_EXTRUDE_SPEED_LIMIT = 10.0
PRES_CHECK_MIN_TEMP_DEFAULT = 170.0  # °C, zusätzlich zu can_extrude
PRES_CHECK_RATE_TOL = 0.2  # ANNAHME ±20 %
PRES_CHECK_SKIP = 16  # ANNAHME: erste Samples je Kanal verwerfen
# (Konfig-Handshake nach start: start löscht das
# zurückgelesene CS1237-Register -> Handshake vor den ersten Samples).
PRES_CHECK_BAD_FRAC = 0.01  # ANNAHME: max. Anteil 0/-1/Sättigung
PRES_CHECK_MIN_SAMPLES = 256  # ANNAHME: ≈ 0,2 s bei 1280/s
PRES_CHECK_GUARD_FACTOR = 1.5  # Sample-Obergrenze = max_s × Rate × 1,5
APAX_BUS_BYTES_PER_S = 12400  # BELEGT live
APAX_BUS_BAUD = 230400  # Düsenleitung


# =====================================================================
# Pin-Helfer (wie k2_motor_init.py)
# =====================================================================

def _clean_pin_name(pin_raw):
    """'!PC7' / 'nozzle_mcu:PA15' -> 'PA15' (Invert- + MCU-Präfix weg)."""
    s = str(pin_raw).strip()
    if ":" in s:
        s = s.split(":", 1)[1]
    return s.lstrip("!^~").strip()


def pin_number_from_enum(enum_pin, pin_name):
    """Klipper-Pinnummer aus MCU-Enumeration 'pin' (ANNAHME Offset-Logik).

    BELEGT Dict-Einträge PA0 [0,16], PB0 [16,16], PC0 [32,16] …:
    PB9 = Basis PB0 (16) + 9 = 25.
    """
    clean = _clean_pin_name(pin_name)
    if not isinstance(enum_pin, dict):
        raise ValueError("Pin-Enumeration fehlt")
    if clean in enum_pin:
        v = enum_pin[clean]
        if isinstance(v, (list, tuple)):
            return int(v[0])
        return int(v)
    import re as _re
    m = _re.match(r"^([A-Z]+)(\d+)$", clean)
    if m:
        base_key = "%s0" % m.group(1)
        if base_key in enum_pin:
            v = enum_pin[base_key]
            base = int(v[0]) if isinstance(v, (list, tuple)) else int(v)
            return base + int(m.group(2))
    raise ValueError("Pin %r nicht in Enumeration" % (pin_name,))


def split_cs0_pin(spec):
    """'nozzle_mcu:PB13, nozzle_mcu:PB14' -> [pin1, pin2]."""
    parts = [p.strip() for p in str(spec).split(",") if p.strip()]
    return parts


def parse_pin_desc(desc):
    """Pin-Beschreibung wie Upstream PrinterPins.parse_pin (pins.py:66-95):
    Reihenfolge [^|~] [!] [chip:]pin. -> dict(pullup, invert, chip, pin).

    pullup: 1 ('^'), -1 ('~'), 0 (ohne). '!^PC7' ist wie bei Upstream
    UNGÜLTIG (Upstream würde mit „Invalid pin description“ abbrechen).
    """
    s = str(desc).strip()
    pull = inv = 0
    if s[:1] in ("^", "~"):
        pull = 1 if s[0] == "^" else -1
        s = s[1:].strip()
    if s[:1] == "!":
        inv = 1
        s = s[1:].strip()
    if ":" in s:
        chip, pin = [p.strip() for p in s.split(":", 1)]
    else:
        chip, pin = "mcu", s
    if (not pin or any(ch in pin for ch in "^~!:")
            or "".join(pin.split()) != pin):
        raise ValueError("ungültige Pin-Beschreibung %r (Format wie Upstream: "
                         "[^~] [!] [chip:]pin, z. B. ^!PC7)" % (desc,))
    return {"pullup": pull, "invert": inv, "chip": chip, "pin": pin}


# =====================================================================
# Absicherung nach Analyse der Düsen-Firmware – reine Funktionen
# =====================================================================

def cs1237_rate_pga(cfg_regs):
    """cfg_regs -> (Rate Hz, PGA); nur die drei belegten Werte (s. CS1237_CFG)."""
    try:
        return CS1237_CFG[int(cfg_regs)]
    except (KeyError, ValueError, TypeError):
        raise ValueError(
            "cfg_regs/pres_cfg_regs=%r unbekannt – zulässig nur %s "
            "(60 = 1280 Hz/PGA128 Stock, 56 = 1280 Hz/PGA64, 44 = 640 Hz/"
            "PGA128; Stock-printer.cfg:343)"
            % (cfg_regs, sorted(CS1237_CFG)))


def pres_sample_period_s(cfg):
    """Abtastperiode der Düse in s = 1/Rate aus cfg_regs (NICHT acq_tick:
    im Bitbang-Modus nur Ein-Schalter, B 0x08007BB6)."""
    rate, _pga = cs1237_rate_pga(cfg["cfg_regs"])
    return 1.0 / rate


def pres_dead_time_s(cfg):
    """Totzeit nach start_prtouch_pres: max(lmt_dead, 64) Samples
    (B 0x08004318, 0x08007F34) – Kontakt darin wird NICHT erkannt."""
    return max(int(cfg["lmt_dead"]), LMT_DEAD_MIN) * pres_sample_period_s(cfg)


def min_arm_settle_s(cfg):
    """Mindestwartezeit nach dem Scharfschalten (vor Ruhetest und Fahrt).

    = ARM_SETTLE_RESERVE × max(lmt_dead, 128) × Abtastperiode.
    lmt_dead: B Totzeit; 128: F gefiltertes Register erst dann voll gültig
    (PRES_FILTER_WARMUP). Stock (128, 1280 Hz): 1,5 × 0,1 s = 0,15 s.
    """
    n = max(int(cfg["lmt_dead"]), LMT_DEAD_MIN, PRES_FILTER_WARMUP)
    return ARM_SETTLE_RESERVE * n * pres_sample_period_s(cfg)


def min_probe_speed(cfg):
    """Mindest-Antastgeschwindigkeit in mm/s (ANNAHME, Herleitung bei
    SPEED_MIN_BASE): 2,5 mm/s × max(1, Schwellenfaktor × Ratenfaktor ×
    PGA-Faktor)."""
    rate, pga = cs1237_rate_pga(cfg["cfg_regs"])
    top = float(cfg["min_hold"]) + 2.0 * float(cfg["add_hold"])
    scale = ((top / STOCK_TRIGGER_TOP) * (rate / STOCK_RATE_HZ)
             * (float(STOCK_PGA) / float(pga)))
    return SPEED_MIN_BASE * max(1.0, scale)


def check_probe_speed(speed, cfg):
    """None wenn zulässig, sonst Fehlertext (zu schnell / zu langsam)."""
    sp = float(speed)
    if not 0.0 < sp <= SPEED_MAX + 1e-12:
        return ("Antastgeschwindigkeit %.2f mm/s unzulässig (hart <= %.1f "
                "mm/s)" % (sp, SPEED_MAX))
    vmin = min_probe_speed(cfg)
    if sp < vmin - 1e-9:
        return ("Antastgeschwindigkeit %.2f mm/s zu langsam (Minimum %.2f "
                "mm/s, ANNAHME): die Düsen-MCU erkennt nur einen schnellen "
                "Kraftanstieg – eine zu flache Kraftrampe (< ~80 Counts/"
                "Sample) löst NIE aus, Z würde ins Bett fahren" % (sp, vmin))
    return None


def validate_mcu_params(cfg):
    """Antast-Parameter gegen die Firmware-Analyse prüfen (wirft ValueError).

    Reihenfolge = Gefährlichkeit. Belege: Analyse der Düsen-Firmware
    (Adressen Düse noz0_130, Basis 0x08003000).
    """
    mn, mx, add = (int(cfg["min_hold"]), int(cfg["max_hold"]),
                   int(cfg["add_hold"]))
    if mn == mx:
        # B 0x08007E26-0x08007E30: min_hold == max_hold -> Stream-Modus,
        # kein Puffer, kein Filter, KEINE Auslösung -> Crash ins Bett.
        raise ValueError(
            "min_hold == max_hold (%d): die Düsen-MCU läuft dann im Stream-"
            "Modus und löst NIE aus – Z würde ins Bett fahren. max_hold "
            "(pres_tri_hold[1]) muss != min_hold sein (Stock 10000 bei 4000)"
            % mn)
    if int(cfg["ned_hftr"]) <= 0:
        # B 0x08007E6C: Hochpass nur bei ned_hftr != 0; F Modell:
        # ohne Hochpass Fehlauslösung nach 133 Samples in reiner Ruhe.
        raise ValueError(
            "ned_hftr=%d: Hochpass aus – die Schwellen wirken dann auf "
            "Absolutwerte (≈ -1,43e6) -> Fehlauslösung schon in Ruhe "
            "(Modell: nach 133 Samples). ned_hftr muss > 0 sein "
            "(pres_tri_fter[1] > 0, Stock 1 -> 1000)" % int(cfg["ned_hftr"]))
    lf = int(cfg["ned_lftr"])
    if not (lf == 0 or 0 < lf <= 1000):
        # B 0x080042EE/0x08007ED6: a = ned_lftr/1000, y = (1-a)·y + a·x;
        # a > 1 -> (1-a) < 0 -> schwingender Tiefpass.
        raise ValueError(
            "ned_lftr=%d unzulässig: Tiefpass a = ned_lftr/1000 muss in "
            "(0, 1] liegen (0 = aus); > 1000 macht den IIR-Filter "
            "schwingend (pres_tri_fter[2] in [0, 1], Stock 0.8)" % lf)
    if int(cfg["ned_tftr"]) < 0:
        raise ValueError("ned_tftr=%d muss >= 0 sein (nur Schalter, Stock 5)"
                         % int(cfg["ned_tftr"]))
    if int(cfg["lmt_dead"]) < LMT_DEAD_MIN:
        # B 0x08004310-0x0800431E: Firmware hebt auf 64 an – der Host würde
        # mit einer falschen Totzeit rechnen.
        raise ValueError(
            "lmt_dead=%d < %d: die Firmware hebt auf %d Samples an; Wert "
            "ist eine SAMPLE-Anzahl (nicht ms), Stock 128 = 100 ms bei "
            "1280 Hz" % (int(cfg["lmt_dead"]), LMT_DEAD_MIN, LMT_DEAD_MIN))
    cs1237_rate_pga(cfg["cfg_regs"])  # wirft bei unbekanntem cfg_regs
    if mn < MIN_HOLD_MIN:
        raise ValueError(
            "min_hold=%d < %d: liegt im Ruherauschen des gefilterten Kanals "
            "(Modell max |gefiltert| in Ruhe 1 002) -> Fehlauslösungen "
            "(Stock 4000)" % (mn, MIN_HOLD_MIN))
    if add < 0:
        raise ValueError(
            "add_hold=%d < 0: die Auslösetreppe min/min+add/min+2·add muss "
            "steigen (ANNAHME, Stock 500)" % add)
    if int(cfg["pres_idx"]) != 0:
        # B 0x08004A78: Kanalzahl = idx+1; k2_prtouch konfiguriert nur EINEN
        # Kanal und liest ch=0 (K2 live: idx 0) – andere Kanäle blieben
        # ohne Pins, würden aber mit ausgewertet.
        raise ValueError(
            "pres_idx=%d: nur 0 unterstützt (K2 hat einen Kraftsensor-Kanal, "
            "Stock idx=0; Kanalzahl = idx+1 in der MCU)" % int(cfg["pres_idx"]))
    if int(cfg["acq_tick"]) <= 0 or int(cfg["aqc_tick"]) <= 0:
        raise ValueError("acq_tick/aqc_tick müssen > 0 sein (acq_tick == 0 "
                         "= keine Abtastung, B 0x08007BAA)")


def validate_arm_settle(cfg):
    """arm_settle_s >= min_arm_settle_s (wirft ValueError)."""
    need = min_arm_settle_s(cfg)
    have = float(cfg["arm_settle_s"])
    if need > ARM_SETTLE_MAX_S + 1e-12:
        raise ValueError(
            "lmt_dead=%d bei %d Hz braucht arm_settle_s >= %.3f s – mehr als "
            "das Maximum %.1f s; lmt_dead verkleinern (Stock 128)"
            % (int(cfg["lmt_dead"]), int(1.0 / pres_sample_period_s(cfg)),
               need, ARM_SETTLE_MAX_S))
    if have < need - 1e-9:
        raise ValueError(
            "arm_settle_s=%.3f zu kurz: Minimum %.3f s = %.1f × max(lmt_dead "
            "%d, %d) Samples × %.4f ms. In der Totzeit nach start_prtouch_pres "
            "erkennt die Düse KEINEN Kontakt"
            % (have, need, ARM_SETTLE_RESERVE, int(cfg["lmt_dead"]),
               PRES_FILTER_WARMUP, pres_sample_period_s(cfg) * 1000.0))


def validate_step_swap_pin(cfg):
    """step_swap_pin für den Endstop (mode probe): '^' + '!' Pflicht.

    PC7-Pull (Entscheidung 17.10.2026, README A.8): Pull-UP wie der
    Stock-Endzustand. Stock sendet config_endstop pull_up=0 (in der Creality-
    FW = Pull-DOWN, B 0x08004734) und DANACH config_prtouch_step (setzt immer
    Pull-Up, B 0x08004910) -> Pull-Up. k2_prtouch sendet config_prtouch_step
    VOR config_endstop (Callback-Reihenfolge) -> mit '!PC7' bliebe Pull-Down.
    Mit '^' fordern BEIDE Befehle Pull-Up -> Endzustand reihenfolge-
    unabhängig = Stock. '!' (ausgelöst = PC7 low) ist B (Ruhe HIGH, Kontakt
    LOW).
    """
    p = parse_pin_desc(cfg["step_swap_pin"])
    if p["chip"] != str(cfg.get("main_mcu", "mcu")):
        raise ValueError(
            "step_swap_pin %r liegt nicht auf main_mcu %r (Swap-Eingang PC7 "
            "ist an der Main-MCU)" % (cfg["step_swap_pin"], cfg.get("main_mcu")))
    if p["pullup"] != 1:
        raise ValueError(
            "step_swap_pin %r: Pull-Up '^' fehlt (ergäbe in der Creality-"
            "Firmware %s statt Stock-Pull-Up – dort heißt pull_up=0 Pull-DOWN, "
            "'~' schwebend). Bitte '^!%s' schreiben"
            % (cfg["step_swap_pin"], CREALITY_PULL.get(p["pullup"], "?"),
               p["pin"]))
    if p["invert"] != 1:
        raise ValueError(
            "step_swap_pin %r: Invertierung '!' fehlt – PC7 ist in Ruhe HIGH "
            "und bei Kontakt LOW, also '^!%s'"
            % (cfg["step_swap_pin"], p["pin"]))
    return p


# =====================================================================
# Config (rein + Klipper-Hülle)
# =====================================================================

def _num_list(raw, default, count, name):
    """'5, 1, 0.8' / [5, 1, 0.8] / None -> Tupel aus count Zahlen."""
    if raw is None or (isinstance(raw, str) and raw.strip() == ""):
        return tuple(float(v) for v in default)
    if isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        parts = [p for p in str(raw).replace(";", ",").split(",")
                 if p.strip() != ""]
    try:
        vals = tuple(float(str(p).strip()) for p in parts)
    except ValueError:
        raise ValueError("%s muss %d Zahlen enthalten: %r"
                         % (name, count, raw))
    if len(vals) != count:
        raise ValueError("%s muss %d Zahlen enthalten: %r"
                         % (name, count, raw))
    return vals


def stock_to_mcu_params(pres_cfg_regs, pres_acq_tkms, pres_tri_fter,
                        pres_tri_hold, pres_ded_tkms, step_acq_tkms,
                        clock_freq_pres=CLOCK_FREQ,
                        clock_freq_step=CLOCK_FREQ):
    """Stock-Konfigwerte -> MCU-Parameter für start_prtouch_pres/_step.

    BELEGT 16.10.2026 (Emulation des Stock-Moduls, Zuordnung durch
    Variation eindeutig; beide MCUs 120 MHz laut klippy.log):
      cfg_regs = pres_cfg_regs
      acq_tick = round(pres_acq_tkms / 1000 * CLOCK_FREQ Düse)
      ned_tftr = pres_tri_fter[0]
      ned_hftr = round(pres_tri_fter[1] * 1000)
      ned_lftr = round(pres_tri_fter[2] * 1000)
      min/max/add_hold = pres_tri_hold
      lmt_dead = int(pres_ded_tkms)   (abgeschnitten, nicht gerundet)
      aqc_tick = round(step_acq_tkms / 1000 * CLOCK_FREQ Main)
    Stock live: [60, 93750, 5, 1000, 800, 4000, 10000, 500, 128], 46875.
    ANNAHME (nicht prüfbar): ned_tftr/ned_hftr stehen im Dictionary als
    %c; Stock sendet trotzdem 1000 (VLQ-kodiert, Upstream-msgproto
    PT_byte kodiert ebenfalls ohne Begrenzung) – wir senden exakt wie Stock.
    """
    f = tuple(float(v) for v in pres_tri_fter)
    h = tuple(pres_tri_hold)
    return {
        "cfg_regs": int(pres_cfg_regs),
        "acq_tick": int(round(float(pres_acq_tkms) / 1000.0
                              * float(clock_freq_pres))),
        "ned_tftr": int(round(f[0])),
        "ned_hftr": int(round(f[1] * 1000.0)),
        "ned_lftr": int(round(f[2] * 1000.0)),
        "min_hold": int(round(float(h[0]))),
        "max_hold": int(round(float(h[1]))),
        "add_hold": int(round(float(h[2]))),
        "lmt_dead": int(float(pres_ded_tkms)),
        "aqc_tick": int(round(float(step_acq_tkms) / 1000.0
                              * float(clock_freq_step))),
    }


def parse_options(opts):
    """Reines Dict (keys klein) -> strukturierte Config.

    Pflicht: keine (alle mit sicheren Defaults); Pins/Stepper-OIDs mit
    Stock-Defaults aus Stock-printer.cfg. Harte Sicherheitsgrenzen werden
    hier durchgesetzt (ValueError bei Überschreitung).
    """
    o = {str(k).strip().lower(): v for k, v in dict(opts).items()}

    def _bool(key, default):
        v = str(o.get(key, str(default))).strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off"):
            return False
        raise ValueError("%s muss bool sein: %r" % (key, o.get(key)))

    def _float(key, default):
        try:
            return float(str(o.get(key, default)).strip())
        except ValueError:
            raise ValueError("%s muss Zahl sein: %r" % (key, o.get(key)))

    def _int(key, default):
        try:
            return int(str(o.get(key, default)).strip())
        except ValueError:
            raise ValueError("%s muss int sein: %r" % (key, o.get(key)))

    def _str(key, default):
        v = o.get(key, default)
        if v is None:
            return None
        s = str(v).strip()
        return s if s != "" else None

    cfg = {}
    # --- Sicherheit ---
    cfg["allow_move"] = _bool("allow_move", False)
    cfg["step_mm"] = _float("step_mm", STEP_MM_DEFAULT)
    cfg["probe_speed"] = _float("probe_speed", SPEED_DEFAULT)
    cfg["lift_speed"] = _float("lift_speed", SPEED_DEFAULT)
    cfg["max_travel_mm"] = _float("max_travel_mm", MAX_TRAVEL_DEFAULT)
    cfg["z_lift_mm"] = _float("z_lift_mm", Z_LIFT_DEFAULT)
    cfg["probe_timeout_s"] = _float("probe_timeout_s", TIMEOUT_DEFAULT_S)
    cfg["contact_thresh"] = _float("contact_thresh", STOCK_CONTACT_THRESH)
    cfg["emergency_thresh"] = _float("emergency_thresh",
                                     EMERGENCY_THRESH_DEFAULT)
    cfg["rest_max_dif"] = _float("rest_max_dif", STOCK_REST_MAX_DIF)
    # Probe-Hauptweg: dort entscheidet die MCU am GEFILTERTEN Kanal (Ruhetest
    # rest_filt_max_frac × min_hold); die Rohwert-Spanne ist nur eine
    # Plausibilitätsprüfung und darf weiter sein als im Rückfallweg (Host-
    # Kriterium auf Rohwerten). Gemessen 22.10.2026, je 64 Samples: kalt
    # 600–1 000; Düse 140 °C gehalten, Hotend-Lüfter gedämpft 4 600–8 236
    # bei bestandenem gefiltertem Ruhetest (ANNAHME Grenze ≈ 2 × Messwert).
    cfg["rest_max_dif_probe"] = _float("rest_max_dif_probe",
                                       REST_MAX_DIF_PROBE_DEFAULT)
    if not cfg["rest_max_dif"] <= cfg["rest_max_dif_probe"] <= 100000:
        raise ValueError("rest_max_dif_probe muss in [rest_max_dif, 100000] "
                         "sein: %r" % (o.get("rest_max_dif_probe"),))
    cfg["rest_retries"] = _int("rest_retries", REST_RETRIES_DEFAULT)
    if not 0 <= cfg["rest_retries"] <= 5:
        raise ValueError("rest_retries muss in [0, 5] sein: %r"
                         % (o.get("rest_retries"),))
    cfg["rest_min_consec"] = _int("rest_min_consec", 2)
    # Host-Kontaktkriterium (nur mode steps), Default = bisheriges Verhalten.
    cfg["contact_criterion"] = (_str("contact_criterion", "window")
                                or "window").lower()
    if cfg["contact_criterion"] not in CONTACT_CRITERIA:
        raise ValueError("contact_criterion muss 'window' oder 'cumulative' "
                         "sein: %r" % (o.get("contact_criterion"),))
    cfg["contact_steps"] = _int("contact_steps", CONTACT_STEPS_DEFAULT)
    if not CONTACT_STEPS_MIN <= cfg["contact_steps"] <= CONTACT_STEPS_MAX:
        raise ValueError("contact_steps muss in [%d, %d] sein: %r"
                         % (CONTACT_STEPS_MIN, CONTACT_STEPS_MAX,
                            o.get("contact_steps")))
    cfg["contact_step_min"] = _float("contact_step_min",
                                     CONTACT_STEP_MIN_DEFAULT)
    if not 0.0 < cfg["step_mm"] <= STEP_MM_MAX + 1e-12:
        raise ValueError("step_mm muss in (0, %.2f] sein (S1): %r"
                         % (STEP_MM_MAX, o.get("step_mm")))
    if not 0.0 < cfg["probe_speed"] <= SPEED_MAX + 1e-12:
        raise ValueError("probe_speed muss in (0, %.1f] sein (S1): %r"
                         % (SPEED_MAX, o.get("probe_speed")))
    if not 0.0 < cfg["lift_speed"] <= 100.0 + 1e-12:
        raise ValueError("lift_speed muss in (0, 100] sein: %r"
                         % (o.get("lift_speed"),))
    if not 0.0 < cfg["max_travel_mm"] <= MAX_TRAVEL_MAX + 1e-12:
        raise ValueError("max_travel_mm muss in (0, %.1f] sein: %r"
                         % (MAX_TRAVEL_MAX, o.get("max_travel_mm")))
    if not 0.0 < cfg["z_lift_mm"]:
        raise ValueError("z_lift_mm muss > 0 sein")
    if cfg["contact_thresh"] <= 0:
        raise ValueError("contact_thresh muss > 0 sein")
    if cfg["emergency_thresh"] <= cfg["contact_thresh"]:
        raise ValueError("emergency_thresh muss > contact_thresh sein "
                         "(sonst löst jeder Kontakt die Notgrenze aus)")
    if cfg["emergency_thresh"] > EMERGENCY_THRESH_MAX + 1e-9:
        raise ValueError("emergency_thresh muss <= %d sein (≈ 2 × größte "
                         "normale Stock-Spitze %d): %r"
                         % (EMERGENCY_THRESH_MAX, LIVE_PEAK_MAX,
                            o.get("emergency_thresh")))
    if not 0.0 < cfg["contact_step_min"] < cfg["contact_thresh"]:
        raise ValueError("contact_step_min muss in (0, contact_thresh) "
                         "liegen: %r" % (o.get("contact_step_min"),))

    # --- Betriebsart + Probe-Hauptweg (Upstream-[probe]-Ersatz) ---
    cfg["mode"] = (_str("mode", "probe") or "probe").lower()
    if cfg["mode"] not in MODES:
        raise ValueError("mode muss 'probe' oder 'steps' sein: %r"
                         % (o.get("mode"),))
    # 'speed' = Upstream-Optionsname (ProbeParameterHelper); Default ist
    # probe_speed, damit beide Wege dieselbe Geschwindigkeit nutzen.
    cfg["speed"] = _float("speed", cfg["probe_speed"])
    if not 0.0 < cfg["speed"] <= SPEED_MAX + 1e-12:
        raise ValueError("speed muss in (0, %.1f] sein (S1): %r"
                         % (SPEED_MAX, o.get("speed")))
    cfg["z_offset"] = _float("z_offset", 0.0)  # wie Stock (Düse = Taster)
    pe = _str("position_endstop", None)
    cfg["position_endstop"] = (cfg["z_offset"] if pe is None
                               else _float("position_endstop", 0.0))
    cfg["probe_max_travel_mm"] = _float("probe_max_travel_mm",
                                        PROBE_MAX_TRAVEL_DEFAULT)
    if not 0.0 < cfg["probe_max_travel_mm"] <= MAX_TRAVEL_MAX + 1e-12:
        raise ValueError("probe_max_travel_mm muss in (0, %.1f] sein: %r"
                         % (MAX_TRAVEL_MAX, o.get("probe_max_travel_mm")))
    cfg["arm_settle_s"] = _float("arm_settle_s", ARM_SETTLE_DEFAULT_S)
    if not 0.0 <= cfg["arm_settle_s"] <= ARM_SETTLE_MAX_S + 1e-12:
        raise ValueError("arm_settle_s muss in [0, %.1f] sein: %r"
                         % (ARM_SETTLE_MAX_S, o.get("arm_settle_s")))
    # Signalweg-Test PA15->PC7 über den Endstop vor JEDER Fahrt (sta_swap
    # 0 -> Endstop ausgelöst, 1 -> frei), Ruhetest nach dem Scharfschalten,
    # optionale Diagnose-Auslesung des eingefrorenen Rings danach (Stock).
    cfg["swap_check"] = _bool("swap_check", True)
    cfg["rest_check"] = _bool("rest_check", True)
    cfg["diag_readout"] = _bool("diag_readout", False)
    # Ruhetest am gefilterten Kanal (is_src=0): max |gefiltert| <=
    # rest_filt_max_frac × min_hold (ANNAHME, s. REST_FILT_MAX_FRAC_DEFAULT).
    cfg["rest_filt_max_frac"] = _float("rest_filt_max_frac",
                                       REST_FILT_MAX_FRAC_DEFAULT)
    if not 0.0 < cfg["rest_filt_max_frac"] <= 1.0 + 1e-12:
        raise ValueError("rest_filt_max_frac muss in (0, 1] sein (1 = "
                         "Auslöseniveau min_hold): %r"
                         % (o.get("rest_filt_max_frac"),))

    # --- MCUs / Pins (Stock-Defaults aus Stock-printer.cfg) ---
    cfg["main_mcu"] = _str("main_mcu", "mcu") or "mcu"
    cfg["nozzle_mcu"] = _str("nozzle_mcu", "nozzle_mcu") or "nozzle_mcu"
    cfg["pres_idx"] = _int("pres_idx", 0)
    cfg["pres_swap_pin"] = _str("pres_swap_pin",
                                "nozzle_mcu:PA15") or "nozzle_mcu:PA15"
    # Default '^!PC7' = Pull-Up wie Stock-Endzustand (validate_step_swap_pin).
    cfg["step_swap_pin"] = (_str("step_swap_pin", STEP_SWAP_PIN_DEFAULT)
                            or STEP_SWAP_PIN_DEFAULT)
    cfg["pres_cs0_pin"] = _str("pres_cs0_pin",
                               "nozzle_mcu:PB13, nozzle_mcu:PB14")
    # ANNAHME clk/sdo: stehen NICHT in der Stock-printer.cfg (Negativbeleg
    # Bit-Spezifikation). Auftrag verlangt Ableitung aus pres_cs0_pin
    # PB13/PB14 mit konfigurierbarer Zuordnung. Falls pres_clk_pin/
    # pres_sdo_pin gesetzt, haben sie Vorrang (für späteren Live-Fund).
    cfg["pres_clk_pin"] = _str("pres_clk_pin", None)
    cfg["pres_sdo_pin"] = _str("pres_sdo_pin", None)
    cfg["pres_clk_sdo_swap"] = _bool("pres_clk_sdo_swap", False)
    cs0 = split_cs0_pin(cfg["pres_cs0_pin"] or "")
    if cfg["pres_clk_pin"] is None or cfg["pres_sdo_pin"] is None:
        if len(cs0) != 2:
            raise ValueError("pres_cs0_pin muss 2 Pins enthalten "
                             "(clk/sdo-Ableitung, ANNAHME): %r"
                             % (cfg["pres_cs0_pin"],))
        d_clk, d_sdo = cs0[0], cs0[1]
        if cfg["pres_clk_sdo_swap"]:
            d_clk, d_sdo = d_sdo, d_clk
        if cfg["pres_clk_pin"] is None:
            cfg["pres_clk_pin"] = d_clk
        if cfg["pres_sdo_pin"] is None:
            cfg["pres_sdo_pin"] = d_sdo

    # --- Sampling-Parameter (Stock-Stil, BELEGT 16.10.2026) ---
    # Eingabe wie Stock-printer.cfg [prtouch_v3]; MCU-Werte werden daraus
    # berechnet (stock_to_mcu_params). Direkte MCU-Optionen (cfg_regs,
    # acq_tick, aqc_tick, ned_*, *_hold, lmt_dead) übersteuern einzeln.
    if "ned_lftr_scale" in o:
        raise ValueError("ned_lftr_scale entfällt (alte ANNAHME widerlegt): "
                         "ned_lftr = round(pres_tri_fter[2] * 1000) – "
                         "pres_tri_fter oder ned_lftr (MCU-Wert) setzen")
    cfg["clock_freq"] = _float("clock_freq", CLOCK_FREQ)
    if cfg["clock_freq"] <= 0:
        raise ValueError("clock_freq muss > 0 sein")
    cfg["pres_cfg_regs"] = _int("pres_cfg_regs", STOCK_CFG_REGS)
    cfg["pres_acq_tkms"] = _float("pres_acq_tkms", STOCK_PRES_ACQ_TKMS)
    cfg["step_acq_tkms"] = _float("step_acq_tkms", STOCK_STEP_ACQ_TKMS)
    cfg["pres_ded_tkms"] = _float("pres_ded_tkms", STOCK_DED_TKMS)
    cfg["pres_tri_fter"] = _num_list(o.get("pres_tri_fter"), STOCK_TRI_FTER,
                                     3, "pres_tri_fter")
    cfg["pres_tri_hold"] = _num_list(o.get("pres_tri_hold"), STOCK_TRI_HOLD,
                                     3, "pres_tri_hold")
    mcu_p = stock_to_mcu_params(
        cfg["pres_cfg_regs"], cfg["pres_acq_tkms"], cfg["pres_tri_fter"],
        cfg["pres_tri_hold"], cfg["pres_ded_tkms"], cfg["step_acq_tkms"],
        cfg["clock_freq"], cfg["clock_freq"])
    for key, val in mcu_p.items():
        cfg[key] = _int(key, val) if key in o else int(val)
    # Firmware-Analyse 17.10.2026: Antast-Parameter prüfen (klare Meldung, kein
    # Start) – min_hold != max_hold, ned_hftr != 0, ned_lftr, lmt_dead,
    # cfg_regs, min_hold-Untergrenze, add_hold, pres_idx, acq/aqc_tick.
    validate_mcu_params(cfg)

    # --- Datenformat (ANNAHME, zentral konfigurierbar) ---
    # BELEGT: ticks/datas sind Byte-Arrays (%.*s), 64 Samples, little/
    # signed/median-Strings in unzip_data. Byte-Breite statisch nicht
    # entscheidbar → Arbeitshypothese 4B u32-LE / 4B s32-LE (MCU-clock).
    # BELEGT 16.10.2026 (Live-Lesetest): MCU packt ticks/datas
    # (n + Größenmaske + Delta-Kodierung) -> packing "packed" (Default).
    # "struct" = alte Arbeitshypothese (feste Breite, tick/data_format).
    cfg["packing"] = _str("packing", "packed") or "packed"
    if cfg["packing"] not in ("packed", "struct"):
        raise ValueError("packing muss 'packed' oder 'struct' sein: %r"
                         % (cfg["packing"],))
    cfg["tick_format"] = _str("tick_format", "<I") or "<I"
    cfg["data_format"] = _str("data_format", "<i") or "<i"
    cfg["tick_size"] = _int("tick_size", 4)
    cfg["data_size"] = _int("data_size", 4)
    for k in ("tick_format", "data_format"):
        try:
            struct.calcsize(cfg[k])
        except struct.error:
            raise ValueError("%s ungültig: %r" % (k, cfg[k]))
    if cfg["tick_size"] <= 0 or cfg["data_size"] <= 0:
        raise ValueError("tick_size/data_size müssen > 0 sein")
    cfg["step_dist_mm"] = _float("step_dist_mm", STOCK_Z_STEP_MM)
    if cfg["step_dist_mm"] <= 0:
        raise ValueError("step_dist_mm muss > 0 sein")

    # --- can_brk / Retry ---
    cfg["retry_dx"] = _float("retry_dx", STOCK_RETRY_DX)
    cfg["retry_dy"] = _float("retry_dy", STOCK_RETRY_DY)
    cfg["retry_lift_mm"] = _float("retry_lift_mm", 1.0)
    # ANNAHME-Schranken aus genau 1 Beobachtung (T58): best<=53 UND
    # step<=42 UND zoft>=0.040 → can_brk False. Nur UND-Verknüpfung,
    # damit normale Randwerte (59/58/…) nicht fälschlich verwerfen.
    cfg["can_brk_min_best"] = _int("can_brk_min_best", 53)
    cfg["can_brk_min_step"] = _int("can_brk_min_step", 42)
    cfg["can_brk_max_zoft"] = _float("can_brk_max_zoft", 0.040)
    # Geltungsbereich (Live-Abgleich 18.10.2026 A8): Stock wertet
    # can_brk NUR im Bereichs-Mesh aus (22/22 Mesh-Antastungen mit Zeile
    # „probe_z is …, can_brk is True“, die 13 G28-Z-/Reinigungs-/
    # G28_RE_CHECK-Antastungen ohne). Die Regel würde 2/2 G28-Z- und 2/3
    # Reinigungs-Antastungen verwerfen (50/36/0,053 · 52/41/0,043 ·
    # 47/31/0,062 · 44/24/0,075). 'mesh' (Default) = nur im Mesh-Kontext,
    # 'all' = altes Verhalten (jede Antastung), 'off' = nie.
    cfg["can_brk_scope"] = (_str("can_brk_scope", "mesh") or "mesh").lower()
    if cfg["can_brk_scope"] not in CAN_BRK_SCOPES:
        raise ValueError("can_brk_scope muss 'mesh', 'all' oder 'off' sein: "
                         "%r" % (o.get("can_brk_scope"),))

    # --- Temperaturen (nur Warnung, S5) ---
    cfg["temp_nozzle_warn"] = _float("temp_nozzle_warn", STOCK_TEMP_NOZZLE)
    cfg["temp_bed_warn"] = _float("temp_bed_warn", STOCK_TEMP_BED)
    cfg["temp_tolerance"] = _float("temp_tolerance", 15.0)
    # Heiß-Antastung (Live-Abgleich E2): nur Meldung, nie Abbruch.
    cfg["hot_probe_warn"] = _bool("hot_probe_warn", True)
    cfg["hot_probe_margin"] = _float("hot_probe_margin",
                                     HOT_PROBE_MARGIN_DEFAULT)
    if not 0.0 < cfg["hot_probe_margin"] <= HOT_PROBE_MARGIN_MAX + 1e-9:
        raise ValueError("hot_probe_margin muss in (0, %.0f] °C sein: %r"
                         % (HOT_PROBE_MARGIN_MAX, o.get("hot_probe_margin")))
    # Lüfterdämpfung beim Antasten (Stock DISTUURB_CTL „SET_FANS=0.60
    # bak_fans=1.00“ bei multi_probe_begin, prth_min_fans 0,6). Gemessen am
    # Pi 22.10.2026: Hotend-Lüfter 100 % → Ruhetest dif 3200–6400, gefiltert
    # bis 3589 (Grenze 2000); Lüfter aus → dif 700–900.
    cfg["fan_damping"] = _bool("fan_damping", True)
    cfg["fan_damping_speed"] = _float("fan_damping_speed", STOCK_FANS)
    if not 0.0 <= cfg["fan_damping_speed"] <= 1.0:
        raise ValueError("fan_damping_speed muss in [0, 1] sein: %r"
                         % (o.get("fan_damping_speed"),))
    cfg["fan_damping_settle_s"] = _float("fan_damping_settle_s",
                                         FAN_DAMPING_SETTLE_DEFAULT_S)
    if not 0.0 <= cfg["fan_damping_settle_s"] <= FAN_DAMPING_SETTLE_MAX_S:
        raise ValueError("fan_damping_settle_s muss in [0, %.0f] s sein: %r"
                         % (FAN_DAMPING_SETTLE_MAX_S,
                            o.get("fan_damping_settle_s")))

    # --- Stepper-OIDs (ANNAHME Auto-Lookup, manuell übersteuerbar) ---
    cfg["stepper_x_oid"] = o.get("stepper_x_oid")
    cfg["stepper_y_oid"] = o.get("stepper_y_oid")
    cfg["stepper_z_oid"] = o.get("stepper_z_oid")
    for k in ("stepper_x_oid", "stepper_y_oid", "stepper_z_oid"):
        if cfg[k] is not None and str(cfg[k]).strip() != "":
            try:
                cfg[k] = int(str(cfg[k]).strip())
            except ValueError:
                raise ValueError("%s muss int sein: %r" % (k, cfg[k]))
        else:
            cfg[k] = None

    cfg["shutdown_on_failure"] = _bool("shutdown_on_failure", False)

    # --- Sensor-Selbsttest KTWO_PRTOUCH_CHECK (Default AUS) ---
    # pres_check: False -> kein config_prtouch_apax, kein G-Code, keine
    # Antwortregistrierung (Verhalten exakt wie vorher).
    cfg["pres_check"] = _bool("pres_check", False)
    cfg["pres_check_hold"] = _float("pres_check_hold",
                                    PRES_CHECK_HOLD_DEFAULT)
    cfg["pres_check_max_s"] = _float("pres_check_max_s",
                                     PRES_CHECK_MAX_S_DEFAULT)
    cfg["pres_check_duration_s"] = _float("pres_check_duration_s",
                                          PRES_CHECK_DURATION_DEFAULT_S)
    cfg["pres_check_extrude_max_mm"] = _float(
        "pres_check_extrude_max_mm", PRES_CHECK_EXTRUDE_MAX_DEFAULT)
    cfg["pres_check_extrude_speed"] = _float(
        "pres_check_extrude_speed", PRES_CHECK_EXTRUDE_SPEED_DEFAULT)
    cfg["pres_check_min_temp"] = _float("pres_check_min_temp",
                                        PRES_CHECK_MIN_TEMP_DEFAULT)
    cfg["pres_check_rate_tol"] = _float("pres_check_rate_tol",
                                        PRES_CHECK_RATE_TOL)
    cfg["pres_check_skip"] = _int("pres_check_skip", PRES_CHECK_SKIP)
    cfg["pres_check_bad_frac"] = _float("pres_check_bad_frac",
                                        PRES_CHECK_BAD_FRAC)
    cfg["pres_check_min_samples"] = _int("pres_check_min_samples",
                                         PRES_CHECK_MIN_SAMPLES)
    cfg["pres_check_extruder"] = (_str("pres_check_extruder", "extruder")
                                  or "extruder")
    if cfg["pres_check_hold"] <= 0:
        raise ValueError("pres_check_hold muss > 0 sein")
    if not 0.0 < cfg["pres_check_max_s"] <= PRES_CHECK_MAX_S_LIMIT + 1e-9:
        raise ValueError("pres_check_max_s muss in (0, %.0f] sein (Buslast "
                         "≈ 12,4 kB/s): %r" % (PRES_CHECK_MAX_S_LIMIT,
                                               o.get("pres_check_max_s")))
    if not 0.0 < cfg["pres_check_duration_s"] <= cfg["pres_check_max_s"]:
        raise ValueError("pres_check_duration_s muss in (0, "
                         "pres_check_max_s] sein: %r"
                         % (o.get("pres_check_duration_s"),))
    if not (0.0 < cfg["pres_check_extrude_max_mm"]
            <= PRES_CHECK_EXTRUDE_MAX_LIMIT + 1e-9):
        raise ValueError("pres_check_extrude_max_mm muss in (0, %.0f] sein"
                         % PRES_CHECK_EXTRUDE_MAX_LIMIT)
    if not (0.0 < cfg["pres_check_extrude_speed"]
            <= PRES_CHECK_EXTRUDE_SPEED_LIMIT + 1e-9):
        raise ValueError("pres_check_extrude_speed muss in (0, %.0f] mm/s "
                         "sein" % PRES_CHECK_EXTRUDE_SPEED_LIMIT)
    if cfg["pres_check_min_temp"] < 0:
        raise ValueError("pres_check_min_temp muss >= 0 sein")
    if not 0.0 < cfg["pres_check_rate_tol"] <= 0.5:
        raise ValueError("pres_check_rate_tol muss in (0, 0.5] sein")
    if not 0 <= cfg["pres_check_skip"] <= 1280:
        raise ValueError("pres_check_skip muss in [0, 1280] sein")
    if not 0.0 <= cfg["pres_check_bad_frac"] <= 0.5:
        raise ValueError("pres_check_bad_frac muss in [0, 0.5] sein")
    if cfg["pres_check_min_samples"] < 2:
        raise ValueError("pres_check_min_samples muss >= 2 sein")

    # --- Kopplungen nach Firmware-Analyse 17.10.2026 (brauchen alle Werte) ---
    # arm_settle_s >= Totzeit × Reserve (beide Modi, Stock-Werte: 0,15 s).
    validate_arm_settle(cfg)
    if cfg["mode"] == "probe":
        # Nur im Probe-Hauptweg stoppt der MCU-Trigger Z: Mindest-
        # geschwindigkeit + Endstop-Pin '^!PC7' (Pull-Up wie Stock).
        err = check_probe_speed(cfg["speed"], cfg)
        if err:
            raise ValueError("speed: %s" % err)
        sw = validate_step_swap_pin(cfg)
        cfg["step_swap_pull"] = CREALITY_PULL.get(sw["pullup"], "?")
    else:
        # mode steps: kein Endstop auf PC7, nur config_prtouch_step (setzt
        # immer Pull-Up, B 0x08004910). Mindestgeschwindigkeit seit
        # 17.10.2026 auch hier (probe_speed): die Düse ist auch im Rückfall-
        # weg scharf und friert ihren Puffer erst bei eigener Auslösung ein;
        # deren Anstiegsdetektor braucht dieselbe Kraftrampe (ANNAHME-Grenze).
        err = check_probe_speed(cfg["probe_speed"], cfg)
        if err:
            raise ValueError("probe_speed: %s" % err)
        cfg["step_swap_pull"] = "Pull-Up (nur config_prtouch_step)"
    rate, pga = cs1237_rate_pga(cfg["cfg_regs"])
    cfg["pres_rate_hz"] = rate
    cfg["pres_pga"] = pga
    cfg["pres_period_s"] = pres_sample_period_s(cfg)
    cfg["pres_dead_time_s"] = pres_dead_time_s(cfg)
    cfg["arm_settle_min_s"] = min_arm_settle_s(cfg)
    cfg["speed_min"] = min_probe_speed(cfg)
    return cfg


def parse_klipper_config(config):
    """Klipper-ConfigWrapper -> parse_options (ANNAHME zur API).

    Wie k2_motor_init.parse_klipper_config: get_options()/get/getint/
    getfloat/getboolean tolerant lesen. FakeConfig in Tests bietet dieselben.
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

    if not keys and isinstance(config, dict):
        return parse_options(config)
    for k in keys:
        opts[k.lower()] = _g(k, "")
    # Fix-Keys nachholen, falls get_options leer war:
    for k in ("allow_move", "step_mm", "probe_speed", "lift_speed",
              "max_travel_mm", "z_lift_mm", "probe_timeout_s",
              "contact_thresh", "emergency_thresh", "rest_max_dif",
              "rest_max_dif_probe", "rest_retries",
              "main_mcu", "nozzle_mcu", "pres_idx", "pres_swap_pin",
              "step_swap_pin", "pres_cs0_pin", "pres_clk_pin", "pres_sdo_pin",
              "pres_clk_sdo_swap", "cfg_regs", "acq_tick", "aqc_tick",
              "ned_tftr", "ned_hftr", "ned_lftr", "ned_lftr_scale",
              "min_hold", "max_hold", "add_hold", "lmt_dead",
              "packing", "tick_format", "data_format", "tick_size", "data_size",
              "clock_freq", "step_dist_mm", "retry_dx", "retry_dy",
              "retry_lift_mm", "temp_nozzle_warn", "temp_bed_warn",
              "temp_tolerance", "hot_probe_warn", "hot_probe_margin",
              "fan_damping", "fan_damping_speed", "fan_damping_settle_s",
              "shutdown_on_failure",
              # Probe-Hauptweg + Stock-Stil-Sampling (16.10.2026):
              "mode", "speed", "z_offset", "position_endstop",
              "probe_max_travel_mm", "arm_settle_s", "swap_check",
              "rest_check", "diag_readout", "pres_cfg_regs",
              "pres_acq_tkms", "step_acq_tkms", "pres_tri_fter",
              "pres_tri_hold", "pres_ded_tkms", "rest_min_consec",
              "stepper_x_oid", "stepper_y_oid", "stepper_z_oid",
              "can_brk_min_best", "can_brk_min_step", "can_brk_max_zoft",
              "can_brk_scope",
              # Absicherung Firmware-Analyse 17.10.2026:
              "rest_filt_max_frac",
              # Rückfallweg-Kontaktkriterium (README B.3):
              "contact_criterion", "contact_steps", "contact_step_min",
              # Sensor-Selbsttest KTWO_PRTOUCH_CHECK (apax-Analyse):
              "pres_check", "pres_check_hold", "pres_check_max_s",
              "pres_check_duration_s", "pres_check_extrude_max_mm",
              "pres_check_extrude_speed", "pres_check_min_temp",
              "pres_check_rate_tol", "pres_check_skip",
              "pres_check_bad_frac", "pres_check_min_samples",
              "pres_check_extruder"):
        if k not in opts:
            v = _g(k, None)
            if v is not None and str(v) != "":
                opts[k] = v
    return parse_options(opts)


def ned_lftr_mcu_value(cfg):
    """MCU-Wert ned_lftr (BELEGT: round(pres_tri_fter[2] * 1000), 0.8 -> 800;
    schon in parse_options berechnet)."""
    return int(cfg["ned_lftr"])


def build_start_pres_tuple(cfg, oid):
    """(oid, cfg_regs, acq_tick, ned_tftr, ned_hftr, ned_lftr_u,
    min_hold, max_hold, add_hold, lmt_dead) für CMD_START_PRES."""
    return (int(oid), int(cfg["cfg_regs"]), int(cfg["acq_tick"]),
            int(cfg["ned_tftr"]), int(cfg["ned_hftr"]),
            int(ned_lftr_mcu_value(cfg)),
            int(cfg["min_hold"]), int(cfg["max_hold"]),
            int(cfg["add_hold"]), int(cfg["lmt_dead"]))


def build_start_step_tuple(cfg, oid):
    """(oid, aqc_tick) für CMD_START_STEP (Tippfehler aqc exakt)."""
    return (int(oid), int(cfg["aqc_tick"]))


# =====================================================================
# Dekodierung (ANNAHME u32-LE/s32-LE, zentral konfigurierbar)
# =====================================================================

def decode_int_list(raw, fmt, size, count=None):
    """Roh-Bytes -> int-Liste (struct, z.B. '<I'/4 oder '<i'/4)."""
    b = bytes(raw)
    if len(b) % int(size) != 0:
        raise ValueError("Byte-Länge %d nicht teilbar durch %d"
                         % (len(b), int(size)))
    n = len(b) // int(size)
    if count is not None and n != int(count):
        raise ValueError("erwartet %d Samples, erhalten %d"
                         % (int(count), n))
    out = []
    for i in range(n):
        (v,) = struct.unpack_from(str(fmt), b, i * int(size))
        out.append(int(v))
    return out


def decode_packed(raw):
    """Gepackter MCU-Puffer -> int-Liste (BELEGT live 16.10.2026).

    Format (ticks UND datas von resault_prtouch_pres, 315+3/318 Paketen
    exakt, Live-Lesetest 16.10.2026):
      [n][Größenmaske, ceil(n/4) Byte, Big-Endian]
      [Wert 0 absolut][Wert 1..n-1 als Delta zum Vorgänger]
    Je Sample i liefert die Maske 2 Bit (LSB zuerst): Länge = Feld+1 Byte,
    jeder Wert little-endian mit Vorzeichen. Beispiel ticks:
    08 aaab | 4B Startzeit | 7× 3B Delta (~94 900 Ticks = 0,79 ms).
    """
    b = bytes(raw)
    if not b:
        return []
    n = b[0]
    mb = (n + 3) // 4
    if len(b) < 1 + mb:
        raise ValueError("gepackter Puffer zu kurz: %d Byte" % len(b))
    mask = int.from_bytes(b[1:1 + mb], "big")
    p = 1 + mb
    out = []
    for i in range(n):
        sz = ((mask >> (2 * i)) & 3) + 1
        if p + sz > len(b):
            raise ValueError("gepackter Puffer endet bei Sample %d" % i)
        v = int.from_bytes(b[p:p + sz], "little", signed=True)
        p += sz
        out.append(v if i == 0 else out[-1] + v)
    if p != len(b):
        raise ValueError("gepackter Puffer: %d Restbytes" % (len(b) - p))
    return out


def _decode_buf(raw, cfg, fmt, size):
    if isinstance(raw, (list, tuple)):
        return [int(x) for x in raw]  # schon dekodiert (merge_ring)
    if cfg.get("packing", "packed") == "packed":
        return decode_packed(raw)
    return decode_int_list(raw, fmt, size)


def decode_ticks(raw, cfg):
    """ticks-Bytes -> Tick-Liste (packed BELEGT; struct = alte Annahme).
    Gepackte Ticks sind kumuliert -> auf u32 begrenzen (MCU-Uhr läuft über)."""
    if isinstance(raw, (list, tuple)):
        return [int(x) & 0xFFFFFFFF for x in raw]  # schon dekodiert
    if cfg.get("packing", "packed") == "packed":
        return [t & 0xFFFFFFFF for t in decode_packed(raw)]
    return decode_int_list(raw, cfg["tick_format"], cfg["tick_size"],
                           count=PRES_N if len(bytes(raw)) // int(cfg["tick_size"]) == PRES_N else None)


def decode_pres_datas(raw, cfg):
    """datas-Bytes (pres) -> Rohwert-Liste (packed BELEGT)."""
    return _decode_buf(raw, cfg, cfg["data_format"], cfg["data_size"])


def decode_step_counts(raw, cfg):
    """datas-Bytes (step) -> Zählerliste (ANNAHME: gleiche Packung wie pres,
    Main-MCU-Seite live noch nicht mitgeschnitten)."""
    return _decode_buf(raw, cfg, cfg["data_format"], cfg["data_size"])


def merge_ring(ticks, datas, n=PRES_N):
    """Blöcke aus dem 64er-Ringpuffer zusammenführen (BELEGT live 16.10.):
    Duplikate (gleicher Tick) entfernen, nach Tick sortieren (u32-Überlauf
    relativ zum ersten Sample), die jüngsten n behalten."""
    if len(ticks) != len(datas):
        raise ValueError("ticks/datas ungleich lang: %d/%d"
                         % (len(ticks), len(datas)))
    if not ticks:
        return [], []
    ref = int(ticks[0])
    seen = {}
    for t, d in zip(ticks, datas):
        seen[int(t) & 0xFFFFFFFF] = int(d)
    keyed = sorted(seen.items(),
                   key=lambda kv: (kv[0] - ref + (1 << 31)) & 0xFFFFFFFF)
    keyed = keyed[-int(n):]
    return [k for k, _ in keyed], [v for _, v in keyed]


def drop_empty(ticks, datas):
    """Einträge mit Tick 0 UND Wert 0 verwerfen: Schieberegister nach start
    noch nicht voll (B Memset bei start, Firmware-Analyse)."""
    keep = [(int(t), int(d)) for t, d in zip(ticks, datas)
            if not (int(t) == 0 and int(d) == 0)]
    return [t for t, _ in keep], [d for _, d in keep]


def read_ring_blocks(send, make_args, cfg, decode_data, n_total=RING_N):
    """64er-Schieberegister blockweise lesen (Firmware-Analyse).

    send(args) -> Antwort-Dict mit 'ticks'/'datas' (gepackt);
    make_args(idx, ln) -> Befehlsargumente. Angefragt wird wie Stock
    ln = n_total - idx; der nächste idx ist idx + n mit n = Anzahl Samples
    im Paket (7-9, 41-Byte-Grenze) – das Antwortfeld 'len' wird bewusst
    NICHT benutzt (B: bei vollständig passendem Bereich um 1 zu groß).
    Abbruch bei n = 0 oder idx >= n_total. Gibt (ticks, datas, idxs)
    zurück (idxs = angefragte Startindizes, für Tests/Diagnose).
    """
    ts, ds, idxs = [], [], []
    idx = 0
    while idx < int(n_total):
        res = send(make_args(idx, int(n_total) - idx))
        idxs.append(idx)
        t = decode_ticks(bytes(res["ticks"]), cfg)
        d = decode_data(bytes(res["datas"]), cfg)
        if len(t) != len(d):
            raise ValueError("Ringblock idx=%d: %d Ticks, aber %d Werte"
                             % (idx, len(t), len(d)))
        if not t:
            break
        ts += t
        ds += d
        idx += len(t)
    return ts, ds, idxs


def check_tick_spacing(ticks, cfg):
    """Tickabstand plausibel? -> (ok, median_s, min_s, Fehlertext|None).

    Erwartet 1/Rate aus cfg_regs (Stock 0,78125 ms, live 0,7907 ms).
    Sortierte, zusammengeführte Ticks (merge_ring): zwischen den Blöcken
    eines LAUFENDEN Registers liegen Lücken (Vielfache der Periode), daher
    Median (Periode ± REST_TICK_TOL) + Minimum (>= REST_TICK_MIN_FRAC ×
    Periode) statt Maximum.
    """
    f = float(cfg.get("clock_freq", CLOCK_FREQ))
    per = pres_sample_period_s(cfg)
    tk = [int(t) & 0xFFFFFFFF for t in ticks]
    diffs = [((b - a) & 0xFFFFFFFF) / f for a, b in zip(tk, tk[1:])]
    if not diffs:
        return False, None, None, "zu wenige Ticks für die Abstandsprüfung"
    med = float(statistics.median(diffs))
    mn = min(diffs)
    tol = REST_TICK_TOL
    if abs(med - per) > tol * per or mn < REST_TICK_MIN_FRAC * per:
        return (False, med, mn,
                "Tickabstand Median %.3f ms (Soll %.3f ms ±%d %%) / Minimum "
                "%.3f ms (Soll >= %.3f ms) – Sensor läuft nicht im Takt "
                "(~5 ms = DOUT hängt high/Sensor ab, ≪ 0,78 ms = DOUT hängt "
                "low, Faktor 2 = falsche Rate)"
                % (med * 1e3, per * 1e3, round(tol * 100), mn * 1e3,
                   REST_TICK_MIN_FRAC * per * 1e3))
    return True, med, mn, None


def ticks_to_times(ticks, freq=None):
    """MCU-ticks -> Sekunden relativ zum Fensterende (letzter = 0.0).

    BELEGT: PRES_TIME 64× -0.0498..0.0, STEP_TIME 64× -0.0244..0.0;
    Swap-synchronisiert (step_bst_time == pres_bst_time an allen 99).
    ANNAHME: beide Fenster teilen bereits die Swap-Nullpunkt-Basis
    (Düse HIGH / Main liest einmal); hier nur (t - t_last)/freq.
    """
    f = float(freq if freq else CLOCK_FREQ)
    if not ticks:
        raise ValueError("leere ticks")
    # 32-Bit-Wrap tolerant (ANNAHME clock32→clock64 wie Upstream):
    last = int(ticks[-1])
    out = []
    for t in ticks:
        d = int(t) - last
        # Wrap-Korrektur für 32-Bit-Überlauf:
        if d > 2 ** 31:
            d -= 2 ** 32
        elif d < -(2 ** 31):
            d += 2 ** 32
        out.append(float(d) / f)
    return out


def step_counts_to_mm(counts, cfg, relative=True):
    """Zähler -> mm. ANNAHME: counts * step_dist_mm; relativ zum Ende.

    BELEGT: STEP_DATA fällt z.B. 0.122→0.000 (Ende = tri_pose);
    0.122/0.0025 ≈ 49 Mikroschritte ≈ 24.4ms×5mm/s. relativ=True zieht
    den Endwert ab, sodass tri_pose ≈ 0 (wie live).
    """
    mms = [float(c) * float(cfg["step_dist_mm"]) for c in counts]
    if relative and mms:
        base = mms[-1]
        mms = [v - base for v in mms]
    return mms


def interp_linear(xs, ys, x):
    """Lineare Interpolation mit Clamp (für bst_pose)."""
    if len(xs) != len(ys) or not xs:
        raise ValueError("interp: leere Achsen")
    if x <= xs[0]:
        return float(ys[0])
    if x >= xs[-1]:
        return float(ys[-1])
    for i in range(1, len(xs)):
        if xs[i] >= x:
            x0, x1 = float(xs[i - 1]), float(xs[i])
            y0, y1 = float(ys[i - 1]), float(ys[i])
            if x1 == x0:
                return y0
            a = (x - x0) / (x1 - x0)
            return y0 + a * (y1 - y0)
    return float(ys[-1])


# =====================================================================
# Kontakterkennung nach Live-Rechenweg (BELEGT Verfahren, ANNAHME Details)
# =====================================================================

def median_int(data):
    return float(statistics.median(list(data)))


def rest_info(datas):
    """Min/max/avg/dif/len wie PRES_CHECK_INFO (BELEGT Format)."""
    vals = list(datas)
    if not vals:
        raise ValueError("leere datas")
    mn, mx = min(vals), max(vals)
    avg = sum(vals) / len(vals)
    return {"min": mn, "max": mx, "avg": avg, "dif": mx - mn,
            "len": len(vals)}


def check_rest_ok(datas, cfg, ticks=None, filt=None):
    """Sensor-Ruhetest (analog PRES_CHECK). -> (ok, info); info['errors']
    listet jede verletzte Bedingung (leer = ok).

    Rohwerte (is_src=1) – immer:
      - mind. REST_MIN_SAMPLES gültige Samples (ANNAHME),
      - dif <= rest_max_dif (ANNAHME Schranke, Default 3000; BELEGT live
        Ruhe-DIF 756-1 292),
      - kein Wert -1 (F: Sensor ab/DOUT hängt high -> MCU liest nach 5 ms
        trotzdem und erhält -1, B 0x08005224/0x080051F6) und alle Werte im
        24-Bit-Bereich (B: s32 aus 24-Bit-Zweierkomplement),
      - Werte nicht konstant (dif > 0; ANNAHME: ein laufender 24-Bit-ADC mit
        PGA 128 rauscht immer – ein abgezogener Sensor besteht sonst den
        dif-Test mit dif = 0, Firmware-Analyse).
    ticks (optional): Tickabstand ≈ 1/Rate (check_tick_spacing).
    filt (optional, is_src=0): max |gefiltert| <= rest_filt_max_frac ×
      min_hold (Firmware-Analyse; gefiltert = das, was die MCU gegen
      min_hold vergleicht).
    """
    errors = []
    vals = [int(v) for v in datas]
    if vals:
        info = rest_info(vals)
    else:
        info = {"min": 0, "max": 0, "avg": 0.0, "dif": 0, "len": 0}
    # Median = Nullpunkt der Notgrenze im Rückfallweg (mode steps).
    info["median"] = median_int(vals) if vals else None
    if len(vals) < REST_MIN_SAMPLES:
        errors.append("nur %d gültige Rohwerte (< %d) – Sampling nicht "
                      "gestartet/Puffer leer?" % (len(vals), REST_MIN_SAMPLES))
    if vals:
        if info["dif"] > float(cfg["rest_max_dif"]):
            errors.append("dif=%d > %d (min=%d max=%d avg=%.0f len=%d)"
                          % (info["dif"], cfg["rest_max_dif"], info["min"],
                             info["max"], info["avg"], info["len"]))
        n_stuck = sum(1 for v in vals if v == ADC_STUCK_VALUE)
        if n_stuck:
            errors.append("%d× Rohwert -1 (Sensor abgezogen/DOUT hängt high)"
                          % n_stuck)
        n_out = sum(1 for v in vals if not ADC24_MIN <= v <= ADC24_MAX)
        if n_out:
            errors.append("%d Rohwerte außerhalb 24 Bit" % n_out)
        if info["dif"] == 0 and len(vals) > 1:
            errors.append("Rohwerte konstant (dif=0) – kein Sensorrauschen, "
                          "Sensor/Leitung prüfen")
    if ticks is not None:
        tok, med, mn, terr = check_tick_spacing(ticks, cfg)
        info["tick_median_ms"] = None if med is None else med * 1e3
        info["tick_min_ms"] = None if mn is None else mn * 1e3
        if not tok:
            errors.append(terr)
    if filt is not None:
        fv = [int(v) for v in filt]
        lim = float(cfg.get("rest_filt_max_frac", REST_FILT_MAX_FRAC_DEFAULT)
                    ) * float(cfg["min_hold"])
        info["filt_len"] = len(fv)
        info["filt_max_abs"] = max(abs(v) for v in fv) if fv else None
        info["filt_limit"] = lim
        if len(fv) < REST_MIN_SAMPLES:
            errors.append("nur %d gültige gefilterte Werte (< %d)"
                          % (len(fv), REST_MIN_SAMPLES))
        elif info["filt_max_abs"] > lim:
            errors.append("gefilterter Kanal max |%d| > %.0f (= %.2f × "
                          "min_hold %d) – Ruherauschen zu nah an der "
                          "Auslöseschwelle" % (info["filt_max_abs"], lim,
                                               cfg.get("rest_filt_max_frac",
                                                       REST_FILT_MAX_FRAC_DEFAULT),
                                               int(cfg["min_hold"])))
    info["errors"] = errors
    return (not errors), info


def detect_contact(pres_datas, pres_times, step_mms, step_times, cfg):
    """Kontakt nach Live-Rechenweg: best_indx → step_bst_indx → zoft.

    BELEGT: CAL_TURN == best_indx, pres_bst == best, step_bst_time ==
    pres_bst_time, tri_time == 0.000, zoft = bst_pose - tri_pose
    (0.010-0.040), final = A + zoft.
    ANNAHME Filterdetails: hier Schwellwert auf Roh-/FTR-Daten
    (contact_thresh, min. rest_min_consec aufeinanderfolgende Samples),
    da exakte ned_*/CAL-Formel nur per Roh-Mitschnitt klärbar.
    Gibt dict(contact, best_indx, best_time, tri_time, step_bst_indx,
    tri_pose, bst_pose, zoft) zurück.
    """
    thresh = float(cfg["contact_thresh"])
    consec = max(1, int(cfg.get("rest_min_consec", 2)))
    datas = list(pres_datas)
    if len(datas) < 8:
        raise ValueError("zu wenig pres-Samples: %d" % len(datas))
    # Nullpunkt pro Fenster neu (BELEGT Drift: SRC-Offset -1.44Mio bis
    # -1.53Mio über Sitzung, pro Punkt neu kalibriert).
    base = median_int(datas[:32])
    devs = [abs(float(v) - base) for v in datas]
    best = None
    run = 0
    for i, d in enumerate(devs):
        if d > thresh:
            run += 1
            if run >= consec:
                best = i - consec + 1
                break
        else:
            run = 0
    if best is None:
        return {"contact": False, "baseline": base}
    best_time = float(pres_times[best]) - float(pres_times[-1])
    tri_time = 0.0
    # step_bst_indx: nächster Step-Index zu best_time (BELEGT Gleichheit
    # der Zeitbasen über Swap an allen 99 Punkten).
    bi = min(range(len(step_times)),
             key=lambda i: abs(float(step_times[i]) - float(best_time)))
    tri_pose = float(step_mms[-1])
    bst_pose = float(interp_linear(list(step_times), list(step_mms),
                                   float(best_time)))
    zoft = float(bst_pose - tri_pose)
    return {"contact": True, "baseline": base, "best_indx": int(best),
            "best_time": float(best_time), "tri_time": float(tri_time),
            "step_bst_indx": int(bi),
            "step_bst_time": float(step_times[bi]) - float(step_times[-1]),
            "tri_pose": float(tri_pose), "bst_pose": float(bst_pose),
            "zoft": float(zoft)}


def step_level(pres_datas, n=CONTACT_TAIL_N):
    """Stufenpegel eines Schritts: Median der jüngsten n Rohwerte (Fenster
    nach M400 gelesen, nach Tick sortiert). Median statt Mittel: einzelne
    Störspitzen (B.2 Punkt 4) verschieben ihn nicht."""
    vals = [int(v) for v in pres_datas][-int(n):]
    if not vals:
        raise ValueError("leere pres-Daten für den Stufenpegel")
    return median_int(vals)


def detect_contact_cumulative(levels, depths, cfg):
    """Treppen-Kriterium über mehrere Schritte (contact_criterion
    'cumulative', ANNAHME, README B.3).

    levels[0] = Ruhe-Nullpunkt vor dem ersten Schritt (Median des
    Ruhetests), levels[j] = Stufenpegel nach Schritt j (step_level);
    depths[j] = Fahrweg ab Start in mm (depths[0] = 0, steigend).
    Kontakt, wenn die letzten L >= contact_steps Stufen gleichsinnig um je
    >= contact_step_min gestiegen sind (Richtung egal, wie die Düse B)
    UND |levels[i] - levels[s-1]| > contact_thresh (s = erste Stufe der
    Folge, levels[s-1] = letzte unbelastete Stufe). Bezug ist damit die
    Stufe VOR dem Anstieg – nicht der feste Ruhe-Nullpunkt (Drift, s. B.3).
    Kontakttiefe: Gerade durch (depth, |level - Bezug|) der Stufen s..i
    (kleinste Quadrate, ANNAHME lineare Federkennlinie), Nullstelle, auf
    [depths[s-1], depths[s]] begrenzt (Kontakt zwischen letzter unbelasteter
    und erster belasteter Stufe).
    """
    k_need = int(cfg.get("contact_steps", CONTACT_STEPS_DEFAULT))
    floor = float(cfg.get("contact_step_min", CONTACT_STEP_MIN_DEFAULT))
    thresh = float(cfg["contact_thresh"])
    if len(levels) != len(depths):
        raise ValueError("levels/depths ungleich lang")
    i = len(levels) - 1
    if i < k_need:
        return {"contact": False}
    d = [float(levels[j]) - float(levels[j - 1]) for j in range(1, i + 1)]
    sign = 1.0 if d[-1] > 0 else -1.0
    s = i  # erste Stufe der gleichsinnigen Folge (Index in levels)
    while s >= 1 and d[s - 1] * sign >= floor:
        s -= 1
    s += 1
    run = i - s + 1
    if run < k_need:
        return {"contact": False, "run_len": max(run, 0)}
    base = float(levels[s - 1])
    total = abs(float(levels[i]) - base)
    if total <= thresh:
        return {"contact": False, "run_len": run, "total": total}
    xs = [float(depths[j]) for j in range(s, i + 1)]
    ys = [abs(float(levels[j]) - base) for j in range(s, i + 1)]
    n = float(len(xs))
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    slope = (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
             if sxx > 0 else 0.0)
    lo, hi = float(depths[s - 1]), float(depths[s])
    if slope > 0:
        dc = mx - my / slope  # Nullstelle der Geraden
        dc = min(max(dc, lo), hi)
    else:
        dc = lo
    return {"contact": True, "run_start": int(s), "run_len": int(run),
            "baseline": base, "total": float(total),
            "slope_per_mm": float(slope), "contact_depth": float(dc),
            "sign": int(sign)}


def check_can_brk(best_indx, step_bst_indx, zoft, cfg):
    """can_brk-Plausibilität (ANNAHME aus genau 1 Beobachtung T58).

    T58 (einziges False in 99): best 53 (frühester), step 42 (frühester),
    zoft 0.040 (größter), final 0.088 deutlich über Nachbarschaft.
    Regel (UND, damit Randwerte nicht einzeln verwerfen):
      False genau wenn best<=53 UND step<=42 UND zoft>=0.040.
    Nachbarschafts-Vergleich (final deutlich über Umgebung) braucht
    Mesh-Kontext und läuft in probe_mesh (ANNAHME Schranke 0.05mm).
    """
    if (int(best_indx) <= int(cfg["can_brk_min_best"])
            and int(step_bst_indx) <= int(cfg["can_brk_min_step"])
            and float(zoft) >= float(cfg["can_brk_max_zoft"]) - 1e-12):
        return False, ("frühester Trigger (best=%d, step=%d) + größtes zoft "
                       "(%.3f) wie Ausreißer T58" % (best_indx,
                                                     step_bst_indx, zoft))
    return True, "plausibel"


def can_brk_applies(context, cfg):
    """Wird die can_brk-Regel in diesem Antast-Kontext ausgewertet?

    context: 'mesh' (KTWO_PRTOUCH_MESH bzw. PROBE_STEPS CONTEXT=mesh),
    'home' (KTWO_PRTOUCH_HOME_Z), 'single' (Einzel-/Reinigungsantastung).
    Stock (Live-Abgleich A8): nur im Bereichs-Mesh -> Scope 'mesh'.
    """
    ctx = str(context or "single").lower()
    if ctx not in PROBE_CONTEXTS:
        raise ValueError("Antast-Kontext muss %s sein: %r"
                         % ("/".join(PROBE_CONTEXTS), context))
    scope = cfg.get("can_brk_scope", "mesh")
    if scope == "off":
        return False
    if scope == "all":
        return True
    return ctx == "mesh"


def emergency_config_warnings(cfg):
    """Plausibilitätshinweise Notgrenze/step_mm (Live-Abgleich A7,
    Live-Werte nach E1: Spitze 21 815, 2 270 Counts/µm).

    Keine harten Fehler (Steifigkeit nur dynamisch geschätzt, ANNAHME) –
    eine zu niedrige Notgrenze ist fail-safe ('emergency' ohne Messwert).
    Zusätzlich „knapp“-Hinweise, wenn Kontaktschritt bzw. Live-Spitze
    >= EMERGENCY_TIGHT_FRAC (90 %) der Notgrenze erreichen.
    """
    out = []
    em = float(cfg["emergency_thresh"])
    jump = float(cfg["step_mm"]) * 1000.0 * LIVE_STIFF_MAX_PER_UM
    tight = EMERGENCY_TIGHT_FRAC * em
    if jump >= em:
        out.append("step_mm %.3f × bis %.0f Counts/µm (Live, dynamisch) ≈ "
                   "%.0f Counts je Kontaktschritt >= emergency_thresh %.0f – "
                   "ein normaler Kontakt kann als 'emergency' enden "
                   "(step_mm verkleinern, Default 0,01)"
                   % (cfg["step_mm"], LIVE_STIFF_MAX_PER_UM, jump, em))
    elif jump >= tight:
        out.append("step_mm %.3f × bis %.0f Counts/µm ≈ %.0f Counts je "
                   "Kontaktschritt liegt nur %.0f %% unter emergency_thresh "
                   "%.0f (knapp, < %.0f %% Reserve)"
                   % (cfg["step_mm"], LIVE_STIFF_MAX_PER_UM, jump,
                      100.0 * (1.0 - jump / em), em,
                      100.0 * (1.0 - EMERGENCY_TIGHT_FRAC)))
    if em <= LIVE_PEAK_MAX:
        out.append("emergency_thresh %.0f <= größte normale Stock-Spitze %d "
                   "(Live) – normale Antastungen können die Notgrenze "
                   "auslösen" % (em, LIVE_PEAK_MAX))
    elif LIVE_PEAK_MAX >= tight:
        out.append("emergency_thresh %.0f nur %.0f %% über der größten "
                   "normalen Stock-Spitze %d (knapp, < %.0f %% Reserve)"
                   % (em, 100.0 * (em / LIVE_PEAK_MAX - 1.0), LIVE_PEAK_MAX,
                      100.0 * (1.0 - EMERGENCY_TIGHT_FRAC)))
    return out


def hot_probe_warning(nozzle_temp, cfg):
    """Heiß-Antastung (Live-Abgleich E2): Text, wenn die Düse beim
    Scharfschalten > temp_nozzle_warn (= g28_ext_temp 140) +
    hot_probe_margin (Default 10) °C ist, sonst None. Reine Meldung –
    kein Abbruch, kein Heizen/Kühlen. hot_probe_warn: False → immer None."""
    if not cfg.get("hot_probe_warn", True) or nozzle_temp is None:
        return None
    ref = float(cfg.get("temp_nozzle_warn", STOCK_TEMP_NOZZLE))
    lim = ref + float(cfg.get("hot_probe_margin", HOT_PROBE_MARGIN_DEFAULT))
    nz = float(nozzle_temp)
    if nz <= lim:
        return None
    return ("Düse %.0f°C > %.0f°C (Antast-Temperatur %.0f + %.0f) beim "
            "Scharfschalten – heiße Antastung: Störung vor dem Kontakt "
            "(Stock-Reinigungsfahrt 157–188 °C bis 3 985 Counts, 0,4 %% unter "
            "min_hold) kann fehlauslösen; vorher auf g28_ext_temp abkühlen "
            "(nur Hinweis)"
            % (nz, lim, ref, lim - ref))


class FanDamper:
    """Hotend-Lüfter während einer Antast-Sitzung drosseln (Stock
    DISTUURB_CTL: „SET_FANS=0.60 bak_fans=1.00“ bei multi_probe_begin).

    get_fans() -> Liste (name, heater_fan). Gebraucht werden die Upstream-
    Attribute von extras/heater_fan.py: fan_speed, last_speed und
    fan.set_speed(value). Dessen Timer setzt die Drehzahl nur, wenn sich der
    errechnete Wert (= fan_speed, solange heiß) ändert – deshalb wird
    fan_speed selbst abgesenkt und ein laufender Lüfter sofort gedrosselt.
    restore() stellt fan_speed zurück; den Rest erledigt der Timer (≤ 1 s).
    """

    def __init__(self, get_fans, speed, settle_s, pause, info=None):
        self.get_fans = get_fans
        self.speed = float(speed)
        self.settle_s = float(settle_s)
        self.pause = pause
        self.info = info
        self.saved = []  # [(name, heater_fan, fan_speed vorher)]
        self.active = False

    def _say(self, msg):
        if self.info is not None:
            try:
                self.info(msg)
            except Exception:
                pass

    def reduce(self):
        """Drosseln (idempotent). Rückgabe: Namen der gebremsten Lüfter."""
        if self.active:
            return []
        self.active = True
        slowed = []
        for name, hf in self.get_fans():
            old = float(hf.fan_speed)
            self.saved.append((name, hf, old))
            new = min(old, self.speed)
            hf.fan_speed = new
            if float(hf.last_speed) > new:
                hf.last_speed = new
                hf.fan.set_speed(new)
                slowed.append("%s %.2f → %.2f" % (name, old, new))
        if slowed:
            self._say("[k2_prtouch] Lüfterdämpfung beim Antasten: %s (Stock "
                      "DISTUURB_CTL)" % ", ".join(slowed))
            if self.settle_s > 0.0:
                self.pause(self.settle_s)
        return slowed

    def restore(self):
        """Ursprüngliche Drehzahl zurück (idempotent)."""
        if not self.active:
            return
        for _name, hf, old in self.saved:
            hf.fan_speed = old
            if 0.0 < float(hf.last_speed) < old:
                hf.last_speed = old
                hf.fan.set_speed(old)
        self.saved = []
        self.active = False


def check_emergency(pres_datas, baseline0, cfg):
    """Notgrenze: max |roh - Nullpunkt| > emergency_thresh → True."""
    m = max(abs(float(v) - float(baseline0)) for v in pres_datas)
    return m > float(cfg["emergency_thresh"]), float(m)


# =====================================================================
# Gekapselte MCU-/Toolhead-Zugriffe (Logik testbar, vgl. k2_box.py)
# =====================================================================


def reactor_sleep(reactor):
    """Relative Pause für Klipper: reactor.pause() erwartet eine ABSOLUTE
    Weckzeit (upstream reactor.py: pause(waketime)). Ohne Reactor -> time.sleep."""
    if reactor is None or not hasattr(reactor, "pause"):
        return time.sleep
    return lambda s: reactor.pause(reactor.monotonic() + float(s))

class PrtouchMcuOps:
    """Protokoll pres-Seite (Düse) + step-Seite (Main)."""

    def test_swap(self):
        """TEST_SWAP-Äquivalent -> dict(py_ver, pres_ver, step_ver)."""
        raise NotImplementedError

    def start_sampling(self):
        raise NotImplementedError

    def read_pres(self):
        """-> (ticks_bytes, datas_bytes) je 64 Samples."""
        raise NotImplementedError

    def read_step(self):
        """-> (ticks_bytes, datas_bytes) je 64 Samples."""
        raise NotImplementedError

    def stop_sampling(self, ok=True):
        raise NotImplementedError

    def read_rest_pres(self):
        """Ruhefenster ohne Fahrt (alt; seit 17.10.2026 ungenutzt – ein
        Lesen VOR start_prtouch_pres liefert ein leeres Register)."""
        return self.read_pres()


class ToolheadOps:
    """Protokoll Z-/XY-Bewegung + M400 + Temperatur (ANNAHME Upstream)."""

    def get_z(self):
        raise NotImplementedError

    def get_xy(self):
        raise NotImplementedError

    def move_down(self, step_mm, speed):
        raise NotImplementedError

    def move_to_xy(self, x, y, speed):
        raise NotImplementedError

    def lift(self, dz_mm, speed):
        raise NotImplementedError

    def wait_moves(self):
        """M400-Äquivalent nach JEDEM Schritt (S1)."""
        raise NotImplementedError

    def get_temps(self):
        """-> (nozzle_temp|None, bed_temp|None). Nur Warnung (S5)."""
        return (None, None)


class KlippyPresMcu(PrtouchMcuOps):
    """Echte pres-Anbindung (ANNAHMEN s. README-k2_prtouch.md A.5).

    - oid via mcu.create_oid() in _build_config (vor connect).
    - config via mcu.add_config_cmd(CMD_CONFIG_PRES) wie Stock (is_init
      False, per Emulation des Stock-Moduls belegt 16.10.2026).
    - start/read/stop via mcu.lookup_query_command(cmd, resp, oid=...)
      nach klippy:connect (ANNAHME Query-Semantik wie Stock-Tupel-Slots).
    - Pins via ppins.lookup_pin/mcu.get_enumeration aufgelöst (wie
      KlippyGpio in k2_motor_init.py).
    """

    def __init__(self, printer, mcu, cfg, oid, pin_nums):
        self.printer = printer
        self.mcu = mcu
        self.cfg = cfg
        self.oid = int(oid)
        self.pin_nums = dict(pin_nums)
        self._q_start = self._q_read = self._q_stop = None

    def handle_connect(self):
        mcu = self.mcu
        self._q_start = mcu.lookup_query_command(
            CMD_START_PRES, RESP_ACK, oid=self.oid)
        self._q_read = mcu.lookup_query_command(
            CMD_READ_PRES, RESP_PRES, oid=self.oid)
        self._q_stop = mcu.lookup_query_command(
            CMD_STOP_PRES, RESP_ACK, oid=self.oid)

    def set_swap(self, level):
        """Swap-Leitung setzen: stop_prtouch_pres sta_swap=level (BELEGT per
        Emulation 16.10.: Stock-TEST_SWAP sendet sta_swap 0, dann 1;
        Antwort ack_prtouch expar1 = PRES_VER, live 71)."""
        r = self._q_stop.send([self.oid, 1 if level else 0])
        return {"err": int(r.get("err", 0)), "expar0": int(r.get("expar0", 0)),
                "expar1": int(r.get("expar1", 0))}

    def test_swap(self):
        # Echter Ablauf in K2PrtouchController.test_swap (set_swap/read_swap).
        return {"py_ver": HOST_PY_VER, "pres_ver": None, "step_ver": None,
                "note": "s. K2PrtouchController.test_swap"}

    def start_sampling(self):
        """start_prtouch_pres (Antast-Modus = Düse scharf) -> ack-Dict.
        Antwort ack_prtouch (BELEGT live 16.10.: err 0 bei Erfolg)."""
        args = build_start_pres_tuple(self.cfg, self.oid)
        return _ack_dict(self._q_start.send(list(args)))

    def disarm(self):
        """Düse entschärfen: stop_prtouch_pres sta_swap=1 (BELEGT
        Fingerdruck-Test 16.10.: setzt PA15/PC7 wieder auf 1). Wirft bei
        Kommunikationsfehler (Aufrufer entscheidet)."""
        return _ack_dict(self._q_stop.send([self.oid, 1]))

    def read_frozen(self):
        """Diagnose nach dem Entschärfen, Stock-Reihenfolge (BELEGT per
        Emulation 16.10.): read_prtouch_pres [oid, 2, 0, 0, 0] (Status,
        len 0), dann is_src=0 (FTR) und is_src=1 (SRC) mit len = 64 - idx.
        Abweichend von Stock (idx fest 0,8,…,56) ist der nächste idx = idx + n
        (n aus dem Paket, 7-9 Samples, Firmware-Analyse) -> keine Lücken."""
        status = self._q_read.send([self.oid, 2, 0, 0, 0])
        out = {"status": {k: status.get(k) for k in
                          ("tri_chxs", "buf_len", "idx", "len")
                          if hasattr(status, "get")}}
        for name, is_src in (("ftr", 0), ("src", 1)):
            out[name] = self._read_ring(is_src)
        return out

    def _read_ring(self, is_src):
        """Ein Register (is_src 1 = roh, 0 = gefiltert) blockweise lesen,
        leere Einträge (Tick 0 + Wert 0) verwerfen, nach Tick sortieren."""
        oid = self.oid
        ts, ds, _ = read_ring_blocks(
            self._q_read.send,
            lambda idx, ln: [oid, int(is_src), 0, idx, ln],
            self.cfg, decode_pres_datas, PRES_N)
        ts, ds = drop_empty(ts, ds)
        return merge_ring(ts, ds, PRES_N)

    def read_pres(self):
        # BELEGT live 16.10.2026: is_src=1 = Rohwerte (Skala wie Stock-
        # READ_PRES, ~-1,428e6); ch=0. BELEGT Firmware-Analyse: idx = Index im
        # Schieberegister, 7-9 Samples je Antwort -> read_ring_blocks
        # (idx += n), merge_ring. Rückgabe: Listen (ticks, datas).
        return self._read_ring(1)

    def read_pres_filtered(self):
        """is_src=0: gefilterte Werte – genau das, was die Düse gegen
        min_hold vergleicht (Firmware-Analyse, 0x08003F92)."""
        return self._read_ring(0)

    def read_status(self):
        """read_prtouch_pres [oid, 2, 0, 0, 0] -> dict(tri_chxs, …).

        B (Firmware-Analyse, 0x08004060-0x0800407A): is_src >= 2 = nur Status,
        Antwort ohne Daten; tri_chxs = Auslösebits. B: tri_chxs
        wird je Sample auf 0 gesetzt und bei Auslösung gesetzt; danach
        acq_tick := 0 (Puffer eingefroren) -> bleibt bis zum nächsten start
        stehen. Gleiche Stelle zieht PA15 (-> PC7) auf 0 (0x08008192).
        ANNAHME: die Statusabfrage hat während laufender Abtastung keine
        Nebenwirkung (Handler liest nur; Stock fragt sie nur nach stop).
        Rückfallweg (mode steps): Ersatz für die PC7-Abfrage im scharfen
        Zustand und zusätzliches MCU-Kontaktsignal je Schritt."""
        st = self._q_read.send([self.oid, 2, 0, 0, 0])
        get = st.get if hasattr(st, "get") else (lambda k, d=None: d)
        return {"tri_chxs": int(get("tri_chxs", 0) or 0),
                "buf_len": get("buf_len"), "idx": get("idx"),
                "len": get("len")}

    def stop_sampling(self, ok=True):
        # BELEGT 16.10.2026: sta_swap=1 = Leitung high (Ruhe, PC7=1);
        # sta_swap=0 zöge sie auf „ausgelöst“. Daher immer 1 (Stock-
        # probe_finish ebenso); ok bleibt nur als API-Parameter.
        _ = ok
        try:
            return self._q_stop.send([self.oid, 1])
        except Exception:
            return None


def _ack_dict(r):
    """ack_prtouch-Antwort -> dict(err, expar0, expar1) (fehlende = 0)."""
    get = r.get if hasattr(r, "get") else (lambda k, d=None: d)
    return {"err": int(get("err", 0) or 0),
            "expar0": int(get("expar0", 0) or 0),
            "expar1": int(get("expar1", 0) or 0)}


class KlippyStepMcu(PrtouchMcuOps):
    """Echte step-Anbindung (Main-MCU, ANNAHMEN wie pres)."""

    def __init__(self, printer, mcu, cfg, oid):
        self.printer = printer
        self.mcu = mcu
        self.cfg = cfg
        self.oid = int(oid)
        self._q_start = self._q_read = self._q_stop = self._q_cont = None

    def handle_connect(self):
        mcu = self.mcu
        # BELEGT (Emulation Stock-probe_prepare 16.10.2026): Antwort auf
        # cont_prtouch_step ist resault_prtouch_step_cnt (cnt_x/y/z).
        # ANNAHME: start/stop_prtouch_step antworten mit ack_prtouch.
        self._q_cont = mcu.lookup_query_command(
            CMD_CONT_STEP, RESP_STEP_CNT, oid=self.oid)
        self._q_start = mcu.lookup_query_command(
            CMD_START_STEP, RESP_ACK, oid=self.oid)
        self._q_read = mcu.lookup_query_command(
            CMD_READ_STEP, RESP_STEP, oid=self.oid)
        self._q_stop = mcu.lookup_query_command(
            CMD_STOP_STEP, RESP_ACK, oid=self.oid)

    def read_swap(self):
        """Swap-Pegel lesen: stop_prtouch_step (BELEGT per Emulation 16.10.:
        ack expar0 = gelesener Pegel, expar1 = STEP_VER, live 71)."""
        r = self._q_stop.send([self.oid])
        return {"err": int(r.get("err", 0)), "expar0": int(r.get("expar0", 0)),
                "expar1": int(r.get("expar1", 0))}

    def test_swap(self):
        return {"py_ver": HOST_PY_VER, "pres_ver": None, "step_ver": None,
                "note": "s. K2PrtouchController.test_swap"}

    def start_sampling(self):
        """start_prtouch_step [oid, aqc_tick] -> ack-Dict (ANNAHME Antwort
        ack_prtouch wie bei pres)."""
        return _ack_dict(self._q_start.send(
            list(build_start_step_tuple(self.cfg, self.oid))))

    def cont_step(self):
        """cont_prtouch_step (Stock-probe_prepare Schritt 1) -> Zähler."""
        r = self._q_cont.send([self.oid])
        get = r.get if hasattr(r, "get") else (lambda k, d=None: d)
        return {"cnt_x": get("cnt_x"), "cnt_y": get("cnt_y"),
                "cnt_z": get("cnt_z")}

    def disarm(self):
        """stop_prtouch_step (Stock-probe_finish Schritt 1)."""
        return _ack_dict(self._q_stop.send([self.oid]))

    def read_frozen(self):
        """Diagnose (Stock-Reihenfolge, BELEGT per Emulation): read_prtouch_
        step [oid, 0, 0], dann len = 64 - idx mit idx += n (Firmware-Analyse)."""
        self._q_read.send([self.oid, 0, 0])
        return {"step": self._read_ring()}

    def _read_ring(self):
        oid = self.oid
        ts, ds, _ = read_ring_blocks(
            self._q_read.send, lambda idx, ln: [oid, idx, ln],
            self.cfg, decode_step_counts, STEP_N)
        ts, ds = drop_empty(ts, ds)
        return merge_ring(ts, ds, STEP_N)

    def read_pres(self):
        raise NotImplementedError("step-MCU hat kein pres")

    def read_step(self):
        # BELEGT Firmware-Analyse: derselbe Encoder wie pres (0x08003E88),
        # Schieberegister 64, 7-9 Samples je Antwort -> idx += n.
        return self._read_ring()

    def stop_sampling(self, ok=True):
        try:
            return self._q_stop.send([self.oid])
        except Exception:
            return None


class KlippyToolhead(ToolheadOps):
    """Echte Toolhead-Anbindung (ANNAHME Upstream-API).

    - manual_move(coord, speed) + wait_moves() + get_position().
      Fallback: gcode.run_script('G1 ...' + 'M400').
    - Temperaturen via extruder/heater_bed get_temp() (nur Warnung).
    """

    def __init__(self, printer):
        self.printer = printer

    def _th(self):
        return self.printer.lookup_object("toolhead")

    def get_z(self):
        try:
            return float(self._th().get_position()[2])
        except Exception:
            gcode = self.printer.lookup_object("gcode")
            return float(gcode.last_position[2])  # ANNAHME Fallback

    def get_xy(self):
        p = self._th().get_position()
        return float(p[0]), float(p[1])

    def _move(self, coord, speed):
        th = self._th()
        if hasattr(th, "manual_move"):
            th.manual_move(list(coord), float(speed))
            return
        # Fallback G-Code (ANNAHME):
        gcode = self.printer.lookup_object("gcode")
        gcode.run_script("G1 X%.3f Y%.3f Z%.4f F%.0f"
                         % (coord[0], coord[1], coord[2],
                            float(speed) * 60.0))

    def move_down(self, step_mm, speed):
        if float(step_mm) <= 0:
            raise ValueError("step_mm muss > 0 sein")
        p = list(self._th().get_position())
        p[2] = float(p[2]) - float(step_mm)
        self._move(p, speed)

    def move_to_xy(self, x, y, speed):
        p = list(self._th().get_position())
        self._move([float(x), float(y), float(p[2]), float(p[3])
                    if len(p) > 3 else 0.0][:3] if len(p) < 4 else
                    [float(x), float(y), float(p[2]), float(p[3])], speed)

    def lift(self, dz_mm, speed):
        p = list(self._th().get_position())
        p[2] = float(p[2]) + float(dz_mm)
        self._move(p, speed)

    def wait_moves(self):
        th = self._th()
        if hasattr(th, "wait_moves"):
            th.wait_moves()
            return
        self.printer.lookup_object("gcode").run_script("M400")

    def get_temps(self):
        nz, bd = None, None
        try:
            ex = self.printer.lookup_object("extruder")
            t, _tgt = ex.get_temp(time.time())
            nz = float(t)
        except Exception:
            pass
        try:
            hb = self.printer.lookup_object("heater_bed")
            t, _tgt = hb.get_temp(time.time())
            bd = float(t)
        except Exception:
            pass
        return nz, bd


# =====================================================================
# Controller – reine Antast-Logik (ohne Klipper importierbar)
# =====================================================================

class K2PrtouchError(RuntimeError):
    pass


class K2PrtouchController:
    """Schrittweise, host-seitig gestoppte Antastung (S1-S5).

    pres_mcu/step_mcu: PrtouchMcuOps; toolhead: ToolheadOps.
    """

    def __init__(self, cfg, pres_mcu, step_mcu, toolhead, pause=None,
                 logger=None):
        self.cfg = dict(cfg)
        self.pres = pres_mcu
        self.step = step_mcu
        self.tool = toolhead
        self.pause = pause if pause is not None else time.sleep
        self.log = logger if logger is not None else self._print_log()
        self.last_result = None
        self.waits = 0
        # Gemeinsamer Arm/Ruhetest/Disarm-Code mit dem Probe-Hauptweg.
        self.core = self._make_core()

    class _print_log:
        def info(self, msg):
            print(msg)

        def error(self, msg):
            print("FEHLER: %s" % msg)

    # --- Helfer -------------------------------------------------------
    def _require_move(self, name):
        if not self.cfg.get("allow_move"):
            raise PermissionError(
                "%s braucht allow_move: True (Default False → keine "
                "Z-Bewegung, nur Selbsttest/Lesen; DRY_RUN=1 für Plan)"
                % name)

    def describe_plan(self):
        c = self.cfg
        lines = ["K2-PrTouch-Plan (Trockenlauf, nichts bewegt/abgetastet)"]
        lines.append("allow_move=%s step_mm=%.3f (max %.2f) speed=%.1f "
                     "(max %.1f) max_travel=%.2f lift=%.2f timeout=%.0fs" % (
                         c["allow_move"], c["step_mm"], STEP_MM_MAX,
                         c["probe_speed"], SPEED_MAX, c["max_travel_mm"],
                         c["z_lift_mm"], c["probe_timeout_s"]))
        lines.append("pres: cfg_regs=%d acq_tick=%d ned=(%d,%d,%d/%d) "
                     "holds=(%d,%d,%d) lmt_dead=%d" % (
                         c["cfg_regs"], c["acq_tick"], c["ned_tftr"],
                         c["ned_hftr"], c["ned_lftr"],
                         ned_lftr_mcu_value(c), c["min_hold"],
                         c["max_hold"], c["add_hold"], c["lmt_dead"]))
        lines.append("step: aqc_tick=%d (Stock 46875 = 0,390625 ms)"
                     % c["aqc_tick"])
        lines.append("Düse: %.0f Hz/PGA %d (%.4f ms), Totzeit %.0f ms, "
                     "arm_settle_s %.3f (min %.3f), speed min %.2f mm/s "
                     "(ANNAHME), PC7 %s" % (
                         c["pres_rate_hz"], c["pres_pga"],
                         c["pres_period_s"] * 1e3,
                         c["pres_dead_time_s"] * 1e3, c["arm_settle_s"],
                         c["arm_settle_min_s"], c["speed_min"],
                         c.get("step_swap_pull", "?")))
        lines.append("Pins: pres_swap=%s step_swap=%s clk=%s sdo=%s "
                     "(clk/sdo PB13/PB14 BELEGT)" % (
                         c["pres_swap_pin"], c["step_swap_pin"],
                         c["pres_clk_pin"], c["pres_sdo_pin"]))
        lines.append("Schwellen: contact=%.0f emergency=%.0f rest_dif=%.0f "
                     "(alle ANNAHME)" % (
                         c["contact_thresh"], c["emergency_thresh"],
                         c["rest_max_dif"]))
        for w in emergency_config_warnings(c):
            lines.append("WARNUNG: %s" % w)
        lines.append("can_brk: Scope %s (Stock: nur Bereichs-Mesh)"
                     % c.get("can_brk_scope", "mesh"))
        if c.get("contact_criterion", "window") == "cumulative":
            lines.append("Kontaktkriterium: window ODER cumulative (Treppe: "
                         ">= %d Stufen je >= %.0f, Summe > %.0f; ANNAHME, "
                         "README B.3)" % (c["contact_steps"],
                                          c["contact_step_min"],
                                          c["contact_thresh"]))
        else:
            lines.append("Kontaktkriterium: window (Fenster-Median, "
                         "README B.2)")
        lines.append("Ablauf pro Punkt: Signalweg-Test → cont_step → "
                     "start_pres/start_step (ack) → %.3fs Totzeit → Ruhetest "
                     "(roh+gefiltert) → tri_chxs=0 → Schritte %.3fmm@%.1fmm/s "
                     "+ M400 + read/detect → stop_step/stop_pres (immer) → "
                     "Lift → Z = Pos + zoft → can_brk (nur Mesh), ggf. 1× "
                     "versetzt "
                     "(%.2f,%.2f)"
                     % (c["arm_settle_s"], c["step_mm"], c["probe_speed"],
                        c["retry_dx"], c["retry_dy"]))
        return "\n".join(lines)

    def test_swap(self):
        """TEST_SWAP-Äquivalent.

        BELEGT (Emulation des Stock-Moduls 16.10.2026, Stock live
        „Swap Test Success“): pres sta_swap=0 -> step lesen -> pres
        sta_swap=1 -> step lesen; Erfolg genau dann, wenn die Main-MCU
        erst 0, dann 1 meldet (expar0 der step-Antworten). PRES_VER/
        STEP_VER = expar1 der ersten Antworten (live 71/71).
        """
        if hasattr(self.pres, "set_swap") and hasattr(self.step, "read_swap"):
            try:
                p0 = self.pres.set_swap(0)
                s0 = self.step.read_swap()
                p1 = self.pres.set_swap(1)
                s1 = self.step.read_swap()
            except Exception as exc:
                raise K2PrtouchError("Swap-Test: MCU-Abfrage fehlgeschlagen: %s"
                                     % exc)
            ok = int(s0["expar0"]) == 0 and int(s1["expar0"]) == 1
            info = {"py_ver": HOST_PY_VER, "pres_ver": p0["expar1"],
                    "step_ver": s0["expar1"], "levels": (s0["expar0"],
                                                         s1["expar0"]),
                    "swap_ok": ok}
            if not ok:
                raise K2PrtouchError(
                    "PR_ERR_CODE_SWAP_PIN_DETECTI: The synchronization pin "
                    "test failed, pres_swap_pin=%s, step_swap_pin=%s "
                    "(gelesen %s statt (0, 1))."
                    % (self.cfg["pres_swap_pin"], self.cfg["step_swap_pin"],
                       info["levels"]))
            return {"pres": info, "step": info}
        try:
            p = self.pres.test_swap()
        except Exception as exc:
            raise K2PrtouchError("Swap-Test pres-Seite fehlgeschlagen: %s"
                                 % exc)
        try:
            s = self.step.test_swap()
        except Exception as exc:
            raise K2PrtouchError("Swap-Test step-Seite fehlgeschlagen: %s"
                                 % exc)
        # ANNAHME Prüfung: beide MCU-Seiten müssen antworten; exakte
        # Versionsnummern (live 78/71/71) nur geloggt, kein harter Match
        # (FW-Updates!). Fail nur bei Ausnahme/false.
        for info, name in ((p, "pres"), (s, "step")):
            if isinstance(info, dict) and info.get("swap_ok") is False:
                raise K2PrtouchError(
                    "PR_ERR_CODE_SWAP_PIN_DETECTI: The synchronization pin "
                    "test failed, pres_swap_pin=%s, step_swap_pin=%s."
                    % (self.cfg["pres_swap_pin"],
                       self.cfg["step_swap_pin"]))
        return {"pres": p, "step": s}

    # --- Scharf schalten (gemeinsamer Code mit dem Probe-Hauptweg) -------
    def _pc7_unarmed(self):
        """PC7 lesen VOR dem Scharfschalten (Signalweg-Test): stop_prtouch_
        step, ack expar0 = PC7-Rohpegel (B Firmware-Analyse, 0x08004244) –
        exakt der Stock-TEST_SWAP-Ablauf. True = ausgelöst (Pegel 0).
        Nur VOR start_prtouch_step zulässig (würde die step-Aufzeichnung
        beenden)."""
        r = self.step.read_swap()
        return int(r.get("expar0", 1)) == 0

    def _armed_triggered(self):
        """Kontakt im scharfen Zustand? -> Status tri_chxs der Düse
        (read_status, B; ANNAHME: ohne Nebenwirkung). Ersetzt im
        Rückfallweg die Endstop-Abfrage des Probe-Wegs (kein Endstop auf
        PC7 in mode steps)."""
        st = self.pres.read_status()
        return int(st.get("tri_chxs", 0) or 0) != 0

    def _make_core(self):
        """K2PrtouchProbeCore für Arm/Ruhetest/Disarm – derselbe Code wie im
        Probe-Hauptweg (arm(): Signalweg-Test -> cont_prtouch_step ->
        start_prtouch_pres [ack] -> start_prtouch_step [ack] -> arm_settle_s
        (>= Totzeit) -> Ruhetest roh+gefiltert+Ticks -> kein Kontakt;
        disarm(): stop_prtouch_step -> stop_prtouch_pres sta_swap=1).
        Ohne descend/get_params: die Fahrt macht probe_point selbst."""
        core = K2PrtouchProbeCore(
            self.cfg, None, None, self._pc7_unarmed, self.tool.wait_moves,
            self.pause, error=K2PrtouchError, logger=None,
            query_triggered_armed=self._armed_triggered)
        core.attach(self.pres, self.step)
        return core

    def preflight(self):
        """Vorabprüfung OHNE Bewegung (KTWO_PRTOUCH_READ, DRY_RUN,
        allow_move: False): scharf schalten -> Totzeit -> Ruhetest -> kein
        Kontakt -> IMMER entschärfen (K2PrtouchProbeCore.preflight).
        Ersetzt den alten check_sensor_rest, der VOR start_prtouch_pres las:
        ein nie gestartetes Register ist leer (Altfehler README A.8)."""
        return self.core.preflight()

    @staticmethod
    def _arm_status(phase):
        return {"swap": "swap_error", "start": "start_error",
                "settle": "start_error", "rest": "rest_error",
                "pc7": "contact_before_move",
                "apax": "apax_busy"}.get(phase, "arm_error")

    def check_temps(self):
        """Temperatur-Empfehlung (S5): nur Warnungen, nie Abbruch/Heizen."""
        try:
            nz, bd = self.tool.get_temps()
        except Exception:
            return ["Temperaturen nicht lesbar (ANNAHME API) – weiter ohne "
                    "Prüfung (nur Empfehlung 140/50)."]
        warns = []
        tol = float(self.cfg["temp_tolerance"])
        hot = hot_probe_warning(nz, self.cfg)
        if hot is not None:
            warns.append(hot)
        elif nz is not None and abs(nz - float(self.cfg["temp_nozzle_warn"])) > tol:
            warns.append("Düse %.0f°C weicht von Stock-Empfehlung %.0f°C ab "
                         "(±%.0f) – Messung ggf. ungenau, nicht geheizt."
                         % (nz, self.cfg["temp_nozzle_warn"], tol))
        if bd is not None and abs(bd - float(self.cfg["temp_bed_warn"])) > tol:
            warns.append("Bett %.0f°C weicht von Stock-Empfehlung %.0f°C ab "
                         "(±%.0f) – Messung ggf. ungenau, nicht geheizt."
                         % (bd, self.cfg["temp_bed_warn"], tol))
        return warns

    # --- Hauptablauf ---------------------------------------------------
    def probe_point(self, x=None, y=None, dry_run=False, _is_retry=False,
                    context="single"):
        """Eine Antastung (Rückfallweg). Gibt dict(status, z, zoft, ...).

        context: 'mesh' | 'home' | 'single' (Default). Die can_brk-Regel
        (und damit die versetzte Wiederholung) gilt nur, wenn
        can_brk_applies(context, cfg) – Default nur im Mesh (Stock-Beleg
        Live-Abgleich 18.10.2026 A8). Sonst can_brk True,
        can_brk_checked False, Regelwert nur als Diagnose (can_brk_rule).

        Reihenfolge (seit 17.10.2026 wie der Probe-Hauptweg, gemeinsamer
        Code K2PrtouchProbeCore.arm/disarm):
          Temperatur-Hinweis -> [allow_move/DRY_RUN: nur preflight, Ende]
          -> Config-Grenzen (step_mm, Geschwindigkeit max 5 / min
          min_probe_speed) -> XY -> wait_moves (Stillstand) -> arm()
          (Signalweg, cont/start_pres/start_step mit ack, arm_settle_s,
          Ruhetest roh+gefiltert, tri_chxs = 0) -> Schritte (step_mm + M400
          + Lesen + Auswertung) -> finally disarm() -> Anheben.
        status: ok / dry_run / swap_error / start_error / rest_error /
                contact_before_move / config_error / denied / move_error /
                emergency / max_travel / timeout / mcu_contact / error /
                disarm_error / lift_error.
        Bei ok: z = A + zoft (A = Z bei Kontakt/tiefster Punkt).
        """
        c = self.cfg
        t0 = time.monotonic()
        try:
            check_brk = can_brk_applies(context, c)
        except ValueError as exc:
            return {"status": "config_error", "error": str(exc),
                    "warnings": []}
        warns = self.check_temps()
        warns += emergency_config_warnings(c)
        for w in warns:
            try:
                self.log.info("[k2_prtouch] Warnung: %s" % w)
            except Exception:
                pass

        # S3: ohne Freigabe/DRY_RUN nur Vorabprüfung (scharf -> Ruhe ->
        # entschärft), keine Bewegung.
        if dry_run or not c.get("allow_move"):
            try:
                info = self.preflight()
            except Exception as exc:
                return {"status": self._arm_status(self.core.arm_phase),
                        "error": str(exc), "warnings": warns,
                        "disarms": self.core.disarm_count}
            rest = info.get("rest") or {}
            return {"status": "dry_run", "warnings": warns,
                    "swap": info.get("swap"), "rest": rest,
                    "baseline": rest.get("median"),
                    "plan": self.describe_plan()}

        step_mm = float(c["step_mm"])
        speed = float(c["probe_speed"])
        # Harte Config-Grenzen (S1) doppelt prüfen (fail-safe):
        if step_mm <= 0 or step_mm > STEP_MM_MAX + 1e-12:
            return {"status": "config_error",
                    "error": "step_mm %.4f verletzt Maximum %.2f (S1)"
                             % (step_mm, STEP_MM_MAX), "warnings": warns}
        err = check_probe_speed(speed, c)  # zu schnell UND zu langsam
        if err:
            return {"status": "config_error",
                    "error": "probe_speed: %s" % err, "warnings": warns}

        # XY-Positionierung (mit Freigabe, S3):
        try:
            self._require_move("Positionierung")
            if x is not None and y is not None:
                self.tool.move_to_xy(float(x), float(y), 100.0)
                self.tool.wait_moves()
                self.waits += 1
        except PermissionError as exc:
            return {"status": "denied", "error": str(exc),
                    "warnings": warns}
        except Exception as exc:
            return {"status": "move_error", "error": "XY: %s" % exc,
                    "warnings": warns}

        res, lift = self._armed_descent(c, step_mm, speed, t0, warns,
                                        check_brk=check_brk)
        if lift:
            try:
                self.tool.lift(float(c["z_lift_mm"]), float(c["lift_speed"]))
                self.tool.wait_moves()
                self.waits += 1
            except Exception as exc:
                msg = "Anheben fehlgeschlagen: %s" % exc
                if res.get("status") == "ok":
                    res["status"] = "lift_error"
                res["error"] = ("%s; %s" % (res["error"], msg)
                                if res.get("error") else msg)
        res["context"] = str(context).lower()
        if res.get("status") != "ok":
            return res
        self.last_result = dict(res)
        # can_brk False -> genau EINMAL versetzt wiederholen (BELEGT
        # T58->T59: +0.25/-0.25mm, 1mm hoch). Erst NACH dem Entschärfen
        # (finally in _armed_descent) – der zweite Versuch schaltet neu scharf.
        if not res["can_brk"] and not _is_retry:
            try:
                self.tool.lift(float(c["retry_lift_mm"]),
                               float(c["lift_speed"]))
                self.tool.wait_moves()
            except Exception:
                pass
            rx = float(x) + float(c["retry_dx"]) if x is not None else None
            ry = float(y) + float(c["retry_dy"]) if y is not None else None
            second = self.probe_point(rx, ry, dry_run=False, _is_retry=True,
                                      context=context)
            second["first_try"] = {k: res[k] for k in
                                   ("z", "zoft", "best_indx",
                                    "step_bst_indx") if k in res}
            second["retried"] = True
            second["retry_dx"] = c["retry_dx"]
            second["retry_dy"] = c["retry_dy"]
            return second
        return res

    def _armed_descent(self, c, step_mm, speed, t0, warns, check_brk=True):
        """Scharf schalten, Schrittfahrt, IMMER entschärfen (try/finally).
        -> (Ergebnis-Dict, anheben?). Angehoben wird erst nach dem
        Entschärfen (probe_point)."""
        core = self.core
        res = None
        lift = False
        total = 0.0
        steps = 0
        moved = False
        try:
            # Stillstand vor dem Scharfschalten (Bezug der Düse = gleitendes
            # Mittel von vor 37-49 ms, Firmware-Analyse) – wie run_probe.
            self.tool.wait_moves()
            self.waits += 1
            try:
                arm_info = core.arm()
            except Exception as exc:
                res = {"status": self._arm_status(core.arm_phase),
                       "error": str(exc), "warnings": warns}
                return res, False
            rest = arm_info.get("rest") or {}
            baseline0 = rest.get("median")
            if baseline0 is None:
                # rest_check: False -> Nullpunkt der Notgrenze trotzdem aus
                # dem (jetzt gefüllten) Register.
                t_b, d_b = self.pres.read_pres()
                _t, d0 = drop_empty(decode_ticks(t_b, c),
                                    decode_pres_datas(d_b, c))
                if len(d0) < REST_MIN_SAMPLES:
                    res = {"status": "rest_error",
                           "error": "Nullpunkt: nur %d gültige Rohwerte – "
                                    "keine Bewegung" % len(d0),
                           "warnings": warns}
                    return res, False
                baseline0 = median_int(d0)
            cumulative = c.get("contact_criterion", "window") == "cumulative"
            if cumulative:
                # Treppen-Kriterium (README B.3): Stufe 0 = Ruhe-Nullpunkt.
                z_start = float(self.tool.get_z())
                levels, depths = [float(baseline0)], [0.0]
            while True:
                if total + step_mm > float(c["max_travel_mm"]) + 1e-9:
                    res = {"status": "max_travel",
                           "error": "kein Kontakt innerhalb max_travel_mm="
                                    "%.2f (S2)" % c["max_travel_mm"]}
                    break
                if time.monotonic() - t0 > float(c["probe_timeout_s"]):
                    res = {"status": "timeout",
                           "error": "Timeout %.0fs (S2)"
                                    % c["probe_timeout_s"]}
                    break
                # S1: kurzer Schritt + M400:
                moved = True
                self.tool.move_down(step_mm, speed)
                self.tool.wait_moves()
                self.waits += 1
                total += step_mm
                steps += 1

                pt_raw, pd_raw = self.pres.read_pres()
                st_raw, sd_raw = self.step.read_step()
                tri = self._armed_triggered()
                pt = ticks_to_times(decode_ticks(pt_raw, c), c["clock_freq"])
                pd = decode_pres_datas(pd_raw, c)
                st = ticks_to_times(decode_ticks(st_raw, c), c["clock_freq"])
                sd_mm = step_counts_to_mm(decode_step_counts(sd_raw, c), c,
                                          relative=True)

                # S2: Notgrenze (Roh-Abweichung vom Ruhe-Nullpunkt):
                is_em, mag = check_emergency(pd, baseline0, c)
                if is_em:
                    res = {"status": "emergency",
                           "error": "Kraft-Notgrenze: Abweichung %.0f > %.0f "
                                    "(S2) – abgebrochen + angehoben."
                                    % (mag, c["emergency_thresh"])}
                    break

                det = detect_contact(pd, pt, sd_mm, st, c)
                if det.get("contact"):
                    A = float(self.tool.get_z())
                    zoft = float(det["zoft"])
                    rule, reason = check_can_brk(det["best_indx"],
                                                 det["step_bst_indx"], zoft,
                                                 c)
                    if check_brk:
                        can = rule
                    else:
                        # A8: außerhalb des Mesh wertet Stock can_brk nicht
                        # aus -> kein Verwerfen, keine Wiederholung.
                        can = True
                        reason = ("nicht ausgewertet (Kontext außerhalb "
                                  "Mesh, Scope %s; Regel: %s)"
                                  % (c.get("can_brk_scope", "mesh"),
                                     "plausibel" if rule else "False"))
                    res = {"status": "ok", "z": float(A + zoft), "A": A,
                           "zoft": zoft, "best_indx": det["best_indx"],
                           "best_time": det["best_time"],
                           "step_bst_indx": det["step_bst_indx"],
                           "tri_pose": det["tri_pose"],
                           "bst_pose": det["bst_pose"],
                           "can_brk": bool(can), "can_reason": reason,
                           "can_brk_checked": bool(check_brk),
                           "can_brk_rule": bool(rule),
                           "criterion": "window",
                           "mcu_triggered": bool(tri),
                           "swap": arm_info.get("swap"), "rest": rest}
                    break
                if cumulative:
                    levels.append(step_level(pd))
                    depths.append(total)
                    cum = detect_contact_cumulative(levels, depths, c)
                    if cum.get("contact"):
                        A = float(self.tool.get_z())
                        zc = z_start - float(cum["contact_depth"])
                        res = {"status": "ok", "z": float(zc), "A": A,
                               "zoft": float(zc - A),
                               "criterion": "cumulative",
                               "best_indx": None, "step_bst_indx": None,
                               "cum_run_len": cum["run_len"],
                               "cum_total": cum["total"],
                               "cum_slope_per_mm": cum["slope_per_mm"],
                               "cum_levels": [float(v) for v in levels],
                               # can_brk-Schranken beziehen sich auf
                               # best_indx im Fenster -> hier nicht anwendbar.
                               "can_brk": True,
                               "can_brk_checked": False,
                               "can_reason": "cumulative: can_brk entfällt "
                                             "(ANNAHME, README B.3)",
                               "mcu_triggered": bool(tri),
                               "swap": arm_info.get("swap"), "rest": rest}
                        break
                if tri:
                    # Düse hat nach ihrer eigenen Logik ausgelöst (B,
                    # Puffer eingefroren), das Host-Kriterium aber nicht ->
                    # kein Z-Wert, sicher abbrechen (Kriterium prüfen).
                    res = {"status": "mcu_contact",
                           "error": "Düse meldet Kontakt (tri_chxs), Host-"
                                    "Kriterium contact_thresh %.0f/%d Samples "
                                    "nicht erfüllt – kein Messwert (ANNAHME "
                                    "Host-Kriterium prüfen, README B)."
                                    % (c["contact_thresh"],
                                       c.get("rest_min_consec", 2))}
                    break
                # sonst: nächster kurzer Schritt (Überfahrweg wächst nur um
                # step_mm; Test (g) prüft Abbruch <= 1 Schritt nach Kontakt).
            lift = True
        except Exception as exc:
            res = {"status": "error", "error": "unerwartet: %s" % exc}
            lift = moved
        finally:
            # Immer entschärfen – auch bei jeder Ausnahme (wie run_probe).
            errs = core.disarm()
            if res is None:  # BaseException: weiterreichen, nur entschärft
                pass
            elif errs:
                msg = "Entschärfen fehlgeschlagen: %s" % "; ".join(errs)
                if res.get("status") == "ok":
                    res["status"] = "disarm_error"
                res["error"] = ("%s; %s" % (res["error"], msg)
                                if res.get("error") else msg)
                res["disarm_errors"] = errs
        res.setdefault("warnings", warns)
        res["steps"] = steps
        res["travel_mm"] = total
        res["waits"] = self.waits
        return res, lift


# =====================================================================
# Probe-Hauptweg (mode: probe) – reine Logik, ohne Klipper testbar
# =====================================================================
#
# Mechanismus (BELEGT 16.10.2026, Live-Lesetest +
# Emulation Stock-probe_prepare/probe_finish):
#   Düse scharf (start_prtouch_pres, Antast-Parameter) -> Kontakt zieht
#   PA15 (Düse) und damit PC7 (Main) auf 0 -> normaler Upstream-
#   MCU_endstop auf ^!PC7 mit trsync stoppt Z IN DER MCU -> danach
#   stop_prtouch_pres sta_swap=1 (PC7 wieder 1).
# Stock-Reihenfolge, hier übernommen:
#   prepare: cont_prtouch_step -> start_prtouch_pres -> start_prtouch_step
#   Fahrt:   endstop_home (Upstream HomingMove/probing_move)
#   finish:  stop_prtouch_step -> stop_prtouch_pres sta_swap=1
#            (-> optional Diagnose: eingefrorenen Ring lesen)
# Zusätzlich (eigene Sicherheit, nicht Stock): Signalweg-Test über den
# Endstop, Ruhetest nach dem Scharfschalten, PC7-Prüfung vor der Fahrt,
# allow_move-Sperre, Geschwindigkeitsgrenze 5 mm/s, Wegbegrenzung außerhalb
# von G28, Entschärfen bei JEDER Ausnahme (try/finally).
#
# ANNAHMEN P1-P6 – Stand nach Analyse der Düsen-Firmware 17.10.2026 (statisch):
#   P1 Stopp unter Bewegung schnell genug – TEILWEISE: MCU löst >= 2 Samples
#      nach erstem Überschreiten aus (PA15 im selben Durchlauf, 0x08008192),
#      Main = Standard-Endstop. NEU: Anstiegsdetektor – zu langsamer
#      Kraftaufbau löst NIE aus -> Mindestgeschwindigkeit (min_probe_speed,
#      Grenze ANNAHME). Physik/Überfahrweg am Gerät offen.
#   P2 arm_settle_s – BESTÄTIGT MIT KORREKTUR: keine „Ruhewertübernahme“
#      (Bezug gleitet, Mittel von vor 37-49 ms), aber Totzeit lmt_dead
#      Samples -> arm_settle_s jetzt an die Totzeit gekoppelt
#      (min_arm_settle_s, Reservefaktor 1,5 = ANNAHME).
#   P3 Ruhetest bei gestartetem start_prtouch_step – BESTÄTIGT (getrennte
#      Zustände auf getrennten MCUs).
#   P4 Signalweg-Test vor start stört nicht – BESTÄTIGT (nur VOR start
#      erlaubt; danach würde er entschärfen).
#   P5 start/stop_prtouch_step antworten mit ack_prtouch – BESTÄTIGT.
#   P6 PC7 geteilt – BESTÄTIGT mit Hinweis: Pull hängt in der Creality-FW von
#      der Reihenfolge ab (pull_up=0 = Pull-DOWN) -> '^!PC7' (beide Befehle
#      Pull-Up, Endzustand = Stock), validate_step_swap_pin.

def compute_probe_target_z(cur_z, z_min, max_travel, is_homing):
    """Zielhöhe der Abwärtsfahrt.

    G28 (Homing via probe:z_virtual_endstop): Upstream-Verhalten, bis
    position_min (die Ausgangshöhe ist dort unbekannt/erzwungen).
    Sonst (PROBE, BED_MESH_CALIBRATE, …): höchstens max_travel unter der
    aktuellen Höhe, nie unter position_min.
    """
    if is_homing:
        return float(z_min)
    return max(float(z_min), float(cur_z) - float(max_travel))


class K2PrtouchProbeCore:
    """Antast-Sitzung für den Upstream-Probe-Weg (probe.py-Session-API).

    Abhängigkeiten (alle injiziert, Fakes in den Tests):
      get_params(gcmd) -> dict wie Upstream ProbeParameterHelper
      descend: descend_until_trigger(gcmd, params),
               pull_trigger_positions(), clear_trigger_positions()
      query_triggered() -> bool (Endstop step_swap_pin, True = ausgelöst)
      wait_moves(), pause(sekunden)
      pres/step (attach() nach klippy:connect): set_swap, start_sampling,
               read_pres, disarm, [read_frozen] bzw. cont_step,
               start_sampling, disarm, [read_frozen]
      error: Fehlerklasse (Klipper: printer.command_error – andere
             Ausnahmen würden in Klipper einen Shutdown auslösen).
    """

    def __init__(self, cfg, get_params, descend, query_triggered,
                 wait_moves, pause, error=K2PrtouchError, logger=None,
                 query_triggered_armed=None):
        self.cfg = dict(cfg)
        self.get_params = get_params
        self.descend = descend
        # query_triggered: Signalweg-Test VOR dem Scharfschalten.
        # query_triggered_armed: Prüfung „noch kein Kontakt“ NACH dem
        # Scharfschalten (Default = query_triggered). mode steps braucht hier
        # einen anderen Weg (Status tri_chxs), weil dort PC7 nur über
        # stop_prtouch_step lesbar ist – das würde die step-Aufzeichnung
        # beenden (K2PrtouchController._armed_triggered).
        self.query_triggered = query_triggered
        self.query_triggered_armed = query_triggered_armed
        self.wait_moves = wait_moves
        self.pause = pause
        self.error = error
        self.log = logger
        self.pres = self.step = None
        self.armed = False
        self.arm_count = 0
        self.disarm_count = 0
        self.last = None
        # Zuletzt erreichte arm()-Stufe (swap/start/settle/rest/pc7/armed) –
        # der Rückfallweg bildet daraus seinen Status (swap_error, …).
        self.arm_phase = None
        # Gegensperre zum Sensor-Selbsttest (KTWO_PRTOUCH_CHECK): Callable
        # -> True, solange der apax-Strom läuft (oder sein Stopp nicht
        # bestätigt ist). None = pres_check aus (Verhalten wie vorher).
        # Grund (apax-Analyse): start_prtouch_apax bei scharfer Düse
        # löscht deren Puffer/Totzeit; beide Tasks teilen das CS1237-DRDY.
        self.apax_busy = None
        # Heiß-Antastung (Live-Abgleich E2): Callable -> Düsen-Ist-
        # temperatur (°C) oder None. Gesetzt nur im Probe-Hauptweg
        # (PrinterK2Prtouch); der Rückfallweg meldet über check_temps.
        # Fehler beim Lesen werden ignoriert (reine Meldung).
        self.get_nozzle_temp = None
        # Lüfterdämpfung (FanDamper) für die Dauer einer Sitzung bzw. der
        # Vorabprüfung. None = aus (Tests, fan_damping: False).
        self.fan_damper = None
        # Grenze der Rohwert-Spanne im Ruhetest; None = rest_max_dif. Der
        # Probe-Hauptweg setzt rest_max_dif_probe (Rückfallweg unverändert).
        self.rest_max_dif = None
        # Wiederholungen bei fehlgeschlagenem Ruhetest (arm()); 0 = keine.
        # Nur der Probe-Hauptweg setzt rest_retries (Rückfallweg/Tests: 0).
        self.rest_retries = 0

    def _fans(self, what):
        """FanDamper.reduce/restore; Fehler nur melden (der Ruhetest fängt
        einen ungedrosselten Lüfter ohnehin ohne Bewegung ab)."""
        if self.fan_damper is None:
            return
        try:
            getattr(self.fan_damper, what)()
        except Exception as exc:
            self._info("[k2_prtouch] Lüfterdämpfung (%s) fehlgeschlagen: %s"
                       % (what, exc))

    def attach(self, pres, step):
        self.pres, self.step = pres, step

    def _info(self, msg):
        try:
            if self.log is not None:
                if hasattr(self.log, "respond_info"):
                    self.log.respond_info(msg)
                else:
                    self.log.info(msg)
        except Exception:
            pass

    def _wrap(self, what, fn, *args):
        """MCU-Aufruf; fremde Ausnahmen -> self.error (kein Shutdown)."""
        try:
            return fn(*args)
        except Exception as exc:
            if isinstance(exc, self.error):
                raise
            raise self.error("[k2_prtouch] %s fehlgeschlagen: %s"
                             % (what, exc))

    # --- Freigabe / Grenzen -------------------------------------------
    def check_allowed(self, gcmd):
        """allow_move + Geschwindigkeitsgrenze; wirft VOR jeder Bewegung."""
        if not self.cfg.get("allow_move"):
            raise self.error(
                "[k2_prtouch] Antasten/Homing gesperrt: allow_move: False "
                "(Default). Erst KTWO_PRTOUCH_TEST_SWAP/KTWO_PRTOUCH_READ "
                "prüfen, dann allow_move: True setzen. Keine Bewegung.")
        params = self.get_params(gcmd)
        sp = float(params.get("probe_speed", 0.0))
        if not 0.0 < sp <= SPEED_MAX + 1e-12:
            raise self.error(
                "[k2_prtouch] Antastgeschwindigkeit %.2f mm/s unzulässig "
                "(hart <= %.1f mm/s). Bei G28: homing_speed und "
                "second_homing_speed in [stepper_z] <= %.1f setzen. "
                "Keine Bewegung." % (sp, SPEED_MAX, SPEED_MAX))
        # Firmware-Analyse: zu langsam -> Kraftrampe zu flach -> die Düse
        # löst nie aus (ANNAHME-Grenze, s. min_probe_speed).
        err = check_probe_speed(sp, self.cfg)
        if err:
            raise self.error(
                "[k2_prtouch] %s. Bei G28: homing_speed UND "
                "second_homing_speed in [stepper_z] >= %.2f setzen (Upstream-"
                "Default second_homing_speed = homing_speed/2). Keine "
                "Bewegung." % (err, min_probe_speed(self.cfg)))
        return params

    # --- Scharf schalten / entschärfen ----------------------------------
    def arm(self):
        """Düse scharf schalten (mit Vorabprüfungen). Wirft self.error.

        Entschärft NICHT selbst – run_probe räumt per finally auf.
        Scheitert NUR der Ruhetest, wird bis zu rest_retries-mal entschärft,
        REST_RETRY_PAUSE_S gewartet und neu scharf geschaltet (seltene
        Einzelausreißer bei heißer Düse, Messung 22.10.2026: 2 von 58
        Ruhefenstern). Signalweg-, ack- und Kontaktfehler nie wiederholen.
        """
        tries = 0
        while True:
            try:
                return self._arm_once()
            except self.error as exc:
                if (self.arm_phase != "rest"
                        or tries >= int(self.rest_retries or 0)):
                    raise
                tries += 1
                errs = self.disarm()
                if errs:
                    raise self.error(
                        "[k2_prtouch] Entschärfen vor der Ruhetest-"
                        "Wiederholung fehlgeschlagen: %s – keine Bewegung."
                        % "; ".join(errs))
                self._info("[k2_prtouch] Ruhetest-Wiederholung %d/%d: %s"
                           % (tries, int(self.rest_retries), exc))
                self.pause(REST_RETRY_PAUSE_S)

    def _arm_once(self):
        if self.pres is None or self.step is None:
            raise self.error("[k2_prtouch] noch nicht verbunden "
                             "(klippy:connect abwarten) – keine Bewegung.")
        if self.apax_busy is not None and self.apax_busy():
            self.arm_phase = "apax"
            raise self.error(
                "[k2_prtouch] Sensor-Selbsttest (KTWO_PRTOUCH_CHECK, apax-"
                "Strom) läuft noch oder sein Stopp ist unbestätigt – Düse "
                "wird NICHT scharf geschaltet, keine Bewegung. Ggf. "
                "KTWO_PRTOUCH_CHECK STOP=1.")
        c = self.cfg
        info = {}
        if self.get_nozzle_temp is not None and c.get("hot_probe_warn", True):
            try:
                hot = hot_probe_warning(self.get_nozzle_temp(), c)
            except Exception:
                hot = None
            if hot is not None:
                info["warnings"] = [hot]
                self._info("[k2_prtouch] Warnung: %s" % hot)
        self.arm_phase = "swap"
        if c.get("swap_check", True):
            # Signalweg PA15 -> PC7 -> Endstop prüfen (wie Stock-TEST_SWAP,
            # aber direkt über den Stopp-Endstop): sta_swap=0 muss den
            # Endstop auslösen, sta_swap=1 muss ihn freigeben.
            a0 = self._wrap("stop_prtouch_pres sta_swap=0",
                            self.pres.set_swap, 0)
            t0 = bool(self._wrap("Endstop-Abfrage", self.query_triggered))
            a1 = self._wrap("stop_prtouch_pres sta_swap=1",
                            self.pres.set_swap, 1)
            t1 = bool(self._wrap("Endstop-Abfrage", self.query_triggered))
            info["swap"] = {"triggered": (t0, t1),
                            "err": (int(a0.get("err", 0)),
                                    int(a1.get("err", 0))),
                            "pres_ver": a0.get("expar1")}
            if not (t0 and not t1):
                raise self.error(
                    "[k2_prtouch] Signalweg-Test fehlgeschlagen: Endstop "
                    "%s meldete ausgelöst=%s bei sta_swap=0 und %s bei "
                    "sta_swap=1 (erwartet True/False). Verkabelung, "
                    "step_swap_pin-Invertierung (^!PC7) oder pres_swap_pin "
                    "prüfen – keine Bewegung."
                    % (c.get("step_swap_pin"), t0, t1))
        # Stock-probe_prepare: cont_step -> start_pres -> start_step.
        self.arm_phase = "start"
        info["cont"] = self._wrap("cont_prtouch_step", self.step.cont_step)
        ack = self._wrap("start_prtouch_pres", self.pres.start_sampling)
        info["ack_pres"] = ack
        if int(ack.get("err", 0)) != 0:
            raise self.error(
                "[k2_prtouch] Düse ließ sich nicht scharf schalten: "
                "start_prtouch_pres ack err=%d (expar0=%s expar1=%s) – "
                "keine Bewegung." % (int(ack.get("err", 0)),
                                     ack.get("expar0"), ack.get("expar1")))
        ack2 = self._wrap("start_prtouch_step", self.step.start_sampling)
        info["ack_step"] = ack2
        if int(ack2.get("err", 0)) != 0:
            raise self.error(
                "[k2_prtouch] start_prtouch_step ack err=%d – keine "
                "Bewegung." % int(ack2.get("err", 0)))
        self.armed = True
        self.arm_count += 1
        # Totzeit abwarten (Firmware-Analyse): Kontakt innerhalb von
        # lmt_dead Samples nach start erkennt die Düse NICHT. parse_options
        # erzwingt arm_settle_s >= min_arm_settle_s; hier doppelt (fail-safe).
        self.arm_phase = "settle"
        settle = float(c.get("arm_settle_s", ARM_SETTLE_DEFAULT_S))
        need = min_arm_settle_s(c)
        if settle < need - 1e-9:
            raise self.error(
                "[k2_prtouch] arm_settle_s %.3f < Totzeit-Minimum %.3f s – "
                "keine Bewegung." % (settle, need))
        self.pause(settle)
        self.arm_phase = "rest"
        if c.get("rest_check", True):
            # Ruhetest erweitert (Firmware-Analyse): Rohwerte (is_src=1)
            # mit Tickabstand/-1/24 Bit/nicht konstant + gefilterter Kanal
            # (is_src=0) gegen min_hold.
            t, d = self._wrap("read_prtouch_pres is_src=1 (Ruhetest roh)",
                              self.pres.read_pres)
            ft, fd = self._wrap(
                "read_prtouch_pres is_src=0 (Ruhetest gefiltert)",
                self.pres.read_pres_filtered)
            ticks, datas = drop_empty(decode_ticks(t, c),
                                      decode_pres_datas(d, c))
            _ft, filt = drop_empty(decode_ticks(ft, c),
                                   decode_pres_datas(fd, c))
            rc = (c if self.rest_max_dif is None
                  else dict(c, rest_max_dif=self.rest_max_dif))
            ok, ri = check_rest_ok(datas, rc, ticks=ticks, filt=filt)
            info["rest"] = ri
            if not ok:
                raise self.error(
                    "[k2_prtouch] Sensor-Ruhetest nach dem Scharfschalten "
                    "fehlgeschlagen: %s – keine Bewegung."
                    % "; ".join(ri["errors"]))
        self.arm_phase = "pc7"
        q_armed = self.query_triggered_armed or self.query_triggered
        if bool(self._wrap("Kontakt-Abfrage (scharf)", q_armed)):
            raise self.error(
                "[k2_prtouch] Düse meldet bereits Kontakt (Endstop %s "
                "ausgelöst) VOR der Fahrt – keine Bewegung. Düse frei? "
                "Restkraft/Filament an der Düse?" % (c.get("step_swap_pin"),))
        self.arm_phase = "armed"
        return info

    def disarm(self):
        """Stock-probe_finish: stop_prtouch_step, dann stop_prtouch_pres
        sta_swap=1. Versucht IMMER beide; gibt Fehlerliste zurück."""
        errs = []
        for name, obj in (("stop_prtouch_step", self.step),
                          ("stop_prtouch_pres sta_swap=1", self.pres)):
            if obj is None:
                continue
            try:
                obj.disarm()
            except Exception as exc:
                errs.append("%s: %s" % (name, exc))
        self.armed = False
        self.disarm_count += 1
        return errs

    def preflight(self):
        """Vorabprüfung OHNE Bewegung (KTWO_PRTOUCH_READ im Probe-Modus):
        arm() inkl. Signalweg-Test, Totzeit, erweitertem Ruhetest und
        PC7-Prüfung, danach IMMER disarm(). Gibt das arm()-Info-Dict zurück
        oder wirft self.error. Braucht kein allow_move (keine Fahrt)."""
        pending = None
        info = None
        self.wait_moves()  # Stillstand wie run_probe (Ruhetest aussagekräftig)
        self._fans("reduce")  # gleiche Bedingungen wie beim Antasten
        try:
            info = self.arm()
        except BaseException as exc:
            pending = exc
            raise
        finally:
            errs = self.disarm()
            self._fans("restore")
            if errs and pending is None:
                raise self.error("[k2_prtouch] Entschärfen fehlgeschlagen: "
                                 "%s" % "; ".join(errs))
        return info

    def _diag(self):
        out = {}
        for name, obj in (("pres", self.pres), ("step", self.step)):
            if obj is None or not hasattr(obj, "read_frozen"):
                continue
            try:
                r = obj.read_frozen()
                out[name] = {k: (len(v[1]) if isinstance(v, tuple) else v)
                             for k, v in r.items()}
            except Exception as exc:
                out[name + "_error"] = str(exc)
        return out

    # --- Upstream-Session-API (vgl. probe.py ProbeEndstopWrapper) ------
    def start_probe_session(self, gcmd):
        self.check_allowed(gcmd)
        self.descend.clear_trigger_positions()
        # Stock DISTUURB_CTL bei multi_probe_begin; zurück in
        # end_probe_session (Upstream ruft das auch nach einem G-Code-Fehler,
        # SampleAveragingHelper._handle_command_error).
        self._fans("reduce")
        return self

    def run_probe(self, gcmd):
        params = self.check_allowed(gcmd)
        # Düse in Ruhe scharf schalten (Schwelle relativ zum Ruhewert beim
        # Scharfschalten, BELEGT Fingerdruck-Test):
        self.wait_moves()
        last = {"status": "arming"}
        self.last = last
        pending = None
        try:
            last["arm"] = self.arm()
            last["status"] = "armed"
            self.descend.descend_until_trigger(gcmd, params)
            last["status"] = "ok"
        except BaseException as exc:
            pending = exc
            last["status"] = "error"
            last["error"] = str(exc)
            raise
        finally:
            errs = self.disarm()
            if errs:
                last["disarm_errors"] = errs
            else:
                try:
                    if self.query_triggered():
                        last["warn"] = ("Endstop nach dem Entschärfen noch "
                                        "ausgelöst")
                except Exception:
                    pass
            if self.cfg.get("diag_readout"):
                last["diag"] = self._diag()
            if errs and pending is None:
                raise self.error("[k2_prtouch] Entschärfen fehlgeschlagen: "
                                 "%s" % "; ".join(errs))

    def pull_probed_results(self):
        return self.descend.pull_trigger_positions()

    def end_probe_session(self):
        self.descend.clear_trigger_positions()
        if self.armed:  # sollte nie vorkommen – trotzdem sicher entschärfen
            self.disarm()
        self._fans("restore")

    def get_status(self):
        return {"armed": self.armed, "arms": self.arm_count,
                "disarms": self.disarm_count, "last": self.last}


# =====================================================================
# Sensor-Selbsttest KTWO_PRTOUCH_CHECK (apax-Strom, Ersatz Stock-PRES_CHECK)
# =====================================================================
# Ablauf (apax-Analyse 17.10.2026, ANNAHMEN markiert):
#   nur wenn die Düse NICHT scharf ist -> start_prtouch_apax (Stock-
#   Parameter, ack expar1 == acq_tick) -> Last (EXTRUDE/MACRO) oder reine
#   Wartezeit (DURATION) -> stop_prtouch_apax IMMER (finally, auch bei
#   Fehler/KeyboardInterrupt) -> Auswertung je Kanal wie Stock
#   (min/max/avg/dif/len, nur laufende Kennwerte, keine Listen).

def build_start_apax_tuple(cfg, oid):
    """(oid, cfg_regs, acq_tick) für CMD_START_APAX. Stock (B):
    cfg_regs = pres_cfg_regs 60, acq_tick = apax_acq_tkms 0,78125 ms ->
    93750 – identisch mit unseren pres-Werten cfg_regs/acq_tick."""
    return (int(oid), int(cfg["cfg_regs"]), int(cfg["acq_tick"]))


def apax_bus_load(seconds):
    """Erwartete Busbelastung der Düsenleitung (B: ≈ 12,4 kB/s,
    10 Bit/Byte bei 230 400 Bd ≈ 54 %)."""
    return {"bytes": int(round(APAX_BUS_BYTES_PER_S * float(seconds))),
            "bytes_per_s": APAX_BUS_BYTES_PER_S,
            "line_share": APAX_BUS_BYTES_PER_S * 10.0 / APAX_BUS_BAUD}


def pres_check_max_samples(cfg):
    """Sample-Obergrenze des Wächters: pres_check_max_s × Rate × 1,5."""
    rate, _pga = cs1237_rate_pga(cfg["cfg_regs"])
    return int(float(cfg["pres_check_max_s"]) * rate
               * PRES_CHECK_GUARD_FACTOR) + 64


class ApaxStats:
    """Laufende Kennwerte des apax-Stroms je Kanal (wie Stock
    _handle_resault/chk_info: min/max/Summe/Anzahl – KEINE Listen).

    Thread-sicher: Klipper ruft Antwort-Handler im serialhdl-Hintergrund-
    thread auf (serialhdl.py _bg_thread). Die ersten 'skip' Samples je Kanal
    werden verworfen (ANNAHME Konfig-Handshake nach start). Ungültige
    Rohwerte (0, -1, 24-Bit-Sättigung, außerhalb 24 Bit) zählen getrennt und
    gehen NICHT in min/max ein (sonst täuscht ein Ausreißer ein großes dif
    vor). Überschreitet die Gesamtzahl max_samples, wird on_overrun genau
    einmal gerufen (Wächter: nicht blockierender Stopp)."""

    def __init__(self, skip=PRES_CHECK_SKIP, freq=CLOCK_FREQ,
                 max_samples=None, on_overrun=None):
        import threading
        self._lock = threading.Lock()
        self.skip = int(skip)
        self.freq = float(freq)
        self.max_samples = max_samples
        self.on_overrun = on_overrun
        self.ch = {}
        self.packets = 0
        self.total = 0
        self.decode_errors = 0
        self.overrun = False

    def _chan(self, ch):
        c = self.ch.get(ch)
        if c is None:
            c = {"recv": 0, "skipped": 0, "len": 0, "valid": 0,
                 "min": None, "max": None, "sum": 0,
                 "zero": 0, "stuck": 0, "sat": 0, "out": 0,
                 "last_tick": None, "tick_span": 0, "tick_n": 0,
                 "espd_moving": 0}
            self.ch[ch] = c
        return c

    def feed(self, ch, ticks, datas, espds=()):
        """Ein Paket (dekodiert) übernehmen."""
        if len(ticks) != len(datas):
            raise ValueError("apax-Paket: %d Ticks, aber %d Werte"
                             % (len(ticks), len(datas)))
        fire = False
        with self._lock:
            self.packets += 1
            c = self._chan(int(ch))
            for t, v in zip(ticks, datas):
                t = int(t) & 0xFFFFFFFF
                v = int(v)
                if c["last_tick"] is not None:
                    c["tick_span"] += (t - c["last_tick"]) & 0xFFFFFFFF
                c["last_tick"] = t
                c["tick_n"] += 1
                c["recv"] += 1
                self.total += 1
                if c["recv"] <= self.skip:
                    c["skipped"] += 1
                    continue
                c["len"] += 1
                if v == ADC_STUCK_VALUE:
                    c["stuck"] += 1
                elif v == 0:
                    c["zero"] += 1
                elif v < ADC24_MIN or v > ADC24_MAX:
                    c["out"] += 1
                elif v in (ADC24_MIN, ADC24_MAX):
                    c["sat"] += 1
                else:
                    c["valid"] += 1
                    c["sum"] += v
                    c["min"] = v if c["min"] is None else min(c["min"], v)
                    c["max"] = v if c["max"] is None else max(c["max"], v)
            c["espd_moving"] += sum(1 for e in espds if int(e) != 0)
            if (self.max_samples is not None and not self.overrun
                    and self.total > int(self.max_samples)):
                self.overrun = True
                fire = True
        if fire and self.on_overrun is not None:
            try:
                self.on_overrun()
            except Exception:
                pass

    def feed_params(self, params):
        """Antwort resault_prtouch_apax (Klipper-params) übernehmen.
        ticks/datas/espds gepackt wie pres (Packer 0x08006524, B)."""
        try:
            ticks = [t & 0xFFFFFFFF
                     for t in decode_packed(bytes(params["ticks"]))]
            datas = decode_packed(bytes(params["datas"]))
            espds = decode_packed(bytes(params.get("espds", b"") or b""))
        except Exception:
            with self._lock:
                self.decode_errors += 1
            return
        self.feed(params.get("ch", 0), ticks, datas, espds)

    def snapshot(self):
        with self._lock:
            return {"ch": {k: dict(v) for k, v in self.ch.items()},
                    "packets": self.packets, "total": self.total,
                    "decode_errors": self.decode_errors,
                    "overrun": self.overrun, "freq": self.freq}


def evaluate_apax(snap, cfg, duration_s=None, require_dif=True,
                  expect_moving=False):
    """Auswertung des Selbsttests -> (ok, info).

    Je Kanal (K2: nur Kanal 0) – alle Grenzen ANNAHME (apax-Analyse):
      - mind. pres_check_min_samples Samples nach dem Verwerfen,
      - Abtastrate aus den MCU-Ticks = Rate(cfg_regs) ± pres_check_rate_tol,
      - Anzahl passt zur Dauer (± Toleranz, Paketrest/Latenz als Spiel),
      - Anteil 0/-1/24-Bit-Sättigung/außerhalb <= pres_check_bad_frac,
      - Rohwerte nicht konstant (dif > 0),
      - require_dif: dif = max - min >= pres_check_hold (Stock pres_chk_hold
        15000; ANNAHME Kriterium „dif >= chk_hold“).
    expect_moving (EXTRUDE): espd nie != 0 -> nur Warnung.
    """
    rate_nom, _pga = cs1237_rate_pga(cfg["cfg_regs"])
    tol = float(cfg.get("pres_check_rate_tol", PRES_CHECK_RATE_TOL))
    hold = float(cfg.get("pres_check_hold", PRES_CHECK_HOLD_DEFAULT))
    min_n = int(cfg.get("pres_check_min_samples", PRES_CHECK_MIN_SAMPLES))
    bad_frac = float(cfg.get("pres_check_bad_frac", PRES_CHECK_BAD_FRAC))
    freq = float(snap.get("freq") or cfg.get("clock_freq", CLOCK_FREQ))
    errors, warns, chans = [], [], {}
    if snap.get("decode_errors"):
        errors.append("%d apax-Pakete nicht dekodierbar"
                      % snap["decode_errors"])
    if snap.get("overrun"):
        errors.append("Strom-Wächter: mehr als %d Samples (pres_check_max_s "
                      "überschritten) – nicht blockierend gestoppt"
                      % pres_check_max_samples(cfg))
    if not snap.get("ch"):
        errors.append("keine Daten empfangen (resault_prtouch_apax) – Strom "
                      "nicht gestartet, Leitung oder oid prüfen")
    elif 0 not in snap["ch"]:
        errors.append("Kanal 0 fehlt (nur %s)" % sorted(snap["ch"]))
    for ch in sorted(snap.get("ch", {})):
        c = snap["ch"][ch]
        e = []
        bad = c["zero"] + c["stuck"] + c["sat"] + c["out"]
        rate = None
        if c["tick_n"] > 1 and c["tick_span"] > 0:
            rate = (c["tick_n"] - 1) / (c["tick_span"] / freq)
        info = {"len": c["len"], "recv": c["recv"], "valid": c["valid"],
                "min": c["min"], "max": c["max"],
                "avg": (c["sum"] / float(c["valid"])) if c["valid"] else None,
                "dif": ((c["max"] - c["min"]) if c["valid"] else None),
                "bad": {"zero": c["zero"], "stuck": c["stuck"],
                        "sat": c["sat"], "out": c["out"]},
                "rate_hz": rate, "espd_moving": c["espd_moving"]}
        if c["len"] < min_n:
            e.append("nur %d Samples (< %d)" % (c["len"], min_n))
        if rate is None or abs(rate - rate_nom) > tol * rate_nom:
            e.append("Abtastrate %s statt %.0f Hz ±%d %%"
                     % ("unbekannt" if rate is None else "%.0f Hz" % rate,
                        rate_nom, round(tol * 100)))
        if duration_s is not None and duration_s > 0:
            lo = (1.0 - tol) * rate_nom * float(duration_s) - 64
            hi = (1.0 + tol) * rate_nom * (float(duration_s) + 0.25) + 64
            if not lo <= c["recv"] <= hi:
                e.append("%d Samples passen nicht zu %.2f s (erwartet "
                         "%.0f…%.0f)" % (c["recv"], duration_s, max(lo, 0),
                                         hi))
        if c["len"] > 0 and bad > bad_frac * c["len"]:
            e.append("%d von %d Rohwerten ungültig (0: %d, -1: %d, "
                     "Sättigung: %d, außerhalb 24 Bit: %d) – Sensor "
                     "abgezogen/DOUT hängt?" % (bad, c["len"], c["zero"],
                                                c["stuck"], c["sat"],
                                                c["out"]))
        if not c["valid"]:
            e.append("keine gültigen Rohwerte")
        elif info["dif"] == 0:
            e.append("Rohwerte konstant (dif=0) – Sensor tot/Leitung prüfen")
        elif require_dif and info["dif"] < hold:
            e.append("dif=%d < pres_check_hold %.0f – keine Kraftänderung "
                     "erkannt (Last beim Test? Kriterium = ANNAHME)"
                     % (info["dif"], hold))
        if expect_moving and c["espd_moving"] == 0:
            warns.append("CH%d: espd immer 0 trotz Extrusion (Extruder stand? "
                         "oid_estp prüfen)" % ch)
        info["errors"] = e
        chans[ch] = info
        errors += ["CH%d: %s" % (ch, x) for x in e]
    res = {"channels": chans, "errors": errors, "warnings": warns,
           "packets": snap.get("packets", 0), "total": snap.get("total", 0),
           "duration_s": duration_s, "require_dif": bool(require_dif),
           "hold": hold, "rate_nominal_hz": rate_nom}
    return (not errors), res


def format_check_lines(res):
    """Stock-ähnliche Zeilen PRES_CHECK_INFO_CH%d min/max/avg/dif/len."""
    out = []
    for ch in sorted(res.get("channels", {})):
        i = res["channels"][ch]
        out.append("PRES_CHECK_INFO_CH%d min=%s, max=%s, avg=%s, dif=%s, "
                   "len=%d, rate=%s Hz, espd_moving=%d"
                   % (ch, i["min"], i["max"],
                      "-" if i["avg"] is None else "%.0f" % i["avg"],
                      i["dif"], i["len"],
                      "-" if i["rate_hz"] is None else "%.0f" % i["rate_hz"],
                      i["espd_moving"]))
    return out


def format_check_failure(res):
    """Fehlertext ähnlich Stock key769 (PR_ERR_CODE_PRES_NOT_BE_SENSED) –
    wird als command_error gemeldet, KEIN Shutdown."""
    chans = sorted(ch for ch, i in res.get("channels", {}).items()
                   if i.get("errors")) or [0]
    return ("[k2_prtouch] PR_ERR_CODE_PRES_NOT_BE_SENSED: The pressure data "
            "in channel=%s cannot be properly sensed. (KTWO_PRTOUCH_CHECK, "
            "Ersatz Stock-key769, kein Shutdown) Gründe: %s"
            % (",".join(str(c) for c in chans),
               "; ".join(res.get("errors") or ["?"])))


class K2PrtouchApaxCheck:
    """Reiner Ablauf des Selbsttests (ohne Klipper testbar).

    apax: start() -> ack-Dict, stop() -> ack-Dict, stop_nowait() (Wächter).
    pause(s), clock() -> s. is_armed() -> True, solange die Düse scharf ist
    (Gegensperre). error: Klipper command_error (kein Shutdown).
    busy() ist die Gegensperre für K2PrtouchProbeCore.arm(): True ab VOR dem
    Senden von start bis zum BESTÄTIGTEN stop.
    """

    def __init__(self, cfg, apax, pause, clock=None, error=K2PrtouchError,
                 is_armed=None, logger=None):
        self.cfg = dict(cfg)
        self.apax = apax
        self.pause = pause
        self.clock = clock if clock is not None else time.monotonic
        self.error = error
        self.is_armed = is_armed
        self.log = logger
        self.active = False
        self.stats = None
        self.stray = 0
        self.runs = 0
        self.last = None

    def busy(self):
        return bool(self.active)

    def on_packet(self, params):
        """Antwort-Handler (serialhdl-Thread): nur Kennwerte nachführen."""
        st = self.stats
        if st is None or not self.active:
            self.stray += 1
            return
        st.feed_params(params)

    def _guard(self):
        try:
            self.apax.stop_nowait()
        except Exception:
            pass

    def stop(self):
        """stop_prtouch_apax mit ack. -> None oder Fehlertext. active bleibt
        True (Antasten gesperrt), solange der Stopp nicht bestätigt ist."""
        try:
            ack = self.apax.stop()
        except Exception as exc:
            return "stop_prtouch_apax: %s" % exc
        ack = ack if hasattr(ack, "get") else {}
        if int(ack.get("err", 0) or 0) != 0:
            return "stop_prtouch_apax ack err=%d" % int(ack.get("err"))
        self.active = False
        return None

    def run(self, load=None, duration=None, require_dif=True,
            expect_moving=False):
        """Selbsttest ausführen -> (ok, info). Wirft self.error bei
        Ablauf-/MCU-Fehlern (Auswertungsfehler -> ok False, kein Wurf)."""
        c = self.cfg
        if self.active:
            raise self.error("[k2_prtouch] KTWO_PRTOUCH_CHECK: apax-Strom "
                             "läuft bereits oder Stopp unbestätigt – erst "
                             "KTWO_PRTOUCH_CHECK STOP=1.")
        if self.is_armed is not None and self.is_armed():
            raise self.error("[k2_prtouch] KTWO_PRTOUCH_CHECK: Düse ist "
                             "scharf (Antasten läuft) – kein Selbsttest.")
        max_s = float(c["pres_check_max_s"])
        dur = None
        if load is None:
            dur = float(c["pres_check_duration_s"] if duration is None
                        else duration)
            if not 0.0 < dur <= max_s + 1e-9:
                raise self.error("[k2_prtouch] KTWO_PRTOUCH_CHECK: DURATION "
                                 "%.2f s außerhalb (0, %.1f] (pres_check_"
                                 "max_s)" % (dur, max_s))
        rate, _pga = cs1237_rate_pga(c["cfg_regs"])
        self.stats = ApaxStats(c.get("pres_check_skip", PRES_CHECK_SKIP),
                               c.get("clock_freq", CLOCK_FREQ),
                               pres_check_max_samples(c),
                               on_overrun=self._guard)
        last = {"status": "running", "mode": "load" if load else "duration"}
        self.last = last
        self.runs += 1
        pending = None
        t0 = t1 = None
        try:
            # active VOR dem Senden: das finally stoppt in jedem Fall, auch
            # wenn die ack-Antwort verloren geht (MCU könnte gestartet sein).
            self.active = True
            try:
                ack = _ack_dict(self.apax.start())
            except Exception as exc:
                if isinstance(exc, self.error):
                    raise
                raise self.error("[k2_prtouch] start_prtouch_apax "
                                 "fehlgeschlagen: %s" % exc)
            last["ack"] = ack
            if ack["err"] != 0 or ack["expar1"] != int(c["acq_tick"]):
                raise self.error(
                    "[k2_prtouch] start_prtouch_apax ack err=%d expar1=%d "
                    "(erwartet err=0 expar1=%d) – Selbsttest abgebrochen."
                    % (ack["err"], ack["expar1"], int(c["acq_tick"])))
            t0 = self.clock()
            if load is not None:
                try:
                    load()
                except Exception as exc:
                    if isinstance(exc, self.error):
                        raise
                    raise self.error("[k2_prtouch] KTWO_PRTOUCH_CHECK Last "
                                     "(EXTRUDE/MACRO): %s" % exc)
            else:
                self.pause(dur)
            t1 = self.clock()
        except BaseException as exc:
            pending = exc
            last["status"] = "error"
            last["error"] = str(exc)
            raise
        finally:
            if t0 is not None and t1 is None:
                t1 = self.clock()
            serr = self.stop()
            last["stopped"] = serr is None
            if serr:
                last["stop_error"] = serr
                last["status"] = "error"
                if pending is None:
                    raise self.error(
                        "[k2_prtouch] KTWO_PRTOUCH_CHECK: Stopp "
                        "fehlgeschlagen (%s) – Antasten bleibt gesperrt; "
                        "KTWO_PRTOUCH_CHECK STOP=1 oder FIRMWARE_RESTART."
                        % serr)
        duration_s = t1 - t0
        ok, res = evaluate_apax(self.stats.snapshot(), c, duration_s,
                                require_dif, expect_moving)
        if duration_s > max_s + 0.5:
            res["errors"].append("Dauer %.1f s > pres_check_max_s %.1f s"
                                 % (duration_s, max_s))
            ok = False
        res["stray_packets"] = self.stray
        last.update(res)
        last["status"] = "ok" if ok else "fail"
        return ok, res

    def get_status(self):
        last = self.last or {}
        return {"active": self.active, "runs": self.runs,
                "status": last.get("status"),
                "errors": list(last.get("errors", []) or []),
                "stray_packets": self.stray}


class KlippyApaxMcu:
    """Klipper-Anbindung des apax-Kanals (Düsen-MCU, gemeinsame PRES-oid wie
    Stock – B: PrtouchApax übernimmt oid und mcu von pres; der
    Strom kommt ohnehin mit S[0] = pres-oid, B).

    Start/Stopp als Query (ack_prtouch), Wächter-Stopp ohne Warten
    (lookup_command, aus dem serialhdl-Thread per register_async_callback in
    den Reactor verschoben). Antwort resault_prtouch_apax über
    mcu.register_serial_response (Upstream 9cb9975), sonst
    mcu.register_response (ältere Upstream-Stände)."""

    def __init__(self, mcu, cfg, oid, reactor=None):
        self.mcu = mcu
        self.cfg = cfg
        self.oid = int(oid)
        self.reactor = reactor
        self._q_start = self._q_stop = self._c_stop = None

    def handle_connect(self, sink):
        mcu = self.mcu
        self._q_start = mcu.lookup_query_command(CMD_START_APAX, RESP_ACK,
                                                 oid=self.oid)
        self._q_stop = mcu.lookup_query_command(CMD_STOP_APAX, RESP_ACK,
                                                oid=self.oid)
        self._c_stop = mcu.lookup_command(CMD_STOP_APAX)
        if hasattr(mcu, "register_serial_response"):
            mcu.register_serial_response(sink, RESP_APAX, self.oid)
        elif hasattr(mcu, "register_response"):
            mcu.register_response(sink, "resault_prtouch_apax", self.oid)
        else:
            raise RuntimeError("MCU kann keine Antworten registrieren")

    def start(self):
        return self._q_start.send(list(build_start_apax_tuple(self.cfg,
                                                              self.oid)))

    def stop(self):
        return _ack_dict(self._q_stop.send([self.oid]))

    def stop_nowait(self):
        def _send(eventtime=None):
            self._c_stop.send([self.oid])
        r = self.reactor
        if r is not None and hasattr(r, "register_async_callback"):
            r.register_async_callback(_send)
        else:
            _send()


# =====================================================================
# Klipper-Hülle [k2_prtouch] (ANNAHMEN zur Upstream-API)
# =====================================================================

def _resolve_pin_number(printer, mcu_name, pin_raw):
    """Pin-Name -> Nummer (alt, seit 16.10.2026 NICHT mehr genutzt: die
    Hülle schickt Pin-NAMEN wie Stock, Upstream löst sie selbst auf)."""
    try:
        ppins = printer.lookup_object("pins")
        if hasattr(ppins, "lookup_pin"):
            res = ppins.lookup_pin(pin_raw)
            if isinstance(res, (list, tuple)) and len(res) == 2:
                return int(res[1])
            if isinstance(res, int):
                return int(res)
    except Exception:
        pass
    try:
        mcu = printer.lookup_object(mcu_name)
        if hasattr(mcu, "get_enumeration"):
            return int(mcu.get_enumeration("pin",
                                           _clean_pin_name(pin_raw)))
        if hasattr(mcu, "get_enumerations"):
            return pin_number_from_enum(
                dict(mcu.get_enumerations()).get("pin", {}), pin_raw)
    except Exception:
        pass
    raise ValueError("Pin-Nummer für %r nicht auflösbar" % (pin_raw,))


def _upstream(name):
    """Upstream-Modul aus klippy/extras laden (im Paket relativ, in den
    Tests über einen 'extras'-Paketpfad). ANNAHME: Datei liegt als
    klippy/extras/k2_prtouch.py (wie jedes Zusatzmodul)."""
    import importlib
    if __package__:
        try:
            return importlib.import_module("." + name, __package__)
        except ImportError:
            pass
    return importlib.import_module("extras." + name)


def _lookup_mcu(printer, name):
    """MCU-Objekt nach Config-Namen. Upstream registriert '[mcu]' als 'mcu'
    und '[mcu nozzle_mcu]' als 'mcu nozzle_mcu' (mcu.py:1250-1260,
    get_printer_mcu). Fallback: Name wörtlich."""
    name = str(name).strip()
    if name != "mcu" and not name.startswith("mcu "):
        try:
            return printer.lookup_object("mcu " + name)
        except Exception:
            pass
    return printer.lookup_object(name)


class K2ProbeOffsets:
    """Wie Upstream ProbeOffsetsHelper (probe.py:410), aber z_offset mit
    Default 0 (Stock: Düse = Taster, keine XY-Versätze)."""

    def __init__(self, cfg):
        self.x_offset = 0.0
        self.y_offset = 0.0
        self.z_offset = float(cfg.get("z_offset", 0.0))

    def get_offsets(self, gcmd=None):
        return self.x_offset, self.y_offset, self.z_offset


class K2DescendHelper:
    """Abwärtsfahrt bis Endstop – nachgebaut nach Upstream
    DescendToEndstopHelper (probe.py:246-275), zusätzlich mit Wegbegrenzung
    außerhalb von G28 (compute_probe_target_z) und erneuter
    Geschwindigkeitsprüfung."""

    def __init__(self, config, mcu_probe, probe_offsets, cfg, probe_mod,
                 manual_probe_mod):
        self.printer = config.get_printer()
        self.mcu_probe = mcu_probe
        self.probe_offsets = probe_offsets
        self.cfg = cfg
        self.manual_probe = manual_probe_mod
        self.z_min_position = probe_mod.lookup_minimum_z(config)
        self.results = []
        # Z-Stepper an den Endstop hängen (trsync stoppt sie in der MCU):
        probe_mod.LookupZSteppers(config, self.mcu_probe.add_stepper)

    def descend_until_trigger(self, gcmd, params):
        toolhead = self.printer.lookup_object("toolhead")
        pos = toolhead.get_position()
        speed = float(params["probe_speed"])
        err = check_probe_speed(speed, self.cfg)  # zu schnell UND zu langsam
        if err:
            raise self.printer.command_error("[k2_prtouch] %s" % err)
        is_homing = gcmd.get_command() == "G28"
        pos[2] = compute_probe_target_z(
            pos[2], self.z_min_position, self.cfg["probe_max_travel_mm"],
            is_homing)
        phoming = self.printer.lookup_object("homing")
        check_movement = not phoming.check_probe_first_home(gcmd)
        ppos = phoming.probing_move(self.mcu_probe, pos, speed,
                                    check_movement=check_movement)
        offsets = self.probe_offsets.get_offsets()
        self.results.append(self.manual_probe.create_probe_result(ppos,
                                                                  offsets))

    def pull_trigger_positions(self):
        res = self.results
        self.results = []
        return res

    def clear_trigger_positions(self):
        self.pull_trigger_positions()


class K2PrtouchEndstopWrapper:
    """Endstop + Probe-Session (vgl. Upstream ProbeEndstopWrapper
    probe.py:545 / BLTouchProbe bltouch.py:26): MCU_endstop-Schnittstelle
    für HomingMove (get_mcu/add_stepper/get_steppers/home_start/home_wait/
    query_endstop, mcu.py:345-411) + Session-API an K2PrtouchProbeCore."""

    def __init__(self, mcu_endstop, core):
        self.mcu_endstop = mcu_endstop
        self.core = core
        self.get_mcu = mcu_endstop.get_mcu
        self.add_stepper = mcu_endstop.add_stepper
        self.get_steppers = mcu_endstop.get_steppers
        self.home_start = mcu_endstop.home_start
        self.home_wait = mcu_endstop.home_wait
        self.query_endstop = mcu_endstop.query_endstop

    def start_probe_session(self, gcmd):
        self.core.start_probe_session(gcmd)
        return self

    def run_probe(self, gcmd):
        self.core.run_probe(gcmd)

    def pull_probed_results(self):
        return self.core.pull_probed_results()

    def end_probe_session(self):
        self.core.end_probe_session()


class K2PrtouchPrinterProbe:
    """Das 'probe'-Objekt (vgl. Upstream PrinterProbe probe.py:608 und
    PrinterBLTouch bltouch.py:281): PROBE, QUERY_PROBE, PROBE_ACCURACY,
    PROBE_CALIBRATE, Z_OFFSET_APPLY_PROBE, probe:z_virtual_endstop sowie
    alle ProbePointsHelper-Nutzer (BED_MESH_CALIBRATE, Z_TILT_ADJUST,
    SCREWS_TILT_CALCULATE …)."""

    def __init__(self, config, cfg):
        probe_mod = _upstream("probe")
        manual_probe_mod = _upstream("manual_probe")
        self.printer = printer = config.get_printer()
        self.reactor = printer.get_reactor()
        self.cfg = cfg
        self.probe_offsets = K2ProbeOffsets(cfg)
        self.param_helper = probe_mod.ProbeParameterHelper(config)
        # Upstream liest 'speed' (Default 5.0); unser cfg['speed'] ist
        # identisch bzw. fällt auf probe_speed zurück und ist hart <= 5.
        self.param_helper.speed = float(cfg["speed"])
        ppins = printer.lookup_object("pins")
        self.mcu_endstop = ppins.setup_pin("endstop", cfg["step_swap_pin"])
        gcode = printer.lookup_object("gcode")
        self.core = K2PrtouchProbeCore(
            cfg, self.param_helper.get_probe_params, None,
            self._query_triggered, self._wait_moves, self._pause,
            error=printer.command_error, logger=gcode)
        self.core.get_nozzle_temp = self._nozzle_temp
        self.core.rest_max_dif = float(cfg["rest_max_dif_probe"])
        self.core.rest_retries = int(cfg["rest_retries"])
        if cfg.get("fan_damping", True):
            self.core.fan_damper = FanDamper(
                self._hotend_fans, cfg["fan_damping_speed"],
                cfg["fan_damping_settle_s"], self._pause,
                info=gcode.respond_info)
        self.mcu_probe = K2PrtouchEndstopWrapper(self.mcu_endstop, self.core)
        self.core.descend = K2DescendHelper(
            config, self.mcu_probe, self.probe_offsets, cfg, probe_mod,
            manual_probe_mod)
        self.probe_session = probe_mod.SampleAveragingHelper(
            config, self.param_helper, self.mcu_probe.start_probe_session)
        self.cmd_helper = probe_mod.ProbeCommandHelper(
            config, self, self.mcu_probe.query_endstop)
        probe_mod.HomingViaProbeHelper(config, float(cfg["position_endstop"]),
                                       self.mcu_probe.query_endstop)
        printer.add_object("probe", self)

    # Hilfen für den Kern (Klipper-APIs: mcu.py:1234, toolhead.py:422)
    def _query_triggered(self):
        mcu = self.mcu_endstop.get_mcu()
        pt = mcu.estimated_print_time(self.reactor.monotonic())
        return bool(self.mcu_endstop.query_endstop(pt))

    def _wait_moves(self):
        self.printer.lookup_object("toolhead").wait_moves()

    def _nozzle_temp(self):
        # Aktiver Extruder (heaters.py Heater.get_temp -> (ist, soll)).
        ex = self.printer.lookup_object("toolhead").get_extruder()
        temp, _target = ex.get_heater().get_temp(self.reactor.monotonic())
        return float(temp)

    def _pause(self, seconds):
        self.reactor.pause(self.reactor.monotonic() + float(seconds))

    def _hotend_fans(self):
        # [heater_fan …] am Extruder (K2: hotend_fan = Düse PB7 + Main PB1);
        # chamber_fan hängt an chamber_heater und bleibt unberührt.
        return [(name, obj) for name, obj
                in self.printer.lookup_objects("heater_fan")
                if "extruder" in getattr(obj, "heater_names", ())]

    # Upstream-Probe-Schnittstelle (probe.py:621-628)
    def get_probe_params(self, gcmd=None):
        return self.param_helper.get_probe_params(gcmd)

    def get_offsets(self, gcmd=None):
        return self.probe_offsets.get_offsets(gcmd)

    def get_status(self, eventtime):
        st = self.cmd_helper.get_status(eventtime)
        st["k2_prtouch"] = self.core.get_status()
        return st

    def start_probe_session(self, gcmd):
        return self.probe_session.start_probe_session(gcmd)


class PrinterK2Prtouch:
    """Klipper-Objekt [k2_prtouch]."""

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.konsole = _K2K.Konsole(self.printer, "k2_prtouch", self.gcode)
        try:
            self.cfg = parse_klipper_config(config)
        except Exception as exc:
            raise config.error("[k2_prtouch] Config-Fehler: %s" % exc)
        self.pres_oid = self.step_oid = None
        self.pres_mcu = self.step_mcu = None
        self.controller = None
        self.probe = None
        self._pin_nums = {}
        # Sensor-Selbsttest (pres_check, Default AUS): oid_estp wird in der
        # Config-Phase über die Klipper-API ermittelt, apax_check bei connect.
        self.apax_oid_estp = None
        self.apax_mcu = None
        self.apax_check = None
        # MCU-Objekte (Upstream-Namen 'mcu' / 'mcu nozzle_mcu'):
        try:
            self.mcu_pres = _lookup_mcu(self.printer, self.cfg["nozzle_mcu"])
            self.mcu_step = _lookup_mcu(self.printer, self.cfg["main_mcu"])
        except Exception as exc:
            raise config.error("[k2_prtouch] MCU nicht gefunden "
                               "(nozzle_mcu/main_mcu): %s" % exc)
        # Upstream: register_config_callback vor connect (mcu.py:1131).
        self.mcu_pres.register_config_callback(self._build_pres_config)
        self.mcu_step.register_config_callback(self._build_step_config)
        self.printer.register_event_handler("klippy:connect",
                                            self.handle_connect)
        # Hauptweg: Upstream-Probe auf step_swap_pin (Default ^!PC7).
        if self.cfg["mode"] == "probe":
            try:
                self.probe = K2PrtouchPrinterProbe(config, self.cfg)
            except config.error:
                raise
            except Exception as exc:
                raise config.error("[k2_prtouch] Probe-Einrichtung: %s"
                                   % exc)
        cmds = [("KTWO_PRTOUCH_PROBE_STEPS", self.cmd_probe,
                 "Rückfall: zeitgesteuerte Schrittfahrt [X=.. Y=.. "
                 "DRY_RUN=1]"),
                ("KTWO_PRTOUCH_TEST_SWAP", self.cmd_swap,
                 "Sync-/Versionscheck ohne Bewegung"),
                ("KTWO_PRTOUCH_READ", self.cmd_read,
                 "Sensor lesen ohne Bewegung"),
                ("KTWO_PRTOUCH_STATUS", self.cmd_status, "Letzter Status")]
        if self.cfg["pres_check"]:
            # Nur mit pres_check: True (Default False -> Befehl fehlt, alles
            # exakt wie vorher).
            cmds.append(("KTWO_PRTOUCH_CHECK", self.cmd_check,
                         "Sensor-Selbsttest (apax, Ersatz PRES_CHECK) "
                         "[DURATION=s | EXTRUDE=mm SPEED=mm/s | MACRO=name] "
                         "[REQUIRE_DIF=0] [STOP=1]"))
        if self.cfg["mode"] == "steps":
            # Nur im Rückfallmodus: eigene Ersatzbefehle für G28 Z / Mesh.
            cmds += [("KTWO_PRTOUCH_HOME_Z", self.cmd_home_z,
                      "Rückfall: Z-Homing Bettmitte [DRY_RUN=1]"),
                     ("KTWO_PRTOUCH_MESH", self.cmd_mesh,
                      "Rückfall: Raster-Mesh [COUNT=.. DRY_RUN=1]")]
        for cmd, fn, desc in cmds:
            self.gcode.register_command(cmd, fn, desc=desc)

    # --- Config-Phase (vor connect) ---
    def _build_pres_config(self):
        mcu = self.mcu_pres
        self.pres_oid = mcu.create_oid()
        # Pins als NAMEN wie Stock (BELEGT wörtlich: swp_pin=PA15
        # clk_pin=PB13 sdo_pin=PB14); Upstream löst '[ _]pin=NAME' über den
        # PinResolver der MCU auf (pins.py:16/41, mcu.py:1022-1025) und
        # msgproto kodiert *_pin über die 'pin'-Enumeration
        # (msgproto.py:125-135). Kein ppins.lookup_pin -> kein Konflikt mit
        # dem Endstop auf PC7 (Stock nutzt PC7 ebenso doppelt).
        swp = _clean_pin_name(self.cfg["pres_swap_pin"])
        clk = _clean_pin_name(self.cfg["pres_clk_pin"])
        sdo = _clean_pin_name(self.cfg["pres_sdo_pin"])
        self._pin_nums.update({"pres_swp": swp, "clk": clk, "sdo": sdo})
        mcu.add_config_cmd(
            "config_prtouch_pres oid=%d idx=%d swp_pin=%s clk_pin=%s "
            "sdo_pin=%s" % (self.pres_oid, int(self.cfg["pres_idx"]),
                            swp, clk, sdo))  # is_init False wie Stock
        if self.cfg["pres_check"]:
            # NACH config_prtouch_pres (Stock-Reihenfolge, apax-Analyse),
            # gemeinsame pres-oid wie Stock (B). oid_estp NUR aus dem
            # Stepper-Objekt des Extruders auf DIESER MCU – falsches oid_estp
            # = MCU-Shutdown beim ersten Sample (B), daher nie raten.
            self.apax_oid_estp = self._extruder_stepper_oid()
            mcu.add_config_cmd("config_prtouch_apax oid=%d oid_estp=%d"
                               % (self.pres_oid, self.apax_oid_estp))

    def _extruder_stepper_oid(self):
        """oid_estp = MCU_stepper.get_oid() des Extruders (Upstream:
        PrinterExtruder.extruder_stepper.stepper, extruder.py:177-182;
        stepper.py:33/58/132). Prüft, dass der Stepper auf DERSELBEN MCU
        liegt wie pres (Objektidentität, kein Namensvergleich) und die oid
        in %c passt. Sonst Config-Fehler mit klarer Meldung (Klipper meldet
        ihn als 'Config error', kein Shutdown)."""
        name = self.cfg["pres_check_extruder"]
        pre = ("[k2_prtouch] pres_check: oid_estp nicht sicher bestimmbar – "
               "config_prtouch_apax wird NICHT gesendet (falsches oid_estp = "
               "MCU-Shutdown beim ersten Sample). ")
        cerr = getattr(self.printer, "config_error", RuntimeError)
        try:
            ext = self.printer.lookup_object(name)
        except Exception as exc:
            raise cerr(pre + "Objekt %r nicht gefunden (pres_check_extruder): "
                       "%s" % (name, exc))
        es = getattr(ext, "extruder_stepper", None)
        st = getattr(es, "stepper", None) if es is not None else None
        if st is None or not hasattr(st, "get_oid") \
                or not hasattr(st, "get_mcu"):
            raise cerr(pre + "%r hat keinen Stepper (step_pin fehlt?)" % name)
        smcu = st.get_mcu()
        if smcu is not self.mcu_pres:
            def _n(m):
                try:
                    return m.get_name()
                except Exception:
                    return repr(m)
            raise cerr(pre + "Stepper von %r liegt auf MCU %r, der Kraftsensor "
                       "auf %r (nozzle_mcu) – die Düsen-FW kann nur einen "
                       "Stepper der EIGENEN MCU nachschlagen. pres_check: False "
                       "setzen oder [extruder] step_pin prüfen."
                       % (name, _n(smcu), _n(self.mcu_pres)))
        oid = int(st.get_oid())
        if not 0 <= oid <= 255 or oid == self.pres_oid:
            raise cerr(pre + "unplausible Stepper-oid %d (pres-oid %d)"
                       % (oid, self.pres_oid))
        return oid

    def _build_step_config(self):
        mcu = self.mcu_step
        self.step_oid = mcu.create_oid()
        swp = _clean_pin_name(self.cfg["step_swap_pin"])
        self._pin_nums["step_swp"] = swp
        ox, oy, oz = self._stepper_oids()
        self._pin_nums.update({"ox": ox, "oy": oy, "oz": oz})
        mcu.add_config_cmd(
            "config_prtouch_step oid=%d oid_xstp=%d oid_ystp=%d "
            "oid_zstp=%d swp_pin=%s" % (self.step_oid, ox, oy, oz, swp))

    def _stepper_oids(self):
        c = self.cfg
        if (c["stepper_x_oid"] is not None
                and c["stepper_y_oid"] is not None
                and c["stepper_z_oid"] is not None):
            return (int(c["stepper_x_oid"]), int(c["stepper_y_oid"]),
                    int(c["stepper_z_oid"]))
        # Upstream: Stepper sind KEINE eigenen Printer-Objekte, sondern
        # über toolhead.get_kinematics().get_steppers() erreichbar
        # (probe.py:203-206); MCU_stepper.get_oid() (stepper.py:132), OID
        # schon im Konstruktor vergeben (stepper.py:33).
        # BELEGT (Emulation 16.10.): oid_zstp = LETZTER Z-Stepper (stepper_z1).
        steppers = {}
        try:
            kin = self.printer.lookup_object("toolhead").get_kinematics()
            for st in kin.get_steppers():
                steppers[st.get_name()] = st
        except Exception:
            pass
        names = ["stepper_x", "stepper_y",
                 "stepper_z1" if "stepper_z1" in steppers else "stepper_z"]
        out = []
        for name in names:
            st = steppers.get(name)
            if st is None:
                try:
                    st = self.printer.lookup_object(name)
                except Exception as exc:
                    raise RuntimeError(
                        "Stepper %r nicht gefunden – notfalls stepper_*_oid "
                        "in [k2_prtouch] setzen: %s" % (name, exc))
            for attr in ("get_oid", "_oid", "oid"):
                try:
                    v = getattr(st, attr)
                    out.append(int(v() if callable(v) else v))
                    break
                except Exception:
                    continue
            else:
                raise RuntimeError(
                    "Stepper-OID für %r nicht auflösbar – notfalls "
                    "stepper_*_oid manuell in [k2_prtouch] setzen." % name)
        return tuple(out)

    def handle_connect(self):
        pres = KlippyPresMcu(self.printer, self.mcu_pres, self.cfg,
                             self.pres_oid, self._pin_nums)
        step = KlippyStepMcu(self.printer, self.mcu_step, self.cfg,
                             self.step_oid)
        try:
            pres.handle_connect()
            step.handle_connect()
        except Exception as exc:
            # Ohne Query-Lookup bleibt der Kern ungebunden -> jede Probe-/
            # Homing-Fahrt bricht VOR der Bewegung mit klarer Meldung ab.
            # Connect-Zeit: Konsole puffert bis kurz nach „ready“.
            self.konsole.fehler("Query-Lookup: %s – PrTouch nicht nutzbar "
                                "(Antasten/Z-Homing brechen vor der Bewegung "
                                "ab)" % exc)
            return
        self.pres_mcu, self.step_mcu = pres, step
        if self.probe is not None:
            self.probe.core.attach(pres, step)
        self.controller = K2PrtouchController(
            self.cfg, pres, step, KlippyToolhead(self.printer),
            pause=reactor_sleep(self.reactor),
            logger=_K2K.KonsolenLog(self.konsole, _K2K.WARNUNG))
        if self.cfg["pres_check"] and self.apax_oid_estp is not None:
            self._setup_apax()

    def _setup_apax(self):
        """Selbsttest anbinden + Gegensperren (arm <-> apax) setzen."""
        apax = KlippyApaxMcu(self.mcu_pres, self.cfg, self.pres_oid,
                             self.reactor)
        check = K2PrtouchApaxCheck(
            self.cfg, apax, reactor_sleep(self.reactor),
            clock=self.reactor.monotonic, error=self.printer.command_error,
            is_armed=self._any_armed, logger=self.gcode)
        try:
            apax.handle_connect(check.on_packet)
        except Exception as exc:
            self.konsole.warnung("pres_check: apax-Lookup fehlgeschlagen: %s "
                                 "– Drucksensor-Selbsttest aus" % exc)
            return
        self.apax_mcu, self.apax_check = apax, check
        if self.probe is not None:
            self.probe.core.apax_busy = check.busy
        if self.controller is not None:
            self.controller.core.apax_busy = check.busy

    def _any_armed(self):
        for core in ((self.probe.core if self.probe is not None else None),
                     (self.controller.core if self.controller is not None
                      else None)):
            if core is not None and core.armed:
                return True
        return False

    def _ctl(self):
        if self.controller is None:
            raise RuntimeError("noch nicht connected (klippy:connect "
                               "abwarten)")
        return self.controller

    def get_status(self, eventtime=None):
        _ = eventtime
        base = {"mode": self.cfg.get("mode"),
                "allow_move": bool(self.cfg.get("allow_move")),
                "step_mm": self.cfg.get("step_mm"),
                "last": self.controller.last_result
                if self.controller else None,
                "probe": self.probe.core.get_status()
                if self.probe is not None else None}
        if self.cfg.get("pres_check"):  # nur dann – sonst exakt wie vorher
            base["pres_check"] = (self.apax_check.get_status()
                                  if self.apax_check is not None else None)
        return base

    # --- G-Code-Helfer ---
    @staticmethod
    def _dry(gcmd):
        try:
            return bool(gcmd.get_int("DRY_RUN", 0))
        except Exception:
            return False

    def cmd_probe(self, gcmd):
        try:
            ctl = self._ctl()
        except Exception as exc:
            gcmd.respond_info("[k2_prtouch] %s" % exc)
            return
        x = y = None
        try:
            x = gcmd.get_float("X", None)
        except Exception:
            pass
        try:
            y = gcmd.get_float("Y", None)
        except Exception:
            pass
        # Antast-Kontext (A8): Default 'single'; eigene Mesh-Makros im
        # Rückfallweg können CONTEXT=mesh angeben (dann can_brk wie Stock).
        try:
            context = (gcmd.get("CONTEXT", "single") or "single").lower()
        except Exception:
            context = "single"
        if context not in PROBE_CONTEXTS:
            msg = ("[k2_prtouch] CONTEXT muss %s sein: %r"
                   % ("/".join(PROBE_CONTEXTS), context))
            if hasattr(gcmd, "error"):
                raise gcmd.error(msg)
            gcmd.respond_info(msg)
            return
        dry = self._dry(gcmd)
        if dry or not self.cfg.get("allow_move"):
            res = ctl.probe_point(x, y, dry_run=True, context=context)
            gcmd.respond_info("[k2_prtouch] Trockenlauf (keine Bewegung): "
                              "%s" % (res.get("plan", res),))
            if not dry:
                gcmd.respond_info("[k2_prtouch] ALLOW_WRITE-Hinweis: "
                                  "allow_move=False → DRY_RUN.")
            return
        res = ctl.probe_point(x, y, dry_run=False, context=context)
        if res.get("status") == "ok":
            gcmd.respond_info("[k2_prtouch] probe at %.3f,%.3f is z=%.4f "
                              "(A=%.4f zoft=%.4f best=%s step=%s can_brk=%s"
                              "%s)"
                              % (x or 0.0, y or 0.0, res["z"], res["A"],
                                 res["zoft"], res.get("best_indx"),
                                 res.get("step_bst_indx"), res["can_brk"],
                                 "" if res.get("can_brk_checked")
                                 else " nicht ausgewertet, Kontext %s"
                                 % context))
            if self.cfg.get("shutdown_on_failure") and not res["can_brk"]:
                raise gcmd.error("[k2_prtouch] can_brk False") \
                    if hasattr(gcmd, "error") else RuntimeError("can_brk")
        else:
            msg = "[k2_prtouch] FEHLER (%s): %s" % (res.get("status"),
                                                    res.get("error"))
            gcmd.respond_info(msg)
            if self.cfg.get("shutdown_on_failure"):
                raise gcmd.error(msg) if hasattr(gcmd, "error") \
                    else RuntimeError(msg)

    def cmd_home_z(self, gcmd):
        """Z-Homing Bettmitte + SET_KINEMATIC_POSITION (ANNAHME API)."""
        try:
            ctl = self._ctl()
        except Exception as exc:
            gcmd.respond_info("[k2_prtouch] %s" % exc)
            return
        dry = self._dry(gcmd)
        # Bettmitte aus [bed_mesh] mesh_min/max (ANNAHME Config-Zugriff):
        cx = cy = 175.0
        try:
            bm = self.printer.lookup_object("bed_mesh")
            if hasattr(bm, "get_mesh_limits"):
                (x0, y0), (x1, y1) = bm.get_mesh_limits()
                cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
        except Exception:
            pass
        # Kontext 'home': can_brk wird wie bei Stock-G28 Z NICHT ausgewertet
        # (A8; Stock-G28-Fahrten erfüllten 2/2 die Regel).
        res = ctl.probe_point(cx, cy, dry_run=dry or
                              not self.cfg.get("allow_move"),
                              context="home")
        if res.get("status") == "dry_run":
            gcmd.respond_info("[k2_prtouch] HOME_Z Trockenlauf: %s"
                              % res.get("plan", ""))
            return
        if res.get("status") != "ok":
            gcmd.respond_info("[k2_prtouch] HOME_Z FEHLER: %s"
                              % res.get("error"))
            return
        # ANNAHME: SET_KINEMATIC_POSITION setzt Z ohne Fahrt (Upstream).
        try:
            self.gcode.run_script("SET_KINEMATIC_POSITION Z=%.4f" % res["z"])
            gcmd.respond_info("[k2_prtouch] HOME_Z ok: Z=%.4f" % res["z"])
        except Exception as exc:
            gcmd.respond_info("[k2_prtouch] SET_KINEMATIC_POSITION: %s "
                              "(ANNAHME – homing_override)" % exc)

    def cmd_mesh(self, gcmd):
        """Eigenes Raster-Mesh (Fallback, ANNAHME bed_mesh-API)."""
        try:
            ctl = self._ctl()
        except Exception as exc:
            gcmd.respond_info("[k2_prtouch] %s" % exc)
            return
        dry = self._dry(gcmd)
        try:
            count = gcmd.get_int("COUNT", 3)
        except Exception:
            count = 3
        count = max(2, min(9, int(count)))
        # Raster aus [bed_mesh] oder Default 5..345 (ANNAHME):
        x0, y0, x1, y1 = 5.0, 5.0, 345.0, 345.0
        try:
            bm = self.printer.lookup_object("bed_mesh")
            if hasattr(bm, "get_mesh_limits"):
                (x0, y0), (x1, y1) = bm.get_mesh_limits()
        except Exception:
            pass
        if dry or not self.cfg.get("allow_move"):
            gcmd.respond_info("[k2_prtouch] MESH Trockenlauf %dx%d "
                              "(keine Bewegung)." % (count, count))
            return
        pts = []
        for iy in range(count):
            for ix in range(count):
                x = x0 + (x1 - x0) * ix / max(1, count - 1)
                y = y0 + (y1 - y0) * iy / max(1, count - 1)
                # Kontext 'mesh': einziger Ort, an dem Stock can_brk
                # auswertet (A8) -> Regel + 1× versetzte Wiederholung.
                r = ctl.probe_point(x, y, context="mesh")
                if r.get("status") != "ok":
                    gcmd.respond_info("[k2_prtouch] MESH Abbruch bei "
                                      "(%.1f,%.1f): %s" % (x, y, r))
                    return
                pts.append((x, y, r["z"]))
        gcmd.respond_info("[k2_prtouch] MESH %dx%d ok, Spanne %.3f..%.3f "
                          "(ANNAHME: Übergabe an bed_mesh)."
                          % (count, count, min(p[2] for p in pts),
                             max(p[2] for p in pts)))

    def cmd_swap(self, gcmd):
        try:
            ctl = self._ctl()
        except Exception as exc:
            gcmd.respond_info("[k2_prtouch] %s" % exc)
            return
        try:
            gcmd.respond_info("[k2_prtouch] TEST_SWAP: %s"
                              % (ctl.test_swap(),))
        except Exception as exc:
            gcmd.respond_info("[k2_prtouch] SWAP_FEHLER: %s" % exc)

    def cmd_read(self, gcmd):
        try:
            ctl = self._ctl()
        except Exception as exc:
            gcmd.respond_info("[k2_prtouch] %s" % exc)
            return
        # mode probe (17.10.2026): vollständige Vorabprüfung OHNE Bewegung
        # (Signalweg, scharf schalten, Totzeit, Ruhetest roh+gefiltert+Ticks,
        # PC7 frei, entschärfen). Grund: ein nie gestartetes Register ist
        # leer – ein Lesen ohne start_prtouch_pres prüft nichts.
        if self.probe is not None and self.probe.core.pres is not None:
            try:
                info = self.probe.core.preflight()
                gcmd.respond_info("[k2_prtouch] READ ok (Vorabprüfung ohne "
                                  "Bewegung): rest=%s" % (info.get("rest"),))
            except Exception as exc:
                gcmd.respond_info("[k2_prtouch] READ: %s" % exc)
            return
        # mode steps: dieselbe Vorabprüfung über den Controller (Signalweg
        # über stop_prtouch_step, Kontakt-Abfrage über tri_chxs).
        try:
            info = ctl.preflight()
            gcmd.respond_info("[k2_prtouch] READ ok (Vorabprüfung ohne "
                              "Bewegung): rest=%s" % (info.get("rest"),))
        except Exception as exc:
            gcmd.respond_info("[k2_prtouch] READ: %s" % exc)

    def _check_hot(self):
        """EXTRUDE nur heiß: can_extrude (min_extrude_temp) UND Temperatur
        >= pres_check_min_temp, aktiver Extruder = pres_check_extruder."""
        name = self.cfg["pres_check_extruder"]
        th = self.printer.lookup_object("toolhead")
        act = th.get_extruder()
        if act.get_name() != name:
            raise self.printer.command_error(
                "[k2_prtouch] KTWO_PRTOUCH_CHECK EXTRUDE: aktiver Extruder "
                "%r != %r – keine Extrusion." % (act.get_name(), name))
        heater = self.printer.lookup_object(name).get_heater()
        temp, _target = heater.get_temp(self.reactor.monotonic())
        if not heater.can_extrude or temp < self.cfg["pres_check_min_temp"]:
            raise self.printer.command_error(
                "[k2_prtouch] KTWO_PRTOUCH_CHECK EXTRUDE: Düse zu kalt "
                "(%.0f °C, Minimum %.0f °C/min_extrude_temp) – erst heizen. "
                "Kein Strom gestartet." % (temp,
                                           self.cfg["pres_check_min_temp"]))

    def _do_extrude(self, mm, speed):
        self.gcode.run_script_from_command(
            "SAVE_GCODE_STATE NAME=_k2_prtouch_check\nM83\n"
            "G1 E%.3f F%.1f\nRESTORE_GCODE_STATE NAME=_k2_prtouch_check"
            % (mm, speed * 60.0))
        self.printer.lookup_object("toolhead").wait_moves()

    def _do_macro(self, macro):
        self.gcode.run_script_from_command(macro)
        self.printer.lookup_object("toolhead").wait_moves()

    def cmd_check(self, gcmd):
        """KTWO_PRTOUCH_CHECK – Sensor-Selbsttest über den apax-Strom.

        DURATION=s (Default pres_check_duration_s): nur messen, keine
          Bewegung (ohne Last schlägt dif fehl -> REQUIRE_DIF=0 prüft nur
          Strom/Rate/Rohwerte, oder Düse von Hand belasten).
        EXTRUDE=mm [SPEED=mm/s]: kleine Extrusion während der Messung (nur
          allow_move, heiß, <= pres_check_extrude_max_mm).
        MACRO=name: Makro (z. B. Düsenreinigung) während der Messung (nur
          allow_move; G28/PROBE darin sind durch die Gegensperre gesperrt).
        STOP=1: nur stop_prtouch_apax wiederholen (Sperre lösen).
        Fehlschlag -> command_error (Stock-key769-Text), KEIN Shutdown."""
        cerr = self.printer.command_error
        check = self.apax_check
        if check is None:
            raise cerr("[k2_prtouch] KTWO_PRTOUCH_CHECK nicht bereit "
                       "(klippy:connect/apax-Lookup fehlgeschlagen).")
        if gcmd.get_int("STOP", 0):
            err = check.stop()
            if err:
                raise cerr("[k2_prtouch] STOP: %s – Antasten bleibt "
                           "gesperrt." % err)
            gcmd.respond_info("[k2_prtouch] apax gestoppt, Sperre gelöst.")
            return
        c = self.cfg
        extrude = gcmd.get_float("EXTRUDE", None)
        macro = gcmd.get("MACRO", None)
        duration = gcmd.get_float("DURATION", None)
        require_dif = bool(gcmd.get_int("REQUIRE_DIF", 1))
        if extrude is not None and macro is not None:
            raise cerr("[k2_prtouch] EXTRUDE und MACRO nicht kombinierbar.")
        load = None
        expect_moving = False
        max_s = float(c["pres_check_max_s"])
        if extrude is not None or macro is not None:
            if not c.get("allow_move"):
                raise cerr("[k2_prtouch] KTWO_PRTOUCH_CHECK EXTRUDE/MACRO "
                           "braucht allow_move: True – nichts bewegt.")
        if extrude is not None:
            speed = gcmd.get_float("SPEED", c["pres_check_extrude_speed"])
            if not 0.0 < extrude <= c["pres_check_extrude_max_mm"] + 1e-9:
                raise cerr("[k2_prtouch] EXTRUDE=%.3f außerhalb (0, %.1f] mm "
                           "(pres_check_extrude_max_mm)."
                           % (extrude, c["pres_check_extrude_max_mm"]))
            if not 0.0 < speed <= PRES_CHECK_EXTRUDE_SPEED_LIMIT + 1e-9:
                raise cerr("[k2_prtouch] SPEED=%.2f außerhalb (0, %.0f] mm/s."
                           % (speed, PRES_CHECK_EXTRUDE_SPEED_LIMIT))
            if extrude / speed + 1.0 > max_s:
                raise cerr("[k2_prtouch] EXTRUDE dauert ~%.1f s > "
                           "pres_check_max_s %.1f s." % (extrude / speed + 1.0,
                                                          max_s))
            self._check_hot()
            load = lambda: self._do_extrude(extrude, speed)
            expect_moving = True
        elif macro is not None:
            import re
            if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", macro):
                raise cerr("[k2_prtouch] MACRO=%r ungültig (nur ein "
                           "Makroname ohne Parameter)." % (macro,))
            load = lambda: self._do_macro(macro)
        self.printer.lookup_object("toolhead").wait_moves()
        gcmd.respond_info("[k2_prtouch] KTWO_PRTOUCH_CHECK: apax-Strom läuft "
                          "(≈ %.1f kB/s, ≈ %d %% der Düsenleitung)"
                          % (APAX_BUS_BYTES_PER_S / 1000.0,
                             round(apax_bus_load(1)["line_share"] * 100)))
        try:
            ok, res = check.run(load=load, duration=duration,
                                require_dif=require_dif,
                                expect_moving=expect_moving)
        except cerr:
            raise
        except Exception as exc:
            raise cerr("[k2_prtouch] KTWO_PRTOUCH_CHECK: %s" % exc)
        for line in format_check_lines(res):
            gcmd.respond_info(line)
        for w in res.get("warnings", []):
            gcmd.respond_info("[k2_prtouch] Warnung: %s" % w)
        if not ok:
            raise cerr(format_check_failure(res))
        gcmd.respond_info("SUCCESS: Strain gauge dynamic detection passed. "
                          "(KTWO_PRTOUCH_CHECK%s)"
                          % ("" if require_dif else ", ohne dif-Kriterium"))

    def cmd_status(self, gcmd):
        st = self.get_status()
        gcmd.respond_info("[k2_prtouch] %s" % (st,))


def load_config(config):
    """Klipper-Einstieg [k2_prtouch] (Upstream-Muster)."""
    return PrinterK2Prtouch(config)
