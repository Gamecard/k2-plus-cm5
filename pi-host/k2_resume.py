# -*- coding: utf-8 -*-
"""k2_resume – Wiedereinstieg in einen abgebrochenen Druck ab einer Schicht.

Ersatz für die Stock-Power-Loss-Fortsetzung (Creality), die Upstream-Klipper
nicht kennt. Ausgangslage: Ein Druck ist abgebrochen (Fehler, CANCEL_PRINT,
Not-Halt), das Teil liegt noch auf dem Bett. Erzeugt wird eine NEUE G-Code-
Datei ``<name>_ab_schicht_<n>.gcode`` im virtual_sdcard-Verzeichnis:

  Vorspann (Zustand der Originaldatei an der Schichtgrenze) + Rest der
  Originaldatei ab dem Marker ``;LAYER_CHANGE`` der Schicht n (Byte-genau).

Schichtmarker: ``;LAYER_CHANGE`` gefolgt von ``;Z:<mm>`` (Orca, Creality
Print 7, eigener Generator – an echten Dateien geprüft).

Zwei Arten der Z-Referenz (MODE):

- ``keep`` – Klipper wurde seit dem Abbruch NICHT neu gestartet und die
  Motoren waren nicht stromlos (``homed_axes == "xyz"``): kein Homing, die
  alte Z-Referenz bleibt. Der Vorspann hebt zuerst nur Z (Bett runter) auf
  eine sichere Höhe über dem höchsten bisher gedruckten Punkt, erst dann
  fährt der Kopf in X/Y.
- ``probe`` – nach Neustart/Not-Halt (Bett abgesackt, nicht referenziert):
  ``G28 X Y``, dann Z-Antasten an einem FREIEN Punkt P neben dem Teil
  (``KTWO_RESUME_ZHOME``; homing.cfg nimmt P statt der Bettmitte), Netz
  ``default`` und Korrektur der Z-Referenz ``m(Mitte) - m(P)``
  (``KTWO_RESUME_ZFIX``, SET_GCODE_OFFSET Z_ADJUST). P liegt außerhalb aller
  bisher gedruckten Bahnen (inkl. Vorreinigungslinie/Spülturm) + Rand in Y
  (Kopf und X-Brücke stehen nicht über dem Teil), X = Mitte der Objekte
  (Schräglage der Dual-Z wirkt nur in X).
  Z_TILT_ADJUST nur, wenn die Antastpunkte und die Fahrstrecke auf
  horizontal_move_z frei sind (sonst Kollision mit dem Teil); sonst Abbruch
  oder mit TILT=0 ohne Ausrichten (Schräglage nach Neustart unbekannt –
  Warnung mit geschätztem Fehler).

Danach: Heizen, CFS-Druckzustand, richtiges Werkzeug (``T<n>``: k2_box-Kette
schneidet/lädt/spült bzw. überspringt, wenn es schon steckt), Vordrucken am
Abfallschacht, Zustand (E-Modus, Lüfter, Kammer, Faktoren, Beschleunigung),
Kopf auf sicherer Höhe über den ersten Punkt der Schicht, absenken, Rest.

G-Code-Befehle (Modul ``[k2_resume]``):

  KTWO_RESUME_INFO FILE=<datei> [LAYER=<n>]        – Schichten, Zustand, Plan
  KTWO_RESUME_PREPARE FILE=<datei> LAYER=<n>|auto [Z=<mm>]
      [MODE=auto|keep|probe] [TILT=auto|1|0] [START=0|1]
  KTWO_RESUME_BEGIN / _ZHOME / _ZFIX                – nur aus der erzeugten Datei

Die Datei wird in einem eigenen Prozess erzeugt (dieses Skript, CLI), damit
Klipper bei großen Dateien (15 MB) nicht blockiert.
"""
import json
import logging
import math
import os
import re
import subprocess
import sys
import time

MARKER = ";LAYER_CHANGE"
VERSION = "1.0"
SAFE_MARGIN_DEFAULT = 10.0     # mm über dem höchsten gedruckten Punkt
PROBE_MARGIN_DEFAULT = 50.0    # mm Abstand P/Antastpunkt zu allem Gedruckten
PRIME_DEFAULT = 25.0           # mm Vordrucken am Abfallschacht
PROBE_TEMP_DEFAULT = 140.0     # Düse beim Antasten (Stock g28_ext_temp)
TILT_MOVE_Z = 5.0              # [z_tilt] horizontal_move_z
TILT_SLOPE_GUESS = 0.6 / 340.0  # live 22.10.2026 nach Neustart: 0,55-0,59 mm
CELL = 5.0                     # mm, Belegungsraster des Betts
BED_LIMIT = (0.0, 0.0, 352.0, 352.0)  # nur Bahnen auf dem Bett (Schacht Y378 nicht)
PROBE_FREE_R = 10.0            # mm um P: keinerlei Extrusion (Messgenauigkeit)
LOW_H = 1.0                    # mm: Vorreinigungslinien u. ä. unter dem Kopf ok
TILT_BODY_H = 3.0              # mm: unter dem Kopf beim Fahren auf Z5 erlaubt
BEAM_BAND = 30.0               # mm: halbe Breite der X-Brücke um Y des Kopfes
BEAM_CLEAR = 25.0              # mm: ANNAHME Luft unter der X-Brücke über der Düse
Z_MAX_SAFE = 340.0
CHILD_TIMEOUT_S = 180.0

_NUM = r"[-+]?(?:\d+\.?\d*|\.\d+)"
_WORD_RE = re.compile(r"([A-Za-z])(" + _NUM + r")")
_KV_RE = re.compile(r"([A-Za-z_]+)=(\S+)")


class ResumeError(Exception):
    pass


# ---------------------------------------------------------------------
# Datei lesen
# ---------------------------------------------------------------------
def _words(cmd):
    """'G1 X1 Y2.5 E.3' -> {'X': 1.0, 'Y': 2.5, 'E': 0.3} (ohne Befehl)."""
    out = {}
    for k, v in _WORD_RE.findall(cmd.split(None, 1)[1] if " " in cmd else ""):
        try:
            out[k.upper()] = float(v)
        except ValueError:
            pass
    return out


