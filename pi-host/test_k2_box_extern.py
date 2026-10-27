#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_k2_box_extern.py – Tests „Externe Spule“ in k2_box (19.10.2026).

Ohne Klipper, ohne Hardware: nutzt die Fakes aus test_k2_box.py
(FakeBoxBus, FakePrinter, FakeConfig, FakeGcmd, Jinja-Render der Makros).
Belege: Live-Mitschnitt externe Spule 19.10.2026 (Laden live, Entladen,
Stock-Makros, zweites Laden), Stock-Filament-Rack-Logik (RFID 0x11, Spülen
der externen Spule), README-k2_box.md §18.

Prüft:
1. Quellen-Datei (filament_source_file): lesen/schreiben/fehlt/kaputt -> CFS.
2. KTWO_BOX_SOURCE in der Hülle (persistent, Sperren Druck/Kette, Meldungen).
3. Quelle EXTERN: ALLE CFS-Aktionen abgelehnt, NICHTS an die Box gesendet
   (LOAD/UNLOAD/PRELOAD, T<n>-Kette, RUNOUT, AUTO_REFILL, ENDING PURGE,
   END_PRINT, ERROR_CLEAR, Nachholen im RESUME, Controller-Sperre); lesendes
   Polling + KTWO_BOX_RFID bleiben erlaubt. 3b: Druck mit externer Spule
   sendet wie Stock live (§9) genau 0x0d [0f 00/01] + 0x0f [01/00].
4. T<n>-Logik: erstes T im Druck = kein Wechsel, anderes T -> PAUSE.
5. Makros (Jinja2): T0..T15/M8200 extern, LOAD_FILAMENT/UNLOAD_FILAMENT/
   QUELLE_*/SET_EXTERNAL_MATERIAL, Registrierung + Upstream-Namensregel.
6. Laden als Skriptfolge (gerenderte Makrozeilen ausgeführt): Live-Werte
   §1/§7 (160 mm = 90 + 70, 240 °C -> 220 °C, F575/F690), Kopfsensor-
   Prüfung, gespeichertes Material, TEMP=, ohne DB, Plan, Fehler, Pause.
7. Entladen als Skriptfolge (Heizen, Schacht verlassen, Schnitt, E-10 F360).
8. Material-Lookup (ID/Name/Typ/Generic/mehrdeutig) + KTWO_BOX_EXTERN_MATERIAL.
9. RFID 0x11 (optional): genau ein Frame 0x11 [02], Sendeschutz, Parser,
   überschreibt das gespeicherte Material, keine Etikett-Rohdaten in Meldungen.
