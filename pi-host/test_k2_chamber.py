#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_k2_chamber.py – Offline-Tests für k2_chamber (ohne Klipper-Lauf, ohne
Hardware). L6 (Abgleich mit Creality-klippy), 18.10.2026.

Aufruf: ``cd pi-host && python3 test_k2_chamber.py``  (Python >= 3.9)

Echte Upstream-Teile (Commit 9cb9975, ``lokal/upstream-klipper``
oder ``$KLIPPER_SRC``): ``configfile.ConfigWrapper``, ``gcode.GCodeCommand``,
``extras.heaters.Heater.set_pwm/temperature_callback/set_temp/get_temp``,
``extras.heaters.ControlBangBang`` (watermark, wie die Kammer-Config),
``extras.heaters.PrinterHeaters.set_temperature``,
``extras.pid_calibrate.ControlAutoTune``, ``extras.fan.FanTachometer.
get_status`` (rpm-Formel). Gefälscht: Reactor (Zeit/Timer), PWM-Pin,
Frequenzzähler (liefert die Tacho-Frequenz), heater_fan-Hülle.

Prüft:
 1. Konfig: Defaults, Section in config-pi/printer.cfg (= Defaults, alle
    Optionen verbraucht), ungültige Werte; Verdrahtungsfehler (Heizer,
    Bett, Lüfter fehlt, Lüfter ohne tachometer_pin, Lüfter nicht an die
    Kammerheizung gekoppelt).
 2. Leistung: 50-%-Anlauf (60 s, je Heizphase neu), Bett-Vorrang
    (Toleranz 2 °C, auch bei laufendem Druck), Sperre -> 0, auch für
    PID_CALIBRATE (ControlAutoTune).
 3. Lüfter-Überwachung: 15 s Stillstand bei Ziel > 0 -> Ziel 0 + "!!"-
    Meldung (key519), Sperre hält neue Ziele auf 0, KTWO_CHAMBER_RESET,
    kurzer Aussetzer < 15 s ohne Folgen, Lüfter dreht -> nie Fehler, Ziel 0
    -> keine Zählung, rpm None = Stillstand, fan_fault_action: shutdown.
 4. KTWO_CHAMBER_WAIT: erreicht, Lüfterfehler während des Wartens, Ziel 0,
    TIMEOUT, Sperre vorab, Shutdown.
 5. Config-Texte: [heater_fan chamber_fan] tachometer_pin PC6 (einzige
    Belegung), M141/M191 mit Sperre + KTWO_CHAMBER_WAIT.
 6. Python-3.9-Syntax, keine lokalen Pfade/IPs.
