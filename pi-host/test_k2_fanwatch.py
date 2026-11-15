#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_k2_fanwatch.py – Offline-Tests für k2_fanwatch (ohne Klipper, ohne
Hardware). Aufruf: ``cd pi-host && python3 test_k2_fanwatch.py``
(Python >= 3.9). Letzte Zeile ``ERGEBNIS: ok (n/n ok)`` bzw.
``ERGEBNIS: x FEHLER``, Exit-Code 0/1.

Attrappen: Printer, Reactor (Zeit/Timer/Callbacks), G-Code, Lüfter
(heater_fan -> 'speed', output_pin -> 'value'), Frequenzzähler (Modul
``pulse_counter``), Heizer, print_stats, pause_resume. Echt: k2_konsole
(liegt im selben Ordner) – zusätzlich wird der Betrieb ohne k2_konsole
geprüft.

Prüft:
 1. Optionen: Defaults, alle Optionen werden gelesen, Grenzen, deutsche
    Fehlermeldungen.
 2. Zustandsautomat (rein): Fehler erst nach spinup+fault_time, Drosselung
    0,6 ohne Bewertung, Entwarnung, Wiederholung, Erhöhen, kurze Aussetzer,
    Abschalten im Fehlerfall, keine Messung.
 3. Hülle im Modus report: Meldungen, Wiederholung, Entwarnung,
    Bauteillüfter unter part_min_speed / ohne Freigabe, get_status.
 4. Modus off.
 5. Modus act: PAUSE nur im Druck und nur einmal, Extruder-Ziel 0 beim
    Hotend, Reihenfolge PAUSE -> Ziel 0, nie Shutdown/Motoren aus.
 6. Fehlende Objekte, leerer Tacho-Pin, nicht lesbare Werte, Shutdown.
 7. Zähler-Parameter und Drehzahl-Formel.
 8. Befehl KTWO_FANWATCH_STATUS, Namensregel.
 9. Betrieb ohne k2_konsole.
