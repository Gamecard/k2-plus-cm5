#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline-Tests für k2_limits.py ([k2_limits], L3 18.10.2026) und die
Makro-Stellen (macros.cfg, cutcal.cfg), ohne Klipper und ohne Hardware.

1. LimitsCore: eng nach Homing, Aufweiten nur gehomt (sonst nichts
   geändert), Halter (k2_box + G-Code), Rücksetzen, Homing im weiten
   Zustand, motor_off; Upstream-check_move-Nachbau („Move out of range“).
2. xy_points: G90/G91, G2/G3-Hülle.
3. Koordinaten ALLER Fahrwege (k2_box-Generatoren mit k2-module.cfg-
   Werten, Schneid-Kalibrierung, RESUME, PAUSE, Homing) gegen die neuen
   Grenzen: im engen Fenster oder (nur mit Aufweitung) im weiten.
4. PrinterK2Limits mit Fake-Klipper: Config-Prüfungen (Y > 352, Z > 350,2,
   x_min_wide < -10 -> Fehler), Connect (X min = [k2_box] cut_pos_x,
   Transform-Kette), G-Codes, Auto-Rücksetzen bei gcode:command_error /
   motor_off / Shutdown, Z-Wächter (Z < -2 außerhalb des Drucks abgelehnt,
   im Druck/ungehomt/aufwärts erlaubt, gcode_move resynchronisiert).
4a. Druck-Schutz (print_low_z_guard): G-Code-Z < 0,05 im Druck auf 2,0
   angehoben (Orca z_after_toolchange=0), Meldung einmal je Druck, Z0,2
   unverändert, Offsets (Schwelle G-Code-Ebene), außerhalb des Drucks alte
   Regel, Pause ohne Eingriff (außer beim Halten), Option aus, Z ungehomt;
   Kette k2_limits -> k2_nozzle_tempcomp -> bed_mesh.
5. Makros gerendert (jinja2): _PI_CLEAN_PATH, _PI_RESUME_RETURN,
   CALIBRATE_CUT_POS, PAUSE – Bahn mit Upstream-Grenzprüfung simuliert,
   0 „Move out of range“; M141/M191 Kammer-Soll auf [k2_safety]-Grenze
   (bzw. 60) begrenzt, TEMPERATURE_WAIT wartet nie über der Grenze.
