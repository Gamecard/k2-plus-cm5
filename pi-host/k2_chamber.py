# -*- coding: utf-8 -*-
"""k2_chamber – Schutzlogik der PTC-Kammerheizung (K2 Plus, Upstream-Klipper).

Ersatz für die Creality-Zusätze (Abgleich mit Creality-klippy, Lücke L6,
README-k2_chamber.md):

1. Lüfter-Überwachung (Stock CR:extras/heaters.py:76-101 + fan_feedback.py):
   Steht der PTC-Lüfter (Tacho, Upstream ``tachometer_pin`` am
   ``[heater_fan chamber_fan]``) länger als ``fan_fault_time`` (Stock 15 s),
   während die Kammerheizung ein Ziel hat oder heizt, wird die Heizung
   sofort abgeschaltet (Stock: ``M141 S0`` + Meldung ``key519``).
   ``fan_fault_action: heater_off`` (Default) = Ziel 0 + Sperre bis
   ``KTWO_CHAMBER_RESET`` (strenger als Stock, dort hebt jedes neue
   ``M141 S>40`` die Sperre auf); ``shutdown`` = Klipper-Shutdown.
2. Bett-Vorrang (Stock CR:extras/heaters.py:277-283): solange das Bett ein
   Ziel hat und noch mehr als ``bed_tolerance`` darunter liegt, bleibt die
   PTC-Leistung 0.
3. 50-%-Anlauf (Stock CR:extras/heaters.py:284-287): in den ersten
   ``soft_start_time`` Sekunden (Stock 200 Regelzyklen à 0,3 s = 60 s) jeder
   Heizphase nur ``soft_start_power`` (Stock 0,5) der Reglerleistung.

Umsetzung: ``heater.set_pwm`` des Kammerheizers wird umhüllt (wirkt auf jede
Regelung, auch PID_CALIBRATE). Die Hülle läuft im Temperatur-Callback unter
``heater.lock`` und liest nur einfache Attribute. Die Lüfter-Überwachung ist
ein Reactor-Timer (1 s).

Konsole (23.10.2026, k2_konsole): Fehler als ``!! [k2_chamber] …``
(Lüfterfehler, Sperre), nicht lesbare Drehzahl als ``// [k2_chamber] …``
(höchstens alle 10 min). Ein unerwarteter Fehler im Überwachungs-Timer
wirft NICHT (kein Klipper-Shutdown), sondern sperrt die Kammerheizung wie
ein Lüfterfehler (Ziel 0, KTWO_CHAMBER_RESET) und meldet ihn rot.

Python >= 3.9 (Pi-Host), keine Hardware-Annahmen außer der Config.
"""

import logging

try:
    from . import k2_konsole as _K2K
except ImportError:
    import k2_konsole as _K2K

ACTIONS = {'heater_off': 'heater_off', 'shutdown': 'shutdown'}
CHECK_INTERVAL = 1.0