def _kv(cmd):
    return {k.upper(): v for k, v in _KV_RE.findall(cmd)}


def _poly_bbox(define_line):
    m = re.search(r"POLYGON=(\[\[.*\]\])", define_line)
    if not m:
        return None
    pts = re.findall(r"\[(" + _NUM + r")\s*,\s*(" + _NUM + r")\]", m.group(1))
    if not pts:
        return None
    xs = [float(a) for a, _b in pts]
    ys = [float(b) for _a, b in pts]
    return [min(xs), min(ys), max(xs), max(ys)]


def _grow(bb, x, y):
    if bb is None:
        return [x, y, x, y]
    return [min(bb[0], x), min(bb[1], y), max(bb[2], x), max(bb[3], y)]


def arc_points(x0, y0, x1, y1, i, j, clockwise, step=2.5):
    """Punkte entlang eines G2/G3-Bogens (I/J relativ zum Start)."""
    cx, cy = x0 + i, y0 + j
    r = math.hypot(i, j)
    if r < 1e-9:
        return [(x0, y0), (x1, y1)]
    a0 = math.atan2(y0 - cy, x0 - cx)
    a1 = math.atan2(y1 - cy, x1 - cx)
    if clockwise:
        sweep = a0 - a1
    else:
        sweep = a1 - a0
    while sweep <= 1e-12:
        sweep += 2 * math.pi
    n = max(1, int(r * sweep / step) + 1)
    sgn = -1.0 if clockwise else 1.0
    pts = [(cx + r * math.cos(a0 + sgn * sweep * k / n),
            cy + r * math.sin(a0 + sgn * sweep * k / n)) for k in range(n)]
    pts.append((x1, y1))
    return pts


def _on_bed(x, y):
    return BED_LIMIT[0] <= x <= BED_LIMIT[2] and BED_LIMIT[1] <= y <= BED_LIMIT[3]


def _cell(x, y):
    return (int(math.floor(x / CELL)), int(math.floor(y / CELL)))


def _mark(occ, x0, y0, x1, y1, z):
    """Strecke in das Raster eintragen (Höhe = max. Z je Zelle)."""
    n = max(1, int(math.hypot(x1 - x0, y1 - y0) / (CELL / 2.0)) + 1)
    for i in range(n + 1):
        t = i / float(n)
        c = _cell(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t)
        if occ.get(c, -1.0) < z:
            occ[c] = z


def occ_max(occ, x, y, r):
    """Höchste belegte Zelle im Umkreis r um (x, y) (0 = frei, -1 = nichts)."""
    best = -1.0
    k = int(math.ceil(r / CELL)) + 1
    cx, cy = _cell(x, y)
    for i in range(cx - k, cx + k + 1):
        for j in range(cy - k, cy + k + 1):
            h = occ.get((i, j))
            if h is None:
                continue
            # nächster Punkt der Zelle zum Mittelpunkt
            px = min(max(x, i * CELL), (i + 1) * CELL)
            py = min(max(y, j * CELL), (j + 1) * CELL)
            if math.hypot(px - x, py - y) <= r and h > best:
                best = h
    return best


def beam_max(occ, y, band=BEAM_BAND):
    """Höchste Belegung im Band |Y - y| <= band über die ganze Breite."""
    best = -1.0
    for (i, j), h in occ.items():
        lo, hi = j * CELL, (j + 1) * CELL
        if hi >= y - band and lo <= y + band and h > best:
            best = h
    return best


def _merge(a, b):
    if a is None:
        return b
    if b is None:
        return a
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3])]


