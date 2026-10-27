#!/usr/bin/env python3
"""filament_rack.py – Python-Nachbau von filament_rack_wrapper (K2 Plus, FW 1.1.7.0).

Ersetzt (statisch rekonstruiert, KEIN Gerätezugriff):
  /usr/share/klipper/klippy/extras/filament_rack_wrapper.cpython-39.so (Stock-Rootfs)
  (ELF32 armhf, Cython 0.29.21, md5 50713b85…, Quellname
  klippy/extras/filament_rack_wrapper.py) + Loader filament_rack.py (3 Zeilen).

WAS IST DAS „RACK“?
  Zwei Dinge in EINEM Modul:
  (a) Ein RS485-Teilnehmer an Adresse 0x11 (Config addr=17), den der Host
      genau EINMAL pro Auslösung abfragt: Request fun 0x02, status 0xFF,
      OHNE Daten; Antwort = 40 ASCII-Zeichen RFID-„vender“ (gleiches
      Layout wie der CFS-Vendor, box.split_rfid_vendor). Auslöser ist der
      Taster/Sensor `not_pin` (Live-Config `!PA5`, Haupt-MCU) über
      register_buttons -> _button_handler -> reactor.register_callback(
      send_data). Das ist ein RFID-Leser am externen Spulenhalter.
      Im beobachteten Live-Betrieb NIE ausgelöst: keine einzige
      "[filament rack] data_send"-Zeile in den Live-Logs 05.–16.10.2026,
      Status zeigt vender/color_value/material_type = '-1'. Ob die Hardware
      im untersuchten K2 Plus verbaut ist, ist damit OFFEN.
  (b) Host-Logik für das manuelle Einlegen/Entladen über die externe Spule
      (Makros LOAD_MATERIAL*/QUIT_MATERIAL* in gcode_macro.cfg): Material-
      Temperatur/-Geschwindigkeit aus material_database.json, Schnellheizen,
      Lüfter sichern/blasen, Abkühlen. Diese Teile laufen im untersuchten Gerät live
      (FILAMENT_RACK_SET_TEMP/FLUSH/… in den Makros, Status-Abfragen).

BELEGFORM
  `Z.<n>` = Cython-__pyx_lineno der Originalquelle filament_rack_wrapper.py
  (aus den Fehlerpfaden des .so; Zuordnung einzelner Operationen ±1 Zeile),
  `@0x…` = Funktions-/Befehlsadresse im .so (llvm-objdump-vaddr, Basis 0).
  B = belegt (Code), L = zusätzlich durch Live-Log bestätigt,
  A = ANNAHME (nicht belegt), TODO = offen.

SENDEN
  Nur Frames BAUEN/PARSEN. Senden ausschließlich über serial485
  (Serial_485_Wrapper.cmd_send_data_with_response -> check_tx_frame) und nur
  mit allow_write=True (Default gesperrt -> RackWriteLocked, nichts gesendet).
"""

import copy
import json
import logging
import os

import serial485
from serial485 import (STATUS_OPERATIONAL, build_frame, build_tx_frame,
                       check_tx_frame, crc8_cal, parse_frame)

_log = logging.getLogger(__name__)

# =====================================================================
# 1) Konstanten (Config-Defaults, Bus, Dateipfade)
# =====================================================================

# Config [filament_rack] – __init__ @0x1cff8, Z.66-78 (B); aufgelöste Werte
# live im configfile-Status (Live-Logs 12./16.10.: addr 17, extrude 140,
# temp 220, velocity 360, max_temp_flush_times 3, didi (), need_clean_length
# 70) (L). Form: name -> (Methode, default, minval, maxval).
CONFIG_SPEC = {
    "not_pin": ("get", None, None, None),             # Z.69 (A: default None, Tupel ('not_pin', ?))
    "addr": ("getint", 17, None, None),               # Z.70 @0x1d2a0
    "extrude": ("getint", 140, 0, 300),               # Z.71 @0x1d324
    "temp": ("getint", 220, 140, 350),                # Z.72 @0x1d3d0
    "velocity": ("getint", 360, 0, 900),              # Z.73 @0x1d484
    # Z.74 @0x1d51c: getfloat('max_temp_flush_times', 370, 300, 1000) ->
    # self.diff_material_type_min_flush_len. CREALITY-FEHLER (B): liest den
    # FALSCHEN Schlüssel; würde max_temp_flush_times in der Config gesetzt
    # (z.B. 3), scheitert diese Zeile an minval=300 (Config-Fehler).
    "_diff_material_type_min_flush_len": ("getfloat:max_temp_flush_times",
                                          370, 300, 1000),
    "max_temp_flush_times": ("getint", 3, 0, 10),     # Z.75 @0x1d5e4
    "didi": ("getfloatlist", (), None, None),         # Z.76 -> beep_didi_param (default A: () laut Live)
    "need_clean_length": ("getint", 70, 0, 200),      # Z.77 @0x1d71c
}
CONFIG_DEFAULTS = {k: v[1] for k, v in CONFIG_SPEC.items()}

# Bus (send_data @0x25cfc, Z.157-162, Bytes-Literale is_unicode=0 in der
# Cython-Stringtabelle @0x40de4-0x40e20) (B)
RACK_ADDR = 0x11            # b'\x11' (= Config addr 17)
RACK_LEN = 0x03             # b'\x03' (len ohne Daten)
RACK_STATE = 0xFF           # b'\xff' (= serial485.STATUS_OPERATIONAL)
RACK_FUN_GET_RFID = 0x02    # b'\x02'
RACK_TIMEOUT_S = 0.5        # vmov.f64 d0,#0.5 @0x263e4 (Z.167)
# data_send = addr + length + state + cmd (PyNumber_Add-Kette @0x26244-0x26270)
DATA_SEND = bytes([RACK_ADDR, RACK_LEN, RACK_STATE, RACK_FUN_GET_RFID])
# Antwort: rfid = ''.join(chr(i) for i in ret[5:-1]) (Genexpr @0xd638,
# Z.170: Slice 5..-1 @0xd718, Builtin chr = Cache-Slot 0x42318 @0x4120) (B)
RFID_SLICE = slice(5, -1)
RFID_LEN = 40               # cmp r0,#40 @0x267c8 (Z.171)
# Feldausschnitte (B): material_type = rfid[11:17] @0x26a48, color_value =
# rfid[17:24] @0x26a98, vender = ganzer String @0x2698c (send_data);
# check_rfid_valid vergleicht rfid[12:17] @0x1af3c mit DB base.id.
RFID_MATERIAL_SLICE = slice(11, 17)
RFID_COLOR_SLICE = slice(17, 24)
RFID_DBID_SLICE = slice(12, 17)
# DB-ID aus 6-stelligem material_type: type[1:6] (@0x14d74 temp, ebenso
# speed/type) (B)
TYPE_DBID_SLICE = slice(1, 6)

