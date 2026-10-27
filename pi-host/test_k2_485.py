#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline-Tests für k2_485.py ([k2_485], gemeinsamer RS485-Bus).

Läuft mit ``python3 test_k2_485.py`` – ohne Klipper, ohne Hardware, ohne
pyserial (Fake-Seriell wird über die echte nachbau/serial485.Serial485 mit
``ser=``-Injektion betrieben). Prüft:

1. Laden: Objekt da, KEIN Port geöffnet, KTWO_BUS_STATUS gültig (Upstream-Regel),
   Connect-/Disconnect-Handler registriert.
2. Connect öffnet GENAU EINE Serial485-Instanz; Motor-Frame byte-genau über
   transact_full und über k2_transport.Rs485Transport (Weg von [k2_motor]).
3. Gemeinsame Instanz: [k2_motor] (Rs485Transport), [k2_box]/[k2_belt]
   (lookup_object('k2_485')) bekommen dasselbe Objekt – ein Port, ein Lock.
4. Batch-Modus (debugoutput): Port wird nie geöffnet, klare Meldung,
   Zugriff -> RuntimeError.
5. Port fehlt (echter Serial485-Pfad, nicht existierender Pfad): Connect ohne
   Absturz, Meldung, get_status open=False; späterer Zugriff öffnet neu.
6. Port fällt im Betrieb aus (OSError) -> RuntimeError, geschlossen, Re-Open;
   TimeoutError (keine Antwort) lässt den Port offen.
7. Python-3.9-Syntax der Module.
8. Sendeschutz Motor-Kalibrierung (17.10.2026):
   an Motor-Adressen 0x80-0x88/0xFD nie fun 0x03/0x04, fun 0x07 nur Sub 02/06
   – serial485 und lokaler k2_transport-Fallback gleich, über k2_485,
   Rs485Transport und TransparentTransport nichts gesendet; Box/Belt/Rack
   mit denselben fun-Codes unberührt.
8b. Update-/Bootloader-/Service-Sperre (18.10.2026): Matrix je Adresse/Sub – fun 0xF0 nur [00]/[02]/[03], nie fun 0xF2,
   nie Ziel 0xF0, an 0xEA/0xEB nie 0x50/0x51/0x56; lokaler Fallback über
   alle addr x fun gleichwertig; veraltetes serial485 (ohne TX_GUARD_REV /
   kleiner Stand) -> lokaler Fallback; legitime Pfade frei; über k2_485,
   Rs485Transport und TransparentTransport nichts gesendet.
8c. Stufe 2 (18.10.2026): Bootloader-Adressen
   0x23/0x24/0x85-0x88 jedes fun gesperrt; Belt 0x21/0x22 Whitelist (fun 0/6
   leer, 2 [03], 4 [03]+12 B, 8 [0/1][Weg BE32 1..50], <= 32 B); RFID 0x11
   nur 0x02/0x14 ohne Daten; 0x02/0x14 nie an 0xFF/0xFD. Matrix, Fallback-
   Gleichwertigkeit, veraltetes serial485 -> Fallback, k2_belt-Pfade
   (INFO/Lesen/Flash/Move/Cali/Motor-0x05) ohne Treffer, Weg > 50 nie auf
   dem Draht, alle Sendewege.
