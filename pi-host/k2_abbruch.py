#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_abbruch.py – Klipper-Zusatzmodul [k2_abbruch]: Abbruch wirkt sofort.

Anlass (13./14.11.2026): Klipper arbeitet G-Code strikt nacheinander ab.
START_PRINT ist EIN Befehl (G28, Heizen, Z-Antasten, Mesh, Reinigung –
zusammen 4–5 min). Ein Abbruch (Mainsail/Orca: Webhook
``pause_resume/cancel`` -> ``CANCEL_PRINT``) bzw. eine Pause wartet so lange
auf die G-Code-Sperre, bis START_PRINT komplett fertig ist – live 14.11.2026
08:46:45 -> 08:50:48 (das Bett wurde trotz Abbruchwunsch noch vermessen).
Ebenso blockiert jedes lange ``M109``/``M190`` im Druck (Temperaturturm).

Lösung (ohne Eingriff in Upstream-Dateien):
1. Die Webhooks ``pause_resume/cancel`` und ``pause_resume/pause`` werden
   beim Start umgehängt. Trifft eine Anfrage ein, merkt sich das Modul das
   SOFORT (ohne G-Code-Sperre) und meldet es in der Konsole; danach läuft
   der Original-Webhook wie bisher (wartet auf die Sperre).
     - Abbruch während eines laufenden Drucks -> „Abbruch offen“.
     - Pause während der Druckvorbereitung (Druckbeginn bis START_PRINT
       fertig) -> wird als ABBRUCH behandelt: Fortsetzen ginge ohnehin
       nicht (Startablauf unvollständig).
     - Pause im normalen Druck bleibt Pause (unverändert).
2. Ist ein Abbruch offen, löst die nächste ABBRUCHSTELLE einen G-Code-Fehler
   aus – das beendet START_PRINT bzw. die laufende Zeile sofort; danach
   laufen das Aufräumen (virtual_sdcard on_error_gcode = _PI_RESET_STATE) und
   der wartende CANCEL_PRINT (Schnitt/Rückzug, Bett anheben) normal.
   Abbruchstellen:
     - ``KTWO_ABBRUCH AKTION=PRUEFEN WO=…`` (Makro _PI_ABBRUCH_PUNKT) zwischen den
       Schritten von START_PRINT und vor dem G28 Z in homing_override,
     - jedes Warten auf eine Heizung (M109/M190 – check_busy der Heizungen),
     - jedes Antasten außerhalb von G28 (Mesh, Z-Tilt; Hook in k2_prtouch),
     - das Warten auf die Kammer (KTWO_CHAMBER_WAIT, Hook in k2_chamber).
   NIE abgebrochen wird mitten in einer CFS-Wechselkette (k2_box
   chain_active – deren eigenes Aufräumen soll nicht gestört werden) und
   mitten in einer G28-Fahrt (ein Fehler in G28 schaltet die Motoren ab, das
   Bett sackt ab). Der Abbruch ist EINMALIG: nach dem Auslösen ist nichts
   mehr offen, CANCEL_PRINT/_PI_RESET_STATE löschen zusätzlich
   (``KTWO_ABBRUCH AKTION=RESET``), damit deren eigenes Heizen nicht abbricht.

Befehle (Klipper: nur NAME=WERT-Parameter):
  KTWO_ABBRUCH                            Zustand anzeigen
  KTWO_ABBRUCH AKTION=PRUEFEN [WO=text]   Abbruchstelle (Fehler, wenn offen)
  KTWO_ABBRUCH AKTION=START               Druckvorbereitung beginnt
  KTWO_ABBRUCH AKTION=START_FERTIG        Druckvorbereitung fertig
  KTWO_ABBRUCH AKTION=RESET               offenen Abbruch verwerfen

