#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_k2_zalign.py – Offline-Tests für k2_zalign (ohne Klipper-Lauf, ohne
Hardware). A10, 17.10.2026.

Aufruf: ``cd pi-host && python3 test_k2_zalign.py``  (Python >= 3.9)

Echte Upstream-Teile (Commit 9cb9975, ``lokal/upstream-klipper``
oder ``$KLIPPER_SRC``): ``configfile.ConfigWrapper`` (Optionsgrenzen),
``gcode.GCodeDispatch.register_command`` + ``args_r`` (Namensregel),
``gcode.GCodeCommand`` (Parameter). Bett/Lichtschranken/Homing = Simulation.

Prüft:
 1. Konfig: Defaults, harte Grenzen (Stock-Obergrenzen), Kreuzregeln,
    Pflicht-Pins, zalign.cfg Block B (einkommentiert) gültig + gesperrt,
    Pins Block L == Block B.
 2. Ablauf (ZAlignCore) gegen Simulation: Normalfall, Startverkippung,
    safe_dist bei defekter Schranke / zu großer Verkippung (harte Invariante:
    Wegdifferenz nach erstem Auslösen nie > safe_dist), beide defekt ->
    max_travel, Start ausgelöst ohne/mit LIFT, Polarität falsch, Prellen,
    instabiles Signal, Schranke klemmt nach Freifahren, Retries, Timeout,
    zmax_position, Geschwindigkeiten/Etappenlängen, finish-Verhalten.
 3. Klipper-Anbindung (PrinterK2ZAlign) mit Fakes: Sperren (allow_move,
    Freigabe-Makro, Motor-Init, Druck aktiv) ohne Bewegung, DRY_RUN,
    STATUS, QUERY_ENDSTOPS-Registrierung, Stepper-Zuordnung, manual_home-
    Parameter, Weg aus get_mcu_position, LIFT/finish/clear_homing_state,
    z_tilt-Reset.
 4. G-Code-Namen: echte Upstream-Prüfung + Parser-Zerlegung.
 5. Python-3.9-Syntax, keine UUID.
