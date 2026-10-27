#!/usr/bin/env python3
"""box.py – Python-Nachbau von box_wrapper ParseData/BoxState/BoxAction (CFS-Box, K2 Plus).

Erweiterung von boxmini.py (NICHT ändern): boxmini.py deckt RFID + Restmenge
als BoxMini-Klasse ab; diese Datei ergänzt sinngemäß nach der box_wrapper-API:

1) ParseData (reine Funktionen, keine Hardware):
   get_rfid / get_remain_len / get_measuring_wheel (BE-Float via struct) /
   get_box_status, dazu belegt: parse_version_sn (ASCII "153"+SN),
   parse_buffer_state / parse_hardware_status (je 1 Byte),
   split_rfid_vendor (40-Zeichen-vender -> Felder, ab 15.10.2026).
2) BoxState: Tn-Datenmodell im Speicher (tn_map/tnn_map/save_data als dicts,
   generate/get/modify/update-Methoden sinngemäß nach API-Doku).
3) BoxAction: belegte Einzel-Step-Sequenz-Builder (Preload enable/disable/
   force-read/force-tension-only,
   Feed-Mode enter, Auto-Preload-Sperre (0x0f, früher „Engage/Release“),
   Print-Mode) + bus-gebundene Klasse.

FW-ANALYSE 15.10.2026: Semantik der CFS-App R153 (= FW in der untersuchten Box)
belegt (Func-Tabelle CFS-App R153). Daraus hier: STATUS_* / STATUS_NAMES, BOX_STATE_*,
parse_box_state, Preload op 3 + langer Timeout, 0x0f-Umbenennung,
EXTRUDE_MODE_* (Re-Export aus cfsseq).

LIVE-LOGS 15.10.2026 (klippy.log 05.-16.10., nur lesend vom Drucker geholt;
lokal, nicht im Repo; Live-Gegenprobe der Protokollanalyse):
FUNC_RETRIES/func_retries (Original-Retries je fun, 0x0a-Poll retries=0),
Host-Namen 0x08 GET_FILAMENT_SENSOR_STATE / 0x15 GET_HARDWARE_STATUS /
0x0f TIGHTEN_UP_ENABLE / Status 0x30 UPDATE_STATE / 0x51 SPEED_ERR (alte
Namen als Aliase), 0x0a b5 „RFID gültig“ unbelegt (live immer 0), b0/b1
Temp/Feuchte-Kandidat (beides inzwischen geklärt, s. FW-ANALYSE 16.10. unten), FW-113-0x0a mit 4 Byte (parse_box_state box_fw),
EVENT_* für 0x30-Ereignisse, Selbsttests mit echten Live-Frames
(SN/Vendor durch Platzhalter ersetzt).

Bus-Anbindung: serial485.Serial485-Instanz wird übergeben (echte Hardware,
pyserial). Der Selbsttest läuft OHNE Hardware (FakeBus + Live-Frames).

Belege (aus der Protokollanalyse, nicht in diesem Repo):
- Func-Codes + Frame-Format + CRC-8/SMBUS poly 0x07 über [len..daten]:
  RS485-Mitschnitte und box_wrapper-API.
- Operational-Requests: STATUS 0xFF, Timeout 2,0 s (Live-Introspektion
  send_data state=0xff, communication_get_rfid/remain payload [Maske]).
- Slots als 1-hot-Maske A=0x01/B=0x02/C=0x04/D=0x08, Default 0x0f = alle.
- Extrude/Retrude-Stufen-Sequenzen stehen bewusst NICHT hier, sondern in
  nachbau/cfsseq.py (dort belegt) – hier kein Duplikat, nur Verweis.
  Geprüfte Einzel-Requests 0x10/0x11 (retries=0, lange Timeouts):
  BoxAction.communication_extrude / communication_retrude (16.10.2026).

REVIEW 16.10.2026: Statusprüfung (CfsStatusError/StatusBytes, M2),
0x10/0x11-Pfad (M3), Messrad op 0 nur mit reset=True (G1), Maske 0 bei
0x0d op 2/3 = 4 Slots (G2), _box_fw_num mit Host-Form "1.5.3" (G3),
Voll-Frame mit Anhang (G4), DeprecationWarning für
communication_get_hardware_status(channel) (G6), slot_to_mask aus cfsseq.

FW-ANALYSE 16.10.2026: 0x0a b0/b1 =
Temperatur °C (int8) / rel. Feuchte % aus AHT30 (BELEGT, Schlüssel temp_c/
rh; *_guess bleiben Aliase), b5 = inlet_tag_mask (Typ-7-Tag-Sperrbit, NICHT
„RFID gültig“), b2-Flags dekodiert (block_*), 0x15 Komponenten-Init-Flags
(HW_COMPONENTS), Status 0x50/0x51/0x52 und Kanal-A-Codes präzisiert.

PRINZIP: Nichts erfinden. Was nicht auf dem Bus belegt ist, ist als TODO
markiert (insbesondere Tn-Innensemantik und .so-Entscheidungslogik).
Kein Netzwerk (Drucker-IP tabu), keine Hardware im Selbsttest.
"""

import struct
import warnings

import serial485
from serial485 import Serial485  # noqa: F401 (für echten Betrieb)

# --- Protokoll-Konstanten (Beleg: Protokollanalyse + box_wrapper-API) ---
BOX_ADDR = 0x01
STATUS_OPERATIONAL = 0xFF
STATUS_REPLY = 0x00
TIMEOUT_OPERATIONAL = 2.0
NO_DATA = 0xFF
SLOT_KEYS = "ABCD"  # NUM 0..3 -> A/B/C/D
SLOT_MASKS = (0x01, 0x02, 0x04, 0x08)
MASK_ALL = 0x0F

FUNC_GET_RFID = 0x02
FUNC_GET_REMAIN_LEN = 0x03
FUNC_SET_BOX_MODE = 0x04
FUNC_GET_BUFFER_STATE = 0x05
# 0x08: Host-Name GET_FILAMENT_SENSOR_STATE (LIVE BELEGT 15.10.2026, klippy.log
# "cmd: GET_FILAMENT_SENSOR_STATE"; .so communication_get_filament_sensor_state
# (addr, pos)). [00] -> Bitmaske Sensor A (live 07 = A,B,C belegt, D leer),
# [01] -> Bitmaske Sensor B/Hub (deckungsgleich mit 0x0a Byte 4).
# ~~GET_HARDWARE_STATUS~~ war unser falscher Name für 0x08.
FUNC_GET_FILAMENT_SENSOR_STATE = 0x08
# Alter (falscher) Name für 0x08 – NICHT mehr verwenden; der Host-Name
# GET_HARDWARE_STATUS gehört zu 0x15 (s. FUNC_GET_HARDWARE_STATUS).
FUNC_HARDWARE_STATUS_LEGACY_08 = FUNC_GET_FILAMENT_SENSOR_STATE
FUNC_GET_BOX_STATE = 0x0A
FUNC_SET_PRE_LOADING = 0x0D
FUNC_MEASURING_WHEEL = 0x0E
# 0x0f: ~~Motor-Engage/Release~~ WIDERLEGT 15.10.2026 (FW-Analyse, inline im
# Dispatcher FUN_0800f27c): Auto-Preload-Sperr-Flag S[8] (0x200037d8),
# x in {0,1} -> Status 0, sonst 1; S[8]!=0 sperrt das automatische Preload
# (FUN_080053b0). KEINE Motor-Aktion. Alter Name bleibt als Alias.
# Host-Name (LIVE 15.10.2026): TIGHTEN_UP_ENABLE (.so
# communication_tighten_up_enable(addr, enable)); Bytes identisch, unsere
# FW-Semantik „Auto-Preload-Sperre“ bleibt die Deutung des Effekts.
FUNC_TIGHTEN_UP_ENABLE = 0x0F
FUNC_PRELOAD_INHIBIT = FUNC_TIGHTEN_UP_ENABLE  # Alias (FW-Semantik)
FUNC_MOTOR_ACTION = FUNC_PRELOAD_INHIBIT  # Alias (alter Name, kompatibel)
# 0x07 = CTRL_CONNECTION_MOTOR_ACTION (Hauptmotor: 0 Stopp, 1 vor, 2 zurück – FW-Analyse);
# LIVE 19.10.2026 (Live-Test externe Spule): Stock sendet vor dem Slot-Rückzug 0x07 [00] (Stopp).
FUNC_CONNECTION_MOTOR = 0x07
FUNC_GET_VERSION_SN = 0x14
# 0x15: Host-Name GET_HARDWARE_STATUS (LIVE BELEGT 15.10.2026, .so
# communication_get_hardware_status(addr), Request leer). Antwort 16 Byte:
# 6 Einträge ((i+1)<<4)|X[i] + 10 x 00 (FW-Analyse FUN_0800f16e; live
# 10 20 30 40 50 60 00.. = alle X 0). ~~Bedeutung von X offen~~ CFS-FW:
# Init-Flags EEPROM/AHT30/Odometer/Buffer/RFID0/RFID1 (HW_COMPONENTS).
FUNC_GET_HARDWARE_STATUS = 0x15

# --- Retries/Timeouts wie im Original (LIVE BELEGT 15.10.2026, alle 6
# klippy.logs 05.-16.10.; je Sendung eine Zeile "retries = N, cmd = ..."; N
# zählt HERUNTER, retries=4 -> 5 Sendungen (Erstsendung + 4), Abstand ~2,0 s
# bei 2-s-Timeout, z.B. Live-Log 05.10.2026).
# Startwert je fun an Box 0x01 (Häufigkeit des Startwerts in Klammern):
#   0x02 4 (18) | 0x03 4 (844) | 0x04 4 (154) | 0x05 4 (16) | 0x08 4 (96)
#   0x0a 0 (10 560) | 0x0d 4 (55) | 0x0e 4 (5 415) | 0x0f 4 (35)
#   0x10 4 (191) | 0x11 4 (90) | 0x14 4 (78) | 0x15 4 (10)
# Auto-Addr 0xa0..0xa3, Motoren 0x81..0x85, 0xff-Broadcasts: retries=0
#   (nicht hier, s. auto_addr.py / motor.py). Belt 0x21/0x22: retries=4.
# 0x10/0x11: BEWUSSTE ABWEICHUNG vom Original (Original 4, Review M3
# 16.10.2026): hier retries=0. Begründung wie bei 0x0d op 2/3 – 0x10/0x11
# lösen Mechanik aus (Fördern/Rückzug), ein Retransmit nach Timeout würde
# den Vorgang ein zweites Mal starten; die Antwort kommt erst nach dem
# Vorgang (0x10 mode 0 live <= 7,25 s, 0x11 p1 <= 23 s) -> lange Timeouts
# (FUNC_TIMEOUTS_SO) statt Wiederholung. FUNC_RETRIES_ORIGINAL hält die
# live gezählten Original-Werte fest.
FUNC_RETRIES_ORIGINAL = {
    FUNC_GET_RFID: 4, FUNC_GET_REMAIN_LEN: 4, FUNC_SET_BOX_MODE: 4,
    FUNC_GET_BUFFER_STATE: 4, FUNC_GET_FILAMENT_SENSOR_STATE: 4,
    FUNC_GET_BOX_STATE: 0, FUNC_SET_PRE_LOADING: 4,
    FUNC_MEASURING_WHEEL: 4, FUNC_TIGHTEN_UP_ENABLE: 4, 0x10: 4, 0x11: 4,
    FUNC_GET_VERSION_SN: 4, FUNC_GET_HARDWARE_STATUS: 4,
}
FUNC_RETRIES = dict(FUNC_RETRIES_ORIGINAL)
FUNC_RETRIES.update({0x10: 0, 0x11: 0})  # bewusste Abweichung (s. oben)
# Host-Timeouts (s) laut box_wrapper-Modulkonstanten (box_wrapper-API,
# „Timeouts“), live konsistent: 0x0d op 2 Antwort nach 31-37 s bei EINER
# Sendung, 0x10 mode 0 nach <=7,25 s, 0x11 p1 nach <=23 s, sonst 2 s
# (Retry-Abstand 2,00-2,17 s). 0x0a: Konstante GET_BOX_STATE=3600 existiert,
# ob sie der Poll-Timeout ist, ist unbelegt (Poll-Antworten 3-390 ms).
# GENUTZT (Review M3, 16.10.2026) von BoxAction.communication_extrude (0x10,
# 15 s > live 7,25 s) und communication_retrude (0x11, 150 s > live 23 s).
# 0x0d nutzt bewusst preload_force_timeout (>= 380 s, max(180+200·n, 300·n))
# statt 300 s.
FUNC_TIMEOUTS_SO = {
    FUNC_SET_PRE_LOADING: 300.0, 0x10: 15.0, 0x11: 150.0,
}


def func_retries(fun):
    """Original-Retries je fun (FUNC_RETRIES; unbekannt -> 4 = Host-Default)."""
    return FUNC_RETRIES.get(fun, serial485.MAX_RETRIES)
# 0x10/0x11 (Extrude/Retrude): siehe cfsseq.py, hier nur als Konstanten zum
# Referenzieren (keine eigenen Builder – kein Duplikat).
FUNC_EXTRUDE = 0x10
FUNC_RETRUDE = 0x11
# 0x10-Modi / 0x11-Parameter: definiert in cfsseq.py (belegt, FW-Analyse
# FUN_080045c6 / FUN_08004d90), hier nur re-exportiert – kein Duplikat.
from cfsseq import (  # noqa: E402,F401
    EXTRUDE_MODE_STAGE1, EXTRUDE_MODE_FEED_TO_STALL, EXTRUDE_MODE_FEED_CHECKED,
    EXTRUDE_MODE_CLEAR_ERROR, EXTRUDE_MODE_FEED_START, EXTRUDE_MODE_QUERY,
    EXTRUDE_MODE_STOP_BUFFER, EXTRUDE_MODE_EXTRUDER_CHECK, EXTRUDE_MODES,
    EXTRUDER_CHECK_MM, RETRUDE_P_TO_BUFFER, RETRUDE_P_PULL_HUB,
    slot_to_mask, check_one_hot)

# --- Status-Byte der ANTWORTEN (BELEGT 15.10.2026, FW-Analyse CFS-FW R153).
# Auswertung seit 16.10.2026 in
# BoxAction (CfsStatusError, Review M2); die reinen Parser ignorieren ihn.
# Hinweis: das Status-Byte im REQUEST ignoriert die App (Dispatcher liest
# frame[2] nie) – STATUS_OPERATIONAL 0xFF bleibt aus Kompatibilität.
STATUS_OK = 0x00
STATUS_INVALID = 0x01        # len/Maske/Subcode ungültig, fun unbekannt (FUN_0800ebc2/eb88)
STATUS_CRC = 0x02            # CRC-Fehler im Request (FUN_0800f880)
STATUS_BUSY = 0x03           # busy / falscher Box-Zustand; 0x50: Wert unverändert
STATUS_FEED_OUT = 0x05       # cmd_parse_out (FUN_08004df2); CFS-FW: auch 0x10 mode 0
#                              feed_top_to_hub FUN_080042a8 – Hub-Sensor nicht in 7 s (700x10 ms) erreicht (B+L)
STATUS_HUB_TIMEOUT = STATUS_FEED_OUT  # Alias (Bedeutung aus der FW-Analyse)
STATUS_BUFFER_FILL_TIMEOUT = 0x06  # buffer_fill_timeout (FUN_08004df2)
STATUS_PRETENSION = 0x08     # Pretension-Warten erfolglos (FUN_08004460)
STATUS_HUB_NO_MOVE = 0x09    # feed_top_to_hub: Odometer bewegt sich nicht (FUN_08004190)
STATUS_FEED_TIMEOUT = 0x0A   # mode 4: 1250x20 ms = 25 s ohne Abschluss; mode 2: <5 mm in 3 s (FUN_08025c4e/08003efa)
STATUS_FEED_MAX_LEN = 0x0B   # Fördern > 3000 mm (odom_start-odom, @0x08025f24)
STATUS_ODOMETER_STALL = 0x0C  # 25x20 ms in Folge <0,01 mm Vorwärts = 500 ms (@0x08025f28; Rückfedern zählt mit)
STATUS_BUFFER_NOT_FULL = 0x0D  # Buffer nicht voll (FUN_08025d96)
STATUS_EXTRUDER_TIMEOUT = 0x0E  # feed extruder timeout, 0x10 mode 7 (FUN_08025dea)
STATUS_RETRACT_BUFFER_TIMEOUT = 0x13  # Rückzug: Buffer-Sensor 5 s (FUN_08004c9a)
STATUS_PULL_HUB_TIMEOUT = 0x14  # Pull-back Stufe 2: Hub nach 7 s noch belegt (FUN_08004884)
STATUS_NO_FILAMENT = 0x16    # Slot ohne Filament (FUN_08004d4a/0800e81a), setzt ERROR
STATUS_PULL_HUB_SENSOR = 0x17  # vor Pull-back: Einlauf-Sensor A meldet kein Filament (FUN_08004c5a)
STATUS_NO_SLOT_INTERNAL = 0x18  # intern, wird auf 0x16 abgebildet
STATUS_PULL_HUB_BLOCK = 0x19  # Pull-back p=1: Odometer <0,01 mm/20 ms UND R[3] (Motorblock >10 s) (FUN_0800494e)
STATUS_PULL_HUB_ODO_TIMEOUT = 0x1A  # Pull-back p=1: 2000x20 ms = 40 s ohne Hub frei (CFS-FW)
STATUS_PRELOAD_EVENT = 0x30  # NUR 0x0a: Preload-Ereignis anstehend (+4 B)
# Host-Name (LIVE 15.10.2026, klippy.log "UPDATE_STATE[0x30]"):
STATUS_UPDATE_STATE = STATUS_PRELOAD_EVENT
# 0x50: ~~Sensorprüfung, Deutung offen~~ CFS-FW 16.10.2026 (B): nur
# STATE_PRINT, aktueller Druck-Slot: (Einlauf frei) | (Hub frei)<<1 != 0 ->
# 0x50, setzt R[8]=1 = Runout/Filament-Verlust im druckenden Slot.
STATUS_PRINT_SENSOR = 0x50
STATUS_RUNOUT = STATUS_PRINT_SENSOR  # Alias (CFS-FW)
# 0x51: ~~nur LED-Mapping~~ LIVE BELEGT 15.10.2026 als echter Fehlercode
# (0x0a in STATE_ERROR, Host "SPEED_ERR[0x51]", key846 „empty printing, box
# speed is smaller than extruder“; Live-Log 12.10.2026, 452x).
# CFS-FW (B): Thread print_check, ~10 s ohne „buffer full“ und
# Odometer >= 40 mm nachgeschoben ODER >= 3 Buffer-leer-Meldungen.
STATUS_SPEED_ERR = 0x51
STATUS_BUFFER_SLOW = STATUS_SPEED_ERR  # Alias (CFS-FW)
# 0x52: CFS-FW (B): print_check, ~10 s ohne „buffer full“ und Odometer
# < 5 mm (Box soll fördern, nichts bewegt sich), nur ohne vorherigen 0x50.
STATUS_NO_FEED = 0x52
# 0x53: wird von der App NIE erzeugt (nur LED-Mapping FUN_08010c8a,
# Zweig in FUN_0800ed26 toter Code) – bewusst keine Konstante.

