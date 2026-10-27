#!/usr/bin/env python3
"""flush.py – reiner Rechen-Nachbau der Stock-Spüllogik beim CFS-Farbwechsel (K2 Plus, FW 1.1.7.0).

Nur Rechnen und G-Code-Listen BAUEN – kein Senden, kein Klipper, kein Drucker.

Quellen (statisch rekonstruiert):
  (1) /usr/share/klipper/klippy/extras/box_wrapper.cpython-39.so (Stock-Rootfs)
      (Cython 0.29.21, armhf; Quellname klippy/extras/box_wrapper.py).
      `Z.<n>` = __pyx_lineno aus den Fehlerpfaden, `@0x…` = ELF-vaddr.
  (2) Farb-Spülvolumen: CFFI-Funktion `get_flushing_volume(rgb_t src, rgb_t dst)`
      aus chelper/filament_change.o. Im 1.1.7.0-Rootfs-Auszug fehlt chelper; Quelle
      ist das OTA-Image V1.1.3.13 desselben Modells (Objekt mit Symbolen, LTO-
      Abschnitte RGB2HSV / DeltaHS_BBS / calc_triangle_3rd_edge /
      calc_flushing_volume). Gleichheit mit 1.1.7.0 ist durch vier Live-Werte
      gestützt (Selbsttest), aber nicht byte-geprüft.

BELEGFORM: B = belegt (Code), B+L = zusätzlich Live-Log, A = ANNAHME.

Ablauf Stock (MultiColorMeterialBoxWrapper.material_change_flush Z.4494–4517):
  M106 P0 S50 -> M400 -> flush_len = get_flush_len(last, tnn) ->
  (velocity, percent) = get_flush_velocity -> Temperaturen -> Schnellheizen ->
  Tn_Extrude(flush_len, …) = cal_flush_list + je Abschnitt
  check_flush_temp_and_extrude (20 %/80 %) + blow + nozzle_clean ->
  Messrad-Prüfung (Düse verstopft) -> move_to_safe_pos -> M104 S<next_temp>.
"""
import math
import struct

# ---------------------------------------------------------------------------
# Stock-Defaults [box] (BoxCfg.__init__ @0x1b6808, config.getX(default, min, max))
# ---------------------------------------------------------------------------
BOX_DEFAULTS = {
    "nozzle_volume": 183.0,                     # getfloat 183 [0, 300]     @0x1b8104
    "max_temp_flush_times": 3,                  # getint   3   [0, 10]      @0x1b81d0
    "diff_material_type_min_flush_len": 370.0,  # getfloat 370 [300, 1000]  @0x1b828c
    "flush_multiplier": 1.0,                    # getfloat 1   [0, 10]      @0x1b8334
    "flush_velocity_factor": 1.2,               # getfloat 1.2 [1, 5]       @0x1b83f0
    "Tn_extrude": 140.0,                        # getfloat 140 [0, 200]     @0x1b84ac
    "Tn_extrude_velocity": [360],               # getintlist (360,)         @0x1b85f8
    "Tn_extrude_temp": 220.0,                   # getfloat 220 [180, …]     @0x1b805c
    "box_first_clean_length": 90,               # getint   90  [0, 600]     @0x1b7660
    "box_need_clean_length": 80,                # getint   80  [0, first]   @0x1b771c
    "box_need_clean_length_max": 100,           # getint   100 [0, 600]     @0x1b77f4
    "diff_length": -10.0,                       # getfloat -10 [-30, 30]    @0x1b8bd4
    # Positionen (has_extrude_pos=1-Zweig)
    "clean_left_pos_x": 150.0, "clean_left_pos_y": 368.0,
    "clean_right_pos_x": 170.0, "clean_right_pos_y": 368.0,
    "clean_velocity": 6000.0,
    "extrude_pos_x": 127.0, "extrude_pos_y": 368.0,
    "clean_pos_left_x": 157.0, "clean_pos_right_x": 172.0,
    "clean_pos_middle_y": 374.0,
    "safe_pos_x": 205.0, "safe_pos_y": 350.0,
}

