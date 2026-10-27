#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline-Test für k2cfg.py – Übernahme von Kalibrierwerten aus dem
SAVE_CONFIG-Block einer Stock-printer.cfg (stock-pruefen / stock-uebernehmen).

Nur SYNTHETISCHE Daten (keine echten Mesh-Werte, keine Geräte-Kennungen).

Teil A  Parser und Prüfungen (Block-Regeln, Mesh-Profil, Shaper, cut_pos_x,
        Abbildung je Abschnitt, Include-Doppelungen).
Teil B  Ende-zu-Ende als Unterprozess auf Temp-Dateien: neu/gleich/behalten/
        überschrieben, Wiederholung ohne Änderung, Block anlegen, Fehlerfälle
        (Exit 2/3, Ziel unverändert), keine auto_addr-Werte in der Ausgabe;
        dazu stock-pruefen gegen die mitgelieferte Pi-printer.cfg.
Teil C  (optional) Abgleich mit Upstream-Klipper ($KLIPPER_SRC): Profilfelder,
        Shaper-Typen, SAVE_CONFIG-Kopf; Ergebnisdatei mit Upstream-
        configfile.py gelesen (kein verworfener/kollidierender Wert, Profil
        und Shaper lesbar wie bed_mesh.py/input_shaper.py). Sonst SKIP.

Aufruf: python3 test_k2cfg.py
"""

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile

HIER = os.path.dirname(os.path.abspath(__file__))
SKRIPT = os.path.join(HIER, "k2cfg.py")
FEHLER = []
N_OK = [0]


def check(bed, text):
    if bed:
        N_OK[0] += 1
        print("  ok    %s" % text)
    else:
        FEHLER.append(text)
        print("  FEHLT %s" % text)


def lade(name, pfad):
    spec = importlib.util.spec_from_file_location(name, pfad)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


K = lade("k2cfg", SKRIPT)
MARKER = K.SAVE_MARKER
KOPF = "\n".join(K.AUTOSAVE_KOPF)
UID_SYNTH = "0xAB, 0xCD, 0xEF"          # synthetisch – darf nie ausgegeben werden


def block(*abschnitte):
    """SAVE_CONFIG-Block aus (name, [(option, wert), ...]); Mehrzeiler mit '\\n'."""
    z = list(K.AUTOSAVE_KOPF)
    for i, (name, opts) in enumerate(abschnitte):
        if i:
            z.append("#*#")
        z.extend(K.abschnitt_zeilen(name, dict(opts)))
    return "\n".join(z) + "\n"


def mesh_opts(nx=4, ny=4, algo="bicubic", version="1", extra=(), weglassen=(),
              punkte=None, pps="2"):
    if punkte is None:
        punkte = "\n" + "\n".join(", ".join("%.6f" % (0.001 * (x + 10 * y))
                                            for x in range(nx)) for y in range(ny))
    o = [("version", version), ("points", punkte), ("x_count", str(nx)),
         ("y_count", str(ny)), ("mesh_x_pps", pps), ("mesh_y_pps", pps),
         ("algo", algo), ("tension", "0.2"), ("min_x", "10.0"), ("max_x", "300.0"),
         ("min_y", "10.0"), ("max_y", "300.0")]
    o = [(k, v) for k, v in o if k not in weglassen]
    return o + list(extra)


STOCK_STD = ("[printer]\nkinematics: corexy\n# Stock-Hauptteil (synthetisch)\n\n"
             + block(("auto_addr", [("mb_addr_table_uniids", "\n" + UID_SYNTH + "\n0x00")]),
                     ("box", [("cut_pos_x", "-8.10")]),
                     ("input_shaper", [("shaper_type_x", "mzv"), ("shaper_freq_x", "40.0"),
                                       ("shaper_type_y", "ei"), ("shaper_freq_y", "35.5")]),
                     ("bed_mesh default", mesh_opts())))

PI_MOD = """[k2_box]
box_addr: 1
# cut_pos_x: hier bewusst NICHT
check_cut_pos_x_min: -9.5
check_cut_pos_x_max: -5.5

[k2_limits]
x_min_wide: -10
"""
PI_HAUPT = """[include mod.cfg]

[printer]
kinematics: corexy

