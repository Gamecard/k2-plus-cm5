#!/usr/bin/env python3
"""motor_init.py – Nachbau der Stock-Motor-Init (K2 Plus, FW 1.1.7.0): Plan,
Trockenlauf, Abgleich mit dem Boot-Log und geschützter Lauf.

Grundlage: statische Rekonstruktion von
Motor_Control.set_motor_pin @0xf87d8 und verify_sys_param @0xc163c aus
motor_control_wrapper.cpython-39.so). Kein Netzwerk, kein Drucker nötig.

Ablauf (Phasen exakt nach der Rekonstruktion):
  P0 transparent-Kanal  transparent_send/_response an der Düsen-MCU (L3089)
  P1 GPIO-Strapping     Dir/Step-Pins der Main-MCU auf Config-Pegel, Rücklesen
                        (key799), 0,2 s. Adresse = 0x81 + 2·STEP + DIR (phys.)
  P2 Adress-Runde       FF 0E [01] auf ttyS5 + transparent, 0,1 s, je Motor
                        0E [02] (0,05 s davor); bis `retries` Runden, 1,0 s
                        Pause; danach key798 + 5 s + SHUTDOWN
  P3 alle normal        „All motors are normal“, motor_params_init = True
  P4 Stall-Mode         0x11 [02] an Z/Z1 (ttyS5) und E (transparent), ≤3×
  P5 Schutzabfrage      0C [0b] an alle Motoren (E transparent)
  P6 Fehler löschen     0C [05] an alle, unbedingt (live nie beantwortet)
  P7 GPIO-Restore       alle Dir/Step physisch 0, 0,1 s, Rücklesen (key800),
                        1,0 s – läuft beim Stock auch im Fehlerfall
  P8 Param-Abgleich     je Motor mit Stall-Pin: IDs 9/10/25 lesen; IDs
                        configurable ∩ Config lesen 06 [02,id] (≤3), Vergleich
                        isclose(rel_tol=1e-9, abs_tol=1e-6), bei Abweichung
                        06 [01,id]+<f (≤3, Echo); 06 [03] je Motor
  P9 bereit             is_ready = True, Event auto_addr:start

Abweichungen vom Stock (bewusst):
- Extruder-Param-Abgleich: Stock schickt E als num=5 an 0x85 auf ttyS5
  (niemand antwortet, ≈9 s Timeouts). Default hier: E über
  transparent 0x81 markiert („transparent – Nachbau fehlt“).
  stock_compatible=True baut den Stock-Weg (0x85) nach.
- ID 226 (stall_pos_err_rad_for_slicer) ist ein Host-Parameter; die Motor-FW
  antwortet mit 0 B. Default: nicht gesendet (stock_compatible: gesendet).
- Die 12 02-UUID-Abfragen gehören NICHT zur Init (Client-Poll über den
  Webhook motor_dev_uuid) und fehlen im Plan.

Sicherheit:
- dry_run() und compare_with_log() greifen nie auf einen Bus zu.
- run() sendet NUR mit confirm=True (sonst PermissionError ohne Senden).
  Schreibende Schritte (Broadcast-Adressierung, Stall-Mode, 0C [05],
  Param-Schreiben, 06 [03]) zusätzlich nur mit allow_write=True. Der
  Broadcast FF 0E 01 lässt jeden Motor seine Adresse aus den Strapping-Pegeln
  neu berechnen – ohne vorher gesetzte Pins (P1 braucht die Klipper-MCU)
  bekämen alle Motoren dieselbe Adresse. Er wird deshalb nur mit
  strapping_ok=True gesendet (der Aufrufer bestätigt, dass P1 über die MCU
  gelaufen ist). GPIO- und transparent-Schritte überspringt run() immer.

Bedienung:
  python3 motor_init.py --dry-run [--config <stock-printer.cfg>] [--stock-compatible]
  python3 motor_init.py --compare <klippy.log> [--config …] [--boot N]
  python3 motor_init.py --selftest
"""

import argparse
import ast
import glob
import math
import os
import re
import struct
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field, asdict

import motor as M
from serial485 import build_frame

HERE = os.path.dirname(os.path.abspath(__file__))
# Stock-Config vom Drucker (lokale Kopie, nicht im Repo; lokal/ ist git-ignoriert).
# printer.cfg bindet motor_control.cfg mit [motor_control] per [include] ein –
# Include-Dateien werden im selben Ordner gesucht.
DEFAULT_CONFIG = os.path.join(HERE, "..", "lokal", "stock-config", "printer.cfg")
DEFAULT_INCLUDE_DIRS = (os.path.join(HERE, "..", "lokal", "stock-config"),)
DEFAULT_PTABLE = os.path.join(HERE, "..", "lokal", "ptable_fix.json")

# --- Konstanten (Belege: Rekonstruktion, s. Modulkopf) ---
NUM_BY_NAME_INIT = {"x": 1, "y": 2, "z": 3, "z1": 4, "e": 1}    # set_motor_pin L3102
MOTOR_NAME_TO_NUM = {"x": 1, "y": 2, "z": 3, "z1": 4, "e": 5}   # verify_sys_param (Modul-Init)
VERIFY_ORDER = ("x", "y", "z", "z1", "e")
PIN_MOTORS = ("x", "y", "z", "z1")      # Motoren mit Dir/Step-Pins (E hat nur Stall)
ALWAYS_READ = tuple(M.SYS_PARAM_STOCK_ALWAYS_READ)  # 9/10/25: immer lesen, nie schreiben
HOST_ONLY_IDS = (226,)                   # stall_pos_err_rad_for_slicer, Motor antwortet 0 B
ABS_TOL = 1e-6                           # MotorFlashParam-Default tolerance (Double 0x1b380)
REL_TOL = 1e-9                           # Python-Default von math.isclose

BROADCAST_ADDR = 0xFF
TRANSPARENT_ADDR = M.EXTRUDER_TRANSPARENT_ADDR   # 0x81 auf dem Düsenbus
STOCK_E_ADDR = 0x85                               # Stock-Fehlgriff (num=5)
SUB_ADDR_SET = 0x01

PAUSE_AFTER_GPIO = 0.2       # L3118 @0xf9724
PAUSE_AFTER_BROADCAST = 0.1  # L3127 @0xfa4a8
PAUSE_BEFORE_ADDR = 0.05     # L3140 @0xfbd0c
PAUSE_ROUND = 1.0            # L3157 @0xfc3b8
PAUSE_AFTER_RESTORE = 0.1    # L3164 @0xfb0a8
PAUSE_RESTORE_DONE = 1.0     # L3168 @0xfb5cc
PAUSE_STALL_RETRY = 0.05     # set_z/e_stall_mode
PAUSE_FAIL = 5.0             # key798/799/800/801 @0xfde94, 0xee9f4
TIMEOUT_TRANSPARENT = 2.0    # TIMEOUT_LONG_TIME_MS (≈2 s live)
STALL_TRIES = 3
PARAM_READ_TRIES = 3
PARAM_WRITE_TRIES = 3
GPIO_RESEND_TRIES = 3

# Typen der configurable-IDs, die NICHT float sind (aus ptable_fix.json,
# lokale Arbeitsdatei; alle übrigen 48 configurable-IDs sind float).
PARAM_TYPES_CONFIGURABLE_NONFLOAT = {
    10: "int16_t", 12: "uint16_t", 66: "uint16_t", 67: "uint16_t", 94: "uint16_t",
    110: "uint16_t", 122: "uint16_t", 128: "uint16_t", 141: "uint16_t",
    173: "uint16_t", 200: "uint16_t", 204: "uint16_t", 217: "uint16_t"}
TYPE_FORMATS = {"float": "<f", "uint16_t": "<H", "int16_t": "<h",
                "uint32_t": "<I", "int32_t": "<i"}

PHASES = (
    ("P0", "transparent-Kanal"),
    ("P1", "GPIO-Strapping"),
    ("P2", "Adress-Runde"),
    ("P3", "alle normal"),
    ("P4", "Stall-Mode"),
    ("P5", "Schutzabfrage"),
    ("P6", "Fehler löschen"),
    ("P7", "GPIO-Restore"),
    ("P8", "Param-Abgleich"),
    ("P9", "bereit"),
)
PHASE_NAMES = dict(PHASES)
WRITING_ARTS = ("broadcast", "stall", "clear", "param_write", "param_update")


# =====================================================================
# Parametertabelle
# =====================================================================

def default_param_table():
    """227 Einträge {id, name, configurable, value_type} aus motor.py
    (SYS_PARAM_NAMES/SYS_PARAM_CONFIGURABLE) + eingebettete Typen. Typen
    nicht-configurable IDs bleiben None (werden nie angefasst)."""
    table = []
    for pid, name in enumerate(M.SYS_PARAM_NAMES):
        conf = pid in M.SYS_PARAM_CONFIGURABLE
        vt = PARAM_TYPES_CONFIGURABLE_NONFLOAT.get(pid, "float") if conf else None
        table.append({"id": pid, "name": name, "configurable": conf, "value_type": vt})
    return table


def load_param_table(path):
    """ptable_fix.json (lokale Arbeitsdatei, 227 Einträge in ID-Reihenfolge)
    -> Tabelle wie default_param_table(). Namen müssen mit motor.py
    übereinstimmen (sonst ValueError)."""
    import json
    with open(path, encoding="utf-8") as fh:
        raw = json.load(fh)
    if len(raw) != M.SYS_PARAM_COUNT:
        raise ValueError("ptable: %d statt %d Einträge" % (len(raw), M.SYS_PARAM_COUNT))
    table = []
    for pid, e in enumerate(raw):
        if e["name"] != M.SYS_PARAM_NAMES[pid]:
            raise ValueError("ptable ID %d: %r != motor.py %r"
                             % (pid, e["name"], M.SYS_PARAM_NAMES[pid]))
        vid = e.get("value_id", "?")
        if vid != "?" and int(vid.split()[-1]) != pid:
            raise ValueError("ptable ID %d: value_id %r" % (pid, vid))
        table.append({"id": pid, "name": e["name"],
                      "configurable": e.get("configurable") == "True",
                      "value_type": e.get("value_type")})
    return table


# =====================================================================
# Config
# =====================================================================

_RE_SECTION = re.compile(r"^\[([^\]]+)\]\s*(?:[#;].*)?$")
_RE_OPTION = re.compile(r"^([^:=\s][^:=]*?)\s*[:=]\s*(.*)$")


def _strip_comment(line):
    """Klipper-Kommentar: '#'/';' am Zeilenanfang oder nach Leerraum."""
    m = re.search(r"(^|\s)[#;]", line)
    return line[:m.start()] if m else line


def parse_cfg_sections(text):
    """Klipper-Config-Text -> {sektion: {option: wert}} (Optionen klein,
    gleichnamige Sektionen zusammengeführt, Fortsetzungszeilen angehängt).
    `#*#`-Autosave-Block wird ignoriert."""
    secs = {}
    cur = None
    last_key = None
    for raw in text.splitlines():
        if raw.startswith("#*#"):
            break
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        m = _RE_SECTION.match(line.strip()) if not raw[:1].isspace() else None
        if m:
            cur = secs.setdefault(m.group(1).strip(), {})
            last_key = None
            continue
        if cur is None:
            continue
        if raw[:1].isspace() and last_key is not None:
            cur[last_key] = (cur[last_key] + "\n" + line.strip()).strip()
            continue
        m = _RE_OPTION.match(line.strip())
        if m:
            last_key = m.group(1).strip().lower()
            cur[last_key] = m.group(2).strip()
    return secs


def load_config_text(path, include_dirs=DEFAULT_INCLUDE_DIRS, _depth=0, _notes=None):
    """Liest eine Config und löst [include …] auf (zuerst neben der
    einbindenden Datei, dann in include_dirs). Rückgabe (text, notizen)."""
    notes = [] if _notes is None else _notes
    if _depth > 10:
        raise ValueError("include zu tief verschachtelt: %s" % path)
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().splitlines()
    out = []
    base = os.path.dirname(os.path.abspath(path))
    for raw in lines:
        m = re.match(r"^\[include\s+([^\]]+)\]", raw.strip())
        if not m:
            out.append(raw)
            continue
        pat = m.group(1).strip()
        found = []
        for d in (base,) + tuple(include_dirs):
            found = sorted(glob.glob(os.path.join(d, pat)))
            if found:
                if d != base:
                    notes.append("Include %s nicht neben %s – genommen aus %s"
                                 % (pat, os.path.basename(path), os.path.relpath(d, HERE)))
                break
        if not found:
            notes.append("Include %s nicht gefunden" % pat)
            continue
        for f in found:
            sub, _ = load_config_text(f, include_dirs, _depth + 1, notes)
            out.append(sub)
    return "\n".join(out), notes


def parse_pin_level(spec):
    """'!PB7,0' -> {'raw','pin','invert','level','phys'} (phys = level ^ invert)."""
    if spec is None:
        return None
    parts = [p.strip() for p in spec.split(",")]
    if len(parts) != 2 or parts[1] not in ("0", "1"):
        raise ValueError("Pin-Angabe muss 'PIN,0|1' sein: %r" % (spec,))
    pin = parts[0]
    inv = pin.startswith("!")
    lvl = int(parts[1])
    return {"raw": pin, "pin": pin.lstrip("!^~"), "invert": inv,
            "level": lvl, "phys": lvl ^ int(inv)}


def _eval_value(text):
    """Config-Wert wie Stock (eval(str(cv))) – hier sicher per literal_eval."""
    try:
        v = ast.literal_eval(text.strip())
    except (ValueError, SyntaxError):
        raise ValueError("Config-Wert nicht auswertbar: %r" % (text,))
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise ValueError("Config-Wert muss Zahl sein: %r" % (text,))
    return v


