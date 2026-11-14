#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests für k2_abbruch.py (Sofort-Abbruch, 14.11.2026). Ohne Klipper."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import k2_abbruch as A  # noqa: E402

PASS, FAIL = [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s %s" % ("PASS" if cond else "FAIL", name,
                        detail if not cond else ""))


class Uhr:
    t = 1000.0

    def __call__(self):
        return Uhr.t


def test_logik():
    z = A.AbbruchZustand(clock=Uhr())
    z.druckzustand("standby")
    check("kein Druck: Abbruch merkt nichts, Original cancel",
          z.anfrage(A.KIND_CANCEL, "standby") == A.KIND_CANCEL
          and z.offen is None)
    z.druckzustand("printing")
    check("Druckbeginn -> Vorbereitung", z.vorbereitung)
    check("Pause in der Vorbereitung -> Abbruch",
          z.anfrage(A.KIND_PAUSE, "printing") == A.KIND_CANCEL
          and z.offen == A.PENDING_PAUSE)
    Uhr.t += 12
    r = z.ausloesen(gesperrt=True)
    check("CFS-Kette aktiv: bleibt offen", r is None and z.offen is not None)
    r = z.ausloesen()
    check("Auslösen einmalig mit Zeit", r == (A.PENDING_PAUSE, 12.0)
          and z.offen is None and z.ausloesen() is None, r)
    z.start_fertig()
    check("Pause im normalen Druck bleibt Pause",
          z.anfrage(A.KIND_PAUSE, "printing") == A.KIND_PAUSE
          and z.offen is None)
    z.anfrage(A.KIND_CANCEL, "printing")
    check("Abbruch im normalen Druck -> offen", z.offen == A.PENDING_CANCEL)
    z.druckzustand("paused")
    check("pausiert: Merker bleibt", z.offen == A.PENDING_CANCEL)
    z.druckzustand("cancelled")
    check("Druck vorbei: Merker weg, keine Vorbereitung",
          z.offen is None and not z.vorbereitung)
    z.druckzustand("paused")
    z.druckzustand("printing")
    check("paused -> printing ist kein neuer Druck", not z.vorbereitung)
    z.anfrage(A.KIND_CANCEL, "paused")
    check("Abbruch in der Pause: nichts vormerken (läuft sofort)",
          z.offen is None)
    z.anfrage(A.KIND_CANCEL, "printing")
    z.reset()
    check("RESET verwirft", z.offen is None)
    check("Text", "nach G28" in A.abbruch_text("Abbruch", "nach G28", 3.4)
          and "3 s" in A.abbruch_text("Abbruch", "x", 3.4))


# --- Klipper-Fakes ------------------------------------------------------
class CmdError(Exception):
    pass


class FakeReactor:
    NOW = 0.0

    def __init__(self):
        self.timers = []

    def monotonic(self):
        return 5.0

    def register_timer(self, cb, when):
        self.timers.append(cb)


class FakeGcode:
    def __init__(self):
        self.cmds, self.infos = {}, []

    def register_command(self, name, fn, desc=None):
        self.cmds[name] = fn

    def respond_info(self, m):
        self.infos.append(m)


class FakeObj:
    def __init__(self, st):
        self.st = st

    def get_status(self, et):
        return dict(self.st)


class FakeHeater:
    def __init__(self):
        self.busy = True

    def check_busy(self, et):
        return self.busy


class FakeCore:
    abort_check = None


class FakeProbe:
    core = FakeCore()


class FakePrinter:
    command_error = CmdError

    def __init__(self):
        self.reactor = FakeReactor()
        self.gcode = FakeGcode()
        self.calls = []
        self.ps = FakeObj({"state": "printing"})
        self.kb = FakeObj({"chain_active": False})
        self.ext = FakeHeater()
        self.bed = FakeHeater()
        self.probe = FakeProbe()
        wh = type("WH", (), {})()
        wh._endpoints = {
            "pause_resume/cancel": lambda r: self.calls.append("CANCEL"),
            "pause_resume/pause": lambda r: self.calls.append("PAUSE")}
        self.wh = wh
        heaters = type("H", (), {})()
        heaters.heaters = {"extruder": self.ext, "heater_bed": self.bed}
        self.heaters = heaters
        self.handlers = {}

    def get_reactor(self):
        return self.reactor

    def register_event_handler(self, ev, cb):
        self.handlers[ev] = cb

    def lookup_object(self, name, default=None):
        return {"gcode": self.gcode, "webhooks": self.wh,
                "print_stats": self.ps, "k2_box": self.kb,
                "heaters": self.heaters, "probe": self.probe}.get(name,
                                                                 default)


class FakeConfig:
    def __init__(self, p):
        self.p = p

    def get_printer(self):
        return self.p


class FakeGcmd:
    def __init__(self, params):
        self.params = params
        self.infos = []

    def get(self, k, default=None):
        return self.params.get(k, default)

    def respond_info(self, m):
        self.infos.append(m)

    def error(self, m):
        return CmdError(m)


def make():
    p = FakePrinter()
    mod = A.K2Abbruch(FakeConfig(p))
    p.handlers["klippy:ready"]()
    return p, mod


def raises(fn):
    try:
        fn()
        return None
    except CmdError as e:
        return str(e)


