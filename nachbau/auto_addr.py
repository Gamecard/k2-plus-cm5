#!/usr/bin/env python3
"""auto_addr.py – Klipper-unabhängiger Port von auto_addr_wrapper.py (K2 Plus).

Port der Protokoll-Logik UND der Zustandsmaschine (Quelle, 729 Zeilen:
 Klartextquelle auto_addr_wrapper.py vom Drucker (lokal, nicht im Repo),
 im Folgenden "Orig :<zeile>").
Klipper-Anbindung (config/printer/reactor/gcode/configfile) ist durch
 injizierbare Abhängigkeiten ersetzt:
 - serial: Objekt mit cmd_send_data_with_response(data, timeout, retransmit)
   (z.B. Adapter um serial485.Serial485.transact)
 - store: dict-ähnlich für Uniid-Persistenz (statt configfile/CXSAVE_CONFIG);
   Werte im printer.cfg-Format des Originals ("\\n  0xA0, 0xA1, ...")
 - Hintergrund-Schleife per threading statt Klipper-Reactor

Ablauf wie im Original:
 1. startup()      = process_set_addr_table (Orig :650-659):
                     set_addr_table + get_addr_table je Tabelle
 2. online_once()  = EIN Durchlauf von process_all (Orig :699-728):
                     0xA1-Broadcast (mit Skip) -> 0xA0 für WAIT_FOR_ACK
                     -> 0xA2 Online-Check -> 0x0B Loader-Check
 3. start_background() führt 1. und dann 2. zyklisch aus (1 s, 10 s bei Druck).

Beleg: Live-Verhalten (Discovery-Polls, 0xa2-Sequenz, UUID) aus der Protokollanalyse.
Selbsttest unten OHNE Hardware (FakeSerial = Busmodell mit CFS-Geräten).
"""

import logging
import threading

log = logging.getLogger("auto_addr")

# --- Konstanten 1:1 aus der Quelle (Orig :19-72) ---
MODE_APP = 0
MODE_LOADER = 1

DEV_TYPE_MB = 1       # CFS-Box
DEV_TYPE_CLM = 2      # Closed-loop-Motoren
DEV_TYPE_BTM = 3      # Belt-Module
DEV_TYPE_CFS_PRO = 10  # neue CFS-Generation

POLY = 0x07

STATUS_OK = 0x00

ONLINE_STATE_OFFLINE = 0
ONLINE_STATE_ONLINE = 1
ONLINE_STATE_INIT = 2
ONLINE_STATE_WAIT_FOR_ACK = 3

MAX_GET_TIMES = 2
MAX_SET_TIMES = 2
MAX_LOST_CNT = 3

PACK_HEAD = 0xF7
BROADCAST_ADDR = 0xFF          # Ziel des Loader-Checks (Orig :27, :624)
BROADCAST_ADDR_MB = 0xFE
BROADCAST_ADDR_CLM = 0xFD
BROADCAST_ADDR_BTM = 0xFC
BROADCAST_ADDR_CFS_PRO = 0xFE

CMD_GET_SLAVE_INFO = 0xA1
CMD_SET_SLAVE_ADDR = 0xA0
CMD_ONLINE_CHECK = 0xA2
CMD_GET_ADDR_TABLE = 0xA3
CMD_LOADER_TO_APP = 0x0B

TIMEOUT_SHORT_TIME = 0.05
TIMEOUT_MEDIUM_TIME = 0.1
TIMEOUT_LONG_TIME = 1.0

CMD_TIMEOUT = {
    CMD_GET_SLAVE_INFO: TIMEOUT_LONG_TIME,
    CMD_SET_SLAVE_ADDR: TIMEOUT_SHORT_TIME,
    CMD_GET_ADDR_TABLE: TIMEOUT_SHORT_TIME,
    CMD_ONLINE_CHECK: TIMEOUT_MEDIUM_TIME,
    CMD_LOADER_TO_APP: TIMEOUT_SHORT_TIME,
}

# Zyklus-Intervalle von process_all (Orig :701-703)
CYCLE_INTERVAL = 1.0
CYCLE_INTERVAL_PRINTING = 10.0

MAX_DATA_LEN = 100
MAX_UNIID_LEN = 12

VALID_DEV_TYPES = (DEV_TYPE_MB, DEV_TYPE_CLM, DEV_TYPE_BTM, DEV_TYPE_CFS_PRO)


class DataPackage:
    def __init__(self, head, slave_addr, length, status, function_code, data, crc):
        self.head = head
        self.slave_addr = slave_addr
        self.length = length
        self.status = status
        self.function_code = function_code
        self.data = list(data)
        self.crc = crc


class FcAckData:
    def __init__(self, dev_type, mode, uniid):
        self.dev_type = dev_type
        self.mode = mode
        self.uniid = list(uniid)


class AddrManager:
    def __init__(self, addr, uniid=None, mapped=0, online=0, acked=0,
                 lost_cnt=0, mode=0, type=0):
        self.addr = addr
        self.uniid = list(uniid) if uniid else [0x00]
        self.mapped = mapped
        self.online = online
        self.acked = acked
        self.lost_cnt = lost_cnt
        self.mode = mode
        self.type = type


def default_tables():
    """Adress-Tabellen 1:1 aus der Quelle (nur MB aktiv, Rest auskommentiert,
    Orig :116-156). Reihenfolge der dict-Keys = Reihenfolge von
    dev_table_map_table (relevant für die Tabellenzuordnung, s. _table_index)."""
    return {
        DEV_TYPE_MB: [
            AddrManager(0x01), AddrManager(0x02),
            AddrManager(0x03), AddrManager(0x04),
        ],
    }


