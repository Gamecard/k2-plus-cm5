#!/usr/bin/env python3
"""cfswatch.py – lesender Live-Monitor für die CFS-Box (K2 Plus, klippy.log).

Liest klippy.log-Zeilen von stdin oder aus Dateien und gibt eine deutsche
Zeitleiste der RS485-Kommunikation aus: Request -> Antwort mit Latenz,
fun-Name, dekodierte Bedeutung, Retries/Timeouts, key8xx-Meldungen.

SICHERHEIT: Das Werkzeug SENDET NIE etwas und öffnet keinen Bus/Port.
Es parst nur Text. Kein pyserial-Import (serial485 importiert pyserial nur
lazy in Serial485.open(), das hier nie aufgerufen wird).

DATENSCHUTZ: SN (0x14), UUIDs (Auto-Addr 0xa0-0xa3, Motor 0x12) und der
RFID-Vendor-String (0x02, außer Material/Farbe/Länge) werden standardmäßig
maskiert; --unmask zeigt sie vollständig.

Logformat (Stock-klippy.log, aus der Protokollanalyse):
- Request je Sendung: `retries = N, cmd = 0x1 0x4 0xff 0xe 0x1`
  (ohne F7/CRC, unpadded %x; N zählt herunter, retries=4 -> bis 5 Sendungen)
- Ende je Sendung:    `cmd[0x1 0x4 0xff 0xe 0x1] get response or timeout`
- Antwort:            `Serial_485: got {'#msgid': n, '#msg': b'...'}` bzw.
                      `cmd_485_send_data_with_response params: 0xf7 ...` (Duplikat)
- Fehlschlag:         `do not get response, retries = N` / `... timeout` / key831
- Log-Uhr = UTC+8 (Druckerzeit), Boots ohne RTC beginnen bei 2020-01-01 08:00.

Dekodierung über die Nachbau-Module (keine Duplikate): box.py (STATUS_NAMES,
BOX_STATE_NAMES, parse_box_state, split_rfid_vendor, get_* / parse_*,
FUNC_*), cfsseq.py (EXTRUDE_MODES), serial485.py (parse_frame, crc8_cal),
motor.py (MOTOR_AXES, SYS_PARAM_NAMES, FUNC_*).

Standardausgabe unterdrückt Rauschen: Auto-Addr-Polls (0xa0-0xa3) und
unveränderte Wiederholungen von 0x0a/0x0e/0x03/0x08 (--all zeigt alles).
0x0a/0x15 nach FW-Analyse (CFS-FW): Temperatur/Feuchte (AHT30)
belegt, Flags = Motorblock (Bit 7 > 10 s, Bit 4 Hauptmotor, Bit 0..3
Feeder), Byte 5 = Typ-7-Tag-Sperre, 0x15 = Init-Flags der 6 Komponenten.
Kanal-A-Diagnose: „Hub-Maske“ in 0x0a muss nach Entfernen eines
Filamentrests leer werden (dauerhaft A = Hub-Sensor A blockiert B/C/D).
Odometer: bei 0x10 Modus 5 wird zusätzlich die seit dem Förderstart
(erste Modus-5-Abfrage nach Modus 4) geförderte Strecke angezeigt.

BEDIENUNG:
  ssh root@<drucker> 'tail -F /mnt/UDISK/printer_data/logs/klippy.log' | python3 cfswatch.py --local
  python3 cfswatch.py --slot A --only-errors klippy-kopie.log   # lokale Log-Kopie
  python3 cfswatch.py --selftest

Optionen: --slot A..D (Filter auf Slotmaske; globale Kommandos bleiben),
--only-errors, --since HH:MM | "JJJJ-MM-TT HH:MM" (in der angezeigten
Zeitzone; HH:MM rastet beim ersten Erreichen ein), --raw (Frames hex +
Datei:Zeile), --local (UTC+8 -> Europe/Berlin), --all, --no-motor,
--unmask, --no-color.
"""

import argparse
import ast
import datetime
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import box  # noqa: E402
import cfsseq  # noqa: E402
import motor  # noqa: E402
from serial485 import parse_frame, crc8_cal, BROADCAST_ADDRS  # noqa: E402

LOG_TZ = datetime.timezone(datetime.timedelta(hours=8))

# Host-Namen der fun-Codes (aus `cmd: …`-Zeilen des Stock-klippy.log)
HOST_FUN_NAMES = {
    box.FUNC_GET_RFID: "GET_RFID", box.FUNC_GET_REMAIN_LEN: "GET_REMAIN_LEN",
    box.FUNC_SET_BOX_MODE: "SET_BOX_MODE",
    box.FUNC_GET_BUFFER_STATE: "GET_BUFFER_STATE",
    box.FUNC_GET_FILAMENT_SENSOR_STATE: "GET_FILAMENT_SENSOR_STATE",
    box.FUNC_GET_BOX_STATE: "GET_BOX_STATE",
    box.FUNC_SET_PRE_LOADING: "SET_PRE_LOADING",
    box.FUNC_MEASURING_WHEEL: "MEASURING_WHEEL",
    box.FUNC_TIGHTEN_UP_ENABLE: "TIGHTEN_UP_ENABLE",
    box.FUNC_EXTRUDE: "EXTRUDE_PROCESS", box.FUNC_RETRUDE: "RETRUDE_PROCESS",
    box.FUNC_GET_VERSION_SN: "GET_VERSION_SN",
    box.FUNC_GET_HARDWARE_STATUS: "GET_HARDWARE_STATUS",
    0xA0: "AUTO_ADDR_SET", 0xA1: "AUTO_ADDR_DISCOVER", 0xA2: "AUTO_ADDR_GET",
    0xA3: "AUTO_ADDR_CHECK",
}
# Host-Statusnamen (live gesehen, `state: NAME[0x..]`), je (fun, status);
# weitere werden zur Laufzeit aus dem Log gelernt.
HOST_STATE_SEED = {
    (0x10, 0x05): "EXTRUDE_ERR1", (0x10, 0x0C): "EXTRUDE_ERR8",
    (0x11, 0x14): "RETRUDE_ERR2", (0x11, 0x19): "RETRUDE_ERR6",
    (0x0A, 0x51): "SPEED_ERR", (0x0A, 0x30): "UPDATE_STATE",
}
MOTOR_FUN_NAMES = {
    motor.FUNC_CONTROL: "Control", motor.FUNC_SYS_PARAM: "Sys-Param",
    motor.FUNC_PROTECTION: "Schutzstatus", motor.FUNC_ADDR: "Adresse",
    motor.FUNC_SET_MODE: "Stall-Mode", motor.FUNC_UUID: "UUID",
    motor.FUNC_FW_UPDATE: "FW-Update", motor.FUNC_VERSION: "Version",
    motor.FUNC_VARIABLE: "Variable",
    motor.FUNC_ERR_DETAIL_DATA: "Fehlerdetail", motor.FUNC_CUT_DETAIL_DATA: "Stalldetail",
    motor.FUNC_POS_DATA_SAMPLE: "Pos-Proben", motor.FUNC_SPD_DATA_SAMPLE: "Spd-Proben",
}
# 0x05 control (Motor-FW-Analyse, B; Handler 0xf880):
# nur Sub 04 (Status) antwortet, 01/02/03 bleiben by design stumm.
CONTROL_SUB_TEXT = {
    motor.CONTROL_OFF: "Regelung AUS", motor.CONTROL_ON: "Regelung EIN",
    motor.CONTROL_TOGGLE: "Regelung umschalten",
    motor.CONTROL_STATUS: "Status lesen (EN/Regler/PWM)",
}
AUTO_ADDR_FUNS = (0xA0, 0xA1, 0xA2, 0xA3)
# Bekannte Bus-Adressen (18.10.2026, Abgleich mit gitstonelabs): Boxen
# 0x01–0x04 (Auto-Addr, T1..T4), Motoren 0x81–0x84 + 0x85 (Box-Motor),
# Belt-Module 0x21/0x22, Broadcasts/Sonderadressen FE/FD/FC/FF/EA/EB
# (serial485.BROADCAST_ADDRS). Alles andere (z.B. Hi: A1 an 0xFB, 0x0a an
# 0x41, 0x02 an 0x11) wird als FREMD markiert und NICHT verworfen.
KNOWN_ADDRS = frozenset(set(range(0x01, 0x05)) | set(motor.MOTOR_ADDRS)
                        | {0x85, 0x21, 0x22} | set(BROADCAST_ADDRS))
FREMD_MARK = "[FREMD: Adresse 0x%02x unbekannt]"


def addr_unknown(addr):
    return addr not in KNOWN_ADDRS
DEDUP_FUNS = (box.FUNC_GET_BOX_STATE, box.FUNC_MEASURING_WHEEL,
              box.FUNC_GET_REMAIN_LEN, box.FUNC_GET_FILAMENT_SENSOR_STATE,
              box.FUNC_GET_BUFFER_STATE)
# Erwartet lange Antwortzeit -> Request sofort anzeigen (live-Betrieb)
LONG_HINT = {box.FUNC_SET_PRE_LOADING: 60, box.FUNC_EXTRUDE: 15,
             box.FUNC_RETRUDE: 30}

