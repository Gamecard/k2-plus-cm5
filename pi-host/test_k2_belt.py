#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_k2_belt.py – Offline-Tests für k2_belt (ohne Klipper, ohne Hardware).

Aufruf: ``cd pi-host && python3 test_k2_belt.py``

Prüft:
 1. Frame-Bau: Requests == Live-Wire aus klippy.log (Belt 0x21, Motor-0x05),
    CRC == nachbau/serial485 und == Live-CRCs (Protokollanalyse).
 2. Parsing der Live-Antworten (Version/Flash/ADC/Echo), Fehlerframes.
 3. Rechenwege gegen Live-Werte (Steigung/Achsabschnitt/Kraft mdlx+mdly)
    und Live-Regelschritte (Schrittweite je Iteration).
 4. Rechenwege gegen das ORIGINAL belt_mdl-live.py (direkt importiert mit
    Attrappen für BoxAction/Klipper; numpy falls vorhanden).
 5. Komplettabläufe Port vs. Original am gleichen Belt-Simulator:
    INFO/TEST/SET/MOVE/CALI → identische Wire-Frames, G-Codes, Pausen,
    Kräfte, Flash-Inhalte; dazu SET-Timeout (key718) und „zu straff“.
 6. Fehlerfälle (key714/715/716/720, Antwort-Prüfung, CALI-Schutz).
 7. Freigabe-Sperren (allow_write/allow_move/DRY_RUN): nichts gesendet.
 8. Retries über echtes serial485.Serial485 (1+4 Sendungen) + Parameter.
 9. Default-Zyklus: Motor-0x05 intern (retries=0) == Live-Frames.
10. Klipper-Hülle via FakePrinter (Mux-G-Codes, Aliase, Webhook,
    command_error statt Shutdown, Bus-Lookup 'k2_485', Config-Fehler).
11. Geteilter Bus: Belt + Box-Poll parallel über EINE Serial485-Instanz.
12. Python-3.9-Syntax.
13. Belt-FW-Analyse 18.10.2026: signed ADC/Kalibrierwerte (Nachrechnung
    beider Live-Achsen, lockerer Riemen statt Phantomkraft/key716),
    Messfrische nach fun 8, Busruhe vor Belt-Requests, eigener
    Sendeschutz (fun 2 nur [03], fun 8 1..max_steps_units).

Abläufe gegen das Original (5) laufen mit ``STOCK_COMPAT`` (Messfrische,
Busruhe und Weggrenze aus) – genau dann ist der Port frame-/pausengleich.