def scan_file(path, layer=None, pos=None):
    """Einmal durch die Datei. Ergebnis (dict):

    layers: [{n, offset, line, z}] (1-basiert, Byte-Offset des Markers)
    state: Zustand unmittelbar VOR dem Marker der Schicht ``layer`` (oder
      None), bbox (alle Extrusionen davor), max_z, first_xy/first_z danach.
    layer_at_pos: Schicht, in der Byte ``pos`` liegt (für LAYER=auto).
    """
    st = {"nozzle": None, "bed": None, "chamber": None, "fans": {},
          "tool": None, "tool_src": "", "e_mode": "M83", "pos_mode": "G90",
          "speed_factor": None, "flow_factor": None, "accel": None,
          "accel_to_decel": None, "scv": None, "m204": None, "f": None,
          "m8200_open": False, "adaptive": False, "start_print": False}
    x = y = 0.0
    z = 0.0
    e_abs = 0.0
    bbox = None
    obj_bbox = None
    occ = {}
    max_z = 0.0
    defines = []
    header = []
    in_header = True
    total_hint = None
    layers = []
    pending_z = None       # Marker gesehen, ;Z: noch nicht
    snap = None
    after = {"first_xy": None, "first_z": None}
    want_after = False
    layer_at_pos = None
    offset = 0
    lineno = 0
    with open(path, "rb") as f:
        for raw in f:
            lineno += 1
            line_off = offset
            offset += len(raw)
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            s = line.strip()
            if in_header:
                if s == "" or s.startswith(";"):
                    if len(header) < 4000:
                        header.append(line)
                else:
                    in_header = False
            if s.startswith(";"):
                if s == MARKER:
                    n = len(layers) + 1
                    layers.append({"n": n, "offset": line_off,
                                   "line": lineno, "z": None})
                    pending_z = layers[-1]
                    if pos is not None and line_off <= pos:
                        layer_at_pos = n
                    if layer is not None and n == layer and snap is None:
                        snap = {"state": json.loads(json.dumps(st)),
                                "bbox": bbox, "obj_bbox": obj_bbox,
                                "occ": dict(occ),
                                "max_z": max_z, "z": z, "x": x, "y": y}
                        want_after = True
                elif pending_z is not None and s.startswith(";Z:"):
                    try:
                        pending_z["z"] = float(s[3:].strip())
                    except ValueError:
                        pass
                    pending_z = None
                elif total_hint is None:
                    m = re.match(r";\s*total layer (?:number|count)\s*[:=]\s*(\d+)",
                                 s, re.I)
                    if m:
                        total_hint = int(m.group(1))
                continue
            cmd = s.split(";", 1)[0].strip()
            if not cmd:
                continue
            word = cmd.split()[0].upper()
            if snap is not None and not want_after:
                continue           # nach der Zielschicht nur noch Marker zählen
            if word in ("G0", "G1", "G2", "G3"):
                w = _words(cmd)
                nx, ny, nz = x, y, z
                if st["pos_mode"] == "G91":
                    nx += w.get("X", 0.0)
                    ny += w.get("Y", 0.0)
                    nz += w.get("Z", 0.0)
                else:
                    nx = w.get("X", x)
                    ny = w.get("Y", y)
                    nz = w.get("Z", z)
                if "F" in w:
                    st["f"] = w["F"]
                if want_after:
                    if after["first_xy"] is None and ("X" in w or "Y" in w):
                        after["first_xy"] = [nx, ny]
                    if after["first_z"] is None and "Z" in w:
                        after["first_z"] = nz
                    if after["first_xy"] is not None and \
                            after["first_z"] is not None:
                        want_after = False
                    x, y, z = nx, ny, nz
                    continue
                extr = False
                if "E" in w:
                    if st["e_mode"] == "M83":
                        extr = w["E"] > 0
                    else:
                        extr = w["E"] > e_abs
                        e_abs = w["E"]
                if extr and (nx != x or ny != y) and _on_bed(x, y) \
                        and _on_bed(nx, ny):
                    # nur echte Bahnen auf dem Bett (Spülen am Schacht und
                    # reines Vordrucken ohne X/Y zählen nicht)
                    if word in ("G2", "G3") and ("I" in w or "J" in w):
                        pts = arc_points(x, y, nx, ny, w.get("I", 0.0),
                                         w.get("J", 0.0), word == "G2")
                    else:
                        pts = [(x, y), (nx, ny)]
                    for (ax, ay), (bx, by) in zip(pts, pts[1:]):
                        bbox = _grow(_grow(bbox, ax, ay), bx, by)
                        _mark(occ, ax, ay, bx, by, nz)
                    max_z = max(max_z, nz)
                x, y, z = nx, ny, nz
            elif want_after:
                continue
            elif word == "G90":
                st["pos_mode"] = "G90"
            elif word == "G91":
                st["pos_mode"] = "G91"
            elif word in ("M82", "M83"):
                st["e_mode"] = word
            elif word == "G92":
                w = _words(cmd)
                if "E" in w:
                    e_abs = w["E"]
                if "X" in w:
                    x = w["X"]
                if "Y" in w:
                    y = w["Y"]
                if "Z" in w:
                    z = w["Z"]
            elif word in ("M104", "M109"):
                w = _words(cmd)
                if "S" in w:
                    st["nozzle"] = w["S"]
            elif word in ("M140", "M190"):
                w = _words(cmd)
                if "S" in w:
                    st["bed"] = w["S"]
            elif word in ("M141", "M191"):
                w = _words(cmd)
                if "S" in w:
                    st["chamber"] = w["S"]
            elif word == "M106":
                w = _words(cmd)
                st["fans"][str(int(w.get("P", 0)))] = w.get("S", 255.0)
            elif word == "M107":
                w = _words(cmd)
                st["fans"][str(int(w.get("P", 0)))] = 0.0
            elif word == "M220":
                st["speed_factor"] = _words(cmd).get("S")
            elif word == "M221":
                st["flow_factor"] = _words(cmd).get("S")
            elif word == "M204":
                w = _words(cmd)
                st["m204"] = w.get("S", w.get("P"))
            elif word == "SET_VELOCITY_LIMIT":
                kv = _kv(cmd)
                for key, name in (("ACCEL", "accel"),
                                  ("ACCEL_TO_DECEL", "accel_to_decel"),
                                  ("SQUARE_CORNER_VELOCITY", "scv")):
                    if key in kv:
                        try:
                            st[name] = float(kv[key])
                        except ValueError:
                            pass
            elif re.match(r"^T\d+$", word):
                st["tool"] = int(word[1:])
                st["tool_src"] = word
            elif word == "M8200":
                w = cmd.upper().split()[1:]
                letters = {t[:1] for t in w}
                if "P" in letters:
                    st["m8200_open"] = True
                if "O" in letters:
                    st["m8200_open"] = False
                if "L" in letters and "F" not in letters:
                    m = re.search(r"\bI=?(\d+)", cmd.upper())
                    if m:
                        st["tool"] = int(m.group(1))
                        st["tool_src"] = "M8200 L I%s" % m.group(1)
            elif word in ("START_PRINT", "PRINT_START"):
                kv = _kv(cmd)
                st["start_print"] = True
                # EXTRUDER=/BED= wie START_PRINT (PRINT_START allgemeiner
                # Klipper-Profile); die _TEMP-Namen haben Vorrang
                for keys, name in ((("EXTRUDER_TEMP", "EXTRUDER"), "nozzle"),
                                   (("BED_TEMP", "BED"), "bed")):
                    for key in keys:
                        if key in kv:
                            try:
                                st[name] = float(kv[key])
                            except ValueError:
                                continue
                            break
                # ADAPTIVE ist seit 23.10.2026 Standard (fehlt = 1)
                st["adaptive"] = kv.get("ADAPTIVE", "1") not in ("0", "")
            elif word == "EXCLUDE_OBJECT_DEFINE":
                if len(defines) < 500:
                    defines.append(cmd)
                obj_bbox = _merge(obj_bbox, _poly_bbox(cmd))
    res = {"layers": layers, "total": len(layers), "total_hint": total_hint,
           "header": header, "defines": defines,
           "layer_at_pos": layer_at_pos, "size": offset}
    if snap is not None:
        snap.update(after)
        res["snap"] = snap
    return res


# ---------------------------------------------------------------------
# Planung
# ---------------------------------------------------------------------
def rect_distance(bb, x, y):
    """Abstand eines Punktes zu einem Rechteck (0 = innen)."""
    dx = max(bb[0] - x, 0.0, x - bb[2])
    dy = max(bb[1] - y, 0.0, y - bb[3])
    return math.hypot(dx, dy)


