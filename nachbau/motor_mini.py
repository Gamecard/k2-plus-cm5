#!/usr/bin/env python3
"""motor_mini.py – Python-Nachbau von motor_control (K2 Closed-loop-Motoren, Kernfunktionen).

Ersetzt: motor_control_wrapper.cpython-39.so (armhf, proprietär).
Eigenständig (importiert motor.py NICHT); gleiche Regeln wie motor.py –
Details, Param-Namen-Tabelle und Update-Pfad dort.

LIVE-STAND 15.10.2026 (Live-Logs, lokal, nicht im Repo; s. motor.py):
- LESEND (senden erlaubt): read_sys_param (0x06 [02, id]),
  check_protection (0x0C [0b] -> 8 B), get_stall_mode (0x11 [04]),
  read_485_addr (0x0E [02]), read_uuid (0x12 [02] -> 8 B),
  query_motor_version (0x0F [00] -> 12 B) und read_variable (0x08 [idx],
  NUR idx 1..12 -> 4 B) – beide live 16.10.2026 an Motor X
  (Live-Abfragen 16.10.2026)
- set_mode (0x11 [01]/[02]/[03], Antwort = Echo) – sendet, live belegt
- write_sys_param: 0x06 [01, id] + <f – sendet NUR mit confirm=True
  (sonst PermissionError); verändert Motorregelparameter, FLÜCHTIG bis
  Kaltstart. Nur IDs aus MINI_WRITABLE_IDS (= Stock-[motor_control]-IDs);
  alle anderen -> PermissionError, Verweis auf motor.py (Namen-Tabelle +
  allow_locked). Wert-Plausibilität wie motor.py (1e-50 -> ValueError).
- update_sys_params: 0x06 [03] – NUR mit confirm=True (wie motor.py)
- Reine Builder: build_sys_param_write_payload, build_sys_param_update_payload
- read_position_feedback / read_speed_feedback / read_current_feedback:
  0x08 idx 5 / 6 / 7+8 (pos_fdb/spd_fdb/id_fdb/iq_fdb, REA-belegt 16.10.;
  pos_fdb = rad single-turn, live 16.10. belegt; spd/id/iq Einheit offen)
- read_position_counts: 0x08 idx 11 = 0..3199 Counts/Umdrehung (live 16.10.,
  Name pos_counts abgeleitet); counts_to_mm (80 Counts/mm bei rot.dist. 40).
  Single-Turn, Wrap alle 40 mm -> absolute Position nur Klipper-Schrittzähler
- read_encoder: GESPERRT (0x06-Altfalle) -> read_position_feedback (idx 5)
- ERGÄNZT 16.10.2026 (Motor-FW-Analyse, Motor-FW WCH RISC-V,
  statisch + Live-Gegenproben): Namen/Einheiten idx 1..12 (VARIABLE_NAMES,
  VARIABLE_UNITS; Details/Belegstatus idx 0..18 in motor.VARIABLE_TABLE),
  read_reference_values (idx 1-4), read_phase_currents (idx 9/10 -> Ia, Ib;
  KORREKTUR 17.10.2026: idx 10 = +Ib bei phase_order_invert 0 (live),
  -Ib nur bei != 0 – Details motor.ib_from_idx10),
  read_dir_input (idx 12, DIR-Eingang live bestätigt); spd_fdb = rad/s;
  check_protection -> decode_protection (Byte 0-3 Fehler, 4-7 Warnung,
  u32 LE), check_protection_raw -> 8 B; decode_motor_status (STATUS-
  Bitfeld bit0 Stall, bit1 Fehler, bit2 Warnung). 0x13..0x16 und
  read_variable_raw_fw (idx 0/13..18) NUR in motor.py.
- WARNUNG: Creality-Host MOTOR_GET DATA=0/13 -> Klipper-SHUTDOWN (live 16.10.,
  2x reproduziert; Ursache belegt: get_int -> None -> parse_num_to_byte
  TypeError VOR dem Senden) -> read_variable lehnt Index außerhalb 1..12 ab.
- query_version: Update-Pfad (0xF0/00, Decompile; live nie beobachtet) –
  FREI, reine "get version"-Abfrage (erster Schritt vor request-update).
  Die zustandsändernden F0-Sender (request_update/send_fw_len/update_end)
  gibt es hier bewusst NICHT – nur in motor.py, dort mit confirm-Sperre.

Adressen: X/Y/Z/Z1 = 0x81-0x84 nur über serial_485 (ttyS5). Der Extruder ist
NICHT 0x85 auf dem RS485 (antwortet nie, Live-Log 05.10.2026),
sondern nur transparent über die (vermutlich) Nozzle-MCU unter 0x81
(Live-Log) – nicht in diesem Modul.

Retries/Timeouts wie Stock: alle Motor-Kommandos retries=0 (1 Sendung);
Timeout 0x06/0x12 = 1.0 s, 0x0C/0x0E = 0.05 s, 0x11/0x0F/0x08 unbelegt (1.0 s).
"""

import math
import struct

from serial485 import Serial485  # noqa: F401 (für echten Betrieb)