def broadcast_addr(dev_type):
    return {
        DEV_TYPE_MB: BROADCAST_ADDR_MB,
        DEV_TYPE_CLM: BROADCAST_ADDR_CLM,
        DEV_TYPE_BTM: BROADCAST_ADDR_BTM,
        DEV_TYPE_CFS_PRO: BROADCAST_ADDR_CFS_PRO,
    }[dev_type]


def crc8_cal(data):
    """1:1 aus der Quelle (verifiziert an 10+ Live-Frames, 16 Hi-Vektoren)."""
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ POLY) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def cal_pack_crc(length, status, function_code, data):
    return crc8_cal([length, status, function_code] + list(data))


# --- Persistenz-Format (Orig save_addr_table_uniids :216-229 / get_ :188-213) ---
def format_uniids(table):
    """Erzeugt exakt den String des Originals (printer.cfg-Format):
    je Eintrag "\\n  " + "0xAA, 0xBB, ..." (Orig :220-225)."""
    uniids = ""
    for entry in table:
        uniids += "\n  "
        for b in entry.uniid:
            uniids += "0x%02X, " % b
        uniids = uniids[:-2]
    return uniids


def _parse_int(value):
    """custom_int_parser des Originals (Orig :191-198)."""
    if value.startswith('0x') or value.startswith('0X'):
        return int(value, 16)
    return int(value)


def parse_uniids(value):
    """Liest gespeicherte Uniids. Akzeptiert
     - String im Original-Format (Klipper getlists, seps=(',', '\\n')),
     - altes Listenformat des Ports ([[0xA0, ...], [0x00], ...]).
    Rückgabe: Liste von Uniid-Listen, oder None bei unsinnigem Inhalt
    (bewusste Abweichung: Original wirft dann einen Config-Fehler bzw.
    crasht später beim Senden – der Port verwirft nur und loggt)."""
    try:
        if isinstance(value, str):
            # Klipper configfile.getlists: Zeilen trimmen, leere Zeilen
            # verwerfen, Zeileninhalt an ',' trennen und jedes Teil parsen.
            rows = []
            for line in value.split('\n'):
                line = line.strip()
                if not line:
                    continue
                rows.append([_parse_int(p.strip()) for p in line.split(',')])
        elif isinstance(value, (list, tuple)):
            rows = []
            for row in value:
                if not isinstance(row, (list, tuple)):
                    return None
                if not all(isinstance(b, int) and not isinstance(b, bool)
                           for b in row):
                    return None
                rows.append([int(b) for b in row])
        else:
            return None
    except (ValueError, TypeError, AttributeError):
        return None
    for row in rows:
        if any(b < 0 or b > 0xFF for b in row):
            return None
    return rows