Status (printer.k2_abbruch): offen, vorbereitung, letzter.
"""

import logging
import time

POLL_S = 0.5  # Druckzustand abfragen (nur zum Aufräumen alter Merker)

KIND_CANCEL = "cancel"
KIND_PAUSE = "pause"
PENDING_CANCEL = "Abbruch"
PENDING_PAUSE = "Pause im Druckstart = Abbruch"

ACTIVE_STATES = ("printing", "paused")


class AbbruchZustand:
    """Reine Logik (ohne Klipper, getestet in test_k2_abbruch.py)."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.offen = None        # PENDING_* oder None
        self.offen_seit = None
        self.vorbereitung = False  # Druckbeginn .. START_PRINT fertig
        self.letzter = None      # {"art", "wo", "nach_s"} des letzten Abbruchs
        self._state = None

    def druckzustand(self, state):
        """print_stats.state (regelmäßig). Neuer Druck (vorher nicht
        printing/paused) -> Vorbereitung beginnt. Kein aktiver Druck ->
        nichts offen, keine Vorbereitung."""
        prev = self._state
        self._state = state
        if state == "printing" and prev not in ACTIVE_STATES:
            self.vorbereitung = True
        if state not in ACTIVE_STATES:
            self.vorbereitung = False
            self.offen = None
            self.offen_seit = None

    def anfrage(self, kind, state):
        """Webhook-Anfrage. Gibt den auszuführenden Original-Webhook zurück
        (KIND_CANCEL/KIND_PAUSE) und merkt ggf. den Abbruch vor."""
        if kind == KIND_CANCEL:
            if state == "printing":
                self._merken(PENDING_CANCEL)
            return KIND_CANCEL
        if state == "printing" and self.vorbereitung:
            self._merken(PENDING_PAUSE)
            return KIND_CANCEL
        return KIND_PAUSE

    def _merken(self, art):
        if self.offen is None:
            self.offen_seit = self.clock()
        self.offen = art

    def ausloesen(self, gesperrt=False):
        """An einer Abbruchstelle: offener Abbruch -> (art, nach_s) und
        verbraucht; sonst None. gesperrt=True (CFS-Kette) -> bleibt offen."""
        if self.offen is None or gesperrt:
            return None
        art, seit = self.offen, self.offen_seit
        self.offen = None
        self.offen_seit = None
        nach = None if seit is None else max(0.0, self.clock() - seit)
        return art, nach

    def start(self):
        self.vorbereitung = True

    def start_fertig(self):
        self.vorbereitung = False

    def reset(self):
        self.offen = None
        self.offen_seit = None


def abbruch_text(art, wo, nach_s):
    return ("Druck abgebrochen (%s) bei: %s%s" % (
        art, wo or "?",
        "" if nach_s is None else " – %.0f s nach der Anfrage" % nach_s))


