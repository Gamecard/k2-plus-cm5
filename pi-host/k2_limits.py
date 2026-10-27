#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_limits.py – Betriebsgrenzen wie Stock (L3, 18.10.2026).

Upstream-Klipper-Zusatzmodul ``[k2_limits]`` (printer.cfg). Ersetzt die
Creality-Befehle ``SET_LIMITS``/``RESTORE_LIMITS``/``SET_Z_LIMIT`` und die
Z-Klemmung in ``cmd_G1`` durch Upstream-verträgliche Mittel (Abgleich mit
Creality-klippy, Lücke L3).

Stock (Creality-klippy 1.1.7.0, ``/usr/share/klipper/klippy`` auf dem Drucker):

- Betriebsfenster nach dem Homing = ``rail.get_range()``
  (CR:kinematics/corexy.py:88-92):
  * X min = ``[box] cut_pos_x`` (CR:stepper.py:332-333 + 368-377), Pi:
    ``[k2_box] cut_pos_x`` (Default -7,80 = SAVE_CONFIG 16.10.2026),
  * Y max = ``[gcode_macro PRINTER_PARAM] variable_max_y_position`` = 352.0
    (CR:stepper.py:326-328; ST gcode_macro.cfg:8),
  * X max 352,5 / Y min -6,2 wie Config.
- ``SET_LIMITS`` (CR:extras/gcode_move.py:96-101 -> corexy.py:71-77) weitet
  X/Y auf die ROH-Config ``[stepper_x] position_min`` -10 / ``[stepper_y]
  position_max`` 400 (Spülschacht Y 374–378, Schneid-Kalibrierung bis X
  -9,5). Stock setzt das OHNE Homing-Prüfung (ungehomte Achse gilt danach
  als gehomt – NICHT nachgebaut: hier nur mit gehomtem X UND Y).
- ``RESTORE_LIMITS`` (gcode_move.py:103-104 -> corexy.py:78-87) setzt nur
  gehomte X/Y wieder auf ``rail.get_range()``.
- ``SET_Z_LIMIT`` nach jedem ``G28 Z`` (CR:extras/homing.py:425/442/452,
  Formel gcode_move.py:72-90): Z max = 360 − ``zmax_safe_pox_diff`` 10
  (ST printer.cfg:399) + |fade_target| + 0,2. ``fade_target`` ist dort
  immer 0: Stock führt VOR dem Z-Homing ``BED_MESH_CLEAR`` aus
  (CR:extras/homing.py:407) und das setzt ``fade_target = 0``
  (CR:extras/bed_mesh.py:199) -> **Z max 350,2**. Upstream setzt beim
  ``G28 Z`` die Grenze aus ``[stepper_z] position_max`` – printer.cfg
  trägt deshalb 350.2 (gleiche Wirkung, nur nach dem Z-Homing gültig).
- ``cmd_G1`` (CR:extras/gcode_move.py:301-308): außerhalb des Drucks
  (``print_stats.state != "printing"``) und mit gehomtem Z wird ein Ziel
  Z < -2 auf -2 GEKLEMMT.

Pi (dieses Modul):

1. **Enge Grenzen** stehen in printer.cfg (``[stepper_x] position_min:
   -7.8``, ``[stepper_y] position_max: 352``, ``[stepper_z] position_max:
   350.2``); Upstream setzt sie beim Homing selbst. ``[k2_limits]`` prüft
   sie beim Laden (harte Obergrenzen Y 352 / Z 350,2) und zieht X min beim
   Connect auf ``[k2_box] cut_pos_x`` nach (Stock-Semantik get_range).
2. **Aufweiten** ``KTWO_LIMITS_WIDE`` bzw. Python ``widen(owner)``: setzt
   ``rail.position_min/max`` (X auf ``x_min_wide`` -10, Y auf ``y_max_wide``
   400) und für gehomte Achsen ``kin.limits`` = ``rail.get_range()``.
   NUR mit gehomtem X und Y (sonst Fehler, nichts geändert).
   Halter-Prinzip: Python-Aufrufer (k2_box) melden sich mit eigenem Namen
   an/ab; eng wird es, wenn kein Halter mehr da ist.
