# -*- coding: utf-8 -*-
"""k2_safety.py – Sicherheits-Nachrüstungen des Creality-Hosts für
UPSTREAM-Klipper (Commit 9cb9975) auf dem K2 Plus (F008), Abschnitt
``[k2_safety]``.

ENTWURF 18.10.2026 – am Gerät UNGETESTET. Doku: README-k2_safety.md.
Grundlage: Abgleich mit Creality-klippy, Lücken L1 und L4.

L1 – ``fakeshutdown`` der Creality-MCU-FW
  Die Creality-MCU-FW (Main + Düse) meldet einen ADC außerhalb des Bereichs
  (Thermistor abgezogen/Kurzschluss) NICHT mit ``shutdown``, sondern mit der
  eigenen Antwort ``fakeshutdown clock=%u static_string_id=%hu``
  (Dictionaries: static_string 30 = ``mcu0 ADC out of range`` bzw.
  ``noz0 ADC out of range``). Die MCU läuft weiter. Der Stock-Host merkt
  sich das (CR:serialhdl.py:314-326) und schaltet bei Temperaturen < 0 oder
  > 500 alle Heizungen aus (CR:mcu.py:650-711, ``TURN_OFF_HEATERS`` :705,
  Meldung ``key509-518``). Upstream hat dafür keinen Handler und schreibt nur
  ``got {...}`` ins Log (UP:serialhdl.py ``handle_default``).
  Dieses Modul registriert für jede MCU aus ``mcus`` einen Handler (Upstream-
  API ``mcu.register_serial_response`` – nur wenn das Dictionary der MCU die
  Antwort im exakt erwarteten Format kennt, ``mcu.check_valid_response``).
  Reaktion (strenger als Stock, ohne G-Code-Queue):
    1. sofort, noch im Serial-Thread: alle Heizer-Ziele 0 (``Heater.set_temp
       (0.)`` – thread-sicher, Heater-Lock, wie TURN_OFF_HEATERS);
    2. im Reactor: ``printer.invoke_shutdown(<Meldung>)`` – Upstream-Shutdown
       schickt ``emergency_stop`` an ALLE MCUs (Ausgänge auf Shutdown-Werte).
  Abschaltbar NUR mit ``fakeshutdown_guard: False`` (dann Verhalten wie
  Upstream: nur Log-Eintrag + Warnung bei jedem Start).

L4 – Obergrenzen der Solltemperaturen (Stock ``product_param``)
  Stock begrenzt jede Sollvorgabe in ``PrinterHeaters.set_temperature``
  (CR:extras/heaters.py:614-625) auf ``[gcode_macro product_param]`` aus
  printer_params.cfg (F008: ``variable_nozzle_temp: 350``,
  ``variable_bed_temp: 120``, ``variable_chamber_temp: 60``); M140/M190
  zusätzlich auf ``max_temp - 15`` (CR:extras/heater_bed.py:47-48, Pi:
  135 - 15 = 120, gleich). Dieses Modul umhüllt bei ``klippy:connect``
  ``set_temp`` UND ``alter_target`` der drei Heizer-OBJEKTE (Instanz-
  Attribut). Damit sind ALLE Wege erfasst, die eine Sollvorgabe setzen:
  M104/M109 (Upstream extruder.py), M140/M190 (heater_bed.py),
  SET_HEATER_TEMPERATURE (heaters.py, auch M141/M191-Makros), PID_CALIBRATE
  (set_temperature + ControlAutoTune.alter_target) sowie Module, die
  ``heater.set_temp`` direkt rufen.
  Verhalten bei Überschreitung: ``over_limit: clamp`` (Default, wie Stock):
  auf die Grenze kappen + Warnung (``// [k2_safety] ...``, Log).
  ``over_limit: error``: Befehl mit Fehler abweisen (``command_error``).

G-Code: ``KTWO_SAFETY_STATUS`` (nur lesend). Status-Objekt ``k2_safety``.
Keine Ziffern in G-Code-Namen (Upstream-Parser).

Konsole (23.10.2026, k2_konsole): fakeshutdown als ``!! [k2_safety] …``
(sofort, vor dem Shutdown; nennt Heizer, deren Abschalten fehlschlug),
Kappung, fehlende optionale Heizer und ``fakeshutdown_guard: False`` als
``// [k2_safety] …`` (Start-Meldungen kurz nach „ready“). Aus dem Serial-
Thread wird NIE in die Konsole geschrieben (nicht threadsicher).
"""