def parse_motor_control_config(text):
    """Text (printer.cfg inkl. aufgelöster Includes) -> Struktur der
    Sektion [motor_control] plus Pins der [stepper_*]-Sektionen."""
    secs = parse_cfg_sections(text)
    if "motor_control" not in secs:
        raise ValueError("keine Sektion [motor_control] (Include motor_control.cfg aufgelöst?)")
    mc = secs["motor_control"]
    if "switch" not in mc:
        raise ValueError("[motor_control] switch fehlt (Pflichtoption)")
    cfg = {
        "switch": int(mc["switch"]),
        "motor_closed_loop": [s.strip() for s in mc.get("motor_closed_loop", "").split(",")
                              if s.strip()],
        "retries": int(mc.get("retries", 4)),
        "overcurrent_switch": int(mc.get("overcurrent_switch", 0)),
        "cut_pos_offset": float(mc.get("cut_pos_offset", 0.4)),
        "pins": {}, "params": {}, "params_raw": {}, "unbekannt": [],
        "stepper_pins": {},
    }
    for m in cfg["motor_closed_loop"]:
        if m not in MOTOR_NAME_TO_NUM:
            raise ValueError("motor_closed_loop: unbekannter Motor %r" % (m,))
    for m in VERIFY_ORDER:
        cfg["pins"][m] = {
            "dir": parse_pin_level(mc.get("motor_%s_dir" % m)),
            "step": parse_pin_level(mc.get("motor_%s_step" % m)),
            "stall": mc.get("motor_%s_stall" % m)}
        cfg["params"][m] = {}
        cfg["params_raw"][m] = {}
    known = {"switch", "motor_closed_loop", "retries", "overcurrent_switch", "cut_pos_offset"}
    for key, val in mc.items():
        if key in known or re.match(r"^motor_(x|y|z|z1|e)_(dir|step|stall)$", key):
            continue
        m = re.match(r"^(z1|x|y|z|e)_(.+)$", key)
        if not m:
            cfg["unbekannt"].append(key)
            continue
        cfg["params_raw"][m.group(1)][m.group(2)] = val
        cfg["params"][m.group(1)][m.group(2)] = _eval_value(val)
    for sname, opts in secs.items():
        if sname.startswith("stepper_"):
            cfg["stepper_pins"][sname] = {"step": opts.get("step_pin"),
                                          "dir": opts.get("dir_pin")}
    return cfg


def load_motor_control_config(path, include_dirs=DEFAULT_INCLUDE_DIRS):
    text, notes = load_config_text(path, include_dirs)
    cfg = parse_motor_control_config(text)
    cfg["notizen"] = notes
    cfg["quelle"] = path
    return cfg


# =====================================================================
# Strapping
# =====================================================================

def strapping_address(dir_phys, step_phys):
    """Motor-FW 0x0E Sub 01 (Handler 0x107b4, Motor-FW-Analyse):
    addr = ((STEP << 1) | DIR) − 0x7f (Byte) = 0x81 + 2·STEP + DIR."""
    for v in (dir_phys, step_phys):
        if v not in (0, 1):
            raise ValueError("Pegel muss 0/1 sein: %r" % (v,))
    return 0x81 + 2 * step_phys + dir_phys


def strapping_table(cfg):
    """[(motor, dir, step, adresse, erwartet, ok)] für Motoren mit Pins."""
    rows = []
    for m in PIN_MOTORS:
        p = cfg["pins"].get(m) or {}
        if not p.get("dir") or not p.get("step"):
            continue
        addr = strapping_address(p["dir"]["phys"], p["step"]["phys"])
        exp = 0x80 + NUM_BY_NAME_INIT[m]
        rows.append({"motor": m, "dir": p["dir"], "step": p["step"],
                     "adresse": addr, "erwartet": exp, "ok": addr == exp})
    return rows


def stepper_mapping(cfg):
    """Motor (motor_control) -> Klipper-[stepper_*] mit denselben Dir/Step-
    Pins (ohne '!'). Bei corexy steuert stepper_x/_y je einen der beiden
    Riemenmotoren – die Namen müssen nicht zusammenpassen."""
    def bare(p):
        return p.lstrip("!^~") if p else None
    res = {}
    for m in PIN_MOTORS:
        p = cfg["pins"].get(m) or {}
        if not p.get("dir") or not p.get("step"):
            continue
        for sname, sp in cfg.get("stepper_pins", {}).items():
            if bare(sp.get("dir")) == p["dir"]["pin"] and bare(sp.get("step")) == p["step"]["pin"]:
                res[m] = sname
    return res


# =====================================================================
# Plan
# =====================================================================

@dataclass
class Step:
    nr: int
    phase: str
    ziel: str                 # x/y/z/z1/e/broadcast/gpio/host
    art: str                  # mcu/gpio/host/broadcast/addr_read/eval/stall/protection/clear/param_read/param_write/param_update/ready
    text: str
    bus: str = "host"         # ttyS5 / transparent / mcu / host
    addr: int = None
    fun: int = None
    payload: list = None
    timeout: float = None
    retries: int = 0          # Bus-Retries (Stock: immer 0)
    versuche: int = 1         # Versuche auf Anwendungsebene
    pause_davor: float = 0.0
    pause_danach: float = 0.0
    bedingung: str = ""
    schreibend: bool = False
    beleg: str = ""
    gruppe: str = None        # 'runde' = wiederholbare Adress-Runde
    aktiv: bool = True        # False = bewusst nicht gesendet
    hinweis: str = ""
    meta: dict = field(default_factory=dict)

    def sig(self):
        if self.art == "gpio":
            return ("G", self.meta["pin_raw"], self.meta["level"])
        if self.bus in ("ttyS5", "transparent") and self.fun is not None and self.payload is not None:
            return (self.bus, self.addr, self.fun, tuple(self.payload))
        return None

    def frame(self):
        """Vollframe (f7 … crc) für Bus-Schritte, sonst None."""
        if self.bus in ("ttyS5", "transparent") and self.payload is not None:
            return build_frame(self.addr, self.fun, list(self.payload))
        return None


@dataclass
class InitPlan:
    steps: list
    cfg: dict
    meta: dict

    def phase_steps(self, ph):
        return [s for s in self.steps if s.phase == ph]


def build_init_plan(cfg, param_table=None, stock_compatible=False):
    """Init-Sequenz als Schrittliste (ohne Bus-Zugriff).

    ValueError, wenn die Strapping-Pegel eines Motors nicht seine Stock-
    Adresse ergeben (der Broadcast würde sonst falsch adressieren)."""
    table = param_table if param_table is not None else default_param_table()
    if len(table) != M.SYS_PARAM_COUNT:
        raise ValueError("Parametertabelle braucht %d Einträge" % M.SYS_PARAM_COUNT)
    closed = list(cfg["motor_closed_loop"])
    retries = int(cfg["retries"])
    if retries < 1:
        raise ValueError("retries muss >= 1 sein: %r" % retries)
    strap = strapping_table(cfg)
    for r in strap:
        if not r["ok"]:
            raise ValueError("Strapping %s: DIR=%d STEP=%d -> 0x%02X, erwartet 0x%02X"
                             % (r["motor"], r["dir"]["phys"], r["step"]["phys"],
                                r["adresse"], r["erwartet"]))
    for m in closed:
        if m != "e" and m not in [r["motor"] for r in strap]:
            raise ValueError("Motor %s ohne Dir/Step-Pins – keine Strapping-Adresse" % m)
    steps = []

    def add(**kw):
        s = Step(nr=len(steps) + 1, **kw)
        steps.append(s)
        return s

    def addr_of(m):
        return TRANSPARENT_ADDR if m == "e" else 0x80 + NUM_BY_NAME_INIT[m]

    def bus_of(m):
        return "transparent" if m == "e" else "ttyS5"

    def tr_hint(m):
        return "transparent – Nachbau fehlt (Düsen-MCU)" if m == "e" else ""

    pin_list = []
    for m in PIN_MOTORS:
        p = cfg["pins"].get(m) or {}
        for kind in ("dir", "step"):
            if p.get(kind):
                pin_list.append((m, kind, p[kind]))

    meta = {"retries": retries, "closed_loop": closed, "stock_compatible": stock_compatible,
            "strapping": strap, "stepper_mapping": stepper_mapping(cfg),
            "notizen": list(cfg.get("notizen", [])), "ignoriert": []}

    # P0
    add(phase="P0", ziel="e", art="mcu", bus="mcu",
        text="transparent-Kanal einrichten: lookup_query_command('transparent_send oid=%c "
             "write=%*s timeout_ms=%u', 'transparent_response oid=%c read=%*s') an nozzle_mcu",
        beleg="set_motor_pin L3089 @0xf87d8; _build_config config_transparent @0xa04fc",
        hinweis="braucht Klipper-MCU (config_transparent) – Nachbau fehlt")

    if cfg["switch"] == 0:
        for m, kind, p in pin_list:
            lvl = 1 if p["invert"] else 0
            add(phase="P7", ziel=m, art="gpio", bus="mcu",
                text="%s %s %s = %d (physisch 0)" % (m.upper(), kind.upper(), p["raw"], lvl),
                meta={"pin_raw": p["raw"], "level": lvl, "phys": 0, "kind": kind, "motor": m},
                beleg="set_motor_pin L3112 (switch=0)", hinweis="braucht Klipper-MCU")
        add(phase="P7", ziel="host", art="host", pause_danach=PAUSE_AFTER_RESTORE,
            text="'switch=0 set_pin_io 0', M400; danach Ende (V)", beleg="L3112–3115")
        meta["anmerkung"] = "switch=0: keine Motor-Init"
        return InitPlan(steps, cfg, meta)

    # P1
    for m, kind, p in pin_list:
        add(phase="P1", ziel=m, art="gpio", bus="mcu",
            text="%s %s %s = %d (physisch %d)" % (m.upper(), kind.upper(), p["raw"],
                                                   p["level"], p["phys"]),
            meta={"pin_raw": p["raw"], "level": p["level"], "phys": p["phys"],
                  "kind": kind, "motor": m},
            beleg="set_motor_pin_dir @0x23b64; check_motor_pin_io @0xef308",
            hinweis="braucht Klipper-MCU (set_digital + query_gpio_status)")
    add(phase="P1", ziel="gpio", art="host", bus="mcu", pause_danach=PAUSE_AFTER_GPIO,
        text="M400, alle Pins zurücklesen ('initial set_pin_io done')",
        bedingung="Abweichung: resend_set_io %d× (je 0,2 s), dann key799 + 5 s" % GPIO_RESEND_TRIES,
        beleg="L3096–3099, L3118", hinweis="braucht Klipper-MCU")

    # P2
    add(phase="P2", ziel="broadcast", art="broadcast", bus="ttyS5", addr=BROADCAST_ADDR,
        fun=M.FUNC_ADDR, payload=[SUB_ADDR_SET], timeout=M.TIMEOUT_ADDR, gruppe="runde",
        schreibend=True, text="Broadcast FF 0E [01]: Motoren übernehmen Adresse aus Strapping",
        bedingung="keine Antwort erwartet (live Timeout ≈50 ms)",
        beleg="L3121/3123 MOTOR_SET_ADDR NUM=9; MOTOR-FW 0x0E Sub 01")
    add(phase="P2", ziel="broadcast", art="broadcast", bus="transparent", addr=BROADCAST_ADDR,
        fun=M.FUNC_ADDR, payload=[SUB_ADDR_SET], timeout=TIMEOUT_TRANSPARENT, gruppe="runde",
        schreibend=True, pause_danach=PAUSE_AFTER_BROADCAST,
        text="Broadcast FF 0E [01] transparent (Düsenbus)",
        bedingung="Antwort leer (live ≈2 s)", beleg="L3122/3125 MOTOR_SET_ADDR NUM=9 EXTRUDER",
        hinweis="transparent – Nachbau fehlt (Düsen-MCU)")
    for m in closed:
        add(phase="P2", ziel=m, art="addr_read", bus=bus_of(m), addr=addr_of(m),
            fun=M.FUNC_ADDR, payload=[M.SUB_ADDR_READ],
            timeout=TIMEOUT_TRANSPARENT if m == "e" else M.TIMEOUT_ADDR,
            pause_davor=PAUSE_BEFORE_ADDR, gruppe="runde",
            text="Adresse lesen %s (0E [02] -> [0x%02X])" % (m.upper(), addr_of(m)),
            bedingung="Antwort zählt als ok (Adresswert ungeprüft, V)",
            beleg="L3130–3142 MOTOR_READ_ADDR NUM=%d" % NUM_BY_NAME_INIT[m], hinweis=tr_hint(m))
    add(phase="P2", ziel="host", art="eval", gruppe="runde", pause_danach=PAUSE_ROUND,
        text="Runde auswerten: alle %d Motoren derselben Runde geantwortet?" % len(closed),
        bedingung="ja -> P3; nein -> %s Pause, nächste Runde (max. %d); danach key798 "
                  "+ 5 s + SHUTDOWN (P7 läuft trotzdem)" % (_fs(PAUSE_ROUND), retries),
        beleg="L3146, L3157/3158, L3192–3198", meta={"retries": retries})

    # P3
    add(phase="P3", ziel="host", art="host", text="'All motors are normal', motor_params_init = True",
        beleg="L3148/3149")

    # P4
    for m in ("z", "z1", "e"):
        if m not in closed:
            continue
        add(phase="P4", ziel=m, art="stall", bus=bus_of(m), addr=addr_of(m),
            fun=M.FUNC_SET_MODE, payload=[M.STALL_MODE_EMERGENCY],
            timeout=TIMEOUT_TRANSPARENT if m == "e" else M.TIMEOUT_STALL,
            versuche=STALL_TRIES, schreibend=True,
            text="Stall-Mode %s: 0x11 [02]" % m.upper(),
            bedingung="Echo [02]; sonst Wiederholung nach %s, nach %d Versuchen %s"
                      % (_fs(PAUSE_STALL_RETRY), STALL_TRIES, "key802" if m == "e" else "key801 + 5 s"),
            beleg="L3150 set_z_stall_mode @0xf3f70" if m != "e" else "L3152 set_e_stall_mode @0x2fcf4",
            hinweis=tr_hint(m), meta={"pause_wiederholung": PAUSE_STALL_RETRY})

    # P5
    for m in closed:
        add(phase="P5", ziel=m, art="protection", bus=bus_of(m), addr=addr_of(m),
            fun=M.FUNC_PROTECTION, payload=[M.SUB_PROT_CHECK],
            timeout=TIMEOUT_TRANSPARENT if m == "e" else M.TIMEOUT_PROTECTION,
            text="Schutzstatus %s: 0C [0b] -> 8 B (Fehler/Warnung)" % m.upper(),
            bedingung="nur bei Fehlerbits: FORCE_MOVE stepper_y 0.01, M84, Clear (V)",
            beleg="L3154 MOTOR_CHECK_PROTECTION_AFTER_HOME DATA=11 @0x59c70",
            hinweis=tr_hint(m) or "Seiteneffekt: gibt bei stall_mode 2 den Notfall-Pin frei")

    # P6
    for m in closed:
        add(phase="P6", ziel=m, art="clear", bus=bus_of(m), addr=addr_of(m),
            fun=M.FUNC_PROTECTION, payload=[M.SUB_PROT_CLEAR],
            timeout=TIMEOUT_TRANSPARENT if m == "e" else M.TIMEOUT_PROTECTION, schreibend=True,
            text="Fehler/Warnung löschen %s: 0C [05]" % m.upper(),
            bedingung="unbedingt; live nie beantwortet (Timeout)",
            beleg="L3155 MOTOR_CLEAR_ERR_WARN_CODE NUM=0 DATA=5", hinweis=tr_hint(m))

    # P7
    for m, kind, p in pin_list:
        lvl = 1 if p["invert"] else 0
        add(phase="P7", ziel=m, art="gpio", bus="mcu",
            text="%s %s %s = %d (physisch 0)" % (m.upper(), kind.upper(), p["raw"], lvl),
            meta={"pin_raw": p["raw"], "level": lvl, "phys": 0, "kind": kind, "motor": m},
            bedingung="immer (auch nach key798)", pause_davor=0.0,
            beleg="set_motor_pin_normal L3163 @0x316a8", hinweis="braucht Klipper-MCU")
    add(phase="P7", ziel="gpio", art="host", bus="mcu", pause_davor=PAUSE_AFTER_RESTORE,
        pause_danach=PAUSE_RESTORE_DONE,
        text="M400, alle Pins zurücklesen ('restore set_pin_io done')",
        bedingung="Abweichung: Resend, dann key800",
        beleg="L3164–3168 check_motor_pin_io_restore", hinweis="braucht Klipper-MCU")

    # P8
    by_name = dict((p["name"], p) for p in table)
    for m in VERIFY_ORDER:
        for name in sorted(cfg["params"].get(m, {})):
            p = by_name.get(name)
            if p is None:
                meta["ignoriert"].append("%s_%s (unbekannter Parameter)" % (m, name))
            elif not p["configurable"]:
                meta["ignoriert"].append("%s_%s (ID %d nicht configurable)" % (m, name, p["id"]))
        if not (cfg["pins"].get(m) or {}).get("stall"):
            continue
        num = MOTOR_NAME_TO_NUM[m]
        if m == "e":
            if stock_compatible:
                bus, addr = "ttyS5", STOCK_E_ADDR
                hint = "Stock-Bug: 0x85 auf ttyS5 antwortet nie (≈1 s Timeout je Versuch)"
            else:
                bus, addr = "transparent", TRANSPARENT_ADDR
                hint = "transparent – Nachbau fehlt (Vorbild motor_extruder_sys_param_init)"
        else:
            bus, addr, hint = "ttyS5", 0x80 + num, ""
        stock_alt = (m == "e" and not stock_compatible)
        add(phase="P8", ziel=m, art="host",
            text="update_flash_param stepper:%d (%s)" % (num, m.upper()),
            bedingung="nur nach erfolgreicher Adress-Runde (cnt != retries+1)",
            beleg="verify_sys_param @0xc163c, L851 (motor_%s_stall gesetzt)" % m)
        for p in table:
            pid = p["id"]
            if not p["configurable"]:
                continue
            configured = p["name"] in cfg["params"].get(m, {})
            if pid not in ALWAYS_READ and not configured:
                continue
            host_only = pid in HOST_ONLY_IDS
            aktiv = not (host_only and not stock_compatible)
            ph = hint
            if host_only:
                ph = ("Host-Parameter (Slicer), Motor-FW antwortet 0 B – " +
                      ("Stock sendet ihn" if stock_compatible else "wird nicht gesendet (Stock sendet)"))
            vt = p["value_type"] or "float"
            cval = cfg["params"][m].get(p["name"]) if configured else None
            rmeta = {"param_id": pid, "name": p["name"], "value_type": vt, "motor": m,
                     "config_value": cval, "nur_lesen": pid in ALWAYS_READ}
            if stock_alt:
                rmeta["stock_sig"] = ("ttyS5", STOCK_E_ADDR, M.FUNC_SYS_PARAM, (M.SUB_SYS_READ, pid))
            add(phase="P8", ziel=m, art="param_read", bus=bus, addr=addr,
                fun=M.FUNC_SYS_PARAM, payload=M.build_sys_param_read_payload(pid),
                timeout=M.TIMEOUT_SYS_PARAM, versuche=PARAM_READ_TRIES, aktiv=aktiv,
                text="%s ID %d %s lesen%s" % (m.upper(), pid, p["name"],
                                               " (nur lesen)" if pid in ALWAYS_READ else ""),
                bedingung="≤%d Versuche; leere Antwort -> 'result is None', weiter" % PARAM_READ_TRIES,
                beleg="verify_sys_param 06 [02,id]", hinweis=ph, meta=rmeta)
            if pid in ALWAYS_READ:
                continue
            wmeta = dict(rmeta)
            wmeta.pop("stock_sig", None)
            if vt == "float":
                payload = M.build_sys_param_write_payload(pid, cval)
                waktiv, wh = aktiv, ph
                if stock_alt:
                    wmeta["stock_sig"] = ("ttyS5", STOCK_E_ADDR, M.FUNC_SYS_PARAM, tuple(payload))
            else:
                payload, waktiv = None, False
                wh = "Schreibformat für %s unbelegt – nicht nachgebaut" % vt
            add(phase="P8", ziel=m, art="param_write", bus=bus, addr=addr,
                fun=M.FUNC_SYS_PARAM, payload=payload, timeout=M.TIMEOUT_SYS_PARAM,
                versuche=PARAM_WRITE_TRIES, schreibend=True, aktiv=waktiv,
                text="%s ID %d %s := %r" % (m.upper(), pid, p["name"], cval),
                bedingung="nur wenn not isclose(gelesen, %r, rel_tol=%g, abs_tol=%g); Echo prüfen"
                          % (cval, REL_TOL, ABS_TOL),
                beleg="verify_sys_param 06 [01,id]+<wert> @0xc4398", hinweis=wh, meta=wmeta)
        umeta = {"motor": m}
        if stock_alt:
            umeta["stock_sig"] = ("ttyS5", STOCK_E_ADDR, M.FUNC_SYS_PARAM, (M.SUB_SYS_UPDATE,))
        add(phase="P8", ziel=m, art="param_update", bus=bus, addr=addr, fun=M.FUNC_SYS_PARAM,
            payload=M.build_sys_param_update_payload(), timeout=M.TIMEOUT_SYS_PARAM,
            schreibend=True, text="%s 'Updata param' 06 [03]" % m.upper(),
            bedingung="einmal je Motor; Ergebnis ungeprüft (V)", beleg="verify_sys_param Ende",
            hinweis=hint, meta=umeta)

    # P9
    add(phase="P9", ziel="host", art="ready",
        text="is_ready = True; send_event('auto_addr:start') – CFS-Auto-Adressierung erst jetzt",
        beleg="L3190/3191; auto_addr_wrapper reg_auto_addr_set")
    meta["anmerkung"] = ("12 02-UUID-Abfragen gehören nicht zur Init (Client-Poll "
                         "motor_dev_uuid) und fehlen bewusst")
    return InitPlan(steps, cfg, meta)


