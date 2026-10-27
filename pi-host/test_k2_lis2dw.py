#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_k2_lis2dw.py – Offline-Tests fuer k2_lis2dw (ohne Klipper-Lauf, ohne Hardware).

Aufruf: ``cd pi-host && python3 test_k2_lis2dw.py``  (Python >= 3.9, numpy optional)

Geladen wird das Modul als ``extras.k2_lis2dw`` zusammen mit den ECHTEN
Upstream-Dateien ``extras/{bulk_sensor,adxl345,lis2dw,resonance_tester,
shaper_calibrate}.py`` (Commit 9cb9975, ``lokal/upstream-klipper``
oder ``$KLIPPER_SRC``); nur ``extras.bus`` (SPI) ist eine Attrappe. Formate und
Kodierung laufen ueber das echte Upstream-``msgproto`` mit dem Creality-
Duesen-Dictionary.

Die Duesen-Firmware wird nach dem Dekompilat emuliert (``FwSim``, Adressen in
README-k2_lis2dw.md §2): Timer alle rest_ticks, je Sample 7-Byte-Read ab 0x28 +
FIFO_SAMPLES-Read, 48-Byte-Puffer -> lis2dw_data, Status mit fifo & 0x1f,
Start schreibt FIFO_CTRL 0xC0, Stopp liest Rest + Status.

Prueft:
 1. Formate: alle Modul-Formate == Dictionary (Main + Duese); Upstream-Formate
    -> "Command format mismatch"; Config-Befehle kodierbar.
 2. Status-Uebersetzung lis2dw_status -> sensor_bulk_status (FIFO-Feld
    nicht mitgezaehlt; count_fifo=True == alter Host), limit_count-Ueberlauf
    0xffff, fifo-Maske.
 3. Dekodierung synthetischer lis2dw_data-Pakete (Wire-Rundreise): int16 LE,
    negative Werte, Teilblock, Sequenz-Ueberlauf.
 4. Achsen-Mapping x,z,y (Stock) und Vorzeichen; 1 g Rohwert -> ~9807 mm/s^2.
 5. Start/Stopp: Registerfolge, query_lis2dw clock/rest_ticks, minclock der
    ersten Statusabfrage, Stopp-Abfrage, falsche Chip-ID, Batch-Modus.
 6. Zeitachse/Clock gegen Firmware-Emulation (Disassembly 22.10.2026: 1
    Sample je Timer, Leer-Schleife bei leerem FIFO): Stock-Lesetakt 2,5 ms
    -> 385 Hz (= live), Default 1,5 ms -> ~625 Hz, Streuung/Drift/Versatz,
    Chip-ODR +-10 %, alter Host (FIFO mitgezaehlt) als Gegenprobe,
    Leer-Schleife bei zu kurzem Lesetakt, Sequenz-Ueberlauf im Langlauf.
 7. Integration: ACCELEROMETER_QUERY, KTWO_ACCEL_STATUS, Stopp nach Ende,
    resonance_tester.connect + MEASURE_AXES_NOISE (echter Upstream-Code,
    braucht numpy).
 8. G-Code-Namen: echte Upstream-Pruefung + Parser-Zerlegung.
 9. Python-3.9-Syntax.
