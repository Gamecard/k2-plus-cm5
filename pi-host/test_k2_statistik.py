#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_k2_statistik.py – Verträglichkeit der K2-Module mit Klippers [statistics].

Klipper (extras/statistics.py) sammelt beim Start jedes Druckerobjekt, das ein
Attribut ``stats`` hat, und ruft es jede Sekunde als ``stats(eventtime)`` auf;
erwartet wird ``(aktiv: bool, text: str)``. Ein ``def stats(self)`` ohne
``eventtime`` (so war es bis 22.10.2026 in k2_485) wirft im Echtbetrieb
„TypeError … takes 1 positional argument but 2 were given“ und führt zum
Shutdown – Offline-Tests und der Batch-Lauf (ohne Reactor-Timer) sehen das nicht.

Prüfung rein statisch (AST, kein Import, keine Hardware):
  1. Jede Methode ``stats`` in einer Klasse der Module ``k2_*.py`` muss genau
     (self, eventtime) annehmen.
  2. ``self.stats = …`` ist nur in Klassen erlaubt, die nie als Druckerobjekt
     registriert werden (Liste INTERN unten), weil Klipper sonst das Attribut
     aufrufen würde.
  3. Die Klipper-Objektklasse von [k2_485] hat kein Attribut ``stats``.
"""
import ast
import glob
import os
import sys

HIER = os.path.dirname(os.path.abspath(__file__))
INTERN = {("k2_prtouch.py", "K2PrtouchApaxCheck")}  # nur intern benutzt, kein add_object

ok = 0
fehler = []


def check(name, bedingung, info=""):
    global ok
    if bedingung:
        ok += 1
    else:
        fehler.append("%s %s" % (name, info))
        print("FEHLER:", name, info)


dateien = sorted(glob.glob(os.path.join(HIER, "k2_*.py")))
check("Module gefunden", len(dateien) >= 5, len(dateien))
for pfad in dateien:
    name = os.path.basename(pfad)
    baum = ast.parse(open(pfad, encoding="utf-8").read(), filename=name)
    for klasse in [n for n in baum.body if isinstance(n, ast.ClassDef)]:
        for knoten in ast.walk(klasse):
            if isinstance(knoten, ast.FunctionDef) and knoten.name == "stats":
                args = [a.arg for a in knoten.args.args]
                check("%s:%s.stats(%s)" % (name, klasse.name, ", ".join(args)),
                      args == ["self", "eventtime"],
                      "– Klipper ruft stats(eventtime); Methode umbenennen")
            if (isinstance(knoten, ast.Attribute) and knoten.attr == "stats"
                    and isinstance(knoten.ctx, ast.Store)
                    and isinstance(knoten.value, ast.Name) and knoten.value.id == "self"):
                check("%s:%s self.stats= (Zeile %d)" % (name, klasse.name, knoten.lineno),
                      (name, klasse.name) in INTERN,
                      "– Attribut 'stats' an einem Druckerobjekt wäre für Klipper aufrufbar")

# 3. dynamisch, ohne Klipper: Klasse importieren und Attribut prüfen
sys.path.insert(0, HIER)
try:
    import k2_485
    check("PrinterK2Bus485 ohne Attribut 'stats'",
          not hasattr(k2_485.PrinterK2Bus485, "stats"))
    check("K2Bus485.bus_stats vorhanden", hasattr(k2_485.K2Bus485, "bus_stats"))
except Exception as exc:  # pragma: no cover
    check("k2_485 importierbar", False, repr(exc))

gesamt = ok + len(fehler)
if fehler:
    print("ERGEBNIS: FEHLER (%d/%d Prüfungen ok)" % (ok, gesamt))
    sys.exit(1)
print("ERGEBNIS: ok (%d/%d Prüfungen ok)" % (ok, gesamt))
