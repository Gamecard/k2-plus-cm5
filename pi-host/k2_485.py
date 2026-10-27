#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_485.py – gemeinsamer RS485-Bus [k2_485] für Upstream-Klipper (K2 Plus).

Ersetzt die Stock-Abschnitte ``[serial_485 serial485]`` (serial_485_wrapper.so,
/dev/ttyS5, 230400 8N1). Am Stock-Bus hängen ALLE RS485-Teilnehmer:
Closed-loop-Motoren X/Y/Z/Z1 (0x81–0x84), CFS-Box (0x01 …) und die
Riemenspann-Module (0x21/0x22). Halbduplex → es darf genau EIN offener Port
mit EINEM Lock existieren (vgl. nachbau/serial485.Serial485._lock,
k2_transport.Rs485Transport-Docstring).

Dieses Modul stellt genau EINE ``nachbau/serial485.Serial485``-Instanz bereit:

- Objektname ``k2_485``: Klipper legt das von ``load_config`` gelieferte
  Objekt beim LADEN unter dem Abschnittsnamen ab (klippy.py ``load_object``:
  ``self.objects[section] = init_func(...)`` – wirkt wie
  ``printer.add_object('k2_485', …)``). ``[k2_motor]`` lädt es bei Bedarf
  selbst vor (``printer.load_object``), ``[k2_box]``/``[k2_belt]`` suchen es
  beim Connect (``lookup_object('k2_485')``) → Reihenfolge der Abschnitte in
  printer.cfg egal.
- Port öffnen erst bei ``klippy:connect`` (bzw. faul beim ersten Frame, falls
  das Öffnen beim Connect scheiterte). Beim Laden wird NICHTS geöffnet.

Verhalten OHNE Port (dokumentiert, im Test geprüft):
- Batch-/Debug-Modus (``klippy.py -o …``, Start-Argument ``debugoutput``):
  Port wird NIE geöffnet (kein Hardwarezugriff bei Trockenläufen). Meldung im
  Log: ``[k2_485] Batch-Modus: RS485-Port … wird nicht geöffnet``. Jeder
  Bus-Zugriff wirft ``RuntimeError`` mit dieser Begründung.
