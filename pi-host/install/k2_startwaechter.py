#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_startwaechter – bringt Klipper nach dem Einschalten selbst in den
Zustand „bereit“ (Systemd-Dienst auf dem Pi, spricht nur mit Moonraker).

Fälle, in denen der Wächter EINEN `FIRMWARE_RESTART` auslöst:
  1. Klipper steht in „error“/„startup“ mit einer Verbindungsmeldung zu
     einer MCU („Unable to connect“, „Lost communication“, „mcu … not
     ready“) – typisch, wenn der Drucker nach dem Pi eingeschaltet wurde.
  1b. Klipper steht in „shutdown“ mit „Lost communication with MCU“ – der
     Drucker wurde bei laufendem Pi ausgeschaltet (24.10.2026: Klipper blieb
     danach bis zum manuellen Neustart tot, auch nach dem Einschalten).
  2. Klipper ist „ready“, aber `printer.k2_box.ready` bleibt False
     (Motor-Init unvollständig, z. B. key798 Extruder-Motor stumm) –
     live mehrfach am 22./23.10.2026, behoben jeweils durch FIRMWARE_RESTART.
  3. Klipper ist „ready“, aber `printer.k2_motor` meldet einen Motor-Init
     mit Busfehler (init_status key798/key799/key801_802, ready False) –
     25.10.2026: Drucker bei laufendem Pi wieder eingeschaltet, Klipper hatte
     sich verbunden, bevor die Motoren hochgefahren waren (X/Y/Z/Z1 ohne
     Adresse). Der Wächter wartet EINSCHALT_WARTE_S nach „ready“, damit die
     Motoren sicher bereit sind, und startet dann neu.

Nach einer Aus-Phase des Druckers (langsamer Takt) beginnt die Zählung neu,
sobald Klipper wieder „ready“ ist – sonst wären die Versuche schon vom
Verbindungsverlust verbraucht und Fall 2/3 griffe nicht (25.10.2026).

Schnell nach dem Aus-/Einschalten (25.10.2026 abends: G28 kam, während der
Wächter noch blind 75 s nach seinem Neustart schlief): nach einem Neustart
fragt der Wächter alle NACH_RESTART_TAKT_S nach, bis Klipper wieder „ready“
ist; Fall 3 meldet sofort in der Konsole, dass die Motoren noch nicht bereit
sind (mit Restzeit), und der Neustart kommt genau nach EINSCHALT_WARTE_S.

Nie eingreifen:
  - im Zustand „shutdown“ aus anderem Grund als Verbindungsverlust (Not-Aus,
    Temperaturfehler, M112 …) – das ist eine Sicherheitsabschaltung, die der
    Mensch quittieren muss;
  - wenn ein Druck läuft oder pausiert ist;
  - mehr als MAX_VERSUCHE-mal je Wächterlauf (Rücksetzen erst, wenn Klipper
    einmal sauber bereit war). Ausnahme Verbindungsfehler (Drucker aus):
    danach weiter im langsamen Takt (LANGSAM_S), bis der Drucker wieder
    eingeschaltet ist – ein FIRMWARE_RESTART ohne erreichbare MCU bewegt und
    heizt nichts.

Jeder Eingriff erscheint als Zeile in der Mainsail-Konsole (über das Makro
_PI_MELDUNG, sobald Klipper wieder bereit ist) und im Systemprotokoll
(journalctl -u k2-startwaechter). Klipper-eigene Notfallmeldungen (Shutdown)
zeigt Mainsail ohnehin rot an.