import logging

try:
    from . import k2_konsole as _K2K
except ImportError:
    import k2_konsole as _K2K

FAKESHUTDOWN_FMT = "fakeshutdown clock=%u static_string_id=%hu"

# Stock-Fehlerschlüssel (CR:mcu.py:650-711): (zu kalt, zu heiß) je Fühler
STOCK_KEYS = (
    ("noz0", "extruder", "key509", "key515", "nozzle"),
    ("bed0", "heater_bed", "key510", "key516", "bed"),
    ("mcu0", "chamber_temp", "key511", "key517", "mcu"),
    ("mcu0", "mcu_temp", "key512", "key518", "mcu"),
)
# Stock-Grenzwerte der ADC-Prüfung (CR:mcu.py:650-711)
STOCK_ADC_LOW = 0.
STOCK_ADC_HIGH = 500.

OVER_LIMIT_CHOICES = {'clamp': 'clamp', 'error': 'error'}


class K2Safety:
    def __init__(self, config):
        self.printer = printer = config.get_printer()
        self.reactor = printer.get_reactor()
        self.name = config.get_name()
        # --- L1 ---
        self.guard = config.getboolean('fakeshutdown_guard', True)
        mcu_names = [m.strip() for m in
                     config.get('mcus', 'mcu, nozzle_mcu').split(',')
                     if m.strip()]
        if not mcu_names:
            raise config.error("[%s] mcus: mindestens eine MCU angeben"
                               % (self.name,))
        self.mcus = []
        for mn in mcu_names:
            objname = 'mcu' if mn == 'mcu' else 'mcu ' + mn
            mcu = printer.lookup_object(objname, None)
            if mcu is None:
                raise config.error("[%s] mcus: MCU '%s' ist nicht "
                                   "konfiguriert" % (self.name, mn))
            self.mcus.append((mn, mcu))
        # --- L4 ---
        self.over_limit = config.getchoice('over_limit', OVER_LIMIT_CHOICES,
                                           'clamp')
        # (Rolle, Heizername, Grenze, Pflicht)
        self.limit_cfg = [
            ('nozzle', config.get('nozzle_heater', 'extruder'),
             config.getfloat('max_nozzle_temp', 350., above=0.), True),
            ('bed', config.get('bed_heater', 'heater_bed'),
             config.getfloat('max_bed_temp', 120., above=0.), True),
            ('chamber', config.get('chamber_heater', 'chamber_heater'),
             config.getfloat('max_chamber_temp', 60., above=0.), False),
        ]
        # Laufzeitzustand
        self.monitored = {}      # MCU-Name -> 'aktiv' | 'kein fakeshutdown im Dict' | 'aus'
        self.limits = {}         # Heizername -> Grenze (nur gefundene)
        self.missing = []        # nicht gefundene optionale Heizer
        self.heater_objs = []    # alle Heizer (für Abschalten)
        self.triggered = False
        self.last_event = ""
        self.event_count = 0
        self.clamp_count = 0
        self.last_clamp = ""
        self._responses = []
        printer.register_event_handler("klippy:mcu_identify",
                                       self._handle_mcu_identify)
        printer.register_event_handler("klippy:connect", self._handle_connect)
        printer.register_event_handler("klippy:ready", self._handle_ready)
        gcode = printer.lookup_object('gcode')
        self.konsole = _K2K.Konsole(printer, 'k2_safety', gcode)
        gcode.register_command('KTWO_SAFETY_STATUS', self.cmd_STATUS,
                               when_not_ready=True,
                               desc=self.cmd_STATUS_help)

    # ------------------------------------------------------------------
    # L1: fakeshutdown
    # ------------------------------------------------------------------
    def _handle_mcu_identify(self):
        # Dictionary ist jetzt geladen (MCU-eigener mcu_identify-Handler ist
        # vor diesem registriert, mcu.add_printer_objects läuft zuerst).
        for mn, mcu in self.mcus:
            if not self.guard:
                self.monitored[mn] = 'aus'
                continue
            if not mcu.check_valid_response(FAKESHUTDOWN_FMT):
                # z.B. Upstream-MCU-FW: kennt kein fakeshutdown, macht echten
                # shutdown -> nichts zu überwachen
                self.monitored[mn] = 'kein fakeshutdown im Dict'
                logging.info("k2_safety: MCU '%s' kennt '%s' nicht – keine "
                             "fakeshutdown-Überwachung nötig", mn,
                             FAKESHUTDOWN_FMT)
                continue
            cb = (lambda params, mn=mn: self._on_fakeshutdown(mn, params))
            self._responses.append(
                mcu.register_serial_response(cb, FAKESHUTDOWN_FMT))
            self.monitored[mn] = 'aktiv'
        if not self.guard:
            logging.warning("k2_safety: fakeshutdown_guard: False – ADC-"
                            "Fehler der Creality-MCU-FW schalten die "
                            "Heizungen NICHT ab (nur Log 'got ...')")

    def _on_fakeshutdown(self, mcu_name, params):
        # Läuft im Serial-Hintergrund-Thread (AsyncResponseWrapper)!
        reason = params.get('static_string_id', '?')
        self.event_count += 1
        first = not self.triggered
        self.triggered = True
        # 1. sofort alle Heizer-Ziele 0 (Heater.set_temp ist thread-sicher)
        self._heaters_off()
        if not first:
            return
        logging.error("k2_safety: MCU '%s' fakeshutdown %r", mcu_name, params)
        # 2. Shutdown im Reactor (invoke_shutdown nur im Hauptthread)
        self.reactor.register_async_callback(
            (lambda e: self._do_shutdown(mcu_name, reason)))

    def _heaters_off(self):
        heaters = list(self.heater_objs)
        if not heaters:
            pheaters = self.printer.lookup_object('heaters', None)
            if pheaters is not None:
                heaters = list(getattr(pheaters, 'heaters', {}).values())
        failed = []
        for h in heaters:
            try:
                h.set_temp(0.)
            except Exception:
                # kann im Serial-Thread laufen -> hier nur Log; die Konsole
                # meldet _do_shutdown (Reactor) mit der Liste
                logging.exception("k2_safety: Heizer aus fehlgeschlagen")
                failed.append(getattr(h, 'name', None)
                              or getattr(h, 'short_name', None) or '?')
        return failed

    def _stock_keys(self, reason):
        # Zuordnung wie CR:mcu.py:650-711 (nur Anzeige, Abschaltung gilt immer)
        out = []
        eventtime = self.reactor.monotonic()
        for tag, obj, k_low, k_high, label in STOCK_KEYS:
            if not reason.startswith(tag):
                continue
            temp = self._read_temp(obj, eventtime)
            if temp is None:
                continue
            if temp < STOCK_ADC_LOW:
                out.append("%s %s %s_temp:%.2f" % (k_low, label, obj, temp))
            elif temp > STOCK_ADC_HIGH:
                out.append("%s %s %s_temp:%.2f" % (k_high, label, obj, temp))
        return out

    def _read_temp(self, obj, eventtime):
        names = [obj, 'temperature_sensor ' + obj]
        for n in names:
            o = self.printer.lookup_object(n, None)
            if o is None:
                continue
            try:
                st = o.get_status(eventtime)
                return float(st.get('temperature'))
            except Exception:
                return None
        return None

    def _do_shutdown(self, mcu_name, reason):
        failed = self._heaters_off()
        keys = self._stock_keys(reason)
        msg = ("k2_safety: MCU '%s' meldet fakeshutdown \"%s\" (Creality-MCU-"
               "FW: Thermistor/ADC außerhalb des Bereichs; Stock-Host: "
               "TURN_OFF_HEATERS + key509-518, CR:serialhdl.py:322-326, "
               "CR:mcu.py:650-711)%s – alle Heizungen aus, Shutdown. "
               "Fühler/Stecker prüfen, dann FIRMWARE_RESTART."
               % (mcu_name, reason,
                  (" [" + "; ".join(keys) + "]") if keys else ""))
        if failed:
            msg += (" ACHTUNG: Abschalten fehlgeschlagen für %s – Netzschalter!"
                    % (", ".join(failed),))
        self.last_event = msg
        # "!! [k2_safety] …" sofort (nach dem Shutdown kommt kein ready mehr)
        self.konsole.fehler(msg[len("k2_safety: "):], sofort=True, log=False)
        self.printer.invoke_shutdown(msg)

    # ------------------------------------------------------------------
    # L4: Solltemperatur-Grenzen
    # ------------------------------------------------------------------
    def _handle_connect(self):
        pheaters = self.printer.lookup_object('heaters')
        self.heater_objs = list(getattr(pheaters, 'heaters', {}).values())
        for role, hname, limit, required in self.limit_cfg:
            heater = getattr(pheaters, 'heaters', {}).get(hname)
            if heater is None:
                if required:
                    raise self.printer.config_error(
                        "[%s] %s_heater: Heizer '%s' nicht gefunden"
                        % (self.name, role, hname))
                self.missing.append(hname)
                self.konsole.warnung("Heizer '%s' fehlt – keine Grenze %.0f"
                                     % (hname, limit))
                continue
            hmax = getattr(heater, 'max_temp', None)
            if hmax is not None and limit > hmax:
                logging.info("k2_safety: Grenze %s %.1f > max_temp %.1f – "
                             "Upstream-max_temp greift zuerst", hname,
                             limit, hmax)
            self._wrap_heater(heater, hname, limit)
            self.limits[hname] = limit

    def _wrap_heater(self, heater, hname, limit):
        if getattr(heater, '_k2_safety_wrapped', False):
            return
        orig_set_temp = heater.set_temp
        orig_alter = getattr(heater, 'alter_target', None)

        def set_temp(degrees):
            degrees = self._check(hname, limit, degrees, 'set_temp')
            return orig_set_temp(degrees)
        heater.set_temp = set_temp
        if orig_alter is not None:
            def alter_target(target_temp):
                # Regelschleife (PID_CALIBRATE) – nie Fehler werfen, nur kappen
                if target_temp and target_temp > limit:
                    target_temp = limit
                return orig_alter(target_temp)
            heater.alter_target = alter_target
        heater._k2_safety_wrapped = True

    def _check(self, hname, limit, degrees, where):
        if not degrees or degrees <= limit:
            return degrees
        if self.over_limit == 'error':
            raise self.printer.command_error(
                "k2_safety: Solltemperatur %.1f für '%s' über der Grenze "
                "%.1f (Stock product_param) – abgewiesen"
                % (degrees, hname, limit))
        msg = ("k2_safety: Solltemperatur %.1f für '%s' auf %.1f gekappt "
               "(Stock product_param, CR:extras/heaters.py:614-625)"
               % (degrees, hname, limit))
        self.clamp_count += 1
        self.last_clamp = msg
        self.konsole.warnung(msg[len("k2_safety: "):])
        return limit

    def _handle_ready(self):
        if not self.guard:
            # gepuffert, erscheint kurz nach „ready“ (Log schon bei identify)
            self.konsole.warnung("WARNUNG fakeshutdown_guard: False – "
                                 "ADC-Fehler schalten die Heizungen NICHT ab",
                                 log=False)

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def get_status(self, eventtime=None):
        return {
            'fakeshutdown_guard': self.guard,
            'monitored': dict(self.monitored),
            'triggered': self.triggered,
            'event_count': self.event_count,
            'last_event': self.last_event,
            'over_limit': self.over_limit,
            'limits': dict(self.limits),
            'missing_heaters': list(self.missing),
            'clamp_count': self.clamp_count,
            'last_clamp': self.last_clamp,
        }

    cmd_STATUS_help = "k2_safety: fakeshutdown-Überwachung und Temperaturgrenzen"

    def cmd_STATUS(self, gcmd):
        lines = ["k2_safety: fakeshutdown_guard=%s" % (self.guard,)]
        for mn, _ in self.mcus:
            lines.append("  MCU %s: %s" % (mn, self.monitored.get(
                mn, 'noch nicht verbunden')))
        lines.append("  ausgelöst: %s (%d Meldungen)%s" % (
            self.triggered, self.event_count,
            (" – " + self.last_event) if self.last_event else ""))
        lines.append("  Grenzen (%s): %s" % (self.over_limit, ", ".join(
            "%s=%.0f" % (k, v) for k, v in sorted(self.limits.items()))
            or "noch nicht verbunden"))
        if self.missing:
            lines.append("  fehlende Heizer: %s" % (", ".join(self.missing),))
        lines.append("  gekappt: %d%s" % (self.clamp_count, (
            " – " + self.last_clamp) if self.last_clamp else ""))
        gcmd.respond_info("\n".join(lines))


def load_config(config):
    return K2Safety(config)