UNKNOWN = "-1"              # Startwert filament_rack_data (Z.107-109) (B, L)
NONE_STR = "None"           # Stringvergleich type == 'None' (@0x14cec) (B)

# Dateien relativ zu base_dir (extras.base_info) (B: Strings, os.path.join)
MATERIAL_DATABASE_REL = "creality/userdata/box/material_database.json"
SYSTEM_CONFIG_REL = "creality/userdata/config/system_config.json"
REMAIN_MATERIAL_REL = "creality/userdata/box/remain_material_data.json"

# Material: Volumenstrom -> Filamentvorschub (get_material_target_speed,
# Z.282 vdiv.f64 durch 2.4 aus .rodata @0x13b80; Z.292 * 60) (B, L:
# Live-Status remain_material_velocity 350.00000000000006 = 14/2.4*60,
# 575.0 = 23/2.4*60). 2.4 ≈ Querschnitt 1,75-mm-Filament in mm².
VOLUMETRIC_DIVISOR = 2.4
PER_MINUTE = 60

# quickly_wait_heating @0x275fc (Z.341-354) (B)
HEAT_WINDOW_K = 10          # target-10 <= cur <= target+10 (@0x27a7c/0x27aa8)
HEAT_POLL_S = 0.5           # reactor.pause(monotonic()+0.5) (C:0.5 @0x27d2c)
HEAT_NUDGE_GCODE = ("G91", "G0 E0.01 F60", "G90")   # Z.352-354
# send_data: Warte-Schleife bis enable_get_filament, 1 s (C:1 @0x25fbc)
ENABLE_POLL_S = 1.0
# is_different_type_material: Temperaturabstand > 10 K (@0xfe88) (B)
DIFF_TEMP_K = 10

# G-Code-Strings (B: Cython-Stringtabelle)
GC_FAN_FULL = "SET_PIN PIN=fan0 VALUE=255"
GC_FAN_OFF = "SET_PIN PIN=fan0 VALUE=0.0"
GC_FAN_SET = "SET_PIN PIN=fan0 VALUE=%.2f"
GC_DWELL_3S = "G4 P3000"
GC_M400 = "M400"
GC_M104 = "M104 S%d"
GC_COOL_PAUSED = "M104 S140"
GC_COOL_OFF = "M104 S0"
GC_FLUSH_FAN = "M106 P0 S50"
GC_SAVE_HOTEND = ("SET_GCODE_VARIABLE MACRO=PRINTER_PARAM "
                  "VARIABLE=hotend_temp VALUE=%s")
BEEP_CMD = "/usr/bin/beep.sh %s"     # Z.200
BEEP_DEFAULT_CMD = "/usr/bin/beep.sh 0.2"   # Z.209-210

# Registrierte G-Codes (__init__ Z.92-101, Reihenfolge wie im Binary) (B)
GCODES = {
    "FILAMENT_RACK": "cmd_filament_rack",
    "FILAMENT_RACK_FLUSH": "cmd_filament_rack_flush",
    "FILAMENT_RACK_SAVE_FAN": "cmd_save_fan",
    "FILAMENT_RACK_RESTORE_FAN": "cmd_restore_fan",
    "FILAMENT_RACK_SET_TEMP": "cmd_filament_rack_set_temp",
    "FILAMENT_RACK_PRE_FLUSH": "cmd_filament_rack_pre_flush",
    "FILAMENT_RACK_MODIFY": "cmd_filament_rack_modify",
    "SET_COOL_TEMP": "cmd_set_cool_temp",
}


class RackWriteLocked(RuntimeError):
    """Senden gesperrt (allow_write=False). Es wurde NICHTS gesendet."""


# =====================================================================
# 2) Frames bauen / parsen (rein, kein Bus)
# =====================================================================

def build_rfid_request_wire():
    """Wire-Bytes wie Stock data_send: [addr][len][state][fun] = 11 03 ff 02
    (ohne Head/CRC; CRC ergänzt der 485-Treiber). Beleg send_data Z.157-162."""
    return DATA_SEND


def build_rfid_request_frame():
    """Vollframe F7 11 03 FF 02 <crc> über serial485 (inkl. Sendeschutz).
    CRC-8/ATM über [len,status,fun] = crc8(03 ff 02) = 0x64."""
    return build_tx_frame(RACK_ADDR, RACK_FUN_GET_RFID, (),
                          status=RACK_STATE)


def data_send_string(data_send=DATA_SEND):
    """Log-Text wie Stock: ' '.join(hex(i) for i in data_send)
    (Genexpr @0xd480, Z.163; Builtin hex = Cache-Slot 0x42310) ->
    '0x11 0x3 0xff 0x2'. Log: '[filament rack] data_send: %s' (Z.165-167)."""
    return " ".join(hex(i) for i in bytes(data_send))


def rfid_from_response(ret):
    """Antwort-Vollframe -> RFID-String wie Stock:
    ''.join(chr(i) for i in ret[5:-1]) (Z.170-171). Der Stock prüft hier
    selbst KEINE CRC/Adresse (macht der 485-Treiber); ret None -> None."""
    if ret is None:
        return None
    return "".join(chr(i) for i in bytes(ret)[RFID_SLICE])


def parse_rfid_response_checked(ret):
    """Zusatz (nicht Stock): Vollframe CRC-/Längen-prüfen (serial485.
    parse_frame), Absender 0x11 + fun 0x02 verlangen, dann wie Stock in
    RFID-String wandeln. Fehler -> ValueError."""
    raw = bytes(ret)
    fun, _data = parse_frame(raw)
    if raw[1] != RACK_ADDR:
        raise ValueError("Absender 0x%02x != Rack 0x11" % raw[1])
    if fun != RACK_FUN_GET_RFID:
        raise ValueError("fun 0x%02x != 0x02" % fun)
    return rfid_from_response(raw[:raw[2] + 3])


