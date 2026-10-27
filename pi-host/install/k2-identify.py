#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2-identify.py – welche K2-MCU hängt an welchem seriellen Port? (nur LESEND)

Läuft auf dem Pi mit dem Klipper-Python (greenlet/cffi/pyserial):

    ~/klippy-env/bin/python ./k2-identify.py                 # alle /dev/serial/by-id/*
    ~/klippy-env/bin/python ./k2-identify.py PORT [PORT ...] # nur diese Ports
    ~/klippy-env/bin/python ./k2-identify.py --baud 230400,250000,115200

Je Port und Baudrate wird über Klippers ``serialhdl`` NUR das Daten-Dictionary
abgefragt (Befehl ``identify``, wie beim Verbindungsaufbau von Klipper).
Es wird KEIN ``config``/``get_config``, KEIN ``reset``, KEINE stk500-Sequenz und
kein Arduino-/DTR-Reset gesendet; der Port wird wie bei Klipper geöffnet
(exklusiv, RTS gesetzt). Ein Port, den ein anderer Prozess offen hat (z. B. der
laufende Klipper), wird NICHT angefasst – es kommt eine Meldung.

Ausgabe je Port: Baud, MCU-Typ und Konstanten (MCU, CLOCK_FREQ, SERIAL_BAUD),
Firmware-Version, Anzahl Befehle/Antworten und die Erkennung
„Main-MCU“ (GD32F303RET6, Konstante MCU=gd32f303xe) bzw.
„Düsen-MCU“ (GD32F303CBT6, MCU=gd32f303xb). Abgleich zusätzlich mit den
bekannten Dictionaries (mcu-dicts/ im Paket) und pi-host/prtouch_mcu_formats.json.
Hinweis: Main- und Düsen-Firmware haben DENSELBEN Befehlssatz (75 Befehle inkl.
prtouch/z_align/Stepper) – getrennt wird über MCU-Typ, Pins und Build-Version.

Optionen:
  --baud LISTE          Baudraten, Komma-getrennt (Standard 230400)
  --timeout S           Wartezeit auf das Identify je Versuch (Standard 5 s)
  --gesamt-timeout S    harte Obergrenze für den ganzen Lauf (Standard 90 s)
  --klipper DIR         Klipper-Checkout (Standard $KLIPPER_DIR oder ~/klipper)
  --dicts DIR           Ordner mit bekannten *.dict.json (Standard: mcu-dicts/ im Paket)
  --ausgabe-rolle main|duese
                        stdout enthält NUR den Port dieser MCU (für install.sh),
                        alle Meldungen auf stderr; Exit 0 nur bei eindeutigem Treffer
  --ohne PORT           Port auslassen (mehrfach möglich). Ohne Port-Angabe wird der
                        RS485-Port aus [k2_485] serial in $CONFIG_DIR/k2-module.cfg
                        (Standard ~/printer_data/config) automatisch ausgelassen –
                        auf den RS485-Bus geht nie ein identify
  --ergebnis DATEI      eindeutige Treffer zusätzlich als Zeilen "main=PORT" /
                        "duese=PORT" in DATEI schreiben (für install.sh)
  -v                    Klipper-Logmeldungen (DEBUG) zeigen

Exit: 0 = mindestens eine MCU erkannt (bzw. Rolle eindeutig gefunden),
      1 = keine Antwort / Rolle nicht (eindeutig) gefunden, 2 = Aufruf-/Umgebungsfehler,
      3 = Gesamt-Timeout.