SEV_INFO, SEV_WARN, SEV_ERR = 0, 1, 2

# --- Log-Zeilen-Muster (nach einem lokalen Log-Parser, nicht im Repo) ---
R_TS = re.compile(r"^\[(\w+)\] (\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d{3}) "
                  r"\[\w+\] \[([^\]]*)\] (.*)$")
R_TX = re.compile(r"retries = (\d+), cmd = ((?:0x[0-9a-fA-F]+ ?)+)\s*$")
R_DONE = re.compile(r"cmd\[((?:0x[0-9a-fA-F]+ ?)+)\] get response or timeout")
R_GOT = re.compile(r"Serial_485: got \{'#msgid': (\d+), '#msg': "
                   r"(b'.*'|b\".*\"), '#name'")
R_PAR = re.compile(r"cmd_485_send_data_with_response params: "
                   r"((?:0x[0-9a-fA-F]+ ?)+)\s*$")
R_KEY = re.compile(r'"code"\s*:\s*"(key\d+)"\s*,\s*"msg"\s*:\s*"((?:[^"\\]|\\.)*)"'
                   r'(?:\s*,\s*"values"\s*:\s*(\[[^\]]*\]))?')
R_STATE = re.compile(r"\] state: ([A-Z][A-Z0-9_]*)\[0x([0-9a-fA-F]+)\]")
R_CRCERR = re.compile(r"b'(crc = 0x[0-9a-f]+, msgcrc8 = 0x[0-9a-f]+[^']*)'")
R_DISCARD = re.compile(r"b'(Discard bytes until next HEAD found)'")


def hx(s):
    return bytes(int(x, 16) for x in s.split())


def hexs(b):
    return " ".join("%02x" % x for x in b)


def de_num(v, nd=1):
    """Zahl deutsch formatieren (Komma)."""
    return ("%.*f" % (nd, v)).replace(".", ",")


def mask_slots(mask):
    if mask == box.MASK_ALL:
        return "alle"
    if not mask:
        return "keine"
    s = "+".join(k for i, k in enumerate(box.SLOT_KEYS) if mask & (1 << i))
    return s + (" (+0x%02x)" % (mask & 0xF0) if mask & 0xF0 else "")


def slot_word(mask):
    s = mask_slots(mask)
    return "Slot " + s if s not in ("alle", "keine") else "Slots " + s


# =====================================================================
# Maskierung
# =====================================================================

def mask_sn(sn, unmask):
    if unmask or len(sn) <= 5:
        return sn
    return sn[:5] + "*" * (len(sn) - 5)


def mask_bytes(b, keep, unmask):
    if unmask:
        return hexs(b)
    return " ".join("%02x" % x if i < keep else "**" for i, x in enumerate(b))


def masked_frame_hex(frame, unmask, is_req):
    """Rohframe hex, personenbezogene Teile maskiert (für --raw)."""
    if unmask or len(frame) < 6:
        return hexs(frame)
    addr, fun = frame[1], frame[4]
    keep_total = len(frame)
    if fun in AUTO_ADDR_FUNS:
        keep_total = 5 + (3 if (is_req and fun == 0xA0) else 2)
    elif addr == box.BOX_ADDR and fun == box.FUNC_GET_VERSION_SN and not is_req:
        keep_total = 5 + 3 + 5
    elif addr == box.BOX_ADDR and fun == box.FUNC_GET_RFID and not is_req:
        keep_total = 5 + 2
    elif addr in motor.MOTOR_ADDRS + (0x85,) and fun == motor.FUNC_UUID \
            and not is_req:
        keep_total = 5 + 2
    out = []
    for i, x in enumerate(frame):
        if i < keep_total or i == len(frame) - 1 and keep_total == len(frame):
            out.append("%02x" % x)
        elif i == len(frame) - 1:
            out.append("cc")  # CRC würde Rückschlüsse erlauben
        else:
            out.append("**")
    return " ".join(out)


# =====================================================================
# Dekodierung
# =====================================================================

