# -*- coding: utf-8 -*-
"""k2_zalign.py – Ersatz für das Creality-[z_align] (Dual-Z-Lichtschranken
PA15/PA8) mit UPSTREAM-Mitteln (Klipper Commit 9cb9975), K2 Plus (F008).

ENTWURF 17.10.2026 – am Gerät UNGETESTET. Ausgeliefert GESPERRT
(``allow_move: False`` + Laufzeit-Freigabe ``_PI_FREIGABE allow_move``).
Doku: ``config-pi/README.md``, ``config-pi/zalign.cfg``. Alles mit ANNAHME ist nicht belegt.

Was Stock macht (Kurzfassung): Die MCU fährt
beide Z-Motoren SELBST (Step-Pins direkt getoggelt, Creality-Befehle
``config_z_align_add``/``query_z_align``) nach unten auf je eine
Lichtschranke; jede Seite stoppt einzeln, Abbruch wenn die Wegdifferenz
beider Seiten ``safe_dist`` übersteigt; dann ``rising_dist`` hoch, langsam
wieder runter (Feinmessung, ``deltaError1``). Danach setzt der Host Z=360
und fährt schnell auf Z54, erst dann tastet die Düse an (G28 Z).

Dieser Ersatz nutzt KEINE Creality-Befehle, nur Upstream-Mechanik:
  * je Lichtschranke ein normaler Upstream-Endstop (``config_endstop`` +
    ``trsync``), am Endstop hängt GENAU ein Z-Stepper (z an PA15, z1 an PA8);
  * Fahrt über ``PrinterHoming.manual_home`` (wie MANUAL_STEPPER ... STOP_ON_
    ENDSTOP): beide Endstops im selben HomingMove, jede Seite stoppt in der
    MCU einzeln (``stepper_stop_on_trigger``), die andere fährt weiter;
  * in ETAPPEN (``chunk_dist``), damit ``safe_dist`` (maximale Wegdifferenz
    der beiden Seiten nach dem ersten Auslösen) auch bei einer defekten
    Schranke hart eingehalten wird; nach jeder Etappe Weg je Stepper aus
    ``get_mcu_position()`` (MCU-Position nach dem Stopp) und Pegel aus
    ``query_endstop()`` (= QUERY_ENDSTOPS);
  * lesend: beide Schranken erscheinen in Upstream-``QUERY_ENDSTOPS`` als
    ``zalign_z``/``zalign_z1`` (auch im Mainsail-Endstop-Panel) und in
    ``KTWO_ZALIGN_STATUS``.

Ablauf ``KTWO_ZALIGN`` (Bewegung nur mit allen Freigaben):
  0  Sperren: allow_move, Freigabe-Makro, kein laufender Druck, Motor-Init
     (Event ``k2_motor:ready``), Schranken-Startzustand.
  1  Startzustand: beide FREI. Ist eine ausgelöst -> Abbruch OHNE Bewegung,
     außer ``LIFT=1`` (Bediener hat gesehen, dass das Bett unten steht):
     dann ``rising_dist`` langsam hoch, danach müssen beide frei sein.
  A  schnell (``quick_speed``) abwärts in Etappen bis beide ausgelöst;
     Abbruch: max_travel ohne Auslösen, nur eine Seite und Wegdifferenz
     >= safe_dist, Schranke wird wieder frei, Stopp ohne Schranke, Timeout.
  B  beide ``rising_dist`` hoch (Lage bekannt: beide standen an der
     Schranke) -> beide müssen FREI melden (sonst Abbruch).
  C  langsam (``slow_speed``) abwärts bis beide ausgelöst (gleiche
     Abbruchregeln, Budget rising_dist + slow_extra_dist); Delta = Weg z1 -
     Weg z. |Delta| <= retry_tolerance -> fertig, sonst B+C bis ``retries``.
  Ende: Z bleibt NICHT referenziert (Default ``zmax_position: 0``) -> danach
     G28 Z (Düse tastet). Mit ``zmax_position > 0`` wird Z auf diesen Wert
     gesetzt (Stock setzt 360, gemessen 352,2 – erst am Gerät festlegen).

Keine Ziffern in G-Code-Namen (Upstream-Parser, config-pi/README §2.3).
Python-3.9-kompatibel. Keine Seriennummern/UUIDs.

Konsole (23.10.2026): Alle Fehler kommen als G-Code-Fehler (``!! …``) des
Befehls KTWO_ZALIGN. Scheitert nach einem Abbruch zusätzlich das Aufräumen
(Z-Homing-Status zurücksetzen), steht das in derselben roten Meldung
(„ACHTUNG …“) – vorher nur im klippy.log.
k2_konsole: nicht nötig – keine Timer/Connect-Meldungen, alles läuft im Befehl.
"""