# Ereigniswerte im 0x30-UPDATE_STATE (je Slot A..D ein Byte). FW-Analyse:
# Werte 1/2/3 aus FUN_080065f4; Bedeutung DEUTUNG, live gestützt 15.10.2026
# (2 -> danach remain 00; 1 -> Preload startet; 3 -> danach GET_RFID).
EVENT_NONE = 0
EVENT_INSERT = 1         # Einstecken / Preload-Start
EVENT_REMOVE = 2         # Herausziehen
EVENT_PRELOAD_DONE = 3   # Preload fertig
EVENT_NAMES = {EVENT_NONE: "-", EVENT_INSERT: "Einstecken",
               EVENT_REMOVE: "Herausziehen", EVENT_PRELOAD_DONE: "Preload fertig"}
STATUS_NAMES = {
    STATUS_OK: "ok",
    STATUS_INVALID: "ungültig (len/Maske/Subcode/fun)",
    STATUS_CRC: "CRC-Fehler im Request",
    STATUS_BUSY: "busy / falscher Zustand",
    STATUS_FEED_OUT: "Feeder-Out / Hub nicht in 7 s erreicht",
    STATUS_BUFFER_FILL_TIMEOUT: "buffer_fill_timeout",
    STATUS_PRETENSION: "Pretension erfolglos",
    STATUS_HUB_NO_MOVE: "feed_top_to_hub: Odometer steht",
    STATUS_FEED_TIMEOUT: "Förder-Timeout (25 s / <5 mm in 3 s)",
    STATUS_FEED_MAX_LEN: "Fördern > 3000 mm",
    STATUS_ODOMETER_STALL: "Odometer-Stillstand 500 ms",
    STATUS_BUFFER_NOT_FULL: "Buffer nicht voll",
    STATUS_EXTRUDER_TIMEOUT: "feed extruder timeout",
    STATUS_RETRACT_BUFFER_TIMEOUT: "Rückzug: Buffer-Sensor-Timeout",
    STATUS_PULL_HUB_TIMEOUT: "Pull-back: Hub nach 7 s noch belegt",
    STATUS_NO_FILAMENT: "Slot ohne Filament",
    STATUS_PULL_HUB_SENSOR: "Pull-back: Einlauf-Sensor leer",
    STATUS_NO_SLOT_INTERNAL: "kein Slot belegt (intern)",
    STATUS_PULL_HUB_BLOCK: "Pull-back: Motorblock > 10 s",
    STATUS_PULL_HUB_ODO_TIMEOUT: "Pull-back: Timeout 40 s",
    STATUS_PRELOAD_EVENT: "UPDATE_STATE / Preload-Ereignis (nur 0x0a)",
    STATUS_PRINT_SENSOR: "Runout im Druck-Slot (nur 0x0a PRINT)",
    STATUS_SPEED_ERR: "SPEED_ERR: Buffer-Nachschub zu langsam",
    STATUS_NO_FEED: "kein Vorschub im Druck (<5 mm in ~10 s)",
}


def status_name(code):
    """Klartext zu einem Antwort-Status-Byte (unbekannt -> 'unbekannt 0x..')."""
    return STATUS_NAMES.get(code, "unbekannt 0x%02x" % code)


# Prüfhinweise für die Konsole (25.10.2026, nach geknicktem
# Zufuhrschlauch bei 0x0c): was man am Gerät nachsehen sollte. Nur
# mechanische/handgreifliche Ursachen; Bus-/Protokollcodes (0x01-0x03) haben
# keinen Hinweis.
_SCHLAUCH = "Zufuhrschlauch an der CFS und zum Kopf geknickt oder abgesteckt?"
_SPULE = "Spule läuft frei (kein Knoten, keine eingeklemmte Windung)?"
STATUS_CHECK_HINTS = {
    STATUS_FEED_OUT: ["Filament im Slot bis zum Einzug eingesteckt?",
                      "Spitze sauber schräg abgeschnitten (nicht geknickt)?",
                      "Weg vom Slot zum Hub frei (kein Filamentrest)?"],
    STATUS_BUFFER_FILL_TIMEOUT: [_SCHLAUCH, "Puffer am Kopf frei beweglich?"],
    STATUS_PRETENSION: [_SPULE, "Filament straff bis zum Einzug?"],
    STATUS_HUB_NO_MOVE: [_SPULE, "Filament am Einzug des Slots eingeklemmt?",
                         "Filamentrest im Hub/Verteiler der CFS?"],
    STATUS_FEED_TIMEOUT: [_SCHLAUCH, _SPULE,
                          "Filamentrest im Hub/Verteiler der CFS?"],
    STATUS_FEED_MAX_LEN: ["Zufuhrschlauch am Kopf abgesteckt (Filament läuft "
                          "ins Leere)?", "Kopf-Filamentsensor funktioniert?"],
    STATUS_ODOMETER_STALL: [_SCHLAUCH, _SPULE,
                            "Filamentrest im Hub/Verteiler der CFS?",
                            "Spitze geknickt/verdickt (neu schräg "
                            "abschneiden)?",
                            "Kopf-Einlass/Schneider frei (Filament von Hand "
                            "bis zum Extruder durchschieben)?"],
    STATUS_BUFFER_NOT_FULL: [_SCHLAUCH, "Puffer am Kopf frei beweglich?"],
    STATUS_EXTRUDER_TIMEOUT: ["Kopf-Einlass/Schneider frei?",
                              "Düse heiß genug und nicht verstopft?",
                              "Extruder-Zahnrad greift das Filament?"],
    STATUS_RETRACT_BUFFER_TIMEOUT: ["Filament im Kopf geschnitten (Schneider)?",
                                    _SCHLAUCH],
    STATUS_PULL_HUB_TIMEOUT: [_SCHLAUCH, "Spule wickelt beim Rückzug auf?"],
    STATUS_NO_FILAMENT: ["Filament im Slot eingelegt bzw. Spule leer?"],
    STATUS_PULL_HUB_SENSOR: ["Filament im Slot eingelegt bzw. Spule leer?"],
    STATUS_PULL_HUB_BLOCK: ["Filament im Kopf geschnitten (Rest im Kopf "
                            "blockiert den Rückzug)?", _SCHLAUCH, _SPULE],
    STATUS_PULL_HUB_ODO_TIMEOUT: [_SCHLAUCH, _SPULE,
                                  "Filamentrest im Hub/Verteiler der CFS?"],
    STATUS_PRINT_SENSOR: ["Spule leer oder Filament gerissen?"],
    STATUS_SPEED_ERR: [_SPULE, _SCHLAUCH],
    STATUS_NO_FEED: ["Düse verstopft?", _SCHLAUCH, _SPULE],
}


def status_check_hint(code):
    """'Bitte überprüfen Sie: 1) … 2) …' zu einem Status-Byte, sonst ''."""
    items = STATUS_CHECK_HINTS.get(code)
    if not items:
        return ""
    return "Bitte überprüfen Sie: " + " ".join(
        "%d) %s" % (i, t) for i, t in enumerate(items, 1))


# Host-Fehlertexte key831–865 (18.10.2026, Abgleich mit gitstonelabs).
# Quelle der Texte: strings der EIGENEN Stock-box_wrapper.cpython-39.so
# (K2 FW 1.1.7.0, /usr/share/klipper/klippy/extras/), wörtlich
# ohne Formatplatzhalter; deckt sich mit der Fremd-Liste (gitstonelabs
# docs/protocol.md 724-758). key842/key854 kommen in UNSERER .so nicht vor -> fehlen hier.
HOST_ERROR_KEYS = {
    "key831": "serial_485 communication timeout",
    "key834": "params error, send data",
    "key835": "extrude error, maybe it's blocked at the connections",
    "key836": "extrude error, maybe there's a blockage between the "
              "connections and the filament sensor",
    "key837": "extrude error, maybe there is a blockage between the filament "
              "sensor and the extrusion gear",
    "key838": "extrude error, through the connections but not extrude",
    "key839": "filament error, no filament detected at box extrude position",
    "key840": "box switch state error, check it's current action",
    "key841": "cut error, cut sensor not detected, cutting not rebound",
    "key843": "rfid is error, get rfid",
    "key844": "the pneumatic joint is abnormal and may collapse",
    "key845": "the nozzle is blocked",
    "key846": "empty printing, box speed is smaller than extruder",
    "key847": "empty printing, material enwind",
    "key848": "material err, may break at connections",
    "key849": "retrude error, failed to exit connections",
    "key850": "retrude error, unspecified retrude, multiple connections "
              "triggered",
    "key851": "retrude error, retrude but not trigger buffer empty limit",
    "key852": "check extruder filament sensor and box sensor state",
    "key853": "humidity sensor error",
    "key855": "cut position error",
    "key856": "no cutter",
    "key857": "motor load error",
    "key858": "errprom error",
    "key859": "measuring wheel error",
    "key860": "buffer error",
    "key861": "left rfid card error",
    "key862": "right rfid card error",
    "key863": "retrude error, retrude success but filament sensor is detected",
    "key864": "extrude error, extrude but not trigger buffer full limit",
    "key865": "retrude error, failed to exit connections",
}
# Box-Status -> Host-key: NUR live belegte Zuordnungen (eigene klippy.log,
# Statuszeile vor der key-Meldung): 0x05 EXTRUDE_ERR1 -> key835
# (Live-Log 12.10.2026), 0x0c EXTRUDE_ERR8 -> key836 (nach dem
# Stock-Retry, Live-Log 12.10.2026), 0x14 RETRUDE_ERR2 -> key849
# (Live-Log 12.10.2026), 0x19 RETRUDE_ERR6 -> key865 (Live-Log 13.10.2026),
# 0x51 SPEED_ERR -> key846 (Live-Log 12.10.2026).
STATUS_HOST_KEY = {
    STATUS_FEED_OUT: "key835",
    STATUS_ODOMETER_STALL: "key836",
    STATUS_PULL_HUB_TIMEOUT: "key849",
    STATUS_PULL_HUB_BLOCK: "key865",
    STATUS_SPEED_ERR: "key846",
}


def host_error_text(code):
    """Box-Status -> (key, Text) nach STATUS_HOST_KEY, sonst (None, None)."""
    key = STATUS_HOST_KEY.get(code)
    return (key, HOST_ERROR_KEYS.get(key)) if key else (None, None)


class CfsStatusError(Exception):
    """Box-Antwort mit Status != 0 (Review M2, 16.10.2026).

    Attribute: status (int), name (STATUS_NAMES-Klartext), data (bytes,
    Antwortdaten – z.B. 0x10 mode 5 liefert trotz Fehler den Odometer-Float,
    0x10 mode 0 / Busy 1 Byte Box-Zustand), fun (Antwort-fun)."""

    def __init__(self, status, name, data=b"", fun=None):
        self.status, self.name, self.data, self.fun = status, name, bytes(data), fun
        Exception.__init__(self, "Box-Status 0x%02x (%s)%s, Daten %s" % (
            status, name, "" if fun is None else " auf fun 0x%02x" % fun,
            bytes(data).hex(" ") or "-"))


class StatusBytes(bytes):
    """Antwortdaten (bytes, API-kompatibel) + Attribut .status (Status-Byte
    der Antwort; None, wenn der Bus kein transact_full bietet) und .fun.
    get_box_status/parse_box_state werten .status bei Rohdaten aus."""

    def __new__(cls, data=b"", status=None, fun=None):
        obj = bytes.__new__(cls, bytes(data))
        obj.status, obj.fun = status, fun
        return obj


# --- Box-Zustand (0x0a Byte 3), BELEGT 15.10.2026 (FW-Analyse FUN_0800fa0c/
# FUN_0800ed26). STATE_CHECK_HUB ist KEIN Box-Zustand, sondern Zustand 2 der
# Preload-Statemachine FUN_080064b8 (taucht in 0x0a nicht auf).
BOX_STATE_IDLE = 0
BOX_STATE_PRELOAD = 1
BOX_STATE_PRINT = 2
BOX_STATE_RELOAD = 3
BOX_STATE_ERROR = 4
BOX_STATE_TEST = 5
BOX_STATE_NAMES = {
    BOX_STATE_IDLE: "STATE_IDLE", BOX_STATE_PRELOAD: "STATE_PRELOAD",
    BOX_STATE_PRINT: "STATE_PRINT", BOX_STATE_RELOAD: "STATE_RELOAD",
    BOX_STATE_ERROR: "STATE_ERROR", BOX_STATE_TEST: "STATE_TEST",
}

# Preload-/RFID-Control-Aktion (0x0d, 2. Datenbyte). KORREKTUR 15.10.2026:
# Die frühere Belegung 00=Arm/01=Disarm/02=Rearm (alte Annahme) ist
# durch KEINEN Live-Frame gestützt (live nur "0x0d len 3" belegt; die ACK-Bytes f7 01 03 00 0d 9e stammen aus ref-hi
# und sind nicht live gesehen; kein 0x0d-Request mitgeschnitten). Beide unabhängigen
# Referenzen sagen übereinstimmend das Gegenteil:
# - nachbau/ref-hi/valid_messages.json: f70105ff0d0f0169 =
#   "CMD_SET_PRE_LOADING box 1, mask=0x0F enable=0x01"
# - k2-vanilla files/box_protocol.py (CMD_RFID_CONTROL=0x0D):
#   set_rfid_insert_reading(enabled) -> (0, int(enabled)),
#   force_rfid_read(mask) -> (mask, 2)
# Daher: 0x00 = Insert-Reading/Preload aus, 0x01 = an, 0x02 = Force-Read.
# ~~TODO (unbelegt): ob das 1. Byte bei 0x00/0x01 die Maske ist (ref-hi: 0x0F)
#   oder ignoriert wird (vanilla: 0)~~ BELEGT 15.10.2026 (FW-Analyse, Handler
#   FUN_0800f0da @CFS-App R153): bei op 0/1 wird die Maske IGNORIERT
#   (FUN_08005376 disable / FUN_0800536e enable wirken global); die
#   übergebene Maske wird trotzdem unverändert gesendet (ref-hi-Vektor).
# op 2 = FUN_08004ec4(mask, 1): Forced Preload MIT RFID (pretension ->
#   STATE_CHECK_HUB -> read rfid + margin estimation) = Force-Read.
# op 3 = FUN_08004ec4(mask, 0): Forced Preload OHNE RFID (nur pretension) – NEU.
# op > 3 -> Status 1. Antwort bei op 2/3 erst NACH dem Vorgang (s.
#   preload_force_timeout).
PRELOAD_DISABLE = 0x00
PRELOAD_ENABLE = 0x01
PRELOAD_FORCE_READ = 0x02
PRELOAD_FORCE_RFID = PRELOAD_FORCE_READ  # Alias (FW-Name)
PRELOAD_FORCE_TENSION_ONLY = 0x03
PRELOAD_ACTIONS = (PRELOAD_DISABLE, PRELOAD_ENABLE, PRELOAD_FORCE_READ,
                   PRELOAD_FORCE_TENSION_ONLY)
PRELOAD_FORCE_ACTIONS = (PRELOAD_FORCE_READ, PRELOAD_FORCE_TENSION_ONLY)