FUNC_SYS_PARAM = 0x06
FUNC_READ_ENCODER = FUNC_SYS_PARAM  # Alt-Name, nur Kompatibilität – nicht senden!
FUNC_PROTECTION = 0x0C
FUNC_ADDR = 0x0E
FUNC_SET_MODE = 0x11
FUNC_UUID = 0x12
FUNC_FW_UPDATE = 0xF0      # Update-Pfad, Sub-Codes aus Decompile
FUNC_VARIABLE = 0x08       # live 16.10.: [idx] -> 4 B LE
FUNC_VERSION = 0x0F        # live 16.10.: [00] -> 12 B
SUB_VERSION_GET = 0x00
VERSION_FIELDS = (("fireware_ver", 4), ("cmd_system_ver", 2),
                  ("protection_ver", 2), ("flash_param_ver", 2),
                  ("logger_ver", 2))  # 12 B, Namen = Wrapper-Strings
VARIABLE_INDEX_MIN, VARIABLE_INDEX_MAX = 1, 12  # 0/13: Host-Crash (s. oben)
# Feste Host-Typkette (REA 16.10., motor_get Py-Z. 1987/1989): 1..10 '<f',
# 11/12 '<I' (UNSIGNED; 13..16 '<I', 17/18 '<f' per G-Code unerreichbar).
VARIABLE_TYPES = dict([(i, "float") for i in range(1, 11)]
                      + [(11, "int"), (12, "int")])
# Indexnamen 1..12: 5..8 Host-Dict NO_ERR_CODE_TO_GET (REA belegt), übrige
# = eigene Namen nach Motor-FW-Deutung (Motor-FW-Analyse 16.10., alle 1..12
# FW + live belegt; Belegstatus/Deutung je Index: motor.VARIABLE_TABLE).
# 11 = Schrittpuls-Zähler 0..3199/Umdrehung, 12 = DIR-Eingangspegel.
VARIABLE_NAMES = {1: "pos_ref", 2: "spd_ref", 3: "id_ref", 4: "iq_ref",
                  5: "pos_fdb", 6: "spd_fdb", 7: "id_fdb", 8: "iq_fdb",
                  9: "ia", 10: "ib", 11: "pos_counts", 12: "dir_input"}
# idx 10 (MOTOR-RESTE §5, B): FW = Ib x (-1 NUR falls phase_order_invert
# != 0; @0xbb30 `lh a5,0x5a(s0)`, beqz -> +1,0 @0x19c98, sonst -1,0
# @0x19d14). obj+0x5a = Sys-Param ID 10, live 0 -> idx 10 = +Ib.
# Bis 17.10. hieß idx 10 "minus_ib" (fälschlich pauschal -Ib).
IDX_IB = 10
IDX_MINUS_IB = IDX_IB          # Alt-Name, nur Kompatibilität
PHASE_ORDER_INVERT_LIVE = 0
# Einheiten (A: Gain*R = 1 Ohm vermutet, s. motor.py)
VARIABLE_UNITS = {1: "rad", 2: "rad/s", 3: "A", 4: "A", 5: "rad", 6: "rad/s",
                  7: "A", 8: "A", 9: "A", 10: "A", 11: "counts", 12: "0/1"}
# Skalierung live 16.10. (Messfahrt X): pos_fdb [rad] = idx11 * 2*pi/3200
POS_COUNTS_PER_REV = 3200   # 200 Vollschritte x 16 Microsteps
SUB_SYS_WRITE = 0x01
SUB_SYS_READ = 0x02
SUB_SYS_UPDATE = 0x03
SUB_PROT_CHECK = 0x0B      # einziger gesendeter 0x0C-Sub; 01..05/07/09 nie
                           # (zustandsändernd bzw. Seiteneffekt, s. motor.py)
SUB_PROT_CLEAR = 0x05      # löscht Codes, live nie beantwortet -> nie senden
SUB_STALL_GET = 0x04
STALL_MODES = (0x01, 0x02, 0x03)
SUB_GET_VERSION = 0x00
SUB_GET_SECTOR = 0x03
SUB_REQUEST_UPDATE = 0x01
SYS_PARAM_COUNT = 227      # live IDs 0..226

MOTOR_RETRIES = 0          # live "retries = 0" bei allen Motor-Sendungen
TIMEOUT_SYS_PARAM = 1.0
TIMEOUT_PROTECTION = 0.05
TIMEOUT_ADDR = 0.05
TIMEOUT_UUID = 1.0
TIMEOUT_STALL = 1.0        # unbelegt (nie Timeout beobachtet)
TIMEOUT_VERSION = 1.0      # 0x0F unbelegt (live nur 1 Antwort, kein Timeout)
TIMEOUT_VARIABLE = 1.0     # 0x08 unbelegt
# Update-Pfad: Binary bricht nach 3 Fehlversuchen ab (mcu_util_485
# `cmp r6,#3` @0x1562c), serial485 macht retries+1 Versuche -> 2.
UPDATE_RETRIES = 2

MOTOR_ADDRS = (0x81, 0x82, 0x83, 0x84)  # X/Y/Z/Z1 über serial_485
SYS_UPDATE_OK = 0x01       # Antwort auf [03] (Live-Log)
FLOAT32_MAX = 3.4028234663852886e38

