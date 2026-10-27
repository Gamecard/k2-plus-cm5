#!/usr/bin/env python3
"""motor.py – Python-Nachbau von motor_control (K2 Closed-loop-Motoren, erweitert).

Ersetzt (teilweise): motor_control_wrapper.cpython-39.so (armhf, proprietär).
Erweiterung von motor_mini.py (motor_mini.py bleibt eigenständig, gleiche
Sperren, gleiche Retry-/Timeout-Regeln).

LIVE-STAND 15.10.2026 (Auswertung von 6 Live-Logs, lokal, nicht im Repo,
10 Boot-Enumerationen; "Live-Log" ohne Datum = Boot 16.10. 01:15
Druckerzeit).
ERGÄNZUNG 16.10.2026 (Live-Abfragen 16.10.2026, Motor X 0x81, Standby,
nicht referenziert, gesendet über Creality-G-Codes MOTOR_VERSION/MOTOR_GET):
0x0F = Version ([00] -> 12 B) und 0x08 = Variablen-Lesepfad ([idx] -> 4 B LE,
idx 1..12) sind live belegt -> query_motor_version(), read_variable().
ERGÄNZUNG 16.10.2026 (statische Analyse
motor_control_wrapper.so + Traceback im Live-Log 13:12:27): 0x08-Index 5..8 =
pos_fdb/spd_fdb/id_fdb/iq_fdb (Host-Dict NO_ERR_CODE_TO_GET), Typkette des
Hosts fest (1..10,17,18 '<f', 11..16 '<I'), Motor_cmd-Tabelle komplett,
Crash-Ursache DATA=0/13 belegt (host-seitig, KEIN Frame auf dem Bus).
ERGÄNZUNG 16.10.2026 (Live-Abfragen 16.10.2026, „Skalierung
pos_fdb“, Messfahrt X nach G28, Bett leer, mit Freigabe): idx 11 =
Single-Turn-Position 0..3199 Counts/Umdrehung (POS_COUNTS_PER_REV = 3200 =
200 Vollschritte x 16 Microsteps; 80 Counts/mm bei rotation_distance 40),
idx 5 pos_fdb = idx11 * 2*pi/3200 [rad]. Beide wrappen alle 40 mm (eine
Umdrehung) -> KEINE absolute Achsposition (die hat nur Klipper über den
Schrittzähler). idx 6 spd_fdb im Stillstand ~0 (+-0,006; Einheit seit
FW-Analyse 16.10.: rad/s).
ERGÄNZUNG 16.10.2026 (statische Analyse der Motor-FW-Images
mot*_022_C30-mot2_002_081.bin aus der Creality-Firmware: WCH RISC-V
CH32V30x-Klasse, Basis 0x3000 hinter 12-KB-Bootloader, Befehlsregistrierung
@0x6100-0x63d8) + Live 16.10. (Fahrt X F600/F1200, DIR-Gegenprobe):
- 0x08-Tabelle der FW idx 0..18 komplett (VARIABLE_TABLE: Name, Einheit,
  Belegstatus); idx 1-4 = pos/spd/id/iq_ref, 5/6 = LESO-Beobachter (Host-
  Namen pos_fdb/spd_fdb), 9/10 = Ia/Ib [A] (idx 10 Vorzeichen s. ERGÄNZUNG
  17.10.), 11 = Schrittpuls-Zähler,
  12 = DIR-Eingang (live bestätigt), 17/18 = °C/V; spd_fdb = rad/s belegt.
  idx > 18: FW sendet einen veralteten Wert (kein Crash).
- 0x0C [0b]: Byte 0-3 Fehler u32 LE, Byte 4-7 Warnung u32 LE -> decode_
  protection(); Sub-Codes 00..0B als Konstanten (SUB_PROT_*).
- STATUS-Byte der Motorantwort = Bitfeld (bit0 Stall, bit1 Fehler latched,
  bit2 Warnung latched) -> decode_motor_status().
- 0x13..0x16 lesend (FW-statisch; LIVE bestätigt 16.10., s. unten):
  read_err_detail, read_stall_detail, read_pos_samples, read_spd_samples.
ERGÄNZUNG 16.10.2026 17:26 Log-Uhr (Live-Abfragen 16.10.2026,
„0x13–0x16 live“, Motor X 0x81, Stillstand nach M84, Klipper durchgehend
ready): über die Klipper-Webhook-Endpunkte motor_err_detail_data (Host
sendet 0x13, danach 0x15 und 0x16) und motor_cut_detail_data (0x14, danach
MOTOR_GET idx 5-8), Parameter {"num": 1}. Requests `81 04 00 13 02` /
`14 02` / `15 02` / `16 02` (Host hängt [02] an, FW ignoriert die Payload),
Antworten STATUS 00: 0x13/0x14 len 0x4d -> 74 B, 0x15/0x16 len 0xa4 ->
161 B (Zählbyte 0x0A + 10 x '<ffff'). Dekodierung = Host-JSON (Feld-
reihenfolge ERR_DETAIL_FORMAT bestätigt; 0x14-Zusatzfloat heißt im Host
param_peak_overcurrent_A).
- 0x11 heißt in der FW emergency_pin_mode (Host: stall_mode).
ERGÄNZUNG 17.10.2026 (statische Analyse Motor-FW und Bootloader):
- KORREKTUR idx 10: FW-Wert = Ib x s, s = -1,0 NUR bei phase_order_invert
  != 0 (@0xbb30 `lh a5,0x5a(s0)`, `beqz` -> +1,0 @0x19c98, sonst -1,0
  @0x19d14; obj+0x5a = Param-ID 10). Live phase_order_invert = 0 -> idx 10
  = +Ib (bis 17.10. fälschlich als -Ib geführt). read_phase_currents()
  nimmt phase_order_invert als Parameter (Default 0), Name idx 10 = "ib"
  (Alt-Konstante IDX_MINUS_IB bleibt als Alias).
- idx 15/16 benannt: encoder_single_turn / encoder_fdb (Counts, 32768/U).
- Fehlerbits 1/4/6 und Warnbit 0 haben in mot2_002_081 keinen Setzer; Bit 11
  = Motorcheck PhaseNotConnected (@0xe082-0xe08e); Bits 9-13 nur aus dem
  Motorcheck beim Hochlauf.
- Erase (F0 [06]) für Motorziele GESPERRT (build_erase_payload/
  update_sequence -> ValueError): Motor-App kennt 0xF0 nicht, Stock sendet
  [06] nur an CFS-Typ 1/10 (mcu_util_485 @0x15378).
- 18.10.2026 (Analyse Stock-Updateweg): Update-Builder an Stock
  angeglichen – Sektorgröße = signed Byte (0xE0 -> 128 B, Chunk 0 ->
  Fehler), Sub 02 = „App starten“ (start_app; update_end bleibt Alias),
  Sub 06 = Erase der CFS-Flash-DB-Page 0x0807F800 (kein App-Flash).
  request_update/send_fw_len HART gesperrt (auch mit confirm=True); der
  Transport (serial485.check_tx_frame) sperrt F0 außer [00]/[02]/[03].
- Broadcast-Schutz zentral in serial485.check_tx_frame: nie 0xFF/0xFD mit
  0x01 (reboot) oder 0x0B (boot), außer 0xFF 0x0B [01] (CFS-Loader->App).

Umfang:
- LESEND (live belegt, senden erlaubt):
  read_sys_param / read_sys_param_raw (0x06 [02, id]),
  check_protection (0x0C [0b]), get_stall_mode (0x11 [04]),
  read_485_addr (0x0E [02]), read_uuid (0x12 [02]),
  query_motor_version (0x0F [00] -> 12 B, live 16.10.),
  read_variable (0x08 [idx], NUR idx 1..12 -> 4 B, live 16.10.),
  read_position_feedback / read_speed_feedback / read_current_feedback
  (0x08 idx 5 / 6 / 7+8, Namen statisch belegt; pos_fdb = rad single-turn,
  live 16.10. belegt; spd_fdb rad/s belegt, id/iq A), read_position_counts
  (0x08 idx 11 = Counts 0..3199/Umdrehung, live 16.10. belegt)
- Umrechnung ohne Bus: pos_rad_to_counts, counts_to_rad, counts_to_mm,
  counts_delta (wrap-korrigierte Differenz, nur |Weg| < 1/2 Umdrehung)
- SETZEN (live belegt, sendet): set_mode (0x11 [01]/[02]/[03], Antwort = Echo)
- SCHREIBEN (live belegt, aber GESCHÜTZT): write_sys_param (0x06 [01, id]+<f)
  und update_sys_params (0x06 [03]) senden NUR mit confirm=True, sonst
  PermissionError ohne Bus-Zugriff. Sie verändern Motorregelparameter
  (Kp/Ki/Schutzgrenzen) – FLÜCHTIG bis zum nächsten Kaltstart.
- Reine Builder (ohne Senden): build_sys_param_read_payload,
  build_sys_param_write_payload, build_sys_param_update_payload
- decode_sys_param_float: Dekodier-Hilfe (<f, ohne Bus)
- read_encoder: GESPERRT (0x06-Altfalle) – "Encoder" = 0x08 idx 5/11
  (Single-Turn) -> read_position_feedback(with_counts=True)
- 0x08-Helfer (FW-Tabelle 16.10.): read_reference_values (idx 1-4),
  read_phase_currents (idx 9/10 -> Ia, Ib; phase_order_invert, Default 0),
  read_dir_input (idx 12);
  read_variable_raw_fw (idx 0..18, NUR confirm=True, eigener Bus-Zugriff,
  live für 0/13..18 NICHT geprüft)
- check_protection (0x0C [0b]) -> dekodiert (decode_protection),
  check_protection_raw -> 8 B; decode_motor_status (STATUS-Bitfeld)
- Detail-/Sample-Kommandos 0x13..0x16 LESEND (FW-statisch belegt, LIVE
  bestätigt 16.10., Request [02] wie Host, retries 0, Timeout 1 s; Host-
  Timeout unbelegt): read_err_detail, read_stall_detail, read_pos_samples,
  read_spd_samples (+ Decoder)
- 0x01/0x03/0x04/0x07/0x0B/0x0D: nur Konstanten + NotImplementedError-
  Stubs (senden NICHTS); 0x0C-Subs 01..05/07/09 nur Konstanten
- WARNUNG Host-Bug (live 16.10., zweimal reproduziert, Ursache BELEGT):
  Creality `MOTOR_GET … DATA=0` und `DATA=13` -> Klipper-SHUTDOWN.
  Traceback (klippy.log 16.10. 13:12:27): motor_control_wrapper.py:1982
  Motor_Control.motor_get -> box_wrapper.py:384 ParseData.parse_num_to_byte
  -> TypeError: 'NoneType' object cannot be interpreted as an integer.
  Creality-gcode.py lehnt DATA außerhalb minval=1/maxval=12 nur mit Warnung
  ab (get_int -> None), motor_get rechnet weiter -> Absturz VOR dem Senden.
  read_variable bleibt deshalb im G-Code-Bereich 1..12.
- Pin-IO (MOTOR_READ_ALL_PIN_IO): KEIN RS485 – dir/step-Pins der Motoren
  hängen an der Main-MCU (Klipper-GPIO PB9, PB10, !PB7, PB8, PB5, PB6, PA1,
  PB15) -> pin_io bleibt Stub (Main-MCU nötig)
- Update-Pfad (Func 0xF0, aus mcu_util_485-Decompile; auf dem Motorbus
  live NIE beobachtet): query_version/query_sector_size frei (Abfragen),
  start_app (Sub 02, Alias update_end) NUR mit confirm=True;
  request_update/send_fw_len HART gesperrt (PermissionError, kein
  Override; der Sendeschutz sperrt F0 [01]/Länge/Daten/[06] ohnehin);
  Builder + update_sequence senden nichts (nur Doku der Stock-Folge).
  HINWEIS 17.10.: Die laufende Motor-App ignoriert 0xF0 komplett (keine
  Antwort, Motor-FW-Analyse) – F0 wirkt nur im Motor-Bootloader (nicht im
  Image). Erase (Sub 0x06) für Motorziele -> ValueError.
- write_sys_param: IDs außerhalb SYS_PARAM_CONFIGURABLE zusätzlich nur mit
  allow_locked=True; Werte mit float32-Plausibilitätsprüfung.

Adressen / Pfade (live):
- X/Y/Z/Z1 = 0x81/0x82/0x83/0x84, AUSSCHLIESSLICH über serial_485
  (/dev/ttyS5, 230400, geteilt mit CFS 0x01 und Belt 0x21/0x22) – Guard
  MOTOR_ADDRS bleibt 0x81-0x84.
- EXTRUDER: NICHT 0x85 über RS485 – 0x85 antwortet dort nie (100 Sendungen,
  alle Timeout ~1 s, Live-Log 05.10.2026; Stock-Fehlgriff
  "stepper 5 -> 0x85"). Der Extruder-Motor antwortet nur über
  `transparent_send` der (vermutlich) Nozzle-MCU unter Adresse 0x81
  (Live-Log `f7 81 04 00 0e 02 80` -> `f7 81 04 00 0e 81 00`
  "get motor extruder addr: 0x81"). Dafür braucht es einen eigenen
  Transport (Creality-MCU-FW mit config_transparent) – NICHT in diesem
  Modul; ein Motor(bus, 0x81) auf ttyS5 ist die X-Achse.

Retries/Timeouts (live, klippy.log* "retries = N, cmd = ..." + Zeitabstand
bis "get response or timeout"): ALLE ~2200 Motor-Sendungen laufen mit
"retries = 0" (genau 1 Sendung, nie ein Retry) -> MOTOR_RETRIES = 0.
Gemessene Timeouts je Func: 0x06 ~1.0 s (0x85: 1.002-1.009 s, 100x),
0x12 ~1.0 s (1.002-1.006 s, 19x), 0x0E ~0.05 s (0.052-0.061 s, 25x),
0x0C ~0.05 s ([05]: 0.052-0.066 s, 50x), 0x05 ~0.05 s. 0x11 lief nie in
einen Timeout -> Wert UNBELEGT, hier 1.0 s (serial485 TIMEOUT_LONG).
Antwortzeiten aller Motoren sonst <= 0.11 s (meist < 0.035 s).
Stock wiederholt nur beim E-Motor (0x85) auf Anwendungsebene (IDs 9/10/25
je 3x) – nicht nachgebaut (0x85 gesperrt).

Nie beobachtet (bleiben Stubs bzw. unverändert): 0x01, 0x03, 0x04, 0x07,
0x0B, 0x0D, 0xF0 (0x13-0x16 seit 16.10. FW-statisch belegt UND live
bestätigt, 0x08/0x0F seit 16.10. live belegt, s. oben;
Bedeutung der Cmd-Bytes seit 16.10. aus der Host-Tabelle Motor_cmd belegt). 0x05 ([01]/[02] an X/Y bei BELT_TENSION)
wurde nie beantwortet (Live-Log 12.10.2026) -> nicht gebaut.

Frühere Belege: Protokollanalyse (Cut-Kalibrierung, Update-
Pfad, Checksumme). Frame-Format/CRC: serial485.py (build_frame/parse_frame/
crc8_cal, CRC über len..data; live gegen 36 626 RX-Frames geprüft).
"""

from serial485 import Serial485  # noqa: F401 (für echten Betrieb)
from serial485 import build_frame, parse_frame, crc8_cal  # noqa: F401 (Selbsttest)
from serial485 import check_tx_frame, TxGuardError  # noqa: F401 (Sendeschutz, Selbsttest)

import math
import struct

# --- Func-Codes Motor-Boards (live, s. Modul-Docstring) ---
# 0x06 Sys-Param (KORREKTUR 15.10.2026, live): Die Param-ID steht im REQUEST,
# die Antwort enthält NUR den Wert:
#   Lesen     [02, id]          -> <f LE (4 B); ID 10 -> 2 B; unbek. ID -> 0 B
#   Schreiben [01, id] + <f LE  -> Echo des Werts (4 B)
#   Update    [03]              -> [01] (= Erfolg)
# Der früher als "einziger Live-Frame" geführte f7 81 07 00 06 00 00 96 43 09
# ist die ANTWORT auf das Schreiben von ID 70 = 300.0
# (Live-Log 05.10.2026, Live-Log 14.10.2026).
#
# Fremd-Hypothesen grant0013 (K2) für live NIE beobachtete Codes – nur
# Abgleich, NICHT senden: 0x01 reboot, 0x03 encoder_cal, 0x04
# elec_offset_cal, 0x07 flash_param, 0x0B boot, 0x0D systemid.
# (0x08 "get feedback" -> live 16.10. Variablen-Lesepfad; 0x0F "version"
# -> live 16.10. bestätigt.)
FUNC_CONTROL = 0x05        # [01]/[02] live nie beantwortet -> NICHT gebaut
# 0x05 control, Sub = Payload-Byte 0 (Motor-FW-Analyse, B, Handler
# 0xf880): 01 Regelung AUS, 02 Regelung EIN (leert 0x15/0x16-Ringe),
# 03 Umschalten (nie senden), 04 Status lesen -> 3 B [EN-Pin, Regler, PWM].
# NUR Sub 04 antwortet; 00/01/02/03/>=05 bleiben by design OHNE Antwort
# (live 0x05 [01]/[02] -> Timeout nach ~52-58 ms). Nur Konstanten (cfswatch).
CONTROL_OFF = 0x01
CONTROL_ON = 0x02
CONTROL_TOGGLE = 0x03
CONTROL_STATUS = 0x04
CONTROL_STATUS_LEN = 3
CONTROL_STATUS_FIELDS = ("en_pin", "controller_on", "pwm_on")
FUNC_SYS_PARAM = 0x06
FUNC_READ_ENCODER = FUNC_SYS_PARAM  # Alt-Name, NUR Kompatibilität – nicht senden!
FUNC_PROTECTION = 0x0C     # Schutzstatus: Request 1 B ([0b]), Antwort 8 B
FUNC_SET_PARAMS = FUNC_PROTECTION  # Alt-Name; frühere Deutung WIDERLEGT
FUNC_ADDR = 0x0E           # [02] -> eigene Adresse; FF 0E 01 Broadcast stumm
FUNC_SET_MODE = 0x11       # Stall-Mode: [01]/[02]/[03] setzen, [04] lesen
FUNC_STALL_MODE = FUNC_SET_MODE
FUNC_UUID = 0x12           # [02] -> 8 B UUID (Präfix cd ab)
FUNC_FW_UPDATE = 0xF0      # Update-Pfad, Sub-Codes aus Decompile (Abschnitt 1.7)
FUNC_VARIABLE = 0x08       # live 16.10.: [idx] -> 4 B LE (Variablen-Lesepfad)
FUNC_VERSION = 0x0F        # live 16.10.: [00] -> 12 B Versionsblock

# Motor_cmd – vollständige Host-Tabelle (REA 16.10.2026, Modul-Init
# motor_control_wrapper.so @0x10168, Dict-Aufbau @0x10a9c; BELEGT als
# Host-Zuordnung Name -> Cmd-Byte). Live gesehen: 0x05 (unbeantwortet),
# 0x06, 0x08, 0x0C, 0x0E, 0x0F, 0x11, 0x12. Alle anderen: NICHT senden,
# nur Stubs (NotImplementedError).
FUNC_REBOOT = 0x01
FUNC_ENCODER_CALIBRATE = 0x03      # encoder_calibrate_official
FUNC_ELEC_OFFSET_CALIBRATE = 0x04
FUNC_FLASH_PARAM = 0x07            # MOTOR_FLASH_PARAM (Payload-Logik vermutet)
FUNC_GET = FUNC_VARIABLE           # Host-Name "get"
FUNC_BOOT = 0x0B                   # MOTOR_BOOT – NIE senden (Bootloader)
FUNC_SYSTEMID = 0x0D
FUNC_ERR_DETAIL_DATA = 0x13        # Snapshot letztes Fehlerereignis (74 B)
FUNC_CUT_DETAIL_DATA = 0x14        # Snapshot Stall-Ereignis (FW cut_detail, 74 B)
FUNC_POS_DATA_SAMPLE = 0x15        # Ring 10 x pos-Probe (FW pos_data, 161 B)
FUNC_SPD_DATA_SAMPLE = 0x16        # Ring 10 x spd-Probe (FW spd_data, 161 B)
# Motor-FW-Namen (Registrierung @0x6100-0x63d8, Motor-FW-Analyse), wo sie
# vom Host-Namen abweichen: 0x11 emergency_pin_mode, 0x14 cut_detail_data,
# 0x15 pos_data, 0x16 spd_data, 0x0E rs485_addr, 0x02 encoder_calibrate.
MOTOR_FW_CMD_NAMES = {
    0x00: "help", 0x01: "reboot", 0x02: "encoder_calibrate",
    0x03: "encoder_calibrate_official", 0x04: "elec_offset_calibrate",
    0x05: "control", 0x06: "sys_param", 0x07: "flash_param", 0x08: "get",
    0x0B: "boot", 0x0C: "protection", 0x0E: "rs485_addr", 0x0F: "version",
    0x11: "emergency_pin_mode", 0x12: "dev_uuid", 0x13: "err_detail_data",
    0x14: "cut_detail_data", 0x15: "pos_data", 0x16: "spd_data",
}
MOTOR_CMD = {
    "reboot": FUNC_REBOOT, "encoder_calibrate_official": FUNC_ENCODER_CALIBRATE,
    "elec_offset_calibrate": FUNC_ELEC_OFFSET_CALIBRATE, "control": FUNC_CONTROL,
    "sys_param": FUNC_SYS_PARAM, "flash_param": FUNC_FLASH_PARAM,
    "get": FUNC_GET, "boot": FUNC_BOOT, "protection": FUNC_PROTECTION,
    "systemid": FUNC_SYSTEMID, "read485_addr": FUNC_ADDR,
    "set485_addr": FUNC_ADDR, "version": FUNC_VERSION,
    "stall_mode": FUNC_STALL_MODE, "dev_uuid": FUNC_UUID,
    "err_detail_data": FUNC_ERR_DETAIL_DATA,
    "cut_detail_data": FUNC_CUT_DETAIL_DATA,
    "pos_data_sample": FUNC_POS_DATA_SAMPLE,
    "spd_data_sample": FUNC_SPD_DATA_SAMPLE,
}
# Motor_Addr (Host, Schlüssel = NUM-String): '0'..'8' -> 0x80..0x88,
# '9' -> 0xFF (Broadcast). MOTOR_NUM_TO_NAME 1..5 -> x, y, z, z1, e.
# Hinweis: NUM 5 (e) -> 0x85 antwortet auf ttyS5 nie (Extruder transparent).
MOTOR_ADDR_BY_NUM = dict([(str(n), 0x80 + n) for n in range(9)] + [("9", 0xFF)])
MOTOR_NUM_TO_NAME = {1: "x", 2: "y", 3: "z", 4: "z1", 5: "e"}