# Laufzeit Forced Preload (BELEGT 15.10.2026, FW-Analyse): FUN_08004ec4 wartet in
# Zustand PRELOAD bis 179 s (0xb3 x 1 s) auf ein laufendes Preload bzw. zieht
# in IDLE erst alle Slots am Hub zurück (FUN_08004cd0(4,1)); dann je Slot der
# Maske NACHEINANDER FUN_08004e80: 5 s + bis 180 s Polling (0xb4 x 1 s).
# Die 0x0d-Antwort kommt erst danach. -> Timeout = Vorlauf + je Slot.
PRELOAD_FORCE_TIMEOUT_BASE = 180.0      # s, Vorlauf (Warten/Pull-back)
PRELOAD_FORCE_TIMEOUT_PER_SLOT = 200.0  # s, 5 s + 180 s + Reserve je Slot
# Stock-Host (Analyse box_wrapper, B): Timeout je
# Versuch = Slotanzahl x cmd_timeout['SET_PRE_LOADING'] (TIMEOUT_LONGER_TIME
# = 300 s, PyNumber_Multiply @0x158b3c), bis 5 Versuche, keine Gesamtfrist.
PRELOAD_FORCE_TIMEOUT_STOCK_PER_SLOT = 300.0


def preload_force_timeout(mask):
    """Host-Timeout (s) für 0x0d op 2/3:
    max(180 + 200·n, 300·n), n = Slots der Maske (Maske 0 -> n = 4).
    Ergebnis 380 / 600 / 900 / 1200 s für 1 / 2 / 3 / 4 Slots.

    ÄNDERUNG 17.10.2026: bisher 180 + 200·n
    (380/580/780/980 s). Box-Worst-Case (V, aus der App-Analyse): je Slot 5 s +
    <= 180 s, vorher <= 179 s Warten (Zustand PRELOAD) bzw. Pull-back
    hub-belegter Slots -> 1 Slot bis 364 s (deckte 380 s), 4 Slots bis
    ~966 s (980 s nur ~14 s Reserve). Neu mindestens der Stock-Wert je
    Versuch (n x 300 s), damit nie kürzer gewartet wird als Stock:
    4 Slots 1200 s (>= ~230 s Reserve). Gleichzeitig = serial485
    MAX_BUS_HOLD_S (1200 s x 1 Versuch, erlaubt). retries bleibt 0 (der
    Aufrufer übergibt retries=0; ein Retransmit würde den Force-Preload
    erneut auslösen, die Box hat keine Duplikaterkennung). Stock dagegen:
    bis 5 Versuche, Maske 0 -> 0 s -> 5 Sendungen in ~20 ms.

    Maske 0 (Review G2, Entscheidung 16.10.2026): wird wie 4 Slots gerechnet
    (jetzt 1200 s), NICHT abgelehnt. Gründe: der Host sendet live [00 02]
    („Force alle“, G-Code-Pfad) – Ablehnen würde
    diesen belegten Request unmöglich machen. Die Laufzeit ist für Maske 0
    unbelegt: FW-Schleife über die Maskenbits ergäbe 0 Slots, der
    Vorlauf (FUN_08004cd0(4,1)) betrifft aber ALLE Slots am Hub (live: Box
    ~11 s stumm). Bei retries=0 ist ein zu kurzer Timeout schädlicher als
    ein zu langer (Spätantwort kollidiert mit Folgeverkehr), daher die
    konservative Obergrenze. Früher: max(1, n) = 380 s.
    """
    n = bin(check_mask(mask)).count("1")
    if n == 0:
        n = len(SLOT_MASKS)
    return max(PRELOAD_FORCE_TIMEOUT_BASE + PRELOAD_FORCE_TIMEOUT_PER_SLOT * n,
               PRELOAD_FORCE_TIMEOUT_STOCK_PER_SLOT * n)


def check_mask(mask):
    """Slotmaske 0x00..0x0f prüfen und zurückgeben (Mehrfachmasken erlaubt).

    Wire-Parameter wie communication_get_rfid(addr, num) sind im Original
    eine MASKE (box_wrapper-API: num=1 -> data b'\\x01', num=15 -> b'\\x0f'),
    keine Slotnummer – es findet KEINE Umdeutung statt.
    """
    if isinstance(mask, bool) or not isinstance(mask, int) \
            or not 0 <= mask <= MASK_ALL:
        raise ValueError("Maske muss int 0x00..0x0f sein: %r" % (mask,))
    return mask


# slot_to_mask (Slot-Index 0..3 -> 1-hot-Maske): einzige Implementierung in
# cfsseq.py, unten mit importiert (Review G6, 16.10.2026; kein Zirkelimport,
# cfsseq importiert nur serial485).


def _is_full_frame(raw):
    """Voll-Frame erkennen: Head 0xF7, Mindestlänge 6 UND
    (a) Längenfeld passt exakt zur Gesamtlänge (len + 3) – CRC prüft danach
        parse_frame (Fehler -> ValueError, wie bisher), oder
    (b) Review G4 16.10.2026: Puffer ist LÄNGER als len + 3 (angehängte
        Bytes, z.B. Folgeframe-Anfang) UND die CRC über die ersten len + 3
        Bytes ist gültig -> Frame (Anhang wird ignoriert). Früher galt so
        ein Puffer als Rohdaten (Frame-Bytes landeten im Parser).
    Rohdaten, die zufällig mit 0xF7 beginnen, gelten nur bei passendem
    Längenfeld (a) bzw. passendem Längenfeld + gültiger CRC (b) als Frame.
    TODO: Restmehrdeutigkeit (Rohdaten mit 0xF7 + passendem Längenbyte) ist
      nur durch getrennte API (Frame vs. Rohdaten) restlos lösbar.
    """
    raw = bytes(raw)
    if len(raw) < 6 or raw[0] != serial485.PACK_HEAD:
        return False
    expect = raw[2] + 3
    if expect == len(raw):
        return True
    if serial485.FRAME_LEN_MIN <= raw[2] and expect < len(raw):
        return serial485.crc8_cal(raw[2:expect - 1]) == raw[expect - 1]
    return False


def _response_data(msg, expect_fun):
    """Hilfsfunktion: Voll-Frame oder Rohdaten -> Antwort-Daten (bytes).

    Voll-Frame (s. _is_full_frame) wird per serial485.parse_frame
    CRC-geprüft und der fun-Code verglichen. Sonst gilt msg als bereits
    extrahierte Rohdaten (z.B. Rückgabe von Serial485.transact).
    Status-Byte raw[3] wird hier (reine Parser) NICHT ausgewertet; die
      Statusprüfung macht BoxAction (CfsStatusError, Review M2 16.10.2026).
    """
    raw = bytes(msg)
    if _is_full_frame(raw):
        fun, data = serial485.parse_frame(raw)
        if fun != expect_fun:
            raise ValueError("fun-Mismatch: 0x%02x, erwartet 0x%02x"
                             % (fun, expect_fun))
        return data
    return raw


# =====================================================================
# 1) ParseData – reine Funktionen (Signaturen sinngemäß nach API-Doku:
#    get_rfid(msg); get_remain_len/get_measuring_wheel(data_send, msg);
#    get_box_status(msg)).
# =====================================================================

def get_rfid(msg):
    """RFID-Antwort parsen (ASCII, ';'-getrennt, Key=Slot-Buchstabe).

    Belegt (Live 12.10.2026): b"A:<hex>;" / b"B:unknown;" / b"C:busy;".
    msg = Voll-Frame (fun 0x02, CRC-geprüft) oder Rohdaten.
    Gibt dict {Slot-Key: Wert} zurück. Leere Antwort -> {}.
    Je Slot-Key wird der Wert bis zum nächsten ';' übernommen; weitere
    ';'-Felder ohne gültigen Slot-Key ("X:..." mit X in A..D) werden
    VERWORFEN, ebenso Felder ohne ':'.
    ERLEDIGT 15.10.2026 (vorher TODO): nach dem vender-Hex folgen KEINE
      weiteren Felder (Frame live vollständig, 49 Tokens/CRC ok). Farbe und
      Material stecken IM 40-Zeichen-vender selbst -> split_rfid_vendor();
      remain kommt separat über 0x03.
    """
    data = _response_data(msg, FUNC_GET_RFID)
    result = {}
    for part in data.decode("ascii", "replace").split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        key, _, value = part.partition(":")
        key, value = key.strip(), value.strip()
        if len(key) == 1 and key in SLOT_KEYS:
            result[key] = value
    return result


# Feldlayout des 40-Zeichen-RFID-vender-Strings (Reihenfolge + Breite).
# Quelle: fake-name/cfs-reverse-engineering firmware.md:340-352 (Feldnamen
# aus dem printf-Format der CFS-FW, month=%c -> 1 Zeichen, Rest %.*s) und
# firmware.md:521-523 (Beispiel-String, 11+13+16 Zeichen = 40). Breiten
# daraus abgeleitet + gegen unseren Live-Query geprüft (mat 101001 und
# col 0FFFFFF stehen exakt an Pos. 11..16 bzw. 17..23). Belegstärke:
# mittel-stark (Grenzen konsistent; Datums-/Supplier-Semantik unbelegt).
RFID_VENDOR_FIELDS = (
    ("month", 1), ("day", 2), ("year", 2), ("supplier", 4), ("batch", 2),
    ("mat_id", 6), ("col", 7),
    ("len", 4), ("number", 6), ("reserve", 6),
)
RFID_VENDOR_LEN = 40


def split_rfid_vendor(s):
    """40-Zeichen-RFID-vender-String in seine Felder zerlegen (rein, kein Bus).

    s = str oder bytes (ASCII), exakt 40 Zeichen, sonst ValueError.
    Gibt dict {month, day, year, supplier, batch, mat_id, col, len, number,
    reserve} mit den Roh-Teilstrings zurück – KEINE Interpretation (z.B.
    mat_id 101001 = Hyper PLA laut Live-Query/Display, col 0FFFFFF = weiß;
    Einheit von len und Datumskodierung sind unbelegt).
    """
    if isinstance(s, (bytes, bytearray)):
        s = bytes(s).decode("ascii")
    if not isinstance(s, str):
        raise ValueError("vender muss str/bytes sein: %r" % (s,))
    if len(s) != RFID_VENDOR_LEN:
        raise ValueError("vender muss %d Zeichen haben, hat %d: %r"
                         % (RFID_VENDOR_LEN, len(s), s))
    out, pos = {}, 0
    for name, width in RFID_VENDOR_FIELDS:
        out[name] = s[pos:pos + width]
        pos += width
    return out


def get_remain_len(data_send, msg):
    """Restmengen-Antwort parsen: 4 Bytes positional A,B,C,D (0-100 %).

    Belegt: Live-Frame f7 01 07 00 03 1c ff 00 00 11 -> A=28 %, B=n/a, C/D=0;
    0xff = kein Wert (nicht ausgewählt / keine Daten), 0x00 s. ACHTUNG.
    data_send = Request-Kontext (Slotmaske, vgl. .so-Signatur) – wird hier
      nur entgegengenommen/dokumentiert, nicht zur Filterung benutzt
      (TODO: ob das .so danach filtert, ist unbelegt).
    msg = Voll-Frame (fun 0x03) oder 4 Rohbytes; andere Länge -> ValueError.
    Gibt dict {Slot-Key: Prozent oder None} zurück.
    ACHTUNG Semantik: 0 heißt NICHT sicher „leer“ – live hat Slot B mit
      remain 0 (Vendor unknown, kein RFID-Restwert) 160 mm gefördert
      (Testlauf T1B). 0 = „kein RFID-Restwert“.
    PRÄZISIERT 15.10.2026 (FW-Analyse FUN_0800ef66): Wire-0xff = nicht in Maske /
      Slot preloadet / Filament da OHNE RFID-Daten (Live B:unknown -> 0xff ✓);
      Wire-0 = Sensor A meldet kein Filament ODER gespeicherter Wert 0 %.
      „remain 0“ bei T1B war ein Host-Zustand, kein Wire-Byte. Parsing
      unverändert (Werte sind mehrdeutig, None/0 bleiben wie geliefert).
    FW-ABHÄNGIG (18.10.2026, Abgleich mit gitstonelabs): Slot mit
      Filament OHNE RFID meldet R153 0xff (-> None, s. oben), Box-FW 113
      dagegen 0x64 (= 100 %, Hi-Mitschnitt TC @23173) – bei FW 113 ist
      100 also KEIN belastbarer Restwert. Kein FW-Parameter hier (Parser
      bleibt FW-neutral); Aufrufer mit box_fw "113" werten 100 als
      „unbekannt“ (REMAIN_NO_RFID_BY_FW).
    """
    data = _response_data(msg, FUNC_GET_REMAIN_LEN)
    if len(data) != 4:
        raise ValueError("Remain-Antwort muss 4 Bytes haben: %r" % (bytes(data),))
    return {key: (None if data[i] == NO_DATA else data[i])
            for i, key in enumerate(SLOT_KEYS)}


# Wire-Wert „Filament da, kein RFID-Restwert“ je Box-FW (get_remain_len)
REMAIN_NO_RFID_BY_FW = {113: 0x64, 153: NO_DATA}


def get_measuring_wheel(data_send, msg):
    """Messrad-Antwort parsen: 4 Bytes Big-Endian IEEE-754 Float, NEGATIV.

    Belegt/KORREKTUR 13.10.: c4e6fe0b -> -1847,94; c5009149 -> -2057,08 (KORREKTUR 15.10.: vorher Tippfehler c5019149/-2073,08);
    c52a4e54 -> -2724,89 (Klippy loggt data_hex -> Float selbst). Der Betrag
    wächst beim Fördern; kein Zähler (vgl. gitstonelabs).
    data_send = Request-Kontext (belegt [01]) – wie bei remain nur Kontext.
    msg = Voll-Frame (fun 0x0e) oder 4 Rohbytes. Sonst ValueError.
    """
    data = _response_data(msg, FUNC_MEASURING_WHEEL)
    if len(data) != 4:
        raise ValueError("Messrad-Antwort muss 4 Bytes haben: %r" % (bytes(data),))
    (value,) = struct.unpack(">f", bytes(data))
    return value


def get_box_status(msg):
    """Box-State/Herzschlag parsen (Func 0x0a, alle ~4 s).

    Belegt: f7 01 09 00 0a 13 34 00 00 00 00 d7 (6 Datenbytes);
    Variante f7 01 07 30 0a 03 00 00 00 51 (4 Datenbytes, Status 0x30);
    FW 113: f7 01 07 00 0a 17 34 00 00 81 (4 Datenbytes, Status 0 – live
    05.10.2026, KEIN Ereignis; s. parse_box_state).
    Original pollt 0x0a mit retries=0 alle ~4 s (live 10 560x).
    ~~Semantik der Einzelbytes ist UNBELEGT~~ BELEGT 15.10.2026 (FW-Analyse
    FUN_0800ed26) – benannte Felder liefert parse_box_state(); diese
    Funktion bleibt generisch (b0..bN, unverändert).
    Gibt dict {raw, len, frame_status (None bei Rohdaten ohne .status),
    b0..bN} zurück. StatusBytes (BoxAction-Rückgabe) liefern frame_status.
    """
    raw = bytes(msg)
    frame_status = None
    if _is_full_frame(raw):
        frame_status = raw[3]
        data = _response_data(raw, FUNC_GET_BOX_STATE)
    else:
        data = raw
        # Rohdaten von BoxAction (StatusBytes) tragen den Status mit
        frame_status = getattr(msg, "status", None)
    out = {"raw": bytes(data), "len": len(data), "frame_status": frame_status}
    for i, byte in enumerate(bytes(data)):
        out["b%d" % i] = byte
    return out


BOX_FW_6BYTE_STATE = 153  # ab Box-App 153 hat 0x0a 6 Datenbytes (live)


def _box_fw_num(box_fw):
    """Box-App-Version -> int (z.B. 153) oder None.

    Akzeptierte Formate (Review G3, 16.10.2026, belegt):
    - Wire-Form aus 0x14 / parse_version_sn: "153", "113" (genau 3 Ziffern,
      auch bytes) bzw. int 100..999.
    - Host-Form aus klippy.log: "1.5.3" / "1.1.3" – Host loggt die Wire-
      Ziffern punktgetrennt (Live-Log 14.10.2026: Daten
      31 35 33 ... -> "version: 1.5.3"; Live-Log 05.10.2026
      "version: 1.1.3" vor dem Box-Update). Nur genau 3 einstellige Teile.
    Alles andere (z.B. "15", "1.5", "1.10.3", "153"+SN) -> ValueError –
      eine Zuordnung wie "15" -> 15x/113 ist NICHT belegt. Früher wurden die
      ersten 3 Zeichen genommen ("1.5.3" -> ValueError, "15" -> 15).
    """
    if box_fw is None:
        return None
    if isinstance(box_fw, bool):
        raise ValueError("box_fw darf nicht bool sein: %r" % (box_fw,))
    if isinstance(box_fw, int):
        if 100 <= box_fw <= 999:
            return box_fw
        raise ValueError("box_fw als int muss 3-stellig sein (z.B. 153): %r"
                         % (box_fw,))
    if isinstance(box_fw, (bytes, bytearray)):
        box_fw = bytes(box_fw).decode("ascii", "replace")
    text = str(box_fw).strip()
    if len(text) == 3 and text.isdigit() and text.isascii():
        return int(text)
    parts = text.split(".")
    if len(parts) == 3 and all(len(p) == 1 and p.isdigit() and p.isascii()
                               for p in parts):
        return int("".join(parts))
    raise ValueError("box_fw unbekanntes Format %r – erlaubt: '153'/'113', "
                     "153/113 oder Host-Form '1.5.3'/'1.1.3'" % (box_fw,))


