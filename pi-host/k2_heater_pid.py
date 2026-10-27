# -*- coding: utf-8 -*-
"""k2_heater_pid – PID-Werte eines Heizers zur Laufzeit setzen, zweiter
PID-Satz für hohe Temperaturen.

Upstream-Klipper liest pid_Kp/Ki/Kd nur beim Start (ControlPID.__init__);
neue Werte wirken erst nach SAVE_CONFIG + Neustart. Beim K2 werden dabei die
Motoren stromlos und das Bett sackt ab (danach G28 + Z_TILT_ADJUST). Zum
Vergleichen mehrerer Wertesätze (22.10.2026: PID_CALIBRATE-Werte pendelten
±1,5–2 °C, M109 hing) setzt dieses Modul die Werte sofort:

    SET_HEATER_PID HEATER=extruder KP=14.296 KI=1.271 KD=40.206 [SAVE=1]
    SET_HEATER_PID HEATER=extruder SET=HIGH KP=.. KI=.. KD=.. [SAVE=1]

Fehlende KP/KI/KD bleiben unverändert. Die Werte gelten bis zum nächsten
Neustart; SAVE=1 merkt sie zusätzlich für SAVE_CONFIG vor (wie PID_CALIBRATE).
Der I-Anteil wird beim Umschalten umgerechnet, damit die Heizleistung nicht
springt. Während PID_CALIBRATE (Regler ist dann kein PID) wird abgelehnt.

Zweiter PID-Satz (Ersatz für Stock high_temp_value 280 / pid_*_high_temp, das
Upstream nicht kennt): Sind high_pid_kp/ki/kd gesetzt, schaltet ein Timer
(alle 0,5 s) den Regler von high_heater um – auf den Hochsatz, sobald das
Ziel >= high_temp ist, zurück auf den Normalsatz (= pid_Kp/Ki/Kd des Heizers
beim Start), sobald das Ziel unter high_temp - high_hysteresis fällt; Ziel 0
schaltet sofort zurück. Läuft PID_CALIBRATE, wird nichts umgeschaltet.

SET_HEATER_PID ohne SET ändert den gerade aktiven Satz (SAVE=1 bei aktivem
Hochsatz schreibt high_pid_* in [k2_heater_pid]). SET=NORMAL|HIGH (nur für
high_heater) speichert den genannten Satz und wendet ihn nur an, wenn er
gerade aktiv ist; SET=HIGH legt einen fehlenden Hochsatz an (Umschaltung ab
dann aktiv).

Config [k2_heater_pid] (alle Optionen optional):
    high_heater: extruder
    high_temp: 280           # Ziel °C, ab dem der Hochsatz gilt
    high_hysteresis: 10      # zurück erst unter high_temp - high_hysteresis
    high_pid_kp / high_pid_ki / high_pid_kd   # Klipper-Skala wie pid_Kp;
                             # fehlen sie, gibt es keine Umschaltung
Status (printer["k2_heater_pid"]): aktiv ("normal" | "hoch" | "aus" = keine
Umschaltung), high_heater, high_temp, normal [kp, ki, kd], hoch [..] | None.

Konsole (23.10.2026, k2_konsole): Ein Fehler im Umschalt-Timer erscheint als
``!! [k2_heater_pid] …`` in Mainsail (höchstens alle 10 min) – sonst liefe
der Heizer unbemerkt mit dem falschen PID-Satz weiter.
"""
import logging

try:
    from . import k2_konsole as _K2K
except ImportError:
    import k2_konsole as _K2K

PID_PARAM_BASE = 255.          # wie klippy/extras/heaters.py
PID_ATTRS = ("Kp", "Ki", "Kd", "temp_integ_max", "heater_max_power",
             "prev_temp_integ")
CHECK_INTERVAL = 0.5           # s, Prüftakt der Umschaltung
HIGH_TEMP_DEFAULT = 280.       # Stock high_temp_value
HIGH_HYSTERESIS_DEFAULT = 10.


def config_section(name):
    """Config-Abschnitt eines Heizers für SAVE=1 (extruder*, heater_bed,
    sonst heater_generic <name>)."""
    if name.startswith("extruder") or name == "heater_bed":
        return name
    return "heater_generic " + name


def is_pid(ctl):
    return all(hasattr(ctl, a) for a in PID_ATTRS)


def pid_values(ctl):
    """Aktive Werte eines ControlPID in Klipper-Skala (wie in der Config)."""
    return (ctl.Kp * PID_PARAM_BASE, ctl.Ki * PID_PARAM_BASE,
            ctl.Kd * PID_PARAM_BASE)


