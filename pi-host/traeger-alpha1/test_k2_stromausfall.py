#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline-Tests für k2_stromausfall.py (ohne Hardware, ohne root).

Prüft:
1. Konfig: Standardwerte, Überschreiben, Fehler (Hysterese, Adresse, Grenze).
2. Registerwert 20 V -> 16000 = 0x3E80 (INA226 1,25 mV/LSB, README des Layouts).
3. INA226 Weg A (hwmon in Temp-„sysfs“): update_interval, in1_lcrit, Rückprüfung,
   Alarm lesen (Latch frei); Rückprüfung falsch -> Fehler.
4. INA226 Weg B (i2ctransfer): exakte Befehlszeilen (0x07 = 3E 80, 0x06 = 10 00,
   Lesen 0x06), Spannung aus Register 0x02.
5. Wächter: Start ohne 24 V (USB-C, ALERT low) bzw. ohne lesbaren INA226
   (IO-Board) -> nie scharf, Flanken ignoriert;
   Scharfschalten erst nach K2SA_SCHARF_S mit guter Spannung; Hysterese.
6. Notfall: Reihenfolge einfrieren -> log1 (ohne fsync) -> sync -> log2 (fsync)
   -> Daten schützen; genau einmal; zweite Flanke ignoriert.
7. Störimpuls (Pegel nach der Bestätigung wieder high) -> kein Notfall.
8. Verpasste Flanke (Pegel low im Takt) -> Notfall.
9. Fehler in einem Schritt -> die übrigen laufen trotzdem.
10. Wiederkehr der Spannung im Schutzzustand -> genau eine Meldung, NICHTS
    aufgetaut/freigegeben/neu gestartet; Trockenlauf: wieder WARTEN.
11. Treiber-Latch: ALERT low bei guter Spannung im WARTEN -> Alarm lesen.
12. SystemAktionen: cgroup.freeze = 1 bzw. SIGSTOP über cgroup.procs; Log-Datei
    mit fsync; remount,ro -> bei EBUSY FIFREEZE; ohne Partition nur sync;
    Freigeben (FITHAW + remount,rw + auftauen).
