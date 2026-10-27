#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_konsole.py – Warn- und Fehlermeldungen der K2-Module in der Konsole.

Kein Klipper-Abschnitt (kein ``load_config``): Hilfsbibliothek, die jedes
K2-Modul selbst einbindet. Ziel (Konsolen-Audit 23.10.2026): JEDE Warnung
und jeder Fehler eines K2-Moduls erscheint auch in der Mainsail-Konsole –
nicht nur im klippy.log.

Format (wie Klipper selbst):
- Fehler:   ``!! [modul] …``  (Mainsail zeigt die Zeile rot)
- Warnung:  ``// [modul] …``
- Hinweis:  ``// [modul] …``  (``stufe=INFO``, z. B. Startmeldungen)

Regeln:
- ``melde()`` wirft NIE (auch nicht ohne gcode-Objekt, im Timer, im Test).
- Vor ``klippy:ready`` (Connect-Phase) gehen Ausgaben meist ins Leere, weil
  Moonraker die Konsole erst danach abonniert → Meldungen werden gepuffert
  und ``FLUSH_DELAY`` s nach ``klippy:ready`` ausgegeben (höchstens
  ``MAX_PUFFER``; Überlauf wird gezählt und gemeldet).
- Entprellung: ``schluessel`` + ``pause`` (s) → dieselbe Meldung höchstens
  einmal je ``pause``; unterdrückte Wiederholungen werden beim nächsten
  Durchlass als „(N× seit der letzten Meldung)“ angehängt. Für Timer-/
  Poll-Pfade Pflicht (sonst flutet ein Dauerfehler die Konsole).
- Log: ``melde()`` schreibt selbst ins klippy.log (Fehler → ``logging.error``,
  Warnung → ``logging.warning``, Hinweis → ``logging.info``), außer
  ``log=False`` (wenn die Stelle schon ``logging.exception`` mit Traceback
  schreibt).
- ``sofort=True``: nicht puffern (Shutdown-Pfade – nach einem Shutdown kommt
  kein ``klippy:ready`` mehr; Klipper zeigt den Shutdown-Grund ohnehin).
- Nur aus dem Reactor (Hauptthread) aufrufen – NICHT aus Serial-/Hintergrund-
  Threads (gcode.respond_* ist nicht threadsicher).