Datenschutz: keine UUIDs/Seriennummern – nur Belt-Messwerte (ADC/Kraft).
"""

import ast
import re
import warnings
import importlib.util
import os
import sys
import threading
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_REPO = next((d for d in (os.path.dirname(_HERE), os.path.dirname(os.path.dirname(_HERE)))
              if os.path.isdir(os.path.join(d, "nachbau"))), os.path.dirname(_HERE))
_NACHBAU = os.path.join(_REPO, "nachbau")
if os.path.isdir(_NACHBAU) and _NACHBAU not in sys.path:
    sys.path.insert(0, _NACHBAU)

import k2_belt as KB  # noqa: E402
import serial485 as S485  # noqa: E402

ORIG_PATH = os.path.join(_REPO, "belt_mdl-live.py")

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    if not cond:
        print("  FEHLER: %s %s" % (name, detail))
    return bool(cond)


def hexb(s):
    return bytes(int(x, 16) for x in s.replace(",", " ").split())


# Optionen, mit denen der Port Frame/Pause-gleich zum Original läuft
# (Verbesserungen aus der Belt-FW-Analyse 18.10.2026 abgeschaltet).
STOCK_COMPAT = {"max_steps_units": 50, "move_settle": 0, "measure_discard": 0,
                "bus_gap": 0}

# Live-Kalibrierung (Messwerte, keine Kennungen) – Live-Log 2026-10-12
LIVE_X = {"idl": 4294424469, "full": 4294136140,
          "slope": -6.936520433268644e-05, "intercept": 298023.6307834735}
LIVE_Y = {"idl": 4294009609, "full": 4293726909,
          "slope": -7.074637424851708e-05, "intercept": 303925.6108250424}


# =====================================================================
# Belt-Simulator (gemeinsam für Original und Port)
# =====================================================================

class BeltSim:
    """Simuliertes Riemenspannmodul. Kraft linear in der Schieberposition,
    ADC aus der Live-Kalibrierung zurückgerechnet. ``settle`` = Kraftverlust
    je M84 (Riemen setzt sich nach dem Rüttel-Zyklus)."""

    def __init__(self, addr=0x21, flash=(0, LIVE_X["idl"], LIVE_X["full"]),
                 base=129.7, k=0.06, settle=1.5, cal=(LIVE_X["idl"], LIVE_X["full"]),
                 version=(1, 2, 3, 4)):
        self.addr = addr
        self.flash = list(flash)
        self.base = base
        self.k = k
        self.settle = settle
        self.cal = cal
        self.mech = 0
        self.version = list(version)
        self.requests = []      # (addr, fun, data)
        self.silent_funs = set()

    def tension(self):
        return self.base + self.k * self.mech

    def adc(self):
        idl, full = self.cal
        t = self.tension()
        return int(round(idl + (t - 140.0) * (full - idl) / 20.0)) & 0xFFFFFFFF

    def shake(self):
        self.base -= self.settle

    def handle(self, addr, fun, data):
        """→ Antwortdaten (bytes) oder None (keine Antwort)."""
        self.requests.append((addr, fun, bytes(data)))
        if addr != self.addr or fun in self.silent_funs:
            return None
        if fun == 0x00:
            return bytes(self.version)
        if fun == 0x02:
            out = [3]
            for v in self.flash:
                out += KB.split_to_bytes(v)
            return bytes(out)
        if fun == 0x04:
            d = bytes(data)
            self.flash = [KB.bytes_to_int(d[1:5]), KB.bytes_to_int(d[5:9]),
                          KB.bytes_to_int(d[9:13])]
            return d
        if fun == 0x06:
            return bytes(KB.split_to_bytes(self.adc()))
        if fun == 0x08:
            d = bytes(data)
            dist = KB.bytes_to_int(d[1:5])
            self.mech += dist if d[0] == 1 else -dist
            return d
        return None


class SimSerial:
    """Fake-pyserial für serial485.Serial485: beantwortet Requests per Sim.
    ``drop`` = so viele Antworten verschlucken (Retry-Test)."""

    def __init__(self, sims, drop=0):
        self.sims = sims if isinstance(sims, (list, tuple)) else [sims]
        self.drop = drop
        self.written = []
        self._buf = bytearray()
        self.busy = False
        self.overlaps = 0
        self.extra = {}   # (addr, fun) -> callable(data) -> bytes|None

    def reset_input_buffer(self):
        self._buf.clear()

    def write(self, frame):
        if self.busy:
            self.overlaps += 1
        frame = bytes(frame)
        self.written.append(frame)
        fun, data = S485.parse_frame(frame)
        addr = frame[1]
        resp = None
        if (addr, fun) in self.extra:
            resp = self.extra[(addr, fun)](data)
        else:
            for sim in self.sims:
                r = sim.handle(addr, fun, data)
                if r is not None:
                    resp = r
                    break
        if resp is not None and self.drop > 0:
            self.drop -= 1
            resp = None
        if resp is not None:
            self.busy = True
            self._buf += S485.build_frame(addr, fun, list(resp))

    def read(self, n):
        if self._buf:
            chunk = bytes(self._buf[:n])
            del self._buf[:n]
            if not self._buf:
                self.busy = False
            return chunk
        time.sleep(0.0005)
        return b""

    def close(self):
        pass


def sim_set(sim, params):
    """Test-Hilfe: Referenzkraft am Simulator einstellen (Kalibrier-Gewicht)."""
    if "tension" in params:
        sim.base = float(params["tension"]) - sim.k * sim.mech


def make_bus(sims, drop=0):
    ser = SimSerial(sims, drop=drop)
    return S485.Serial485("sim", ser=ser), ser


# =====================================================================
# Original laden (belt_mdl-live.py mit Attrappen)
# =====================================================================

class OrigCmdError(Exception):
    pass


def load_original():
    if not os.path.isfile(ORIG_PATH):
        return None, "Original fehlt: %s" % ORIG_PATH
    note = ""
    pkg = types.ModuleType("_k2orig")
    pkg.__path__ = []
    sys.modules["_k2orig"] = pkg
    bw = types.ModuleType("_k2orig.box_wrapper")
    bw.BoxAction = object  # Attrappe (im Original ungenutzt)
    sys.modules["_k2orig.box_wrapper"] = bw
    try:
        import numpy  # noqa: F401
    except ImportError:
        npm = types.ModuleType("numpy")
        npm.array = lambda x: list(x)

        def _polyfit(x, y, deg):
            s, i = KB.fit_line(x[0], x[1], y[0], y[1])
            return [s, i]
        npm.polyfit = _polyfit
        sys.modules["numpy"] = npm
        note = " (numpy fehlt – Attrappe, Vergleich nur eingeschränkt)"
    spec = importlib.util.spec_from_file_location("_k2orig.belt_mdl", ORIG_PATH)
    mod = importlib.util.module_from_spec(spec)
    mod.__package__ = "_k2orig"
    spec.loader.exec_module(mod)
    return mod, note


class OGcode:
    def __init__(self, sim=None):
        self.infos = []
        self.scripts = []
        self.mux = {}
        self.sim = sim

    def respond_info(self, msg, log=True):
        self.infos.append(str(msg))

    def register_mux_command(self, cmd, key, value, func, desc=None):
        self.mux.setdefault(cmd, {})[value] = func

    def run_script_from_command(self, script):
        self.scripts.append(script)
        if script == "M84" and self.sim is not None:
            self.sim.shake()


class OReactor:
    def __init__(self):
        self.pauses = []

    def monotonic(self):
        return 0.0

    def pause(self, waketime):
        self.pauses.append(waketime - 0.0)


class OSerial:
    """Ersatz für Crealitys serial_485-Objekt: cmd_send_data_with_response
    (Wire ohne F7/CRC, Timeout) → voller Antwort-Frame oder None."""

    def __init__(self, sim):
        self.sim = sim
        self.wires = []

    def cmd_send_data_with_response(self, wire, timeout):
        wire = bytes(wire)
        self.wires.append(wire)
        resp = self.sim.handle(wire[0], wire[3], wire[4:])
        if resp is None:
            return None
        return S485.build_frame(wire[0], wire[3], list(resp))


class OWebhooks:
    def __init__(self):
        self.mux = {}

    def register_mux_endpoint(self, path, key, value, cb):
        self.mux.setdefault(path, {})[value] = cb


class OPrinter:
    command_error = OrigCmdError

    def __init__(self, sim):
        self.reactor = OReactor()
        self.gcode = OGcode(sim)
        self.serial = OSerial(sim)
        self.webhooks = OWebhooks()

    def lookup_object(self, name):
        return {"gcode": self.gcode, "serial_485 serial485": self.serial,
                "webhooks": self.webhooks}[name]

    def get_reactor(self):
        return self.reactor


class OConfig:
    def __init__(self, printer, name, opts):
        self.printer = printer
        self.name = name
        self.opts = opts

    def get_printer(self):
        return self.printer

    def get_name(self):
        return self.name

    def getint(self, k, default=None, minval=None, maxval=None):
        return int(self.opts.get(k, default))

    def getfloat(self, k, default=None, minval=None, maxval=None):
        return float(self.opts.get(k, default))


class OGcmd:
    def __init__(self, params):
        self.params = params

    def get_int(self, k, default=None):
        v = self.params.get(k, default)
        return None if v is None else int(v)


def run_original(orig, sim, steps, name="mdlx"):
    p = OPrinter(sim)
    obj = orig.BELT_MDL(OConfig(p, "belt_mdl %s" % name,
                                {"ch_max_dis": 5, "ch_best_n": 140,
                                 "ch_best_error": 0.15}))
    err = None
    try:
        for cmd, params in steps:
            if cmd == "SIM":
                sim_set(sim, params)
                continue
            obj.gcode.mux["BELT_MDL_" + cmd][name](OGcmd(params))
    except OrigCmdError as exc:
        err = str(exc)
    return {"wires": list(p.serial.wires), "scripts": list(p.gcode.scripts),
            "pauses": list(p.reactor.pauses), "err": err, "obj": obj,
            "infos": p.gcode.infos}


def run_port(sim, steps, name="mdlx", cfg_over=None):
    bus, ser = make_bus(sim)
    opts = {"ch_best_error": 0.15, "allow_write": True, "allow_move": True,
            "cycle_gcode": "\n".join(KB.STOCK_CYCLE_GCODE)}
    opts.update(STOCK_COMPAT)
    opts.update(cfg_over or {})
    cfg = KB.parse_options(opts, name)
    scripts, pauses, infos = [], [], []

    def run_gcode(line):
        scripts.append(line)
        if line == "M84":
            sim.shake()
    ctl = KB.K2BeltController(name, cfg, bus=bus, pause=pauses.append,
                              respond=infos.append, run_gcode=run_gcode)
    err = None
    try:
        for cmd, params in steps:
            if cmd == "SIM":
                sim_set(sim, params)
            elif cmd == "INFO":
                ctl.info()
            elif cmd == "TEST":
                ctl.test()
            elif cmd == "SET":
                ctl.set_tension(params.get("MDL_N"))
            elif cmd == "MOVE":
                ctl.move(params["POS"])
            elif cmd == "CALI":
                ctl.calibrate(params["DIR"], params.get("TEN"))
    except KB.BeltError as exc:
        err = str(exc)
    wires = [f[1:-1] for f in ser.written]
    return {"wires": wires, "scripts": scripts, "pauses": pauses, "err": err,
            "ctl": ctl, "frames": list(ser.written), "infos": infos, "ser": ser}


# =====================================================================
# Tests
# =====================================================================

def test_frames():
    # Requests aus Live-Log 2026-10-12 („retries = 4, cmd = …“, ohne F7/CRC)
    live_wire = [
        ((0x21, 0x00, []), "21 03 00 00"),
        ((0x21, 0x02, [3]), "21 04 00 02 03"),
        ((0x21, 0x06, []), "21 03 00 06"),
        ((0x21, 0x08, [1, 0, 0, 0, 0x0b]), "21 08 00 08 01 00 00 00 0b"),
        ((0x21, 0x04, [3, 0, 0, 0, 0x93, 0xff, 0xf7, 0xb7, 0x95, 0xff, 0xf3, 0x51, 0x4c]),
         "21 10 00 04 03 00 00 00 93 ff f7 b7 95 ff f3 51 4c"),
        ((0x22, 0x00, []), "22 03 00 00"),
        ((0x22, 0x02, [3]), "22 04 00 02 03"),
        ((0x81, 0x05, [2]), "81 04 00 05 02"),
        ((0x82, 0x05, [1]), "82 04 00 05 01"),
    ]
    for (addr, fun, data), want in live_wire:
        check("wire %s" % want, KB.request_wire(addr, fun, data) == hexb(want))
        full = bytes(KB.build_frame(addr, fun, data))
        check("frame==serial485 %s" % want, full == S485.build_frame(addr, fun, data))
        check("frame-hülle %s" % want, full[0] == 0xF7 and full[1:-1] == hexb(want))
    # Live-Antworten mit CRC (Protokollanalyse + klippy.log)
    live_resp = [
        "f7 21 07 00 00 01 02 03 04 f0",
        "f7 21 10 00 02 03 00 00 00 93 ff f7 b7 95 ff f3 51 4c 3d",
        "f7 21 10 00 02 03 00 00 00 00 ff f7 b7 95 ff f3 51 4c 07",
        "f7 21 07 00 06 ff f8 c7 12 4c",
        "f7 21 07 00 06 ff f9 fc 24 cb",
        "f7 21 08 00 08 01 00 00 00 0b 84",
        "f7 21 10 00 04 03 00 00 00 93 ff f7 b7 95 ff f3 51 4c 6d",
        "f7 22 07 00 00 01 02 03 04 f0",
        "f7 22 10 00 02 03 00 00 00 00 ff f1 63 09 ff ed 12 bd 6b",
        "f7 22 07 00 06 ff f3 69 ed 9d",
    ]
    for s in live_resp:
        raw = hexb(s)
        rebuilt = S485.build_frame(raw[1], raw[4], list(raw[5:-1]))
        check("live-crc %s" % s[:20], rebuilt == raw, rebuilt.hex(" "))
        check("crc8 k2_belt %s" % s[:20], KB.crc8_cal(raw[2:-1]) == raw[-1])
    return True


def test_parse():
    fun, data = KB.parse_response(hexb("f7 21 07 00 00 01 02 03 04 f0"), 0x21)
    check("version fun", fun == 0 and data == bytes([1, 2, 3, 4]))
    fun, data = KB.parse_response(
        hexb("f7 21 10 00 02 03 00 00 00 93 ff f7 b7 95 ff f3 51 4c 3d"), 0x21)
    check("flash fun/len", fun == 2 and len(data) == 13 and data[0] == 3)
    check("flash place=147", KB.bytes_to_int(data[1:5]) == 147)
    check("flash idl/full", KB.bytes_to_int(data[5:9]) == LIVE_X["idl"]
          and KB.bytes_to_int(data[9:13]) == LIVE_X["full"])
    fun, data = KB.parse_response(hexb("f7 21 07 00 06 ff f9 fc 24 cb"), 0x21)
    check("adc live", fun == 6 and KB.bytes_to_int(data) == 4294573092)
    fun, data = KB.parse_response(hexb("f7 21 08 00 08 01 00 00 00 0b 84"), 0x21)
    check("move echo", data == bytes([1, 0, 0, 0, 0x0b]))
    # Fehler: falsche Adresse, CRC, Kopf, zu kurz
    for bad, why in (("f7 22 07 00 00 01 02 03 04 f0", "addr"),
                     ("f7 21 07 00 00 01 02 03 04 f1", "crc"),
                     ("f6 21 07 00 00 01 02 03 04 f0", "head"),
                     ("f7 21 07", "kurz")):
        try:
            KB.parse_response(hexb(bad), 0x21)
            check("parse-fehler %s" % why, False)
        except ValueError:
            check("parse-fehler %s" % why, True)
    # split/bytes_to_int == Original-Semantik
    for v in (0, 1, 147, 0x11223344, 0xFFFFFFFF, LIVE_X["idl"]):
        check("split/join %d" % v, KB.bytes_to_int(KB.split_to_bytes(v)) == v)
    check("split BE", KB.split_to_bytes(0x11223344) == [0x11, 0x22, 0x33, 0x44])
    return True


def test_live_math():
    s, i = KB.fit_line(LIVE_X["idl"], LIVE_X["full"], 140, 160)
    check("slope mdlx live", abs(s - LIVE_X["slope"]) <= 1e-9 * abs(LIVE_X["slope"]), repr(s))
    check("intercept mdlx live", abs(i - LIVE_X["intercept"]) < 1e-3, repr(i))
    live_pulls_x = [(4294573092, 129.69073523645056), (4294573184, 129.68435363768367),
                    (4294573058, 129.69309365341906), (4294499275, 134.8110665247077),
                    (4294494417, 135.14804268733133)]
    for adc, want in live_pulls_x:
        got = KB.adc_to_newton(adc, LIVE_X["idl"], 140, s)
        check("pull mdlx %d" % adc, abs(got - want) < 1e-6, "%r vs %r" % (got, want))
    got = KB.adc_to_newton(0xfff8c712, LIVE_X["idl"], 140, s)
    check("pull 135,18 N", abs(got - 135.18) < 0.005, repr(got))
    sy, iy = KB.fit_line(LIVE_Y["idl"], LIVE_Y["full"], 140, 160)
    check("slope mdly live", abs(sy - LIVE_Y["slope"]) <= 1e-9 * abs(LIVE_Y["slope"]))
    got = KB.adc_to_newton(4294142445, LIVE_Y["idl"], 140, sy)
    check("pull mdly live", abs(got - 130.60233463020995) < 1e-6, repr(got))
    # Live-Regelschritte (times:N, Kraft davor, gesendete Schrittweite)
    live_steps = [(1, 129.68435363768367, 11), (2, 129.69309365341906, 10),
                  (13, 134.8110665247077, 7), (14, 135.14804268733133, 6),
                  (18, 134.97705745865824, 7), (19, 135.00570528808748, 6),
                  (20, 135.45949245482916, 6), (21, 135.87110557733104, 5),
                  (22, 136.57294271473074, 3), (23, 136.9557692774688, 2),
                  (24, 136.92504049197305, 1), (25, 137.14007262536325, 4)]
    for n, cur, want in live_steps:
        got = KB.tension_step(n, 140, cur)
        check("live-schritt %d" % n, got == want, "%d vs %d" % (got, want))
    # times:26 bei 137,258 N: innerhalb 140·(1−0,02) → fertig (live keine Bewegung)
    check("live-ende 26", 140 * 0.98 < 137.25792410754366)
    return True


def test_vs_original_math(orig):
    if orig is None:
        check("original importierbar", False, "belt_mdl-live.py fehlt")
        return False
    sim = BeltSim()
    p = OPrinter(sim)
    o = orig.BELT_MDL(OConfig(p, "belt_mdl mdlx", {"ch_max_dis": 5, "ch_best_n": 140,
                                                   "ch_best_error": 0.15}))
    cal_sets = [(LIVE_X["idl"], LIVE_X["full"]), (LIVE_Y["idl"], LIVE_Y["full"]),
                (1000, 50000), (3000000000, 2900000000), (123456, 123457)]
    n = 0
    for idl, full in cal_sets:
        o.mdl.current_place = 0
        o.mdl.idl_adc, o.mdl.full_adc = idl, full
        o.init_adc_to_num()
        s, _i = KB.fit_line(idl, full, 140, 160)
        check("fit slope vs orig %d" % idl,
              abs(s - o.mdl.slope) <= 1e-9 * abs(o.mdl.slope))
        span = abs(full - idl)
        for frac in (-0.5, 0.0, 0.25, 0.5, 1.0, 2.0):
            adc = int(idl + frac * (full - idl))
            try:
                want = o.adc_to_num(adc)
            except OrigCmdError:
                continue
            got = KB.adc_to_newton(adc, idl, 140, s)
            n += 1
            check("kraft vs orig %d/%s" % (idl, frac), abs(got - want) < 1e-6,
                  "%r vs %r (span %d)" % (got, want, span))
    check("kraft-vergleiche > 20", n > 20, str(n))
    # Schrittweiten aller Zweige (>20/40/50/70/110) prüft Test 5 „timeout“:
    # 3 × 200 Regelschritte Port vs. Original Frame für Frame.
    return True


def _cmp_runs(tag, ro, rp, tens_tol=1e-6):
    check("%s wires" % tag, ro["wires"] == rp["wires"],
          "orig %d / port %d" % (len(ro["wires"]), len(rp["wires"])))
    check("%s gcode" % tag, ro["scripts"] == rp["scripts"],
          "%r vs %r" % (ro["scripts"][:4], rp["scripts"][:4]))
    check("%s pausen" % tag, len(ro["pauses"]) == len(rp["pauses"]) and all(
        abs(a - b) < 1e-12 for a, b in zip(ro["pauses"], rp["pauses"])))
    check("%s fehler-gleichlauf" % tag, (ro["err"] is None) == (rp["err"] is None),
          "%r / %r" % (ro["err"], rp["err"]))


def test_flows_vs_original(orig):
    if orig is None:
        return False
    steps = [("INFO", {}), ("TEST", {}), ("SET", {"MDL_N": 140}),
             ("INFO", {}), ("MOVE", {"POS": 170}), ("MOVE", {"POS": 150}),
             ("SIM", {"tension": 140.0}), ("CALI", {"DIR": 1, "TEN": 140}),
             ("SIM", {"tension": 160.0}), ("CALI", {"DIR": 2, "TEN": 160}),
             ("SIM", {"tension": 141.0}),
             ("CALI", {"DIR": 0}), ("INFO", {}), ("TEST", {})]
    so, sp = BeltSim(), BeltSim()
    ro = run_original(orig, so, steps)
    rp = run_port(sp, steps)
    _cmp_runs("ablauf mdlx", ro, rp)
    check("ablauf ohne fehler", ro["err"] is None and rp["err"] is None,
          "%r / %r" % (ro["err"], rp["err"]))
    o, c = ro["obj"], rp["ctl"]
    check("kraft gleich", abs(o.mdl.current_tension - c.current_tension) < 1e-6)
    check("position gleich", o.mdl.current_place == c.current_place)
    check("flash gleich", so.flash == sp.flash, "%r vs %r" % (so.flash, sp.flash))
    check("mech gleich", so.mech == sp.mech)
    n_moves = sum(1 for w in rp["wires"] if w[3] == 0x08)
    check("SET hat geregelt", n_moves > 10, str(n_moves))
    check("zyklus 2x im G-Code", rp["scripts"].count("M84") == 2
          and rp["scripts"][0] == "RESET_HOME_AXES_XY")
    check("test-ergebnis gleich", o.error_result == c.error_result)

    # mdly (0x22), Live-Kalibrierung Y
    sy_o = BeltSim(addr=0x22, flash=(0, LIVE_Y["idl"], LIVE_Y["full"]),
                   cal=(LIVE_Y["idl"], LIVE_Y["full"]), base=130.6)
    sy_p = BeltSim(addr=0x22, flash=(0, LIVE_Y["idl"], LIVE_Y["full"]),
                   cal=(LIVE_Y["idl"], LIVE_Y["full"]), base=130.6)
    st = [("INFO", {}), ("SET", {"MDL_N": 140}), ("TEST", {})]
    ro = run_original(orig, sy_o, st, name="mdly")
    rp = run_port(sy_p, st, name="mdly")
    _cmp_runs("ablauf mdly", ro, rp)
    check("mdly adresse 0x22", all(w[0] == 0x22 for w in rp["wires"]))

    # zu straff: kein Lockern (Original), nur Messung
    so, sp = BeltSim(base=150.0), BeltSim(base=150.0)
    st = [("INFO", {}), ("SET", {"MDL_N": 140})]
    ro, rp = run_original(orig, so, st), run_port(sp, st)
    _cmp_runs("zu straff", ro, rp)
    check("zu straff ohne fun 8", not any(w[3] == 0x08 for w in rp["wires"]))

    # Timeout: Riemen spannt kaum → >200 Schritte → key718 in beiden
    so, sp = BeltSim(k=0.0001), BeltSim(k=0.0001)
    st = [("INFO", {}), ("SET", {"MDL_N": 140})]
    ro, rp = run_original(orig, so, st), run_port(sp, st)
    _cmp_runs("timeout", ro, rp)
    check("timeout key718 orig", ro["err"] is not None and "key718" in ro["err"])
    check("timeout key718 port", rp["err"] is not None and "key718" in rp["err"], rp["err"])
    check("timeout kein flash-write", not any(w[3] == 0x04 for w in rp["wires"]))
    return True


def test_errors(orig):
    def ctl_for(sim, name="mdlx", **over):
        bus, ser = make_bus(sim)
        opts = {"allow_write": True, "allow_move": True}
        opts.update(over)
        return KB.K2BeltController(name, KB.parse_options(opts, name), bus=bus,
                                   pause=lambda s: None, respond=lambda m: None,
                                   run_gcode=lambda l: None), ser

    def expect(tag, fn, kind, key):
        try:
            fn()
            check(tag, False, "kein Fehler")
        except KB.BeltError as exc:
            check(tag, exc.kind == kind and exc.key == key and ("key%d" % key) in str(exc),
                  "%s/%s %s" % (exc.kind, exc.key, exc))

    c, _ = ctl_for(BeltSim(flash=(0, 0xFFFFFFFF, LIVE_X["full"])))
    expect("unkalibriert mdlx 714", c.info, "uncal", 714)
    c, _ = ctl_for(BeltSim(addr=0x22, flash=(0xFFFFFFFF, 1, 2)), name="mdly")
    expect("unkalibriert mdly 715", c.info, "uncal", 715)
    c, _ = ctl_for(BeltSim(flash=(0, 0, LIVE_X["full"])))
    expect("kalibrierwert 0 → 720", c.info, "cal_zero", 720)
    # 650 N: ADC noch im 24-Bit-Bereich → echte Plausibilitätsgrenze key716
    c, _ = ctl_for(BeltSim(base=650.0))
    expect("kraft > 600 → 716", c.info, "tension", 716)
    c, _ = ctl_for(BeltSim(addr=0x22), bus_timeout=0.01)  # niemand auf 0x21
    expect("keine antwort → 712", c.read_version, "comm", 712)
    c, _ = ctl_for(BeltSim(flash=(0, 5, 5)))
    expect("idl==full → 720", c.info, "cal_zero", 720)
    # Original zum Vergleich: gleiche Codes
    if orig is not None:
        for flash, key in (((0, 0xFFFFFFFF, 1), "key714"), ((0, 0, 1), "key720")):
            r = run_original(orig, BeltSim(flash=flash), [("INFO", {})])
            check("orig %s" % key, r["err"] is not None and key in r["err"], r["err"])
        r = run_original(orig, BeltSim(base=700.0), [("INFO", {})])
        check("orig key716", r["err"] is not None and "key716" in r["err"])
    # CALI-Schutz: Port liest den Flash VOR dem Schreiben; Original schreibt
    # ohne vorheriges INFO 0xFFFFFFFF über die Kalibrierung.
    sim = BeltSim(flash=(147, LIVE_X["idl"], LIVE_X["full"]))
    c, ser = make_bus(sim)
    ctl = KB.K2BeltController("mdlx", KB.parse_options({"allow_write": True}, "mdlx"),
                              bus=c, pause=lambda s: None, respond=lambda m: None)
    ctl.calibrate(0)
    check("cali0 port: flash zuerst gelesen", [w[1:-1][3] for w in ser.written] == [2, 4])
    check("cali0 port: kalibrierung erhalten",
          sim.flash == [0, LIVE_X["idl"], LIVE_X["full"]], repr(sim.flash))
    if orig is not None:
        so = BeltSim(flash=(147, LIVE_X["idl"], LIVE_X["full"]))
        run_original(orig, so, [("CALI", {"DIR": 0})])
        check("cali0 original zerstört kalibrierung (Beleg Abweichung)",
              so.flash == [0, 0xFFFFFFFF, 0xFFFFFFFF], repr(so.flash))
    # MOVE: Weg zu groß / negativ → Fehler, nichts gesendet
    sim = BeltSim()
    c, ser = ctl_for(sim)
    c.read_flash()
    n0 = len(ser.written)
    for pos in (0x10000, -1):
        try:
            c.move(pos)
            check("move %d abgelehnt" % pos, False)
        except KB.BeltError:
            check("move %d abgelehnt" % pos, True)
    check("move-fehler sendet nichts", len(ser.written) == n0)
    # Echo-Abweichung beim Flash-Schreiben → Fehler
    sim = BeltSim()
    c, ser = ctl_for(sim)
    c.read_flash()
    ser.extra[(0x21, 0x04)] = lambda d: bytes([3] + [0] * 12)
    try:
        c.write_flash()
        check("flash-echo-abweichung", False)
    except KB.BeltError as exc:
        check("flash-echo-abweichung", "weicht ab" in str(exc), str(exc))
    # Config-Fehler
    for bad in ({"addr": "0x30"}, {"ch_best_n": "400"}, {"cali_n1": "150", "cali_n2": "150"},
                {"cycle_gcode": "@FOO 1"}, {"cycle_gcode": "@MOTOR_CONTROL NUM=9 DATA=1"},
                {"allow_write": "vielleicht"}):
        try:
            KB.parse_options(bad, "mdlx")
            check("config-fehler %r" % bad, False)
        except ValueError:
            check("config-fehler %r" % bad, True)
    cfg = KB.parse_options({}, "mdly")
    check("config defaults", cfg["addr"] == 0x22 and not cfg["allow_write"]
          and not cfg["allow_move"] and cfg["ch_best_n"] == 140
          and cfg["ch_best_error"] == 0.05 and cfg["cycles"] == 2)
    check("config default-zyklus", cfg["cycle_steps"][0] == ("motor", (1, 2))
          and ("wait", 0.3) in cfg["cycle_steps"]
          and all(v != "RESET_HOME_AXES_XY" for _k, v in cfg["cycle_steps"]))
    return True


def test_guards():
    for opts, tag in (({}, "keine freigabe"), ({"allow_write": True}, "nur write"),
                      ({"allow_move": True}, "nur move"),
                      ({"allow_write": True, "allow_move": True, "_dry": 1}, "dry_run")):
        sim = BeltSim()
        bus, ser = make_bus(sim)
        dry = bool(opts.pop("_dry", 0))
        scripts, out = [], []
        ctl = KB.K2BeltController("mdlx", KB.parse_options(opts, "mdlx"), bus=bus,
                                  pause=lambda s: None, respond=out.append,
                                  run_gcode=scripts.append)
        r_move = ctl.move(30, dry_run=dry)
        r_set = ctl.set_tension(140, dry_run=dry)
        cali = [ctl.calibrate(d, None, dry_run=dry) for d in (0, 1, 2)]
        funs = [f[4] for f in ser.written]
        addrs = [f[1] for f in ser.written]
        check("%s: move blockiert" % tag, r_move["executed"] is False and r_move["plan"])
        check("%s: set blockiert" % tag, r_set["executed"] is False and len(r_set["plan"]) >= 3)
        wr_ok = opts.get("allow_write") and not dry
        check("%s: cali %s" % (tag, "ausgeführt" if wr_ok else "blockiert"),
              all(c["executed"] == bool(wr_ok) for c in cali))
        check("%s: kein fun 8, kein Motor, kein G-Code" % tag,
              0x08 not in funs and all(a in (0x21,) for a in addrs) and not scripts)
        if wr_ok:
            check("%s: nur cali schreibt" % tag, funs.count(0x04) == 3)
        else:
            check("%s: kein fun 4" % tag, 0x04 not in funs)
        check("%s: lesen erlaubt" % tag, 0x02 in funs and 0x06 in funs)
        check("%s: plan nennt frame" % tag, any("f7 21" in line for line in r_move["plan"]))
        check("%s: meldung nichts gesendet" % tag,
              any("NICHTS gesendet" in m for m in out))
        check("%s: mech unverändert" % tag, sim.mech == 0)
    # Interne Sperren greifen auch bei direktem Aufruf
    sim = BeltSim()
    bus, ser = make_bus(sim)
    ctl = KB.K2BeltController("mdlx", KB.parse_options({}, "mdlx"), bus=bus,
                              pause=lambda s: None, respond=lambda m: None)
    for fn in (lambda: ctl.slider_move(1, 5), ctl.write_flash, ctl.run_cycle,
               lambda: ctl.motor_control(1, 2)):
        try:
            fn()
            check("interne sperre", False)
        except KB.BeltError:
            check("interne sperre", True)
    check("interne sperren senden nichts", ser.written == [])
    return True


class RecBus:
    """Zeichnet transact_full-Parameter auf (Timeout/Retries-Prüfung)."""

    def __init__(self, sim):
        self.sim = sim
        self.calls = []

    def transact_full(self, addr, fun, data, timeout=None, retries=None):
        self.calls.append((addr, fun, tuple(data), timeout, retries))
        r = self.sim.handle(addr, fun, data)
        if r is None:
            raise TimeoutError("keine Antwort")
        raw = S485.build_frame(addr, fun, list(r))
        return addr, 0, fun, bytes(r), raw


def test_retries():
    # Parameter wie Original/Live
    sim = BeltSim()
    rb = RecBus(sim)
    ctl = KB.K2BeltController("mdlx", KB.parse_options(
        {"allow_write": True, "allow_move": True}, "mdlx"), bus=rb,
        pause=lambda s: None, respond=lambda m: None, run_gcode=lambda l: None)
    ctl.info()
    ctl.set_tension(140)
    belt = [c for c in rb.calls if c[0] == 0x21]
    mot = [c for c in rb.calls if c[0] in (0x81, 0x82)]
    check("belt timeout 1 s / retries 4", belt and all(c[3] == 1.0 and c[4] == 4 for c in belt))
    check("motor-0x05 timeout 0,05 s / retries 0", len(mot) == 8 and all(
        c[1] == 0x05 and c[3] == 0.05 and c[4] == 0 for c in mot))
    check("motor-0x05 daten 2,2,1,1 je zyklus",
          [c[2][0] for c in mot] == [2, 2, 1, 1, 2, 2, 1, 1]
          and [c[0] for c in mot][:4] == [0x81, 0x82, 0x81, 0x82])
    # Echte Retries über serial485: 4 verschluckte Antworten → Erfolg mit 5. Sendung
    sim = BeltSim()
    bus, ser = make_bus(sim, drop=4)
    ctl = KB.K2BeltController("mdlx", KB.parse_options({"bus_timeout": 0.02}, "mdlx"),
                              bus=bus, pause=lambda s: None, respond=lambda m: None)
    hal, soft = ctl.read_version()
    check("retry: 5. sendung erfolgreich", len(ser.written) == 5 and hal == b"\x01\x02"
          and soft == b"\x03\x04", str(len(ser.written)))
    # 5 verschluckt → Fehler nach genau 5 Sendungen, Text deutsch + key712
    sim = BeltSim()
    bus, ser = make_bus(sim, drop=5)
    ctl = KB.K2BeltController("mdlx", KB.parse_options({"bus_timeout": 0.02}, "mdlx"),
                              bus=bus, pause=lambda s: None, respond=lambda m: None)
    try:
        ctl.read_adc()
        check("retry: timeout nach 5", False)
    except KB.BeltError as exc:
        check("retry: timeout nach 5", len(ser.written) == 5 and exc.key == 712
              and "keine gültige Antwort" in str(exc), "%d %s" % (len(ser.written), exc))
    check("retry: alle 5 identisch", len(set(ser.written)) == 1)
    st = bus.stats()
    check("retry: serial485-statistik", st["sent"] == 5 and st["retries"] == 4
          and st["timeouts"] == 1, repr(st))
    return True


def test_default_cycle():
    sim = BeltSim()
    bus, ser = make_bus(sim)
    scripts = []

    def rg(line):
        scripts.append(line)
        if line == "M84":
            sim.shake()
    ctl = KB.K2BeltController("mdlx", KB.parse_options(
        {"allow_write": True, "allow_move": True}, "mdlx"), bus=bus,
        pause=lambda s: None, respond=lambda m: None, run_gcode=rg)
    ctl.info()
    ctl.set_tension(140)
    mot = [f for f in ser.written if f[1] in (0x81, 0x82)]
    check("default: 8 motor-frames", len(mot) == 8)
    check("default: motor-frame == live",
          mot[0][1:-1] == hexb("81 04 00 05 02") and mot[1][1:-1] == hexb("82 04 00 05 02")
          and mot[2][1:-1] == hexb("81 04 00 05 01"))
    check("default: motor-crc", all(S485.parse_frame(f) for f in mot))
    check("default: kein MOTOR_CONTROL/RESET als G-Code",
          not any(s.startswith(("MOTOR_CONTROL", "RESET_HOME")) for s in scripts))
    check("default: FORCE_MOVE + M84", scripts.count("M84") == 2 and sum(
        1 for s in scripts if s.startswith("FORCE_MOVE")) == 8)
    check("default: flash am ende", ser.written[-1][4] == 0x04)
    return True


# ---------------------------------------------------------------------
# Klipper-Hülle
# ---------------------------------------------------------------------

class FakeCommandError(Exception):
    pass


class FakeReactor:
    def __init__(self):
        self.waits = []
        self.t = 100.0

    def monotonic(self):
        return self.t

    def pause(self, waketime):
        self.waits.append(waketime - self.t)


def upstream_gcode_name_ok(cmd):
    """Nachbau der Namensprüfung aus Upstream gcode.py register_command
    (Buchstabe+Ziffer am Anfang ist ungültig, z. B. K2_…)."""
    try:
        c = cmd.upper().split()[0]
        float(c[1:])
        if c[0].isupper() and c[1].isdigit():
            return True
    except Exception:
        pass
    if (cmd.upper() != cmd or not cmd.replace('_', 'A').isalnum()
            or cmd[0].isdigit() or cmd[1:2].isdigit()):
        return False
    # Upstream-Parser (gcode.py _process_commands, args_r '([A-Z_]+|[A-Z*])')
    # zerlegt den Befehl: Ziffern im Namen kappen ihn (KTWO_485_X -> KTWO_485)
    # -> "Unknown command" trotz erfolgreicher Registrierung.
    import re as _re
    parts = _re.split('([A-Z_]+|[A-Z*])', cmd.upper())
    return ''.join(parts[:3]).strip() == cmd


class FakeGcode:
    def __init__(self):
        self.infos = []
        self.mux = {}
        self.descs = {}
        self.scripts = []

    def respond_info(self, msg, log=True):
        self.infos.append(str(msg))

    def register_mux_command(self, cmd, key, value, func, desc=None):
        if not upstream_gcode_name_ok(cmd):
            raise RuntimeError("Can't register '%s' as it is an invalid name" % cmd)
        prev = self.mux.setdefault(cmd, (key, {}))
        if prev[0] != key or value in prev[1]:
            raise ValueError("mux-Konflikt %s %s" % (cmd, value))
        prev[1][value] = func
        self.descs[cmd] = desc

    def run_script_from_command(self, script):
        self.scripts.append(script)

    def run(self, cmd, params):
        key, values = self.mux[cmd]
        return values[params[key]](FakeGcmd(params))


class FakeGcmd:
    error = FakeCommandError

    def __init__(self, params):
        self.params = dict(params)

    def get(self, name, default=None):
        return self.params.get(name, default)

    def get_int(self, name, default=None, minval=None, maxval=None):
        v = self.params.get(name, default)
        if v is None:
            return None
        v = int(v)
        if (minval is not None and v < minval) or (maxval is not None and v > maxval):
            raise FakeCommandError("Bereich %s" % name)
        return v

    def get_float(self, name, default=None, minval=None, maxval=None,
                  above=None, below=None):
        v = self.params.get(name, default)
        if v is None:
            return None
        v = float(v)
        if ((minval is not None and v < minval) or (maxval is not None and v > maxval)
                or (above is not None and v <= above)):
            raise FakeCommandError("Bereich %s" % name)
        return v


class FakeWebRequest:
    def __init__(self):
        self.sent = None

    def send(self, data):
        self.sent = data


class FakeWebhooks:
    def __init__(self):
        self.mux = {}

    def register_mux_endpoint(self, path, key, value, cb):
        self.mux.setdefault(path, {})[value] = cb


class FakePrinter:
    command_error = FakeCommandError

    def __init__(self, bus=None):
        self.reactor = FakeReactor()
        self.gcode = FakeGcode()
        self.webhooks = FakeWebhooks()
        self.objects = {"gcode": self.gcode, "webhooks": self.webhooks}
        if bus is not None:
            self.objects["k2_485"] = bus
        self.handlers = {}

    def lookup_object(self, name):
        if name not in self.objects:
            raise KeyError(name)
        return self.objects[name]

    def get_reactor(self):
        return self.reactor

    def register_event_handler(self, ev, cb):
        self.handlers.setdefault(ev, []).append(cb)

    def fire(self, ev):
        for cb in self.handlers.get(ev, []):
            cb()


class FakeConfig:
    def __init__(self, printer, name, opts):
        self.printer = printer
        self.name = name
        self.opts = dict(opts)

    def get_printer(self):
        return self.printer

    def get_name(self):
        return self.name

    def get(self, key, default=None):
        return self.opts.get(key, default)

    def error(self, msg):
        return ValueError(msg)


def test_klipper_shell():
    sx = BeltSim()
    sy = BeltSim(addr=0x22, flash=(0, LIVE_Y["idl"], LIVE_Y["full"]),
                 cal=(LIVE_Y["idl"], LIVE_Y["full"]), base=130.6)
    bus, ser = make_bus([sx, sy])
    pr = FakePrinter(bus)
    bx = KB.load_config_prefix(FakeConfig(pr, "k2_belt mdlx", {
        "ch_best_n": "140", "ch_best_error": "0.15", "stock_aliases": "True"}))
    by = KB.load_config_prefix(FakeConfig(pr, "k2_belt mdly", {
        "ch_best_n": "140", "ch_best_error": "0.15", "stock_aliases": "True",
        "allow_write": "True", "allow_move": "True"}))
    g = pr.gcode
    for cmd in ("KTWO_BELT_INFO", "KTWO_BELT_TEST", "KTWO_BELT_MOVE", "KTWO_BELT_SET",
                "KTWO_BELT_CALI", "BELT_MDL_INFO", "BELT_MDL_SET", "BELT_MDL_CALI"):
        check("mux %s beide" % cmd, cmd in g.mux and g.mux[cmd][0] == "MDL_NAME"
              and set(g.mux[cmd][1]) == {"mdlx", "mdly"})
    check("webhooks", set(pr.webhooks.mux.get("k2_belt_test", {})) == {"mdlx", "mdly"}
          and "belt_mdl_test" in pr.webhooks.mux)
    pr.fire("klippy:connect")
    check("bus 'k2_485' verdrahtet", bx.controller.bus is bus and by.controller.bus is bus)
    g.run("KTWO_BELT_INFO", {"MDL_NAME": "mdlx"})
    check("INFO meldet kraft", any("Riemenkraft" in m for m in g.infos)
          and g.infos[-1] == "MDL_NAME: mdlx")
    g.infos.clear()
    g.run("BELT_MDL_TEST", {"MDL_NAME": "mdly"})
    check("TEST alias mdly", any(m.startswith("get_mdl_data: {'current_tension'")
                                 for m in g.infos))
    wr = FakeWebRequest()
    pr.webhooks.mux["k2_belt_test"]["mdlx"](wr)
    check("webhook ergebnis", isinstance(wr.sent, dict)
          and set(wr.sent) == {"current_tension", "target_error"}
          and wr.sent["target_error"] in (0, 1))
    # mdlx ohne Freigabe: SET → Plan, nichts gesendet, kein Fehler
    n0 = len(ser.written)
    g.infos.clear()
    g.run("KTWO_BELT_SET", {"MDL_NAME": "mdlx", "MDL_N": "140"})
    check("SET ohne freigabe: plan", any("NICHTS gesendet" in m for m in g.infos))
    check("SET ohne freigabe: nur lesen", all(f[4] in (0x02, 0x06)
                                               for f in ser.written[n0:]))
    # mdly mit Freigabe (Stock-Makro-Weg über Aliase)
    g.run("BELT_MDL_INFO", {"MDL_NAME": "mdly"})
    g.run("BELT_MDL_SET", {"MDL_NAME": "mdly", "MDL_N": "140"})
    check("SET mdly ausgeführt", sy.mech > 0 and sy.flash[0] == by.controller.current_place)
    check("SET mdly G-Codes über run_script_from_command",
          g.scripts.count("M84") == 2 and any(s.startswith("FORCE_MOVE") for s in g.scripts))
    check("pausen relativ (reactor.pause(now+s))", pr.reactor.waits
          and all(w >= 0 for w in pr.reactor.waits) and 0.3 in
          [round(w, 6) for w in pr.reactor.waits])
    st = by.get_status(0)
    check("get_status", st["tension"] == by.controller.current_tension
          and st["calibrated"] and st["addr"] == 0x22 and st["allow_move"])
    # Fehler als command_error (kein Shutdown), deutsch
    try:
        g.run("KTWO_BELT_MOVE", {"MDL_NAME": "mdlx"})
        check("MOVE ohne POS → command_error", False)
    except FakeCommandError as exc:
        check("MOVE ohne POS → command_error", "POS" in str(exc))
    try:
        g.run("KTWO_BELT_CALI", {"MDL_NAME": "mdly", "DIR": "7"})
        check("CALI DIR=7 → command_error", False)
    except FakeCommandError as exc:
        check("CALI DIR=7 → command_error", "DIR muss" in str(exc))
    sx.flash = [0, 0xFFFFFFFF, 0xFFFFFFFF]
    try:
        g.run("KTWO_BELT_INFO", {"MDL_NAME": "mdlx"})
        check("unkalibriert → command_error", False)
    except FakeCommandError as exc:
        check("unkalibriert → command_error", "key714" in str(exc) and "nicht kalibriert"
              in str(exc))
    try:
        g.run("KTWO_BELT_SET", {"MDL_NAME": "mdly", "MDL_N": "500"})
        check("MDL_N 500 → command_error", False)
    except FakeCommandError:
        check("MDL_N 500 → command_error", True)
    # DRY_RUN trotz Freigabe
    n0 = len(ser.written)
    g.run("KTWO_BELT_MOVE", {"MDL_NAME": "mdly", "DRY_RUN": "1",
                             "POS": str(by.controller.current_place - 10)})
    check("DRY_RUN sendet keine mechanik", all(f[4] in (0x02, 0x06)
                                                for f in ser.written[n0:]))
    # ohne Bus: Fehlermeldung statt Absturz
    pr2 = FakePrinter(None)
    b2 = KB.load_config_prefix(FakeConfig(pr2, "k2_belt mdlx", {}))
    pr2.fire("klippy:connect")
    check("ohne bus: connect-hinweis", any("nicht verdrahtet" in m for m in pr2.gcode.infos))
    try:
        pr2.gcode.run("KTWO_BELT_INFO", {"MDL_NAME": "mdlx"})
        check("ohne bus → command_error", False)
    except FakeCommandError as exc:
        check("ohne bus → command_error", "k2_485" in str(exc))
    check("ohne aliase keine BELT_MDL_*", "BELT_MDL_INFO" not in pr2.gcode.mux
          and b2.get_status(0)["tension"] == 0.0)
    try:
        KB.load_config_prefix(FakeConfig(FakePrinter(bus), "k2_belt mdlx", {"addr": "0x40"}))
        check("config-fehler → config.error", False)
    except ValueError as exc:
        check("config-fehler → config.error", "Config-Fehler" in str(exc))
    return True


def test_shared_bus():
    """Belt (0x21) + Box-Poll (0x01 fun 0x0a) parallel über EINE Instanz."""
    sim = BeltSim()
    bus, ser = make_bus(sim)
    ser.extra[(0x01, 0x0a)] = lambda d: bytes([0x12, 0x34, 0, 4, 1, 0])
    ctl = KB.K2BeltController("mdlx", KB.parse_options({}, "mdlx"), bus=bus,
                              pause=lambda s: None, respond=lambda m: None)
    ctl.read_flash()
    ctl.init_fit()
    errs, box_ok = [], [0]

    def belt_worker():
        try:
            for _ in range(40):
                ctl.measure()
        except Exception as exc:  # pragma: no cover
            errs.append(exc)

    def box_worker():
        try:
            for _ in range(40):
                d = bus.transact(0x01, 0x0a, [], timeout=0.5, retries=0)
                if d == bytes([0x12, 0x34, 0, 4, 1, 0]):
                    box_ok[0] += 1
        except Exception as exc:  # pragma: no cover
            errs.append(exc)
    ts = [threading.Thread(target=belt_worker), threading.Thread(target=box_worker)]
    for t in ts:
        t.start()
    for t in ts:
        t.join(30)
    check("geteilter bus: keine fehler", not errs, repr(errs[:2]))
    check("geteilter bus: box-antworten korrekt", box_ok[0] == 40, str(box_ok[0]))
    check("geteilter bus: belt-kraft plausibel", abs(ctl.current_tension - sim.tension()) < 0.01)
    check("geteilter bus: keine überlappung", ser.overlaps == 0, str(ser.overlaps))
    check("geteilter bus: 80 + 1 frames", len(ser.written) == 81, str(len(ser.written)))
    return True


def test_py39():
    for fn in ("k2_belt.py", "test_k2_belt.py"):
        path = os.path.join(_HERE, fn)
        try:
            with open(path, encoding="utf-8") as fh:
                ast.parse(fh.read(), feature_version=(3, 9))
            check("py3.9 %s" % fn, True)
        except SyntaxError as exc:
            check("py3.9 %s" % fn, False, str(exc))
    with open(os.path.join(_HERE, "k2_belt.py"), encoding="utf-8") as fh:
        src = fh.read()
    code_lines = [ln.strip() for ln in src.splitlines()]
    check("kein numpy-import", not any(re.match(r"(import|from)\s+numpy", ln)
                                       for ln in code_lines))
    return True


class EvBus:
    """Bus-Attrappe mit Ereignisprotokoll (Sendungen + Pausen in EINER
    Liste) und optionaler stats()-Funktion für den Busruhe-Test."""

    def __init__(self, sim, with_stats=False):
        self.sim = sim
        self.events = []
        self.st = {"sent": 0, "received": 0, "timeouts": 0, "crc_errors": 0}
        self.foreign_during_pause = 0   # so viele Pausen mit Fremdverkehr
        if not with_stats:
            self.stats = None

    def stats(self):  # noqa: F811 – per Instanz abschaltbar
        return dict(self.st)

    def pause(self, s):
        self.events.append(("pause", round(s, 9)))
        if self.foreign_during_pause > 0:
            self.foreign_during_pause -= 1
            self.st["sent"] += 1        # anderes Modul hat gesendet
            self.st["received"] += 1

    def transact_full(self, addr, fun, data, timeout=None, retries=None):
        self.events.append(("tx", fun, tuple(data)))
        self.st["sent"] += 1
        r = self.sim.handle(addr, fun, data)
        if r is None:
            self.st["timeouts"] += 1
            raise TimeoutError("keine Antwort")
        self.st["received"] += 1
        return addr, 0, fun, bytes(r), S485.build_frame(addr, fun, list(r))


class StaleSim(BeltSim):
    """Belt mit 10-Hz-ADC-Verhalten: die ERSTE fun-6-Abfrage nach einer
    Fahrt liefert noch den Wert von VOR der Fahrt."""

    def __init__(self, *a, **kw):
        BeltSim.__init__(self, *a, **kw)
        self.stale = None

    def handle(self, addr, fun, data):
        if addr == self.addr and fun == 0x08:
            self.stale = self.adc()
        if addr == self.addr and fun == 0x06 and self.stale is not None:
            self.requests.append((addr, fun, bytes(data)))
            v, self.stale = self.stale, None
            return bytes(KB.split_to_bytes(v))
        return BeltSim.handle(self, addr, fun, data)


def test_befund_1018(orig):
    # --- 1. Vorzeichen / Byte-Layout -----------------------------------
    check("int32 ff f8 c7 12", KB.bytes_to_int32(hexb("ff f8 c7 12")) == -473326)
    check("int32 Flash idl X", KB.bytes_to_int32(hexb("ff f7 b7 95")) == -542827)
    check("int32 Flash full X", KB.bytes_to_int32(hexb("ff f3 51 4c")) == -831156)
    check("int32 Flash idl Y", KB.bytes_to_int32(hexb("ff f1 63 09")) == -957687)
    check("int32 Flash full Y", KB.bytes_to_int32(hexb("ff ed 12 bd")) == -1240387)
    check("int32 positiv", KB.bytes_to_int32(hexb("00 0f 42 40")) == 1000000)
    check("int24 ok ±", KB.is_int24(-(1 << 23)) and KB.is_int24((1 << 23) - 1))
    for raw, why in ((0x00800000, "Bit23=1, Oberbyte 00"),
                     (0xFF7FFFFF, "Bit23=0, Oberbyte FF"),
                     (0x7F000000, "Oberbyte 7F")):
        check("kein int24: %s" % why, not KB.is_int24(KB.to_int32(raw)))
    check("split signed == unsigned bytes",
          KB.split_to_bytes(-542827) == KB.split_to_bytes(LIVE_X["idl"]))

    # --- 2. Nachrechnung beider Live-Achsen (signed) --------------------
    for tag, live, idl_s, full_s, cpn, n0 in (
            ("X", LIVE_X, -542827, -831156, -14416.45, 102.35),
            ("Y", LIVE_Y, -957687, -1240387, -14135.0, 72.25)):
        check("Achse %s idl/full signed" % tag,
              KB.to_int32(live["idl"]) == idl_s and KB.to_int32(live["full"]) == full_s)
        s_, i_ = KB.fit_line(idl_s, full_s, 140, 160)
        check("Achse %s Steigung == Live" % tag,
              abs(s_ - live["slope"]) <= 1e-9 * abs(live["slope"]), repr(s_))
        check("Achse %s Zählimpulse/N %.2f" % (tag, cpn), abs(1.0 / s_ - cpn) < 0.01,
              repr(1.0 / s_))
        check("Achse %s ADC=0 ≙ %.2f N" % (tag, n0), abs(i_ - n0) < 0.01, repr(i_))
        # Überall, wo live gemessen wurde (alle ADC negativ), gleiche Kraft wie Stock
        for frac in (0.0, 0.5, 1.0, 1.5):
            adc_u = int(live["idl"] + frac * (live["full"] - live["idl"]))
            a = KB.adc_to_newton(adc_u, live["idl"], 140, live["slope"])
            b = KB.adc_to_newton(KB.to_int32(adc_u), idl_s, 140, s_)
            check("Achse %s signed == stock bei %.1f" % (tag, frac), abs(a - b) < 1e-6)
        # lockerer Riemen: ADC positiv → Stock-Phantomkraft, signed korrekt
        adc_pos = 1000000
        stock = adc_pos * live["slope"] + live["intercept"]
        port = KB.adc_to_newton(adc_pos, idl_s, 140, s_)
        check("Achse %s Stock-Phantomkraft ~3e5 N" % tag, stock > 2.9e5, repr(stock))
        check("Achse %s signed: %.1f N (klein)" % (tag, port), -50 < port < n0, repr(port))
    got = KB.adc_to_newton(KB.to_int32(0xfff8c712), -542827, 140,
                           KB.fit_line(-542827, -831156, 140, 160)[0])
    check("signed: 135,18 N", abs(got - 135.18) < 0.005, repr(got))

    def ctl_for(sim, name="mdlx", **over):
        bus, ser = make_bus(sim)
        opts = {"allow_write": True, "allow_move": True}
        opts.update(over)
        out = []
        return KB.K2BeltController(name, KB.parse_options(opts, name), bus=bus,
                                   pause=lambda s: None, respond=out.append,
                                   run_gcode=lambda l: None), ser, out

    # lockerer Riemen X (30 N) / Y (50 N) / entlastet (-20 N): Port misst,
    # Original löst key716/717 aus
    for name, addr, live, base in (("mdlx", 0x21, LIVE_X, 30.0),
                                   ("mdly", 0x22, LIVE_Y, 45.0),
                                   ("mdlx", 0x21, LIVE_X, -20.0)):
        mk = (lambda: BeltSim(addr=addr, flash=(0, live["idl"], live["full"]),
                              cal=(live["idl"], live["full"]), base=base))
        sim = mk()
        c, ser, out = ctl_for(sim, name)
        try:
            c.info()
            check("locker %s %.0f N: Port misst" % (name, base),
                  abs(c.current_tension - base) < 0.01 and c.current_adc > 0,
                  "%r %r" % (c.current_tension, c.current_adc))
            check("locker %s %.0f N: Hinweis" % (name, base),
                  any("Hinweis" in m for m in out), repr(out[-2:]))
        except KB.BeltError as exc:
            check("locker %s %.0f N: Port misst" % (name, base), False, str(exc))
        if orig is not None:
            r = run_original(orig, mk(), [("INFO", {})], name=name)
            key = "key716" if addr == 0x21 else "key717"
            check("locker %s %.0f N: Original %s (Phantomkraft)" % (name, base, key),
                  r["err"] is not None and key in r["err"]
                  and r["obj"].mdl.current_tension > 2.9e5,
                  "%r %r" % (r["err"], r["obj"].mdl.current_tension))
    # SET aus lockerem Zustand: Port spannt bis ins Ziel (Original bricht ab)
    sim = BeltSim(base=60.0, k=0.5, settle=0.0)
    c, ser, out = ctl_for(sim)
    c.info()
    res = c.set_tension(140)
    check("SET aus 60 N: Ziel erreicht", res["executed"] and
          140 * 0.98 <= c.current_tension <= 140 * 1.02 + 6, repr(c.current_tension))
    check("SET: alle fun 8 <= 50", all(KB.bytes_to_int(f[6:10]) <= 50
                                       for f in ser.written if f[4] == 0x08))
    # ADC-Layout-Fehler / Vollausschlag → klare Meldung, kein key716
    for raw, txt in (("00 80 00 00", "kein 24-Bit-Wert"), ("00 7f ff ff", "Anschlag"),
                     ("ff 80 00 00", "Anschlag")):
        sim = BeltSim()
        c, ser, out = ctl_for(sim)
        ser.extra[(0x21, 0x06)] = lambda d, raw=raw: hexb(raw)
        try:
            c.read_adc()
            check("ADC %s → Fehler" % raw, False)
        except KB.BeltError as exc:
            check("ADC %s → Fehler" % raw, txt in str(exc) and exc.key is None, str(exc))
    # Kalibrierwert außerhalb 24 Bit → cal_zero/720 mit Klartext
    c, _ser, _o = ctl_for(BeltSim(flash=(0, 0x7F000000, LIVE_X["full"])))
    try:
        c.info()
        check("Flash außerhalb 24 Bit → 720", False)
    except KB.BeltError as exc:
        check("Flash außerhalb 24 Bit → 720", exc.key == 720 and "24-Bit" in str(exc),
              str(exc))
    # Flash-Write schreibt signed Werte bitgleich zum Stock
    sim = BeltSim(flash=(147, LIVE_X["idl"], LIVE_X["full"]))
    c, ser, _o = ctl_for(sim)
    c.read_flash()
    check("read_flash signed", c.idl_adc == -542827 and c.full_adc == -831156)
    c.write_flash()
    check("write_flash bytes == Live", ser.written[-1][1:-1] == hexb(
        "21 10 00 04 03 00 00 00 93 ff f7 b7 95 ff f3 51 4c"))
    # CALI mit ADC -1 würde den Sentinel speichern → abgelehnt
    sim = BeltSim()
    c, ser, _o = ctl_for(sim)
    ser.extra[(0x21, 0x06)] = lambda d: hexb("ff ff ff ff")
    try:
        c.calibrate(1)
        check("CALI ADC -1 abgelehnt", False)
    except KB.BeltError as exc:
        check("CALI ADC -1 abgelehnt", "0xFFFFFFFF" in str(exc)
              and not any(f[4] == 0x04 for f in ser.written))

    # --- 3. Messfrische nach fun 8 --------------------------------------
    check("FW-Zeit je Einheit 6,5536 ms", abs(KB.MOVE_TIME_PER_UNIT_FW - 0.0065536) < 1e-12)
    check("Wartezeit 2 Einh.: 0,1131 s", abs(KB.move_wait_time(2) - 0.1131072) < 1e-9)
    check("Wartezeit 50 Einh.: Stock 0,66 s", abs(KB.move_wait_time(50) - 0.66) < 1e-9)
    check("Wartezeit settle 0 == Stock", all(abs(KB.move_wait_time(d, 0) - d * 0.0132)
                                             < 1e-12 for d in (1, 12, 50, 0xFFFE)))
    sim = BeltSim()
    eb = EvBus(sim)
    c = KB.K2BeltController("mdlx", KB.parse_options(
        {"allow_write": True, "allow_move": True, "bus_gap": 0}, "mdlx"),
        bus=eb, pause=eb.pause, respond=lambda m: None)
    c.read_flash()
    c.init_fit()
    c.current_tension = 130.0
    del eb.events[:]
    c.slider_move(1, 2)
    c.measure_fresh()
    want = [("tx", 8, (1, 0, 0, 0, 2)), ("pause", round(0.0065536 * 2 + 0.1, 9)),
            ("tx", 6, ()), ("pause", 0.11), ("tx", 6, ())]
    check("Messfrische: fun8, Fahrt+100 ms, verwerfen, 110 ms, messen",
          eb.events == want, repr(eb.events))
    # Stale-ADC-Simulator: mit Verwerfen frischer Wert, Stock-Weg veraltet
    for opts, fresh in (({}, True), ({"measure_discard": 0, "move_settle": 0}, False)):
        sim = StaleSim(k=0.5)
        bus, _ser = make_bus(sim)
        o2 = {"allow_write": True, "allow_move": True}
        o2.update(opts)
        c = KB.K2BeltController("mdlx", KB.parse_options(o2, "mdlx"), bus=bus,
                                pause=lambda s: None, respond=lambda m: None)
        c.read_flash()
        c.init_fit()
        before = c.measure()
        c.slider_move(1, 10)
        after = c.measure_fresh()
        tag = "verwerfen" if fresh else "stock"
        check("stale-sim %s" % tag,
              (abs(after - sim.tension()) < 0.01) if fresh
              else (abs(after - before) < 0.01 and abs(after - sim.tension()) > 4),
              "%r %r %r" % (before, after, sim.tension()))
    for bad in ({"measure_discard": "9"}, {"move_settle": "-1"},
                {"measure_interval": "3"}):
        try:
            KB.parse_options(bad, "mdlx")
            check("config-fehler %r" % bad, False)
        except ValueError:
            check("config-fehler %r" % bad, True)
    cfg = KB.parse_options({}, "mdlx")
    check("defaults messfrische/busruhe/weg", cfg["move_settle"] == 0.1
          and cfg["measure_discard"] == 1 and cfg["measure_interval"] == 0.11
          and cfg["bus_gap"] == 0.005 and cfg["max_steps_units"] == 50)

    # --- 4. Busruhe -------------------------------------------------------
    sim = BeltSim()
    eb = EvBus(sim, with_stats=True)
    c = KB.K2BeltController("mdlx", KB.parse_options({}, "mdlx"), bus=eb,
                            pause=eb.pause, respond=lambda m: None)
    c.read_version()
    check("busruhe: 5 ms vor dem Request", eb.events == [("pause", 0.005), ("tx", 0, ())],
          repr(eb.events))
    del eb.events[:]
    eb.foreign_during_pause = 2
    c.read_adc()
    check("busruhe: Fremdverkehr → erneut warten",
          eb.events == [("pause", 0.005)] * 3 + [("tx", 6, ())], repr(eb.events))
    eb2 = EvBus(sim, with_stats=False)
    c = KB.K2BeltController("mdlx", KB.parse_options({}, "mdlx"), bus=eb2,
                            pause=eb2.pause, respond=lambda m: None)
    c.read_version()
    check("busruhe ohne stats(): eine Pause", eb2.events == [("pause", 0.005),
                                                             ("tx", 0, ())])
    eb3 = EvBus(sim, with_stats=True)
    eb3.foreign_during_pause = 1000
    c = KB.K2BeltController("mdlx", KB.parse_options({}, "mdlx"), bus=eb3,
                            pause=eb3.pause, respond=lambda m: None)
    c.read_version()
    check("busruhe: Obergrenze %d Versuche" % KB.BUS_GAP_MAX_TRIES,
          sum(1 for e in eb3.events if e[0] == "pause") == KB.BUS_GAP_MAX_TRIES
          and eb3.events[-1][0] == "tx")
    for bad in ({"bus_gap": "0.001"}, {"bus_gap": "0.5"}):
        try:
            KB.parse_options(bad, "mdlx")
            check("config-fehler %r" % bad, False)
        except ValueError:
            check("config-fehler %r" % bad, True)
    # Klipper-Hülle: Busruhe über reactor.pause (relativ)
    pr = FakePrinter(make_bus(BeltSim())[0])
    bx = KB.load_config_prefix(FakeConfig(pr, "k2_belt mdlx", {}))
    pr.fire("klippy:connect")
    pr.gcode.run("KTWO_BELT_INFO", {"MDL_NAME": "mdlx"})
    check("hülle: 3 Requests → 3× 5 ms", [round(w, 6) for w in pr.reactor.waits]
          == [0.005] * 3, repr(pr.reactor.waits))

    # --- 5. Sendeschutz k2_belt ------------------------------------------
    sim = BeltSim()
    c, ser, _o = ctl_for(sim)
    for fun, data, why in ((0x02, [4], "fun 2 [04]"), (0x02, [], "fun 2 leer"),
                           (0x02, [3, 0], "fun 2 [03 00]"),
                           (0x04, [0], "fun 4 [00] (löscht)"),
                           (0x04, [2] + [0] * 8, "fun 4 n=2"),
                           (0x06, [1], "fun 6 mit Daten"),
                           (0x08, [1, 0, 0, 0, 0], "fun 8 dist 0"),
                           (0x08, [1, 0, 0, 0, 51], "fun 8 dist 51"),
                           (0x08, [1, 0, 0, 0xFF, 0xFF], "fun 8 dist 0xFFFF"),
                           (0x08, [2, 0, 0, 0, 1], "fun 8 dir 2"),
                           (0x0A, [], "fun 0x0A"), (0xF0, [], "fun 0xF0")):
        n0 = len(ser.written)
        try:
            c._xfer(fun, data)
            check("sendeschutz %s" % why, False)
        except KB.BeltError as exc:
            check("sendeschutz %s" % why, "Sendeschutz k2_belt" in str(exc)
                  and len(ser.written) == n0, str(exc))
    c2, ser2, _o = ctl_for(BeltSim(), allow_write=False, allow_move=False)
    for fun, data in ((0x04, [3] + [0] * 12), (0x08, [1, 0, 0, 0, 1])):
        try:
            c2._xfer(fun, data)
            check("sendeschutz ohne freigabe fun %d" % fun, False)
        except KB.BeltError:
            check("sendeschutz ohne freigabe fun %d" % fun, ser2.written == [])
    c3, ser3, _o = ctl_for(BeltSim())
    c3.read_flash()
    check("fun 2 sendet genau [03]", ser3.written[-1][1:-1] == hexb("21 04 00 02 03"))
    for d in (0, 51):
        try:
            c3.slider_move(1, d)
            check("slider_move %d abgelehnt" % d, False)
        except KB.BeltError:
            check("slider_move %d abgelehnt" % d, not any(f[4] == 8 for f in ser3.written))
    c3.slider_move(1, 50)
    check("slider_move 50 erlaubt", ser3.written[-1][4] == 8)
    n0 = len(ser3.written)
    try:
        c3.move(c3.current_place + 51)
        check("MOVE 51 > max_steps_units abgelehnt", False)
    except KB.BeltError as exc:
        check("MOVE 51 > max_steps_units abgelehnt", "max_steps_units" in str(exc)
              and len(ser3.written) == n0)
    r = c3.move(c3.current_place)
    check("MOVE Weg 0: nichts gesendet", r["executed"] is False
          and len(ser3.written) == n0)
    # Obergrenze der Option = Sendeschutz TX_BELT_MOVE_MAX (50), 18.10.2026
    for v, ok in (("50", True), ("51", False), ("0xFFFE", False), ("0", False),
                  ("12", True)):
        try:
            cfg = KB.parse_options({"max_steps_units": v}, "mdlx")
            check("max_steps_units %s" % v, ok)
        except ValueError:
            check("max_steps_units %s" % v, not ok)
    c4 = KB.K2BeltController("mdlx", KB.parse_options(
        {"allow_move": True, "max_steps_units": "50"}, "mdlx"), bus=RecBus(BeltSim()),
        pause=lambda s: None, respond=lambda m: None)
    for d in (51, 0xFFFF):
        try:
            c4.slider_move(1, d)
            check("Grenze 50 (%d abgelehnt)" % d, False)
        except KB.BeltError:
            check("Grenze 50 (%d abgelehnt)" % d, True)
    c4.slider_move(1, 50)
    check("50 bei max_steps_units=50 erlaubt",
          c4.bus.calls[-1][1] == 8 and c4.bus.calls[-1][2][1:] == (0, 0, 0, 50))
    check("get_status max_steps_units", c4.get_status()["max_steps_units"] == 50)
    return True


def main():
    # numpy-RankWarning des Originals bei absichtlich schlechten Fits stummschalten
    warnings.simplefilter("ignore")
    orig, note = load_original()
    if orig is None:
        print("WARNUNG: %s" % note)
    else:
        print("Original geladen: %s%s" % (os.path.basename(ORIG_PATH), note))
    tests = [
        ("1 Frame-Bau (Live-Wire/CRC)", test_frames),
        ("2 Parsing Live-Antworten", test_parse),
        ("3 Rechenwege vs. Live", test_live_math),
        ("4 Rechenwege vs. Original", lambda: test_vs_original_math(orig)),
        ("5 Abläufe Port == Original", lambda: test_flows_vs_original(orig)),
        ("6 Fehlerfälle", lambda: test_errors(orig)),
        ("7 Freigabe-Sperren", test_guards),
        ("8 Retries (serial485)", test_retries),
        ("9 Default-Zyklus Motor-0x05", test_default_cycle),
        ("10 Klipper-Hülle", test_klipper_shell),
        ("11 Geteilter Bus", test_shared_bus),
        ("12 Python 3.9", test_py39),
        ("13 Belt-FW-Befund 18.10.", lambda: test_befund_1018(orig)),
    ]
    test_ok = 0
    for name, fn in tests:
        before = len(RESULTS)
        try:
            fn()
            exc = None
        except Exception as e:  # Testfehler sichtbar machen
            import traceback
            traceback.print_exc()
            exc = e
            check("%s: Ausnahme" % name, False, repr(e))
        mine = RESULTS[before:]
        ok = exc is None and all(r[1] for r in mine) and len(mine) > 0
        test_ok += ok
        print("%s %s (%d Prüfungen)" % ("ok    " if ok else "FEHLER", name, len(mine)))
    n_ok = sum(1 for r in RESULTS if r[1])
    all_ok = test_ok == len(tests) and n_ok == len(RESULTS)
    print("ERGEBNIS: %s (%d/%d Tests, %d/%d Prüfungen ok)"
          % ("ok" if all_ok else "FEHLER", test_ok, len(tests), n_ok, len(RESULTS)))
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