class K2Chamber:
    def __init__(self, config):
        self.printer = printer = config.get_printer()
        self.reactor = printer.get_reactor()
        self.name = config.get_name()
        self.heater_name = config.get('heater', 'chamber_heater')
        self.fan_name = config.get('fan', 'heater_fan chamber_fan')
        self.bed_name = config.get('bed_heater', 'heater_bed')
        self.bed_priority = config.getboolean('bed_priority', True)
        self.bed_tolerance = config.getfloat('bed_tolerance', 2.0, minval=0.)
        self.soft_start_time = config.getfloat('soft_start_time', 60.,
                                               minval=0.)
        self.soft_start_power = config.getfloat('soft_start_power', 0.5,
                                                above=0., maxval=1.)
        self.fan_fault_time = config.getfloat('fan_fault_time', 15.,
                                              minval=2., maxval=60.)
        self.min_rpm = config.getfloat('min_rpm', 100., minval=0.)
        self.action = config.getchoice('fan_fault_action', ACTIONS,
                                       'heater_off')
        self.heater = None
        self.bed = None
        self.fan = None
        # Zustand (Hülle, Temperatur-Thread)
        self.fault = False
        self.fault_msg = None
        self.bed_blocking = False
        self.soft_start_since = None
        self.soft_start_active = False
        # Zustand (Timer, Reactor)
        self.stall_since = None
        self.last_rpm = None
        self.fault_count = 0
        self.check_timer = None
        printer.register_event_handler('klippy:connect', self._handle_connect)
        printer.register_event_handler('klippy:ready', self._handle_ready)
        printer.register_event_handler('klippy:shutdown',
                                       self._handle_shutdown)
        gcode = printer.lookup_object('gcode')
        self.konsole = _K2K.Konsole(printer, 'k2_chamber', gcode)
        gcode.register_command('KTWO_CHAMBER_STATUS', self.cmd_STATUS,
                               desc=self.cmd_STATUS_help)
        gcode.register_command('KTWO_CHAMBER_RESET', self.cmd_RESET,
                               desc=self.cmd_RESET_help)
        gcode.register_command('KTWO_CHAMBER_WAIT', self.cmd_WAIT,
                               desc=self.cmd_WAIT_help)

    # ------------------------------------------------------------------
    # Verdrahtung
    # ------------------------------------------------------------------
    def _handle_connect(self):
        pheaters = self.printer.lookup_object('heaters')
        heaters = getattr(pheaters, 'heaters', {})
        self.heater = heaters.get(self.heater_name)
        if self.heater is None:
            raise self.printer.config_error(
                "[%s] heater: Heizer '%s' nicht gefunden"
                % (self.name, self.heater_name))
        if self.bed_priority:
            self.bed = heaters.get(self.bed_name)
            if self.bed is None:
                raise self.printer.config_error(
                    "[%s] bed_heater: Heizer '%s' nicht gefunden (oder "
                    "bed_priority: False)" % (self.name, self.bed_name))
        fan_obj = self.printer.lookup_object(self.fan_name, None)
        if fan_obj is None or not hasattr(fan_obj, 'fan'):
            raise self.printer.config_error(
                "[%s] fan: Lüfter-Abschnitt '%s' nicht gefunden"
                % (self.name, self.fan_name))
        tach = getattr(fan_obj.fan, 'tachometer', None)
        if tach is None or getattr(tach, '_freq_counter', None) is None:
            raise self.printer.config_error(
                "[%s] '%s' hat kein tachometer_pin – ohne Drehzahl keine "
                "Lüfter-Überwachung (Stock fan_feedback fan0_pin: PC6)"
                % (self.name, self.fan_name))
        heater_names = getattr(fan_obj, 'heater_names', None)
        if heater_names is not None and self.heater_name not in heater_names:
            raise self.printer.config_error(
                "[%s] '%s' läuft nicht mit '%s' mit (heater: %s) – der "
                "PTC-Lüfter muss an die Kammerheizung gekoppelt sein"
                % (self.name, self.fan_name, self.heater_name,
                   ', '.join(heater_names)))
        self.fan = fan_obj
        self._wrap_heater(self.heater)

    def _wrap_heater(self, heater):
        if getattr(heater, '_k2_chamber_wrapped', False):
            return
        orig_set_pwm = heater.set_pwm

        def set_pwm(read_time, value):
            return orig_set_pwm(read_time, self._filter_pwm(read_time, value))
        heater.set_pwm = set_pwm
        heater._k2_chamber_wrapped = True

    def _handle_ready(self):
        self.check_timer = self.reactor.register_timer(
            self._check_event, self.reactor.monotonic() + CHECK_INTERVAL)

    def _handle_shutdown(self):
        if self.check_timer is not None:
            self.reactor.update_timer(self.check_timer, self.reactor.NEVER)

    # ------------------------------------------------------------------
    # Leistungsfilter (Temperatur-Callback, unter heater.lock)
    # ------------------------------------------------------------------
    def _bed_blocks(self):
        bed = self.bed
        if bed is None:
            return False
        target = getattr(bed, 'target_temp', 0.) or 0.
        if target <= 0.:
            return False
        return (getattr(bed, 'smoothed_temp', 0.) or 0.) \
            < target - self.bed_tolerance

    def _filter_pwm(self, read_time, value):
        if self.fault:
            self.soft_start_since = None
            self.soft_start_active = False
            return 0.
        blocked = self._bed_blocks()
        self.bed_blocking = blocked and value > 0.
        if blocked or value <= 0.:
            self.soft_start_since = None
            self.soft_start_active = False
            return 0.
        if self.soft_start_since is None:
            self.soft_start_since = read_time
        if read_time - self.soft_start_since < self.soft_start_time:
            self.soft_start_active = True
            return value * self.soft_start_power
        self.soft_start_active = False
        return value

    # ------------------------------------------------------------------
    # Lüfter-Überwachung (Reactor-Timer)
    # ------------------------------------------------------------------
    def _rpm(self, eventtime):
        try:
            return self.fan.fan.tachometer.get_status(eventtime)['rpm']
        except Exception as exc:
            logging.exception("k2_chamber: Drehzahl nicht lesbar")
            self.konsole.warnung(
                "PTC-Lüfter-Drehzahl nicht lesbar (%s: %s) – zählt als "
                "Stillstand, tachometer_pin prüfen" % (type(exc).__name__, exc),
                schluessel="rpm", pause=600., log=False)
            return None

    def _heater_active(self):
        h = self.heater
        return (getattr(h, 'target_temp', 0.) or 0.) > 0. \
            or (getattr(h, 'last_pwm_value', 0.) or 0.) > 0.

    def _check_event(self, eventtime):
        try:
            return self._check(eventtime)
        except Exception as exc:
            # nie aus dem Timer werfen (sonst Klipper-Shutdown) – stattdessen
            # sicher sperren wie bei einem Lüfterfehler
            logging.exception("k2_chamber: Überwachung fehlgeschlagen")
            first = not self.fault
            self.fault = True
            self.stall_since = None
            if first:
                self.fault_count += 1
                self.fault_msg = ("Überwachungsfehler (%s: %s) – Kammerheizung "
                                  "AUS" % (type(exc).__name__, exc))
            try:
                self._set_target_zero()
            except Exception:
                logging.exception("k2_chamber: Ziel 0 nach Fehler")
            self.konsole.fehler(
                "Lüfter-Überwachung fehlgeschlagen (%s: %s) – Kammerheizung "
                "gesperrt bis KTWO_CHAMBER_RESET, Details im klippy.log"
                % (type(exc).__name__, exc),
                schluessel="check", pause=600., log=False)
            return eventtime + CHECK_INTERVAL

    def _check(self, eventtime):
        if self.printer.is_shutdown():
            return self.reactor.NEVER
        rpm = self._rpm(eventtime)
        self.last_rpm = rpm
        if self.fault:
            # Sperre: neues Ziel sofort wieder auf 0
            if (getattr(self.heater, 'target_temp', 0.) or 0.) > 0.:
                self._set_target_zero()
                self._respond_error(
                    "k2_chamber: Kammerheizung gesperrt (PTC-Lüfterfehler) –"
                    " Ziel auf 0 gesetzt. Lüfter prüfen, dann "
                    "KTWO_CHAMBER_RESET")
            self.stall_since = None
            return eventtime + CHECK_INTERVAL
        stalled = rpm is None or rpm < self.min_rpm
        if self._heater_active() and stalled:
            if self.stall_since is None:
                self.stall_since = eventtime
            elif eventtime - self.stall_since >= self.fan_fault_time:
                return self._fan_fault(eventtime, rpm)
        else:
            self.stall_since = None
        return eventtime + CHECK_INTERVAL

    def _set_target_zero(self):
        pheaters = self.printer.lookup_object('heaters')
        try:
            pheaters.set_temperature(self.heater, 0.)
        except Exception:
            logging.exception("k2_chamber: set_temperature(0) fehlgeschlagen"
                              " – setze Ziel direkt")
            self.heater.target_temp = 0.

    def _respond_error(self, msg):
        # "!! [k2_chamber] …" (Konsole + logging.error, wirft nie)
        if msg.startswith("k2_chamber: "):
            msg = msg[len("k2_chamber: "):]
        self.konsole.fehler(msg)

    def _fan_fault(self, eventtime, rpm):
        stall = eventtime - self.stall_since
        self.fault = True
        self.fault_count += 1
        self.stall_since = None
        self.fault_msg = (
            "PTC-Lüfter steht seit %.0f s (rpm=%s < %.0f) bei "
            "aktiver Kammerheizung – Kammerheizung AUS (Stock key519)"
            % (stall, 'n/a' if rpm is None else '%.0f' % rpm, self.min_rpm))
        self._set_target_zero()
        msg = "k2_chamber: " + self.fault_msg
        if self.action == 'shutdown':
            logging.error(msg)
            self.printer.invoke_shutdown(msg)
            return self.reactor.NEVER
        self._respond_error(msg + " – gesperrt bis KTWO_CHAMBER_RESET")
        return eventtime + CHECK_INTERVAL

    # ------------------------------------------------------------------
    # G-Code
    # ------------------------------------------------------------------
    def get_status(self, eventtime=None):
        h = self.heater
        return {
            'fault': self.fault,
            'fault_msg': self.fault_msg or '',
            'fault_count': self.fault_count,
            'rpm': self.last_rpm,
            'min_rpm': self.min_rpm,
            'stalled_for': (0. if self.stall_since is None or eventtime is None
                            else max(0., eventtime - self.stall_since)),
            'bed_blocking': self.bed_blocking,
            'soft_start': self.soft_start_active,
            'fan_fault_action': self.action,
            'heater_target': (getattr(h, 'target_temp', 0.)
                              if h is not None else 0.),
        }

    cmd_STATUS_help = "Zustand der PTC-Kammerheizungs-Überwachung"

    def cmd_STATUS(self, gcmd):
        st = self.get_status(self.reactor.monotonic())
        fan_speed = getattr(self.fan, 'last_speed', None)
        gcmd.respond_info(
            "k2_chamber: Ziel=%.0f pwm=%.2f Lüfter-Soll=%s rpm=%s (min %.0f)"
            " Stillstand=%.0f s/%.0f s Bett-Vorrang=%s Anlauf-50%%=%s "
            "Fehler=%s Aktion=%s%s"
            % (st['heater_target'],
               getattr(self.heater, 'last_pwm_value', 0.) or 0.,
               fan_speed, st['rpm'], self.min_rpm, st['stalled_for'],
               self.fan_fault_time, st['bed_blocking'], st['soft_start'],
               st['fault'], self.action,
               (" – " + st['fault_msg']) if st['fault'] else ""))

    cmd_RESET_help = "Sperre nach PTC-Lüfterfehler aufheben (Lüfter geprüft)"

    def cmd_RESET(self, gcmd):
        was = self.fault
        self.fault = False
        self.fault_msg = None
        self.stall_since = None
        gcmd.respond_info("k2_chamber: Sperre %s" % (
            "aufgehoben – Lüfter läuft beim nächsten Heizen wieder unter "
            "Überwachung" if was else "war nicht aktiv"))

    cmd_WAIT_help = ("Auf Kammertemperatur warten (wie TEMPERATURE_WAIT, "
                     "bricht bei Lüfterfehler/Ziel 0 ab)")

    def cmd_WAIT(self, gcmd):
        minimum = gcmd.get_float('MINIMUM')
        timeout = gcmd.get_float('TIMEOUT', 0., minval=0.)
        if self.fault:
            raise gcmd.error("k2_chamber: Kammerheizung gesperrt "
                             "(PTC-Lüfterfehler) – KTWO_CHAMBER_RESET")
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.get_last_move_time()
        pheaters = self.printer.lookup_object('heaters')
        reactor = self.reactor
        eventtime = start = reactor.monotonic()
        while not self.printer.is_shutdown():
            if self.fault:
                raise gcmd.error("k2_chamber: Warten abgebrochen – %s"
                                 % (self.fault_msg,))
            temp, target = self.heater.get_temp(eventtime)
            if target <= 0.:
                raise gcmd.error("k2_chamber: Warten abgebrochen – Ziel der "
                                 "Kammerheizung ist 0")
            if temp >= minimum:
                return
            if timeout and eventtime - start >= timeout:
                raise gcmd.error("k2_chamber: Kammer erreicht %.1f °C nicht "
                                 "in %.0f s (jetzt %.1f)"
                                 % (minimum, timeout, temp))
            get_temp = getattr(pheaters, '_get_temp', None)
            if get_temp is not None:
                gcmd.respond_raw(get_temp(eventtime))
            eventtime = reactor.pause(eventtime + 1.)
        raise gcmd.error("k2_chamber: Warten abgebrochen – Klipper im "
                         "Shutdown")


def load_config(config):
    return K2Chamber(config)
