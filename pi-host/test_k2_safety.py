#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_k2_safety.py – Offline-Tests für k2_safety (ohne Klipper-Lauf, ohne
Hardware). L1/L4 (Abgleich mit Creality-klippy), 18.10.2026.

Aufruf: ``cd pi-host && python3 test_k2_safety.py``  (Python >= 3.9)

Echte Upstream-Teile (Commit 9cb9975, ``lokal/upstream-klipper``
oder ``$KLIPPER_SRC``): ``configfile.ConfigWrapper``, ``msgproto.
MessageParser`` (mit den echten F008-Dictionaries, ``$K2_MCU_DICT_DIR``),
``mcu.MCU.check_valid_response``/``register_serial_response`` +
``mcu.AsyncResponseWrapper``, ``serialhdl.SerialReader.handle_default``,
``extras.heaters.Heater.set_temp/alter_target/cmd_SET_HEATER_TEMPERATURE``,
``extras.heaters.PrinterHeaters.set_temperature/turn_off_all_heaters``,
``extras.heater_bed.PrinterHeaterBed.cmd_M140/cmd_M190``,
``kinematics.extruder.PrinterExtruder.cmd_M104/cmd_M109``,
``extras.pid_calibrate.ControlAutoTune``, ``gcode.GCodeCommand``.
Serial-Verteilung = Nachbau von ``serialhdl._bg_thread`` (Handler-Tabelle,
Lock, Fremd-Thread). ``serial`` (pyserial) wird nur als leeres Modul ersetzt,
falls nicht installiert (serialhdl importiert es, benutzt es hier nicht).

Prüft:
 1. Konfig: Defaults, Section in config-pi/k2-module.cfg (= Defaults, alle
    Optionen verbraucht), ungültige Werte.
 2. L1: Registrierung je MCU (Dict kennt fakeshutdown), echte Nachricht aus
    dem Dict kodiert/dekodiert, Handler aus Fremd-Thread -> Heizer-Ziele
    sofort 0, danach im Reactor genau EIN invoke_shutdown mit Meldung;
    Stock-Key-Zuordnung; guard aus -> Upstream "got ..."; Dict ohne
    fakeshutdown -> keine Registrierung, kein Fehler.
 3. L4: Kappung M104/M109/M140/M190/SET_HEATER_TEMPERATURE (auch M141-Weg),
    PID_CALIBRATE-Regelziel, Werte unter der Grenze unverändert,
    over_limit: error, fehlende Heizer, kein Doppel-Wrap.
 4. Python-3.9-Syntax, keine lokalen Pfade/IPs.