class AutoAddr:
    """Auto-Adress-Vergabe + Online-Überwachung. Entspricht AutoAddrWrapper."""

    def __init__(self, serial, store=None, tables=None, debug=False):
        self._serial = serial
        self.store = store if store is not None else {}
        self.tables = tables if tables is not None else default_tables()
        self.debug = debug
        self.uniid_changed = False
        self.next_interval = CYCLE_INTERVAL
        self._stop = threading.Event()
        self._thread = None
        # Orig :174-176: gespeicherte Uniids je Tabelle laden
        for dev_type, table in self.tables.items():
            self._load_saved_uniids(dev_type, table)

    def dprintf(self, msg):
        if self.debug:
            log.info(msg)

    # --- Persistenz (statt configfile + CXSAVE_CONFIG) ---
    @staticmethod
    def _store_key(dev_type):
        return {DEV_TYPE_MB: "mb_addr_table_uniids",
                DEV_TYPE_CFS_PRO: "cfs_pro_addr_table_uniids"}.get(dev_type,
                "dev_%d_uniids" % dev_type)

    def _load_saved_uniids(self, dev_type, table):
        """Orig get_addr_table_uniids :188-213."""
        name = self._store_key(dev_type)
        saved = self.store.get(name)
        if saved is None:
            self.dprintf("Error: %s not exist" % name)
            return
        uniids = parse_uniids(saved)
        if uniids is None:
            log.warning("auto_addr: %s unlesbar (%r), verworfen", name, saved)
            return
        if len(uniids) != len(table):  # Orig :200-201: alles verwerfen
            self.dprintf("Error: %s, uniids len: %d, dev_table_map.size: %d"
                         % (name, len(uniids), len(table)))
            return
        for entry, uniid in zip(table, uniids):
            entry.uniid = list(uniid)
            # Orig :207-210: nicht-leere Uniid -> war vergeben, Status INIT
            if len(uniid) >= 1 and uniid[0] != 0:
                entry.mapped = 1
                entry.online = ONLINE_STATE_INIT

    def _save_uniids(self, dev_type, table):
        """Orig save_addr_table_uniids :216-229 (Original-String-Format)."""
        self.store[self._store_key(dev_type)] = format_uniids(table)

    # --- Framing (1:1 data_pack/send_package, ohne Head/CRC auf die Leitung) ---
    def data_pack(self, slave_addr, cmd, data):
        length = len(data) + 3
        return DataPackage(PACK_HEAD, slave_addr, length, STATUS_OK,
                           cmd, list(data), 0x00)

    def send_package(self, package):
        wire = ([package.slave_addr, package.length, package.status,
                 package.function_code] + list(package.data))
        package.crc = cal_pack_crc(package.length, package.status,
                                   package.function_code, package.data)
        timeout = CMD_TIMEOUT[package.function_code]
        ret = self._serial.cmd_send_data_with_response(bytes(wire), timeout, False)
        if ret is None:
            self.dprintf("Error: no response")
            return
        self.data_handler(ret)

    def data_handler(self, ret):
        if ret is None or len(ret) < 6:  # Kurz-Frame: Original -> IndexError
            self.dprintf("Error: frame too short")
            return
        package = DataPackage(ret[0], ret[1], ret[2], ret[3], ret[4],
                              [b for b in ret[5:-1]], ret[-1])
        if package.status == STATUS_OK:
            self.function_code_cb(package)
        else:
            self.dprintf("Error: status %d" % package.status)

    # --- Antwort-Dispatch (Orig function_code_cb :327-439) ---
    def _table_index(self, dev_type):
        """Tabellenzuordnung 1:1 inkl. Original-Eigenheit (Orig :335-341,
        find_cfs_dev_table_index :319-325): die Schleife bricht bei i=0 ab,
        daher landet JEDE Antwort in der ERSTEN Tabelle (dev_table_map_table[0]),
        CFS-Pro in der MB-Tabelle (bzw. ebenfalls Index 0). Mit der Default-
        Konfiguration (nur MB) ist das ohnehin die einzige Tabelle; bewusst
        nicht "repariert", damit Sendesequenzen/Zustände identisch bleiben
        (z.B. ordnet das Original eine CLM-Antwort auf 0xA1 der MB-Tabelle zu)."""
        keys = list(self.tables.keys())
        index = 0
        for i in range(len(keys)):
            if dev_type == DEV_TYPE_CFS_PRO:
                index = keys.index(DEV_TYPE_MB) if DEV_TYPE_MB in keys else 0
            elif dev_type == keys[i]:
                index = i
            break
        return index

    def _table_for(self, dev_type):
        """(dev_type der Tabelle, Tabelle) gemäß _table_index."""
        keys = list(self.tables.keys())
        if not keys:
            return None, None
        key = keys[self._table_index(dev_type)]
        return key, self.tables[key]

    def function_code_cb(self, package):
        fun = package.function_code
        # Bewusste Verbesserung (G2): Original erzeugt ack_data nur für die
        # vier Auto-Addr-Codes und crasht bei anderen Codes (z.B. 0x0B-Antwort)
        # mit AttributeError auf ack_data.dev_type (Orig :333-337). Der Port
        # ignoriert unbekannte Codes bzw. zu kurze Nutzdaten sauber.
        if fun not in (CMD_SET_SLAVE_ADDR, CMD_GET_SLAVE_INFO,
                       CMD_ONLINE_CHECK, CMD_GET_ADDR_TABLE):
            self.dprintf("unknown function code: %d" % fun)
            return
        if len(package.data) < 2:
            self.dprintf("Error: ack data too short")
            return
        ack = FcAckData(package.data[0], package.data[1], package.data[2:])
        table_type, table = self._table_for(ack.dev_type)
        if table is None:
            return
        # Loader-Kennung merken (Orig :343-352) – auch bei ungültigem dev_type
        for entry in table:
            if entry.uniid == ack.uniid:
                entry.mode = ack.mode
                if ack.mode == MODE_LOADER:
                    self.dprintf("addr 0x%02X in loader mode" % entry.addr)
                break
        valid = ack.dev_type in VALID_DEV_TYPES  # Orig is_dev_type_valid :315
        if fun == CMD_SET_SLAVE_ADDR:            # Orig :354-377
            if valid:
                for entry in table:
                    if entry.online in (ONLINE_STATE_INIT, ONLINE_STATE_WAIT_FOR_ACK) \
                            and entry.addr == package.slave_addr \
                            and entry.uniid == ack.uniid and entry.acked == 0:
                        entry.acked = 1
                        entry.online = ONLINE_STATE_ONLINE
                        entry.lost_cnt = 0
                        entry.type = ack.dev_type
                        break
        elif fun == CMD_GET_SLAVE_INFO:          # Orig :378-388
            if valid:
                addr = self.addr_allocate(ack.uniid, table)
                self.dprintf("addr: %d" % addr)
        elif fun == CMD_ONLINE_CHECK:            # Orig :390-407
            if valid:
                for entry in table:
                    if entry.addr == package.slave_addr \
                            and entry.uniid == ack.uniid:
                        entry.acked = 1
                        entry.online = ONLINE_STATE_ONLINE
                        entry.lost_cnt = 0
                        entry.type = ack.dev_type
                        break
        elif fun == CMD_GET_ADDR_TABLE:          # Orig :409-433
            if valid:
                for i, entry in enumerate(table):
                    if entry.addr == package.slave_addr:
                        if any(j != i and e.uniid == ack.uniid
                               for j, e in enumerate(table)):
                            return  # Duplikat: alte Adresse behalten (Orig :423)
                        entry.uniid = ack.uniid
                        entry.mapped = 1
                        entry.acked = 1
                        entry.online = ONLINE_STATE_ONLINE
                        entry.lost_cnt = 0
                        entry.type = ack.dev_type
                        self.uniid_changed = True
                        break
        if self.uniid_changed:                   # Orig :437-439
            self.uniid_changed = False
            self._save_uniids(table_type, table)

    # --- Adress-Vergabe (Orig addr_allocate :246-289) ---
    def addr_allocate(self, uniid, table):
        for entry in table:
            if entry.mapped == 1:
                # Methode 1: gemappt, offline/init, UUID passt (Orig :252-257)
                if entry.online in (ONLINE_STATE_OFFLINE, ONLINE_STATE_INIT):
                    if uniid == entry.uniid:
                        entry.mapped = 1
                        entry.online = ONLINE_STATE_WAIT_FOR_ACK
                        return entry.addr
                # Gerät meldet sich auf Broadcast, obwohl ONLINE: es hat
                # rebootet und seine Adresse verloren -> erneut 0xA0 senden
                # (Orig :258-265)
                elif entry.online == ONLINE_STATE_ONLINE and uniid == entry.uniid:
                    self.dprintf("addr 0x%02X: slave restarted" % entry.addr)
                    entry.online = ONLINE_STATE_WAIT_FOR_ACK
                    entry.acked = 0
                    return -1
        for entry in table:  # Methode 2: ungemappte Adresse (Orig :267-274)
            if entry.mapped == 0:
                entry.mapped = 1
                entry.online = ONLINE_STATE_WAIT_FOR_ACK
                entry.uniid = list(uniid)
                self.uniid_changed = True
                return entry.addr
        for entry in table:  # Methode 3: gemappt, offline, UUID egal (Orig :277-287)
            if entry.mapped == 1 and entry.online in (ONLINE_STATE_OFFLINE,
                                                      ONLINE_STATE_INIT) \
                    and entry.uniid != uniid:
                entry.uniid = list(uniid)
                entry.mapped = 1
                entry.online = ONLINE_STATE_WAIT_FOR_ACK
                self.uniid_changed = True
                return entry.addr
        return -1

    # --- Kommando-Schnittstelle (1:1 communication_*, Orig :479-501) ---
    # Diese Methoden senden UNBEDINGT ein Kommando; die Zustandslogik steckt
    # in den logic_*-Methoden darunter.
    def get_slave_info(self, dev_type, extra=None):
        bc = broadcast_addr(dev_type)
        self.send_package(self.data_pack(bc, CMD_GET_SLAVE_INFO, [bc, bc]))

    def set_slave_addr(self, dev_type, addr, uniid):
        self.send_package(self.data_pack(broadcast_addr(dev_type),
                                          CMD_SET_SLAVE_ADDR, [addr] + list(uniid)))

    def online_check(self, dev_type, addr):
        self.send_package(self.data_pack(addr, CMD_ONLINE_CHECK, []))

    def get_addr_table(self, dev_type, addr):
        self.send_package(self.data_pack(addr, CMD_GET_ADDR_TABLE, []))

    def loader_to_app(self, dev_type, addr):
        self.send_package(self.data_pack(addr, CMD_LOADER_TO_APP, [0x01]))

    # --- Logik-Schnittstelle (Orig :515-626) ---
    def logic_get_addr_table(self, dev_type):
        """Orig get_addr_table :515-538: bis MAX_GET_TIMES je nicht-ONLINE-
        Adresse 0xA3 senden; Abbruch, sobald alle ONLINE."""
        table = self.tables[dev_type]
        for _ in range(MAX_GET_TIMES):
            for entry in table:
                if entry.online != ONLINE_STATE_ONLINE:
                    self.get_addr_table(dev_type, entry.addr)
            if sum(e.online == ONLINE_STATE_ONLINE for e in table) == len(table):
                return

    def logic_set_addr_table(self, dev_type):
        """Orig set_addr_table :540-566: bis MAX_SET_TIMES für gemappte
        INIT-Einträge (aus Store) 0xA0 senden; Abbruch, wenn Anzahl ONLINE
        == Anzahl in diesem Durchlauf gesendeter (1:1 inkl. dieser Zählweise)."""
        table = self.tables[dev_type]
        for _ in range(MAX_SET_TIMES):
            mapped_cnt = 0
            for entry in table:
                if entry.mapped == 1 and entry.online == ONLINE_STATE_INIT:
                    mapped_cnt += 1
                    self.set_slave_addr(dev_type, entry.addr, entry.uniid)
            valid = sum(e.online == ONLINE_STATE_ONLINE for e in table)
            if valid == mapped_cnt:
                return

    def logic_get_slave_info(self, dev_type):
        """Orig get_slave_info :568-583: 0xA1-Broadcast, außer alle Einträge
        sind ONLINE oder WAIT_FOR_ACK (M1)."""
        table = self.tables[dev_type]
        busy = sum(e.online in (ONLINE_STATE_ONLINE, ONLINE_STATE_WAIT_FOR_ACK)
                   for e in table)
        if busy == len(table):
            return False
        self.get_slave_info(dev_type)
        return True

    def logic_set_slave_addr(self, dev_type):
        """Orig set_slave_addr :585-593: 0xA0 für alle WAIT_FOR_ACK-Einträge."""
        for entry in self.tables[dev_type]:
            if entry.mapped == 1 and entry.online == ONLINE_STATE_WAIT_FOR_ACK:
                self.set_slave_addr(dev_type, entry.addr, entry.uniid)

    def logic_online_check(self, dev_type):
        """Orig online_check :595-614."""
        for entry in self.tables[dev_type]:
            if entry.mapped == 1:
                entry.lost_cnt += 1
                self.online_check(dev_type, entry.addr)
                if entry.lost_cnt > MAX_LOST_CNT:
                    entry.online = ONLINE_STATE_OFFLINE
                    entry.acked = 0
                    self.dprintf("Error: addr %.2X offline" % entry.addr)

    def logic_loader_check(self, dev_type):
        """Orig loader_check :615-626: steht ein Eintrag im Loader, einmal
        0x0B [0x01] an BROADCAST_ADDR (0xFF) senden."""
        for entry in self.tables[dev_type]:
            if entry.mode == MODE_LOADER:
                self.loader_to_app(dev_type, BROADCAST_ADDR)
                return True
        return False

    # --- Prozesse (Orig :650-728) ---
    def startup(self):
        """Orig process_set_addr_table :650-659 (vor auto_addr:set_finished):
        gespeicherte Adressen setzen, Fehlende per 0xA3 abfragen."""
        for dev_type in list(self.tables.keys()):
            self.logic_set_addr_table(dev_type)
            self.logic_get_addr_table(dev_type)

    @staticmethod
    def _is_printing(printing):
        """printing: bool oder Klipper-print_stats.state-String."""
        if isinstance(printing, str):
            return printing in ("printing", "pause")
        return bool(printing)

    def online_once(self, printing=False):
        """EIN Durchlauf von process_all (Orig :699-728). printing (bool oder
        "printing"/"pause") bestimmt next_interval: 10 s statt 1 s (M4)."""
        self.next_interval = (CYCLE_INTERVAL_PRINTING if self._is_printing(printing)
                              else CYCLE_INTERVAL)
        types = list(self.tables.keys())
        for dev_type in types:                   # Orig :708-715
            self.logic_get_slave_info(dev_type)
            # "kompatibel mit Gerätetypen gleicher Broadcast-Adresse":
            # nach jedem Broadcast 0xA0 für ALLE Tabellen (1:1)
            for dev_type2 in types:
                self.logic_set_slave_addr(dev_type2)
        for dev_type in types:                   # Orig :718-720
            self.logic_online_check(dev_type)
        for dev_type in types:                   # Orig :723-727: 1x pro Zyklus
            if self.logic_loader_check(dev_type):
                break
        return self.status_text()

    def status_text(self):
        lines = []
        for dev_type, table in self.tables.items():
            for e in table:
                lines.append("dev %d addr 0x%02X mapped %d online %d acked %d lost %d mode %d uniid %s"
                             % (dev_type, e.addr, e.mapped, e.online, e.acked,
                                e.lost_cnt, e.mode,
                                ",".join("0x%02X" % b for b in e.uniid)))
        return "\n".join(lines)

    def start_background(self, interval=1.0, printing=None, startup=True):
        """Hintergrund-Schleife statt Reactor. Erst startup() (wie
        klippy:ready -> process_set_addr_table), dann sofort der erste
        process_all-Durchlauf, danach alle `interval` s bzw. 10 s, solange
        printing() wahr ist (printing: Callable -> bool/State-String, oder
        fester Wert). Exceptions werden geloggt, die Schleife läuft weiter."""
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()

        def loop():
            if startup:
                try:
                    self.startup()
                except Exception:
                    log.exception("auto_addr: startup fehlgeschlagen")
            while not self._stop.is_set():
                try:
                    state = printing() if callable(printing) else printing
                    self.online_once(state)
                    wait = (CYCLE_INTERVAL_PRINTING if self._is_printing(state)
                            else interval)
                except Exception:
                    log.exception("auto_addr: Zyklus fehlgeschlagen")
                    wait = interval
                if self._stop.wait(wait):
                    break
        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()

    def stop_background(self):
        self._stop.set()