# Schreibbare IDs (motor_mini hat keine Namen-/Configurable-Tabelle):
# genau die IDs, die die Stock-K2-Plus-Config [motor_control] nach jedem
# Kaltstart schreibt (= motor.SYS_PARAM_STOCK_CONFIGURED, Teilmenge von
# motor.SYS_PARAM_CONFIGURABLE). Live geschrieben beobachtet: 70 (Pos-Kp,
# Live-Logs 05.10./12.10.2026), 86, 87, 188 (Schleppfehler-
# Zeit). 226 nur X/Y, Motor-FW kennt sie nicht (0-B-Antwort). Weitere IDs
# (auch "not configurable" wie 1 param_boot_key) -> motor.py mit
# confirm=True (+ allow_locked=True). Abgleich im motor.py-Selbsttest.
MINI_WRITABLE_IDS = frozenset((70, 86, 87, 102, 103, 116, 118, 119, 120, 121,
                               187, 188, 226))


def decode_sys_param_float(data):
    """4 Bytes 0x06-Antwort -> Float (`<f`), ohne Bus-Zugriff.
    Beleg: 00 00 96 43 -> 300.0, 00 00 c8 42 -> 100.0 (ID 70)."""
    raw = bytes(data)
    if len(raw) != 4:
        raise ValueError("0x06-Payload muss 4 Bytes haben: %r" % (raw,))
    return struct.unpack("<f", raw)[0]


def _check_param_id(param_id):
    if isinstance(param_id, bool) or not isinstance(param_id, int) \
            or not 0 <= param_id < SYS_PARAM_COUNT:
        raise ValueError("Param-ID muss int 0..226 sein: %r" % (param_id,))


def build_sys_param_write_payload(param_id, value):
    """[01, id] + <f LE – REINER BUILDER, sendet nichts.
    Live-Log 05.10.2026: `81 09 00 06 01 46 00 00 96 43`
    (ID 70 = 300.0), Vollframe f7 81 09 00 06 01 46 00 00 96 43 8f.
    Wert: endlich, |Wert| <= float32-Max, != 0 darf nicht auf 0.0 runden
    (struct schreibt 1e-50 sonst still als 0.0) -> sonst ValueError."""
    _check_param_id(param_id)
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value) or abs(value) > FLOAT32_MAX:
        raise ValueError("Sys-Param-Wert muss endliche float32-Zahl sein: %r"
                         % (value,))
    try:
        raw = struct.pack("<f", value)
    except OverflowError:
        raise ValueError("Sys-Param-Wert passt nicht in float32: %r" % (value,))
    if value != 0 and struct.unpack("<f", raw)[0] == 0.0:
        raise ValueError("Sys-Param-Wert %r rundet in float32 auf 0.0" % (value,))
    return [SUB_SYS_WRITE, param_id] + list(raw)


def build_sys_param_update_payload():
    """[03] "Updata param" -> Antwort [01] (Live-Log). Builder."""
    return [SUB_SYS_UPDATE]


def decode_motor_version(data):
    """12 B 0x0F-Antwort -> dict (fireware_ver "a.b.c.d", Rest "major.minor",
    minor dezimal; 'raw'). Live: 01 09 07 51 01 03 01 03 02 4b 01 00 ->
    1.9.7.81 / 1.3 / 1.3 / 2.75 / 1.0."""
    raw = bytes(data)
    if len(raw) != 12:
        raise ValueError("0x0F: 12 Bytes erwartet: %r" % (raw,))
    out, pos = {}, 0
    for name, n in VERSION_FIELDS:
        out[name] = ".".join(str(b) for b in raw[pos:pos + n])
        pos += n
    out["raw"] = raw
    return out


def _check_variable_index(idx):
    if isinstance(idx, bool) or not isinstance(idx, int) \
            or not VARIABLE_INDEX_MIN <= idx <= VARIABLE_INDEX_MAX:
        raise ValueError("0x08-Index muss int 1..12 sein: %r – MOTOR_GET "
                         "DATA=0/13 lässt den Creality-Host in Klipper-SHUTDOWN "
                         "laufen; außerhalb 1..12 unbelegt" % (idx,))


def decode_protection(data):
    """8 B 0x0C [0b] -> {'err_code', 'warning_code', 'errors', 'warnings',
    'ok', 'raw'}: Byte 0-3 Fehler u32 LE (Bit n -> key781+n), Byte 4-7
    Warnung u32 LE (Bit n -> key795+n); MOTOR-FW + Host-Parser belegt.
    'errors'/'warnings' = Bitnummern-Listen."""
    raw = bytes(data)
    if len(raw) != 8:
        raise ValueError("0x0C: 8 Bytes erwartet: %r" % (raw,))
    err, warn = struct.unpack("<II", raw)
    return {"err_code": err, "warning_code": warn,
            "errors": [b for b in range(32) if err >> b & 1],
            "warnings": [b for b in range(32) if warn >> b & 1],
            "ok": err == 0 and warn == 0, "raw": raw}


def decode_motor_status(status):
    """STATUS-Byte der Motorantwort -> {'stall' (bit0), 'err_latched' (bit1),
    'warn_latched' (bit2), 'other', 'ok'} (MOTOR-FW, Builder @0xdd6e)."""
    if isinstance(status, bool) or not isinstance(status, int) \
            or not 0 <= status <= 0xFF:
        raise ValueError("STATUS muss int 0..255 sein: %r" % (status,))
    return {"stall": bool(status & 1), "err_latched": bool(status & 2),
            "warn_latched": bool(status & 4), "other": status & 0xF8,
            "ok": status == 0}