"""

import ast
import configparser
import importlib.util
import json
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
MODULE_PATH = os.path.join(_HERE, "k2_safety.py")
README_PATH = os.path.join(_HERE, "README-k2_safety.md")
MODULE_CFG = os.path.join(_HERE, "config-pi", "k2-module.cfg")
DICT_DIR = os.environ.get("K2_MCU_DICT_DIR") or os.path.join(
    _REPO, "lokal", "mcu-dicts")
DICT_NOZ = os.path.join(DICT_DIR, "noz-duese-F008-GD32F303CBT6.dict.json")
DICT_MAIN = os.path.join(DICT_DIR, "mcu-main-F008-GD32F303RET6.dict.json")

if not os.path.isfile(os.path.join(UPSTREAM_KLIPPY, "configfile.py")):
    print("SKIP (k2_safety) Upstream-Klipper nicht gefunden: %s – KLIPPER_SRC "
          "setzen" % UPSTREAM_KLIPPY)
    sys.exit(0)
if not (os.path.isfile(DICT_NOZ) and os.path.isfile(DICT_MAIN)):
    print("SKIP (k2_safety) MCU-Dictionaries fehlen in %s – K2_MCU_DICT_DIR "
          "setzen" % DICT_DIR)
    sys.exit(0)
if UPSTREAM_KLIPPY not in sys.path:
    sys.path.append(UPSTREAM_KLIPPY)
try:
    import serial  # noqa: F401
except ImportError:
    sys.modules["serial"] = types.ModuleType("serial")
import configfile as ucfg            # noqa: E402
import gcode as ugcode               # noqa: E402
import msgproto as umsgproto         # noqa: E402
import mcu as umcu                   # noqa: E402
import serialhdl as userialhdl       # noqa: E402
from extras import heaters as uheaters          # noqa: E402
from extras import heater_bed as uheater_bed    # noqa: E402
from extras import pid_calibrate as upid        # noqa: E402
from kinematics import extruder as uextruder    # noqa: E402

spec = importlib.util.spec_from_file_location("k2_safety", MODULE_PATH)
sm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sm)

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
    def __init__(self):
        self.async_cbs = []
        self.t = 100.

    def monotonic(self):
        return self.t

    def register_async_callback(self, cb):
        self.async_cbs.append(cb)

    def run_async(self):
        cbs, self.async_cbs = self.async_cbs, []
        for cb in cbs:
            cb(self.t)


class FakeGcode:
    def __init__(self):
        self.commands = {}
        self.info = []
        self.raw = []

    def register_command(self, name, func, when_not_ready=False, desc=None):
        old = self.commands.get(name)
        if func is None:
            self.commands.pop(name, None)
        else:
            self.commands[name] = func
        return old

    def respond_info(self, msg, log=True):
        self.info.append(msg)

    def respond_raw(self, msg):
        self.raw.append(msg)

    def cmd(self, line):
        """Echter Upstream-GCodeCommand aus einer Zeile 'CMD K=V ...'
        bzw. 'M104 S400'."""
        parts = line.split()
        name = parts[0].upper()
        params = {}
        for p in parts[1:]:
            if "=" in p:
                k, v = p.split("=", 1)
            else:
                k, v = p[0], p[1:]
            params[k.upper()] = v
        return ugcode.GCodeCommand(self, name, line, params, False)


class FakeToolhead:
    def __init__(self):
        self.extruder = None

    def register_lookahead_callback(self, cb):
        cb(0.)

    def get_extruder(self):
        return self.extruder

    def get_last_move_time(self):
        return 0.


class FakeSensor:
    def __init__(self, temp):
        self.temp = temp

    def get_status(self, eventtime):
        return {"temperature": self.temp}


class FakePrinter:
    command_error = ugcode.CommandError
    config_error = ucfg.error

    def __init__(self):
        self.reactor = FakeReactor()
        self.gcode = FakeGcode()
        self.objects = {"gcode": self.gcode}
        self.events = {}
        self.shutdowns = []
        self.start_args = {"debugoutput": "batch"}   # Warten überspringen

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

    def is_shutdown(self):
        return bool(self.shutdowns)

    def get_start_args(self):
        return self.start_args


class FakeSerial:
    """Handler-Tabelle + Verteilung wie Upstream serialhdl.SerialReader."""

    def __init__(self, msgparser, name):
        self.msgparser = msgparser
        self.handlers = {}
        self.lock = threading.Lock()
        self.warn_prefix = "mcu '%s': " % name
        self.default_seen = []

    def get_msgparser(self):
        return self.msgparser

    def register_response(self, callback, name, oid=None):
        with self.lock:
            if callback is None:
                del self.handlers[name, oid]
            else:
                self.handlers[name, oid] = callback

    def handle_default(self, params):
        self.default_seen.append(params)
        userialhdl.SerialReader.handle_default(self, params)

    def dispatch_bytes(self, data):
        """Wie serialhdl._bg_thread: parsen, Handler unter Lock, in einem
        FREMDEN Thread."""
        mp = self.msgparser.messages_by_id[data[0]]
        params, pos = mp.parse(bytes(data), 0)
        params['#name'] = mp.name
        params['#sent_time'] = params['#receive_time'] = 0.

        def bg():
            hdl = (params['#name'], params.get('oid'))
            with self.lock:
                hdl = self.handlers.get(hdl, self.handle_default)
                hdl(params)
        t = threading.Thread(target=bg)
        t.start()
        t.join()
        return params


class FakeConnHelper:
    def __init__(self, serial):
        self.serial = serial

    def get_serial(self):
        return self.serial


class FakeCfgHelper:
    def __init__(self):
        self.finalized = False
        self.post_init = []

    def is_config_finalized(self):
        return self.finalized

    def register_post_init_callback(self, cb):
        self.post_init.append(cb)

    def finalize(self):
        self.finalized = True
        for cb in self.post_init:
            cb()


class FakeMCU:
    """Echte Upstream-Methoden check_valid_response/register_serial_response
    auf Fake-Verbindung."""
    check_valid_response = umcu.MCU.check_valid_response
    register_serial_response = umcu.MCU.register_serial_response

    def __init__(self, name, dict_data):
        self._name = name
        mp = umsgproto.MessageParser()
        mp.process_identify(dict_data, decompress=False)
        self._serial = FakeSerial(mp, name)
        self._conn_helper = FakeConnHelper(self._serial)
        self._config_helper = FakeCfgHelper()

    def get_name(self):
        return self._name


def make_heater(printer, name, min_temp, max_temp):
    h = uheaters.Heater.__new__(uheaters.Heater)
    h.printer = printer
    h.name = name
    h.short_name = name.split()[-1]
    h.min_temp, h.max_temp = min_temp, max_temp
    h.lock = threading.Lock()
    h.target_temp = 0.
    h.max_power = 1.
    h.control = None
    return h


def load_dict(path, drop_fakeshutdown=False):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if drop_fakeshutdown:
        d["responses"] = {k: v for k, v in d["responses"].items()
                          if not k.startswith("fakeshutdown")}
    return json.dumps(d).encode()


class Rig:
    """Drucker mit echten Upstream-Heizer-Kommandos und zwei MCUs."""

    def __init__(self, cfg_text="", with_chamber=True, with_extruder=True,
                 drop_fs=(), chamber_max=80.):
        self.printer = p = FakePrinter()
        self.mcus = {
            "mcu": FakeMCU("mcu", load_dict(DICT_MAIN, "mcu" in drop_fs)),
            "nozzle_mcu": FakeMCU("nozzle_mcu", load_dict(
                DICT_NOZ, "nozzle_mcu" in drop_fs)),
        }
        p.objects["mcu"] = self.mcus["mcu"]
        p.objects["mcu nozzle_mcu"] = self.mcus["nozzle_mcu"]
        # Heizer wie config-pi/printer.cfg (max_temp 390/135/80)
        ph = uheaters.PrinterHeaters.__new__(uheaters.PrinterHeaters)
        ph.printer = p
        ph.heaters = {}
        self.ext = make_heater(p, "extruder", 0., 390.)
        self.bed = make_heater(p, "heater_bed", 0., 135.)
        self.cham = make_heater(p, "heater_generic chamber_heater", -30.,
                                chamber_max)
        if with_extruder:
            ph.heaters["extruder"] = self.ext
        ph.heaters["heater_bed"] = self.bed
        if with_chamber:
            ph.heaters["chamber_heater"] = self.cham
        p.objects["heaters"] = self.ph = ph
        self.toolhead = FakeToolhead()
        p.objects["toolhead"] = self.toolhead
        pe = uextruder.PrinterExtruder.__new__(uextruder.PrinterExtruder)
        pe.printer = p
        pe.heater = self.ext
        pe.get_heater = (lambda: self.ext)
        self.toolhead.extruder = pe
        self.pe = pe
        pb = uheater_bed.PrinterHeaterBed.__new__(uheater_bed.PrinterHeaterBed)
        pb.printer = p
        pb.heater = self.bed
        self.pb = pb
        self.cfg_text = cfg_text

    def build(self):
        self.safety = sm.load_config(make_cfg(self.cfg_text, self.printer))
        return self.safety

    def identify_connect(self):
        self.printer.send_event("klippy:mcu_identify")
        for m in self.mcus.values():
            m._config_helper.finalize()
        self.printer.send_event("klippy:connect")
        self.printer.send_event("klippy:ready")

    def g(self, line):
        gc = self.printer.gcode
        name = line.split()[0].upper()
        cmd = gc.cmd(line)
        if name == "M104":
            return self.pe.cmd_M104(cmd)
        if name == "M109":
            return uextruder.PrinterExtruder.cmd_M109(self.pe, cmd)
        if name == "M140":
            return self.pb.cmd_M140(cmd)
        if name == "M190":
            return self.pb.cmd_M190(cmd)
        if name == "SET_HEATER_TEMPERATURE":
            h = self.ph.heaters[cmd.get("HEATER")]
            return h.cmd_SET_HEATER_TEMPERATURE(cmd)
        if name == "TURN_OFF_HEATERS":
            return self.ph.cmd_TURN_OFF_HEATERS(cmd)
        if name == "KTWO_SAFETY_STATUS":
            return gc.commands[name](cmd)
        raise AssertionError(name)

    def targets(self):
        return (self.ext.target_temp, self.bed.target_temp,
                self.cham.target_temp)

    def fake(self, mcu_name, reason):
        m = self.mcus[mcu_name]
        mp = m._serial.msgparser.lookup_command(sm.FAKESHUTDOWN_FMT)
        data = mp.encode([123456, reason])
        return m._serial.dispatch_bytes(data)


def make_cfg(text, printer, section="k2_safety"):
    fc = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(
        "#", ";"))
    fc.read_string("[%s]\n%s" % (section, text))
    return ucfg.ConfigWrapper(printer, fc, {}, section)


# ---------------------------------------------------------------------------
# 1. Konfig
# ---------------------------------------------------------------------------

def test_config():
    r = Rig()
    s = r.build()
    check("1 Default guard an", s.guard is True)
    check("1 Default MCUs mcu + nozzle_mcu",
          [n for n, _ in s.mcus] == ["mcu", "nozzle_mcu"])
    check("1 Default Grenzen 350/120/60 (Stock product_param F008)",
          [c[2] for c in s.limit_cfg] == [350., 120., 60.], s.limit_cfg)
    check("1 Default over_limit clamp", s.over_limit == "clamp")
    # Section aus config-pi/k2-module.cfg
    fc = configparser.RawConfigParser(strict=False, inline_comment_prefixes=(
        "#", ";"))
    with open(MODULE_CFG, encoding="utf-8") as f:
        fc.read_file(f)
    check("1 k2-module.cfg hat [k2_safety]", fc.has_section("k2_safety"))
    r2 = Rig()
    w = ucfg.ConfigWrapper(r2.printer, fc, {}, "k2_safety")
    s2 = sm.load_config(w)
    check("1 k2-module.cfg: Werte = Defaults",
          (s2.guard, [c[2] for c in s2.limit_cfg], s2.over_limit,
           [n for n, _ in s2.mcus], [c[1] for c in s2.limit_cfg]) ==
          (True, [350., 120., 60.], "clamp", ["mcu", "nozzle_mcu"],
           ["extruder", "heater_bed", "chamber_heater"]))
    used = set(o for (sec, o) in w.access_tracking if sec == "k2_safety")
    check("1 k2-module.cfg: alle Optionen verbraucht (check_unused_options)",
          set(fc.options("k2_safety")) <= used,
          set(fc.options("k2_safety")) - used)
    check("1 over_limit ungültig -> Config-Fehler", raises(
        lambda: Rig("over_limit: egal").build(), ucfg.error))
    check("1 MCU unbekannt -> Config-Fehler", raises(
        lambda: Rig("mcus: mcu, gibtsnicht").build(), ucfg.error,
        "gibtsnicht"))
    check("1 mcus leer -> Config-Fehler", raises(
        lambda: Rig("mcus: ,").build(), ucfg.error))
    check("1 Grenze 0 -> Config-Fehler", raises(
        lambda: Rig("max_nozzle_temp: 0").build(), ucfg.error))
    s3 = Rig("fakeshutdown_guard: False").build()
    check("1 guard abschaltbar nur ausdrücklich", s3.guard is False)


# ---------------------------------------------------------------------------
# 2. L1 fakeshutdown
# ---------------------------------------------------------------------------

def test_fakeshutdown():
    r = Rig()
    s = r.build()
    nz = r.mcus["nozzle_mcu"]._serial
    # Dict-Fakten
    st = nz.msgparser.lookup_command(sm.FAKESHUTDOWN_FMT)
    check("2 Dict Düse kennt '%s'" % sm.FAKESHUTDOWN_FMT, st is not None)
    enums = nz.msgparser.get_enumerations().get("static_string_id", {})
    check("2 Dict Düse: static_string 30 = 'noz0 ADC out of range'",
          enums.get("noz0 ADC out of range") == 30, enums)
    menums = r.mcus["mcu"]._serial.msgparser.get_enumerations().get(
        "static_string_id", {})
    check("2 Dict Main: static_string 30 = 'mcu0 ADC out of range'",
          menums.get("mcu0 ADC out of range") == 30)
    r.printer.send_event("klippy:mcu_identify")
    check("2 identify: beide MCUs 'aktiv'",
          s.monitored == {"mcu": "aktiv", "nozzle_mcu": "aktiv"}, s.monitored)
    check("2 vor post_init: nur Platzhalter-Handler (AsyncResponseWrapper)",
          ("fakeshutdown", None) in nz.handlers and
          nz.handlers[("fakeshutdown", None)].__name__ == "<lambda>" and
          "_on_fakeshutdown" not in repr(
              nz.handlers[("fakeshutdown", None)].__code__.co_names))
    for m in r.mcus.values():
        m._config_helper.finalize()
    r.printer.send_event("klippy:connect")
    r.printer.send_event("klippy:ready")
    check("2 nach post_init: k2_safety-Handler registriert",
          "_on_fakeshutdown" in nz.handlers[("fakeshutdown", None)]
          .__code__.co_names)
    r.g("M104 S220")
    r.g("M140 S60")
    r.g("SET_HEATER_TEMPERATURE HEATER=chamber_heater TARGET=45")
    check("2 Ausgangslage geheizt", r.targets() == (220., 60., 45.),
          r.targets())
    params = r.fake("nozzle_mcu", "noz0 ADC out of range")
    check("2 echte Nachricht dekodiert (static_string_id als Text)",
          params.get("static_string_id") == "noz0 ADC out of range" and
          params.get("clock") == 123456, params)
    check("2 Heizer-Ziele SOFORT 0 (noch im Serial-Thread)",
          r.targets() == (0., 0., 0.), r.targets())
    check("2 Shutdown noch nicht im Serial-Thread", not r.printer.shutdowns)
    check("2 Upstream-'got' NICHT erreicht", not nz.default_seen)
    r.printer.reactor.run_async()
    sd = r.printer.shutdowns
    check("2 genau ein invoke_shutdown im Reactor", len(sd) == 1, sd)
    msg = sd[0] if sd else ""
    check("2 Meldung nennt MCU, Grund, fakeshutdown, Quelle",
          all(x in msg for x in ("nozzle_mcu", "noz0 ADC out of range",
                                 "fakeshutdown", "CR:mcu.py:650-711",
                                 "Heizungen aus")), msg)
    check("2 Fehlermeldung '!!' an die Konsole",
          any(x.startswith("!! [k2_safety] MCU 'nozzle_mcu' meldet fakeshutdown")
              for x in r.printer.gcode.raw), r.printer.gcode.raw)
    # Wiederholung
    r.g("M104 S200")    # (Upstream würde im Shutdown ablehnen; hier Fake)
    r.fake("nozzle_mcu", "noz0 ADC out of range")
    r.printer.reactor.run_async()
    check("2 zweiter fakeshutdown: Heizer wieder 0, kein zweiter Shutdown",
          r.targets()[0] == 0. and len(r.printer.shutdowns) == 1 and
          s.event_count == 2)
    st = s.get_status(0.)
    check("2 Status triggered/last_event", st["triggered"] and
          "noz0" in st["last_event"])
    # Main-MCU mit Stock-Key-Zuordnung (Kammer < 0 -> key511)
    r = Rig()
    r.printer.objects["temperature_sensor chamber_temp"] = FakeSensor(-12.5)
    r.printer.objects["temperature_sensor mcu_temp"] = FakeSensor(35.)
    r.build()
    r.identify_connect()
    r.g("M140 S90")
    r.fake("mcu", "mcu0 ADC out of range")
    check("2 Main-MCU: Heizer 0", r.targets() == (0., 0., 0.))
    r.printer.reactor.run_async()
    msg = r.printer.shutdowns[0] if r.printer.shutdowns else ""
    check("2 Main-MCU: Stock-Key key511 (chamber_temp < 0) in Meldung",
          "key511" in msg and "'mcu'" in msg and "key512" not in msg, msg)
    # Konsolen-Audit 23.10.2026: Abschalten scheitert -> in der !!-Meldung
    r = Rig()
    s = r.build()
    r.identify_connect()

    def kaputt(deg):
        raise RuntimeError("Heizer hängt")
    r.ext.set_temp = kaputt
    logging.disable(logging.CRITICAL)
    r.fake("nozzle_mcu", "noz0 ADC out of range")
    r.printer.reactor.run_async()
    logging.disable(logging.NOTSET)
    rot = [x for x in r.printer.gcode.raw if x.startswith("!! [k2_safety]")]
    check("2 Abschalten fehlgeschlagen -> ACHTUNG in !!-Meldung + Shutdown",
          len(rot) == 1 and "ACHTUNG: Abschalten fehlgeschlagen" in rot[0]
          and len(r.printer.shutdowns) == 1, rot)
    # fakeshutdown VOR klippy:connect (Heizer noch nicht eingesammelt)
    r = Rig()
    s = r.build()
    r.printer.send_event("klippy:mcu_identify")
    for m in r.mcus.values():
        m._config_helper.finalize()
    r.ext.target_temp = 150.
    r.fake("nozzle_mcu", "noz0 ADC out of range")
    check("2 vor connect: Heizer trotzdem 0 (Rückfall pheaters.heaters)",
          r.ext.target_temp == 0.)
    # guard aus -> Upstream-Verhalten
    r = Rig("fakeshutdown_guard: False")
    s = r.build()
    r.identify_connect()
    nz = r.mcus["nozzle_mcu"]._serial
    check("2 guard aus: kein Handler, Status 'aus'",
          ("fakeshutdown", None) not in nz.handlers and
          s.monitored["nozzle_mcu"] == "aus")
    check("2 guard aus: WARNUNG beim Start",
          any(x.startswith("// [k2_safety] WARNUNG fakeshutdown_guard: False")
              for x in r.printer.gcode.raw), r.printer.gcode.raw)
    r.g("M104 S200")
    r.fake("nozzle_mcu", "noz0 ADC out of range")
    r.printer.reactor.run_async()
    check("2 guard aus: Upstream handle_default ('got ...'), kein Shutdown",
          len(nz.default_seen) == 1 and not r.printer.shutdowns and
          r.ext.target_temp == 200.)
    # Dict ohne fakeshutdown (z.B. Upstream-MCU-FW)
    r = Rig(drop_fs=("nozzle_mcu",))
    s = r.build()
    r.identify_connect()
    check("2 Dict ohne fakeshutdown: keine Registrierung, kein Fehler",
          s.monitored == {"mcu": "aktiv",
                          "nozzle_mcu": "kein fakeshutdown im Dict"} and
          ("fakeshutdown", None) not in r.mcus["nozzle_mcu"]._serial.handlers,
          s.monitored)
    # Ohne k2_safety: Upstream verschluckt die Meldung
    r = Rig()
    nz = r.mcus["nozzle_mcu"]._serial
    r.ext.target_temp = 200.
    r.fake("nozzle_mcu", "noz0 ADC out of range")
    check("2 Referenz ohne k2_safety: nur 'got', Heizer bleibt 200",
          len(nz.default_seen) == 1 and r.ext.target_temp == 200.)


# ---------------------------------------------------------------------------
# 3. L4 Grenzen
# ---------------------------------------------------------------------------

def test_limits():
    r = Rig()
    s = r.build()
    r.identify_connect()
    gc = r.printer.gcode
    r.g("M104 S400")
    check("3 M104 S400 -> 350", r.ext.target_temp == 350., r.ext.target_temp)
    check("3 Warnung bei Kappung (// [k2_safety])",
          any(x.startswith("// [k2_safety] Solltemperatur 400.0")
              and "auf 350.0 gekappt" in x for x in gc.raw), gc.raw)
    r.g("M109 S360")
    check("3 M109 S360 -> 350", r.ext.target_temp == 350.)
    r.g("M140 S130")
    check("3 M140 S130 -> 120", r.bed.target_temp == 120.)
    r.g("M190 S125")
    check("3 M190 S125 -> 120", r.bed.target_temp == 120.)
    r.g("SET_HEATER_TEMPERATURE HEATER=chamber_heater TARGET=75")
    check("3 SET_HEATER_TEMPERATURE Kammer 75 -> 60 (auch M141/M191-Weg)",
          r.cham.target_temp == 60.)
    r.g("SET_HEATER_TEMPERATURE HEATER=extruder TARGET=389")
    check("3 SET_HEATER_TEMPERATURE Düse 389 -> 350",
          r.ext.target_temp == 350.)
    r.g("SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=134")
    check("3 SET_HEATER_TEMPERATURE Bett 134 -> 120",
          r.bed.target_temp == 120.)
    check("3 Upstream-max_temp-Prüfung weiter aktiv (M104 S500 -> 350, "
          "kein Fehler, da vorher gekappt)",
          not raises(lambda: r.g("M104 S500")) and
          r.ext.target_temp == 350.)
    n = s.clamp_count
    r.g("M104 S350")
    r.g("M140 S120")
    r.g("SET_HEATER_TEMPERATURE HEATER=chamber_heater TARGET=60")
    check("3 genau auf der Grenze: unverändert, keine Warnung",
          r.targets() == (350., 120., 60.) and s.clamp_count == n)
    r.g("M104 S215")
    r.g("M140 S65")
    r.g("SET_HEATER_TEMPERATURE HEATER=chamber_heater TARGET=45")
    check("3 normale Werte unverändert", r.targets() == (215., 65., 45.))
    r.g("TURN_OFF_HEATERS")
    check("3 TURN_OFF_HEATERS -> 0", r.targets() == (0., 0., 0.))
    check("3 Untergrenze Upstream bleibt (M104 S-5 -> Fehler)",
          raises(lambda: r.g("M104 S-5"), ugcode.CommandError))
    # PID_CALIBRATE: Regelziel über alter_target
    tune = upid.ControlAutoTune(r.ext, 380.)
    r.ext.set_pwm = (lambda t, v: None)
    r.ext.get_pwm_delay = (lambda: 0.)
    tune.temperature_update(1., 20., 380.)
    check("3 PID_CALIBRATE TARGET=380: Regelziel (alter_target) -> 350",
          r.ext.target_temp == 350., r.ext.target_temp)
    r.ext.alter_target(0.)
    check("3 alter_target(0) bleibt 0", r.ext.target_temp == 0.)
    # Status-Befehl
    gc.info[:] = []
    r.g("KTWO_SAFETY_STATUS")
    out = "\n".join(gc.info)
    check("3 KTWO_SAFETY_STATUS zeigt Grenzen + MCUs",
          "extruder=350" in out and "heater_bed=120" in out and
          "chamber_heater=60" in out and "nozzle_mcu: aktiv" in out, out)
    # kein Doppel-Wrap bei zweitem connect
    f = r.ext.set_temp
    r.printer.send_event("klippy:connect")
    check("3 zweiter connect: kein Doppel-Wrap", r.ext.set_temp is f)
    # over_limit: error
    r = Rig("over_limit: error")
    r.build()
    r.identify_connect()
    r.g("M104 S200")
    check("3 error-Modus: M104 S400 -> command_error, Ziel unverändert",
          raises(lambda: r.g("M104 S400"), ugcode.CommandError,
                 "abgewiesen") and r.ext.target_temp == 200.)
    check("3 error-Modus: SET_HEATER_TEMPERATURE Kammer 75 -> Fehler",
          raises(lambda: r.g(
              "SET_HEATER_TEMPERATURE HEATER=chamber_heater TARGET=75"),
              ugcode.CommandError))
    r.g("M140 S100")
    check("3 error-Modus: Werte unter der Grenze gehen", r.bed.target_temp
          == 100.)
    r.ext.alter_target(380.)
    check("3 error-Modus: alter_target kappt (Regelschleife wirft nie)",
          r.ext.target_temp == 350.)
    # Fehlende Heizer
    r = Rig(with_chamber=False)
    s = r.build()
    r.identify_connect()
    check("3 Kammerheizer fehlt: nur Warnung, Status missing",
          s.missing == ["chamber_heater"] and "chamber_heater"
          not in s.limits)
    r = Rig(with_extruder=False)
    r.build()
    check("3 Düsenheizer fehlt: Config-Fehler bei connect", raises(
        lambda: r.identify_connect(), ucfg.error, "extruder"))
    # Grenze über Upstream-max_temp: Upstream greift zuerst
    r = Rig("max_chamber_temp: 90")
    r.build()
    r.identify_connect()
    check("3 Grenze > max_temp: Upstream-Fehler bleibt (85 > 80)",
          raises(lambda: r.g(
              "SET_HEATER_TEMPERATURE HEATER=chamber_heater TARGET=85"),
              ugcode.CommandError, "out of range"))


# ---------------------------------------------------------------------------
# 4. Syntax / Repo-Regeln
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
        check("4 Python-3.9-Syntax %s" % os.path.basename(path), ok)
    for path in (MODULE_PATH, README_PATH):
        if not os.path.isfile(path):
            check("4 vorhanden %s" % os.path.basename(path), False)
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        check("4 keine lokalen Pfade/IPs in %s" % os.path.basename(path),
              not re.search(r"/(?:ho)me/|/(?:ro)ot/|\b(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]"
                            r"|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b", src))


def main():
    logging.disable(logging.CRITICAL)
    for fn in (test_config, test_fakeshutdown, test_limits, test_syntax):
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