def split_rack_rfid(rfid):
    """Felder wie send_data Z.174-176: vender = ganzer String,
    material_type = rfid[11:17], color_value = rfid[17:24]."""
    return {"vender": rfid,
            "material_type": rfid[RFID_MATERIAL_SLICE],
            "color_value": rfid[RFID_COLOR_SLICE]}


# =====================================================================
# 3) Material-Datenbank (rein; Datei-Lader separat)
# =====================================================================

def load_json_file(path):
    """json.load(open(path,'r')) – wie Stock (with open(...,'r'))."""
    with open(path, "r") as f:
        return json.load(f)


def _material_list(db):
    """data.get('result', {}).get('list', []) (B: check_rfid_valid Z.138,
    get_material_* Z.246/277/308)."""
    return (db or {}).get("result", {}).get("list", [])


def _db_id(entry):
    """material_list[i].get('base', {}).get('id', ?) (B; Default A: None)."""
    return entry.get("base", {}).get("id", None)


def check_rfid_valid(rfid, db, respond_info=None):
    """check_rfid_valid @0x1af00 (Z.131-145, B):
    material_type = rfid[12:17]; True, wenn ein DB-Eintrag base.id ==
    material_type hat (dann gcode.respond_info('material_type: %s')).
    Fehlende/kaputte DB -> False (Stock: except -> logging.error mit dem
    copy-paste-Text 'material database get extrusion speed: %s')."""
    material_type = rfid[RFID_DBID_SLICE]
    try:
        for entry in _material_list(db):
            if _db_id(entry) == material_type:
                if respond_info:
                    respond_info("material_type: %s" % material_type)
                return True
    except Exception as err:  # noqa: BLE001 – wie Stock
        _log.error("material database get extrusion speed: %s", err)
    return False


def _type_or_fail(type_):
    """Gemeinsamer Anfang von get_material_target_temp/speed/type (B):
    'None' oder '-1' -> Warnung 'failed to obtain consumable type'."""
    if type_ == NONE_STR or type_ == UNKNOWN:
        _log.warning("failed to obtain consumable type")
        return None
    return type_[TYPE_DBID_SLICE]


def get_material_target_temp(db, type_, max_=False, fallback_temp=220):
    """get_material_target_temp(self, type=None, max=False) @0x14bf0
    (Z.232-262, B). type None -> self.filament_rack_data['material_type']
    (macht der Aufrufer). Treffer base.id == type[1:6]:
      max=True  -> int(base.get('maxTemp', 1))
      max=False -> int(kvParam.get('nozzle_temperature', 1))
    Kein Treffer / 'None' / '-1' -> self.temp (fallback_temp).
    A: Zuordnung max -> maxTemp aus Variablennamen nozzle_max_temp
    (zwei int()-Pfade @0x164cc/0x16518) abgeleitet."""
    type_tmp = _type_or_fail(type_)
    if type_tmp is None:
        return fallback_temp
    temp = None
    try:
        for entry in _material_list(db):
            if _db_id(entry) == type_tmp:
                nozzle_temp = entry.get("kvParam", {}).get(
                    "nozzle_temperature", 1)
                nozzle_max_temp = entry.get("base", {}).get("maxTemp", 1)
                temp = int(nozzle_max_temp) if max_ else int(nozzle_temp)
                break
    except Exception as err:  # noqa: BLE001
        _log.error("material database get nozzle temp: %s", err)
    if temp is None:
        _log.warning("get material target temp fail")
        return fallback_temp
    _log.info("get material temp: %d", temp)
    return temp


def get_material_target_speed(db, type_, fallback_velocity=360,
                              respond_info=None):
    """get_material_target_speed(self, type=None) @0x12644 (Z.264-293, B, L):
    Treffer: v = float(kvParam.get('filament_max_volumetric_speed', 1)) / 2.4
    (respond_info 'max_volumetric_speed: %s'), Rückgabe v * 60 (mm/min).
    Fehlschlag -> self.velocity (fallback_velocity, OHNE *60; Z.269/289).
    Live: 'max_volumetric_speed: 23' -> 'get material extrusion speed: 9'."""
    type_tmp = _type_or_fail(type_)
    if type_tmp is None:
        return fallback_velocity
    speed = None
    try:
        for entry in _material_list(db):
            if _db_id(entry) == type_tmp:
                mvs = entry.get("kvParam", {}).get(
                    "filament_max_volumetric_speed", 1)
                if respond_info:
                    respond_info("max_volumetric_speed: %s" % mvs)
                speed = float(mvs) / VOLUMETRIC_DIVISOR
                break
    except Exception as err:  # noqa: BLE001
        _log.error("material database get extrusion speed: %s", err)
    if speed is None:
        _log.warning("get material extrusion speed fail")
        return fallback_velocity
    _log.info("get material extrusion speed: %d", speed)
    return speed * PER_MINUTE


def get_material_type(db, type_, respond_info=None):
    """get_material_type(self, type=None) @0x101f4 (Z.295-325, B):
    Treffer -> base.get('meterialType', 1) (sic, Creality-Schreibweise),
    respond_info 'meterialType: %s', Log 'get next material type: %s'.
    Fehlschlag -> None (GOT 0x3ffb0 = _Py_NoneStruct @0x10a94)."""
    type_tmp = _type_or_fail(type_)
    if type_tmp is None:
        return None
    mtype = None
    try:
        for entry in _material_list(db):
            if _db_id(entry) == type_tmp:
                mtype = entry.get("base", {}).get("meterialType", 1)
                if respond_info:
                    respond_info("meterialType: %s" % mtype)
                break
    except Exception as err:  # noqa: BLE001
        _log.error("material database get extrusion speed: %s", err)
    if mtype is None:
        _log.warning("get material base type fail")
        return None
    _log.info("get next material type: %s", mtype)
    return mtype