import inspect
import logging
import math

# Harte Grenzen (Stock-Werte aus der Stock-printer.cfg [z_align], Z. 386-399,
# sind die OBERGRENZEN; Defaults bewusst darunter).
QUICK_SPEED_MAX = 30.0     # Stock quick_speed 30 mm/s (Z. 388)
SLOW_SPEED_MAX = 10.0      # Stock slow_speed 10 mm/s (Z. 389)
RISING_DIST_MAX = 20.0     # Stock rising_dist 10 mm (Z. 390), F008-Alt 20
SAFE_DIST_MAX = 40.0       # Stock safe_dist 40 mm (Z. 391)
MAX_TRAVEL_MAX = 400.0     # > Z-Bereich 370 mm (position_min -10 .. max 360)
SLOW_EXTRA_MAX = 10.0
TIMEOUT_MAX = 300.0
RETRIES_MAX = 5            # Stock retries 5 (Z. 394)
RETRY_TOL_MAX = 0.5
EPS = 1e-6
NAMES = ("z", "z1")

DEFAULTS = {
    "allow_move": False,
    "freigabe_macro": "_PI_FREIGABE",
    "require_motor_ready": True,
    "quick_speed": 10.0,
    "slow_speed": 5.0,
    "rising_dist": 10.0,
    "safe_dist": 10.0,
    "chunk_dist": 5.0,
    "max_travel": 370.0,
    "slow_extra_dist": 3.0,
    "retries": 3,
    "retry_tolerance": 0.025,
    "timeout": 150.0,
    "zmax_position": 0.0,
}


class ZAlignError(Exception):
    """Abbruch mit Meldung (wird als G-Code-Fehler gemeldet)."""


def validate_cfg(cfg):
    """Kreuzprüfungen (Einzelgrenzen prüft Klipper beim Lesen bzw.
    check_limits). Wirft ValueError."""
    check_limits(cfg)
    if cfg["chunk_dist"] > cfg["safe_dist"] + EPS:
        raise ValueError("chunk_dist (%.2f) muss <= safe_dist (%.2f) sein – "
                         "sonst kann eine Etappe safe_dist überschreiten"
                         % (cfg["chunk_dist"], cfg["safe_dist"]))
    if cfg["slow_speed"] > cfg["quick_speed"] + EPS:
        raise ValueError("slow_speed (%.2f) muss <= quick_speed (%.2f) sein"
                         % (cfg["slow_speed"], cfg["quick_speed"]))
    if cfg["rising_dist"] + cfg["slow_extra_dist"] > cfg["max_travel"] + EPS:
        raise ValueError("rising_dist + slow_extra_dist > max_travel")
    return cfg


def check_limits(cfg):
    def rng(key, lo, hi, lo_incl=False):
        v = cfg[key]
        if (v < lo - EPS if lo_incl else v <= lo) or v > hi + EPS:
            raise ValueError("%s=%r außerhalb %s%s, %s]" % (
                key, v, "[" if lo_incl else "(", lo, hi))
    rng("quick_speed", 0.0, QUICK_SPEED_MAX)
    rng("slow_speed", 0.0, SLOW_SPEED_MAX)
    rng("rising_dist", 0.0, RISING_DIST_MAX)
    rng("safe_dist", 0.0, SAFE_DIST_MAX)
    rng("chunk_dist", 0.0, SAFE_DIST_MAX)
    rng("max_travel", 0.0, MAX_TRAVEL_MAX)
    rng("slow_extra_dist", 0.0, SLOW_EXTRA_MAX)
    rng("timeout", 0.0, TIMEOUT_MAX)
    rng("retry_tolerance", 0.0, RETRY_TOL_MAX)
    rng("retries", 0, RETRIES_MAX, lo_incl=True)
    rng("zmax_position", 0.0, 400.0, lo_incl=True)