"""

import ast
import configparser
import importlib.util
import logging
import os
import re
import sys
import threading
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_ks = os.environ.get("KLIPPER_SRC", "")
if _ks and os.path.isdir(os.path.join(_ks, "klippy")):
    _ks = os.path.join(_ks, "klippy")
UPSTREAM_KLIPPY = os.path.normpath(_ks or os.path.join(
    _REPO, "lokal", "upstream-klipper", "klippy"))
MODULE_PATH = os.path.join(_HERE, "k2_chamber.py")
README_PATH = os.path.join(_HERE, "README-k2_chamber.md")
PRINTER_CFG = os.path.join(_HERE, "config-pi", "printer.cfg")
MACROS_CFG = os.path.join(_HERE, "config-pi", "macros.cfg")

if not os.path.isfile(os.path.join(UPSTREAM_KLIPPY, "configfile.py")):
    print("SKIP (k2_chamber) Upstream-Klipper nicht gefunden: %s – "
          "KLIPPER_SRC setzen" % UPSTREAM_KLIPPY)
    sys.exit(0)
if UPSTREAM_KLIPPY not in sys.path:
    sys.path.append(UPSTREAM_KLIPPY)
try:
    import serial  # noqa: F401
except ImportError:
    sys.modules["serial"] = types.ModuleType("serial")
import configfile as ucfg                       # noqa: E402
import gcode as ugcode                          # noqa: E402
from extras import heaters as uheaters          # noqa: E402
from extras import pid_calibrate as upid        # noqa: E402
from extras import fan as ufan                  # noqa: E402

spec = importlib.util.spec_from_file_location("k2_chamber", MODULE_PATH)
cm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cm)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("  %s %s%s" % ("OK  " if ok else "FAIL", name,
                         ("  [" + str(detail) + "]") if detail and not ok
                         else ""))


def raises(fn, exc=Exception, text=None):
    try:
        fn()
    except exc as e:
        return text is None or text in str(e)
    return False


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeReactor:
    NOW = 0.
    NEVER = 9999999999999999.

    def __init__(self):
        self.t = 100.
        self.timers = []
        self.on_pause = None

    def monotonic(self):
        return self.t

    def register_timer(self, cb, waketime=NEVER):
        tm = [cb, waketime]
        self.timers.append(tm)
        return tm

    def update_timer(self, tm, waketime):
        tm[1] = waketime

    def run_until(self, t):
        while True:
            due = [tm for tm in self.timers if tm[1] <= t]
            if not due:
                break
            tm = min(due, key=lambda x: x[1])
            self.t = max(self.t, tm[1])
            tm[1] = tm[0](self.t)
        self.t = max(self.t, t)

    def pause(self, waketime):
        if waketime > 100. + 3600.:
            raise RuntimeError("Test-Reactor: Warten > 1 h (Endlosschleife)")
        if self.on_pause is not None:
            self.on_pause(waketime)
        self.run_until(waketime)
        return self.t


class FakeGcode:
    def __init__(self):
        self.commands = {}
        self.info = []
        self.raw = []

    def register_command(self, name, func, when_not_ready=False, desc=None):
        self.commands[name] = func

    def respond_info(self, msg, log=True):
        self.info.append(msg)

    def respond_raw(self, msg):
        self.raw.append(msg)

    def cmd(self, line):
        parts = line.split()
        params = {}
        for p in parts[1:]:
            k, v = p.split("=", 1)
            params[k.upper()] = v
        return ugcode.GCodeCommand(self, parts[0].upper(), line, params,
                                   False)

    def run(self, line):
        gc = self.cmd(line)
        return self.commands[gc.get_command()](gc)


class FakeToolhead:
    def register_lookahead_callback(self, cb):
        cb(0.)

    def get_last_move_time(self):
        return 0.


class FakePrinter:
    command_error = ugcode.CommandError
    config_error = ucfg.error

    def __init__(self):
        self.reactor = FakeReactor()
        self.gcode = FakeGcode()
        self.objects = {"gcode": self.gcode, "toolhead": FakeToolhead()}
        self.events = {}
        self.shutdowns = []

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=configparser.Error):
        if name in self.objects:
            return self.objects[name]
        if default is configparser.Error:
            raise self.config_error("Unknown config object '%s'" % name)
        return default

    def register_event_handler(self, ev, cb):
        self.events.setdefault(ev, []).append(cb)

    def send_event(self, ev):
        for cb in self.events.get(ev, []):
            cb()

    def invoke_shutdown(self, msg, details={}):
        self.shutdowns.append(msg)
        self.send_event("klippy:shutdown")

    def is_shutdown(self):
        return bool(self.shutdowns)

    def get_start_args(self):
        return {}


class FakeMCU:
    def estimated_print_time(self, eventtime):
        return eventtime


class FakePWM:
    def __init__(self):
        self.values = []

    def set_pwm(self, t, v):
        self.values.append((t, v))

    def get_mcu(self):
        return FakeMCU()


def make_heater(printer, name, max_temp, control="watermark"):
    """Echter Upstream-Heater (ohne Sensor/Pin-Setup) + echte Regelung."""
    h = uheaters.Heater.__new__(uheaters.Heater)
    h.printer = printer
    h.name = h.short_name = name
    h.min_temp, h.max_temp = -30., max_temp
    h.lock = threading.Lock()
    h.target_temp = h.last_temp = h.smoothed_temp = 0.
    h.last_temp_time = 0.
    h.max_power = 1.
    h.min_pwm_change = h.max_power * uheaters.MIN_PWM_CHANGE_RATIO
    h.smooth_time = 1.
    h.inv_smooth_time = 1.
    h.min_extrude_temp = 170.
    h.can_extrude = False
    h.pwm_delay = 0.3
    h.next_pwm_time = 0.
    h.last_pwm_value = 0.
    h.verify_mainthread_time = 1e12
    h.mcu_pwm = FakePWM()
    fc = configparser.RawConfigParser()
    fc.read_string("[x]\nmax_delta: 2.0\n")
    h.control = uheaters.ControlBangBang(
        h, ucfg.ConfigWrapper(printer, fc, {}, "x"))
    return h


class FakeFreq:
    def __init__(self):
        self.freq = 0.

    def get_frequency(self):
        return self.freq


def make_heater_fan(heater_names=("chamber_heater",), tach=True):
    fan = types.SimpleNamespace()
    t = ufan.FanTachometer.__new__(ufan.FanTachometer)
    t._freq_counter = FakeFreq() if tach else None
    t.ppr = 2
    fan.tachometer = t
    hf = types.SimpleNamespace(fan=fan, heater_names=list(heater_names),
                               last_speed=0.)
    return hf


class FakePHeaters:
    set_temperature = uheaters.PrinterHeaters.set_temperature

    def __init__(self, printer):
        self.printer = printer
        self.heaters = {}
        self.has_started = True
        self.gcode_id_to_sensor = {}

    def _get_temp(self, eventtime):
        return "T:0"


class Rig:
    def __init__(self, extra="", fan_kw=None, no_bed=False, no_fan=False):
        self.printer = p = FakePrinter()
        self.ph = FakePHeaters(p)
        p.objects["heaters"] = self.ph
        self.cham = make_heater(p, "chamber_heater", 80.)
        self.ph.heaters["chamber_heater"] = self.cham
        if not no_bed:
            self.bed = make_heater(p, "heater_bed", 120.)
            self.ph.heaters["heater_bed"] = self.bed
        self.hf = make_heater_fan(**(fan_kw or {}))
        if not no_fan:
            p.objects["heater_fan chamber_fan"] = self.hf
        self.cfg = make_cfg(extra, p)

    def build(self, connect=True, ready=True):
        self.mod = cm.load_config(self.cfg)
        if connect:
            self.printer.send_event("klippy:connect")
        if ready:
            self.printer.send_event("klippy:ready")
        return self.mod

    def rpm(self, rpm):
        # rpm = freq * 30 / ppr  -> freq = rpm * ppr / 30
        self.hf.fan.tachometer._freq_counter.freq = rpm * 2 / 30.

    def temp(self, heater, temp, t=None):
        """Temperaturmeldung wie der ADC-Callback (Regelung + set_pwm)."""
        if t is None:
            t = self.printer.reactor.t
        heater.temperature_callback(t, temp)
        heater.smoothed_temp = temp
        return heater.last_pwm_value


def make_cfg(text, printer, section="k2_chamber"):
    fc = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(
        "#", ";"))
    fc.read_string("[%s]\n%s" % (section, text))
    return ucfg.ConfigWrapper(printer, fc, {}, section)


# ---------------------------------------------------------------------------
# 1. Konfig
# ---------------------------------------------------------------------------

def test_config():
    m = Rig().build()
    check("1 Defaults (15 s, 100 rpm, heater_off, Bett 2 °C, 60 s/50 %)",
          (m.fan_fault_time, m.min_rpm, m.action, m.bed_priority,
           m.bed_tolerance, m.soft_start_time, m.soft_start_power,
           m.heater_name, m.fan_name, m.bed_name) ==
          (15., 100., "heater_off", True, 2., 60., .5, "chamber_heater",
           "heater_fan chamber_fan", "heater_bed"))
    fc = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(
        "#", ";"))
    with open(PRINTER_CFG, encoding="utf-8") as f:
        fc.read_file(f)
    check("1 printer.cfg hat [k2_chamber]", fc.has_section("k2_chamber"))
    r = Rig()
    w = ucfg.ConfigWrapper(r.printer, fc, {}, "k2_chamber")
    r.cfg = w
    m2 = r.build()
    check("1 printer.cfg: Werte = Defaults",
          (m2.fan_fault_time, m2.min_rpm, m2.action, m2.bed_priority,
           m2.bed_tolerance, m2.soft_start_time, m2.soft_start_power,
           m2.heater_name, m2.fan_name, m2.bed_name) ==
          (m.fan_fault_time, m.min_rpm, m.action, m.bed_priority,
           m.bed_tolerance, m.soft_start_time, m.soft_start_power,
           m.heater_name, m.fan_name, m.bed_name))
    used = set(o for (sec, o) in w.access_tracking if sec == "k2_chamber")
    check("1 printer.cfg: alle Optionen verbraucht",
          set(fc.options("k2_chamber")) <= used,
          set(fc.options("k2_chamber")) - used)
    check("1 fan_fault_action ungültig -> Config-Fehler", raises(
        lambda: Rig("fan_fault_action: egal").build(), ucfg.error))
    check("1 fan_fault_time 1 (< 2) -> Config-Fehler", raises(
        lambda: Rig("fan_fault_time: 1").build(), ucfg.error))
    check("1 fan_fault_time 120 (> 60) -> Config-Fehler", raises(
        lambda: Rig("fan_fault_time: 120").build(), ucfg.error))
    check("1 soft_start_power 0 -> Config-Fehler", raises(
        lambda: Rig("soft_start_power: 0").build(), ucfg.error))
    check("1 Heizer fehlt -> Config-Fehler", raises(
        lambda: Rig("heater: gibtsnicht").build(), ucfg.error, "gibtsnicht"))
    check("1 Bett fehlt -> Config-Fehler", raises(
        lambda: Rig(no_bed=True).build(), ucfg.error, "heater_bed"))
    m3 = Rig("bed_priority: False", no_bed=True).build()
    check("1 bed_priority: False ohne Bett erlaubt", m3.bed is None)
    check("1 Lüfter fehlt -> Config-Fehler", raises(
        lambda: Rig(no_fan=True).build(), ucfg.error, "nicht gefunden"))
    check("1 Lüfter ohne tachometer_pin -> Config-Fehler", raises(
        lambda: Rig(fan_kw={"tach": False}).build(), ucfg.error,
        "tachometer_pin"))
    check("1 Lüfter nicht an chamber_heater gekoppelt -> Config-Fehler",
          raises(lambda: Rig(fan_kw={"heater_names": ("extruder",)}).build(),
                 ucfg.error, "chamber_heater"))
    r4 = Rig()
    m4 = r4.build()
    r4.printer.send_event("klippy:connect")
    check("1 kein Doppel-Wrap bei zweitem connect",
          r4.cham.set_pwm.__closure__ is not None
          and getattr(r4.cham, "_k2_chamber_wrapped", False) and m4)
    check("1 Befehle registriert",
          {"KTWO_CHAMBER_STATUS", "KTWO_CHAMBER_RESET",
           "KTWO_CHAMBER_WAIT"} <= set(r4.printer.gcode.commands))


# ---------------------------------------------------------------------------
# 2. Leistung
# ---------------------------------------------------------------------------

def test_power():
    r = Rig()
    m = r.build()
    r.rpm(3000)
    r.cham.set_temp(50.)
    t0 = r.printer.reactor.t
    p1 = r.temp(r.cham, 20., t0)
    check("2 Anlauf: erste Heizphase 50 % (Stock temp_coff 0.5)", p1 == .5,
          p1)
    check("2 Status soft_start", m.get_status(t0)["soft_start"] is True)
    p2 = r.temp(r.cham, 25., t0 + 59.)
    check("2 Anlauf nach 59 s noch 50 %", p2 == .5, p2)
    p3 = r.temp(r.cham, 30., t0 + 60.5)
    check("2 nach 60 s volle Leistung", p3 == 1., p3)
    # Watermark aus (>= Ziel + 2) -> 0, neue Heizphase wieder 50 %
    p4 = r.temp(r.cham, 52.5, t0 + 70.)
    check("2 Watermark aus -> 0", p4 == 0., p4)
    p5 = r.temp(r.cham, 47.5, t0 + 80.)
    check("2 neue Heizphase -> wieder 50 % (Stock start_heating_seconds=0)",
          p5 == .5, p5)
    # Bett-Vorrang
    r2 = Rig()
    m2 = r2.build()
    r2.bed.target_temp = 100.
    r2.bed.smoothed_temp = 60.
    r2.cham.set_temp(50.)
    t = r2.printer.reactor.t
    q1 = r2.temp(r2.cham, 20., t)
    check("2 Bett heizt (60/100) -> PTC 0 (Stock heater_bed_state==1)",
          q1 == 0., q1)
    check("2 Status bed_blocking", m2.get_status(t)["bed_blocking"] is True)
    r2.bed.smoothed_temp = 97.9
    q2 = r2.temp(r2.cham, 20., t + 10.)
    check("2 Bett 97.9 (> 2 °C unter Ziel) -> weiter 0", q2 == 0., q2)
    r2.bed.smoothed_temp = 98.1
    q3 = r2.temp(r2.cham, 20., t + 20.)
    check("2 Bett 98.1 (innerhalb 2 °C) -> PTC startet mit 50 %",
          q3 == .5, q3)
    r2.bed.smoothed_temp = 105.
    q4 = r2.temp(r2.cham, 20., t + 30.)
    check("2 Bett über Ziel -> kein Vorrang (Stock state 2)", q4 == .5, q4)
    r2.bed.smoothed_temp = 90.
    q5 = r2.temp(r2.cham, 20., t + 40.)
    check("2 Bett fällt im Druck > 2 °C ab -> PTC wieder 0 (Stock "
          "fortlaufend)", q5 == 0., q5)
    r2.bed.target_temp = 0.
    q6 = r2.temp(r2.cham, 20., t + 50.)
    check("2 Bett-Ziel 0 -> kein Vorrang, Anlauf neu 50 %", q6 == .5, q6)
    r3 = Rig("bed_priority: False")
    r3.build()
    r3.bed.target_temp = 100.
    r3.cham.set_temp(50.)
    q7 = r3.temp(r3.cham, 20.)
    check("2 bed_priority: False -> PTC heizt trotz Bett", q7 == .5, q7)
    r5 = Rig("soft_start_time: 0")
    r5.build()
    r5.cham.set_temp(50.)
    q8 = r5.temp(r5.cham, 20.)
    check("2 soft_start_time 0 -> sofort volle Leistung", q8 == 1., q8)
    # Sperre -> 0, auch unter PID_CALIBRATE (ControlAutoTune)
    r6 = Rig()
    m6 = r6.build()
    m6.fault = True
    r6.cham.set_temp(50.)
    q9 = r6.temp(r6.cham, 20.)
    check("2 Sperre -> 0 trotz Ziel", q9 == 0., q9)
    at = upid.ControlAutoTune(r6.cham, 50.)
    old = r6.cham.set_control(at)
    r6.cham.set_temp(50.)
    at.temperature_update(r6.printer.reactor.t + 1., 20., 50.)
    check("2 Sperre wirkt auch unter PID_CALIBRATE (ControlAutoTune)",
          r6.cham.last_pwm_value == 0. and at.last_pwm == 1.,
          (r6.cham.last_pwm_value, at.last_pwm))
    m6.fault = False
    r6.cham.set_control(old)
    r6.cham.set_temp(50.)
    q10 = r6.temp(r6.cham, 20., r6.printer.reactor.t + 2.)
    check("2 nach Reset wieder Anlauf 50 %", q10 == .5, q10)
    check("2 Pin-Wert = gefilterter Wert",
          r6.cham.mcu_pwm.values[-1][1] == .5, r6.cham.mcu_pwm.values[-1:])


# ---------------------------------------------------------------------------
# 3. Lüfter-Überwachung
# ---------------------------------------------------------------------------

def test_fan_monitor():
    r = Rig()
    m = r.build()
    rc = r.printer.reactor
    r.rpm(0.)
    r.cham.set_temp(50.)
    t0 = rc.t
    rc.run_until(t0 + 14.5)
    check("3 Stillstand 13,5 s -> noch kein Fehler", not m.fault
          and r.cham.target_temp == 50.)
    rc.run_until(t0 + 16.5)
    check("3 Stillstand >= 15 s -> Fehler, Ziel 0 (Stock M141 S0)",
          m.fault and r.cham.target_temp == 0., (m.fault, r.cham.target_temp))
    check("3 Meldung als Fehler '!!' mit key519",
          any(x.startswith("!! [k2_chamber] PTC-Lüfter steht") and "key519"
              in x for x in r.printer.gcode.raw), r.printer.gcode.raw)
    check("3 kein Shutdown bei heater_off", not r.printer.shutdowns)
    check("3 Leistung nach Fehler 0", r.temp(r.cham, 20.) == 0.)
    # Sperre: neues Ziel wird wieder 0
    n = len(r.printer.gcode.raw)
    r.cham.set_temp(55.)
    rc.run_until(rc.t + 1.5)
    check("3 Sperre: neues Ziel -> sofort wieder 0 + Meldung",
          r.cham.target_temp == 0. and len(r.printer.gcode.raw) > n
          and "gesperrt" in r.printer.gcode.raw[-1])
    r.printer.gcode.run("KTWO_CHAMBER_STATUS")
    check("3 STATUS zeigt Fehler", "Fehler=True" in r.printer.gcode.info[-1],
          r.printer.gcode.info[-1:])
    r.printer.gcode.run("KTWO_CHAMBER_RESET")
    check("3 RESET hebt Sperre auf", not m.fault
          and "aufgehoben" in r.printer.gcode.info[-1])
    r.rpm(3000.)
    r.cham.set_temp(50.)
    rc.run_until(rc.t + 60.)
    check("3 Lüfter dreht -> kein Fehler, Ziel bleibt",
          not m.fault and r.cham.target_temp == 50.)
    check("3 Status rpm 3000", abs(m.get_status(rc.t)["rpm"] - 3000.) < 1e-6,
          m.get_status(rc.t)["rpm"])
    # kurzer Aussetzer
    r.rpm(50.)
    rc.run_until(rc.t + 10.)
    r.rpm(3000.)
    rc.run_until(rc.t + 2.)
    r.rpm(50.)
    rc.run_until(rc.t + 10.)
    check("3 zwei Aussetzer je 10 s (< 15 s, unterbrochen) -> kein Fehler",
          not m.fault)
    rc.run_until(rc.t + 6.)
    check("3 Aussetzer läuft weiter auf >= 15 s -> Fehler", m.fault)
    # Ziel 0 und PWM 0 -> keine Zählung
    r2 = Rig()
    m2 = r2.build()
    r2.rpm(0.)
    r2.printer.reactor.run_until(r2.printer.reactor.t + 100.)
    check("3 Ziel 0 / PWM 0 -> Stillstand egal", not m2.fault)
    # PWM > 0 ohne Ziel (Nachlauf) zählt
    r2.cham.last_pwm_value = .5
    r2.printer.reactor.run_until(r2.printer.reactor.t + 17.)
    check("3 PWM > 0 bei Ziel 0 zählt ebenfalls", m2.fault)
    # rpm None = Stillstand
    r3 = Rig()
    m3 = r3.build()
    r3.hf.fan.tachometer.get_status = lambda e: {"rpm": None}
    r3.cham.set_temp(50.)
    r3.printer.reactor.run_until(r3.printer.reactor.t + 17.)
    check("3 rpm None (kein Messwert) -> Fehler (fail-safe)", m3.fault)
    # Shutdown-Variante
    r4 = Rig("fan_fault_action: shutdown")
    m4 = r4.build()
    r4.rpm(0.)
    r4.cham.set_temp(50.)
    r4.printer.reactor.run_until(r4.printer.reactor.t + 17.)
    check("3 shutdown: Ziel 0 + invoke_shutdown mit Meldung",
          m4.fault and r4.cham.target_temp == 0. and len(r4.printer.shutdowns)
          == 1 and "PTC-Lüfter steht" in r4.printer.shutdowns[0],
          r4.printer.shutdowns)
    tm = r4.printer.reactor.timers[-1]
    check("3 shutdown: Timer gestoppt", tm[1] == FakeReactor.NEVER)
    # min_rpm 0 -> rpm 0 ist "Stillstand"? (0 < 0 falsch) -> nie Fehler
    r5 = Rig("min_rpm: 0")
    m5 = r5.build()
    r5.rpm(0.)
    r5.cham.set_temp(50.)
    r5.printer.reactor.run_until(r5.printer.reactor.t + 30.)
    check("3 min_rpm 0 schaltet Erkennung praktisch ab (dokumentiert)",
          not m5.fault)
    # Konsolen-Audit 23.10.2026: Drehzahl-Ausnahme -> // einmal (entprellt)
    r7 = Rig()
    m7 = r7.build()

    def kaputt(e):
        raise RuntimeError("Tacho weg")
    r7.hf.fan.tachometer.get_status = kaputt
    logging.disable(logging.CRITICAL)
    r7.printer.reactor.run_until(r7.printer.reactor.t + 30.)
    logging.disable(logging.NOTSET)
    warn = [x for x in r7.printer.gcode.raw
            if x.startswith("// [k2_chamber] PTC-Lüfter-Drehzahl nicht lesbar")]
    check("3 Drehzahl-Ausnahme -> eine //-Warnung (entprellt)", len(warn) == 1,
          r7.printer.gcode.raw)
    # unerwartete Ausnahme im Timer -> kein Wurf, Sperre + !! (fail-safe)
    r8 = Rig()
    m8 = r8.build()
    r8.cham.set_temp(50.)

    def boom(e):
        raise ValueError("Testfehler")
    m8._check = boom
    logging.disable(logging.CRITICAL)
    try:
        r8.printer.reactor.run_until(r8.printer.reactor.t + 10.)
        geworfen = False
    except Exception:
        geworfen = True
    logging.disable(logging.NOTSET)
    rot = [x for x in r8.printer.gcode.raw
           if x.startswith("!! [k2_chamber] Lüfter-Überwachung fehlgeschlagen")]
    check("3 Timer-Ausnahme: kein Wurf, Sperre, Ziel 0, einmal !!",
          not geworfen and m8.fault and r8.cham.target_temp == 0.
          and len(rot) == 1 and not r8.printer.shutdowns, r8.printer.gcode.raw)


# ---------------------------------------------------------------------------
# 4. KTWO_CHAMBER_WAIT
# ---------------------------------------------------------------------------

def test_wait():
    r = Rig()
    m = r.build()
    r.rpm(3000.)
    r.cham.set_temp(50.)
    temps = iter([30., 40., 49.5])

    def on_pause(wt):
        try:
            r.temp(r.cham, next(temps), wt)
            r.cham.last_temp_time = wt
        except StopIteration:
            pass
    r.printer.reactor.on_pause = on_pause
    r.cham.last_temp_time = r.printer.reactor.t
    r.printer.gcode.run("KTWO_CHAMBER_WAIT MINIMUM=49")
    check("4 Temperatur erreicht -> Rückkehr", r.cham.smoothed_temp >= 49.)
    # Lüfterfehler während des Wartens
    r2 = Rig("fan_fault_time: 3")
    r2.build()
    r2.rpm(0.)
    r2.cham.set_temp(50.)
    check("4 Lüfterfehler im Warten -> Fehler statt endlos",
          raises(lambda: r2.printer.gcode.run("KTWO_CHAMBER_WAIT MINIMUM=49"),
                 ugcode.CommandError, "PTC-Lüfter steht"))
    check("4 danach Ziel 0", r2.cham.target_temp == 0.)
    check("4 Sperre vorab -> sofort Fehler",
          raises(lambda: r2.printer.gcode.run("KTWO_CHAMBER_WAIT MINIMUM=49"),
                 ugcode.CommandError, "gesperrt"))
    r3 = Rig()
    r3.build()
    check("4 Ziel 0 -> Fehler",
          raises(lambda: r3.printer.gcode.run("KTWO_CHAMBER_WAIT MINIMUM=49"),
                 ugcode.CommandError, "Ziel"))
    r3.rpm(3000.)
    r3.cham.set_temp(50.)
    check("4 TIMEOUT -> Fehler",
          raises(lambda: r3.printer.gcode.run(
              "KTWO_CHAMBER_WAIT MINIMUM=49 TIMEOUT=5"),
              ugcode.CommandError, "nicht in 5 s"))
    r4 = Rig()
    r4.build()
    r4.rpm(3000.)
    r4.cham.set_temp(50.)
    r4.printer.reactor.on_pause = lambda wt: r4.printer.invoke_shutdown("x")
    check("4 Shutdown im Warten -> Fehler",
          raises(lambda: r4.printer.gcode.run("KTWO_CHAMBER_WAIT MINIMUM=49"),
                 ugcode.CommandError, "Shutdown"))
    check("4 MINIMUM Pflicht", raises(
        lambda: r4.printer.gcode.run("KTWO_CHAMBER_WAIT"),
        ugcode.CommandError))
    check("4 m genutzt", m is not None)


# ---------------------------------------------------------------------------
# 5. Config-Texte
# ---------------------------------------------------------------------------

def test_cfg_text():
    fc = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(
        "#", ";"))
    with open(PRINTER_CFG, encoding="utf-8") as f:
        src = f.read()
    fc.read_string(src)
    sec = "heater_fan chamber_fan"
    check("5 heater_fan chamber_fan: tachometer_pin PC6 (Stock fan0_pin, "
          "ohne Pull-up)", fc.get(sec, "tachometer_pin") == "PC6")
    check("5 heater_fan chamber_fan: heater chamber_heater",
          fc.get(sec, "heater").strip() == "chamber_heater")
    check("5 heater_fan chamber_fan: fan_speed 1.0",
          float(fc.get(sec, "fan_speed")) == 1.)
    uses = [(s, o) for s in fc.sections() for o, v in fc.items(s)
            if re.search(r"(^|[\s,!^~:])PC6\b", v)]
    check("5 PC6 nur einmal belegt", uses == [(sec, "tachometer_pin")], uses)
    with open(MACROS_CFG, encoding="utf-8") as f:
        mac = f.read()
    for name in ("M141", "M191"):
        mm = re.search(r"\[gcode_macro %s\]\n(.*?)(?=\n\[)" % name, mac, re.S)
        body = mm.group(1) if mm else ""
        check("5 %s weist S>40 bei Sperre ab" % name,
              "kc.fault" in body and "action_raise_error" in body)
    mm = re.search(r"\[gcode_macro M191\]\n(.*?)(?=\n\[)", mac, re.S)
    check("5 M191 wartet über KTWO_CHAMBER_WAIT (Fallback TEMPERATURE_WAIT)",
          mm and "KTWO_CHAMBER_WAIT MINIMUM={s - 1}" in mm.group(1)
          and "TEMPERATURE_WAIT SENSOR" in mm.group(1))


# ---------------------------------------------------------------------------
# 6. Syntax / Repo-Regeln
# ---------------------------------------------------------------------------

def test_syntax():
    for path in (MODULE_PATH, os.path.abspath(__file__)):
        with open(path, encoding="utf-8") as f:
            src = f.read()
        try:
            ast.parse(src, path, feature_version=(3, 9))
            ok = True
        except SyntaxError as e:
            ok = False
            print("  ", e)
        check("6 Python-3.9-Syntax %s" % os.path.basename(path), ok)
    for path in (MODULE_PATH, README_PATH):
        if not os.path.isfile(path):
            check("6 vorhanden %s" % os.path.basename(path), False)
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        check("6 keine lokalen Pfade/IPs in %s" % os.path.basename(path),
              not re.search(r"/(?:ho)me/|/(?:ro)ot/|\b(?:192\.168|10\.\d{1,3}"
                            r"|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b",
                            src))


def main():
    logging.disable(logging.CRITICAL)
    for fn in (test_config, test_power, test_fan_monitor, test_wait,
               test_cfg_text, test_syntax):
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
