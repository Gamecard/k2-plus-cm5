#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline-Test für k2-identify.py (ohne Drucker, ohne echte Ports).

Teil A  Auswertung (Main/Düse/unklar) mit den bekannten Dictionaries.
Teil B  Ende-zu-Ende als Unterprozess mit Fake-``serialhdl``/``serial``/``reactor``
        (K2_IDENTIFY_KLIPPY): Main, Düse, stumm, gesperrt, belegt (/proc),
        Fremd-Firmware, Baudliste, Gesamt-Timeout, --ausgabe-rolle, Fehlerfälle.
Teil C  (optional) ECHTES Klipper-``serialhdl`` gegen eine Fake-MCU an einem pty:
        prüft, dass nur ``identify`` gesendet wird. Braucht $KLIPPER_SRC mit
        gebautem chelper und greenlet/cffi/pyserial im laufenden Python, sonst SKIP.

Aufruf: python3 test_k2_identify.py   (Dicts: $K2_MCU_DICT_DIR, ./mcu-dicts oder
        ../../lokal/mcu-dicts)
"""

import importlib.util
import json
import os
import select
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zlib

HIER = os.path.dirname(os.path.abspath(__file__))
SKRIPT = os.path.join(HIER, "k2-identify.py")
FEHLER = []
N_OK = [0]


def check(bed, text):
    if bed:
        N_OK[0] += 1
        print("  ok    %s" % text)
    else:
        FEHLER.append(text)
        print("  FEHLT %s" % text)


def lade_modul():
    spec = importlib.util.spec_from_file_location("k2_identify", SKRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def dict_dir():
    for d in (os.environ.get("K2_MCU_DICT_DIR"), os.path.join(HIER, "mcu-dicts"),
              os.path.join(HIER, "..", "..", "lokal", "mcu-dicts")):
        if d and os.path.isfile(os.path.join(d, "mcu-main-F008-GD32F303RET6.dict.json")):
            return os.path.abspath(d)
    return None


DD = dict_dir()
if DD is None:
    # Im öffentlichen Repo liegen die F008-Dictionaries nicht bei -> sauber
    # überspringen statt fehlschlagen (im Pi-Paket sind sie enthalten).
    print("ÜBERSPRUNGEN: MCU-Dictionaries nicht gefunden (K2_MCU_DICT_DIR setzen)")
    sys.exit(0)
MAIN = os.path.join(DD, "mcu-main-F008-GD32F303RET6.dict.json")
DUESE = os.path.join(DD, "noz-duese-F008-GD32F303CBT6.dict.json")


def info_aus_dict(path):
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    return {"constants": d["config"], "version": d["version"],
            "commands": list(d["commands"]), "responses": list(d["responses"]),
            "enumerations": d.get("enumerations", {})}


# ---------------------------------------------------------------------
def teil_a(m):
    print("Teil A: Auswertung")
    refs = m.referenzen_laden([DD], [os.path.join(HIER, "..", "prtouch_mcu_formats.json"),
                                     os.path.join(HIER, "pi-host", "prtouch_mcu_formats.json")])
    check(len(refs) >= 4, "Referenzen geladen (%d)" % len(refs))
    b = m.auswerten(info_aus_dict(MAIN), refs)
    check(b["rolle"] == "main", "Main-Dict -> main (%s)" % "; ".join(b["gruende"]))
    check(any("MCU=gd32f303xe" in g for g in b["gruende"]), "Main: Grund MCU-Konstante")
    check(any("PD" in g for g in b["gruende"]), "Main: Grund PD-Pins")
    check(any("bekannter Stand" in g for g in b["gruende"]), "Main: Firmware-Stand erkannt")
    check(any("trennt nicht" in h for h in b["hinweise"]), "Main: Hinweis Befehlssatz identisch")
    b = m.auswerten(info_aus_dict(DUESE), refs)
    check(b["rolle"] == "duese", "Düsen-Dict -> duese (%s)" % "; ".join(b["gruende"]))
    check(any("48-Pin" in g for g in b["gruende"]), "Düse: Grund nur PA..PC")
    f012 = os.path.join(DD, "mcu-main-F012-GD32F303RET6.dict.json")
    if os.path.isfile(f012):
        b = m.auswerten(info_aus_dict(f012), refs)
        check(b["rolle"] == "main", "Main F012 (andere Version) -> main")
    # Nur eingebaute Referenzen (Paket ohne Dicts)
    b = m.auswerten(info_aus_dict(DUESE), m.referenzen_laden([], []))
    check(b["rolle"] == "duese", "Düse auch nur mit eingebauten Referenzen")
    # Widerspruch: Main-Version, aber MCU-Konstante der Düse
    i = info_aus_dict(MAIN)
    i["constants"] = dict(i["constants"], MCU="gd32f303xb")
    b = m.auswerten(i, refs)
    check(b["rolle"] is None and any("WIDERSPRUCH" in h for h in b["hinweise"]),
          "Widerspruch -> unklar mit Hinweis")
    # Fremd-Firmware
    fremd = {"constants": {"MCU": "stm32f103", "CLOCK_FREQ": 72000000},
             "version": "v0.12.0-xyz", "commands": ["config_stepper oid=%c", "get_config"],
             "responses": [], "enumerations": {}}
    b = m.auswerten(fremd, refs)
    check(b["rolle"] is None and any("kein K2-Typ" in h for h in b["hinweise"])
          and any("Fremd" in h for h in b["hinweise"]), "Fremd-Firmware -> unklar, Hinweise")


# ---------------------------------------------------------------------
FAKE_SERIAL = r'''
import json, os
class SerialException(IOError):
    pass
def _beh(port):
    m = json.load(open(os.environ["FAKE_K2_MAP"]))
    return m.get(os.path.basename(os.path.realpath(port)), {"art": "stumm"})
def _log(t):
    with open(os.environ["FAKE_K2_LOG"], "a") as fh:
        fh.write(t + "\n")
class Serial:
    def __init__(self, port=None, baudrate=9600, timeout=None, exclusive=None, **kw):
        self.port, self.baudrate, self.exclusive, self.rts, self.is_open = port, baudrate, exclusive, None, False
    def open(self):
        b = _beh(self.port)
        if b["art"] == "gesperrt":
            raise SerialException("Could not exclusively lock port %s: [Errno 11] Resource temporarily unavailable" % self.port)
        if b["art"] == "kaputt":
            raise SerialException("[Errno 13] could not open port %s: Permission denied" % self.port)
        _log("open %s %d exclusive=%s rts=%s" % (os.path.basename(os.path.realpath(self.port)), self.baudrate, self.exclusive, self.rts))
        self.is_open = True
    def close(self):
        _log("close %s" % os.path.basename(os.path.realpath(self.port)))
        self.is_open = False
'''
FAKE_REACTOR = r'''
import time
class _Comp:
    def __init__(self, v): self.v = v
    def wait(self, waketime=None, waketime_result=None): return self.v
class Reactor:
    NEVER = 9999999999999999.
    def __init__(self): self.cbs = []; self.ende = False
    def monotonic(self): return time.monotonic()
    def register_callback(self, cb, waketime=0):
        self.cbs.append(cb); return _Comp(None)
    def run(self):
        while self.cbs and not self.ende:
            self.cbs.pop(0)(self.monotonic())
    def end(self): self.ende = True
'''
FAKE_SERIALHDL = r'''
import json, os, time
from serial import _beh, _log
class _Parser:
    def __init__(self, d):
        self.d = d
    def get_version_info(self): return self.d["version"], self.d.get("build_versions", "")
    def get_messages(self):
        out = [(i, "command", f) for f, i in self.d["commands"].items()]
        out += [(i, "response", f) for f, i in self.d["responses"].items()]
        return out
    def get_constants(self): return dict(self.d["config"])
    def get_enumerations(self): return dict(self.d.get("enumerations", {}))
class SerialReader:
    def __init__(self, reactor, mcu_name=""):
        self.reactor = reactor; self.dev = None; self.mp = None
    def _start_session(self, dev):
        self.dev = dev
        b = _beh(dev.port)
        _log("identify %s %d" % (os.path.basename(os.path.realpath(dev.port)), dev.baudrate))
        # Timeout-Hülle des Skripts muss greifen: register_callback().wait()
        c = self.reactor.register_callback(lambda e: None)
        c.wait(self.reactor.monotonic() + 5.)
        if b["art"] == "haengt":
            time.sleep(120)
        if b["art"] in ("stumm",) or b.get("baud", dev.baudrate) != dev.baudrate:
            self.disconnect(); return False
        self.mp = _Parser(json.load(open(b["dict"])))
        return True
    def get_msgparser(self): return self.mp
    def disconnect(self):
        if self.dev is not None:
            self.dev.close(); self.dev = None
'''


def b_lauf(tmp, args, timeout=60, env_extra=None):
    env = dict(os.environ, K2_IDENTIFY_KLIPPY=os.path.join(tmp, "klippy"),
               K2_IDENTIFY_BYID_DIR=os.path.join(tmp, "by-id"),
               K2_IDENTIFY_REEXEC="1", FAKE_K2_MAP=os.path.join(tmp, "map.json"),
               FAKE_K2_LOG=os.path.join(tmp, "log.txt"), K2_MCU_DICT_DIR=DD,
               CONFIG_DIR=os.path.join(tmp, "keine-config"))
    env.update(env_extra or {})
    t0 = time.monotonic()
    p = subprocess.run([sys.executable, "-B", SKRIPT] + args, env=env, cwd=tmp,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    return p.returncode, p.stdout.decode(), p.stderr.decode(), time.monotonic() - t0


def b_aufbau(tmp, geraete):
    """geraete: {name: verhalten}; legt dev/<name> + by-id/usb-FAKE_<name> an."""
    for d in ("klippy", "dev", "by-id"):
        shutil.rmtree(os.path.join(tmp, d), ignore_errors=True)
        os.makedirs(os.path.join(tmp, d))
    for name, txt in (("serial.py", FAKE_SERIAL), ("reactor.py", FAKE_REACTOR),
                      ("serialhdl.py", FAKE_SERIALHDL)):
        with open(os.path.join(tmp, "klippy", name), "w") as fh:
            fh.write(txt)
    for name in geraete:
        open(os.path.join(tmp, "dev", name), "w").close()
        os.symlink(os.path.join(tmp, "dev", name),
                   os.path.join(tmp, "by-id", "usb-FAKE_%s-if00-port0" % name))
    with open(os.path.join(tmp, "map.json"), "w") as fh:
        json.dump(geraete, fh)
    open(os.path.join(tmp, "log.txt"), "w").close()


def byid(tmp, name):
    return os.path.join(tmp, "by-id", "usb-FAKE_%s-if00-port0" % name)


def log(tmp):
    with open(os.path.join(tmp, "log.txt")) as fh:
        return fh.read()


def abschnitt(out, port):
    """Ausgabeblock eines Ports (bis zum nächsten 'Port:' bzw. zur Zusammenfassung)."""
    i = out.find("Port: %s\n" % port)
    if i < 0:
        return ""
    rest = out[i + 1:]
    j = min([k for k in (rest.find("\nPort: "), rest.find("\n=====")) if k >= 0] or [len(rest)])
    return rest[:j]


def teil_b():
    print("Teil B: Ende-zu-Ende mit Fake-serialhdl")
    tmp = tempfile.mkdtemp(prefix="k2id-")
    fremd = os.path.join(tmp, "fremd.dict.json")
    with open(fremd, "w") as fh:
        json.dump({"version": "v0.12.0-fremd", "config": {"MCU": "stm32f103", "CLOCK_FREQ": 72000000,
                   "SERIAL_BAUD": 250000}, "commands": {"get_config": 1, "config_stepper oid=%c": 2},
                   "responses": {"config is_config=%c": 3}}, fh)
    try:
        g = {"ttyMAIN": {"art": "antwort", "dict": MAIN}, "ttyDUESE": {"art": "antwort", "dict": DUESE},
             "ttySTUMM": {"art": "stumm"}, "ttyGESPERRT": {"art": "gesperrt"},
             "ttyFREMD": {"art": "antwort", "dict": fremd}}
        b_aufbau(tmp, g)
        rc, out, err, _t = b_lauf(tmp, ["--timeout", "0.5"])
        check(rc == 0, "B1 alle by-id-Ports, Exit 0 (rc=%d)" % rc)
        a = abschnitt(out, byid(tmp, "ttyMAIN"))
        check("ERKENNUNG:  MAIN-MCU (GD32F303RET6)" in a and "gd32f303xe" in a
              and "CLOCK_FREQ 120000000" in a and "75 Befehle" in a
              and "20241220_140327" in a, "B1 Main erkannt (MCU, CLOCK_FREQ, Version, Befehle)")
        a = abschnitt(out, byid(tmp, "ttyDUESE"))
        check("ERKENNUNG:  DÜSEN-MCU (GD32F303CBT6)" in a and "gd32f303xb" in a, "B1 Düse erkannt")
        a = abschnitt(out, byid(tmp, "ttySTUMM"))
        check("keine Antwort" in a and "TX/RX gekreuzt" in a and "Pin 10" in a and "RS232" in a,
              "B1 stumm: Hinweis Kabel/TX-RX/Pegel")
        a = abschnitt(out, byid(tmp, "ttyGESPERRT"))
        check("BELEGT" in a and "Klipper läuft" in a, "B1 gesperrter Port -> BELEGT-Meldung")
        a = abschnitt(out, byid(tmp, "ttyFREMD"))
        check("UNKLAR" in a and "kein K2-Typ" in a, "B1 Fremd-Firmware -> UNKLAR")
        check("./install.sh --main-serial %s --duese-serial %s" % (byid(tmp, "ttyMAIN"), byid(tmp, "ttyDUESE")) in out
              and "nichts eingetragen" in out, "B1 Vorschlag für install.sh")
        lg = log(tmp)
        check("open ttyMAIN 230400 exclusive=True rts=True" in lg, "B1 Port wie Klipper geöffnet (exklusiv, RTS)")
        check(lg.count("open ") == lg.count("close "), "B1 jeder geöffnete Port wieder geschlossen")
        check("ttyGESPERRT" not in lg.replace("identify ttyGESPERRT", ""), "B1 gesperrter Port nie geöffnet")
        check(err.strip() == "", "B1 keine stderr-Ausgabe (%r)" % err[:200])

        erg = os.path.join(tmp, "erg.txt")
        rc, out, err, _t = b_lauf(tmp, ["--timeout", "0.3", "--ergebnis", erg])
        with open(erg) as fh:
            e = fh.read()
        check(rc == 0 and e == "main=%s\nduese=%s\n" % (byid(tmp, "ttyMAIN"), byid(tmp, "ttyDUESE")),
              "B1 --ergebnis: main=/duese=-Zeilen")

        # RS485-Port aus k2-module.cfg und --ohne werden ausgelassen
        cfgd = os.path.join(tmp, "cfg")
        os.makedirs(cfgd, exist_ok=True)
        with open(os.path.join(cfgd, "k2-module.cfg"), "w") as fh:
            fh.write("[k2_box]\nserial: egal\n\n[k2_485]\nserial: %s\nbaud: 230400\n" % byid(tmp, "ttySTUMM"))
        open(os.path.join(tmp, "log.txt"), "w").close()
        rc, out, err, _t = b_lauf(tmp, ["--timeout", "0.3", "--ohne", byid(tmp, "ttyFREMD")],
                                  env_extra={"CONFIG_DIR": cfgd})
        lg = log(tmp)
        check(rc == 0 and "Ausgelassen" in out and "ttySTUMM" not in lg and "ttyFREMD" not in lg
              and "identify ttyMAIN" in lg, "B1 RS485-Port (k2-module.cfg) + --ohne nie angefasst")
        with open(os.path.join(cfgd, "k2-module.cfg"), "w") as fh:
            fh.write("[k2_485]\nserial: /dev/serial/by-id/usb-ANNAHME_USB-RS485-if00-port0\n")
        open(os.path.join(tmp, "log.txt"), "w").close()
        rc, out, err, _t = b_lauf(tmp, ["--timeout", "0.3"], env_extra={"CONFIG_DIR": cfgd})
        check("identify ttySTUMM" in log(tmp) and "Ausgelassen" not in out, "B1 Platzhalter in [k2_485] -> nichts ausgelassen")
        rc, out, err, _t = b_lauf(tmp, ["--ohne", byid(tmp, "ttyMAIN"), byid(tmp, "ttyMAIN")])
        check(rc == 1 and "Keine Ports übrig" in out, "B1 alles ausgelassen -> Exit 1")

        # --ausgabe-rolle
        rc, out, err, _t = b_lauf(tmp, ["--timeout", "0.5", "--ausgabe-rolle", "main"])
        check(rc == 0 and out.strip() == byid(tmp, "ttyMAIN") and "ZUSAMMENFASSUNG" in err,
              "B2 --ausgabe-rolle main: stdout nur Port, Meldungen auf stderr")
        rc, out, err, _t = b_lauf(tmp, ["--ausgabe-rolle", "duese", byid(tmp, "ttyDUESE")])
        check(rc == 0 and out.strip() == byid(tmp, "ttyDUESE"), "B2 --ausgabe-rolle duese mit Port-Angabe")
        rc, out, err, _t = b_lauf(tmp, ["--timeout", "0.3", "--ausgabe-rolle", "main", byid(tmp, "ttySTUMM")])
        check(rc == 1 and out.strip() == "" and "nicht gefunden" in err, "B2 Rolle nicht gefunden -> Exit 1, stdout leer")

        # Baudliste, nur angegebener Port
        g = {"ttyMAIN": {"art": "antwort", "dict": MAIN, "baud": 250000}, "ttyDUESE": {"art": "antwort", "dict": DUESE}}
        b_aufbau(tmp, g)
        rc, out, err, _t = b_lauf(tmp, ["--timeout", "0.3", "--baud", "230400,250000,115200", byid(tmp, "ttyMAIN")])
        lg = log(tmp)
        check(rc == 0 and "Baud 230400: keine Antwort" in out and "Baud 250000: ANTWORT" in out
              and "identify ttyMAIN 115200" not in lg and "ttyDUESE" not in lg,
              "B3 Baudliste: 230400 stumm, 250000 Antwort, danach Schluss; anderer Port unberührt")

        # zwei Main-MCUs -> kein Vorschlag für main
        g = {"ttyA": {"art": "antwort", "dict": MAIN}, "ttyB": {"art": "antwort", "dict": MAIN}}
        b_aufbau(tmp, g)
        rc, out, err, _t = b_lauf(tmp, [])
        erg = os.path.join(tmp, "erg.txt")
        rc, out, err, _t = b_lauf(tmp, ["--ergebnis", erg])
        with open(erg) as fh:
            e = fh.read()
        check("ACHTUNG: Main-MCU (GD32F303RET6) an mehreren Ports" in out and "--main-serial" not in out
              and e == "", "B4 mehrdeutig -> kein Vorschlag, --ergebnis leer")
        rc, out, err, _t = b_lauf(tmp, ["--ausgabe-rolle", "main"])
        check(rc == 1 and out.strip() == "" and "mehrdeutig" in err, "B4 --ausgabe-rolle mehrdeutig -> Exit 1")

        # Gesamt-Timeout
        b_aufbau(tmp, {"ttyHAENGT": {"art": "haengt"}})
        rc, out, err, t = b_lauf(tmp, ["--gesamt-timeout", "1.5"], timeout=30)
        check(rc == 3 and "Gesamt-Timeout" in out and t < 10, "B5 Gesamt-Timeout -> Exit 3 nach %.1f s" % t)

        # /proc-Belegung: anderer Prozess hält das Gerät offen -> nicht angefasst
        b_aufbau(tmp, {"ttyMAIN": {"art": "antwort", "dict": MAIN}})
        with open(os.path.join(tmp, "dev", "ttyMAIN")) as fh:
            halter = subprocess.Popen(["sleep", "30"], stdin=fh)
        try:
            time.sleep(0.2)
            rc, out, err, _t = b_lauf(tmp, [])
        finally:
            halter.kill()
            halter.wait()
        check(rc == 1 and "BELEGT durch PID %d (sleep)" % halter.pid in out and "open " not in log(tmp),
              "B6 belegt (/proc): Meldung mit PID, Port NICHT geöffnet")

        # leere by-id / fehlender Port / Aufruf- und Importfehler
        b_aufbau(tmp, {})
        rc, out, err, _t = b_lauf(tmp, [])
        check(rc == 1 and "Keine Ports unter" in out, "B7 keine Ports -> Exit 1 mit Hinweis")
        rc, out, err, _t = b_lauf(tmp, [os.path.join(tmp, "dev", "gibtsnicht")])
        check(rc == 1 and "FEHLT" in out, "B7 nicht vorhandener Port -> FEHLT, Exit 1")
        rc, out, err, _t = b_lauf(tmp, ["--baud", "abc"])
        check(rc == 2 and "FEHLER" in err, "B8 ungültige Option -> Exit 2")
        rc, out, err, _t = b_lauf(tmp, ["--egal"])
        check(rc == 2, "B8 unbekannte Option -> Exit 2")
        b_aufbau(tmp, {"ttyX": {"art": "kaputt"}})
        rc, out, err, _t = b_lauf(tmp, [])
        check(rc == 1 and "FEHLER – Port nicht zu öffnen" in out, "B8 Port-Öffnungsfehler gemeldet (kein BELEGT)")
        rc, out, err, _t = b_lauf(tmp, [], env_extra={"K2_IDENTIFY_KLIPPY": os.path.join(tmp, "fehlt")})
        check(rc == 2 and "Klipper-Module nicht ladbar" in out and "klippy-env" in out,
              "B9 Klipper nicht ladbar -> Exit 2 mit Hinweis klippy-env")
        rc, out, err, _t = b_lauf(tmp, ["--hilfe"])
        check(rc == 0 and "NUR LESEND" in out.upper(), "B10 --hilfe")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------
class FakeMcuPty:
    """Fake-MCU an einem pty: beantwortet ``identify`` wie die MCU-Firmware
    (Antwort mit empfangener Sequenz + leerer Ack-Block), protokolliert alle
    empfangenen Befehle. modus 'stumm' = liest nur mit. Auch für Attrappen-Tests
    (msgproto aus dem jeweiligen Klipper übergeben)."""

    def __init__(self, msgproto, dictpfad, modus="antwort"):
        import pty
        self.master, slave = pty.openpty()
        self.pfad = os.ttyname(slave)
        os.close(slave)
        with open(dictpfad, encoding="utf-8") as fh:
            data = zlib.compress(json.dumps(json.load(fh)).encode())
        self.prot = {"bytes": 0, "cmds": []}
        self.stop = threading.Event()
        self.th = threading.Thread(target=self._lauf, args=(msgproto, data, modus), daemon=True)
        self.th.start()

    def beenden(self):
        self.stop.set()
        self.th.join(2)
        os.close(self.master)

    def _lauf(self, msgproto, data, modus):
        master, protokoll, stop = self.master, self.prot, self.stop
        p = msgproto.MessageParser()
        buf = bytearray()
        resp = p.messages_by_name["identify_response"]
        while not stop.is_set():
            r, _w, _x = select.select([master], [], [], 0.05)
            if not r:
                continue
            try:
                ch = os.read(master, 4096)
            except OSError:
                time.sleep(0.05)
                continue
            protokoll["bytes"] += len(ch)
            buf += ch
            while True:
                n = p.check_packet(buf)
                if n == 0:
                    break
                if n < 0:
                    i = buf.find(b"\x7e")
                    buf = buf[i + 1:] if i >= 0 else bytearray()
                    continue
                blk, buf = buf[:n], buf[n:]
                pos = 2
                antwort = []
                while pos < n - 3:
                    mid, _pp = p.msgid_parser.parse(blk, pos)
                    mf = p.messages_by_id.get(mid)
                    if mf is None:
                        protokoll["cmds"].append("unbekannt-%d" % mid)
                        break
                    par, pos = mf.parse(blk, pos)
                    protokoll["cmds"].append(mf.name)
                    if mf.name == "identify":
                        antwort += resp.encode([par["offset"],
                                                data[par["offset"]:par["offset"] + par["count"]]])
                if modus == "stumm":
                    continue
                # wie die MCU-Firmware: Antwort mit der empfangenen Sequenz,
                # danach leerer Ack-Block mit Sequenz+1
                seq = blk[1] & 0x0f
                for nutz, sq in ((antwort, seq), ([], (seq + 1) & 0x0f)):
                    if sq == seq and not nutz:
                        continue
                    out = [len(nutz) + 5, 0x10 | sq] + nutz
                    out += msgproto.crc16_ccitt(out) + [0x7e]
                    os.write(master, bytes(out))


def teil_c():
    print("Teil C: echtes Klipper-serialhdl gegen Fake-MCU (pty)")
    ks = os.environ.get("KLIPPER_SRC", "")
    kd = os.path.join(ks, "klippy") if ks else ""
    if not ks or not os.path.isfile(os.path.join(kd, "chelper", "c_helper.so")):
        print("  SKIP  $KLIPPER_SRC ohne gebauten chelper (%s)" % (ks or "nicht gesetzt"))
        return
    try:
        import greenlet, cffi, serial  # noqa: F401,E401
    except ImportError as e:
        print("  SKIP  %s fehlt im laufenden Python (%s)" % (e.name, sys.executable))
        return
    sys.path.insert(0, kd)
    import msgproto

    def lauf(modus, dictpfad, args, belegen=False):
        mcu = FakeMcuPty(msgproto, dictpfad, modus)
        spfad, prot = mcu.pfad, mcu.prot
        tmp = tempfile.mkdtemp(prefix="k2id-pty-")
        os.makedirs(os.path.join(tmp, "by-id"))
        port = os.path.join(tmp, "by-id", "usb-FTDI_FAKE_RS232-if00-port0")
        os.symlink(spfad, port)
        halter = None
        if belegen:
            halter = subprocess.Popen(["sleep", "30"], stdin=open(spfad, "rb"))
            time.sleep(0.2)
        env = dict(os.environ, K2_IDENTIFY_BYID_DIR=os.path.join(tmp, "by-id"), K2_IDENTIFY_REEXEC="1",
                   K2_MCU_DICT_DIR=DD, CONFIG_DIR=os.path.join(tmp, "keine-config"))
        env.pop("K2_IDENTIFY_KLIPPY", None)
        t0 = time.monotonic()
        try:
            pr = subprocess.run([sys.executable, "-B", SKRIPT, "--klipper", ks] + args, env=env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
        finally:
            dt = time.monotonic() - t0
            if halter:
                halter.kill()
                halter.wait()
            mcu.beenden()
            shutil.rmtree(tmp, ignore_errors=True)
        return pr.returncode, pr.stdout.decode(), pr.stderr.decode(), prot, dt, port

    rc, out, err, prot, dt, port = lauf("antwort", MAIN, [])
    check(rc == 0 and "ERKENNUNG:  MAIN-MCU" in out, "C1 Main über echtes serialhdl erkannt (%.1f s)" % dt)
    check(prot["cmds"] and set(prot["cmds"]) == {"identify"},
          "C1 Fake-MCU empfing NUR identify (%d Befehle, %d Bytes)" % (len(prot["cmds"]), prot["bytes"]))
    check("--main-serial %s" % port in out, "C1 Vorschlag --main-serial mit by-id-Pfad")
    rc, out, err, prot, dt, port = lauf("antwort", DUESE, ["--ausgabe-rolle", "duese"])
    check(rc == 0 and out.strip() == port and set(prot["cmds"]) == {"identify"},
          "C2 Düse, --ausgabe-rolle duese, nur identify")
    rc, out, err, prot, dt, port = lauf("stumm", MAIN, ["--timeout", "1"])
    check(rc == 1 and "keine Antwort" in out and dt < 15 and set(prot["cmds"]) <= {"identify"},
          "C3 stumme MCU: keine Antwort nach Timeout 1 s (Lauf %.1f s), nur identify-Versuche" % dt)
    rc, out, err, prot, dt, port = lauf("antwort", MAIN, [], belegen=True)
    check(rc == 1 and "BELEGT durch PID" in out and prot["bytes"] == 0,
          "C4 belegter pty: nicht geöffnet, 0 Bytes an die MCU")
    if err.strip():
        print("  (stderr C4: %s)" % err.strip()[:200])


def main():
    m = lade_modul()
    teil_a(m)
    teil_b()
    teil_c()
    print()
    if FEHLER:
        print("ERGEBNIS: %d ok, %d FEHLGESCHLAGEN" % (N_OK[0], len(FEHLER)))
        for f in FEHLER:
            print("  - " + f)
        return 1
    print("ERGEBNIS: %d/%d BESTANDEN" % (N_OK[0], N_OK[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