def plan_lines(cfg):
    """DRY_RUN-Plan (Text, keine Bewegung)."""
    c = cfg
    n_a = int(math.ceil(c["max_travel"] / c["chunk_dist"] - EPS))
    return [
        "Plan KTWO_ZALIGN (keine Bewegung):",
        "0 Sperren: allow_move=%s, Freigabe-Makro=%s, Motor-Init nötig=%s"
        % (c["allow_move"], c["freigabe_macro"] or "-",
           c["require_motor_ready"]),
        "1 Startzustand: beide Schranken müssen FREI sein (sonst Abbruch; "
        "LIFT=1 -> %.1f mm hoch mit %.1f mm/s)" % (c["rising_dist"],
                                                  c["slow_speed"]),
        "A abwärts %.1f mm/s in Etappen à %.1f mm, max %.1f mm (%d Etappen), "
        "Abbruch bei Wegdifferenz >= safe_dist %.1f mm nach erster Schranke"
        % (c["quick_speed"], c["chunk_dist"], c["max_travel"], n_a,
           c["safe_dist"]),
        "B %.1f mm hoch (%.1f mm/s), danach beide FREI" % (
            c["rising_dist"], c["quick_speed"]),
        "C abwärts %.1f mm/s, max %.1f mm, Delta <= %.3f mm, bis %d Wiederholungen"
        % (c["slow_speed"], c["rising_dist"] + c["slow_extra_dist"],
           c["retry_tolerance"], c["retries"]),
        "Ende: Z %s; Timeout gesamt %.0f s" % (
            "= %.3f (referenziert)" % c["zmax_position"]
            if c["zmax_position"] > 0 else "NICHT referenziert -> danach G28 Z",
            c["timeout"]),
    ]


