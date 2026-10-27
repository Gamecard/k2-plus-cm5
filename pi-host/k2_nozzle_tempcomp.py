#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_nozzle_tempcomp.py – Düsen-Temperaturkompensation wie Stock (L7, 18.10.2026).

Upstream-Klipper-Zusatzmodul ``[k2_nozzle_tempcomp]`` (config-pi/k2-module.cfg).
Ersetzt die Creality-``prtouch_v3``-Funktion ``get_z_now_comp`` samt
``SET_TEMP_NOT_LINEAR``. Belege: Analyse der Stock-Funktion (Formel/Ablauf,
Nachrechnung 17/17 Log-Tupel).

Stock (``prtouch_v3_wrapper.so``, Rekonstruktion aus der Analyse):

- ΔT = Düsen-**Soll** (``extruder.heater.target_temp``) − Referenz
  ``g28_temp`` = geglättete Düsen-**Ist**temperatur (``smoothed_temp``) beim
  letzten Z-Antasten (Ende G28 Z) bzw. bei ``BED_MESH_CALIBRATE``.
- comp = 0 für ΔT ≤ 0, sonst ``min(0.181, a·ΔT² + b·ΔT + c)`` mit
  a = 5.1e-06, b = 0, c = 0.017316 (mm); danach auf [0; 0,2] begrenzt.
- comp wird als konstanter Z-Zuschlag auf jeden Netzwert addiert (Düse
  fährt höher), nur bei geladenem Netz, nur im Zustand ``printing``/
  ``paused``, und blendet mit dem Netz-Fade aus (voll bis fade_start, 0 ab
  fade_end).
- Neu gerechnet wird nur bei Änderung der Solltemperatur (``lst_temp``);
  G28 Z setzt ``lst_temp = 0`` (erzwingt Neurechnung), BED_MESH_CALIBRATE
  setzt nur die Referenz.
- Keine Speicherung (reine Laufzeitwerte).

Pi (dieses Modul):

1. G-Code-Bewegungs-Transform (Upstream-Muster ``z_thermal_adjust``):
   ``move()`` addiert ``comp · fade``, ``get_position()`` zieht es wieder
   ab. Einhängen beim ``klippy:connect`` DIREKT ÜBER ``bed_mesh`` (Kette
   wird abgelaufen; die Position anderer Transforms wie ``[k2_limits]``
   bleibt erhalten, unabhängig von der Reihenfolge der Abschnitte):
   ``gcode_move -> [k2_limits] -> k2_nozzle_tempcomp -> bed_mesh ->
   toolhead``. Ohne ``bed_mesh`` in der Kette: oben auf (wirkt dann nie,
   da Freigabe ein geladenes Netz verlangt).
2. Fade-Faktor = ``bed_mesh.get_z_factor(z)`` (Option ``fade_with_mesh``,
   Default True = Stock). False = konstanter Zuschlag in jeder Höhe.
3. Referenz: Event ``homing:home_rails_end`` mit Z (Upstream feuert es
   auch beim G28 Z über ``probe:z_virtual_endstop``) -> ref =
   ``smoothed_temp``, Neurechnung erzwungen; nach erfolgreichem
   ``BED_MESH_CALIBRATE`` (Befehl wird beim Connect umhüllt) -> nur ref.
4. Abweichungen vom Stock (bewusst, sicherer):
   - Ohne Referenz (seit dem Start kein G28 Z) gilt comp = 0 (Stock rechnet
     dann gegen g28_temp-Startwert).
   - ``KTWO_NOZZLE_TEMPCOMP`` ohne Parameter zeigt nur den Status (Stock
     ``SET_TEMP_NOT_LINEAR`` ohne Parameter = Werks-Reset); Werks-Reset
     ausdrücklich mit ``RESET=1``. Nicht angegebene A/B/C bleiben.
   - ``COMP_Z=`` wird ebenfalls auf [0; 0,2] begrenzt (Stock: ungeklemmt).
   - Lineare Variante (``enable_not_linear_comp: False``),
     ``SET_CUMULATIVE_HEATING_TIME`` und Kammerkompensation fehlen (live
     inaktiv).