# 0x0a Byte 2 (CFS-FW, B): R[3]|0x80 = Motorblock > 10 s (einmalig
# gemeldet), sonst R[5] = Blockmaske sofort. Bit 4 = Hauptmotor (MS3791),
# Bit 0..3 = Feeder A..D. Live 0x90 = Hauptmotor > 10 s, 0x91 = Hauptmotor
# + Feeder A, 01/02/04/08 nach Preload/Retract = Feeder-Block des Slots
# (vermutlich normaler Pretension-Endanschlag, V).
FLAG_BLOCK_LONG = 0x80
FLAG_BLOCK_MAIN_MOTOR = 0x10
FLAG_BLOCK_FEEDER_MASK = 0x0F


def _int8(v):
    return v - 0x100 if v >= 0x80 else v


def parse_box_state(msg, box_fw=None):
    """0x0a-Antwort mit Namen (BELEGT 15.10.2026, FW-Analyse FUN_0800ed26;
    LIVE-GEGENPROBE 15.10.2026, klippy.log 05.-16.10.; FW-Analyse 16.10.2026).

    Normal R153 (6 Datenbytes): b0 = S[1] = Temperatur °C, b1 = S[0] = rel.
    Feuchte % – BELEGT (CFS-FW, B+L): AHT30 @I2C 0x38, ~1,2 s Takt,
    ganzzahlig abgeschnitten, Temperatur int8 (Zweierkomplement), Feuchte
    uint8 0..100; bei AHT-Initfehler dauerhaft 0 (s. 0x15). Schlüssel
    "temp_c"/"rh"; ~~Vermutung~~ "temp_c_guess"/"rh_guess" bleiben Aliase.
    b2 = Flags (R[3]|0x80 = Motorblock > 10 s bzw. R[5] = Blockmaske; Bit 4
    Hauptmotor, Bit 0..3 Feeder A..D -> block_long/block_main_motor/
    block_feeder_mask), b3 = Box-Zustand (BOX_STATE_*; live 0,1,2,4),
    b4 = Bitmaske Sensor B/Hub (live = 0x08 [01]), b5 = Bitmaske aus
    FUN_080053f2 – ~~„Slot hat gültige RFID-Daten“~~ KORRIGIERT (CFS-FW):
    Bit i = Einlauf-Flag && Tag geladen && Tag-Typ 7 && Tag-Feld+0x10 == 0
    („Typ-7-Tag ohne Datenfreigabe“; schließt 0x02-Daten des Slots aus) ->
    Schlüssel "inlet_tag_mask"; "b5"/"rfid_valid_mask" bleiben Aliase.
    Frame-Status: ERROR -> S[3] (gespeicherter Fehlercode, live 05/0c/14/
    19/51), PRINT -> 0x50 oder 0, sonst 0.
    Ereignis (Status 0x30 UPDATE_STATE, 4 Datenbytes): je Slot A..D ein
    Ereigniswert (EVENT_*: 1 Einstecken, 2 Herausziehen, 3 Preload fertig –
    Deutung, live gestützt) – dann KEIN Zustandsbyte.
    FW 113 (Box-App vor dem Update am 12.10.2026): nur 4 Datenbytes
    b0 b1 Block-Flags Zustand mit Status 0 (live f7 01 07 00 0a 17 34 00 00
    81) -> kind 'state_v113' (KEIN Ereignis!); b0/b1 dort nur *_guess (FW
    113 nicht disassembliert). b2 = Block-Flags wie R153 (block_long/
    block_main_motor/block_feeder_mask) – DEUTUNG (V), gestützt durch den
    Hi-Mitschnitt FW 113 (gitstonelabs): 0x02 nach
    Stillstand Slot B (TC @27710), 0x08 nach Insert Slot D (TC @23393);
    Schlüssel "flags_basis" = "V" (R153: "B").
    Rohdaten ohne Frame (kein Status bekannt): 6 Byte -> 'state'; 4 Byte
    sind mehrdeutig -> nur mit box_fw entscheidbar (>=153: 'event',
    <153: 'state_v113'), sonst kind 'ambiguous' (früher fälschlich 'event').
    box_fw = Box-App-Version aus 0x14 ("113"/"153"), optional; bei
    Voll-Frames nur zur Plausibilisierung (6 Byte bei FW 113 -> 'unknown').
    Gibt dict zurück: kind ('state'/'state_v113'/'event'/'ambiguous'/
    'unknown'), frame_status, status_name, raw, und die benannten Felder.
    """
    fw = _box_fw_num(box_fw)
    base = get_box_status(msg)
    data, fst = base["raw"], base["frame_status"]
    out = {"raw": data, "frame_status": fst, "box_fw": fw,
           "status_name": None if fst is None else status_name(fst)}
    if len(data) == 4:
        if fst == STATUS_UPDATE_STATE:
            kind = "event"
        elif fst is None:  # Rohdaten: nur über die Box-FW entscheidbar
            if fw is None:
                kind = "ambiguous"
            else:
                kind = "event" if fw >= BOX_FW_6BYTE_STATE else "state_v113"
        else:
            kind = ("state_v113" if fw is None or fw < BOX_FW_6BYTE_STATE
                    else "unknown")
    elif len(data) == 6 and fst != STATUS_UPDATE_STATE and (
            fw is None or fw >= BOX_FW_6BYTE_STATE):
        kind = "state"
    else:
        kind = "unknown"
    out["kind"] = kind
    if kind == "event":
        out["events"] = {k: data[i] for i, k in enumerate(SLOT_KEYS)}
        out["event_names"] = {k: EVENT_NAMES.get(v, "unbekannt %d" % v)
                              for k, v in out["events"].items()}
    elif kind in ("state", "state_v113"):
        out.update({"s1": data[0], "s0": data[1], "flags": data[2],
                    "state": data[3],
                    "state_name": BOX_STATE_NAMES.get(data[3], "unknown"),
                    "temp_c_guess": data[0], "rh_guess": data[1]})
        fl = data[2]
        out.update({"block_long": bool(fl & FLAG_BLOCK_LONG),
                    "block_main_motor": bool(fl & FLAG_BLOCK_MAIN_MOTOR),
                    "block_feeder_mask": fl & FLAG_BLOCK_FEEDER_MASK,
                    "flags_basis": "B" if kind == "state" else "V"})
        if kind == "state":
            t = _int8(data[0])
            out.update({"temp_c": t, "rh": data[1],
                        "temp_c_guess": t, "rh_guess": data[1],
                        "sensor_b_mask": data[4], "hub_mask": data[4],
                        "inlet_tag_mask": data[5],
                        "b5": data[5], "rfid_valid_mask": data[5]})
        if data[3] == BOX_STATE_ERROR and fst is not None:
            out["error_code"] = fst
    return out


def parse_version_sn(msg):
    """Version/SN-Antwort parsen: ASCII "153"+SN (Func 0x14).

    Belegt (K2 13.10.): Voll-Frame f7 01 19 00 14 ..., Daten ASCII
      "153"+SN (19 Zeichen, "SN-DEMO-…", hier gekürzt) = CFS-App-Version + SN,
      CRC ok. LIVE 15.10.2026: vor dem Box-Update am 12.10.2026 "113"+SN
      (Host loggt "version: 1.1.3" bzw. "1.5.3").
    Hi-Request belegt: f7 01 03 ff 14 06 (STATUS 0xff, leer).
    Gibt (app_version, sn) zurück, z.B. ("153", "SN-DEMO-…").
    TODO: ob "153" je CFS-Typ variiert (cfs6_222?) – unbelegt.
    """
    data = _response_data(msg, FUNC_GET_VERSION_SN)
    text = bytes(data).decode("ascii", "replace")
    if len(text) < 3:
        raise ValueError("Versions-Antwort zu kurz: %r" % (text,))
    return text[:3], text[3:]


def parse_buffer_state(msg):
    """Buffer-State-Antwort parsen (Func 0x05, Request leer, 1 Byte).

    Belegt: f7 01 04 00 05 00 19 (Daten 0x00). Gibt int 0..255 zurück.
    TODO: Wertesemantik (Buffer voll/leer/Position) ist unbelegt –
      frühere CUT_STATE-Bezeichnung war falsch.
    """
    data = _response_data(msg, FUNC_GET_BUFFER_STATE)
    if len(data) != 1:
        raise ValueError("Buffer-Antwort muss 1 Byte haben: %r" % (bytes(data),))
    return data[0]


def parse_filament_sensor_state(msg):
    """GET_FILAMENT_SENSOR_STATE-Antwort parsen (Func 0x08, Request [pos], 1 Byte).

    LIVE BELEGT 15.10.2026 (Host-Name, klippy.log): [00] -> Bitmaske
    Sensor A (live 07 = A,B,C Filament, D leer – passt zu remain D 00),
    [01] -> Bitmaske Sensor B/Hub (= 0x0a Byte 4). Gibt int (Maske) zurück.
    """
    data = _response_data(msg, FUNC_GET_FILAMENT_SENSOR_STATE)
    if len(data) != 1:
        raise ValueError("Sensor-Antwort muss 1 Byte haben: %r" % (bytes(data),))
    return data[0]


def parse_hardware_status(msg):
    """ALT-NAME (Alias) für parse_filament_sensor_state (0x08, 1 Byte).

    ~~HARDWARE_STATUS 0x08~~ KORRIGIERT 15.10.2026: Host nennt 0x08
    GET_FILAMENT_SENSOR_STATE; GET_HARDWARE_STATUS ist 0x15
    (-> parse_get_hardware_status). Verhalten unverändert (0x08).
    """
    return parse_filament_sensor_state(msg)


# 0x15-Komponenten (CFS-FW, B+L): Init-Flags @0x2000390d, Namen aus
# FUN_080050fe; Byte i = ((i+1)<<4) | Flag[i], 0 = OK.
HW_COMPONENTS = (
    ("eeprom", "EEPROM"),             # 1 = fehlt, 2 = Verify @0x7f0 fehlgeschlagen
    ("aht", "AHT30 Temp/Feuchte"),    # 1 = nicht gefunden / 3x Lesefehler
    ("odometer", "Odometer MT6826"),  # 1 = Timer-Create-/Kommunikationsfehler
    ("buffer", "Buffer-Sensor"),      # 1 = PD8 UND PD9 aktiv (unplausibel)
    ("rfid0", "RFID-Leser 0"),        # 1 = Fehler (Leser-Erkennung FUN_080145be)
    ("rfid1", "RFID-Leser 1"),        # 1 = Fehler (auch bei erst später gelungenem Retry)
)


def parse_get_hardware_status(msg):
    """GET_HARDWARE_STATUS-Antwort parsen (Func 0x15, Request leer, 16 Byte).

    LIVE BELEGT 15.10.2026: f7 01 13 00 15 10 20 30 40 50 60 + 10 x 00 (ad).
    FW-Analyse FUN_0800f16e: 6 Einträge ((i+1)<<4)|X[i], Rest 0.
    ~~Bedeutung von X offen~~ BELEGT (CFS-FW): X = Init-Flags EEPROM,
    AHT30, Odometer MT6826, Buffer, RFID0, RFID1 (HW_COMPONENTS), 0 = OK.
    Gibt dict zurück: raw, entries [(index, x), ...] (index = oberes Nibble
    1..6, x = unteres Nibble; Null-Bytes entfallen – kompatibel),
    components [{key, name, code, ok}] für die 6 Positionen (code None,
    wenn das obere Nibble nicht zur Position passt), all_ok (bool).
    """
    data = bytes(_response_data(msg, FUNC_GET_HARDWARE_STATUS))
    if len(data) != 16:
        raise ValueError("Hardware-Status muss 16 Byte haben: %r" % (data,))
    comps = []
    for i, (key, name) in enumerate(HW_COMPONENTS):
        b = data[i]
        code = (b & 0x0F) if (b >> 4) == i + 1 else None
        comps.append({"key": key, "name": name, "code": code,
                      "ok": code == 0})
    return {"raw": data,
            "entries": [(b >> 4, b & 0x0F) for b in data if b],
            "components": comps,
            "all_ok": all(c["ok"] for c in comps) and not any(data[6:])}


# =====================================================================
# 2) BoxState – Tn-Datenmodell (in-memory, sinngemäß nach API-Doku).
# =====================================================================

class BoxState:
    """Tn/Tnn-Zustand der CFS-Box (reines Datenmodell, kein Bus).

    Modell-Annahme (eigene, dokumentiert – .so-Innenleben ist TODO):
    - Tn = Box-Ebene, Schlüssel (addr, part, num).
    - Tnn = Box+Slot-Ebene, Schlüssel "T{addr}{Slotbuchstabe}" (live: T1A,
      T1B, T1D; vgl. Extrude-Matrix der Protokollanalyse).
    - tn_map: {(addr, part, num): Tn-Dict}, tnn_map: {Tnn-Key: Content-Dict},
      save_data: {(addr, part, num): Save-Dict} (Save-Layer).
    Die Methoden heißen wie die .so-Fläche (generate/get/modify/update…);
    was das .so zusätzlich tut (Validierung, Events, Persistenz via BoxSave),
    ist TODO und hier bewusst nicht nachgebaut.
    """

    def __init__(self):
        self.state_init()

    # -- Lifecycle ----------------------------------------------------
    def state_init(self):
        """Zustand leeren (vgl. BoxState.state_init)."""
        self.tn_map = {}
        self.tnn_map = {}
        self.save_data = {}
        self.box_mode = None  # vgl. get_box_mode (TODO: Moduswerte)
        self.color_count = {}  # vgl. clear_color_count
        self.e_err = {}  # vgl. clear_e_err / e_err_set (TODO: Codes)

    # -- Tn-Ebene ------------------------------------------------------
    @staticmethod
    def _tn_key(addr, part, num):
        return (int(addr), int(part), int(num))

    def generate_Tn_data(self, addr, part, num, subpart=0, uppart=0, data=None):
        """Tn-Eintrag erzeugen (vgl. generate_Tn_data/inner_data).

        subpart/uppart stehen in der API-Signatur (modify_Tn_inner_data) –
        ihre .so-Semantik ist TODO; sie werden nur abgelegt.
        """
        return {"addr": int(addr), "part": int(part), "num": int(num),
                "subpart": int(subpart), "uppart": int(uppart),
                "data": dict(data) if data else {}}

    def get_Tn_data(self, addr, part, num):
        """Tn-Eintrag lesen (None wenn unbekannt)."""
        return self.tn_map.get(self._tn_key(addr, part, num))

    def modify_Tn_data(self, addr, part, num, **fields):
        """Tn-Eintrag anlegen/aktualisieren (vgl. modify_Tn_data)."""
        key = self._tn_key(addr, part, num)
        entry = self.tn_map.get(key)
        if entry is None:
            entry = self.generate_Tn_data(addr, part, num)
            self.tn_map[key] = entry
        entry.update(fields)
        return entry

    def get_Tn_inner_data(self, addr, part, num, subpart):
        """Tn-Innendaten lesen (vgl. get_Tn_inner_data; TODO-Semantik)."""
        entry = self.get_Tn_data(addr, part, num)
        if entry is None:
            return None
        return entry["data"].get(subpart)

    def modify_Tn_inner_data(self, addr, part, num, subpart, value, uppart=0):
        """Tn-Innendaten schreiben (vgl. modify_Tn_inner_data)."""
        key = self._tn_key(addr, part, num)
        entry = self.tn_map.get(key)
        if entry is None:
            entry = self.generate_Tn_data(addr, part, num, subpart=subpart,
                                          uppart=uppart)
            self.tn_map[key] = entry
        entry["data"][subpart] = value
        return entry

    def generate_Tn_map(self):
        """Kopie der Tn-Map (vgl. generate_Tn_map)."""
        return {k: {"addr": v["addr"], "part": v["part"], "num": v["num"],
                    "subpart": v["subpart"], "uppart": v["uppart"],
                    "data": dict(v["data"])}
                for k, v in self.tn_map.items()}

    # -- Tnn-Ebene ------------------------------------------------------
    @staticmethod
    def tnn_key(addr, slot):
        """Tnn-Schlüssel, z.B. (1, 0) -> 'T1A' (live-Format)."""
        if slot in range(4):
            letter = SLOT_KEYS[slot]
        elif isinstance(slot, str) and slot in SLOT_KEYS:
            letter = slot
        else:
            raise ValueError("Slot muss 0..3 oder A..D sein")
        return "T%d%s" % (int(addr), letter)

    def generate_Tnn_content(self, tnn, rfid=None, remain=None, extra=None):
        """Tnn-Content erzeugen (vgl. generate_Tnn_content; Felder TODO)."""
        content = {"tnn": tnn, "rfid": rfid, "remain": remain}
        if extra:
            content.update(extra)
        return content

    def get_Tnn_content(self, tnn):
        """Tnn-Content lesen (vgl. get_Tnn_content)."""
        return self.tnn_map.get(tnn)

    def update_Tnn_map(self, tnn, content):
        """Tnn-Content ablegen (vgl. update_Tnn_map/BoxAction.update_Tnn_map)."""
        self.tnn_map[tnn] = dict(content)
        return self.tnn_map[tnn]

    def generate_Tnn_map(self):
        """Kopie der Tnn-Map (vgl. generate_Tnn_map)."""
        return {k: dict(v) for k, v in self.tnn_map.items()}

    def get_Tnn_map(self):
        """Alias für generate_Tnn_map (vgl. get_Tnn_map)."""
        return self.generate_Tnn_map()

    # -- BoxMode / Save-Layer / Sync ------------------------------------
    def get_box_mode(self):
        """Box-Modus lesen (vgl. BoxState.get_box_mode; Werte TODO)."""
        return self.box_mode

    def get_tn_save_data(self, addr, part, num):
        """Save-Eintrag lesen (vgl. get_tn_save_data)."""
        return self.save_data.get(self._tn_key(addr, part, num))

    def modify_tn_save_data(self, addr, part, num, **fields):
        """Save-Eintrag schreiben (vgl. modify_tn_save_data)."""
        key = self._tn_key(addr, part, num)
        entry = self.save_data.setdefault(key, {})
        entry.update(fields)
        return entry

    def update_tn_save_data(self, addr, part, num):
        """Tn -> Save übernehmen (vgl. update_tn_save_data/sync_tn_data)."""
        entry = self.get_Tn_data(addr, part, num)
        if entry is None:
            return None
        return self.modify_tn_save_data(addr, part, num,
                                        subpart=entry["subpart"],
                                        uppart=entry["uppart"],
                                        data=dict(entry["data"]))

    def sync_tn_data(self):
        """Alle Tn-Einträge in den Save-Layer spiegeln (vgl. sync_tn_data)."""
        for (addr, part, num) in list(self.tn_map):
            self.update_tn_save_data(addr, part, num)
        return len(self.save_data)

    def update_same_material_list(self, material_list):
        """Gleich-Material-Liste ablegen (vgl. update_same_material_list).

        TODO: .so-Gruppierungslogik unbelegt – nur Ablage, keine Auswertung.
        """
        self.same_material_list = list(material_list)
        return self.same_material_list

    def clear_color_count(self):
        """Farbzähler löschen (vgl. clear_color_count)."""
        self.color_count = {}
        return self.color_count

    def clear_e_err(self):
        """Extrude-Fehlerflags löschen (vgl. clear_e_err; Codes TODO)."""
        self.e_err = {}
        return self.e_err

    def e_err_set(self, key, value=True):
        """Fehlerflag setzen (vgl. e_err_set; Codes TODO)."""
        self.e_err[key] = value
        return self.e_err


