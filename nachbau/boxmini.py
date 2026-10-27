#!/usr/bin/env python3
"""boxmini.py – Python-Nachbau von box_wrapper (CFS-Box, K2 Plus, Kernfunktionen).

Ersetzt: box_wrapper.cpython-39.so (armhf, CPython 3.9, proprietär).

Umfang:
- get_rfid: RFID-Tag eines Slots lesen (Func 0x02)
- has_filament: Belegung pro Slot aus Restmenge (Func 0x03): True oder
  None (unbekannt) – remain 0 belegt KEIN „leer“ (s. has_filament)
- remain percent: Restmenge in Prozent (Func 0x03)

Belege (aus der Protokollanalyse):
- Func-Codes + ASCII-Format: RS485-Mitschnitte des Originals
- `A:<hex>;` / `B:unknown;` / `C:busy;`: Live-Frames 12.10.2026
- GET_REMAIN_LEN: Slotmaske im Request, Antwort positional A..D;
  0xff = kein Wert, 0 = kein RFID-Restwert (Live: 1a 00 ff 00 = A 26%;
  Slot B mit remain 0 hat live 160 mm gefördert, Testlauf T1B)
  PRÄZISIERT 15.10.2026 (FW-Analyse CFS-FW R153 FUN_0800ef66):
  Wire-0xff = nicht in Maske / Slot preloadet gerade /
  Filament da, aber KEINE RFID-Daten; Wire-0 = Sensor A meldet kein Filament
  ODER gespeicherter Prozentwert 0. Beide Werte bleiben für has_filament
  mehrdeutig -> Logik unverändert (True nur bei >0, sonst None).
- Klipper-G-Code NUM=0..3 numerisch (FALLE: NUM=A wirft key60-Shutdown).
  Wire/.so-Parameter num ist dagegen eine MASKE (box_wrapper-API:
  communication_get_rfid(1, 1) -> data b'\x01'). BoxMini.get_rfid/
  has_filament nehmen bewusst einen Slot-INDEX 0..3 und wandeln ihn per
  slot_to_mask() um; get_remain nimmt eine Maske.

- LIVE 15.10.2026 (Live-Logs 05.-16.10.): Original sendet 0x02/0x03 mit
  retries=4 (= 5 Sendungen, je 2 s Timeout) -> hier explizit RETRIES_OPERATIONAL.
  Live 0x03 nach Herausziehen von A: `00 ff 1d 00` (A=0 = Sensor A leer),
  Filament ohne Tag: 0xff – bestätigt die FW-Deutung oben.

Benutzung: Serial485-Instanz aus serial485.py übergeben (echte Hardware, pyserial).
Der Selbsttest unten läuft OHNE Hardware (Fake-Transport mit Live-Frames).
"""

from serial485 import Serial485  # noqa: F401 (für echten Betrieb)

# Func-Codes CFS/Box, Addr 0x01 (Beleg: Protokollanalyse)
FUNC_GET_RFID = 0x02
FUNC_GET_REMAIN_LEN = 0x03
BOX_ADDR = 0x01
STATUS_OPERATIONAL = 0xFF
TIMEOUT_OPERATIONAL = 2.0
RETRIES_OPERATIONAL = 4  # wie Original (live retries=4 für 0x02/0x03, 15.10.2026)
NO_DATA = 0xFF
SLOT_KEYS = "ABCD"  # Slot-Index 0..3 -> A/B/C/D (Beleg: Live-Frames)
SLOT_MASKS = (0x01, 0x02, 0x04, 0x08)


# slot_to_mask: Slot-Index 0..3 (A..D) -> 1-hot-Maske 0x01/0x02/0x04/0x08.
# Einzige Implementierung in cfsseq.py (Review G6, 16.10.2026; cfsseq
# importiert nur serial485 -> kein Zirkelimport). Name bleibt hier exportiert.
from cfsseq import slot_to_mask  # noqa: E402,F401


