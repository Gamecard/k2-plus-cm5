#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2-bootstart.py – K2-Geräte aus dem Bootloader in die App starten (VOR Klipper).

!! GERÄTETEST OFFEN – am echten Drucker noch NIE gelaufen. Nur mit ausdrücklicher
!! Freigabe benutzen (BOOTSTART-WERKZEUG.md, PI-INBETRIEBNAHME.md 1.1/2.5/3.3).

Ersetzt den START-Teil der Stock-Werkzeuge mcu_util/mcu_util_485 (Stock-Boot-Check
/etc/init.d/mcu_update). Es wird NIE geflasht, gelöscht oder in den Bootloader
geschickt. Logik + Sendeschutz: nachbau/bootstart.py.

  ~/klippy-env/bin/python ./k2-bootstart.py --main PORT --duese PORT --rs485 PORT
  ~/klippy-env/bin/python ./k2-bootstart.py --aus-config ~/printer_data/config
  ./k2-bootstart.py --dry-run [--main PORT] [--duese PORT] [--rs485 PORT]

Ablauf UART (je --main/--duese):
  1. Klipper-identify @230400 (nur lesend, wie k2-identify.py). Antwortet die App:
     NICHTS weiter senden.
  2. Sonst @115200 8N1: 75 (Handshake) -> 75; 00 FF (Version) -> 25 B + ~Σ;
     nur bei gültiger Version mit Präfix mcu0_/noz0_: 02 FD (Start) -> 75 8A.
  3. Nachkontrolle: identify @230400.
Ablauf RS485 (--rs485, 230400):
  A1-Discovery an 0xFD (Motoren), 0xFC (Belts), 0xFE (CFS); NUR Geräte mit
  mode 1 (Loader): A0-Adressvergabe (Motor 0x85.., Belt 0x23.., CFS 0x01..),
  F0 [00] Version, F0 [03] Sektor, F0 [02] Start, Nachkontrolle F0 [00].
  Geräte in der App (mode 0) werden nicht angefasst.

Optionen:
  --main PORT            Main-MCU (J22-RS232-Konverter)
  --duese PORT           Düsen-MCU
  --rs485 PORT           USB-RS485-Adapter (Motoren/Belts/CFS)
  --aus-config DIR       Ports aus DIR/printer.cfg ([mcu], [mcu nozzle_mcu]) und
                         DIR/k2-module.cfg ([k2_485]); Platzhalter (ANNAHME) werden
                         ausgelassen. Ausdrückliche --main/--duese/--rs485 haben Vorrang.
  --ohne-motoren / --ohne-belts / --ohne-cfs
                         RS485-Gruppe auslassen (kein A1 an 0xFD/0xFC/0xFE)
  --a1-runden N          Discovery-Runden je Gruppe, 1..3 (Standard 3 wie Stock)
  --identify-timeout S   Wartezeit des Klipper-identify (Standard 5 s)
  --warte-auf-ports S    bis S Sekunden warten, bis alle Ports existieren (systemd, Standard 0)
  --gesamt-timeout S     harte Obergrenze für den ganzen Lauf (Standard 240 s)
  --ohne-nachkontrolle   nach dem Start nicht nachprüfen
  --nur-erkennen         nur feststellen, wer im Bootloader ist (UART: identify, 75, 00 FF;
                         RS485: nur A1) – NIE Start/A0/F0 (im Sendeschutz gesperrt).
                         Exit 1, wenn etwas im Loader ist. ACHTUNG: Ob ein Handshake einen
                         Autostart-Timer des MCU-Bootloaders anhält, ist offen -> danach
                         normalen Lauf (mit Start) oder Drucker aus/an.
  --klipper DIR          Klipper-Checkout (Standard $KLIPPER_DIR oder ~/klipper)
  --dry-run              nur die geplanten Schritte und Frame-Bytes zeigen; öffnet
                         KEINEN Port, lädt kein Klipper, sendet nichts
  -v                     Klipper-Logmeldungen (DEBUG) zeigen

Exit: 0 = alles in der App (bzw. nichts im Loader), 1 = mindestens ein Gerät nicht
      gestartet/stumm, 2 = Aufruf-/Umgebungsfehler (nichts gesendet, falls vor dem
      Lauf erkannt), 3 = Gesamt-Timeout, 4 = Sendeschutz hat einen Frame abgelehnt
      (interner Fehler, dieser Frame NICHT gesendet).