def is_different_type_material(last_type, current_type, last_temp,
                               current_temp):
    """is_different_type_material @0xfb0c (Z.327-339, B).
    Eingaben = Ergebnisse von get_material_type/get_material_target_temp für
    remain_material.type (last) bzw. filament_rack_data (current).
    - einer der Typen None -> False (Z.332)
    - last not in current UND current not in last -> True
      (__Pyx_PySequence_ContainsTF(..., Py_NE) @0xfcd4/0xfcf0: Teilstring-
      Vergleich, z.B. 'PLA' vs. 'PLA-CF' gilt als gleich)
    - sonst abs(last_temp - current_temp) > 10 -> True, sonst False."""
    if last_type is None or current_type is None:
        return False
    if last_type not in current_type and current_type not in last_type:
        return True
    return abs(last_temp - current_temp) > DIFF_TEMP_K


# =====================================================================
# 4) G-Code-Abläufe (rein: liefern Befehlslisten / nutzen Callbacks)
# =====================================================================

def blow_gcode(fan_last_value, fan_scale):
    """blow @0xceb4 (Z.380-385, B): Lüfter fan0 3 s voll, dann zurück auf
    last_value * scale. Gibt die G-Codes in Sende-Reihenfolge zurück."""
    value = fan_last_value * fan_scale
    return [GC_FAN_FULL, GC_DWELL_3S, GC_M400, GC_FAN_SET % value]


def save_fan_gcode(fan_last_value, fan_scale):
    """cmd_save_fan @0xca34 (Z.387-390, B): fan0_last_value = last_value *
    scale (nur wenn output_pin fan0 existiert, A: Prüfung 'is not None'),
    dann Lüfter aus. Rückgabe (gemerkter Wert, G-Codes)."""
    saved = fan_last_value * fan_scale
    return saved, [GC_FAN_OFF]


def restore_fan_gcode(fan0_last_value):
    """cmd_restore_fan @0xc5e8 (Z.392-396, B): SET_PIN fan0 auf gemerkten
    Wert (nur wenn fan0_last_value nicht None)."""
    if fan0_last_value is None:
        return []
    return [GC_FAN_SET % fan0_last_value]


def set_cool_temp_gcode(print_state, target_temp, min_extrude_temp):
    """cmd_set_cool_temp @0x17a34 (Z.217-226, B):
    'paused': falls target_temp >= min_extrude_temp (RichCompare Py_GE
      @0x17c48) -> PRINTER_PARAM.hotend_temp = target_temp sichern;
      dann immer M104 S140.
    'printing': nur logging.warning('unknown action'), kein G-Code.
    sonst: M104 S0."""
    if print_state == "paused":
        out = []
        if target_temp >= min_extrude_temp:
            out.append(GC_SAVE_HOTEND % target_temp)
        out.append(GC_COOL_PAUSED)
        return out
    if print_state == "printing":
        _log.warning("unknown action")
        return []
    return [GC_COOL_OFF]


def quickly_wait_heating(set_temp, run_gcode, get_target, get_smoothed,
                         can_extrude, pause, is_shutdown, max_loops=None):
    """quickly_wait_heating(self, set_temp) @0x275fc (Z.341-354, B).
    Port mit Callbacks statt Klipper-Objekten:
      M104 S<set_temp>; target = heater.target_temp;
      while not is_shutdown():
          cur = heater.smoothed_temp
          if target-10 <= cur <= target+10 and heater.can_extrude:
              G91 / G0 E0.01 F60 / G90; break
          pause(0.5)
    Rückgabe True = Fenster erreicht, False = Shutdown/max_loops.
    max_loops nur für Tests (Stock: unbegrenzt)."""
    run_gcode(GC_M104 % set_temp)
    target = get_target()
    loops = 0
    while not is_shutdown():
        cur = get_smoothed()
        if (target - HEAT_WINDOW_K <= cur <= target + HEAT_WINDOW_K
                and can_extrude()):
            for g in HEAT_NUDGE_GCODE:
                run_gcode(g)
            return True
        pause(HEAT_POLL_S)
        loops += 1
        if max_loops is not None and loops >= max_loops:
            return False
    return False


def beep_plan(didi, sound_sw=1):
    """beep @0x18a10 (Z.182-212, B) als Ablaufplan statt subprocess:
    sound_sw = system_config.json user_info.get('sound_sw', 1)
    (T:('sound_sw', 1)); falsy -> kein Ton.
    didi leer -> [('sh', '/usr/bin/beep.sh 0.2')].
    sonst für index in range(len(didi)): gerade -> ('sh', beep.sh <didi[i]>)
    + ('sleep', didi[i]); ungerade -> ('sleep', didi[i]) (tst #1 @0x19aac).
    A: Reihenfolge Ton->sleep im geraden Zweig aus Zeilen 200/205."""
    if not sound_sw:
        return []
    if len(didi) == 0:
        return [("sh", BEEP_DEFAULT_CMD)]
    plan = []
    for index in range(len(didi)):
        if index % 2 == 0:
            plan.append(("sh", BEEP_CMD % str(didi[index])))
        plan.append(("sleep", didi[index]))
    return plan


def read_sound_switch(system_config):
    """beep Z.183-189: system_config.json -> user_info.sound_sw (Default 1).
    Datei fehlt/kaputt -> 1 (A: Initialwert isEnanleBeep; Stock loggt
    'system config get sound switch: %s')."""
    try:
        if os.path.exists(system_config):
            data = load_json_file(system_config)
            return data.get("user_info", {}).get("sound_sw", 1)
    except Exception as err:  # noqa: BLE001
        _log.error("system config get sound switch: %s", err)
    return 1  # A


def modify_rack_data(params):
    """cmd_filament_rack_modify @0xdfb8 (Z.461-481, B, L): COLOR_VALUE /
    MATERIAL_TYPE / VENDER aus gcmd.get_command_parameters(); fehlend ->
    Warnung '<x> get failed' und '-1'. Live: UI sendet beim Start
    FILAMENT_RACK_MODIFY ohne Parameter -> alles '-1' (Status 12./16.10.).
    A: 'fehlend' = Wert None (dict.get-Default)."""
    out = {}
    for key, field, msg in (("COLOR_VALUE", "color_value",
                             "color_value get failed"),
                            ("MATERIAL_TYPE", "material_type",
                             "material_type get failed"),
                            ("VENDER", "vender", "vender get failed")):
        val = params.get(key)
        if val is None:
            _log.warning(msg)
            val = UNKNOWN
        out[field] = val
    _log.info("vender: %s, color_value: %s, material_type: %s",
              out["vender"], out["color_value"], out["material_type"])
    return out