13. TrockenAktionen ändern nichts. sysrq nur mit K2SA_SYSRQ=1.
14. GPIO-Adapter v1/v2 mit nachgebauten gpiod-Modulen (API-Namen).
15. Python-3.9-Syntax.
"""

import ast
import errno
import os
import shutil
import signal
import sys
import tempfile
import types

HIER = os.path.dirname(os.path.abspath(__file__))
if HIER not in sys.path:
    sys.path.insert(0, HIER)

import k2_stromausfall as S  # noqa: E402

ERG = []


def check(name, cond, detail=""):
    ERG.append((name, bool(cond)))
    print("%s %s %s" % ("PASS" if cond else "FAIL", name, detail))


class Uhr(object):
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


class FakeAkt(object):
    def __init__(self, uhr, pegel_nach_bestaetigung=0, fehler_bei=None):
        self.uhr = uhr
        self.pegel_wert = pegel_nach_bestaetigung
        self.fehler_bei = fehler_bei or set()
        self.aufrufe = []
        self.meldungen = []

    def _rec(self, name, *a):
        self.aufrufe.append((name,) + a)
        self.uhr.t += 0.001
        if name in self.fehler_bei:
            raise OSError(errno.EIO, "Testfehler " + name)

    def pegel(self):
        return self.pegel_wert

    def alarm_loeschen(self):
        self._rec("alarm_loeschen")
        return "1"

    def schlafe(self, s):
        self.aufrufe.append(("schlafe", s))
        self.uhr.t += s

    def melde(self, text):
        self.meldungen.append(text)

    def dienste_einfrieren(self):
        self._rec("einfrieren")
        return "klipper.service:eingefroren"

    def dienste_auftauen(self):
        self._rec("auftauen")

    def log_daten(self, text, fsync=False):
        self._rec("log", fsync)
        return True

    def sync(self):
        self._rec("sync")

    def daten_schuetzen(self):
        self._rec("schuetzen")
        return "/daten remount,ro"

    def daten_freigeben(self):
        self._rec("freigeben")

    def sysrq(self):
        self._rec("sysrq")
        return "sysrq s+u"


def kfg(**ueber):
    env = {"K2SA_SCHARF_S": "2"}
    env.update(ueber)
    return S.Konfig(env)


def namen(akt):
    return [a[0] for a in akt.aufrufe if a[0] not in ("schlafe", "alarm_loeschen")]


# ---------------------------------------------------------------------

def test_konfig():
    k = S.Konfig({})
    check("Konfig: Standard GPIO27 / 0x40 / 20 V / 21 V / 1 ms",
          (k.gpio_name, k.gpio_offset, k.ina_adr, k.grenze_mv, k.wieder_mv, k.bestaetigung_s)
          == ("GPIO27", 27, 0x40, 20000, 21000, 0.001))
    check("Konfig: Standard-Dienste klipper + moonraker",
          k.dienste == ["klipper.service", "moonraker.service"])
    check("Konfig: Log auf der Datenpartition",
          k.log == "/daten/printer_data/logs/k2_stromausfall.log", k.log)
    k = S.Konfig({"K2SA_GRENZE_MV": "19000", "K2SA_DATEN": "/mnt/x/", "K2SA_SYSRQ": "1"})
    check("Konfig: Überschreiben + Daten ohne Schrägstrich + sysrq",
          k.grenze_mv == 19000 and k.daten == "/mnt/x" and k.sysrq)
    for env, name in (({"K2SA_WIEDER_MV": "20000"}, "Hysterese"),
                      ({"K2SA_INA_ADR": "0x50"}, "Adresse"),
                      ({"K2SA_GRENZE_MV": "40000", "K2SA_WIEDER_MV": "41000"}, "Grenze > 36 V"),
                      ({"K2SA_DIENSTE": "../x"}, "Dienstname"),
                      ({"K2SA_GPIO_OFFSET": "abc"}, "Zahl")):
        try:
            S.Konfig(env)
            ok = False
        except S.KonfigFehler:
            ok = True
        check("Konfig: Fehler erkannt (%s)" % name, ok)


def test_register():
    check("Register: 20 V -> 16000 = 0x3E80", S.grenze_register(20000) == 0x3E80)
    check("Register: Maske BUL = 0x1000, Limit-Register 0x07, Mask 0x06",
          (S.INA226_MASK_BUL, S.INA226_REG_LIMIT, S.INA226_REG_MASK) == (0x1000, 0x07, 0x06))
    check("FIFREEZE/FITHAW wie linux/fs.h", (S.FIFREEZE, S.FITHAW) == (0xC0045877, 0xC0045878))


def test_ina_hwmon():
    tmp = tempfile.mkdtemp()
    try:
        d = os.path.join(tmp, "bus", "i2c", "devices", "1-0040", "hwmon", "hwmon3")
        os.makedirs(d)
        for n, v in (("in1_input", "24012"), ("in1_lcrit", "0"), ("in1_lcrit_alarm", "1"),
                     ("update_interval", "35")):
            open(os.path.join(d, n), "w").write(v + "\n")
        k = S.Konfig({"K2SA_SYSFS": tmp})
        ina = S.Ina226(k, run=lambda argv: (_ for _ in ()).throw(AssertionError("kein i2ctransfer")))
        check("hwmon: Verzeichnis 1-0040 gefunden", ina.hwmon == d, str(ina.hwmon))
        txt = ina.einrichten()
        check("hwmon: in1_lcrit = 20000 geschrieben",
              open(os.path.join(d, "in1_lcrit")).read().strip() == "20000")
        check("hwmon: update_interval = 2 geschrieben",
              open(os.path.join(d, "update_interval")).read().strip() == "2")
        check("hwmon: Meldung nennt 0x3E80 und Latch", "0x3E80" in txt and "Latch" in txt, txt)
        check("hwmon: Spannung gelesen", ina.spannung_mv() == 24012)
        check("hwmon: Alarm lesen (Latch frei)", ina.alarm_loeschen() == "1")
        # Rückprüfung falsch: Datei wird nach dem Schreiben „vom Treiber“ geändert
        k2 = S.Konfig({"K2SA_SYSFS": tmp, "K2SA_GRENZE_MV": "19000"})
        ina2 = S.Ina226(k2)
        orig = ina2._schreib

        def schreib_falsch(name, wert):
            orig(name, 12345 if name == "in1_lcrit" else wert)
        ina2._schreib = schreib_falsch
        try:
            ina2.einrichten()
            ok = False
        except OSError as exc:
            ok = "Rückprüfung" in str(exc)
        check("hwmon: falsche Rückprüfung -> Fehler", ok)
    finally:
        shutil.rmtree(tmp)


class R(object):
    def __init__(self, rc=0, out="", err=""):
        self.returncode, self.stdout, self.stderr = rc, out, err


def test_ina_i2c():
    befehle = []

    def run(argv):
        befehle.append(argv)
        if argv[-1] == "r2" and argv[-2] == "0x02":
            return R(0, "0x4b 0x00\n")   # 0x4B00 = 19200 -> 24000 mV
        return R(0, "0x10 0x00\n")
    k = S.Konfig({"K2SA_SYSFS": "/gibt/es/nicht"})
    ina = S.Ina226(k, run=run)
    check("i2c: ohne hwmon -> Weg B", ina.hwmon is None)
    txt = ina.einrichten()
    soll = [["i2ctransfer", "-f", "-y", "1", "w3@0x40", "0x07", "0x3e", "0x80"],
            ["i2ctransfer", "-f", "-y", "1", "w3@0x40", "0x06", "0x10", "0x00"],
            ["i2ctransfer", "-f", "-y", "1", "w1@0x40", "0x06", "r2"]]
    check("i2c: Befehle exakt (erst Limit, dann Maske, dann lesen)", befehle == soll, str(befehle))
    check("i2c: Meldung ohne Latch", "ohne Latch" in txt, txt)
    check("i2c: Spannung 0x4B00 -> 24000 mV", ina.spannung_mv() == 24000)
    ina_f = S.Ina226(k, run=lambda a: R(1, "", "Error: Read failed"))
    check("i2c: Lesefehler -> None", ina_f.spannung_mv() is None)
    try:
        ina_f.einrichten()
        ok = False
    except OSError:
        ok = True
    check("i2c: Schreibfehler -> OSError", ok)


def test_waechter_usbc():
    u = Uhr()
    akt = FakeAkt(u)
    w = S.Waechter(kfg(), akt, uhr=u)
    for _ in range(10):
        w.takt(0, 0)
        w.flanke()
        u.t += 1.0
    check("USB-C ohne 24 V: nie scharf, kein Notfall",
          w.zustand == w.WARTEN and w.notfaelle == 0 and "einfrieren" not in namen(akt))
    check("USB-C: Meldung 'nicht scharf' nur einmal",
          sum(1 for m in akt.meldungen if m.startswith("nicht scharf")) == 1, str(akt.meldungen))
    check("USB-C: kein Latch-Lesen bei 0 V", not any(a[0] == "alarm_loeschen" for a in akt.aufrufe))


def test_waechter_scharf_hysterese():
    u = Uhr()
    akt = FakeAkt(u)
    w = S.Waechter(kfg(), akt, uhr=u)
    w.takt(1, 20500)            # zwischen Grenze und Wiederkehr
    u.t += 5
    w.takt(1, 20500)
    check("Hysterese: 20,5 V -> nicht scharf", w.zustand == w.WARTEN)
    w.takt(1, 24000)
    u.t += 1.0
    w.takt(1, 24000)
    check("Scharf: noch nicht nach 1 s", w.zustand == w.WARTEN)
    u.t += 1.1
    w.takt(1, 24000)
    check("Scharf: nach 2 s guter Spannung", w.zustand == w.SCHARF)


def scharfer_waechter(**kw):
    u = Uhr()
    akt = FakeAkt(u, **kw)
    w = S.Waechter(kfg(), akt, uhr=u)
    w.takt(1, 24000)
    u.t += 3
    w.takt(1, 24000)
    assert w.zustand == w.SCHARF
    return u, akt, w


def test_notfall():
    u, akt, w = scharfer_waechter()
    r = w.flanke()
    check("Notfall: ausgelöst", r and w.zustand == w.SCHUTZ and w.notfaelle == 1)
    check("Notfall: erst Bestätigung (1 ms warten)", akt.aufrufe[0] == ("schlafe", 0.001))
    check("Notfall: Reihenfolge einfrieren, log, sync, log(fsync), schützen",
          namen(akt) == ["einfrieren", "log", "sync", "log", "schuetzen"], str(namen(akt)))
    logs = [a for a in akt.aufrufe if a[0] == "log"]
    check("Notfall: log1 ohne fsync, log2 mit fsync", logs == [("log", False), ("log", True)])
    check("Notfall: kein sysrq ohne K2SA_SYSRQ", "sysrq" not in namen(akt))
    check("Notfall: Meldung SCHUTZZUSTAND mit Schritten",
          any("SCHUTZZUSTAND" in m and "schuetzen=" in m for m in akt.meldungen))
    vorher = len(akt.aufrufe)
    w.flanke()
    w.takt(0, None)
    check("Notfall: zweite Flanke/Pegel low -> nichts mehr", len(akt.aufrufe) == vorher)
    # mit sysrq
    u = Uhr()
    akt = FakeAkt(u)
    w = S.Waechter(kfg(K2SA_SYSRQ="1"), akt, uhr=u)
    w.zustand = w.SCHARF
    w.flanke()
    check("Notfall: sysrq zuletzt, wenn eingestellt", namen(akt)[-1] == "sysrq")


def test_stoerimpuls_und_verpasst():
    u, akt, w = scharfer_waechter(pegel_nach_bestaetigung=1)
    r = w.flanke()
    check("Störimpuls: kein Notfall, bleibt scharf",
          not r and w.zustand == w.SCHARF and "einfrieren" not in namen(akt) and w.stoerimpulse == 1)
    u, akt, w = scharfer_waechter(pegel_nach_bestaetigung=0)
    w.takt(0, None)
    check("Verpasste Flanke: Pegel low im Takt -> Notfall", w.notfaelle == 1 and w.zustand == w.SCHUTZ)


def test_fehler_im_schritt():
    u, akt, w = scharfer_waechter(fehler_bei={"einfrieren", "sync"})
    w.flanke()
    check("Fehler in einfrieren/sync: übrige Schritte laufen",
          namen(akt) == ["einfrieren", "log", "sync", "log", "schuetzen"], str(namen(akt)))
    check("Fehler: im Protokoll vermerkt",
          sum(1 for n, e, d in w.protokoll if str(e).startswith("FEHLER")) == 2, str(w.protokoll))


def test_wiederkehr():
    u, akt, w = scharfer_waechter()
    w.flanke()
    n = len(akt.aufrufe)
    for v in (5000, 24000, 24000, 24000, 24000, 24000, 24000):
        w.takt(0, v)
        u.t += 1.0
    wieder = [m for m in akt.meldungen if "wieder da" in m]
    check("Wiederkehr: genau eine Meldung", len(wieder) == 1, str(wieder))
    check("Wiederkehr: nichts aufgetaut/freigegeben/neu",
          len(akt.aufrufe) == n and w.zustand == w.SCHUTZ)
    check("Wiederkehr: Meldung nennt --freigeben", "--freigeben" in wieder[0])
    # Trockenlauf: danach wieder WARTEN
    u = Uhr()
    akt = FakeAkt(u)
    w = S.Waechter(kfg(), akt, uhr=u, neu_scharf_nach_wiederkehr=True)
    w.zustand = w.SCHARF
    w.flanke()
    for _ in range(5):
        w.takt(1, 24000)
        u.t += 1.0
    check("Trockenlauf: nach Wiederkehr wieder WARTEN/SCHARF",
          w.zustand in (w.WARTEN, w.SCHARF))


def test_ohne_ina():
    u = Uhr()
    akt = FakeAkt(u)
    w = S.Waechter(kfg(), akt, uhr=u)
    for _ in range(10):
        w.takt(1, None)      # GPIO27 offen/high, INA226 nicht lesbar (IO-Board)
        w.flanke()
        u.t += 1.0
    check("Ohne INA226 (IO-Board): nie scharf, kein Notfall",
          w.zustand == w.WARTEN and w.notfaelle == 0 and "einfrieren" not in namen(akt))
    check("Ohne INA226: Grund gemeldet", any("nicht lesbar" in m for m in akt.meldungen), str(akt.meldungen))


def test_latch():
    u = Uhr()
    akt = FakeAkt(u)
    w = S.Waechter(kfg(), akt, uhr=u)
    w.takt(0, 24000)
    check("Latch: ALERT low bei 24 V im WARTEN -> Alarm lesen",
          ("alarm_loeschen",) in akt.aufrufe)


def test_system_aktionen():
    tmp = tempfile.mkdtemp()
    try:
        cg = os.path.join(tmp, "cg")
        os.makedirs(os.path.join(cg, "klipper.service"))
        open(os.path.join(cg, "klipper.service", "cgroup.freeze"), "w").write("0\n")
        os.makedirs(os.path.join(cg, "moonraker.service"))
        open(os.path.join(cg, "moonraker.service", "cgroup.procs"), "w").write("101\n102\n")
        daten = os.path.join(tmp, "daten")
        os.makedirs(os.path.join(daten, "printer_data", "logs"))
        k = S.Konfig({"K2SA_CGROUP": cg, "K2SA_DATEN": daten,
                      "K2SA_DIENSTE": "klipper.service moonraker.service fehlt.service"})
        kills, mounts, ioctls, ausgaben, runs = [], [], [], [], []

        def mount(z, f):
            mounts.append((z, f))
            raise OSError(errno.EBUSY, "busy")

        akt = S.SystemAktionen(k, mount=mount, ioctl_pfad=lambda p, r: ioctls.append((p, r)),
                               kill=lambda pid, sig: kills.append((pid, sig)),
                               ausgabe=ausgaben.append, ismount=lambda p: p == daten,
                               run=lambda a: (runs.append(a), R(0))[1])
        txt = akt.dienste_einfrieren()
        check("System: cgroup.freeze = 1",
              open(os.path.join(cg, "klipper.service", "cgroup.freeze")).read().strip() == "1")
        check("System: ohne cgroup.freeze -> SIGSTOP an alle PIDs",
              kills == [(101, signal.SIGSTOP), (102, signal.SIGSTOP)], str(kills))
        check("System: fehlende Einheit gemeldet", "fehlt.service:fehlt" in txt, txt)
        check("System: Log geschrieben", akt.log_daten("STROMAUSFALL test", fsync=True)
              and "STROMAUSFALL test" in open(k.log).read())
        ersatz = os.path.join(tmp, "varlog", "k2_stromausfall.log")
        os.makedirs(os.path.dirname(ersatz))
        k_e = S.Konfig({"K2SA_DATEN": os.path.join(tmp, "fehlt"), "K2SA_LOG_ERSATZ": ersatz})
        a_e = S.SystemAktionen(k_e)
        check("System: ohne Datenpartition -> Ersatz-Log (/var/log)",
              a_e.log_daten("STROMAUSFALL ersatz", fsync=True) and "ersatz" in open(ersatz).read())
        k_n = S.Konfig({"K2SA_DATEN": os.path.join(tmp, "fehlt"), "K2SA_LOG_ERSATZ": os.path.join(tmp, "x", "y.log")})
        check("System: kein Log-Ordner -> False, kein Absturz", S.SystemAktionen(k_n).log_daten("z") is False)
        t = akt.daten_schuetzen()
        check("System: remount,ro (MS_REMOUNT|MS_RDONLY) zuerst",
              mounts == [(daten, S.MS_REMOUNT | S.MS_RDONLY)], str(mounts))
        check("System: EBUSY -> FIFREEZE", ioctls == [(daten, S.FIFREEZE)] and "FIFREEZE" in t, t)
        k2 = S.Konfig({"K2SA_CGROUP": cg, "K2SA_DATEN": os.path.join(tmp, "nicht")})
        a2 = S.SystemAktionen(k2, mount=mount, ismount=lambda p: False)
        n = len(mounts)
        check("System: ohne Datenpartition -> nur Meldung",
              "keine Datenpartition" in a2.daten_schuetzen() and len(mounts) == n)
        ok_mount = []
        a3 = S.SystemAktionen(k, mount=lambda z, f: ok_mount.append(z), ismount=lambda p: True)
        check("System: remount,ro klappt -> kein FIFREEZE nötig",
              "remount,ro" in a3.daten_schuetzen() and ok_mount == [daten])
        ioctls[:] = []
        kills[:] = []
        frei = akt.daten_freigeben()
        auf = akt.dienste_auftauen()
        check("System: Freigeben = FITHAW + remount,rw",
              ioctls == [(daten, S.FITHAW)] and runs == [["mount", "-o", "remount,rw", daten]], frei)
        check("System: Auftauen = cgroup.freeze 0 + SIGCONT",
              open(os.path.join(cg, "klipper.service", "cgroup.freeze")).read().strip() == "0"
              and kills == [(101, signal.SIGCONT), (102, signal.SIGCONT)], auf)
        # Trocken
        tr = S.TrockenAktionen(akt)
        vorher = open(os.path.join(cg, "klipper.service", "cgroup.freeze")).read()
        tr.dienste_einfrieren()
        tr.log_daten("x", True)
        tr.daten_schuetzen()
        tr.sysrq()
        check("Trocken: nichts verändert",
              open(os.path.join(cg, "klipper.service", "cgroup.freeze")).read() == vorher
              and len(ioctls) == 1)
    finally:
        shutil.rmtree(tmp)


# --- nachgebaute gpiod-Module (nur die benutzten API-Namen) -------------

def gpiod_v1(pegel_folge, label="pinctrl-rp1"):
    m = types.ModuleType("gpiod")
    m.LINE_REQ_EV_BOTH_EDGES = 6
    m.LINE_REQ_FLAG_BIAS_DISABLE = 8

    class LineEvent(object):
        RISING_EDGE, FALLING_EDGE = 1, 2

        def __init__(self, t):
            self.type = t
    m.LineEvent = LineEvent

    class Chip(object):
        def name(self):
            return "gpiochip0"

        def label(self):
            return label

        def get_line(self, off):
            return Line(off)

    class Line(object):
        def __init__(self, off):
            self.off = off
            self.req = None

        def request(self, consumer, type, flags=0):
            self.req = (consumer, type, flags)

        def owner(self):
            return Chip()

        def offset(self):
            return self.off

        def get_value(self):
            return pegel_folge[0]

        def event_wait(self, sec=0, nsec=0):
            return True

        def event_read(self):
            return LineEvent(LineEvent.FALLING_EDGE)
    m.find_line = lambda name: Line(27) if name == "GPIO27" else None
    m.ChipIter = lambda: iter([Chip()])
    return m


def test_gpio_adapter():
    k = S.Konfig({})
    g = gpiod_v1([1])
    l1 = S.GpioLeitung(k, gpiod_mod=g)
    check("GPIO v1: Leitung GPIO27, Flanken beide, Bias aus",
          not l1.v2 and l1.line.off == 27 and l1.line.req == ("k2_stromausfall", 6, 8), str(l1.line.req))
    check("GPIO v1: Pegel + fallende Flanke", l1.pegel() == 1 and l1.warte(0.5) == ["fallend"])
    g.find_line = lambda name: None
    l1b = S.GpioLeitung(k, gpiod_mod=g)
    check("GPIO v1: Rückfall Chip-Label pinctrl-rp1 + Offset 27", l1b.line.off == 27 and "gpiochip0:27" == l1b.ort)
    g2 = gpiod_v1([1], label="anderer")
    g2.find_line = lambda name: None
    try:
        S.GpioLeitung(k, gpiod_mod=g2)
        ok = False
    except OSError:
        ok = True
    check("GPIO v1: nichts gefunden -> OSError", ok)

    # v2
    m = types.ModuleType("gpiod")
    lm = types.SimpleNamespace(
        Direction=types.SimpleNamespace(INPUT="IN"),
        Edge=types.SimpleNamespace(BOTH="BOTH"),
        Bias=types.SimpleNamespace(DISABLED="BIAS_AUS"),
        Value=types.SimpleNamespace(ACTIVE="AKTIV", INACTIVE="INAKTIV"))
    m.line = lm
    angefragt = {}

    class LineSettings(object):
        def __init__(self, **kw):
            self.kw = kw
    m.LineSettings = LineSettings

    class Ev(object):
        class Type(object):
            RISING_EDGE, FALLING_EDGE = "R", "F"

        def __init__(self, t):
            self.event_type = t

    class Req(object):
        def get_value(self, off):
            return "INAKTIV"

        def wait_edge_events(self, td):
            return True

        def read_edge_events(self):
            return [Ev("F"), Ev("R")]

    def request_lines(pfad, consumer, config):
        angefragt.update(pfad=pfad, consumer=consumer, config=config)
        return Req()
    m.request_lines = request_lines

    class Chip(object):
        def __init__(self, p):
            self.p = p

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def line_offset_from_id(self, name):
            if name == "GPIO27":
                return 27
            raise OSError(errno.ENOENT, "nicht da")

        def get_info(self):
            return types.SimpleNamespace(label="pinctrl-rp1")
    m.Chip = Chip
    orig_glob = S.glob.glob
    S.glob.glob = lambda muster: ["/dev/gpiochip0"] if "gpiochip" in muster else orig_glob(muster)
    try:
        l2 = S.GpioLeitung(k, gpiod_mod=m)
    finally:
        S.glob.glob = orig_glob
    ls = angefragt["config"][27]
    check("GPIO v2: request_lines auf Offset 27, Eingang, beide Flanken, Bias aus",
          l2.v2 and angefragt["pfad"] == "/dev/gpiochip0"
          and ls.kw == {"direction": "IN", "edge_detection": "BOTH", "bias": "BIAS_AUS"}, str(ls.kw))
    check("GPIO v2: Pegel inaktiv -> 0, Flanken fallend/steigend",
          l2.pegel() == 0 and l2.warte(0.5) == ["fallend", "steigend"])


def test_py39():
    src = open(os.path.join(HIER, "k2_stromausfall.py"), encoding="utf-8").read()
    try:
        ast.parse(src, feature_version=(3, 9))
        ok = True
    except SyntaxError as exc:
        ok = False
        print(exc)
    check("Python-3.9-Syntax k2_stromausfall.py", ok)
    check("main --probelauf ohne --trocken -> abgelehnt (Exit 2)",
          S.main(["--probelauf"], env={"K2SA_SYSFS": "/gibt/es/nicht"}) == 2)


def main():
    for fn in (test_konfig, test_register, test_ina_hwmon, test_ina_i2c, test_waechter_usbc,
               test_waechter_scharf_hysterese, test_notfall, test_stoerimpuls_und_verpasst,
               test_fehler_im_schritt, test_wiederkehr, test_ohne_ina, test_latch, test_system_aktionen,
               test_gpio_adapter, test_py39):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(fn.__name__ + " (Ausnahme)", False, repr(exc))
    fehler = [n for n, ok in ERG if not ok]
    print("ERGEBNIS: %s (%d/%d Prüfungen ok)" % (
        "ok" if not fehler else "%d FEHLER" % len(fehler), len(ERG) - len(fehler), len(ERG)))
    return 1 if fehler else 0


if __name__ == "__main__":
    raise SystemExit(main())
