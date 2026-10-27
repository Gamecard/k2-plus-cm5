#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline-Test für nachbau/bootstart.py + k2-bootstart.py (ohne Drucker, ohne echte Ports).

Teil A  Frame-Bytes: eigene, unabhängige CRC-8 (Poly 0x07, MSB-first, init 0) über
        len..data, Rahmen F7 addr len status fun data crc; Abgleich an Live-Frames.
Teil B  Sendeschutz: Whitelist (erlaubt) und harte Verbote (abgelehnt, nichts gesendet);
        Standard-Sendeschutz (serial485/k2_transport) unverändert.
Teil C  UART-Simulation (Fake-MCU, Fake-Uhr): App läuft, Loader, stumm, falscher
        Handshake, Prüfsummenfehler, Timeout, ungültige/fremde Version, Start-NAK,
        Nachkontrolle, Bytes in Häppchen.
Teil D  RS485-Simulation (Fake-Bus mit Geräten): App-Motoren, Loader-Motoren/-Belts/-CFS,
        CFS-App, stumme Geräte, CRC-Fehler, Timeouts, ungültige App, Echo, Adresskonflikte.
Teil E  CLI k2-bootstart.py (Fake-Umgebung, Unterprozess für --dry-run/--hilfe).
Teil F  Paket/Integration: pack.sh, install.sh --bootstart, uninstall.sh, systemd-Vorlage,
        Python-3.9-Syntax, keine lokalen Pfade/Kennungen in den neuen Dateien.

