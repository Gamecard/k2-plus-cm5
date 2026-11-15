#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline-Tests für k2_mesh_guard.py ([k2_mesh_guard]) – ohne Klipper und
ohne Hardware: ``python3 test_k2_mesh_guard.py``.

Die Attrappen bilden die Schnittstelle des Klipper-Stands des Druckers
nach (Referenzdateien nur gelesen). Gestützt auf:

- probe.py:428-446   ProbePointsHelper.__init__ (finalize_callback,
                     probe_points, speed, use_offsets, lift_speed,
                     probe_offsets)
- probe.py:451-455   update_probe_points / use_xy_offsets
- probe.py:458-465   _move / _raise_tool (Z = horizontal_move_z)
- probe.py:466-472   _invoke_callback (Rückruf frisch vom Objekt gelesen,
                     Rückgabe "retry" = neu vermessen)
- probe.py:473-479   _move_next (Punkt minus Sonden-Versatz, speed)
- probe.py:480-515   start_probe (Schleife: heben, anfahren, antasten;
                     nach dem letzten Punkt Ergebnisse abholen, Rückruf,
                     DANACH Sitzungsende)
- probe.py:318-410   SampleAveragingHelper (hw_probe_session, Fehler bei
                     doppeltem Start, run_probe, pull_probed_results)
- probe.py:17-31,268 Ergebnisobjekte: ProbeResult-Tupel mit bed_x, bed_y,
                     bed_z, test_x, test_y, test_z
                     (create_probe_result(pos, offsets)). ANNAHME: die
                     Felddefinition steht in manual_probe.py, die für
                     diesen Stand nicht vorlag – Form aus probe.py
                     abgeleitet.
- bed_mesh.py:93     BedMesh.bmc
- bed_mesh.py:335    BedMeshCalibrate.probe_mgr (Rückruf probe_finalize)
- bed_mesh.py:654-710 probe_finalize (round(bed_x, 2), Nullpunkt-Zusatz-
                     punkt, Längenprüfung gegen die Rasterpunkte)
- bed_mesh.py:823-824 ProbeManager.probe_helper, use_xy_offsets(True)
- bed_mesh.py:866-885 start_probe, get_zero_ref_mode, get_substitutes
- bed_mesh.py:887-950 generate_points (Zickzack-Reihenfolge)

