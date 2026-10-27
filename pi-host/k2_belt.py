#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_belt.py – Klipper-Zusatzmodul [k2_belt] für die Riemenspann-Module
mdlx/mdly des Creality K2 Plus.

Offener Port von Crealitys ``klippy/extras/belt_mdl.py`` (Klartext-Python,
621 Zeilen, GPLv3, live identisch zu Image 1.1.3.13; lokal als
``belt_mdl-live.py``) für Upstream-Klipper auf einem Pi-Host. Gleiche
Stilregeln wie ``k2_box.py``/``k2_motor_init.py``: reine Logik ohne Klipper
testbar (``K2BeltController``), dünne Klipper-Hülle (``PrinterK2Belt``),
Port-Zugriffe nur über einen injizierten, GETEILTEN RS485-Bus.

Grundlagen (nur gelesen, nichts gesendet):
- belt_mdl-live.py (Original: COM/STA/MDL/BELT_MDL, Rechenwege, Regelung,
  Fehler key712–key721, G-Codes BELT_MDL_INFO/MOVE/SET/CALI/TEST,
  Webhook ``belt_mdl_test``)
- Protokollanalyse RS485 (Frame ``[F7][addr][len][00][fun][daten][crc]``,
  CRC-8/ATM über len..daten, Adressen 0x21 = mdlx / 0x22 = mdly, fun
  0/2/4/6/8, 32-bit big-endian), §3.1/§6.2 (Belt ``retries = 4`` ⇒ 5
  Sendungen; Motor-0x05 ``[01]/[02]`` an X/Y bei BELT_TENSION, retries=0,
  nie beantwortet; ein gemeinsamer Port ttyS5 für Motoren + CFS + Belt)
- Live-Log 2026-10-12:
  vollständige BELT_TENSION-Läufe mdlx/mdly inkl. Requests, Antworten,
  Steigung/Achsabschnitt, Kraftwerten und Flash-Schreib-Echo
- Stock-printer.cfg des Druckers ([belt_mdl mdlx/mdly]:
  ch_max_dis 5, ch_best_n 140, ch_best_error 0.15) und gcode_macro.cfg
  (Makro BELT_TENSION: BELT_MDL_INFO + BELT_MDL_SET MDL_N=140 je Achse)
- pi-host/k2_transport.py (build_frame/parse_frame, nachbau-Pfadsuche),
  pi-host/k2_box.py (Bus-Teilung: printer-Objekt ``k2_485``),
  nachbau/serial485.py (``transact_full`` mit Lock über die ganze
  Transaktion, ``retries`` = Wiederholungen nach der Erstsendung)
- Upstream-Klipper: gcode
  ``register_mux_command``/``run_script_from_command``/``respond_info``,
  ``webhooks.register_mux_endpoint``, ``reactor.pause(waketime)``,
  ``printer.command_error``, ``force_move`` (FORCE_MOVE nur mit
  ``enable_force_move: True``)

Busablauf je Kommando (wie Original ``send_data``: Wire ``hex_data[1:-1]``
= ``[addr][len][00][fun][daten]``, CRC ergänzt der Treiber, Timeout 1 s,
retries = 4 laut Live-Log):

  fun 0  Version lesen        Antwort [hal(2)][soft(2)]
  fun 2  Flash lesen [03]     Antwort [03][place 4][idl_adc 4][full_adc 4]
  fun 4  Flash schreiben      [03][place][idl][full] → Echo (SCHREIBEND)
  fun 6  ADC lesen            Antwort [adc 4] (int32 BE, vorzeichenerweiterter
                              24-Bit-Wert des CS1237 – SIGNED)
  fun 8  Schieber bewegen     [dir][dist 4] → Echo SOFORT (vor der Fahrt),
                              danach max(dist*0,0132 s, dist*6,55 ms +
                              move_settle) warten (MECHANISCH)

Analyse der Belt-Firmware 18.10.2026, hier umgesetzt:
- ADC (fun 6) und idl/full (fun 2/4) SIGNED (int32, 24-Bit-Bereich) statt
  unsigned wie Stock → lockerer Riemen ergibt kleine/negative Kraft statt
  ~3·10⁵ N + key716/717 (§6.1). Sentinel 0xFFFFFFFF wird VOR der Umdeutung
  geprüft.
- Messfrische (§4, CS1237 ≈ 10 Hz, kein Mitteln): nach fun 8 Fahrdauer +
  ``move_settle`` warten, dann ``measure_discard`` Messungen verwerfen
  (je ``measure_interval`` Abstand).
- Busruhe (§2: Frame-Ende = 3 ms Ruhe): vor jedem Belt-Request
  ``bus_gap`` (5 ms) Pause, wiederholt, falls der Bus währenddessen aktiv war.
- Eigener Sendeschutz (§3/§7): fun 2 nur ``[03]``, fun 4 nur ``[03]+12 B``,
  fun 8 nur ``dir 0/1`` und ``1 <= dist <= max_steps_units`` (<= 0xFFFE).

Kraft in N: Gerade durch (idl_adc, cali_n1) und (full_adc, cali_n2) –
Original ``np.polyfit(x, y, 1)`` mit 2 Punkten = exakte Zweipunktgerade.
Hier reines Python (kein numpy), Abweichung zum Original < 1e-9 N
(Test gegen Original und Live-Werte).

Sicherheit (Auftrag):
- Lesen (fun 0/2/6) ist immer erlaubt.
- Flash schreiben (fun 4) nur mit ``allow_write: True``.
- Mechanik (fun 8, Rüttel-Zyklus mit FORCE_MOVE/M84, Motor-0x05) nur mit
  ``allow_move: True``. KTWO_BELT_MOVE/KTWO_BELT_SET brauchen BEIDES (sie
  schreiben am Ende den Flash). Ohne Freigabe bzw. mit ``DRY_RUN=1``: Plan
  ausgeben (inkl. der Frames, die gesendet würden), nichts senden.
- Fehler sind ``command_error`` (Klipper bleibt betriebsbereit, kein
  Shutdown); Texte deutsch, Stock-Code (key712…key721) in Klammern.

G-Codes (Mux-Key ``MDL_NAME`` wie Original; Stock-Namen optional per
``stock_aliases: True``):
- ``KTWO_BELT_INFO MDL_NAME=…``  (BELT_MDL_INFO)  Version/Flash/ADC/Kraft
- ``KTWO_BELT_TEST MDL_NAME=…``  (BELT_MDL_TEST)  wie INFO + Bereichsprüfung
- ``KTWO_BELT_MOVE MDL_NAME=… POS=<abs> [DRY_RUN=1]``  (BELT_MDL_MOVE)
- ``KTWO_BELT_SET MDL_NAME=… [MDL_N=<N>] [DRY_RUN=1]`` (BELT_MDL_SET)
- ``KTWO_BELT_CALI MDL_NAME=… DIR=0|1|2 [TEN=<N>] [DRY_RUN=1]`` (BELT_MDL_CALI)
Webhook: ``k2_belt_test`` (Mux ``MDL_NAME``), Alias ``belt_mdl_test``.

Konsole (23.10.2026, k2_konsole): Start-Meldungen (RS485-Bus fehlt beim
Connect, Webhook nicht registriert) als ``// [k2_belt <name>] …`` kurz nach
„ready“ – vorher gingen sie beim Start verloren. Alles andere läuft im
Befehl (Fehler = G-Code-Fehler ``!! …``).