Aufruf: k2_startwaechter.py [--moonraker URL] [--einmal] [--trocken]
"""
import argparse
import json
import logging
import time
import urllib.request

MAX_VERSUCHE = 3
TAKT_S = 15                 # Prüftakt
WARTE_NACH_RESTART_S = 75   # höchstens so lange nach einem Neustart auf „ready“ warten
NACH_RESTART_TAKT_S = 3     # dabei so oft nachfragen (vorher 75 s blind gewartet)
BOX_KARENZ_S = 90           # so lange darf k2_box nach „ready“ noch nicht bereit sein
EINSCHALT_WARTE_S = 30      # Motor-Init-Busfehler: so lange nach „ready“ warten (Motoren booten)
MOTOR_BUSFEHLER = ("key798", "key799", "key801_802")   # Motoren antworteten nicht
LANGSAM_S = 120             # Drucker aus: so oft neu verbinden (unbegrenzt)
SPANNUNG_TAKT_S = 60       # Pi-Spannung prüfen
SPANNUNG_PAUSE_S = 1800    # höchstens alle 30 min eine Konsolenwarnung
VERBINDUNG = ("unable to connect", "lost communication", "not ready",
              "mcu '", "serial connection closed", "timeout on connect")


def verbindungsfehler(info_state, info_msg):
    """True, wenn Klipper keine MCU erreicht (Drucker aus/Kabel), nicht bei
    Sicherheitsabschaltungen."""
    st = (info_state or "").lower()
    msg = (info_msg or "").lower()
    if st == "shutdown":
        return "lost communication" in msg
    return st == "error" and any(k in msg for k in VERBINDUNG)


def entscheide(info_state, info_msg, druck_state, box_ready, ready_seit,
               jetzt, versuche, motor=None):
    """Reine Entscheidung (testbar): None oder Grund-Text für einen
    FIRMWARE_RESTART. ``motor`` = printer.k2_motor (ready, init_status) oder
    None (Modul fehlt / ältere Fassung ohne diese Felder)."""
    if versuche >= MAX_VERSUCHE:
        return None
    if druck_state in ("printing", "paused"):
        return None
    st = (info_state or "").lower()
    msg = (info_msg or "").lower()
    if st == "shutdown":
        if "lost communication" in msg:
            return "Verbindung zum Drucker verloren (Drucker aus- und wieder eingeschaltet?)"
        return None
    if st in ("error", "startup") and any(k in msg for k in VERBINDUNG):
        if st == "startup":
            return None          # noch im Verbindungsaufbau – abwarten
        return "Klipper meldet keine MCU-Verbindung"
    if st == "ready" and motor and motor.get("ready") is False \
            and motor.get("init_status") in MOTOR_BUSFEHLER \
            and ready_seit is not None and jetzt - ready_seit >= EINSCHALT_WARTE_S:
        return ("Motoren nach dem Start nicht bereit (Motor-Init %s, "
                "Motoren antworteten nicht)" % motor.get("init_status"))
    if st == "ready" and box_ready is False and ready_seit is not None \
            and jetzt - ready_seit >= BOX_KARENZ_S:
        return "CFS-Box nach dem Start nicht bereit (Motor-Init unvollständig)"
    return None


def throttle_text(wert):
    """vcgencmd get_throttled (Bitmaske) -> Warntext oder None. Nur „jetzt“-
    Bits (0–3), damit eine längst vorbei Unterspannung nicht dauernd meldet."""
    teile = []
    if wert & 0x1:
        teile.append("Unterspannung")
    if wert & 0x4:
        teile.append("Leistung gedrosselt")
    elif wert & 0x2:
        teile.append("Takt begrenzt")
    if wert & 0x8:
        teile.append("Temperaturgrenze")
    if not teile:
        return None
    return ("Pi-Warnung: %s – Netzteil prüfen (empfohlen 5,1 V / 5 A, "
            "z. B. offizielles 27-W-Netzteil). Droht Absturz im Druck."
            % ", ".join(teile))


def lies_throttled():
    import subprocess
    out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True,
                         text=True, timeout=5).stdout
    return int(out.strip().split("=")[1], 16)


class Moonraker:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def get(self, pfad):
        with urllib.request.urlopen(self.url + pfad, timeout=10) as r:
            return json.load(r)["result"]

    def meldung(self, text):
        """Zeile in die Mainsail-Konsole (nur wenn Klipper bereit ist)."""
        text = "".join(c for c in text if c not in '#;*"')
        req = urllib.request.Request(
            self.url + "/printer/gcode/script",
            data=json.dumps({"script": '_PI_MELDUNG MSG="%s"' % text}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()

    def post(self, pfad):
        req = urllib.request.Request(self.url + pfad, data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()


def motor_busfehler(motor):
    """True, wenn der letzte Motor-Init an stummen Motoren scheiterte."""
    return bool(motor and motor.get("ready") is False
                and motor.get("init_status") in MOTOR_BUSFEHLER)


def warte_bereit(mr, max_s=WARTE_NACH_RESTART_S, takt=NACH_RESTART_TAKT_S):
    """Nach einem FIRMWARE_RESTART nachfragen, bis Klipper wieder „ready“ ist
    (höchstens max_s). Erste Pause, damit Klipper den alten Zustand verlässt."""
    ende = time.monotonic() + max_s
    time.sleep(min(5, max_s))
    while time.monotonic() < ende:
        try:
            if mr.get("/printer/info").get("state") == "ready":
                return True
        except Exception:
            pass
        time.sleep(takt)
    return False


def lauf(mr, einmal=False, trocken=False):
    versuche = 0
    ready_seit = None
    motor_gemeldet = False      # Warnung „Motoren noch nicht bereit“ schon gezeigt
    offen = []          # Konsolenzeilen, die auf „ready“ warten
    aufgegeben = False
    spannung_zuletzt = 0.0
    spannung_gemeldet = -1e9
    langsam_zuletzt = None      # Zeit des letzten Versuchs im langsamen Takt
    langsam = False
    eingeschaltet = False       # nach einer Aus-Phase wieder verbunden
    while True:
        try:
            info = mr.get("/printer/info")
            state, msg = info.get("state"), info.get("state_message", "")
            druck = box = motor = None
            if state == "ready":
                q = mr.get("/printer/objects/query?print_stats=state&k2_box=ready"
                           "&k2_motor=ready,init_status")
                st = q.get("status", {})
                druck = st.get("print_stats", {}).get("state")
                box = st.get("k2_box", {}).get("ready") if "k2_box" in st else None
                motor = st.get("k2_motor") if "init_status" in st.get("k2_motor", {}) else None
                if ready_seit is None:
                    ready_seit = time.monotonic()
                if langsam:
                    # Drucker nach einer Aus-Phase wieder an: neue Zählung,
                    # damit ein unvollständiger Motor-Init noch neu startet
                    logging.info("Drucker wieder verbunden – Zählung der Neustarts beginnt neu")
                    versuche = 0
                    aufgegeben = False
                    langsam = False
                    langsam_zuletzt = None
                    eingeschaltet = True
                while offen:
                    try:
                        mr.meldung(offen[0])
                        offen.pop(0)
                    except Exception:
                        break
                if motor_busfehler(motor) and not motor_gemeldet \
                        and druck not in ("printing", "paused") and versuche < MAX_VERSUCHE:
                    rest = max(0, int(round(EINSCHALT_WARTE_S - (time.monotonic() - ready_seit))))
                    try:
                        mr.meldung("Start-Wächter: Motoren noch nicht bereit (Motor-Init %s, Drucker "
                                   "gerade eingeschaltet?) – automatischer Neustart in %d s. Bis zur "
                                   "Meldung bereit bitte nicht referenzieren." % (motor.get("init_status"), rest))
                        motor_gemeldet = True
                    except Exception:
                        pass
                motor_ok = not (motor and motor.get("ready") is False
                                and motor.get("init_status") is not None)
                if box is not False and motor_ok:
                    if versuche or eingeschaltet:
                        try:
                            mr.meldung("Start-Wächter: Drucker wieder verbunden und bereit (nach %s)."
                                       % ("dem Einschalten" if eingeschaltet and not versuche else
                                          "%d automatischem Neustart" % versuche))
                        except Exception:
                            pass
                    versuche = 0            # einmal sauber bereit -> zurücksetzen
                    aufgegeben = False
                    eingeschaltet = False
                    motor_gemeldet = False
                    langsam = False
                    langsam_zuletzt = None
            else:
                ready_seit = None
            grund = entscheide(state, msg, druck, box, ready_seit,
                               time.monotonic(), versuche, motor)
            if grund is None and versuche >= MAX_VERSUCHE \
                    and verbindungsfehler(state, msg):
                # Drucker aus: unbegrenzt, aber nur alle LANGSAM_S
                jetzt_l = time.monotonic()
                if not langsam:
                    langsam = True
                    text = ("Start-Wächter: Drucker antwortet nicht (ausgeschaltet?) – "
                            "verbinde automatisch neu, sobald er wieder an ist (Versuch alle %d s)."
                            % LANGSAM_S)
                    logging.warning(text)
                    offen.append(text)
                if langsam_zuletzt is None or jetzt_l - langsam_zuletzt >= LANGSAM_S:
                    langsam_zuletzt = jetzt_l
                    logging.info("FIRMWARE_RESTART (langsamer Takt): %s", msg)
                    if not trocken:
                        try:
                            mr.post("/printer/firmware_restart")
                        except Exception as exc:
                            logging.debug("firmware_restart: %s", exc)
            elif grund is None and versuche >= MAX_VERSUCHE and not aufgegeben \
                    and entscheide(state, msg, druck, box, ready_seit,
                                   time.monotonic(), 0, motor):
                aufgegeben = True
                text = ("Start-Wächter: Nach %d Neustarts weiterhin Problem – bitte prüfen "
                        "(Kabel, Strom). Kein weiterer automatischer Neustart." % versuche)
                logging.error(text)
                offen.append(text)
            if grund:
                versuche += 1
                offen.append("Start-Wächter: automatischer FIRMWARE_RESTART %d von %d – Grund: %s."
                             % (versuche, MAX_VERSUCHE, grund))
                logging.warning("FIRMWARE_RESTART (%d/%d): %s", versuche,
                                MAX_VERSUCHE, grund)
                if not trocken:
                    mr.post("/printer/firmware_restart")
                ready_seit = None
                motor_gemeldet = False
                warte_bereit(mr)
                continue
        except Exception as exc:     # Moonraker/Klipper noch nicht da
            logging.debug("Abfrage fehlgeschlagen: %s", exc)
            ready_seit = None
        jetzt = time.monotonic()
        if jetzt - spannung_zuletzt >= SPANNUNG_TAKT_S:
            spannung_zuletzt = jetzt
            try:
                text = throttle_text(lies_throttled())
                if text and jetzt - spannung_gemeldet >= SPANNUNG_PAUSE_S:
                    logging.warning(text)
                    try:
                        mr.meldung(text)
                        spannung_gemeldet = jetzt
                    except Exception:
                        pass
            except Exception as exc:
                logging.debug("vcgencmd: %s", exc)
        if einmal:
            return
        schlaf = TAKT_S
        if motor_gemeldet and ready_seit is not None:   # Neustart genau zur Frist, nicht erst im Takt
            schlaf = max(1.0, min(TAKT_S, ready_seit + EINSCHALT_WARTE_S - time.monotonic() + 0.5))
        time.sleep(schlaf)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--moonraker", default="http://localhost:7125")
    ap.add_argument("--einmal", action="store_true")
    ap.add_argument("--trocken", action="store_true",
                    help="nur melden, keinen FIRMWARE_RESTART senden")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="k2_startwaechter: %(levelname)s %(message)s")
    lauf(Moonraker(a.moonraker), a.einmal, a.trocken)


if __name__ == "__main__":
    main()
