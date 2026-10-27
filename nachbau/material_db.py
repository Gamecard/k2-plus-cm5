#!/usr/bin/env python3
"""material_db.py – Lader und Abfragen für die Creality-Materialdatenbank
`material_database.json` (K2 Plus, FW 1.1.7.0).

DATEIEN (Stock)
  - Stock-Default (Firmware): /etc/sysConfig/defData/material/material_database.json
  - Arbeitskopie, die der Stock-Host-Code LIEST:
    creality/userdata/box/material_database.json (Strings in
    filament_rack_wrapper.so und box_wrapper.so). Format = Antwort der
    Creality-Cloud: {"code", "msg", "reqId", "result": {"list", "count",
    "version"}}.

STRUKTUR (je Eintrag in result.list)
  engineVersion (str), printerIntName (str, "F008"), nozzleDiameter ([str]),
  base    – Katalog: id (5 Zeichen), name, brand, meterialType (sic!),
            minTemp/maxTemp (int, °C), density (float), diameter (str) …
  kvParam – Slicer-Profil (Orca-Schlüssel, ALLE Werte als str, "nil" = leer):
            nozzle_temperature, nozzle_temperature_range_low/high,
            filament_max_volumetric_speed (mm³/s), filament_type …

STOCK-SEMANTIK (B = belegt aus dem Stock-Host-Code, hier nachgebildet)
  - Schlüssel: base.id == type[1:6] (6-stelliger RFID-Materialcode, z. B.
    "1" + 5-stellige ID) bzw. rfid[12:17] (40-Zeichen-RFID-vender).
    Erster Treffer gewinnt (Schleife mit break); printerIntName und
    nozzleDiameter prüft der Stock NICHT (A: aus dem Code der Lookups).
  - Temperatur:  int(kvParam.nozzle_temperature), max=True -> int(base.maxTemp);
    Fehlschlag -> Fallback self.temp (Config-Default 220).
  - Geschwindigkeit: float(kvParam.filament_max_volumetric_speed) / 2.4 * 60
    (mm/min); Fehlschlag -> self.velocity (360) OHNE *60.
  - Typ: base.meterialType, Fehlschlag -> None.
  - RESUME-Vorextrusion (Stock RESUME_EXTERNAL, gcode_macro.cfg live :914):
    remain_material_velocity * 0.8.

Reine Funktionen, keine Klipper-/Bus-Abhängigkeit, keine eingebetteten
Benutzerdaten. Selbsttest mit synthetischer Mini-DB:
    python3 material_db.py               (= --selftest)
    python3 material_db.py --struktur <datei>   nur Zählwerte, keine Inhalte
    python3 material_db.py --tabelle <datei>    Markdown-Tabelle der genutzten Felder
"""

import json
import sys

# Konstanten (Stock-Belege s. Kopf)
VOLUMETRIC_DIVISOR = 2.4      # ≈ Querschnitt 1,75-mm-Filament in mm²
PER_MINUTE = 60
RESUME_PRIME_FACTOR = 0.8     # RESUME_EXTERNAL: remain_material_velocity * 0.8
FALLBACK_TEMP = 220           # [filament_rack] temp (Default 220)
FALLBACK_VELOCITY = 360       # [filament_rack] velocity (Default 360, ohne *60)
DIFF_TEMP_K = 10              # is_different_type_material: |dT| > 10
DB_ID_LEN = 5
TYPE_LEN = 6
RFID_LEN = 40
RFID_DBID_SLICE = slice(12, 17)
TYPE_DBID_SLICE = slice(1, 6)

# Felder, die der Stock-Host-Code nachweislich liest (Strings in
# box_wrapper.so / filament_rack_wrapper.so)
USED_BASE = ("id", "meterialType", "maxTemp")
USED_KV = ("nozzle_temperature", "filament_max_volumetric_speed")


class MaterialDBError(ValueError):
    """Datei hat nicht die erwartete Grundstruktur (result.list)."""


