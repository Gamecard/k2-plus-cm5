# -*- coding: utf-8 -*-
"""k2_cfs_sensor – CFS-Box-Klima (Temperatur + relative Luftfeuchte der Luft
in der Box, NICHT des Filaments) als Klipper-Temperatursensor für Mainsail.

Quelle sind ausschließlich die Werte, die [k2_box] beim zyklischen 0x0a-Poll
ohnehin liest (Statusbytes temp_c = int8 °C, rh = %; KTWO_BOX_STATUS zeigt
„Temp=25 °C Feuchte=47 %“) – KEIN zusätzlicher Bus-Verkehr.

Config (Reihenfolge wichtig: [k2_cfs_sensor] registriert den Sensortyp und
muss deshalb VOR dem temperature_sensor-Abschnitt stehen):

    [k2_cfs_sensor]
    #box_object: k2_box   # Klipper-Objekt mit get_status() temp_c/rh
    #report_time:         # s; leer = poll_interval von [k2_box] (Default 4)
    #max_age: 60          # s ohne frischen Poll -> veraltet, keine Weitergabe

    [temperature_sensor cfs_box]
    sensor_type: k2_cfs
    min_temp: -10
    max_temp: 80

Warum ein eigenes Modul statt der Registrierung in k2_box: Der Sensortyp
muss beim Laden des temperature_sensor-Abschnitts bekannt sein. Hinge er an
[k2_box], bräche jeder Klipper-Start mit „Unknown temperature sensor“ ab,
sobald [k2_box] fehlt oder auskommentiert ist. So startet Klipper auch ohne
Box; der Sensor bleibt dann ohne Wert.

Ungültig/fehlend: Solange keine gültige Messung vorliegt (Box offline, noch
nicht gepollt, Poll älter als max_age, Wert außerhalb min_temp/max_temp
bzw. Feuchte > 100 %), wird der Klipper-Callback NICHT aufgerufen – keine
Ersatz-Temperatur. Der temperature_sensor zeigt dann seinen letzten Wert
(vor der ersten Messung Klippers Startwert 0); der Zustand steht im Status
printer["k2_cfs_sensor"].sensors.<name> (valid/reason/age). Bewusst KEIN
Shutdown bei Werten außerhalb min/max (anders als temperature_host): der
Sensor regelt nichts, ein falsch gelesenes Statusbyte soll keinen Druck
abbrechen.

Feuchte in Mainsail: Mainsail zeigt Zusatzwerte (humidity) nur für Objekte
"<typ> <name>" mit typ aus aht10/aht1x/aht2x/aht3x/bme280/htu21d/sgp40/
sht3x/temperature_combined. Deshalb registriert jeder Sensor zusätzlich das
Objekt "aht10 <name>" – ein REINER ANZEIGE-ALIAS (kein AHT10-Chip, kein
Treiber), get_status {"temperature", "humidity"}. Existiert das Objekt schon,
entfällt der Alias (nur Log).

Konsole (23.10.2026, k2_konsole): Lesefehler im Abtast-Timer und der
Übergang gültig → ungültig (Box offline/veraltet/Wert außerhalb) erscheinen
als ``// [k2_cfs_sensor] …`` in der Mainsail-Konsole, je Grund höchstens
alle 30 min. Ein Sensor, der von Anfang an keinen Wert hat (Box nicht
angeschlossen), meldet sich NICHT.
"""
import logging

try:
    from . import k2_konsole as _K2K
except ImportError:
    import k2_konsole as _K2K

KONSOLE_PAUSE = 1800.          # s: gleiche Sensor-Warnung höchstens so oft
SENSOR_TYPE = "k2_cfs"
ALIAS_PREFIX = "aht10"          # Mainsail-Anzeige-Alias, s. Docstring
REPORT_TIME_DEFAULT = 4.0       # = POLL_INTERVAL_DEFAULT in k2_box
MAX_AGE_DEFAULT = 60.0
RH_MAX = 100


