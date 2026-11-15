# -*- coding: utf-8 -*-
"""k2_fanwatch – Tachometer-Überwachung für Hotend-Lüfter und Bauteillüfter
(K2 Plus, Upstream-Klipper auf dem Pi-Host), Abschnitt ``[k2_fanwatch]``.

Idee aus dem Projekt K2 OpenKlipper (GPLv3), kein Code übernommen.

ENTWURF 15.11.2026 – am Gerät UNGETESTET. Doku: README-k2_fanwatch.md.

Zweck
  Steht der Hotend-Lüfter, staut sich die Wärme im Kühlkörper (Filament
  wird oberhalb der Schmelzzone weich, Verstopfung). Steht der
  Bauteillüfter, leidet das Druckbild. Beide Lüfter haben einen
  Tachometer-Ausgang an der Düsen-MCU, der bisher nicht ausgewertet wird.
  Dieses Modul zählt die Tacho-Impulse (Upstream ``pulse_counter.
  FrequencyCounter``, wie ``FanTachometer`` in fan.py) und meldet einen
  stehenden Lüfter.

Regeln der Bewertung (je Lüfter, Klasse ``LuefterWaechter``)
  - Bewertet wird nur, wenn der Lüfter mindestens mit ``*_min_speed``
    angesteuert ist. Darunter ist der Zähler ungenau (die Lüfter werden
    über ihre PWM versorgt); die Drosselung des Hotend-Lüfters beim
    Antasten (0,6) liegt damit außerhalb der Bewertung.
  - Nach dem Einschalten bzw. Erhöhen gilt ``spinup_time`` Schonfrist.
  - Fehler erst, wenn die Drehzahl ``fault_time`` lang durchgehend 0 ist
    (nach dem Einschalten zählt diese Zeit erst ab dem Ende der Schonfrist).
  - Dreht der Lüfter wieder: Entwarnung. Wird er abgeschaltet oder
    gedrosselt, endet der Fehlerfall als „ungeklärt“; beim nächsten
    Einschalten wird neu geprüft (kein Verriegeln, kein Reset-Befehl).

Reaktion (``mode``)
  off     nichts (kein Zähler auf der MCU, kein Timer)
  report  rote Meldung je Fehlerfall, Wiederholung alle ``repeat_interval``,
          Entwarnung (Default – Einführungsphase)
  act     wie report, zusätzlich: im Druck ``PAUSE`` (einmal je Fehlerfall,
          nie wenn schon pausiert); beim Hotend-Lüfter außerdem das
          Extruder-Ziel auf 0 (auch außerhalb eines Drucks, solange der
          Fehlerfall andauert).
  Niemals: Motoren abschalten, Klipper-Shutdown.

Aufbau: reine Logik (``lese_optionen``, ``LuefterWaechter``) ohne Klipper
testbar; ``K2Fanwatch`` ist die dünne Klipper-Hülle (Reactor-Timer, Zähler,
Lesen der Lüfterstärke aus ``get_status`` der Lüfter-Objekte).

G-Code: ``KTWO_FANWATCH_STATUS`` (nur lesend). Status-Objekt ``k2_fanwatch``.
Keine Ziffern in G-Code-Namen (Upstream-Parser).

Python >= 3.9 (Pi-Host). Abhängigkeiten: Upstream-Klipper; k2_konsole
optional (ohne sie einfache Ausgabe über gcode.respond_raw + Log).
"""

import logging

try:
    from . import k2_konsole as _K2K
except ImportError:
    try:
        import k2_konsole as _K2K
    except ImportError:
        _K2K = None

MODUL = "k2_fanwatch"

MODE_OFF = "off"
MODE_REPORT = "report"
MODE_ACT = "act"
MODES = (MODE_OFF, MODE_REPORT, MODE_ACT)

# Zustände eines Lüfters (get_status 'state')
Z_DEAKTIVIERT = "deaktiviert"      # nicht überwacht (Pin leer, Objekt fehlt)
Z_AUS = "aus"                      # unter min_speed: keine Bewertung
Z_ANLAUF = "anlauf"                # Schonfrist nach Einschalten/Erhöhen
Z_OK = "ok"                        # dreht
Z_WARTET = "wartet"                # 0 U/min, fault_time läuft
Z_FEHLER = "fehler"                # 0 U/min länger als fault_time
Z_KEINE_MESSUNG = "keine_messung"  # Zähler liefert (noch) keine Daten

