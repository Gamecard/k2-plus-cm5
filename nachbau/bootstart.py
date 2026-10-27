#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""bootstart.py – K2-Geräte aus dem Bootloader in die App starten (NUR Start, NIE Update).

Nachbau des START-Teils der Stock-Werkzeuge `mcu_util` (UART: Main-/Düsen-MCU)
und `mcu_util_485` (RS485: Motoren, Belts, CFS) für den Pi. Läuft VOR Klipper
(CLI: pi-host/install/k2-bootstart.py). Es wird NIE geflasht, gelöscht oder
in den Bootloader geschickt.

Belege:
  [FW §x], [BL §x], [NT §x], [PG §x]: Abschnitte der zugrunde liegenden
          Protokoll-/Firmware-Analyse (Stock-Updateweg, CFS-Bootloader,
          Gesamtprotokoll; nicht in diesem Repo)
  [BS §x] BOOTSTART-WERKZEUG.md (Nachtrag dieses Werkzeugs: Feinheiten aus
          `mcu_util`/`mcu_util_485` mit Binär-Adresse, Abweichungen, Offenes)

UART (Main-/Düsen-MCU), Ablauf je Port:
  1. App-Prüfung: Klipper-`identify` @230400 (Aufrufer, z. B. k2-identify).
     Antwortet die App -> NICHTS weiter senden.
  2. sonst @115200 8N1 roh: Handshake `75` -> `75`, Version `00 FF` ->
     25 Byte + ~Σ, Start `02 FD` -> `75 8A`. Andere Bytes sendet das Modul
     nicht (check_uart_tx).
RS485 (230400), Ablauf:
  A1-Discovery an 0xFD/0xFC/0xFE -> nur Geräte mit mode 1 (Loader):
  A0-Adressvergabe (Motor 0x85.., Belt 0x23.., CFS 0x01..) -> F0 [00]
  Version -> F0 [03] Sektor -> F0 [02] Start. Geräte in der App (mode 0)
  werden nicht angefasst.

Sendeschutz: Jeder RS485-Frame läuft durch check_bootstart_frame (enge
Whitelist, s. dort). Der Standard-Sendeschutz serial485.check_tx_frame gilt
ZUSÄTZLICH; einzig seine Regel 5 (Bootloader-Adressen 0x23/0x24/0x85–0x88
gesperrt) wird für F0 [00]/[03]/[02] an genau diese Loader-Adressen ersetzt.
serial485/k2_transport bleiben unverändert.