10. Config/Doku-Verdrahtung.  11. Python-3.9-Syntax.
"""

import json
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import test_k2_box as TB  # noqa: E402  (setzt HOME auf ein Wegwerf-Verzeichnis)

K = TB.K
FakeGcmd = TB.FakeGcmd
MECH = (0x04, 0x0D, 0x0F, 0x10, 0x11)   # Box-Schreibbefehle (Filament/Modus)


# =====================================================================
# Helfer
# =====================================================================

def _tmpdir():
    d = tempfile.mkdtemp(prefix="k2box-extern-")
    TB._TMP_DIRS.append(d)
    return d


def ext_db_data():
    """Synthetische Mini-DB (Format wie Creality result.list, KEINE echten
    Inhalte): zwei PLA (Hyper/Generic), zwei PETG, TPU ohne Temperatur."""
    def e(dbid, name, mtype, temp, tmax, vol):
        return {"base": {"id": dbid, "name": name, "meterialType": mtype,
                         "maxTemp": tmax},
                "kvParam": {"nozzle_temperature": temp,
                            "filament_max_volumetric_speed": vol}}
    lst = [e("90001", "Hyper PLA", "PLA", "220", 240, "23"),
           e("90002", "Generic PLA", "PLA", "220", 240, "14"),
           e("90003", "Generic PETG", "PETG", "250", 270, "16"),
           e("90004", "Test TPU", "TPU", None, 240, "2"),
           e("90005", "Hyper PETG", "PETG", "245", 270, "18")]
    return {"code": 0, "msg": "ok", "result": {"list": lst, "count": len(lst)}}


def ext_db():
    return K._MDB.MaterialDB(ext_db_data())


class RfidBus(TB.FakeBoxBus):
    """FakeBoxBus + RFID-Leser 0x11 (externe Spule): fun 0x02 -> 40 ASCII
    (tag) bzw. Timeout (tag None)."""

    def __init__(self, tag=None, **kw):
        TB.FakeBoxBus.__init__(self, **kw)
        self.rack_tag = tag

    def _handle(self, addr, fun, payload):
        if addr == K.EXTERN_RFID_ADDR:
            if self.rack_tag is None:
                raise TimeoutError("RFID 0x11: keine Antwort (Test)")
            return addr, 0x00, fun, self.rack_tag.encode("ascii")
        return TB.FakeBoxBus._handle(self, addr, fun, payload)


def ext_hull(db=True, source_file="", extern=True, bus=None, **opt):
    """Klipper-Hülle wie test_k2_box.build_hull (+ Lüfter-Pins, gcode_arcs),
    optional Material-DB und Quelle EXTERN."""
    bus = bus if bus is not None else TB.FakeBoxBus()
    printer = TB.FakePrinter(bus, with_motion=True)
    printer.objects["output_pin fan0"] = TB._PinObj(0.4, 255.0)
    printer.objects["output_pin fan0_en"] = TB._PinObj(1.0, 1.0)
    printer.objects["gcode_arcs"] = object()
    opts = TB.test_options(allow_move="True",
                           filament_source_file=source_file)
    opts.update(opt)
    mod = K.PrinterK2Box(TB.FakeConfig(printer, opts))
    printer.fire("klippy:connect")
    mod._bus = bus
    if db:
        mod.material_db = ext_db()
        mod.material_db_info = "Test-DB"
    if extern:
        mod.source = K.SOURCE_EXTERN
    return bus, printer, mod


def call(fn, params=None):
    g = FakeGcmd(params or {})
    fn(g)
    return g.infos


def raises(fn, params, *texts):
    g = FakeGcmd(params or {})
    try:
        fn(g)
    except ValueError as exc:     # FakeGcmd.error -> ValueError
        for t in texts:
            assert t in str(exc), (t, str(exc))
        return str(exc)
    raise AssertionError("%s: kein G-Code-Fehler (%s)" % (
        getattr(fn, "__name__", fn), g.infos))


def mech(bus, n0=0):
    return [c for c in bus.calls[n0:] if c[1] in MECH]


def moves(lines):
    return [l for l in lines if l.split()[0] in (
        "G0", "G1", "G2", "G3", "M104", "M109", "TEMPERATURE_WAIT")]


# --- Makros rendern + ausführen ----------------------------------------
_ENV = None


def _env():
    global _ENV
    if _ENV is None:
        _ENV = TB._render_env()
    return _ENV


class _Raise(Exception):
    pass


def render(name, printer, params=None, rawparams=""):
    env, macros, _kcfg, _param = _env()
    msgs = []

    def _info(m):
        msgs.append(m)
        return ""

    def _err(m):
        raise _Raise(m)
    out = env.from_string(macros[name][0]).render(
        params=dict(params or {}), rawparams=rawparams, printer=printer,
        action_respond_info=_info, action_raise_error=_err)
    return [l.strip() for l in out.split("\n")
            if l.strip() and not l.strip().startswith("#")], msgs


def macro_printer(mod=None, k2=None, fr=True, homed="xyz", z=50.0,
                  with_k2=True):
    _env_, _m, _kcfg, param = _env()
    pr = {"gcode_macro _PI_PARAM": dict(param),
          "gcode_macro _PI_FREIGABE": {"allow_move": fr},
          "gcode_macro M8200": {"tnn": "", "tool": -1},
          "gcode_macro _KTWO_FAN_SAVE": {"fan2_value": 0.0},
          "output_pin fan2": {"value": 0.0},
          "pause_resume": {"is_paused": False},
          "print_stats": {"state": "standby"},
          "toolhead": {"homed_axes": homed},
          "gcode_move": {"gcode_position": {"x": 200.0, "y": 200.0, "z": z,
                                            "e": 0.0}}}
    if with_k2:
        st = mod.get_status() if mod is not None else {
            "change_enabled": True, "chain_error": "", "allow_move": True,
            "source": "extern"}
        st = dict(st)
        st.update(k2 or {})
        pr["k2_box"] = st
    return pr


def run_macro(mod, printer, name, rawparams="", homed="xyz", z=50.0, fr=True):
    """Makro mit dem aktuellen k2_box-Status rendern und Zeile für Zeile
    über FakeGcode ausführen (KTWO_* -> Modul, G-Code -> Simulation)."""
    lines, msgs = render(name, macro_printer(mod, fr=fr, homed=homed, z=z),
                         rawparams=rawparams)
    for ln in lines:
        printer.gcode.run_script_from_command(ln)
    return lines, msgs


# =====================================================================
# 1. Quellen-Datei
# =====================================================================

def test_quelle_datei():
    d = _tmpdir()
    p = os.path.join(d, "k2", "filament_source.json")
    assert K.load_filament_source("") == (None, "aus", "")
    st, status, info = K.load_filament_source(p)
    assert st is None and status == "fehlt" and "Quelle CFS" in info, info
    mat = {"type": "90001", "color": "0ffffff", "name": "Hyper PLA",
           "temp": 215, "origin": "hand"}
    K.save_filament_source(p, "EXTERN", mat)
    assert not os.path.exists(p + ".tmp")
    raw = json.load(open(p, encoding="utf-8"))
    assert raw == {"source": "extern", "extern_material": {
        "type": "90001", "color": "0FFFFFF", "name": "Hyper PLA",
        "temp": 215.0, "origin": "hand"}}, raw
    st, status, info = K.load_filament_source(p)
    assert status == "geladen" and st["source"] == "extern" \
        and st["material"]["color"] == "0FFFFFF", (st, info)
    # andere Top-Level-Schlüssel bleiben
    raw["notiz"] = 1
    json.dump(raw, open(p, "w", encoding="utf-8"))
    K.save_filament_source(p, "cfs", None)
    raw = json.load(open(p, encoding="utf-8"))
    assert raw == {"source": "cfs", "extern_material": None, "notiz": 1}, raw
    # kaputt -> CFS + beiseitegelegt, nie überschrieben
    for bad in ("{kein json", '{"source": "irgendwas"}', '["extern"]',
                '{"source": "extern", "extern_material": {"type": "90001", '
                '"color": "rot"}}',
                '{"source": "extern", "extern_material": {"type": "90001", '
                '"temp": 900}}'):
        open(p, "w", encoding="utf-8").write(bad)
        st, status, info = K.load_filament_source(p)
        assert st is None and status == "fehler" and "Quelle CFS" in info, \
            (bad, info)
        assert not os.path.exists(p), "kaputte Datei muss beiseitegelegt sein"
        assert [f for f in os.listdir(os.path.dirname(p)) if ".defekt-" in f]
    # Material ohne Typ und Name = kein Material
    open(p, "w", encoding="utf-8").write(
        '{"source": "extern", "extern_material": {"color": "0FF0000"}}')
    st, status, _i = K.load_filament_source(p)
    assert status == "geladen" and st["material"] is None, st
    # Normalisierer
    assert K.normalize_source(" Extern ") == "extern"
    assert K.normalize_source("CFS") == "cfs"
    for bad in ("", "rack", None):
        try:
            K.normalize_source(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("Quelle %r angenommen" % (bad,))
    assert K.normalize_extern_name(' "PLA" ') == "PLA"
    for bad in ("PLA;M112", "#PLA", "x" * 41):
        try:
            K.normalize_extern_name(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("Name %r angenommen" % (bad,))
    assert K.normalize_extern_temp(None) is None
    for bad in (99, 451, "x", True, float("nan")):
        try:
            K.normalize_extern_temp(bad)
        except ValueError:
            pass
        else:
            raise AssertionError("Temperatur %r angenommen" % (bad,))
    return ("aus/fehlt/geladen, atomar ohne .tmp, fremde Schlüssel bleiben, 5 "
            "Kaputt-Fälle -> CFS + .defekt, Normalisierer")


# =====================================================================
# 2. KTWO_BOX_SOURCE in der Hülle
# =====================================================================

def test_quelle_huelle():
    d = _tmpdir()
    p = os.path.join(d, "filament_source.json")
    K.save_filament_source(p, "extern", {"type": "90001", "color": "0FFFFFF",
                                         "name": "Hyper PLA"})
    bus, printer, mod = ext_hull(source_file=p, extern=False)
    assert mod.source == "extern" and mod.source_load == "geladen", mod.source
    st = mod.get_status()
    assert st["source"] == "extern" and st["extern_material"]["type"] == "90001"
    assert any(m.startswith("// [k2_box]") and "Filamentquelle" in m
               for m in printer.gcode.raw), printer.gcode.raw
    n0 = len(bus.calls)
    infos = call(mod.cmd_source)
    assert "EXTERN" in infos[0] and "gesperrt" in infos[0], infos
    # Umschalten persistent
    infos = call(mod.cmd_source, {"SOURCE": "CFS"})
    assert mod.source == "cfs" and json.load(open(p))["source"] == "cfs"
    assert "gespeichert" in infos[-1], infos
    bus.head = True
    infos = call(mod.cmd_source, {"SOURCE": "extern"})
    assert mod.source == "extern" and json.load(open(p))["source"] == "extern"
    assert "Kopfsensor meldet Filament" in infos[-1], infos
    assert json.load(open(p))["extern_material"]["name"] == "Hyper PLA"
    raises(mod.cmd_source, {"SOURCE": "rack"}, "EXTERN oder CFS")
    # Sperren: Druck läuft, Kette aktiv; Pause erlaubt
    printer.set_printing(True)
    raises(mod.cmd_source, {"SOURCE": "CFS"}, "Druck läuft", "bleibt EXTERN")
    printer.paused, printer.print_state = True, "paused"
    printer._refresh()
    call(mod.cmd_source, {"SOURCE": "CFS"})
    assert mod.source == "cfs"
    printer.paused = False
    printer.set_printing(False)
    mod.chain = mod._new_chain()
    mod.chain.update({"active": True, "tnn": "T1B"})
    raises(mod.cmd_source, {"SOURCE": "EXTERN"}, "Vorgang läuft", "T1B")
    mod.chain = mod._new_chain()
    assert mod.source == "cfs" and not mech(bus, n0) and len(bus.calls) == n0, \
        "Umschalten sendet nichts"
    # Neustart: Datei kaputt -> CFS + WARNUNG
    open(p, "w").write("{kaputt")
    bus2, printer2, mod2 = ext_hull(source_file=p, extern=False)
    assert mod2.source == "cfs" and mod2.source_load == "fehler"
    assert any(m.startswith("// [k2_box] WARNUNG") and "Quelle CFS" in m
               for m in printer2.gcode.raw), printer2.gcode.raw
    # Persistenz aus
    bus3, printer3, mod3 = ext_hull(extern=False)
    infos = call(mod3.cmd_source, {"SOURCE": "EXTERN"})
    assert mod3.source == "extern" and "nur bis zum Neustart" in infos[-1]
    return ("Start aus Datei, Anzeige, CFS<->EXTERN gespeichert, Hinweis "
            "Kopfsensor, Sperre Druck/Kette, Pause frei, kaputt -> CFS, aus")


# =====================================================================
# 3. Quelle EXTERN: alle CFS-Aktionen gesperrt, nichts gesendet
# =====================================================================

def test_extern_sperren_nichts_gesendet():
    bus, printer, mod = ext_hull(allow_write="True",
                                 resume_redo_change="True")
    ctl = mod._ensure_controller()
    n0 = len(bus.calls)
    for fn, params in ((mod.cmd_load, {"SLOT": "B"}),
                       (mod.cmd_load, {"SLOT": "B", "DRY_RUN": "1"}),
                       (mod.cmd_unload, {"SLOT": "B"}),
                       (mod.cmd_preload, {"SLOT": "A", "OP": "1"}),
                       (mod.cmd_preload, {"SLOT": "A", "OP": "2", "FORCE": "1"}),
                       (mod.cmd_pre_opt, {"TNN": "T1B", "SOURCE": "T"}),
                       (mod.cmd_retrude, {}),
                       (mod.cmd_extrude, {"TNN": "T1B"}),
                       (mod.cmd_flush, {"TNN": "T1B"}),
                       (mod.cmd_auto_refill, {"ENABLE": "1"}),
                       (mod.cmd_ending, {"PURGE": "1"})):
        msg = raises(fn, params, "Quelle")
        assert "EXTERN" in msg and "nichts gesendet" in msg or \
            "kein CFS-Nachladen" in msg, msg
    assert len(bus.calls) == n0, bus.calls[n0:]
    assert not mod.chain.get("active") and not mod.auto_refill
    call(mod.cmd_auto_refill, {"ENABLE": "0"})
    # T<n>-Kette aus der Druckdatei (alte Makros/M8200): PRE_OPT pausiert,
    # Folgeschritte übersprungen, END_OPT räumt auf – keine Bewegung
    printer.set_printing(True)
    n_sc = len(printer.gcode.scripts)
    infos = TB.run_chain(mod, "T1B")
    sc = printer.gcode.scripts[n_sc:]
    assert "PAUSE" in sc and printer.paused, sc
    assert not moves(sc), moves(sc)
    assert sum("übersprungen" in m for m in infos) >= 4, infos
    assert not mod.chain.get("active") and not mod.chain_error
    assert any("Quelle ist EXTERN" in r for r in printer.gcode.raw)
    # Runout im Druck: normale Pause, kein Nachladen
    infos = call(mod.cmd_runout)
    assert "Quelle EXTERN" in mod.runout_last and printer.paused
    assert "RESUME" not in printer.gcode.scripts[n_sc:]
    # Druckende / Druckzustand / Fehler quittieren / Nachholen
    printer.paused = False
    printer.set_printing(False)
    infos = call(mod.cmd_end_print, {"UNLOAD": "1"})
    assert "Quelle EXTERN" in infos[-1], infos
    mod.ending_material = {"slot": "A", "tnn": "T1A", "why": "Test"}
    infos = call(mod.cmd_end_print, {})
    assert "kein Entladen" in infos[-1] and mod.ending_material, infos
    mod.ending_material = None
    mod.chain_error = "EXTRUDE fehlgeschlagen: Test"
    infos = call(mod.cmd_error_clear)
    assert "0x04 unterdrückt" in infos[-1] and not mod.chain_error, infos
    mod.chain_error = "x"
    mod._reset_print_state(FakeGcmd(), "Test")
    assert not mod.chain_error
    printer.paused, printer.print_state = True, "paused"
    printer._refresh()
    bus.head = False
    mod.aborted = {"step": "EXTRUDE", "tnn": "T1B", "source": "T"}
    raises(mod.cmd_resume_check, {"MOVE": "1"}, "Quelle EXTERN",
           "LOAD_FILAMENT")
    printer.paused = False
    printer.set_printing(False)
    mod.aborted = None
    # Restmaterial-Auslöser im Poll: bei Quelle EXTERN nie
    mod._note_ending({"kind": "state", "state": K._BOX.BOX_STATE_PRINT,
                      "frame_status": K._BOX.STATUS_RUNOUT})
    assert mod.ending_material is None
    assert len(bus.calls) == n0, ("Sperren senden nichts", bus.calls[n0:])
    # Controller-Sperre (auch direkte Aufrufe senden nichts); do_print_state
    # ist die enge Ausnahme (test_extern_druck_busmitschnitt)
    for fn, args, kw in ((ctl.do_extrude, ("B",), {}),
                         (ctl.do_retrude, ("B",), {}),
                         (ctl.do_preload, ("A", 1), {}),
                         (ctl.do_preload, ("D", 0), {})):
        try:
            fn(*args, **kw)
        except PermissionError as exc:
            assert "Quelle EXTERN" in str(exc) and "nichts gesendet" in str(exc)
        else:
            raise AssertionError("%s ohne Sperre" % fn.__name__)
    assert len(bus.calls) == n0 and not ctl.print_hold
    # Lesen bleibt erlaubt: 0x0a-Poll, KTWO_BOX_STATUS, KTWO_BOX_RFID
    ctl.poll_status()
    infos = call(mod.cmd_status)
    assert "Quelle=EXTERN" in infos[-1], infos
    call(mod.cmd_rfid, {"SLOT": "ALL"})
    funs = sorted({c[1] for c in bus.calls[n0:]})
    assert funs == [0x02, 0x03, 0x0A], funs
    # zurück auf CFS: LOAD sendet wieder (Düse als heiß angenommen)
    call(mod.cmd_source, {"SOURCE": "CFS"})
    mod._cold_reason = lambda: None
    n1 = len(bus.calls)
    call(mod.cmd_load, {"SLOT": "B"})
    assert [c for c in bus.calls[n1:] if c[1] == 0x10], "CFS wieder frei"
    return ("LOAD/UNLOAD/PRELOAD/PRE_OPT/RETRUDE/EXTRUDE/FLUSH/AUTO_REFILL/"
            "PURGE -> Fehler; T-Kette im Druck -> PAUSE ohne Bewegung; Runout "
            "= Pause; END_PRINT/ERROR_CLEAR/Nachholen ohne Frame; Controller-"
            "Sperre; Poll/STATUS/RFID lesend frei; CFS wieder frei")


# =====================================================================
# 3b. Druck mit externer Spule: Bus-Mitschnitt wie Stock live (§8/§9)
# =====================================================================

def test_extern_druck_busmitschnitt():
    """Stock live: SD-Start 0x0d [0f 00] +
    0x0f [01], Druckende Z+2, 0x0d [0f 01] + 0x0f [00], kein Schnitt/
    Entladen, kein 0x04/Einzug – genau wie bei CFS-Drucken."""
    bus, printer, mod = ext_hull(allow_write="True")
    ctl = mod._ensure_controller()
    # enge Ausnahme: nur genau diese vier Frames
    wb = mod._extern_write_block
    for ok in ([(0x0D, (0x0F, 0x00)), (0x0F, (0x01,))],
               [(0x0D, (0x0F, 0x01)), (0x0F, (0x00,))]):
        assert wb(ok) is None, ok
    for bad in (None, [], [(0x04, (0x00, 0x01))], [(0x0D, (0x01, 0x00))],
                [(0x0D, (0x0F, 0x02))], [(0x0F, (0x02,))],
                [(0x0D, (0x0F, 0x00)), (0x10, (0x01, 0x00, 0x00))],
                [(0x11, (0x01, 0x01))]):
        assert wb(bad) and "Quelle EXTERN" in wb(bad), bad
    mod.source = K.SOURCE_CFS
    assert wb([(0x10, (0x01, 0x00, 0x00))]) is None
    mod.source = K.SOURCE_EXTERN
    # Druckablauf wie macros.cfg: START_PRINT -> KTWO_BOX_PRINT_START, Datei
    # mit T0/T0/M8200 L I0, END_PRINT -> KTWO_BOX_END_PRINT + PRINT_END
    n0, s0 = len(bus.calls), len(printer.gcode.scripts)
    printer.set_printing(True)
    printer.objects["print_stats"].print_start_time = 300.0
    call(mod.cmd_print_start)
    assert ctl.print_hold
    ctl.poll_status()                                 # lesend
    call(mod.cmd_extern_tool, {"T": "0"})
    call(mod.cmd_extern_tool, {"T": "0"})
    lines, _m = render("M8200", macro_printer(mod), {"L": "", "I": "0"})
    assert lines == ["KTWO_BOX_EXTERN_TOOL T=0"]
    printer.gcode.run_script_from_command(lines[0])
    bus.head = True                                   # Filament bleibt im Kopf
    infos = call(mod.cmd_end_print, {})
    assert "kein Entladen" in infos[-1], infos
    call(mod.cmd_print_end)
    assert not ctl.print_hold and not printer.paused
    got = [(c[1], tuple(c[2])) for c in bus.calls[n0:]]
    assert got == [(0x0D, (0x0F, 0x00)), (0x0F, (0x01,)), (0x0A, ()),
                   (0x0D, (0x0F, 0x01)), (0x0F, (0x00,))], got
    assert not moves(printer.gcode.scripts[s0:]), "kein Schnitt/Entladen"
    assert not [m for m in printer.gcode.raw if "fehlgeschlagen" in m]
    # _PI_RESET_STATE-Weg (print_hold) und ohne allow_write = nur Plan
    bus2, printer2, mod2 = ext_hull(allow_write="False")
    n = len(bus2.calls)
    infos = call(mod2.cmd_print_start) + call(mod2.cmd_print_end)
    assert len(bus2.calls) == n and sum("PLAN" in m for m in infos) == 2
    # Makros: START_PRINT/END_PRINT rufen die Bausteine auch bei EXTERN
    env, macros, _k, param = _env()
    pr = macro_printer(mod, k2={"change_enabled": True, "source": "extern",
                                "end_print_unload": True,
                                "ending_material": False})
    pr["toolhead"].update({"position": {"z": 20.0}})
    pr["gcode_macro _PI_PARAM"] = dict(param)
    lines, _m = render("END_PRINT", pr)
    assert lines.index("KTWO_BOX_END_PRINT") < lines.index("KTWO_BOX_PRINT_END") \
        and lines.index("G1 Z2 F600") < lines.index("KTWO_BOX_END_PRINT"), lines
    lines, _m = render("START_PRINT", pr, {"EXTRUDER_TEMP": "220",
                                           "BED_TEMP": "60"})
    assert "KTWO_BOX_PRINT_START" in lines, lines
    return ("Ausnahme genau 0d[0f 00/01]+0f[01/00] (0x04/0x10/0x11/andere "
            "Masken gesperrt); Extern-Druck: 0d[0f 00], 0f[01], 0a, 0d[0f 01], "
            "0f[00] – kein Schnitt/Entladen/Einzug; ohne allow_write Plan; "
            "START/END_PRINT-Makros")


# =====================================================================
# 4. T<n>-Logik
# =====================================================================

def test_t_logik():
    bus, printer, mod = ext_hull()
    n0 = len(bus.calls)
    raises(mod.cmd_extern_tool, {"T": "1"}, "T1 gesperrt", "Quelle ist EXTERN")
    raises(mod.cmd_extern_tool, {"T": "16"}, "0..15")
    printer.set_printing(True)
    ps = printer.objects["print_stats"]
    ps.print_start_time = 100.0
    infos = call(mod.cmd_extern_tool, {"T": "1"})
    assert "erstes Werkzeug" in infos[0] and not printer.paused, infos
    assert mod.get_status()["extern_tool"] == 1
    infos = call(mod.cmd_extern_tool, {"T": "1"})
    assert "unverändert" in infos[0] and not printer.paused, infos
    call(mod.cmd_extern_tool, {"T": "2"})
    assert printer.paused and printer.gcode.scripts[-1] == "PAUSE"
    assert any("Datei verlangt Werkzeugwechsel (T1 -> T2), Quelle ist extern"
               in r for r in printer.gcode.raw), printer.gcode.raw
    assert mod.get_status()["extern_tool"] == 2
    # in der Pause: von Hand -> Fehler
    raises(mod.cmd_extern_tool, {"T": "3"}, "Quelle ist EXTERN")
    printer.sim("RESUME")
    infos = call(mod.cmd_extern_tool, {"T": "2"})
    assert "unverändert" in infos[0] and not printer.paused
    # neuer Druck (print_start_time) -> erstes Werkzeug ohne Pause
    ps.print_start_time = 200.0
    infos = call(mod.cmd_extern_tool, {"T": "3"})
    assert "erstes Werkzeug" in infos[0] and not printer.paused, infos
    # Reset bei Datei-Reset / PRINT_START / CANCEL_RESET
    printer.fire("virtual_sdcard:reset_file")
    assert mod._extern_tool is None
    call(mod.cmd_extern_tool, {"T": "0"})
    call(mod.cmd_print_start)
    assert mod._extern_tool is None
    call(mod.cmd_extern_tool, {"T": "5"})
    printer.set_printing(False)
    call(mod.cmd_cancel_reset)
    assert mod._extern_tool is None
    # Quelle CFS: ohne Wirkung
    mod.source = K.SOURCE_CFS
    infos = call(mod.cmd_extern_tool, {"T": "1"})
    assert "ohne Wirkung" in infos[0]
    got = [(c[1], tuple(c[2])) for c in bus.calls[n0:]]
    assert got == [(0x0D, (0x0F, 0x00)), (0x0F, (0x01,))], \
        ("T-Logik sendet nichts, nur PRINT_START wie Stock", got)
    return ("außerhalb Druck Fehler; 1. T kein Wechsel, gleiches T nichts, "
            "T1->T2 PAUSE + Meldung, Pause = Fehler, neuer Druck/Resets, CFS "
            "ohne Wirkung; Frames nur 0x0d/0x0f aus PRINT_START")


# =====================================================================
# 5. Makros (Jinja2)
# =====================================================================

def test_makros_extern():
    try:
        import jinja2  # noqa: F401
    except Exception:
        return "SKIP (jinja2 fehlt)"
    env, macros, _kcfg, _p = _env()
    for n in ("SET_FILAMENT_SOURCE", "LOAD_FILAMENT", "UNLOAD_FILAMENT",
              "SET_EXTERNAL_MATERIAL"):
        assert n in macros, "Makro %s fehlt" % n
    ext = macro_printer(k2={"source": "extern"})
    cfs = macro_printer(k2={"source": "cfs"})
    for i in range(1, 16):
        assert render("T%d" % i, ext)[0] == ["_KTWO_TOOL_CHANGE T=%d" % i]
        assert render("_KTWO_TOOL_CHANGE", ext, {"T": str(i)})[0] == [
            "KTWO_BOX_EXTERN_TOOL T=%d" % i]
    assert render("T0", ext)[0] == ["KTWO_BOX_EXTERN_TOOL T=0"]
    assert render("T0", macro_printer(k2={"source": "extern",
                                          "change_enabled": False}))[0] == [
        "KTWO_BOX_EXTERN_TOOL T=0"]
    lines, _m = render("_KTWO_TOOL_CHANGE", cfs, {"T": "1"})
    assert lines[1] == "KTWO_BOX_PRE_OPT TNN=T1B SOURCE=T", lines
    for params, want in (({"P": "", "S": "5"}, ["KTWO_BOX_EXTERN_TOOL T=5"]),
                         ({"L": "", "I": "3"}, ["KTWO_BOX_EXTERN_TOOL T=3"]),
                         ({"L": "", "I": "=7"}, ["KTWO_BOX_EXTERN_TOOL T=7"]),
                         ({"L": ""}, ["KTWO_BOX_EXTERN_TOOL T=0"]),
                         ({"P": ""}, []), ({"C": "", "S": "0"}, []),
                         ({"R": ""}, []), ({"W": ""}, []), ({"O": ""}, []),
                         ({"F": "", "S": "600", "L": "100"}, [])):
        lines, msgs = render("M8200", ext, params)
        assert lines == want, (params, lines)
        if not want:
            assert msgs and "Quelle EXTERN" in msgs[0], msgs
    assert render("M8200", cfs, {"C": ""})[0] == ["KTWO_BOX_CUT"]
    # LOAD_FILAMENT / UNLOAD_FILAMENT: Reihenfolge
    load = ["KTWO_BOX_GO_EXTRUDE_POS", "_KTWO_FAN_SAVE",
            "KTWO_BOX_EXTERN_FLUSH", "KTWO_BOX_SAFE_POS",
            "KTWO_BOX_EXTERN_END", "_KTWO_FAN_RESTORE"]
    # Live 26.10.2026: danach Extruder freigeben (der bestromte Closed-Loop-
    # Extruder hielt das abgeschnittene Filament fest)
    unload = ["KTWO_BOX_GO_EXTRUDE_POS", "KTWO_BOX_EXTERN_HEAT",
              "KTWO_BOX_SAFE_POS", "KTWO_BOX_CUT", "KTWO_BOX_EXTERN_RETRACT",
              "KTWO_BOX_SAFE_POS", "KTWO_BOX_EXTERN_END",
              "SET_STEPPER_ENABLE STEPPER=extruder ENABLE=0",
              '_PI_MELDUNG MSG="UNLOAD_FILAMENT: Extruder-Motor freigegeben '
              '– Filament jetzt von Hand herausziehen."']
    k2 = {"source": "extern", "allow_move": True}
    assert render("LOAD_FILAMENT", macro_printer(k2=k2))[0] == [
        "KTWO_BOX_EXTERN_BEGIN OP=LADEN"] + load
    assert render("UNLOAD_FILAMENT", macro_printer(k2=k2))[0] == [
        "KTWO_BOX_EXTERN_BEGIN OP=ENTLADEN"] + unload
    lines, _m = render("LOAD_FILAMENT", macro_printer(k2=k2, homed="z", z=2.0),
                       rawparams='MATERIAL="Hyper PLA" TEMP=215')
    assert lines[:4] == ['KTWO_BOX_EXTERN_BEGIN OP=LADEN MATERIAL="Hyper PLA" '
                         'TEMP=215', "G28 X Y", "G90", "G1 Z5.0 F600"], lines
    lines, _m = render("UNLOAD_FILAMENT", macro_printer(k2=k2, homed=""))
    assert lines[1] == "G28 X Y" and "G1 Z5.0 F600" not in lines, lines
    # Homing nur bei Bedarf (Stock §7: zweites Laden ohne Homing)
    assert "G28 X Y" not in render("LOAD_FILAMENT", macro_printer(k2=k2))[0]
    # [k2_box] allow_move False -> nur Plan, kein G28; _PI_FREIGABE aus ->
    # Fehler vor jeder Bewegung; ohne [k2_box] -> Fehler
    lines, _m = render("LOAD_FILAMENT", macro_printer(
        k2={"source": "extern", "allow_move": False}, homed="", fr=False))
    assert "G28 X Y" not in lines and lines[0].startswith(
        "KTWO_BOX_EXTERN_BEGIN"), lines
    lines, _m = render("UNLOAD_FILAMENT", macro_printer(
        k2={"source": "extern", "allow_move": False}, homed="", fr=False))
    assert not [l for l in lines if l.startswith("SET_STEPPER_ENABLE")], \
        "Plan ohne Bewegungsfreigabe: Extruder bleibt, wie er ist"
    for name in ("LOAD_FILAMENT", "UNLOAD_FILAMENT"):
        for pr, txt in ((macro_printer(k2=k2, fr=False), "_PI_FREIGABE"),
                        (macro_printer(with_k2=False), "[k2_box] fehlt")):
            try:
                render(name, pr)
            except _Raise as exc:
                assert txt in str(exc), exc
            else:
                raise AssertionError("%s ohne Sperre" % name)
    for src, want in (("EXTERNAL", "EXTERN"), ("external", "EXTERN"),
                      ("EXTERN", "EXTERN"), ("CFS", "CFS")):
        assert render("SET_FILAMENT_SOURCE", ext, {"SOURCE": src})[0] == [
            "KTWO_BOX_SOURCE SOURCE=%s" % want], src
    try:
        render("SET_FILAMENT_SOURCE", ext)
    except _Raise as exc:
        assert "SOURCE=CFS oder SOURCE=EXTERNAL" in str(exc), exc
    else:
        raise AssertionError("SET_FILAMENT_SOURCE ohne SOURCE ohne Fehler")
    assert render("SET_EXTERNAL_MATERIAL", ext, rawparams="MATERIAL=PLA")[0] == [
        "KTWO_BOX_EXTERN_MATERIAL MATERIAL=PLA"]
    # registriert + Upstream-Namensregel (keine Ziffern, kein K2_)
    bus, printer, mod = ext_hull()
    import re as _re
    used = set()
    for src, _v in macros.values():
        used.update(_re.findall(r"KTWO_(?:BOX|EXTERN)_[A-Z_]*[A-Z]", src))
    pcfg = open(os.path.join(_HERE, "config-pi", "printer.cfg"),
                encoding="utf-8").read()
    used.update(_re.findall(r"KTWO_EXTERN_[A-Z_]*[A-Z]", pcfg))
    new = ("KTWO_BOX_SOURCE", "KTWO_BOX_EXTERN_MATERIAL", "KTWO_BOX_EXTERN_TOOL",
           "KTWO_BOX_EXTERN_BEGIN", "KTWO_BOX_EXTERN_FLUSH",
           "KTWO_BOX_EXTERN_HEAT", "KTWO_BOX_EXTERN_RETRACT",
           "KTWO_BOX_EXTERN_END", "KTWO_EXTERN_RFID")
    assert set(new) <= used, sorted(set(new) - used)
    missing = sorted(u for u in used if u not in printer.gcode.commands)
    assert not missing, missing
    for n in new + ("SET_FILAMENT_SOURCE", "LOAD_FILAMENT",
                    "UNLOAD_FILAMENT", "SET_EXTERNAL_MATERIAL"):
        assert TB.upstream_gcode_name_ok(n), n
    return ("T0..T15/_KTWO_TOOL_CHANGE/M8200 extern -> KTWO_BOX_EXTERN_TOOL, "
            "CFS unverändert; LADEN/ENTLADEN-Reihenfolge, G28 nur ohne "
            "Referenz, Z5, Sperren; %d Befehle registriert" % len(used))


# =====================================================================
# 6. Laden als Skriptfolge
# =====================================================================

def _load_hull(**opt):
    o = dict(allow_write="False")       # extern braucht kein allow_write
    o.update(opt)
    bus, printer, mod = ext_hull(**o)
    mod.remain_material = {"type": "90001", "color": "0FFFFFF", "tnn": "T1A"}
    bus.head = True
    mod._now = lambda: 1000.5
    return bus, printer, mod


def test_laden_ablauf_live():
    # §7 live: zweites Laden ohne Eingabe -> gespeichertes Material (Hyper
    # PLA weiß wie die Düse), gleiche Farbe: V 60 -> 101,25 -> 160 = [90, 70]
    bus, printer, mod = _load_hull()
    mod.extern_material = {"type": "90001", "color": "0FFFFFF",
                           "name": "Hyper PLA", "temp": None, "origin": "hand"}
    n0, s0 = len(bus.calls), len(printer.gcode.scripts)
    lines, _m = run_macro(mod, printer, "LOAD_FILAMENT")
    sc = printer.gcode.scripts[s0:]
    assert "G28 X Y" not in lines, "kein Homing, war gehomt (§7)"
    assert len(bus.calls) == n0, ("kein Box-Frame", bus.calls[n0:])
    F = K._FLUSH
    vol, fl = F.color_flush_len([255, 255, 255], [255, 255, 255],
                                K.flush_cfg(mod.cfg))
    assert vol == 60 and abs(fl - 101.25) < 1e-9, (vol, fl)
    assert any("FLUSH-Plan extern: 160 mm (90 + 70;" in m
               for m in printer.gcode.infos), printer.gcode.infos
    ext = [l for l in sc if l.startswith("G1 E")]
    assert ext == ["G1 E18 F575", "G1 E72 F690", "G1 E14 F575",
                   "G1 E56 F575"], ext
    i_heat = sc.index("M109 S220")
    i_max = sc.index("M104 S240")
    assert i_heat < i_max < sc.index("TEMPERATURE_WAIT SENSOR=extruder "
                                     "MINIMUM=235") < sc.index(ext[0])
    assert sc.index(ext[1]) < sc.index("M104 S220") < sc.index(ext[2])
    i_off = sc.index("SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=0")
    assert sc.index("SAVE_GCODE_STATE NAME=_ktwo_box_chain") < i_off < i_heat
    i_cool = len(sc) - 1 - sc[::-1].index("M104 S0")
    assert i_cool > sc.index(ext[-1])
    assert sc.index("SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=1") \
        > i_cool
    assert sc[-1] == "RESTORE_GCODE_STATE NAME=_ktwo_box_chain" or \
        "RESTORE_GCODE_STATE NAME=_ktwo_box_chain" in sc[-3:], sc[-4:]
    assert printer.pos[:2] == [160.0, 345.0] and printer.target == 0.0
    assert printer.sensor.enabled
    assert mod.remain_material == {"type": "90001", "color": "0FFFFFF",
                                   "tnn": ""}, mod.remain_material
    st = mod.get_status()
    assert not st["extern_active"] and not st["chain_active"]
    assert any("Externe Spule geladen" in m for m in printer.gcode.infos)
    return ("§7: kein Homing, 0 Frames, V60 -> 101,25 -> 160 = 90+70, M109 "
            "220 -> Spitze 240 -> 220, F575/F690/F575 (Stock FILAMENT_RACK), "
            "Sensor aus/an, Heizung aus, X160 Y345, remain gespeichert")


def test_laden_material_und_pruefungen():
    d = _tmpdir()
    p = os.path.join(d, "filament_source.json")
    bus, printer, mod = _load_hull(source_file=p)
    n0 = len(bus.calls)
    # Quelle CFS -> gesperrt
    mod.source = K.SOURCE_CFS
    raises(mod.cmd_extern_begin, {"OP": "LADEN", "MATERIAL": "PLA"},
           "Quelle ist CFS", "SET_FILAMENT_SOURCE SOURCE=EXTERNAL")
    mod.source = K.SOURCE_EXTERN
    # kein Material gespeichert, keins angegeben
    raises(mod.cmd_extern_begin, {"OP": "LADEN"}, "MATERIAL=", "angeben")
    # Kopfsensor leer -> Meldung, nichts bewegt, nichts gespeichert
    bus.head = False
    s0 = len(printer.gcode.scripts)
    raises(mod.cmd_extern_begin, {"OP": "LADEN", "MATERIAL": "PLA"},
           "Filament bis zur LED am Druckkopf schieben")
    assert not printer.gcode.scripts[s0:] and mod.extern_material is None
    assert not mod.chain.get("active")
    # Kopfsensor fehlt (unbekannt) -> gesperrt
    del printer.objects["filament_switch_sensor filament_sensor"]
    raises(mod.cmd_extern_begin, {"OP": "LADEN", "MATERIAL": "PLA"},
           "liefert keinen Zustand")
    printer.objects["filament_switch_sensor filament_sensor"] = printer.sensor
    bus.head = True
    # Druck läuft -> gesperrt; Pause ohne allow_move -> gesperrt
    printer.set_printing(True)
    raises(mod.cmd_extern_begin, {"OP": "LADEN", "MATERIAL": "PLA"},
           "Druck läuft")
    printer.set_printing(False)
    # offener Wechsel-Fehler -> gesperrt
    mod.chain_error = "EXTRUDE fehlgeschlagen"
    raises(mod.cmd_extern_begin, {"OP": "LADEN", "MATERIAL": "PLA"},
           "KTWO_BOX_ERROR_CLEAR")
    mod.chain_error = ""
    # MATERIAL=PLA (Typ, zwei Einträge -> Generic PLA) COLOR rot: neu
    s0 = len(printer.gcode.scripts)
    run_macro(mod, printer, "LOAD_FILAMENT", "MATERIAL=PLA COLOR=FF0000")
    sc = printer.gcode.scripts[s0:]
    assert mod.extern_material == {"type": "90002", "color": "0FF0000",
                                   "name": "Generic PLA", "temp": None,
                                   "origin": "hand"}, mod.extern_material
    saved = json.load(open(p))
    assert saved["source"] == "extern" and \
        saved["extern_material"]["type"] == "90002", saved
    F = K._FLUSH
    want = K.plan_flush_lengths(mod.cfg, None, "0FFFFFF", "0FF0000", False)[0]
    plan = [m for m in printer.gcode.infos if "FLUSH-Plan extern" in m][-1]
    assert "FLUSH-Plan extern: %s mm" % K._n(sum(want)) in plan, (plan, want)
    assert F is not None and "G1 E%s F350" % K._n(round(want[0] * 0.2, 3)) \
        in sc, sc
    assert mod.remain_material == {"type": "90002", "color": "0FF0000",
                                   "tnn": ""}
    # TEMP= überschreibt die DB-Temperatur und wird gespeichert
    s0 = len(printer.gcode.scripts)
    run_macro(mod, printer, "LOAD_FILAMENT", "TEMP=215")
    sc = printer.gcode.scripts[s0:]
    assert mod.extern_material["temp"] == 215.0 \
        and mod.extern_material["type"] == "90002"
    assert "M109 S215" in sc and "M104 S240" in sc and "M104 S215" in sc, sc
    assert json.load(open(p))["extern_material"]["temp"] == 215.0
    # Grenzen: 170 (min_extrude_temp) .. 350 (k2_safety/max_temp-10)
    for t, txt in (("400", "außerhalb 170..350"), ("150", "außerhalb 170")):
        raises(mod.cmd_extern_begin, {"OP": "LADEN", "TEMP": t}, txt)
    printer.objects["configfile"] = TB._Obj({"settings": {"extruder": {
        "max_temp": 300.0, "min_extrude_temp": 180.0}}})
    raises(mod.cmd_extern_begin, {"OP": "LADEN", "TEMP": "295"},
           "außerhalb 180..290")
    printer.objects["k2_safety"] = TB._Obj({"limits": {"extruder": 280.0}})
    raises(mod.cmd_extern_begin, {"OP": "LADEN", "TEMP": "285"}, "..280")
    del printer.objects["configfile"], printer.objects["k2_safety"]
    assert mod.extern_material["temp"] == 215.0, "abgelehnt = unverändert"
    # TPU ohne nozzle_temperature in der DB -> TEMP= nötig
    raises(mod.cmd_extern_begin, {"OP": "LADEN", "MATERIAL": "TPU"},
           "Temperatur für", "TEMP=")
    assert mod.extern_material["type"] == "90002"
    assert len(bus.calls) == n0, "kein Box-Frame"
    return ("Quelle CFS/ohne Material/Kopf leer/ohne Sensor/Druck/Fehler offen "
            "gesperrt ohne Bewegung; PLA -> Generic PLA rot gespeichert, Menge "
            "Farbweg, F350; TEMP=215 gespeichert (Spitze 240); Grenzen "
            "min_extrude/max_temp/k2_safety; TPU braucht TEMP=")


def test_laden_ohne_db_plan_fehler_pause():
    # ohne Material-DB: MATERIAL=PLA braucht TEMP=, dann Name gespeichert
    bus, printer, mod = _load_hull(db=False)
    raises(mod.cmd_extern_begin, {"OP": "LADEN", "MATERIAL": "PLA"},
           "keine Material-DB", "TEMP=")
    s0 = len(printer.gcode.scripts)
    run_macro(mod, printer, "LOAD_FILAMENT", "MATERIAL=PLA TEMP=210")
    sc = printer.gcode.scripts[s0:]
    assert mod.extern_material["name"] == "PLA" and \
        mod.extern_material["temp"] == 210.0 and \
        mod.extern_material["type"] is None
    # Düsen-Material unbekannt (ohne DB) -> 220 wie Stock-Fallback
    assert "M109 S220" in sc and [l for l in sc if l.startswith("G1 E")][0] \
        == "G1 E18 F360", sc
    # Plan: [k2_box] allow_move False -> nichts bewegt/geheizt, remain bleibt
    bus, printer, mod = _load_hull(allow_move="False")
    mod.extern_material = {"type": "90001", "color": "0FFFFFF",
                           "name": "Hyper PLA", "temp": None, "origin": "hand"}
    s0 = len(printer.gcode.scripts)
    run_macro(mod, printer, "LOAD_FILAMENT", homed="")
    sc = printer.gcode.scripts[s0:]
    assert not moves(sc) and "G28 X Y" not in sc, moves(sc)
    assert any("PLAN" in m for m in printer.gcode.infos)
    assert mod.remain_material["tnn"] == "T1A" and not mod.chain.get("active")
    assert printer.sensor.enabled
    # Fehler mitten im Spülen außerhalb eines Drucks: Düse aus, Sensor an,
    # Zustand zurück, G-Code-Fehler
    bus, printer, mod = _load_hull()
    mod.extern_material = {"type": "90001", "color": "0FFFFFF",
                           "name": "Hyper PLA", "temp": None, "origin": "hand"}
    printer.fail_on = "G1 E72"
    try:
        run_macro(mod, printer, "LOAD_FILAMENT")
    except ValueError as exc:
        assert "EXTERN_FLUSH fehlgeschlagen" in str(exc) and "Düse aus" \
            in str(exc), exc
    else:
        raise AssertionError("Fehler im Spülen ohne G-Code-Fehler")
    sc = printer.gcode.scripts
    assert sc[-3:] == ["M104 S0",
                       "SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=1",
                       "RESTORE_GCODE_STATE NAME=_ktwo_box_chain"], sc[-4:]
    assert not mod.chain.get("active") and printer.sensor.enabled
    assert mod.remain_material["tnn"] == "T1A", "remain nur nach Erfolg"
    # in der Pause (allow_write False): läuft, Ende auf Pausentemperatur
    bus, printer, mod = _load_hull()
    mod.extern_material = {"type": "90001", "color": "0FFFFFF",
                           "name": "Hyper PLA", "temp": None, "origin": "hand"}
    printer.paused, printer.print_state, printer.target = True, "paused", 140.0
    printer._refresh()
    s0 = len(printer.gcode.scripts)
    run_macro(mod, printer, "LOAD_FILAMENT")
    sc = printer.gcode.scripts[s0:]
    assert "M109 S220" in sc and sc.count("M104 S140") == 1 \
        and "M104 S0" not in sc, sc
    assert printer.paused and "PAUSE" not in sc and "RESUME" not in sc
    assert not mod.chain_error
    # Fehler in der Pause -> chain_error (RESUME gesperrt), Pausentemperatur
    printer.fail_on = "G1 E72"
    s0 = len(printer.gcode.scripts)
    run_macro(mod, printer, "LOAD_FILAMENT")   # kein G-Code-Fehler in der Pause
    sc = printer.gcode.scripts[s0:]
    assert mod.chain_error and "EXTERN_FLUSH" in mod.chain_error
    assert mod.aborted["source"] == K.EXTERN_CHAIN and not mod.chain.get("active")
    assert "M104 S140" in sc and printer.sensor.enabled
    infos = call(mod.cmd_error_clear)
    assert "0x04 unterdrückt" in infos[-1]
    bus.head = True
    mod.aborted = dict(mod.aborted)
    infos = call(mod.cmd_resume_check, {"MOVE": "1"})
    assert "RESUME frei" in infos[-1], infos
    assert not mech(bus), "kein Box-Frame"
    return ("ohne DB TEMP= Pflicht/Name gespeichert/220 + F360; Plan ohne "
            "Bewegung; Fehler -> M104 S0 + Sensor + Zustand + G-Code-Fehler; "
            "Pause: M104 S140, Fehler -> chain_error, ERROR_CLEAR ohne 0x04")


# =====================================================================
# 7. Entladen als Skriptfolge
# =====================================================================

def test_entladen_ablauf():
    bus, printer, mod = _load_hull()
    mod.extern_material = {"type": "90001", "color": "0FFFFFF",
                           "name": "Hyper PLA", "temp": None, "origin": "hand"}
    n0, s0 = len(bus.calls), len(printer.gcode.scripts)
    lines, _m = run_macro(mod, printer, "UNLOAD_FILAMENT")
    sc = printer.gcode.scripts[s0:]
    seq = ["SAVE_GCODE_STATE NAME=_ktwo_box_chain", "G0 X133 F12000",
           "SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=0", "M109 S220",
           "G0 X160 F12000", "G0 Y345 F12000", "G0 X10 Y200 F12000",
           "G0 X-7.8 Y200 F30000", "G0 X10 F12000", "G1 E-10 F360", "M400",
           "M104 S0", "SET_FILAMENT_SENSOR SENSOR=filament_sensor ENABLE=1",
           "RESTORE_GCODE_STATE NAME=_ktwo_box_chain"]
    i = -1
    for want in seq:
        i = sc.index(want, i + 1)
    assert printer.pos[:2] == [160.0, 345.0], printer.pos
    assert len(bus.calls) == n0 and mod.remain_material["tnn"] == "T1A"
    assert any("Filament jetzt von Hand herausziehen" in m
               for m in printer.gcode.infos)
    # Kopfsensor leer: kein Heizen, kein Rückzug, Schnitt trotzdem (Stock)
    bus.head = False
    s0 = len(printer.gcode.scripts)
    run_macro(mod, printer, "UNLOAD_FILAMENT")
    sc = printer.gcode.scripts[s0:]
    assert "G0 X-7.8 Y200 F30000" in sc and not [
        l for l in sc if l.startswith(("M109", "G1 E"))], sc
    # TEMP= nur für dieses Entladen, Material bleibt
    bus.head = True
    s0 = len(printer.gcode.scripts)
    run_macro(mod, printer, "UNLOAD_FILAMENT", "TEMP=230")
    assert "M109 S230" in printer.gcode.scripts[s0:]
    assert mod.extern_material["temp"] is None
    # ohne gespeichertes Material: Düsen-Material (Stock FILAMENT_RACK_SET_
    # TEMP), sonst MATERIAL=/TEMP= verlangt
    mod.extern_material = None
    mod.remain_material = {"type": "90003", "color": None, "tnn": ""}
    s0 = len(printer.gcode.scripts)
    run_macro(mod, printer, "UNLOAD_FILAMENT")
    assert "M109 S250" in printer.gcode.scripts[s0:]
    mod.remain_material = {"type": None, "color": None, "tnn": ""}
    raises(mod.cmd_extern_begin, {"OP": "ENTLADEN"}, "MATERIAL=", "TEMP=")
    # MATERIAL= beim Entladen setzt + speichert
    run_macro(mod, printer, "UNLOAD_FILAMENT", "MATERIAL=PETG")
    assert mod.extern_material["type"] == "90003" \
        and mod.extern_material["name"] == "Generic PETG"
    # Baustein ohne laufenden Vorgang -> Fehler
    for fn in (mod.cmd_extern_heat, mod.cmd_extern_retract,
               mod.cmd_extern_flush):
        raises(fn, {}, "nur innerhalb EXTERN_")
    infos = call(mod.cmd_extern_end)
    assert "kein externer" in infos[0]
    assert len(bus.calls) == n0, "Entladen sendet nichts"
    return ("Schacht -> Sensor aus -> M109 220 -> Schacht verlassen -> Schnitt "
            "-> E-10 F360 -> X160 Y345 -> M104 S0 -> Sensor an; Kopf leer ohne "
            "Heizen/Rückzug; TEMP= einmalig; Düsen-Material; 0 Frames")


# =====================================================================
# 8. Material-Lookup
# =====================================================================

def test_material_lookup():
    db = ext_db()
    f = K.find_extern_material
    assert f(db, "90001") == ("90001", "Hyper PLA")
    assert f(db, "190003") == ("190003", "Generic PETG")      # RFID-Code
    assert f(db, "hyper pla") == ("90001", "Hyper PLA")
    assert f(db, "PLA") == ("90002", "Generic PLA")           # Typ -> Generic
    assert f(db, "petg") == ("90003", "Generic PETG")
    assert f(db, "TPU") == ("90004", "Test TPU")              # Typ eindeutig
    assert f(db, '"Hyper PETG"') == ("90005", "Hyper PETG")
    for raw, txt in (("Hyper", "mehrdeutig"), ("ABS", "nicht in der"),
                     ("", "leer")):
        try:
            f(db, raw)
        except ValueError as exc:
            assert txt in str(exc), exc
        else:
            raise AssertionError("%r angenommen" % raw)
    try:
        f(None, "PLA")
    except ValueError as exc:
        assert "material_database" in str(exc)
    data = ext_db_data()
    data["result"]["list"][1]["base"]["name"] = "Basic PLA"   # kein Generic
    try:
        f(K._MDB.MaterialDB(data), "PLA")
    except ValueError as exc:
        assert "mehrdeutig" in str(exc) and "Hyper PLA (90001)" in str(exc)
    else:
        raise AssertionError("PLA ohne Generic eindeutig?")
    # KTWO_BOX_EXTERN_MATERIAL
    bus, printer, mod = ext_hull()
    infos = call(mod.cmd_extern_material)
    assert "keines gespeichert" in infos[0], infos
    infos = call(mod.cmd_extern_material, {"MATERIAL": "PLA",
                                           "COLOR": "00FF00"})
    assert "Generic PLA (90002)" in infos[0] and "220 °C (Material-DB 90002)" \
        in infos[0], infos
    infos = call(mod.cmd_extern_material, {"TEMP": "205"})
    assert mod.extern_material["temp"] == 205.0 and "Handwert" in infos[0]
    call(mod.cmd_extern_material, {"TEMP": "0"})
    assert mod.extern_material["temp"] is None
    raises(mod.cmd_extern_material, {"TEMP": "500"}, "außerhalb")
    raises(mod.cmd_extern_material, {"MATERIAL": "PLA", "TYPE": "90001"},
           "nicht zusammen")
    call(mod.cmd_extern_material, {"TYPE": "190001"})
    assert mod.extern_material["name"] == "Hyper PLA" and \
        mod.extern_material["color"] is None
    infos = call(mod.cmd_extern_material, {"MATERIAL": "TPU"})
    assert "LOAD_FILAMENT braucht TEMP=" in infos[0], infos
    call(mod.cmd_extern_material, {"CLEAR": "1"})
    assert mod.extern_material is None
    raises(mod.cmd_extern_material, {"COLOR": "FF0000"}, "braucht ein Material")
    mod.chain = mod._new_chain()
    mod.chain.update({"active": True, "source": K.EXTERN_CHAIN, "op": "LADEN"})
    raises(mod.cmd_extern_material, {"MATERIAL": "PLA"}, "Vorgang läuft")
    mod.chain = mod._new_chain()
    return ("ID/RFID-Code/Name/Typ->Generic/eindeutiger Typ, mehrdeutig/fehlt/"
            "ohne DB -> Fehler; SET_EXTERNAL_MATERIAL setzen/TEMP/0/Grenzen/TYPE/"
            "CLEAR/Sperre")


# =====================================================================
# 9. RFID 0x11 (optional)
# =====================================================================

def test_extern_rfid():
    tag = TB._mtag("190001", "0FF8800")   # synthetisch (kein echtes Etikett)
    # aus (Default): nichts gesendet
    bus = RfidBus(tag)
    _b, printer, mod = ext_hull(bus=bus)
    assert mod.cfg["extern_rfid"] is False
    infos = call(mod.cmd_extern_rfid)
    assert "aus" in infos[0] and not bus.calls, infos
    # an: genau EIN Frame 0x11 [02] ohne Daten, retries 0, 0,5 s
    d = _tmpdir()
    p = os.path.join(d, "filament_source.json")
    bus = RfidBus(tag)
    _b, printer, mod = ext_hull(bus=bus, extern_rfid="True", source_file=p)
    mod.extern_material = {"type": None, "color": None, "name": "PLA",
                           "temp": 210.0, "origin": "hand"}
    infos = call(mod.cmd_extern_rfid)
    assert [(c[0], c[1], c[2]) for c in bus.calls] == [(0x11, 0x02, ())], \
        bus.calls
    kw = bus.calls[0][3]
    assert kw == {"timeout": 0.5, "retries": 0, "status": 0xFF}, kw
    assert mod.extern_material == {"type": "190001", "color": "0FF8800",
                                   "name": "Hyper PLA", "temp": None,
                                   "origin": "rfid"}, mod.extern_material
    assert "Material 190001 (Hyper PLA), Farbe 0FF8800" in infos[-1], infos
    saved = open(p, encoding="utf-8").read()
    alltext = "\n".join(printer.gcode.infos + printer.gcode.raw + infos) + saved
    assert tag not in alltext and tag[:11] not in alltext, \
        "Etikett-Rohdaten dürfen nicht erscheinen"
    assert mod.get_status()["extern_rfid_last"] == "Material 190001, Farbe 0FF8800"
    # Stock-Request + Sendeschutz unverändert (nur lesen)
    S485 = K._S485
    S485.check_tx_frame(0x11, 0x02, [])
    for bad in ((0x11, 0x0B, []), (0x11, 0x02, [0x00]), (0x11, 0x03, [1])):
        try:
            S485.check_tx_frame(*bad)
        except ValueError:
            pass
        else:
            raise AssertionError("Sendeschutz lässt %r durch" % (bad,))
    if K._RACK is not None:
        assert K._RACK.build_rfid_request_frame() == bytes.fromhex("f71103ff0264")
    # Parser: unlesbar/leer -> Material bleibt
    for bad in ("0" * 40, "kurz"):
        bus.rack_tag = bad
        n = len(bus.calls)
        infos = call(mod.cmd_extern_rfid)
        assert len(bus.calls) == n + 1 and "nicht lesbar" in infos[-1], infos
        assert mod.extern_material["type"] == "190001"
    bus.rack_tag = None
    infos = call(mod.cmd_extern_rfid)
    assert "keine Antwort" in infos[-1] and mod.extern_material["type"] == "190001"
    # nicht in der DB -> Hinweis TEMP=
    bus.rack_tag = TB._mtag("199999", "0000000")
    infos = call(mod.cmd_extern_rfid)
    assert mod.extern_material["type"] == "199999" and "TEMP=" in infos[-1]
    # während eines Vorgangs: nichts gesendet
    mod.chain = mod._new_chain()
    mod.chain.update({"active": True, "source": K.EXTERN_CHAIN, "op": "LADEN"})
    n = len(bus.calls)
    infos = call(mod.cmd_extern_rfid)
    assert len(bus.calls) == n and "Vorgang läuft" in infos[0]
    mod.chain = mod._new_chain()
    # reine Parser-Funktion (Antwort-Vollframe wie Stock ret[5:-1])
    raw = bytes(TB.REF_BUILD(0x11, 0x02, list(tag.encode()), status=0x00))
    assert K.extern_rfid_tag(raw_frame=raw) == ("190001", "0FF8800")
    assert K.extern_rfid_tag(data=b"x" * 39) is None
    return ("aus = 0 Frames; an = genau 0x11 [02] (0,5 s, retries 0), Material "
            "überschrieben (Handwert weg), keine Rohdaten in Meldungen/Datei; "
            "Sendeschutz unverändert; unlesbar/Timeout/fremd/Vorgang")


# =====================================================================
# 10. Config/Doku-Verdrahtung, 11. Python 3.9
# =====================================================================

def test_config_doku():
    cfg = K.parse_options({})
    assert cfg["extern_rfid"] is False and cfg["filament_source_file"] == \
        K.FILAMENT_SOURCE_FILE_DEFAULT
    km = open(os.path.join(_HERE, "config-pi", "k2-module.cfg"),
              encoding="utf-8").read()
    a = km.index("\n[k2_box]\n")
    sec = km[a:km.index("\n[", a + 1)]
    assert "\nfilament_source_file: ~/printer_data/config/k2/" \
           "filament_source.json\n" in sec and "\nextern_rfid: False\n" in sec
    pc = open(os.path.join(_HERE, "config-pi", "printer.cfg"),
              encoding="utf-8").read()
    assert "#[gcode_button extern_spule]\n#pin: !PA5\n#press_gcode:\n" \
           "#  KTWO_EXTERN_RFID\n" in pc
    assert "\n[gcode_button extern_spule]" not in pc, "RFID muss AUS sein"
    rd = open(os.path.join(_HERE, "README-k2_box.md"), encoding="utf-8").read()
    assert "## 18. Externe Spule" in rd
    cr = open(os.path.join(_HERE, "config-pi", "README.md"),
              encoding="utf-8").read()
    assert "LOAD_FILAMENT" in cr and "SET_FILAMENT_SOURCE SOURCE=EXTERNAL" in cr
    assert "| box.cfg `[filament_rack]`, `[load_ai]` | E |" not in cr
    return "Defaults, k2-module.cfg, printer.cfg (RFID auskommentiert), READMEs"


def test_makro_beschreibungen():
    # 25.10.2026: jedes in Mainsail sichtbare Makro (Name ohne "_") hat eine
    # deutsche Beschreibung (Tooltip); die alten Namen (bis 25.10.2026) sind weg
    import re as _re
    fehlt, gesamt = [], 0
    for fn in ("macros.cfg", "cutcal.cfg", "zalign.cfg"):
        txt = open(os.path.join(_HERE, "config-pi", fn), encoding="utf-8").read()
        for m in _re.finditer(r"^\[gcode_macro ([^\]]+)\]\n(.*?)(?=^\[|\Z)", txt,
                              _re.M | _re.S):
            name, body = m.group(1), m.group(2)
            if name.startswith("_"):
                continue
            gesamt += 1
            d = _re.search(r"^description:\s*(\S.*)$", body, _re.M)
            if not d:
                fehlt.append("%s:%s" % (fn, name))
    assert not fehlt, fehlt
    names_cfg = open(os.path.join(_HERE, "config-pi", "macros.cfg"),
                     encoding="utf-8").read()
    for old in ("QUELLE_EXTERN", "QUELLE_CFS", "EXTERN_LADEN", "EXTERN_ENTLADEN",
                "EXTERN_MATERIAL", "BETT_AUSRICHTEN", "WIEDEREINSTIEG",
                "TEMP_RUHETEST", "BEWEGUNG_FREIGEBEN"):
        assert "[gcode_macro %s]" % old not in names_cfg, old
    return "%d sichtbare Makros mit Beschreibung, alte Namen entfernt" % gesamt


def test_python39_extern():
    import ast
    for fn in ("k2_box.py", "test_k2_box_extern.py"):
        src = open(os.path.join(_HERE, fn), encoding="utf-8").read()
        ast.parse(src, filename=fn, feature_version=(3, 9))
    return "k2_box.py + test_k2_box_extern.py Python-3.9-Syntax ok"


TESTS = [
    ("Quellen-Datei (fehlt/kaputt -> CFS, atomar)", test_quelle_datei),
    ("KTWO_BOX_SOURCE (persistent, Sperren)", test_quelle_huelle),
    ("Quelle EXTERN: alle CFS-Aktionen gesperrt, nichts gesendet",
     test_extern_sperren_nichts_gesendet),
    ("Extern-Druck: Bus-Mitschnitt wie Stock (0x0d/0x0f-Ausnahme)",
     test_extern_druck_busmitschnitt),
    ("T<n>-Logik (erstes T / anderes T -> PAUSE)", test_t_logik),
    ("Makros T/M8200/LOAD_/UNLOAD_FILAMENT/SET_FILAMENT_SOURCE (Jinja2)", test_makros_extern),
    ("Laden live §1/§7 als Skriptfolge", test_laden_ablauf_live),
    ("Laden: Material, Kopfsensor, TEMP=, Grenzen", test_laden_material_und_pruefungen),
    ("Laden: ohne DB, Plan, Fehler, Pause", test_laden_ohne_db_plan_fehler_pause),
    ("Entladen als Skriptfolge", test_entladen_ablauf),
    ("Material-Lookup + KTWO_BOX_EXTERN_MATERIAL", test_material_lookup),
    ("RFID 0x11 der externen Spule (optional)", test_extern_rfid),
    ("Config/Doku-Verdrahtung", test_config_doku),
    ("Makro-Beschreibungen (Mainsail-Tooltip)", test_makro_beschreibungen),
    ("Python-3.9-Syntax", test_python39_extern),
]


def main():
    fails, total = 0, 0
    print("test_k2_box_extern – %d Prüfungen (ohne Klipper, ohne Hardware)"
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
            import traceback
            traceback.print_exc()
            print("FAIL %s: %s: %s" % (name, type(exc).__name__, exc))
        else:
            print("ok %s – %s" % (name, detail))
    print("ERGEBNIS: %s (%d/%d ok)"
          % ("ok" if fails == 0 else "%d FEHLER" % fails, total - fails, total))
    return fails


if __name__ == "__main__":
    raise SystemExit(main())