# =====================================================================
# 5) RemainMaterial (Datei remain_material_data.json)
# =====================================================================

class RemainMaterial:
    """RemainMaterial @0x25018/0x234b0/0x21c68/0x2190c/0xaf3c (Z.8-61, B, L).
    Merkt Farbe/Typ des zuletzt per Rack geladenen Materials über Neustarts.
    get_velocity holt v über get_material_target_speed(type=self.type)
    (im Stock via config.get_printer().lookup_object('filament_rack');
    hier per Callback speed_fn)."""

    def __init__(self, path, speed_fn=None):
        self.path = path                 # Z.10 os.path.join(base_dir, REL)
        self.color = None                # Z.12
        self.type = None                 # Z.13
        self.velocity = None             # Z.14 (A: Startwert None)
        self.speed_fn = speed_fn
        self.init()                      # Z.15

    def init(self):
        """Z.17-33: Datei lesen; fehlt 'remain_material' -> mit
        {'color': None, 'type': None} anlegen und schreiben."""
        data = {}
        if os.path.exists(self.path):
            try:
                data = load_json_file(self.path)
            except Exception as err:  # noqa: BLE001
                _log.error("%s", err)
        rm = data.get("remain_material", {})
        if isinstance(rm, dict) and "color" in rm and "type" in rm:
            self.color = rm["color"]
            self.type = rm["type"]
        else:
            data["remain_material"] = {"color": None, "type": None}
            self._write(data)

    def _write(self, data):
        try:
            with open(self.path, "w") as f:
                json.dump(data, f)
        except Exception as err:  # noqa: BLE001
            _log.error("%s", err)

    def save(self, color, type_):
        """Z.36-53: merken, Datei aktualisieren, Log 'save: color: %s,
        type: %s', danach get_velocity()."""
        self.color = color
        self.type = type_
        data = {}
        try:
            data = load_json_file(self.path)
        except Exception as err:  # noqa: BLE001
            _log.error("%s", err)
        data["remain_material"] = {"color": color, "type": type_}
        self._write(data)
        _log.info("save: color: %s, type: %s", self.color, self.type)
        self.get_velocity()

    def get_velocity(self):
        """Z.54-56: velocity = get_material_target_speed(type=self.type)."""
        if self.speed_fn is not None:
            self.velocity = self.speed_fn(self.type)
        return self.velocity

    def get_status(self):
        """Z.58-61 (B, L: Status-Schlüssel live identisch)."""
        return {"remain_material_color": self.color,
                "remain_material_type": self.type,
                "remain_material_velocity": self.velocity}


# =====================================================================
# 6) Bus-/Zustands-Klasse (Nachbau der RS485-Hälfte von FilamentRackWrapper)
# =====================================================================

class FilamentRackRfid:
    """RS485-Teil von FilamentRackWrapper: send_data + Flags.

    wrapper  = serial485.Serial_485_Wrapper (oder kompatibel: Methode
               cmd_send_data_with_response(data, timeout)).
    material_db = geparste material_database.json (dict) oder None.
    allow_write = False (Default): send_data wirft RackWriteLocked, BEVOR
               etwas gesendet wird. Nur True erlaubt den Bus.
    pause    = Callback statt reactor.pause (Default: kein Warten).
    beep     = Callback statt threading.Thread(target=self.beep).start()
               (Default: nichts; Stock spielt den Ton parallel ab).
    """

    def __init__(self, wrapper, material_db=None, allow_write=False,
                 pause=None, beep=None, respond_info=None):
        self.wrapper = wrapper
        self.material_db = material_db
        self.allow_write = allow_write
        self.pause = pause or (lambda s: None)
        self.beep = beep or (lambda: None)
        self.respond_info = respond_info
        # __init__ Z.107-109 (B, L)
        self.filament_rack_data = {"vender": UNKNOWN, "color_value": UNKNOWN,
                                   "material_type": UNKNOWN}
        # Z.85-87 Flags; A: Startwerte False (send_data vergleicht per
        # Identität mit Py_False @0x25df4, enable_get_filament setzt True).
        self.can_get_filament_rack = False
        self.can_get_filament_rack_tmp = False

    def enable_get_filament(self, *_args):
        """@0xaea4 (Z.129, B): can_get_filament_rack = True. Stock-Event:
        'auto_addr:start' (falls [motor_control] switch == 1) bzw.
        'klippy:ready' (A: genaue Verzweigung @0x1e24c-0x1e800)."""
        self.can_get_filament_rack = True

    def button_handler(self, eventtime, state):
        """_button_handler @0xd924 (Z.124-126, B): Log
        '[filament rack] not_pin trigger, state: %s'; if state ->
        reactor.register_callback(send_data). Hier: direkt send_data."""
        _log.info("[filament rack] not_pin trigger, state: %s", state)
        if state:
            return self.send_data(eventtime)
        return None

    def send_data(self, eventtime=None):
        """send_data(self, eventtime=None) @0x25cfc / Wrapper @0x27458
        (Z.147-178, B). Ablauf wie Stock:
          1. can_get_filament_rack is False: wenn _tmp schon True -> return;
             sonst _tmp = True und in 1-s-Schritten warten bis freigegeben.
          2. beep (Stock: eigener Thread), data_send 11 03 ff 02 loggen,
          3. ret = _serial.cmd_send_data_with_response(data_send, 0.5),
          4. None -> Warnung 'do not get respond'; sonst rfid = chr(ret[5:-1]),
             len == 40 und check_rfid_valid -> vender/material_type/
             color_value übernehmen, sonst Warnung 'rfid[%s] is error'.
        Rückgabe (Nachbau-Zusatz): rfid-String oder None.
        Gesperrt (allow_write False) -> RackWriteLocked VOR jedem Bus-Zugriff.
        """
        if self.allow_write is not True:
            raise RackWriteLocked(
                "filament_rack.send_data: Senden gesperrt (allow_write=False);"
                " Frame wäre %s" % build_rfid_request_frame().hex(" "))
        if self.can_get_filament_rack is False:
            if self.can_get_filament_rack_tmp is True:
                return None
            self.can_get_filament_rack_tmp = True
            while self.can_get_filament_rack is False:
                self.pause(ENABLE_POLL_S)
        self.beep()
        data_send = build_rfid_request_wire()
        check_tx_frame(data_send[0], data_send[3], data_send[4:])
        _log.info("[filament rack] data_send: %s", data_send_string(data_send))
        ret = self.wrapper.cmd_send_data_with_response(data_send,
                                                       RACK_TIMEOUT_S)
        return self.handle_response(ret)

    def handle_response(self, ret):
        """Antwortauswertung von send_data (Z.168-178), rein testbar."""
        if ret is None:
            _log.warning("[filament rack] do not get respond")
            return None
        rfid = rfid_from_response(ret)
        if len(rfid) == RFID_LEN and check_rfid_valid(
                rfid, self.material_db, self.respond_info):
            _log.info("[filament rack] rfid: %s", rfid)
            self.filament_rack_data.update(split_rack_rfid(rfid))
            return rfid
        _log.warning("[filament rack] rfid[%s] is error", rfid)
        return None

    def get_status(self, remain_material=None):
        """get_status @0x174c0 (B, L): deepcopy(filament_rack_data) +
        remain_material.get_status()."""
        data = copy.deepcopy(self.filament_rack_data)
        if remain_material is not None:
            data.update(remain_material.get_status())
        return data