Prozessende per os._exit (Klipper-Hintergrund-Threads blockieren sonst).
"""

import importlib.util
import logging
import os
import sys
import threading
import time

EXIT_OK, EXIT_NICHT_ALLES, EXIT_FEHLER, EXIT_TIMEOUT, EXIT_SENDESCHUTZ = 0, 1, 2, 3, 4
ROLLEN = {"main": "Main-MCU", "duese": "Düsen-MCU"}
HIER = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------
# Module laden (nachbau/bootstart.py, k2-identify.py, k2cfg.py)
# ---------------------------------------------------------------------
def nachbau_pfad(klipper_dir=None):
    kand = [os.environ.get("K2_NACHBAU_DIR"), os.path.join(HIER, "nachbau"),
            os.path.join(HIER, "..", "..", "nachbau")]
    if klipper_dir:
        kand.append(os.path.join(klipper_dir, "klippy", "extras", "k2_nachbau"))
    for d in kand:
        if d and os.path.isfile(os.path.join(d, "bootstart.py")) \
                and os.path.isfile(os.path.join(d, "serial485.py")):
            return os.path.abspath(d)
    return None


def lade_bootstart(klipper_dir=None):
    d = nachbau_pfad(klipper_dir)
    if d is None:
        raise ImportError("nachbau/bootstart.py + serial485.py nicht gefunden (K2_NACHBAU_DIR setzen)")
    if d not in sys.path:
        sys.path.insert(0, d)
    import bootstart  # noqa: E402
    return bootstart


def lade_datei(name, datei):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HIER, datei))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------
# Echte Umgebung (Ports, Klipper-identify) – Tests ersetzen sie
# ---------------------------------------------------------------------
class EchteUmgebung:
    def __init__(self, klipper_dir, identify_timeout, verbose=False):
        self.klipper_dir = klipper_dir
        self.identify_timeout = identify_timeout
        self.verbose = verbose
        self.k2id = lade_datei("k2_identify", "k2-identify.py")
        self.mods = None
        self.reactor = None
        self.uhr = time.monotonic
        self.schlafe = time.sleep

    def klipper_laden(self, argv):
        klippy_dir = os.environ.get("K2_IDENTIFY_KLIPPY") or os.path.join(self.klipper_dir, "klippy")
        try:
            self.mods = self.k2id.klipper_importieren(klippy_dir)
        except Exception as e:  # noqa: BLE001
            env_py = os.path.expanduser("~/klippy-env/bin/python")
            if (os.path.isfile(env_py) and not os.environ.get("K2_BOOTSTART_REEXEC")
                    and os.path.realpath(sys.executable) != os.path.realpath(env_py)):
                os.environ["K2_BOOTSTART_REEXEC"] = "1"
                sys.stdout.flush()
                os.execv(env_py, [env_py, os.path.abspath(__file__)] + argv[1:])
            raise RuntimeError("Klipper-Module nicht ladbar aus %s: %s (mit "
                               "~/klippy-env/bin/python starten)" % (klippy_dir, e))
        logging.basicConfig(level=logging.DEBUG if self.verbose else logging.CRITICAL,
                            stream=sys.stderr)
        self.reactor = self.mods[0].Reactor()

    def ausfuehren(self, fn):
        if self.reactor is None:
            return fn()
        erg = {}

        def _lauf(_eventtime):
            try:
                erg["r"] = fn()
            except BaseException as e:  # noqa: BLE001 – im Hauptthread neu werfen
                erg["x"] = e
            finally:
                self.reactor.end()
        self.reactor.register_callback(_lauf)
        self.reactor.run()
        if "x" in erg:
            raise erg["x"]
        return erg.get("r")

    def identify(self, port):
        return self.k2id.identify_versuch(self.mods, self.reactor, port, 230400,
                                          self.identify_timeout)

    @staticmethod
    def oeffne(port, baud):
        import serial  # pyserial (im klippy-env vorhanden)
        dev = serial.Serial(baudrate=baud, bytesize=8, parity="N", stopbits=1,
                            timeout=0.02, exclusive=True)
        dev.port = port
        dev.rts = True            # wie Klipper/k2-identify (kein DTR-Reset-Puls)
        dev.open()
        dev.reset_input_buffer()
        return dev

    def port_nutzer(self, port):
        return self.k2id.port_nutzer(port)

    @staticmethod
    def existiert(port):
        return os.path.exists(port)


# ---------------------------------------------------------------------
# Aufruf
# ---------------------------------------------------------------------
def args_parsen(argv):
    a = {"main": None, "duese": None, "rs485": None, "aus_config": None,
         "gruppen_aus": set(), "a1_runden": 3, "identify_timeout": 5.0, "warte": 0.0,
         "gesamt": 240.0, "nachkontrolle": True, "dry": False, "verbose": False,
         "erkennen": False,
         "klipper": os.environ.get("KLIPPER_DIR") or os.path.expanduser("~/klipper")}
    i = 0
    while i < len(argv):
        x = argv[i]

        def wert():
            if i + 1 >= len(argv):
                raise ValueError("%s braucht einen Wert" % x)
            return argv[i + 1]
        if x in ("-h", "--hilfe", "--help"):
            a["hilfe"] = True
        elif x == "-v":
            a["verbose"] = True
        elif x == "--dry-run":
            a["dry"] = True
        elif x == "--ohne-nachkontrolle":
            a["nachkontrolle"] = False
        elif x == "--nur-erkennen":
            a["erkennen"] = True
        elif x in ("--ohne-motoren", "--ohne-belts", "--ohne-cfs"):
            a["gruppen_aus"].add(x[7:])
        elif x in ("--main", "--duese", "--rs485", "--aus-config", "--klipper"):
            a[x[2:].replace("-", "_")] = wert()
            i += 1
        elif x == "--a1-runden":
            a["a1_runden"] = int(wert())
            i += 1
            if not 1 <= a["a1_runden"] <= 3:
                raise ValueError("--a1-runden 1..3")
        elif x in ("--identify-timeout", "--warte-auf-ports", "--gesamt-timeout"):
            key = {"--identify-timeout": "identify_timeout", "--warte-auf-ports": "warte",
                   "--gesamt-timeout": "gesamt"}[x]
            a[key] = float(wert())
            i += 1
        else:
            raise ValueError("unbekannte Option/Argument %s" % x)
        i += 1
    if a["identify_timeout"] <= 0 or a["gesamt"] <= 0 or a["warte"] < 0:
        raise ValueError("Zeiten müssen > 0 sein (--warte-auf-ports >= 0)")
    return a


def ports_aus_config(a, say):
    """--aus-config: fehlende Ports aus printer.cfg/k2-module.cfg (k2cfg.serials)."""
    k2cfg = lade_datei("k2cfg", "k2cfg.py")
    d = a["aus_config"]
    quellen = (("printer.cfg", "mcu", "main"), ("printer.cfg", "mcu nozzle_mcu", "duese"),
               ("k2-module.cfg", "k2_485", "rs485"))
    gelesen = {}
    for datei, sek, key in quellen:
        p = os.path.join(d, datei)
        if p not in gelesen:
            try:
                with open(p, encoding="utf-8") as fh:
                    gelesen[p] = k2cfg.serials(fh.read())
            except OSError as e:
                raise ValueError("--aus-config: %s nicht lesbar (%s)" % (p, e))
        v = gelesen[p].get(sek)
        if a[key]:
            continue
        if not v or k2cfg.PLACEHOLDER in v:
            say("Konfiguration: [%s] serial in %s fehlt/Platzhalter – %s ausgelassen"
                % (sek, datei, {"main": "Main-MCU", "duese": "Düsen-MCU", "rs485": "RS485"}[key]))
            continue
        a[key] = v
        say("Konfiguration: [%s] serial -> %s" % (sek, v))


def gruppen_waehlen(bs, a):
    namen = {"motoren": bs.GRUPPE_MOTOR, "belts": bs.GRUPPE_BELT, "cfs": bs.GRUPPE_CFS}
    aus = set(namen[n] for n in a["gruppen_aus"])
    return tuple(g for g in bs.GRUPPEN_REIHENFOLGE if g not in aus)


def trockenlauf(bs, a, say):
    say("TROCKENLAUF (--dry-run): kein Port geöffnet, nichts gesendet. Alle Frames unten "
        "haben den Bootstart-Sendeschutz passiert.")
    teile = [(r, a[r]) for r in ("main", "duese") if a[r]]
    if not (teile or a["rs485"]):
        teile = [("main", "<--main>"), ("duese", "<--duese>")]
        a = dict(a, rs485="<--rs485>")
    for rolle, port in teile:
        say()
        say("%s %s:" % (ROLLEN[rolle], port))
        for n, (text, b) in enumerate(bs.plan_uart(rolle, a["erkennen"]), 1):
            say("  %d. %s%s" % (n, text, ("\n       Bytes: %s" % b.hex(" ")) if b else ""))
    if a["rs485"]:
        say()
        say("RS485 %s (230400 Bd, 8N1):" % a["rs485"])
        for n, (text, b) in enumerate(bs.plan_rs485(gruppen_waehlen(bs, a),
                                                    nur_erkennen=a["erkennen"]), 1):
            say("  %d. %s%s" % (n, text, ("\n       Frame: %s" % b.hex(" ")) if b else ""))
        say()
        say("Whitelist (alles andere wird abgelehnt)%s:"
            % (" – im Modus --nur-erkennen NUR die drei A1-Zeilen" if a["erkennen"] else ""))
        for z in bs.whitelist_text():
            say("  - %s" % z)
    return EXIT_OK


def ablauf(bs, a, umg, say):
    """Echter Lauf. Rückgabe Exit-Code."""
    teile = [(r, a[r]) for r in ("main", "duese") if a[r]]
    ports = [p for _r, p in teile] + ([a["rs485"]] if a["rs485"] else [])
    real = {}
    for p in ports:
        real.setdefault(os.path.realpath(p), []).append(p)
    doppelt = [v for v in real.values() if len(v) > 1]
    if doppelt:
        say("FEHLER: derselbe Port mehrfach angegeben: %s – nichts gesendet" % doppelt)
        return EXIT_FEHLER
    # Vorprüfung ALLER Ports, bevor irgendetwas gesendet wird
    ende = umg.uhr() + a["warte"]
    while True:
        fehlt = [p for p in ports if not umg.existiert(p)]
        if not fehlt or umg.uhr() >= ende:
            break
        umg.schlafe(0.5)
    if fehlt:
        say("FEHLER: Port fehlt: %s – nichts gesendet" % ", ".join(fehlt))
        return EXIT_FEHLER
    for p in ports:
        nutzer = umg.port_nutzer(p)
        if nutzer:
            say("FEHLER: %s BELEGT durch %s – Klipper läuft? Erst 'sudo systemctl stop "
                "klipper'. Nichts gesendet." % (p, ", ".join("PID %d (%s)" % n for n in nutzer)))
            return EXIT_FEHLER
    erg_uart, erg_485 = [], None
    for rolle, port in teile:
        say()
        say("%s %s" % (ROLLEN[rolle], port))
        erg_uart.append(bs.uart_bootstart(rolle, port, umg.identify, umg.oeffne, say,
                                          schlafe=umg.schlafe, uhr=umg.uhr,
                                          nachkontrolle=a["nachkontrolle"],
                                          zeiten=a.get("zeiten"), nur_erkennen=a["erkennen"]))
    if a["rs485"]:
        say()
        say("RS485 %s (230400 Bd)" % a["rs485"])
        try:
            ser = umg.oeffne(a["rs485"], 230400)
        except Exception as e:  # noqa: BLE001
            say("  Port nicht zu öffnen: %s – nichts gesendet" % e)
            erg_485 = {"ok": False, "umgebung": True, "geraete": [], "crc_fehler": 0, "gesendet": []}
        else:
            bus = bs.Bus485Roh(ser, bs.Sitzung(nur_erkennen=a["erkennen"]), uhr=umg.uhr)
            try:
                erg_485 = bs.rs485_bootstart(bus, say, gruppen=gruppen_waehlen(bs, a),
                                             a1_runden=a["a1_runden"], schlafe=umg.schlafe,
                                             uhr=umg.uhr, nachkontrolle=a["nachkontrolle"],
                                             zeiten=a.get("zeiten"))
            finally:
                try:
                    ser.close()
                except Exception:  # noqa: BLE001
                    pass
            erg_485["gesendet"] = list(bus.gesendet)
    return zusammenfassung(bs, erg_uart, erg_485, say, a["erkennen"])


def zusammenfassung(bs, erg_uart, erg_485, say, erkennen=False):
    say()
    say("=" * 68)
    say("ZUSAMMENFASSUNG k2-bootstart")
    umgebung = fehler = False
    for e in erg_uart:
        say("  %-10s %s: %s – %s" % (ROLLEN[e["rolle"]], e["port"], e["status"].upper(), e["text"]))
        if e["gesendet"]:
            say("             gesendet @115200: %s" % " | ".join(b.hex(" ") for b in e["gesendet"]))
        umgebung |= e["status"] == "umgebung"
        fehler |= e["status"] in ("fehler", "loader")
    if erg_485 is not None:
        g = erg_485["geraete"]
        zaehl = {}
        for x in g:
            zaehl[(bs.GRUPPEN[x.gruppe]["name"], x.status)] = zaehl.get(
                (bs.GRUPPEN[x.gruppe]["name"], x.status), 0) + 1
        teile = ["%s %s: %d" % (n, s, c) for (n, s), c in sorted(zaehl.items())]
        say("  %-10s %s" % ("RS485", "; ".join(teile) if teile else "keine A1-Antwort (kein Gerät "
                            "im Loader; Motor-/Belt-Apps und adressierte CFS-Apps antworten nie auf A1)"))
        for x in g:
            if x.status in ("gestartet", "fehler", "fremd", "loader"):
                say("             %s%s: %s%s" % (x.name(), (" @0x%02x" % x.adresse) if x.adresse else "",
                                                 x.status, (" – %s" % x.grund) if x.grund else ""))
        say("             %d Frames gesendet (alle per Bootstart-Whitelist geprüft), CRC-Fehler "
            "empfangen: %d" % (len(erg_485.get("gesendet", [])), erg_485.get("crc_fehler", 0)))
        umgebung |= bool(erg_485.get("umgebung"))
        fehler |= not erg_485["ok"]
    code = EXIT_FEHLER if umgebung else (EXIT_NICHT_ALLES if fehler else EXIT_OK)
    say("ERGEBNIS: %s" % {EXIT_OK: "OK – nichts mehr im Loader (bzw. App lief schon)",
                          EXIT_NICHT_ALLES: ("ERKENNUNG: mindestens ein Gerät im Bootloader bzw. stumm "
                                             "(Exit 1) – nichts gestartet (--nur-erkennen)" if erkennen else
                                             "NICHT ALLES GESTARTET (Exit 1) – STOP, Rückweg zum Stock "
                                             "(PI-INBETRIEBNAHME Anhang C)"),
                          EXIT_FEHLER: "AUFRUF-/UMGEBUNGSFEHLER (Exit 2)"}[code])
    say("=" * 68)
    return code


def main(argv, umgebung=None, say=None):
    try:
        a = args_parsen(argv[1:])
    except ValueError as e:
        sys.stderr.write("FEHLER: %s (--hilfe)\n" % e)
        return EXIT_FEHLER
    if a.get("hilfe"):
        sys.stdout.write(__doc__ + "\n")
        return EXIT_OK
    if say is None:
        def say(msg=""):
            sys.stdout.write(msg + "\n")
            sys.stdout.flush()
    try:
        bs = lade_bootstart(a["klipper"])
    except ImportError as e:
        say("FEHLER: %s" % e)
        return EXIT_FEHLER
    say("k2-bootstart (Whitelist-Stand %d) – GERÄTETEST OFFEN, nur mit ausdrücklicher Freigabe.%s"
        % (bs.BOOTSTART_REV, " Modus --nur-erkennen (kein Start)." if a["erkennen"] else ""))
    if a["aus_config"]:
        try:
            ports_aus_config(a, say)
        except ValueError as e:
            say("FEHLER: %s" % e)
            return EXIT_FEHLER
        if not (a["main"] or a["duese"] or a["rs485"]):
            say("FEHLER: --aus-config %s: kein einziger serial-Pfad gesetzt (nur Platzhalter) – "
                "erst --main-serial/--duese-serial/--rs485-serial eintragen; nichts zu tun"
                % a["aus_config"])
            return EXIT_FEHLER
    if a["dry"]:
        try:
            return trockenlauf(bs, a, say)
        except bs.BootstartGuardError as e:
            say("INTERNER FEHLER (Sendeschutz im Plan): %s" % e)
            return EXIT_SENDESCHUTZ
    if not (a["main"] or a["duese"] or a["rs485"]):
        say("FEHLER: keine Ports (--main/--duese/--rs485 oder --aus-config) – nichts zu tun")
        return EXIT_FEHLER
    echt = umgebung is None
    if echt:
        wd = threading.Timer(a["gesamt"], _gesamt_timeout, args=(say, a["gesamt"]))
        wd.daemon = True
        wd.start()
        umgebung = EchteUmgebung(a["klipper"], a["identify_timeout"], a["verbose"])
        if a["main"] or a["duese"]:
            try:
                umgebung.klipper_laden(argv)
            except RuntimeError as e:
                say("FEHLER: %s – nichts gesendet" % e)
                return EXIT_FEHLER
    try:
        return umgebung.ausfuehren(lambda: ablauf(bs, a, umgebung, say))
    except bs.BootstartGuardError as e:
        say("INTERNER FEHLER: Sendeschutz hat einen Frame abgelehnt (NICHT gesendet): %s" % e)
        return EXIT_SENDESCHUTZ


def _gesamt_timeout(say, s):
    say("FEHLER: Gesamt-Timeout %.0f s überschritten – Abbruch." % s)
    sys.stdout.flush()
    os._exit(EXIT_TIMEOUT)


def _start():
    try:
        rc = main(sys.argv)
    except KeyboardInterrupt:
        rc = 130
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    finally:
        os._exit(rc)


if __name__ == "__main__":
    _start()