# 0x13 err_detail_data: Host-Unpack-Format @0x3a3f0 (BELEGT als Host-
# Format). MOTOR-FW 16.10. (statisch) + LIVE bestätigt 16.10. (Motor X,
# Webhook motor_err_detail_data): Request `81 04 00 13 02` (der Host hängt
# [02] an; die FW liest 0x6cc nicht -> Payload egal, Nachbau sendet wie
# der Host DETAIL_REQUEST_PAYLOAD), Antwort len 0x4d -> 74 B = 66 B
# Grundformat + 8 B Union (Inhalt je Fehlertyp, ERR_CODE_TO_UNION).
# Snapshot des LETZTEN Fehlerereignisses (erfasst @0xc172 bei jedem in
# Maske 0x400 freigegebenen Fehlerbit), bleibt bis zum nächsten Ereignis,
# auch über protection [05]. Live (kein Fehler seit Boot): err_code 0,
# Messfelder 0, nur statische Felder gefüllt (dev_uuid, elec_offset 123,58,
# phase_order_invert 0, elec_offset_err_deg 3,867) – deckt sich mit „Handler
# kopiert nur statische Felder nach“.
# 0x14 cut_detail_data: gleiche 74 B, Snapshot beim STALL-Ereignis (Stall-
# Callback @0xbf44); der Host entpackt 70 B (= Grundformat + 1 float). Name
# des Zusatzfloats LIVE aus dem Host-JSON (Webhook motor_cut_detail_data):
# param_peak_overcurrent_A (= erste 4 B der Union; live 0,012 trotz
# err_code 0 -> Bedeutung/Einheit nur Host-Name, Wert unplausibel klein für
# einen Spitzenstrom-Grenzwert). Feldreihenfolge/Format live gegen alle
# Host-JSON-Werte bestätigt (Selbsttest 7e).
ERR_DETAIL_FORMAT = "<I8BfHfiiffffffffi"
CUT_DETAIL_FORMAT = ERR_DETAIL_FORMAT + "f"
ERR_DETAIL_BASE_LEN = 66
DETAIL_UNION_LEN = 8
DETAIL_LEN = ERR_DETAIL_BASE_LEN + DETAIL_UNION_LEN   # 74 B (FW, 0x13 und 0x14)
ERR_DETAIL_FIELDS = (
    "err_code", "dev_uuid", "elec_offset", "phase_order_invert",
    "elec_offset_err_deg", "encoder_fdb", "encoder_fdb_last",
    "pos_fdb_encoder", "spd_fdb_encoder", "spd_fdb_ctrl", "id", "iq",
    "board_detect_power_supply_V", "board_detect_temperature_C",
    "pos_loop_pid_err", "step_increment")  # dev_uuid = 8 B
CUT_DETAIL_EXTRA_FIELD = "param_peak_overcurrent_A"   # Host-Name (live 16.10.)
# Request-Payload 0x13..0x16: wie der Host [02] (live 16.10.); die FW wertet
# sie nicht aus (Motor-FW-Analyse: kein Lesezugriff auf 0x6cc).
DETAIL_REQUEST_PAYLOAD = (0x02,)

# 0x15 pos_data / 0x16 spd_data (MOTOR-FW 16.10., statisch; LIVE bestätigt
# 16.10., Webhook motor_err_detail_data sendet nach 0x13 auch 0x15/0x16):
# Request [02] (s. DETAIL_REQUEST_PAYLOAD), Antwort len 0xa4 = Zählbyte
# 0x0A + 10 Proben je '<ffff' (16 B) = 161 B, älteste zuerst (Ringkopf
# obj+0x88c gemeinsam). Geschrieben nur bei ctrl+4 == 1 (@0xb91c); kein
# Start, kein Löschen. Feldnamen = Host-Strings; Zuordnung Name-Reihenfolge
# live = Host-JSON (pos: target_pos 2,159847 an Position 3, leso_err 0 an
# Position 4; fdb/leso im Stillstand gleich -> nicht unterscheidbar; spd:
# alle ~0, Reihenfolge nur konsistent, nicht unterscheidbar).
SAMPLE_COUNT = 10
SAMPLE_FORMAT = "<ffff"
SAMPLE_LEN = 1 + SAMPLE_COUNT * 16   # 161 B
POS_SAMPLE_FIELDS = ("fdb_pos", "leso_pos", "target_pos", "leso_err")
SPD_SAMPLE_FIELDS = ("fdb_spd", "leso_spd", "target_spd", "leso_in_u")
TIMEOUT_DETAIL = 1.0       # 0x13..0x16: Host-Timeout unbelegt; live Antwort
                           # nach 0,014..0,174 s (Log-Zeitstempel inkl. Logging)

# Fehler-/Warnbits (Host-Dicts motor_error_dict / motor_warning_dict,
# Modul-Init, BELEGT als Bit -> key-Zuordnung; Kurztexte = Übersetzung der
# key-Bedeutung laut statischer Analyse). Byte-Layout der 8-B-Antwort von 0x0C [0b]
# seit Motor-FW-Analyse 16.10. BELEGT (FW + Host-Parser): Byte 0-3 = Fehler
# latched u32 LE, Byte 4-7 = Warnung latched u32 LE; Bit n -> key781+n bzw.
# key795+n (Host-Index i = 8*Byte + Bit = LE-Bitnummer) -> decode_protection.
# FW-Setzer (0xde18-0xe0ae): Bit 0..3, 5..10, 12, 13 belegt, Bit 4 (HW-
# Überstrom) Setzer nicht lokalisiert, Bit 11 in der Analyse ohne Beleg;
# Warnung Bit 1 Unterspannung (0x480 < min), Bit 2 MCU-Übertemperatur
# (0x484 > max), Bit 0 Setzer nicht lokalisiert. Live nur 8x 00.
# PRÄZISIERT 17.10.2026 (Motor-FW-Analyse, B; alle Schreibstellen auf
# obj+0x428/0x438 aufgezählt): Fehlerprüfung FUN_ddf2 wird nur mit n = 0, 2,
# 3, 5, 7, 8, 9 aufgerufen -> Bit 1 (EncoderReadErr), Bit 4 (HW-Überstrom,
# Tabelleneintrag nur Latch-Neuberechnung @0xdfe8) und Bit 6 (PosFdbOverLimit)
# werden in mot2_002_081 NIE gesetzt; Warnbit 0 (CommunicationErr) ebenfalls
# nie. Bit 11 = Motorcheck „PhaseNotConnected“ (@0xe082-0xe08e). Bits 9-13
# stammen nur aus dem Motorcheck beim Hochlauf (FUN_e51c; FW-Namen als
# Zweitbeschriftung in MOTOR_ERROR_BITS_MOTORCHECK). Zyklisch setzbar sind
# nur Bits 0, 2, 3, 5, 7, 8 und Warnbits 1, 2.
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
# 17.10.2026 (Motor-FW-Analyse, B): in mot2_002_081 ohne Setzer
MOTOR_ERROR_BITS_NEVER_SET = (1, 4, 6)
MOTOR_WARNING_BITS_NEVER_SET = (0,)
# Motorcheck-Ergebnis obj+0x1d6 -> Fehlerbit, FW-Namen (Zweitbeschriftung)
MOTOR_ERROR_BITS_MOTORCHECK = {
    9: "ValueAbnormal", 10: "UnStable", 11: "PhaseNotConnected",
    12: "ResistanceDifferent", 13: "OtherErr",
}
ERROR_CODE_BITS = 32       # u32, genutzt Bit 0..13
WARNING_CODE_BITS = 32     # u32, genutzt Bit 0..2

# STATUS-Byte (Byte 3) der Motorantwort = Bitfeld (MOTOR-FW, Antwort-
# Builder @0xdd6e, BELEGT statisch; live bisher immer 00). Im Request 00.
MOTOR_STATUS_STALL = 0x01        # bit0: Stall-Flag (obj+0x40 = 0x08 idx 14)
MOTOR_STATUS_ERR_LATCHED = 0x02  # bit1: latched Fehlercode != 0
MOTOR_STATUS_WARN_LATCHED = 0x04  # bit2: latched Warncode != 0
MOTOR_STATUS_KNOWN = 0x07
# ERR_CODE_TO_UNION (Host): Fehlerbit-WERT -> (Format, Felder) der Detail-
# daten. 256 -> tracking_error: Schlüssel VERMUTET (letztes Element).
ERR_CODE_TO_UNION = {
    1: ("<ii", ("lower_encoder_pulse", "upper_encoder_pulse")),
    4: ("<f", ("peak_overcurrent_A",)),
    8: ("<ff", ("continuous_overcurrent_A", "continuous_overcurrent_time_s")),
    32: (None, ("speed_over_limit",)),
    64: (None, ("pos_over_limit_min", "pos_over_limit_max")),
    128: (None, ("pos_cmd_mutation_turns",)),
    256: (None, ("tracking_error",)),
}

# 0x0F Version (live 16.10.2026, MOTOR_VERSION NUM=1 DATA=0):
#   TX data_send `81 04 00 0f 00`
#   RX `f7 81 0f 00 0f 01 09 07 51 01 03 01 03 02 4b 01 00 02`
#   12 B = fireware_ver 4 B (1.9.7.81) | cmd_system_ver 2 B (1.3) |
#          protection_ver 2 B (1.3) | flash_param_ver 2 B (2.75) |
#          logger_ver 2 B (1.0). Feldnamen = Strings motor_control_wrapper.so.
SUB_VERSION_GET = 0x00
VERSION_LEN = 12
VERSION_FIELDS = (("fireware_ver", 4), ("cmd_system_ver", 2),
                  ("protection_ver", 2), ("flash_param_ver", 2),
                  ("logger_ver", 2))

# 0x08 Variable (live 16.10.2026, MOTOR_GET NUM=1 DATA=idx, Scan 1..12,
# Rohdaten lokal, nicht im Repo): TX `81 04 00 08 <idx>`, RX 4 B LE.
# Host-Text "系统所支持变量当前值" (aktueller Wert einer vom System
# unterstützten Variable); Namen gibt der Host NICHT aus.
# Host-Bug: DATA=0 und DATA=13 -> Klipper-SHUTDOWN (key60) -> nur 1..12.
# Ursache BELEGT (Traceback 16.10. 13:12:27): G-Code-Grenze minval=1/
# maxval=12 -> get_int -> None (nur Warnung) -> parse_num_to_byte(None)
# -> TypeError, kein Frame geht raus.
VARIABLE_LEN = 4
VARIABLE_INDEX_MIN = 1
VARIABLE_INDEX_MAX = 12   # G-Code-Grenze des Hosts (maxval=12)
# Indexnamen. Quellen: (H) Host-Dict NO_ERR_CODE_TO_GET @0x10cf8-0x10d60
# (REA, BELEGT: 5..8); (F) Motor-FW-GET-Sprungtabelle @0x1bce4, idx 0..18
# (Motor-FW-Analyse 16.10., statisch BELEGT, Feld-Adressen dort); (L) live
# 16.10. (Standby-Scan, Messfahrt X, Fahrt F600/F1200, DIR-Gegenprobe).
# Belegstatus je Index in VARIABLE_TABLE[idx][2]:
#   "belegt"   = FW-Code und Live-Werte passen
#   "fw"       = nur FW-statisch belegt (live nicht erreichbar/geprüft)
#   "vermutet" = Deutung aus FW-Code abgeleitet, nicht zwingend
#   "offen"    = Feld unbekannt
# Einheit: rad / rad/s / A / counts / °C / V, "0/1" = Pegel/Flag, None = -.
# Strom-Einheit A: FW rechnet raw*Vref/4096/(Gain*R) (@0x5ef2); dass
# Gain*R = 1 Ohm ist (0,806 mA/LSB), ist VERMUTET (Flash-Parameter
# driver_board_amp_gain / R_of_current_sample_ohm lesend prüfbar).
# Namen 1..4/9/10/12..18 sind EIGENE Nachbau-Namen nach FW-Deutung (der
# Host hat dafür keine), 17/18 = FW-Strings board_detect_*.
VARIABLE_TABLE = {
    0: ("const_zero", None, "fw", "FW sendet konstant 0"),
    1: ("pos_ref", "rad", "belegt",
        "Positions-Sollwert (ctrl+0x118 = target_pos + 0x84, begrenzt), single-turn"),
    2: ("spd_ref", "rad/s", "belegt",
        "Drehzahl-Sollwert (ctrl+0x1e4 = Pos-PI-Ausgang + Vorsteuerung); live 1,633 @ 1,571 Soll"),
    3: ("id_ref", "A", "belegt",
        "d-Strom-Sollwert (ctrl+0x2b0); FOC ohne Feldschwächung -> live 0"),
    4: ("iq_ref", "A", "belegt",
        "q-Strom-Sollwert (ctrl+0x37c); live ~ iq_fdb"),
    5: ("pos_fdb", "rad", "belegt",
        "Host-Name; FW = LESO-Positionsbeobachter leso_pos (ctrl+0xbc), "
        "single-turn = idx11*2pi/3200"),
    6: ("spd_fdb", "rad/s", "belegt",
        "Host-Name; FW = LESO-Drehzahl leso_spd = spd_fdb_ctrl (ctrl+0xc0); "
        "live 1,53-1,57 @ 1,571 Soll"),
    7: ("id_fdb", "A", "belegt", "d-Strom-Istwert (ctrl+0x60c), Host-Name"),
    8: ("iq_fdb", "A", "belegt", "q-Strom-Istwert (ctrl+0x610), Host-Name"),
    9: ("ia", "A", "belegt",
        "Phasenstrom Ia (ADC-Kanal 0 - Offset); Stillstand 0,00161 = 2 LSB"),
    10: ("ib", "A", "belegt",
         "Phasenstrom Ib (ADC-Kanal 1 - Offset) x s, s = +1,0 bei "
         "phase_order_invert 0 (live), -1,0 sonst (@0xbb30 lh 0x5a(obj); "
         "beqz -> +1,0 @0x19c98, sonst -1,0 @0x19d14); Stillstand 0,00081 "
         "= 1 LSB. KORREKTUR 17.10.: nicht pauschal -Ib"),
    11: ("pos_counts", "counts", "belegt",
         "Schrittpuls-Zählerstand (obj+0x7bc u32, STEP-Eingang), 0..3199 = "
         "3200/Umdrehung; x2pi/3200 = Soll-Winkel"),
    12: ("dir_input", "0/1", "belegt",
         "Pegel DIR-Eingang (pins+0x110); live -X = 0, +X = 1, Stillstand "
         "hält letzte Richtung (Gegenprobe 16.10.)"),
    13: ("en_input", "0/1", "vermutet",
         "Pegel EN-Eingang (pins+0x118): 1 -> Regelung aus"),
    14: ("stall_flag", "0/1", "fw",
         "Stall-Flag (obj+0x40) = STATUS bit0 (|iq_ref| bzw. |pos-PID-Fehler| > Schwelle)"),
    15: ("encoder_single_turn", "counts", "fw",
         "Encoder Single-Turn 0..32767 (enc+0x24, richtungskorrigiert), "
         "32768 Counts/U; rad = x*2pi/32768; live nie gelesen"),
    16: ("encoder_fdb", "counts", "fw",
         "encoder_fdb (enc+0x2c -> obj+0x84, Feld encoder_fdb in 0x13/0x14): "
         "bei singleturn_control_mode 1 (FW-Default) == idx 15, sonst "
         "Umdrehungen*32768 + idx 15; live nie gelesen"),
    17: ("board_detect_temperature_C", "°C", "fw",
         "MCU-Temperatursensor (Kalibrierung 0x1FFFF720)"),
    18: ("board_detect_power_supply_V", "V", "fw",
         "Versorgungsspannung (ADC x Vref x power_voltage_sample_gain)"),
}
VARIABLE_FW_INDEX_MIN = 0
VARIABLE_FW_INDEX_MAX = 18   # > 18: FW sendet veralteten Scratch-Wert (gp-0x7b4)
VARIABLE_NAMES_HOST = {5: "pos_fdb", 6: "spd_fdb", 7: "id_fdb", 8: "iq_fdb"}
VARIABLE_NAMES_DERIVED = {11: "pos_counts"}   # live 16.10. abgeleitet (Alt-Name)
# idx -> Name für alle Indizes 0..18 (15/16 seit 17.10. benannt)
VARIABLE_NAMES = dict((i, v[0]) for i, v in VARIABLE_TABLE.items() if v[0])
VARIABLE_UNITS = dict((i, v[1]) for i, v in VARIABLE_TABLE.items())
VARIABLE_STATUS = dict((i, v[2]) for i, v in VARIABLE_TABLE.items())
IDX_POS_FDB, IDX_SPD_FDB, IDX_ID_FDB, IDX_IQ_FDB = 5, 6, 7, 8
IDX_POS_COUNTS = 11
IDX_POS_REF, IDX_SPD_REF, IDX_ID_REF, IDX_IQ_REF = 1, 2, 3, 4
IDX_IA, IDX_IB, IDX_DIR_INPUT = 9, 10, 12
IDX_MINUS_IB = IDX_IB   # Alt-Name (bis 17.10. fälschlich "-Ib"), nur Kompatibilität
IDX_ENCODER_SINGLE_TURN, IDX_ENCODER_FDB = 15, 16
ENCODER_COUNTS_PER_REV = 32768   # enc+0x14 = 0x8000 (Motor-FW-Analyse)
# Vorzeichen idx 10 (Motor-FW-Analyse): obj+0x5a = Sys-Param ID 10
# param_phase_order_invert (live 2 B `00 00`, Live-Log; 0x13 live 0).
SYS_PARAM_PHASE_ORDER_INVERT = 10
PHASE_ORDER_INVERT_LIVE = 0
# Skalierung (BELEGT live 16.10.): idx 5 pos_fdb [rad] = idx11 * 2*pi/3200.
# 3200 = 200 Vollschritte x 16 Microsteps ([stepper_x] microsteps 16);
# rotation_distance 40 -> 80 Counts/mm. Live-Rest pos_fdb - idx11*2pi/3200
# ~ +8e-5 rad (~0,04 Counts, Ursache offen) -> Toleranz POS_RAD_TOL.
POS_COUNTS_PER_REV = 3200
POS_ROTATION_DISTANCE = 40.0   # mm/Umdrehung, printer.cfg [stepper_x] (live)
POS_RAD_TOL = 2e-4
# Typkette des Hosts (REA BELEGT: motor_get @0x51524, Py-Zeile 1987/1989,
# Vergleiche @0x51e04-0x520e8 und @0x526bc-0x527c8; gleiche Kette in
# motor_extruder_get und _motor_no_cut_detail_data). Fest verdrahtet, der
# Motor liefert keinen Typ. Wertbytes = result[5:result[2]+2] (Vollframe,
# = [5:9] bei len 7 = die 4 Datenbytes). 13..18 kennt der Host, sie sind
# per G-Code aber nicht erreichbar und am Motor live NICHT geprüft ->
# read_variable sperrt sie bewusst.
VARIABLE_HOST_FORMATS = dict([(i, "<f") for i in list(range(1, 11)) + [17, 18]]
                             + [(i, "<I") for i in range(11, 17)])
# Typ je G-Code-erreichbarem Index (1..12): "float" = '<f', "int" = '<I'
# (UNSIGNED laut Host; bis 16.10. fälschlich '<i').
VARIABLE_TYPES = dict((i, "float" if VARIABLE_HOST_FORMATS[i] == "<f" else "int")
                      for i in range(VARIABLE_INDEX_MIN, VARIABLE_INDEX_MAX + 1))
# FW-seitige Formate idx 0..18 (für read_variable_raw_fw): Host-Kette, die
# laut Motor-FW-Analyse zu den FW-Feldern passt (float 1..10/17/18, Ganzzahl
# 11..16); idx 0 = konstant 0, Typ ohne Bedeutung -> '<I'.
VARIABLE_FW_FORMATS = dict([(0, "<I")] + list(VARIABLE_HOST_FORMATS.items()))

# Sub-Codes 0x06 (live)
SUB_SYS_WRITE = 0x01       # "sys参数操作-写入成功" (Live-Log 12.10.2026)
SUB_SYS_READ = 0x02        # "sys参数操作-读取" (Live-Log)
SUB_SYS_UPDATE = 0x03      # "Updata param / sys参数操作-更新" (Live-Log)
SYS_UPDATE_OK = 0x01       # Antwort auf [03]

# Sub-Codes 0x0C (Payload-Byte 0; MOTOR-FW Sprungtabelle @0x1bd30, 12
# Einträge, statisch BELEGT; live nur [0b] beantwortet, [05] nie).
# FW-Felder: Fehler roh 0x428 / latched 0x42c (= roh & Maske 0x400),
# Warnung roh 0x438 / latched 0x43c (& Maske 0x404), Backup 0x434/0x444
# (vermutlich Flash-Param protect_err/warning_code_backup). Byteweise ab
# niedrigster Adresse gesendet = u32 Little-Endian.
# NUR [0b] wird über eine öffentliche Methode gesendet (check_protection,
# = Stock MOTOR_CHECK_PROTECTION_AFTER_HOME DATA=11). Die übrigen Subs sind
# NUR Konstanten:
#   01..05 zustandsändernd (protect_en, protect_report, Codes löschen),
#   07/09/0B: Seiteneffekt @0x10616 – steht emergency_pin_mode/stall_mode
#   (obj+0x32) auf 2 (Notfall-Ausgang), gibt die FW nach der Antwort den
#   Notfall-/Stall-Pin frei (quittiert die Notfall-Leitung, Codes bleiben).
#   Das gilt auch für [0b] – Stock sendet [0b] trotzdem (Boot, nach Homing).
#   06/08/0A lesen nur die Backups (ohne Seiteneffekt, live nie gesendet).
SUB_PROT_NOP = 0x00             # nichts, keine Antwortdaten
SUB_PROT_EN_OFF = 0x01          # protect_en = 0 (obj+0x3f4)        – NICHT senden
SUB_PROT_EN_ON = 0x02           # protect_en = 1                     – NICHT senden
SUB_PROT_REPORT_OFF = 0x03      # protect_report = 0 (obj+0x3f6)     – NICHT senden
SUB_PROT_REPORT_ON = 0x04       # protect_report = 1                 – NICHT senden
SUB_PROT_CLEAR = 0x05           # löscht roh/latched/Vorzyklus (Backups bleiben)
SUB_PROT_ERR_BACKUP = 0x06      # -> 4 B Fehler-Backup
SUB_PROT_ERR_LATCHED = 0x07     # -> 4 B Fehler latched (*Seiteneffekt)
SUB_PROT_WARN_BACKUP = 0x08     # -> 4 B Warn-Backup
SUB_PROT_WARN_LATCHED = 0x09    # -> 4 B Warn latched (*Seiteneffekt)
SUB_PROT_BACKUPS = 0x0A         # -> 8 B Fehler-Backup + Warn-Backup
SUB_PROT_CHECK = 0x0B      # -> 8 B Fehler latched + Warn latched (*Seiteneffekt);
                           # MOTOR_CHECK_PROTECTION_AFTER_HOME DATA=11 (Live-Log)
SUB_PROT_LATCHED = SUB_PROT_CHECK
SUB_PROT_UNKNOWN_05 = SUB_PROT_CLEAR  # Alt-Name; live nie beantwortet
                                      # (Live-Log) -> nie senden
SUB_PROT_SIDE_EFFECT = (SUB_PROT_ERR_LATCHED, SUB_PROT_WARN_LATCHED, SUB_PROT_CHECK)
SUB_PROT_STATE_CHANGING = (SUB_PROT_EN_OFF, SUB_PROT_EN_ON, SUB_PROT_REPORT_OFF,
                           SUB_PROT_REPORT_ON, SUB_PROT_CLEAR)
PROTECTION_LEN = 8         # Antwort 8 B (live immer 8x 00 = kein Fehler)
STALL_MODE_EMERGENCY = 2   # 0x11-Modus 2 = Notfall-Ausgang (Seiteneffekt s. oben)

# Sub-Codes 0x11 (live)
STALL_MODES = (0x01, 0x02, 0x03)  # setzen, Antwort = Echo
SUB_STALL_GET = 0x04       # lesen -> [mode, 00] (Live-Log 12.10.2026)

SUB_ADDR_READ = 0x02       # 0x0E [02] -> [addr]
SUB_UUID_READ = 0x02       # 0x12 [02] -> 8 B
UUID_LEN = 8
UUID_PREFIX = b"\xcd\xab"  # live bei allen 4 Motoren