# Live-[box]-Werte (box.cfg des Druckers, nur Positions-/Längenoptionen)
LIVE_BOX_CFG = dict(BOX_DEFAULTS, **{
    "box_need_clean_length": 70,
    "clean_left_pos_x": 135.0, "clean_left_pos_y": 378.0,
    "clean_right_pos_x": 160.0, "clean_right_pos_y": 378.0,
    "clean_velocity": 12000.0,
    "extrude_pos_x": 133.0, "extrude_pos_y": 378.0,
    "clean_pos_left_x": 160.0, "clean_pos_right_x": 170.0,
    "clean_pos_middle_y": 374.0,
    "safe_pos_x": 225.0, "safe_pos_y": 345.0,
    "Tn_extrude": 140.0, "Tn_extrude_velocity": [360], "Tn_extrude_temp": 220.0,
})

FILAMENT_AREA_DIVISOR = 2.4   # material_volume_to_length: volume / 2.4 (Z.3003, @0x1c6748 -> K 2.4)
IGNORE_COLORS = ("-1", "none", "unknown")   # ignore_ops, MCMBW.get_flush_len Z.4423

# ---------------------------------------------------------------------------
# 1. Farb-Spülvolumen (filament_change.o, calc_flushing_volume.constprop.0)
# ---------------------------------------------------------------------------
_COS120 = struct.unpack("<d", bytes.fromhex("af08191b0000e0bf"))[0]   # Literal @0x420 (.o)
_MAX_FLUSH_VOLUME = 800.0     # m_max_flush_volume = 0x44480000 / Literal 800.0 @0x430
_MIN_FLUSH_VOLUME = 60.0      # Literal 60.0 @0x428


def _f(x):
    """Auf float32 runden (die C-Funktion rechnet gemischt f32/f64)."""
    return struct.unpack("<f", struct.pack("<f", x))[0]


def _rgb2hsv(r, g, b):
    """RGB2HSV (@0x0 im .o): r,g,b in [0,1] als float32."""
    mx = _f(max(max(r, g), b))
    mn = _f(min(min(r, g), b))
    d = _f(mx - mn)
    if abs(d) < 0.001:
        h = 0.0
    elif r == mx:
        h = _f(math.fmod(_f(_f(g - b) / d), 6.0) * 60.0)
    elif g == mx:
        h = _f(_f(_f(_f(b - r) / d) + 2.0) * 60.0)
    else:
        h = _f(_f(_f(_f(r - g) / d) + 4.0) * 60.0)
    s = 0.0 if abs(mx) < 0.001 else _f(d / mx)
    return h, s, mx


def flushing_volume(src, dst):
    """get_flushing_volume(src_rgb, dst_rgb) -> int mm³ in [60, 800].

    Helligkeitsanteil (0.3R+0.59G+0.11B): dunkler->heller pow(Δ,0.7)*560, sonst Δ*80
    (dann Farbabstand auf 0.33·v_src+0.67·v_dst begrenzt); Farbabstand im HSV-Kreis
    (max 1.2) *230; beide als Dreieckseiten mit 120° kombiniert."""
    r1, g1, b1 = (_f(c / 255.0) for c in src)
    r2, g2, b2 = (_f(c / 255.0) for c in dst)
    h1, s1, v1 = _rgb2hsv(r1, g1, b1)
    h2, s2, v2 = _rgb2hsv(r2, g2, b2)
    a1 = _f(_f(h1 / 180.0) * math.pi)
    a2 = _f(_f(h2 / 180.0) * math.pi)
    dy = _f(math.sin(a1) * s1 * v1 - math.sin(a2) * s2 * v2)
    dx = _f(math.cos(a1) * s1 * v1 - math.cos(a2) * s2 * v2)
    hs = _f(min(_f(1.2), _f(math.sqrt(_f(_f(dy * dy) + _f(dx * dx))))))
    from_l = _f(g1 * 0.59 + r1 * 0.3 + b1 * 0.11)
    to_l = _f(g2 * 0.59 + r2 * 0.3 + b2 * 0.11)
    if from_l <= to_l:
        lumi = _f(math.pow(_f(to_l - from_l), _f(0.7)) * 560.0)
    else:
        lumi = _f(_f(from_l - to_l) * 80.0)
        hs = _f(min(_f(v1 * 0.33 + v2 * 0.67), hs))
    hsf = _f(hs * 230.0)
    a2b2 = _f(_f(lumi * lumi) + _f(hsf * hsf))
    ab2 = _f(_f(hsf + hsf) * lumi)
    vol = _f(math.sqrt(a2b2 - ab2 * _COS120))
    vol = _f(max(vol, _MIN_FLUSH_VOLUME))
    return int(min(int(vol), _MAX_FLUSH_VOLUME))