# =====================================================================
# Trockenlauf
# =====================================================================

def _fs(x):
    return ("%g s" % x).replace(".", ",")


def _hexl(bs):
    return " ".join("%02x" % b for b in bs)


def describe_step(s):
    if s.bus in ("ttyS5", "transparent") and s.fun is not None:
        where = "%s 0x%02X" % (s.bus, s.addr)
        pl = "[%s]" % _hexl(s.payload) if s.payload is not None else "[?]"
        core = "[%s] Fun 0x%02X %s" % (where, s.fun, pl)
        extra = []
        if s.timeout is not None:
            extra.append("Timeout " + _fs(s.timeout))
        if s.versuche > 1:
            extra.append("≤%d Versuche" % s.versuche)
        return "%s – %s (%s)" % (core, s.text, ", ".join(extra))
    return "[%s] %s" % (s.bus, s.text)


def dry_run(plan, details=True):
    """Menschenlesbarer Ablauf (deutsch). KEIN Bus-Zugriff."""
    cfg, meta = plan.cfg, plan.meta
    out = ["Motor-Init-Plan (Trockenlauf, nichts gesendet)"]
    if cfg.get("quelle"):
        out.append("Config: %s" % os.path.relpath(cfg["quelle"], os.getcwd()))
    for n in meta.get("notizen", []):
        out.append("Hinweis: %s" % n)
    out.append("switch=%d  retries=%d  motor_closed_loop=%s  stock_compatible=%s"
               % (cfg["switch"], cfg["retries"], ",".join(cfg["motor_closed_loop"]),
                  meta.get("stock_compatible")))
    if meta.get("strapping"):
        out.append("Strapping (Adresse = 0x81 + 2·STEP + DIR, physische Pegel):")
        for r in meta["strapping"]:
            out.append("  %-2s DIR %-4s=%d STEP %-4s=%d -> 0x%02X (erwartet 0x%02X) %s%s"
                       % (r["motor"], r["dir"]["raw"], r["dir"]["phys"], r["step"]["raw"],
                          r["step"]["phys"], r["adresse"], r["erwartet"],
                          "ok" if r["ok"] else "FALSCH",
                          ("  Pins = [%s]" % meta["stepper_mapping"][r["motor"]])
                          if r["motor"] in meta.get("stepper_mapping", {}) else ""))
    for ign in meta.get("ignoriert", []):
        out.append("Ignoriert (wie Stock): %s" % ign)
    cur = None
    for s in plan.steps:
        if s.phase != cur:
            cur = s.phase
            extra = ""
            if s.gruppe == "runde":
                extra = " – wiederholt bis alle antworten, max. %d Runden" % meta["retries"]
            out.append("")
            out.append("%s %s%s" % (cur, PHASE_NAMES.get(cur, ""), extra))
            if cur == "P8" and not details:
                n_r = sum(1 for x in plan.phase_steps("P8") if x.art == "param_read")
                n_w = sum(1 for x in plan.phase_steps("P8") if x.art == "param_write")
                out.append("  (%d Leseschritte, %d bedingte Schreibschritte – Details mit "
                           "details=True)" % (n_r, n_w))
        if cur == "P8" and not details and s.art in ("param_read", "param_write"):
            continue
        flags = []
        if s.schreibend:
            flags.append("schreibend")
        if not s.aktiv:
            flags.append("NICHT gesendet")
        line = "  %3d. %s" % (s.nr, describe_step(s))
        if s.pause_davor:
            line += " | vorher " + _fs(s.pause_davor)
        if s.pause_danach:
            line += " | danach " + _fs(s.pause_danach)
        if flags:
            line += " {%s}" % ", ".join(flags)
        out.append(line)
        if s.hinweis:
            out.append("        Hinweis: %s" % s.hinweis)
        if s.bedingung and (details or s.art not in ("param_read", "param_write")):
            out.append("        Bedingung: %s" % s.bedingung)
    act = [s for s in plan.steps if s.aktiv and s.sig() and s.art != "gpio"]
    out.append("")
    out.append("Summe: %d Schritte; Bus-Schritte aktiv: ttyS5 %d, transparent %d; GPIO %d; "
               "schreibend %d; nicht gesendet %d"
               % (len(plan.steps), sum(1 for s in act if s.bus == "ttyS5"),
                  sum(1 for s in act if s.bus == "transparent"),
                  sum(1 for s in plan.steps if s.art == "gpio"),
                  sum(1 for s in plan.steps if s.schreibend and s.aktiv),
                  sum(1 for s in plan.steps if not s.aktiv)))
    if meta.get("anmerkung"):
        out.append("Anmerkung: %s" % meta["anmerkung"])
    return "\n".join(out)


# =====================================================================
# Boot-Log-Abgleich
# =====================================================================

_B = r"(b'(?:[^'\\]|\\.)*'|b\"(?:[^\"\\]|\\.)*\")"
RE_TAG = re.compile(r"\] \[([^\]]+)\] ")
RE_PIN = re.compile(r"pin:(\S+) value:(\d+) (\w+) result:\{.*?'status': (\d+)")
RE_REQ485 = re.compile(r"data_send: " + _B + r"\s*$")
RE_RES485 = re.compile(r"data_send:" + _B + r" recv_result:(None|" + _B[1:-1] + r")")
RE_REQT = re.compile(r"data_send:" + _B + r"\s*$")
RE_REST = re.compile(r"data_send:" + _B + r" result:\{.*?'read': " + _B)
MARKERS = ("initial set_pin_io done", "restore set_pin_io done", "broadcast retries:",
           "All motors are normal", "update_flash_param stepper:", "Updata param",
           "stall mode change")


def _lit(s):
    return ast.literal_eval(s)


def _tag(line):
    m = RE_TAG.search(line)
    return m.group(1) if m else None


def _split_req(raw, transparent):
    """Request-Bytes -> (addr, fun, data). ttyS5-Log ohne f7/CRC
    (addr len status fun data), transparent als Vollframe."""
    if transparent:
        if len(raw) < 6 or raw[0] != 0xF7:
            raise ValueError("transparent-Frame unerwartet: %r" % (raw,))
        return raw[1], raw[4], tuple(raw[5:-1])
    if len(raw) < 4:
        raise ValueError("Request zu kurz: %r" % (raw,))
    return raw[0], raw[3], tuple(raw[4:])