Aufruf: python3 test_k2_bootstart.py   (Repo: pi-host/install/, Paket: Paketwurzel)
"""

import ast
import importlib.util
import os
import re
import subprocess
import sys
import tempfile

HIER = os.path.dirname(os.path.abspath(__file__))
SKRIPT = os.path.join(HIER, "k2-bootstart.py")
FEHLER = []
N_OK = [0]


def check(bed, text):
    if bed:
        N_OK[0] += 1
        print("  ok    %s" % text)
    else:
        FEHLER.append(text)
        print("  FEHLT %s" % text)


def nachbau_dir():
    for d in (os.environ.get("K2_NACHBAU_DIR"), os.path.join(HIER, "nachbau"),
              os.path.join(HIER, "..", "..", "nachbau")):
        if d and os.path.isfile(os.path.join(d, "bootstart.py")):
            return os.path.abspath(d)
    raise SystemExit("nachbau/bootstart.py nicht gefunden")


ND = nachbau_dir()
sys.path.insert(0, ND)
import bootstart as bs  # noqa: E402
import serial485  # noqa: E402


def lade_cli():
    spec = importlib.util.spec_from_file_location("k2_bootstart_cli", SKRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------------
# Referenz (unabhängig von serial485): CRC + Rahmen
# ---------------------------------------------------------------------
def ref_crc8(daten):
    crc = 0
    for b in daten:
        for i in range(7, -1, -1):
            bit = ((crc >> 7) & 1) ^ ((b >> i) & 1)
            crc = (crc << 1) & 0xFF
            if bit:
                crc ^= 0x07
    return crc


def ref_frame(addr, fun, daten, status=0):
    body = [len(daten) + 3, status, fun] + list(daten)
    return bytes([0xF7, addr] + body + [ref_crc8(body)])


def ref_parse(raw):
    assert raw[0] == 0xF7, "head"
    n = raw[2]
    assert len(raw) == n + 3, "Länge"
    assert ref_crc8(raw[2:-1]) == raw[-1], "CRC"
    return raw[1], raw[3], raw[4], list(raw[5:-1])


class Uhr:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def schlafe(self, s):
        self.t += s


# =====================================================================
def teil_a():
    print("Teil A: Frame-Bytes und CRC")
    live = ["f7 01 03 00 02 b3", "f7 01 09 00 0a 13 34 00 00 00 00 d7",
            "f7 21 07 00 00 01 02 03 04 f0", "f7 01 07 00 03 ff ff ff 00 98"]
    check(all(ref_crc8(bytes.fromhex(h)[2:-1]) == bytes.fromhex(h)[-1] for h in live),
          "A1 Referenz-CRC trifft 4 Live-Frames (Poly 0x07 über len..data)")
    check(all(ref_crc8(list(range(n))) == serial485.crc8_cal(list(range(n))) for n in range(40)),
          "A2 Referenz-CRC = serial485.crc8_cal (40 Vektoren)")
    for g in (0xFD, 0xFC, 0xFE):
        d = bs.check_bootstart_frame(g, bs.FUN_A1, [g, g])
        fr = serial485.build_frame(g, bs.FUN_A1, d)
        check(fr == ref_frame(g, 0xA1, [g, g]) and fr[:5] == bytes([0xF7, g, 5, 0, 0xA1]),
              "A3 A1 an 0x%02x = %s" % (g, fr.hex(" ")))
    uid = list(range(0x10, 0x1C))
    for g, a in ((0xFD, 0x85), (0xFC, 0x23), (0xFE, 0x01)):
        fr = serial485.build_frame(g, bs.FUN_A0, bs.check_bootstart_frame(g, bs.FUN_A0, [a] + uid))
        check(fr == ref_frame(g, 0xA0, [a] + uid) and fr[2] == 0x10 and len(fr) == 19,
              "A4 A0 an 0x%02x [%02x + UID12] len 0x10 = %s" % (g, a, fr.hex(" ")))
    for a in (0x85, 0x88, 0x23, 0x24, 0x01, 0x04):
        for sub in (0, 3, 2):
            fr = serial485.build_frame(a, 0xF0, bs.check_bootstart_frame(a, 0xF0, [sub]))
            if fr != ref_frame(a, 0xF0, [sub]):
                check(False, "A5 F0 [%02x] an 0x%02x" % (sub, a))
    check(serial485.build_frame(0x85, 0xF0, [2]) == bytes.fromhex("f7 85 04 00 f0 02 42"),
          "A5 F0-Frames byte-genau (z. B. Start an 0x85 = f7 85 04 00 f0 02 42)")
    plan = bs.plan_rs485()
    check(all(ref_parse(b) for _t, b in plan if b) and sum(1 for _t, b in plan if b) == 15,
          "A6 alle 15 Trockenlauf-Frames gültig (Referenz-Parser)")
    check(bs.uart_pruefsumme(b"\x75") == 0x8A and bs.uart_pruefsumme(bytes(25)) == 0xFF
          and bs.uart_pruefsumme(b"mcu0") == (~sum(b"mcu0")) & 0xFF,
          "A7 UART-Prüfsumme ~Σ (0x75 -> 0x8a)")


# =====================================================================
def teil_b():
    print("Teil B: Sendeschutz")
    uid = list(range(0x30, 0x3C))
    erlaubt = [(g, 0xA1, [g, g]) for g in (0xFD, 0xFC, 0xFE)]
    erlaubt += [(0xFD, 0xA0, [a] + uid) for a in (0x85, 0x86, 0x87, 0x88)]
    erlaubt += [(0xFC, 0xA0, [a] + uid) for a in (0x23, 0x24)]
    erlaubt += [(0xFE, 0xA0, [a] + uid) for a in (1, 2, 3, 4)]
    erlaubt += [(a, 0xF0, [s]) for a in sorted(bs.ALLE_LOADER_ADRESSEN) for s in (0, 2, 3)]
    ok = True
    for a, f, d in erlaubt:
        try:
            bs.check_bootstart_frame(a, f, d)
        except bs.BootstartGuardError as e:
            ok = False
            print("        unerwartet gesperrt: %s" % e)
    check(ok, "B1 Whitelist: %d Frames (A1/A0/F0 [00]/[03]/[02]) erlaubt" % len(erlaubt))

    def gesperrt(a, f, d, sitzung=None, muster=None):
        try:
            bs.check_bootstart_frame(a, f, d, sitzung)
        except bs.BootstartGuardError as e:
            return "nichts gesendet" in str(e) and (muster is None or muster in str(e))
        return False

    faelle = []
    for a in sorted(bs.ALLE_LOADER_ADRESSEN) + [0xFD, 0xFC, 0xFE, 0xFF]:
        faelle += [(a, 0xF0, [0x01], "Update-Request"), (a, 0xF0, [0x06], "Erase"),
                   (a, 0xF0, [0x10, 0x27, 0, 0], None), (a, 0xF0, [0xAA] * 0x80, None),
                   (a, 0xF0, [], None), (a, 0xF0, [0, 0], None), (a, 0xF0, [4], None),
                   (a, 0xF0, [5], None), (a, 0xF0, [7], None)]
    check(all(gesperrt(a, f, d, muster=m) for a, f, d, m in faelle),
          "B2 F0 [01]/[06]/Länge/Datenblock 0x80/leer/[04][05][07]/Zusatzbyte gesperrt (%d Fälle)"
          % len(faelle))
    check(all(gesperrt(a, 0xF2, d, muster="0xF2") for a in (1, 0xFE, 0x85, 0xFF, 0x23)
              for d in ([0], [1, 8], [0] + [0x11] * 8 + [1, 2])),
          "B3 fun 0xF2 (EEPROM-Dienst) an jede Adresse gesperrt")
    check(all(gesperrt(0xF0, f, d, muster="0xF0") for f, d in ((0xF2, [1, 4]), (0xA1, [0xF0, 0xF0]),
                                                                (0xF0, [0]), (0x0A, []))),
          "B4 Ziel 0xF0 mit jedem fun gesperrt")
    check(all(gesperrt(a, 0x0B, d, muster="0x0B") for a in (0xFF, 0xFD, 0xFC, 0xFE, 0x85, 0x23, 0x01, 0x11, 0x81)
              for d in ([0], [1], [])),
          "B5 fun 0x0B ([00] Motoren in den Bootloader, auch [01]) überall gesperrt")
    check(all(gesperrt(a, 0x01, d, muster="Reboot") for a in (0xFF, 0xFD, 0x81, 0x85, 0x01) for d in ([0], [])),
          "B6 fun 0x01 (Reboot) gesperrt")
    check(all(gesperrt(a, f, d, muster="Kalibrierung") for a in (0x81, 0x82, 0x83, 0x84, 0x85, 0xFD, 0xFF)
              for f, d in ((3, []), (4, []), (7, [1]), (7, [2]), (7, [6]), (7, [3]))),
          "B7 Motor 0x03/0x04/0x07 (Kalibrierung/Flash-Param, auch lesende Subs) gesperrt")
    andere = [(0xFD, 0xA1, [0xFD, 0x01]), (0xFE, 0xA1, [0xFE]), (0xFC, 0xA1, [0xFD, 0xFD]),
              (0xFF, 0xA1, [0xFF, 0xFF]), (0xFB, 0xA1, [0xFB, 0xFB]), (0x85, 0xA1, [0x85, 0x85]),
              (0xFD, 0xA0, [0x81] + uid), (0xFC, 0xA0, [0x21] + uid), (0xFE, 0xA0, [0x05] + uid),
              (0xFE, 0xA0, [0x85] + uid), (0xFD, 0xA0, [0x85] + uid[:11]), (0xFD, 0xA0, [0x85] + uid + [0]),
              (0xFF, 0xA0, [0x85] + uid), (0xFB, 0xA0, [0x11] + uid),
              (0x81, 0xF0, [0]), (0x84, 0xF0, [2]), (0x21, 0xF0, [0]), (0x22, 0xF0, [2]),
              (0x11, 0xF0, [0]), (0x05, 0xF0, [2]), (0xFD, 0xF0, [0]), (0xFF, 0xF0, [2]),
              (0x85, 0x0E, [1]), (0x01, 0x0A, []), (0x01, 0x0D, [0x0F, 2]), (0xEB, 0x50, [1]),
              (0x01, 0x50, [1]), (0x23, 0x00, []), (0x85, 0x0F, [0]), (0xFE, 0xA2, []), (0x01, 0xA3, [])]
    check(all(gesperrt(a, f, d) for a, f, d in andere),
          "B8 alles außerhalb der Whitelist gesperrt (%d Fälle: falsche Gruppen/Daten/Adressen, App-"
          "Adressen, andere fun)" % len(andere))
    s = bs.Sitzung()
    s.melde_loader(0xFD, uid)
    check(gesperrt(0xFD, 0xA0, [0x85] + list(range(12)), s, "nicht als Loader")
          and not gesperrt(0xFD, 0xA0, [0x85] + uid, s),
          "B9 Sitzung: A0 nur für in diesem Lauf gemeldete Loader-UID")
    s.bestaetige(0x85, list(range(12)))
    check(gesperrt(0xFD, 0xA0, [0x85] + uid, s, "schon an ein anderes Gerät"),
          "B10 Sitzung: Adresse an andere UID vergeben -> A0 gesperrt")
    check(gesperrt(0x86, 0xF0, [0], s, "nicht als Loader bestätigt")
          and not gesperrt(0x85, 0xF0, [2], s),
          "B11 Sitzung: F0 nur an bestätigte Adresse")
    bus = bs.Bus485Roh(FakeSchreiber(), None)
    try:
        bus.sende(0xFD, 0xA1, [0xFD, 0xFD])
        r = False
    except bs.BootstartGuardError:
        r = bus.ser.written == []
    check(r, "B12 Bus485Roh sendet ohne Sitzung nichts")
    bus = bs.Bus485Roh(FakeSchreiber(), bs.Sitzung())
    try:
        bus.sende(0xFF, 0x0B, [0])
        r = False
    except bs.BootstartGuardError:
        r = bus.ser.written == []
    check(r and issubclass(bs.BootstartGuardError, serial485.TxGuardError),
          "B13 Verstoß -> BootstartGuardError (TxGuardError), 0 Bytes geschrieben")
    # Standard-Sendeschutz unverändert
    try:
        serial485.check_tx_frame(0x85, 0xF0, [2])
        std = False
    except serial485.TxGuardError:
        std = True
    tr = open(os.path.join(ND, "..", "pi-host", "k2_transport.py"), encoding="utf-8").read() \
        if os.path.isfile(os.path.join(ND, "..", "pi-host", "k2_transport.py")) else None
    check(std and serial485.TX_GUARD_REV == 20261027
          and serial485.TX_BOOTLOADER_ADDRS == frozenset((0x23, 0x24, 0x85, 0x86, 0x87, 0x88))
          and (tr is None or "TX_GUARD_REV = 20261027" in tr),
          "B14 Standard-Sendeschutz unverändert (F0 an 0x85 weiter gesperrt, TX_GUARD_REV 20261027)")
    for b in bs.UART_ERLAUBT:
        bs.check_uart_tx(b)
    uart_nein = [b"\x01\xfe", b"\x03\xfc", b"\x04\xfb", b"\x05\xfa", b"\x00\x10\x00\x00\xef",
                 bytes(range(0x80)), b"", b"\x75\x75", b"\x00\xff\xff", b"\x02", b"\x7e"]
    r = True
    for b in uart_nein:
        try:
            bs.check_uart_tx(b)
            r = False
        except bs.BootstartGuardError as e:
            r = r and "nichts gesendet" in str(e)
    check(r, "B15 UART: nur 75 / 00 FF / 02 FD; Request/Sektor/Transparent/Länge/Daten gesperrt")


class FakeSchreiber:
    def __init__(self):
        self.written = []

    def write(self, b):
        self.written.append(bytes(b))

    def reset_input_buffer(self):
        pass


# =====================================================================
# Teil C – UART
# =====================================================================
class FakeMcu:
    """Main-/Düsen-MCU: App (identify @230400) oder Bootloader (@115200)."""

    def __init__(self, uhr, app=False, version="mcu0_140_G32-mcu0_022_000", stumm=False,
                 handshake=0x75, pruefsumme_falsch=0, start=(0x75, 0x8A), start_wird_app=True,
                 version_stumm=False, haeppchen=False):
        self.uhr = uhr
        self.app = app
        self.version = version.encode() if isinstance(version, str) else version
        self.stumm = stumm
        self.handshake_byte = handshake
        self.pruefsumme_falsch = pruefsumme_falsch
        self.start = bytes(start)
        self.start_wird_app = start_wird_app
        self.version_stumm = version_stumm
        self.haeppchen = haeppchen
        self.empfangen = []
        self.rx = []
        self.identify_n = 0
        self.offen_baud = []

    def identify(self, _port=None):
        self.identify_n += 1
        if self.app:
            return {"status": "antwort", "version": "1.1.0.48-293-g493f9a0f"}
        return {"status": "keine_antwort"}

    def write(self, b):
        b = bytes(b)
        self.empfangen.append(b)
        if self.stumm or self.app:
            return
        t = self.uhr.t + 0.05
        if b == b"\x75":
            self.rx.append((t, bytes([self.handshake_byte])))
        elif b == b"\x00\xff" and not self.version_stumm:
            cs = (~sum(self.version)) & 0xFF
            if self.pruefsumme_falsch > 0:
                self.pruefsumme_falsch -= 1
                cs ^= 0x55
            r = self.version + bytes([cs])
            if self.haeppchen:
                self.rx += [(t, r[:7]), (t + 0.4, r[7:20]), (t + 0.9, r[20:])]
            else:
                self.rx.append((t, r))
        elif b == b"\x02\xfd":
            self.rx.append((t, self.start))
            if self.start == b"\x75\x8a" and self.start_wird_app:
                self.app = True

    def read(self, n):
        bereit = [x for x in self.rx if x[0] <= self.uhr.t]
        if not bereit:
            self.uhr.t += 0.02
            return b""
        self.rx = [x for x in self.rx if x[0] > self.uhr.t]
        return b"".join(x[1] for x in bereit)

    def reset_input_buffer(self):
        self.rx = [x for x in self.rx if x[0] > self.uhr.t]

    def close(self):
        pass


def uart_lauf(mcu, rolle="main", nachkontrolle=True):
    uhr = mcu.uhr
    log = []

    def oeffne(port, baud):
        mcu.offen_baud.append(baud)
        return mcu
    t0 = uhr.t
    e = bs.uart_bootstart(rolle, "PORT", mcu.identify, oeffne, log.append, schlafe=uhr.schlafe,
                          uhr=uhr, nachkontrolle=nachkontrolle)
    return e, log, uhr.t - t0


def teil_c():
    print("Teil C: UART (Main-/Düsen-MCU)")
    u = Uhr()
    m = FakeMcu(u, app=True)
    e, log, _ = uart_lauf(m)
    check(e["status"] == "app" and m.empfangen == [] and m.offen_baud == [] and m.identify_n == 1,
          "C1 App antwortet auf identify -> Port @115200 nie geöffnet, 0 Bytes gesendet")
    m = FakeMcu(Uhr())
    e, log, _ = uart_lauf(m)
    check(e["status"] == "gestartet" and m.empfangen == [b"\x75", b"\x00\xff", b"\x02\xfd"]
          and m.offen_baud == [115200] and e["version"] == "mcu0_140_G32-mcu0_022_000"
          and m.identify_n == 2 and "App antwortet danach" in e["text"],
          "C2 Loader: 75 | 00 ff | 02 fd @115200, Version gelesen, Nachkontrolle identify ok")
    m = FakeMcu(Uhr(), version="noz0_130_G30-noz0_021_000")
    e, _, _ = uart_lauf(m, rolle="duese")
    check(e["status"] == "gestartet", "C3 Düse (noz0_…) gestartet")
    m = FakeMcu(Uhr(), stumm=True)
    e, _, dt = uart_lauf(m)
    check(e["status"] == "fehler" and m.empfangen == [b"\x75"] and 2.9 <= dt <= 3.3
          and "keine Antwort" in e["text"],
          "C4 stumm: nur 1x 75, Abbruch nach 3 x 1 s Stille (%.2f s), kein Start" % dt)
    m = FakeMcu(Uhr(), handshake=0x00)
    e, _, _ = uart_lauf(m)
    check(e["status"] == "fehler" and m.empfangen == [b"\x75"] and "0x00" in e["text"],
          "C5 falsches Handshake-Byte: sofort Abbruch (wie Stock Exit 2), keine Wiederholung")
    m = FakeMcu(Uhr(), pruefsumme_falsch=1)
    e, _, _ = uart_lauf(m)
    check(e["status"] == "gestartet" and m.empfangen == [b"\x75", b"\x00\xff", b"\x00\xff", b"\x02\xfd"],
          "C6 1x Prüfsumme falsch -> Version erneut, dann Start")
    m = FakeMcu(Uhr(), pruefsumme_falsch=3)
    e, _, _ = uart_lauf(m)
    check(e["status"] == "fehler" and m.empfangen == [b"\x75"] + [b"\x00\xff"] * 3,
          "C7 3x Prüfsumme falsch -> Abbruch, KEIN 02 FD")
    m = FakeMcu(Uhr(), version_stumm=True)
    e, _, _ = uart_lauf(m)
    check(e["status"] == "fehler" and m.empfangen == [b"\x75", b"\x00\xff"],
          "C8 Version-Timeout -> keine Wiederholung (wie Stock), kein Start")
    for v, grund in (("mcu0_140_G32-mcu0_000_000", "000_000"), ("mcu0_140_G32-" + "\x00" * 12, "ungültig"),
                     ("noz0_130_G30-noz0_021_000", "Präfix"), ("mot2_022_C30-mot2_002_081", "Präfix")):
        m = FakeMcu(Uhr(), version=v)
        e, _, _ = uart_lauf(m)
        check(e["status"] == "fehler" and b"\x02\xfd" not in m.empfangen and grund in e["text"],
              "C9 Version %r -> KEIN Start (%s)" % (v[:25].replace("\x00", "\\0"), grund))
    m = FakeMcu(Uhr(), start=(0x1F, 0xE0))
    e, _, _ = uart_lauf(m)
    check(e["status"] == "fehler" and m.empfangen.count(b"\x02\xfd") == 3,
          "C10 Start-Antwort falsch (1f e0) -> 3x 02 FD, dann Fehler")
    m = FakeMcu(Uhr(), start_wird_app=False)
    e, _, _ = uart_lauf(m)
    check(e["status"] == "fehler" and "keine App-Antwort" in e["text"],
          "C11 Start quittiert, aber App antwortet danach nicht -> Fehler")
    m = FakeMcu(Uhr(), haeppchen=True)
    e, _, _ = uart_lauf(m)
    check(e["status"] == "gestartet", "C12 Versionsantwort in 3 Häppchen (0,9 s) wird zusammengesetzt")
    u = Uhr()
    e = bs.uart_bootstart("main", "P", lambda p: {"status": "belegt", "text": "Port gesperrt"},
                          lambda p, b: (_ for _ in ()).throw(AssertionError("geöffnet")),
                          lambda s: None, schlafe=u.schlafe, uhr=u)
    check(e["status"] == "umgebung" and e["gesendet"] == [],
          "C13 identify meldet belegt -> Umgebungsfehler, Port nicht geöffnet")
    u = Uhr()

    def kaputt(p, b):
        raise OSError("Gerät weg")
    e = bs.uart_bootstart("main", "P", lambda p: {"status": "keine_antwort"}, kaputt,
                          lambda s: None, schlafe=u.schlafe, uhr=u)
    check(e["status"] == "umgebung" and "Gerät weg" in e["text"],
          "C14 Port @115200 nicht zu öffnen -> Umgebungsfehler")
    m = FakeMcu(Uhr())
    e, _, _ = uart_lauf(m, nachkontrolle=False)
    check(e["status"] == "gestartet" and m.identify_n == 1, "C15 --ohne-nachkontrolle: kein 2. identify")
    u = Uhr()
    m = FakeMcu(u)
    e = bs.uart_bootstart("main", "P", m.identify, lambda p, b: m, lambda s: None,
                          schlafe=u.schlafe, uhr=u, nur_erkennen=True)
    try:
        bs.check_uart_tx(b"\x02\xfd", nur_erkennen=True)
        gesperrt = False
    except bs.BootstartGuardError:
        gesperrt = True
    bl = bs.UartBootloader(FakeMcu(Uhr()), nur_erkennen=True)
    try:
        bl.start()
        gesperrt2 = False
    except bs.BootstartGuardError:
        gesperrt2 = bl.gesendet == []
    check(e["status"] == "loader" and m.empfangen == [b"\x75", b"\x00\xff"] and not m.app
          and gesperrt and gesperrt2,
          "C16 --nur-erkennen: nur 75 | 00 ff, Status loader; 02 FD im Sendeschutz gesperrt")


# =====================================================================
# Teil D – RS485
# =====================================================================
VERSIONEN = {0xFD: "mot2_023_C30-mot2_002_081", 0xFC: "bet2_023_C03-bet2_000_001",
             0xFE: "cfs0_050_G32-cfs0_000_153"}
TYP = {0xFD: 2, 0xFC: 3, 0xFE: 1}


class FakeGeraet:
    def __init__(self, gruppe, uid, mode=1, typ=None, version=None, sektor=0xE0, adresse=None,
                 a0_antwort=True, f0_antwort=True, f0_stumm_subs=(), start_code=0x75,
                 bleibt_loader=False, korrupt=0, verzoegerung=0.1, antwort_absender=None,
                 a1_kurz=False, a1_doppelt=False):
        self.gruppe = gruppe
        self.uid = list(uid)
        self.mode = mode
        self.typ = TYP[gruppe] if typ is None else typ
        v = VERSIONEN[gruppe] if version is None else version
        self.version = v.encode("latin-1") if isinstance(v, str) else v
        self.sektor = sektor
        self.adresse = adresse
        self.a0_antwort = a0_antwort
        self.f0_antwort = f0_antwort
        self.f0_stumm_subs = f0_stumm_subs
        self.start_code = start_code
        self.bleibt_loader = bleibt_loader
        self.korrupt = korrupt
        self.verzoegerung = verzoegerung
        self.antwort_absender = antwort_absender
        self.a1_kurz = a1_kurz
        self.a1_doppelt = a1_doppelt
        self.empfangen = []
        self.gestartet = False

    def eigene(self):
        return self.gruppe if self.adresse is None else self.adresse

    def verarbeite(self, addr, fun, d):
        g = self.gruppe
        if addr in (g, self.eigene()):
            self.empfangen.append((addr, fun, list(d)))
        if self.mode == 0:   # App: nur unadressierte CFS-App antwortet auf A1 (mode 0)
            if fun == 0xA1 and addr == g == 0xFE and self.adresse is None and d == [g, g]:
                return [ref_frame(g, 0xA1, [self.typ, 0] + self.uid)]
            return []
        if fun == 0xA1 and addr == g and d == [g, g]:
            if self.adresse is None or self.antwort_absender is not None:
                daten = [self.typ, self.mode] + (self.uid[:6] if self.a1_kurz else self.uid)
                fr = ref_frame(self.eigene() if self.antwort_absender is None else self.antwort_absender,
                               0xA1, daten)
                return [fr, fr] if self.a1_doppelt else [fr]
            return []
        if fun == 0xA0 and addr == g and d[1:] == self.uid:
            self.adresse = d[0]
            return [ref_frame(self.adresse, 0xA0, [self.typ, 1] + self.uid)] if self.a0_antwort else []
        if fun == 0xF0 and self.adresse is not None and addr == self.adresse and len(d) == 1:
            if not self.f0_antwort or d[0] in self.f0_stumm_subs:
                return []
            if d[0] == 0:
                return [ref_frame(self.adresse, 0xF0, list(self.version))]
            if d[0] == 3:
                return [ref_frame(self.adresse, 0xF0, [self.sektor])]
            if d[0] == 2:
                fr = ref_frame(self.adresse, 0xF0, [self.start_code])
                if self.start_code == 0x75 and not self.bleibt_loader:
                    self.mode = 0
                    self.gestartet = True
                    self.adresse_app = self.adresse
                    self.adresse = None if self.gruppe == 0xFE else self.adresse
                return [fr]
        return []


class FakeBus:
    def __init__(self, uhr, geraete, echo=False):
        self.uhr = uhr
        self.geraete = geraete
        self.echo = echo
        self.rx = []
        self.written = []

    def write(self, b):
        b = bytes(b)
        self.written.append(b)
        addr, status, fun, d = ref_parse(b)       # wirft bei CRC-/Rahmenfehler
        assert status == 0
        if self.echo:
            self.rx.append((self.uhr.t, b))
        for g in self.geraete:
            # Zufallsverzögerung nur bei A1 (CFS-Bootloader 1–999 ms, BL §1.1),
            # sonst antwortet der Loader sofort (20 ms)
            verz = g.verzoegerung if fun == 0xA1 else 0.02
            for i, fr in enumerate(g.verarbeite(addr, fun, d)):
                if g.korrupt > 0:
                    g.korrupt -= 1
                    fr = fr[:-1] + bytes([fr[-1] ^ 0xFF])
                self.rx.append((self.uhr.t + verz + 0.01 * i, fr))

    def read(self, n):
        bereit = [x for x in self.rx if x[0] <= self.uhr.t]
        if not bereit:
            self.uhr.t += 0.02
            return b""
        bereit.sort(key=lambda x: x[0])
        self.rx = [x for x in self.rx if x[0] > self.uhr.t]
        return b"".join(x[1] for x in bereit)

    def reset_input_buffer(self):
        self.rx = [x for x in self.rx if x[0] > self.uhr.t]

    def close(self):
        pass

    def frames(self):
        return [ref_parse(b) for b in self.written]


def rs_lauf(geraete, echo=False, gruppen=bs.GRUPPEN_REIHENFOLGE, nachkontrolle=True, a1_runden=3):
    uhr = Uhr()
    ser = FakeBus(uhr, geraete, echo=echo)
    bus = bs.Bus485Roh(ser, bs.Sitzung(), uhr=uhr)
    log = []
    erg = bs.rs485_bootstart(bus, log.append, gruppen=gruppen, a1_runden=a1_runden,
                             schlafe=uhr.schlafe, uhr=uhr, nachkontrolle=nachkontrolle)
    return erg, ser, log


def uid(n):
    return [0xA0 + n] + [n] * 10 + [0x5A]


def nur_erlaubtes(ser):
    """Jeder gesendete Frame: fun A1/A0/F0, F0 nur [00]/[03]/[02], Ziel nie 0xF0."""
    for a, _s, f, d in ser.frames():
        if f not in (0xA1, 0xA0, 0xF0) or a == 0xF0 or (f == 0xF0 and d not in ([0], [2], [3])):
            return False
    return True


def teil_d():
    print("Teil D: RS485 (Motoren/Belts/CFS)")
    # D1 alles in der App
    gs = [FakeGeraet(0xFD, uid(i), mode=0, adresse=0x81 + i) for i in range(4)]
    gs += [FakeGeraet(0xFE, uid(9), mode=0, adresse=0x01)]
    erg, ser, _ = rs_lauf(gs)
    fr = ser.frames()
    check(erg["ok"] and erg["geraete"] == [] and len(fr) == 9 and all(f == 0xA1 for _a, _s, f, _d in fr),
          "D1 alles in der App: nur 9x A1 (3 Gruppen x 3 Runden), kein A0/F0")
    # D2 Loader: 4 Motoren, 2 Belts, 1 CFS
    gs = [FakeGeraet(0xFD, uid(i), verzoegerung=0.1 + 0.2 * i) for i in range(4)]
    gs += [FakeGeraet(0xFC, uid(4 + i)) for i in range(2)] + [FakeGeraet(0xFE, uid(7))]
    erg, ser, log = rs_lauf(gs)
    fr = ser.frames()
    a1 = [a for a, _s, f, _d in fr if f == 0xA1]
    a0 = [(a, d[0]) for a, _s, f, d in fr if f == 0xA0]
    check(a1 == [0xFD, 0xFC, 0xFE, 0xFE, 0xFE],
          "D2 Discovery: Motoren/Belts nach 1 Runde vollständig (Ziel 4/2), CFS 3 Runden")
    check(a0 == [(0xFD, 0x85), (0xFD, 0x86), (0xFD, 0x87), (0xFD, 0x88), (0xFC, 0x23), (0xFC, 0x24),
                 (0xFE, 0x01)],
          "D2 A0: Motor 0x85–0x88, Belt 0x23/0x24, CFS 0x01 (Reihenfolge der A1-Antworten)")
    erstes_a0 = [w for w in ser.written if w[4] == 0xA0][0]
    check(erstes_a0 == ref_frame(0xFD, 0xA0, [0x85] + uid(0)) and ser.written[1] == erstes_a0,
          "D2 erstes A0 direkt nach der Motor-Discovery, byte-genau: %s" % erstes_a0.hex(" "))
    f0 = [(a, d[0]) for a, _s, f, d in fr if f == 0xF0]
    adr = [0x85, 0x86, 0x87, 0x88, 0x23, 0x24, 0x01]
    check(f0 == [(a, 0) for a in adr] + [(a, 3) for a in adr] + [(a, 2) for a in adr] + [(a, 0) for a in adr],
          "D2 F0: erst alle [00], dann [03], dann [02], dann Nachkontrolle [00] – je 1x")
    check(erg["ok"] and all(g.gestartet for g in gs) and all(x.status == "gestartet" for x in erg["geraete"])
          and erg["geraete"][0].version == VERSIONEN[0xFD] and erg["geraete"][0].sektor == 0xE0,
          "D2 alle 7 Geräte gestartet, Version/Sektor gelesen")
    check(nur_erlaubtes(ser) and erg["crc_fehler"] == 0, "D2 nur Whitelist-Frames auf dem Draht")
    # D3 CFS-App (unadressiert, mode 0) neben CFS-Loader
    app = FakeGeraet(0xFE, uid(1), mode=0)
    ld = FakeGeraet(0xFE, uid(2), verzoegerung=0.3)
    erg, ser, _ = rs_lauf([app, ld], gruppen=(0xFE,))
    check(erg["ok"] and ld.gestartet and [f for _a, f, _d in app.empfangen].count(0xA1) == 3
          and not any(f == 0xA0 and d[1:] == app.uid for _a, f, d in app.empfangen)
          and not any(f == 0xF0 for _a, f, _d in app.empfangen) and app.adresse is None
          and [x.status for x in erg["geraete"]] == ["app", "gestartet"],
          "D3 CFS-App (mode 0) nur per A1 gesehen, nie A0 für ihre UID, nie F0; Loader-CFS gestartet")
    # D4 CFS mit ungültiger App (Version nur <boot>- + 12 Nullbytes)
    g = FakeGeraet(0xFE, uid(3), version=b"cfs0_050_G32-" + bytes(12))
    erg, ser, _ = rs_lauf([g], gruppen=(0xFE,))
    x = erg["geraete"][0]
    check(not erg["ok"] and x.status == "fehler" and "KEIN Start" in x.grund
          and (0x01, 0xF0, [2]) not in g.empfangen and (0x01, 0xF0, [6]) not in g.empfangen,
          "D4 CFS mit ungültiger App: KEIN F0 [02], kein Erase; bleibt im Loader (Fehler gemeldet)")
    # D5 Start abgelehnt 0x1F
    g = FakeGeraet(0xFE, uid(3), start_code=0x1F)
    erg, ser, _ = rs_lauf([g], gruppen=(0xFE,))
    x = erg["geraete"][0]
    check(not erg["ok"] and "0x1F" in x.grund and [d for _a, f, d in g.empfangen if f == 0xF0].count([2]) == 1,
          "D5 Start-Antwort 0x1F: 1x F0 [02] (Antwort da -> keine Wiederholung), Fehler gemeldet")
    # D6 stumm nach A1 (A0 ohne Antwort): CFS 2 Versuche, Motor 1; Adresse verbraucht
    c = FakeGeraet(0xFE, uid(4), a0_antwort=False)
    m1 = FakeGeraet(0xFD, uid(5), a0_antwort=False)
    m2 = FakeGeraet(0xFD, uid(6), verzoegerung=0.5)
    erg, ser, _ = rs_lauf([c, m1, m2])
    fr = ser.frames()
    check([(a, d[0]) for a, _s, f, d in fr if f == 0xA0] == [(0xFD, 0x85), (0xFD, 0x86), (0xFE, 0x01), (0xFE, 0x01)],
          "D6 A0 ohne Antwort: Motor 1 Versuch, CFS 2 Versuche; nächster Motor bekommt 0x86 (0x85 verbraucht)")
    check(not erg["ok"] and m2.gestartet and not any(a in (0x85, 0x01) for a, _s, f, _d in fr if f == 0xF0),
          "D6 kein F0 an unbestätigte Adressen (0x85/0x01); m2 gestartet; Ergebnis Fehler")
    # D7 CRC-Fehler: A1-/A0-Antwort heil, die erste F0-[00]-Antwort kaputt -> Wiederholung
    g = FakeGeraet(0xFC, uid(8))
    orig = g.verarbeite

    def kaputt_bei_version(addr, fun, d, _o=orig, _g=g):
        if fun == 0xF0 and d == [0] and not getattr(_g, "_k", False):
            _g._k = True
            _g.korrupt = 1
        return _o(addr, fun, d)
    g.verarbeite = kaputt_bei_version
    erg, ser, _ = rs_lauf([g], gruppen=(0xFC,))
    v0 = [d for _a, _s, f, d in ser.frames() if f == 0xF0 and d == [0]]
    check(erg["ok"] and erg["crc_fehler"] == 1 and len(v0) == 3 and g.gestartet,
          "D7 CRC-Fehler in F0-[00]-Antwort: gezählt, 2. Versuch ok (+ Nachkontrolle), Belt gestartet")
    # D8 Timeouts: Gerät antwortet nach A0 nicht mehr
    g = FakeGeraet(0xFD, uid(9), f0_antwort=False)
    erg, ser, _ = rs_lauf([g], gruppen=(0xFD,))
    subs = [d[0] for a, _s, f, d in ser.frames() if f == 0xF0]
    check(not erg["ok"] and subs == [0, 0, 0, 3, 3, 3] and "keine Antwort" in erg["geraete"][0].grund,
          "D8 F0 ohne Antwort: [00] 3x, [03] 3x (je 500 ms), KEIN [02]")
    g = FakeGeraet(0xFD, uid(9), f0_stumm_subs=(3,))
    erg, ser, _ = rs_lauf([g], gruppen=(0xFD,))
    check(erg["ok"] and g.gestartet and erg["geraete"][0].sektor is None,
          "D8 nur Sektor stumm: Start trotzdem (Sektor ist für den Start nicht nötig, wie Stock)")
    # D9 schon adressierter Loader-Motor (antwortet von 0x85) + unadressierter
    alt = FakeGeraet(0xFD, uid(10), adresse=0x85, antwort_absender=0x85)
    neu = FakeGeraet(0xFD, uid(11), verzoegerung=0.4)
    erg, ser, _ = rs_lauf([alt, neu], gruppen=(0xFD,))
    a0 = [(a, d[0]) for a, _s, f, d in ser.frames() if f == 0xA0]
    check(erg["ok"] and a0 == [(0xFD, 0x86)] and alt.gestartet and neu.gestartet,
          "D9 Loader an 0x85 ohne A0 weiter (Stock), neuer Motor bekommt 0x86 (kein Doppel)")
    # D10 fremder Typ / unerwarteter Absender / unbekannter mode -> nicht angefasst
    f1 = FakeGeraet(0xFE, uid(12), typ=4)
    f2 = FakeGeraet(0xFC, uid(13), antwort_absender=0x21, adresse=0x21)
    f3 = FakeGeraet(0xFD, uid(14), mode=2)
    erg, ser, _ = rs_lauf([f1, f2, f3])
    st = sorted(x.status for x in erg["geraete"])
    check(st == ["fremd", "fremd", "fremd"] and not any(f in (0xA0, 0xF0) for _a, _s, f, _d in ser.frames()),
          "D10 Typ 4 an 0xFE / Absender 0x21 / mode 2: gemeldet, nie A0/F0")
    # D11 Echo-Adapter
    gs = [FakeGeraet(0xFC, uid(15))]
    erg, ser, _ = rs_lauf(gs, echo=True, gruppen=(0xFC,))
    check(erg["ok"] and gs[0].gestartet, "D11 Adapter mit Echo: eigene Frames verworfen, Belt gestartet")
    # D12 Start quittiert, Loader bleibt -> Nachkontrolle merkt es
    g = FakeGeraet(0xFD, uid(16), bleibt_loader=True)
    erg, ser, _ = rs_lauf([g], gruppen=(0xFD,))
    check(not erg["ok"] and "noch der Loader" in erg["geraete"][0].grund,
          "D12 Start mit 75 quittiert, Loader antwortet danach weiter -> Fehler (Nachkontrolle)")
    erg, ser, _ = rs_lauf([FakeGeraet(0xFD, uid(16), bleibt_loader=True)], gruppen=(0xFD,), nachkontrolle=False)
    check(erg["ok"] and len([1 for _a, _s, f, d in ser.frames() if f == 0xF0 and d == [0]]) == 1,
          "D12 ohne Nachkontrolle: kein 2. F0 [00]")
    # D13 Gruppen auslassen
    erg, ser, _ = rs_lauf([FakeGeraet(0xFE, uid(17))], gruppen=(0xFD, 0xFC))
    check(not any(a == 0xFE for a, _s, _f, _d in ser.frames()) and erg["geraete"] == [],
          "D13 ohne CFS-Gruppe: kein Frame an 0xFE")
    # D14 zu viele Loader-Motoren
    gs = [FakeGeraet(0xFD, uid(20 + i), verzoegerung=0.05 + 0.1 * i) for i in range(5)]
    erg, ser, _ = rs_lauf(gs, gruppen=(0xFD,))
    st = [x.status for x in erg["geraete"]]
    check(st.count("gestartet") == 4 and st.count("fehler") == 1 and not erg["ok"]
          and "keine freie" in [x for x in erg["geraete"] if x.status == "fehler"][0].grund,
          "D14 5 Loader-Motoren: 4 gestartet, 5. ohne freie Adresse nicht angefasst")
    # D15 doppelte A1-Antwort / zu kurze A1-Antwort
    d1 = FakeGeraet(0xFC, uid(25), a1_doppelt=True)
    d2 = FakeGeraet(0xFC, uid(26), a1_kurz=True, verzoegerung=0.3)
    erg, ser, log = rs_lauf([d1, d2], gruppen=(0xFC,))
    check(len(erg["geraete"]) == 1 and d1.gestartet and not d2.gestartet
          and any("zu kurz" in z for z in log),
          "D15 doppelte A1-Antwort einmal gezählt, zu kurze A1-Antwort ignoriert")
    # D16 Ohne Antwort überhaupt (leerer Bus)
    erg, ser, _ = rs_lauf([], a1_runden=1)
    check(erg["ok"] and len(ser.written) == 3, "D16 leerer Bus, --a1-runden 1: 3x A1, Ergebnis ok")
    # D17 Zeitverhalten Discovery (Fenster 1 s + 1 s je Runde)
    uhr = Uhr()
    ser = FakeBus(uhr, [])
    bus = bs.Bus485Roh(ser, bs.Sitzung(), uhr=uhr)
    t0 = uhr.t
    bs.rs485_bootstart(bus, lambda s: None, gruppen=(0xFD,), schlafe=uhr.schlafe, uhr=uhr)
    check(5.9 <= uhr.t - t0 <= 6.3, "D17 3 Runden à (1,0 + 1,0) s = %.2f s" % (uhr.t - t0))
    # D18 --nur-erkennen: nur A1, Loader gemeldet, A0/F0 im Sendeschutz gesperrt
    gs = [FakeGeraet(0xFD, uid(30)), FakeGeraet(0xFC, uid(31)), FakeGeraet(0xFE, uid(32), mode=0)]
    uhr = Uhr()
    ser = FakeBus(uhr, gs)
    s = bs.Sitzung(nur_erkennen=True)
    bus = bs.Bus485Roh(ser, s, uhr=uhr)
    erg = bs.rs485_bootstart(bus, lambda t: None, schlafe=uhr.schlafe, uhr=uhr)
    st = [x.status for x in erg["geraete"]]
    try:
        s.melde_loader(0xFD, uid(30))
        s.bestaetige(0x85, uid(30))
        bus.sende(0x85, 0xF0, [0])
        gesp = False
    except bs.BootstartGuardError:
        gesp = True
    check(all(f == 0xA1 for _a, _s, f, _d in ser.frames()) and st == ["loader", "loader", "app"]
          and not erg["ok"] and gesp and not any(g.gestartet for g in gs)
          and all(g.adresse is None for g in gs),
          "D18 --nur-erkennen: nur A1, 2 Loader + 1 App gemeldet, A0/F0 gesperrt, nichts adressiert")


# =====================================================================
# Teil E – CLI
# =====================================================================
class FakeUmgebung:
    def __init__(self, uhr, ports, belegt=(), erscheint=None):
        self.uhr = uhr
        self.schlafe = uhr.schlafe
        self.ports = ports              # port -> FakeMcu | FakeBus
        self.belegt = set(belegt)
        self.erscheint = erscheint or {}   # port -> Zeitpunkt
        self.geoeffnet = []

    def ausfuehren(self, fn):
        return fn()

    def existiert(self, p):
        if p in self.erscheint:
            return self.uhr.t >= self.erscheint[p]
        return p in self.ports

    def port_nutzer(self, p):
        return [(4711, "klippy")] if p in self.belegt else []

    def identify(self, p):
        return self.ports[p].identify()

    def oeffne(self, p, baud):
        self.geoeffnet.append((p, baud))
        dev = self.ports[p]
        if isinstance(dev, FakeBus):
            assert baud == 230400
        else:
            assert baud == 115200
        return dev


def cli_lauf(cli, argv, umg):
    out = []
    rc = cli.main(["k2-bootstart.py"] + argv, umgebung=umg, say=lambda s="": out.append(s))
    return rc, "\n".join(out)


def teil_e():
    print("Teil E: CLI k2-bootstart.py")
    cli = lade_cli()
    check(cli.lade_bootstart() is bs, "E0 CLI nutzt dasselbe nachbau/bootstart.py")
    u = Uhr()
    main = FakeMcu(u, app=True)
    duese = FakeMcu(u, version="noz0_130_G30-noz0_021_000")
    bus = FakeBus(u, [FakeGeraet(0xFC, uid(1)), FakeGeraet(0xFC, uid(2), verzoegerung=0.3)])
    umg = FakeUmgebung(u, {"M": main, "D": duese, "R": bus})
    rc, out = cli_lauf(cli, ["--main", "M", "--duese", "D", "--rs485", "R"], umg)
    check(rc == 0 and main.empfangen == [] and duese.empfangen == [b"\x75", b"\x00\xff", b"\x02\xfd"]
          and "ERGEBNIS: OK" in out and "Belts gestartet: 2" in out,
          "E1 Main in App (nichts gesendet), Düse gestartet, 2 Belts gestartet -> Exit 0")
    u = Uhr()
    bus = FakeBus(u, [FakeGeraet(0xFE, uid(3), version=b"cfs0_050_G32-" + bytes(12))])
    rc, out = cli_lauf(cli, ["--rs485", "R"], FakeUmgebung(u, {"R": bus}))
    check(rc == 1 and "NICHT ALLES GESTARTET" in out, "E2 CFS mit ungültiger App -> Exit 1")
    u = Uhr()
    m = FakeMcu(u)
    rc, out = cli_lauf(cli, ["--main", "M", "--rs485", "R"], FakeUmgebung(u, {"M": m}))
    check(rc == 2 and m.empfangen == [] and "Port fehlt" in out, "E3 RS485-Port fehlt -> Exit 2, NICHTS gesendet (auch nicht an M)")
    u = Uhr()
    m = FakeMcu(u)
    b = FakeBus(u, [])
    rc, out = cli_lauf(cli, ["--main", "M", "--rs485", "R"], FakeUmgebung(u, {"M": m, "R": b}, belegt={"R"}))
    check(rc == 2 and m.empfangen == [] and b.written == [] and "BELEGT" in out,
          "E4 Port belegt (Klipper läuft) -> Exit 2, nichts gesendet")
    rc, out = cli_lauf(cli, ["--main", "M", "--duese", "M"], FakeUmgebung(Uhr(), {"M": FakeMcu(Uhr())}))
    check(rc == 2 and "mehrfach" in out, "E5 derselbe Port zweimal -> Exit 2")
    u = Uhr()
    b = FakeBus(u, [FakeGeraet(0xFD, uid(4))])
    umg = FakeUmgebung(u, {"R": b}, erscheint={"R": u.t + 10})
    rc, out = cli_lauf(cli, ["--rs485", "R", "--warte-auf-ports", "30"], umg)
    check(rc == 0 and b.written, "E6 --warte-auf-ports: Port erscheint nach 10 s, Lauf ok")
    u = Uhr()
    umg = FakeUmgebung(u, {"R": FakeBus(u, [])}, erscheint={"R": u.t + 10})
    rc, out = cli_lauf(cli, ["--rs485", "R", "--warte-auf-ports", "3"], umg)
    check(rc == 2 and "Port fehlt" in out, "E7 --warte-auf-ports zu kurz -> Exit 2")
    # Sendeschutz-Verstoß im Ablauf -> Exit 4, Frame nicht gesendet
    u = Uhr()
    b = FakeBus(u, [])
    orig = bs.rs485_bootstart

    def boese(bus, log, **kw):
        bus.sende(0xFF, 0x0B, [0x00])
    bs.rs485_bootstart = boese
    try:
        rc, out = cli_lauf(cli, ["--rs485", "R"], FakeUmgebung(u, {"R": b}))
    finally:
        bs.rs485_bootstart = orig
    check(rc == 4 and b.written == [] and "Sendeschutz" in out, "E8 Sendeschutz-Verstoß -> Exit 4, 0 Bytes")
    # --aus-config
    d = tempfile.mkdtemp()
    with open(os.path.join(d, "printer.cfg"), "w", encoding="utf-8") as fh:
        fh.write("[mcu]\nserial: M\nbaud: 230400\n\n[mcu nozzle_mcu]\n"
                 "serial: /dev/serial/by-id/usb-ANNAHME_USB-UART_Duesen-MCU-if00-port0\n")
    with open(os.path.join(d, "k2-module.cfg"), "w", encoding="utf-8") as fh:
        fh.write("[k2_485]\nserial: R\nbaud: 230400\n")
    u = Uhr()
    m = FakeMcu(u, app=True)
    b = FakeBus(u, [])
    rc, out = cli_lauf(cli, ["--aus-config", d, "--ohne-belts", "--ohne-cfs"], FakeUmgebung(u, {"M": m, "R": b}))
    check(rc == 0 and "Platzhalter" in out and all(f[0] == 0xFD for f in b.frames())
          and len(b.written) == 3 and m.identify_n == 1,
          "E9 --aus-config: [mcu] + [k2_485] übernommen, Düsen-Platzhalter ausgelassen; --ohne-belts/-cfs")
    rc, out = cli_lauf(cli, ["--aus-config", os.path.join(d, "fehlt")], FakeUmgebung(Uhr(), {}))
    check(rc == 2 and "nicht lesbar" in out, "E10 --aus-config ohne Dateien -> Exit 2")
    rc, out = cli_lauf(cli, ["--dry-run", "--rs485", "R", "--ohne-cfs"], None)
    check(rc == 0 and "f7 fd 05 00 a1 fd fd ce" in out and "a1 fe fe" not in out
          and "TROCKENLAUF" in out, "E11 --dry-run (ohne Umgebung): Frames gezeigt, ohne CFS")
    alt_err, sys.stderr = sys.stderr, open(os.devnull, "w")
    try:
        rcs = [cli_lauf(cli, argv, None)[0]
               for argv in (["--bla"], ["--a1-runden", "4"], ["--gesamt-timeout", "0"], ["--main"])]
    finally:
        sys.stderr.close()
        sys.stderr = alt_err
    check(rcs == [2, 2, 2, 2], "E12 ungültige Aufrufe -> Exit 2 %s" % rcs)
    d2 = tempfile.mkdtemp()
    for n, inh in (("printer.cfg", "[mcu]\nserial: /dev/serial/by-id/usb-ANNAHME_x\n"),
                   ("k2-module.cfg", "[k2_485]\nserial: /dev/serial/by-id/usb-ANNAHME_y\n")):
        with open(os.path.join(d2, n), "w", encoding="utf-8") as fh:
            fh.write(inh)
    rc, out = cli_lauf(cli, ["--aus-config", d2, "--dry-run"], None)
    check(rc == 2 and "nur Platzhalter" in out,
          "E12 --aus-config nur mit Platzhaltern -> Exit 2 (auch im Trockenlauf; install.sh --bootstart bricht ab)")
    rc, out = cli_lauf(cli, [], FakeUmgebung(Uhr(), {}))
    check(rc == 2 and "keine Ports" in out, "E13 ohne Ports -> Exit 2")
    u = Uhr()
    m = FakeMcu(u)
    b = FakeBus(u, [FakeGeraet(0xFC, uid(33))])
    rc, out = cli_lauf(cli, ["--nur-erkennen", "--main", "M", "--rs485", "R"], FakeUmgebung(u, {"M": m, "R": b}))
    check(rc == 1 and m.empfangen == [b"\x75", b"\x00\xff"] and all(f[2] == 0xA1 for f in b.frames())
          and "ERKENNUNG" in out and "Belts loader: 1" in out,
          "E16 --nur-erkennen: Main 75|00 ff, RS485 nur A1, Belt im Loader gemeldet -> Exit 1")
    u = Uhr()
    rc, out = cli_lauf(cli, ["--nur-erkennen", "--main", "M"], FakeUmgebung(u, {"M": FakeMcu(u, app=True)}))
    check(rc == 0, "E16 --nur-erkennen, alles in der App -> Exit 0")
    env = dict(os.environ, K2_NACHBAU_DIR=ND, PYTHONDONTWRITEBYTECODE="1")
    pr = subprocess.run([sys.executable, "-B", SKRIPT, "--dry-run"], stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, env=env, timeout=60)
    o = pr.stdout.decode()
    check(pr.returncode == 0 and "75" in o and "02 fd" in o and "f7 fe 05 00 a1 fe fe f8" in o
          and "f7 85 04 00 f0 02 42" in o and "Whitelist" in o,
          "E14 Unterprozess --dry-run ohne Ports: UART- und RS485-Plan, Exit 0")
    pr = subprocess.run([sys.executable, "-B", SKRIPT, "--hilfe"], stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, env=env, timeout=60)
    check(pr.returncode == 0 and "GERÄTETEST OFFEN" in pr.stdout.decode(), "E15 --hilfe -> Exit 0")


# =====================================================================
# Teil F – Paket/Integration (statisch)
# =====================================================================
def teil_f():
    print("Teil F: Paket/Integration")
    lies = lambda p: open(p, encoding="utf-8").read() if os.path.isfile(p) else None  # noqa: E731
    pack = lies(os.path.join(HIER, "pack.sh"))
    inst = lies(os.path.join(HIER, "install.sh"))
    unin = lies(os.path.join(HIER, "uninstall.sh"))
    unit = lies(os.path.join(HIER, "k2-bootstart.service"))
    if pack is not None:   # nur im Repo (pack.sh liegt nicht im Paket)
        check(all(x in pack for x in ("k2-bootstart.py", "test_k2_bootstart.py", "k2-bootstart.service")),
              "F1 pack.sh nimmt k2-bootstart.py, test_k2_bootstart.py, k2-bootstart.service mit")
    check(inst is not None and "--bootstart)" in inst and "--bootstart-aus)" in inst
          and "BOOTSTART=0" in inst and "systemctl enable k2-bootstart.service" in inst
          and "bootstart" in re.search(r"for m in ([^;]*); do", inst).group(1),
          "F2 install.sh: --bootstart (Standard AUS) / --bootstart-aus, Selbsttest nachbau/bootstart")
    check(unin is not None and "k2-bootstart.service" in unin, "F3 uninstall.sh baut die Einheit zurück")
    check(unit is not None and "Type=oneshot" in unit and "Before=@DIENST@.service" in unit
          and "--aus-config @CONFIG_DIR@" in unit and "WantedBy=multi-user.target" in unit
          and "GERÄTETEST OFFEN" in unit,
          "F4 systemd-Vorlage: oneshot, Before=<klipper>, --aus-config, Hinweis Gerätetest offen")
    neu = [os.path.join(ND, "bootstart.py"), SKRIPT, os.path.abspath(__file__)]
    bad = []
    for p in neu:
        try:
            ast.parse(open(p, encoding="utf-8").read(), p, feature_version=(3, 9))
        except SyntaxError as e:
            bad.append("%s: %s" % (os.path.basename(p), e))
    check(not bad, "F5 Python-3.9-Syntax der neuen Dateien %s" % (bad or ""))
    # generische Muster (absolute lokale Pfade – als Zeichenklassen geschrieben, damit der
    # Leak-Grep diese Zeile nicht selbst meldet –, Arbeitsordner-Schema, IP, UUID, Mail); konkrete
    # Namen prüft pack.sh mit der lokalen Musterdatei
    muster = re.compile(r"(/h[o]me/|/t[m]p/|/r[o]ot/|-lokal\b|\b(re|oc)-[a-z0-9]+-1\d{3}\b|"
                        r"192\.168\.|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}|@[a-z0-9-]+\.[a-z]{2,})")
    treffer = []
    for p in neu + [os.path.join(HIER, "k2-bootstart.service")]:
        if os.path.isfile(p):
            for n, z in enumerate(open(p, encoding="utf-8"), 1):
                if p.endswith("test_k2_bootstart.py") and "muster = re.compile" in z:
                    continue
                if muster.search(z) and "re.compile" not in z:
                    treffer.append("%s:%d" % (os.path.basename(p), n))
    check(not treffer, "F6 keine lokalen Pfade/Ordner/IPs/UUIDs/Mails in neuen Dateien %s" % (treffer or ""))


def main():
    teil_a()
    teil_b()
    teil_c()
    teil_d()
    teil_e()
    teil_f()
    print()
    if FEHLER:
        print("ERGEBNIS: %d ok, %d FEHLGESCHLAGEN" % (N_OK[0], len(FEHLER)))
        for f in FEHLER:
            print("  - " + f)
        return 1
    print("ERGEBNIS: %d/%d BESTANDEN" % (N_OK[0], N_OK[0]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