Ohne Klipper importierbar (Tests: ``python3 test_k2_konsole.py``).
Python-3.9-kompatibel.
"""

import logging
import time

FEHLER = "fehler"
WARNUNG = "warnung"
INFO = "info"

FLUSH_DELAY = 5.0      # s nach klippy:ready (Moonraker abonniert die Ausgabe)
MAX_PUFFER = 50        # Meldungen vor ready
STANDARD_PAUSE = 300.  # Entprellung, wenn nur ein Schlüssel angegeben ist


def _jetzt(printer):
    try:
        return printer.get_reactor().monotonic()
    except Exception:
        return time.monotonic()


def format_zeilen(modul, text, stufe=WARNUNG):
    """Text -> Liste fertiger Konsolenzeilen (``!! …`` bzw. ``// …``)."""
    text = ("%s" % (text,)).strip() or "(leere Meldung)"
    tag = "[%s]" % (modul,)
    if not text.startswith(tag):
        text = "%s %s" % (tag, text)
    prefix = "!! " if stufe == FEHLER else "// "
    return [prefix + z.strip() for z in text.split("\n") if z.strip()]


class Konsole:
    """Eine Instanz je Modul: ``Konsole(printer, "k2_box").melde(text, …)``."""

    def __init__(self, printer, modul, gcode=None):
        self.printer = printer
        self.modul = modul
        self._gcode = gcode
        self._bereit = False
        self._flush_geplant = False
        self._puffer = []
        self._verworfen = 0
        self._zuletzt = {}        # schluessel -> Zeit der letzten Ausgabe
        self._unterdrueckt = {}   # schluessel -> Anzahl seitdem
        self.gesendet = 0         # Zähler (Diagnose/Tests)
        # Erst nach „ready“ angelegt (z. B. faul im Betrieb): sofort ausgeben.
        # Ohne Zustands-/Ereignis-API (kein Klipper, Offline-Test mit
        # Attrappe): ebenfalls sofort – wie vor dem Audit.
        try:
            kategorie = printer.get_state_message()[1]
        except Exception:
            kategorie = None
        if kategorie is None or kategorie == "ready":
            self._bereit = True
        else:
            try:
                printer.register_event_handler("klippy:ready",
                                               self._handle_ready)
            except Exception:
                self._bereit = True

    # --- Zustand ------------------------------------------------------
    def _ist_bereit(self):
        return self._bereit

    def _handle_ready(self):
        self._flush_geplant = True
        try:
            reactor = self.printer.get_reactor()
            reactor.register_timer(self._flush_timer,
                                   reactor.monotonic() + FLUSH_DELAY)
        except Exception:
            self.flush()

    def _flush_timer(self, eventtime):
        self.flush()
        try:
            return self.printer.get_reactor().NEVER
        except Exception:
            return 9e99

    def flush(self):
        """Puffer ausgeben (nach ready) – wirft nie."""
        self._bereit = True
        self._flush_geplant = False
        puffer, self._puffer = self._puffer, []
        if self._verworfen:
            puffer.append((WARNUNG, "%d ältere Meldungen vor dem Start "
                                    "verworfen (siehe klippy.log)"
                           % (self._verworfen,)))
            self._verworfen = 0
        for stufe, text in puffer:
            self._ausgeben(stufe, text)

    # --- Ausgabe ------------------------------------------------------
    def _gcode_obj(self):
        if self._gcode is None:
            try:
                self._gcode = self.printer.lookup_object("gcode", None)
            except Exception:
                self._gcode = None
        return self._gcode

    def _ausgeben(self, stufe, text):
        gcode = self._gcode_obj()
        if gcode is None:
            return False
        zeilen = format_zeilen(self.modul, text, stufe)
        try:
            if hasattr(gcode, "respond_raw"):
                for z in zeilen:
                    gcode.respond_raw(z)
            else:
                gcode.respond_info("\n".join(zeilen))
        except Exception:
            try:
                logging.exception("[%s] Konsolenausgabe fehlgeschlagen"
                                  % (self.modul,))
            except Exception:
                pass
            return False
        self.gesendet += 1
        return True

    def melde(self, text, stufe=WARNUNG, schluessel=None, pause=None,
              log=True, sofort=False):
        """Meldung ausgeben (bzw. bis ready puffern). Rückgabe: True =
        ausgegeben/gepuffert, False = entprellt/fehlgeschlagen. Wirft nie."""
        try:
            return self._melde(text, stufe, schluessel, pause, log, sofort)
        except Exception:
            try:
                logging.exception("[%s] melde() fehlgeschlagen" % (self.modul,))
            except Exception:
                pass
            return False

    def _melde(self, text, stufe, schluessel, pause, log, sofort=False):
        text = ("%s" % (text,)).strip()
        if log:
            tag = "[%s]" % (self.modul,)
            ltext = text if text.startswith(tag) else "%s %s" % (tag, text)
            {FEHLER: logging.error, WARNUNG: logging.warning}.get(
                stufe, logging.info)(ltext)
        if schluessel is not None:
            if pause is None:
                pause = STANDARD_PAUSE
            jetzt = _jetzt(self.printer)
            letzte = self._zuletzt.get(schluessel)
            if letzte is not None and jetzt - letzte < pause:
                self._unterdrueckt[schluessel] = \
                    self._unterdrueckt.get(schluessel, 0) + 1
                return False
            self._zuletzt[schluessel] = jetzt
            n = self._unterdrueckt.pop(schluessel, 0)
            if n:
                text = "%s (%d× seit der letzten Meldung)" % (text, n)
        if not self._ist_bereit() and not sofort:
            if len(self._puffer) >= MAX_PUFFER:
                self._puffer.pop(0)
                self._verworfen += 1
            self._puffer.append((stufe, text))
            return True
        return self._ausgeben(stufe, text)

    def fehler(self, text, **kw):
        return self.melde(text, FEHLER, **kw)

    def warnung(self, text, **kw):
        return self.melde(text, WARNUNG, **kw)

    def info(self, text, **kw):
        return self.melde(text, INFO, **kw)

    def zuruecksetzen(self, schluessel):
        """Entprellung für einen Schlüssel aufheben (Zustand wieder gut)."""
        self._zuletzt.pop(schluessel, None)
        self._unterdrueckt.pop(schluessel, None)


class LogAdapter:
    """``logger``-Ersatz für die Controller (K2MotorController,
    K2BoxController): ``info``/``warning``/``error`` -> klippy.log mit
    ``[modul]``. Früher bekamen sie das gcode-Objekt, das kein ``info``/
    ``error`` hat – die Aufrufe liefen still ins Leere (Audit 23.10.2026).
    Konsolenausgabe macht die Hülle selbst (aus dem Ergebnis), sonst
    doppelt."""

    def __init__(self, modul):
        self.modul = modul

    def _log(self, fn, msg):
        try:
            fn("[%s] %s" % (self.modul, msg))
        except Exception:
            pass

    def info(self, msg):
        self._log(logging.info, msg)

    def warning(self, msg):
        self._log(logging.warning, msg)

    warn = warning

    def error(self, msg):
        self._log(logging.error, msg)

    def debug(self, msg):
        self._log(logging.debug, msg)


class KonsolenLog:
    """``logger``-Ersatz, der in die Konsole schreibt (über eine Konsole):
    ``info`` mit Stufe ``info_stufe`` (z. B. WARNUNG, wenn der Controller
    seine Warnungen per ``log.info`` meldet), ``warning`` -> Warnung,
    ``error`` -> Fehler. Wirft nie."""

    def __init__(self, konsole, info_stufe=INFO):
        self.konsole = konsole
        self.info_stufe = info_stufe

    def info(self, msg):
        self.konsole.melde(msg, self.info_stufe)

    def warning(self, msg):
        self.konsole.warnung(msg)

    warn = warning

    def error(self, msg):
        self.konsole.fehler(msg)
