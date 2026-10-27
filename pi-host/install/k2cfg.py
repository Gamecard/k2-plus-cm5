#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2cfg.py – Hilfswerkzeug für install.sh (läuft auf dem Pi, nur Standardbibliothek).

Unterbefehle:

  merge NEU AUS [--alt ALT] [--set ABSCHNITT=WERT ...] [--save-config-aus-paket]
      Schreibt NEU (Paket-Datei) nach AUS und übernimmt dabei
        * die ``serial:``-Zeilen der Abschnitte [mcu], [mcu nozzle_mcu], [k2_485]:
          Vorrang hat --set, sonst der Wert aus ALT (bereits installierte Datei),
          sofern er kein Platzhalter (``ANNAHME``) ist;
        * die Zeile ``material_database:`` im Abschnitt [k2_box] (Pfad der
          Creality-Materialdatenbank) und die Freigaben ``allow_write``/
          ``allow_move`` ([k2_box], [k2_motor], [k2_prtouch], [k2_belt mdlx/mdly],
          ``variable_allow_move`` in [gcode_macro _PI_FREIGABE]) samt [k2_motor]
          ``auto_init``/``stall_monitor``: steht die
          Zeile in NEU und ALT aktiv und weicht ALT ab (von Hand angepasst),
          bleibt der Wert aus ALT (KEEP_OPTIONS); eine in ALT auskommentierte/
          fehlende Zeile übernimmt den Paketwert;
        * den SAVE_CONFIG-Block aus ALT (Kalibrierwerte), wenn ALT eine
          K2-Konfiguration ist und der Block abweicht – außer mit
          --save-config-aus-paket.
      Jede Übernahme wird auf stdout gemeldet.

  setze DATEI ABSCHNITT OPTION WERT
      Setzt die erste aktive Zeile ``OPTION:`` im Abschnitt [ABSCHNITT] (nur
      Hauptteil, nicht SAVE_CONFIG) auf WERT, z. B. für install.sh
      --bewegung-gesperrt: ``setze homing.cfg "gcode_macro _PI_FREIGABE"
      variable_allow_move False``. Exit 0 = gesetzt oder schon gleich,
      3 = Option im Abschnitt nicht gefunden (Datei unverändert).

  platzhalter DATEI ...
      Listet Abschnitte, deren ``serial:`` noch ein Platzhalter ist, mit
      Hinweis auf den Adapter (Main-MCU: USB-RS232-Konverter an J22).
      Exit 0 = keine Platzhalter, 3 = Platzhalter vorhanden.

  stock-pruefen STOCK [--referenz PI_CFG]
      Liest NUR den SAVE_CONFIG-Block der Stock-printer.cfg STOCK und meldet je
      Abschnitt, ob er auf den Pi übernommen werden kann (Abbildung s. u.).
      PI_CFG (Pi-printer.cfg samt [include]-Dateien) liefert die Grenzen für
      cut_pos_x und den Vergleich mit [bed_mesh]. Schreibt nichts.

  stock-uebernehmen STOCK ZIEL [--ueberschreiben]
      Übernimmt die abbildbaren Abschnitte aus STOCK in den SAVE_CONFIG-Block
      von ZIEL (Pi-printer.cfg; Block wird bei Bedarf angelegt). Ein im
      Ziel-Block vorhandener gleichnamiger Abschnitt mit ANDEREN Werten bleibt,
      außer mit --ueberschreiben (dann ganz ersetzt). Gleiche Werte -> keine
      Änderung. Jede Entscheidung wird gemeldet, letzte Zeile
      ``  stock ERGEBNIS: neu=.. gleich=.. behalten=.. ueberschrieben=.. ausgelassen=..``.

      Abbildung Stock -> Pi (Upstream-Klipper 9cb9975):
        [box] cut_pos_x        -> [k2_box] cut_pos_x (in [check_cut_pos_x_min,
                                  check_cut_pos_x_max] und [x_min_wide, 0])
        [input_shaper]         -> [input_shaper] (nur Upstream-Optionen/-Typen)
        [bed_mesh <name>]      -> [bed_mesh <name>] (Profil Version 1, alle zehn
                                  Upstream-Felder, Punkte = y_count x x_count)
        [auto_addr]            -> NIE (Geräte-Kennungen; Werte werden nicht gezeigt)
        [heater_bed], [extruder], [prtouch*] und alles Unbekannte -> ausgelassen
                                  (nur Abschnitts-/Optionsnamen gemeldet)
      Exit (beide): 0 = mindestens ein Abschnitt übernehmbar, 2 = Datei/Block
      unlesbar oder beschädigt, 3 = nichts übernehmbar (ZIEL unverändert).