def parse_color_value(cv):
    """Tn_data color_value (z. B. '0RRGGBB') -> [r, g, b] oder None (ignoriert).

    Stock Z.4457/4461/4462: in ignore_ops -> None; hex_pairs = [cv[j:j+2] for j in
    range(1, len(cv), 2)]; scv = [int(p, 16) for p in hex_pairs]. convert_scv nimmt
    davon [0..2] (Z.2743–2745)."""
    if cv is None or cv in IGNORE_COLORS:
        return None
    pairs = [cv[j:j + 2] for j in range(1, len(cv), 2)]
    vals = [int(p, 16) for p in pairs]
    if len(vals) < 3:
        return None          # convert_scv-Ausnahmezweig -> False -> get_flush_len None
    return vals[:3]


def color_flush_len(src, dst, cfg=BOX_DEFAULTS):
    """BoxAction.get_flush_len (Z.3006–3013, @0x12528c):
    (flushing_volume + nozzle_volume) / 2.4 * flush_multiplier (mm Filament)."""
    vol = flushing_volume(src, dst)
    return vol, (vol + cfg["nozzle_volume"]) / FILAMENT_AREA_DIVISOR * cfg["flush_multiplier"]


def finish_flush_len(flush_len, different, cfg=BOX_DEFAULTS):
    """Nachbearbeitung in MCMBW.get_flush_len (Farbzweig Z.4477–4491, G-Code-Zweig
    Z.4438–4450 identisch): anderes Material -> ×2, mindestens diff_min; sonst
    mindestens box_first_clean_length + box_need_clean_length."""
    if different:
        v = flush_len * 2
        return v if v > cfg["diff_material_type_min_flush_len"] else cfg["diff_material_type_min_flush_len"]
    floor = cfg["box_first_clean_length"] + cfg["box_need_clean_length"]
    return floor if flush_len < floor else flush_len


def get_flush_len(last_color, current_color, different=False, last_tnn="T1A", cfg=BOX_DEFAULTS):
    """MultiColorMeterialBoxWrapper.get_flush_len, Farbzweig (nicht druckend bzw. G-Code
    ohne Matrix). last_color = remain_material.color (gespeicherte Farbe des
    Materials in der Düse), current_color = Tn_data[..]['color_value'] des Ziels.
    Fallback (keine/ignorierte Farbe, convert-Fehler): boxcfg.Tn_extrude roh."""
    if last_tnn is None and last_color is None:
        return cfg["Tn_extrude"]                       # Z.4425–4427
    scv = parse_color_value(last_color)
    if scv is None:
        return cfg["Tn_extrude"]                       # Z.4456–4459
    tcv = parse_color_value(current_color)
    if tcv is None:
        return cfg["Tn_extrude"]                       # Z.4464–4467
    _vol, fl = color_flush_len(scv, tcv, cfg)          # Z.4470
    return finish_flush_len(fl, different, cfg)


def gcode_matrix_flush_len(matrix, last_idx, cur_idx, multiplier=None):
    """get_flush_length_from_gcode (Z.4357–4406): Slicer-flush_volumes_matrix
    (quadratisch, Länge n²), Volumen [last][cur] / 2.4 * flush_multiplier des
    Slicers (fehlt -> 1). KEIN nozzle_volume. None bei Formfehler/Index."""
    if not matrix:
        return None
    q = math.sqrt(len(matrix))
    if not q.is_integer():
        return None
    q = int(q)
    if not (0 <= last_idx < q and 0 <= cur_idx < q):
        return None
    vol = matrix[last_idx * q + cur_idx]
    return vol / FILAMENT_AREA_DIVISOR * (1 if multiplier is None else multiplier)