class ZAlignCore:
    """Ablauflogik ohne Klipper (Hardware-Adapter injiziert, Fakes in den
    Tests).

    hw.query()            -> {"z": bool, "z1": bool}  (True = ausgelöst)
    hw.prepare()          Z für relative Fahrten nutzbar machen
    hw.descend(dist, v)   -> {"z": mm, "z1": mm}  abwärts (Z+), Homing-Fahrt
                           mit beiden Schranken, gefahrener Weg je Seite
    hw.lift(dist, v)      aufwärts (Z-), normale Fahrt
    hw.finish(zpos)       zpos None -> Z NICHT referenziert, sonst Z=zpos
    clock()               Sekunden (monoton)
    log(text)             Meldung an die Konsole
    """

    def __init__(self, cfg, hw, clock, log=None):
        self.cfg = cfg
        self.hw = hw
        self.clock = clock
        self.log = log or (lambda msg: None)
        self.t0 = None
        self.moved = False          # True ab der ersten Z-Vorbereitung/Fahrt
        self.last_result = None

    def _check_time(self, where):
        if self.clock() - self.t0 > self.cfg["timeout"]:
            raise ZAlignError("Timeout %.0f s überschritten (%s)"
                              % (self.cfg["timeout"], where))

    def _query(self):
        st = self.hw.query()
        return {n: bool(st[n]) for n in NAMES}

    def descend(self, budget, speed, phase):
        """Abwärts in Etappen bis beide Schranken ausgelöst sind."""
        c = self.cfg
        weg = {n: 0.0 for n in NAMES}
        trig = {n: False for n in NAMES}
        first = None
        kipp = 0.0          # Zunahme der Wegdifferenz seit erstem Auslösen
        rest = float(budget)
        etappen = 0
        while True:
            self._check_time("Phase %s" % phase)
            seg = min(c["chunk_dist"], rest)
            if first is not None:
                seg = min(seg, c["safe_dist"] - kipp)
            if seg <= EPS:
                if first is not None:
                    other = [n for n in NAMES if not trig[n]][0]
                    raise ZAlignError(
                        "Phase %s: nur Schranke %s ausgelöst, %s nach %.2f mm "
                        "Wegdifferenz (safe_dist %.2f) nicht – Abbruch "
                        "(Schranke/Kabel %s prüfen, Bett verkippt?)"
                        % (phase, first, other, kipp, c["safe_dist"], other))
                raise ZAlignError(
                    "Phase %s: keine Schranke innerhalb %.1f mm ausgelöst – "
                    "Abbruch (Polarität/Pull-up/Kabel prüfen)"
                    % (phase, budget))
            d = self.hw.descend(seg, speed)
            etappen += 1
            rest -= seg
            st = self._query()
            for n in NAMES:
                moved = float(d[n])
                if moved < -EPS or moved > seg + 0.01:
                    raise ZAlignError(
                        "Phase %s: Wegmessung %s=%.4f mm bei Etappe %.3f mm "
                        "unplausibel" % (phase, n, moved, seg))
                if trig[n] and not st[n]:
                    raise ZAlignError(
                        "Phase %s: Schranke %s wieder FREI nach Auslösen "
                        "(Signal instabil/Prellen) – Abbruch" % (phase, n))
                if moved < seg - 0.01 and not st[n]:
                    raise ZAlignError(
                        "Phase %s: Seite %s nach %.3f/%.3f mm gestoppt, "
                        "Schranke meldet aber FREI (Störung/Prellen?) – "
                        "Abbruch" % (phase, n, moved, seg))
                weg[n] += moved
            neu = [n for n in NAMES if st[n] and not trig[n]]
            if first is None and neu:
                first = neu[0] if len(neu) == 1 else "beide"
            if first is not None:
                kipp += abs(float(d["z"]) - float(d["z1"]))
            for n in NAMES:
                trig[n] = trig[n] or st[n]
            if all(trig.values()):
                break
            if first is not None and kipp >= c["safe_dist"] - EPS:
                other = [n for n in NAMES if not trig[n]][0]
                raise ZAlignError(
                    "Phase %s: nur Schranke %s ausgelöst, %s nach %.2f mm "
                    "Wegdifferenz (safe_dist %.2f) nicht – Abbruch"
                    % (phase, first, other, kipp, c["safe_dist"]))
        res = {"weg_z": weg["z"], "weg_z1": weg["z1"],
               "diff": weg["z1"] - weg["z"], "erste": first,
               "etappen": etappen}
        self.log("Phase %s: Weg z=%.3f z1=%.3f mm, Differenz z1-z=%+.3f mm, "
                 "zuerst %s, %d Etappen" % (phase, weg["z"], weg["z1"],
                                             res["diff"], first, etappen))
        return res

    def run(self, lift_ok=False):
        c = self.cfg
        self.t0 = self.clock()
        result = {"ok": False, "error": None, "lift": False,
                  "phase_a": None, "phase_c": [], "delta": None,
                  "dauer_s": 0.0}
        prepared = False
        try:
            st = self._query()
            if any(st.values()):
                ausg = ", ".join(n for n in NAMES if st[n])
                if not lift_ok:
                    raise ZAlignError(
                        "Schranke(n) %s schon AUSGELÖST – keine Bewegung. Bett "
                        "steht unten (sichtbar prüfen!) -> KTWO_ZALIGN LIFT=1; "
                        "Bett NICHT unten -> Polarität/Pull-up falsch, STOP"
                        % ausg)
                self.hw.prepare()
                prepared = self.moved = True
                self.log("LIFT: %.1f mm hoch (%s ausgelöst)" % (
                    c["rising_dist"], ausg))
                self.hw.lift(c["rising_dist"], c["slow_speed"])
                result["lift"] = True
                st = self._query()
                if any(st.values()):
                    raise ZAlignError(
                        "nach LIFT %.1f mm noch ausgelöst: %s – Abbruch "
                        "(Polarität falsch oder Schranke verschmutzt)" % (
                            c["rising_dist"],
                            ", ".join(n for n in NAMES if st[n])))
            if not prepared:
                self.hw.prepare()
                prepared = self.moved = True
            result["phase_a"] = self.descend(c["max_travel"], c["quick_speed"],
                                             "A")
            for versuch in range(c["retries"] + 1):
                self._check_time("Phase B")
                self.hw.lift(c["rising_dist"], c["quick_speed"])
                st = self._query()
                if any(st.values()):
                    raise ZAlignError(
                        "Phase B: nach %.1f mm hoch noch ausgelöst: %s – "
                        "Abbruch" % (c["rising_dist"],
                                     ", ".join(n for n in NAMES if st[n])))
                r = self.descend(c["rising_dist"] + c["slow_extra_dist"],
                                 c["slow_speed"], "C%d" % (versuch + 1))
                result["phase_c"].append(r)
                result["delta"] = r["diff"]
                if abs(r["diff"]) <= c["retry_tolerance"] + EPS:
                    break
            else:
                raise ZAlignError(
                    "Delta z1-z %+.4f mm nach %d Versuchen > retry_tolerance "
                    "%.4f – Abbruch (Schranken-Wiederholgenauigkeit?)" % (
                        result["delta"], c["retries"] + 1,
                        c["retry_tolerance"]))
            zpos = c["zmax_position"] if c["zmax_position"] > 0 else None
            self.hw.finish(zpos)
            result["ok"] = True
            return result
        except Exception as e:
            result["error"] = str(e)
            if prepared:
                try:
                    self.hw.finish(None)
                except Exception as fe:
                    logging.exception("k2_zalign: finish nach Fehler")
                    result["finish_error"] = "%s: %s" % (type(fe).__name__, fe)
            raise
        finally:
            result["dauer_s"] = self.clock() - self.t0
            self.last_result = result