Anschluss (PI-ANSCHLUSS-LOWERBOARD.md): die Main-MCU spricht über RS232
(J22 Pin 10 TX / Pin 4 RX, GND 2/8/11) -> USB-RS232-Konverter (FTDI, by-id
``usb-FTDI_…-if00-port0``), 230400 Baud. Welcher by-id-Port welche MCU ist,
zeigt ``k2-identify.py`` (nur lesend).
"""

import collections
import glob
import math
import os
import re
import sys

SERIAL_SECTIONS = ("mcu", "mcu nozzle_mcu", "k2_485")
SAVE_MARKER = "#*# <---------------------- SAVE_CONFIG ---------------------->"
PLACEHOLDER = "ANNAHME"

_SEC_RE = re.compile(r"^\[([^\]]+)\]\s*$")
_SERIAL_RE = re.compile(r"^(serial\s*[:=]\s*)(\S.*?)\s*$")
# Hinweise je serial-Abschnitt (Platzhalter-Liste, Prüfung von --set-Werten)
SERIAL_HINWEIS = {
    "mcu": "Main-MCU: Variante B Pi-UART4 /dev/ttyAMA4 (Overlay uart4-pi5); "
           "Variante A USB-RS232-Konverter an J22 (by-id usb-FTDI_...); "
           "Port zeigt ./k2-identify.py",
    "mcu nozzle_mcu": "Düsen-MCU: Variante B Pi-UART2 /dev/ttyAMA2 (Overlay uart2-pi5); "
                      "./k2-identify.py erkennt sie",
    "k2_485": "USB-RS485-Adapter (CFS/Motoren/Belt), /dev/serial/by-id/usb-...",
}
# Weitere Optionen, deren Wert bei Wiederholung aus ALT erhalten bleibt: Pfad der
# Material-DB und die Freigaben (allow_*), die nach der Inbetriebnahme von Hand
# gesetzt werden – sonst setzte ein erneuter install.sh-Lauf sie auf den Paketstand
# zurück (und der Drucker könnte nicht mehr referenzieren/drucken).
KEEP_OPTIONS = (("k2_box", "material_database"),
                ("k2_box", "allow_write"), ("k2_box", "allow_move"),
                ("k2_motor", "allow_write"), ("k2_motor", "auto_init"),
                ("k2_motor", "stall_monitor"), ("k2_prtouch", "allow_move"),
                ("gcode_macro _PI_FREIGABE", "variable_allow_move"),
                ("k2_belt mdlx", "allow_write"), ("k2_belt mdlx", "allow_move"),
                ("k2_belt mdly", "allow_write"), ("k2_belt mdly", "allow_move"))


def _opt_re(key):
    return re.compile(r"^(" + re.escape(key) + r"\s*[:=]\s*)(\S.*?)\s*$")


def options(text, wanted=KEEP_OPTIONS):
    """{(abschnitt, schlüssel): wert} der ersten aktiven Zeile (nur Hauptteil)."""
    body, _ = split_save_config(text)
    res = {}
    sec = None
    for line in body.splitlines():
        m = _SEC_RE.match(line)
        if m:
            sec = m.group(1).strip()
            continue
        for wsec, key in wanted:
            if sec == wsec and (wsec, key) not in res:
                m = _opt_re(key).match(line)
                if m:
                    res[(wsec, key)] = m.group(2)
    return res


def set_options(text, values):
    """Ersetzt je (abschnitt, schlüssel) die erste aktive Zeile; (text, geändert)."""
    body, save = split_save_config(text)
    out = []
    sec = None
    done = set()
    changed = []
    for line in body.splitlines(True):
        m = _SEC_RE.match(line.rstrip("\n"))
        if m:
            sec = m.group(1).strip()
        else:
            for (wsec, key), new in values.items():
                if sec != wsec or (wsec, key) in done:
                    continue
                m2 = _opt_re(key).match(line.rstrip("\n"))
                if m2:
                    done.add((wsec, key))
                    if m2.group(2) != new:
                        nl = "\n" if line.endswith("\n") else ""
                        line = m2.group(1) + new + nl
                        changed.append((wsec, key))
                    break
        out.append(line)
    return "".join(out) + save, changed


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def split_save_config(text):
    """(Hauptteil, SAVE_CONFIG-Block oder '')."""
    idx = text.find(SAVE_MARKER)
    if idx < 0:
        return text, ""
    return text[:idx], text[idx:]


def serials(text):
    """{abschnitt: wert} der aktiven serial-Zeilen (nur Hauptteil)."""
    body, _ = split_save_config(text)
    res = {}
    sec = None
    for line in body.splitlines():
        m = _SEC_RE.match(line)
        if m:
            sec = m.group(1).strip()
            continue
        if sec in SERIAL_SECTIONS:
            m = _SERIAL_RE.match(line)
            if m and sec not in res:
                res[sec] = m.group(2)
    return res


def set_serials(text, values):
    """Ersetzt die erste serial-Zeile je Abschnitt; gibt (text, geändert) zurück."""
    body, save = split_save_config(text)
    out = []
    sec = None
    done = set()
    changed = []
    for line in body.splitlines(True):
        m = _SEC_RE.match(line.rstrip("\n"))
        if m:
            sec = m.group(1).strip()
        elif sec in values and sec not in done:
            m2 = _SERIAL_RE.match(line.rstrip("\n"))
            if m2:
                done.add(sec)
                new = values[sec]
                if m2.group(2) != new:
                    nl = "\n" if line.endswith("\n") else ""
                    line = m2.group(1) + new + nl
                    changed.append(sec)
        out.append(line)
    return "".join(out) + save, changed


def serial_warnungen(sec, val):
    """Plausibilitäts-Hinweise zu einem serial-Wert (kein Abbruch)."""
    res = []
    if not val.startswith("/dev/"):
        res.append("[%s] serial %s ist kein /dev/-Pfad" % (sec, val))
    elif re.match(r"/dev/ttyAMA\d+$", val) and sec != "k2_485":
        pass        # Pi-UART (Variante B): fester Name über das DT-Overlay
    elif not val.startswith("/dev/serial/by-id/"):
        res.append("[%s] serial %s ist kein /dev/serial/by-id/-Pfad "
                   "(ttyUSBn kann sich beim Neustart ändern)" % (sec, val))
    if sec == "mcu" and val.startswith("/dev/serial/by-id/") and "FTDI" not in val:
        res.append("[mcu] %s ist kein FTDI-Gerät – die Main-MCU hängt am USB-RS232-"
                   "Konverter (usb-FTDI_...); mit ./k2-identify.py prüfen" % val)
    return res


def is_k2_config(text):
    return "[k2_prtouch]" in text or "[k2_485]" in text


def cmd_merge(args):
    if len(args) < 2:
        raise SystemExit("merge: NEU AUS fehlen")
    neu, aus = args[0], args[1]
    alt = None
    sets = {}
    save_aus_paket = False
    i = 2
    while i < len(args):
        a = args[i]
        if a == "--alt":
            alt = args[i + 1]
            i += 2
        elif a == "--set":
            k, sep, v = args[i + 1].partition("=")
            if not sep or k not in SERIAL_SECTIONS or not v.strip():
                raise SystemExit("merge: --set ABSCHNITT=WERT ungültig: %r" % args[i + 1])
            sets[k] = v.strip()
            i += 2
        elif a == "--save-config-aus-paket":
            save_aus_paket = True
            i += 1
        else:
            raise SystemExit("merge: unbekannte Option %r" % a)

    text = _read(neu)
    alt_text = _read(alt) if alt and os.path.isfile(alt) else None
    have = serials(text)

    values = {}
    for sec in have:
        if sec in sets:
            values[sec] = sets[sec]
            src = "Option"
            for w in serial_warnungen(sec, values[sec]):
                print("  WARN  %s" % w)
        elif alt_text is not None:
            old = serials(alt_text).get(sec)
            if old and PLACEHOLDER not in old:
                values[sec] = old
                src = "bisherige Datei"
            else:
                continue
        else:
            continue
        print("  serial [%s] <- %s (%s)" % (sec, values[sec], src))
    text, _changed = set_serials(text, values)

    if alt_text is not None:
        neu_opts = options(text)
        keep = {}
        for k, old in options(alt_text).items():
            if k in neu_opts and old != neu_opts[k] and PLACEHOLDER not in old:
                keep[k] = old
                print("  [%s] %s <- %s (bisherige Datei, Paket: %s)"
                      % (k[0], k[1], old, neu_opts[k]))
        text, _changed = set_options(text, keep)

    if alt_text is not None and is_k2_config(alt_text):
        _b, save_alt = split_save_config(alt_text)
        body, save_neu = split_save_config(text)
        if save_alt and save_alt != save_neu:
            if save_aus_paket:
                print("  SAVE_CONFIG: Block aus dem PAKET übernommen "
                      "(bisherige Kalibrierwerte verworfen, steht im Backup)")
            else:
                text = body + save_alt
                print("  SAVE_CONFIG: bisherigen Block (Kalibrierwerte) BEHALTEN")

    tmp = aus + ".k2tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, aus)
    return 0


def cmd_setze(args):
    if len(args) != 4:
        raise SystemExit("setze: DATEI ABSCHNITT OPTION WERT")
    pfad, sec, key, wert = args
    text, changed = set_options(_read(pfad), {(sec, key): wert})
    if (sec, key) not in options(text, ((sec, key),)):
        print("  [%s] %s nicht gefunden – %s unverändert" % (sec, key, pfad))
        return 3
    if changed:
        tmp = pfad + ".k2tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, pfad)
        print("  [%s] %s <- %s" % (sec, key, wert))
    else:
        print("  [%s] %s = %s (unverändert)" % (sec, key, wert))
    return 0


def cmd_platzhalter(args):
    found = False
    for path in args:
        if not os.path.isfile(path):
            continue
        for sec, val in serials(_read(path)).items():
            if PLACEHOLDER in val:
                found = True
                print("  %s  [%s]  serial: %s" % (os.path.basename(path), sec, val))
                if sec in SERIAL_HINWEIS:
                    print("      -> %s" % SERIAL_HINWEIS[sec])
    return 3 if found else 0


# =====================================================================
# Kalibrierwerte aus dem SAVE_CONFIG-Block einer Stock-printer.cfg
# =====================================================================
# Regeln für den Block wie Upstream klippy/configfile.py (ConfigAutoSave):
# Kopf = SAVE_MARKER + "DO NOT EDIT"-Zeile + "#*#", danach nur Zeilen mit
# "#*# " (bzw. genau "#*#"); alles nach '#' im Inhalt ist Kommentar; beim
# Laden verwirft Upstream SAVE_CONFIG-Optionen, die auch im Hauptteil oder
# einem [include] stehen (_strip_duplicates), und SAVE_CONFIG bricht bei
# solchen Include-Werten ab (_disallow_include_conflicts).
AUTOSAVE_KOPF = (SAVE_MARKER,
                 "#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.",
                 "#*#")
# Upstream klippy/extras/shaper_defs.py INPUT_SHAPERS: Name -> max_damping_ratio
UPSTREAM_SHAPER = {"zv": 0.99, "mzv": 0.99, "zvd": 0.99, "ei": 0.4,
                   "2hump_ei": 0.3, "3hump_ei": 0.2}
SHAPER_OPTIONEN = ("shaper_type", "shaper_type_x", "shaper_type_y",
                   "shaper_freq_x", "shaper_freq_y",
                   "damping_ratio_x", "damping_ratio_y")
SHAPER_TYP_DEFAULT = "mzv"             # Upstream input_shaper.py
# Upstream klippy/extras/bed_mesh.py PROFILE_VERSION / PROFILE_OPTIONS
# (Creality-Fork: identisch, bed_mesh.py dort Zeile 11-16)
MESH_VERSION = 1
MESH_FELDER = collections.OrderedDict((
    ("min_x", float), ("max_x", float), ("min_y", float), ("max_y", float),
    ("x_count", int), ("y_count", int), ("mesh_x_pps", int), ("mesh_y_pps", int),
    ("algo", str), ("tension", float)))
MESH_ALGOS = ("lagrange", "bicubic", "direct")   # Upstream ZMesh
# Fallbacks, wenn die Referenz-Config die Werte nicht setzt: k2_box.py
# CHANGE_DEFAULTS check_cut_pos_x_min/max, k2_limits.py STOCK_X_MIN_WIDE
CUT_MIN_DEFAULT = -9.5
CUT_MAX_DEFAULT = -5.5
X_MIN_WIDE_DEFAULT = -10.0
# Bekannte Stock-Abschnitte, die NICHT übernommen werden: (Regex, Grund)
STOCK_AUSLASSEN = (
    (r"auto_addr$",
     "Geräte-Kennungen (UIDs) der Stock-Adressierung – der Pi vergibt die "
     "Adressen selbst ([k2_box] Auto-Addr); Werte werden nicht angezeigt"),
    (r"heater_bed$",
     "Creality-Bett-Leistungskalibrierung (max_power je Netzspannung) bzw. "
     "davon abhängige PID – Pi-Werte stehen in printer.cfg [heater_bed], "
     "bei Bedarf am Pi PID_CALIBRATE"),
    (r"extruder\d*$",
     "Creality-Extruder-Werte (zweiter Hochtemperatur-PID-Satz, "
     "user_nozzle_diameter) – Pi-Werte stehen in printer.cfg [extruder], "
     "bei Bedarf am Pi PID_CALIBRATE"),
    (r"prtouch",
     "Creality-Kraftsensor-Kalibrierung – der Pi nutzt [k2_prtouch] mit "
     "eigener Kalibrierung (PROBE_CALIBRATE)"),
)
STOCK_HOLEN = "scp root@<drucker>:/mnt/UDISK/printer_data/config/printer.cfg stock-printer.cfg"


class BlockFehler(Exception):
    pass


def _ohne_raute(zeile):
    """Upstream ConfigFileReader: alles ab '#' ist Kommentar."""
    pos = zeile.find("#")
    return zeile if pos < 0 else zeile[:pos]


def block_zerlegen(save, streng=True):
    """SAVE_CONFIG-Block -> (kopf, [[abschnitt, rohzeilen], ...]).

    streng: Kopf exakt wie Upstream (sonst liest Upstream den Block nicht).
    Wirft BlockFehler bei Zeilen ohne '#*# '-Präfix.
    """
    zeilen = save.split("\n")
    if streng:
        if tuple(zeilen[:3]) != AUTOSAVE_KOPF:
            raise BlockFehler("Kopf des SAVE_CONFIG-Blocks weicht vom Upstream-"
                              "Format ab (3 Kopfzeilen)")
        kopf, rest = list(zeilen[:3]), zeilen[3:]
    else:
        kopf, rest = [zeilen[0]], zeilen[1:]
    rest = "\n".join(rest).strip().split("\n") if "\n".join(rest).strip() else []
    abschnitte = []
    for nr, z in enumerate(rest):
        if not z.startswith("#*#") or (len(z) >= 4 and not z.startswith("#*# ")):
            raise BlockFehler("Zeile %d nach dem SAVE_CONFIG-Kopf beginnt nicht mit "
                              "'#*# ' (Block von Hand verändert?)" % (nr + 1))
        m = _SEC_RE.match(_ohne_raute(z[4:]).rstrip())
        if m:
            abschnitte.append([m.group(1).strip(), [z]])
        elif abschnitte:
            abschnitte[-1][1].append(z)
        elif streng and z.rstrip() != "#*#":
            # Upstream: configparser -> MissingSectionHeaderError beim Start
            raise BlockFehler("Inhalt vor dem ersten Abschnitt im SAVE_CONFIG-Block")
        else:
            kopf.append(z)
    return kopf, abschnitte


def block_inhalt(abschnitte, quelle="SAVE_CONFIG"):
    """[[abschnitt, rohzeilen]] -> {abschnitt: {option: wert}} wie Upstream
    (RawConfigParser strict=False, Optionsnamen klein, Mehrzeiler mit '\\n')."""
    import configparser
    cp = configparser.RawConfigParser(strict=False,
                                      inline_comment_prefixes=(";", "#"))
    text = "\n".join(_ohne_raute(z[4:]) for _n, roh in abschnitte for z in roh)
    try:
        cp.read_string(text, quelle)
    except configparser.Error as exc:
        raise BlockFehler("SAVE_CONFIG-Block nicht lesbar: %s"
                          % str(exc).splitlines()[0])
    res = collections.OrderedDict()
    for s in cp.sections():
        res[s] = collections.OrderedDict((o, cp.get(s, o)) for o in cp.options(s))
    return res


def _norm(wert):
    """Vergleichsform eines Werts: Zeilen x Komma-Teile, Zahlen als float."""
    zeilen = []
    for z in wert.split("\n"):
        if not z.strip():
            continue
        teile = []
        for t in z.split(","):
            t = t.strip()
            try:
                teile.append(float(t))
            except ValueError:
                teile.append(t.lower())
        zeilen.append(tuple(teile))
    return tuple(zeilen)


def _kurz(wert, n=60):
    w = " ".join(t.strip() for t in wert.split("\n") if t.strip())
    return w if len(w) <= n else w[:n - 3] + "..."


def abschnitt_zeilen(name, optionen):
    """Abschnitt im Format von Upstream-SAVE_CONFIG (ohne Leerzeile danach)."""
    z = ["#*# [%s]" % name]
    for opt, wert in optionen.items():
        teile = wert.split("\n")
        z.append(("#*# %s = %s" % (opt, teile[0].strip())).rstrip())
        z.extend("#*# \t" + t.strip() for t in teile[1:] if t.strip())
    return z


def block_bauen(kopf, abschnitte):
    zeilen = list(kopf)
    for i, (_name, roh) in enumerate(abschnitte):
        roh = list(roh)
        while roh and roh[-1].rstrip() == "#*#":
            roh.pop()
        if i:
            zeilen.append("#*#")
        zeilen.extend(roh)
    return "\n".join(zeilen) + "\n"


def regulaere_optionen(pfad, _besucht=None):
    """{(abschnitt, option): (datei, wert)} aller aktiven Optionen im Hauptteil
    von PFAD samt [include ...] (Pfad relativ zur einbindenden Datei, glob,
    wie Upstream). Erste Wertzeile, Kommentare entfernt."""
    besucht = set() if _besucht is None else _besucht
    res = {}
    absolut = os.path.abspath(pfad)
    if absolut in besucht or not os.path.isfile(pfad):
        return res
    besucht.add(absolut)
    with open(pfad, encoding="utf-8", errors="replace") as fh:
        body, _save = split_save_config(fh.read())
    sec = None
    for line in body.splitlines():
        line = _ohne_raute(line).rstrip()
        m = _SEC_RE.match(line)
        if m:
            sec = m.group(1).strip()
            if sec.startswith("include "):
                for f in sorted(glob.glob(os.path.join(os.path.dirname(pfad),
                                                       sec[8:].strip()))):
                    for k, v in regulaere_optionen(f, besucht).items():
                        res.setdefault(k, v)
                sec = None
            continue
        if sec is None or not line or line[0].isspace():
            continue
        m = re.match(r"^([^:=]+?)\s*[:=]\s*(.*)$", line)
        if m:
            res.setdefault((sec, m.group(1).strip().lower()),
                           (os.path.basename(pfad), m.group(2).strip()))
    return res


def _float(text):
    v = float(text)
    if math.isnan(v) or math.isinf(v):
        raise ValueError(text)
    return v


def _ref_float(ref, sec, opt, default, hinweise):
    if (sec, opt) not in ref:
        return default
    try:
        return _float(ref[(sec, opt)][1])
    except ValueError:
        hinweise.append("[%s] %s = %r nicht lesbar – Vorgabe %s benutzt"
                        % (sec, opt, ref[(sec, opt)][1], default))
        return default


def _pruefe_cut(wert, ref, hinweise):
    try:
        v = _float(wert)
    except ValueError:
        return "cut_pos_x %r ist keine Zahl" % (wert,)
    lo = _ref_float(ref, "k2_box", "check_cut_pos_x_min", CUT_MIN_DEFAULT, hinweise)
    hi = _ref_float(ref, "k2_box", "check_cut_pos_x_max", CUT_MAX_DEFAULT, hinweise)
    if not lo <= v <= hi:
        return ("cut_pos_x %s außerhalb [k2_box] check_cut_pos_x_min/max [%s, %s] "
                "– k2_box würde den Start verweigern" % (wert, lo, hi))
    xw = _ref_float(ref, "k2_limits", "x_min_wide", X_MIN_WIDE_DEFAULT, hinweise)
    if not xw <= v <= 0.0:
        return ("cut_pos_x %s außerhalb [k2_limits] x_min_wide..0 [%s, 0] "
                "– k2_limits würde den Start verweigern" % (wert, xw))
    return None


def _pruefe_shaper(opts):
    unbekannt = [o for o in opts if o not in SHAPER_OPTIONEN]
    if unbekannt:
        return "unbekannte Optionen (Upstream-[input_shaper] kennt sie nicht): %s" \
               % ", ".join(unbekannt)
    if not opts:
        return "Abschnitt leer"
    for achse in "xy":
        typ = opts.get("shaper_type_" + achse,
                       opts.get("shaper_type", SHAPER_TYP_DEFAULT)).strip()
        if typ not in UPSTREAM_SHAPER:
            return "Shaper-Typ %r (Achse %s) kennt Upstream nicht (%s)" % (
                typ, achse, "/".join(sorted(UPSTREAM_SHAPER)))
        f = opts.get("shaper_freq_" + achse)
        if f is not None:
            try:
                if _float(f) < 0:
                    raise ValueError(f)
            except ValueError:
                return "shaper_freq_%s %r ist keine Zahl >= 0" % (achse, f)
        d = opts.get("damping_ratio_" + achse)
        if d is not None:
            try:
                dv = _float(d)
            except ValueError:
                return "damping_ratio_%s %r ist keine Zahl" % (achse, d)
            if not 0.0 <= dv <= UPSTREAM_SHAPER[typ]:
                return "damping_ratio_%s %s außerhalb 0..%s (Upstream, Typ %s)" % (
                    achse, d, UPSTREAM_SHAPER[typ], typ)
    return None


def _mesh_punkte(wert):
    """wie Upstream getlists('points', seps=(',', '\\n'), parser=float)."""
    zeilen = []
    for z in wert.split("\n"):
        if not z.strip():
            continue
        zeilen.append([_float(t.strip()) for t in z.split(",")])
    return zeilen


def pruefe_mesh(opts):
    """-> (grund oder None, Kurzbeschreibung, Parameter)."""
    bekannt = set(MESH_FELDER) | {"version", "points"}
    unbekannt = [o for o in opts if o not in bekannt]
    if unbekannt:
        return ("unbekannte Profilfelder (Bedeutung offen, Upstream kennt sie nicht): %s"
                % ", ".join(unbekannt)), "", {}
    try:
        version = int(opts.get("version", "0").strip())
    except ValueError:
        return "version %r ist keine ganze Zahl" % (opts.get("version"),), "", {}
    if version != MESH_VERSION:
        return ("Profil-Version %d, Upstream erwartet %d (würde es als inkompatibel "
                "verwerfen)" % (version, MESH_VERSION)), "", {}
    fehlend = [k for k in list(MESH_FELDER) + ["points"] if k not in opts]
    if fehlend:
        return ("Pflichtfelder fehlen (Upstream bräche beim Start ab): %s"
                % ", ".join(fehlend)), "", {}
    p = {}
    for k, typ in MESH_FELDER.items():
        try:
            if typ is int:
                p[k] = int(opts[k].strip())
            elif typ is float:
                p[k] = _float(opts[k].strip())
            else:
                p[k] = opts[k].strip()
        except ValueError:
            return "%s %r ist kein %s" % (k, opts[k], typ.__name__), "", {}
    try:
        punkte = _mesh_punkte(opts["points"])
    except ValueError:
        return "points enthält Nicht-Zahlen bzw. leere Einträge", "", {}
    if p["x_count"] < 3 or p["y_count"] < 3:
        return "x_count/y_count %d/%d < 3" % (p["x_count"], p["y_count"]), "", {}
    if p["mesh_x_pps"] < 0 or p["mesh_y_pps"] < 0:
        return "mesh_x_pps/mesh_y_pps negativ", "", {}
    if not (p["min_x"] < p["max_x"] and p["min_y"] < p["max_y"]):
        return "min_x/max_x bzw. min_y/max_y vertauscht oder gleich", "", {}
    if p["algo"] not in MESH_ALGOS:
        return "algo %r kennt Upstream nicht (%s)" % (p["algo"], "/".join(MESH_ALGOS)), "", {}
    if p["algo"] == "direct" and (p["mesh_x_pps"] or p["mesh_y_pps"]):
        return "algo direct verlangt mesh_x_pps = mesh_y_pps = 0", "", {}
    if p["algo"] == "bicubic" and min(p["x_count"], p["y_count"]) < 4:
        return "bicubic mit weniger als 4 Punkten je Achse (Upstream: nur lagrange)", "", {}
    if p["algo"] == "lagrange" and max(p["x_count"], p["y_count"]) > 6:
        return "lagrange mit mehr als 6 Punkten je Achse (Upstream lehnt das ab)", "", {}
    if not 0.0 <= p["tension"] <= 2.0:
        return "tension %s außerhalb 0..2" % (p["tension"],), "", {}
    if len(punkte) != p["y_count"] or any(len(z) != p["x_count"] for z in punkte):
        return ("points passt nicht zu x_count x y_count = %d x %d (%d Zeilen, Längen %s)"
                % (p["x_count"], p["y_count"], len(punkte),
                   "/".join(str(n) for n in sorted({len(z) for z in punkte})))), "", {}
    alle = [v for z in punkte for v in z]
    kurz = "%dx%d, %s, X %s..%s, Y %s..%s, Z %.3f..%.3f" % (
        p["x_count"], p["y_count"], p["algo"], p["min_x"], p["max_x"],
        p["min_y"], p["max_y"], min(alle), max(alle))
    return None, kurz, p


def _paar(ref, opt, default, typ):
    if ("bed_mesh", opt) not in ref:
        return default
    teile = [t.strip() for t in ref[("bed_mesh", opt)][1].split(",") if t.strip()]
    try:
        werte = [typ(t) for t in teile]
    except ValueError:
        return None
    if len(werte) == 1:
        werte = werte * 2
    return tuple(werte) if len(werte) == 2 else None


def mesh_gegen_pi(p, ref):
    """Abweichungen des Profils vom [bed_mesh] der Pi-Config (nur Hinweis)."""
    if ("bed_mesh", "mesh_min") not in ref:
        return []
    soll = {
        "min": _paar(ref, "mesh_min", None, float),
        "max": _paar(ref, "mesh_max", None, float),
        "count": _paar(ref, "probe_count", (3, 3), int),
        "pps": _paar(ref, "mesh_pps", (2, 2), int),
    }
    algo = ref.get(("bed_mesh", "algorithm"), ("", "lagrange"))[1].lower()
    try:
        tension = float(ref.get(("bed_mesh", "bicubic_tension"), ("", "0.2"))[1])
    except ValueError:
        tension = None
    ist = {"min": (p["min_x"], p["min_y"]), "max": (p["max_x"], p["max_y"]),
           "count": (p["x_count"], p["y_count"]),
           "pps": (p["mesh_x_pps"], p["mesh_y_pps"])}
    namen = {"min": "mesh_min", "max": "mesh_max", "count": "probe_count",
             "pps": "mesh_pps"}
    ab = []
    for k in ("min", "max", "count", "pps"):
        if soll[k] is not None and tuple(soll[k]) != tuple(ist[k]):
            ab.append("%s Profil %s / Pi %s" % (namen[k], ist[k], tuple(soll[k])))
    if p["algo"] != algo:
        ab.append("algorithm Profil %s / Pi %s" % (p["algo"], algo))
    if tension is not None and abs(p["tension"] - tension) > 1e-9:
        ab.append("bicubic_tension Profil %s / Pi %s" % (p["tension"], tension))
    return ab


def stock_abbilden(stock, ref):
    """Stock-SAVE_CONFIG-Inhalt -> Liste von Einträgen (dict):
    quelle, ziel, optionen, ok, grund, info, ganz (Abschnitt als Einheit),
    hinweise, warnungen."""
    res = []
    for name, opts in stock.items():
        e = {"quelle": name, "ziel": None, "optionen": collections.OrderedDict(),
             "ok": False, "grund": "", "info": "", "ganz": True,
             "hinweise": [], "warnungen": []}
        res.append(e)
        if name == "box":
            e["ziel"], e["ganz"] = "k2_box", False
            rest = [o for o in opts if o != "cut_pos_x"]
            if rest:
                e["hinweise"].append("weitere [box]-Optionen ohne Pi-Abbildung, NICHT "
                                     "übernommen: %s" % ", ".join(rest))
            if "cut_pos_x" not in opts:
                e["grund"] = "keine abbildbare Option (nur %s)" % ", ".join(rest)
                continue
            grund = _pruefe_cut(opts["cut_pos_x"], ref, e["hinweise"])
            if grund:
                e["grund"] = grund
                continue
            e["optionen"]["cut_pos_x"] = opts["cut_pos_x"].strip()
            e["info"] = "cut_pos_x = %s" % opts["cut_pos_x"].strip()
        elif name == "input_shaper":
            e["ziel"] = name
            grund = _pruefe_shaper(opts)
            if grund:
                e["grund"] = grund
                continue
            e["optionen"].update(opts)
            e["info"] = ", ".join("%s = %s" % (o, _kurz(v)) for o, v in opts.items())
        elif name.startswith("bed_mesh ") and name[9:].strip():
            e["ziel"] = name
            grund, kurz, p = pruefe_mesh(opts)
            if grund:
                e["grund"] = grund
                continue
            e["optionen"].update(opts)
            e["info"] = kurz
            e["hinweise"].append("Stock-Messung (Creality-prtouch, Stock-Homing) – "
                                 "nach der Pi-Inbetriebnahme mit G29 neu messen und "
                                 "vergleichen")
            ab = mesh_gegen_pi(p, ref)
            if ab:
                e["hinweise"].append("Raster weicht von [bed_mesh] der Pi-Config ab "
                                     "(nur Hinweis, Upstream nutzt die Profilwerte): "
                                     + "; ".join(ab))
            if name[9:].strip() == "default":
                e["warnungen"].append("Profil 'default' lädt die Pi-Config automatisch "
                                      "(_PI_MESH_LADEN nach dem Z-Homing in homing.cfg, "
                                      "START_PRINT in macros.cfg) – wie bei Stock")
        else:
            for muster, grund in STOCK_AUSLASSEN:
                if re.match(muster, name):
                    e["grund"] = grund
                    break
            else:
                e["grund"] = ("unbekannter Abschnitt – keine geprüfte Abbildung auf den "
                              "Pi (Optionen: %s)" % (", ".join(opts) or "keine"))
            continue
        # Wirkt der Wert überhaupt? (Upstream _strip_duplicates)
        doppelt = sorted({ref[(e["ziel"], o)][0] for o in e["optionen"]
                          if (e["ziel"], o) in ref})
        if doppelt:
            e["grund"] = ("[%s] %s steht schon im Hauptteil/Include (%s) – Upstream "
                          "würde den SAVE_CONFIG-Wert verwerfen; dort entfernen"
                          % (e["ziel"], ", ".join(o for o in e["optionen"]
                                                  if (e["ziel"], o) in ref),
                             ", ".join(doppelt)))
            continue
        e["ok"] = True
    return res


def stock_lesen(pfad):
    """Stock-printer.cfg -> Inhalt des SAVE_CONFIG-Blocks; BlockFehler bei Problemen."""
    try:
        with open(pfad, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
    except OSError as exc:
        raise BlockFehler("%s nicht lesbar: %s" % (pfad, exc.strerror or exc))
    _body, save = split_save_config(text)
    if not save:
        raise BlockFehler("%s hat keinen SAVE_CONFIG-Block (Marker fehlt) – "
                          "falsche Datei?" % pfad)
    _kopf, abschnitte = block_zerlegen(save, streng=False)
    inhalt = block_inhalt(abschnitte, pfad)
    if not inhalt:
        raise BlockFehler("SAVE_CONFIG-Block von %s ist leer" % pfad)
    return inhalt


def _melde_eintrag(e, status=None):
    pfeil = "[%s]" % e["quelle"]
    if e["ziel"] and e["ok"]:
        pfeil += " -> [%s]" % e["ziel"]
    if e["ok"]:
        print("  stock %s: %s (%s)" % (pfeil, status or "übernehmbar", e["info"]))
    else:
        print("  stock %s: AUSGELASSEN – %s" % (pfeil, e["grund"]))
    for h in e["hinweise"]:
        print("        Hinweis: %s" % h)
    if e["ok"]:
        for w in e["warnungen"]:
            print("  WARN  [%s]: %s" % (e["ziel"], w))


def _stock_args(args, name):
    pos, flags = [], {}
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--referenz" and name == "stock-pruefen":
            if i + 1 >= len(args):
                raise SystemExit("%s: --referenz braucht eine Datei" % name)
            flags["referenz"] = args[i + 1]
            i += 2
        elif a == "--ueberschreiben" and name == "stock-uebernehmen":
            flags["ueberschreiben"] = True
            i += 1
        elif a.startswith("--"):
            raise SystemExit("%s: unbekannte Option %r" % (name, a))
        else:
            pos.append(a)
            i += 1
    return pos, flags


def cmd_stock_pruefen(args):
    pos, flags = _stock_args(args, "stock-pruefen")
    if len(pos) != 1:
        raise SystemExit("stock-pruefen: STOCK fehlt (genau eine Datei)")
    ref = regulaere_optionen(flags["referenz"]) if flags.get("referenz") else {}
    try:
        eintraege = stock_abbilden(stock_lesen(pos[0]), ref)
    except BlockFehler as exc:
        print("  FEHLER %s" % exc)
        print("        Hinweis: Original-printer.cfg holen: %s" % STOCK_HOLEN)
        return 2
    for e in eintraege:
        _melde_eintrag(e)
    n = sum(1 for e in eintraege if e["ok"])
    print("  stock PRUEFUNG: uebernehmbar=%d ausgelassen=%d" % (n, len(eintraege) - n))
    return 0 if n else 3


def cmd_stock_uebernehmen(args):
    pos, flags = _stock_args(args, "stock-uebernehmen")
    if len(pos) != 2:
        raise SystemExit("stock-uebernehmen: STOCK ZIEL fehlen")
    stock_pfad, ziel = pos
    ueber = flags.get("ueberschreiben", False)
    try:
        text = _read(ziel)
        body, save = split_save_config(text)
        if "\n#*# " in body:
            raise BlockFehler("%s: '#*# '-Zeilen außerhalb des SAVE_CONFIG-Blocks – "
                              "Upstream würde den Block ignorieren" % ziel)
        if save and not body.endswith("\n"):
            raise BlockFehler("%s: SAVE_CONFIG-Marker steht nicht am Zeilenanfang" % ziel)
        if save:
            kopf, abschnitte = block_zerlegen(save, streng=True)
        else:
            kopf, abschnitte = list(AUTOSAVE_KOPF), []
        ziel_inhalt = block_inhalt(abschnitte, ziel)
        eintraege = stock_abbilden(stock_lesen(stock_pfad), regulaere_optionen(ziel))
    except (BlockFehler, OSError, UnicodeDecodeError) as exc:
        print("  FEHLER %s" % exc)
        return 2
    zaehler = collections.OrderedDict((k, 0) for k in (
        "neu", "gleich", "behalten", "ueberschrieben", "ausgelassen"))
    geaendert = False
    index = {name: i for i, (name, _roh) in enumerate(abschnitte)}
    for e in eintraege:
        if not e["ok"]:
            zaehler["ausgelassen"] += 1
            _melde_eintrag(e)
            continue
        z = e["ziel"]
        alt = ziel_inhalt.get(z)
        if alt is None:
            neu_opts = e["optionen"]
            status, art = "NEU übernommen", "neu"
        else:
            if e["ganz"]:
                gleich = (set(alt) == set(e["optionen"]) and
                          all(_norm(alt[o]) == _norm(v) for o, v in e["optionen"].items()))
                neu_opts = e["optionen"]
            else:
                gleich = all(o in alt and _norm(alt[o]) == _norm(v)
                             for o, v in e["optionen"].items())
                neu_opts = collections.OrderedDict(alt)
                neu_opts.update(e["optionen"])
            if gleich:
                status, art = "gleich, unverändert", "gleich"
            else:
                diff = []
                for o in list(e["optionen"]) + [o for o in alt if o not in e["optionen"]]:
                    sv, pv = e["optionen"].get(o), alt.get(o)
                    if sv is not None and pv is not None and _norm(sv) == _norm(pv):
                        continue
                    if not e["ganz"] and sv is None:
                        continue
                    diff.append("%s: Stock %s / Pi-Block %s" % (
                        o, "fehlt" if sv is None else
                        ("abweichend" if o == "points" else _kurz(sv, 30)),
                        "fehlt" if pv is None else
                        ("abweichend" if o == "points" else _kurz(pv, 30))))
                if ueber:
                    status, art = "ÜBERSCHRIEBEN (--ueberschreiben; bisher: %s)" % "; ".join(diff), \
                        "ueberschrieben"
                else:
                    status, art = ("BEHALTEN – Pi-Block weicht ab (%s); Stock-Wert nur mit "
                                   "--ueberschreiben (install.sh: --stock-ueberschreiben)"
                                   % "; ".join(diff)), "behalten"
        zaehler[art] += 1
        _melde_eintrag(e, status)
        if art in ("neu", "ueberschrieben"):
            zeilen = abschnitt_zeilen(z, neu_opts)
            if z in index:
                abschnitte[index[z]] = [z, zeilen]
            else:
                index[z] = len(abschnitte)
                abschnitte.append([z, zeilen])
            ziel_inhalt[z] = collections.OrderedDict(neu_opts)
            geaendert = True
    n_ok = sum(1 for e in eintraege if e["ok"])
    if geaendert:
        if not save:
            body = body.rstrip() + "\n\n"
        neu = body + block_bauen(kopf, abschnitte)
        # Gegenprobe: Ergebnis muss nach denselben Regeln lesbar sein
        _b, s2 = split_save_config(neu)
        try:
            block_inhalt(block_zerlegen(s2, streng=True)[1], ziel)
        except BlockFehler as exc:
            print("  FEHLER Ergebnis nicht lesbar (%s) – %s unverändert" % (exc, ziel))
            return 2
        tmp = ziel + ".k2tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(neu)
        os.replace(tmp, ziel)
    print("  stock ERGEBNIS: %s%s" % (" ".join("%s=%d" % kv for kv in zaehler.items()),
                                      "" if geaendert else " (Datei unverändert)"))
    return 0 if n_ok else 3


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 2
    cmd, args = argv[1], argv[2:]
    if cmd == "merge":
        return cmd_merge(args)
    if cmd == "platzhalter":
        return cmd_platzhalter(args)
    if cmd == "setze":
        return cmd_setze(args)
    if cmd == "stock-pruefen":
        return cmd_stock_pruefen(args)
    if cmd == "stock-uebernehmen":
        return cmd_stock_uebernehmen(args)
    print("unbekannter Unterbefehl: %s" % cmd, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
