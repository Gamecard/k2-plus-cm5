#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_k2_box.py – Tests für k2_box (ohne Klipper, ohne Hardware).

Läuft mit ``python3 test_k2_box.py`` (nachbau/ per sys.path, Pfadsuche wie
in pi-host/k2_transport.py). Prüft:

1. Kompletter Load-Ablauf (Slot B, Erfolg).
2. Fehlerabbruch mit Meldung (Slot A, 0x0c bei ~245 mm wie Kanal-A-Defekt).
3. Unload (Slot B, Retrude-Folge).
4. allow_write=False sendet keine mechanischen Kommandos (nur lesen + Plan).
5. Frame-Folge == Live-Sequenzen aus cfsseq (EXTRUDE/RETRUDE_LIVE_CMDS).
6. Gemeinsamer Lock mit Motor-Transport (keine Überlappung).
7. Lesende Abfragen + 0x0a-Poll (retries=0) + Moonraker-get_status.
8. Preload OPs + Auto-Addr nach k2_motor:ready + Guards (nie 0x50/0x06,
   keine Bursts, Cut-Platzhalter).
9. G-Codes KTWO_BOX_STATUS/LOAD/UNLOAD/PRELOAD via FakePrinter.
Filamentwechsel (Ergänzung 16.10.2026, Belege README-k2_box.md §8):
10. Bausteine (Schnitt/Schacht/Abstreifen/Spülen/Rückzug) == box.cfg + Live,
    TNN-Mapping, Optionsgrenzen, Upstream-Namensregel.
11. Einzug bis Kopfsensor + Stage 7 (0x0e ohne Extruder, Timeout -> IDLE).
12. Rückzug mit Extruder-Haken je p0 (Frames == cfsseq).
13. Box-Zustand abwarten (PRELOAD/ERROR/Timeout).
14. Volle Kette T1B über die Klipper-Hülle (Reihenfolge Bus + G-Code).
15. Sperren (allow_write/allow_move/Druck ohne Freigabe -> Pause).
16. Fehler im Druck -> Pause, Quittung, RESUME_FIX.
17. Fehler ohne Druck -> G-Code-Fehler; Box 2/Hub belegt/kein Bus.
18. ReactorBusProxy.  19. Makros T0-T15/M8200/RESUME (Jinja2).
20.-24. Auto-Nachladen bei Runout (A7): Slot-Wahl, Ablauf, Pausenpfade,
    Befehle AUTO_REFILL/SAME_MATERIAL, Config (printer.cfg/k2-module.cfg).
25. Python-3.9-Syntax.
RESUME nach abgebrochenem Wechsel (17.10.2026 spät):
Kopf leer -> gesperrt / voll -> frei, Kopfsensor fehlt -> gesperrt bis
HEAD_LOADED=1, Abbruch-Merker (Ziel-TNN), Nachholen (resume_redo_change)
inkl. Fehlerfall + Sperren, RESUME-Makro ruft KTWO_BOX_RESUME_CHECK.
Düsen-Material persistent + Slicer-Matrix + M600 (17.10.2026):
remain_material_file
lesen (ok/fehlt/kaputt/Struktur/Farbe, .defekt-…), atomar schreiben nur
nach FLUSH + Änderung, flush_mode matrix (Kopf/Ende, 16×16 asymmetrisch,
TOOL=, nur printing, Rückfälle), Makro M600 -> PAUSE. HOME zeigt im Test
auf ein Wegwerf-Verzeichnis (Default-Pfad unter ~).
Restmaterial + Druckende (17.10.2026,
README-k2_box.md §12.5): Auslöser 0x50 nur im Druck, Stückfolge (Kopf leer
+ 1 Stück), max_tube_length (gekürzt, Kopf voll = Pause), Kopfsensor
unbekannt -> Pause ohne Förderung, Zeitlimit, Geschwindigkeit mit/ohne DB,
keine Kreise, kein Schnitt/Rückzug danach, KTWO_BOX_ENDING, end_print_unload
an/aus (Fehler ohne Pause), END_PRINT-Makro.
Live-Abgleich 18.10.2026: A1 Erst-Laden ohne
Schnitt/Rückzug (Kopf+Hub leer, Rückfälle), A2 Box-Druckzustand 0x0d/0x0f
(KTWO_BOX_PRINT_START/END, kein 0x0f im Einzug während des Drucks), A3
Rückzug mit Spülgeschwindigkeit, A5 end_print_unload Default an, A6
END_PRINT-Reihenfolge Z+2 -> Entladen -> Park.

Fakes:
- FakeBoxBus: simuliert Box-Zustände (IDLE/PRELOAD/PRINT/ERROR), Hub-Maske,
  Odometer-Fortschritt (+50 mm je mode-5-Poll), Fehlerfall 0x0c bei ~245 mm
  für Slot A (alter Kanal-A-Defekt, Beleg Live-Test Kanal A +
  Live-Mitschnitt: 25x20 ms <0,01 mm → 0x0c), RFID/Remain/Version/HW-Status, Auto-Addr
  0xA0-0xA3. Zeichnet alle Calls + Intervalle (Lock-Test) auf.
- FakeMotorBus: minimaler Motor-Transport mit gleichem Lock (für Test 6).
"""

import math
import os
import struct
import sys
import threading
import time

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
_REPO = next((d for d in (os.path.dirname(_HERE), os.path.dirname(os.path.dirname(_HERE)))
              if os.path.isdir(os.path.join(d, "nachbau"))), os.path.dirname(_HERE))
_NACHBAU = os.path.join(_REPO, "nachbau")
if os.path.isdir(_NACHBAU) and _NACHBAU not in sys.path:
    sys.path.insert(0, _NACHBAU)

import k2_box as K
import logging
import tempfile as _tempfile

# Schutz: remain_material_file hat einen Default unter ~ – Tests dürfen nie
# ins echte Home schreiben. HOME zeigt für den ganzen Lauf auf ein
# Wegwerf-Verzeichnis (Tilde-Auflösung), test_options() schaltet die
# Persistenz zusätzlich aus (Tests mit Datei setzen einen tmp-Pfad).
_TEST_HOME = _tempfile.mkdtemp(prefix="k2box-home-")
os.environ["HOME"] = _TEST_HOME
_TMP_DIRS = [_TEST_HOME]


def _cleanup_tmp():
    import shutil
    for d in _TMP_DIRS:
        shutil.rmtree(d, ignore_errors=True)


import atexit as _atexit  # noqa: E402
_atexit.register(_cleanup_tmp)
logging.getLogger().setLevel(logging.CRITICAL)  # Fehlerpfad-Tests ohne Log-Rauschen

try:
    import box as BOX
    _HAVE_BOX = True
except Exception as _e:
    BOX = None
    _HAVE_BOX = False
    print("WARNUNG: nachbau/box.py nicht importierbar: %s" % _e)

try:
    import cfsseq as SEQ
    _HAVE_SEQ = True
except Exception as _e:
    SEQ = None
    _HAVE_SEQ = False
    print("WARNUNG: nachbau/cfsseq.py nicht importierbar: %s" % _e)

try:
    from serial485 import build_frame as REF_BUILD
    _HAVE_S485 = True
except Exception:
    REF_BUILD = None
    _HAVE_S485 = False

UID12 = [0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xAB]  # Platzhalter (Repo-Anonymisierung), Fake-Box
SN19 = b"10000000000X000TEST"  # 19 Zeichen wie echte SN (Beleg box-Selbsttest)


# =====================================================================
# FakeBoxBus
# =====================================================================

class FakeBoxBus:
    """Simuliert CFS-Box 0x01 (BoxAction-kompatibel: transact_full)."""

    def __init__(self, lock=None, fail_after_mm=None, odo_step=50.0,
                 temp_c=22, rh=46, uid=None, addr=0x01):
        self._lock = lock if lock is not None else threading.Lock()
        self.fail_after_mm = fail_after_mm  # z.B. 245.0 nur für Slot A (0x01)
        self.odo_step = float(odo_step)
        self.odo = 0.0
        self.temp_c = int(temp_c)
        self.rh = int(rh)
        self.uid = list(uid if uid is not None else UID12)
        self.box_addr = int(addr)
        self.state = 0  # IDLE
        self.hub_mask = 0x00
        self.stored_error = 0x00
        self.feeding = False
        self.inhibit = 0
        self.preload_enable = 1
        # Slots: alle belegt mit RFID-Tag (B für Erfolg, A für Fehler – beide
        # mit Filament, damit mode 0 den Hub erreicht und erst das Odometer
        # stoppt; Beleg CFS-Firmware-Analyse, Folgefehler 3).
        self.slots = {}
        for i, letter in enumerate("ABCD"):
            self.slots[letter] = {"filament": True,
                                  "rfid": "TAG%s1234567890ABCDEF12345678" % letter,
                                  "remain": [28, 29, 30, 31][i]}
        self.calls = []  # [(addr, fun, tuple(data), kwargs)]
        self.intervals = []  # [(t0, t1)] für Lock-Test
        # Wechsel-Simulation (Tests 10+): gemeinsamer Ereignis-Trace mit dem
        # FakeGcode, Kopfsensor (Filament am Kopf ab head_at_mm Odometer),
        # mode 7 nur ok, wenn der Extruder seit mode 6 gefördert hat.
        self.trace = None
        self.head = False
        self.head_at_mm = None
        self.need_extruder_for_mode7 = False
        self.extruder_mm = 0.0
        self.preload_polls = 0  # so viele 0x0a-Polls noch PRELOAD melden
        self.print_frame_status = 0x00  # 0x0a im PRINT: 0x50 = Runout (A7)
        # Stock-Retry (18.10.2026): je Förderphase (ab mode 4) nach so vielen
        # mode-5-Polls Status stall_status melden (None = Phase läuft durch);
        # mode0_status = Fehler schon auf 10 [m 00 00] (z.B. 0x09).
        self.stall_phases = None
        self.stall_status = 0x0C
        self.mode0_status = None
        self._phase = -1
        self._phase_polls = 0

    # -- Bus-API (BoxAction-kompatibel) ---------------------------------
    def transact_full(self, addr, fun, data=(), timeout=None, retries=0,
                      status=0xFF, expect_fun=None, expect_addr=None, **kw):
        if fun in K.FORBIDDEN_FUNS:
            raise AssertionError("FakeBox: verbotenes fun 0x%02x gesendet!" % fun)
        payload = list(data)
        with self._lock:
            t0 = time.monotonic()
            time.sleep(0.015)  # Arbeit simulieren (Lock-Test braucht Dauer)
            self.calls.append((addr, fun, tuple(payload),
                               dict(timeout=timeout, retries=retries,
                                    status=status)))
            if self.trace is not None:
                self.trace.append(("bus", fun, tuple(payload)))
            raddr, rstatus, rfun, rdata = self._handle(addr, fun, payload)
            t1 = time.monotonic()
            self.intervals.append((t0, t1))
        if _HAVE_S485 and REF_BUILD is not None:
            rraw = bytes(REF_BUILD(raddr, rfun, rdata, status=rstatus))
        else:  # pragma: no cover
            rraw = b""
        return raddr, rstatus, rfun, bytes(rdata), rraw

    def transact(self, addr, fun, data=(), timeout=None, retries=0,
                 status=0xFF, **kw):
        _a, _st, _f, d, _r = self.transact_full(
            addr, fun, data, timeout=timeout, retries=retries, status=status)
        return d

    # -- Box-Verhalten ----------------------------------------------------
    def _handle(self, addr, fun, payload):
        A = self.box_addr
        if fun == 0xA1:  # Discovery an 0xFE
            return 0xFE, 0x00, 0xA1, bytes([0x01, 0x00] + self.uid)
        if fun == 0xA0:  # Adressvergabe [neu]+UID
            if len(payload) >= 13 and payload[1:] == self.uid:
                self.box_addr = int(payload[0])
                A = self.box_addr
            return A, 0x00, 0xA0, bytes([0x01, 0x00] + self.uid)
        if fun in (0xA2, 0xA3):  # Online-Check / Addr-Tabelle
            return A, 0x00, fun, bytes([0x01, 0x00] + self.uid)
        if fun == 0x0B:  # Loader-Check (keine Antwort, Beleg auto_addr)
            raise TimeoutError("0x0B bleibt unbeantwortet")
        if addr not in (A, 0xFE, 0xEA, 0xEB) and addr != A:
            raise TimeoutError("FakeBox: falsche Adresse 0x%02x" % addr)
        if fun == 0x0A:  # Status-Poll (6 B, Beleg CFS-Firmware-Analyse)
            t = self.temp_c & 0xFF
            if self.preload_polls > 0:
                self.preload_polls -= 1
                data = bytes([t, self.rh & 0xFF, 0x00, 1,
                              self.hub_mask & 0xFF, 0x00])
                return A, 0x00, 0x0A, data
            data = bytes([t, self.rh & 0xFF, 0x00, self.state,
                          self.hub_mask & 0xFF, 0x00])
            st = self.stored_error if self.state == 4 else (
                self.print_frame_status if self.state == 2 else 0x00)
            return A, st, 0x0A, data
        if fun == 0x02:  # RFID [Maske] -> ASCII
            mask = payload[0] if payload else 0x0F
            out = b""
            for i, letter in enumerate("ABCD"):
                if mask & (1 << i):
                    slot = self.slots[letter]
                    tag = slot["rfid"] if slot["filament"] else "unknown"
                    if tag is None:
                        tag = "unknown"
                    out += ("%s:%s;" % (letter, tag)).encode("ascii")
            return A, 0x00, 0x02, out
        if fun == 0x03:  # Remain [Maske] -> 4 B positional
            mask = payload[0] if payload else 0x0F
            out = []
            for i, letter in enumerate("ABCD"):
                if not (mask & (1 << i)):
                    out.append(0xFF)
                    continue
                slot = self.slots[letter]
                if not slot["filament"]:
                    out.append(0x00)
                elif slot["rfid"] is None:
                    out.append(0xFF)
                else:
                    out.append(int(slot["remain"]))
            return A, 0x00, 0x03, bytes(out)
        if fun == 0x04:  # Box-Mode [mask, mode]
            mask, mode = payload[0], payload[1]
            if mode == 1 and mask == 0x00:  # IDLE (löscht auch ERROR, live)
                self.state, self.stored_error, self.feeding = 0, 0x00, False
                return A, 0x00, 0x04, b""
            if mode == 0:
                if mask == 0:
                    return A, 0x01, 0x04, b""
                self.state = 2  # PRINT
                return A, 0x00, 0x04, b""
            return A, 0x01, 0x04, b""
        if fun == 0x0F:  # Inhibit [x]
            x = payload[0] if payload else 0xFF
            if x in (0, 1):
                self.inhibit = x
                return A, 0x00, 0x0F, b""
            return A, 0x01, 0x0F, b""
        if fun == 0x0D:  # Preload [mask, op]
            _mask, op = payload[0], payload[1]
            if op in (0, 1):
                self.preload_enable = op
                return A, 0x00, 0x0D, b""
            if op in (2, 3):
                self.state = 0
                return A, 0x00, 0x0D, b""
            return A, 0x01, 0x0D, b""
        if fun == 0x10:  # Extrude [mask, mode, p]
            mask, mode, p = payload[0], payload[1], payload[2]
            if mode == 0:  # Stage1: Hub erreichen (Sensor schon aktiv ok)
                if mask == 0 or mask not in (0x01, 0x02, 0x04, 0x08):
                    return A, 0x01, 0x10, b""
                letters = [l for i, l in enumerate("ABCD") if mask == (1 << i)]
                if self.mode0_status is not None:
                    self.state, self.stored_error = 4, self.mode0_status
                    return A, self.mode0_status, 0x10, bytes([4])
                if not letters or not self.slots[letters[0]]["filament"]:
                    self.state, self.stored_error = 4, 0x16
                    return A, 0x16, 0x10, bytes([4])
                self.hub_mask |= mask
                self.state = 3  # RELOAD während Förderung
                return A, 0x00, 0x10, bytes([self.state])
            if mode == 4:  # Dauerfördern starten (sofort ok)
                self.feeding = True
                self._phase += 1
                self._phase_polls = 0
                return A, 0x00, 0x10, b""
            if mode == 5:  # Odometer-Abfrage (Fehler aus mode 4)
                self._phase_polls += 1
                sp = self.stall_phases
                if sp is not None and 0 <= self._phase < len(sp) \
                        and sp[self._phase] is not None \
                        and self._phase_polls >= sp[self._phase]:
                    self.state, self.stored_error = 4, self.stall_status
                    self.feeding = False
                    return A, self.stall_status, 0x10, struct.pack(">f", -self.odo)
                if mask == 0x01 and self.fail_after_mm is not None:
                    if self.odo >= self.fail_after_mm:
                        self.state, self.stored_error = 4, 0x0C
                        self.feeding = False
                        return A, 0x0C, 0x10, struct.pack(">f", -self.odo)
                    if self.odo + self.odo_step >= self.fail_after_mm:
                        # Nächster Schritt würde die Schwelle reißen → jetzt
                        # Stillstand melden (Odometer UNVERÄNDERT, live).
                        self.state, self.stored_error = 4, 0x0C
                        self.feeding = False
                        return A, 0x0C, 0x10, struct.pack(">f", -self.odo)
                self.odo += self.odo_step
                if self.head_at_mm is not None and self.odo >= self.head_at_mm:
                    self.head = True
                return A, 0x00, 0x10, struct.pack(">f", -self.odo)
            if mode == 6:  # Stopp/Buffer
                self.feeding = False
                self.extruder_mm = 0.0
                return A, 0x00, 0x10, b""
            if mode == 7:  # Extruder-Check p mm (Odometer > p in 300 ms)
                if self.need_extruder_for_mode7 and self.extruder_mm <= 0:
                    self.state, self.stored_error = 4, 0x0E
                    return A, 0x0E, 0x10, b""
                return A, 0x00, 0x10, b""
            if mode == 3:  # Fehler löschen
                self.state, self.stored_error = 0, 0x00
                return A, 0x00, 0x10, b""
            return A, 0x01, 0x10, b""
        if fun == 0x11:  # Retrude [mask, p]
            mask, p = payload[0], payload[1]
            if p not in (0, 1):
                return A, 0x01, 0x11, b""
            # p0 = Feeder + Hauptmotor bis Buffer-Sensor (Filament bleibt
            # durch den Hub), erst p1 = pull_back_hub räumt den Hub (CFS-
            # Firmware FUN_08004d90; 22.10.2026: vorher räumte das Modell schon bei p0)
            if p == 1:
                if mask == 0:
                    self.hub_mask = 0x00
                else:
                    self.hub_mask &= (~mask) & 0xFF
                self.head = False
            self.state = 0
            return A, 0x00, 0x11, b""
        if fun == 0x05:  # Buffer
            return A, 0x00, 0x05, b"\x00"
        if fun == 0x08:  # Sensor [pos]
            pos = payload[0] if payload else 0
            if pos == 0:
                m = 0
                for i, letter in enumerate("ABCD"):
                    if self.slots[letter]["filament"]:
                        m |= (1 << i)
                return A, 0x00, 0x08, bytes([m])
            return A, 0x00, 0x08, bytes([self.hub_mask & 0xFF])
        if fun == 0x0E:  # Messrad [op]
            op = payload[0] if payload else 1
            if op == 1:
                return A, 0x00, 0x0E, struct.pack(">f", -self.odo)
            self.odo = 0.0  # op 0 nullen (nur mit reset=True aufgerufen)
            return A, 0x00, 0x0E, b""
        if fun == 0x14:  # Version/SN
            return A, 0x00, 0x14, b"153" + SN19
        if fun == 0x15:  # HW-Status (alle ok)
            return A, 0x00, 0x15, bytes([0x10, 0x20, 0x30, 0x40, 0x50, 0x60]
                                        + [0] * 10)
        raise AssertionError("FakeBox unerwartet: fun 0x%02x %r" % (fun, payload))


class FakeMotorBus:
    """Minimaler Motor-Transport (nur für Lock-Test, gleiche Lock-Instanz)."""

    def __init__(self, lock):
        self._lock = lock
        self.calls = []
        self.intervals = []

    def transact(self, addr, fun, data, timeout=1.0, retries=0):
        payload = list(data)
        with self._lock:
            t0 = time.monotonic()
            time.sleep(0.015)
            self.calls.append((addr, fun, tuple(payload)))
            t1 = time.monotonic()
            self.intervals.append((t0, t1))
        return 0x00, fun, bytes(payload)


# =====================================================================
# FakePrinter (Klipper-Hülle, minimal)
# =====================================================================

class FakeReactor:
    """Reactor mit virtueller Uhr: pause(waketime) springt dorthin (+1 ms
    echte Wartezeit, damit Hilfs-Threads des ReactorBusProxy laufen)."""
    NOW = 0

    def __init__(self):
        self.timers = []
        self.pauses = []
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def pause(self, waketime):
        self.pauses.append(float(waketime))
        time.sleep(0.001)
        self.now = max(self.now + 0.001, float(waketime))
        return self.now

    def register_timer(self, cb, when=0):
        self.timers.append(cb)
        return cb


class FakeGcode:
    def __init__(self):
        self.infos = []
        self.commands = {}
        self.scripts = []   # ausgeführte Zeilen (run_script_from_command)
        self.raw = []
        self.printer = None  # FakePrinter (für Zustands-Simulation)

    def respond_info(self, msg):
        self.infos.append(str(msg))

    def respond_raw(self, msg):
        self.raw.append(str(msg))

    def run_script_from_command(self, script):
        for line in script.split("\n"):
            line = line.strip()
            if not line:
                continue
            self.scripts.append(line)
            name = line.split()[0]
            if name in self.commands:  # z. B. KTWO_BOX_* aus KTWO_BOX_RUNOUT
                params = dict(t.split("=", 1) for t in line.split()[1:]
                              if "=" in t)
                g = FakeGcmd(params)
                try:
                    self.commands[name](g)
                finally:
                    self.infos.extend(g.infos)
                continue
            if self.printer is not None:
                self.printer.sim(line)

    def register_command(self, name, func, desc=None):
        if not upstream_gcode_name_ok(name):
            raise RuntimeError("Can't register '%s' as it is an invalid name" % name)
        self.commands[name] = func


def upstream_gcode_name_ok(cmd):
    """Nachbau der Namensprüfung aus Upstream gcode.py register_command
    (Buchstabe+Ziffer am Anfang ist ungültig, z. B. K2_…)."""
    try:
        c = cmd.upper().split()[0]
        float(c[1:])
        if c[0].isupper() and c[1].isdigit():
            return True
    except Exception:
        pass
    if (cmd.upper() != cmd or not cmd.replace('_', 'A').isalnum()
            or cmd[0].isdigit() or cmd[1:2].isdigit()):
        return False
    # Upstream-Parser (gcode.py _process_commands, args_r '([A-Z_]+|[A-Z*])')
    # zerlegt den Befehl: Ziffern im Namen kappen ihn (KTWO_485_X -> KTWO_485)
    # -> "Unknown command" trotz erfolgreicher Registrierung.
    import re as _re
    parts = _re.split('([A-Z_]+|[A-Z*])', cmd.upper())
    return ''.join(parts[:3]).strip() == cmd


class _Obj:
    def __init__(self, status):
        self.status = status

    def get_status(self, eventtime=None):
        return dict(self.status)


class _SensorObj:
    def __init__(self, printer):
        self.printer = printer
        self.enabled = True

    def get_status(self, eventtime=None):
        bus = self.printer.bus
        return {"filament_detected": bool(getattr(bus, "head", False)),
                "enabled": self.enabled}


class _PinObj:
    """[output_pin] wie Upstream: get_status value = VALUE/scale."""

    def __init__(self, value=0.0, scale=255.0):
        self.value = float(value)
        self.scale = float(scale)

    def get_status(self, eventtime=None):
        return {"value": self.value}


class FakePrinter:
    def __init__(self, bus, with_motion=False):
        self.reactor = FakeReactor()
        self.gcode = FakeGcode()
        self.gcode.printer = self
        self.bus = bus
        self.objects = {"gcode": self.gcode, "k2_485": bus}
        self.handlers = {}
        self.events = []
        # Drucker-Simulation für die Wechsel-Tests (Positionen in mm)
        self.pos = [200.0, 200.0, 20.0, 0.0]
        self.relative = False
        self.accel = 20000.0
        self.saved = {}
        self.fail_on = None  # Zeilenanfang -> gcode-Fehler simulieren
        if with_motion:
            self.print_state = "standby"
            self.paused = False
            self.target = 0.0
            self.sensor = _SensorObj(self)
            self.objects.update({
                "print_stats": _Obj({}), "pause_resume": _Obj({}),
                "toolhead": _Obj({}), "gcode_move": _Obj({}),
                "extruder": _Obj({}),
                "filament_switch_sensor filament_sensor": self.sensor})
            self._refresh()

    def _refresh(self):
        if "toolhead" not in self.objects:
            return
        self.objects["print_stats"].status = {"state": self.print_state}
        self.objects["pause_resume"].status = {"is_paused": self.paused}
        self.objects["toolhead"].status = {
            "homed_axes": "xyz", "max_accel": self.accel,
            "axis_maximum": [352.5, 400.0, 360.0, 0.0]}
        self.objects["gcode_move"].status = {"gcode_position": list(self.pos)}
        self.objects["extruder"].status = {"target": self.target}

    def sim(self, line):
        """Minimale G-Code-Simulation (nur was die Wechsel-Tests brauchen)."""
        if self.fail_on and line.startswith(self.fail_on):
            raise ValueError("Simulierter G-Code-Fehler bei %r" % line)
        if self.bus is not None and getattr(self.bus, "trace", None) is not None:
            self.bus.trace.append(("gcode", line))
        w = line.split()
        cmd = w[0]
        args = {}
        for tok in w[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                args[k] = v
            else:
                args[tok[0]] = tok[1:]
        if cmd == "G90":
            self.relative = False
        elif cmd == "G91":
            self.relative = True
        elif cmd in ("G0", "G1"):
            for i, ax in enumerate("XYZE"):
                if ax in args:
                    v = float(args[ax])
                    self.pos[i] = self.pos[i] + v if (self.relative or ax == "E") \
                        else v
                    if ax == "E" and v > 0 and self.bus is not None:
                        self.bus.extruder_mm += v
        elif cmd == "SET_VELOCITY_LIMIT" and "ACCEL" in args:
            self.accel = float(args["ACCEL"])
        elif cmd in ("M104", "M109"):
            self.target = float(args.get("S", 0))
        elif cmd == "SAVE_GCODE_STATE":
            self.saved[args["NAME"]] = self.relative
        elif cmd == "RESTORE_GCODE_STATE":
            self.relative = self.saved.get(args["NAME"], self.relative)
        elif cmd == "SET_FILAMENT_SENSOR":
            self.sensor.enabled = bool(int(args["ENABLE"]))
        elif cmd == "SET_PIN" and ("output_pin %s" % args.get("PIN")) \
                in self.objects:
            pin = self.objects["output_pin %s" % args["PIN"]]
            pin.value = float(args["VALUE"]) / pin.scale
        elif cmd == "PAUSE":
            self.paused = True
            self.print_state = "paused"
        elif cmd == "RESUME":
            self.paused = False
            self.print_state = "printing"
        self._refresh()

    def set_printing(self, on=True):
        self.print_state = "printing" if on else "standby"
        self._refresh()

    def lookup_object(self, name):
        try:
            return self.objects[name]
        except KeyError:
            raise RuntimeError("Objekt %r fehlt" % (name,))

    def get_reactor(self):
        return self.reactor

    def register_event_handler(self, ev, cb):
        self.handlers.setdefault(ev, []).append(cb)

    def send_event(self, ev):
        self.events.append(ev)
        for cb in self.handlers.get(ev, []):
            cb()

    def fire(self, ev):
        for cb in self.handlers.get(ev, []):
            cb()


class FakeConfig:
    def __init__(self, printer, opts):
        self.printer = printer
        self._opts = dict(opts)

    def get_printer(self):
        return self.printer

    def get_options(self):
        return list(self._opts.keys())

    def get(self, name, default=None):
        return self._opts.get(name, default)

    def error(self, msg):
        raise ValueError(msg)


class FakeGcmd:
    def __init__(self, params=None):
        self.params = dict(params or {})
        self.infos = []

    def get(self, name, default=None):
        return self.params.get(name, self.params.get(name.lower(), default))

    def get_int(self, name, default=None, **kw):
        v = self.get(name, default)
        if v is None:
            return default
        return int(v)

    def get_float(self, name, default=None, **kw):
        v = self.get(name, default)
        if v is None:
            return default
        v = float(v)
        if "minval" in kw and v < kw["minval"]:
            raise ValueError("%s < minval" % name)
        if "maxval" in kw and v > kw["maxval"]:
            raise ValueError("%s > maxval" % name)
        if "above" in kw and v <= kw["above"]:
            raise ValueError("%s <= above" % name)
        return v

    def respond_info(self, msg):
        self.infos.append(str(msg))

    def error(self, msg):
        raise ValueError(msg)


# =====================================================================
# Helfer
# =====================================================================

def test_options(**over):
    o = {"box_addr": "1", "allow_write": "True",
         "shutdown_on_failure": "False", "auto_init": "True",
         "poll_interval": "4.0", "remain_material_file": "",
         "slot_material_file": ""}
    o.update(over)
    return o


def build_system(opts=None, **bus_kw):
    opts = dict(opts if opts is not None else test_options())
    cfg = K.parse_options(opts)
    bus = FakeBoxBus(**bus_kw)
    ctl = K.K2BoxController(cfg, bus, pause=lambda s: None)
    return cfg, bus, ctl


def request_frames(bus):
    """Calls -> Vollframes (Status 0xFF, wie BoxAction sendet)."""
    if not _HAVE_S485:
        raise AssertionError("serial485 fehlt für Frame-Vergleich")
    return [bytes(REF_BUILD(a, f, list(d), status=0xFF))
            for a, f, d, _k in bus.calls]


# =====================================================================
# Einzeltests
# =====================================================================

def test_load_ok():
    cfg, bus, ctl = build_system()
    res = ctl.do_load("B")
    assert res["status"] == "ok" and res["slot"] == "B", res
    assert len(res["odo_mm"]) == 5, res["odo_mm"]
    assert res["odo_mm"][-1] > res["odo_mm"][0] > 0, res["odo_mm"]
    poll = ctl.poll_status()
    assert poll["hub_mask"] & 0x02, poll
    return "Load B ok, Odometer %s mm, Hub B belegt" % (
        round(res["odo_mm"][-1], 1),)


def _test_load_fehler_0c_impl():
    cfg, bus, ctl = build_system()
    bus.fail_after_mm = 245.0  # Kanal-A-Defekt (Beleg Live-Test Kanal A)
    try:
        ctl.do_load("A")
    except BOX.CfsStatusError as exc:
        assert exc.status == 0x0C, "Status 0x%02x statt 0x0c" % exc.status
        assert "Stillstand" in exc.name or "0x0c" in str(exc).lower() \
            or "stillstand" in str(exc).lower(), exc.name
        poll = ctl.poll_status()
        assert poll["state_name"] == "STATE_ERROR", poll
        assert poll.get("error_code") == 0x0C, poll
        return "Abbruch 0x0c mit Meldung '%s' (Odometer %.0f mm)" % (
            exc.name, ctl.bus.odo)
    raise AssertionError("Load A hätte mit 0x0c abbrechen müssen")


def test_unload_ok():
    cfg, bus, ctl = build_system()
    ctl.do_load("B")
    assert bus.hub_mask & 0x02
    res = ctl.do_unload("B")
    assert res["status"] == "ok", res
    assert bus.hub_mask & 0x02 == 0, "Hub B müsste frei sein"
    return "Unload B ok, Hub frei"


def test_allow_write_false():
    opts = test_options(allow_write="False")
    cfg, bus, ctl = build_system(opts=opts)
    assert "allow_write=False" in ctl.describe_plan()
    for call, label in ((lambda: ctl.do_load("B"), "Load"),
                        (lambda: ctl.do_unload("B"), "Unload"),
                        (lambda: ctl.do_preload("B", 2), "Preload op2"),
                        (lambda: ctl.do_extrude("B"), "Extrude"),
                        (lambda: ctl.do_retrude("B"), "Retrude")):
        try:
            call()
        except PermissionError:
            pass
        else:
            raise AssertionError("%s hätte PermissionError werfen müssen" % label)
    mech = [c for c in bus.calls if c[1] in (0x0D, 0x04, 0x0F, 0x10, 0x11, 0xA0)]
    assert mech == [], "mechanische Frames trotz allow_write=False: %s" % (mech,)
    # Lesendes geht trotzdem (Poll + RFID + Remain + Version):
    poll = ctl.poll_status()
    assert poll["kind"] == "state", poll
    assert ctl.get_rfid("B") is not None
    assert isinstance(ctl.get_remain(), dict)
    assert ctl.get_version_sn()[0] == "153"
    # Cut = reine Kopfbewegung (16.10.2026): do_cut liefert nur den G-Code-
    # Plan und sendet KEIN Box-Frame (Beleg Live-Cut ohne Box-/Motor-Frame).
    n_cut = len(bus.calls)
    plan = ctl.do_cut("B")
    assert plan["status"] == "plan" and any("X-7.8" in l for l in plan["gcode"]), plan
    assert len(bus.calls) == n_cut, "do_cut hat gesendet!"
    assert all(c[1] != 0x50 and c[1] != 0x06 for c in bus.calls), "verbotenes fun!"
    return "keine mechanischen Frames (%d nur-lesend), Cut = Plan ohne Frame" % (
        len(bus.calls),)


def test_frame_folge_live():
    if not (_HAVE_SEQ and _HAVE_S485 and _HAVE_BOX):
        return "SKIP (nachbau fehlt)"
    # Extrude Slot B (Maske 0x02, pushes=5) == EXTRUDE_LIVE_CMDS_B
    cfg, bus, ctl = build_system()
    ctl.do_load("B")
    got = request_frames(bus)
    exp = SEQ.build_extrude_sequence(0x02, pushes=5)
    assert got == exp, "Extrude-Folge weicht ab:\n got %d\n exp %d" % (len(got), len(exp))
    live = SEQ.requests_from_log_cmds(SEQ.EXTRUDE_LIVE_CMDS_B)
    assert got == live, "Extrude != EXTRUDE_LIVE_CMDS_B"
    # Retrude Slot A (Maske 0x01, 4x p0 + p1) == RETRUDE_LIVE_CMDS_A (gefiltert)
    cfg2, bus2, ctl2 = build_system()
    ctl2.do_unload("A")
    got2 = request_frames(bus2)
    exp2 = SEQ.build_retrude_sequence(0x01)
    assert got2 == exp2, "Retrude-Folge weicht ab"
    live2 = SEQ.requests_from_log_cmds(SEQ.RETRUDE_LIVE_CMDS_A)
    live2f = SEQ.filter_funs(live2, (0x04, 0x08, 0x11))
    assert got2 == live2f, "Retrude != RETRUDE_LIVE_CMDS_A (gefiltert 04/08/11)"
    return "Extrude %d + Retrude %d Frames live-identisch" % (len(got), len(got2))


def test_shared_lock():
    lock = threading.Lock()
    box_bus = FakeBoxBus(lock=lock)
    mot_bus = FakeMotorBus(lock)
    cfg = K.parse_options(test_options())
    ctl = K.K2BoxController(cfg, box_bus, pause=lambda s: None)
    errors = []

    def _box():
        try:
            for _ in range(4):
                box_bus.transact_full(0x01, 0x0A, [], timeout=0.5, retries=0)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    def _mot():
        try:
            for _ in range(4):
                mot_bus.transact(0x81, 0x0C, [0x0B], timeout=0.5, retries=0)
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=_box), threading.Thread(target=_mot)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5.0)
    assert not any(t.is_alive() for t in threads), "Threads hängen (Deadlock?)"
    assert not errors, errors
    spans = sorted(box_bus.intervals + mot_bus.intervals)
    for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
        assert not (b0 < a1 - 1e-9 and a0 < b1 - 1e-9), \
            "Überlappung trotz gemeinsamem Lock: %s vs %s" % ((a0, a1), (b0, b1))
    return "8 Transaktionen (4 Box + 4 Motor) ohne Überlappung (1 Lock)"


def test_lesen_poll_status():
    opts = test_options(allow_write="False")  # lesend geht auch ohne Write
    cfg, bus, ctl = build_system(opts=opts)
    poll = ctl.poll_status()
    call0 = bus.calls[0]
    assert call0[1] == 0x0A and call0[3].get("retries") == 0, \
        "0x0a-Poll muss retries=0 haben (Original): %r" % (call0,)
    assert poll["kind"] == "state" and poll["temp_c"] == 22 \
        and poll["rh"] == 46, poll
    rfid = ctl.get_rfid("B")
    assert rfid is not None and "TAGB" in rfid, rfid
    remain = ctl.get_remain()
    assert remain["B"] == 29, remain
    ver, sn = ctl.get_version_sn()
    assert ver == "153" and sn == SN19.decode("ascii"), (ver, sn)
    hw = ctl.get_hardware_status()
    assert hw["all_ok"] is True, hw
    assert ctl.get_sensor(0) & 0x0F == 0x0F
    assert ctl.get_buffer() == 0
    st = ctl.get_status()
    assert st["state_name"] == "STATE_IDLE" and st["hub_mask"] == 0 \
        and st["temp_c"] == 22 and st["rh"] == 46 \
        and st["slots"]["B"]["remain"] == 29, st
    return "Poll retries=0, Temp 22 °C/46 %, RFID/Remain/Version/HW ok"


def test_preload_addr_guards():
    cfg, bus, ctl = build_system()
    # Preload OPs (alle 0-3 sendbar, op 2/3 mit langem Timeout + retries=0)
    for op in (0, 1, 2, 3):
        n = len(bus.calls)
        ctl.do_preload("C", op)
        _a, _f, data, kw = bus.calls[n]
        assert tuple(data) == (0x04, op), data
        if op in (2, 3):
            assert kw.get("retries") == 0, kw
            assert kw.get("timeout") >= 380.0, kw
        else:
            assert kw.get("retries") == 4, kw  # wie Original (Live-Mitschnitt)
    # Mechanik-Retries: 0x10/0x11 immer 0 (keine Bursts)
    bus.calls.clear()
    ctl.do_load("D")
    for _a, f, _d, kw in bus.calls:
        if f in (0x10, 0x11):
            assert kw.get("retries") == 0, (f, kw)
    # Adressvergabe erst nach k2_motor:ready
    cfg2, bus2, ctl2 = build_system()
    n0 = len(bus2.calls)
    res = ctl2.ensure_addr(motor_ready=False)
    assert res["status"] == "wait_motor" and len(bus2.calls) == n0, res
    res = ctl2.on_motor_ready()
    assert res["status"] == "ready" and ctl2.is_ready, res
    funs = {c[1] for c in bus2.calls}
    assert 0xA1 in funs and 0xA2 in funs, funs
    # Guards: SLOT/OP-Fehler senden NICHTS
    n1 = len(bus.calls)
    for bad_call in (lambda: ctl.do_load("E"), lambda: ctl.do_load(9),
                     lambda: ctl.do_preload("B", 4),
                     lambda: K.check_not_forbidden(0x50),
                     lambda: K.check_not_forbidden(0x06)):
        try:
            bad_call()
        except (ValueError, PermissionError):
            pass
        else:
            raise AssertionError("Guard hätte werfen müssen: %r" % (bad_call,))
    assert len(bus.calls) == n1, "Guards haben gesendet!"
    return "Preload 0-3 (Timeouts/Retries ok), Auto-Addr nach ready, Guards ok"


def test_gcodes():
    bus = FakeBoxBus()
    printer = FakePrinter(bus)
    cfg = FakeConfig(printer, {"box_addr": "1", "allow_write": "True",
                               "shutdown_on_failure": "False",
                               "auto_init": "True", "poll_interval": "4.0"})
    mod = K.PrinterK2Box(cfg)
    printer.fire("klippy:connect")
    mod._bus = bus  # ANNAHME-Bus injizieren (Test)
    # Düse heiß (Temperatur-Guard KTWO_BOX_LOAD/UNLOAD, 18.10.2026)
    printer.objects["extruder"] = _Obj({"temperature": 220.0, "target": 220.0,
                                        "can_extrude": True})
    mod.handle_motor_ready()
    assert mod.controller is not None and mod.controller.is_ready
    assert "k2_box:ready" in printer.events
    g = FakeGcmd({})
    mod.cmd_status(g)
    assert any("Zustand" in m for m in g.infos), g.infos
    g2 = FakeGcmd({"SLOT": "B"})
    mod.cmd_load(g2)
    assert any("ok" in m for m in g2.infos), g2.infos
    g3 = FakeGcmd({"SLOT": "B"})
    mod.cmd_unload(g3)
    assert any("ok" in m for m in g3.infos), g3.infos
    g4 = FakeGcmd({"SLOT": "C", "OP": "1"})
    mod.cmd_preload(g4)
    assert any("ok" in m for m in g4.infos), g4.infos
    g5 = FakeGcmd({"SLOT": "B", "DRY_RUN": "1"})
    n = len(bus.calls)
    mod.cmd_load(g5)
    assert len(bus.calls) == n and any("Trockenlauf" in m for m in g5.infos)
    st = mod.get_status()
    assert "hub_mask" in st and "slots" in st, st
    # Bus da, Port aber weg ([k2_485] ohne Adapter): G-Code-Fehler statt
    # "Internal error" + Shutdown (Batch-Probe G 16.10.2026).
    class DeadBus:
        def transact_full(self, *a, **k):
            raise RuntimeError("[k2_485] Port /dev/x nicht zu öffnen (Test)")
        transact = transact_full
    mod.controller, mod._bus = None, DeadBus()  # Controller neu mit totem Bus
    for fn, params in ((mod.cmd_load, {"SLOT": "B"}), (mod.cmd_unload, {"SLOT": "B"}),
                       (mod.cmd_preload, {"SLOT": "C", "OP": "1"})):
        try:
            fn(FakeGcmd(params))
        except ValueError as exc:  # FakeGcmd.error
            assert "RS485-Bus-Fehler" in str(exc), exc
        else:
            raise AssertionError("%s: Bus-Fehler ohne G-Code-Fehler" % fn.__name__)
    return ("STATUS/LOAD/UNLOAD/PRELOAD + DRY_RUN + get_status ok, Bus-Fehler -> "
            "G-Code-Fehler")


# =====================================================================
# Filamentwechsel (Ergänzung 16.10.2026) – Belege s. README-k2_box.md §8
# =====================================================================

class VClock:
    """Virtuelle Uhr für Controller-Tests (pause springt vor)."""

    def __init__(self):
        self.t = 0.0
        self.pauses = []

    def clock(self):
        return self.t

    def pause(self, s):
        self.pauses.append(float(s))
        self.t += float(s)


def build_vsystem(opts=None, **bus_kw):
    cfg, bus, ctl = build_system(opts, **bus_kw)
    vc = VClock()
    ctl.pause = vc.pause
    ctl.clock = vc.clock
    return cfg, bus, ctl, vc


def _frames(trace):
    return [(e[1], e[2]) for e in trace if e[0] == "bus"]


def test_wechsel_bausteine():
    c = K.parse_options(test_options())
    # Defaults = box.cfg + SAVE_CONFIG (cut_pos_x -7.80), Sperren aus
    assert c["cut_pos_x"] == -7.8 and c["pre_cut_pos_x"] == 10.0 \
        and c["pre_cut_pos_y"] == 200.0 and c["cut_pos_y"] == 200.0, c
    assert c["allow_move"] is False and c["pause_on_failure"] is True, c
    assert c["filament_sensor"] == "filament_sensor" and c["cut_sensor"] == ""
    # Stock-Schreibweise (Klipper schreibt Optionsnamen klein)
    c2 = K.parse_options(test_options(Tn_retrude="-12", cut_pos_x="-7.6"))
    assert c2["tn_retrude"] == -12.0 and c2["cut_pos_x"] == -7.6, c2
    for bad in ({"cut_pos_x": "-4.0"}, {"cut_pos_x": "-10"},
                {"tn_retrude": "10"}, {"cut_velocity": "0"},
                {"change_z_hop": "8"}, {"allow_move": "vielleicht"}):
        try:
            K.parse_options(test_options(**bad))
        except ValueError:
            pass
        else:
            raise AssertionError("Option hätte abgelehnt werden müssen: %r" % bad)
    # Schneidfahrt = T0-Beispiel Schritt 3-5 + CR_BOX_CUT (cut_pos_x -7.80)
    assert K.gcode_cut(c, 20000) == [
        "G90", "G0 X10 Y200 F12000", "SET_VELOCITY_LIMIT ACCEL=5000",
        "G0 X-7.8 Y200 F30000", "G4 P100", "G0 X10 F12000",
        "SET_VELOCITY_LIMIT ACCEL=20000", "M400"], K.gcode_cut(c, 20000)
    # Schacht (T0-Beispiel Schritt 6-6.3) / Verlassen (Schritt 11)
    assert K.gcode_go_extrude_pos(c) == [
        "G90", "G0 Y345 F12000", "G0 X139 F12000", "G0 Y378 F12000",
        "G0 X133 F12000"]
    assert K.gcode_safe_pos(c) == ["G90", "G0 X160 F12000", "G0 Y345 F12000"]
    # Abstreifen (10.1-10.3): Blech-Schnitt, 2 Kreise r=4, 3x Schütteln
    w = K.gcode_wipe(c, 20000)
    assert w[:5] == ["G90", "SET_VELOCITY_LIMIT ACCEL=5000", "G0 X160 F12000",
                     "G0 X135 F12000", "G0 X160 Y374 F12000"], w[:5]
    circ1, circ2 = w[5:21], w[22:38]
    assert w[21] == "G0 X170 Y374 F12000", w[21]

    def _pts(lines):
        out = []
        for l in lines:
            parts = dict((t[0], float(t[1:])) for t in l.split()[1:])
            out.append((parts["X"], parts["Y"], parts["F"]))
        return out
    for pts, cx, end in ((_pts(circ1), 164.0, (160.0, 374.0)),
                         (_pts(circ2), 166.0, (170.0, 374.0))):
        for x, y, f in pts:
            assert abs(math.hypot(x - cx, y - 374.0) - 4.0) < 0.01 and f == 10000
        assert abs(pts[-1][0] - end[0]) < 0.01 and abs(pts[-1][1] - end[1]) < 0.01
    # G2 = im Uhrzeigersinn: vom Startpunkt (160|374) zuerst nach oben (y+)
    assert _pts(circ1)[0][1] > 374.0 and _pts(circ2)[0][1] > 374.0
    assert w[38:44] == ["G0 X160 Y378 F12000", "G0 X133 Y378 F12000"] * 3, w[38:44]
    assert w[-1] == "SET_VELOCITY_LIMIT ACCEL=20000"
    # Spülen live: length [90, 70], check_and_extrude 18/72 und 14/56 mm
    assert K.flush_segments(c) == [90.0, 70.0]
    assert K.gcode_flush_segment(90, 360) == ["G91", "G1 E18 F360",
                                              "G1 E72 F360", "G90"]
    assert K.gcode_flush_segment(70, 360)[1:3] == ["G1 E14 F360", "G1 E56 F360"]
    assert K.flush_segments(c, 200) == [90.0, 90.0, 20.0]
    # Rückzug live 3x "extrude = -10.0" (= buffer_empty_len 30)
    assert K.retrude_extruder_steps(c) == [-10.0, -10.0, -10.0]
    assert K.retrude_extruder_steps(c, 25) == [-10.0, -10.0, -5.0]
    assert K.gcode_extruder_move(-10, 600) == ["G91", "G1 E-10 F600", "G90"]
    # TNN wie box.cfg M8200 L: addr = I/4+1, Slot = I%4
    for i, tnn in ((0, "T1A"), (1, "T1B"), (3, "T1D"), (4, "T2A"),
                   (9, "T3B"), (15, "T4D"), ("=2", "T1C")):
        assert K.tnn_from_index(i) == tnn, (i, K.tnn_from_index(i))
    assert K.parse_tnn("t1b") == (1, 1, "B", "T1B")
    for bad in (16, -1, "x", True):
        try:
            K.tnn_from_index(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("tnn_from_index(%r) hätte werfen müssen" % (bad,))
    for bad in ("T5A", "T1E", "1A", "T12"):
        try:
            K.parse_tnn(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("parse_tnn(%r) hätte werfen müssen" % (bad,))
    # Upstream-Namensregel für alle neuen G-Codes
    for name in ("KTWO_BOX_PRE_OPT", "KTWO_BOX_CUT", "KTWO_BOX_GO_EXTRUDE_POS",
                 "KTWO_BOX_SAFE_POS", "KTWO_BOX_RETRUDE", "KTWO_BOX_EXTRUDE",
                 "KTWO_BOX_FLUSH", "KTWO_BOX_WIPE", "KTWO_BOX_WASTE",
                 "KTWO_BOX_END_OPT", "KTWO_BOX_WAIT", "KTWO_BOX_ERROR_CLEAR",
                 "KTWO_BOX_RESUME_FIX"):
        assert upstream_gcode_name_ok(name), name
    return "Cut/Schacht/Abstreifen/Spülen/Rückzug == box.cfg + Live, TNN-Mapping ok"


def test_extrude_sensor_stage7():
    cfg, bus, ctl, vc = build_vsystem()
    bus.trace = []
    bus.head_at_mm = 200.0           # Kopfsensor nach 4 Polls (4 x 50 mm)
    bus.need_extruder_for_mode7 = True
    stage7 = []

    def _stage7():
        stage7.append(vc.t)
        bus.trace.append(("stage7",))
        bus.extruder_mm += 30.0      # Extruder greift (G1 E30 F360)

    res = ctl.do_extrude("B", sensor_fn=lambda: bus.head, sensor_timeout=90,
                         poll_interval=2.0, stage7_fn=_stage7, stage7_delay=2.0)
    assert res["status"] == "ok" and len(res["odo_mm"]) == 4, res
    assert ctl.loaded_slot == "B"
    seq = [e for e in bus.trace]
    names = []
    for e in seq:
        if e[0] == "stage7":
            names.append("S7")
        else:
            names.append("%02x:%s" % (e[1], ",".join("%02x" % b for b in e[2])))
    exp = (["04:00,01", "0f:01", "08:00", "10:02,00,00", "10:02,04,00"]
           + ["10:02,05,00"] * 4
           + ["10:02,06,00", "S7", "10:02,07,03", "05:", "04:02,00", "0f:00"])
    assert names == exp, names
    assert 2.0 in vc.pauses, vc.pauses  # stage7_delay vor mode 7
    # Ohne Stage 7 greift kein Extruder -> Box meldet 0x0e (mode 7)
    cfg2, bus2, ctl2, vc2 = build_vsystem()
    bus2.need_extruder_for_mode7 = True
    try:
        ctl2.do_extrude("C")
    except BOX.CfsStatusError as exc:
        assert exc.status == 0x0E, exc.status
    else:
        raise AssertionError("mode 7 ohne Extruder hätte 0x0e liefern müssen")
    # Kopfsensor nie erreicht -> Timeout, Box per 0x04 [00 01] gestoppt
    cfg3, bus3, ctl3, vc3 = build_vsystem()
    try:
        ctl3.do_extrude("D", sensor_fn=lambda: False, sensor_timeout=10,
                        poll_interval=2.0)
    except K.BoxChangeError as exc:
        assert "Kopf-Filamentsensor" in str(exc), exc
    else:
        raise AssertionError("Sensor-Timeout hätte BoxChangeError werfen müssen")
    last = bus3.calls[-1]
    assert last[1] == 0x04 and tuple(last[2]) == (0x00, 0x01), last
    polls = [c for c in bus3.calls if c[1] == 0x10 and c[2][1] == 0x05]
    assert 5 <= len(polls) <= 7, len(polls)  # ~alle 2 s in 10 s
    assert ctl3.loaded_slot is None
    return ("Einzug bis Kopfsensor (4 Polls), Stage 7 zwischen mode 6/7, "
            "0x0e ohne Extruder, Timeout -> IDLE")


def test_retrude_after_p0():
    cfg, bus, ctl, vc = build_vsystem()
    ctl.do_load("A")
    bus.trace = []
    marks = []

    def _after(i):
        marks.append(i)
        bus.trace.append(("E", i))

    ctl.do_retrude("A", after_p0=_after)
    names = [("E%d" % e[1]) if e[0] == "E" else
             "%02x:%s" % (e[1], ",".join("%02x" % b for b in e[2]))
             for e in bus.trace]
    assert names == ["04:00,01", "08:00", "11:01,00", "E0", "11:01,00", "E1",
                     "11:01,00", "E2", "11:01,00", "E3", "11:01,01"], names
    assert ctl.loaded_slot is None and bus.hub_mask & 0x01 == 0
    # Frames selbst unverändert == cfsseq-Live-Sequenz
    if _HAVE_SEQ and _HAVE_S485:
        got = [bytes(REF_BUILD(0x01, f, list(d), status=0xFF))
               for f, d in _frames(bus.trace)]
        assert got == SEQ.build_retrude_sequence(0x01), "Retrude-Frames weichen ab"
    return "Rückzug: p0 -> Extruder-Haken x4 -> p1, Frames live-identisch"


def test_wait_box_ready():
    cfg, bus, ctl, vc = build_vsystem()
    bus.preload_polls = 3
    st = ctl.wait_box_ready(timeout=60, interval=1.0)
    assert st["state_name"] == "STATE_IDLE", st
    assert len([c for c in bus.calls if c[1] == 0x0A]) == 4 and \
        vc.pauses == [1.0, 1.0, 1.0], vc.pauses
    bus.state, bus.stored_error = 4, 0x0C
    try:
        ctl.wait_box_ready(timeout=60)
    except BOX.CfsStatusError as exc:
        assert exc.status == 0x0C, exc.status
    else:
        raise AssertionError("ERROR-Zustand hätte CfsStatusError werfen müssen")
    bus.state, bus.stored_error = 0, 0
    bus.preload_polls = 10000
    try:
        ctl.wait_box_ready(timeout=5, interval=1.0)
    except K.BoxChangeError as exc:
        assert "nicht bereit" in str(exc), exc
    else:
        raise AssertionError("Timeout hätte BoxChangeError werfen müssen")
    return "PRELOAD -> warten -> IDLE, ERROR -> 0x0c, Timeout"


def build_hull(**opt_over):
    bus = FakeBoxBus()
    printer = FakePrinter(bus, with_motion=True)
    opts = test_options(allow_move="True")
    opts.update(opt_over)
    mod = K.PrinterK2Box(FakeConfig(printer, opts))
    printer.fire("klippy:connect")
    mod._bus = bus
    return bus, printer, mod


def run_chain(mod, tnn, ret=1, source="T"):
    """Kette wie macros.cfg _KTWO_TOOL_CHANGE (T-Befehl, SOURCE=T) -> alle
    Antworten. source="" = Kette wie M8200/von Hand (kein Nachholen)."""
    infos = []
    pre = {"TNN": tnn}
    if source:
        pre["SOURCE"] = source
    for fn, params in ((mod.cmd_pre_opt, pre), (mod.cmd_cut, {}),
                       (mod.cmd_retrude, {}), (mod.cmd_extrude, {"TNN": tnn}),
                       (mod.cmd_waste, {}), (mod.cmd_flush, {"TNN": tnn}),
                       (mod.cmd_end_opt, {"RETURN": str(ret)})):
        g = FakeGcmd(params)
        fn(g)
        infos.extend(g.infos)
    return infos


def _idx(trace, pred, start=0):
    for i in range(start, len(trace)):
        if pred(trace[i]):
            return i
    raise AssertionError("Eintrag fehlt ab %d" % start)


def _g(line):
    return lambda e: e[0] == "gcode" and e[1] == line


def _b(fun, data):
    return lambda e: e[0] == "bus" and e[1] == fun and tuple(e[2]) == tuple(data)


def test_wechsel_kette_voll():
    bus, printer, mod = build_hull(allow_write="True")
    bus.hub_mask, bus.head = 0x01, True   # A ist geladen
    bus.head_at_mm = 300.0
    bus.need_extruder_for_mode7 = True
    bus.trace = []
    run_chain(mod, "T1B")
    t = bus.trace
    i0 = _idx(t, _g("SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=0"))
    i1 = _idx(t, _g("SAVE_GCODE_STATE NAME=_ktwo_box_chain"), i0)
    i2 = _idx(t, _g("G1 Z0.4 F600"), i1)
    i3 = _idx(t, _g("G0 X-7.8 Y200 F30000"), i2)          # Schnitt
    i4 = _idx(t, _g("G0 X133 F12000"), i3)                # Schacht
    i5 = _idx(t, _g("M109 S220"), i4)
    i6 = _idx(t, _b(0x08, (0x01,)), i5)                   # Hub lesen
    p0 = i6
    for k in range(3):                                    # p0 + E-10 x3
        p0 = _idx(t, _b(0x11, (0x01, 0x00)), p0 + 1)
        _idx(t, _g("G1 E-10 F600"), p0)
        assert _idx(t, _g("G1 E-10 F600"), p0) < _idx(t, _b(0x11, (0x01, 0x00)), p0 + 1)
    i7 = _idx(t, _b(0x11, (0x01, 0x01)), p0)              # p1
    i8 = _idx(t, _b(0x10, (0x02, 0x06, 0x00)), i7)        # mode 6
    i9 = _idx(t, _g("G1 E30 F360"), i8)                   # Stage 7
    i10 = _idx(t, _b(0x10, (0x02, 0x07, 0x03)), i9)       # mode 7
    i11 = _idx(t, _b(0x04, (0x02, 0x00)), i10)            # PRINT
    i12 = _idx(t, _g("G1 E18 F360"), i11)
    i13 = _idx(t, _g("G1 E72 F432"), i12)                 # 80 % ×1,2 (Stock)
    i14 = _idx(t, _g("G0 X135 F12000"), i13)              # Blech 1
    i15 = _idx(t, _g("G1 E14 F360"), i14)
    i16 = _idx(t, _g("G1 E56 F360"), i15)
    i17 = _idx(t, _g("G0 X135 F12000"), i16)              # Abstreifen 2
    i18 = _idx(t, _g("G0 Y345 F12000"), i17)              # Schacht verlassen
    i19 = _idx(t, _g("G0 X200 Y200 F12000"), i18)         # Rückfahrt
    i20 = _idx(t, _g("G0 Z20 F600"), i19)
    i21 = _idx(t, _g("M104 S0"), i20)
    _idx(t, _g("RESTORE_GCODE_STATE NAME=_ktwo_box_chain"), i21)
    _idx(t, _g("SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=1"), i21)
    assert [round(v, 3) for v in printer.pos[:3]] == [200.0, 200.0, 20.0], printer.pos
    st = mod.get_status()
    assert st["loaded_tnn"] == "T1B" and not st["chain_active"] \
        and st["chain_error"] == "" and st["change_enabled"], st
    assert printer.accel == 20000.0, printer.accel
    assert not [m for m in printer.gcode.raw if m.startswith("!!")], printer.gcode.raw
    # Gleicher Slot nochmal: kein Wechsel (Hub B + Kopfsensor)
    n_bus = len([e for e in t if e[0] == "bus" and e[1] in (0x10, 0x11, 0x04)])
    n_g = len([e for e in t if e[0] == "gcode" and e[1].startswith(("G0", "G1"))])
    infos = run_chain(mod, "T1B")
    assert len([e for e in t if e[0] == "bus" and e[1] in (0x10, 0x11, 0x04)]) == n_bus
    assert len([e for e in t if e[0] == "gcode" and e[1].startswith(("G0", "G1"))]) == n_g
    assert any("bereits geladen" in m for m in infos), infos
    return "T1B: Hop→Schnitt→Schacht→E-10x3/p0→p1→Einzug→E30→mode7→Spülen 90+70→Rückfahrt; T1B erneut = nichts"


def test_wechsel_sperren():
    # 1) beide gesperrt, kein Druck: nur Plan, keine Bewegung, keine Mechanik
    bus, printer, mod = build_hull(allow_write="False", allow_move="False")
    infos = run_chain(mod, "T1C")
    moves = [l for l in printer.gcode.scripts
             if l.split()[0] in ("G0", "G1", "M109", "M104", "PAUSE")]
    assert moves == [], moves
    mech = [c for c in bus.calls if c[1] in (0x04, 0x0D, 0x0F, 0x10, 0x11, 0xA0)]
    assert mech == [], mech
    plans = [m for m in infos if "PLAN" in m]
    assert any("X-7.8" in m for m in plans) and any("PLAN Box EXTRUDE" in m
                                                     for m in plans), plans
    assert mod.get_status()["change_enabled"] is False
    # 2) Trockenlauf mit Bewegung (allow_move, ohne Box): Kopf + Extruder ja, Box nein
    bus, printer, mod = build_hull(allow_write="False", allow_move="True")
    run_chain(mod, "T1C")
    sc = printer.gcode.scripts
    assert "G0 X-7.8 Y200 F30000" in sc and sc.count("G1 E-10 F600") == 3 \
        and "G1 E30 F360" in sc and "G1 E18 F360" in sc, sc
    mech = [c for c in bus.calls if c[1] in (0x04, 0x0D, 0x0F, 0x10, 0x11, 0xA0)]
    assert mech == [], mech
    # 3) Im Druck ohne Freigabe: PAUSE, Rest übersprungen, keine Bewegung
    bus, printer, mod = build_hull(allow_write="False", allow_move="True")
    printer.set_printing(True)
    infos = run_chain(mod, "T1B")
    sc = printer.gcode.scripts
    assert "PAUSE" in sc and not [l for l in sc if l.startswith(("G0", "G1"))], sc
    st = mod.get_status()
    assert "nicht freigegeben" in st["chain_error"] and not st["chain_active"], st
    assert any("übersprungen" in m for m in infos), infos
    assert printer.sensor.enabled, "Kopfsensor muss wieder aktiv sein"
    mod.cmd_error_clear(FakeGcmd({}))
    assert mod.get_status()["chain_error"] == ""
    return "gesperrt = nur Plan; allow_move ohne Box = Trockenlauf; Druck ohne Freigabe = PAUSE"


def test_wechsel_fehler_im_druck():
    bus, printer, mod = build_hull(allow_write="True")
    bus.fail_after_mm = 245.0          # Slot A: 0x0c wie Kanal-A-Defekt
    bus.head_at_mm = 1000.0
    printer.set_printing(True)
    bus.trace = []
    run_chain(mod, "T1A")
    sc = printer.gcode.scripts
    # seit 17.10.2026: PAUSE trägt das Rückfahrziel = Druckposition vor dem
    # Wechsel (200/200/20, Z ohne Hop) – Fehlerposition ist der Schacht
    want = "PAUSE CFS_RETURN_X=200 CFS_RETURN_Y=200 CFS_RETURN_Z=20"
    assert want in sc and "PAUSE" not in sc, sc
    st = mod.get_status()
    assert "0x0c" in st["chain_error"] and not st["chain_active"], st
    assert st["pending_resume_z"] == 20.0, st
    assert any(m.startswith("!!") and "PAUSIERT" in m for m in printer.gcode.raw)
    # Prüfhinweis (25.10.2026) als eigene !!-Zeile, u. a. Zufuhrschlauch
    assert any(m.startswith("!!") and "Bitte überprüfen Sie: 1) Zufuhrschlauch"
               in m for m in printer.gcode.raw), printer.gcode.raw
    i_pause = sc.index(want)
    assert not [l for l in sc[i_pause:] if l.startswith(("G0", "G1"))], sc[i_pause:]
    assert printer.sensor.enabled and "RESTORE_GCODE_STATE NAME=_ktwo_box_chain" \
        in sc[i_pause:]
    # PRE_OPT mit offenem Fehler: nichts, keine zweite Pause-Bewegung
    n = len(sc)
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1A"}))
    mod.cmd_cut(FakeGcmd({}))
    assert not [l for l in sc[n:] if l.startswith(("G0", "G1"))], sc[n:]
    mod.cmd_end_opt(FakeGcmd({}))
    # Quittieren: Box 0x04 [00 01] (löscht ERROR) + RESUME_FIX senkt Z
    mod.cmd_error_clear(FakeGcmd({}))
    assert bus.calls[-1][1] == 0x04 and tuple(bus.calls[-1][2]) == (0, 1)
    mod.cmd_resume_fix(FakeGcmd({}))
    assert "G1 Z20 F600" in printer.gcode.scripts
    assert mod.get_status()["pending_resume_z"] is None
    return "0x0c im Druck -> PAUSE (kein Abbruch), Folgeschritte übersprungen, Quittung + Z-Fix"


def test_wechsel_fehler_ohne_druck():
    # Box-Fehler außerhalb des Drucks -> G-Code-Fehler, Kette aufgeräumt
    bus, printer, mod = build_hull(allow_write="True")
    bus.fail_after_mm = 245.0
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1A"}))
    try:
        mod.cmd_extrude(FakeGcmd({"TNN": "T1A"}))
    except ValueError as exc:
        assert "0x0c" in str(exc) and "Bitte überprüfen Sie" in str(exc), exc
    else:
        raise AssertionError("Box-Fehler ohne Druck hätte G-Code-Fehler werfen müssen")
    assert not mod.get_status()["chain_active"] and printer.sensor.enabled
    assert "PAUSE" not in printer.gcode.scripts
    # Bewegungsfehler im Schnitt -> Beschleunigung zurück + Fehler
    bus, printer, mod = build_hull(allow_write="True")
    printer.fail_on = "G0 X-7.8"
    try:
        mod.cmd_cut(FakeGcmd({}))
    except ValueError as exc:
        assert "CUT fehlgeschlagen" in str(exc), exc
    else:
        raise AssertionError("Bewegungsfehler hätte G-Code-Fehler werfen müssen")
    assert printer.accel == 20000.0, printer.accel
    # Box 2 nicht konfiguriert / Hub durch anderen Slot belegt
    bus, printer, mod = build_hull(allow_write="True")
    for params, text in (({"TNN": "T2A"}, "Box 2"), ({"TNN": "T1B"}, "Hub belegt")):
        bus.hub_mask = 0x01 if params["TNN"] == "T1B" else 0
        try:
            mod.cmd_extrude(FakeGcmd(params))
        except ValueError as exc:
            assert text in str(exc), exc
        else:
            raise AssertionError("%s hätte abgelehnt werden müssen" % params)
    mech = [c for c in bus.calls if c[1] in (0x10, 0x11)]
    assert mech == [], mech
    # Ohne Bus (RS485 fehlt): klare Meldung, kein Absturz
    bus2 = FakeBoxBus()
    printer2 = FakePrinter(bus2, with_motion=True)
    del printer2.objects["k2_485"]
    mod2 = K.PrinterK2Box(FakeConfig(printer2, test_options(allow_move="True")))
    try:
        mod2.cmd_retrude(FakeGcmd({}))
    except ValueError as exc:
        assert "k2_485" in str(exc), exc
    else:
        raise AssertionError("ohne Bus hätte G-Code-Fehler kommen müssen")
    return "ohne Druck: G-Code-Fehler + Aufräumen; Box 2/Hub belegt/kein Bus abgelehnt"


# =====================================================================
# RESUME nach abgebrochenem CFS-Wechsel im Schneidbereich (17.10.2026,
# README-k2_box.md §10)
# =====================================================================

def _hull_t1b_ok(**over):
    """Hülle wie test_wechsel_kette_voll: Slot A geladen, T1B läuft durch."""
    bus, printer, mod = build_hull(allow_write="True", **over)
    bus.hub_mask, bus.head = 0x01, True
    bus.head_at_mm = 300.0
    bus.need_extruder_for_mode7 = True
    return bus, printer, mod


def _set_pos(printer, x, y, z):
    printer.pos[:3] = [float(x), float(y), float(z)]
    printer._refresh()


def test_resume_ziel_nach_abbruch():
    # 1) Baustein: Schneidbereich nur in X verlassen (box.cfg:13 pre_cut_pos_x)
    c = dict(K.CHANGE_DEFAULTS)
    assert K.gcode_leave_cut_zone(c, [10.0, 200.0, 5.0]) == []
    assert K.gcode_leave_cut_zone(c, [9.98, 200.0, 5.0]) == ["G0 X10 F12000", "M400"]
    assert K.gcode_leave_cut_zone(c, [-7.8, 200.0, 5.0]) == ["G0 X10 F12000", "M400"]
    assert K.gcode_leave_cut_zone(c, None) == []
    # 2) T-Kette im Druck, Schnitt bricht AM ANSCHLAG ab (nach G0 X-7.8,
    #    vor dem Rückweg G0 X10) -> PAUSE mit Rückfahrziel Druckposition
    bus, printer, mod = _hull_t1b_ok()
    printer.set_printing(True)
    _set_pos(printer, 110, 120, 10)
    printer.fail_on = "G4 P"
    run_chain(mod, "T1B")
    sc = printer.gcode.scripts
    assert [round(v, 3) for v in printer.pos[:3]] == [-7.8, 200.0, 10.4], \
        printer.pos
    want = "PAUSE CFS_RETURN_X=110 CFS_RETURN_Y=120 CFS_RETURN_Z=10"
    assert want in sc and "PAUSE" not in sc, sc
    i = sc.index(want)
    assert not [l for l in sc[i:] if l.startswith(("G0", "G1"))], sc[i:]
    st = mod.get_status()
    assert "CUT fehlgeschlagen" in st["chain_error"] and not st["chain_active"]
    assert st["pending_resume_z"] == 10.0, st   # Fallback ohne PAUSE-Makro
    assert any("Rückfahrziel für RESUME = Druckposition vor dem Wechsel "
               "X110 Y120 Z10" in m for m in printer.gcode.raw), printer.gcode.raw
    assert printer.accel == 20000.0 and printer.sensor.enabled
    assert mod._keep_start_pos == [110.0, 120.0, 10.0]
    # 3) Wiederholung IN DER PAUSE bricht erneut am Hebel ab: kein zweites
    #    PAUSE, kein neues Ziel/Z, Startposition = die des 1. Wechsels
    assert printer.paused
    mod.cmd_error_clear(FakeGcmd({}))
    n = len(sc)
    run_chain(mod, "T1B")
    assert not [l for l in sc[n:] if l.startswith("PAUSE")], sc[n:]
    assert sc[n:].count("G0 X10 Y200 F12000") == 1   # vom Hebel nur in X weg
    assert mod.get_status()["pending_resume_z"] == 10.0
    assert mod._keep_start_pos == [110.0, 120.0, 10.0]
    # 4) erfolgreiche Wiederholung in der Pause: END_OPT fährt X/Y zur
    #    Startposition, senkt Z aber NICHT (das macht RESUME)
    printer.fail_on = None
    mod.cmd_error_clear(FakeGcmd({}))
    bus.trace = []
    infos = run_chain(mod, "T1B")
    t = bus.trace
    i1 = _idx(t, _g("G0 X10 Y200 F12000"))
    assert not [e for e in t[:i1] if e[0] == "gcode" and e[1].startswith(("G0", "G1"))
                and "Y" in e[1] and "Z" not in e[1]], t[:i1]
    i2 = _idx(t, _g("G0 X110 Y120 F12000"), i1)
    assert not [e for e in t[i2:] if e[0] == "gcode" and e[1].startswith("G0 Z")], t[i2:]
    assert round(printer.pos[2], 3) > 10.0 and printer.pos[:2] == [110.0, 120.0]
    assert any("Z bleibt" in m for m in infos), infos
    st = mod.get_status()
    assert st["loaded_tnn"] == "T1B" and st["chain_error"] == "" \
        and mod._keep_start_pos is None, st
    # 5) Startposition eines früheren Abbruchs veraltet nicht: nach CANCEL
    #    (keine Pause mehr) nimmt ein neuer Wechsel die AKTUELLE Position
    bus, printer, mod = _hull_t1b_ok()
    printer.set_printing(True)
    _set_pos(printer, 110, 120, 10)
    printer.fail_on = "G4 P"
    run_chain(mod, "T1B")
    assert mod._keep_start_pos == [110.0, 120.0, 10.0]
    printer.paused, printer.fail_on = False, None      # CANCEL + neuer Druck
    printer.set_printing(True)
    mod.cmd_error_clear(FakeGcmd({}))
    _set_pos(printer, 50, 60, 5)
    bus.trace = []
    run_chain(mod, "T1B")
    t = bus.trace
    i3 = _idx(t, _g("G0 X50 Y60 F12000"))
    _idx(t, _g("G0 Z5 F600"), i3)
    assert not [e for e in t if e[0] == "gcode" and e[1].startswith("G0 X110 Y120")]
    assert [round(v, 3) for v in printer.pos[:3]] == [50.0, 60.0, 5.0]
    # 6) Einzelschritt (M8200 C ohne P) im Druck: Ziel = Position davor
    bus, printer, mod = _hull_t1b_ok()
    printer.set_printing(True)
    _set_pos(printer, 80, 90, 7)
    printer.fail_on = "G4 P"
    mod.cmd_cut(FakeGcmd({}))
    assert "PAUSE CFS_RETURN_X=80 CFS_RETURN_Y=90 CFS_RETURN_Z=7" \
        in printer.gcode.scripts, printer.gcode.scripts
    assert mod.get_status()["pending_resume_z"] is None and mod._step_start is None
    # 7) Fehler OHNE Bewegung (Druck ohne Freigabe): PAUSE ohne Ziel
    bus, printer, mod = build_hull(allow_write="False", allow_move="True")
    printer.set_printing(True)
    run_chain(mod, "T1B")
    assert "PAUSE" in printer.gcode.scripts and not [
        l for l in printer.gcode.scripts if l.startswith("PAUSE ")]
    return ("Hebel-Abbruch im Druck -> PAUSE CFS_RETURN=Druckpos (Z ohne Hop); "
            "Wiederholung in der Pause ohne neue Pause, END_OPT ohne Z-Senkung; "
            "alte Startposition verworfen; Einzelschritt; ohne Bewegung kein Ziel")


def test_schacht_aus_schneidbereich_und_resume_fix():
    # Kopf am Hebel (abgebrochener Schnitt außerhalb/innerhalb einer Pause,
    # Schritt von Hand wiederholt): erst NUR X auf pre_cut_pos_x, dann Y345
    bus, printer, mod = build_hull(allow_write="False", allow_move="True")
    _set_pos(printer, -7.8, 200, 20)
    bus.trace = []
    mod.cmd_go_extrude_pos(FakeGcmd({}))
    moves = [e[1] for e in bus.trace if e[0] == "gcode"
             and e[1].startswith(("G0", "G1", "M400"))]
    assert moves == ["G0 X10 F12000", "M400", "G0 Y345 F12000", "G0 X139 F12000",
                     "G0 Y378 F12000", "G0 X133 F12000"], moves
    # außerhalb des Schneidbereichs unverändert (Stock T0-Beispiel 6.)
    _set_pos(printer, 10, 200, 20)
    bus.trace = []
    mod.cmd_go_extrude_pos(FakeGcmd({}))
    moves = [e[1] for e in bus.trace if e[0] == "gcode"
             and e[1].startswith(("G0", "G1"))]
    assert moves[0] == "G0 Y345 F12000", moves
    # RESUME_FIX CLEAR=1 (aus PAUSE): verwirft die Z-Korrektur ohne Fahrt
    mod.pending_resume_z = 12.0
    n = len(printer.gcode.scripts)
    g = FakeGcmd({"CLEAR": "1"})
    mod.cmd_resume_fix(g)
    assert mod.get_status()["pending_resume_z"] is None
    assert any("verworfen" in m for m in g.infos), g.infos
    g = FakeGcmd({"CLEAR": "1"})
    mod.cmd_resume_fix(g)
    assert g.infos == [] and printer.gcode.scripts[n:] == []
    mod.cmd_resume_fix(FakeGcmd({}))           # nichts offen -> nichts
    assert printer.gcode.scripts[n:] == []
    return "Schacht-Einfahrt vom Hebel: X10 vor Y345; RESUME_FIX CLEAR=1 ohne Fahrt"


def _render_env():
    import jinja2
    path = os.path.join(_HERE, "config-pi", "macros.cfg")
    macros = _macro_sources(path)
    kcfg = {}
    sec = open(os.path.join(_HERE, "config-pi", "k2-module.cfg"),
               encoding="utf-8").read()
    # Abschnittskopf am Zeilenanfang (Fix 17.10. spät: "[k2_box]" steht
    # vorher schon in einem Kommentar -> kcfg war leer, Render nur Defaults)
    a = sec.index("\n[k2_box]\n") + 1
    for ln in sec[a:sec.index("\n[", a + 1)].splitlines()[1:]:
        if ":" in ln and not ln.startswith("#"):
            k, v = ln.split(":", 1)
            kcfg[k.strip().lower()] = v.strip()

    def pyval(v):
        if v in ("True", "False"):
            return v == "True"
        try:
            return float(v) if "." in v else int(v)
        except ValueError:
            return v
    param = {k: pyval(v) for k, v in macros["_PI_PARAM"][1].items()}
    return jinja2.Environment("{%", "%}", "{", "}"), macros, kcfg, param


class _RenderRaise(Exception):
    pass


def _rprinter(param, kcfg, **upd):
    pr = {
        "gcode_macro _PI_PARAM": dict(param, hotend_temp=220, pause_x=110.0,
                                      pause_y=120.0, pause_z=10.0,
                                      pause_pos_valid=True),
        "gcode_macro _PI_FREIGABE": {"allow_move": True},
        "k2_box": {"allow_move": True, "allow_write": True,
                   "change_enabled": True, "chain_active": False,
                   "chain_error": ""},
        "toolhead": {"homed_axes": "xyz", "max_accel": 20000.0,
                     "position": {"x": 342.5, "y": 200.0, "z": 50.0},
                     "axis_maximum": {"x": 352.5, "y": 400.0}},
        "gcode_move": {"gcode_position": {"x": 342.5, "y": 200.0, "z": 50.0,
                                          "e": 0.0}},
        "pause_resume": {"is_paused": True},
        "print_stats": {"state": "printing"},
        "extruder": {"temperature": 220.0, "target": 220.0, "can_extrude": True},
        "configfile": {"config": {"k2_box": dict(kcfg)},
                       "settings": {"extruder": {"min_extrude_temp": 170.0}}},
        "output_pin fan0": {"value": 0.0}, "output_pin fan2": {"value": 0.0},
    }
    for key, val in upd.items():
        pr[key].update(val)
    return pr


def _render(env, macros, name, printer, params=None):
    msgs = []

    def _info(m):
        msgs.append(m)
        return ""

    def _err(m):
        raise _RenderRaise(m)
    out = env.from_string(macros[name][0]).render(
        params=dict(params or {}), rawparams="", printer=printer,
        action_respond_info=_info, action_raise_error=_err)
    return [l.strip() for l in out.split("\n")
            if l.strip() and not l.strip().startswith("#")], msgs


def test_makros_resume_schneidbereich():
    try:
        env, macros, kcfg, param = _render_env()
    except ImportError:
        return "SKIP (jinja2 fehlt)"
    for n in ("PAUSE", "_PI_RESUME_PREPARE", "_PI_RESUME_RETURN",
              "_PI_RESUME_REBASE", "_PI_CLEAN_PATH"):
        assert n in macros, n
    assert param.get("pause_rebase") is False, param
    R = lambda name, pr, params=None: _render(env, macros, name, pr, params)  # noqa: E731
    pos = lambda x, y, z: {"gcode_position": {"x": x, "y": y, "z": z, "e": 0.0}}  # noqa: E731
    tpos = lambda x, y, z: {"position": {"x": x, "y": y, "z": z}}  # noqa: E731

    def raises(name, pr, txt, params=None):
        try:
            lines, _m = R(name, pr, params)
        except _RenderRaise as exc:
            assert all(t in str(exc) for t in txt), exc
            return str(exc)
        raise AssertionError("%s hätte verweigern müssen: %s" % (name, lines))

    # --- PAUSE normal: Ziel = Position, kein Rebase, Altlast-Z weg -------
    pr = _rprinter(param, kcfg, gcode_move=pos(110.0, 120.0, 10.0),
                   toolhead=tpos(110.0, 120.0, 10.0),
                   pause_resume={"is_paused": False})
    lines, msgs = R("PAUSE", pr)
    i = lines.index("PAUSE_BASE")
    assert lines[i + 1:i + 7] == [
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_x VALUE=110.0",
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_y VALUE=120.0",
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_z VALUE=10.0",
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_pos_valid VALUE=True",
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_rebase VALUE=False",
        "KTWO_BOX_RESUME_FIX CLEAR=1"], lines
    mv = [l for l in lines if l.startswith("G1 ")]
    assert mv[0] == "G1 E-1.0 F180" and "G1 X10.0 F12000.0" not in lines \
        and "G1 Y345.0 F12000.0" not in lines and msgs == [], (lines, msgs)
    # --- PAUSE nach Abbruch AM HEBEL (k2_box: CFS_RETURN = Druckposition) --
    pr = _rprinter(param, kcfg, gcode_move=pos(-7.8, 200.0, 10.4),
                   toolhead=tpos(-7.8, 200.0, 10.4),
                   pause_resume={"is_paused": False})
    cfs = {"CFS_RETURN_X": "110", "CFS_RETURN_Y": "120", "CFS_RETURN_Z": "10"}
    lines, msgs = R("PAUSE", pr, cfs)
    i = lines.index("PAUSE_BASE")
    assert lines[i + 1:i + 7] == [
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_x VALUE=110.0",
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_y VALUE=120.0",
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_z VALUE=10.0",
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_pos_valid VALUE=True",
        "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_rebase VALUE=True",
        "KTWO_BOX_RESUME_FIX CLEAR=1"], lines
    assert msgs and "Fehlerposition X-7.80 Y200.00 Z10.40" in msgs[0] \
        and "X110.00 Y120.00 Z10.00" in msgs[0], msgs
    mv = [l for l in lines[i:] if l.startswith(("G1 ", "G90", "G91"))]
    assert mv[:3] == ["G90", "G1 X10.0 F12000.0", "G91"], mv   # erst nur X raus
    assert lines.index("G1 X10.0 F12000.0") < lines.index("G1 Z39.6 F600") \
        < lines.index("G1 X342.5 Y200.0 F6000"), lines
    # --- PAUSE nach Abbruch IM SCHACHT: Z-Hub, X160, Y345, dann Park -------
    pr = _rprinter(param, kcfg, gcode_move=pos(133.0, 378.0, 10.4),
                   toolhead=tpos(133.0, 378.0, 10.4),
                   pause_resume={"is_paused": False})
    lines, _m = R("PAUSE", pr, cfs)
    mv = [l for l in lines if l.startswith("G1 ") and "E" not in l]
    assert mv == ["G1 Z39.6 F600", "G1 X160.0 F12000.0", "G1 Y345.0 F12000.0",
                  "G1 X342.5 Y200.0 F6000"], mv
    # --- PREPARE: Rebase nach der Rückfahrt -------------------------------
    pr = _rprinter(param, kcfg)
    pr["gcode_macro _PI_PARAM"]["pause_rebase"] = True
    lines, _m = R("_PI_RESUME_PREPARE", pr)
    assert lines == ["SAVE_GCODE_STATE NAME=_pi_resume_prep",
                     "_PI_NOZZLE_CLEAN ONLY=GO WHY=RESUME", "M109 S220",
                     "_PI_RESUME_PRIME", "_PI_NOZZLE_CLEAN WHY=RESUME",
                     "_PI_RESUME_RETURN",
                     "RESTORE_GCODE_STATE NAME=_pi_resume_prep",
                     "_PI_RESUME_REBASE"], lines
    # ohne Bewegungsfreigabe: verweigert (RESUME_BASE führe zur Fehlerpos.)
    pr["gcode_macro _PI_FREIGABE"]["allow_move"] = False
    raises("_PI_RESUME_PREPARE", pr, ["RESUME gesperrt", "abgebrochenem CFS",
                                      "_PI_FREIGABE allow_move=False"])
    pr = _rprinter(param, kcfg)
    pr["gcode_macro _PI_PARAM"]["pause_rebase"] = True
    pr["k2_box"]["allow_move"] = False
    raises("_PI_RESUME_PREPARE", pr, ["RESUME gesperrt", "[k2_box] allow_move"])
    # Kopf am Hebel (Abbruch in einer Pause): erst nur X, dann Schacht
    pr = _rprinter(param, kcfg, gcode_move=pos(-7.8, 200.0, 50.4))
    lines, msgs = R("_PI_RESUME_PREPARE", pr)
    assert lines[:5] == ["SAVE_GCODE_STATE NAME=_pi_resume_prep", "G90",
                         "G1 X10.0 F12000.0", "M400",
                         "_PI_NOZZLE_CLEAN ONLY=GO WHY=RESUME"], lines
    assert "_PI_RESUME_REBASE" not in lines and "Schneidbereich" in msgs[0]
    pr["k2_box"]["allow_move"] = False
    raises("_PI_RESUME_PREPARE", pr, ["RESUME gesperrt", "Schneidbereich X-7.80"])
    # Ziel am Hebel: immer verweigert, vor jeder Zeile
    pr = _rprinter(param, kcfg)
    pr["gcode_macro _PI_PARAM"].update(pause_x=-7.8, pause_y=200.0)
    raises("_PI_RESUME_PREPARE", pr, ["RESUME gesperrt", "Schneidhebel"])
    # nicht referenziert: alter Pfad (nur M109), kein Fehlalarm X0 < 10
    pr = _rprinter(param, kcfg, gcode_move=pos(0.0, 0.0, 0.0),
                   toolhead={"homed_axes": ""})
    lines, msgs = R("_PI_RESUME_PREPARE", pr)
    assert lines == ["M109 S220"] and "nicht referenziert" in msgs[0], (lines, msgs)
    # --- RETURN -----------------------------------------------------------
    pr = _rprinter(param, kcfg, gcode_move=pos(160.0, 345.0, 50.0))
    pr["gcode_macro _PI_PARAM"]["pause_rebase"] = True
    lines, msgs = R("_PI_RESUME_RETURN", pr)
    assert lines == ["G90", "G0 X110.0 Y120.0 F12000.0", "G1 Z10.0 F600.0"] \
        and "vor dem abgebrochenen CFS-Wechsel" in msgs[0], (lines, msgs)
    pr["gcode_macro _PI_PARAM"].update(pause_x=5.0)          # Bettkante
    lines, _m = R("_PI_RESUME_RETURN", pr)
    assert lines == ["G90", "G0 X10.0 Y120.0 F12000.0", "G0 X5.0 F12000.0",
                     "G1 Z10.0 F600.0"], lines
    pr = _rprinter(param, kcfg, gcode_move=pos(-7.8, 200.0, 50.0))
    lines, _m = R("_PI_RESUME_RETURN", pr)
    assert lines == ["G90", "G0 X10.0 F12000.0", "M400",
                     "G0 X110.0 Y120.0 F12000.0", "G1 Z10.0 F600.0"], lines
    pr["k2_box"]["chain_error"] = "[k2_box] GO_EXTRUDE_POS fehlgeschlagen: x"
    raises("_PI_RESUME_RETURN", pr, ["vor der Rückfahrt", "GO_EXTRUDE_POS"])
    # --- REBASE -----------------------------------------------------------
    pr = _rprinter(param, kcfg, gcode_move=pos(110.0, 120.0, 10.0))
    lines, msgs = R("_PI_RESUME_REBASE", pr)
    assert lines == ["RESTORE_GCODE_STATE NAME=PAUSE_STATE",
                     "SAVE_GCODE_STATE NAME=PAUSE_STATE",
                     "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=pause_rebase "
                     "VALUE=False"], lines
    pr = _rprinter(param, kcfg, gcode_move=pos(110.0, 120.5, 10.0))
    raises("_PI_RESUME_REBASE", pr, ["nicht an der Druckposition",
                                     "PAUSE_STATE unverändert"])
    # --- _PI_CLEAN_PATH GO vom Hebel == k2_box _go_extrude_pos -------------
    pr = _rprinter(param, kcfg, gcode_move=pos(-7.8, 200.0, 20.0))
    lines, _m = R("_PI_CLEAN_PATH", pr, {"PART": "GO"})
    c = dict(K.CHANGE_DEFAULTS)
    exp = K.gcode_go_extrude_pos(c)
    exp = exp[:1] + K.gcode_leave_cut_zone(c, [-7.8, 200.0]) + exp[1:]
    norm = lambda l: [(w[0], float(w[1:])) if w[0] in "XYF" and len(w) > 1  # noqa: E731
                      else w for w in l.split()]
    assert [norm(l) for l in lines] == [norm(l) for l in exp], (lines, exp)
    return ("PAUSE (normal/Hebel/Schacht), PREPARE (Rebase, Sperren, Hebel), "
            "RETURN (Ziel Bettkante, vom Hebel, chain_error), REBASE, CLEAN_PATH")


# =====================================================================
# RESUME nach abgebrochenem Wechsel: Kopfsensor-Sperre / Nachholen
# (17.10.2026 spät, README-k2_box.md §11)
# =====================================================================

def _hull_abort_leer(source="T", target=0.0, **over):
    """Druck mit T1B (Hub B, Kopf voll) -> T0 (= T1A): Schnitt + Rückzug B
    laufen (Kopf danach LEER), Einzug A bricht mit 0x0c ab -> _fail ->
    PAUSE. "PAUSE" ruft wie das Makro KTWO_BOX_RESUME_FIX CLEAR=1."""
    bus, printer, mod = build_hull(allow_write="True", **over)
    bus.hub_mask, bus.head = 0x02, True
    bus.fail_after_mm = 100.0
    bus.need_extruder_for_mode7 = True
    printer.objects["gcode_macro _PI_PARAM"] = _Obj({"hotend_temp": 235.0})

    def fake_pause(g):                       # = macros.cfg PAUSE (Kern)
        printer.sim("PAUSE")
        mod.cmd_resume_fix(FakeGcmd({"CLEAR": "1"}))
    printer.gcode.commands["PAUSE"] = fake_pause
    printer.set_printing(True)
    printer.target = float(target)          # Druck-Zieltemperatur (M104)
    _set_pos(printer, 110, 120, 10)
    run_chain(mod, "T1A", source=source)
    return bus, printer, mod


def _check_raises(fn, params, texts):
    try:
        fn(FakeGcmd(params))
    except ValueError as exc:
        assert all(t in str(exc) for t in texts), (texts, str(exc))
        return str(exc)
    raise AssertionError("hätte gesperrt sein müssen: %s" % (texts,))


def _moves(sc):
    return [l for l in sc if l.split()[0] in ("G0", "G1", "M109", "M104",
                                              "PAUSE", "RESUME")]


def test_resume_kopf_sperre():
    # 1) Kopf LEER nach Abbruch: gesperrt (Default resume_redo_change AUS)
    bus, printer, mod = _hull_abort_leer()
    sc = printer.gcode.scripts
    assert "PAUSE CFS_RETURN_X=110 CFS_RETURN_Y=120 CFS_RETURN_Z=10" in sc, sc
    st = mod.get_status()
    assert "EXTRUDE fehlgeschlagen" in st["chain_error"] and printer.paused
    assert st["change_aborted"] and st["resume_tnn"] == "T1A" \
        and st["head_filament"] is False and not st["resume_redo_change"], st
    assert any("KTWO_BOX_ERROR_CLEAR, Wechsel (T<n>) bzw. Schritt von Hand "
               "wiederholen, dann RESUME" in m for m in printer.gcode.raw)
    # offener Fehler hat Vorrang (wie das RESUME-Makro)
    _check_raises(mod.cmd_resume_check, {"MOVE": "1"},
                  ["RESUME gesperrt", "CFS-Wechsel fehlgeschlagen"])
    g = FakeGcmd({})
    mod.cmd_error_clear(g)
    assert any("RESUME prüft den Kopf-Filamentsensor" in m for m in g.infos)
    n, nb = len(sc), len(bus.calls)
    msg = _check_raises(mod.cmd_resume_check, {"MOVE": "1"}, [
        "RESUME gesperrt", "filament_sensor meldet KEIN Filament",
        "EXTRUDE fehlgeschlagen; Ziel T1A = T0", "PAUSIERT",
        "T0 (lädt T1A), dann RESUME", "resume_redo_change: False"])
    assert _moves(sc[n:]) == [] and len(bus.calls) == nb, (sc[n:], msg)
    assert printer.paused and mod.get_status()["change_aborted"]
    # HEAD_LOADED=1 gegen einen LEER meldenden Sensor: abgelehnt
    g = FakeGcmd({"HEAD_LOADED": "1"})
    mod.cmd_error_clear(g)
    assert any("HEAD_LOADED abgelehnt" in m for m in g.infos), g.infos
    _check_raises(mod.cmd_resume_check, {"MOVE": "1"}, ["KEIN Filament"])
    # 2) Wechsel von Hand in der Pause (T0) -> Kopf voll -> RESUME frei
    bus.fail_after_mm = None
    bus.head_at_mm = bus.odo + 100.0
    run_chain(mod, "T1A")
    st = mod.get_status()
    assert st["head_filament"] is True and st["loaded_tnn"] == "T1A" \
        and st["chain_error"] == "", st
    g = FakeGcmd({"MOVE": "1"})
    mod.cmd_resume_check(g)
    assert any("RESUME frei" in m for m in g.infos), g.infos
    mod.cmd_resume_fix(FakeGcmd({}))          # Ende RESUME
    assert not mod.get_status()["change_aborted"]
    # 3) Kopf VOLL nach Abbruch am Schneidhebel (nach dem Hebelstoß): der
    #    Faden ist abgeschnitten (head_cut, Live 26.10.2026) -> gesperrt,
    #    keine Fahrt; nach Prüfung von Hand HEAD_LOADED=1 -> frei
    bus, printer, mod = _hull_t1b_ok()
    printer.set_printing(True)
    _set_pos(printer, 110, 120, 10)
    printer.fail_on = "G4 P"
    run_chain(mod, "T1B")
    assert mod.head_cut, "Schnitt pessimistisch vor der Fahrt gemerkt"
    g = FakeGcmd({})
    mod.cmd_error_clear(g)
    assert any("abgeschnitten" in m for m in g.infos), g.infos
    n = len(printer.gcode.scripts)
    _check_raises(mod.cmd_resume_check, {"MOVE": "1"}, [
        "abgeschnittene Faden", "CUT fehlgeschlagen", "HEAD_LOADED=1"])
    assert printer.gcode.scripts[n:] == []
    g = FakeGcmd({"HEAD_LOADED": "1"})
    mod.cmd_error_clear(g)
    assert not mod.head_cut and mod.aborted is None, g.infos
    g = FakeGcmd({"MOVE": "1"})
    mod.cmd_resume_check(g)
    assert any("kein abgebrochener Wechsel" in m for m in g.infos), g.infos
    assert printer.gcode.scripts[n:] == []
    return ("Kopf leer -> RESUME gesperrt (keine Bewegung/kein Frame, Meldung "
            "T0 lädt T1A), HEAD_LOADED gegen Sensor abgelehnt, nach T0 in der "
            "Pause frei; Kopf voll nach Schnitt -> gesperrt, HEAD_LOADED=1 "
            "gibt frei")


def test_resume_kopf_sensor_fehlt():
    # [filament_switch_sensor] fehlt -> Zustand unbekannt -> gesperrt (auch
    # mit resume_redo_change), Freigabe nur per HEAD_LOADED=1
    bus, printer, mod = _hull_abort_leer(resume_redo_change="True")
    mod.cmd_error_clear(FakeGcmd({}))
    del printer.objects["filament_switch_sensor filament_sensor"]
    assert mod.get_status()["head_filament"] is None
    n = len(printer.gcode.scripts)
    _check_raises(mod.cmd_resume_check, {"MOVE": "1"}, [
        "RESUME gesperrt", "liefert keinen Zustand",
        "[filament_switch_sensor filament_sensor] fehlt", "UNBEKANNT",
        "KTWO_BOX_ERROR_CLEAR HEAD_LOADED=1", "ohne Kopfsensor nie"])
    assert printer.gcode.scripts[n:] == []
    g = FakeGcmd({"HEAD_LOADED": "1"})
    mod.cmd_error_clear(g)
    assert any("Kopf als geladen bestätigt" in m and "unbekannt" in m
               for m in g.infos), g.infos
    g = FakeGcmd({"MOVE": "1"})
    mod.cmd_resume_check(g)
    assert any("kein abgebrochener Wechsel" in m for m in g.infos)
    # Option filament_sensor leer (nicht konfiguriert): ebenso gesperrt
    bus, printer, mod = _hull_abort_leer(filament_sensor="")
    mod.cmd_error_clear(FakeGcmd({}))
    _check_raises(mod.cmd_resume_check, {"MOVE": "1"},
                  ["(nicht konfiguriert, [k2_box] filament_sensor leer)",
                   "liefert keinen Zustand"])
    return "Sensor fehlt/leer konfiguriert -> gesperrt, HEAD_LOADED=1 gibt frei"


# =====================================================================
# PAUSE-Temperatur nach abgebrochenem Wechsel (17.10.2026 spät,
# "PAUSE merkt nach Kettenabbruch hotend_temp = Wechsel-Temperatur",
# README-k2_box.md §11)
# =====================================================================

def _pause_lines(sc):
    return [l for l in sc if l.split()[0] == "PAUSE"]


def test_pause_temp_nach_abbruch():
    # 1) Druck mit 200 °C, Wechsel heizt (RETRUDE/EXTRUDE: M109 S220) und
    #    bricht im Einzug ab -> PAUSE bekommt CFS_TEMP=200 (Ziel VOR dem
    #    Aufheizen), nicht 220
    bus, printer, mod = _hull_abort_leer(target=200.0)
    sc = printer.gcode.scripts
    assert "M109 S220" in sc and printer.target == 220.0, sc
    assert _pause_lines(sc) == ["PAUSE CFS_RETURN_X=110 CFS_RETURN_Y=120 "
                                "CFS_RETURN_Z=10 CFS_TEMP=200"], _pause_lines(sc)
    assert any("Drucktemperatur vor dem Wechsel 200 °C (nicht die Wechsel-"
               "Temperatur 220 °C)" in m for m in printer.gcode.raw), \
        printer.gcode.raw
    # 2) mehrere Abbrüche: Wiederholung IN der Pause (PAUSE-Makro hat auf
    #    140 °C gesenkt) heizt erneut und bricht wieder ab -> keine zweite
    #    PAUSE, kein CFS_TEMP (hotend_temp stammt aus der ersten Pause)
    printer.target = 140.0                   # = macros.cfg PAUSE M104 S140
    mod.cmd_error_clear(FakeGcmd({}))
    n = len(sc)
    run_chain(mod, "T1A")
    assert "EXTRUDE fehlgeschlagen" in mod.get_status()["chain_error"]
    assert "M109 S220" in sc[n:] and _pause_lines(sc[n:]) == [], sc[n:]
    assert mod.chain["orig_target"] is None    # Kette aufgeräumt
    # ... und die Düse steht nicht die ganze Pause auf Wechsel-Temperatur:
    # zurück auf das Ziel vor diesem Wechsel (Pausentemperatur 140)
    i = sc.index("M109 S220", n)
    assert sc[i:].count("M104 S140") == 1 and printer.target == 140.0, sc[i:]
    assert any("Düse zurück auf 140 °C" in m for m in printer.gcode.raw)
    # 3) manueller T<n> in der Pause (Erfolg): END_OPT stellt das Ziel VOR
    #    diesem Wechsel her = Pausentemperatur 140, NICHT die Drucktemperatur
    #    (die heizt erst RESUME aus hotend_temp)
    printer.target = 140.0
    mod.cmd_error_clear(FakeGcmd({}))
    bus.fail_after_mm = None
    bus.head_at_mm = bus.odo + 100.0
    n = len(sc)
    run_chain(mod, "T1A")
    assert mod.get_status()["chain_error"] == "" and printer.paused
    assert "M109 S220" in sc[n:] and sc[n:].count("M104 S140") == 1 \
        and printer.target == 140.0 and _pause_lines(sc[n:]) == [], sc[n:]
    # 4) Abbruch VOR dem Aufheizen (Schnitt am Hebel): Ziel unverändert ->
    #    PAUSE ohne CFS_TEMP (merkt ohnehin das Druckziel)
    bus, printer, mod = _hull_t1b_ok()
    printer.set_printing(True)
    printer.target = 200.0
    _set_pos(printer, 110, 120, 10)
    printer.fail_on = "G4 P"
    run_chain(mod, "T1B")
    sc = printer.gcode.scripts
    assert "M109 S220" not in sc and _pause_lines(sc) == [
        "PAUSE CFS_RETURN_X=110 CFS_RETURN_Y=120 CFS_RETURN_Z=10"], sc
    # 5) Druckziel schon >= Wechsel-Temperatur (235): Kette heizt auf 235,
    #    Ziel unverändert -> kein CFS_TEMP
    bus, printer, mod = _hull_abort_leer(target=235.0)
    sc = printer.gcode.scripts
    assert "M109 S235" in sc and _pause_lines(sc) == [
        "PAUSE CFS_RETURN_X=110 CFS_RETURN_Y=120 CFS_RETURN_Z=10"], sc
    # 6) Ziel vor dem Wechsel 0 (unbekannt/aus): kein CFS_TEMP -> PAUSE
    #    merkt wie bisher das aktuelle Ziel 220 (nicht kalt weiterdrucken),
    #    Meldung erklärt es
    bus, printer, mod = _hull_abort_leer(target=0.0)
    sc = printer.gcode.scripts
    assert _pause_lines(sc) == ["PAUSE CFS_RETURN_X=110 CFS_RETURN_Y=120 "
                                "CFS_RETURN_Z=10"], sc
    assert any("vor dem Wechsel unbekannt (0)" in m and "220 °C" in m
               for m in printer.gcode.raw), printer.gcode.raw
    # 7) Einzelschritt ohne Kette im Druck (KTWO_BOX_EXTRUDE von Hand):
    #    Ziel vor dem Schritt -> CFS_TEMP; danach kein Merker-Rest
    bus, printer, mod = build_hull(allow_write="True")
    bus.fail_after_mm = 100.0
    printer.set_printing(True)
    printer.target = 205.0
    _set_pos(printer, 80, 90, 7)
    mod.cmd_extrude(FakeGcmd({"TNN": "T1A"}))
    sc = printer.gcode.scripts
    pl = _pause_lines(sc)
    assert len(pl) == 1 and pl[0].startswith("PAUSE CFS_RETURN_X=80 ") \
        and pl[0].endswith(" CFS_TEMP=205"), sc
    assert mod._step_target is None and mod._step_start is None
    # 8) ohne Druck: Fehler -> G-Code-Fehler, keine PAUSE (unverändert)
    bus, printer, mod = build_hull(allow_write="True")
    bus.fail_after_mm = 100.0
    printer.target = 200.0
    try:
        mod.cmd_extrude(FakeGcmd({"TNN": "T1A"}))
    except ValueError:
        pass
    assert _pause_lines(printer.gcode.scripts) == []
    return ("Abbruch nach M109 S220 bei 200 °C -> PAUSE CFS_TEMP=200; "
            "Wiederholung in der Pause ohne PAUSE/CFS_TEMP, Erfolg -> M104 S140; "
            "vor dem Heizen / Ziel 235 / Ziel 0 -> kein CFS_TEMP; Einzelschritt")


def test_makros_pause_temp():
    try:
        env, macros, kcfg, param = _render_env()
    except ImportError:
        return "SKIP (jinja2 fehlt)"
    R = lambda name, pr, params=None: _render(env, macros, name, pr, params)  # noqa: E731
    pos = {"gcode_position": {"x": 133.0, "y": 378.0, "z": 10.4, "e": 0.0}}
    tpos = {"position": {"x": 133.0, "y": 378.0, "z": 10.4}}
    key = "SET_GCODE_VARIABLE MACRO=_PI_PARAM VARIABLE=hotend_temp VALUE="
    cfs = {"CFS_RETURN_X": "110", "CFS_RETURN_Y": "120", "CFS_RETURN_Z": "10"}

    def pause(params, target=220.0):
        pr = _rprinter(param, kcfg, gcode_move=pos, toolhead=tpos,
                       pause_resume={"is_paused": False},
                       extruder={"target": target})
        lines, msgs = R("PAUSE", pr, params)
        return [l for l in lines if l.startswith(key)], lines, msgs
    # mit CFS_TEMP: merkt 200 statt Wechsel-Ziel 220, Meldung
    ht, lines, msgs = pause(dict(cfs, CFS_TEMP="200"))
    assert ht == [key + "200.0"], ht
    assert any("Drucktemperatur vor dem Wechsel 200.0 °C" in m
               and "220.0 °C" in m for m in msgs), msgs
    assert "M104 S140" in lines, lines         # Pausentemperatur wie bisher
    # ohne CFS_TEMP (normale Pause / Abbruch vor dem Heizen): aktuelles Ziel
    ht, _l, msgs = pause(cfs)
    assert ht == [key + "220.0"] and not [m for m in msgs if "Drucktemp" in m]
    ht, _l, _m = pause({}, target=205.0)
    assert ht == [key + "205.0"], ht
    # CFS_TEMP 0 / ungültig -> aktuelles Ziel (nie hotend_temp 0 = kalt)
    for bad in ("0", "-5", "abc"):
        ht, _l, msgs = pause(dict(cfs, CFS_TEMP=bad))
        assert ht == [key + "220.0"], (bad, ht)
    # schon pausiert: PAUSE tut nichts (hotend_temp der ersten Pause bleibt)
    pr = _rprinter(param, kcfg, pause_resume={"is_paused": True})
    lines, _m = R("PAUSE", pr, dict(cfs, CFS_TEMP="200"))
    assert lines == [], lines
    # RESUME-Vorbereitung heizt auf hotend_temp (= 200 nach CFS_TEMP)
    pr = _rprinter(param, kcfg)
    pr["gcode_macro _PI_PARAM"]["hotend_temp"] = 200.0
    lines, _m = R("_PI_RESUME_PREPARE", pr)
    assert "M109 S200" in lines and "M109 S220" not in lines, lines
    return ("PAUSE CFS_TEMP=200 -> hotend_temp 200 (sonst/0/ungültig "
            "aktuelles Ziel), in der Pause nichts, PREPARE M109 S200")


def test_resume_abbruch_merker():
    bus, printer, mod = _hull_abort_leer()
    # PAUSE aus _fail ruft RESUME_FIX CLEAR=1 -> Merker bleibt
    assert mod.aborted == {"step": "EXTRUDE", "tnn": "T1A", "source": "T"}, \
        mod.aborted
    # PRE_OPT bei offenem Fehler (nichts passiert) überschreibt ihn nicht
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1C", "SOURCE": "T"}))
    mod.cmd_end_opt(FakeGcmd({}))
    assert mod.aborted["tnn"] == "T1A", mod.aborted
    # Einzelschritt ohne TNN (CUT von Hand in der Pause) behält Ziel/Herkunft
    mod.cmd_error_clear(FakeGcmd({}))
    printer.fail_on = "G4 P"
    mod.cmd_cut(FakeGcmd({}))
    assert mod.aborted == {"step": "CUT", "tnn": "T1A", "source": "T"}, \
        mod.aborted
    assert "PAUSE" not in [l.split()[0] for l in printer.gcode.scripts[-3:]]
    # neue Pause, die NICHT aus _fail stammt (Hand-PAUSE, Runout, neuer
    # Druck nach CANCEL) -> veralteter Merker weg
    mod.cmd_resume_fix(FakeGcmd({"CLEAR": "1"}))
    assert mod.aborted is None and not mod.get_status()["change_aborted"]
    # M8200-Kette (ohne SOURCE): Herkunft leer, Ziel aus EXTRUDE TNN
    bus, printer, mod = _hull_abort_leer(source="")
    assert mod.aborted == {"step": "EXTRUDE", "tnn": "T1A", "source": ""}
    # Ende eines RESUME (RESUME_FIX ohne CLEAR) -> Merker weg
    mod.cmd_resume_fix(FakeGcmd({}))
    assert mod.aborted is None
    # Fehler ohne Druck: kein Merker
    bus, printer, mod = build_hull(allow_write="True")
    bus.fail_after_mm = 50.0
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1A", "SOURCE": "T"}))
    try:
        mod.cmd_extrude(FakeGcmd({"TNN": "T1A"}))
    except ValueError:
        pass
    assert mod.aborted is None
    return ("Merker überlebt PAUSE aus _fail, nicht die abgelehnte PRE_OPT; "
            "Einzelschritt behält Ziel; neue Pause/RESUME-Ende löschen; "
            "M8200 ohne Herkunft; ohne Druck kein Merker")


def test_resume_nachholen():
    # resume_redo_change True + beide Freigaben + MOVE=1: Stock-analog
    bus, printer, mod = _hull_abort_leer(resume_redo_change="True")
    mod.cmd_error_clear(FakeGcmd({}))        # Box 0x04 [00 01] -> IDLE
    assert bus.hub_mask == 0x01, bus.hub_mask  # A steckt im Hub (Abbruch)
    bus.fail_after_mm = None
    bus.head_at_mm = bus.odo + 100.0
    pos0 = list(printer.pos[:3])
    bus.trace = []
    n = len(printer.gcode.scripts)
    g = FakeGcmd({"MOVE": "1"})
    mod.cmd_resume_check(g)
    t = bus.trace
    sc = printer.gcode.scripts[n:]
    assert sc[0] == "KTWO_BOX_PRE_OPT TNN=T1A Z_HOP=0 SOURCE=T", sc[:3]
    i0 = _idx(t, _g("M109 S235"))                          # Schacht + Heizen
    i1 = _idx(t, _b(0x11, (0x01, 0x00)), i0)               # Rückzug A (Hub)
    _idx(t, _g("G1 E-10 F600"), i1)                        # mit heißer Düse
    i2 = _idx(t, _b(0x11, (0x01, 0x01)), i1)               # p1
    i3 = _idx(t, _b(0x10, (0x01, 0x00, 0x00)), i2)         # Einzug A
    i4 = _idx(t, _g("G1 E30 F360"), i3)                    # Stage 7
    i5 = _idx(t, _b(0x10, (0x01, 0x07, 0x03)), i4)
    i6 = _idx(t, _b(0x04, (0x01, 0x00)), i5)               # PRINT
    i7 = _idx(t, _g("G1 E18 F360"), i6)                    # Spülen
    i8 = _idx(t, _g("G1 E56 F360"), i7)
    _idx(t, _g("G0 Y345 F12000"), i8)                      # Schacht raus
    assert "G0 X-7.8 Y200 F30000" not in sc, "kein Schnitt (Kopf leer)"
    assert not [l for l in sc if l.startswith(("G1 Z", "G0 Z", "PAUSE",
                                               "RESUME"))], sc
    assert not [l for l in sc if l.startswith("G0 X110 Y120")], \
        "keine Rückfahrt (END_OPT RETURN=0, die macht RESUME)"
    assert i0 < i1 and printer.pos[:2] == [160.0, 345.0] \
        and printer.pos[2] == pos0[2], printer.pos
    st = mod.get_status()
    assert st["chain_error"] == "" and st["head_filament"] is True \
        and st["loaded_tnn"] == "T1A" and not st["chain_active"] \
        and printer.paused and printer.sensor.enabled, st
    assert any("NACHGEHOLT" in m for m in g.infos) \
        and any("nachgeholt (Kopfsensor meldet Filament)" in m
                for m in g.infos), g.infos
    mod.cmd_resume_fix(FakeGcmd({}))
    assert not mod.get_status()["change_aborted"]
    # Nachholen scheitert (Slot A wieder 0x0c): gesperrt, pausiert, keine
    # Rückfahrt, Merker bleibt (nächstes RESUME holt erneut nach)
    bus, printer, mod = _hull_abort_leer(resume_redo_change="True")
    mod.cmd_error_clear(FakeGcmd({}))
    n = len(printer.gcode.scripts)
    _check_raises(mod.cmd_resume_check, {"MOVE": "1"}, [
        "Nachholen des Wechsels T1A fehlgeschlagen", "EXTRUDE fehlgeschlagen",
        "keine Rückfahrt", "KTWO_BOX_ERROR_CLEAR"])
    sc = printer.gcode.scripts[n:]
    assert printer.paused and not [l for l in sc if l.split()[0] in (
        "PAUSE", "RESUME") or l.startswith("G0 X110 Y120")], sc
    st = mod.get_status()
    assert "EXTRUDE fehlgeschlagen" in st["chain_error"] \
        and mod.aborted["source"] == "T" and mod.aborted["tnn"] == "T1A", st
    # Düse nach dem gescheiterten Nachholen zurück auf das Ziel davor (hier
    # 220 aus der Fake-Pause; real die Pausentemperatur 140)
    i = sc.index("M109 S235")
    assert sc[i:].count("M104 S220") == 1 and printer.target == 220.0, sc[i:]
    return ("Nachholen: PRE_OPT Z_HOP=0 -> Schacht/M109 235 -> Rückzug A "
            "(E-10 heiß) -> Einzug A -> E30/mode7/PRINT -> Spülen -> raus, "
            "kein Schnitt/Z/Rückfahrt; Fehler -> gesperrt + pausiert")


def test_resume_nachholen_sperren():
    def blocked(over, prep, params, text, source="T"):
        bus, printer, mod = _hull_abort_leer(source=source, **over)
        mod.cmd_error_clear(FakeGcmd({}))
        prep(bus, printer, mod)
        bus.calls[:] = []
        n = len(printer.gcode.scripts)
        _check_raises(mod.cmd_resume_check, params,
                      ["RESUME gesperrt", "KEIN Filament", text])
        assert printer.gcode.scripts[n:] == [] and _mech(bus) == [], \
            (text, printer.gcode.scripts[n:])
    nop = lambda b, p, m: None  # noqa: E731
    on = {"resume_redo_change": "True"}
    blocked(on, nop, {"MOVE": "0"}, "RESUME darf nicht fahren")
    blocked(on, nop, {}, "RESUME darf nicht fahren")
    blocked(on, lambda b, p, m: m.cfg.update(allow_write=False),
            {"MOVE": "1"}, "nicht freigegeben")
    blocked(on, nop, {"MOVE": "1"}, "nicht aus einem T<n>-Wechsel",
            source="")
    blocked(on, lambda b, p, m: m.aborted.update(tnn=""), {"MOVE": "1"},
            "Ziel-TNN des abgebrochenen Wechsels unbekannt")
    blocked(on, lambda b, p, m: m.cmd_pre_opt(FakeGcmd({"TNN": "T1C"})),
            {"MOVE": "1"}, "Wechselkette noch offen")
    blocked({}, nop, {"MOVE": "1"}, "resume_redo_change: False")
    # nicht pausiert: nichts zu prüfen (RESUME-Makro ruft dann gar nicht)
    bus, printer, mod = _hull_abort_leer()
    mod.cmd_error_clear(FakeGcmd({}))
    printer.paused, printer.print_state = False, "printing"
    printer._refresh()
    g = FakeGcmd({"MOVE": "1"})
    mod.cmd_resume_check(g)
    assert any("nicht pausiert" in m for m in g.infos), g.infos
    return ("Nachholen nur mit Option + allow_write/allow_move + MOVE=1 + "
            "T<n>-Herkunft + bekanntem Ziel + geschlossener Kette – sonst "
            "gesperrt ohne Bewegung/Frame")


def test_makros_resume_kopfpruefung():
    try:
        env, macros, kcfg, param = _render_env()
    except ImportError:
        return "SKIP (jinja2 fehlt)"
    R = lambda pr: _render(env, macros, "RESUME", pr)  # noqa: E731
    pr = _rprinter(param, kcfg, k2_box={"change_aborted": True})
    lines, _m = R(pr)
    i = lines.index("_PI_RESUME_PREPARE")
    assert lines[:i] == ["KTWO_BOX_RESUME_CHECK MOVE=1"], lines
    for upd in ({"gcode_macro _PI_FREIGABE": {"allow_move": False}},
                {"toolhead": {"homed_axes": "xy"}},
                {"gcode_macro _PI_PARAM": {"pause_pos_valid": False}}):
        pr = _rprinter(param, kcfg, k2_box={"change_aborted": True}, **upd)
        lines, _m = R(pr)
        assert lines[0] == "KTWO_BOX_RESUME_CHECK MOVE=0", (upd, lines)
    # ohne Abbruch bzw. nicht pausiert: kein Prüfbefehl (Ablauf unverändert)
    for upd in ({"k2_box": {"change_aborted": False}},
                {"k2_box": {"change_aborted": True},
                 "pause_resume": {"is_paused": False}}):
        lines, _m = R(_rprinter(param, kcfg, **upd))
        assert not [l for l in lines if "RESUME_CHECK" in l], (upd, lines)
    # offener Fehler: Reihenfolge der Anleitung (erst quittieren)
    pr = _rprinter(param, kcfg, k2_box={"chain_error": "EXTRUDE x"})
    try:
        R(pr)
    except _RenderRaise as exc:
        assert "KTWO_BOX_ERROR_CLEAR, Wechsel (T<n>)" in str(exc), exc
    else:
        raise AssertionError("RESUME hätte gesperrt sein müssen")
    kb = dict(kcfg)
    assert kb.get("resume_redo_change") == "False", kb
    return ("RESUME: KTWO_BOX_RESUME_CHECK MOVE=1/0 vor _PI_RESUME_PREPARE nur "
            "nach Abbruch, Auslieferung resume_redo_change False")


def test_reactor_bus_proxy():
    bus = FakeBoxBus()
    seen = []
    real = bus.transact_full

    def _tf(*a, **k):
        seen.append(threading.current_thread() is threading.main_thread())
        return real(*a, **k)

    bus.transact_full = _tf
    r = FakeReactor()
    proxy = K.ReactorBusProxy(bus, r)
    out = proxy.transact_full(0x01, 0x0A, [], timeout=0.5, retries=0)
    assert out[2] == 0x0A and seen == [False] and r.pauses, (out, seen)

    class Boom:
        def transact_full(self, *a, **k):
            raise RuntimeError("Port weg")
    try:
        K.ReactorBusProxy(Boom(), r).transact_full(1, 0x0A, [])
    except RuntimeError as exc:
        assert "Port weg" in str(exc)
    else:
        raise AssertionError("Fehler im Hilfs-Thread muss durchgereicht werden")
    # Hülle nutzt den Proxy, wenn der Reactor pause+monotonic hat
    b2, p2, m2 = build_hull()
    ctl = m2._ensure_controller()
    assert isinstance(ctl.bus, K.ReactorBusProxy), type(ctl.bus)
    return "Bus-Aufruf im Hilfs-Thread, Reactor pausiert, Fehler durchgereicht"


def test_python39():
    import ast
    for fn in ("k2_box.py", "test_k2_box.py"):
        with open(os.path.join(_HERE, fn), encoding="utf-8") as fh:
            ast.parse(fh.read(), filename=fn, feature_version=(3, 9))
    return "k2_box.py + test_k2_box.py Python-3.9-Syntax ok"


def _macro_sources(path):
    """[gcode_macro NAME] -> (gcode-Text, variablen) aus einer cfg-Datei."""
    macros, cur, in_gcode, var = {}, None, False, {}
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            if line.startswith("["):
                cur, in_gcode = None, False
                if line.startswith("[gcode_macro "):
                    cur = line[len("[gcode_macro "):-1].strip()
                    macros[cur] = {"gcode": [], "vars": {}}
                continue
            if cur is None:
                continue
            if in_gcode and (line.startswith((" ", "\t")) or not line.strip()):
                macros[cur]["gcode"].append(line.strip())
                continue
            in_gcode = False
            if line.startswith("gcode:"):
                in_gcode = True
            elif line.startswith("variable_"):
                k, v = line[len("variable_"):].split(":", 1)
                macros[cur]["vars"][k.strip()] = v.strip()
    return {k: ("\n".join(v["gcode"]), v["vars"]) for k, v in macros.items()}


def test_makros_cfg():
    try:
        import jinja2
    except Exception:
        return "SKIP (jinja2 fehlt)"
    path = os.path.join(_HERE, "config-pi", "macros.cfg")
    macros = _macro_sources(path)
    for n in ["T%d" % i for i in range(16)] + ["M8200", "_KTWO_TOOL_CHANGE",
                                               "RESUME"]:
        assert n in macros, "Makro %s fehlt" % n
    env = jinja2.Environment("{%", "%}", "{", "}")

    class _Raise(Exception):
        pass

    def render(name, params=None, k2=None, m8200_tnn="", m8200_tool=-1):
        src, _vars = macros[name]
        printer = {"k2_box": dict({"change_enabled": True, "chain_error": ""},
                                  **(k2 or {})),
                   "gcode_macro M8200": {"tnn": m8200_tnn,
                                         "tool": m8200_tool},
                   "gcode_macro _PI_PARAM": {"hotend_temp": 0, "fan0_speed": 0,
                                             "fan2_speed": 0},
                   "gcode_macro _KTWO_FAN_SAVE": {"fan2_value": 0.0},
                   "output_pin fan2": {"value": 0.0},
                   "pause_resume": {"is_paused": False}}
        msgs = []

        def _info(m):
            msgs.append(m)
            return ""

        def _err(m):
            raise _Raise(m)

        out = env.from_string(src).render(
            params=dict(params or {}), rawparams="", printer=printer,
            action_respond_info=_info, action_raise_error=_err)
        lines = [l.strip() for l in out.split("\n")
                 if l.strip() and not l.strip().startswith("#")]
        return lines, msgs

    # T-Befehle: T1..T15 -> Kette, T0 nur mit Freigabe (sonst Einfarbbetrieb)
    for i in range(1, 16):
        assert render("T%d" % i)[0] == ["_KTWO_TOOL_CHANGE T=%d" % i]
    assert render("T0")[0] == ["_KTWO_TOOL_CHANGE T=0"]
    lines, msgs = render("T0", k2={"change_enabled": False})
    assert lines == [] and "Einfarbbetrieb" in msgs[0], (lines, msgs)
    exp = {0: "T1A", 1: "T1B", 3: "T1D", 4: "T2A", 5: "T2B", 15: "T4D"}
    for i, tnn in exp.items():
        lines, _m = render("_KTWO_TOOL_CHANGE", {"T": str(i)})
        assert lines == ["_KTWO_FAN_SAVE", "KTWO_BOX_PRE_OPT TNN=%s SOURCE=T" % tnn,
                         "KTWO_BOX_CUT", "KTWO_BOX_RETRUDE",
                         "KTWO_BOX_EXTRUDE TNN=%s" % tnn, "KTWO_BOX_WASTE",
                         "KTWO_BOX_FLUSH TNN=%s TOOL=%d" % (tnn, i),
                         "KTWO_BOX_END_OPT RETURN=1", "_KTWO_FAN_RESTORE"], lines
    # T<n> in der offenen M8200-Kette nach "M8200 L I<n>" (Creality Print 7):
    # kein zweiter Wechsel, die Kette bleibt offen (schließt "M8200 O")
    open_k2 = {"chain_active": True, "chain_tnn": "T1B", "loaded_tnn": "T1B"}
    lines, msgs = render("_KTWO_TOOL_CHANGE", {"T": "1"}, k2=open_k2)
    assert lines == [] and "schon eingezogen" in msgs[0], (lines, msgs)
    for upd in ({"loaded_tnn": "T1A"}, {"chain_tnn": "T1A"},
                {"chain_active": False}, {"chain_error": "EXTRUDE x"}):
        lines, _m = render("_KTWO_TOOL_CHANGE", {"T": "1"},
                           k2=dict(open_k2, **upd))
        assert lines[1] == "KTWO_BOX_PRE_OPT TNN=T1B SOURCE=T", (upd, lines)
    # M8200-Kette (Stock box.cfg-Semantik)
    cases = [({"P": ""}, ["KTWO_BOX_PRE_OPT Z_HOP=0 SOURCE=M8200"]),
             ({"P": "", "S": "5"},
              ["KTWO_BOX_PRE_OPT Z_HOP=0 SOURCE=M8200 TNN=T2B"]),
             ({"C": "", "S": "0"}, ["KTWO_BOX_CUT"]),
             ({"R": ""}, ["KTWO_BOX_RETRUDE"]),
             # Stock CR_BOX_RETRUDE LENGTH≠0 = break_box_extruder_retrude
             # (kein Box-Rückzug, Vorzeichen wie E; CP7-Dateien)
             ({"R": "", "E": "20"}, ["KTWO_BOX_BREAK_RETRUDE LENGTH=20.0"]),
             ({"R": "", "E": "-30"}, ["KTWO_BOX_BREAK_RETRUDE LENGTH=-30.0"]),
             ({"R": "", "E": "0"}, ["KTWO_BOX_RETRUDE"]),
             ({"L": "", "I": "0"}, ["KTWO_BOX_EXTRUDE TNN=T1A",
                                    "SET_GCODE_VARIABLE MACRO=M8200 VARIABLE=tnn VALUE='\"T1A\"'",
                                    "SET_GCODE_VARIABLE MACRO=M8200 VARIABLE=tool VALUE=0",
                                    "KTWO_BOX_WASTE"]),
             ({"L": "", "I": "=7"}, None),
             ({"W": ""}, ["KTWO_BOX_WASTE"]),
             ({"F": "", "S": "600", "L": "100"}, ["KTWO_BOX_FLUSH",
                                                 "SET_GCODE_VARIABLE MACRO=M8200 VARIABLE=tnn VALUE='\"\"'",
                                                 "SET_GCODE_VARIABLE MACRO=M8200 VARIABLE=tool VALUE=-1"]),
             ({"O": ""}, ["KTWO_BOX_END_OPT RETURN=0"])]
    for params, want in cases:
        lines, msgs = render("M8200", params)
        if want is None:
            assert lines[0] == "KTWO_BOX_EXTRUDE TNN=T2D" and msgs == ["tnn: T2D"], lines
        else:
            assert lines == want, (params, lines)
    lines, _m = render("M8200", {"F": ""}, m8200_tnn="T1C")
    assert lines[0] == "KTWO_BOX_FLUSH TNN=T1C", lines
    # TOOL aus M8200 L I<n> durchreichen (flush_mode matrix)
    lines, _m = render("M8200", {"F": ""}, m8200_tnn="T1C", m8200_tool=2)
    assert lines[0] == "KTWO_BOX_FLUSH TNN=T1C TOOL=2", lines
    # RESUME gesperrt bei offenem Wechsel-Fehler, sonst mit RESUME_FIX
    try:
        render("RESUME", k2={"chain_error": "EXTRUDE fehlgeschlagen"})
    except _Raise as exc:
        assert "RESUME gesperrt" in str(exc)
    else:
        raise AssertionError("RESUME hätte gesperrt sein müssen")
    lines, _m = render("RESUME")
    assert lines[-2:] == ["RESUME_BASE", "KTWO_BOX_RESUME_FIX"], lines
    # Jeder KTWO_BOX_*-Befehl der Makros ist im Modul registriert
    bus = FakeBoxBus()
    printer = FakePrinter(bus, with_motion=True)
    K.PrinterK2Box(FakeConfig(printer, test_options()))
    import re as _re
    used = set()
    for src, _v in macros.values():
        used.update(_re.findall(r"KTWO_BOX_[A-Z_]*[A-Z]", src))
    missing = sorted(u for u in used if u not in printer.gcode.commands)
    assert not missing, missing
    assert "_PI_KEIN_CFS" not in open(path, encoding="utf-8").read()
    return "T0..T15/M8200/RESUME gerendert (Jinja2), TNN == box.cfg, %d KTWO_BOX_* registriert" % len(used)


# =====================================================================
# Auto-Nachladen bei Runout (A7)
# =====================================================================

# Synthetische 40-Zeichen-vender (Feldlayout box.RFID_VENDOR_FIELDS, KEINE
# echten Seriennummern): mat 101001 (Hyper PLA) + Farbe weiß/schwarz.
V_WEISS = "10126000000" + "101001" + "0FFFFFF" + "0330000000000000"
V_SCHWARZ = V_WEISS.replace("0FFFFFF", "0000000")
V_PETG = V_WEISS.replace("101001", "102001")


def test_runout_slotwahl():
    sel = K.select_runout_slot
    rf = {"A": V_WEISS, "B": V_SCHWARZ, "C": V_WEISS, "D": V_WEISS}
    assert K.rfid_material(V_WEISS) == ("101001", "0FFFFFF")
    for bad in ("unknown", "busy", "", None, V_WEISS[:-1],
                "10126000000" + "000000" + "0FFFFFF" + "0330000000000000"):
        assert K.rfid_material(bad) is None, bad
    # gleiches Material + Farbe, Reihenfolge ab dem Slot NACH dem leeren
    assert sel("A", 0x0E, rf)[0] == "C"          # B schwarz übersprungen
    assert sel("C", 0x0B, rf)[0] == "D"          # zyklisch C -> D
    assert sel("D", 0x07, rf)[0] == "A"          # D -> A
    assert sel("A", 0x0A, rf)[0] == "D"          # C leer -> D (weiß)
    assert sel("A", 0x03, rf)[0] is None         # C/D ohne Filament am Einlauf
    assert sel("A", 0x02, rf)[0] is None         # nur schwarz belegt
    assert sel("A", 0x02, rf, require_color=False)[0] == "B"
    t, why = sel("A", 0x0E, {"A": V_WEISS, "B": V_PETG, "C": "unknown",
                             "D": V_PETG}, require_color=False)
    assert t is None and "no tray" in why, why  # anderes Material / kein Tag
    t, why = sel("A", 0x0E, {"A": "unknown", "C": V_WEISS})
    assert t is None and "unbekannt" in why, why
    assert sel("A", 0x0F, rf)[0] != "A"           # nie der leere Slot
    # Gleich-Material-Gruppen (Stock same_material, manuell)
    g = K.parse_same_material("T1A,T1C/T1B,T1D")
    assert g == [["A", "C"], ["B", "D"]], g
    assert sel("A", 0x0F, {}, g)[0] == "C" and sel("D", 0x0F, {}, g)[0] == "B"
    assert sel("A", 0x0B, {}, g)[0] is None       # C leer
    assert sel("A", 0x0F, {}, [["B", "D"]])[0] is None
    assert K.parse_same_material("T1D,T1A,T1B") == [["D", "A", "B"]]
    assert sel("A", 0x0F, {}, [["D", "A", "B"]])[0] == "B"  # Listenfolge
    for bad in ("T1A", "T1A,T2B", "T1A,T1B/T1B,T1C", "", "T1A,T1E"):
        try:
            K.parse_same_material(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("LIST %r hätte abgelehnt werden müssen" % bad)
    # Optionen: Default AUS, Grenzen
    c = K.parse_options(test_options())
    assert c["runout_refill"] is False and c["runout_require_color"] is True \
        and c["runout_resume"] is True and c["runout_tail_length"] == 30.0 \
        and c["runout_tail_velocity"] == 600.0, c
    for bad in ({"runout_tail_length": "-1"}, {"runout_tail_length": "150"},
                {"runout_tail_velocity": "0"}, {"runout_refill": "vielleicht"}):
        try:
            K.parse_options(test_options(**bad))
        except ValueError:
            pass
        else:
            raise AssertionError("Option %r hätte abgelehnt werden müssen" % bad)
    return "RFID mat+Farbe zyklisch ab Nachbar, Einlauf Pflicht, Gruppen-Liste, Optionen Default AUS"


def build_runout(**opt_over):
    """Druck läuft mit T1A, Spule A leer: Hub/Kopf leer, Box PRINT + 0x50,
    Etikett A beim Laden gemerkt, B schwarz, C weiß (Ersatz), D weiß leer."""
    opts = dict(allow_write="True", runout_refill="True")
    opts.update(opt_over)
    bus, printer, mod = build_hull(**opts)
    for letter, tag in zip("ABCD", (V_WEISS, V_SCHWARZ, V_WEISS, V_WEISS)):
        bus.slots[letter]["rfid"] = tag
    ctl = mod._ensure_controller()
    ctl.get_rfid("A")                      # wie nach KTWO_BOX_EXTRUDE T1A
    ctl.loaded_slot = "A"
    bus.slots["A"]["filament"] = False     # Spule leer -> RFID "unknown"
    bus.slots["D"]["filament"] = False
    bus.state, bus.print_frame_status = 2, 0x50
    bus.hub_mask, bus.head = 0x00, False
    bus.head_at_mm = 100.0
    bus.need_extruder_for_mode7 = True
    printer.objects["gcode_macro _PI_PARAM"] = _Obj({"hotend_temp": 235.0})
    printer.set_printing(True)
    printer.gcode.run_script_from_command("PAUSE")  # pause_on_runout: True
    bus.calls[:] = []
    bus.trace = []
    return bus, printer, mod


def _mech(bus):
    return [c for c in bus.calls if c[1] in (0x04, 0x0D, 0x0F, 0x10, 0x11)]


def test_runout_ablauf():
    bus, printer, mod = build_runout()
    n0 = len(printer.gcode.scripts)
    g = FakeGcmd({})
    mod.cmd_runout(g)
    t = bus.trace
    i0 = _idx(t, _b(0x0A, ()))                              # Zustand PRINT/0x50
    i1 = _idx(t, _b(0x08, (0x01,)), i0)                     # Hub leer
    i2 = _idx(t, _b(0x08, (0x00,)), i1)                     # Einlauf
    i3 = _idx(t, _b(0x02, (0x0F,)), i2)                     # RFID alle
    i4 = _idx(t, _g("SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=0"), i3)
    i5 = _idx(t, _g("G0 X133 F12000"), i4)                  # Schacht
    i6 = _idx(t, _g("M109 S235"), i5)                       # Drucktemperatur
    i7 = _idx(t, _g("G1 E30 F600"), i6)                     # Stock E30 F600
    i8 = _idx(t, _b(0x10, (0x04, 0x00, 0x00)), i7)          # Einzug Slot C
    i9 = _idx(t, _b(0x10, (0x04, 0x06, 0x00)), i8)
    i10 = _idx(t, _g("G1 E30 F360"), i9)                    # Stage 7
    i11 = _idx(t, _b(0x10, (0x04, 0x07, 0x03)), i10)
    i12 = _idx(t, _b(0x04, (0x04, 0x00)), i11)              # PRINT Slot C
    i13 = _idx(t, _g("G1 E18 F360"), i12)                   # Spülen 90 + 70
    i14 = _idx(t, _g("G1 E56 F360"), i13)
    i15 = _idx(t, _g("G0 Y345 F12000"), i14)                # Schacht raus
    _idx(t, _g("SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=1"), i15)
    sc = printer.gcode.scripts[n0:]
    assert sc[-1] == "RESUME", sc[-5:]
    assert "KTWO_BOX_PRE_OPT TNN=T1C Z_HOP=0" in sc, sc[:3]
    assert not [l for l in sc if l.startswith("G1 Z")], "kein Z-Hop (PAUSE hob)"
    assert "G0 X-7.8 Y200 F30000" not in sc, "kein Schnitt (Kopf leer)"
    assert not [c for c in bus.calls if c[1] == 0x11], "kein Rückzug (Hub leer)"
    st = mod.get_status()
    assert st["loaded_tnn"] == "T1C" and st["chain_error"] == "" \
        and not st["chain_active"] and st["auto_refill"] == 1, st
    assert "T1A leer -> T1C nachgeladen" in st["runout_last"], st
    assert not printer.paused and printer.print_state == "printing"
    assert not [m for m in printer.gcode.raw if m.startswith("!!")], printer.gcode.raw
    # Rückfahrt zum Parkpunkt der PAUSE vor dem RESUME (start_pos)
    _idx(t, _g("G0 X200 Y200 F12000"), i15)
    # Box im ERROR 0x51 (Folge des Runouts) -> erst 0x04 [00 01], dann Kette
    bus, printer, mod = build_runout()
    bus.state, bus.stored_error = 4, 0x51
    mod.cmd_runout(FakeGcmd({}))
    k = [i for i, c in enumerate(bus.calls) if c[1] == 0x04]
    assert tuple(bus.calls[k[0]][2]) == (0x00, 0x01), bus.calls[k[0]]
    assert mod.get_status()["loaded_tnn"] == "T1C" and not printer.paused
    # runout_resume False: nachgeladen, Druck bleibt pausiert
    bus, printer, mod = build_runout(runout_resume="False")
    mod.cmd_runout(FakeGcmd({}))
    assert printer.paused and "RESUME" not in printer.gcode.scripts
    assert mod.get_status()["loaded_tnn"] == "T1C"
    return "T1A leer: Analyse 0a/08/08/02 -> Schacht, M109 235, E30 F600 -> Einzug C, E30 F360, mode7, PRINT -> Spülen -> Park -> RESUME; ERROR 0x51 quittiert"


def _assert_runout_pause(bus, printer, mod, text, n_scripts=None, bus_ok=True):
    st = mod.get_status()
    assert text in st["runout_last"], (text, st["runout_last"])
    assert printer.paused, "Druck muss pausiert bleiben"
    sc = printer.gcode.scripts[n_scripts or 0:]
    assert not [l for l in sc if l.split()[0] in ("G0", "G1", "M109", "RESUME")
                or l.startswith("KTWO_")], sc
    assert _mech(bus) == [], _mech(bus)
    if not bus_ok:
        assert bus.calls == [], bus.calls
    assert any(m.startswith("!!") and "PAUSIERT" in m for m in printer.gcode.raw)
    assert st["chain_error"] == "", "Vorprüfung sperrt RESUME nicht"


def test_runout_pausenpfade():
    # 1) Auslieferung: Auto-Nachladen aus -> nur Pause, kein Bus
    bus, printer, mod = build_runout(runout_refill="False")
    n = len(printer.gcode.scripts)
    mod.cmd_runout(FakeGcmd({}))
    _assert_runout_pause(bus, printer, mod, "Auto-Nachladen aus", n, bus_ok=False)
    # 2) Freigabe fehlt -> Pause, kein Bus
    bus, printer, mod = build_runout(allow_write="False")
    n = len(printer.gcode.scripts)
    mod.cmd_runout(FakeGcmd({}))
    _assert_runout_pause(bus, printer, mod, "nicht freigegeben", n, bus_ok=False)
    # 3) pause_on_runout False (Druck läuft noch): k2_box pausiert selbst
    bus, printer, mod = build_runout(allow_move="False")
    printer.paused, printer.print_state = False, "printing"
    printer._refresh()
    n = len(printer.gcode.scripts)
    mod.cmd_runout(FakeGcmd({}))
    assert printer.gcode.scripts[n:] == ["PAUSE"], printer.gcode.scripts[n:]
    _assert_runout_pause(bus, printer, mod, "nicht freigegeben", n + 1)
    # 4) Lesende Fälle: je eine Störung -> Pause mit Klartext
    cases = (
        ("Kopf", lambda b, m: setattr(b, "head", True), "Fehlauslösung"),
        ("IDLE", lambda b, m: setattr(b, "state", 0), "CFS druckt nicht"),
        ("Fehler", lambda b, m: (setattr(b, "state", 4),
                                 setattr(b, "stored_error", 0x0C)),
         "ERROR (0x0c"),
        ("Slot", lambda b, m: setattr(m.controller, "loaded_slot", None),
         "Slot unbekannt"),
        ("Hub", lambda b, m: setattr(b, "hub_mask", 0x01), "Hub meldet noch"),
        ("Einlauf", lambda b, m: b.slots["A"].update(filament=True),
         "Einlauf von Slot A"),
        ("kein Ersatz", lambda b, m: b.slots["C"].update(rfid=V_SCHWARZ),
         "no tray"),
        ("kein Etikett", lambda b, m: m.controller.tag_cache.clear(),
         "unbekannt (kein lesbares RFID"),
    )
    for name, setup, text in cases:
        bus, printer, mod = build_runout()
        setup(bus, mod)
        bus.calls[:] = []
        n = len(printer.gcode.scripts)
        mod.cmd_runout(FakeGcmd({}))
        try:
            _assert_runout_pause(bus, printer, mod, text, n)
        except AssertionError as exc:
            raise AssertionError("%s: %s" % (name, exc))
    # 5) Farbe egal -> B (schwarz) wird genommen (DRY_RUN: nichts bewegt)
    bus, printer, mod = build_runout(runout_require_color="False")
    bus.slots["C"]["rfid"] = V_SCHWARZ
    n = len(printer.gcode.scripts)
    g = FakeGcmd({"DRY_RUN": "1"})
    mod.cmd_runout(g)
    assert any("Ersatz T1B" in m and "DRY_RUN" in m for m in g.infos), g.infos
    assert printer.gcode.scripts[n:] == [] and _mech(bus) == []
    # 6) Kein RS485-Bus -> klare Meldung, Pause
    bus, printer, mod = build_runout()
    mod.controller, mod._bus = None, None
    del printer.objects["k2_485"]
    n = len(printer.gcode.scripts)
    mod.cmd_runout(FakeGcmd({}))
    _assert_runout_pause(bus, printer, mod, "k2_485", n, bus_ok=False)
    # 7) Fehler IN der Kette (Ersatz A läuft auf 0x0c) -> Pause, RESUME gesperrt
    bus, printer, mod = build_runout()
    ctl = mod.controller
    bus.slots["A"]["filament"] = True
    ctl.get_rfid("B")
    bus.slots["B"]["rfid"] = V_WEISS
    ctl.loaded_slot = "B"
    ctl.tag_cache["B"] = V_WEISS
    bus.slots["B"]["filament"] = False
    bus.slots["C"]["filament"] = False
    bus.fail_after_mm = 50.0
    mod.cmd_runout(FakeGcmd({}))
    st = mod.get_status()
    assert "0x0c" in st["chain_error"] and printer.paused, st
    assert "fehlgeschlagen" in st["runout_last"] and "T1A" in st["runout_last"]
    assert "RESUME" not in printer.gcode.scripts and printer.sensor.enabled
    # 8) Ohne Druck (Handaufruf) -> G-Code-Fehler statt Pause
    bus, printer, mod = build_runout(runout_refill="False")
    printer.paused, printer.print_state = False, "standby"
    printer._refresh()
    try:
        mod.cmd_runout(FakeGcmd({}))
    except ValueError as exc:
        assert "Auto-Nachladen aus" in str(exc), exc
    else:
        raise AssertionError("ohne Druck hätte ein G-Code-Fehler kommen müssen")
    return "aus/ohne Freigabe/Kopf/IDLE/0x0c/Slot/Hub/Einlauf/kein Ersatz/kein Etikett/kein Bus -> Pause ohne Bewegung; Kettenfehler -> Pause+RESUME gesperrt; ohne Druck -> Fehler"


def test_runout_befehle():
    bus, printer, mod = build_runout(runout_refill="False")
    for name in ("KTWO_BOX_RUNOUT", "KTWO_BOX_AUTO_REFILL",
                 "KTWO_BOX_SAME_MATERIAL"):
        assert name in printer.gcode.commands and upstream_gcode_name_ok(name)
    printer.gcode.run_script_from_command("KTWO_BOX_AUTO_REFILL ENABLE=1")
    assert mod.get_status()["auto_refill"] == 1
    printer.gcode.run_script_from_command("KTWO_BOX_SAME_MATERIAL LIST=T1A,T1D")
    assert mod.get_status()["same_material"] == [["T1A", "T1D"]]
    try:
        printer.gcode.run_script_from_command("KTWO_BOX_SAME_MATERIAL LIST=T1A,T2A")
    except ValueError as exc:
        assert "Box 2" in str(exc), exc
    else:
        raise AssertionError("T2A hätte abgelehnt werden müssen")
    # Gruppe A+D: D ohne Filament -> kein Ersatz, obwohl C per RFID passt
    g = FakeGcmd({"DRY_RUN": "1"})
    mod.cmd_runout(g)
    assert any("Gruppe A+D" in m for m in g.infos), g.infos
    bus.slots["D"]["filament"] = True
    bus.slots["D"]["rfid"] = "unknown"   # Gruppe gilt auch ohne Etikett
    g = FakeGcmd({"DRY_RUN": "1"})
    mod.cmd_runout(g)
    assert any("Ersatz T1D" in m for m in g.infos), g.infos
    printer.gcode.run_script_from_command("KTWO_BOX_SAME_MATERIAL CLEAR=1")
    printer.gcode.run_script_from_command("KTWO_BOX_AUTO_REFILL ENABLE=0")
    st = mod.get_status()
    assert st["same_material"] == [] and st["auto_refill"] == 0, st
    # print_slot aus dem 0x0a-Poll im PRINT (Hub-Maske) als Fallback
    bus2, printer2, mod2 = build_hull()
    ctl = mod2._ensure_controller()
    bus2.state, bus2.hub_mask = 2, 0x02
    ctl.poll_status()
    assert ctl.print_slot == "B", ctl.print_slot
    return "AUTO_REFILL/SAME_MATERIAL setzen Status, Gruppen ersetzen RFID-Regel, print_slot aus 0x0a"


def test_runout_config():
    """printer.cfg/k2-module.cfg (Auslieferung): runout_gcode = KTWO_BOX_RUNOUT,
    pause_on_runout True (Upstream pausiert VOR runout_gcode), Auto-Nachladen
    AUS, kein cut_pos_x in [k2_box] (SAVE_CONFIG-Konflikt, A6)."""
    import configparser
    base = os.path.join(_HERE, "config-pi")

    def section(fn, name):
        cp = configparser.RawConfigParser(strict=False,
                                          inline_comment_prefixes=("#",))
        txt = open(os.path.join(base, fn), encoding="utf-8").read()
        txt = txt.split("#*# <---------------------- SAVE_CONFIG")[0]
        cp.read_string(txt)
        return dict(cp.items(name))

    fs = section("printer.cfg", "filament_switch_sensor filament_sensor")
    assert fs["pause_on_runout"].strip() == "True", fs
    assert fs["runout_gcode"].strip() == "KTWO_BOX_RUNOUT", fs
    kb = section("k2-module.cfg", "k2_box")
    assert "cut_pos_x" not in kb, "cut_pos_x in [k2_box] -> SAVE_CONFIG-Konflikt"
    c = K.parse_options(kb)
    assert c["cut_pos_x"] == -7.8 and c["runout_refill"] is False \
        and c["allow_write"] is False and c["allow_move"] is False, c
    return "pause_on_runout True + runout_gcode KTWO_BOX_RUNOUT, runout_refill False, cut_pos_x nur Default -7.80"


# =====================================================================
# Material-DB, Spülen wie Stock, Preload-Sperren (17.10.2026)
# Grundlage: Stock-Spüllogik, Material-DB-Felder, Preload-Schutz der
# Stock-Firmware (README-k2_box.md §12)
# =====================================================================

def _mtag(mat, col):
    """Synthetisches 40-Zeichen-RFID-vender (box.RFID_VENDOR_FIELDS)."""
    t = "10126000000" + mat + col + "0330000000000000"
    assert len(t) == 40, t
    return t


def _mini_material_db():
    """Synthetische Mini-DB (Format result.list wie Creality, KEINE echten
    Inhalte): IDs 9000x, Werte im Stil der Stock-Default-DB)."""
    def e(dbid, mtype, temp, tmax, vol):
        return {"base": {"id": dbid, "name": "Test " + mtype,
                         "meterialType": mtype, "maxTemp": tmax},
                "kvParam": {"nozzle_temperature": temp,
                            "filament_max_volumetric_speed": vol}}
    lst = [e("90001", "PLA", "220", 240, "23"),
           e("90002", "PLA", "220", 240, "14"),
           e("90003", "PETG", "250", 270, "16"),
           e("90004", "TPU", None, 240, "2")]
    return {"code": 0, "msg": "ok", "result": {"list": lst, "count": len(lst)}}


T_PLA23 = _mtag("190001", "0FFFFFF")    # weiß, vol 23
T_PLA14 = _mtag("190002", "0CE58F8")    # violett, vol 14
T_PETG = _mtag("190003", "026EEEE")     # türkis, PETG 250/270
T_TPU = _mtag("190004", "0000000")      # TPU ohne nozzle_temperature
T_FREMD = _mtag("199999", "0FFFFFF")    # nicht in der DB


def _with_db(mod):
    mod.material_db = K._MDB.MaterialDB(_mini_material_db())
    mod.material_db_info = "Test-DB"
    return mod


def _material_hull(tags=None, db=True, **opt):
    o = dict(allow_write="True")
    o.update(opt)
    bus, printer, mod = build_hull(**o)
    for letter, tag in zip("ABCD", tags or (T_PLA23, T_PLA14, T_PETG, T_TPU)):
        bus.slots[letter]["rfid"] = tag
    if db:
        _with_db(mod)
    return bus, printer, mod


def test_material_db_laden():
    import json
    import tempfile
    assert K.load_material_db("") == (None, "")
    db, info = K.load_material_db("/nicht/da/material_database.json")
    assert db is None and "nicht ladbar" in info and "Fallback" in info, info
    d = tempfile.mkdtemp(prefix="k2box-mdb-")
    good = os.path.join(d, "material_database.json")
    with open(good, "w") as fh:
        json.dump(_mini_material_db(), fh)
    bad = os.path.join(d, "kaputt.json")
    with open(bad, "w") as fh:
        fh.write("{kein json")
    db, info = K.load_material_db(good)
    assert db is not None and len(db) == 4 and "4 Einträge" in info, info
    db2, info2 = K.load_material_db(bad)
    assert db2 is None and "nicht ladbar" in info2, info2
    # Klipper-Hülle: Option -> geladen; kaputt -> Fallback, KEIN Config-Fehler
    bus = FakeBoxBus()
    printer = FakePrinter(bus, with_motion=True)
    mod = K.PrinterK2Box(FakeConfig(printer, test_options(
        material_database=good)))
    st = mod.get_status()
    assert st["material_db"] is True and "geladen" in st["material_db_info"]
    printer.fire("klippy:connect")
    # Start-Meldungen kommen über k2_konsole (respond_raw "// [k2_box] …")
    assert any("geladen" in m for m in printer.gcode.infos + printer.gcode.raw), \
        printer.gcode.infos + printer.gcode.raw
    mod = K.PrinterK2Box(FakeConfig(FakePrinter(FakeBoxBus(), True),
                                    test_options(material_database=bad)))
    assert mod.material_db is None and mod.get_status()["material_db"] is False
    # Default aus
    mod = K.PrinterK2Box(FakeConfig(FakePrinter(FakeBoxBus(), True),
                                    test_options()))
    assert mod.material_db is None and mod.get_status()["prime_speed"] == 0.0
    # Abfragen (Stock-Semantik, Fallback Tn_extrude_temp)
    mdb = K._MDB.MaterialDB(_mini_material_db())
    p = K.material_params(mdb, "190003", 220.0)
    assert p["known"] and p["temp"] == 250.0 and p["max_temp"] == 270.0 \
        and p["vol"] == 16.0 and p["mtype"] == "PETG", p
    p = K.material_params(mdb, "190004", 220.0)    # nozzle_temperature null
    assert p["known"] and not p["temp_known"] and p["temp"] == 220.0 \
        and p["max_temp"] == 240.0 and p["vol"] == 2.0, p
    for key in ("199999", None, "", "-1"):
        p = K.material_params(mdb, key, 220.0)
        assert not p["known"] and p["temp"] == 220.0 and p["vol"] is None, p
    p = K.material_params(None, "190003", 215.0)
    assert not p["known"] and p["temp"] == 215.0
    return ("leer = aus, fehlt/kaputt -> None + Meldung (kein Config-Fehler), "
            "Mini-DB 4 Einträge, PETG 250/270/16, TPU null -> 220")


def test_material_temperaturen():
    # 1) DB an: Einzug PETG-Slot C -> M109 S250 (Ziel 0, Stock-Material-
    #    Temperatur statt max(Ziel, 220)); Einzug TPU (null) -> 220 + Hinweis
    bus, printer, mod = _material_hull()
    printer.target = 0.0
    printer._refresh()
    g = FakeGcmd({"TNN": "T1C"})
    bus.head_at_mm = 100.0
    mod.cmd_extrude(g)
    sc = printer.gcode.scripts
    assert "M109 S250" in sc and "M109 S220" not in sc, sc
    assert "G1 E30 F360" in sc, "Greifen: min(360, 16/2,4·60=400) = 360"
    bus, printer, mod = _material_hull()
    bus.head_at_mm = 100.0
    g = FakeGcmd({"TNN": "T1D"})
    mod.cmd_extrude(g)
    sc = printer.gcode.scripts
    assert "M109 S220" in sc and "G1 E30 F50" in sc, sc   # TPU: Greifen 2/2,4·60
    assert any("HINWEIS" in m and "190004 ohne nozzle_temperature" in m
               and "220" in m for m in g.infos), g.infos
    # unbekanntes Material (nicht in DB) -> 220 + Hinweis; Druckziel höher bleibt
    bus, printer, mod = _material_hull(tags=(T_FREMD,) * 4)
    bus.head_at_mm = 100.0
    printer.target = 230.0
    printer._refresh()
    g = FakeGcmd({"TNN": "T1A"})
    mod.cmd_extrude(g)
    assert "M109 S230" in printer.gcode.scripts, printer.gcode.scripts
    assert any("199999 nicht in der Material-DB" in m for m in g.infos), g.infos
    # TEMP= bleibt fest
    bus, printer, mod = _material_hull()
    bus.head_at_mm = 100.0
    mod.cmd_extrude(FakeGcmd({"TNN": "T1C", "TEMP": "235"}))
    assert "M109 S235" in printer.gcode.scripts
    # 2) DB aus: bisheriges Verhalten max(Ziel, 220), kein RFID-Lesen dafür
    bus, printer, mod = _material_hull(db=False)
    bus.head_at_mm = 100.0
    bus.calls[:] = []
    mod.cmd_extrude(FakeGcmd({"TNN": "T1C"}))
    sc = printer.gcode.scripts
    assert "M109 S220" in sc and "G1 E30 F360" in sc, sc
    first02 = [i for i, c in enumerate(bus.calls) if c[1] == 0x02]
    first10 = [i for i, c in enumerate(bus.calls) if c[1] == 0x10]
    assert not first02 or first02[0] > first10[0], "DB aus: RFID erst nach dem Einzug"
    # 3) Rückzug heizt auf das Material IN der Düse (remain_material)
    bus, printer, mod = _material_hull()
    bus.hub_mask, bus.head = 0x04, True
    mod.remain_material = {"type": "190003", "color": "026EEEE", "tnn": "T1C"}
    mod.cmd_retrude(FakeGcmd({}))
    assert "M109 S250" in printer.gcode.scripts, printer.gcode.scripts
    # 4) Kappung an [extruder] max_temp − 10
    bus, printer, mod = _material_hull()
    printer.objects["configfile"] = _Obj({"settings": {"extruder": {
        "max_temp": 255.0, "nozzle_diameter": 0.4}}})
    bus.head_at_mm = 100.0
    g = FakeGcmd({"TNN": "T1C"})
    mod.cmd_extrude(g)
    assert "M109 S245" in printer.gcode.scripts and any(
        "max_temp" in m for m in g.infos), g.infos
    return ("DB: PETG 250, TPU null -> 220 + Hinweis, fremd -> max(Ziel, 220) "
            "+ Hinweis, TEMP= fest, Rückzug = Material in der Düse, Kappung; "
            "DB aus = bisher (220, F360, kein RFID-Lesen)")


def test_flush_farbvolumen():
    """Farb-Spülmenge gegen die Live-Werte (flush.py-Selbsttest, Log 12./13.10.)."""
    F = K._FLUSH
    c = K.parse_options(test_options())
    W, V, C = "0FFFFFF", "0CE58F8", "026EEEE"
    for src, dst, vol in ((W, W, 60), (W, V, 165), (W, C, 193), (C, W, 367)):
        assert F.flushing_volume(F.parse_color_value(src),
                                 F.parse_color_value(dst)) == vol
    cases = [((W, W, False), [90.0, 70.0], "Volumen 60"),        # 101.25 -> 160
             ((W, V, False), [90.0, 70.0], "Volumen 165"),        # 145 -> 160
             ((C, W, False), [90.0, 70.0, 70.0], "Volumen 367"),  # 229.17
             ((W, V, True), None, "×2"),                          # 290 -> 370
             ((None, V, False), [90.0, 70.0], "Farbe alt unbekannt"),
             ((W, "-1", False), [90.0, 70.0], "Farbe neu unbekannt"),
             ((W, "0ZZZZZZ", False), [90.0, 70.0], "Farbe neu unbekannt")]
    for (src, dst, diff), want, why_part in cases:
        segs, why = K.plan_flush_lengths(c, None, src, dst, diff)
        if want is None:
            assert abs(sum(segs) - 370.0) < 1e-9 \
                and segs == F.cal_flush_list(370.0, K.flush_cfg(c)), segs
        else:
            assert segs == want, (src, dst, segs)
        assert why_part in why, (why_part, why)
    # anderes Material + große Menge: türkis -> weiß ×2 = 458,33
    segs, _w = K.plan_flush_lengths(c, None, C, W, True)
    assert abs(sum(segs) - 458.3333333) < 1e-6, segs
    # LENGTH= -> cal_flush_list (Stock), fixed -> bisherige 90er-Stücke
    assert K.plan_flush_lengths(c, 200)[0] == [100.0, 100.0]
    assert K.plan_flush_lengths(c, 160)[0] == [90.0, 70.0]
    assert K.plan_flush_lengths(c, 0)[0] == []
    cf = K.parse_options(test_options(flush_mode="fixed"))
    assert K.plan_flush_lengths(cf, None, C, W, True)[0] == [90.0, 70.0]
    assert K.plan_flush_lengths(cf, 200)[0] == [90.0, 90.0, 20.0]
    # box_need_clean_length 0 -> keine Endlosschleife, fest
    c0 = K.parse_options(test_options(box_need_clean_length="0"))
    assert K.plan_flush_lengths(c0, None, C, W)[0] == [90.0]
    for bad in ({"flush_mode": "farbe"}, {"flush_clean": "x"},
                {"flush_velocity_factor": "0.5"}, {"nozzle_volume": "400"},
                {"wipe_minimum_cruise_ratio": "1"}):
        try:
            K.parse_options(test_options(**bad))
        except ValueError:
            pass
        else:
            raise AssertionError("hätte abgelehnt werden müssen: %r" % bad)
    return ("Volumen 60/165/193/367 (live), [90,70]/[90,70,70], ×2 min 370, "
            "unbekannt/ungültig -> [90,70], LENGTH -> cal_flush_list, fixed = bisher")


def test_flush_geschwindigkeit_abschnitte():
    c = K.parse_options(test_options())
    vb = K.flush_velocity_base
    assert vb(c, 23) == 360 and vb(c, 14) == 350.00000000000006 \
        and vb(c, None) == 360.0 and abs(vb(c, 23, 0.6) - 540) < 1e-9 \
        and abs(vb(c, 2) - 50.0) < 1e-9
    # Live 12.10. 00:26: [90, 70], 18@360, 72@432 (Spitze 240, warten 235),
    # dann „restore target temp“ 220, 14@360, 56@360
    l0 = K.flush_segment_lines(c, 90.0, 0, 2, 360.0, 220.0, 240.0)
    l1 = K.flush_segment_lines(c, 70.0, 1, 2, 360.0, 220.0, 240.0)
    assert l0 == ["M104 S240", "TEMPERATURE_WAIT SENSOR=extruder MINIMUM=235",
                  "G91", "G1 E18 F360", "G1 E72 F432", "G90"], l0
    assert l1 == ["M104 S220", "G91", "G1 E14 F360", "G1 E56 F360", "G90"], l1
    # Gegenprobe flush.flush_plan (Selbsttest-Vektor)
    F = K._FLUSH
    _segs, plan = F.flush_plan(160, [360.0], 220, 240, F.LIVE_BOX_CFG)
    assert [st for st in plan[0][1] if st[0] == "E"] == [("E", 18.0, 360.0),
                                                         ("E", 72.0, 432.0)]
    # ohne Spitze (max == temp): keine Temperaturzeilen
    assert K.flush_segment_lines(c, 90.0, 0, 2, 360.0, 250.0, 250.0) == [
        "G91", "G1 E18 F360", "G1 E72 F432", "G90"]
    # 4 Abschnitte: Faktor 1,2 / 1,2 / 1,2 / 1 (letzter = max_temp_flush_times)
    feeds = [K.flush_segment_lines(c, s, i, 4, 360.0, 220.0, 220.0)[2]
             for i, s in enumerate([90.0, 70.0, 70.0, 79.17])]
    assert [f.split("F")[1] for f in feeds] == ["432", "432", "432", "360"], feeds
    # flush_velocity_factor 1 = eine Geschwindigkeit (bisher)
    c1 = K.parse_options(test_options(flush_velocity_factor="1"))
    assert K.flush_segment_lines(c1, 90.0, 0, 2, 360.0, 220.0, 220.0)[2] \
        == "G1 E72 F360"
    return "v 360/350/50/540, 18@360 72@432 (240/235) | 220 14@360 56@360 == live, 4 Abschnitte 1,2/1,2/1,2/1"


def test_flush_reinigung_stock():
    c = K.parse_options(test_options())
    mid = K.gcode_nozzle_clean(c, False, 0.0, 20000.0, 0.5)
    assert mid[:4] == ["G90", "SET_VELOCITY_LIMIT ACCEL=5000 MINIMUM_CRUISE_RATIO=0",
                       "G0 X170 F12000", "G0 X135 F12000"], mid[:4]
    assert mid[4:10] == ["G0 X160 Y378 F12000", "G0 X133 Y378 F12000"] * 3
    assert mid[-2:] == ["M400", "SET_VELOCITY_LIMIT ACCEL=20000 MINIMUM_CRUISE_RATIO=0.5"]
    assert not [l for l in mid if l.startswith(("G2", "G3", "G1"))] \
        and "G0 Y345 F12000" not in mid, "Zwischenabschnitt: kein Kreis, bleibt im Schacht"
    end = K.gcode_nozzle_clean(c, True, 1.5, 20000.0, None)
    assert end[1] == "SET_VELOCITY_LIMIT ACCEL=5000", end[1]   # MCR unbekannt
    assert end[4:8] == ["G0 X161.5 Y374 F12000", "G2 I4 J0 F10000",
                        "G0 X168.5 Y374 F10000", "G3 I-4 J0 F10000"], end[4:8]
    assert end[-5:] == ["G0 X133 Y378 F12000", "G0 X160 F12000",
                        "G0 Y345 F12000", "M400",
                        "SET_VELOCITY_LIMIT ACCEL=20000"], end[-5:]
    # ohne [gcode_arcs]: 16-Eck um dieselben Mittelpunkte (165,5 / 164,5)
    poly = K.gcode_nozzle_clean(c, True, 1.5, None, None, arcs=False)
    assert not [l for l in poly if l.startswith(("G2", "G3"))]
    g1 = [l for l in poly if l.startswith("G1 ")]
    assert len(g1) == 32
    for l, cx in [(x, 165.5) for x in g1[:16]] + [(x, 164.5) for x in g1[16:]]:
        pr = dict((t[0], float(t[1:])) for t in l.split()[1:])
        assert abs(math.hypot(pr["X"] - cx, pr["Y"] - 374.0) - 4.0) < 0.01
    # Blasen (Stock blow)
    assert K.gcode_blow("fan0", 5.0, 70.0) == [
        "SET_PIN PIN=fan0 VALUE=255", "G4 P5000", "M400", "SET_PIN PIN=fan0 VALUE=70"]
    return "Blech X170->X135 + 3× Schütteln (Zwischen), Kreise G2/G3 I±4 + Versatz + Ausfahrt (Ende), MCR 0/zurück, 16-Eck-Fallback, blow"


def _flush_hull(**opt):
    bus, printer, mod = _material_hull(**opt)
    printer.objects["output_pin fan0"] = _PinObj(0.4, 255.0)
    printer.objects["output_pin fan0_en"] = _PinObj(1.0, 1.0)
    printer.objects["gcode_arcs"] = object()
    printer.objects["toolhead"].status["minimum_cruise_ratio"] = 0.5
    orig = printer._refresh

    def refresh():
        orig()
        printer.objects["toolhead"].status["minimum_cruise_ratio"] = 0.5
    printer._refresh = refresh
    mod._now = lambda: 1000.5             # Stock-Versatz monotonic % 3 = 1,5
    ctl = mod._ensure_controller()
    return bus, printer, mod, ctl


def test_flush_ablauf_stock():
    # weiß (A) in der Düse -> türkis PETG (C): anderes Material, Farbe
    bus, printer, mod, ctl = _flush_hull()
    mod.remain_material = {"type": "190001", "color": "0FFFFFF", "tnn": "T1A"}
    ctl.loaded_slot = "C"
    printer.target = 0.0
    printer._refresh()
    bus.trace = []
    n0 = len(printer.gcode.scripts)
    g = FakeGcmd({"TNN": "T1C"})
    mod.cmd_flush(g)
    sc = printer.gcode.scripts[n0:]
    t = bus.trace
    # Menge: 193 mm³ -> 156,67 mm ×2 = 313,33 (anderes Material, > 370? nein -> 370)
    F = K._FLUSH
    want = F.cal_flush_list(370.0, K.flush_cfg(mod.cfg))
    assert any("FLUSH-Plan T1C: 370 mm" in m and "Volumen 193" in m
               for m in g.infos), g.infos
    ext = [l for l in sc if l.startswith("G1 E")]
    assert len(ext) == 2 * len(want), (ext, want)
    # Temperatur: max(0, 220, 250) = 250, alt (220) < neu -> keine Spitze;
    # Geschwindigkeit PETG 16 mm³/s -> min(360, 400) = 360, 80 % ×1,2
    assert "M109 S250" in sc and "TEMPERATURE_WAIT SENSOR=extruder MINIMUM=265" not in sc
    assert ext[0] == "G1 E18 F360" and ext[1] == "G1 E72 F432" \
        and ext[-1].endswith("F360"), ext
    # Reihenfolge: M106 S50 -> M109 -> je Abschnitt Extrusion -> Blasen ->
    # Reinigung; Kreise NUR nach dem letzten
    i_fan = sc.index("M106 P0 S50")
    assert i_fan < sc.index("M109 S250"), sc[:12]
    g2 = [i for i, l in enumerate(sc) if l.startswith("G2 ")]
    last_e = max(i for i, l in enumerate(sc) if l.startswith("G1 E"))
    assert len(g2) == 1 and g2[0] > last_e, (g2, last_e)
    assert sc.count("G3 I-4 J0 F10000") == 1
    assert sc.count("G0 X170 F12000") == len(want), "Blech nach jedem Abschnitt"
    assert sc.count("SET_PIN PIN=fan0 VALUE=255") == len(want)
    assert sc.count("G4 P5000") == len(want)
    assert "G0 X161.5 Y374 F12000" in sc, "Kreisstart 160 + 1,5 (monotonic % 3)"
    assert sc.count("SET_VELOCITY_LIMIT ACCEL=5000 MINIMUM_CRUISE_RATIO=0") == len(want)
    assert sc[-1].startswith("SET_PIN PIN=fan0 VALUE="), sc[-3:]
    assert printer.objects["output_pin fan0"].value == 0.4, "Lüfter zurück"
    assert printer.accel == 20000.0 and printer.pos[:2] == [160.0, 345.0], printer.pos
    # remain_material = neues Material (Stock remain_material.save)
    assert mod.remain_material == {"type": "190003", "color": "026EEEE",
                                   "tnn": "T1C"}, mod.remain_material
    assert not [e for e in t if e[0] == "bus" and e[1] == 0x0E], "clog aus: kein Messrad"
    # 2) gleiche Farbe/Material (PETG -> PETG): Spitze maxTemp 270 im 1.
    #    Abschnitt, zurück auf 250 im letzten
    n0 = len(printer.gcode.scripts)
    mod.cmd_flush(FakeGcmd({"TNN": "T1C"}))
    sc = printer.gcode.scripts[n0:]
    assert sc.index("M104 S270") < sc.index("TEMPERATURE_WAIT SENSOR=extruder "
                                             "MINIMUM=265") < sc.index("M104 S250"), sc
    assert [l for l in sc if l.startswith("G1 E")] == [
        "G1 E18 F360", "G1 E72 F432", "G1 E14 F360", "G1 E56 F360"], sc
    # 3) Geschwindigkeit aus der DB: PLA vol 14 -> 350 / 420
    n0 = len(printer.gcode.scripts)
    mod.cmd_flush(FakeGcmd({"TNN": "T1B"}))
    sc = printer.gcode.scripts[n0:]
    assert "G1 E18 F350" in sc and "G1 E72 F420" in sc, sc
    # 4) DB aus + unbekannte Farben = bisherige Menge/Temperatur
    bus, printer, mod, ctl = _flush_hull(db=False, tags=("TAG",) * 4)
    n0 = len(printer.gcode.scripts)
    printer.target = 0.0
    printer._refresh()
    mod.cmd_flush(FakeGcmd({"TNN": "T1B"}))
    sc = printer.gcode.scripts[n0:]
    assert "M109 S220" in sc and [l for l in sc if l.startswith("G1 E")] == [
        "G1 E18 F360", "G1 E72 F432", "G1 E14 F360", "G1 E56 F360"], sc
    assert mod.remain_material["type"] is None
    # 5) legacy + ohne Blasen + ohne Versatz: volles Abstreifen je Abschnitt
    bus, printer, mod, ctl = _flush_hull(flush_clean="legacy", flush_blow="False")
    n0 = len(printer.gcode.scripts)
    mod.cmd_flush(FakeGcmd({"TNN": "T1B"}))
    sc = printer.gcode.scripts[n0:]
    assert not [l for l in sc if l.startswith(("M106", "SET_PIN", "G2", "G3"))]
    assert sc.count("G0 X160 Y374 F12000") == 2, "Kreise nach jedem Abschnitt"
    bus, printer, mod, ctl = _flush_hull(flush_wipe_offset="0")
    n0 = len(printer.gcode.scripts)
    mod.cmd_flush(FakeGcmd({"TNN": "T1B"}))
    assert "G0 X160 Y374 F12000" in printer.gcode.scripts[n0:]
    # 6) ohne [gcode_arcs]: 16-Eck statt G2/G3
    bus, printer, mod, ctl = _flush_hull()
    del printer.objects["gcode_arcs"]
    n0 = len(printer.gcode.scripts)
    mod.cmd_flush(FakeGcmd({"TNN": "T1B"}))
    sc = printer.gcode.scripts[n0:]
    assert not [l for l in sc if l.startswith(("G2", "G3"))] \
        and len([l for l in sc if l.startswith("G1 X")]) == 32
    # 7) Plan ohne allow_move: nichts bewegt, remain_material unverändert
    bus, printer, mod, ctl = _flush_hull(allow_move="False")
    n0 = len(printer.gcode.scripts)
    g = FakeGcmd({"TNN": "T1C"})
    mod.cmd_flush(g)
    assert not [l for l in printer.gcode.scripts[n0:]
                if l.split()[0] in ("G0", "G1", "M109", "SET_PIN")]
    assert mod.remain_material["type"] is None and any("PLAN" in m for m in g.infos)
    return ("Farbe+Material: 370 mm, M106 S50, M109 250, 18@360/72@432, Blasen + "
            "Blech je Abschnitt, Kreise nur am Ende (Versatz 1,5), Lüfter/ACCEL/"
            "MCR zurück, remain_material; PETG->PETG Spitze 270; PLA14 350/420; "
            "DB aus = bisher; legacy/ohne arcs/ohne allow_move")


def test_flush_verstopfung():
    bus, printer, mod, ctl = _flush_hull(flush_clog_check="True")
    g = FakeGcmd({"TNN": "T1B"})
    bus.trace = []
    mod.cmd_flush(g)
    n_wheel = len([e for e in bus.trace if e[0] == "bus" and e[1] == 0x0E])
    assert n_wheel == 3, n_wheel                    # vorher + 2 Abschnitte
    assert any("WARNUNG FLUSH: Messrad" in m and "key845" in m
               for m in g.infos), g.infos
    st = mod.get_status()
    assert st["chain_error"] == "" and not printer.paused, "nur Warnung"
    # Messrad bewegt sich (> 10 mm): keine Warnung
    bus, printer, mod, ctl = _flush_hull(flush_clog_check="True")
    orig = ctl.get_wheel
    ticks = iter([0.0, -60.0, -110.0])
    ctl.get_wheel = lambda: next(ticks)
    g = FakeGcmd({"TNN": "T1B"})
    mod.cmd_flush(g)
    assert not any("WARNUNG FLUSH" in m for m in g.infos), g.infos
    ctl.get_wheel = orig
    return "Messrad < 10 mm -> nur Warnung (key845), kein Fehler/Pause; Default aus"


def test_prime_speed_status_und_makro():
    bus, printer, mod = _material_hull()
    mod._ensure_controller()
    st = mod.get_status
    mod.remain_material = {"type": "190001", "color": None, "tnn": ""}
    assert st()["prime_speed"] == 460.0
    mod.remain_material = {"type": "190002", "color": None, "tnn": ""}
    assert st()["prime_speed"] == 280.00000000000006
    mod.remain_material = {"type": "199999", "color": None, "tnn": ""}
    assert abs(st()["prime_speed"] - 288.0) < 1e-9
    mod.remain_material = {"type": None, "color": None, "tnn": ""}
    ctl = mod.controller
    ctl.loaded_slot = "D"
    ctl.tag_cache["D"] = T_TPU
    assert abs(st()["prime_speed"] - 40.0) < 1e-9, "geladener Slot (Cache) TPU"
    bus.calls[:] = []
    st()
    assert bus.calls == [], "get_status ohne Bus"
    mod.material_db = None
    assert st()["prime_speed"] == 0.0
    try:
        env, macros, kcfg, param = _render_env()
    except ImportError:
        return "Status ok, Makro SKIP (jinja2 fehlt)"
    pos = {"gcode_position": {"x": 133.0, "y": 378.0, "z": 50.0, "e": 0.0}}
    for kb, want in (({"prime_speed": 280.00000000000006}, "F280.00000000000006"),
                     ({"prime_speed": 0.0}, "F460.0"), ({}, "F460.0")):
        pr = _rprinter(param, kcfg, gcode_move=pos, k2_box=kb)
        lines, msgs = _render(env, macros, "_PI_RESUME_PRIME", pr)
        assert "G1 E82.0 %s" % want in lines, (kb, lines)
    pr = _rprinter(param, kcfg, gcode_move=pos)
    del pr["k2_box"]
    lines, _m = _render(env, macros, "_PI_RESUME_PRIME", pr)
    assert "G1 E82.0 F460.0" in lines, lines
    return "prime_speed 460/280/288/TPU 40/DB aus 0 (ohne Bus); Makro nimmt prime_speed > 0, sonst 460"


def test_runout_redo_basistemperatur():
    # Nachladen/Nachholen geben BASE_TEMP (Drucktemperatur vor der Pause)
    # weiter, EXTRUDE/FLUSH rechnen dann selbst mit der Material-DB
    bus, printer, mod = build_runout()
    mod.cmd_runout(FakeGcmd({}))
    sc = printer.gcode.scripts
    assert "KTWO_BOX_EXTRUDE TNN=T1C BASE_TEMP=235" in sc \
        and "KTWO_BOX_FLUSH TNN=T1C BASE_TEMP=235" in sc, sc
    bus, printer, mod = build_runout()
    mod.cmd_runout(FakeGcmd({"TEMP": "240"}))
    assert "KTWO_BOX_FLUSH TNN=T1C TEMP=240" in printer.gcode.scripts
    # mit DB + PETG-Ersatzslot: Heizen max(235, 250)
    bus, printer, mod = build_runout()
    for letter, tag in zip("ABCD", (T_PETG, T_PLA23, T_PETG, T_PETG)):
        bus.slots[letter]["rfid"] = tag
    ctl = mod._ensure_controller()
    bus.slots["A"]["filament"] = True
    ctl.get_rfid("A")
    bus.slots["A"]["filament"] = False
    _with_db(mod)
    mod.cmd_runout(FakeGcmd({}))
    assert "M109 S250" in printer.gcode.scripts, printer.gcode.scripts
    return "Runout/Nachholen: BASE_TEMP=Drucktemperatur (TEMP= bleibt fest), DB: PETG 250"


def test_preload_sperren():
    R = K.preload_block_reason
    assert "Maske 0" in R(2, 0, False, False) and "Maske 0" in R(3, 0, False, True)
    assert R(1, 0, True, False) is None, "op 0/1: Maske/Kopf egal"
    assert "FORCE=1" in R(1, 1, False, True) and R(1, 1, False, True, True) is None
    assert "meldet Filament" in R(2, 1, True, False)
    assert "unbekannt" in R(2, 1, None, False)
    assert "unbekannt" in R(2, 1, None, True, True), "FORCE hebt die Kopfregel nicht auf"
    assert R(2, 1, False, False) is None and R(3, 4, False, True, True) is None
    # Klipper-Hülle KTWO_BOX_PRELOAD
    def run(params, head=False, printing=False, sensor=True):
        bus, printer, mod = build_hull(allow_write="True")
        bus.head = head
        if not sensor:
            mod.cfg["filament_sensor"] = ""
        printer.set_printing(printing)
        bus.calls[:] = []
        g = FakeGcmd(params)
        mod.cmd_preload(g)
        return [c for c in bus.calls if c[1] == 0x0D], g.infos
    sent, infos = run({"SLOT": "B", "OP": "2"})
    assert len(sent) == 1 and tuple(sent[0][2]) == (0x02, 0x02) \
        and sent[0][3]["timeout"] >= 380, sent
    for params, kw, txt in (({"SLOT": "B", "OP": "2"}, {"head": True}, "meldet Filament"),
                            ({"SLOT": "B", "OP": "3"}, {"sensor": False}, "unbekannt"),
                            ({"SLOT": "B", "OP": "2"}, {"printing": True}, "FORCE=1"),
                            ({"SLOT": "B", "OP": "1"}, {"printing": True}, "FORCE=1"),
                            ({"SLOT": "B", "OP": "2", "FORCE": "1"},
                             {"printing": True, "head": True}, "meldet Filament")):
        sent, infos = run(params, **kw)
        assert sent == [] and any("gesperrt" in m and txt in m and
                                  "nichts gesendet" in m for m in infos), (params, infos)
    sent, _i = run({"SLOT": "B", "OP": "2", "FORCE": "1"}, printing=True)
    assert len(sent) == 1, "FORCE=1 im Druck bei leerem Kopf"
    sent, _i = run({"SLOT": "B", "OP": "1"})
    assert len(sent) == 1
    # Controller mit guard
    cfg, bus, ctl = build_system()
    try:
        ctl.do_preload("A", 2, guard={"head": True, "printing": False})
    except PermissionError as exc:
        assert "nichts gesendet" in str(exc)
    else:
        raise AssertionError("guard hätte sperren müssen")
    assert not [c for c in bus.calls if c[1] == 0x0D]
    return "Maske 0 abgelehnt (N2), op 2/3 nur Kopf leer, Druck nur mit FORCE=1 (N3), sonst nichts gesendet"


def test_box_wait_timeout():
    import configparser
    assert K.CHANGE_DEFAULTS["box_wait_timeout"] == 380.0 \
        == float(BOX.preload_force_timeout(0x01)), K.CHANGE_DEFAULTS["box_wait_timeout"]
    c = K.parse_options(test_options())
    assert c["box_wait_timeout"] >= 380.0
    cp = configparser.RawConfigParser(strict=False, inline_comment_prefixes=("#",))
    cp.read_string(open(os.path.join(_HERE, "config-pi", "k2-module.cfg"),
                        encoding="utf-8").read())
    kb = dict(cp.items("k2_box"))
    assert float(kb["box_wait_timeout"]) >= 380.0, kb["box_wait_timeout"]
    assert "material_database" not in kb, "Auslieferung: DB auskommentiert"
    # GEÄNDERT 18.10.2026 (E4): Auslieferung flush_mode matrix (Stock)
    assert kb["flush_mode"] == "matrix" and kb["flush_clog_check"] == "False"
    cfg = K.parse_options(kb)
    assert cfg["material_database"] == "" and cfg["flush_blow"] is True
    txt = open(os.path.join(_HERE, "config-pi", "k2-module.cfg"),
               encoding="utf-8").read()
    assert "#material_database: ~/printer_data/config/k2/material_database.json" in txt
    import inspect
    sig = inspect.signature(K.K2BoxController.wait_box_ready)
    assert sig.parameters["timeout"].default == 380.0
    return "box_wait_timeout 380 = preload_force_timeout(1 Slot) (Default, k2-module.cfg, wait_box_ready)"


# =====================================================================
# Düsen-Material persistent (remain_material_file) – 17.10.2026
# (Stock remain_material_data.json)
# =====================================================================

def _prep_chain(bus):
    """Wie _hull_t1b_ok: Slot A geladen, Einzug erreicht den Kopfsensor."""
    bus.hub_mask, bus.head = 0x01, True
    bus.head_at_mm = bus.odo + 300.0
    bus.need_extruder_for_mode7 = True


def _rm_dir():
    d = _tempfile.mkdtemp(prefix="k2box-remain-")
    _TMP_DIRS.append(d)
    return d


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read_json(path):
    import json
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# Stock-Format (Nachtrag Gegenprobe 17.10.: kompakt, eine Zeile, json.dump);
# Farbe/Typ synthetisch
STOCK_REMAIN = '{"remain_material": {"color": "0ce58f8", "type": "190002"}}'


def test_remain_laden():
    import glob
    d = _rm_dir()
    # Default + Tilde (HOME zeigt im Test auf ein Wegwerf-Verzeichnis)
    cfg = K.parse_options({})
    assert cfg["remain_material_file"] == \
        "~/printer_data/config/k2/remain_material.json"
    assert K.remain_path(cfg["remain_material_file"]) == os.path.join(
        _TEST_HOME, "printer_data", "config", "k2", "remain_material.json")
    assert K.parse_options({"remain_material_file": ""})[
        "remain_material_file"] == ""
    assert K.load_remain_material("") == (None, "aus", "")
    # Auslieferung k2-module.cfg: Persistenz an (Default-Pfad), matrix an
    # (GEÄNDERT 18.10.2026, E4)
    import configparser
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.read(os.path.join(_HERE, "config-pi", "k2-module.cfg"),
            encoding="utf-8")
    kb = dict(cp.items("k2_box"))
    assert kb["remain_material_file"] == \
        "~/printer_data/config/k2/remain_material.json" and \
        kb["flush_mode"] == "matrix", kb.get("remain_material_file")
    # Stock-Datei als Startwert (ohne tnn)
    p = os.path.join(d, "stock.json")
    _write(p, STOCK_REMAIN)
    rm, st, info = K.load_remain_material(p)
    assert st == "geladen" and rm == {"type": "190002", "color": "0CE58F8",
                                       "tnn": ""}, (rm, st)
    assert "0CE58F8" in info
    # mit tnn + Zusatzschlüssel (toleriert), null-Werte
    _write(p, '{"x": 1, "remain_material": {"color": null, "type": null, '
              '"tnn": "t1b", "saved_at": 5}}')
    assert K.load_remain_material(p)[:2] == (
        {"type": None, "color": None, "tnn": "T1B"}, "geladen")
    _write(p, '{"remain_material": {"color": "-1", "type": "190001", '
              '"tnn": "T9Z"}}')
    rm, st, info = K.load_remain_material(p)
    assert st == "geladen" and rm["color"] is None and rm["tnn"] == "" \
        and "ignoriert" in info, (rm, info)
    # fehlt -> nur Meldung, nichts angelegt
    q = os.path.join(d, "neu", "remain.json")
    rm, st, info = K.load_remain_material(q)
    assert (rm, st) == (None, "fehlt") and not os.path.exists(q), info
    # kaputt / falsche Struktur / Farbe ungültig -> fehler + beiseitegelegt,
    # Inhalt bleibt erhalten (nie überschrieben)
    bad = ["", "{kaputt", "[]", "null", '{"remain_material": {}}',
           '{"remain_material": null}', '{"anders": 1}',
           '{"remain_material": [1, 2]}',
           '{"remain_material": {"color": "0GGGGGG", "type": "1"}}',
           '{"remain_material": {"color": "12345", "type": "1"}}',
           '{"remain_material": {"color": "0FFFFFF", "type": {"a": 1}}}']
    for i, text in enumerate(bad):
        b = os.path.join(d, "bad%d.json" % i)
        _write(b, text)
        rm, st, info = K.load_remain_material(b)
        assert rm is None and st == "fehler", (text, st, info)
        moved = glob.glob(b + ".defekt-*")
        assert not os.path.exists(b) and len(moved) == 1, (text, moved)
        assert open(moved[0], encoding="utf-8").read() == text
        assert "beiseitegelegt" in info, info
    # Hülle: Laden erst bei klippy:connect, Status, kein Config-Fehler
    _write(p, STOCK_REMAIN)
    bus = FakeBoxBus()
    printer = FakePrinter(bus, with_motion=True)
    mod = K.PrinterK2Box(FakeConfig(printer, test_options(
        allow_move="True", remain_material_file=p)))
    assert mod.get_status()["remain_material_load"] == "ungelesen"
    assert mod.remain_material["color"] is None
    printer.fire("klippy:connect")
    st = mod.get_status()
    assert st["remain_material_file"] == p and st["remain_material_load"] == \
        "geladen" and st["remain_material"]["color"] == "0CE58F8", st
    printer.fire("klippy:connect")      # zweites connect liest nicht erneut
    b = os.path.join(d, "kaputt.json")
    _write(b, "{")
    printer = FakePrinter(FakeBoxBus(), with_motion=True)
    mod = K.PrinterK2Box(FakeConfig(printer, test_options(
        remain_material_file=b)))
    printer.fire("klippy:connect")
    st = mod.get_status()
    assert st["remain_material_load"] == "fehler" and \
        st["remain_material"]["color"] is None
    assert any(m.startswith("// [k2_box] WARNUNG") and "ungültig" in m
               for m in printer.gcode.raw), printer.gcode.raw
    # Stock-Datei als Startwert -> erster Wechsel nutzt die alte Farbe
    _write(p, STOCK_REMAIN)
    bus, printer, mod, ctl = _flush_hull(remain_material_file=p)
    g = FakeGcmd({"TNN": "T1A"})
    mod.cmd_flush(g)
    assert any("Farbe 0CE58F8 -> 0FFFFFF" in m for m in g.infos), g.infos
    return ("ok/fehlt/kaputt (11 Fälle, .defekt-…, Inhalt erhalten)/null/"
            "tnn tolerant, Laden bei connect, Status, Stock-Datei als Startwert")


def test_remain_schreiben():
    d = _rm_dir()
    p = os.path.join(d, "tief", "k2", "remain.json")     # Verzeichnis fehlt
    # atomar: makedirs, tmp + fsync + os.replace + Verzeichnis-fsync
    calls = []
    o_rep, o_fs = K.os.replace, K.os.fsync
    K.os.replace = lambda a, b: (calls.append(("replace", a, b)), o_rep(a, b))[1]
    K.os.fsync = lambda fd: (calls.append(("fsync", fd)), o_fs(fd))[1]
    try:
        K.save_remain_material(p, {"type": "190003", "color": "026eeee",
                                   "tnn": "T1C"})
    finally:
        K.os.replace, K.os.fsync = o_rep, o_fs
    assert [c[0] for c in calls] == ["fsync", "replace", "fsync"], calls
    assert calls[1][1] == p + ".tmp" and calls[1][2] == p
    assert _read_json(p) == {"remain_material": {
        "color": "026EEEE", "type": "190003", "tnn": "T1C"}}
    assert not os.path.exists(p + ".tmp")
    # andere Top-Level-Schlüssel bleiben (Stock read-modify-write)
    _write(p, '{"fremd": [1], "remain_material": {"color": null, "type": null}}')
    K.save_remain_material(p, {"type": None, "color": None, "tnn": ""})
    assert _read_json(p) == {"fremd": [1], "remain_material": {
        "color": None, "type": None}}
    # vorhandene kaputte Datei beim Schreiben: erst beiseitelegen, nie
    # überschreiben; ungültiger Typ wird als null geschrieben
    import glob
    _write(p, "{kaputt")
    K.save_remain_material(p, {"type": "a b", "color": "0abcdef", "tnn": ""})
    assert _read_json(p) == {"remain_material": {"color": "0ABCDEF",
                                                 "type": None}}
    moved = glob.glob(p + ".defekt-*")
    assert len(moved) == 1 and open(moved[0]).read() == "{kaputt", moved
    # Schreibfehler mitten im dump: Original bleibt, tmp weg
    _write(p, STOCK_REMAIN)
    o_dump = K.json.dump

    def boom(*a, **k):
        raise OSError("Platte voll")
    K.json.dump = boom
    try:
        K.save_remain_material(p, {"type": "1", "color": None, "tnn": ""})
    except OSError:
        pass
    else:
        raise AssertionError("OSError erwartet")
    finally:
        K.json.dump = o_dump
    assert open(p, encoding="utf-8").read() == STOCK_REMAIN
    assert not os.path.exists(p + ".tmp")
    # Hülle: Schreiben NUR nach erfolgreichem FLUSH und nur bei Änderung
    f = os.path.join(d, "hull", "remain.json")
    saves = []
    o_save = K.save_remain_material

    def counting(path, remain):
        saves.append(dict(remain))
        return o_save(path, remain)
    K.save_remain_material = counting
    try:
        bus, printer, mod, ctl = _flush_hull(remain_material_file=f)
        assert mod.get_status()["remain_material_load"] == "fehlt"
        _prep_chain(bus)
        # Kette OHNE Flush (Cut/Rückzug/Einzug/End) -> kein Schreiben
        for fn, prm in ((mod.cmd_pre_opt, {"TNN": "T1B"}), (mod.cmd_cut, {}),
                        (mod.cmd_retrude, {}), (mod.cmd_extrude, {"TNN": "T1B"}),
                        (mod.cmd_waste, {}), (mod.cmd_end_opt, {"RETURN": "0"})):
            fn(FakeGcmd(prm))
        assert not saves and not os.path.exists(f), saves
        # Flush -> geschrieben
        mod.cmd_flush(FakeGcmd({"TNN": "T1B"}))
        assert len(saves) == 1 and _read_json(f) == {"remain_material": {
            "color": "0CE58F8", "type": "190002", "tnn": "T1B"}}, saves
        # gleiches Material erneut -> KEIN Schreiben
        mod.cmd_flush(FakeGcmd({"TNN": "T1B"}))
        assert len(saves) == 1, saves
        # Spülfehler (G-Code-Fehler im Abschnitt, ohne Druck) -> kein Schreiben
        printer.fail_on = "G1 E"
        try:
            mod.cmd_flush(FakeGcmd({"TNN": "T1C"}))
        except Exception:
            pass
        printer.fail_on = None
        mod.chain_error = ""
        assert len(saves) == 1 and mod.remain_material["tnn"] == "T1B"
        # Abbruch im Druck (-> PAUSE, wie Cancel-Pfad) -> kein Schreiben
        printer.set_printing(True)
        printer.fail_on = "G1 E"
        mod.cmd_flush(FakeGcmd({"TNN": "T1C"}))
        printer.fail_on = None
        assert printer.paused and len(saves) == 1, saves
        printer.paused = False
        printer.set_printing(False)
        mod.chain_error = ""
        mod.aborted = None
        # nur Plan (allow_move False) -> kein Schreiben
        bus2, pr2, mod2, _c2 = _flush_hull(remain_material_file=f,
                                           allow_move="False")
        assert mod2.remain_material["tnn"] == "T1B"     # Neustart: geladen
        mod2.cmd_flush(FakeGcmd({"TNN": "T1C"}))
        assert len(saves) == 1
        # anderes Material -> geschrieben
        mod.cmd_flush(FakeGcmd({"TNN": "T1C"}))
        assert len(saves) == 2 and _read_json(f)["remain_material"]["type"] \
            == "190003"
        # Neustart-Simulation: neuer Lauf liest den Wert, Spülplan nutzt ihn
        bus3, pr3, mod3, _c3 = _flush_hull(remain_material_file=f)
        g = FakeGcmd({"TNN": "T1A"})
        mod3.cmd_flush(g)
        assert any("Farbe 026EEEE -> 0FFFFFF" in m and "anderes Material" in m
                   for m in g.infos), g.infos
        # Schreibfehler (Elternpfad ist eine Datei) -> nur WARNUNG, FLUSH ok
        blocker = os.path.join(d, "blocker")
        _write(blocker, "x")
        bus4, pr4, mod4, _c4 = _flush_hull(
            remain_material_file=os.path.join(blocker, "remain.json"))
        g = FakeGcmd({"TNN": "T1B"})
        mod4.cmd_flush(g)
        assert any("WARNUNG Düsen-Material nicht gespeichert" in m
                   for m in g.infos) and any("ok" in m for m in g.infos) \
            and mod4.remain_material["type"] == "190002" \
            and mod4.chain_error == "", g.infos
        # KTWO_BOX_REMAIN (Nicht-Stock): Anzeige, Setzen, CLEAR, Fehler
        n0 = len(saves)
        g = FakeGcmd({})
        mod.cmd_remain(g)
        assert len(saves) == n0 and "Farbe 026EEEE" in g.infos[-1], g.infos
        mod.cmd_remain(FakeGcmd({"COLOR": "#00ff00", "TYPE": "190001",
                                 "TNN": "T1A"}))
        assert _read_json(f)["remain_material"] == {
            "color": "000FF00", "type": "190001", "tnn": "T1A"}
        mod.cmd_remain(FakeGcmd({"COLOR": "000FF00"}))        # unverändert
        assert len(saves) == n0 + 1
        mod.cmd_remain(FakeGcmd({"CLEAR": "1"}))
        assert _read_json(f)["remain_material"] == {"color": None,
                                                    "type": None}
        for bad in ({"COLOR": "rot"}, {"TYPE": "a b"}, {"TNN": "T9A"}):
            try:
                mod.cmd_remain(FakeGcmd(bad))
            except Exception as exc:
                assert "KTWO_BOX_REMAIN" in str(exc), exc
            else:
                raise AssertionError("Fehler erwartet: %r" % bad)
        # aus (leer): nie schreiben
        bus5, pr5, mod5, _c5 = _flush_hull()
        mod5.cmd_flush(FakeGcmd({"TNN": "T1C"}))
        assert mod5.get_status()["remain_material_file"] == "" and \
            mod5.get_status()["remain_material_load"] == "aus"
    finally:
        K.save_remain_material = o_save
    assert not os.path.exists(os.path.join(_TEST_HOME, "printer_data"))
    return ("atomar (tmp/fsync/replace/Verz.-fsync, makedirs), Fehler -> "
            "Original bleibt; nur nach FLUSH + bei Änderung (nicht Cut/Rückzug/"
            "Einzug/Fehler/Abbruch/Plan), Neustart, Schreibfehler = WARNUNG, "
            "KTWO_BOX_REMAIN")


# =====================================================================
# Slicer-Spülmatrix (flush_mode matrix)
# =====================================================================

def _matrix16():
    """Synthetische 16×16-Matrix, Diagonale 0, ASYMMETRISCH ([0][1]=569,
    [1][0]=419 wie Live-Beispiel A.2, sonst 300 + 7·i + 3·j)."""
    m = []
    for i in range(16):
        for j in range(16):
            m.append(0 if i == j else 300 + 7 * i + 3 * j)
    m[0 * 16 + 1] = 569
    m[1 * 16 + 0] = 419
    return m


def _gcode_file(d, name, matrix=None, mult="1.3", where="end", filler_kb=1200):
    """Synthetische G-Code-Datei (KEIN echter Druckauftrag)."""
    lines = []
    cfg = []
    if matrix is not None:
        cfg.append("; flush_volumes_matrix = " + ",".join(
            "%g" % v for v in matrix))
    if mult is not None:
        cfg.append("; flush_multiplier = %s" % mult)
    head = ["; HEADER_BLOCK_START", "; generated by Testgenerator",
            "; HEADER_BLOCK_END"]
    if where == "head":
        head += cfg
    lines += head
    body = "G1 X10 Y10 E0.1 ; Fülltext\n" * (filler_kb * 1024 // 27)
    tail = ["; CONFIG_BLOCK_START"] + (cfg if where == "end" else []) + \
        ["; CONFIG_BLOCK_END"]
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n" + body + "\n".join(tail) + "\n")
    return p


class _VSD:
    """Upstream virtual_sdcard (file_path()/get_status, UP-VS:104-112)."""

    def __init__(self, path):
        self.path = path

    def file_path(self):
        return self.path

    def get_status(self, eventtime=None):
        return {"file_path": self.path}


def test_matrix_parsen():
    d = _rm_dir()
    M = _matrix16()
    p_end = _gcode_file(d, "ende.gcode", M)
    assert os.path.getsize(p_end) > 2 * K.MATRIX_SCAN_BYTES
    info = K.read_flush_matrix(p_end)
    assert info["n"] == 16 and info["multiplier"] == 1.3 and \
        info["where"] == "Dateiende" and info["matrix"] == [float(v) for v in M]
    p_head = _gcode_file(d, "kopf.gcode", M, where="head")
    info_h = K.read_flush_matrix(p_head)
    assert info_h["where"] == "Dateikopf" and info_h["n"] == 16
    small = _gcode_file(d, "klein.gcode", M, filler_kb=1)
    assert K.read_flush_matrix(small)["where"] == "Datei"
    # L8 (18.10.2026): Fenster 512 KiB wie Stock metadata.py READ_SIZE –
    # Matrix ~300 KiB vor dem Ende (früher außerhalb von 256 KiB) gefunden
    assert K.MATRIX_SCAN_BYTES == 512 * 1024
    deep = os.path.join(d, "tief.gcode")
    with open(deep, "w", encoding="utf-8") as fh:
        fh.write("G1 X1\n" * 200000 + "; flush_volumes_matrix = 0,1,1,0\n"
                 + "G1 X1\n" * 50000)
    assert K.read_flush_matrix(deep)["where"] == "Dateiende"
    # Matrix außerhalb der Fenster (Mitte) -> nicht gefunden
    mid = os.path.join(d, "mitte.gcode")
    with open(mid, "w", encoding="utf-8") as fh:
        fh.write("G1 X1\n" * 100000 + "; flush_volumes_matrix = 0,1,1,0\n"
                 + "G1 X1\n" * 100000)
    for path, text in ((mid, "keine"),
                       (_gcode_file(d, "ohne.gcode", None), "keine"),
                       (_gcode_file(d, "15.gcode", list(range(15))),
                        "nicht quadratisch")):
        try:
            K.read_flush_matrix(path)
        except ValueError as exc:
            assert text in str(exc), exc
        else:
            raise AssertionError("ValueError erwartet: %s" % path)
    # ohne Multiplikator -> None (= 1 wie Stock)
    assert K.read_flush_matrix(_gcode_file(d, "om.gcode", [0, 240, 120, 0],
                                           mult=None))["multiplier"] is None
    # Länge = M[alt][neu] / 2,4 · m (flush.py gcode_matrix_flush_len)
    l01, w01 = K.slicer_matrix_flush(info, 0, 1)
    l10, _w = K.slicer_matrix_flush(info, 1, 0)
    assert abs(l01 - 569 / 2.4 * 1.3) < 1e-9 and abs(l01 - 308.2083333) < 1e-6
    assert abs(l10 - 419 / 2.4 * 1.3) < 1e-9 and abs(l10 - 226.9583333) < 1e-6
    assert "[0][1] = 569" in w01 and "1.3" in w01, w01
    assert abs(K.slicer_matrix_flush(info, 15, 14)[0]
               - (300 + 105 + 42) / 2.4 * 1.3) < 1e-9
    for a, b in ((16, 0), (0, 16), (-1, 0), (None, 1)):
        try:
            K.slicer_matrix_flush(info, a, b)
        except ValueError:
            pass
        else:
            raise AssertionError("Index %r/%r hätte scheitern müssen" % (a, b))
    # plan_flush_lengths: matrix -> Stock-Nachbearbeitung, sonst wie color
    c = K.parse_options({"flush_mode": "matrix"})
    segs, why = K.plan_flush_lengths(c, None, "0FFFFFF", "0CE58F8", False,
                                     matrix={"len": l01, "why": w01})
    assert abs(sum(segs) - l01) < 1e-9 and "Slicer-Matrix" in why, (segs, why)
    segs, _w = K.plan_flush_lengths(c, None, None, None, True,
                                    matrix={"len": 100.0, "why": "x"})
    assert sum(segs) == 370.0                     # anders: max(200, 370)
    segs, _w = K.plan_flush_lengths(c, None, None, None, False,
                                    matrix={"len": 50.0, "why": "x"})
    assert sum(segs) == 160.0                     # gleich: min 90 + 70
    assert K.plan_flush_lengths(c, None, "0FFFFFF", "0CE58F8", False) == \
        K.plan_flush_lengths(K.parse_options({}), None, "0FFFFFF", "0CE58F8",
                             False)               # ohne Matrix = color
    assert K.parse_options({"flush_mode": "MATRIX"})["flush_mode"] == "matrix"
    return ("Kopf/Ende/klein/Mitte/300 KiB tief (512 KiB), 16×16 asymmetrisch [0][1]=569 -> "
            "308,21 / [1][0]=419 -> 226,96 (×1,3), Index-/Formfehler, "
            "Nachbearbeitung min 160 / anders min 370")


def _matrix_hull(gfile, **opt):
    o = dict(flush_mode="matrix")
    o.update(opt)
    if o.get("flush_mode") is None:       # None = Default (E4: matrix)
        o.pop("flush_mode")
    bus, printer, mod, ctl = _flush_hull(**o)
    printer.objects["virtual_sdcard"] = _VSD(gfile)
    printer.set_printing(True)
    return bus, printer, mod, ctl


def _plan_line(g):
    return [m for m in g.infos if "FLUSH-Plan" in m][-1]


def test_matrix_flush():
    d = _rm_dir()
    gf = _gcode_file(d, "druck.gcode", _matrix16())
    bus, printer, mod, ctl = _matrix_hull(gf)
    reads = []
    o_read = K.read_flush_matrix
    K.read_flush_matrix = lambda p, *a: (reads.append(p), o_read(p, *a))[1]
    try:
        # GEÄNDERT 18.10.2026 (E5/A4): T1A stammt aus dem VORDRUCK
        # (remain_material) -> erster Einzug dieses Drucks über den Farbweg
        # (Stock last_tnn None), KEIN Matrix-Lesen. Bis 18.10. nahm k2_box
        # hier schon [0][1].
        mod.remain_material = {"type": "190001", "color": "0FFFFFF",
                               "tnn": "T1A"}
        g = FakeGcmd({"TNN": "T1A", "TOOL": "0"})
        mod.cmd_flush(g)
        assert "Slicer-Matrix [" not in _plan_line(g) and \
            "Farbe 0FFFFFF -> 0FFFFFF" in _plan_line(g), _plan_line(g)
        assert any("erster Einzug im Druck" in m and "Farbweg" in m
                   for m in g.infos) and not any(
            "WARNUNG Slicer" in m for m in g.infos), g.infos
        assert reads == [] and mod.get_status(0)["flush_print_tnn"] == "T1A"
        printer.gcode.scripts[:] = []
        # T0 (T1A, weiß PLA) im Druck gespült -> T1 (T1B, violett PLA): [0][1]
        g = FakeGcmd({"TNN": "T1B", "TOOL": "1"})
        mod.cmd_flush(g)
        pl = _plan_line(g)
        assert "Slicer-Matrix [0][1] = 569 mm³ / 2,4 × 1.3" in pl and \
            "308.208 mm" in pl, pl
        assert mod.remain_material["tnn"] == "T1B"
        ext = sum(float(l.split()[1][1:]) for l in printer.gcode.scripts
                  if l.startswith("G1 E"))
        assert abs(ext - 308.208) < 0.01, ext
        # zurück: [1][0] = 419 (Richtung zählt)
        g = FakeGcmd({"TNN": "T1A", "TOOL": "0"})
        mod.cmd_flush(g)
        assert "[1][0] = 419" in _plan_line(g) and "226.958 mm" in \
            _plan_line(g), _plan_line(g)
        assert len(reads) == 1, "Cache (Pfad/Größe/mtime)"
        # ohne TOOL: Index aus TNN (feste Zuordnung), anderes Material ×2
        g = FakeGcmd({"TNN": "T1C"})
        mod.cmd_flush(g)
        want = max(2 * (300 + 0 + 6) / 2.4 * 1.3, 370)
        assert "[0][2] = 306" in _plan_line(g) and "anderes Material" in \
            _plan_line(g) and ("%s mm" % K._n(want)) in _plan_line(g), \
            _plan_line(g)
        # Reset-Ereignis (neuer Druck/Druckende) -> altes Werkzeug im Druck
        # unbekannt: Farbweg ohne Lesen, danach neu lesen
        printer.fire("virtual_sdcard:reset_file")
        assert mod.get_status(0)["flush_print_tnn"] == ""
        g = FakeGcmd({"TNN": "T1A", "TOOL": "0"})
        mod.cmd_flush(g)
        assert "Slicer-Matrix [" not in _plan_line(g) and len(reads) == 1
        mod.cmd_flush(FakeGcmd({"TNN": "T1B", "TOOL": "1"}))
        assert len(reads) == 2, reads
        with open(gf, "a", encoding="utf-8") as fh:
            fh.write("; Nachtrag\n")
        mod.cmd_flush(FakeGcmd({"TNN": "T1A", "TOOL": "0"}))
        assert len(reads) == 3, reads
    finally:
        K.read_flush_matrix = o_read

    def fallback(setup, params, text, warn=True):
        bus, printer, mod, ctl = _matrix_hull(gf)
        mod.remain_material = {"type": "190001", "color": "0FFFFFF",
                               "tnn": "T1A"}
        mod._print_flush_tnn = "T1A"      # in diesem Druck gespült (E5)
        setup(printer, mod)
        was_paused = printer.paused
        g = FakeGcmd(params)
        mod.cmd_flush(g)
        pl = _plan_line(g)
        assert "Slicer-Matrix [" not in pl and "Farbe 0FFFFFF -> 0" in \
            pl, (text, pl)
        hits = [m for m in g.infos if text in m]
        assert hits, (text, g.infos)
        assert ("WARNUNG" in hits[0]) == warn, hits
        assert mod.chain_error == "" and printer.paused == was_paused
        return g

    ok = lambda pr, mod: None
    # nur im Druck: standby / paused -> Hinweis (keine Warnung) + color
    fallback(lambda pr, m: pr.set_printing(False), {"TNN": "T1B", "TOOL": "1"},
             "nur im laufenden Druck (Zustand standby)", warn=False)

    def paused(pr, m):
        pr.paused = True
        pr.print_state = "paused"
        pr._refresh()
    fallback(paused, {"TNN": "T1B", "TOOL": "1"},
             "nur im laufenden Druck (Zustand paused)", warn=False)
    # altes Werkzeug unbekannt (Düsen-Material ohne Slot, z. B. von Hand
    # per KTWO_BOX_REMAIN) – GEÄNDERT 18.10.2026 (E5): jetzt Hinweis statt
    # WARNUNG (Düsen-Material ≠ im Druck gespültes Werkzeug)
    fallback(lambda pr, m: m.remain_material.update(tnn=""),
             {"TNN": "T1B", "TOOL": "1"},
             "ohne Slot ≠ im Druck gespült T1A", warn=False)
    # E5/A4: im Druck noch nicht gespült / neuer Druck -> Hinweis + Farbweg
    fallback(lambda pr, m: m._print_flush_reset(),
             {"TNN": "T1B", "TOOL": "1"}, "erster Einzug im Druck", warn=False)
    fallback(lambda pr, m: pr.fire("virtual_sdcard:reset_file"),
             {"TNN": "T1B", "TOOL": "1"}, "erster Einzug im Druck", warn=False)
    # TOOL passt nicht zu TNN
    fallback(ok, {"TNN": "T1B", "TOOL": "2"}, "passt nicht zu TNN")
    # keine Datei / keine Matrix / nicht quadratisch / Index >= n
    fallback(lambda pr, m: pr.objects.pop("virtual_sdcard"),
             {"TNN": "T1B", "TOOL": "1"}, "keine laufende Druckdatei")
    fallback(lambda pr, m: setattr(pr.objects["virtual_sdcard"], "path",
                                   _gcode_file(d, "o.gcode", None)),
             {"TNN": "T1B", "TOOL": "1"}, "keine '; flush_volumes_matrix")
    fallback(lambda pr, m: setattr(pr.objects["virtual_sdcard"], "path",
                                   _gcode_file(d, "q.gcode", [1] * 15)),
             {"TNN": "T1B", "TOOL": "1"}, "nicht quadratisch")
    fallback(lambda pr, m: setattr(pr.objects["virtual_sdcard"], "path",
                                   _gcode_file(d, "z.gcode", [0, 5, 5, 0])),
             {"TNN": "T1C", "TOOL": "2"}, "außerhalb der 2x2-Matrix")
    fallback(lambda pr, m: setattr(pr.objects["virtual_sdcard"], "path",
                                   _gcode_file(d, "cp.gcode", [0])),
             {"TNN": "T1B", "TOOL": "1"}, "außerhalb der 1x1-Matrix")
    # LENGTH= hat Vorrang (kein Matrix-Versuch), flush_mode color ignoriert
    # die Matrix
    bus, printer, mod, ctl = _matrix_hull(gf)
    mod.remain_material = {"type": "190001", "color": "0FFFFFF", "tnn": "T1A"}
    g = FakeGcmd({"TNN": "T1B", "TOOL": "1", "LENGTH": "95"})
    mod.cmd_flush(g)
    assert ": 95 mm (95;" in _plan_line(g) and not any(
        "Slicer-Matrix" in m for m in g.infos), g.infos
    bus, printer, mod, ctl = _matrix_hull(gf, flush_mode="color")
    mod.remain_material = {"type": "190001", "color": "0FFFFFF", "tnn": "T1A"}
    g = FakeGcmd({"TNN": "T1B", "TOOL": "1"})
    mod.cmd_flush(g)
    assert not any("Slicer-Matrix" in m for m in g.infos), g.infos
    # Kette über die Hülle (wie _KTWO_TOOL_CHANGE T=1: FLUSH TNN=T1B TOOL=1)
    bus, printer, mod, ctl = _matrix_hull(gf)
    mod.remain_material = {"type": "190001", "color": "0FFFFFF", "tnn": "T1A"}
    mod._print_flush_tnn = "T1A"          # T1A in diesem Druck gespült (E5)
    ctl.loaded_slot = "A"
    _prep_chain(bus)
    infos = []
    for fn, prm in ((mod.cmd_pre_opt, {"TNN": "T1B", "SOURCE": "T"}),
                    (mod.cmd_cut, {}), (mod.cmd_retrude, {}),
                    (mod.cmd_extrude, {"TNN": "T1B"}), (mod.cmd_waste, {}),
                    (mod.cmd_flush, {"TNN": "T1B", "TOOL": "1"}),
                    (mod.cmd_end_opt, {"RETURN": "1"})):
        g = FakeGcmd(prm)
        fn(g)
        infos.extend(g.infos)
    assert any("Slicer-Matrix [0][1] = 569" in m for m in infos), infos
    assert mod.chain_error == "" and not printer.paused, mod.chain_error
    return ("Hülle: [0][1]=569 -> 308,208 mm (TOOL=1), [1][0]=419 -> 226,958, "
            "TNN-Zuordnung ohne TOOL, anderes Material ×2, Cache + reset_file/"
            "mtime; 1. Einzug im Druck = Farbweg (E5); Rückfälle standby/"
            "paused/alt unbekannt/TOOL≠TNN/keine "
            "Datei/keine Matrix/nicht quadratisch/Index ≥ n -> color; LENGTH=; "
            "Kette T1A->T1B")


def test_matrix_erster_einzug_live():
    """E4/E5 (Live-Abgleich 18.10.2026): Live-Werte Druck 3 –
    Matrix 0,82,73,0 × 0.3, Düse 01B04AE (Vordruck, T1A) -> Erst-Einzug
    T1B 0CE58F8 = Farbweg 368 mm³ + 183 -> 229,58 [90, 70, 70]; im Druck
    B->A [1][0]=73 und A->B [0][1]=82 -> 9,1/10,3 -> min 160 [90, 70]."""
    d = _rm_dir()
    gf = _gcode_file(d, "live3.gcode", [0, 82, 73, 0], mult="0.3")
    # Default = matrix (E4): _matrix_hull setzt flush_mode nicht selbst
    bus, printer, mod, ctl = _matrix_hull(gf, flush_mode=None)
    assert mod.cfg["flush_mode"] == "matrix", mod.cfg["flush_mode"]
    mod.slot_manual["T1A"] = {"type": "190001", "color": "01B04AE"}
    mod.slot_manual["T1B"] = {"type": "190001", "color": "0CE58F8"}

    def flush(tnn, tool):
        printer.gcode.scripts[:] = []
        g = FakeGcmd({"TNN": tnn, "TOOL": str(tool)})
        mod.cmd_flush(g)
        ext = [float(l.split()[1][1:]) for l in printer.gcode.scripts
               if l.startswith("G1 E")]
        return g, _plan_line(g), ext

    def segs(pl):
        return pl.split("(", 1)[1].split(";", 1)[0]

    printer.objects["print_stats"].print_start_time = 1000.0
    for rnd in (1, 2):
        # Düsen-Material aus dem Vordruck (Slot A bekannt!)
        mod.remain_material = {"type": "190001", "color": "01B04AE",
                               "tnn": "T1A"}
        g, pl, ext = flush("T1B", 1)
        assert "Slicer-Matrix [" not in pl and "Farbe 01B04AE -> 0CE58F8: " \
            "Volumen 368 mm³ + Düse 183 -> 229.583 mm" in pl and \
            segs(pl) == "90 + 70 + 70", (rnd, pl)
        assert abs(sum(ext) - 230.0) < 0.01, ext  # cal_flush_list
        assert any("erster Einzug im Druck" in m for m in g.infos), g.infos
        for _i in range(2):
            g, pl, ext = flush("T1A", 0)
            assert "Slicer-Matrix [1][0] = 73 mm³ / 2,4 × 0.3" in pl and \
                "9.125 mm (min 160) -> 160 mm" in pl and \
                segs(pl) == "90 + 70", pl
            g, pl, ext = flush("T1B", 1)
            assert "Slicer-Matrix [0][1] = 82 mm³ / 2,4 × 0.3" in pl and \
                "10.25 mm (min 160) -> 160 mm" in pl and \
                segs(pl) == "90 + 70", pl
            assert abs(sum(ext) - 160.0) < 0.01, ext
        # neuer Druck, 2. Runde über die print_stats-Kennung (ohne Ereignis)
        printer.objects["print_stats"].print_start_time = 2000.0
    # KTWO_BOX_PRINT_START setzt ebenfalls zurück
    assert mod._print_flush_tnn == "T1B"
    mod._print_state = lambda gcmd, on: None
    mod.cmd_print_start(FakeGcmd({}))
    assert mod._print_flush_tnn == ""
    g, pl, ext = flush("T1A", 0)
    assert "Slicer-Matrix [" not in pl, pl
    # KTWO_BOX_CANCEL_RESET START=1 (START_PRINT ohne Freigabe) auch
    mod._print_flush_tnn = "T1A"
    mod.cmd_cancel_reset(FakeGcmd({"START": "1"}))
    assert mod._print_flush_tnn == ""
    # Gegenprobe E4: flush_mode color hätte A->B im Druck 229,58 gespült
    bus, printer, mod, ctl = _matrix_hull(gf, flush_mode="color")
    mod.slot_manual["T1B"] = {"type": "190001", "color": "0CE58F8"}
    mod.remain_material = {"type": "190001", "color": "01B04AE", "tnn": "T1A"}
    mod._print_flush_tnn = "T1A"
    g = FakeGcmd({"TNN": "T1B", "TOOL": "1"})
    mod.cmd_flush(g)
    assert "229.583 mm" in _plan_line(g), _plan_line(g)
    # außerhalb des Drucks gespült -> zählt nicht als Wechsel im Druck
    bus, printer, mod, ctl = _matrix_hull(gf, flush_mode=None)
    printer.set_printing(False)
    mod.remain_material = {"type": "190001", "color": "01B04AE", "tnn": "T1A"}
    mod.cmd_flush(FakeGcmd({"TNN": "T1B", "TOOL": "1"}))
    assert mod._print_flush_tnn == "" and \
        mod.remain_material["tnn"] == "T1B"
    return ("Default matrix; 1. Einzug 01B04AE->0CE58F8 Farbweg 229,583 "
            "-> [90, 70, 70] (2 Drucke, print_start_time-Wechsel), B->A "
            "[1][0]=73 / A->B [0][1]=82 ×0,3 -> 160 [90, 70]; Reset per "
            "PRINT_START/CANCEL_RESET START=1; color-Gegenprobe 229,583; "
            "Spülen außerhalb Druck zählt nicht")


def test_makro_m600():
    try:
        import jinja2
    except Exception:
        return "SKIP (jinja2 fehlt)"
    path = os.path.join(_HERE, "config-pi", "macros.cfg")
    macros = _macro_sources(path)
    assert "M600" in macros, "Makro M600 fehlt"
    env = jinja2.Environment("{%", "%}", "{", "}")

    def render(state, paused=False):
        msgs = []
        out = env.from_string(macros["M600"][0]).render(
            params={}, rawparams="",
            printer={"print_stats": {"state": state},
                     "pause_resume": {"is_paused": paused}},
            action_respond_info=lambda m: msgs.append(m) or "",
            action_raise_error=lambda m: (_ for _ in ()).throw(ValueError(m)))
        return [l.strip() for l in out.split("\n")
                if l.strip() and not l.strip().startswith("#")], msgs

    lines, msgs = render("printing")
    assert lines == ["PAUSE"] and "Filamentwechsel" in msgs[0], (lines, msgs)
    for st, pa in (("standby", False), ("paused", True), ("printing", True),
                   ("complete", False)):
        lines, msgs = render(st, pa)
        assert lines == [] and "ignoriert" in msgs[0], (st, lines, msgs)
    # kein Konflikt: genau ein [gcode_macro M600], kein rename_existing,
    # Upstream kennt kein M600 (Referenz-Klon, falls vorhanden)
    txt = open(path, encoding="utf-8").read()
    assert txt.count("[gcode_macro M600]") == 1
    sec = txt.split("[gcode_macro M600]", 1)[1].split("\n[", 1)[0]
    assert "rename_existing" not in sec
    up = os.path.join(_REPO, "lokal", "upstream-klipper",
                      "klippy")
    if os.path.isdir(up):
        for root, _dirs, files in os.walk(up):
            for fn in files:
                if fn.endswith(".py"):
                    with open(os.path.join(root, fn), encoding="utf-8",
                              errors="replace") as fh:
                        assert "M600" not in fh.read(), fn
        return "M600 im Druck -> PAUSE, sonst Meldung; Upstream ohne M600 (geprüft)"
    return "M600 im Druck -> PAUSE, sonst Meldung"


# =====================================================================
# Runner
# =====================================================================

# =====================================================================
# Restmaterial + Druckende (17.10.2026, README-k2_box.md §12.5)
# =====================================================================

import re as _re_end  # noqa: E402

_PIECE_RE = _re_end.compile(r"^G1 E(\d+(?:\.\d+)?) F")


def _ending_hull(empty_after=3, hub=0x00, printing=True, **opt):
    """Druck läuft mit T1A (weiß PLA vol 23 in der Düse), Spule A im CFS
    leer: Box PRINT + 0x50, Hub leer, Kopf voll. Der Kopfsensor meldet nach
    ``empty_after`` Ausspül-Stücken LEER (None = nie). Mit fan0 + arcs."""
    o = dict(allow_write="True")
    o.update(opt)
    db = o.pop("db", True)
    bus, printer, mod = _material_hull(db=db, **o)
    printer.objects["output_pin fan0"] = _PinObj(0.4, 255.0)
    printer.objects["gcode_arcs"] = object()
    ctl = mod._ensure_controller()
    ctl.get_rfid("A")
    ctl.loaded_slot = "A"
    mod.remain_material = {"type": "190001", "color": "0FFFFFF", "tnn": "T1A"}
    bus.slots["A"]["filament"] = False
    bus.state, bus.print_frame_status = 2, 0x50
    bus.hub_mask, bus.head = hub, True
    bus.head_at_mm = 300.0
    bus.need_extruder_for_mode7 = True
    if printing:
        printer.set_printing(True)
    printer.target = 210.0
    printer._refresh()
    pieces = []
    orig = printer.sim

    def sim(line):
        orig(line)
        m = _PIECE_RE.match(line)
        if m and line.split()[1] in ("E80", "E40", "E20"):
            pieces.append(float(m.group(1)))
            if empty_after is not None and len(pieces) == empty_after:
                bus.head = False
    printer.sim = sim
    bus.calls[:] = []
    bus.trace = []
    return bus, printer, mod, ctl, pieces


def test_ending_ausloeser():
    # 1) 0x50 im PRINT während des Drucks -> Merker + Meldung (Stock send_data)
    bus, printer, mod, ctl, _p = _ending_hull()
    assert mod.ending_material is None and not mod.get_status()["ending_material"]
    ctl.poll_status()
    em = mod.ending_material
    assert em and em["tnn"] == "T1A" and em["slot"] == "A", em
    st = mod.get_status()
    assert st["ending_material"] and st["ending_material_tnn"] == "T1A" \
        and st["ending_material_purge"] and st["end_print_unload"], st  # A5: Default an
    assert any("Restmaterial: Spule T1A im CFS leer" in m
               for m in printer.gcode.infos), printer.gcode.infos[-3:]
    # zweiter Poll: keine zweite Meldung (Flag schon gesetzt)
    n = len(printer.gcode.infos)
    ctl.poll_status()
    assert len(printer.gcode.infos) == n
    # 2) gespeicherter Fehler 0x50 im ERROR -> ebenso; 0x51/0x52 nicht
    for state, err, want in ((4, 0x50, True), (4, 0x51, False),
                             (4, 0x52, False), (0, 0x00, False)):
        bus, printer, mod, ctl, _p = _ending_hull()
        bus.state, bus.stored_error = state, err
        ctl.poll_status()
        assert bool(mod.ending_material) is want, (state, err)
    # 3) PRINT ohne 0x50 -> nichts; 0x50 ohne Druck -> nichts (Stock nur
    #    printing/paused); in der Pause -> ja
    bus, printer, mod, ctl, _p = _ending_hull()
    bus.print_frame_status = 0x00
    ctl.poll_status()
    assert mod.ending_material is None
    bus, printer, mod, ctl, _p = _ending_hull(printing=False)
    ctl.poll_status()
    assert mod.ending_material is None
    printer.gcode.run_script_from_command("PAUSE")
    ctl.poll_status()
    assert mod.ending_material and mod.ending_material["tnn"] == "T1A"
    # 4) Poll-Timer (handle_motor_ready) und KTWO_BOX_STATUS werten aus
    bus, printer, mod, ctl, _p = _ending_hull()
    mod.cmd_status(FakeGcmd({}))
    assert mod.ending_material is not None
    # 5) KTWO_BOX_ENDING: Anzeige / CLEAR / SET SLOT
    g = FakeGcmd({})
    mod.cmd_ending(g)
    assert any("Fall offen T1A" in m for m in g.infos), g.infos
    mod.cmd_ending(FakeGcmd({"CLEAR": "1"}))
    assert mod.ending_material is None and "erledigt" in mod.ending_last
    mod.cmd_ending(FakeGcmd({"SET": "1", "SLOT": "C"}))
    assert mod.ending_material["tnn"] == "T1C"
    try:
        mod.cmd_ending(FakeGcmd({"SET": "1", "SLOT": "X"}))
    except ValueError as exc:
        assert "KTWO_BOX_ENDING" in str(exc)
    else:
        raise AssertionError("SLOT=X hätte abgelehnt werden müssen")
    # 5b) PRE_OPT pollt frisch (0x50 seit dem letzten Timer-Poll) und spült;
    #     Polls WÄHREND einer Kette setzen keinen Merker (altes 0x50)
    bus, printer, mod, ctl, pieces = _ending_hull(empty_after=1)
    assert mod.ending_material is None
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1B", "SOURCE": "T"}))
    assert pieces == [80.0, 80.0] and mod.chain["ending_purged"], pieces
    bus.state, bus.print_frame_status = 2, 0x50
    ctl.poll_status()
    assert mod.ending_material is None, "Kette aktiv -> kein Merker"
    mod.cmd_end_opt(FakeGcmd({"RETURN": "0"}))
    ctl.poll_status()
    assert mod.ending_material is not None, "nach der Kette wieder"
    # 6) Runout-Nachladen löscht den Merker (Rest am Kopfsensor vorbei) und
    #    spült NICHT (Stock check_material_refill)
    bus, printer, mod = build_runout()
    mod._ensure_controller().poll_status()   # 0x50 im Druck/Pause -> Merker
    assert mod.ending_material is not None
    n0 = len(printer.gcode.scripts)
    mod.cmd_runout(FakeGcmd({}))
    sc = printer.gcode.scripts[n0:]
    assert mod.ending_material is None and "Runout-Nachladen" in mod.ending_last
    assert not [l for l in sc if l.startswith("G1 E80")], "kein Ausspülen"
    assert mod.get_status()["loaded_tnn"] == "T1C" and not printer.paused
    return ("0x50 im PRINT/ERROR nur im Druck/Pause -> Merker T1A + Meldung "
            "(einmal); 0x51/0x52/IDLE nicht; ENDING SET/CLEAR; Runout löscht, "
            "spült nicht")


def test_ending_stueckfolge():
    # Wechsel T1A (leer gelaufen) -> T1B: Kopfsensor leer nach 3 Stücken ->
    # 4 Stücke (Stock: + 1 Stück), dann kein Schnitt/Rückzug, Einzug B
    bus, printer, mod, ctl, pieces = _ending_hull(empty_after=3)
    ctl.poll_status()
    assert mod.ending_material
    n0 = len(printer.gcode.scripts)
    infos = run_chain(mod, "T1B")
    sc = printer.gcode.scripts[n0:]
    assert pieces == [80.0] * 4, pieces
    ext = [l for l in sc if l.startswith("G1 E80")]
    assert ext == ["G1 E80 F575"] * 4, ext        # 23/2,4·60, ohne ×0,8/360
    i_off = sc.index("SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=0")
    i_heat = sc.index("M109 S220")
    i_first = sc.index("G1 E80 F575")
    assert i_off < i_heat < i_first, (i_off, i_heat, i_first)
    last_piece = max(i for i, l in enumerate(sc) if l.startswith("G1 E80"))
    seg = sc[i_first:last_piece + 40]
    seg = seg[:next(i for i, l in enumerate(seg) if l.startswith("G1 E30"))] \
        if any(l.startswith("G1 E30") for l in seg) else seg
    # je Stück Blasen + Blech, KEINE Kreise, Schacht nicht verlassen
    assert seg.count("SET_PIN PIN=fan0 VALUE=255") == 4, seg
    assert seg.count("G4 P5000") == 4
    assert seg.count("G0 X170 F12000") == 4 and seg.count("G0 X135 F12000") == 4
    assert not [l for l in seg if l.startswith(("G2", "G3"))], "keine Kreise"
    assert "G0 Y345 F12000" not in seg[:seg.index("G0 X135 F12000") + 10]
    assert "M104 S140" not in sc, "in der Kette kein Abkühlen"
    # kein Schnitt, kein Rückzug, Box IDLE nach dem Ausspülen, dann Einzug B
    assert "G0 X-7.8 Y200 F30000" not in sc, "kein Schnitt"
    assert not [c for c in bus.calls if c[1] == 0x11], "kein Rückzug"
    t = bus.trace
    i_idle = _idx(t, _b(0x04, (0x00, 0x01)), _idx(t, lambda e: e[0] == "gcode"
                                                  and e[1] == "G1 E80 F575"))
    _idx(t, _b(0x10, (0x02, 0x00, 0x00)), i_idle)        # Einzug B danach
    assert any("CUT übersprungen (Restmaterial" in m for m in infos), infos
    assert any("RETRUDE übersprungen (Restmaterial" in m for m in infos)
    st = mod.get_status()
    assert not st["ending_material"] and "320 mm in 4 Stücken" in st["ending_last"], st
    assert st["loaded_tnn"] == "T1B" and st["chain_error"] == "" \
        and not st["chain_active"] and not printer.paused, st
    # remain_material: Ausspülen speichert nichts, erst FLUSH B (Stock)
    assert mod.remain_material["tnn"] == "T1B"
    assert printer.objects["output_pin fan0"].value == 0.4
    # Kopf schon leer beim Start -> 2 Stücke (Stock: Sensor nach Stück 1)
    bus, printer, mod, ctl, pieces = _ending_hull(empty_after=None)
    bus.head = False
    mod.cmd_ending(FakeGcmd({"SET": "1", "SLOT": "A"}))
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1B", "SOURCE": "T"}))
    assert pieces == [80.0, 80.0], pieces
    assert mod.chain["ending_purged"] and mod.ending_material is None
    mod.cmd_end_opt(FakeGcmd({"RETURN": "1"}))
    return ("Kopf leer nach Stück 3 -> 4×E80 F575, je Blasen + Blech ohne "
            "Kreise, kein Schnitt/Rückzug, 0x04 IDLE, Einzug B; Kopf leer "
            "von Anfang an -> 2 Stücke")


def test_ending_geschwindigkeit():
    def first_piece(**kw):
        bus, printer, mod, ctl, pieces = _ending_hull(empty_after=1, **kw)
        rm = kw.get("_remain")
        if rm:
            mod.remain_material = rm
        mod.cmd_ending(FakeGcmd({"SET": "1", "SLOT": "A"}))
        n0 = len(printer.gcode.scripts)
        mod.cmd_pre_opt(FakeGcmd({"TNN": "T1B"}))
        sc = printer.gcode.scripts[n0:]
        mod.cmd_end_opt(FakeGcmd({"RETURN": "0"}))
        return [l for l in sc if l.startswith("G1 E80")], sc
    ext, sc = first_piece()
    assert ext == ["G1 E80 F575"] * 2, ext          # PLA vol 23
    # PLA vol 14 -> 350 (Flush wäre min(360, 350)·0,8 nicht; hier ohne ×0,8)
    bus, printer, mod, ctl, pieces = _ending_hull(empty_after=1)
    mod.remain_material = {"type": "190002", "color": "0CE58F8", "tnn": "T1B"}
    mod.cmd_ending(FakeGcmd({"SET": "1"}))
    n0 = len(printer.gcode.scripts)
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1C"}))
    assert [l for l in printer.gcode.scripts[n0:] if l.startswith("G1 E80")] \
        == ["G1 E80 F350"] * 2
    mod.cmd_end_opt(FakeGcmd({}))
    # ohne Material-DB: bisherige Spülgeschwindigkeit Tn_extrude_velocity 360
    bus, printer, mod, ctl, pieces = _ending_hull(empty_after=1, db=False)
    mod.cmd_ending(FakeGcmd({"SET": "1"}))
    n0 = len(printer.gcode.scripts)
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1B"}))
    sc = printer.gcode.scripts[n0:]
    assert [l for l in sc if l.startswith("G1 E80")] == ["G1 E80 F360"] * 2, sc
    assert "M109 S220" in sc, "ohne DB: max(Ziel 210, Tn_extrude_temp 220)"
    mod.cmd_end_opt(FakeGcmd({}))
    # DB an, Material ohne max_vol (TPU-Eintrag hat vol 2 -> 50) bzw. nicht
    # in der DB -> Tn_extrude_velocity
    bus, printer, mod, ctl, pieces = _ending_hull(empty_after=1)
    mod.remain_material = {"type": "199999", "color": None, "tnn": "T1A"}
    mod.cmd_ending(FakeGcmd({"SET": "1"}))
    n0 = len(printer.gcode.scripts)
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1B"}))
    assert [l for l in printer.gcode.scripts[n0:] if l.startswith("G1 E80")] \
        == ["G1 E80 F360"] * 2
    mod.cmd_end_opt(FakeGcmd({}))
    bus, printer, mod, ctl, pieces = _ending_hull(empty_after=1)
    mod.remain_material = {"type": "190004", "color": None, "tnn": "T1D"}
    mod.cmd_ending(FakeGcmd({"SET": "1"}))
    n0 = len(printer.gcode.scripts)
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1B"}))
    assert [l for l in printer.gcode.scripts[n0:] if l.startswith("G1 E80")] \
        == ["G1 E80 F50"] * 2
    mod.cmd_end_opt(FakeGcmd({}))
    # Pause: Temperatur aus _PI_PARAM.hotend_temp (Stock PRINTER_PARAM)
    bus, printer, mod, ctl, pieces = _ending_hull(empty_after=1)
    printer.objects["gcode_macro _PI_PARAM"] = _Obj({"hotend_temp": 235.0})
    printer.gcode.run_script_from_command("PAUSE")
    printer.target = 140.0
    printer._refresh()
    mod.cmd_ending(FakeGcmd({"SET": "1"}))
    n0 = len(printer.gcode.scripts)
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1B"}))
    assert "M109 S235" in printer.gcode.scripts[n0:]
    return "PLA23 575, PLA14 350, ohne DB/unbekannt 360, TPU 50, Pause 235 °C"


def test_ending_grenzen():
    # 1) Obergrenze: Kopf bleibt voll -> 80+80+40 = 200 mm, nie darüber,
    #    dann Fehler -> PAUSE (Druck), Merker bleibt, RESUME gesperrt
    bus, printer, mod, ctl, pieces = _ending_hull(empty_after=None,
                                                  max_tube_length="200")
    ctl.poll_status()
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1B", "SOURCE": "T"}))
    assert pieces == [80.0, 80.0, 40.0], pieces
    assert printer.paused and mod.ending_material is not None
    st = mod.get_status()
    assert "Obergrenze max_tube_length 200 mm" in st["chain_error"], st
    assert any(m.startswith("!!") and "PAUSIERT" in m for m in printer.gcode.raw)
    assert not [c for c in bus.calls if c[1] == 0x04], "kein IDLE nach Fehler"
    infos = []
    for fn, p in ((mod.cmd_cut, {}), (mod.cmd_retrude, {}),
                  (mod.cmd_end_opt, {"RETURN": "1"})):
        g = FakeGcmd(p)
        fn(g)
        infos += g.infos
    assert "G0 X-7.8 Y200 F30000" not in printer.gcode.scripts
    # Obergrenze, Kopf genau beim letzten Stück leer -> ok + WARNUNG
    bus, printer, mod, ctl, pieces = _ending_hull(empty_after=3,
                                                  max_tube_length="200")
    mod.cmd_ending(FakeGcmd({"SET": "1"}))
    g = FakeGcmd({"TNN": "T1B"})
    mod.cmd_pre_opt(g)
    assert pieces == [80.0, 80.0, 40.0] and mod.ending_material is None
    assert any("erst an der Obergrenze 200 mm leer" in m for m in g.infos), g.infos
    mod.cmd_end_opt(FakeGcmd({}))
    # Default-Obergrenze 3000: 37×80 + 40 (Stock wäre 38×80 = 3040)
    bus, printer, mod, ctl, pieces = _ending_hull(empty_after=None)
    mod.cmd_ending(FakeGcmd({"SET": "1"}))
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1B"}))
    assert len(pieces) == 38 and sum(pieces) == 3000.0 and pieces[-1] == 40.0
    # 2) Kopfsensor unbekannt -> NICHT fördern, PAUSE + Meldung
    bus, printer, mod, ctl, pieces = _ending_hull()
    del printer.objects["filament_switch_sensor filament_sensor"]
    mod.cmd_ending(FakeGcmd({"SET": "1"}))
    n0 = len(printer.gcode.scripts)
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1B", "SOURCE": "T"}))
    sc = printer.gcode.scripts[n0:]
    assert not [l for l in sc if l.startswith(("G1 E", "M109"))], sc
    assert printer.paused and mod.ending_material is not None
    assert "NICHT blind" in mod.get_status()["chain_error"]
    assert mod.get_status()["change_aborted"]
    # 3) Kopfsensor fällt unterwegs aus -> Abbruch nach dem Stück
    bus, printer, mod, ctl, pieces = _ending_hull(empty_after=None)
    mod.cmd_ending(FakeGcmd({"SET": "1"}))
    orig = printer.sim
    sens = printer.objects["filament_switch_sensor filament_sensor"]

    def sim(line):
        orig(line)
        if len(pieces) == 2 and "filament_switch_sensor filament_sensor" \
                in printer.objects:
            del printer.objects["filament_switch_sensor filament_sensor"]
    printer.sim = sim
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1B"}))
    assert pieces == [80.0, 80.0] and printer.paused, pieces
    assert "keinen Zustand mehr" in mod.get_status()["chain_error"]
    printer.objects["filament_switch_sensor filament_sensor"] = sens
    # 4) Zeitlimit -> Abbruch + PAUSE
    bus, printer, mod, ctl, pieces = _ending_hull(empty_after=None,
                                                  ending_material_timeout="100")
    clock = {"t": 0.0}

    def now():
        clock["t"] += 30.0
        return clock["t"]
    mod._now = now
    mod.cmd_ending(FakeGcmd({"SET": "1"}))
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1B"}))
    assert 1 <= len(pieces) < 10 and printer.paused, pieces
    assert "Zeitlimit 100 s" in mod.get_status()["chain_error"]
    # 5) ohne Freigaben (kein Druck): nur PLAN, Merker bleibt
    bus, printer, mod, ctl, pieces = _ending_hull(allow_move="False",
                                                  printing=False)
    mod.cmd_ending(FakeGcmd({"SET": "1"}))
    g = FakeGcmd({"TNN": "T1B"})
    mod.cmd_pre_opt(g)
    assert pieces == [] and mod.ending_material is not None
    assert any("PLAN Restmaterial T1A ausspülen" in m for m in g.infos), g.infos
    assert not [c for c in bus.calls if c[1] == 0x04]
    mod.cmd_end_opt(FakeGcmd({}))
    #    im Druck ohne allow_move -> PAUSE wie jeder Wechsel (nichts gefördert)
    bus, printer, mod, ctl, pieces = _ending_hull(allow_move="False")
    mod.cmd_ending(FakeGcmd({"SET": "1"}))
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1B"}))
    assert pieces == [] and printer.paused
    # 6) ending_material_purge False -> Schnitt + Rückzug wie bisher
    bus, printer, mod, ctl, pieces = _ending_hull(ending_material_purge="False",
                                                  hub=0x01)
    ctl.poll_status()
    assert mod.ending_material is not None
    infos = run_chain(mod, "T1B")
    assert pieces == [] and "G0 X-7.8 Y200 F30000" in printer.gcode.scripts
    assert [c for c in bus.calls if c[1] == 0x11], "Rückzug wie bisher"
    assert any("ending_material_purge: False" in m for m in infos)
    assert mod.ending_material is None
    # 7) RETRUDE ohne Kette bei offenem Fall -> WARNUNG
    bus, printer, mod, ctl, pieces = _ending_hull(printing=False, hub=0x01)
    mod.cmd_ending(FakeGcmd({"SET": "1"}))
    g = FakeGcmd({})
    mod.cmd_retrude(g)
    assert any("WARNUNG Restmaterial-Fall offen" in m for m in g.infos), g.infos
    return ("Obergrenze 200 = 80+80+40 -> Pause, beim letzten leer = ok+WARNUNG, "
            "3000 = 37×80+40; Sensor fehlt/fällt aus -> Pause ohne Förderung; "
            "Zeitlimit; Plan ohne Freigaben; Option aus = Schnitt+Rückzug")


def test_ending_hand_purge():
    # KTWO_BOX_ENDING PURGE=1 außerhalb eines Drucks (Stock
    # BOX_EXTRUSION_ALL_MATERIALS): Sensor aus/zurück, M104 S140, IDLE
    bus, printer, mod, ctl, pieces = _ending_hull(empty_after=2, printing=False)
    n0 = len(printer.gcode.scripts)
    g = FakeGcmd({"PURGE": "1"})
    mod.cmd_ending(g)
    sc = printer.gcode.scripts[n0:]
    assert pieces == [80.0] * 3, pieces
    assert sc[0] == "SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=0", sc[:3]
    i_cool = sc.index("M104 S140")
    assert i_cool > max(i for i, l in enumerate(sc) if l.startswith("G1 E80"))
    assert "SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=1" in sc[i_cool - 3:]
    assert [c for c in bus.calls if c[1] == 0x04 and c[2] == (0x00, 0x01)]
    assert ctl.loaded_slot is None
    # im laufenden Druck abgelehnt
    bus, printer, mod, ctl, pieces = _ending_hull()
    try:
        mod.cmd_ending(FakeGcmd({"PURGE": "1"}))
    except ValueError as exc:
        assert "nur außerhalb eines Drucks" in str(exc)
    else:
        raise AssertionError("PURGE im Druck hätte abgelehnt werden müssen")
    assert pieces == []
    return "PURGE=1: 3 Stücke, Sensor aus/an, M104 S140, IDLE; im Druck abgelehnt"


def test_end_print_unload():
    # 1) Option aus (seit 18.10. ausdrücklich, Default an – A5), kein
    #    Restmaterial -> nichts
    bus, printer, mod, ctl, pieces = _ending_hull(hub=0x02,
                                                  end_print_unload="False")
    ctl.loaded_slot = "B"
    n0 = len(printer.gcode.scripts)
    g = FakeGcmd({})
    mod.cmd_end_print(g)
    assert printer.gcode.scripts[n0:] == [] and bus.calls == []
    assert any("nichts zu tun" in m for m in g.infos), g.infos
    # 2) end_print_unload an: Schacht, Heizen, Schnitt, Rückzug in die Box,
    #    Schacht verlassen, KEIN Spülen, keine Pause, remain bleibt
    bus, printer, mod, ctl, pieces = _ending_hull(hub=0x02,
                                                  end_print_unload="True")
    ctl.loaded_slot = "B"
    bus.slots["A"]["filament"] = True
    bus.state, bus.print_frame_status = 2, 0x00
    rm = dict(mod.remain_material)
    n0 = len(printer.gcode.scripts)
    g = FakeGcmd({})
    mod.cmd_end_print(g)
    sc = printer.gcode.scripts[n0:]
    t = bus.trace
    assert sc[0] == "KTWO_BOX_PRE_OPT Z_HOP=0", sc[:2]
    i_heat = sc.index("M109 S220")
    i_cut = sc.index("G0 X-7.8 Y200 F30000")
    assert sc.index("G0 X133 F12000") < i_heat < i_cut
    p1 = _idx(t, _b(0x11, (0x02, 0x01)))
    # A3 (18.10.): Rückzug mit der Spülgeschwindigkeit des Düsenmaterials
    # (DB: PLA max_vol 23 -> min(360, 575) = 360) statt fest F600
    assert _idx(t, _g("G1 E-10 F360")) < p1
    assert "G1 E-10 F600" not in sc
    assert "KTWO_BOX_END_OPT RETURN=0" in sc and "G0 Y345 F12000" in sc
    assert not [l for l in sc if _PIECE_RE.match(l) and not l.startswith("G1 E-")
                and l != "G1 E-10 F360"], "kein Spülen/Ausspülen"
    assert not [l for l in sc if l.startswith("G1 E18")], "kein FLUSH"
    assert not printer.paused and not mod.get_status()["chain_active"]
    assert mod.get_status()["loaded_tnn"] == "" and mod.remain_material == rm
    assert any("geschnitten und in die Box zurückgezogen" in m for m in g.infos)
    assert "M104 S210" in sc, "END_OPT: Ziel vor dem Druckende zurück"
    # UNLOAD=0 überstimmt
    bus, printer, mod, ctl, pieces = _ending_hull(hub=0x02,
                                                  end_print_unload="True")
    n0 = len(printer.gcode.scripts)
    mod.cmd_end_print(FakeGcmd({"UNLOAD": "0"}))
    assert printer.gcode.scripts[n0:] == []
    # 3) Kopf leer -> nur Meldung, kein Schnitt
    bus, printer, mod, ctl, pieces = _ending_hull(hub=0x00,
                                                  end_print_unload="True")
    bus.print_frame_status = 0x00
    bus.head = False
    g = FakeGcmd({})
    mod.cmd_end_print(g)
    assert "G0 X-7.8 Y200 F30000" not in printer.gcode.scripts
    assert any("Kopf-Filamentsensor leer" in m for m in g.infos), g.infos
    # 4) nicht freigegeben -> übersprungen, nichts bewegt, keine Pause
    bus, printer, mod, ctl, pieces = _ending_hull(hub=0x02, allow_move="False",
                                                  end_print_unload="True")
    n0 = len(printer.gcode.scripts)
    g = FakeGcmd({})
    mod.cmd_end_print(g)
    assert printer.gcode.scripts[n0:] == [] and not printer.paused
    assert any("übersprungen – nicht freigegeben" in m for m in g.infos)
    # 5) Fehler beim Schnitt -> Meldung, KEINE Pause, kein G-Code-Fehler,
    #    Kette aufgeräumt (END_PRINT läuft weiter)
    bus, printer, mod, ctl, pieces = _ending_hull(hub=0x02,
                                                  end_print_unload="True")
    bus.print_frame_status = 0x00
    printer.fail_on = "G0 X-7.8"
    mod.cmd_end_print(FakeGcmd({}))
    assert not printer.paused and not mod.get_status()["chain_active"]
    assert mod.get_status()["chain_error"] == ""
    assert any(m.startswith("!!") and "END_PRINT fehlgeschlagen" in m
               and "läuft weiter" in m for m in printer.gcode.raw), printer.gcode.raw
    assert printer.objects["filament_switch_sensor filament_sensor"].enabled
    # 6) Restmaterial offen, end_print_unload AUS -> am Druckende ausspülen
    #    (Stock box_end Z.5111), kein Schnitt
    bus, printer, mod, ctl, pieces = _ending_hull(empty_after=2,
                                                  end_print_unload="False")
    ctl.poll_status()
    mod.cmd_end_print(FakeGcmd({}))
    assert pieces == [80.0] * 3 and mod.ending_material is None
    assert "G0 X-7.8 Y200 F30000" not in printer.gcode.scripts
    assert not printer.paused and not mod.get_status()["chain_active"]
    #    + Sensor unbekannt am Druckende -> Meldung statt Pause, Merker bleibt
    bus, printer, mod, ctl, pieces = _ending_hull()
    del printer.objects["filament_switch_sensor filament_sensor"]
    mod.cmd_ending(FakeGcmd({"SET": "1"}))
    mod.cmd_end_print(FakeGcmd({}))
    assert pieces == [] and not printer.paused and mod.ending_material
    assert any("NICHT blind" in m for m in printer.gcode.raw), printer.gcode.raw
    #    + Restmaterial und Unload an -> ausspülen, danach nichts zu entladen
    bus, printer, mod, ctl, pieces = _ending_hull(empty_after=1,
                                                  end_print_unload="True")
    mod.cmd_ending(FakeGcmd({"SET": "1"}))
    g = FakeGcmd({})
    mod.cmd_end_print(g)
    assert pieces == [80.0] * 2 and "G0 X-7.8 Y200 F30000" not in \
        printer.gcode.scripts
    assert any("kein Entladen nötig" in m for m in g.infos), g.infos
    return ("aus = nichts; an = Schacht/M109/Schnitt/E-10+p1/Schacht raus, "
            "kein Spülen, remain bleibt; UNLOAD=0; Kopf leer; ohne Freigabe; "
            "Fehler = Meldung ohne Pause; Restmaterial am Druckende ausgespült")


def test_ending_config_makro():
    c = K.parse_options(test_options())
    # A5 (18.10.2026): Stock entlädt am Druckende immer -> Default True
    assert c["ending_material_purge"] is True and c["end_print_unload"] is True
    assert c["max_tube_length"] == 3000 and isinstance(c["max_tube_length"], int)
    assert c["ending_material_timeout"] == 3600.0
    for bad in ({"max_tube_length": "50"}, {"max_tube_length": "20000"},
                {"max_tube_length": "3000.5"}, {"ending_material_timeout": "0"},
                {"ending_material_purge": "vielleicht"}):
        try:
            K.parse_options(test_options(**bad))
        except ValueError:
            pass
        else:
            raise AssertionError("Option %r hätte abgelehnt werden müssen" % bad)
    import configparser
    cp = configparser.RawConfigParser(strict=False,
                                      inline_comment_prefixes=("#",))
    cp.read_string(open(os.path.join(_HERE, "config-pi", "k2-module.cfg"),
                        encoding="utf-8").read())
    kb = K.parse_options(dict(cp.items("k2_box")))
    assert kb["ending_material_purge"] is True and kb["end_print_unload"] \
        is True and kb["max_tube_length"] == 3000 \
        and kb["ending_material_timeout"] == 3600.0, kb
    try:
        env, macros, kcfg, param = _render_env()
    except ImportError:
        return "Optionen ok; Makro SKIP (jinja2 fehlt)"

    def lines(**k2):
        pr = _rprinter(param, kcfg, k2_box=dict(
            {"end_print_unload": False, "ending_material": False}, **k2),
            pause_resume={"is_paused": False})
        out, _m = _render(env, macros, "END_PRINT", pr)
        return out
    assert "KTWO_BOX_END_PRINT" not in lines()
    out = lines(end_print_unload=True)
    assert "KTWO_BOX_END_PRINT" in out
    # END_PRINT läuft im Zustand printing -> TURN_OFF_HEATERS FORCE=1
    # (Bedienschutz 23.10.2026, macros.cfg)
    assert out.index("KTWO_BOX_END_PRINT") < out.index("TURN_OFF_HEATERS FORCE=1"), out
    assert "KTWO_BOX_END_PRINT" in lines(ending_material=True)
    assert "KTWO_BOX_END_PRINT" not in lines(end_print_unload=True,
                                             change_enabled=False)
    pr = _rprinter(param, kcfg, k2_box={"end_print_unload": True,
                                        "ending_material": False},
                   toolhead={"homed_axes": ""})
    assert "KTWO_BOX_END_PRINT" not in _render(env, macros, "END_PRINT", pr)[0]
    return ("Defaults purge AN/unload AN (A5)/3000/3600, Grenzen, k2-module.cfg; "
            "END_PRINT ruft KTWO_BOX_END_PRINT nur mit Option/Restmaterial + "
            "Freigabe + Referenz, vor TURN_OFF_HEATERS")


# =====================================================================
# Stock-Retry bei 0x0c + Temperatur-Guard (18.10.2026, Abgleich mit
# gitstonelabs, README-k2_box.md §13)
# =====================================================================

def _names(bus, start=0):
    return ["%02x:%s" % (c[1], ",".join("%02x" % b for b in c[2]))
            for c in bus.calls[start:] if c[1] != 0x0A]


def test_extrude_stock_retry():
    tail = ["10:02,06,00", "10:02,07,03", "05:", "04:02,00", "0f:00"]
    pre = ["04:00,01", "0f:01", "08:00", "10:02,00,00"]
    # a) 0x0c in Phase 1 -> EIN mode-4-Neustart (retry_fn "restart") -> ok
    cfg, bus, ctl, vc = build_vsystem()
    bus.stall_phases = [2]
    seen = []
    res = ctl.do_extrude("B", odo_polls=3,
                         retry_fn=lambda k, e: seen.append((k, e.status)))
    assert res["status"] == "ok" and res["retries"] == ["mode4-neustart"], res
    assert seen == [("restart", 0x0C)], seen
    exp = (pre + ["10:02,04,00"] + ["10:02,05,00"] * 2 + ["10:02,04,00"]
           + ["10:02,05,00"] * 3 + tail)
    assert _names(bus) == exp, _names(bus)
    # b) 0x0c auch nach dem Neustart -> 04 [00 01] + 11 [m 01], Neuversuch
    #    ab mode 0 (ohne 0f/08), der gelingt
    cfg, bus, ctl, vc = build_vsystem()
    bus.stall_phases = [2, 1]
    seen = []
    res = ctl.do_extrude("B", odo_polls=2,
                         retry_fn=lambda k, e: seen.append(k))
    assert res["retries"] == ["mode4-neustart", "neuversuch"], res
    assert seen == ["restart", "full"], seen
    exp = (pre + ["10:02,04,00"] + ["10:02,05,00"] * 2 + ["10:02,04,00",
           "10:02,05,00", "04:00,01", "11:02,01", "10:02,00,00",
           "10:02,04,00"] + ["10:02,05,00"] * 2 + tail)
    assert _names(bus) == exp, _names(bus)
    assert ctl.loaded_slot == "B"
    # c) auch der Neuversuch scheitert -> Abbruch, Folge == Stock-Fehlerpfad
    #    (K2-Live-Log Polls 2/2/3/2), KEIN 04/11 am Ende, Box bleibt ERROR
    cfg, bus, ctl, vc = build_vsystem()
    bus.stall_phases = [2, 2, 3, 2]
    try:
        ctl.do_extrude("B", odo_polls=5)
    except BOX.CfsStatusError as exc:
        assert exc.status == 0x0C and "Neuversuch" in exc.name, exc.name
    else:
        raise AssertionError("doppelt gescheiterter Einzug hätte abbrechen müssen")
    if _HAVE_SEQ and _HAVE_S485:
        got = [bytes(REF_BUILD(a, f, list(d), status=0xFF))
               for a, f, d, _k in bus.calls if f != 0x0A]
        assert got == SEQ.build_extrude_failure_path(0x02, (2, 2, 3, 2)), \
            _names(bus)
        live = SEQ.requests_from_log_cmds(SEQ.EXTRUDE_RETRY_LIVE_CMDS_B)
        assert got[3:] == live, "Fehlerpfad != Live-Log 2026-10-12"
    assert ctl.last_extrude_retries == ["mode4-neustart", "neuversuch",
                                        "mode4-neustart"], ctl.last_extrude_retries
    assert ctl.poll_status().get("error_code") == 0x0C and ctl.loaded_slot is None
    # d) Dauer-Stillstand (Kanal A ~245 mm): begrenzt, keine Endlosschleife
    cfg, bus, ctl, vc = build_vsystem(fail_after_mm=245.0)
    try:
        ctl.do_extrude("A", odo_polls=50)
    except BOX.CfsStatusError as exc:
        assert exc.status == 0x0C, exc.status
    else:
        raise AssertionError("Dauer-Stillstand hätte abbrechen müssen")
    n4 = sum(1 for c in bus.calls if c[1] == 0x10 and c[2][1] == 0x04)
    n0 = sum(1 for c in bus.calls if c[1] == 0x10 and c[2][1] == 0x00)
    n11 = sum(1 for c in bus.calls if c[1] == 0x11)
    assert (n4, n0, n11) == (4, 2, 1), (n4, n0, n11)
    # e) 0x09 schon auf mode 0 -> 04 [00 01] + 11 [m 01] (Stock), Abbruch
    cfg, bus, ctl, vc = build_vsystem()
    bus.mode0_status = 0x09
    try:
        ctl.do_extrude("C")
    except BOX.CfsStatusError as exc:
        assert exc.status == 0x09, exc.status
    else:
        raise AssertionError("0x09 auf mode 0 hätte abbrechen müssen")
    assert _names(bus) == ["04:00,01", "0f:01", "08:00", "10:04,00,00",
                           "04:00,01", "11:04,01"], _names(bus)
    assert ctl.last_extrude_retries == ["0x09-abbruch"]
    # f) anderer Status (0x0b > 3000 mm) -> sofortiger Abbruch, kein Retry
    cfg, bus, ctl, vc = build_vsystem()
    bus.stall_phases, bus.stall_status = [1], 0x0B
    try:
        ctl.do_extrude("B")
    except BOX.CfsStatusError as exc:
        assert exc.status == 0x0B, exc.status
    else:
        raise AssertionError("0x0b hätte sofort abbrechen müssen")
    assert _names(bus)[-1] == "10:02,05,00" and ctl.last_extrude_retries == []
    # g) mit Kopfsensor: Budget je Förderphase, Retry greift genauso
    cfg, bus, ctl, vc = build_vsystem()
    bus.stall_phases = [1]
    bus.head_at_mm = 100.0
    res = ctl.do_extrude("B", sensor_fn=lambda: bus.head, sensor_timeout=90,
                         poll_interval=2.0)
    assert res["retries"] == ["mode4-neustart"] and bus.head, res
    return ("0x0c -> mode 4 neu -> 04/11 + Neuversuch ab mode 0 -> Abbruch "
            "(== K2-Live-Log), max. 4 Förderphasen, 0x09 -> 04/11 + Abbruch, "
            "andere Status sofort")


def test_extrude_retry_huelle():
    """KTWO_BOX_EXTRUDE: Düsenreinigung vor dem mode-4-Neustart, Meldungen;
    Dauer-Stillstand im Druck -> bestehender Pfad (PAUSE, RESUME gesperrt)."""
    bus, printer, mod = build_hull()
    bus.trace = []
    bus.head_at_mm = 150.0
    bus.need_extruder_for_mode7 = True
    bus.stall_phases = [1, 1]
    g = FakeGcmd({"TNN": "T1B"})
    mod.cmd_extrude(g)
    assert any("Stock-Retry: Düsenreinigung" in m for m in g.infos), g.infos
    assert any("ein Neuversuch ab mode 0" in m for m in g.infos), g.infos
    assert any("EXTRUDE T1B ok" in m for m in g.infos), g.infos
    tr = bus.trace
    i1 = _idx(tr, lambda e: e[0] == "bus" and e[1] == 0x10 and e[2][1] == 0x04)
    i2 = _idx(tr, lambda e: e[0] == "bus" and e[1] == 0x10 and e[2][1] == 0x04,
              i1 + 1)
    clean = [e[1] for e in tr[i1:i2] if e[0] == "gcode"]
    assert "G0 X170 F12000" in clean and "M400" in clean, clean
    assert mod.controller.loaded_slot == "B"
    # Dauer-Stillstand im Druck: Abbruch -> PAUSE wie bisher
    bus2, printer2, mod2 = build_hull()
    bus2.fail_after_mm = 245.0
    printer2.set_printing(True)
    g2 = FakeGcmd({"TNN": "T1A"})
    mod2.cmd_extrude(g2)
    assert printer2.paused and "0x0c" in (mod2.chain_error or ""), \
        (printer2.paused, mod2.chain_error)
    assert sum(1 for c in bus2.calls if c[1] == 0x11) == 1
    return "Reinigung vor mode-4-Neustart, Neuversuch gemeldet, Dauerfehler -> PAUSE"


class _Heater:
    def __init__(self, min_t):
        self.min_extrude_temp = min_t


def test_temperatur_guard():
    bus = FakeBoxBus()
    printer = FakePrinter(bus)
    mod = K.PrinterK2Box(FakeConfig(printer, test_options()))
    printer.fire("klippy:connect")
    mod._bus = bus
    mod.handle_motor_ready()

    def _try(fn, slot="B"):
        n = len(bus.calls)
        g = FakeGcmd({"SLOT": slot})
        try:
            fn(g)
        except ValueError as exc:
            sent = [c for c in bus.calls[n:] if c[1] != 0x0A]
            assert not sent, "trotz Sperre gesendet: %s" % (sent,)
            return str(exc)
        return None
    # kein [extruder] lesbar -> gesperrt (sicher)
    for fn in (mod.cmd_load, mod.cmd_unload):
        txt = _try(fn)
        assert txt and "nicht lesbar" in txt and "nichts gesendet" in txt, txt
    # kalt (can_extrude False) -> gesperrt, Meldung mit Temperatur/Grenze
    ext = _Obj({"temperature": 25.0, "target": 0.0, "can_extrude": False})
    ext.heater = _Heater(170.0)
    printer.objects["extruder"] = ext
    for fn, name in ((mod.cmd_load, "Load"), (mod.cmd_unload, "Unload")):
        txt = _try(fn)
        assert txt and ("%s SLOT=B abgelehnt" % name) in txt \
            and "25 °C unter min_extrude_temp 170" in txt \
            and "weder 0x10 noch 0x11" in txt, txt
    # DRY_RUN bleibt erlaubt (sendet ohnehin nichts)
    g = FakeGcmd({"SLOT": "B", "DRY_RUN": "1"})
    mod.cmd_load(g)
    assert any("Trockenlauf" in m for m in g.infos), g.infos
    # ohne can_extrude: Ist-Temperatur gegen heater.min_extrude_temp
    ext.status = {"temperature": 160.0, "target": 160.0}
    ext.heater = _Heater(150.0)
    assert _try(mod.cmd_load) is None
    assert any(c[1] == 0x10 for c in bus.calls), "Load hätte senden müssen"
    assert _try(mod.cmd_unload) is None
    # ohne heater-Attribut/configfile: Upstream-Default 170 °C
    del ext.heater
    txt = _try(mod.cmd_load)
    assert txt and "160 °C unter min_extrude_temp 170" in txt, txt
    # configfile min_extrude_temp 0 -> erlaubt
    printer.objects["configfile"] = _Obj(
        {"settings": {"extruder": {"min_extrude_temp": 0.0}}})
    assert _try(mod.cmd_unload) is None
    # heiß (can_extrude True) -> ok
    ext.status = {"temperature": 230.0, "target": 230.0, "can_extrude": True}
    assert _try(mod.cmd_load) is None and _try(mod.cmd_unload) is None
    return ("LOAD/UNLOAD kalt/unlesbar -> Abbruch ohne Frame, can_extrude > "
            "Ist-Temp vs. min_extrude_temp (heater/configfile/170), DRY_RUN frei")


# ---------------------------------------------------------------------
# L3 (18.10.2026): Fahrgrenzen – k2_box weitet nur für Schritte außerhalb
# des Betriebsfensters auf und gibt sie im finally zurück ([k2_limits])
# ---------------------------------------------------------------------

class _LimRail:
    def __init__(self, lo, hi):
        self.position_min, self.position_max = lo, hi

    def get_range(self):
        return self.position_min, self.position_max


class _LimKin:
    def __init__(self):
        self.rails = [_LimRail(-7.8, 352.5), _LimRail(-6.2, 352.0),
                      _LimRail(-10.0, 350.2)]
        self.limits = [r.get_range() for r in self.rails]


class _LimFake:
    """[k2_limits] mit echter LimitsCore/xy_points (k2_limits.py)."""

    def __init__(self, printer):
        import k2_limits as L
        self.L = L
        self.printer = printer
        self.kin = _LimKin()
        self.core = L.LimitsCore(
            self.kin, narrow={0: (-7.8, 352.5), 1: (-6.2, 352.0)},
            wide={0: (-10.0, 352.5), 1: (-6.2, 400.0)})

    def available(self):
        return True

    def needs_wide(self, lines):
        pts = self.L.xy_points(lines, self.printer.pos[:2])
        return any(self.core.outside_narrow(x, y) for x, y in pts)

    def widen(self, owner, reason=""):
        self.core.widen(owner, reason)

    def release(self, owner):
        self.core.release(owner)


def _lim_hull():
    bus, printer, mod = build_hull()
    lim = _LimFake(printer)
    printer.objects["k2_limits"] = lim
    orig = printer.lookup_object

    def lookup(name, default=KeyError):
        if name in printer.objects:
            return printer.objects[name]
        if default is KeyError:
            return orig(name)
        return default
    printer.lookup_object = lookup
    seen = []
    sim = printer.sim

    def sim_rec(line):
        if line.startswith(("G0", "G1")):
            seen.append((line, lim.core.is_wide()))
        return sim(line)
    printer.sim = sim_rec
    return printer, mod, lim, seen


def test_limits_l3():
    printer, mod, lim, seen = _lim_hull()
    printer.pos[:3] = [200.0, 200.0, 20.0]
    mod.cmd_go_extrude_pos(FakeGcmd())
    y378 = [w for l, w in seen if "Y378" in l]
    assert y378 and all(y378), seen
    assert not lim.core.is_wide() and lim.kin.limits[1] == (-6.2, 352.0)
    assert ("wide", "k2_box", "Abfallschacht (BOX_GO_TO_EXTRUDE_POS)") in lim.core.log
    # Schnitt an cut_pos_x (= enge X-Grenze) -> keine Aufweitung
    del seen[:]
    n_log = len(lim.core.log)
    printer.pos[:3] = [200.0, 200.0, 20.0]
    mod.cmd_cut(FakeGcmd())
    assert any("X-7.8" in l for l, _w in seen) and len(lim.core.log) == n_log, \
        (seen, lim.core.log[n_log:])
    # Fehler mitten im Schacht -> Halter zurück, eng
    printer.pos[:3] = [200.0, 200.0, 20.0]
    printer.fail_on = "G0 Y378"
    try:
        mod.cmd_go_extrude_pos(FakeGcmd())
        raise AssertionError("Fehler erwartet")
    except ValueError:
        pass
    printer.fail_on = None
    assert not lim.core.is_wide() and lim.kin.rails[1].position_max == 352.0
    # ungehomt -> Aufweiten verweigert, keine Fahrt
    lim.kin.limits[1] = (1.0, -1.0)
    del seen[:]
    printer.pos[:3] = [200.0, 200.0, 20.0]
    try:
        mod.cmd_go_extrude_pos(FakeGcmd())
        raise AssertionError("Fehler erwartet")
    except ValueError as exc:
        assert "nicht referenziert" in str(exc), exc
    assert seen == [] and not lim.core.is_wide(), seen
    return ("Schacht weit + zurück, Schnitt an cut_pos_x ohne Aufweitung, "
            "Fehler -> eng, ungehomt -> keine Fahrt")


def test_m8200_kette_grenzen():
    """Creality Print 7: Datei fährt zwischen M8200 P und O selbst zum
    Schacht (G0 Y378) – live 22.10.2026 „Move out of range: 139 378“.
    M8200-Kette hält die Grenzen weit bis END_OPT/Abbruch, T<n>-Kette nicht."""
    printer, mod, lim, seen = _lim_hull()
    printer.pos[:3] = [200.0, 200.0, 20.0]
    owner = "k2_box_m8200"
    mod.cmd_pre_opt(FakeGcmd({"Z_HOP": "0"}))           # = M8200 P
    assert owner in lim.core.holders and lim.kin.limits[1] == (-6.2, 400.0), \
        (lim.core.holders, lim.kin.limits)
    mod.cmd_end_opt(FakeGcmd({"RETURN": "0"}))          # = M8200 O
    assert owner not in lim.core.holders and not lim.core.is_wide(), \
        lim.core.holders
    assert lim.kin.limits[1] == (-6.2, 352.0), lim.kin.limits
    # T<n>-Kette: keine Ketten-Aufweitung
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1A", "SOURCE": "T"}))
    assert owner not in lim.core.holders, lim.core.holders
    mod.cmd_end_opt(FakeGcmd({"RETURN": "0"}))
    # Abbruch: neue Kette ohne END_OPT bzw. Aufräumen gibt frei
    mod.cmd_pre_opt(FakeGcmd({"Z_HOP": "0"}))
    assert owner in lim.core.holders
    mod._chain_cleanup(FakeGcmd())
    assert owner not in lim.core.holders and not lim.core.is_wide()
    # ohne allow_move: nichts aufweiten
    mod.cfg["allow_move"] = False
    mod.cmd_pre_opt(FakeGcmd({"Z_HOP": "0"}))
    assert owner not in lim.core.holders
    mod.cfg["allow_move"] = True
    mod._chain_cleanup(FakeGcmd())
    return "M8200 P weit bis O, Abbruch gibt frei, T<n> unverändert, ohne Freigabe nichts"


# =====================================================================
# KTWO_BOX_RFID / KTWO_BOX_SLOT_SET (18.10.2026; Stock BOX_INFO_REFRESH /
# BOX_MODIFY_TN_DATA)
# =====================================================================

def _calls_since(bus, n0):
    return [(c[1], c[2]) for c in bus.calls[n0:]]


def _raises(fn, params, text):
    g = FakeGcmd(params)
    try:
        fn(g)
    except ValueError as exc:
        assert text in str(exc), (text, str(exc))
        return str(exc)
    raise AssertionError("Fehler erwartet (%s): %s" % (text, g.infos))


def test_rfid_befehl():
    bus, printer, mod = _material_hull()
    ctl = mod._ensure_controller()
    bus.slots["D"]["rfid"] = None                 # D ohne Etikett
    # Einzelslot: genau 0x02 [02] + 0x03 [02], nichts sonst
    n0 = len(bus.calls)
    g = FakeGcmd({"SLOT": "T1B"})
    mod.cmd_rfid(g)
    assert _calls_since(bus, n0) == [(0x02, (0x02,)), (0x03, (0x02,))], \
        _calls_since(bus, n0)
    txt = g.infos[-1]
    assert "Maske 0x02" in txt and "T1B: Etikett gelesen" in txt and \
        "Material 190002 (Test PLA, PLA, 220 °C), Farbe 0CE58F8" in txt and \
        "Rest 29 %" in txt and "Quelle rfid" in txt, txt
    # Slot -> Bit für alle vier Schreibweisen
    for raw, bit in (("T1A", 1), ("a", 1), ("C", 4), ("3", 8), ("t1d", 8)):
        n0 = len(bus.calls)
        mod.cmd_rfid(FakeGcmd({"SLOT": raw}))
        assert _calls_since(bus, n0) == [(0x02, (bit,)), (0x03, (bit,))], raw
    # ALL (und ohne SLOT): 0x02 [0f] + 0x03 [0f]
    for params in ({"SLOT": "ALL"}, {}):
        n0 = len(bus.calls)
        g = FakeGcmd(params)
        mod.cmd_rfid(g)
        assert _calls_since(bus, n0) == [(0x02, (0x0F,)), (0x03, (0x0F,))]
    txt = g.infos[-1]
    assert txt.count("Etikett gelesen") == 3 and \
        "T1D: kein lesbares Etikett (unknown), Material/Farbe unbekannt, " \
        "Rest unbekannt" in txt and "T1C" in txt and "PETG" in txt, txt
    st = mod.get_status()
    sm = st["slot_material"]
    assert sm["A"]["type"] == "190001" and sm["A"]["color"] == "0FFFFFF" \
        and sm["A"]["remain"] == 28 and sm["A"]["source"] == "rfid"
    assert sm["D"]["type"] is None and sm["D"]["source"] == "" and \
        sm["D"]["remain"] is None
    assert st["rfid_last"].startswith("T1A:")
    # Einzel-Remain überschreibt die anderen Slots nicht (0xff = nicht gefragt)
    bus.slots["B"]["remain"] = 77
    mod.cmd_rfid(FakeGcmd({"SLOT": "B"}))
    sm = mod.get_status()["slot_material"]
    assert sm["B"]["remain"] == 77 and sm["A"]["remain"] == 28 and \
        sm["C"]["remain"] == 30
    # nie Maske 0, nie Preload/Bewegung (Stock-Makro hätte 0x0d RUN)
    assert all(not (f in (0x02, 0x03) and d == (0,)) for _a, f, d, _k
               in bus.calls), "Maske 0 gesendet"
    assert not any(c[1] in (0x0D, 0x10, 0x11, 0x04) for c in bus.calls)
    # Fehler: andere Box, ungültiger Slot -> G-Code-Fehler ohne Frame
    for raw, text in (("T2A", "Box 2"), ("T1E", "T1A..T4D"),
                      ("E", "A..D"), ("0x01", "A..D")):
        n0 = len(bus.calls)
        _raises(mod.cmd_rfid, {"SLOT": raw}, text)
        assert len(bus.calls) == n0, raw
    # Sperren: Kette aktiv / busy / Druck (FORCE=1 hebt nur den Druck auf)
    mod.chain["active"], mod.chain["tnn"] = True, "T1C"
    n0 = len(bus.calls)
    _raises(mod.cmd_rfid, {"SLOT": "A", "FORCE": "1"}, "Wechsel läuft (T1C)")
    mod.chain = mod._new_chain()
    ctl.busy = True
    _raises(mod.cmd_rfid, {"SLOT": "A"}, "busy")
    ctl.busy = False
    printer.set_printing(True)
    _raises(mod.cmd_rfid, {"SLOT": "A"}, "FORCE=1")
    assert len(bus.calls) == n0
    printer.paused, printer.print_state = True, "paused"
    printer._refresh()
    _raises(mod.cmd_rfid, {}, "Druck läuft/pausiert")
    mod.cmd_rfid(FakeGcmd({"SLOT": "A", "FORCE": "1"}))
    assert _calls_since(bus, n0) == [(0x02, (0x01,)), (0x03, (0x01,))]
    printer.paused = False
    printer.set_printing(False)
    # lesend: auch ohne allow_write; Bus-Fehler -> G-Code-Fehler
    bus2, pr2, mod2 = _material_hull(allow_write="False")
    n0 = len(bus2.calls)
    mod2.cmd_rfid(FakeGcmd({"SLOT": "T1A"}))
    assert _calls_since(bus2, n0) == [(0x02, (1,)), (0x03, (1,))]
    orig = bus2._handle

    def dead(addr, fun, payload):
        if fun == 0x02:
            raise OSError("Port weg")
        return orig(addr, fun, payload)
    bus2._handle = dead
    _raises(mod2.cmd_rfid, {"SLOT": "A"}, "RS485-Bus-Fehler")
    # Makro-/Befehlsnamen Upstream-tauglich
    assert upstream_gcode_name_ok("KTWO_BOX_RFID") and \
        upstream_gcode_name_ok("KTWO_BOX_SLOT_SET")
    assert "KTWO_BOX_RFID" in printer.gcode.commands and \
        "KTWO_BOX_SLOT_SET" in printer.gcode.commands
    return ("0x02/0x03 [Slotbit] bzw. [0f], nie Maske 0/0x0d, Meldung DB-"
            "Material/Farbe/Rest, Status slot_material, Einzel-Remain ohne "
            "Nebenwirkung, Sperren Kette/busy/Druck (FORCE), ohne allow_write")


def test_slot_set():
    sel = K.select_runout_slot
    # reine Bausteine
    assert K.merge_tag(None, None) is None
    assert K.merge_tag(("190001", "0FFFFFF"), {"color": "0FF0000"}) == \
        ("190001", "0FF0000")
    assert K.merge_tag(None, {"type": "90003"}) == ("90003", None)
    assert K.same_material_key("190003", "90003") and \
        not K.same_material_key("190003", "190001") and \
        not K.same_material_key(None, None)
    rf = {"A": V_WEISS, "B": "unknown", "C": V_SCHWARZ, "D": "unknown"}
    assert sel("A", 0x0F, rf)[0] is None
    t, why = sel("A", 0x0F, rf, manual={"D": {"type": "01001",
                                               "color": "0FFFFFF"}})
    assert t == "D", (t, why)
    assert sel("A", 0x0F, rf, manual={"C": {"color": "0FFFFFF"}})[0] == "C"
    assert sel("B", 0x0F, rf, manual={"B": {"type": "101001"}},
               require_color=False)[0] == "C"
    assert sel("B", 0x0F, rf, manual={"B": {"type": "101001"}})[0] is None
    # Hülle: Anzeige, Setzen je Feld, Vorrang, Wirkung auf Spülen/Temperatur
    bus, printer, mod = _material_hull()
    ctl = mod._ensure_controller()
    mod.cmd_rfid(FakeGcmd({"SLOT": "ALL"}))
    g = FakeGcmd({"SLOT": "T1B"})
    mod.cmd_slot_set(g)
    assert "Handwert keiner" in g.infos[-1] and "Quelle rfid" in g.infos[-1]
    g = FakeGcmd({"SLOT": "T1B", "COLOR": "#ff0000"})
    mod.cmd_slot_set(g)
    sm = mod.get_status()["slot_material"]["B"]
    assert (sm["type"], sm["color"], sm["source"]) == (
        "190002", "0FF0000", "rfid+manuell"), sm
    assert mod.slot_manual == {"T1B": {"color": "0FF0000"}}
    mod.cmd_slot_set(FakeGcmd({"SLOT": "B", "MATERIAL": "test petg"}))
    sm = mod.get_status()["slot_material"]["B"]
    assert (sm["type"], sm["color"], sm["source"]) == (
        "90003", "0FF0000", "manuell") and "PETG" in sm["material"], sm
    # Spülplan nutzt den Handwert (Material-DB: PETG 250 °C, anderes
    # Material als PLA in der Düse -> different, Farbe rot)
    mod.remain_material = {"type": "190001", "color": "0FFFFFF", "tnn": "T1A"}
    n0 = len(bus.calls)
    pl = mod._flush_plan(FakeGcmd({}), "B", None, None, "T1B")
    assert len(bus.calls) == n0, "mit Handwert kein RFID-Lesen"
    assert pl["new_key"] == "90003" and pl["new_col"] == "0FF0000" and \
        pl["different"] and pl["temp"] >= 250.0, pl
    # TYPE=<id> / COLOR=none (nur Feld weg) / CLEAR=1
    mod.cmd_slot_set(FakeGcmd({"SLOT": "T1B", "TYPE": "190004",
                               "COLOR": "none"}))
    assert mod.slot_manual == {"T1B": {"type": "190004"}}
    assert mod._cached_tag("B") == ("190004", "0CE58F8")
    mod.cmd_slot_set(FakeGcmd({"SLOT": "T1B", "CLEAR": "1"}))
    assert mod.slot_manual == {} and mod._cached_tag("B") == (
        "190002", "0CE58F8")
    # Fehler
    for params, text in (({"SLOT": "ALL", "COLOR": "fff000"}, "ALL hier nicht"),
                         ({}, "SLOT=T1A..T1D fehlt"),
                         ({"SLOT": "T2B", "COLOR": "fff000"}, "Box 2"),
                         ({"SLOT": "B", "COLOR": "rot"}, "0RRGGBB"),
                         ({"SLOT": "B", "TYPE": "a b"}, "1-32 Zeichen"),
                         ({"SLOT": "B", "TYPE": "1", "MATERIAL": "x"},
                          "nicht zusammen"),
                         ({"SLOT": "B", "MATERIAL": "Test"}, "mehrdeutig"),
                         ({"SLOT": "B", "MATERIAL": "Nylon"},
                          "nicht in der Material-DB")):
        _raises(mod.cmd_slot_set, params, text)
    assert mod.slot_manual == {}
    _bus0, _p0, mod0 = _material_hull(db=False)
    _raises(mod0.cmd_slot_set, {"SLOT": "A", "MATERIAL": "PLA"},
            "material_database")
    # Sperre in der Kette (Anzeige bleibt erlaubt)
    mod.chain["active"], mod.chain["tnn"] = True, "T1A"
    _raises(mod.cmd_slot_set, {"SLOT": "C", "COLOR": "00ff00"},
            "Wechsel läuft (T1A)")
    mod.cmd_slot_set(FakeGcmd({"SLOT": "C"}))
    mod.chain = mod._new_chain()
    # Vorrang: lesbares Etikett verwirft den Handwert, „unknown“ nicht
    bus.slots["D"]["rfid"] = None
    mod.cmd_slot_set(FakeGcmd({"SLOT": "T1A", "COLOR": "0123456"}))
    mod.cmd_slot_set(FakeGcmd({"SLOT": "T1D", "TYPE": "190003",
                               "COLOR": "0ABCDEF"}))
    g = FakeGcmd({"SLOT": "ALL"})
    mod.cmd_rfid(g)
    assert mod.slot_manual == {"T1D": {"type": "190003",
                                       "color": "0ABCDEF"}}, mod.slot_manual
    assert any("T1A: RFID-Etikett gelesen – Handwert -/0123456 verworfen"
               in m for m in printer.gcode.infos)
    assert "T1D: kein lesbares Etikett (unknown), Material 190003 " \
        "(Test PETG, PETG, 250 °C), Farbe 0ABCDEF, Quelle manuell" \
        in g.infos[-1], g.infos[-1]
    # Runout-Auswahl in der Hülle sieht den Handwert (A leer, D = PETG)
    sm = mod._manual_letters()
    assert sm == {"D": {"type": "190003", "color": "0ABCDEF"}}
    return ("merge je Feld, Runout mit Handwert (5/6-stellige ID), Spülplan/"
            "Temperatur aus Handwert ohne RFID-Lesen, TYPE/MATERIAL/none/"
            "CLEAR, Fehler, Kettensperre, lesbares Etikett verwirft, unknown "
            "nicht")


def test_slot_set_datei():
    import glob
    d = _rm_dir()
    cfg = K.parse_options({})
    assert cfg["slot_material_file"] == \
        "~/printer_data/config/k2/slot_material.json"
    assert K.parse_options({"slot_material_file": ""})[
        "slot_material_file"] == ""
    import configparser
    cp = configparser.ConfigParser(interpolation=None, strict=False)
    cp.read(os.path.join(_HERE, "config-pi", "k2-module.cfg"),
            encoding="utf-8")
    assert dict(cp.items("k2_box"))["slot_material_file"] == \
        "~/printer_data/config/k2/slot_material.json"
    assert K.load_slot_material("") == (None, "aus", "")
    p = os.path.join(d, "k2", "slot.json")
    assert K.load_slot_material(p)[:2] == (None, "fehlt")
    # Setzen schreibt atomar (tmp + fsync + replace), Verzeichnis angelegt
    bus, printer, mod = _material_hull(slot_material_file=p)
    assert mod.get_status()["slot_material_load"] == "fehlt"
    calls = []
    o_rep = K.os.replace
    K.os.replace = lambda a, b: (calls.append((a, b)), o_rep(a, b))[1]
    try:
        mod.cmd_slot_set(FakeGcmd({"SLOT": "T1C", "COLOR": "11aa22",
                                   "TYPE": "190001"}))
    finally:
        K.os.replace = o_rep
    assert calls == [(p + ".tmp", p)] and not os.path.exists(p + ".tmp")
    assert _read_json(p) == {"slots": {"T1C": {"type": "190001",
                                               "color": "011AA22"}}}
    # Neustart: Handwert zurück (connect), wirkt auf den Slot
    bus2, pr2, mod2 = _material_hull(slot_material_file=p)
    st = mod2.get_status()
    assert st["slot_material_load"] == "geladen" and \
        st["slot_material"]["C"]["color"] == "011AA22" and \
        "T1C 190001/011AA22" in st["slot_material_info"], st
    # lesbares Etikett beim Lesen -> Handwert weg, Datei nachgezogen
    mod2.cmd_rfid(FakeGcmd({"SLOT": "C"}))
    assert mod2.slot_manual == {} and _read_json(p) == {"slots": {}}
    # andere Top-Level-Schlüssel bleiben; fremde Box bleibt erhalten
    _write(p, '{"x": 1, "slots": {"T2A": {"color": "0FFFFFF", "type": null}}}')
    bus3, pr3, mod3 = _material_hull(slot_material_file=p)
    mod3.cmd_slot_set(FakeGcmd({"SLOT": "T1A", "COLOR": "000000"}))
    assert _read_json(p) == {"x": 1, "slots": {
        "T1A": {"type": None, "color": "0000000"},
        "T2A": {"type": None, "color": "0FFFFFF"}}}
    assert mod3.get_status()["slot_material"]["A"]["color"] == "0000000"
    # kaputt / falsche Struktur -> Warnung, beiseitegelegt, nie Config-Fehler
    for i, text in enumerate(("{", "[]", '{"slots": []}',
                              '{"slots": {"T9Z": {}}}',
                              '{"slots": {"T1A": {"color": "rot"}}}',
                              '{"anders": 1}')):
        b = os.path.join(d, "bad%d.json" % i)
        _write(b, text)
        bus4, pr4, mod4 = _material_hull(slot_material_file=b)
        st = mod4.get_status()
        assert st["slot_material_load"] == "fehler" and \
            mod4.slot_manual == {}, (text, st["slot_material_info"])
        moved = glob.glob(b + ".defekt-*")
        assert len(moved) == 1 and open(moved[0]).read() == text, text
        assert any(m.startswith("// [k2_box] WARNUNG") and "ungültig" in m
                   for m in pr4.gcode.raw), pr4.gcode.raw
    # Schreibfehler = nur Warnung, Wert gilt im Speicher
    blocker = os.path.join(d, "datei-statt-ordner")
    _write(blocker, "x")
    bus5, pr5, mod5 = _material_hull(
        slot_material_file=os.path.join(blocker, "slot.json"))
    g = FakeGcmd({"SLOT": "A", "COLOR": "222222"})
    mod5.cmd_slot_set(g)
    assert mod5.slot_manual == {"T1A": {"color": "0222222"}} and \
        any("nicht gespeichert" in m for m in g.infos), g.infos
    # aus: nur Speicher, keine Datei
    bus6, pr6, mod6 = _material_hull()
    mod6.cmd_slot_set(FakeGcmd({"SLOT": "A", "COLOR": "333333"}))
    assert mod6.get_status()["slot_material_file"] == "" and \
        not glob.glob(os.path.join(_TEST_HOME, "printer_data", "config",
                                   "k2", "slot_material.json"))
    return ("Default/k2-module.cfg, atomar, Neustart lädt, RFID verwirft + "
            "Datei, fremde Box/Schlüssel bleiben, 6 kaputte Fälle (.defekt-…), "
            "Schreibfehler nur Warnung, aus = nur Speicher")


# =====================================================================
# Live-Abgleich Einfarbdruck 18.10.2026 (A1/A2/A3/A5/A6)
# =====================================================================

def _mech18(bus_calls, funs=(0x04, 0x08, 0x0D, 0x0F, 0x10, 0x11)):
    return [(c[1], tuple(c[2])) for c in bus_calls if c[1] in funs]


def test_live_a1_erstladen():
    # A1: T<n>-Kette, Kopf sicher leer + Hub leer -> nur laden (Stock T0
    # live 16:46:26: kein Cut, kein Rückzug, 0x04 [00 01] -> 0x08 [00] -> 0x10)
    bus, printer, mod = build_hull(allow_write="True")
    bus.hub_mask, bus.head = 0x00, False
    bus.head_at_mm = 300.0
    bus.need_extruder_for_mode7 = True
    bus.trace = []
    infos = run_chain(mod, "T1A")
    sc = printer.gcode.scripts
    assert "G0 X-7.8 Y200 F30000" not in sc, "kein Schnitt"
    assert not [l for l in sc if l.startswith("G1 E-10")], "kein Extruder-Rückzug"
    assert not [c for c in bus.calls if c[1] == 0x11], "kein 0x11"
    t = bus.trace
    i0 = _idx(t, _b(0x04, (0x00, 0x01)))
    i1 = _idx(t, _b(0x10, (0x01, 0x00, 0x00)), i0)          # m0
    _idx(t, _b(0x04, (0x01, 0x00)), i1)                    # PRINT
    assert any("nur laden" in m for m in infos) and any(
        "CUT übersprungen" in m for m in infos) and any(
        "RETRUDE übersprungen" in m for m in infos), infos
    st = mod.get_status()
    assert st["loaded_tnn"] == "T1A" and st["chain_error"] == "" \
        and not st["chain_active"], st
    # Rückfälle (bisheriges sicheres Verhalten = schneiden):
    # a) Kopfsensor unbekannt (fehlt)
    bus, printer, mod = build_hull(allow_write="True")
    bus.hub_mask, bus.head, bus.head_at_mm = 0x00, False, 300.0
    del printer.objects["filament_switch_sensor filament_sensor"]
    run_chain(mod, "T1A")
    assert "G0 X-7.8 Y200 F30000" in printer.gcode.scripts, "Sensor ? -> Schnitt"
    # b) Kopf leer, aber Hub belegt (Slot B) -> Schnitt + Rückzug B
    bus, printer, mod = build_hull(allow_write="True")
    bus.hub_mask, bus.head, bus.head_at_mm = 0x02, False, 300.0
    bus.need_extruder_for_mode7 = True
    run_chain(mod, "T1A")
    sc = printer.gcode.scripts
    assert "G0 X-7.8 Y200 F30000" in sc and [
        c for c in bus.calls if c[1] == 0x11 and c[2][0] == 0x02], "Hub -> Rückzug"
    # c) Hub unlesbar -> Schnitt
    bus, printer, mod = build_hull(allow_write="True")
    bus.hub_mask, bus.head, bus.head_at_mm = 0x00, False, 300.0
    orig = bus._handle

    def no_hub(addr, fun, payload):
        if fun == 0x08 and payload == [0x01]:
            raise TimeoutError("Hub stumm")
        return orig(addr, fun, payload)
    bus._handle = no_hub
    mod._empty_load(FakeGcmd({}))  # ohne Kette: nie
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1A", "SOURCE": "T"}))
    assert mod._empty_load(FakeGcmd({})) is False
    mod._chain_cleanup()
    # d) M8200-Kette (ohne SOURCE=T) -> wie bisher schneiden
    bus, printer, mod = build_hull(allow_write="True")
    bus.hub_mask, bus.head, bus.head_at_mm = 0x00, False, 300.0
    bus.need_extruder_for_mode7 = True
    run_chain(mod, "T1A", ret=0, source="")
    assert "G0 X-7.8 Y200 F30000" in printer.gcode.scripts, "M8200 -> Schnitt"
    # e) Kopf mit Filament -> Schnitt (Regression: test_wechsel_kette_voll)
    return ("Kopf+Hub leer: kein Schnitt/0x11/E-10, nur 04/08/10…/04 PRINT; "
            "Sensor fehlt/Hub belegt/Hub unlesbar/M8200 -> Schnitt wie bisher")


def test_live_a2_druckzustand():
    # A2 Controller: Start 0x0d [0f 00] + 0x0f [01], Ende 0x0d [0f 01] +
    # 0x0f [00] (live 16:45:36/37, 17:44:08), Maske nie 0
    bus, printer, mod = build_hull(allow_write="True")
    bus.hub_mask, bus.head, bus.head_at_mm = 0x00, False, 300.0
    bus.need_extruder_for_mode7 = True
    printer.set_printing(True)
    ctl = mod._ensure_controller()
    n0 = len(bus.calls)
    g = FakeGcmd({})
    mod.cmd_print_start(g)
    assert _mech18(bus.calls[n0:]) == [(0x0D, (0x0F, 0x00)), (0x0F, (0x01,))], \
        _mech18(bus.calls[n0:])
    assert ctl.print_hold and mod.get_status()["print_hold"]
    assert bus.preload_enable == 0 and bus.inhibit == 1
    assert any("0x0d [0f 00] + 0x0f [01]" in m for m in g.infos), g.infos
    # Wechsel im Druck: KEIN 0x0f (Stock T0, live)
    n1 = len(bus.calls)
    infos = run_chain(mod, "T1A")
    m = _mech18(bus.calls[n1:])
    assert not [x for x in m if x[0] in (0x0F, 0x0D)], m
    assert m[0] == (0x08, (0x01,)) and (0x04, (0x01, 0x00)) in m, m
    assert any("ohne 0x0f-Umschaltung" in x for x in infos), infos
    assert bus.inhibit == 1, "Sperre bleibt druckweit"
    # Druckende
    n2 = len(bus.calls)
    mod.cmd_print_end(FakeGcmd({}))
    assert _mech18(bus.calls[n2:]) == [(0x0D, (0x0F, 0x01)), (0x0F, (0x00,))]
    assert not ctl.print_hold and bus.inhibit == 0 and bus.preload_enable == 1
    # außerhalb eines Drucks: bisheriges 0x0f-Paar im Einzug
    printer.set_printing(False)
    bus.hub_mask, bus.head = 0x00, False
    ctl.loaded_slot = None
    n3 = len(bus.calls)
    run_chain(mod, "T1B")
    m = _mech18(bus.calls[n3:])
    assert (0x0F, (0x01,)) in m and m.index((0x0F, (0x01,))) < m.index(
        (0x0F, (0x00,))), m
    # Sperre gehalten, aber kein Druck mehr (Druck ohne END_PRINT) -> 0x0f-Paar
    ctl.print_hold = True
    assert mod._extrude_inhibit(ctl) is True
    printer.set_printing(True)
    assert mod._extrude_inhibit(ctl) is False
    ctl.print_hold = False
    # ohne allow_write: nur Plan, kein Frame
    bus, printer, mod = build_hull(allow_write="False")
    g = FakeGcmd({})
    mod.cmd_print_start(g)
    mod.cmd_print_end(g)
    assert not _mech18(bus.calls) and any("PLAN Box KTWO_BOX_PRINT_START" in m
                                        for m in g.infos), g.infos
    # Bus-Fehler: nur Meldung, kein G-Code-Fehler, print_hold bleibt aus
    bus, printer, mod = build_hull(allow_write="True")
    printer.set_printing(True)
    orig = bus._handle

    def tot(addr, fun, payload):
        if fun == 0x0D:
            raise TimeoutError("Box stumm")
        return orig(addr, fun, payload)
    bus._handle = tot
    mod.cmd_print_start(FakeGcmd({}))
    ctl = mod._ensure_controller()
    assert not ctl.print_hold and not printer.paused
    assert any(m.startswith("!!") and "PRINT_START fehlgeschlagen" in m
               and "Druck läuft weiter" in m for m in printer.gcode.raw), \
        printer.gcode.raw
    ctl.print_hold = True
    mod.cmd_print_end(FakeGcmd({}))
    assert not ctl.print_hold, "Ende setzt print_hold auch bei Fehler zurück"
    # do_print_state: Maske fest 0x0f, op 0/1 auch im Druck (force wie Stock)
    assert K.preload_block_reason(K.PRELOAD_DISABLE, K.MASK_ALL, None, True,
                                  force=True) is None
    # Registrierung + Upstream-Namensregel
    for name in ("KTWO_BOX_PRINT_START", "KTWO_BOX_PRINT_END"):
        assert name in printer.gcode.commands and upstream_gcode_name_ok(name)
    return ("Start 0d[0f 00]+0f[01], Wechsel im Druck ohne 0x0f, Ende "
            "0d[0f 01]+0f[00]; außerhalb Druck 0x0f-Paar; Plan ohne "
            "allow_write; Bus-Fehler = Meldung, Druck läuft")


def test_live_a3_rueckzug_geschwindigkeit():
    # A3: Rückzug je p0 mit der Spülgeschwindigkeit des Düsenmaterials
    # (live 18.10. „extrude = -10.0, velocity: [350.0]“, max_vol 14)
    def chain_retrude(db, nozzle_type):
        bus, printer, mod = _material_hull(db=db)
        bus.hub_mask, bus.head, bus.head_at_mm = 0x02, True, 300.0
        bus.need_extruder_for_mode7 = True
        if nozzle_type:
            mod.remain_material = {"type": nozzle_type, "color": "0CE58F8",
                                   "tnn": "T1B"}
        mod.cmd_pre_opt(FakeGcmd({"TNN": "T1C", "SOURCE": "T"}))
        mod.cmd_cut(FakeGcmd({}))
        mod.cmd_retrude(FakeGcmd({}))
        sc = printer.gcode.scripts
        mod._chain_cleanup()
        return [l for l in sc if l.startswith("G1 E-10")]
    assert chain_retrude(True, "190002") == ["G1 E-10 F350"] * 3   # PLA vol 14
    assert chain_retrude(True, "190001") == ["G1 E-10 F360"] * 3   # vol 23 -> 360
    assert chain_retrude(True, "190004") == ["G1 E-10 F50"] * 3    # TPU vol 2
    assert chain_retrude(True, "199999") == ["G1 E-10 F600"] * 3   # unbekannt
    assert chain_retrude(False, "190002") == ["G1 E-10 F600"] * 3  # ohne DB
    return "DB PLA14 F350 / PLA23 F360 / TPU F50; unbekannt bzw. ohne DB F600"


def test_live_a6_end_print_makro():
    try:
        env, macros, kcfg, param = _render_env()
    except ImportError:
        return "SKIP (jinja2 fehlt)"
    k2 = {"end_print_unload": True, "ending_material": False}
    pr = _rprinter(param, kcfg, k2_box=k2, pause_resume={"is_paused": False})
    out, _m = _render(env, macros, "END_PRINT", pr)
    # Stock: Z +2 -> entladen -> Box-Druckzustand zurück -> Rückzug/Z-Hub
    i_z = out.index("G1 Z2 F600")
    i_box = out.index("KTWO_BOX_END_PRINT")
    i_end = out.index("KTWO_BOX_PRINT_END")
    i_park = out.index("_PI_END_PARK")
    i_off = out.index("TURN_OFF_HEATERS FORCE=1")
    assert i_z < i_box < i_end < i_park < i_off, out
    assert not [l for l in out if l.startswith("G1 E")], "kein Rückzug vor dem Schnitt"
    assert not [l for l in out[:i_park] if l.startswith("G1 Z") and l != "G1 Z2 F600"], out
    # Park: Rückzug + Z auf max_z/2 (Position frisch; hier Z 52 nach +2)
    pr2 = _rprinter(param, kcfg, k2_box=k2, pause_resume={"is_paused": False},
                    toolhead={"position": {"x": 160.0, "y": 345.0, "z": 52.0}})
    park, _m = _render(env, macros, "_PI_END_PARK", pr2)
    mz = float(param["max_z_position"])
    assert park == ["M400", "G92 E0", "G91", "G1 E-1.0 F180",
                    "G1 E-%s F4000" % param["pause_extrude"],
                    "G1 Z%s F600" % (mz / 2 - 52.0), "G90"], park
    assert mz / 2 <= 350.2 and mz <= 350.2, "k2_limits Z max 350,2"
    # nahe max_z: kein Z+2, Park ohne Z-Hub
    pr3 = _rprinter(param, kcfg, k2_box=k2, pause_resume={"is_paused": False},
                    toolhead={"position": {"x": 1.0, "y": 1.0, "z": mz - 1.0}})
    out3, _m = _render(env, macros, "END_PRINT", pr3)
    assert "G1 Z2 F600" not in out3
    park3, _m = _render(env, macros, "_PI_END_PARK", pr3)
    assert not [l for l in park3 if l.startswith("G1 Z")], park3
    # nicht referenziert: keine Bewegung, kein Entladen, Box-Zustand trotzdem
    pr4 = _rprinter(param, kcfg, k2_box=k2, pause_resume={"is_paused": False},
                    toolhead={"homed_axes": ""})
    out4, _m = _render(env, macros, "END_PRINT", pr4)
    assert "KTWO_BOX_END_PRINT" not in out4 and "KTWO_BOX_PRINT_END" in out4 \
        and not [l for l in out4 if l.startswith("G1")], out4
    assert _render(env, macros, "_PI_END_PARK", pr4)[0] == []
    # ohne Freigabe: weder Entladen noch Box-Zustand, Z+2/Park bleiben
    pr5 = _rprinter(param, kcfg, k2_box=dict(k2, change_enabled=False),
                    pause_resume={"is_paused": False})
    out5, _m = _render(env, macros, "END_PRINT", pr5)
    assert not [l for l in out5 if l.startswith("KTWO_BOX")] \
        and "G1 Z2 F600" in out5 and "_PI_END_PARK" in out5, out5
    # START_PRINT: Box-Druckzustand vor Heizen/Homing, nur mit Freigabe
    pr6 = _rprinter(param, kcfg, pause_resume={"is_paused": False})
    st, _m = _render(env, macros, "START_PRINT", pr6,
                     {"EXTRUDER_TEMP": "220", "BED_TEMP": "50"})
    assert st.index("KTWO_BOX_PRINT_START") < st.index("G28"), st
    pr7 = _rprinter(param, kcfg, k2_box={"change_enabled": False},
                    pause_resume={"is_paused": False})
    st7, _m = _render(env, macros, "START_PRINT", pr7,
                      {"EXTRUDER_TEMP": "220", "BED_TEMP": "50"})
    assert "KTWO_BOX_PRINT_START" not in st7
    return ("END_PRINT: Z+2 -> KTWO_BOX_END_PRINT -> KTWO_BOX_PRINT_END -> "
            "_PI_END_PARK (E-1/E-2, Z max_z/2) -> Heizungen aus; Grenzen/"
            "ungehomt/ohne Freigabe; START_PRINT -> KTWO_BOX_PRINT_START")


# =====================================================================
# Druckabbruch (18.10.2026, C2–C6)
# =====================================================================

def _cancel_base(printer):
    """= Upstream CANCEL_PRINT_BASE (do_cancel + CLEAR_PAUSE)."""
    printer.paused = False
    printer.print_state = "cancelled"
    printer._refresh()


def _cancel_print(printer, mod, end_params=None):
    """= macros.cfg CANCEL_PRINT (Kern): END_PRINT CANCEL=1 ->
    KTWO_BOX_END_PRINT CANCEL=1, CANCEL_PRINT_BASE, KTWO_BOX_CANCEL_RESET.
    -> (infos END_PRINT, infos CANCEL_RESET, chain_error vor dem Reset)."""
    g = FakeGcmd(dict({"CANCEL": "1"}, **(end_params or {})))
    mod.cmd_end_print(g)
    err_before = mod.get_status()["chain_error"]
    _cancel_base(printer)
    g2 = FakeGcmd({})
    mod.cmd_cancel_reset(g2)
    return g.infos, g2.infos, err_before


def test_c3_pause_cancel_neuer_druck():
    # 1) Abbruch im Einzug, Kopf LEER (Schritt EXTRUDE): Pause + Sperre
    bus, printer, mod = _hull_abort_leer()
    st = mod.get_status()
    assert printer.paused and "EXTRUDE fehlgeschlagen" in st["chain_error"] \
        and st["chain_error_step"] == "EXTRUDE" and st["change_aborted"], st
    # In der Pause: weder CANCEL_RESET noch PRINT_START lockern die Sperre
    g = FakeGcmd({})
    mod.cmd_cancel_reset(g)
    assert any("nichts zurückgesetzt" in m for m in g.infos), g.infos
    g = FakeGcmd({"START": "1"})
    mod.cmd_cancel_reset(g)
    assert mod.get_status()["chain_error"] and mod.get_status()["change_aborted"]
    mod.cmd_print_start(FakeGcmd({}))
    assert mod.get_status()["chain_error"], "PRINT_START in der Pause"
    _check_raises(mod.cmd_resume_check, {"MOVE": "1"}, ["RESUME gesperrt"])
    # Ohne CANCEL=1 (END_PRINT von Hand): wie bisher übersprungen
    g = FakeGcmd({})
    mod.cmd_end_print(g)
    assert any("übersprungen – Wechsel aktiv oder Wechsel-Fehler offen" in m
               for m in g.infos), g.infos
    # CANCEL_PRINT: Kopf leer -> nicht entladen (Meldung), Sperre steht bis
    # CANCEL_PRINT_BASE, danach zurückgesetzt
    n, nb = len(printer.gcode.scripts), len(bus.calls)
    inf, rinf, err = _cancel_print(printer, mod)
    assert err, "chain_error bleibt bis nach CANCEL_PRINT_BASE"
    assert any("NICHT entladen" in m and "Schritt EXTRUDE" not in m
               and "kein Filament" in m for m in printer.gcode.raw), \
        printer.gcode.raw[-2:]
    assert not [l for l in printer.gcode.scripts[n:]
                if l.startswith(("G0", "G1", "M109"))], printer.gcode.scripts[n:]
    # einziger Frame: Box-ERROR quittieren (wie KTWO_BOX_ERROR_CLEAR)
    assert [(c[1], tuple(c[2])) for c in bus.calls[nb:]] == [(0x04, (0, 1))]
    st = mod.get_status()
    assert st["chain_error"] == "" and not st["change_aborted"] \
        and st["pending_resume_z"] is None and st["chain_error_step"] == "" \
        and not st["chain_active"], st
    assert any("Druckabbruch (CANCEL_PRINT): zurückgesetzt" in m
               and "clear_resume_tnn" in m for m in rinf), rinf
    # 2) Neuer Druck: T0 (T1A) läuft sofort durch (vorher: PRE_OPT lehnte ab)
    bus.fail_after_mm = None
    bus.head_at_mm = bus.odo + 100.0
    printer.set_printing(True)
    g = FakeGcmd({})
    mod.cmd_print_start(g)
    assert not any("zurückgesetzt" in m for m in g.infos), "nichts mehr offen"
    n = len(printer.gcode.scripts)
    run_chain(mod, "T1A")
    st = mod.get_status()
    assert st["chain_error"] == "" and st["loaded_tnn"] == "T1A" \
        and st["head_filament"] is True and not printer.paused, st
    assert not [l for l in printer.gcode.scripts[n:] if l.startswith("PAUSE")]
    # 3) Fehler beim SPÜLEN, Kopf VOLL -> CANCEL entlädt wie Stock box_end
    bus, printer, mod = _hull_t1b_ok()
    printer.set_printing(True)
    _set_pos(printer, 110, 120, 10)
    printer.fail_on = "G1 E18"
    run_chain(mod, "T1B")
    st = mod.get_status()
    assert printer.paused and st["chain_error_step"] == "FLUSH" \
        and st["head_filament"] is True, st
    printer.fail_on = None
    n, nb = len(printer.gcode.scripts), len(bus.calls)
    bus.trace = []
    inf, rinf, err = _cancel_print(printer, mod)
    sc = printer.gcode.scripts[n:]
    assert "FLUSH fehlgeschlagen" in err, "Sperre während des Entladens"
    assert sc[0] == "KTWO_BOX_PRE_OPT Z_HOP=0" and "G0 X-7.8 Y200 F30000" in sc \
        and "KTWO_BOX_RETRUDE" in sc, sc[:4]
    assert _idx(bus.trace, _b(0x11, (0x02, 0x01))) >= 0
    assert any("entladen wie Stock box_end" in m for m in inf), inf
    assert any("geschnitten und in die Box zurückgezogen" in m for m in inf)
    assert not [l for l in sc if l.startswith("PAUSE")]
    st = mod.get_status()
    assert st["chain_error"] == "" and not st["change_aborted"] \
        and st["head_filament"] is False, st
    # 4) Fehler beim SCHNITT (Kopf voll): NICHT automatisch schneiden
    bus, printer, mod = _hull_t1b_ok()
    printer.set_printing(True)
    _set_pos(printer, 110, 120, 10)
    printer.fail_on = "G4 P"
    run_chain(mod, "T1B")
    assert mod.get_status()["chain_error_step"] == "CUT" and printer.paused
    printer.fail_on = None
    n, nb = len(printer.gcode.scripts), len(bus.calls)
    inf, rinf, err = _cancel_print(printer, mod)
    sc = printer.gcode.scripts[n:]
    assert "G0 X-7.8 Y200 F30000" not in sc, sc
    assert [(c[1], tuple(c[2])) for c in bus.calls[nb:]] == [(0x04, (0, 1))]
    assert any("NICHT entladen" in m and "Schritt CUT" in m
               for m in printer.gcode.raw), printer.gcode.raw[-1:]
    assert mod.get_status()["chain_error"] == ""
    # 5) Abbruch scheitert vor CANCEL_PRINT_BASE (Pause bleibt): Sperre bleibt
    bus, printer, mod = _hull_abort_leer()
    mod.cmd_end_print(FakeGcmd({"CANCEL": "1"}))
    mod.cmd_cancel_reset(FakeGcmd({}))      # noch pausiert -> nichts
    assert mod.get_status()["chain_error"] and printer.paused
    _check_raises(mod.cmd_resume_check, {"MOVE": "1"}, ["RESUME gesperrt"])
    # 6) Rückfall KTWO_BOX_PRINT_START (Druck endete ohne CANCEL_RESET)
    printer.paused = False
    printer.print_state = "printing"
    printer._refresh()
    g = FakeGcmd({})
    mod.cmd_print_start(g)
    assert any("neuer Druck (KTWO_BOX_PRINT_START): zurückgesetzt" in m
               for m in g.infos), g.infos
    assert mod.get_status()["chain_error"] == "" \
        and not mod.get_status()["change_aborted"]
    # 7) M8200-Kette aus der Datei (offene Kette + Fehler): CANCEL räumt sie
    bus, printer, mod = _hull_t1b_ok()
    printer.set_printing(True)
    _set_pos(printer, 110, 120, 10)
    printer.fail_on = "G4 P"
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1B"}))
    mod.cmd_cut(FakeGcmd({}))               # M8200 C scheitert -> Pause
    assert printer.paused and mod.get_status()["chain_active"]
    printer.fail_on = None
    inf, rinf, err = _cancel_print(printer, mod)
    assert any("offene Kette T1B aufgeräumt" in m for m in inf), inf
    st = mod.get_status()
    assert not st["chain_active"] and st["chain_error"] == "", st
    return ("Pause+Fehler: Sperre bleibt in der Pause (CANCEL_RESET/PRINT_START"
            "/RESUME), CANCEL: Kopf leer bzw. Fehler im Schnitt -> nur Meldung, "
            "Fehler beim Spülen + Kopf voll -> entladen; Reset erst nach "
            "CANCEL_PRINT_BASE; neuer Druck T0 ok; Rückfall PRINT_START; "
            "offene M8200-Kette")


def test_c4_c5_c6_makros():
    try:
        env, macros, kcfg, param = _render_env()
    except ImportError:
        return "SKIP (jinja2 fehlt)"
    # C4: _PI_RESET_STATE -> KTWO_BOX_PRINT_END nur mit print_hold
    pr = _rprinter(param, kcfg, k2_box={"print_hold": True})
    out, _m = _render(env, macros, "_PI_RESET_STATE", pr)
    assert out.index("CLEAR_PAUSE") < out.index("KTWO_BOX_PRINT_END"), out
    pr = _rprinter(param, kcfg, k2_box={"print_hold": False})
    assert "KTWO_BOX_PRINT_END" not in _render(env, macros, "_PI_RESET_STATE",
                                               pr)[0]
    pr = _rprinter(param, kcfg)
    del pr["k2_box"]
    assert "KTWO_BOX_PRINT_END" not in _render(env, macros, "_PI_RESET_STATE",
                                               pr)[0]
    # C4 Hülle: Fehler beim Box-Zustand nur Meldung (kein Fehler)
    bus, printer, mod = build_hull(allow_write="True")
    mod.cmd_print_start(FakeGcmd({}))
    assert mod.get_status()["print_hold"]
    bus_calls = len(bus.calls)
    orig = bus.transact_full

    def boom(*a, **k):
        raise RuntimeError("Bus weg")
    bus.transact_full = boom
    mod.cmd_print_end(FakeGcmd({}))
    bus.transact_full = orig
    assert any("KTWO_BOX_PRINT_END fehlgeschlagen" in m
               for m in printer.gcode.raw), printer.gcode.raw
    assert not mod.get_status()["print_hold"] and len(bus.calls) == bus_calls
    # CANCEL_PRINT-Makro: END_PRINT CANCEL=1 -> BASE -> CANCEL_RESET
    pr = _rprinter(param, kcfg)
    out, _m = _render(env, macros, "CANCEL_PRINT", pr)
    assert out == ["END_PRINT CANCEL=1", "CANCEL_PRINT_BASE",
                   "KTWO_BOX_CANCEL_RESET"], out
    k2 = {"end_print_unload": True, "ending_material": False}
    pr = _rprinter(param, kcfg, k2_box=k2, pause_resume={"is_paused": False})
    out, _m = _render(env, macros, "END_PRINT", pr, {"CANCEL": "1"})
    assert "KTWO_BOX_END_PRINT CANCEL=1" in out, out
    out, _m = _render(env, macros, "END_PRINT", pr)
    assert "KTWO_BOX_END_PRINT" in out and "KTWO_BOX_END_PRINT CANCEL=1" \
        not in out
    # C5: nach _WAIT_TEMP_START Bauteillüfter S127 (Stock), Rest unverändert
    i = out.index("_WAIT_TEMP_START")
    assert out[i + 1] == "M106 P0 S127", out[i:]
    assert _render(env, macros, "_WAIT_TEMP_START", pr)[0][0] == "M106 P0 S255"
    # C6: Z+2 nur unter max_z - 5 (Stock END_PRINT_Z_SAFE)
    mz = float(param["max_z_position"])
    for z, want in ((mz - 5.5, True), (mz - 5.0, False), (mz - 3.0, False)):
        prz = _rprinter(param, kcfg, k2_box=k2,
                        pause_resume={"is_paused": False},
                        toolhead={"position": {"x": 1.0, "y": 1.0, "z": z}})
        got = "G1 Z2 F600" in _render(env, macros, "END_PRINT", prz)[0]
        assert got is want, (z, got)
    # START_PRINT ohne Freigabe: nur Zurücksetzen (C3-Rückfall)
    pr7 = _rprinter(param, kcfg, k2_box={"change_enabled": False},
                    pause_resume={"is_paused": False})
    st7, _m = _render(env, macros, "START_PRINT", pr7,
                      {"EXTRUDER_TEMP": "220", "BED_TEMP": "50"})
    assert "KTWO_BOX_CANCEL_RESET START=1" in st7 and "KTWO_BOX_PRINT_START" \
        not in st7, st7
    assert st7.index("KTWO_BOX_CANCEL_RESET START=1") > st7.index("CLEAR_PAUSE")
    # Hülle: CANCEL_RESET START=1 im laufenden Druck erlaubt, ohne START nicht
    bus, printer, mod = _hull_abort_leer()
    printer.paused = False
    printer.print_state = "printing"
    printer._refresh()
    mod.cmd_cancel_reset(FakeGcmd({}))
    assert mod.get_status()["chain_error"], "ohne START im Druck: nichts"
    mod.cmd_cancel_reset(FakeGcmd({"START": "1"}))
    assert mod.get_status()["chain_error"] == ""
    return ("_PI_RESET_STATE -> KTWO_BOX_PRINT_END nur mit print_hold (Fehler "
            "nur Meldung); CANCEL_PRINT/END_PRINT CANCEL=1; M106 P0 S127 nach "
            "_WAIT_TEMP_START; Z+2 < max_z-5; START_PRINT ohne Freigabe -> "
            "CANCEL_RESET START=1")


class _FakeWebhooks:
    def __init__(self):
        self.calls = []
        self._endpoints = {"pause_resume/cancel": self._cancel}
        self.during = None

    def _cancel(self, web_request):
        self.calls.append(web_request)
        if self.during is not None:
            self.during()


def test_c2_abbruch_flag():
    # 1) Einhüllung beim Connect, Flag nur während des Originals
    bus = FakeBoxBus()
    printer = FakePrinter(bus, with_motion=True)
    wh = _FakeWebhooks()
    printer.objects["webhooks"] = wh
    mod = K.PrinterK2Box(FakeConfig(printer, test_options(
        allow_move="True")))
    printer.fire("klippy:connect")
    mod._bus = bus
    assert mod.get_status()["cancel_hook"] == "eingehüllt"
    seen = []
    printer.set_printing(True)
    wh.during = lambda: seen.append((mod.get_status()["cancel_pending"],
                                     mod._cancel_requested()))
    wh._endpoints["pause_resume/cancel"]("req")
    assert wh.calls == ["req"] and seen == [(True, True)], seen
    assert not mod.get_status()["cancel_pending"]
    # Flag auch nach einer Ausnahme im Original weg; zweiter Connect hüllt
    # nicht doppelt ein
    def boom():
        raise RuntimeError("CANCEL_PRINT kaputt")
    wh.during = boom
    try:
        wh._endpoints["pause_resume/cancel"]("x")
    except RuntimeError:
        pass
    assert mod._cancel_req is None
    w1 = wh._endpoints["pause_resume/cancel"]
    printer.fire("klippy:connect")
    assert wh._endpoints["pause_resume/cancel"] is w1
    # ohne webhooks / Option aus: nur Status
    _b2, _p2, m2 = build_hull()
    assert "nicht möglich" in m2.get_status()["cancel_hook"]
    _b3, p3, m3 = build_hull(cancel_interrupt="False")
    p3.objects["webhooks"] = _FakeWebhooks()
    p3.fire("klippy:connect")
    assert "cancel_interrupt: False" in m3.get_status()["cancel_hook"]
    assert K.parse_options(test_options())["cancel_interrupt"] is True
    # Flag wirkt nur im Druck und nie in KTWO_BOX_END_PRINT
    mod._cancel_req = 1.0
    printer.set_printing(False)
    assert not mod._cancel_requested()
    printer.set_printing(True)
    mod._end_print = True
    assert not mod._cancel_requested()
    mod._end_print = False
    mod._cancel_req = None

    # 2) Einzug wartet auf den Kopfsensor -> Abbruch: 0x04 [00 01], kein
    #    mode 6/7, keine PAUSE, kein Wechsel-Fehler, Rest der Kette leer
    bus, printer, mod = build_hull(allow_write="True")
    bus.hub_mask, bus.head, bus.head_at_mm = 0x00, False, None
    printer.set_printing(True)
    _set_pos(printer, 110, 120, 10)
    sens = printer.sensor
    cnt = {"n": 0}
    orig_gs = sens.get_status

    def gs(eventtime=None):
        cnt["n"] += 1
        if cnt["n"] == 12:
            mod._cancel_req = 5.0           # Webhook kommt mitten im Einzug
        return orig_gs(eventtime)
    sens.get_status = gs
    bus.trace = []
    infos = run_chain(mod, "T1A")
    sc = printer.gcode.scripts
    funs = [(c[1], tuple(c[2])) for c in bus.calls]
    i_last5 = max(i for i, f in enumerate(funs)
                  if f[0] == 0x10 and f[1][1] == 0x05)
    assert funs[i_last5 + 1] == (0x04, (0x00, 0x01)), funs[i_last5:]
    assert not [f for f in funs if f[0] == 0x10 and f[1][1] in (0x06, 0x07)]
    assert not [l for l in sc if l.startswith("PAUSE")], sc
    st = mod.get_status()
    assert st["chain_error"] == "" and not st["change_aborted"] \
        and not st["chain_active"] and not printer.paused, st
    assert any("EXTRUDE beendet" in m and "Druckabbruch" in m
               for m in infos), infos
    assert any("FLUSH übersprungen" in m for m in infos), infos
    assert not [l for l in sc if l.startswith("G1 E18")], "kein Spülen"
    # ... CANCEL_PRINT danach: Entladen läuft trotz Flag (Kopf leer -> nichts)
    g = FakeGcmd({"CANCEL": "1"})
    mod.cmd_end_print(g)
    assert any("Kopf-Filamentsensor leer" in m for m in g.infos), g.infos
    mod._cancel_req = None

    # 3) Spülen: Abbruch nach dem ersten Abschnitt -> kein zweiter, keine
    #    Pause, Düsen-Material NICHT übernommen
    bus, printer, mod = _hull_t1b_ok()
    printer.set_printing(True)
    _set_pos(printer, 110, 120, 10)
    rm = dict(mod.remain_material)
    orig_sim = printer.sim

    def sim(line):
        orig_sim(line)
        if line.startswith("G1 E18"):
            mod._cancel_req = 7.0
    printer.sim = sim
    infos = run_chain(mod, "T1B")
    sc = printer.gcode.scripts
    assert "G1 E18 F360" in sc and not [l for l in sc if l.startswith("G1 E14")]
    assert not [l for l in sc if l.startswith("PAUSE")]
    assert mod.remain_material == rm and mod.get_status()["chain_error"] == ""
    assert any("FLUSH beendet" in m for m in infos), infos
    assert "SET_VELOCITY_LIMIT ACCEL=20000" in sc[sc.index("G1 E18 F360"):]
    mod._cancel_req = None

    # 4) Abbruch während M109 im Einzug (nicht unterbrechbar): der nächste
    #    Wartepunkt (Box-Zustand) beendet den Schritt, kein 0x10-Frame
    bus, printer, mod = build_hull(allow_write="True")
    bus.head_at_mm = 1000.0
    printer.set_printing(True)
    orig_sim = printer.sim
    trip = {"on": False}

    def sim2(line):
        orig_sim(line)
        if line.startswith("M109") and not trip["on"]:
            trip["on"] = True
            mod._cancel_req = 9.0
    printer.sim = sim2
    infos = run_chain(mod, "T1A")
    assert trip["on"] and not printer.paused
    assert not [c for c in bus.calls if c[1] == 0x10], "kein Einzug"
    assert any("EXTRUDE beendet" in m for m in infos), infos
    assert mod.get_status()["chain_error"] == ""
    mod._cancel_req = None
    # 4b) Echter Fehler bei wartendem Abbruch (direkt über _fail): keine
    #     PAUSE, Fehler gemerkt (Entlade-Prüfung), CANCEL_RESET löscht ihn
    # Fehler direkt über _fail mit Flag
    bus, printer, mod = build_hull(allow_write="True")
    printer.set_printing(True)
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1A"}))
    mod._cancel_req = 3.0
    mod._fail(FakeGcmd({}), "EXTRUDE", "0x0c Testfehler")
    st = mod.get_status()
    assert not printer.paused and st["chain_error_step"] == "EXTRUDE" \
        and st["chain_skip"], st
    assert any("Druckabbruch (CANCEL) wartet: keine Pause" in m
               for m in printer.gcode.raw)
    mod._cancel_req = None
    mod.cmd_end_opt(FakeGcmd({}))
    _cancel_base(printer)
    mod.cmd_cancel_reset(FakeGcmd({}))
    assert mod.get_status()["chain_error"] == ""

    # 5) Controller: wait_box_ready (Preload läuft) endet mit BoxCancelled
    cfg, bus, ctl = build_system()
    bus.state, bus.preload_polls = 1, 50
    hits = {"n": 0}

    def chk():
        hits["n"] += 1
        return hits["n"] >= 3
    ctl.cancel_check = chk
    try:
        ctl.wait_box_ready(380.0, 1.0)
    except K.BoxCancelled as exc:
        assert "Druckabbruch" in str(exc)
    else:
        raise AssertionError("wait_box_ready hätte abbrechen müssen")
    assert issubclass(K.BoxCancelled, K.BoxChangeError)

    # 6) Nachholen im RESUME bzw. Runout: Abbruch -> nie weiterdrucken
    bus, printer, mod = _hull_abort_leer(resume_redo_change="True")
    mod.cmd_error_clear(FakeGcmd({}))
    bus.fail_after_mm = None
    bus.head_at_mm = bus.odo + 100.0
    mod._cancel_req = 4.0
    _check_raises(mod.cmd_resume_check, {"MOVE": "1"},
                  ["RESUME abgebrochen", "Druckabbruch"])
    assert printer.paused
    mod._cancel_req = None
    return ("Webhook eingehüllt (Flag nur während CANCEL, auch bei Ausnahme "
            "weg, kein Doppel-Hook, Option/kein webhooks), Einzug -> 0x04 "
            "IDLE ohne mode 6/7, Spülen nach Abschnitt, kein PAUSE/Fehler, "
            "Fehler bei wartendem Abbruch ohne Pause, wait_box_ready, "
            "Nachholen gesperrt, END_PRINT-Entladen ignoriert das Flag")


def test_einzel_rueckzug_sensor_gesperrt():
    """22.10.2026 am Gerät: KTWO_BOX_RETRUDE von Hand (ohne Druck) löste über
    pause_on_runout eine PAUSE aus. Einzel-RETRUDE/UNLOAD sperren den
    Kopfsensor jetzt wie die Kette und geben ihn danach wieder frei."""
    bus, printer, mod = _material_hull()
    bus.hub_mask, bus.head = 0x08, True
    s0 = len(printer.gcode.scripts)
    mod.cmd_retrude(FakeGcmd({}))
    sc = printer.gcode.scripts[s0:]
    off = "SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=0"
    on = "SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=1"
    assert off in sc and on in sc, sc
    heat = [i for i, l in enumerate(sc) if l.startswith("M109")]
    assert sc.index(off) < (heat[0] if heat else len(sc)), sc
    assert sc.index(on) == max(i for i, l in enumerate(sc) if l == on), sc
    assert printer.sensor.enabled, "Kopfsensor muss danach wieder aktiv sein"
    # war er vorher aus, bleibt er aus (kein ungewolltes Einschalten)
    bus, printer, mod = _material_hull()
    bus.hub_mask, bus.head = 0x08, True
    printer.sensor.enabled = False
    s0 = len(printer.gcode.scripts)
    mod.cmd_retrude(FakeGcmd({}))
    sc = printer.gcode.scripts[s0:]
    assert not any(l.startswith("SET_FILAMENT_SENSOR") for l in sc), sc
    assert not printer.sensor.enabled
    return "RETRUDE ohne Kette: Sensor aus vor dem Heizen/Rückzug, danach wieder an; war er aus, bleibt er aus"


def test_schnittschutz_nach_spuelen():
    """22.10.2026 am Gerät: KTWO_BOX_RETRUDE direkt nach KTWO_BOX_FLUSH ohne
    Schnitt -> Box-Status 0x19 (Pull-back: Motorblock > 10 s). Einzel-RETRUDE/
    UNLOAD lehnen jetzt ab, bis KTWO_BOX_CUT lief (FORCE=1 übergeht); Laden
    und die Kette bleiben unberührt."""
    def _refused(fn, params):
        n_bus, n_sc = len(bus.calls), len(printer.gcode.scripts)
        g = FakeGcmd(params)
        try:
            fn(g)
        except ValueError as exc:
            assert "abgelehnt" in str(exc) and "KTWO_BOX_CUT" in str(exc) \
                and "FORCE=1" in str(exc), exc
        else:
            raise AssertionError("%s nicht abgelehnt" % (params,))
        assert len(bus.calls) == n_bus, "nichts gesendet"
        # keine Bewegung/Heizung (UNLOAD schaltet wie die Kälte-Ablehnung
        # nur den Kopfsensor kurz aus und wieder an)
        assert not [l for l in printer.gcode.scripts[n_sc:]
                    if not l.startswith("SET_FILAMENT_SENSOR")], \
            printer.gcode.scripts[n_sc:]
        if fn == mod.cmd_retrude:
            assert len(printer.gcode.scripts) == n_sc

    def _retruded(n_bus):
        return [c for c in bus.calls[n_bus:] if c[1] == 0x11]

    # 1) FLUSH -> RETRUDE abgelehnt, FORCE=1 erlaubt (mit Warnung)
    bus, printer, mod = build_hull(allow_write="True")
    assert mod.get_status()["cut_needed"] == ""
    mod.cmd_flush(FakeGcmd({"TNN": "T1B"}))
    assert mod.cut_needed == "dem Spülen T1B", mod.cut_needed
    assert mod.get_status()["cut_needed"] == "dem Spülen T1B"
    bus.hub_mask, bus.head = 0x02, True
    _refused(mod.cmd_retrude, {})
    _refused(mod.cmd_retrude, {"SLOT": "B"})
    n = len(bus.calls)
    g = FakeGcmd({"FORCE": "1"})
    mod.cmd_retrude(g)
    assert _retruded(n) and any("WARNUNG RETRUDE ohne Schnitt" in m
                                for m in g.infos), g.infos
    assert mod.cut_needed == "", "erfolgreicher Rückzug gibt frei"
    # 2) FLUSH -> CUT -> RETRUDE erlaubt
    mod.cmd_flush(FakeGcmd({"TNN": "T1B"}))
    assert mod.cut_needed
    bus.hub_mask, bus.head = 0x02, True
    mod.cmd_cut(FakeGcmd({}))
    assert mod.cut_needed == ""
    n = len(bus.calls)
    mod.cmd_retrude(FakeGcmd({}))
    assert _retruded(n)
    # 3) FLUSH -> UNLOAD abgelehnt, FORCE=1 erlaubt (Düse heiß: Kälteschutz
    #    zuerst, s. test_temperatur_guard)
    orig = printer._refresh

    def refresh():
        orig()
        printer.objects["extruder"].status.update(temperature=220.0,
                                                  can_extrude=True)
    printer._refresh = refresh
    mod.cmd_flush(FakeGcmd({"TNN": "T1B"}))
    bus.hub_mask, bus.head = 0x02, True
    _refused(mod.cmd_unload, {"SLOT": "B"})
    n = len(bus.calls)
    mod.cmd_unload(FakeGcmd({"SLOT": "B", "FORCE": "1"}))
    assert _retruded(n) and mod.cut_needed == ""
    # 4) Laden (EXTRUDE) setzt nichts; danach Rückzug ohne Schnitt (9.3 live)
    bus, printer, mod = build_hull(allow_write="True")
    bus.hub_mask, bus.head = 0x00, False
    bus.head_at_mm = 300.0
    bus.need_extruder_for_mode7 = True
    mod.cmd_extrude(FakeGcmd({"TNN": "T1C"}))
    assert mod.cut_needed == "" and bus.hub_mask == 0x04, (mod.cut_needed,
                                                          bus.hub_mask)
    n = len(bus.calls)
    mod.cmd_retrude(FakeGcmd({}))
    assert _retruded(n)
    # ... auch ein offener Merker wird durch frisches Laden frei
    mod.cut_needed = "dem Spülen T1C"
    bus.hub_mask, bus.head = 0x00, False
    mod.cmd_extrude(FakeGcmd({"TNN": "T1C"}))
    assert mod.cut_needed == ""
    # 5) Kette unberührt: RETRUDE in der Kette auch ohne vorherigen CUT
    bus, printer, mod = build_hull(allow_write="True")
    bus.hub_mask, bus.head = 0x01, True
    bus.head_at_mm = 300.0
    bus.need_extruder_for_mode7 = True
    mod.cut_needed = "dem Spülen T1A"
    mod.cmd_pre_opt(FakeGcmd({"TNN": "T1B"}))
    n = len(bus.calls)
    mod.cmd_retrude(FakeGcmd({}))
    assert _retruded(n) and mod.get_status()["chain_error"] == ""
    mod.cmd_end_opt(FakeGcmd({}))
    infos = run_chain(mod, "T1C")
    assert not [m for m in infos if "abgelehnt" in m], infos
    st = mod.get_status()
    assert st["loaded_tnn"] == "T1C" and st["chain_error"] == "", st
    assert mod.cut_needed == "dem Spülen T1C", "Kette endet mit Spülen"
    # 6) allow_write=False: Plan-Pfad unverändert (kein Schutz, nichts gesendet)
    bus, printer, mod = build_hull(allow_write="False")
    bus.hub_mask, bus.head = 0x02, True
    mod.cut_needed = "dem Spülen T1B"
    n = len(bus.calls)
    g = FakeGcmd({})
    mod.cmd_retrude(g)
    assert not _retruded(n) and not [m for m in g.infos if "abgelehnt" in m]
    # 7) Druckbeginn setzt den Merker; im Druck lehnt RETRUDE per PAUSE ab
    bus, printer, mod = build_hull(allow_write="True")
    mod.cmd_print_start(FakeGcmd({}))
    assert mod.cut_needed == "dem Druckbeginn", mod.cut_needed
    printer.set_printing(True)
    bus.hub_mask, bus.head = 0x02, True
    n = len(bus.calls)
    mod.cmd_retrude(FakeGcmd({}))
    assert not _retruded(n)
    assert any(l.startswith("PAUSE") for l in printer.gcode.scripts), \
        printer.gcode.scripts[-5:]
    assert "abgelehnt" in mod.get_status()["chain_error"]
    return ("FLUSH -> RETRUDE/UNLOAD abgelehnt (nichts gesendet), FORCE=1 mit "
            "Warnung; CUT bzw. Rückzug geben frei; Laden setzt nichts/gibt "
            "frei; Kette unberührt; allow_write=False unverändert; Druckbeginn "
            "setzt, im Druck PAUSE")


# Creality-Print-7-Dateien (22.10.2026, README-k2_box.md §19): Zeilen
# 1:1 aus einer CP7.3-Mehrfarbdatei (Druckbahnen/Turm gekürzt). Jeder Wechsel
# = M8200 P/R E-30/C/R/L + T<n>, danach spült die DATEI selbst, M8200 O.
_CP7_START = """
M83
M8200 P S0
M220 S100
G0 Y200 F12000
G0 X10
SET_VELOCITY_LIMIT ACCEL=5000 ACCEL_TO_DECEL=25000
G0 F30000
M8200 C S0
SET_VELOCITY_LIMIT ACCEL=5000 ACCEL_TO_DECEL=5000
G0 Y345 F18000
G0 X139
G0 Y378
G0 X133
M8200 R
M104 S240
M8200 L I0
M106 S0
M106 P2 S0
T0
; FLUSH_START
M106 S30
G1 F60
M400
G1 E16.2 F573.733
G1 E1.8 F60
M400
M106 S255
G4 P5000
M106 S30
G1 E-2 F1800
; FLUSH_END
SET_VELOCITY_LIMIT ACCEL=5000 ACCEL_TO_DECEL=5000
G0 X160 F12000
G0 X135
G0 X160 Y374 F12000
G2 I4 J0 P1 F10000
G0 X170 Y374 F12000
G3 I-4 J0 P1 F10000
G0 X160 Y378 F12000
G0 X133 Y378 F12000
; FLUSH_START
G1 F60
M400
M104 S220
M400
M106 S255
G4 P5000
M106 S0
; FLUSH_END
G0 X133 Y378 F12000
M8200 O
M204 S2000
G1 Z3 F600
"""

_CP7_WECHSEL = """
; WIPE_TOWER_START
G17
G2 Z1 I0.86 J0.86 P1 F10000
G1 X0 Y245 F30000
M106 S255
M8200 P S1
M220 S100
M8200 R E-30
M400
G0 X10 Y200 F12000
SET_VELOCITY_LIMIT ACCEL=5000 ACCEL_TO_DECEL=25000
G0 F30000
M8200 C S0
SET_VELOCITY_LIMIT ACCEL=5000 ACCEL_TO_DECEL=5000
G0 Y345 F18000
G0 X139
G0 Y378
G0 X133
M8200 R
M104 S240
M8200 L I1
M400
G1 E40 F573
T1
; FLUSH_START
M106 S30
M104 S240
G1 F60
M400
G1 E36 F573
G1 E1.28 F60
M400
M106 S255
G4 P5000
M106 S30
G1 E-2 F1800
; FLUSH_END
SET_VELOCITY_LIMIT ACCEL=5000 ACCEL_TO_DECEL=5000
G0 X160 F12000
G0 X135
G0 X160 Y374
G2 I4 J0 P1 F10000
G0 X170 Y374 F12000
G3 I-4 J0 P1 F10000
G0 X160 Y378 F12000
G0 X133 Y378
; FLUSH_START
M104 S220
G1 F60
M400
G1 E15.86 F573
M400
M106 S255
G4 P5000
M106 S30
G1 E-2 F1800
; FLUSH_END
G0 X160
G0 Y345
M8200 O
M106 P2 S204
M106 S255
G0 Z1.000 F1200
"""


def _cp7_hull(hub, head):
    """Hülle wie test_wechsel_kette_voll + Druck läuft, Düse 220 °C (Ist
    lesbar, can_extrude) – für Datei-Abläufe über die Makros."""
    bus, printer, mod = build_hull(allow_write="True")
    bus.hub_mask, bus.head = hub, head
    bus.head_at_mm = 300.0
    bus.need_extruder_for_mode7 = True
    bus.trace = []
    orig = printer._refresh

    def _refresh():
        orig()
        if "extruder" in printer.objects:
            printer.objects["extruder"].status = {
                "target": printer.target, "temperature": 220.0,
                "can_extrude": True}
    printer._refresh = _refresh
    printer.target = 220.0
    printer.set_printing(True)
    return bus, printer, mod


def _cp7_run(text, bus, printer, mod):
    """Datei-Zeilen wie Klipper: Makros (M8200, T<n>, _KTWO_*) mit Jinja aus
    macros.cfg gerendert, KTWO_BOX_* an das Modul, Rest an die G-Code-
    Simulation. Ergebnis: Trace-Einträge ("datei"/"cmd"/"bus"/"gcode")."""
    env, macros, kcfg, param = _render_env()
    mvars = {n: dict(m[1]) for n, m in macros.items()}
    out = []

    def pv(v):
        v = str(v).strip("'").strip('"')
        try:
            return float(v) if "." in v else int(v)
        except ValueError:
            return v

    def pr():
        d = _rprinter(param, kcfg)
        d["k2_box"] = mod.get_status()
        d["pause_resume"] = {"is_paused": printer.paused}
        d["print_stats"] = {"state": printer.print_state}
        for n, vs in mvars.items():
            d["gcode_macro %s" % n] = {k: pv(v) for k, v in vs.items()}
        return d

    def run(line):
        line = line.split(";")[0].strip()
        if not line:
            return
        w = line.split()
        cmd = w[0].upper()
        prm = {}
        for tok in w[1:]:
            if "=" in tok:
                k, v = tok.split("=", 1)
                prm[k.upper()] = v
            else:
                prm[tok[0].upper()] = tok[1:]
        if cmd in ("M8200", "_KTWO_TOOL_CHANGE", "_KTWO_FAN_SAVE",
                   "_KTWO_FAN_RESTORE") or (cmd.startswith("T")
                                            and cmd[1:].isdigit()):
            lines, msgs = _render(env, macros, cmd, pr(), prm)
            out.append(("makro", line, msgs))
            for l in lines:
                run(l)
            return
        if cmd == "SET_GCODE_VARIABLE":
            mvars.setdefault(prm["MACRO"], {})[prm["VARIABLE"].lower()] = \
                prm["VALUE"]
            return
        if cmd in printer.gcode.commands:
            g = FakeGcmd(prm)
            n0 = len(bus.trace)
            printer.gcode.commands[cmd](g)
            out.append(("cmd", line, g.infos))
            out.extend(bus.trace[n0:])
            del bus.trace[n0:]
            return
        out.append(("datei", line))
        printer.sim(line)
        if bus.trace and bus.trace[-1] == ("gcode", line):
            bus.trace.pop()
    for ln in text.strip().splitlines():
        run(ln)
    return out


def test_retrude_kopf_leer_kalt():
    """Live 26.10.2026: T1 in der Pause, Kopf schon leer, Düse 140 °C ->
    die Extruder-Schritte des Rückzugs scheiterten („Extrude below minimum
    temp“). Jetzt: Kopf leer + kalt -> nur Box-Rückzug, kein G1 E, kein
    Heizen; Kopf voll -> heizt und fördert wie bisher."""
    for head, cold in ((False, True), (True, True), (False, False)):
        bus, printer, mod = build_hull(allow_write="True")
        bus.hub_mask, bus.head = 0x01, head
        orig = printer._refresh
        heiss = {"v": not cold}

        def _refresh(orig=orig, heiss=heiss):
            orig()
            if "extruder" in printer.objects:
                printer.objects["extruder"].status = {
                    "target": printer.target, "temperature":
                    220.0 if heiss["v"] else 140.0, "can_extrude": heiss["v"]}
        printer._refresh = _refresh
        sim = printer.sim

        def sim2(line, sim=sim, heiss=heiss):
            if line.startswith("M109"):
                heiss["v"] = True       # Heizen macht die Düse heiß
            return sim(line)
        printer.sim = sim2
        printer._refresh()
        n, nb = len(printer.gcode.scripts), len(bus.calls)
        g = FakeGcmd({})
        mod.cmd_retrude(g)
        sc = printer.gcode.scripts[n:]
        assert [c for c in bus.calls[nb:] if c[1] == 0x11], "Box-Rückzug"
        e_moves = [l for l in sc if l.startswith("G1 E")]
        skip = [m for m in g.infos if "Extruder-Rückzug ausgelassen" in m]
        if head is False and cold:
            assert skip and not e_moves and not [l for l in sc
                                                 if l.startswith("M109")], sc
        else:
            assert not skip and e_moves, (head, cold, sc, g.infos)
        assert any("RETRUDE Slot A ok" in m for m in g.infos), g.infos
        assert mod.chain_error == "", mod.chain_error
    return ("Kopf leer + Düse kalt: nur Box-Rückzug (kein G1 E, kein Heizen); "
            "Kopf voll: heizt + Extruder-Schritte; Kopf leer + heiß: wie bisher")


def test_m8200_abbruch_resume():
    """Live 26.10.2026: Creality-Print-Block, Box beim vollen Rückzug (M8200 R
    nach dem Schnitt) stromlos -> PAUSE an der Schachtposition (nicht der
    Druckposition vor dem Block); RESUME mit abgeschnittenem Faden am
    Kopfsensor gesperrt; T1 in der Pause; RESUME öffnet Kette + Fahrgrenzen
    wieder -> Rest des Blocks (T1 leer, Wischen Y374/378) bis M8200 O."""
    try:
        import jinja2  # noqa: F401
    except Exception:
        return "SKIP (jinja2 fehlt)"
    bus, printer, mod, lim, seen = _m8200_hull()
    zeilen = _CP7_WECHSEL.strip().splitlines()
    i_r = zeilen.index("M8200 R")
    _cp7_run("\n".join(zeilen[:i_r]), bus, printer, mod)
    assert mod.head_cut and mod.get_status()["head_cut"], "Schnitt gemerkt"
    assert [round(v, 3) for v in printer.pos[:2]] == [133.0, 378.0]
    _m8200_rest_der_pruefung(bus, printer, mod, lim, seen, zeilen, i_r)
    _m8200_weitere_faelle()
    return ("Box beim Rückzug stromlos: PAUSE an der Schachtposition, RESUME "
            "mit abgeschnittenem Faden gesperrt, T1 in der Pause, RESUME "
            "öffnet Kette + Fahrgrenzen bis M8200 O (Rest leer, Sensor "
            "gesperrt), Wischen Y374/378 frei; Abbruch beim Schnitt -> Ziel "
            "X10 Y200, kein zweiter Wechsel; Hand-PAUSE im Block; END_OPT in "
            "der Pause/END_OPT-Fehler")


def _m8200_hull():
    """CP7-Hülle + echte Fahrgrenzen ([k2_limits]-Fake) + Box „stromlos“
    (bus.tot) + Mitschnitt der Fahrten mit Grenzen-Zustand."""
    bus, printer, mod = _cp7_hull(0x01, True)
    lim = _LimFake(printer)
    printer.objects["k2_limits"] = lim
    orig_lookup = printer.lookup_object

    def lookup(name, default=KeyError):
        if name in printer.objects:
            return printer.objects[name]
        if default is KeyError:
            return orig_lookup(name)
        return default
    printer.lookup_object = lookup
    seen = []
    sim = printer.sim

    def sim_rec(line):
        if line.startswith(("G0", "G1")):
            seen.append((line, lim.core.is_wide()))
        return sim(line)
    printer.sim = sim_rec
    orig_handle = bus._handle

    def handle(addr, fun, payload):
        if getattr(bus, "tot", False) and fun not in (0xA0, 0xA1, 0xA2, 0xA3):
            raise TimeoutError("Box stromlos")
        return orig_handle(addr, fun, payload)
    bus._handle = handle
    return bus, printer, mod, lim, seen


def _m8200_rest_der_pruefung(bus, printer, mod, lim, seen, zeilen, i_r):
    # 1) Box stromlos beim vollen Rückzug -> PAUSE ohne Rückfahrziel zur
    #    Druckposition X0 Y245 (der Kopf steht, wo die Datei ihn erwartet)
    bus.tot = True
    sc = printer.gcode.scripts
    n = len(sc)
    _cp7_run("M8200 R", bus, printer, mod)
    pause = [l for l in sc[n:] if l.startswith("PAUSE")]
    assert len(pause) == 1 and "CFS_RETURN" not in pause[0], sc[n:]
    assert printer.paused and mod._m8200_offen == "T1B"
    st = mod.get_status()
    assert "RETRUDE fehlgeschlagen" in st["chain_error"] and st["head_cut"]
    # 2) Fehler quittiert, Kopfsensor meldet den abgeschnittenen Faden ->
    #    RESUME gesperrt (vorher: frei)
    g = FakeGcmd({})
    mod.cmd_error_clear(g)
    assert any("abgeschnitten" in m for m in g.infos), g.infos
    _check_raises(mod.cmd_resume_check, {"MOVE": "1"}, [
        "RESUME gesperrt", "abgeschnittene Faden", "T1 (lädt T1B)"])
    # G-Code-Fehler in der Pause setzt die Fahrgrenzen zurück ([k2_limits])
    lim.core.restore_all("G-Code-Fehler")
    # 3) Box wieder da, T1 in der Pause: A zurück, B geladen
    bus.tot = False
    t = _cp7_run("T1", bus, printer, mod)
    fl = [e for e in t if e[0] == "cmd" and e[1].startswith("KTWO_BOX_FLUSH")]
    assert fl and any("FLUSH T1B ausgelassen" in m for m in fl[0][2]), fl
    st = mod.get_status()
    assert st["loaded_tnn"] == "T1B" and not st["chain_active"] \
        and not st["head_cut"] and st["chain_error"] == "", st
    assert mod._m8200_offen == "T1B", "T<n>-Kette schließt den Block nicht"
    g = FakeGcmd({"MOVE": "1"})
    mod.cmd_resume_check(g)
    assert any("RESUME frei" in m for m in g.infos), g.infos
    # 4) RESUME: Reinigung/Rückfahrt setzen die Fahrgrenzen zurück
    #    (KTWO_LIMITS_RESTORE = restore_all), RESUME_BASE, Ende RESUME:
    #    Kette (leer, B schon geladen) und Fahrgrenzen wieder offen,
    #    Kopfsensor gesperrt
    lim.core.restore_all("KTWO_LIMITS_RESTORE")
    printer.paused, printer.print_state = False, "printing"
    printer._refresh()
    g = FakeGcmd({})
    mod.cmd_resume_fix(g)
    st = mod.get_status()
    assert st["chain_active"] and st["chain_tnn"] == "T1B" \
        and st["chain_skip"], st
    assert lim.core.is_wide() and mod._m8200_offen == ""
    assert not printer.sensor.enabled, "Kopfsensor wie PRE_OPT gesperrt"
    assert any("wieder geöffnet" in m and "aufgeweitet" in m
               and "laufen leer" in m for m in g.infos), g.infos
    # 5) Rest des Blocks aus der Datei: kein Box-Frame mehr, kein Schnitt
    del seen[:]
    nb = len(bus.calls)
    t = _cp7_run("\n".join(zeilen[i_r + 1:]), bus, printer, mod)
    assert not [c for c in bus.calls[nb:] if c[1] in (0x10, 0x11)], \
        bus.calls[nb:]
    i_tc = [k for k, e in enumerate(t) if e[0] == "makro"
            and e[1] == "_KTWO_TOOL_CHANGE T=1"]
    assert i_tc and "schon eingezogen" in t[i_tc[0]][2][0], t
    assert not any(e[0] == "cmd" and e[1].startswith("KTWO_BOX_PRE_OPT")
                   for e in t), "kein zweiter Wechsel"
    schacht = [(l, w) for l, w in seen if "Y374" in l or "Y378" in l]
    assert schacht and all(w for _l, w in schacht), schacht
    st = mod.get_status()
    assert not st["chain_active"] and not lim.core.is_wide() \
        and st["loaded_tnn"] == "T1B" and not printer.paused, st
    assert printer.sensor.enabled, "M8200 O gibt den Kopfsensor frei"
    # 6) Druckabbruch statt RESUME löscht den offenen Block
    mod._m8200_offen = "T1B"
    printer.set_printing(False)          # nach CANCEL_PRINT_BASE
    mod.cmd_cancel_reset(FakeGcmd({}))
    assert mod._m8200_offen == ""


def _m8200_weitere_faelle():
    zeilen = _CP7_WECHSEL.strip().splitlines()
    # A) Abbruch beim Schnitt (nach dem Hebelstoß): Rückfahrziel = Position
    #    vor M8200 C (X10 Y200), nicht die Druckposition X0 Y245; T1 in der
    #    Pause; danach läuft der Rest des Blocks ohne zweiten Wechsel
    bus, printer, mod, lim, seen = _m8200_hull()
    i_c = zeilen.index("M8200 C S0")
    _cp7_run("\n".join(zeilen[:i_c]), bus, printer, mod)
    printer.fail_on = "G4 P"
    sc = printer.gcode.scripts
    n = len(sc)
    _cp7_run("M8200 C S0", bus, printer, mod)
    pause = [l for l in sc[n:] if l.startswith("PAUSE")]
    assert len(pause) == 1 and "CFS_RETURN_X=10 CFS_RETURN_Y=200" in pause[0], \
        sc[n:]
    assert mod._m8200_offen == "T1B" and mod.head_cut
    printer.fail_on = None
    mod.cmd_error_clear(FakeGcmd({}))
    _check_raises(mod.cmd_resume_check, {"MOVE": "1"}, ["abgeschnittene"])
    t = _cp7_run("T1", bus, printer, mod)
    assert mod.get_status()["loaded_tnn"] == "T1B" and not mod.head_cut
    assert any("FLUSH T1B ausgelassen" in m for e in t if e[0] == "cmd"
               for m in e[2]), "Spülen übernimmt die Datei"
    printer.paused, printer.print_state = False, "printing"
    printer._refresh()
    mod.cmd_resume_fix(FakeGcmd({}))
    del seen[:]
    nb, n = len(bus.calls), len(sc)
    _cp7_run("\n".join(zeilen[i_c + 1:]), bus, printer, mod)
    assert not [c for c in bus.calls[nb:] if c[1] in (0x10, 0x11)], \
        "kein zweiter Rückzug/Einzug"
    assert "G0 X-7.8 Y200 F30000" not in sc[n:], "kein zweiter Schnitt"
    schacht = [w for l, w in seen if "Y378" in l or "Y374" in l]
    assert schacht and all(schacht), seen
    assert not mod.get_status()["chain_active"] and not lim.core.is_wide()
    # A2) Anderes Ziel in der Pause (T2 statt T1): eigenes Spülen bleibt
    bus, printer, mod, lim, seen = _m8200_hull()
    _cp7_run("\n".join(zeilen[:i_c]), bus, printer, mod)
    printer.fail_on = "G4 P"
    _cp7_run("M8200 C S0", bus, printer, mod)
    printer.fail_on = None
    mod.cmd_error_clear(FakeGcmd({}))
    t = _cp7_run("T2", bus, printer, mod)
    assert not any("ausgelassen" in m for e in t if e[0] == "cmd"
                   for m in e[2]), "T2 != Ziel des Blocks -> selbst spülen"
    assert any(e[0] == "cmd" and e[1].startswith("KTWO_BOX_FLUSH")
               and any("FLUSH-Plan T1C" in m for m in e[2]) for e in t), t
    # B) Hand-PAUSE im Schacht-Teil des Blocks (kein Fehler): Reinigung/
    #    Rückfahrt setzen die Grenzen zurück -> RESUME weitet sie wieder,
    #    der Wechsel läuft normal weiter (A -> B)
    bus, printer, mod, lim, seen = _m8200_hull()
    i_r = zeilen.index("M8200 R")
    _cp7_run("\n".join(zeilen[:i_r]), bus, printer, mod)
    printer.paused, printer.print_state = True, "paused"
    printer._refresh()
    lim.core.restore_all("KTWO_LIMITS_RESTORE")
    printer.paused, printer.print_state = False, "printing"
    printer._refresh()
    g = FakeGcmd({})
    mod.cmd_resume_fix(g)
    assert lim.core.is_wide() and any("noch offen" in m for m in g.infos), \
        g.infos
    assert not mod.get_status()["chain_skip"]
    del seen[:]
    _cp7_run("\n".join(zeilen[i_r:]), bus, printer, mod)
    st = mod.get_status()
    assert st["loaded_tnn"] == "T1B" and not st["chain_active"] \
        and not lim.core.is_wide(), st
    assert all(w for l, w in seen if "Y374" in l or "Y378" in l), seen
    # C) END_OPT in der Pause (Kette von Hand geschlossen) behält den
    #    Merker; ein END_OPT-Fehler setzt ihn nicht neu
    bus, printer, mod, lim, seen = _m8200_hull()
    mod._m8200_offen = "T1B"
    printer.paused, printer.print_state = True, "paused"
    printer._refresh()
    mod.cmd_end_opt(FakeGcmd({"RETURN": "0"}))
    assert mod._m8200_offen == "T1B"
    mod._m8200_offen = ""
    mod.chain = mod._new_chain()
    mod.chain.update({"active": True, "tnn": "T1B", "source": K.M8200_CHAIN})
    printer.paused, printer.print_state = False, "printing"
    printer._refresh()
    mod._fail(FakeGcmd({}), "END_OPT", "Testfehler")
    assert mod._m8200_offen == "", "END_OPT-Fehler öffnet keinen Block"


def test_cp7_wechselblock():
    """Creality-Print-7-Wechselblock (M8200 R E-30 VOR dem Schnitt) und
    Startblock über macros.cfg + k2_box."""
    try:
        import jinja2  # noqa: F401
    except Exception:
        return "SKIP (jinja2 fehlt)"

    def first(t, pred, start=0):
        for i in range(start, len(t)):
            if pred(t[i]):
                return i
        raise AssertionError("fehlt ab %d" % start)

    def is_cmd(name):
        return lambda e: e[0] == "cmd" and e[1].split()[0] == name

    def is_g(line):
        return lambda e: e[0] == "gcode" and e[1] == line

    def is_b(fun, data):
        return lambda e: e[0] == "bus" and e[1] == fun and tuple(e[2]) == data

    # 1) Wechsel A -> B im Druck
    bus, printer, mod = _cp7_hull(0x01, True)
    t = _cp7_run(_CP7_WECHSEL, bus, printer, mod)
    i_pre = first(t, is_cmd("KTWO_BOX_PRE_OPT"))
    i_brk = first(t, is_cmd("KTWO_BOX_BREAK_RETRUDE"), i_pre)
    assert t[i_brk][1] == "KTWO_BOX_BREAK_RETRUDE LENGTH=-30.0", t[i_brk]
    i_cut = first(t, is_cmd("KTWO_BOX_CUT"), i_brk)
    brk = t[i_brk:i_cut]
    # Stock break_box_extruder_retrude: 04 IDLE, 11 [m 00], 04 IDLE, E-30 F300
    b1 = first(brk, is_b(0x04, (0x00, 0x01)))
    b2 = first(brk, is_b(0x11, (0x01, 0x00)), b1)
    b3 = first(brk, is_b(0x04, (0x00, 0x01)), b2)
    first(brk, is_g("G1 E-30 F300"), b3)
    assert not any(e[0] == "bus" and e[1] == 0x11 and e[2][1] == 0x01
                   for e in brk), "kein pull_back_hub vor dem Schnitt"
    assert not any(e[0] == "gcode" and e[1].startswith(("G0", "M109"))
                   for e in brk), "keine Fahrt/kein Heizen beim Rückzug"
    # Schnitt, dann (nach der Fahrt der Datei zum Schacht) voller Rückzug
    first(t, is_g("G0 X-7.8 Y200 F30000"), i_cut)
    i_r = first(t, is_cmd("KTWO_BOX_RETRUDE"), i_cut)
    first(t, lambda e: e[0] == "datei" and e[1] == "G0 X133", i_cut)
    i_p1 = first(t, is_b(0x11, (0x01, 0x01)), i_r)
    assert len([e for e in t[i_r:i_p1] if is_b(0x11, (0x01, 0x00))(e)]) == 4
    i_ext = first(t, is_cmd("KTWO_BOX_EXTRUDE"), i_p1)
    assert t[i_ext][1] == "KTWO_BOX_EXTRUDE TNN=T1B", t[i_ext]
    # T1 in der offenen M8200-Kette: kein zweiter Wechsel, Kette bleibt offen
    i_t1 = first(t, lambda e: e[0] == "makro" and e[1] == "T1", i_ext)
    i_tc = first(t, lambda e: e[0] == "makro"
                 and e[1] == "_KTWO_TOOL_CHANGE T=1", i_t1)
    assert "schon eingezogen" in t[i_tc][2][0], t[i_tc]
    i_o = first(t, is_cmd("KTWO_BOX_END_OPT"), i_tc)
    assert t[i_o][1] == "KTWO_BOX_END_OPT RETURN=0"
    assert [e[1] for e in t[i_ext + 1:i_o] if e[0] == "cmd"] == \
        ["KTWO_BOX_WASTE"], "zwischen Einzug und M8200 O kein weiterer " \
        "Box-Schritt (WASTE nur Hinweis, die Datei spült selbst)"
    k2_moves = [e for e in t[i_o:] if e[0] == "gcode"
                and e[1].startswith(("G0", "G1", "M104", "M109"))]
    assert not k2_moves, k2_moves     # keine Rückfahrt, Temperatur bleibt
    assert any("Wechsel T1B abgeschlossen" in m for m in t[i_o][2]), t[i_o]
    assert printer.target == 220.0, printer.target
    st = mod.get_status()
    assert st["loaded_tnn"] == "T1B" and not st["chain_active"] \
        and st["chain_error"] == "" and not printer.paused, st
    assert not [m for m in printer.gcode.raw if m.startswith("!!")], \
        printer.gcode.raw
    assert len([e for e in t if is_cmd("KTWO_BOX_PRE_OPT")(e)]) == 1
    # 2) Startblock (Kopf + Hub leer): Schnitt wie Stock, kein Rückzug,
    #    Einzug A, T0 übersprungen, END_OPT verlässt den Schacht, 220 °C bleibt
    bus, printer, mod = _cp7_hull(0x00, False)
    t = _cp7_run(_CP7_START, bus, printer, mod)
    assert not any(is_cmd("KTWO_BOX_BREAK_RETRUDE")(e) for e in t)
    i_r = first(t, is_cmd("KTWO_BOX_RETRUDE"))
    assert any("Hub leer" in m for m in t[i_r][2]), t[i_r]
    assert not any(e[0] == "bus" and e[1] == 0x11 for e in t)
    i_ext = first(t, is_cmd("KTWO_BOX_EXTRUDE"), i_r)
    i_t0 = first(t, lambda e: e[0] == "makro"
                 and e[1] == "_KTWO_TOOL_CHANGE T=0", i_ext)
    assert "schon eingezogen" in t[i_t0][2][0], t[i_t0]
    i_o = first(t, is_cmd("KTWO_BOX_END_OPT"), i_t0)
    first(t, is_g("G0 Y345 F12000"), i_o)            # Schacht verlassen
    assert not any(e[0] == "gcode" and e[1].startswith("M104")
                   for e in t[i_o:]), "Ziel 220 aus der Datei bleibt"
    assert any("aus der Datei bleibt" in m for m in t[i_o][2]), t[i_o]
    assert printer.target == 220.0, printer.target
    st = mod.get_status()
    assert st["loaded_tnn"] == "T1A" and not st["chain_active"] \
        and not printer.paused, st
    # 3) KTWO_BOX_BREAK_RETRUDE einzeln: Hub leer -> nur Meldung; LENGTH 0
    #    -> Ablehnung; kalt -> Ablehnung ohne Frame; Plan ohne allow_write
    bus, printer, mod = _cp7_hull(0x00, False)
    printer.set_printing(False)
    g = FakeGcmd({"LENGTH": "-30"})
    mod.cmd_break_retrude(g)
    assert any("kein Slot geladen" in m for m in g.infos), g.infos
    assert not [c for c in bus.calls if c[1] in (0x04, 0x11)]
    assert "G1 E-30 F300" not in printer.gcode.scripts
    for prm in ({}, {"LENGTH": "0"}):
        try:
            mod.cmd_break_retrude(FakeGcmd(prm))
        except ValueError as exc:
            assert "LENGTH" in str(exc), exc
        else:
            raise AssertionError("LENGTH fehlt/0 hätte abgelehnt werden müssen")
    bus, printer, mod = _cp7_hull(0x02, True)
    printer.set_printing(False)
    orig = printer._refresh

    def _cold():
        orig()
        printer.objects["extruder"].status = {"target": 0.0,
                                              "temperature": 25.0,
                                              "can_extrude": False}
    printer._refresh = _cold
    printer._refresh()
    try:
        mod.cmd_break_retrude(FakeGcmd({"LENGTH": "-30"}))
    except ValueError as exc:
        assert "kalt" in str(exc), exc
    else:
        raise AssertionError("kalte Düse hätte abgelehnt werden müssen")
    assert not [c for c in bus.calls if c[1] in (0x04, 0x11)]
    bus, printer, mod = _cp7_hull(0x02, True)
    printer.set_printing(False)
    g = FakeGcmd({"LENGTH": "-30"})
    mod.cmd_break_retrude(g)              # einzeln: Slot B, Sensor aus/an
    assert [c[1:3] for c in bus.calls if c[1] in (0x04, 0x11)] == [
        (0x04, (0x00, 0x01)), (0x11, (0x02, 0x00)), (0x04, (0x00, 0x01))]
    sc = printer.gcode.scripts
    assert sc.index("SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=0") \
        < sc.index("G1 E-30 F300") \
        < sc.index("SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=1")
    assert bus.hub_mask == 0x02 and mod.get_status()["cut_needed"] == ""
    bus, printer, mod = _cp7_hull(0x02, True)
    mod.cfg["allow_write"] = False
    printer.set_printing(False)
    g = FakeGcmd({"LENGTH": "-30"})
    mod.cmd_break_retrude(g)
    assert any("PLAN Box BREAK_RETRUDE" in m for m in g.infos), g.infos
    assert not [c for c in bus.calls if c[1] in (0x04, 0x11)]
    return ("CP7: M8200 R E-30 = 04/11 p0/04 + E-30 F300 (kein Box-Rückzug vor "
            "dem Schnitt), Schnitt, voller Rückzug, Einzug, T<n> in offener "
            "Kette übersprungen, Datei spült, M8200 O ohne Rückfahrt, Ziel "
            "aus der Datei bleibt; Startblock; Einzelbefehl-Grenzfälle")


def test_konsole_poll():
    """Konsolen-Audit 23.10.2026: Poll-Timer meldet Box-Ausfall (erst nach
    POLL_WARN_FAILS in Folge, entprellt, Entwarnung) und Box-ERROR-Wechsel
    rot (einmal je Code); wirft nie."""
    bus, printer, mod = build_hull(allow_write="True", auto_init="True")
    mod.handle_motor_ready()
    poll = printer.reactor.timers[-1]
    ctl = mod.controller
    raw = printer.gcode.raw
    n0 = len(raw)

    def tot():
        raise TimeoutError("keine Antwort")
    ctl.poll_status = tot
    for i in range(K.POLL_WARN_FAILS - 1):
        poll(100.0 + i)
    assert len(raw) == n0, raw[n0:]
    poll(110.0)
    poll(111.0)
    warn = [z for z in raw[n0:] if z.startswith("// [k2_box] CFS-Box antwortet nicht")]
    assert len(warn) == 1 and "%d Abfragen" % K.POLL_WARN_FAILS in warn[0], raw[n0:]
    idle = {"kind": "state", "state": K.BOX_STATE_IDLE}
    ctl.poll_status = lambda: idle
    poll(112.0)
    assert raw[-1].startswith("// [k2_box] CFS-Box antwortet wieder"), raw[-1]
    n1 = len(raw)
    err = {"kind": "state", "state": K._BOX.BOX_STATE_ERROR, "error_code": 0x19}
    ctl.poll_status = lambda: err
    poll(113.0)
    poll(114.0)
    rot = [z for z in raw[n1:] if z.startswith("!! [k2_box] CFS meldet Fehler 0x19")]
    assert len(rot) == 1 and "KTWO_BOX_ERROR_CLEAR" in rot[0], raw[n1:]
    ctl.poll_status = lambda: idle
    poll(115.0)
    ctl.poll_status = lambda: err
    poll(116.0)
    rot = [z for z in raw[n1:] if z.startswith("!! [k2_box] CFS meldet Fehler")]
    assert len(rot) == 2, raw[n1:]
    # Wechsel-Fehler schon gemeldet -> kein zweites Rot
    n2 = len(raw)
    ctl.poll_status = lambda: idle
    poll(117.0)
    mod.chain_error = "Testfehler"
    ctl.poll_status = lambda: err
    poll(118.0)
    assert len(raw) == n2, raw[n2:]
    mod.chain_error = None
    # Poll-Auswertung mit Unsinn wirft nicht
    ctl.poll_status = lambda: {"kind": "state", "state": K._BOX.BOX_STATE_ERROR,
                               "error_code": "kaputt"}
    assert poll(119.0) == 119.0 + float(mod.cfg.get("poll_interval",
                                                    K.POLL_INTERVAL_DEFAULT))
    return ("Ausfall nach %d Polls einmal //, Entwarnung, ERROR 0x19 einmal !!, "
            "neuer Wechsel erneut, Kettenfehler still, Unsinn ohne Wurf"
            % K.POLL_WARN_FAILS)


def test_box_wiederanmeldung():
    """Live 26.10.2026: Box im Wechsel abgezogen + wieder eingesteckt -> sie
    hat ihre Adresse verloren. Nach POLL_WARN_FAILS stummen Polls läuft ein
    Auto-Addr-Durchlauf wie Stock (0xA1 -> „slave restarted“ -> 0xA0), die
    Box antwortet wieder; im Druck höchstens alle 10 s, vorher nie; ohne
    allow_write keiner."""
    bus, printer, mod = build_hull(allow_write="True", auto_init="True")
    mod.handle_motor_ready()
    poll = printer.reactor.timers[-1]
    raw = printer.gcode.raw
    orig = bus._handle

    def handle(addr, fun, payload):
        if bus.box_addr == 0 and fun in (0x0A, 0xA2, 0xA3):
            raise TimeoutError("Box ohne Adresse (neu gestartet)")
        if getattr(bus, "tot", False):
            raise TimeoutError("Box stromlos")
        return orig(addr, fun, payload)
    bus._handle = handle
    poll(100.0)
    assert mod._poll_fails == 0
    # Neustart der Box: Adresse weg
    bus.box_addr = 0
    n0, c0 = len(raw), len(bus.calls)
    for i in range(K.POLL_WARN_FAILS - 1):
        poll(101.0 + i)
    assert not [c for c in bus.calls[c0:] if c[1] in (0xA0, 0xA1)], \
        "Wiederanmeldung vor POLL_WARN_FAILS"
    poll(110.0)
    funs = [c[1] for c in bus.calls[c0:]]
    assert 0xA1 in funs and 0xA0 in funs and bus.box_addr == 0x01, funs
    assert any("Wiederanmeldung läuft" in z and "alle 4 s" in z
               for z in raw[n0:]), raw[n0:]
    poll(111.0)
    assert raw[-1].startswith("// [k2_box] CFS-Box antwortet wieder") \
        and "neu angemeldet" in raw[-1], raw[-1]
    assert mod._poll_fails == 0 and mod._readdr_laeufe == 0
    # Box bleibt stromlos, Druck läuft: Durchlauf höchstens alle 10 s
    printer.set_printing(True)
    bus.tot = True
    c1, n1 = len(bus.calls), len(raw)
    for t in range(200, 240, 4):
        poll(float(t))
    a1 = [c for c in bus.calls[c1:] if c[1] == 0xA1]
    assert 3 <= len(a1) <= 4, len(a1)
    assert sum("Wiederanmeldung läuft" in z and "alle 10 s" in z
               for z in raw[n1:]) == 1, raw[n1:]
    bus.tot = False
    bus.box_addr = 0
    for t in range(240, 260, 4):
        poll(float(t))
    assert bus.box_addr == 0x01 and mod._poll_fails == 0, (bus.box_addr,
                                                           mod._poll_fails)
    # ohne allow_write: keine Adressvergabe, nie 0xA0
    bus, printer, mod = build_hull(allow_write="False", auto_init="True")
    mod.handle_motor_ready()
    poll = printer.reactor.timers[-1]
    bus.box_addr = 0
    c2 = len(bus.calls)
    for t in range(300, 330, 4):
        poll(float(t))
    assert not [c for c in bus.calls[c2:] if c[1] in (0xA0, 0xA1)]
    return ("Box neu gestartet: nach %d stummen Polls 0xA1/0xA0, antwortet "
            "wieder (neu angemeldet); im Druck stromlos höchstens alle 10 s; "
            "allow_write=False sendet nichts" % K.POLL_WARN_FAILS)


TESTS = [
    ("kompletter Load-Ablauf (B)", test_load_ok),
    ("Fehlerabbruch 0x0c mit Meldung (A ~245 mm)",
     _test_load_fehler_0c_impl),
    ("Unload (B)", test_unload_ok),
    ("allow_write=False schreibt nichts", test_allow_write_false),
    ("Frame-Folge == Live-Sequenzen", test_frame_folge_live),
    ("gemeinsamer Lock (keine Überlappung)", test_shared_lock),
    ("Lesen + Poll retries=0 + Moonraker-Status", test_lesen_poll_status),
    ("Preload/Addr/Guards (nie 0x50/0x06, keine Bursts)",
     test_preload_addr_guards),
    ("G-Codes via FakePrinter", test_gcodes),
    ("Wechsel-Bausteine == box.cfg/Live", test_wechsel_bausteine),
    ("Einzug mit Kopfsensor + Stage 7", test_extrude_sensor_stage7),
    ("Rückzug mit Extruder-Haken je p0", test_retrude_after_p0),
    ("Box-Zustand abwarten (BOX_MODE_WAIT)", test_wait_box_ready),
    ("Wechsel-Kette T1B komplett (Freigaben an)", test_wechsel_kette_voll),
    ("Wechsel-Sperren (allow_write/allow_move/Druck)", test_wechsel_sperren),
    ("Wechsel-Fehler im Druck -> Pause", test_wechsel_fehler_im_druck),
    ("Wechsel-Fehler ohne Druck -> G-Code-Fehler", test_wechsel_fehler_ohne_druck),
    ("RESUME-Ziel nach Abbruch am Schneidhebel (PAUSE CFS_RETURN)",
     test_resume_ziel_nach_abbruch),
    ("Schacht vom Hebel nur über X + RESUME_FIX CLEAR",
     test_schacht_aus_schneidbereich_und_resume_fix),
    ("Makros PAUSE/RESUME nach CFS-Abbruch (Schneidbereich/Schacht)",
     test_makros_resume_schneidbereich),
    ("RESUME nach Abbruch: Kopf leer gesperrt / voll frei",
     test_resume_kopf_sperre),
    ("RESUME nach Abbruch: Kopfsensor fehlt -> gesperrt, HEAD_LOADED",
     test_resume_kopf_sensor_fehlt),
    ("RESUME nach Abbruch: Merker (Ziel-TNN) Lebensdauer",
     test_resume_abbruch_merker),
    ("RESUME nach Abbruch: Wechsel nachholen (resume_redo_change)",
     test_resume_nachholen),
    ("RESUME nach Abbruch: Nachholen nur mit allen Freigaben",
     test_resume_nachholen_sperren),
    ("Makros RESUME: Kopfprüfung vor PREPARE", test_makros_resume_kopfpruefung),
    ("PAUSE-Temperatur nach Abbruch (CFS_TEMP = Druckziel)",
     test_pause_temp_nach_abbruch),
    ("Makros PAUSE CFS_TEMP / RESUME-Heizen", test_makros_pause_temp),
    ("ReactorBusProxy (kein Reactor-Blockieren)", test_reactor_bus_proxy),
    ("Konsole: Poll-Ausfall/Box-ERROR (Audit 23.10.)", test_konsole_poll),
    ("Box-Wiederanmeldung nach Stromverlust (Live 26.10.)",
     test_box_wiederanmeldung),
    ("Makros T0-T15/M8200/RESUME (macros.cfg)", test_makros_cfg),
    ("Runout: Slot-Wahl-Regel + Optionen", test_runout_slotwahl),
    ("Runout: Nachladen T1A -> T1C + RESUME", test_runout_ablauf),
    ("Runout: Pausenpfade + Sperren", test_runout_pausenpfade),
    ("Runout: AUTO_REFILL/SAME_MATERIAL", test_runout_befehle),
    ("Runout: printer.cfg/k2-module.cfg", test_runout_config),
    ("Material-DB laden (an/aus/kaputt) + Abfragen", test_material_db_laden),
    ("Material-Temperaturen (DB an/aus, unbekannt, Kappung)",
     test_material_temperaturen),
    ("Spülmenge Farbvolumen == Live (flush.py)", test_flush_farbvolumen),
    ("Spül-Geschwindigkeit + Abschnitte 20/80 ×1,2", test_flush_geschwindigkeit_abschnitte),
    ("Reinigung Stock (Blech / Kreise am Ende / blow)", test_flush_reinigung_stock),
    ("FLUSH-Ablauf wie Stock (Hülle)", test_flush_ablauf_stock),
    ("FLUSH Verstopfungsprüfung nur Warnung", test_flush_verstopfung),
    ("RESUME-Vorextrusion prime_speed (Status + Makro)",
     test_prime_speed_status_und_makro),
    ("Runout/Nachholen BASE_TEMP + DB", test_runout_redo_basistemperatur),
    ("Preload-Sperren (Kopf, Druck/FORCE, Maske 0)", test_preload_sperren),
    ("box_wait_timeout >= 380 s", test_box_wait_timeout),
    ("Düsen-Material-Datei lesen (ok/fehlt/kaputt/Struktur/Farbe)",
     test_remain_laden),
    ("Düsen-Material-Datei schreiben (atomar, nur nach FLUSH/Änderung)",
     test_remain_schreiben),
    ("Slicer-Matrix parsen (Kopf/Ende, 16×16 asymmetrisch)",
     test_matrix_parsen),
    ("FLUSH flush_mode matrix (TOOL, nur printing, Rückfälle)",
     test_matrix_flush),
    ("FLUSH E4/E5: Default matrix, 1. Einzug Farbweg (Live-Werte Druck 3)",
     test_matrix_erster_einzug_live),
    ("Makro M600 -> PAUSE", test_makro_m600),
    ("Restmaterial: Auslöser 0x50 im Druck, ENDING, Runout",
     test_ending_ausloeser),
    ("Restmaterial: Stückfolge, Ende per Kopfsensor, keine Kreise",
     test_ending_stueckfolge),
    ("Restmaterial: Geschwindigkeit mit/ohne DB, Temperatur",
     test_ending_geschwindigkeit),
    ("Restmaterial: Obergrenze, Sensor unbekannt, Zeitlimit, Sperren",
     test_ending_grenzen),
    ("Restmaterial: KTWO_BOX_ENDING PURGE=1 (von Hand)", test_ending_hand_purge),
    ("Druckende: end_print_unload an/aus, Fehler ohne Pause",
     test_end_print_unload),
    ("Restmaterial/Druckende: Optionen, k2-module.cfg, END_PRINT-Makro",
     test_ending_config_makro),
    ("Stock-Retry bei 0x0c (Controller, == Live-Log)", test_extrude_stock_retry),
    ("Stock-Retry bei 0x0c (KTWO_BOX_EXTRUDE-Hülle)", test_extrude_retry_huelle),
    ("Temperatur-Guard KTWO_BOX_LOAD/UNLOAD", test_temperatur_guard),
    ("L3 Fahrgrenzen [k2_limits] im k2_box-Schritt", test_limits_l3),
    ("M8200-Kette hält Fahrgrenzen weit bis END_OPT (CP7)", test_m8200_kette_grenzen),
    ("KTWO_BOX_RFID (= BOX_INFO_REFRESH, nur lesend)", test_rfid_befehl),
    ("KTWO_BOX_SLOT_SET (= BOX_MODIFY_TN_DATA) + Vorrang/Wirkung",
     test_slot_set),
    ("Slot-Handwerte-Datei (slot_material_file)", test_slot_set_datei),
    ("Live-Abgleich 18.10. A1: Erst-Laden ohne Schnitt/Rückzug",
     test_live_a1_erstladen),
    ("Live-Abgleich 18.10. A2: Box-Druckzustand 0x0d/0x0f",
     test_live_a2_druckzustand),
    ("Live-Abgleich 18.10. A3: Rückzug mit Spülgeschwindigkeit",
     test_live_a3_rueckzug_geschwindigkeit),
    ("Live-Abgleich 18.10. A5/A6: END_PRINT-Reihenfolge, START_PRINT",
     test_live_a6_end_print_makro),
    ("Druckabbruch C3: Pause+Wechselfehler -> CANCEL -> neuer Druck",
     test_c3_pause_cancel_neuer_druck),
    ("Druckabbruch C4/C5/C6: Makros _PI_RESET_STATE/CANCEL_PRINT/END_PRINT",
     test_c4_c5_c6_makros),
    ("Druckabbruch C2: Abbruch-Flag in Warteschleifen", test_c2_abbruch_flag),
    ("Einzel-RETRUDE: Kopfsensor gesperrt (keine Runout-PAUSE)",
     test_einzel_rueckzug_sensor_gesperrt),
    ("Schnittschutz: Einzel-RETRUDE/UNLOAD nach Spülen erst nach CUT",
     test_schnittschutz_nach_spuelen),
    ("RETRUDE: Kopf leer + Düse kalt -> nur Box-Rückzug (Live 26.10.)",
     test_retrude_kopf_leer_kalt),
    ("M8200-Block: Abbruch, T1 in der Pause, RESUME (Live 26.10.)",
     test_m8200_abbruch_resume),
    ("Creality-Print-7-Wechselblock (M8200 R E-30 vor dem Schnitt)",
     test_cp7_wechselblock),
    ("Python-3.9-Syntax", test_python39),
]


def main():
    fails, total = 0, 0
    print("test_k2_box – %d Prüfungen (ohne Klipper, ohne Hardware)"
          % len(TESTS))
    for name, fn in TESTS:
        total += 1
        try:
            detail = fn()
        except AssertionError as exc:
            fails += 1
            print("FAIL %s: %s" % (name, exc))
        except Exception as exc:  # pragma: no cover
            fails += 1
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
        else:
            print("ok %s – %s" % (name, detail))
    print("ERGEBNIS: %s (%d/%d ok)"
          % ("ok" if fails == 0 else "%d FEHLER" % fails, total - fails, total))
    return fails


if __name__ == "__main__":
    raise SystemExit(main())