Status ``printer.k2_nozzle_tempcomp``: enabled, active, reason, comp,
applied, target, ref_temperature, ref_source, a, b, c, max_comp,
fade_with_mesh, forced, recalcs.

Konsole (23.10.2026, k2_konsole): Connect-Warnungen (kein [print_stats] →
Kompensation nie aktiv; bed_mesh nicht in der Transform-Kette) erscheinen als
``// [k2_nozzle_tempcomp] …`` kurz nach „ready“.

Ohne Netzwerk, ohne Hardware testbar (test_k2_nozzle_tempcomp.py).
"""

import logging
import math

try:
    from . import k2_konsole as _K2K
except ImportError:
    import k2_konsole as _K2K

# --- Belegte Stock-Werte (Floats bytegenau) ----------
STOCK_A = 5.1e-06            # temp_non_linaer_a (Bytes ae5b4e531564d53e)
STOCK_B = 0.0                # temp_non_linaer_b
STOCK_C = 0.017316           # temp_non_linaer_c (Bytes e146ca1649bb913f)
STOCK_MAX_COMP = 0.181       # get_temp_compensate, Literal 0x1b3e8
CLAMP_MIN = 0.0              # get_z_now_comp: max(..., 0)  (fest)
CLAMP_MAX = 0.2              # get_z_now_comp: min(..., 0.2) (fest, 0x23678)
ACTIVE_STATES = ("printing", "paused")   # 0x2301c/0x23070
EPS = 1e-9


def clamp(value):
    """Stock-Endklemme [0; 0,2] (fest, nicht konfigurierbar)."""
    return max(min(float(value), CLAMP_MAX), CLAMP_MIN)


def stock_comp(target, ref, a=STOCK_A, b=STOCK_B, c=STOCK_C,
               max_comp=STOCK_MAX_COMP):
    """Stock-Formel get_temp_compensate + Klemme get_z_now_comp (mm)."""
    dt = float(target) - float(ref)
    if dt <= 0.0:
        return 0.0
    v = a * dt * dt + b * dt + c
    if not math.isfinite(v):
        return 0.0
    v = max(min(v, max_comp), 0.0)
    return clamp(v)


# =====================================================================
# Reine Logik (ohne Klipper testbar)
# =====================================================================

class CompCore:
    """Zustand wie Stock: ref (g28_temp), lst_target (lst_temp), now_comp."""

    def __init__(self, a=STOCK_A, b=STOCK_B, c=STOCK_C,
                 max_comp=STOCK_MAX_COMP, enabled=True, logger=None):
        self.a, self.b, self.c = float(a), float(b), float(c)
        self.max_comp = float(max_comp)
        self.enabled = bool(enabled)
        self.ref = None              # None = noch keine Referenz
        self.ref_source = ""
        self.lst_target = None       # None = Neurechnung fällig
        self.now_comp = 0.0
        self.forced = False
        self.recalcs = 0
        self.logger = logger or (lambda msg: logging.info(msg))

    # -- Referenz -------------------------------------------------------
    def set_reference(self, temp, source, reset_last=True):
        """G28 Z (reset_last=True: lst_temp = 0, now_comp = 0) bzw.
        BED_MESH_CALIBRATE (reset_last=False: nur g28_temp)."""
        self.ref = float(temp)
        self.ref_source = source
        if reset_last:
            self.lst_target = None
            self.now_comp = 0.0
            self.forced = False
        self.logger("k2_nozzle_tempcomp: Referenz %.2f °C (%s)"
                    % (self.ref, source))

    # -- Koeffizienten ---------------------------------------------------
    def set_coeffs(self, a=None, b=None, c=None):
        if a is not None:
            self.a = float(a)
        if b is not None:
            self.b = float(b)
        if c is not None:
            self.c = float(c)
        self.lst_target = None       # Stock: sofort neu rechnen
        self.forced = False

    def factory_reset(self):
        self.set_coeffs(STOCK_A, STOCK_B, STOCK_C)
        self.max_comp = STOCK_MAX_COMP

    def force(self, comp_z, target):
        """Stock COMP_Z: Wert bis zur nächsten Solländerung (hier geklemmt)."""
        self.now_comp = clamp(comp_z)
        self.lst_target = float(target)
        self.forced = True
        self.logger("k2_nozzle_tempcomp: now_comp:%.3f erzwungen (COMP_Z) "
                    "target_temp:%.2f" % (self.now_comp, float(target)))

    # -- Rechnung ---------------------------------------------------------
    def compute(self, target):
        if self.ref is None:
            return 0.0
        return stock_comp(target, self.ref, self.a, self.b, self.c,
                          self.max_comp)

    def current(self, target):
        """Wert bei freigegebener Kompensation (Stock get_z_now_comp ohne
        Zustandsprüfung). Neu gerechnet nur bei Solländerung."""
        target = float(target)
        if self.lst_target is None or target != self.lst_target:
            self.now_comp = self.compute(target)
            self.lst_target = target
            self.forced = False
            self.recalcs += 1
            # Stock-Logzeile (WHY_DEBUG), vergleichbar mit der Nachrechnung
            self.logger("k2_nozzle_tempcomp: get_z_now_comp now_comp:%.3f "
                        "target_temp:%.2f G28_temp:%s"
                        % (self.now_comp, target,
                           "-" if self.ref is None else "%.2f" % self.ref))
        return self.now_comp


def invert_fade(z_out, comp, factor_fn, iterations=50):
    """Löst z_out = z + comp·factor(z) nach z (Fixpunkt; |comp·f'| ≤
    0,2/Fade-Weg ≪ 1, konvergiert in wenigen Schritten)."""
    z = z_out - comp * factor_fn(z_out - comp)
    for _ in range(iterations):
        nz = z_out - comp * factor_fn(z)
        if abs(nz - z) < EPS:
            return nz
        z = nz
    return z


# =====================================================================
# Klipper-Anbindung
# =====================================================================

class PrinterK2NozzleTempComp:
    """[k2_nozzle_tempcomp] – s. Modul-Docstring."""

    def __init__(self, config):
        self.printer = printer = config.get_printer()
        self.gcode = printer.lookup_object("gcode")
        self.konsole = _K2K.Konsole(printer, "k2_nozzle_tempcomp", self.gcode)
        enabled = config.getboolean("enable", True)
        a = config.getfloat("a", STOCK_A)
        b = config.getfloat("b", STOCK_B)
        c = config.getfloat("c", STOCK_C)
        max_comp = config.getfloat("max_comp", STOCK_MAX_COMP,
                                   minval=0.0, maxval=CLAMP_MAX)
        self.fade_with_mesh = config.getboolean("fade_with_mesh", True)
        self.ref_on_mesh_calibrate = config.getboolean(
            "ref_on_mesh_calibrate", True)
        self.heater_name = config.get("heater", "extruder")
        self.core = CompCore(a, b, c, max_comp, enabled)
        self.heater = None
        self.print_stats = None
        self.bed_mesh = None
        self.gcode_move = None
        self.next_transform = None
        self.chain_position = "nicht eingehängt"
        self.applied = 0.0
        self._orig_mesh_cal = None
        printer.register_event_handler("klippy:connect", self._handle_connect)
        printer.register_event_handler("homing:home_rails_end",
                                       self._handle_home_rails_end)
        self.gcode.register_command(
            "KTWO_NOZZLE_TEMPCOMP", self.cmd_TEMPCOMP,
            desc="Düsen-Temperaturkompensation (Stock SET_TEMP_NOT_LINEAR): "
                 "Status | A= B= C= | RESET=1 | ENABLE=0/1 | COMP_Z= | REF=")

    # -- Lebenszyklus --------------------------------------------------
    def _handle_connect(self):
        pheaters = self.printer.lookup_object("heaters")
        try:
            self.heater = pheaters.lookup_heater(self.heater_name)
        except Exception as exc:
            raise self.printer.config_error(
                "[k2_nozzle_tempcomp] heater '%s' nicht gefunden: %s"
                % (self.heater_name, exc))
        self.print_stats = self.printer.lookup_object("print_stats", None)
        self.bed_mesh = self.printer.lookup_object("bed_mesh", None)
        self.gcode_move = self.printer.lookup_object("gcode_move")
        self._insert_transform()
        if self.ref_on_mesh_calibrate and self.bed_mesh is not None:
            orig = self.gcode.register_command("BED_MESH_CALIBRATE", None)
            if orig is not None:
                self._orig_mesh_cal = orig
                self.gcode.register_command(
                    "BED_MESH_CALIBRATE", self._cmd_mesh_calibrate,
                    desc="Bett-Netz messen (+ Referenz [k2_nozzle_tempcomp])")
        if self.print_stats is None:
            self.konsole.warnung("kein [print_stats]/[virtual_sdcard] – "
                                 "Kompensation nie aktiv")

    def _insert_transform(self):
        """Direkt über bed_mesh einhängen (Kette ablaufen)."""
        gm = self.gcode_move
        bm = self.bed_mesh
        parent, node = None, getattr(gm, "move_transform", None)
        seen = 0
        while node is not None and node is not bm and seen < 32:
            parent, node = node, getattr(node, "next_transform", None)
            seen += 1
        if bm is not None and node is bm and parent is not None:
            self.next_transform = bm
            parent.next_transform = self
            self.chain_position = "über bed_mesh, unter %s" % (
                type(parent).__name__,)
        else:
            self.next_transform = gm.set_move_transform(self, force=True)
            self.chain_position = "oben, darunter %s" % (
                type(self.next_transform).__name__,)
            if bm is not None and node is not bm:
                self.konsole.warnung("bed_mesh nicht in der Transform-Kette "
                                     "gefunden – oben eingehängt")
        logging.info("k2_nozzle_tempcomp: Transform %s", self.chain_position)

    def chain_text(self):
        """Aktuelle Transform-Kette ab gcode_move (Diagnose)."""
        names, node, n = [], getattr(self.gcode_move, "move_transform", None), 0
        while node is not None and n < 32:
            names.append("k2_nozzle_tempcomp" if node is self
                         else type(node).__name__)
            if node is self.bed_mesh:
                names.append("toolhead")
                break
            node, n = getattr(node, "next_transform", None), n + 1
        return " -> ".join(names) or self.chain_position

    def _smoothed_temp(self):
        # Stock liest heater.smoothed_temp direkt; Upstream
        # Heater hat dasselbe Attribut (get_temp() liefert bei veraltetem
        # Messwert 0 – daher das Attribut).
        t = getattr(self.heater, "smoothed_temp", None)
        if t is None:
            t = self.heater.get_temp(
                self.printer.get_reactor().monotonic())[0]
        return float(t)

    def _handle_home_rails_end(self, homing_state, rails):
        try:
            axes = homing_state.get_axes()
        except Exception:
            return
        if 2 not in axes or self.heater is None:
            return
        self.core.set_reference(self._smoothed_temp(), "G28 Z",
                                reset_last=True)

    def _cmd_mesh_calibrate(self, gcmd):
        self._orig_mesh_cal(gcmd)
        if self.heater is not None:
            self.core.set_reference(self._smoothed_temp(),
                                    "BED_MESH_CALIBRATE", reset_last=False)

    # -- Freigabe --------------------------------------------------------
    def _target(self):
        return float(getattr(self.heater, "target_temp", 0.0) or 0.0)

    def release_reason(self):
        """None = freigegeben, sonst Grund."""
        if not self.core.enabled:
            return "abgeschaltet"
        if self.heater is None:
            return "nicht verbunden"
        ps = self.print_stats
        if ps is None or getattr(ps, "state", None) not in ACTIVE_STATES:
            return "kein Druck"
        if self.bed_mesh is None or self.bed_mesh.get_mesh() is None:
            return "kein Netz geladen"
        if self.core.ref is None:
            return "keine Referenz (G28 Z)"
        return None

    def _base_comp(self):
        if self.release_reason() is not None:
            return 0.0
        return self.core.current(self._target())

    def _factor(self, z):
        if not self.fade_with_mesh or self.bed_mesh is None:
            return 1.0
        return float(self.bed_mesh.get_z_factor(z))

    # -- Transform --------------------------------------------------------
    def get_position(self):
        pos = list(self.next_transform.get_position())
        comp = self._base_comp()
        if comp:
            pos[2] = invert_fade(pos[2], comp, self._factor)
        return pos

    def move(self, newpos, speed):
        comp = self._base_comp()
        if comp:
            add = comp * self._factor(newpos[2])
            self.applied = add
            if add:
                newpos = list(newpos)
                newpos[2] = newpos[2] + add
        else:
            self.applied = 0.0
        self.next_transform.move(newpos, speed)

    # -- G-Code ---------------------------------------------------------
    def cmd_TEMPCOMP(self, gcmd):
        core = self.core
        changed = []
        if gcmd.get_int("RESET", 0, minval=0, maxval=1):
            core.factory_reset()
            changed.append("Werkswerte a=%g b=%g c=%g max=%g"
                           % (STOCK_A, STOCK_B, STOCK_C, STOCK_MAX_COMP))
        a = gcmd.get_float("A", None)
        b = gcmd.get_float("B", None)
        c = gcmd.get_float("C", None)
        for n, v in (("A", a), ("B", b), ("C", c)):
            if v is not None and not math.isfinite(v):
                raise gcmd.error("[k2_nozzle_tempcomp] %s ungültig" % n)
        if a is not None or b is not None or c is not None:
            core.set_coeffs(a, b, c)
            changed.append("a=%g b=%g c=%g" % (core.a, core.b, core.c))
        en = gcmd.get_int("ENABLE", None, minval=0, maxval=1)
        if en is not None:
            core.enabled = bool(en)
            changed.append("AN" if en else "AUS")
        ref = gcmd.get_float("REF", None, minval=0.0, maxval=500.0)
        if ref is not None:
            core.set_reference(ref, "REF= von Hand", reset_last=True)
            changed.append("Referenz %.2f °C" % ref)
        comp_z = gcmd.get_float("COMP_Z", None)
        if comp_z is not None:
            if not math.isfinite(comp_z):
                raise gcmd.error("[k2_nozzle_tempcomp] COMP_Z ungültig")
            core.force(comp_z, self._target() if self.heater else 0.0)
            changed.append("COMP_Z %.3f bis zur nächsten Solländerung"
                           % core.now_comp)
        st = self.get_status()
        msg = ("[k2_nozzle_tempcomp] %s, %s; comp %.3f mm (angewandt %.3f), "
               "Soll %.1f °C, Referenz %s (%s), a=%g b=%g c=%g max=%g, "
               "Fade %s, Kette: %s"
               % ("AN" if st["enabled"] else "AUS",
                  "aktiv" if st["active"] else "inaktiv: " + st["reason"],
                  st["comp"], st["applied"], st["target"],
                  "-" if st["ref_temperature"] is None
                  else "%.2f °C" % st["ref_temperature"],
                  st["ref_source"] or "-", st["a"], st["b"], st["c"],
                  st["max_comp"], "wie Netz" if self.fade_with_mesh else "aus",
                  self.chain_text()))
        if changed:
            msg = "[k2_nozzle_tempcomp] gesetzt: %s\n%s" % (
                "; ".join(changed), msg)
        gcmd.respond_info(msg)

    def get_status(self, eventtime=None):
        reason = self.release_reason()
        target = self._target() if self.heater is not None else 0.0
        comp = self.core.current(target) if reason is None else 0.0
        return {"enabled": self.core.enabled,
                "active": reason is None,
                "reason": reason or "",
                "comp": comp,
                "applied": self.applied,
                "target": target,
                "ref_temperature": self.core.ref,
                "ref_source": self.core.ref_source,
                "a": self.core.a, "b": self.core.b, "c": self.core.c,
                "max_comp": self.core.max_comp,
                "fade_with_mesh": self.fade_with_mesh,
                "forced": self.core.forced,
                "recalcs": self.core.recalcs}


def load_config(config):
    return PrinterK2NozzleTempComp(config)
