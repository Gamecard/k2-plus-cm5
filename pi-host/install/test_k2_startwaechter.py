#!/usr/bin/env python3
"""Offline-Test der Entscheidungslogik von k2_startwaechter.py."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import k2_startwaechter as w
ok = 0
def check(name, cond):
    global ok
    assert cond, name
    ok += 1
    print("ok", name)
e = w.entscheide
check("Shutdown (Sicherheit) nie", e("shutdown", "Heater extruder not heating at expected rate", None, None, None, 100, 0) is None)
check("Shutdown M112 nie", e("shutdown", "Shutdown due to M112 command", None, None, None, 100, 0) is None)
check("Drucker aus (Lost communication) -> Restart", e("shutdown", "Lost communication with MCU 'mcu'", None, None, None, 100, 0))
check("Lost communication, max Versuche -> nichts (langsamer Takt übernimmt)", e("shutdown", "Lost communication with MCU 'mcu'", None, None, None, 100, 3) is None)
vf = w.verbindungsfehler
check("verbindungsfehler: Lost communication", vf("shutdown", "Lost communication with MCU 'mcu'"))
check("verbindungsfehler: Unable to connect", vf("error", "mcu 'mcu': Unable to connect"))
check("verbindungsfehler: Heizfehler nein", not vf("shutdown", "Heater extruder not heating at expected rate"))
check("verbindungsfehler: ready nein", not vf("ready", ""))
check("Fehler Verbindung -> Restart", e("error", "mcu 'mcu': Unable to connect", None, None, None, 100, 0))
check("startup abwarten", e("startup", "mcu 'mcu': Unable to connect", None, None, None, 100, 0) is None)
check("Fehler ohne Verbindungsbezug -> nichts", e("error", "Option 'x' is not valid in section 'y'", None, None, None, 100, 0) is None)
check("max Versuche", e("error", "Unable to connect", None, None, None, 100, 3) is None)
check("Druck läuft -> nichts", e("ready", "", "printing", False, 0, 500, 0) is None)
check("Box nicht bereit nach Karenz -> Restart", e("ready", "", "standby", False, 0, 100, 0))
check("Box nicht bereit vor Karenz -> warten", e("ready", "", "standby", False, 50, 100, 0) is None)
check("Box bereit -> nichts", e("ready", "", "standby", True, 0, 500, 0) is None)
check("keine Box (k2_box fehlt) -> nichts", e("ready", "", "standby", None, 0, 500, 0) is None)
m798 = {"ready": False, "init_status": "key798"}
check("Motor key798 nach Wartezeit -> Restart", "key798" in (e("ready", "", "standby", False, 0, w.EINSCHALT_WARTE_S, 0, m798) or ""))
check("Motor key798 vor Wartezeit -> warten", e("ready", "", "standby", False, 0, w.EINSCHALT_WARTE_S - 1, 0, m798) is None)
check("Motor key798, Druck läuft -> nichts", e("ready", "", "printing", False, 0, 500, 0, m798) is None)
check("Motor key798, max Versuche -> nichts", e("ready", "", "standby", False, 0, 500, 3, m798) is None)
check("Motor-Init noch nicht gelaufen -> nichts (Box-Karenz)", e("ready", "", "standby", None, 0, 60, 0, {"ready": False, "init_status": None}) is None)
check("Motor config_error -> kein Restart über Motorregel", e("ready", "", "standby", None, 0, 500, 0, {"ready": False, "init_status": "config_error"}) is None)
check("Motoren bereit -> nichts", e("ready", "", "standby", True, 0, 500, 0, {"ready": True, "init_status": "ready"}) is None)
tt = w.throttle_text
check("throttled 0 -> nichts", tt(0) is None)
check("nur früher (0x50000) -> nichts", tt(0x50000) is None)
check("Unterspannung jetzt", "Unterspannung" in tt(0x50001))
check("gedrosselt", "Leistung gedrosselt" in tt(0x4))

# Ablauf-Simulation: Drucker aus (Lost communication) -> 3 schnelle Versuche,
# dann langsamer Takt ohne Ende; Drucker wieder an -> bereit, Meldung.
class Uhr:
    t = 1000.0
uhr = Uhr()
class Stop(Exception):
    pass
class FakeMR:
    def __init__(self):
        self.posts = []; self.meld = []; self.aus_bis = 1000.0 + 2000
    def get(self, pfad):
        an = uhr.t >= self.aus_bis
        if pfad.startswith("/printer/info"):
            if not an:
                return {"state": "shutdown" if not self.posts else "error",
                        "state_message": "Lost communication with MCU 'mcu'" if not self.posts
                        else "mcu 'mcu': Unable to connect"}
            return {"state": "ready", "state_message": ""}
        return {"status": {"print_stats": {"state": "standby"}, "k2_box": {"ready": True}}}
    def post(self, pfad):
        self.posts.append(uhr.t)
    def meldung(self, text):
        self.meld.append(text)
schritte = [0]
def fake_sleep(s_):
    uhr.t += s_
    schritte[0] += 1
    if uhr.t > 1000.0 + 3000:
        raise Stop()
w.time.sleep = fake_sleep
w.time.monotonic = lambda: uhr.t
w.lies_throttled = lambda: 0
mr = FakeMR()
try:
    w.lauf(mr)
except Stop:
    pass
schnell = [t for t in mr.posts if t < 1000 + 3 * (w.WARTE_NACH_RESTART_S + w.TAKT_S) + 1]
check("Sim: 3 schnelle Versuche", len(schnell) >= 3)
check("Sim: danach weitere Versuche im langsamen Takt", len(mr.posts) > 3)
abst = [b - a for a, b in zip(mr.posts[3:], mr.posts[4:])]
check("Sim: langsamer Takt >= LANGSAM_S", all(x >= w.LANGSAM_S - w.TAKT_S for x in abst))
check("Sim: nach dem Einschalten keine Versuche mehr", all(t < 1000 + 2000 + w.LANGSAM_S for t in mr.posts))
check("Sim: Meldung langsamer Takt", any("ausgeschaltet" in m for m in mr.meld))
check("Sim: Meldung wieder verbunden", any("wieder verbunden" in m for m in mr.meld))

# Ablauf 25.10.2026: Drucker lange aus (Versuche verbraucht), dann wieder an;
# die erste Verbindung kommt, bevor die Motoren hochgefahren sind (key798).
# Erwartung: neue Zählung, FIRMWARE_RESTART >= EINSCHALT_WARTE_S nach ready,
# danach bereit + Meldung, kein „Nach 3 Neustarts weiterhin Problem“.
uhr.t = 1000.0
class FakeMR2(FakeMR):
    def __init__(self):
        FakeMR.__init__(self)
        self.an_ab = 1000.0 + 1500      # Drucker wieder eingeschaltet
        self.ready_ab = None            # erste Verbindung nach dem Einschalten
        self.restart_nach_an = []
    def get(self, pfad):
        if uhr.t < self.an_ab:
            return FakeMR.get(self, pfad) if pfad.startswith("/printer/info") else {}
        if self.ready_ab is None:
            self.ready_ab = uhr.t
        if pfad.startswith("/printer/info"):
            return {"state": "ready", "state_message": ""}
        gut = bool(self.restart_nach_an)
        return {"status": {"print_stats": {"state": "standby"},
                           "k2_box": {"ready": gut},
                           "k2_motor": {"ready": gut,
                                        "init_status": "ready" if gut else "key798"}}}
    def post(self, pfad):
        FakeMR.post(self, pfad)
        if uhr.t >= self.an_ab and self.ready_ab is not None:
            self.restart_nach_an.append(uhr.t)
mr2 = FakeMR2()
mr2.aus_bis = mr2.an_ab
try:
    w.lauf(mr2)
except Stop:
    pass
check("Sim2: vor dem Einschalten Versuche verbraucht (langsamer Takt)", any("ausgeschaltet" in m for m in mr2.meld))
check("Sim2: genau ein Neustart nach dem Einschalten", len(mr2.restart_nach_an) == 1)
check("Sim2: Neustart frühestens EINSCHALT_WARTE_S nach ready",
      mr2.restart_nach_an and mr2.restart_nach_an[0] - mr2.ready_ab >= w.EINSCHALT_WARTE_S)
check("Sim2: Neustart vor der Box-Karenz", mr2.restart_nach_an and mr2.restart_nach_an[0] - mr2.ready_ab < w.BOX_KARENZ_S + w.TAKT_S)
check("Sim2: Grund nennt key798", any("key798" in m for m in mr2.meld))
check("Sim2: danach wieder verbunden", any("wieder verbunden" in m for m in mr2.meld))
check("Sim2: nicht aufgegeben", not any("weiterhin Problem" in m for m in mr2.meld))

# Ablauf 25.10.2026 abends: Drucker kurz aus- und wieder eingeschaltet. Klipper
# verbindet ~20 s nach jedem Neustart; die Motoren antworten erst 50 s nach dem
# Einschalten. Alte Fassung: 75 s blind gewartet -> „bereit“ erst nach ~180 s,
# ein G28 lief ins Leere. Erwartung: Warnung sofort, zweiter Neustart
# genau EINSCHALT_WARTE_S nach „ready“, „bereit“ nach < 100 s.
uhr.t = 1000.0
class FakeMR3:
    VERBINDEN_S, MOTOR_BOOT_S = 20.0, 50.0
    def __init__(self):
        self.an_ab = 1010.0             # Drucker wieder an (aus seit 1000)
        self.posts = []; self.meld = []
    def _zustand(self):
        if not self.posts:
            return "shutdown", "Lost communication with MCU 'mcu'", None
        tp = self.posts[-1]
        if uhr.t < tp + self.VERBINDEN_S:
            return "startup", "mcu 'mcu': Starting serial connect", None
        motoren_ok = tp + self.VERBINDEN_S - self.an_ab >= self.MOTOR_BOOT_S
        return "ready", "", motoren_ok
    def get(self, pfad):
        st, msg, ok = self._zustand()
        if pfad.startswith("/printer/info"):
            return {"state": st, "state_message": msg}
        return {"status": {"print_stats": {"state": "standby"}, "k2_box": {"ready": bool(ok)},
                           "k2_motor": {"ready": bool(ok), "init_status": "ready" if ok else "key798"}}}
    def post(self, pfad):
        self.posts.append(uhr.t)
    def meldung(self, text):
        self.meld.append((uhr.t, text))
mr3 = FakeMR3()
def fake_sleep3(s_):
    uhr.t += s_
    if uhr.t > 1000.0 + 400:
        raise Stop()
w.time.sleep = fake_sleep3
try:
    w.lauf(mr3)
except Stop:
    pass
warn3 = [t for t, m in mr3.meld if "noch nicht bereit" in m]
bereit3 = [t for t, m in mr3.meld if "wieder verbunden und bereit" in m]
ready3 = mr3.posts[0] + FakeMR3.VERBINDEN_S if mr3.posts else None
check("Sim3: zwei Neustarts (Verbindung, dann Motoren)", len(mr3.posts) == 2)
check("Sim3: Warnung „Motoren noch nicht bereit“ vor dem zweiten Neustart",
      warn3 and len(mr3.posts) == 2 and warn3[0] < mr3.posts[1])
check("Sim3: Warnung höchstens 5 s nach „ready“", warn3 and warn3[0] - ready3 <= 5)
check("Sim3: zweiter Neustart genau nach EINSCHALT_WARTE_S (+ < 2 s)",
      len(mr3.posts) == 2 and w.EINSCHALT_WARTE_S <= mr3.posts[1] - ready3 < w.EINSCHALT_WARTE_S + 2)
check("Sim3: bereit nach < 100 s (vorher ~180 s)", bereit3 and bereit3[0] - 1000 < 100)
check("Sim3: Warnung nennt Restzeit", any("Neustart in" in m for _, m in mr3.meld))
import ast
for f in ("k2_startwaechter.py", "test_k2_startwaechter.py"):
    ast.parse(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), f), encoding="utf-8").read(), feature_version=(3, 9))
check("Python-3.9-Syntax", True)
print("ERGEBNIS: ok (%d/%d ok)" % (ok, ok))