def _resp_data(frame):
    """Antwort-Vollframe -> Daten (ohne Kopf/CRC) oder None."""
    if not frame or len(frame) < 6 or frame[0] != 0xF7:
        return None
    return bytes(frame[5:-1])


def find_boots(lines):
    return [i for i, s in enumerate(lines) if "initial set_pin_io done" in s]


def parse_boot_events(lines, boot=-1):
    """Ereignisse der Motor-Init eines Boots aus klippy.log-Zeilen.

    Bereich: ab den GPIO-Rücklesezeilen vor 'initial set_pin_io done' bis zur
    ersten auto_addr-Zeile (bzw. nächstem Boot / Dateiende). Rückgabe
    (events, markers, (start, end) 1-basiert, boot_nr, n_boots)."""
    starts = find_boots(lines)
    if not starts:
        raise ValueError("kein 'initial set_pin_io done' im Log – keine Motor-Init gefunden")
    try:
        b = starts[boot]
    except IndexError:
        raise ValueError("Boot %r nicht vorhanden (%d Boots)" % (boot, len(starts)))
    bnr = starts.index(b) + 1
    start = b
    for k in range(b - 1, max(-1, b - 60), -1):   # GPIO-Rücklesen davor (ggf. verschränkt)
        if "set_pin_io done" in lines[k]:
            break
        if RE_PIN.search(lines[k]):
            start = k
    end = len(lines)
    for j in range(b + 1, len(lines)):
        if "[auto_addr_wrapper:" in lines[j] or "initial set_pin_io done" in lines[j]:
            end = j
            break
    events, markers = [], []
    pend = defaultdict(deque)
    for i in range(start, end):
        s = lines[i]
        for mk in MARKERS:
            if mk in s:
                markers.append((i + 1, s[s.index(mk):].strip()))
                break
        m = RE_PIN.search(s)
        if m:
            events.append({"line": i + 1, "kind": "gpio", "pin": m.group(1),
                           "level": int(m.group(2)), "ok": m.group(3) == "success",
                           "status": int(m.group(4)), "tag": _tag(s), "quelle": "init",
                           "sig": ("G", m.group(1), int(m.group(2)))})
            continue
        if "data_send" not in s:
            continue
        m = RE_RES485.search(s)
        if m:
            req = _lit(m.group(1))
            ans = None if m.group(2) == "None" else _lit(m.group(2))
            _add_frame(events, pend, req, False, ans, True, i + 1, s)
            continue
        m = RE_REST.search(s)
        if m:
            req = _lit(m.group(1))
            ans = _lit(m.group(2))
            _add_frame(events, pend, req, True, ans or None, True, i + 1, s)
            continue
        m = RE_REQ485.search(s)
        if m:
            req = _lit(m.group(1))
            pend[(False, req)].append((i + 1, _tag(s)))
            continue
        m = RE_REQT.search(s)
        if m and _tag(s) != "gcode:respond_info:453":
            req = _lit(m.group(1))
            if req[:1] == b"\xf7":
                pend[(True, req)].append((i + 1, _tag(s)))
    for (tr, req), q in pend.items():
        for ln, tag in q:
            a, f, d = _split_req(req, tr)
            events.append(_mk_event(ln, tag, tr, a, f, d, None, False))
    events.sort(key=lambda e: e["line"])
    return events, markers, (start + 1, end), bnr, len(starts)


def _mk_event(line, tag, transparent, addr, fun, data, ans, known):
    quelle = "poll" if (tag or "").startswith("webhooks") else "init"
    bus = "transparent" if transparent else "ttyS5"
    return {"line": line, "kind": "frame", "bus": bus, "addr": addr, "fun": fun,
            "data": data, "tag": tag, "quelle": quelle, "antwort": ans,
            "antwort_bekannt": known, "sig": (bus, addr, fun, data)}


def _add_frame(events, pend, req, transparent, ans, known, line, s):
    q = pend.get((transparent, req))
    if q:
        ln, tag = q.popleft()
    else:
        ln, tag = line, None   # transparent ohne Anforderungszeile (z.B. Stall E)
    a, f, d = _split_req(req, transparent)
    events.append(_mk_event(ln, tag, transparent, a, f, d, ans, known))


def _decode_value(data, vt):
    if data is None:
        return None
    if len(data) == 0:
        return "leer"
    fmt = TYPE_FORMATS.get(vt or "float", "<f")
    if struct.calcsize(fmt) != len(data):
        return data.hex()
    return struct.unpack(fmt, data)[0]


def _near(sig, es):
    """Gleicher Bus+Fun und (gleiche Adresse oder gleiche Payload)."""
    if sig is None or es is None or sig[0] != es[0] or sig[0] == "G":
        return False
    return sig[2] == es[2] and (sig[1] == es[1] or sig[3] == es[3])


def compare_with_log(plan, log_lines, boot=-1):
    """Gleicht die Init-Frames eines Boots mit dem Plan ab (kein Bus).

    Ergebnis-Dict: treffer (Schritt -> Log-Zeilen), abweichungen,
    bewusst (Stock sendet, Nachbau bewusst anders), zusaetzlich (Log ohne
    Planschritt; Client-Polls getrennt), fehlend, nicht_ausgeloest
    (bedingte Writes), runden, param_bericht, gpio_bericht."""
    events, markers, rng, bnr, nboots = parse_boot_events(log_lines, boot)
    steps = plan.steps
    sigs = [s.sig() for s in steps]
    alts = [s.meta.get("stock_sig") for s in steps]
    rsteps = [i for i, s in enumerate(steps) if s.gruppe == "runde"]
    r0 = rsteps[0] if rsteps else None
    r1 = rsteps[-1] if rsteps else None
    hits = defaultdict(list)          # Schritt-Index -> [(runde, event, via)]
    res = {"bereich": rng, "boot": bnr, "boots": nboots, "abweichungen": [],
           "zusaetzlich": [], "polls": [], "fehlend": [], "nicht_ausgeloest": [],
           "ohne_frame": 0, "bewusst_nicht_im_log": [], "markers": markers}
    skipped = set()
    p, last, rnd = 0, None, 0

    def skip_range(a, b):
        for k in range(a, b):
            if hits.get(k) or k in skipped:
                continue
            skipped.add(k)

    for ev in events:
        if ev["quelle"] == "poll":
            res["polls"].append(ev)
            continue
        es = ev["sig"]
        if last is not None and es in (sigs[last], alts[last]) \
                and len([h for h in hits[last] if h[0] == rnd]) < steps[last].versuche:
            hits[last].append((rnd, ev, "sig" if es == sigs[last] else "stock"))
            continue
        if r0 is not None and es == sigs[r0] and p > r0 and rnd < plan.meta["retries"]:
            skip_range(p, r1 + 1)
            rnd += 1
            hits[r0].append((rnd, ev, "sig"))
            p, last = r0 + 1, r0
            continue
        j = None
        for k in range(p, len(steps)):
            if es == sigs[k] or (alts[k] is not None and es == alts[k]):
                j = k
                break
        if j is not None:
            if j == r0:
                rnd += 1
            skip_range(p, j)
            hits[j].append((rnd, ev, "sig" if es == sigs[j] else "stock"))
            p, last = j + 1, j
            continue
        q = next((k for k in range(p, len(steps)) if sigs[k] is not None and steps[k].aktiv), None)
        if q is not None and _near(sigs[q], es):
            skip_range(p, q)
            hits[q].append((rnd, ev, "abweichung"))
            p, last = q + 1, q
            continue
        res["zusaetzlich"].append(ev)
    skip_range(p, len(steps))

    for k in sorted(skipped):
        s = steps[k]
        if hits.get(k):
            continue
        if sigs[k] is None:
            res["ohne_frame"] += 1
        elif not s.aktiv:
            res["bewusst_nicht_im_log"].append(s)
        elif s.art == "param_write":
            res["nicht_ausgeloest"].append(s)
        else:
            res["fehlend"].append(s)
    treffer, bewusst = [], []
    for k in sorted(hits):
        s = steps[k]
        for rr, ev, via in hits[k]:
            if via == "abweichung":
                res["abweichungen"].append((s, ev))
        vias = set(v for _, _, v in hits[k])
        if "stock" in vias:
            bewusst.append((s, hits[k], "Stock sendet an 0x%02X auf ttyS5 (Stock-Bug), Plan: %s"
                            % (STOCK_E_ADDR, s.hinweis)))
        elif not s.aktiv:
            bewusst.append((s, hits[k], "Stock sendet, Plan sendet bewusst nicht: %s" % s.hinweis))
        elif vias == {"sig"}:
            treffer.append((s, hits[k]))
    res["treffer"] = treffer
    res["bewusst"] = bewusst
    res["hits"] = hits
    res["runden"] = rnd
    res["runden_max"] = plan.meta.get("retries")
    # Runden: wer hat geantwortet
    rinfo = defaultdict(list)
    for k in rsteps:
        if steps[k].art != "addr_read":
            continue
        for rr, ev, _ in hits.get(k, []):
            d = _resp_data(ev["antwort"])
            rinfo[rr].append((steps[k].ziel, d[0] if d else None))
    res["runden_info"] = dict(rinfo)
    # GPIO
    g = []
    for k, s in enumerate(steps):
        if s.art != "gpio":
            continue
        for _, ev, _ in hits.get(k, []):
            g.append((s, ev, ev["status"] == s.meta["phys"] and ev["ok"]))
    res["gpio_bericht"] = g
    # Param-Bericht
    rows = []
    for k, s in enumerate(steps):
        if s.art != "param_read":
            continue
        hk = hits.get(k, [])
        val = None
        n = len(hk)
        if hk:
            ev = hk[-1][1]
            d = _resp_data(ev["antwort"])
            val = _decode_value(d, s.meta["value_type"])
        cval = s.meta["config_value"]
        wstep = None
        if not s.meta["nur_lesen"] and k + 1 < len(steps) and steps[k + 1].art == "param_write":
            wstep = k + 1
        written = bool(wstep is not None and hits.get(wstep))
        expect = None
        if wstep is not None and isinstance(val, (int, float)) and not isinstance(val, bool):
            expect = not math.isclose(val, cval, rel_tol=REL_TOL, abs_tol=ABS_TOL)
        echo = None
        if written:
            d = _resp_data(hits[wstep][-1][1]["antwort"])
            echo = _decode_value(d, s.meta["value_type"])
        rows.append({"motor": s.ziel, "id": s.meta["param_id"], "name": s.meta["name"],
                     "versuche": n, "gelesen": val, "config": cval,
                     "nur_lesen": s.meta["nur_lesen"], "erwartet_schreiben": expect,
                     "geschrieben": written, "echo": echo,
                     "konsistent": (expect == written) if expect is not None else None,
                     "im_log": n > 0})
    res["param_bericht"] = rows
    return res


def _evdesc(ev):
    if ev["kind"] == "gpio":
        return "pin %s=%d (Zeile %d)" % (ev["pin"], ev["level"], ev["line"])
    return "%s 0x%02X Fun 0x%02X [%s] (Zeile %d, %s)" % (
        ev["bus"], ev["addr"], ev["fun"], _hexl(ev["data"]), ev["line"], ev["tag"] or "ohne Anforderungszeile")


def _fmtv(v):
    if isinstance(v, float):
        return "%.6g" % v
    return str(v)