# ---------------------------------------------------------------------------
# 2. Abschnitte (BoxAction.cal_flush_list Z.3015–3049, @0x123bf0)
# ---------------------------------------------------------------------------
def cal_flush_list(src_length, cfg=BOX_DEFAULTS):
    first = cfg["box_first_clean_length"]
    need = cfg["box_need_clean_length"]
    mx = cfg["box_need_clean_length_max"]
    out = []
    if src_length <= mx:                                   # Z.3016
        out.append(src_length if src_length >= first else first)   # Z.3017–3022
        return out
    out.append(first)                                      # Z.3025
    remain = src_length - first                            # Z.3026
    count = 1
    while remain >= need:                                  # Z.3028
        out.append(need)                                   # Z.3030
        remain -= need                                     # Z.3031
        count += 1                                         # Z.3032
    if count == 1 and remain > 0:                          # Z.3033
        out.append(need)                                   # Z.3035
    elif count >= 2 and remain > 0:                        # Z.3036/3038
        avg = (remain - (mx - first)) / (count - 1)        # Z.3039
        if remain <= mx - need:
            out[count - 1] += remain                       # Z.3041
        elif avg > mx - need:                              # Z.3042
            out.append(need)                               # Z.3044
        else:
            out[0] = mx                                    # Z.3046/3047
            for i in range(count - 1):                     # Z.3048
                out[i + 1] += avg                          # Z.3049
    return out


# ---------------------------------------------------------------------------
# 3. Geschwindigkeit / Temperatur
# ---------------------------------------------------------------------------
def flush_velocity(max_volumetric_speed, nozzle_diameter=0.4, baseline=0.4, cfg=BOX_DEFAULTS):
    """BoxAction.get_flush_velocity (Z.2942–2981): next = max_volumetric_speed/2.4
    (mm/s, aus material_database.json), sonst Tn_extrude_velocity[0]/60, sonst 6;
    base = min(next*60, 360); scale = nozzle/baseline (baseline <= 0 -> 1);
    Rückgabe ([base*scale], [1]) in mm/min."""
    if max_volumetric_speed:
        nxt = float(max_volumetric_speed) / FILAMENT_AREA_DIVISOR
    else:
        try:
            nxt = cfg["Tn_extrude_velocity"][0] / 60
        except Exception:  # noqa: BLE001
            nxt = 6
    base = 360 if 360 < nxt * 60 else nxt * 60
    scale = 1.0 if baseline <= 0 else nozzle_diameter / baseline
    return [base * scale], [1]


def flush_temps(last_temp, next_temp, last_max, next_max):
    """get_flush_temp / get_flush_max_temp (Z.2886–2898): je max(); material_change_flush
    Z.4507: Max-Temperatur-Spülung nur wenn last_temp >= next_temp."""
    flush_temp = max(last_temp, next_temp)
    flush_max = max(last_max, next_max)
    return flush_temp, (flush_max if last_temp >= next_temp else flush_temp)


def segment_plan(length, index, temp, max_temp, velocity, cfg=BOX_DEFAULTS, max_temp_flush=True):
    """check_flush_temp_and_extrude (Z.1336–1359): Temperatur-Kommandos + 2 Extrusionen
    (20 % mit velocity, 80 % mit velocity*factor). index = Abschnittsnummer, für den
    LETZTEN Abschnitt (wenn >1) übergibt Tn_Extrude max_temp_flush_times (Z.3079/3080)."""
    steps = []
    if not max_temp_flush:
        return [("E", length, velocity[0])]                # Z.1359 check_and_extrude
    factor = 1
    if index == 0:
        steps.append(("WAIT", max_temp - 5))               # quickly_wait_heating(max-5)
        steps.append(("M104", max_temp))                   # "set target max temp"
    if index < cfg["max_temp_flush_times"]:
        factor = cfg["flush_velocity_factor"]
    elif index == cfg["max_temp_flush_times"]:
        steps.append(("M104", temp))                       # "restore target temp"
    for portion, f in ((0.2, 1), (0.8, factor)):
        steps.append(("E", length * portion, velocity[0] * f))
    return steps