def _to_int(value):
    """int() wie Stock; None/"nil"/kaputt -> None (Stock: except -> Fallback)."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def db_id_from_key(key):
    """Suchschlüssel -> 5-stellige base.id.

    - 40 Zeichen (RFID-vender) -> rfid[12:17] (Stock check_rfid_valid)
    - 6 Zeichen (RFID-Materialcode)  -> type[1:6] (Stock get_material_*)
    - 5 Zeichen                      -> unverändert (direkte ID)
    'None', '-1', None, sonstige Längen -> None (Stock: 'failed to obtain
    consumable type' bzw. kein Treffer)."""
    if not isinstance(key, str) or key in ("None", "-1"):
        return None
    if len(key) == RFID_LEN:
        return key[RFID_DBID_SLICE]
    if len(key) == TYPE_LEN:
        return key[TYPE_DBID_SLICE]
    if len(key) == DB_ID_LEN:
        return key
    return None


class MaterialDB:
    """Geparste material_database.json (dict) mit Stock-gleichen Abfragen."""

    def __init__(self, data):
        if not isinstance(data, dict):
            raise MaterialDBError("Wurzel ist kein Objekt")
        result = data.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("list"), list):
            raise MaterialDBError("result.list fehlt oder ist keine Liste")
        self.data = data
        self.list = result["list"]

    @classmethod
    def load(cls, path):
        with open(path, "r") as f:
            return cls(json.load(f))

    def __len__(self):
        return len(self.list)

    # --- Suche ---------------------------------------------------------
    def find(self, key):
        """Erster Eintrag mit base.id == db_id_from_key(key), sonst None."""
        dbid = db_id_from_key(key)
        if dbid is None:
            return None
        for entry in self.list:
            if not isinstance(entry, dict):
                continue
            base = entry.get("base")
            if isinstance(base, dict) and base.get("id") == dbid:
                return entry
        return None

    @staticmethod
    def _kv(entry, name):
        kv = entry.get("kvParam") if isinstance(entry, dict) else None
        return kv.get(name) if isinstance(kv, dict) else None

    @staticmethod
    def _base(entry, name):
        base = entry.get("base") if isinstance(entry, dict) else None
        return base.get(name) if isinstance(base, dict) else None

    # --- Stock-Abfragen ------------------------------------------------
    def nozzle_temp(self, key, fallback=FALLBACK_TEMP):
        """get_material_target_temp(max=False): int(kvParam.nozzle_temperature)."""
        t = _to_int(self._kv(self.find(key), "nozzle_temperature"))
        return fallback if t is None else t

    def max_temp(self, key, fallback=FALLBACK_TEMP):
        """get_material_target_temp(max=True): int(base.maxTemp)."""
        t = _to_int(self._base(self.find(key), "maxTemp"))
        return fallback if t is None else t

    def min_temp(self, key, fallback=None):
        """base.minTemp (vom Stock-Host-Code NICHT nachweislich gelesen)."""
        t = _to_int(self._base(self.find(key), "minTemp"))
        return fallback if t is None else t

    def temp_range(self, key):
        """(nozzle_temperature_range_low, _high) als int oder None (Slicer-Feld)."""
        e = self.find(key)
        lo = _to_int(self._kv(e, "nozzle_temperature_range_low"))
        hi = _to_int(self._kv(e, "nozzle_temperature_range_high"))
        return None if lo is None or hi is None else (lo, hi)

    def max_volumetric_speed(self, key):
        """kvParam.filament_max_volumetric_speed in mm³/s, None = unbekannt."""
        return _to_float(self._kv(self.find(key), "filament_max_volumetric_speed"))

    def extrusion_speed(self, key, fallback=FALLBACK_VELOCITY):
        """get_material_target_speed: vol / 2.4 * 60 (mm/min); Fallback ohne *60."""
        v = self.max_volumetric_speed(key)
        if v is None:
            return fallback
        return v / VOLUMETRIC_DIVISOR * PER_MINUTE

    def resume_prime_speed(self, key, fallback=FALLBACK_VELOCITY):
        """RESUME_EXTERNAL: remain_material_velocity * 0.8 (mm/min)."""
        return self.extrusion_speed(key, fallback) * RESUME_PRIME_FACTOR

    def material_type(self, key):
        """get_material_type: base.meterialType, sonst None."""
        t = self._base(self.find(key), "meterialType")
        return t if isinstance(t, str) else None

    def is_different_type(self, last_key, current_key):
        """is_different_type_material (Teilstring-Vergleich, sonst |dT| > 10)."""
        lt, ct = self.material_type(last_key), self.material_type(current_key)
        if lt is None or ct is None:
            return False
        if lt not in ct and ct not in lt:
            return True
        return abs(self.nozzle_temp(last_key) - self.nozzle_temp(current_key)) > DIFF_TEMP_K

    # --- VORSCHLAG (nicht Stock-belegt) ---------------------------------
    def change_temp_proposal(self, next_key, current_target, fallback=FALLBACK_TEMP):
        """Vorschlag für pi-host: Wechsel-Temperatur =
        max(aktuelles Ziel, nozzle_temperature des NEUEN Materials) statt
        max(Ziel, 220). Unbekanntes Material -> max(Ziel, fallback)."""
        return max(float(current_target or 0.0), float(self.nozzle_temp(next_key, fallback)))

    # --- Auswertung ohne Inhalte -----------------------------------------
    def structure_report(self):
        """Nur Zählwerte (für die Benutzer-Kopie geeignet, keine Inhalte)."""
        ids = [self._base(e, "id") for e in self.list if isinstance(e, dict)]
        rep = {
            "eintraege": len(self.list),
            "count_feld": self.data.get("result", {}).get("count"),
            "ids_eindeutig": len(set(ids)),
            "ids_doppelt": len(ids) - len(set(ids)),
            "id_laengen": sorted({len(i) for i in ids if isinstance(i, str)}),
            "printerIntName": len({e.get("printerIntName") for e in self.list if isinstance(e, dict)}),
            "nozzleDiameter": len({tuple(e.get("nozzleDiameter") or ()) for e in self.list if isinstance(e, dict)}),
            "kvParam_anzahl_min": min((len(e.get("kvParam") or {}) for e in self.list
                                       if isinstance(e, dict)), default=0),
            "kvParam_anzahl_max": max((len(e.get("kvParam") or {}) for e in self.list
                                       if isinstance(e, dict)), default=0),
            "keine_objekte": sum(1 for e in self.list if not isinstance(e, dict)),
        }
        for name in USED_KV:
            rep["unlesbar_kv." + name] = sum(
                1 for e in self.list if _to_float(self._kv(e, name)) is None)
        for name in ("maxTemp", "meterialType"):
            rep["fehlt_base." + name] = sum(
                1 for e in self.list if self._base(e, name) in (None, ""))
        return rep

    def rows(self):
        """Je Eintrag die vom Stock genutzten Felder + abgeleitete Werte."""
        out = []
        for e in self.list:
            dbid = self._base(e, "id")
            vol = _to_float(self._kv(e, "filament_max_volumetric_speed"))
            speed = None if vol is None else vol / VOLUMETRIC_DIVISOR * PER_MINUTE
            out.append({
                "id": dbid, "name": self._base(e, "name"),
                "meterialType": self._base(e, "meterialType"),
                "nozzle_temperature": _to_int(self._kv(e, "nozzle_temperature")),
                "range": (self._kv(e, "nozzle_temperature_range_low"),
                          self._kv(e, "nozzle_temperature_range_high")),
                "minTemp": _to_int(self._base(e, "minTemp")),
                "maxTemp": _to_int(self._base(e, "maxTemp")),
                "vol": vol, "speed": speed,
                "prime": None if speed is None else speed * RESUME_PRIME_FACTOR,
            })
        return out