def format_compare(res, show_values=True, max_list=12, mask_calib=False):
    out = ["Abgleich Plan ↔ Boot-Log (Zeilen %d–%d, Boot %d von %d)"
           % (res["bereich"][0], res["bereich"][1], res["boot"], res["boots"])]
    n_frames = sum(len(h) for _, h in res["treffer"])
    out.append("Übereinstimmungen: %d Planschritte, %d Log-Ereignisse (inkl. Wiederholungen/Runden)"
               % (len(res["treffer"]), n_frames))
    by_phase = defaultdict(int)
    for s, h in res["treffer"]:
        by_phase[s.phase] += 1
    out.append("  je Phase: " + ", ".join("%s %d" % (ph, by_phase[ph]) for ph, _ in PHASES
                                         if by_phase.get(ph)))
    rr = res["runden_info"]
    out.append("Adress-Runden: %d (max. %s)" % (res["runden"], res.get("runden_max")))
    for r in sorted(rr):
        ans = ["%s=0x%02X" % (m, a) for m, a in rr[r] if a is not None]
        no = [m for m, a in rr[r] if a is None]
        out.append("  Runde %d: Antwort %s%s" % (r, ", ".join(ans) or "keine",
                                                 ("; ohne Antwort: " + ", ".join(no)) if no else ""))
    g = res["gpio_bericht"]
    out.append("GPIO-Rücklesen: %d/%d Pegel wie geplant (physisch)" % (sum(1 for x in g if x[2]), len(g)))
    for s, ev, ok in g:
        if not ok:
            out.append("  ABWEICHUNG %s: Log status %d, Plan physisch %d" % (s.text, ev["status"], s.meta["phys"]))
    out.append("Abweichungen (Ziel/Payload anders): %d" % len(res["abweichungen"]))
    for s, ev in res["abweichungen"][:max_list]:
        out.append("  Schritt %d %s ↔ Log %s" % (s.nr, s.text, _evdesc(ev)))
    out.append("Bewusst anders als Stock: %d Planschritte" % len(res["bewusst"]))
    grp = defaultdict(list)
    for s, h, why in res["bewusst"]:
        grp[why].append((s, h))
    for why, lst in grp.items():
        out.append("  %d× %s" % (len(lst), why))
        for s, h in lst[:max_list]:
            out.append("     Schritt %d %s – %d Log-Frame(s), Zeilen %s"
                       % (s.nr, s.text, len(h), ",".join(str(x[1]["line"]) for x in h)))
    polls = res["polls"]
    out.append("Im Log zusätzlich: %d Init-Ereignisse ohne Planschritt, %d Client-Polls"
               % (len(res["zusaetzlich"]), len(polls)))
    for ev in res["zusaetzlich"][:max_list]:
        out.append("  %s" % _evdesc(ev))
    if polls:
        pc = defaultdict(int)
        for ev in polls:
            pc[(ev["fun"], ev["tag"])] += 1
        for (fun, tag), n in sorted(pc.items()):
            out.append("  %d× Fun 0x%02X [%s] – Client-Poll (12 02 = motor_dev_uuid), nicht Init"
                       % (n, fun, tag))
    out.append("Im Plan fehlend (nicht im Log): %d" % len(res["fehlend"]))
    fg = defaultdict(list)
    for s in res["fehlend"]:
        fg[(s.phase, s.ziel)].append(s)
    for (ph, z), lst in fg.items():
        out.append("  %s %s: %d Schritt(e), z.B. %s" % (ph, z, len(lst), lst[0].text))
    out.append("Bedingte Writes nicht ausgelöst (Wert lag in Toleranz): %d" % len(res["nicht_ausgeloest"]))
    if res["bewusst_nicht_im_log"]:
        out.append("Bewusst nicht gesendet und auch nicht im Log: %d" % len(res["bewusst_nicht_im_log"]))
    rows = res["param_bericht"]
    if rows:
        wr = [r for r in rows if r["geschrieben"]]
        out.append("Param-Abgleich: %d Lesungen im Log, %d Writes live" % (
            sum(1 for r in rows if r["im_log"]), len(wr)))
        if show_values:
            out.append("  Motor  ID  Name                                    gelesen      Config     Ergebnis")
            for r in rows:
                if not r["im_log"]:
                    continue
                if r["gelesen"] is None:
                    erg = "keine Antwort"
                elif r["nur_lesen"]:
                    erg = "nur gelesen"
                elif r["gelesen"] == "leer":
                    erg = "0-B-Antwort"
                elif r["geschrieben"]:
                    erg = "geschrieben, Echo %s" % _fmtv(r["echo"])
                else:
                    erg = "in Toleranz"
                if r["konsistent"] is False:
                    erg += "  INKONSISTENT (isclose sagt %s)" % ("schreiben" if r["erwartet_schreiben"] else "gleich")
                gv = r["gelesen"]
                if mask_calib and r["nur_lesen"] and isinstance(gv, float):
                    gv = "<kalib.>"
                out.append("  %-5s %3d  %-38s %-12s %-10s %s"
                           % (r["motor"], r["id"], r["name"][:38],
                              _fmtv(gv) + ("" if r["versuche"] <= 1 else " (%d×)" % r["versuche"]),
                              "-" if r["config"] is None else _fmtv(r["config"]), erg))
        kons = [r for r in rows if r["konsistent"] is not None]
        out.append("Konsistenz isclose ↔ Write im Log: %d/%d" % (sum(1 for r in kons if r["konsistent"]), len(kons)))
    return "\n".join(out)


# =====================================================================
# Ausführung (geschützt)
# =====================================================================

def _isclose(a, b):
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def run(bus, plan, confirm=False, allow_write=False, strapping_ok=False, sleep=time.sleep):
    """Führt den Plan über `bus` (serial485.Serial485 oder kompatibel) aus.

    - confirm != True -> PermissionError, es wird nichts gesendet.
    - schreibende Schritte nur mit allow_write=True, der Broadcast zusätzlich
      nur mit strapping_ok=True (P1 lief extern über die Klipper-MCU).
    - GPIO-/MCU-/transparent-Schritte und 0x85 werden immer übersprungen.
    Rückgabe: Liste {nr, phase, status, text}."""
    if confirm is not True:
        raise PermissionError("motor_init.run gesperrt: sendet Motor-Frames – nur mit confirm=True")
    rep = []

    def note(s, status, text=""):
        rep.append({"nr": s.nr if s else None, "phase": s.phase if s else None,
                    "status": status, "text": (s.text + (" – " + text if text else "")) if s else text})

    def skip_reason(s):
        if not s.aktiv:
            return "nicht aktiv: " + s.hinweis
        if s.bus in ("mcu", "transparent"):
            return "braucht Klipper-MCU (%s) – übersprungen" % s.bus
        if s.addr == STOCK_E_ADDR:
            return "0x85 wird nicht angesprochen (Stock-Bug)"
        if s.schreibend and allow_write is not True:
            return "schreibend – allow_write fehlt"
        if s.art == "broadcast" and strapping_ok is not True:
            return "Broadcast nur mit strapping_ok=True (GPIO-Phase über MCU nötig)"
        return None

    steps = plan.steps
    reads = {}
    i = 0
    normal = None
    while i < len(steps):
        s = steps[i]
        if s.gruppe == "runde":
            grp = [x for x in steps if x.gruppe == "runde"]
            normal = False
            for r in range(1, plan.meta["retries"] + 1):
                ok, need = set(), set()
                for x in grp:
                    if x.art == "eval":
                        continue
                    why = skip_reason(x)
                    if why:
                        note(x, "übersprungen", "Runde %d: %s" % (r, why))
                        continue
                    if x.pause_davor:
                        sleep(x.pause_davor)
                    if x.art == "broadcast":
                        try:
                            bus.transact(x.addr, x.fun, list(x.payload), timeout=x.timeout,
                                         retries=0)
                            note(x, "ok", "Runde %d: unerwartete Antwort" % r)
                        except TimeoutError:
                            note(x, "ok", "Runde %d: keine Antwort (erwartet)" % r)
                    elif x.art == "addr_read":
                        need.add(x.ziel)
                        try:
                            a = M.Motor(bus, x.addr).read_485_addr()
                            ok.add(x.ziel)
                            note(x, "ok", "Runde %d: Antwort 0x%02X" % (r, a))
                        except (TimeoutError, ValueError) as e:
                            note(x, "timeout", "Runde %d: %s" % (r, e))
                    if x.pause_danach:
                        sleep(x.pause_danach)
                if need and ok == need:
                    normal = True
                    rep.append({"nr": None, "phase": "P2", "status": "ok",
                                "text": "Runde %d: alle ttyS5-Motoren geantwortet (%s)%s"
                                        % (r, ",".join(sorted(ok)),
                                           "; E nicht geprüft (transparent fehlt)"
                                           if "e" in plan.meta["closed_loop"] else "")})
                    break
                sleep(PAUSE_ROUND)
            i = max(k for k, x in enumerate(steps) if x.gruppe == "runde") + 1
            if not normal:
                rep.append({"nr": None, "phase": "P2", "status": "abbruch",
                            "text": "nach %d Runden nicht alle Motoren – Stock: key798 + "
                                    "SHUTDOWN; Stall/Param entfallen" % plan.meta["retries"]})
                for x in steps[i:]:
                    if x.phase in ("P7",):
                        note(x, "übersprungen", skip_reason(x) or "")
                return rep
            continue
        why = skip_reason(s)
        if s.art in ("host", "eval", "ready", "mcu"):
            if s.bus == "mcu":
                note(s, "übersprungen", "braucht Klipper-MCU")
            else:
                note(s, "info")
            i += 1
            continue
        if why:
            note(s, "übersprungen", why)
            i += 1
            continue
        if s.pause_davor:
            sleep(s.pause_davor)
        mot = M.Motor(bus, s.addr)
        if s.art == "stall":
            done = False
            for t in range(s.versuche):
                try:
                    mot.set_mode(s.payload[0])
                    done = True
                    break
                except (TimeoutError, RuntimeError, ValueError):
                    sleep(PAUSE_STALL_RETRY)
            note(s, "ok" if done else "fehler", "" if done else "key801-Fall (3 Versuche)")
        elif s.art == "protection":
            try:
                d = mot.check_protection()
                note(s, "ok", "ok" if d["ok"] else "Fehlerbits: %s %s" % (d["errors"], d["warnings"]))
            except (TimeoutError, ValueError) as e:
                note(s, "timeout", str(e))
        elif s.art == "clear":
            try:
                bus.transact(s.addr, s.fun, list(s.payload), timeout=s.timeout, retries=0)
                note(s, "ok", "Antwort erhalten")
            except TimeoutError:
                note(s, "ok", "keine Antwort (live ebenso)")
        elif s.art == "param_read":
            raw = None
            for t in range(s.versuche):
                try:
                    raw = mot.read_sys_param_raw(s.meta["param_id"])
                    break
                except TimeoutError:
                    continue
            val = _decode_value(raw, s.meta["value_type"])
            reads[(s.ziel, s.meta["param_id"])] = val
            note(s, "ok" if raw is not None else "timeout", "gelesen %s" % _fmtv(val))
        elif s.art == "param_write":
            val = reads.get((s.ziel, s.meta["param_id"]))
            cval = s.meta["config_value"]
            if not isinstance(val, (int, float)) or isinstance(val, bool):
                note(s, "übersprungen", "kein Lesewert (%s)" % _fmtv(val))
            elif _isclose(val, cval):
                note(s, "ok", "in Toleranz (%s)" % _fmtv(val))
            else:
                done = None
                for t in range(s.versuche):
                    try:
                        done = mot.write_sys_param(s.meta["param_id"], cval, confirm=True)
                        break
                    except (TimeoutError, RuntimeError):
                        continue
                note(s, "ok" if done is not None else "fehler",
                     "%s -> %s %s" % (_fmtv(val), _fmtv(cval),
                                      "write success" if done is not None else "write failed"))
        elif s.art == "param_update":
            try:
                mot.update_sys_params(confirm=True)
                note(s, "ok")
            except (TimeoutError, RuntimeError) as e:
                note(s, "fehler", "%s (Stock ignoriert das Ergebnis)" % e)
        if s.pause_danach:
            sleep(s.pause_danach)
        i += 1
    return rep


# =====================================================================
# Selbsttest
# =====================================================================

