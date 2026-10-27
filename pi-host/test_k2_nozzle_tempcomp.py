#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline-Tests für k2_nozzle_tempcomp.py ([k2_nozzle_tempcomp], L7
18.10.2026), ohne Klipper und ohne Hardware.

1. Formel gegen alle 17 Log-Tupel (Nachrechnung der Stock-Werte): Ausgabe im
   Stock-Format %.3f identisch; Sprung c bei ΔT > 0, 0 bei ΔT ≤ 0, Grenze
   0,181, feste Klemme [0; 0,2].
2. CompCore: Neurechnung nur bei Solländerung, Referenz G28 (lst reset) vs.
   BED_MESH_CALIBRATE (nur ref), COMP_Z bis zur Solländerung (geklemmt),
   Koeffizienten/Werks-Reset, ohne Referenz 0.
3. Modul mit Fake-Klipper: Freigabe (printing/paused, Netz, Referenz,
   ENABLE), Referenz bei homing:home_rails_end nur mit Z, BED_MESH_CALIBRATE
   umhüllt, Fade wie Netz (Upstream get_z_factor) bzw. fade_with_mesh False,
   Transform-Kette mit Fake-bed_mesh (Upstream-Formeln) und ECHTEM
   k2_limits (beide Connect-Reihenfolgen + ohne bed_mesh), Hin-/Rückweg
   get_position, k2_limits-Z-Wächter sieht unkompensiertes Z, G-Code
   KTWO_NOZZLE_TEMPCOMP, Status-Objekt, Config k2-module.cfg.