Prozessende immer per os._exit (Klipper-Hintergrund-Threads blockieren sonst).
"""

import glob
import json
import logging
import os
import sys
import threading
import time

ROLLEN = {
    "main": "Main-MCU (GD32F303RET6)",
    "duese": "Düsen-MCU (GD32F303CBT6)",
}
# MCU-Konstante im Dictionary -> Rolle (Main RET6, Düse CBT6)
MCU_TYP_ROLLE = {
    "gd32f303xe": "main",    # GD32F303RET6, 512 KB Flash, 64 Pins (PA..PD)
    "gd32f303xb": "duese",   # GD32F303CBT6, 128 KB Flash, 48 Pins (PA..PC)
}
# Fest eingebaute Referenz (Stand Live-FW 1.1.7.0 / F008), falls keine Dicts da sind
EINGEBAUT = [
    {"name": "eingebaut main F008", "rolle": "main", "mcu": "gd32f303xe",
     "version": "1.1.0.48-293-g493f9a0f-dirty-20241220_140327-ubuntu1804",
     "initial_pins": "!PB1,!PB2,!PB3,!PB4,!PA0,PB0,PA12,!PC8,!PC12", "befehle": None},
    {"name": "eingebaut duese F008", "rolle": "duese", "mcu": "gd32f303xb",
     "version": "1.1.0.48-293-g493f9a0f-dirty-20241220_143931-ubuntu1804",
     "initial_pins": "!PB1,!PB3,!PB8,!PB15", "befehle": None},
]
STANDARD_BAUD = [230400]
EXIT_OK, EXIT_NICHTS, EXIT_FEHLER, EXIT_TIMEOUT = 0, 1, 2, 3

KEINE_ANTWORT_HINWEIS = (
    "    Mögliche Ursachen (nichts gesendet außer identify):\n"
    "    - Kabel/Kontakt: Klemmen fest? 2-mm-Jumper wackelig? Drucker eingeschaltet?\n"
    "    - TX/RX gekreuzt? Konverter-RXD <- J22 Pin 10 (Board sendet), TXD -> Pin 4, GND -> Pin 2/8/11\n"
    "    - Pegel: J22 ist RS232 (±5,5 V) -> nur USB-RS232-Konverter (RS232-Klemmen, nicht A/B),\n"
    "      kein 3,3-V-TTL-Adapter; für TTL-Leitungen umgekehrt kein RS232-Konverter\n"
    "    - Baudrate: K2-MCUs laufen mit 230400 (andere mit --baud 230400,250000,... testen)\n"
    "    - Upper-Board/Display-Kabel noch an derselben Leitung? (nie beide gleichzeitig)")


def _out(msg=""):
    sys.stdout.write(msg + "\n")


class Ausgabe:
    """Meldungen: normal auf stdout, mit --ausgabe-rolle auf stderr."""

    def __init__(self, stream):
        self.stream = stream

    def __call__(self, msg=""):
        self.stream.write(msg + "\n")
        self.stream.flush()


# ---------------------------------------------------------------------
# Referenzen (bekannte Dictionaries)
# ---------------------------------------------------------------------
def _rolle_aus_name(name):
    n = name.lower()
    if "main" in n:
        return "main"
    if "duese" in n or "noz" in n or "nozzle" in n:
        return "duese"
    return None


def referenzen_laden(dict_dirs, formats_paths):
    """Liste bekannter MCU-Stände (eingebaut + Dicts + prtouch_mcu_formats.json)."""
    refs = [dict(r) for r in EINGEBAUT]
    for d in dict_dirs:
        if not d or not os.path.isdir(d):
            continue
        for p in sorted(glob.glob(os.path.join(d, "*.dict.json"))):
            rolle = _rolle_aus_name(os.path.basename(p))
            try:
                with open(p, encoding="utf-8") as fh:
                    data = json.load(fh)
            except (OSError, ValueError):
                continue
            cfg = data.get("config", {})
            refs.append({
                "name": os.path.basename(p)[:-len(".dict.json")],
                "rolle": rolle or MCU_TYP_ROLLE.get(str(cfg.get("MCU", "")).lower()),
                "mcu": str(cfg.get("MCU", "")).lower(),
                "version": data.get("version", ""),
                "initial_pins": cfg.get("INITIAL_PINS"),
                "befehle": frozenset(data.get("commands", {})),
            })
    for p in formats_paths:
        if not p or not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        for key, rolle in (("main", "main"), ("nozzle", "duese")):
            v = (data.get(key) or {}).get("version")
            if v:
                refs.append({"name": "prtouch_mcu_formats %s" % key, "rolle": rolle,
                             "mcu": None, "version": v, "initial_pins": None,
                             "befehle": None})
        break
    return refs


# ---------------------------------------------------------------------
# Auswertung eines Identify-Ergebnisses
# ---------------------------------------------------------------------
def auswerten(info, refs):
    """info: {'constants','version','commands','responses','enumerations'} ->
    {'rolle': 'main'|'duese'|None, 'gruende': [...], 'hinweise': [...], 'k2': bool}"""
    const = info.get("constants") or {}
    mcu = str(const.get("MCU", "")).lower()
    version = info.get("version") or ""
    cmds = set(info.get("commands") or [])
    names = set(c.split()[0] for c in cmds if c)
    enums = info.get("enumerations") or {}
    pins = enums.get("pin") or {}
    stimmen = {"main": [], "duese": []}
    hinweise = []

    r = MCU_TYP_ROLLE.get(mcu)
    if r:
        stimmen[r].append("MCU=%s" % mcu)
    elif mcu:
        hinweise.append("MCU-Typ %s ist kein K2-Typ (erwartet gd32f303xe/xb)" % mcu)
    else:
        hinweise.append("keine Konstante MCU im Dictionary")

    ver_treffer = sorted(set(x["name"] for x in refs
                             if x.get("version") and x["version"] == version))
    for x in refs:
        if x.get("version") and x["version"] == version and x.get("rolle"):
            g = "Firmware = bekannter Stand (%s)" % ", ".join(
                n for n in ver_treffer
                if any(y["name"] == n and y.get("rolle") == x["rolle"] for y in refs))
            if g not in stimmen[x["rolle"]]:
                stimmen[x["rolle"]].append(g)
    if not ver_treffer and version:
        hinweise.append("Firmware-Version unbekannt (keinem bekannten Dictionary gleich)")

    if pins:
        if any(str(k).startswith("PD") for k in pins):
            stimmen["main"].append("Pins PD* vorhanden (64-Pin-Gehäuse)")
        elif "PC0" in pins:
            stimmen["duese"].append("Pins nur PA..PC (48-Pin-Gehäuse)")

    ip = const.get("INITIAL_PINS")
    if ip:
        for x in refs:
            if x.get("initial_pins") and x["initial_pins"] == ip and x.get("rolle"):
                g = "INITIAL_PINS wie %s" % x["name"]
                if not any(s.startswith("INITIAL_PINS") for s in stimmen[x["rolle"]]):
                    stimmen[x["rolle"]].append(g)

    # Befehlssatz: nur Plausibilität (Main und Düse sind bei Creality identisch)
    k2_typisch = {"config_prtouch_pres", "config_z_align", "transparent_send",
                  "query_gpio_status"}
    k2 = k2_typisch <= names
    satz = [x["name"] for x in refs if x.get("befehle") and x["befehle"] == frozenset(cmds)]
    if satz:
        hinweise.append("Befehlssatz identisch mit %s (Main/Düse gleich, trennt nicht)"
                        % ", ".join(satz))
    elif cmds and not k2:
        hinweise.append("keine K2-Spezialbefehle (prtouch/z_align/transparent_send) – "
                        "Fremd-/Upstream-Firmware?")

    rolle = None
    if stimmen["main"] and not stimmen["duese"]:
        rolle = "main"
    elif stimmen["duese"] and not stimmen["main"]:
        rolle = "duese"
    elif stimmen["main"] and stimmen["duese"]:
        hinweise.append("WIDERSPRUCH: Merkmale für Main (%s) und Düse (%s)"
                        % ("; ".join(stimmen["main"]), "; ".join(stimmen["duese"])))
    return {"rolle": rolle, "gruende": stimmen.get(rolle, []) if rolle else [],
            "hinweise": hinweise, "k2": k2}


# ---------------------------------------------------------------------
# Port-Belegung (ohne Port zu öffnen)
# ---------------------------------------------------------------------
def port_nutzer(port):
    """[(pid, name)] anderer Prozesse, die das Gerät offen haben (/proc/*/fd)."""
    try:
        ziel = os.path.realpath(port)
    except OSError:
        return []
    eigen = os.getpid()
    res = []
    for d in glob.glob("/proc/[0-9]*"):
        try:
            pid = int(os.path.basename(d))
        except ValueError:
            continue
        if pid == eigen:
            continue
        try:
            fds = os.listdir(d + "/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                if os.readlink("%s/fd/%s" % (d, fd)) == ziel:
                    try:
                        with open(d + "/comm") as fh:
                            name = fh.read().strip()
                    except OSError:
                        name = "?"
                    res.append((pid, name))
                    break
            except OSError:
                continue
    return res


def ist_lock_fehler(exc):
    t = str(exc).lower()
    return "lock" in t or "busy" in t or "resource temporarily unavailable" in t


# ---------------------------------------------------------------------
# Klipper-Zugriff
# ---------------------------------------------------------------------
class _TimeoutCompletion:
    def __init__(self, comp, reactor, timeout):
        self._c, self._r, self._t = comp, reactor, timeout

    def wait(self, waketime=None, waketime_result=None):
        # Klipper wartet im Verbindungsaufbau fest 5 s – hier der eigene Wert
        return self._c.wait(self._r.monotonic() + self._t, waketime_result)

    def __getattr__(self, name):
        return getattr(self._c, name)


class _TimeoutReactor:
    """Reaktor-Hülle: nur das Warten auf das Identify-Ergebnis bekommt den
    eigenen Timeout, alles andere geht unverändert an Klippers Reaktor."""

    def __init__(self, reactor, timeout):
        self._r, self._t = reactor, timeout

    def register_callback(self, callback, *args):
        return _TimeoutCompletion(self._r.register_callback(callback, *args),
                                  self._r, self._t)

    def __getattr__(self, name):
        return getattr(self._r, name)


def klipper_importieren(klippy_dir):
    if klippy_dir not in sys.path:
        sys.path.insert(0, klippy_dir)
    import serial  # noqa: F401  (pyserial)
    import reactor as reactor_mod
    import serialhdl
    return reactor_mod, serialhdl, serial


def identify_versuch(mods, reactor, port, baud, timeout):
    """Ein Versuch an einem Port/einer Baudrate. Gibt Ergebnis-dict zurück:
    status: 'antwort' | 'keine_antwort' | 'belegt' | 'fehler'."""
    _reactor_mod, serialhdl, serial = mods
    try:
        dev = serial.Serial(baudrate=baud, timeout=0, exclusive=True)
        dev.port = port
        dev.rts = True            # wie Klipper connect_uart (kein DTR-/Reset-Puls)
        dev.open()
    except (OSError, IOError, serial.SerialException) as e:
        if ist_lock_fehler(e):
            return {"status": "belegt", "text": "Port gesperrt (%s)" % e}
        return {"status": "fehler", "text": "Port nicht zu öffnen: %s" % e}
    reader = serialhdl.SerialReader(_TimeoutReactor(reactor, timeout))
    t0 = time.monotonic()
    try:
        ok = reader._start_session(dev)   # sendet nur "identify offset=.. count=.."
    except Exception as e:  # noqa: BLE001 – Klipper-interne Fehler melden, nicht abstürzen
        try:
            reader.disconnect()
        except Exception:  # noqa: BLE001
            pass
        return {"status": "fehler", "text": "Fehler beim Identify: %s" % e}
    dauer = time.monotonic() - t0
    if not ok:
        return {"status": "keine_antwort", "dauer": dauer}
    mp = reader.get_msgparser()
    version, build = mp.get_version_info()
    cmds, resps = [], []
    for _msgid, typ, fmt in mp.get_messages():
        if typ == "command":
            cmds.append(fmt)
        elif typ == "response":
            resps.append(fmt)
    info = {"status": "antwort", "dauer": dauer, "version": version,
            "build_versions": build, "constants": mp.get_constants(),
            "enumerations": mp.get_enumerations(), "commands": cmds,
            "responses": resps}
    reader.disconnect()
    return info


# ---------------------------------------------------------------------
# Ablauf
# ---------------------------------------------------------------------
def by_id_ports():
    d = os.environ.get("K2_IDENTIFY_BYID_DIR", "/dev/serial/by-id")
    if not os.path.isdir(d):
        return d, []
    return d, sorted(os.path.join(d, n) for n in os.listdir(d))


def rs485_port_aus_config(cfg_dir):
    """serial-Wert von [k2_485] aus k2-module.cfg (None bei Platzhalter/fehlend)."""
    p = os.path.join(cfg_dir, "k2-module.cfg")
    try:
        with open(p, encoding="utf-8") as fh:
            zeilen = fh.read().splitlines()
    except OSError:
        return None
    sec = None
    for z in zeilen:
        t = z.strip()
        if t.startswith("[") and t.endswith("]"):
            sec = t[1:-1].strip()
        elif sec == "k2_485" and (t.startswith("serial:") or t.startswith("serial=")):
            v = t.split(t[6], 1)[1].strip()
            return None if (not v or "ANNAHME" in v) else v
    return None


def ports_filtern(ports, ohne, say):
    weg = set()
    for o in ohne:
        try:
            weg.add(os.path.realpath(o))
        except OSError:
            pass
    res = []
    for p in ports:
        if os.path.realpath(p) in weg:
            say("Ausgelassen (--ohne bzw. RS485-Port aus k2-module.cfg): %s" % p)
        else:
            res.append(p)
    return res


def zeige_antwort(say, info, bew):
    c = info.get("constants") or {}
    say("    MCU:        %s   CLOCK_FREQ %s   SERIAL_BAUD %s"
        % (c.get("MCU", "?"), c.get("CLOCK_FREQ", "?"), c.get("SERIAL_BAUD", "?")))
    say("    Firmware:   %s" % (info.get("version") or "?"))
    names = set(x.split()[0] for x in info.get("commands", []) if x)
    say("    Befehle:    %d Befehle, %d Antworten (prtouch %s, z_align %s, Stepper %s)" % (
        len(info.get("commands", [])), len(info.get("responses", [])),
        "ja" if any(n.startswith(("config_prtouch", "start_prtouch")) for n in names) else "nein",
        "ja" if "config_z_align" in names else "nein",
        "ja" if "config_stepper" in names else "nein"))
    if bew["rolle"]:
        say("    ERKENNUNG:  %s  [%s]" % (ROLLEN[bew["rolle"]].upper(), "; ".join(bew["gruende"])))
    else:
        say("    ERKENNUNG:  UNKLAR (weder eindeutig Main noch Düse)")
    for h in bew["hinweise"]:
        say("    Hinweis:    %s" % h)


def ablauf(args, mods, reactor, say, refs):
    ports = args["ports"]
    ergebnisse = []   # (port, baud, status, info, bew)
    for port in ports:
        say()
        say("Port: %s" % port)
        if os.path.islink(port):
            say("  -> %s" % os.path.realpath(port))
        if not os.path.exists(port):
            say("  FEHLT (Gerät nicht vorhanden)")
            ergebnisse.append((port, None, "fehlt", None, None))
            continue
        nutzer = port_nutzer(port)
        if nutzer:
            say("  BELEGT durch %s – NICHT angefasst. Klipper läuft? Erst "
                "'sudo systemctl stop klipper', dann erneut."
                % ", ".join("PID %d (%s)" % n for n in nutzer))
            ergebnisse.append((port, None, "belegt", None, None))
            continue
        for baud in args["baud"]:
            info = identify_versuch(mods, reactor, port, baud, args["timeout"])
            st = info["status"]
            if st == "antwort":
                bew = auswerten(info, refs)
                say("  Baud %d: ANTWORT (%.1f s)" % (baud, info.get("dauer", 0)))
                zeige_antwort(say, info, bew)
                ergebnisse.append((port, baud, st, info, bew))
                break
            if st == "belegt":
                say("  Baud %d: BELEGT – %s. Klipper läuft? Erst stoppen, dann erneut."
                    % (baud, info["text"]))
                ergebnisse.append((port, baud, st, info, None))
                break
            if st == "fehler":
                say("  Baud %d: FEHLER – %s" % (baud, info["text"]))
                ergebnisse.append((port, baud, st, info, None))
                break
            say("  Baud %d: keine Antwort (%.1f s)" % (baud, args["timeout"]))
            ergebnisse.append((port, baud, st, info, None))
        else:
            say(KEINE_ANTWORT_HINWEIS)
    return ergebnisse


def zusammenfassung(say, ergebnisse):
    treffer = {"main": [], "duese": []}
    say()
    say("=" * 68)
    say("ZUSAMMENFASSUNG")
    for port, baud, st, _info, bew in ergebnisse:
        if st == "antwort":
            r = bew["rolle"]
            if r:
                treffer[r].append(port)
            say("  %-10s %s @%s  %s" % ("ANTWORT", port, baud, ROLLEN[r] if r else "unklar"))
    for port in sorted(set(e[0] for e in ergebnisse)):
        sts = [e[2] for e in ergebnisse if e[0] == port]
        if "antwort" in sts:
            continue
        st = "belegt" if "belegt" in sts else ("fehlt" if "fehlt" in sts else
                                               ("fehler" if "fehler" in sts else "keine Antwort"))
        say("  %-10s %s" % (st.upper() if st != "keine Antwort" else "STUMM", port))
    say()
    teile = []
    for r, opt in (("main", "--main-serial"), ("duese", "--duese-serial")):
        if len(treffer[r]) == 1:
            teile.append("%s %s" % (opt, treffer[r][0]))
        elif len(treffer[r]) > 1:
            say("  ACHTUNG: %s an mehreren Ports (%s) – kein Vorschlag"
                % (ROLLEN[r], ", ".join(treffer[r])))
    if teile:
        say("VORSCHLAG (nichts eingetragen):")
        say("  ./install.sh %s [übrige Optionen]" % " ".join(teile))
    else:
        say("Kein Vorschlag (keine MCU eindeutig erkannt).")
    say("=" * 68)
    return treffer


def args_parsen(argv):
    a = {"ports": [], "baud": list(STANDARD_BAUD), "timeout": 5.0, "gesamt": 90.0,
         "klipper": os.environ.get("KLIPPER_DIR") or os.path.expanduser("~/klipper"),
         "dicts": None, "rolle": None, "verbose": False, "ergebnis": None, "ohne": []}
    i = 0
    while i < len(argv):
        x = argv[i]
        def wert():
            if i + 1 >= len(argv):
                raise ValueError("%s braucht einen Wert" % x)
            return argv[i + 1]
        if x in ("-h", "--hilfe", "--help"):
            a["hilfe"] = True
            i += 1
            continue
        if x == "-v":
            a["verbose"] = True
            i += 1
            continue
        if x == "--baud":
            a["baud"] = [int(b) for b in wert().split(",") if b.strip()]
            if not a["baud"] or min(a["baud"]) <= 0:
                raise ValueError("--baud: ungültige Liste")
        elif x == "--timeout":
            a["timeout"] = float(wert())
        elif x == "--gesamt-timeout":
            a["gesamt"] = float(wert())
        elif x == "--klipper":
            a["klipper"] = wert()
        elif x == "--dicts":
            a["dicts"] = wert()
        elif x == "--ergebnis":
            a["ergebnis"] = wert()
        elif x == "--ohne":
            a["ohne"].append(wert())
        elif x == "--ausgabe-rolle":
            a["rolle"] = wert()
            if a["rolle"] not in ROLLEN:
                raise ValueError("--ausgabe-rolle main|duese")
        elif x.startswith("-"):
            raise ValueError("unbekannte Option %s" % x)
        else:
            a["ports"].append(x)
            i += 1
            continue
        i += 2
    if a["timeout"] <= 0 or a["gesamt"] <= 0:
        raise ValueError("Timeouts müssen > 0 sein")
    return a


def main(argv):
    try:
        args = args_parsen(argv[1:])
    except ValueError as e:
        sys.stderr.write("FEHLER: %s (--hilfe)\n" % e)
        return EXIT_FEHLER
    if args.get("hilfe"):
        _out(__doc__)
        return EXIT_OK
    say = Ausgabe(sys.stderr if args["rolle"] else sys.stdout)

    # Gesamt-Timeout: harte Grenze, auch wenn Klipper-Threads hängen
    def _gesamt():
        say("FEHLER: Gesamt-Timeout %.0f s überschritten – Abbruch (nichts verändert)."
            % args["gesamt"])
        sys.stdout.flush()
        os._exit(EXIT_TIMEOUT)
    wd = threading.Timer(args["gesamt"], _gesamt)
    wd.daemon = True
    wd.start()

    hier = os.path.dirname(os.path.abspath(__file__))
    if not args["ports"]:
        byid, args["ports"] = by_id_ports()
        if not args["ports"]:
            say("Keine Ports unter %s – Konverter/Adapter angesteckt? (ls -l /dev/serial/by-id/)"
                % byid)
            return EXIT_NICHTS
        say("Ports unter %s: %d" % (byid, len(args["ports"])))
        cfg_dir = os.environ.get("CONFIG_DIR") or os.path.expanduser("~/printer_data/config")
        r485 = rs485_port_aus_config(cfg_dir)
        if r485:
            args["ohne"].append(r485)
    if args["ohne"]:
        args["ports"] = ports_filtern(args["ports"], args["ohne"], say)
        if not args["ports"]:
            say("Keine Ports übrig (alle ausgelassen).")
            return EXIT_NICHTS

    klippy_dir = os.environ.get("K2_IDENTIFY_KLIPPY") or os.path.join(args["klipper"], "klippy")
    try:
        mods = klipper_importieren(klippy_dir)
    except Exception as e:  # noqa: BLE001
        env_py = os.path.expanduser("~/klippy-env/bin/python")
        if (os.path.isfile(env_py) and not os.environ.get("K2_IDENTIFY_REEXEC")
                and os.path.realpath(sys.executable) != os.path.realpath(env_py)):
            say("Hinweis: Klipper-Module fehlen in %s – neu gestartet mit %s"
                % (sys.executable, env_py))
            os.environ["K2_IDENTIFY_REEXEC"] = "1"
            sys.stdout.flush()
            os.execv(env_py, [env_py, os.path.abspath(__file__)] + argv[1:])
        say("FEHLER: Klipper-Module nicht ladbar aus %s: %s" % (klippy_dir, e))
        say("  Mit dem Klipper-Python starten: ~/klippy-env/bin/python %s"
            % os.path.basename(__file__))
        return EXIT_FEHLER
    logging.basicConfig(level=logging.DEBUG if args["verbose"] else logging.CRITICAL,
                        stream=sys.stderr)

    dict_dirs = [args["dicts"], os.environ.get("K2_MCU_DICT_DIR"),
                 os.path.join(hier, "mcu-dicts"),
                 os.path.join(hier, "..", "..", "lokal", "mcu-dicts")]
    formats = [os.path.join(hier, "pi-host", "prtouch_mcu_formats.json"),
               os.path.join(hier, "..", "prtouch_mcu_formats.json")]
    refs = referenzen_laden(dict_dirs, formats)
    say("k2-identify: nur 'identify' wird gesendet; Baud %s, Timeout %.1f s/Versuch, "
        "Referenzen: %d" % (",".join(map(str, args["baud"])), args["timeout"], len(refs)))

    reactor_mod = mods[0]
    reactor = reactor_mod.Reactor()
    erg = {}

    def _lauf(eventtime):
        try:
            erg["e"] = ablauf(args, mods, reactor, say, refs)
        except Exception as e:  # noqa: BLE001
            erg["x"] = e
        finally:
            reactor.end()
    reactor.register_callback(_lauf)
    reactor.run()
    if "x" in erg:
        say("FEHLER: %s: %s" % (type(erg["x"]).__name__, erg["x"]))
        return EXIT_FEHLER
    treffer = zusammenfassung(say, erg.get("e", []))
    if args["ergebnis"]:
        with open(args["ergebnis"], "w", encoding="utf-8") as fh:
            for r in ("main", "duese"):
                if len(treffer[r]) == 1:
                    fh.write("%s=%s\n" % (r, treffer[r][0]))
    if args["rolle"]:
        t = treffer[args["rolle"]]
        if len(t) == 1:
            _out(t[0])
            return EXIT_OK
        say("Rolle %s: %s" % (args["rolle"], "nicht gefunden" if not t else "mehrdeutig"))
        return EXIT_NICHTS
    return EXIT_OK if (treffer["main"] or treffer["duese"]
                       or any(e[2] == "antwort" for e in erg.get("e", []))) else EXIT_NICHTS


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
