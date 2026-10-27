#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_k2_heater_pid.py – SET_HEATER_PID offline (ohne Klipper, ohne Hardware).

Prüft: Werte landen in Klipper-Skala (/255) im Regler, fehlende Parameter
bleiben, I-Zustand wird stoßfrei umgerechnet, Nicht-PID-Regler (z. B. während
PID_CALIBRATE) und unbekannte Heizer werden abgelehnt, SAVE=1 merkt die Werte
für SAVE_CONFIG im richtigen Abschnitt vor.

Zweiter PID-Satz: Umschaltung über den Timer mit Hysterese, Ziel 0 sofort
normal, ohne Hochsatz keine Umschaltung, Autotune-Regler bleibt unberührt,
SET=NORMAL|HIGH (inkl. SAVE-Abschnitt, Anlegen eines fehlenden Hochsatzes),
Ausnahme im Timer ohne Absturz, Config-Prüfung beim Start.
"""
import logging
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import k2_heater_pid as m  # noqa: E402

ok = 0
fehler = []


def check(name, cond):
    global ok
    if cond:
        ok += 1
        print("ok", name)
    else:
        fehler.append(name)
        print("FEHLER", name)


class ConfigError(Exception):
    pass


class GcmdError(Exception):
    pass


class FakeGcmd:
    def __init__(self, **p):
        self.p = p
        self.out = []

    def get(self, k, default=None):
        if k not in self.p:
            if default is None and k == "HEATER":
                raise GcmdError("fehlt " + k)
            return default
        return self.p[k]

    def get_float(self, k, default=None, minval=None):
        v = float(self.p.get(k, default))
        if minval is not None and v < minval:
            raise GcmdError("zu klein " + k)
        return v

    def get_int(self, k, default=None):
        return int(self.p.get(k, default))

    def respond_info(self, msg):
        self.out.append(msg)

    def error(self, msg):
        return GcmdError(msg)


class FakePID:
    def __init__(self, kp, ki, kd, max_power=1.0):
        self.Kp, self.Ki, self.Kd = kp / 255., ki / 255., kd / 255.
        self.heater_max_power = max_power
        self.temp_integ_max = max_power / self.Ki
        self.prev_temp_integ = 0.


class FakeHeater:
    def __init__(self, control):
        self.control = control
        self.lock = threading.Lock()
        self.target = 0.
        self.kaputt = False

    def get_temp(self, eventtime):
        if self.kaputt:
            raise RuntimeError("Sensor weg")
        return 25., self.target


class FakeHeaters:
    def __init__(self, heaters):
        self.heaters = heaters

    def lookup_heater(self, name):
        if name not in self.heaters:
            raise ConfigError("Unknown heater '%s'" % name)
        return self.heaters[name]


class FakeConfigfile:
    def __init__(self):
        self.saved = {}

    def set(self, section, option, value):
        self.saved[(section, option)] = value


class FakeReactor:
    NOW = 0.

    def __init__(self):
        self.timers = []

    def register_timer(self, cb, waketime):
        self.timers.append([cb, waketime])


class FakeGcode:
    def __init__(self):
        self.cmds = {}
        self.out = []

    def register_command(self, name, fn, desc=None):
        self.cmds[name] = fn

    def respond_info(self, msg):
        self.out.append(msg)


class FakePrinter:
    config_error = ConfigError

    def __init__(self, objs):
        self.objs = objs
        self.handlers = {}
        self.reactor = FakeReactor()

    def lookup_object(self, name):
        return self.objs[name]

    def register_event_handler(self, event, cb):
        self.handlers.setdefault(event, []).append(cb)

    def send(self, event):
        for cb in self.handlers.get(event, []):
            cb()

    def get_reactor(self):
        return self.reactor


class FakeConfig:
    error = ConfigError

    def __init__(self, printer, opts=None):
        self.printer = printer
        self.opts = opts or {}

    def get_printer(self):
        return self.printer

    def get_name(self):
        return "k2_heater_pid"

    def get(self, k, default=None):
        return self.opts.get(k, default)

    def getfloat(self, k, default=None, minval=None, above=None):
        if k not in self.opts:
            return default
        v = float(self.opts[k])
        if minval is not None and v < minval:
            raise ConfigError(k)
        if above is not None and v <= above:
            raise ConfigError(k)
        return v


HOCH = {"high_pid_kp": "8", "high_pid_ki": "0.5", "high_pid_kd": "45"}


def aufbau(opts=None, start=False):
    ext = FakeHeater(FakePID(17.015, 1.436, 50.407))
    bed = FakeHeater(FakePID(67.892, 2.647, 435.358))
    gen = FakeHeater(object())                     # z. B. ControlAutoTune
    gcode = FakeGcode()
    cf = FakeConfigfile()
    pr = FakePrinter({"gcode": gcode, "configfile": cf,
                      "heaters": FakeHeaters({"extruder": ext,
                                              "heater_bed": bed,
                                              "kalib": gen})})
    obj = m.load_config(FakeConfig(pr, opts))
    if start:
        pr.send("klippy:connect")
        pr.send("klippy:ready")
    return gcode.cmds["SET_HEATER_PID"], ext, bed, cf, obj, pr, gcode


def fast(a, b):
    return abs(a - b) < 1e-9


def werte(ctl):
    return tuple(round(v * 255., 6) for v in (ctl.Kp, ctl.Ki, ctl.Kd))


def tick(pr, n=1):
    """Timer n-mal laufen lassen (wie der Reactor)."""
    for _ in range(n):
        for t in pr.reactor.timers:
            t[1] = t[0](t[1])


# --- bisherige Tests (ohne Start-Ereignisse, ohne Hochsatz) -----------------
def test_setzen_und_skala():
    cmd, ext, _, cf, _, _, _ = aufbau()
    g = FakeGcmd(HEATER="extruder", KP="14.296", KI="1.271", KD="40.206")
    cmd(g)
    c = ext.control
    check("Kp /255", fast(c.Kp, 14.296 / 255.))
    check("Ki /255", fast(c.Ki, 1.271 / 255.))
    check("Kd /255", fast(c.Kd, 40.206 / 255.))
    check("temp_integ_max neu", fast(c.temp_integ_max, 1.0 / c.Ki))
    check("Meldung", "Kp=14.296 Ki=1.271 Kd=40.206" in g.out[-1]
          and "vorgemerkt" not in g.out[-1])
    check("ohne SAVE nichts vorgemerkt", cf.saved == {})


def test_teilweise():
    cmd, ext, _, _, _, _, _ = aufbau()
    ki0, kd0 = ext.control.Ki, ext.control.Kd
    cmd(FakeGcmd(HEATER="extruder", KP="12"))
    check("nur Kp geändert", fast(ext.control.Kp, 12 / 255.)
          and fast(ext.control.Ki, ki0) and fast(ext.control.Kd, kd0))


def test_integ_stossfrei():
    cmd, ext, _, _, _, _, _ = aufbau()
    c = ext.control
    c.prev_temp_integ = 30.0
    leistung = c.Ki * c.prev_temp_integ
    cmd(FakeGcmd(HEATER="extruder", KI="2.0"))
    check("I-Leistung gleich", fast(c.Ki * c.prev_temp_integ, leistung))
    cmd(FakeGcmd(HEATER="extruder", KI="0"))
    check("Ki 0 -> integ 0", c.prev_temp_integ == 0. and c.temp_integ_max == 0.)


def test_ablehnen():
    cmd, _, _, _, _, _, _ = aufbau()
    for name, g in (("kein PID", FakeGcmd(HEATER="kalib", KP="1")),
                    ("unbekannt", FakeGcmd(HEATER="gibtsnicht", KP="1")),
                    ("negativ", FakeGcmd(HEATER="extruder", KP="-1"))):
        try:
            cmd(g)
            check("abgelehnt: " + name, False)
        except GcmdError:
            check("abgelehnt: " + name, True)


def test_save():
    cmd, _, bed, cf, _, _, _ = aufbau()
    g = FakeGcmd(HEATER="heater_bed", KP="60", KI="2", KD="400", SAVE="1")
    cmd(g)
    check("SAVE Abschnitt heater_bed",
          cf.saved.get(("heater_bed", "pid_Kp")) == "60.000"
          and cf.saved.get(("heater_bed", "pid_Ki")) == "2.000"
          and cf.saved.get(("heater_bed", "pid_Kd")) == "400.000"
          and cf.saved.get(("heater_bed", "control")) == "pid")
    check("SAVE Meldung", "vorgemerkt ([heater_bed])" in g.out[-1])
    check("Abschnitt generic", m.config_section("kammer")
          == "heater_generic kammer" and m.config_section("extruder1")
          == "extruder1")


# --- zweiter PID-Satz -----------------------------------------------------
def test_ohne_hochsatz_keine_umschaltung():
    cmd, ext, _, _, obj, pr, gcode = aufbau(start=True)
    st = obj.get_status(0.)
    check("ohne Hochsatz: Status aus", st["aktiv"] == "aus"
          and st["hoch"] is None and st["normal"] == [17.015, 1.436, 50.407])
    ext.target = 300.
    tick(pr, 3)
    check("ohne Hochsatz: Regler unverändert",
          werte(ext.control) == (17.015, 1.436, 50.407) and gcode.out == [])
    check("Timer-Takt 0,5 s", pr.reactor.timers[0][1] == 1.5)


def test_umschaltung_hysterese():
    cmd, ext, _, _, obj, pr, gcode = aufbau(HOCH, start=True)
    check("Start: normal", obj.get_status(0.)["aktiv"] == "normal")
    ext.control.prev_temp_integ = 40.
    leistung = ext.control.Ki * ext.control.prev_temp_integ
    ext.target = 279.
    tick(pr)
    check("279 < 280: bleibt normal", obj.active == "normal")
    ext.target = 300.
    tick(pr)
    check("300: hoch", obj.active == "hoch"
          and werte(ext.control) == (8., 0.5, 45.))
    check("hoch: I-Leistung stoßfrei",
          fast(ext.control.Ki * ext.control.prev_temp_integ,
               min(leistung, 1.0)))
    check("hoch: Meldung", any("PID-Satz HOCH (Ziel 300 °C ≥ 280)" in o
                               for o in gcode.out))
    ext.target = 275.
    tick(pr)
    check("275 (Hysterese): bleibt hoch", obj.active == "hoch")
    ext.target = 270.
    tick(pr)
    check("270 = Grenze: bleibt hoch", obj.active == "hoch")
    ext.target = 269.
    tick(pr)
    check("269: normal", obj.active == "normal"
          and werte(ext.control) == (17.015, 1.436, 50.407))
    ext.target = 290.
    tick(pr)
    ext.target = 0.
    tick(pr)
    check("Ziel 0: sofort normal", obj.active == "normal"
          and werte(ext.control) == (17.015, 1.436, 50.407))
    st = obj.get_status(0.)
    check("Status-Felder", st["high_temp"] == 280. and st["hoch"]
          == [8., 0.5, 45.] and st["high_heater"] == "extruder")


def test_hysterese_option():
    opts = dict(HOCH, high_temp="250", high_hysteresis="5")
    cmd, ext, _, _, obj, pr, _ = aufbau(opts, start=True)
    ext.target = 250.
    tick(pr)
    ext.target = 246.
    tick(pr)
    check("high_temp 250/Hysterese 5: 246 bleibt hoch", obj.active == "hoch")
    ext.target = 244.
    tick(pr)
    check("244: normal", obj.active == "normal")


def test_autotune_unberuehrt():
    cmd, ext, _, _, obj, pr, _ = aufbau(HOCH, start=True)
    pid = ext.control
    auto = object()
    ext.control = auto                  # PID_CALIBRATE läuft
    ext.target = 300.
    tick(pr, 2)
    check("Autotune: Regler nicht ersetzt/angefasst", ext.control is auto
          and obj.active == "normal")
    ext.control = pid                   # PID_CALIBRATE fertig
    tick(pr)
    check("danach: hoch", obj.active == "hoch"
          and werte(ext.control) == (8., 0.5, 45.))
    ext.control = object()
    try:
        cmd(FakeGcmd(HEATER="extruder", SET="HIGH", KP="9"))
        check("SET während Autotune abgelehnt", False)
    except GcmdError:
        check("SET während Autotune abgelehnt", True)


def test_timer_ausnahme():
    cmd, ext, _, _, obj, pr, gcode = aufbau(HOCH, start=True)
    ext.kaputt = True
    logging.disable(logging.CRITICAL)   # erwartete Fehlermeldung nicht zeigen
    try:
        tick(pr, 2)
        check("Timer-Ausnahme: kein Absturz, Takt läuft weiter",
              pr.reactor.timers[0][1] == 1.0)
    except Exception:
        check("Timer-Ausnahme: kein Absturz, Takt läuft weiter", False)
    logging.disable(logging.NOTSET)
    rot = [z for z in gcode.out if z.startswith("!! [k2_heater_pid]")]
    check("Timer-Ausnahme: einmal rot in der Konsole (entprellt)",
          len(rot) == 1 and "PID-Umschaltung fehlgeschlagen" in rot[0])
    ext.kaputt = False
    ext.target = 300.
    tick(pr)
    check("nach Ausnahme: Umschaltung geht wieder", obj.active == "hoch")


def test_set_high_normal():
    cmd, ext, _, cf, obj, pr, _ = aufbau(HOCH, start=True)
    g = FakeGcmd(HEATER="extruder", SET="high", KP="9", KI="0.6", KD="44")
    cmd(g)
    check("SET=HIGH bei normal: nur gespeichert",
          werte(ext.control) == (17.015, 1.436, 50.407)
          and obj.high_set == (9., 0.6, 44.) and "gespeichert" in g.out[-1])
    ext.target = 300.
    tick(pr)
    check("Umschaltung nimmt neuen Hochsatz", werte(ext.control)
          == (9., 0.6, 44.))
    g = FakeGcmd(HEATER="extruder", SET="NORMAL", KP="10", KI="0.8", KD="40")
    cmd(g)
    check("SET=NORMAL bei hoch: nur gespeichert", werte(ext.control)
          == (9., 0.6, 44.) and obj.normal_set == (10., 0.8, 40.))
    g = FakeGcmd(HEATER="extruder", KD="47")
    cmd(g)
    check("ohne SET bei hoch: ändert aktiven Hochsatz",
          werte(ext.control) == (9., 0.6, 47.) and obj.high_set
          == (9., 0.6, 47.) and obj.normal_set == (10., 0.8, 40.))
    g = FakeGcmd(HEATER="extruder", KD="48", SAVE="1")
    cmd(g)
    check("SAVE ohne SET bei hoch -> [k2_heater_pid] high_pid_*",
          cf.saved.get(("k2_heater_pid", "high_pid_kd")) == "48.000"
          and ("extruder", "pid_Kd") not in cf.saved)
    ext.target = 0.
    tick(pr)
    check("Ziel 0: neuer Normalsatz aktiv", werte(ext.control)
          == (10., 0.8, 40.))
    g = FakeGcmd(HEATER="extruder", SET="HIGH", KP="7", SAVE="1")
    cmd(g)
    check("SET=HIGH SAVE: Abschnitt k2_heater_pid",
          cf.saved.get(("k2_heater_pid", "high_pid_kp")) == "7.000"
          and cf.saved.get(("k2_heater_pid", "high_pid_ki")) == "0.600"
          and "vorgemerkt ([k2_heater_pid])" in g.out[-1])
    g = FakeGcmd(HEATER="extruder", SET="NORMAL", KP="11", SAVE="1")
    cmd(g)
    check("SET=NORMAL SAVE bei normal: aktiv + Abschnitt extruder",
          werte(ext.control) == (11., 0.8, 40.)
          and cf.saved.get(("extruder", "pid_Kp")) == "11.000")


def test_set_ablehnen():
    cmd, ext, bed, _, obj, pr, _ = aufbau(HOCH, start=True)
    for name, g in (("SET am Bett", FakeGcmd(HEATER="heater_bed", SET="HIGH",
                                             KP="1")),
                    ("SET falsch", FakeGcmd(HEATER="extruder", SET="MITTEL",
                                            KP="1"))):
        try:
            cmd(g)
            check("abgelehnt: " + name, False)
        except GcmdError:
            check("abgelehnt: " + name, True)
    check("Bett unverändert", werte(bed.control) == (67.892, 2.647, 435.358))


def test_hochsatz_anlegen():
    cmd, ext, _, _, obj, pr, _ = aufbau(start=True)
    check("vorher aus", obj.get_status(0.)["aktiv"] == "aus")
    g = FakeGcmd(HEATER="extruder", SET="HIGH", KP="8", KI="0.5", KD="45")
    cmd(g)
    check("SET=HIGH legt Hochsatz an, Umschaltung aktiv",
          obj.get_status(0.)["aktiv"] == "normal"
          and obj.high_set == (8., 0.5, 45.)
          and werte(ext.control) == (17.015, 1.436, 50.407))
    ext.target = 285.
    tick(pr)
    check("angelegter Hochsatz schaltet", obj.active == "hoch"
          and werte(ext.control) == (8., 0.5, 45.))


def test_config_pruefung():
    try:
        aufbau({"high_pid_kp": "8"})
        check("teilweiser Hochsatz -> Config-Fehler", False)
    except ConfigError:
        check("teilweiser Hochsatz -> Config-Fehler", True)
    try:
        aufbau(dict(HOCH, high_heater="gibtsnicht"), start=True)
        check("fehlender high_heater mit Hochsatz -> Config-Fehler", False)
    except ConfigError:
        check("fehlender high_heater mit Hochsatz -> Config-Fehler", True)
    _, _, _, _, obj, pr, _ = aufbau({"high_heater": "gibtsnicht"}, start=True)
    check("fehlender high_heater ohne Hochsatz: aus, kein Timer",
          obj.active == "aus" and pr.reactor.timers == [])
    try:
        aufbau(dict(HOCH, high_heater="kalib"), start=True)
        check("high_heater ohne PID -> Config-Fehler", False)
    except ConfigError:
        check("high_heater ohne PID -> Config-Fehler", True)


def test_py39():
    import ast
    for f in ("k2_heater_pid.py", "test_k2_heater_pid.py"):
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), f)
        ast.parse(open(p, encoding="utf-8").read(), feature_version=(3, 9))
    check("Python-3.9-Syntax", True)


TESTS = [test_setzen_und_skala, test_teilweise, test_integ_stossfrei,
         test_ablehnen, test_save, test_ohne_hochsatz_keine_umschaltung,
         test_umschaltung_hysterese, test_hysterese_option,
         test_autotune_unberuehrt, test_timer_ausnahme, test_set_high_normal,
         test_set_ablehnen, test_hochsatz_anlegen, test_config_pruefung,
         test_py39]

if __name__ == "__main__":
    for t in TESTS:
        t()
    print("ERGEBNIS: %s (%d/%d ok)" % ("ok" if not fehler else "FEHLER",
                                      ok, ok + len(fehler)))
    sys.exit(1 if fehler else 0)