"""

import ast
import configparser
import importlib.util
import logging
import math
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_ks = os.environ.get("KLIPPER_SRC", "")
if _ks and os.path.isdir(os.path.join(_ks, "klippy")):
    _ks = os.path.join(_ks, "klippy")
UPSTREAM_KLIPPY = os.path.normpath(_ks or os.path.join(
    _REPO, "lokal", "upstream-klipper", "klippy"))
MODULE_PATH = os.path.join(_HERE, "k2_zalign.py")
ZALIGN_CFG = os.path.join(_HERE, "config-pi", "zalign.cfg")
if not os.path.isfile(os.path.join(UPSTREAM_KLIPPY, "configfile.py")):
    print("SKIP (k2_zalign) Upstream-Klipper nicht gefunden: %s – KLIPPER_SRC "
          "setzen" % UPSTREAM_KLIPPY)
    sys.exit(0)
if UPSTREAM_KLIPPY not in sys.path:
    sys.path.append(UPSTREAM_KLIPPY)
import configfile as ucfg   # noqa: E402  (Upstream)
import gcode as ugcode      # noqa: E402  (Upstream)

spec = importlib.util.spec_from_file_location("k2_zalign", MODULE_PATH)
zm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(zm)

RESULTS = []
STEP = 0.0025  # mm je Vollschritt (rotation_distance 8, 16 Mikroschritte)


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("  [%s] %s%s" % ("ok" if ok else "FEHLER", name,
                           "" if ok else "  -> %r" % (detail,)))


def raises(fn, exc=Exception, text=None):
    try:
        fn()
    except exc as e:
        return text is None or text in str(e)
    return False


# ---------------------------------------------------------------------------
# Simulation Bett + Lichtschranken
# ---------------------------------------------------------------------------

class SimBed:
    """gap[n] = Abstand (mm) der Seite n über ihrer Schranke. Schranke
    ausgelöst bei gap <= ausloese[n] (Default 0). defekt: löst nie aus;
    klemmt: immer ausgelöst; prellen: Seite stoppt einmal bei gap <= x,
    meldet aber frei; flackern: nach Auslösen in der nächsten Abfrage frei."""

    def __init__(self, gap_z, gap_z1, defekt=(), klemmt=(), prellen=None,
                 flackern=None, offsets=None, clock=None):
        self.gap = {"z": float(gap_z), "z1": float(gap_z1)}
        self.defekt, self.klemmt = set(defekt), set(klemmt)
        self.prellen = dict(prellen or {})
        self.flackern = flackern
        self._flack_done = False
        self.offsets = list(offsets or [])    # je Abwärtsfahrt (z, z1)
        self.cur_off = {"z": 0.0, "z1": 0.0}
        self.calls = []
        self.clock = clock
        self.first_trig = None                 # (gap_z, gap_z1) beim 1. Trigger
        self.max_tilt_after = 0.0
        self.pending_bounce = set()

    def _trig(self, n):
        if n in self.klemmt:
            return True
        if n in self.defekt:
            return False
        return self.gap[n] <= self.cur_off[n] + 1e-9

    def query(self):
        self.calls.append(("query",))
        st = {n: self._trig(n) for n in zm.NAMES}
        for n in list(self.pending_bounce):
            st[n] = False
            self.pending_bounce.discard(n)
        if self.flackern and st.get(self.flackern) and not self._flack_done \
                and any(c[0] == "descend" for c in self.calls[-3:]) \
                and self._seen_trig.get(self.flackern):
            self._flack_done = True
            st[self.flackern] = False
        self._seen_trig = getattr(self, "_seen_trig", {})
        for n in zm.NAMES:
            if st[n]:
                self._seen_trig[n] = True
        return st

    def prepare(self):
        self.calls.append(("prepare",))

    def descend(self, dist, speed):
        self.calls.append(("descend", dist, speed))
        if self.clock is not None:
            self.clock.t += dist / speed + 0.2
        moved = {}
        trig_before = {n: self._trig(n) for n in zm.NAMES}
        for n in zm.NAMES:
            if trig_before[n]:
                moved[n] = 0.0
                continue
            limit = dist
            if n in self.prellen and self.gap[n] - dist <= self.prellen[n]:
                limit = max(0.0, self.gap[n] - self.prellen[n])
                del self.prellen[n]
                self.pending_bounce.add(n)
            elif n not in self.defekt:
                limit = min(dist, max(0.0, self.gap[n] - self.cur_off[n]))
            # Schritt-Quantisierung wie MCU-Position
            m = round(limit / STEP) * STEP
            self.gap[n] -= m
            moved[n] = m
        # Invariante: Wegdifferenz nach erstem Auslösen
        tr = {n: self._trig(n) for n in zm.NAMES}
        if self.first_trig is None and any(tr.values()):
            # Zeitpunkt des ersten Auslösens innerhalb der Etappe: die
            # ausgelöste Seite fuhr moved[x], die andere bis dahin dasselbe
            x = [n for n in zm.NAMES if tr[n]][0]
            y = [n for n in zm.NAMES if n != x][0]
            self.first_trig = True
            self.max_tilt_after = abs(moved[y] - moved[x])
        elif self.first_trig is not None:
            self.max_tilt_after += abs(moved["z"] - moved["z1"])
        return moved

    def lift(self, dist, speed):
        self.calls.append(("lift", dist, speed))
        if self.clock is not None:
            self.clock.t += dist / speed + 0.2
        for n in zm.NAMES:
            self.gap[n] += dist
        # neue Abwärtsfahrt -> nächste Auslöse-Abweichung
        if self.offsets:
            oz, oz1 = self.offsets.pop(0)
            self.cur_off = {"z": oz, "z1": oz1}
        self.first_trig = None

    def finish(self, zpos):
        self.calls.append(("finish", zpos))


class Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def cfg_with(**kw):
    c = dict(zm.DEFAULTS)
    c.update(kw)
    zm.validate_cfg(c)
    return c


def run_core(bed, lift_ok=False, **kw):
    clock = bed.clock or Clock()
    bed.clock = clock
    core = zm.ZAlignCore(cfg_with(**kw), bed, clock)
    try:
        res = core.run(lift_ok=lift_ok)
        return res, None, core
    except zm.ZAlignError as e:
        return core.last_result, str(e), core


def kinds(bed):
    return [c[0] for c in bed.calls if c[0] != "query"]


# ---------------------------------------------------------------------------
# 1. Konfiguration
# ---------------------------------------------------------------------------

def make_config(text, section="k2_zalign", printer=None):
    fc = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(
        ";", "#"))
    fc.read_string(text)
    return ucfg.ConfigWrapper(printer or FakePrinter(), fc, {}, section)


def test_config():
    c = dict(zm.DEFAULTS)
    check("1 Defaults gültig", zm.validate_cfg(dict(c)) is not None)
    for key, bad in (("quick_speed", 30.5), ("slow_speed", 10.5),
                     ("safe_dist", 40.5), ("rising_dist", 20.5),
                     ("max_travel", 401), ("timeout", 301), ("retries", 6),
                     ("quick_speed", 0.0), ("chunk_dist", 0.0)):
        d = dict(c)
        d[key] = bad
        check("1 Grenze %s=%r -> Fehler" % (key, bad),
              raises(lambda: zm.validate_cfg(d), ValueError))
    d = dict(c, chunk_dist=12.0, safe_dist=10.0)
    check("1 chunk_dist > safe_dist -> Fehler",
          raises(lambda: zm.validate_cfg(d), ValueError, "chunk_dist"))
    d = dict(c, slow_speed=10.0, quick_speed=5.0)
    check("1 slow_speed > quick_speed -> Fehler",
          raises(lambda: zm.validate_cfg(d), ValueError, "slow_speed"))
    # Upstream-ConfigWrapper: Stock-Obergrenzen als maxval
    base = "[k2_zalign]\npin_z: ^!PA15\npin_z1: ^!PA8\n"
    for opt, val in (("quick_speed", "31"), ("slow_speed", "11"),
                     ("safe_dist", "41"), ("retries", "6"),
                     ("chunk_dist", "20")):
        cfgw = make_config(base + "%s: %s\n" % (opt, val))
        check("1 Upstream-Config %s=%s -> config error" % (opt, val),
              raises(lambda: zm.PrinterK2ZAlign(cfgw), configparser.Error))
    cfgw = make_config("[k2_zalign]\npin_z1: ^!PA8\n")
    check("1 pin_z fehlt -> config error",
          raises(lambda: zm.PrinterK2ZAlign(cfgw), configparser.Error,
                 "pin_z"))
    p = FakePrinter()
    obj = zm.PrinterK2ZAlign(make_config(base, printer=p))
    check("1 Default allow_move False", obj.cfg["allow_move"] is False)
    check("1 Endstops mit Pull-up+Invertierung angelegt",
          p.pins.setups == [("endstop", "^!PA15"), ("endstop", "^!PA8")],
          p.pins.setups)
    check("1 QUERY_ENDSTOPS-Registrierung zalign_z/zalign_z1",
          [n for _e, n in p.qe.endstops] == ["zalign_z", "zalign_z1"])
    # zalign.cfg: Block L aktiv, Block B auskommentiert, einkommentiert gültig
    with open(ZALIGN_CFG, encoding="utf-8") as f:
        txt = f.read()
    check("1 zalign.cfg: [k2_zalign] NICHT aktiv (Auslieferung)",
          not re.search(r"^\[k2_zalign\]", txt, re.M))
    pins_l = re.findall(r"^\[gcode_button (\S+)\]\n(?:#.*\n)*pin: (\S+)",
                        txt, re.M)
    check("1 zalign.cfg Block L: gcode_button ^!PA15/^!PA8",
          pins_l == [("zalign_z", "^!PA15"), ("zalign_z1", "^!PA8")], pins_l)
    m = re.search(r"^#\[k2_zalign\]\n((?:#.*\n)+)", txt, re.M)
    block = ""
    if m:
        lines = ["[k2_zalign]"]
        for ln in m.group(1).splitlines():
            if ln.startswith("##"):
                continue
            lines.append(ln[1:])
        block = "\n".join(lines) + "\n"
    ok = bool(m)
    if ok:
        p2 = FakePrinter()
        o2 = zm.PrinterK2ZAlign(make_config(block, printer=p2))
        ok = (o2.cfg["allow_move"] is False and o2.pin_desc ==
              {"z": "^!PA15", "z1": "^!PA8"} and
              o2.cfg["freigabe_macro"] == "_PI_FREIGABE" and
              o2.cfg["require_motor_ready"] is True)
        check("1 zalign.cfg Block B einkommentiert: gültig, GESPERRT, "
              "gleiche Pins", ok, (o2.cfg, o2.pin_desc))
        c2 = o2.cfg
        check("1 Block B Ersttest-Werte <= Stock (30/10/10/40)",
              c2["quick_speed"] <= 30 and c2["slow_speed"] <= 10 and
              c2["rising_dist"] <= 10 and c2["safe_dist"] <= 40, c2)
    else:
        check("1 zalign.cfg Block B gefunden", False)
    check("1 ZALIGN_STOCK reicht nur LIFT durch (kein DRY_RUN vor G28 Z)",
          "KTWO_ZALIGN LIFT=" in txt and "{rawparams}" not in txt)


# ---------------------------------------------------------------------------
# 2. Ablauf gegen Simulation
# ---------------------------------------------------------------------------

def test_core():
    # Normalfall: z 100 mm, z1 101,5 mm über der Schranke
    bed = SimBed(100.0, 101.5)
    res, err, core = run_core(bed)
    check("2 Normalfall ok", err is None and res["ok"], err)
    check("2 Phase A Differenz z1-z = +1,5 mm (Startverkippung)",
          abs(res["phase_a"]["diff"] - 1.5) < 0.003, res["phase_a"])
    check("2 Phase C Delta 0, ein Versuch",
          abs(res["delta"]) < 1e-9 and len(res["phase_c"]) == 1, res)
    check("2 Ende: finish(None) = Z NICHT referenziert",
          bed.calls[-1] == ("finish", None), bed.calls[-1])
    d = [c for c in bed.calls if c[0] == "descend"]
    a = [c for c in d if c[2] == zm.DEFAULTS["quick_speed"]]
    cc = [c for c in d if c[2] == zm.DEFAULTS["slow_speed"]]
    check("2 Etappen <= chunk_dist, Phase A quick, Phase C slow",
          all(c[1] <= zm.DEFAULTS["chunk_dist"] + 1e-9 for c in d) and a
          and cc and len(a) + len(cc) == len(d))
    lifts = [c for c in bed.calls if c[0] == "lift"]
    check("2 genau ein Freifahren (Phase B) mit rising_dist/quick",
          lifts == [("lift", zm.DEFAULTS["rising_dist"],
                     zm.DEFAULTS["quick_speed"])], lifts)
    check("2 Seiten stehen am Ende beide an der Schranke",
          abs(bed.gap["z"]) < 1e-9 and abs(bed.gap["z1"]) < 1e-9, bed.gap)
    check("2 kein Freifahren vor dem ersten prepare",
          kinds(bed)[0] == "prepare", kinds(bed)[:2])

    # Startverkippung 8 mm < safe_dist 10 -> ok, Differenz gemessen
    bed = SimBed(50.0, 42.0)
    res, err, _ = run_core(bed)
    check("2 Startverkippung 8 mm (z1 tiefer) -> ok, diff -8",
          err is None and abs(res["phase_a"]["diff"] + 8.0) < 0.003, err)

    # Verkippung 15 mm > safe_dist 10 -> Abbruch, Invariante
    bed = SimBed(60.0, 45.0)
    res, err, _ = run_core(bed)
    check("2 Verkippung 15 mm > safe_dist -> Abbruch",
          err is not None and "nur Schranke z1" in err, err)
    check("2 Invariante: Wegdifferenz nach erstem Auslösen <= safe_dist",
          bed.max_tilt_after <= zm.DEFAULTS["safe_dist"] + 1e-6,
          bed.max_tilt_after)
    check("2 Abbruch -> finish(None)", bed.calls[-1] == ("finish", None))
    # Konsolen-Audit 23.10.2026: finish scheitert -> im Ergebnis vermerkt
    bed = SimBed(60.0, 45.0)
    orig_finish = bed.finish

    def finish_kaputt(zpos):
        orig_finish(zpos)
        raise RuntimeError("clear_homing_state weg")
    bed.finish = finish_kaputt
    logging.disable(logging.CRITICAL)
    res, err, _ = run_core(bed)
    logging.disable(logging.NOTSET)
    check("2 finish-Fehler nach Abbruch: ursprünglicher Fehler bleibt, "
          "finish_error vermerkt",
          err is not None and "nur Schranke z1" in err
          and "clear_homing_state weg" in (res or {}).get("finish_error", ""),
          (err, res and res.get("finish_error")))

    # defekte Schranke z (löst nie aus), alle chunk/safe-Kombinationen
    worst = 0.0
    okall = True
    for chunk, safe in ((5, 10), (2.5, 5), (10, 10), (3, 7), (1, 40)):
        for gz1 in (0.3, 7.1, 33.3, 120.0):
            bed = SimBed(200.0, gz1, defekt=("z",))
            res, err, _ = run_core(bed, chunk_dist=chunk, safe_dist=safe,
                                   timeout=300)
            okall &= err is not None and "nur Schranke z1" in err
            worst = max(worst, bed.max_tilt_after - safe)
    check("2 defekte Schranke z: immer Abbruch, nie > safe_dist",
          okall and worst <= 1e-6, worst)

    # beide defekt -> max_travel, Gesamtweg begrenzt
    bed = SimBed(500.0, 500.0, defekt=("z", "z1"))
    res, err, _ = run_core(bed, timeout=300)
    weg = sum(c[1] for c in bed.calls if c[0] == "descend")
    check("2 beide Schranken tot -> Abbruch nach max_travel",
          err is not None and "keine Schranke" in err and
          abs(weg - zm.DEFAULTS["max_travel"]) < 1e-6, (err, weg))

    # Start ausgelöst ohne LIFT -> keine Bewegung
    bed = SimBed(0.0, 3.0)
    res, err, core = run_core(bed)
    check("2 Start ausgelöst ohne LIFT -> Abbruch OHNE Bewegung",
          err is not None and "LIFT=1" in err and kinds(bed) == [] and
          not core.moved, (err, kinds(bed)))
    # mit LIFT
    bed = SimBed(0.0, 3.0)
    res, err, _ = run_core(bed, lift_ok=True)
    check("2 LIFT=1: erst rising_dist langsam hoch, dann Ablauf ok",
          err is None and kinds(bed)[:2] == ["prepare", "lift"] and
          bed.calls[[c[0] for c in bed.calls].index("lift")][2] ==
          zm.DEFAULTS["slow_speed"] and res["lift"], (err, kinds(bed)[:3]))
    # Polarität falsch: beide "ausgelöst", bleiben es auch nach LIFT
    bed = SimBed(100.0, 100.0, klemmt=("z", "z1"))
    res, err, _ = run_core(bed, lift_ok=True)
    check("2 Polarität falsch + LIFT -> Abbruch nach LIFT, keine Abwärtsfahrt",
          err is not None and "nach LIFT" in err and
          "descend" not in kinds(bed), (err, kinds(bed)))
    bed = SimBed(100.0, 100.0, klemmt=("z", "z1"))
    res, err, _ = run_core(bed)
    check("2 Polarität falsch ohne LIFT -> keine Bewegung",
          err is not None and kinds(bed) == [])

    # Prellen: Seite z stoppt bei 30 mm, Schranke meldet frei
    bed = SimBed(100.0, 100.0, prellen={"z": 31.3})
    res, err, _ = run_core(bed)
    check("2 Stopp ohne ausgelöste Schranke (Prellen) -> Abbruch",
          err is not None and "meldet aber FREI" in err, err)
    # Signal instabil: nach Auslösen wieder frei
    bed = SimBed(10.0, 20.0, flackern="z")
    res, err, _ = run_core(bed)
    check("2 Schranke nach Auslösen wieder frei -> Abbruch",
          err is not None and "wieder FREI" in err, err)
    # Schranke klemmt nach Freifahren (Phase B)
    bed = SimBed(10.0, 10.0)
    clock = Clock()
    bed.clock = clock
    core = zm.ZAlignCore(cfg_with(), bed, clock)
    orig_lift = bed.lift

    def lift_klemmt(dist, speed):
        orig_lift(dist, speed)
        bed.klemmt.add("z1")
    bed.lift = lift_klemmt
    try:
        core.run()
        err = None
    except zm.ZAlignError as e:
        err = str(e)
    check("2 Phase B: Schranke nach Freifahren noch ausgelöst -> Abbruch",
          err is not None and "Phase B" in err, err)

    # Retries: erster C-Durchlauf Delta 0,1 mm, zweiter 0
    bed = SimBed(20.0, 20.0, offsets=[(0.0, 0.1), (0.0, 0.1)])
    res, err, _ = run_core(bed)
    check("2 Delta 0,1 > Toleranz -> Wiederholung, dann ok",
          err is None and len(res["phase_c"]) == 2 and
          abs(abs(res["phase_c"][0]["diff"]) - 0.1) < 0.003, (err, res and
                                                          res["phase_c"]))
    bed = SimBed(20.0, 20.0, offsets=[(0.0, 0.1), (0.0, 0.0)] * 5)
    res, err, _ = run_core(bed, retries=2)
    check("2 Delta bleibt > Toleranz -> Abbruch nach retries+1",
          err is not None and "nach 3 Versuchen" in err and
          len(res["phase_c"]) == 3, err)
    # Timeout
    bed = SimBed(300.0, 300.0)
    res, err, _ = run_core(bed, timeout=20)
    check("2 Timeout -> Abbruch", err is not None and "Timeout" in err, err)
    # zmax_position
    bed = SimBed(30.0, 30.0)
    res, err, _ = run_core(bed, zmax_position=350.0)
    check("2 zmax_position 350 -> finish(350)",
          err is None and bed.calls[-1] == ("finish", 350.0), bed.calls[-1])
    # Phase C-Budget = rising + slow_extra, Etappen <= chunk
    bed = SimBed(30.0, 30.0, offsets=[(0.0, -20.0)])  # z1 löst in C nie aus
    res, err, _ = run_core(bed, safe_dist=40.0, chunk_dist=5.0, timeout=300)
    c_weg = sum(c[1] for c in bed.calls if c[0] == "descend" and
                c[2] == zm.DEFAULTS["slow_speed"])
    budget = zm.DEFAULTS["rising_dist"] + zm.DEFAULTS["slow_extra_dist"]
    check("2 Phase C ohne zweite Schranke -> Abbruch, Weg <= Budget",
          err is not None and c_weg <= budget + 1e-6, (err, c_weg))
    # Plan-Text
    txt = "\n".join(zm.plan_lines(cfg_with()))
    check("2 DRY_RUN-Plan nennt Sperren, Phasen und Grenzen",
          all(k in txt for k in ("allow_move", "A abwärts", "B ", "C abwärts",
                                 "safe", "NICHT referenziert")), txt)


# ---------------------------------------------------------------------------
# 3. Klipper-Anbindung mit Fakes
# ---------------------------------------------------------------------------

class FakeEndstop:
    def __init__(self, desc, bed, side):
        self.desc, self.bed, self.side = desc, bed, side
        self.steppers = []

    def add_stepper(self, s):
        self.steppers.append(s)

    def get_steppers(self):
        return list(self.steppers)

    def query_endstop(self, print_time):
        return 1 if self.bed._trig(self.side) else 0


class FakePins:
    def __init__(self, printer):
        self.printer = printer
        self.setups = []

    def setup_pin(self, ptype, desc):
        self.setups.append((ptype, desc))
        side = "z" if "PA15" in desc else "z1"
        return FakeEndstop(desc, self.printer.bed, side)


class FakeQE:
    def __init__(self):
        self.endstops = []

    def register_endstop(self, es, name):
        self.endstops.append((es, name))


class FakeStepper:
    def __init__(self, name, printer, side):
        self.name, self.printer, self.side = name, printer, side
        self.mcu_pos = 0

    def get_name(self):
        return self.name

    def is_active_axis(self, a):
        return a == "z" and self.side is not None

    def get_mcu_position(self):
        return self.mcu_pos

    def get_step_dist(self):
        return STEP


class FakeKin:
    def __init__(self, steppers):
        self.steppers = steppers
        self.cleared = []

    def get_steppers(self):
        return list(self.steppers)

    def get_status(self, eventtime):
        return {"axis_minimum": (-10.0, -6.2, -10.0, 0.0),
                "axis_maximum": (352.5, 400.0, 360.0, 0.0)}

    def clear_homing_state(self, axes):
        self.cleared.append(axes)


class FakeToolhead:
    def __init__(self, kin, bed=None):
        self.kin = kin
        self.bed = bed
        self.pos = [0.0, 0.0, 0.0, 0.0]
        self.set_calls, self.moves = [], []
        self.homed = ""

    def get_kinematics(self):
        return self.kin

    def get_position(self):
        return list(self.pos)

    def set_position(self, pos, homing_axes=""):
        self.pos = list(pos)
        self.set_calls.append((list(pos), homing_axes))
        if homing_axes:
            self.homed = homing_axes

    def get_last_move_time(self):
        return 1.0

    def move(self, pos, speed):
        lo, hi = -10.0, 360.0
        assert lo - 1e-9 <= pos[2] <= hi + 1e-9, pos
        self.moves.append((list(pos), speed))
        dz = self.pos[2] - pos[2]
        if self.bed is not None and dz > 0:
            self.bed.lift(dz, speed)       # Z kleiner = Bett hoch
        self.pos = list(pos)

    def wait_moves(self):
        pass


class FakeHoming:
    def __init__(self, printer):
        self.printer = printer
        self.calls = []

    def manual_home(self, toolhead, endstops, pos, speed, probe_pos,
                    triggered, check_triggered):
        self.calls.append({"pos": list(pos), "speed": speed,
                           "probe_pos": probe_pos, "triggered": triggered,
                           "check_triggered": check_triggered,
                           "names": [n for _e, n in endstops]})
        assert -10.0 - 1e-9 <= pos[2] <= 360.0 + 1e-9, pos
        dist = pos[2] - toolhead.pos[2]
        moved = self.printer.bed.descend(dist, speed)
        for es, _n in endstops:
            for s in es.get_steppers():
                s.mcu_pos += int(round(moved[s.side] / STEP))
        toolhead.pos = list(pos)
        return pos


class FakeMacro:
    def __init__(self, allow):
        self.allow = allow

    def get_status(self, eventtime):
        return {"allow_move": self.allow, "xy_stall_ok": True}


class FakeStatus:
    def __init__(self, st):
        self.st = st

    def get_status(self, eventtime):
        return dict(self.st)


class FakeZStatus:
    def __init__(self):
        self.resets = 0

    def reset(self):
        self.resets += 1


class FakeZTilt:
    def __init__(self):
        self.z_status = FakeZStatus()


class FakeReactor:
    def __init__(self):
        self.t = 0.0

    def monotonic(self):
        self.t += 0.01
        return self.t


class FakeGcode:
    def __init__(self, printer):
        self.cmds, self.out = {}, []
        real = ugcode.GCodeDispatch.__new__(ugcode.GCodeDispatch)
        real.printer = printer
        real.ready_gcode_handlers = real.gcode_handlers = {}
        real.base_gcode_handlers, real.gcode_help = {}, {}
        self._real = real

    def register_command(self, cmd, fn, when_not_ready=False, desc=None):
        self._real.register_command(cmd, fn, when_not_ready, desc)
        parts = ugcode.GCodeDispatch.args_r.split(cmd.upper())
        if "".join(parts[:3]).strip() != cmd:
            raise RuntimeError("Parser zerlegt %r" % cmd)
        self.cmds[cmd] = fn

    def respond_info(self, msg, log=True):
        self.out.append(msg)

    def respond_raw(self, msg):
        self.out.append(msg)

    def run(self, cmd, **params):
        p = {k: str(v) for k, v in params.items()}
        g = ugcode.GCodeCommand(self, cmd, cmd, p, False)
        self.cmds[cmd](g)


class FakePrinter:
    command_error = ugcode.CommandError
    config_error = configparser.Error

    def __init__(self, bed=None):
        self.bed = bed or SimBed(100.0, 100.0)
        self.objects = {}
        self.handlers = {}
        self.reactor = FakeReactor()
        self.pins = FakePins(self)
        self.qe = FakeQE()
        self.gcode = FakeGcode(self)
        sz = FakeStepper("stepper_z", self, "z")
        sz1 = FakeStepper("stepper_z1", self, "z1")
        sx = FakeStepper("stepper_x", self, None)
        self.kin = FakeKin([sx, sz, sz1])
        self.toolhead = FakeToolhead(self.kin, self.bed)
        self.homing = FakeHoming(self)
        self.objects.update({
            "pins": self.pins, "gcode": self.gcode,
            "toolhead": self.toolhead, "homing": self.homing,
            "gcode_macro _PI_FREIGABE": FakeMacro(False),
            "print_stats": FakeStatus({"state": "standby"}),
            "k2_motor": object(), "z_tilt": FakeZTilt()})

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=configparser.Error):
        if name in self.objects:
            return self.objects[name]
        if default is configparser.Error:
            raise self.config_error("Unknown config object '%s'" % name)
        return default

    def load_object(self, config, name):
        assert name == "query_endstops"
        return self.qe

    def register_event_handler(self, ev, cb):
        self.handlers.setdefault(ev, []).append(cb)

    def send_event(self, ev, *a):
        return [cb(*a) for cb in self.handlers.get(ev, [])]


BASE = ("[k2_zalign]\npin_z: ^!PA15\npin_z1: ^!PA8\n")


def make_obj(extra="", bed=None):
    p = FakePrinter(bed)
    obj = zm.PrinterK2ZAlign(make_config(BASE + extra, printer=p))
    p.send_event("klippy:mcu_identify")
    return p, obj


def run_cmd(p, cmd, **params):
    try:
        p.gcode.run(cmd, **params)
        return None
    except ugcode.CommandError as e:
        return str(e)


def test_klipper():
    p, obj = make_obj()
    check("3 Stepper zugeordnet: zalign_z->stepper_z, zalign_z1->stepper_z1",
          [s.get_name() for s in obj.endstops["z"][0].get_steppers()] ==
          ["stepper_z"] and
          [s.get_name() for s in obj.endstops["z1"][0].get_steppers()] ==
          ["stepper_z1"])
    # Gesperrt (allow_move False) -> keine Bewegung
    err = run_cmd(p, "KTWO_ZALIGN")
    check("3 allow_move False -> G-Code-Fehler, keine Bewegung",
          err and "gesperrt" in err and "allow_move: False" in err and
          not p.homing.calls and not p.toolhead.set_calls, err)
    # DRY_RUN
    err = run_cmd(p, "KTWO_ZALIGN", DRY_RUN=1)
    check("3 DRY_RUN: Plan + Sperren + Schranken, keine Bewegung",
          err is None and "Plan KTWO_ZALIGN" in p.gcode.out[-1] and
          "allow_move: False" in p.gcode.out[-1] and
          "zalign_z:open" in p.gcode.out[-1] and not p.homing.calls,
          p.gcode.out[-1:])
    err = run_cmd(p, "KTWO_ZALIGN_STATUS")
    check("3 STATUS lesend: open/open + GESPERRT",
          err is None and "zalign_z:open zalign_z1:open" in p.gcode.out[-1]
          and "GESPERRT" in p.gcode.out[-1], p.gcode.out[-1:])
    # allow_move True, aber Freigabe-Makro False
    p, obj = make_obj("allow_move: True\n")
    err = run_cmd(p, "KTWO_ZALIGN")
    check("3 _PI_FREIGABE allow_move=False -> gesperrt",
          err and "_PI_FREIGABE allow_move=False" in err and
          not p.homing.calls, err)
    p.objects["gcode_macro _PI_FREIGABE"] = FakeMacro("True")
    err = run_cmd(p, "KTWO_ZALIGN")
    check("3 Freigabe als String 'True' gilt NICHT", err and "gesperrt" in err)
    p.objects["gcode_macro _PI_FREIGABE"] = FakeMacro(True)
    err = run_cmd(p, "KTWO_ZALIGN")
    check("3 Motor-Init fehlt -> gesperrt",
          err and "k2_motor:ready" in err and not p.homing.calls, err)
    p.send_event("k2_motor:ready")
    p.objects["print_stats"] = FakeStatus({"state": "printing"})
    err = run_cmd(p, "KTWO_ZALIGN")
    check("3 Druck aktiv -> gesperrt", err and "Druck aktiv" in err)
    del p.objects["gcode_macro _PI_FREIGABE"]
    p.objects["print_stats"] = FakeStatus({"state": "standby"})
    err = run_cmd(p, "KTWO_ZALIGN")
    check("3 Freigabe-Makro fehlt -> gesperrt", err and "fehlt" in err)
    # Volle Freigabe, Erfolgspfad
    bed = SimBed(120.0, 121.25)
    p, obj = make_obj("allow_move: True\n", bed=bed)
    p.objects["gcode_macro _PI_FREIGABE"] = FakeMacro(True)
    p.send_event("k2_motor:ready")
    err = run_cmd(p, "KTWO_ZALIGN")
    r = obj.last_result
    check("3 Erfolgspfad ok, Startverkippung +1,25 mm aus get_mcu_position",
          err is None and r["ok"] and abs(r["phase_a"]["diff"] - 1.25) <
          0.003, (err, r))
    h = p.homing.calls
    check("3 manual_home: probe_pos=False triggered=True "
          "check_triggered=False, beide Schranken",
          h and all(c["probe_pos"] is False and c["triggered"] is True and
                    c["check_triggered"] is False and
                    c["names"] == ["zalign_z", "zalign_z1"] for c in h), h[:1])
    check("3 Etappen-Ziele im Z-Bereich, Start bei axis_minimum",
          all(-10.0 <= c["pos"][2] <= -10.0 + 5.0 + 1e-9 for c in h),
          [c["pos"][2] for c in h][:3])
    check("3 Z beim Start als 'z' referenziert (nur Z)",
          p.toolhead.set_calls[0][1] == "z", p.toolhead.set_calls[0])
    check("3 Freifahren: move auf axis_maximum - rising_dist",
          p.toolhead.moves and p.toolhead.moves[0][0][2] == 350.0,
          p.toolhead.moves[:1])
    check("3 Ende: clear_homing_state('z')", p.kin.cleared == ["z"],
          p.kin.cleared)
    check("3 z_tilt.applied zurückgesetzt",
          p.objects["z_tilt"].z_status.resets == 1)
    st = obj.get_status()
    check("3 get_status: last_result + allow_move",
          st["allow_move"] is True and st["last_result"]["ok"] is True)
    check("3 Abschlussmeldung nennt Startverkippung und G28 Z",
          "Startverkippung" in p.gcode.out[-1] and "G28 Z" in p.gcode.out[-1],
          p.gcode.out[-1:])
    # Abbruch (Schranke z1 defekt) -> G-Code-Fehler, Z nicht referenziert
    bed = SimBed(40.0, 40.0, defekt=("z1",))
    p, obj = make_obj("allow_move: True\nsafe_dist: 5\nchunk_dist: 2.5\n",
                      bed=bed)
    p.objects["gcode_macro _PI_FREIGABE"] = FakeMacro(True)
    p.send_event("k2_motor:ready")
    err = run_cmd(p, "KTWO_ZALIGN")
    check("3 defekte Schranke -> G-Code-Fehler 'Abbruch', Z gelöscht",
          err and "Abbruch" in err and "safe_dist" in err and
          p.kin.cleared == ["z"] and bed.max_tilt_after <= 5.0 + 1e-6,
          (err, bed.max_tilt_after))
    # Start ausgelöst ohne LIFT -> kein set_position
    bed = SimBed(0.0, 0.0)
    p, obj = make_obj("allow_move: True\n", bed=bed)
    p.objects["gcode_macro _PI_FREIGABE"] = FakeMacro(True)
    p.send_event("k2_motor:ready")
    err = run_cmd(p, "KTWO_ZALIGN")
    check("3 Start ausgelöst -> 'ohne Bewegung', kein set_position/move",
          err and "ohne Bewegung" in err and not p.toolhead.set_calls and
          not p.homing.calls and not p.kin.cleared and
          p.objects["z_tilt"].z_status.resets == 0, err)
    err = run_cmd(p, "KTWO_ZALIGN", LIFT=1)
    check("3 LIFT=1 -> Freifahren mit slow_speed, dann ok",
          err is None and p.toolhead.moves[0][1] == zm.DEFAULTS["slow_speed"],
          (err, p.toolhead.moves[:1]))
    # falscher Stepper-Name
    p = FakePrinter()
    obj = zm.PrinterK2ZAlign(make_config(BASE + "stepper_z1: stepper_x\n",
                                         printer=p))
    check("3 stepper_z1 kein Z-Stepper -> config error",
          raises(lambda: p.send_event("klippy:mcu_identify"),
                 configparser.Error, "kein Z-Stepper"))
    # zmax_position
    bed = SimBed(30.0, 30.0)
    p, obj = make_obj("allow_move: True\nzmax_position: 345\n", bed=bed)
    p.objects["gcode_macro _PI_FREIGABE"] = FakeMacro(True)
    p.send_event("k2_motor:ready")
    err = run_cmd(p, "KTWO_ZALIGN")
    check("3 zmax_position 345 -> Z=345 gesetzt, nicht gelöscht",
          err is None and p.toolhead.pos[2] == 345.0 and not p.kin.cleared,
          (err, p.toolhead.pos))


# ---------------------------------------------------------------------------
# 4./5. Namen, Syntax
# ---------------------------------------------------------------------------

def test_names_syntax():
    p, obj = make_obj()
    names = sorted(p.gcode.cmds)
    check("4 registrierte Befehle (Upstream-Prüfung + Parser)",
          names == ["KTWO_ZALIGN", "KTWO_ZALIGN_STATUS"], names)
    check("4 Gegenprobe: Ziffer in der Mitte wird vom Parser zerlegt",
          raises(lambda: p.gcode.register_command("KTWO_Z1_ALIGN",
                                                  lambda g: None),
                 RuntimeError, "Parser"))
    check("4 Gegenprobe: K2_* lehnt Upstream ab",
          raises(lambda: p.gcode.register_command("K2_ZALIGN",
                                                  lambda g: None)))
    for path in (MODULE_PATH, os.path.abspath(__file__)):
        with open(path, encoding="utf-8") as f:
            src = f.read()
        try:
            ast.parse(src, feature_version=(3, 9))
            ok = True
        except SyntaxError as e:
            ok = False
            print("  ", e)
        check("5 Python-3.9-Syntax %s" % os.path.basename(path), ok)
    with open(MODULE_PATH, encoding="utf-8") as f:
        src = f.read()
    check("5 keine UUID/Seriennummer im Modul",
          not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", src, re.I))
    check("5 Modul nutzt keine Creality-Befehle (z_align/query_z_align)",
          "config_z_align" not in src.split('"""', 2)[2] and
          "query_z_align" not in src.split('"""', 2)[2])


def main():
    for fn in (test_config, test_core, test_klipper, test_names_syntax):
        print("== %s" % fn.__name__)
        fn()
    bad = [r for r in RESULTS if not r[1]]
    print("\n%d Pruefungen, %d bestanden, %d fehlgeschlagen" % (
        len(RESULTS), len(RESULTS) - len(bad), len(bad)))
    if bad:
        for name, ok, detail in bad:
            print("FEHLER:", name, detail)
        sys.exit(1)
    print("ALLE TESTS BESTANDEN")


if __name__ == "__main__":
    main()