"""

import ast
import configparser
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import k2_nozzle_tempcomp as T  # noqa: E402
import k2_limits as L           # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print("%s %s %s" % ("PASS" if cond else "FAIL", name,
                        detail if not cond else ""))


def near(a, b, tol=1e-9):
    return abs(a - b) <= tol


# 17 Tupel (target, G28_temp, Log now_comp) aus der Nachrechnung (Live-Log)
LOG_TUPEL = [
    (140.00, 139.43, "0.017"), (220.00, 139.43, "0.050"),
    (235.00, 139.43, "0.064"), (240.00, 139.43, "0.069"),
    (0.00, 139.78, "0.000"), (220.00, 139.78, "0.050"),
    (240.00, 139.78, "0.069"), (0.00, 140.23, "0.000"),
    (140.00, 140.23, "0.000"), (220.00, 140.23, "0.050"),
    (235.00, 140.23, "0.063"), (240.00, 140.23, "0.068"),
    (0.00, 140.49, "0.000"), (140.00, 140.49, "0.000"),
    (220.00, 140.49, "0.050"), (230.00, 140.49, "0.058"),
    (235.00, 140.49, "0.063"),
]


def test_formel():
    check("17 Log-Tupel vorhanden", len(set(LOG_TUPEL)) == 17)
    bad = []
    for t, g, log in LOG_TUPEL:
        v = T.stock_comp(t, g)
        if "%.3f" % v != log:
            bad.append((t, g, log, v))
    check("Formel = Stock-Log (17/17, Format %.3f)", not bad, bad)
    # zusätzlich über CompCore inkl. Logzeile im Stock-Format
    bad = []
    for t, g, log in LOG_TUPEL:
        lines = []
        core = T.CompCore(logger=lines.append)
        core.set_reference(g, "G28 Z")
        core.current(t)
        want = ("get_z_now_comp now_comp:%s target_temp:%.2f G28_temp:%.2f"
                % (log, t, g))
        if not any(want in ln for ln in lines):
            bad.append((want, lines))
    check("CompCore-Logzeile = Stock WHY_DEBUG (17/17)", not bad, bad)
    check("Sprung c knapp über Referenz",
          near(T.stock_comp(140.0, 139.99), T.STOCK_A * 0.01 ** 2 + 0.017316,
               1e-12))
    check("ΔT = 0 -> 0", T.stock_comp(140.0, 140.0) == 0.0)
    check("Grenze 0,181 (ΔT 300)", near(T.stock_comp(440.0, 140.0), 0.181))
    check("Klemme 0,2 fest (max_comp 0,5 -> 0,2)",
          near(T.stock_comp(440.0, 140.0, 1e-3, 0, 0, 0.5), 0.2))
    check("Klemme 0 (negative Koeffizienten)",
          T.stock_comp(220.0, 140.0, -1e-3, 0, -0.1) == 0.0)
    check("Werte Befund §0: 220 °C -> 0,050, 240 °C -> 0,069",
          "%.3f" % T.stock_comp(220, 140) == "0.050"
          and "%.3f" % T.stock_comp(240, 139.43) == "0.069")


def test_core():
    lines = []
    c = T.CompCore(logger=lines.append)
    check("ohne Referenz 0", c.current(220.0) == 0.0)
    c.set_reference(139.43, "G28 Z")
    v = c.current(220.0)
    check("nach G28 neu gerechnet", near(v, T.stock_comp(220, 139.43)))
    n = c.recalcs
    c.current(220.0)
    check("gleiche Solltemperatur -> keine Neurechnung", c.recalcs == n)
    c.current(240.0)
    check("Solländerung -> Neurechnung", c.recalcs == n + 1
          and near(c.now_comp, T.stock_comp(240, 139.43)))
    # BED_MESH_CALIBRATE: nur Referenz, keine Neurechnung (Stock)
    c.set_reference(150.0, "BED_MESH_CALIBRATE", reset_last=False)
    v = c.current(240.0)
    check("Mesh-Referenz: Wert bleibt bis Solländerung (Stock lst_temp)",
          near(v, T.stock_comp(240, 139.43)) and c.ref == 150.0)
    v = c.current(230.0)
    check("danach Solländerung rechnet mit neuer Referenz",
          near(v, T.stock_comp(230, 150.0)))
    # G28: lst reset -> sofort neu
    c.set_reference(140.49, "G28 Z", reset_last=True)
    check("G28 setzt now_comp 0 + erzwingt Neurechnung",
          c.now_comp == 0.0 and c.lst_target is None
          and near(c.current(230.0), T.stock_comp(230, 140.49)))
    # COMP_Z
    c.force(0.5, 230.0)
    check("COMP_Z geklemmt 0,2 + gilt bei gleichem Soll",
          c.current(230.0) == 0.2 and c.forced)
    c.force(-1.0, 230.0)
    check("COMP_Z negativ -> 0", c.current(230.0) == 0.0)
    check("COMP_Z endet bei Solländerung",
          near(c.current(235.0), T.stock_comp(235, 140.49)) and not c.forced)
    # Koeffizienten
    c.set_coeffs(a=1e-5)
    check("A= rechnet sofort neu, B/C bleiben",
          near(c.current(235.0), T.stock_comp(235, 140.49, 1e-5, 0.0,
                                                0.017316))
          and c.c == 0.017316)
    c.max_comp = 0.1
    c.factory_reset()
    check("Werks-Reset a/b/c/max",
          (c.a, c.b, c.c, c.max_comp) == (T.STOCK_A, T.STOCK_B, T.STOCK_C,
                                          T.STOCK_MAX_COMP))
    # invert_fade
    f = (lambda z: 1.0 if z < 5 else (0.0 if z >= 50 else (50 - z) / 45.0))
    bad = []
    for z in (0.2, 4.9, 5.0, 5.05, 20.0, 49.99, 50.0, 80.0):
        zo = z + 0.069 * f(z)
        zi = T.invert_fade(zo, 0.069, f)
        if not near(zi, z, 1e-7):
            bad.append((z, zo, zi))
    check("invert_fade Hin/Rück (0..80 mm)", not bad, bad)


# ---------------------------------------------------------------------
# Fakes (Upstream-Ausschnitte)
# ---------------------------------------------------------------------

class CmdError(Exception):
    pass


class FakeRail:
    def __init__(self, lo, hi):
        self.position_min, self.position_max = lo, hi

    def get_range(self):
        return self.position_min, self.position_max


class FakeKin:
    def __init__(self):
        self.rails = [FakeRail(-7.8, 352.5), FakeRail(-6.2, 352.0),
                      FakeRail(-10.0, 350.2)]
        self.limits = [r.get_range() for r in self.rails]   # gehomt
        self.axes_min = (-7.8, -6.2, -10.0)


class FakeToolhead:
    def __init__(self):
        self.kin = FakeKin()
        self.pos = [175.0, 175.0, 10.0, 0.0]
        self.moves = []

    def get_kinematics(self):
        return self.kin

    def get_position(self):
        return list(self.pos)

    def move(self, newpos, speed):
        self.pos = list(newpos)
        self.moves.append(list(newpos))


class FakeGcodeMove:
    """Upstream gcode_move: move_transform + set_move_transform(force)."""

    def __init__(self, th):
        self.th = th
        self.move_transform = None
        self.resets = 0

    def set_move_transform(self, t, force=False):
        if self.move_transform is not None and not force:
            raise ValueError("G-Code move transform already specified")
        old = self.move_transform or self.th
        self.move_transform = t
        return old

    def _top(self):
        return self.move_transform or self.th

    def g1(self, x, y, z):
        self._top().move([x, y, z, 0.0], 100.0)

    def pos(self):
        return self._top().get_position()

    def reset_last_position(self):
        self.resets += 1

    def get_status(self, eventtime=None):
        p = self.pos()
        return {"gcode_position": p, "position": p,
                "absolute_coordinates": True}


class FakeMesh:
    def calc_z(self, x, y):
        return 0.02 + 0.0004 * x - 0.0002 * y   # 0..0,16 mm, schräg


class FakeBedMesh:
    """Upstream bed_mesh: get_z_factor/get_position wörtlich, move ohne
    Splitter (Endpunkt = z + fade_target + factor·(calc_z − fade_target))."""
    FADE_DISABLE = 0x7FFFFFFF

    def __init__(self, gm, th, fade_start=5.0, fade_end=50.0,
                 fade_target=0.05):
        self.toolhead = th
        self.fade_start, self.fade_end = fade_start, fade_end
        self.fade_dist = fade_end - fade_start
        self.tool_offset = 0.0
        self.fade_target = 0.0
        self.base_fade_target = fade_target
        self.z_mesh = None
        self.last_position = [0.0] * 4
        gm.set_move_transform(self)          # Upstream: im __init__

    def set_mesh(self, mesh):
        self.z_mesh = mesh
        self.fade_target = self.base_fade_target if mesh else 0.0

    def get_mesh(self):
        return self.z_mesh

    def get_z_factor(self, z_pos):
        z_pos += self.tool_offset
        if z_pos >= self.fade_end:
            return 0.
        elif z_pos >= self.fade_start:
            return (self.fade_end - z_pos) / self.fade_dist
        else:
            return 1.

    def get_position(self):
        if self.z_mesh is None:
            self.last_position[:] = self.toolhead.get_position()
            self.last_position[2] -= self.fade_target
        else:
            cur_pos = self.toolhead.get_position()
            x, y, z = cur_pos[:3]
            max_adj = self.z_mesh.calc_z(x, y)
            factor = 1.
            z_adj = max_adj - self.fade_target
            fade_z_pos = z + self.tool_offset
            if min(fade_z_pos, (fade_z_pos - max_adj)) >= self.fade_end:
                factor = 0.
            elif max(fade_z_pos, (fade_z_pos - max_adj)) >= self.fade_start:
                factor = ((self.fade_end + self.fade_target - fade_z_pos) /
                          (self.fade_dist - z_adj))
                factor = max(0., min(1., factor))
            final_z_adj = factor * z_adj + self.fade_target
            self.last_position[:] = [x, y, z - final_z_adj] + cur_pos[3:]
        return list(self.last_position)

    def mesh_z(self, x, y, z):
        """Toolhead-Z, das bed_mesh für G-Code-Z (unter ihm) erzeugt."""
        factor = self.get_z_factor(z)
        if self.z_mesh is None or not factor:
            return z + self.fade_target
        return z + self.fade_target + factor * (
            self.z_mesh.calc_z(x, y) - self.fade_target)

    def move(self, newpos, speed):
        x, y, z = newpos[:3]
        self.toolhead.move([x, y, self.mesh_z(x, y, z)] + list(newpos[3:]),
                           speed)
        self.last_position[:] = newpos


class FakeHeater:
    def __init__(self):
        self.target_temp = 0.0
        self.smoothed_temp = 25.0

    def get_temp(self, eventtime):
        return self.smoothed_temp, self.target_temp


class FakeHeaters:
    def __init__(self, h):
        self.h = h

    def lookup_heater(self, name):
        if name != "extruder":
            raise ValueError("Unknown heater '%s'" % name)
        return self.h


class FakeGcode:
    def __init__(self):
        self.cmds = {}

    def register_command(self, name, fn, desc=None, when_not_ready=False):
        if fn is None:
            return self.cmds.pop(name, None)
        if name in self.cmds:
            raise ValueError("gcode command %s already registered" % name)
        self.cmds[name] = fn

    def respond_info(self, msg):
        pass

    def run(self, name, **params):
        g = FakeGcmd(params)
        self.cmds[name](g)
        return g


class FakeGcmd:
    def __init__(self, params):
        self.params = dict(params)
        self.infos = []

    def get(self, name, default=None):
        return self.params.get(name, default)

    def get_float(self, name, default=None, minval=None, maxval=None):
        if name not in self.params:
            return default
        v = float(self.params[name])
        if (minval is not None and v < minval) or \
                (maxval is not None and v > maxval):
            raise CmdError("%s außerhalb" % name)
        return v

    def get_int(self, name, default=None, minval=None, maxval=None):
        if name not in self.params:
            return default
        v = int(self.params[name])
        if (minval is not None and v < minval) or \
                (maxval is not None and v > maxval):
            raise CmdError("%s außerhalb" % name)
        return v

    def respond_info(self, msg):
        self.infos.append(msg)

    def error(self, msg):
        return CmdError(msg)


class FakePrintStats:
    def __init__(self):
        self.state = "standby"


class FakeSection:
    def __init__(self, opts):
        self.opts = opts

    def getfloat(self, name, default=None, **kw):
        return float(self.opts.get(name, default))


class FakeConfig:
    def __init__(self, printer, opts=None):
        self.printer = printer
        self.opts = dict(opts or {})

    def get_printer(self):
        return self.printer

    def get(self, name, default=None):
        return self.opts.get(name, default)

    def getfloat(self, name, default=None, minval=None, maxval=None, **kw):
        v = float(self.opts.get(name, default))
        if (minval is not None and v < minval) or \
                (maxval is not None and v > maxval):
            raise ValueError("%s außerhalb" % name)
        return v

    def getboolean(self, name, default=None):
        v = self.opts.get(name, default)
        return v if isinstance(v, bool) else str(v) == "True"

    def getsection(self, name):
        st = {"stepper_x": {"position_min": -7.8, "position_max": 352.5},
              "stepper_y": {"position_min": -6.2, "position_max": 352.0},
              "stepper_z": {"position_min": -10.0, "position_max": 350.2}}
        return FakeSection(st[name])

    def error(self, msg):
        return ValueError(msg)


class FakeHomingState:
    def __init__(self, axes):
        self.axes = axes

    def get_axes(self):
        return self.axes


class FakePrinter:
    command_error = CmdError

    def __init__(self, with_mesh=True):
        self.gcode = FakeGcode()
        self.th = FakeToolhead()
        self.gm = FakeGcodeMove(self.th)
        self.heater = FakeHeater()
        self.ps = FakePrintStats()
        self.objects = {"gcode": self.gcode, "toolhead": self.th,
                        "gcode_move": self.gm, "print_stats": self.ps,
                        "heaters": FakeHeaters(self.heater)}
        self.bm = None
        if with_mesh:
            self.bm = FakeBedMesh(self.gm, self.th)
            self.objects["bed_mesh"] = self.bm
            self.mesh_calls = 0

            def mesh_cal(gcmd):
                self.mesh_calls += 1
                self.bm.set_mesh(FakeMesh())
            self.gcode.register_command("BED_MESH_CALIBRATE", mesh_cal)
        self.handlers = {}

    def lookup_object(self, name, default=KeyError):
        if name in self.objects:
            return self.objects[name]
        if default is KeyError:
            raise KeyError(name)
        return default

    def register_event_handler(self, ev, fn):
        self.handlers.setdefault(ev, []).append(fn)

    def fire(self, ev, *args):
        for fn in self.handlers.get(ev, []):
            fn(*args)

    def get_reactor(self):
        class R:
            def monotonic(self):
                return 0.0
        return R()

    def config_error(self, msg):
        return ValueError(msg)


def build(order=("tc", "limits"), with_mesh=True, tc_opts=None,
          limits=True):
    """Lädt Module in der Reihenfolge ``order`` (= Reihenfolge der
    Connect-Handler wie Abschnittsreihenfolge) und feuert klippy:connect."""
    pr = FakePrinter(with_mesh)
    mods = {}
    for name in order:
        if name == "tc":
            mods["tc"] = T.PrinterK2NozzleTempComp(FakeConfig(pr, tc_opts))
            pr.objects["k2_nozzle_tempcomp"] = mods["tc"]
        elif name == "limits" and limits:
            mods["limits"] = L.PrinterK2Limits(FakeConfig(pr, {}))
    pr.fire("klippy:connect")
    return pr, mods


def chain(pr):
    out, node = [], pr.gm.move_transform
    while node is not None:
        out.append(type(node).__name__)
        node = getattr(node, "next_transform", None) \
            if not isinstance(node, FakeBedMesh) else pr.th
        if node is pr.th:
            out.append("Toolhead")
            break
    return out


def start_print(pr, target=220.0, ref=139.43, mesh=True):
    pr.heater.smoothed_temp = ref
    pr.fire("homing:home_rails_end", FakeHomingState([2]), [])
    if mesh:
        pr.bm.set_mesh(FakeMesh())
    pr.heater.target_temp = target
    pr.ps.state = "printing"


def test_kette():
    want = ["ZGuardTransform", "PrinterK2NozzleTempComp", "FakeBedMesh",
            "Toolhead"]
    pr, m = build(("tc", "limits"))
    check("Kette (tc vor limits): limits -> tc -> bed_mesh",
          chain(pr) == want, chain(pr))
    check("Status-Kette dynamisch (inkl. k2_limits oben)",
          m["tc"].chain_text() == "ZGuardTransform -> k2_nozzle_tempcomp -> "
          "FakeBedMesh -> toolhead", m["tc"].chain_text())
    pr2, m2 = build(("limits", "tc"))
    check("Kette (limits vor tc): tc trotzdem direkt über bed_mesh",
          chain(pr2) == want, chain(pr2))
    pr3, m3 = build(("tc",), limits=False)
    check("Kette ohne k2_limits: tc -> bed_mesh",
          chain(pr3) == want[1:], chain(pr3))
    pr4, m4 = build(("tc",), with_mesh=False, limits=False)
    check("ohne bed_mesh: oben auf Toolhead, nie aktiv",
          pr4.gm.move_transform is m4["tc"]
          and m4["tc"].next_transform is pr4.th
          and m4["tc"].release_reason() == "kein Druck")
    pr4.ps.state = "printing"
    pr4.heater.target_temp = 220
    pr4.fire("homing:home_rails_end", FakeHomingState([2]), [])
    pr4.gm.g1(100, 100, 0.3)
    check("ohne bed_mesh im Druck: kein Zuschlag",
          near(pr4.th.pos[2], 0.3)
          and m4["tc"].release_reason() == "kein Netz geladen")

    # Wirkung + Hin/Rück in beiden Reihenfolgen
    for name, (p, mm) in (("tc,limits", (pr, m)), ("limits,tc", (pr2, m2))):
        start_print(p)
        comp = T.stock_comp(220, 139.43)
        bad = []
        for (x, y, z) in ((100, 100, 0.2), (300, 50, 2.0), (175, 175, 5.0),
                          (50, 300, 20.0), (175, 175, 49.9),
                          (175, 175, 50.0), (175, 175, 120.0)):
            p.gm.g1(x, y, z)
            f = p.bm.get_z_factor(z)
            want_z = p.bm.mesh_z(x, y, z + comp * f)
            back = p.gm.pos()[2]
            if not (near(p.th.pos[2], want_z, 1e-9) and near(back, z, 1e-6)):
                bad.append((x, y, z, p.th.pos[2], want_z, back))
        check("Zuschlag mit Fade + get_position zurück (%s)" % name,
              not bad, bad)
    # Erste Schicht: exakt Netz + comp (Faktor 1)
    p = pr
    p.gm.g1(100, 100, 0.2)
    check("Z0,2 im Druck: Toolhead = 0,2 + Netz + 0,0504",
          near(p.th.pos[2], 0.2 + FakeMesh().calc_z(100, 100)
               + T.stock_comp(220, 139.43)))
    # k2_limits-Wächter sieht unkompensiertes Z (pausiert = Wächter aktiv)
    p.ps.state = "paused"
    p.gm.g1(100, 100, 1.0)
    try:
        p.gm.g1(100, 100, -3.0)
        ok = False
    except CmdError as exc:
        ok = "Z-3.000 abgelehnt" in str(exc)
    check("k2_limits lehnt Z-3 in Pause ab (Kette intakt)", ok)
    p.gm.g1(100, 100, -2.0)
    check("Z-2 in Pause erlaubt, Rückweg exakt -2",
          near(p.gm.pos()[2], -2.0, 1e-6))


def test_freigabe():
    pr, m = build(("tc", "limits"))
    tc = m["tc"]
    pr.heater.target_temp = 220.0
    pr.heater.smoothed_temp = 139.43
    pr.gm.g1(100, 100, 10.0)
    check("standby: inaktiv", tc.release_reason() == "kein Druck"
          and near(pr.th.pos[2], pr.bm.mesh_z(100, 100, 10.0)))
    pr.ps.state = "printing"
    check("printing ohne Netz: inaktiv",
          tc.release_reason() == "kein Netz geladen")
    pr.bm.set_mesh(FakeMesh())
    check("printing + Netz ohne Referenz: inaktiv",
          tc.release_reason() == "keine Referenz (G28 Z)")
    pr.fire("homing:home_rails_end", FakeHomingState([0, 1]), [])
    check("Homing nur X/Y setzt keine Referenz", tc.core.ref is None)
    pr.fire("homing:home_rails_end", FakeHomingState([2]), [])
    check("Homing Z: Referenz = smoothed_temp",
          tc.core.ref == 139.43 and tc.core.ref_source == "G28 Z")
    check("printing + Netz + Referenz: aktiv", tc.release_reason() is None)
    st = tc.get_status()
    check("Status comp/target/ref",
          st["active"] and near(st["comp"], T.stock_comp(220, 139.43))
          and st["target"] == 220.0 and st["ref_temperature"] == 139.43)
    pr.ps.state = "paused"
    check("paused: aktiv", tc.release_reason() is None)
    for s in ("complete", "cancelled", "error", "standby"):
        pr.ps.state = s
        if tc.release_reason() != "kein Druck":
            check("Zustand %s inaktiv" % s, False)
            break
    else:
        check("complete/cancelled/error/standby: inaktiv", True)
    pr.ps.state = "printing"
    # Abschaltung zur Laufzeit
    g = pr.gcode.run("KTWO_NOZZLE_TEMPCOMP", ENABLE=0)
    pr.gm.g1(100, 100, 0.2)
    check("ENABLE=0: kein Zuschlag",
          near(pr.th.pos[2], pr.bm.mesh_z(100, 100, 0.2))
          and tc.get_status()["reason"] == "abgeschaltet"
          and "AUS" in g.infos[0])
    pr.gcode.run("KTWO_NOZZLE_TEMPCOMP", ENABLE=1)
    pr.gm.g1(100, 100, 0.2)
    check("ENABLE=1: Zuschlag wieder da",
          near(pr.th.pos[2], pr.bm.mesh_z(100, 100, 0.2 + 0.050418), 1e-5))
    # Config enable: False
    pr2, m2 = build(("tc",), tc_opts={"enable": False}, limits=False)
    start_print(pr2)
    pr2.gm.g1(100, 100, 0.2)
    check("Config enable False: aus",
          near(pr2.th.pos[2], pr2.bm.mesh_z(100, 100, 0.2))
          and m2["tc"].release_reason() == "abgeschaltet")
    # Solländerung im Druck
    pr.heater.target_temp = 240.0
    pr.gm.g1(100, 100, 0.4)
    check("Solländerung 220 -> 240 wirkt bei nächster Bewegung",
          near(pr.th.pos[2], pr.bm.mesh_z(100, 100, 0.4 +
                                           T.stock_comp(240, 139.43))))


def test_mesh_calibrate_und_fade():
    pr, m = build(("tc",), limits=False)
    tc = m["tc"]
    check("BED_MESH_CALIBRATE umhüllt",
          pr.gcode.cmds["BED_MESH_CALIBRATE"] == tc._cmd_mesh_calibrate)
    pr.heater.smoothed_temp = 139.43
    pr.fire("homing:home_rails_end", FakeHomingState([2]), [])
    pr.heater.target_temp = 220.0
    pr.ps.state = "printing"
    pr.gcode.run("BED_MESH_CALIBRATE")
    check("Original BED_MESH_CALIBRATE lief (Netz geladen)",
          pr.mesh_calls == 1 and pr.bm.get_mesh() is not None)
    check("BED_MESH_CALIBRATE: Referenz neu, lst bleibt",
          tc.core.ref == 139.43 and tc.core.ref_source == "BED_MESH_CALIBRATE")
    pr.heater.smoothed_temp = 141.0
    tc.get_status()            # rechnet (220 gegen 139.43)
    pr.gcode.run("BED_MESH_CALIBRATE")
    check("Mesh-Referenz 141 ohne Neurechnung (Stock)",
          tc.core.ref == 141.0
          and near(tc.get_status()["comp"], T.stock_comp(220, 139.43)))
    # Fehler im Original: keine Referenz
    def boom(gcmd):
        raise CmdError("Probe-Fehler")
    tc._orig_mesh_cal = boom
    pr.heater.smoothed_temp = 150.0
    try:
        pr.gcode.run("BED_MESH_CALIBRATE")
    except CmdError:
        pass
    check("BED_MESH_CALIBRATE-Fehler: Referenz unverändert",
          tc.core.ref == 141.0)
    # ref_on_mesh_calibrate False
    pr2, m2 = build(("tc",), tc_opts={"ref_on_mesh_calibrate": False},
                    limits=False)
    check("ref_on_mesh_calibrate False: nicht umhüllt",
          pr2.gcode.cmds["BED_MESH_CALIBRATE"] != m2["tc"]._cmd_mesh_calibrate)
    # fade_with_mesh False: konstanter Zuschlag
    pr3, m3 = build(("tc",), tc_opts={"fade_with_mesh": False}, limits=False)
    start_print(pr3)
    comp = T.stock_comp(220, 139.43)
    bad = []
    for z in (0.2, 20.0, 60.0, 200.0):
        pr3.gm.g1(100, 100, z)
        if not (near(pr3.th.pos[2], pr3.bm.mesh_z(100, 100, z + comp))
                and near(pr3.gm.pos()[2], z, 1e-6)):
            bad.append((z, pr3.th.pos[2]))
    check("fade_with_mesh False: konstant + Rückweg", not bad, bad)
    # fade_with_mesh True (Default): ab 50 mm 0, bei 27,5 mm halb
    pr4, m4 = build(("tc",), limits=False)
    start_print(pr4)
    pr4.gm.g1(100, 100, 27.5)
    check("Fade Mitte (27,5 mm): halber Zuschlag",
          near(m4["tc"].applied, comp * 0.5))
    pr4.gm.g1(100, 100, 50.0)
    check("Fade Ende (50 mm): 0", m4["tc"].applied == 0.0
          and near(pr4.th.pos[2], pr4.bm.mesh_z(100, 100, 50.0)))


def test_gcode():
    pr, m = build(("tc",), limits=False)
    tc = m["tc"]
    start_print(pr, target=220.0, ref=140.0)
    g = pr.gcode.run("KTWO_NOZZLE_TEMPCOMP")
    check("ohne Parameter: nur Status (kein Reset)",
          len(g.infos) == 1 and "gesetzt" not in g.infos[0]
          and "comp 0.050" in g.infos[0])
    pr.gcode.run("KTWO_NOZZLE_TEMPCOMP", A=1e-5, C=0.01)
    check("A/C setzen, B bleibt, sofort neu",
          (tc.core.a, tc.core.b, tc.core.c) == (1e-5, 0.0, 0.01)
          and near(tc.get_status()["comp"], T.stock_comp(220, 140, 1e-5, 0,
                                                         0.01)))
    pr.gcode.run("KTWO_NOZZLE_TEMPCOMP", RESET=1)
    check("RESET=1: Werkswerte",
          (tc.core.a, tc.core.b, tc.core.c) == (T.STOCK_A, T.STOCK_B,
                                                T.STOCK_C))
    pr.gcode.run("KTWO_NOZZLE_TEMPCOMP", COMP_Z=0.9)
    check("COMP_Z 0,9 -> 0,2 (Klemme fest)",
          tc.get_status()["comp"] == 0.2 and tc.get_status()["forced"])
    pr.heater.target_temp = 230.0
    check("COMP_Z endet bei Solländerung",
          near(tc.get_status()["comp"], T.stock_comp(230, 140.0)))
    pr.gcode.run("KTWO_NOZZLE_TEMPCOMP", REF=139.43)
    check("REF= setzt Referenz (G28-Semantik)",
          tc.core.ref == 139.43
          and near(tc.get_status()["comp"], T.stock_comp(230, 139.43)))
    try:
        pr.gcode.run("KTWO_NOZZLE_TEMPCOMP", ENABLE=2)
        ok = False
    except CmdError:
        ok = True
    check("ENABLE=2 abgewiesen", ok)
    try:
        pr.gcode.run("KTWO_NOZZLE_TEMPCOMP", A=float("nan"))
        ok = False
    except CmdError:
        ok = True
    check("A=nan abgewiesen", ok)
    try:
        T.PrinterK2NozzleTempComp(FakeConfig(FakePrinter(),
                                             {"max_comp": 0.3}))
        ok = False
    except ValueError:
        ok = True
    check("Config max_comp > 0,2 abgewiesen", ok)
    keys = {"enabled", "active", "reason", "comp", "applied", "target",
            "ref_temperature", "ref_source", "a", "b", "c", "max_comp",
            "fade_with_mesh", "forced", "recalcs"}
    check("Status-Schlüssel", set(tc.get_status()) == keys)


def test_config():
    p = os.path.join(_HERE, "config-pi", "k2-module.cfg")
    cp = configparser.RawConfigParser(strict=False, inline_comment_prefixes=None)
    cp.optionxform = str
    cp.read(p, encoding="utf-8")
    ok = cp.has_section("k2_nozzle_tempcomp")
    check("k2-module.cfg: [k2_nozzle_tempcomp] vorhanden", ok)
    if not ok:
        return
    s = dict(cp.items("k2_nozzle_tempcomp"))
    check("Config = Stock-Werte, an, Fade wie Netz",
          s.get("enable") == "True" and float(s.get("a")) == T.STOCK_A
          and float(s.get("b")) == T.STOCK_B
          and float(s.get("c")) == T.STOCK_C
          and float(s.get("max_comp")) == T.STOCK_MAX_COMP
          and s.get("fade_with_mesh") == "True", s)
    pr = FakePrinter()
    tc = T.PrinterK2NozzleTempComp(FakeConfig(pr, {
        k: (v == "True" if v in ("True", "False") else v)
        for k, v in s.items()}))
    check("Config ladbar", tc.core.enabled and tc.fade_with_mesh)


def test_py39():
    bad = []
    for f in ("k2_nozzle_tempcomp.py", "test_k2_nozzle_tempcomp.py"):
        try:
            ast.parse(open(os.path.join(_HERE, f), encoding="utf-8").read(),
                      feature_version=(3, 9))
        except SyntaxError as exc:
            bad.append("%s: %s" % (f, exc))
    check("Python-3.9-Syntax", not bad, bad)


def main():
    for fn in (test_formel, test_core, test_kette, test_freigabe,
               test_mesh_calibrate_und_fade, test_gcode, test_config,
               test_py39):
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