def flush_plan(flush_len, velocity, temp, max_temp, cfg=BOX_DEFAULTS):
    """Tn_Extrude-Spülpfad (Z.3067–3104): Abschnitte, je segment_plan + blow +
    nozzle_clean(circle=False), letzter Abschnitt nozzle_clean() (circle=True)."""
    segs = cal_flush_list(flush_len, cfg)
    plan = []
    n = len(segs)
    for i, seg in enumerate(segs):
        idx = cfg["max_temp_flush_times"] if (i != 0 and i == n - 1) else i
        plan.append((seg, segment_plan(seg, idx, temp, max_temp, velocity, cfg),
                     "nozzle_clean(circle=%s)" % ("True" if i == n - 1 else "False")))
    return segs, plan


def nozzle_blocked(wheel_diff_sum, cfg=BOX_DEFAULTS):
    """Z.3102: abs(Summe Messrad-Differenzen) < abs(diff_length) -> key845."""
    return abs(wheel_diff_sum) < abs(cfg["diff_length"])


# ---------------------------------------------------------------------------
# 4. G-Code-Folgen (nur Listen bauen)
# ---------------------------------------------------------------------------
def gcode_extrude(length, feed):
    """check_and_extrude / check_speed_and_extrude (Z.1311–1332)."""
    return ["G91", "G0 E%.2f F%.2f" % (length, feed), "G90", "M400"]


def gcode_blow(fan0_last=0.0):
    """BoxAction.blow (Z.2733–2740): 5 s Lüfter fan0 voll, dann zurück."""
    return ["SET_PIN PIN=fan0 VALUE=255", "G4 P5000", "M400",
            "SET_PIN PIN=fan0 VALUE=%.2f" % fan0_last]


def gcode_nozzle_clean(cfg, circle=True, offset=0.0, y_pos=None,
                       max_accel=20000.0, max_accel_to_decel=10000.0):
    """BoxAction.nozzle_clean (Z.3149–3182, @0x118eec). offset = monotonic() % 3
    (Stock zufällig 0..3 mm, live "temp_pos:%s"). Rückgabe ohne die flushing_sign-
    Aufrufe (set bei circle am Anfang, reset am Ende)."""
    v = cfg["clean_velocity"]
    g = ["G90", "SET_VELOCITY_LIMIT ACCEL=5000 ACCEL_TO_DECEL=5000"]
    if y_pos is not None and y_pos < cfg["safe_pos_y"]:                       # Z.3159
        g += ["G0 X%.2f F%.2f" % (cfg["clean_right_pos_x"], v),
              "G0 Y%.2f F%.2f" % (cfg["safe_pos_y"], v)]
    g += ["G0 X%.2f F%.2f" % (cfg["clean_pos_right_x"], v),                    # Z.3162
          "G0 X%.2f F%.2f" % (cfg["clean_left_pos_x"], v)]                     # Z.3163
    if circle:                                                                  # Z.3164
        g += ["G0 X%.2f Y%.2f F12000" % (cfg["clean_pos_left_x"] + offset, cfg["clean_pos_middle_y"]),
              "G2 I4 J0 P1 F10000",
              "G0 X%.2f Y%.2f F10000" % (cfg["clean_pos_right_x"] - offset, cfg["clean_pos_middle_y"]),
              "G3 I-4 J0 P1 F10000"]
    for _ in range(3):                                                          # Z.3169–3173
        g += ["G0 X%.2f Y%.2f F%.2f" % (cfg["clean_right_pos_x"], cfg["clean_right_pos_y"], v),
              "G0 X%.2f Y%.2f F%.2f" % (cfg["extrude_pos_x"], cfg["clean_left_pos_y"], v)]
    if circle:                                                                  # Z.3174–3178
        g += ["G0 X%.2f F%.2f" % (cfg["clean_right_pos_x"], v),
              "G0 Y%.2f F%.2f" % (cfg["safe_pos_y"], v)]
    g += ["M400", "SET_VELOCITY_LIMIT ACCEL=%.3f ACCEL_TO_DECEL=%.3f" % (max_accel, max_accel_to_decel)]
    return g