def point_free(occ, x, y, margin=PROBE_MARGIN_DEFAULT, body_h=LOW_H,
               head_z=None):
    """Antastpunkt frei? -> (ok, Grund). Unter der Düse (PROBE_FREE_R)
    keinerlei Bahn, unter dem Kopf (margin) nichts höher als body_h, unter
    der X-Brücke (Y-Band) nichts höher als BEAM_CLEAR (+ head_z)."""
    h = occ_max(occ, x, y, PROBE_FREE_R)
    if h >= 0:
        return False, ("bei X%.0f Y%.0f liegt Gedrucktes unter der Düse"
                       % (x, y))
    h = occ_max(occ, x, y, margin)
    if h > body_h:
        return False, ("bei X%.0f Y%.0f steht im Umkreis %.0f mm Gedrucktes "
                       "(%.1f mm hoch) unter dem Kopf" % (x, y, margin, h))
    hb = beam_max(occ, y)
    if hb > BEAM_CLEAR + (head_z or 0.0):
        return False, ("bei Y%.0f stünde die X-Brücke über %.1f mm hohem "
                       "Gedruckten" % (y, hb))
    return True, "frei"


def choose_probe_point(occ, obj_bbox, bbox, mesh_min=(5.0, 5.0),
                       mesh_max=(345.0, 345.0), margin=PROBE_MARGIN_DEFAULT):
    """Freier Antastpunkt P neben dem Teil, X möglichst = Mitte der Objekte
    (Dual-Z-Schräglage wirkt nur in X), sonst nah an der Bettmitte."""
    cx = (mesh_min[0] + mesh_max[0]) / 2.0
    cy = (mesh_min[1] + mesh_max[1]) / 2.0
    if not occ:
        return [round(cx, 2), round(cy, 2)]
    ref = obj_bbox or bbox
    tx = (ref[0] + ref[2]) / 2.0 if ref else cx
    cands = []
    xv = mesh_min[0]
    while xv <= mesh_max[0] + 1e-9:
        yv = mesh_min[1]
        while yv <= mesh_max[1] + 1e-9:
            cands.append((abs(xv - tx), abs(yv - cy), xv, yv))
            yv += CELL
        xv += CELL
    cands.sort()
    for _dx, _dy, xv, yv in cands:
        if point_free(occ, xv, yv, margin)[0]:
            return [round(xv, 2), round(yv, 2)]
    return None


def tilt_check(occ, tilt_points, start=None, margin=PROBE_MARGIN_DEFAULT,
               move_z=TILT_MOVE_Z):
    """Z_TILT_ADJUST (Fahrt auf move_z) ohne Kollision? -> (ok, Grund).
    Antastpunkte wie P frei; Fahrstrecke start -> P1 -> P2 ...: unter dem
    Kopf nichts höher als TILT_BODY_H, unter der Brücke nichts höher als
    BEAM_CLEAR + move_z."""
    if not occ:
        return True, "nichts gedruckt"
    for px, py in tilt_points:
        ok, why = point_free(occ, px, py, margin, body_h=TILT_BODY_H,
                             head_z=move_z)
        if not ok:
            return False, "Antastpunkt: " + why
    path = ([list(start)] if start else []) + [list(p) for p in tilt_points]
    for (ax, ay), (bx, by) in zip(path, path[1:]):
        n = max(1, int(math.hypot(bx - ax, by - ay) / CELL))
        for i in range(n + 1):
            t = i / float(n)
            x, y = ax + (bx - ax) * t, ay + (by - ay) * t
            h = occ_max(occ, x, y, margin)
            if h > TILT_BODY_H:
                return False, ("Fahrt auf Z%.0f bei X%.0f Y%.0f über %.1f mm "
                               "hohem Gedruckten" % (move_z, x, y, h))
            if beam_max(occ, y) > BEAM_CLEAR + move_z:
                return False, ("Fahrt auf Z%.0f: X-Brücke über hohem Teil "
                               "(Y%.0f)" % (move_z, y))
    return True, "Antastpunkte und Fahrstrecke frei"


def _fmt(v):
    if v is None:
        return None
    s = ("%.3f" % v).rstrip("0").rstrip(".")
    return s or "0"