3. **Rücksetzen** ``KTWO_LIMITS_RESTORE`` = Stock-RESTORE_LIMITS
   (unbedingt, alle Halter weg): Rails eng, gehomte Achsen eng. Automatisch
   zurückgesetzt wird außerdem bei JEDEM G-Code-Fehler (Upstream-Event
   ``gcode:command_error``, feuert auch für verschachtelte Makro-Fehler),
   bei ``stepper_enable:motor_off`` und beim Shutdown. Python-Aufrufer
   geben ihren Halter in ``finally`` zurück.
4. **Z-Untergrenze außerhalb des Drucks**: G-Code-Bewegungs-Transform
   (Upstream-Muster wie exclude_object: ``set_move_transform(force=True)``
   beim Connect, Kette auf den vorigen Transform, z. B. bed_mesh). Ein Ziel
   Z < ``z_min_outside_print`` (-2) wird bei gehomtem Z und NICHT
   laufendem Druck ABGELEHNT (G-Code-Fehler, keine Bewegung), wenn es
   tiefer als die aktuelle Höhe führt. Begründung s. README-k2_limits.md:
   Ablehnen statt Klemmen (kein stilles Fahren auf -2, Makro bricht ab);
   Transform statt G1-Wrapper (deckt G0/G1/G2/G3 und RESTORE_GCODE_STATE
   MOVE=1 ab, kein Umbenennen von G1, keine Kollision mit anderen
   G1-Overrides); interne Fahrten (Probe, Homing, zalign) laufen wie bei
   Stock nicht über gcode_move und bleiben unberührt.
5. **Z-Untergrenze im Druck** (``print_low_z_guard``, Default True; kein
   Stock-Vorbild, Pi-Schutz 18.10.2026): Orcas K2-Vorlage
   ``change_filament_gcode`` endet mit ``G1 Z{z_after_toolchange}``; vor der
   ersten Schicht ist das 0 -> nach einem Werkzeugwechsel fährt die Düse
   auf Z0 und kratzt übers Bett. Im selben Transform: bei
   ``print_stats.state == "printing"`` und gehomtem Z wird ein Ziel mit
   G-Code-Z (vor SET_GCODE_OFFSET/G92, also ``newpos.z -
   gcode_move.base_position.z``) unter ``print_min_z`` (0,05) NICHT
   gefahren, sondern auf G-Code-Z ``print_low_z_raise`` (2,0) angehoben;
   eine Meldung je Druck (Schlüssel ``print_stats.print_start_time``). Die
   G-Code-Position bleibt beim angeforderten Wert (nachfolgende XY-/E-
   Fahrten ohne Z bleiben dadurch ebenfalls angehoben, bis eine Fahrt mit
   Z >= print_min_z kommt). Status ``paused``: kein Eingriff (alte -2-Regel)
   – AUSSER der Kopf steht beim Pausieren gerade durch diesen Schutz
   angehoben (``low_z_holding``): dann hebt auch die RESUME-Rückfahrt
   (``_PI_RESUME_RETURN`` G1 Z{pause_z}, RESTORE_GCODE_STATE MOVE=1) an,
   statt die Düse aufs Bett zu setzen. Ungehomtes Z: kein Eingriff
   (Upstream meldet bei Z-Fahrt selbst „Must home“; das Anheben würde eine
   XY-Fahrt vor G28 Z sonst zum Fehler machen).

Status ``printer.k2_limits``: wide, holders, x_min/x_max/y_min/y_max (eng),
x_min_wide/y_max_wide, z_max, z_min_outside_print, print_low_z_guard,
print_min_z, print_low_z_raise, low_z_raises, low_z_holding.

Konsole (23.10.2026, k2_konsole): Fehler beim Nachziehen der Status-
anzeige axis_minimum (Connect) als ``// [k2_limits] …`` kurz nach „ready“.