# Ereignisse aus LuefterWaechter.update()
E_FEHLER = "fehler"
E_WIEDERHOLUNG = "wiederholung"
E_ENTWARNUNG = "entwarnung"
E_UNGEKLAERT = "ungeklaert"
E_KEINE_MESSUNG = "keine_messung"

EPS = 1e-6

# Messfenster des Zählers (wie Upstream FanTachometer: 1 s)
SAMPLE_TIME = 1.0
# Im Druck läuft zuerst die PAUSE (das PAUSE-Makro merkt sich dabei die
# Drucktemperatur für RESUME), erst danach wird das Extruder-Ziel 0 gesetzt.
# Hängt die PAUSE länger als diese Zeit, wird das Ziel trotzdem 0 gesetzt.
# ANNAHME: 20 s zusätzlicher Stillstand des Hotend-Lüfters sind unkritisch
# (Hitzestau baut sich über Minuten auf).
PAUSE_VORRANG = 20.0
# Entprellung für Warnungen aus dem Timer (s)
WARN_PAUSE = 600.0

# (Option, Default, Mindestwert, Höchstwert, größer-als, ganzzahlig)
ZAHL_OPTIONEN = (
    ("tach_ppr", 2, 1, 12, None, True),
    ("tach_poll_interval", 0.0015, None, 0.05, 0., False),
    ("hotend_min_speed", 0.95, None, 1., 0., False),
    ("part_min_speed", 0.5, None, 1., 0., False),
    ("min_rpm", 0., 0., 5000., None, False),
    ("spinup_time", 5., 0., 120., None, False),
    ("fault_time", 5., 2., 600., None, False),
    ("poll_interval", 1., 0.2, 10., None, False),
    ("repeat_interval", 300., 0., 86400., None, False),
)
TEXT_OPTIONEN = (
    ("hotend_tach_pin", "nozzle_mcu:PA12"),
    ("part_tach_pin", "nozzle_mcu:PC13"),
    ("hotend_fan", "heater_fan hotend_fan"),
    ("part_fan", "output_pin fan0"),
    ("part_enable", "output_pin fan0_en"),
    ("hotend_heater", "extruder"),
)


# ----------------------------------------------------------------------
# Optionen (reine Funktionen, ohne Klipper testbar)
# ----------------------------------------------------------------------

def _fehlerklasse(config):
    return getattr(config, "error", None) or ValueError


def _abschnitt(config):
    try:
        return config.get_name()
    except Exception:
        return MODUL


def _roh(config, name):
    """Rohtext einer Option; None = Option fehlt in der Config."""
    wert = config.get(name, None)
    if wert is None:
        return None
    return ("%s" % (wert,)).strip()


def _zahl_text(wert):
    return ("%d" % wert) if float(wert) == int(wert) else ("%g" % wert)


def lese_zahl(config, name, default, minval=None, maxval=None, above=None,
              ganz=False):
    """Zahl mit Bereichsprüfung lesen; Fehler als deutsche config.error."""
    err = _fehlerklasse(config)
    wo = "[%s] Option '%s'" % (_abschnitt(config), name)
    roh = _roh(config, name)
    if roh is None:
        return default
    if roh == "":
        raise err("%s ist leer – Zahl erwartet (Default %s)"
                  % (wo, _zahl_text(default)))
    try:
        wert = int(roh) if ganz else float(roh)
    except ValueError:
        hinweis = " (Dezimalpunkt statt Komma verwenden)" if "," in roh else ""
        raise err("%s = '%s' ist keine %s%s"
                  % (wo, roh, "ganze Zahl" if ganz else "Zahl", hinweis))
    if wert != wert or wert in (float("inf"), float("-inf")):
        raise err("%s = '%s' ist keine endliche Zahl" % (wo, roh))
    if minval is not None and wert < minval:
        raise err("%s = %s ist zu klein (mindestens %s)"
                  % (wo, roh, _zahl_text(minval)))
    if above is not None and wert <= above:
        raise err("%s = %s ist zu klein (muss größer als %s sein)"
                  % (wo, roh, _zahl_text(above)))
    if maxval is not None and wert > maxval:
        raise err("%s = %s ist zu groß (höchstens %s)"
                  % (wo, roh, _zahl_text(maxval)))
    return wert