Abgedeckt: Optionen/Grenzen; ebenes 9×9-Raster; einzelner Ausreißer
Mitte/Rand/Ecke; echte Bettform (Schräge, Wölbung, Verdrehung);
unvollständige Raster; zu viele Ausreißer; Hülle off/report/act; Fehler im
Wächter; Sondenfehler; fehlende Attribute; get_status; Befehlsname;
Python-3.9-Syntax.
"""

import ast
import collections
import logging
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import k2_mesh_guard as G  # noqa: E402

logging.disable(logging.CRITICAL)   # Log-Zeilen des Moduls nicht ausgeben

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print("%s %s %s" % ("PASS" if cond else "FAIL", name,
                        detail if not cond else ""))


# ---------------------------------------------------------------------
# Raster-Hilfen
# ---------------------------------------------------------------------

def raster(nx=9, ny=9, lo=5.0, hi=345.0, form=None):
    """Punkte in der Reihenfolge von bed_mesh.py:913-930 (Zickzack)."""
    dx = (hi - lo) / (nx - 1)
    dy = (hi - lo) / (ny - 1)
    max_x = lo + dx * (nx - 1)
    pts = []
    for i in range(ny):
        for j in range(nx):
            x = lo + j * dx if not i % 2 else max_x - j * dx
            y = lo + i * dy
            pts.append((x, y, form(x, y) if form else 0.0))
    return pts


def index_bei(pts, x, y):
    for i, p in enumerate(pts):
        if abs(p[0] - x) < 0.01 and abs(p[1] - y) < 0.01:
            return i
    raise KeyError((x, y))


def mit(pts, i, dz):
    pts = list(pts)
    pts[i] = (pts[i][0], pts[i][1], pts[i][2] + dz)
    return pts


def woelbung(hoehe=0.8):
    a = hoehe / (2 * 170.0 ** 2)
    return lambda x, y: a * ((x - 175.0) ** 2 + (y - 175.0) ** 2)


def schraege(x, y):
    return 0.0015 * x - 0.001 * y           # 0,51 / 0,34 mm über das Bett


def verdrehung(x, y):
    return 0.6 * (x - 175.0) * (y - 175.0) / (170.0 ** 2)


def rauschen(x, y):
    # fest (kein Zufall): +-0,02 mm
    return 0.02 * (((int(x) * 7 + int(y) * 13) % 5) - 2) / 2.0


def indizes(liste):
    return [a["index"] for a in liste]


# ---------------------------------------------------------------------
# 1. Optionen
# ---------------------------------------------------------------------

def test_optionen():
    o = G.optionen_pruefen({})
    check("Optionen: Defaults", o == {
        "mode": "report", "threshold": 0.25, "min_neighbours": 2,
        "max_points": 6, "repeats": 2, "max_shift": 1.0,
        "edge_trend": True}, o)
    check("Optionen: None = Default",
          G.optionen_pruefen({"mode": None, "threshold": None}) == o)
    o = G.optionen_pruefen({"mode": " ACT ", "threshold": "0.4",
                            "min_neighbours": "3", "max_points": "4",
                            "repeats": "5", "max_shift": "0.5",
                            "edge_trend": "False"})
    check("Optionen: Texte wie aus der Config",
          o["mode"] == "act" and o["threshold"] == 0.4
          and o["min_neighbours"] == 3 and o["max_points"] == 4
          and o["repeats"] == 5 and o["max_shift"] == 0.5
          and o["edge_trend"] is False
          and isinstance(o["repeats"], int), o)
    for mode in ("off", "report", "act"):
        check("Optionen: mode %s erlaubt" % mode,
              G.optionen_pruefen({"mode": mode})["mode"] == mode)

    def fehler(roh):
        try:
            G.optionen_pruefen(roh)
        except G.OptionsFehler as exc:
            return str(exc)
        return None

    grenzen = [("threshold", "0.05", "2", "0.049", "2.01"),
               ("min_neighbours", "1", "4", "0", "5"),
               ("max_points", "1", "20", "0", "21"),
               ("repeats", "1", "5", "0", "6"),
               ("max_shift", "0.05", "5", "0.04", "5.1")]
    for name, lo, hi, unter, ueber in grenzen:
        check("Optionen: %s Grenzen %s und %s erlaubt" % (name, lo, hi),
              fehler({name: lo}) is None and fehler({name: hi}) is None)
        for wert in (unter, ueber):
            f = fehler({name: wert})
            check("Optionen: %s = %s abgelehnt (deutscher Text mit Bereich)"
                  % (name, wert),
                  f is not None and "[k2_mesh_guard]" in f and name in f
                  and "außerhalb" in f and wert in f, f)
    f = fehler({"mode": "auto"})
    check("Optionen: mode auto abgelehnt",
          f is not None and "off" in f and "report" in f and "act" in f, f)
    f = fehler({"threshold": "0,25"})
    check("Optionen: Dezimalkomma abgelehnt",
          f is not None and "keine Zahl" in f, f)
    f = fehler({"repeats": "2.5"})
    check("Optionen: repeats 2.5 abgelehnt (ganze Zahl)",
          f is not None and "ganze Zahl" in f, f)
    f = fehler({"threshold": "nan"})
    check("Optionen: nan abgelehnt", f is not None, f)
    f = fehler({"edge_trend": "vielleicht"})
    check("Optionen: edge_trend ungültig abgelehnt", f is not None, f)
    f = fehler({"schwelle": "1"})
    check("Optionen: unbekannte Option abgelehnt",
          f is not None and "schwelle" in f, f)


# ---------------------------------------------------------------------
# 2.–6. Reine Logik
# ---------------------------------------------------------------------

def test_eben():
    pts = raster()
    aus, bewertet = G.analysiere(pts)
    check("Eben 9×9: kein Ausreißer, 81 Punkte bewertet",
          aus == [] and bewertet == 81, (aus, bewertet))
    aus = G.finde_ausreisser(raster(form=rauschen))
    check("Eben 9×9 mit Messrauschen ±0,02: kein Ausreißer", aus == [], aus)
    check("Median: ungerade/gerade",
          G.median([3, 1, 2]) == 2 and G.median([4, 1, 3, 2]) == 2.5)


def test_einzelner():
    basis = raster(form=rauschen)
    faelle = [("Mitte", 175.0, 175.0, 4), ("Rand", 175.0, 5.0, 3),
              ("Rand links", 5.0, 217.5, 3), ("Ecke", 5.0, 5.0, 2),
              ("Ecke hinten rechts", 345.0, 345.0, 2),
              ("Rand neben der Ecke", 47.5, 5.0, 3),
              ("eins neben der Ecke diagonal", 47.5, 47.5, 4)]
    for name, x, y, nachbarn in faelle:
        i = index_bei(basis, x, y)
        aus = G.finde_ausreisser(mit(basis, i, 0.6))
        ok = (indizes(aus) == [i] and aus[0]["neighbours"] == nachbarn
              and abs(aus[0]["deviation"] - 0.6) < 0.08
              and aus[0]["x"] == x and aus[0]["y"] == y
              and abs(aus[0]["z"] - aus[0]["median"]
                      - aus[0]["deviation"]) < 1e-9)
        check("Einzelner Ausreißer %s (+0,6): genau dieser Punkt" % name,
              ok, aus)
    i = index_bei(basis, 175.0, 175.0)
    aus = G.finde_ausreisser(mit(basis, i, -0.6))
    check("Ausreißer nach unten (−0,6) erkannt",
          indizes(aus) == [i] and aus[0]["deviation"] < -0.5, aus)
    eben = raster()
    check("Genau an der Schwelle (0,25) = kein Ausreißer",
          G.finde_ausreisser(mit(eben, i, 0.25)) == [])
    check("Knapp über der Schwelle (0,26) = Ausreißer",
          indizes(G.finde_ausreisser(mit(eben, i, 0.26))) == [i])
    check("threshold 1,0: +0,6 kein Ausreißer",
          G.finde_ausreisser(mit(eben, i, 0.6), 1.0) == [])
    # Ecke neben einem Rand-Ausreißer: der festgehaltene Ausreißer ist
    # keine Stütze mehr (sonst würde die Ecke mitgemeldet: zwei
    # Nachbarwerte = Mittelwert)
    j = index_bei(eben, 47.5, 5.0)
    aus = G.finde_ausreisser(mit(eben, j, 1.5))
    check("Großer Rand-Ausreißer steckt die Ecke nicht an",
          indizes(aus) == [j], aus)
    # min_neighbours
    k = index_bei(eben, 5.0, 5.0)
    check("min_neighbours 3: Ecke wird nicht bewertet",
          G.finde_ausreisser(mit(eben, k, 0.6), 0.25, 3) == []
          and G.analysiere(eben, 0.25, 3)[1] == 77)
    check("min_neighbours 4: nur Innenpunkte (49)",
          G.analysiere(eben, 0.25, 4)[1] == 49)
    # zwei getrennte Ausreißer, Reihenfolge nach Index
    aus = G.finde_ausreisser(mit(mit(basis, 70, 0.5), 12, -0.5))
    check("Zwei Ausreißer: beide, nach Index sortiert",
          indizes(aus) == [12, 70], aus)


def test_bettform():
    formen = [("Schräge", schraege), ("Wölbung 0,8 mm", woelbung(0.8)),
              ("Wölbung −0,8 mm (Mulde umgekehrt)", woelbung(-0.8)),
              ("Verdrehung 0,6 mm", verdrehung),
              ("Schräge + Wölbung + Rauschen",
               lambda x, y: schraege(x, y) + woelbung(0.6)(x, y)
               + rauschen(x, y))]
    for name, form in formen:
        aus = G.finde_ausreisser(raster(form=form))
        check("Bettform %s: KEIN Ausreißer" % name, aus == [], aus)
    # Nutzen der Rand-Fortsetzung: größte Abweichung (Ecke) wird kleiner
    pts = raster(form=lambda x, y: woelbung(0.8)(x, y) + 0.001 * x)
    r = G.Raster(pts)

    def groesste(trend):
        return max(abs(r.z[i] - G.median(r.nachbarwerte(r.ort[i], (),
                                                        trend)))
                   for i in range(len(r.z)))
    mit_trend, ohne_trend = groesste(True), groesste(False)
    check("Rand-Fortsetzung senkt die Form-Abweichung der Ecke "
          "(%.3f statt %.3f mm)" % (mit_trend, ohne_trend),
          mit_trend < 0.08 and ohne_trend > 0.15, (mit_trend, ohne_trend))
    # reine Schräge: mit Rand-Fortsetzung exakt 0
    pts = raster(form=schraege)
    check("Schräge: Abweichung überall praktisch 0",
          G.finde_ausreisser(pts, 1e-6) == [])
    # grobes Raster 5×5 mit starker Wölbung
    check("Bettform 5×5, Wölbung 0,8 mm: kein Ausreißer",
          G.finde_ausreisser(raster(5, 5, form=woelbung(0.8))) == [])
    # Ausreißer auf gewölbtem, schrägem Bett wird trotzdem gefunden
    pts = raster(form=lambda x, y: schraege(x, y) + woelbung(0.8)(x, y))
    for name, x, y in (("Mitte", 175.0, 175.0), ("Ecke", 345.0, 5.0),
                       ("Rand", 345.0, 175.0)):
        i = index_bei(pts, x, y)
        aus = G.finde_ausreisser(mit(pts, i, 0.5))
        check("Ausreißer %s auf gewölbt-schrägem Bett gefunden" % name,
              indizes(aus) == [i], aus)


def test_unvollstaendig():
    voll = raster(form=rauschen)
    # rundes Bett: Ecken fehlen (bed_mesh.py:925-930)
    rund = [p for p in voll
            if (p[0] - 175.0) ** 2 + (p[1] - 175.0) ** 2 <= 180.0 ** 2]
    check("Rundes Raster (%d Punkte): kein Ausreißer" % len(rund),
          len(rund) < 81 and G.finde_ausreisser(rund) == [])
    i = index_bei(rund, 175.0, 5.0)
    aus = G.finde_ausreisser(mit(rund, i, 0.6))
    check("Rundes Raster: Ausreißer am Rand gefunden", indizes(aus) == [i],
          aus)
    # Loch in der Mitte + fehlende Einzelpunkte
    loch = [p for p in voll if not (130 < p[0] < 220 and 130 < p[1] < 220)]
    loch = [p for n, p in enumerate(loch) if n not in (3, 40)]
    check("Raster mit Loch (%d Punkte): kein Ausreißer" % len(loch),
          G.finde_ausreisser(loch) == [])
    i = index_bei(loch, 90.0, 132.5)        # direkt links vom Loch
    aus = G.finde_ausreisser(mit(loch, i, 0.6))
    check("Raster mit Loch: Ausreißer neben dem Loch gefunden",
          indizes(aus) == [i], aus)
    # adaptive Raster (kleiner Bereich, andere Punktzahlen)
    for nx, ny in ((3, 3), (4, 7), (7, 3), (3, 9)):
        pts = raster(nx, ny, 100.0, 160.0, form=schraege)
        ok = G.finde_ausreisser(pts) == []
        i = len(pts) // 2
        aus = G.finde_ausreisser(mit(pts, i, 0.5))
        check("Adaptiv %d×%d: eben ok, Ausreißer gefunden" % (nx, ny),
              ok and indizes(aus) == [i], aus)
    pts = raster(3, 3, 100.0, 120.0)
    aus = G.finde_ausreisser(mit(pts, 0, 0.5))
    check("Adaptiv 3×3 (10 mm Abstand): Ecke gefunden",
          indizes(aus) == [0], aus)
    # Sonderfälle
    check("Leere Liste / ein Punkt / zwei Punkte: keine Ausreißer",
          G.finde_ausreisser([]) == []
          and G.finde_ausreisser([(0, 0, 5.0)]) == []
          and G.finde_ausreisser([(0, 0, 0.0), (10, 0, 5.0)]) == [])
    zeile = [(10.0 * i, 0.0, 0.0) for i in range(7)]
    aus = G.finde_ausreisser(mit(zeile, 3, 0.5))
    check("Einzelne Zeile (7 Punkte): Ausreißer in der Mitte gefunden",
          indizes(aus) == [3], aus)
    # gemessene statt Soll-Koordinaten: Rundung/Toleranz
    krumm = [(p[0] + (0.004 if n % 2 else -0.004),
              p[1] + (0.012 if n % 3 else -0.011), p[2])
             for n, p in enumerate(voll)]
    r = G.Raster(krumm)
    check("Koordinaten gerundet: 9 Spalten und 9 Zeilen trotz ±0,012 mm",
          len(r.x_mitten) == 9 and len(r.y_mitten) == 9,
          (len(r.x_mitten), len(r.y_mitten)))
    i = index_bei(voll, 175.0, 175.0)
    check("Koordinaten gerundet: Ausreißer gefunden",
          indizes(G.finde_ausreisser(mit(krumm, i, 0.6))) == [i])
    # doppelter Punkt: wird nicht bewertet, stört nicht
    doppelt = voll + [(175.0, 175.0, 3.0)]
    check("Doppelter Punkt in einer Zelle stört nicht",
          G.finde_ausreisser(doppelt) == [])
    # fehlende Spalte: kein Nachbar über die Lücke hinweg
    luecke = [p for p in raster() if abs(p[0] - 175.0) > 1]
    r = G.Raster(luecke)
    zelle = r.ort[index_bei(luecke, 132.5, 175.0)]
    check("Lücke (fehlende Spalte): kein Nachbar über die Lücke",
          r.nachbar(zelle, (1, 0)) is None
          and r.nachbar(zelle, (-1, 0)) is not None)
    try:
        G.finde_ausreisser([(0, 0, float("nan")), (1, 0, 0), (2, 0, 0)])
        ok = False
    except ValueError:
        ok = True
    check("Ungültiger Messwert (nan) -> ValueError", ok)


def test_entscheidung():
    E = G.entscheide
    check("Entscheidung: off -> aus",
          E("off", 3, 6) == G.AKTION_AUS and E("off", 0, 6) == G.AKTION_AUS)
    check("Entscheidung: keine Ausreißer",
          E("report", 0, 6) == G.AKTION_KEINE
          and E("act", 0, 6) == G.AKTION_KEINE)
    check("Entscheidung: report meldet nur",
          E("report", 1, 6) == G.AKTION_MELDEN
          and E("report", 6, 6) == G.AKTION_MELDEN)
    check("Entscheidung: act misst nach (bis max_points)",
          E("act", 1, 6) == G.AKTION_NACHMESSEN
          and E("act", 6, 6) == G.AKTION_NACHMESSEN)
    check("Entscheidung: zu viele -> nur melden",
          E("act", 7, 6) == G.AKTION_ZU_VIELE
          and E("report", 7, 6) == G.AKTION_ZU_VIELE)
    # viele Ausreißer im Raster
    pts = raster()
    for i in (10, 12, 14, 16, 46, 48, 50, 52):
        pts = mit(pts, i, 0.7)
    aus = G.finde_ausreisser(pts)
    check("8 Ausreißer im Raster -> zu viele (max_points 6)",
          len(aus) == 8 and E("act", len(aus), 6) == G.AKTION_ZU_VIELE,
          indizes(aus))
    # bestätigter Wert
    B = G.bestaetigter_wert
    check("Bestätigt: Fehlwert + 2 gute Nachmessungen -> guter Wert",
          B(0.60, [0.02, 0.01]) == 0.02)
    check("Bestätigt: 2 Nachmessungen bestätigen den alten Wert",
          B(0.60, [0.61, 0.59]) == 0.60)
    check("Bestätigt: 1 Nachmessung -> Mittel (halbe Korrektur)",
          abs(B(0.60, [0.00]) - 0.30) < 1e-12)
    check("Bestätigt: 3 Nachmessungen (gerade Anzahl) -> Mittel der Mitte",
          abs(B(0.60, [0.00, 0.02, 0.04]) - 0.03) < 1e-12)
    check("Bestätigt: ohne Nachmessung bleibt der alte Wert",
          B(0.6, []) == 0.6)
    # Texte
    a = {"index": 40, "x": 175.0, "y": 175.0, "z": 0.612, "median": 0.012,
         "deviation": 0.6, "neighbours": 4}
    t = G.text_ausreisser(a)
    check("Text Ausreißer: Position, alter Wert, Median, Abweichung",
          "X=175.00" in t and "Y=175.00" in t and "+0.612" in t
          and "+0.012" in t and "+0.600" in t and "Nachbar-Median" in t, t)
    t = G.text_bestaetigung(a, [0.01, 0.02], 0.02)
    check("Text Bestätigung: neue Werte + bestätigter Wert",
          "+0.010, +0.020" in t and "bestätigt +0.020" in t
          and "+0.612" in t, t)
    opt = G.optionen_pruefen({})
    Z = G.text_zusammenfassung
    check("Text Zusammenfassung: nichts bei aus/keine",
          Z(G.AKTION_AUS, 0, 81, opt) is None
          and Z(G.AKTION_KEINE, 0, 81, opt) is None)
    t = Z(G.AKTION_ZU_VIELE, 8, 81, opt)
    check("Text zu viele: „Bett/Düse prüfen“, kein Nachmessen",
          "Bett/Düse prüfen" in t and "NICHT" in t and "8 von 81" in t, t)
    check("Text report / act unterscheidbar",
          "report" in Z(G.AKTION_MELDEN, 1, 81, opt)
          and "act" in Z(G.AKTION_NACHMESSEN, 1, 81, opt))


# ---------------------------------------------------------------------
# Attrappen (Schnittstelle der Referenzdateien, s. Docstring)
# ---------------------------------------------------------------------

# probe.py:21-31 / 268: Tupel mit benannten Feldern
ProbeResult = collections.namedtuple(
    "probe_result", ["bed_x", "bed_y", "bed_z", "test_x", "test_y",
                     "test_z"])


class CommandError(Exception):
    pass


class ConfigError(Exception):
    pass


class FakeReactor:
    NEVER = 9e99

    def __init__(self):
        self.t = 100.0

    def monotonic(self):
        self.t += 0.01
        return self.t


class FakeGcode:
    def __init__(self):
        self.commands = {}
        self.lines = []

    def register_command(self, name, func, desc=None):
        self.commands[name] = (func, desc)

    def respond_raw(self, msg):
        self.lines.append(msg)

    def respond_info(self, msg):
        self.lines.append("// " + msg)


class FakeGcmd:
    def __init__(self, params=None, command="BED_MESH_CALIBRATE"):
        self.params = dict(params or {})
        self.command = command
        self.out = []
        self.error = CommandError

    def get(self, name, default=None):
        return self.params.get(name, default)

    def get_float(self, name, default=None, **kw):
        return float(self.params.get(name, default))

    def get_command(self):
        return self.command

    def respond_info(self, msg):
        self.out.append(msg)


class FakeToolhead:
    def __init__(self):
        self.pos = [0.0, 0.0, 10.0, 0.0]
        self.moves = []
        self.flushes = 0

    def manual_move(self, coord, speed):
        for i, c in enumerate(coord):
            if c is not None:
                self.pos[i] = c
        self.moves.append((tuple(self.pos[:3]), speed))

    def get_position(self):
        return list(self.pos)

    def get_last_move_time(self):
        self.flushes += 1
        return 0.0

    def get_status(self, eventtime):
        return {"homed_axes": "xyz"}


class FakePrinter:
    command_error = CommandError
    config_error = ConfigError

    def __init__(self):
        self.objects = {}
        self.handlers = {}
        self.reactor = FakeReactor()
        self.objects["gcode"] = FakeGcode()
        self.objects["toolhead"] = FakeToolhead()

    def lookup_object(self, name, default=ConfigError):
        if name in self.objects:
            return self.objects[name]
        if default is ConfigError:
            raise ConfigError("Unknown config object '%s'" % (name,))
        return default

    def add_object(self, name, obj):
        self.objects[name] = obj

    def register_event_handler(self, event, cb):
        self.handlers.setdefault(event, []).append(cb)

    def send_event(self, event, *args):
        return [cb(*args) for cb in self.handlers.get(event, [])]

    def get_reactor(self):
        return self.reactor


class FakeConfig:
    error = ConfigError

    def __init__(self, printer, name="k2_mesh_guard", werte=None):
        self.printer = printer
        self.name = name
        self.werte = dict(werte or {})

    def get_printer(self):
        return self.printer

    def get_name(self):
        return self.name

    def get(self, option, default=ConfigError):
        if option in self.werte:
            return self.werte[option]
        if default is ConfigError:
            raise ConfigError("Option '%s' fehlt" % (option,))
        return default


class FakeHwSession:
    """Sonden-Hardware: liefert je Antasten ein ProbeResult am aktuellen
    XY (probe.py:257-269: test = Kopfposition, bed = test + Versatz)."""

    def __init__(self, printer, probe):
        self.printer = printer
        self.probe = probe
        self.results = []
        self.ended = 0

    def run_probe(self, gcmd):
        th = self.printer.lookup_object("toolhead")
        x, y = th.pos[0], th.pos[1]
        ox, oy, oz = self.probe.get_offsets()
        bx, by = x + ox, y + oy
        self.probe.calls.append((round(bx, 3), round(by, 3), th.pos[2]))
        nr = len(self.probe.calls)
        if self.probe.fail_at is not None and nr == self.probe.fail_at:
            raise CommandError("Probe triggered prior to movement")
        if self.probe.bug_at is not None and nr == self.probe.bug_at:
            raise RuntimeError("Sondenmodul defekt")
        z = self.probe.z_at(bx, by, nr)
        th.pos[2] = z + oz        # Düse steht am Auslösepunkt
        self.results.append(ProbeResult(bx, by, z, x, y, z + oz))

    def pull_probed_results(self):
        res, self.results = self.results, []
        return res

    def end_probe_session(self):
        self.ended += 1


class FakeSampleAveraging:
    """probe.py:318-410 (ein Messwert je run_probe, samples = 1)."""

    def __init__(self, printer, start_session_cb):
        self.printer = printer
        self.start_session_cb = start_session_cb
        self.hw_probe_session = None
        self.results = []
        self.starts = 0
        self.ends = 0

    def start_probe_session(self, gcmd):
        if self.hw_probe_session is not None:
            raise CommandError("Internal probe error - start/end probe "
                               "session mismatch")
        self.hw_probe_session = self.start_session_cb(gcmd)
        self.results = []
        self.starts += 1
        return self

    def end_probe_session(self):
        hw = self.hw_probe_session
        if hw is None:
            raise CommandError("Internal probe error - start/end probe "
                               "session mismatch")
        self.results = []
        self.hw_probe_session = None
        self.ends += 1
        hw.end_probe_session()

    def run_probe(self, gcmd):
        if self.hw_probe_session is None:
            raise CommandError("Internal probe error - start/end probe "
                               "session mismatch")
        self.hw_probe_session.run_probe(gcmd)
        self.results.append(self.hw_probe_session.pull_probed_results()[0])

    def pull_probed_results(self):
        res, self.results = self.results, []
        return res


class FakeProbe:
    """Objekt 'probe' (probe.py:611-631 bzw. das K2-Sondenmodul)."""

    def __init__(self, printer, form=None, offsets=(0.0, 0.0, 0.0)):
        self.printer = printer
        self.form = form or (lambda x, y: 0.0)
        self.offsets = offsets
        self.einmalig = {}      # (x, y) -> Liste von Z-Werten je Antasten
        self.calls = []
        self.fail_at = None
        self.bug_at = None
        self.hw = FakeHwSession(printer, self)
        self.probe_session = FakeSampleAveraging(printer,
                                                 lambda gcmd: self.hw)

    def z_at(self, x, y, nr):
        key = (round(x, 2), round(y, 2))
        folge = self.einmalig.get(key)
        if folge:
            return folge.pop(0)
        return self.form(x, y)

    def get_offsets(self, gcmd=None):
        return self.offsets

    def get_probe_params(self, gcmd=None):
        return {"lift_speed": 7.0, "probe_speed": 5.0, "samples": 1}

    def get_status(self, eventtime):
        return {"name": "k2_prtouch"}

    def start_probe_session(self, gcmd):
        return self.probe_session.start_probe_session(gcmd)


class FakeProbePointsHelper:
    """probe.py:428-515, Ablauf und Namen wie in der Referenz (ohne den
    manuellen Zweig)."""

    def __init__(self, printer, finalize_callback, speed=100.0,
                 horizontal_move_z=5.0):
        self.printer = printer
        self.finalize_callback = finalize_callback
        self.probe_points = []
        self.name = "bed_mesh"
        self.default_horizontal_move_z = horizontal_move_z
        self.speed = speed
        self.use_offsets = False
        self.lift_speed = self.speed
        self.probe_offsets = (0.0, 0.0, 0.0)

    def update_probe_points(self, points, min_points):
        self.probe_points = points

    def use_xy_offsets(self, use_offsets):
        self.use_offsets = use_offsets

    def _move(self, coord, speed):
        self.printer.lookup_object("toolhead").manual_move(coord, speed)

    def _raise_tool(self, is_first=False):
        speed = self.lift_speed
        if is_first:
            speed = self.speed
        self._move([None, None, self.horizontal_move_z], speed)

    def _invoke_callback(self, results):
        self.printer.lookup_object("toolhead").get_last_move_time()
        res = self.finalize_callback(results)
        return res != "retry"

    def _move_next(self, probe_num):
        nextpos = list(self.probe_points[probe_num])
        if self.use_offsets:
            nextpos[0] -= self.probe_offsets[0]
            nextpos[1] -= self.probe_offsets[1]
        self._move(nextpos, self.speed)

    def start_probe(self, gcmd):
        probe = self.printer.lookup_object("probe", None)
        self.horizontal_move_z = gcmd.get_float(
            "HORIZONTAL_MOVE_Z", self.default_horizontal_move_z)
        self.lift_speed = probe.get_probe_params(gcmd)["lift_speed"]
        self.probe_offsets = probe.get_offsets(gcmd)
        probe_session = probe.start_probe_session(gcmd)
        probe_num = 0
        while 1:
            self._raise_tool(not probe_num)
            if probe_num >= len(self.probe_points):
                results = probe_session.pull_probed_results()
                done = self._invoke_callback(results)
                if done:
                    break
                probe_num = 0
            self._move_next(probe_num)
            probe_session.run_probe(gcmd)
            probe_num += 1
        probe_session.end_probe_session()


class HelferOhneMoveNext(object):
    """Anderer Klipper-Stand: gleiche Schleife, aber das Anfahren heißt
    anders (kein ``_move_next``)."""

    _move = FakeProbePointsHelper._move
    _raise_tool = FakeProbePointsHelper._raise_tool
    _invoke_callback = FakeProbePointsHelper._invoke_callback
    _anfahren = FakeProbePointsHelper._move_next

    def __init__(self, vorlage):
        self.__dict__.update(vars(vorlage))

    def start_probe(self, gcmd):
        probe = self.printer.lookup_object("probe", None)
        self.horizontal_move_z = gcmd.get_float(
            "HORIZONTAL_MOVE_Z", self.default_horizontal_move_z)
        self.lift_speed = probe.get_probe_params(gcmd)["lift_speed"]
        self.probe_offsets = probe.get_offsets(gcmd)
        probe_session = probe.start_probe_session(gcmd)
        probe_num = 0
        while 1:
            self._raise_tool(not probe_num)
            if probe_num >= len(self.probe_points):
                results = probe_session.pull_probed_results()
                if self._invoke_callback(results):
                    break
                probe_num = 0
            self._anfahren(probe_num)
            probe_session.run_probe(gcmd)
            probe_num += 1
        probe_session.end_probe_session()


class FakeProbeManager:
    """bed_mesh.py:809-885 (Ausschnitt)."""

    def __init__(self, printer, finalize_cb, base_points):
        self.printer = printer
        self.base_points = base_points
        self.substitutes = collections.OrderedDict()
        self.zref_mode = 0
        self.zero_ref_pos = None
        self.probe_helper = FakeProbePointsHelper(printer, finalize_cb)
        self.probe_helper.use_xy_offsets(True)
        self.probe_helper.update_probe_points(self.get_std_path(), 3)

    def get_std_path(self):
        path = list(self.base_points)
        if self.zref_mode == 2:
            path.append(self.zero_ref_pos)
        return path

    def set_zero_ref_probe(self, pos):
        self.zref_mode = 2
        self.zero_ref_pos = pos
        self.probe_helper.update_probe_points(self.get_std_path(), 3)

    def get_zero_ref_mode(self):
        return self.zref_mode

    def get_substitutes(self):
        return self.substitutes

    def get_base_points(self):
        return self.base_points

    def start_probe(self, gcmd):
        self.probe_helper.start_probe(gcmd)


class FakeBedMeshCalibrate:
    """bed_mesh.py:324-337, 644-710 (Ausschnitt)."""

    def __init__(self, printer, base_points):
        self.printer = printer
        self.finalize_calls = []
        self.retry_einmal = False
        self.probe_mgr = FakeProbeManager(printer, self.probe_finalize,
                                          base_points)

    def cmd_BED_MESH_CALIBRATE(self, gcmd):
        self.probe_mgr.start_probe(gcmd)

    def probe_finalize(self, positions):
        roh = positions
        positions = [[round(p.bed_x, 2), round(p.bed_y, 2), p.bed_z]
                     for p in positions]
        z_offset = 0.0
        if self.probe_mgr.get_zero_ref_mode() == 2:
            z_offset = positions.pop()[2]
        base = self.probe_mgr.get_base_points()
        if len(base) != len(positions):
            raise CommandError("bed_mesh: invalid position list size")
        self.finalize_calls.append({"roh": roh, "pos": positions,
                                    "z_offset": z_offset})
        if self.retry_einmal:
            self.retry_einmal = False
            return "retry"
        return None


class FakeBedMesh:
    def __init__(self, printer, base_points):
        self.bmc = FakeBedMeshCalibrate(printer, base_points)


class Aufbau:
    """Drucker-Attrappe mit bed_mesh, Sonde und Wächter."""

    def __init__(self, mode="report", werte=None, form=None, nx=9, ny=9,
                 offsets=(0.0, 0.0, 0.0), bed_mesh=True, probe=True,
                 connect=True):
        self.printer = FakePrinter()
        self.gcode = self.printer.objects["gcode"]
        self.toolhead = self.printer.objects["toolhead"]
        self.base = [(p[0], p[1]) for p in raster(nx, ny)]
        self.probe = None
        if probe:
            self.probe = FakeProbe(self.printer, form, offsets)
            self.printer.add_object("probe", self.probe)
        self.bed_mesh = None
        if bed_mesh:
            self.bed_mesh = FakeBedMesh(self.printer, self.base)
            self.printer.add_object("bed_mesh", self.bed_mesh)
        cfg = dict(werte or {})
        if mode is not None:
            cfg["mode"] = mode
        self.guard = G.load_config(FakeConfig(self.printer, werte=cfg))
        self.printer.add_object("k2_mesh_guard", self.guard)
        if connect:
            self.connect()

    def connect(self):
        self.printer.send_event("klippy:connect")

    @property
    def bmc(self):
        return self.bed_mesh.bmc

    @property
    def helper(self):
        return self.bed_mesh.bmc.probe_mgr.probe_helper

    def ausreisser(self, x, y, folge):
        self.probe.einmalig[(round(x, 2), round(y, 2))] = list(folge)

    def vermessen(self, params=None):
        self.gcmd = FakeGcmd(params)
        self.bmc.cmd_BED_MESH_CALIBRATE(self.gcmd)
        return self.bmc.finalize_calls

    def z_bei(self, x, y, call=-1):
        for p in self.bmc.finalize_calls[call]["pos"]:
            if abs(p[0] - x) < 0.01 and abs(p[1] - y) < 0.01:
                return p[2]
        raise KeyError((x, y))

    def meldungen(self):
        return [z for z in self.gcode.lines if "[k2_mesh_guard]" in z]


# ---------------------------------------------------------------------
# 7. Hülle: off / report / act
# ---------------------------------------------------------------------

def test_attrappe_ohne_waechter():
    a = Aufbau(mode="off", form=schraege)
    orig = a.helper.finalize_callback
    calls = a.vermessen()
    check("Attrappe: Vermessung ohne Wächter liefert 81 Punkte, 1 Rückruf",
          len(calls) == 1 and len(calls[0]["pos"]) == 81
          and a.probe.probe_session.ends == 1
          and a.probe.probe_session.hw_probe_session is None)
    check("mode off: Rückruf und start_probe NICHT umhüllt, nicht "
          "eingehängt",
          a.helper.finalize_callback == orig
          and "start_probe" not in vars(a.helper)
          and a.guard.hooked is False and a.guard.mode == "off"
          and a.meldungen() == [])
    st = a.guard.get_status()
    check("mode off: Status aus, keine Zählung",
          st["mode"] == "off" and st["last_action"] == "off"
          and st["counters"]["meshes"] == 0, st)


def test_report():
    a = Aufbau(mode=None, form=rauschen)      # Default = report
    check("Default-Modus report, eingehängt",
          a.guard.mode == "report" and a.guard.hooked is True
          and a.helper.finalize_callback == a.guard._finalize
          and "start_probe" not in vars(a.helper))
    calls = a.vermessen()
    st = a.guard.get_status()
    check("report, sauberes Bett: 1 Rückruf, keine Meldung",
          len(calls) == 1 and a.meldungen() == []
          and st["last_action"] == "none" and st["last_points"] == 81
          and st["last_outlier_count"] == 0
          and st["counters"]["meshes"] == 1, (a.meldungen(), st))
    a.ausreisser(175.0, 175.0, [0.6])
    vorher = len(a.probe.calls)
    calls = a.vermessen()
    st = a.guard.get_status()
    check("report: Ausreißer wird NICHT geändert (Wert bleibt 0,6)",
          len(calls) == 2 and abs(a.z_bei(175.0, 175.0) - 0.6) < 1e-9)
    check("report: nichts neu angetastet (81 Antastungen)",
          len(a.probe.calls) - vorher == 81)
    check("report: Ergebnisobjekte unverändert weitergereicht",
          all(isinstance(r, ProbeResult) for r in calls[1]["roh"])
          and len(calls[1]["roh"]) == 81)
    m = a.meldungen()
    check("report: Meldung mit Präfix, Position, Werten",
          len(m) == 2 and all(z.startswith("// [k2_mesh_guard]") for z in m)
          and "1 von 81" in m[0] and "report" in m[0]
          and "X=175.00 Y=175.00" in m[1] and "+0.600" in m[1], m)
    check("report: Status",
          st["last_action"] == "reported" and st["last_outlier_count"] == 1
          and st["last_outliers"][0]["x"] == 175.0
          and st["last_outliers"][0]["confirmed"] is None
          and st["last_outliers"][0]["replaced"] is False
          and st["last_remaining_count"] == 1
          and st["counters"] == {"meshes": 2, "outliers": 1,
                                 "reprobed_points": 0, "replaced_values": 0,
                                 "too_many": 0, "skipped": 0,
                                 "guard_errors": 0}, st)
    check("report: Sitzung sauber beendet",
          a.probe.probe_session.hw_probe_session is None
          and a.probe.probe_session.starts == a.probe.probe_session.ends)


def test_act():
    form = lambda x, y: schraege(x, y) + rauschen(x, y)   # noqa: E731
    a = Aufbau(mode="act", form=form)
    check("act: Rückruf und start_probe umhüllt",
          a.helper.finalize_callback == a.guard._finalize
          and vars(a.helper).get("start_probe") == a.guard._start_probe)
    # sauberes Bett: nichts zu tun
    calls = a.vermessen()
    check("act, sauberes Bett: 81 Antastungen, 1 Rückruf, keine Meldung",
          len(a.probe.calls) == 81 and len(calls) == 1
          and a.meldungen() == [])
    # Fehlwert (Materialrest fällt danach ab): 1. Messung +0,6
    x, y = 132.5, 217.5
    soll = form(x, y)
    a.ausreisser(x, y, [soll + 0.6])
    a.probe.calls[:] = []
    a.toolhead.moves[:] = []
    calls = a.vermessen()
    check("act: Original-Rückruf genau einmal", len(calls) == 2)
    check("act: Wert ersetzt (bed_mesh rechnet mit dem bestätigten Wert)",
          abs(a.z_bei(x, y) - soll) < 1e-9, a.z_bei(x, y))
    roh = calls[1]["roh"]
    i = index_bei([(r.bed_x, r.bed_y) for r in roh], x, y)
    check("act: Ergebnisobjekt bleibt ProbeResult, nur bed_z geändert",
          isinstance(roh[i], ProbeResult) and roh[i].bed_x == x
          and roh[i].bed_y == y and abs(roh[i].bed_z - soll) < 1e-9
          and abs(roh[i].test_z - (soll + 0.6)) < 1e-9
          and len(roh) == 81, roh[i])
    andere = [abs(p[2] - form(p[0], p[1])) for n, p in
              enumerate(calls[1]["pos"]) if n != i]
    check("act: alle anderen 80 Werte unverändert", max(andere) < 1e-9)
    check("act: 81 + repeats (2) Antastungen, Nachmessung am selben XY",
          len(a.probe.calls) == 83
          and a.probe.calls[81][:2] == (x, y)
          and a.probe.calls[82][:2] == (x, y), a.probe.calls[81:])
    check("act: jede Nachmessung startet auf horizontal_move_z (5)",
          a.probe.calls[81][2] == 5.0 and a.probe.calls[82][2] == 5.0,
          a.probe.calls[81:])
    # Bewegungen nach dem letzten regulären Punkt
    letzte = a.toolhead.moves[-5:]
    check("act: Anfahrt heben(7) -> XY(100) -> heben(7) -> heben(7)",
          [m[1] for m in letzte] == [7.0, 7.0, 100.0, 7.0, 7.0]
          and letzte[2][0][:2] == (x, y) and letzte[2][0][2] == 5.0
          and letzte[3][0] == (x, y, 5.0) and letzte[4][0] == (x, y, 5.0),
          letzte)
    check("act: Sitzung erst NACH dem Rückruf beendet, genau einmal je "
          "Vermessung",
          a.probe.probe_session.hw_probe_session is None
          and a.probe.probe_session.starts == 2
          and a.probe.probe_session.ends == 2 and a.probe.hw.ended == 2)
    m = a.meldungen()
    check("act: Meldungen (Kopf, Ausreißer, neue Werte, bestätigt, ok)",
          len(m) == 4 and "act" in m[0]
          and "X=132.50 Y=217.50" in m[1] and "Nachbar-Median" in m[1]
          and "nachgemessen" in m[2] and "bestätigt" in m[2]
          and "kein Punkt mehr auffällig" in m[3]
          and all(z.startswith("// [k2_mesh_guard]") for z in m), m)
    st = a.guard.get_status()
    e = st["last_outliers"][0]
    check("act: Status mit neuen Werten und bestätigtem Wert",
          st["last_action"] == "reprobed" and st["last_outlier_count"] == 1
          and st["last_remaining_count"] == 0 and len(e["new_z"]) == 2
          and abs(e["confirmed"] - soll) < 1e-6 and e["replaced"] is True
          and abs(e["z"] - (soll + 0.6)) < 1e-6
          and st["counters"]["reprobed_points"] == 1
          and st["counters"]["replaced_values"] == 1
          and st["counters"]["meshes"] == 2, st)
    check("act: nach der Vermessung kein gemerkter Befehl mehr",
          a.guard._gcmd is None)


def test_act_varianten():
    # Sonden-Versatz: Anfahrt = Punkt minus Versatz (probe.py:476-478)
    a = Aufbau(mode="act", offsets=(10.0, -4.0, 0.0))
    a.ausreisser(175.0, 175.0, [0.6])
    a.vermessen()
    xy = [m[0][:2] for m in a.toolhead.moves if m[1] == 100.0][-1]
    check("act mit Sonden-Versatz: Kopf fährt Punkt − Versatz an, "
          "Sonde misst am Punkt",
          xy == (165.0, 179.0) and a.probe.calls[-1][:2] == (175.0, 175.0)
          and abs(a.z_bei(175.0, 175.0)) < 1e-9, (xy, a.probe.calls[-1]))
    # HORIZONTAL_MOVE_Z der Vermessung gilt auch fürs Nachmessen
    a = Aufbau(mode="act")
    a.ausreisser(175.0, 175.0, [0.6])
    a.vermessen({"HORIZONTAL_MOVE_Z": "8"})
    check("act: Hubhöhe aus dem Befehl (8 mm) auch beim Nachmessen",
          a.probe.calls[-1][2] == 8.0 and a.probe.calls[-2][2] == 8.0
          and a.toolhead.pos[2] == 8.0, a.probe.calls[-2:])
    # repeats 3
    a = Aufbau(mode="act", werte={"repeats": "3"})
    a.ausreisser(5.0, 5.0, [0.6])
    a.vermessen()
    check("act: repeats 3 -> 3 Nachmessungen an der Ecke, Wert korrigiert",
          len(a.probe.calls) == 84 and abs(a.z_bei(5.0, 5.0)) < 1e-9)
    # Materialrest bleibt: alle Messungen hoch -> Wert bleibt, Warnung
    a = Aufbau(mode="act")
    a.ausreisser(175.0, 175.0, [0.6, 0.61, 0.59])
    calls = a.vermessen()
    st = a.guard.get_status()
    m = a.meldungen()
    check("act: bestätigter Ausreißer bleibt (0,6), Warnung „bleiben“",
          abs(a.z_bei(175.0, 175.0) - 0.6) < 1e-9 and len(calls) == 1
          and st["last_remaining_count"] == 1
          and st["last_outliers"][0]["replaced"] is False
          and st["counters"]["replaced_values"] == 0
          and any("bleiben auch nach dem Nachmessen" in z for z in m)
          and any("Bett/Düse prüfen" in z for z in m), (st, m))
    # große Verschiebung -> Warnung max_shift, Wert trotzdem übernommen
    a = Aufbau(mode="act")
    a.ausreisser(175.0, 175.0, [1.6])
    a.vermessen()
    m = a.meldungen()
    check("act: Verschiebung 1,6 > max_shift 1,0 -> Warnung, Wert "
          "übernommen",
          abs(a.z_bei(175.0, 175.0)) < 1e-9
          and any("max_shift" in z and "WARNUNG" in z for z in m), m)
    a = Aufbau(mode="act", werte={"max_shift": "2"})
    a.ausreisser(175.0, 175.0, [1.6])
    a.vermessen()
    check("act: max_shift 2 -> keine Verschiebungs-Warnung",
          not any("max_shift" in z for z in a.meldungen()))
    # zwei Ausreißer: Reihenfolge wie gemessen
    a = Aufbau(mode="act")
    a.ausreisser(345.0, 345.0, [0.5])
    a.ausreisser(47.5, 5.0, [-0.5])
    a.vermessen()
    check("act: zwei Ausreißer, beide korrigiert, in Messreihenfolge",
          abs(a.z_bei(345.0, 345.0)) < 1e-9
          and abs(a.z_bei(47.5, 5.0)) < 1e-9
          and [c[:2] for c in a.probe.calls[81:]] == [
              (47.5, 5.0), (47.5, 5.0), (345.0, 345.0), (345.0, 345.0)]
          and a.guard.get_status()["counters"]["replaced_values"] == 2,
          a.probe.calls[81:])
    # zu viele Ausreißer: nicht nachmessen
    a = Aufbau(mode="act", werte={"max_points": "2"})
    for x, y in ((90.0, 90.0), (175.0, 175.0), (260.0, 260.0)):
        a.ausreisser(x, y, [0.7])
    calls = a.vermessen()
    st = a.guard.get_status()
    m = a.meldungen()
    check("act, zu viele Ausreißer: nichts neu angetastet, Werte "
          "unverändert, Meldung",
          len(a.probe.calls) == 81 and len(calls) == 1
          and abs(a.z_bei(175.0, 175.0) - 0.7) < 1e-9
          and st["last_action"] == "too_many"
          and st["last_outlier_count"] == 3
          and st["counters"]["too_many"] == 1
          and "Bett/Düse prüfen" in m[0] and len(m) == 4, (st, m))
    # adaptives Raster 4×3
    a = Aufbau(mode="act", nx=4, ny=3, form=schraege)
    x, y = a.base[5]
    a.ausreisser(x, y, [schraege(x, y) + 0.5])
    a.vermessen()
    check("act: adaptives Raster 4×3 korrigiert",
          abs(a.z_bei(x, y) - schraege(x, y)) < 1e-9
          and len(a.probe.calls) == 14)
    # Rückgabe "retry" wird durchgereicht (probe.py:471-472, 510-511)
    a = Aufbau(mode="act")
    a.bmc.retry_einmal = True
    calls = a.vermessen()
    check("act: Rückgabe „retry“ durchgereicht -> zweite Runde",
          len(calls) == 2 and len(a.probe.calls) == 162
          and a.guard.get_status()["counters"]["meshes"] == 2)


def test_bed_mesh_besonderheiten():
    # Nullpunkt außerhalb des Rasters (bed_mesh.py:658-665, 1017-1018)
    a = Aufbau(mode="act", form=lambda x, y: 0.2)
    a.bmc.probe_mgr.set_zero_ref_probe((175.0, 380.0))
    a.probe.einmalig[(175.0, 380.0)] = [1.5]
    a.ausreisser(175.0, 175.0, [0.8])
    calls = a.vermessen()
    st = a.guard.get_status()
    check("Nullpunkt-Zusatzpunkt wird nicht bewertet, Raster schon",
          len(calls) == 1 and calls[0]["z_offset"] == 1.5
          and st["last_points"] == 81 and st["last_outlier_count"] == 1
          and abs(a.z_bei(175.0, 175.0) - 0.2) < 1e-9
          and len(calls[0]["roh"]) == 82, st)
    # faulty_region aktiv -> übersprungen
    a = Aufbau(mode="act")
    a.bmc.probe_mgr.substitutes[3] = [(1.0, 1.0)]
    a.ausreisser(175.0, 175.0, [0.8])
    calls = a.vermessen()
    st = a.guard.get_status()
    check("faulty_region aktiv: Prüfung übersprungen, Werte unverändert, "
          "eine Meldung",
          len(calls) == 1 and abs(a.z_bei(175.0, 175.0) - 0.8) < 1e-9
          and st["last_action"] == "skipped"
          and st["counters"]["skipped"] == 1 and len(a.meldungen()) == 1
          and len(a.probe.calls) == 81, (st, a.meldungen()))


# ---------------------------------------------------------------------
# 8./9. Fehler im Wächter, Sondenfehler
# ---------------------------------------------------------------------

def test_fehler_im_waechter():
    for mode in ("report", "act"):
        a = Aufbau(mode=mode)
        a.ausreisser(175.0, 175.0, [0.6])
        alt = G.analysiere

        def kaputt(*args, **kw):
            raise ZeroDivisionError("Testfehler")
        G.analysiere = kaputt
        try:
            calls = a.vermessen()
        except Exception as exc:
            calls = repr(exc)
        finally:
            G.analysiere = alt
        st = a.guard.get_status()
        m = a.meldungen()
        check("Ausnahme im Wächter (%s): Original-Rückruf einmal mit "
              "unveränderten Ergebnissen" % mode,
              isinstance(calls, list) and len(calls) == 1
              and abs(a.z_bei(175.0, 175.0) - 0.6) < 1e-9
              and len(calls[0]["roh"]) == 81, calls)
        check("Ausnahme im Wächter (%s): genau eine Meldung, gezählt"
              % mode,
              len(m) == 1 and "ZeroDivisionError" in m[0]
              and "UNVERÄNDERTEN" in m[0]
              and st["counters"]["guard_errors"] == 1
              and st["last_action"] == "error", (m, st))
        check("Ausnahme im Wächter (%s): Sitzung sauber beendet" % mode,
              a.probe.probe_session.hw_probe_session is None)
    # Fehler MITTEN im Nachmessen (kein Sondenfehler): Liste unverändert
    a = Aufbau(mode="act")
    a.ausreisser(90.0, 90.0, [0.6])
    a.ausreisser(260.0, 260.0, [0.6])
    alt = G.bestaetigter_wert
    zaehler = []

    def zweiter_kaputt(alt_z, neue):
        zaehler.append(1)
        if len(zaehler) == 2:
            raise KeyError("Testfehler 2")
        return alt(alt_z, neue)
    G.bestaetigter_wert = zweiter_kaputt
    try:
        calls = a.vermessen()
    finally:
        G.bestaetigter_wert = alt
    check("Wächter-Fehler mitten im Nachmessen: KEIN Wert geändert (auch "
          "der erste nicht), Rückruf einmal, Düse oben",
          len(calls) == 1 and abs(a.z_bei(90.0, 90.0) - 0.6) < 1e-9
          and abs(a.z_bei(260.0, 260.0) - 0.6) < 1e-9
          and a.toolhead.pos[2] == 5.0
          and a.guard.get_status()["counters"]["guard_errors"] == 1
          and a.guard.get_status()["counters"]["replaced_values"] == 0,
          (calls, a.toolhead.pos))
    # danach funktioniert die nächste Vermessung wieder
    a.ausreisser(90.0, 90.0, [0.6])
    calls = a.vermessen()
    check("Nach einem Wächter-Fehler: nächste Vermessung normal",
          len(calls) == 2 and abs(a.z_bei(90.0, 90.0)) < 1e-9)


def test_sondenfehler():
    a = Aufbau(mode="act")
    a.ausreisser(175.0, 175.0, [0.6])
    a.probe.fail_at = 83               # zweite Nachmessung
    fehler = None
    try:
        a.vermessen()
    except CommandError as exc:
        fehler = exc
    check("Sondenfehler beim Nachmessen wird weitergereicht "
          "(gleicher Fehler)",
          fehler is not None and "prior to movement" in str(fehler), fehler)
    check("Sondenfehler: Original-Rückruf NICHT aufgerufen, kein "
          "Wächter-Fehler gezählt",
          a.bmc.finalize_calls == []
          and a.guard.get_status()["counters"]["guard_errors"] == 0
          and not any("interner Fehler" in z for z in a.meldungen()))
    # anderer Ausnahmetyp aus der Sonde: ebenfalls weitergereicht
    a = Aufbau(mode="act")
    a.ausreisser(175.0, 175.0, [0.6])
    a.probe.bug_at = 82
    fehler = None
    try:
        a.vermessen()
    except RuntimeError as exc:
        fehler = exc
    check("Beliebige Ausnahme der Sonde wird weitergereicht",
          fehler is not None and a.bmc.finalize_calls == [], fehler)
    # G-Code-Fehler bei der Anfahrt: weitergereicht
    a = Aufbau(mode="act")
    a.ausreisser(175.0, 175.0, [0.6])
    orig_next = a.helper._move_next
    stand = []

    def move_next(n):
        if len(a.probe.calls) >= 81:
            stand.append(n)
            raise CommandError("Move out of range")
        return orig_next(n)
    a.helper._move_next = move_next
    fehler = None
    try:
        a.vermessen()
    except CommandError as exc:
        fehler = exc
    check("G-Code-Fehler bei der Anfahrt wird weitergereicht",
          fehler is not None and "out of range" in str(fehler)
          and a.bmc.finalize_calls == [] and len(stand) == 1)
    # Sondenfehler in der normalen Vermessung (ohne Wächter-Beteiligung)
    a = Aufbau(mode="act")
    a.probe.fail_at = 10
    try:
        a.vermessen()
        ok = False
    except CommandError:
        ok = True
    check("Sondenfehler in der normalen Vermessung unverändert "
          "(Wächter stört nicht)",
          ok and a.guard._gcmd is None and a.bmc.finalize_calls == [])


# ---------------------------------------------------------------------
# 10. Fehlende Attribute
# ---------------------------------------------------------------------

def test_fehlende_attribute():
    a = Aufbau(mode="act", bed_mesh=False)
    m = a.meldungen()
    check("Kein [bed_mesh]: nicht eingehängt, eine Meldung, kein Fehler",
          a.guard.hooked is False and len(m) == 1 and "bed_mesh" in m[0]
          and "nicht aktiv" in m[0], m)

    def ohne(pfad, attribut):
        b = Aufbau(mode="act", connect=False)
        ziel = b.bed_mesh
        for name in pfad:
            ziel = getattr(ziel, name)
        orig_cb = b.helper.finalize_callback
        if attribut == "finalize_callback":
            ziel.finalize_callback = None
        else:
            delattr(ziel, attribut)
        b.connect()
        return b, orig_cb

    for pfad, attribut in (((), "bmc"), (("bmc",), "probe_mgr"),
                           (("bmc", "probe_mgr"), "probe_helper"),
                           (("bmc", "probe_mgr", "probe_helper"),
                            "finalize_callback")):
        b, _cb = ohne(pfad, attribut)
        m = b.meldungen()
        check("Attribut %s fehlt: einmal gemeldet, nicht eingehängt"
              % attribut,
              b.guard.hooked is False and len(m) == 1
              and attribut in m[0] and b.guard.get_status()["hooked"]
              is False, m)
    # Vermessung läuft ohne Wächter weiter (probe_mgr fehlt nicht, nur
    # der Wächter ist draußen): bmc-Attribut für den Wächter versteckt
    b = Aufbau(mode="act", connect=False)
    echte = b.printer.objects["bed_mesh"]

    class Leer(object):
        pass
    b.printer.objects["bed_mesh"] = Leer()
    b.connect()
    b.printer.objects["bed_mesh"] = echte
    b.ausreisser(175.0, 175.0, [0.6])
    calls = b.vermessen()
    check("Nicht eingehängt: Vermessung läuft unverändert (Wert 0,6, "
          "81 Antastungen)",
          len(calls) == 1 and abs(b.z_bei(175.0, 175.0) - 0.6) < 1e-9
          and len(b.probe.calls) == 81)

    # act, aber Nachmessen nicht möglich -> melden, unverändert
    def gesperrt(name, vorbereiten, stichwort):
        c = Aufbau(mode="act", connect=False)
        vorbereiten(c)
        c.connect()
        c.ausreisser(175.0, 175.0, [0.6])
        calls = c.vermessen()
        st = c.guard.get_status()
        m = c.meldungen()
        check("act, %s: nur Meldung, Werte unverändert, Rückruf einmal"
              % name,
              c.guard.hooked is True and len(calls) == 1
              and abs(c.z_bei(175.0, 175.0) - 0.6) < 1e-9
              and len(c.probe.calls) == 81
              and st["last_action"] == "blocked"
              and st["last_outlier_count"] == 1
              and st["counters"]["guard_errors"] == 0
              and any("Nachmessen nicht möglich" in z and stichwort in z
                      for z in m), (st, m))

    def ohne_session(c):
        sitzung = c.probe.probe_session
        del c.probe.probe_session
        c.probe.start_probe_session = sitzung.start_probe_session
    gesperrt("Sonde ohne probe_session", ohne_session, "probe_session")

    def ohne_hw(c):
        sitzung = c.probe.probe_session

        class Zu(object):
            def __getattr__(self, name):
                if name == "hw_probe_session":
                    return None
                return getattr(sitzung, name)
        c.probe.probe_session = Zu()
        c.probe.start_probe_session = sitzung.start_probe_session
    gesperrt("keine offene Sitzung", ohne_hw, "keine Sonden-Sitzung offen")

    def ohne_move_next(c):
        c.bmc.probe_mgr.probe_helper = HelferOhneMoveNext(c.helper)
    gesperrt("Helfer ohne _move_next", ohne_move_next, "_move_next")

    def punktliste_falsch(c):
        # Punktliste des Helfers passt im Rückruf nicht zu den Ergebnissen
        # -> Auswertung mit den GEMESSENEN Koordinaten (bed_x/bed_y)
        helper = c.helper
        orig = helper._invoke_callback

        def invoke(results):
            voll = helper.probe_points
            helper.probe_points = voll[:-1]
            try:
                return orig(results)
            finally:
                helper.probe_points = voll
        helper._invoke_callback = invoke
    gesperrt("Punktliste passt nicht", punktliste_falsch, "Punktliste")

    def ohne_sonde(c):
        # Sonde nach dem Start der Vermessung nicht mehr auffindbar
        helper = c.helper
        orig = helper._invoke_callback

        def invoke(results):
            sonde = c.printer.objects.pop("probe")
            try:
                return orig(results)
            finally:
                c.printer.objects["probe"] = sonde
        helper._invoke_callback = invoke
    gesperrt("Objekt probe fehlt", ohne_sonde, "probe")
    # Ergebnisobjekt ohne _replace (kein Tupel): Kopie mit neuem bed_z

    class Obj(object):
        def __init__(self):
            self.bed_x, self.bed_y, self.bed_z = 1.0, 2.0, 0.6
    o = Obj()
    n = G.ersetze_z(o, 0.1)
    check("ersetze_z: Objekt ohne _replace -> Kopie, Original unberührt",
          n is not o and n.bed_z == 0.1 and o.bed_z == 0.6
          and n.bed_x == 1.0)
    r = ProbeResult(1.0, 2.0, 0.6, 1.0, 2.0, 0.6)
    n = G.ersetze_z(r, 0.1)
    check("ersetze_z: ProbeResult -> neues Tupel, Original unberührt",
          n.bed_z == 0.1 and r.bed_z == 0.6 and n.bed_x == 1.0
          and n.test_z == 0.6)


# ---------------------------------------------------------------------
# 11. Config-Fehler, Status, Befehl, Syntax
# ---------------------------------------------------------------------

def test_config_status_befehl():
    for werte, stichwort in (({"threshold": "3"}, "threshold"),
                             ({"mode": "an"}, "mode"),
                             ({"repeats": "0"}, "repeats"),
                             ({"max_points": "x"}, "max_points")):
        try:
            Aufbau(mode=None, werte=werte)
            f = None
        except ConfigError as exc:
            f = str(exc)
        check("Config-Fehler %s: deutscher Klartext als config.error"
              % (werte,),
              f is not None and "[k2_mesh_guard]" in f and stichwort in f,
              f)
    a = Aufbau(mode="act", werte={"threshold": "0.3", "max_points": "4"})
    st = a.guard.get_status(123.0)
    erwartet = {"mode", "hooked", "threshold", "min_neighbours",
                "max_points", "repeats", "max_shift", "edge_trend",
                "last_action", "last_points", "last_outlier_count",
                "last_outliers", "last_remaining_count", "counters"}
    check("get_status: Schlüssel vollständig, Werte aus der Config",
          set(st) == erwartet and st["mode"] == "act"
          and st["threshold"] == 0.3 and st["max_points"] == 4
          and st["hooked"] is True and st["last_outliers"] == []
          and st["last_outlier_count"] == 0, st)
    a.ausreisser(175.0, 175.0, [0.6])
    a.vermessen()
    st1 = a.guard.get_status()
    st1["last_outliers"][0]["x"] = -1
    st1["counters"]["meshes"] = 99
    st2 = a.guard.get_status()
    check("get_status: liefert Kopien (Aufrufer kann nichts verstellen)",
          st2["last_outliers"][0]["x"] == 175.0
          and st2["counters"]["meshes"] == 1)
    import json
    check("get_status: als JSON darstellbar", bool(json.dumps(st2)))
    # Befehl
    namen = sorted(a.gcode.commands)
    check("G-Code: KTWO_MESH_GUARD_STATUS registriert, mit Beschreibung",
          namen == ["KTWO_MESH_GUARD_STATUS"]
          and a.gcode.commands[namen[0]][1], namen)
    check("Befehlsname-Regel: nur Großbuchstaben/Unterstriche, keine "
          "Ziffern",
          all(re.match(r"^[A-Z_]+$", n) for n in namen), namen)
    gcmd = FakeGcmd(command="KTWO_MESH_GUARD_STATUS")
    a.gcode.commands["KTWO_MESH_GUARD_STATUS"][0](gcmd)
    t = "\n".join(gcmd.out)
    check("KTWO_MESH_GUARD_STATUS: Modus, Zähler, letzter Ausreißer",
          "Modus=act" in t and "eingehängt=ja" in t
          and "Vermessungen=1" in t and "X=175.00 Y=175.00" in t
          and "bestätigt" in t
          and all(z.startswith("[k2_mesh_guard]") for z in t.split("\n")),
          t)
    b = Aufbau(mode="off")
    gcmd = FakeGcmd(command="KTWO_MESH_GUARD_STATUS")
    b.gcode.commands["KTWO_MESH_GUARD_STATUS"][0](gcmd)
    check("KTWO_MESH_GUARD_STATUS bei mode off",
          "Modus=off" in gcmd.out[0] and "eingehängt=nein" in gcmd.out[0])
    check("load_config vorhanden", callable(G.load_config))


def test_py39():
    bad = []
    for f in ("k2_mesh_guard.py", "test_k2_mesh_guard.py"):
        try:
            with open(os.path.join(_HERE, f), encoding="utf-8") as fh:
                ast.parse(fh.read(), feature_version=(3, 9))
        except SyntaxError as exc:
            bad.append("%s: %s" % (f, exc))
    check("Python-3.9-Syntax", not bad, bad)


def main():
    for fn in (test_optionen, test_eben, test_einzelner, test_bettform,
               test_unvollstaendig, test_entscheidung,
               test_attrappe_ohne_waechter, test_report, test_act,
               test_act_varianten, test_bed_mesh_besonderheiten,
               test_fehler_im_waechter, test_sondenfehler,
               test_fehlende_attribute, test_config_status_befehl,
               test_py39):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(fn.__name__ + " (Ausnahme)", False, repr(exc))
    fails = [n for n, ok in RESULTS if not ok]
    if fails:
        print("ERGEBNIS: %d FEHLER" % (len(fails),))
        return 1
    print("ERGEBNIS: ok (%d/%d ok)" % (len(RESULTS), len(RESULTS)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