# Gekürzter Auszug des echten Boots 16.10.2026 (Live-Log, lokal, nicht im
# Repo): nur Motor-Init-Zeilen, Param-Teil
# nur Motor X (stepper:1) sowie E (stepper:5) ID 9 und 06 [03].
# Anonymisiert: UUID-Antworten (12 02) -> cd ab + Nullbytes, Kalibrierwerte
# ID 9/25 -> synthetisch 100.0/4.0 (CRC jeweils neu berechnet).
BOOT_LOG_EXCERPT = r"""
[INFO] 2026-10-16 01:15:39,986 [root] [klippy:_connect:258] pin:PB9 value:0 success result:{'oid': 11, 'status': 0, '#name': 'gpio_status', '#sent_time': 61.50041029222223, '#receive_time': 61.50130093177778}
[INFO] 2026-10-16 01:15:39,991 [root] [klippy:_connect:258] pin:PB10 value:0 success result:{'oid': 11, 'status': 0, '#name': 'gpio_status', '#sent_time': 61.50547316722223, '#receive_time': 61.50613088977778}
[INFO] 2026-10-16 01:15:39,996 [root] [klippy:_connect:258] pin:!PB7 value:0 success result:{'oid': 11, 'status': 1, '#name': 'gpio_status', '#sent_time': 61.510192334222225, '#receive_time': 61.510867222777776}
[INFO] 2026-10-16 01:15:40,002 [root] [klippy:_connect:258] pin:PB8 value:0 success result:{'oid': 11, 'status': 0, '#name': 'gpio_status', '#sent_time': 61.51519104222223, '#receive_time': 61.516183556777776}
[INFO] 2026-10-16 01:15:40,007 [root] [klippy:_connect:258] pin:PB5 value:0 success result:{'oid': 11, 'status': 0, '#name': 'gpio_status', '#sent_time': 61.520363334222225, '#receive_time': 61.521110514777774}
[INFO] 2026-10-16 01:15:40,011 [root] [klippy:_connect:258] pin:PB6 value:1 success result:{'oid': 11, 'status': 1, '#name': 'gpio_status', '#sent_time': 61.52533600122222, '#receive_time': 61.52594847277778}
[INFO] 2026-10-16 01:15:40,016 [root] [klippy:_connect:258] pin:PA1 value:1 success result:{'oid': 11, 'status': 1, '#name': 'gpio_status', '#sent_time': 61.52949337622223, '#receive_time': 61.53069647277778}
[INFO] 2026-10-16 01:15:40,020 [root] [klippy:_connect:258] pin:PB15 value:1 success result:{'oid': 11, 'status': 1, '#name': 'gpio_status', '#sent_time': 61.53448237622222, '#receive_time': 61.53508951477777}
[INFO] 2026-10-16 01:15:40,021 [root] [gcode:respond_info:453] initial set_pin_io done
[INFO] 2026-10-16 01:15:40,224 [root] [klippy:_connect:258] data_send: b'\xff\x04\x00\x0e\x01'
[INFO] 2026-10-16 01:15:40,296 [root] [gcode:respond_info:453] data_send:b'\xff\x04\x00\x0e\x01' recv_result:None
[INFO] 2026-10-16 01:15:40,298 [root] [klippy:_connect:258] data_send:b'\xf7\xff\x04\x00\x0e\x01\x89'
[INFO] 2026-10-16 01:15:42,381 [root] [gcode:respond_info:453] data_send:b'\xf7\xff\x04\x00\x0e\x01\x89' result:{'oid': 1, 'read': b'', '#name': 'transparent_response', '#sent_time': 63.01430190466667, '#receive_time': 63.84028289077778}
[INFO] 2026-10-16 01:15:42,384 [root] [gcode:respond_info:453] broadcast retries:1
[INFO] 2026-10-16 01:15:42,563 [root] [klippy:_connect:258] data_send: b'\x81\x04\x00\x0e\x02'
[INFO] 2026-10-16 01:15:42,569 [root] [webhooks:_process_request:273] data_send: b'\x81\x04\x00\x12\x02'
[INFO] 2026-10-16 01:15:42,633 [root] [gcode:respond_info:453] data_send:b'\x81\x04\x00\x0e\x02' recv_result:None
[INFO] 2026-10-16 01:15:42,686 [root] [klippy:_connect:258] data_send: b'\x82\x04\x00\x0e\x02'
[INFO] 2026-10-16 01:15:43,635 [root] [gcode:respond_info:453] data_send:b'\x81\x04\x00\x12\x02' recv_result:None
[INFO] 2026-10-16 01:15:43,637 [root] [webhooks:_process_request:273] data_send: b'\x82\x04\x00\x12\x02'
[INFO] 2026-10-16 01:15:43,701 [root] [gcode:respond_info:453] data_send:b'\x82\x04\x00\x0e\x02' recv_result:None
[INFO] 2026-10-16 01:15:43,754 [root] [klippy:_connect:258] data_send: b'\x83\x04\x00\x0e\x02'
[INFO] 2026-10-16 01:15:44,704 [root] [gcode:respond_info:453] data_send:b'\x82\x04\x00\x12\x02' recv_result:None
[INFO] 2026-10-16 01:15:44,706 [root] [webhooks:_process_request:273] data_send: b'\x83\x04\x00\x12\x02'
[INFO] 2026-10-16 01:15:44,727 [root] [gcode:respond_info:453] data_send:b'\x83\x04\x00\x0e\x02' recv_result:b'\xf7\x83\x04\x00\x0e\x83\x0e'
[INFO] 2026-10-16 01:15:44,740 [root] [gcode:respond_info:453] data_send:b'\x83\x04\x00\x12\x02' recv_result:b'\xf7\x83\x0b\x00\x12\xcd\xab\x00\x00\x00\x00\x00\x00\xad'
[INFO] 2026-10-16 01:15:44,742 [root] [webhooks:_process_request:273] data_send: b'\x84\x04\x00\x12\x02'
[INFO] 2026-10-16 01:15:44,786 [root] [klippy:_connect:258] data_send: b'\x84\x04\x00\x0e\x02'
[INFO] 2026-10-16 01:15:45,755 [root] [gcode:respond_info:453] data_send:b'\x84\x04\x00\x12\x02' recv_result:None
[INFO] 2026-10-16 01:15:45,822 [root] [gcode:respond_info:453] data_send:b'\x84\x04\x00\x0e\x02' recv_result:None
[INFO] 2026-10-16 01:15:45,825 [root] [klippy:_connect:258] data_send:b'\xf7\x81\x04\x00\x0e\x02\x80'
[INFO] 2026-10-16 01:15:45,831 [root] [gcode:respond_info:453] data_send:b'\xf7\x81\x04\x00\x0e\x02\x80' result:{'oid': 1, 'read': b'\xf7\x81\x04\x00\x0e\x81\x00', '#name': 'transparent_response', '#sent_time': 67.34472608722223, '#receive_time': 67.34635382333333}
[INFO] 2026-10-16 01:15:46,837 [root] [klippy:_connect:258] data_send: b'\xff\x04\x00\x0e\x01'
[INFO] 2026-10-16 01:15:46,897 [root] [gcode:respond_info:453] data_send:b'\xff\x04\x00\x0e\x01' recv_result:None
[INFO] 2026-10-16 01:15:46,899 [root] [klippy:_connect:258] data_send:b'\xf7\xff\x04\x00\x0e\x01\x89'
[INFO] 2026-10-16 01:15:48,905 [root] [gcode:respond_info:453] data_send:b'\xf7\xff\x04\x00\x0e\x01\x89' result:{'oid': 1, 'read': b'', '#name': 'transparent_response', '#sent_time': 70.09289483822222, '#receive_time': 70.41970931077778}
[INFO] 2026-10-16 01:15:48,907 [root] [gcode:respond_info:453] broadcast retries:2
[INFO] 2026-10-16 01:15:49,078 [root] [klippy:_connect:258] data_send: b'\x81\x04\x00\x0e\x02'
[INFO] 2026-10-16 01:15:49,099 [root] [gcode:respond_info:453] data_send:b'\x81\x04\x00\x0e\x02' recv_result:b'\xf7\x81\x04\x00\x0e\x81\x00'
[INFO] 2026-10-16 01:15:49,155 [root] [klippy:_connect:258] data_send: b'\x82\x04\x00\x0e\x02'
[INFO] 2026-10-16 01:15:49,172 [root] [gcode:respond_info:453] data_send:b'\x82\x04\x00\x0e\x02' recv_result:b'\xf7\x82\x04\x00\x0e\x82\t'
[INFO] 2026-10-16 01:15:49,228 [root] [klippy:_connect:258] data_send: b'\x83\x04\x00\x0e\x02'
[INFO] 2026-10-16 01:15:49,245 [root] [gcode:respond_info:453] data_send:b'\x83\x04\x00\x0e\x02' recv_result:b'\xf7\x83\x04\x00\x0e\x83\x0e'
[INFO] 2026-10-16 01:15:49,300 [root] [klippy:_connect:258] data_send: b'\x84\x04\x00\x0e\x02'
[INFO] 2026-10-16 01:15:49,336 [root] [gcode:respond_info:453] data_send:b'\x84\x04\x00\x0e\x02' recv_result:b'\xf7\x84\x04\x00\x0e\x84\x1b'
[INFO] 2026-10-16 01:15:49,341 [root] [klippy:_connect:258] data_send:b'\xf7\x81\x04\x00\x0e\x02\x80'
[INFO] 2026-10-16 01:15:49,347 [root] [gcode:respond_info:453] data_send:b'\xf7\x81\x04\x00\x0e\x02\x80' result:{'oid': 1, 'read': b'\xf7\x81\x04\x00\x0e\x81\x00', '#name': 'transparent_response', '#sent_time': 70.86039842222223, '#receive_time': 70.86177170033334}
[INFO] 2026-10-16 01:15:49,352 [root] [gcode:respond_info:453] All motors are normal
[INFO] 2026-10-16 01:15:49,355 [root] [klippy:_connect:258] data_send: b'\x83\x04\x00\x11\x02'
[INFO] 2026-10-16 01:15:49,369 [root] [gcode:respond_info:453] data_send:b'\x83\x04\x00\x11\x02' recv_result:b'\xf7\x83\x04\x00\x11\x02\x14'
[INFO] 2026-10-16 01:15:49,371 [root] [klippy:_connect:258] stall mode change motor83: send:b'\x02' recv:b'\x02'
[INFO] 2026-10-16 01:15:49,373 [root] [klippy:_connect:258] data_send: b'\x84\x04\x00\x11\x02'
[INFO] 2026-10-16 01:15:49,388 [root] [gcode:respond_info:453] data_send:b'\x84\x04\x00\x11\x02' recv_result:b'\xf7\x84\x04\x00\x11\x02\x14'
[INFO] 2026-10-16 01:15:49,390 [root] [klippy:_connect:258] stall mode change motor84: send:b'\x02' recv:b'\x02'
[INFO] 2026-10-16 01:15:49,397 [root] [gcode:respond_info:453] data_send:b'\xf7\x81\x04\x00\x11\x02\x14' result:{'oid': 1, 'read': b'\xf7\x81\x04\x00\x11\x02\x14', '#name': 'transparent_response', '#sent_time': 70.91027022744444, '#receive_time': 70.91180574133332}
[INFO] 2026-10-16 01:15:49,399 [root] [klippy:_connect:258] stall mode change motor_e: send:b'\x02' recv:b'\x02'
[INFO] 2026-10-16 01:15:49,402 [root] [gcode:<lambda>:186] data_send: b'\x81\x04\x00\x0c\x0b'
[INFO] 2026-10-16 01:15:49,419 [root] [gcode:respond_info:453] data_send:b'\x81\x04\x00\x0c\x0b' recv_result:b'\xf7\x81\x0b\x00\x0c\x00\x00\x00\x00\x00\x00\x00\x00\xfb'
[INFO] 2026-10-16 01:15:49,422 [root] [gcode:<lambda>:186] data_send: b'\x82\x04\x00\x0c\x0b'
[INFO] 2026-10-16 01:15:49,441 [root] [gcode:respond_info:453] data_send:b'\x82\x04\x00\x0c\x0b' recv_result:b'\xf7\x82\x0b\x00\x0c\x00\x00\x00\x00\x00\x00\x00\x00\xfb'
[INFO] 2026-10-16 01:15:49,443 [root] [gcode:<lambda>:186] data_send: b'\x83\x04\x00\x0c\x0b'
[INFO] 2026-10-16 01:15:49,462 [root] [gcode:respond_info:453] data_send:b'\x83\x04\x00\x0c\x0b' recv_result:b'\xf7\x83\x0b\x00\x0c\x00\x00\x00\x00\x00\x00\x00\x00\xfb'
[INFO] 2026-10-16 01:15:49,465 [root] [gcode:<lambda>:186] data_send: b'\x84\x04\x00\x0c\x0b'
[INFO] 2026-10-16 01:15:49,485 [root] [gcode:respond_info:453] data_send:b'\x84\x04\x00\x0c\x0b' recv_result:b'\xf7\x84\x0b\x00\x0c\x00\x00\x00\x00\x00\x00\x00\x00\xfb'
[INFO] 2026-10-16 01:15:49,493 [root] [gcode:respond_info:453] data_send:b'\xf7\x81\x04\x00\x0c\x0b\x95' result:{'oid': 1, 'read': b'\xf7\x81\x0b\x00\x0c\x00\x00\x00\x00\x00\x00\x00\x00\xfb', '#name': 'transparent_response', '#sent_time': 71.00632510244445, '#receive_time': 71.00805688088889}
[INFO] 2026-10-16 01:15:49,498 [root] [gcode:<lambda>:186] data_send: b'\x81\x04\x00\x0c\x05'
[INFO] 2026-10-16 01:15:49,560 [root] [gcode:respond_info:453] data_send:b'\x81\x04\x00\x0c\x05' recv_result:None
[INFO] 2026-10-16 01:15:49,614 [root] [gcode:<lambda>:186] data_send: b'\x82\x04\x00\x0c\x05'
[INFO] 2026-10-16 01:15:49,680 [root] [gcode:respond_info:453] data_send:b'\x82\x04\x00\x0c\x05' recv_result:None
[INFO] 2026-10-16 01:15:49,733 [root] [gcode:<lambda>:186] data_send: b'\x83\x04\x00\x0c\x05'
[INFO] 2026-10-16 01:15:49,798 [root] [gcode:respond_info:453] data_send:b'\x83\x04\x00\x0c\x05' recv_result:None
[INFO] 2026-10-16 01:15:49,851 [root] [gcode:<lambda>:186] data_send: b'\x84\x04\x00\x0c\x05'
[INFO] 2026-10-16 01:15:49,921 [root] [gcode:respond_info:453] data_send:b'\x84\x04\x00\x0c\x05' recv_result:None
[INFO] 2026-10-16 01:15:49,974 [root] [gcode:<lambda>:186] data_send:b'\xf7\x81\x04\x00\x0c\x05\xbf'
[INFO] 2026-10-16 01:15:50,029 [root] [gcode:respond_info:453] data_send:b'\xf7\x81\x04\x00\x0c\x05\xbf' result:{'oid': 1, 'read': b'', '#name': 'transparent_response', '#sent_time': 71.49396272844444, '#receive_time': 71.54483406177778}
[INFO] 2026-10-16 01:15:50,559 [root] [klippy:_connect:258] pin:PB9 value:0 success result:{'oid': 11, 'status': 0, '#name': 'gpio_status', '#sent_time': 72.07412754722222, '#receive_time': 72.07472864477778}
[INFO] 2026-10-16 01:15:50,564 [root] [klippy:_connect:258] pin:PB10 value:0 success result:{'oid': 11, 'status': 0, '#name': 'gpio_status', '#sent_time': 72.07825283922222, '#receive_time': 72.07891014477778}
[INFO] 2026-10-16 01:15:50,585 [root] [klippy:_connect:258] pin:!PB7 value:1 success result:{'oid': 11, 'status': 0, '#name': 'gpio_status', '#sent_time': 72.08270813122222, '#receive_time': 72.08337556177779}
[INFO] 2026-10-16 01:15:50,590 [root] [klippy:_connect:258] pin:PB8 value:0 success result:{'oid': 11, 'status': 0, '#name': 'gpio_status', '#sent_time': 72.10414900622222, '#receive_time': 72.10500706177778}
[INFO] 2026-10-16 01:15:50,594 [root] [klippy:_connect:258] pin:PB5 value:0 success result:{'oid': 11, 'status': 0, '#name': 'gpio_status', '#sent_time': 72.10896471422221, '#receive_time': 72.10964881177779}
[INFO] 2026-10-16 01:15:50,598 [root] [klippy:_connect:258] pin:PB6 value:0 success result:{'oid': 11, 'status': 0, '#name': 'gpio_status', '#sent_time': 72.11324483922222, '#receive_time': 72.11387376977778}
[INFO] 2026-10-16 01:15:50,603 [root] [klippy:_connect:258] pin:PA1 value:0 success result:{'oid': 11, 'status': 0, '#name': 'gpio_status', '#sent_time': 72.11736567322222, '#receive_time': 72.11797064477778}
[INFO] 2026-10-16 01:15:50,607 [root] [klippy:_connect:258] pin:PB15 value:0 success result:{'oid': 11, 'status': 0, '#name': 'gpio_status', '#sent_time': 72.12170767322222, '#receive_time': 72.12231860377779}
[INFO] 2026-10-16 01:15:50,608 [root] [gcode:respond_info:453] restore set_pin_io done
[INFO] 2026-10-16 01:15:51,613 [root] [klippy:_connect:258] update_flash_param stepper:1
[INFO] 2026-10-16 01:15:51,627 [root] [klippy:_connect:258] data_send: b'\x81\x05\x00\x06\x02\t'
[INFO] 2026-10-16 01:15:51,641 [root] [gcode:respond_info:453] data_send:b'\x81\x05\x00\x06\x02\t' recv_result:b'\xf7\x81\x07\x00\x06\x00\x00\xc8B\xd4'
[INFO] 2026-10-16 01:15:51,648 [root] [klippy:_connect:258] data_send: b'\x81\x05\x00\x06\x02\n'
[INFO] 2026-10-16 01:15:51,661 [root] [gcode:respond_info:453] data_send:b'\x81\x05\x00\x06\x02\n' recv_result:b'\xf7\x81\x05\x00\x06\x00\x00\x90'
[INFO] 2026-10-16 01:15:51,689 [root] [klippy:_connect:258] data_send: b'\x81\x05\x00\x06\x02\x19'
[INFO] 2026-10-16 01:15:51,708 [root] [gcode:respond_info:453] data_send:b'\x81\x05\x00\x06\x02\x19' recv_result:b'\xf7\x81\x07\x00\x06\x00\x00\x80@)'
[INFO] 2026-10-16 01:15:51,770 [root] [klippy:_connect:258] data_send: b'\x81\x05\x00\x06\x02F'
[INFO] 2026-10-16 01:15:51,786 [root] [gcode:respond_info:453] data_send:b'\x81\x05\x00\x06\x02F' recv_result:b'\xf7\x81\x07\x00\x06\x00\x00\x96C\t'
[INFO] 2026-10-16 01:15:51,794 [root] [klippy:_connect:258] data_send: b'\x81\t\x00\x06\x01F\x00\x00\xc8B'
[INFO] 2026-10-16 01:15:51,807 [root] [gcode:respond_info:453] data_send:b'\x81\t\x00\x06\x01F\x00\x00\xc8B' recv_result:b'\xf7\x81\x07\x00\x06\x00\x00\xc8B\xd4'
[INFO] 2026-10-16 01:15:51,831 [root] [klippy:_connect:258] data_send: b'\x81\x05\x00\x06\x02V'
[INFO] 2026-10-16 01:15:51,861 [root] [gcode:respond_info:453] data_send:b'\x81\x05\x00\x06\x02V' recv_result:b'\xf7\x81\x07\x00\x06B`\xe5<\x9c'
[INFO] 2026-10-16 01:15:51,866 [root] [klippy:_connect:258] data_send: b'\x81\x05\x00\x06\x02W'
[INFO] 2026-10-16 01:15:51,881 [root] [gcode:respond_info:453] data_send:b'\x81\x05\x00\x06\x02W' recv_result:b'\xf7\x81\x07\x00\x06\x00\x00\xc0@r'
[INFO] 2026-10-16 01:15:51,905 [root] [klippy:_connect:258] data_send: b'\x81\x05\x00\x06\x02f'
[INFO] 2026-10-16 01:15:51,917 [root] [gcode:respond_info:453] data_send:b'\x81\x05\x00\x06\x02f' recv_result:b'\xf7\x81\x07\x00\x06\x00\x00 @1'
[INFO] 2026-10-16 01:15:51,921 [root] [klippy:_connect:258] data_send: b'\x81\x05\x00\x06\x02g'
[INFO] 2026-10-16 01:15:51,938 [root] [gcode:respond_info:453] data_send:b'\x81\x05\x00\x06\x02g' recv_result:b'\xf7\x81\x07\x00\x06\x00\x00HCe'
[INFO] 2026-10-16 01:15:51,958 [root] [klippy:_connect:258] data_send: b'\x81\x05\x00\x06\x02t'
[INFO] 2026-10-16 01:15:51,970 [root] [gcode:respond_info:453] data_send:b'\x81\x05\x00\x06\x02t' recv_result:b'\xf7\x81\x07\x00\x06\x00\x00\x80?S'
[INFO] 2026-10-16 01:15:51,978 [root] [klippy:_connect:258] data_send: b'\x81\x05\x00\x06\x02v'
[INFO] 2026-10-16 01:15:51,990 [root] [gcode:respond_info:453] data_send:b'\x81\x05\x00\x06\x02v' recv_result:b'\xf7\x81\x07\x00\x06\x00\x00\x00\x00X'
[INFO] 2026-10-16 01:15:51,995 [root] [klippy:_connect:258] data_send: b'\x81\x05\x00\x06\x02w'
[INFO] 2026-10-16 01:15:52,012 [root] [gcode:respond_info:453] data_send:b'\x81\x05\x00\x06\x02w' recv_result:b'\xf7\x81\x07\x00\x06\x00\x00zE\xa4'
[INFO] 2026-10-16 01:15:52,016 [root] [klippy:_connect:258] data_send: b'\x81\x05\x00\x06\x02x'
[INFO] 2026-10-16 01:15:52,032 [root] [gcode:respond_info:453] data_send:b'\x81\x05\x00\x06\x02x' recv_result:b'\xf7\x81\x07\x00\x06\x00\x00zE\xa4'
[INFO] 2026-10-16 01:15:52,038 [root] [klippy:_connect:258] data_send: b'\x81\x05\x00\x06\x02y'
[INFO] 2026-10-16 01:15:52,051 [root] [gcode:respond_info:453] data_send:b'\x81\x05\x00\x06\x02y' recv_result:b'\xf7\x81\x07\x00\x06\x00\x00\xc8C\xd3'
[INFO] 2026-10-16 01:15:52,124 [root] [klippy:_connect:258] data_send: b'\x81\x05\x00\x06\x02\xbb'
[INFO] 2026-10-16 01:15:52,152 [root] [gcode:respond_info:453] data_send:b'\x81\x05\x00\x06\x02\xbb' recv_result:b'\xf7\x81\x07\x00\x06\x9a\x99\x99>\xe7'
[INFO] 2026-10-16 01:15:52,157 [root] [klippy:_connect:258] data_send: b'\x81\x05\x00\x06\x02\xbc'
[INFO] 2026-10-16 01:15:52,172 [root] [gcode:respond_info:453] data_send:b'\x81\x05\x00\x06\x02\xbc' recv_result:b'\xf7\x81\x07\x00\x06\n\xd7#<\xd8'
[INFO] 2026-10-16 01:15:52,182 [root] [klippy:_connect:258] data_send: b'\x81\t\x00\x06\x01\xbc\xcd\xcc\xcc='
[INFO] 2026-10-16 01:15:52,196 [root] [gcode:respond_info:453] data_send:b'\x81\t\x00\x06\x01\xbc\xcd\xcc\xcc=' recv_result:b'\xf7\x81\x07\x00\x06\xcd\xcc\xcc=\xd9'
[INFO] 2026-10-16 01:15:52,242 [root] [klippy:_connect:258] data_send: b'\x81\x05\x00\x06\x02\xe2'
[INFO] 2026-10-16 01:15:52,253 [root] [gcode:respond_info:453] data_send:b'\x81\x05\x00\x06\x02\xe2' recv_result:b'\xf7\x81\x03\x00\x06\xaf'
[INFO] 2026-10-16 01:15:52,259 [root] [klippy:_connect:258] Updata param
[INFO] 2026-10-16 01:15:52,261 [root] [klippy:_connect:258] data_send: b'\x81\x04\x00\x06\x03'
[INFO] 2026-10-16 01:15:52,275 [root] [gcode:respond_info:453] data_send:b'\x81\x04\x00\x06\x03' recv_result:b'\xf7\x81\x04\x00\x06\x01!'
[INFO] 2026-10-16 01:15:54,319 [root] [klippy:_connect:258] update_flash_param stepper:5
[INFO] 2026-10-16 01:15:54,331 [root] [klippy:_connect:258] data_send: b'\x85\x05\x00\x06\x02\t'
[INFO] 2026-10-16 01:15:55,339 [root] [gcode:respond_info:453] data_send:b'\x85\x05\x00\x06\x02\t' recv_result:None
[INFO] 2026-10-16 01:15:55,341 [root] [klippy:_connect:258] data_send: b'\x85\x05\x00\x06\x02\t'
[INFO] 2026-10-16 01:15:56,354 [root] [gcode:respond_info:453] data_send:b'\x85\x05\x00\x06\x02\t' recv_result:None
[INFO] 2026-10-16 01:15:56,357 [root] [klippy:_connect:258] data_send: b'\x85\x05\x00\x06\x02\t'
[INFO] 2026-10-16 01:15:57,370 [root] [gcode:respond_info:453] data_send:b'\x85\x05\x00\x06\x02\t' recv_result:None
[INFO] 2026-10-16 01:16:03,705 [root] [klippy:_connect:258] Updata param
[INFO] 2026-10-16 01:16:03,706 [root] [klippy:_connect:258] data_send: b'\x85\x04\x00\x06\x03'
[INFO] 2026-10-16 01:16:04,715 [root] [gcode:respond_info:453] data_send:b'\x85\x04\x00\x06\x03' recv_result:None
[INFO] 2026-10-16 01:16:04,826 [root] [auto_addr_wrapper:send_package:468] cmd_485_send_data_with_response timeout
"""