# =====================================================================
# 7) Nicht nachgebaut (Stubs mit Beleglage)
# =====================================================================

def cmd_filament_rack_pre_flush(*_args, **_kw):
    """cmd_filament_rack_pre_flush (B): Im .so existiert NUR der
    Argument-Wrapper @0xb960 (self, gcmd) ohne Rumpf-Funktion -> Stock ist
    ein No-op (gibt None zurück). Nachbau ebenso."""
    return None


def cmd_filament_rack_flush(*_args, **_kw):
    """cmd_filament_rack_flush @0x286d0 (Z.398-455) – NICHT nachgebaut.
    Belegt ist die Struktur: M106 P0 S50; remain/current
    Temperaturen (max=True/False) + Speed; is_different_type_material ->
    diff_material_type_min_flush_len statt extrude; Farb-Hexpaare
    (int(.,16)) -> box.box_action.get_flush_len / cal_flush_list /
    check_flush_temp_and_extrude(max_temp_flush=…, max_temp_flush_times);
    box.box_state.clear_e_err; blow(); box.box_action.nozzle_clean('circle'?);
    remain_material.save(color_value, material_type). Hängt an
    box_wrapper.BoxAction (nicht Teil dieses Moduls) – TODO."""
    raise NotImplementedError(
        "cmd_filament_rack_flush: hängt an box_wrapper.BoxAction "
        "(get_flush_len/cal_flush_list/check_flush_temp_and_extrude/"
        "nozzle_clean) – nicht nachgebaut")


def klipper_glue(*_args, **_kw):
    """Klipper-Einbindung (load_config, register_buttons([not_pin]),
    register_command, find_objs: lookup_object('serial_485 serial485'),
    'toolhead', 'print_stats', 'output_pin fan0', 'box') – bewusst NICHT
    nachgebaut."""
    raise NotImplementedError("Klipper-Glue nicht nachgebaut")


# =====================================================================
# 8) Selbsttest (ohne Hardware)
# =====================================================================