# =====================================================================
# 3) BoxAction – belegte Einzel-Step-Builder + bus-gebundene Klasse.
#    Gesamt-Reihenfolge/Steuerung (Sensor-/Messrad-gesteuert, Stufen 00/04/
#    05/06/07, Retrude START/FINISH): siehe cfsseq.py – hier NICHT dupliziert.
# =====================================================================

def build_preload(mask, action):
    """SET_PRE_LOADING / RFID-Control (0x0d): [Slotmaske][Aktion].

    Aktion 0x00=disable / 0x01=enable / 0x02=Force-Read (mit RFID) /
    0x03=Forced Preload ohne RFID (Belege s. PRELOAD_*-Konstanten, FW-Analyse
    FUN_0800f0da; Referenzvektor f70105ff0d0f0169 = Maske 0x0F, enable).
    Maske wirkt nur bei 02/03 (bei 00/01 von der FW ignoriert).
    STATUS 0xff. Identische Bytes wie cfsseq.build_preload.
    TODO: .so-Parameter force (communication_set_pre_loading force=False)
      – Wire-Abbild unbelegt; laut FW gibt es KEIN separates force-Byte,
      naheliegend ist force -> Aktion 0x02/0x03 (Host-Seite unbelegt).
    """
    mask = check_mask(mask)
    if action not in PRELOAD_ACTIONS:
        raise ValueError("Aktion muss 0x00..0x03 sein")
    return serial485.build_frame(BOX_ADDR, FUNC_SET_PRE_LOADING, [mask, action],
                                 status=STATUS_OPERATIONAL)


def build_preload_enable(mask=MASK_ALL):
    """Preload/Insert-Reading an (0x0d [Maske 01]) – Referenz-belegt."""
    return build_preload(mask, PRELOAD_ENABLE)


def build_preload_disable(mask=MASK_ALL):
    """Preload/Insert-Reading aus (0x0d [Maske 00]) – Referenz-belegt."""
    return build_preload(mask, PRELOAD_DISABLE)


def build_preload_force_read(mask=MASK_ALL):
    """RFID-Force-Read (0x0d [Maske 02]) – Referenz-belegt (vanilla);
    FW-Analyse 15.10.2026: Forced Preload MIT RFID (FUN_08004ec4(mask,1))."""
    return build_preload(mask, PRELOAD_FORCE_READ)


def build_preload_force_tension_only(mask=MASK_ALL):
    """Forced Preload OHNE RFID (0x0d [Maske 03]) – FW-belegt 15.10.2026
    (FUN_0800f0da -> FUN_08004ec4(mask, 0): nur pretension, kein RFID-Lesen);
    nicht live gesehen. Antwort erst nach dem Vorgang (preload_force_timeout)."""
    return build_preload(mask, PRELOAD_FORCE_TENSION_ONLY)


def build_feed_mode_enter():
    """Feed-Mode betreten (0x04 [00 01]) – K2-belegt (RS485-Mitschnitt).
    FW-Analyse 15.10.2026 (FUN_0800f014): mode 1 = Print verlassen -> STATE_IDLE."""
    return serial485.build_frame(BOX_ADDR, FUNC_SET_BOX_MODE, [0x00, 0x01],
                                 status=STATUS_OPERATIONAL)


def build_print_mode(mask):
    """Print-Mode (0x04 [Slotmaske 00]) – K2-belegt (RS485-Mitschnitt).
    FW-Analyse 15.10.2026: mode 0 = Slot -> STATE_PRINT (mask != 0 nötig)."""
    return serial485.build_frame(BOX_ADDR, FUNC_SET_BOX_MODE,
                                 [check_mask(mask), 0x00],
                                 status=STATUS_OPERATIONAL)


def build_preload_inhibit(inhibit):
    """Auto-Preload-Sperre (0x0f [01] sperren / [00] freigeben).

    Host-Name: TIGHTEN_UP_ENABLE (LIVE 15.10.2026: [01] vor dem Load, [00]
    nach 0x04 [m 00]); Bytes identisch, s. build_tighten_up_enable.

    BELEGT 15.10.2026 (FW-Analyse, inline im Dispatcher FUN_0800f27c): setzt
    S[8] (0x200037d8); S[8] != 0 sperrt das automatische Preload
    (FUN_080053b0). KEINE Motor-Aktion. Live-Flanke: Host sperrt vor dem
    Load ([01]) und gibt danach frei ([00]).
    """
    return serial485.build_frame(BOX_ADDR, FUNC_PRELOAD_INHIBIT,
                                 [0x01 if inhibit else 0x00],
                                 status=STATUS_OPERATIONAL)


def build_tighten_up_enable(enable):
    """Host-Name für 0x0f: TIGHTEN_UP_ENABLE [01]/[00] (= build_preload_inhibit;
    unsere FW-Semantik: [01] sperrt das Auto-Preload)."""
    return build_preload_inhibit(bool(enable))


def build_engage():
    """Alias (alter Name): 0x0f [01] = Auto-Preload SPERREN.

    ~~Motor-Engage~~ WIDERLEGT 15.10.2026 (FW-Analyse) – s. build_preload_inhibit.
    Bytes unverändert.
    """
    return build_preload_inhibit(True)


def build_release():
    """Alias (alter Name): 0x0f [00] = Auto-Preload FREIGEBEN.

    ~~Motor-Release~~ WIDERLEGT 15.10.2026 (FW-Analyse) – s. build_preload_inhibit.
    """
    return build_preload_inhibit(False)


def build_get_rfid(mask=0x0F):
    """GET_RFID-Request ([Maske], 0x02) – Live-Introspektion belegt
    (communication_get_rfid data=b'\\x01'/b'\\x0f', STATUS 0xff, Timeout 2 s)."""
    return serial485.build_frame(BOX_ADDR, FUNC_GET_RFID, [check_mask(mask)],
                                 status=STATUS_OPERATIONAL)


def build_get_remain(mask=0x0F):
    """GET_REMAIN_LEN-Request ([Maske], 0x03) – wie build_get_rfid belegt."""
    return serial485.build_frame(BOX_ADDR, FUNC_GET_REMAIN_LEN, [check_mask(mask)],
                                 status=STATUS_OPERATIONAL)


def build_get_wheel():
    """MEASURING_WHEEL-Request ([01], 0x0e) – K2-belegt."""
    return serial485.build_frame(BOX_ADDR, FUNC_MEASURING_WHEEL, [0x01],
                                 status=STATUS_OPERATIONAL)


def build_get_box_state():
    """BOX_STATE-Poll (0x0a, leer) – Herzschlag alle ~4 s, K2-belegt.

    ~~TODO: Poll-Payload des Hosts~~ LIVE BELEGT 15.10.2026: Host sendet
    genau diese leere Form (10 560x, retries=0, Abstand ~4 s); Antwort
    6 Byte (R153) bzw. 4 Byte (FW 113) -> parse_box_state.
    """
    return serial485.build_frame(BOX_ADDR, FUNC_GET_BOX_STATE, [],
                                 status=STATUS_OPERATIONAL)


def build_get_buffer():
    """BUFFER_STATE-Request (0x05, leer) – K2-belegt."""
    return serial485.build_frame(BOX_ADDR, FUNC_GET_BUFFER_STATE, [],
                                 status=STATUS_OPERATIONAL)


def build_get_filament_sensor_state(pos=0x00):
    """GET_FILAMENT_SENSOR_STATE-Request (0x08 [pos]) – live belegt
    15.10.2026: pos 0x00 = Sensor A, 0x01 = Sensor B/Hub; weitere pos TODO."""
    if not 0 <= pos <= 0xFF:
        raise ValueError("pos muss 0x00..0xff sein")
    return serial485.build_frame(BOX_ADDR, FUNC_GET_FILAMENT_SENSOR_STATE,
                                 [pos], status=STATUS_OPERATIONAL)


def build_get_hardware(channel=0x00):
    """ALT-NAME (Alias): 0x08 [Kanal] = build_get_filament_sensor_state.
    ~~HARDWARE_STATUS~~ – Host-Name von 0x08 ist GET_FILAMENT_SENSOR_STATE."""
    return build_get_filament_sensor_state(channel)


def build_get_hardware_status():
    """GET_HARDWARE_STATUS-Request (0x15, leer, STATUS 0xff) – live belegt
    15.10.2026 (10 Requests, Antwort 16 Byte, s. parse_get_hardware_status)."""
    return serial485.build_frame(BOX_ADDR, FUNC_GET_HARDWARE_STATUS, [],
                                 status=STATUS_OPERATIONAL)


def build_get_version():
    """VERSION_SN-Request (0x14, leer, STATUS 0xff) – Hi-belegt
    (f7 01 03 ff 14 06); K2-Antwort s. parse_version_sn."""
    return serial485.build_frame(BOX_ADDR, FUNC_GET_VERSION_SN, [],
                                 status=STATUS_OPERATIONAL)


# Status-Bytes, die bei 0x0a REGULÄR Ereignisse/Zustände tragen (kein Fehler
# des Requests): 0x30 UPDATE_STATE, 0x50 PRINT-Sensor, in STATE_ERROR der
# gespeicherte Fehlercode S[3] (live 05/0c/14/19/51). Nur dataless 0x01/0x02
# (ungültig / CRC-Fehler im Request, Antwort ohne Daten)
# sind dort echte Transportfehler.
_REQUEST_ERROR_STATUSES = (STATUS_INVALID, STATUS_CRC)

# 0x0e-Ops (FW-Analyse FUN_0800efc6): 0 = Odometer NULLEN, 1 = lesen (4 B Float)
WHEEL_OP_RESET = 0x00
WHEEL_OP_READ = 0x01

# 0x11 p (FW-Analyse FUN_08004d90): nur 0/1 gültig, sonst Status 1
RETRUDE_PARAMS = (RETRUDE_P_TO_BUFFER, RETRUDE_P_PULL_HUB)