Selbsttest: python3 bootstart.py   (ohne Hardware; ausführlich:
pi-host/install/test_k2_bootstart.py)
"""

import time

import serial485
from serial485 import (STATUS_OK, TxGuardError, build_frame, check_tx_frame,
                       _scan_frame, _resync)

BOOTSTART_REV = 20261018   # Stand der Whitelist (JJJJMMTT)


class BootstartGuardError(TxGuardError):
    """Bootstart-Sendeschutz verletzt (Whitelist/Verbot). Es wurde NICHTS
    gesendet. Unterklasse von serial485.TxGuardError (ValueError)."""


class ProtokollOffen(Exception):
    """Ein Protokollteil ist nicht belegt – das Werkzeug rät nicht, sondern
    bricht hier ab (s. BOOTSTART-WERKZEUG.md „Offen“)."""


# =====================================================================
# UART (mcu_util) – Main-/Düsen-MCU
# =====================================================================
UART_BAUD_BOOTLOADER = 115200   # cfsetospeed 0x1002 = B115200 [FW §2.1]
UART_BAUD_APP = 230400          # Klipper-App, nicht Bootloader [FW §2.1]
# Befehle 2 Byte [cmd, ~cmd], Handshake 1 Byte [FW §2.2]; Bytefolge an den
# Literalen geprüft (mcu_util @0x111f0 = 0xfffffd02 -> „02 FD“) [BS §2.1]
UART_HANDSHAKE = b"\x75"        # Zustand 1: 1 Byte 0x75 [FW §2.2, BS §2.1]
UART_CMD_VERSION = b"\x00\xff"  # Zustand 7 [FW §2.2]
UART_CMD_START = b"\x02\xfd"    # Zustand 0x11 [FW §2.2]
UART_ACK = 0x75
UART_VERSION_LEN = 25           # 25 Byte ASCII + 1 Byte ~Σ [FW §2.2]
UART_FENSTER_S = 1.0            # select 1 s [FW §2.3]
UART_STILLE_MAX = 3             # 3 Timeouts in Folge -> Exit 1 [FW §2.3]
UART_VERSUCHE = 3               # Prüfsumme/Antwort falsch: bis 3 Sendungen [FW §2.2, BS §2.2]
UART_ERLAUBT = {
    UART_HANDSHAKE: "Handshake",
    UART_CMD_VERSION: "Version lesen",
    UART_CMD_START: "App starten",
}
# Nur für klare Fehlermeldungen (alle gesperrt) [FW §2.2, PG §2.1]
UART_GESPERRT_BEKANNT = {
    b"\x01\xfe": "Update-Request (danach Länge + Datenblöcke = Flashen)",
    b"\x03\xfc": "Sektorgröße (gehört zum Update-Ablauf)",
    b"\x04\xfb": "Transparentmodus an (Extruder-Motor-Update-Weg)",
    b"\x05\xfa": "Transparentmodus aus",
}
# Präfix der Bootloader-Version je Rolle (live: mcu0_140_G32-…, noz0_130_G30-…)
# [FW Nachtrag „Live-Gegenprobe“, FW §4 Dateinamen]
UART_PRAEFIX = {"main": ("mcu0_",), "duese": ("noz0_",)}
UART_NACHLAUF_S = 1.0           # Wartezeit vor der Nachkontrolle (ANNAHME, [BS §5])


def uart_pruefsumme(data):
    """~Σ (8 Bit) – mcu_util FUN_00010e84 [FW §2.2, PG §2.2]."""
    return (~sum(bytearray(data))) & 0xFF


def check_uart_tx(data, nur_erkennen=False):
    """UART-Sendeschutz: erlaubt sind NUR `75`, `00 FF`, `02 FD` (mit
    nur_erkennen nur `75`, `00 FF`). Alles andere (Request/Länge/Daten/
    Sektor/Transparent) -> BootstartGuardError."""
    data = bytes(data)
    if nur_erkennen and data == UART_CMD_START:
        raise BootstartGuardError(
            "Bootstart-Sendeschutz UART: [02 fd] (App starten) im Modus --nur-erkennen "
            "gesperrt; nichts gesendet")
    if data in UART_ERLAUBT:
        return data
    was = UART_GESPERRT_BEKANNT.get(data[:2], "kein Handshake/Version/Start")
    raise BootstartGuardError(
        "Bootstart-Sendeschutz UART: [%s] gesperrt – %s; erlaubt sind nur 75, "
        "00 FF, 02 FD; nichts gesendet"
        % (data[:8].hex(" "), was))


def version_bereinigen(raw):
    """Wie mcu_util Zustand 8: Zeichen außerhalb [0-9A-Za-z_-] -> '0'
    [FW §2.2 „Nicht-[0-9A-Za-z_-] wird zu 0“]."""
    out = []
    for b in bytearray(raw):
        c = chr(b)
        if ("0" <= c <= "9") or ("A" <= c <= "Z") or ("a" <= c <= "z") or c in "_-":
            out.append(c)
        else:
            out.append("0")
    return "".join(out)


def version_pruefen(text, praefixe):
    """(ok, grund) – startfähig nur bei `<boot 12>-<app 12>`:
    - genau 25 Zeichen, '-' an Stelle 12 [FW §4: Dateiname = 25-Zeichen-String];
    - App-Teil Zeichen 4 und 8 = '_' (Stock is_invalid_fw_version [FW §1.1]);
    - App-Nummer nicht `000_000` (Stock würde dann flashen, nie starten:
      [FW §1.1 compare_version], CFS [FW §3.2 Schritt 6]) – kein Beleg, dass
      ein Start damit sicher ist -> kein Start;
    - Bootloader-Präfix passt zur Rolle/Gruppe (z. B. `mcu0_`, `mot`)."""
    if text is None:
        return False, "keine Version"
    if len(text) != 25 or text[12] != "-":
        return False, "Format nicht <boot>-<app> (25 Zeichen)"
    boot, app = text[:12], text[13:]
    if app[4:5] != "_" or app[8:9] != "_":
        return False, "App-Teil ungültig (Stock würde flashen, nicht starten)"
    if app[5:] == "000_000":
        return False, "App-Nummer 000_000 (Stock würde flashen, nicht starten)"
    if praefixe and not any(boot.startswith(p) for p in praefixe):
        return False, "Bootloader-Präfix %r passt nicht zu %s" % (boot[:5], "/".join(praefixe))
    return True, "ok"


class UartFehler(Exception):
    """art: 'timeout' (3 s Stille, Stock Exit 1) | 'antwort' (falsches Byte,
    Stock Exit 2) | 'pruefsumme' (3x falsche Prüfsumme, Stock Exit 2)."""

    def __init__(self, art, text):
        Exception.__init__(self, text)
        self.art = art


class UartBootloader:
    """Start-Teil von mcu_util (Zustände 1/2, 7/8, 0x11/0x12) auf einem
    pyserial-ähnlichen Objekt (write/read/reset_input_buffer), das schon mit
    115200 8N1 offen ist. Zeiten parametrierbar (Tests), Standard = Stock."""

    def __init__(self, ser, uhr=time.monotonic, fenster_s=UART_FENSTER_S,
                 stille_max=UART_STILLE_MAX, versuche=UART_VERSUCHE, nur_erkennen=False):
        self.ser = ser
        self.uhr = uhr
        self.fenster_s = fenster_s
        self.stille_max = stille_max
        self.versuche = versuche
        self.nur_erkennen = nur_erkennen
        self.gesendet = []   # Protokoll aller gesendeten Bytefolgen

    def _sende(self, data):
        data = check_uart_tx(data, self.nur_erkennen)   # Sendeschutz VOR jedem Schreiben
        self.ser.write(data)
        self.gesendet.append(data)

    def _lies(self, n_min):
        """Sammelt Bytes, bis >= n_min da sind. Stock: select 1 s, 3 leere
        Fenster in Folge -> Timeout; jedes empfangene Byte setzt den Zähler
        zurück [FW §2.3, BS §2.2]."""
        buf = bytearray()
        still = 0
        start = self.uhr()
        while True:
            chunk = self.ser.read(64)
            if chunk:
                buf += chunk
                still = 0
                start = self.uhr()
                if len(buf) >= n_min:
                    return bytes(buf)
                continue
            if self.uhr() - start >= self.fenster_s:
                still += 1
                start = self.uhr()
                if still >= self.stille_max:
                    raise UartFehler("timeout", "keine Antwort (%d x %.1f s Stille, %d/%d Byte)"
                                     % (self.stille_max, self.fenster_s, len(buf), n_min))

    def _leeren(self):
        try:
            self.ser.reset_input_buffer()    # Stock: tcflush beim Öffnen [BS §2.1]
        except Exception:
            pass

    def handshake(self):
        """`75` -> erstes Byte `75`. EIN Versuch: falsches Byte = Fehler
        (Stock Zustand 2 -> Exit 2), Stille 3 s = Timeout (Exit 1) [BS §2.2]."""
        self._leeren()
        self._sende(UART_HANDSHAKE)
        r = self._lies(1)
        if r[0] != UART_ACK:
            raise UartFehler("antwort", "Handshake: Antwort 0x%02x statt 0x75" % r[0])
        return True

    def version(self):
        """`00 FF` -> 25 Byte + ~Σ. Prüfsumme falsch -> erneut senden (bis 3x),
        Stille -> Timeout ohne Wiederholung [FW §2.2, BS §2.2].
        Rückgabe: (bereinigter Text, Rohbytes)."""
        for _ in range(self.versuche):
            self._leeren()
            self._sende(UART_CMD_VERSION)
            r = self._lies(UART_VERSION_LEN + 1)
            roh = r[:UART_VERSION_LEN]
            if r[UART_VERSION_LEN] == uart_pruefsumme(roh):
                return version_bereinigen(roh), roh
        raise UartFehler("pruefsumme", "Version: %d x Prüfsumme falsch" % self.versuche)

    def start(self):
        """`02 FD` -> `75 8A` (0x8A = ~0x75). Falsche Antwort -> erneut (bis 3x),
        Stille -> Timeout [FW §2.2, BS §2.2]."""
        letzte = b""
        for _ in range(self.versuche):
            self._leeren()
            self._sende(UART_CMD_START)
            r = self._lies(2)
            letzte = r[:2]
            if r[0] == UART_ACK and r[1] == uart_pruefsumme(r[:1]):
                return True
        raise UartFehler("antwort", "Start: %d x falsche Antwort (zuletzt %s)"
                         % (self.versuche, letzte.hex(" ")))


def uart_bootstart(rolle, port, identify, oeffne, log, schlafe=time.sleep,
                   uhr=time.monotonic, nachkontrolle=True, zeiten=None, nur_erkennen=False):
    """Ablauf für EINE MCU. identify(port) -> dict mit 'status'
    ('antwort'|'keine_antwort'|'belegt'|'fehler') wie k2-identify.identify_versuch
    (@230400). oeffne(port, baud) -> pyserial-ähnliches Objekt.
    nur_erkennen: nach Handshake + Version Schluss, NIE `02 FD` (auch im
    Sendeschutz gesperrt) -> Status 'loader'.
    Rückgabe: dict(status='app'|'gestartet'|'loader'|'fehler'|'umgebung',
    text=..., version=..., gesendet=[bytes])."""
    zeiten = dict(zeiten or {})
    erg = {"rolle": rolle, "port": port, "status": "fehler", "text": "",
           "version": None, "gesendet": []}
    # 1. App-Prüfung (nur lesend) – antwortet die App, wird NICHTS gesendet
    info = identify(port)
    st = info.get("status")
    if st == "antwort":
        erg.update(status="app", text="App antwortet auf identify @%d (%s) – nichts gesendet"
                   % (UART_BAUD_APP, info.get("version") or "?"))
        log("  App läuft (identify @%d, Firmware %s) -> nichts gesendet"
            % (UART_BAUD_APP, info.get("version") or "?"))
        return erg
    if st in ("belegt", "fehler"):
        erg.update(status="umgebung", text="App-Prüfung nicht möglich: %s" % info.get("text", st))
        log("  App-Prüfung nicht möglich: %s -> nichts gesendet" % info.get("text", st))
        return erg
    log("  keine Antwort auf identify @%d -> Bootloader-Protokoll @%d"
        % (UART_BAUD_APP, UART_BAUD_BOOTLOADER))
    # 2. Bootloader: Handshake, Version, Start (Stock: -c, -g, -s [FW §1.1])
    try:
        ser = oeffne(port, UART_BAUD_BOOTLOADER)
    except Exception as e:   # pyserial fehlt, Port weg/gesperrt
        erg.update(status="umgebung", text="Port @%d nicht zu öffnen: %s" % (UART_BAUD_BOOTLOADER, e))
        log("  Port @%d nicht zu öffnen: %s -> nichts gesendet" % (UART_BAUD_BOOTLOADER, e))
        return erg
    bl = UartBootloader(ser, uhr=uhr, nur_erkennen=nur_erkennen,
                        fenster_s=zeiten.get("uart_fenster_s", UART_FENSTER_S))
    try:
        try:
            bl.handshake()
            log("  Handshake 75 -> 75: ok")
            text, roh = bl.version()
            erg["version"] = text
            log("  Version (00 FF): %s" % text)
            ok, grund = version_pruefen(text, UART_PRAEFIX.get(rolle, ()))
            if not ok:
                erg.update(text="Version %s: %s – KEIN Start" % (text, grund))
                log("  KEIN Start: %s" % grund)
                return erg
            if nur_erkennen:
                erg.update(status="loader", text="im Bootloader (%s); --nur-erkennen: NICHT "
                           "gestartet – danach Start nötig (Lauf ohne --nur-erkennen) oder "
                           "Drucker aus/an" % text)
                log("  --nur-erkennen: im Bootloader, KEIN Start gesendet")
                return erg
            bl.start()
            log("  Start 02 FD -> 75 8A: ok")
        except UartFehler as e:
            erg.update(text="Bootloader-Protokoll: %s" % e)
            log("  Bootloader-Protokoll: %s" % e)
            return erg
    finally:
        erg["gesendet"] = list(bl.gesendet)
        try:
            ser.close()
        except Exception:
            pass
    erg.update(status="gestartet", text="aus dem Bootloader gestartet (%s)" % text)
    # 3. Nachkontrolle (nur lesend)
    if nachkontrolle:
        schlafe(zeiten.get("uart_nachlauf_s", UART_NACHLAUF_S))
        info = identify(port)
        if info.get("status") == "antwort":
            log("  Nachkontrolle: App antwortet auf identify (%s)" % (info.get("version") or "?"))
            erg["text"] += "; App antwortet danach"
        else:
            erg.update(status="fehler", text=erg["text"] + "; NACH dem Start keine App-Antwort")
            log("  Nachkontrolle: App antwortet NICHT auf identify")
    return erg


def plan_uart(rolle, nur_erkennen=False):
    """Trockenlauf: [(Text, bytes|None)] – jede Bytefolge geht durch check_uart_tx."""
    p = UART_PRAEFIX.get(rolle, ("?",))[0]
    plan = [("App-Prüfung: Klipper-identify @%d (nur lesen) – antwortet die App: "
             "ENDE, nichts weiter" % UART_BAUD_APP, None),
            ("sonst @%d 8N1: Handshake, erwartet 75 (≤ %d x %.0f s Stille)"
             % (UART_BAUD_BOOTLOADER, UART_STILLE_MAX, UART_FENSTER_S), UART_HANDSHAKE),
            ("Version, erwartet 25 Byte + ~Σ (≤ %d Versuche)" % UART_VERSUCHE,
             UART_CMD_VERSION)]
    if nur_erkennen:
        plan.append(("--nur-erkennen: ENDE, kein Start (02 FD gesperrt)", None))
    else:
        plan += [("nur bei gültiger Version mit Präfix %s…: Start, erwartet 75 8A" % p,
                  UART_CMD_START),
                 ("Nachkontrolle: Klipper-identify @%d (nur lesen)" % UART_BAUD_APP, None)]
    for _t, b in plan:
        if b is not None:
            check_uart_tx(b, nur_erkennen)
    return plan


# =====================================================================
# RS485 (mcu_util_485) – Motoren, Belts, CFS
# =====================================================================
FUN_A1 = 0xA1    # GET_SLAVE_INFO / Discovery [PG §1.6, FW §3.2 Schritt 1]
FUN_A0 = 0xA0    # SET_SLAVE_ADDR [PG §1.6, FW §3.2 Schritt 2]
FUN_F0 = 0xF0    # Update-Funktion, Sub in data[0] [FW §3.2]
SUB_VERSION = 0x00   # [FW §3.2 Schritt 3]
SUB_SEKTOR = 0x03    # [FW §3.2 Schritt 4]
SUB_START = 0x02     # [FW §3.2 Schritt 8]
F0_SUBS_ERLAUBT = (SUB_VERSION, SUB_SEKTOR, SUB_START)
F0_ACK = 0x75        # Start ok nur bei data[0] == 0x75 [FW §3.2 Schritt 8, BS §3.4]
F0_CODES = {0x1F: "0x1F (abgelehnt – CFS: App-CRC ungültig)", 0x20: "0x20 (Transfer fertig)",
            0x21: "0x21 (Flash-Fehler)", 0x75: "0x75 (Ack)"}   # [FW §3.2, BL §1.1]
MODE_APP = 0
MODE_LOADER = 1      # [BL §1.1 „mode 1“, FW §3.2]
DEV_TYPE_CFS = 1
DEV_TYPE_MOTOR = 2
DEV_TYPE_BELT = 3
DEV_TYPE_CFS_PRO = 10  # [FW §3.1 Typtabelle]
VERSION_LEN = 25     # F0 [00] -> 25 Byte ab data[0] [FW §3.2 Schritt 3, NT §3a]

GRUPPE_MOTOR = 0xFD
GRUPPE_BELT = 0xFC
GRUPPE_CFS = 0xFE
# Je Gruppe: Typen, Loader-Adressen (Startadresse +1 je Gerät, Live: Motor
# 0x85–0x88, Belt 0x23/0x24, CFS 0x01), Discovery-Ziel, A0-Versuche,
# Versionspräfix [FW §3.2 Schritte 1/2, FW Nachtrag, PG §1.6, BS §3.2]
GRUPPEN = {
    GRUPPE_MOTOR: {"name": "Motoren", "typen": (DEV_TYPE_MOTOR,),
                   "adressen": (0x85, 0x86, 0x87, 0x88), "ziel": 4,
                   "a0_versuche": 1, "a0_immer": False, "praefix": ("mot",)},
    GRUPPE_BELT: {"name": "Belts", "typen": (DEV_TYPE_BELT,),
                  "adressen": (0x23, 0x24), "ziel": 2,
                  "a0_versuche": 1, "a0_immer": False, "praefix": ("bet",)},
    GRUPPE_CFS: {"name": "CFS", "typen": (DEV_TYPE_CFS, DEV_TYPE_CFS_PRO),
                 "adressen": (0x01, 0x02, 0x03, 0x04), "ziel": 4,
                 "a0_versuche": 2, "a0_immer": True, "praefix": ("cfs",)},
}
GRUPPEN_REIHENFOLGE = (GRUPPE_MOTOR, GRUPPE_BELT, GRUPPE_CFS)   # wie Stock (RFID entfällt)
ALLE_LOADER_ADRESSEN = frozenset(a for g in GRUPPEN.values() for a in g["adressen"])
UID_LEN = 12

# Zeiten (Stock-Host-Timeouts) [FW §3.2, §3.5, BS §3.1]
A1_FENSTER_S = 1.0     # Timeout 1000 ms
A1_PAUSE_S = 1.0       # danach usleep 1 s, der Empfangsthread sammelt weiter
A1_RUNDEN = 3          # Motor/Belt/CFS je bis 3 Runden
A0_TIMEOUT_S = 1.5     # 0x5dc ms
F0_TIMEOUT_S = 0.5     # 500 ms
F0_VERSUCHE = 3        # 3 Versuche (Wiederholung nur ohne Antwort)
NACHLAUF_S = 3.5       # CFS: Ack, 3 s, Sprung [BL §1.1 Sub 2] + 0,5 s Reserve (ANNAHME)


class Sitzung:
    """Laufzeit-Whitelist: welche UIDs in diesem Lauf als Loader (mode 1)
    gemeldet wurden und welche Loader-Adressen bestätigt sind. Ohne Eintrag
    hier lehnt check_bootstart_frame A0/F0 ab."""

    def __init__(self, nur_erkennen=False):
        self.nur_erkennen = nur_erkennen   # True: nur A1, A0/F0 gesperrt
        self.loader_uids = dict((g, set()) for g in GRUPPEN)
        self.adressen = {}     # addr -> uid (bestätigt)

    def melde_loader(self, gruppe, uid):
        self.loader_uids[gruppe].add(tuple(uid))

    def bestaetige(self, addr, uid):
        self.adressen[addr] = tuple(uid)


def check_bootstart_frame(addr, fun, data=(), sitzung=None):
    """Bootstart-Sendeschutz für EINEN RS485-Frame (zusätzlich zum Standard).

    1. Verbote (vor der Whitelist, jede Adresse): Ziel 0xF0, fun 0xF2,
       fun 0xF0 mit anderem Inhalt als genau [00]/[02]/[03] (also nie [01]
       Update-Request, Längen-Frame, Datenblöcke, [06] CFS-DB-Erase),
       fun 0x0B (boot/Loader->App; [00] schickt Motoren in den Bootloader),
       fun 0x01 (Reboot), fun 0x03/0x04/0x07 (Motor-Kalibrierung/Flash-Param).
    2. Whitelist (sonst nichts):
       - A1 an 0xFD/0xFC/0xFE, Daten genau [Gruppe, Gruppe]
       - A0 an 0xFD/0xFC/0xFE, Daten [Adresse] + 12 Byte UID, Adresse aus
         dem Loader-Bereich der Gruppe (Motor 0x85–0x88, Belt 0x23–0x24,
         CFS 0x01–0x04); mit Sitzung zusätzlich: UID in DIESEM Lauf als
         Loader dieser Gruppe gemeldet, Adresse nicht an andere UID vergeben
       - F0 [00]/[03]/[02] an eine Loader-Adresse; mit Sitzung zusätzlich:
         Adresse in DIESEM Lauf bestätigt
    3. Standard-Sendeschutz serial485.check_tx_frame zusätzlich – außer für
       F0 an 0x23/0x24/0x85–0x88 (dort ersetzt diese Whitelist Regel 5).
    Verstoß -> BootstartGuardError. Rückgabe: Daten als Liste."""
    data = list(data)
    hexd = " ".join("%02x" % b for b in data[:16])

    def nein(grund):
        raise BootstartGuardError(
            "Bootstart-Sendeschutz: addr 0x%02x fun 0x%02x [%s] gesperrt – %s; "
            "nichts gesendet" % (addr, fun, hexd, grund))

    # --- 1. Verbote ---------------------------------------------------
    if addr == 0xF0:
        nein("Zieladresse 0xF0 = EEPROM-Dienst des CFS-Bootloaders")
    if fun == 0xF2:
        nein("fun 0xF2 = EEPROM-Dienst (Schreiben mit Schlüssel)")
    if fun == FUN_F0 and not (len(data) == 1 and data[0] in F0_SUBS_ERLAUBT):
        if data[:1] == [0x01] and len(data) == 1:
            nein("F0 [01] = Update-Request, danach wird die App überschrieben")
        if data[:1] == [0x06] and len(data) == 1:
            nein("F0 [06] = Erase der CFS-DB-Page 0x0807F800")
        nein("F0 nur genau [00] Version, [03] Sektor, [02] Start – Länge/Datenblock/andere Sub "
             "= Flashen")
    if fun == 0x0B:
        nein("fun 0x0B (boot; [00] schickt Motoren/RFID in den Bootloader) "
             "gehört nicht zum Werkzeug")
    if fun == 0x01:
        nein("fun 0x01 = Reboot")
    if fun in (0x03, 0x04, 0x07):
        nein("fun 0x03/0x04/0x07 = Motor-Kalibrierung/Flash-Parameter")
    # --- 2. Whitelist -------------------------------------------------
    if fun == FUN_A1:
        if addr not in GRUPPEN:
            nein("A1 nur an die Gruppen 0xFD/0xFC/0xFE")
        if data != [addr, addr]:
            nein("A1 nur mit Daten [Gruppe, Gruppe] (mcu_util_485 Literal 0x%02x%02x)" % (addr, addr))
    elif sitzung is not None and sitzung.nur_erkennen:
        nein("Modus --nur-erkennen: nur A1-Discovery, kein A0/F0")
    elif fun == FUN_A0:
        if addr not in GRUPPEN:
            nein("A0 nur an die Gruppen 0xFD/0xFC/0xFE")
        if len(data) != 1 + UID_LEN:
            nein("A0 nur [Adresse] + 12 Byte UID (13 Byte)")
        if data[0] not in GRUPPEN[addr]["adressen"]:
            nein("Adresse 0x%02x nicht im Loader-Bereich der Gruppe 0x%02x (%s)"
                 % (data[0], addr, " ".join("%02x" % a for a in GRUPPEN[addr]["adressen"])))
        if sitzung is not None:
            uid = tuple(data[1:])
            if uid not in sitzung.loader_uids.get(addr, ()):
                nein("UID in diesem Lauf nicht als Loader (mode 1) dieser Gruppe gemeldet")
            alt = sitzung.adressen.get(data[0])
            if alt is not None and alt != uid:
                nein("Adresse 0x%02x ist schon an ein anderes Gerät vergeben" % data[0])
    elif fun == FUN_F0:
        if addr not in ALLE_LOADER_ADRESSEN:
            nein("F0 nur an Loader-Adressen (Motor 0x85–0x88, Belt 0x23/0x24, CFS 0x01–0x04)")
        if sitzung is not None and addr not in sitzung.adressen:
            nein("Adresse 0x%02x in diesem Lauf nicht als Loader bestätigt" % addr)
    else:
        nein("nicht in der Bootstart-Whitelist (nur A1, A0, F0 [00]/[03]/[02])")
    # --- 3. Standard-Sendeschutz zusätzlich ---------------------------
    if addr not in serial485.TX_BOOTLOADER_ADDRS:
        try:
            check_tx_frame(addr, fun, data)
        except TxGuardError as e:
            raise BootstartGuardError("Standard-Sendeschutz: %s" % e)
    elif fun != FUN_F0:   # Absicherung: an Loader-Adressen nur F0 (oben schon erzwungen)
        nein("an Loader-Adressen nur F0")
    return data


def whitelist_text():
    """Lesbare Whitelist (für CLI --hilfe/README)."""
    z = ["A1 an 0x%02x Daten [%02x %02x] (%s)" % (g, g, g, GRUPPEN[g]["name"])
         for g in GRUPPEN_REIHENFOLGE]
    z += ["A0 an 0x%02x Daten [Adr ∈ %s] + 12 Byte UID (nur in diesem Lauf gemeldete Loader-UID)"
          % (g, "/".join("%02x" % a for a in GRUPPEN[g]["adressen"])) for g in GRUPPEN_REIHENFOLGE]
    z.append("F0 [00] / [03] / [02] an in diesem Lauf bestätigte Loader-Adressen")
    return z


class Bus485Roh:
    """Minimaler RS485-Zugang NUR für dieses Werkzeug: jeder Frame läuft durch
    check_bootstart_frame (mit Sitzung). ser: pyserial-ähnlich (230400 offen).
    Rahmen: F7 addr len status fun data crc, len = n+3, CRC-8 Poly 0x07 über
    len..data (serial485.build_frame), Status 0 wie mcu_util_485 [BS §3.1]."""

    def __init__(self, ser, sitzung, uhr=time.monotonic):
        self.ser = ser
        self.sitzung = sitzung
        self.uhr = uhr
        self.gesendet = []        # alle gesendeten Frames (bytes)
        self.crc_fehler = 0
        self._letzter = b""
        self._rest = bytearray()

    def sende(self, addr, fun, data=()):
        if self.sitzung is None:
            raise BootstartGuardError("Bootstart-Sendeschutz: Senden nur mit Sitzung; nichts gesendet")
        data = check_bootstart_frame(addr, fun, data, self.sitzung)
        frame = build_frame(addr, fun, data, status=STATUS_OK)
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        self._rest = bytearray()
        self.ser.write(frame)
        self.gesendet.append(frame)
        self._letzter = frame
        return frame

    def _frames(self, ende):
        """Liefert gültige Frames (addr, status, fun, data, raw) bis ende.
        CRC-Fehler werden gezählt und übersprungen (Resync auf 0xF7), ein
        Echo des eigenen letzten Frames wird verworfen (ANNAHME: Adapter
        ohne Echo; nur Vorsicht)."""
        buf = self._rest
        while True:
            while buf:
                st, val = _scan_frame(buf)
                if st == "need":
                    break
                if st == "bad":
                    if "CRC" in str(val):
                        self.crc_fehler += 1
                    buf = bytearray(_resync(buf))
                    continue
                n, a, s, f, d = val
                raw = bytes(buf[:n])
                del buf[:n]
                if raw == self._letzter:
                    continue
                self._rest = buf
                yield a, s, f, bytes(d), raw
            if self.uhr() >= ende:
                self._rest = buf
                return
            chunk = self.ser.read(64)
            if chunk:
                buf += chunk

    def sammle(self, ende, fun):
        return [fr for fr in self._frames(ende) if fr[2] == fun]

    def warte(self, ende, passt):
        for fr in self._frames(ende):
            if passt(fr):
                return fr
        return None


class Geraet:
    """Ein per A1 gemeldetes Gerät."""

    def __init__(self, gruppe, absender, typ, mode, uid):
        self.gruppe = gruppe
        self.absender = absender
        self.typ = typ
        self.mode = mode
        self.uid = tuple(uid)
        self.adresse = None
        self.version = None
        self.sektor = None
        self.status = "neu"   # app|fremd|loader|adressiert|gestartet|fehler
        self.grund = ""

    @property
    def uid_kurz(self):
        return "…%02x%02x" % (self.uid[-2], self.uid[-1]) if len(self.uid) >= 2 else "?"

    def name(self):
        return "%s Typ %d UID %s" % (GRUPPEN[self.gruppe]["name"], self.typ, self.uid_kurz)


def _sub_frage(bus, addr, sub, timeout, versuche, uhr):
    """F0 [sub] an addr; Wiederholung nur ohne Antwort (Stock: do{..}while
    (ret && ++i != 3), BS §3.3). Antwort: Absender == addr, fun 0xF0."""
    for _ in range(versuche):
        bus.sende(addr, FUN_F0, [sub])
        fr = bus.warte(uhr() + timeout, lambda f: f[0] == addr and f[2] == FUN_F0)
        if fr is not None:
            return fr
    return None


def rs485_bootstart(bus, log, gruppen=GRUPPEN_REIHENFOLGE, a1_runden=A1_RUNDEN,
                    schlafe=time.sleep, uhr=time.monotonic, nachkontrolle=True,
                    zeiten=None):
    """Kompletter RS485-Lauf. bus: Bus485Roh mit Sitzung.
    Rückgabe dict(geraete=[Geraet], ok=bool, crc_fehler=int)."""
    z = {"a1_fenster_s": A1_FENSTER_S, "a1_pause_s": A1_PAUSE_S, "a0_timeout_s": A0_TIMEOUT_S,
         "f0_timeout_s": F0_TIMEOUT_S, "nachlauf_s": NACHLAUF_S}
    z.update(zeiten or {})
    sitzung = bus.sitzung
    alle = []
    for g in gruppen:
        cfg = GRUPPEN[g]
        log("  %s: A1 an 0x%02x (bis %d Runden)" % (cfg["name"], g, a1_runden))
        # --- 1. Discovery -------------------------------------------
        gef = []
        for runde in range(1, a1_runden + 1):
            bus.sende(g, FUN_A1, [g, g])
            neu = 0
            ende = uhr() + z["a1_fenster_s"] + z["a1_pause_s"]
            for absender, _s, _f, d, _raw in bus.sammle(ende, FUN_A1):
                if len(d) < 2 + UID_LEN:
                    log("    A1-Antwort von 0x%02x zu kurz (%d Byte) – ignoriert" % (absender, len(d)))
                    continue
                typ, mode, uid = d[0], d[1], tuple(d[2:2 + UID_LEN])
                if any(x.typ == typ and x.uid == uid for x in gef):
                    continue   # Stock: „duplicate uuid, skip!“ [FW §3.2 Schritt 1]
                gef.append(Geraet(g, absender, typ, mode, uid))
                neu += 1
            log("    Runde %d: %d neue Antwort(en)" % (runde, neu))
            if sum(1 for x in gef if x.typ in cfg["typen"]) >= cfg["ziel"]:
                break
        # --- Einordnen ----------------------------------------------
        belegt = set()
        for x in gef:
            if x.typ not in cfg["typen"]:
                x.status, x.grund = "fremd", "Typ %d gehört nicht zur Gruppe – nicht angefasst" % x.typ
            elif x.mode == MODE_APP:
                x.status, x.grund = "app", "in der App (mode 0) – nicht angefasst"
            elif x.mode != MODE_LOADER:
                x.status, x.grund = "fremd", "mode %d unbekannt – nicht angefasst" % x.mode
            elif x.absender == g:
                x.status = "loader"
                sitzung.melde_loader(g, x.uid)
            elif x.absender in cfg["adressen"] and not cfg["a0_immer"]:
                # schon adressierter Loader: Stock sendet dann kein A0 [BS §3.2]
                if x.absender in belegt:
                    x.status, x.grund = "fehler", "Loader-Adresse 0x%02x doppelt" % x.absender
                else:
                    x.status, x.adresse = "adressiert", x.absender
                    belegt.add(x.absender)
                    sitzung.bestaetige(x.absender, x.uid)
            else:
                x.status, x.grund = "fremd", ("Antwort von 0x%02x (unerwartet) – nicht angefasst"
                                              % x.absender)
            log("    %s: mode %d, Absender 0x%02x -> %s%s"
                % (x.name(), x.mode, x.absender, x.status, (" (%s)" % x.grund) if x.grund else ""))
        if sitzung.nur_erkennen:
            for x in gef:
                if x.status in ("loader", "adressiert"):
                    x.status, x.grund = "loader", "im Bootloader (mode 1); --nur-erkennen: nicht gestartet"
            alle.extend(gef)
            continue
        # --- 2. Adressvergabe A0 (nur Loader) -------------------------
        frei = [a for a in cfg["adressen"] if a not in belegt]
        for x in gef:
            if x.status != "loader":
                continue
            if not frei:
                x.status, x.grund = "fehler", "keine freie Loader-Adresse"
                log("    %s: keine freie Loader-Adresse – nicht angefasst" % x.name())
                continue
            a = frei.pop(0)   # auch bei Fehlschlag verbraucht (sicherer als Stock, BS §4)
            fr = None
            for _ in range(cfg["a0_versuche"]):
                bus.sende(g, FUN_A0, [a] + list(x.uid))
                fr = bus.warte(uhr() + z["a0_timeout_s"],
                               lambda f, a=a, u=x.uid: f[0] == a and f[2] == FUN_A0
                               and len(f[3]) >= 2 + UID_LEN and tuple(f[3][2:2 + UID_LEN]) == u)
                if fr is not None:
                    break
            if fr is None:
                x.status, x.grund = "fehler", "A0 -> 0x%02x ohne passende Antwort" % a
                log("    %s: A0 -> 0x%02x ohne passende Antwort – nicht weiter angefasst" % (x.name(), a))
                continue
            x.status, x.adresse = "adressiert", a
            sitzung.bestaetige(a, x.uid)
            log("    %s: A0 -> 0x%02x ok" % (x.name(), a))
        alle.extend(gef)
    # --- 3./4. Version und Sektor (alle adressierten) ---------------
    adressiert = [x for x in alle if x.status == "adressiert"]
    for x in adressiert:
        fr = _sub_frage(bus, x.adresse, SUB_VERSION, z["f0_timeout_s"], F0_VERSUCHE, uhr)
        if fr is None:
            x.grund = "Version: keine Antwort"
        elif len(fr[3]) < VERSION_LEN:
            x.grund = "Version: Antwort zu kurz (%d Byte)" % len(fr[3])
        else:
            x.version = version_bereinigen(fr[3][:VERSION_LEN])
        log("    0x%02x F0 [00] Version: %s" % (x.adresse, x.version or x.grund))
    for x in adressiert:
        fr = _sub_frage(bus, x.adresse, SUB_SEKTOR, z["f0_timeout_s"], F0_VERSUCHE, uhr)
        if fr is not None and fr[3]:
            x.sektor = fr[3][0]
        log("    0x%02x F0 [03] Sektor: %s" % (x.adresse, ("0x%02x" % x.sektor)
                                               if x.sektor is not None else "keine Antwort"))
    # --- 5. Start --------------------------------------------------
    gestartet = []
    for x in adressiert:
        ok, grund = version_pruefen(x.version, GRUPPEN[x.gruppe]["praefix"])
        if not ok:
            x.status, x.grund = "fehler", "KEIN Start: %s" % (x.grund or grund)
            log("    0x%02x %s (bleibt im Loader)" % (x.adresse, x.grund))
            continue
        fr = _sub_frage(bus, x.adresse, SUB_START, z["f0_timeout_s"], F0_VERSUCHE, uhr)
        if fr is None:
            x.status, x.grund = "fehler", "Start: keine Antwort"
        elif fr[3][:1] == bytes([F0_ACK]):
            x.status = "gestartet"
            gestartet.append(x)
        else:
            code = fr[3][0] if fr[3] else None
            x.status, x.grund = "fehler", "Start abgelehnt: %s" % (
                F0_CODES.get(code, "0x%02x" % code) if code is not None else "leere Antwort")
        log("    0x%02x F0 [02] Start: %s" % (x.adresse, "ok (75)" if x.status == "gestartet"
                                              else x.grund))
    # --- Nachkontrolle: Loader noch da? (F0 [00], 1 Versuch) ---------
    if nachkontrolle and gestartet:
        schlafe(z["nachlauf_s"])
        for x in gestartet:
            fr = _sub_frage(bus, x.adresse, SUB_VERSION, z["f0_timeout_s"], 1, uhr)
            if fr is not None and len(fr[3]) >= VERSION_LEN:
                # nur eine echte 25-Byte-Versionsantwort zählt als „Loader noch da“
                x.status, x.grund = "fehler", "nach dem Start antwortet noch der Loader"
                log("    0x%02x Nachkontrolle: Loader antwortet noch -> NICHT gestartet" % x.adresse)
            elif fr is not None:
                log("    0x%02x Nachkontrolle: Antwort ohne Version (Status 0x%02x, %d Byte) – "
                    "kein Loader (vermutlich App)" % (x.adresse, fr[1], len(fr[3])))
            else:
                log("    0x%02x Nachkontrolle: Loader schweigt (App läuft vermutlich)" % x.adresse)
    ok = not any(x.status in ("fehler", "loader") for x in alle)
    return {"geraete": alle, "ok": ok, "crc_fehler": bus.crc_fehler}


def plan_rs485(gruppen=GRUPPEN_REIHENFOLGE, beispiel_uid=tuple(range(1, 13)), nur_erkennen=False):
    """Trockenlauf: [(Text, bytes|None)]. A0 mit Beispiel-UID (echte UID
    kommt aus der A1-Antwort). Jeder Frame läuft durch check_bootstart_frame
    (statisch, ohne Sitzung)."""
    plan = []
    for g in gruppen:
        cfg = GRUPPEN[g]
        plan.append(("Discovery %s: A1 an 0x%02x, bis %d Runden, Fenster %.1f s + %.1f s"
                     % (cfg["name"], g, A1_RUNDEN, A1_FENSTER_S, A1_PAUSE_S),
                     build_frame(g, FUN_A1, check_bootstart_frame(g, FUN_A1, [g, g]))))
    if nur_erkennen:
        plan.append(("--nur-erkennen: ENDE nach der Discovery (mode je Gerät melden); "
                     "kein A0, kein F0", None))
        return plan
    plan.append(("Nur Geräte mit mode 1 (Loader); Geräte in der App: nichts", None))
    for g in gruppen:
        cfg = GRUPPEN[g]
        a = cfg["adressen"][0]
        d = check_bootstart_frame(g, FUN_A0, [a] + list(beispiel_uid))
        plan.append(("Adressvergabe %s: A0 an 0x%02x [Adr %s] + UID (Beispiel-UID; %d Versuch(e) "
                     "à %.1f s)" % (cfg["name"], g, "/".join("%02x" % x for x in cfg["adressen"]),
                                    cfg["a0_versuche"], A0_TIMEOUT_S),
                     build_frame(g, FUN_A0, d)))
    for g in gruppen:
        a = GRUPPEN[g]["adressen"][0]
        for sub, was in ((SUB_VERSION, "Version"), (SUB_SEKTOR, "Sektorgröße"),
                         (SUB_START, "Start (nur bei gültiger Version)")):
            plan.append(("F0 [%02x] %s an 0x%02x (je Loader-Adresse; %d x %.1f s)"
                         % (sub, was, a, F0_VERSUCHE, F0_TIMEOUT_S),
                         build_frame(a, FUN_F0, check_bootstart_frame(a, FUN_F0, [sub]))))
    plan.append(("Nachkontrolle: %.1f s warten, F0 [00] je gestarteter Adresse (1 Versuch)"
                 % NACHLAUF_S, None))
    return plan


# =====================================================================
# Selbsttest (ohne Hardware; ausführlich: pi-host/install/test_k2_bootstart.py)
# =====================================================================
def selftest():
    n = [0, 0]

    def check(name, cond):
        n[0] += 1
        if cond:
            print("ok %s" % name)
        else:
            n[1] += 1
            print("FAIL %s" % name)

    check("UART ~Σ: ~0x75 = 0x8a", uart_pruefsumme(b"\x75") == 0x8A)
    for b in UART_ERLAUBT:
        check_uart_tx(b)
    for b in (b"\x01\xfe", b"\x03\xfc", b"\x04\xfb", b"\x05\xfa", b"\x00\x00\x00\x00\xff"):
        try:
            check_uart_tx(b)
            check("UART %s gesperrt" % b.hex(), False)
        except BootstartGuardError:
            check("UART %s gesperrt" % b.hex(), True)
    check("A1 an 0xFD byte-genau",
          build_frame(0xFD, FUN_A1, check_bootstart_frame(0xFD, FUN_A1, [0xFD, 0xFD]))
          == bytes([0xF7, 0xFD, 0x05, 0x00, 0xA1, 0xFD, 0xFD, serial485.crc8_cal([5, 0, 0xA1, 0xFD, 0xFD])]))
    for a, f, d in ((0x85, 0xF0, [0x01]), (0x01, 0xF0, [0x06]), (0xF0, 0xF2, [1]),
                    (0xFF, 0x0B, [0x00]), (0xFD, 0x01, [0]), (0x81, 0x03, []),
                    (0x85, 0xF0, [0, 0, 0, 0]), (0x81, 0xF0, [0x02]), (0xFD, 0xA1, [0xFD, 0x01])):
        try:
            check_bootstart_frame(a, f, d)
            check("gesperrt 0x%02x/0x%02x %s" % (a, f, d), False)
        except BootstartGuardError:
            check("gesperrt 0x%02x/0x%02x %s" % (a, f, d), True)
    s = Sitzung()
    try:
        check_bootstart_frame(0x85, FUN_F0, [0x02], s)
        check("F0 an unbestätigte Adresse gesperrt", False)
    except BootstartGuardError:
        check("F0 an unbestätigte Adresse gesperrt", True)
    s.bestaetige(0x85, range(12))
    check("F0 [02] an bestätigte Loader-Adresse erlaubt",
          check_bootstart_frame(0x85, FUN_F0, [0x02], s) == [0x02])
    check("Version-Prüfung", version_pruefen("mot2_023_C30-mot2_002_081", ("mot",))[0]
          and not version_pruefen("cfs0_050_G32-000000000000", ("cfs",))[0]
          and not version_pruefen("mcu0_140_G32-mcu0_000_000", ("mcu0_",))[0])
    check("Plan RS485/UART baubar", len(plan_rs485()) > 10 and len(plan_uart("main")) == 5)
    print("SELBSTTEST: %d/%d ok" % (n[0] - n[1], n[0]))
    return n[1]


if __name__ == "__main__":
    raise SystemExit(selftest())