Ohne Klipper importierbar (Tests: ``python3 test_k2_belt.py``).
Python-3.9-kompatibel.
"""

import time

# Import-Regel (Fix 16.10.2026, s. k2_transport.py): eigener Ordner NIE in
# sys.path (verdeckte unter Klipper Standardmodule wie "statistics");
# k2_transport relativ (Klipper-Paket extras), Fallback absolut (Tests).
# k2_belt braucht nachbau/ nicht direkt (nur k2_transport, das es anhängt).
try:
    from . import k2_transport as _K2T
except ImportError:
    try:
        import k2_transport as _K2T
    except ImportError:  # pragma: no cover – Einzel-Deployment ohne k2_transport
        _K2T = None
try:
    from . import k2_konsole as _K2K
except ImportError:
    import k2_konsole as _K2K

# =====================================================================
# Frame-Helfer (bevorzugt aus k2_transport/serial485, sonst lokal)
# =====================================================================

PACK_HEAD = 0xF7
STATUS_REQ = 0x00     # Original COM.star = 0x00
POLY = 0x07


def _crc8_local(data):
    """CRC-8/ATM (poly 0x07, init 0x00) – byte-identisch zu serial485.crc8_cal."""
    crc = 0
    for byte in bytes(data):
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ POLY) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def _build_frame_local(addr, fun, data=(), status=STATUS_REQ):
    data = list(data)
    body = [len(data) + 3, status, fun] + data
    return bytes([PACK_HEAD, addr] + body + [_crc8_local(body)])


if _K2T is not None:
    build_frame = _K2T.build_frame
    crc8_cal = _K2T.crc8_cal
else:  # pragma: no cover – Einzel-Deployment ohne k2_transport
    build_frame = _build_frame_local
    crc8_cal = _crc8_local

# =====================================================================
# Protokoll-Konstanten (Beleg: belt_mdl-live.py STA/COM, Protokollanalyse)
# =====================================================================

FUN_READ_VERSION = 0x00
FUN_READ_FLASH = 0x02
FUN_WRITE_FLASH = 0x04
FUN_READ_ADC = 0x06
FUN_MOVE_SLIDER = 0x08
FUN_NAMES = {
    FUN_READ_VERSION: "Version lesen",
    FUN_READ_FLASH: "Flash lesen",
    FUN_WRITE_FLASH: "Flash schreiben",
    FUN_READ_ADC: "ADC lesen",
    FUN_MOVE_SLIDER: "Schieber bewegen",
}
ADDR_MDLX = 0x21
ADDR_MDLY = 0x22
VALID_ADDRS = (ADDR_MDLX, ADDR_MDLY)

# Original: cmd_send_data_with_response(buf, 1) → 1 s; Live-Log „retries = 4“
# bei jedem Belt-Request (= 1 Erstsendung + 4 Wiederholungen, §6.2).
BUS_TIMEOUT = 1.0
BUS_RETRIES = 4

FLASH_NUM = 3                  # Original: Get_flash(3) / write_flash_buf(3, …)
UNCALIBRATED = 0xFFFFFFFF      # leerer Flash = nicht kalibriert (key714/715)
UNCAL_SIGNED = -1              # dasselbe Bitmuster als int32 (idl/full)
MOVE_LIMIT = 0xFFFF            # Original set_move: 0 <= dist < 0xffff
MOVE_HARD_MAX = 0xFFFE         # FW: nur 1 <= dist <= 0xFFFE
# Obergrenze der Option max_steps_units = Sendeschutz serial485
# TX_BELT_MOVE_MAX (50); mehr würde zur Laufzeit am Sendeschutz scheitern.
MOVE_CFG_MAX = 50
MAX_STEPS_DEFAULT = 50         # ≤ 50 Einheiten je fun 8 (live max. 12)
MOVE_TIME_PER_UNIT = 0.0132    # Original: delaytime = rang_num * 0.0132 s
# FW: Zähler = (dist << 16) / 500 Flanken, ISR 20 kHz
# → 131,072 Flanken × 50 µs = 6,5536 ms je Einheit.
MOVE_TIME_PER_UNIT_FW = 65536.0 / 500.0 / 20000.0
MOVE_SETTLE_DEFAULT = 0.1      # ≥ 1 CS1237-Wandlung (10 Hz, ANNAHME Datenblatt)
MEASURE_DISCARD_DEFAULT = 1    # erste Messung nach einer Fahrt verwerfen
MEASURE_INTERVAL_DEFAULT = 0.11  # Abstand > 1 Wandlungsperiode (100 ms)
BUS_GAP_DEFAULT = 0.005        # FW trennt Frames nach 3 ms Ruhe (+Reserve)
BUS_GAP_FW_MIN = 0.003
BUS_GAP_MAX_TRIES = 20
# CS1237: signed 24 Bit, auf int32 erweitert (FW @0x289C).
ADC_MIN = -(1 << 23)
ADC_MAX = (1 << 23) - 1
TENSION_LOOSE = 50.0           # Hinweis „Riemen locker“ unterhalb (nur Meldung)
TENSION_MAX = 600.0            # Original adc_to_num: >600 oder <-300 → key716
TENSION_MIN = -300.0
CALI_N1_DEFAULT = 140.0        # Original MDL.adjustnum1
CALI_N2_DEFAULT = 160.0        # Original MDL.adjustnum2
CONTROL_ERROR_DEFAULT = 0.02   # Original MDL.mistake (Regeltoleranz ±2 %)
MAX_ITERATIONS = 200           # Original run_tension: > 200 → Abbruch
CYCLES_DEFAULT = 2             # Original set_tension: for i in range(2)

# Motor-0x05 (Original: MOTOR_CONTROL NUM=n DATA=d → 0x80+n fun 0x05 [d]),
# live retries=0, ≈50 ms bis „timeout“, nie beantwortet (§3.1).
MOTOR_CONTROL_FUN = 0x05
MOTOR_CONTROL_TIMEOUT = 0.05
MOTOR_CONTROL_RETRIES = 0
MOTOR_BASE_ADDR = 0x80

# Stock-Reihenfolge des Rüttel-Zyklus (Original set_tension, 1:1). Zeilen
# mit ``@`` sind KEIN G-Code, sondern werden intern ausgeführt:
#   @WAIT <s>                       → reactor.pause (Original: 0,3 s)
#   @MOTOR_CONTROL NUM=<n> DATA=<d> → Motor-0x05 über den geteilten Bus
STOCK_CYCLE_GCODE = (
    "RESET_HOME_AXES_XY",
    "MOTOR_CONTROL NUM=1 DATA=2",
    "MOTOR_CONTROL NUM=2 DATA=2",
    "@WAIT 0.3",
    "FORCE_MOVE STEPPER=stepper_x DISTANCE=-200 VELOCITY=60",
    "FORCE_MOVE STEPPER=stepper_y DISTANCE=200 VELOCITY=60",
    "M400",
    "FORCE_MOVE STEPPER=stepper_x DISTANCE=200 VELOCITY=60",
    "FORCE_MOVE STEPPER=stepper_y DISTANCE=-200 VELOCITY=60",
    "M400",
    "MOTOR_CONTROL NUM=1 DATA=1",
    "MOTOR_CONTROL NUM=2 DATA=1",
    "M84",
)
# Default für Upstream: MOTOR_CONTROL (Creality-.so) intern als Motor-0x05,
# RESET_HOME_AXES_XY entfällt (ANNAHME: gibt es upstream nicht; das Makro
# BELT_TENSION hat vorher schon M84 gesendet, und das M84 am Zyklusende
# löscht den Homing-Zustand ohnehin).
DEFAULT_CYCLE_GCODE = (
    "@MOTOR_CONTROL NUM=1 DATA=2",
    "@MOTOR_CONTROL NUM=2 DATA=2",
    "@WAIT 0.3",
    "FORCE_MOVE STEPPER=stepper_x DISTANCE=-200 VELOCITY=60",
    "FORCE_MOVE STEPPER=stepper_y DISTANCE=200 VELOCITY=60",
    "M400",
    "FORCE_MOVE STEPPER=stepper_x DISTANCE=200 VELOCITY=60",
    "FORCE_MOVE STEPPER=stepper_y DISTANCE=-200 VELOCITY=60",
    "M400",
    "@MOTOR_CONTROL NUM=1 DATA=1",
    "@MOTOR_CONTROL NUM=2 DATA=1",
    "M84",
)

# Stock-Fehlercodes (mdlx, mdly) – nur als Referenz in den Meldungen.
STOCK_KEYS = {
    "comm": (712, 713),       # 485-Kommunikation
    "uncal": (714, 715),      # Flash 0xFFFFFFFF = nicht kalibriert
    "tension": (716, 717),    # Kraft > 600 N oder < -300 N
    "timeout": (718, 719),    # Regelung > 200 Schritte
    "cal_zero": (720, 721),   # Kalibrierwert 0
}

OPTION_NAMES = ("ch_max_dis", "ch_best_n", "ch_best_error", "addr",
                "allow_write", "allow_move", "stock_aliases", "verbose",
                "control_error", "cali_n1", "cali_n2", "cycles",
                "cycle_gcode", "bus_object", "bus_timeout",
                "max_steps_units", "move_settle", "measure_discard",
                "measure_interval", "bus_gap")


# =====================================================================
# Reine Helfer (1:1 zum Original, ohne numpy)
# =====================================================================

def split_to_bytes(data):
    """32-bit-Wert → 4 Bytes big-endian (Original split_to_bytes)."""
    data = int(data) & 0xFFFFFFFF
    return [((data >> (8 * i)) & 0xFF) for i in range(4)][::-1]


def bytes_to_int(byte_array):
    """4 Bytes big-endian → unsigned 32 bit (Original bytes_to_int)."""
    b = bytes(byte_array)
    if len(b) != 4:
        raise ValueError("bytes_to_int braucht genau 4 Bytes, nicht %d" % len(b))
    return (b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3]


def to_int32(value):
    """Unsigned 32 bit → signed int32 (Zweierkomplement)."""
    value = int(value) & 0xFFFFFFFF
    return value - (1 << 32) if value & 0x80000000 else value


def bytes_to_int32(byte_array):
    """4 Bytes big-endian → signed int32 (FW-Semantik für ADC/idl/full)."""
    return to_int32(bytes_to_int(byte_array))


def is_int24(value):
    """True, wenn ``value`` ein vorzeichenerweiterter 24-Bit-Wert ist, d. h.
    das oberste Byte ist 0x00 (Bit 23 = 0) bzw. 0xFF (Bit 23 = 1) – genau
    das Layout der FW (``>> 3`` + ``| 0xFF000000`` bei Bit 23, @0x289C)."""
    return ADC_MIN <= int(value) <= ADC_MAX


def move_wait_time(dist, settle=MOVE_SETTLE_DEFAULT):
    """Wartezeit nach fun 8: max(Stock dist·13,2 ms, FW-Fahrdauer dist·6,55 ms
    + settle). settle = 0 → exakt Stock (Faktor ≈ 2 Reserve bleibt immer)."""
    dist = int(dist)
    return max(dist * MOVE_TIME_PER_UNIT,
               dist * MOVE_TIME_PER_UNIT_FW + float(settle))


def request_wire(addr, fun, data=()):
    """Wire-Bytes wie Original ``hex_data[1:-1]``: [addr][len][00][fun][daten]."""
    data = list(data)
    return bytes([addr, len(data) + 3, STATUS_REQ, fun] + data)


def frame_hex(addr, fun, data=()):
    """Vollständiger Sende-Frame (inkl. F7 + CRC) als Hex-Text für Pläne."""
    return bytes(build_frame(addr, fun, list(data), status=STATUS_REQ)).hex(" ")


def parse_response(raw, addr):
    """Antwort-Vollframe prüfen wie Original ``recv_sensor_data`` (+ CRC).

    Gibt ``(fun, daten)`` zurück; Daten = ``raw[5 : len-3+5]``. Fehler →
    ValueError (Original lieferte stattdessen ``(0, -n, 0)`` und brach
    still ab).
    """
    raw = bytes(raw or b"")
    if len(raw) < 6:
        raise ValueError("Antwort zu kurz (%d Bytes)" % len(raw))
    if raw[0] != PACK_HEAD:
        raise ValueError("falscher Frame-Kopf 0x%02X" % raw[0])
    if raw[1] != addr:
        raise ValueError("Antwort von 0x%02X statt 0x%02X" % (raw[1], addr))
    length = raw[2]
    if length < 3 or len(raw) < length + 3:
        raise ValueError("Längenfeld %d passt nicht zu %d Bytes" % (length, len(raw)))
    if crc8_cal(raw[2:length + 2]) != raw[length + 2]:
        raise ValueError("CRC-Fehler")
    return raw[4], bytes(raw[5:length - 3 + 5])


def fit_line(idl_adc, full_adc, n1, n2):
    """Zweipunktgerade (Original ``np.polyfit([idl, full], [n1, n2], 1)``).

    Gibt ``(slope, intercept)`` zurück. idl == full ist singulär (numpy
    lieferte dann eine Warnung + Unsinn) → ValueError.
    """
    dx = float(full_adc) - float(idl_adc)
    if dx == 0.0:
        raise ValueError("Kalibrierwerte identisch (idl_adc == full_adc)")
    slope = (float(n2) - float(n1)) / dx
    intercept = float(n1) - slope * float(idl_adc)
    return slope, intercept


def adc_to_newton(adc, idl_adc, n1, slope):
    """ADC → Kraft in N. Numerisch stabile Form ``n1 + (adc-idl)*slope``
    (mathematisch identisch zu ``adc*slope + intercept`` des Originals; die
    Differenz ist Gleitkomma-Rundung < 1e-9 N, s. Test)."""
    return float(n1) + (float(adc) - float(idl_adc)) * slope


def tension_step(movetimes, target, current):
    """Schrittweite des Regelschritts ``movetimes`` (Original run_tension,
    Zeile für Zeile übernommen – inkl. der Stock-Kommentare sinngemäß).

    Große Abweichung → großer Schritt (max. 12), Schritt fällt mit der
    Schrittzahl, ab Schritt 20/40/50/70/110 feste Feinschritte 4/3/2/1
    (gegen Spindel-Nichtlinearität/Schwingen).
    """
    aimpull = abs(target - current)
    aimpull = 10 if aimpull > 10 else aimpull
    aimmove = int(aimpull) + 2
    if aimmove > movetimes:
        aimmove = aimmove - movetimes
    elif movetimes > 110:
        aimmove = 1
    elif movetimes > 70:
        aimmove = 2
    elif movetimes > 50:
        aimmove = 3
    elif movetimes > 40:
        aimmove = 4
    elif movetimes > 20:
        if aimmove > (movetimes - 20):
            aimmove = aimmove + 20 - movetimes
    return aimmove


def default_addr_for(name):
    """Original config_addr: 0x22 wenn Name 'mdly', sonst 0x21."""
    return ADDR_MDLY if name == "mdly" else ADDR_MDLX


def parse_cycle_lines(text_or_lines):
    """Zyklus-Text (mehrzeilig) → Liste ``(art, wert)``.

    art = 'gcode' (Zeile an Klipper), 'wait' (Sekunden),
    'motor' ((num, data) → Motor-0x05). Ungültige ``@``-Zeilen → ValueError.
    """
    if isinstance(text_or_lines, (list, tuple)):
        lines = list(text_or_lines)
    else:
        lines = str(text_or_lines).splitlines()
    out = []
    for raw in lines:
        line = raw.strip()
        if not line:
            continue
        if not line.startswith("@"):
            out.append(("gcode", line))
            continue
        parts = line[1:].split()
        cmd = parts[0].upper() if parts else ""
        if cmd == "WAIT":
            if len(parts) != 2:
                raise ValueError("@WAIT braucht genau eine Zahl: %r" % (line,))
            try:
                sec = float(parts[1])
            except ValueError:
                raise ValueError("@WAIT: keine Zahl: %r" % (line,))
            if not 0.0 <= sec <= 60.0:
                raise ValueError("@WAIT muss 0..60 s sein: %r" % (line,))
            out.append(("wait", sec))
        elif cmd == "MOTOR_CONTROL":
            kv = {}
            for p in parts[1:]:
                if "=" not in p:
                    raise ValueError("@MOTOR_CONTROL: Parameter %r ohne '='" % (p,))
                k, v = p.split("=", 1)
                kv[k.upper()] = v
            try:
                num = int(kv.get("NUM", ""))
                data = int(kv.get("DATA", ""))
            except ValueError:
                raise ValueError("@MOTOR_CONTROL braucht NUM=<1..4> DATA=<1..12>: %r"
                                 % (line,))
            if not 1 <= num <= 4 or not 1 <= data <= 12:
                raise ValueError("@MOTOR_CONTROL: NUM 1..4 (X/Y/Z/Z1), DATA 1..12: %r"
                                 % (line,))
            out.append(("motor", (num, data)))
        else:
            raise ValueError("unbekannte @-Anweisung %r (erlaubt: @WAIT, "
                             "@MOTOR_CONTROL)" % (line,))
    return out


# =====================================================================
# Config (rein, testbar)
# =====================================================================

def parse_options(opts, name):
    """Dict (Werte Strings oder Zahlen) → strukturierte Config.

    Stock-Optionen mit gleichen Grenzen wie das Original:
    ch_max_dis (int 0..10, Default 5, im Original ungenutzt),
    ch_best_n (int 0..300, Default 140), ch_best_error (0.001..1, Default 0.05;
    Stock-printer.cfg setzt 0.15). Neue Optionen s. README.
    """
    o = {}
    for k, v in dict(opts).items():
        if v is None:
            continue
        o[str(k).strip().lower()] = v

    def _num(key, default, conv, lo=None, hi=None):
        raw = o.get(key, default)
        try:
            val = conv(str(raw).strip()) if isinstance(raw, str) else conv(raw)
        except (TypeError, ValueError):
            raise ValueError("%s muss eine Zahl sein: %r" % (key, raw))
        if lo is not None and val < lo:
            raise ValueError("%s muss >= %s sein: %r" % (key, lo, raw))
        if hi is not None and val > hi:
            raise ValueError("%s muss <= %s sein: %r" % (key, hi, raw))
        return val

    def _int_auto(s):
        return int(str(s).strip(), 0)

    def _bool(key, default):
        raw = o.get(key, default)
        if isinstance(raw, bool):
            return raw
        v = str(raw).strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off"):
            return False
        raise ValueError("%s muss True/False sein: %r" % (key, raw))

    cfg = {
        "name": name,
        "ch_max_dis": _num("ch_max_dis", 5, int, 0, 10),
        "ch_best_n": _num("ch_best_n", 140, int, 0, 300),
        "ch_best_error": _num("ch_best_error", 0.05, float, 0.001, 1.0),
        "addr": _num("addr", default_addr_for(name), _int_auto),
        "allow_write": _bool("allow_write", False),
        "allow_move": _bool("allow_move", False),
        "stock_aliases": _bool("stock_aliases", False),
        "verbose": _bool("verbose", False),
        "control_error": _num("control_error", CONTROL_ERROR_DEFAULT, float, 0.001, 0.5),
        "cali_n1": _num("cali_n1", CALI_N1_DEFAULT, float, 0.0, 300.0),
        "cali_n2": _num("cali_n2", CALI_N2_DEFAULT, float, 0.0, 300.0),
        "cycles": _num("cycles", CYCLES_DEFAULT, int, 0, 5),
        "bus_object": str(o.get("bus_object", "") or "").strip(),
        "bus_timeout": _num("bus_timeout", BUS_TIMEOUT, float, 0.001, 5.0),
        # Belt-FW-Analyse 18.10.2026 (s. Modul-Docstring/README §4a)
        "max_steps_units": _num("max_steps_units", MAX_STEPS_DEFAULT, _int_auto,
                                1, MOVE_CFG_MAX),
        "move_settle": _num("move_settle", MOVE_SETTLE_DEFAULT, float, 0.0, 2.0),
        "measure_discard": _num("measure_discard", MEASURE_DISCARD_DEFAULT, int, 0, 5),
        "measure_interval": _num("measure_interval", MEASURE_INTERVAL_DEFAULT, float,
                                 0.0, 2.0),
        "bus_gap": _num("bus_gap", BUS_GAP_DEFAULT, float, 0.0, 0.1),
    }
    if cfg["addr"] not in VALID_ADDRS:
        raise ValueError("addr muss 0x21 (mdlx) oder 0x22 (mdly) sein: 0x%02X"
                         % (cfg["addr"],))
    if cfg["cali_n1"] == cfg["cali_n2"]:
        raise ValueError("cali_n1 und cali_n2 dürfen nicht gleich sein")
    if 0.0 < cfg["bus_gap"] < BUS_GAP_FW_MIN:
        raise ValueError("bus_gap muss 0 (aus) oder >= %.3f s sein (Belt-FW trennt "
                         "Frames erst nach 3 ms Ruhe): %r" % (BUS_GAP_FW_MIN,
                                                               cfg["bus_gap"]))
    cyc = o.get("cycle_gcode")
    cfg["cycle_steps"] = parse_cycle_lines(cyc if cyc is not None
                                           else DEFAULT_CYCLE_GCODE)
    return cfg


# =====================================================================
# Controller – reine Logik (ohne Klipper importierbar)
# =====================================================================

class BeltError(Exception):
    """Fehler des Riemenspannmoduls (Klipper-Hülle → command_error)."""

    def __init__(self, msg, kind=None, key=None):
        Exception.__init__(self, msg)
        self.kind = kind
        self.key = key


class K2BeltController:
    """Ein Riemenspannmodul (mdlx oder mdly) am geteilten RS485-Bus.

    ``bus``: serial485-artig (``transact_full(addr, fun, data, timeout=,
    retries=)`` → ``(addr, status, fun, daten, roh)``; Fallback
    ``transact`` → Daten). DIESELBE Instanz wie [k2_motor]/[k2_box] – der
    Bus serialisiert jede Transaktion mit seinem Lock (Halbduplex). Das
    Modul öffnet den Port nie selbst.
    ``pause(s)``: relative Wartezeit (Klipper: reactor.pause(now + s)).
    ``respond(msg)``: Ausgabe (Klipper: gcode.respond_info).
    ``run_gcode(line)``: G-Code ausführen (Klipper: run_script_from_command).
    """

    def __init__(self, name, cfg, bus=None, pause=None, respond=None,
                 run_gcode=None):
        self.name = name
        self.cfg = dict(cfg)
        self.addr = self.cfg.get("addr", default_addr_for(name))
        self.bus = bus
        self.pause = pause if pause is not None else time.sleep
        self.respond = respond if respond is not None else print
        self.run_gcode = run_gcode
        # Zustand wie Original MDL (Startwerte identisch)
        self.hal_version = None
        self.soft_version = None
        self.flash_loaded = False
        self.current_place = 0
        # idl/full/ADC SIGNED (int32); -1 == Flash 0xFFFFFFFF
        self.idl_adc = UNCAL_SIGNED
        self.full_adc = UNCAL_SIGNED
        self.current_adc = None
        self.current_adc_raw = None
        self.current_tension = 0.0
        self.slope = 0.0
        self.intercept = 0.0
        self.fitted = False
        self.adjust1 = float(self.cfg.get("cali_n1", CALI_N1_DEFAULT))
        self.adjust2 = float(self.cfg.get("cali_n2", CALI_N2_DEFAULT))
        self.target_tension = float(self.cfg.get("ch_best_n", 140))
        self.target_error = float(self.cfg.get("ch_best_error", 0.05))
        self.error_result = None
        self.last_iterations = 0
        self.last_error = None

    # ------------------------------------------------------------------
    # Ausgabe/Fehler
    # ------------------------------------------------------------------
    def _say(self, msg):
        try:
            self.respond("[k2_belt %s] %s" % (self.name, msg))
        except Exception:
            pass

    def _debug(self, msg):
        if self.cfg.get("verbose"):
            self._say(msg)

    def _err(self, kind, text):
        keys = STOCK_KEYS.get(kind)
        key = None
        if keys:
            key = keys[1] if self.addr == ADDR_MDLY else keys[0]
        msg = "[k2_belt %s] %s" % (self.name, text)
        if key is not None:
            msg += " (Stock-Fehler key%d)" % key
        self.last_error = msg
        return BeltError(msg, kind=kind, key=key)

    # ------------------------------------------------------------------
    # Bus
    # ------------------------------------------------------------------
    def _xfer(self, fun, data=()):
        """Ein Belt-Request (Timeout 1 s, retries 4 wie Original/Live)."""
        if self.bus is None:
            raise self._err(None, "RS485-Bus nicht verdrahtet (erwartet printer-"
                                  "Objekt 'k2_485', geteilt mit [k2_motor]/[k2_box])")
        payload = [int(b) & 0xFF for b in data]
        self._check_tx(fun, payload)
        timeout = float(self.cfg.get("bus_timeout", BUS_TIMEOUT))
        self._debug("TX %s: %s" % (FUN_NAMES.get(fun, "fun 0x%02X" % fun),
                                   frame_hex(self.addr, fun, payload)))
        self._bus_quiet()
        try:
            full = getattr(self.bus, "transact_full", None)
            if full is not None:
                raddr, _rstatus, rfun, rdata, _raw = full(
                    self.addr, fun, payload, timeout=timeout, retries=BUS_RETRIES)
            else:
                res = self.bus.transact(self.addr, fun, payload, timeout=timeout,
                                        retries=BUS_RETRIES)
                if isinstance(res, tuple) and len(res) == 3:
                    _rstatus, rfun, rdata = res
                else:
                    rfun, rdata = fun, res
                raddr = self.addr
        except TimeoutError as exc:
            raise self._err("comm", "RS485-Kommunikation gestört: keine gültige "
                                    "Antwort von 0x%02X auf %s nach %d Sendungen (%s)"
                            % (self.addr, FUN_NAMES.get(fun, "fun 0x%02X" % fun),
                               BUS_RETRIES + 1, exc))
        except ValueError as exc:
            raise self._err("comm", "RS485-Fehler bei %s: %s"
                            % (FUN_NAMES.get(fun, "fun 0x%02X" % fun), exc))
        except (RuntimeError, OSError) as exc:
            # Bus-Objekt da, Port aber nicht nutzbar ([k2_485]: Port fehlt,
            # Batch-Modus, Adapter gezogen) -> G-Code-Fehler statt
            # "Internal error" + Klipper-Shutdown (Batch-Probe G 16.10.2026).
            raise self._err(None, "RS485-Bus nicht verfügbar bei %s: %s"
                            % (FUN_NAMES.get(fun, "fun 0x%02X" % fun), exc))
        if raddr != self.addr:
            raise self._err("comm", "Antwort von 0x%02X statt 0x%02X" % (raddr, self.addr))
        if rfun != fun:
            raise self._err("comm", "Antwort mit Funktionscode 0x%02X statt 0x%02X"
                            % (rfun, fun))
        return bytes(rdata)

    def _check_tx(self, fun, payload):
        """Eigener Sendeschutz für 0x21/0x22, zusätzlich zum
        Schutz in serial485/k2_transport. Verstoß → Fehler, NICHTS gesendet."""
        def bad(text):
            return self._err(None, "Sendeschutz k2_belt: %s an 0x%02X abgelehnt – "
                                   "nichts gesendet (%s)"
                             % (FUN_NAMES.get(fun, "fun 0x%02X" % fun), self.addr, text))
        if fun in (FUN_READ_VERSION, FUN_READ_ADC):
            if payload:
                raise bad("Daten müssen leer sein")
        elif fun == FUN_READ_FLASH:
            if payload != [FLASH_NUM]:
                raise bad("nur Daten [03] erlaubt (n > ~14 überschreibt RAM der FW)")
        elif fun == FUN_WRITE_FLASH:
            if not self.cfg.get("allow_write"):
                raise bad("allow_write fehlt")
            if len(payload) != 1 + 4 * FLASH_NUM or payload[0] != FLASH_NUM:
                raise bad("nur [03] + 12 Bytes erlaubt ([00]/n<3 löscht die "
                          "Kalibrierung)")
        elif fun == FUN_MOVE_SLIDER:
            if not self.cfg.get("allow_move"):
                raise bad("allow_move fehlt")
            if len(payload) != 5 or payload[0] not in (0, 1):
                raise bad("nur [dir 0/1][dist 4 B] erlaubt")
            dist = bytes_to_int(payload[1:5])
            limit = self._max_steps()
            if not 1 <= dist <= limit:
                raise bad("Weg %d außerhalb 1..%d (max_steps_units; FW hat keine "
                          "Endlage)" % (dist, limit))
        else:
            raise bad("Funktionscode der Belt-FW unbekannt (nur 0/2/4/6/8)")

    def _max_steps(self):
        return min(int(self.cfg.get("max_steps_units", MAX_STEPS_DEFAULT)),
                   MOVE_CFG_MAX)

    def _bus_quiet(self):
        """≥ ``bus_gap`` Busruhe vor einem Belt-Request.

        serial485 sendet direkt nach dem Lock (keine Mindestpause). Die Belt-
        FW trennt Frames nur über 3 ms Ruhe – kommt der Request früher nach
        einem Fremd-Frame, verschmelzen beide und der Belt verwirft ihn.
        Hier: Pause ``bus_gap``; hat sich die Bus-Statistik (sent/received/
        timeouts/crc) während der Pause geändert, war der Bus aktiv → erneut
        warten (max. BUS_GAP_MAX_TRIES). Unter Klipper (ein Reactor-Thread,
        blockierende Transaktionen) sendet nach der Pause niemand mehr
        dazwischen. Grenze: spontane Fremd-Frames ohne Host-Anfrage sieht
        die Statistik nicht."""
        gap = float(self.cfg.get("bus_gap", BUS_GAP_DEFAULT))
        if gap <= 0.0:
            return
        # k2_485-Objekt: bus_stats(); roher Bus (nachbau/serial485, Tests): stats()
        stats = getattr(self.bus, "bus_stats", None) or getattr(self.bus, "stats", None)

        def snap():
            if stats is None:
                return None
            try:
                st = stats() or {}
                return tuple(st.get(k) for k in ("sent", "received", "timeouts",
                                                 "crc_errors"))
            except Exception:
                return None
        for _ in range(BUS_GAP_MAX_TRIES):
            before = snap()
            self.pause(gap)
            if before is None or snap() == before:
                return
        self._debug("Busruhe: Bus nach %d × %.0f ms weiter aktiv – sende trotzdem"
                    % (BUS_GAP_MAX_TRIES, gap * 1000))

    # ------------------------------------------------------------------
    # Lesende Grundfunktionen (immer erlaubt)
    # ------------------------------------------------------------------
    def read_version(self):
        """fun 0 → Hardware-/Softwareversion (je 2 Bytes, Original Get_version)."""
        data = self._xfer(FUN_READ_VERSION)
        if len(data) < 4:
            raise self._err("comm", "Versionsantwort zu kurz (%d Bytes)" % len(data))
        self.hal_version = bytes(data[:2])
        self.soft_version = bytes(data[2:])
        self._say("Hardwareversion %s, Softwareversion %s"
                  % (self.hal_version.hex(), self.soft_version.hex()))
        return self.hal_version, self.soft_version

    def read_flash(self, require_calibrated=True):
        """fun 2 [03] → place/idl_adc/full_adc (Original get_flash_data).

        ``require_calibrated``: wie Original Fehler key714/715, wenn ein Wert
        0xFFFFFFFF ist (für CALI/MOVE-Vorbereitung abschaltbar)."""
        data = self._xfer(FUN_READ_FLASH, [FLASH_NUM])
        if len(data) < 13:
            raise self._err("comm", "Flash-Antwort zu kurz (%d statt 13 Bytes)" % len(data))
        self.current_place = bytes_to_int(data[1:5])
        # idl/full SIGNED; 0xFFFFFFFF → -1 = UNCAL_SIGNED,
        # is_uncalibrated() erkennt den Sentinel weiterhin.
        idl_raw = bytes_to_int(data[5:9])
        full_raw = bytes_to_int(data[9:13])
        self.idl_adc = to_int32(idl_raw)
        self.full_adc = to_int32(full_raw)
        self.flash_loaded = True
        self.fitted = False
        self._say("Flash: Position %d, idl_adc %d (0x%08X), full_adc %d (0x%08X)"
                  % (self.current_place, self.idl_adc, idl_raw, self.full_adc,
                     full_raw))
        if require_calibrated and self.is_uncalibrated():
            raise self._err("uncal", "Dehnmessstreifen nicht kalibriert (Flash-Wert "
                                     "0xFFFFFFFF) – erst KTWO_BELT_CALI DIR=1/2/0")
        return self.current_place, self.idl_adc, self.full_adc

    def is_uncalibrated(self):
        return (self.current_place == UNCALIBRATED
                or UNCAL_SIGNED in (self.idl_adc, self.full_adc)
                or UNCALIBRATED in (self.idl_adc, self.full_adc))

    def ensure_flash(self, require_calibrated=False):
        """Flash einmal lesen, falls noch nicht geschehen (nur lesend).

        Abweichung zum Original (Sicherheit): Original schrieb bei MOVE/CALI
        ohne vorheriges INFO die RAM-Startwerte (0xFFFFFFFF) in den Flash.
        """
        if not self.flash_loaded:
            self.read_flash(require_calibrated=require_calibrated)

    def read_adc(self):
        """fun 6 → ADC-Rohwert, SIGNED (FW: CS1237 24 Bit vorzeichenerweitert
        auf int32, big-endian). Abweichung Original: unsigned.

        Prüft das Byte-Layout: oberstes Byte muss die Vorzeichenerweiterung
        von Bit 23 sein (0x00/0xFF), sonst ist die Antwort kein FW-ADC-Wert.
        Vollausschlag (±2²³) = Übersteuerung/Sensor offen → Fehler."""
        data = self._xfer(FUN_READ_ADC)
        if len(data) < 4:
            raise self._err("comm", "ADC-Antwort zu kurz (%d Bytes)" % len(data))
        raw = bytes_to_int(data[:4])
        val = to_int32(raw)
        if not is_int24(val):
            raise self._err("adc", "ADC-Antwort 0x%08X ist kein 24-Bit-Wert (oberes "
                                   "Byte 0x%02X passt nicht zu Bit 23) – Übertragung "
                                   "oder Belt-Firmware prüfen" % (raw, raw >> 24))
        if val in (ADC_MIN, ADC_MAX):
            raise self._err("adc", "ADC am Anschlag (%d = Vollausschlag 24 Bit) – "
                                   "Dehnmessstreifen übersteuert oder getrennt" % val)
        self.current_adc_raw = raw
        self.current_adc = val
        self._debug("ADC %d (0x%08X)" % (val, raw))
        return val

    def measure_fresh(self):
        """Messung nach einer Fahrt: FW mittelt nicht, CS1237
        ≈ 10 Hz → fun 6 liefert den LETZTEN Wert (bis 100 ms alt, ggf. mit
        der Fahrt im Integrationsfenster). Die Wartezeit nach fun 8 enthält
        schon ``move_settle`` (≥ 1 Wandlung nach Fahrende); zusätzlich werden
        ``measure_discard`` Werte verworfen, je ``measure_interval`` (> 1
        Wandlungsperiode) Abstand – der benutzte Wert stammt dann aus einer
        Wandlung, die vollständig NACH dem Fahrende lag. discard = 0 →
        Stock-Verhalten (1 Messung direkt nach der Pause)."""
        n = int(self.cfg.get("measure_discard", MEASURE_DISCARD_DEFAULT))
        interval = float(self.cfg.get("measure_interval", MEASURE_INTERVAL_DEFAULT))
        for _ in range(n):
            old = self.read_adc()
            self._debug("ADC %d verworfen (Messfrische nach Fahrt)" % old)
            self.pause(interval)
        self.read_adc()
        return self.adc_to_tension()

    # ------------------------------------------------------------------
    # Rechenwege
    # ------------------------------------------------------------------
    def init_fit(self):
        """Geradenfit ADC → N (Original init_adc_to_num inkl. Prüfungen)."""
        if self.is_uncalibrated():
            raise self._err("uncal", "Dehnmessstreifen nicht kalibriert (Flash-Wert "
                                     "0xFFFFFFFF) – erst KTWO_BELT_CALI DIR=1/2/0")
        if self.idl_adc == 0 or self.full_adc == 0:
            raise self._err("cal_zero", "Kalibrierwert des Dehnmessstreifens ist 0")
        for nm, v in (("idl_adc", self.idl_adc), ("full_adc", self.full_adc)):
            if not is_int24(v):
                raise self._err("cal_zero", "Kalibrierwert %s = %d liegt außerhalb des "
                                            "24-Bit-ADC-Bereichs – Flash-Inhalt "
                                            "unbrauchbar, neu kalibrieren" % (nm, v))
        try:
            self.slope, self.intercept = fit_line(self.idl_adc, self.full_adc,
                                                  self.adjust1, self.adjust2)
        except ValueError as exc:
            raise self._err("cal_zero", "Kalibrierung unbrauchbar: %s" % exc)
        self.fitted = True
        self._debug("Steigung %r, Achsabschnitt %r" % (self.slope, self.intercept))
        return self.slope, self.intercept

    def adc_to_tension(self, adc=None):
        """ADC → N (Original adc_to_num), mit Plausibilitätsgrenzen."""
        if not self.fitted:
            return None  # Original: erst init_adc_to_num nötig
        adc = self.current_adc if adc is None else adc
        if adc is None:
            return None
        val = adc_to_newton(adc, self.idl_adc, self.adjust1, self.slope)
        self.current_tension = val
        self._debug("Kraft %.2f N" % val)
        if val > TENSION_MAX or val < TENSION_MIN:
            # Mit signed-Auswertung nur noch echte Ausreißer (Stock-Grenzen);
            # die Phantomkraft ~3·10⁵ N bei lockerem Riemen gibt es nicht mehr.
            raise self._err("tension", "Riemenkraft unplausibel: %.1f N (erlaubt "
                                       "-300..600 N; ADC %d, idl %d, full %d) – "
                                       "Kalibrierung/Sensor prüfen"
                            % (val, adc, self.idl_adc, self.full_adc))
        return val

    def tension_hint(self, val=None):
        """Klartext-Hinweis für niedrige Kräfte (nur Meldung, kein Fehler)."""
        val = self.current_tension if val is None else val
        if val < 0.0:
            return ("Hinweis: Kraft %.1f N < 0 – Riemen entlastet/locker oder "
                    "Kalibrierung verschoben (kein Fehler)" % val)
        if val < TENSION_LOOSE:
            return "Hinweis: Riemen locker (%.1f N < %.0f N)" % (val, TENSION_LOOSE)
        return None

    def measure(self):
        """ADC lesen + umrechnen (Fit muss vorliegen, ohne Frische-Logik)."""
        self.read_adc()
        return self.adc_to_tension()

    # ------------------------------------------------------------------
    # Lesende Abläufe
    # ------------------------------------------------------------------
    def info(self):
        """KTWO_BELT_INFO = Original mdl_info_cmd: fun 0, fun 2, Fit, fun 6."""
        self.read_version()
        self.read_flash(require_calibrated=True)
        self.init_fit()
        self.read_adc()
        self.adc_to_tension()
        self._say("ADC %d → Riemenkraft %.2f N (Position %d)"
                  % (self.current_adc, self.current_tension, self.current_place))
        hint = self.tension_hint()
        if hint:
            self._say(hint)
        return self.snapshot()

    def test(self):
        """KTWO_BELT_TEST / Webhook = Original get_mdl_data.

        Rückgabe wie Original: ``{'current_tension': N, 'target_error': 0|1}``
        (0 = innerhalb ch_best_n·(1±ch_best_error), streng < und >)."""
        self.info()
        target_max = self.target_tension * (1 + self.target_error)
        target_min = self.target_tension * (1 - self.target_error)
        ok = target_min < self.current_tension < target_max
        self.error_result = 0 if ok else 1
        self._say("Prüfung: %.2f N, Sollbereich %.1f..%.1f N → %s"
                  % (self.current_tension, target_min, target_max,
                     "OK" if ok else "AUSSERHALB"))
        return {"current_tension": self.current_tension,
                "target_error": self.error_result}

    # ------------------------------------------------------------------
    # Freigaben
    # ------------------------------------------------------------------
    def _blocked(self, need_write, need_move, dry_run):
        """Grund (str), warum nicht ausgeführt wird, oder None."""
        if dry_run:
            return "DRY_RUN=1"
        missing = []
        if need_write and not self.cfg.get("allow_write"):
            missing.append("allow_write: True")
        if need_move and not self.cfg.get("allow_move"):
            missing.append("allow_move: True")
        if missing:
            return "Freigabe fehlt (%s)" % ", ".join(missing)
        return None

    def _plan_result(self, reason, plan):
        self._say("NICHTS gesendet – %s. Plan:" % reason)
        for line in plan:
            self._say("  " + line)
        return {"executed": False, "reason": reason, "plan": list(plan)}

    def _flash_payload(self, place=None, idl=None, full=None):
        place = self.current_place if place is None else place
        idl = self.idl_adc if idl is None else idl
        full = self.full_adc if full is None else full
        return ([FLASH_NUM] + split_to_bytes(place) + split_to_bytes(idl)
                + split_to_bytes(full))

    def _plan_flash(self, place=None, idl=None, full=None):
        payload = self._flash_payload(place, idl, full)
        return ("Flash schreiben (fun 4): Position %d, idl_adc %d, full_adc %d → %s"
                % (bytes_to_int(payload[1:5]), bytes_to_int32(payload[5:9]),
                   bytes_to_int32(payload[9:13]),
                   frame_hex(self.addr, FUN_WRITE_FLASH, payload)))

    def _plan_move(self, direction, dist):
        data = [direction] + split_to_bytes(dist)
        return ("Schieber bewegen (fun 8): Richtung %d, Weg %d, danach %.3f s warten → %s"
                % (direction, dist, self._move_wait(dist),
                   frame_hex(self.addr, FUN_MOVE_SLIDER, data)))

    # ------------------------------------------------------------------
    # Schreibende / bewegende Grundfunktionen (nur mit Freigabe)
    # ------------------------------------------------------------------
    def _require(self, need_write, need_move):
        reason = self._blocked(need_write, need_move, False)
        if reason is not None:
            raise self._err(None, "intern blockiert: %s" % reason)

    def _move_wait(self, dist):
        return move_wait_time(dist, self.cfg.get("move_settle", MOVE_SETTLE_DEFAULT))

    def write_flash(self):
        """fun 4 [03][place][idl][full] + Echo-Prüfung (Original write_flash).

        Abweichung: Original prüfte das Echo mit ODER und ignorierte das
        Ergebnis; hier müssen alle drei Werte übereinstimmen, sonst Fehler."""
        self._require(True, False)
        payload = self._flash_payload()
        data = self._xfer(FUN_WRITE_FLASH, payload)
        if len(data) < 13:
            raise self._err("comm", "Flash-Schreib-Echo zu kurz (%d Bytes)" % len(data))
        echo = (bytes_to_int(data[1:5]), bytes_to_int32(data[5:9]),
                bytes_to_int32(data[9:13]))
        want = (self.current_place, self.idl_adc, self.full_adc)
        if echo != want:
            raise self._err("comm", "Flash-Rücklese weicht ab: geschrieben %r, "
                                    "Echo %r" % (want, echo))
        self._say("Flash geschrieben: Position %d, idl_adc %d, full_adc %d" % want)
        return echo

    def slider_move(self, direction, dist):
        """fun 8 [dir][dist BE32], danach warten (Original set_move: dist·0,0132 s).

        Wartezeit = max(Stock dist·13,2 ms, FW-Fahrdauer dist·6,55 ms +
        move_settle) – die FW schickt das Echo VOR der Fahrt und kennt kein
        Busy-Flag (fun 8 während der Fahrt überschreibt den Restweg).
        Die Wartezeit läuft NACH der Bus-Transaktion – der Bus-Lock ist dann
        frei (andere Module dürfen in der Zeit senden, wie Stock).
        Weg: 1..max_steps_units (Default 50, hart <= 0xFFFE); dist 0 sendet
        die FW nur als Echo – hier abgelehnt."""
        self._require(False, True)
        if direction not in (0, 1):
            raise self._err(None, "Richtung muss 0 oder 1 sein: %r" % (direction,))
        limit = self._max_steps()
        if not 1 <= dist <= limit:
            raise self._err(None, "Schieberweg %d außerhalb 1..%d (max_steps_units)"
                            % (dist, limit))
        data = self._xfer(FUN_MOVE_SLIDER, [direction] + split_to_bytes(dist))
        self.pause(self._move_wait(dist))
        if len(data) < 5:
            raise self._err("comm", "Bewegungs-Echo zu kurz (%d Bytes)" % len(data))
        echo = bytes_to_int(data[1:5])
        if echo != dist:
            raise self._err("comm", "Bewegungs-Echo %d statt %d" % (echo, dist))
        self._debug("Schieber %s %d" % ("+" if direction == 1 else "-", dist))
        return echo

    def motor_control(self, num, data):
        """Motor-0x05 an 0x80+NUM (Original: MOTOR_CONTROL NUM= DATA=).

        retries=0, 50 ms; live nie beantwortet → Timeout ist der Normalfall."""
        self._require(False, True)
        if self.bus is None:
            raise self._err(None, "RS485-Bus nicht verdrahtet")
        addr = MOTOR_BASE_ADDR + int(num)
        self._debug("TX Motor-0x05: %s" % frame_hex(addr, MOTOR_CONTROL_FUN, [data]))
        try:
            full = getattr(self.bus, "transact_full", None)
            if full is not None:
                res = full(addr, MOTOR_CONTROL_FUN, [int(data)],
                           timeout=MOTOR_CONTROL_TIMEOUT, retries=MOTOR_CONTROL_RETRIES)
            else:
                res = self.bus.transact(addr, MOTOR_CONTROL_FUN, [int(data)],
                                        timeout=MOTOR_CONTROL_TIMEOUT,
                                        retries=MOTOR_CONTROL_RETRIES)
        except TimeoutError:
            self._debug("Motor 0x%02X fun 0x05 [%d]: keine Antwort (wie Stock)"
                        % (addr, data))
            return None
        except ValueError as exc:
            raise self._err(None, "Motor-0x05 an 0x%02X: %s" % (addr, exc))
        return res

    def run_cycle(self):
        """Rüttel-Zyklus (Original set_tension-Schleifenkörper nach run_tension)."""
        self._require(False, True)
        for kind, val in self.cfg.get("cycle_steps", []):
            if kind == "wait":
                self.pause(val)
            elif kind == "motor":
                self.motor_control(val[0], val[1])
            else:
                if self.run_gcode is None:
                    raise self._err(None, "kein G-Code-Ausführer für %r" % (val,))
                self.run_gcode(val)

    # ------------------------------------------------------------------
    # Abläufe mit Freigabe
    # ------------------------------------------------------------------
    def move(self, pos, dry_run=False):
        """KTWO_BELT_MOVE POS= (Original set_place): Schieber auf absolute
        Position, danach Position in den Flash."""
        pos = int(pos)
        self.ensure_flash(require_calibrated=False)
        if self.current_place == UNCALIBRATED:
            raise self._err("uncal", "Schieberposition unbekannt (Flash 0xFFFFFFFF) – "
                                     "erst KTWO_BELT_CALI DIR=0")
        if not 0 <= pos <= 0xFFFFFFFF:
            raise self._err(None, "POS muss >= 0 sein: %d" % pos)
        delta = pos - self.current_place
        direction = 1 if delta > 0 else 0
        dist = abs(delta)
        limit = self._max_steps()
        if dist > limit:
            raise self._err(None, "Weg %d zu groß (max. %d je Bewegung, Option "
                                  "max_steps_units, hart %d; die Belt-FW hat keine "
                                  "Endlage)" % (dist, limit, MOVE_HARD_MAX))
        if dist == 0:
            self._say("Schieber steht bereits auf Position %d – nichts gesendet" % pos)
            return {"executed": False, "reason": "keine Bewegung nötig", "plan": [],
                    "place": pos}
        reason = self._blocked(True, True, dry_run)
        if reason is not None:
            return self._plan_result(reason, [
                "Position %d → %d" % (self.current_place, pos),
                self._plan_move(direction, dist),
                self._plan_flash(place=pos)])
        self.current_place = pos  # Original: vor der Bewegung gesetzt
        self.slider_move(direction, dist)
        self.write_flash()
        return {"executed": True, "place": self.current_place}

    def run_tension(self, target):
        """Regelschleife (Original run_tension, 1:1). 1 = fertig, 0 = Timeout.

        Nur Spannen (Richtung 1); zu straff gilt nach einer Messung als fertig
        (Original). Prüft die ZULETZT gemessene Kraft – nach dem Rüttel-
        Zyklus also den Wert von vor dem Zyklus (Original-Verhalten)."""
        mistake = float(self.cfg.get("control_error", CONTROL_ERROR_DEFAULT))
        movetimes = 0
        while True:
            movetimes += 1
            self.last_iterations = movetimes
            self._debug("Regelschritt %d: %.2f N (Ziel %.1f N)"
                        % (movetimes, self.current_tension, target))
            if movetimes > MAX_ITERATIONS:
                return 0
            aimmove = tension_step(movetimes, target, self.current_tension)
            if target * (1 + mistake) < self.current_tension:
                self.read_adc()
                self.adc_to_tension()
                return 1
            elif target * (1 - mistake) > self.current_tension:
                self.slider_move(1, aimmove)
                self.current_place = self.current_place + aimmove
                self.measure_fresh()  # Stock: 1× read_adc direkt nach der Pause
            else:
                return 1

    def set_tension(self, target=None, dry_run=False):
        """KTWO_BELT_SET MDL_N= (Original set_tension)."""
        target = self.target_tension if target is None else float(target)
        if not 0.0 < target <= 300.0:
            raise self._err(None, "MDL_N muss 0 < N <= 300 sein: %r" % (target,))
        self.ensure_flash(require_calibrated=False)
        self.init_fit()
        self.read_adc()
        self.adc_to_tension()
        reason = self._blocked(True, True, dry_run)
        if reason is not None:
            mistake = float(self.cfg.get("control_error", CONTROL_ERROR_DEFAULT))
            plan = ["Ist %.2f N, Ziel %.1f N ±%.0f %%, Position %d"
                    % (self.current_tension, target, mistake * 100, self.current_place)]
            if target * (1 - mistake) > self.current_tension:
                plan.append("Regelung (max. %d Schritte), erster Schritt: %s"
                            % (MAX_ITERATIONS,
                               self._plan_move(1, tension_step(1, target,
                                                               self.current_tension))))
            else:
                plan.append("Regelung: keine Bewegung nötig (zu straff wird nicht "
                            "gelockert, wie Original)")
            plan.append("%d Rüttel-Zyklen: %s" % (
                int(self.cfg.get("cycles", CYCLES_DEFAULT)),
                "; ".join(self._cycle_text())))
            plan.append("danach finale Regelung + Flash schreiben (fun 4, neue Position)")
            return self._plan_result(reason, plan)
        self._require(True, True)
        for _ in range(int(self.cfg.get("cycles", CYCLES_DEFAULT))):
            self.run_tension(target)  # Ergebnis ignoriert (Original)
            self.run_cycle()
        result = self.run_tension(target)
        if result == 0:
            raise self._err("timeout", "Riemenspannen nach %d Schritten nicht im "
                                       "Ziel (%.2f N statt %.1f N)"
                            % (MAX_ITERATIONS, self.current_tension, target))
        self.write_flash()
        self._say("Riemenkraft %.2f N (Ziel %.1f N), Position %d"
                  % (self.current_tension, target, self.current_place))
        return {"executed": True, "tension": self.current_tension,
                "place": self.current_place}

    def _cycle_text(self):
        out = []
        for kind, val in self.cfg.get("cycle_steps", []):
            if kind == "wait":
                out.append("warten %.2f s" % val)
            elif kind == "motor":
                out.append("Motor-0x05 %s" % frame_hex(MOTOR_BASE_ADDR + val[0],
                                                     MOTOR_CONTROL_FUN, [val[1]]))
            else:
                out.append(val)
        return out

    def calibrate(self, direction, ten=None, dry_run=False):
        """KTWO_BELT_CALI DIR= TEN= (Original set_tension_correction).

        DIR 0: Position := 0 → Flash. DIR 1: ADC bei Referenzkraft TEN
        (Default cali_n1) als idl_adc → Flash. DIR 2: dto. full_adc (cali_n2).
        TEN wirkt (wie Original) nur im RAM bis zum Neustart – dauerhaft
        über cali_n1/cali_n2 in printer.cfg."""
        direction = int(direction)
        if direction not in (0, 1, 2):
            raise self._err(None, "DIR muss 0, 1 oder 2 sein: %r" % (direction,))
        self.ensure_flash(require_calibrated=False)
        if direction in (1, 2) and ten is not None:
            ten = float(ten)
            if not 0.0 <= ten <= 300.0:
                raise self._err(None, "TEN muss 0..300 N sein: %r" % (ten,))
        reason = self._blocked(True, False, dry_run)
        if direction == 0:
            if reason is not None:
                return self._plan_result(reason, [self._plan_flash(place=0)])
            self.current_place = 0
            self.write_flash()
            return {"executed": True, "place": 0}
        adc = self.read_adc()  # lesend, auch im Plan
        if adc == UNCAL_SIGNED:
            raise self._err(None, "gemessener ADC -1 (= 0xFFFFFFFF) würde als "
                                  "„nicht kalibriert“ gespeichert – Messung "
                                  "wiederholen")
        n = (self.adjust1 if direction == 1 else self.adjust2) if ten is None else ten
        if reason is not None:
            if direction == 1:
                line = self._plan_flash(idl=adc)
            else:
                line = self._plan_flash(full=adc)
            return self._plan_result(reason, [
                "Referenzkraft %.1f N, gemessener ADC %d" % (n, adc), line])
        default_n = self.cfg.get("cali_n1" if direction == 1 else "cali_n2")
        if direction == 1:
            self.adjust1 = n
            self.idl_adc = adc
        else:
            self.adjust2 = n
            self.full_adc = adc
        self.fitted = False
        if ten is not None and float(ten) != float(default_n):
            self._say("Hinweis: TEN=%.1f gilt nur bis zum Neustart (nicht im Flash) – "
                      "dauerhaft cali_n%d: %.1f in printer.cfg eintragen"
                      % (n, direction, n))
        self.write_flash()
        return {"executed": True, "adc": adc, "n": n}

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------
    def snapshot(self):
        return {
            "current_tension": self.current_tension,
            "current_place": self.current_place,
            "current_adc": self.current_adc,
            "idl_adc": self.idl_adc,
            "full_adc": self.full_adc,
            "slope": self.slope,
            "intercept": self.intercept,
        }

    def get_status(self, eventtime=None):
        """Klipper-Status (Original: nur ``tension``; hier zusätzlich mehr)."""
        return {
            "tension": self.current_tension,
            "current_place": self.current_place,
            "current_adc": self.current_adc,
            "current_adc_raw": self.current_adc_raw,
            "calibrated": bool(self.flash_loaded and not self.is_uncalibrated()),
            "target_tension": self.target_tension,
            "target_error": self.target_error,
            "in_range": None if self.error_result is None else self.error_result == 0,
            "hal_version": self.hal_version.hex() if self.hal_version else None,
            "soft_version": self.soft_version.hex() if self.soft_version else None,
            "addr": self.addr,
            "allow_write": bool(self.cfg.get("allow_write")),
            "allow_move": bool(self.cfg.get("allow_move")),
            "max_steps_units": self._max_steps(),
            "last_iterations": self.last_iterations,
            "last_error": self.last_error,
        }


# =====================================================================
# Klipper-Hülle [k2_belt <name>]
# =====================================================================

BUS_OBJECT_NAMES = ("k2_485", "k2_rs485", "serial485")


class PrinterK2Belt:
    """Klipper-Objekt [k2_belt mdlx] / [k2_belt mdly]."""

    COMMANDS = (
        ("KTWO_BELT_INFO", "BELT_MDL_INFO", "cmd_INFO",
         "Riemenspannmodul: Version/Flash/ADC/Kraft lesen"),
        ("KTWO_BELT_TEST", "BELT_MDL_TEST", "cmd_TEST",
         "Riemenspannmodul: Kraft im Sollbereich?"),
        ("KTWO_BELT_MOVE", "BELT_MDL_MOVE", "cmd_MOVE",
         "Riemenspannmodul: Schieber auf POS (allow_move+allow_write)"),
        ("KTWO_BELT_SET", "BELT_MDL_SET", "cmd_SET",
         "Riemenspannmodul: auf MDL_N spannen (allow_move+allow_write)"),
        ("KTWO_BELT_CALI", "BELT_MDL_CALI", "cmd_CALI",
         "Riemenspannmodul: kalibrieren DIR=0|1|2 (allow_write)"),
    )

    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        self.name = config.get_name().split()[-1]
        self.konsole = _K2K.Konsole(self.printer, "k2_belt %s" % self.name,
                                    self.gcode)
        opts = {}
        for key in OPTION_NAMES:
            val = config.get(key, None)
            if val is not None:
                opts[key] = val
        try:
            self.cfg = parse_options(opts, self.name)
        except ValueError as exc:
            raise config.error("[k2_belt %s] Config-Fehler: %s" % (self.name, exc))
        self._bus = None
        self.controller = K2BeltController(
            self.name, self.cfg, bus=None, pause=self._pause,
            respond=self._respond, run_gcode=self._run_gcode)
        for new, old, meth, desc in self.COMMANDS:
            handler = self._wrap(getattr(self, meth))
            self.gcode.register_mux_command(new, "MDL_NAME", self.name, handler,
                                            desc=desc)
            if self.cfg.get("stock_aliases"):
                self.gcode.register_mux_command(old, "MDL_NAME", self.name, handler,
                                                desc=desc + " (Stock-Alias)")
        try:
            webhooks = self.printer.lookup_object("webhooks")
            webhooks.register_mux_endpoint("k2_belt_test", "MDL_NAME", self.name,
                                           self._handle_webhook_test)
            if self.cfg.get("stock_aliases"):
                webhooks.register_mux_endpoint("belt_mdl_test", "MDL_NAME", self.name,
                                               self._handle_webhook_test)
        except Exception as exc:  # Webhooks sind optional
            self.konsole.warnung("Webhook nicht registriert: %s" % (exc,))
        self.printer.register_event_handler("klippy:connect", self._handle_connect)

    # -- Verdrahtung -----------------------------------------------------
    def _pause(self, seconds):
        if seconds and seconds > 0:
            # Upstream: reactor.pause(waketime) erwartet ABSOLUTE Zeit.
            self.reactor.pause(self.reactor.monotonic() + float(seconds))

    def _respond(self, msg):
        self.gcode.respond_info(msg)

    def _run_gcode(self, line):
        self.gcode.run_script_from_command(line)

    def _ensure_bus(self):
        """Geteilten RS485-Bus holen (wie [k2_box]: printer-Objekt 'k2_485',
        dieselbe Instanz wie [k2_motor] – EIN Port, EIN Lock)."""
        if self._bus is not None:
            return self._bus
        names = ((self.cfg["bus_object"],) if self.cfg.get("bus_object")
                 else BUS_OBJECT_NAMES)
        for name in names:
            try:
                bus = self.printer.lookup_object(name)
            except Exception:
                continue
            if bus is not None:
                self._bus = bus
                self.controller.bus = bus
                return bus
        raise BeltError("[k2_belt %s] RS485-Bus nicht verdrahtet: Abschnitt [k2_485] "
                        "fehlt in printer.cfg (erwartet printer-Objekt %s, dieselbe "
                        "Instanz wie [k2_motor]/[k2_box])"
                        % (self.name, "/".join("'%s'" % n for n in names)))

    def _handle_connect(self):
        try:
            self._ensure_bus()
        except BeltError as exc:
            # Connect-Zeit: Konsole puffert bis kurz nach „ready“
            self.konsole.warnung(str(exc) + " – Befehle melden Fehler, bis er "
                                 "da ist.")

    def _wrap(self, func):
        def handler(gcmd):
            try:
                self._ensure_bus()
                func(gcmd)
            except BeltError as exc:
                raise gcmd.error(str(exc))
        return handler

    def get_status(self, eventtime=None):
        return self.controller.get_status(eventtime)

    # -- Webhook -----------------------------------------------------------
    def _handle_webhook_test(self, web_request):
        try:
            km = self.printer.lookup_object("k2_motor")
        except Exception:      # [k2_motor] nicht geladen
            km = None
        if getattr(getattr(km, "controller", None), "cal_aktiv", None):
            # Motor-Kalibrierlauf hält den RS485-Bus; ein direkter Buszugriff
            # würde den Reactor bis zum Ende des Laufs blockieren
            raise self.printer.command_error("k2_belt: Motor-Kalibrierlauf läuft – "
                                             "später erneut")
        try:
            self._ensure_bus()
            res = self.controller.test()
        except BeltError as exc:
            raise self.printer.command_error(str(exc))
        web_request.send(res)

    # -- G-Codes -----------------------------------------------------------
    @staticmethod
    def _dry(gcmd):
        return bool(gcmd.get_int("DRY_RUN", 0, minval=0, maxval=1))

    def _done(self):
        self._respond("MDL_NAME: %s" % (self.name,))

    def cmd_INFO(self, gcmd):
        self.controller.info()
        self._done()

    def cmd_TEST(self, gcmd):
        res = self.controller.test()
        self._respond("get_mdl_data: %s" % (res,))
        self._done()

    def cmd_MOVE(self, gcmd):
        pos = gcmd.get_int("POS", None)
        if pos is None:
            raise gcmd.error("[k2_belt %s] POS=<Position> fehlt (Original-Default 0 "
                             "bewusst entfernt)" % self.name)
        self.controller.move(pos, dry_run=self._dry(gcmd))
        self._done()

    def cmd_SET(self, gcmd):
        target = gcmd.get_float("MDL_N", self.controller.target_tension,
                                above=0.0, maxval=300.0)
        self.controller.set_tension(target, dry_run=self._dry(gcmd))
        self._done()

    def cmd_CALI(self, gcmd):
        direction = gcmd.get_int("DIR", None)
        if direction is None:
            raise gcmd.error("[k2_belt %s] DIR=0|1|2 fehlt (Original-Default 0 "
                             "bewusst entfernt)" % self.name)
        ten = gcmd.get_float("TEN", None)
        self.controller.calibrate(direction, ten, dry_run=self._dry(gcmd))
        self._done()


def load_config_prefix(config):
    """Klipper-Einstieg [k2_belt mdlx] / [k2_belt mdly]."""
    return PrinterK2Belt(config)


def load_config(config):
    """Klipper-Einstieg [k2_belt] (ohne Namen → Adresse 0x21 bzw. addr)."""
    return PrinterK2Belt(config)