class Decoder:
    """Request/Antwort -> Klartext. Hält Kontext (Odometer, Box-FW)."""

    def __init__(self, unmask=False):
        self.unmask = unmask
        self.box_fw = None
        self.odo = {}          # mask -> {"base": float|None, "max": float}
        self.host_state = dict(HOST_STATE_SEED)

    # --- Requests ---------------------------------------------------
    def request(self, addr, fun, data):
        """-> (text, slotmaske oder None, kategorie)"""
        if fun in AUTO_ADDR_FUNS:
            if addr_unknown(addr):
                return ("(Auto-Addr) " + FREMD_MARK % addr, None, "auto")
            return ("(Auto-Addr)", None, "auto")
        if addr in motor.MOTOR_ADDRS or addr == 0x85:
            return (self._motor_req(addr, fun, data), None, "motor")
        if addr in (0x21, 0x22):
            return ("Belt-Modul 0x%02x fun 0x%02x [%s]" % (addr, fun, hexs(data)),
                    None, "motor")
        if addr == 0xFF:
            name = MOTOR_FUN_NAMES.get(fun, "fun 0x%02x" % fun)
            return ("Broadcast %s [%s]" % (name, hexs(data)), None, "motor")
        if addr != box.BOX_ADDR:
            mark = (" " + FREMD_MARK % addr) if addr_unknown(addr) else ""
            return ("Adresse 0x%02x fun 0x%02x [%s]%s" % (
                addr, fun, hexs(data), mark), None, "other")
        return self._box_req(fun, data) + ("cfs",)

    def _box_req(self, fun, d):
        n = len(d)
        if fun in (box.FUNC_GET_RFID, box.FUNC_GET_REMAIN_LEN) and n >= 1:
            what = "RFID lesen" if fun == box.FUNC_GET_RFID else "Restmenge"
            return ("%s %s" % (what, slot_word(d[0])), d[0] or None)
        if fun == box.FUNC_SET_BOX_MODE and n >= 2:
            mode = {0: "PRINT", 1: "IDLE (löscht auch ERROR)"}.get(
                d[1], "Modus %d" % d[1])
            return ("Box-Modus %s%s" % (mode, (", " + slot_word(d[0]))
                                         if d[0] else ""), d[0] or None)
        if fun == box.FUNC_SET_PRE_LOADING and n >= 2:
            op = {box.PRELOAD_DISABLE: "Auto-Preload aus (global)",
                  box.PRELOAD_ENABLE: "Auto-Preload an (global)",
                  box.PRELOAD_FORCE_READ: "Force-Preload mit RFID",
                  box.PRELOAD_FORCE_TENSION_ONLY: "Force-Preload ohne RFID",
                  }.get(d[1], "op %d" % d[1])
            m = d[0] if d[1] in box.PRELOAD_FORCE_ACTIONS else None
            if d[1] in box.PRELOAD_FORCE_ACTIONS and d[0] == 0:
                where = "Maske 0x00 (= alle, laut Live-Befund)"
            elif d[1] in box.PRELOAD_FORCE_ACTIONS:
                where = slot_word(d[0])
            else:
                where = "Maske 0x%02x" % d[0]
            return ("%s, %s" % (op, where), m or None)
        if fun == box.FUNC_EXTRUDE and n >= 2:
            mode = d[1]
            txt = "%s Modus %d (%s)" % (slot_word(d[0]), mode,
                                        cfsseq.EXTRUDE_MODES.get(mode, "?"))
            if mode == cfsseq.EXTRUDE_MODE_EXTRUDER_CHECK and n >= 3:
                txt += " p=%d mm" % d[2]
            if mode == cfsseq.EXTRUDE_MODE_FEED_START:
                self.odo[d[0]] = {"base": None, "max": 0.0}
            return (txt, d[0] or None)
        if fun == box.FUNC_RETRUDE and n >= 2:
            p = {cfsseq.RETRUDE_P_TO_BUFFER: "p0 bis Buffer-Sensor",
                 cfsseq.RETRUDE_P_PULL_HUB: "p1 aus Hub zurückziehen"}.get(
                     d[1], "p%d" % d[1])
            return ("%s %s" % (slot_word(d[0]), p), d[0] or None)
        if fun == box.FUNC_GET_FILAMENT_SENSOR_STATE and n >= 1:
            return ({0: "Sensor A (Einlass)", 1: "Sensor B (Hub)"}.get(
                d[0], "Sensor %d" % d[0]), None)
        if fun == box.FUNC_TIGHTEN_UP_ENABLE and n >= 1:
            return ("Auto-Preload-Sperre %s" % ("an" if d[0] else "aus"), None)
        if fun == box.FUNC_MEASURING_WHEEL:
            return ("Messrad lesen [%s]" % hexs(d), None)
        if fun in (box.FUNC_GET_BOX_STATE, box.FUNC_GET_VERSION_SN,
                   box.FUNC_GET_BUFFER_STATE, box.FUNC_GET_HARDWARE_STATUS) \
                and not d:
            return ("", None)
        return ("[%s]" % hexs(d), None)

    def _motor_req(self, addr, fun, d):
        ax = motor.MOTOR_AXES.get(addr, "E" if addr == 0x85 else "?")
        name = MOTOR_FUN_NAMES.get(fun, "fun 0x%02x" % fun)
        txt = "Motor %s(0x%02x) %s" % (ax, addr, name)
        if fun == motor.FUNC_SYS_PARAM and d:
            if d[0] == motor.SUB_SYS_READ and len(d) >= 2:
                txt += " lesen ID %d %s" % (d[1], self._param_name(d[1]))
            elif d[0] == motor.SUB_SYS_WRITE and len(d) >= 6:
                (v,) = struct.unpack("<f", bytes(d[2:6]))
                txt += " schreiben ID %d %s = %g" % (d[1], self._param_name(d[1]), v)
            elif d[0] == motor.SUB_SYS_UPDATE:
                txt += " übernehmen"
            else:
                txt += " [%s]" % hexs(d)
        elif fun == motor.FUNC_SET_MODE and d:
            txt += " lesen" if d[0] == motor.SUB_STALL_GET else " setzen %d" % d[0]
        elif fun == motor.FUNC_VARIABLE and len(d) == 1:
            txt += " lesen idx %d" % d[0]
            if d[0] in motor.VARIABLE_NAMES:
                txt += " (%s)" % motor.VARIABLE_NAMES[d[0]]
            if not motor.VARIABLE_INDEX_MIN <= d[0] <= motor.VARIABLE_INDEX_MAX:
                txt += " (außerhalb 1..12 – Host-Crash bei MOTOR_GET DATA=0/13!)"
        elif fun == motor.FUNC_VERSION and list(d) == [motor.SUB_VERSION_GET]:
            txt += " abfragen"
        elif fun == motor.FUNC_CONTROL and d and d[0] in CONTROL_SUB_TEXT:
            # Motor-FW (B): nur Sub 04 antwortet
            txt += " %s [%s]" % (CONTROL_SUB_TEXT[d[0]], hexs(d))
        elif d:
            txt += " [%s]" % hexs(d)
        return txt

    @staticmethod
    def _param_name(pid):
        names = motor.SYS_PARAM_NAMES
        return "(%s)" % names[pid] if 0 <= pid < len(names) else ""

    # --- Antworten --------------------------------------------------
    def status_text(self, fun, st):
        host = self.host_state.get((fun, st))
        s = "Status 0x%02x = %s" % (st, box.status_name(st))
        return s + (" (Host: %s)" % host if host else "")

    def response(self, req_addr, req_fun, req_data, frame):
        """-> (text, severity, slotmaske oder None, dedup-key)"""
        try:
            fun, data = parse_frame(frame)
        except ValueError as e:
            return ("ungültiger Frame (%s)" % e, SEV_ERR, None, None)
        st, addr = frame[3], frame[1]
        if addr != box.BOX_ADDR or fun in AUTO_ADDR_FUNS:
            txt, sev = self._other_resp(addr, fun, data, req_data), SEV_INFO
            if addr in motor.MOTOR_ADDRS and st:
                # MOTOR-FW 16.10.: STATUS = Bitfeld (bit0 Stall, bit1/2 Codes)
                ms = motor.decode_motor_status(st)
                flags = [n for k, n in (("stall", "Stall"),
                                        ("err_latched", "Fehler latched"),
                                        ("warn_latched", "Warnung latched")) if ms[k]]
                if ms["other"]:
                    flags.append("Bits 0x%02x unbelegt" % ms["other"])
                txt += "; Motor-STATUS 0x%02x: %s" % (st, ", ".join(flags))
                sev = SEV_WARN
            if addr_unknown(addr):
                txt += " " + FREMD_MARK % addr
                sev = max(sev, SEV_WARN)
            return (txt, sev, None, None)
        sev = SEV_INFO
        slots = None
        parts = []
        if fun == box.FUNC_GET_BOX_STATE:
            return self._box_state(frame)
        if st not in (box.STATUS_OK,):
            sev = SEV_ERR
            parts.append(self.status_text(fun, st))
        elif not (fun == box.FUNC_EXTRUDE and len(data) == 4):
            parts.append("ok")
        dk = None
        try:
            if fun == box.FUNC_EXTRUDE:
                parts.extend(self._extrude_resp(req_data, data, st))
            elif fun == box.FUNC_MEASURING_WHEEL and len(data) == 4:
                v = box.get_measuring_wheel(req_data, data)
                parts.append("Messrad %s mm" % de_num(v))
                dk = round(v, 1)
            elif fun == box.FUNC_GET_REMAIN_LEN and len(data) == 4:
                rem = box.get_remain_len(req_data, data)
                parts.append("Rest " + ", ".join(
                    "%s=%s" % (k, "ohne RFID-Wert" if v is None else
                               ("0 (kein Filament/0 %)" if v == 0 else "%d %%" % v))
                    for k, v in rem.items()))
                dk = bytes(data)
            elif fun == box.FUNC_GET_RFID:
                parts.append(self._rfid(data))
            elif fun == box.FUNC_GET_VERSION_SN and data:
                ver, sn = box.parse_version_sn(data)
                self.box_fw = ver
                parts.append("Box-FW %s, SN %s" % (ver, mask_sn(sn, self.unmask)))
            elif fun == box.FUNC_GET_FILAMENT_SENSOR_STATE and len(data) == 1:
                m = box.parse_filament_sensor_state(data)
                which = "Hub" if req_data[:1] == b"\x01" else "Filament"
                parts.append("%s-Maske 0x%02x = %s" % (which, m, mask_slots(m)))
                dk = (bytes(req_data), m)
            elif fun == box.FUNC_GET_BUFFER_STATE and len(data) == 1:
                parts.append("Buffer 0x%02x (Semantik offen)" % data[0])
                dk = data[0]
            elif fun == box.FUNC_GET_HARDWARE_STATUS and len(data) == 16:
                hw = box.parse_get_hardware_status(data)
                if hw["all_ok"]:
                    parts.append("HW-Init alles ok (%s)" % ", ".join(
                        c["name"] for c in hw["components"]))
                else:
                    sev = max(sev, SEV_WARN)
                    parts.append("HW-Init FEHLER: " + ", ".join(
                        "%s %s" % (c["name"], "ok" if c["ok"] else
                                   ("Code %d" % c["code"] if c["code"] is not None
                                    else "Byte unplausibel"))
                        for c in hw["components"])
                        + (" [%s]" % hexs(hw["raw"])))
            elif data:
                parts.append("Daten [%s]" % hexs(data))
        except ValueError as e:
            parts.append("Daten [%s] (nicht dekodierbar: %s)" % (hexs(data), e))
            sev = max(sev, SEV_WARN)
        if fun in (box.FUNC_EXTRUDE, box.FUNC_RETRUDE) and req_data:
            slots = req_data[0] or None
        return ("; ".join(p for p in parts if p), sev, slots,
                None if dk is None else (fun, bytes(req_data), st, dk))

    def _extrude_resp(self, req_data, data, st):
        mode = req_data[1] if len(req_data) >= 2 else None
        mask = req_data[0] if req_data else 0
        out = []
        if mode == cfsseq.EXTRUDE_MODE_QUERY and len(data) == 4:
            (v,) = struct.unpack(">f", bytes(data))
            o = self.odo.get(mask)
            if o is not None:
                if o["base"] is None:
                    o["base"] = v
                delta = abs(v - o["base"])
                o["max"] = max(o["max"], delta)
                txt = "Odometer %s mm seit Förderstart" % de_num(delta)
                if o["max"] - delta >= 0.5:
                    txt += " (max %s mm)" % de_num(o["max"])
                txt += ", roh %s mm" % de_num(v)
            else:
                txt = "Odometer roh %s mm (Förderstart nicht im Log)" % de_num(v)
            if st == box.STATUS_ODOMETER_STALL:
                txt += " -> Förderung STEHT"
                if o is not None:
                    txt += " nach ~%s mm" % de_num(o["max"], 0)
            out.append(txt)
        elif mode == cfsseq.EXTRUDE_MODE_STAGE1 and len(data) == 1:
            out.append("Zustand %s" % box.BOX_STATE_NAMES.get(data[0], data[0]))
        elif data:
            out.append("Daten [%s]" % hexs(data))
        return out

    def _rfid(self, data):
        try:
            res = box.get_rfid(data)
        except ValueError:
            return "Daten [%s]" % hexs(data)
        if not res:
            return "keine RFID-Daten"
        items = []
        for k, v in res.items():
            if len(v) == box.RFID_VENDOR_LEN:
                try:
                    f = box.split_rfid_vendor(v)
                    txt = "Material %s, Farbe %s, Länge %s" % (
                        f["mat_id"], f["col"], f["len"])
                    txt += (", Vendor %s" % v) if self.unmask else \
                        ", Vendor [maskiert]"
                    items.append("%s: %s" % (k, txt))
                    continue
                except ValueError:
                    pass
            items.append("%s: %s" % (k, v if self.unmask or v in
                                     ("unknown", "busy") else "[maskiert]"))
        return "RFID " + "; ".join(items)

    def _box_state(self, frame):
        st = frame[3]
        try:
            r = box.parse_box_state(frame, self.box_fw)
        except ValueError as e:
            return ("0x0a nicht dekodierbar (%s)" % e, SEV_WARN, None, None)
        sev, slots = SEV_INFO, None
        if r["kind"] == "event":
            ev = [(k, r["event_names"][k]) for k, v in r["events"].items() if v]
            slots = sum(1 << box.SLOT_KEYS.index(k) for k, _ in ev) or None
            txt = "0x30 Ereignis: " + (", ".join(
                "%s Slot %s" % (n, k) for k, n in ev) or "keins")
            return (txt, sev, slots, None)
        if r["kind"] in ("state", "state_v113"):
            name = r["state_name"].replace("STATE_", "")
            parts = ["Box-Zustand %s" % name]
            if r["kind"] == "state":
                hub = r["hub_mask"]
                parts.append("Hub-Maske %s" % (mask_slots(hub) if hub else "leer"))
                slots = (hub | (r["flags"] & 0x0F)) or None
            else:
                parts.append("FW113-Format")
            if r["flags"]:
                fl = r["flags"]
                ft = "Flags 0x%02x" % fl
                if r["kind"] == "state":
                    # CFS-FW: Bit 7 = Block > 10 s, Bit 4 Hauptmotor,
                    # Bit 0..3 Feeder A..D
                    who = (["Hauptmotor"] if r["block_main_motor"] else []) + \
                        (["Feeder " + mask_slots(r["block_feeder_mask"])]
                         if r["block_feeder_mask"] else [])
                    ft += " (Motorblock%s: %s)" % (
                        " > 10 s" if r["block_long"] else "",
                        " + ".join(who) or "?")
                elif fl & 0x0F:
                    ft += " (%s)" % mask_slots(fl & 0x0F)
                parts.append(ft)
            if r["kind"] == "state" and r["inlet_tag_mask"]:
                parts.append("Typ-7-Tag-Sperre %s" % mask_slots(
                    r["inlet_tag_mask"]))
            if r["kind"] == "state":
                parts.append("Temp %d °C, Feuchte %d %%" % (r["temp_c"], r["rh"]))
            else:
                parts.append("Temp ~%d °C? Feuchte ~%d %%? (FW113, ungeprüft)"
                             % (r["temp_c_guess"], r["rh_guess"]))
            if st != box.STATUS_OK:
                parts.insert(1, self.status_text(box.FUNC_GET_BOX_STATE, st))
                sev = SEV_ERR
            dk = (box.FUNC_GET_BOX_STATE, st, bytes(r["raw"][2:]))
            return ("; ".join(parts), sev, slots, dk)
        return ("0x0a %s: [%s] Status 0x%02x" % (r["kind"], hexs(r["raw"]), st),
                SEV_WARN, None, None)

    def _other_resp(self, addr, fun, data, req_data=b""):
        if addr in motor.MOTOR_ADDRS and fun == motor.FUNC_CONTROL \
                and len(data) == motor.CONTROL_STATUS_LEN \
                and req_data[:1] == bytes([motor.CONTROL_STATUS]):
            # MOTOR-RESTE §1: [obj+0x7a6 EN-Pin (1 = gesperrt), ctrl+4, drv+4]
            return "Control-Status: EN-Pin %d, Regler %s, PWM %s" % (
                data[0], "ein" if data[1] else "aus", "ein" if data[2] else "aus")
        if addr in motor.MOTOR_ADDRS and fun == motor.FUNC_VERSION \
                and len(data) == motor.VERSION_LEN:
            v = motor.decode_motor_version(data)
            return ("Version fw %s, cmd_system %s, protection %s, flash_param %s, "
                    "logger %s" % tuple(v[n] for n, _ in motor.VERSION_FIELDS))
        if addr in motor.MOTOR_ADDRS and fun == motor.FUNC_VARIABLE \
                and len(data) == motor.VARIABLE_LEN and len(req_data) == 1 \
                and req_data[0] in motor.VARIABLE_TYPES:
            r = motor.decode_variable(req_data[0], data)
            return "Variable idx %d%s = %s%s (%s; float %g / int %d)" % (
                r["index"], " %s" % r["name"] if r["name"] else "",
                ("%g" % r["value"]) if r["type"] == "float"
                else r["value"], " %s" % r["unit"] if r["unit"] else "",
                r["type"], r["float"], r["int"])
        if fun in AUTO_ADDR_FUNS:
            return "Auto-Addr-Antwort [%s]" % mask_bytes(data, 2, self.unmask)
        if fun == motor.FUNC_UUID and len(data) == motor.UUID_LEN:
            return "UUID %s" % mask_bytes(data, 2, self.unmask)
        if fun == motor.FUNC_SYS_PARAM and len(data) == 4:
            return "Wert %g" % struct.unpack("<f", bytes(data))[0]
        if fun == motor.FUNC_PROTECTION and len(data) == motor.PROTECTION_LEN:
            if not any(data):
                return "Schutzstatus ok (8x00)"
            p = motor.decode_protection(data)
            return "Schutzstatus Fehler 0x%08x [%s], Warnung 0x%08x [%s]" % (
                p["err_code"], ", ".join("%d %s" % (b, t) for b, _k, t in p["errors"]),
                p["warning_code"],
                ", ".join("%d %s" % (b, t) for b, _k, t in p["warnings"]))
        if addr in motor.MOTOR_ADDRS and fun in (motor.FUNC_ERR_DETAIL_DATA,
                                                 motor.FUNC_CUT_DETAIL_DATA) \
                and len(data) == motor.DETAIL_LEN:
            d = motor.decode_err_detail(data, cut=fun == motor.FUNC_CUT_DETAIL_DATA)
            return "%s err_code 0x%x [%s], %g V, %g °C, pos-PID-Fehler %g" % (
                "Stalldetail" if fun == motor.FUNC_CUT_DETAIL_DATA else "Fehlerdetail",
                d["err_code"], ", ".join(t for _b, _k, t in d["errors"]),
                d["board_detect_power_supply_V"], d["board_detect_temperature_C"],
                d["pos_loop_pid_err"])
        return "[%s]" % hexs(data) if data else "ok"