# --- Selbsttest OHNE Hardware: Busmodell mit realistischen CFS-Geräten ---
U1 = [0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xAB]
U2 = [0x11] * 12


class FakeDev:
    def __init__(self, uuid, addr=None, typ=DEV_TYPE_MB, mode=MODE_APP):
        self.uuid = list(uuid)
        self.addr = addr      # None = keine Adresse (frisch gebootet)
        self.typ = typ
        self.mode = mode
        self.alive = True


class FakeSerial:
    """Busmodell: Gerät antwortet auf 0xA1 nur ohne Adresse, auf 0xA0 bei
    passender UUID (übernimmt dann die Adresse), auf 0xA2/0xA3 nur unter
    seiner Adresse. 0x0B an 0xFF schaltet Loader-Geräte in die App (keine
    Antwort). Mehrere adresslose Geräte: es antwortet das erste.
    Vereinfachung ggü. CFS-FW R153 (FW-Analyse): die echte Box
    beantwortet 0xA0 3x (Duplikate) und ignoriert 0xA0/0xA1 an 0xFE, sobald
    sie eine Adresse hat; A1-Antwort 0-1000 ms zufällig verzögert. Selbsttest
    13 prüft den Port gegen dieses FW-Verhalten (FwSerial)."""

    UUID = U1

    def __init__(self, devs=None):
        self.devs = devs if devs is not None else []
        self.sent = []  # (addr, fun, payload)

    def cmd_send_data_with_response(self, data, timeout, retransmit):
        addr, fun, payload = data[0], data[3], list(data[4:])
        self.sent.append((addr, fun, payload))
        for d in self.devs:
            if not d.alive:
                continue
            if fun == CMD_GET_SLAVE_INFO and addr == broadcast_addr(d.typ) \
                    and d.addr is None:
                return bytes(_frame(addr, fun, [d.typ, d.mode] + d.uuid))
            if fun == CMD_SET_SLAVE_ADDR and addr == broadcast_addr(d.typ) \
                    and payload[1:] == d.uuid:
                d.addr = payload[0]
                return bytes(_frame(d.addr, fun, [d.typ, d.mode] + d.uuid))
            if fun in (CMD_ONLINE_CHECK, CMD_GET_ADDR_TABLE) and addr == d.addr:
                return bytes(_frame(addr, fun, [d.typ, d.mode] + d.uuid))
            if fun == CMD_LOADER_TO_APP and addr == BROADCAST_ADDR:
                d.mode = MODE_APP
        return None

    def cmds(self, start=0):
        return [(a, f) for a, f, _ in self.sent[start:]]