[bed_mesh]
mesh_min: 10, 10
mesh_max: 300, 300
probe_count: 4, 4
mesh_pps: 2, 2
algorithm: bicubic
bicubic_tension: 0.2

[input_shaper]

"""


def pi_cfg(d, save=None, mod=PI_MOD, haupt=PI_HAUPT):
    with open(os.path.join(d, "mod.cfg"), "w", encoding="utf-8") as fh:
        fh.write(mod)
    p = os.path.join(d, "printer.cfg")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(haupt + (save if save is not None else
                          block(("input_shaper", [("shaper_type_x", "mzv"),
                                                  ("shaper_freq_x", "40.0"),
                                                  ("shaper_type_y", "ei"),
                                                  ("shaper_freq_y", "35.5")]))))
    return p


def schreibe(d, name, text):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return p


def lies(p):
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def lauf(*args):
    r = subprocess.run([sys.executable, "-I", SKRIPT] + list(args),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       universal_newlines=True, timeout=60)
    return r.returncode, r.stdout


def inhalt(p):
    _b, s = K.split_save_config(lies(p))
    return K.block_inhalt(K.block_zerlegen(s)[1])


# ---------------------------------------------------------------------
def teil_a():
    print("Teil A: Parser und Prüfungen")
    # Block-Regeln (Upstream configfile.py _find_autosave_data)
    kopf, abschn = K.block_zerlegen(block(("input_shaper", [("shaper_freq_x", "40")])))
    check(len(kopf) == 3 and [a[0] for a in abschn] == ["input_shaper"],
          "streng: Upstream-Kopf (3 Zeilen) + 1 Abschnitt erkannt")
    for text, was in ((MARKER + "\n#*# anderer Kopf\n#*#\n#*# [a]\n#*# b = 1\n", "Kopf weicht ab"),
                      (KOPF + "\n#*# [a]\nb = 1\n", "Zeile ohne '#*# '"),
                      (KOPF + "\n#*# [a]\n#*#b = 1\n", "'#*#' ohne Leerzeichen"),
                      (KOPF + "\n#*# b = 1\n#*# [a]\n", "Inhalt vor erstem Abschnitt")):
        try:
            K.block_zerlegen(text)
            check(False, "BlockFehler erwartet: %s" % was)
        except K.BlockFehler:
            check(True, "BlockFehler: %s" % was)
    _k, ab = K.block_zerlegen(MARKER + "\n#*# Creality-Kopf frei\n#*#\n#*# [x]\n#*# y = 2\n",
                              streng=False)
    check([a[0] for a in ab] == ["x"], "Stock (nicht streng): freier Kopftext erlaubt")
    inh = K.block_inhalt(K.block_zerlegen(block(("bed_mesh p", mesh_opts(nx=4, ny=3, algo="lagrange"))))[1])
    pk = K._mesh_punkte(inh["bed_mesh p"]["points"])
    check(len(pk) == 3 and all(len(z) == 4 for z in pk), "points als 3x4-Matrix gelesen")
    check(K._norm("-6.5") == K._norm("-6.50") and K._norm("MZV") == K._norm("mzv"),
          "Vergleich numerisch bzw. ohne Groß/klein")

    # Mesh-Profil (Upstream bed_mesh.py ProfileManager/ZMesh)
    def mesh(**kw):
        o = dict(mesh_opts(**kw))
        return K.pruefe_mesh(o)[0]
    check(mesh() is None, "gültiges 4x4-bicubic-Profil")
    check(mesh(nx=5, ny=3, algo="lagrange") is None, "gültiges 5x3-lagrange-Profil")
    check(mesh(nx=3, ny=3, algo="direct", pps="0") is None, "gültiges 3x3-direct-Profil (pps 0)")
    faelle = (
        (dict(version="2"), "Profil-Version 2"),
        (dict(weglassen=("tension",)), "Pflichtfelder fehlen"),
        (dict(weglassen=("points",)), "Pflichtfelder fehlen"),
        (dict(extra=(("creality_feld", "1"),)), "unbekannte Profilfelder"),
        (dict(algo="spline"), "algo"),
        (dict(nx=3, ny=3), "bicubic mit weniger als 4"),
        (dict(nx=7, ny=4, algo="lagrange"), "lagrange mit mehr als 6"),
        (dict(nx=3, ny=3, algo="direct"), "direct verlangt"),
        (dict(punkte="\n0.1, 0.2, 0.3, 0.4\n0.1, 0.2, 0.3"), "points passt nicht"),
        (dict(punkte="\n0.1, 0.2, 0.3, 0.4,\n" * 4), "Nicht-Zahlen bzw. leere"),
        (dict(nx=2, ny=4, algo="lagrange"), "< 3"),
    )
    for kw, erwartet in faelle:
        g = mesh(**kw)
        check(g is not None and erwartet in g, "Mesh abgelehnt (%s): %s" % (erwartet, g))
    o = dict(mesh_opts())
    o["x_count"] = "4.0"
    check("ist kein int" in (K.pruefe_mesh(o)[0] or ""), "x_count '4.0' abgelehnt (Upstream getint)")
    o = dict(mesh_opts())
    o["min_x"], o["max_x"] = "300", "10"
    check("vertauscht" in (K.pruefe_mesh(o)[0] or ""), "min_x > max_x abgelehnt")
    o = dict(mesh_opts())
    o["tension"] = "2.5"
    check("tension" in (K.pruefe_mesh(o)[0] or ""), "tension außerhalb 0..2 abgelehnt")

    # Input-Shaper (Upstream input_shaper.py / shaper_defs.py)
    sh = K._pruefe_shaper
    check(sh({"shaper_type_x": "mzv", "shaper_freq_x": "40"}) is None, "Shaper mzv ok")
    check(sh({"shaper_type": "ei", "damping_ratio_x": "0.3"}) is None, "shaper_type + damping ok")
    check("kennt Upstream nicht" in (sh({"shaper_type_x": "smooth_zv"}) or ""), "unbekannter Shaper-Typ")
    check("kennt Upstream nicht" in (sh({"shaper_type_y": "MZV"}) or ""), "Typ in Großbuchstaben (Upstream exakt)")
    check("unbekannte Optionen" in (sh({"shaper_freq_x": "40", "smoothing_x": "1"}) or ""),
          "unbekannte Shaper-Option")
    check(">= 0" in (sh({"shaper_freq_y": "-1"}) or ""), "negative Frequenz")
    check("außerhalb" in (sh({"shaper_type_x": "ei", "damping_ratio_x": "0.5"}) or ""),
          "damping_ratio über max (ei 0.4)")

    # Abbildung je Abschnitt
    stock = K.block_inhalt(K.block_zerlegen(K.split_save_config(STOCK_STD)[1], streng=False)[1])
    e = {x["quelle"]: x for x in K.stock_abbilden(stock, {})}
    check(not e["auto_addr"]["ok"] and "Geräte-Kennungen" in e["auto_addr"]["grund"],
          "[auto_addr] ausgelassen (Geräte-Kennungen)")
    check(e["box"]["ok"] and e["box"]["ziel"] == "k2_box" and not e["box"]["ganz"]
          and e["box"]["optionen"] == {"cut_pos_x": "-8.10"}, "[box] cut_pos_x -> [k2_box] (Option)")
    check(e["input_shaper"]["ok"] and e["input_shaper"]["ganz"], "[input_shaper] gleichnamig (Abschnitt)")
    check(e["bed_mesh default"]["ok"] and e["bed_mesh default"]["warnungen"],
          "[bed_mesh default] gleichnamig + Warnung Autoload")
    s2 = {"box": {"cut_pos_x": "-4.0", "zusatz": "1"}, "heater_bed": {"max_power": "0.9"},
          "extruder": {"pid_kp": "1"}, "prtouch default": {"z_gap_00": "0"},
          "foo_bar": {"a": "1", "b": "2"}, "bed_mesh": {"x": "1"},
          "input_shaper": {"shaper_type_x": "nix"}}
    e = {x["quelle"]: x for x in K.stock_abbilden(s2, {})}
    check(not e["box"]["ok"] and "check_cut_pos_x" in e["box"]["grund"]
          and any("zusatz" in h for h in e["box"]["hinweise"]),
          "[box] cut_pos_x -4.0 außerhalb Grenzen + Zusatzoption gemeldet")
    check(all(not e[n]["ok"] for n in ("heater_bed", "extruder", "prtouch default")),
          "[heater_bed]/[extruder]/[prtouch default] ausgelassen")
    check(not e["foo_bar"]["ok"] and "unbekannter Abschnitt" in e["foo_bar"]["grund"]
          and "a, b" in e["foo_bar"]["grund"], "unbekannter Abschnitt mit Optionsnamen gemeldet")
    check(not e["bed_mesh"]["ok"], "[bed_mesh] ohne Profilnamen ausgelassen")
    check(not e["input_shaper"]["ok"], "[input_shaper] mit unbekanntem Typ ausgelassen")
    ref = {("k2_box", "check_cut_pos_x_min"): ("m.cfg", "-8.0")}
    e = {x["quelle"]: x for x in K.stock_abbilden({"box": {"cut_pos_x": "-8.10"}}, ref)}
    check(not e["box"]["ok"], "cut_pos_x gegen check_cut_pos_x_min aus der Referenz (-8.0)")
    ref = {("k2_limits", "x_min_wide"): ("m.cfg", "-8.0")}
    e = {x["quelle"]: x for x in K.stock_abbilden({"box": {"cut_pos_x": "-8.10"}}, ref)}
    check(not e["box"]["ok"] and "x_min_wide" in e["box"]["grund"],
          "cut_pos_x gegen [k2_limits] x_min_wide aus der Referenz")

    # Include-Doppelung -> Upstream würde den SAVE_CONFIG-Wert verwerfen
    d = tempfile.mkdtemp(prefix="k2cfg-a-")
    try:
        p = pi_cfg(d, mod=PI_MOD.replace("# cut_pos_x: hier bewusst NICHT", "cut_pos_x: -7.5"))
        ref = K.regulaere_optionen(p)
        check(ref.get(("k2_box", "cut_pos_x"), ("", ""))[0] == "mod.cfg",
              "regulaere_optionen folgt [include] (mod.cfg)")
        check(("k2_box", "check_cut_pos_x_min") in ref and ("input_shaper", "shaper_freq_x") not in ref,
              "SAVE_CONFIG-Block und Kommentare zählen nicht als Hauptteil")
        e = {x["quelle"]: x for x in K.stock_abbilden({"box": {"cut_pos_x": "-8.10"}}, ref)}
        check(not e["box"]["ok"] and "mod.cfg" in e["box"]["grund"],
              "cut_pos_x im Include -> ausgelassen mit Dateiname")
        ab = K.mesh_gegen_pi(K.pruefe_mesh(dict(mesh_opts(nx=5, ny=4)))[2], ref)
        check(len(ab) == 1 and "probe_count" in ab[0], "Raster-Abweichung zu [bed_mesh] gemeldet")
        check(K.mesh_gegen_pi(K.pruefe_mesh(dict(mesh_opts()))[2], ref) == [],
              "gleiches Raster -> kein Hinweis")
    finally:
        shutil.rmtree(d)


# ---------------------------------------------------------------------
def teil_b():
    print("Teil B: stock-uebernehmen / stock-pruefen (Unterprozess)")
    d = tempfile.mkdtemp(prefix="k2cfg-b-")
    try:
        stock = schreibe(d, "stock.cfg", STOCK_STD)
        p = pi_cfg(d)
        vorher = lies(p)
        rc, out = lauf("stock-uebernehmen", stock, p)
        neu = lies(p)
        check(rc == 0 and "ERGEBNIS: neu=2 gleich=1 behalten=0 ueberschrieben=0 ausgelassen=1" in out,
              "Erstimport: neu=2 (k2_box, bed_mesh) gleich=1 (Shaper) ausgelassen=1")
        check(neu.startswith(vorher.rstrip("\n")), "Hauptteil + bisheriger Block unverändert, nur angehängt")
        i = inhalt(p)
        check(i.get("k2_box") == {"cut_pos_x": "-8.10"} and "bed_mesh default" in i,
              "Block enthält [k2_box] cut_pos_x und [bed_mesh default]")
        check(K._norm(i["bed_mesh default"]["points"]) ==
              K._norm(dict(mesh_opts())["points"]), "Mesh-Punkte unverändert übernommen")
        check(UID_SYNTH not in out and "0xAB" not in out and "0xAB" not in neu,
              "auto_addr-Werte weder ausgegeben noch übernommen")
        check("WARN  [bed_mesh default]" in out, "Warnung: Profil 'default' wird automatisch geladen")
        rc, out = lauf("stock-uebernehmen", stock, p)
        check(rc == 0 and "gleich=3" in out and "(Datei unverändert)" in out and lies(p) == neu,
              "Wiederholung: alles gleich, Datei bytegleich")

        # Konflikt: Pi-Block mit anderen Werten
        konf = block(("input_shaper", [("shaper_type_x", "zv"), ("shaper_freq_x", "60.0"),
                                       ("damping_ratio_x", "0.12")]),
                     ("k2_box", [("cut_pos_x", "-7.00"), ("weitere_option", "1")]),
                     ("bed_mesh default", mesh_opts(nx=5, ny=5)))
        p = pi_cfg(d, save=konf)
        vorher = lies(p)
        rc, out = lauf("stock-uebernehmen", stock, p)
        check(rc == 0 and "behalten=3" in out and "(Datei unverändert)" in out and lies(p) == vorher,
              "abweichende Abschnitte ohne --ueberschreiben BEHALTEN, Datei unverändert")
        check("--stock-ueberschreiben" in out and "Stock 40.0 / Pi-Block 60.0" in out,
              "Meldung nennt Abweichung und Option")
        rc, out = lauf("stock-uebernehmen", stock, p, "--ueberschreiben")
        i = inhalt(p)
        check(rc == 0 and "ueberschrieben=3" in out, "--ueberschreiben: 3 Abschnitte ersetzt")
        check(set(i["input_shaper"]) == {"shaper_type_x", "shaper_freq_x", "shaper_type_y",
                                         "shaper_freq_y"},
              "[input_shaper] ganz ersetzt (damping_ratio_x des Pi-Blocks entfällt)")
        check(i["k2_box"] == {"cut_pos_x": "-8.10", "weitere_option": "1"},
              "[k2_box]: nur cut_pos_x ersetzt, andere Option bleibt")
        check(i["bed_mesh default"]["x_count"] == "4", "[bed_mesh default] ganz ersetzt (4x4)")
        _b, s = K.split_save_config(lies(p))
        check(K.block_zerlegen(s, streng=True) is not None, "Ergebnis erfüllt die Upstream-Blockregeln")

        # Kein Block im Ziel -> wird angelegt
        p = pi_cfg(d, save="")
        rc, out = lauf("stock-uebernehmen", stock, p)
        t = lies(p)
        check(rc == 0 and "neu=3" in out and ("\n\n" + KOPF + "\n#*# [k2_box]") in t,
              "Ziel ohne SAVE_CONFIG: Block mit Upstream-Kopf angelegt (Leerzeile davor)")

        # Fehlerfälle: Ziel unverändert
        kaputt = PI_HAUPT + KOPF + "\n#*# [input_shaper]\nshaper_freq_x = 1\n"
        p = schreibe(d, "printer.cfg", kaputt)
        rc, out = lauf("stock-uebernehmen", stock, p)
        check(rc == 2 and lies(p) == kaputt and "FEHLER" in out, "Ziel-Block beschädigt -> Exit 2, unverändert")
        p = pi_cfg(d)
        vorher = lies(p)
        for name, text, erw in (
                ("ohne-block.cfg", "[printer]\nkinematics: corexy\n", 2),
                ("nur-uid.cfg", "[x]\n\n" + block(("auto_addr", [("mb_addr_table_uniids", UID_SYNTH)])), 3),
                ("kaputt.cfg", "[x]\n\n" + KOPF + "\n#*# [box]\ncut_pos_x = -8\n", 2)):
            s = schreibe(d, name, text)
            rc, out = lauf("stock-uebernehmen", s, p)
            check(rc == erw and lies(p) == vorher and UID_SYNTH not in out,
                  "Stock %s -> Exit %d, Ziel unverändert" % (name, erw))
        rc, out = lauf("stock-uebernehmen", os.path.join(d, "fehlt.cfg"), p)
        check(rc == 2 and lies(p) == vorher, "Stock-Datei fehlt -> Exit 2")
        rc, out = lauf("stock-uebernehmen", stock, p, "--unbekannt")
        check(rc != 0 and lies(p) == vorher, "unbekannte Option -> Abbruch")

        # CRLF-Datei (z. B. über Windows kopiert) und anderer Profilname
        crlf = STOCK_STD.replace("bed_mesh default", "bed_mesh pla_60").replace("\n", "\r\n")
        s = schreibe(d, "crlf.cfg", crlf)
        p = pi_cfg(d)
        rc, out = lauf("stock-uebernehmen", s, p)
        check(rc == 0 and "bed_mesh pla_60" in inhalt(p) and "WARN  [bed_mesh" not in out,
              "CRLF-Stock-Datei gelesen; Profil 'pla_60' ohne Autoload-Warnung")

        # stock-pruefen
        rc, out = lauf("stock-pruefen", stock, "--referenz", pi_cfg(d))
        check(rc == 0 and "uebernehmbar=3 ausgelassen=1" in out and UID_SYNTH not in out,
              "stock-pruefen: 3 übernehmbar, 1 ausgelassen, Exit 0")
        rc, out = lauf("stock-pruefen", schreibe(d, "u.cfg", "[x]\n\n" + block(
            ("auto_addr", [("mb_addr_table_uniids", UID_SYNTH)]))))
        check(rc == 3 and UID_SYNTH not in out, "stock-pruefen: nichts übernehmbar -> Exit 3")
        rc, out = lauf("stock-pruefen", os.path.join(d, "fehlt.cfg"))
        check(rc == 2, "stock-pruefen: Datei fehlt -> Exit 2")

        # gegen die mitgelieferte Pi-Config (Repo: ../config-pi, Paket: pi-host/config-pi)
        for kand in (os.path.join(HIER, "..", "config-pi", "printer.cfg"),
                     os.path.join(HIER, "pi-host", "config-pi", "printer.cfg")):
            if os.path.isfile(kand):
                rc, out = lauf("stock-pruefen", stock, "--referenz", kand)
                check(rc == 0 and "[box] -> [k2_box]: übernehmbar" in out
                      and "steht schon im Hauptteil" not in out,
                      "gegen mitgelieferte printer.cfg: cut_pos_x übernehmbar (k2-module.cfg "
                      "ohne aktive cut_pos_x-Zeile)")
                check("Raster weicht" in out,
                      "gegen mitgelieferte printer.cfg: Raster-Hinweis (synthetisches 4x4)")
                break
        else:
            print("  SKIP  mitgelieferte Pi-printer.cfg nicht gefunden")
    finally:
        shutil.rmtree(d)


# ---------------------------------------------------------------------
def teil_c():
    print("Teil C: Abgleich mit Upstream-Klipper ($KLIPPER_SRC)")
    src = os.environ.get("KLIPPER_SRC")
    kl = os.path.join(src or "", "klippy")
    need = [os.path.join(kl, f) for f in ("configfile.py", "extras/bed_mesh.py",
                                          "extras/shaper_defs.py")]
    if not src or not all(os.path.isfile(f) for f in need):
        print("  SKIP  KLIPPER_SRC nicht gesetzt bzw. ohne klippy/configfile.py, "
              "extras/bed_mesh.py, extras/shaper_defs.py")
        return
    bm = lies(need[1])
    m = re.search(r"^PROFILE_VERSION\s*=\s*(\d+)", bm, re.M)
    check(m and int(m.group(1)) == K.MESH_VERSION, "PROFILE_VERSION = %s" % K.MESH_VERSION)
    m = re.search(r"^PROFILE_OPTIONS\s*=\s*\{(.*?)\}", bm, re.M | re.S)
    felder = dict(re.findall(r"'(\w+)'\s*:\s*(\w+)", m.group(1))) if m else {}
    check(felder == {k: t.__name__ for k, t in K.MESH_FELDER.items()},
          "PROFILE_OPTIONS = MESH_FELDER (%d Felder)" % len(felder))
    sd = lies(need[2])
    shaper = dict((n, float(v)) for n, v in re.findall(
        r"name='(\w+)'.*?max_damping_ratio=([0-9.]+)", sd, re.S))
    check(shaper == K.UPSTREAM_SHAPER, "INPUT_SHAPERS = UPSTREAM_SHAPER (%s)" % ", ".join(sorted(shaper)))
    cf = lade("upstream_configfile", need[0])
    check(cf.AUTOSAVE_HEADER == "\n" + "\n".join(K.AUTOSAVE_KOPF) + "\n",
          "AUTOSAVE_HEADER = AUTOSAVE_KOPF")

    d = tempfile.mkdtemp(prefix="k2cfg-c-")
    try:
        stock = schreibe(d, "stock.cfg", STOCK_STD)
        p = pi_cfg(d, save=block(("input_shaper", [("shaper_type_x", "zv"), ("shaper_freq_x", "60")])))
        rc, _out = lauf("stock-uebernehmen", stock, p, "--ueberschreiben")
        check(rc == 0, "Ergebnisdatei erzeugt")
        rdr = cf.ConfigFileReader()
        daten = rdr.read_config_file(p)
        auto = object.__new__(cf.ConfigAutoSave)
        regulaer, autosave = auto._find_autosave_data(daten)
        check(bool(autosave.strip()), "Upstream findet den SAVE_CONFIG-Block (nicht 'corrupted')")
        reg_fc = rdr.build_fileconfig_with_includes(regulaer, p)
        check(auto._strip_duplicates(autosave, reg_fc) == autosave,
              "Upstream verwirft keinen Wert (_strip_duplicates ohne Treffer)")
        save_fc = rdr.build_fileconfig(autosave, p)
        konflikte = [(s, o) for s in save_fc.sections() for o in save_fc.options(s)
                     if reg_fc.has_option(s, o)]
        check(not konflikte, "kein SAVE_CONFIG-Konflikt mit Hauptteil/Include")
        rdr.append_fileconfig(reg_fc, autosave, "*AUTOSAVE*")
        w = cf.ConfigWrapper(None, reg_fc, {}, "bed_mesh default")
        pts = w.getlists("points", seps=(",", "\n"), parser=float)
        werte = {k: (w.getint(k) if t is int else w.getfloat(k) if t is float else w.get(k))
                 for k, t in K.MESH_FELDER.items()}
        check(w.getint("version") == 1 and len(pts) == werte["y_count"]
              and all(len(z) == werte["x_count"] for z in pts),
              "Profil mit Upstream-ConfigWrapper gelesen (version, points %dx%d, 10 Felder)"
              % (werte["x_count"], werte["y_count"]))
        w = cf.ConfigWrapper(None, reg_fc, {}, "input_shaper")
        check(w.get("shaper_type_y") == "ei" and w.getfloat("shaper_freq_y", minval=0.) == 35.5,
              "[input_shaper] mit Upstream-ConfigWrapper gelesen (ei 35.5)")
        w = cf.ConfigWrapper(None, reg_fc, {}, "k2_box")
        check(w.getfloat("cut_pos_x") == -8.10 and w.getfloat("check_cut_pos_x_min") == -9.5,
              "[k2_box]: cut_pos_x aus SAVE_CONFIG + Optionen aus dem Include zusammengeführt")
    finally:
        shutil.rmtree(d)


def standardwerte():
    """Fallbacks von k2cfg = Defaults in k2_box.py/k2_limits.py (Repo oder Paket)."""
    print("Teil A2: Fallback-Grenzen = Modul-Defaults")
    for basis in (os.path.join(HIER, ".."), os.path.join(HIER, "pi-host")):
        box, lim = os.path.join(basis, "k2_box.py"), os.path.join(basis, "k2_limits.py")
        if os.path.isfile(box) and os.path.isfile(lim):
            b, li = lies(box), lies(lim)
            mn = re.search(r'"check_cut_pos_x_min":\s*(-?[0-9.]+)', b)
            mx = re.search(r'"check_cut_pos_x_max":\s*(-?[0-9.]+)', b)
            xw = re.search(r"^STOCK_X_MIN_WIDE\s*=\s*(-?[0-9.]+)", li, re.M)
            check(mn and mx and float(mn.group(1)) == K.CUT_MIN_DEFAULT
                  and float(mx.group(1)) == K.CUT_MAX_DEFAULT,
                  "k2_box check_cut_pos_x_min/max = %s/%s" % (K.CUT_MIN_DEFAULT, K.CUT_MAX_DEFAULT))
            check(xw and float(xw.group(1)) == K.X_MIN_WIDE_DEFAULT,
                  "k2_limits STOCK_X_MIN_WIDE = %s" % K.X_MIN_WIDE_DEFAULT)
            return
    print("  SKIP  k2_box.py/k2_limits.py nicht gefunden")


def teil_d():
    """merge: Freigaben (allow_*) bleiben bei Wiederholung; setze; serial-Hinweise."""
    print("Teil D: Freigaben bei Wiederholung, setze, serial-Plausibilität")
    paket = ("[k2_box]\nallow_write: False\nallow_move: False\n\n[k2_motor]\nallow_write: False\n\n"
             "[gcode_macro _PI_FREIGABE]\nvariable_allow_move: True\ngcode:\n  M118 x\n\n"
             "[k2_belt mdlx]\nallow_write: True\n")
    with tempfile.TemporaryDirectory() as d:
        neu = schreibe(d, "neu.cfg", paket)
        alt = schreibe(d, "alt.cfg", paket.replace("[k2_box]\nallow_write: False", "[k2_box]\nallow_write: True")
                       .replace("[k2_motor]\nallow_write: False", "[k2_motor]\nallow_write: True")
                       .replace("variable_allow_move: True", "variable_allow_move: False")
                       .replace("[k2_belt mdlx]\nallow_write: True", "[k2_belt mdlx]\n#allow_write: False"))
        aus = os.path.join(d, "aus.cfg")
        rc, out = lauf("merge", neu, aus, "--alt", alt)
        t = lies(aus) if os.path.exists(aus) else ""
        check(rc == 0 and "[k2_box]\nallow_write: True\nallow_move: False" in t,
              "D1 [k2_box] allow_write True aus bisheriger Datei, allow_move (gleich) bleibt")
        check("[k2_motor]\nallow_write: True" in t and "variable_allow_move: False" in t,
              "D1 [k2_motor] allow_write und _PI_FREIGABE allow_move aus bisheriger Datei")
        check("[k2_belt mdlx]\nallow_write: True" in t, "D1 auskommentierte Zeile in ALT -> Paketwert")
        check("[k2_motor] allow_write <- True (bisherige Datei, Paket: False)" in out, "D1 Übernahme wird gemeldet")
        rc, out = lauf("merge", neu, aus, "--alt", neu)
        check(rc == 0 and lies(aus) == paket, "D2 ALT = Paket -> Paketstand")
        rc, out = lauf("setze", aus, "gcode_macro _PI_FREIGABE", "variable_allow_move", "False")
        check(rc == 0 and "variable_allow_move: False\ngcode:" in lies(aus), "D3 setze _PI_FREIGABE allow_move False")
        vorher = lies(aus)
        rc, out = lauf("setze", aus, "gcode_macro _PI_FREIGABE", "variable_allow_move", "False")
        check(rc == 0 and "unverändert" in out and lies(aus) == vorher, "D3 setze zweimal: unverändert")
        rc, out = lauf("setze", aus, "k2_motor", "gibt_es_nicht", "1")
        check(rc == 3 and lies(aus) == vorher, "D4 setze unbekannte Option: Exit 3, Datei unverändert")
    check(K.serial_warnungen("mcu", "/dev/ttyAMA4") == [] and K.serial_warnungen("mcu nozzle_mcu", "/dev/ttyAMA2") == [],
          "D5 Pi-UART /dev/ttyAMA4/2 (Variante B) ohne Warnung")
    check(K.serial_warnungen("k2_485", "/dev/ttyAMA4") != [] and K.serial_warnungen("mcu", "/dev/ttyUSB0") != [],
          "D5 ttyUSBn bzw. RS485 an ttyAMA weiterhin mit Hinweis")


def main():
    teil_a()
    standardwerte()
    teil_b()
    teil_c()
    teil_d()
    if FEHLER:
        print("ERGEBNIS: %d ok, %d FEHLGESCHLAGEN" % (N_OK[0], len(FEHLER)))
        for f in FEHLER:
            print("  - %s" % f)
        return 1
    print("ERGEBNIS: %d/%d BESTANDEN" % (N_OK[0], N_OK[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
