#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_k2_cfs_sensor.py – CFS-Box-Klima als Temperatursensor (offline).

Prüft: [k2_cfs_sensor] registriert sensor_type k2_cfs, der Sensor gibt die
zuletzt von k2_box gepollten Werte weiter (kein Bus), ohne Box / ohne Poll /
veraltet / außerhalb min-max KEIN Callback und KEIN Shutdown, der Mainsail-
Alias „aht10 <name>“ liefert temperature + humidity, Takt = poll_interval,
Python-3.9-Syntax.
"""
import ast
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
import k2_cfs_sensor as m  # noqa: E402

ok = 0
fehler = []


def check(name, cond, info=""):
    global ok
    if cond:
        ok += 1
        print("ok", name)
    else:
        fehler.append(name)
        print("FEHLER", name, info)


class ConfigError(Exception):
    pass


class FakeReactor:
    NOW = 0.0
    NEVER = 9e99

    def __init__(self):
        self.timers = []

    def register_timer(self, cb, when):
        self.timers.append((cb, when))
        return cb


class FakeMcu:
    def estimated_print_time(self, eventtime):
        return eventtime + 1000.0


class FakeHeaters:
    def __init__(self):
        self.factories = {}

    def add_sensor_factory(self, name, fn):
        self.factories[name] = fn


class FakePrinter:
    config_error = ConfigError

    def __init__(self):
        self.objects = {"mcu": FakeMcu()}
        self.reactor = FakeReactor()
        self.heaters = FakeHeaters()
        self.handlers = {}
        self.shutdowns = []
        self.start_args = {}

    def get_reactor(self):
        return self.reactor

    def get_start_args(self):
        return self.start_args

    def load_object(self, config, name):
        assert name == "heaters"
        return self.heaters

    def add_object(self, name, obj):
        if name in self.objects:
            raise ConfigError("Printer object '%s' already created" % name)
        self.objects[name] = obj

    def lookup_object(self, name, default=KeyError):
        if name in self.objects:
            return self.objects[name]
        if default is KeyError:
            raise ConfigError("unbekannt %s" % name)
        return default

    def register_event_handler(self, ev, fn):
        self.handlers.setdefault(ev, []).append(fn)

    def invoke_shutdown(self, msg):
        self.shutdowns.append(msg)


class FakeConfig:
    def __init__(self, printer, name, opts=None):
        self.printer, self.name, self.opts = printer, name, dict(opts or {})

    def get_printer(self):
        return self.printer

    def get_name(self):
        return self.name

    def get(self, key, default=None):
        return self.opts.get(key, default)

    def getfloat(self, key, default=None, above=None, **kw):
        v = self.opts.get(key, default)
        return None if v is None else float(v)


class _Ctl:
    def __init__(self):
        self.last_poll = None


class FakeBox:
    """[k2_box] mit Status-Cache wie K2BoxController.get_status."""

    def __init__(self, poll_interval=6.0):
        self.cfg = {"poll_interval": poll_interval}
        self.controller = _Ctl()
        self.temp, self.rh = None, None
        self.raise_on_status = False

    def poll(self, temp, rh):
        self.temp, self.rh = temp, rh
        self.controller.last_poll = {"kind": "state", "temp_c": temp,
                                     "rh": rh}

    def get_status(self, eventtime=None):
        if self.raise_on_status:
            raise RuntimeError("Testfehler")
        return {"temp_c": self.temp, "rh": self.rh}


def aufbau(box=True, opts=None, sensor_opts=None):
    pr = FakePrinter()
    if box:
        pr.objects["k2_box"] = FakeBox()
    reg = m.load_config(FakeConfig(pr, "k2_cfs_sensor", opts))
    pr.objects["k2_cfs_sensor"] = reg
    so = {"sensor_type": "k2_cfs", "min_temp": "-10", "max_temp": "80"}
    so.update(sensor_opts or {})
    sensor = pr.heaters.factories["k2_cfs"](
        FakeConfig(pr, "temperature_sensor cfs_box", so))
    sensor.setup_minmax(-10.0, 80.0)
    calls = []
    sensor.setup_callback(lambda t, v: calls.append((t, v)))
    for fn in pr.handlers.get("klippy:connect", []):
        fn()
    return pr, reg, sensor, calls


def test_registrierung_und_takt():
    pr, reg, sensor, _ = aufbau()
    check("Factory k2_cfs registriert", "k2_cfs" in pr.heaters.factories)
    check("Timer bei klippy:connect", len(pr.reactor.timers) == 1)
    check("Takt = poll_interval der Box", sensor.get_report_time_delta() == 6.0)
    pr2, _r, s2, _c = aufbau(opts={"report_time": "2.5"})
    check("Takt report_time überstimmt", s2.get_report_time_delta() == 2.5)
    pr3, _r, s3, _c = aufbau(box=False)
    check("Takt ohne Box = 4 s", s3.get_report_time_delta() == 4.0)
    pr4 = FakePrinter()
    pr4.start_args = {"debugoutput": "x"}
    reg4 = m.load_config(FakeConfig(pr4, "k2_cfs_sensor"))
    reg4._factory(FakeConfig(pr4, "temperature_sensor cfs_box"))
    for fn in pr4.handlers.get("klippy:connect", []):
        fn()
    check("Batch/debugoutput: kein Timer", pr4.reactor.timers == [])


def test_wert_und_alias():
    pr, reg, sensor, calls = aufbau()
    box = pr.objects["k2_box"]
    box.poll(25, 47)
    nxt = sensor._sample(100.0)
    check("Callback mit Poll-Temperatur", calls == [(1100.0, 25.0)], calls)
    check("nächster Takt", nxt == 106.0, nxt)
    alias = pr.objects["aht10 cfs_box"]
    check("Alias temperature+humidity",
          alias.get_status(100.0) == {"temperature": 25.0, "humidity": 47},
          alias.get_status(100.0))
    st = reg.get_status(100.0)["sensors"]["cfs_box"]
    check("Status gültig", st["valid"] and st["humidity"] == 47
          and st["alias"] == "aht10 cfs_box", st)
    check("kein Shutdown", pr.shutdowns == [])


def test_ohne_werte_kein_absturz():
    pr, reg, sensor, calls = aufbau(box=False)
    sensor._sample(10.0)
    st = reg.get_status(10.0)["sensors"]["cfs_box"]
    check("ohne Box: kein Callback", calls == [])
    check("ohne Box: Grund", not st["valid"] and "k2_box" in st["reason"], st)
    check("ohne Box: Alias leer", pr.objects["aht10 cfs_box"].get_status(10.0) == {})
    pr, reg, sensor, calls = aufbau()
    sensor._sample(10.0)                      # Box da, aber noch kein Poll
    check("ohne Poll: kein Callback", calls == [])
    pr.objects["k2_box"].raise_on_status = True
    sensor._sample(14.0)
    check("Statusfehler: kein Absturz/Shutdown",
          calls == [] and pr.shutdowns == []
          and "Lesefehler" in reg.get_status(14.0)["sensors"]["cfs_box"]["reason"])


def test_grenzen_und_veraltet():
    pr, reg, sensor, calls = aufbau()
    box = pr.objects["k2_box"]
    box.poll(120, 30)                         # außerhalb max_temp 80
    sensor._sample(10.0)
    st = reg.get_status(10.0)["sensors"]["cfs_box"]
    check("außerhalb max: kein Callback, kein Shutdown",
          calls == [] and pr.shutdowns == [] and not st["valid"]
          and "außerhalb" in st["reason"], st)
    box.poll(24, 180)                         # Feuchte unplausibel
    sensor._sample(14.0)
    check("Feuchte > 100: Temp gültig, Feuchte None",
          calls[-1][1] == 24.0 and sensor.rh is None
          and pr.objects["aht10 cfs_box"].get_status(14.0) == {"temperature": 24.0})
    box.poll(26, 45)
    sensor._sample(20.0)
    n = len(calls)
    sensor._sample(70.0)                      # gleicher Poll, 50 s alt
    check("50 s alt noch gültig", len(calls) == n + 1)
    sensor._sample(90.0)                      # 70 s alt > max_age 60
    st = reg.get_status(90.0)["sensors"]["cfs_box"]
    check("veraltet: kein Callback", len(calls) == n + 1 and not st["valid"]
          and "veraltet" in st["reason"], st)
    check("veraltet: Alias leer", pr.objects["aht10 cfs_box"].get_status(90.0) == {})
    box.poll(26, 44)                          # frischer Poll
    sensor._sample(94.0)
    check("frischer Poll wieder gültig", len(calls) == n + 2
          and reg.get_status(94.0)["sensors"]["cfs_box"]["valid"])


def test_alias_kollision():
    pr = FakePrinter()
    pr.objects["k2_box"] = FakeBox()
    pr.objects["aht10 cfs_box"] = object()    # schon vorhanden
    reg = m.load_config(FakeConfig(pr, "k2_cfs_sensor"))
    s = reg._factory(FakeConfig(pr, "temperature_sensor cfs_box"))
    check("Alias-Kollision: kein Fehler, kein Alias", s.alias is None)


class FakeGcode:
    def __init__(self):
        self.raw = []

    def respond_raw(self, z):
        self.raw.append(z)


def test_konsole():
    """Konsolen-Audit 23.10.2026: Lesefehler + gültig->ungültig in Mainsail."""
    pr, reg, sensor, calls = aufbau()
    g = pr.objects["gcode"] = FakeGcode()
    sensor._sample(10.0)                      # ohne Poll: von Anfang an leer
    sensor.konsole.flush()                    # = klippy:ready + FLUSH_DELAY
    check("Konsole: Startzustand ohne Wert meldet nichts", g.raw == [], g.raw)
    box = pr.objects["k2_box"]
    box.poll(26, 45)
    sensor._sample(20.0)
    sensor._sample(90.0)                      # veraltet -> ungültig
    check("Konsole: gültig->ungültig als // [k2_cfs_sensor]",
          len(g.raw) == 1 and g.raw[0].startswith("// [k2_cfs_sensor] Sensor cfs_box")
          and "veraltet" in g.raw[0], g.raw)
    box.raise_on_status = True
    sensor._sample(94.0)
    sensor._sample(98.0)
    check("Konsole: Lesefehler einmal (entprellt)",
          len(g.raw) == 2 and "Lesefehler" in g.raw[1], g.raw)


def test_py39_und_stats():
    for f in ("k2_cfs_sensor.py", "test_k2_cfs_sensor.py"):
        ast.parse(open(os.path.join(HIER, f), encoding="utf-8").read(),
                  feature_version=(3, 9))
    check("Python-3.9-Syntax", True)
    check("keine stats-Methoden (Klipper [statistics])",
          not any(hasattr(c, "stats") for c in (
              m.K2CfsSensor, m.K2CfsHumidityAlias, m.K2CfsSensorRegistry)))


TESTS = [test_registrierung_und_takt, test_wert_und_alias,
         test_ohne_werte_kein_absturz, test_grenzen_und_veraltet,
         test_alias_kollision, test_konsole, test_py39_und_stats]

if __name__ == "__main__":
    for t in TESTS:
        t()
    print("ERGEBNIS: %s (%d/%d ok)" % ("ok" if not fehler else "FEHLER",
                                      ok, ok + len(fehler)))
    sys.exit(1 if fehler else 0)
