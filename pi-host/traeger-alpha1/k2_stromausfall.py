#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_stromausfall – Stromausfall-Wächter für den K2-CM5-Träger Alpha 1.

Hardware (Schaltplan generator/k2_traeger.py, README des Layouts):
  INA226 U4, I2C1 Adresse 0x40, misst den 24-V-Eingang hinter F1 (Shunt R23
  5 mΩ). ALERT (open drain) = PWR_FAIL_N -> GPIO27 (CM5-Pin 48), Pull-up
  R50 10 kΩ auf 3,3 V, Messpad TP12. Aktiv LOW.

Ablauf:
1. Start: Bus-Unterspannungsgrenze setzen (Standard 20 V).
   - Weg A (Standard): hwmon des Treibers ina2xx (Overlay k2-traeger-ina226):
     ``update_interval`` (kleinster Wert, ≈ 2 ms) und ``in1_lcrit`` (mV). Der
     Treiber schreibt Alert-Limit 0x07 = mV / 1,25 (20 V -> 16000 = 0x3E80)
     und im Mask/Enable-Register 0x06 das BUL-Bit (0x1000). ACHTUNG: Der
     Treiber setzt beim Laden ALERT-LATCH EIN (ina2xx.c, ina2xx_init) –
     ALERT bleibt nach einem Einbruch low, bis 0x06 gelesen wird
     (``in1_lcrit_alarm``). Die README des Layouts nennt „ohne Latch“; das gilt
     nur für Weg B.
   - Weg B (kein hwmon): direkt per ``i2ctransfer``: 0x07 = 0x3E80, dann
     0x06 = 0x1000 (BUL, ALERT aktiv low, OHNE Latch, Datenblatt INA226
     Tab. „Mask/Enable Register“), danach 0x06 lesen (löscht alte Flags).
2. Scharf erst, wenn GPIO27 high ist UND der INA226 lesbar ist UND die
   Spannung >= Wiederkehr-Schwelle (Standard 21 V) seit ``K2SA_SCHARF_S`` s
   anliegt. Läuft der CM5 an der Notfall-USB-C-Buchse ohne 24 V, ist ALERT
   dauerhaft low -> nie scharf. Ohne lesbaren INA226 (z. B. CM5 zurück auf dem
   IO-Board, GPIO27 offen) -> nie scharf, kein Fehlauslösen.
3. Fallende Flanke an GPIO27 (libgpiod, Flankenerkennung im Kernel):
   Bestätigung nach ``K2SA_BESTAETIGUNG_MS`` (Pegel noch low? sonst Störimpuls,
   nur Meldung). Dann sofort, jeder Schritt einzeln abgesichert:
   a) Klipper/Moonraker einfrieren (cgroup v2 ``cgroup.freeze`` = 1, ersatzweise
      SIGSTOP) – keine weiteren Schreibzugriffe, Zustand bleibt erhalten;
   b) Ereigniszeile ins Log auf der Datenpartition (ohne fsync);
   c) ``sync()``;
   d) zweite Zeile „sync fertig nach … ms“ + fsync (zeigt am Muster, wie weit
      die Haltezeit reichte);
   e) Datenpartition schreibschützen: ``remount,ro`` (mount(2)); scheitert das
      (EBUSY – eingefrorene Prozesse halten Dateien offen), ``FIFREEZE``: alles
      Gepufferte wird geschrieben, das Journal geleert, weitere Schreiber
      warten;
   f) optional (``K2SA_SYSRQ=1``) Notfall-Sync + Notfall-ro aller Dateisysteme
      über /proc/sysrq-trigger;
   g) Meldung ins Journal.
4. Danach Schutzzustand bis zum Neustart. Kommt die Spannung zurück (meist
   nicht, der CM5 geht vorher aus), wird NUR gemeldet – nichts aufgetaut,
   nichts neu gestartet (Klipper-Zustand ist nach dem Einbruch ohnehin
   ungültig: die MCUs waren stromlos). Zurück: ``k2_stromausfall --freigeben``
   (taut auf, remount rw, Dienste weiter) und dann ``sudo reboot``.