"""

import ast
import importlib.util
import json
import math
import multiprocessing
import os
import random
import re
import struct
import sys
import time
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_HERE)
_ks = os.environ.get("KLIPPER_SRC", "")
if _ks and os.path.isdir(os.path.join(_ks, "klippy")):
    _ks = os.path.join(_ks, "klippy")
UPSTREAM_KLIPPY = os.path.normpath(_ks or os.path.join(
    _REPO, "lokal", "upstream-klipper", "klippy"))
# MCU-Dictionaries der Creality-Firmware (nicht im Repo): K2_MCU_DICT_DIR setzen,
# sonst lokal/mcu-dicts. Fehlen sie, werden die Tests übersprungen (Exit 0).
DICT_DIR = os.environ.get("K2_MCU_DICT_DIR") or os.path.join(
    _REPO, "lokal", "mcu-dicts")
DICT_NOZ = os.path.join(DICT_DIR, "noz-duese-F008-GD32F303CBT6.dict.json")
DICT_MAIN = os.path.join(DICT_DIR, "mcu-main-F008-GD32F303RET6.dict.json")
MODULE_PATH = os.path.join(_HERE, "k2_lis2dw.py")
if not (os.path.isfile(DICT_NOZ) and os.path.isfile(DICT_MAIN)):
    print("SKIP (k2_lis2dw) MCU-Dictionaries fehlen in %s – K2_MCU_DICT_DIR "
          "setzen (mcu-main-F008-GD32F303RET6.dict.json, "
          "noz-duese-F008-GD32F303CBT6.dict.json)" % DICT_DIR)
    sys.exit(0)
if not os.path.isdir(os.path.join(UPSTREAM_KLIPPY, "extras")):
    print("SKIP (k2_lis2dw) Upstream-Klipper nicht gefunden: %s – KLIPPER_SRC "
          "setzen" % UPSTREAM_KLIPPY)
    sys.exit(0)

FREQ = 120000000  # CLOCK_FREQ beider MCUs (Dictionary)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    if not cond:
        print("  FEHLER: %s %s" % (name, detail))


# ---------------------------------------------------------------------------
# Upstream laden: Paket "extras" = Upstream-extras, nur "extras.bus" = Attrappe
# ---------------------------------------------------------------------------
if UPSTREAM_KLIPPY not in sys.path:
    sys.path.append(UPSTREAM_KLIPPY)
import msgproto  # noqa: E402  (Upstream)
import gcode as ugcode  # noqa: E402  (Upstream)

_stub_bus = types.ModuleType("extras.bus")
_ext = types.ModuleType("extras")
_ext.__path__ = [os.path.join(UPSTREAM_KLIPPY, "extras")]
sys.modules["extras"] = _ext
sys.modules["extras.bus"] = _stub_bus
_ext.bus = _stub_bus


class FakeSPI:
    """Attrappe fuer bus.MCU_SPI (nur was lis2dw/k2_lis2dw nutzt)."""

    def __init__(self, config, mode, default_speed):
        self.printer = config.get_printer()
        self.mcu = self.printer.fake_mcu
        self.mode = mode
        self.speed = config.getint('spi_speed', default_speed)
        self.cs_pin = config.get('cs_pin')
        self.sw_pins = tuple(config.get('spi_software_%s_pin' % n, None)
                             for n in ('sclk', 'mosi', 'miso'))
        self.oid = self.mcu.create_oid()
        self.cq = object()
        self.log = []

    def get_mcu(self):
        return self.mcu

    def get_oid(self):
        return self.oid

    def get_command_queue(self):
        return self.cq

    def spi_send(self, data, minclock=0, reqclock=0):
        data = list(data)
        self.log.append(("send", data))
        self.mcu.host_spi_op()
        if not self.mcu.is_fileoutput():
            self.mcu.sim.chip.write(self.mcu.now, data[0], data[1])

    def spi_transfer(self, data, minclock=0, reqclock=0):
        data = list(data)
        self.log.append(("xfer", data))
        self.mcu.host_spi_op()
        reg = data[0] & 0x7f
        return {'response': bytes([0, self.mcu.sim.chip.regs.get(reg, 0)])}


def _MCU_SPI_from_config(config, mode, pin_option="cs_pin",
                         default_speed=100000, share_type=None,
                         cs_active_high=False):
    return FakeSPI(config, mode, default_speed)


_stub_bus.MCU_SPI_from_config = _MCU_SPI_from_config
_stub_bus.MCU_I2C_from_config = None

from extras import bulk_sensor as ubulk  # noqa: E402
from extras import adxl345 as uadxl  # noqa: E402
from extras import lis2dw as ulis  # noqa: E402

_spec = importlib.util.spec_from_file_location("extras.k2_lis2dw", MODULE_PATH)
KL = importlib.util.module_from_spec(_spec)
sys.modules["extras.k2_lis2dw"] = KL
_spec.loader.exec_module(KL)


def load_parser(path):
    mp = msgproto.MessageParser()
    with open(path, "rb") as f:
        mp.process_identify(f.read(), decompress=False)
    return mp


MP_NOZ = load_parser(DICT_NOZ)
MP_MAIN = load_parser(DICT_MAIN)


def roundtrip(mp, name, **params):
    """Antwort wie die Firmware kodieren und wie der Host parsen."""
    fmt = mp.messages_by_name[name]
    enc = fmt.encode_by_name(**params)
    out, pos = fmt.parse(enc, 0)
    assert pos == len(enc)
    out['#name'] = name
    return out


# ---------------------------------------------------------------------------
# Chip- und Firmware-Emulation (nach Dekompilat der Duesen-Firmware)
# ---------------------------------------------------------------------------
def default_value(k, clock):
    # Index im Wert kodiert: rx = k mod 30000 - 15000 (auch negativ),
    # ry = 2048 (= 1 g bei +-16 g, linksbuendig), rz = -(k mod 1000) - 1
    return ((k % 30000) - 15000, 2048, -(k % 1000) - 1)


def index_from_rx(rx, approx):
    base = rx + 15000
    n = round((approx - base) / 30000.)
    return base + n * 30000


class ChipSim:
    """LIS2DW12-Modell: ODR, FIFO 32 Plaetze, Bypass/Continuous, FSS/OVR."""

    def __init__(self, f_chip=1600.0, value_fn=default_value):
        self.regs = {0x0f: 0x44, 0x20: 0x00, 0x21: 0x04, 0x25: 0x00,
                     0x2e: 0x00, 0x2f: 0x00}
        self.f_chip = f_chip
        self.value_fn = value_fn
        self.on_clock = None
        self.k_next = 0
        self.fifo = []
        self.ovr = False
        self.last_out = (-1, (0, 0, 0))
        self.overwritten = 0
        self.dup_reads = 0

    def sample_clock(self, k):
        return self.on_clock + (k + 1) * FREQ / self.f_chip

    def advance(self, clock):
        if self.on_clock is None:
            return
        while self.sample_clock(self.k_next) <= clock:
            k = self.k_next
            self.k_next += 1
            val = self.value_fn(k, self.sample_clock(k))
            if (self.regs[0x2e] >> 5) == 0:  # Bypass: FIFO bleibt leer
                self.last_out = (k, val)
                continue
            if len(self.fifo) >= 32:
                self.fifo.pop(0)
                self.ovr = True
                self.overwritten += 1
            self.fifo.append((k, val))

    def write(self, clock, reg, val):
        self.advance(clock)
        self.regs[reg] = val
        if reg == 0x20:
            if val == 0x94 and self.on_clock is None:
                self.on_clock = clock
                self.k_next = 0
            elif val == 0x00:
                self.on_clock = None
        if reg == 0x2e and (val >> 5) == 0:
            self.fifo = []
            self.ovr = False

    def read_sample(self, clock):
        self.advance(clock)
        if self.fifo:
            self.last_out = self.fifo.pop(0)
            if len(self.fifo) < 32:
                self.ovr = False
        else:
            self.dup_reads += 1
        return self.last_out

    def read_fss(self, clock):
        self.advance(clock)
        return min(len(self.fifo), 32) | (0x40 if self.ovr else 0)


class BusyLoop(Exception):
    """Firmware-Schleife ohne Pause ueber BUSY_LIMIT Lesungen (s. FwSim)."""


class FwSim:
    """Emulation command_query_lis2dw / lis2dw_task / lis2dw_query /
    lis2dw_report / command_query_lis2dw_status der Creality-Firmware.

    lis2dw_query (@0x08004e78, Disassembly 22.10.2026) liest JE AUFRUF EIN
    Sample (7 Byte ab 0x28) + FIFO_SAMPLES und entscheidet dann:
      * FIFO_SAMPLES & 0x3f != 0 -> Timer neu: waketime = jetzt + rest_ticks
        (0x8004f04 cbnz -> 0x8004f0a -> 0x8004f1e), also EIN Sample je
        rest_ticks, obwohl noch Daten im FIFO liegen;
      * == 0 und fifo_disable == 0 -> sched_wake_task (0x8004f44): sofort
        erneut lesen – ohne Pause; leer gelesene Lesungen liefern Doppel-
        Samples, bis zufaellig ein zweites Sample im FIFO steht.
    Lieferrate daher 1/(rest_ticks + Laufzeit), unabhaengig vom ODR; der
    FIFO laeuft voll und ueberschreibt (Continuous). Laufzeit an live
    22.10.2026 angepasst (rest 2,5 ms -> 385-388 Hz): Ereignis->Task
    T_EVENT + 2 SPI-Transfers T_SPI = 12000 Ticks (100 us)."""
    HAVE_START, RUNNING, PENDING = 1, 2, 4
    T_SPI = 4800      # Ticks je Soft-SPI-Transfer (40 us, an live angepasst)
    T_EVENT = 2400    # Timer-Ereignis -> Task (20 us)
    T_WAKE = 240      # sched_wake_task -> erneuter Task-Lauf
    BUSY_LIMIT = 200  # Leer-Lesungen am Stueck -> BusyLoop

    def __init__(self, mcu, chip):
        self.mcu = mcu
        self.chip = chip
        self.oid = None
        self.seq = self.limit = 0
        self.flags = 0
        self.fifo_disable = 0
        self.data = bytearray()
        self.rest_ticks = 0
        self.waketime = None
        self.reports = 0
        self.busy_reads = 0

    def cmd_query(self, clock, rest_ticks):
        self.waketime = None
        if not rest_ticks:
            raise AssertionError("Stopp muss als Query-Befehl kommen")
        self.waketime = clock
        self.rest_ticks = rest_ticks
        self.flags = self.HAVE_START
        self.seq = self.limit = 0
        self.data = bytearray()
        self.fifo_disable = 0

    def run_until(self, clock):
        while self.waketime is not None and self.waketime <= clock:
            t = self.waketime
            self.waketime = None
            if self.flags & self.HAVE_START:
                self.flags = self.RUNNING
                self.fifo_disable = 0
                self.chip.write(t, 0x2e, 0xc0)       # FIFO_CTRL Continuous
                self.waketime = t + self.T_SPI + self.rest_ticks
            else:
                self._query_loop(t + self.T_EVENT)

    def _query_once(self, t):
        k, val = self.chip.read_sample(t)
        t += self.T_SPI
        fss = self.chip.read_fss(t)
        t += self.T_SPI
        self.data += struct.pack("<hhh", *val)
        if len(self.data) > 0x2a:
            self._report()
        if fss & 0x40:
            self.limit = (self.limit + 1) & 0xffff
        return t, fss & 0x3f

    def _query_loop(self, t):
        busy = 0
        while True:
            t, remaining = self._query_once(t)
            if not remaining and not self.fifo_disable:
                # FW: FIFO leer -> sofort erneut (kein Timer)
                busy += 1
                self.busy_reads += 1
                if busy > self.BUSY_LIMIT:
                    raise BusyLoop("lis2dw_query ohne Pause (%d Leer-Lesungen)"
                                   % busy)
                t += self.T_WAKE
                continue
            if self.flags & self.RUNNING:
                self.waketime = t + self.rest_ticks
            return t

    def _report(self):
        params = roundtrip(MP_NOZ, "lis2dw_data", oid=self.oid,
                           sequence=self.seq & 0xffff, data=bytes(self.data))
        self.data = bytearray()
        self.seq += 1
        self.reports += 1
        self.mcu.deliver("lis2dw_data", self.oid, params)

    def _status(self, time1, time2, fifo):
        return roundtrip(MP_NOZ, "lis2dw_status", oid=self.oid,
                         clock=time1 & 0xffffffff, query_ticks=time2 - time1,
                         next_sequence=self.seq & 0xffff,
                         buffered=len(self.data), fifo=fifo,
                         limit_count=self.limit)

    def query_status(self, t):
        self.run_until(t)
        fss = self.chip.read_fss(t)
        return self._status(t, t + self.T_SPI, fss & 0x1f)

    def stop(self, t):
        self.run_until(t)
        self.waketime = None
        self.flags = 0
        self.fifo_disable = 1
        t = self._query_loop(t)
        time1 = t
        self.chip.write(t, 0x2e, 0x00)
        t += self.T_SPI
        fss = self.chip.read_fss(t)
        if self.data:
            self._report()
        return self._status(time1, time1 + self.T_SPI, fss & 0x1f)


class Sim:
    def __init__(self, mcu, f_chip=1600.0, value_fn=default_value):
        self.mcu = mcu
        self.chip = ChipSim(f_chip, value_fn)
        self.fw = FwSim(mcu, self.chip)

    def advance_to(self, clock):
        self.fw.run_until(clock)
        self.chip.advance(clock)
        self.mcu.now = max(self.mcu.now, clock)


# ---------------------------------------------------------------------------
# Klipper-Attrappen (MCU, Reactor, Toolhead, G-Code, Config)
# ---------------------------------------------------------------------------
class CommandError(Exception):
    pass


class ConfigError(Exception):
    pass


class FakeCmd:
    def __init__(self, mcu, fmt):
        self.mcu, self.fmt = mcu, fmt
        self.mp = mcu.mp.lookup_command(fmt)

    def send(self, data=(), minclock=0, reqclock=0):
        self.mp.encode(list(data))  # Typpruefung wie auf dem Draht
        self.mcu.sent.append((self.fmt.split()[0], list(data), minclock,
                              reqclock))
        if self.mcu.is_fileoutput():
            return
        name = self.fmt.split()[0]
        if name == "query_lis2dw":
            self.mcu.sim.fw.cmd_query(data[1], data[2])

    def send_wait_ack(self, data=(), minclock=0, reqclock=0):
        self.send(data, minclock, reqclock)


class FakeQuery:
    def __init__(self, mcu, fmt, resp, oid):
        self.mcu, self.fmt, self.resp, self.oid = mcu, fmt, resp, oid
        self.mp = mcu.mp.lookup_command(fmt)
        mcu.mp.lookup_command(resp)

    def send(self, data=(), minclock=0, reqclock=0, retry=True):
        self.mp.encode(list(data))
        name = self.fmt.split()[0]
        self.mcu.queries.append((name, list(data), minclock, reqclock))
        if self.mcu.is_fileoutput():
            # wie Upstream-DummyResponse (mcu.py): alle Felder 0
            return self.mcu.mp.create_dummy_response(self.resp.split()[0],
                                                     {'oid': self.oid})
        t = max(self.mcu.now, minclock, reqclock) + 2400   # Senden 20 us
        sim = self.mcu.sim
        sim.advance_to(t)
        if name == "query_lis2dw_status":
            params = sim.fw.query_status(t)
        elif name == "query_lis2dw":
            params = sim.fw.stop(t)
        else:
            raise AssertionError(name)
        self.mcu.now = t + 2400
        return params


class FakeMCU:
    def __init__(self, mp=MP_NOZ, fileoutput=False):
        self.mp = mp
        self.fileoutput = fileoutput
        self.oids = 0
        self.config_cmds, self.restart_cmds = [], []
        self.config_cbs = []
        self.responses = {}
        self.sent, self.queries = [], []
        self.now = 1000 * FREQ  # beliebiger Startpunkt (64 bit)
        self.sim = None
        self.host_op_ticks = FREQ // 1000  # 1 ms je synchroner Host-SPI-Op

    def get_name(self):
        return "nozzle_mcu"

    def create_oid(self):
        self.oids += 1
        return self.oids - 1

    def add_config_cmd(self, cmd, is_init=False, on_restart=False):
        self.mp.create_command(cmd)  # muss mit dem Dictionary kodierbar sein
        (self.restart_cmds if on_restart else self.config_cmds).append(cmd)

    def register_config_callback(self, cb):
        self.config_cbs.append(cb)

    def build(self):
        for cb in self.config_cbs:
            cb()

    def lookup_command(self, fmt, cq=None):
        return FakeCmd(self, fmt)

    def lookup_query_command(self, fmt, resp, oid=None, cq=None,
                             is_async=False):
        return FakeQuery(self, fmt, resp, oid)

    def register_serial_response(self, cb, msg, oid=None):
        self.mp.lookup_command(msg)
        self.responses[(msg.split()[0], oid)] = cb

    def deliver(self, name, oid, params):
        self.responses[(name, oid)](params)

    def is_fileoutput(self):
        return self.fileoutput

    def host_spi_op(self):
        if self.sim is not None:
            self.sim.advance_to(self.now + self.host_op_ticks)

    def estimated_print_time(self, eventtime):
        return self.now / float(FREQ)

    def print_time_to_clock(self, print_time):
        return int(print_time * FREQ)

    def clock_to_print_time(self, clock):
        return clock / float(FREQ)

    def seconds_to_clock(self, t):
        return int(t * FREQ)

    def clock32_to_clock64(self, clock32):
        last = self.now
        diff = (clock32 - last) & 0xffffffff
        diff -= (diff & 0x80000000) << 1
        return last + diff


class FakeReactor:
    NEVER = 9999999999999999.
    NOW = 0.

    def __init__(self, mcu):
        self.mcu = mcu
        self.timers = []

    def monotonic(self):
        return self.mcu.now / float(FREQ)

    def register_timer(self, cb, waketime=NEVER):
        t = [cb, waketime]
        self.timers.append(t)
        return t

    def unregister_timer(self, t):
        if t in self.timers:
            self.timers.remove(t)

    def pause(self, waketime):
        time.sleep(0.01)
        return waketime


class FakeToolhead:
    def __init__(self, printer):
        self.printer = printer

    def get_last_move_time(self):
        return self.printer.fake_mcu.now / float(FREQ)

    def wait_moves(self):
        pass

    def dwell(self, delay):
        self.printer.run_host(delay)


class FakeGcode:
    def __init__(self, printer):
        real = ugcode.GCodeDispatch.__new__(ugcode.GCodeDispatch)
        real.printer = printer
        real.ready_gcode_handlers = {}
        real.gcode_handlers = real.ready_gcode_handlers
        real.base_gcode_handlers, real.gcode_help = {}, {}
        real.mux_commands = {}
        real.status_commands = {}
        self.real = real
        self.out = []

    def register_command(self, cmd, func, when_not_ready=False, desc=None):
        self._parser_check(cmd)
        self.real.register_command(cmd, func, when_not_ready, desc)

    def register_mux_command(self, cmd, key, value, func, desc=None):
        self._parser_check(cmd)
        self.real.register_mux_command(cmd, key, value, func, desc)

    def _parser_check(self, cmd):
        parts = ugcode.GCodeDispatch.args_r.split(cmd.upper())
        if ''.join(parts[:3]).strip() != cmd:
            raise ConfigError("Parser zerlegt %r" % (cmd,))

    def respond_info(self, msg, log=True):
        self.out.append(msg)

    def run_mux(self, cmd, params=None):
        gcmd = FakeGcmd(self, params or {})
        self.real._cmd_mux(cmd, gcmd)
        return gcmd


class FakeGcmd:
    error = CommandError

    def __init__(self, gcode, params):
        self.gcode = gcode
        self.params = dict(params)
        self.out = []

    def get(self, name, default=None, parser=str, **kw):
        v = self.params.get(name, default)
        return parser(v) if (v is not None and v is not default) else v

    def get_float(self, name, default=None, **kw):
        return float(self.params.get(name, default))

    def get_int(self, name, default=None, **kw):
        return int(self.params.get(name, default))

    def respond_info(self, msg, log=True):
        self.out.append(msg)
        self.gcode.out.append(msg)


class FakePrinter:
    command_error = CommandError
    config_error = ConfigError

    def __init__(self, mcu):
        self.fake_mcu = mcu
        self.reactor = FakeReactor(mcu)
        self.objects = {'toolhead': FakeToolhead(self),
                        'webhooks': types.SimpleNamespace(
                            endpoints={},
                            register_mux_endpoint=self._reg_ep)}
        self.objects['gcode'] = FakeGcode(self)
        self.events = {}

    def _reg_ep(self, path, key, value, cb):
        self.objects['webhooks'].endpoints[(path, key, value)] = cb

    def lookup_object(self, name, default=Exception):
        if name in self.objects:
            return self.objects[name]
        if default is Exception:
            raise ConfigError("Unknown config object '%s'" % (name,))
        return default

    def get_reactor(self):
        return self.reactor

    def register_event_handler(self, ev, cb):
        self.events.setdefault(ev, []).append(cb)

    def run_host(self, seconds, step=0.05):
        """Zeit vorspulen: Firmware laufen lassen, faellige Reactor-Timer
        (BatchBulkHelper) ausfuehren."""
        mcu = self.fake_mcu
        end = mcu.now + int(seconds * FREQ)
        while mcu.now < end:
            nxt = min(end, mcu.now + int(step * FREQ))
            if mcu.sim is not None:
                mcu.sim.advance_to(nxt)
            mcu.now = nxt
            ev = mcu.now / float(FREQ)
            for t in list(self.reactor.timers):
                if t in self.reactor.timers and t[1] <= ev:
                    t[1] = t[0](ev)


class FakeConfig:
    error = ConfigError

    def __init__(self, printer, name, values):
        self.printer, self.name, self.values = printer, name, dict(values)

    def get_printer(self):
        return self.printer

    def get_name(self):
        return self.name

    def get(self, key, default=Exception):
        if key in self.values:
            return self.values[key]
        if default is Exception:
            raise ConfigError("Option '%s' fehlt" % key)
        return default

    @staticmethod
    def _limits(key, v, minval=None, maxval=None, **kw):
        # wie Klipper configfile: minval/maxval -> Config-Fehler
        if v is None:
            return v
        if minval is not None and v < minval:
            raise ConfigError("Option '%s' muss >= %s sein" % (key, minval))
        if maxval is not None and v > maxval:
            raise ConfigError("Option '%s' muss <= %s sein" % (key, maxval))
        return v

    def getint(self, key, default=Exception, **kw):
        v = self.get(key, default)
        return self._limits(key, None if v is None else int(v), **kw)

    def getfloat(self, key, default=Exception, **kw):
        v = self.get(key, default)
        return self._limits(key, None if v is None else float(v), **kw)

    def getboolean(self, key, default=Exception):
        v = self.get(key, default)
        if isinstance(v, bool):
            return v
        return str(v).lower() in ('1', 'true', 'yes')

    def getlist(self, key, default=Exception, sep=',', count=None):
        v = self.get(key, default)
        if isinstance(v, str):
            v = [p.strip() for p in v.split(sep) if p.strip()]
        v = list(v)
        if count is not None and len(v) != count:
            raise ConfigError("%s braucht %d Werte" % (key, count))
        return v

    def getlists(self, key, default=Exception, seps=(',',), count=None,
                 parser=str):
        v = self.get(key, default)
        rows = [r for r in re.split(r"\n", v) if r.strip()]
        out = []
        for r in rows:
            vals = [parser(p.strip()) for p in r.split(seps[0])]
            if count is not None and len(vals) != count:
                raise ConfigError("%s" % key)
            out.append(tuple(vals))
        return out


STOCK_OPTS = {
    'cs_pin': 'nozzle_mcu:PA4', 'spi_speed': '5000000', 'axes_map': 'x,z,y',
    'spi_software_sclk_pin': 'nozzle_mcu:PA5',
    'spi_software_mosi_pin': 'nozzle_mcu:PA7',
    'spi_software_miso_pin': 'nozzle_mcu:PA6',
}


def make_env(opts=None, fileoutput=False, f_chip=1600.0,
             value_fn=default_value, name="k2_lis2dw"):
    mcu = FakeMCU(fileoutput=fileoutput)
    mcu.sim = Sim(mcu, f_chip, value_fn)
    printer = FakePrinter(mcu)
    o = dict(STOCK_OPTS)
    o.update(opts or {})
    cfg = FakeConfig(printer, name, o)
    chip = KL.load_config(cfg)
    printer.objects[name] = chip   # Klipper: Objektname = Abschnittsname
    mcu.build()
    mcu.sim.fw.oid = chip.oid
    return mcu, printer, chip


# ---------------------------------------------------------------------------
# 1. Formate
# ---------------------------------------------------------------------------
def test_formats():
    for label, mp in (("Duese", MP_NOZ), ("Main", MP_MAIN)):
        for fmt in (KL.QUERY_FMT, KL.QUERY_STATUS_FMT, KL.DATA_FMT,
                    KL.STATUS_FMT, "config_lis2dw oid=%c spi_oid=%c"):
            try:
                mp.lookup_command(fmt)
                ok = True
            except msgproto.error as e:
                ok = False
                fmt = "%s (%s)" % (fmt, e)
            check("1 Format im Dictionary %s: %s" % (label, fmt.split()[0]),
                  ok, fmt)
    # Upstream-Formate scheitern genau wie im Batch-Lauf C
    bad = 0
    for fmt in ("query_lis2dw oid=%c rest_ticks=%u",
                "config_lis2dw oid=%c bus_oid=%c bus_oid_type=%c"
                " lis_chip_type=%c"):
        try:
            MP_NOZ.lookup_command(fmt)
        except msgproto.error as e:
            bad += "Command format mismatch" in str(e)
    check("1 Upstream-Formate -> 'Command format mismatch'", bad == 2)
    try:
        MP_NOZ.lookup_command("sensor_bulk_status oid=%c clock=%u "
                              "query_ticks=%u next_sequence=%hu buffered=%u "
                              "possible_overflows=%hu")
        unknown = False
    except msgproto.error as e:
        unknown = "Unknown command" in str(e)
    check("1 sensor_bulk_status unbekannt in Creality-FW", unknown)
    # Config-Befehle des Moduls
    mcu, printer, chip = make_env()
    check("1 config_lis2dw-Befehl", mcu.config_cmds == [
        "config_lis2dw oid=1 spi_oid=0"], mcu.config_cmds)
    check("1 on_restart Stopp-Befehl", mcu.restart_cmds == [
        "query_lis2dw oid=1 clock=0 rest_ticks=0"], mcu.restart_cmds)
    spi = chip.bus
    check("1 SPI wie Stock (Modus 3, 5 MHz, CS PA4, SW-SPI PA5/PA7/PA6)",
          spi.mode == 3 and spi.speed == 5000000
          and spi.cs_pin == 'nozzle_mcu:PA4'
          and spi.sw_pins == ('nozzle_mcu:PA5', 'nozzle_mcu:PA7',
                              'nozzle_mcu:PA6'))
    check("1 Blockung 8 Samples x 6 Byte",
          chip.ffreader.samples_per_block == 8
          and chip.ffreader.bytes_per_sample == 6)
    # Ohne cs_pin -> klarer Config-Fehler
    try:
        mcu2 = FakeMCU()
        mcu2.sim = Sim(mcu2)
        p2 = FakePrinter(mcu2)
        o = dict(STOCK_OPTS)
        del o['cs_pin']
        KL.load_config(FakeConfig(p2, "k2_lis2dw", o))
        ok = False
    except ConfigError as e:
        ok = "cs_pin" in str(e)
    check("1 fehlender cs_pin -> Config-Fehler", ok)


# ---------------------------------------------------------------------------
# 2. Status-Uebersetzung
# ---------------------------------------------------------------------------
def old_chip_clock(seq, params):
    # alter Host (lis2dw.py vor bulk_sensor-Umbau): seq*8 + buffered//6 + fifo + 1
    return seq * 8 + params['buffered'] // 6 + (params['fifo'] & 0x1f) + 1


def test_status():
    p = roundtrip(MP_NOZ, "lis2dw_status", oid=1, clock=0xfffffff0,
                  query_ticks=700, next_sequence=5, buffered=18, fifo=0x23,
                  limit_count=7)
    t = KL.translate_status(p)
    check("2 translate Felder", t['clock'] == 0xfffffff0
          and t['query_ticks'] == 700 and t['next_sequence'] == 5
          and t['possible_overflows'] == 7 and t['oid'] == 1, t)
    check("2 buffered = nur Firmware-Puffer (FIFO-Feld nicht mitgezaehlt)",
          t['buffered'] == 18, t['buffered'])
    t2 = KL.translate_status(p, count_fifo=True)
    check("2 count_fifo=True: buffered + (fifo&0x1f)*6 (alter Host)",
          t2['buffered'] == 18 + 3 * 6, t2['buffered'])
    rnd = random.Random(1)
    same = True
    for _ in range(500):
        seq = rnd.randrange(0, 70000)
        p = {'oid': 1, 'clock': rnd.randrange(1 << 32), 'query_ticks': 600,
             'next_sequence': seq & 0xffff,
             'buffered': rnd.randrange(0, 43, 6), 'fifo': rnd.randrange(64),
             'limit_count': rnd.randrange(1 << 16)}
        tt = KL.translate_status(p, count_fifo=True)
        new = seq * 8 + tt['buffered'] // 6 + 1
        same &= (new == old_chip_clock(seq, p))
    check("2 count_fifo=True: chip_clock == alter Host (500 Faelle)", same)
    # limit_count-Ueberlauf und Sequenz ueber den echten FixedFreqReader
    mcu, printer, chip = make_env()
    rd = chip.ffreader
    seqs = [0xfffe, 0xffff, 0x0000, 0x0003]
    lcs = [0xfffd, 0xffff, 0x0002, 0x0004]
    ok = True
    rd.last_sequence = 0xfffd
    rd.last_overflows = 0xfffc
    rd._clear_duration_filter()
    raw = []

    class Q:
        def send(self, data=(), minclock=0, reqclock=0, retry=True):
            return KL.translate_status(raw.pop(0))
    rd.query_status_cmd = Q()
    for s, lc in zip(seqs, lcs):
        raw.append({'oid': 1, 'clock': 1000, 'query_ticks': 500,
                    'next_sequence': s, 'buffered': 0, 'fifo': 0,
                    'limit_count': lc})
        rd._update_clock()
    ok = rd.last_sequence == 0x10003 and rd.last_overflows == 0x10004
    check("2 Sequenz/limit_count ueber 0xffff fortgezaehlt", ok,
          (hex(rd.last_sequence), hex(rd.last_overflows)))


# ---------------------------------------------------------------------------
# 3./4. Dekodierung + Achsen
# ---------------------------------------------------------------------------
def feed_and_pull(chip, blocks, last_sequence=0, chip_base=0):
    rd = chip.ffreader
    rd.last_sequence = last_sequence
    cs = rd.clock_sync
    # feste Zeitachse: 1 Sample = 75000 Ticks (1600 Hz), Basis 0
    cs.reset(0, chip_base)
    cs.mcu_clock_variance = 75000. * 1e6
    cs.chip_clock_covariance = 1e6

    class Q:
        def send(self, data=(), minclock=0, reqclock=0, retry=True):
            return {'clock': 0, 'query_ticks': 1 << 30,
                    'next_sequence': last_sequence & 0xffff, 'buffered': 0,
                    'possible_overflows': 0}
    rd.query_status_cmd = Q()
    rd.max_query_duration = 0
    for seq, raw in blocks:
        data = b"".join(struct.pack("<hhh", *r) for r in raw)
        params = roundtrip(MP_NOZ, "lis2dw_data", oid=chip.oid,
                           sequence=seq & 0xffff, data=data)
        chip.mcu.deliver("lis2dw_data", chip.oid, params)
    samples = rd.pull_samples()
    chip._convert_samples(samples)
    return samples


def test_decode():
    mcu, printer, chip = make_env()
    raws0 = [(1, -1, 32767), (-32768, 2048, -2048), (0x1234, -0x1234, 7),
             (100, 200, 300), (-100, -200, -300), (0, 0, 0), (1, 2, 3),
             (-7, 8, -9)]
    raws1 = [(11, 12, 13), (-21, -22, -23), (31, 32, 33)]   # Teilblock
    s = feed_and_pull(chip, [(0, raws0), (1, raws1)])
    check("3 Anzahl Samples 8 + 3", len(s) == 11, len(s))
    S = KL.SCALE
    exp = [(r[0] * S, r[2] * S, r[1] * S) for r in raws0 + raws1]  # x,z,y
    ok = all(abs(a[1] - round(e[0], 6)) < 1e-6 and abs(a[2] - round(e[1], 6))
             < 1e-6 and abs(a[3] - round(e[2], 6)) < 1e-6
             for a, e in zip(s, exp))
    check("3 Werte int16 LE + Achsen x,z,y", ok, (s[:2], exp[:2]))
    times = [x[0] for x in s]
    ok = all(abs(times[i] - i * 75000. / FREQ) < 2e-6 for i in range(len(s)))
    check("3 Zeitachse Index*1/1600 s (Block 1 = Sample 8..)", ok,
          times[:3] + times[8:9])
    # Byte-Layout direkt: XL,XH,YL,YH,ZL,ZH
    d = bytes([0x34, 0x12, 0xcc, 0xed, 0x07, 0x00])
    check("3 Layout XL,XH,YL,YH,ZL,ZH", struct.unpack("<hhh", d)
          == (0x1234, -0x1234, 7))
    # Sequenz-Ueberlauf: last_sequence 0x1fffe (Status sagt next=0xfffe),
    # Pakete 0xfffd (alt), 0xfffe, 0xffff, 0x0000
    mcu, printer, chip = make_env()
    blk = [(i, 0, 0) for i in range(8)]
    s = feed_and_pull(chip, [(0xfffd, blk), (0xfffe, blk), (0xffff, blk),
                             (0x0000, blk)],
                      last_sequence=0x1fffe, chip_base=0x1fffd * 8)
    t0 = [round((x[0] * FREQ) / 75000.) for x in s[::8]]
    check("3 Sequenz-Ueberlauf 0xffff->0x0000 (Zeitindex fortlaufend)",
          t0 == [0, 8, 16, 24], t0)
    # Achsen-Varianten
    for amap, expect in (("x,z,y", (1, 3, 2)), ("-x,-z,y", (-1, -3, 2)),
                         ("z,x,-y", (3, 1, -2))):
        mcu, printer, chip = make_env({'axes_map': amap})
        s = feed_and_pull(chip, [(0, [(1, 2, 3)])])
        got = tuple(round(v / KL.SCALE) for v in s[0][1:])
        check("4 axes_map %s" % amap, got == expect, got)
    one_g = 2048 * KL.SCALE
    check("4 1 g (Rohwert 2048) ~ 9807 mm/s^2", abs(one_g - 9807) < 30,
          one_g)


# ---------------------------------------------------------------------------
# 5. Start/Stopp
# ---------------------------------------------------------------------------
def test_start_stop():
    mcu, printer, chip = make_env()
    chip._start_measurements()
    sends = [d for k, d in chip.bus.log if k == "send"]
    check("5 Registerfolge CTRL2 06, FIFO 00 (Bypass), CTRL6 74 (ODR/4),"
          " CTRL1 94",
          sends == [[0x21, 0x06], [0x2e, 0x00], [0x25, 0x74], [0x20, 0x94]],
          sends)
    reads = [d[0] for k, d in chip.bus.log if k == "xfer"]
    check("5 WHO_AM_I zuerst gelesen + jede Schreibung rueckgelesen",
          reads == [0x8f, 0xa1, 0xae, 0xa5, 0xa0], [hex(r) for r in reads])
    q = [s for s in mcu.sent if s[0] == "query_lis2dw"]
    ok = (len(q) == 1 and q[0][1][0] == chip.oid
          and q[0][1][2] == 180000 and q[0][3] == q[0][1][1])
    check("5 query_lis2dw oid clock=reqclock rest_ticks=180000 (1.5 ms)", ok,
          q)
    st = [s for s in mcu.queries if s[0] == "query_lis2dw_status"]
    check("5 erste Statusabfrage mit minclock = Startzeitpunkt",
          len(st) == 1 and st[0][2] == q[0][1][1], st)
    check("5 Firmware gestartet (FIFO_CTRL C0 durch FW)",
          mcu.sim.chip.regs[0x2e] == 0xc0 and mcu.sim.fw.flags == 2)
    printer.run_host(0.3)
    chip._finish_measurements()
    endq = [s for s in mcu.queries if s[0] == "query_lis2dw"]
    check("5 Stopp als query_lis2dw oid clock=0 rest_ticks=0 (Query)",
          endq and endq[-1][1] == [chip.oid, 0, 0], endq)
    check("5 nach Stopp FIFO_CTRL 00 + Status ausgewertet",
          mcu.sim.chip.regs[0x2e] == 0x00
          and chip.last_end_status is not None
          and 'possible_overflows' in chip.last_end_status)
    # Falsche Chip-ID
    mcu, printer, chip = make_env()
    mcu.sim.chip.regs[0x0f] = 0x33
    try:
        chip._start_measurements()
        ok = False
    except CommandError as e:
        ok = "Invalid lis2dw id" in str(e)
    check("5 falsche Chip-ID -> command_error, nichts gestartet",
          ok and not [s for s in mcu.sent if s[0] == "query_lis2dw"])
    # Lesetakt und Tiefpass per Config
    mcu, printer, chip = make_env({'read_rest_ms': '2.5', 'bandwidth': '2'})
    chip._start_measurements()
    sends = [d for k, d in chip.bus.log if k == "send"]
    q = [s for s in mcu.sent if s[0] == "query_lis2dw"]
    check("5 read_rest_ms 2.5 / bandwidth 2 -> rest_ticks 300000, CTRL6 34"
          " (Stock-Werte)", q and q[0][1][2] == 300000
          and [0x25, 0x34] in sends, (q, sends))
    for opts, name in (({'read_rest_ms': '1.25'}, "read_rest_ms < 1.5"),
                       ({'read_rest_ms': '11'}, "read_rest_ms > 10"),
                       ({'bandwidth': '3'}, "bandwidth 3")):
        try:
            make_env(opts)
            ok = False
        except ConfigError:
            ok = True
        check("5 Config-Fehler bei %s" % (name,), ok)
    # Alter Ablauf (start_fifo_bypass False)
    mcu, printer, chip = make_env({'start_fifo_bypass': 'False'})
    chip._start_measurements()
    sends = [d for k, d in chip.bus.log if k == "send"]
    check("5 start_fifo_bypass False -> FIFO_CTRL C0 vorab (Creality-Ablauf)",
          sends[1] == [0x2e, 0xc0], sends)
    # Batch-Modus: keine Lesezugriffe, nur Schreiben + Befehle
    mcu, printer, chip = make_env(fileoutput=True)
    chip._start_measurements()
    chip._finish_measurements()
    kinds = set(k for k, d in chip.bus.log)
    check("5 Batch-Modus: nur spi_send, Start+Stopp gesendet",
          kinds == {"send"} and [s[0] for s in mcu.sent] == ["query_lis2dw"]
          and [s[0] for s in mcu.queries][-1] == "query_lis2dw")


# ---------------------------------------------------------------------------
# 6. Zeitachse gegen Firmware-Emulation
# ---------------------------------------------------------------------------
def run_timeline(seconds, f_chip, bypass=True, collect=True, opts=None):
    o = {'start_fifo_bypass': str(bypass)}
    o.update(opts or {})
    mcu, printer, chip = make_env(o, f_chip=f_chip)
    got = []

    def client(msg):
        if collect:
            got.extend(msg['data'])
        return True
    chip.batch_bulk.add_client(client)
    printer.run_host(seconds)
    sim = mcu.sim
    on = sim.chip.on_clock
    res = []
    approx = 0
    for ptime, x, y, z in got:
        rx = int(round(x / KL.SCALE))
        k = index_from_rx(rx, approx)
        approx = k + 1
        true = (on + (k + 1) * FREQ / f_chip) / FREQ
        res.append((k, ptime, true))
    return mcu, chip, res


def tstats(res, skip_s=1.0):
    """Zeitachse in ms: Versatz (Host-Zeit - wahre Sample-Zeit), Streuung,
    max. Abweichung vom Versatz, Lieferrate, Drift (Versatz 2. - 1. Haelfte),
    Index-Schritte (Dezimierung des 1600-Hz-Stroms)."""
    t0 = res[0][2]
    sel = [r for r in res if r[2] - t0 >= skip_s]
    errs = [(p - t) * 1000. for k, p, t in sel]
    mean = sum(errs) / len(errs)
    sd = math.sqrt(sum((e - mean) ** 2 for e in errs) / len(errs))
    mx = max(abs(e - mean) for e in errs)
    rate = (len(sel) - 1) / (sel[-1][1] - sel[0][1])
    h = len(errs) // 2
    drift = sum(errs[h:]) / (len(errs) - h) - sum(errs[:h]) / h
    steps = {}
    for a, b in zip(sel, sel[1:]):
        steps[b[0] - a[0]] = steps.get(b[0] - a[0], 0) + 1
    return {'offset': mean, 'sd': sd, 'max': mx, 'rate': rate,
            'drift': drift, 'steps': steps}


def test_timeline():
    f_chip = 1600.0 * (1 + 400e-6)          # Chip-Oszillator +400 ppm
    # a) Stock-Lesetakt 2,5 ms: Modell gibt die Live-Rate wieder
    mcu, chip, res = run_timeline(10.0, f_chip, opts={'read_rest_ms': '2.5'})
    st = tstats(res)
    print("  Lesetakt 2,5 ms (Stock): %.1f Samples/s, Schritte %s" % (
        st['rate'], st['steps']))
    check("6 Lesetakt 2,5 ms -> 383-390 Samples/s (live 22.10.: 385-388)",
          383 < st['rate'] < 390, st['rate'])
    check("6 Firmware liefert 1 Sample je Timer (Dezimierung 4/5)",
          set(st['steps']) <= {4, 5}, st['steps'])
    # b) Default 1,5 ms
    mcu, chip, res = run_timeline(30.0, f_chip)
    st = tstats(res)
    print("  Lesetakt 1,5 ms (Default): %.1f Samples/s, Versatz %.2f ms,"
          " Streuung %.3f ms, max %.3f ms, Drift %.3f ms, Schritte %s" % (
              st['rate'], st['offset'], st['sd'], st['max'], st['drift'],
              st['steps']))
    check("6 Default ~625 Samples/s (1/(1,5 ms + 0,1 ms))",
          620 < st['rate'] < 630, st['rate'])
    check("6 keine Leer-Schleife, keine Doppel-Lesungen",
          mcu.sim.fw.busy_reads == 0 and mcu.sim.chip.dup_reads == 0,
          (mcu.sim.fw.busy_reads, mcu.sim.chip.dup_reads))
    check("6 Samples streng aufsteigend (Dezimierung 2/3, kein Rueckschritt)",
          set(st['steps']) <= {2, 3}, st['steps'])
    check("6 Zeitachse: Streuung < 0,35 ms, max < 1,2 ms, Drift < 0,2 ms",
          st['sd'] < 0.35 and st['max'] < 1.2 and abs(st['drift']) < 0.2,
          st)
    check("6 Versatz = FIFO-Latenz 10-25 ms (konstant, fuer PSD egal)",
          10 < st['offset'] < 25, st['offset'])
    check("6 Lieferrate == geschaetzte Rate (Glaettung data_rate)",
          abs(chip.data_rate - st['rate']) < 10, (chip.data_rate,
                                                   st['rate']))
    # c) Chip-ODR +-10 %: Rate bleibt, keine Leer-Schleife (Bypass-Start)
    for fc in (1600.0 * 0.9, 1600.0 * 1.1):
        mcu, chip, res = run_timeline(5.0, fc)
        st = tstats(res)
        check("6 Chip %.0f Hz: ~625/s, keine Leer-Schleife, Streuung < 0,45"
              " ms" % fc, 620 < st['rate'] < 630
              and mcu.sim.fw.busy_reads == 0 and st['sd'] < 0.45,
              (st['rate'], mcu.sim.fw.busy_reads, st['sd']))
    # d) alter Host (FIFO-Feld mitgezaehlt) verzerrt die Zeitachse
    orig = KL.translate_status
    KL.translate_status = lambda p, count_fifo=False: orig(p, True)
    try:
        mcu, chip, res = run_timeline(10.0, f_chip,
                                      opts={'read_rest_ms': '2.5'})
        old = tstats(res)
    finally:
        KL.translate_status = orig
    print("  Alter Host (FIFO mitgezaehlt, 2,5 ms): Streuung %.3f ms, max"
          " %.3f ms" % (old['sd'], old['max']))
    check("6 FIFO-Feld mitzaehlen verzerrt (Streuung > 1 ms) – deshalb raus",
          old['sd'] > 1.0, old['sd'])
    # e) Creality-Ablauf (FIFO vorab voll) ebenso sauber
    mcu, chip, res = run_timeline(5.0, f_chip, bypass=False)
    st = tstats(res)
    check("6 start_fifo_bypass False: ~625/s, keine Leer-Schleife",
          620 < st['rate'] < 630 and mcu.sim.fw.busy_reads == 0,
          (st['rate'], mcu.sim.fw.busy_reads))
    # f) Gefahr, die die Untergrenze verhindert: zu kurzer Lesetakt
    mcu = FakeMCU()
    mcu.responses[("lis2dw_data", 1)] = lambda params: None
    sim = Sim(mcu, 1600.0)
    sim.fw.oid = 1
    sim.chip.write(0, 0x2e, 0x00)
    sim.chip.write(0, 0x20, 0x94)
    sim.fw.cmd_query(1000, int(0.0003 * FREQ))       # 0,3 ms
    try:
        sim.advance_to(int(0.05 * FREQ))
        hard = False
    except BusyLoop:
        hard = True
    print("  Lesetakt 0,3 ms: %d Leer-Lesungen, %d Doppel-Samples in 50 ms%s"
          % (sim.fw.busy_reads, sim.chip.dup_reads,
             " (Dauerschleife)" if hard else ""))
    check("6 Lesetakt 0,3 ms -> Leer-Schleife mit Doppel-Samples (Grund fuer"
          " read_rest_ms >= 1,5)", hard or (sim.fw.busy_reads > 100
                                            and sim.chip.dup_reads > 100),
          (sim.fw.busy_reads, sim.chip.dup_reads))
    # g) Langlauf ueber den 16-bit-Sequenzueberlauf (0x10000 Bloecke)
    t = time.time()
    mcu3, chip3, res3 = run_timeline(845.0, 1600.0, collect=True)
    st3 = tstats(res3[-20000:], skip_s=0.0)
    ks = [r[0] for r in res3]
    back = sum(1 for a, b in zip(ks, ks[1:]) if b <= a)
    check("6 Langlauf: Sequenz > 0x10000, ohne Rueckschritt, Zeitachse"
          " stabil", mcu3.sim.fw.seq > 0x10000 and back == 0
          and 10 < st3['offset'] < 25 and st3['sd'] < 0.35,
          (hex(mcu3.sim.fw.seq), back, st3))
    print("  Langlauf 845 s: %d Samples, FW-Sequenz 0x%x, Versatz am Ende"
          " %.2f ms (%.1f s Rechenzeit)" % (len(res3), mcu3.sim.fw.seq,
                                           st3['offset'], time.time() - t))


# ---------------------------------------------------------------------------
# 7. Integration (AccelCommandHelper, resonance_tester)
# ---------------------------------------------------------------------------
def test_integration():
    mcu, printer, chip = make_env()
    gc = printer.lookup_object('gcode')
    g = gc.run_mux("ACCELEROMETER_QUERY")
    out = " ".join(g.out)
    m = re.search(r"\(x, y, z\): (\S+), (\S+), (\S+)", out)
    ok = m is not None
    if ok:
        x, y, z = (float(v.rstrip(',')) for v in m.groups())
        ok = abs(z - 9807) < 30 and y < 0
    check("7 ACCELEROMETER_QUERY (ohne CHIP) -> z ~ 1 g", ok, out)
    g = gc.run_mux("ACCELEROMETER_QUERY", {'CHIP': 'k2_lis2dw'})
    check("7 ACCELEROMETER_QUERY CHIP=k2_lis2dw", "accelerometer values"
          in " ".join(g.out))
    # Messung endet: naechster Batch -> Client weg -> Stopp-Befehl
    printer.run_host(0.5)
    endq = [s for s in mcu.queries if s[0] == "query_lis2dw"]
    check("7 nach Ende automatisch gestoppt (query_lis2dw rest_ticks=0)",
          endq and endq[-1][1][2] == 0 and not chip.batch_bulk.is_started,
          endq)
    g = gc.run_mux("KTWO_ACCEL_STATUS")
    check("7 KTWO_ACCEL_STATUS Rohstatus", g.out and "next_sequence=" in
          g.out[0] and "messung=False" in g.out[0], g.out)
    g = gc.run_mux("ACCELEROMETER_DEBUG_READ", {'REG': '0x0f'})
    check("7 ACCELEROMETER_DEBUG_READ REG=0x0f -> 0x44", g.out
          and "0x44" in g.out[0], g.out)
    ep = printer.objects['webhooks'].endpoints
    check("7 Webhook lis2dw/dump_lis2dw sensor=k2_lis2dw",
          ("lis2dw/dump_lis2dw", "sensor", "k2_lis2dw") in ep, list(ep))
    # Upstream resonance_tester
    try:
        import numpy  # noqa: F401
        have_np = True
    except ImportError:
        have_np = False
    from extras import resonance_tester as urt
    printer.objects['configfile'] = types.SimpleNamespace()
    rcfg = FakeConfig(printer, "resonance_tester", {
        'accel_chip': 'k2_lis2dw', 'probe_points': '175,175,175',
        'min_freq': '20', 'max_freq': '120', 'accel_per_hz': '100'})
    rt = urt.ResonanceTester(rcfg)
    rt.connect()
    check("7 resonance_tester.connect akzeptiert k2_lis2dw",
          rt.accel_chips == [('xy', chip)] and chip.name == 'k2_lis2dw')
    if not have_np:
        print("  HINWEIS: numpy fehlt -> MEASURE_AXES_NOISE uebersprungen")
        return
    try:
        multiprocessing.set_start_method('fork', force=True)
    except (RuntimeError, ValueError):
        pass
    noise = random.Random(7)

    def noisy(k, clock):
        return (int(noise.gauss(0, 20)), 2048 + int(noise.gauss(0, 20)),
                int(noise.gauss(0, 20)))
    mcu, printer, chip = make_env(value_fn=noisy)
    printer.objects['configfile'] = types.SimpleNamespace()
    rt = urt.ResonanceTester(FakeConfig(printer, "resonance_tester", {
        'accel_chip': 'k2_lis2dw', 'probe_points': '175,175,175'}))
    rt.connect()
    g = FakeGcmd(printer.lookup_object('gcode'), {'MEAS_TIME': '2'})
    rt.cmd_MEASURE_AXES_NOISE(g)
    out = " ".join(g.out)
    m = re.search(r"accelerometer: (\S+) \(x\), (\S+) \(y\), (\S+) \(z\)",
                  out)
    check("7 MEASURE_AXES_NOISE (echter Upstream-Rechenweg)", m is not None
          and all(float(v) > 0 for v in m.groups()), out)
    print("  MEASURE_AXES_NOISE: %s" % out)


# ---------------------------------------------------------------------------
# 8./9. Namen, Syntax
# ---------------------------------------------------------------------------
def test_names_syntax():
    mcu, printer, chip = make_env()
    names = sorted(printer.lookup_object('gcode').real.ready_gcode_handlers)
    check("8 registrierte Befehle (Upstream-Pruefung + Parser)", names == [
        'ACCELEROMETER_DEBUG_READ', 'ACCELEROMETER_DEBUG_WRITE',
        'ACCELEROMETER_MEASURE', 'ACCELEROMETER_QUERY', 'KTWO_ACCEL_STATUS'],
        names)
    gc = FakeGcode(printer)
    try:
        gc.register_command("KTWO_LIS2DW_STATUS", lambda g: None)
        ok = False
    except (ConfigError, Exception) as e:
        ok = "Parser" in str(e)
    check("8 Gegenprobe: Ziffer im Namen wird vom Parser zerlegt", ok)
    # zweiter Chip mit Namen
    mcu2, printer2, chip2 = make_env(name="k2_lis2dw duese")
    names2 = printer2.lookup_object('gcode').real.mux_commands[
        'KTWO_ACCEL_STATUS'][1]
    check("8 [k2_lis2dw duese] -> CHIP=duese, kein Default",
          list(names2) == ['duese'], list(names2))
    for p in (MODULE_PATH, os.path.abspath(__file__)):
        with open(p, encoding="utf-8") as f:
            src = f.read()
        try:
            ast.parse(src, feature_version=(3, 9))
            ok = True
        except SyntaxError as e:
            ok = False
            print("  ", e)
        check("9 Python-3.9-Syntax %s" % os.path.basename(p), ok)
    with open(MODULE_PATH, encoding="utf-8") as f:
        src = f.read()
    check("9 keine UUID/Seriennummer im Modul",
          not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-", src, re.I))


def main():
    if not os.path.isdir(os.path.join(UPSTREAM_KLIPPY, "extras")):
        print("Upstream-Klipper nicht gefunden: %s (KLIPPER_SRC setzen)"
              % UPSTREAM_KLIPPY)
        sys.exit(2)
    t = time.time()
    for fn in (test_formats, test_status, test_decode, test_start_stop,
               test_timeline, test_integration, test_names_syntax):
        print("== %s" % fn.__name__)
        fn()
    bad = [r for r in RESULTS if not r[1]]
    print("\n%d Pruefungen, %d bestanden, %d fehlgeschlagen (%.1f s)" % (
        len(RESULTS), len(RESULTS) - len(bad), len(bad), time.time() - t))
    if bad:
        for name, ok, detail in bad:
            print("FEHLER:", name, detail)
        sys.exit(1)
    print("ALLE TESTS BESTANDEN")


if __name__ == "__main__":
    main()