def selftest():
    import tempfile

    total = 0
    fails = 0

    def check(name, cond):
        nonlocal total, fails
        total += 1
        print(("ok   " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    F = bytes.fromhex

    # -- 1. CRC: gemeinsame serial485-CRC gegen ECHTE Frames ----------------
    # Kein echter Rack-Frame existiert (Rack live nie gesehen). Geprüft wird
    # daher die CRC-Funktion an echten Frames mit gleichem Aufbau:
    # (a) Live-Remain-Frame 12.10.2026 (box.py-Selbsttest),
    # (b) ref-hi valid_messages.json f70105ff0d0f0169 (status 0xFF wie Rack).
    live = F("f7 01 07 00 03 1c ff 00 00 11")
    check("crc live f7 01 07 00 03 1c ff 00 00 -> 0x11",
          crc8_cal(live[2:-1]) == 0x11)
    refhi = F("f7 01 05 ff 0d 0f 01 69")
    check("crc ref-hi f70105ff0d0f01 -> 0x69 (status 0xFF)",
          crc8_cal(refhi[2:-1]) == 0x69)
    check("parse_frame live ok", parse_frame(live) == (0x03, F("1c ff 00 00")))

    # -- 2. Request-Frame --------------------------------------------------
    check("wire data_send = 11 03 ff 02", build_rfid_request_wire()
          == F("11 03 ff 02"))
    req = build_rfid_request_frame()
    check("frame = f7 11 03 ff 02 64", req == F("f7 11 03 ff 02 64"))
    check("frame == build_frame(0x11,0x02,[],0xff)",
          req == build_frame(0x11, 0x02, [], status=STATUS_OPERATIONAL))
    check("state == serial485.STATUS_OPERATIONAL",
          RACK_STATE == serial485.STATUS_OPERATIONAL)
    check("len-Feld = len(data)+3 = 3", req[2] == RACK_LEN == 3)
    check("addr == Config-Default 17", RACK_ADDR == CONFIG_DEFAULTS["addr"])
    check("frame parsebar (eigene CRC)", parse_frame(req) == (0x02, b""))
    check("Sendeschutz lässt 0x11/0x02 durch",
          check_tx_frame(0x11, 0x02, ()) == [])
    check("Log-String wie Stock '0x11 0x3 0xff 0x2'",
          data_send_string() == "0x11 0x3 0xff 0x2")

    # -- 3. Antwort parsen (SYNTHETISCHER Vendor, Layout wie box.py) ---------
    vendor = "1052500001A1010010FF88000330000000000000"   # synthetisch
    check("synth. vendor 40 Zeichen", len(vendor) == RFID_LEN)
    resp = build_frame(RACK_ADDR, RACK_FUN_GET_RFID, vendor.encode("ascii"),
                       status=0x00)
    check("antwort 46 Byte (len 0x2b)", len(resp) == 46 and resp[2] == 0x2B)
    check("rfid = chr(ret[5:-1])", rfid_from_response(resp) == vendor)
    check("rfid None -> None", rfid_from_response(None) is None)
    check("checked parse ok", parse_rfid_response_checked(resp) == vendor)
    bad = bytearray(resp)
    bad[10] ^= 0x01
    try:
        parse_rfid_response_checked(bytes(bad))
        check("checked parse CRC-Fehler erkannt", False)
    except ValueError:
        check("checked parse CRC-Fehler erkannt", True)
    other = build_frame(0x01, RACK_FUN_GET_RFID, vendor.encode(), status=0)
    try:
        parse_rfid_response_checked(other)
        check("checked parse fremder Absender abgelehnt", False)
    except ValueError:
        check("checked parse fremder Absender abgelehnt", True)
    sp = split_rack_rfid(vendor)
    check("material_type = rfid[11:17] = 101001",
          sp["material_type"] == "101001")
    check("color_value = rfid[17:24] = 0FF8800", sp["color_value"] == "0FF8800")
    check("vender = ganzer String", sp["vender"] == vendor)
    try:
        import box as _box
        vf = _box.split_rfid_vendor(vendor)
        check("konsistent mit box.split_rfid_vendor (mat_id/col)",
              vf["mat_id"] == sp["material_type"]
              and vf["col"] == sp["color_value"])
    except ImportError:
        check("konsistent mit box.split_rfid_vendor (übersprungen)", True)

    # -- 4. Material-DB (synthetisch) -------------------------------------
    db = {"result": {"list": [
        {"base": {"id": "01001", "maxTemp": 240, "meterialType": "PLA"},
         "kvParam": {"nozzle_temperature": "220",
                     "filament_max_volumetric_speed": "23"}},
        {"base": {"id": "03001", "maxTemp": 280, "meterialType": "PETG"},
         "kvParam": {"nozzle_temperature": "250",
                     "filament_max_volumetric_speed": "14"}},
    ]}}
    check("check_rfid_valid: rfid[12:17]=01001 in DB",
          check_rfid_valid(vendor, db) is True)
    check("check_rfid_valid: unbekannte ID -> False",
          check_rfid_valid(vendor[:12] + "99999" + vendor[17:], db) is False)
    check("check_rfid_valid: DB None -> False",
          check_rfid_valid(vendor, None) is False)
    check("temp 101001 -> 220", get_material_target_temp(db, "101001") == 220)
    check("temp max 101001 -> 240",
          get_material_target_temp(db, "101001", max_=True) == 240)
    check("temp '-1' -> fallback self.temp",
          get_material_target_temp(db, "-1", fallback_temp=215) == 215)
    check("temp 'None' -> fallback",
          get_material_target_temp(db, "None") == 220)
    check("temp unbekannt -> fallback",
          get_material_target_temp(db, "109999", fallback_temp=230) == 230)
    infos = []
    v23 = get_material_target_speed(db, "101001", respond_info=infos.append)
    check("speed 23 mm³/s -> 23/2.4*60 = 575.0 (live 575.0)", v23 == 575.0)
    check("speed respond_info 'max_volumetric_speed: 23'",
          infos == ["max_volumetric_speed: 23"])
    check("speed 14 -> 350.00000000000006 (live exakt)",
          get_material_target_speed(db, "103001") == 350.00000000000006)
    check("speed Log-%d: int(23/2.4)=9, int(14/2.4)=5 (live 9/5)",
          int(23 / 2.4) == 9 and int(14 / 2.4) == 5)
    check("speed fallback ohne *60", get_material_target_speed(
        db, "-1", fallback_velocity=360) == 360)
    check("type 101001 -> PLA", get_material_type(db, "101001") == "PLA")
    check("type unbekannt -> None", get_material_type(db, "199999") is None)
    check("type '-1' -> None", get_material_type(db, "-1") is None)
    check("diff: None -> False",
          is_different_type_material(None, "PLA", 220, 220) is False)
    check("diff: PLA vs PETG -> True",
          is_different_type_material("PLA", "PETG", 220, 250) is True)
    check("diff: PLA vs PLA-CF Teilstring, dT 0 -> False",
          is_different_type_material("PLA", "PLA-CF", 220, 220) is False)
    check("diff: gleicher Typ dT 15 > 10 -> True",
          is_different_type_material("PLA", "PLA", 220, 235) is True)
    check("diff: gleicher Typ dT 10 -> False",
          is_different_type_material("PLA", "PLA", 220, 230) is False)

    # -- 5. G-Code-Abläufe --------------------------------------------------
    check("blow 0.5*255", blow_gcode(0.5, 255) == [
        "SET_PIN PIN=fan0 VALUE=255", "G4 P3000", "M400",
        "SET_PIN PIN=fan0 VALUE=127.50"])
    check("save_fan", save_fan_gcode(0.2, 255) == (51.0, [GC_FAN_OFF]))
    check("restore_fan", restore_fan_gcode(51.0)
          == ["SET_PIN PIN=fan0 VALUE=51.00"])
    check("restore_fan None -> nichts", restore_fan_gcode(None) == [])
    check("cool paused heiß", set_cool_temp_gcode("paused", 220, 170) == [
        GC_SAVE_HOTEND % 220, "M104 S140"])
    check("cool paused kalt", set_cool_temp_gcode("paused", 0, 170)
          == ["M104 S140"])
    check("cool printing -> nichts", set_cool_temp_gcode("printing", 220,
                                                         170) == [])
    check("cool standby -> M104 S0", set_cool_temp_gcode("standby", 0, 170)
          == ["M104 S0"])

    temps = iter([150, 190, 205, 211])
    sent, pauses = [], []
    reached = quickly_wait_heating(
        220, sent.append, lambda: 220, lambda: next(temps), lambda: True,
        pauses.append, lambda: False, max_loops=10)
    check("heating: Fenster bei 211 (>=210) erreicht", reached is True)
    check("heating: M104 + Nudge-G-Codes",
          sent == ["M104 S220", "G91", "G0 E0.01 F60", "G90"])
    check("heating: 3 Pausen à 0.5 s", pauses == [0.5, 0.5, 0.5])
    sent2 = []
    check("heating: can_extrude False -> kein Abschluss",
          quickly_wait_heating(220, sent2.append, lambda: 220, lambda: 220,
                               lambda: False, lambda s: None, lambda: False,
                               max_loops=3) is False
          and sent2 == ["M104 S220"])
    check("heating: Shutdown -> False", quickly_wait_heating(
        220, lambda g: None, lambda: 220, lambda: 0, lambda: True,
        lambda s: None, lambda: True) is False)

    check("beep leer -> beep.sh 0.2", beep_plan(()) == [
        ("sh", "/usr/bin/beep.sh 0.2")])
    check("beep didi (0.1,0.2,0.1)", beep_plan((0.1, 0.2, 0.1)) == [
        ("sh", "/usr/bin/beep.sh 0.1"), ("sleep", 0.1), ("sleep", 0.2),
        ("sh", "/usr/bin/beep.sh 0.1"), ("sleep", 0.1)])
    check("beep sound_sw 0 -> stumm", beep_plan((), sound_sw=0) == [])
    check("modify ohne Parameter -> alles '-1' (live)",
          modify_rack_data({}) == {"color_value": "-1",
                                   "material_type": "-1", "vender": "-1"})
    check("modify mit Parametern", modify_rack_data(
        {"COLOR_VALUE": "0FF8800", "MATERIAL_TYPE": "101001",
         "VENDER": "X"})["material_type"] == "101001")

    # -- 6. Config-Defaults (live aufgelöst 12./16.10.) ---------------------
    live_cfg = {"addr": 17, "extrude": 140, "temp": 220, "velocity": 360,
                "max_temp_flush_times": 3, "didi": (), "need_clean_length": 70}
    check("Config-Defaults == Live-Status",
          all(CONFIG_DEFAULTS[k] == v for k, v in live_cfg.items()))
    check("diff_material_type_min_flush_len Default 370 (Box-Live 370)",
          CONFIG_DEFAULTS["_diff_material_type_min_flush_len"] == 370)
    check("8 G-Codes registriert", len(GCODES) == 8)

    # -- 7. RemainMaterial (temporäre Datei) ---------------------------------
    with tempfile.TemporaryDirectory() as td:
        p = os.path.join(td, "remain_material_data.json")
        rm = RemainMaterial(p, speed_fn=lambda t: get_material_target_speed(
            db, t) if t else None)
        check("remain: neue Datei mit None/None angelegt",
              load_json_file(p) == {"remain_material": {"color": None,
                                                        "type": None}})
        rm.save("0FF8800", "103001")
        check("remain: save + velocity 350.00000000000006",
              rm.get_status() == {"remain_material_color": "0FF8800",
                                  "remain_material_type": "103001",
                                  "remain_material_velocity":
                                      350.00000000000006})
        rm2 = RemainMaterial(p)
        check("remain: Neustart liest Farbe/Typ",
              (rm2.color, rm2.type) == ("0FF8800", "103001"))

    # -- 8. Bus-Klasse mit Fake-Wrapper (kein Gerät) -----------------------
    class FakeWrapper:
        def __init__(self, answer):
            self.answer = answer
            self.calls = []

        def cmd_send_data_with_response(self, data, timeout=1.0):
            self.calls.append((bytes(data), timeout))
            return self.answer

    fw = FakeWrapper(resp)
    rack = FilamentRackRfid(fw, material_db=db)
    try:
        rack.send_data()
        check("gesperrt: RackWriteLocked", False)
    except RackWriteLocked:
        check("gesperrt: RackWriteLocked", True)
    check("gesperrt: nichts gesendet", fw.calls == [])

    rack = FilamentRackRfid(fw, material_db=db, allow_write=True)
    rack.enable_get_filament()
    check("send_data ok -> rfid", rack.send_data() == vendor)
    check("send_data Wire+Timeout wie Stock",
          fw.calls == [(F("11 03 ff 02"), 0.5)])
    check("status übernommen", rack.get_status()["material_type"] == "101001"
          and rack.get_status()["color_value"] == "0FF8800")

    fw_none = FakeWrapper(None)
    r2 = FilamentRackRfid(fw_none, material_db=db, allow_write=True)
    r2.enable_get_filament()
    check("keine Antwort -> None, Status bleibt '-1'",
          r2.send_data() is None
          and r2.get_status()["material_type"] == "-1")
    short = build_frame(RACK_ADDR, RACK_FUN_GET_RFID, b"SHORT", status=0)
    r3 = FilamentRackRfid(FakeWrapper(short), material_db=db,
                          allow_write=True)
    r3.enable_get_filament()
    check("rfid != 40 Zeichen -> error, Status '-1'",
          r3.send_data() is None and r3.filament_rack_data["vender"] == "-1")

    # Flag-Logik: nicht freigegeben -> warten (1 s), zweiter Aufruf verworfen
    waits = []
    r4 = FilamentRackRfid(FakeWrapper(resp), material_db=db,
                          allow_write=True)

    def _pause(s):
        waits.append(s)
        if len(waits) == 2:
            r4.enable_get_filament()
    r4.pause = _pause
    check("nicht freigegeben: wartet 2x 1 s, dann Abfrage",
          r4.send_data() == vendor and waits == [1.0, 1.0])
    r5 = FilamentRackRfid(FakeWrapper(resp), material_db=db,
                          allow_write=True)
    r5.can_get_filament_rack_tmp = True
    check("nicht freigegeben + _tmp: sofort return None",
          r5.send_data() is None)
    beeps = []
    r6 = FilamentRackRfid(FakeWrapper(resp), material_db=db,
                          allow_write=True, beep=lambda: beeps.append(1))
    r6.enable_get_filament()
    check("button state 0 -> nichts", r6.button_handler(0.0, 0) is None
          and beeps == [])
    check("button state 1 -> send_data + beep",
          r6.button_handler(0.0, 1) == vendor and beeps == [1])
    try:
        import serial485 as s4
        check("echter Serial_485_Wrapper hat cmd_send_data_with_response",
              hasattr(s4.Serial_485_Wrapper, "cmd_send_data_with_response"))
    except Exception:  # noqa: BLE001
        check("echter Serial_485_Wrapper vorhanden", False)

    # -- 9. Stubs ------------------------------------------------------------
    check("pre_flush No-op", cmd_filament_rack_pre_flush(None) is None)
    for fn in (cmd_filament_rack_flush, klipper_glue):
        try:
            fn()
            check("stub %s NotImplementedError" % fn.__name__, False)
        except NotImplementedError:
            check("stub %s NotImplementedError" % fn.__name__, True)

    print("SELBSTTEST filament_rack: %s %d/%d" % (
        "ok" if fails == 0 else "FEHLER", total - fails, total))
    return fails


if __name__ == "__main__":
    logging.basicConfig(level=logging.CRITICAL)
    raise SystemExit(selftest())
