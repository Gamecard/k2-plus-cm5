#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_k2_konsole.py – Konsolen-Helfer der K2-Module (offline).

Prüft: Format ``!! [modul]`` / ``// [modul]``, Puffer bis klippy:ready +
Ausgabe nach FLUSH_DELAY, Überlauf, Entprellung mit Wiederholungszähler,
nie werfen (kaputtes gcode, kein gcode), Log-Stufen, Python-3.9-Syntax,
und dass jedes K2-Modul mit Warn-/Fehler-Log den Helfer einbindet.
"""
import ast
import glob
import logging
import os
import re
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
import k2_konsole as m  # noqa: E402

logging.getLogger().addHandler(logging.NullHandler())   # Testausgabe ruhig

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


class FakeReactor:
    NEVER = 9e99

    def __init__(self):
        self.t = 100.0
        self.timers = []

    def monotonic(self):
        return self.t

    def register_timer(self, cb, when):
        self.timers.append([cb, when])
        return cb

    def lauf_bis(self, t):
        self.t = t
        for tm in list(self.timers):
            if tm[1] <= t:
                tm[1] = tm[0](t)


class FakeGcode:
    def __init__(self):
        self.raw = []

    def respond_raw(self, z):
        self.raw.append(z)


class FakePrinter:
    def __init__(self, state="startup"):
        self.reactor = FakeReactor()
        self.gcode = FakeGcode()
        self.handlers = {}
        self.state = state

    def get_reactor(self):
        return self.reactor

    def get_state_message(self):
        return ("x", self.state)

    def register_event_handler(self, ev, cb):
        self.handlers.setdefault(ev, []).append(cb)

    def lookup_object(self, name, default=None):
        return self.gcode if name == "gcode" else default

    def fire(self, ev):
        self.state = "ready" if ev == "klippy:ready" else self.state
        for cb in self.handlers.get(ev, []):
            cb()


def test_format():
    z = m.format_zeilen("k2_x", "Port weg", m.FEHLER)
    check("Fehler-Format !! [modul]", z == ["!! [k2_x] Port weg"], z)
    z = m.format_zeilen("k2_x", "[k2_x] schon markiert\nzweite", m.WARNUNG)
    check("Warnung-Format // ohne doppeltes Tag, mehrzeilig",
          z == ["// [k2_x] schon markiert", "// zweite"], z)


def test_puffer_bis_ready():
    pr = FakePrinter()
    k = m.Konsole(pr, "k2_x")
    k.fehler("vor ready")
    check("vor ready nichts ausgegeben", pr.gcode.raw == [])
    pr.fire("klippy:ready")
    k.warnung("während Flush-Wartezeit")
    check("auch direkt nach ready noch gepuffert", pr.gcode.raw == [])
    pr.reactor.lauf_bis(100.0 + m.FLUSH_DELAY)
    check("nach FLUSH_DELAY beide in Reihenfolge",
          pr.gcode.raw == ["!! [k2_x] vor ready",
                           "// [k2_x] während Flush-Wartezeit"], pr.gcode.raw)
    k.warnung("danach direkt")
    check("nach Flush direkt", pr.gcode.raw[-1] == "// [k2_x] danach direkt")


def test_sofort():
    pr = FakePrinter()
    k = m.Konsole(pr, "k2_x")
    k.fehler("Shutdown-Grund", sofort=True)
    check("sofort=True umgeht den Puffer vor ready",
          pr.gcode.raw == ["!! [k2_x] Shutdown-Grund"], pr.gcode.raw)


def test_nach_ready_angelegt():
    pr = FakePrinter(state="ready")
    k = m.Konsole(pr, "k2_x")
    k.info("sofort")
    check("nach ready angelegt -> sofort", pr.gcode.raw == ["// [k2_x] sofort"])


def test_ueberlauf():
    pr = FakePrinter()
    k = m.Konsole(pr, "k2_x")
    for i in range(m.MAX_PUFFER + 3):
        k.warnung("m%d" % i)
    k.flush()
    check("Überlauf: MAX_PUFFER + Hinweis", len(pr.gcode.raw) == m.MAX_PUFFER + 1)
    check("Überlauf: älteste verworfen", pr.gcode.raw[0] == "// [k2_x] m3")
    check("Überlauf: Hinweis mit Anzahl", "3 ältere Meldungen" in pr.gcode.raw[-1])


def test_entprellung():
    pr = FakePrinter(state="ready")
    k = m.Konsole(pr, "k2_x")
    r1 = k.warnung("Sensor weg", schluessel="s", pause=60)
    pr.reactor.t += 10
    r2 = k.warnung("Sensor weg", schluessel="s", pause=60)
    k.warnung("Sensor weg", schluessel="s", pause=60)
    check("entprellt: 1 Ausgabe", r1 and not r2 and len(pr.gcode.raw) == 1)
    pr.reactor.t += 60
    k.warnung("Sensor weg", schluessel="s", pause=60)
    check("nach Pause: Zähler angehängt",
          pr.gcode.raw[-1].endswith("(2× seit der letzten Meldung)"),
          pr.gcode.raw)
    k.zuruecksetzen("s")
    k.warnung("Sensor weg", schluessel="s", pause=60)
    check("zuruecksetzen hebt Entprellung auf", len(pr.gcode.raw) == 3)


def test_nie_werfen():
    class Kaputt:
        def respond_raw(self, z):
            raise RuntimeError("weg")
    pr = FakePrinter(state="ready")
    pr.gcode = Kaputt()
    k = m.Konsole(pr, "k2_x")
    check("kaputtes gcode wirft nicht", k.fehler("x") is False)

    class Leer:
        pass
    k = m.Konsole(Leer(), "k2_x")
    check("Printer ohne API: bereit, kein gcode, kein Wurf",
          k.fehler("x") is False and k._bereit)

    class NurInfo:
        def __init__(self):
            self.t = []

        def respond_info(self, s):
            self.t.append(s)
    g = NurInfo()
    k = m.Konsole(Leer(), "k2_x", gcode=g)
    k.fehler("y")
    check("Fallback respond_info", g.t == ["!! [k2_x] y"], g.t)


def test_log_stufen():
    erfasst = []

    class H(logging.Handler):
        def emit(self, rec):
            erfasst.append((rec.levelno, rec.getMessage()))
    h = H()
    root = logging.getLogger()
    alt = root.level
    root.setLevel(logging.INFO)
    root.addHandler(h)
    try:
        k = m.Konsole(FakePrinter(state="ready"), "k2_x")
        k.fehler("f")
        k.warnung("w")
        k.info("i")
        k.warnung("ohne", log=False)
    finally:
        root.removeHandler(h)
        root.setLevel(alt)
    check("Log-Stufen error/warning/info, log=False still",
          erfasst == [(logging.ERROR, "[k2_x] f"), (logging.WARNING, "[k2_x] w"),
                      (logging.INFO, "[k2_x] i")], erfasst)


def test_log_adapter():
    erfasst = []

    class H(logging.Handler):
        def emit(self, rec):
            erfasst.append((rec.levelno, rec.getMessage()))
    h = H()
    root = logging.getLogger()
    alt = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(h)
    try:
        la = m.LogAdapter("k2_x")
        la.info("i")
        la.warn("w")
        la.error("e")
        la.debug("d")
    finally:
        root.removeHandler(h)
        root.setLevel(alt)
    check("LogAdapter: info/warn/error/debug mit [modul] ins Log",
          erfasst == [(logging.INFO, "[k2_x] i"), (logging.WARNING, "[k2_x] w"),
                      (logging.ERROR, "[k2_x] e"), (logging.DEBUG, "[k2_x] d")],
          erfasst)
    pr = FakePrinter(state="ready")
    kl = m.KonsolenLog(m.Konsole(pr, "k2_x"), m.WARNUNG)
    kl.info("[k2_x] Warnung: heiß")
    kl.error("kaputt")
    check("KonsolenLog: info als Warnung, error rot",
          pr.gcode.raw == ["// [k2_x] Warnung: heiß", "!! [k2_x] kaputt"],
          pr.gcode.raw)


def test_controller_logger_verdrahtet():
    """Die Hüllen geben ihren Controllern keinen gcode-„logger“ mehr (dessen
    info/error liefen still ins Leere)."""
    falsch = []
    for f in ("k2_box.py", "k2_motor_init.py", "k2_prtouch.py"):
        src = open(os.path.join(HIER, f), encoding="utf-8").read()
        for zeile in src.split("\n"):
            if re.search(r"logger=self\.gcode\b", zeile):
                falsch.append("%s: %s" % (f, zeile.strip()))
    # K2PrtouchApaxCheck nutzt respond_info (prüft hasattr) -> erlaubt
    falsch = [z for z in falsch if "is_armed=self._any_armed" not in z]
    check("kein logger=self.gcode für Controller mit info/error", not falsch,
          falsch)


def test_module_binden_ein():
    """Jedes K2-Modul mit Warn-/Fehler-Log benutzt den Konsolen-Helfer."""
    fehlend = []
    for f in sorted(glob.glob(os.path.join(HIER, "k2_*.py"))):
        name = os.path.basename(f)
        if name == "k2_konsole.py":
            continue
        src = open(f, encoding="utf-8").read()
        if re.search(r"logging\.(warning|error|exception)\(", src) \
                and "k2_konsole" not in src:
            fehlend.append(name)
    check("alle Module mit Warn-/Fehler-Log binden k2_konsole ein",
          not fehlend, fehlend)


def test_py39():
    for f in ("k2_konsole.py", "test_k2_konsole.py"):
        ast.parse(open(os.path.join(HIER, f), encoding="utf-8").read(),
                  feature_version=(3, 9))
    check("Python-3.9-Syntax", True)
    check("kein load_config / keine stats (kein Klipper-Abschnitt)",
          not hasattr(m, "load_config") and not hasattr(m.Konsole, "stats"))


TESTS = [test_format, test_puffer_bis_ready, test_sofort, test_nach_ready_angelegt,
         test_ueberlauf, test_entprellung, test_nie_werfen, test_log_stufen,
         test_log_adapter, test_controller_logger_verdrahtet,
         test_module_binden_ein, test_py39]

if __name__ == "__main__":
    for t in TESTS:
        t()
    print("ERGEBNIS: %s (%d/%d ok)" % ("ok" if not fehler else "FEHLER",
                                      ok, ok + len(fehler)))
    sys.exit(1 if fehler else 0)