class _FakeBus:
    """Antwortet regelbasiert wie die Motoren (keine Hardware).

    values: {(addr, id): wert} für 06 [02,id]; fehlend -> Config-/Ersatzwert.
    silent: {(runde, addr)} – 0E [02] ohne Antwort in dieser Runde.
    """

    def __init__(self, values=None, silent=(), types=None, dead=()):
        self.calls = []
        self.values = dict(values or {})
        self.silent = set(silent)
        self.types = types or {}
        self.dead = set(dead)
        self.round = 0

    def transact(self, addr, fun, data=(), **kw):
        data = list(data)
        self.calls.append((addr, fun, data, kw))
        if addr in self.dead:
            raise TimeoutError("FakeBus: 0x%02X tot" % addr)
        if fun == M.FUNC_ADDR and data == [1]:
            self.round += 1
            raise TimeoutError("Broadcast")
        if fun == M.FUNC_ADDR and data == [2]:
            if (self.round, addr) in self.silent:
                raise TimeoutError("still")
            return bytes([addr])
        if fun == M.FUNC_SET_MODE:
            return bytes(data)
        if fun == M.FUNC_PROTECTION and data == [0x0B]:
            return bytes(8)
        if fun == M.FUNC_PROTECTION and data == [0x05]:
            raise TimeoutError("0C 05 nie beantwortet")
        if fun == M.FUNC_SYS_PARAM and data[0] == 2:
            pid = data[1]
            if pid in HOST_ONLY_IDS:
                return b""
            vt = self.types.get(pid, "float")
            v = self.values.get((addr, pid), 0)
            return struct.pack(TYPE_FORMATS[vt], v if vt == "float" else int(v))
        if fun == M.FUNC_SYS_PARAM and data[0] == 1:
            return bytes(data[2:])
        if fun == M.FUNC_SYS_PARAM and data == [3]:
            return bytes([1])
        raise AssertionError("FakeBus: unerwartet 0x%02X %02x %r" % (addr, fun, data))


