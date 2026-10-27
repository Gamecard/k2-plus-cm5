#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_transport.py – RS485- und Transparent-Transporte für K2-Plus-Motoren.

Ziel: Upstream-Klipper-Zusatzmodul [k2_motor] (offener Ersatz für Crealitys
motor_control_wrapper.so). Diese Datei kapselt die zwei Bus-Wege mit
GLEICHER Schnittstelle::

    transact(addr, fun, data, timeout=..., retries=0) -> (status, fun, data)

- ``Rs485Transport``: X/Y/Z/Z1 (0x81–0x84) über den RS485-Port (/dev/ttyS5,
  230400). Wrapper um eine nachbau/serial485-artige Bus-API (erwartet
  ``transact_full`` oder ``transact``). In Upstream-Klipper ist das ein
  eigener Python-Seriell-Port – HINWEIS: Der Port wird mit CFS (0x01) und
  Belt (0x21/0x22) geteilt; Zugriffe müssen über EINE gemeinsame Queue /
  EINEN Lock serialisiert werden (Halbduplex, vgl. nachbau/serial485.py).
- ``TransparentTransport``: Extruder-Motor (0x81 auf dem Düsenbus) über die
  Düsen-MCU (``config_transparent`` / ``transparent_send`` /
  ``transparent_response``). Baut den VOLLEN RS485-Frame (F7 addr len status
  fun data crc) mit ``serial485.build_frame``, sendet per ``transparent_send``
  (``timeout_ms``), parst ``transparent_response`` mit ``parse_frame``
  (CRC prüfen, Absenderadresse prüfen). ``retries=0`` wie Stock (genau eine
  Sendung, nie ein Retry – live ~2200 Motor-Sendungen alle „retries = 0“).
- SENDESCHUTZ (17.10.2026) in BEIDEN Transporten vor jeder Sendung
  (``check_tx_frame``/``build_tx_frame``, aus nachbau/serial485 bzw. lokal
  gleichwertig): max. 100 Datenbytes je Frame (fun 0xF0: 0x80), Empfang
  weiter bis len 255; Broadcast 0xFF/0xFD nie mit fun 0x01 (reboot) oder
  0x0B (boot), außer 0xFF 0x0B [01] (CFS-Loader->App); an Motor-Adressen
  0x80-0x88/0xFD nie fun 0x03/0x04 (Kalibrierung) und fun 0x07 nur lesend
  (Sub 02/06) – hart, ohne Override (Box/
  Belt nutzen 0x03/0x04/0x07 anders und bleiben frei). Seit 18.10.2026
  zusätzlich an JEDE Adresse: fun 0xF0 nur [00]/[02]/[03] (kein Update-
  Request/Länge/Daten/06-DB-Erase), nie fun 0xF2, nie Ziel 0xF0, an
  0xEA/0xEB nie 0x50/0x51/0x56. Stufe 2
  (18.10.2026): nie an Bootloader-Adressen 0x23/0x24/0x85-0x88; Belt
  0x21/0x22 nur fun 0/6 leer, 2 [03], 4 [03]+12 B, 8 [0/1][Weg 1..50],
  Frame <= 32 B; RFID 0x11 nur 0x02/0x14 ohne Daten; 0x02/0x14 nie an
  0xFF/0xFD (Belt-/RFID-Firmware). Verstoß ->
  ``TxGuardError`` (ValueError), nichts gesendet. serial485 wird nur mit
  ``TX_GUARD_REV`` >= lokalem Stand genutzt, sonst lokaler Fallback.

Grundlagen (nur gelesen, nichts gesendet):
- Analyse des Stock-Motor-Inits (Ablauf, Param-Abgleich,
  Strapping, Zeiten, Stock-Bug 0x85)
- Analyse der GPIO-Phase (MCU-Befehle transparent_*, set_digital_out,
  query_gpio_status, config_digital_out, [duplicate_pin_override])
- nachbau/serial485.py (build_frame, parse_frame, CRC-8/ATM Poly 0x07)
- nachbau/motor.py (Func-Codes, Timeouts, Adressen)
- Live-Log 15.10.2026 (Boot; transparent-
  Format: Host sendet VOLLE Frames inkl. F7 und CRC, z. B. ``f7 ff 04 00 0e
  01 89``, ``f7 81 04 00 0e 02 80``, ``f7 81 04 00 11 02 14``,
  ``f7 81 04 00 0c 0b 95``; Antwort z. B. ``f7 81 04 00 0e 81 00``,
  ``f7 81 0b 00 0c 00×8 fb``)

Upstream-Klipper-API (geprüft gegen den Upstream-Klon 9cb9975):
- ``mcu.create_oid()``, ``mcu.add_config_cmd("config_transparent oid=%d")``,
  ``mcu.lookup_query_command("transparent_send oid=%c write=%*s
  timeout_ms=%u", "transparent_response oid=%c read=%*s", oid=...,
  is_async=True)`` + eigener Sende-Helfer ``TransparentOneShotCommand``
  (L2, 18.10.2026, s. dort): GENAU EINE Sendung, Warten bis 60 s wie Stock.
- Alle MCU-Zugriffe laufen über die kleine Adapterklasse
  ``TransparentMcuAdapter`` (Protokoll: ``exchange(write, timeout_ms)``),
  damit Tests mit einer Fake-MCU laufen (s. test_k2_motor.py).