def build_plan(scan, layer, mode, tilt="auto", safe_margin=SAFE_MARGIN_DEFAULT,
               probe_margin=PROBE_MARGIN_DEFAULT, mesh_min=(5.0, 5.0),
               mesh_max=(345.0, 345.0), tilt_points=((5.0, 175.0), (345.0, 175.0)),
               home_center=(175.0, 175.0)):
    """Prüfen + Plan (dict) oder ResumeError mit Klartext."""
    layers = scan["layers"]
    if not layers:
        raise ResumeError("keine Schichtmarker ';LAYER_CHANGE' in der Datei – "
                          "Wiedereinstieg nicht möglich")
    if not 1 <= layer <= len(layers):
        raise ResumeError("Schicht %d gibt es nicht (1..%d)" % (layer, len(layers)))
    snap = scan.get("snap")
    if snap is None:
        raise ResumeError("Zustand an Schicht %d nicht ermittelt" % layer)
    st = snap["state"]
    lz = layers[layer - 1]["z"]
    if lz is None:
        raise ResumeError("Schicht %d hat keine ';Z:'-Angabe" % layer)
    if st["m8200_open"]:
        raise ResumeError("an Schicht %d ist eine CFS-Wechselkette (M8200 P ohne "
                          "O) offen – andere Schicht wählen" % layer)
    if st["nozzle"] is None or st["nozzle"] < 150:
        raise ResumeError("Düsentemperatur an Schicht %d unklar (%r)"
                          % (layer, st["nozzle"]))
    if snap["first_xy"] is None:
        raise ResumeError("nach dem Marker der Schicht %d keine X/Y-Fahrt" % layer)
    if layer > 1 and snap["bbox"] is None:
        raise ResumeError("vor Schicht %d keine Extrusion gefunden – Datei "
                          "unklar" % layer)
    top = max(snap["max_z"], lz)
    safe_z = min(max(top + safe_margin, 10.0), Z_MAX_SAFE)
    if top + 2.0 > Z_MAX_SAFE:
        raise ResumeError("Teil zu hoch für sichere Fahrhöhe (%.1f mm)" % top)
    plan = {"layer": layer, "total": len(layers), "z": lz, "mode": mode,
            "safe_z": round(safe_z, 2), "top_z": round(top, 3),
            "state": st, "first_xy": snap["first_xy"],
            "first_z": snap["first_z"], "bbox": snap["bbox"],
            "obj_bbox": snap["obj_bbox"], "warnings": []}
    if st["tool"] is None:
        plan["warnings"].append("kein Werkzeugbefehl vor Schicht %d – das "
                                "Filament im Kopf wird verwendet" % layer)
    if mode == "probe":
        occ = snap["occ"]
        p = choose_probe_point(occ, snap["obj_bbox"], snap["bbox"], mesh_min,
                               mesh_max, probe_margin)
        if p is None:
            raise ResumeError("kein freier Antastpunkt neben dem Teil (Rand "
                              "%.0f mm) – Z-Referenz nicht sicher bestimmbar"
                              % probe_margin)
        plan["probe_xy"] = p
        plan["home_center"] = list(home_center)
        ok, why = tilt_check(occ, list(tilt_points), start=p,
                             margin=probe_margin)
        if tilt == "1" and not ok:
            raise ResumeError("Z_TILT_ADJUST nicht sicher: " + why)
        plan["tilt"] = ok if tilt in ("auto", "1") else False
        plan["tilt_reason"] = why
        if not plan["tilt"]:
            ref = snap["obj_bbox"] or snap["bbox"]
            dx = max(abs(ref[0] - p[0]), abs(ref[2] - p[0])) if ref else 0.0
            err = TILT_SLOPE_GUESS * dx
            if tilt == "auto" and not ok:
                raise ResumeError(
                    "Z_TILT_ADJUST nicht sicher (%s). Ohne Ausrichten ist die "
                    "Schräglage nach dem Neustart unbekannt (geschätzt bis "
                    "±%.2f mm über dem Teil). Mit TILT=0 trotzdem erzeugen."
                    % (why, err))
            plan["warnings"].append(
                "ohne Z_TILT_ADJUST: Schräglage unbekannt, geschätzter Fehler "
                "bis ±%.2f mm über dem Teil" % err)
    return plan


def build_preamble(plan, orig_name, prime=PRIME_DEFAULT,
                   probe_temp=PROBE_TEMP_DEFAULT):
    st = plan["state"]
    sz = _fmt(plan["safe_z"])
    L = []
    a = L.append
    a("; K2-Wiedereinstieg %s (k2_resume): %s ab Schicht %d von %d (Z %s)"
      % (VERSION, orig_name, plan["layer"], plan["total"], _fmt(plan["z"])))
    a("; Modus %s, sichere Fahrhöhe Z %s (höchster gedruckter Punkt %s)"
      % (plan["mode"], sz, _fmt(plan["top_z"])))
    for w in plan["warnings"]:
        a("; WARNUNG: " + w)
    a("SET_PRINT_STATS_INFO TOTAL_LAYER=%d CURRENT_LAYER=%d"
      % (plan["total"], plan["layer"]))
    a("KTWO_RESUME_BEGIN MODE=%s LAYER=%d SAFE_Z=%s" % (plan["mode"],
                                                       plan["layer"], sz))
    a("CLEAR_PAUSE")
    a("G90")
    bed = st["bed"] or 0.0
    if plan["mode"] == "probe":
        px, py = plan["probe_xy"]
        a("M140 S%s" % _fmt(bed))
        a("M104 S%s" % _fmt(probe_temp))
        a("G28 X Y")
        a("KTWO_RESUME_ZHOME X=%s Y=%s" % (_fmt(px), _fmt(py)))
        a("G1 Z%s F600" % sz)
        if bed:
            a("M190 S%s" % _fmt(bed))
        a("M109 S%s" % _fmt(probe_temp))
        if plan.get("tilt"):
            a("Z_TILT_ADJUST HORIZONTAL_MOVE_Z=%s" % _fmt(TILT_MOVE_Z))
            a("G1 Z%s F600" % sz)
            a("KTWO_RESUME_ZHOME X=%s Y=%s" % (_fmt(px), _fmt(py)))
            a("G1 Z%s F600" % sz)
        a("SET_GCODE_OFFSET Z=0")
        a("BED_MESH_PROFILE LOAD=default")
        a("KTWO_RESUME_ZFIX X=%s Y=%s" % (_fmt(px), _fmt(py)))
    else:
        # zuerst NUR Z (Bett nach unten bzw. nicht tiefer als sicher)
        a("G1 Z%s F600" % sz)
        if bed:
            a("M140 S%s" % _fmt(bed))
    if st["chamber"]:
        a("M141 S%s" % _fmt(st["chamber"]))
    a("M104 S%s" % _fmt(st["nozzle"]))
    if bed:
        a("M190 S%s" % _fmt(bed))
    a("_PI_WIEDEREINSTIEG_BOX_START")
    if st["tool"] is not None:
        a("T%d" % st["tool"])
    a("G90")
    a("G1 Z%s F600" % sz)
    a("M109 S%s" % _fmt(st["nozzle"]))
    a("_PI_WIEDEREINSTIEG_VORDRUCK E=%s" % _fmt(prime))
    a("G90")
    a(st["e_mode"])
    a("G92 E0")
    for p_, s_ in sorted(st["fans"].items()):
        if p_ == "0":
            a("M106 S%s" % _fmt(s_) if s_ else "M107")
        else:
            a("M106 P%s S%s" % (p_, _fmt(s_)))
    if st["speed_factor"] is not None:
        a("M220 S%s" % _fmt(st["speed_factor"]))
    if st["flow_factor"] is not None:
        a("M221 S%s" % _fmt(st["flow_factor"]))
    lim = []
    if st["accel"] is not None:
        lim.append("ACCEL=%s" % _fmt(st["accel"]))
    if st["accel_to_decel"] is not None:
        lim.append("ACCEL_TO_DECEL=%s" % _fmt(st["accel_to_decel"]))
    if st["scv"] is not None:
        lim.append("SQUARE_CORNER_VELOCITY=%s" % _fmt(st["scv"]))
    if lim:
        a("SET_VELOCITY_LIMIT " + " ".join(lim))
    elif st["m204"] is not None:
        a("M204 S%s" % _fmt(st["m204"]))
    fx, fy = plan["first_xy"]
    a("G1 Z%s F600" % sz)
    a("G0 X%s Y%s F12000" % (_fmt(fx), _fmt(fy)))
    zdown = plan["first_z"] if plan["first_z"] is not None else plan["z"]
    zdown = max(zdown, plan["z"])
    a("G1 Z%s F600" % _fmt(zdown))
    if st["f"]:
        a("G1 F%s" % _fmt(st["f"]))
    a("; ---- Originaldatei ab Schicht %d ----" % plan["layer"])
    return L