def selftest():
    fails = 0
    total = 0

    def check(name, cond):
        nonlocal fails, total
        total += 1
        if not cond:
            fails += 1
            print("FEHLER:", name)

    def raises(exc, fn, *a, **kw):
        try:
            fn(*a, **kw)
        except exc:
            return True
        return False

    # 1. Config (echte Live-Config + Werksvorlage motor_control.cfg)
    cfg = load_motor_control_config(DEFAULT_CONFIG)
    check("cfg switch/retries", cfg["switch"] == 1 and cfg["retries"] == 4)
    check("cfg closed loop", cfg["motor_closed_loop"] == ["x", "y", "z", "z1", "e"])
    check("cfg pins y invert", cfg["pins"]["y"]["dir"] == {"raw": "!PB7", "pin": "PB7", "invert": True,
                                                           "level": 0, "phys": 1})
    check("cfg e nur stall", cfg["pins"]["e"]["dir"] is None and cfg["pins"]["e"]["stall"] == "nozzle_mcu:PB12")
    check("cfg param x kp", cfg["params"]["x"]["controller_pos_loop_pid_param_kp"] == 100)
    check("cfg param z ki", cfg["params"]["z"]["controller_spd_loop_pid_param_ki"] == 2.0)
    check("cfg e ohne params", cfg["params"]["e"] == {})
    check("cfg include-notiz", any("motor_control.cfg" in n for n in cfg["notizen"]))
    check("cfg corexy-zuordnung", stepper_mapping(cfg) == {"x": "stepper_y", "y": "stepper_x",
                                                           "z": "stepper_z", "z1": "stepper_z1"})
    t2 = parse_cfg_sections("[a]\nk: 1 # c\nm = 2\n  weiter\n[a]\nn: 3\n#*# [x]\n#*# q: 1\n")
    check("cfg parser", t2 == {"a": {"k": "1", "m": "2\nweiter", "n": "3"}})
    check("cfg ohne motor_control", raises(ValueError, parse_motor_control_config, "[x]\na: 1\n"))
    check("pin-spec ungueltig", raises(ValueError, parse_pin_level, "PB9"))

    # 2. Strapping
    check("strap formel", [strapping_address(d, s) for d, s in ((0, 0), (1, 0), (0, 1), (1, 1))]
          == [0x81, 0x82, 0x83, 0x84])
    st = strapping_table(cfg)
    check("strap 4 motoren", [(r["motor"], r["adresse"], r["ok"]) for r in st]
          == [("x", 0x81, True), ("y", 0x82, True), ("z", 0x83, True), ("z1", 0x84, True)])
    bad = dict(cfg, pins=dict(cfg["pins"]))
    bad["pins"]["x"] = dict(cfg["pins"]["x"], dir=parse_pin_level("PB9,1"))
    check("strap falsch -> ValueError", raises(ValueError, build_init_plan, bad))
    check("strap pegel guard", raises(ValueError, strapping_address, 2, 0))

    # 3. Plan
    plan = build_init_plan(cfg)
    phases = []
    for s in plan.steps:
        if not phases or phases[-1] != s.phase:
            phases.append(s.phase)
    check("plan phasen", phases == [p for p, _ in PHASES])
    check("plan gpio 8+8", len([s for s in plan.steps if s.art == "gpio"]) == 16)
    r = [s for s in plan.steps if s.gruppe == "runde"]
    check("plan runde", [(s.art, s.bus, s.addr) for s in r] ==
          [("broadcast", "ttyS5", 0xFF), ("broadcast", "transparent", 0xFF),
           ("addr_read", "ttyS5", 0x81), ("addr_read", "ttyS5", 0x82), ("addr_read", "ttyS5", 0x83),
           ("addr_read", "ttyS5", 0x84), ("addr_read", "transparent", 0x81), ("eval", "host", None)])
    check("plan pausen", r[1].pause_danach == 0.1 and r[2].pause_davor == 0.05 and r[-1].pause_danach == 1.0)
    check("plan stall z/z1/e", [(s.ziel, s.bus, s.addr) for s in plan.phase_steps("P4")]
          == [("z", "ttyS5", 0x83), ("z1", "ttyS5", 0x84), ("e", "transparent", 0x81)])
    check("plan 0c", [s.payload for s in plan.phase_steps("P5")] == [[0x0B]] * 5
          and [s.payload for s in plan.phase_steps("P6")] == [[0x05]] * 5)
    reads = defaultdict(list)
    for s in plan.phase_steps("P8"):
        if s.art == "param_read":
            reads[s.ziel].append(s.meta["param_id"])
    cfg_x = [9, 10, 25, 70, 86, 87, 102, 103, 116, 118, 119, 120, 121, 187, 188, 226]
    check("plan reads x", reads["x"] == cfg_x)
    check("plan reads z (ohne 226)", reads["z"] == cfg_x[:-1])
    check("plan reads e", reads["e"] == [9, 10, 25])
    e_r = [s for s in plan.phase_steps("P8") if s.ziel == "e" and s.art != "host"]
    check("plan e transparent markiert", all(s.bus == "transparent" and s.addr == 0x81 and
                                             "Nachbau fehlt" in s.hinweis for s in e_r))
    w70 = [s for s in plan.phase_steps("P8") if s.art == "param_write" and s.ziel == "x"
           and s.meta["param_id"] == 70][0]
    check("plan write x70 = live", bytes(w70.frame()[1:-1]).hex(" ") == "81 09 00 06 01 46 00 00 c8 42")
    s226 = [s for s in plan.phase_steps("P8") if s.meta.get("param_id") == 226]
    check("plan 226 inaktiv", len(s226) == 4 and not any(s.aktiv for s in s226))
    check("plan schreibend markiert", all(s.schreibend for s in plan.steps if s.art in WRITING_ARTS)
          and not any(s.schreibend for s in plan.steps if s.art not in WRITING_ARTS))
    check("plan keine 12 02", not any(s.fun == M.FUNC_UUID for s in plan.steps))
    psc = build_init_plan(cfg, stock_compatible=True)
    e_sc = [s for s in psc.phase_steps("P8") if s.ziel == "e" and s.art != "host"]
    check("stock_compatible e 0x85", all(s.bus == "ttyS5" and s.addr == 0x85 for s in e_sc))
    check("stock_compatible 226 aktiv", all(s.aktiv for s in psc.phase_steps("P8")
                                            if s.meta.get("param_id") == 226))
    c0 = dict(cfg, switch=0)
    p0 = build_init_plan(c0)
    check("switch=0 ohne bus", not any(s.sig() and s.art != "gpio" for s in p0.steps))
    if os.path.exists(DEFAULT_PTABLE):
        tab = load_param_table(DEFAULT_PTABLE)
        check("ptable == eingebettet", [(t["id"], t["name"], t["configurable"],
                                         t["value_type"] if t["configurable"] else None) for t in tab]
              == [(t["id"], t["name"], t["configurable"], t["value_type"]) for t in default_param_table()])
    else:
        print("Hinweis: ptable_fix.json nicht vorhanden – Tabellenabgleich übersprungen")

    # 4. Trockenlauf
    txt = dry_run(plan)
    check("dry-run inhalt", "0x81 + 2·STEP + DIR" in txt and "P8 Param-Abgleich" in txt
          and "transparent – Nachbau fehlt" in txt and "NICHT gesendet" in txt)
    check("dry-run kurz", "Leseschritte" in dry_run(plan, details=False))

    # 5. Log-Abgleich gegen den eingebetteten Auszug
    lines = BOOT_LOG_EXCERPT.strip("\n").split("\n")
    res = compare_with_log(plan, lines)
    check("cmp runden 2", res["runden"] == 2)
    ri = res["runden_info"]
    check("cmp runde 1: z + e", sorted(m for m, a in ri[1] if a) == ["e", "z"])
    check("cmp runde 2: alle", sorted(m for m, a in ri[2] if a) == ["e", "x", "y", "z", "z1"])
    check("cmp keine abweichung", res["abweichungen"] == [])
    check("cmp nichts zusaetzlich", res["zusaetzlich"] == [])
    check("cmp polls 12 02", len(res["polls"]) == 4 and all(e["fun"] == 0x12 for e in res["polls"]))
    check("cmp gpio 16 ok", len(res["gpio_bericht"]) == 16 and all(x[2] for x in res["gpio_bericht"]))
    fz = set((s.phase, s.ziel) for s in res["fehlend"])
    check("cmp fehlend nur P8 y/z/z1/e", fz == {("P8", "y"), ("P8", "z"), ("P8", "z1"), ("P8", "e")})
    check("cmp fehlend e = 10/25", sorted(s.meta["param_id"] for s in res["fehlend"]
                                          if s.ziel == "e") == [10, 25])
    bw = dict((s.nr, (s, h)) for s, h, _ in res["bewusst"])
    e9 = [v for v in bw.values() if v[0].ziel == "e" and v[0].meta.get("param_id") == 9]
    check("cmp e ID9 stock 0x85 3x", len(e9) == 1 and len(e9[0][1]) == 3)
    check("cmp e [03] stock", any(v[0].ziel == "e" and v[0].art == "param_update" for v in bw.values()))
    check("cmp 226 bewusst", any(v[0].meta.get("param_id") == 226 and v[0].ziel == "x" for v in bw.values()))
    rows = [r for r in res["param_bericht"] if r["motor"] == "x"]
    check("cmp x writes 70/188", sorted(r["id"] for r in rows if r["geschrieben"]) == [70, 188])
    check("cmp x konsistent", all(r["konsistent"] for r in rows if r["konsistent"] is not None)
          and sum(1 for r in rows if r["konsistent"] is not None) == 12)
    check("cmp x anonymisiert", [r["gelesen"] for r in rows if r["id"] in (9, 25)] == [100.0, 4.0])
    check("cmp format", "Client-Poll" in format_compare(res)
          and "<kalib.>" in format_compare(res, mask_calib=True))
    check("cmp ohne boot", raises(ValueError, compare_with_log, plan, ["nichts"]))
    lt = ("[INFO] x [root] [klippy:_connect:258] data_send: b'\\x83\\t\\x00\\x06\\x01V)\\\\\\x0f='\n"
          "[INFO] x [root] [gcode:respond_info:453] data_send:b'\\x83\\t\\x00\\x06\\x01V)\\\\\\x0f=' "
          "recv_result:b'\\xf7\\x83\\x07\\x00\\x06)\\\\\\x0f=\\x9e'")
    ev, _, _, _, _ = parse_boot_events(["... initial set_pin_io done"] + lt.split("\n"))
    check("parser backslash", len(ev) == 1 and ev[0]["data"] == (1, 0x56, 0x29, 0x5C, 0x0F, 0x3D)
          and ev[0]["tag"] == "klippy:_connect:258")

    # 6. Schutz
    fb = _FakeBus()
    check("run ohne confirm", raises(PermissionError, run, fb, plan) and fb.calls == [])
    check("run confirm=1", raises(PermissionError, run, fb, plan, confirm=1) and fb.calls == [])
    vals = {}
    for s in plan.phase_steps("P8"):
        if s.art == "param_read" and s.bus == "ttyS5":
            cv = s.meta["config_value"]
            vals[(s.addr, s.meta["param_id"])] = 1.5 if cv is None else cv
    vals[(0x81, 70)] = 300.0           # X Kp wie Werkszustand -> Write
    vals[(0x83, 188)] = 0.01           # Z err_time -> Write
    vals[(0x84, 86)] = 0.035 + 5e-7    # in abs_tol -> kein Write
    types = dict(PARAM_TYPES_CONFIGURABLE_NONFLOAT)
    fb = _FakeBus(vals, types=types)
    rep = run(fb, plan, confirm=True, sleep=lambda t: None)
    sent = [(a, f, d) for a, f, d, _ in fb.calls]
    check("run lesend: keine Writes", not any(f in (M.FUNC_SET_MODE,) or (f == M.FUNC_PROTECTION and d == [5])
                                             or (f == M.FUNC_SYS_PARAM and d[0] in (1, 3))
                                             or (f == M.FUNC_ADDR and d == [1]) for a, f, d in sent))
    check("run lesend: reads da", sum(1 for a, f, d in sent if f == M.FUNC_SYS_PARAM) == 4 * 15)
    check("run lesend: meldet würde-nicht", any("allow_write fehlt" in r["text"] for r in rep))

    # 7. FakeBus-Lauf mit confirm + allow_write (+ strapping_ok) gegen den Plan
    fb = _FakeBus(vals, types=types)
    rep = run(fb, plan, confirm=True, allow_write=True, strapping_ok=True, sleep=lambda t: None)
    sent = [(a, f, tuple(d)) for a, f, d, _ in fb.calls]
    exp = []
    for s in plan.steps:
        if not (s.aktiv and s.bus == "ttyS5" and s.sig()):
            continue
        if s.art == "param_write":
            v = vals.get((s.addr, s.meta["param_id"]))
            if _isclose(v, s.meta["config_value"]):
                continue
        exp.append((s.addr, s.fun, tuple(s.payload)))
    check("run frames == plan", sent == exp)
    check("run writes", [(a, d[1]) for a, f, d in sent if f == M.FUNC_SYS_PARAM and d[0] == 1]
          == [(0x81, 70), (0x83, 188)])
    check("run retries=0", all(kw.get("retries") == 0 for _, _, _, kw in fb.calls))
    check("run transparent übersprungen", any("transparent" in r["text"] and r["status"] == "übersprungen"
                                             for r in rep))
    # zweite Runde nötig (Z1 in Runde 1 still)
    fb = _FakeBus(vals, types=types, silent={(1, 0x84)})
    rep = run(fb, plan, confirm=True, allow_write=True, strapping_ok=True, sleep=lambda t: None)
    check("run 2 runden", sum(1 for a, f, d, _ in fb.calls if f == M.FUNC_ADDR and d == [1]) == 2
          and any(r["text"].startswith("Runde 2: alle") for r in rep))
    # Abbruch: Z1 tot -> 4 Runden, keine Stall/Param-Frames
    fb = _FakeBus(vals, types=types, dead={0x84})
    rep = run(fb, plan, confirm=True, allow_write=True, strapping_ok=True, sleep=lambda t: None)
    check("run abbruch", rep[-1]["status"] in ("abbruch", "übersprungen")
          and any(r["status"] == "abbruch" for r in rep)
          and sum(1 for a, f, d, _ in fb.calls if f == M.FUNC_ADDR and d == [1]) == 4
          and not any(f in (M.FUNC_SET_MODE, M.FUNC_SYS_PARAM, M.FUNC_PROTECTION) for a, f, d, _ in fb.calls))
    # ohne strapping_ok kein Broadcast
    fb = _FakeBus(vals, types=types)
    run(fb, plan, confirm=True, allow_write=True, sleep=lambda t: None)
    check("run ohne strapping_ok kein broadcast", not any(a == 0xFF for a, f, d, _ in fb.calls))
    # stock_compatible: 0x85 wird nie angesprochen
    fb = _FakeBus(vals, types=types)
    run(fb, psc, confirm=True, allow_write=True, strapping_ok=True, sleep=lambda t: None)
    check("run nie 0x85", not any(a == 0x85 for a, f, d, _ in fb.calls))

    print("SELBSTTEST: %s (%d Prüfungen)" % ("ok" if fails == 0 else "%d FEHLER" % fails, total))
    return fails


# =====================================================================
# CLI
# =====================================================================

def main(argv=None):
    ap = argparse.ArgumentParser(description="Motor-Init-Plan (K2 Plus): Trockenlauf, "
                                             "Log-Abgleich, Selbsttest. Sendet nichts.")
    ap.add_argument("--dry-run", action="store_true", help="Plan ausgeben (kein Bus)")
    ap.add_argument("--compare", metavar="KLIPPY_LOG", help="Plan mit Boot-Log abgleichen")
    ap.add_argument("--config", default=DEFAULT_CONFIG, help="printer.cfg (Includes werden aufgelöst)")
    ap.add_argument("--include-dir", action="append", default=[],
                    help="zusätzlicher Suchordner für [include] (vor der Werksvorlage)")
    ap.add_argument("--ptable", help="ptable_fix.json statt eingebetteter Tabelle")
    ap.add_argument("--stock-compatible", action="store_true",
                    help="Stock-Verhalten (E-Param an 0x85, ID 226 senden)")
    ap.add_argument("--boot", type=int, default=-1, help="Boot-Nr. im Log (1-basiert, -1 = letzter)")
    ap.add_argument("--kurz", action="store_true", help="Trockenlauf ohne Einzel-Param-Schritte")
    ap.add_argument("--ohne-werte", action="store_true", help="Abgleich ohne Werte-Tabelle")
    ap.add_argument("--maskieren", action="store_true",
                    help="Kalibrierwerte (IDs 9/25) in der Werte-Tabelle ausblenden")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not (a.dry_run or a.compare):
        ap.print_help()
        return 2
    cfg = load_motor_control_config(a.config, tuple(a.include_dir) + DEFAULT_INCLUDE_DIRS)
    table = load_param_table(a.ptable) if a.ptable else None
    plan = build_init_plan(cfg, table, stock_compatible=a.stock_compatible)
    if a.dry_run:
        print(dry_run(plan, details=not a.kurz))
    if a.compare:
        with open(a.compare, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().split("\n")
        boot = a.boot - 1 if a.boot > 0 else a.boot
        res = compare_with_log(plan, lines, boot)
        if a.dry_run:
            print()
        print(format_compare(res, show_values=not a.ohne_werte, mask_calib=a.maskieren))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