class BoxAction:
    """Box-Abläufe gegen den Bus (vgl. BoxAction.communication_*).

    STATUSPRÜFUNG (Review M2, 16.10.2026): Bietet der Bus transact_full
    (serial485.Serial485), wird das Status-Byte der Antwort ausgewertet:
    Status != 0 -> CfsStatusError(status, name, data) – AUSSER bei 0x0a
    (communication_get_box_state), dessen Status regulär Ereignisse trägt
    (0x30/0x50/gespeicherter Fehler): dort wird NICHT geworfen, der Status
    kommt als .status der Rückgabe (StatusBytes) zurück (nur dataless
    0x01/0x02 = Request ungültig/CRC werfen auch dort).
    raise_on_status=False (Konstruktor, bei extrude/retrude auch je Aufruf)
    schaltet das Werfen ab; dann trägt die Rückgabe den Status als .status.
    Alle communication_* geben StatusBytes zurück (bytes-Unterklasse ->
    API-kompatibel zu den bisherigen Rohdaten). Busse ohne transact_full
    (Fremd-Adapter): Status unbekannt (.status None), keine Prüfung.
    """

    def __init__(self, bus, addr=BOX_ADDR, raise_on_status=True):
        self.bus = bus
        self.addr = addr
        self.raise_on_status = raise_on_status

    def _call(self, fun, data, timeout=TIMEOUT_OPERATIONAL, retries=None,
              raise_on_status=None, status_is_report=False):
        """Ein Request an die Box, Antwort als StatusBytes (s. Klasse)."""
        retries = func_retries(fun) if retries is None else retries
        raise_on_status = (self.raise_on_status if raise_on_status is None
                           else raise_on_status)
        kw = dict(status=STATUS_OPERATIONAL, timeout=timeout, retries=retries)
        full_fn = getattr(self.bus, "transact_full", None)
        if full_fn is None:
            return StatusBytes(self.bus.transact(self.addr, fun, data, **kw),
                               None, fun)
        _raddr, rstatus, rfun, rdata, _raw = full_fn(self.addr, fun, data, **kw)
        out = StatusBytes(rdata, rstatus, rfun)
        if raise_on_status and rstatus != STATUS_OK:
            if (not status_is_report
                    or (rstatus in _REQUEST_ERROR_STATUSES and not rdata)):
                raise CfsStatusError(rstatus, status_name(rstatus), rdata, rfun)
        return out

    # -- Abfragen (alle Timeout 2 s, STATUS 0xff; vgl. box_wrapper-API) --
    def communication_get_rfid(self, num=0x0F):
        """RFID lesen (num = Slotmaske 0x00..0x0f wie im Original, z.B.
        1 = Slot A, 15 = alle; KEINE Slotnummer). Gibt Rohdaten zurück;
        parsen per get_rfid()."""
        return self._call(FUNC_GET_RFID, [check_mask(num)])

    def communication_get_remain_len(self, num=0x0F):
        """Restmengen lesen (num = Slotmaske 0x00..0x0f, Default alle)."""
        return self._call(FUNC_GET_REMAIN_LEN, [check_mask(num)])

    def communication_measuring_wheel(self, action=WHEEL_OP_READ, reset=False):
        """Messrad (0x0e [op]).

        op 1 (Default) = lesen -> Float (mm, negativ, s. get_measuring_wheel).
        op 0 = Odometer NULLEN (FW-Analyse FUN_0800efc6, Review G1 16.10.2026) –
          verändert Box-Zustand, live nie vom Host gesendet (0x0e live nur
          [01]); daher NUR mit reset=True erlaubt, Rückgabe dann die
          (unbelegte) Antwort als StatusBytes statt Float.
        Andere ops -> ValueError.
        """
        if isinstance(action, bool) or action not in (WHEEL_OP_RESET,
                                                      WHEEL_OP_READ):
            raise ValueError("Messrad-op muss 0x01 (lesen) oder 0x00 "
                             "(nullen, nur mit reset=True) sein: %r"
                             % (action,))
        if action == WHEEL_OP_RESET:
            if reset is not True:
                raise ValueError("0x0e op 0 nullt das Odometer – nur mit "
                                 "reset=True erlaubt")
            return self._call(FUNC_MEASURING_WHEEL, [WHEEL_OP_RESET])
        raw = self._call(FUNC_MEASURING_WHEEL, [WHEEL_OP_READ])
        return get_measuring_wheel(bytes([action]), raw)

    def communication_get_box_state(self):
        """Box-State lesen (0x0a-Poll, Antwort via get_box_status/
        parse_box_state). Wie im Original OHNE Retransmit (retries=0, live
        10 560 Polls alle ~4 s; ein verpasster Poll wird nicht wiederholt).
        Status wirft NICHT (Ereignis-/Fehlerbericht), steht in .status;
        parse_box_state(rueckgabe) nutzt ihn als frame_status."""
        return self._call(FUNC_GET_BOX_STATE, [], status_is_report=True)

    def communication_get_buffer_state(self):
        """Buffer-State lesen (0x05, 1 Byte)."""
        return self._call(FUNC_GET_BUFFER_STATE, [])

    def communication_get_filament_sensor_state(self, pos=0x00):
        """Filament-Sensor lesen (0x08 [pos], 1 Byte Bitmaske; .so-Name
        communication_get_filament_sensor_state(addr, pos)). pos 0 = Sensor A,
        1 = Sensor B/Hub (live belegt)."""
        if isinstance(pos, bool) or not isinstance(pos, int) \
                or not 0 <= pos <= 0xFF:
            raise ValueError("pos muss 0x00..0xff sein: %r" % (pos,))
        return self._call(FUNC_GET_FILAMENT_SENSOR_STATE, [pos])

    def communication_get_hardware_status(self, channel=None):
        """Hardware-Status lesen – .so-Name, KORRIGIERT 15.10.2026: Func 0x15
        (leer, 16 Byte, s. parse_get_hardware_status).

        VERALTET (Review G6, 16.10.2026): mit channel (alte Signatur) wird
        wie früher 0x08 [channel] gesendet – das ist ein ANDERES Kommando.
        Dafür DeprecationWarning; stattdessen
        communication_get_filament_sensor_state(channel) verwenden.
        """
        if channel is not None:
            warnings.warn(
                "communication_get_hardware_status(channel) sendet 0x08 – "
                "veraltet, communication_get_filament_sensor_state(channel) "
                "verwenden", DeprecationWarning, stacklevel=2)
            return self.communication_get_filament_sensor_state(channel)
        return self._call(FUNC_GET_HARDWARE_STATUS, [])

    def communication_get_version_sn(self):
        """Version/SN lesen (0x14, ASCII "153"+SN)."""
        return self._call(FUNC_GET_VERSION_SN, [])

    # -- Aktionen (belegte Einzel-Steps; Antwort = Box-ACK, kein Parsen) --
    def communication_set_pre_loading(self, mask, action, timeout=None):
        """Preload setzen (0x0d [Maske][Aktion 00 aus/01 an/02 Force-Read/
        03 Force ohne RFID]).

        mask = Slotmaske 0x00..0x0f, action = PRELOAD_DISABLE/ENABLE/
        FORCE_READ/FORCE_TENSION_ONLY (Belege s. Konstanten).
        ~~TODO: .so-Parameter action/num/force-Abbild~~ GEKLÄRT 17.10.2026
        (Analyse box_wrapper): Stock-Signatur
        (addr, action, num=15, force=False); num = Slotmaske 0..15
        (G-Code NUM, get_int minval 0/maxval 15, Default 0), geht 1:1 als
        1. Datenbyte auf den Draht (Py 5297/5325, 5076) – entspricht
        hier mask (Wire-identisch). OPEN/CLOSE (op 1/0) sendet der Stock
        an ALLE verbundenen Boxen (ADDR ignoriert); RUN/TIGHT (op 2/3)
        nur an ADDR. Maske 0 ist ein Fehlgebrauch (NUM als Slotindex
        „A“ gemeint, UI/Makro BOX_INFO_REFRESH) und Ursache der
        [00 02]-Bursts; der Stock-Erstpreload sendet nie
        Maske 0. force ist hier nicht abgebildet.
        ~~TODO bei echtem Preload ggf. länger wählen~~ ERLEDIGT 15.10.2026
        (FW-Analyse FUN_0800f0da/FUN_08004ec4/FUN_08004e80): bei op 2/3 kommt die
        Antwort erst NACH dem Vorgang (~3 min je Slot, + Vorlauf). Daher
        Default-Timeout preload_force_timeout(mask) und retries=0 – ein
        2-s-Timeout mit Retries würde den Force-Preload mehrfach auslösen.
        op 0/1 antworten sofort: operational 2 s, Retries wie Original (4).
        LIVE 15.10.2026: op 2 [01 02] antwortet nach 31-37 s bei EINER
        Sendung; das Original sendet mit retries=4 und Timeout 300 s
        (SET_PRE_LOADING). BEWUSSTE ABWEICHUNG hier: retries=0 + Timeout
        nach FW-Laufzeit (>=380 s), weil ein Retransmit nach 300 s den
        Force-Preload erneut auslösen würde (Mehrslot-Masken > 300 s
        möglich). Die Host-Bursts [00 02] (5 Sendungen in ~20 ms, nie
        beantwortet) werden NICHT nachgebildet. Maske 0 bei op 2/3: 1200 s
        (wie 4 Slots, s. preload_force_timeout; bis 16.10.: 980 s). Timeout
        seit 17.10.2026 max(180+200·n, 300·n). Der Bus-Lock wird so lange
        gehalten (serial485 MAX_BUS_HOLD_S, Review M6).
        timeout = expliziter Override (s).
        """
        if action not in PRELOAD_ACTIONS:
            raise ValueError("Aktion muss 0x00..0x03 sein")
        mask = check_mask(mask)
        if action in PRELOAD_FORCE_ACTIONS:
            return self._call(
                FUNC_SET_PRE_LOADING, [mask, action],
                timeout=(preload_force_timeout(mask) if timeout is None
                         else timeout),
                retries=0)
        return self._call(FUNC_SET_PRE_LOADING, [mask, action],
                          timeout=(TIMEOUT_OPERATIONAL if timeout is None
                                   else timeout))

    def communication_extrude(self, mask, mode, p=0x00, timeout=None,
                              raise_on_status=None):
        """Extrude-/Feed-Prozess (0x10 [mask][mode][p]) – geschützter Pfad
        (Review M3, 16.10.2026).

        mask = 1-hot-Slotmaske (FW-Analyse: Maske 0 ungültig, eb88 strict;
          Mehrfachmasken unbelegt -> ValueError, wie cfsseq).
        mode = EXTRUDE_MODE_* 0..7 (FW-Analyse FUN_080045c6; > 7 = keine Antwort
          -> ValueError). mode 3 (EXTRUDE_MODE_CLEAR_ERROR) LÖSCHT den
          Box-Fehler (STATE_ERROR -> IDLE, Fehlercode 0, FUN_08003e2c); der
          Host nutzt live stattdessen 0x04 [00 01]. mode 1/2 fördern bis
          Stillstand (live nie gesendet, Laufzeit unbelegt -> ggf. timeout
          übergeben). mode 4 antwortet sofort, die Box fördert danach selbst
          und beantwortet mode 5 (Odometer-Abfrage, Status = Fehler aus mode
          4, z.B. 0x0c) und mode 6.
        p = int 0..255 (nur mode 7 nutzt ihn: Extruder-Check p mm, live 3).
        retries=0 (FUNC_RETRIES, bewusste Abweichung vom Original 4: ein
        Retransmit würde die Mechanik erneut auslösen); timeout Default
        FUNC_TIMEOUTS_SO[0x10] = 15 s (live mode 0 <= 7,25 s).
        Status != 0 -> CfsStatusError (data = Antwortdaten, z.B. Odometer bei
        mode 5 / Box-Zustand bei mode 0), außer raise_on_status=False.
        """
        mask = check_one_hot(mask)
        if isinstance(mode, bool) or mode not in EXTRUDE_MODES:
            raise ValueError("0x10-mode muss 0..7 sein (EXTRUDE_MODE_*): %r"
                             % (mode,))
        if isinstance(p, bool) or not isinstance(p, int) or not 0 <= p <= 0xFF:
            raise ValueError("0x10-p muss int 0..255 sein: %r" % (p,))
        return self._call(FUNC_EXTRUDE, [mask, mode, p],
                          timeout=(FUNC_TIMEOUTS_SO[FUNC_EXTRUDE]
                                   if timeout is None else timeout),
                          raise_on_status=raise_on_status)

    def communication_retrude(self, mask, p, timeout=None,
                              raise_on_status=None):
        """Retrude (0x11 [mask][p]) – geschützter Pfad (Review M3).

        mask = 1-hot-Slotmaske oder 0 (FW-Analyse FUN_08004d90: 0 = alle Slots
          mit aktivem Sensor B).
        p = RETRUDE_P_TO_BUFFER (0, bis Buffer-Sensor, live „START“) oder
          RETRUDE_P_PULL_HUB (1, pull_back_hub, live „FINISH“); andere p
          beantwortet die FW mit Status 1 -> hier ValueError.
        retries=0 (bewusste Abweichung vom Original 4, s. FUNC_RETRIES);
        timeout Default FUNC_TIMEOUTS_SO[0x11] = 150 s (live p1 <= 23 s).
        Status != 0 (z.B. 0x13/0x14/0x16/0x17/0x19, Busy 0x03 + Zustand) ->
        CfsStatusError, außer raise_on_status=False.
        """
        if mask != 0:
            mask = check_one_hot(mask)
        elif isinstance(mask, bool):
            raise ValueError("Maske muss int sein: %r" % (mask,))
        if isinstance(p, bool) or p not in RETRUDE_PARAMS:
            raise ValueError("0x11-p muss 0 oder 1 sein: %r" % (p,))
        return self._call(FUNC_RETRUDE, [mask, p],
                          timeout=(FUNC_TIMEOUTS_SO[FUNC_RETRUDE]
                                   if timeout is None else timeout),
                          raise_on_status=raise_on_status)

    def communication_set_box_mode_feed(self):
        """Feed-Mode betreten (0x04 [00 01]) – live auch zum Fehlerlöschen
        aus STATE_ERROR genutzt (Host nutzt 0x10 mode 3 nie)."""
        return self._call(FUNC_SET_BOX_MODE, [0x00, 0x01])

    def communication_set_box_mode_print(self, mask):
        """Print-Mode (0x04 [Maske 00])."""
        return self._call(FUNC_SET_BOX_MODE, [check_mask(mask), 0x00])

    def communication_set_preload_inhibit(self, inhibit):
        """Auto-Preload-Sperre setzen (0x0f [01] sperren / [00] frei).

        BELEGT 15.10.2026 (FW-Analyse, s. build_preload_inhibit): FW akzeptiert
        nur 0/1 (sonst Status 1), Antwort sofort ohne Daten.
        """
        if isinstance(inhibit, bool):
            inhibit = int(inhibit)
        if inhibit not in (0x00, 0x01):
            raise ValueError("inhibit muss 0/1 bzw. False/True sein")
        return self._call(FUNC_PRELOAD_INHIBIT, [inhibit])

    def communication_tighten_up_enable(self, enable):
        """Host-/.so-Name für 0x0f (TIGHTEN_UP_ENABLE) – identisch mit
        communication_set_preload_inhibit (Semantik laut FW-Analyse)."""
        return self.communication_set_preload_inhibit(enable)

    def communication_ctrl_connection_motor_action(self, act):
        """.so-Name CTRL_CONNECTION_MOTOR_ACTION = **0x07 [act]** (Hauptmotor:
        0 Stopp, 1 vor, 2 zurück; FW-Analyse).
        KORREKTUR 19.10.2026 (live, Test externe Spule): Stock-Log
        zeigt `data_send 0x1 0x4 0xff 0x7 0x0` → `cmd: CTRL_CONNECTION_MOTOR_ACTION`
        (vor dem Slot-Rückzug 0x11). Die frühere Zuordnung zu 0x0f war falsch
        (0x0f = TIGHTEN_UP_ENABLE, live belegt). Erlaubt ist nur der live
        belegte Stopp 0x00 – 1/2 bewegen Filament und bleiben gesperrt."""
        if act != 0x00:
            raise ValueError("act: nur 0x00 (Stopp) ist live belegt und erlaubt")
        return self._call(FUNC_CONNECTION_MOTOR, [act])

    # -- TODO (unbelegt, bewusst NICHT implementiert) ---------------------
    # communication_extrude_process / retrude_process als ABLAUF (Stufen-
    #   Steuerung sensor-/messrad-gesteuert): siehe cfsseq.py; die
    #   Einzel-Requests 0x10/0x11 gibt es oben (communication_extrude/
    #   communication_retrude, seit 16.10.2026).
    # DryRun/Dryer (get/set_dry_mode, pause/continue/auto_dry),
    # create_connect, set_motor_speed, Test-Loop (Tighten-Up = 0x0f und
    # Filament-Sensor = 0x08 sind seit 15.10.2026 oben umgesetzt):
    #   Wire-Abbild unbelegt -> hier kein erfundenes Byte.