def write_resume(path, out_path, layer, mode, tilt="auto", prime=PRIME_DEFAULT,
                 **kw):
    scan = scan_file(path, layer=layer)
    plan = build_plan(scan, layer, mode, tilt=tilt, **kw)
    pre = build_preamble(plan, os.path.basename(path), prime=prime)
    start = scan["layers"][layer - 1]["offset"]
    tmp = out_path + ".tmp"
    with open(path, "rb") as src, open(tmp, "wb") as dst:
        for h in scan["header"]:
            dst.write((h + "\n").encode("utf-8"))
        for d in scan["defines"]:
            dst.write((d + "\n").encode("utf-8"))
        for p in pre:
            dst.write((p + "\n").encode("utf-8"))
        src.seek(start)
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            dst.write(chunk)
    os.replace(tmp, out_path)
    plan = dict(plan)
    plan["out"] = out_path
    plan["start_offset"] = start
    return plan


def info(path, layer=None, pos=None):
    scan = scan_file(path, layer=layer, pos=pos)
    zs = [l["z"] for l in scan["layers"] if l["z"] is not None]
    out = {"total": scan["total"], "total_hint": scan["total_hint"],
           "z_min": min(zs) if zs else None, "z_max": max(zs) if zs else None,
           "layer_at_pos": scan["layer_at_pos"], "size": scan["size"],
           "zs": [[l["n"], l["z"]] for l in scan["layers"]]}
    if scan.get("snap"):
        sn = scan["snap"]
        out["state"] = sn["state"]
        out["max_z"] = sn["max_z"]
        out["bbox"] = sn["bbox"]
        out["obj_bbox"] = sn["obj_bbox"]
        out["layer_z"] = scan["layers"][layer - 1]["z"]
    return out


def layer_for_z(zs, z):
    """[[n, z], ...] -> Schicht mit dem nächsten Z (gleich weit: die tiefere)."""
    best = None
    for n, lz in zs:
        if lz is None:
            continue
        if best is None or abs(lz - z) < abs(best[1] - z) - 1e-9:
            best = (n, lz)
    return best[0] if best else None


def out_name(fname, layer):
    stem = fname[:-6] if fname.lower().endswith(".gcode") else fname
    stem = re.sub(r"_ab_schicht_\d+$", "", stem)
    return "%s_ab_schicht_%d.gcode" % (stem, layer)


def _cli(argv):
    """python3 k2_resume.py info|build JSON-Parameter -> JSON auf stdout."""
    if len(argv) != 3 or argv[1] not in ("info", "build"):
        sys.stderr.write("Aufruf: k2_resume.py info|build '<json>'\n")
        return 2
    p = json.loads(argv[2])
    try:
        if argv[1] == "info":
            res = info(p["path"], p.get("layer"), p.get("pos"))
        else:
            kw = {k: p[k] for k in ("safe_margin", "probe_margin", "mesh_min",
                                    "mesh_max", "tilt_points", "home_center")
                  if k in p}
            res = write_resume(p["path"], p["out"], p["layer"], p["mode"],
                               tilt=p.get("tilt", "auto"),
                               prime=p.get("prime", PRIME_DEFAULT), **kw)
        sys.stdout.write(json.dumps({"ok": True, "result": res}))
    except ResumeError as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}))
    except Exception as exc:          # Datei/IO
        sys.stdout.write(json.dumps({"ok": False,
                                     "error": "%s: %s" % (type(exc).__name__, exc)}))
    return 0