Ohne Netzwerk, ohne Hardware testbar (test_k2_limits.py).
"""

import logging
import math
import re

try:
    from . import k2_konsole as _K2K
except ImportError:
    import k2_konsole as _K2K

# --- Belegte Stock-Werte (s. Docstring) ---------------------------------
STOCK_X_MIN_WIDE = -10.0     # ST [stepper_x] position_min (Roh-Config)
STOCK_Y_MAX_WIDE = 400.0     # ST [stepper_y] position_max (Roh-Config)
STOCK_Y_MAX = 352.0          # PRINTER_PARAM variable_max_y_position
STOCK_Z_MAX_CONFIG = 360.0   # ST [stepper_z] position_max
STOCK_ZMAX_SAFE_DIFF = 10.0  # ST [z_align] zmax_safe_pox_diff
STOCK_Z_OFFSET = 0.2         # CR:gcode_move.py:87 offset_value
STOCK_Z_MAX = round(STOCK_Z_MAX_CONFIG - STOCK_ZMAX_SAFE_DIFF + 0.0
                    + STOCK_Z_OFFSET, 3)          # 350.2 (fade_target 0)
STOCK_Z_MIN_G1 = -2.0        # CR:gcode_move.py:305-308
PRINT_MIN_Z = 0.05           # Pi: G-Code-Z unter dieser Schwelle im Druck
PRINT_LOW_Z_RAISE = 2.0      # ... wird auf diese G-Code-Z angehoben
X_MIN_NARROW_MAX = 0.0       # Schneidhebel liegt bei negativem X
EPS = 1e-6

GCODE_OWNER = "gcode"        # Halter von KTWO_LIMITS_WIDE


class LimitsError(Exception):
    """Aufweiten nicht erlaubt (Achsen nicht gehomt) – nichts geändert."""


# =====================================================================
# Reine Logik (ohne Klipper testbar)
# =====================================================================

def _homed(limit):
    return limit[0] <= limit[1]


class LimitsCore:
    """Verwaltet Rails + ``kin.limits`` für X (0) und Y (1).

    ``kin`` braucht ``limits`` (Liste von (min, max), Upstream corexy) und
    ``rails`` (Objekte mit ``position_min``/``position_max``/``get_range``,
    Upstream GenericPrinterRail)."""

    def __init__(self, kin, narrow, wide):
        self.kin = kin
        self.narrow = {0: tuple(narrow[0]), 1: tuple(narrow[1])}
        self.wide = {0: tuple(wide[0]), 1: tuple(wide[1])}
        self.holders = {}            # owner -> Grund
        self.log = []                # Diagnose (letzte Aktionen)
        for axis in (0, 1):
            rail = kin.rails[axis]
            if not (hasattr(rail, "position_min") and hasattr(rail, "position_max")
                    and hasattr(rail, "get_range")):
                raise LimitsError(
                    "Rail %s ohne position_min/position_max/get_range – "
                    "Upstream-Version passt nicht" % "xy"[axis])
            lo, hi = self.wide[axis]
            nlo, nhi = self.narrow[axis]
            if not (lo <= nlo + EPS and nhi <= hi + EPS and nlo < nhi):
                raise LimitsError("Fenster %s: eng %s nicht in weit %s"
                                  % ("xy"[axis], self.narrow[axis],
                                     self.wide[axis]))

    # -- Zustand -----------------------------------------------------
    def is_wide(self):
        return bool(self.holders)

    def axes_homed(self):
        return [_homed(self.kin.limits[a]) for a in (0, 1)]

    def _apply(self, wide):
        win = self.wide if wide else self.narrow
        for axis in (0, 1):
            lo, hi = win[axis]
            rail = self.kin.rails[axis]
            rail.position_min = lo
            rail.position_max = hi
            # Stock restore_limits: nur gehomte Achsen (ungehomte bleiben
            # ungehomt – kein Stock-SET_LIMITS-Nebeneffekt)
            if _homed(self.kin.limits[axis]):
                self.kin.limits[axis] = tuple(rail.get_range())

    def widen(self, owner, reason=""):
        """Weiten (X min -> x_min_wide, Y max -> y_max_wide). LimitsError,
        wenn X oder Y nicht gehomt ist (nichts geändert)."""
        hx, hy = self.axes_homed()
        if not (hx and hy):
            raise LimitsError(
                "Aufweiten der Fahrgrenzen verweigert: %s nicht referenziert "
                "(G28 X Y zuerst) – nichts geändert"
                % "/".join(a for a, h in zip("XY", (hx, hy)) if not h))
        self.holders[owner] = reason or owner
        self._apply(True)
        self.log.append(("wide", owner, reason))

    def release(self, owner):
        """Halter zurückgeben; ohne Halter wieder eng."""
        self.holders.pop(owner, None)
        if not self.holders:
            self._apply(False)
        self.log.append(("release", owner, ""))

    def restore_all(self, why=""):
        """Stock-RESTORE_LIMITS: unbedingt eng, alle Halter weg."""
        had = bool(self.holders)
        self.holders.clear()
        self._apply(False)
        self.log.append(("restore", why, ""))
        return had

    def set_narrow_x_min(self, x_min):
        """X min des engen Fensters (cut_pos_x) – nur im engen Zustand."""
        lo, hi = self.narrow[0]
        if not self.wide[0][0] - EPS <= x_min < hi:
            raise LimitsError("X min %.3f außerhalb [%.3f, %.3f)"
                              % (x_min, self.wide[0][0], hi))
        self.narrow[0] = (float(x_min), hi)
        if not self.holders:
            self._apply(False)

    def outside_narrow(self, x, y):
        (xl, xh), (yl, yh) = self.narrow[0], self.narrow[1]
        return (x is not None and (x < xl - EPS or x > xh + EPS)) or \
            (y is not None and (y < yl - EPS or y > yh + EPS))


# --- G-Code-Zeilen auf XY-Ziele prüfen (für k2_box) --------------------
_WORD = re.compile(r"([A-Z])\s*([-+]?(?:\d+\.?\d*|\.\d+))")


def xy_points(lines, start, absolute=True):
    """XY-Punkte, die ``lines`` anfährt (G-Code-Koordinaten).

    ``start`` = (x, y) aktuelle G-Code-Position (None = unbekannt ->
    relative Fahrten nicht auswertbar, ausgelassen). G90/G91 werden
    verfolgt; G0/G1: Zielpunkt; G2/G3: Zielpunkt + Hüllpunkte des Kreises
    (Mittelpunkt ± Radius, konservativ). Andere Befehle (M400, G4,
    SET_*, SAVE/RESTORE_GCODE_STATE ohne MOVE) bewegen nicht."""
    x, y = (None, None) if start is None else (float(start[0]), float(start[1]))
    out = []
    for raw in lines:
        line = str(raw).split(";", 1)[0].strip().upper()
        if not line:
            continue
        head = line.split()[0]
        if head == "G90":
            absolute = True
            continue
        if head == "G91":
            absolute = False
            continue
        if head not in ("G0", "G1", "G2", "G3"):
            continue
        words = dict(_WORD.findall(line[len(head):]))
        nx, ny = x, y
        if "X" in words:
            v = float(words["X"])
            nx = v if absolute else (None if x is None else x + v)
        if "Y" in words:
            v = float(words["Y"])
            ny = v if absolute else (None if y is None else y + v)
        if head in ("G2", "G3") and x is not None and y is not None:
            cx = x + float(words.get("I", 0.0))
            cy = y + float(words.get("J", 0.0))
            r = math.hypot(x - cx, y - cy)
            out += [(cx - r, cy), (cx + r, cy), (cx, cy - r), (cx, cy + r)]
        if "X" in words or "Y" in words:
            out.append((nx, ny))
        x, y = nx, ny
    return out


# =====================================================================
# Klipper-Anbindung
# =====================================================================

class ZGuardTransform:
    """G-Code-Bewegungs-Transform: Z < z_min außerhalb des Drucks ablehnen;
    im Druck G-Code-Z < print_min_z auf print_low_z_raise anheben."""

    def __init__(self, owner, next_transform):
        self.owner = owner
        self.next_transform = next_transform

    def get_position(self):
        # Physische Lage (nach Anheben) – gcode_move übernimmt sie nur bei
        # reset_last_position; die angeforderte G-Code-Z bleibt sonst stehen.
        return self.next_transform.get_position()

    def move(self, newpos, speed):
        o = self.owner
        raised = o.low_z_raise(newpos)
        if raised is not None:
            self.next_transform.move(raised, speed)
            return
        z = newpos[2]
        if o.z_guard and z < o.z_min_outside_print - EPS and o.z_guard_active():
            cur = self.next_transform.get_position()
            if z < cur[2] - EPS:
                o.z_rejects += 1
                try:
                    o.gcode_move.reset_last_position()
                except Exception:
                    pass
                raise o.printer.command_error(
                    "[k2_limits] Z%.3f abgelehnt: außerhalb des Drucks nie "
                    "unter Z%.1f (Stock gcode_move G1, Düse ins Bett) – "
                    "keine Bewegung" % (z, o.z_min_outside_print))
        self.next_transform.move(newpos, speed)


class PrinterK2Limits:
    """[k2_limits] – Betriebsgrenzen wie Stock (s. Modul-Docstring)."""

    def __init__(self, config):
        self.printer = printer = config.get_printer()
        self.gcode = printer.lookup_object("gcode")
        self.konsole = _K2K.Konsole(printer, "k2_limits", self.gcode)
        self.x_min_wide = config.getfloat("x_min_wide", STOCK_X_MIN_WIDE,
                                          minval=STOCK_X_MIN_WIDE,
                                          maxval=X_MIN_NARROW_MAX)
        self.y_max_wide = config.getfloat("y_max_wide", STOCK_Y_MAX_WIDE,
                                          minval=STOCK_Y_MAX,
                                          maxval=STOCK_Y_MAX_WIDE)
        self.x_min_from_cut_pos = config.getboolean("x_min_from_cut_pos", True)
        self.z_guard = config.getboolean("z_guard", True)
        self.z_min_outside_print = config.getfloat(
            "z_min_outside_print", STOCK_Z_MIN_G1, minval=STOCK_Z_MIN_G1,
            maxval=0.0)
        self.print_low_z_guard = config.getboolean("print_low_z_guard", True)
        self.print_min_z = config.getfloat("print_min_z", PRINT_MIN_Z,
                                           minval=0.0, maxval=1.0)
        self.print_low_z_raise = config.getfloat(
            "print_low_z_raise", PRINT_LOW_Z_RAISE, minval=0.2, maxval=20.0)
        if self.print_low_z_raise < self.print_min_z + 0.1 - EPS:
            raise config.error(
                "[k2_limits] print_low_z_raise %.3f muss mindestens 0,1 mm "
                "über print_min_z %.3f liegen"
                % (self.print_low_z_raise, self.print_min_z))
        # Enge Config-Werte prüfen (harte Obergrenzen = Stock-Fenster)
        sx = config.getsection("stepper_x")
        sy = config.getsection("stepper_y")
        sz = config.getsection("stepper_z")
        self.cfg_x = (sx.getfloat("position_min", 0.), sx.getfloat("position_max"))
        self.cfg_y = (sy.getfloat("position_min", 0.), sy.getfloat("position_max"))
        self.cfg_z = (sz.getfloat("position_min", 0.), sz.getfloat("position_max"))
        if not self.x_min_wide - EPS <= self.cfg_x[0] <= X_MIN_NARROW_MAX + EPS:
            raise config.error(
                "[k2_limits] [stepper_x] position_min %.3f muss zwischen "
                "x_min_wide %.3f und 0 liegen (Stock: cut_pos_x)"
                % (self.cfg_x[0], self.x_min_wide))
        if self.cfg_y[1] > STOCK_Y_MAX + EPS:
            raise config.error(
                "[k2_limits] [stepper_y] position_max %.3f > %.1f (Stock "
                "PRINTER_PARAM max_y_position) – Betriebsfenster zu weit; "
                "Schacht/Spülen laufen über KTWO_LIMITS_WIDE"
                % (self.cfg_y[1], STOCK_Y_MAX))
        if self.cfg_z[1] > STOCK_Z_MAX + EPS:
            raise config.error(
                "[k2_limits] [stepper_z] position_max %.3f > %.1f (Stock "
                "SET_Z_LIMIT nach G28 Z: 360-10+0+0,2) – Bett führe in den "
                "unteren Anschlag" % (self.cfg_z[1], STOCK_Z_MAX))
        self.core = None
        self.gcode_move = None
        self.transform = None
        self.z_rejects = 0
        self.low_z_raises = 0
        self.low_z_holding = False   # Kopf steht gerade durch den Schutz höher
        self._low_z_key = None       # Druck, für den gemeldet wurde
        self._low_z_reported = False
        self.auto_restores = 0
        self.x_min_source = "printer.cfg"
        printer.register_event_handler("klippy:connect", self._handle_connect)
        printer.register_event_handler("gcode:command_error", self._on_error)
        printer.register_event_handler("stepper_enable:motor_off", self._on_motor_off)
        printer.register_event_handler("klippy:shutdown", self._on_shutdown)
        for name, fn, desc in (
                ("KTWO_LIMITS_WIDE", self.cmd_wide,
                 "Fahrgrenzen für Schacht/Spülen/Schneiden aufweiten (nur X/Y gehomt) [REASON=]"),
                ("KTWO_LIMITS_RESTORE", self.cmd_restore,
                 "Fahrgrenzen auf das Betriebsfenster zurücksetzen (Stock RESTORE_LIMITS)"),
                ("KTWO_LIMITS_STATUS", self.cmd_status,
                 "Fahrgrenzen anzeigen")):
            self.gcode.register_command(name, fn, desc=desc)

    # -- Lebenszyklus --------------------------------------------------
    def _handle_connect(self):
        toolhead = self.printer.lookup_object("toolhead")
        kin = toolhead.get_kinematics()
        if not hasattr(kin, "limits") or not hasattr(kin, "rails"):
            raise self.printer.config_error(
                "[k2_limits] Kinematik ohne limits/rails (nur corexy/"
                "cartesian Upstream 9cb9975 geprüft)")
        x_lo = self.cfg_x[0]
        if self.x_min_from_cut_pos:
            box = self.printer.lookup_object("k2_box", None)
            cut = None
            try:
                cut = (getattr(box, "cfg", None) or {}).get("cut_pos_x")
            except Exception:
                cut = None
            if cut is not None:
                cut = float(cut)
                if not self.x_min_wide - EPS <= cut <= X_MIN_NARROW_MAX + EPS:
                    raise self.printer.config_error(
                        "[k2_limits] [k2_box] cut_pos_x %.3f außerhalb "
                        "x_min_wide %.3f..0" % (cut, self.x_min_wide))
                x_lo = cut
                self.x_min_source = "[k2_box] cut_pos_x"
        try:
            self.core = LimitsCore(
                kin, narrow={0: (x_lo, self.cfg_x[1]), 1: self.cfg_y},
                wide={0: (self.x_min_wide, self.cfg_x[1]),
                      1: (self.cfg_y[0], self.y_max_wide)})
        except LimitsError as exc:
            raise self.printer.config_error("[k2_limits] %s" % exc)
        self.core.restore_all("connect")
        # Statusanzeige axis_minimum.x nachziehen (Upstream rechnet sie
        # einmal aus der Config)
        try:
            amin = list(kin.axes_min)
            if abs(amin[0] - x_lo) > EPS:
                amin[0] = x_lo
                kin.axes_min = type(kin.axes_min)(amin)
        except Exception as exc:
            logging.exception("k2_limits: axes_min nicht nachgezogen")
            self.konsole.warnung(
                "Statusanzeige axis_minimum.x nicht auf %.3f nachgezogen (%s: "
                "%s) – Fahrgrenzen selbst gelten trotzdem"
                % (x_lo, type(exc).__name__, exc), log=False)
        if x_lo != self.cfg_x[0]:
            logging.info("k2_limits: X min %.3f aus %s (printer.cfg %.3f)",
                         x_lo, self.x_min_source, self.cfg_x[0])
        self.gcode_move = self.printer.lookup_object("gcode_move")
        if self.z_guard or self.print_low_z_guard:
            self.transform = ZGuardTransform(self, None)
            self.transform.next_transform = self.gcode_move.set_move_transform(
                self.transform, force=True)

    def z_guard_active(self):
        """Druck läuft nicht und Z ist gehomt."""
        ps = self.printer.lookup_object("print_stats", None)
        if ps is not None and getattr(ps, "state", None) == "printing":
            return False
        kin = self.printer.lookup_object("toolhead").get_kinematics()
        return _homed(kin.limits[2])

    def _print_state(self):
        ps = self.printer.lookup_object("print_stats", None)
        if ps is None:
            return None, None
        return getattr(ps, "state", None), getattr(ps, "print_start_time", None)

    def _z_homed(self):
        kin = self.printer.lookup_object("toolhead").get_kinematics()
        return _homed(kin.limits[2])

    def _base_z(self):
        """G-Code->Toolhead-Versatz Z (SET_GCODE_OFFSET + G92), Upstream
        ``gcode_move.base_position``."""
        gm = self.gcode_move
        try:
            return float(gm.base_position[2])
        except Exception:
            return 0.0

    def low_z_raise(self, newpos):
        """Druck-Schutz: angehobene Kopie von ``newpos`` oder None (kein
        Eingriff). ``newpos`` (gcode_move.last_position) bleibt unverändert."""
        if not self.print_low_z_guard:
            return None
        state, start = self._print_state()
        if state not in ("printing", "paused"):
            self.low_z_holding = False
            return None
        key = start if start is not None else "-"
        if key != self._low_z_key:
            self._low_z_key = key
            self._low_z_reported = False
            self.low_z_holding = False
        base = self._base_z()
        gz = newpos[2] - base
        if gz >= self.print_min_z - EPS:
            if state == "printing":
                self.low_z_holding = False
            return None
        if state == "paused" and not self.low_z_holding:
            return None          # Pause: alte Regel (s. Docstring)
        if not self._z_homed():
            return None
        raised = list(newpos)
        raised[2] = base + self.print_low_z_raise
        self.low_z_raises += 1
        self.low_z_holding = True
        if not self._low_z_reported:
            self._low_z_reported = True
            msg = ("k2_limits: G1 Z%.3f im Druck → auf Z%.2f angehoben (Orca "
                   "z_after_toolchange=0?) – Schwelle Z%.2f (G-Code-Ebene), "
                   "weitere Fälle in diesem Druck ohne Meldung"
                   % (gz, self.print_low_z_raise, self.print_min_z))
            try:
                self.gcode.respond_info(msg)     # landet auch im klippy.log
            except Exception:
                logging.info(msg)
        return raised

    def _auto_restore(self, why):
        if self.core is not None and self.core.is_wide():
            self.core.restore_all(why)
            self.auto_restores += 1
            logging.info("k2_limits: Fahrgrenzen zurückgesetzt (%s)", why)
            return True
        return False

    def _on_error(self, *args):
        if self._auto_restore("G-Code-Fehler"):
            try:
                self.gcode.respond_info(
                    "[k2_limits] Fahrgrenzen nach Fehler zurückgesetzt "
                    "(Betriebsfenster)")
            except Exception:
                pass

    def _on_motor_off(self, *args):
        self._auto_restore("Motoren aus")

    def _on_shutdown(self, *args):
        self._auto_restore("Shutdown")

    # -- Python-API (k2_box) ---------------------------------------------
    def available(self):
        return self.core is not None

    def widen(self, owner, reason=""):
        if self.core is None:
            raise LimitsError("[k2_limits] noch nicht verbunden")
        self.core.widen(owner, reason)

    def release(self, owner):
        if self.core is not None:
            self.core.release(owner)

    def _gcode_xy(self):
        """(G-Code-XY, Versatz G-Code->Toolhead, absolut?)"""
        gm = self.gcode_move or self.printer.lookup_object("gcode_move")
        st = gm.get_status(self.printer.get_reactor().monotonic())
        gp = st.get("gcode_position")
        pos = st.get("position")
        absolute = bool(st.get("absolute_coordinates", True))
        if gp is None:
            return None, (0.0, 0.0), absolute
        if pos is None:
            off = (0.0, 0.0)
        else:
            off = (float(pos[0]) - float(gp[0]), float(pos[1]) - float(gp[1]))
        return (float(gp[0]), float(gp[1])), off, absolute

    def needs_wide(self, lines):
        """True, wenn ``lines`` einen XY-Punkt außerhalb des engen
        Betriebsfensters anfährt (Toolhead-Koordinaten)."""
        if self.core is None:
            return False
        start, off, absolute = self._gcode_xy()
        for x, y in xy_points(lines, start, absolute):
            tx = None if x is None else x + off[0]
            ty = None if y is None else y + off[1]
            if self.core.outside_narrow(tx, ty):
                return True
        return False

    # -- G-Code ---------------------------------------------------------
    def cmd_wide(self, gcmd):
        if self.core is None:
            raise gcmd.error("[k2_limits] noch nicht verbunden")
        reason = gcmd.get("REASON", "G-Code")
        try:
            self.core.widen(GCODE_OWNER, reason)
        except LimitsError as exc:
            raise gcmd.error(str(exc))
        (xl, _xh), (_yl, yh) = self.core.wide[0], self.core.wide[1]
        gcmd.respond_info("[k2_limits] Fahrgrenzen aufgeweitet (%s): X ab "
                          "%.2f, Y bis %.1f – KTWO_LIMITS_RESTORE danach"
                          % (reason, xl, yh))

    def cmd_restore(self, gcmd):
        if self.core is None:
            return
        self.core.restore_all("KTWO_LIMITS_RESTORE")
        (xl, xh), (yl, yh) = self.core.narrow[0], self.core.narrow[1]
        gcmd.respond_info("[k2_limits] Betriebsfenster: X %.2f..%.2f, Y "
                          "%.2f..%.2f" % (xl, xh, yl, yh))

    def cmd_status(self, gcmd):
        st = self.get_status()
        gcmd.respond_info(
            "[k2_limits] %s; eng X %.2f..%.2f (X min aus %s) Y %.2f..%.2f, "
            "weit X ab %.2f / Y bis %.1f, Z max %.2f, Z außerhalb Druck >= "
            "%.1f (%s), im Druck G-Code-Z < %.2f -> %.2f (%s, angehoben %d), "
            "Halter %s, Ablehnungen Z %d, Auto-Rücksetzen %d"
            % ("AUFGEWEITET" if st["wide"] else "eng", st["x_min"],
               st["x_max"], self.x_min_source, st["y_min"], st["y_max"],
               st["x_min_wide"], st["y_max_wide"], st["z_max"],
               st["z_min_outside_print"],
               "aktiv" if (self.transform is not None and self.z_guard)
               else "aus", st["print_min_z"], st["print_low_z_raise"],
               "aktiv" if (self.transform is not None
                           and self.print_low_z_guard) else "aus",
               self.low_z_raises,
               ",".join(st["holders"]) or "-", self.z_rejects,
               self.auto_restores))

    def get_status(self, eventtime=None):
        if self.core is not None:
            (xl, xh), (yl, yh) = self.core.narrow[0], self.core.narrow[1]
            holders = sorted(self.core.holders)
            wide = self.core.is_wide()
        else:
            (xl, xh), (yl, yh) = self.cfg_x, self.cfg_y
            holders, wide = [], False
        return {"wide": wide, "holders": holders,
                "x_min": xl, "x_max": xh, "y_min": yl, "y_max": yh,
                "x_min_wide": self.x_min_wide, "y_max_wide": self.y_max_wide,
                "z_max": self.cfg_z[1],
                "z_min_outside_print": self.z_min_outside_print,
                "z_rejects": self.z_rejects,
                "print_low_z_guard": self.print_low_z_guard,
                "print_min_z": self.print_min_z,
                "print_low_z_raise": self.print_low_z_raise,
                "low_z_raises": self.low_z_raises,
                "low_z_holding": self.low_z_holding}


def load_config(config):
    return PrinterK2Limits(config)