# --- Selbsttest (keine Hardware, keine Netzwerkzugriffe) ---
class _FakeBus:
    """Spielt Antwort-Daten ab, protokolliert Requests (wie boxmini._FakeBus)."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def transact(self, addr, fun, data=(), **kwargs):
        self.calls.append((addr, fun, list(data), dict(kwargs)))
        fun_exp, rdata = serial485.parse_frame(self.answers.pop(0))
        assert fun_exp == fun, (fun_exp, fun)
        return rdata


class _FakeBusFull(_FakeBus):
    """Wie _FakeBus, zusätzlich transact_full (Status-Byte, wie Serial485)."""

    def transact_full(self, addr, fun, data=(), **kwargs):
        self.calls.append((addr, fun, list(data), dict(kwargs)))
        raw = self.answers.pop(0)
        fun_exp, rdata = serial485.parse_frame(raw)
        assert fun_exp == fun, (fun_exp, fun)
        return raw[1], raw[3], fun_exp, rdata, raw


def selftest():
    fails = 0

    def check(name, cond):
        nonlocal fails
        print(("ok " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    F = bytes.fromhex
    # -- 1. RFID: echte Live-Frames (12.10.2026) --
    check("rfid B:unknown",
          get_rfid(F("f7 01 0d 00 02 42 3a 75 6e 6b 6e 6f 77 6e 3b 8e"))
          == {"B": "unknown"})
    check("rfid C:busy",
          get_rfid(F("f7 01 0a 00 02 43 3a 62 75 73 79 3b 54"))
          == {"C": "busy"})
    check("rfid roh+multi",
          get_rfid(b"A:10125;B:unknown;") == {"A": "10125", "B": "unknown"})
    check("rfid leer", get_rfid(b"") == {})
    # Live-Vendor (RFID-Frame 12.10.2026) feldweise:
    # DATENSCHUTZ 15.10.2026: Platzhalter statt echtem Tag-String – Layout
    # und mat_id/col/len wie live, Datum/Supplier/Batch/Nummer erfunden.
    live_vendor = "1012400009A1010010FFFFFF0165000000000000"
    vf = split_rfid_vendor(live_vendor)
    for name, exp in (("month", "1"), ("day", "01"), ("year", "24"),
                      ("supplier", "0000"), ("batch", "9A"),
                      ("mat_id", "101001"), ("col", "0FFFFFF"),
                      ("len", "0165"), ("number", "000000"),
                      ("reserve", "000000")):
        check("vendor %s=%s" % (name, exp), vf.get(name) == exp)
    check("vendor bytes == str",
          split_rfid_vendor(live_vendor.encode("ascii")) == vf)
    check("vendor felder lückenlos",
          "".join(vf[n] for n, _ in RFID_VENDOR_FIELDS) == live_vendor)
    for bad_v in (live_vendor[:-1], live_vendor + "0", ""):
        try:
            split_rfid_vendor(bad_v)
            check("vendor laengen-guard %d" % len(bad_v), False)
        except ValueError:
            check("vendor laengen-guard %d" % len(bad_v), True)
    try:
        get_rfid(F("f7 01 03 00 03 b3"))  # falscher fun -> muss scheitern
        check("rfid fun-guard", False)
    except ValueError:
        check("rfid fun-guard", True)

    # -- 2. Remain: echte Live-Frames --
    check("remain A=28",
          get_remain_len(b"\x0f", F("f7 01 07 00 03 1c ff 00 00 11"))
          == {"A": 28, "B": None, "C": 0, "D": 0})
    check("remain leer/none",
          get_remain_len(b"\x0f", F("f7 01 07 00 03 ff ff ff 00 98"))
          == {"A": None, "B": None, "C": None, "D": 0})
    check("remain roh", get_remain_len(b"\x01", bytes.fromhex("1a 00 ff 00"))
          == {"A": 26, "B": 0, "C": None, "D": 0})
    for bad_len in (b"\x1c", b"", bytes(5)):
        try:
            get_remain_len(b"\x0f", serial485.build_frame(
                BOX_ADDR, FUNC_GET_REMAIN_LEN, bad_len, status=STATUS_REPLY))
            check("remain laengen-guard %dB" % len(bad_len), False)
        except ValueError:
            check("remain laengen-guard %dB" % len(bad_len), True)

    # -- 3. Messrad: echte Live-Frames (KORREKTUR 13.10.: BE-Float negativ) --
    w1 = get_measuring_wheel(b"\x01", F("f7 01 07 00 0e c4 e6 fe 0b f3"))
    check("wheel -1847,94", abs(w1 - (-1847.94)) < 0.02)
    w2 = get_measuring_wheel(b"\x01", F("f7 01 07 00 0e c5 00 91 49 a9"))
    check("wheel negativ", w2 < 0)
    check("wheel c52a4e54",
          abs(get_measuring_wheel(b"\x01", bytes.fromhex("c5 2a 4e 54"))
              - (-2724.89)) < 0.02)
    try:
        get_measuring_wheel(b"\x01", bytes.fromhex("c4 e6 fe"))  # 3 B
        check("wheel laengen-guard", False)
    except ValueError:
        check("wheel laengen-guard", True)
    # synthetisch: struct-Roundtrip über echten Frame-Bau
    synth = struct.pack(">f", -123.456)
    check("wheel synthetisch",
          get_measuring_wheel(b"\x01", serial485.build_frame(
              BOX_ADDR, FUNC_MEASURING_WHEEL, synth,
              status=STATUS_REPLY)) == struct.unpack(">f", synth)[0])

    # -- 4. Box-State: echte Frames (6 B + 4 B/0x30-Variante) --
    st = get_box_status(F("f7 01 09 00 0a 13 34 00 00 00 00 d7"))
    check("boxstate 6B", st["len"] == 6 and st["b0"] == 0x13
          and st["b1"] == 0x34 and st["frame_status"] == 0x00)
    st30 = get_box_status(F("f7 01 07 30 0a 03 00 00 00 51"))
    check("boxstate 0x30/4B", st30["len"] == 4 and st30["frame_status"] == 0x30
          and st30["raw"] == bytes.fromhex("03 00 00 00"))
    # Rohdaten mit erstem Byte 0xF7, aber unpassendem Längenfeld -> Rohdaten
    st_raw = get_box_status(bytes([0xF7, 0x34, 0, 0, 0, 0]))
    check("boxstate roh mit 0xF7", st_raw["frame_status"] is None
          and st_raw["len"] == 6 and st_raw["b0"] == 0xF7)

    # 0x0a benannt (FW-Analyse 15.10.2026) – Live-Frames passen zum Layout
    nb = parse_box_state(F("f7 01 09 00 0a 13 34 00 00 00 00 d7"))
    check("parse_box_state live IDLE", nb["kind"] == "state"
          and nb["state"] == BOX_STATE_IDLE and nb["state_name"] == "STATE_IDLE"
          and nb["s1"] == 0x13 and nb["s0"] == 0x34 and nb["flags"] == 0
          and nb["sensor_b_mask"] == 0 and nb["rfid_valid_mask"] == 0
          and nb["status_name"] == "ok")
    ne = parse_box_state(F("f7 01 07 30 0a 03 00 00 00 51"))
    check("parse_box_state live 0x30-Ereignis", ne["kind"] == "event"
          and ne["events"] == {"A": 3, "B": 0, "C": 0, "D": 0}
          and "state" not in ne)
    nerr = parse_box_state(serial485.build_frame(
        BOX_ADDR, FUNC_GET_BOX_STATE, [0x13, 0x34, 0, BOX_STATE_ERROR, 0, 0],
        status=STATUS_NO_FILAMENT))
    check("parse_box_state ERROR -> Fehlercode", nerr["state_name"] == "STATE_ERROR"
          and nerr["error_code"] == 0x16)
    check("parse_box_state unbekannt", parse_box_state(b"\x01\x02")["kind"]
          == "unknown")
    check("box-state-enum 0..5", [BOX_STATE_NAMES[i] for i in range(6)] ==
          ["STATE_IDLE", "STATE_PRELOAD", "STATE_PRINT", "STATE_RELOAD",
           "STATE_ERROR", "STATE_TEST"])

    # -- 4b. LIVE-GEGENPROBE 15.10.2026: echte 0x0a-Frames aus klippy.log*
    #    (keine SN/UUID darin; CRC wird von parse_frame geprüft) --
    li = parse_box_state(F("f7 01 09 00 0a 16 2e 00 00 00 00 b5"))  # 13.10. IDLE
    check("live 0x0a R153 IDLE, b5=0, Temp 22 °C / 46 % rF (CFS-FW)",
          li["kind"] == "state" and li["state"] == BOX_STATE_IDLE
          and li["b5"] == 0 and li["rfid_valid_mask"] == 0
          and li["inlet_tag_mask"] == 0
          and li["temp_c"] == 22 and li["rh"] == 46
          and li["temp_c_guess"] == 22 and li["rh_guess"] == 46
          and not li["block_long"] and li["block_feeder_mask"] == 0)
    lneg = parse_box_state(serial485.build_frame(
        BOX_ADDR, FUNC_GET_BOX_STATE, [0xFD, 0x50, 0, 0, 0, 0]))
    check("0x0a Temperatur int8: 0xfd -> -3 °C", lneg["temp_c"] == -3
          and lneg["rh"] == 80 and lneg["s1"] == 0xFD)
    lsp = parse_box_state(F("f7 01 09 51 0a 11 35 00 04 01 00 a1"))  # 12.10. key846
    check("live 0x0a SPEED_ERR 0x51 in ERROR, Hub=A",
          lsp["state_name"] == "STATE_ERROR" and lsp["error_code"] == 0x51
          and lsp["status_name"].startswith("SPEED_ERR")
          and lsp["hub_mask"] == 0x01)
    lmb = parse_box_state(F("f7 01 09 19 0a 16 2f 91 04 01 00 95"))  # 13.10. key865
    check("live 0x0a motor block 0x19, flags 0x91",
          lmb["error_code"] == STATUS_PULL_HUB_BLOCK and lmb["flags"] == 0x91)
    check("0x0a flags 0x91 = Block >10 s, Hauptmotor + Feeder A (CFS-FW)",
          lmb["block_long"] and lmb["block_main_motor"]
          and lmb["block_feeder_mask"] == 0x01)
    check("STATUS 0x50/0x51/0x52/0x1a benannt, 0x53 nicht",
          STATUS_RUNOUT == 0x50 and STATUS_BUFFER_SLOW == 0x51
          and STATUS_NO_FEED == 0x52 and STATUS_PULL_HUB_ODO_TIMEOUT == 0x1A
          and all(c in STATUS_NAMES for c in (0x50, 0x51, 0x52, 0x1A))
          and 0x53 not in STATUS_NAMES
          and status_name(0x53) == "unbekannt 0x53")
    l113 = parse_box_state(F("f7 01 07 00 0a 17 34 00 00 81"))  # 05.10. FW 113
    check("live 0x0a FW113 4 Byte -> state_v113, KEIN Ereignis",
          l113["kind"] == "state_v113" and "events" not in l113
          and l113["state"] == BOX_STATE_IDLE and l113["s1"] == 0x17)
    # 18.10.2026 (K3): FW-113-Byte 2 = Block-Flags (V, Hi-Mitschnitt)
    check("FW113 b2 0x00 -> keine Block-Flags, Basis V",
          not l113["block_long"] and l113["block_feeder_mask"] == 0
          and l113["flags_basis"] == "V" and li["flags_basis"] == "B")
    f113 = parse_box_state(bytes.fromhex("17 2a 02 04"), box_fw="113")
    check("FW113 b2 0x02 (Stillstand B, TC @27710) -> Feeder B, ERROR",
          f113["kind"] == "state_v113" and f113["block_feeder_mask"] == 0x02
          and not f113["block_main_motor"]
          and f113["state"] == BOX_STATE_ERROR)
    check("FW113 b2 0x08 (Insert D, TC @23393) -> Feeder D",
          parse_box_state(bytes.fromhex("17 2a 08 00"),
                          box_fw=113)["block_feeder_mask"] == 0x08)
    # K4: remain FW-abhängig; K5: key-Tabelle + belegte Zuordnung
    check("remain ohne RFID: FW113 0x64, R153 0xff",
          REMAIN_NO_RFID_BY_FW == {113: 0x64, 153: 0xFF}
          and get_remain_len(None, bytes([0x64, 0, 0, 0]))["A"] == 100)
    check("key-Tabelle 831-865 (ohne 842/854), Status->key nur belegt",
          len(HOST_ERROR_KEYS) == 31 and "key842" not in HOST_ERROR_KEYS
          and "key854" not in HOST_ERROR_KEYS
          and host_error_text(0x0C)[0] == "key836"
          and host_error_text(0x51) == ("key846", HOST_ERROR_KEYS["key846"])
          and host_error_text(0x09) == (None, None)
          and all(k in HOST_ERROR_KEYS for k in STATUS_HOST_KEY.values()))
    check("FW113-Frame mit box_fw=153 -> unknown",
          parse_box_state(F("f7 01 07 00 0a 17 34 00 00 81"),
                          box_fw="153")["kind"] == "unknown")
    raw4 = bytes.fromhex("17 34 00 02")
    check("4-Byte-Rohdaten ohne FW -> ambiguous (nicht event)",
          parse_box_state(raw4)["kind"] == "ambiguous")
    r113 = parse_box_state(raw4, box_fw="113")
    check("4-Byte-Rohdaten FW113 -> PRINT",
          r113["kind"] == "state_v113" and r113["state"] == BOX_STATE_PRINT)
    check("4-Byte-Rohdaten FW153 -> event",
          parse_box_state(bytes.fromhex("00 02 00 00"), box_fw=153)["events"]
          == {"A": 0, "B": EVENT_REMOVE, "C": 0, "D": 0})
    for hx, slot, ev in (("f7 01 07 30 0a 00 02 00 00 bd", "B", EVENT_REMOVE),
                         ("f7 01 07 30 0a 01 00 00 00 7d", "A", EVENT_INSERT),
                         ("f7 01 07 30 0a 00 00 00 03 62", "D",
                          EVENT_PRELOAD_DONE)):
        le = parse_box_state(F(hx))
        check("live UPDATE_STATE %s=%d" % (slot, ev), le["kind"] == "event"
              and le["events"][slot] == ev
              and le["event_names"][slot] == EVENT_NAMES[ev])
    check("STATUS_UPDATE_STATE alias 0x30",
          STATUS_UPDATE_STATE == STATUS_PRELOAD_EVENT == 0x30)
    # 0x08 / 0x15 live (Host-Namen)
    check("live 0x08 [00] -> Maske 07 (A,B,C)",
          parse_filament_sensor_state(F("f7 01 04 00 08 07 e5")) == 0x07
          and parse_hardware_status(F("f7 01 04 00 08 07 e5")) == 0x07)
    hw = parse_get_hardware_status(F(
        "f7 01 13 00 15 10 20 30 40 50 60 00 00 00 00 00 00 00 00 00 00 ad"))
    check("live 0x15 GET_HARDWARE_STATUS 6 Einträge",
          hw["entries"] == [(i, 0) for i in range(1, 7)])
    check("live 0x15 Komponenten alle ok (CFS-FW)", hw["all_ok"]
          and [c["key"] for c in hw["components"]] ==
          ["eeprom", "aht", "odometer", "buffer", "rfid0", "rfid1"]
          and all(c["code"] == 0 for c in hw["components"]))
    hwf = parse_get_hardware_status(serial485.build_frame(
        BOX_ADDR, FUNC_GET_HARDWARE_STATUS,
        [0x12, 0x21, 0x30, 0x40, 0x50, 0x61] + [0] * 10))
    check("0x15 Fehlerfall EEPROM=2, AHT=1, RFID1=1", not hwf["all_ok"]
          and [c["code"] for c in hwf["components"]] == [2, 1, 0, 0, 0, 1]
          and [c["ok"] for c in hwf["components"]] ==
          [False, False, True, True, True, False])
    hwx = parse_get_hardware_status(serial485.build_frame(
        BOX_ADDR, FUNC_GET_HARDWARE_STATUS, [0x20] + [0] * 15))
    check("0x15 Nibble passt nicht zur Position -> code None",
          hwx["components"][0]["code"] is None and not hwx["all_ok"])
    check("namen 0x08/0x15/0x0f",
          FUNC_GET_FILAMENT_SENSOR_STATE == FUNC_HARDWARE_STATUS_LEGACY_08 == 0x08
          and FUNC_GET_HARDWARE_STATUS == 0x15
          and FUNC_TIGHTEN_UP_ENABLE == FUNC_PRELOAD_INHIBIT == 0x0F)
    check("builder get-hw-status 0x15", build_get_hardware_status()
          == serial485.build_frame(BOX_ADDR, 0x15, [], status=0xFF))
    check("builder tighten-up == inhibit",
          build_tighten_up_enable(1) == build_preload_inhibit(True))
    # Retries wie Original (live: Startwert je fun)
    check("FUNC_RETRIES 0x0a=0, 0x10/0x11=0 (bewusste Abweichung), Rest 4",
          func_retries(FUNC_GET_BOX_STATE) == 0
          and func_retries(FUNC_EXTRUDE) == func_retries(FUNC_RETRUDE) == 0
          and all(func_retries(f) == 4 for f in FUNC_RETRIES
                  if f not in (FUNC_GET_BOX_STATE, FUNC_EXTRUDE, FUNC_RETRUDE)))
    check("FUNC_RETRIES_ORIGINAL 0x10/0x11 = 4 (live)",
          FUNC_RETRIES_ORIGINAL[FUNC_EXTRUDE] == FUNC_RETRIES_ORIGINAL[
              FUNC_RETRUDE] == 4)

    # -- 5. Version/SN: Live-Layout, SN durch Platzhalter ersetzt
    #    (DATENSCHUTZ 15.10.2026; CRC neu berechnet, = build_frame) --
    sn_ph = "SN-DEMO-1234567890X"  # 19 Zeichen wie die echte SN
    f153 = F("f7 01 19 00 14 31 35 33 53 4e 2d 44 45 4d 4f 2d 31 32 33 34"
             " 35 36 37 38 39 30 58 d4")
    check("version-frame platzhalter CRC == build_frame",
          f153 == serial485.build_frame(BOX_ADDR, FUNC_GET_VERSION_SN,
                                        b"153" + sn_ph.encode(),
                                        status=STATUS_REPLY))
    ver, sn = parse_version_sn(f153)
    check("version 153", ver == "153" and sn == sn_ph)
    ver113, _ = parse_version_sn(serial485.build_frame(
        BOX_ADDR, FUNC_GET_VERSION_SN, b"113" + sn_ph.encode(),
        status=STATUS_REPLY))
    check("version 113 (Box-FW vor 12.10.)", ver113 == "113")
    check("version-req Hi",
          serial485.parse_frame(F("f7 01 03 ff 14 06")) == (0x14, b""))

    # -- 6. Buffer/Hardware: echte Frames + synthetische Builder --
    check("buffer 0x00", parse_buffer_state(F("f7 01 04 00 05 00 19")) == 0)
    # Referenzvektor ref-hi/valid_messages.json: mask=0x0F enable=0x01
    check("builder preload-enable (ref-hi f70105ff0d0f0169)",
          build_preload_enable(0x0F) == F("f7 01 05 ff 0d 0f 01 69"))
    check("builder preload-disable",
          build_preload_disable(0x01) == F("f7 01 05 ff 0d 01 00 b8"))
    check("builder preload-force-read",
          build_preload_force_read(0x01) == F("f7 01 05 ff 0d 01 02 b6"))
    # vanilla set_rfid_insert_reading(True) -> (0, 1)
    check("builder preload-enable vanilla (0,1)",
          build_preload(0x00, PRELOAD_ENABLE)[5:7] == bytes([0x00, 0x01]))
    import cfsseq
    check("preload box == cfsseq (M1)",
          all(build_preload(m, a) == cfsseq.build_preload(m, a)
              for m in range(16) for a in PRELOAD_ACTIONS))
    # Masken-Semantik: num/mask ist MASKE, keine Slotnummer
    check("mask 0x01/0x02/0x03 unverändert",
          [check_mask(m) for m in (0x01, 0x02, 0x03)] == [0x01, 0x02, 0x03])
    check("mehrfachmaske 0x05/0x0C",
          build_get_rfid(0x05)[5] == 0x05 and build_get_remain(0x0C)[5] == 0x0C)
    check("print-mode maske 0x02", build_print_mode(0x02)[5:7] == b"\x02\x00")
    check("slot_to_mask", [slot_to_mask(i) for i in range(4)]
          == [0x01, 0x02, 0x04, 0x08])
    for bad in (0x10, -1, True, "A", 1.0):
        try:
            check_mask(bad)
            check("mask-guard %r" % (bad,), False)
        except ValueError:
            check("mask-guard %r" % (bad,), True)
    for bad in (4, -1, True):
        try:
            slot_to_mask(bad)
            check("slot-guard %r" % (bad,), False)
        except ValueError:
            check("slot-guard %r" % (bad,), True)
    check("builder feed-enter",
          build_feed_mode_enter() == F("f7 01 05 ff 04 00 01 90"))
    check("builder print-mode",
          build_print_mode(0x01) == F("f7 01 05 ff 04 01 00 82"))
    check("builder engage", build_engage() == F("f7 01 04 ff 0f 01 b7"))
    check("builder release", build_release() == F("f7 01 04 ff 0f 00 b0"))
    check("builder get-rfid", build_get_rfid(0x0F) == F("f7 01 04 ff 02 0f 74"))
    check("builder get-remain", build_get_remain(0x0F) == F("f7 01 04 ff 03 0f 61"))
    check("builder get-wheel", build_get_wheel() == F("f7 01 04 ff 0e 01 a2"))
    check("builder get-buffer", build_get_buffer() == F("f7 01 03 ff 05 71"))
    check("builder get-hw", build_get_hardware(0) == F("f7 01 04 ff 08 00 db"))
    check("builder get-version", build_get_version() == F("f7 01 03 ff 14 06"))
    try:
        build_preload(0, 0x04)
        check("preload aktions-guard", False)
    except ValueError:
        check("preload aktions-guard", True)
    # -- 6b. FW-Analyse 15.10.2026: op 3, 0x0f-Alias, Timeout-Formel --
    check("builder preload-op3 tension-only",
          build_preload_force_tension_only(0x01)[5:7] == b"\x01\x03"
          and serial485.parse_frame(build_preload_force_tension_only(0x01))
          == (FUNC_SET_PRE_LOADING, b"\x01\x03"))
    check("preload op3 box == cfsseq",
          build_preload(0x02, PRELOAD_FORCE_TENSION_ONLY)
          == cfsseq.build_preload(0x02, cfsseq.PRELOAD_FORCE_TENSION_ONLY))
    check("preload-inhibit == alter engage/release",
          build_preload_inhibit(True) == build_engage()
          == F("f7 01 04 ff 0f 01 b7")
          and build_preload_inhibit(False) == build_release())
    check("FUNC_MOTOR_ACTION alias 0x0f",
          FUNC_MOTOR_ACTION == FUNC_PRELOAD_INHIBIT == 0x0F)
    # 17.10.2026: max(180+200·n, 300·n)
    check("force-timeout 1 slot 380 s", preload_force_timeout(0x01) == 380.0)
    check("force-timeout 2 slots 600 s", preload_force_timeout(0x05) == 600.0)
    check("force-timeout 3 slots 900 s", preload_force_timeout(0x0E) == 900.0)
    check("force-timeout 4 slots 1200 s", preload_force_timeout(0x0F) == 1200.0)
    check("force-timeout maske 0 -> wie 4 slots (G2), 1200 s",
          preload_force_timeout(0x00) == 1200.0)
    check("force-timeout nie unter Stock n x 300 s, deckt 1-Slot-Worst-Case 364 s",
          all(preload_force_timeout(m) >= 300.0 * bin(m).count("1")
              for m in range(1, 16))
          and preload_force_timeout(0x01) > 364.0)
    import serial485 as _s485
    check("force-timeout 4 slots <= serial485 MAX_BUS_HOLD_S (retries 0)",
          max(preload_force_timeout(m) for m in range(16)) <= _s485.MAX_BUS_HOLD_S
          and _s485.Serial485._check_hold(preload_force_timeout(0x0F), 0) == 1200.0)
    check("extrude-modi re-export",
          EXTRUDE_MODE_EXTRUDER_CHECK == 7 and EXTRUDE_MODE_QUERY == 5
          and EXTRUDER_CHECK_MM == 3 and RETRUDE_P_PULL_HUB == 1)
    check("status-namen", status_name(0x16) == "Slot ohne Filament"
          and status_name(STATUS_CRC) == "CRC-Fehler im Request"
          and status_name(0x7f) == "unbekannt 0x7f")

    # -- 7. BoxState: Tn/Tnn-Roundtrip (Modell, kein Bus) --
    bs = BoxState()
    bs.modify_Tn_data(1, 0, 0, subpart=1, uppart=0)
    bs.modify_Tn_inner_data(1, 0, 0, "filament", "A")
    check("tn get", bs.get_Tn_data(1, 0, 0)["subpart"] == 1)
    check("tn inner", bs.get_Tn_inner_data(1, 0, 0, "filament") == "A")
    check("tn map", (1, 0, 0) in bs.generate_Tn_map())
    tnn = BoxState.tnn_key(1, 0)
    check("tnn key", tnn == "T1A")
    bs.update_Tnn_map(tnn, bs.generate_Tnn_content(tnn, rfid="10125", remain=28))
    check("tnn content", bs.get_Tnn_content("T1A")["remain"] == 28
          and len(bs.get_Tnn_map()) == 1)
    check("save-layer", bs.update_tn_save_data(1, 0, 0) is not None
          and bs.get_tn_save_data(1, 0, 0) is not None
          and bs.sync_tn_data() >= 1)
    check("same-material", bs.update_same_material_list(["T1A"]) == ["T1A"])
    bs.e_err_set("key_test")
    check("e_err", bs.clear_e_err() == {})
    check("color-count", bs.clear_color_count() == {})

    # -- 8. BoxAction gegen FakeBus (Request-Payloads + STATUS/Timeout) --
    bus = _FakeBus([
        F("f7 01 0d 00 02 42 3a 75 6e 6b 6e 6f 77 6e 3b 8e"),  # rfid
        F("f7 01 07 00 03 1c ff 00 00 11"),  # remain
        F("f7 01 07 00 0e c4 e6 fe 0b f3"),  # wheel
        F("f7 01 03 00 0d 9e"),  # preload-ACK (synthetisch, CRC-echt)
        F("f7 01 03 00 04 a1"),  # feed-ACK (Live-Pollform)
        F("f7 01 03 00 07 a8"),  # 0x07-Stopp-ACK (live 19.10.2026)
        F("f7 01 03 00 0d 9e"),  # force-preload-ACK (synthetisch)
        F("f7 01 03 00 0d 9e"),  # force-tension-only-ACK (synthetisch)
        F("f7 01 03 00 0f 90"),  # preload-inhibit-ACK
    ])
    act = BoxAction(bus)
    # num ist MASKE wie im Original: 0x02 = Slot B -> Live-Antwort B:unknown
    check("action rfid", get_rfid(act.communication_get_rfid(0x02))
          == {"B": "unknown"} and bus.calls[0][2] == [0x02])
    check("action remain", get_remain_len(
        b"\x0f", act.communication_get_remain_len(0x0F))["A"] == 28)
    check("action wheel",
          abs(act.communication_measuring_wheel() - (-1847.94)) < 0.02)
    act.communication_set_pre_loading(0x01, PRELOAD_ENABLE)
    act.communication_set_box_mode_feed()
    act.communication_ctrl_connection_motor_action(0x00)
    check("action preload-payload", bus.calls[3][2] == [0x01, 0x01])
    check("action feed-payload", bus.calls[4][2] == [0x00, 0x01])
    check("action connection-motor 0x07 [00] (live 19.10.)",
          bus.calls[5][1] == 0x07 and bus.calls[5][2] == [0x00])
    check("action status/timeout",
          all(c[3].get("status") == STATUS_OPERATIONAL
              and c[3].get("timeout") == TIMEOUT_OPERATIONAL
              for c in bus.calls))
    # 0x0d op 2/3: langer Timeout + retries=0 (FW-Analyse: Antwort nach Vorgang)
    act.communication_set_pre_loading(0x01, PRELOAD_FORCE_READ)
    act.communication_set_pre_loading(0x03, PRELOAD_FORCE_TENSION_ONLY,
                                      timeout=123.0)
    check("action force: timeout 380 s, retries 0",
          bus.calls[6][2] == [0x01, 0x02]
          and bus.calls[6][3].get("timeout") == 380.0
          and bus.calls[6][3].get("retries") == 0)
    check("action force op3: override-timeout, retries 0",
          bus.calls[7][2] == [0x03, 0x03]
          and bus.calls[7][3].get("timeout") == 123.0
          and bus.calls[7][3].get("retries") == 0)
    check("action enable: retries 4 wie Original",
          bus.calls[3][3].get("retries") == 4)
    act.communication_set_preload_inhibit(True)
    check("action preload-inhibit payload", bus.calls[8][1] == 0x0F
          and bus.calls[8][2] == [0x01])
    try:
        act.communication_ctrl_connection_motor_action(0x02)
        check("motor act-guard", False)
    except ValueError:
        check("motor act-guard", True)

    # -- 8b. Retries je fun (Original) gegen FakeBus mit Live-Antworten --
    bus2 = _FakeBus([
        F("f7 01 09 00 0a 16 2e 00 00 00 00 b5"),
        F("f7 01 13 00 15 10 20 30 40 50 60 00 00 00 00 00 00 00 00 00 00 ad"),
        F("f7 01 04 00 08 01 f7"),
        F("f7 01 04 00 08 07 e5"),
        F("f7 01 03 00 0f 90"),
    ])
    act2 = BoxAction(bus2)
    st_raw = act2.communication_get_box_state()
    check("action 0x0a retries=0 (Original-Poll)",
          bus2.calls[0][3].get("retries") == 0
          and parse_box_state(st_raw, box_fw="153")["kind"] == "state")
    act2.communication_get_hardware_status()
    check("action 0x15 leer, retries 4", bus2.calls[1][1] == 0x15
          and bus2.calls[1][2] == [] and bus2.calls[1][3]["retries"] == 4)
    check("action 0x08 [01] Hub-Maske",
          act2.communication_get_filament_sensor_state(1) == b"\x01"
          and bus2.calls[2][1:3] == (0x08, [0x01]))
    import warnings as _w
    with _w.catch_warnings(record=True) as wl:
        _w.simplefilter("always")
        act2.communication_get_hardware_status(0)  # alte Signatur -> 0x08
    check("action alte hw-Signatur -> 0x08 + DeprecationWarning",
          bus2.calls[3][1:3] == (0x08, [0])
          and any(issubclass(x.category, DeprecationWarning) for x in wl))
    act2.communication_tighten_up_enable(1)
    check("action tighten-up 0x0f [01] retries 4",
          bus2.calls[4][1:3] == (0x0F, [1]) and bus2.calls[4][3]["retries"] == 4)

    # -- 8c. Review 16.10.2026: Status-Durchreichung (M2), 0x10/0x11 (M3),
    #    Wheel-Guard (G1), Maske 0 (G2), box_fw-Formate (G3), Anhang (G4) --
    st0c = F("f7 01 07 0c 10 c3 30 40 af a4")  # live: 0x10 mode 5, Status 0x0c
    bus3 = _FakeBusFull([
        st0c, st0c,
        F("f7 01 09 51 0a 11 35 00 04 01 00 a1"),  # live 0x0a SPEED_ERR
        F("f7 01 07 30 0a 03 00 00 00 51"),        # live 0x0a UPDATE_STATE
        serial485.build_frame(BOX_ADDR, FUNC_GET_BOX_STATE, [], status=0x02),
        serial485.build_frame(BOX_ADDR, FUNC_RETRUDE, [], status=0x16),
        F("f7 01 03 00 11 ca"),                    # live leere 0x11-Antwort
        F("f7 01 03 00 0d 9e"),
        serial485.build_frame(BOX_ADDR, FUNC_MEASURING_WHEEL, []),
    ])
    act3 = BoxAction(bus3)
    try:
        act3.communication_extrude(0x02, EXTRUDE_MODE_QUERY)
        check("M2 Status 0x0c -> CfsStatusError", False)
    except CfsStatusError as e:
        check("M2 Status 0x0c -> CfsStatusError (Name, Daten)",
              e.status == 0x0C and e.name == STATUS_NAMES[0x0C]
              and e.data == F("c3 30 40 af") and e.fun == FUNC_EXTRUDE)
    check("M3 extrude: [02 05 00], retries 0, timeout 15 s",
          bus3.calls[0][1:3] == (0x10, [0x02, 0x05, 0x00])
          and bus3.calls[0][3]["retries"] == 0
          and bus3.calls[0][3]["timeout"] == FUNC_TIMEOUTS_SO[0x10] == 15.0)
    r = act3.communication_extrude(0x02, EXTRUDE_MODE_QUERY,
                                   raise_on_status=False)
    check("M2 raise_on_status=False -> StatusBytes .status 0x0c",
          isinstance(r, bytes) and r == F("c3 30 40 af") and r.status == 0x0C)
    r = act3.communication_get_box_state()
    pb = parse_box_state(r)
    check("M2 0x0a Status 0x51 wirft NICHT, Status durchgereicht",
          r.status == 0x51 and pb["error_code"] == 0x51
          and pb["frame_status"] == 0x51)
    r = act3.communication_get_box_state()
    check("M2 0x0a UPDATE_STATE 4 B Rohdaten -> event (über .status)",
          r.status == 0x30 and parse_box_state(r)["kind"] == "event")
    try:
        act3.communication_get_box_state()
        check("M2 0x0a dataless CRC-Status 0x02 wirft", False)
    except CfsStatusError as e:
        check("M2 0x0a dataless CRC-Status 0x02 wirft", e.status == 0x02)
    try:
        act3.communication_retrude(0x01, RETRUDE_P_TO_BUFFER)
        check("M2 0x11 Status 0x16 wirft", False)
    except CfsStatusError as e:
        check("M2 0x11 Status 0x16 wirft", e.status == 0x16
              and e.name == "Slot ohne Filament")
    r = act3.communication_retrude(0x00, RETRUDE_P_PULL_HUB)
    check("M3 retrude mask 0 (alle), p1, retries 0, timeout 150 s",
          r == b"" and r.status == 0
          and bus3.calls[6][1:3] == (0x11, [0x00, 0x01])
          and bus3.calls[6][3]["retries"] == 0
          and bus3.calls[6][3]["timeout"] == 150.0)
    act3.communication_set_pre_loading(0x00, PRELOAD_FORCE_READ)
    check("G2 0x0d op 2 Maske 0 -> 1200 s, retries 0",
          bus3.calls[7][2] == [0x00, 0x02]
          and bus3.calls[7][3]["timeout"] == 1200.0
          and bus3.calls[7][3]["retries"] == 0)
    r = act3.communication_measuring_wheel(WHEEL_OP_RESET, reset=True)
    check("G1 wheel op 0 mit reset=True sendet [00]",
          bus3.calls[8][1:3] == (0x0E, [0x00]) and r.status == 0)
    n_calls = len(bus3.calls)
    for name, call in (
            ("G1 wheel op 0 ohne reset", lambda: act3.communication_measuring_wheel(0)),
            ("G1 wheel op 2", lambda: act3.communication_measuring_wheel(2)),
            ("M3 extrude mode 8", lambda: act3.communication_extrude(0x01, 8)),
            ("M3 extrude p 256", lambda: act3.communication_extrude(0x01, 7, 256)),
            ("M3 extrude maske 0", lambda: act3.communication_extrude(0, 0)),
            ("M3 extrude maske 0x03", lambda: act3.communication_extrude(3, 0)),
            ("M3 retrude p 2", lambda: act3.communication_retrude(0x01, 2)),
            ("M3 retrude maske 0x05", lambda: act3.communication_retrude(5, 0))):
        try:
            call()
            check("guard " + name, False)
        except ValueError:
            check("guard " + name, True)
    check("guards senden nichts", len(bus3.calls) == n_calls)
    check("M3 mode 3 = Fehler löschen dokumentiert",
          EXTRUDE_MODE_CLEAR_ERROR == 3
          and "Fehler" in EXTRUDE_MODES[EXTRUDE_MODE_CLEAR_ERROR])

    # Am ECHTEN Serial485 mit Mock-Port: Status-Fehler + Spätantwort
    import time as _t

    class _MockPort:
        def __init__(self, reply=None, delay=0.0):
            self.reply, self.delay, self.written, self.t0 = reply, delay, [], None
            self.buf = bytearray()

        def reset_input_buffer(self):
            self.buf.clear()

        def write(self, frame):
            self.written.append(bytes(frame))
            self.t0 = _t.monotonic()

        def read(self, n):
            if (self.reply is not None and self.t0 is not None
                    and _t.monotonic() - self.t0 >= self.delay):
                self.buf += self.reply
                self.reply = None
            if self.buf:
                c = bytes(self.buf[:n])
                del self.buf[:n]
                return c
            _t.sleep(0.002)
            return b""

    real = serial485.Serial485("/dev/ttyFAKE", ser=_MockPort(st0c))
    try:
        BoxAction(real).communication_extrude(0x02, EXTRUDE_MODE_QUERY,
                                              timeout=0.2)
        check("M2 echter Serial485: Status 0x0c wirft", False)
    except CfsStatusError as e:
        check("M2 echter Serial485: Status 0x0c wirft", e.status == 0x0C
              and real.ser.written[0] == cfsseq.build_extrude_stage(0x02, 5))
    late = serial485.Serial485("/dev/ttyFAKE", ser=_MockPort(
        F("f7 01 03 00 11 ca"), delay=0.1))
    try:
        BoxAction(late).communication_retrude(0x02, RETRUDE_P_PULL_HUB,
                                              timeout=0.03)
        ok = False
    except TimeoutError:
        ok = True
    check("M3 Spätantwort nach Timeout (retries 0): genau 1 Sendung",
          ok and len(late.ser.written) == 1 and late.stats()["sent"] == 1)
    fe = serial485.Serial485("/dev/ttyFAKE", ser=_MockPort(
        serial485.build_frame(0x81, 0x0e, [1, 2, 3, 4]) + F(
            "f7 01 07 00 0e c4 e6 fe 0b f3")))
    check("M1 Box-Messrad: fremder Absender 0x81 übersprungen",
          abs(BoxAction(fe).communication_measuring_wheel() - (-1847.94)) < 0.02)

    # G3: box_fw-Formate (Host loggt "1.5.3", Wire "153")
    for val, exp in (("153", 153), ("113", 113), (153, 153), (113, 113),
                     (b"153", 153), ("1.5.3", 153), ("1.1.3", 113),
                     (" 1.5.3 ", 153), (None, None)):
        check("G3 box_fw %r -> %r" % (val, exp), _box_fw_num(val) == exp)
    for val in ("15", "1.5", "1.10.3", "153" + "1" * 5, 15, 1153, True, "",
                "abc", "1.5.x"):
        try:
            _box_fw_num(val)
            check("G3 box_fw %r -> ValueError" % (val,), False)
        except ValueError:
            check("G3 box_fw %r -> ValueError" % (val,), True)
    six = F("f7 01 09 00 0a 16 2e 00 00 00 00 b5")
    check("G3 6-Byte-Frame mit box_fw=113 / '1.1.3' -> unknown",
          parse_box_state(six, box_fw=113)["kind"] == "unknown"
          and parse_box_state(six, box_fw="1.1.3")["kind"] == "unknown"
          and parse_box_state(six, box_fw="1.5.3")["kind"] == "state")

    # G4: Voll-Frame mit angehängtem Byte -> Frame (CRC über len+3 Bytes)
    rem = F("f7 01 07 00 03 1c ff 00 00 11")
    check("G4 Frame + Anhang 0x00 -> als Frame geparst",
          get_remain_len(b"\x0f", rem + b"\x00")
          == {"A": 28, "B": None, "C": 0, "D": 0})
    check("G4 0x0a-Frame + 0xF7 -> frame_status erkannt",
          get_box_status(six + b"\xf7")["frame_status"] == 0
          and get_box_status(six + b"\xf7")["len"] == 6)
    bad_tail = bytearray(rem + b"\x00")
    bad_tail[9] ^= 0xFF  # CRC kaputt -> kein Frame -> Rohdaten (Länge 11)
    check("G4 kaputte CRC + Anhang -> Rohdaten", not _is_full_frame(bad_tail)
          and _is_full_frame(rem))

    # -- 9. CRC-Negativtest (verfälschtes Byte muss scheitern) --
    bad = bytearray(F("f7 01 07 00 03 1c ff 00 00 11"))
    bad[5] ^= 0x01
    try:
        serial485.parse_frame(bytes(bad))
        check("crc-guard", False)
    except ValueError:
        check("crc-guard", True)

    print("SELBSTTEST: %s" % ("ok" if fails == 0 else "%d FEHLER" % fails))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