def lese_optionen(config):
    """Alle Optionen des Abschnitts lesen und prüfen -> dict."""
    err = _fehlerklasse(config)
    wo = "[%s]" % (_abschnitt(config),)
    opt = {}
    roh = _roh(config, "mode")
    mode = MODE_REPORT if roh is None else roh.lower()
    if mode not in MODES:
        raise err("%s Option 'mode' = '%s' ist ungültig – erlaubt: %s "
                  "(off = aus, report = nur melden, act = melden + PAUSE/"
                  "Extruder-Ziel 0)" % (wo, roh, ", ".join(MODES)))
    opt["mode"] = mode
    for name, default in TEXT_OPTIONEN:
        roh = _roh(config, name)
        opt[name] = default if roh is None else roh
    for name, default, minval, maxval, above, ganz in ZAHL_OPTIONEN:
        opt[name] = lese_zahl(config, name, default, minval, maxval, above,
                              ganz)
    if 0. < opt["repeat_interval"] < 10.:
        raise err("%s Option 'repeat_interval' = %s ist zu klein – 0 (keine "
                  "Wiederholung) oder mindestens 10 s"
                  % (wo, _zahl_text(opt["repeat_interval"])))
    if opt["poll_interval"] > opt["fault_time"]:
        raise err("%s Option 'poll_interval' = %s darf nicht größer sein als "
                  "'fault_time' = %s"
                  % (wo, _zahl_text(opt["poll_interval"]),
                     _zahl_text(opt["fault_time"])))
    hp, pp = opt["hotend_tach_pin"], opt["part_tach_pin"]
    if hp and pp and hp.lstrip("^~!") == pp.lstrip("^~!"):
        raise err("%s 'hotend_tach_pin' und 'part_tach_pin' nennen denselben "
                  "Pin '%s'" % (wo, hp))
    return opt


# ----------------------------------------------------------------------
# Zustandsautomat je Lüfter (rein, ohne Klipper)
# ----------------------------------------------------------------------

class LuefterWaechter:
    """Bewertet einen Lüfter aus (Zeit, angesteuerte Stärke 0..1, Drehzahl).

    ``update(now, speed, rpm)`` liefert eine Liste von Ereignissen
    (``E_FEHLER``, ``E_WIEDERHOLUNG``, ``E_ENTWARNUNG``, ``E_UNGEKLAERT``,
    ``E_KEINE_MESSUNG``); ``rpm=None`` heißt „keine Messung“ (wird nie als
    Stillstand gewertet). Der Zustand steht in ``state``/``fault``.
    """

    def __init__(self, min_speed, spinup_time=5., fault_time=5.,
                 repeat_interval=300., min_rpm=0.):
        self.min_speed = float(min_speed)
        self.spinup_time = float(spinup_time)
        self.fault_time = float(fault_time)
        self.repeat_interval = float(repeat_interval)
        self.min_rpm = float(min_rpm)
        self.state = Z_AUS
        self.fault = False
        self.fault_count = 0
        self.speed = 0.
        self.rpm = None
        self.fault_since = None      # Beginn des laufenden Fehlerfalls
        self.last_fault_time = None  # Beginn des letzten Fehlerfalls
        self.last_stall = 0.         # Stillstandsdauer beim letzten Ereignis
        self._on_since = None        # seit wann >= min_speed angesteuert
        self._start_ende = None      # Ende der Schonfrist des Einschaltens
        self._schon_bis = None       # keine Bewertung vor diesem Zeitpunkt
        self._null_seit = None       # seit wann durchgehend 0 U/min
        self._letzte_meldung = None
        self._keine_messung_gemeldet = False

    def _ruhe(self):
        self._on_since = None
        self._start_ende = None
        self._schon_bis = None
        self._null_seit = None
        self._keine_messung_gemeldet = False

    def stalled_for(self, now):
        """Sekunden durchgehend 0 U/min (0, wenn nicht im Stillstand)."""
        if self._null_seit is None or now is None:
            return 0.
        return max(0., now - self._null_seit)

    def update(self, now, speed, rpm):
        ev = []
        try:
            speed = max(0., min(1., float(speed)))
        except (TypeError, ValueError):
            speed = 0.
        vorher = self.speed
        self.speed = speed
        self.rpm = rpm
        if speed < self.min_speed - EPS:
            # nicht (ausreichend) angesteuert: keine Bewertung möglich
            if self.fault:
                self.fault = False
                self.fault_since = None
                ev.append(E_UNGEKLAERT)
            self._ruhe()
            self.state = Z_AUS
            return ev
        if self._on_since is None:
            self._on_since = now
            self._start_ende = now + self.spinup_time
            self._schon_bis = self._start_ende
        elif speed > vorher + EPS:
            # im Betrieb erhöht: Bewertung aussetzen, Stillstandszeit läuft
            self._schon_bis = max(self._schon_bis, now + self.spinup_time)
        if rpm is None:
            self._null_seit = None
            if not self.fault:
                self.state = Z_KEINE_MESSUNG
                if (not self._keine_messung_gemeldet and now - self._on_since
                        >= self.spinup_time + self.fault_time):
                    self._keine_messung_gemeldet = True
                    ev.append(E_KEINE_MESSUNG)
            return ev
        if rpm > self.min_rpm:
            if self.fault:
                self.last_stall = (now - self.fault_since
                                   if self.fault_since is not None else 0.)
                self.fault = False
                self.fault_since = None
                ev.append(E_ENTWARNUNG)
            self._null_seit = None
            self.state = Z_OK
            return ev
        # Stillstand gemessen
        if self._null_seit is None:
            self._null_seit = now
        if self.fault:
            self.state = Z_FEHLER
            self.last_stall = now - self._null_seit
            if (self.repeat_interval > 0. and now - self._letzte_meldung
                    >= self.repeat_interval - EPS):
                self._letzte_meldung = now
                ev.append(E_WIEDERHOLUNG)
            return ev
        if now < self._schon_bis - EPS:
            self.state = Z_ANLAUF
            return ev
        basis = max(self._null_seit, self._start_ende)
        if now - basis >= self.fault_time - EPS:
            self.fault = True
            self.fault_count += 1
            self.fault_since = now
            self.last_fault_time = now
            self.last_stall = now - self._null_seit
            self._letzte_meldung = now
            self.state = Z_FEHLER
            ev.append(E_FEHLER)
        else:
            self.state = Z_WARTET
        return ev