def _fmt(v, nd=0):
    if v is None:
        return "–"
    if isinstance(v, float):
        return ("%%.%df" % nd) % v
    return str(v)


def markdown_table(db):
    lines = ["| id | Name | meterialType | nozzle_temperature | Bereich low–high | minTemp | maxTemp "
             "| vol. mm³/s | Speed mm/min (vol/2,4·60) | RESUME-Prime ·0,8 |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for r in db.rows():
        lines.append("| %s | %s | %s | %s | %s–%s | %s | %s | %s | %s | %s |" % (
            r["id"], r["name"], r["meterialType"], _fmt(r["nozzle_temperature"]),
            _fmt(r["range"][0]), _fmt(r["range"][1]), _fmt(r["minTemp"]),
            _fmt(r["maxTemp"]), _fmt(r["vol"], 0), _fmt(r["speed"], 1), _fmt(r["prime"], 1)))
    return "\n".join(lines)


# =====================================================================
# Selbsttest (synthetische Mini-DB, keine echten Inhalte)
# =====================================================================

def _mini_db():
    def entry(dbid, mtype, temp, tmax, vol, kv_extra=None):
        kv = {"nozzle_temperature": temp, "filament_max_volumetric_speed": vol,
              "nozzle_temperature_range_low": "190", "nozzle_temperature_range_high": str(tmax)}
        kv.update(kv_extra or {})
        return {"engineVersion": "3.0.0", "printerIntName": "TEST", "nozzleDiameter": ["0.4"],
                "base": {"id": dbid, "name": "Test " + mtype, "meterialType": mtype,
                         "minTemp": 190, "maxTemp": tmax},
                "kvParam": kv}
    lst = [
        entry("90001", "PLA", "220", 240, "23"),
        entry("90002", "PLA-CF", "225", 240, "18"),
        entry("90003", "PETG", "250", 270, "14"),
        entry("90004", "TPU", None, 240, "2"),          # unvollständig (nozzle_temperature fehlt)
        entry("90001", "ABS", "260", 280, "16"),        # Doppel-ID: erster Treffer gewinnt
        {"base": {"id": "90005"}},                       # ohne kvParam/Typ
        "kaputt",                                        # kein Objekt
    ]
    return {"code": 0, "msg": "ok", "result": {"list": lst, "count": len(lst), "version": "0"}}


def selftest():
    n = [0, 0]

    def check(name, cond):
        n[1] += 1
        if cond:
            n[0] += 1
        else:
            print("FEHLER:", name)

    db = MaterialDB(_mini_db())
    rfid = "1" * 11 + "1" + "90003" + "0FFFFFF" + "0" * 16   # 40 Zeichen, mat_id 190003
    check("RFID-Länge synthetisch 40", len(rfid) == 40)
    check("db_id: 6-stellig -> [1:6]", db_id_from_key("190001") == "90001")
    check("db_id: RFID -> [12:17]", db_id_from_key(rfid) == "90003")
    check("db_id: 5-stellig direkt", db_id_from_key("90002") == "90002")
    check("db_id: '-1'/'None'/None/Länge 4 -> None",
          all(db_id_from_key(k) is None for k in ("-1", "None", None, "9000")))
    check("find: erster Treffer bei Doppel-ID", db.material_type("190001") == "PLA")
    check("nozzle_temp PLA 220", db.nozzle_temp("190001") == 220)
    check("nozzle_temp über RFID (PETG 250)", db.nozzle_temp(rfid) == 250)
    check("max_temp PETG 270", db.max_temp("190003") == 270)
    check("nozzle_temp fehlt -> Fallback 220", db.nozzle_temp("190004") == 220)
    check("nozzle_temp unbekannt -> eigener Fallback", db.nozzle_temp("199999", fallback=205) == 205)
    check("speed 23 -> 575.0 (live 575.0)", db.extrusion_speed("190001") == 575.0)
    check("speed 14 -> 350.00000000000006 (live)", db.extrusion_speed("190003") == 350.00000000000006)
    check("speed TPU 2 -> 50", abs(db.extrusion_speed("190004") - 50.0) < 1e-9)
    check("speed unbekannt -> 360 ohne *60", db.extrusion_speed("-1") == 360)
    check("speed ohne kvParam -> Fallback", db.extrusion_speed("190005") == 360)
    check("prime 23 -> 460 (live 460.0)", db.resume_prime_speed("190001") == 460.0)
    check("prime 14 -> 280.00000000000006 (live)", db.resume_prime_speed("190003") == 280.00000000000006)
    check("prime unbekannt -> 288", abs(db.resume_prime_speed("-1") - 288.0) < 1e-9)
    check("Typ fehlt -> None", db.material_type("190005") is None)
    check("PLA vs PLA-CF: Teilstring, |dT| 5 -> gleich", db.is_different_type("190001", "190002") is False)
    check("PLA vs PETG -> verschieden", db.is_different_type("190001", "190003") is True)
    check("Typ None -> nicht verschieden", db.is_different_type("190001", "190005") is False)
    check("temp_range PLA (190, 240)", db.temp_range("190001") == (190, 240))
    check("Vorschlag Wechsel PLA->PETG bei Ziel 220 -> 250", db.change_temp_proposal("190003", 220) == 250.0)
    check("Vorschlag Ziel höher bleibt", db.change_temp_proposal("190001", 235) == 235.0)
    check("Vorschlag unbekannt -> max(Ziel, 220)", db.change_temp_proposal("-1", 0) == 220.0)
    rep = db.structure_report()
    check("Struktur: 7 Einträge, 1 Doppel-ID, 1 Nicht-Objekt",
          rep["eintraege"] == 7 and rep["ids_doppelt"] == 1 and rep["keine_objekte"] == 1)
    check("Struktur: nozzle_temperature unlesbar 3 (TPU, ohne kv, kaputt)",
          rep["unlesbar_kv.nozzle_temperature"] == 3)
    check("rows: Länge = Einträge (kaputter Eintrag zählt mit)", len(db.rows()) == 7)
    try:
        MaterialDB({"result": {}})
        check("ohne result.list -> MaterialDBError", False)
    except MaterialDBError:
        check("ohne result.list -> MaterialDBError", True)

    # Kreuzprobe gegen nachbau/filament_rack.py (gleiche Stock-Semantik)
    try:
        sys.path.insert(0, __file__.rsplit("/", 1)[0] if "/" in __file__ else ".")
        import logging
        logging.disable(logging.CRITICAL)
        import filament_rack as fr
        raw = _mini_db()
        for key in ("190001", "190003", "190004", "199999"):
            check("Kreuz temp %s" % key, fr.get_material_target_temp(raw, key) == db.nozzle_temp(key))
            check("Kreuz speed %s" % key, fr.get_material_target_speed(raw, key) == db.extrusion_speed(key))
        check("Kreuz max_temp", fr.get_material_target_temp(raw, "190003", max_=True) == db.max_temp("190003"))
    except ImportError as exc:
        print("Hinweis: Kreuzprobe übersprungen (%s)" % exc)

    print("ok %d/%d" % (n[0], n[1]) if n[0] == n[1] else "FEHLER %d/%d" % (n[0], n[1]))
    return 0 if n[0] == n[1] else 1


def main(argv):
    if len(argv) <= 1 or argv[1] == "--selftest":
        return selftest()
    if argv[1] in ("--struktur", "--tabelle") and len(argv) == 3:
        db = MaterialDB.load(argv[2])
        if argv[1] == "--struktur":
            for k, v in db.structure_report().items():
                print("%s: %s" % (k, v))
        else:
            print(markdown_table(db))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