Aufruf:
  k2_stromausfall.py                 Wächter (systemd-Dienst k2-stromausfall)
  k2_stromausfall.py --trocken       Wächter, führt im Ernstfall NICHTS aus,
                                     meldet nur, was geschähe
  k2_stromausfall.py --einrichten    nur INA226-Grenze setzen + Status
  k2_stromausfall.py --status        Spannung, Alarm, GPIO27, Datenpartition
  k2_stromausfall.py --probelauf     Ablauf 3a–g sofort, nur als Trockenlauf
  k2_stromausfall.py --freigeben     Schutzzustand aufheben (vor dem Neustart)

Einstellungen (Umgebung, z. B. /etc/default/k2_stromausfall):
  K2SA_GPIO_NAME=GPIO27  K2SA_GPIO_OFFSET=27  K2SA_GPIO_CHIP=pinctrl-rp1
  K2SA_I2C_BUS=1  K2SA_INA_ADR=0x40  K2SA_GRENZE_MV=20000  K2SA_WIEDER_MV=21000
  K2SA_UPDATE_MS=2  K2SA_BESTAETIGUNG_MS=1  K2SA_SCHARF_S=2  K2SA_WIEDER_S=3
  K2SA_DIENSTE="klipper.service moonraker.service"  K2SA_DATEN=/daten
  K2SA_LOG=<DATEN>/printer_data/logs/k2_stromausfall.log (fehlt der Ordner:
  K2SA_LOG_ERSATZ=/var/log/k2_stromausfall.log)  K2SA_SYSRQ=0
  K2SA_CGROUP=/sys/fs/cgroup/system.slice  K2SA_SYSFS=/sys