# ----------------------------------------------------------------------
# Hilfen der Klipper-Hülle
# ----------------------------------------------------------------------

class _EinfacheKonsole:
    """Ersatz, wenn k2_konsole fehlt: ``!! …``/``// …`` + klippy.log.
    Wirft nie."""

    def __init__(self, printer, modul, gcode=None):
        self.printer = printer
        self.modul = modul
        self._gcode = gcode

    def _aus(self, prefix, logfn, text):
        zeile = "[%s] %s" % (self.modul, ("%s" % (text,)).strip())
        try:
            logfn(zeile)
        except Exception:
            pass
        try:
            gcode = self._gcode
            if gcode is None:
                gcode = self.printer.lookup_object("gcode", None)
            if gcode is not None:
                gcode.respond_raw(prefix + zeile)
                return True
        except Exception:
            pass
        return False

    def fehler(self, text, **kw):
        return self._aus("!! ", logging.error, text)

    def warnung(self, text, **kw):
        return self._aus("// ", logging.warning, text)

    def info(self, text, **kw):
        return self._aus("// ", logging.info, text)


def neue_konsole(printer, gcode=None):
    if _K2K is not None:
        try:
            return _K2K.Konsole(printer, MODUL, gcode)
        except Exception:
            logging.exception("[%s] k2_konsole nicht nutzbar – einfache "
                              "Ausgabe" % (MODUL,))
    return _EinfacheKonsole(printer, MODUL, gcode)


def neuer_zaehler(printer, pin, sample_time, poll_time):
    """Upstream-Frequenzzähler (config_counter/query_counter/counter_state)."""
    try:
        from . import pulse_counter
    except ImportError:
        import pulse_counter
    return pulse_counter.FrequencyCounter(printer, pin, sample_time,
                                          poll_time)