"""

import ast
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import k2_limits as L  # noqa: E402
import k2_box as KB    # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print("%s %s %s" % ("PASS" if cond else "FAIL", name,
                        detail if not cond else ""))


# ---------------------------------------------------------------------
# Fakes (Upstream-Ausschnitte)
# ---------------------------------------------------------------------

class FakeRail:
    """Upstream GenericPrinterRail: position_min/max + get_range()."""

    def __init__(self, lo, hi):
        self.position_min = lo
        self.position_max = hi

    def get_range(self):
        return self.position_min, self.position_max


class FakeKin:
    """Upstream corexy: limits, rails, set_position(homing_axes),
    clear_homing_state, check_move (Logik 1:1 corexy.py)."""

    def __init__(self, x=(-7.8, 352.5), y=(-6.2, 352.0), z=(-10.0, 350.2)):
        self.rails = [FakeRail(*x), FakeRail(*y), FakeRail(*z)]
        self.limits = [(1.0, -1.0)] * 3
        self.axes_min = (x[0], y[0], z[0])
        self.axes_max = (x[1], y[1], z[1])

    def set_position(self, newpos, homing_axes):
        for i, rail in enumerate(self.rails):
            if "xyz"[i] in homing_axes:
                self.limits[i] = rail.get_range()

    def clear_homing_state(self, axes):
        for i, a in enumerate("xyz"):
            if a in axes:
                self.limits[i] = (1.0, -1.0)

    def move_error(self, start, end):
        """None = erlaubt, sonst Text (wie corexy.check_move/_check_endstops)."""
        d = [e - s for s, e in zip(start, end)]
        x, y = end[:2]
        lim = self.limits
        need = (x < lim[0][0] or x > lim[0][1] or y < lim[1][0] or y > lim[1][1]
                or d[2])
        if not need:
            return None
        for i in (0, 1, 2):
            if d[i] and (end[i] < lim[i][0] or end[i] > lim[i][1]):
                if lim[i][0] > lim[i][1]:
                    return "Must home axis first"
                return "Move out of range: %.3f %.3f %.3f" % tuple(end[:3])
        return None


def homed_kin():
    k = FakeKin()
    k.set_position(None, "xyz")
    return k


def core_for(kin, x_lo=-7.8):
    return L.LimitsCore(kin, narrow={0: (x_lo, 352.5), 1: (-6.2, 352.0)},
                        wide={0: (-10.0, 352.5), 1: (-6.2, 400.0)})


# ---------------------------------------------------------------------
# 1. LimitsCore
# ---------------------------------------------------------------------

def test_core():
    kin = FakeKin()
    c = core_for(kin)
    c.restore_all("start")
    check("Core: ungehomt bleibt ungehomt beim Rücksetzen",
          kin.limits[0] == (1.0, -1.0) and kin.limits[1] == (1.0, -1.0))
    err = None
    try:
        c.widen("gcode")
    except L.LimitsError as exc:
        err = exc
    check("Core: Aufweiten ungehomt -> LimitsError, nichts geändert",
          err is not None and kin.rails[1].position_max == 352.0
          and kin.limits[1] == (1.0, -1.0) and not c.is_wide(), repr(err))
    kin.set_position(None, "x")   # nur X gehomt
    err = None
    try:
        c.widen("gcode")
    except L.LimitsError as exc:
        err = exc
    check("Core: nur X gehomt -> verweigert (Y nicht referenziert)",
          err is not None and "Y" in str(err) and not c.is_wide())
    kin.set_position(None, "xyz")
    check("Core: nach Homing eng (X ab -7,8, Y bis 352)",
          kin.limits[0] == (-7.8, 352.5) and kin.limits[1] == (-6.2, 352.0))
    c.widen("gcode", "Test")
    check("Core: aufgeweitet X ab -10, Y bis 400 (Rails + limits)",
          kin.limits[0] == (-10.0, 352.5) and kin.limits[1] == (-6.2, 400.0)
          and kin.rails[1].position_max == 400.0 and c.is_wide())
    c.widen("k2_box")
    c.release("k2_box")
    check("Core: Halter k2_box zurück, G-Code-Halter hält weit",
          c.is_wide() and kin.limits[1] == (-6.2, 400.0))
    c.release("gcode")
    check("Core: letzter Halter zurück -> eng",
          not c.is_wide() and kin.limits[1] == (-6.2, 352.0)
          and kin.rails[0].position_min == -7.8)
    c.widen("gcode")
    c.widen("k2_box")
    c.restore_all("RESTORE")
    check("Core: restore_all (Stock RESTORE_LIMITS) unbedingt eng",
          not c.is_wide() and kin.limits[1] == (-6.2, 352.0))
    # Homing im weiten Zustand -> wide (Upstream nimmt rail.get_range), dann eng
    c.widen("gcode")
    kin.set_position(None, "y")
    check("Core: G28 Y im weiten Zustand -> Y weit", kin.limits[1] == (-6.2, 400.0))
    c.restore_all()
    check("Core: danach eng", kin.limits[1] == (-6.2, 352.0))
    # motor_off: limits ungehomt, Rücksetzen macht nichts gehomt
    c.widen("gcode")
    kin.clear_homing_state("xyz")
    c.restore_all("motor_off")
    check("Core: motor_off -> Rails eng, Achsen ungehomt",
          kin.limits[0] == (1.0, -1.0) and kin.rails[1].position_max == 352.0)
    # Upstream-Grenzprüfung
    kin = homed_kin()
    c = core_for(kin)
    c.restore_all()
    e1 = kin.move_error((133, 345, 5), (133, 378, 5))
    e2 = kin.move_error((133, 378, 5), (160, 378, 5))
    e3 = kin.move_error((160, 378, 5), (160, 378, 7))
    e4 = kin.move_error((160, 378, 5), (160, 345, 5))
    check("Upstream-Prüfung eng: Y378 abgelehnt, X-/Z-/E-Fahrt im Schacht "
          "erlaubt, Fahrt ins Fenster erlaubt",
          e1 and "out of range" in e1 and e2 is None and e3 is None and e4 is None,
          (e1, e2, e3, e4))
    c.widen("k2_box")
    check("Upstream-Prüfung weit: Y378 erlaubt",
          kin.move_error((133, 345, 5), (133, 378, 5)) is None)
    c.release("k2_box")
    check("Upstream-Prüfung eng: X -9 abgelehnt, X = cut_pos_x -7,8 erlaubt",
          kin.move_error((10, 200, 5), (-9, 200, 5)) is not None
          and kin.move_error((10, 200, 5), (-7.8, 200, 5)) is None)
    check("Upstream-Prüfung: Z 350,2 erlaubt, 350,3 abgelehnt",
          kin.move_error((0, 0, 300), (0, 0, 350.2)) is None
          and kin.move_error((0, 0, 300), (0, 0, 350.3)) is not None)
    c.set_narrow_x_min(-8.2)
    check("Core: X min nachziehen (cut_pos_x -8,2)",
          kin.limits[0] == (-8.2, 352.5) and kin.rails[0].position_min == -8.2)
    err = None
    try:
        core_for(FakeKin(), x_lo=-11)
    except L.LimitsError as exc:
        err = exc
    check("Core: enges Fenster außerhalb weit -> Fehler", err is not None)


# ---------------------------------------------------------------------
# 2. xy_points
# ---------------------------------------------------------------------

def test_xy_points():
    pts = L.xy_points(["G90", "G0 Y345 F12000", "G0 X139", "G91", "G1 X-6",
                       "G1 E5", "M400", "G90", "G0 X10 Y200"], (200, 200))
    check("xy_points: G90/G91 verfolgt, E/M400 ignoriert",
          pts == [(200, 345), (139, 345), (133, 345), (10, 200)], pts)
    pts = L.xy_points(["G2 I4 J0 F10000"], (160, 374))
    check("xy_points: G2-Hülle (Kreis um 164|374, r 4)",
          (164, 378) in pts and (168, 374) in pts and (160, 374) in pts, pts)
    pts = L.xy_points(["G91", "G1 Y10"], None, absolute=True)
    check("xy_points: relative Fahrt ohne Start -> unbekannt (None)",
          pts == [(None, None)], pts)


# ---------------------------------------------------------------------
# 3. Koordinaten aller Fahrwege gegen die neuen Grenzen
# ---------------------------------------------------------------------

def _kcfg():
    """[k2_box]-Werte aus k2-module.cfg + CHANGE_DEFAULTS."""
    c = dict(KB.CHANGE_DEFAULTS)
    txt = open(os.path.join(_HERE, "config-pi", "k2-module.cfg"),
               encoding="utf-8").read()
    a = txt.index("\n[k2_box]\n") + 1
    for ln in txt[a:txt.index("\n[", a + 1)].splitlines()[1:]:
        if ":" in ln and not ln.lstrip().startswith("#"):
            k, v = ln.split(":", 1)
            k = k.strip().lower()
            if k in c:
                try:
                    c[k] = float(v.split("#")[0].strip())
                except ValueError:
                    pass
    return c


def _cfg_window():
    """Enge Grenzen aus printer.cfg lesen."""
    txt = open(os.path.join(_HERE, "config-pi", "printer.cfg"),
               encoding="utf-8").read()

    def sec(name, key):
        a = txt.index("\n[%s]\n" % name)
        b = txt.index("\n[", a + 1)
        for ln in txt[a:b].splitlines():
            if ln.startswith(key + ":"):
                return float(ln.split(":", 1)[1])
        raise KeyError(key)
    return {"x": (sec("stepper_x", "position_min"), sec("stepper_x", "position_max")),
            "y": (sec("stepper_y", "position_min"), sec("stepper_y", "position_max")),
            "z": (sec("stepper_z", "position_min"), sec("stepper_z", "position_max"))}


def test_fahrwege():
    w = _cfg_window()
    check("printer.cfg: X -7,8..352,5, Y -6,2..352, Z -10..350,2 (Stock-Fenster)",
          w == {"x": (-7.8, 352.5), "y": (-6.2, 352.0), "z": (-10.0, 350.2)}, w)
    check("Stock-Z-Formel 360-10+0+0,2 = 350,2", L.STOCK_Z_MAX == 350.2)
    c = _kcfg()
    narrow = {0: (c["cut_pos_x"], 352.5), 1: (-6.2, 352.0)}
    wide = {0: (-10.0, 352.5), 1: (-6.2, 400.0)}

    def inside(p, win):
        return win[0][0] - 1e-6 <= p[0] <= win[0][1] + 1e-6 and \
            win[1][0] - 1e-6 <= p[1] <= win[1][1] + 1e-6
    paths = {
        "Schacht (GO_EXTRUDE_POS)": (KB.gcode_go_extrude_pos(c), (200, 200)),
        "Schacht aus Schneidbereich": (["G90"] + KB.gcode_leave_cut_zone(
            c, [c["cut_pos_x"], 200, 5]) + KB.gcode_go_extrude_pos(c)[1:],
            (c["cut_pos_x"], 200)),
        "Abstreifen (WIPE)": (KB.gcode_wipe(c, 20000), (c["extrude_pos_x"], c["extrude_pos_y"])),
        "Reinigung mit Kreis": (KB.gcode_nozzle_clean(c, True, 2.0, 20000),
                                (c["extrude_pos_x"], c["extrude_pos_y"])),
        "Reinigung ohne Kreis": (KB.gcode_nozzle_clean(c, False),
                                 (c["extrude_pos_x"], c["extrude_pos_y"])),
        "Schacht verlassen": (KB.gcode_safe_pos(c), (c["extrude_pos_x"], c["extrude_pos_y"])),
        "Schneiden": (KB.gcode_cut(c, 20000), (200, 200)),
    }
    bad, need = [], {}
    for name, (lines, start) in paths.items():
        pts = L.xy_points(lines, start)
        if any(not inside(p, wide) for p in pts):
            bad.append((name, [p for p in pts if not inside(p, wide)]))
        need[name] = any(not inside(p, narrow) for p in pts)
    check("Fahrwege k2_box: alle Punkte im weiten Fenster (X >= -10, Y <= 400)",
          not bad, bad)
    check("Fahrwege k2_box: Schacht/Wischen/Reinigung brauchen Aufweitung, "
          "Schneiden (X = cut_pos_x) nicht",
          need["Schacht (GO_EXTRUDE_POS)"] and need["Abstreifen (WIPE)"]
          and need["Reinigung mit Kreis"] and not need["Schneiden"], need)
    # Schneid-Kalibrierung (k2_motor CUT_CAL-Defaults)
    xs = [-5.0 - 0.1 * i for i in range(46)]
    check("Schneid-Kalibrierung: Anstöße bis -9,5 im weiten Fenster, außerhalb "
          "des engen (Aufweitung nötig)",
          min(xs) >= -10.0 and min(xs) < c["cut_pos_x"]
          and abs(min(xs) - c["check_cut_pos_x_min"]) < 1e-6)
    # PAUSE-Park: axis_maximum.x - 10 / axis_maximum.y / 2 (eng)
    park = (352.5 - 10, 352.0 / 2)
    check("PAUSE-Park X342,5 Y176 im engen Fenster (Stock: max_y 352 / 2)",
          inside(park, narrow), park)
    # Homing-Rückzüge: X 352,5-10, Y -6,2+10
    check("Homing-Rückzüge X342,5 / Y3,8 im engen Fenster",
          inside((342.5, 3.8), narrow))
    # END_PRINT/PAUSE Z (max_z_position 350) unter Z max 350,2
    mz = 350.0
    check("END_PRINT/PAUSE Z-Ziel <= max_z_position 350 < 350,2", mz <= w["z"][1])


# ---------------------------------------------------------------------
# 4. PrinterK2Limits mit Fake-Klipper
# ---------------------------------------------------------------------

class CmdError(Exception):
    pass


class FakeSection:
    def __init__(self, opts):
        self.opts = opts

    def getfloat(self, name, default=None, **kw):
        return float(self.opts.get(name, default))


class FakeConfig:
    def __init__(self, printer, opts=None, steppers=None):
        self.printer = printer
        self.opts = dict(opts or {})
        st = {"stepper_x": {"position_min": -7.8, "position_max": 352.5},
              "stepper_y": {"position_min": -6.2, "position_max": 352.0},
              "stepper_z": {"position_min": -10.0, "position_max": 350.2}}
        for k, v in (steppers or {}).items():
            st[k].update(v)
        self.st = st

    def get_printer(self):
        return self.printer

    def getsection(self, name):
        return FakeSection(self.st[name])

    def getfloat(self, name, default=None, minval=None, maxval=None, **kw):
        v = float(self.opts.get(name, default))
        if minval is not None and v < minval:
            raise ValueError("%s < minval" % name)
        if maxval is not None and v > maxval:
            raise ValueError("%s > maxval" % name)
        return v

    def getboolean(self, name, default=None):
        v = self.opts.get(name, default)
        return v if isinstance(v, bool) else str(v) == "True"

    def error(self, msg):
        return ValueError(msg)


class FakeGcode:
    def __init__(self):
        self.cmds = {}
        self.infos = []

    def register_command(self, name, fn, desc=None):
        self.cmds[name] = fn

    def respond_info(self, msg):
        self.infos.append(msg)


class FakeGcmd:
    def __init__(self, params=None):
        self.params = params or {}
        self.infos = []

    def get(self, name, default=None):
        return self.params.get(name, default)

    def respond_info(self, msg):
        self.infos.append(msg)

    def error(self, msg):
        return CmdError(msg)


class FakeToolhead:
    def __init__(self, kin):
        self.kin = kin
        self.pos = [200.0, 200.0, 5.0, 0.0]
        self.moves = []

    def get_kinematics(self):
        return self.kin

    def get_position(self):
        return list(self.pos)

    def move(self, newpos, speed):
        err = self.kin.move_error(self.pos, newpos)
        if err:
            raise CmdError(err)
        self.moves.append(list(newpos))
        self.pos = list(newpos)


class FakeGcodeMove:
    def __init__(self, toolhead):
        self.th = toolhead
        self.transform = None
        self.resets = 0
        self.base_position = [0.0, 0.0, 0.0, 0.0]   # Upstream gcode_move

    def set_move_transform(self, t, force=False):
        old = self.transform or self.th
        self.transform = t
        return old

    def reset_last_position(self):
        self.resets += 1

    def get_status(self, eventtime=None):
        p = self.th.pos
        return {"gcode_position": list(p), "position": list(p),
                "absolute_coordinates": True}


class FakePrintStats:
    def __init__(self):
        self.state = "standby"
        self.print_start_time = None      # Upstream note_start setzt es


class FakeBox:
    def __init__(self, cut):
        self.cfg = {"cut_pos_x": cut}


class FakePrinter:
    command_error = CmdError

    def __init__(self, kin, box_cut=-7.8):
        self.gcode = FakeGcode()
        self.kin = kin
        self.toolhead = FakeToolhead(kin)
        self.gcode_move = FakeGcodeMove(self.toolhead)
        self.ps = FakePrintStats()
        self.objects = {"gcode": self.gcode, "toolhead": self.toolhead,
                        "gcode_move": self.gcode_move, "print_stats": self.ps}
        if box_cut is not None:
            self.objects["k2_box"] = FakeBox(box_cut)
        self.handlers = {}

    def lookup_object(self, name, default=KeyError):
        if name in self.objects:
            return self.objects[name]
        if default is KeyError:
            raise KeyError(name)
        return default

    def register_event_handler(self, ev, fn):
        self.handlers.setdefault(ev, []).append(fn)

    def fire(self, ev):
        for fn in self.handlers.get(ev, []):
            fn()

    def get_reactor(self):
        class R:
            def monotonic(self):
                return 0.0
        return R()

    def config_error(self, msg):
        return ValueError(msg)


def make(opts=None, steppers=None, box_cut=-7.8, homed=True):
    kin = FakeKin()
    pr = FakePrinter(kin, box_cut=box_cut)
    mod = L.PrinterK2Limits(FakeConfig(pr, opts, steppers))
    pr.fire("klippy:connect")
    if homed:
        kin.set_position(None, "xyz")
    return pr, mod


def test_modul():
    for desc, steppers, opts in (
            ("Y position_max 400", {"stepper_y": {"position_max": 400}}, None),
            ("Z position_max 360", {"stepper_z": {"position_max": 360}}, None),
            ("X position_min -11", {"stepper_x": {"position_min": -11}}, None),
            ("x_min_wide -11", None, {"x_min_wide": -11}),
            ("y_max_wide 420", None, {"y_max_wide": 420}),
            ("z_min_outside_print -5", None, {"z_min_outside_print": -5})):
        err = None
        try:
            make(opts, steppers)
        except ValueError as exc:
            err = exc
        check("Config zu weit (%s) -> Fehler" % desc, err is not None)
    pr, mod = make(box_cut=-8.2)
    kin = pr.kin
    check("Connect: X min = [k2_box] cut_pos_x -8,2 (Rail + Status axis_minimum)",
          kin.rails[0].position_min == -8.2 and kin.axes_min[0] == -8.2
          and mod.x_min_source == "[k2_box] cut_pos_x")
    check("Connect: Transform gekettet (nächster = Toolhead/bed_mesh)",
          pr.gcode_move.transform is mod.transform
          and mod.transform.next_transform is pr.toolhead)
    pr, mod = make(box_cut=None)
    check("Ohne k2_box: X min aus printer.cfg -7,8",
          pr.kin.rails[0].position_min == -7.8 and mod.x_min_source == "printer.cfg")
    # G-Codes
    pr, mod = make(homed=False)
    err = None
    try:
        pr.gcode.cmds["KTWO_LIMITS_WIDE"](FakeGcmd())
    except CmdError as exc:
        err = exc
    check("KTWO_LIMITS_WIDE ungehomt -> G-Code-Fehler, eng",
          err is not None and not mod.get_status()["wide"])
    pr.kin.set_position(None, "xyz")
    pr.gcode.cmds["KTWO_LIMITS_WIDE"](FakeGcmd({"REASON": "Test"}))
    st = mod.get_status()
    check("KTWO_LIMITS_WIDE gehomt -> weit, Halter gcode",
          st["wide"] and st["holders"] == ["gcode"] and pr.kin.limits[1] == (-6.2, 400.0))
    pr.gcode.cmds["KTWO_LIMITS_RESTORE"](FakeGcmd())
    check("KTWO_LIMITS_RESTORE -> eng", not mod.get_status()["wide"]
          and pr.kin.limits[1] == (-6.2, 352.0))
    for ev in ("gcode:command_error", "stepper_enable:motor_off", "klippy:shutdown"):
        mod.widen("k2_box", "x")
        pr.fire(ev)
        check("Auto-Rücksetzen bei %s" % ev,
              not mod.get_status()["wide"] and pr.kin.rails[1].position_max == 352.0)
    pr.fire("gcode:command_error")
    check("command_error im engen Zustand: keine Meldung/kein Zähler",
          mod.auto_restores == 3)
    gm = pr.gcode.cmds["KTWO_LIMITS_STATUS"]
    g = FakeGcmd()
    gm(g)
    check("KTWO_LIMITS_STATUS meldet Fenster", "eng X -7.80..352.50" in g.infos[0], g.infos)
    check("Namen ohne Ziffern (Upstream-Parser)",
          all(not any(ch.isdigit() for ch in n) for n in pr.gcode.cmds))
    # needs_wide
    pr.toolhead.pos = [200.0, 200.0, 5.0, 0.0]
    check("needs_wide: Schacht ja, Bettfahrt nein",
          mod.needs_wide(["G90", "G0 Y378"]) and not mod.needs_wide(["G0 X100 Y300"]))

    # Z-Wächter
    pr, mod = make()
    t = mod.transform
    pr.toolhead.pos = [200.0, 200.0, 5.0, 0.0]
    err = None
    try:
        t.move([200.0, 200.0, -3.0, 0.0], 10)
    except CmdError as exc:
        err = exc
    check("Z-Wächter: G1 Z-3 außerhalb des Drucks abgelehnt, keine Bewegung, "
          "gcode_move resynchronisiert",
          err is not None and "abgelehnt" in str(err) and pr.toolhead.moves == []
          and pr.gcode_move.resets == 1, repr(err))
    t.move([200.0, 200.0, -2.0, 0.0], 10)
    check("Z-Wächter: Z-2 erlaubt (Stock-Grenze)", pr.toolhead.pos[2] == -2.0)
    pr.ps.state = "printing"
    t.move([200.0, 200.0, -3.0, 0.0], 10)
    check("Z-Wächter: im Druck (printing) keine -2-Ablehnung; Druck-Schutz "
          "hebt Z-3 auf Z2", pr.toolhead.pos[2] == 2.0, pr.toolhead.pos)
    pr, mod = make({"print_low_z_guard": False})
    t = mod.transform
    pr.ps.state = "printing"
    t.move([200.0, 200.0, -3.0, 0.0], 10)
    check("Z-Wächter: im Druck ohne Druck-Schutz nicht aktiv (wie Stock)",
          pr.toolhead.pos[2] == -3.0)
    pr.ps.state = "paused"
    t.move([200.0, 200.0, -2.5, 0.0], 10)
    check("Z-Wächter: aufwärts unter -2 erlaubt (aus -3 nach -2,5)",
          pr.toolhead.pos[2] == -2.5)
    t.move([210.0, 200.0, -2.5, 1.0], 10)
    check("Z-Wächter: XY/E-Fahrt in gleicher Höhe erlaubt", pr.toolhead.pos[0] == 210.0)
    err = None
    try:
        t.move([210.0, 200.0, -2.6, 1.0], 10)
    except CmdError as exc:
        err = exc
    check("Z-Wächter: tiefer unter -2 in der Pause abgelehnt", err is not None)
    pr.kin.clear_homing_state("z")
    pr.toolhead.pos = [200.0, 200.0, 5.0, 0.0]
    err = None
    try:
        t.move([200.0, 200.0, -3.0, 0.0], 10)
    except CmdError as exc:
        err = exc
    check("Z-Wächter: Z ungehomt -> nicht zuständig (Upstream: Must home)",
          err is not None and "Must home" in str(err), repr(err))
    pr, mod = make({"z_guard": False, "print_low_z_guard": False})
    check("z_guard + print_low_z_guard False -> kein Transform",
          mod.transform is None and pr.gcode_move.transform is None)
    pr, mod = make({"z_guard": False})
    pr.toolhead.pos = [200.0, 200.0, 5.0, 0.0]
    mod.transform.move([200.0, 200.0, -3.0, 0.0], 10)
    check("z_guard False, Druck-Schutz an: Transform da, -2-Regel aus",
          mod.transform is not None and pr.toolhead.pos[2] == -3.0)


def _start(pr, t0=100.0):
    pr.ps.state = "printing"
    pr.ps.print_start_time = t0


def _g1(pr, mod, z, x=150.0, y=150.0, e=0.0):
    """gcode_move.cmd_G1: last_position = G-Code + base_position."""
    gm = pr.gcode_move
    newpos = [x + gm.base_position[0], y + gm.base_position[1],
              z + gm.base_position[2], e]
    before = list(newpos)
    mod.transform.move(newpos, 10)
    return newpos == before          # last_position nicht verändert


def _err(fn):
    try:
        fn()
    except CmdError as exc:
        return exc
    return None


def test_druckschutz():
    """Druck-Schutz (Orca change_filament_gcode G1 Z{z_after_toolchange}=0
    vor der ersten Schicht)."""
    pr, mod = make()
    th = pr.toolhead
    _start(pr)
    th.pos = [0.0, 245.0, 0.4, 0.0]
    unchanged = _g1(pr, mod, 0.0, 0.0, 245.0)
    st = mod.get_status()
    hits = [m for m in pr.gcode.infos if "im Druck" in m]
    check("Druck: G1 Z0 -> auf Z2 angehoben, newpos (gcode_move) unverändert",
          th.pos[2] == 2.0 and unchanged and st["low_z_raises"] == 1
          and st["low_z_holding"], (th.pos, st))
    check("Druck: Meldung mit Orca-Hinweis",
          len(hits) == 1 and "G1 Z0.000 im Druck → auf Z2.00 angehoben "
          "(Orca z_after_toolchange=0?)" in hits[0], pr.gcode.infos)
    _g1(pr, mod, 0.0, 100.0, 100.0)            # XY ohne Z (gleiche G-Code-Z)
    _g1(pr, mod, 0.0, 100.0, 100.0, e=1.0)     # E-Fahrt
    _g1(pr, mod, 0.04, 100.0, 100.0)           # knapp unter Schwelle
    check("Druck: Folgefahrten unter Schwelle bleiben auf Z2, Meldung nur "
          "einmal je Druck",
          th.pos[2] == 2.0 and len([m for m in pr.gcode.infos
                                    if "im Druck" in m]) == 1
          and mod.low_z_raises == 4, (th.pos, pr.gcode.infos))
    _g1(pr, mod, 0.2, 110.0, 100.0)
    check("Druck: erste Schicht Z0,2 unverändert, Halten beendet",
          th.pos[2] == 0.2 and not mod.low_z_holding, th.pos)
    _g1(pr, mod, 0.05, 120.0, 100.0)
    check("Druck: Z0,05 (= Schwelle) unverändert", th.pos[2] == 0.05, th.pos)
    _g1(pr, mod, -0.5, 120.0, 100.0)
    check("Druck: Z-0,5 ebenfalls angehoben (keine -2-Ablehnung im Druck)",
          th.pos[2] == 2.0 and pr.gcode_move.resets == 0, th.pos)
    # Neuer Druck -> neue Meldung
    pr.ps.state = "complete"
    _g1(pr, mod, 10.0)
    _start(pr, 200.0)
    _g1(pr, mod, 0.0)
    check("Zweiter Druck: wieder genau eine Meldung",
          len([m for m in pr.gcode.infos if "im Druck" in m]) == 2
          and th.pos[2] == 2.0, pr.gcode.infos)
    # Offsets: Schwelle auf G-Code-Ebene
    pr, mod = make()
    th = pr.toolhead
    _start(pr)
    pr.gcode_move.base_position[2] = -0.1       # SET_GCODE_OFFSET Z=-0.1
    _g1(pr, mod, 0.1)
    check("Offset Z-0,1: G-Code-Z0,1 (Toolhead 0,0) unverändert – Schwelle "
          "G-Code-Ebene", abs(th.pos[2] - 0.0) < 1e-9 and mod.low_z_raises == 0,
          th.pos)
    _g1(pr, mod, 0.0)
    check("Offset Z-0,1: G-Code-Z0 -> G-Code-Z2 (Toolhead 1,9)",
          abs(th.pos[2] - 1.9) < 1e-9, th.pos)
    pr.gcode_move.base_position[2] = 0.3        # SET_GCODE_OFFSET Z=0.3
    _g1(pr, mod, 0.0)
    check("Offset Z+0,3: G-Code-Z0 (Toolhead 0,3) trotzdem angehoben auf 2,3",
          abs(th.pos[2] - 2.3) < 1e-9, th.pos)
    # Außerhalb des Drucks: alte Regel
    pr, mod = make()
    th = pr.toolhead
    th.pos = [150.0, 150.0, 5.0, 0.0]
    _g1(pr, mod, 0.0)
    check("Standby: Z0 unverändert gefahren", th.pos[2] == 0.0
          and mod.low_z_raises == 0)
    _g1(pr, mod, -2.0)
    err = _err(lambda: _g1(pr, mod, -3.0))
    check("Standby: Z-2 erlaubt, Z-3 abgelehnt (alte Regel)",
          th.pos[2] == -2.0 and err is not None and "abgelehnt" in str(err))
    # Pause ohne Halten: kein Eingriff
    pr, mod = make()
    th = pr.toolhead
    _start(pr)
    _g1(pr, mod, 0.3)
    pr.ps.state = "paused"
    _g1(pr, mod, 0.0)
    err = _err(lambda: _g1(pr, mod, -3.0))
    check("Pause (Kopf nicht angehoben): Z0 gefahren, Z-3 abgelehnt (alte "
          "Regel)", th.pos[2] == 0.0 and err is not None
          and mod.low_z_raises == 0, th.pos)
    # Pause WÄHREND des Haltens: RESUME-Rückfahrt bleibt oben
    pr, mod = make()
    th = pr.toolhead
    _start(pr)
    _g1(pr, mod, 0.0)                    # angehoben, hält
    pr.ps.state = "paused"
    _g1(pr, mod, 5.0, 342.5, 176.0)      # PAUSE-Park
    _g1(pr, mod, 0.0, 150.0, 150.0)      # _PI_RESUME_RETURN G1 Z{pause_z}
    check("Pause während Halten: Park Z5, RESUME-Rückfahrt Z0 -> Z2",
          th.pos[2] == 2.0, th.pos)
    pr.ps.state = "printing"
    _g1(pr, mod, 0.2)
    check("Nach RESUME: erste Schicht Z0,2", th.pos[2] == 0.2)
    # Option aus
    pr, mod = make({"print_low_z_guard": False})
    _start(pr)
    _g1(pr, mod, 0.0)
    check("print_low_z_guard False: Z0 im Druck unverändert",
          pr.toolhead.pos[2] == 0.0 and mod.low_z_raises == 0)
    # Eigene Werte + Config-Prüfung
    pr, mod = make({"print_min_z": 0.1, "print_low_z_raise": 5.0})
    _start(pr)
    _g1(pr, mod, 0.08)
    check("print_min_z 0,1 / raise 5: Z0,08 -> Z5", pr.toolhead.pos[2] == 5.0)
    for desc, opts in (("raise <= min", {"print_min_z": 0.5,
                                          "print_low_z_raise": 0.5}),
                       ("print_min_z 2", {"print_min_z": 2.0}),
                       ("raise 30", {"print_low_z_raise": 30.0})):
        err = None
        try:
            make(opts)
        except ValueError as exc:
            err = exc
        check("Druck-Schutz-Config (%s) -> Fehler" % desc, err is not None)
    # Z ungehomt: kein Eingriff (Upstream "Must home" nur bei Z-Bewegung)
    pr, mod = make()
    _start(pr)
    pr.kin.clear_homing_state("z")
    pr.toolhead.pos = [200.0, 200.0, 0.0, 0.0]
    _g1(pr, mod, 0.0, 175.0, 175.0)
    check("Z ungehomt im Druck (START_PRINT vor G28 Z): XY-Fahrt ohne "
          "Anheben, kein Fehler", pr.toolhead.pos[:3] == [175.0, 175.0, 0.0]
          and mod.low_z_raises == 0, pr.toolhead.pos)
    # Status/G-Code
    g = FakeGcmd()
    pr.gcode.cmds["KTWO_LIMITS_STATUS"](g)
    st = mod.get_status()
    check("Status: Druck-Schutz-Felder + KTWO_LIMITS_STATUS",
          st["print_min_z"] == 0.05 and st["print_low_z_raise"] == 2.0
          and st["print_low_z_guard"] is True
          and "im Druck G-Code-Z < 0.05 -> 2.00 (aktiv" in g.infos[0], g.infos)


def test_kette_tempcomp():
    """Kette gcode_move -> k2_limits -> k2_nozzle_tempcomp -> bed_mesh."""
    import test_k2_nozzle_tempcomp as TT
    for order in (("tc", "limits"), ("limits", "tc")):
        pr, m = TT.build(order)
        TT.start_print(pr)
        lim, tc = m["limits"], m["tc"]
        comp = tc._base_comp()
        gm, bm, th = pr.gm, pr.bm, pr.th
        gm.g1(175.0, 175.0, 0.0)
        want = bm.mesh_z(175.0, 175.0, 2.0 + comp * bm.get_z_factor(2.0))
        check("Kette %s: G1 Z0 im Druck -> 2 + tempcomp + Netz (Toolhead "
              "%.5f)" % ("/".join(order), want),
              comp > 0 and abs(th.pos[2] - want) < 1e-9
              and lim.low_z_raises == 1, (th.pos, want, comp))
        gm.g1(176.0, 175.0, 0.2)
        want = bm.mesh_z(176.0, 175.0, 0.2 + comp * bm.get_z_factor(0.2))
        check("Kette %s: Z0,2 nur tempcomp + Netz (k2_limits greift nicht)"
              % "/".join(order),
              abs(th.pos[2] - want) < 1e-9 and lim.low_z_raises == 1
              and abs(gm.pos()[2] - 0.2) < 1e-9, (th.pos, want))


# ---------------------------------------------------------------------
# 5. Makros (jinja2)
# ---------------------------------------------------------------------

def _tb():
    import test_k2_box as TB
    return TB


def _simulate(lines, kin, pos, core):
    """Gerenderte Makrozeilen mit Upstream-Grenzprüfung abfahren.
    -> (Fehlerliste, Endposition). KTWO_LIMITS_WIDE/RESTORE wirken auf core."""
    errs = []
    absolute = True
    pos = list(pos)
    for ln in lines:
        w = ln.split()
        head = w[0].upper()
        if head == "KTWO_LIMITS_WIDE":
            core.widen("gcode")
            continue
        if head == "KTWO_LIMITS_RESTORE":
            core.restore_all()
            continue
        if head == "G90":
            absolute = True
            continue
        if head == "G91":
            absolute = False
            continue
        if head not in ("G0", "G1"):
            continue
        new = list(pos)
        for tok in w[1:]:
            ax = tok[0].upper()
            if ax in "XYZ":
                i = "XYZ".index(ax)
                v = float(tok[1:])
                new[i] = v if absolute else pos[i] + v
        e = kin.move_error(pos, new)
        if e:
            errs.append((ln, e))
        else:
            pos = new
    return errs, pos


def test_makros():
    try:
        import jinja2  # noqa: F401
    except ImportError:
        print("SKIP Makros (jinja2 fehlt)")
        return
    TB = _tb()
    env, macros, kcfg, param = TB._render_env()
    lim_st = {"y_max": 352.0, "x_min": -7.8, "wide": False}
    # _PI_CLEAN_PATH GO / WIPE / LEAVE ab Bettmitte
    pr = TB._rprinter(param, kcfg)
    pr["k2_limits"] = dict(lim_st)
    pr["gcode_move"]["gcode_position"] = {"x": 200.0, "y": 200.0, "z": 5.0, "e": 0}
    go, _ = TB._render(env, macros, "_PI_CLEAN_PATH", pr, {"PART": "GO"})
    pr["gcode_move"]["gcode_position"] = {"x": 133.0, "y": 378.0, "z": 5.0, "e": 0}
    wipe, _ = TB._render(env, macros, "_PI_CLEAN_PATH", pr, {"PART": "WIPE"})
    leave, _ = TB._render(env, macros, "_PI_CLEAN_PATH", pr, {"PART": "LEAVE"})
    check("_PI_CLEAN_PATH GO: WIDE vor Y378, RESTORE am Ende",
          go.index("KTWO_LIMITS_WIDE REASON=Schacht") < next(
              i for i, l in enumerate(go) if l.startswith("G0 Y378"))
          and go[-1] == "KTWO_LIMITS_RESTORE", go)
    check("_PI_CLEAN_PATH WIPE: WIDE zuerst, RESTORE zuletzt",
          wipe[:2] == ["G90", "KTWO_LIMITS_WIDE REASON=Abstreifen"]
          and wipe[-1] == "KTWO_LIMITS_RESTORE", (wipe[:2], wipe[-2:]))
    check("_PI_CLEAN_PATH LEAVE: keine Aufweitung nötig",
          not any("KTWO_LIMITS" in l for l in leave), leave)
    kin = homed_kin()
    core = core_for(kin)
    core.restore_all()
    errs, end = _simulate(go + wipe + leave, kin, [200.0, 200.0, 5.0], core)
    check("Reinigungsbahn GO+WIPE+LEAVE: 0 Move out of range, endet eng",
          not errs and not core.is_wide() and abs(end[1] - 345.0) < 1e-6, errs)
    # Gegenprobe: ohne WIDE scheitert dieselbe Bahn
    kin2 = homed_kin()
    core2 = core_for(kin2)
    core2.restore_all()
    errs2, _ = _simulate([l for l in go + wipe if "KTWO_LIMITS" not in l],
                         kin2, [200.0, 200.0, 5.0], core2)
    check("Gegenprobe: Bahn ohne Aufweitung -> Move out of range", bool(errs2))
    # aus dem Schneidbereich
    pr["gcode_move"]["gcode_position"] = {"x": -7.8, "y": 200.0, "z": 5.0, "e": 0}
    go2, _ = TB._render(env, macros, "_PI_CLEAN_PATH", pr, {"PART": "GO"})
    kin = homed_kin()
    core = core_for(kin)
    core.restore_all()
    errs, _ = _simulate(go2, kin, [-7.8, 200.0, 5.0], core)
    check("Schacht aus dem Schneidbereich (X -7,8): 0 Fehler", not errs, errs)

    # _PI_RESUME_RETURN: Pausenposition im Schacht bzw. auf dem Bett
    def resume(px, py, start):
        p = TB._rprinter(param, kcfg)
        p["k2_limits"] = dict(lim_st)
        p["gcode_macro _PI_PARAM"].update(pause_x=px, pause_y=py, pause_z=10.0)
        p["gcode_move"]["gcode_position"] = {"x": start[0], "y": start[1],
                                             "z": start[2], "e": 0}
        lines, _m = TB._render(env, macros, "_PI_RESUME_RETURN", p)
        k = homed_kin()
        c = core_for(k)
        c.restore_all()
        return lines, _simulate(lines, k, start, c), c
    lines, (errs, end), c = resume(133.0, 378.0, [225.0, 345.0, 20.0])
    check("RESUME-Rückfahrt in den Schacht (Y378): WIDE/RESTORE, 0 Fehler, "
          "endet eng",
          "KTWO_LIMITS_WIDE REASON=RESUME-Schacht" in lines and not errs
          and not c.is_wide() and end[:2] == [133.0, 378.0], (lines, errs))
    lines, (errs, end), c = resume(110.0, 120.0, [225.0, 345.0, 20.0])
    check("RESUME-Rückfahrt aufs Bett: keine Aufweitung, 0 Fehler",
          not any("KTWO_LIMITS" in l for l in lines) and not errs, (lines, errs))
    lines, (errs, end), c = resume(110.0, 120.0, [-7.8, 200.0, 20.0])
    check("RESUME-Rückfahrt aus dem Schneidbereich: 0 Fehler", not errs, errs)
    lines, (errs, end), c = resume(-5.0, 120.0, [225.0, 345.0, 20.0])
    check("RESUME-Rückfahrt zu Ziel X-5 (Druck an der Kante): 0 Fehler", not errs,
          errs)

    # PAUSE aus dem Schacht / Schneidbereich: ohne Aufweitung ins Fenster
    for start in ([133.0, 378.0, 20.0], [-7.8, 200.0, 20.0]):
        p = TB._rprinter(param, kcfg)
        p["pause_resume"]["is_paused"] = False
        p["toolhead"]["axis_maximum"] = {"x": 352.5, "y": 352.0}
        p["toolhead"]["position"] = {"x": start[0], "y": start[1], "z": start[2]}
        p["gcode_move"]["gcode_position"] = {"x": start[0], "y": start[1],
                                             "z": start[2], "e": 0}
        lines, _m = TB._render(env, macros, "PAUSE", p)
        k = homed_kin()
        c = core_for(k)
        c.restore_all()
        errs, end = _simulate(lines, k, start, c)
        check("PAUSE ab %s ohne Aufweitung: 0 Fehler, Park X342,5 Y176" % (start[:2],),
              not errs and end[:2] == [342.5, 176.0], (errs, end))

    # CALIBRATE_CUT_POS (cutcal.cfg)
    cut = TB._macro_sources(os.path.join(_HERE, "config-pi", "cutcal.cfg"))
    p = TB._rprinter(param, kcfg)
    p["k2_motor"] = {"allow_write": True, "homing_active": False,
                     "cut_cal": {"active": False}}
    p["filament_switch_sensor filament_sensor"] = {"enabled": True,
                                                   "filament_detected": False}
    lines, _m = TB._render(env, cut, "CALIBRATE_CUT_POS", p)
    i_w = lines.index("KTWO_LIMITS_WIDE REASON=CALIBRATE_CUT_POS")
    i_c = lines.index("KTWO_MOTOR_CUT_CAL")
    i_r = lines.index("KTWO_LIMITS_RESTORE")
    check("CALIBRATE_CUT_POS: WIDE -> KTWO_MOTOR_CUT_CAL -> M400 -> RESTORE",
          i_w < i_c < i_r and lines[i_c + 1] == "M400", lines)

    # M141/M191 Kammer-Obergrenze ([k2_safety] max_chamber_temp)
    def chamber(name, s, safety=60.0):
        p = TB._rprinter(param, kcfg)
        p["gcode_macro _PI_CHAMBER_MAX"] = {"fallback": 60}
        if safety is not None:
            p["k2_safety"] = {"limits": {"chamber_heater": safety}}
        return TB._render(env, macros, name, p, {"S": str(s)})
    lines, msgs = chamber("M191", 70)
    check("M191 S70 mit k2_safety 60: Ziel 60, TEMPERATURE_WAIT MINIMUM=59",
          "SET_HEATER_TEMPERATURE HEATER=chamber_heater TARGET=60" in lines
          and any(l.endswith("MINIMUM=59") for l in lines)
          and any("begrenzt" in m for m in msgs), (lines, msgs))
    lines, msgs = chamber("M191", 70, safety=None)
    check("M191 S70 ohne k2_safety: Fallback 60", any(l.endswith("MINIMUM=59")
                                                       for l in lines), lines)
    lines, msgs = chamber("M191", 70, safety=55.0)
    check("M191 S70 mit k2_safety 55: Warten auf 54",
          any(l.endswith("MINIMUM=54") for l in lines), lines)
    lines, msgs = chamber("M191", 50)
    check("M191 S50 unverändert (MINIMUM=49, keine Meldung)",
          any(l.endswith("MINIMUM=49") for l in lines) and not msgs, (lines, msgs))
    lines, msgs = chamber("M141", 70)
    check("M141 S70: Ziel 60 + Meldung",
          "SET_HEATER_TEMPERATURE HEATER=chamber_heater TARGET=60" in lines
          and msgs, lines)
    lines, msgs = chamber("M191", 35)
    check("M191 S35 (Kühlbereich) -> M141 S35 unverändert", "M141 S35" in lines, lines)


def test_py39():
    bad = []
    for f in ("k2_limits.py", "test_k2_limits.py"):
        try:
            ast.parse(open(os.path.join(_HERE, f), encoding="utf-8").read(),
                      feature_version=(3, 9))
        except SyntaxError as exc:
            bad.append("%s: %s" % (f, exc))
    check("Python-3.9-Syntax", not bad, bad)


def main():
    for fn in (test_core, test_xy_points, test_fahrwege, test_modul,
               test_druckschutz, test_kette_tempcomp, test_makros, test_py39):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(fn.__name__ + " (Ausnahme)", False, repr(exc))
    fails = [n for n, ok in RESULTS if not ok]
    print("ERGEBNIS: %s (%d/%d Prüfungen ok)" % (
        "ok" if not fails else "%d FEHLER" % len(fails),
        len(RESULTS) - len(fails), len(RESULTS)))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
