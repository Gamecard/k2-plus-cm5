#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""test_k2_prtouch.py – Tests ohne Klipper (python3).

Fake-MCUs + Fake-Toolhead mit simulierter Kraftkurve (Kontakt bei bekannter
Z-Höhe, Rauschen, Drift) →
  (a) Kontakt korrekt, Z-Fehler < 0,02mm
  (b) kein Schritt > step_mm, Gesamtweg nie > max_travel_mm
  (c) Notgrenze bricht ab und hebt an
  (d) allow_move=False bewegt nie Z
  (e) defekter Swap/Sensor → Abbruch vor Bewegung
  (f) Formatstrings exakt wie Dictionary
  (g) Abbruch spätestens 1 Schritt nach Kontakt (Überfahrweg begrenzt)

Aufruf: python3 test_k2_prtouch.py
"""

import json
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import k2_prtouch as K

# prtouch-Befehle/-Antworten aus den MCU-Data-Dictionaries (Main + Düse, FW 1.1.0.48-293)
FORMATS = os.path.join(_HERE, "prtouch_mcu_formats.json")


PASS = []
FAIL = []


def check(name, cond, detail=""):
    if cond:
        PASS.append(name)
        print("PASS %s %s" % (name, detail))
    else:
        FAIL.append(name)
        print("FAIL %s %s" % (name, detail))


class DummyLog:
    def __init__(self):
        self.infos = []
        self.errors = []

    def info(self, msg):
        self.infos.append(str(msg))

    def error(self, msg):
        self.errors.append(str(msg))


def det_noise(i, amp):
    """Deterministisches Rauschen ±amp (kein random → reproduzierbar)."""
    if amp <= 0:
        return 0
    span = 2 * int(amp) + 1
    return ((i * 7919 + 13) % span) - int(amp)


class FakeToolhead:
    """Simuliert Z (+XY), zeichnet alle Bewegungen auf."""

    def __init__(self, z_start=0.2, x=100.0, y=100.0, temps=(140.0, 50.0),
                 events=None):
        self.events = events if events is not None else []
        self.fail_down_at = None  # n-ter Abwärtsschritt wirft (Fehlertest)
        self.fail_lift = False
        self.z = float(z_start)
        self.x = float(x)
        self.y = float(y)
        self.z_start = float(z_start)
        self.temps = tuple(temps)
        self.moves = []  # (kind, dz_or_xy, speed)
        self.waits = 0
        self.min_z = float(z_start)

    def get_z(self):
        return float(self.z)

    def get_xy(self):
        return (float(self.x), float(self.y))

    def move_down(self, step_mm, speed):
        if (self.fail_down_at is not None
                and len(self.down_steps()) + 1 >= self.fail_down_at):
            self.events.append("down_fail")
            raise RuntimeError("Toolhead-Fehler (Test)")
        self.events.append("down")
        self.moves.append(("down", float(step_mm), float(speed)))
        self.z -= float(step_mm)
        if self.z < self.min_z:
            self.min_z = float(self.z)

    def move_to_xy(self, x, y, speed):
        self.events.append("xy")
        self.moves.append(("xy", (float(x), float(y)), float(speed)))
        self.x, self.y = float(x), float(y)

    def lift(self, dz_mm, speed):
        if self.fail_lift:
            raise RuntimeError("Lift-Fehler (Test)")
        self.events.append("lift")
        self.moves.append(("lift", float(dz_mm), float(speed)))
        self.z += float(dz_mm)

    def wait_moves(self):
        self.waits += 1
        self.events.append("wait")

    def get_temps(self):
        return self.temps

    def down_steps(self):
        return [m[1] for m in self.moves if m[0] == "down"]

    def total_down(self):
        return sum(self.down_steps())


class FakePres:
    """Düsen-MCU: Kraftkurve mit Kontakt bei z_true.

    Seit 17.10.2026 mit Arm-Zustand wie die echte Düse: Register erst nach
    start_prtouch_pres gefüllt (vorher Tick 0 + Wert 0 = leer, B Memset),
    Swap-Leitung (set_swap -> Pegel, den FakeStep.read_swap liest),
    Status tri_chxs (read_status), disarm."""

    def __init__(self, tool, cfg, z_true=0.0, baseline=-1450000,
                 noise_amp=300, swap_ok=True, rest_noisy=False,
                 emergency_always=False, spike=-9000, tick_base=10000000,
                 events=None):
        self.events = events if events is not None else []
        self.armed = False
        self.level = 1          # PA15/PC7
        self.ack_err = 0        # start_prtouch_pres ack err
        self.never_fills = False  # start wirkungslos -> Register bleibt leer
        self.mcu_trigger = True   # Düse löst bei Kontakt selbst aus
        self.trigger_on_arm = False
        self.fail_disarm = False
        self.tool = tool
        self.cfg = cfg
        self.z_true = float(z_true)
        self.baseline = int(baseline)
        self.noise_amp = int(noise_amp)
        self.swap_ok = bool(swap_ok)
        self.rest_noisy = bool(rest_noisy)
        self.emergency_always = bool(emergency_always)
        self.spike = int(spike)
        self.tick_base = int(tick_base)
        self.reads = 0
        self.starts = 0
        self.stops = []

    def test_swap(self):
        if not self.swap_ok:
            return {"swap_ok": False}
        return {"py_ver": 78, "pres_ver": 71, "step_ver": 71,
                "swap_ok": True}

    def set_swap(self, level):
        self.events.append("swap%d" % int(level))
        if self.swap_ok:  # defekte Leitung: Main liest immer 1
            self.level = int(level)
        return {"err": 0, "expar0": 0, "expar1": 71}

    def start_sampling(self):
        self.events.append("start_pres")
        self.starts += 1
        if self.ack_err:
            return {"err": self.ack_err, "expar0": 0, "expar1": 0}
        self.armed = True
        return {"err": 0, "expar0": 64, "expar1": 0}

    def disarm(self):
        self.events.append("stop_pres1")
        self.armed = False
        self.level = 1
        if self.fail_disarm:
            raise RuntimeError("Timeout stop_prtouch_pres (Test)")
        return {"err": 0, "expar0": 0, "expar1": 71}

    def _contact(self):
        return float(self.tool.get_z()) <= self.z_true + 1e-12

    def read_status(self):
        self.events.append("status")
        tri = self.armed and (self.trigger_on_arm
                              or (self.mcu_trigger and self._contact()))
        return {"tri_chxs": 1 if tri else 0, "buf_len": 64, "idx": 0,
                "len": 0}

    def _filled(self):
        return self.armed and not self.never_fills

    @staticmethod
    def _empty():
        return struct.pack("<64I", *([0] * 64)), struct.pack("<64i",
                                                             *([0] * 64))

    def read_pres_filtered(self):
        self.events.append("read_ftr")
        if not self._filled():
            return self._empty()
        return self._ticks(), struct.pack(
            "<64i", *[det_noise(3 * i + 1, 300) for i in range(64)])

    def stop_sampling(self, ok=True):
        self.stops.append(bool(ok))

    def _ticks(self):
        base = self.tick_base + self.reads * 100000
        step = 93750  # pres acq_tick
        last = base
        ticks = [last - (63 - i) * step for i in range(64)]
        return struct.pack("<64I", *[t & 0xFFFFFFFF for t in ticks])

    def _ruhe_datas(self, noisy=False):
        out = []
        for i in range(64):
            if noisy:
                # groß: abwechselnd ±5000 → dif 10000 > 3000
                v = self.baseline + (5000 if i % 2 == 0 else -5000)
            else:
                v = self.baseline + det_noise(i + self.reads * 131,
                                              self.noise_amp)
            out.append(int(v))
        return struct.pack("<64i", *out)

    def read_rest_pres(self):
        self.reads += 1
        return self._ticks(), self._ruhe_datas(noisy=self.rest_noisy)

    def read_pres(self):
        self.events.append("read_pres")
        if not self._filled():
            return self._empty()  # nie gestartetes Register (Altfehler)
        self.reads += 1
        if self.emergency_always and self.tool.down_steps():
            # Ruhe für baseline0 war ok (Ruhetest), hier Notfall:
            out = [self.baseline + 50000 + det_noise(i, 100)
                   for i in range(64)]
            return self._ticks(), struct.pack("<64i", *out)
        if not self._contact():
            return self._ticks(), self._ruhe_datas(noisy=self.rest_noisy)
        # Kontakt: erste 60 Ruhe, letzte 4 Spike (wie live FTR-Spitze):
        out = []
        for i in range(64):
            if i < 60:
                out.append(self.baseline + det_noise(i, self.noise_amp))
            else:
                out.append(self.baseline + self.spike
                           - (i - 60) * 500 + det_noise(i, 50))
        return self._ticks(), struct.pack("<64i", *out)


class FakeStep:
    """Main-MCU: Schrittzähler → counts, relativ zum Trigger."""

    def __init__(self, tool, cfg, swap_ok=True, tick_base=20000000,
                 pres=None, events=None):
        self.pres = pres
        self.events = events if events is not None else []
        self.fail_disarm = False
        self.tool = tool
        self.cfg = cfg
        self.swap_ok = bool(swap_ok)
        self.tick_base = int(tick_base)
        self.reads = 0
        self.starts = 0
        self.stops = []

    def test_swap(self):
        if not self.swap_ok:
            return {"swap_ok": False}
        return {"py_ver": 78, "pres_ver": 71, "step_ver": 71,
                "swap_ok": True}

    def read_swap(self):
        # stop_prtouch_step: expar0 = PC7-Pegel (B 0x08004244)
        self.events.append("read_swap")
        return {"err": 0, "expar0": self.pres.level if self.pres else 1,
                "expar1": 71}

    def cont_step(self):
        self.events.append("cont")
        return {"cnt_x": 0, "cnt_y": 0, "cnt_z": 0}

    def start_sampling(self):
        self.events.append("start_step")
        self.starts += 1
        return {"err": 0, "expar0": 0, "expar1": 0}

    def disarm(self):
        self.events.append("stop_step")
        if self.fail_disarm:
            raise RuntimeError("Timeout stop_prtouch_step (Test)")
        return {"err": 0, "expar0": 1, "expar1": 71}

    def stop_sampling(self, ok=True):
        self.stops.append(bool(ok))

    def read_step(self):
        self.events.append("read_step")
        self.reads += 1
        speed = float(self.cfg["probe_speed"])
        stepd = float(self.cfg["step_dist_mm"])
        aqc = int(self.cfg["aqc_tick"])
        freq = float(self.cfg["clock_freq"])
        base = self.tick_base + self.reads * 50000
        ticks = [base - (63 - i) * aqc for i in range(64)]
        tb = struct.pack("<64I", *[t & 0xFFFFFFFF for t in ticks])
        # Zeiten relativ, Z_sample = current + (-t)*speed:
        zcur = float(self.tool.get_z())
        counts = []
        for i in range(64):
            t = (ticks[i] - ticks[-1]) / freq
            zsample = zcur + (-t) * speed
            counts.append(int(round((zsample - zcur) / stepd)))
        db = struct.pack("<64i", *counts)
        return tb, db


def make_ctl(z_start=0.2, z_true=0.0, cfg_over=None, fake_over=None):
    cfg_over = dict(cfg_over or {})
    fake_over = dict(fake_over or {})
    base = {"allow_move": True, "step_mm": 0.02, "probe_speed": 5.0,
            "max_travel_mm": 5.0, "packing": "struct"}  # Fakes liefern feste Breite
    base.update(cfg_over)
    cfg = K.parse_options(base)
    events = []
    tool = FakeToolhead(z_start=z_start,
                        temps=fake_over.get("temps", (140.0, 50.0)),
                        events=events)
    pres = FakePres(tool, cfg, z_true=z_true, events=events,
                    spike=fake_over.get("spike", -9000),
                    baseline=fake_over.get("baseline", -1450000),
                    noise_amp=fake_over.get("noise_amp", 300),
                    swap_ok=fake_over.get("swap_ok", True),
                    rest_noisy=fake_over.get("rest_noisy", False),
                    emergency_always=fake_over.get("emergency_always",
                                                   False))
    step = FakeStep(tool, cfg, pres=pres, events=events,
                    swap_ok=fake_over.get("step_swap_ok",
                                          fake_over.get("swap_ok", True)))
    log = DummyLog()
    pauses = []

    def _pause(sec):
        events.append("pause")
        pauses.append(float(sec))

    ctl = K.K2PrtouchController(cfg, pres, step, tool,
                                pause=_pause, logger=log)
    ctl._test_events = events
    ctl._test_pauses = pauses
    return cfg, tool, pres, step, ctl, log


def test_a_contact_accuracy():
    cfg, tool, pres, step, ctl, log = make_ctl(z_start=0.2, z_true=0.0)
    res = ctl.probe_point(100.0, 100.0)
    err = abs(res.get("z", 999.0) - 0.0) if res.get("status") == "ok" else 999
    check("(a) status ok", res.get("status") == "ok", str(res)[:200])
    check("(a) Z-Fehler < 0,02mm", err < 0.02, "err=%.4f z=%s" % (
        err, res.get("z")))
    # zoft im Live-Bereich 0.010-0.040 (Toleranz für diskrete Steps):
    zf = res.get("zoft", -1)
    check("(a) zoft 0.005-0.045", 0.005 <= zf <= 0.045, "zoft=%.4f" % zf)


def test_b_step_limits():
    cfg, tool, pres, step, ctl, log = make_ctl(z_start=0.2, z_true=0.0)
    res = ctl.probe_point()
    downs = tool.down_steps()
    mx = max(downs) if downs else 0.0
    tot = tool.total_down()
    check("(b) kein Schritt > step_mm",
          all(d <= cfg["step_mm"] + 1e-9 for d in downs),
          "max=%.4f step_mm=%.4f" % (mx, cfg["step_mm"]))
    check("(b) Gesamtweg <= max_travel",
          tot <= cfg["max_travel_mm"] + 1e-9,
          "total=%.4f max=%.2f" % (tot, cfg["max_travel_mm"]))
    # Negativ: Ziel außerhalb Hub → max_travel-Abbruch, trotzdem begrenzt:
    cfg2, tool2, _, _, ctl2, _ = make_ctl(z_start=0.5, z_true=0.0,
                                          cfg_over={"max_travel_mm": 0.1})
    res2 = ctl2.probe_point()
    check("(b) max_travel-Abbruch", res2.get("status") == "max_travel",
          str(res2.get("status")))
    check("(b) trotz Abbruch begrenzt",
          tool2.total_down() <= 0.1 + 1e-9, "total=%.4f" % tool2.total_down())
    # Config-Härten:
    try:
        K.parse_options({"step_mm": 0.06})
        check("(b) step_mm>0.05 abgelehnt", False, "kein Fehler")
    except ValueError:
        check("(b) step_mm>0.05 abgelehnt", True)
    try:
        K.parse_options({"probe_speed": 6.0})
        check("(b) speed>5 abgelehnt", False, "kein Fehler")
    except ValueError:
        check("(b) speed>5 abgelehnt", True)


def test_c_emergency():
    cfg, tool, pres, step, ctl, log = make_ctl(
        z_start=0.2, z_true=0.0, fake_over={"emergency_always": True})
    z0 = tool.get_z()
    res = ctl.probe_point()
    check("(c) Notgrenze bricht ab", res.get("status") == "emergency",
          str(res)[:200])
    check("(c) angehoben nach Abbruch", tool.get_z() > tool.min_z,
          "min=%.4f jetzt=%.4f" % (tool.min_z, tool.get_z()))
    check("(c) kein Weiterprobe (steps klein)",
          res.get("steps", 999) <= 2, "steps=%s" % res.get("steps"))


def test_d_no_move_without_allow():
    cfg, tool, pres, step, ctl, log = make_ctl(
        z_start=0.2, z_true=0.0, cfg_over={"allow_move": False})
    z0 = tool.get_z()
    res = ctl.probe_point(100.0, 100.0, dry_run=False)
    check("(d) allow_move=False → dry_run",
          res.get("status") == "dry_run", str(res.get("status")))
    check("(d) Z unbewegt", abs(tool.get_z() - z0) < 1e-12
          and len(tool.moves) == 0, "moves=%d" % len(tool.moves))
    # DRY_RUN=1 auch mit allow_move=True bewegt nichts:
    cfg2, tool2, _, _, ctl2, _ = make_ctl(z_start=0.2, z_true=0.0)
    res2 = ctl2.probe_point(dry_run=True)
    check("(d) DRY_RUN=1 bewegt nichts",
          res2.get("status") == "dry_run" and len(tool2.moves) == 0)


def test_e_swap_rest_abort_before_move():
    # Swap defekt:
    cfg, tool, pres, step, ctl, log = make_ctl(
        z_start=0.2, z_true=0.0, fake_over={"swap_ok": False})
    res = ctl.probe_point()
    check("(e) Swap defekt → swap_error", res.get("status") == "swap_error",
          str(res)[:200])
    check("(e) kein Schritt bei Swap-Fehler", len(tool.moves) == 0,
          "moves=%d" % len(tool.moves))
    # Sensor unruhig:
    cfg2, tool2, _, _, ctl2, _ = make_ctl(
        z_start=0.2, z_true=0.0, fake_over={"rest_noisy": True})
    res2 = ctl2.probe_point()
    check("(e) Sensor unruhig → rest_error",
          res2.get("status") == "rest_error", str(res2)[:200])
    check("(e) kein Schritt bei Ruhe-Fehler", len(tool2.moves) == 0)


def test_f_formatstrings():
    try:
        _f = json.load(open(FORMATS))
        main, noz = _f["main"], _f["nozzle"]
    except Exception as exc:
        check("(f) Dicts lesbar", False, str(exc))
        return
    for name, fmt in K.ALL_FORMATS.items():
        in_main = (fmt in main.get("commands", {})
                   or fmt in main.get("responses", {}))
        in_noz = (fmt in noz.get("commands", {})
                  or fmt in noz.get("responses", {}))
        check("(f) %s exakt" % name, in_main and in_noz,
              "" if (in_main and in_noz) else "fehlt")
    # Tippfehler exakt:
    check("(f) aqc_tick-Schreibweise", "aqc_tick" in K.CMD_START_STEP
          and "acq_tick" not in K.CMD_START_STEP)
    check("(f) resault-Schreibweise", K.RESP_PRES.startswith("resault_")
          and K.RESP_STEP.startswith("resault_"))


def test_g_stop_within_one_step():
    for zt in (0.0, 0.05):
        cfg, tool, pres, step, ctl, log = make_ctl(z_start=0.2, z_true=zt)
        res = ctl.probe_point()
        over = zt - tool.min_z  # >0 = zu tief
        check("(g) Kontakt z=%.2f: 1 Schritt genügt" % zt,
              res.get("status") == "ok" and over <= cfg["step_mm"] + 1e-9,
              "min_z=%.4f wahr=%.4f over=%.4f steps=%s" % (
                  tool.min_z, zt, over, res.get("steps")))
    # Wartet nach JEDEM Schritt (M400-Äquivalent):
    cfg, tool, pres, step, ctl, log = make_ctl(z_start=0.2, z_true=0.0)
    res = ctl.probe_point()
    downs = len(tool.down_steps())
    check("(g) wait nach jedem Schritt", tool.waits >= downs,
          "waits=%d downs=%d" % (tool.waits, downs))


def test_extra_can_brk_retry():
    # can_brk-Heuristik pur (T58-Werte → False, Normal → True):
    cfg = K.parse_options({})
    can, _ = K.check_can_brk(53, 42, 0.040, cfg)
    check("(x) T58 → can_brk False", can is False)
    can2, _ = K.check_can_brk(59, 54, 0.017, cfg)
    check("(x) normal → can_brk True", can2 is True)


# Echte Pakete (Live-Lesetest 16.10.2026, Düsen-MCU, Stock-READ-Parameter):
LIVE_PACKED = [('08aaab5679b6082c7301927201a27201927201927201927201a27201', '081012f034ea30c500bfdf359b002f', [146176342, 146271362, 146366228, 146461110, 146555976, 146650842, 146745708, 146840590], [-1428240, -1428192, -1427995, -1428060, -1428093, -1428040, -1427885, -1427838]), ('08aaabf20ec2089471019c7301d27101927201d87301a27201a27201', '0814166a37ea5effd8fe631cb100c100b1', [146935538, 147030150, 147125282, 147219956, 147314822, 147410014, 147504896, 147599778], [-1427606, -1427768, -1428064, -1427965, -1427937, -1427760, -1427567, -1427646]), ('0902aaab2a41a809d672014a72019e7201927201927201927201d87301927201', '090000025a35ea67232d27f6e4fcad', [162021674, 162116608, 162211402, 162306280, 162401146, 162496012, 162590878, 162686070, 162780936], [-1428134, -1428031, -1427996, -1427951, -1427912, -1427922, -1427950, -1427954, -1428037])]


def test_live_packed_decode():
    cfg = K.parse_options({})
    check("(live) Default packing = packed", cfg["packing"] == "packed")
    for tk, dt, exp_t, exp_d in LIVE_PACKED:
        t = K.decode_ticks(bytes.fromhex(tk), cfg)
        d = K.decode_pres_datas(bytes.fromhex(dt), cfg)
        check("(live) Paket n=%d ticks exakt" % len(t), t == exp_t)
        check("(live) Paket n=%d datas exakt" % len(d), d == exp_d)
        dts = [(b - a) & 0xFFFFFFFF for a, b in zip(t, t[1:])]
        check("(live) Abstand 0,78..0,80 ms", all(93500 <= x <= 96500 for x in dts))
        check("(live) Ruhewert um -1,428e6", all(-1430000 < v < -1426000 for v in d))
    try:
        K.decode_packed(bytes.fromhex(LIVE_PACKED[0][1])[:-1])
        check("(live) verkürzter Puffer -> Fehler", False)
    except ValueError:
        check("(live) verkürzter Puffer -> Fehler", True)


# Live-Test D1 (16.10.2026): 8 Blöcke à <=8 Samples aus dem 64er-Ring (dekodiert)
LIVE_D1 = [(0, [462289465, 462384385, 462479321, 462574299, 462668985, 462763905, 462858825], [-1427901, -1427746, -1427906, -1427725, -1427921, -1427938, -1427640]), (8, [463902365, 463997285, 464092205, 464187125, 464282061, 464376981, 464471917, 464566523], [-1427658, -1427741, -1427844, -1427841, -1428038, -1427964, -1427806, -1427800]), (16, [465515473, 465610409, 465705329, 465800249, 465895185, 465989791, 466084711, 466179647], [-1427644, -1427681, -1427749, -1427591, -1427509, -1427641, -1427408, -1427400]), (24, [467223517, 467318107, 467413027, 467507963, 467602899, 467697819, 467792739, 467887675], [-1427843, -1427994, -1427708, -1427409, -1427500, -1427741, -1427747, -1427628]), (32, [468836519, 468931125, 469026061, 469121181, 469215873, 469310805, 469405647, 469500739], [-1427628, -1427614, -1427658, -1427858, -1427873, -1427708, -1427378, -1427432]), (40, [470639049, 470733969, 470828889, 470923641, 471018557, 471113411, 471208271, 471303269], [-1427666, -1427966, -1427881, -1427963, -1427936, -1427618, -1427793, -1427419]), (48, [472441963, 472536569, 472631489, 472726409, 472821369, 472916147, 473011103, 473105957], [-1427565, -1427591, -1427742, -1427891, -1427936, -1427924, -1427698, -1427282]), (56, [474149623, 474244639, 474339229, 474434149, 474529069, 474623981, 474718901, 474813837], [-1427492, -1427511, -1427694, -1427781, -1427788, -1427840, -1427553, -1427606])]


class _RingQuery:
    """Fake read_prtouch_pres: liefert die Live-Blöcke als gepackte Puffer."""
    def __init__(self, blocks):
        self.blocks = {i: (t, d) for i, t, d in blocks}
        self.sent = []

    @staticmethod
    def _pack(vals):
        n = len(vals)
        mask = 0
        body = b""
        prev = None
        for i, v in enumerate(vals):
            x = v if prev is None else v - prev
            prev = v
            for sz in (1, 2, 3, 4):
                lo, hi = -(1 << (8 * sz - 1)), (1 << (8 * sz - 1)) - 1
                if lo <= x <= hi:
                    break
            mask |= (sz - 1) << (2 * i)
            body += int(x).to_bytes(sz, "little", signed=True)
        mb = (n + 3) // 4
        return bytes([n]) + mask.to_bytes(mb, "big") + body

    def send(self, params):
        self.sent.append(list(params))
        oid, is_src, ch, idx, ln = params
        t, d = self.blocks[idx]
        # Ticks > 2^31 als vorzeichenbehaftete Werte packen (kumuliert ok)
        tt = [x - (1 << 32) if x >= (1 << 31) else x for x in t]
        return {"ticks": self._pack(tt), "datas": self._pack(d), "idx": idx,
                "len": len(d)}


def _wrap32(x):
    return ((int(x) + (1 << 31)) & 0xFFFFFFFF) - (1 << 31)


def _delta_size(x):
    """Bytebreite je Delta wie Düsen-Encoder 0x08006524 (±2^7/2^15/2^23)."""
    if -0x80 <= x <= 0x7F:
        return 1
    if -0x8000 <= x <= 0x7FFF:
        return 2
    if -0x800000 <= x <= 0x7FFFFF:
        return 3
    return 4


class _McuEncoder:
    """Nachbau Encoder 0x08006524 + Abschluss 0x080064D8 (Firmware-Analyse):
    Delta zum Vorgänger (32 Bit), 0x80000000 übersprungen, Rückgabe =
    Datenbytes + (n_alt >> 2) + 2 (= Paketgröße)."""

    def __init__(self):
        self.prev = 0
        self.n = 0
        self.mask = 0
        self.body = b""

    def add(self, v):
        if int(v) & 0xFFFFFFFF == 0x80000000:
            return len(self.body) + (self.n >> 2) + 2
        n_old = self.n
        x = _wrap32(int(v) - self.prev)
        self.prev = int(v)
        sz = _delta_size(x)
        self.mask |= (sz - 1) << (2 * self.n)
        self.body += x.to_bytes(sz, "little", signed=True)
        self.n += 1
        return len(self.body) + (n_old >> 2) + 2

    def finish(self):
        if self.n == 0:
            return b""
        return (bytes([self.n]) + self.mask.to_bytes((self.n + 3) // 4, "big")
                + self.body)


class _McuRegister:
    """Nachbau read_prtouch_pres (Düse 0x08003F60) bzw. read_prtouch_step:
    64er-SCHIEBEREGISTER (Index 0 = ältestes), Antwort endet sobald
    ticks+datas > 41 Byte (Überlauf-Sample enthalten), len-Feld =
    Endindex - idx + 1 (bei vollständigem Bereich also n + 1, Analyse
    der Düsen-Firmware). advance = Samples, die nach jeder Abfrage nachgeschoben werden
    (laufendes Register)."""

    def __init__(self, ticks, raw, filt=None, step=False, advance=0):
        self.ticks = [int(t) for t in ticks]
        self.raw = [int(v) for v in raw]
        self.filt = [int(v) for v in (filt if filt is not None else raw)]
        assert len(self.ticks) == len(self.raw) == len(self.filt) == 64
        self.step = step
        self.advance = int(advance)
        self.sent = []
        self.ns = []
        self.lens = []
        self._k = 0

    def _shift(self):
        self._k += 1
        t = (self.ticks[-1] + 94900) & 0xFFFFFFFF
        v = self.raw[-1] + (37 if self._k % 2 else -41)
        self.ticks = self.ticks[1:] + [t]
        self.raw = self.raw[1:] + [v]
        self.filt = self.filt[1:] + [(self._k * 13) % 200 - 100]

    def send(self, params):
        self.sent.append(list(params))
        if self.step:
            oid, idx, ln = params
            src = self.raw
        else:
            oid, is_src, ch, idx, ln = params
            if is_src >= 2:
                return {"tri_chxs": 0, "buf_len": 64, "idx": 0, "len": 0,
                        "ticks": b"", "datas": b""}
            src = self.raw if is_src == 1 else self.filt
        end = min(64, idx + ln)
        et, ed = _McuEncoder(), _McuEncoder()
        k = idx
        while k < end:
            if et.add(self.ticks[k]) + ed.add(src[k]) > 41:
                break
            k += 1
        res = {"ticks": et.finish(), "datas": ed.finish(), "idx": idx,
               "len": (k - idx) + 1, "buf_len": 64}
        self.ns.append(et.n)
        self.lens.append(res["len"])
        for _ in range(self.advance):
            self._shift()
        return res


def test_live_ring_read():
    """Live-Werte (Test D1) im Schieberegister-Nachbau: Index 0 leer (Tick 0,
    Wert 0 = Register noch nicht voll), danach 63 echte Samples."""
    cfg = K.parse_options({})
    t_live = [t for _, ts, _ in LIVE_D1 for t in ts]
    d_live = [v for _, _, ds in LIVE_D1 for v in ds]
    reg = _McuRegister([0] + t_live, [0] + d_live)
    pres = K.KlippyPresMcu(None, None, cfg, 0, {})
    pres._q_read = reg
    t, d = pres.read_pres()
    idxs = [p[3] for p in reg.sent]
    exp_idx = [sum(reg.ns[:i]) for i in range(len(reg.ns))]
    check("(ring) idx += n (n aus dem Paket), len = 64 - idx, is_src 1",
          idxs == exp_idx and all(p[1] == 1 and p[4] == 64 - p[3]
                                  for p in reg.sent), str(reg.sent))
    check("(ring) Blockgrößen 7-9 (41-Byte-Grenze) ns=%s" % reg.ns,
          all(7 <= n <= 9 for n in reg.ns[:-1]) and sum(reg.ns) == 64)
    check("(ring) leerer Eintrag (Tick 0/Wert 0) verworfen, 63 Samples",
          len(d) == 63 and 0 not in t and t == t_live and d == d_live)
    check("(ring) Ticks streng steigend", all(b > a for a, b in zip(t, t[1:])))
    check("(ring) Rohwerte im Ruhebereich", all(-1430000 < v < -1426000 for v in d))
    ok, info = K.check_rest_ok(d, cfg, ticks=t)
    check("(ring) Ruhetest inkl. Tickabstand ok (dif=%d, Median %.4f ms)"
          % (info["dif"], info.get("tick_median_ms") or 0), ok, str(info))
    t2, d2 = K.merge_ring([5, 3, 5, 4], [50, 30, 51, 40], 64)
    check("(ring) merge_ring sortiert + entfernt Duplikate", t2 == [3, 4, 5] and d2 == [30, 40, 51])
    t3, _ = K.merge_ring([0xFFFFFFF0, 0x10, 0xFFFFFFF8], [1, 3, 2], 64)
    check("(ring) merge_ring u32-Überlauf", t3 == [0xFFFFFFF0, 0xFFFFFFF8, 0x10])


class _SwapPres:
    def __init__(self):
        self.levels = []

    def set_swap(self, level):
        self.levels.append(level)
        return {"err": 0, "expar0": 0, "expar1": 71}


class _SwapStep:
    def __init__(self, answers):
        self.answers = list(answers)

    def read_swap(self):
        return {"err": 0, "expar0": self.answers.pop(0), "expar1": 71}


def test_swap_mechanism():
    cfg = K.parse_options({})
    # Erfolgstabelle aus der Emulation des Stock-Moduls (16.10.2026):
    for ans, exp in (((0, 1), True), ((1, 0), False), ((0, 0), False), ((1, 1), False)):
        pres, step = _SwapPres(), _SwapStep(ans)
        ctl = K.K2PrtouchController(cfg, pres, step, FakeToolhead(z_start=1.0),
                                    pause=lambda s: None)
        try:
            r = ctl.test_swap()
            got = True
            vers = (r["pres"]["pres_ver"], r["step"]["step_ver"])
        except K.K2PrtouchError:
            got = False
            vers = None
        check("(swap) Pegel %s -> %s" % (ans, "Success" if exp else "ERROR"), got is exp)
        check("(swap) Reihenfolge sta_swap 0, dann 1", pres.levels == [0, 1])
        if exp:
            check("(swap) Versionen 71/71", vers == (71, 71))



# =====================================================================
# Stock-Konfig -> MCU-Parameter (BELEGT per Emulation 16.10.2026)
# =====================================================================

def test_stock_mapping():
    cfg = K.parse_options({})
    tup = list(K.build_start_pres_tuple(cfg, 7))
    check("(map) Default start_prtouch_pres wie Stock",
          tup == [7, 60, 93750, 5, 1000, 800, 4000, 10000, 500, 128], str(tup))
    check("(map) Default aqc_tick 46875",
          list(K.build_start_step_tuple(cfg, 3)) == [3, 46875])
    cfg2 = K.parse_options({"pres_tri_fter": "7, 3, 0.25",
                            "pres_tri_hold": "4100, 10200, 530",
                            "pres_ded_tkms": "96.5",
                            "pres_acq_tkms": "0.5",
                            "step_acq_tkms": "0.5"})
    tup2 = list(K.build_start_pres_tuple(cfg2, 0))
    check("(map) Variation 7,3,0.25/4100,10200,530/96.5/0.5ms",
          tup2 == [0, 60, 60000, 7, 3000, 250, 4100, 10200, 530, 96], str(tup2))
    check("(map) Variation aqc_tick 60000", cfg2["aqc_tick"] == 60000)
    p = K.stock_to_mcu_params(60, 0.78125, (5, 1, 0.8), (4000, 10000, 500),
                              128.0, 0.390625)
    check("(map) lmt_dead abgeschnitten (96.9 -> 96)",
          K.stock_to_mcu_params(60, 0.78125, (5, 1, 0.8), (1, 2, 3), 96.9,
                                0.39)["lmt_dead"] == 96)
    check("(map) stock_to_mcu_params rein", p["acq_tick"] == 93750
          and p["aqc_tick"] == 46875 and p["ned_hftr"] == 1000)
    cfg3 = K.parse_options({"lmt_dead": "77", "pres_cfg_regs": "44",
                            "arm_settle_s": "0.3"})
    check("(map) direkte MCU-Option übersteuert",
          cfg3["lmt_dead"] == 77 and cfg3["cfg_regs"] == 44
          and cfg3["ned_lftr"] == 800)
    try:
        K.parse_options({"ned_lftr_scale": "10"})
        check("(map) ned_lftr_scale abgelehnt", False)
    except ValueError:
        check("(map) ned_lftr_scale abgelehnt", True)


# =====================================================================
# Probe-Hauptweg: K2PrtouchProbeCore mit Fakes (kein Klipper)
# =====================================================================

class ProbeLine:
    """Simuliert die Swap-Leitung PA15 (Düse) -> PC7 (Main)."""

    def __init__(self):
        self.level = 1
        self.armed = False
        self.stuck_low = False      # Leitung hängt auf 0 (ausgelöst)
        self.trigger_on_arm = False  # Düse löst sofort beim Scharfschalten aus
        self.ack_err = 0
        self.rest_noisy = False
        self.sensor_stuck = False   # Sensor ab: alle 5 ms Rohwert -1
        self.filt_noisy = False     # gefilterter Kanal zu nah an min_hold
        self.log = []

    def triggered(self):
        self.log.append("query")
        return self.stuck_low or self.level == 0


class FakeProbePres:
    def __init__(self, line):
        self.line = line

    def set_swap(self, level):
        self.line.log.append("swap%d" % level)
        self.line.level = int(level)
        return {"err": 0, "expar0": 0, "expar1": 71}

    def start_sampling(self):
        self.line.log.append("start_pres")
        if self.line.ack_err:
            return {"err": self.line.ack_err, "expar0": 0, "expar1": 0}
        self.line.armed = True
        self.line.level = 0 if self.line.trigger_on_arm else 1
        return {"err": 0, "expar0": 0, "expar1": 71}

    @staticmethod
    def _ticks():
        # 0,78125 ms = 93 750 Takte (1280 Hz, cfg_regs 60)
        return [10000000 + i * 93750 for i in range(64)]

    def read_pres(self):
        self.line.log.append("read_pres")
        if self.line.sensor_stuck:
            # Firmware-Analyse: DOUT hängt high -> alle 5 ms gelesen, Wert -1
            return [10000000 + i * 600000 for i in range(64)], [-1] * 64
        base = -1428000
        d = [base + ((5000 if i % 2 else -5000) if self.line.rest_noisy
                     else det_noise(i, 300)) for i in range(64)]
        return self._ticks(), d

    def read_pres_filtered(self):
        self.line.log.append("read_ftr")
        amp = 2500 if self.line.filt_noisy else 300
        return self._ticks(), [det_noise(3 * i + 1, amp) for i in range(64)]

    def disarm(self):
        self.line.log.append("stop_pres1")
        self.line.armed = False
        self.line.level = 1
        return {"err": 0, "expar0": 0, "expar1": 71}


class FakeProbeStep:
    def __init__(self, line, fail_disarm=False):
        self.line = line
        self.fail_disarm = fail_disarm

    def cont_step(self):
        self.line.log.append("cont")
        return {"cnt_x": 0, "cnt_y": 0, "cnt_z": 0}

    def start_sampling(self):
        self.line.log.append("start_step")
        return {"err": 0, "expar0": 0, "expar1": 71}

    def disarm(self):
        self.line.log.append("stop_step")
        if self.fail_disarm:
            raise RuntimeError("Timeout")
        return {"err": 0}


class FakeDescend:
    def __init__(self, line, raise_exc=None, z_true=0.0):
        self.line = line
        self.raise_exc = raise_exc
        self.z_true = z_true
        self.results = []
        self.calls = []

    def descend_until_trigger(self, gcmd, params):
        self.line.log.append("DESCEND")
        self.calls.append(dict(params))
        if self.raise_exc is not None:
            raise self.raise_exc
        if self.line.armed:
            self.line.level = 0  # Kontakt zieht PA15/PC7 auf 0
        self.results.append(self.z_true)

    def pull_trigger_positions(self):
        r, self.results = self.results, []
        return r

    def clear_trigger_positions(self):
        self.results = []


class FakeGcmd:
    def __init__(self, params=None, command="PROBE"):
        self.params = dict(params or {})
        self.command = command

    def get_command(self):
        return self.command


def make_core(cfg_over=None, line=None, raise_exc=None, fail_disarm=False):
    # arm_settle_s 0.2 (Default; seit 17.10. >= 0,15 s Pflicht, die Fakes
    # pausieren nicht wirklich)
    base = {"allow_move": True, "arm_settle_s": 0.2}
    base.update(cfg_over or {})
    cfg = K.parse_options(base)
    line = line or ProbeLine()
    pauses = []
    waits = []

    def get_params(gcmd):
        return {"probe_speed": float(gcmd.params.get("PROBE_SPEED",
                                                     cfg["speed"])),
                "lift_speed": cfg["lift_speed"]}

    desc = FakeDescend(line, raise_exc=raise_exc)
    core = K.K2PrtouchProbeCore(cfg, get_params, desc, line.triggered,
                                lambda: waits.append(1), pauses.append)
    core.attach(FakeProbePres(line), FakeProbeStep(line, fail_disarm))
    core._test_pauses = pauses
    return core, line, desc, waits


EXPECT_ORDER = ["swap0", "query", "swap1", "query", "cont", "start_pres",
                "start_step", "read_pres", "read_ftr", "query", "DESCEND",
                "stop_step", "stop_pres1", "query"]


def _run(core, gcmd=None):
    gcmd = gcmd or FakeGcmd()
    try:
        sess = core.start_probe_session(gcmd)
    except K.K2PrtouchError as exc:
        return None, exc
    try:
        sess.run_probe(gcmd)
        return sess.pull_probed_results(), None
    except K.K2PrtouchError as exc:
        return None, exc
    finally:
        sess.end_probe_session()


def test_probe_arm_disarm_order():
    core, line, desc, waits = make_core()
    res, err = _run(core)
    check("(probe) Antastung ok", err is None and res == [0.0], str(err))
    check("(probe) Reihenfolge Arm vor Fahrt / Disarm danach (Stock)",
          line.log == EXPECT_ORDER, str(line.log))
    check("(probe) wait_moves vor dem Scharfschalten", len(waits) == 1)
    check("(probe) danach entschärft, PC7 wieder 1",
          not core.armed and line.level == 1 and core.arm_count == 1
          and core.disarm_count == 1)


def test_probe_disarm_on_exception():
    core, line, desc, _ = make_core(raise_exc=K.K2PrtouchError(
        "No trigger on probe after full movement"))
    res, err = _run(core)
    check("(probe) Ausnahme in der Fahrt wird weitergereicht",
          err is not None and "No trigger" in str(err), str(err))
    check("(probe) bei Ausnahme trotzdem entschärft",
          line.log[-3:] == ["stop_step", "stop_pres1", "query"]
          and not core.armed, str(line.log))
    # Auch fremde Ausnahmen (z.B. KeyboardInterrupt-ähnlich) -> entschärft:
    core2, line2, _, _ = make_core(raise_exc=ValueError("intern"))
    try:
        core2.run_probe(FakeGcmd())
        ok = False
    except ValueError:
        ok = True
    check("(probe) fremde Ausnahme -> entschärft + weitergereicht",
          ok and "stop_pres1" in line2.log and not core2.armed)
    # Disarm-Fehler nach erfolgreicher Fahrt -> Fehler, pres trotzdem versucht
    core3, line3, _, _ = make_core(fail_disarm=True)
    res3, err3 = _run(core3)
    check("(probe) Disarm-Fehler gemeldet, pres-Stop trotzdem gesendet",
          err3 is not None and "Entschärfen" in str(err3)
          and "stop_pres1" in line3.log, str(err3))


def test_probe_ack_error():
    line = ProbeLine()
    line.ack_err = 3
    core, line, desc, _ = make_core(line=line)
    res, err = _run(core)
    check("(probe) ack err!=0 -> Abbruch", err is not None
          and "err=3" in str(err), str(err))
    check("(probe) ack err -> keine Fahrt, entschärft",
          "DESCEND" not in line.log and "stop_pres1" in line.log)


def test_probe_pc7_already_triggered():
    # Ohne Signalweg-Test: Düse löst sofort beim Scharfschalten aus.
    line = ProbeLine()
    line.trigger_on_arm = True
    core, line, desc, _ = make_core({"swap_check": False}, line=line)
    res, err = _run(core)
    check("(probe) PC7 vor Fahrt ausgelöst -> Abbruch",
          err is not None and "VOR der Fahrt" in str(err), str(err))
    check("(probe) ausgelöst -> keine Fahrt, entschärft",
          "DESCEND" not in line.log and "stop_pres1" in line.log)
    # Mit Signalweg-Test: hängende Leitung fällt schon dort auf.
    line2 = ProbeLine()
    line2.stuck_low = True
    core2, line2, _, _ = make_core(line=line2)
    res2, err2 = _run(core2)
    check("(probe) hängende Leitung -> Signalweg-Test schlägt fehl",
          err2 is not None and "Signalweg" in str(err2)
          and "DESCEND" not in line2.log and "start_pres" not in line2.log,
          str(err2))
    # Ruhetest unruhig -> Abbruch vor Fahrt
    line3 = ProbeLine()
    line3.rest_noisy = True
    core3, line3, _, _ = make_core(line=line3)
    res3, err3 = _run(core3)
    check("(probe) Ruhetest unruhig -> Abbruch vor Fahrt",
          err3 is not None and "Ruhetest" in str(err3)
          and "DESCEND" not in line3.log and "stop_pres1" in line3.log)


def test_probe_allow_move_blocks():
    core, line, desc, waits = make_core({"allow_move": False})
    res, err = _run(core)
    check("(probe) allow_move False -> verständlicher Fehler",
          err is not None and "allow_move" in str(err), str(err))
    check("(probe) allow_move False -> kein MCU-Befehl, keine Fahrt",
          line.log == [] and not waits and not desc.calls, str(line.log))
    try:
        core.run_probe(FakeGcmd())
        ok = False
    except K.K2PrtouchError:
        ok = True
    check("(probe) auch run_probe direkt gesperrt", ok and line.log == [])
    core2, line2, _, _ = make_core()
    core2.attach(None, None)
    res2, err2 = _run(core2)
    check("(probe) ungebunden (vor connect) -> Abbruch ohne Fahrt",
          err2 is not None and "verbunden" in str(err2)
          and "DESCEND" not in line2.log)


def test_probe_speed_limit():
    core, line, desc, _ = make_core()
    res, err = _run(core, FakeGcmd({"PROBE_SPEED": "10"}))
    check("(probe) PROBE_SPEED=10 abgelehnt", err is not None
          and "5.0 mm/s" in str(err) and line.log == [], str(err))
    core2, line2, desc2, _ = make_core()
    gcmd = FakeGcmd({"PROBE_SPEED": "5"}, command="G28")
    res2, err2 = _run(core2, gcmd)
    check("(probe) PROBE_SPEED=5 erlaubt", err2 is None
          and desc2.calls[0]["probe_speed"] == 5.0, str(err2))
    for opt in ("speed", "probe_speed"):
        try:
            K.parse_options({opt: "5.5"})
            check("(probe) Config %s 5.5 abgelehnt" % opt, False)
        except ValueError:
            check("(probe) Config %s 5.5 abgelehnt" % opt, True)
    cfg = K.parse_options({"probe_speed": "3"})
    check("(probe) speed fällt auf probe_speed zurück", cfg["speed"] == 3.0)
    for v in ("10.5", "0"):
        try:
            K.parse_options({"probe_max_travel_mm": v})
            check("(probe) probe_max_travel_mm=%s abgelehnt" % v, False)
        except ValueError:
            check("(probe) probe_max_travel_mm=%s abgelehnt" % v, True)


def test_probe_target_z():
    f = K.compute_probe_target_z
    check("(probe) Ziel ohne G28 begrenzt auf max_travel",
          abs(f(5.0, -5.0, 8.0, False) - (-3.0)) < 1e-12)
    check("(probe) Ziel nie unter position_min",
          abs(f(2.0, -1.0, 8.0, False) - (-1.0)) < 1e-12)
    check("(probe) G28: bis position_min (Upstream)",
          abs(f(300.0, -5.0, 8.0, True) - (-5.0)) < 1e-12)
    cfg = K.parse_options({})
    check("(probe) Defaults: mode probe, allow_move False, z_offset 0, "
          "position_endstop = z_offset",
          cfg["mode"] == "probe" and cfg["allow_move"] is False
          and cfg["z_offset"] == 0.0 and cfg["position_endstop"] == 0.0)
    cfg2 = K.parse_options({"z_offset": "0.1"})
    check("(probe) position_endstop folgt z_offset",
          cfg2["position_endstop"] == 0.1)
    try:
        K.parse_options({"mode": "magic"})
        check("(probe) mode ungültig abgelehnt", False)
    except ValueError:
        check("(probe) mode ungültig abgelehnt", True)


class _Q:
    """Fake lookup_query_command-Objekt (zeichnet send-Daten auf)."""

    def __init__(self, name, fn):
        self.name, self.fn, self.sent = name, fn, []

    def send(self, data=(), minclock=0, reqclock=0, retry=True):
        self.sent.append(list(data))
        return self.fn(list(data))


def test_klippy_adapters_disarm_and_frozen():
    cfg = K.parse_options({})
    pres = K.KlippyPresMcu(None, None, cfg, 4, {})
    pres._q_stop = _Q("stop", lambda d: {"err": 0, "expar0": 0,
                                         "expar1": 71})
    pres.stop_sampling(ok=False)
    pres.disarm()
    check("(adapter) stop/disarm senden immer sta_swap=1",
          pres._q_stop.sent == [[4, 1], [4, 1]], str(pres._q_stop.sent))
    pres._q_start = _Q("start", lambda d: {"err": 2})
    check("(adapter) start_sampling liefert ack-Dict",
          pres.start_sampling()["err"] == 2
          and pres._q_start.sent[0] == [4, 60, 93750, 5, 1000, 800, 4000,
                                        10000, 500, 128])
    blk = _RingQuery._pack(list(range(100, 108)))
    pres._q_read = _Q("read", lambda d: {"ticks": b"" if d[4] == 0 else blk,
                                         "datas": b"" if d[4] == 0 else blk,
                                         "tri_chxs": 1})
    fr = pres.read_frozen()
    sent = pres._q_read.sent
    exp = ([[4, 2, 0, 0, 0]]
           + [[4, 0, 0, i, 64 - i] for i in range(0, 64, 8)]
           + [[4, 1, 0, i, 64 - i] for i in range(0, 64, 8)])
    check("(adapter) eingefrorener Ring: Stock-Abfragefolge", sent == exp,
          str(sent[:3]))
    check("(adapter) read_frozen dekodiert ftr/src", "ftr" in fr
          and "src" in fr and fr["status"].get("tri_chxs") == 1)
    step = K.KlippyStepMcu(None, None, cfg, 9)
    step._q_read = _Q("read", lambda d: {"ticks": b"" if d[2] == 0 else blk,
                                         "datas": b"" if d[2] == 0 else blk})
    step.read_frozen()
    exp2 = [[9, 0, 0]] + [[9, i, 64 - i] for i in range(0, 64, 8)]
    check("(adapter) step-Ring: Stock-Abfragefolge",
          step._q_read.sent == exp2)


# =====================================================================
# Integration gegen ECHTE Upstream-Dateien (optional, nur wenn die lokale
# Upstream-Kopie vorhanden ist): probe.py, homing.py (Homing-Klasse),
# manual_probe.py, gcode.py, pins.py – mit Fake-MCU/-Toolhead.
# =====================================================================

# Pfad zum klippy/-Ordner eines Upstream-Klipper-Checkouts: Umgebungsvariable
# KLIPPER_SRC (Checkout-Wurzel oder klippy/), sonst lokal/upstream-klipper.
_ks = os.environ.get("KLIPPER_SRC", "")
if _ks and os.path.isdir(os.path.join(_ks, "klippy")):
    _ks = os.path.join(_ks, "klippy")
UPSTREAM_KLIPPY = os.path.normpath(_ks or os.path.join(
    _HERE, "..", "lokal", "upstream-klipper", "klippy"))


class _Ev:
    def __init__(self):
        self.handlers = {}

    def register(self, ev, cb):
        self.handlers.setdefault(ev, []).append(cb)

    def send(self, ev, *a):
        return [cb(*a) for cb in self.handlers.get(ev, [])]


def _integration_env(cfg_over=None, z_true=0.0, homing_speed=5.0,
                     extruder=None):
    """extruder (nur pres_check-Tests): None = kein Extruder-Objekt (wie
    bisher); dict(mcu='nozzle_mcu'|'mcu', oid=int, stepper=True|False)."""
    import collections
    if UPSTREAM_KLIPPY not in sys.path:
        sys.path.append(UPSTREAM_KLIPPY)
    import gcode as ugcode
    import pins as upins
    from extras import homing as uhoming

    line = ProbeLine()
    line.z_true = z_true
    rec = {"moves": [], "probing": [], "config": [], "homing_start": 0}

    class Reactor:
        def monotonic(self):
            return 100.0

        def pause(self, t):
            return t

    class Toolhead:
        def __init__(self):
            self.pos = [100.0, 100.0, 5.0, 0.0]
            self.homed = "xyz"

        def get_position(self):
            return list(self.pos)

        def set_position(self, pos, homing_axes=""):
            self.pos = list(pos)
            if homing_axes:
                self.homed = "".join(sorted(set(self.homed + homing_axes)))

        def manual_move(self, coord, speed):
            for i, v in enumerate(coord):
                if v is not None:
                    self.pos[i] = float(v)
            rec["moves"].append((list(self.pos), speed))

        def move(self, pos, speed):
            self.pos = list(pos)
            rec["moves"].append((list(self.pos), speed))

        def wait_moves(self):
            line.log.append("wait")

        def get_last_move_time(self):
            return 1.0

        def get_status(self, eventtime):
            return {"homed_axes": self.homed}

        def get_kinematics(self):
            return Kin()

        def get_extruder(self):
            return printer.objects.get("extruder")

    class Stepper:
        def __init__(self, name, oid):
            self.name, self.oid = name, oid

        def get_name(self, short=False):
            return self.name

        def get_oid(self):
            return self.oid

        def is_active_axis(self, a):
            return self.name.startswith("stepper_" + a)

    class Kin:
        def get_steppers(self):
            return [Stepper("stepper_x", 11), Stepper("stepper_y", 12),
                    Stepper("stepper_z", 13), Stepper("stepper_z1", 14)]

    class Endstop:
        def __init__(self, mcu, pin_params):
            self.mcu, self.pp = mcu, pin_params
            self.steppers = []

        def get_mcu(self):
            return self.mcu

        def add_stepper(self, s):
            self.steppers.append(s.get_name())

        def get_steppers(self):
            return list(self.steppers)

        def home_start(self, *a, **k):
            rec["homing_start"] += 1
            return None

        def home_wait(self, t):
            if line.armed:
                line.level = 0
                return 1.0
            return 0.0

        def query_endstop(self, print_time):
            # wie mcu.py:406-411: pin_value ^ invert
            line.log.append("query")
            return (0 if line.stuck_low else line.level) ^ self.pp["invert"]

    class Mcu:
        def __init__(self, name):
            self.name, self.oid, self.cbs = name, 0, []

        def register_config_callback(self, cb):
            self.cbs.append(cb)

        def create_oid(self):
            self.oid += 1
            return self.oid - 1

        def add_config_cmd(self, cmd, is_init=False, on_restart=False):
            rec["config"].append((self.name, cmd))

        def setup_pin(self, pin_type, pin_params):
            assert pin_type == "endstop"
            rec["endstop_pin"] = dict(pin_params)
            return Endstop(self, pin_params)

        def estimated_print_time(self, t):
            return 0.0

        # nur pres_check (apax): Wächter-Stopp + Antwortregistrierung
        def lookup_command(self, msgformat, cq=None):
            name = msgformat.split()[0]

            def fn(d):
                rec.setdefault("nowait", []).append((name, list(d)))
                return None
            return _Q(name, fn)

        def register_serial_response(self, cb, msgformat, oid=None):
            rec["apax_sink"] = (cb, msgformat, oid, self.name)

        def lookup_query_command(self, msgformat, respformat, oid=None,
                                 cq=None, is_async=False):
            name = msgformat.split()[0]
            pres = FakeProbePres(line)
            step = FakeProbeStep(line)
            regs = {}

            def fn(d):
                if name == "start_prtouch_pres":
                    rec["start_pres"] = list(d)
                    return pres.start_sampling()
                if name == "stop_prtouch_pres":
                    rec.setdefault("stop_pres", []).append(list(d))
                    if d[1] == 1 and line.armed:
                        return pres.disarm()
                    return pres.set_swap(d[1])
                if name == "read_prtouch_pres":
                    if d[1] >= 2:  # Status-Abfrage (is_src >= 2)
                        return {"tri_chxs": 0, "buf_len": 64, "idx": 0,
                                "len": 0, "ticks": b"", "datas": b""}
                    if d[3] == 0:  # neuer Ringlesevorgang = 1 Logeintrag
                        t, v = (pres.read_pres() if d[1] == 1
                                else pres.read_pres_filtered())
                        regs[d[1]] = _McuRegister(t, v)
                    return regs[d[1]].send(d)
                if name == "cont_prtouch_step":
                    return step.cont_step()
                if name == "start_prtouch_step":
                    rec["start_step"] = list(d)
                    return step.start_sampling()
                if name == "stop_prtouch_step":
                    step.disarm()
                    return {"err": 0, "expar0": line.level, "expar1": 71}
                if name == "start_prtouch_apax":
                    rec.setdefault("apax", []).append(("start", list(d)))
                    hook = rec.get("on_apax_start")
                    if hook is not None:
                        hook(rec["apax_sink"][0])
                    return {"err": 0, "expar0": 0, "expar1": d[2]}
                if name == "stop_prtouch_apax":
                    rec.setdefault("apax", []).append(("stop", list(d)))
                    return {"err": rec.get("apax_stop_err", 0),
                            "expar0": 0, "expar1": 0}
                return {}
            return _Q(name, fn)

    class Homing:
        """Fake PrinterHoming: probing_move simuliert HomingMove grob."""

        check_probe_first_home = uhoming.PrinterHoming.check_probe_first_home

        def probing_move(self, mcu_probe, pos, speed, check_movement=True):
            line.log.append("DESCEND")
            rec["probing"].append((list(pos), speed, check_movement))
            mcu_probe.home_start(0., 0., 0, 0.)
            if mcu_probe.home_wait(1.0) <= 0.:
                raise ugcode.CommandError("No trigger on probe after full "
                                          "movement")
            th.pos[2] = max(pos[2], th.pos[2] - (th.pos[2] - line.z_true))
            return list(th.pos)

    class Gcode:
        Coord = ugcode.Coord

        def __init__(self):
            self.cmds, self.out = {}, []
            # Echte Upstream-Namensprüfung (gcode.py register_command) ohne
            # GCodeDispatch.__init__ – fängt ungültige Namen wie K2_* ab.
            real = ugcode.GCodeDispatch.__new__(ugcode.GCodeDispatch)
            real.printer = Printer
            real.ready_gcode_handlers = real.gcode_handlers = {}
            real.base_gcode_handlers, real.gcode_help = {}, {}
            self._real = real

        def register_command(self, cmd, fn, when_not_ready=False, desc=None):
            assert cmd not in self.cmds, cmd
            self._real.register_command(cmd, fn, when_not_ready, desc)
            # Parser-Gegenprobe: Befehlszeile muss genau diesen Namen ergeben.
            parts = ugcode.GCodeDispatch.args_r.split(cmd.upper())
            assert ''.join(parts[:3]).strip() == cmd, "Parser zerlegt %r" % cmd
            self.cmds[cmd] = fn

        def respond_info(self, msg, log=True):
            self.out.append(msg)

        def respond_raw(self, msg):
            self.out.append(msg)

        def run_script_from_command(self, script):
            rec.setdefault("scripts", []).append(script)

        def create_gcode_command(self, command, commandline, params):
            return ugcode.GCodeCommand(self, command, commandline, params,
                                       False)

    class Printer:
        command_error = ugcode.CommandError
        config_error = RuntimeError

        def __init__(self):
            self.objects, self.ev = {}, _Ev()

        def lookup_object(self, name, default=RuntimeError):
            if name in self.objects:
                return self.objects[name]
            if default is RuntimeError:
                raise RuntimeError("Unknown config object '%s'" % name)
            return default

        def add_object(self, name, obj):
            if name in self.objects:
                raise RuntimeError("Printer object '%s' already created"
                                   % name)
            self.objects[name] = obj

        def register_event_handler(self, ev, cb):
            self.ev.register(ev, cb)

        def send_event(self, ev, *a):
            return self.ev.send(ev, *a)

        def get_reactor(self):
            return reactor

        def get_start_args(self):
            return {}

        def is_shutdown(self):
            return False

    class Config:
        error = ValueError

        def __init__(self, name, opts, sections=None):
            self.name, self.opts = name, dict(opts)
            self.sections = sections or {}

        def get_printer(self):
            return printer

        def get_name(self):
            return self.name

        def get(self, o, default=None, note_valid=True):
            return self.opts.get(o, default)

        def getfloat(self, o, default=None, minval=None, maxval=None,
                     above=None, below=None, note_valid=True):
            return float(self.opts.get(o, default))

        def getint(self, o, default=None, minval=None, maxval=None,
                   note_valid=True):
            return int(self.opts.get(o, default))

        def getboolean(self, o, default=None, note_valid=True):
            return bool(self.opts.get(o, default))

        def getchoice(self, o, choices, default=None, note_valid=True):
            return self.opts.get(o, default)

        def has_section(self, s):
            return s in self.sections

        def getsection(self, s):
            return self.sections[s]

        def get_prefix_sections(self, p):
            return []

    reactor = Reactor()
    printer = Printer()
    th = Toolhead()
    gc = Gcode()
    pins = upins.PrinterPins()
    mcu, noz = Mcu("mcu"), Mcu("nozzle_mcu")
    pins.register_chip("mcu", mcu)
    pins.register_chip("nozzle_mcu", noz)
    for n, o in (("gcode", gc), ("toolhead", th), ("pins", pins),
                 ("mcu", mcu), ("mcu nozzle_mcu", noz),
                 ("homing", Homing())):
        printer.add_object(n, o)
    if extruder is not None:
        class _Heater:
            can_extrude = bool(extruder.get("hot", True))

            def get_temp(self, t):
                return (float(extruder.get("temp", 220.0)), 220.0)

        class _EStepper:
            def __init__(self, m, oid):
                self._m, self._oid = m, oid

            def get_mcu(self):
                return self._m

            def get_oid(self):
                return self._oid

        class _ES:
            pass

        class _Extruder:
            def __init__(self):
                self.extruder_stepper = None
                if extruder.get("stepper", True):
                    self.extruder_stepper = _ES()
                    self.extruder_stepper.stepper = _EStepper(
                        noz if extruder.get("mcu", "nozzle_mcu")
                        == "nozzle_mcu" else mcu, extruder.get("oid", 7))

            def get_name(self):
                return "extruder"

            def get_heater(self):
                return _Heater()

        printer.add_object("extruder", _Extruder())
    opts = {"allow_move": "True", "arm_settle_s": "0.2"}
    opts.update(cfg_over or {})
    zsec = Config("stepper_z", {"position_min": "-5"})
    config = Config("k2_prtouch", opts, {"stepper_z": zsec})
    obj = K.load_config(config)
    printer.add_object("k2_prtouch", obj)
    # Config-Phase + identify + connect wie Klipper:
    for m in (noz, mcu):
        for cb in m.cbs:
            cb()
    printer.send_event("klippy:mcu_identify")
    printer.send_event("klippy:connect")
    HomingInfo = collections.namedtuple(
        "homing_info", ["speed", "position_endstop", "retract_speed",
                        "retract_dist", "positive_dir",
                        "second_homing_speed"])
    hinfo = HomingInfo(homing_speed, 0.0, homing_speed, 0.0, False,
                       homing_speed / 2.0)
    return {"printer": printer, "obj": obj, "gcode": gc, "th": th,
            "line": line, "rec": rec, "pins": pins, "ugcode": ugcode,
            "uhoming": uhoming, "hinfo": hinfo}


def test_upstream_integration():
    if not os.path.isdir(os.path.join(UPSTREAM_KLIPPY, "extras")):
        print("SKIP (upstream) lokale Upstream-Kopie fehlt: %s"
              % UPSTREAM_KLIPPY)
        return
    env = _integration_env()
    gc, rec, line, th = env["gcode"], env["rec"], env["line"], env["th"]
    printer = env["printer"]
    check("(upstream) Objekt 'probe' registriert",
          printer.lookup_object("probe", None) is env["obj"].probe)
    check("(upstream) Endstop auf ^!PC7 (mcu, invert=1, pullup=1)",
          rec["endstop_pin"]["pin"] == "PC7"
          and rec["endstop_pin"]["invert"] == 1
          and rec["endstop_pin"]["pullup"] == 1
          and rec["endstop_pin"]["chip_name"] == "mcu")
    check("(upstream) Upstream-Befehle registriert",
          all(c in gc.cmds for c in ("PROBE", "QUERY_PROBE",
                                     "PROBE_ACCURACY", "PROBE_CALIBRATE",
                                     "Z_OFFSET_APPLY_PROBE",
                                     "KTWO_PRTOUCH_PROBE_STEPS",
                                     "KTWO_PRTOUCH_TEST_SWAP"))
          and "KTWO_PRTOUCH_HOME_Z" not in gc.cmds
          and "KTWO_PRTOUCH_PROBE" not in gc.cmds, str(sorted(gc.cmds)))
    cfgcmds = [c for _, c in rec["config"]]
    check("(upstream) config_prtouch_pres mit Pin-Namen wie Stock",
          "config_prtouch_pres oid=0 idx=0 swp_pin=PA15 clk_pin=PB13 "
          "sdo_pin=PB14" in cfgcmds, str(cfgcmds))
    check("(upstream) config_prtouch_step: X/Y/letzter Z-Stepper, PC7",
          "config_prtouch_step oid=0 oid_xstp=11 oid_ystp=12 oid_zstp=14 "
          "swp_pin=PC7" in cfgcmds, str(cfgcmds))
    ep = env["obj"].probe.mcu_endstop
    check("(upstream) Z-Stepper am Endstop (LookupZSteppers)",
          ep.get_steppers() == ["stepper_z", "stepper_z1"])
    # PROBE über die echte ProbeCommandHelper/SampleAveragingHelper-Kette:
    line.log.clear()
    g = gc.create_gcode_command("PROBE", "PROBE", {})
    gc.cmds["PROBE"](g)
    check("(upstream) PROBE ok + Ergebnis gemeldet",
          any("estimate contact at z=0.000000" in m for m in gc.out),
          str(gc.out[-2:]))
    check("(upstream) PROBE: Stock-Reihenfolge um die Fahrt",
          line.log == ["wait"] + EXPECT_ORDER, str(line.log))
    check("(upstream) start_prtouch_pres/step-Parameter wie Stock",
          rec["start_pres"][1:] == [60, 93750, 5, 1000, 800, 4000, 10000,
                                    500, 128]
          and rec["start_step"][1:] == [46875])
    pos, speed, chk = rec["probing"][-1]
    check("(upstream) PROBE: Ziel 5.0-8.0 = -3.0, 5 mm/s, check_movement",
          abs(pos[2] - (-3.0)) < 1e-9 and speed == 5.0 and chk is True,
          str(rec["probing"][-1]))
    # QUERY_PROBE: invertiert -> 'open' in Ruhe
    gc.cmds["QUERY_PROBE"](gc.create_gcode_command("QUERY_PROBE", "", {}))
    check("(upstream) QUERY_PROBE in Ruhe 'open'", gc.out[-1] == "probe: open")
    # PROBE_ACCURACY SAMPLES=3 -> 3 x Arm/Disarm
    n0 = env["obj"].probe.core.arm_count
    gc.cmds["PROBE_ACCURACY"](gc.create_gcode_command(
        "PROBE_ACCURACY", "PROBE_ACCURACY SAMPLES=3", {"SAMPLES": "3"}))
    core = env["obj"].probe.core
    check("(upstream) PROBE_ACCURACY: 3x scharf/entschärft",
          core.arm_count - n0 == 3 and core.disarm_count == core.arm_count
          and any("probe accuracy results" in m for m in gc.out))
    # PROBE_SPEED=10 -> Fehler, keine Fahrt
    nprob = len(rec["probing"])
    try:
        gc.cmds["PROBE"](gc.create_gcode_command(
            "PROBE", "PROBE PROBE_SPEED=10", {"PROBE_SPEED": "10"}))
        ok = False
    except env["ugcode"].CommandError as exc:
        ok = "5.0 mm/s" in str(exc)
    check("(upstream) PROBE PROBE_SPEED=10 -> CommandError, keine Fahrt",
          ok and len(rec["probing"]) == nprob)
    # G28 Z über die ECHTE Homing-Klasse (homing.py:265-294):
    hv = env["pins"].setup_pin("endstop", "probe:z_virtual_endstop")
    check("(upstream) probe:z_virtual_endstop position_endstop = z_offset",
          hv.get_position_endstop() == 0.0)

    class Rail:
        def get_endstops(self):
            return [(hv, "probe")]

        def get_homing_info(self):
            return env["hinfo"]

    th.pos = [175.0, 175.0, 120.0, 0.0]
    hs = env["uhoming"].Homing(printer)
    line.log.clear()
    hs.home_rails([Rail()], [None, None, 120.0 * 1.5, None],
                  [None, None, 0.0, None])
    pos, speed, chk = rec["probing"][-1]
    check("(upstream) G28 Z: Fahrt bis position_min, 5 mm/s, ohne "
          "check_movement (1. Versuch)",
          abs(pos[2] - (-5.0)) < 1e-9 and speed == 5.0 and chk is False,
          str(rec["probing"][-1]))
    check("(upstream) G28 Z: Z danach = 0 (Kontakt)", abs(th.pos[2]) < 1e-9,
          str(th.pos))
    check("(upstream) G28 Z: scharf vor / entschärft nach Fahrt",
          line.log[:2] == ["wait", "swap0"] and "stop_pres1" in line.log
          and not core.armed, str(line.log))


def test_upstream_integration_guards():
    if not os.path.isdir(os.path.join(UPSTREAM_KLIPPY, "extras")):
        print("SKIP (upstream) lokale Upstream-Kopie fehlt")
        return
    # allow_move False: G28 Z bricht vor jeder Bewegung ab.
    env = _integration_env({"allow_move": "False"})
    hv = env["pins"].setup_pin("endstop", "probe:z_virtual_endstop")

    class Rail:
        def get_endstops(self):
            return [(hv, "probe")]

        def get_homing_info(self):
            return env["hinfo"]

    env["th"].pos = [175.0, 175.0, 120.0, 0.0]
    env["line"].log.clear()
    try:
        env["uhoming"].Homing(env["printer"]).home_rails(
            [Rail()], [None, None, 180.0, None], [None, None, 0.0, None])
        ok = False
    except env["ugcode"].CommandError as exc:
        ok = "allow_move" in str(exc)
    check("(upstream) G28 Z mit allow_move False -> Fehler, keine Fahrt",
          ok and env["rec"]["probing"] == [] and env["line"].log == []
          and env["th"].pos[2] == 120.0, str(env["line"].log))
    # homing_speed 10 in [stepper_z] -> abgelehnt vor Bewegung
    env2 = _integration_env(homing_speed=10.0)
    hv2 = env2["pins"].setup_pin("endstop", "probe:z_virtual_endstop")

    class Rail2(Rail):
        def get_endstops(self):
            return [(hv2, "probe")]

        def get_homing_info(self):
            return env2["hinfo"]

    env2["line"].log.clear()
    try:
        env2["uhoming"].Homing(env2["printer"]).home_rails(
            [Rail2()], [None, None, 180.0, None], [None, None, 0.0, None])
        ok = False
    except env2["ugcode"].CommandError as exc:
        ok = "homing_speed" in str(exc)
    check("(upstream) G28 mit homing_speed 10 -> abgelehnt, keine Fahrt",
          ok and env2["rec"]["probing"] == [] and env2["line"].log == [])
    # Leitung hängt auf 0 -> PROBE bricht ab, Düse entschärft
    env3 = _integration_env()
    env3["line"].stuck_low = True
    env3["line"].log.clear()
    try:
        env3["gcode"].cmds["PROBE"](env3["gcode"].create_gcode_command(
            "PROBE", "PROBE", {}))
        ok = False
    except env3["ugcode"].CommandError as exc:
        ok = "Signalweg" in str(exc)
    check("(upstream) PROBE bei hängender Leitung -> CommandError, keine "
          "Fahrt, entschärft",
          ok and env3["rec"]["probing"] == []
          and env3["line"].log[-3:] == ["stop_step", "swap1", "query"]
          and env3["rec"]["stop_pres"][-1][1] == 1, str(env3["line"].log))
    # mode steps: kein 'probe'-Objekt, Rückfallbefehle registriert
    env4 = _integration_env({"mode": "steps"})
    check("(upstream) mode steps: kein 'probe', HOME_Z/MESH/PROBE_STEPS da",
          env4["printer"].lookup_object("probe", None) is None
          and all(c in env4["gcode"].cmds for c in (
              "KTWO_PRTOUCH_HOME_Z", "KTWO_PRTOUCH_MESH",
              "KTWO_PRTOUCH_PROBE_STEPS"))
          and "PROBE" not in env4["gcode"].cmds)

# =====================================================================
# Absicherung nach Analyse der Düsen-Firmware (17.10.2026) – Punkte 1-6
# =====================================================================

def _raises(opts, needle=None):
    """parse_options muss ValueError werfen (optional mit Textteil)."""
    try:
        K.parse_options(opts)
    except ValueError as exc:
        return needle is None or needle in str(exc)
    return False


def test_1017_config_validation():
    """Punkt 1: Antast-Parameter beim Laden prüfen (kein Start)."""
    cfg = K.parse_options({})
    check("(1017-1) Stock-Defaults gültig, unverändert",
          list(K.build_start_pres_tuple(cfg, 1))
          == [1, 60, 93750, 5, 1000, 800, 4000, 10000, 500, 128])
    check("(1017-1) min_hold == max_hold (pres_tri_hold) -> Stream-Modus abgelehnt",
          _raises({"pres_tri_hold": "4000, 4000, 500"}, "Stream"))
    check("(1017-1) min_hold == max_hold (direkte MCU-Option) abgelehnt",
          _raises({"max_hold": "4000"}, "NIE"))
    check("(1017-1) max_hold != min_hold (auch < min_hold) erlaubt",
          not _raises({"max_hold": "3999"}))
    check("(1017-1) ned_hftr = 0 (pres_tri_fter[1] = 0) -> abgelehnt",
          _raises({"pres_tri_fter": "5, 0, 0.8"}, "Hochpass"))
    check("(1017-1) ned_hftr = 0 (direkt) abgelehnt",
          _raises({"ned_hftr": "0"}, "ned_hftr"))
    check("(1017-1) ned_hftr negativ abgelehnt", _raises({"ned_hftr": "-1"}))
    for v, ok in (("0", True), ("1", True), ("800", True), ("1000", True),
                  ("1001", False), ("5000", False)):
        check("(1017-1) ned_lftr=%s %s" % (v, "erlaubt" if ok else "abgelehnt"),
              _raises({"ned_lftr": v}, "ned_lftr") is (not ok))
    check("(1017-1) pres_tri_fter[2] = 1.2 (-> 1200) abgelehnt",
          _raises({"pres_tri_fter": "5, 1, 1.2"}, "schwingend"))
    check("(1017-1) lmt_dead 63 abgelehnt", _raises({"lmt_dead": "63"}, "SAMPLE"))
    check("(1017-1) pres_ded_tkms 50 (-> 50) abgelehnt",
          _raises({"pres_ded_tkms": "50"}, "lmt_dead"))
    check("(1017-1) lmt_dead 64 erlaubt", not _raises({"lmt_dead": "64"}))
    check("(1017-1) cfg_regs 61 unbekannt -> abgelehnt",
          _raises({"pres_cfg_regs": "61"}, "unbekannt"))
    check("(1017-1) cfg_regs 56 (1280 Hz/PGA64) erlaubt",
          not _raises({"pres_cfg_regs": "56"}))
    check("(1017-1) cfg_regs 44 (640 Hz) mit arm_settle_s 0.3 erlaubt",
          not _raises({"pres_cfg_regs": "44", "arm_settle_s": "0.3"}))
    check("(1017-1) min_hold 999 (< Ruherauschen) abgelehnt",
          _raises({"pres_tri_hold": "999, 10000, 500"}, "Ruherauschen"))
    check("(1017-1) min_hold 1000 erlaubt",
          not _raises({"pres_tri_hold": "1000, 10000, 500"}))
    check("(1017-1) add_hold < 0 abgelehnt",
          _raises({"pres_tri_hold": "4000, 10000, -1"}, "add_hold"))
    check("(1017-1) pres_idx 1 abgelehnt", _raises({"pres_idx": "1"}, "pres_idx"))
    check("(1017-1) acq_tick 0 abgelehnt", _raises({"acq_tick": "0"}))
    check("(1017-1) ned_tftr 0 (nur Schalter) erlaubt",
          not _raises({"ned_tftr": "0"}))
    # Klipper-Hülle: Config-Fehler -> config.error, kein Start
    class _Cfg(dict):
        class error(Exception):
            pass

        def get_printer(self):
            raise AssertionError("darf nicht erreicht werden")

        def get_options(self):
            return list(self.keys())

    class _P:
        def get_reactor(self):
            return None

        def lookup_object(self, name):
            return None

    class _C(_Cfg):
        def get_printer(self):
            return _P()

    try:
        K.PrinterK2Prtouch(_C({"pres_tri_hold": "4000, 4000, 500"}))
        ok = False
    except _Cfg.error as exc:
        ok = "Config-Fehler" in str(exc) and "Stream" in str(exc)
    check("(1017-1) Hülle: Fehler als config.error, kein Start", ok)


def test_1017_arm_settle():
    """Punkt 2: arm_settle_s an die Totzeit koppeln."""
    cfg = K.parse_options({})
    check("(1017-2) Abtastperiode aus cfg_regs 60 = 0,78125 ms",
          abs(K.pres_sample_period_s(cfg) - 0.00078125) < 1e-12)
    check("(1017-2) Totzeit Stock 128 Samples = 100 ms",
          abs(K.pres_dead_time_s(cfg) - 0.1) < 1e-9)
    check("(1017-2) Minimum Stock = 1,5 × 128 × 0,78125 ms = 0,15 s",
          abs(K.min_arm_settle_s(cfg) - 0.15) < 1e-9
          and abs(cfg["arm_settle_min_s"] - 0.15) < 1e-9)
    check("(1017-2) Default 0,2 s gültig", cfg["arm_settle_s"] == 0.2)
    check("(1017-2) arm_settle_s 0.149 abgelehnt",
          _raises({"arm_settle_s": "0.149"}, "Totzeit"))
    check("(1017-2) arm_settle_s 0 abgelehnt", _raises({"arm_settle_s": "0"}))
    check("(1017-2) arm_settle_s 0.15 erlaubt", not _raises({"arm_settle_s": "0.15"}))
    c64 = K.parse_options({"lmt_dead": "64"})
    check("(1017-2) lmt_dead 64: Minimum über Filter-Einschwingen 128 (0,15 s)",
          abs(c64["arm_settle_min_s"] - 0.15) < 1e-9
          and abs(c64["pres_dead_time_s"] - 0.05) < 1e-9)
    check("(1017-2) lmt_dead 256 braucht 0,3 s (0,2 abgelehnt)",
          _raises({"lmt_dead": "256"}, "arm_settle_s")
          and not _raises({"lmt_dead": "256", "arm_settle_s": "0.3"}))
    check("(1017-2) cfg_regs 44 (640 Hz) braucht 0,3 s (0,2 abgelehnt)",
          _raises({"pres_cfg_regs": "44"}, "arm_settle_s"))
    check("(1017-2) lmt_dead 2000 > Maximum 2 s -> abgelehnt",
          _raises({"lmt_dead": "2000", "arm_settle_s": "2.0"}, "Maximum"))
    check("(1017-2) acq_tick ändert die Periode NICHT (Bitbang)",
          abs(K.parse_options({"pres_acq_tkms": "2.0"})["arm_settle_min_s"]
              - 0.15) < 1e-9)
    # Laufzeit: Kern pausiert genau arm_settle_s, fail-safe bei zu kurz
    core, line, desc, _ = make_core({"arm_settle_s": "0.25"})
    res, err = _run(core)
    check("(1017-2) Kern pausiert arm_settle_s nach dem Scharfschalten",
          err is None and core._test_pauses == [0.25], str(core._test_pauses))
    core2, line2, desc2, _ = make_core()
    core2.cfg["arm_settle_s"] = 0.05  # an parse_options vorbei
    res2, err2 = _run(core2)
    check("(1017-2) Kern: zu kurze Wartezeit -> Abbruch vor Fahrt, entschärft",
          err2 is not None and "Totzeit" in str(err2)
          and "DESCEND" not in line2.log and "stop_pres1" in line2.log,
          str(err2))


def test_1017_min_speed():
    """Punkt 3: Mindestgeschwindigkeit (ANNAHME 2,5 mm/s bei Stock)."""
    cfg = K.parse_options({})
    check("(1017-3) Mindestgeschwindigkeit Stock 2,5 mm/s",
          abs(K.min_probe_speed(cfg) - 2.5) < 1e-12 and cfg["speed_min"] == 2.5)
    check("(1017-3) speed 2.4 abgelehnt", _raises({"speed": "2.4"}, "zu langsam"))
    check("(1017-3) speed 2.5 erlaubt", not _raises({"speed": "2.5"}))
    check("(1017-3) probe_speed 2 (speed fällt zurück) abgelehnt",
          _raises({"probe_speed": "2"}, "zu langsam"))
    # Seit 17.10.2026 (mode steps): Düse ist auch im Rückfallweg
    # scharf -> dieselbe Mindestgeschwindigkeit (ANNAHME-Grenze) für probe_speed.
    check("(1017-3) mode steps: probe_speed 2 abgelehnt (Düse scharf, Rampe)",
          _raises({"mode": "steps", "probe_speed": "2"}, "zu langsam"))
    check("(1017-3) mode steps: probe_speed 2.5 erlaubt",
          not _raises({"mode": "steps", "probe_speed": "2.5"}))
    c = K.parse_options({"pres_tri_hold": "8000, 10000, 1000"})
    check("(1017-3) Schwellen 8000/1000 -> Minimum 5,0 mm/s",
          abs(c["speed_min"] - 5.0) < 1e-9
          and _raises({"pres_tri_hold": "8000, 10000, 1000", "speed": "4.9"},
                      "zu langsam"))
    check("(1017-3) Schwellen 9000/1000 -> Minimum > 5 -> unzulässig",
          _raises({"pres_tri_hold": "9000, 10000, 1000"}, "zu langsam"))
    check("(1017-3) cfg_regs 56 (PGA 64) -> Minimum 5,0 mm/s",
          abs(K.parse_options({"pres_cfg_regs": "56"})["speed_min"] - 5.0) < 1e-9)
    check("(1017-3) cfg_regs 44 (640 Hz) -> nie unter 2,5 mm/s",
          K.parse_options({"pres_cfg_regs": "44",
                           "arm_settle_s": "0.3"})["speed_min"] == 2.5)
    core, line, desc, _ = make_core()
    res, err = _run(core, FakeGcmd({"PROBE_SPEED": "2"}))
    check("(1017-3) PROBE_SPEED=2 -> Fehler 'zu langsam', kein MCU-Befehl",
          err is not None and "zu langsam" in str(err)
          and "second_homing_speed" in str(err) and line.log == []
          and not desc.calls, str(err))
    core2, line2, desc2, _ = make_core()
    res2, err2 = _run(core2, FakeGcmd({"PROBE_SPEED": "2.5"}))
    check("(1017-3) PROBE_SPEED=2.5 erlaubt", err2 is None
          and desc2.calls[0]["probe_speed"] == 2.5, str(err2))
    check("(1017-3) check_probe_speed: 0 / 5.1 / 2.49 abgelehnt, 3 ok",
          K.check_probe_speed(0, cfg) and K.check_probe_speed(5.1, cfg)
          and K.check_probe_speed(2.49, cfg)
          and K.check_probe_speed(3.0, cfg) is None)


def test_1017_rest_extended():
    """Punkt 4: Ruhetest um Tickabstand, -1/24 Bit, gefilterten Kanal."""
    cfg = K.parse_options({})
    per = 93750  # 0,78125 ms in Takten
    tk = [5000000 + i * per for i in range(64)]
    ok_d = [-1428000 + det_noise(i, 300) for i in range(64)]
    ok_f = [det_noise(i, 300) for i in range(64)]
    ok, info = K.check_rest_ok(ok_d, cfg, ticks=tk, filt=ok_f)
    check("(1017-4) gesunder Sensor: ok", ok and not info["errors"], str(info))
    stuck = [-1] * 64
    ok_old = K.rest_info(stuck)["dif"] <= cfg["rest_max_dif"]
    ok, info = K.check_rest_ok(stuck, cfg)
    check("(1017-4) Sensor ab (-1, dif 0): alter dif-Test bestünde, neuer nicht",
          ok_old and not ok and any("-1" in e for e in info["errors"])
          and any("konstant" in e for e in info["errors"]), str(info["errors"]))
    ok, info = K.check_rest_ok([-1428000] * 64, cfg)
    check("(1017-4) konstante Rohwerte (dif 0) abgelehnt",
          not ok and any("konstant" in e for e in info["errors"]))
    big = list(ok_d)
    big[5] = 1 << 23
    ok, info = K.check_rest_ok(big, cfg)
    check("(1017-4) Wert außerhalb 24 Bit abgelehnt",
          not ok and any("24 Bit" in e for e in info["errors"]))
    ok, info = K.check_rest_ok(ok_d[:10], cfg)
    check("(1017-4) zu wenige Samples (10) abgelehnt",
          not ok and any("gültige Rohwerte" in e for e in info["errors"]))
    ok, info = K.check_rest_ok([], cfg)
    check("(1017-4) leeres Register abgelehnt (kein Fehlerwurf)", not ok)
    for name, step, good in (("5 ms (Sensor hängt)", 600000, False),
                             ("55 µs (DOUT low)", 6600, False),
                             ("1,5625 ms (falsche Rate)", 187500, False),
                             ("0,7907 ms (live)", 94884, True),
                             ("0,85 ms (+8,8 %)", 102000, True),
                             ("0,87 ms (+11 %)", 104400, False)):
        t2 = [5000000 + i * step for i in range(64)]
        ok, info = K.check_rest_ok(ok_d, cfg, ticks=t2)
        check("(1017-4) Tickabstand %s -> %s" % (name, "ok" if good else "Abbruch"),
              ok is good, str(info["errors"]))
    # Jitter: ein Sample 60 µs zu spät gelesen (langer + kurzer Abstand) -> ok
    jit = list(tk)
    jit[20] += 7200
    ok, info = K.check_rest_ok(ok_d, cfg, ticks=jit)
    check("(1017-4) Jitter ±60 µs an einem Sample -> ok", ok, str(info["errors"]))
    # Einzelner viel zu kurzer Abstand (0,3 × Periode) -> Abbruch über Minimum
    short = list(tk)
    short[20] = short[19] + 28000
    short[21:] = [t + 28000 - per for t in short[21:]]
    ok, info = K.check_rest_ok(ok_d, cfg, ticks=sorted(short))
    check("(1017-4) Einzelabstand 0,23 ms (< 0,5 × Periode) -> Abbruch",
          not ok and any("Minimum" in e for e in info["errors"]),
          str(info["errors"]))
    # Laufendes Register (Lücken zwischen Blöcken): Median trotzdem ≈ Periode
    gap = [5000000 + (i + 11 * (i // 8)) * per for i in range(64)]
    ok, info = K.check_rest_ok(ok_d, cfg, ticks=gap)
    check("(1017-4) Blocklücken (laufendes Register) -> ok über Median", ok,
          str(info["errors"]))
    noisy_f = [2100 if i == 30 else v for i, v in enumerate(ok_f)]
    ok, info = K.check_rest_ok(ok_d, cfg, ticks=tk, filt=noisy_f)
    check("(1017-4) gefiltert max 2100 > 0,5 × min_hold 4000 -> Abbruch",
          not ok and any("gefilterter Kanal" in e for e in info["errors"])
          and info["filt_max_abs"] == 2100 and info["filt_limit"] == 2000.0)
    neg_f = [-2100 if i == 30 else v for i, v in enumerate(ok_f)]
    ok, info = K.check_rest_ok(ok_d, cfg, filt=neg_f)
    check("(1017-4) gefiltert -2100 (Betrag) -> Abbruch", not ok)
    cfg1 = K.parse_options({"rest_filt_max_frac": "1.0"})
    ok, info = K.check_rest_ok(ok_d, cfg1, filt=noisy_f)
    check("(1017-4) rest_filt_max_frac 1.0 -> 2100 < 4000 ok", ok)
    check("(1017-4) rest_filt_max_frac 0 / 1.5 abgelehnt",
          _raises({"rest_filt_max_frac": "0"}) and
          _raises({"rest_filt_max_frac": "1.5"}))
    ok, info = K.check_rest_ok(ok_d, cfg, filt=ok_f[:5])
    check("(1017-4) zu wenige gefilterte Werte abgelehnt", not ok)
    # Kern (mode probe): Abbruch vor Fahrt, entschärft
    for flag, needle in (("sensor_stuck", "-1"), ("filt_noisy", "gefilterter"),
                         ("rest_noisy", "dif=")):
        line = ProbeLine()
        setattr(line, flag, True)
        core, line, desc, _ = make_core(line=line)
        res, err = _run(core)
        check("(1017-4) Kern %s -> Ruhetest-Abbruch vor Fahrt" % flag,
              err is not None and "Ruhetest" in str(err) and needle in str(err)
              and "DESCEND" not in line.log and "stop_pres1" in line.log
              and not core.armed, str(err))
    core, line, desc, _ = make_core()
    res, err = _run(core)
    ri = core.last["arm"]["rest"]
    check("(1017-4) Kern liest roh + gefiltert, Info mit Tick/Filter",
          err is None and "read_ftr" in line.log
          and abs(ri["tick_median_ms"] - 0.78125) < 1e-6
          and ri["filt_max_abs"] <= 300, str(ri))
    # Rückfallweg: Tickprüfung greift auch dort
    cfg_s, tool, pres, step, ctl, log = make_ctl(z_start=0.2, z_true=0.0)
    pres._ticks = lambda: struct.pack("<64I", *[10000000 + i * 600000
                                               for i in range(64)])
    r = ctl.probe_point()
    check("(1017-4) Rückfallweg: Tickabstand 5 ms -> rest_error, keine Fahrt",
          r.get("status") == "rest_error" and "Tickabstand" in r.get("error", "")
          and not tool.moves, str(r)[:200])


def _varied_register():
    """64 Samples, deren Datendeltas Blöcke mit n = 9, 7, 8 … erzwingen."""
    ticks = [146176342 + i * 94900 for i in range(64)]
    vals, v = [], -1428000
    for i in range(64):
        if i == 0:
            d = 0
        elif i <= 8:
            d = 50 if i % 2 else -50      # 1-Byte-Deltas -> Block n = 9
        elif i <= 15:
            d = 300 if i % 2 else -300    # 2-Byte-Deltas -> Block n = 7
        elif i <= 23:
            d = 150 if i % 2 else -110    # gemischt 1/2 Byte -> n = 8
        else:
            d = 60 if i % 2 else -60      # 1 Byte -> 4 × 9, Rest 4 (vollständig)
        v += d
        vals.append(v)
    return ticks, vals


def test_1017_ring_blocks():
    """Punkt 5: Blocklesen idx += n (n aus dem Paket), len-Feld ignoriert."""
    cfg = K.parse_options({})
    ticks, vals = _varied_register()
    filt = [det_noise(i, 200) for i in range(64)]
    reg = _McuRegister(ticks, vals, filt=filt)
    pres = K.KlippyPresMcu(None, None, cfg, 3, {})
    pres._q_read = reg
    t, d = pres.read_pres()
    check("(1017-5) Blockgrößen 9/7/8 kommen vor (ns=%s)" % reg.ns,
          reg.ns[:3] == [9, 7, 8] and set(reg.ns[:-1]) <= {7, 8, 9}
          and reg.ns[-1] == 4)
    check("(1017-5) alle 64 Samples genau einmal, lückenlos",
          t == ticks and d == vals)
    idxs = [p[3] for p in reg.sent]
    check("(1017-5) nächster idx = idx + n, angefragt len = 64 - idx",
          idxs == [sum(reg.ns[:i]) for i in range(len(reg.ns))]
          and all(p[4] == 64 - p[3] for p in reg.sent), str(idxs))
    check("(1017-5) len-Feld der Antwort beim vollständigen Restblock n+1 "
          "(%d bei %d Samples) – nicht benutzt" % (reg.lens[-1], reg.ns[-1]),
          reg.lens[-1] == reg.ns[-1] + 1 and len(d) == 64)
    # Alte Methode (feste 8er-Schritte, len 8) hätte Samples verloren:
    reg_old = _McuRegister(ticks, vals)
    got = set()
    for idx in range(0, 64, 8):
        r = reg_old.send([3, 1, 0, idx, 8])
        got.update(K.decode_ticks(r["ticks"], cfg))
    check("(1017-5) Gegenprobe: feste 8er-Schritte verlieren %d Sample(s)"
          % (64 - len(got)), len(got) < 64)
    # Gefilterter Kanal (is_src=0) über denselben Weg:
    n_sent = len(reg.sent)
    tf, df = pres.read_pres_filtered()
    check("(1017-5) is_src=0 liefert den gefilterten Kanal vollständig",
          df == filt and tf == ticks
          and all(p[1] == 0 for p in reg.sent[n_sent:]))
    # Register noch nicht voll: leere Einträge werden verworfen
    reg2 = _McuRegister([0] * 20 + ticks[20:], [0] * 20 + vals[20:])
    pres._q_read = reg2
    t2, d2 = pres.read_pres()
    check("(1017-5) Tick-0/Wert-0-Einträge verworfen (44 von 64)",
          len(d2) == 44 and t2 == ticks[20:])
    # n = 0 vor Ende -> Abbruch, keine Endlosschleife
    class _Zero:
        def __init__(self):
            self.sent = []

        def send(self, p):
            self.sent.append(list(p))
            return {"ticks": b"", "datas": b"", "len": 1}
    z = _Zero()
    ts, ds, ix = K.read_ring_blocks(z.send, lambda i, ln: [0, 1, 0, i, ln],
                                    cfg, K.decode_pres_datas)
    check("(1017-5) n = 0 -> Abbruch nach 1 Abfrage", ts == [] and len(z.sent) == 1)
    # ticks/datas ungleich lang -> Fehler (fail-safe)
    blk8 = _RingQuery._pack(list(range(100, 108)))
    blk7 = _RingQuery._pack(list(range(100, 107)))
    try:
        K.read_ring_blocks(lambda p: {"ticks": blk8, "datas": blk7},
                           lambda i, ln: [0, 1, 0, i, ln], cfg,
                           K.decode_pres_datas)
        ok = False
    except ValueError:
        ok = True
    check("(1017-5) ticks/datas mit verschiedenem n -> ValueError", ok)
    # Laufendes Register (3 neue Samples je Abfrage): Ruhetest trotzdem ok
    calm = [-1428000 + det_noise(i, 300) for i in range(64)]
    reg3 = _McuRegister(ticks, calm, advance=3)
    pres._q_read = reg3
    t3, d3 = pres.read_pres()
    ok, info = K.check_rest_ok(d3, cfg, ticks=t3)
    check("(1017-5) laufendes Register: sortiert, Ruhetest ok (Median %.3f ms)"
          % (info.get("tick_median_ms") or 0),
          all(b > a for a, b in zip(t3, t3[1:])) and ok, str(info["errors"]))
    # Eingefrorener Ring (Diagnose): Status, dann is_src 0 und 1 mit idx += n
    reg4 = _McuRegister(ticks, vals, filt=filt)
    pres._q_read = reg4
    fr = pres.read_frozen()
    s0 = reg4.sent
    nf = len(reg4.ns) // 2
    check("(1017-5) read_frozen: Status [oid,2,0,0,0], dann FTR/SRC idx += n",
          s0[0] == [3, 2, 0, 0, 0] and fr["ftr"][1] == filt
          and fr["src"][1] == vals
          and [p[3] for p in s0[1:1 + nf]]
          == [sum(reg4.ns[:i]) for i in range(nf)], str(s0[:4]))
    # step-Seite (Main): gleiches Verfahren
    regs = _McuRegister(ticks, vals, step=True)
    stp = K.KlippyStepMcu(None, None, cfg, 9)
    stp._q_read = regs
    ts5, ds5 = stp.read_step()
    check("(1017-5) read_step: idx += n, alle 64",
          ts5 == ticks and ds5 == vals
          and [p[1] for p in regs.sent]
          == [sum(regs.ns[:i]) for i in range(len(regs.ns))])
    regs2 = _McuRegister(ticks, vals, step=True)
    stp._q_read = regs2
    st = stp.read_frozen()
    check("(1017-5) step read_frozen: [oid,0,0] zuerst, dann idx += n",
          regs2.sent[0] == [9, 0, 0] and st["step"][1] == vals)


def _creality_pc7_pull(cmds):
    """Endzustand des PC7-Pulls in der CREALITY-Firmware aus der Folge der
    MCU-Config-Befehle (Firmware-Analyse): wer zuletzt konfiguriert, gewinnt;
    config_endstop pull_up 0 = Pull-Down, 1 = Pull-Up, sonst schwebend;
    config_prtouch_step = immer Pull-Up."""
    pull = None
    for c in cmds:
        if c.startswith("config_endstop") and "pin=PC7" in c:
            v = int(c.split("pull_up=")[1].split()[0])
            pull = K.CREALITY_PULL.get(v, "schwebend")
        elif c.startswith("config_prtouch_step") and "swp_pin=PC7" in c:
            pull = "Pull-Up"
    return pull


def test_1017_pc7_pull():
    """Punkt 6: PC7-Pull bewusst Pull-Up (Stock-Endzustand) via '^!PC7'."""
    p = K.parse_pin_desc("^!PC7")
    check("(1017-6) parse_pin_desc ^!PC7 -> pullup 1, invert 1, mcu",
          p == {"pullup": 1, "invert": 1, "chip": "mcu", "pin": "PC7"})
    check("(1017-6) parse_pin_desc ~!PC7 -> pullup -1",
          K.parse_pin_desc("~!PC7")["pullup"] == -1)
    try:
        K.parse_pin_desc("!^PC7")
        ok = False
    except ValueError:
        ok = True
    check("(1017-6) '!^PC7' (falsche Reihenfolge, Upstream ungültig) abgelehnt", ok)
    cfg = K.parse_options({})
    check("(1017-6) Default step_swap_pin '^!PC7' = Pull-Up",
          cfg["step_swap_pin"] == "^!PC7" and cfg["step_swap_pull"] == "Pull-Up")
    check("(1017-6) '!PC7' (Creality: Pull-DOWN) im Probe-Modus abgelehnt",
          _raises({"step_swap_pin": "!PC7"}, "Pull-DOWN"))
    check("(1017-6) '~!PC7' (Creality: schwebend) abgelehnt",
          _raises({"step_swap_pin": "~!PC7"}, "schwebend"))
    check("(1017-6) '^PC7' ohne Invertierung abgelehnt",
          _raises({"step_swap_pin": "^PC7"}, "Invertierung"))
    check("(1017-6) '^!nozzle_mcu:PC7' (falsche MCU) abgelehnt",
          _raises({"step_swap_pin": "^!nozzle_mcu:PC7"}, "main_mcu"))
    check("(1017-6) mode steps: '!PC7' erlaubt (kein Endstop)",
          not _raises({"mode": "steps", "step_swap_pin": "!PC7"}))
    stock = ["config_endstop oid=1 pin=PC7 pull_up=0",
             "config_trsync oid=2",
             "config_prtouch_step oid=3 oid_xstp=4 oid_ystp=7 oid_zstp=9 "
             "swp_pin=PC7"]
    alt = [stock[2], "config_endstop oid=0 pin=PC7 pull_up=0"]
    neu = [stock[2], "config_endstop oid=0 pin=PC7 pull_up=1"]
    check("(1017-6) Creality-Modell: Stock-Folge -> Pull-Up",
          _creality_pc7_pull(stock) == "Pull-Up")
    check("(1017-6) Creality-Modell: alte k2-Folge mit !PC7 -> Pull-Down",
          _creality_pc7_pull(alt) == "Pull-Down")
    check("(1017-6) Creality-Modell: neue k2-Folge mit ^!PC7 -> Pull-Up "
          "(reihenfolgeunabhängig)",
          _creality_pc7_pull(neu) == "Pull-Up"
          and _creality_pc7_pull(neu[::-1]) == "Pull-Up")


def test_1017_upstream():
    """Integration gegen die echten Upstream-Dateien (sonst SKIP)."""
    if not os.path.isdir(os.path.join(UPSTREAM_KLIPPY, "extras")):
        print("SKIP (1017-upstream) lokale Upstream-Kopie fehlt")
        return
    # Config-Fehler beim Laden -> kein Start
    try:
        _integration_env({"pres_tri_hold": "4000, 4000, 500"})
        ok = False
    except ValueError as exc:
        ok = "Config-Fehler" in str(exc) and "Stream" in str(exc)
    check("(1017-upstream) min_hold == max_hold -> Config-Fehler beim Laden", ok)
    try:
        _integration_env({"step_swap_pin": "!PC7"})
        ok = False
    except ValueError as exc:
        ok = "Pull-DOWN" in str(exc)
    check("(1017-upstream) step_swap_pin '!PC7' -> Config-Fehler beim Laden", ok)
    # G28 Z: second_homing_speed 2 (< 2,5) -> 2. Antastung abgelehnt
    import collections
    env = _integration_env()
    hv = env["pins"].setup_pin("endstop", "probe:z_virtual_endstop")
    HI = collections.namedtuple(
        "homing_info", ["speed", "position_endstop", "retract_speed",
                        "retract_dist", "positive_dir", "second_homing_speed"])
    hinfo = HI(5.0, 0.0, 5.0, 5.0, False, 2.0)

    class Rail:
        def get_endstops(self):
            return [(hv, "probe")]

        def get_homing_info(self):
            return hinfo

    env["th"].pos = [175.0, 175.0, 120.0, 0.0]
    try:
        env["uhoming"].Homing(env["printer"]).home_rails(
            [Rail()], [None, None, 180.0, None], [None, None, 0.0, None])
        ok = False
    except env["ugcode"].CommandError as exc:
        ok = "zu langsam" in str(exc) and "second_homing_speed" in str(exc)
    core = env["obj"].probe.core
    check("(1017-upstream) G28 Z: second_homing_speed 2 -> 2. Fahrt abgelehnt",
          ok and len(env["rec"]["probing"]) == 1 and not core.armed,
          str(env["rec"]["probing"]))
    # KTWO_PRTOUCH_READ im Probe-Modus: Vorabprüfung ohne Bewegung
    env2 = _integration_env({"allow_move": "False"})
    env2["line"].log.clear()
    gc2 = env2["gcode"]
    gc2.cmds["KTWO_PRTOUCH_READ"](gc2.create_gcode_command(
        "KTWO_PRTOUCH_READ", "KTWO_PRTOUCH_READ", {}))
    core2 = env2["obj"].probe.core
    check("(1017-upstream) KTWO_PRTOUCH_READ: scharf -> Ruhetest -> entschärft, "
          "keine Fahrt (auch allow_move False)",
          any("READ ok" in m for m in gc2.out) and env2["rec"]["probing"] == []
          and "read_ftr" in env2["line"].log
          and env2["line"].log[0] == "wait"
          and env2["line"].log[-2:] == ["stop_step", "stop_pres1"]
          and not core2.armed and core2.arm_count == 1, str(gc2.out[-1:]))
    env3 = _integration_env()
    env3["line"].sensor_stuck = True
    gc3 = env3["gcode"]
    gc3.cmds["KTWO_PRTOUCH_READ"](gc3.create_gcode_command(
        "KTWO_PRTOUCH_READ", "KTWO_PRTOUCH_READ", {}))
    check("(1017-upstream) KTWO_PRTOUCH_READ bei abgezogenem Sensor -> Meldung, "
          "entschärft",
          any("Ruhetest" in m and "-1" in m for m in gc3.out)
          and not env3["obj"].probe.core.armed, str(gc3.out[-1:]))
    # PROBE bei abgezogenem Sensor: keine Fahrt
    env4 = _integration_env()
    env4["line"].sensor_stuck = True
    try:
        env4["gcode"].cmds["PROBE"](env4["gcode"].create_gcode_command(
            "PROBE", "PROBE", {}))
        ok = False
    except env4["ugcode"].CommandError as exc:
        ok = "Ruhetest" in str(exc)
    check("(1017-upstream) PROBE bei abgezogenem Sensor -> keine Fahrt",
          ok and env4["rec"]["probing"] == [])


# =====================================================================
# 17.10.2026: Rückfallweg mode steps – Reihenfolge wie der Probe-Weg
# („Ruhetest liest vor start_prtouch_pres“, README A.8/B.1)
# =====================================================================

STEPS_ARM = ["wait", "swap0", "read_swap", "swap1", "read_swap", "cont",
             "start_pres", "start_step", "pause", "read_pres", "read_ftr",
             "status"]


def _idx(ev, name, last=False):
    if name not in ev:
        return -1
    return len(ev) - 1 - ev[::-1].index(name) if last else ev.index(name)


def test_1017_steps_order():
    cfg, tool, pres, step, ctl, log = make_ctl(z_start=0.2, z_true=0.0)
    res = ctl.probe_point()
    ev = ctl._test_events
    check("(steps) Antastung ok", res.get("status") == "ok", str(res)[:200])
    check("(steps) Reihenfolge Stillstand → Signalweg → cont → start_pres → "
          "start_step → Totzeit → Ruhetest roh+gefiltert → tri_chxs",
          ev[:len(STEPS_ARM)] == STEPS_ARM, str(ev[:14]))
    check("(steps) erster Schritt erst NACH Ruhetest/Kontaktprüfung",
          _idx(ev, "down") > _idx(ev, "status") > _idx(ev, "read_ftr")
          > _idx(ev, "start_pres"), str(ev[:16]))
    check("(steps) Ende: stop_step → stop_pres sta_swap=1 → Lift",
          ev[-4:] == ["stop_step", "stop_pres1", "lift", "wait"],
          str(ev[-6:]))
    check("(steps) genau 1× scharf, 1× entschärft, Totzeit = arm_settle_s",
          ev.count("start_pres") == 1 and ev.count("stop_pres1") == 1
          and ctl._test_pauses == [cfg["arm_settle_s"]]
          and not ctl.core.armed, str(ctl._test_pauses))
    check("(steps) je Schritt M400 + Lesen + tri_chxs",
          ev.count("down") == ev.count("read_step") == res["steps"]
          and ev.count("status") == res["steps"] + 1)
    check("(steps) Düse hat mit ausgelöst (mcu_triggered im Ergebnis)",
          res.get("mcu_triggered") is True)
    check("(steps) Nullpunkt = Median des Ruhetests nach dem Scharfschalten",
          abs(res["rest"]["median"] - (-1450000)) <= 300, str(res["rest"]))
    # preflight (KTWO_PRTOUCH_READ / DRY_RUN) – gleicher Arm-Code, entschärft
    cfg2, tool2, pres2, _, ctl2, _ = make_ctl(z_start=0.2, z_true=0.0)
    info = ctl2.preflight()
    ev2 = ctl2._test_events
    check("(steps) preflight: scharf → Ruhe → entschärft, keine Bewegung",
          ev2 == STEPS_ARM + ["stop_step", "stop_pres1"]
          and not tool2.moves and info["rest"]["len"] == 64, str(ev2))


def test_1017_steps_register_empty():
    """Altfehler: Ruhetest VOR start_prtouch_pres liest ein leeres Register."""
    cfg, tool, pres, step, ctl, log = make_ctl(z_start=0.2, z_true=0.0)
    t, d = pres.read_pres()  # unscharf, wie der alte check_sensor_rest
    tk, dd = K.drop_empty(K.decode_ticks(t, cfg), K.decode_pres_datas(d, cfg))
    ok, info = K.check_rest_ok(dd, cfg, ticks=tk)
    check("(steps-leer) unscharf gelesen: Register leer -> Ruhetest scheitert "
          "(deshalb war mode steps unbenutzbar)",
          not ok and len(dd) == 0, str(info["errors"]))
    check("(steps-leer) alter Weg check_sensor_rest entfernt",
          not hasattr(ctl, "check_sensor_rest"))
    del ctl._test_events[:]
    r = ctl.probe_point()
    check("(steps-leer) neuer Weg: ok, Ruhetest erst nach start_pres",
          r.get("status") == "ok" and _idx(ctl._test_events, "read_pres")
          > _idx(ctl._test_events, "start_pres"), str(r)[:160])
    # Start wirkungslos (ack 0, Register bleibt leer) -> Abbruch vor Fahrt
    cfg2, tool2, pres2, _, ctl2, _ = make_ctl(z_start=0.2, z_true=0.0)
    pres2.never_fills = True
    r2 = ctl2.probe_point()
    ev2 = ctl2._test_events
    check("(steps-leer) Register nach start leer -> rest_error, keine Fahrt",
          r2.get("status") == "rest_error"
          and "gültige Rohwerte" in r2.get("error", "")
          and not tool2.moves, str(r2)[:200])
    check("(steps-leer) trotzdem entschärft",
          ev2[-2:] == ["stop_step", "stop_pres1"] and not ctl2.core.armed,
          str(ev2[-4:]))


def test_1017_steps_errors():
    # (1) Signalweg defekt -> swap_error, nie scharf, entschärft
    cfg, tool, pres, step, ctl, _ = make_ctl(fake_over={"swap_ok": False})
    r = ctl.probe_point()
    ev = ctl._test_events
    check("(steps-err) Signalweg defekt -> swap_error, kein start, keine Fahrt",
          r.get("status") == "swap_error" and "start_pres" not in ev
          and not tool.moves and ev[-2:] == ["stop_step", "stop_pres1"],
          str(r)[:160])
    # (2) ack err != 0 -> start_error
    cfg, tool, pres, step, ctl, _ = make_ctl()
    pres.ack_err = 3
    r = ctl.probe_point()
    ev = ctl._test_events
    check("(steps-err) ack err=3 -> start_error, kein Ruhetest, keine Fahrt, "
          "entschärft", r.get("status") == "start_error"
          and "err=3" in r.get("error", "") and "read_pres" not in ev
          and not tool.moves and ev[-2:] == ["stop_step", "stop_pres1"],
          str(r)[:160])
    # (3) Düse meldet schon beim Scharfschalten Kontakt (tri_chxs)
    cfg, tool, pres, step, ctl, _ = make_ctl()
    pres.trigger_on_arm = True
    r = ctl.probe_point()
    check("(steps-err) tri_chxs vor der Fahrt -> contact_before_move, "
          "keine Fahrt, entschärft",
          r.get("status") == "contact_before_move" and not tool.moves
          and ctl._test_events[-2:] == ["stop_step", "stop_pres1"],
          str(r)[:160])
    # (4) Ausnahme während der Fahrt -> entschärft VOR dem Anheben
    cfg, tool, pres, step, ctl, _ = make_ctl(z_start=0.2, z_true=0.0)
    tool.fail_down_at = 3
    r = ctl.probe_point()
    ev = ctl._test_events
    check("(steps-err) Ausnahme im 3. Schritt -> error, entschärft, dann Lift",
          r.get("status") == "error" and ev.count("down") == 2
          and _idx(ev, "lift") > _idx(ev, "stop_pres1")
          > _idx(ev, "stop_step") > _idx(ev, "down_fail"), str(ev[-6:]))
    # (5) Notgrenze -> entschärft vor Lift
    cfg, tool, pres, step, ctl, _ = make_ctl(
        fake_over={"emergency_always": True})
    r = ctl.probe_point()
    ev = ctl._test_events
    check("(steps-err) Notgrenze -> emergency, 1 Schritt, entschärft, Lift",
          r.get("status") == "emergency" and r.get("steps") == 1
          and ev[-4:] == ["stop_step", "stop_pres1", "lift", "wait"],
          str(r)[:160])
    # (6) max_travel -> entschärft vor Lift, Weg begrenzt
    cfg, tool, pres, step, ctl, _ = make_ctl(z_start=0.5, z_true=0.0,
                                             cfg_over={"max_travel_mm": 0.1})
    r = ctl.probe_point()
    ev = ctl._test_events
    check("(steps-err) max_travel -> entschärft, Lift, Weg <= 0,1 mm",
          r.get("status") == "max_travel" and tool.total_down() <= 0.1 + 1e-9
          and ev[-4:] == ["stop_step", "stop_pres1", "lift", "wait"],
          str(r)[:160])
    # (7) Entschärfen schlägt fehl (step) -> disarm_error, pres trotzdem
    cfg, tool, pres, step, ctl, _ = make_ctl(z_start=0.2, z_true=0.0)
    step.fail_disarm = True
    r = ctl.probe_point()
    ev = ctl._test_events
    check("(steps-err) stop_step-Fehler -> disarm_error, stop_pres gesendet, "
          "angehoben", r.get("status") == "disarm_error"
          and "Entschärfen" in r.get("error", "") and "stop_pres1" in ev
          and ev[-2:] == ["lift", "wait"], str(r)[:200])
    # (8) BaseException (z.B. KeyboardInterrupt) -> entschärft + weiter
    cfg, tool, pres, step, ctl, _ = make_ctl(z_start=0.2, z_true=0.0)

    def _boom(step_mm, speed):
        raise KeyboardInterrupt()
    tool.move_down = _boom
    try:
        ctl.probe_point()
        ok = False
    except KeyboardInterrupt:
        ok = True
    check("(steps-err) KeyboardInterrupt -> weitergereicht, entschärft",
          ok and ctl._test_events[-2:] == ["stop_step", "stop_pres1"]
          and not ctl.core.armed, str(ctl._test_events[-4:]))
    # (9) Düse löst aus, Host-Kriterium (3000) nicht -> mcu_contact, kein Z
    cfg, tool, pres, step, ctl, _ = make_ctl(z_start=0.2, z_true=0.0,
                                             fake_over={"spike": -2000})
    r = ctl.probe_point()
    ev = ctl._test_events
    check("(steps-err) tri_chxs ohne Host-Kontakt -> mcu_contact, kein z, "
          "entschärft + Lift", r.get("status") == "mcu_contact"
          and "z" not in r and ev[-4:] == ["stop_step", "stop_pres1",
                                            "lift", "wait"], str(r)[:200])
    # (10) Lift-Fehler nach Kontakt -> lift_error
    cfg, tool, pres, step, ctl, _ = make_ctl(z_start=0.2, z_true=0.0)
    tool.fail_lift = True
    r = ctl.probe_point()
    check("(steps-err) Lift-Fehler -> lift_error, entschärft",
          r.get("status") == "lift_error" and not ctl.core.armed
          and "stop_pres1" in ctl._test_events, str(r)[:160])


def test_1017_steps_guards():
    # allow_move False -> nur Vorabprüfung (scharf -> Ruhe -> entschärft)
    cfg, tool, pres, step, ctl, _ = make_ctl(cfg_over={"allow_move": False})
    r = ctl.probe_point(100.0, 100.0)
    ev = ctl._test_events
    check("(steps-guard) allow_move False -> dry_run, 0 Bewegungen, "
          "MCU entschärft", r.get("status") == "dry_run" and not tool.moves
          and "down" not in ev and "xy" not in ev
          and ev[-2:] == ["stop_step", "stop_pres1"]
          and abs(r.get("baseline", 0) + 1450000) <= 300, str(r)[:160])
    # DRY_RUN mit kaputtem Sensor meldet den Fehler statt „ok“
    cfg, tool, pres, step, ctl, _ = make_ctl(fake_over={"rest_noisy": True})
    r = ctl.probe_point(dry_run=True)
    check("(steps-guard) DRY_RUN mit unruhigem Sensor -> rest_error",
          r.get("status") == "rest_error" and not tool.moves, str(r)[:160])
    # Mindestgeschwindigkeit doppelt (Laufzeit, falls cfg verändert)
    cfg, tool, pres, step, ctl, _ = make_ctl()
    ctl.cfg["probe_speed"] = 2.0
    r = ctl.probe_point(100.0, 100.0)
    check("(steps-guard) probe_speed 2 zur Laufzeit -> config_error, "
          "kein MCU-Befehl, keine Bewegung",
          r.get("status") == "config_error" and "zu langsam" in r["error"]
          and not tool.moves and not ctl._test_events, str(r)[:160])
    ctl.cfg["probe_speed"] = 5.5
    r = ctl.probe_point()
    check("(steps-guard) probe_speed 5,5 -> config_error",
          r.get("status") == "config_error" and not tool.moves)
    # Config: arm_settle_s/Antast-Parameter gelten auch in mode steps
    check("(steps-guard) mode steps: arm_settle_s 0.1 / min_hold==max_hold "
          "abgelehnt", _raises({"mode": "steps", "arm_settle_s": "0.1"},
                               "zu kurz")
          and _raises({"mode": "steps", "pres_tri_hold": "4000,4000,500"},
                      "Stream"))
    # can_brk False -> zweiter Versuch schaltet NACH dem Entschärfen neu scharf
    calls = []
    orig = K.check_can_brk

    def _fake_can(b, s_, z, c):
        calls.append(1)
        return (len(calls) > 1), "Test"
    K.check_can_brk = _fake_can
    try:
        cfg, tool, pres, step, ctl, _ = make_ctl(z_start=0.2, z_true=0.0)
        # seit 18.10.2026 (A8): Wiederholung nur im Mesh-Kontext
        r = ctl.probe_point(100.0, 100.0, context="mesh")
    finally:
        K.check_can_brk = orig
    ev = ctl._test_events
    starts = [i for i, e in enumerate(ev) if e == "start_pres"]
    stops = [i for i, e in enumerate(ev) if e == "stop_pres1"]
    check("(steps-guard) can_brk-Wiederholung: 2× scharf/entschärft, "
          "zweites start_pres nach erstem stop_pres",
          r.get("retried") and r.get("status") == "ok" and len(starts) == 2
          and len(stops) == 2 and stops[0] < starts[1], str(ev.count("down")))
    # Adapter: Status-Abfrage wie Stock read_prtouch_pres [oid, 2, 0, 0, 0]
    kp = K.KlippyPresMcu(None, None, K.parse_options({}), 4, {})
    kp._q_read = _Q("read", lambda d: {"tri_chxs": 1, "buf_len": 64,
                                       "idx": 0, "len": 0})
    st = kp.read_status()
    check("(steps-guard) KlippyPresMcu.read_status sendet [oid,2,0,0,0]",
          kp._q_read.sent == [[4, 2, 0, 0, 0]] and st["tri_chxs"] == 1,
          str(kp._q_read.sent))


# =====================================================================
# 17.10.2026 (3): Treppen-Kriterium contact_criterion 'cumulative'
# (README-k2_prtouch B.2 Punkt 5 / B.3, ANNAHME)
# =====================================================================

class FakePresTreppe(FakePres):
    """Düse mit Treppen-Kraftverlauf: nach jedem Schritt + M400 steht die
    Kraft still (Fenster großteils NACH dem Schritt). Fenster = 'trans'
    Samples alter Stufe, dann neue Stufe, Rauschen ±noise. Kraft =
    k_per_mm × Eindrückung unter z_true, Vorzeichen 'sign' (live negativ),
    Drift je Lesen, optionale Einzelsprünge {read_nr: counts}. Die Düse
    löst bei dieser flachen Treppe NICHT selbst aus (mcu_trigger False)."""

    def __init__(self, tool, cfg, k_per_mm=125000.0, sign=-1, drift=0.0,
                 trans=12, jumps=None, **kw):
        FakePres.__init__(self, tool, cfg, **kw)
        self.k_per_mm = float(k_per_mm)
        self.sign = int(sign)
        self.drift = float(drift)
        self.trans = int(trans)
        self.jumps = dict(jumps or {})
        self.prev_level = None
        self.mcu_trigger = False

    def force_level(self):
        z = float(self.tool.get_z())
        f = self.k_per_mm * (self.z_true - z) if z < self.z_true else 0.0
        return (self.baseline + self.sign * f + self.drift * self.reads
                + self.jumps.get(self.reads, 0))

    def read_pres(self):
        self.events.append("read_pres")
        if not self._filled():
            return self._empty()
        self.reads += 1
        lv = self.force_level()
        prev = lv if self.prev_level is None else self.prev_level
        out = [int(round(prev if i < self.trans else lv))
               + det_noise(i + self.reads * 131, self.noise_amp)
               for i in range(64)]
        self.prev_level = lv
        return self._ticks(), struct.pack("<64i", *out)


def make_ctl_treppe(z_start=0.2, z_true=0.05, cfg_over=None, **pres_kw):
    base = {"allow_move": True, "step_mm": 0.02, "probe_speed": 5.0,
            "max_travel_mm": 5.0, "packing": "struct", "mode": "steps"}
    base.update(dict(cfg_over or {}))
    cfg = K.parse_options(base)
    events = []
    tool = FakeToolhead(z_start=z_start, events=events)
    pres = FakePresTreppe(tool, cfg, z_true=z_true, events=events, **pres_kw)
    step = FakeStep(tool, cfg, pres=pres, events=events)
    ctl = K.K2PrtouchController(cfg, pres, step, tool,
                                pause=lambda s: events.append("pause"),
                                logger=DummyLog())
    ctl._test_events = events
    return cfg, tool, pres, step, ctl


CUM = {"contact_criterion": "cumulative"}


def test_1017_treppe_default_unchanged():
    """Default 'window' = bisheriges Verhalten: die Treppe (2 500 Counts je
    0,02-mm-Schritt, B.2 Punkt 5) wird NICHT erkannt -> Notgrenze."""
    cfg = K.parse_options({})
    check("(treppe) Default contact_criterion = window, steps 2, min 250",
          cfg["contact_criterion"] == "window" and cfg["contact_steps"] == 2
          and cfg["contact_step_min"] == 250.0)
    cfg, tool, pres, step, ctl = make_ctl_treppe()
    r = ctl.probe_point(100.0, 100.0)
    ev = ctl._test_events
    check("(treppe) window: Treppe 2500/Schritt -> emergency (Treppen-"
          "Effekt nachgestellt), entschärft, angehoben",
          r.get("status") == "emergency" and ev.count("stop_pres1") == 1
          and "lift" in ev, str(r)[:200])
    # Standard-Fake (Spitze im Fenster): window und cumulative identisch
    out = {}
    for name, over in (("window", {}), ("cumulative", CUM)):
        _c, _t, _p, _s, ctl2, _l = make_ctl(z_start=0.2, z_true=0.0,
                                            cfg_over=over)
        rr = ctl2.probe_point(100.0, 100.0)
        out[name] = rr
    check("(treppe) Sprung im Fenster: cumulative = window (gleiches z, "
          "Kriterium 'window' greift zuerst)",
          out["window"]["status"] == out["cumulative"]["status"] == "ok"
          and out["window"]["z"] == out["cumulative"]["z"]
          and out["cumulative"]["criterion"] == "window"
          and out["window"]["steps"] == out["cumulative"]["steps"],
          "%s / %s" % (out["window"].get("z"), out["cumulative"].get("z")))


def test_1017_treppe_cumulative():
    for z_true in (0.05, 0.047, 0.0333):
        cfg, tool, pres, step, ctl = make_ctl_treppe(z_true=z_true,
                                                     cfg_over=CUM)
        r = ctl.probe_point(100.0, 100.0)
        ev = ctl._test_events
        over = z_true - tool.min_z
        check("(treppe) cumulative z_true=%.4f: ok, |Fehler| < 0,003 mm, "
              "Überfahrweg <= 3 Schritte, entschärft vor Lift" % z_true,
              r.get("status") == "ok" and r.get("criterion") == "cumulative"
              and abs(r["z"] - z_true) < 0.003 and over <= 3 * 0.02 + 1e-9
              and ev.index("stop_pres1") < ev.index("lift")
              and r["zoft"] >= 0 and r["can_brk"] is True,
              "z=%s over=%.3f %s" % (r.get("z"), over, str(r)[:160]))
    cfg, tool, pres, step, ctl = make_ctl_treppe(sign=+1, cfg_over=CUM)
    r = ctl.probe_point(100.0, 100.0)
    check("(treppe) Vorzeichen egal (Kraft positiv)",
          r.get("status") == "ok" and abs(r["z"] - 0.05) < 0.003, str(r)[:160])
    # kleinere Stufen (1000/Schritt): längere Folge, trotzdem vor Notgrenze
    cfg, tool, pres, step, ctl = make_ctl_treppe(k_per_mm=50000.0,
                                                 cfg_over=CUM)
    r = ctl.probe_point(100.0, 100.0)
    check("(treppe) 1000/Schritt: Kontakt nach 4 Stufen (Summe > 3000), "
          "z ±0,003", r.get("status") == "ok" and r["cum_run_len"] == 4
          and abs(r["z"] - 0.05) < 0.003, str(r)[:200])
    # contact_steps 3: eine Stufe später
    cfg, tool, pres, step, ctl = make_ctl_treppe(
        cfg_over=dict(CUM, contact_steps=3))
    r = ctl.probe_point(100.0, 100.0)
    check("(treppe) contact_steps 3 -> Folge >= 3",
          r.get("status") == "ok" and r["cum_run_len"] == 3, str(r)[:160])
    # Notgrenze bleibt vorrangig: 10 000/Schritt -> 2. Stufe 20 000 > 15 000
    # (trans=0: Fenster komplett nach dem Schritt -> window sieht nichts).
    # Seit 18.10.2026 Default 25 000 -> alte Grenze 15 000 hier explizit.
    cfg, tool, pres, step, ctl = make_ctl_treppe(
        k_per_mm=500000.0, z_true=0.06, trans=0,
        cfg_over=dict(CUM, emergency_thresh=15000))
    r = ctl.probe_point(100.0, 100.0)
    check("(treppe) steile Treppe: Notgrenze 15 000 greift vor cumulative",
          r.get("status") == "emergency", str(r)[:160])
    # Default 27 500 (A7/E1): dieselbe Treppe (2. Stufe 20 000 = Live-
    # Bereich) wird jetzt gemessen statt verworfen.
    cfg, tool, pres, step, ctl = make_ctl_treppe(
        k_per_mm=500000.0, z_true=0.06, trans=0, cfg_over=CUM)
    r = ctl.probe_point(100.0, 100.0)
    check("(treppe) steile Treppe mit Default-Notgrenze 27 500: ok "
          "(cumulative, 2 Stufen)",
          cfg["emergency_thresh"] == 27500 and r.get("status") == "ok"
          and r.get("criterion") == "cumulative", str(r)[:160])
    # Düse löst selbst aus, Host nicht -> mcu_contact wie bisher
    cfg, tool, pres, step, ctl = make_ctl_treppe(k_per_mm=10000.0,
                                                 cfg_over=CUM)
    pres.mcu_trigger = True
    r = ctl.probe_point(100.0, 100.0)
    check("(treppe) tri_chxs ohne Host-Kontakt -> mcu_contact (unverändert)",
          r.get("status") == "mcu_contact", str(r)[:160])


def test_1017_treppe_robust():
    # langsame Drift (100 Counts je Lesen ≈ live 2 400 in 24 s), kein Kontakt
    cfg, tool, pres, step, ctl = make_ctl_treppe(
        z_true=-5.0, drift=-100.0, cfg_over=dict(CUM, max_travel_mm=1.0))
    r = ctl.probe_point(100.0, 100.0)
    check("(treppe) Drift 100/Schritt über 50 Schritte: kein Fehlkontakt "
          "(max_travel)", r.get("status") == "max_travel", str(r)[:160])
    # einzelner Sprung +5000 und zurück (Störung) -> kein Kontakt
    cfg, tool, pres, step, ctl = make_ctl_treppe(
        z_true=-5.0, jumps={6: -2800}, cfg_over=dict(CUM, max_travel_mm=0.4))
    r = ctl.probe_point(100.0, 100.0)
    check("(treppe) Einzelstufe 2800 (eine Stufe, zurück) -> kein Kontakt",
          r.get("status") == "max_travel", str(r)[:160])
    # Rauschen der Stufen ±400 (wechselnd) -> keine gleichsinnige Folge
    jumps = {i: (400 if i % 2 else -400) for i in range(2, 40)}
    cfg, tool, pres, step, ctl = make_ctl_treppe(
        z_true=-5.0, jumps=jumps, cfg_over=dict(CUM, max_travel_mm=0.6))
    r = ctl.probe_point(100.0, 100.0)
    check("(treppe) Stufenrauschen ±400 -> kein Kontakt",
          r.get("status") == "max_travel", str(r)[:160])
    # Fehler im Schritt -> trotzdem entschärft (gemeinsamer finally)
    cfg, tool, pres, step, ctl = make_ctl_treppe(cfg_over=CUM)
    tool.fail_down_at = 3
    r = ctl.probe_point(100.0, 100.0)
    ev = ctl._test_events
    check("(treppe) Ausnahme im Schritt (cumulative) -> error, entschärft",
          r.get("status") == "error" and ev.count("stop_pres1") == 1,
          str(r)[:160])


def test_1017_treppe_pure_config():
    cfg = K.parse_options(CUM)
    lv = [0.0, 10.0, -5.0, 1250.0, 3750.0]
    dp = [0.0, 0.02, 0.04, 0.06, 0.08]
    d = K.detect_contact_cumulative(lv, dp, cfg)
    check("(treppe) rein: Nullstelle 0,05 mm, Folge 2, Bezug = Stufe davor",
          d["contact"] and d["run_start"] == 3 and d["run_len"] == 2
          and abs(d["contact_depth"] - 0.04996) < 1e-6 and d["baseline"] == -5.0,
          str(d))
    d2 = K.detect_contact_cumulative([0.0, 3500.0, 7000.0], [0, 0.02, 0.04],
                                     cfg)
    check("(treppe) rein: Nullstelle auf [Stufe s-1, s] begrenzt",
          d2["contact"] and 0.0 <= d2["contact_depth"] <= 0.02, str(d2))
    d3 = K.detect_contact_cumulative([0.0, 1000.0, 2000.0], [0, 0.02, 0.04],
                                     cfg)
    check("(treppe) rein: Summe <= contact_thresh -> kein Kontakt",
          not d3["contact"], str(d3))
    d4 = K.detect_contact_cumulative([0.0, 5000.0], [0, 0.02], cfg)
    check("(treppe) rein: weniger als contact_steps Stufen -> kein Kontakt",
          not d4["contact"])
    check("(treppe) step_level = Median der jüngsten 16",
          K.step_level([0] * 48 + [7] * 15 + [10 ** 6]) == 7.0)
    check("(treppe) Config: ungültiges Kriterium / contact_steps 1, 11 / "
          "contact_step_min >= contact_thresh abgelehnt",
          _raises({"contact_criterion": "fest"}, "contact_criterion")
          and _raises({"contact_steps": "1"}, "contact_steps")
          and _raises({"contact_steps": "11"}, "contact_steps")
          and _raises({"contact_step_min": "3000"}, "contact_step_min")
          and _raises({"contact_step_min": "0"}, "contact_step_min"))

    class _Cfg:
        def __init__(self, d):
            self.d = d

        def get_options(self):
            return list(self.d)

        def get(self, k, default=None):
            return self.d.get(k, default)
    c2 = K.parse_klipper_config(_Cfg({"contact_criterion": "cumulative",
                                      "contact_steps": "4",
                                      "contact_step_min": "300"}))
    check("(treppe) parse_klipper_config reicht die Optionen durch",
          c2["contact_criterion"] == "cumulative" and c2["contact_steps"] == 4
          and c2["contact_step_min"] == 300.0)
    cfg, tool, pres, step, ctl = make_ctl_treppe(cfg_over=CUM)
    check("(treppe) describe_plan nennt das Kriterium",
          "cumulative" in ctl.describe_plan())


# =====================================================================
# Sensor-Selbsttest KTWO_PRTOUCH_CHECK (pres_check, apax-Analyse 17.10.2026)
# =====================================================================

def _pack_vals(vals):
    """Gegenstück zu K.decode_packed (Packer der Düse):
    [n][Maske BE][Wert0 absolut][Deltas], je Feld 1-4 Byte LE signed."""
    n = len(vals)
    fields, mask = [], 0
    prev = None
    for i, v in enumerate(vals):
        x = int(v) if prev is None else int(v) - prev
        prev = int(v)
        x = ((x + (1 << 31)) & 0xFFFFFFFF) - (1 << 31)  # u32-Überlauf
        for sz in (1, 2, 3, 4):
            if -(1 << (8 * sz - 1)) <= x < (1 << (8 * sz - 1)):
                break
        mask |= (sz - 1) << (2 * i)
        fields.append(x.to_bytes(sz, "little", signed=True))
    mb = (n + 3) // 4
    return bytes([n]) + mask.to_bytes(mb, "big") + b"".join(fields)


def _apax_packets(n, value_fn, start_tick=10000000, period=93750,
                  espd_fn=None, per=6, ch=0, oid=0, start_i=0):
    """n Samples als resault_prtouch_apax-Pakete (je 'per' Samples)."""
    out = []
    i = 0
    while i < n:
        k = min(per, n - i)
        idx = range(start_i + i, start_i + i + k)
        ticks = [(start_tick + j * period) & 0xFFFFFFFF for j in idx]
        out.append({"oid": oid, "ch": ch, "len": k,
                    "ticks": _pack_vals(ticks),
                    "datas": _pack_vals([value_fn(j) for j in idx]),
                    "espds": _pack_vals([(espd_fn(j) if espd_fn else 0)
                                         for j in idx])})
        i += k
    return out


def _val_good(j):
    # Ruhe ±300 + Kraftwelle 40 000 Counts (wie Wischen/Extrudieren)
    return -1428000 + det_noise(j, 300) + (40000 if (j // 200) % 2 else 0)


def _val_quiet(j):
    return -1428000 + det_noise(j, 300)


class FakeApax:
    def __init__(self, ev, rate=1280.0, value_fn=_val_good, period=93750,
                 espd_fn=None):
        self.ev = ev
        self.sink = None
        self.ack = None
        self.stop_err = 0
        self.stop_raise = False
        self.rate, self.value_fn, self.period = rate, value_fn, period
        self.espd_fn = espd_fn
        self.sent = 0

    def deliver(self, seconds):
        n = int(round(seconds * self.rate))
        for p in _apax_packets(n, self.value_fn, period=self.period,
                               espd_fn=self.espd_fn, start_i=self.sent):
            self.sink(p)
        self.sent += n

    def start(self):
        self.ev.append("start")
        return self.ack or {"err": 0, "expar0": 0, "expar1": 93750}

    def stop(self):
        self.ev.append("stop")
        if self.stop_raise:
            raise RuntimeError("Timeout on wait for 'ack_prtouch'")
        return {"err": self.stop_err, "expar0": 0, "expar1": 0}

    def stop_nowait(self):
        self.ev.append("stop_nowait")


def make_check(cfg_over=None, armed=False, **apax_kw):
    base = {"pres_check": True}
    base.update(cfg_over or {})
    cfg = K.parse_options(base)
    ev = []
    now = [0.0]
    apax = FakeApax(ev, **apax_kw)

    def pause(s):
        ev.append("pause")
        apax.deliver(s)
        now[0] += float(s)

    chk = K.K2PrtouchApaxCheck(cfg, apax, pause, clock=lambda: now[0],
                               is_armed=lambda: armed)
    apax.sink = chk.on_packet
    return chk, apax, ev, now


def test_1017_check_config():
    c = K.parse_options({})
    check("(check) pres_check Default False", c["pres_check"] is False)
    c = K.parse_options({"pres_check": "True"})
    check("(check) Defaults hold 15000/max 15 s/Dauer 3 s/EXTRUDE max 5 mm",
          c["pres_check"] and c["pres_check_hold"] == 15000
          and c["pres_check_max_s"] == 15 and c["pres_check_duration_s"] == 3
          and c["pres_check_extrude_max_mm"] == 5
          and c["pres_check_extruder"] == "extruder")
    check("(check) Stock-Startparameter (oid, 60, 93750)",
          K.build_start_apax_tuple(c, 4) == (4, 60, 93750))
    bl = K.apax_bus_load(10)
    check("(check) Buslast 12,4 kB/s ≈ 54 % der Düsenleitung",
          bl["bytes"] == 124000 and 0.53 < bl["line_share"] < 0.55)
    for opts, needle in (({"pres_check_hold": "0"}, "pres_check_hold"),
                         ({"pres_check_max_s": "61"}, "pres_check_max_s"),
                         ({"pres_check_duration_s": "20"},
                          "pres_check_duration_s"),
                         ({"pres_check_extrude_max_mm": "21"}, "extrude_max"),
                         ({"pres_check_extrude_speed": "11"}, "extrude_speed"),
                         ({"pres_check_rate_tol": "0.6"}, "rate_tol"),
                         ({"pres_check_bad_frac": "0.6"}, "bad_frac"),
                         ({"pres_check_min_samples": "1"}, "min_samples"),
                         ({"pres_check": "vielleicht"}, "pres_check")):
        check("(check) Config abgelehnt: %s" % (opts,), _raises(opts, needle))

    class Cfg(dict):
        def get_options(self):
            return list(self.keys())
    c2 = K.parse_klipper_config(Cfg({"pres_check": "True",
                                     "pres_check_hold": "20000"}))
    check("(check) parse_klipper_config übernimmt pres_check*",
          c2["pres_check"] and c2["pres_check_hold"] == 20000)


def test_1017_check_off_unchanged():
    if not os.path.isdir(os.path.join(UPSTREAM_KLIPPY, "extras")):
        print("SKIP (check) lokale Upstream-Kopie fehlt")
        return
    for ext in (None, {"mcu": "nozzle_mcu", "oid": 7}):
        env = _integration_env(extruder=ext)
        obj, rec, gc = env["obj"], env["rec"], env["gcode"]
        check("(check aus) kein config_prtouch_apax, kein Befehl, keine "
              "Registrierung (Extruder %s)" % ("da" if ext else "fehlt"),
              not any("apax" in c for _m, c in rec["config"])
              and "KTWO_PRTOUCH_CHECK" not in gc.cmds
              and "apax_sink" not in rec and obj.apax_check is None
              and obj.probe.core.apax_busy is None
              and obj.controller.core.apax_busy is None
              and "pres_check" not in obj.get_status())


def test_1017_check_oid_estp():
    if not os.path.isdir(os.path.join(UPSTREAM_KLIPPY, "extras")):
        print("SKIP (check) lokale Upstream-Kopie fehlt")
        return
    env = _integration_env({"pres_check": "True"},
                           extruder={"mcu": "nozzle_mcu", "oid": 7})
    obj, rec, gc = env["obj"], env["rec"], env["gcode"]
    cfgs = [c for m, c in rec["config"] if m == "nozzle_mcu"]
    i_pres = [i for i, c in enumerate(cfgs)
              if c.startswith("config_prtouch_pres")]
    i_apax = [i for i, c in enumerate(cfgs)
              if c.startswith("config_prtouch_apax")]
    want = "config_prtouch_apax oid=%d oid_estp=7" % obj.pres_oid
    check("(check an) config_prtouch_apax mit pres-oid + Extruder-oid 7",
          len(i_apax) == 1 and cfgs[i_apax[0]] == want, str(cfgs))
    check("(check an) Reihenfolge: config_prtouch_apax NACH "
          "config_prtouch_pres (gleiche MCU)",
          len(i_pres) == 1 and i_apax and i_apax[0] > i_pres[0]
          and not any("apax" in c for m, c in rec["config"] if m == "mcu"))
    sink = rec.get("apax_sink")
    check("(check an) resault_prtouch_apax mit pres-oid auf nozzle_mcu "
          "registriert",
          sink is not None and sink[1] == K.RESP_APAX
          and sink[2] == obj.pres_oid and sink[3] == "nozzle_mcu")
    check("(check an) Befehl registriert, Gegensperre in beiden Kernen",
          "KTWO_PRTOUCH_CHECK" in gc.cmds and obj.apax_check is not None
          and obj.probe.core.apax_busy == obj.apax_check.busy
          and obj.controller.core.apax_busy == obj.apax_check.busy
          and obj.get_status()["pres_check"]["active"] is False)
    for ext, needle, name in (
            ({"mcu": "mcu", "oid": 7}, "liegt auf MCU", "falsche MCU"),
            (None, "nicht gefunden", "kein Extruder"),
            ({"stepper": False}, "keinen Stepper", "ohne Stepper"),
            ({"mcu": "nozzle_mcu", "oid": 300}, "unplausible",
             "oid > 255")):
        try:
            _integration_env({"pres_check": "True"}, extruder=ext)
            ok, msg = False, "kein Fehler"
        except RuntimeError as exc:  # Fake config_error = RuntimeError
            msg = str(exc)
            ok = needle in msg and "NICHT gesendet" in msg
        check("(check an) oid_estp-Fehler -> Config-Fehler (%s)" % name,
              ok, msg[:120])


def test_1017_check_pure_eval():
    # gut: Kraftwelle 40 000 > 15 000
    chk, apax, ev, _ = make_check()
    ok, res = chk.run(duration=2.0)
    ch0 = res["channels"][0]
    check("(check) gut: ok, dif >= 15000, Rate 1280 Hz, len ≈ 2 s × 1280",
          ok and ch0["dif"] >= 15000 and abs(ch0["rate_hz"] - 1280) < 1
          and ch0["len"] == 2560 - K.PRES_CHECK_SKIP
          and ev == ["start", "pause", "stop"] and not chk.busy(),
          str(res["errors"]))
    lines = K.format_check_lines(res)
    check("(check) Stock-ähnliche Ausgabe PRES_CHECK_INFO_CH0 … dif=…",
          lines and lines[0].startswith("PRES_CHECK_INFO_CH0 min=")
          and "dif=%d" % ch0["dif"] in lines[0])
    # schlecht: nur Ruherauschen -> dif < hold
    chk, apax, ev, _ = make_check(value_fn=_val_quiet)
    ok, res = chk.run(duration=2.0)
    check("(check) ohne Last: dif < 15000 -> Fehlschlag, key769-Text",
          not ok and any("pres_check_hold" in e for e in res["errors"])
          and "PR_ERR_CODE_PRES_NOT_BE_SENSED" in
          K.format_check_failure(res) and ev[-1] == "stop")
    chk, apax, ev, _ = make_check(value_fn=_val_quiet)
    ok, res = chk.run(duration=2.0, require_dif=False)
    check("(check) REQUIRE_DIF=0: Strom/Rate/Rohwerte ok -> ok", ok,
          str(res["errors"]))
    # tot: Sensor abgezogen (-1), konstant, gesättigt, 0
    for fn, needle, name in (
            (lambda j: -1, "ungültig", "-1 (abgezogen)"),
            (lambda j: -1428000, "konstant", "konstant"),
            (lambda j: K.ADC24_MAX, "ungültig", "24-Bit-Sättigung"),
            (lambda j: 0, "ungültig", "0")):
        chk, apax, ev, _ = make_check(value_fn=fn)
        ok, res = chk.run(duration=1.0, require_dif=False)
        check("(check) tot: %s -> Fehlschlag" % name,
              not ok and any(needle in e for e in res["errors"])
              and ev[-1] == "stop", str(res["errors"])[:160])
    # einzelne Ausreißer -1 unter 1 % erlaubt, gehen NICHT in min/max ein
    chk, apax, ev, _ = make_check(
        value_fn=lambda j: -1 if j == 500 else _val_good(j))
    ok, res = chk.run(duration=2.0)
    check("(check) 1 Ausreißer -1: ok, nicht in min/max",
          ok and res["channels"][0]["bad"]["stuck"] == 1
          and res["channels"][0]["max"] < 0)
    # Ausreißer 0 darf kein großes dif vortäuschen
    chk, apax, ev, _ = make_check(
        value_fn=lambda j: 0 if j == 500 else _val_quiet(j))
    ok, res = chk.run(duration=2.0)
    check("(check) Ausreißer 0 täuscht kein dif vor -> Fehlschlag",
          not ok and res["channels"][0]["dif"] < 15000)
    # keine Daten / falsche Rate / zu wenige Samples
    chk, apax, ev, _ = make_check(rate=0.0)
    ok, res = chk.run(duration=1.0)
    check("(check) keine Pakete -> Fehlschlag 'keine Daten'",
          not ok and any("keine Daten" in e for e in res["errors"]))
    chk, apax, ev, _ = make_check(rate=320.0, period=93750 * 4)
    ok, res = chk.run(duration=2.0)
    check("(check) Rate 320 Hz statt 1280 -> Fehlschlag (Rate + Anzahl)",
          not ok and any("Abtastrate" in e for e in res["errors"])
          and any("passen nicht" in e for e in res["errors"]))
    chk, apax, ev, _ = make_check()
    ok, res = chk.run(duration=0.1)
    check("(check) zu kurz (128 Samples < 256) -> Fehlschlag",
          not ok and any("Samples (<" in e for e in res["errors"]))
    # Anzahl passt nicht zur Dauer (MCU liefert nur halb so viel)
    chk, apax, ev, now = make_check()
    apax.rate = 640.0
    ok, res = chk.run(duration=2.0)
    check("(check) halbe Sample-Anzahl bei richtiger Tickrate -> Fehlschlag",
          not ok and any("passen nicht" in e for e in res["errors"]))
    # Wächter: mehr als max_s × Rate × 1,5 Samples -> stop_nowait
    chk, apax, ev, _ = make_check({"pres_check_max_s": "1",
                                   "pres_check_duration_s": "1"})
    ok, res = chk.run(load=lambda: apax.deliver(3.0))
    check("(check) Wächter: Strom zu lang -> stop_nowait + Fehlschlag",
          not ok and "stop_nowait" in ev and ev[-1] == "stop"
          and any("Wächter" in e for e in res["errors"]), str(ev))
    # espd-Hinweis bei Extrusion ohne Extruderbewegung
    chk, apax, ev, _ = make_check()
    ok, res = chk.run(load=lambda: apax.deliver(2.0), expect_moving=True)
    check("(check) EXTRUDE ohne espd != 0 -> nur Warnung",
          ok and res["warnings"] and "espd" in res["warnings"][0])
    chk, apax, ev, _ = make_check(espd_fn=lambda j: -4000)
    ok, res = chk.run(load=lambda: apax.deliver(2.0), expect_moving=True)
    check("(check) espd != 0 gezählt, keine Warnung",
          ok and not res["warnings"]
          and res["channels"][0]["espd_moving"] == 2560)


def test_1017_check_interlock_finally():
    # Gegensperre 1: Kern scharf -> CHECK startet nichts
    chk, apax, ev, _ = make_check(armed=True)
    try:
        chk.run(duration=1.0)
        ok = False
    except K.K2PrtouchError as exc:
        ok = "scharf" in str(exc)
    check("(check) Düse scharf -> CHECK abgelehnt, nichts gesendet",
          ok and ev == [])
    # Gegensperre 2: apax läuft -> arm() schaltet nicht scharf
    core, line, desc, waits = make_core()
    busy = [True]
    core.apax_busy = lambda: busy[0]
    res, err = _run(core)
    check("(check) apax aktiv -> arm() abgelehnt, keine Swap/Start/Fahrt",
          err is not None and "Selbsttest" in str(err)
          and not any(x in line.log for x in ("swap0", "start_pres",
                                              "DESCEND"))
          and core.arm_phase == "apax", str(line.log))
    busy[0] = False
    line.log.clear()
    res, err = _run(core)
    check("(check) apax gestoppt -> Antasten wieder normal",
          err is None and line.log == EXPECT_ORDER, str(err))
    # Gegensperre 3: während des CHECK ist busy() True
    chk, apax, ev, _ = make_check()
    seen = []
    chk.run(load=lambda: (seen.append(chk.busy()), apax.deliver(1.0)))
    check("(check) busy() True während des Stroms, danach False",
          seen == [True] and not chk.busy())
    # stop im finally: Last wirft
    chk, apax, ev, _ = make_check()

    def boom():
        apax.deliver(0.5)
        raise RuntimeError("Makro kaputt")
    try:
        chk.run(load=boom)
        ok = False
    except K.K2PrtouchError as exc:
        ok = "Makro kaputt" in str(exc)
    check("(check) Fehler in der Last -> command_error + stop im finally",
          ok and ev == ["start", "stop"] and not chk.busy()
          and chk.last["status"] == "error")
    chk, apax, ev, _ = make_check()
    try:
        chk.run(load=lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
        ok = False
    except KeyboardInterrupt:
        ok = True
    check("(check) KeyboardInterrupt -> unverändert weiter, stop im finally",
          ok and ev == ["start", "stop"] and not chk.busy())
    # ack-Fehler / falsches expar1 -> stop trotzdem
    for ack, name in (({"err": 3, "expar0": 0, "expar1": 93750}, "err=3"),
                      ({"err": 0, "expar0": 0, "expar1": 0}, "expar1=0")):
        chk, apax, ev, _ = make_check()
        apax.ack = ack
        try:
            chk.run(duration=1.0)
            ok = False
        except K.K2PrtouchError as exc:
            ok = "start_prtouch_apax ack" in str(exc)
        check("(check) start-ack %s -> Abbruch + stop" % name,
              ok and ev == ["start", "stop"] and not chk.busy())
    # stop scheitert -> Sperre bleibt, STOP-Wiederholung löst sie
    chk, apax, ev, _ = make_check()
    apax.stop_raise = True
    try:
        chk.run(duration=1.0)
        ok = False
    except K.K2PrtouchError as exc:
        ok = "Stopp fehlgeschlagen" in str(exc)
    check("(check) stop scheitert -> Fehler, busy bleibt True (arm gesperrt)",
          ok and chk.busy())
    try:
        chk.run(duration=1.0)
        ok = False
    except K.K2PrtouchError as exc:
        ok = "STOP=1" in str(exc)
    check("(check) zweiter CHECK bei unbestätigtem Stopp abgelehnt",
          ok and ev.count("start") == 1)
    apax.stop_raise = False
    check("(check) stop()-Wiederholung löst die Sperre",
          chk.stop() is None and not chk.busy())
    apax.stop_err = 5
    chk.active = True
    check("(check) stop ack err != 0 -> Sperre bleibt",
          chk.stop() is not None and chk.busy())
    # Pakete außerhalb des CHECK werden ignoriert
    chk, apax, ev, _ = make_check()
    apax.deliver(0.1)
    check("(check) Pakete ohne laufenden CHECK -> ignoriert (stray)",
          chk.stray > 0 and chk.stats is None)


def test_1017_check_upstream_cmd():
    if not os.path.isdir(os.path.join(UPSTREAM_KLIPPY, "extras")):
        print("SKIP (check) lokale Upstream-Kopie fehlt")
        return
    env = _integration_env({"pres_check": "True"},
                           extruder={"mcu": "nozzle_mcu", "oid": 7})
    obj, rec, gc, ug = env["obj"], env["rec"], env["gcode"], env["ugcode"]
    oid = obj.pres_oid

    def run(params, value_fn=_val_good, n=2560):
        rec["apax"] = []
        rec["scripts"] = []
        rec["on_apax_start"] = (
            (lambda sink: [sink(p) for p in _apax_packets(n, value_fn,
                                                          oid=oid)])
            if n else None)
        gc.out[:] = []
        line = "KTWO_PRTOUCH_CHECK " + " ".join(
            "%s=%s" % kv for kv in params.items())
        gcmd = gc.create_gcode_command("KTWO_PRTOUCH_CHECK", line,
                                       dict(params))
        try:
            gc.cmds["KTWO_PRTOUCH_CHECK"](gcmd)
            return None
        except ug.CommandError as exc:
            return str(exc)

    err = run({"DURATION": "2"})
    check("(check upstream) gut: SUCCESS, start [oid,60,93750] + stop [oid]",
          err is None and any("SUCCESS" in o for o in gc.out)
          and rec["apax"] == [("start", [oid, 60, 93750]),
                              ("stop", [oid])], str(err))
    check("(check upstream) Buslast-Hinweis + PRES_CHECK_INFO_CH0",
          any("kB/s" in o for o in gc.out)
          and any(o.startswith("PRES_CHECK_INFO_CH0") for o in gc.out))
    err = run({"DURATION": "2"}, value_fn=_val_quiet)
    check("(check upstream) schlecht: command_error key769-Text, stop gesendet",
          err is not None and "PR_ERR_CODE_PRES_NOT_BE_SENSED" in err
          and rec["apax"][-1] == ("stop", [oid]))
    err = run({"DURATION": "1"}, value_fn=lambda j: -1)
    check("(check upstream) tot (-1): Fehler, kein Shutdown (CommandError)",
          err is not None and "ungültig" in err)
    err = run({"DURATION": "99"})
    check("(check upstream) DURATION > pres_check_max_s -> Fehler ohne start",
          err is not None and rec["apax"] == [])
    err = run({"EXTRUDE": "2"})
    check("(check upstream) EXTRUDE=2: SAVE/M83/G1 E2 F240/RESTORE im Strom",
          err is None and rec["scripts"]
          and "M83\nG1 E2.000 F240.0" in rec["scripts"][0]
          and rec["scripts"][0].startswith("SAVE_GCODE_STATE")
          and rec["scripts"][0].endswith(
              "RESTORE_GCODE_STATE NAME=_k2_prtouch_check")
          and [a[0] for a in rec["apax"]] == ["start", "stop"], str(err))
    err = run({"EXTRUDE": "6"})
    check("(check upstream) EXTRUDE > max 5 mm -> abgelehnt, nichts gesendet",
          err is not None and rec["apax"] == [] and rec["scripts"] == [])
    err = run({"MACRO": "_PI_NOZZLE_CLEAN"})
    check("(check upstream) MACRO: Makro während des Stroms",
          err is None and rec["scripts"] == ["_PI_NOZZLE_CLEAN"]
          and [a[0] for a in rec["apax"]] == ["start", "stop"], str(err))
    err = run({"MACRO": "G28;X"})
    check("(check upstream) MACRO mit Sonderzeichen abgelehnt",
          err is not None and rec["apax"] == [])
    err = run({"EXTRUDE": "1", "MACRO": "FOO"})
    check("(check upstream) EXTRUDE+MACRO abgelehnt", err is not None
          and rec["apax"] == [])
    err = run({"STOP": "1"}, n=0)
    check("(check upstream) STOP=1 sendet nur stop_prtouch_apax",
          err is None and rec["apax"] == [("stop", [oid])])
    # kalt -> EXTRUDE abgelehnt
    env2 = _integration_env({"pres_check": "True"},
                            extruder={"mcu": "nozzle_mcu", "oid": 7,
                                      "temp": 25.0, "hot": False})
    gcmd = env2["gcode"].create_gcode_command(
        "KTWO_PRTOUCH_CHECK", "KTWO_PRTOUCH_CHECK EXTRUDE=2",
        {"EXTRUDE": "2"})
    try:
        env2["gcode"].cmds["KTWO_PRTOUCH_CHECK"](gcmd)
        ok = False
    except env2["ugcode"].CommandError as exc:
        ok = "zu kalt" in str(exc)
    check("(check upstream) EXTRUDE kalt -> abgelehnt, kein Strom",
          ok and "apax" not in env2["rec"])
    # allow_move False -> EXTRUDE/MACRO gesperrt, DURATION erlaubt
    env3 = _integration_env({"pres_check": "True", "allow_move": "False"},
                            extruder={"mcu": "nozzle_mcu", "oid": 7})
    gcmd = env3["gcode"].create_gcode_command(
        "KTWO_PRTOUCH_CHECK", "KTWO_PRTOUCH_CHECK MACRO=FOO",
        {"MACRO": "FOO"})
    try:
        env3["gcode"].cmds["KTWO_PRTOUCH_CHECK"](gcmd)
        ok = False
    except env3["ugcode"].CommandError as exc:
        ok = "allow_move" in str(exc)
    check("(check upstream) MACRO ohne allow_move -> abgelehnt",
          ok and "apax" not in env3["rec"])
    # Gegensperre in der Hülle: G28/PROBE während aktivem apax
    obj.apax_check.active = True
    try:
        obj.probe.core.arm()
        ok = False
    except ug.CommandError as exc:
        ok = "Selbsttest" in str(exc)
    obj.apax_check.active = False
    check("(check upstream) arm() bei aktivem apax -> CommandError", ok)
    obj.probe.core.armed = True
    err = run({"DURATION": "1"})
    obj.probe.core.armed = False
    check("(check upstream) CHECK bei scharfer Düse -> abgelehnt",
          err is not None and "scharf" in err and rec["apax"] == [])


# =====================================================================
# 18.10.2026: Live-Abgleich A7 (Notgrenze/step_mm) und
# A8 (can_brk nur im Mesh-Kontext)
# =====================================================================

def test_1018_a7_emergency_step():
    # Live-Werte seit Live-Abgleich E1: Spitze 21 815,
    # Steifigkeit bis 2 270 Counts/µm, Default-Notgrenze 27 500.
    cfg = K.parse_options({})
    check("(A7/E1) Defaults: emergency_thresh 27500 > Live-Max 21815, "
          "Steifigkeit 2270/µm, step_mm 0,01, Obergrenze 40000",
          cfg["emergency_thresh"] == 27500 and cfg["step_mm"] == 0.01
          and K.EMERGENCY_THRESH_DEFAULT > K.LIVE_PEAK_MAX == 21815
          and K.LIVE_STIFF_MAX_PER_UM == 2270.0
          and K.EMERGENCY_THRESH_MAX == 40000)
    check("(A7/E1) Default-Kombination ohne Plausibilitätswarnung "
          "(0,01 mm × 2270/µm = 22700 < 0,9 × 27500 = 24750)",
          K.emergency_config_warnings(cfg) == [],
          str(K.emergency_config_warnings(cfg)))
    w = K.emergency_config_warnings(K.parse_options({"step_mm": 0.02}))
    check("(A7/E1) step_mm 0,02 + 27500 -> Warnung Kontaktschritt (45400)",
          len(w) == 1 and "45400" in w[0], str(w))
    w = K.emergency_config_warnings(
        K.parse_options({"emergency_thresh": 15000}))
    check("(A7/E1) alte Grenze 15000 -> Warnung 'unter Live-Spitze 21815'",
          any("21815" in x and "<=" in x for x in w), str(w))
    # Bisheriger Default 25 000: kein harter Fall mehr, aber „knapp“
    # (Kontaktschritt 22 700 = 9 % darunter, Spitze 21 815 = 14,6 % darunter).
    w = K.emergency_config_warnings(
        K.parse_options({"emergency_thresh": 25000}))
    check("(E1) alter Default 25000 -> genau 1 Hinweis 'knapp' "
          "(Kontaktschritt 22700, 9 %)",
          len(w) == 1 and "knapp" in w[0] and "22700" in w[0], str(w))
    w = K.emergency_config_warnings(
        K.parse_options({"emergency_thresh": 23000}))
    check("(E1) 23000 -> 'knapp' für Kontaktschritt UND Live-Spitze",
          len(w) == 2 and all("knapp" in x for x in w)
          and "21815" in w[1], str(w))
    # Konsistenz/Grenzen
    check("(A7) emergency_thresh <= contact_thresh abgelehnt",
          _raises({"emergency_thresh": 3000}, "contact_thresh")
          and _raises({"emergency_thresh": 2000}, "contact_thresh"))
    check("(A7) emergency_thresh 40001 abgelehnt, 40000 erlaubt",
          _raises({"emergency_thresh": 40001}, "40000")
          and K.parse_options({"emergency_thresh": 40000})[
              "emergency_thresh"] == 40000)
    check("(A7) step_mm-Grenzen unverändert (0,05 ok, 0,06 abgelehnt)",
          K.parse_options({"step_mm": 0.05})["step_mm"] == 0.05
          and _raises({"step_mm": 0.06}, "step_mm"))
    # Live-Maximum als Kontaktspitze im Fenster: Default misst, alt verwirft
    out = {}
    for name, over in (("neu", {}), ("alt", {"emergency_thresh": 15000})):
        _c, tool, _p, _s, ctl, _l = make_ctl(
            z_start=0.2, z_true=0.0, cfg_over=over,
            fake_over={"spike": -21815})
        out[name] = ctl.probe_point(100.0, 100.0)
    check("(A7/E1) Live-Spitze 21815 im Kontaktfenster: Default ok, "
          "alte 15000 -> emergency",
          out["neu"].get("status") == "ok"
          and out["alt"].get("status") == "emergency",
          "%s / %s" % (out["neu"].get("status"), out["alt"].get("status")))
    # Kontaktschritt bei Live-Steifigkeit 2270/µm: 0,01 mm -> ~22700 ok
    # (mit 27500; alter Default 25000 auch noch ok, aber knapp);
    # 0,02 mm -> ~45400 -> emergency (fail-safe, ohne Messwert)
    _c, tool, _p, _s, ctl, _l = make_ctl(
        z_start=0.2, z_true=0.0, cfg_over={"step_mm": 0.01},
        fake_over={"spike": -22700})
    r1 = ctl.probe_point(100.0, 100.0)
    _c, tool2, _p, _s, ctl2, _l = make_ctl(
        z_start=0.2, z_true=0.0, cfg_over={"step_mm": 0.02},
        fake_over={"spike": -45400})
    r2 = ctl2.probe_point(100.0, 100.0)
    check("(A7/E1) Kontaktschritt 0,01 mm (22700) ok, Überfahrweg <= 0,01; "
          "0,02 mm (45400) -> emergency + angehoben",
          r1.get("status") == "ok" and (0.0 - tool.min_z) <= 0.01 + 1e-9
          and r2.get("status") == "emergency"
          and tool2.get_z() > tool2.min_z,
          "%s / %s" % (str(r1)[:80], str(r2)[:80]))
    # Ein Sprung über die neue Notgrenze (27 501) -> emergency
    _c, _t, _p, _s, ctl4, _l = make_ctl(
        z_start=0.2, z_true=0.0, fake_over={"spike": -27600})
    r4 = ctl4.probe_point(100.0, 100.0)
    check("(E1) Kontaktsprung 27600 > Default 27500 -> emergency",
          r4.get("status") == "emergency", str(r4)[:120])
    # Warnung landet im Ergebnis/Plan (fail-safe, kein Fehler)
    _c, _t, _p, _s, ctl3, _l = make_ctl(z_start=0.2, z_true=0.0,
                                        cfg_over={"emergency_thresh": 15000})
    r3 = ctl3.probe_point(100.0, 100.0)
    plan = ctl3.describe_plan()
    check("(A7) Warnung in warnings + describe_plan",
          any("21815" in w for w in r3.get("warnings", []))
          and "WARNUNG" in plan and "can_brk: Scope mesh" in plan, plan[-300:])


class _FakeFanPin:
    def __init__(self, log):
        self.log = log

    def set_speed(self, value):
        self.log.append("fan%.1f" % value)


class _FakeHeaterFan:
    """Attribute wie Upstream extras/heater_fan.py."""

    def __init__(self, log, running=True, fan_speed=1.0):
        self.fan_speed = fan_speed
        self.last_speed = fan_speed if running else 0.0
        self.fan = _FakeFanPin(log)


def test_1022_fan_damping():
    """Lüfterdämpfung beim Antasten (Stock DISTUURB_CTL SET_FANS=0.60,
    Messung 22.10.2026: Hotend-Lüfter 100 % stört den Ruhetest)."""
    cfg = K.parse_options({})
    check("(Lüfter) Defaults: an, 0,6 (Stock prth_min_fans), 1,5 s",
          cfg["fan_damping"] is True and cfg["fan_damping_speed"] == 0.6
          and cfg["fan_damping_settle_s"] == 1.5)
    check("(Lüfter) fan_damping_speed 1,5 / settle 11 s abgelehnt",
          _raises({"fan_damping_speed": 1.5}, "fan_damping_speed")
          and _raises({"fan_damping_settle_s": 11}, "fan_damping_settle_s"))
    # Reine Klasse: laufender Lüfter wird sofort gedrosselt, kalter nicht.
    log, pauses, infos = [], [], []
    hot = _FakeHeaterFan(log)
    cold = _FakeHeaterFan(log, running=False)
    fd = K.FanDamper(lambda: [("heater_fan hotend_fan", hot),
                              ("heater_fan kalt", cold)],
                     0.6, 1.5, pauses.append, info=infos.append)
    slowed = fd.reduce()
    check("(Lüfter) laufender Lüfter sofort 0,6 + 1,5 s warten, kalter nur "
          "fan_speed", len(slowed) == 1 and log == ["fan0.6"]
          and pauses == [1.5] and hot.fan_speed == 0.6
          and hot.last_speed == 0.6 and cold.fan_speed == 0.6
          and cold.last_speed == 0.0 and len(infos) == 1, str((log, pauses)))
    check("(Lüfter) reduce idempotent", fd.reduce() == [] and log == ["fan0.6"])
    fd.restore()
    check("(Lüfter) restore: fan_speed zurück, laufender sofort 1,0",
          hot.fan_speed == 1.0 and hot.last_speed == 1.0
          and cold.fan_speed == 1.0 and cold.last_speed == 0.0
          and log == ["fan0.6", "fan1.0"] and not fd.active, str(log))
    fd.restore()
    check("(Lüfter) restore idempotent", log == ["fan0.6", "fan1.0"])
    # Probe-Sitzung: drosseln VOR dem Scharfschalten, zurück NACH dem
    # Entschärfen – auch wenn die Fahrt mit Fehler endet.
    for exc, name in ((None, "ok"), (K.K2PrtouchError("No trigger"), "Fehler")):
        core, line, _d, _w = make_core(raise_exc=exc)
        fan = _FakeHeaterFan(line.log)
        core.fan_damper = K.FanDamper(lambda: [("hf", fan)], 0.6, 1.5,
                                      core._test_pauses.append)
        _run(core)
        lg = line.log
        check("(Lüfter) Sitzung %s: fan0.6 vor swap0, fan1.0 nach stop_pres1"
              % name, lg.count("fan0.6") == 1 and lg.count("fan1.0") == 1
              and lg.index("fan0.6") < lg.index("swap0")
              and lg.index("fan1.0") > lg.index("stop_pres1")
              and fan.fan_speed == 1.0, str(lg))
    # Vorabprüfung (KTWO_PRTOUCH_READ) unter denselben Bedingungen.
    core, line, _d, _w = make_core()
    fan = _FakeHeaterFan(line.log)
    core.fan_damper = K.FanDamper(lambda: [("hf", fan)], 0.6, 0.0,
                                  core._test_pauses.append)
    core.preflight()
    lg = line.log
    check("(Lüfter) preflight: gedrosselt während arm, danach zurück",
          lg[0] == "fan0.6" and lg.index("fan1.0") > lg.index("stop_pres1")
          and fan.fan_speed == 1.0, str(lg))
    # Fehler in der Lüftersteuerung: nur Meldung, Antastung läuft.
    core, line, _d, _w = make_core()

    def _boom():
        raise RuntimeError("kein heater_fan")
    core.fan_damper = K.FanDamper(_boom, 0.6, 1.5, core._test_pauses.append)
    res, err = _run(core)
    check("(Lüfter) Fehler beim Drosseln bricht die Antastung nicht ab",
          err is None and res == [0.0], str(err))
    # Rohwert-Spanne: Probe-Hauptweg mit eigener (weiterer) Grenze.
    check("(Ruhe roh) Default rest_max_dif_probe 16000, rest_max_dif 3000",
          cfg["rest_max_dif_probe"] == 16000.0 and cfg["rest_max_dif"] == 3000.0)
    check("(Ruhe roh) rest_max_dif_probe < rest_max_dif abgelehnt",
          _raises({"rest_max_dif_probe": 2000}, "rest_max_dif_probe"))
    core, line, _d, _w = make_core(cfg_over={"rest_max_dif": 1,
                                             "rest_max_dif_probe": 1})
    res, err = _run(core)
    strict_fail = err is not None and "dif=" in str(err)
    core, line, _d, _w = make_core(cfg_over={"rest_max_dif": 1,
                                             "rest_max_dif_probe": 1})
    core.rest_max_dif = 100000.0
    res2, err2 = _run(core)
    check("(Ruhe roh) Kern nutzt rest_max_dif, mit Override die weitere "
          "Grenze", strict_fail and err2 is None and res2 == [0.0],
          "%s / %s" % (err, err2))
    # Ruhetest-Wiederholung: nur der Ruhetest wird wiederholt, mit Entschärfen
    # dazwischen; Default im Kern 0 (Rückfallweg/Tests unverändert).
    check("(Ruhe Wdh.) Default rest_retries 2, 6 abgelehnt",
          cfg["rest_retries"] == 2 and _raises({"rest_retries": 6},
                                                "rest_retries"))
    orig = K.check_rest_ok
    calls = {"n": 0}

    def _flaky(fails):
        def fake(datas, cfg_, ticks=None, filt=None):
            calls["n"] += 1
            ok, info = orig(datas, cfg_, ticks=ticks, filt=filt)
            if calls["n"] <= fails:
                info = dict(info, errors=["Ausreißer (Test)"])
                return False, info
            return ok, info
        return fake
    try:
        calls["n"] = 0
        K.check_rest_ok = _flaky(1)
        core, line, _d, _w = make_core()
        core.rest_retries = 2
        res, err = _run(core)
        lg = line.log
        check("(Ruhe Wdh.) 1 Ausreißer: entschärft, neu scharf, Antastung ok",
              err is None and res == [0.0] and core.arm_count == 2
              and core.disarm_count == 2 and lg.count("start_pres") == 2
              and lg.index("stop_pres1") < lg.index("DESCEND")
              and 0.3 in core._test_pauses, str((err, lg)))
        calls["n"] = 0
        K.check_rest_ok = _flaky(3)
        core, line, _d, _w = make_core()
        core.rest_retries = 2
        res, err = _run(core)
        check("(Ruhe Wdh.) 3 Ausreißer bei 2 Wiederholungen: Abbruch ohne "
              "Fahrt, entschärft", err is not None and "DESCEND" not in line.log
              and not core.armed and core.arm_count == 3, str(err))
        calls["n"] = 0
        K.check_rest_ok = _flaky(1)
        core, line, _d, _w = make_core()
        res, err = _run(core)
        check("(Ruhe Wdh.) Kern-Default 0: keine Wiederholung",
              err is not None and core.arm_count == 1, str(err))
    finally:
        K.check_rest_ok = orig
    # Signalweg-Fehler wird nie wiederholt.
    core, line, _d, _w = make_core()
    core.rest_retries = 2
    line.stuck_low = True
    res, err = _run(core)
    check("(Ruhe Wdh.) Signalweg-Fehler ohne Wiederholung",
          err is not None and line.log.count("swap0") == 1, str(err))
    core, line, _d, _w = make_core()
    core.rest_retries = 2
    line.trigger_on_arm = True
    res, err = _run(core)
    check("(Ruhe Wdh.) Kontakt vor der Fahrt ohne Wiederholung",
          err is not None and line.log.count("start_pres") == 1
          and "DESCEND" not in line.log, str(err))


def test_1018_e2_hot_probe():
    """Live-Abgleich E2: Warnung bei Antastung mit heißer Düse
    (> temp_nozzle_warn 140 + hot_probe_margin 10), nur Meldung."""
    cfg = K.parse_options({})
    check("(E2) Defaults: hot_probe_warn an, Marge 10 °C, Referenz 140",
          cfg["hot_probe_warn"] is True and cfg["hot_probe_margin"] == 10.0
          and cfg["temp_nozzle_warn"] == 140.0)
    check("(E2) reine Funktion: 150 °C still, 151 °C Warnung, None still",
          K.hot_probe_warning(150.0, cfg) is None
          and "151" in (K.hot_probe_warning(151.0, cfg) or "")
          and K.hot_probe_warning(None, cfg) is None)
    off = K.parse_options({"hot_probe_warn": False})
    check("(E2) hot_probe_warn False -> nie Warnung",
          K.hot_probe_warning(188.0, off) is None)
    m5 = K.parse_options({"hot_probe_margin": 5, "temp_nozzle_warn": 130})
    check("(E2) Referenz/Marge konfigurierbar (130+5: 136 warnt, 135 nicht)",
          K.hot_probe_warning(136.0, m5) is not None
          and K.hot_probe_warning(135.0, m5) is None)
    check("(E2) hot_probe_margin 0 / 101 abgelehnt",
          _raises({"hot_probe_margin": 0}, "hot_probe_margin")
          and _raises({"hot_probe_margin": 101}, "hot_probe_margin"))
    # Rückfallweg: Warnung in warnings, Antastung läuft normal weiter.
    _c, _t, _p, _s, ctl, log = make_ctl(z_start=0.2, z_true=0.0,
                                        fake_over={"temps": (180.0, 50.0)})
    r = ctl.probe_point(100.0, 100.0)
    hw = [w for w in r.get("warnings", []) if "heiße Antastung" in w]
    check("(E2) mode steps 180 °C: genau 1 Heiß-Warnung (statt "
          "Abweichungs-Warnung), Status ok, geloggt",
          r.get("status") == "ok" and len(hw) == 1
          and not any("weicht" in w and "Düse" in w
                      for w in r.get("warnings", []))
          and any("heiße Antastung" in x for x in log.infos), str(r)[:200])
    _c, _t, _p, _s, ctl, _l = make_ctl(z_start=0.2, z_true=0.0,
                                       fake_over={"temps": (145.0, 50.0)})
    r = ctl.probe_point(100.0, 100.0)
    check("(E2) mode steps 145 °C: keine Temperatur-Warnung",
          r.get("status") == "ok"
          and not any("°C" in w for w in r.get("warnings", [])),
          str(r.get("warnings")))
    _c, _t, _p, _s, ctl, _l = make_ctl(
        z_start=0.2, z_true=0.0, cfg_over={"hot_probe_warn": False},
        fake_over={"temps": (180.0, 50.0)})
    r = ctl.probe_point(100.0, 100.0)
    check("(E2) mode steps hot_probe_warn False: alte Abweichungs-Warnung",
          r.get("status") == "ok"
          and not any("heiße" in w for w in r.get("warnings", []))
          and any("weicht" in w for w in r.get("warnings", [])),
          str(r)[:200])
    # Probe-Hauptweg: Kern meldet beim Scharfschalten, Ablauf unverändert.
    core, line, desc, waits = make_core()
    msgs = []

    class _L:
        def info(self, m):
            msgs.append(m)
    core.log = _L()
    core.get_nozzle_temp = lambda: 175.0
    res, err = _run(core)
    check("(E2) Probe-Weg 175 °C: Warnung geloggt + in last['arm'], "
          "Antastung ok, Reihenfolge unverändert",
          err is None and any("heiße Antastung" in m for m in msgs)
          and core.last["arm"].get("warnings")
          and line.log == EXPECT_ORDER, "%s %s" % (err, msgs))
    core, line, desc, waits = make_core()
    msgs2 = []

    class _L2:
        def info(self, m):
            msgs2.append(m)
    core.log = _L2()
    core.get_nozzle_temp = lambda: 140.0
    res, err = _run(core)
    check("(E2) Probe-Weg 140 °C: keine Warnung",
          err is None and not msgs2 and "warnings" not in core.last["arm"],
          str(msgs2))

    def _boom():
        raise RuntimeError("kein Extruder")
    core, line, desc, waits = make_core()
    core.get_nozzle_temp = _boom
    res, err = _run(core)
    check("(E2) Probe-Weg: Temperatur nicht lesbar -> still, Antastung ok",
          err is None and line.log == EXPECT_ORDER, str(err))
    core, line, desc, waits = make_core({"hot_probe_warn": False})
    called = []
    core.get_nozzle_temp = lambda: called.append(1) or 200.0
    res, err = _run(core)
    check("(E2) Probe-Weg hot_probe_warn False: Temperatur nicht gelesen",
          err is None and not called, str(err))


# Live-Tripel (best_indx, step_bst_indx, zoft), Live-Abgleich 18.10.2026
LIVE_G28 = [(50, 36, 0.053), (52, 41, 0.043)]
LIVE_CLEAN = [(53, 43, 0.040), (47, 31, 0.062), (44, 24, 0.075)]


def test_1018_a8_can_brk_context():
    cfg = K.parse_options({})
    check("(A8) Default can_brk_scope = mesh", cfg["can_brk_scope"] == "mesh")
    rule = [K.check_can_brk(b, s_, z, cfg)[0] for b, s_, z in
            LIVE_G28 + LIVE_CLEAN]
    check("(A8) Regel selbst unverändert: verwirft 2/2 G28 + 2/3 Clean "
          "(53/43/0,040 plausibel)", rule == [False, False, True, False,
                                              False],
          str(rule))
    check("(A8) can_brk_applies: nur mesh (Scope mesh)",
          K.can_brk_applies("mesh", cfg)
          and not K.can_brk_applies("home", cfg)
          and not K.can_brk_applies("single", cfg)
          and not K.can_brk_applies(None, cfg))
    c_all = K.parse_options({"can_brk_scope": "all"})
    c_off = K.parse_options({"can_brk_scope": "off"})
    check("(A8) Scope all = alt (überall), off = nie",
          all(K.can_brk_applies(x, c_all) for x in K.PROBE_CONTEXTS)
          and not any(K.can_brk_applies(x, c_off) for x in K.PROBE_CONTEXTS))
    check("(A8) ungültiger Scope/Kontext abgelehnt",
          _raises({"can_brk_scope": "g28"}, "can_brk_scope"))
    try:
        K.can_brk_applies("clean", cfg)
        bad = False
    except ValueError:
        bad = True
    check("(A8) unbekannter Kontext -> ValueError", bad)

    # Controller: Regel liefert False (wie G28/Clean live) ->
    # außerhalb Mesh KEIN Verwerfen/keine Wiederholung, im Mesh 1× versetzt.
    orig = K.check_can_brk

    def _always_false(b, s_, z, c):
        return False, "Test: wie Live-G28"
    K.check_can_brk = _always_false
    try:
        res = {}
        for ctx in ("single", "home", "mesh"):
            _c, tool, _p, _s, ctl, _l = make_ctl(z_start=0.2, z_true=0.0)
            r = ctl.probe_point(100.0, 100.0, context=ctx)
            res[ctx] = (r, ctl._test_events.count("start_pres"), tool)
        _c, _t, _p, _s, ctl_all, _l = make_ctl(
            z_start=0.2, z_true=0.0, cfg_over={"can_brk_scope": "all"})
        r_all = ctl_all.probe_point(100.0, 100.0, context="home")
        _c, tool_x, _p, _s, ctl_x, _l = make_ctl(z_start=0.2, z_true=0.0)
        r_x = ctl_x.probe_point(100.0, 100.0, context="clean")
    finally:
        K.check_can_brk = orig
    for ctx in ("single", "home"):
        r, n_start, _t = res[ctx]
        check("(A8) Kontext %s: can_brk True (nicht ausgewertet), Regelwert "
              "False als Diagnose, keine Wiederholung" % ctx,
              r.get("status") == "ok" and r["can_brk"] is True
              and r["can_brk_checked"] is False and r["can_brk_rule"] is False
              and not r.get("retried") and n_start == 1
              and r.get("context") == ctx
              and "nicht ausgewertet" in r["can_reason"], str(r)[:200])
    r, n_start, _t = res["mesh"]
    check("(A8) Kontext mesh: Regel greift -> 1× versetzt wiederholt "
          "(2× scharf), Ergebnis aus 2. Versuch",
          r.get("retried") is True and n_start == 2
          and r.get("first_try") is not None
          and r.get("can_brk_checked") is True and r.get("context") == "mesh",
          str(r)[:200])
    check("(A8) Scope all: auch home wird wiederholt (altes Verhalten)",
          r_all.get("retried") is True, str(r_all)[:160])
    check("(A8) unbekannter Kontext -> config_error ohne Bewegung",
          r_x.get("status") == "config_error" and not tool_x.moves,
          str(r_x)[:160])
    # Echte Regel + Live-Werte (Fake liefert normale Indizes) -> mesh ok
    _c, _t, _p, _s, ctl, _l = make_ctl(z_start=0.2, z_true=0.0)
    r = ctl.probe_point(100.0, 100.0, context="mesh")
    check("(A8) Kontext mesh, plausible Antastung: can_brk ausgewertet, "
          "True, keine Wiederholung",
          r.get("status") == "ok" and r["can_brk_checked"] is True
          and r["can_brk"] is True and not r.get("retried"), str(r)[:160])

    # G-Code-Hülle: welcher Befehl welchen Kontext übergibt
    calls = []

    class _Ctl:
        def probe_point(self, x, y, dry_run=False, context="single"):
            calls.append((context, dry_run))
            return {"status": "ok", "z": 0.0, "A": 0.0, "zoft": 0.0,
                    "best_indx": 55, "step_bst_indx": 50, "can_brk": True,
                    "can_brk_checked": context == "mesh"}

    class _G:
        def __init__(self, p):
            self.p = dict(p)
            self.msgs = []

        def get(self, k, d=None):
            return self.p.get(k, d)

        def get_int(self, k, d=None):
            return int(self.p.get(k, d))

        def get_float(self, k, d=None):
            v = self.p.get(k, d)
            return None if v is None else float(v)

        def respond_info(self, m):
            self.msgs.append(m)

        def error(self, m):
            return RuntimeError(m)

    class _Pr:
        def lookup_object(self, name):
            raise KeyError(name)

    class _Gc:
        def run_script(self, s_):
            calls.append(("script", s_))

    obj = K.PrinterK2Prtouch.__new__(K.PrinterK2Prtouch)
    obj.cfg = K.parse_options({"mode": "steps", "allow_move": True})
    obj.controller = _Ctl()
    obj.printer = _Pr()
    obj.gcode = _Gc()
    obj.cmd_mesh(_G({"COUNT": "2"}))
    obj.cmd_home_z(_G({}))
    obj.cmd_probe(_G({"X": "10", "Y": "10"}))
    obj.cmd_probe(_G({"X": "10", "Y": "10", "CONTEXT": "MESH"}))
    ctxs = [c_ for c_ in calls if c_[0] != "script"]
    check("(A8) Hülle: MESH -> mesh (4 Punkte), HOME_Z -> home, "
          "PROBE_STEPS -> single bzw. CONTEXT=mesh",
          ctxs == [("mesh", False)] * 4 + [("home", False),
                                           ("single", False),
                                           ("mesh", False)], str(ctxs))
    g = _G({"CONTEXT": "clean"})
    try:
        obj.cmd_probe(g)
        bad = False
    except RuntimeError as exc:
        bad = "CONTEXT" in str(exc)
    check("(A8) PROBE_STEPS CONTEXT=clean -> Fehler, keine Antastung",
          bad and len([c_ for c_ in calls if c_[0] != "script"]) == 7)


def main():
    test_live_ring_read()
    test_swap_mechanism()
    test_live_packed_decode()
    test_a_contact_accuracy()
    test_b_step_limits()
    test_c_emergency()
    test_d_no_move_without_allow()
    test_e_swap_rest_abort_before_move()
    test_f_formatstrings()
    test_g_stop_within_one_step()
    test_extra_can_brk_retry()
    test_stock_mapping()
    test_probe_arm_disarm_order()
    test_probe_disarm_on_exception()
    test_probe_ack_error()
    test_probe_pc7_already_triggered()
    test_probe_allow_move_blocks()
    test_probe_speed_limit()
    test_probe_target_z()
    test_klippy_adapters_disarm_and_frozen()
    test_upstream_integration()
    test_upstream_integration_guards()
    test_1017_config_validation()
    test_1017_arm_settle()
    test_1017_min_speed()
    test_1017_rest_extended()
    test_1017_ring_blocks()
    test_1017_pc7_pull()
    test_1017_upstream()
    test_1017_steps_order()
    test_1017_steps_register_empty()
    test_1017_steps_errors()
    test_1017_steps_guards()
    test_1017_treppe_default_unchanged()
    test_1017_treppe_cumulative()
    test_1017_treppe_robust()
    test_1017_treppe_pure_config()
    test_1017_check_config()
    test_1017_check_off_unchanged()
    test_1017_check_oid_estp()
    test_1017_check_pure_eval()
    test_1017_check_interlock_finally()
    test_1017_check_upstream_cmd()
    test_1018_a7_emergency_step()
    test_1018_a8_can_brk_context()
    test_1018_e2_hot_probe()
    test_1022_fan_damping()
    print("\n%d bestanden, %d fehlgeschlagen" % (len(PASS), len(FAIL)))
    if FAIL:
        print("FEHLER:", FAIL)
        sys.exit(1)
    print("ALLE TESTS BESTANDEN")


if __name__ == "__main__":
    main()


# --- Review 16.10.2026: harte Obergrenze für max_travel_mm ---
def _review_max_travel_cap():
    import k2_prtouch as _m
    try:
        _m.parse_options({"max_travel_mm": 50})
        print("FAIL (review) max_travel_mm=50 wurde akzeptiert")
        return False
    except ValueError:
        pass
    _m.parse_options({"max_travel_mm": 10})
    print("PASS (review) max_travel_mm > 10 wird abgelehnt, 10 erlaubt")
    return True


if __name__ == "__main__":
    import sys as _sys
    if not _review_max_travel_cap():
        _sys.exit(1)