class Kanal:
    """Ein überwachter Lüfter: Config, Zähler, Lüfter-Objekt, Wächter."""

    def __init__(self, key, titel, tach_pin, objekt_name, waechter):
        self.key = key                  # 'hotend' / 'part'
        self.titel = titel              # Anzeigename in Meldungen
        self.tach_pin = tach_pin
        self.objekt_name = objekt_name
        self.waechter = waechter
        self.aktiv = True
        self.grund = ""                 # warum nicht überwacht
        self.zaehler = None
        self.objekt = None
        self.freigabe = None            # optionales Freigabe-Objekt (fan0_en)
        self.pause_ausgeloest = False   # PAUSE im Fehlerfall angefordert
        self.rpm = None
        self.speed = 0.

    def abschalten(self, grund):
        self.aktiv = False
        self.grund = grund
        self.waechter.state = Z_DEAKTIVIERT


# ----------------------------------------------------------------------
# Klipper-Hülle
# ----------------------------------------------------------------------

class K2Fanwatch:
    def __init__(self, config):
        self.printer = printer = config.get_printer()
        self.reactor = printer.get_reactor()
        self.name = config.get_name()
        self.opt = opt = lese_optionen(config)
        self.mode = opt["mode"]
        self.ppr = opt["tach_ppr"]
        self.poll_interval = opt["poll_interval"]
        self.gcode = printer.lookup_object("gcode")
        self.konsole = neue_konsole(printer, self.gcode)
        self.heater = None
        self.timer = None
        self.pausen = 0               # ausgelöste PAUSEn
        self.ziel_geloescht = 0       # wie oft das Extruder-Ziel 0 gesetzt
        self.letztes_ziel = 0.        # letztes gelöschtes Extruder-Ziel
        self.letzte_meldung = ""
        self._pause_seit = None       # PAUSE angefordert, noch nicht fertig
        self._warn_zeit = {}          # Entprellung der Timer-Warnungen
        self.kanaele = []
        for key, titel, pin_opt, fan_opt, min_opt in (
                ("hotend", "Hotend-Lüfter", "hotend_tach_pin", "hotend_fan",
                 "hotend_min_speed"),
                ("part", "Bauteillüfter", "part_tach_pin", "part_fan",
                 "part_min_speed")):
            waechter = LuefterWaechter(
                opt[min_opt], opt["spinup_time"], opt["fault_time"],
                opt["repeat_interval"], opt["min_rpm"])
            kanal = Kanal(key, titel, opt[pin_opt], opt[fan_opt], waechter)
            if self.mode == MODE_OFF:
                kanal.abschalten("mode: off")
            elif not kanal.tach_pin:
                kanal.abschalten("%s ist leer" % (pin_opt,))
            elif not kanal.objekt_name:
                kanal.abschalten("%s ist leer" % (fan_opt,))
            else:
                # Zähler in der Config-Phase anlegen (MCU-Konfiguration)
                try:
                    kanal.zaehler = neuer_zaehler(
                        printer, kanal.tach_pin, SAMPLE_TIME,
                        opt["tach_poll_interval"])
                except Exception as exc:
                    raise config.error(
                        "[%s] %s = '%s': Tachometer-Zähler lässt sich nicht "
                        "anlegen (%s: %s) – Pin-Schreibweise prüfen (z. B. "
                        "nozzle_mcu:PA12) oder Wert leer lassen"
                        % (self.name, pin_opt, kanal.tach_pin,
                           type(exc).__name__, exc))
            self.kanaele.append(kanal)
            setattr(self, key, kanal)
        printer.register_event_handler("klippy:connect", self._handle_connect)
        printer.register_event_handler("klippy:ready", self._handle_ready)
        printer.register_event_handler("klippy:shutdown",
                                       self._handle_shutdown)
        self.gcode.register_command("KTWO_FANWATCH_STATUS", self.cmd_STATUS,
                                    desc=self.cmd_STATUS_help)

    # ------------------------------------------------------------------
    # Verdrahtung
    # ------------------------------------------------------------------
    def _handle_connect(self):
        if self.mode == MODE_OFF:
            return
        for kanal in self.kanaele:
            if not kanal.aktiv:
                continue
            obj = self.printer.lookup_object(kanal.objekt_name, None)
            if obj is None or not hasattr(obj, "get_status"):
                kanal.abschalten("Abschnitt [%s] fehlt" % (kanal.objekt_name,))
                self._melde_warnung(
                    "%s: Abschnitt [%s] nicht gefunden – dieser Lüfter wird "
                    "NICHT überwacht" % (kanal.titel, kanal.objekt_name))
                continue
            kanal.objekt = obj
        part = self.part
        freigabe = self.opt["part_enable"]
        if part.aktiv and freigabe:
            # fehlt das Freigabe-Objekt, zählt allein die Stärke
            part.freigabe = self.printer.lookup_object(freigabe, None)
        if self.mode == MODE_ACT and self.hotend.aktiv:
            name = self.opt["hotend_heater"]
            try:
                self.heater = self.printer.lookup_object(
                    "heaters").lookup_heater(name)
            except Exception as exc:
                self.heater = None
                self._melde_warnung(
                    "hotend_heater '%s' nicht gefunden (%s) – bei einem "
                    "Hotend-Lüfterfehler kann das Extruder-Ziel NICHT auf 0 "
                    "gesetzt werden" % (name, exc))

    def _handle_ready(self):
        if self.mode == MODE_OFF:
            return
        if not any(k.aktiv for k in self.kanaele):
            return
        self.timer = self.reactor.register_timer(
            self._poll, self.reactor.monotonic() + self.poll_interval)

    def _handle_shutdown(self):
        if self.timer is not None:
            try:
                self.reactor.update_timer(self.timer, self.reactor.NEVER)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Meldungen
    # ------------------------------------------------------------------
    def _melde_fehler(self, text):
        self.letzte_meldung = text
        self.konsole.fehler(text)

    def _melde_warnung(self, text, schluessel=None, eventtime=None):
        if schluessel is not None and eventtime is not None:
            letzte = self._warn_zeit.get(schluessel)
            if letzte is not None and eventtime - letzte < WARN_PAUSE:
                return
            self._warn_zeit[schluessel] = eventtime
        self.konsole.warnung(text)

    def _melde_info(self, text):
        self.konsole.info(text)

    # ------------------------------------------------------------------
    # Messwerte
    # ------------------------------------------------------------------
    def _staerke(self, kanal, eventtime):
        """Angesteuerte Stärke 0..1 aus get_status des Lüfter-Objekts:
        heater_fan/fan -> 'speed', output_pin -> 'value'. Nicht lesbar ->
        0 (keine Bewertung) + entprellte Warnung."""
        try:
            status = kanal.objekt.get_status(eventtime)
            schluessel = (("value", "speed")
                          if kanal.objekt_name.startswith("output_pin")
                          else ("speed", "value"))
            wert = None
            for s in schluessel:
                if status.get(s) is not None:
                    wert = float(status[s])
                    break
            if wert is None:
                raise KeyError("weder 'speed' noch 'value' im Status")
            if kanal.freigabe is not None:
                frei = kanal.freigabe.get_status(eventtime).get("value")
                if frei is not None and float(frei) <= 0.:
                    wert = 0.
            return wert
        except Exception as exc:
            self._melde_warnung(
                "%s: Stärke von [%s] nicht lesbar (%s: %s) – keine Bewertung"
                % (kanal.titel, kanal.objekt_name, type(exc).__name__, exc),
                schluessel="staerke-" + kanal.key, eventtime=eventtime)
            return 0.

    def _rpm(self, kanal, eventtime):
        """Drehzahl in U/min; None = (noch) keine Zählerdaten."""
        try:
            zaehler = kanal.zaehler
            # ANNAHME: Upstream FrequencyCounter hält _last_time = None, bis
            # die MCU die erste counter_state-Antwort geschickt hat. Fehlt
            # das Attribut (andere Version), gilt der Zähler als lesbar.
            if getattr(zaehler, "_last_time", 0.) is None:
                return None
            return float(zaehler.get_frequency()) * 30. / self.ppr
        except Exception as exc:
            self._melde_warnung(
                "%s: Drehzahl nicht lesbar (%s: %s) – keine Bewertung"
                % (kanal.titel, type(exc).__name__, exc),
                schluessel="rpm-" + kanal.key, eventtime=eventtime)
            return None

    # ------------------------------------------------------------------
    # Überwachung (Reactor-Timer)
    # ------------------------------------------------------------------
    def _poll(self, eventtime):
        try:
            if self.printer.is_shutdown():
                return self.reactor.NEVER
            self._pruefen(eventtime)
        except Exception as exc:
            # nie aus dem Timer werfen (sonst Klipper-Shutdown)
            logging.exception("[%s] Überwachung fehlgeschlagen" % (MODUL,))
            self._melde_warnung(
                "Überwachung fehlgeschlagen (%s: %s) – Details im klippy.log"
                % (type(exc).__name__, exc),
                schluessel="poll", eventtime=eventtime)
        return eventtime + self.poll_interval

    def _pruefen(self, eventtime):
        for kanal in self.kanaele:
            if not kanal.aktiv:
                continue
            kanal.speed = self._staerke(kanal, eventtime)
            kanal.rpm = self._rpm(kanal, eventtime)
            for ereignis in kanal.waechter.update(eventtime, kanal.speed,
                                                  kanal.rpm):
                self._ereignis(kanal, ereignis, eventtime)
            if self.mode == MODE_ACT and kanal.waechter.fault:
                self._handeln(kanal, eventtime)

    def _ereignis(self, kanal, ereignis, eventtime):
        w = kanal.waechter
        prozent = kanal.speed * 100.
        if ereignis == E_FEHLER:
            kanal.pause_ausgeloest = False
            if self.mode == MODE_ACT:
                folge = ("im Druck folgt PAUSE"
                         + (", Extruder-Ziel wird 0 gesetzt"
                            if kanal.key == "hotend" else ""))
            else:
                folge = "nur Meldung (mode: report)"
            self._melde_fehler(
                "%s STEHT: 0 U/min seit %.0f s bei Ansteuerung %.0f %% – %s. "
                "Lüfter, Stecker und Tacho-Leitung prüfen"
                % (kanal.titel, w.last_stall, prozent, folge))
        elif ereignis == E_WIEDERHOLUNG:
            self._melde_fehler(
                "%s steht weiterhin: 0 U/min seit %.0f s bei Ansteuerung "
                "%.0f %%" % (kanal.titel, w.last_stall, prozent))
        elif ereignis == E_ENTWARNUNG:
            kanal.pause_ausgeloest = False
            self._melde_info(
                "%s dreht wieder (%.0f U/min) – Fehler nach %.0f s aufgehoben"
                % (kanal.titel, kanal.rpm or 0., w.last_stall))
        elif ereignis == E_UNGEKLAERT:
            kanal.pause_ausgeloest = False
            self._melde_warnung(
                "%s wird nicht mehr ausreichend angesteuert (%.0f %%) – der "
                "Fehler bleibt ungeklärt und wird beim nächsten Einschalten "
                "neu geprüft" % (kanal.titel, prozent))
        elif ereignis == E_KEINE_MESSUNG:
            self._melde_warnung(
                "%s: der Tachometer-Zähler (%s) liefert keine Daten – keine "
                "Überwachung möglich. Pin und MCU-Firmware prüfen"
                % (kanal.titel, kanal.tach_pin))

    # ------------------------------------------------------------------
    # Reaktion im Modus act
    # ------------------------------------------------------------------
    def _druck_laeuft(self, eventtime):
        """True nur bei print_stats 'printing' und nicht pausiert."""
        stats = self.printer.lookup_object("print_stats", None)
        pause = self.printer.lookup_object("pause_resume", None)
        if stats is None or pause is None:
            return False
        if stats.get_status(eventtime).get("state") != "printing":
            return False
        return not pause.get_status(eventtime).get("is_paused", False)

    def _handeln(self, kanal, eventtime):
        if not kanal.pause_ausgeloest and self._druck_laeuft(eventtime):
            kanal.pause_ausgeloest = True
            self._pause_anfordern(kanal, eventtime)
        if kanal.key != "hotend":
            return
        if (self._pause_seit is not None
                and eventtime - self._pause_seit < PAUSE_VORRANG):
            return   # PAUSE läuft: Ziel 0 folgt direkt danach
        self._ziel_null(eventtime)

    def _pause_anfordern(self, kanal, eventtime):
        if self._pause_seit is not None:
            return   # eine PAUSE ist schon unterwegs (anderer Lüfter)
        self._pause_seit = eventtime
        self._melde_fehler("%s steht – Druck wird pausiert (PAUSE)"
                           % (kanal.titel,))
        # eigener Reactor-Callback: der Überwachungs-Timer läuft weiter,
        # während PAUSE auf die G-Code-Warteschlange wartet
        register = getattr(self.reactor, "register_callback", None)
        if register is not None:
            register(self._pause_ausfuehren)
        else:
            self._pause_ausfuehren(eventtime)

    def _pause_ausfuehren(self, eventtime):
        try:
            if self._druck_laeuft(self.reactor.monotonic()):
                self.gcode.run_script("PAUSE")
                self.pausen += 1
        except Exception as exc:
            logging.exception("[%s] PAUSE fehlgeschlagen" % (MODUL,))
            self._melde_fehler(
                "PAUSE fehlgeschlagen (%s: %s) – Druck von Hand anhalten"
                % (type(exc).__name__, exc))
        finally:
            self._pause_seit = None
        try:
            if self.hotend.waechter.fault:
                self._ziel_null(self.reactor.monotonic())
        except Exception:
            logging.exception("[%s] Extruder-Ziel 0 nach PAUSE" % (MODUL,))

    def _ziel_null(self, eventtime):
        """Extruder-Ziel > 0 -> 0 (Hotend-Lüfter steht). True = gesetzt."""
        heater = self.heater
        if heater is None:
            return False
        ziel = float(heater.get_status(eventtime).get("target", 0.) or 0.)
        if ziel <= 0.:
            return False
        heater.set_temp(0.)
        self.ziel_geloescht += 1
        self.letztes_ziel = ziel
        self._melde_fehler(
            "Hotend-Lüfter steht – Extruder-Ziel %.0f °C auf 0 gesetzt "
            "(Schutz vor Hitzestau). Erst den Lüfter prüfen, dann neu heizen"
            % (ziel,))
        return True

    # ------------------------------------------------------------------
    # Status / G-Code
    # ------------------------------------------------------------------
    def _kanal_status(self, kanal, eventtime):
        w = kanal.waechter
        return {
            "enabled": kanal.aktiv,
            "reason": kanal.grund,
            "rpm": kanal.rpm,
            "speed": kanal.speed,
            "state": w.state,
            "fault": w.fault,
            "fault_count": w.fault_count,
            "stalled_for": w.stalled_for(eventtime),
            "min_speed": w.min_speed,
            "tach_pin": kanal.tach_pin,
        }

    def get_status(self, eventtime=None):
        hotend = self._kanal_status(self.hotend, eventtime)
        part = self._kanal_status(self.part, eventtime)
        return {
            "mode": self.mode,
            "fault": bool(hotend["fault"] or part["fault"]),
            "hotend": hotend,
            "part": part,
            "pause_count": self.pausen,
            "heater_off_count": self.ziel_geloescht,
            "last_heater_target": self.letztes_ziel,
            "last_message": self.letzte_meldung,
        }

    cmd_STATUS_help = ("Stand der Lüfter-Überwachung (Hotend- und "
                       "Bauteillüfter)")

    def cmd_STATUS(self, gcmd):
        st = self.get_status(self.reactor.monotonic())
        zeilen = ["[%s] Modus %s%s" % (
            MODUL, st["mode"],
            {MODE_OFF: " (aus)", MODE_REPORT: " (nur melden)",
             MODE_ACT: " (melden + PAUSE/Extruder-Ziel 0)"}[st["mode"]])]
        for kanal in self.kanaele:
            ks = st[kanal.key]
            if not ks["enabled"]:
                zeilen.append("[%s] %s: nicht überwacht (%s)"
                              % (MODUL, kanal.titel, ks["reason"]))
                continue
            rpm = ("keine Messung" if ks["rpm"] is None
                   else "%.0f U/min" % (ks["rpm"],))
            zeilen.append(
                "[%s] %s: Zustand %s, %s, Ansteuerung %.0f %% (Prüfung ab "
                "%.0f %%), Stillstand %.0f s, Fehler %s (bisher %d), Tacho %s"
                % (MODUL, kanal.titel, ks["state"], rpm, ks["speed"] * 100.,
                   ks["min_speed"] * 100., ks["stalled_for"],
                   "JA" if ks["fault"] else "nein", ks["fault_count"],
                   ks["tach_pin"]))
        if st["mode"] == MODE_ACT:
            zeilen.append("[%s] ausgelöste PAUSEn: %d, Extruder-Ziel 0 "
                          "gesetzt: %d×" % (MODUL, st["pause_count"],
                                            st["heater_off_count"]))
        gcmd.respond_info("\n".join(zeilen))


def load_config(config):
    return K2Fanwatch(config)