- Normalbetrieb, Port fehlt/belegt/pyserial fehlt: KEIN Absturz, KEIN
  Shutdown. Beim Connect eine Meldung ``[k2_485] Port … nicht zu öffnen: …``
  (Log + Konsole); ``get_status`` zeigt ``open: False`` + ``error``.
  Jeder Bus-Zugriff versucht das Öffnen erneut (USB-Adapter nachträglich
  eingesteckt) und wirft sonst ``RuntimeError("[k2_485] Port … nicht zu
  öffnen …")`` – die Nutzer-Module melden das als G-Code-Fehler bzw.
  Diagnose-Meldung.
- Bricht der Port im Betrieb weg (``OSError``/``SerialException``, nicht
  ``TimeoutError``), wird er geschlossen und beim nächsten Zugriff neu
  geöffnet.

Config::

    [k2_485]
    serial: /dev/serial/by-id/usb-…-if00-port0   # Pflicht
    baud: 230400                                  # Stock 230400 8N1
    #timeout: 1.0                                 # Default-Timeout je Frame (s)
    #warn_window: 600        # s, Fenster der Bus-Statistik-Warnung
    #warn_timeouts: 30       # je Fenster; 0 = Kriterium aus
    #warn_crc_errors: 10     # je Fenster; 0 = aus
    #warn_retries: 30        # je Fenster; 0 = aus (alle drei 0 = kein Timer)
    #rs485_de: adapter       # adapter | kernel (s. unten)

Richtungsumschaltung DE (``rs485_de``, K2-CM5-Träger Alpha 1, 24.10.2026):

- ``adapter`` (Standard, Verhalten wie bisher): USB-RS485-Adapter schaltet
  DE selbst (Auto-DE). Es wird KEIN ioctl abgesetzt.
- ``kernel``: Onboard-UART (Träger: ``/dev/ttyAMA0``, UART0 GPIO14/15,
  THVD1450 DE/~RE an GPIO17 = UART0-RTS). Der PL011-Treiber schaltet RTS
  (= DE) selbst: high nur während des Sendens, danach sofort low
  (``amba-pl011.c`` pl011_rs485_tx_start/-stop, Abfall nach Leeren des
  Schieberegisters). Normal setzt das schon der Device-Tree
  (``dtoverlay=uart0-pi5,rs485``: ``linux,rs485-enabled-at-boot-time`` + RTS
  auf GPIO17). Beim Öffnen prüft [k2_485] per ``TIOCGRS485`` und setzt bei
  Bedarf per ``TIOCSRS485``: ENABLED + RTS_ON_SEND, ohne RTS_AFTER_SEND
  (DE aktiv high), Verzögerungen bleiben. Rückprüfung; scheitert sie, bleibt
  der Port ZU (klare Meldung, kein Absturz). Hinweis: Fehlt im Overlay
  ``rs485``/``ctsrts``, liegt RTS nicht auf GPIO17 – das sieht kein ioctl
  (Prüfung ``pinctrl get 17``, README-traeger-alpha1.md).
- DE per GPIO in Software ist bewusst NICHT umgesetzt: Bei 230400 Baud muss
  DE ≤ 1 Zeichen (≈ 43 µs) nach dem letzten Stoppbit fallen, sonst gehen die
  ersten Antwortbytes der Motoren verloren; ``tcdrain`` schläft in Jiffies
  (≥ 4 ms) und der Bus läuft in einem Python-Hilfs-Thread (GIL-Wechsel bis
  5 ms) – nicht verlässlich.

G-Code: ``KTWO_BUS_STATUS`` (nur Zähler/Zustand, KEIN Bus-Verkehr).

Konsole (23.10.2026, k2_konsole):
- Connect-Meldungen (Port offen / nicht zu öffnen / Batch) erscheinen kurz
  nach „ready“ in Mainsail (``!! [k2_485] …`` bei Fehlern) – vorher gingen
  sie beim Start verloren.
- Bus-Statistik-Wächter (Reactor-Timer alle 60 s, ab 180 s nach „ready“):
  steigen Timeouts/CRC-Fehler/Wiederholungen innerhalb von ``warn_window``
  über die Grenzen, kommt EINE Warnung ``// [k2_485] RS485-Bus: …`` mit den
  Zahlen (höchstens alle 30 min). Port-Ausfall/-Rückkehr im Betrieb meldet
  derselbe Timer (``_call`` selbst läuft teils im Hilfs-Thread des
  ReactorBusProxy und schreibt deshalb NIE in die Konsole).

Bus-API (an Serial485 durchgereicht): ``transact``, ``transact_full``,
``raw_send``, ``raw_send_wait_ack``, ``raw_send_wait_ack_full``,
``get_response``, ``register_response``, ``stats`` – dazu ``is_open()``,
``open()``, ``close()``.

Ohne Klipper importierbar (Tests: ``python3 test_k2_485.py``).
Python-3.9-kompatibel.
"""

import logging

# Import-Regel wie k2_transport.py: eigener Ordner NIE in sys.path,
# Geschwister relativ (Klipper-Paket extras), Fallback absolut (Tests).
try:
    from . import k2_transport as _K2T
except ImportError:
    import k2_transport as _K2T

try:
    from . import k2_konsole as _K2K
except ImportError:
    import k2_konsole as _K2K

SECTION = "k2_485"
DEFAULT_BAUD = 230400
DEFAULT_TIMEOUT = 1.0   # = serial485.TIMEOUT_LONG

# RS485-Richtungsumschaltung (Träger Alpha 1, 24.10.2026)
RS485_DE_MODI = ("adapter", "kernel")
DEFAULT_RS485_DE = "adapter"
# linux/include/uapi/asm-generic/ioctls.h (arm64 = asm-generic)
TIOCGRS485 = 0x542E
TIOCSRS485 = 0x542F
# linux/include/uapi/linux/serial.h, struct serial_rs485 (8 × __u32 = 32 Byte)
SER_RS485_ENABLED = 1 << 0
SER_RS485_RTS_ON_SEND = 1 << 1
SER_RS485_RTS_AFTER_SEND = 1 << 2
SER_RS485_RX_DURING_TX = 1 << 4
RS485_STRUCT_FMT = "=8I"
# Test-Haken: callable(fd, request, buf) wie fcntl.ioctl (buf wird verändert).
IOCTL = None

# Bus-Statistik-Wächter (Konsolen-Audit 23.10.2026)
STAT_KEYS = ("sent", "timeouts", "crc_errors", "retries")
STAT_TAKT = 60.0         # s, Abfrage der Zähler (kein Bus-Verkehr)
STAT_KARENZ = 180.0      # s nach „ready“ ohne Wertung (Start-Suchläufe)
STAT_PAUSE = 1800.0      # s, gleiche Warnung höchstens so oft
WARN_DEFAULTS = {"warn_window": 600.0, "warn_timeouts": 30,
                 "warn_crc_errors": 10, "warn_retries": 30}

# Test-Haken: callable(port, baud, timeout) -> Serial485-artiges Objekt.
# None = echte nachbau/serial485.Serial485 (pyserial).
SERIAL_FACTORY = None


def _rs485_flags_ok(flags):
    """DE aktiv high: RTS-Pin high beim Senden, low danach (THVD1450)."""
    return (bool(flags & SER_RS485_ENABLED)
            and bool(flags & SER_RS485_RTS_ON_SEND)
            and not flags & SER_RS485_RTS_AFTER_SEND)


def rs485_kernel_sicherstellen(fd, ioctl=None):
    """Kernel-RS485-Modus prüfen/setzen (rs485_de: kernel).

    Rückgabe: Text ``"Device-Tree"`` (war schon passend) bzw. ``"gesetzt"``
    (per TIOCSRS485 eingeschaltet/korrigiert). RuntimeError mit Begründung,
    wenn der Treiber kein RS485 kann oder die Rückprüfung scheitert."""
    import struct
    if ioctl is None:
        ioctl = IOCTL
    if ioctl is None:
        import fcntl
        ioctl = fcntl.ioctl
    st = struct.Struct(RS485_STRUCT_FMT)
    buf = bytearray(st.size)
    try:
        ioctl(fd, TIOCGRS485, buf)
    except OSError as exc:
        raise RuntimeError("Kernel-RS485 nicht verfügbar (TIOCGRS485: %s) – "
                           "Port ohne RS485-Treiber? Für USB-Adapter "
                           "rs485_de: adapter" % (exc,))
    werte = list(st.unpack(bytes(buf)))
    if _rs485_flags_ok(werte[0]):
        return "Device-Tree"
    werte[0] = ((werte[0] | SER_RS485_ENABLED | SER_RS485_RTS_ON_SEND)
                & ~SER_RS485_RTS_AFTER_SEND)
    neu = bytearray(st.pack(*werte))
    try:
        ioctl(fd, TIOCSRS485, neu)
        zurueck = bytearray(st.size)
        ioctl(fd, TIOCGRS485, zurueck)
    except OSError as exc:
        raise RuntimeError("Kernel-RS485 nicht einschaltbar (TIOCSRS485: %s)"
                           % (exc,))
    flags = st.unpack(bytes(zurueck))[0]
    if not _rs485_flags_ok(flags):
        raise RuntimeError("Kernel-RS485 nach TIOCSRS485 nicht aktiv "
                           "(flags=0x%x) – Overlay uart0-pi5,rs485 prüfen"
                           % flags)
    return "gesetzt"


def _load_serial485():
    """nachbau/serial485 laden (Herkunft geprüft) – ImportError mit Hinweis."""
    return _K2T.import_nachbau("serial485")


class BusStatWaechter:
    """Reine Auswertung der Bus-Zähler (testbar, ohne Klipper).

    ``pruefe(jetzt, stats)`` merkt sich je Aufruf die Zählerstände und liefert
    einen Warntext, wenn innerhalb von ``fenster`` s die Zunahme von
    timeouts/crc_errors/retries eine Grenze erreicht (Grenze 0 = aus), sonst
    None. Nach einer Warnung beginnt das Fenster neu. Fallende Zähler (Port
    neu geöffnet = neue Instanz) setzen die Historie zurück. In den ersten
    ``karenz`` s nach ``start`` wird nur beobachtet."""

    def __init__(self, fenster=600.0, timeouts=30, crc_errors=10, retries=30,
                 karenz=STAT_KARENZ, start=0.0):
        self.fenster = float(fenster)
        self.grenzen = {"timeouts": int(timeouts),
                        "crc_errors": int(crc_errors),
                        "retries": int(retries)}
        self.karenz = float(karenz)
        self.start = float(start)
        self.verlauf = []        # [(zeit, {key: wert})]
        self.warnungen = 0

    def aktiv(self):
        return any(v > 0 for v in self.grenzen.values())

    def pruefe(self, jetzt, stats):
        if not self.aktiv() or not stats:
            return None
        if jetzt - self.start < self.karenz:
            self.verlauf = []
            return None
        try:
            cur = {k: int(stats.get(k) or 0) for k in STAT_KEYS}
        except (TypeError, ValueError):
            return None
        if self.verlauf and any(cur[k] < self.verlauf[-1][1][k]
                                for k in STAT_KEYS):
            self.verlauf = []    # Zähler zurückgesetzt (neue Instanz)
        self.verlauf.append((jetzt, cur))
        while len(self.verlauf) > 1 and \
                jetzt - self.verlauf[1][0] >= self.fenster:
            self.verlauf.pop(0)
        t0, alt = self.verlauf[0]
        diff = {k: cur[k] - alt[k] for k in STAT_KEYS}
        ueber = [k for k, g in self.grenzen.items() if g > 0 and diff[k] >= g]
        if not ueber:
            return None
        self.verlauf = [(jetzt, cur)]
        self.warnungen += 1
        minuten = max(1, int(round((jetzt - t0) / 60.0)))
        return ("RS485-Bus: in den letzten %d min %d Timeouts, %d CRC-Fehler, "
                "%d Wiederholungen bei %d Sendungen (Grenze erreicht: %s) – "
                "Kabel/Stecker/Abschluss des Busses prüfen; bei "
                "ausgeschalteter CFS-Box laufen deren Abfragen ins Leere. "
                "Zähler: KTWO_BUS_STATUS"
                % (minuten, diff["timeouts"], diff["crc_errors"],
                   diff["retries"], diff["sent"], ", ".join(ueber)))


class K2Bus485:
    """Gemeinsamer RS485-Bus (genau eine Serial485-Instanz, faul geöffnet)."""

    PASSTHROUGH = ("transact", "transact_full", "raw_send", "raw_send_wait_ack",
                   "raw_send_wait_ack_full", "get_response", "register_response")

    def __init__(self, printer, port, baud=DEFAULT_BAUD, timeout=DEFAULT_TIMEOUT,
                 batch=False, factory=None, respond=None, respond_error=None,
                 rs485_de=DEFAULT_RS485_DE):
        if not port:
            raise ValueError("serial: fehlt")
        if rs485_de not in RS485_DE_MODI:
            raise ValueError("rs485_de: '%s' unbekannt (erlaubt: %s)"
                             % (rs485_de, ", ".join(RS485_DE_MODI)))
        self.rs485_de = rs485_de
        self.rs485_kernel = None   # "Device-Tree" | "gesetzt" (nur kernel)
        self.printer = printer
        self.port = str(port)
        self.baud = int(baud)
        self.timeout = float(timeout)
        self.batch = bool(batch)
        self._factory = factory
        self._respond = respond
        self._respond_error = respond_error
        self._bus = None
        self.last_error = None
        self.open_count = 0
        self.fail_count = 0      # Port im Betrieb ausgefallen (OSError)

    # --- Zustand ------------------------------------------------------
    def is_open(self):
        return self._bus is not None

    def _msg(self, text, error=False):
        # Konsole/gcode schreibt selbst ins Log – sonst nur logging.
        respond = (self._respond_error if error and self._respond_error
                   else self._respond)
        if respond is not None:
            try:
                respond(text)
                return
            except Exception:
                pass
        (logging.error if error else logging.info)(text)

    def open(self):
        """Port öffnen (idempotent). RuntimeError mit klarer Meldung."""
        if self._bus is not None:
            return self._bus
        if self.batch:
            self.last_error = ("Batch-Modus: RS485-Port %s wird nicht geöffnet"
                               % self.port)
            raise RuntimeError("[k2_485] " + self.last_error)
        factory = self._factory or SERIAL_FACTORY
        try:
            if factory is not None:
                bus = factory(self.port, self.baud, self.timeout)
            else:
                s485 = _load_serial485()
                bus = s485.Serial485(self.port, self.baud, timeout=self.timeout)
        except BaseException as exc:  # pyserial fehlt -> SystemExit im Nachbau
            if isinstance(exc, (KeyboardInterrupt, GeneratorExit)):
                raise
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            raise RuntimeError("[k2_485] Port %s nicht zu öffnen (%s) – serial: in "
                               "[k2_485] prüfen (ls -l /dev/serial/by-id/)"
                               % (self.port, self.last_error))
        if self.rs485_de == "kernel":
            try:
                ser = getattr(bus, "ser", None)
                if ser is None or not hasattr(ser, "fileno"):
                    raise RuntimeError("Port ohne Dateideskriptor (fileno)")
                self.rs485_kernel = rs485_kernel_sicherstellen(ser.fileno())
            except (RuntimeError, OSError, ValueError) as exc:
                if hasattr(bus, "disconnect"):
                    try:
                        bus.disconnect()
                    except Exception:
                        pass
                self.rs485_kernel = None
                self.last_error = "rs485_de: kernel – %s" % (exc,)
                raise RuntimeError("[k2_485] Port %s: %s" % (self.port, self.last_error))
        self._bus = bus
        self.last_error = None
        self.open_count += 1
        return bus

    def close(self):
        bus, self._bus = self._bus, None
        if bus is not None and hasattr(bus, "disconnect"):
            try:
                bus.disconnect()
            except Exception:
                pass

    # --- Bus-API (durchgereicht, faul geöffnet) ------------------------
    def _call(self, name, *args, **kwargs):
        bus = self.open()
        try:
            return getattr(bus, name)(*args, **kwargs)
        except TimeoutError:
            raise  # normale Antwort-Zeitüberschreitung, Port bleibt offen
        except OSError as exc:  # SerialException (Adapter gezogen o. ä.)
            # KEINE Konsole hier (evtl. Hilfs-Thread) – der Statistik-Timer
            # meldet den Ausfall über fail_count.
            self.last_error = "%s: %s" % (type(exc).__name__, exc)
            self.fail_count += 1
            self.close()
            raise RuntimeError("[k2_485] Port %s ausgefallen (%s) – wird beim "
                               "nächsten Zugriff neu geöffnet"
                               % (self.port, self.last_error))

    def transact(self, *args, **kwargs):
        return self._call("transact", *args, **kwargs)

    def transact_full(self, *args, **kwargs):
        return self._call("transact_full", *args, **kwargs)

    def raw_send(self, *args, **kwargs):
        return self._call("raw_send", *args, **kwargs)

    def raw_send_wait_ack(self, *args, **kwargs):
        return self._call("raw_send_wait_ack", *args, **kwargs)

    def raw_send_wait_ack_full(self, *args, **kwargs):
        return self._call("raw_send_wait_ack_full", *args, **kwargs)

    def get_response(self, *args, **kwargs):
        return self._call("get_response", *args, **kwargs)

    def register_response(self, *args, **kwargs):
        return self._call("register_response", *args, **kwargs)

    def bus_stats(self):
        """Zähler der offenen Instanz (ohne Port: leer) – kein Bus-Verkehr.

        Bewusst NICHT ``stats``: Klippers [statistics] ruft jedes Druckerobjekt mit
        Attribut ``stats`` als ``stats(eventtime)`` auf und erwartet ``(aktiv, text)``
        – ein ``stats(self)`` führte im Echtbetrieb (22.10.2026) zu
        „Unhandled exception during run“ und Shutdown."""
        if self._bus is None or not hasattr(self._bus, "stats"):
            return {}
        return dict(self._bus.stats())

    # --- Klipper-Ereignisse ---------------------------------------------
    def handle_connect(self):
        if self.batch:
            self.last_error = ("Batch-Modus: RS485-Port %s wird nicht geöffnet"
                               % self.port)
            self._msg("[k2_485] " + self.last_error)
            return
        try:
            self.open()
            de = ("DE: Kernel-RS485 (%s)" % self.rs485_kernel
                  if self.rs485_de == "kernel" else "DE: Adapter")
            self._msg("[k2_485] RS485-Port %s offen (%d Baud, %s)"
                      % (self.port, self.baud, de))
        except RuntimeError as exc:
            # KEIN Absturz: Nutzer-Module melden Fehler beim Zugriff.
            self._msg("%s – Motoren/CFS/Belt ohne Bus, Zugriffe melden Fehler"
                      % exc, error=True)

    def handle_disconnect(self):
        self.close()

    def get_status(self, eventtime=None):
        return {"serial": self.port, "baud": self.baud, "open": self.is_open(),
                "batch": self.batch, "error": self.last_error,
                "rs485_de": self.rs485_de, "rs485_kernel": self.rs485_kernel,
                "stats": self.bus_stats()}

    def status_text(self):
        st = self.get_status()
        return ("[k2_485] serial=%s baud=%d offen=%s batch=%s de=%s%s fehler=%s stats=%s"
                % (st["serial"], st["baud"], st["open"], st["batch"],
                   st["rs485_de"],
                   ("/" + st["rs485_kernel"]) if st["rs485_kernel"] else "",
                   st["error"], st["stats"]))


class PrinterK2Bus485(K2Bus485):
    """Klipper-Objekt [k2_485]."""

    def __init__(self, config):
        printer = config.get_printer()
        port = config.get("serial")
        baud = config.getint("baud", DEFAULT_BAUD, minval=1200)
        timeout = config.getfloat("timeout", DEFAULT_TIMEOUT, above=0.)
        rs485_de = str(config.get("rs485_de", DEFAULT_RS485_DE)).strip().lower()
        if rs485_de not in RS485_DE_MODI:
            raise config.error("[k2_485] rs485_de: '%s' unbekannt (erlaubt: %s)"
                               % (rs485_de, ", ".join(RS485_DE_MODI)))
        batch = False
        try:
            batch = printer.get_start_args().get("debugoutput") is not None
        except Exception:
            pass
        # nachbau/serial485 muss importierbar sein (sonst Config-Fehler beim
        # Laden statt Überraschung beim Connect) – außer mit Test-Factory.
        if SERIAL_FACTORY is None:
            try:
                _load_serial485()
            except ImportError as exc:
                raise config.error("[k2_485] %s" % exc)
        warn = {"warn_window": config.getfloat(
                    "warn_window", WARN_DEFAULTS["warn_window"], above=0.)}
        for key in ("warn_timeouts", "warn_crc_errors", "warn_retries"):
            warn[key] = config.getint(key, WARN_DEFAULTS[key], minval=0)
        self.gcode = printer.lookup_object("gcode")
        self.konsole = _K2K.Konsole(printer, "k2_485", self.gcode)
        K2Bus485.__init__(self, printer, port, baud=baud, timeout=timeout,
                          batch=batch, respond=self._respond_console,
                          respond_error=self._respond_console_error,
                          rs485_de=rs485_de)
        self.waechter = BusStatWaechter(
            warn["warn_window"], warn["warn_timeouts"],
            warn["warn_crc_errors"], warn["warn_retries"])
        self._fail_gemeldet = 0
        self._port_weg_gemeldet = False
        printer.register_event_handler("klippy:connect", self.handle_connect)
        printer.register_event_handler("klippy:ready", self.handle_ready)
        printer.register_event_handler("klippy:disconnect", self.handle_disconnect)
        self.gcode.register_command("KTWO_BUS_STATUS", self.cmd_status,
                                    desc="RS485-Bus [k2_485]: Zustand/Zähler "
                                         "(kein Bus-Verkehr)")

    def handle_connect(self):
        K2Bus485.handle_connect(self)
        if self._bus is None and self.last_error and not self.batch:
            self._port_weg_gemeldet = True   # schon rot gemeldet (Connect)

    def _respond_console(self, text):
        # Connect-Meldungen: gepuffert bis kurz nach „ready“
        self.konsole.info(text)

    def _respond_console_error(self, text):
        self.konsole.fehler(text)

    # --- Bus-Statistik-Wächter (Reactor-Timer, wirft nie) ----------------
    def handle_ready(self):
        if self.batch or not self.waechter.aktiv():
            return
        try:
            reactor = self.printer.get_reactor()
            self.waechter.start = reactor.monotonic()
            reactor.register_timer(self._stat_timer,
                                   reactor.monotonic() + STAT_TAKT)
        except Exception:
            logging.exception("[k2_485] Statistik-Timer nicht startbar")

    def _stat_timer(self, eventtime):
        try:
            self._stat_check(eventtime)
        except Exception:
            logging.exception("[k2_485] Statistik-Prüfung")
        return eventtime + STAT_TAKT

    def _stat_check(self, eventtime):
        if self.fail_count > self._fail_gemeldet:
            self._fail_gemeldet = self.fail_count
            self.konsole.fehler(
                "RS485-Port %s im Betrieb ausgefallen (%s, %d× seit dem Start) "
                "– USB-Adapter/Kabel prüfen; wird beim nächsten Zugriff neu "
                "geöffnet" % (self.port, self.last_error, self.fail_count),
                schluessel="ausfall", pause=STAT_PAUSE)
            if self._bus is None:
                self._port_weg_gemeldet = True   # kein zweites „nicht offen“
        if self._bus is None and self.last_error and not self.batch:
            if not self._port_weg_gemeldet:
                self._port_weg_gemeldet = True
                self.konsole.fehler("RS485-Port %s nicht offen (%s)"
                                    % (self.port, self.last_error),
                                    schluessel="weg", pause=STAT_PAUSE)
            return
        if self._bus is not None and self._port_weg_gemeldet:
            self._port_weg_gemeldet = False
            self.konsole.info("RS485-Port %s wieder offen" % (self.port,))
        text = self.waechter.pruefe(eventtime, self.bus_stats())
        if text:
            self.konsole.warnung(text, schluessel="statistik",
                                 pause=STAT_PAUSE)

    def cmd_status(self, gcmd):
        gcmd.respond_info(self.status_text())


def load_config(config):
    """Klipper-Einstieg [k2_485]: Rückgabe = Objekt 'k2_485'."""
    return PrinterK2Bus485(config)