def _frame(addr, fun, data, status=STATUS_OK):
    length = len(data) + 3
    body = [length, status, fun] + list(data)
    return [PACK_HEAD, addr] + body + [crc8_cal(body)]


def selftest():
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("ok " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    def uuids_unique(table):
        used = [tuple(e.uniid) for e in table if e.mapped]
        return len(used) == len(set(used))

    A1, A0, A2, A3, LB = (CMD_GET_SLAVE_INFO, CMD_SET_SLAVE_ADDR,
                          CMD_ONLINE_CHECK, CMD_GET_ADDR_TABLE, CMD_LOADER_TO_APP)
    FE = BROADCAST_ADDR_MB

    # 1. CRC-Identität mit Quelle + Live-Frame
    check("crc quell-identisch",
          crc8_cal([0x07, 0x00, 0x00, 0x01, 0x02, 0x03, 0x04]) == 0xF0)
    live = bytes.fromhex("f7 01 11 00 a2 01 00 a0 a1 a2 a3 a4 a5 a6 a7 a8 a9 aa ab f0")
    check("crc live-a2", crc8_cal(list(live[2:-1])) == live[-1])

    # 2. Neues Gerät, leerer Store: Startphase + 3 Zyklen -> 0x01 ONLINE
    dev = FakeDev(U1)
    bus = FakeSerial([dev])
    aa = AutoAddr(bus)
    t = aa.tables[DEV_TYPE_MB]
    aa.startup()
    check("start leer: nur 0xA3 an 1..4 (2x)",
          bus.cmds() == [(a, A3) for a in (1, 2, 3, 4)] * 2)
    n = len(bus.sent)
    for _ in range(3):
        aa.online_once()
    check("neu: Sequenz a1,a0,a2 | a1,a2 | a1,a2",
          bus.cmds(n) == [(FE, A1), (FE, A0), (1, A2)] + [(FE, A1), (1, A2)] * 2)
    check("neu: 0x01 ONLINE acked", t[0].online == ONLINE_STATE_ONLINE
          and t[0].acked == 1 and t[0].uniid == U1 and dev.addr == 0x01)
    check("neu: Rest frei", all(e.mapped == 0 for e in t[1:]))
    check("neu: Store im Original-Format, UUID genau 1x",
          aa.store["mb_addr_table_uniids"] == "\n  " + ", ".join(
              "0x%02X" % b for b in U1) + "\n  0x00\n  0x00\n  0x00")

    # 3. Zwei neue Geräte -> 0x01 und 0x02, keine Duplikate
    d1, d2 = FakeDev(U1), FakeDev(U2)
    aa = AutoAddr(FakeSerial([d1, d2]))
    t = aa.tables[DEV_TYPE_MB]
    for _ in range(3):
        aa.online_once()
    check("zwei: 0x01/0x02 ONLINE", (d1.addr, d2.addr) == (1, 2)
          and t[0].online == t[1].online == ONLINE_STATE_ONLINE)
    check("zwei: keine Duplikat-UUIDs", uuids_unique(t) and t[2].mapped == 0)

    # 4. Reboot (Adresse verloren) -> -1, WAIT_FOR_ACK, erneut 0xA0 auf 0x01
    dev = FakeDev(U1)
    bus = FakeSerial([dev])
    aa = AutoAddr(bus)
    t = aa.tables[DEV_TYPE_MB]
    aa.online_once()
    dev.addr = None  # Reboot
    probe = AutoAddr(FakeSerial())
    pe = probe.tables[DEV_TYPE_MB][0]
    pe.uniid, pe.mapped, pe.online, pe.acked = list(U1), 1, ONLINE_STATE_ONLINE, 1
    r = probe.addr_allocate(list(U1), probe.tables[DEV_TYPE_MB])
    check("reboot: addr_allocate -1 + WAIT_FOR_ACK, acked 0",
          r == -1 and pe.online == ONLINE_STATE_WAIT_FOR_ACK and pe.acked == 0)
    n = len(bus.sent)
    aa.online_once()
    check("reboot: Sequenz a1,a0,a2", bus.cmds(n) == [(FE, A1), (FE, A0), (1, A2)])
    check("reboot: wieder 0x01 ONLINE, keine Duplikate",
          dev.addr == 1 and t[0].online == ONLINE_STATE_ONLINE
          and uuids_unique(t) and t[1].mapped == 0)

    # 5. Kaltstart mit UUID im Store (Original-String) -> Startphase setzt 0x01
    saved = "\n  " + ", ".join("0x%02X" % b for b in U1) + "\n  0x00\n  0x00\n  0x00"
    dev = FakeDev(U1)
    bus = FakeSerial([dev])
    aa = AutoAddr(bus, store={"mb_addr_table_uniids": saved})
    t = aa.tables[DEV_TYPE_MB]
    check("kalt: geladen mapped/INIT", t[0].mapped == 1
          and t[0].online == ONLINE_STATE_INIT and t[0].uniid == U1)
    aa.startup()
    check("kalt: Startsequenz a0 | a3 2,3,4 (2x)",
          bus.cmds() == [(FE, A0)] + [(a, A3) for a in (2, 3, 4)] * 2)
    check("kalt: 0x01 ONLINE nach Startphase",
          t[0].online == ONLINE_STATE_ONLINE and dev.addr == 1)
    aa.online_once()
    check("kalt: Zyklus a1,a2, 0x01 ONLINE",
          bus.cmds(7) == [(FE, A1), (1, A2)] and t[0].online == ONLINE_STATE_ONLINE)
    # Kaltstart, Gerät reagiert nicht auf Startphase -> 0xA1-Pfad (Methode 1)
    dev = FakeDev(U1)
    dev.alive = False
    bus = FakeSerial([dev])
    aa = AutoAddr(bus, store={"mb_addr_table_uniids": saved})
    aa.startup()
    check("kalt stumm: 2x a0, bleibt INIT",
          bus.cmds().count((FE, A0)) == 2
          and aa.tables[DEV_TYPE_MB][0].online == ONLINE_STATE_INIT)
    dev.alive = True
    aa.online_once()
    check("kalt spät: per a1->a0 auf 0x01 ONLINE",
          dev.addr == 1 and aa.tables[DEV_TYPE_MB][0].online == ONLINE_STATE_ONLINE)

    # 6. Broadcast-Skip: alle 4 Einträge ONLINE -> kein 0xA1
    devs = [FakeDev([i] * 12) for i in range(1, 5)]
    bus = FakeSerial(devs)
    aa = AutoAddr(bus)
    for _ in range(5):
        aa.online_once()
    n = len(bus.sent)
    aa.online_once()
    check("skip: 4 online, kein a1",
          bus.cmds(n) == [(a, A2) for a in (1, 2, 3, 4)]
          and uuids_unique(aa.tables[DEV_TYPE_MB]))

    # 7. Loader-Gerät: 0x0B [01] an 0xFF einmal je Zyklus, bis App-Modus
    dev = FakeDev(U1, mode=MODE_LOADER)
    bus = FakeSerial([dev])
    aa = AutoAddr(bus)
    aa.online_once()
    check("loader: Sequenz a1,a0,a2,0b@ff",
          bus.cmds() == [(FE, A1), (FE, A0), (1, A2), (BROADCAST_ADDR, LB)]
          and bus.sent[-1][2] == [0x01])
    n = len(bus.sent)
    aa.online_once()  # Gerät jetzt App -> 0xA2-Antwort setzt mode 0
    check("loader: danach kein 0x0B mehr",
          LB not in [f for _, f in bus.cmds(n)]
          and aa.tables[DEV_TYPE_MB][0].mode == MODE_APP)

    # 8. Store: Roundtrip, Original-String, altes Listenformat, Fehlwerte
    aa = AutoAddr(FakeSerial())
    t = aa.tables[DEV_TYPE_MB]
    t[0].uniid, t[1].uniid = list(U1), list(U2)
    aa._save_uniids(DEV_TYPE_MB, t)
    s = aa.store["mb_addr_table_uniids"]
    aa2 = AutoAddr(FakeSerial(), store={"mb_addr_table_uniids": s})
    check("store roundtrip", [e.uniid for e in aa2.tables[DEV_TYPE_MB]]
          == [U1, U2, [0], [0]] and aa2.tables[DEV_TYPE_MB][1].mapped == 1)
    # wie Klipper ihn aus printer.cfg liefert (Einrückung entfernt)
    cfg = "\n0xa0, 0xa1,0xA2 ,0xA3, 0xA4, 0xA5, 0xA6, 0xA7, 0xA8, 0xA9, 0xAA, 0xAB\n0x00\n0\n0x00\n"
    aa2 = AutoAddr(FakeSerial(), store={"mb_addr_table_uniids": cfg})
    check("store printer.cfg-String", aa2.tables[DEV_TYPE_MB][0].uniid == U1
          and aa2.tables[DEV_TYPE_MB][2].mapped == 0)
    aa2 = AutoAddr(FakeSerial(), store={"mb_addr_table_uniids": [U1, [0], [0], [0]]})
    check("store altes Listenformat", aa2.tables[DEV_TYPE_MB][0].uniid == U1
          and aa2.tables[DEV_TYPE_MB][0].online == ONLINE_STATE_INIT)
    ok = True
    for bad in ("\n  0x%02X\n  0x00" % 0xA0, [U1, [0]], [None], [5], "x", 5,
                None, "0xZZ\n0\n0\n0", [[256], [0], [0], [0]], [["a"], [0], [0], [0]]):
        try:
            aa2 = AutoAddr(FakeSerial(), store={"mb_addr_table_uniids": bad})
            ok = ok and all(e.mapped == 0 and e.uniid == [0]
                            for e in aa2.tables[DEV_TYPE_MB])
        except Exception as ex:  # darf nicht passieren
            print("   Exception bei %r: %r" % (bad, ex))
            ok = False
    check("store falsche Länge/Fehlwerte verworfen, kein Crash", ok)

    # 9. Druck-Intervall
    aa = AutoAddr(FakeSerial())
    aa.online_once(printing=True)
    i1 = aa.next_interval
    aa.online_once("pause")
    i2 = aa.next_interval
    aa.online_once(printing="standby")
    check("intervall 10 s bei Druck/Pause, sonst 1 s",
          (i1, i2, aa.next_interval) == (10.0, 10.0, 1.0))

    # 10. Hintergrund: Startphase + erster Zyklus sofort, Exceptions geloggt
    import time
    dev = FakeDev(U1)
    bus = FakeSerial([dev])
    aa = AutoAddr(bus)
    aa.start_background(interval=100.0)
    t0 = time.time()
    while time.time() - t0 < 2 and dev.addr is None:
        time.sleep(0.01)
    aa.stop_background()
    check("bg: erster Zyklus sofort (Startphase davor)",
          dev.addr == 1 and bus.cmds()[:8] == [(a, A3) for a in (1, 2, 3, 4)] * 2)

    class Boom:
        calls = 0

        def cmd_send_data_with_response(self, *a):
            Boom.calls += 1
            raise IOError("bus weg")

    records = []

    class H(logging.Handler):
        def emit(self, rec):
            records.append(rec)
    h = H()
    log.addHandler(h)
    log.propagate = False
    aa = AutoAddr(Boom())
    aa.start_background(interval=0.01)
    t0 = time.time()
    while time.time() - t0 < 2 and Boom.calls < 4:
        time.sleep(0.01)
    aa.stop_background()
    aa._thread.join(1)
    log.removeHandler(h)
    log.propagate = True
    check("bg: Exceptions per logging.exception, Schleife läuft weiter",
          Boom.calls >= 3 and any(r.exc_info for r in records))

    # 11. Offline nach MAX_LOST_CNT, Rückkehr
    dev = FakeDev(U1)
    aa = AutoAddr(FakeSerial([dev]))
    e = aa.tables[DEV_TYPE_MB][0]
    aa.online_once()
    dev.alive = False
    for _ in range(MAX_LOST_CNT):
        aa.online_once()
    still = e.online == ONLINE_STATE_ONLINE
    aa.online_once()
    check("offline erst nach %d verpassten Checks" % (MAX_LOST_CNT + 1),
          still and e.online == ONLINE_STATE_OFFLINE and e.acked == 0)
    dev.alive = True
    dev.addr = None  # kommt rebootet zurück
    aa.online_once()
    check("offline -> Rückkehr per a1 (Methode 1) auf 0x01",
          e.online == ONLINE_STATE_ONLINE and dev.addr == 1
          and uuids_unique(aa.tables[DEV_TYPE_MB]))

    # 13. CFS-FW R153 (FW-Analyse): A0-Antwort kommt 3x, adressierte Box
    #     ignoriert A0/A1. Port muss Duplikate tolerieren und nach Host-
    #     Neustart (Box behält Adresse) über A3 wieder ONLINE kommen.
    class FwSerial(FakeSerial):
        def __init__(self, devs=None):
            FakeSerial.__init__(self, devs)
            self.dups = []  # 2 weitere A0-Antworten je erfolgreichem A0

        def cmd_send_data_with_response(self, data, timeout, retransmit):
            addr, fun = data[0], data[3]
            if fun in (CMD_GET_SLAVE_INFO, CMD_SET_SLAVE_ADDR) and all(
                    d.addr is not None for d in self.devs if d.alive):
                self.sent.append((addr, fun, list(data[4:])))
                return None  # adressierte Box verwirft A0/A1 im RX-Zustand 1
            ret = FakeSerial.cmd_send_data_with_response(self, data, timeout,
                                                         retransmit)
            if fun == CMD_SET_SLAVE_ADDR and ret is not None:
                self.dups += [ret, ret]
            return ret

    dev = FakeDev(U1)
    fw = FwSerial([dev])
    aa = AutoAddr(fw)
    t = aa.tables[DEV_TYPE_MB]
    aa.online_once()
    saves = aa.store.get("mb_addr_table_uniids")
    for dup in fw.dups:          # Duplikate wie von der FW nachgereicht
        aa.data_handler(dup)
    check("FW: A0-Antwort 3x toleriert (Zustand/Store unverändert)",
          len(fw.dups) == 2 and t[0].online == ONLINE_STATE_ONLINE
          and t[0].acked == 1 and uuids_unique(t) and t[1].mapped == 0
          and aa.store.get("mb_addr_table_uniids") == saves)
    aa2 = AutoAddr(fw, store=aa.store)   # Host-Neustart, Box behält 0x01
    n = len(fw.sent)
    aa2.startup()
    t2 = aa2.tables[DEV_TYPE_MB]
    check("FW: adressierte Box ignoriert A0 -> ONLINE über A3",
          (FE, A0) in fw.cmds(n) and (1, A3) in fw.cmds(n)
          and t2[0].online == ONLINE_STATE_ONLINE and t2[0].uniid == U1)

    # 12. G2: unbekannter Function-Code / Kurz-Frame ohne Crash
    aa = AutoAddr(FakeSerial())
    aa.data_handler(bytes(_frame(0x01, CMD_LOADER_TO_APP, [1])))
    aa.data_handler(bytes(_frame(0x01, CMD_ONLINE_CHECK, [])))
    aa.data_handler(b"\xf7\x01")
    check("unbekannter Code/Kurz-Frame ignoriert", True)

    print("SELBSTTEST: %s" % ("ok" if fails == 0 else "%d FEHLER" % fails))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