class K2CfsSensor:
    """Sensor für [temperature_sensor <name>] mit sensor_type k2_cfs."""

    def __init__(self, config, registry):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.registry = registry
        self.name = config.get_name().split()[-1]
        self.min_temp = -273.15
        self.max_temp = 99999999.9
        self._callback = None
        self._timer = None
        self._last_poll = None          # Identität des letzten k2_box-Polls
        self._fresh_time = None         # eventtime des letzten frischen Polls
        self.temp = self.rh = None      # letzte gültige Werte
        self.valid = False
        self.reason = "noch keine Messung"
        self.age = None
        self.konsole = _K2K.Konsole(self.printer, "k2_cfs_sensor")
        alias = "%s %s" % (ALIAS_PREFIX, self.name)
        if self.printer.lookup_object(alias, None) is None:
            self.printer.add_object(alias, K2CfsHumidityAlias(self))
            self.alias = alias
        else:
            logging.info("k2_cfs_sensor: Objekt '%s' existiert – kein "
                         "Mainsail-Alias für die Feuchte", alias)
            self.alias = None
        registry.sensors[self.name] = self
        self.printer.register_event_handler("klippy:connect",
                                            self._handle_connect)

    # --- Klipper-Sensor-Schnittstelle -----------------------------------
    def setup_minmax(self, min_temp, max_temp):
        self.min_temp = min_temp
        self.max_temp = max_temp

    def setup_callback(self, cb):
        self._callback = cb

    def get_report_time_delta(self):
        return self.registry.report_time()

    # --- Abtastung ------------------------------------------------------
    def _handle_connect(self):
        if self.printer.get_start_args().get("debugoutput") is not None:
            return
        self._timer = self.reactor.register_timer(self._sample,
                                                  self.reactor.NOW)

    def _read_box(self, eventtime):
        """(temp_c, rh, frisch) aus [k2_box] oder Grund-Text."""
        box = self.printer.lookup_object(self.registry.box_object, None)
        if box is None:
            return None, None, "kein [%s]" % (self.registry.box_object,)
        st = box.get_status(eventtime) or {}
        poll = getattr(getattr(box, "controller", None), "last_poll", None)
        if poll is not None and poll is not self._last_poll:
            self._last_poll = poll
            self._fresh_time = eventtime
        elif poll is None and st.get("temp_c") is not None:
            # ohne Controller-Cache: jeder gelesene Wert gilt als frisch
            self._fresh_time = eventtime
        return st.get("temp_c"), st.get("rh"), None

    def _sample(self, eventtime):
        interval = self.registry.report_time()
        war_gueltig = self.valid
        try:
            self._update(eventtime)
        except Exception as exc:
            # nie aus dem Timer heraus werfen (Klipper-Shutdown)
            logging.exception("k2_cfs_sensor: Fehler beim Lesen")
            self.valid, self.reason = False, "Lesefehler (Log)"
            self.konsole.warnung(
                "Sensor %s: Lesefehler (%s: %s) – Wert ungültig, Details im "
                "klippy.log" % (self.name, type(exc).__name__, exc),
                schluessel="lesefehler", pause=KONSOLE_PAUSE, log=False)
            return eventtime + interval
        if war_gueltig and not self.valid:
            self.konsole.warnung(
                "Sensor %s: kein gültiger Wert mehr (%s) – Anzeige bleibt "
                "auf dem letzten Wert" % (self.name, self.reason),
                schluessel="ungueltig", pause=KONSOLE_PAUSE)
        return eventtime + interval

    def _update(self, eventtime):
        temp, rh, grund = self._read_box(eventtime)
        if grund:
            self.valid, self.reason = False, grund
            return
        if temp is None:
            self.valid, self.reason = False, "Box noch nicht gepollt/offline"
            return
        self.age = (None if self._fresh_time is None
                    else round(eventtime - self._fresh_time, 1))
        if self.age is not None and self.age > self.registry.max_age:
            self.valid = False
            self.reason = "veraltet (%.0f s ohne frischen Poll)" % (self.age,)
            return
        temp = float(temp)
        if not self.min_temp <= temp <= self.max_temp:
            self.valid = False
            self.reason = ("Temperatur %.0f °C außerhalb %.0f..%.0f – "
                           "ignoriert" % (temp, self.min_temp, self.max_temp))
            return
        if rh is not None and not 0 <= int(rh) <= RH_MAX:
            rh = None                   # Feuchte unplausibel, Temp bleibt
        self.temp, self.rh = temp, (None if rh is None else int(rh))
        self.valid, self.reason = True, ""
        if self._callback is not None:
            mcu = self.printer.lookup_object("mcu")
            self._callback(mcu.estimated_print_time(eventtime), temp)

    def status(self):
        return {"valid": self.valid, "reason": self.reason,
                "temperature": self.temp, "humidity": self.rh,
                "age": self.age, "alias": self.alias or ""}


class K2CfsHumidityAlias:
    """REINER Mainsail-Anzeige-Alias „aht10 <name>“ – kein AHT10-Chip."""

    def __init__(self, sensor):
        self.sensor = sensor

    def get_status(self, eventtime):
        s = self.sensor
        if not s.valid or s.temp is None:
            return {}
        out = {"temperature": round(s.temp, 2)}
        if s.rh is not None:
            out["humidity"] = s.rh
        return out


class K2CfsSensorRegistry:
    """[k2_cfs_sensor]: registriert sensor_type k2_cfs."""

    def __init__(self, config):
        self.printer = config.get_printer()
        self.box_object = config.get("box_object", "k2_box")
        self._report_time = config.getfloat("report_time", None, above=0.)
        self.max_age = config.getfloat("max_age", MAX_AGE_DEFAULT, above=0.)
        self.sensors = {}
        pheaters = self.printer.load_object(config, "heaters")
        pheaters.add_sensor_factory(SENSOR_TYPE, self._factory)

    def _factory(self, config):
        return K2CfsSensor(config, self)

    def report_time(self):
        if self._report_time:
            return self._report_time
        box = self.printer.lookup_object(self.box_object, None)
        try:
            v = float((getattr(box, "cfg", None) or {}).get(
                "poll_interval", REPORT_TIME_DEFAULT))
        except (TypeError, ValueError):
            v = REPORT_TIME_DEFAULT
        return v if v > 0 else REPORT_TIME_DEFAULT

    def get_status(self, eventtime):
        return {"sensors": {n: s.status() for n, s in self.sensors.items()}}


def load_config(config):
    return K2CfsSensorRegistry(config)