def counts_to_mm(counts, rotation_distance=40.0, counts_per_rev=POS_COUNTS_PER_REV):
    """Counts (idx 11, oder Count-Differenz) -> mm innerhalb einer Umdrehung.
    Live 16.10.: 800 Counts = 10 mm. Single-Turn (Wrap alle 40 mm)."""
    return counts * float(rotation_distance) / counts_per_rev


def decode_variable(idx, data):
    """4 B 0x08-Antwort -> {'index','name','unit','raw','float' (<f),'int'
    (<I wie Host),'type' (feste Host-Kette),'value'}. Namen/Einheiten s.
    VARIABLE_NAMES/VARIABLE_UNITS (FW-Deutung 16.10.)."""
    _check_variable_index(idx)
    raw = bytes(data)
    if len(raw) != 4:
        raise ValueError("0x08 idx %d: 4 Bytes erwartet: %r" % (idx, raw))
    f, i = struct.unpack("<f", raw)[0], struct.unpack("<I", raw)[0]
    typ = VARIABLE_TYPES[idx]
    return {"index": idx, "name": VARIABLE_NAMES.get(idx),
            "unit": VARIABLE_UNITS.get(idx), "raw": raw,
            "float": f, "int": i, "type": typ,
            "value": f if typ == "float" else i}