# =====================================================================
# Monitor
# =====================================================================

class Txn:
    __slots__ = ("body", "addr", "fun", "data", "t", "ts", "retries",
                 "attempt", "t0", "rx", "rx_t", "closed", "loc", "frame",
                 "desc", "slots", "cat", "announced")


class Monitor:
    def __init__(self, slot=None, only_errors=False, since=None, raw=False,
                 local=False, show_all=False, no_motor=False, unmask=False,
                 color=False):
        self.slot_mask = None if slot is None else (1 << box.SLOT_KEYS.index(slot))
        self.only_errors = only_errors
        self.raw, self.local, self.all = raw, local, show_all
        self.no_motor, self.unmask, self.color = no_motor, unmask, color
        self.dec = Decoder(unmask)
        self.cur = None
        self.last_unanswered = None
        self.recent_rx = []     # [(frame, t)] für params-Duplikate
        self.last_resp_fun = None
        self.dedup = {}
        self.last_key = (None, 0.0)
        self.since_abs, self.since_tod, self.since_ok = None, None, since is None
        if since:
            if re.fullmatch(r"\d\d?:\d\d", since):
                h, m = map(int, since.split(":"))
                self.since_tod = datetime.time(h, m)
            else:
                self.since_abs = datetime.datetime.strptime(since, "%Y-%m-%d %H:%M")
        self.out = []

    # --- Zeit ---
    def _disp_time(self, dt):
        if self.local:
            try:
                from zoneinfo import ZoneInfo
                dt = dt.replace(tzinfo=LOG_TZ).astimezone(
                    ZoneInfo("Europe/Berlin")).replace(tzinfo=None)
            except Exception:  # keine tzdata -> feste MESZ-Annahme
                dt = dt - datetime.timedelta(hours=6)
        return dt

    def _since_pass(self, dt):
        if self.since_ok:
            return True
        if self.since_abs is not None:
            return dt >= self.since_abs
        if dt.time() >= self.since_tod:
            self.since_ok = True
        return self.since_ok

    # --- Ausgabe ---
    def emit(self, dt, sev, text, slots=None, cat="cfs", raw=None, loc=None):
        if cat == "auto" and not self.all:
            return
        if cat == "motor" and (self.no_motor or self.slot_mask):
            return
        if self.only_errors and sev < SEV_WARN:
            return
        if self.slot_mask and slots is not None and not (slots & self.slot_mask):
            return
        d = self._disp_time(dt)
        if not self._since_pass(d):
            return
        mark = {SEV_INFO: "  ", SEV_WARN: "~ ", SEV_ERR: "!!"}[sev]
        line = "%s %s %s" % (d.strftime("%d.%m. %H:%M:%S.") +
                             "%03d" % (d.microsecond // 1000), mark, text)
        if self.raw and (raw or loc):
            line += "   [%s]" % " @".join(x for x in (raw, loc) if x)
        if self.color:
            c = {SEV_WARN: "\033[33m", SEV_ERR: "\033[1;31m"}.get(sev)
            if c:
                line = c + line + "\033[0m"
        self.out.append(line)

    def flush(self):
        o, self.out = self.out, []
        return o

    # --- Zeilen ---
    def feed(self, line, loc=None):
        m = R_TS.match(line.rstrip("\n"))
        if not m:
            return
        _lvl, ts, src, msg = m.groups()
        try:
            dt = datetime.datetime.strptime(ts, "%Y-%m-%d %H:%M:%S,%f")
        except ValueError:
            return
        t = dt.timestamp()
        mm = R_TX.search(msg)
        if mm and "do not get response" not in msg:
            return self._tx(dt, t, int(mm.group(1)), hx(mm.group(2)), loc)
        mm = R_GOT.search(msg)
        if mm:
            try:
                fr = ast.literal_eval(mm.group(2))
            except (ValueError, SyntaxError):
                return
            fr = bytes(fr)
            self.recent_rx = [(f, u) for f, u in self.recent_rx
                              if t - u < 1.0][-7:] + [(fr, t)]
            return self._rx(dt, t, fr, loc)
        mm = R_PAR.search(msg)
        if mm:
            fr = hx(mm.group(1))
            if len(fr) > 4:
                self.last_resp_fun = fr[4]
            for i, (f, u) in enumerate(self.recent_rx):
                if f == fr and t - u < 1.0:
                    del self.recent_rx[i]
                    return  # Duplikat einer got-Zeile
            return self._rx(dt, t, fr, loc)
        mm = R_DONE.search(msg)
        if mm:
            return self._done(dt, t, hx(mm.group(1)), loc)
        mm = R_STATE.search(msg)
        if mm and self.last_resp_fun is not None:
            self.dec.host_state.setdefault(
                (self.last_resp_fun, int(mm.group(2), 16)), mm.group(1))
            return
        mm = R_KEY.search(msg)
        if mm:
            return self._key(dt, t, msg, mm, loc)
        mm = R_CRCERR.search(msg) or R_DISCARD.search(msg)
        if mm:
            self.emit(dt, SEV_ERR, "Bus: %s (Host verwirft Bytes)" % mm.group(1),
                      cat="cfs", loc=loc)

    def _tx(self, dt, t, retries, body, loc):
        if len(body) < 4:
            return
        prev = self.last_unanswered
        x = Txn()
        x.body, x.addr, x.fun, x.data = body, body[0], body[3], body[4:]
        x.t, x.ts, x.retries, x.loc = t, dt, retries, loc
        x.rx, x.rx_t, x.closed, x.announced = None, None, False, False
        x.frame = bytes([0xF7]) + body + bytes([crc8_cal(list(body[1:]))])
        if prev is not None and prev.body == body and prev.retries == retries + 1:
            x.attempt, x.t0 = prev.attempt + 1, prev.t0
            x.desc, x.slots, x.cat = prev.desc, prev.slots, prev.cat
        else:
            x.attempt, x.t0 = 1, t
            x.desc, x.slots, x.cat = self.dec.request(x.addr, x.fun, x.data)
        self.last_unanswered = None
        self.cur = x
        long_s = LONG_HINT.get(x.fun) if x.addr == box.BOX_ADDR else None
        if long_s and x.attempt == 1 and not self._quick(x):
            x.announced = True
            self.emit(dt, SEV_INFO, "%s -> gesendet, Antwort kann bis ~%d s dauern"
                      % (self._head(x), long_s), x.slots, x.cat,
                      self._raw_tx(x), loc)

    @staticmethod
    def _quick(x):
        d = x.data
        if x.fun == box.FUNC_SET_PRE_LOADING:
            return len(d) < 2 or d[1] not in box.PRELOAD_FORCE_ACTIONS
        if x.fun == box.FUNC_EXTRUDE:
            return len(d) < 2 or d[1] != cfsseq.EXTRUDE_MODE_STAGE1
        if x.fun == box.FUNC_RETRUDE:
            return len(d) < 2 or d[1] != cfsseq.RETRUDE_P_PULL_HUB
        return True

    def _head(self, x):
        if x.addr == box.BOX_ADDR or x.fun in AUTO_ADDR_FUNS:
            name = HOST_FUN_NAMES.get(x.fun, "fun 0x%02x" % x.fun)
            h = "0x%02x %s" % (x.fun, name)
            return (h + " " + x.desc) if x.desc else h
        return x.desc

    def _raw_tx(self, x):
        return "TX " + masked_frame_hex(x.frame, self.unmask, True)

    def _rx(self, dt, t, fr, loc):
        x = self.cur
        if len(fr) < 6:
            return
        if x is not None and x.rx is None and not x.closed and fr[4] == x.fun \
                and (fr[1] == x.addr or x.addr in (0xFE, 0xFF)):
            x.rx, x.rx_t = fr, t
            text, sev, slots, dk = self.dec.response(x.addr, x.fun, x.data, fr)
            if slots is None:
                slots = x.slots
            lat = (t - x.t) * 1000.0
            lat_s = ("%d ms" % round(lat)) if lat < 1000 else ("%s s" % de_num(lat / 1000.0))
            retr = ""
            if x.attempt > 1:
                retr = " (erst im Versuch %d beantwortet)" % x.attempt
                sev = max(sev, SEV_WARN)
            if dk is not None and x.attempt == 1 and not self.all \
                    and x.fun in DEDUP_FUNS and x.addr == box.BOX_ADDR:
                if self.dedup.get(dk[:2] if x.fun != box.FUNC_GET_BOX_STATE
                                  else dk[0]) == dk:
                    return
                self.dedup[dk[:2] if x.fun != box.FUNC_GET_BOX_STATE else dk[0]] = dk
            raw = "TX %s | RX %s" % (masked_frame_hex(x.frame, self.unmask, True),
                                     masked_frame_hex(fr, self.unmask, False))
            self.emit(dt, sev, "%s -> %s: %s%s" % (self._head(x), lat_s, text, retr),
                      slots, x.cat, raw, loc)
        else:
            text, sev, slots, _dk = self.dec.response(fr[1], fr[4], b"", fr)
            cat = "auto" if fr[4] in AUTO_ADDR_FUNS else (
                "cfs" if fr[1] == box.BOX_ADDR else "motor")
            self.emit(dt, max(sev, SEV_INFO), "Antwort ohne passenden Request "
                      "(addr 0x%02x fun 0x%02x): %s" % (fr[1], fr[4], text),
                      slots, cat, "RX " + masked_frame_hex(fr, self.unmask, False), loc)

    def _done(self, dt, t, body, loc):
        x = self.cur
        if x is None or x.closed or body != x.body:
            return
        x.closed = True
        if x.rx is not None:
            return
        self.last_unanswered = x
        total = x.attempt + x.retries
        silent = self._known_silent(x)
        sev = SEV_INFO if silent else (SEV_WARN if x.retries else SEV_ERR)
        if x.retries:
            txt = "keine Antwort auf Versuch %d/%d nach %d ms, Wiederholung folgt" % (
                x.attempt, total, round((t - x.t) * 1000))
        else:
            txt = "KEINE ANTWORT – endgültig nach %d Sendung%s in %s s (Timeout)" % (
                total, "en" if total > 1 else "", de_num(t - x.t0, 2))
            if silent:
                txt = "keine Antwort (erwartet/by design)"
        self.emit(dt, sev, "%s -> %s" % (self._head(x), txt), x.slots, x.cat,
                  self._raw_tx(x), loc)

    @staticmethod
    def _known_silent(x):
        if x.fun in (0xA0, 0xA1):
            return True
        if x.addr == 0x85 or x.addr == 0xFF:
            return True
        # 0x05 (MOTOR-RESTE §1, B): Sub 01/02 (und 00/03/>=05) antworten
        # NIE – „erwartet“. Sub 04 (Status lesen) antwortet mit 3 B, dort
        # ist ein Ausbleiben ein echter Fehler (nicht stumm schalten).
        if x.addr in motor.MOTOR_ADDRS and (
                (x.fun == motor.FUNC_CONTROL and x.data[:1] not in
                 (b"", bytes([motor.CONTROL_STATUS]))) or
                (x.fun == motor.FUNC_PROTECTION and x.data[:1] ==
                 bytes([motor.SUB_PROT_UNKNOWN_05]))):
            return True
        return False

    def _key(self, dt, t, msg, mm, loc):
        code, text, values = mm.group(1), mm.group(2), mm.group(3) or ""
        same = "the same err" in msg
        k = (code, text, values)
        if (same or (self.last_key[0] == k and t - self.last_key[1] < 2.0)) \
                and not self.all:
            self.last_key = (k, t)
            return
        self.last_key = (k, t)
        slots = 0
        for s in re.findall(r'"([A-D])"', values):
            slots |= 1 << box.SLOT_KEYS.index(s)
        is_cfs = code.startswith("key8")
        sev = SEV_ERR if is_cfs else SEV_WARN
        txt = "%s%s: %s%s" % ("FEHLER " if is_cfs else "Meldung ", code, text,
                              (" %s" % values) if values else "")
        if same:
            txt += " (Wiederholung)"
        self.emit(dt, sev, txt, slots or None, "cfs", None, loc)


# =====================================================================
# Selbsttest (echte Logzeilen; SN/UUID/Vendor durch Platzhalter ersetzt,
# CRC dafür neu berechnet)
# =====================================================================

SELFTEST_LOG = r"""
[INFO] 2026-10-05 02:12:10,000 [root] [reactor:_check_timers:168] retries = 4, cmd = 0x1 0x4 0xff 0xe 0x1
[INFO] 2026-10-05 02:12:12,010 [root] [reactor:_check_timers:168] cmd[0x1 0x4 0xff 0xe 0x1] get response or timeout
[WARNING] 2026-10-05 02:12:12,012 [root] [reactor:_check_timers:168] do not get response, retries = 4, cmd = b'\x01\x04\xff\x0e\x01'
[INFO] 2026-10-05 02:12:12,020 [root] [reactor:_check_timers:168] retries = 3, cmd = 0x1 0x4 0xff 0xe 0x1
[INFO] 2026-10-05 02:12:14,030 [root] [reactor:_check_timers:168] cmd[0x1 0x4 0xff 0xe 0x1] get response or timeout
[INFO] 2026-10-05 02:12:14,040 [root] [reactor:_check_timers:168] retries = 2, cmd = 0x1 0x4 0xff 0xe 0x1
[INFO] 2026-10-05 02:12:16,050 [root] [reactor:_check_timers:168] cmd[0x1 0x4 0xff 0xe 0x1] get response or timeout
[INFO] 2026-10-05 02:12:16,060 [root] [reactor:_check_timers:168] retries = 1, cmd = 0x1 0x4 0xff 0xe 0x1
[INFO] 2026-10-05 02:12:18,070 [root] [reactor:_check_timers:168] cmd[0x1 0x4 0xff 0xe 0x1] get response or timeout
[INFO] 2026-10-05 02:12:18,080 [root] [reactor:_check_timers:168] retries = 0, cmd = 0x1 0x4 0xff 0xe 0x1
[INFO] 2026-10-05 02:12:20,090 [root] [reactor:_check_timers:168] cmd[0x1 0x4 0xff 0xe 0x1] get response or timeout
[INFO] 2026-10-05 02:12:20,092 [root] [gcode:<lambda>:186] cmd_485_send_data_with_response timeout
[INFO] 2026-10-05 02:12:20,094 [root] [gcode:<lambda>:186] !! {"code":"key831", "msg":"serial_485 communication timeout", "values": [1]}
[INFO] 2026-10-13 03:33:59,673 [root] [reactor:_check_timers:168] retries = 4, cmd = 0x1 0x6 0xff 0x10 0x1 0x0 0x0
[WARNING] 2026-10-13 03:34:04,213 [root] [threading:run:910] Serial_485: got {'#msgid': 4, '#msg': b'\xf7\x01\x04\x00\x10\x00\x0f', '#name': '#unknown'}
[INFO] 2026-10-13 03:34:04,215 [root] [reactor:_check_timers:168] cmd[0x1 0x6 0xff 0x10 0x1 0x0 0x0] get response or timeout
[INFO] 2026-10-13 03:34:04,265 [root] [reactor:_check_timers:168] retries = 4, cmd = 0x1 0x6 0xff 0x10 0x1 0x4 0x0
[WARNING] 2026-10-13 03:34:04,270 [root] [threading:run:910] Serial_485: got {'#msgid': 3, '#msg': b'\xf7\x01\x03\x00\x10\xcd', '#name': '#unknown'}
[INFO] 2026-10-13 03:34:04,272 [root] [reactor:_check_timers:168] cmd[0x1 0x6 0xff 0x10 0x1 0x4 0x0] get response or timeout
[INFO] 2026-10-13 03:34:04,285 [root] [reactor:_check_timers:168] retries = 4, cmd = 0x1 0x6 0xff 0x10 0x1 0x5 0x0
[INFO] 2026-10-13 03:34:04,292 [root] [gcode:<lambda>:186] cmd_485_send_data_with_response params: 0xf7 0x1 0x7 0x0 0x10 0xc4 0xda 0x9f 0xb1 0x47
[INFO] 2026-10-13 03:34:04,294 [root] [reactor:_check_timers:168] cmd[0x1 0x6 0xff 0x10 0x1 0x5 0x0] get response or timeout
[INFO] 2026-10-13 03:34:06,400 [root] [reactor:_check_timers:168] retries = 4, cmd = 0x1 0x6 0xff 0x10 0x1 0x5 0x0
[WARNING] 2026-10-13 03:34:06,418 [root] [threading:run:910] Serial_485: got {'#msgid': 7, '#msg': b'\xf7\x01\x07\x00\x10\xc4\xf8\xc1I\xee', '#name': '#unknown'}
[INFO] 2026-10-13 03:34:06,419 [root] [gcode:<lambda>:186] cmd_485_send_data_with_response params: 0xf7 0x1 0x7 0x0 0x10 0xc4 0xf8 0xc1 0x49 0xee
[INFO] 2026-10-13 03:34:06,420 [root] [reactor:_check_timers:168] cmd[0x1 0x6 0xff 0x10 0x1 0x5 0x0] get response or timeout
[INFO] 2026-10-13 03:34:08,434 [root] [reactor:_check_timers:168] retries = 4, cmd = 0x1 0x6 0xff 0x10 0x1 0x5 0x0
[WARNING] 2026-10-13 03:34:08,439 [root] [threading:run:910] Serial_485: got {'#msgid': 7, '#msg': b'\xf7\x01\x07\x0c\x10\xc4\xf8)0\x86', '#name': '#unknown'}
[INFO] 2026-10-13 03:34:08,441 [root] [reactor:_check_timers:168] cmd[0x1 0x6 0xff 0x10 0x1 0x5 0x0] get response or timeout
[INFO] 2026-10-13 03:34:08,446 [root] [gcode:<lambda>:186] state: EXTRUDE_ERR8[0xc]
[INFO] 2026-10-13 03:34:50,029 [root] [gcode:<lambda>:186] !! {"code":"key836", "msg":"extrude error, maybe there's a blockage between the connections and the filament sensor", "values": [1, "A"]}
[INFO] 2026-10-13 03:34:52,366 [root] [reactor:_check_timers:168] retries = 0, cmd = 0x1 0x3 0xff 0xa
[WARNING] 2026-10-13 03:34:52,371 [root] [threading:run:910] Serial_485: got {'#msgid': 9, '#msg': b'\xf7\x01\t\x0c\n\x170\x01\x04\x01\x00\x80', '#name': '#unknown'}
[INFO] 2026-10-13 03:34:52,373 [root] [reactor:_check_timers:168] cmd[0x1 0x3 0xff 0xa] get response or timeout
[INFO] 2026-10-13 03:45:28,198 [root] [reactor:_check_timers:168] retries = 0, cmd = 0x1 0x3 0xff 0xa
[WARNING] 2026-10-13 03:45:28,202 [root] [threading:run:910] Serial_485: got {'#msgid': 7, '#msg': b'\xf7\x01\x070\n\x01\x00\x00\x00}', '#name': '#unknown'}
[INFO] 2026-10-13 03:45:28,204 [root] [reactor:_check_timers:168] cmd[0x1 0x3 0xff 0xa] get response or timeout
[INFO] 2026-10-12 00:28:37,839 [root] [reactor:_check_timers:168] retries = 0, cmd = 0x1 0x3 0xff 0xa
[WARNING] 2026-10-12 00:28:37,844 [root] [threading:run:910] Serial_485: got {'#msgid': 9, '#msg': b'\xf7\x01\tQ\n\x115\x00\x04\x01\x00\xa1', '#name': '#unknown'}
[INFO] 2026-10-12 00:28:37,875 [root] [reactor:_check_timers:168] cmd[0x1 0x3 0xff 0xa] get response or timeout
[INFO] 2026-10-12 00:28:37,887 [root] [reactor:invoke:53] !! {"code":"key846", "msg":"empty printing, box speed is smaller than extruder", "values": []}
[INFO] 2026-10-12 00:25:52,466 [root] [reactor:_check_timers:168] retries = 0, cmd = 0x1 0x3 0xff 0xa
[WARNING] 2026-10-12 00:25:52,471 [root] [threading:run:910] Serial_485: got {'#msgid': 9, '#msg': b'\xf7\x01\t\x00\n\x115\x00\x02\x01\x00$', '#name': '#unknown'}
[INFO] 2026-10-12 00:25:52,473 [root] [reactor:_check_timers:168] cmd[0x1 0x3 0xff 0xa] get response or timeout
[INFO] 2026-10-12 00:57:50,600 [root] [reactor:_check_timers:168] retries = 4, cmd = 0x1 0x4 0xff 0x2 0x1
[WARNING] 2026-10-12 00:57:50,684 [root] [threading:run:910] Serial_485: got {'#msgid': 46, '#msg': b'\xf7\x01.\x00\x02A:101250000001010010FFFFFF0000000000000000;\xd7', '#name': '#unknown'}
[INFO] 2026-10-12 00:57:50,690 [root] [reactor:_check_timers:168] cmd[0x1 0x4 0xff 0x2 0x1] get response or timeout
[INFO] 2026-10-13 00:10:00,000 [root] [reactor:_check_timers:168] retries = 4, cmd = 0x1 0x3 0xff 0x14
[WARNING] 2026-10-13 00:10:00,006 [root] [threading:run:910] Serial_485: got {'#msgid': 24, '#msg': b'\xf7\x01\x18\x00\x14153SN-DEPLATZHALTER00B', '#name': '#unknown'}
[INFO] 2026-10-13 00:10:00,008 [root] [reactor:_check_timers:168] cmd[0x1 0x3 0xff 0x14] get response or timeout
[INFO] 2026-10-13 00:10:01,000 [root] [reactor:_check_timers:168] retries = 4, cmd = 0x1 0x3 0xff 0x15
[WARNING] 2026-10-13 00:10:01,006 [root] [threading:run:910] Serial_485: got {'#msgid': 25, '#msg': b'\xf7\x01\x13\x00\x15\x10 0@P`\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\xad', '#name': '#unknown'}
[INFO] 2026-10-13 00:10:01,008 [root] [reactor:_check_timers:168] cmd[0x1 0x3 0xff 0x15] get response or timeout
[INFO] 2026-10-13 03:07:10,098 [root] [reactor:_check_timers:168] retries = 0, cmd = 0x1 0x3 0x0 0xa2
[WARNING] 2026-10-13 03:07:10,103 [root] [threading:run:910] Serial_485: got {'#msgid': 17, '#msg': b'\xf7\x01\x11\x00\xa2\x01\x00\xa0\xa1\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00%', '#name': '#unknown'}
[INFO] 2026-10-13 03:07:10,108 [root] [reactor:_check_timers:168] cmd[0x1 0x3 0x0 0xa2] get response or timeout
[ERROR] 2026-10-05 03:09:49,169 [root] [__init__:logging_callback:312] b'crc = 0x99, msgcrc8 = 0xa3, buf_len = 0x14'
[INFO] 2026-10-16 01:15:42,629 [root] [reactor:_check_timers:168] retries = 0, cmd = 0x81 0x4 0x0 0x12 0x2
[INFO] 2026-10-16 01:15:43,632 [root] [reactor:_check_timers:168] cmd[0x81 0x4 0x0 0x12 0x2] get response or timeout
""".strip("\n").splitlines()


def selftest():
    fails = []

    def check(cond, what):
        if not cond:
            fails.append(what)

    mon = Monitor(show_all=True, raw=True)
    for ln in SELFTEST_LOG:
        mon.feed(ln, "selftest")
    out = mon.flush()
    txt = "\n".join(out)
    print(txt)
    print("-" * 60)
    # Retry-Kette 0x0e: 4 Wiederholungs-Hinweise + endgültiger Timeout nach 5
    check(txt.count("Wiederholung folgt") == 4, "Retry-Kette: 4 Zwischenversuche")
    check("Versuch 1/5" in txt and "Versuch 4/5" in txt, "Retry-Zählung 1..4/5")
    check("endgültig nach 5 Sendungen in 10,09 s" in txt, "Timeout nach 5 Sendungen")
    check("FEHLER key831" in txt, "key831 hervorgehoben")
    # 0x10 Modus 0 (lang, angekündigt), Modus 4 und 5 mit Odometer
    check("Modus 0 (stage1" in txt and "gesendet, Antwort kann" in txt,
          "0x10 Modus 0 angekündigt")
    check("Slot A Modus 4 (Dauerfördern" in txt, "0x10 Modus 4 Slot A")
    check("Odometer 0,0 mm seit Förderstart, roh -1749,0 mm" in txt,
          "Odometer-Basis")
    check("Odometer 241,0 mm seit Förderstart, roh -1990,0 mm" in txt,
          "Odometer 241 mm")
    check("Status 0x0c = Odometer-Stillstand 500 ms (Host: EXTRUDE_ERR8)" in txt,
          "Status 0x0c")
    check("(max 241,0 mm)" in txt and "STEHT nach ~241 mm" in txt,
          "Stillstand nach ~241 mm")
    check("FEHLER key836" in txt and '[1, "A"]' in txt, "key836 Slot A")
    # 0x0a ERROR mit Fehlercode 0x0c, Flags/Hub
    check("Box-Zustand ERROR; Status 0x0c" in txt and "Hub-Maske A" in txt,
          "0x0a ERROR 0x0c Hub A")
    check("Temp 23 °C, Feuchte 48 %" in txt and "Vermutung" not in txt,
          "0x0a Temp/Feuchte")
    check("Box-Zustand PRINT; Hub-Maske A; Temp 17 °C, Feuchte 53 %" in txt,
          "0x0a PRINT")
    check("HW-Init alles ok (EEPROM, AHT30 Temp/Feuchte, Odometer MT6826, "
          "Buffer-Sensor, RFID-Leser 0, RFID-Leser 1)" in txt, "0x15 dekodiert")
    # 0x30-Ereignis und 0x51
    check("0x30 Ereignis: Einstecken Slot A" in txt, "0x30 Ereignis")
    check("Status 0x51 = SPEED_ERR: Buffer-Nachschub zu langsam (Host: SPEED_ERR)"
          in txt, "0x51 SPEED_ERR")
    # Maskierung
    check("Material 101001, Farbe 0FFFFFF, Länge 0000, Vendor [maskiert]" in txt,
          "RFID-Vendor maskiert")
    check("SN SN-DE*************" in txt and "PLATZHALTER" not in txt,
          "SN maskiert")
    check("Auto-Addr-Antwort [01 00 ** **" in txt, "UUID maskiert")
    check("Bus: crc = 0x99" in txt, "CRC-Fehler gemeldet")
    check("Motor X(0x81) UUID [02] -> KEINE ANTWORT" in txt, "Motor-Name")
    # Motor 0x0F/0x08 (Live-Frames 16.10.2026, Motor X) direkt am Decoder
    dm = Decoder()
    tv = dm.response(0x81, motor.FUNC_VERSION, b"\x00", hx(
        "f7 81 0f 00 0f 01 09 07 51 01 03 01 03 02 4b 01 00 02"))[0]
    check(tv == "Version fw 1.9.7.81, cmd_system 1.3, protection 1.3, "
          "flash_param 2.75, logger 1.0", "Motor 0x0F Version: " + tv)
    t9 = dm.response(0x81, motor.FUNC_VARIABLE, b"\x09",
                     hx("f7 81 07 00 08 33 33 d3 3a e6"))[0]
    t11 = dm.response(0x81, motor.FUNC_VARIABLE, b"\x0b",
                      hx("f7 81 07 00 08 52 04 00 00 71"))[0]
    check(t9.startswith("Variable idx 9 ia = 0.00161133 A (float") and
          t11.startswith("Variable idx 11 pos_counts = 1106 counts (int"),
          "Motor 0x08: %s | %s" % (t9, t11))
    t5 = dm.response(0x81, motor.FUNC_VARIABLE, b"\x05",
                     hx("f7 81 07 00 08 00 00 00 00 0a"))[0]
    check(t5.startswith("Variable idx 5 pos_fdb = 0 rad (float")
          and dm.request(0x81, motor.FUNC_VARIABLE, b"\x08")[0]
          == "Motor X(0x81) Variable lesen idx 8 (iq_fdb)", "Motor 0x08 Namen: " + t5)
    tq = dm.request(0x81, motor.FUNC_VARIABLE, b"\x0b")[0]
    tq0 = dm.request(0x81, motor.FUNC_VARIABLE, b"\x0d")[0]
    check(tq == "Motor X(0x81) Variable lesen idx 11 (pos_counts)" and "Host-Crash" in tq0
          and dm.request(0x81, motor.FUNC_VERSION, b"\x00")[0]
          == "Motor X(0x81) Version abfragen", "Motor 0x08/0x0F-Request")
    # MOTOR-FW 16.10.: Einheit spd_fdb, DIR-Eingang, 0x0C dekodiert, STATUS-Bits
    from serial485 import build_frame as _bf

    def build_frame_m(addr, fun, data, status):
        return _bf(addr, fun, data, status=status)
    t6 = dm.response(0x81, motor.FUNC_VARIABLE, b"\x06", build_frame_m(
        0x81, motor.FUNC_VARIABLE, list(struct.pack("<f", 1.5)), 0))[0]
    t12 = dm.response(0x81, motor.FUNC_VARIABLE, b"\x0c", build_frame_m(
        0x81, motor.FUNC_VARIABLE, [1, 0, 0, 0], 0))[0]
    check(t6.startswith("Variable idx 6 spd_fdb = 1.5 rad/s (float")
          and t12.startswith("Variable idx 12 dir_input = 1 0/1 (int")
          and dm.request(0x81, motor.FUNC_VARIABLE, b"\x0c")[0]
          == "Motor X(0x81) Variable lesen idx 12 (dir_input)",
          "Motor 0x08 Einheiten: %s | %s" % (t6, t12))
    tp, sp = dm.response(0x82, motor.FUNC_PROTECTION, b"\x0b", build_frame_m(
        0x82, motor.FUNC_PROTECTION, [0, 1, 0, 0, 2, 0, 0, 0], 0x06))[:2]
    check("Fehler 0x00000100 [8 Schleppfehler], Warnung 0x00000002 [1 Unterspannung]"
          in tp and "Motor-STATUS 0x06: Fehler latched, Warnung latched" in tp
          and sp == SEV_WARN, "Motor 0x0C dekodiert: " + tp)
    t0c = dm.response(0x81, motor.FUNC_PROTECTION, b"\x0b", hx(
        "f7 81 0b 00 0c 00 00 00 00 00 00 00 00 fb"))
    check(t0c[0] == "Schutzstatus ok (8x00)" and t0c[1] == SEV_INFO, "Motor 0x0C ok")
    check(dm.request(0x83, motor.FUNC_ERR_DETAIL_DATA, b"")[0]
          == "Motor Z(0x83) Fehlerdetail", "Motor 0x13-Request")
    # Flags 0x91 (CFS-FW) + 0x15-Fehlerfall direkt am Decoder
    dec = Decoder()
    t91 = dec.response(0x01, box.FUNC_GET_BOX_STATE, b"",
                       hx("f7 01 09 19 0a 16 2f 91 04 01 00 95"))[0]
    check("Flags 0x91 (Motorblock > 10 s: Hauptmotor + Feeder A)" in t91
          and "Pull-back: Motorblock > 10 s" in t91, "0x0a Flags 0x91")
    from serial485 import build_frame
    thw, shw = dec.response(0x01, box.FUNC_GET_HARDWARE_STATUS, b"", build_frame(
        0x01, 0x15, [0x10, 0x21, 0x30, 0x40, 0x50, 0x60] + [0] * 10))[:2]
    check("HW-Init FEHLER" in thw and "AHT30 Temp/Feuchte Code 1" in thw
          and shw == SEV_WARN, "0x15 Fehlerfall")
    # Filter
    m2 = Monitor(slot="B", only_errors=True)
    for ln in SELFTEST_LOG:
        m2.feed(ln)
    t2 = "\n".join(m2.flush())
    check("key836" not in t2 and "EXTRUDE_ERR8" not in t2, "--slot B filtert A")
    check("key831" in t2, "--slot B behält globale Fehler")
    m3 = Monitor(only_errors=True, since="03:34")
    for ln in SELFTEST_LOG:
        m3.feed(ln)
    t3 = m3.flush()
    check(t3 and t3[0].startswith("13.10. 03:34")
          and not any("02:12" in s for s in t3), "--since")
    m4 = Monitor(local=True)
    m4.feed(SELFTEST_LOG[-2])
    check(m4.flush() == [], "Motor ohne Antwort-Ende: nichts ausgegeben")
    m4.emit(datetime.datetime(2026, 10, 13, 3, 34, 8, 434000), SEV_INFO, "x")
    check(m4.flush()[0].startswith("12.10. 21:34:08.434"), "--local UTC+8->MESZ")
    # 0x05 control (MOTOR-RESTE §1, 17.10.2026): [02]/[01] ohne Antwort =
    # erwartet (Live-Log 12.10.2026, BELT_TENSION);
    # [04] ohne Antwort = echter Fehler (synthetisch, live nie gesendet)
    ctl_log = [
        "[INFO] 2026-10-12 00:39:23,766 [root] [reactor:_check_timers:168] "
        "retries = 0, cmd = 0x81 0x4 0x0 0x5 0x2",
        "[INFO] 2026-10-12 00:39:23,818 [root] [reactor:_check_timers:168] "
        "cmd[0x81 0x4 0x0 0x5 0x2] get response or timeout",
        "[INFO] 2026-10-12 00:39:30,000 [root] [reactor:_check_timers:168] "
        "retries = 0, cmd = 0x82 0x4 0x0 0x5 0x1",
        "[INFO] 2026-10-12 00:39:30,052 [root] [reactor:_check_timers:168] "
        "cmd[0x82 0x4 0x0 0x5 0x1] get response or timeout",
        "[INFO] 2026-10-12 00:39:31,000 [root] [reactor:_check_timers:168] "
        "retries = 0, cmd = 0x81 0x4 0x0 0x5 0x4",
        "[INFO] 2026-10-12 00:39:31,052 [root] [reactor:_check_timers:168] "
        "cmd[0x81 0x4 0x0 0x5 0x4] get response or timeout",
    ]
    mc = Monitor(show_all=True)
    for ln in ctl_log:
        mc.feed(ln, "selftest-ctl")
    tc = mc.flush()
    tcj = "\n".join(tc)
    check(len(tc) == 3
          and "Motor X(0x81) Control Regelung EIN [02] -> keine Antwort (erwartet/by design)"
          in tcj
          and "Motor Y(0x82) Control Regelung AUS [01] -> keine Antwort (erwartet/by design)"
          in tcj, "0x05 [01]/[02] ohne Antwort = erwartet: " + tcj)
    check("Control Status lesen (EN/Regler/PWM) [04] -> KEINE ANTWORT" in tcj
          and "erwartet" not in tc[-1], "0x05 [04] ohne Antwort = Fehler: " + tcj)
    tcs = Decoder().response(0x81, motor.FUNC_CONTROL, b"\x04", hx(
        "f7 81 06 00 05 00 01 01 %02x" % crc8_cal([6, 0, 5, 0, 1, 1])))
    check(tcs[0] == "Control-Status: EN-Pin 0, Regler ein, PWM ein"
          and tcs[1] == SEV_INFO, "0x05 [04] Antwort dekodiert: %r" % (tcs,))

    m5 = Monitor(unmask=True)
    for ln in SELFTEST_LOG:
        m5.feed(ln)
    t5 = "\n".join(m5.flush())
    check("PLATZHALTER" in t5 and "Vendor 10125" in t5, "--unmask")
    # K8 (18.10.2026): fremde Adressen markieren statt verwerfen (Hi-Mitschnitt)
    dk = Decoder()
    r41 = dk.request(0x41, box.FUNC_GET_BOX_STATE, b"")
    rfb = dk.request(0xFB, 0xA1, b"")
    r02 = dk.request(0x02, box.FUNC_GET_BOX_STATE, b"")
    check("FREMD: Adresse 0x41" in r41[0] and r41[2] == "other"
          and "FREMD: Adresse 0xfb" in rfb[0]
          and "FREMD" not in r02[0]
          and "FREMD" not in dk.request(0xFE, 0xA1, b"")[0],
          "Fremd-Adressen (Request): %r %r %r" % (r41, rfb, r02))
    a11 = dk.response(0x11, 0x02, b"", hx(
        "f7 11 03 00 02 %02x" % crc8_cal([3, 0, 2])))
    check("FREMD: Adresse 0x11" in a11[0] and a11[1] == SEV_WARN,
          "Fremd-Adresse (Antwort): %r" % (a11,))
    if fails:
        print("SELBSTTEST FEHLGESCHLAGEN:", *fails, sep="\n  ")
        return 1
    print("SELBSTTEST OK (%d Zeilen Ausgabe)" % len(out))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Lesender CFS-Monitor für klippy.log (sendet nie).")
    ap.add_argument("files", nargs="*", help="klippy.log-Dateien (sonst stdin)")
    ap.add_argument("--slot", choices=list(box.SLOT_KEYS), type=str.upper)
    ap.add_argument("--only-errors", action="store_true")
    ap.add_argument("--since", help='HH:MM oder "JJJJ-MM-TT HH:MM"')
    ap.add_argument("--raw", action="store_true", help="Frames hex + Datei:Zeile")
    ap.add_argument("--local", action="store_true", help="UTC+8 -> Europe/Berlin")
    ap.add_argument("--all", action="store_true",
                    help="auch Auto-Addr und unveränderte Wiederholungen")
    ap.add_argument("--no-motor", action="store_true")
    ap.add_argument("--unmask", action="store_true", help="SN/UUID/Vendor zeigen")
    ap.add_argument("--no-color", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    mon = Monitor(slot=a.slot, only_errors=a.only_errors, since=a.since,
                  raw=a.raw, local=a.local, show_all=a.all, no_motor=a.no_motor,
                  unmask=a.unmask, color=sys.stdout.isatty() and not a.no_color)
    try:
        if a.files:
            for p in a.files:
                name = os.path.basename(p)
                with open(p, errors="replace") as f:
                    for n, ln in enumerate(f, 1):
                        mon.feed(ln, "%s:%d" % (name, n))
                        for o in mon.flush():
                            print(o)
        else:
            n = 0
            for ln in sys.stdin:
                n += 1
                mon.feed(ln, "stdin:%d" % n)
                for o in mon.flush():
                    print(o, flush=True)
    except (KeyboardInterrupt, BrokenPipeError):
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