# ---------------------------------------------------------------------
# Klipper-Modul
# ---------------------------------------------------------------------
class K2Resume:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.safe_margin = config.getfloat("safe_margin", SAFE_MARGIN_DEFAULT,
                                           minval=3.0, maxval=50.0)
        self.probe_margin = config.getfloat("probe_margin",
                                            PROBE_MARGIN_DEFAULT, minval=20.0,
                                            maxval=150.0)
        self.prime = config.getfloat("prime_mm", PRIME_DEFAULT, minval=0.,
                                     maxval=100.)
        self.z_home_at = None
        self.last = None
        self.busy = False
        for name, fn, desc in (
                ("KTWO_RESUME_INFO", self.cmd_info,
                 "Wiedereinstieg: Schichten/Zustand einer Datei (FILE= [LAYER=])"),
                ("KTWO_RESUME_PREPARE", self.cmd_prepare,
                 "Wiedereinstieg: Datei ab Schicht erzeugen (FILE= LAYER=|auto "
                 "[MODE=auto|keep|probe] [TILT=auto|1|0] [START=1])"),
                ("KTWO_RESUME_BEGIN", self.cmd_begin,
                 "nur aus einer Wiedereinstiegs-Datei: Voraussetzungen prüfen"),
                ("KTWO_RESUME_ZHOME", self.cmd_zhome,
                 "nur aus einer Wiedereinstiegs-Datei: G28 Z an X/Y"),
                ("KTWO_RESUME_ZFIX", self.cmd_zfix,
                 "nur aus einer Wiedereinstiegs-Datei: Z-Referenz auf Bettmitte "
                 "umrechnen")):
            self.gcode.register_command(name, fn, desc=desc)

    # --- Hilfen --------------------------------------------------------
    def _status(self, name):
        obj = self.printer.lookup_object(name, None)
        if obj is None or not hasattr(obj, "get_status"):
            return {}
        try:
            return obj.get_status(self.reactor.monotonic()) or {}
        except Exception:
            return {}

    def _sd_dir(self):
        sd = self.printer.lookup_object("virtual_sdcard", None)
        d = getattr(sd, "sdcard_dirname", None)
        if not d:
            raise ResumeError("[virtual_sdcard] fehlt")
        return d

    def _file(self, gcmd):
        name = gcmd.get("FILE")
        if "/" in name or name.startswith(".."):
            raise gcmd.error("FILE= nur Dateiname im G-Code-Verzeichnis")
        path = os.path.join(self._sd_dir(), name)
        if not os.path.isfile(path):
            raise gcmd.error("Datei %s nicht gefunden" % (name,))
        return name, path

    def _cfg_kw(self):
        out = {"safe_margin": self.safe_margin,
               "probe_margin": self.probe_margin}
        cfg = self._status("configfile").get("settings", {})
        bm = cfg.get("bed_mesh") or {}
        if bm.get("mesh_min") and bm.get("mesh_max"):
            mn, mx = list(bm["mesh_min"]), list(bm["mesh_max"])
            out["mesh_min"], out["mesh_max"] = mn, mx
            out["home_center"] = [(mn[0] + mx[0]) / 2.0, (mn[1] + mx[1]) / 2.0]
        zt = cfg.get("z_tilt") or {}
        if zt.get("points"):
            out["tilt_points"] = [list(p) for p in zt["points"]]
        return out

    def _run_child(self, mode, params):
        """Erzeugung/Analyse im eigenen Prozess; Reactor läuft weiter."""
        proc = subprocess.Popen(
            [sys.executable, os.path.abspath(__file__), mode,
             json.dumps(params)], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE)
        end = self.reactor.monotonic() + CHILD_TIMEOUT_S
        while proc.poll() is None:
            now = self.reactor.monotonic()
            if now > end:
                proc.kill()
                raise ResumeError("Erzeugung dauerte zu lange (> %.0f s)"
                                  % CHILD_TIMEOUT_S)
            self.reactor.pause(now + 0.2)
        out, err = proc.communicate()
        try:
            res = json.loads(out.decode("utf-8") or "{}")
        except ValueError:
            raise ResumeError("Erzeugung fehlgeschlagen: %s"
                              % err.decode("utf-8", "replace")[-300:])
        if not res.get("ok"):
            raise ResumeError(res.get("error") or "unbekannter Fehler")
        return res["result"]

    def _print_active(self):
        return self._status("print_stats").get("state") in ("printing",
                                                            "paused")

    # --- G-Code ---------------------------------------------------------
    def cmd_info(self, gcmd):
        name, path = self._file(gcmd)
        layer = gcmd.get_int("LAYER", None, minval=1)
        pos = None
        sd = self._status("virtual_sdcard")
        if sd.get("file_path") and os.path.basename(sd["file_path"]) == name:
            pos = sd.get("file_position") or None
        try:
            r = self._run_child("info", {"path": path, "layer": layer,
                                         "pos": pos})
        except ResumeError as exc:
            raise gcmd.error("[k2_resume] %s" % exc)
        msg = ["[k2_resume] %s: %d Schichten, Z %s..%s" % (
            name, r["total"], _fmt(r["z_min"]), _fmt(r["z_max"]))]
        if r.get("layer_at_pos"):
            msg.append("Abbruch lag in Schicht %d (Dateiposition %d)"
                       % (r["layer_at_pos"], pos))
        if layer and r.get("state"):
            s = r["state"]
            msg.append("Schicht %d: Z %s, Düse %s °C, Bett %s °C, Werkzeug %s, "
                       "höchster gedruckter Punkt Z %s" % (
                           layer, _fmt(r.get("layer_z")), _fmt(s["nozzle"]),
                           _fmt(s["bed"]), s["tool_src"] or "-",
                           _fmt(r.get("max_z"))))
        homed = self._status("toolhead").get("homed_axes", "")
        msg.append("Z-Referenz jetzt: %s" % (
            "erhalten (MODE=keep möglich)" if homed == "xyz"
            else "verloren (MODE=probe, Antasten neben dem Teil)"))
        gcmd.respond_info("\n".join(msg))

    def cmd_prepare(self, gcmd):
        if self.busy:
            raise gcmd.error("[k2_resume] läuft schon")
        if self._print_active():
            raise gcmd.error("[k2_resume] Druck läuft/pausiert – erst beenden "
                             "(pausiert: RESUME statt Wiedereinstieg)")
        name, path = self._file(gcmd)
        raw = str(gcmd.get("LAYER", "")).strip().lower()
        pos = None
        zwant = gcmd.get_float("Z", None)
        if raw not in ("", "auto") and not raw.isdigit():
            raise gcmd.error("[k2_resume] LAYER=<n>|auto")
        if raw in ("", "auto"):
            sd = self._status("virtual_sdcard")
            if sd.get("file_path") and os.path.basename(sd["file_path"]) == name \
                    and sd.get("file_position"):
                pos = sd["file_position"]
            if pos is None and zwant is None:
                raise gcmd.error("[k2_resume] LAYER=<n> oder Z=<mm> angeben "
                                 "(Abbruchstelle unbekannt)")
        homed = self._status("toolhead").get("homed_axes", "")
        mode = str(gcmd.get("MODE", "auto")).lower()
        if mode == "auto":
            mode = "keep" if homed == "xyz" else "probe"
        if mode not in ("keep", "probe"):
            raise gcmd.error("[k2_resume] MODE=auto|keep|probe")
        if mode == "keep" and homed != "xyz":
            raise gcmd.error("[k2_resume] MODE=keep geht nur mit erhaltener "
                             "Z-Referenz (homed xyz) – MODE=probe nehmen")
        tilt = str(gcmd.get("TILT", "auto")).lower()
        if tilt not in ("auto", "1", "0"):
            raise gcmd.error("[k2_resume] TILT=auto|1|0")
        self.busy = True
        try:
            if raw in ("", "auto"):
                r = self._run_child("info", {"path": path, "pos": pos})
                layer = r.get("layer_at_pos") if pos is not None else None
                if layer is None and zwant is not None:
                    layer = layer_for_z(r.get("zs") or [], zwant)
                if layer is None:
                    raise ResumeError("Abbruchschicht nicht bestimmbar – "
                                      "LAYER=<n> angeben")
            else:
                layer = int(raw)
            out = out_name(name, layer)
            params = dict(self._cfg_kw())
            params.update({"path": path,
                           "out": os.path.join(self._sd_dir(), out),
                           "layer": layer, "mode": mode, "tilt": tilt,
                           "prime": self.prime})
            plan = self._run_child("build", params)
        except ResumeError as exc:
            raise gcmd.error("[k2_resume] %s" % exc)
        finally:
            self.busy = False
        self.last = {"file": out, "layer": plan["layer"], "z": plan["z"],
                     "mode": plan["mode"], "safe_z": plan["safe_z"],
                     "probe_xy": plan.get("probe_xy"),
                     "tilt": plan.get("tilt"), "warnings": plan["warnings"]}
        lines = ["[k2_resume] Datei %s erzeugt: ab Schicht %d von %d (Z %s), "
                 "Modus %s, sichere Fahrhöhe Z %s" % (
                     out, plan["layer"], plan["total"], _fmt(plan["z"]),
                     plan["mode"], _fmt(plan["safe_z"]))]
        if plan.get("probe_xy"):
            lines.append("Z-Antasten neben dem Teil bei X%s Y%s, Z_TILT_ADJUST: "
                         "%s (%s)" % (_fmt(plan["probe_xy"][0]),
                                      _fmt(plan["probe_xy"][1]),
                                      "ja" if plan.get("tilt") else "nein",
                                      plan.get("tilt_reason", "")))
        for w in plan["warnings"]:
            lines.append("WARNUNG: " + w)
        lines.append("Teil NICHT vom Bett nehmen. Start: %s"
                     % ("jetzt" if gcmd.get_int("START", 0) else
                        "Datei in Mainsail drucken"))
        gcmd.respond_info("\n".join(lines))
        if gcmd.get_int("START", 0):
            self.gcode.run_script_from_command(
                "SDCARD_PRINT_FILE FILENAME=\"%s\"" % (out,))

    def cmd_begin(self, gcmd):
        mode = str(gcmd.get("MODE", "")).lower()
        homed = self._status("toolhead").get("homed_axes", "")
        if mode == "keep" and homed != "xyz":
            raise gcmd.error(
                "[k2_resume] Wiedereinstieg abgebrochen VOR jeder Bewegung: "
                "Z-Referenz verloren (Neustart, Not-Halt oder Motoren aus). "
                "Datei mit KTWO_RESUME_PREPARE ... MODE=probe neu erzeugen.")
        if mode == "probe":
            fr = self._status("gcode_macro _PI_FREIGABE")
            if fr and not fr.get("allow_move"):
                raise gcmd.error("[k2_resume] Wiedereinstieg abgebrochen VOR "
                                 "jeder Bewegung: erst ALLOW_MOTION")
            safe = gcmd.get_float("SAFE_Z", None)
            th = self._status("toolhead")
            # Z noch referenziert (MODE=probe von Hand gewählt): vor G28 X Y
            # erst Z auf sichere Höhe, sonst fährt X/Y durchs Teil
            if safe is not None and "z" in homed and \
                    float((th.get("position") or [0, 0, 0])[2]) < safe:
                self.gcode.run_script_from_command("G90\nG1 Z%.3f F600" % safe)
        kb = self._status("k2_box")
        if kb.get("chain_error"):
            raise gcmd.error("[k2_resume] CFS-Wechselfehler offen (%s) – erst "
                             "KTWO_BOX_ERROR_CLEAR" % kb["chain_error"])
        gcmd.respond_info("[k2_resume] Wiedereinstieg ab Schicht %s, Modus %s, "
                          "sichere Fahrhöhe Z %s" % (
                              gcmd.get("LAYER", "?"), mode,
                              gcmd.get("SAFE_Z", "?")))

    def cmd_zhome(self, gcmd):
        x = gcmd.get_float("X")
        y = gcmd.get_float("Y")
        homed = self._status("toolhead").get("homed_axes", "")
        if "x" not in homed or "y" not in homed:
            raise gcmd.error("[k2_resume] ZHOME braucht X/Y referenziert")
        self.z_home_at = [x, y]
        try:
            self.gcode.run_script_from_command("G28 Z")
        finally:
            self.z_home_at = None

    def cmd_zfix(self, gcmd):
        x = gcmd.get_float("X")
        y = gcmd.get_float("Y")
        bm = self.printer.lookup_object("bed_mesh", None)
        mesh = bm.get_mesh() if bm is not None else None
        if mesh is None:
            gcmd.respond_info("[k2_resume] kein Netz geladen – Z-Referenz bleibt "
                              "am Antastpunkt (kein Ausgleich)")
            return
        cfg = self._cfg_kw()
        cx, cy = cfg.get("home_center", [175.0, 175.0])
        try:
            adj = mesh.calc_z(cx, cy) - mesh.calc_z(x, y)
        except Exception as exc:
            raise gcmd.error("[k2_resume] Netzwert nicht berechenbar: %s" % exc)
        if abs(adj) > 1.0:
            raise gcmd.error("[k2_resume] Netz-Unterschied Mitte/Antastpunkt "
                             "%.3f mm unplausibel – abgebrochen" % adj)
        self.gcode.run_script_from_command(
            "SET_GCODE_OFFSET Z_ADJUST=%.4f MOVE=0" % adj)
        gcmd.respond_info("[k2_resume] Z-Referenz von X%.1f Y%.1f auf die "
                          "Bettmitte umgerechnet: %+.3f mm" % (x, y, adj))

    def get_status(self, eventtime):
        return {"z_home_at": self.z_home_at, "busy": self.busy,
                "last": self.last}


def load_config(config):
    return K2Resume(config)


if __name__ == "__main__":
    sys.exit(_cli(sys.argv))