# ---------------------------------------------------------------------------
# Klipper-Anbindung
# ---------------------------------------------------------------------------

class KlipperZAlignHW:
    """Hardware-Adapter auf Upstream-APIs (homing.manual_home, MCU_endstop,
    MCU_stepper.get_mcu_position, toolhead.set_position/move,
    kinematics.clear_homing_state)."""

    def __init__(self, printer, endstops, steppers):
        self.printer = printer
        self.endstops = endstops        # {"z": (mcu_endstop, "zalign_z"), ...}
        self.steppers = steppers        # {"z": MCU_stepper, ...}

    def _toolhead(self):
        return self.printer.lookup_object("toolhead")

    def _zrange(self):
        th = self._toolhead()
        st = th.get_kinematics().get_status(
            self.printer.get_reactor().monotonic())
        return float(st["axis_minimum"][2]), float(st["axis_maximum"][2])

    def query(self):
        th = self._toolhead()
        pt = th.get_last_move_time()
        return {n: bool(es.query_endstop(pt))
                for n, (es, _name) in self.endstops.items()}

    def prepare(self):
        th = self._toolhead()
        zlo, _zhi = self._zrange()
        pos = th.get_position()
        pos[2] = zlo
        th.set_position(pos, homing_axes="z")

    def _set_z(self, z):
        th = self._toolhead()
        pos = th.get_position()
        pos[2] = z
        th.set_position(pos)
        return pos

    def descend(self, dist, speed):
        zlo, zhi = self._zrange()
        if dist > zhi - zlo + EPS:
            raise ZAlignError("Etappe %.2f mm > Z-Bereich" % dist)
        th = self._toolhead()
        pos = self._set_z(zlo)
        p0 = {n: s.get_mcu_position() for n, s in self.steppers.items()}
        target = list(pos)
        target[2] = zlo + dist
        phoming = self.printer.lookup_object("homing")
        endstops = [self.endstops[n] for n in NAMES]
        kw = {"triggered": True, "check_triggered": False}
        if "probe_pos" in inspect.signature(phoming.manual_home).parameters:
            kw["probe_pos"] = False
        phoming.manual_home(th, endstops, target, speed, **kw)
        p1 = {n: s.get_mcu_position() for n, s in self.steppers.items()}
        return {n: (p1[n] - p0[n]) * self.steppers[n].get_step_dist()
                for n in NAMES}

    def lift(self, dist, speed):
        zlo, zhi = self._zrange()
        if dist > zhi - zlo + EPS:
            raise ZAlignError("Hub %.2f mm > Z-Bereich" % dist)
        th = self._toolhead()
        pos = self._set_z(zhi)
        target = list(pos)
        target[2] = zhi - dist
        th.move(target, speed)
        th.wait_moves()

    def finish(self, zpos):
        th = self._toolhead()
        if zpos is None:
            th.get_last_move_time()
            th.get_kinematics().clear_homing_state("z")
            return
        self._set_z(float(zpos))