9. L2 transparent-Kanal (18.10.2026, Abgleich mit Creality-klippy):
   KlippyTransparentAdapter.setup() nutzt lookup_query_command(is_async=True)
   und ersetzt den Upstream-Sende-Helfer durch TransparentOneShotCommand
   (Batch/fileoutput: DummyResponse bleibt). Mit Fake-Reactor/-Seriell im
   Upstream-Ablauf (CommandQueryWrapper._do_send): späte Antwort (1,9 s) ->
   GENAU 1 transparent_send, 0 Neusendungen; ohne Antwort -> TimeoutError
   nach > 60 s (Stock CR:mcu.py:493), weiterhin 1 Sendung, Callback
   abgemeldet; veraltete Antwort (#sent_time vor der Anfrage) zählt nicht;
   Gegenprobe: der Upstream-Helfer RetryAsyncCommand (Logik 1:1) hätte
   mehrfach gesendet. Quelltext-Abgleich gegen den Upstream-Klon und die
   Creality-Quelle (übersprungen, wenn nicht vorhanden).
10. rs485_de (K2-CM5-Träger Alpha 1, 24.10.2026): Standard ``adapter`` setzt
   KEIN ioctl; ``kernel`` prüft per TIOCGRS485 und setzt bei Bedarf per
   TIOCSRS485 ENABLED + RTS_ON_SEND ohne RTS_AFTER_SEND (DE aktiv high),
   Verzögerungen bleiben; schon passender Device-Tree -> kein Setzen;
   invertierte Polarität wird korrigiert; Treiber ohne RS485 (ENOTTY) bzw.
   fehlgeschlagene Rückprüfung -> Port bleibt zu, Meldung, kein Absturz;
   ungültiger Wert -> Config-Fehler; Port ohne fileno -> Meldung.
"""

import ast
import logging
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import k2_485 as B  # noqa: E402
import k2_transport as T  # noqa: E402

S485 = T.import_nachbau("serial485")

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond)))
    print("%s %s %s" % ("PASS" if cond else "FAIL", name, detail))


# ---------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------

class FakeSerial:
    """Halbduplex-Port: pro write() eine geskriptete Antwort (None = Stille)."""

    def __init__(self, script=(), fail_on_write=False):
        self.script = list(script)
        self.written = []
        self.buf = bytearray()
        self.fail_on_write = fail_on_write
        self.closed = False

    def reset_input_buffer(self):
        self.buf.clear()

    def write(self, frame):
        if self.fail_on_write:
            raise OSError(5, "Input/output error (Adapter gezogen)")
        self.written.append(bytes(frame))
        self.buf.clear()
        if self.script:
            nxt = self.script.pop(0)
            if nxt is not None:
                self.buf += bytes(nxt)
        return len(frame)

    def read(self, n=1):
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def close(self):
        self.closed = True


class Factory:
    def __init__(self, *serials):
        self.serials = list(serials)
        self.calls = []
        self.instances = []

    def __call__(self, port, baud, timeout):
        self.calls.append((port, baud, timeout))
        ser = self.serials.pop(0) if self.serials else FakeSerial()
        inst = S485.Serial485(port, baud, timeout=timeout, ser=ser)
        self.instances.append(inst)
        return inst


class FakeGcode:
    def __init__(self):
        self.commands = {}
        self.infos = []

    def register_command(self, name, func, desc=None):
        self.commands[name] = func

    def respond_info(self, msg):
        self.infos.append(str(msg))


class FakePrinter:
    def __init__(self, debugoutput=None):
        self.gcode = FakeGcode()
        self.objects = {"gcode": self.gcode}
        self.handlers = {}
        self.start_args = {}
        if debugoutput is not None:
            self.start_args["debugoutput"] = debugoutput

    def lookup_object(self, name, default=RuntimeError):
        if name in self.objects:
            return self.objects[name]
        if default is not RuntimeError:
            return default
        raise RuntimeError("Unknown config object '%s'" % name)

    def register_event_handler(self, ev, cb):
        self.handlers.setdefault(ev, []).append(cb)

    def get_start_args(self):
        return self.start_args

    def send(self, ev):
        for cb in self.handlers.get(ev, []):
            cb()

    def load_section(self, mod, config):
        # wie klippy.py load_object: Rückgabe unter dem Abschnittsnamen
        self.objects[config.get_name()] = mod.load_config(config)
        return self.objects[config.get_name()]


class FakeConfig:
    def __init__(self, printer, opts, name="k2_485"):
        self.printer = printer
        self.opts = dict(opts)
        self.name = name

    def get_printer(self):
        return self.printer

    def get_name(self):
        return self.name

    def get(self, key, default=RuntimeError):
        if key in self.opts:
            return self.opts[key]
        if default is RuntimeError:
            raise ValueError("Option '%s' in section '%s' must be specified"
                             % (key, self.name))
        return default

    def getint(self, key, default=None, minval=None, maxval=None):
        v = int(self.get(key, default))
        if minval is not None and v < minval:
            raise ValueError("minval")
        return v

    def getfloat(self, key, default=None, minval=None, maxval=None,
                 above=None, below=None):
        v = float(self.get(key, default))
        if above is not None and v <= above:
            raise ValueError("above")
        return v

    def error(self, msg):
        return ValueError(msg)


def upstream_gcode_name_ok(cmd):
    """Nachbau der Namensprüfung aus Upstream gcode.py register_command."""
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


PORT = "/dev/serial/by-id/usb-TEST_RS485-if00-port0"   # Platzhalter
# Motor X (0x81) Version 0x0F [0x01] -> Antwort mit 4 Datenbytes (Fake-Werte)
REQ = bytes(S485.build_frame(0x81, 0x0F, [0x01]))
RESP = bytes(S485.build_frame(0x81, 0x0F, [0x01, 0x09, 0x07, 0x51]))


def make(debugoutput=None, factory=None, opts=None):
    B.SERIAL_FACTORY = factory
    pr = FakePrinter(debugoutput=debugoutput)
    o = {"serial": PORT, "baud": "230400"}
    o.update(opts or {})
    obj = pr.load_section(B, FakeConfig(pr, o))
    return pr, obj


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------

def test_laden_und_connect():
    fac = Factory(FakeSerial([RESP]))
    pr, bus = make(factory=fac)
    check("Laden: Objekt 'k2_485' registriert", pr.lookup_object("k2_485") is bus)
    check("Laden: kein Port geöffnet", not bus.is_open() and fac.calls == [])
    check("Laden: KTWO_BUS_STATUS gültiger Upstream-Name",
          list(pr.gcode.commands) == ["KTWO_BUS_STATUS"]
          and upstream_gcode_name_ok("KTWO_BUS_STATUS")
          and not upstream_gcode_name_ok("K2_BUS_STATUS")
          and not upstream_gcode_name_ok("KTWO_485_STATUS"))
    check("Laden: connect/disconnect-Handler",
          "klippy:connect" in pr.handlers and "klippy:disconnect" in pr.handlers)
    pr.send("klippy:connect")
    check("Connect: genau eine Serial485-Instanz offen",
          bus.is_open() and len(fac.instances) == 1
          and fac.calls == [(PORT, 230400, 1.0)], fac.calls)
    check("Connect: Meldung 'offen'", any("offen" in i for i in pr.gcode.infos),
          pr.gcode.infos)
    addr, st, fun, data, _raw = bus.transact_full(0x81, 0x0F, [0x01], timeout=0.2,
                                                  retries=0)
    ser = fac.instances[0].ser
    check("transact_full: Request byte-genau", ser.written == [REQ],
          [w.hex(" ") for w in ser.written])
    check("transact_full: Antwort geparst",
          (addr, st, fun, bytes(data)) == (0x81, 0, 0x0F, bytes([1, 9, 7, 0x51])))
    # Weg von [k2_motor]: Rs485Transport über dasselbe Objekt
    ser.script.append(RESP)
    tr = T.Rs485Transport(pr.lookup_object("k2_485"), name="k2_485")
    res = tr.transact(0x81, 0x0F, [0x01], timeout=0.2, retries=0)
    check("Rs485Transport über k2_485", res == (0, 0x0F, bytes([1, 9, 7, 0x51])), res)
    check("eine Instanz für alle (kein zweites Öffnen)", len(fac.calls) == 1)
    st = bus.get_status()
    check("get_status/stats", st["open"] and st["stats"].get("sent") == 2, st)
    g = type("G", (), {"infos": [], "respond_info": lambda self, m: self.infos.append(m)})()
    pr.gcode.commands["KTWO_BUS_STATUS"](g)
    check("KTWO_BUS_STATUS ohne Bus-Verkehr", "offen=True" in g.infos[0]
          and len(ser.written) == 2, g.infos)
    pr.send("klippy:disconnect")
    check("Disconnect schließt", not bus.is_open())


def test_gemeinsam_mit_modulen():
    """[k2_motor]/[k2_box]/[k2_belt] holen dasselbe Objekt."""
    fac = Factory(FakeSerial())
    pr, bus = make(factory=fac)
    import k2_box as KBOX  # noqa: F401  (Import ohne sys.path-Manipulation)
    import k2_belt as KBELT
    import k2_motor_init as KM
    found = []
    for name in KBELT.BUS_OBJECT_NAMES[:1] + ("k2_485",):
        found.append(pr.lookup_object(name))
    check("Belt/Box-Suchname 'k2_485' liefert dieselbe Instanz",
          KBELT.BUS_OBJECT_NAMES[0] == "k2_485" and all(f is bus for f in found))
    check("k2_motor BUS_SECTION = 'k2_485'", KM.BUS_SECTION == "k2_485")
    ext = [p for p in sys.path if os.path.realpath(p) == os.path.realpath(_HERE)]
    check("Modulordner nicht von den Modulen in sys.path eingefügt",
          len(ext) == 1, ext)
    nb = [p for p in sys.path if p and os.path.realpath(p) == T.NACHBAU_DIR]
    check("nachbau/ angehängt (nicht vorne)",
          nb and sys.path.index(nb[0]) > 0, (T.NACHBAU_DIR, sys.path[:3]))
    # Belt am Bus ohne nutzbaren Port -> BeltError (G-Code-Fehler), kein
    # RuntimeError durch die Hülle (sonst Klipper-Shutdown, Batch-Probe G).
    B.SERIAL_FACTORY = None
    pr2, bus2 = make(debugoutput="/tmp/x.serial")
    ctl = KBELT.K2BeltController("mdlx", KBELT.parse_options({}, "mdlx"), bus=bus2,
                                 pause=lambda s: None, respond=lambda m: None,
                                 run_gcode=lambda l: None)
    try:
        ctl.read_version()
        err = None
    except Exception as exc:
        err = exc
    check("k2_belt: Bus-Fehler -> BeltError", isinstance(err, KBELT.BeltError)
          and "Batch-Modus" in str(err), repr(err))
    import statistics
    check("import statistics = Standardbibliothek",
          hasattr(statistics, "median")
          and "extras" not in os.path.dirname(statistics.__file__))


def test_batch():
    fac = Factory()
    pr, bus = make(debugoutput="/tmp/out.serial", factory=fac)
    pr.send("klippy:connect")
    check("Batch: Port nie geöffnet", not bus.is_open() and fac.calls == [])
    check("Batch: Meldung", any("Batch-Modus" in i for i in pr.gcode.infos),
          pr.gcode.infos)
    try:
        bus.transact_full(0x81, 0x0F, [1])
    except RuntimeError as exc:
        check("Batch: Zugriff -> RuntimeError mit Grund", "Batch-Modus" in str(exc),
              str(exc))
    else:
        check("Batch: Zugriff -> RuntimeError mit Grund", False)


def test_port_fehlt():
    pr, bus = make(factory=None,
                   opts={"serial": "/dev/serial/by-id/usb-GIBT-ES-NICHT-if00"})
    try:
        pr.send("klippy:connect")
        crashed = None
    except BaseException as exc:  # auch SystemExit (pyserial fehlt)
        crashed = exc
    check("Port fehlt: Connect ohne Absturz", crashed is None, repr(crashed))
    check("Port fehlt: Meldung", any("nicht zu öffnen" in i for i in pr.gcode.infos),
          pr.gcode.infos)
    st = bus.get_status()
    check("Port fehlt: get_status open=False + error",
          st["open"] is False and st["error"], st["error"])
    try:
        bus.transact(0x81, 0x0F, [1], timeout=0.05, retries=0)
    except RuntimeError as exc:
        check("Port fehlt: Zugriff -> RuntimeError", "nicht zu öffnen" in str(exc))
    else:
        check("Port fehlt: Zugriff -> RuntimeError", False)
    # Adapter nachträglich da -> nächster Zugriff öffnet
    fac = Factory(FakeSerial([RESP]))
    B.SERIAL_FACTORY = fac
    data = bus.transact(0x81, 0x0F, [1], timeout=0.2, retries=0)
    check("Port später da: Re-Open beim Zugriff",
          bytes(data) == bytes([1, 9, 7, 0x51]) and bus.is_open()
          and bus.last_error is None)


def test_ausfall_timeout():
    fac = Factory(FakeSerial([None]), FakeSerial(fail_on_write=True), FakeSerial([RESP]))
    pr, bus = make(factory=fac)
    pr.send("klippy:connect")
    try:
        bus.transact(0x81, 0x0F, [1], timeout=0.05, retries=0)
        ok = False
    except TimeoutError:
        ok = True
    check("TimeoutError durchgereicht, Port bleibt offen", ok and bus.is_open())
    bus.close()
    try:
        bus.transact(0x81, 0x0F, [1], timeout=0.05, retries=0)
        msg = ""
    except RuntimeError as exc:
        msg = str(exc)
    check("OSError -> RuntimeError 'ausgefallen', geschlossen",
          "ausgefallen" in msg and not bus.is_open(), msg)
    data = bus.transact(0x81, 0x0F, [1], timeout=0.2, retries=0)
    check("danach Re-Open", bytes(data) == bytes([1, 9, 7, 0x51])
          and len(fac.calls) == 3)


def test_konsole_statistik():
    """Konsolen-Audit 23.10.2026: Bus-Statistik-Wächter (rein) + Timer."""
    W = B.BusStatWaechter
    w = W(fenster=600, timeouts=30, crc_errors=10, retries=30, karenz=180,
          start=0.0)

    def st(sent, to=0, crc=0, rt=0):
        return {"sent": sent, "received": sent - to, "timeouts": to,
                "crc_errors": crc, "retries": rt}
    check("Karenz: nichts, Verlauf leer",
          w.pruefe(60, st(100, 50, 50, 50)) is None and w.verlauf == [])
    check("erste Probe nach Karenz: keine Warnung", w.pruefe(200, st(200, 5)) is None)
    check("knapp unter den Grenzen: keine Warnung",
          w.pruefe(500, st(400, 34, 9, 29)) is None)
    txt = w.pruefe(560, st(450, 35, 9, 29))
    check("30 Timeouts im Fenster -> Warnung mit Zahlen",
          txt is not None and "30 Timeouts" in txt and "timeouts" in txt
          and "KTWO_BUS_STATUS" in txt, txt)
    check("nach Warnung Fenster neu", w.pruefe(620, st(460, 36)) is None)
    # langsam über lange Zeit: nie 30 in 10 min -> keine Warnung
    w2 = W(start=0.0)
    hits = [w2.pruefe(t, st(t, t // 60 * 2)) for t in range(180, 7200, 60)]
    check("2 Timeouts/min über 2 h: keine Warnung (je Fenster ~20)",
          all(h is None for h in hits))
    w3 = W(start=0.0)
    w3.pruefe(200, st(100))
    check("CRC 10 -> Warnung", "crc_errors" in (w3.pruefe(260, st(120, 0, 10)) or ""))
    w4 = W(start=0.0)
    w4.pruefe(200, st(500, 40, 9))
    check("Zähler fällt (Port neu) -> Verlauf neu, keine Warnung",
          w4.pruefe(260, st(10, 1)) is None and len(w4.verlauf) == 1)
    w5 = W(timeouts=0, crc_errors=0, retries=0)
    check("alle Grenzen 0 -> inaktiv", not w5.aktiv()
          and w5.pruefe(1e6, st(1, 99, 99, 99)) is None)
    check("unsinnige Zähler -> None", W(start=0.0).pruefe(500, {"sent": "x"}) is None)

    # Timer-Integration
    class R:
        NEVER = 9e99

        def __init__(self):
            self.t, self.timers = 1000.0, []

        def monotonic(self):
            return self.t

        def register_timer(self, cb, when):
            self.timers.append([cb, when])
            return cb

    class G(FakeGcode):
        def __init__(self):
            FakeGcode.__init__(self)
            self.raw = []

        def respond_raw(self, z):
            self.raw.append(z)

    fac = Factory(FakeSerial())
    pr, bus = make(factory=fac)
    pr.gcode = pr.objects["gcode"] = G()
    bus.gcode = bus.konsole._gcode = pr.gcode
    r = R()
    pr.get_reactor = lambda: r
    pr.send("klippy:connect")
    pr.send("klippy:ready")
    timer = [t for t in r.timers if t[0] == bus._stat_timer]
    check("ready: Statistik-Timer alle 60 s", len(timer) == 1
          and timer[0][1] == 1000.0 + B.STAT_TAKT, r.timers)
    zaehler = {"sent": 0, "received": 0, "timeouts": 0, "retries": 0,
               "crc_errors": 0}
    bus.bus_stats = lambda: dict(zaehler)
    t = 1000.0
    for i in range(4):                 # Karenz (180 s) + erste Proben
        t += 60
        bus._stat_timer(t)
    zaehler.update(sent=300, timeouts=40)
    t += 60
    nxt = bus._stat_timer(t)
    warn = [z for z in pr.gcode.raw if z.startswith("// [k2_485] RS485-Bus:")]
    check("Timer: Warnung // [k2_485] RS485-Bus mit 40 Timeouts",
          len(warn) == 1 and "40 Timeouts" in warn[0] and nxt == t + B.STAT_TAKT,
          pr.gcode.raw)
    zaehler.update(sent=600, timeouts=80)
    t += 60
    bus._stat_timer(t)
    warn = [z for z in pr.gcode.raw if z.startswith("// [k2_485] RS485-Bus:")]
    check("Timer: zweite Warnung entprellt (30 min)", len(warn) == 1, pr.gcode.raw)
    bus.fail_count, bus.last_error = 1, "SerialException: weg"
    bus._bus = None
    t += 60
    bus._stat_timer(t)
    rot = [z for z in pr.gcode.raw if z.startswith("!! [k2_485]")]
    check("Timer: Port-Ausfall rot (einmal, kein zweites 'nicht offen')",
          len(rot) == 1 and "ausgefallen" in rot[0], pr.gcode.raw)
    bus._bus = fac.instances[0]
    t += 60
    bus._stat_timer(t)
    check("Timer: Port wieder offen gemeldet",
          pr.gcode.raw[-1].startswith("// [k2_485] RS485-Port")
          and "wieder offen" in pr.gcode.raw[-1], pr.gcode.raw[-1:])
    bus.bus_stats = lambda: 1 / 0
    logging.disable(logging.CRITICAL)    # erwarteter Traceback im Log
    try:
        bus._stat_timer(t + 60)
        ok = True
    except Exception:
        ok = False
    logging.disable(logging.NOTSET)
    check("Timer: Ausnahme wird nicht geworfen", ok)


def test_config_fehler():
    B.SERIAL_FACTORY = Factory()
    pr = FakePrinter()
    try:
        pr.load_section(B, FakeConfig(pr, {"baud": "230400"}))
        msg = ""
    except ValueError as exc:
        msg = str(exc)
    check("serial: fehlt -> Config-Fehler", "serial" in msg, msg)


def test_py39():
    bad = []
    for f in ("k2_485.py", "k2_transport.py", "k2_motor_init.py", "k2_box.py",
              "k2_belt.py", "k2_prtouch.py", "test_k2_485.py"):
        src = open(os.path.join(_HERE, f), encoding="utf-8").read()
        try:
            ast.parse(src, filename=f, feature_version=(3, 9))
        except SyntaxError as exc:
            bad.append("%s: %s" % (f, exc))
    check("Python-3.9-Syntax", not bad, bad)


class _FakeTransparent(T.TransparentMcuAdapter):
    """Zeichnet transparent-Sendungen auf, antwortet nie (b'')."""

    def __init__(self):
        self.writes = []

    def exchange(self, write, timeout_ms):
        self.writes.append(bytes(write))
        return b""


def _motor_guard_cases():
    """(addr, fun, data, erlaubt) – Motor gesperrt/erlaubt, Box/Belt frei."""
    cases = []
    for a in list(range(0x80, 0x89)) + [0xFD]:
        cases += [(a, 0x03, [], False), (a, 0x03, [0x00], False),
                  (a, 0x04, [0x00], False), (a, 0x04, [], False),
                  (a, 0x07, [], False), (a, 0x07, [0x00], False),
                  (a, 0x07, [0x01], False), (a, 0x07, [0x03, 0x00], False),
                  (a, 0x07, [0x04, 0x00], False),
                  (a, 0x07, [0x05, 0x14, 0, 0, 0x80, 0x3F], False),
                  (a, 0x07, [0x07], False),
                  # 0x85-0x88 = Motor-Bootloader: seit Stufe 2 jedes fun gesperrt
                  (a, 0x07, [0x02], a not in S485.TX_BOOTLOADER_ADDRS),
                  (a, 0x07, [0x06, 0x14], a not in S485.TX_BOOTLOADER_ADDRS)]
    cases += [(0x81, 0x06, [0x02, 0x46], True), (0x81, 0x0F, [0x01], True),
              (0x81, 0x05, [0x02], True), (0x81, 0x0E, [0x02], True)]
    # Box (0x01-0x04, Gruppe 0xFE): 0x03 Restmenge, 0x04 Modus, 0x07 Hauptmotor
    for a in (0x01, 0x02, 0x03, 0x04, 0xFE):
        cases += [(a, 0x03, [0x0F], True), (a, 0x04, [0x00, 0x01], True),
                  (a, 0x04, [0x0F, 0x00], True), (a, 0x07, [0x01], True),
                  (a, 0x07, [0x02], True), (a, 0x07, [0x00], True)]
    # Belt (0x21/0x22): 0x04 WRITE_FLASH, 0x02 READ_FLASH (seit Stufe 2 nur
    # Whitelist-Payload [03](+12 B)); Rack 0x11 (nur 0x02/0x14); 0xFF/0xFC
    for a in (0x21, 0x22):
        cases += [(a, 0x04, [0x03] + [0] * 11 + [0x8C], True),
                  (a, 0x02, [0x03], True)]
    cases += [(0x11, 0x02, [], True), (0xFF, 0x03, [], True),
              (0xFF, 0x04, [0x00], True), (0xFF, 0x07, [0x05], True),
              (0xFC, 0x04, [0x00], True)]
    return cases


def test_motor_sendeschutz():
    cases = _motor_guard_cases()
    check("Sendeschutz: k2_transport nutzt serial485.check_tx_frame",
          T.check_tx_frame is S485.check_tx_frame
          and T.TxGuardError is S485.TxGuardError
          and hasattr(S485, "TX_MOTOR_GUARD_ADDRS"))
    check("Sendeschutz: Motor-Adressen identisch (serial485 = lokal)",
          S485.TX_MOTOR_GUARD_ADDRS == T.TX_MOTOR_GUARD_ADDRS
          == frozenset(list(range(0x80, 0x89)) + [0xFD])
          and S485.TX_FLASH_PARAM_READ_SUBS == T.TX_FLASH_PARAM_READ_SUBS
          == frozenset((0x02, 0x06)))
    for name, fn in (("serial485", S485.check_tx_frame),
                     ("lokal", T._check_tx_frame_local)):
        wrong = []
        for addr, fun, data, ok in cases:
            try:
                fn(addr, fun, data)
                got = True
            except ValueError:
                got = False
            if got != ok:
                wrong.append("0x%02X/0x%02X %s" % (addr, fun, data))
        check("Sendeschutz Matrix %s (%d Fälle)" % (name, len(cases)),
              not wrong, wrong[:5])
    try:
        S485.check_tx_frame(0x83, 0x04, [0x00])
        msg = ""
    except S485.TxGuardError as exc:
        msg = str(exc)
    check("Sendeschutz: Meldung nennt elec_offset + nichts gesendet",
          "elec_offset" in msg and "nichts gesendet" in msg, msg)

    # über [k2_485] (echte Serial485 + Fake-Port): gesperrt -> nichts auf dem Draht
    fac = Factory(FakeSerial([None] * 8))
    pr, bus = make(factory=fac)
    pr.send("klippy:connect")
    ser = fac.instances[0].ser
    blocked = [c[:3] for c in cases if not c[3]]
    nraise = 0
    for addr, fun, data in blocked:
        try:
            bus.transact_full(addr, fun, data, timeout=0.01, retries=0)
        except S485.TxGuardError:
            nraise += 1
        except Exception:
            pass
    check("k2_485: alle gesperrten Motor-Frames -> TxGuardError, Draht leer",
          nraise == len(blocked) and ser.written == [], (nraise, len(blocked)))
    # Box/Belt über denselben Bus: unverändert byte-genau gesendet
    import k2_belt as KBELT
    box_req = (0x01, 0x04, [0x00, 0x01])
    belt_req = (KBELT.ADDR_MDLX, KBELT.FUN_WRITE_FLASH, [0x03] + [0] * 11 + [0x8C])
    for addr, fun, data in (box_req, belt_req, (0x81, 0x07, [0x06, 0x14])):
        try:
            bus.transact_full(addr, fun, data, timeout=0.01, retries=0)
        except TimeoutError:
            pass
    check("k2_485: Box 0x04 / Belt WRITE_FLASH / Motor 0x07 [06] gesendet",
          ser.written[:1] == [bytes(S485.build_frame(*box_req))]
          and ser.written[1:2] == [bytes(S485.build_frame(*belt_req))]
          and ser.written[2:3] == [bytes(S485.build_frame(0x81, 0x07, [0x06, 0x14]))],
          [w.hex(" ") for w in ser.written])
    pr.send("klippy:disconnect")

    # Rs485Transport (Weg von [k2_motor]) und TransparentTransport (Extruder 0x81)
    class _RecBus:
        def __init__(self):
            self.calls = []

        def transact_full(self, addr, fun, data, timeout=None, retries=0):
            self.calls.append((addr, fun, list(data)))
            raise TimeoutError("still")
    rb = _RecBus()
    rs = T.Rs485Transport(rb)
    mcu = _FakeTransparent()
    tr = T.TransparentTransport(mcu)
    nr = nt = 0
    for addr, fun, data in blocked:
        try:
            rs.transact(addr, fun, data, timeout=0.01, retries=0)
        except T.TxGuardError:
            nr += 1
        except TimeoutError:
            pass
        try:
            tr.transact(addr, fun, data, timeout=0.01, retries=0)
        except T.TxGuardError:
            nt += 1
        except TimeoutError:
            pass
    check("Rs485Transport/TransparentTransport: gesperrt, nichts gesendet",
          nr == nt == len(blocked) and rb.calls == [] and rs.sent == []
          and mcu.writes == [] and tr.sent == [], (nr, nt, rb.calls, mcu.writes))
    for t in (rs, tr):
        try:
            t.transact(0x81, 0x07, [0x02], timeout=0.01, retries=0)
        except TimeoutError:
            pass
    check("Rs485/transparent: Motor 0x07 [02] (lesend) erlaubt",
          rb.calls == [(0x81, 0x07, [0x02])]
          and mcu.writes == [bytes(S485.build_frame(0x81, 0x07, [0x02]))],
          (rb.calls, mcu.writes))


# ---------------------------------------------------------------------
# Update-/Bootloader-/Service-Sperre (18.10.2026)
# ---------------------------------------------------------------------

# Adressen: CFS 0x01-0x04, RFID/Rack 0x11, Belt 0x21-0x23, Motor 0x81-0x88,
# Gruppen 0xFC/0xFD/0xFE/0xFF, CFS-Sonderadressen 0xEA/0xEB.
_UPD_ADDRS = (0x01, 0x02, 0x03, 0x04, 0x11, 0x21, 0x22, 0x23, 0x81, 0x84,
              0x85, 0x88, 0xEA, 0xEB, 0xFC, 0xFD, 0xFE, 0xFF)
# Adressen mit fun-Whitelist bzw. Komplettsperre (Stufe 2, 18.10.2026)
_STUFE2_ADDRS = frozenset((0x11, 0x21, 0x22, 0x23, 0x24, 0x85, 0x86, 0x87,
                           0x88))


def _update_guard_cases():
    """(addr, fun, data, erlaubt) – Matrix je Adresse/Sub."""
    cases = []
    for a in _UPD_ADDRS:
        # Belt/RFID/Bootloader-Adressen: F0 seit Stufe 2 (Whitelist) gesperrt
        f0_frei = a not in _STUFE2_ADDRS
        for sub in range(0x100):
            cases.append((a, 0xF0, [sub], f0_frei and sub in (0x00, 0x02, 0x03)))
        cases += [(a, 0xF0, [], False),                       # leer
                  (a, 0xF0, [0x00, 0x00], False),             # Zusatzbyte
                  (a, 0xF0, [0x02, 0x01], False),
                  (a, 0xF0, [0x03, 0xE0], False),
                  (a, 0xF0, [0x00, 0x30, 0x00, 0x00], False),  # Längen-Frame
                  (a, 0xF0, [0x02] * 0x80, False),            # Datenblock
                  (a, 0xF0, [0x00] * 0x81, False),
                  (a, 0xF2, [], False), (a, 0xF2, [0x01, 0x10], False),
                  (a, 0xF2, [0x00] + [0x11] * 8 + [0x42], False)]
    for fun in range(0x100):                                  # Ziel 0xF0
        cases += [(0xF0, fun, [], False), (0xF0, fun, [0x01], False)]
    for a in (0xEA, 0xEB):
        for fun in (0x50, 0x51, 0x56):
            cases += [(a, fun, [], False), (a, fun, [0x00], False),
                      (a, fun, [0x01], False)]
        cases += [(a, 0x0A, [], True), (a, 0x0C, [], True),
                  (a, 0x14, [], True), (a, 0x55, [0x01], True)]
    # dieselben fun-Codes außerhalb 0xEA/0xEB: nicht Sache dieser Sperre
    cases += [(0x01, 0x56, [], True), (0x81, 0x50, [0x00], True),
              (0x02, 0x51, [0x01], True), (0xEF, 0x0E, [0x01], True),
              (0xF1, 0xA2, [], True)]
    return cases


def _legit_frames():
    """Bestehende legitime Sendungen (Boot/Connect, Auto-Addr, Version,
    Box-Preload/-Status, Belt, Motor-Init, Bootloader->App) – müssen frei
    bleiben."""
    import k2_belt as KBELT
    f = [(0xFF, 0x0B, [0x01]),                        # Loader->App (auto_addr)
         (0xFE, 0xA1, []), (0xFD, 0xA1, []), (0xFC, 0xA1, []),
         (0xFE, 0xA0, list(range(13))), (0x01, 0xA2, []), (0x01, 0xA3, []),
         (0xFF, 0x0E, [0x01]), (0x81, 0x0E, [0x02]),  # Motor-Adresse
         (0x81, 0x0C, [0x0B]), (0x81, 0x06, [0x02, 0x46]),
         (0x81, 0x07, [0x02]), (0x81, 0x07, [0x06, 0x14]), (0x81, 0x12, [0x02]),
         (0x81, 0xF0, [0x00]), (0x81, 0xF0, [0x03]),  # motor.query_*
         (0x01, 0x14, []), (0x01, 0x15, []), (0x01, 0x0A, []), (0x01, 0x0C, []),
         (0x01, 0x0D, [0x02, 0x0F]), (0x01, 0x04, [0x00, 0x01]),
         (0x01, 0x02, [0x01]), (0x01, 0x10, [0x01, 0x00, 0x00]),
         (0x11, 0x02, []), (0x11, 0x14, []),          # Rack/RFID (filament_rack)
         (0x01, 0x14, []), (0x02, 0x02, [0x00]),      # Box 0x02/0x14 unicast
         # Belt (k2_belt, Payload-Format der Belt-FW)
         (KBELT.ADDR_MDLX, KBELT.FUN_READ_VERSION, []),
         (KBELT.ADDR_MDLY, KBELT.FUN_READ_FLASH, [0x03]),
         (KBELT.ADDR_MDLX, KBELT.FUN_READ_ADC, []),
         (KBELT.ADDR_MDLX, KBELT.FUN_WRITE_FLASH,
          [0x03] + KBELT.split_to_bytes(147) + KBELT.split_to_bytes(0xFFF7B795)
          + KBELT.split_to_bytes(0xFFF3524C)),
         (KBELT.ADDR_MDLY, KBELT.FUN_MOVE_SLIDER, [1] + KBELT.split_to_bytes(12)),
         (KBELT.ADDR_MDLX, KBELT.FUN_MOVE_SLIDER, [0] + KBELT.split_to_bytes(1)),
         (KBELT.ADDR_MDLX, KBELT.FUN_MOVE_SLIDER, [1] + KBELT.split_to_bytes(50)),
         (0x81, 0x05, [0x02]), (0x82, 0x05, [0x01])]  # Belt-Rüttelzyklus Motor-0x05
    return f


def test_update_sendeschutz():
    cases = _update_guard_cases()
    check("F0-Sperre: Konstanten serial485 = lokal",
          S485.TX_F0_ALLOWED_SUBS == T.TX_F0_ALLOWED_SUBS
          == frozenset((0x00, 0x02, 0x03))
          and S485.TX_FUNC_EEPROM == T.TX_FUNC_EEPROM == 0xF2
          and S485.TX_ADDR_EEPROM_SERVICE == T.TX_ADDR_EEPROM_SERVICE == 0xF0
          and S485.TX_CFS_SPECIAL_ADDRS == T.TX_CFS_SPECIAL_ADDRS
          == frozenset((0xEA, 0xEB))
          and S485.TX_CFS_SPECIAL_BLOCKED_FUNCS
          == T.TX_CFS_SPECIAL_BLOCKED_FUNCS == frozenset((0x50, 0x51, 0x56))
          and S485.TX_GUARD_REV >= T.TX_GUARD_REV)
    check("F0-Sperre: k2_transport nutzt aktuelles serial485",
          T.check_tx_frame is S485.check_tx_frame
          and T._nachbau_guard_ok(S485))
    for name, fn in (("serial485", S485.check_tx_frame),
                     ("lokal", T._check_tx_frame_local)):
        wrong = []
        for addr, fun, data, ok in cases:
            try:
                fn(addr, fun, data)
                got = True
            except ValueError:
                got = False
            if got != ok:
                wrong.append("0x%02X/0x%02X %s" % (addr, fun, data[:4]))
        check("F0/F2/0xF0/0xEB-Matrix %s (%d Fälle, %d gesperrt)"
              % (name, len(cases), sum(1 for c in cases if not c[3])),
              not wrong, wrong[:5])
    # Gleichwertigkeit flächendeckend: alle addr x fun mit typischen Payloads
    payloads = ([], [0x00], [0x01], [0x02], [0x03], [0x06], [0x02, 0x00],
                [0x00] * 0x80, [0x00] * 101)
    diff = []
    for addr in range(0x100):
        for fun in range(0x100):
            for d in payloads:
                r = []
                for fn in (S485.check_tx_frame, T._check_tx_frame_local):
                    try:
                        fn(addr, fun, d)
                        r.append(True)
                    except ValueError:
                        r.append(False)
                if r[0] != r[1]:
                    diff.append((addr, fun, len(d)))
    check("Fallback gleichwertig: 256 addr x 256 fun x %d Payloads"
          % len(payloads), not diff, diff[:5])
    legit = _legit_frames()
    bad = []
    for addr, fun, data in legit:
        for fn in (S485.check_tx_frame, T._check_tx_frame_local):
            try:
                fn(addr, fun, data)
            except ValueError as exc:
                bad.append((addr, fun, str(exc)[:60]))
    check("Legitime Pfade frei (%d Frames: Loader->App, Auto-Addr, Version, "
          "Box, Belt, Motor-Init, F0 [00]/[03])" % len(legit), not bad, bad[:3])

    # veraltetes serial485 (ohne F0-Sperre bzw. kleiner TX_GUARD_REV) -> lokal
    import types
    base = {k: getattr(S485, k) for k in (
        "check_tx_frame", "build_tx_frame", "TxGuardError",
        "TX_MOTOR_GUARD_ADDRS", "TX_F0_ALLOWED_SUBS", "TX_GUARD_REV",
        "TX_BELT_ADDRS", "TX_BOOTLOADER_ADDRS", "TX_RFID_ALLOWED_FUNS",
        "tx_kalibrier_freigabe", "tx_kalibrier_freigabe_loeschen")}
    stale = []
    for drop, rev in (("TX_F0_ALLOWED_SUBS", None), ("TX_GUARD_REV", None),
                      (None, 20261017), (None, 20261018), (None, True),
                      (None, "20261019"), (None, 20261026),
                      ("tx_kalibrier_freigabe", None),
                      ("TX_MOTOR_GUARD_ADDRS", None)):
        attrs = dict(base)
        if drop:
            del attrs[drop]
        if rev is not None:
            attrs["TX_GUARD_REV"] = rev
        stale.append(types.SimpleNamespace(**attrs))
    sel = [T._select_tx_guard(m) for m in stale + [None]]
    check("Veraltetes serial485 (17.10.-Stand/ohne Merkmal) -> lokaler Fallback",
          all(s == (T._check_tx_frame_local, T._build_tx_frame_local,
                    T._LocalTxGuardError) for s in sel)
          and T._select_tx_guard(types.SimpleNamespace(**base))[0]
          is S485.check_tx_frame)
    # 17.10.-Stand simuliert: alte Prüfung ließe F0 [01] durch, Fallback nicht
    def _old_check(addr, fun, data=()):
        return list(data)
    old = dict(base, check_tx_frame=_old_check)
    del old["TX_F0_ALLOWED_SUBS"]
    del old["TX_GUARD_REV"]
    chk = T._select_tx_guard(types.SimpleNamespace(**old))[0]
    try:
        chk(0x01, 0xF0, [0x01])
        blocked_old = False
    except ValueError:
        blocked_old = True
    check("Fallback sperrt F0 [01], obwohl altes serial485 es durchließe",
          blocked_old and chk is T._check_tx_frame_local)

    # Sendewege: [k2_485] (echte Serial485 + Fake-Port), Rs485Transport,
    # TransparentTransport – gesperrt -> nichts auf dem Draht
    blocked = [c[:3] for c in cases if not c[3]][::7]
    fac = Factory(FakeSerial([None] * 8))
    pr, bus = make(factory=fac)
    pr.send("klippy:connect")
    ser = fac.instances[0].ser
    n = 0
    for addr, fun, data in blocked:
        try:
            bus.transact_full(addr, fun, data, timeout=0.01, retries=0)
        except S485.TxGuardError:
            n += 1
        except Exception:
            pass
    check("k2_485: %d gesperrte Update-/Service-Frames -> TxGuardError, Draht leer"
          % len(blocked), n == len(blocked) and ser.written == [], n)
    for d in ([0x00], [0x02], [0x03]):
        try:
            bus.transact_full(0x01, 0xF0, d, timeout=0.01, retries=0)
        except TimeoutError:
            pass
    check("k2_485: F0 [00]/[02]/[03] an 0x01 byte-genau gesendet",
          ser.written == [bytes(S485.build_frame(0x01, 0xF0, d))
                          for d in ([0x00], [0x02], [0x03])],
          [w.hex(" ") for w in ser.written])
    pr.send("klippy:disconnect")

    class _RecBus:
        def __init__(self):
            self.calls = []

        def transact_full(self, addr, fun, data, timeout=None, retries=0):
            self.calls.append((addr, fun, list(data)))
            raise TimeoutError("still")
    rb = _RecBus()
    rs = T.Rs485Transport(rb)
    mcu = _FakeTransparent()
    tr = T.TransparentTransport(mcu)
    nr = nt = 0
    for addr, fun, data in blocked:
        for t in (rs, tr):
            try:
                t.transact(addr, fun, data, timeout=0.01, retries=0)
            except T.TxGuardError:
                if t is rs:
                    nr += 1
                else:
                    nt += 1
            except (TimeoutError, ValueError):
                pass
    check("Rs485Transport/TransparentTransport: Update-/Service-Frames "
          "gesperrt, nichts gesendet",
          nr == nt == len(blocked) and rb.calls == [] and mcu.writes == [],
          (nr, nt, len(blocked)))


# ---------------------------------------------------------------------
# Stufe 2 (18.10.2026): Belt-Whitelist, Bootloader-Adressen, RFID 0x11
# (Belt-/RFID-Firmware, Live-Gegenprobe)
# ---------------------------------------------------------------------

def _mv(d, dist):
    return [d] + list(int(dist).to_bytes(4, "big"))


def _stufe2_cases():
    """(addr, fun, data, erlaubt)."""
    cases = []
    for a in (0x23, 0x24, 0x85, 0x86, 0x87, 0x88):
        for fun in range(0x100):
            cases += [(a, fun, [], False), (a, fun, [0x00], False),
                      (a, fun, [0x01], False), (a, fun, [0x02], False)]
    for a in (0x21, 0x22):
        for fun in range(0x100):
            if fun not in (0, 2, 4, 6, 8):
                cases += [(a, fun, [], False), (a, fun, [0x03], False)]
        cases += [(a, 0x00, [], True), (a, 0x06, [], True),
                  (a, 0x00, [0x00], False), (a, 0x06, [0x03], False),
                  (a, 0x02, [0x03], True), (a, 0x02, [], False),
                  (a, 0x02, [0x00], False), (a, 0x02, [0x02], False),
                  (a, 0x02, [0x3F], False), (a, 0x02, [0x03, 0x00], False),
                  (a, 0x04, [0x03] + [0] * 12, True),
                  (a, 0x04, [0x03] + [0xFF] * 12, True),
                  (a, 0x04, [], False), (a, 0x04, [0x00], False),
                  (a, 0x04, [0x03], False), (a, 0x04, [0x03] + [0] * 11, False),
                  (a, 0x04, [0x03] + [0] * 13, False),
                  (a, 0x04, [0x02] + [0] * 8, False),
                  (a, 0x04, [0x04] + [0] * 16, False),
                  (a, 0x04, [0x10] + [0] * 25, False),
                  (a, 0x08, [], False), (a, 0x08, [0x01], False),
                  (a, 0x08, [0x01, 0, 0, 0, 5, 0], False),
                  (a, 0x08, [0x01, 0, 5], False),
                  (a, 0x00, [0] * 26, False), (a, 0x00, [0] * 27, False)]
        for dist in (0, 1, 2, 12, 49, 50, 51, 100, 0xFFFE, 0xFFFF,
                     0x00010001, 0x01000000, 0xFFFFFFFF):
            for d in (0, 1, 2, 0x80, 0xFF):
                cases.append((a, 0x08, _mv(d, dist),
                              d in (0, 1) and 1 <= dist <= 50))
    cases += [(0x11, 0x02, [], True), (0x11, 0x14, [], True),
              (0x11, 0x02, [0x00], False), (0x11, 0x14, [0x01], False)]
    for fun in range(0x100):
        if fun not in (0x02, 0x14):
            cases += [(0x11, fun, [], False), (0x11, fun, [0x00], False),
                      (0x11, fun, [0x01], False)]
    for a in (0xFF, 0xFD):
        cases += [(a, f, d, False) for f in (0x02, 0x14)
                  for d in ([], [0x00], [0x01])]
    # Nachbarn/andere Geräte unberührt
    cases += [(0x20, 0x0B, [0x00], True), (0x25, 0xA2, [], True),
              (0x84, 0x08, [0x01], True), (0x89, 0x0E, [0x01], True),
              (0x10, 0x0B, [0x00], True), (0x12, 0x03, [], True),
              (0x01, 0x02, [0x01], True), (0x04, 0x14, [], True),
              (0xFE, 0x14, [], True), (0xFC, 0x02, [], True),
              (0xEA, 0x14, [], True), (0x81, 0x14, [], True)]
    return cases


class _BeltRecBus:
    """Fake-Bus für k2_belt: wendet den echten Sendeschutz an (wie
    serial485.transact_full), zeichnet zugelassene Frames auf und antwortet
    wie die Belt-FW."""

    def __init__(self, guard):
        self.guard = guard
        self.sent = []
        self.blocked = []
        self.flash = [147, 0xFFF7B795, 0xFFF3524C]

    def transact_full(self, addr, fun, data, timeout=None, retries=0):
        data = list(data)
        try:
            self.guard(addr, fun, data)
        except ValueError as exc:
            self.blocked.append((addr, fun, data, str(exc)))
            raise
        self.sent.append((addr, fun, data))
        if fun == 0x00:
            rd = [1, 2, 3, 4]
        elif fun == 0x02:
            rd = [3] + [b for v in self.flash for b in v.to_bytes(4, "big")]
        elif fun == 0x04:
            self.flash = [int.from_bytes(bytes(data[1 + 4 * i:5 + 4 * i]), "big")
                          for i in range(3)]
            rd = list(data)
        elif fun == 0x06:
            rd = list((0xFFF60000).to_bytes(4, "big"))
        elif fun == 0x08:
            rd = list(data)
        else:
            raise TimeoutError("still")  # Motor-0x05: live nie beantwortet
        raw = bytes(S485.build_frame(addr, fun, rd))
        return addr, 0, fun, bytes(rd), raw


def test_stufe2_sendeschutz():
    check("Stufe 2: Konstanten serial485 = lokal",
          S485.TX_BELT_ADDRS == T.TX_BELT_ADDRS == frozenset((0x21, 0x22))
          and S485.TX_BOOTLOADER_ADDRS == T.TX_BOOTLOADER_ADDRS
          == frozenset((0x23, 0x24, 0x85, 0x86, 0x87, 0x88))
          and S485.TX_RFID_ADDR == T.TX_RFID_ADDR == 0x11
          and S485.TX_RFID_ALLOWED_FUNS == T.TX_RFID_ALLOWED_FUNS
          == frozenset((0x02, 0x14))
          and S485.TX_BROADCAST_BLOCKED_FUNS == T.TX_BROADCAST_BLOCKED_FUNS
          and S485.TX_BELT_MOVE_MAX == T.TX_BELT_MOVE_MAX == 50
          and S485.TX_BELT_FRAME_MAX == T.TX_BELT_FRAME_MAX == 32
          and S485.TX_BELT_FLASH_NUM == T.TX_BELT_FLASH_NUM == 0x03
          and S485.TX_BELT_FLASH_BYTES == T.TX_BELT_FLASH_BYTES == 12
          and S485.TX_GUARD_REV == T.TX_GUARD_REV == 20261027)
    check("Stufe 2: k2_transport nutzt serial485 (Regelstand aktuell)",
          T.check_tx_frame is S485.check_tx_frame and T._nachbau_guard_ok(S485))
    cases = _stufe2_cases()
    for name, fn in (("serial485", S485.check_tx_frame),
                     ("lokal", T._check_tx_frame_local)):
        wrong = []
        for addr, fun, data, ok in cases:
            try:
                fn(addr, fun, data)
                got = True
            except ValueError:
                got = False
            if got != ok:
                wrong.append("0x%02X/0x%02X %s" % (addr, fun, data[:6]))
        check("Stufe-2-Matrix %s (%d Fälle, %d gesperrt)"
              % (name, len(cases), sum(1 for c in cases if not c[3])),
              not wrong, wrong[:5])
    # Gleichwertigkeit: alle addr x fun mit Belt-/RFID-typischen Payloads
    payloads = ([], [0x00], [0x01], [0x03], [0x03] + [0] * 12,
                [0x03] + [0] * 11, [0x02] + [0] * 12, _mv(1, 1), _mv(0, 50),
                _mv(1, 51), _mv(1, 0), _mv(2, 5), [0] * 26, [0] * 27)
    diff = []
    for addr in range(0x100):
        for fun in range(0x100):
            for d in payloads:
                r = []
                for fn in (S485.check_tx_frame, T._check_tx_frame_local):
                    try:
                        fn(addr, fun, d)
                        r.append(True)
                    except ValueError:
                        r.append(False)
                if r[0] != r[1]:
                    diff.append((addr, fun, d[:3]))
    check("Stufe 2: Fallback gleichwertig (256 x 256 x %d Payloads)"
          % len(payloads), not diff, diff[:5])
    # serial485 mit Stand 20261018 (ohne Stufe 2) -> lokaler Fallback
    import types
    base = {k: getattr(S485, k) for k in (
        "check_tx_frame", "build_tx_frame", "TxGuardError",
        "TX_MOTOR_GUARD_ADDRS", "TX_F0_ALLOWED_SUBS", "TX_GUARD_REV",
        "TX_BELT_ADDRS", "TX_BOOTLOADER_ADDRS", "TX_RFID_ALLOWED_FUNS")}
    olds = []
    for drop, rev in ((None, 20261018), ("TX_BELT_ADDRS", None),
                      ("TX_BOOTLOADER_ADDRS", None),
                      ("TX_RFID_ALLOWED_FUNS", None)):
        attrs = dict(base, check_tx_frame=lambda a, f, d=(): list(d))
        if drop:
            del attrs[drop]
        if rev is not None:
            attrs["TX_GUARD_REV"] = rev
        olds.append(T._select_tx_guard(types.SimpleNamespace(**attrs))[0])
    blk = 0
    for chk in olds:
        for a, f, d in ((0x11, 0x0B, [0x00]), (0x23, 0xF0, [0x02]),
                        (0x21, 0x08, _mv(1, 0xFFFE)), (0xFF, 0x02, [])):
            try:
                chk(a, f, d)
            except ValueError:
                blk += 1
    check("Stufe 2: serial485 vom 18.10. Stufe 1 (Rev 20261018/ohne Merkmal) "
          "-> Fallback sperrt 0x11 0x0B, 0x23, Belt-Weg 0xFFFE, 0xFF 0x02",
          blk == 4 * len(olds)
          and all(c is T._check_tx_frame_local for c in olds))
    # Legitime Pfade aus k2_belt (Payload-Builder des Moduls) über den Guard
    import k2_belt as KBELT
    for name, fn in (("serial485", S485.check_tx_frame),
                     ("lokal", T._check_tx_frame_local)):
        rb = _BeltRecBus(fn)
        msgs = []
        ctl = KBELT.K2BeltController(
            "mdlx", KBELT.parse_options({"allow_write": True,
                                         "allow_move": True}, "mdlx"),
            bus=rb, pause=lambda s: None, respond=msgs.append,
            run_gcode=lambda g: None)
        errs = []
        for step, call in (("info", ctl.info),
                           ("read_version", ctl.read_version),
                           ("read_flash", ctl.read_flash),
                           ("read_adc", ctl.read_adc),
                           ("write_flash", ctl.write_flash),
                           ("move +10", lambda: ctl.move(ctl.current_place + 10)),
                           ("move -10", lambda: ctl.move(ctl.current_place - 10)),
                           ("slider 1/12", lambda: ctl.slider_move(1, 12)),
                           ("slider 0/1", lambda: ctl.slider_move(0, 1)),
                           ("slider 1/50", lambda: ctl.slider_move(1, 50)),
                           ("cali 0", lambda: ctl.calibrate(0)),
                           ("motor_control", lambda: ctl.motor_control(1, 2))):
            try:
                call()
            except Exception as exc:  # noqa: BLE001
                errs.append((step, str(exc)[:120]))
        funs = sorted(set(f for _a, f, _d in rb.sent))
        check("Stufe 2: k2_belt-Pfade (%s): INFO/Lesen/Flash/Move/Cali/Motor-"
              "0x05 ohne Sendeschutz-Treffer" % name,
              not rb.blocked and not errs and {0, 2, 4, 6, 8} <= set(funs)
              and all(a in (0x21, 0x81) for a, _f, _d in rb.sent),
              (rb.blocked[:2], errs[:3], funs))
        # über 50 Einheiten: nie auf dem Draht (k2_belt oder Guard lehnt ab)
        n0 = len(rb.sent)
        for dist in (51, 0xFFFE):
            try:
                ctl.slider_move(1, dist)
            except Exception:  # noqa: BLE001 – BeltError/TxGuardError erwartet
                pass
        check("Stufe 2: k2_belt Weg > 50 -> nichts gesendet (%s)" % name,
              len(rb.sent) == n0, rb.sent[n0:])
    # filament_rack-Request (Stock-Bytes) über den Guard
    FR = T.import_nachbau("filament_rack")
    req = FR.build_rfid_request_frame()   # nutzt build_tx_frame (Guard)
    check("Stufe 2: filament_rack-Request 0x11 0x02 (status 0xFF) erlaubt",
          bytes(req) == bytes(
              S485.build_frame(0x11, 0x02, [], status=0xFF)))
    # Sendewege: k2_485 (echte Serial485 + Fake-Port), Rs485/Transparent
    blocked = [c[:3] for c in cases if not c[3]][::11]
    fac = Factory(FakeSerial([None] * 8))
    pr, bus = make(factory=fac)
    pr.send("klippy:connect")
    ser = fac.instances[0].ser
    n = 0
    for addr, fun, data in blocked:
        try:
            bus.transact_full(addr, fun, data, timeout=0.01, retries=0)
        except S485.TxGuardError:
            n += 1
        except Exception:
            pass
    check("Stufe 2: k2_485 %d gesperrte Frames -> TxGuardError, Draht leer"
          % len(blocked), n == len(blocked) and ser.written == [], n)
    for addr, fun, data in ((0x21, 0x08, _mv(1, 12)), (0x11, 0x14, [])):
        try:
            bus.transact_full(addr, fun, data, timeout=0.01, retries=0)
        except TimeoutError:
            pass
    check("Stufe 2: k2_485 Belt fun 8 [01 00 00 00 0c] / RFID 0x14 byte-genau",
          ser.written == [bytes(S485.build_frame(0x21, 0x08, _mv(1, 12))),
                          bytes(S485.build_frame(0x11, 0x14, []))],
          [w.hex(" ") for w in ser.written])
    pr.send("klippy:disconnect")
    rb2 = _BeltRecBus(lambda a, f, d: None)
    rs = T.Rs485Transport(rb2)
    mcu = _FakeTransparent()
    tr = T.TransparentTransport(mcu)
    nr = nt = 0
    for addr, fun, data in blocked:
        for t in (rs, tr):
            try:
                t.transact(addr, fun, data, timeout=0.01, retries=0)
            except T.TxGuardError:
                if t is rs:
                    nr += 1
                else:
                    nt += 1
            except (TimeoutError, ValueError):
                pass
    check("Stufe 2: Rs485Transport/TransparentTransport gesperrt, nichts "
          "gesendet", nr == nt == len(blocked) and rb2.sent == []
          and mcu.writes == [], (nr, nt, len(blocked)))


# ---------------------------------------------------------------------
# L2 (18.10.2026): transparent_send genau einmal, Wartezeit 60 s wie Stock
# ---------------------------------------------------------------------

class _L2Completion:
    def __init__(self, reactor):
        self.reactor = reactor
        self.done = False
        self.result = None

    def wait(self, waketime):
        r = self.reactor
        while not self.done:
            due = r.next_due()
            if due is None or due > waketime:
                r.now = max(r.now, waketime)
                break
            r.now = max(r.now, due)
            r.run_next()
        return self.result if self.done else None


class _L2Reactor:
    """Simulierte Zeit: completion.wait springt zum nächsten Ereignis."""

    def __init__(self):
        self.now = 1000.0
        self.events = []

    def monotonic(self):
        return self.now

    def completion(self):
        return _L2Completion(self)

    def async_complete(self, completion, params):
        completion.done = True
        completion.result = params

    def schedule(self, when, fn):
        self.events.append((when, fn))
        self.events.sort(key=lambda e: e[0])

    def next_due(self):
        return self.events[0][0] if self.events else None

    def run_next(self):
        _when, fn = self.events.pop(0)
        fn()


class _L2Serial:
    """Ausschnitt Upstream-serialhdl: register_response, raw_send_wait_ack,
    raw_send. answer_after = Sekunden bis transparent_response (None = nie);
    stale=True liefert vorher eine Antwort mit #sent_time VOR der Anfrage."""

    def __init__(self, reactor, answer_after=None, read=b"", stale=False):
        self.reactor = reactor
        self.answer_after = answer_after
        self.read = bytes(read)
        self.stale = stale
        self.handlers = {}
        self.sends = []      # raw_send_wait_ack
        self.resends = []    # raw_send (Neusendung)

    def get_reactor(self):
        return self.reactor

    def register_response(self, cb, name, oid=None):
        if cb is None:
            self.handlers.pop((name, oid), None)
        else:
            self.handlers[(name, oid)] = cb

    def _deliver(self, sent_time, oid, read):
        cb = self.handlers.get(("transparent_response", oid))
        if cb is not None:
            cb({"#sent_time": sent_time, "oid": oid, "read": read})

    def _schedule_answer(self, cmd):
        oid = cmd[0]
        t = self.reactor.now
        if self.stale:
            self.reactor.schedule(t + 0.001, lambda: self._deliver(t - 5.0, oid, b"ALT"))
        if self.answer_after is not None:
            self.reactor.schedule(t + self.answer_after,
                                  lambda: self._deliver(t, oid, self.read))

    def raw_send_wait_ack(self, cmd, minclock, reqclock, cmd_queue):
        self.sends.append(cmd)
        self._schedule_answer(cmd)

    def raw_send(self, cmd, minclock, reqclock, cmd_queue):
        self.resends.append(cmd)
        self._schedule_answer(cmd)


class _L2CommandError(Exception):
    """Wie printer.command_error (Upstream _do_send wandelt serialhdl.error)."""


class _L2UpstreamRetryAsync:
    """Logik 1:1 aus Upstream 9cb9975 mcu.RetryAsyncCommand (mcu.py:40-72) –
    nur für die Gegenprobe (sendet in der Warteschleife neu)."""
    TIMEOUT_TIME = 5.0
    RETRY_TIME = 0.500

    def __init__(self, serial, name, oid=None):
        self.serial = serial
        self.name = name
        self.oid = oid
        self.reactor = serial.get_reactor()
        self.completion = self.reactor.completion()
        self.min_query_time = self.reactor.monotonic()
        self.need_response = True
        self.serial.register_response(self.handle_callback, name, oid)

    def handle_callback(self, params):
        if self.need_response and params['#sent_time'] >= self.min_query_time:
            self.need_response = False
            self.reactor.async_complete(self.completion, params)

    def get_response(self, cmds, cmd_queue, minclock=0, reqclock=0, retry=True):
        cmd, = cmds
        self.serial.raw_send_wait_ack(cmd, minclock, reqclock, cmd_queue)
        self.min_query_time = 0.
        timeout_time = query_time = self.reactor.monotonic()
        if retry:
            timeout_time += self.TIMEOUT_TIME
        while 1:
            params = self.completion.wait(query_time + self.RETRY_TIME)
            if params is not None:
                self.serial.register_response(None, self.name, self.oid)
                return params
            query_time = self.reactor.monotonic()
            if query_time > timeout_time:
                self.serial.register_response(None, self.name, self.oid)
                raise _L2CommandError("Timeout on wait for '%s' response"
                                      % (self.name,))
            self.serial.raw_send(cmd, minclock, minclock, cmd_queue)


class _L2QueryWrapper:
    """Nachbau Upstream mcu.CommandQueryWrapper (Auswahl + _do_send)."""

    def __init__(self, serial, respformat, oid, is_async, fileoutput):
        self._serial = serial
        self._response = respformat.split()[0]
        self._oid = oid
        self._xmit_helper = "SerialRetryCommand"
        if fileoutput:
            self._xmit_helper = "DummyResponse"
        elif is_async:
            self._xmit_helper = _L2UpstreamRetryAsync

    def send(self, data=(), minclock=0, reqclock=0, retry=True):
        xh = self._xmit_helper(self._serial, self._response, self._oid)
        try:
            return xh.get_response([tuple(data)], None, minclock, reqclock, retry)
        except Exception as exc:   # serialhdl.error -> command_error
            raise _L2CommandError(str(exc))


class _L2Mcu:
    def __init__(self, serial, fileoutput=False):
        self.serial = serial
        self.fileoutput = fileoutput
        self.lookups = []

    def is_fileoutput(self):
        return self.fileoutput

    def lookup_query_command(self, msgformat, respformat, oid=None, cq=None,
                             is_async=False):
        self.lookups.append((msgformat, respformat, oid, is_async))
        return _L2QueryWrapper(self.serial, respformat, oid, is_async,
                               self.fileoutput)


def _l2_adapter(answer_after, read=b"", stale=False, fileoutput=False):
    reactor = _L2Reactor()
    ser = _L2Serial(reactor, answer_after=answer_after, read=read, stale=stale)
    mcu = _L2Mcu(ser, fileoutput=fileoutput)
    ad = T.KlippyTransparentAdapter(mcu, 7)
    ad.setup()
    return reactor, ser, mcu, ad


def test_motor_uuid_encoder_sperre():
    """26.10.2026 (Motor-FW-Analyse 25.10.2026): Motor 0x02 gesperrt,
    0x12 nur [02] (auch an 0xFF); serial485 und Fallback gleich; altes
    serial485 (Rev 20261019) -> Fallback."""
    import types
    check("0x02/0x12: Konstanten serial485 = lokal",
          S485.TX_FUNC_ENCODER_REG_CHECK == T.TX_FUNC_ENCODER_REG_CHECK == 0x02
          and S485.TX_FUNC_DEV_UUID == T.TX_FUNC_DEV_UUID == 0x12
          and S485.TX_DEV_UUID_READ == T.TX_DEV_UUID_READ == (0x02,))
    uuid_schreiben = [0x01] + [0xAB] * 8
    gesperrt = [(a, f, d) for a in list(range(0x80, 0x89)) + [0xFD]
                for f, d in ((0x02, []), (0x02, [0x00]), (0x12, []),
                             (0x12, [0x01]), (0x12, uuid_schreiben),
                             (0x12, [0x02, 0x00]))]
    gesperrt += [(0xFF, 0x12, uuid_schreiben), (0xFF, 0x12, [])]
    frei = [(0x81, 0x12, [0x02]), (0x84, 0x12, [0x02]), (0xFF, 0x12, [0x02]),
            (0x01, 0x02, [0x01]), (0x02, 0x02, [0x00]), (0x01, 0x12, [0x01]),
            (0x21, 0x02, [0x03])]
    for name, fn in (("serial485", S485.check_tx_frame),
                     ("lokal", T._check_tx_frame_local)):
        falsch = []
        for a, f, d in gesperrt:
            try:
                fn(a, f, d)
                falsch.append(("frei", a, f, d[:2]))
            except ValueError:
                pass
        for a, f, d in frei:
            try:
                fn(a, f, d)
            except ValueError as exc:
                falsch.append(("gesperrt", a, f, d[:2], str(exc)[:50]))
        check("0x02/0x12-Matrix %s (%d gesperrt, %d frei)"
              % (name, len(gesperrt), len(frei)), not falsch, falsch[:4])
    diff = []
    for addr in range(0x100):
        for fun in (0x02, 0x12):
            for d in ([], [0x00], [0x01], [0x02], [0x02, 0x00], uuid_schreiben):
                r = []
                for fn in (S485.check_tx_frame, T._check_tx_frame_local):
                    try:
                        fn(addr, fun, d)
                        r.append(True)
                    except ValueError:
                        r.append(False)
                if r[0] != r[1]:
                    diff.append((addr, fun, d[:2]))
    check("0x02/0x12: Fallback gleichwertig (256 Adressen)", not diff, diff[:5])
    attrs = {k: getattr(S485, k) for k in (
        "check_tx_frame", "build_tx_frame", "TxGuardError",
        "TX_MOTOR_GUARD_ADDRS", "TX_F0_ALLOWED_SUBS", "TX_BELT_ADDRS",
        "TX_BOOTLOADER_ADDRS", "TX_RFID_ALLOWED_FUNS")}
    attrs.update(check_tx_frame=lambda a, f, d=(): list(d), TX_GUARD_REV=20261019)
    chk = T._select_tx_guard(types.SimpleNamespace(**attrs))[0]
    blk = 0
    for a, f, d in ((0x81, 0x12, uuid_schreiben), (0x81, 0x02, [])):
        try:
            chk(a, f, d)
        except ValueError:
            blk += 1
    check("0x02/0x12: serial485 vom 19.10. (Rev 20261019) -> Fallback sperrt",
          chk is T._check_tx_frame_local and blk == 2)
    check("0x02/0x12: Motor-Init-Leseframe 0x12 [02] baut byte-genau",
          bytes(S485.build_tx_frame(0x81, 0x12, [0x02]))
          == bytes(T.build_frame(0x81, 0x12, [0x02])))


def test_kalibrier_einmalfreigabe():
    """26.10.2026 abends: tx_kalibrier_freigabe setzt die Freigabe lokal UND in
    serial485; nur exakte Frames an 0x81-0x84; Rs485Transport sendet den
    Frame genau einmal; nach dem Löschen wieder gesperrt; Fallback gleich."""
    import time as _t
    frames = ((0x81, 0x04, [0x00]), (0x84, 0x03, [0x00]), (0x82, 0x07, [0x03, 0x00]))
    ok = True
    for a, f, d in frames:
        for fn in (S485.check_tx_frame, T._check_tx_frame_local):
            try:
                fn(a, f, d)
                ok = False
            except ValueError:
                pass
        T.tx_kalibrier_freigabe(a, f, d, 2.0)
        for fn in (S485.check_tx_frame, T._check_tx_frame_local):
            ok &= fn(a, f, d) == list(d)
        T.tx_kalibrier_freigabe_loeschen()
        for fn in (S485.check_tx_frame, T._check_tx_frame_local):
            try:
                fn(a, f, d)
                ok = False
            except ValueError:
                pass
    check("Einmalfreigabe in beiden Schichten, nach Löschen gesperrt", ok)
    # nur der exakte Frame; Rest bleibt gesperrt
    T.tx_kalibrier_freigabe(0x81, 0x04, [0x00], 2.0)
    falsch = []
    for a, f, d in ((0x82, 0x04, [0x00]), (0x81, 0x03, [0x00]), (0x81, 0x04, [0x01]),
                    (0xFD, 0x04, [0x00]), (0x81, 0x07, [0x03, 0x00]),
                    (0x81, 0x07, [0x01]), (0x81, 0x02, []), (0x81, 0x12, [0x01] + [0] * 8)):
        for fn in (S485.check_tx_frame, T._check_tx_frame_local):
            try:
                fn(a, f, d)
                falsch.append((a, f, d))
            except ValueError:
                pass
    T.tx_kalibrier_freigabe_loeschen()
    check("Einmalfreigabe: alles andere bleibt gesperrt", not falsch, falsch[:3])
    unzul = []
    for a, f, d, g in ((0xFD, 0x04, [0], 1), (0x85, 0x04, [0], 1), (0x81, 0x07, [5, 0], 1),
                       (0x81, 0x04, [0], 30), (0x81, 0x04, [0], -1)):
        try:
            T.tx_kalibrier_freigabe(a, f, d, g)
            unzul.append((a, f, d, g))
        except ValueError:
            pass
    T.tx_kalibrier_freigabe_loeschen()
    check("Einmalfreigabe: unzulässige Anforderungen abgelehnt", not unzul, unzul)
    T.tx_kalibrier_freigabe(0x81, 0x04, [0x00], 0.001)
    _t.sleep(0.01)
    abgelaufen = 0
    for fn in (S485.check_tx_frame, T._check_tx_frame_local):
        try:
            fn(0x81, 0x04, [0x00])
        except ValueError:
            abgelaufen += 1
    T.tx_kalibrier_freigabe_loeschen()
    check("Einmalfreigabe läuft ab", abgelaufen == 2)


def test_l2_transparent_einmal():
    # Konstanten wie Stock (CR:mcu.py:493-495)
    check("L2: Host-Wartezeit 60 s, Poll 0,5 s (Stock ExtruderRetryAsyncCommand)",
          T.TransparentOneShotCommand.TIMEOUT_TIME == 60.0
          and T.TransparentOneShotCommand.RETRY_TIME == 0.5)
    # setup(): is_async=True + Einmal-Helfer
    reactor, ser, mcu, ad = _l2_adapter(1.9, read=bytes(S485.build_frame(
        0x81, 0x0E, [0x00], status=0x00)))
    check("L2: lookup_query_command mit is_async=True und Stock-Formaten",
          mcu.lookups == [(T.TRANSPARENT_SEND_FMT, T.TRANSPARENT_RESP_FMT, 7, True)],
          mcu.lookups)
    check("L2: Sende-Helfer = TransparentOneShotCommand",
          ad._query._xmit_helper is T.TransparentOneShotCommand and ad.one_shot)
    # späte Antwort (1,9 s ~ Broadcast-Zeitfenster) über TransparentTransport
    tr = T.TransparentTransport(ad)
    t0 = reactor.now
    st, fn, data = tr.transact(0x81, 0x0E, [0x01], timeout=2.0, retries=0)
    check("L2: Antwort nach 1,9 s -> GENAU 1 transparent_send, 0 Neusendungen",
          len(ser.sends) == 1 and ser.resends == [] and ad.sends == 1,
          (len(ser.sends), len(ser.resends)))
    check("L2: gesendeter Frame byte-genau, timeout_ms 2000",
          ser.sends[0] == (7, bytes(S485.build_frame(0x81, 0x0E, [0x01])), 2000),
          ser.sends)
    check("L2: Antwort geparst (fun 0E, Daten 00), Wartezeit ~1,9 s",
          (st, fn, data) == (0, 0x0E, b"\x00") and 1.85 <= reactor.now - t0 <= 2.0,
          (st, fn, data, reactor.now - t0))
    check("L2: Antwort-Callback danach abgemeldet", ser.handlers == {}, ser.handlers)

    # keine Antwort: einmal senden, > 60 s warten, TimeoutError
    reactor, ser, mcu, ad = _l2_adapter(None)
    tr = T.TransparentTransport(ad)
    t0 = reactor.now
    err = None
    try:
        tr.transact(0x81, 0x0E, [0x01], timeout=2.0, retries=0)
    except Exception as exc:
        err = exc
    waited = reactor.now - t0
    check("L2: keine Antwort -> TimeoutError (TransparentHostTimeout)",
          isinstance(err, TimeoutError) and isinstance(err, T.TransparentHostTimeout),
          repr(err))
    check("L2: Timeout erst nach > 60 s (Stock TIMEOUT_TIME), nicht nach 5 s/0,5 s",
          60.0 < waited <= 61.0, waited)
    check("L2: auch beim Timeout GENAU 1 Sendung, 0 Neusendungen",
          len(ser.sends) == 1 and ser.resends == [], (len(ser.sends), len(ser.resends)))
    check("L2: Callback nach Timeout abgemeldet", ser.handlers == {}, ser.handlers)
    check("L2: Timeout-Text nennt 60 s und 'keine Wiederholung'",
          "60 s" in str(err) and "keine Wiederholung" in str(err), str(err))

    # veraltete Antwort (#sent_time vor der Anfrage) wird ignoriert
    good = bytes(S485.build_frame(0x81, 0x0E, [0x00]))
    reactor, ser, mcu, ad = _l2_adapter(0.8, read=good, stale=True)
    res = ad.exchange(bytes(S485.build_frame(0x81, 0x0E, [0x01])), 2000)
    check("L2: veraltete Antwort verworfen, echte Antwort geliefert",
          res == good and len(ser.sends) == 1 and ser.resends == [], res)

    # leere Antwort der MCU (MCU-Timeout timeout_ms) -> TimeoutError wie bisher
    reactor, ser, mcu, ad = _l2_adapter(2.0, read=b"")
    tr = T.TransparentTransport(ad)
    err = None
    try:
        tr.transact(0xFF, 0x0E, [0x01], timeout=2.0, retries=0)
    except Exception as exc:
        err = exc
    check("L2: leere transparent_response -> TimeoutError, 1 Sendung",
          isinstance(err, TimeoutError) and len(ser.sends) == 1
          and ser.resends == [], (repr(err), len(ser.sends)))

    # Gegenprobe: Upstream-RetryAsyncCommand (is_async ohne Ersatz) sendet neu
    reactor = _L2Reactor()
    ser = _L2Serial(reactor, answer_after=1.9, read=good)
    q = _L2QueryWrapper(ser, T.TRANSPARENT_RESP_FMT, 7, True, False)
    q.send([7, b"x", 2000])
    check("L2-Gegenprobe: Upstream-is_async allein hätte NEU gesendet (> 1 Frame)",
          len(ser.resends) >= 2, len(ser.resends))
    reactor = _L2Reactor()
    ser = _L2Serial(reactor, answer_after=1.9, read=good)
    q = _L2QueryWrapper(ser, T.TRANSPARENT_RESP_FMT, 7, True, False)
    err = None
    try:
        q.send([7, b"x", 2000], retry=False)
    except Exception as exc:
        err = exc
    check("L2-Gegenprobe: Upstream-is_async mit retry=False bricht nach 0,5 s ab",
          err is not None and len(ser.resends) == 0, repr(err))

    # Batch/fileoutput: DummyResponse bleibt, kein Ersatz
    _r, _s, mcu, ad = _l2_adapter(None, fileoutput=True)
    check("L2: fileoutput (Batch) -> Upstream-DummyResponse bleibt",
          ad._query._xmit_helper == "DummyResponse" and not ad.one_shot)

    # Wrapper ohne _xmit_helper (andere Upstream-Version) -> setup bricht ab
    class _NoHelperMcu(_L2Mcu):
        def lookup_query_command(self, *a, **kw):
            class Q:
                def send(self, *a, **kw):
                    return {"read": b""}
            return Q()
    ad = T.KlippyTransparentAdapter(_NoHelperMcu(None), 3)
    err = None
    try:
        ad.setup()
    except RuntimeError as exc:
        err = exc
    blocked = False
    try:
        ad.exchange(b"x", 10)
    except RuntimeError:
        blocked = True
    check("L2: ohne _xmit_helper -> setup() RuntimeError, Kanal gesperrt",
          err is not None and "WIEDERHOLEN" in str(err) and blocked, repr(err))

    # Fehler, die kein Host-Timeout sind, bleiben unverändert
    class _ShutdownQ:
        _xmit_helper = None

        def send(self, *a, **kw):
            raise _L2CommandError("MCU 'nozzle_mcu' shutdown: Timer too close")

    class _ShutdownMcu(_L2Mcu):
        def lookup_query_command(self, *a, **kw):
            return _ShutdownQ()
    ad = T.KlippyTransparentAdapter(_ShutdownMcu(None), 3)
    ad.setup()
    err = None
    try:
        ad.exchange(b"x", 10)
    except Exception as exc:
        err = exc
    check("L2: andere MCU-Fehler nicht als TimeoutError getarnt",
          isinstance(err, _L2CommandError), repr(err))


def test_l2_quellen():
    """Quelltext-Abgleich: Upstream-Schnittstelle + Stock-Wartezeit."""
    root = os.path.dirname(_HERE)
    up = os.path.join(root, "lokal", "upstream-klipper", "klippy", "mcu.py")
    cr = os.path.join(root, "lokal", "stock-rootfs", "usr", "share", "klipper", "klippy", "mcu.py")
    if os.path.isfile(up):
        src = open(up, encoding="utf-8").read()
        check("L2-Quelle Upstream: is_async -> RetryAsyncCommand, _xmit_helper-Aufruf passt",
              "self._xmit_helper = RetryAsyncCommand" in src
              and "xh = self._xmit_helper(self._serial, self._response, self._oid)" in src
              and "xh.get_response(cmds, self._cmd_queue, minclock, reqclock," in src)
        i = src.index("class RetryAsyncCommand")
        j = src.index("class CommandQueryWrapper")
        check("L2-Quelle Upstream: RetryAsyncCommand sendet in der Warteschleife neu",
              "self.serial.raw_send(cmd, minclock, minclock, cmd_queue)" in src[i:j])
    else:
        print("SKIP L2-Quelle Upstream (Klon fehlt: %s)" % up)
    if os.path.isfile(cr):
        src = open(cr, encoding="utf-8").read()
        i = src.index("class ExtruderRetryAsyncCommand")
        part = src[i:i + 400]
        check("L2-Quelle Stock: ExtruderRetryAsyncCommand 60 s, 0,5 s, IS_RESEND False",
              "TIMEOUT_TIME = 60.0" in part and "RETRY_TIME = 0.500" in part
              and "IS_RESEND = False" in part)
    else:
        print("SKIP L2-Quelle Stock (lokal/stock-rootfs fehlt)")


# ---------------------------------------------------------------------
# 10. rs485_de (Träger Alpha 1)
# ---------------------------------------------------------------------

class FakeSerialFd(FakeSerial):
    def fileno(self):
        return 7


class FakeIoctl:
    """Kernel-Seite von TIOCGRS485/TIOCSRS485 (struct serial_rs485, 32 Byte)."""

    def __init__(self, flags=0, before=0, after=0, fehler=None, ignoriert_set=False):
        import struct
        self.st = struct.Struct(B.RS485_STRUCT_FMT)
        self.werte = [flags, before, after, 0, 0, 0, 0, 0]
        self.fehler = fehler
        self.ignoriert_set = ignoriert_set
        self.calls = []

    def __call__(self, fd, req, buf):
        self.calls.append((fd, req, len(buf)))
        if self.fehler is not None:
            raise self.fehler
        if req == B.TIOCGRS485:
            buf[:] = self.st.pack(*self.werte)
            return 0
        if req == B.TIOCSRS485:
            if not self.ignoriert_set:
                self.werte = list(self.st.unpack(bytes(buf)))
            return 0
        raise OSError(25, "Inappropriate ioctl for device")


def test_rs485_de():
    import errno
    E = B.SER_RS485_ENABLED
    ON = B.SER_RS485_RTS_ON_SEND
    AFTER = B.SER_RS485_RTS_AFTER_SEND
    try:
        # a) Standard adapter: kein ioctl, Verhalten wie bisher
        io = FakeIoctl()
        B.IOCTL = io
        fac = Factory(FakeSerialFd([RESP]))
        pr, bus = make(factory=fac)
        pr.send("klippy:connect")
        check("rs485_de: Standard = adapter", bus.rs485_de == "adapter")
        check("rs485_de adapter: kein ioctl", io.calls == [], str(io.calls))
        check("rs485_de adapter: Port offen", bus.is_open())
        check("rs485_de adapter: Status zeigt de=adapter",
              "de=adapter" in bus.status_text(), bus.status_text())

        # b) kernel, Flags 0 -> gesetzt (ENABLED|RTS_ON_SEND), Verzögerungen bleiben
        io = FakeIoctl(flags=0, before=0, after=2)
        B.IOCTL = io
        fac = Factory(FakeSerialFd([RESP]))
        pr, bus = make(factory=fac, opts={"rs485_de": "kernel", "serial": "/dev/ttyAMA0"})
        pr.send("klippy:connect")
        reqs = [c[1] for c in io.calls]
        check("rs485_de kernel: G, S, G (Prüfen, Setzen, Rückprüfen)",
              reqs == [B.TIOCGRS485, B.TIOCSRS485, B.TIOCGRS485], str(reqs))
        check("rs485_de kernel: struct 32 Byte, fd aus fileno()",
              all(c[2] == 32 and c[0] == 7 for c in io.calls), str(io.calls))
        check("rs485_de kernel: Flags ENABLED|RTS_ON_SEND, ohne AFTER_SEND",
              io.werte[0] == (E | ON), hex(io.werte[0]))
        check("rs485_de kernel: Verzögerung nach dem Senden unverändert (2 ms)",
              io.werte[2] == 2, str(io.werte))
        check("rs485_de kernel: Port offen, Status 'gesetzt'",
              bus.is_open() and bus.rs485_kernel == "gesetzt"
              and bus.get_status()["rs485_de"] == "kernel", bus.status_text())
        check("rs485_de kernel: Frame geht wie gewohnt über den Bus",
              bus.transact_full(0x81, 0x0F, [0x01], timeout=0.2, retries=0) is not None
              and fac.instances[0].ser.written == [REQ])

        # c) Device-Tree hat schon gesetzt -> nur lesen
        io = FakeIoctl(flags=E | ON)
        B.IOCTL = io
        fac = Factory(FakeSerialFd())
        pr, bus = make(factory=fac, opts={"rs485_de": "kernel"})
        pr.send("klippy:connect")
        check("rs485_de kernel: Device-Tree passend -> kein TIOCSRS485",
              [c[1] for c in io.calls] == [B.TIOCGRS485] and bus.rs485_kernel == "Device-Tree",
              str(io.calls))
        check("rs485_de kernel: KTWO_BUS_STATUS nennt de=kernel/Device-Tree",
              "de=kernel/Device-Tree" in bus.status_text(), bus.status_text())

        # d) invertierte Polarität (rs485_invert_rts) -> korrigiert
        io = FakeIoctl(flags=E | AFTER)
        B.IOCTL = io
        fac = Factory(FakeSerialFd())
        pr, bus = make(factory=fac, opts={"rs485_de": "kernel"})
        pr.send("klippy:connect")
        check("rs485_de kernel: invertierte RTS-Polarität korrigiert",
              io.werte[0] == (E | ON) and bus.is_open(), hex(io.werte[0]))

        # e) Treiber ohne RS485 (USB-Adapter: ENOTTY) -> zu, Meldung, kein Absturz
        io = FakeIoctl(fehler=OSError(errno.ENOTTY, "Inappropriate ioctl for device"))
        B.IOCTL = io
        ser = FakeSerialFd()
        fac = Factory(ser)
        pr, bus = make(factory=fac, opts={"rs485_de": "kernel"})
        pr.send("klippy:connect")
        st = bus.get_status()
        check("rs485_de kernel ohne Treiber: Port zu, kein Absturz",
              not bus.is_open() and st["open"] is False, str(st))
        check("rs485_de kernel ohne Treiber: Fehlertext nennt rs485_de/adapter",
              "rs485_de" in (st["error"] or "") and "adapter" in (st["error"] or ""),
              str(st["error"]))
        check("rs485_de kernel ohne Treiber: Serial485-Instanz getrennt (disconnect)",
              fac.instances and fac.instances[0]._connected is False)
        try:
            bus.transact_full(0x81, 0x0F, [0x01], timeout=0.1, retries=0)
            ok = False
        except RuntimeError as exc:
            ok = "rs485_de" in str(exc)
        check("rs485_de kernel ohne Treiber: Zugriff -> RuntimeError mit Grund", ok)
        check("rs485_de kernel ohne Treiber: nichts gesendet", ser.written == [])

        # f) Rückprüfung scheitert (Treiber ignoriert das Setzen)
        io = FakeIoctl(flags=0, ignoriert_set=True)
        B.IOCTL = io
        fac = Factory(FakeSerialFd())
        pr, bus = make(factory=fac, opts={"rs485_de": "kernel"})
        pr.send("klippy:connect")
        check("rs485_de kernel: Rückprüfung scheitert -> Port zu",
              not bus.is_open() and "nicht aktiv" in (bus.last_error or ""),
              str(bus.last_error))

        # g) Port ohne fileno
        B.IOCTL = FakeIoctl()
        fac = Factory(FakeSerial())
        pr, bus = make(factory=fac, opts={"rs485_de": "kernel"})
        pr.send("klippy:connect")
        check("rs485_de kernel: Port ohne fileno -> Meldung, zu",
              not bus.is_open() and "fileno" in (bus.last_error or ""), str(bus.last_error))

        # h) ungültiger Wert -> Config-Fehler; Groß-/Kleinschreibung egal
        try:
            make(factory=Factory(), opts={"rs485_de": "gpio"})
            ok = False
        except ValueError as exc:
            ok = "rs485_de" in str(exc)
        check("rs485_de: ungültiger Wert 'gpio' -> Config-Fehler", ok)
        pr, bus = make(factory=Factory(), opts={"rs485_de": "Kernel"})
        check("rs485_de: 'Kernel' wird als kernel gelesen", bus.rs485_de == "kernel")
        try:
            B.K2Bus485(None, "/dev/x", rs485_de="xyz")
            ok = False
        except ValueError:
            ok = True
        check("rs485_de: K2Bus485 prüft den Wert selbst", ok)

        # i) Konstanten gegen linux/serial.h + asm-generic/ioctls.h
        check("rs485_de: ioctl-Nummern/Flags wie Kernel-UAPI",
              (B.TIOCGRS485, B.TIOCSRS485) == (0x542E, 0x542F)
              and (E, ON, AFTER, B.SER_RS485_RX_DURING_TX) == (1, 2, 4, 16))
    finally:
        B.IOCTL = None
        B.SERIAL_FACTORY = None


def main():
    for fn in (test_laden_und_connect, test_gemeinsam_mit_modulen, test_batch,
               test_port_fehlt, test_ausfall_timeout, test_konsole_statistik,
               test_config_fehler,
               test_py39, test_motor_sendeschutz, test_update_sendeschutz,
               test_stufe2_sendeschutz, test_motor_uuid_encoder_sperre,
               test_kalibrier_einmalfreigabe,
               test_l2_transparent_einmal,
               test_l2_quellen, test_rs485_de):
        try:
            fn()
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(fn.__name__ + " (Ausnahme)", False, repr(exc))
    B.SERIAL_FACTORY = None
    B.IOCTL = None
    fails = [n for n, ok in RESULTS if not ok]
    print("ERGEBNIS: %s (%d/%d Prüfungen ok)" % (
        "ok" if not fails else "%d FEHLER" % len(fails),
        len(RESULTS) - len(fails), len(RESULTS)))
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