def parse_rfid_response(data):
    """Parst GET_RFID-Antwort (ASCII, ';'-getrennt, Key=Slot-Buchstabe).

    Beispiele (live, anonymisiert): b"A:10125000...000000;" / b"B:unknown;" / b"C:busy;"
    Gibt dict {Slot-Key: Wert} zurück. Leere Antwort -> {}.
    """
    result = {}
    for part in bytes(data).decode("ascii", "replace").split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, _, value = part.partition(":")
        key, value = key.strip(), value.strip()
        if len(key) == 1 and key in SLOT_KEYS:
            result[key] = value
    return result


def parse_remain_response(data):
    """Parst GET_REMAIN_LEN-Antwort: 4 Bytes A,B,C,D (0-100 %, 0xff = nicht ausgewählt).

    Beleg: Live-Frame f7 01 07 00 03 1a 00 ff 00 99 -> A=26 %.
    Gibt dict {Slot-Key: Prozent oder None} zurück. Genau 4 Bytes nötig,
    sonst ValueError (z.B. leere Antwort bei Fehler-Status).
    """
    raw = bytes(data)
    if len(raw) != 4:
        raise ValueError("Remain-Antwort muss 4 Bytes haben: %r" % (raw,))
    return {key: (None if raw[i] == NO_DATA else raw[i])
            for i, key in enumerate(SLOT_KEYS)}


class BoxMini:
    """CFS-Box (Kernfunktionen): RFID + Belegung + Restmenge."""

    def __init__(self, bus, addr=BOX_ADDR):
        self.bus = bus
        self.addr = addr

    def get_rfid(self, slot=0):
        """RFID-Tag von Slot-INDEX 0..3 (A..D) lesen.

        Komfort-API (nicht die .so-Signatur): der Index wird per
        slot_to_mask() in die 1-hot-Slotmaske des Requests umgesetzt. Die
        Antwort ist ASCII (`A:...;`). STATUS=0xff ist der Operational-Modus.
        """
        mask = slot_to_mask(slot)
        raw = self.bus.transact(self.addr, FUNC_GET_RFID, [mask],
                                status=STATUS_OPERATIONAL,
                                timeout=TIMEOUT_OPERATIONAL,
                                retries=RETRIES_OPERATIONAL)
        parsed = parse_rfid_response(raw)
        return parsed.get(SLOT_KEYS[slot])

    def get_remain(self, mask=0x0F):
        """Restmengen der ausgewählten Slots: {A/B/C/D: Prozent oder None}.

        Request: eine 1-hot-/Mehrfach-Slotmaske; Antwort positional A..D,
        0xff bedeutet „kein Wert“, 0 bedeutet „kein RFID-Restwert“ (NICHT
        sicher leer, s. has_filament).
        """
        if isinstance(mask, bool) or not isinstance(mask, int) \
                or not 0 <= mask <= 0x0F:
            raise ValueError("Maske muss 0x00..0x0f sein")
        raw = self.bus.transact(self.addr, FUNC_GET_REMAIN_LEN, [mask],
                                status=STATUS_OPERATIONAL,
                                timeout=TIMEOUT_OPERATIONAL,
                                retries=RETRIES_OPERATIONAL)
        return parse_remain_response(raw)

    def has_filament(self, slot=0):
        """Belegung von Slot-INDEX 0..3 aus der Restmenge ableiten.

        Rückgabe: True  = Restmenge > 0 (RFID-Restwert vorhanden -> belegt);
                  None  = unbekannt (remain 0 oder 0xff).
        Bewusst NIE False: remain 0 heißt nur „kein RFID-Restwert“ – live
        hat Slot B mit remain 0 (Vendor unknown) 160 mm gefördert
        (Testlauf T1B; „remain 0“ war dort ein Host-Wert).
        Echte Leer-Erkennung bräuchte den Filament-Sensorstatus – laut FW-Analyse
        (15.10.2026) liefert 0x08 [00] eine Bitmaske Sensor A; LIVE BELEGT
        15.10.2026 (Host-Name GET_FILAMENT_SENSOR_STATE, [00] -> 07 bei
        A,B,C belegt / D leer, deckungsgleich mit remain D 00) -> nutzbar
        über box.BoxAction.communication_get_filament_sensor_state; hier
        bewusst nicht eingebaut (BoxMini bleibt minimal).
        Ungültiger Index -> ValueError.
        """
        mask = slot_to_mask(slot)
        remain = self.get_remain(mask)[SLOT_KEYS[slot]]
        return True if (remain is not None and remain > 0) else None