# Retries/Timeouts (live, s. Modul-Docstring)
MOTOR_RETRIES = 0          # "retries = 0" bei allen Motor-Sendungen
TIMEOUT_SYS_PARAM = 1.0    # 0x06 (0x85-Timeouts 1.002-1.009 s)
TIMEOUT_PROTECTION = 0.05  # 0x0C ([05]-Timeouts 0.052-0.066 s)
TIMEOUT_ADDR = 0.05        # 0x0E (0.052-0.061 s)
TIMEOUT_UUID = 1.0         # 0x12 (1.002-1.006 s)
TIMEOUT_STALL = 1.0        # 0x11: nie Timeout beobachtet -> UNBELEGT (TIMEOUT_LONG)
# 0x0F/0x08: live 16.10. nur je 1 Antwort (Moonraker-G-Code), kein Timeout
# beobachtet -> Wert UNBELEGT, 1.0 s (serial485 TIMEOUT_LONG). retries=0
# wie alle Motor-Sendungen.
TIMEOUT_VERSION = 1.0      # unbelegt
TIMEOUT_VARIABLE = 1.0     # unbelegt

# Sub-Codes Update-Pfad (Beleg: Protokollanalyse, je 1 Byte, 3 Retries)
SUB_GET_VERSION = 0x00     # get version
SUB_REQUEST_UPDATE = 0x01  # request update
SUB_GET_SECTOR = 0x03      # get sector size
SUB_ERASE_DB_PAGE = 0x06   # 18.10.2026: Erase der CFS-Flash-DB-/Parameter-
                           # Page 0x0807F800 (CFS-Bootloader case 6, Konstante
                           # @0x08001dd8; Arbeitsmodus Key6, Version Key5 ->
                           # Werkseinstellung). KEIN Teil des App-Flashens
                           # (Stock-Updateweg). Stock
                           # sendet es nur an CFS-Typ 1/10; für Motoren hier
                           # gesperrt (build_erase_payload), im Transport
                           # immer gesperrt (serial485.TX_F0_ALLOWED_SUBS).
SUB_ERASE_SECTOR = SUB_ERASE_DB_PAGE   # Alt-Name (bis 17.10.2026)
SUB_START_APP = 0x02       # 18.10.2026: „App starten“ (Bootloader -> App;
                           # CFS-Bootloader nur bei gültiger App-CRC: Ack 0x75,
                           # 3 s Pause, Sprung; sonst 0x1F). Stock sendet es
                           # bei JEDEM Boot an alle adressierten Geräte außer
                           # „fail“ (Stock-Updateweg). Beleg
                           # mcu_util_485 @0x15a50..0x15a8c: Zustand 10, Func
                           # 0xF0, 1 Byte = 0x02, Timeout 500 ms.
SUB_UPDATE_END = SUB_START_APP         # Alt-Name: war fälschlich „update_end“;
                           # das Transfer-Ende meldet der Bootloader mit 0x20
                           # nach dem letzten Block (BL_ACK_DONE), nicht Sub 02.
START_APP_TIMEOUT = 0.5    # s, Beleg: mov r7,#500 -> [sp,#8] vor bl 0x148a4
UPDATE_END_TIMEOUT = START_APP_TIMEOUT # Alt-Name
# Bootloader-Antwortcodes im Datenteil (Stock-Updateweg, B)
BL_ACK_OK = 0x75           # weiter / angenommen
BL_ACK_DONE = 0x20         # letzter Block geschrieben = Transfer fertig
BL_ACK_CRC_ERR = 0x1F      # Prüfsumme/Länge/Request fehlerhaft
BL_ACK_FLASH_ERR = 0x21    # Flash-Schreibfehler nach 3 Versuchen

# Retries Update-Pfad: mcu_util_485 bricht nach 3 Fehlversuchen ab (Zähler
# r6, `cmp r6,#3` bei 0x1562c bzw. 0x15b78). serial485.transact macht
# retries+1 Versuche -> retries=2 = 3 Versuche des Binarys. Gilt NUR für
# F0-Kommandos; alle Live-Motorkommandos nutzen MOTOR_RETRIES = 0.
UPDATE_RETRIES = 2

# Sektorgröße (Antwort auf F0 [03]) = SIGNED Byte, 18.10.2026 korrigiert
# (Bootloader-Analyse, Stock-Updateweg;
# mcu_util_485 `ldrsb r1,[r4,#40]` @0x15560, @0x15858-0x15860):
#   s < 0  -> Chunk = (s * 252) & 0xFF   (0xE0 = -32 -> 128 B, CFS-Bootloader)
#   s >= 0 -> Chunk 0 (Stock: s == 0 „invalid sector size!“, s > 0 still
#             KEIN Datenblock = latenter Host-Bug) -> hier ValueError.
# Bis 17.10.2026 fälschlich als Bytezahl 1..127 geführt.
SECTOR_BYTE_CFS = 0xE0     # fest im CFS-Bootloader cfs0_050_G32 -> 128 B
SECTOR_CHUNK_FACTOR = 0xFC # 252

# Gerätetypen der Auto-Adressierung/mcu_util_485 (auto_addr DEV_TYPE_*):
# 1 = CFS-Box (MB), 2 = Closed-loop-Motor (CLM), 10 = CFS-Pro. Erase F0 [06]
# sendet der Stock-Updater NUR an Typ 1/10 (mcu_util_485 Erase-Zustand 5
# @0x15378-0x153d4: `ldr r5,[node,#0x10]; cmp r5,#1; cmpne r5,#10`).
DEV_TYPE_CFS = 1
DEV_TYPE_MOTOR = 2
DEV_TYPE_CFS_PRO = 10
ERASE_DEV_TYPES = (DEV_TYPE_CFS, DEV_TYPE_CFS_PRO)

PARAM_LEN_0C = PROTECTION_LEN  # Alt-Name
FW_LEN_BYTES = 4           # Beleg: 1.7 "4 Bytes = send fw len (32-bit LE)"

MOTOR_ADDRS = (0x81, 0x82, 0x83, 0x84)  # X/Y/Z/Z1 über serial_485 (ttyS5)
MOTOR_AXES = {0x81: "X", 0x82: "Y", 0x83: "Z", 0x84: "Z1"}
EXTRUDER_TRANSPARENT_ADDR = 0x81  # E-Motor NUR transparent (Nozzle-MCU), s. Docstring

