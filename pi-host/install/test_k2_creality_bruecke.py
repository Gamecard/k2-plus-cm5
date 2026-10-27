#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline-Tests für k2_creality_bruecke (ohne tornado, ohne Netz)."""
import json
import sys

import k2_creality_bruecke as b

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


def msg(params, method="set"):
    return json.dumps({"method": method, "params": params})


check("K2-Pfad", b.datei_aus_pfad(
    "printprt:/mnt/UDISK/printer_data/gcodes/Beispiel_Teil.gcode")
    == "Beispiel_Teil.gcode")
check("K1-Pfad", b.datei_aus_pfad(
    "printprt:/usr/data/printer_data/gcodes/a.gcode") == "a.gcode")
check("Unterordner bleibt", b.datei_aus_pfad(
    "/mnt/UDISK/printer_data/gcodes/ordner/a.gcode") == "ordner/a.gcode")
check("fremder Pfad -> Dateiname", b.datei_aus_pfad("/tmp/x/b.gcode")
      == "b.gcode")
check("leer -> None", b.datei_aus_pfad("printprt:") is None)
check(".. abgewiesen", b.datei_aus_pfad(
    "/mnt/UDISK/printer_data/gcodes/../x.gcode") is None)
check("opGcodeFile", b.druckauftrag(msg({"opGcodeFile":
      "printprt:/mnt/UDISK/printer_data/gcodes/t.gcode"})) == "t.gcode")
check("multiColorPrint", b.druckauftrag(msg({"multiColorPrint":
      {"gcode": "/mnt/UDISK/printer_data/gcodes/m.gcode",
       "enableSelfTest": 0}})) == "m.gcode")
check("colorMatch ignoriert", b.druckauftrag(msg({"colorMatch":
      {"path": "/mnt/UDISK/printer_data/gcodes/m.gcode", "list": []}}))
      is None)
check("get ignoriert", b.druckauftrag(msg({"boxsInfo": 1}, "get")) is None)
check("kein JSON", b.druckauftrag("ok") is None)
check("heart_beat", b.druckauftrag('{"ModeCode":"heart_beat"}') is None)

print("ERGEBNIS: %s (%d/%d ok)" % ("ok" if not fehler else "FEHLER", ok,
                                  ok + len(fehler)))
sys.exit(1 if fehler else 0)