# --- Selbsttest mit Live-Frames (keine Hardware nötig) ---
class _FakeBus:
    """Spielt aufgezeichnete Antworten ab (Framing wie Serial485.transact)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def transact(self, addr, fun, data=(), **kwargs):
        self.calls.append((addr, fun, list(data), dict(kwargs)))
        from serial485 import parse_frame
        raw = self.answers.pop(0)
        rfun, rdata = parse_frame(raw)
        assert rfun == fun, (rfun, fun)
        return rdata


def selftest():
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("ok " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    # 1. RFID-Parsing (Live-Frames 12.10.2026)
    check("rfid A", parse_rfid_response(
        bytes.fromhex("41 3a 31 30 31 32 35")) == {"A": "10125"})
    check("rfid unknown", parse_rfid_response(b"B:unknown;") == {"B": "unknown"})
    check("rfid busy", parse_rfid_response(b"C:busy;") == {"C": "busy"})
    check("rfid leer", parse_rfid_response(b"") == {})

    # 2. Remain-Parsing (Live: A=26 %, B=0, C/D=n/a)
    check("remain", parse_remain_response(bytes.fromhex("1a 00 ff 00")) ==
          {"A": 26, "B": 0, "C": None, "D": 0})

    # 3. BoxMini gegen FakeBus mit echten Live-Response-Frames
    bus = _FakeBus([
        bytes.fromhex("f7 01 07 00 03 1a 00 ff 00 99"),  # GET_REMAIN_LEN
        bytes.fromhex("f7 01 07 00 03 1c ff 00 00 11"),  # GET_REMAIN_LEN (A=28)
    ])
    box = BoxMini(bus)
    check("box remain", box.get_remain() == {"A": 26, "B": 0, "C": None, "D": 0})
    check("remain request mask", bus.calls[0][2] == [0x0F] and
          bus.calls[0][3]["status"] == STATUS_OPERATIONAL
          and bus.calls[0][3]["retries"] == RETRIES_OPERATIONAL == 4)
    check("box filament A", box.has_filament(0) is True
          and bus.calls[1][2] == [0x01])

    # 4. has_filament: remain 0 = unbekannt (Slot B live 160 mm trotz 0)
    bus = _FakeBus([
        bytes.fromhex("f7 01 07 00 03 1a 00 ff 00 99"),  # B = 0
        bytes.fromhex("f7 01 07 00 03 1c ff 00 00 11"),  # B = 0xff
    ])
    box = BoxMini(bus)
    check("filament B remain 0 -> None", box.has_filament(1) is None
          and bus.calls[0][2] == [0x02])
    check("filament B 0xff -> None", box.has_filament(1) is None)
    for bad in (-1, 4, True):
        try:
            box.has_filament(bad)
            check("filament index-guard %r" % (bad,), False)
        except ValueError:
            check("filament index-guard %r" % (bad,), True)
    try:
        parse_remain_response(bytes.fromhex("1a 00 ff"))
        check("remain laengen-guard", False)
    except ValueError:
        check("remain laengen-guard", True)
    # 5. LIVE 15.10.2026: remain nach Herausziehen A (Live-Log 13.10.2026)
    check("live remain 00 ff 1d 00", parse_remain_response(
        bytes.fromhex("00 ff 1d 00")) == {"A": 0, "B": None, "C": 29, "D": 0})
    bus = _FakeBus([bytes.fromhex("f7 01 07 00 03 00 ff 1d 00 20")])
    check("live remain frame A=0 -> has_filament None",
          BoxMini(bus).has_filament(0) is None)
    check("slot_to_mask", [slot_to_mask(i) for i in range(4)]
          == [0x01, 0x02, 0x04, 0x08])
    print("SELBSTTEST: %s" % ("ok" if fails == 0 else "%d FEHLER" % fails))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