Kein Netzwerk, kein Druckerzugriff. Reine Frame-Logik, ohne Hardware lauffähig.
"""

import importlib
import os
import sys
import threading
import time

# --- nachbau/ finden und ANHÄNGEN (Fix 16.10.2026, Import-Falle) -------------
# Früher: sys.path.insert(0, <eigener Ordner>) + insert(0, nachbau/). Unter
# Klipper ist der eigene Ordner klippy/extras/ – vorne in sys.path verdeckt er
# Standardmodule (k2_prtouch: "import statistics" -> extras/statistics.py,
# statistics.median fehlt). Regeln jetzt:
#   1. Der Modulordner kommt NIE in sys.path. Geschwister (k2_transport usw.)
#      laden die Module per "from . import X" (Klipper-Paket extras), Fallback
#      "import X" nur für die Offline-Tests (pi-host/ ist dort sys.path[0]).
#   2. nachbau/ wird nur ANGEHÄNGT (sys.path.append): Namen der Standard-
#      bibliothek, von klippy/ und site-packages haben Vorrang, nachbau kann
#      nichts verdecken. Die nachbau-Dateien importieren sich gegenseitig
#      absolut ("import serial485"), deshalb muss der Ordner in sys.path.
#   3. import_nachbau()/check_nachbau_module() prüfen, dass ein Modul wirklich
#      aus nachbau/ kommt (z. B. kein pip-Paket "box"), sonst klare Meldung.
# Suchreihenfolge (erster Ordner mit serial485.py gewinnt):
#   $K2_NACHBAU_DIR                 (Override, z. B. für Tests)
#   <modulordner>/k2_nachbau/       INSTALLATIONSWEG Pi: klippy/extras/k2_nachbau/
#   <modulordner>/../nachbau/       Repo: pi-host/../nachbau (Offline-Tests)
#   <modulordner>/../../nachbau/    alter Weg: <klipper>/nachbau/ (Stand 16.10. früh)
NACHBAU_ENV = "K2_NACHBAU_DIR"
NACHBAU_SUBDIR = "k2_nachbau"
_HERE = os.path.dirname(os.path.abspath(__file__))


def nachbau_candidates(here=_HERE):
    """Kandidaten-Ordner für nachbau/ in Suchreihenfolge."""
    out = []
    env = os.environ.get(NACHBAU_ENV, "").strip()
    if env:
        out.append(env)
    out.append(os.path.join(here, NACHBAU_SUBDIR))
    out.append(os.path.join(os.path.dirname(here), "nachbau"))
    out.append(os.path.join(os.path.dirname(os.path.dirname(here)), "nachbau"))
    return out


def find_nachbau_dir(here=_HERE):
    """Erster Kandidat mit serial485.py (realpath) oder None."""
    for cand in nachbau_candidates(here):
        if os.path.isfile(os.path.join(cand, "serial485.py")):
            return os.path.realpath(cand)
    return None


NACHBAU_DIR = find_nachbau_dir()


def ensure_nachbau_path():
    """nachbau/ ANHÄNGEN (nie vorne einfügen). -> Ordner oder None."""
    if NACHBAU_DIR is None:
        return None
    known = set(os.path.realpath(p) for p in sys.path if p)
    if NACHBAU_DIR not in known:
        sys.path.append(NACHBAU_DIR)
    return NACHBAU_DIR


def check_nachbau_module(mod):
    """ImportError, wenn ``mod`` nicht aus NACHBAU_DIR stammt (Namenskollision)."""
    name = getattr(mod, "__name__", "?")
    path = getattr(mod, "__file__", None)
    where = os.path.dirname(os.path.realpath(path)) if path else None
    if NACHBAU_DIR is None or where != NACHBAU_DIR:
        raise ImportError(
            "Modul %r kommt aus %s statt aus nachbau/ (%s) – Namenskollision "
            "(z. B. pip-Paket gleichen Namens) oder nachbau/ fehlt; Installation "
            "s. config-pi/README.md §3" % (name, where, NACHBAU_DIR))
    return mod


def import_nachbau(name):
    """nachbau-Modul laden (Pfad anhängen, Herkunft prüfen)."""
    if ensure_nachbau_path() is None:
        raise ImportError(
            "nachbau/ nicht gefunden (gesucht: %s) – Installation s. "
            "config-pi/README.md §3" % ", ".join(nachbau_candidates()))
    return check_nachbau_module(importlib.import_module(name))


try:
    # Bevorzugt: geprüfte Implementierung aus dem Nachbau verwenden.
    _nb_s485 = import_nachbau("serial485")
    _nb_build_frame = _nb_s485.build_frame
    _nb_parse_frame = _nb_s485.parse_frame
    _nb_crc8 = _nb_s485.crc8_cal
    _HAVE_NACHBAU = True
except Exception:  # pragma: no cover – Fallback für Klipper ohne nachbau/
    _nb_s485 = None
    _HAVE_NACHBAU = False

# --- Lokale Frame-Helfer (byte-identisch zu nachbau/serial485.py) ------------
# Dienen als Fallback, wenn nachbau/serial485.py nicht importierbar ist
# (Upstream-Klipper-Deployment: nur k2_transport.py + k2_motor_init.py
# kopieren). In Tests wird die Byte-Gleichheit gegen serial485 geprüft.
PACK_HEAD = 0xF7
STATUS_OK = 0x00
POLY = 0x07
BROADCAST_ADDR = 0xFF


def _crc8_cal(data):
    crc = 0
    for byte in bytes(data):
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ POLY) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


FRAME_LEN_MAX = 255            # Empfang/neutraler Bau (wie serial485)

# --- Sendeschutz (17.10.2026, byte-gleich zu nachbau/serial485.py) -----------
# Längengrenze für AUSGEHENDE Frames (Empfang bleibt bei len 255):
# - CFS-App: Frame-Kontext 0x70 = 112 B, Daten ab Offset 6, CRC fest bei 0x6A
#   -> sauber nur <= 100 Datenbytes (CFS-Firmware-Analyse, B).
# - CFS-Bootloader verwirft len > 0xFC stumm (Bootloader-Analyse, B).
# - Längste live gesendete Requests: 0xA0 mit 13 Datenbytes, 0x06-Schreiben
#   mit 6; an Motoren (RS485 und transparent) nie mehr als 6 Datenbytes.
#   Update-Datenblöcke (fun 0xF0) haben genau 0x80 Datenbytes.
# -> 100 Datenbytes je Frame, bei fun 0xF0 0x80 (len 0x83 < 0xFC).
# Broadcast-Schutz (Motor-Firmware-Analyse, B): an 0xFF/0xFD nie fun 0x01
# (reboot) und nie fun 0x0B (boot -> ALLE Motoren in den Bootloader, Motor-FW
# FUN_104e6 bei Payload [00]); einzige Ausnahme 0xFF 0x0B [01] = CFS-Loader->App
# (live `0xff 0x4 0x0 0xb 0x1`; Motor-App ignoriert [01], @0x104f6-0x104fc).
TX_DATA_MAX = 100
TX_FUNC_UPDATE = 0xF0
TX_DATA_MAX_UPDATE = 0x80
BOOTLOADER_LEN_MAX = 0xFC
TX_FUNC_REBOOT = 0x01
TX_FUNC_BOOT = 0x0B
TX_BOOT_GUARD_ADDRS = frozenset((0xFF, 0xFD))
TX_LOADER_TO_APP_PAYLOAD = (0x01,)
# Motor-Kalibrier-/Flash-Sperre (17.10.2026, gleich serial485, Begründung
# dort): an Motor-Adressen
# 0x80-0x88 + Gruppe 0xFD nie 0x03 (encoder_calibrate), 0x04 (elec_offset,
# auch _ALL), 0x07 nur Sub 02/06 (lesend; 01/03/04/05 ändern Flash/RAM).
# Box (0x01-0x04/0xFE: 0x03 Restmenge, 0x04 Modus, 0x07 Hauptmotor), Belt
# (0x21/0x22: 0x04 Flash), Rack und 0xFF bleiben frei.
TX_MOTOR_GUARD_ADDRS = frozenset(list(range(0x80, 0x89)) + [0xFD])
TX_MOTOR_BLOCKED_FUNCS = frozenset((0x03, 0x04))
TX_FUNC_FLASH_PARAM = 0x07
TX_FLASH_PARAM_READ_SUBS = frozenset((0x02, 0x06))
# Nachtrag 26.10.2026 (gleich serial485, Motor-FW-Analyse
# 25.10.2026): an Motor-Adressen nie 0x02 (Encoder-Register brennen), 0x12
# (dev_uuid) nur exakt [02] lesend – Sub 01 schreibt den Kalibrier-Record;
# 0x12 auch an 0xFF nur [02].
TX_FUNC_ENCODER_REG_CHECK = 0x02
TX_FUNC_DEV_UUID = 0x12
TX_DEV_UUID_READ = (0x02,)
# Update-/Bootloader-/Service-Sperre (18.10.2026, gleich serial485, Begründung
# dort), an JEDE Adresse, ohne Override: fun 0xF0 nur Payload exakt [00] Version /
# [02] App starten / [03] Sektorgröße (gesperrt: 01 Update-Request, Länge,
# Datenblöcke, 06 CFS-DB-Page-Erase, unbekannte Subs, leer); fun 0xF2
# (Bootloader-EEPROM-Dienst) nie; Zieladresse 0xF0 (Service-Adresse) nie;
# an 0xEA/0xEB (CFS-Sonderadressen, alle Boxen) nie 0x50 (Config #6 + Reset),
# 0x51 (Debug-Schalter), 0x56 (Reset).
TX_F0_ALLOWED_SUBS = frozenset((0x00, 0x02, 0x03))
TX_FUNC_EEPROM = 0xF2
TX_ADDR_EEPROM_SERVICE = 0xF0
TX_CFS_SPECIAL_ADDRS = frozenset((0xEA, 0xEB))
TX_CFS_SPECIAL_BLOCKED_FUNCS = frozenset((0x50, 0x51, 0x56))
# Stufe 2 (18.10.2026, gleich serial485, Begründung dort), hart:
# - Bootloader-Adressen 0x23/0x24 (Belt) und 0x85-0x88 (Motor): jedes fun
#   gesperrt (Pi hat keinen Bootloader-Pfad).
# - Belt 0x21/0x22: fun 0/6 nur ohne Daten, fun 2 nur [03], fun 4 nur [03] +
#   12 B, fun 8 nur [dir 0/1][dist BE32 1..TX_BELT_MOVE_MAX], andere fun
#   gesperrt; Belt-Frames <= 32 B gesamt.
# - RFID 0x11: nur fun 0x02/0x14 ohne Daten (0x0B = Bootloader-Reset);
#   0x02/0x14 nie an 0xFF/0xFD.
TX_BELT_ADDRS = frozenset((0x21, 0x22))
TX_BELT_FRAME_MAX = 32
TX_BELT_FLASH_NUM = 0x03
TX_BELT_FLASH_BYTES = 12
TX_BELT_MOVE_MAX = 50
TX_BOOTLOADER_ADDRS = frozenset((0x23, 0x24, 0x85, 0x86, 0x87, 0x88))
TX_RFID_ADDR = 0x11
TX_RFID_ALLOWED_FUNS = frozenset((0x02, 0x14))
TX_BROADCAST_BLOCKED_FUNS = frozenset((0x02, 0x14))
# Regelstand (monoton, Format JJJJMMTT; 18.10.2026 Stufe 2 -> 20261019,
# 26.10.2026 Motor 0x02/0x12 -> 20261026):
# nachbau/serial485 wird nur genutzt, wenn sein TX_GUARD_REV >= diesem Wert
# ist (sonst lokaler Fallback, s. unten).
TX_GUARD_REV = 20261027
# Kalibrier-Einmalfreigabe (26.10.2026 abends, gleich serial485): genau ein
# Frame aus TX_CAL_FREIGABE_FRAMES an 0x81-0x84, ≤ 10 s, nur über
# tx_kalibrier_freigabe() (setzt die Freigabe lokal UND in serial485, weil
# beide Schichten prüfen).
TX_CAL_FREIGABE_ADDRS = frozenset((0x81, 0x82, 0x83, 0x84))
TX_CAL_FREIGABE_FRAMES = frozenset(((0x03, (0x00,)), (0x04, (0x00,)),
                                    (0x07, (0x03, 0x00))))
TX_CAL_FREIGABE_MAX_S = 10.0
_cal_freigabe = None
_cal_freigabe_lock = threading.Lock()


def _cal_freigabe_passt(addr, fun, data):
    global _cal_freigabe
    with _cal_freigabe_lock:
        f = _cal_freigabe
        if f is None:
            return False
        if time.monotonic() > f[3]:
            _cal_freigabe = None
            return False
        return (addr, fun, tuple(data)) == f[:3]


class _LocalTxGuardError(ValueError):
    """Sendeschutz verletzt (Fallback ohne nachbau/); nichts gesendet."""


def _build_frame_local(addr, fun, data=(), status=STATUS_OK):
    """Neutraler Frame-Bau (auch Antwort-/Testframes bis len 255), byte-
    identisch zu serial485.build_frame. Sendepfade: _build_tx_frame_local."""
    data = list(data)
    length = len(data) + 3
    if length > FRAME_LEN_MAX:
        raise ValueError("Frame zu lang: len %d > %d (%d Datenbytes)"
                         % (length, FRAME_LEN_MAX, len(data)))
    body = [length, status, fun] + data
    return bytes([PACK_HEAD, addr] + body + [_crc8_cal(body)])


def _check_tx_frame_local(addr, fun, data=()):
    """Sendeschutz wie serial485.check_tx_frame (Fallback ohne nachbau/).
    Verstoß -> _LocalTxGuardError (ValueError); Rückgabe Daten als Liste."""
    data = list(data)
    if addr == TX_ADDR_EEPROM_SERVICE:
        raise _LocalTxGuardError(
            "Sendeschutz: Zieladresse 0xF0 fun 0x%02x gesperrt – Service-"
            "Adresse des CFS-Bootloaders (EEPROM-Dienst); nichts gesendet" % fun)
    if fun == TX_FUNC_EEPROM:
        raise _LocalTxGuardError(
            "Sendeschutz: addr 0x%02x fun 0xF2 (Bootloader-EEPROM-Dienst) "
            "gesperrt; nichts gesendet" % addr)
    if addr in TX_BOOTLOADER_ADDRS:
        raise _LocalTxGuardError(
            "Sendeschutz: Bootloader-Adresse 0x%02x fun 0x%02x gesperrt "
            "(0x23/0x24 Belt, 0x85-0x88 Motor); nichts gesendet" % (addr, fun))
    if addr in TX_BELT_ADDRS:
        why = None
        if len(data) + 6 > TX_BELT_FRAME_MAX:
            why = "Frame > %d B" % TX_BELT_FRAME_MAX
        elif fun in (0x00, 0x06):
            why = "fun 0/6 nur ohne Daten" if data else None
        elif fun == 0x02:
            why = None if data == [TX_BELT_FLASH_NUM] else "fun 2 nur [03]"
        elif fun == 0x04:
            why = (None if len(data) == 1 + TX_BELT_FLASH_BYTES
                   and data[0] == TX_BELT_FLASH_NUM
                   else "fun 4 nur [03] + 12 Byte")
        elif fun == 0x08:
            ok8 = (len(data) == 5 and data[0] in (0, 1)
                   and 1 <= int.from_bytes(bytes(data[1:5]), "big")
                   <= TX_BELT_MOVE_MAX)
            why = None if ok8 else ("fun 8 nur [0/1][Weg BE32 1..%d]"
                                    % TX_BELT_MOVE_MAX)
        else:
            why = "Belt-FW kennt nur fun 0/2/4/6/8"
        if why is not None:
            raise _LocalTxGuardError(
                "Sendeschutz: Belt 0x%02x fun 0x%02x [%s] gesperrt – %s; "
                "nichts gesendet" % (addr, fun, " ".join(
                    "%02x" % b for b in data[:8]), why))
    if addr == TX_RFID_ADDR and not (fun in TX_RFID_ALLOWED_FUNS and not data):
        raise _LocalTxGuardError(
            "Sendeschutz: RFID 0x11 fun 0x%02x gesperrt – nur 0x02/0x14 ohne "
            "Daten (0x0B = Bootloader-Reset); nichts gesendet" % fun)
    if addr in TX_BOOT_GUARD_ADDRS and fun in TX_BROADCAST_BLOCKED_FUNS:
        raise _LocalTxGuardError(
            "Sendeschutz: Broadcast 0x%02x fun 0x%02x gesperrt – 0x02/0x14 "
            "nur unicast (RFID antwortet auf 0xFF); nichts gesendet"
            % (addr, fun))
    if fun == TX_FUNC_UPDATE and not (
            len(data) == 1 and data[0] in TX_F0_ALLOWED_SUBS):
        raise _LocalTxGuardError(
            "Sendeschutz: addr 0x%02x fun 0xF0 [%s] gesperrt – erlaubt nur "
            "[00]/[02]/[03] (Update-Request/Länge/Daten/06 DB-Page-Erase "
            "gesperrt); nichts gesendet"
            % (addr, " ".join("%02x" % b for b in data[:8])))
    if addr in TX_CFS_SPECIAL_ADDRS and fun in TX_CFS_SPECIAL_BLOCKED_FUNCS:
        raise _LocalTxGuardError(
            "Sendeschutz: Sonderadresse 0x%02x fun 0x%02x gesperrt – "
            "0x50 Config + Reset / 0x51 Debug / 0x56 Reset an alle Boxen; "
            "nichts gesendet" % (addr, fun))
    limit = TX_DATA_MAX_UPDATE if fun == TX_FUNC_UPDATE else TX_DATA_MAX
    if len(data) > limit or len(data) + 3 > BOOTLOADER_LEN_MAX:
        raise _LocalTxGuardError(
            "Sendeschutz: addr 0x%02x fun 0x%02x mit %d Datenbytes (len %d) "
            "> Grenze %d (CFS-App 100, Update-Block 0x80, Bootloader len "
            "<= 0xFC); nichts gesendet" % (addr, fun, len(data), len(data) + 3,
                                           limit))
    if addr in TX_BOOT_GUARD_ADDRS:
        if fun == TX_FUNC_REBOOT:
            raise _LocalTxGuardError(
                "Sendeschutz: Broadcast 0x%02x fun 0x01 (reboot) gesperrt – "
                "startet ALLE Motoren neu; nichts gesendet" % addr)
        if fun == TX_FUNC_BOOT and not (
                addr == BROADCAST_ADDR and tuple(data) == TX_LOADER_TO_APP_PAYLOAD):
            raise _LocalTxGuardError(
                "Sendeschutz: Broadcast 0x%02x fun 0x0B [%s] gesperrt – "
                "schickt ALLE Motoren in den Bootloader. Erlaubt ist nur "
                "0xFF 0x0B [01] (CFS-Loader->App); nichts gesendet"
                % (addr, " ".join("%02x" % b for b in data)))
    if addr in TX_MOTOR_GUARD_ADDRS:
        if fun in TX_MOTOR_BLOCKED_FUNCS and _cal_freigabe_passt(addr, fun, data):
            return data                   # Kalibrier-Einmalfreigabe
        if fun in TX_MOTOR_BLOCKED_FUNCS:
            raise _LocalTxGuardError(
                "Sendeschutz: Motor 0x%02x fun 0x%02x (Kalibrierung) gesperrt "
                "– open-loop ohne Schutz, schreibt Flash/EEPROM; nichts "
                "gesendet" % (addr, fun))
        if fun == TX_FUNC_FLASH_PARAM and (
                not data or data[0] not in TX_FLASH_PARAM_READ_SUBS) \
                and not _cal_freigabe_passt(addr, fun, data):
            raise _LocalTxGuardError(
                "Sendeschutz: Motor 0x%02x fun 0x07 (flash_param) Sub [%s] "
                "gesperrt – erlaubt nur lesend Sub 02/06; nichts gesendet"
                % (addr, " ".join("%02x" % b for b in data[:1])))
        if fun == TX_FUNC_ENCODER_REG_CHECK:
            raise _LocalTxGuardError(
                "Sendeschutz: Motor 0x%02x fun 0x02 (encoder_calibrate) "
                "gesperrt – brennt Encoder-Register ins EEPROM; nichts "
                "gesendet" % addr)
    if (addr in TX_MOTOR_GUARD_ADDRS or addr == BROADCAST_ADDR) \
            and fun == TX_FUNC_DEV_UUID and tuple(data) != TX_DEV_UUID_READ:
        raise _LocalTxGuardError(
            "Sendeschutz: addr 0x%02x fun 0x12 (dev_uuid) [%s] gesperrt – "
            "erlaubt nur [02] (UUID lesen); Sub 01 schreibt den "
            "Kalibrier-Record neu; nichts gesendet"
            % (addr, " ".join("%02x" % b for b in data[:8])))
    return data


def _build_tx_frame_local(addr, fun, data=(), status=STATUS_OK):
    """Sende-Frame (Fallback): Sendeschutz + neutraler Bau."""
    return _build_frame_local(addr, fun, _check_tx_frame_local(addr, fun, data),
                              status=status)


def _parse_frame_local(raw):
    raw = bytes(raw)
    if len(raw) < 6:
        raise ValueError("Frame zu kurz: %d Bytes" % len(raw))
    if raw[0] != PACK_HEAD:
        raise ValueError("head-Fehler: 0x%02x" % raw[0])
    length = raw[2]
    if not 3 <= length <= 255:
        raise ValueError("Längenfeld unplausibel: %d" % length)
    expect = length + 3
    if len(raw) < expect:
        raise ValueError("unvollständig: %d < %d" % (len(raw), expect))
    body = list(raw[2:expect - 1])
    if _crc8_cal(body) != raw[expect - 1]:
        raise ValueError("CRC-Fehler")
    return raw[4], bytes(raw[5:expect - 1])


if _HAVE_NACHBAU:
    build_frame = _nb_build_frame
    parse_frame = _nb_parse_frame
    crc8_cal = _nb_crc8
else:  # pragma: no cover
    build_frame = _build_frame_local
    parse_frame = _parse_frame_local
    crc8_cal = _crc8_cal

# Sendeschutz: nachbau/serial485 bevorzugt (eine Quelle der Wahrheit). Ein
# älteres serial485 ohne check_tx_frame, ohne Motor-Kalibriersperre
# (17.10.2026) oder mit älterem Regelstand (TX_GUARD_REV fehlt/kleiner, z. B.
# ohne F0/F2/0xF0/0xEB-Sperre vom 18.10.2026; Pi-Installation nicht
# aktualisiert) fällt auf die lokale, gleichwertige Prüfung zurück – nie
# ohne bzw. mit schwächerem Schutz.
def _nachbau_guard_ok(mod):
    """True, wenn mod (serial485) den aktuellen Sendeschutz vollständig hat."""
    if mod is None:
        return False
    for name in ("check_tx_frame", "build_tx_frame", "TxGuardError",
                 "TX_MOTOR_GUARD_ADDRS", "TX_F0_ALLOWED_SUBS",
                 "TX_BELT_ADDRS", "TX_BOOTLOADER_ADDRS", "TX_RFID_ALLOWED_FUNS",
                 "tx_kalibrier_freigabe", "tx_kalibrier_freigabe_loeschen"):
        if not hasattr(mod, name):
            return False
    rev = getattr(mod, "TX_GUARD_REV", None)
    return (isinstance(rev, int) and not isinstance(rev, bool)
            and rev >= TX_GUARD_REV)


def _select_tx_guard(mod):
    """(check_tx_frame, build_tx_frame, TxGuardError): aus mod, wenn
    _nachbau_guard_ok(mod), sonst der lokale Fallback."""
    if _nachbau_guard_ok(mod):
        return mod.check_tx_frame, mod.build_tx_frame, mod.TxGuardError
    return _check_tx_frame_local, _build_tx_frame_local, _LocalTxGuardError


check_tx_frame, build_tx_frame, TxGuardError = _select_tx_guard(_nb_s485)


def tx_kalibrier_freigabe(addr, fun, data, gueltig_s=5.0):
    """Kalibrier-Einmalfreigabe in BEIDEN Schichten (lokal + serial485, falls
    vorhanden). Unzulässiger Frame/Gültigkeit -> ValueError, nichts gesetzt."""
    global _cal_freigabe
    key = (int(fun), tuple(int(b) for b in data))
    if addr not in TX_CAL_FREIGABE_ADDRS or key not in TX_CAL_FREIGABE_FRAMES:
        raise ValueError("Kalibrier-Freigabe für addr 0x%02x fun 0x%02x [%s] nicht "
                         "zulässig" % (addr, fun, " ".join("%02x" % b for b in data)))
    gueltig_s = float(gueltig_s)
    if not 0.0 < gueltig_s <= TX_CAL_FREIGABE_MAX_S:
        raise ValueError("Kalibrier-Freigabe: Gültigkeit 0 < s <= %.0f"
                         % TX_CAL_FREIGABE_MAX_S)
    fn = getattr(_nb_s485, "tx_kalibrier_freigabe", None)
    if fn is not None:
        fn(addr, fun, data, gueltig_s)
    with _cal_freigabe_lock:
        _cal_freigabe = (int(addr), key[0], key[1], time.monotonic() + gueltig_s)


def tx_kalibrier_freigabe_loeschen():
    """Freigabe in beiden Schichten löschen (immer im finally aufrufen)."""
    global _cal_freigabe
    with _cal_freigabe_lock:
        _cal_freigabe = None
    fn = getattr(_nb_s485, "tx_kalibrier_freigabe_loeschen", None)
    if fn is not None:
        fn()

def _check_addr_fun(addr, fun):
    if isinstance(addr, bool) or not isinstance(addr, int) or not 0 <= addr <= 0xFF:
        raise ValueError("addr muss int 0..255 sein: %r" % (addr,))
    if isinstance(fun, bool) or not isinstance(fun, int) or not 0 <= fun <= 0xFF:
        raise ValueError("fun muss int 0..255 sein: %r" % (fun,))


# =====================================================================
# L2 (18.10.2026): transparent_send GENAU EINMAL senden, bis 60 s warten
# =====================================================================
# Stock (Creality-klippy 1.1.7.0, /usr/share/klipper/klippy/
# mcu.py:492-523 + 539-540 auf dem Drucker): CommandQueryWrapper(..., extruder_transparent=
# True) -> ExtruderRetryAsyncCommand mit TIMEOUT_TIME = 60.0, RETRY_TIME =
# 0.500, IS_RESEND = False: raw_send_wait_ack EINMAL, dann bis 60 s auf
# transparent_response warten, nie neu senden.
# Upstream 9cb9975 (geprüft):
# - ohne is_async: serialhdl.SerialRetryCommand (serialhdl.py:325-355) –
#   liegt die Antwort beim Ack noch nicht vor, wird bis zu 5x NEU gesendet
#   (10/20/40/80/160 ms). Die transparent-Antwort kommt erst nach dem
#   RS485-Rundlauf (ms bis ~2 s) -> mehrere Frames an den E-Motor.
# - is_async=True: mcu.RetryAsyncCommand (mcu.py:40-72) sendet ebenfalls
#   NEU, und zwar alle RETRY_TIME = 0,5 s bis TIMEOUT_TIME = 5 s (raw_send
#   in der Warteschleife); mit retry=False endet das Warten schon nach 0,5 s
#   (timeout_time = Sendezeit) -> Broadcast (~2 s) scheitert.
#   (Korrektur einer früheren Annahme „is_async: einmal senden, kein
#   erneutes Senden“ – trifft auf 9cb9975 nicht zu.)
# Deshalb: lookup_query_command(..., is_async=True) (Upstream-Weg, liefert
# den CommandQueryWrapper samt Fehlerumsetzung) und danach dessen
# Sende-Helfer ``_xmit_helper`` (UP:mcu.py:85-89, Aufruf
# ``xh.get_response(cmds, cmd_queue, minclock, reqclock, retry)`` in
# ``_do_send``, UP:mcu.py:93-101) durch ``TransparentOneShotCommand``
# ersetzen. Im Batch (fileoutput) bleibt Upstreams DummyResponse.
# Fehlt ``_xmit_helper`` (andere Upstream-Version), bricht setup() ab –
# nie stillschweigend mit einem wiederholenden Helfer weiter.
TRANSPARENT_HOST_TIMEOUT = 60.0     # CR:mcu.py:493 TIMEOUT_TIME = 60.0
# Länger nur für Kalibrierläufe (timeout_ms > 55 s): Host wartet
# timeout_ms + TRANSPARENT_HOST_NACHLAUF. Düsen-MCU: Sekunden als u8 -> max 255 s.
TRANSPARENT_HOST_NACHLAUF = 5.0
TRANSPARENT_MCU_TIMEOUT_MAX_MS = 255000
TRANSPARENT_HOST_POLL = 0.5         # CR:mcu.py:494 RETRY_TIME = 0.500
TRANSPARENT_SEND_FMT = "transparent_send oid=%c write=%*s timeout_ms=%u"
TRANSPARENT_RESP_FMT = "transparent_response oid=%c read=%*s"
# Kennung im Fehlertext: CommandQueryWrapper._do_send macht aus
# serialhdl.error einen command_error mit demselben Text.
TRANSPARENT_TIMEOUT_MARK = "transparent: keine Antwort"


class TransparentHostTimeout(TimeoutError):
    """Keine transparent_response der Düsen-MCU binnen 60 s (Host-Seite)."""


def _serial_error_class():
    """serialhdl.error unter Klipper (CommandQueryWrapper wandelt sie in
    einen command_error), offline TransparentHostTimeout."""
    try:
        import serialhdl  # klippy/ liegt unter Klipper in sys.path
        return serialhdl.error
    except Exception:
        return TransparentHostTimeout


class TransparentOneShotCommand:
    """Sende-Helfer für CommandQueryWrapper (Upstream-Schnittstelle
    ``(serial, name, oid)`` + ``get_response(cmds, cmd_queue, minclock,
    reqclock, retry)``) – Nachbau von Stock-ExtruderRetryAsyncCommand:

    - ``raw_send_wait_ack`` GENAU EINMAL (auch mit retry=True),
    - Antwort nur, wenn ``#sent_time`` >= Erzeugungszeit (wie Stock
      ``min_query_time``) – alte/fremde Antworten zählen nicht,
    - Warten in 0,5-s-Schritten bis ``TIMEOUT_TIME`` (60 s), KEIN raw_send,
    - Antwort-Callback immer abmelden (auch bei Fehler/Timeout).
    """
    TIMEOUT_TIME = TRANSPARENT_HOST_TIMEOUT
    RETRY_TIME = TRANSPARENT_HOST_POLL

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
        if self.need_response and params.get("#sent_time", 0.) >= self.min_query_time:
            self.need_response = False
            self.reactor.async_complete(self.completion, params)

    def get_response(self, cmds, cmd_queue, minclock=0, reqclock=0, retry=True):
        try:
            if len(cmds) != 1:
                raise _serial_error_class()(
                    "transparent: genau ein Befehl je Sendung erwartet (%d)"
                    % len(cmds))
            self.serial.raw_send_wait_ack(cmds[0], minclock, reqclock, cmd_queue)
            first = query_time = self.reactor.monotonic()
            while True:
                params = self.completion.wait(query_time + self.RETRY_TIME)
                if params is not None:
                    return params
                query_time = self.reactor.monotonic()
                if query_time > first + self.TIMEOUT_TIME:
                    raise _serial_error_class()(
                        "%s '%s' (oid %s) binnen %.0f s – einmal gesendet, "
                        "keine Wiederholung (Stock ExtruderRetryAsyncCommand)"
                        % (TRANSPARENT_TIMEOUT_MARK, self.name, self.oid,
                           self.TIMEOUT_TIME))
                # bewusst KEIN raw_send: nie ein zweiter Frame an den E-Motor
        finally:
            self.serial.register_response(None, self.name, self.oid)


# =====================================================================
# Adapter-Protokoll für die Düsen-MCU
# =====================================================================

class TransparentMcuAdapter:
    """Protokoll für den transparent-Kanal (Düsen-MCU).

    Reale Implementierung (Upstream-Klipper 9cb9975)::

        oid = mcu.create_oid()
        mcu.add_config_cmd("config_transparent oid=%d" % oid)
        query = mcu.lookup_query_command(
            "transparent_send oid=%c write=%*s timeout_ms=%u",
            "transparent_response oid=%c read=%*s", oid=oid, is_async=True)
        query._xmit_helper = TransparentOneShotCommand   # L2: eine Sendung
        read = query.send([oid, bytes(write), int(timeout_ms)])["read"]

    Tests nutzen eine Fake-Implementierung mit derselben Methode
    ``exchange(write, timeout_ms) -> read`` (s. test_k2_motor.py: FakeMcu).
    """

    def exchange(self, write, timeout_ms):
        """Vollen Frame senden, Antwort-Bytes empfangen (b'' = leer/Timeout)."""
        raise NotImplementedError


class KlippyTransparentAdapter(TransparentMcuAdapter):
    """Echte Anbindung an die Klipper-MCU (Upstream-API 9cb9975, L2: Einmal-Sendung).

    Lebenszyklus nach REVIEW 1 (wie Stock ``_build_config`` @0xa04fc + L3089):
    - VOR ``klippy:connect``: ``oid = mcu.create_oid()`` +
      ``mcu.register_config_callback(cb)``, im Callback
      ``mcu.add_config_cmd("config_transparent oid=%d" % oid)`` (s.
      ``PrinterK2Motor._build_transparent_config`` in k2_motor_init.py).
    - NACH ``klippy:connect``: ``setup()`` löst den Query-Befehl auf.
    Bis dahin wirft ``exchange`` einen RuntimeError – der Init-Lauf startet
    deshalb erst nach dem Connect.
    """

    def __init__(self, mcu, oid):
        self.mcu = mcu
        self.oid = oid
        self._query = None
        self.one_shot = False   # True = TransparentOneShotCommand aktiv
        self.sends = 0          # Diagnose: Aufrufe von query.send

    def _is_fileoutput(self):
        fn = getattr(self.mcu, "is_fileoutput", None)
        try:
            return bool(fn()) if callable(fn) else False
        except Exception:
            return False

    def setup(self):
        """Query-Befehl nach dem Connect auflösen (L2, 18.10.2026):
        ``is_async=True`` + Sende-Helfer ``TransparentOneShotCommand``
        (eine Sendung, bis 60 s warten, Stock CR:mcu.py:492-523)."""
        try:
            query = self.mcu.lookup_query_command(
                TRANSPARENT_SEND_FMT, TRANSPARENT_RESP_FMT, oid=self.oid,
                is_async=True)
        except Exception as exc:
            raise RuntimeError("transparent_send-Lookup fehlgeschlagen: %s" % exc)
        if self._is_fileoutput():
            # Batch/Dateiausgabe: Upstream-DummyResponse (keine echte MCU)
            self.one_shot = False
        else:
            if not hasattr(query, "_xmit_helper"):
                raise RuntimeError(
                    "transparent: CommandQueryWrapper ohne _xmit_helper "
                    "(Upstream-Version != 9cb9975?) – ohne Einmal-Sendung "
                    "würde Upstream transparent_send WIEDERHOLEN; Kanal "
                    "bleibt gesperrt")
            query._xmit_helper = TransparentOneShotCommand
            self.one_shot = True
        self._query = query

    def exchange(self, write, timeout_ms):
        if self._query is None:
            raise RuntimeError("transparent-Kanal nicht eingerichtet (setup() fehlt)")
        if int(timeout_ms) > TRANSPARENT_MCU_TIMEOUT_MAX_MS:
            raise ValueError("transparent: timeout_ms %d > %d (Düsen-MCU: Sekunden "
                             "als u8)" % (int(timeout_ms), TRANSPARENT_MCU_TIMEOUT_MAX_MS))
        self.sends += 1
        alt = None
        warte = float(timeout_ms) / 1000.0 + TRANSPARENT_HOST_NACHLAUF
        if self.one_shot and warte > TRANSPARENT_HOST_TIMEOUT:
            # Kalibrierlauf (26.10.2026): Host wartet länger als die 60 s des
            # Stock-Helfers, sonst bräche er vor der MCU ab.
            alt = self._query._xmit_helper
            self._query._xmit_helper = type(
                "TransparentOneShotLang", (TransparentOneShotCommand,),
                {"TIMEOUT_TIME": warte})
        try:
            res = self._query.send([self.oid, bytes(write), int(timeout_ms)])
        except Exception as exc:
            # 60-s-Host-Timeout (command_error mit unserer Kennung) ->
            # TimeoutError wie „keine Antwort“; alles andere unverändert.
            if TRANSPARENT_TIMEOUT_MARK in str(exc):
                raise TransparentHostTimeout(str(exc))
            raise
        finally:
            if alt is not None:
                self._query._xmit_helper = alt
        read = res.get("read", b"") if isinstance(res, dict) else b""
        return bytes(read) if read is not None else b""


# =====================================================================
# Rs485Transport – X/Y/Z/Z1 über den RS485-Port
# =====================================================================

class Rs485Transport:
    """Transport für X/Y/Z/Z1 (0x81–0x84) über RS485.

    ``bus`` ist eine nachbau/serial485-artige Bus-API. Bevorzugt wird
    ``bus.transact_full(addr, fun, data, timeout, retries, ...)`` genutzt
    (liefert ``(addr, status, fun, daten, roh)`` inkl. STATUS-Byte); falls
    nur ``bus.transact`` existiert (liefert nur Daten), wird ``status=0x00``
    angenommen (ANNAHME, im Test beides abgedeckt).

    HINWEIS Upstream-Klipper: Der RS485-Port (/dev/ttyS5, 230400, 8N1) wird
    mit CFS (0x01) und Belt (0x21/0x22) GETEILT. Es darf genau EIN offener
    Port mit EINER Sende-Queue existieren (Halbduplex-Lock über die ganze
    Transaktion, vgl. serial485.Serial485._lock). Dieses Modul öffnet den
    Port NICHT selbst, sondern bekommt ``bus`` injiziert – die Port-Verwaltung
    (open/close/queue) bleibt beim Host bzw. einem zentralen 485-Manager.
    """

    BUS_NAME = "ttyS5"

    def __init__(self, bus, name="ttyS5"):
        if bus is None:
            raise ValueError("Rs485Transport braucht ein bus-Objekt")
        self.bus = bus
        self.name = name
        self.sent = []  # [(addr, fun, tuple(data))] – Diagnose, kein Bus-Zugriff

    def transact(self, addr, fun, data, timeout=1.0, retries=0):
        """``(status, fun, data)`` – TimeoutError bei ausbleibender Antwort.

        ``retries``: Stock nutzt IMMER 0 (genau eine Sendung). Der Parameter
        bleibt für Schnittstellen-Gleichheit erhalten und wird an den Bus
        durchgereicht; Aufrufer übergeben 0 (wie Stock).
        """
        _check_addr_fun(addr, fun)
        payload = list(bytes(data) if isinstance(data, (bytes, bytearray)) else data)
        # Sendeschutz (Länge, Broadcast 0x01/0x0B, Motor 0x03/0x04/0x07) VOR
        # dem Bus – unabhängig davon, ob ``bus`` selbst prüft (z. B. Fremd-/
        # Test-Bus).
        payload = check_tx_frame(addr, fun, payload)
        self.sent.append((addr, fun, tuple(payload)))
        full = getattr(self.bus, "transact_full", None)
        if full is not None:
            raddr, rstatus, rfun, rdata, _raw = full(
                addr, fun, payload, timeout=timeout, retries=retries)
            if raddr != addr and addr != BROADCAST_ADDR:
                raise ValueError("Absender 0x%02X statt 0x%02X" % (raddr, addr))
            if rfun != fun:
                raise ValueError("fun-Mismatch: 0x%02X statt 0x%02X" % (rfun, fun))
            return int(rstatus), int(rfun), bytes(rdata)
        # Fallback: bus.transact liefert nur Daten (STATUS unbekannt → 0x00).
        rdata = self.bus.transact(addr, fun, payload, timeout=timeout, retries=retries)
        return STATUS_OK, int(fun), bytes(rdata)


# =====================================================================
# TransparentTransport – Extruder über die Düsen-MCU
# =====================================================================

class TransparentTransport:
    """Transport für den Extruder-Motor (0x81 auf dem Düsenbus).

    Ablauf je Transaktion (ANNAHME zur Upstream-API, belegt durch Stock-Log):
    1. Vollen Frame bauen: ``build_frame(addr, fun, data, status=0x00)``
       = ``[F7][addr][len][status][fun][data][crc]`` (CRC über len..data).
    2. Per ``transparent_send oid write timeout_ms`` senden (``timeout_ms``
       = ``int(timeout * 1000)`` wie Stock ``TIMEOUT_LONG_TIME_MS`` ≈ 2 s
       für den Broadcast, sonst je Func).
    3. ``transparent_response`` lesen: ``b''`` = leer → TimeoutError (Stock:
       Broadcast und ``0C [05]`` bleiben unbeantwortet/leer – der Aufrufer
       wertet das als „erwartet“ aus).
    4. Vollframe mit ``parse_frame`` prüfen (CRC), Absenderadresse prüfen
       (Antwort-Absender == Anfrage-Adresse; Broadcast 0xFF akzeptiert
       beliebig, kommt live aber nie mit Antwort), fun prüfen, STATUS-Byte
       (Byte 3) zurückgeben.

    ``retries``: Stock nutzt 0 (Konstante in ``motor_read485_addr`` 0x615a0
    bzw. ``TIMEOUT_LONG_TIME_MS`` für transparent). Der Parameter wird
    ausgeführt (``retries+1`` Versuche), Aufrufer übergeben 0 wie Stock.
    """

    BUS_NAME = "transparent"

    def __init__(self, mcu_adapter, oid=1, name="transparent"):
        if mcu_adapter is None or not hasattr(mcu_adapter, "exchange"):
            raise ValueError("TransparentTransport braucht mcu_adapter.exchange()")
        self.adapter = mcu_adapter
        self.oid = oid
        self.name = name
        self.sent = []  # [(addr, fun, tuple(data))] – Diagnose

    def transact(self, addr, fun, data, timeout=2.0, retries=0):
        _check_addr_fun(addr, fun)
        payload = list(bytes(data) if isinstance(data, (bytes, bytearray)) else data)
        # Sendeschutz (Länge, Broadcast, Motor-Kalibrierung) vor jeder MCU-Sendung.
        frame = bytes(build_tx_frame(addr, fun, payload, status=STATUS_OK))
        self.sent.append((addr, fun, tuple(payload)))
        if timeout is None or timeout < 0:
            raise ValueError("timeout muss >= 0 sein: %r" % (timeout,))
        if retries is None or retries < 0:
            raise ValueError("retries muss >= 0 sein: %r" % (retries,))
        timeout_ms = int(round(float(timeout) * 1000))
        last_err = None
        for _ in range(int(retries) + 1):
            read = bytes(self.adapter.exchange(frame, timeout_ms) or b"")
            if not read:
                last_err = TimeoutError(
                    "transparent 0x%02X fun 0x%02X: keine Antwort (leer)" % (addr, fun))
                continue
            try:
                rfun, rdata = parse_frame(read)
            except ValueError as exc:
                last_err = exc
                continue
            if len(read) < 6 or read[0] != PACK_HEAD:
                last_err = ValueError("transparent: head-Fehler")
                continue
            raddr = read[1]
            rstatus = read[3]
            if addr != BROADCAST_ADDR and raddr != addr:
                last_err = ValueError(
                    "transparent: Absender 0x%02X statt 0x%02X" % (raddr, addr))
                continue
            if rfun != fun:
                last_err = ValueError(
                    "transparent: fun-Mismatch 0x%02X statt 0x%02X" % (rfun, fun))
                continue
            return int(rstatus), int(rfun), bytes(rdata)
        if isinstance(last_err, TimeoutError):
            raise last_err
        if last_err is not None:
            raise TimeoutError(
                "transparent 0x%02X fun 0x%02X: %s" % (addr, fun, last_err))
        raise TimeoutError("transparent 0x%02X fun 0x%02X: keine Antwort" % (addr, fun))