Benötigt: python3, python3-libgpiod (1.x Bookworm oder 2.x Trixie),
i2c-tools (nur Weg B). Läuft als root. Python 3.9-kompatibel.
Offline-Test: python3 test_k2_stromausfall.py
"""

import errno
import glob
import os
import signal
import subprocess
import sys
import time

FIFREEZE = 0xC0045877   # _IOWR('X', 119, int), linux/fs.h
FITHAW = 0xC0045878     # _IOWR('X', 120, int)
MS_RDONLY = 1
MS_REMOUNT = 32
INA226_BUS_LSB_MV = 1.25          # Datenblatt INA226: Bus-Spannung 1,25 mV/LSB
INA226_REG_BUS = 0x02
INA226_REG_MASK = 0x06
INA226_REG_LIMIT = 0x07
INA226_MASK_BUL = 0x1000          # Bus Under-Voltage, ALERT aktiv low, LEN = 0

STANDARD = {
    "K2SA_GPIO_NAME": "GPIO27",
    "K2SA_GPIO_OFFSET": "27",
    "K2SA_GPIO_CHIP": "pinctrl-rp1",
    "K2SA_I2C_BUS": "1",
    "K2SA_INA_ADR": "0x40",
    "K2SA_GRENZE_MV": "20000",
    "K2SA_WIEDER_MV": "21000",
    "K2SA_UPDATE_MS": "2",
    "K2SA_BESTAETIGUNG_MS": "1",
    "K2SA_SCHARF_S": "2",
    "K2SA_WIEDER_S": "3",
    "K2SA_DIENSTE": "klipper.service moonraker.service",
    "K2SA_DATEN": "/daten",
    "K2SA_LOG": "",
    "K2SA_LOG_ERSATZ": "/var/log/k2_stromausfall.log",
    "K2SA_SYSRQ": "0",
    "K2SA_CGROUP": "/sys/fs/cgroup/system.slice",
    "K2SA_SYSFS": "/sys",
}


class KonfigFehler(ValueError):
    pass


class Konfig(object):
    """Einstellungen aus der Umgebung (EnvironmentFile) mit Prüfung."""

    def __init__(self, env=None):
        quelle = dict(STANDARD)
        env = os.environ if env is None else env
        for k in STANDARD:
            if env.get(k, "") != "":
                quelle[k] = env[k]
        try:
            self.gpio_name = quelle["K2SA_GPIO_NAME"].strip()
            self.gpio_offset = int(quelle["K2SA_GPIO_OFFSET"], 0)
            self.gpio_chip = quelle["K2SA_GPIO_CHIP"].strip()
            self.i2c_bus = int(quelle["K2SA_I2C_BUS"], 0)
            self.ina_adr = int(quelle["K2SA_INA_ADR"], 0)
            self.grenze_mv = int(quelle["K2SA_GRENZE_MV"], 0)
            self.wieder_mv = int(quelle["K2SA_WIEDER_MV"], 0)
            self.update_ms = int(quelle["K2SA_UPDATE_MS"], 0)
            self.bestaetigung_s = float(quelle["K2SA_BESTAETIGUNG_MS"]) / 1000.0
            self.scharf_s = float(quelle["K2SA_SCHARF_S"])
            self.wieder_s = float(quelle["K2SA_WIEDER_S"])
            self.sysrq = quelle["K2SA_SYSRQ"].strip() in ("1", "ja", "true", "yes")
        except ValueError as exc:
            raise KonfigFehler("Einstellung ungültig: %s" % (exc,))
        self.dienste = [d for d in quelle["K2SA_DIENSTE"].split() if d]
        self.daten = quelle["K2SA_DATEN"].rstrip("/") or "/"
        self.log = (quelle["K2SA_LOG"].strip()
                    or os.path.join(self.daten, "printer_data", "logs",
                                    "k2_stromausfall.log"))
        self.log_ersatz = quelle["K2SA_LOG_ERSATZ"].strip()
        self.cgroup = quelle["K2SA_CGROUP"]
        self.sysfs = quelle["K2SA_SYSFS"]
        if not 0x40 <= self.ina_adr <= 0x4F:
            raise KonfigFehler("K2SA_INA_ADR 0x%x außerhalb 0x40–0x4F" % self.ina_adr)
        if not 1000 <= self.grenze_mv <= 36000:
            raise KonfigFehler("K2SA_GRENZE_MV %d außerhalb 1000–36000 (INA226 VBUS)"
                               % self.grenze_mv)
        if self.wieder_mv <= self.grenze_mv:
            raise KonfigFehler("K2SA_WIEDER_MV muss über K2SA_GRENZE_MV liegen "
                               "(Hysterese)")
        if not 0.0 <= self.bestaetigung_s <= 0.05:
            raise KonfigFehler("K2SA_BESTAETIGUNG_MS 0–50")
        for d in self.dienste:
            if "/" in d or d.startswith("."):
                raise KonfigFehler("K2SA_DIENSTE: ungültiger Name %r" % d)


def grenze_register(mv):
    """Alert-Limit-Register (0x07) für eine Bus-Spannung in mV."""
    wert = int(round(mv / INA226_BUS_LSB_MV))
    if not 0 <= wert <= 0x7FFF:
        raise KonfigFehler("Grenze %d mV nicht darstellbar" % mv)
    return wert


# ---------------------------------------------------------------------
# INA226 0x40
# ---------------------------------------------------------------------

class Ina226(object):
    """INA226 über hwmon (Weg A) oder i2ctransfer (Weg B)."""

    def __init__(self, kfg, run=None):
        self.kfg = kfg
        self.run = run or self._run
        self.hwmon = self._finde_hwmon()

    @staticmethod
    def _run(argv):
        return subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=5)

    def _finde_hwmon(self):
        basis = os.path.join(self.kfg.sysfs, "bus", "i2c", "devices",
                             "%d-%04x" % (self.kfg.i2c_bus, self.kfg.ina_adr), "hwmon")
        for d in sorted(glob.glob(os.path.join(basis, "hwmon*"))):
            if os.path.isfile(os.path.join(d, "in1_input")):
                return d
        return None

    def _lies(self, name):
        with open(os.path.join(self.hwmon, name)) as f:
            return f.read().strip()

    def _schreib(self, name, wert):
        with open(os.path.join(self.hwmon, name), "w") as f:
            f.write("%s\n" % (wert,))

    def _i2c(self, *teile):
        argv = ["i2ctransfer", "-f", "-y", str(self.kfg.i2c_bus)] + list(teile)
        r = self.run(argv)
        if r.returncode != 0:
            raise OSError("i2ctransfer: %s" % ((r.stderr or "").strip() or r.returncode))
        return (r.stdout or "").split()

    def einrichten(self):
        """Grenze setzen, alte Alarme löschen. Rückgabe: Beschreibung."""
        kfg = self.kfg
        reg = grenze_register(kfg.grenze_mv)
        if self.hwmon:
            try:
                self._schreib("update_interval", kfg.update_ms)
            except OSError:
                pass   # nicht kritisch (Standard-Mittelung ≈ 35 ms)
            self._schreib("in1_lcrit", kfg.grenze_mv)
            zurueck = int(self._lies("in1_lcrit"))
            if abs(zurueck - kfg.grenze_mv) > 2:
                raise OSError("in1_lcrit Rückprüfung %d mV statt %d mV"
                              % (zurueck, kfg.grenze_mv))
            alarm = self._lies("in1_lcrit_alarm")   # liest 0x06 -> Latch gelöscht
            try:
                intervall = self._lies("update_interval")
            except OSError:
                intervall = "?"
            return ("hwmon %s: in1_lcrit=%d mV (Register 0x07=0x%04X), "
                    "update_interval=%s ms, alter Alarm=%s (gelöscht), Latch AN (Treiber)"
                    % (os.path.basename(self.hwmon), zurueck, reg, intervall, alarm))
        adr = "w3@0x%02x" % kfg.ina_adr
        self._i2c(adr, "0x%02x" % INA226_REG_LIMIT, "0x%02x" % (reg >> 8), "0x%02x" % (reg & 0xFF))
        self._i2c(adr, "0x%02x" % INA226_REG_MASK, "0x%02x" % (INA226_MASK_BUL >> 8),
                  "0x%02x" % (INA226_MASK_BUL & 0xFF))
        self._i2c("w1@0x%02x" % kfg.ina_adr, "0x%02x" % INA226_REG_MASK, "r2")
        return ("i2ctransfer: 0x07=0x%04X (%d mV), 0x06=0x%04X (BUL, ohne Latch)"
                % (reg, kfg.grenze_mv, INA226_MASK_BUL))

    def alarm_loeschen(self):
        """Mask/Enable (0x06) lesen -> gelatchter ALERT wird frei. Nur lesend."""
        try:
            if self.hwmon:
                return self._lies("in1_lcrit_alarm")
            return " ".join(self._i2c("w1@0x%02x" % self.kfg.ina_adr,
                                      "0x%02x" % INA226_REG_MASK, "r2"))
        except (OSError, subprocess.SubprocessError):
            return None

    def spannung_mv(self):
        """Bus-Spannung in mV oder None (I2C/hwmon nicht lesbar)."""
        try:
            if self.hwmon:
                return int(self._lies("in1_input"))
            teile = self._i2c("w1@0x%02x" % self.kfg.ina_adr, "0x%02x" % INA226_REG_BUS, "r2")
            roh = (int(teile[0], 16) << 8) | int(teile[1], 16)
            return int(round(roh * INA226_BUS_LSB_MV))
        except (OSError, ValueError, IndexError, subprocess.SubprocessError):
            return None


# ---------------------------------------------------------------------
# GPIO27 über libgpiod (v2 = Trixie, v1 = Bookworm)
# ---------------------------------------------------------------------

class GpioLeitung(object):
    """Eingang mit Flankenerkennung beider Richtungen, interner Pull AUS
    (R50 10 kΩ zieht hoch; RP1-Standard wäre Pull-down)."""

    def __init__(self, kfg, gpiod_mod=None):
        if gpiod_mod is None:
            import gpiod as gpiod_mod   # python3-libgpiod
        self.g = gpiod_mod
        self.kfg = kfg
        self.v2 = hasattr(gpiod_mod, "request_lines")
        if self.v2:
            self._oeffne_v2()
        else:
            self._oeffne_v1()

    # --- v2 -----------------------------------------------------------
    def _oeffne_v2(self):
        g, kfg = self.g, self.kfg
        from datetime import timedelta
        self._td = timedelta
        pfad, off = None, None
        for p in sorted(glob.glob("/dev/gpiochip*")):
            try:
                with g.Chip(p) as chip:
                    try:
                        off = chip.line_offset_from_id(kfg.gpio_name)
                        pfad = p
                        break
                    except (OSError, ValueError):
                        if chip.get_info().label == kfg.gpio_chip:
                            pfad, off = p, kfg.gpio_offset
            except OSError:
                continue
        if pfad is None:
            raise OSError("GPIO %s / %s:%d nicht gefunden"
                          % (kfg.gpio_name, kfg.gpio_chip, kfg.gpio_offset))
        ls = g.LineSettings(direction=g.line.Direction.INPUT,
                            edge_detection=g.line.Edge.BOTH,
                            bias=g.line.Bias.DISABLED)
        self.off = off
        self.ort = "%s:%d" % (pfad, off)
        self.req = g.request_lines(pfad, consumer="k2_stromausfall", config={off: ls})

    # --- v1 -----------------------------------------------------------
    def _oeffne_v1(self):
        g, kfg = self.g, self.kfg
        line = g.find_line(kfg.gpio_name) if hasattr(g, "find_line") else None
        if line is None:
            for chip in g.ChipIter():
                if chip.label() == kfg.gpio_chip:
                    line = chip.get_line(kfg.gpio_offset)
                    break
        if line is None:
            raise OSError("GPIO %s / %s:%d nicht gefunden"
                          % (kfg.gpio_name, kfg.gpio_chip, kfg.gpio_offset))
        flags = getattr(g, "LINE_REQ_FLAG_BIAS_DISABLE", 0)
        line.request(consumer="k2_stromausfall", type=g.LINE_REQ_EV_BOTH_EDGES, flags=flags)
        self.line = line
        self.ort = "%s:%d" % (line.owner().name(), line.offset())

    # --- gemeinsam ----------------------------------------------------
    def pegel(self):
        if self.v2:
            return 1 if self.req.get_value(self.off) == self.g.line.Value.ACTIVE else 0
        return int(self.line.get_value())

    def warte(self, timeout_s):
        """Liste der Flanken ('fallend'/'steigend') bis timeout_s."""
        aus = []
        if self.v2:
            if self.req.wait_edge_events(self._td(seconds=timeout_s)):
                for ev in self.req.read_edge_events():
                    fall = ev.event_type == ev.Type.FALLING_EDGE
                    aus.append("fallend" if fall else "steigend")
            return aus
        sek = int(timeout_s)
        if self.line.event_wait(sec=sek, nsec=int((timeout_s - sek) * 1e9)):
            ev = self.line.event_read()
            aus.append("fallend" if ev.type == self.g.LineEvent.FALLING_EDGE else "steigend")
        return aus


# ---------------------------------------------------------------------
# Systemaktionen
# ---------------------------------------------------------------------

def _libc_mount(target, flags):
    import ctypes
    import ctypes.util
    libc = ctypes.CDLL(ctypes.util.find_library("c") or "libc.so.6", use_errno=True)
    if libc.mount(None, target.encode(), None, ctypes.c_ulong(flags), None) != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))


def _ioctl_pfad(pfad, req):
    import fcntl
    fd = os.open(pfad, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        fcntl.ioctl(fd, req, 0)
    finally:
        os.close(fd)


class SystemAktionen(object):
    """Echte Aktionen; Funktionen für Tests austauschbar."""

    def __init__(self, kfg, leitung=None, mount=None, ioctl_pfad=None, kill=None,
                 ausgabe=None, ismount=None, run=None, ina=None):
        self.kfg = kfg
        self.leitung = leitung
        self.ina = ina
        self.mount = mount or _libc_mount
        self.ioctl_pfad = ioctl_pfad or _ioctl_pfad
        self.kill = kill or os.kill
        self.ausgabe = ausgabe or (lambda t: (sys.stdout.write(t + "\n"), sys.stdout.flush()))
        self.ismount = ismount or os.path.ismount
        self.run = run or (lambda argv: subprocess.run(argv, timeout=20))

    def pegel(self):
        return self.leitung.pegel()

    def alarm_loeschen(self):
        return self.ina.alarm_loeschen() if self.ina is not None else None

    def schlafe(self, s):
        if s > 0:
            time.sleep(s)

    def melde(self, text):
        self.ausgabe("k2_stromausfall: " + text)

    def _cg(self, unit):
        return os.path.join(self.kfg.cgroup, unit)

    def dienste_einfrieren(self, wert=1):
        erg = []
        for unit in self.kfg.dienste:
            d = self._cg(unit)
            frz = os.path.join(d, "cgroup.freeze")
            try:
                if os.path.isfile(frz):
                    with open(frz, "w") as f:
                        f.write("%d\n" % wert)
                    erg.append("%s:%s" % (unit, "eingefroren" if wert else "aufgetaut"))
                    continue
                procs = os.path.join(d, "cgroup.procs")
                if not os.path.isfile(procs):
                    erg.append("%s:fehlt" % unit)
                    continue
                with open(procs) as f:
                    pids = [int(x) for x in f.read().split()]
                sig = signal.SIGSTOP if wert else signal.SIGCONT
                for pid in pids:
                    try:
                        self.kill(pid, sig)
                    except OSError:
                        pass
                erg.append("%s:%s(%d)" % (unit, "SIGSTOP" if wert else "SIGCONT", len(pids)))
            except OSError as exc:
                erg.append("%s:fehler(%s)" % (unit, exc))
        return ", ".join(erg) or "keine Dienste"

    def dienste_auftauen(self):
        return self.dienste_einfrieren(0)

    def log_daten(self, text, fsync=False):
        pfad = self.kfg.log
        if not os.path.isdir(os.path.dirname(pfad)):
            # vor dem Umzug auf die Datenpartition (k2_readonly.sh umziehen)
            pfad = self.kfg.log_ersatz
            if not pfad or not os.path.isdir(os.path.dirname(pfad)):
                return False
        fd = os.open(pfad, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (time.strftime("%Y-%m-%d %H:%M:%S ") + text + "\n").encode("utf-8"))
            if fsync:
                os.fsync(fd)
        finally:
            os.close(fd)
        return True

    def sync(self):
        os.sync()

    def daten_schuetzen(self):
        ziel = self.kfg.daten
        if not self.ismount(ziel):
            return "keine Datenpartition unter %s (nur sync)" % ziel
        try:
            self.mount(ziel, MS_REMOUNT | MS_RDONLY)
            return "%s remount,ro" % ziel
        except OSError as exc:
            grund = errno.errorcode.get(exc.errno, str(exc.errno))
        try:
            self.ioctl_pfad(ziel, FIFREEZE)
            return "%s eingefroren (FIFREEZE; remount,ro: %s)" % (ziel, grund)
        except OSError as exc:
            if exc.errno == errno.EBUSY:
                return "%s war schon eingefroren" % ziel
            return "%s NICHT geschützt (remount: %s, FIFREEZE: %s)" % (ziel, grund, exc)

    def daten_freigeben(self):
        ziel = self.kfg.daten
        teile = []
        if self.ismount(ziel):
            try:
                self.ioctl_pfad(ziel, FITHAW)
                teile.append("aufgetaut")
            except OSError as exc:
                teile.append("nicht eingefroren" if exc.errno == errno.EINVAL
                             else "FITHAW: %s" % exc)
            r = self.run(["mount", "-o", "remount,rw", ziel])
            teile.append("remount,rw ok" if r.returncode == 0 else "remount,rw FEHLER")
        else:
            teile.append("keine Datenpartition")
        return "%s: %s" % (ziel, ", ".join(teile))

    def sysrq(self):
        with open("/proc/sysrq-trigger", "w") as f:
            f.write("s")
        with open("/proc/sysrq-trigger", "w") as f:
            f.write("u")
        return "sysrq s+u"


class TrockenAktionen(object):
    """Wie SystemAktionen, schreibt/friert aber NICHTS – meldet nur."""

    def __init__(self, echt):
        self.echt = echt

    def pegel(self):
        return self.echt.pegel()

    def alarm_loeschen(self):
        return self.echt.alarm_loeschen()   # nur Register lesen – harmlos

    def schlafe(self, s):
        self.echt.schlafe(s)

    def melde(self, text):
        self.echt.melde("[TROCKEN] " + text)

    def dienste_einfrieren(self):
        return "würde einfrieren: %s" % " ".join(self.echt.kfg.dienste)

    def log_daten(self, text, fsync=False):
        self.melde("würde loggen%s: %s" % (" (fsync)" if fsync else "", text))
        return False

    def sync(self):
        pass

    def daten_schuetzen(self):
        return "würde %s remount,ro bzw. FIFREEZE" % self.echt.kfg.daten

    def sysrq(self):
        return "würde sysrq s+u"


# ---------------------------------------------------------------------
# Zustandsmaschine (reine Logik, offline getestet)
# ---------------------------------------------------------------------

class Waechter(object):
    WARTEN, SCHARF, SCHUTZ = "warten", "scharf", "schutz"

    def __init__(self, kfg, akt, uhr=None, neu_scharf_nach_wiederkehr=False):
        self.kfg = kfg
        self.akt = akt
        self.uhr = uhr or time.monotonic
        # nur Trockenlauf: nach gemeldeter Wiederkehr wieder WARTEN
        self.neu_scharf = neu_scharf_nach_wiederkehr
        self.zustand = self.WARTEN
        self.gut_seit = None
        self.wieder_seit = None
        self.wieder_gemeldet = False
        self.letzter_grund = None
        self.notfaelle = 0
        self.stoerimpulse = 0
        self.protokoll = []

    def takt(self, pegel, spannung_mv):
        jetzt = self.uhr()
        if self.zustand == self.WARTEN:
            if pegel == 1 and spannung_mv is not None and spannung_mv >= self.kfg.wieder_mv:
                if self.gut_seit is None:
                    self.gut_seit = jetzt
                if jetzt - self.gut_seit >= self.kfg.scharf_s:
                    self.zustand = self.SCHARF
                    self.letzter_grund = None
                    self.akt.melde("scharf (GPIO27 high, %.2f V)" % (spannung_mv / 1000.0))
            else:
                self.gut_seit = None
                if (pegel == 0 and spannung_mv is not None
                        and spannung_mv >= self.kfg.wieder_mv):
                    # Treiber-Latch: ALERT hängt von einem früheren Einbruch
                    # (z. B. Start an USB-C ohne 24 V) – Register lesen gibt frei
                    self.akt.alarm_loeschen()
                if spannung_mv is None:
                    grund = "INA226 0x%02x nicht lesbar (kein Träger? Overlay?)" % self.kfg.ina_adr
                elif pegel == 0:
                    grund = "ALERT low (keine 24 V? USB-C-Notversorgung?)"
                else:
                    grund = "Eingang %.2f V < %.2f V" % (spannung_mv / 1000.0,
                                                         self.kfg.wieder_mv / 1000.0)
                if grund.split(" ")[0] != (self.letzter_grund or "").split(" ")[0]:
                    self.akt.melde("nicht scharf: " + grund)
                self.letzter_grund = grund
        elif self.zustand == self.SCHARF:
            if pegel == 0:
                self.flanke()      # verpasste Flanke (Abfrage im Takt)
        elif self.zustand == self.SCHUTZ:
            if spannung_mv is not None and spannung_mv >= self.kfg.wieder_mv:
                if self.wieder_seit is None:
                    self.wieder_seit = jetzt
                if (not self.wieder_gemeldet
                        and jetzt - self.wieder_seit >= self.kfg.wieder_s):
                    self.wieder_gemeldet = True
                    self.akt.melde(
                        "24 V wieder da (%.2f V). Schutzzustand BLEIBT (nichts "
                        "aufgetaut, kein Neustart). Weiter: sudo k2_stromausfall "
                        "--freigeben && sudo reboot" % (spannung_mv / 1000.0))
                    if self.neu_scharf:
                        self.zustand = self.WARTEN
                        self.wieder_seit = None
                        self.wieder_gemeldet = False
                        self.akt.alarm_loeschen()
            else:
                self.wieder_seit = None

    def flanke(self):
        """Fallende Flanke an GPIO27. True = Notfall ausgelöst."""
        if self.zustand != self.SCHARF:
            return False
        self.akt.schlafe(self.kfg.bestaetigung_s)
        if self.akt.pegel() != 0:
            self.stoerimpulse += 1
            self.akt.melde("Störimpuls an GPIO27 ignoriert (%d)" % self.stoerimpulse)
            return False
        self.notfall()
        return True

    def _schritt(self, name, fn, *args):
        t = self.uhr()
        try:
            erg = fn(*args)
        except Exception as exc:  # jeder Schritt einzeln – Rest läuft weiter
            erg = "FEHLER %s: %s" % (type(exc).__name__, exc)
        self.protokoll.append((name, erg, (self.uhr() - t) * 1000.0))
        return erg

    def notfall(self):
        t0 = self.uhr()
        self.zustand = self.SCHUTZ
        self.notfaelle += 1
        self.protokoll = []
        einfrieren = self._schritt("einfrieren", self.akt.dienste_einfrieren)
        self._schritt("log1", self.akt.log_daten,
                      "STROMAUSFALL erkannt (GPIO27 low, < %.1f V); %s"
                      % (self.kfg.grenze_mv / 1000.0, einfrieren), False)
        self._schritt("sync", self.akt.sync)
        self._schritt("log2", self.akt.log_daten,
                      "sync fertig nach %.1f ms" % ((self.uhr() - t0) * 1000.0), True)
        self._schritt("schuetzen", self.akt.daten_schuetzen)
        if self.kfg.sysrq:
            self._schritt("sysrq", self.akt.sysrq)
        gesamt = (self.uhr() - t0) * 1000.0
        self.akt.melde("SCHUTZZUSTAND nach %.1f ms: %s" % (gesamt, "; ".join(
            "%s=%s (%.1f ms)" % (n, e, d) for n, e, d in self.protokoll)))
        return gesamt


# ---------------------------------------------------------------------
# Hauptprogramm
# ---------------------------------------------------------------------

def _daten_status(kfg):
    if not os.path.ismount(kfg.daten):
        return "%s: keine eigene Partition" % kfg.daten
    with open("/proc/self/mounts") as f:
        for z in f:
            t = z.split()
            if len(t) >= 4 and t[1] == kfg.daten:
                return "%s: %s %s (%s)" % (kfg.daten, t[0], t[2], t[3])
    return "%s: eingehängt" % kfg.daten


def main(argv=None, env=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    trocken = "--trocken" in argv
    try:
        kfg = Konfig(env)
    except KonfigFehler as exc:
        print("k2_stromausfall: %s" % exc, file=sys.stderr)
        return 2
    ina = Ina226(kfg)
    if "--status" in argv:
        print("INA226 0x%02x: %s, Spannung %s mV" % (
            kfg.ina_adr, ina.hwmon or "ohne hwmon (i2ctransfer)", ina.spannung_mv()))
        try:
            leitung = GpioLeitung(kfg)
            print("GPIO27 (%s): Pegel %d" % (leitung.ort, leitung.pegel()))
        except Exception as exc:  # Status darf nie abbrechen
            print("GPIO27: nicht lesbar (%s)" % exc)
        print(_daten_status(kfg))
        return 0
    if "--freigeben" in argv:
        akt = SystemAktionen(kfg)
        print("k2_stromausfall: " + akt.daten_freigeben())
        print("k2_stromausfall: " + akt.dienste_auftauen())
        print("k2_stromausfall: jetzt neu starten: sudo reboot")
        return 0
    if "--einrichten" in argv:
        print("k2_stromausfall: " + ina.einrichten())
        return 0
    if "--probelauf" in argv:
        if not trocken:
            print("k2_stromausfall: --probelauf nur zusammen mit --trocken", file=sys.stderr)
            return 2
        akt = TrockenAktionen(SystemAktionen(kfg, leitung=None, ina=ina))
        w = Waechter(kfg, akt)
        w.zustand = Waechter.SCHARF
        w.notfall()
        return 0

    leitung = GpioLeitung(kfg)
    echt = SystemAktionen(kfg, leitung=leitung, ina=ina)
    akt = TrockenAktionen(echt) if trocken else echt
    try:
        akt.melde("INA226: " + ina.einrichten())
    except Exception as exc:
        akt.melde("INA226 NICHT eingerichtet (%s) – ALERT kommt so nicht; "
                  "Überwachung läuft trotzdem" % exc)
    akt.melde("überwache %s, Daten %s, Log %s%s" % (
        leitung.ort, kfg.daten, kfg.log, " (TROCKEN)" if trocken else ""))
    w = Waechter(kfg, akt, neu_scharf_nach_wiederkehr=trocken)
    naechste_spannung = 0.0
    spannung = None
    while True:
        for ev in leitung.warte(0.5):
            if ev == "fallend":
                w.flanke()
        jetzt = time.monotonic()
        if w.zustand != Waechter.SCHARF and jetzt >= naechste_spannung:
            spannung = ina.spannung_mv()
            naechste_spannung = jetzt + 1.0
        w.takt(leitung.pegel(), spannung if w.zustand != Waechter.SCHARF else None)


if __name__ == "__main__":
    sys.exit(main())