class MotorMini:
    """Closed-loop-Motor (Kernfunktionen)."""

    def __init__(self, bus, addr):
        if addr not in MOTOR_ADDRS:
            raise ValueError("unbekannte Motor-Adresse: 0x%02x" % addr)
        self.bus = bus
        self.addr = addr

    def _tx(self, fun, payload, timeout):
        return bytes(self.bus.transact(self.addr, fun, payload, timeout=timeout,
                                       retries=MOTOR_RETRIES))

    def read_sys_param(self, param_id):
        """0x06 [02, id] -> float; 0-Byte-Antwort -> None (ID unbekannt,
        live ID 226, Live-Log); andere Länge -> ValueError (live
        ID 10 mit 2 B, Format unbelegt). Live-Log: ID 9 -> 123.58."""
        _check_param_id(param_id)
        raw = self._tx(FUNC_SYS_PARAM, [SUB_SYS_READ, param_id], TIMEOUT_SYS_PARAM)
        if not raw:
            return None
        if len(raw) != 4:
            raise ValueError("0x06 ID %d: %d-Byte-Antwort %r (Format unbelegt)"
                             % (param_id, len(raw), raw))
        return decode_sys_param_float(raw)

    def write_sys_param(self, param_id, value, confirm=False):
        """0x06 SCHREIBEN – nur mit confirm=True, sonst PermissionError ohne
        Senden. Verändert Motorregelparameter (flüchtig bis Kaltstart). Nur
        IDs aus MINI_WRITABLE_IDS, sonst PermissionError (-> motor.py).
        Live-Echo im Log vom 12.10.2026. Echo-Fehler -> RuntimeError."""
        payload = build_sys_param_write_payload(param_id, value)
        if confirm is not True:
            raise PermissionError("write_sys_param gesperrt: verändert Motorregel"
                                  "parameter – nur mit confirm=True")
        if param_id not in MINI_WRITABLE_IDS:
            raise PermissionError(
                "write_sys_param(ID %d) in motor_mini gesperrt: nur Stock-"
                "konfigurierte IDs %s; andere IDs über motor.py (Namen-Tabelle, "
                "allow_locked)" % (param_id, sorted(MINI_WRITABLE_IDS)))
        echo = self._tx(FUNC_SYS_PARAM, payload, TIMEOUT_SYS_PARAM)
        if echo != bytes(payload[2:]):
            raise RuntimeError("0x06 Schreib-Echo passt nicht: %s" % echo.hex())
        return decode_sys_param_float(echo)

    def update_sys_params(self, confirm=False):
        """0x06 [03] "Updata param" – nur mit confirm=True, sonst
        PermissionError ohne Senden. Stock: einmal je Motor am Ende der
        Enumeration (Live-Log: Antwort `f7 81 04 00 06 01 21`); Wirkung
        unbelegt (live NICHT dauerhaft). Antwort != [01] -> RuntimeError."""
        if confirm is not True:
            raise PermissionError("update_sys_params gesperrt: nur mit confirm=True")
        ans = self._tx(FUNC_SYS_PARAM, build_sys_param_update_payload(),
                       TIMEOUT_SYS_PARAM)
        if ans != bytes([SYS_UPDATE_OK]):
            raise RuntimeError("0x06 [03]: Antwort %s statt 01" % ans.hex())
        return True

    def check_protection_raw(self):
        """0x0C [0b] -> 8 B Schutzstatus (live immer 8×00; Live-Log).
        [05] wird nie gesendet (nie beantwortet). FW-Seiteneffekt bei
        stall_mode 2: Notfall-Pin wird nach der Antwort freigegeben."""
        ans = self._tx(FUNC_PROTECTION, [SUB_PROT_CHECK], TIMEOUT_PROTECTION)
        if len(ans) != 8:
            raise ValueError("0x0C: 8 Bytes erwartet: %r" % (ans,))
        return ans

    def check_protection(self):
        """0x0C [0b] -> decode_protection (live 8×00 -> ok=True)."""
        return decode_protection(self.check_protection_raw())

    def set_mode(self, mode, *extra):
        """Stall-Mode setzen (0x11 [01]/[02]/[03]), Antwort = Echo.
        Live-Logs (u. a. 12.10.2026).
        Genau 1 Byte – das frühere "[02 00]" war die Antwort auf [04]."""
        if extra or isinstance(mode, bool) or not isinstance(mode, int) \
                or mode not in STALL_MODES:
            raise ValueError("Stall-Mode muss genau ein Byte 1/2/3 sein: %r"
                             % ((mode,) + extra,))
        echo = self._tx(FUNC_SET_MODE, [mode], TIMEOUT_STALL)
        if echo != bytes([mode]):
            raise RuntimeError("0x11 Echo passt nicht: %s" % echo.hex())
        return mode

    def get_stall_mode(self):
        """0x11 [04] -> [mode, 00] (Live-Log 12.10.2026)."""
        ans = self._tx(FUNC_SET_MODE, [SUB_STALL_GET], TIMEOUT_STALL)
        if len(ans) != 2:
            raise ValueError("0x11 [04]: 2 Bytes erwartet: %r" % (ans,))
        return ans[0]

    def read_485_addr(self):
        """0x0E [02] -> eigene Adresse (Live-Log)."""
        ans = self._tx(FUNC_ADDR, [0x02], TIMEOUT_ADDR)
        if len(ans) != 1:
            raise ValueError("0x0E: 1 Byte erwartet: %r" % (ans,))
        return ans[0]

    def read_uuid(self):
        """0x12 [02] -> 8 B UUID, Präfix cd ab (Live-Log)."""
        ans = self._tx(FUNC_UUID, [0x02], TIMEOUT_UUID)
        if len(ans) != 8:
            raise ValueError("0x12: 8 Bytes erwartet: %r" % (ans,))
        return ans

    dev_uuid = read_uuid

    def query_motor_version(self):
        """0x0F [00] -> dict (MOTOR_VERSION DATA=0; live 16.10. Motor X
        `81 04 00 0f 00` -> fw 1.9.7.81 ...). retries=0, Timeout unbelegt."""
        return decode_motor_version(self._tx(FUNC_VERSION, [SUB_VERSION_GET],
                                             TIMEOUT_VERSION))

    motor_version = query_motor_version

    def read_variable(self, idx):
        """0x08 [idx] -> dict (MOTOR_GET DATA=idx), NUR idx 1..12, sonst
        ValueError ohne Senden (Host-Crash bei 0/13). Live-Scan 16.10. Motor X
        (Standby): 1-8 = 0, 9 = 0.0016113, 10 = 0.0008057, 11 = 1106, 12 = 0."""
        _check_variable_index(idx)
        return decode_variable(idx, self._tx(FUNC_VARIABLE, [idx],
                                             TIMEOUT_VARIABLE))

    def read_position_feedback(self):
        """pos_fdb (0x08 idx 5) -> float [rad] = idx11*2pi/3200, single-turn
        (live 16.10.). Mit Counts: motor.py read_position_feedback(with_counts)."""
        return self.read_variable(5)["value"]

    def read_position_counts(self):
        """0x08 idx 11 (pos_counts, Name abgeleitet) -> int 0..3199/Umdrehung."""
        return self.read_variable(11)["value"]

    def read_speed_feedback(self):
        """spd_fdb (0x08 idx 6) -> float [rad/s] (FW + live 16.10. belegt)."""
        return self.read_variable(6)["value"]

    def read_current_feedback(self):
        """id_fdb/iq_fdb (idx 7, 8) -> {'id','iq','abs'=hypot} wie Host-Stats."""
        i_d, i_q = self.read_variable(7)["value"], self.read_variable(8)["value"]
        return {"id": i_d, "iq": i_q, "abs": math.hypot(i_d, i_q)}

    def read_reference_values(self):
        """Sollwerte idx 1..4 -> {'pos_ref' rad, 'spd_ref' rad/s, 'id_ref' A,
        'iq_ref' A} (4 Frames)."""
        return dict((VARIABLE_NAMES[i], self.read_variable(i)["value"])
                    for i in (1, 2, 3, 4))

    def read_phase_currents(self, phase_order_invert=PHASE_ORDER_INVERT_LIVE):
        """idx 9/10 -> {'ia', 'ib', 'ib_fw', 'minus_ib', 'phase_order_invert'}
        [A]. KORREKTUR 17.10.2026: idx 10 = Ib x (-1 NUR falls
        phase_order_invert != 0) -> bei 0 (live, Default) ist 'ib' = idx 10.
        phase_order_invert = Sys-Param ID 10 (int; bool/sonst -> ValueError
        ohne Senden). 'ib_fw' = Rohwert idx 10, 'minus_ib' = -ib (Alt-
        Schlüssel; bis 17.10. fälschlich = Rohwert). Wie motor.py."""
        if isinstance(phase_order_invert, bool) or not isinstance(phase_order_invert, int):
            raise ValueError("phase_order_invert muss int sein (live 0): %r"
                             % (phase_order_invert,))
        ia, ib_fw = self.read_variable(9)["value"], self.read_variable(IDX_IB)["value"]
        ib = -ib_fw if phase_order_invert != 0 else ib_fw
        return {"ia": ia, "ib": ib, "ib_fw": ib_fw, "minus_ib": -ib,
                "phase_order_invert": phase_order_invert}

    def read_dir_input(self):
        """idx 12 -> Pegel DIR-Eingang 0/1 (live 16.10.: -X 0, +X 1)."""
        return self.read_variable(12)["value"]

    def read_encoder(self, *args, **kwargs):
        """GESPERRT – sendet KEINEN Frame (0x06-Altfalle: 0x06 = Sys-Param).
        "Encoder" = 0x08 idx 5 (pos_fdb rad) / idx 11 (Counts), single-turn
        -> read_position_feedback / read_position_counts."""
        raise NotImplementedError(
            "read_encoder gesperrt (0x06 = Sys-Param): Positionsrückmeldung = "
            "read_position_feedback()/read_position_counts() (0x08 idx 5/11, "
            "single-turn 3200/Umdrehung)")

    def query_version(self):
        """Version über Update-Pfad abfragen (Func 0xf0/Sub 0x00). Rohdaten.
        FREI: reine Abfrage ("get version", 1.7), ändert keinen Zustand."""
        return self.bus.transact(self.addr, FUNC_FW_UPDATE, [SUB_GET_VERSION],
                                 retries=UPDATE_RETRIES)