# ---------------------------------------------------------------------------
# Selbsttest
# ---------------------------------------------------------------------------
def selftest():
    total = 0
    fails = 0

    def check(name, cond):
        nonlocal total, fails
        total += 1
        print(("ok   " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    W, V, C = (255, 255, 255), (0xce, 0x58, 0xf8), (0x26, 0xee, 0xee)
    # Live-Paare (Log 12./13.10., FW 1.1.7.0): "flush_volume: X, nozzle_volume: 183"
    check("Volumen gleiche Farbe = 60 (live)", flushing_volume(W, W) == 60)
    check("Volumen weiß->violett = 165 (live)", flushing_volume(W, V) == 165)
    check("Volumen weiß->türkis = 193 (live)", flushing_volume(W, C) == 193)
    check("Volumen türkis->weiß = 367 (live)", flushing_volume(C, W) == 367)
    check("Volumen schwarz->weiß = 560 (Bereich)", flushing_volume((0, 0, 0), W) == 560)
    check("Volumen nie > 800", max(flushing_volume((a, 0, 0), (0, b, 255))
                                   for a in (0, 128, 255) for b in (0, 255)) <= 800)
    # flush_len (live "flush_len: %f")
    for vol, exp in ((60, 101.25), (165, 145.0), (193, 156.666667), (367, 229.166667)):
        check("flush_len(%d) = %s (live)" % (vol, exp),
              abs((vol + 183) / 2.4 * 1.0 - exp) < 1e-5)
    _, fl = color_flush_len(C, W)
    check("color_flush_len türkis->weiß = 229.1667", abs(fl - 229.1666667) < 1e-6)
    # Farbstring
    check("parse '0FFFFFF'", parse_color_value("0FFFFFF") == [255, 255, 255])
    check("parse '0ce58f8'", parse_color_value("0ce58f8") == [0xce, 0x58, 0xf8])
    check("parse '-1' ignoriert", parse_color_value("-1") is None)
    # Nachbearbeitung
    L = LIVE_BOX_CFG
    check("min first+need: 101.25 -> 160 (live extrude = 160)", finish_flush_len(101.25, False, L) == 160)
    check("anderes Material: 145 -> max(290, 370) = 370", finish_flush_len(145.0, True, L) == 370)
    check("anderes Material: 229.17 -> 458.33", abs(finish_flush_len(229.1666667, True, L) - 458.3333333) < 1e-6)
    check("Fallback ohne Farbe -> Tn_extrude 140", get_flush_len(None, "0FFFFFF", last_tnn=None, cfg=L) == 140)
    check("get_flush_len weiß->violett -> 160", get_flush_len("0FFFFFF", "0ce58f8", cfg=L) == 160)
    # cal_flush_list (live "length: [...]")
    check("cal_flush_list(160) = [90, 70] (live)", cal_flush_list(160, L) == [90, 70])
    check("cal_flush_list(229.17) = [90, 70, 70] (live)", cal_flush_list(229.16666666666669, L) == [90, 70, 70])
    r = cal_flush_list(247.08333333333334, L)
    check("cal_flush_list(247.08) = [90, 70, 87.08] (live)", r[:2] == [90, 70] and abs(r[2] - 87.08333333333334) < 1e-9 and len(r) == 3)
    r = cal_flush_list(309.1666666666667, L)
    check("cal_flush_list(309.17) = [90, 70, 70, 79.17] (live)", r[:3] == [90, 70, 70] and abs(r[3] - 79.16666666666669) < 1e-9)
    check("cal_flush_list(95) = [95]", cal_flush_list(95, L) == [95])
    check("cal_flush_list(50) = [90]", cal_flush_list(50, L) == [90])
    check("cal_flush_list(150) = [90, 70]", cal_flush_list(150, L) == [90, 70])
    check("cal_flush_list(200) = [100, 100]", cal_flush_list(200, L) == [100, 100])
    # Geschwindigkeit (live "flush_velocity: next=… base=… feed=…")
    v, p = flush_velocity(23)
    check("velocity 23 mm³/s -> 360 (live)", v == [360] and p == [1])
    v, _ = flush_velocity(14)
    check("velocity 14 mm³/s -> 350.00000000000006 (live)", v == [350.00000000000006])
    v, _ = flush_velocity(None)
    check("velocity ohne DB -> 360", v == [360.0])
    v, _ = flush_velocity(23, 0.6, 0.4)
    check("velocity 0.6er Düse -> 540", abs(v[0] - 540) < 1e-9)
    # Temperatur + Abschnittsplan (live 18@360 / 72@432 / 14@360 / 56@360, 235/240/220)
    t, mt = flush_temps(220, 220, 240, 240)
    check("flush_temps PLA->PLA = (220, 240)", (t, mt) == (220, 240))
    check("flush_temps kalt->heiß ohne Max-Spülung", flush_temps(220, 250, 240, 270) == (250, 250))
    segs, plan = flush_plan(160, [360.0], 220, 240, L)
    s0, s1 = plan[0][1], plan[1][1]
    check("Abschnitt 1: WAIT 235, M104 240, 18@360, 72@432 (live)",
          s0 == [("WAIT", 235), ("M104", 240), ("E", 18.0, 360.0), ("E", 72.0, 432.0)])
    check("Abschnitt 2: M104 220, 14@360, 56@360 (live)",
          s1 == [("M104", 220), ("E", 14.0, 360.0), ("E", 56.0, 360.0)])
    check("Wischen: Zwischenabschnitt ohne, letzter mit Kreis",
          plan[0][2].endswith("False)") and plan[1][2].endswith("True)"))
    segs, plan = flush_plan(309.1666666666667, [360.0], 220, 240, L)
    check("4 Abschnitte: Index 0,1,2,3 -> Faktor 1.2,1.2,1.2,1",
          [st[-1][2] for _, st, _ in plan] == [432.0, 432.0, 432.0, 360.0])
    check("Düse verstopft bei Messrad-Diff 0.0016 (live key845)", nozzle_blocked(-0.0015773773193359375))
    check("keine Verstopfung bei 82.9 mm", not nozzle_blocked(-82.9169921875))
    check("Matrix-Pfad 2x2 [0][1]", abs(gcode_matrix_flush_len([0, 240, 120, 0], 0, 1) - 100.0) < 1e-9)
    check("Matrix-Pfad Formfehler -> None", gcode_matrix_flush_len([1, 2, 3], 0, 1) is None)
    # G-Code
    g = gcode_nozzle_clean(L, circle=True, offset=1.0, y_pos=378.0)
    check("nozzle_clean: X170 -> X135, Kreis 161/169, 3x Schütteln, raus",
          g[2:4] == ["G0 X170.00 F12000.00", "G0 X135.00 F12000.00"]
          and g[4] == "G0 X161.00 Y374.00 F12000" and g[6] == "G0 X169.00 Y374.00 F10000"
          and g.count("G0 X160.00 Y378.00 F12000.00") == 3
          and g[-4:-2] == ["G0 X160.00 F12000.00", "G0 Y345.00 F12000.00"])
    g = gcode_nozzle_clean(L, circle=False, y_pos=378.0)
    check("nozzle_clean(circle=False): kein G2/G3, bleibt im Schacht",
          not any(x.startswith(("G2", "G3")) for x in g) and "G0 Y345.00 F12000.00" not in g)
    check("gcode_extrude", gcode_extrude(18.0, 360.0) == ["G91", "G0 E18.00 F360.00", "G90", "M400"])

    print("SELBSTTEST flush: %s %d/%d" % ("ok" if fails == 0 else "FEHLER", total - fails, total))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