class K2Abbruch:
    """Klipper-Objekt [k2_abbruch]."""

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.z = AbbruchZustand()
        self.orig = {}
        self.print_stats = None
        self.k2_box = None
        self.hooks = []
        self.printer.register_event_handler("klippy:ready", self._ready)
        self.gcode.register_command(
            "KTWO_ABBRUCH", self.cmd_KTWO_ABBRUCH,
            desc="Sofort-Abbruch: ohne Parameter Zustand; AKTION=PRUEFEN "
                 "[WO=] | START | START_FERTIG | RESET")

    # --- Einhängen -----------------------------------------------------
    def _ready(self):
        self.print_stats = self.printer.lookup_object("print_stats", None)
        self.k2_box = self.printer.lookup_object("k2_box", None)
        wh = self.printer.lookup_object("webhooks")
        eps = getattr(wh, "_endpoints", None)
        if isinstance(eps, dict):
            for path, kind in (("pause_resume/cancel", KIND_CANCEL),
                               ("pause_resume/pause", KIND_PAUSE)):
                cb = eps.get(path)
                if cb is not None:
                    self.orig[kind] = cb
                    eps[path] = self._webhook(kind)
                    self.hooks.append(path)
        else:
            logging.info("k2_abbruch: webhooks._endpoints fehlt – Webhooks "
                         "nicht umgehängt")
        pheaters = self.printer.lookup_object("heaters", None)
        for name, heater in sorted(getattr(pheaters, "heaters", {}).items()):
            self._wrap_heater(name, heater)
        probe = self.printer.lookup_object("probe", None)
        core = getattr(probe, "core", None)
        if core is not None and hasattr(core, "abort_check"):
            core.abort_check = lambda: self.pruefen("Antasten")
            self.hooks.append("probe")
        self.reactor.register_timer(self._poll, self.reactor.NOW)
        logging.info("k2_abbruch: eingehängt: %s", ", ".join(self.hooks))

    def _wrap_heater(self, name, heater):
        orig = getattr(heater, "check_busy", None)
        if orig is None:
            return

        def check_busy(eventtime, _orig=orig, _name=name):
            busy = _orig(eventtime)
            if busy:
                self.pruefen("Warten auf %s" % _name)
            return busy
        heater.check_busy = check_busy
        self.hooks.append("heater %s" % name)

    def _webhook(self, kind):
        def handler(web_request):
            state = self._state()
            vorher = self.z.offen
            aktion = self.z.anfrage(kind, state)
            if self.z.offen is not None and vorher is None:
                self._melden(
                    "%s angefordert – wird beim nächsten Schritt "
                    "ausgeführt (Heizen/Antasten/Startschritt brechen sofort "
                    "ab; CFS-Wechsel und G28-Fahrt laufen noch zu Ende)"
                    % self.z.offen)
            cb = self.orig.get(aktion) or self.orig.get(kind)
            cb(web_request)
        return handler

    def _melden(self, msg):
        logging.info("k2_abbruch: %s", msg)
        try:
            self.gcode.respond_info("[k2_abbruch] %s" % msg)
        except Exception:
            pass

    def _state(self):
        if self.print_stats is None:
            return "standby"
        try:
            return self.print_stats.get_status(
                self.reactor.monotonic()).get("state", "standby")
        except Exception:
            return "standby"

    def _poll(self, eventtime):
        self.z.druckzustand(self._state())
        return eventtime + POLL_S

    def _kette_aktiv(self):
        kb = self.k2_box
        if kb is None:
            return False
        try:
            return bool(kb.get_status(self.reactor.monotonic())
                        .get("chain_active"))
        except Exception:
            return False

    # --- Abbruchstelle -------------------------------------------------
    def pruefen(self, wo):
        """Wirft command_error, wenn ein Abbruch offen ist (einmalig)."""
        if self.z.offen is None:
            return
        r = self.z.ausloesen(gesperrt=self._kette_aktiv())
        if r is None:
            return
        art, nach = r
        self.z.letzter = {"art": art, "wo": wo, "nach_s": nach}
        self.z.vorbereitung = False
        txt = abbruch_text(art, wo, nach)
        logging.info("k2_abbruch: %s", txt)
        raise self.printer.command_error(txt)

    # --- G-Code ----------------------------------------------------------
    def cmd_KTWO_ABBRUCH(self, gcmd):
        sub = gcmd.get("AKTION", "").strip().upper()
        if sub == "PRUEFEN":
            self.pruefen(gcmd.get("WO", "Startschritt"))
        elif sub == "START":
            self.z.start()
        elif sub == "START_FERTIG":
            self.z.start_fertig()
        elif sub == "RESET":
            self.z.reset()
        elif sub:
            raise gcmd.error("KTWO_ABBRUCH: AKTION=%s unbekannt (PRUEFEN, "
                             "START, START_FERTIG, RESET)" % sub)
        else:
            gcmd.respond_info(
                "[k2_abbruch] offen: %s, Druckvorbereitung: %s, letzter: %s, "
                "eingehängt: %s" % (self.z.offen or "nein",
                                    "ja" if self.z.vorbereitung else "nein",
                                    self.z.letzter or "-",
                                    ", ".join(self.hooks) or "nichts"))

    def get_status(self, eventtime=None):
        return {"offen": self.z.offen or "",
                "vorbereitung": self.z.vorbereitung,
                "letzter": self.z.letzter}


def load_config(config):
    return K2Abbruch(config)