# --- Selbsttest mit Live-Frames (keine Hardware nötig) ---
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

    from serial485 import parse_frame, build_frame, crc8_cal as _crc8

    class _Bus:
        """Prüft TX gegen Log-Bytes (data_send-Form ohne f7/CRC) und spielt
        Log-Antworten (mit Geräte-CRC) ab."""

        def __init__(self, script):
            self.script = list(script)
            self.calls = []

        def transact(self, addr, fun, data=(), **kw):
            self.calls.append((addr, fun, list(data), kw))
            assert self.script, "unerwarteter Sendeversuch"
            want_tx, rx = self.script.pop(0)
            frame, want = build_frame(addr, fun, list(data)), bytes.fromhex(want_tx)
            assert (frame if want[0] == 0xF7 else frame[1:-1]) == want
            raw = bytes.fromhex(rx)
            rfun, rdata = parse_frame(raw)
            assert raw[1] == addr and rfun == fun
            return rdata

    # 1. Dekodierung / Builder gegen Log
    check("0x06 = 300.0", decode_sys_param_float(bytes.fromhex("00 00 96 43")) == 300.0)
    check("0x06 = 100.0", decode_sys_param_float(bytes.fromhex("00 00 c8 42")) == 100.0)
    check("write-builder = Log-TX + verifizierter Vollframe",
          build_frame(0x81, 6, build_sys_param_write_payload(70, 300.0))
          == bytes.fromhex("f7 81 09 00 06 01 46 00 00 96 43 8f")
          and bytes(build_sys_param_write_payload(70, 100.0))
          == bytes.fromhex("01 46 00 00 c8 42"))
    check("update-builder [03]", build_sys_param_update_payload() == [3])
    for bad in [(227, 1.0), (True, 1.0), (70, float("nan")), (70, True)]:
        check("builder guard %r" % (bad,),
              raises(ValueError, build_sys_param_write_payload, *bad))

    # 2. Lesende Kommandos gegen Live-Frames
    bus = _Bus([
        ("81 05 00 06 02 09", "f7 81 07 00 06 20 29 f7 42 59"),  # Live-Log
        ("81 05 00 06 02 0a", "f7 81 05 00 06 00 00 90"),        # :2520/2526
        ("81 05 00 06 02 e2", "f7 81 03 00 06 af"),              # :2859/2865
        ("81 04 00 0c 0b", "f7 81 0b 00 0c 00 00 00 00 00 00 00 00 fb"),  # :2441/2447
        ("81 04 00 11 04", "f7 81 05 00 11 02 00 0e"),           # 10-12:31409/31432
        ("81 04 00 11 03", "f7 81 04 00 11 03 13"),              # 10-12:31436/31454
        ("81 04 00 0e 02", "f7 81 04 00 0e 81 00"),              # :2387/2393
        # 0x12: TX in Live-Log-Form; Antwort PLATZHALTER-UUID, CRC neu
        ("81 04 00 12 02", "f7 81 0b 00 12 cd ab 00 00 00 00 00 00 ad"),
        ("81 09 00 06 01 46 00 00 c8 42", "f7 81 07 00 06 00 00 c8 42 d4"),  # 10-12:10700/10706
        ("81 04 00 06 03", "f7 81 04 00 06 01 21"),              # Live-Log
        # verifizierter Live-Vollframe (10-05:16596) -> Echo (10-14:25260)
        ("f7 81 09 00 06 01 46 00 00 96 43 8f", "f7 81 07 00 06 00 00 96 43 09"),
    ])
    mm = MotorMini(bus, 0x81)
    check("ID 9 = 123.58", abs(mm.read_sys_param(9) - 123.58) < 1e-3
          and bus.calls[0][3] == {"timeout": 1.0, "retries": 0})
    check("ID 10 (2 B) -> ValueError", raises(ValueError, mm.read_sys_param, 10))
    check("ID 226 (0 B) -> None", mm.read_sys_param(226) is None)
    pr = mm.check_protection()
    check("check_protection 8x00 -> ok", pr["ok"] and pr["raw"] == bytes(8)
          and pr["errors"] == [] == pr["warnings"]
          and bus.calls[3][3] == {"timeout": 0.05, "retries": 0})
    check("get_stall_mode -> 2", mm.get_stall_mode() == 2)
    check("set_mode 3 Echo", mm.set_mode(3) == 3)
    check("read_485_addr -> 0x81", mm.read_485_addr() == 0x81)
    check("read_uuid 8 B cd ab", mm.read_uuid()[:2] == b"\xcd\xab")
    nobus = _Bus([])
    check("write ohne confirm gesperrt",
          raises(PermissionError, MotorMini(nobus, 0x81).write_sys_param, 70, 100.0)
          and nobus.calls == [])
    check("write confirm=True Echo", mm.write_sys_param(70, 100.0, confirm=True) == 100.0)
    check("update [03] -> [01]", mm.update_sys_params(confirm=True) is True)
    check("Live-Vollframe ID70=300", mm.write_sys_param(70, 300.0, confirm=True) == 300.0)
    check("alle Script-Frames verbraucht", not bus.script)
    m0 = MotorMini(nobus, 0x83)
    check("update ohne confirm gesperrt",
          raises(PermissionError, m0.update_sys_params)
          and raises(PermissionError, m0.update_sys_params, confirm="yes"))
    for pid in (1, 9, 224, 225, 0):
        check("ID %d nicht in MINI_WRITABLE_IDS gesperrt" % pid,
              raises(PermissionError, m0.write_sys_param, pid, 1.0, confirm=True))
    for bad in (1e-50, -1e-50, 3.5e38, float("inf")):
        check("Wert %r -> ValueError" % (bad,),
              raises(ValueError, m0.write_sys_param, 70, bad, confirm=True))
    check("Sperren senden nichts", nobus.calls == [])

    # 3. Guards / Sperren senden nichts
    check("addr-guard 0x85", raises(ValueError, MotorMini, None, 0x85))
    for args in [(), (0x9643,)]:
        check("read_encoder%r wirft" % (args,),
              raises(NotImplementedError, MotorMini(nobus, 0x81).read_encoder, *args))
    for bad in [(), (0,), (4,), (256,), (True,), (0x02, 0x00)]:
        check("set_mode guard %r" % (bad,),
              raises((TypeError, ValueError), MotorMini(nobus, 0x81).set_mode, *bad))
    check("Guards senden nichts", nobus.calls == [])

    # 3b. 0x0F / 0x08 gegen Live-Frames 16.10. (Motor X, Standby)
    bus = _Bus([
        ("81 04 00 0f 00",
         "f7 81 0f 00 0f 01 09 07 51 01 03 01 03 02 4b 01 00 02"),
        ("81 04 00 08 01", "f7 81 07 00 08 00 00 00 00 0a"),
        ("81 04 00 08 09", "f7 81 07 00 08 33 33 d3 3a e6"),
        ("81 04 00 08 0a", "f7 81 07 00 08 33 33 53 3a 50"),
        ("81 04 00 08 0b", "f7 81 07 00 08 52 04 00 00 71"),
        ("81 04 00 08 0c", "f7 81 07 00 08 00 00 00 00 0a"),
    ])
    mv = MotorMini(bus, 0x81)
    v = mv.query_motor_version()
    check("0x0F Version 1.9.7.81 / 1.3 / 1.3 / 2.75 / 1.0",
          (v["fireware_ver"], v["cmd_system_ver"], v["protection_ver"],
           v["flash_param_ver"], v["logger_ver"])
          == ("1.9.7.81", "1.3", "1.3", "2.75", "1.0")
          and len(v["raw"]) == 12
          and bus.calls[0][3] == {"timeout": TIMEOUT_VERSION, "retries": 0})
    r1, r9, r10, r11, r12 = [mv.read_variable(i) for i in (1, 9, 10, 11, 12)]
    check("0x08 idx 1 = 0.0", r1["value"] == 0.0 and r1["type"] == "float"
          and bus.calls[1][3] == {"timeout": TIMEOUT_VARIABLE, "retries": 0})
    check("0x08 idx 9 = 0.0016113", abs(r9["float"] - 0.0016113281) < 1e-9)
    check("0x08 idx 10 = 0.0008057", abs(r10["value"] - 0.0008056641) < 1e-9)
    check("0x08 idx 11 = 1106 (int)", r11["int"] == 1106 and r11["value"] == 1106
          and r11["name"] == "pos_counts")
    check("Skalierung 690 -> 1.35481 rad, 800 Counts = 10 mm",
          abs(690 * 2 * math.pi / POS_COUNTS_PER_REV - 1.35481) < 1e-5
          and counts_to_mm(1490 - 690) == 10.0 and counts_to_mm(3200) == 40.0)
    check("0x08 idx 12 = 0 (int)", r12["value"] == 0 and r12["type"] == "int")
    check("0x0F/0x08 Script verbraucht", not bus.script)
    check("0x08 Namen 5..8 + '<I' unsigned",
          decode_variable(5, bytes(4))["name"] == "pos_fdb" and r1["name"] == "pos_ref"
          and decode_variable(8, bytes(4))["name"] == "iq_fdb"
          and decode_variable(11, b"\xff\xff\xff\xff")["value"] == 0xFFFFFFFF)
    def _vrx(v):
        body = bytes([0x07, 0x00, FUNC_VARIABLE]) + struct.pack("<f", v)
        return "f7 81 %s %02x" % (body.hex(" "), _crc8(body))
    bus = _Bus([("81 04 00 08 %02x" % i, _vrx(v))
                for i, v in ((5, 0.5), (6, 1.0), (7, 3.0), (8, -4.0))])
    mf = MotorMini(bus, 0x81)
    check("Feedback pos/spd/id,iq (idx 5..8) + hypot",
          (mf.read_position_feedback(), mf.read_speed_feedback(),
           mf.read_current_feedback()) == (0.5, 1.0, {"id": 3.0, "iq": -4.0, "abs": 5.0})
          and not bus.script)
    # MOTOR-FW 16.10.: Namen/Einheiten, Helfer (Live-Werte F600, DIR-Gegenprobe)
    check("Namen/Einheiten 1..12 vollständig",
          sorted(VARIABLE_NAMES) == sorted(VARIABLE_UNITS) == list(range(1, 13))
          and VARIABLE_UNITS[6] == "rad/s" and VARIABLE_NAMES[12] == "dir_input"
          and decode_variable(9, bytes(4))["unit"] == "A")
    vals = {1: 5.188, 2: 1.633, 3: 0.0, 4: 0.0794, 9: -0.0725, 10: -0.0363}
    bus = _Bus([("81 04 00 08 %02x" % i, _vrx(vals[i])) for i in (1, 2, 3, 4, 9, 10)]
               + [("81 04 00 08 0c", "f7 81 07 00 08 %s %02x" % (
                   (bytes([7, 0, 8]) + struct.pack("<I", v))[3:].hex(" "),
                   _crc8(bytes([7, 0, 8]) + struct.pack("<I", v)))) for v in (0, 1)])
    mh = MotorMini(bus, 0x81)
    ref, ph = mh.read_reference_values(), mh.read_phase_currents()
    # KORREKTUR 17.10.2026: phase_order_invert 0 (live) -> Ib = +idx10
    check("read_reference_values / read_phase_currents / read_dir_input",
          abs(ref["spd_ref"] - 1.633) < 1e-5 and ref["id_ref"] == 0.0
          and abs(ph["ib"] + 0.0363) < 1e-6 and abs(ph["ib_fw"] + 0.0363) < 1e-6
          and abs(ph["minus_ib"] - 0.0363) < 1e-6 and abs(ph["ia"] + 0.0725) < 1e-6
          and ph["phase_order_invert"] == 0 and VARIABLE_NAMES[10] == "ib"
          and IDX_IB == IDX_MINUS_IB == 10
          and [mh.read_dir_input(), mh.read_dir_input()] == [0, 1] and not bus.script)
    bus = _Bus([("81 04 00 08 %02x" % i, _vrx(vals[i])) for i in (9, 10)])
    ph1 = MotorMini(bus, 0x81).read_phase_currents(phase_order_invert=1)
    check("read_phase_currents(phase_order_invert=1) -> Ib = -idx10",
          abs(ph1["ib"] - 0.0363) < 1e-6 and abs(ph1["minus_ib"] + 0.0363) < 1e-6
          and not bus.script)
    check("read_phase_currents ungültiges invert -> ValueError ohne Senden",
          raises(ValueError, MotorMini(nobus, 0x81).read_phase_currents, True)
          and raises(ValueError, MotorMini(nobus, 0x81).read_phase_currents, 1.0)
          and nobus.calls == [])
    dp = decode_protection(bytes.fromhex("05 01 00 00 06 00 00 00"))
    check("decode_protection u32 LE / decode_motor_status",
          dp["errors"] == [0, 2, 8] and dp["warnings"] == [1, 2] and not dp["ok"]
          and decode_motor_status(0)["ok"] and decode_motor_status(5)["stall"]
          and decode_motor_status(5)["warn_latched"]
          and not decode_motor_status(5)["err_latched"]
          and raises(ValueError, decode_protection, bytes(9)))
    for bad in (0, 13, -1, True, 1.0, None):
        check("0x08 idx-guard %r" % (bad,),
              raises(ValueError, MotorMini(nobus, 0x81).read_variable, bad))
    check("0x0F Längen-Guard", raises(ValueError, decode_motor_version, bytes(11)))
    check("0x08 Guards senden nichts", nobus.calls == [])

    # 4. query_version (Update-Pfad, retries=2 = 3 Versuche)
    class _Rec:
        calls = []

        def transact(self, addr, fun, data=(), **kw):
            self.calls.append((addr, fun, list(data), kw))
            return b""
    rec = _Rec()
    MotorMini(rec, 0x82).query_version()
    check("query_version retries=2 (3 Versuche)",
          rec.calls[0][1:3] == (FUNC_FW_UPDATE, [SUB_GET_VERSION])
          and rec.calls[0][3].get("retries") == UPDATE_RETRIES == 2)

    print("SELBSTTEST: %s" % ("ok" if fails == 0 else "%d FEHLER" % fails))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