def test_klipper():
    p, mod = make()
    eps = p.wh._endpoints
    check("Webhooks, Heizungen, Probe eingehängt",
          set(mod.hooks) >= {"pause_resume/cancel", "pause_resume/pause",
                             "heater extruder", "heater heater_bed", "probe"}
          and p.probe.core.abort_check is not None, mod.hooks)
    # Druckbeginn (Poll) -> Vorbereitung; Pause im Start -> Abbruch
    p.reactor.timers[0](5.0)
    eps["pause_resume/pause"](None)
    check("Pause im Druckstart ruft Original-CANCEL, Meldung sofort",
          p.calls == ["CANCEL"] and mod.z.offen == A.PENDING_PAUSE
          and any("angefordert" in m for m in p.gcode.infos), p.calls)
    # Heiz-Warten bricht ab (einmalig)
    e = raises(lambda: p.ext.check_busy(1.0))
    check("M109-Warten bricht mit G-Code-Fehler ab",
          e is not None and "Warten auf extruder" in e, e)
    check("danach normales Warten (einmalig)",
          raises(lambda: p.ext.check_busy(1.0)) is None
          and p.ext.check_busy(1.0) is True)
    check("Status letzter Abbruch",
          mod.get_status()["letzter"]["wo"] == "Warten auf extruder")
    # Heizung schon am Ziel -> nicht abbrechen (busy False)
    eps["pause_resume/cancel"](None)
    p.bed.busy = False
    check("Heizung am Ziel: kein Abbruch, Merker bleibt",
          raises(lambda: p.bed.check_busy(1.0)) is None
          and mod.z.offen == A.PENDING_CANCEL)
    # CFS-Kette aktiv -> nicht abbrechen
    p.kb.st["chain_active"] = True
    check("CFS-Kette aktiv: kein Abbruch",
          raises(lambda: p.ext.check_busy(1.0)) is None
          and mod.z.offen == A.PENDING_CANCEL)
    p.kb.st["chain_active"] = False
    # Antasten-Hook
    e = raises(p.probe.core.abort_check)
    check("Antasten bricht ab", e is not None and "Antasten" in e, e)
    # Startschritt-Befehl
    eps["pause_resume/cancel"](None)
    cmd = p.gcode.cmds["KTWO_ABBRUCH"]
    e = raises(lambda: cmd(FakeGcmd({"AKTION": "PRUEFEN",
                                     "WO": "nach G28"})))
    check("KTWO_ABBRUCH AKTION=PRUEFEN bricht ab", e is not None
          and "nach G28" in e, e)
    check("ohne offenen Abbruch: PRUEFEN tut nichts",
          raises(lambda: cmd(FakeGcmd({"AKTION": "PRUEFEN"}))) is None)
    eps["pause_resume/cancel"](None)
    cmd(FakeGcmd({"AKTION": "RESET"}))
    check("AKTION=RESET verwirft (CANCEL_PRINT-Aufräumen heizt normal)",
          mod.z.offen is None and raises(lambda: p.ext.check_busy(1.0))
          is None)
    check("unbekannte AKTION -> Fehler",
          raises(lambda: cmd(FakeGcmd({"AKTION": "XYZ"}))) is not None)
    g = FakeGcmd({})
    cmd(g)
    check("Statusausgabe", g.infos and "offen: nein" in g.infos[0], g.infos)
    # Pause im normalen Druck: Original-PAUSE, nichts offen
    cmd(FakeGcmd({"AKTION": "START_FERTIG"}))
    p.calls.clear()
    eps["pause_resume/pause"](None)
    check("Pause im normalen Druck = Pause", p.calls == ["PAUSE"]
          and mod.z.offen is None, p.calls)
    # kein Druck: Abbruch wird nicht vorgemerkt
    p.ps.st["state"] = "standby"
    p.reactor.timers[0](6.0)
    eps["pause_resume/cancel"](None)
    check("ohne Druck nichts vorgemerkt", mod.z.offen is None)


def test_cfg_makros():
    """macros.cfg/homing.cfg/k2-module.cfg enthalten die Abbruchstellen."""
    d = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config-pi")
    m = open(os.path.join(d, "macros.cfg"), encoding="utf-8").read()
    h = open(os.path.join(d, "homing.cfg"), encoding="utf-8").read()
    k = open(os.path.join(d, "k2-module.cfg"), encoding="utf-8").read()
    sp = m[m.index("[gcode_macro START_PRINT]"):
           m.index("[gcode_macro _PI_ABBRUCH_PUNKT]")]
    n = sp.count("_PI_ABBRUCH_PUNKT WO=")
    check("START_PRINT: START, 8 Abbruchstellen, START_FERTIG",
          "_PI_ABBRUCH_PUNKT AKTION=START\n" in sp
          and "_PI_ABBRUCH_PUNKT AKTION=START_FERTIG" in sp and n == 8, n)
    i_start = sp.index("AKTION=START\n")
    check("START vor dem ersten Befehl", i_start < sp.index("  G90"))
    cp = m[m.index("[gcode_macro CANCEL_PRINT]"):]
    check("CANCEL_PRINT: RESET vor END_PRINT",
          cp.index("AKTION=RESET") < cp.index("END_PRINT CANCEL=1"))
    rs = m[m.index("[gcode_macro _PI_RESET_STATE]"):]
    check("_PI_RESET_STATE: RESET zuerst",
          rs.index("AKTION=RESET") < rs.index("TURN_OFF_HEATERS"))
    check("homing_override: Abbruchstelle vor G28 Z",
          h.index('_PI_ABBRUCH_PUNKT WO="vor G28 Z"') < h.index(
              "    G28 Z\n", h.index("[homing_override]")))
    check("k2-module.cfg: [k2_abbruch]", "\n[k2_abbruch]\n" in k + "\n")


def main():
    test_logik()
    test_klipper()
    test_cfg_makros()
    print("\nERGEBNIS: %s (%d/%d ok)" % ("ok" if not FAIL else "FEHLER",
                                        len(PASS), len(PASS) + len(FAIL)))
    if FAIL:
        print("FEHLER:", FAIL)
        sys.exit(1)


if __name__ == "__main__":
    main()