10. Python-3.9-Syntax, Modulkopf, Doku-Regeln.
"""

import ast
import importlib.util
import logging
import os
import re
import sys
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.join(_HERE, "k2_fanwatch.py")
README_PATH = os.path.join(_HERE, "README-k2_fanwatch.md")
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print("  %s %s%s" % ("OK  " if ok else "FAIL", name,
                         ("  [" + str(detail) + "]") if detail and not ok
                         else ""))


# ---------------------------------------------------------------------------
# Attrappen
# ---------------------------------------------------------------------------

class FakeZaehler:
    """Ersatz für pulse_counter.FrequencyCounter."""

    def __init__(self, printer, pin, sample_time, poll_time):
        if "kaputt" in pin:
            raise ValueError("Invalid pin description '%s'" % (pin,))
        self.pin = pin
        self.sample_time = sample_time
        self.poll_time = poll_time
        self._last_time = 0.      # None = MCU hat noch nichts geliefert
        self._freq = 0.
        self.wirft = False

    def get_frequency(self):
        if self.wirft:
            raise RuntimeError("Zähler defekt")
        return self._freq

    def rpm(self, rpm, ppr=2):
        self._freq = rpm * ppr / 30.


ZAEHLER = []


def _fabrik(printer, pin, sample_time, poll_time):
    z = FakeZaehler(printer, pin, sample_time, poll_time)
    ZAEHLER.append(z)
    return z


_pc = types.ModuleType("pulse_counter")
_pc.FrequencyCounter = _fabrik
sys.modules["pulse_counter"] = _pc


def lade_modul(name="k2_fanwatch"):
    spec = importlib.util.spec_from_file_location(name, MODULE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


fw = lade_modul()


class ConfigError(Exception):
    pass


_FEHLT = object()


class FakeConfig:
    error = ConfigError

    def __init__(self, printer, werte=None, name="k2_fanwatch"):
        self.printer = printer
        self.werte = dict(werte or {})
        self.name = name
        self.gelesen = set()

    def get_printer(self):
        return self.printer

    def get_name(self):
        return self.name

    def get(self, option, default=_FEHLT):
        self.gelesen.add(option)
        if option in self.werte:
            return self.werte[option]
        if default is _FEHLT:
            raise ConfigError("Option '%s' in section '%s' must be specified"
                              % (option, self.name))
        return default


class FakeReactor:
    NOW = 0.
    NEVER = 9999999999999999.

    def __init__(self):
        self.t = 100.
        self.timers = []
        self.callbacks = []
        self.halte_callbacks = False

    def monotonic(self):
        return self.t

    def register_timer(self, cb, waketime=NEVER):
        tm = [cb, waketime]
        self.timers.append(tm)
        return tm

    def update_timer(self, tm, waketime):
        tm[1] = waketime

    def register_callback(self, cb, waketime=NOW):
        self.callbacks.append(cb)

    def _callbacks(self):
        while self.callbacks and not self.halte_callbacks:
            self.callbacks.pop(0)(self.t)

    def run_until(self, t):
        self._callbacks()
        while True:
            due = [tm for tm in self.timers if tm[1] <= t]
            if not due:
                break
            tm = min(due, key=lambda x: x[1])
            self.t = max(self.t, tm[1])
            tm[1] = tm[0](self.t)
            self._callbacks()
        self.t = max(self.t, t)


class FakeGcode:
    def __init__(self):
        self.commands = {}
        self.raw = []
        self.info = []
        self.skripte = []
        self.hook = None

    def register_command(self, name, func, when_not_ready=False, desc=None):
        self.commands[name] = func

    def respond_raw(self, msg):
        self.raw.append(msg)

    def respond_info(self, msg, log=True):
        self.info.append(msg)

    def run_script(self, script):
        self.skripte.append(script)
        if self.hook is not None:
            self.hook(script)


class FakeGcmd:
    def __init__(self):
        self.info = []

    def respond_info(self, msg, log=True):
        self.info.append(msg)


class FakePrinter:
    config_error = ConfigError

    def __init__(self):
        self.reactor = FakeReactor()
        self.gcode = FakeGcode()
        self.objects = {"gcode": self.gcode}
        self.events = {}
        self.shutdown = False
        self.shutdown_aufrufe = []

    def get_reactor(self):
        return self.reactor

    def lookup_object(self, name, default=_FEHLT):
        if name in self.objects:
            return self.objects[name]
        if default is _FEHLT:
            raise ConfigError("Unknown config object '%s'" % (name,))
        return default

    def register_event_handler(self, ev, cb):
        self.events.setdefault(ev, []).append(cb)

    def send_event(self, ev):
        for cb in self.events.get(ev, []):
            cb()

    def is_shutdown(self):
        return self.shutdown

    def invoke_shutdown(self, msg):
        self.shutdown_aufrufe.append(msg)


class FakeFan:
    """heater_fan: get_status -> speed (+ rpm None wie ohne tachometer_pin)."""

    def __init__(self, speed=0.):
        self.speed = speed
        self.wirft = False

    def get_status(self, eventtime):
        if self.wirft:
            raise RuntimeError("Status defekt")
        return {"speed": self.speed, "rpm": None}


class FakePin:
    """output_pin: get_status -> value (0..1)."""

    def __init__(self, value=0.):
        self.value = value

    def get_status(self, eventtime):
        return {"value": self.value}


class FakeHeater:
    def __init__(self, target=0.):
        self.target = target
        self.gesetzt = []

    def get_status(self, eventtime):
        return {"temperature": 25., "target": self.target, "power": 0.}

    def set_temp(self, degrees):
        self.gesetzt.append(degrees)
        self.target = degrees


class FakeHeaters:
    def __init__(self, heizer):
        self.heizer = heizer

    def lookup_heater(self, name):
        if name not in self.heizer:
            raise ConfigError("Unknown heater '%s'" % (name,))
        return self.heizer[name]


class FakePrintStats:
    def __init__(self, state="standby"):
        self.state = state

    def get_status(self, eventtime):
        return {"state": self.state}


class FakePauseResume:
    def __init__(self):
        self.is_paused = False

    def get_status(self, eventtime):
        return {"is_paused": self.is_paused}


class Umgebung:
    """Drucker-Attrappe mit geladenem Modul (connect + ready ausgelöst).
    Start: Hotend-Lüfter 1,0 mit 8000 U/min, Bauteillüfter aus."""

    def __init__(self, werte=None, hotend=True, part=True, freigabe=True,
                 heizer=True, druck="standby", modul=None, ziel=0.,
                 objekte=None):
        modul = modul or fw
        self.printer = p = FakePrinter()
        p.objects.update(objekte or {})
        self.reactor = p.reactor
        self.gcode = p.gcode
        self.hotend_fan = FakeFan(1.0)
        self.part_fan = FakePin(0.)
        self.fan_en = FakePin(1.)
        if hotend:
            p.objects["heater_fan hotend_fan"] = self.hotend_fan
        if part:
            p.objects["output_pin fan0"] = self.part_fan
        if freigabe:
            p.objects["output_pin fan0_en"] = self.fan_en
        self.heater = FakeHeater(ziel)
        p.objects["heaters"] = FakeHeaters(
            {"extruder": self.heater} if heizer else {})
        self.stats = FakePrintStats(druck)
        self.pause = FakePauseResume()
        p.objects["print_stats"] = self.stats
        p.objects["pause_resume"] = self.pause
        self.config = FakeConfig(p, werte)
        del ZAEHLER[:]
        self.fw = modul.load_config(self.config)
        self.zaehler = list(ZAEHLER)
        self.z_hotend = self.fw.hotend.zaehler
        self.z_part = self.fw.part.zaehler
        if self.z_hotend is not None:
            self.z_hotend.rpm(8000)
        p.send_event("klippy:connect")
        p.send_event("klippy:ready")

    def bis(self, t):
        self.reactor.run_until(t)

    def rote(self):
        return [z for z in self.gcode.raw if z.startswith("!! ")]

    def gelbe(self):
        return [z for z in self.gcode.raw if z.startswith("// ")]

    def status(self):
        return self.fw.get_status(self.reactor.monotonic())

    def pause_makro(self, ziel_danach=140.):
        """PAUSE wie das echte Makro: merkt das Ziel, pausiert, setzt die
        Düse auf die Pausentemperatur."""
        self.ziel_bei_pause = []

        def hook(script):
            if script == "PAUSE":
                self.ziel_bei_pause.append(self.heater.target)
                self.stats.state = "paused"
                self.pause.is_paused = True
                self.heater.target = ziel_danach
        self.gcode.hook = hook


def config_fehler(werte):
    """Text des Config-Fehlers oder None."""
    try:
        fw.lese_optionen(FakeConfig(None, werte))
    except ConfigError as e:
        return str(e)
    return None


def laufe(w, schritte):
    """Wächter mit (Zeit, Stärke, rpm) füttern -> Liste (Zeit, Ereignis)."""
    aus = []
    for t, speed, rpm in schritte:
        for e in w.update(t, speed, rpm):
            aus.append((t, e))
    return aus


def sekunden(start, ende, speed, rpm):
    return [(float(t), speed, rpm) for t in range(start, ende + 1)]


# ---------------------------------------------------------------------------
# 1. Optionen
# ---------------------------------------------------------------------------

def test_optionen():
    cfg = FakeConfig(None, {})
    opt = fw.lese_optionen(cfg)
    soll = {
        "mode": "report", "hotend_tach_pin": "nozzle_mcu:PA12",
        "part_tach_pin": "nozzle_mcu:PC13", "tach_ppr": 2,
        "hotend_fan": "heater_fan hotend_fan", "part_fan": "output_pin fan0",
        "hotend_min_speed": 0.95, "part_min_speed": 0.5, "spinup_time": 5.,
        "fault_time": 5., "poll_interval": 1., "repeat_interval": 300.,
    }
    for k, v in sorted(soll.items()):
        check("1 Default %s = %s" % (k, v), opt.get(k) == v, opt.get(k))
    check("1 Default mode ist report (nur melden)", opt["mode"] == "report")
    check("1 Zusatz-Defaults (part_enable, hotend_heater, min_rpm, "
          "tach_poll_interval)",
          opt["part_enable"] == "output_pin fan0_en"
          and opt["hotend_heater"] == "extruder" and opt["min_rpm"] == 0.
          and opt["tach_poll_interval"] == 0.0015)
    alle = dict((k, "%s" % v) for k, v in opt.items())
    cfg = FakeConfig(None, alle)
    opt2 = fw.lese_optionen(cfg)
    check("1 alle Optionen aus der Config werden gelesen (keine bleibt "
          "unbenutzt)", cfg.gelesen == set(alle), set(alle) - cfg.gelesen)
    check("1 Werte aus der Config = Defaults", opt2 == opt)
    check("1 tach_ppr ist ganzzahlig", isinstance(opt2["tach_ppr"], int))
    for m in ("off", "report", "act", "ACT", " Report "):
        check("1 mode '%s' gültig" % m, config_fehler({"mode": m}) is None)
    ok = fw.lese_optionen(FakeConfig(None, {"hotend_tach_pin": "",
                                           "repeat_interval": "0"}))
    check("1 leerer Tacho-Pin und repeat_interval 0 sind erlaubt",
          ok["hotend_tach_pin"] == "" and ok["repeat_interval"] == 0.)
    faelle = [
        ({"mode": "pause"}, "mode", "erlaubt: off, report, act"),
        ({"fault_time": "1"}, "fault_time", "zu klein"),
        ({"fault_time": "601"}, "fault_time", "zu groß"),
        ({"fault_time": "abc"}, "fault_time", "keine Zahl"),
        ({"fault_time": ""}, "fault_time", "ist leer"),
        ({"hotend_min_speed": "0,95"}, "hotend_min_speed", "Dezimalpunkt"),
        ({"hotend_min_speed": "0"}, "hotend_min_speed", "größer als 0"),
        ({"hotend_min_speed": "1.2"}, "hotend_min_speed", "höchstens 1"),
        ({"part_min_speed": "-0.1"}, "part_min_speed", "größer als 0"),
        ({"part_min_speed": "1.01"}, "part_min_speed", "höchstens 1"),
        ({"tach_ppr": "0"}, "tach_ppr", "mindestens 1"),
        ({"tach_ppr": "2.5"}, "tach_ppr", "keine ganze Zahl"),
        ({"spinup_time": "-1"}, "spinup_time", "mindestens 0"),
        ({"spinup_time": "121"}, "spinup_time", "höchstens 120"),
        ({"poll_interval": "0.1"}, "poll_interval", "mindestens 0.2"),
        ({"poll_interval": "11"}, "poll_interval", "höchstens 10"),
        ({"poll_interval": "6", "fault_time": "5"}, "poll_interval",
         "nicht größer"),
        ({"repeat_interval": "5"}, "repeat_interval", "mindestens 10"),
        ({"repeat_interval": "-1"}, "repeat_interval", "mindestens 0"),
        ({"min_rpm": "nan"}, "min_rpm", "keine endliche Zahl"),
        ({"tach_poll_interval": "0"}, "tach_poll_interval", "größer als 0"),
        ({"hotend_tach_pin": "nozzle_mcu:PA12",
          "part_tach_pin": "^nozzle_mcu:PA12"}, "part_tach_pin",
         "denselben Pin"),
    ]
    for werte, option, text in faelle:
        msg = config_fehler(werte)
        check("1 Config-Fehler %s -> '%s'" % (werte, text),
              msg is not None and msg.startswith("[k2_fanwatch]")
              and option in msg and text in msg, msg)
    grenz = [{"fault_time": "2"}, {"fault_time": "600"},
             {"hotend_min_speed": "1"}, {"part_min_speed": "0.01"},
             {"spinup_time": "0"}, {"repeat_interval": "10"},
             {"poll_interval": "0.2"}, {"tach_ppr": "12"}]
    for werte in grenz:
        check("1 Grenzwert erlaubt %s" % werte, config_fehler(werte) is None,
              config_fehler(werte))


# ---------------------------------------------------------------------------
# 2. Zustandsautomat (rein)
# ---------------------------------------------------------------------------

def test_waechter():
    W = fw.LuefterWaechter
    # Hotend 1,0 und 0 U/min ab dem Einschalten
    w = W(0.95, 5., 5., 300.)
    ev = laufe(w, sekunden(0, 9, 1.0, 0.))
    check("2 Hotend 1,0 / 0 U/min: bis 9 s kein Fehler", ev == [], ev)
    check("2 … Zustand zuletzt 'wartet'", w.state == fw.Z_WARTET, w.state)
    w2 = W(0.95, 5., 5., 300.)
    laufe(w2, sekunden(0, 4, 1.0, 0.))
    check("2 … in der Schonfrist Zustand 'anlauf'", w2.state == fw.Z_ANLAUF,
          w2.state)
    ev = laufe(w, [(10., 1.0, 0.)])
    check("2 … Fehler genau nach spinup+fault_time (10 s)",
          ev == [(10., fw.E_FEHLER)] and w.fault and w.state == fw.Z_FEHLER
          and w.fault_count == 1, ev)
    ev = laufe(w, sekunden(11, 60, 1.0, 0.))
    check("2 … Fehler wird nicht jede Sekunde neu gemeldet", ev == [], ev)
    # Wiederholung
    ev = laufe(w, sekunden(61, 320, 1.0, 0.))
    check("2 Wiederholmeldung genau einmal nach repeat_interval (310 s)",
          ev == [(310., fw.E_WIEDERHOLUNG)], ev)
    ev = laufe(w, sekunden(321, 620, 1.0, 0.))
    check("2 zweite Wiederholung bei 610 s",
          ev == [(610., fw.E_WIEDERHOLUNG)], ev)
    # Entwarnung
    ev = laufe(w, [(621., 1.0, 7900.)])
    check("2 Drehzahl kehrt zurück -> Entwarnung, Zustand ok",
          ev == [(621., fw.E_ENTWARNUNG)] and not w.fault
          and w.state == fw.Z_OK, ev)
    ev = laufe(w, sekunden(622, 700, 1.0, 7900.))
    check("2 danach Ruhe, Fehlerzähler bleibt 1", ev == []
          and w.fault_count == 1)
    # repeat_interval 0 = keine Wiederholung
    w = W(0.95, 5., 5., 0.)
    ev = laufe(w, sekunden(0, 2000, 1.0, 0.))
    check("2 repeat_interval 0: nur die erste Meldung",
          ev == [(10., fw.E_FEHLER)], ev)
    # dreht -> nie Fehler
    w = W(0.95, 5., 5., 300.)
    ev = laufe(w, sekunden(0, 600, 1.0, 8000.))
    check("2 Lüfter dreht: nie ein Ereignis, Zustand ok",
          ev == [] and w.state == fw.Z_OK)
    # Drosselung 0,6 (Antasten): keine Bewertung
    w = W(0.95, 5., 5., 300.)
    ev = laufe(w, sekunden(0, 120, 0.6, 0.))
    check("2 Drosselung 0,6 mit 0 U/min: keine Bewertung (Zustand 'aus')",
          ev == [] and w.state == fw.Z_AUS and not w.fault, (ev, w.state))
    w = W(0.95, 5., 5., 300.)
    schritte = (sekunden(0, 30, 1.0, 8000.) + sekunden(31, 90, 0.6, 0.)
                + sekunden(91, 93, 1.0, 0.) + sekunden(94, 200, 1.0, 8000.))
    ev = laufe(w, schritte)
    check("2 Lauf -> Drosselung 0,6 (60 s, Tacho 0) -> zurück auf 1,0: kein "
          "Fehlalarm", ev == [] and w.fault_count == 0, ev)
    # nach der Drosselung bleibt der Lüfter wirklich stehen
    w = W(0.95, 5., 5., 300.)
    ev = laufe(w, sekunden(0, 30, 1.0, 8000.) + sekunden(31, 60, 0.6, 0.)
               + sekunden(61, 80, 1.0, 0.))
    check("2 nach der Drosselung steht der Lüfter: Fehler 10 s nach dem "
          "Hochschalten", ev == [(71., fw.E_FEHLER)], ev)
    # Stillstand im Betrieb
    w = W(0.95, 5., 5., 300.)
    ev = laufe(w, sekunden(0, 100, 1.0, 8000.) + sekunden(101, 110, 1.0, 0.))
    check("2 Stillstand im Betrieb: Fehler nach fault_time (5 s)",
          ev == [(106., fw.E_FEHLER)], ev)
    # kurzer Aussetzer
    w = W(0.95, 5., 5., 300.)
    ev = laufe(w, sekunden(0, 100, 1.0, 8000.) + sekunden(101, 104, 1.0, 0.)
               + sekunden(105, 200, 1.0, 8000.))
    check("2 Aussetzer von 4 s (< fault_time): kein Fehler", ev == [], ev)
    # Erhöhen im Betrieb
    w = W(0.5, 5., 5., 300.)
    ev = laufe(w, sekunden(0, 50, 0.6, 3000.) + sekunden(51, 53, 0.6, 0.)
               + sekunden(54, 70, 1.0, 0.))
    check("2 Erhöhen 0,6 -> 1,0 bei stehendem Lüfter: keine Bewertung in der "
          "Schonfrist, Fehler bei 59 s (54 + spinup)",
          ev == [(59., fw.E_FEHLER)], ev)
    w = W(0.5, 5., 5., 300.)
    ev = laufe(w, sekunden(0, 50, 0.6, 3000.) + sekunden(51, 60, 1.0, 7500.))
    check("2 Erhöhen bei drehendem Lüfter: kein Ereignis", ev == [])
    # Absenken löst keine Schonfrist aus
    w = W(0.5, 5., 5., 300.)
    ev = laufe(w, sekunden(0, 50, 1.0, 7500.) + sekunden(51, 60, 0.7, 0.))
    check("2 Absenken (über min_speed) startet keine Schonfrist: Fehler nach "
          "5 s", ev == [(56., fw.E_FEHLER)], ev)
    # Bauteillüfter unter part_min_speed
    w = W(0.5, 5., 5., 300.)
    ev = laufe(w, sekunden(0, 300, 0.4, 0.))
    check("2 Bauteillüfter 0,4 (< part_min_speed 0,5) und 0 U/min: keine "
          "Bewertung", ev == [] and w.state == fw.Z_AUS)
    ev = laufe(w, sekunden(301, 320, 0.5, 0.))
    check("2 genau part_min_speed zählt als angesteuert (Fehler nach 10 s)",
          ev == [(311., fw.E_FEHLER)], ev)
    # Abschalten im Fehlerfall, neuer Fehlerfall
    ev = laufe(w, [(321., 0., 0.)])
    check("2 Abschalten im Fehlerfall -> 'ungeklärt', Fehler-Flag aus",
          ev == [(321., fw.E_UNGEKLAERT)] and not w.fault
          and w.state == fw.Z_AUS, ev)
    ev = laufe(w, sekunden(322, 400, 0., 0.))
    check("2 abgeschaltet: Ruhe", ev == [])
    ev = laufe(w, sekunden(401, 420, 1.0, 0.))
    check("2 erneutes Einschalten: neuer Fehlerfall nach 10 s (Zähler 2)",
          ev == [(411., fw.E_FEHLER)] and w.fault_count == 2, ev)
    # keine Messung
    w = W(0.95, 5., 5., 300.)
    ev = laufe(w, sekunden(0, 400, 1.0, None))
    check("2 keine Messung (rpm None): nie Fehler, genau ein Hinweis nach "
          "spinup+fault_time", ev == [(10., fw.E_KEINE_MESSUNG)]
          and not w.fault and w.state == fw.Z_KEINE_MESSUNG, ev)
    ev = laufe(w, sekunden(401, 403, 1.0, 0.) + sekunden(404, 405, 1.0, None)
               + sekunden(406, 409, 1.0, 0.))
    check("2 Messlücke unterbricht 'durchgehend 0': kein Fehler", ev == [],
          ev)
    # min_rpm
    w = W(0.95, 5., 5., 300., min_rpm=100.)
    ev = laufe(w, sekunden(0, 12, 1.0, 50.))
    check("2 min_rpm 100: 50 U/min zählt als Stillstand",
          ev == [(10., fw.E_FEHLER)], ev)
    w = W(0.95, 5., 5., 300.)
    ev = laufe(w, sekunden(0, 60, 1.0, 15.))
    check("2 min_rpm 0 (Default): 15 U/min zählt als drehend", ev == [])
    # spinup 0
    w = W(0.95, 0., 5., 300.)
    ev = laufe(w, sekunden(0, 6, 1.0, 0.))
    check("2 spinup_time 0: Fehler nach fault_time", ev == [(5., fw.E_FEHLER)],
          ev)
    # stalled_for
    w = W(0.95, 5., 5., 300.)
    laufe(w, sekunden(0, 8, 1.0, 0.))
    check("2 stalled_for zählt die Sekunden mit 0 U/min",
          w.stalled_for(8.) == 8. and w.stalled_for(None) == 0.)
    # ungültige Stärke
    w = W(0.95, 5., 5., 300.)
    ev = laufe(w, [(0., None, 0.), (1., "x", 0.), (2., 7.0, 8000.)])
    check("2 unlesbare Stärke = 0, Stärke > 1 wird auf 1 begrenzt",
          ev == [] and w.speed == 1.0)


# ---------------------------------------------------------------------------
# 3. Hülle, Modus report
# ---------------------------------------------------------------------------

def test_report():
    u = Umgebung()
    check("3 zwei Zähler angelegt (Hotend PA12, Bauteil PC13)",
          [z.pin for z in u.zaehler] == ["nozzle_mcu:PA12",
                                         "nozzle_mcu:PC13"])
    check("3 Timer nach ready registriert", len(u.reactor.timers) == 1)
    u.bis(400.)
    check("3 Lüfter dreht: keine Meldung in 300 s", u.gcode.raw == [],
          u.gcode.raw)
    st = u.status()
    check("3 get_status: mode, rpm, Stärke, Zustand, Fehler-Flag",
          st["mode"] == "report" and st["fault"] is False
          and abs(st["hotend"]["rpm"] - 8000.) < 0.5
          and st["hotend"]["speed"] == 1.0 and st["hotend"]["state"] == "ok"
          and st["hotend"]["fault"] is False and st["hotend"]["enabled"]
          and st["part"]["state"] == "aus" and st["part"]["speed"] == 0.
          and st["part"]["fault"] is False, st)
    # Hotend bleibt stehen (ab Start)
    u = Umgebung(ziel=220.)
    u.z_hotend.rpm(0)
    u.bis(110.5)
    check("3 Hotend 1,0 / 0 U/min: vor spinup+fault_time keine Meldung",
          u.gcode.raw == [], u.gcode.raw)
    check("3 … Zustand 'wartet'", u.status()["hotend"]["state"] == "wartet")
    u.bis(111.)
    rote = u.rote()
    check("3 … danach genau eine rote Meldung mit Präfix [k2_fanwatch]",
          len(rote) == 1 and rote[0].startswith("!! [k2_fanwatch] "
                                               "Hotend-Lüfter STEHT"), rote)
    check("3 … Meldung nennt 'nur Meldung (mode: report)'",
          "mode: report" in rote[0], rote)
    st = u.status()
    check("3 … get_status: fault True, Zustand 'fehler', Zähler 1",
          st["fault"] is True and st["hotend"]["fault"] is True
          and st["hotend"]["state"] == "fehler"
          and st["hotend"]["fault_count"] == 1
          and st["hotend"]["rpm"] == 0. and st["part"]["fault"] is False, st)
    u.bis(410.)
    check("3 report: keine PAUSE, Extruder-Ziel bleibt 220",
          u.gcode.skripte == [] and u.heater.target == 220.
          and u.heater.gesetzt == [])
    check("3 bis 410 s keine weitere Meldung", len(u.gcode.raw) == 1,
          u.gcode.raw)
    u.bis(411.)
    check("3 Wiederholmeldung nach repeat_interval (300 s)",
          len(u.rote()) == 2 and "steht weiterhin" in u.rote()[1], u.rote())
    u.z_hotend.rpm(7900)
    u.bis(412.)
    gelbe = u.gelbe()
    check("3 Drehzahl zurück -> Entwarnung",
          len(gelbe) == 1 and "dreht wieder" in gelbe[0]
          and "7900" in gelbe[0] and u.status()["fault"] is False, gelbe)
    # Drosselung beim Antasten
    u = Umgebung()
    u.bis(130.)
    u.hotend_fan.speed = 0.6
    u.z_hotend.rpm(0)
    u.bis(250.)
    check("3 Drosselung 0,6 (Tacho 0) über 120 s: keine Meldung",
          u.gcode.raw == [] and u.status()["hotend"]["state"] == "aus",
          u.gcode.raw)
    u.hotend_fan.speed = 1.0
    u.bis(253.)
    u.z_hotend.rpm(8000)
    u.bis(400.)
    check("3 zurück auf 1,0 (Tacho braucht 3 s): keine Meldung",
          u.gcode.raw == [], u.gcode.raw)
    # Bauteillüfter
    u = Umgebung()
    u.part_fan.value = 0.4
    u.z_part.rpm(0)
    u.bis(400.)
    check("3 Bauteillüfter 0,4 (< part_min_speed) / 0 U/min: keine Meldung",
          u.gcode.raw == [], u.gcode.raw)
    u.part_fan.value = 1.0
    u.bis(409.5)
    check("3 Bauteillüfter 1,0 / 0 U/min: vor 10 s keine Meldung",
          u.gcode.raw == [])
    u.bis(411.)
    check("3 … danach rote Meldung 'Bauteillüfter STEHT'",
          len(u.rote()) == 1 and "Bauteillüfter STEHT" in u.rote()[0],
          u.rote())
    st = u.status()
    check("3 … Status: part fault, hotend ok",
          st["part"]["fault"] and not st["hotend"]["fault"] and st["fault"])
    u.part_fan.value = 0.
    u.bis(412.)
    check("3 Bauteillüfter abgeschaltet -> Hinweis 'ungeklärt', Flag aus",
          len(u.gelbe()) == 1 and "ungeklärt" in u.gelbe()[0]
          and not u.status()["fault"], u.gelbe())
    u = Umgebung()
    u.part_fan.value = 1.0
    u.fan_en.value = 0.
    u.z_part.rpm(0)
    u.bis(300.)
    check("3 Bauteillüfter ohne Freigabe (fan0_en 0): keine Bewertung",
          u.gcode.raw == [] and u.status()["part"]["state"] == "aus",
          u.gcode.raw)
    u = Umgebung(druck="printing", ziel=220.)
    u.z_hotend.rpm(0)
    u.part_fan.value = 1.0
    u.z_part.rpm(0)
    u.bis(200.)
    check("3 report im Druck: beide Lüfter gemeldet, keine PAUSE, Ziel "
          "bleibt", len(u.rote()) == 2 and u.gcode.skripte == []
          and u.heater.target == 220. and u.stats.state == "printing",
          (u.rote(), u.gcode.skripte))


# ---------------------------------------------------------------------------
# 4. Modus off
# ---------------------------------------------------------------------------

def test_off():
    u = Umgebung({"mode": "off"}, druck="printing", ziel=220.)
    check("4 off: kein Zähler angelegt (kein Pin belegt)", u.zaehler == []
          and u.z_hotend is None and u.z_part is None)
    check("4 off: kein Timer", u.reactor.timers == [])
    u.bis(1000.)
    st = u.status()
    check("4 off: keine Meldung, keine Aktion", u.gcode.raw == []
          and u.gcode.skripte == [] and u.heater.target == 220.)
    check("4 off: get_status mode off, Lüfter 'deaktiviert', kein Fehler",
          st["mode"] == "off" and st["fault"] is False
          and st["hotend"]["state"] == "deaktiviert"
          and st["hotend"]["enabled"] is False
          and st["part"]["state"] == "deaktiviert"
          and st["hotend"]["rpm"] is None, st)
    check("4 off: Befehl KTWO_FANWATCH_STATUS trotzdem registriert",
          "KTWO_FANWATCH_STATUS" in u.gcode.commands)


# ---------------------------------------------------------------------------
# 5. Modus act
# ---------------------------------------------------------------------------

def test_act():
    act = {"mode": "act"}
    # außerhalb eines Drucks: nur Extruder-Ziel 0
    u = Umgebung(act, ziel=220.)
    u.z_hotend.rpm(0)
    u.bis(110.5)
    check("5 act, kein Druck: vor dem Fehler bleibt das Ziel 220",
          u.heater.target == 220. and u.gcode.skripte == [])
    u.bis(111.)
    check("5 act, kein Druck, Hotend steht: Extruder-Ziel 0, keine PAUSE",
          u.heater.target == 0. and u.heater.gesetzt == [0.]
          and u.gcode.skripte == [], (u.heater.gesetzt, u.gcode.skripte))
    check("5 … rote Meldungen: Fehler + 'Extruder-Ziel 220 °C auf 0'",
          len(u.rote()) == 2 and "Extruder-Ziel 220 °C auf 0" in u.rote()[1],
          u.rote())
    u.bis(150.)
    check("5 … Ziel wird nicht jede Sekunde neu gesetzt",
          u.heater.gesetzt == [0.])
    u.heater.target = 200.
    u.bis(151.)
    check("5 neues Ziel bei andauerndem Fehler -> wieder 0",
          u.heater.target == 0. and u.heater.gesetzt == [0., 0.])
    st = u.status()
    check("5 get_status: heater_off_count 2, last_heater_target 200, "
          "pause_count 0", st["heater_off_count"] == 2
          and st["last_heater_target"] == 200. and st["pause_count"] == 0, st)
    u = Umgebung(act, ziel=0.)
    u.z_hotend.rpm(0)
    u.bis(200.)
    check("5 act, kein Druck, Ziel 0: nur Meldung, set_temp nicht gerufen",
          len(u.rote()) == 1 and u.heater.gesetzt == []
          and u.gcode.skripte == [])
    # im Druck: PAUSE, danach Ziel 0
    u = Umgebung(act, druck="printing", ziel=220.)
    u.pause_makro()
    u.z_hotend.rpm(0)
    u.bis(110.5)
    check("5 act im Druck: vor dem Fehler keine PAUSE", u.gcode.skripte == [])
    u.bis(111.)
    check("5 act im Druck, Hotend steht: genau eine PAUSE",
          u.gcode.skripte == ["PAUSE"], u.gcode.skripte)
    check("5 … PAUSE läuft VOR dem Abschalten (Makro sieht noch 220 °C)",
          u.ziel_bei_pause == [220.], u.ziel_bei_pause)
    check("5 … danach Extruder-Ziel 0 (auch die Pausentemperatur des Makros)",
          u.heater.target == 0. and u.heater.gesetzt == [0.],
          u.heater.gesetzt)
    u.bis(400.)
    check("5 … pausiert: keine weitere PAUSE", u.gcode.skripte == ["PAUSE"])
    # Fortsetzen ohne Reparatur: kein zweites PAUSE im selben Fehlerfall
    u.stats.state = "printing"
    u.pause.is_paused = False
    u.bis(700.)
    check("5 Fortsetzen bei andauerndem Fehlerfall: PAUSE nur einmal je "
          "Fehlerfall", u.gcode.skripte == ["PAUSE"]
          and u.status()["pause_count"] == 1, u.gcode.skripte)
    # Entwarnung, neuer Fehlerfall -> neue PAUSE
    u.z_hotend.rpm(8000)
    u.bis(710.)
    u.heater.target = 220.
    u.z_hotend.rpm(0)
    u.bis(730.)
    check("5 neuer Fehlerfall nach Entwarnung: zweite PAUSE",
          u.gcode.skripte == ["PAUSE", "PAUSE"]
          and u.status()["hotend"]["fault_count"] == 2, u.gcode.skripte)
    # schon pausiert
    u = Umgebung(act, druck="paused", ziel=220.)
    u.pause.is_paused = True
    u.z_hotend.rpm(0)
    u.bis(300.)
    check("5 schon pausiert: keine PAUSE, aber Extruder-Ziel 0",
          u.gcode.skripte == [] and u.heater.target == 0.)
    u = Umgebung(act, druck="printing", ziel=220.)
    u.pause.is_paused = True
    u.z_hotend.rpm(0)
    u.bis(300.)
    check("5 pause_resume.is_paused trotz 'printing': keine PAUSE",
          u.gcode.skripte == [] and u.heater.target == 0.)
    for zustand in ("standby", "complete", "cancelled", "error"):
        u = Umgebung(act, druck=zustand, ziel=220.)
        u.z_hotend.rpm(0)
        u.part_fan.value = 1.0
        u.z_part.rpm(0)
        u.bis(300.)
        check("5 print_stats '%s': keine PAUSE" % zustand,
              u.gcode.skripte == [])
    # Fehler vor dem Druckstart, Druck beginnt später
    u = Umgebung(act, ziel=0.)
    u.pause_makro()
    u.part_fan.value = 1.0
    u.z_part.rpm(0)
    u.bis(200.)
    check("5 Bauteillüfter-Fehler ohne Druck: keine PAUSE",
          u.gcode.skripte == [] and len(u.rote()) == 1)
    u.stats.state = "printing"
    u.bis(202.)
    check("5 Druck startet bei andauerndem Fehler: PAUSE (einmal)",
          u.gcode.skripte == ["PAUSE"])
    # Bauteillüfter im Druck
    u = Umgebung(act, druck="printing", ziel=220.)
    u.pause_makro(ziel_danach=220.)
    u.part_fan.value = 1.0
    u.z_part.rpm(0)
    u.bis(300.)
    check("5 act im Druck, Bauteillüfter steht: genau eine PAUSE",
          u.gcode.skripte == ["PAUSE"], u.gcode.skripte)
    check("5 … Extruder-Ziel bleibt unangetastet (nur beim Hotend-Lüfter)",
          u.heater.gesetzt == [] and u.heater.target == 220.)
    # unter part_min_speed
    u = Umgebung(act, druck="printing", ziel=220.)
    u.part_fan.value = 0.3
    u.z_part.rpm(0)
    u.bis(600.)
    check("5 act im Druck, Bauteillüfter 0,3 / 0 U/min: nichts",
          u.gcode.skripte == [] and u.gcode.raw == [])
    # Drosselung im Druck (Antasten)
    u = Umgebung(act, druck="printing", ziel=220.)
    u.hotend_fan.speed = 0.6
    u.z_hotend.rpm(0)
    u.bis(600.)
    check("5 act im Druck, Hotend gedrosselt 0,6 / 0 U/min: nichts",
          u.gcode.skripte == [] and u.gcode.raw == []
          and u.heater.target == 220.)
    # beide gleichzeitig
    u = Umgebung(act, druck="printing", ziel=220.)
    u.pause_makro()
    u.z_hotend.rpm(0)
    u.part_fan.value = 1.0
    u.z_part.rpm(0)
    u.bis(300.)
    check("5 beide Lüfter stehen gleichzeitig: nur eine PAUSE",
          u.gcode.skripte == ["PAUSE"] and u.ziel_bei_pause == [220.]
          and u.heater.target == 0., u.gcode.skripte)
    # PAUSE hängt in der Warteschlange
    u = Umgebung(act, druck="printing", ziel=220.)
    u.pause_makro()
    u.reactor.halte_callbacks = True
    u.z_hotend.rpm(0)
    u.bis(111. + fw.PAUSE_VORRANG - 1.)
    check("5 PAUSE hängt: Ziel bleibt vorerst (PAUSE hat Vorrang)",
          u.heater.target == 220. and u.gcode.skripte == [])
    u.bis(111. + fw.PAUSE_VORRANG)
    check("5 PAUSE hängt länger als PAUSE_VORRANG: Ziel trotzdem 0",
          u.heater.target == 0., u.heater.target)
    u.reactor.halte_callbacks = False
    u.bis(111. + fw.PAUSE_VORRANG + 5.)
    check("5 … die PAUSE wird nachgeholt (einmal)",
          u.gcode.skripte == ["PAUSE"], u.gcode.skripte)
    # PAUSE wirft
    u = Umgebung(act, druck="printing", ziel=220.)

    def kaputt(script):
        raise RuntimeError("Makro defekt")
    u.gcode.hook = kaputt
    u.z_hotend.rpm(0)
    try:
        u.bis(300.)
        ok = True
    except Exception as e:
        ok = False
        print("  ", e)
    check("5 PAUSE wirft: kein Absturz, rote Meldung, Ziel trotzdem 0",
          ok and any("PAUSE fehlgeschlagen" in z for z in u.rote())
          and u.heater.target == 0. and u.gcode.skripte == ["PAUSE"],
          u.rote())
    check("5 … Timer läuft weiter", u.reactor.timers[0][1] > 300.)
    # ohne register_callback (anderer Reactor)
    u = Umgebung(act, druck="printing", ziel=220.)
    u.pause_makro()
    u.fw.reactor = types.SimpleNamespace(
        monotonic=u.reactor.monotonic, NEVER=u.reactor.NEVER)
    u.z_hotend.rpm(0)
    u.bis(300.)
    check("5 Reactor ohne register_callback: PAUSE direkt, dann Ziel 0",
          u.gcode.skripte == ["PAUSE"] and u.ziel_bei_pause == [220.]
          and u.heater.target == 0.)
    # nie Shutdown, nie etwas anderes als PAUSE
    alle = []
    for druck in ("printing", "standby"):
        u = Umgebung(act, druck=druck, ziel=220.)
        u.z_hotend.rpm(0)
        u.part_fan.value = 1.0
        u.z_part.rpm(0)
        u.bis(2000.)
        alle.append(u)
    check("5 act: nie Klipper-Shutdown", all(
        u.printer.shutdown_aufrufe == [] for u in alle))
    check("5 act: außer PAUSE wird kein G-Code ausgeführt (keine Motoren "
          "aus)", all(set(u.gcode.skripte) <= {"PAUSE"} for u in alle),
          [u.gcode.skripte for u in alle])
    with open(MODULE_PATH, encoding="utf-8") as f:
        code = f.read()
    check("5 Quelltext: kein invoke_shutdown, kein M84/M18/"
          "SET_STEPPER_ENABLE, einziger run_script ist PAUSE",
          "invoke_shutdown" not in code and "M84" not in code
          and "M18" not in code and "SET_STEPPER_ENABLE" not in code
          and re.findall(r"run_script\w*\((.*?)\)", code) == ['"PAUSE"'],
          re.findall(r"run_script\w*\((.*?)\)", code))


# ---------------------------------------------------------------------------
# 6. Fehlende Objekte, leerer Pin, unlesbare Werte
# ---------------------------------------------------------------------------

def test_fehlend():
    u = Umgebung(hotend=False)
    u.part_fan.value = 1.0
    u.z_part.rpm(0)
    try:
        u.bis(300.)
        ok = True
    except Exception as e:
        ok = False
        print("  ", e)
    st = u.status()
    check("6 Hotend-Lüfter-Objekt fehlt: kein Absturz", ok)
    check("6 … Hotend 'deaktiviert' mit Grund, genau eine Meldung dazu",
          st["hotend"]["enabled"] is False
          and st["hotend"]["state"] == "deaktiviert"
          and "heater_fan hotend_fan" in st["hotend"]["reason"]
          and len([z for z in u.gelbe() if "nicht gefunden" in z]) == 1,
          (st["hotend"], u.gelbe()))
    check("6 … der Bauteillüfter wird weiter überwacht",
          st["part"]["fault"] is True and len(u.rote()) == 1)
    u = Umgebung(part=False)
    u.z_hotend.rpm(0)
    u.bis(300.)
    st = u.status()
    check("6 Bauteillüfter-Objekt fehlt: deaktiviert + eine Meldung, Hotend "
          "weiter überwacht", st["part"]["enabled"] is False
          and len([z for z in u.gelbe() if "nicht gefunden" in z]) == 1
          and st["hotend"]["fault"] is True, (st, u.gelbe()))
    u = Umgebung(hotend=False, part=False)
    check("6 beide Objekte fehlen: kein Timer, zwei Meldungen",
          u.reactor.timers == []
          and len([z for z in u.gelbe() if "nicht gefunden" in z]) == 2)
    u.bis(300.)
    check("6 … danach Ruhe", len(u.gcode.raw) == 2)
    # leerer Tacho-Pin
    u = Umgebung({"hotend_tach_pin": ""}, ziel=220.)
    check("6 leerer hotend_tach_pin: nur der Bauteil-Zähler wird angelegt",
          [z.pin for z in u.zaehler] == ["nozzle_mcu:PC13"]
          and u.z_hotend is None)
    u.part_fan.value = 1.0
    u.z_part.rpm(0)
    u.bis(300.)
    st = u.status()
    check("6 … Hotend 'deaktiviert' (Grund: Pin leer), keine Meldung dazu, "
          "Bauteillüfter überwacht",
          st["hotend"]["state"] == "deaktiviert"
          and "hotend_tach_pin" in st["hotend"]["reason"]
          and u.gelbe() == [] and st["part"]["fault"] is True, st)
    u = Umgebung({"part_tach_pin": "", "hotend_tach_pin": ""})
    check("6 beide Pins leer: kein Zähler, kein Timer, keine Meldung",
          u.zaehler == [] and u.reactor.timers == [] and u.gcode.raw == [])
    u = Umgebung({"part_fan": ""})
    check("6 leerer part_fan: Bauteillüfter nicht überwacht, kein Zähler "
          "dafür", [z.pin for z in u.zaehler] == ["nozzle_mcu:PA12"]
          and u.status()["part"]["enabled"] is False)
    # Freigabe-Objekt fehlt
    u = Umgebung(freigabe=False)
    u.part_fan.value = 1.0
    u.z_part.rpm(0)
    u.bis(300.)
    check("6 Freigabe-Objekt fehlt: Bewertung allein nach der Stärke",
          u.status()["part"]["fault"] is True)
    u = Umgebung({"part_enable": ""})
    u.part_fan.value = 1.0
    u.fan_en.value = 0.
    u.z_part.rpm(0)
    u.bis(300.)
    check("6 part_enable leer: Freigabe wird nicht ausgewertet",
          u.status()["part"]["fault"] is True)
    # Heizer fehlt (act)
    u = Umgebung({"mode": "act"}, heizer=False, druck="printing")
    u.pause_makro()
    u.z_hotend.rpm(0)
    try:
        u.bis(300.)
        ok = True
    except Exception as e:
        ok = False
        print("  ", e)
    check("6 act ohne Heizer-Objekt: Warnung, PAUSE trotzdem, kein Absturz",
          ok and u.gcode.skripte == ["PAUSE"]
          and any("hotend_heater" in z for z in u.gelbe()), u.gelbe())
    u = Umgebung(heizer=False)
    check("6 report ohne Heizer-Objekt: keine Warnung (wird nicht gebraucht)",
          u.gcode.raw == [])
    # print_stats / pause_resume fehlen
    u = Umgebung({"mode": "act"}, ziel=220.)
    del u.printer.objects["print_stats"]
    del u.printer.objects["pause_resume"]
    u.z_hotend.rpm(0)
    u.bis(300.)
    check("6 act ohne print_stats/pause_resume: keine PAUSE, Ziel 0, kein "
          "Absturz", u.gcode.skripte == [] and u.heater.target == 0.)
    # Zähler lässt sich nicht anlegen
    try:
        Umgebung({"hotend_tach_pin": "kaputt:PA12"})
        msg = None
    except ConfigError as e:
        msg = str(e)
    check("6 Zähler lässt sich nicht anlegen: deutscher Config-Fehler",
          msg is not None and msg.startswith("[k2_fanwatch] hotend_tach_pin")
          and "kaputt:PA12" in msg and "lässt sich nicht anlegen" in msg, msg)
    # Stärke nicht lesbar
    u = Umgebung()
    u.hotend_fan.wirft = True
    u.z_hotend.rpm(0)
    try:
        u.bis(500.)
        ok = True
    except Exception as e:
        ok = False
        print("  ", e)
    check("6 get_status des Lüfters wirft: kein Absturz, kein Fehlalarm, "
          "eine entprellte Warnung", ok and u.rote() == []
          and len(u.gelbe()) == 1 and "nicht lesbar" in u.gelbe()[0],
          u.gcode.raw)
    u.bis(100. + fw.WARN_PAUSE + 5.)
    check("6 … Warnung wiederholt sich erst nach %d s" % fw.WARN_PAUSE,
          len(u.gelbe()) == 2, u.gelbe())
    # Zähler wirft
    u = Umgebung()
    u.z_hotend.wirft = True
    u.bis(300.)
    check("6 Zähler nicht lesbar: kein Fehlalarm, Warnungen entprellt",
          u.rote() == [] and len([z for z in u.gelbe()
                                  if "Drehzahl nicht lesbar" in z]) == 1,
          u.gcode.raw)
    # Zähler ohne Daten
    u = Umgebung()
    u.z_hotend._last_time = None
    u.z_hotend.rpm(0)
    u.bis(1000.)
    st = u.status()
    check("6 MCU liefert keine Zählerdaten: kein Fehlalarm, Zustand "
          "'keine_messung', rpm None, genau ein Hinweis",
          u.rote() == [] and st["hotend"]["state"] == "keine_messung"
          and st["hotend"]["rpm"] is None and len(u.gelbe()) == 1
          and "liefert keine Daten" in u.gelbe()[0], (st, u.gcode.raw))
    # Lüfter-Objekt ohne speed/value
    u = Umgebung()
    u.printer.objects["heater_fan hotend_fan"].get_status = lambda t: {}
    u.z_hotend.rpm(0)
    u.bis(300.)
    check("6 Lüfter-Status ohne 'speed'/'value': keine Bewertung + Warnung",
          u.rote() == [] and len(u.gelbe()) == 1)
    # Shutdown
    u = Umgebung()
    u.printer.shutdown = True
    u.printer.send_event("klippy:shutdown")
    u.z_hotend.rpm(0)
    u.bis(500.)
    check("6 Shutdown: Timer steht, keine Meldung",
          u.reactor.timers[0][1] == u.reactor.NEVER and u.gcode.raw == [])
    u = Umgebung()
    u.printer.shutdown = True
    u.z_hotend.rpm(0)
    u.bis(500.)
    check("6 Shutdown ohne Ereignis: Timer beendet sich selbst",
          u.reactor.timers[0][1] == u.reactor.NEVER and u.gcode.raw == [])
    # unerwarteter Fehler im Timer
    u = Umgebung()
    u.fw.hotend.waechter.update = lambda *a: 1 / 0
    try:
        u.bis(300.)
        ok = True
    except Exception as e:
        ok = False
        print("  ", e)
    check("6 unerwarteter Fehler im Timer: kein Wurf (kein Shutdown), "
          "entprellte Warnung, Timer läuft weiter",
          ok and len(u.gelbe()) == 1 and u.reactor.timers[0][1] > 300.
          and u.printer.shutdown_aufrufe == [], u.gcode.raw)


# ---------------------------------------------------------------------------
# 7. Zähler-Parameter, Drehzahl-Formel, Lesen der Stärke
# ---------------------------------------------------------------------------

def test_zaehler():
    u = Umgebung()
    z = u.z_hotend
    check("7 Zähler: Messfenster 1 s, Abtastung 0,0015 s (wie Upstream-"
          "Tachometer)", z.sample_time == 1.0 and z.poll_time == 0.0015)
    z._freq = 533.3333
    u.bis(102.)
    check("7 Drehzahl = Frequenz × 30 / ppr (533,3 Hz, ppr 2 -> 8000)",
          abs(u.status()["hotend"]["rpm"] - 8000.) < 0.1,
          u.status()["hotend"]["rpm"])
    u = Umgebung({"tach_ppr": "4", "tach_poll_interval": "0.003",
                  "hotend_tach_pin": "^nozzle_mcu:PA12"})
    u.z_hotend._freq = 533.3333
    u.bis(102.)
    check("7 tach_ppr 4 halbiert die Anzeige, tach_poll_interval und "
          "Pull-up-Schreibweise werden durchgereicht",
          abs(u.status()["hotend"]["rpm"] - 4000.) < 0.1
          and u.z_hotend.poll_time == 0.003
          and u.z_hotend.pin == "^nozzle_mcu:PA12")
    u = Umgebung()
    u.part_fan.value = 0.75
    u.z_part.rpm(5200)
    u.bis(102.)
    st = u.status()
    check("7 Stärke: heater_fan aus 'speed', output_pin aus 'value'",
          st["hotend"]["speed"] == 1.0 and st["part"]["speed"] == 0.75
          and abs(st["part"]["rpm"] - 5200.) < 0.5
          and st["part"]["state"] == "ok", st)
    u = Umgebung({"hotend_fan": "fan_generic kopf", "part_fan": "fan"},
                 hotend=False, part=False,
                 objekte={"fan_generic kopf": FakeFan(1.0),
                          "fan": FakeFan(0.8)})
    u.bis(102.)
    st = u.status()
    check("7 andere Lüfter-Abschnitte (fan, fan_generic) über 'speed'",
          st["hotend"]["speed"] == 1.0 and st["part"]["speed"] == 0.8, st)
    u = Umgebung({"poll_interval": "2", "fault_time": "4",
                  "spinup_time": "2"})
    u.z_hotend.rpm(0)
    u.bis(107.5)
    n = len(u.rote())
    u.bis(108.)
    check("7 poll_interval 2 / spinup 2 / fault 4: Fehler 6 s nach dem "
          "ersten Abfragen", n == 0 and len(u.rote()) == 1, u.rote())


# ---------------------------------------------------------------------------
# 8. Befehl
# ---------------------------------------------------------------------------

def test_befehl():
    u = Umgebung({"mode": "act"}, ziel=220.)
    namen = sorted(u.gcode.commands)
    check("8 registrierter Befehl: KTWO_FANWATCH_STATUS",
          namen == ["KTWO_FANWATCH_STATUS"], namen)
    check("8 Befehlsnamen nur Großbuchstaben und Unterstriche (keine "
          "Ziffern)", all(re.match(r"^[A-Z_]+$", n) for n in namen), namen)
    quelle = open(MODULE_PATH, encoding="utf-8").read()
    reg = re.findall(r"register_(?:mux_)?command\(\s*\"([^\"]+)\"", quelle)
    check("8 Quelltext: alle register_command-Namen ohne Ziffern",
          reg == ["KTWO_FANWATCH_STATUS"]
          and all(re.match(r"^[A-Z_]+$", n) for n in reg), reg)
    u.bis(105.)
    g = FakeGcmd()
    u.gcode.commands["KTWO_FANWATCH_STATUS"](g)
    text = "\n".join(g.info)
    check("8 Status-Ausgabe: Modus, beide Lüfter, Drehzahl, Zustand",
          "Modus act" in text and "Hotend-Lüfter: Zustand ok" in text
          and "8000 U/min" in text and "Bauteillüfter: Zustand aus" in text
          and "Fehler nein" in text and "nozzle_mcu:PA12" in text
          and all(z.startswith("[k2_fanwatch]") for z in text.split("\n")),
          text)
    u.z_hotend.rpm(0)
    u.bis(200.)
    g = FakeGcmd()
    u.gcode.commands["KTWO_FANWATCH_STATUS"](g)
    text = "\n".join(g.info)
    check("8 Status im Fehlerfall: 'Zustand fehler', 'Fehler JA', Zähler",
          "Hotend-Lüfter: Zustand fehler" in text and "Fehler JA" in text
          and "Extruder-Ziel 0 gesetzt: 1×" in text, text)
    u = Umgebung({"hotend_tach_pin": ""})
    g = FakeGcmd()
    u.gcode.commands["KTWO_FANWATCH_STATUS"](g)
    check("8 Status nennt nicht überwachte Lüfter mit Grund",
          "Hotend-Lüfter: nicht überwacht (hotend_tach_pin ist leer)"
          in "\n".join(g.info), g.info)
    u = Umgebung({"mode": "off"})
    g = FakeGcmd()
    u.gcode.commands["KTWO_FANWATCH_STATUS"](g)
    check("8 Status im Modus off", "Modus off" in g.info[0]
          and "nicht überwacht (mode: off)" in g.info[0], g.info)
    u = Umgebung()
    u.z_hotend._last_time = None
    u.bis(105.)
    g = FakeGcmd()
    u.gcode.commands["KTWO_FANWATCH_STATUS"](g)
    check("8 Status ohne Zählerdaten: 'keine Messung'",
          "keine Messung" in g.info[0], g.info)


# ---------------------------------------------------------------------------
# 9. Ohne k2_konsole
# ---------------------------------------------------------------------------

def test_ohne_konsole():
    alt = sys.modules.get("k2_konsole", _FEHLT)
    sys.modules["k2_konsole"] = None      # import schlägt fehl
    try:
        mod = lade_modul("k2_fanwatch_ohne_konsole")
    finally:
        if alt is _FEHLT:
            del sys.modules["k2_konsole"]
        else:
            sys.modules["k2_konsole"] = alt
    check("9 Modul lädt ohne k2_konsole", mod._K2K is None)
    check("9 mit k2_konsole wird sie benutzt", fw._K2K is not None
          and type(Umgebung().fw.konsole).__name__ == "Konsole")
    u = Umgebung({"mode": "act"}, modul=mod, druck="printing", ziel=220.)
    u.pause_makro()
    check("9 Ersatz-Konsole aktiv",
          type(u.fw.konsole).__name__ == "_EinfacheKonsole")
    u.z_hotend.rpm(0)
    u.bis(200.)
    check("9 ohne k2_konsole: rote Meldung '!! [k2_fanwatch] …', PAUSE, "
          "Ziel 0", u.rote() and u.rote()[0].startswith(
              "!! [k2_fanwatch] Hotend-Lüfter STEHT")
          and u.gcode.skripte == ["PAUSE"] and u.heater.target == 0.,
          u.gcode.raw)
    u.z_hotend.rpm(8000)
    u.bis(205.)
    check("9 ohne k2_konsole: Entwarnung '// [k2_fanwatch] …'",
          any(z.startswith("// [k2_fanwatch] Hotend-Lüfter dreht wieder")
              for z in u.gelbe()), u.gelbe())
    k = mod._EinfacheKonsole(types.SimpleNamespace(
        lookup_object=lambda *a: 1 / 0), "k2_fanwatch")
    try:
        ok = k.fehler("x") is False and k.warnung("x") is False
    except Exception:
        ok = False
    check("9 Ersatz-Konsole wirft nie (auch ohne gcode-Objekt)", ok)


# ---------------------------------------------------------------------------
# 10. Syntax, Modulkopf, Doku-Regeln
# ---------------------------------------------------------------------------

def test_regeln():
    for path in (MODULE_PATH, os.path.abspath(__file__)):
        with open(path, encoding="utf-8") as f:
            src = f.read()
        try:
            ast.parse(src, path, feature_version=(3, 9))
            ok = True
        except SyntaxError as e:
            ok = False
            print("  ", e)
        check("10 Python-3.9-Syntax %s" % os.path.basename(path), ok)
    with open(MODULE_PATH, encoding="utf-8") as f:
        modul = f.read()
    kopf = ast.get_docstring(ast.parse(modul)) or ""
    check("10 Modulkopf nennt die Herkunft der Idee",
          "Idee aus dem Projekt K2 OpenKlipper (GPLv3), kein Code übernommen"
          in kopf)
    check("10 Modul importiert nur Standardbibliothek/Klipper/k2_konsole",
          sorted(set(re.findall(r"^\s*(?:import|from)\s+([\w.]+)", modul,
                                re.M))) == [".", "k2_konsole", "logging",
                                            "pulse_counter"],
          sorted(set(re.findall(r"^\s*(?:import|from)\s+([\w.]+)", modul,
                                re.M))))
    verboten = [
        ("Rev" + "erse Engineering", re.I), ("Ghi" + "dra", re.I),
        ("Disas" + "sembl", re.I), ("Dekom" + "pil", re.I),
        (r"\b(?:K" + r"I|A" + r"I)\b", 0), ("Assis" + "tent", re.I),
        ("Cl" + "aude", re.I), ("Chat" + "GPT", re.I), ("Cop" + "ilot", re.I),
        (r"/(?:ho)me/|/(?:ro)ot/", 0),
        (r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b", 0),
    ]
    for path in (MODULE_PATH, README_PATH, os.path.abspath(__file__)):
        name = os.path.basename(path)
        if not os.path.isfile(path):
            check("10 vorhanden %s" % name, False)
            continue
        with open(path, encoding="utf-8") as f:
            src = f.read()
        treffer = [m for m, fl in verboten if re.search(m, src, fl)]
        check("10 keine verbotenen Begriffe, Pfade oder IP-Adressen in %s"
              % name, treffer == [], treffer)
    if os.path.isfile(README_PATH):
        with open(README_PATH, encoding="utf-8") as f:
            readme = f.read()
        optionen = (["mode"] + [n for n, _ in fw.TEXT_OPTIONEN]
                    + [z[0] for z in fw.ZAHL_OPTIONEN])
        fehlen = [o for o in optionen if "`%s`" % o not in readme]
        check("10 README nennt jede Option", fehlen == [], fehlen)
        mm = re.search(r"```ini\n(.*?)```", readme, re.S)
        block = mm.group(1) if mm else ""
        werte = {}
        for zeile in block.split("\n"):
            zeile = zeile.split("#")[0].strip()
            if ":" in zeile and not zeile.startswith("["):
                k, v = zeile.split(":", 1)
                werte[k.strip()] = v.strip()
        try:
            opt = fw.lese_optionen(FakeConfig(None, werte))
            ok = (opt == fw.lese_optionen(FakeConfig(None, {}))
                  and "[k2_fanwatch]" in block and len(werte) >= 12)
        except ConfigError as e:
            ok = False
            print("  ", e)
        check("10 README-Config-Ausschnitt ist gültig und entspricht den "
              "Defaults", ok, werte)
        check("10 README nennt den Befehl und die Einführungsreihenfolge",
              "KTWO_FANWATCH_STATUS" in readme and "report" in readme
              and "act" in readme)


def main():
    logging.disable(logging.CRITICAL)
    for fn in (test_optionen, test_waechter, test_report, test_off, test_act,
               test_fehlend, test_zaehler, test_befehl, test_ohne_konsole,
               test_regeln):
        print("== %s" % fn.__name__)
        try:
            fn()
        except Exception as e:
            import traceback
            traceback.print_exc()
            check("%s ohne Ausnahme" % fn.__name__, False,
                  "%s: %s" % (type(e).__name__, e))
    bad = [r for r in RESULTS if not r[1]]
    print("")
    for name, ok, detail in bad:
        print("FEHLER:", name, detail)
    if bad:
        print("ERGEBNIS: %d FEHLER" % len(bad))
        sys.exit(1)
    print("ERGEBNIS: ok (%d/%d ok)" % (len(RESULTS), len(RESULTS)))


if __name__ == "__main__":
    main()