class PrinterK2ZAlign:
    """[k2_zalign] – Klipper-Objekt."""

    def __init__(self, config):
        self.printer = printer = config.get_printer()
        self.reactor = printer.get_reactor()
        self.name = config.get_name()
        cfg = dict(DEFAULTS)
        cfg["allow_move"] = config.getboolean("allow_move", False)
        cfg["freigabe_macro"] = config.get("freigabe_macro",
                                           DEFAULTS["freigabe_macro"]).strip()
        cfg["require_motor_ready"] = config.getboolean(
            "require_motor_ready", True)
        cfg["quick_speed"] = config.getfloat(
            "quick_speed", DEFAULTS["quick_speed"], above=0.,
            maxval=QUICK_SPEED_MAX)
        cfg["slow_speed"] = config.getfloat(
            "slow_speed", DEFAULTS["slow_speed"], above=0.,
            maxval=SLOW_SPEED_MAX)
        cfg["rising_dist"] = config.getfloat(
            "rising_dist", DEFAULTS["rising_dist"], above=0.,
            maxval=RISING_DIST_MAX)
        cfg["safe_dist"] = config.getfloat(
            "safe_dist", DEFAULTS["safe_dist"], above=0., maxval=SAFE_DIST_MAX)
        cfg["chunk_dist"] = config.getfloat(
            "chunk_dist", DEFAULTS["chunk_dist"], above=0.,
            maxval=SAFE_DIST_MAX)
        cfg["max_travel"] = config.getfloat(
            "max_travel", DEFAULTS["max_travel"], above=0.,
            maxval=MAX_TRAVEL_MAX)
        cfg["slow_extra_dist"] = config.getfloat(
            "slow_extra_dist", DEFAULTS["slow_extra_dist"], above=0.,
            maxval=SLOW_EXTRA_MAX)
        cfg["retries"] = config.getint("retries", DEFAULTS["retries"],
                                       minval=0, maxval=RETRIES_MAX)
        cfg["retry_tolerance"] = config.getfloat(
            "retry_tolerance", DEFAULTS["retry_tolerance"], above=0.,
            maxval=RETRY_TOL_MAX)
        cfg["timeout"] = config.getfloat("timeout", DEFAULTS["timeout"],
                                         above=0., maxval=TIMEOUT_MAX)
        cfg["zmax_position"] = config.getfloat(
            "zmax_position", DEFAULTS["zmax_position"], minval=0.,
            maxval=400.)
        try:
            validate_cfg(cfg)
        except ValueError as e:
            raise config.error("[%s] %s" % (self.name, e))
        self.cfg = cfg
        # Lichtschranken als normale Upstream-Endstops (Pin nur hier belegt)
        ppins = printer.lookup_object("pins")
        self.pin_desc = {"z": config.get("pin_z"), "z1": config.get("pin_z1")}
        self.stepper_names = {
            "z": config.get("stepper_z", "stepper_z"),
            "z1": config.get("stepper_z1", "stepper_z1")}
        self.endstops = {}
        qe = printer.load_object(config, "query_endstops")
        for n in NAMES:
            es = ppins.setup_pin("endstop", self.pin_desc[n])
            label = "zalign_" + n
            self.endstops[n] = (es, label)
            qe.register_endstop(es, label)
        self.steppers = {}
        self.motor_ready = False
        self.running = False
        self.last_query = {}
        self.last_result = None
        self.hw = None
        printer.register_event_handler("klippy:mcu_identify",
                                       self._handle_mcu_identify)
        printer.register_event_handler("k2_motor:ready", self._handle_motor)
        gcode = printer.lookup_object("gcode")
        gcode.register_command("KTWO_ZALIGN", self.cmd_KTWO_ZALIGN,
                               desc=self.cmd_KTWO_ZALIGN_help)
        gcode.register_command("KTWO_ZALIGN_STATUS",
                               self.cmd_KTWO_ZALIGN_STATUS,
                               desc=self.cmd_KTWO_ZALIGN_STATUS_help)

    # -- Ereignisse -------------------------------------------------------
    def _handle_mcu_identify(self):
        kin = self.printer.lookup_object("toolhead").get_kinematics()
        by_name = {s.get_name(): s for s in kin.get_steppers()}
        for n in NAMES:
            sname = self.stepper_names[n]
            s = by_name.get(sname)
            if s is None or not s.is_active_axis("z"):
                raise self.printer.config_error(
                    "[%s] Stepper '%s' fehlt oder ist kein Z-Stepper"
                    % (self.name, sname))
            es, _label = self.endstops[n]
            es.add_stepper(s)
            self.steppers[n] = s
        if self.steppers["z"] is self.steppers["z1"]:
            raise self.printer.config_error(
                "[%s] stepper_z und stepper_z1 müssen verschieden sein"
                % self.name)
        self.hw = KlipperZAlignHW(self.printer, self.endstops, self.steppers)

    def _handle_motor(self):
        self.motor_ready = True

    # -- Hilfen -----------------------------------------------------------
    def _freigabe_ok(self):
        macro = self.cfg["freigabe_macro"]
        if not macro:
            return True, "kein Freigabe-Makro konfiguriert"
        obj = self.printer.lookup_object("gcode_macro " + macro, None)
        if obj is None:
            return False, "Freigabe-Makro %s fehlt" % macro
        val = obj.get_status(self.reactor.monotonic()).get("allow_move")
        # Nur echte Wahrheitswerte (SET_GCODE_VARIABLE VALUE=True -> True);
        # ein String "False" gilt NICHT als Freigabe.
        ok = (not isinstance(val, str)) and bool(val)
        return ok, "%s allow_move=%s" % (macro, val)

    def _reset_ztilt(self):
        """z_tilt.applied ungültig machen: die Lichtschranken-Ausrichtung
        überschreibt die mechanische z_tilt-Korrektur (Upstream setzt das
        sonst nur bei motor_off zurück, z_tilt.py:69-83)."""
        zt = self.printer.lookup_object("z_tilt", None)
        st = getattr(zt, "z_status", None)
        if st is not None and hasattr(st, "reset"):
            st.reset()

    def _sperren(self):
        """Liste der Gründe, warum KTWO_ZALIGN nicht fahren darf."""
        gruende = []
        if not self.cfg["allow_move"]:
            gruende.append("[%s] allow_move: False" % self.name)
        ok, txt = self._freigabe_ok()
        if not ok:
            gruende.append(txt)
        if self.cfg["require_motor_ready"]:
            if self.printer.lookup_object("k2_motor", None) is None:
                gruende.append("[k2_motor] fehlt (require_motor_ready)")
            elif not self.motor_ready:
                gruende.append("Motor-Init nicht fertig (Event k2_motor:ready "
                               "fehlt; KTWO_MOTOR_INIT)")
        ps = self.printer.lookup_object("print_stats", None)
        if ps is not None:
            state = ps.get_status(self.reactor.monotonic()).get("state")
            if state in ("printing", "paused"):
                gruende.append("Druck aktiv (print_stats %s)" % state)
        if self.hw is None:
            gruende.append("Stepper nicht zugeordnet")
        return gruende

    def _query_text(self):
        st = self.hw.query()
        self.last_query = {self.endstops[n][1]: bool(st[n]) for n in NAMES}
        return " ".join("%s:%s" % (self.endstops[n][1],
                                   "TRIGGERED" if st[n] else "open")
                        for n in NAMES)

    # -- G-Code -------------------------------------------------------------
    cmd_KTWO_ZALIGN_STATUS_help = ("Lichtschranken PA15/PA8 lesen (keine "
                                   "Bewegung) + Sperren + letztes Ergebnis")

    def cmd_KTWO_ZALIGN_STATUS(self, gcmd):
        lines = ["[k2_zalign] %s (TRIGGERED = Schranke unterbrochen, "
                 "Pins %s/%s)" % (self._query_text(), self.pin_desc["z"],
                                  self.pin_desc["z1"])]
        g = self._sperren()
        lines.append("Bewegung: %s" % ("FREI" if not g else
                                       "GESPERRT – " + "; ".join(g)))
        r = self.last_result
        if r:
            lines.append("Letzter Lauf: ok=%s delta=%s phase_a_diff=%s "
                         "dauer=%.1fs fehler=%s" % (
                             r["ok"],
                             "%+.4f" % r["delta"] if r["delta"] is not None
                             else "-",
                             "%+.3f" % r["phase_a"]["diff"]
                             if r.get("phase_a") else "-",
                             r["dauer_s"], r["error"]))
        gcmd.respond_info("\n".join(lines))

    cmd_KTWO_ZALIGN_help = ("Dual-Z an den Lichtschranken ausrichten "
                            "(GESPERRT; DRY_RUN=1 = nur Plan, LIFT=1 = "
                            "Bett steht sichtbar unten)")

    def cmd_KTWO_ZALIGN(self, gcmd):
        dry = gcmd.get_int("DRY_RUN", 0, minval=0, maxval=1)
        lift_ok = gcmd.get_int("LIFT", 0, minval=0, maxval=1) == 1
        if dry:
            lines = plan_lines(self.cfg)
            g = self._sperren()
            lines.append("Sperren: %s" % ("keine" if not g else "; ".join(g)))
            lines.append("Schranken jetzt: %s" % self._query_text())
            gcmd.respond_info("\n".join(lines))
            return
        g = self._sperren()
        if g:
            raise gcmd.error("KTWO_ZALIGN gesperrt: %s. Keine Bewegung."
                             % "; ".join(g))
        if self.running:
            raise gcmd.error("KTWO_ZALIGN läuft bereits")
        self.running = True
        core = ZAlignCore(self.cfg, self.hw, self.reactor.monotonic,
                          log=gcmd.respond_info)
        try:
            res = core.run(lift_ok=lift_ok)
        except (ZAlignError, self.printer.command_error) as e:
            self.last_result = getattr(core, "last_result", None)
            fe = (self.last_result or {}).get("finish_error")
            extra = ("" if not fe else
                     " – ACHTUNG: Z-Homing-Status nicht zurückgesetzt (%s), "
                     "vor jeder Z-Bewegung G28 Z" % (fe,))
            if core.moved:
                self._reset_ztilt()
                raise gcmd.error("KTWO_ZALIGN Abbruch: %s (Z ist NICHT "
                                 "referenziert)%s" % (e, extra))
            raise gcmd.error("KTWO_ZALIGN Abbruch ohne Bewegung: %s%s"
                             % (e, extra))
        finally:
            self.running = False
        self._reset_ztilt()
        self.last_result = res
        gcmd.respond_info(
            "KTWO_ZALIGN fertig: Startverkippung (Phase A) z1-z=%+.3f mm, "
            "Delta (Phase C) %+.4f mm nach %d Versuch(en), %.1f s. %s" % (
                res["phase_a"]["diff"], res["delta"], len(res["phase_c"]),
                res["dauer_s"],
                "Z=%.3f gesetzt" % self.cfg["zmax_position"]
                if self.cfg["zmax_position"] > 0
                else "Z NICHT referenziert -> jetzt G28 Z"))

    def get_status(self, eventtime=None):
        return {
            "allow_move": bool(self.cfg["allow_move"]),
            "running": self.running,
            "motor_ready": self.motor_ready,
            "last_query": dict(self.last_query),
            "last_result": dict(self.last_result) if self.last_result else None,
        }


def load_config(config):
    return PrinterK2ZAlign(config)