# --- Sys-Param-Tabelle (live, flash_param-Enumeration Live-Log) ---
# 227 IDs (0..226), Namen identisch zu grant0013 (inkl. Original-Tippfehler
# "standar"/"calibtate"/"parma"). Index = Param-ID. Achtung Log-Falle: in
# "update_flash_param key:... value:N" ist N die ID, nicht der Wert.
SYS_PARAM_NAMES = (
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
# Plus-Log: alles AUSSER "is not configurable" (166 IDs) – d.h. 45 "is not
# need override" + 13 per [motor_control] konfiguriert + 3 Kalibrierwerte.
SYS_PARAM_CONFIGURABLE = frozenset((
    9, 10, 12, 13, 14, 20, 21, 23, 25, 41, 42, 43, 66, 67, 70, 86, 87, 89,
    94, 95, 97, 102, 103, 105, 110, 111, 113, 116, 118, 119, 120, 121, 122,
    123, 127, 128, 130, 141, 142, 143, 144, 145, 173, 179, 180, 181, 182,
    183, 187, 188, 189, 190, 200, 204, 217, 218, 220, 221, 222, 223, 226))
# Stock liest immer (Kalibrierwerte, kein Write): 9/10/25
SYS_PARAM_STOCK_ALWAYS_READ = (9, 10, 25)
# Stock-K2-Plus-Config [motor_control] setzt (nach JEDEM Kaltstart neu, da
# 0x06/01 flüchtig): 226 nur X/Y und von der Motor-FW unbekannt (0-B-Antwort)
SYS_PARAM_STOCK_CONFIGURED = (70, 86, 87, 102, 103, 116, 118, 119, 120, 121,
                              187, 188, 226)


def _check_param_id(param_id):
    if isinstance(param_id, bool) or not isinstance(param_id, int):
        raise ValueError("Param-ID muss int sein: %r" % (param_id,))
    if not 0 <= param_id < SYS_PARAM_COUNT:
        raise ValueError("Param-ID muss 0..%d sein (live 227 IDs): %r"
                         % (SYS_PARAM_COUNT - 1, param_id))


def sys_param_name(param_id):
    """Param-ID -> Name (live-Tabelle SYS_PARAM_NAMES)."""
    _check_param_id(param_id)
    return SYS_PARAM_NAMES[param_id]


def sys_param_id(name):
    """Name -> Param-ID (ValueError, wenn unbekannt)."""
    try:
        return SYS_PARAM_NAMES.index(name)
    except ValueError:
        raise ValueError("unbekannter Sys-Param-Name: %r" % (name,))


def decode_sys_param_float(data):
    """4 Bytes 0x06-Antwort -> Float (Little-Endian, `<f`). KEIN Bus-Zugriff.

    Beleg live: f7 81 07 00 06 20 29 f7 42 59 -> 123.58 (ID 9 an X,
    Live-Log); 00 00 96 43 -> 300.0 / 00 00 c8 42 -> 100.0 (ID 70,
    Live-Log 14.10.2026 / Live-Log 12.10.2026).
    """
    raw = bytes(data)
    if len(raw) != 4:
        raise ValueError("0x06-Payload muss 4 Bytes haben: %r" % (raw,))
    return struct.unpack("<f", raw)[0]


FLOAT32_MAX = 3.4028234663852886e38   # größter endlicher float32-Betrag


def _float_bytes(value):
    """Wert -> 4 Bytes `<f` mit Plausibilitätsprüfung (ValueError bei):
    nicht int/float bzw. bool, nicht endlich, |Wert| > float32-Max, oder
    Eingabe != 0 rundet in float32 auf 0 (z.B. 1e-50 – struct schreibt das
    sonst STILL als 0.0)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Sys-Param-Wert muss int/float sein: %r" % (value,))
    if not math.isfinite(value):
        raise ValueError("Sys-Param-Wert muss endlich sein: %r" % (value,))
    if abs(value) > FLOAT32_MAX:
        raise ValueError("Sys-Param-Wert |%r| > float32-Max %r" % (value, FLOAT32_MAX))
    try:
        raw = struct.pack("<f", value)
    except OverflowError:
        raise ValueError("Sys-Param-Wert passt nicht in float32: %r" % (value,))
    if value != 0 and struct.unpack("<f", raw)[0] == 0.0:
        raise ValueError("Sys-Param-Wert %r rundet in float32 auf 0.0 (Unterlauf)"
                         % (value,))
    return raw


def float32_rounding(value):
    """(float32-Wert, relative Abweichung) für einen Sys-Param-Wert, ohne Bus.
    Gleiche Prüfungen wie beim Schreiben (ValueError). Abweichung =
    |f32 - value| / |value| (0.0 bei value == 0); z.B. 0.1 -> ~1.5e-8,
    Subnormal 1e-45 -> ~0.4 (geschriebener Wert 1.4e-45)."""
    f32 = struct.unpack("<f", _float_bytes(value))[0]
    return f32, (abs(f32 - value) / abs(value) if value else 0.0)


def build_sys_param_read_payload(param_id):
    """0x06-Lese-Payload [02, id] (live: Live-Log `81 05 00 06 02 09`)."""
    _check_param_id(param_id)
    return [SUB_SYS_READ, param_id]


def build_sys_param_write_payload(param_id, value):
    """0x06-Schreib-Payload [01, id] + <f LE. REINER BUILDER, sendet nichts.

    Live belegt: `81 09 00 06 01 46 00 00 c8 42` (ID 70 = 100.0,
    Live-Log 12.10.2026) und `81 09 00 06 01 46 00 00 96 43`
    (ID 70 = 300.0, Live-Log 05.10.2026); Vollframe
    f7 81 09 00 06 01 46 00 00 96 43 8f. Antwort = Echo der 4 Float-Bytes.
    """
    _check_param_id(param_id)
    return [SUB_SYS_WRITE, param_id] + list(_float_bytes(value))


def build_sys_param_update_payload():
    """0x06-Update-Payload [03] ("Updata param"), Antwort [01]
    (Live-Log). REINER BUILDER, sendet nichts."""
    return [SUB_SYS_UPDATE]


def build_motor_version_payload():
    """0x0F-Payload [00] (live 16.10.: `81 04 00 0f 00`). Builder."""
    return [SUB_VERSION_GET]


def decode_motor_version(data):
    """12 B 0x0F-Antwort -> dict. KEIN Bus-Zugriff.

    fireware_ver "a.b.c.d" (4 Bytes dezimal), die übrigen "major.minor"
    (minor dezimal: 02 4b -> "2.75"); 'raw' = Rohbytes.
    Live 16.10.: 01 09 07 51 01 03 01 03 02 4b 01 00 -> fireware_ver
    1.9.7.81, cmd_system_ver 1.3, protection_ver 1.3, flash_param_ver 2.75,
    logger_ver 1.0.
    """
    raw = bytes(data)
    if len(raw) != VERSION_LEN:
        raise ValueError("0x0F: %d statt %d Bytes: %r" % (len(raw), VERSION_LEN, raw))
    out, pos = {}, 0
    for name, n in VERSION_FIELDS:
        out[name] = ".".join(str(b) for b in raw[pos:pos + n])
        pos += n
    out["raw"] = raw
    return out


def _check_variable_index(idx):
    if isinstance(idx, bool) or not isinstance(idx, int) \
            or not VARIABLE_INDEX_MIN <= idx <= VARIABLE_INDEX_MAX:
        raise ValueError(
            "0x08-Index muss int %d..%d sein (live belegt): %r – Index 0/13 "
            "lässt den Creality-Host (motor_control_wrapper.so, MOTOR_GET "
            "DATA=0/13) in Klipper-SHUTDOWN laufen; Verhalten des Motors "
            "außerhalb 1..12 unbelegt" % (VARIABLE_INDEX_MIN, VARIABLE_INDEX_MAX, idx))


def counts_to_rad(counts, counts_per_rev=POS_COUNTS_PER_REV):
    """idx-11-Counts -> rad (= pos_fdb-Skala). Live 16.10.: 690 -> 1.35481
    (pos_fdb live 1.35489). KEIN Bus-Zugriff."""
    return counts * 2.0 * math.pi / counts_per_rev


def pos_rad_to_counts(rad, counts_per_rev=POS_COUNTS_PER_REV):
    """pos_fdb [rad] -> Counts (float, nicht gerundet). Live 16.10.:
    1.35489 -> 690.04. Single-Turn (0..counts_per_rev). KEIN Bus-Zugriff."""
    return rad * counts_per_rev / (2.0 * math.pi)


def counts_to_mm(counts, rotation_distance=POS_ROTATION_DISTANCE,
                 counts_per_rev=POS_COUNTS_PER_REV):
    """Counts -> mm innerhalb EINER Umdrehung (bzw. Count-Differenz -> mm).
    Live 16.10.: 800 Counts = 10 mm (rotation_distance 40, 3200/rev).
    ACHTUNG Single-Turn: idx 11/pos_fdb wrappen alle rotation_distance mm;
    absolute Achsposition gibt es nur über den Klipper-Schrittzähler."""
    return counts * float(rotation_distance) / counts_per_rev


def counts_delta(new, old, counts_per_rev=POS_COUNTS_PER_REV):
    """Wrap-korrigierte Differenz new-old in (-rev/2, +rev/2]. Live 16.10.:
    3090 -> 690 = +800 (X160 -> X170). Nur eindeutig, wenn sich die Achse
    zwischen zwei Abfragen um < 1/2 Umdrehung (20 mm) bewegt hat; 40-mm-
    Vielfache sind unsichtbar (Aliasing). KEIN Bus-Zugriff."""
    d = (new - old) % counts_per_rev
    return d - counts_per_rev if d > counts_per_rev / 2 else d


def encoder_counts_to_rad(counts):
    """Encoder-Counts (idx 15/16, encoder_fdb in 0x13/0x14) -> rad,
    32768 Counts/U (Motor-FW-Analyse; live 0x14: 4421 -> 0,8477 rad =
    pos_fdb_encoder). NICHT für idx 11 (STEP-Pulse, 3200/U). Ohne Bus."""
    return counts_to_rad(counts, ENCODER_COUNTS_PER_REV)


def _check_phase_order_invert(phase_order_invert):
    if isinstance(phase_order_invert, bool) or not isinstance(phase_order_invert, int):
        raise ValueError("phase_order_invert muss int sein (Sys-Param ID 10, "
                         "live 0): %r" % (phase_order_invert,))
    return phase_order_invert


def ib_from_idx10(value, phase_order_invert=PHASE_ORDER_INVERT_LIVE):
    """0x08-idx-10-Wert -> Phasenstrom Ib [A], ohne Bus.

    FW (Motor-FW-Analyse, B): idx 10 = Ib x s mit s = +1,0 bei
    phase_order_invert == 0 und -1,0 bei != 0 (@0xbb30 `lh a5,0x5a(s0)`,
    `beqz` -> +1,0 @0x19c98, sonst -1,0 @0x19d14; obj+0x5a = int16 =
    Sys-Param ID 10). Da s*s = 1: Ib = idx10 x s. Live ist
    phase_order_invert = 0 -> Ib = idx 10 (unverändert).
    phase_order_invert: int (0 = live/Default); bool/sonstiges -> ValueError.
    """
    if _check_phase_order_invert(phase_order_invert) != 0:
        return -value
    return value


def build_variable_payload(idx):
    """0x08-Payload [idx], idx 1..12 (live 16.10.: `81 04 00 08 09`). Builder."""
    _check_variable_index(idx)
    return [idx]


def _decode_variable_any(idx, data, fmt):
    raw = bytes(data)
    if len(raw) != VARIABLE_LEN:
        raise ValueError("0x08 idx %d: %d statt 4 Bytes: %r" % (idx, len(raw), raw))
    f = struct.unpack("<f", raw)[0]
    i = struct.unpack("<I", raw)[0]
    typ = "float" if fmt == "<f" else "int"
    info = VARIABLE_TABLE.get(idx, (None, None, "offen", ""))
    return {"index": idx, "name": info[0], "unit": info[1], "status": info[2],
            "raw": raw, "fmt": fmt, "float": f, "int": i,
            "type": typ, "value": f if typ == "float" else i}


def decode_variable(idx, data):
    """4 B 0x08-Antwort -> {'index', 'name', 'unit', 'status', 'raw', 'fmt',
    'float', 'int', 'type', 'value'}. KEIN Bus-Zugriff. NUR idx 1..12.

    'float' = <f, 'int' = <I (UNSIGNED wie der Host, REA 16.10.),
    'fmt'/'type' = feste Host-Typkette (VARIABLE_HOST_FORMATS), 'value' =
    Wert in diesem Typ, 'name'/'unit'/'status' = VARIABLE_TABLE (FW-Deutung
    16.10., Belegstatus "belegt"/"fw"/"vermutet"/"offen").
    Live: 33 33 d3 3a -> 0.0016113 (idx 9 = Ia [A]), 52 04 00 00 -> 1106
    (idx 11 pos_counts).
    """
    _check_variable_index(idx)
    return _decode_variable_any(idx, data, VARIABLE_HOST_FORMATS[idx])


def decode_variable_fw(idx, data):
    """Wie decode_variable, aber für den FW-Bereich idx 0..18 (MOTOR-FW-
    FW-Analyse: Sprungtabelle @0x1bce4, 19 Einträge). KEIN Bus-Zugriff."""
    _check_variable_fw_index(idx)
    return _decode_variable_any(idx, data, VARIABLE_FW_FORMATS[idx])


def _check_variable_fw_index(idx):
    if isinstance(idx, bool) or not isinstance(idx, int) \
            or not VARIABLE_FW_INDEX_MIN <= idx <= VARIABLE_FW_INDEX_MAX:
        raise ValueError("0x08-FW-Index muss int %d..%d sein: %r (> 18 sendet die "
                         "FW nur einen veralteten Wert)"
                         % (VARIABLE_FW_INDEX_MIN, VARIABLE_FW_INDEX_MAX, idx))


def decode_motor_status(status):
    """STATUS-Byte (Byte 3) einer Motorantwort -> dict. KEIN Bus-Zugriff.

    MOTOR-FW (Antwort-Builder @0xdd6e, statisch BELEGT): bit0 = Stall-Flag
    (= 0x08 idx 14), bit1 = latched Fehlercode != 0, bit2 = latched Warncode
    != 0; 'other' = übrige Bits (unbelegt, erwartet 0). Live bisher immer 00.
    Gilt NUR für Motor-Boards (CFS 0x01 nutzt das Byte anders)."""
    if isinstance(status, bool) or not isinstance(status, int) \
            or not 0 <= status <= 0xFF:
        raise ValueError("STATUS muss int 0..255 sein: %r" % (status,))
    return {"raw": status,
            "stall": bool(status & MOTOR_STATUS_STALL),
            "err_latched": bool(status & MOTOR_STATUS_ERR_LATCHED),
            "warn_latched": bool(status & MOTOR_STATUS_WARN_LATCHED),
            "other": status & ~MOTOR_STATUS_KNOWN & 0xFF,
            "ok": status == 0}


def decode_protection(data):
    """8 B Antwort von 0x0C [0b] (bzw. [0a]) -> dict. KEIN Bus-Zugriff.

    Layout BELEGT (MOTOR-FW + Host-Parser motor_auto_check_protection
    @0x56788): Byte 0-3 = Fehlercode u32 LE, Byte 4-7 = Warncode u32 LE.
    Der Host baut den Bitstring per bin(b).zfill(8)[::-1] je Byte -> Index
    i = 8*Byte + Bit = LE-Bitnummer -> motor_error_dict[i] (key781+i) bzw.
    motor_warning_dict[i] (key795+i). Rückgabe: 'err_code', 'warning_code',
    'errors'/'warnings' (Listen (bit, key, text), s. decode_error_bits),
    'ok' (beide 0), 'raw'. Live nur 8x 00 (-> ok)."""
    raw = bytes(data)
    if len(raw) != PROTECTION_LEN:
        raise ValueError("0x0C: %d statt 8 Bytes: %r" % (len(raw), raw))
    err, warn = struct.unpack("<II", raw)
    return {"err_code": err, "warning_code": warn,
            "errors": decode_error_bits(err), "warnings": decode_warning_bits(warn),
            "ok": err == 0 and warn == 0, "raw": raw}


def decode_detail_union(err_code, union):
    """8-B-Union der Detaildaten (0x13/0x14) -> dict nach ERR_CODE_TO_UNION.

    err_code ist im Snapshot GENAU EIN Bit (FW: err_code = 1 << n). Format
    bekannt (Host) nur für 1/4/8; 32/64/128/256 nur Feldnamen -> Werte None.
    Unbekannt/0 -> {}. Rohbytes stehen immer unter 'union_raw' (Aufrufer)."""
    union = bytes(union)
    ent = ERR_CODE_TO_UNION.get(err_code)
    if not ent:
        return {}
    fmt, names = ent
    if fmt is None:
        return dict((n, None) for n in names)
    return dict(zip(names, struct.unpack(fmt, union[:struct.calcsize(fmt)])))


def decode_err_detail(data, cut=False):
    """74 B Antwort von 0x13 (cut=False) bzw. 0x14 (cut=True) -> dict.
    KEIN Bus-Zugriff. Format FW-statisch belegt, LIVE bestätigt 16.10.
    (alle Felder = Host-JSON der Webhooks motor_err/cut_detail_data).

    Felder ERR_DETAIL_FIELDS aus ERR_DETAIL_FORMAT (66 B; dev_uuid = 8 B
    als bytes), 'union_raw' = Byte 66..73, 'union' = decode_detail_union,
    bei cut=True zusätzlich CUT_DETAIL_EXTRA_FIELD = Host-Name
    param_peak_overcurrent_A ('<f' aus Byte 66..69, wie der Host mit
    CUT_DETAIL_FORMAT), 'errors' = decode_error_bits."""
    raw = bytes(data)
    if len(raw) != DETAIL_LEN:
        raise ValueError("0x%02x: %d statt %d Bytes: %r"
                         % (FUNC_CUT_DETAIL_DATA if cut else FUNC_ERR_DETAIL_DATA,
                            len(raw), DETAIL_LEN, raw))
    vals = struct.unpack(ERR_DETAIL_FORMAT, raw[:ERR_DETAIL_BASE_LEN])
    out = {"err_code": vals[0], "dev_uuid": bytes(vals[1:9])}
    for name, v in zip(ERR_DETAIL_FIELDS[2:], vals[9:]):
        out[name] = v
    out["union_raw"] = raw[ERR_DETAIL_BASE_LEN:]
    out["union"] = decode_detail_union(out["err_code"], out["union_raw"])
    out["errors"] = decode_error_bits(out["err_code"])
    if cut:
        out[CUT_DETAIL_EXTRA_FIELD] = struct.unpack(
            "<f", raw[ERR_DETAIL_BASE_LEN:ERR_DETAIL_BASE_LEN + 4])[0]
    out["raw"] = raw
    return out


def decode_samples(data, fields):
    """161 B Antwort von 0x15/0x16 -> Liste von 10 dicts (älteste zuerst).
    KEIN Bus-Zugriff. Byte 0 = Anzahl (FW fest 0x0A), dann je Probe '<ffff'
    mit den Feldnamen fields (POS_/SPD_SAMPLE_FIELDS; Reihenfolge = Host,
    LIVE bestätigt 16.10.). Länge/Zählbyte falsch -> ValueError."""
    raw = bytes(data)
    if len(raw) != SAMPLE_LEN or raw[0] != SAMPLE_COUNT:
        raise ValueError("Sample-Antwort: %d B / Zählbyte %r statt %d B / %d"
                         % (len(raw), raw[:1], SAMPLE_LEN, SAMPLE_COUNT))
    return [dict(zip(fields, struct.unpack_from(SAMPLE_FORMAT, raw, 1 + 16 * k)))
            for k in range(SAMPLE_COUNT)]


def decode_error_bits(code):
    """err_code (int-Bitmaske) -> Liste (bit, key, text). KEIN Bus-Zugriff.
    Bit-Zuordnung BELEGT (motor_error_dict); im 0x0C-Frame = u32 LE aus
    Byte 0-3 (decode_protection). Unbekannte Bits -> (bit, None, 'unbekannt')."""
    return _decode_bits(code, MOTOR_ERROR_BITS)


def decode_warning_bits(code):
    """warning_code (int-Bitmaske) -> Liste (bit, key, text), s.
    decode_error_bits (motor_warning_dict Bit 0..2)."""
    return _decode_bits(code, MOTOR_WARNING_BITS)


def _decode_bits(code, table):
    if isinstance(code, bool) or not isinstance(code, int) or code < 0:
        raise ValueError("Bitmaske muss int >= 0 sein: %r" % (code,))
    out, bit = [], 0
    while code >> bit:
        if code >> bit & 1:
            key, text = table.get(bit, (None, "unbekannt"))
            out.append((bit, key, text))
        bit += 1
    return out


def fw_checksum(data):
    """1er-Komplement-Summe über die FW-Daten: (~summe) & 0xFF, 1 Byte.

    Beleg: Protokollanalyse (mcu_util FUN_00010e54 laut FW-Analyse; im
    Live-mcu_util bei 0x10e84: Summe aller Datenbytes, dann bitweises
    Komplement, 1 Byte angehängt).
    HINWEIS: Für 485-Chunks (1.7 "send fw data") ist NICHT separat belegt,
    ob/wie dieses Byte mitgesendet wird – Builder hängen es NICHT automatisch an.
    """
    return (~(sum(bytes(data)) & 0xFF)) & 0xFF


# --- Update-Sequenz-Builder (Beleg: Protokollanalyse) ---
# Jede Funktion liefert die Payload-Liste für Func 0xF0; Versand via
# bus.transact(addr, FUNC_FW_UPDATE, payload, retries=UPDATE_RETRIES).

def build_version_payload():
    """Sub 0x00 (1B) = get version."""
    return [SUB_GET_VERSION]


def build_sector_payload():
    """Sub 0x03 (1B) = get sector size (Antwort legt die Chunk-Größe fest)."""
    return [SUB_GET_SECTOR]


def _check_erase_target(dev_type):
    """Erase nur für CFS-Gerätetyp 1/10; Motor (2) und alles andere ->
    ValueError (ohne Bus-Zugriff). bool wird abgelehnt (True == 1!)."""
    if isinstance(dev_type, bool) or dev_type not in ERASE_DEV_TYPES:
        raise ValueError(
            "Erase (F0 [06]) für Gerätetyp %r gesperrt – nur CFS-Typ 1/10. "
            "Motorziele: die Motor-App kennt 0xF0 nicht (Kommandotabelle "
            "FUN_60fc nur 0x00-0x16, Frame ignoriert), der "
            "Stock-Updater mcu_util_485 sendet [06] nur an Typ 1/10 "
            "(@0x15378-0x153d4 `cmp r5,#1; cmpne r5,#10`); Wirkung im "
            "Motor-Bootloader unbelegt" % (dev_type,))
    return dev_type


def build_erase_payload(dev_type=DEV_TYPE_MOTOR):
    """Sub 0x06 (1B) = Erase der CFS-Flash-DB-Page 0x0807F800 – NUR CFS-Typ
    1/10. 18.10.2026: Das ist KEIN Sektor-Erase vor dem App-Flash, sondern
    setzt die Box-DB (Arbeitsmodus/Parameter) auf Werkseinstellung
    (Stock-Updateweg). Der Sendeschutz
    (serial485.check_tx_frame) sperrt F0 [06] an JEDE Adresse – dieser
    Builder ist reine Dokumentation.

    17.10.2026 (Motor-FW-Analyse, B): Für Motorziele GESPERRT ->
    ValueError. Belege: (1) Die Motor-App registriert nur Cmd 0x00-0x16
    (FUN_60fc, 19 Einträge), 0xF0 fehlt -> jeder F0-Frame wird ignoriert
    (keine Antwort, keine Wirkung). (2) Der Stock-Updater mcu_util_485
    sendet F0 [06] (Timeout 500 ms, 3 Versuche) nur für Gerätetyp 1 oder
    10 (Erase-Zustand 5 @0x15378-0x153d4), Motoren sind Typ 2 -> Stock
    sendet Motoren nie 0x06. Ein Motor-Update ginge nur über den
    Bootloader (0x0B), dessen F0-[06]-Verhalten nicht im Image liegt.
    dev_type: Gerätetyp (Default DEV_TYPE_MOTOR = 2, dieses Modul ist der
    Motor-Nachbau); nur DEV_TYPE_CFS (1) / DEV_TYPE_CFS_PRO (10) erlaubt.
    """
    _check_erase_target(dev_type)
    return [SUB_ERASE_DB_PAGE]


def build_update_request_payload():
    """Sub 0x01 (1B) = request update (Bootloader-Ack 0x75). Vom
    Sendeschutz an jede Adresse gesperrt – nur Dokumentation."""
    return [SUB_REQUEST_UPDATE]


def build_start_app_payload():
    """Sub 0x02 (1B) = App starten (Bootloader -> App). Beleg: mcu_util_485
    Zustand 10 @0x15a64: mov r3,#2 -> strb [sp,#0x48], Länge 1, Func 0xF0,
    Timeout 500 ms; Stock sendet es bei jedem Boot an alle adressierten
    Geräte (Stock-Updateweg). Bis 17.10.2026 fälschlich
    „update_end“ genannt – das Transfer-Ende meldet der Bootloader selbst
    mit 0x20 nach dem letzten Block."""
    return [SUB_START_APP]


build_update_end_payload = build_start_app_payload   # Alt-Name


def build_fw_len_payload(fw_len):
    """4 Bytes FW-Länge, 32-bit Little-Endian (Beleg: 1.7). Vom Sendeschutz
    gesperrt (F0 nur [00]/[02]/[03]) – nur Dokumentation."""
    if isinstance(fw_len, bool) or not isinstance(fw_len, int):
        raise ValueError("FW-Länge muss int sein: %r" % (fw_len,))
    if not 0 <= fw_len <= 0xFFFFFFFF:
        raise ValueError("FW-Länge muss 0..0xffffffff sein: %r" % (fw_len,))
    return list(struct.pack("<I", fw_len))


def sector_chunk_size(sector_byte):
    """Chunk-Größe in Byte aus dem Sektor-Byte (Antwort auf F0 [03]) wie
    Stock mcu_util_485: signed Byte s; s < 0 -> (s * 252) & 0xFF
    (0xE0 -> 128), s >= 0 bzw. Ergebnis 0 -> ValueError (Stock: s == 0
    Fehler, s > 0 / 0x80 / 0xC0 still kein Datenblock – hier Abbruch mit
    Fehler, Bootloader-Analyse). sector_byte = Rohbyte 0..255
    (auch negativ -128..-1 als bereits signed Wert erlaubt)."""
    if isinstance(sector_byte, bool) or not isinstance(sector_byte, int):
        raise ValueError("Sektor-Byte muss int sein: %r" % (sector_byte,))
    if not -0x80 <= sector_byte <= 0xFF:
        raise ValueError("Sektor-Byte muss 0..255 (bzw. -128..-1) sein: %r"
                         % (sector_byte,))
    s = sector_byte - 0x100 if sector_byte >= 0x80 else sector_byte
    chunk = (s * SECTOR_CHUNK_FACTOR) & 0xFF if s < 0 else 0
    if chunk == 0:
        raise ValueError(
            "Sektor-Byte 0x%02x ergibt Chunk 0 – Stock sendet dann still "
            "keine Daten (s == 0: „invalid sector size!“); Abbruch"
            % (sector_byte & 0xFF))
    return chunk


def chunk_firmware(fw_bytes, sector_byte):
    """Teilt die FW in Blöcke der Stock-Chunk-Größe (sector_chunk_size;
    CFS 0xE0 -> 128 B). Letzter Block darf kürzer sein.

    Ungültiges Sektor-Byte (Chunk 0, bool/float, außerhalb 0..255) ->
    ValueError; leere FW -> ValueError (ein leerer 0xF0-Frame ist nicht
    belegt). 18.10.2026: Parameter ist das SIGNED Sektor-Byte, nicht mehr
    eine Bytezahl 1..127."""
    chunk = sector_chunk_size(sector_byte)
    fw = bytes(fw_bytes)
    if not fw:
        raise ValueError("leere Firmware")
    return [fw[i:i + chunk] for i in range(0, len(fw), chunk)]


def update_sequence(fw_bytes, sector_byte, erase=False, dev_type=DEV_TYPE_MOTOR):
    """Dokumentierte Stock-Update-Sequenz als (Schritt, Func, Payload)-Liste
    – NICHT sendefähig: serial485.check_tx_frame sperrt request, len,
    Datenblöcke und erase an jede Adresse (Stock-Updateweg).

    Reihenfolge wie mcu_util_485 (Stock-Updateweg): version -> sector ->
    [erase-db-page, NUR mit erase=True UND dev_type CFS 1/10; Stock: immer
    bei CFS] -> request -> len(LE) -> Daten-Blöcke (Chunk aus dem signed
    Sektor-Byte, CFS 0xE0 -> 128 B; der Bootloader antwortet je Block 0x75,
    nach dem letzten 0x20 = fertig) -> start-app (Sub 0x02, Beleg:
    mcu_util_485 @0x15a64, Timeout START_APP_TIMEOUT).

    erase=True für Motorziele (Default dev_type = DEV_TYPE_MOTOR) ->
    ValueError (Beleg s. build_erase_payload: Motor-App kennt 0xF0 nicht,
    Stock sendet [06] nur an CFS-Typ 1/10, mcu_util_485 @0x15378). Ohne
    erase ist dev_type ohne Wirkung. Sendet nichts.
    """
    if erase:
        _check_erase_target(dev_type)  # vor allem anderen, ohne Bus
    fw = bytes(fw_bytes)
    chunks = chunk_firmware(fw, sector_byte)  # prüft leer + Sektor-Byte
    seq = [
        ("get-version", FUNC_FW_UPDATE, build_version_payload()),
        ("get-sector-size", FUNC_FW_UPDATE, build_sector_payload()),
    ]
    if erase:
        seq.append(("erase-db-page (CFS-Typ %d)" % dev_type, FUNC_FW_UPDATE,
                    build_erase_payload(dev_type)))
    seq += [
        ("request-update", FUNC_FW_UPDATE, build_update_request_payload()),
        ("send-fw-len", FUNC_FW_UPDATE, build_fw_len_payload(len(fw))),
    ]
    for i, chunk in enumerate(chunks):
        seq.append(("send-fw-chunk-%d" % i, FUNC_FW_UPDATE, list(chunk)))
    seq.append(("start-app", FUNC_FW_UPDATE, build_start_app_payload()))
    return seq


class Motor:
    """Closed-loop-Motor X/Y/Z/Z1 an serial_485 (live belegte Operationen,
    geschütztes Schreiben, Update-Pfad, TODO-Stubs).

    Alle live belegten Kommandos senden mit retries=MOTOR_RETRIES (0 = genau
    1 Sendung, wie Stock) und dem je Func gemessenen Timeout.
    """

    def __init__(self, bus, addr):
        if addr not in MOTOR_ADDRS:
            raise ValueError("unbekannte Motor-Adresse: 0x%02x (nur 0x81-0x84 "
                             "über serial_485; Extruder nur transparent)" % addr)
        self.bus = bus
        self.addr = addr

    @property
    def axis(self):
        """Achsname X/Y/Z/Z1."""
        return MOTOR_AXES[self.addr]

    def _tx(self, fun, payload, timeout):
        return bytes(self.bus.transact(self.addr, fun, payload, timeout=timeout,
                                       retries=MOTOR_RETRIES))

    def _tx_status(self, fun, payload, timeout):
        """Wie _tx, liefert aber (STATUS-Byte oder None, daten). Nutzt
        bus.transact_full (serial485, Review M2), sonst transact (Status
        dann None = nicht verfügbar)."""
        full = getattr(self.bus, "transact_full", None)
        if full is None:
            return None, self._tx(fun, payload, timeout)
        _addr, st, _fun, data, _raw = full(self.addr, fun, payload,
                                           timeout=timeout, retries=MOTOR_RETRIES)
        return st, bytes(data)

    # --- 0x06 Sys-Param ---

    def read_sys_param_raw(self, param_id):
        """0x06 [02, id] senden, Rohantwort (bytes) zurückgeben. LESEND.

        Live (klippy.log): ID 9 -> 4 B, ID 10 -> 2 B `00 00`
        (2520/2526), ID 226 -> 0 B (2859/2865, Motor-FW kennt ID nicht).
        retries=0, Timeout 1.0 s.
        """
        return self._tx(FUNC_SYS_PARAM, build_sys_param_read_payload(param_id),
                        TIMEOUT_SYS_PARAM)

    def read_sys_param(self, param_id):
        """Sys-Param lesen (0x06 [02, id]) -> float. LESEND.

        Rückgabe:
        - 4-Byte-Antwort -> float (`<f` LE), z.B. ID 9 an X = 123.58
          (Live-Log `81 05 00 06 02 09` ->
          `f7 81 07 00 06 20 29 f7 42 59`)
        - 0-Byte-Antwort -> None = Motor-FW kennt die ID nicht (ID 226,
          Live-Log `f7 81 03 00 06 af`)
        - andere Länge -> ValueError. Betrifft live ID 10
          (param_phase_order_invert, 2 B `00 00`, Live-Log); deren
          Format (u16/i16/bool) ist UNBELEGT -> read_sys_param_raw nutzen.
        Timeout -> TimeoutError (serial485).
        """
        raw = self.read_sys_param_raw(param_id)
        if len(raw) == 0:
            return None
        if len(raw) != 4:
            raise ValueError("0x06 ID %d: %d-Byte-Antwort %r ist kein float "
                             "(Format unbelegt, read_sys_param_raw nutzen)"
                             % (param_id, len(raw), raw))
        return decode_sys_param_float(raw)

    def get_sys_param(self, param_id):
        """Alias für read_sys_param (Wrapper-Name MOTOR_SYS_PARAM lesen)."""
        return self.read_sys_param(param_id)

    def write_sys_param(self, param_id, value, confirm=False, allow_locked=False):
        """Sys-Param SCHREIBEN (0x06 [01, id] + <f) – GESCHÜTZT.

        ACHTUNG: verändert Motorregelparameter (z.B. ID 70 Positionsregler-Kp,
        188 Schleppfehler-Zeit) im laufenden Motor. Live FLÜCHTIG: nach
        Kaltstart wieder Werkswerte (Kp 300, err_time 0.01 …), nach Soft-
        Restart noch der geschriebene Wert (Protokollanalyse).
        Ohne confirm=True -> PermissionError, es wird NICHTS gesendet.

        Zweite Sperre: IDs AUSSERHALB SYS_PARAM_CONFIGURABLE (166 IDs, im
        Plus-Log "is not configurable", z.B. ID 1 param_boot_key, 224/225
        param_flash_key/param_flash_checksum, Kalibrier-/Phasenwerte) nur mit
        ZUSÄTZLICHEM allow_locked=True – sonst PermissionError mit ID-Namen.
        Wert-Plausibilität (ValueError, vor allen Sperren): endlich,
        |Wert| <= float32-Max, Eingabe != 0 darf nicht auf 0.0 runden (1e-50).
        Rundung vorab prüfbar: float32_rounding(value).

        Live-Log 12.10.2026: `81 09 00 06 01 46 00 00 c8 42` ->
        Echo `f7 81 07 00 06 00 00 c8 42 d4` ("写入成功", 100.0).
        retries=0, Timeout 1.0 s. Echo != gesendete Bytes -> RuntimeError.
        Rückgabe: geechoter Wert (float).
        """
        payload = build_sys_param_write_payload(param_id, value)  # prüft vorher
        if confirm is not True:
            raise PermissionError(
                "write_sys_param(ID %d=%r) gesperrt: verändert Motorregel"
                "parameter (flüchtig bis Kaltstart) – nur mit confirm=True"
                % (param_id, value))
        if param_id not in SYS_PARAM_CONFIGURABLE and allow_locked is not True:
            raise PermissionError(
                "write_sys_param(ID %d %s) gesperrt: laut Stock 'not configurable' "
                "(Schlüssel/Checksumme/Kalibrierung) – nur mit confirm=True UND "
                "allow_locked=True" % (param_id, SYS_PARAM_NAMES[param_id]))
        echo = self._tx(FUNC_SYS_PARAM, payload, TIMEOUT_SYS_PARAM)
        if echo != bytes(payload[2:]):
            raise RuntimeError("0x06 Schreib-Echo passt nicht: gesendet %s, "
                               "Antwort %s" % (bytes(payload[2:]).hex(), echo.hex()))
        return decode_sys_param_float(echo)

    def set_sys_param(self, param_id, value, confirm=False, allow_locked=False):
        """Alias für write_sys_param (gleicher Schutz, confirm=True nötig)."""
        return self.write_sys_param(param_id, value, confirm=confirm,
                                    allow_locked=allow_locked)

    def update_sys_params(self, confirm=False):
        """0x06 [03] "Updata param" – GESCHÜTZT (confirm=True nötig).

        Stock sendet es einmal pro Motor am Ende der flash_param-Enumeration
        (Live-Log `81 04 00 06 03` -> `f7 81 04 00 06 01 21`).
        Wirkung unbelegt (macht die Werte jedenfalls NICHT dauerhaft – live
        flüchtig). Antwort != [01] -> RuntimeError.
        """
        if confirm is not True:
            raise PermissionError("update_sys_params gesperrt: nur mit confirm=True")
        ans = self._tx(FUNC_SYS_PARAM, build_sys_param_update_payload(),
                       TIMEOUT_SYS_PARAM)
        if ans != bytes([SYS_UPDATE_OK]):
            raise RuntimeError("0x06 [03]: Antwort %s statt 01" % ans.hex())
        return True

    # --- 0x0C Schutzstatus ---

    def check_protection_raw(self):
        """Schutzstatus abfragen (0x0C [0b]) -> 8 Byte (bytes). LESEND.

        = Stock MOTOR_CHECK_PROTECTION_AFTER_HOME DATA=11 (Live-Log).
        Live-Log: `81 04 00 0c 0b` ->
        `f7 81 0b 00 0c 00×8 fb` (alle Motoren immer 8×00 = kein Fehler).
        Layout: decode_protection (Byte 0-3 Fehler, 4-7 Warnung, u32 LE).
        SEITENEFFEKT (MOTOR-FW @0x10616, statisch): steht stall_mode/
        emergency_pin_mode auf 2, gibt die FW nach der Antwort den Notfall-
        Pin frei (Codes bleiben) – wie beim Stock, der [0b] genauso sendet.
        retries=0, Timeout 0.05 s. Die Subs 01..05/07/09 werden NIE gesendet
        (Konstanten SUB_PROT_*; [05] live nie beantwortet, Live-Log).
        """
        ans = self._tx(FUNC_PROTECTION, [SUB_PROT_CHECK], TIMEOUT_PROTECTION)
        if len(ans) != PROTECTION_LEN:
            raise ValueError("0x0C: %d statt 8 Bytes: %r" % (len(ans), ans))
        return ans

    def check_protection(self):
        """Schutzstatus (0x0C [0b]) -> dekodiert (decode_protection):
        {'err_code', 'warning_code', 'errors', 'warnings', 'ok', 'raw'}.
        LESEND (Seiteneffekt Notfall-Pin bei stall_mode 2, s.
        check_protection_raw). Live 8×00 -> ok=True, keine Bits."""
        return decode_protection(self.check_protection_raw())

    check_protection_code = check_protection  # Host-Name (gleicher Parser @0xd6cbc)

    def write_params_raw(self, data8=None):
        """VERALTET – frühere Deutung "8 Byte Parameter senden" WIDERLEGT.

        Live ist 0x0C ein 1-Byte-Request [0b] mit 8-Byte-Antwort (die 8×00
        waren die ANTWORT). Ohne Argument = Alias auf check_protection_raw()
        (8 B); mit Argument -> ValueError (es werden nie 8 Byte gesendet).
        """
        if data8 is not None:
            raise ValueError("write_params_raw: 8-Byte-0x0C-Senden widerlegt "
                             "(0x0C = Schutzabfrage [0b]); check_protection() nutzen")
        return self.check_protection_raw()

    # --- 0x11 Stall-Mode ---

    def set_mode(self, mode, *extra):
        """Stall-Mode setzen (0x11 [mode], mode ∈ 01/02/03). SENDET.

        Live: Homing MOTOR_STALL_MODE DATA=1 -> [01] an X/Y, DATA=2 -> [02]
        an alle (Live-Log); Live-Log 12.10.2026:
        `81 04 00 11 01` -> `f7 81 04 00 11 01 1d`; [03]:
        Live-Log 12.10.2026 -> `f7 81 04 00 11 03 13`.
        Antwort = Echo (Abweichung -> RuntimeError). Bedeutung der Modi
        UNBELEGT. Das frühere "[02 00]" ist die ANTWORT auf [04] (s.
        get_stall_mode) – 2-Byte-Requests werden abgelehnt.
        retries=0, Timeout 1.0 s (für 0x11 unbelegt).
        """
        if extra:
            raise ValueError("0x11 nimmt genau 1 Byte (live); [02 00] war die "
                             "Antwort auf [04]: %r" % ((mode,) + extra,))
        if isinstance(mode, bool) or not isinstance(mode, int) or mode not in STALL_MODES:
            raise ValueError("Stall-Mode muss 1, 2 oder 3 sein (live belegt): %r"
                             % (mode,))
        echo = self._tx(FUNC_SET_MODE, [mode], TIMEOUT_STALL)
        if echo != bytes([mode]):
            raise RuntimeError("0x11 Echo passt nicht: %s statt %02x"
                               % (echo.hex(), mode))
        return mode

    def get_stall_mode(self):
        """Stall-Mode lesen (0x11 [04]) -> int. LESEND.

        Live-Log 12.10.2026: `81 04 00 11 04` ->
        `f7 81 05 00 11 02 00 0e` ("get_stall_mode … b'\\x02'"), Antwort
        [mode, 00]. retries=0, Timeout 1.0 s (unbelegt).
        """
        ans = self._tx(FUNC_STALL_MODE, [SUB_STALL_GET], TIMEOUT_STALL)
        if len(ans) != 2:
            raise ValueError("0x11 [04]: 2 Bytes erwartet: %r" % (ans,))
        return ans[0]

    # --- 0x0E / 0x12 ---

    def read_485_addr(self):
        """Eigene RS485-Adresse lesen (0x0E [02]) -> int. LESEND.

        Live-Log: `83 04 00 0e 02` -> `f7 83 04 00 0e 83 0e`,
        Live-Log (0x81). retries=0, Timeout 0.05 s. Der
        Broadcast `FF 0E 01` bleibt unbeantwortet (Live-Log) und wird
        hier nicht gebaut.
        """
        ans = self._tx(FUNC_ADDR, [SUB_ADDR_READ], TIMEOUT_ADDR)
        if len(ans) != 1:
            raise ValueError("0x0E: 1 Byte erwartet: %r" % (ans,))
        return ans[0]

    def read_uuid(self):
        """Geräte-UUID lesen (0x12 [02]) -> 8 Byte (bytes). LESEND.

        Live-Log: `83 04 00 12 02` -> Antwort len 0b, 8 B
        mit Präfix cd ab (alle 4 Motoren). retries=0, Timeout 1.0 s.
        Präfix wird nicht erzwungen (nur Länge).
        """
        ans = self._tx(FUNC_UUID, [SUB_UUID_READ], TIMEOUT_UUID)
        if len(ans) != UUID_LEN:
            raise ValueError("0x12: 8 Bytes erwartet: %r" % (ans,))
        return ans

    dev_uuid = read_uuid  # grant0013-Name

    # --- Gesperrt ---

    # --- 0x0F Version / 0x08 Variablen (live 16.10.2026) ---

    def query_motor_version(self):
        """Motor-Version im Normalbetrieb (0x0F [00]) -> dict. LESEND.

        = Stock MOTOR_VERSION NUM=n DATA=0. Live 16.10. (Motor X):
        `81 04 00 0f 00` -> `f7 81 0f 00 0f 01 09 07 51 01 03 01 03 02 4b 01
        00 02` = fireware_ver 1.9.7.81, cmd_system_ver 1.3, protection_ver
        1.3, flash_param_ver 2.75, logger_ver 1.0 (s. decode_motor_version).
        retries=0, Timeout 1.0 s (UNBELEGT). Nicht verwechseln mit
        query_version() (Update-Pfad F0/00, live nie beobachtet).
        """
        return decode_motor_version(self._tx(FUNC_VERSION,
                                             build_motor_version_payload(),
                                             TIMEOUT_VERSION))

    motor_version = query_motor_version         # Wrapper-Name
    motor_version_normal = query_motor_version  # früherer Stub-Name

    def read_variable(self, idx):
        """Variable lesen (0x08 [idx]) -> dict (s. decode_variable). LESEND.

        = Stock MOTOR_GET NUM=n DATA=idx. NUR idx 1..12 (live 16.10. Scan an
        Motor X, Standby: 1-8 = 0, 9 = 0.0016113, 10 = 0.0008057, 11 = 1106,
        12 = 0); sonst ValueError OHNE Senden.
        Begründung der Grenze: Der Creality-G-Code erlaubt nur DATA 1..12
        (get_int maxval=12); DATA=0/13 führte live zum Host-Crash (Traceback
        16.10. 13:12:27: motor_get:1982 -> parse_num_to_byte(None) ->
        TypeError, kein Frame gesendet). Die Host-Typkette kennt zwar 13..18
        (VARIABLE_HOST_FORMATS), am Motor ist das aber live NICHT geprüft –
        bewusst gesperrt. Typ = feste Host-Kette (1..10 '<f', 11/12 '<I').
        Namen: 5..8 = pos_fdb/spd_fdb/id_fdb/iq_fdb (Host), 11 = pos_counts
        (abgeleitet, VARIABLE_NAMES).
        retries=0, Timeout 1.0 s (UNBELEGT).
        """
        payload = build_variable_payload(idx)
        return decode_variable(idx, self._tx(FUNC_VARIABLE, payload,
                                             TIMEOUT_VARIABLE))

    motor_get = read_variable  # Host-Name

    def read_variable_raw_fw(self, idx, confirm=False):
        """0x08 [idx] für den FW-Bereich idx 0..18 – GESCHÜTZT (confirm=True).

        Entscheidung 16.10.2026: Die Grenze 1..12 von read_variable stammt
        aus dem Creality-HOST (G-Code maxval=12, Crash bei DATA=0/13 VOR dem
        Senden). Ein eigener Bus-Zugriff umgeht den Host; die Motor-FW
        behandelt idx 0..18 in der GET-Sprungtabelle (@0x1bce4) rein lesend
        (Motor-FW-Analyse) – deshalb sauber baubar. Trotzdem gesperrt, weil
        0 und 13..18 am Motor LIVE NIE gesendet wurden: ohne confirm=True
        PermissionError, nichts gesendet. idx > 18 -> ValueError (FW sendet
        dann nur einen veralteten Scratch-Wert). Rückgabe decode_variable_fw.
        NICHT über Creality-G-Code nachbilden (MOTOR_GET DATA=0/13 ->
        Klipper-SHUTDOWN). retries=0, Timeout 1.0 s (unbelegt).
        """
        _check_variable_fw_index(idx)
        if confirm is not True:
            raise PermissionError(
                "read_variable_raw_fw(idx %d) gesperrt: FW-statisch lesend, live "
                "für 0/13..18 nie geprüft – nur mit confirm=True" % idx)
        return decode_variable_fw(idx, self._tx(FUNC_VARIABLE, [idx],
                                                TIMEOUT_VARIABLE))

    def read_reference_values(self):
        """Sollwerte der FOC-Kaskade (0x08 idx 1..4, 4 Frames) -> dict
        {'pos_ref' [rad], 'spd_ref' [rad/s], 'id_ref' [A], 'iq_ref' [A]}.
        LESEND. FW-Deutung belegt (Motor-FW-Analyse), live 16.10. passend
        (F600: spd_ref 1,633 @ 1,571 Soll, id_ref 0, iq_ref ~ iq_fdb)."""
        return dict((VARIABLE_NAMES[i], self.read_variable(i)["value"])
                    for i in (IDX_POS_REF, IDX_SPD_REF, IDX_ID_REF, IDX_IQ_REF))

    def read_phase_currents(self, phase_order_invert=PHASE_ORDER_INVERT_LIVE):
        """Phasenströme (0x08 idx 9, 10; 2 Frames) in A. LESEND.

        KORREKTUR 17.10.2026 (Motor-FW-Analyse, B): idx 10 = Ib x (-1 NUR
        falls phase_order_invert != 0), s. ib_from_idx10. Bei
        phase_order_invert = 0 (live, Default) ist 'ib' = idx 10.

        phase_order_invert: Wert von Sys-Param ID 10 (param_phase_order_
        invert, obj+0x5a), int, Default 0 = Live-Stand aller Motoren. Kein
        Bus-Zugriff dafür (bei Bedarf vorher read_sys_param_raw(10) lesen;
        live 2 B `00 00`). bool/sonstiges -> ValueError OHNE Senden.

        Rückgabe {'ia', 'ib', 'ib_fw' (idx-10-Rohwert), 'minus_ib' (= -ib,
        Alt-Schlüssel; bis 17.10. fälschlich der Rohwert idx 10),
        'phase_order_invert'}. Skala raw*Vref/4096/(Gain*R); Gain*R = 1 Ohm
        VERMUTET (Motor-FW-Analyse: V, stark)."""
        _check_phase_order_invert(phase_order_invert)
        ia = self.read_variable(IDX_IA)["value"]
        ib_fw = self.read_variable(IDX_IB)["value"]
        ib = ib_from_idx10(ib_fw, phase_order_invert)
        return {"ia": ia, "ib": ib, "ib_fw": ib_fw, "minus_ib": -ib,
                "phase_order_invert": phase_order_invert}

    def read_dir_input(self):
        """Pegel des DIR-Eingangs (0x08 idx 12) -> int 0/1. LESEND.
        FW: GPIO pins+0x110, bei Flanke Zählrichtung umgeschaltet; live
        16.10. bestätigt (Fahrt -X = 0, +X = 1, Stillstand hält die letzte
        Richtung, nach Einschalten 0)."""
        return self.read_variable(IDX_DIR_INPUT)["value"]

    def read_position_feedback(self, with_counts=False):
        """pos_fdb (0x08 idx 5) -> float [rad]. LESEND.
        Name statisch belegt (NO_ERR_CODE_TO_GET); Skalierung live 16.10. BELEGT:
        pos_fdb = idx11 * 2*pi/3200, SINGLE-TURN (Wrap alle 40 mm bei
        rotation_distance 40; stromlos vor G28: idx 5 = 0.0, idx 11 = 1106).
        with_counts=True -> 2 Frames (idx 5 + idx 11), Rückgabe dict
        {'pos_fdb' [rad], 'counts' (idx 11, 0..3199), 'counts_from_rad',
        'mm_in_rev' (counts_to_mm, nur innerhalb der Umdrehung)}.
        Absolute Achsposition NUR über den Klipper-Schrittzähler."""
        rad = self.read_variable(IDX_POS_FDB)["value"]
        if not with_counts:
            return rad
        counts = self.read_position_counts()
        return {"pos_fdb": rad, "counts": counts,
                "counts_from_rad": pos_rad_to_counts(rad),
                "mm_in_rev": counts_to_mm(counts)}

    def read_position_counts(self):
        """0x08 idx 11 (eigener Name pos_counts, Host-Name unbekannt) -> int
        0..3199 Counts/Umdrehung. LESEND. Live 16.10. belegt (80 Counts/mm,
        Wrap alle 40 mm). Umrechnung: counts_to_mm / counts_delta."""
        return self.read_variable(IDX_POS_COUNTS)["value"]

    def read_speed_feedback(self):
        """spd_fdb (0x08 idx 6) -> float [rad/s]. LESEND. Einheit BELEGT
        16.10. (FW: LESO-Drehzahl spd_fdb_ctrl, Parameter *_rad_s; live
        1,53-1,57 rad/s bei 10 mm/s = 1,571 rad/s Soll)."""
        return self.read_variable(IDX_SPD_FDB)["value"]

    def read_current_feedback(self):
        """id_fdb/iq_fdb (0x08 idx 7, 8) -> {'id', 'iq', 'abs'}. LESEND,
        2 Frames. 'abs' = hypot(id, iq) wie der Host-Stats-Hook
        (Motor_Control.stats, overcurrent=|I|). Einheit A (FW: aus den
        Phasenströmen idx 9/10; Gain*R = 1 Ohm vermutet)."""
        i_d = self.read_variable(IDX_ID_FDB)["value"]
        i_q = self.read_variable(IDX_IQ_FDB)["value"]
        return {"id": i_d, "iq": i_q, "abs": math.hypot(i_d, i_q)}

    def read_encoder(self, *args, **kwargs):
        """GESPERRT – sendet KEINEN Frame.

        Alt-Falle: früher als 0x06 gedeutet; 0x06 ist live Sys-Param
        (Lesen/Schreiben von Regelparametern), kein Encoder. "Encoder" =
        0x08 idx 5 (pos_fdb, rad) / idx 11 (Counts 0..3199), beide SINGLE-
        TURN (live 16.10. belegt) -> read_position_feedback(with_counts=True)
        bzw. read_position_counts() nutzen.
        """
        raise NotImplementedError(
            "read_encoder gesperrt (0x06 = Sys-Param): Positionsrückmeldung = "
            "read_position_feedback(with_counts=True) (0x08 idx 5 pos_fdb rad / "
            "idx 11 Counts, single-turn 3200/Umdrehung)")

    # --- 0x13..0x16 Detail-/Sample-Daten (MOTOR-FW 16.10.: LESEND;
    # LIVE bestätigt 16.10. über die Klipper-Webhooks motor_err_detail_data /
    # motor_cut_detail_data) ---

    def _read_detail(self, fun, length):
        # Payload [02] wie der Host (live `81 04 00 1x 02`); die FW ignoriert
        # sie (kein Lesezugriff auf 0x6cc) – byte-gleich zum Stock-Verkehr.
        st, ans = self._tx_status(fun, list(DETAIL_REQUEST_PAYLOAD), TIMEOUT_DETAIL)
        if len(ans) != length:
            raise ValueError("0x%02x: %d statt %d Bytes: %r"
                             % (fun, len(ans), length, ans))
        return st, ans

    def read_err_detail(self):
        """0x13 err_detail_data -> dict (decode_err_detail) + 'status'.
        LIVE bestätigt 16.10. (Motor X, STATUS 00, Werte = Host-JSON).
        Request `81 04 00 13 02` wie der Host (FW ignoriert die [02]),
        Antwort 74 B = Snapshot des LETZTEN Fehlerereignisses (bleibt bis zum
        nächsten, auch über protection [05]); der Handler kopiert nur
        statische Felder nach (idempotent; live ohne Fehler seit Boot: nur
        dev_uuid/elec_offset/phase_order_invert/elec_offset_err_deg gefüllt).
        'status' = decode_motor_status oder None. retries=0, Timeout 1 s
        (Host-Timeout unbelegt)."""
        st, ans = self._read_detail(FUNC_ERR_DETAIL_DATA, DETAIL_LEN)
        out = decode_err_detail(ans)
        out["status"] = None if st is None else decode_motor_status(st)
        return out

    def read_stall_detail(self):
        """0x14 cut_detail_data -> dict (decode_err_detail(cut=True)) +
        'status'. LIVE bestätigt 16.10. (Motor X, STATUS 00, Werte = Host-
        JSON inkl. param_peak_overcurrent_A). Request `81 04 00 14 02` wie
        der Host, Antwort 74 B = Snapshot beim letzten STALL-Ereignis
        (Messpunkt von MOTOR_CHECK_CUT_POS; live z. B. encoder_fdb 4421,
        24,365 V, 50,32 °C). retries=0, Timeout 1 s (Host-Timeout unbelegt)."""
        st, ans = self._read_detail(FUNC_CUT_DETAIL_DATA, DETAIL_LEN)
        out = decode_err_detail(ans, cut=True)
        out["status"] = None if st is None else decode_motor_status(st)
        return out

    def read_pos_samples(self):
        """0x15 pos_data -> {'samples': 10 x {fdb_pos, leso_pos, target_pos,
        leso_err}, 'status'}. LIVE bestätigt 16.10. (Motor X Stillstand:
        10 x fdb/leso_pos 2,15985, target_pos 2,159847, leso_err 0 = Host-
        JSON). Request `81 04 00 15 02` wie der Host, Antwort 161 B
        (Zählbyte 0x0A + 10 x '<ffff', älteste zuerst); Ring wird nur bei
        ctrl+4 == 1 geschrieben, kein Start/Löschen. retries=0, Timeout 1 s."""
        st, ans = self._read_detail(FUNC_POS_DATA_SAMPLE, SAMPLE_LEN)
        return {"samples": decode_samples(ans, POS_SAMPLE_FIELDS),
                "status": None if st is None else decode_motor_status(st)}

    def read_spd_samples(self):
        """0x16 spd_data -> {'samples': 10 x {fdb_spd, leso_spd, target_spd,
        leso_in_u}, 'status'}. LIVE bestätigt 16.10. (Motor X Stillstand:
        10 x ~0 = Host-JSON). Request `81 04 00 16 02`, sonst wie
        read_pos_samples (eigener Ring). retries=0, Timeout 1 s."""
        st, ans = self._read_detail(FUNC_SPD_DATA_SAMPLE, SAMPLE_LEN)
        return {"samples": decode_samples(ans, SPD_SAMPLE_FIELDS),
                "status": None if st is None else decode_motor_status(st)}

    # Host-Namen (Motor_cmd) -> lesende Methoden
    err_detail_data = read_err_detail
    cut_detail_data = read_stall_detail
    pos_data_sample = read_pos_samples
    spd_data_sample = read_spd_samples

    # --- Stubs aus der Host-Tabelle Motor_cmd (REA 16.10., live nie
    # gesehen) – senden NICHTS ---

    def reboot(self, *args):
        """TODO 0x01 reboot (Host-Tabelle) – live nie gesehen, nicht senden.
        FW (Motor-FW-Analyse, B): FUN_f724, nur Payload [00] wirkt (Watchdog-
        Reset ohne Boot-Key); per 0xFF erreichbar -> Broadcast sperrt
        serial485.check_tx_frame."""
        raise NotImplementedError("TODO 0x01 reboot: nicht nachgebaut")

    def boot(self, *args):
        """GESPERRT 0x0B boot (MOTOR_BOOT „使系统进入boot模式“) – Bootloader,
        Haltemoment weg. Nie senden. FW (Motor-FW-Analyse, B): FUN_104e6, nur
        Payload [00] wirkt (boot_key 0x8567 + Watchdog-Reset), [01] ist in
        der Motor-App wirkungslos (@0x104f6-0x104fc). Broadcast 0xFF 0x0B
        (außer [01] = CFS-Loader->App) sperrt serial485.check_tx_frame."""
        raise NotImplementedError("0x0B boot gesperrt (Bootloader)")

    def systemid(self, *args):
        """GESPERRT/wirkungslos: 0x0D systemid (nur Host-Tabelle).

        Motor-FW-Analyse (B):
        - Host: nur Dict-Eintrag Motor_cmd['systemid'] = 0x0D (einzige
          Ladestelle im Modul-Init), kein G-Code, keine Funktion, kein
          Webhook -> Stock sendet 0x0D nie.
        - FW mot2_002_081: 0x0D ist NICHT registriert (Tabelle 0x00-0x08,
          0x0B, 0x0C, 0x0E, 0x0F, 0x11-0x16) -> ignoriert, keine Antwort.
        - Die eigentliche Systemidentifikation (R/L-Messung,
          system_id_RL_param_*) ist der Motorcheck beim Hochlauf (FUN_e51c,
          Fehlerbits 9-13), nicht per Bus auslösbar; motor_R/L landen im
          Normalbetrieb nicht im Flash (V).
        Bleibt Stub: kein Nutzen, keine Frame-Erzeugung."""
        raise NotImplementedError("TODO 0x0D systemid: unbelegt")

    # --- Update-Pfad (Func 0xF0, live NIE beobachtet) ---
    # FREI (lesend): query_version (Sub 00), query_sector_size (Sub 03) – im
    # Binary reine "get"-Abfragen VOR request-update (1.7, 3 Retries), sie
    # ändern laut Ablauf keinen Zustand. Restrisiko: wie die Motor-App-FW
    # auf F0 reagiert, ist live unbelegt.
    # GESCHÜTZT (confirm=True, sonst PermissionError ohne Bus-Zugriff):
    # start_app (Sub 02, Alias update_end). HART GESPERRT (18.10.2026, auch
    # mit confirm=True): request_update, send_fw_len – der Sendeschutz sperrt
    # F0 [01]/Länge/Daten/[06] ohnehin an jede Adresse. Es gibt bewusst KEINE Methode
    # für Daten-Chunks/Erase (nur Builder + update_sequence, senden nichts).
    # Erase ist für Motoren zusätzlich in den Buildern gesperrt (17.10.2026,
    # ValueError). Die laufende Motor-App ignoriert 0xF0 komplett (keine
    # Antwort) – query_* laufen an einem App-Motor also in den Timeout.

    @staticmethod
    def _check_update_confirm(name, confirm):
        if confirm is not True:
            raise PermissionError(
                "%s gesperrt: F0-Update-Pfad (live nie beobachtet) kann den Motor "
                "in den Bootloader schicken – Haltemoment weg, Z kann absacken; "
                "nur mit confirm=True" % name)

    def query_version(self):
        """Version über Update-Pfad abfragen (Func 0xF0/Sub 0x00). Rohdaten.
        FREI: reine Abfrage (1.7 "get version", erster Schritt vor
        request-update); live nie beobachtet."""
        return self.bus.transact(self.addr, FUNC_FW_UPDATE,
                                 build_version_payload(),
                                 retries=UPDATE_RETRIES)

    def query_sector_size(self):
        """Sector-Größe abfragen (Func 0xF0/Sub 0x03). Rohdaten.
        FREI: reine Abfrage (1.7 "get sector size", vor request-update)."""
        return self.bus.transact(self.addr, FUNC_FW_UPDATE,
                                 build_sector_payload(),
                                 retries=UPDATE_RETRIES)

    @staticmethod
    def _update_hard_locked(name):
        raise PermissionError(
            "%s HART gesperrt (18.10.2026, kein Override): F0-Update-Request/"
            "Länge/Datenblöcke überschreiben die Geräte-App; der Sendeschutz "
            "serial485.check_tx_frame erlaubt F0 nur [00]/[02]/[03]; "
            "nichts gesendet" % name)

    def request_update(self, confirm=False):
        """Update anmelden (Func 0xF0/Sub 0x01) – HART GESPERRT (18.10.2026):
        PermissionError auch mit confirm=True, nichts gesendet.

        RISIKO: Schaltet den Slave vermutlich in den Bootloader/Update-Modus
        (danach im Binary nur noch len/Daten/update_end, "wait to startup
        app"). Ein Achsmotor verliert dabei die Regelung/das Haltemoment –
        bei Z (0x83/0x84) kann das Bett/Portal mechanisch absacken. Live nie
        beobachtet."""
        self._update_hard_locked("request_update")

    def send_fw_len(self, fw_len, confirm=False):
        """FW-Länge senden (4B LE, Func 0xF0) – HART GESPERRT (18.10.2026,
        auch mit confirm=True). Länge wird vor der Sperre geprüft
        (ValueError)."""
        build_fw_len_payload(fw_len)
        self._update_hard_locked("send_fw_len")

    def start_app(self, confirm=False):
        """App starten (Func 0xF0/Sub 0x02, Timeout 500 ms, Beleg:
        mcu_util_485 Zustand 10 @0x15a64) – GESCHÜTZT (confirm=True).
        Wirkt nur im Bootloader (Motor-App ignoriert 0xF0); Stock sendet es
        bei jedem Boot. RISIKO: startet die vorhandene App – war ein Flash
        unvollständig, verweigert der CFS-Bootloader (CRC); Motor-Bootloader
        unbelegt. Vom Sendeschutz erlaubt (F0 [02])."""
        self._check_update_confirm("start_app", confirm)
        self.bus.transact(self.addr, FUNC_FW_UPDATE,
                          build_start_app_payload(),
                          timeout=START_APP_TIMEOUT,
                          retries=UPDATE_RETRIES)

    def update_end(self, confirm=False):
        """Alt-Name für start_app (Sub 0x02 = App starten, nicht
        „Update-Ende“; 18.10.2026)."""
        self.start_app(confirm=confirm)

    # --- TODO-Stubs: live nie beobachtete Motor_Control-Methoden ---
    # Quelle der Namen: motor_control_wrapper-Strings (Protokollanalyse).
    # KEINER sendet Bus-Frames.

    def encoder_calibrate(self):
        """GESPERRT: 0x03 encoder_calibrate_official – dreht den Motor!

        Motor-FW-Analyse (B = Code):
        - Host: G-Code MOTOR_ENCODER_CALIBRATE_OFFICIAL NUM=1..10
          [EXTRUDER=1 -> transparent], motor_send_data(addr, 0x03, 60,
          data=b'\\x00', retries_en=False); Request F7 <addr> 04 00 03 00
          <CRC>. Stock ruft es nie auf (nur manuell/Werk).
        - Antwort 1 B Status: 0x0C Erfolg, 0x00 nicht gelaufen, 0x04 läuft,
          0x08 fehlgeschlagen (Host-Texte 校准成功/没有运行校准/…).
        - FW (Handler 0xf79c): Regel-IRQ gesperrt, protect_en = 0,
          Phasenprüfung (schreibt phase_order_invert IMMER in den Flash),
          Encoder-IC-Reg 0x0E <- 0x58 + EEPROM-Brand (6 s), dann open-loop
          Dauerdrehung ~2,5 U/s, bis ~63 Umdrehungen (403225 x 62 us,
          Rampen), Abbruch bei Encoder-Status "fertig", 6,5 s warten.
          Dauer ~14-41 s. Danach Regler + PWM AUS (Motor stromlos).
        - Risiko: Achs-Crash, kein Schutz/kein EN-Notstopp/kein Bus während
          des Laufs, Encoder-EEPROM-Verschleiß, falsches phase_order_invert
          bei blockiertem Rotor. Daher keine sendefähige Implementierung."""
        raise NotImplementedError("TODO motor_encoder_calibrate: 0x03 live nie "
                                  "beobachtet")

    def elec_offset_calibrate(self):
        """GESPERRT: 0x04 elec_offset_calibrate – bewegt den Motor, schreibt Flash.

        Motor-FW-Analyse (B = Code):
        - Host: MOTOR_ELEC_OFFSET_CALIBRATE NUM=1..10 [EXTRUDER=1] bzw.
          _ALL (= NUM 1..4 nacheinander); motor_send_data(addr, 0x04, 60,
          data=b'\\x00', retries_en=False); Request F7 <addr> 04 00 04 00
          <CRC>. FW führt nur Payload-Byte 0 == 0x00 aus. Stock: nie.
        - Antwort 9 B: [pass u8 (1 = 校准成功), err f32 LE (el. Grad),
          avg f32 LE (Encoder-Counts)].
        - FW (Handler 0xf800): protect_en = 0, Phasenprüfung (phase_order_
          invert immer -> Flash), open-loop 1 Umdrehung vor + 1 zurück
          (200 Schritte, je ~20 ms), Pass = bewegt UND err < elec_angle_
          calibration_standar_deg (Default 15). Nur bei Pass: elec_offset/
          elec_offset_err_deg, ALLE Parameter in den Flash + Kalibrier-
          Record @0x08022B00 (Werks-Sicherung) überschrieben. Danach Regler
          + PWM AUS, Parameter aus dem Flash neu geladen. Dauer ~10-13 s.
        - Risiko: Bewegung ohne Schutz/Notstopp, Fehlkalibrierung an
          belasteter Achse überschreibt die Werks-Sicherung dauerhaft."""
        raise NotImplementedError("TODO motor_elec_offset_calibrate: 0x04 live "
                                  "nie beobachtet")

    def set_e_stall_mode(self, *args):
        """TODO: Extruder-Stall-Mode. Live: 0x11 [02] geht an den E-Motor
        NUR transparent unter 0x81 (Live-Log) – dieser Transport ist
        hier nicht nachgebaut (ttyS5-0x81 = X-Achse)."""
        raise NotImplementedError("TODO set_e_stall_mode: E-Motor nur über "
                                  "transparent (Nozzle-MCU), nicht serial_485")

    def set_z_stall_mode(self, *args):
        """TODO: Wrapper-Name `set_z_stall_mode`; live = 0x11 [02] an
        0x83/0x84 (Live-Log) -> Motor(bus, 0x83).set_mode(2)
        nutzen. Weitere Logik des Wrappers unbelegt."""
        raise NotImplementedError("TODO set_z_stall_mode: Wrapper-Logik unbelegt; "
                                  "set_mode(2) an 0x83/0x84 nutzen")

    def get_flash_param(self, param_id):
        """TODO: `MOTOR_FLASH_PARAM` – 0x07 live nie beobachtet (die Stock-
        "flash_param"-Enumeration läuft live über 0x06, s. read_sys_param).

        Hypothese REA 16.10. (VERMUTET, Vergleiche @0x4bbbc-0x4bd08):
        Payload [DATA] (+ 00 bei DATA 3/4, + ID bei DATA 5/6); DATA=6 =
        Lesen (Wert nach PARAMS_TYPE entpackt), DATA=5 = Schreiben (Wert
        gepackt: float 'f' (nativ), uint16_t '<H', uint32_t '>I' BIG-ENDIAN,
        int16_t '<h', sonst '>I'; Antwort Statusbyte 0 = Erfolg).
        Parameter NUM 1..10, ID (0), PARAMS (''), PARAMS_TYPE ('float')."""
        raise NotImplementedError("TODO MOTOR_FLASH_PARAM lesen (ID=%r): 0x07 "
                                  "live nie beobachtet" % (param_id,))

    def set_flash_param(self, param_id, value):
        """GESPERRT: `MOTOR_FLASH_PARAM` schreiben (0x07) – live nie beobachtet.

        Motor-FW-Analyse (B = Code):
        - Host: Payload [DATA] (+00 bei 3/4, +ID bei 5/6, bei 5 + Wert:
          float 'f', u16 '<H', i16 '<h', u32/i32 '>I' BIG-ENDIAN),
          retries_en=False. Stock nutzt 0x07 nie (Boot schreibt über 0x06).
        - FW-Subs: 01 = Werksreset (use_default_param -> Flash, Neustart;
          nur elec_offset/err/phase_order bleiben, u. a. amp_gain/R fallen
          auf FW-Default), 02 = Flash-Prüfung (5 B), 03 = alle Parameter in
          den Flash (Antwort [01] IMMER), 04 = aus Flash laden, 05 = Wert
          (LE ab Byte 2) nur ins RAM (Regelung kurz aus/Neu-Init; Float
          über Text mit 6 Nachkommastellen), 06 = Flash-Wert lesen.
        - Inkonsistenzen: u32/i32 Host BE vs. FW LE (byte-verdreht); Host
          wertet bei DATA=5 data[0] als Status 0/1, FW schickt Wert-Echo.
        - Risiko: Werksreset/Fehlwerte dauerhaft, Regelungsunterbrechung,
          bei latched Fehler bleibt die Regelung aus. Daher gesperrt."""
        raise NotImplementedError("TODO MOTOR_FLASH_PARAM schreiben (ID=%r): "
                                  "0x07 live nie beobachtet" % (param_id,))

    def pin_io(self, *args):
        """TODO: Pin-IO – live über die Main-MCU (set_pin_io, Live-Log;
        MOTOR_READ_ALL_PIN_IO 16.10. = Klipper-GPIO
        gpio_status PB9, PB10, !PB7, PB8, PB5, PB6, PA1, PB15), NICHT über den
        Motorbus; hier nicht nachgebaut."""
        raise NotImplementedError("TODO Pin-IO: läuft über Main-MCU, nicht RS485")



# --- Selbsttest (keine Hardware, kein Netzwerk nötig) ---
class _FakeBus:
    """Spielt Live-Mitschnitte ab und PRÜFT die gesendeten Bytes.

    script: Liste (erwartet_tx_hex, antwort_hex | None).
    erwartet_tx_hex = Log-Form "data_send" OHNE Kopf f7 und CRC
    (addr len status fun data) ODER Vollframe mit f7…crc (transparent-Log).
    antwort_hex = Log-Rohframe (mit Geräte-CRC) oder None (= Timeout).
    """

    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def transact(self, addr, fun, data=(), **kwargs):
        return self.transact_full(addr, fun, data, **kwargs)[3]

    def transact_full(self, addr, fun, data=(), **kwargs):
        """Wie serial485.transact_full: (addr, status, fun, daten, roh)."""
        self.calls.append((addr, fun, list(data), dict(kwargs)))
        assert self.script, "FakeBus: unerwarteter Sendeversuch"
        want_tx, rx = self.script.pop(0)
        frame = build_frame(addr, fun, list(data))
        want = bytes.fromhex(want_tx)
        got = frame if want[0] == 0xF7 else frame[1:-1]
        assert got == want, "TX %s != Log %s" % (got.hex(" "), want.hex(" "))
        if rx is None:
            raise TimeoutError("FakeBus-Timeout")
        raw = bytes.fromhex(rx)
        rfun, rdata = parse_frame(raw)  # prüft Geräte-CRC
        assert raw[1] == addr and rfun == fun, (raw[1], rfun, addr, fun)
        return raw[1], raw[3], rfun, rdata, raw


def selftest():
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("ok " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    def raises(exc, fn, *a, **kw):
        try:
            fn(*a, **kw)
        except exc:
            return True
        except Exception:
            return False
        return False

    MR = {"timeout": TIMEOUT_SYS_PARAM, "retries": 0}

    # 1. Dekodierung + Builder gegen Live-Bytes (Erwartung aus dem Log)
    check("0x06 00 00 96 43 = 300.0",
          decode_sys_param_float(bytes.fromhex("00 00 96 43")) == 300.0)
    check("0x06 00 00 c8 42 = 100.0",
          decode_sys_param_float(bytes.fromhex("00 00 c8 42")) == 100.0)
    check("float len-guard", raises(ValueError, decode_sys_param_float, b"\x00\x01\x02"))
    check("write-builder ID70=300 = Log-TX (Live-Log 05.10.2026)",
          bytes([0x81, 0x09, 0x00, FUNC_SYS_PARAM])
          + bytes(build_sys_param_write_payload(70, 300.0))
          == bytes.fromhex("81 09 00 06 01 46 00 00 96 43"))
    check("write-builder Vollframe = verifizierter Frame",
          build_frame(0x81, FUNC_SYS_PARAM, build_sys_param_write_payload(70, 300))
          == bytes.fromhex("f7 81 09 00 06 01 46 00 00 96 43 8f"))
    check("write-builder ID70=100 = Log-TX (Live-Log 12.10.2026)",
          build_frame(0x81, 6, build_sys_param_write_payload(70, 100.0))[1:-1]
          == bytes.fromhex("81 09 00 06 01 46 00 00 c8 42"))
    check("update-builder [03]", build_sys_param_update_payload() == [0x03])
    check("read-builder [02 09]", build_sys_param_read_payload(9) == [0x02, 0x09])
    for bad in [(-1, 1.0), (227, 1.0), (True, 1.0), (70, True), (70, "1"),
                (70, float("nan")), (70, float("inf")), (70, 1e300)]:
        check("write-builder guard %r" % (bad,),
              raises(ValueError, build_sys_param_write_payload, *bad))

    # 2. Param-Tabelle (live 227 IDs, Namen = grant0013)
    check("227 Namen", len(SYS_PARAM_NAMES) == SYS_PARAM_COUNT == 227
          and len(set(SYS_PARAM_NAMES)) == 227)
    check("ID 70 = pos-loop kp",
          sys_param_name(70) == "controller_pos_loop_pid_param_kp"
          and sys_param_id("controller_pos_loop_pid_param_kp") == 70)
    check("ID 9/188/226 Namen", sys_param_name(9) == "param_elec_offset"
          and sys_param_name(188) == "protection_param_prt_track_err_time"
          and sys_param_name(226) == "stall_pos_err_rad_for_slicer")
    check("Stock-Config-IDs configurable",
          set(SYS_PARAM_STOCK_CONFIGURED) | set(SYS_PARAM_STOCK_ALWAYS_READ)
          <= SYS_PARAM_CONFIGURABLE and len(SYS_PARAM_CONFIGURABLE) == 61)
    check("name-guard", raises(ValueError, sys_param_id, "gibtsnicht"))
    import motor_mini  # nur Abgleich; motor_mini importiert motor.py NICHT
    check("motor_mini-Schreibliste = Stock-Config-IDs (configurable)",
          motor_mini.MINI_WRITABLE_IDS == set(SYS_PARAM_STOCK_CONFIGURED)
          <= SYS_PARAM_CONFIGURABLE)
    wrong = _FakeBus([("81 05 00 06 02 09", "f7 82 07 00 06 53 4a 18 43 ae")])
    check("FakeBus prüft Absenderadresse (Antwort von 0x82 an 0x81)",
          raises(AssertionError, Motor(wrong, 0x81).read_sys_param, 9))

    # 3. read_sys_param gegen Live-Frames (Live-Log)
    bus = _FakeBus([
        ("81 05 00 06 02 09", "f7 81 07 00 06 20 29 f7 42 59"),   # ID 9
        ("81 05 00 06 02 0a", "f7 81 05 00 06 00 00 90"),         # ID 10, 2 B
        ("81 05 00 06 02 0a", "f7 81 05 00 06 00 00 90"),         # ID 10 raw
        ("81 05 00 06 02 19", "f7 81 07 00 06 dc 7a 77 40 93"),   # ID 25
        ("81 05 00 06 02 e2", "f7 81 03 00 06 af"),               # ID 226, 0 B
        ("82 05 00 06 02 09", "f7 82 07 00 06 53 4a 18 43 ae"),   # Y ID 9
    ])
    mx = Motor(bus, 0x81)
    check("ID 9 X = 123.58", abs(mx.read_sys_param(9) - 123.58) < 1e-3
          and bus.calls[0][3] == MR)
    check("ID 10 (2 B) -> ValueError", raises(ValueError, mx.read_sys_param, 10))
    check("ID 10 raw = 00 00", mx.read_sys_param_raw(10) == b"\x00\x00")
    check("ID 25 X = 3.867", abs(mx.get_sys_param(25) - 3.867) < 1e-3)
    check("ID 226 (0 B) -> None", mx.read_sys_param(226) is None)
    check("ID 9 Y = 152.29", abs(Motor(bus, 0x82).read_sys_param(9) - 152.29) < 1e-2)
    check("read id-guard sendet nichts",
          raises(ValueError, mx.read_sys_param, 227) and len(bus.calls) == 6
          and not bus.script)

    # 4. write_sys_param / update: Schutz + Live-Echo
    nobus = _FakeBus([])
    m0 = Motor(nobus, 0x81)
    check("write ohne confirm -> PermissionError",
          raises(PermissionError, m0.write_sys_param, 70, 100.0))
    check("write confirm='yes' -> PermissionError",
          raises(PermissionError, m0.write_sys_param, 70, 100.0, confirm="yes"))
    check("set_sys_param ohne confirm gesperrt",
          raises(PermissionError, m0.set_sys_param, 70, 100.0))
    check("update ohne confirm gesperrt", raises(PermissionError, m0.update_sys_params))
    check("gesperrte Writes senden nichts", nobus.calls == [])
    bus = _FakeBus([
        ("81 09 00 06 01 46 00 00 c8 42", "f7 81 07 00 06 00 00 c8 42 d4"),
        ("f7 81 09 00 06 01 46 00 00 96 43 8f", "f7 81 07 00 06 00 00 96 43 09"),
        ("81 04 00 06 03", "f7 81 04 00 06 01 21"),
        ("81 09 00 06 01 46 00 00 c8 42", "f7 81 07 00 06 00 00 96 43 09"),
    ])
    mw = Motor(bus, 0x81)
    check("write ID70=100 Echo (10-12:10700/10706)",
          mw.write_sys_param(70, 100.0, confirm=True) == 100.0
          and bus.calls[0][3] == MR)
    check("write ID70=300 Echo (Live-Log 14.10.2026)",
          mw.set_sys_param(70, 300, confirm=True) == 300.0)
    check("update [03] -> [01] (Live-Log)",
          mw.update_sys_params(confirm=True) is True)
    check("falsches Echo -> RuntimeError",
          raises(RuntimeError, mw.write_sys_param, 70, 100.0, confirm=True))
    check("alle Write-Frames verbraucht", not bus.script)

    # 4b. Gesperrte IDs (not configurable) + Wert-Plausibilität (nichts gesendet)
    for pid in (1, 224, 225, 0, 11):
        check("ID %d (%s) ohne allow_locked gesperrt" % (pid, sys_param_name(pid)),
              raises(PermissionError, m0.write_sys_param, pid, 1.0, confirm=True)
              and raises(PermissionError, m0.set_sys_param, pid, 1.0, confirm=True))
    check("allow_locked ohne confirm weiter gesperrt",
          raises(PermissionError, m0.write_sys_param, 1, 1.0, allow_locked=True))
    check("allow_locked='yes' zählt nicht",
          raises(PermissionError, m0.write_sys_param, 1, 1.0, confirm=True,
                 allow_locked="yes"))
    for bad in (1e-50, -1e-50, 3.5e38, -1e39, float("inf"), float("nan")):
        check("Wert %r -> ValueError (auch mit confirm)" % (bad,),
              raises(ValueError, m0.write_sys_param, 70, bad, confirm=True)
              and raises(ValueError, build_sys_param_write_payload, 70, bad))
    check("float32-Grenzen ok", build_sys_param_write_payload(70, FLOAT32_MAX)[2:]
          == list(struct.pack("<f", FLOAT32_MAX))
          and build_sys_param_write_payload(70, 0.0)[2:] == [0, 0, 0, 0]
          and build_sys_param_write_payload(70, -0.0)[2:] == [0, 0, 0, 0x80])
    f32, rel = float32_rounding(0.1)
    check("float32_rounding 0.1", f32 != 0.1 and 0 < rel < 1e-7
          and float32_rounding(300.0) == (300.0, 0.0))
    check("Sperren/Plausibilität senden nichts", nobus.calls == [])
    bus = _FakeBus([
        # verifizierter Live-Frame (Live-Log 05.10.2026), Echo Live-Log 14.10.2026
        ("f7 81 09 00 06 01 46 00 00 96 43 8f", "f7 81 07 00 06 00 00 96 43 09"),
        # ID 1 mit allow_locked: SYNTHETISCH (nie live geschrieben), CRC neu
        ("81 09 00 06 01 01 00 00 80 3f", "f7 81 07 00 06 00 00 80 3f %02x"
         % crc8_cal(bytes.fromhex("07 00 06 00 00 80 3f"))),
    ])
    mw = Motor(bus, 0x81)
    check("gültiger Write ID70=300 = Live-Frame",
          mw.write_sys_param(70, 300.0, confirm=True) == 300.0)
    check("ID 1 nur mit confirm+allow_locked",
          mw.write_sys_param(1, 1.0, confirm=True, allow_locked=True) == 1.0
          and not bus.script)

    # 5. check_protection / write_params_raw ([0b] live, [05] nie)
    bus = _FakeBus([
        ("81 04 00 0c 0b", "f7 81 0b 00 0c 00 00 00 00 00 00 00 00 fb"),
        ("f7 81 04 00 0c 0b 95", "f7 81 0b 00 0c 00 00 00 00 00 00 00 00 fb"),
        ("84 04 00 0c 0b", "f7 84 0b 00 0c 00 00 00 00 00 00 00 00 fb"),
    ])
    p0 = Motor(bus, 0x81).check_protection()
    check("protection X 8x00 dekodiert ok (Live-Log)",
          p0["ok"] is True and p0["err_code"] == 0 == p0["warning_code"]
          and p0["errors"] == [] == p0["warnings"] and p0["raw"] == bytes(8)
          and bus.calls[0][3] == {"timeout": TIMEOUT_PROTECTION, "retries": 0})
    check("write_params_raw() = Alias raw (transparent-Frame Live-Log)",
          Motor(bus, 0x81).write_params_raw() == bytes(8))
    check("protection Z1 raw", Motor(bus, 0x84).check_protection_raw() == bytes(8)
          and Motor.check_protection_code is Motor.check_protection)
    check("write_params_raw(8B) abgelehnt",
          raises(ValueError, Motor(nobus, 0x82).write_params_raw, [0] * 8))
    check("[05] nirgends sendbar", nobus.calls == [] and SUB_PROT_UNKNOWN_05 == 5
          and "05" not in [("%02x" % c[2][0]) for c in bus.calls])

    # 6. 0x11 set_mode / get_stall_mode, 0x0E, 0x12
    bus = _FakeBus([
        ("81 04 00 11 01", "f7 81 04 00 11 01 1d"),     # 10-12:2864/2870
        ("f7 81 04 00 11 02 14", "f7 81 04 00 11 02 14"),  # Live-Log
        ("81 04 00 11 03", "f7 81 04 00 11 03 13"),     # 10-12:31436/31454
        ("81 04 00 11 04", "f7 81 05 00 11 02 00 0e"),  # 10-12:31409/31432
        ("83 04 00 0e 02", "f7 83 04 00 0e 83 0e"),     # Live-Log
        ("f7 81 04 00 0e 02 80", "f7 81 04 00 0e 81 00"),  # Live-Log
        ("83 04 00 12 02", "f7 83 0b 00 12 cd ab 00 00 00 00 00 00 ad"),
        # ^ TX Live-Log; Antwort = PLATZHALTER (echte UUID aus dem Log
        #   entfernt), CRC neu berechnet
        ("81 04 00 11 01", "f7 81 04 00 11 03 13"),     # falsches Echo
        ("83 04 00 0e 02", None),                       # Timeout
    ])
    m = Motor(bus, 0x81)
    check("set_mode 1 Echo", m.set_mode(1) == 1
          and bus.calls[0][3] == {"timeout": TIMEOUT_STALL, "retries": 0})
    check("set_mode 2 Echo", m.set_mode(2) == 2)
    check("set_mode 3 Echo", m.set_mode(3) == 3)
    check("get_stall_mode -> 2", m.get_stall_mode() == 2)
    mz = Motor(bus, 0x83)
    check("read_485_addr Z = 0x83", mz.read_485_addr() == 0x83
          and bus.calls[4][3] == {"timeout": TIMEOUT_ADDR, "retries": 0})
    check("read_485_addr X = 0x81", m.read_485_addr() == 0x81)
    u = mz.dev_uuid()
    check("read_uuid 8 B Präfix cd ab", len(u) == 8 and u[:2] == UUID_PREFIX
          and bus.calls[6][3] == {"timeout": TIMEOUT_UUID, "retries": 0})
    check("set_mode falsches Echo -> RuntimeError", raises(RuntimeError, m.set_mode, 1))
    check("Timeout durchgereicht", raises(TimeoutError, mz.read_485_addr))
    n = len(bus.calls)
    for bad in [(), (0,), (4,), (256,), (True,), (1.0,), (0x02, 0x00)]:
        check("set_mode guard %r" % (bad,), raises((TypeError, ValueError),
                                                    m.set_mode, *bad))
    check("set_mode guards senden nichts", len(bus.calls) == n and not bus.script)

    # 7. Gesperrtes / Stubs / Adressen
    for args in [(), (0x9643,)]:
        check("read_encoder%r wirft" % (args,),
              raises(NotImplementedError, Motor(nobus, 0x81).read_encoder, *args))
    check("addr-guard 0x85 (Extruder nicht RS485)", raises(ValueError, Motor, None, 0x85))
    check("addr-guard 0x05", raises(ValueError, Motor, None, 0x05))
    mm = Motor(nobus, 0x84)
    stub_ok = all(raises(NotImplementedError, f) for f in [
        mm.encoder_calibrate, mm.elec_offset_calibrate,
        mm.pin_io,
        lambda: mm.set_e_stall_mode(1), lambda: mm.set_z_stall_mode(1),
        lambda: mm.get_flash_param(0), lambda: mm.set_flash_param(0, 1),
        mm.reboot, mm.boot, mm.systemid])
    check("stubs not-implemented", stub_ok)
    check("stubs/Sperren senden nichts", nobus.calls == [])
    check("kurz-frame", raises(ValueError, parse_frame, bytes.fromhex("f7 81")))
    corrupt = bytearray(bytes.fromhex("f7 81 07 00 06 00 00 96 43 09"))
    corrupt[-1] ^= 0xFF
    check("crc-fehler", raises(ValueError, parse_frame, bytes(corrupt)))

    # 7b. 0x0F Version + 0x08 Variablen (Live-Abfragen 16.10.2026,
    #     Scan-Rohdaten lokal, nicht im Repo; Motor X, Standby)
    V_RX = "f7 81 0f 00 0f 01 09 07 51 01 03 01 03 02 4b 01 00 02"
    bus = _FakeBus([("81 04 00 0f 00", V_RX), ("81 04 00 0f 00", V_RX)])
    v = Motor(bus, 0x81).query_motor_version()
    check("0x0F Version live (fw 1.9.7.81 ...)",
          v == {"fireware_ver": "1.9.7.81", "cmd_system_ver": "1.3",
                "protection_ver": "1.3", "flash_param_ver": "2.75",
                "logger_ver": "1.0",
                "raw": bytes.fromhex("01 09 07 51 01 03 01 03 02 4b 01 00")}
          and bus.calls[0][3] == {"timeout": TIMEOUT_VERSION, "retries": 0})
    check("0x0F Alias motor_version_normal",
          Motor(bus, 0x81).motor_version_normal()["flash_param_ver"] == "2.75"
          and not bus.script)
    check("0x0F Vollframe TX", build_frame(0x81, FUNC_VERSION,
                                           build_motor_version_payload())[1:-1]
          == bytes.fromhex("81 04 00 0f 00"))
    check("0x0F Längen-Guard",
          raises(ValueError, decode_motor_version, bytes(11))
          and raises(ValueError, decode_motor_version, bytes(13)))
    scan = {i: "00 00 00 00" for i in range(1, 13)}
    scan.update({9: "33 33 d3 3a", 10: "33 33 53 3a", 11: "52 04 00 00"})
    crcs = {i: "0a" for i in range(1, 13)}
    crcs.update({9: "e6", 10: "50", 11: "71"})  # Geräte-CRC aus dem Scan
    bus = _FakeBus([("81 04 00 08 %02x" % i,
                     "f7 81 07 00 08 %s %s" % (scan[i], crcs[i]))
                    for i in range(1, 13)])
    mv = Motor(bus, 0x81)
    res = {i: mv.read_variable(i) for i in range(1, 13)}
    check("0x08 idx 1..12 gelesen, retries=0",
          not bus.script and len(bus.calls) == 12 and all(
              c[2] == [i + 1] and c[3] == {"timeout": TIMEOUT_VARIABLE, "retries": 0}
              for i, c in enumerate(bus.calls)))
    check("0x08 idx 1-8/12 = 0", all(res[i]["value"] == 0 and res[i]["raw"] == bytes(4)
                                     for i in list(range(1, 9)) + [12]))
    check("0x08 idx 9 float 0.0016113",
          abs(res[9]["float"] - 0.0016113281017169356) < 1e-12
          and res[9]["type"] == "float" and res[9]["value"] == res[9]["float"])
    check("0x08 idx 10 float 0.0008057",
          abs(res[10]["value"] - 0.0008056640508584678) < 1e-12)
    check("0x08 idx 11 int 1106", res[11]["int"] == 1106 and res[11]["value"] == 1106
          and res[11]["type"] == "int" and res[11]["raw"] == bytes.fromhex("52 04 00 00"))
    check("0x08 VARIABLE_TYPES 1..12", sorted(VARIABLE_TYPES) == list(range(1, 13))
          and [VARIABLE_TYPES[i] for i in (9, 10, 11, 12)] == ["float", "float", "int", "int"])
    # REA 16.10.: feste Host-Typkette + Indexnamen
    check("0x08 Host-Kette 1..10,17,18 '<f' / 11..16 '<I'",
          sorted(VARIABLE_HOST_FORMATS) == list(range(1, 19))
          and all(VARIABLE_HOST_FORMATS[i] == "<f" for i in list(range(1, 11)) + [17, 18])
          and all(VARIABLE_HOST_FORMATS[i] == "<I" for i in range(11, 17)))
    check("0x08 Namen: Host 5..8 + abgeleitet 11 in VARIABLE_TABLE enthalten",
          VARIABLE_NAMES_HOST == {5: "pos_fdb", 6: "spd_fdb", 7: "id_fdb", 8: "iq_fdb"}
          and all(VARIABLE_NAMES[i] == n for i, n in
                  list(VARIABLE_NAMES_HOST.items()) + list(VARIABLE_NAMES_DERIVED.items()))
          and res[5]["name"] == "pos_fdb" and res[1]["name"] == "pos_ref"
          and res[11]["name"] == "pos_counts" and res[9]["name"] == "ia"
          and res[11]["fmt"] == "<I" and res[9]["fmt"] == "<f")
    check("0x08 int unsigned '<I' (ff ff ff ff -> 4294967295)",
          decode_variable(11, b"\xff\xff\xff\xff")["value"] == 0xFFFFFFFF
          and decode_variable(12, b"\x00\x00\x00\x80")["int"] == 0x80000000)
    check("0x08 Hostgrenze 13..18 bekannt, aber gesperrt",
          all(raises(ValueError, decode_variable, i, bytes(4)) for i in range(13, 19)))
    # Feedback-Helfer: synthetische Antworten (Bytes = struct.pack('<f'))
    fb = {5: 1.5, 6: -2.25, 7: 3.0, 8: 4.0}
    def _vrx(v):
        body = bytes([0x07, 0x00, FUNC_VARIABLE]) + struct.pack("<f", v)
        return "f7 81 %s %02x" % (body.hex(" "), crc8_cal(body))
    bus = _FakeBus([("81 04 00 08 %02x" % i, _vrx(fb[i])) for i in (5, 6, 7, 8)])
    mf = Motor(bus, 0x81)
    pos, spd, cur = (mf.read_position_feedback(), mf.read_speed_feedback(),
                     mf.read_current_feedback())
    check("Feedback pos idx5 / spd idx6 / id,iq idx7,8 + hypot",
          pos == 1.5 and spd == -2.25 and cur == {"id": 3.0, "iq": 4.0, "abs": 5.0}
          and [c[2] for c in bus.calls] == [[5], [6], [7], [8]] and not bus.script)
    check("motor_get Alias", Motor.motor_get is Motor.read_variable)
    # Skalierung pos_fdb / idx 11 (live 16.10., Messfahrt X, rotation_distance 40)
    live = [(50, 1.35489, 690), (100, 2.92568, 1490), (150, 4.49648, 2290),
            (160, 6.06728, 3090), (170, 1.35489, 690), (180, 2.92568, 1490),
            (185, 3.71108, 1890), (200, 6.06728, 3090), (250, 1.35470, 690)]
    check("Skalierung 690 -> 1.35481 rad, 3200/rev",
          POS_COUNTS_PER_REV == 3200 and abs(counts_to_rad(690) - 1.35481) < 1e-5
          and abs(counts_to_rad(3200) - 2 * math.pi) < 1e-12)
    check("Skalierung Live-Tabelle idx5 = idx11*2pi/3200 (Tol 2e-4 rad)",
          all(abs(counts_to_rad(c) - r) < POS_RAD_TOL
              and abs(pos_rad_to_counts(r) - c) < 0.1 for _, r, c in live))
    check("Skalierung 800 Counts = 10 mm, 80 Counts/mm",
          counts_to_mm(1490 - 690) == 10.0 and counts_to_mm(80) == 1.0
          and counts_to_mm(3200) == POS_ROTATION_DISTANCE == 40.0)
    check("Skalierung Wrap X160->X170 (+800), X185<->X150 (+-400)",
          counts_delta(690, 3090) == 800 and counts_delta(2290, 1890) == 400
          and counts_delta(1890, 2290) == -400 and counts_delta(690, 690) == 0)
    check("Skalierung Single-Turn: Live-Counts = (80*X + 3090) mod 3200",
          all((80 * x + 3090) % 3200 == c for x, _, c in live))
    def _vrxb(i, raw):
        body = bytes([0x07, 0x00, FUNC_VARIABLE]) + raw
        return ("81 04 00 08 %02x" % i,
                "f7 81 %s %02x" % (body.hex(" "), crc8_cal(body)))
    bus = _FakeBus([_vrxb(5, struct.pack("<f", 1.35489)),
                    _vrxb(11, struct.pack("<I", 690))])
    pf = Motor(bus, 0x81).read_position_feedback(with_counts=True)
    check("read_position_feedback(with_counts) idx 5+11",
          [c[2] for c in bus.calls] == [[5], [11]] and not bus.script
          and pf["counts"] == 690 and abs(pf["pos_fdb"] - 1.35489) < 1e-6
          and abs(pf["counts_from_rad"] - 690) < 0.1
          and abs(pf["mm_in_rev"] - 8.625) < 1e-9)
    bus = _FakeBus([_vrxb(11, struct.pack("<I", 3090))])
    check("read_position_counts idx 11",
          Motor(bus, 0x81).read_position_counts() == 3090 and not bus.script)
    # Motor_cmd-Tabelle + Adressen + Fehlerbits (Konstanten der statischen Analyse)
    check("MOTOR_CMD Host-Tabelle", MOTOR_CMD["get"] == 0x08 == FUNC_VARIABLE
          and MOTOR_CMD["version"] == 0x0F and MOTOR_CMD["protection"] == 0x0C
          and MOTOR_CMD["sys_param"] == 0x06 and MOTOR_CMD["stall_mode"] == 0x11
          and MOTOR_CMD["read485_addr"] == MOTOR_CMD["set485_addr"] == 0x0E
          and MOTOR_CMD["dev_uuid"] == 0x12 and MOTOR_CMD["boot"] == 0x0B
          and [MOTOR_CMD[k] for k in ("err_detail_data", "cut_detail_data",
                                      "pos_data_sample", "spd_data_sample")]
          == [0x13, 0x14, 0x15, 0x16] and len(MOTOR_CMD) == 19)
    check("MOTOR_ADDR_BY_NUM 1..4 = MOTOR_ADDRS, '9' = FF",
          tuple(MOTOR_ADDR_BY_NUM[str(n)] for n in range(1, 5)) == MOTOR_ADDRS
          and MOTOR_ADDR_BY_NUM["9"] == 0xFF and MOTOR_ADDR_BY_NUM["0"] == 0x80)
    check("ERR_DETAIL_FORMAT 66 B / 16 Felder",
          struct.calcsize(ERR_DETAIL_FORMAT) == 66
          and struct.calcsize(CUT_DETAIL_FORMAT) == 70 and len(ERR_DETAIL_FIELDS) == 16
          and len(struct.unpack(ERR_DETAIL_FORMAT, bytes(66))) == 15 + 8)
    check("Fehlerbits 0..13 -> key781..794, Warnbits 0..2 -> key795..797",
          [MOTOR_ERROR_BITS[b][0] for b in range(14)] == ["key%d" % k for k in range(781, 795)]
          and [MOTOR_WARNING_BITS[b][0] for b in range(3)] == ["key795", "key796", "key797"])
    check("decode_error_bits 0x105 / 0x4000",
          [b[:2] for b in decode_error_bits(0x105)] == [(0, "key781"), (2, "key783"), (8, "key789")]
          and decode_error_bits(0x4000) == [(14, None, "unbekannt")]
          and decode_error_bits(0) == [])
    check("decode_warning_bits 0x6", [b[1] for b in decode_warning_bits(6)] == ["key796", "key797"]
          and raises(ValueError, decode_warning_bits, -1)
          and raises(ValueError, decode_error_bits, True))
    check("decode_detail_union Encoder-Puls <ii / unbekannt {}",
          decode_detail_union(1, struct.pack("<ii", -5, 6))
          == {"lower_encoder_pulse": -5, "upper_encoder_pulse": 6}
          and decode_detail_union(0, bytes(8)) == {}
          and decode_detail_union(0x2000, bytes(8)) == {})
    check("ERR_CODE_TO_UNION Bitwerte", sorted(ERR_CODE_TO_UNION) == [1, 4, 8, 32, 64, 128, 256]
          and all(struct.calcsize(v[0]) == 4 * len(v[1])
                  for v in ERR_CODE_TO_UNION.values() if v[0]))
    for bad in (0, 13, -1, 255, True, 1.0, None, "9"):
        check("0x08 idx-guard %r (Host-Crash 0/13)" % (bad,),
              raises(ValueError, Motor(nobus, 0x81).read_variable, bad))
    check("0x08 Längen-Guard", raises(ValueError, decode_variable, 9, bytes(3)))
    check("0x08 Guards senden nichts", nobus.calls == [])

    # 7c. Motor-FW-Analyse 16.10.: Variablentabelle, Helfer, DIR-Gegenprobe
    check("VARIABLE_TABLE idx 0..18 vollständig, Status/Einheiten gültig",
          sorted(VARIABLE_TABLE) == list(range(19))
          and set(VARIABLE_STATUS.values()) <= {"belegt", "fw", "vermutet", "offen"}
          and set(u for u in VARIABLE_UNITS.values() if u)
          <= {"rad", "rad/s", "A", "counts", "°C", "V", "0/1"}
          and sorted(i for i in VARIABLE_TABLE if VARIABLE_NAMES.get(i) is None) == [])
    check("VARIABLE_TABLE Kernnamen/Einheiten",
          [VARIABLE_NAMES[i] for i in (1, 2, 3, 4, 9, 10, 12)]
          == ["pos_ref", "spd_ref", "id_ref", "iq_ref", "ia", "ib", "dir_input"]
          and VARIABLE_UNITS[6] == "rad/s" and VARIABLE_UNITS[5] == "rad"
          and VARIABLE_UNITS[9] == "A" and VARIABLE_UNITS[11] == "counts"
          and VARIABLE_UNITS[17] == "°C" and VARIABLE_UNITS[18] == "V"
          and VARIABLE_STATUS[12] == "belegt" and VARIABLE_STATUS[13] == "vermutet"
          and VARIABLE_STATUS[15] == "fw" and VARIABLE_STATUS[16] == "fw")
    # 17.10.2026 (Motor-FW-Analyse): idx 15/16 benannt, Encoder 32768/U
    check("idx 15/16 = encoder_single_turn / encoder_fdb (counts, '<I')",
          VARIABLE_NAMES[15] == "encoder_single_turn"
          and VARIABLE_NAMES[16] == "encoder_fdb"
          and VARIABLE_UNITS[15] == VARIABLE_UNITS[16] == "counts"
          and VARIABLE_FW_FORMATS[15] == VARIABLE_FW_FORMATS[16] == "<I"
          and (IDX_ENCODER_SINGLE_TURN, IDX_ENCODER_FDB) == (15, 16)
          and decode_variable_fw(16, struct.pack("<I", 4421))["value"] == 4421)
    check("encoder_counts_to_rad 32768/U (live 0x14: 4421 -> 0,8477 rad)",
          ENCODER_COUNTS_PER_REV == 32768
          and abs(encoder_counts_to_rad(4421) - 0.84772) < 1e-5
          and abs(encoder_counts_to_rad(16384) - math.pi) < 1e-12)
    # 17.10.2026 (Motor-FW-Analyse): Bits ohne Setzer + Motorcheck-Namen
    check("Fehlerbits ohne Setzer 1/4/6, Warnbit 0; Motorcheck 9..13",
          MOTOR_ERROR_BITS_NEVER_SET == (1, 4, 6)
          and MOTOR_WARNING_BITS_NEVER_SET == (0,)
          and all(b in MOTOR_ERROR_BITS for b in MOTOR_ERROR_BITS_NEVER_SET)
          and sorted(MOTOR_ERROR_BITS_MOTORCHECK) == [9, 10, 11, 12, 13]
          and MOTOR_ERROR_BITS_MOTORCHECK[11] == "PhaseNotConnected"
          and MOTOR_ERROR_BITS[11][0] == "key792")
    check("decode_variable liefert unit/status",
          decode_variable(6, struct.pack("<f", 1.572))["unit"] == "rad/s"
          and decode_variable(12, bytes(4))["status"] == "belegt")
    # Live-Werte Fahrt X F600 (Live-Abfragen 16.10.2026, Bytes = struct.pack)
    f600 = {1: 5.188, 2: 1.633, 3: 0.0, 4: 0.0794, 9: -0.0725, 10: -0.0363}
    bus = _FakeBus([_vrxb(i, struct.pack("<f", f600[i])) for i in (1, 2, 3, 4, 9, 10)]
                   + [_vrxb(12, struct.pack("<I", v)) for v in (0, 1, 1)])
    mr = Motor(bus, 0x81)
    ref = mr.read_reference_values()
    check("read_reference_values idx 1..4 (live F600)",
          sorted(ref) == ["id_ref", "iq_ref", "pos_ref", "spd_ref"]
          and abs(ref["pos_ref"] - 5.188) < 1e-5 and abs(ref["spd_ref"] - 1.633) < 1e-5
          and ref["id_ref"] == 0.0 and abs(ref["iq_ref"] - 0.0794) < 1e-6
          and [c[2] for c in bus.calls] == [[1], [2], [3], [4]])
    ph = mr.read_phase_currents()
    # KORREKTUR 17.10.2026 (Motor-FW-Analyse): phase_order_invert 0 (live)
    # -> Ib = idx 10 unverändert (vorher fälschlich -idx10)
    check("read_phase_currents idx 9/10 -> Ia, Ib = +idx10 (live F600, invert 0)",
          abs(ph["ia"] + 0.0725) < 1e-6 and abs(ph["ib"] + 0.0363) < 1e-6
          and abs(ph["ib_fw"] + 0.0363) < 1e-6
          and abs(ph["minus_ib"] - 0.0363) < 1e-6
          and ph["phase_order_invert"] == 0
          and [c[2] for c in bus.calls[4:6]] == [[9], [10]])
    check("IDX_IB = 10, Alt-Alias IDX_MINUS_IB, Live-Default 0",
          IDX_IB == IDX_MINUS_IB == 10 and PHASE_ORDER_INVERT_LIVE == 0
          and SYS_PARAM_PHASE_ORDER_INVERT == 10
          and SYS_PARAM_NAMES[10] == "param_phase_order_invert")
    check("ib_from_idx10: invert 0 -> +, != 0 -> - (@0xbb30 beqz)",
          ib_from_idx10(-0.0363) == -0.0363
          and ib_from_idx10(-0.0363, 0) == -0.0363
          and ib_from_idx10(-0.0363, 1) == 0.0363
          and ib_from_idx10(0.5, -1) == -0.5
          and all(raises(ValueError, ib_from_idx10, 1.0, bad)
                  for bad in (True, False, None, 1.0, "0")))
    bus_inv = _FakeBus([_vrxb(i, struct.pack("<f", f600[i])) for i in (9, 10)])
    ph1 = Motor(bus_inv, 0x81).read_phase_currents(phase_order_invert=1)
    check("read_phase_currents(phase_order_invert=1) -> Ib = -idx10",
          abs(ph1["ib"] - 0.0363) < 1e-6 and abs(ph1["ib_fw"] + 0.0363) < 1e-6
          and abs(ph1["minus_ib"] + 0.0363) < 1e-6 and not bus_inv.script)
    nobus_ph = _FakeBus([])
    check("read_phase_currents ungültiges invert -> ValueError ohne Senden",
          raises(ValueError, Motor(nobus_ph, 0x81).read_phase_currents, True)
          and raises(ValueError, Motor(nobus_ph, 0x81).read_phase_currents, "1")
          and nobus_ph.calls == [])
    # DIR-Gegenprobe 16.10.: Fahrt -X = 0, Fahrt +X = 1, Stillstand danach = 1
    check("read_dir_input idx 12: -X 0 / +X 1 / Stillstand hält 1",
          [mr.read_dir_input() for _ in range(3)] == [0, 1, 1] and not bus.script)
    # read_variable_raw_fw: Sperre + FW-Bereich (synthetisch, nie live)
    check("read_variable_raw_fw ohne confirm gesperrt, nichts gesendet",
          raises(PermissionError, Motor(nobus, 0x81).read_variable_raw_fw, 17)
          and raises(PermissionError, Motor(nobus, 0x81).read_variable_raw_fw, 13,
                     confirm="yes")
          and nobus.calls == [])
    for bad in (19, -1, 255, True, 1.0):
        check("read_variable_raw_fw idx-guard %r" % (bad,),
              raises(ValueError, Motor(nobus, 0x81).read_variable_raw_fw, bad,
                     confirm=True))
    bus = _FakeBus([_vrxb(17, struct.pack("<f", 31.5)), _vrxb(18, struct.pack("<f", 24.1)),
                    _vrxb(0, bytes(4)), _vrxb(13, struct.pack("<I", 1))])
    mfw = Motor(bus, 0x81)
    t17, t18, t0, t13 = [mfw.read_variable_raw_fw(i, confirm=True) for i in (17, 18, 0, 13)]
    check("read_variable_raw_fw 17 °C / 18 V / 0 / 13 (SYNTHETISCH)",
          abs(t17["value"] - 31.5) < 1e-6 and t17["unit"] == "°C"
          and abs(t18["value"] - 24.1) < 1e-5 and t18["unit"] == "V"
          and t0["value"] == 0 and t0["name"] == "const_zero"
          and t13["value"] == 1 and t13["type"] == "int" and t13["name"] == "en_input"
          and all(c[3] == {"timeout": TIMEOUT_VARIABLE, "retries": 0} for c in bus.calls)
          and not bus.script)
    check("read_variable bleibt 1..12 (Host-Crash), decode_variable_fw 0..18",
          raises(ValueError, Motor(nobus, 0x81).read_variable, 17)
          and decode_variable_fw(18, bytes(4))["fmt"] == "<f"
          and raises(ValueError, decode_variable_fw, 19, bytes(4)))

    # 7d. STATUS-Bitfeld + 0x0C-Layout (MOTOR-FW + Host-Parser)
    st0, st7 = decode_motor_status(0x00), decode_motor_status(0x07)
    check("decode_motor_status 00 (live) / 07 / 0x0a",
          st0["ok"] and not (st0["stall"] or st0["err_latched"] or st0["warn_latched"])
          and st7["stall"] and st7["err_latched"] and st7["warn_latched"]
          and st7["other"] == 0 and decode_motor_status(0x0A)["other"] == 0x08
          and decode_motor_status(0x0A)["err_latched"]
          and raises(ValueError, decode_motor_status, 256)
          and raises(ValueError, decode_motor_status, True))
    pr = decode_protection(bytes.fromhex("05 01 00 00 06 00 00 00"))
    check("decode_protection Fehler 0x105 / Warnung 0x6 (u32 LE)",
          pr["err_code"] == 0x105 and pr["warning_code"] == 6 and not pr["ok"]
          and [b[1] for b in pr["errors"]] == ["key781", "key783", "key789"]
          and [b[1] for b in pr["warnings"]] == ["key796", "key797"])
    check("decode_protection Byte 3 Bit 7 = Fehlerbit 31, Byte 4 = Warnbit 0",
          decode_protection(bytes.fromhex("00 00 00 80 01 00 00 00"))["errors"]
          == [(31, None, "unbekannt")]
          and decode_protection(bytes.fromhex("00 00 00 80 01 00 00 00"))["warnings"][0][1]
          == "key795"
          and raises(ValueError, decode_protection, bytes(7)))

    def _host_protection(data):
        # Host-Parser motor_auto_check_protection @0x56788 (Motor-FW-Analyse)
        s_ = "".join(bin(b)[2:].zfill(8)[::-1] for b in data)
        return ([i for i, c in enumerate(s_[:32]) if c == "1"],
                [i for i, c in enumerate(s_[32:]) if c == "1"])
    import random
    rnd = random.Random(1016)
    same = True
    for _ in range(300):
        d = bytes(rnd.randrange(256) for _ in range(8))
        p_ = decode_protection(d)
        same &= _host_protection(d) == ([b[0] for b in p_["errors"]],
                                        [b[0] for b in p_["warnings"]])
    check("decode_protection = Host-Bitstring-Parser (300 Zufallsframes)", same)
    check("0x0C Sub-Konstanten (FW-Sprungtabelle 00..0B)",
          [SUB_PROT_NOP, SUB_PROT_EN_OFF, SUB_PROT_EN_ON, SUB_PROT_REPORT_OFF,
           SUB_PROT_REPORT_ON, SUB_PROT_CLEAR, SUB_PROT_ERR_BACKUP, SUB_PROT_ERR_LATCHED,
           SUB_PROT_WARN_BACKUP, SUB_PROT_WARN_LATCHED, SUB_PROT_BACKUPS, SUB_PROT_CHECK]
          == list(range(12)) and SUB_PROT_UNKNOWN_05 == SUB_PROT_CLEAR
          and SUB_PROT_CHECK in SUB_PROT_SIDE_EFFECT
          and MOTOR_FW_CMD_NAMES[0x11] == "emergency_pin_mode")
    # check_protection mit SYNTHETISCHEM Fehlerframe (CRC neu)
    def _prx(addr, status, data):
        body = bytes([len(data) + 3, status, FUNC_PROTECTION]) + bytes(data)
        return "f7 %02x %s %02x" % (addr, body.hex(" "), crc8_cal(body))
    bus = _FakeBus([("82 04 00 0c 0b", _prx(0x82, 0x06,
                                            bytes.fromhex("00 01 00 00 02 00 00 00")))])
    pc = Motor(bus, 0x82).check_protection()
    check("check_protection dekodiert Schleppfehler + Unterspannung (SYNTHETISCH)",
          pc["err_code"] == 0x100 and pc["errors"][0][:2] == (8, "key789")
          and pc["warnings"][0][:2] == (1, "key796") and not bus.script)

    # 7e. 0x13..0x16 LIVE (16.10.2026 17:26 Log-Uhr, Motor X 0x81, Stillstand
    # nach M84, Klipper-Webhooks motor_err_detail_data / motor_cut_detail_data,
    # {"num": 1}). Rohframes aus klippy.log; die 6 echten UUID-Bytes nach
    # "cd ab" sind durch 00 ersetzt (PLATZHALTER) und die CRC dafür NEU
    # berechnet (CRC-8/ATM über len..data). 0x15/0x16 enthalten keine UUID ->
    # Original-CRC 6f / 1e (Geräte-CRC, von parse_frame geprüft).
    def _drx(addr, fun, status, data):
        body = bytes([len(data) + 3, status, fun]) + bytes(data)
        return "f7 %02x %s %02x" % (addr, body.hex(" "), crc8_cal(body))
    uuid0 = bytes.fromhex("cd ab 00 00 00 00 00 00")   # PLATZHALTER
    live13 = ("f7 81 4d 00 13 00 00 00 00 cd ab 00 00 00 00 00 00 20 29 f7 42"
              " 00 00 dc 7a 77 40" + " 00" * 52 + " a2")
    live14 = ("f7 81 4d 00 14 00 00 00 00 cd ab 00 00 00 00 00 00 20 29 f7 42"
              " 00 00 dc 7a 77 40 45 11 00 00 45 11 00 00 ed 03 59 3f eb 3f 8c 3f"
              " c4 11 c4 3f 4a 68 11 3c bd 04 04 3f 2f eb c2 41 d6 48 49 42"
              " c0 62 46 3c 00 00 00 00 a6 9b 44 3c 00 00 00 00 c0")
    live15 = ("f7 81 a4 00 15 0a" + " e7 3a 0a 40 e7 3a 0a 40 ef 3a 0a 40 00 00 00 00"
              * 10 + " 6f")
    live16 = ("f7 81 a4 00 16 0a" + " 00 00 00 00 65 97 99 ba 4d 92 b2 ba cf 84 d5 ba"
              * 10 + " 1e")
    check("Live-Frames 0x13..0x16: Länge/CRC (0x13/0x14 CRC neu, 0x15/0x16 Gerät)",
          [len(bytes.fromhex(f)) for f in (live13, live14, live15, live16)]
          == [80, 80, 167, 167]
          and all(parse_frame(bytes.fromhex(f))[0] == fn for f, fn in
                  ((live13, 0x13), (live14, 0x14), (live15, 0x15), (live16, 0x16)))
          and _drx(0x81, 0x14, 0x00, bytes.fromhex(live14)[5:-1]) == live14)
    bus = _FakeBus([("81 04 00 13 02", live13), ("81 04 00 15 02", live15),
                    ("81 04 00 16 02", live16), ("81 04 00 14 02", live14)])
    md = Motor(bus, 0x81)

    def _near(a, b, tol):
        return abs(a - b) <= tol
    e = md.read_err_detail()
    check("LIVE 0x13 = Host-JSON (err_code 0, elec_offset 123.58, err_deg 3.867)",
          e["err_code"] == 0 and e["dev_uuid"] == uuid0 and e["errors"] == []
          and _near(e["elec_offset"], 123.58, 5e-3) and e["phase_order_invert"] == 0
          and _near(e["elec_offset_err_deg"], 3.867, 5e-4)
          and all(e[k] == 0 for k in ERR_DETAIL_FIELDS[5:])
          and e["union"] == {} and e["union_raw"] == bytes(8)
          and CUT_DETAIL_EXTRA_FIELD not in e and e["status"]["ok"]
          and bus.calls[0][2] == [0x02]
          and bus.calls[0][3] == {"timeout": TIMEOUT_DETAIL, "retries": 0})
    ps = md.read_pos_samples()
    check("LIVE 0x15 = Host-JSON (10 x fdb/leso 2.15985, target 2.159847, err 0)",
          len(ps["samples"]) == SAMPLE_COUNT and ps["status"]["ok"]
          and all(_near(s_["fdb_pos"], 2.15985, 5e-6) and _near(s_["leso_pos"], 2.15985, 5e-6)
                  and _near(s_["target_pos"], 2.159847, 5e-7) and s_["leso_err"] == 0
                  for s_ in ps["samples"]))
    ss = md.read_spd_samples()
    check("LIVE 0x16 = Host-JSON (10 x ~0, |v| < 2e-3)",
          len(ss["samples"]) == SAMPLE_COUNT and ss["status"]["ok"]
          and all(s_["fdb_spd"] == 0 and all(abs(v) < 2e-3 for v in s_.values())
                  for s_ in ss["samples"])
          and _near(ss["samples"][0]["leso_in_u"], -0.001629, 1e-6))
    c = md.read_stall_detail()
    want14 = {"encoder_fdb": (4421, 0), "encoder_fdb_last": (4421, 0),
              "pos_fdb_encoder": (0.8477, 5e-5), "spd_fdb_encoder": (1.0957, 5e-5),
              "spd_fdb_ctrl": (1.5318, 5e-5), "id": (0.00887, 5e-6),
              "iq": (0.5157, 5e-5), "board_detect_power_supply_V": (24.365, 5e-4),
              "board_detect_temperature_C": (50.32, 5e-3),
              "pos_loop_pid_err": (0.01211, 5e-6), "step_increment": (0, 0),
              "param_peak_overcurrent_A": (0.012, 1e-7)}
    check("LIVE 0x14 = Host-JSON (12 Felder inkl. param_peak_overcurrent_A)",
          all(_near(c[k], v, t) for k, (v, t) in want14.items())
          and CUT_DETAIL_EXTRA_FIELD == "param_peak_overcurrent_A"
          and c["err_code"] == 0 and c["dev_uuid"] == uuid0
          and _near(c["elec_offset"], 123.58, 5e-3) and c["status"]["ok"]
          and struct.unpack(CUT_DETAIL_FORMAT, bytes.fromhex(live14)[5:75])[-1]
          == c[CUT_DETAIL_EXTRA_FIELD])
    check("LIVE Reihenfolge 0x13/0x15/0x16/0x14, alle Payload [02], retries 0",
          [c_[1] for c_ in bus.calls] == [0x13, 0x15, 0x16, 0x14]
          and all(c_[2] == [0x02] and c_[3]["retries"] == 0 for c_ in bus.calls)
          and not bus.script and list(DETAIL_REQUEST_PAYLOAD) == [0x02])
    # SYNTHETISCH (live nur err_code 0): Union-Dekodierung je Fehlerbit
    base = struct.pack(ERR_DETAIL_FORMAT, 0x100, *(list(uuid0) + [
        123.5, 0, 1.25, 1000, 999, 1.0, 2.0, 3.0, 0.1, 0.2, 24.0, 30.0, 0.5, 7]))
    det = base + struct.pack("<ff", 0.75, 0.0)
    cut = struct.pack(ERR_DETAIL_FORMAT, 0x4, *(list(uuid0) + [
        123.5, 1, 1.25, 10, 20, 1.0, 2.0, 3.0, 0.1, 0.2, 24.0, 30.0, 0.5, 7])) \
        + struct.pack("<ff", 2.5, 0.0)
    pos = bytes([10]) + b"".join(struct.pack("<ffff", k, k + 0.5, k + 1, -k)
                                 for k in range(10))
    spd = bytes([10]) + b"".join(struct.pack("<ffff", k, k, 2 * k, 0.25)
                                 for k in range(10))
    bus = _FakeBus([("81 04 00 13 02", _drx(0x81, 0x13, 0x02, det)),
                    ("81 04 00 14 02", _drx(0x81, 0x14, 0x01, cut)),
                    ("81 04 00 15 02", _drx(0x81, 0x15, 0x00, pos)),
                    ("81 04 00 16 02", _drx(0x81, 0x16, 0x00, spd))])
    md = Motor(bus, 0x81)
    e = md.read_err_detail()
    check("read_err_detail SYNTHETISCH: Schleppfehler 0x100 + Status latched",
          len(det) == DETAIL_LEN == 74 and e["err_code"] == 0x100
          and e["dev_uuid"] == uuid0 and e["phase_order_invert"] == 0
          and abs(e["elec_offset"] - 123.5) < 1e-6 and e["encoder_fdb"] == 1000
          and e["board_detect_power_supply_V"] == 24.0
          and e["board_detect_temperature_C"] == 30.0 and e["step_increment"] == 7
          and e["errors"][0][1] == "key789" and e["union"] == {"tracking_error": None}
          and e["union_raw"] == struct.pack("<ff", 0.75, 0.0)
          and e["status"]["err_latched"] and not e["status"]["stall"])
    c = md.read_stall_detail()
    check("read_stall_detail SYNTHETISCH: Union Spitzenstrom + Zusatzfloat (Host 70 B)",
          c["err_code"] == 4 and c["union"] == {"peak_overcurrent_A": 2.5}
          and c[CUT_DETAIL_EXTRA_FIELD] == 2.5 and c["errors"][0][1] == "key783"
          and c["status"]["stall"]
          and struct.unpack(CUT_DETAIL_FORMAT, cut[:70])[-1] == c[CUT_DETAIL_EXTRA_FIELD])
    ps = md.read_pos_samples()
    check("read_pos_samples SYNTHETISCH: 161 B, Feldzuordnung je Probe",
          len(pos) == SAMPLE_LEN == 161 and len(ps["samples"]) == 10
          and ps["samples"][3] == {"fdb_pos": 3.0, "leso_pos": 3.5,
                                   "target_pos": 4.0, "leso_err": -3.0}
          and ps["status"]["ok"])
    ss = md.read_spd_samples()
    check("read_spd_samples SYNTHETISCH: Felder fdb/leso/target_spd, leso_in_u",
          ss["samples"][9] == {"fdb_spd": 9.0, "leso_spd": 9.0, "target_spd": 18.0,
                               "leso_in_u": 0.25}
          and [c_[1] for c_ in bus.calls] == [0x13, 0x14, 0x15, 0x16]
          and all(c_[2] == [0x02] for c_ in bus.calls) and not bus.script)
    check("Host-Aliase 0x13..0x16 = lesende Methoden",
          Motor.err_detail_data is Motor.read_err_detail
          and Motor.cut_detail_data is Motor.read_stall_detail
          and Motor.pos_data_sample is Motor.read_pos_samples
          and Motor.spd_data_sample is Motor.read_spd_samples)
    bus = _FakeBus([("81 04 00 13 02", _drx(0x81, 0x13, 0x00, det[:66])),
                    ("81 04 00 15 02", _drx(0x81, 0x15, 0x00, bytes([9]) + pos[1:]))])
    check("0x13/0x15 Längen-/Zählbyte-Guard",
          raises(ValueError, Motor(bus, 0x81).read_err_detail)
          and raises(ValueError, Motor(bus, 0x81).read_pos_samples) and not bus.script
          and raises(ValueError, decode_samples, pos[:-1], POS_SAMPLE_FIELDS))
    class _NoFull:
        def __init__(self):
            self.calls = []
        def transact(self, addr, fun, data=(), **kw):
            self.calls.append((addr, fun, list(data), kw))
            return pos
    nf = _NoFull()
    check("ohne transact_full: status None, Daten trotzdem",
          Motor(nf, 0x83).read_pos_samples()["status"] is None and nf.calls[0][2] == [0x02])

    # 8. Update-Pfad (Doku 1.7 + Disassembly, live nie beobachtet)
    bus = _FakeBus([
        ("81 04 00 f0 00", "f7 81 06 00 f0 00 02 02 %02x"
         % crc8_cal(bytes.fromhex("06 00 f0 00 02 02"))),
    ])
    check("query_version sub 0x00 retries=2",
          list(Motor(bus, 0x81).query_version()) == [0, 2, 2]
          and bus.calls[0][3].get("retries") == UPDATE_RETRIES)
    check("retries=2 -> 3 Versuche wie Binary", UPDATE_RETRIES + 1 == 3)
    # F0-Sender: ohne confirm KEIN Bus-Zugriff
    for addr in MOTOR_ADDRS:
        mu = Motor(nobus, addr)
        for name, fn in [("request_update", lambda c: mu.request_update(**c)),
                         ("send_fw_len", lambda c: mu.send_fw_len(6, **c)),
                         ("start_app", lambda c: mu.start_app(**c)),
                         ("update_end", lambda c: mu.update_end(**c))]:
            check("%s 0x%02x ohne confirm gesperrt" % (name, addr),
                  raises(PermissionError, fn, {})
                  and raises(PermissionError, fn, {"confirm": "yes"})
                  and raises(PermissionError, fn, {"confirm": 1}))
    check("send_fw_len Längen-Guard vor Sperre",
          raises(ValueError, Motor(nobus, 0x83).send_fw_len, -1))
    check("F0-Sperren senden nichts", nobus.calls == [])

    def _syn(addr, data_hex):
        # SYNTHETISCHE Antwort (F0 live nie beobachtet), CRC neu berechnet
        body = bytes([len(bytes.fromhex(data_hex)) + 3, 0x00, 0xF0]) \
            + bytes.fromhex(data_hex)
        return "f7 %02x %s %02x" % (addr, body.hex(" "), crc8_cal(body))
    # 18.10.2026: request_update/send_fw_len HART gesperrt (auch confirm=True),
    # start_app (Sub 02 = App starten, Alias update_end) mit confirm=True
    bus = _FakeBus([
        ("83 04 00 f0 02", _syn(0x83, "00")),
        ("83 04 00 f0 02", _syn(0x83, "00")),
    ])
    mu = Motor(bus, 0x83)
    check("request_update/send_fw_len auch mit confirm=True gesperrt",
          raises(PermissionError, mu.request_update, confirm=True)
          and raises(PermissionError, mu.send_fw_len, 6, confirm=True)
          and bus.calls == [])
    mu.start_app(confirm=True)
    mu.update_end(confirm=True)
    check("start_app/update_end (Sub 02) mit confirm=True senden korrekt",
          not bus.script and [c[2] for c in bus.calls] == [[2], [2]]
          and all(c[3].get("retries") == UPDATE_RETRIES for c in bus.calls)
          and all(c[3].get("timeout") == START_APP_TIMEOUT for c in bus.calls)
          and UPDATE_END_TIMEOUT == START_APP_TIMEOUT)
    # Sendeschutz: F0 nur [00]/[02]/[03]; Builder-Payloads request/len/
    # Daten/erase gesperrt (an Motor UND CFS-Adressen)
    check("Sendeschutz: version/sector/start-app frei, request/len/erase/Daten gesperrt",
          all(check_tx_frame(a, FUNC_FW_UPDATE, p) == p
              for a in (0x01, 0x83) for p in (build_version_payload(),
                                              build_sector_payload(),
                                              build_start_app_payload()))
          and all(raises(TxGuardError, check_tx_frame, a, FUNC_FW_UPDATE, p)
                  for a in (0x01, 0x83)
                  for p in (build_update_request_payload(),
                            build_fw_len_payload(6),
                            build_erase_payload(DEV_TYPE_CFS),
                            list(bytes(128)), [0x41] * 5)))
    check("payload version", build_version_payload() == [0x00])
    check("payload sector", build_sector_payload() == [0x03])
    # 17.10.2026: Erase nur CFS-Typ 1/10, Motorziele -> ValueError
    check("Sub 06 = DB-Page-Erase (Alias SUB_ERASE_SECTOR)",
          SUB_ERASE_DB_PAGE == SUB_ERASE_SECTOR == 0x06)
    check("payload erase CFS-Typ 1/10", build_erase_payload(DEV_TYPE_CFS) == [0x06]
          and build_erase_payload(dev_type=DEV_TYPE_CFS_PRO) == [0x06])
    check("erase für Motoren gesperrt (Default + Typ 2, bool, None)",
          all(raises(ValueError, build_erase_payload, *a)
              for a in ((), (DEV_TYPE_MOTOR,), (2,), (True,), (None,), (3,))))
    try:
        build_erase_payload()
        msg_e = ""
    except ValueError as e:
        msg_e = str(e)
    check("erase-Fehlermeldung mit Beleg", "0x15378" in msg_e and "0xF0" in msg_e)
    # 17.10.2026: Broadcast-Schutz zentral in serial485 (Motor-FW boot/reboot
    # per 0xFF erreichbar); 0xFF 0x0B [01] (CFS-Loader->App) bleibt erlaubt
    check("serial485-Sendeschutz: FF 0B [00]/FF 01 gesperrt, FF 0B [01] frei",
          raises(TxGuardError, check_tx_frame, 0xFF, FUNC_BOOT, [0x00])
          and raises(TxGuardError, check_tx_frame, 0xFF, FUNC_REBOOT, [0x00])
          and check_tx_frame(0xFF, FUNC_BOOT, [0x01]) == [0x01]
          and check_tx_frame(0xFF, FUNC_ADDR, [0x01]) == [0x01])
    check("Motor.boot/reboot bleiben Stubs (senden nichts)",
          raises(NotImplementedError, Motor(nobus, 0x81).boot)
          and raises(NotImplementedError, Motor(nobus, 0x81).reboot)
          and nobus.calls == [])
    check("payload request", build_update_request_payload() == [0x01])
    check("payload start-app (Alias update-end)",
          build_start_app_payload() == [0x02] == build_update_end_payload()
          and SUB_START_APP == SUB_UPDATE_END == 0x02)
    check("fw_len LE", build_fw_len_payload(0x1E240) == [0x40, 0xE2, 0x01, 0x00])
    for bad in [-1, 2 ** 32, True, 1.0]:
        check("fw_len guard %r" % (bad,), raises(ValueError, build_fw_len_payload, bad))
    check("checksum ~sum", fw_checksum([0x01, 0x02, 0x03]) == 0xF9)
    check("checksum leer=0xff", fw_checksum([]) == 0xFF)
    # 18.10.2026: Sektor-Byte signed wie Stock (0xE0 -> 128, Chunk 0 -> Fehler)
    check("Sektor-Byte 0xE0 -> 128 B (CFS-Bootloader)",
          sector_chunk_size(SECTOR_BYTE_CFS) == 128
          and sector_chunk_size(-32) == 128)
    check("Sektor-Byte 0xFF -> 4, 0x81 -> 252, 0xFE -> 8, 0xC1 -> 252",
          [sector_chunk_size(b) for b in (0xFF, 0x81, 0xFE, 0xC1)]
          == [4, 252, 8, 252])
    check("Sektor-Byte: alle Chunks <= 252 (len <= 0xFC)",
          all(sector_chunk_size(b) <= 252 for b in range(0x81, 0x100)
              if b != 0xC0))
    for bad in [0x00, 0x01, 0x7F, 0x80, 0xC0, 0, 4, 127, 256, -129, True,
                2.5, 4.0, None]:
        check("Sektor-Byte guard %r" % (bad,),
              raises(ValueError, sector_chunk_size, bad)
              and raises(ValueError, chunk_firmware, b"AB", bad))
    check("chunks 0xE0: 300 B -> 128/128/44",
          [len(c) for c in chunk_firmware(bytes(300), 0xE0)] == [128, 128, 44])
    check("chunks 0xFF (4 B)",
          chunk_firmware(b"ABCDEFGHIJ", 0xFF) == [b"ABCD", b"EFGH", b"IJ"])
    for fw in [b"", []]:
        check("leere FW guard %r" % (fw,), raises(ValueError, update_sequence, fw, 0xE0))
    seq = update_sequence(b"ABCDEF", 0xFF)
    check("seq reihenfolge (ohne erase)",
          [s[0] for s in seq] == ["get-version", "get-sector-size",
                                  "request-update", "send-fw-len",
                                  "send-fw-chunk-0", "send-fw-chunk-1",
                                  "start-app"])
    check("seq len-payload", seq[3][2] == [0x06, 0x00, 0x00, 0x00])
    check("seq ende sub 0x02 = App starten", seq[-1][2] == [SUB_START_APP])
    check("Bootloader-Antwortcodes 0x75/0x20/0x1F/0x21",
          (BL_ACK_OK, BL_ACK_DONE, BL_ACK_CRC_ERR, BL_ACK_FLASH_ERR)
          == (0x75, 0x20, 0x1F, 0x21))
    blocked_steps = []
    for label, fun, payload in update_sequence(bytes(300), 0xE0, erase=True,
                                               dev_type=DEV_TYPE_CFS):
        if raises(TxGuardError, check_tx_frame, 0x01, fun, payload):
            blocked_steps.append(label.split(" ")[0].rstrip("-0123456789"))
    check("update_sequence nicht sendefähig: request/len/erase/Daten gesperrt",
          blocked_steps == ["erase-db-page", "request-update", "send-fw-len",
                            "send-fw-chunk", "send-fw-chunk", "send-fw-chunk"])
    check("seq fun-codes", all(s[1] == FUNC_FW_UPDATE for s in seq))
    check("seq erase=True für Motor -> ValueError (Default, Typ 2)",
          raises(ValueError, update_sequence, b"ABCDEF", 0xFF, erase=True)
          and raises(ValueError, update_sequence, b"ABCDEF", 0xFF, True,
                     DEV_TYPE_MOTOR)
          and raises(ValueError, update_sequence, b"ABCDEF", 0xFF, erase=True,
                     dev_type=True))
    check("seq ohne erase: dev_type ohne Wirkung",
          update_sequence(b"ABCDEF", 0xFF, dev_type=DEV_TYPE_MOTOR) == seq)
    seq_e = update_sequence(b"ABCDEF", 0xFF, erase=True, dev_type=DEV_TYPE_CFS)
    check("seq erase nur auf Wunsch und nur CFS-Typ 1/10",
          seq_e[2] == ("erase-db-page (CFS-Typ 1)",
                       FUNC_FW_UPDATE, [SUB_ERASE_DB_PAGE])
          and len(seq_e) == len(seq) + 1
          and all(s[2] != [SUB_ERASE_SECTOR] for s in seq))
    rt_ok = True
    for label, fun, payload in seq + update_sequence(bytes(range(256)), 0xE0):
        rfun, rdata = parse_frame(build_frame(0x83, fun, payload))
        rt_ok &= (rfun == fun and list(rdata) == list(payload))
    check("seq roundtrip", rt_ok)

    print("SELBSTTEST: %s" % ("ok" if fails == 0 else "%d FEHLER" % fails))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