def apply_pid(ctl, kp, ki, kd):
    """Werte (Klipper-Skala wie in der Config) in einen ControlPID setzen;
    I-Zustand so umrechnen, dass Ki*integ (Heizleistung) gleich bleibt."""
    old_ki = ctl.Ki
    ctl.Kp = kp / PID_PARAM_BASE
    ctl.Ki = ki / PID_PARAM_BASE
    ctl.Kd = kd / PID_PARAM_BASE
    if ctl.Ki:
        ctl.temp_integ_max = ctl.heater_max_power / ctl.Ki
        integ = ctl.prev_temp_integ * old_ki / ctl.Ki if old_ki else 0.
        ctl.prev_temp_integ = max(0., min(ctl.temp_integ_max, integ))
    else:
        ctl.temp_integ_max = 0.
        ctl.prev_temp_integ = 0.


def _fmt(vals):
    return "Kp=%.3f Ki=%.3f Kd=%.3f" % tuple(vals)


class K2HeaterPid:
    cmd_SET_HEATER_PID_help = ("PID-Werte eines Heizers sofort setzen "
                               "(HEATER= KP= KI= KD= [SET=NORMAL|HIGH] "
                               "[SAVE=1])")

    def __init__(self, config):
        self.printer = config.get_printer()
        self.section = config.get_name()
        self.high_name = config.get("high_heater", "extruder")
        self.high_temp = config.getfloat("high_temp", HIGH_TEMP_DEFAULT,
                                         above=0.)
        self.high_hyst = config.getfloat("high_hysteresis",
                                         HIGH_HYSTERESIS_DEFAULT, minval=0.)
        vals = [config.getfloat(o, None, minval=0.)
                for o in ("high_pid_kp", "high_pid_ki", "high_pid_kd")]
        given = [v is not None for v in vals]
        if any(given) and not all(given):
            raise config.error("[%s] high_pid_kp, high_pid_ki und high_pid_kd "
                               "nur gemeinsam angeben" % (self.section,))
        self.high_set = tuple(vals) if all(given) else None
        self.normal_set = None
        self.heater = None
        self.active = "aus"
        self.reactor = None
        self.gcode = self.printer.lookup_object("gcode")
        self.konsole = _K2K.Konsole(self.printer, "k2_heater_pid", self.gcode)
        self.gcode.register_command("SET_HEATER_PID", self.cmd_SET_HEATER_PID,
                                    desc=self.cmd_SET_HEATER_PID_help)
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)
        self.printer.register_event_handler("klippy:ready",
                                            self._handle_ready)

    # --- Start ---------------------------------------------------------
    def _handle_connect(self):
        pheaters = self.printer.lookup_object("heaters")
        try:
            heater = pheaters.lookup_heater(self.high_name)
        except self.printer.config_error:
            if self.high_set is not None:
                raise self.printer.config_error(
                    "[%s] high_heater '%s' gibt es nicht"
                    % (self.section, self.high_name))
            logging.info("k2_heater_pid: Heizer %s fehlt – keine "
                         "PID-Umschaltung", self.high_name)
            return
        if not is_pid(heater.control):
            if self.high_set is not None:
                raise self.printer.config_error(
                    "[%s] high_heater '%s' braucht control: pid"
                    % (self.section, self.high_name))
            return
        self.heater = heater
        self.normal_set = pid_values(heater.control)
        if self.high_set is not None:
            self.active = "normal"

    def _handle_ready(self):
        if self.heater is None:
            return
        self.reactor = self.printer.get_reactor()
        self.reactor.register_timer(self._timer, self.reactor.NOW)

    # --- Umschaltung ---------------------------------------------------
    def _timer(self, eventtime):
        try:
            self._check(eventtime)
        except Exception as exc:
            # nie aus dem Timer heraus werfen (sonst Klipper-Shutdown)
            logging.exception("k2_heater_pid: Fehler bei der PID-Umschaltung")
            self.konsole.fehler(
                "%s: PID-Umschaltung fehlgeschlagen (%s: %s) – aktiver Satz "
                "bleibt %s, Details im klippy.log"
                % (self.high_name, type(exc).__name__, exc, self.active),
                schluessel="timer", pause=600., log=False)
        return eventtime + CHECK_INTERVAL

    def _want(self, target):
        if target <= 0.:
            return "normal"
        if target >= self.high_temp:
            return "hoch"
        if target < self.high_temp - self.high_hyst:
            return "normal"
        return self.active              # Hysterese-Band: bleibt

    def _check(self, eventtime):
        if self.high_set is None or self.heater is None:
            return
        ctl = self.heater.control
        if not is_pid(ctl):             # PID_CALIBRATE läuft
            return
        target = self.heater.get_temp(eventtime)[1]
        want = self._want(target)
        if want == self.active:
            return
        vals = self.high_set if want == "hoch" else self.normal_set
        with self.heater.lock:
            apply_pid(ctl, *vals)
        self.active = want
        if want == "hoch":
            why = "Ziel %.0f °C ≥ %.0f" % (target, self.high_temp)
        else:
            why = "Ziel %.0f °C < %.0f" % (target, self.high_temp
                                           - self.high_hyst)
        msg = ("[k2_heater_pid] %s: PID-Satz %s (%s): %s"
               % (self.high_name, want.upper(), why, _fmt(vals)))
        logging.info(msg)
        try:
            self.gcode.respond_info(msg)
        except Exception:
            pass

    # --- G-Code ----------------------------------------------------------
    def cmd_SET_HEATER_PID(self, gcmd):
        name = gcmd.get("HEATER")
        pheaters = self.printer.lookup_object("heaters")
        try:
            heater = pheaters.lookup_heater(name)
        except self.printer.config_error as exc:
            raise gcmd.error(str(exc))
        ctl = heater.control
        if not is_pid(ctl):
            raise gcmd.error("SET_HEATER_PID: %s regelt gerade nicht per PID "
                             "(control: pid nötig, nicht während "
                             "PID_CALIBRATE)" % (name,))
        which = gcmd.get("SET", None)
        managed = self.heater is not None and heater is self.heater
        if which is not None:
            which = which.strip().upper()
            if which not in ("NORMAL", "HIGH"):
                raise gcmd.error("SET_HEATER_PID: SET=NORMAL oder SET=HIGH")
            if not managed:
                raise gcmd.error("SET_HEATER_PID: SET nur für %s "
                                 "(high_heater)" % (self.high_name,))
        elif managed and self.active == "hoch":
            which = "HIGH"              # gerade aktiver Satz
        elif managed:
            which = "NORMAL"
        if which == "HIGH":
            base = self.high_set or pid_values(ctl)
        elif which == "NORMAL":
            base = self.normal_set or pid_values(ctl)
        else:
            base = pid_values(ctl)
        kp = gcmd.get_float("KP", base[0], minval=0.)
        ki = gcmd.get_float("KI", base[1], minval=0.)
        kd = gcmd.get_float("KD", base[2], minval=0.)
        save = gcmd.get_int("SAVE", 0)
        vals = (kp, ki, kd)
        if which is None:
            now = True
            label = ""
        else:
            if which == "HIGH":
                if self.high_set is None and self.active == "aus":
                    self.active = "normal"      # Umschaltung ab jetzt aktiv
                self.high_set = vals
                now = self.active == "hoch"
            else:
                self.normal_set = vals
                now = self.active in ("normal", "aus")
            label = " Satz %s" % (which,)
        if now:
            with heater.lock:
                apply_pid(ctl, kp, ki, kd)
            msg = ("SET_HEATER_PID %s%s: %s – sofort aktiv bis zum Neustart"
                   % (name, label, _fmt(vals)))
        else:
            msg = ("SET_HEATER_PID %s%s: %s – gespeichert, wirkt bei der "
                   "nächsten Umschaltung (aktiv: %s, bis zum Neustart)"
                   % (name, label, _fmt(vals), self.active))
        if save:
            configfile = self.printer.lookup_object("configfile")
            if which == "HIGH":
                section = self.section
                configfile.set(section, "high_pid_kp", "%.3f" % (kp,))
                configfile.set(section, "high_pid_ki", "%.3f" % (ki,))
                configfile.set(section, "high_pid_kd", "%.3f" % (kd,))
            else:
                section = config_section(name)
                configfile.set(section, "control", "pid")
                configfile.set(section, "pid_Kp", "%.3f" % (kp,))
                configfile.set(section, "pid_Ki", "%.3f" % (ki,))
                configfile.set(section, "pid_Kd", "%.3f" % (kd,))
            msg += "; für SAVE_CONFIG vorgemerkt ([%s])" % (section,)
        gcmd.respond_info(msg)

    def get_status(self, eventtime):
        def r(vals):
            return [round(v, 3) for v in vals] if vals else None
        return {"aktiv": self.active, "high_heater": self.high_name,
                "high_temp": self.high_temp, "normal": r(self.normal_set),
                "hoch": r(self.high_set)}


def load_config(config):
    return K2HeaterPid(config)
