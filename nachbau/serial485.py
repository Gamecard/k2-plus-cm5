#!/usr/bin/env python3
"""serial485.py – Reiner-Python-Nachbau des Creality-RS485-Bustreibers (K2 Plus).

Ersetzt: serial_485_wrapper.cpython-39.so (armhf, CPython 3.9, proprietär).

Belege (aus der Protokollanalyse, nicht in diesem Repo):
- Frame-Format + Func-Codes: RS485-Mitschnitte und Stock-Host-Code
- CRC-Algorithmus: auto_addr_wrapper.py (Klartextquelle vom Drucker, POLY=0x07,
  crc8_cal/cal_pack_crc)
- Retry/Timeout-Verhalten: klippy.log-Mitschnitte (retries=4, Timeouts je Command)
- Empfangs-Validierung: belt_mdl.py recv_sensor_data (head/addr/len/fun-Prüfung)

Abhängigkeit für echte Hardware: pyserial (pip install pyserial).
Der Selbsttest unten läuft OHNE Hardware und OHNE pyserial.
"""

# --- Protokoll-Konstanten (Beleg: auto_addr_wrapper.py + Live-Frames) ---
PACK_HEAD = 0xF7
STATUS_OK = 0x00
STATUS_OPERATIONAL = 0xFF
POLY = 0x07

# Timeouts je Command (Beleg: auto_addr_wrapper.py cmd_timeout)
TIMEOUT_SHORT = 0.05    # SET_SLAVE_ADDR, GET_ADDR_TABLE, LOADER_TO_APP
TIMEOUT_MEDIUM = 0.10   # ONLINE_CHECK
TIMEOUT_LONG = 1.0      # Addressing-/Belt-Kommandos
TIMEOUT_OPERATIONAL = 2.0  # CFS-Operational-Kommandos (Live-Introspektion/Referenz)

# Auto-Addr-Commands (Beleg: auto_addr_wrapper.py)
CMD_GET_SLAVE_INFO = 0xA1
CMD_SET_SLAVE_ADDR = 0xA0
CMD_ONLINE_CHECK = 0xA2
CMD_GET_ADDR_TABLE = 0xA3
CMD_LOADER_TO_APP = 0x0B

# Broadcast-Adressen (Beleg: auto_addr_wrapper.py)
BROADCAST_MB = 0xFE      # CFS-Box
BROADCAST_CLM = 0xFD     # Closed-loop-Motoren
BROADCAST_BTM = 0xFC     # Belt-Module
BROADCAST_ALL = 0xFF     # Loader-Check (auto_addr.py BROADCAST_ADDR, 0x0B)
# 0xEA/0xEB: CFS-App-Sonderadressen (FW-Analyse CFS-App):
# ~~0xEA = Broadcast ohne Antwort~~ KORRIGIERT (CFS-FW): 0xEA wird bei
# normalen Kommandos TROTZDEM beantwortet (Absender = eigene Adresse; nur
# CRC-Fehler-/unbekannt-fun-/0x50-Antwort entfallen); 0xEB nur für 0x50
# (Antwort mit Absender 0xEB) / 0x51 (Debug, keine Antwort). 0xFE/0xFF nimmt
# die App nur ohne eigene Adresse an. Für Abfragen bei mehreren Boxen
# ungeeignet (Kollision) – Absenderprüfung bleibt hier "beliebig".
ADDR_EA = 0xEA
ADDR_EB = 0xEB
# Ziele, bei denen der Absender der Antwort NICHT gleich dem Ziel sein muss
# (Review M1, 16.10.2026): Broadcasts antworten mit Geräte- ODER Broadcast-
# Adresse (live: a1 an 0xFE -> Antwort von 0xFE, Live-Log 12.10.2026;
# a2 an 0x01 -> Antwort von 0x01). Für alle anderen Ziele gilt: Antwort-
# Absender == Ziel (live: 0x01 und 0x81..0x84 antworten nur mit eigener
# Adresse). Ohne diese Prüfung würde z.B. ein Box-Frame (0x01, fun 0x0e) als
# Antwort für Motor 0x81 (gleicher fun auf ttyS5) akzeptiert.
BROADCAST_ADDRS = frozenset((BROADCAST_MB, BROADCAST_CLM, BROADCAST_BTM,
                             BROADCAST_ALL, ADDR_EA, ADDR_EB))
_AUTO = object()  # Sentinel: expect_addr aus Zieladresse ableiten


def reply_addr_for(addr):
    """Erwarteter Absender der Antwort auf einen Request an addr:
    addr selbst, bei BROADCAST_ADDRS None (= beliebig)."""
    return None if addr in BROADCAST_ADDRS else addr

MAX_RETRIES = 4          # Beleg: klippy.log "retries = 4"

# Obergrenze, wie lange EINE Transaktion den Bus-Lock halten darf
# (timeout * (retries + 1)), Review M6 16.10.2026. Längster belegter Fall:
# 0x0d op 2/3 Maske 0x0F/0x00 = 1200 s (box.preload_force_timeout =
# max(180+200·n, 300·n) seit 17.10.2026, retries=0) – genau an der Grenze.
# Darüber -> ValueError statt stundenlanger Bus-Blockade (z.B. 300 s x 5).
MAX_BUS_HOLD_S = 1200.0
# Ab dieser Haltedauer wird der lange Lock im Log vermerkt (INFO).
LONG_HOLD_LOG_S = 5.0

# Serial_485_Wrapper._done-Begrenzung (Fix Teil B.1, 16.10.2026): nie abgeholte
# Antworten ließen _done unbegrenzt wachsen (Speicherleck bei langlebigen
# Sessions). MAX = harte Obergrenze (älteste IDs fliegen zuerst raus),
# TTL = Verfallszeit für verwaiste Einträge (Threads die nie abholen).
WRAPPER_DONE_MAX = 128
WRAPPER_DONE_TTL_S = 60.0
# Serial485 Bg-Race-Fix (Teil B.2, 16.10.2026): raw_send registriert eine
# erwartete Antwort, die der Hintergrund-Thread NICHT dispatcht, sondern in
# _resp_queue parkt; get_response holt dort zuerst ab. So kann der bg-Thread
# keine get_response-Antwort mehr in der Lücke zwischen raw_send und
# get_response wegschnappen. TTLs gegen Verwaisten bei Crashs.
BG_EXPECT_TTL_S = 2.0
RESP_QUEUE_TTL_S = 5.0
RESP_QUEUE_MAX = 32

# Plausibilitätsgrenzen für das Längenfeld (len = status+fun+daten... inkl. crc,
# vgl. build_frame: len = len(data)+3). Minimum 3 = Frame ohne Daten.
# EMPFANG: bleibt beim Byte-Maximum 255 (Parser soll jeden Frame erkennen;
# live längstes RX-len = 46, RFID-Antwort). SENDEN: s. TX_* unten.
FRAME_LEN_MIN = 3
FRAME_LEN_MAX = 255

# --- Sendeschutz (17.10.2026) -------------------------------------------
# Längengrenze für AUSGEHENDE Frames (check_tx_frame/build_tx_frame; der
# Empfang bleibt bei FRAME_LEN_MAX = 255):
# - CFS-App (FW-Analyse, B): Empfangs-Statemachine FUN_0800f880
#   prüft nur len >= 3, der Frame-Kontext ist 0x70 = 112 B, Daten ab
#   Offset 6, CRC fest bei 0x6A -> sauber nur <= 100 Datenbytes (len <= 103);
#   längere Frames überschreiben CRC-/Timeout-Felder, ab len ~110 Überlauf.
# - CFS-Bootloader (FW-Analyse, B): vec_54 @0x080011ec
#   verwirft len > 0xFC STUMM (keine Antwort, Host sieht nur Timeouts).
# - Längste tatsächlich gesendete Frames: live 0xA0 (Auto-Addr SET) mit
#   13 Datenbytes (len 0x10; Auszählung aller `retries = N, cmd = …` in
#   den Live-Logs vom 15./16.10.2026), dann 0x06-Schreiben (6 Datenbytes); im
#   Nachbau ebenso. Einzige längere Klasse: Update-Datenblöcke (fun 0xF0)
#   mit GENAU 0x80 Datenbytes (len 0x83) – der Bootloader kopiert je Frame
#   fest 0x80 B (FUN_08001480), größere Blöcke zerstören das Image.
# Gewählt (konservativ): alle fun 100 Datenbytes (= CFS-App-Grenze, das
# 7-Fache des längsten Live-Requests); fun 0xF0 (Update-Pfad, Ziel ist
# dann der Bootloader) 0x80 Datenbytes (len 0x83 < 0xFC). ANNAHME: Die
# Motor-App puffert mindestens so viel wie die CFS-App (Motor-RX-Puffer
# nicht ausgewertet; live nie > 6 Datenbytes an Motoren).
TX_DATA_MAX = 100              # Datenbytes je Sende-Frame (len <= 103)
TX_FUNC_UPDATE = 0xF0          # Update-Pfad (mcu_util_485), Datenblöcke
TX_DATA_MAX_UPDATE = 0x80      # Datenbytes bei fun 0xF0 (len <= 0x83)
# HINWEIS 18.10.2026: Datenblöcke fun 0xF0 sind seit TX_F0_ALLOWED_SUBS
# ohnehin gesperrt (nur [00]/[02]/[03]); die Längengrenze bleibt als
# zweite Schranke bestehen.
BOOTLOADER_LEN_MAX = 0xFC      # harte Bootloader-Grenze für das len-Byte
# Broadcast-Schutz (Motor-FW-Analyse, B): Die Motor-App nimmt per
# 0xFF genau reboot (0x01), boot (0x0B) und rs485_addr (0x0E) an
# (Broadcast-Flag @0xd758-0xd762). boot 0x0B [00] (FUN_104e6) setzt
# boot_key 0x8567 und löst einen Watchdog-Reset aus -> Bootloader;
# reboot 0x01 [00] (FUN_f724) startet neu. Per 0xFF träfe das ALLE Motoren
# gleichzeitig (Haltemoment weg, Z sackt ab). Beide Handler wirken im
# Binärmodus NUR bei Payload-Byte 0 == 0 (0x104f6-0x104fc bzw.
# 0xf734-0xf73a: `lbu a5,0(payload); beqz` -> ausführen, sonst Rücksprung).
# Daher:
# - 0xFF/0xFD + fun 0x01: immer gesperrt (kein legitimer Nutzer).
# - 0xFF/0xFD + fun 0x0B: gesperrt, AUSSER exakt Payload [01] an 0xFF =
#   CFS-Loader->App (auto_addr.logic_loader_check, Stock live
#   `0xff 0x4 0x0 0xb 0x1`; laut FW-Analyse nimmt der CFS-Bootloader
#   an 0xFF nur 0x0B [01] an; die Motor-App ignoriert [01], s. oben).
# 0xFD (BROADCAST_CLM) ist die Motor-Gruppenadresse der Auto-Adressierung
# (Motor-Bootloader, Verhalten nicht im Image) – vorsorglich mit gesperrt.
# 0xFE/0xFC (CFS-/Belt-Gruppe) erreichen die Motor-App nicht (Dispatcher
# FUN_d5c2: nur eigene Adresse oder 0xFF) und bleiben frei. Unicast
# 0x0B/0x01 an 0x81-0x84 sperrt motor.py (boot()/reboot() = Stubs).
TX_FUNC_REBOOT = 0x01
TX_FUNC_BOOT = CMD_LOADER_TO_APP           # 0x0B (Motor: boot)
TX_BOOT_GUARD_ADDRS = frozenset((BROADCAST_ALL, BROADCAST_CLM))
TX_LOADER_TO_APP_PAYLOAD = (0x01,)         # einzig erlaubt: 0xFF 0x0B [01]
# Motor-Kalibrier-/Flash-Sperre (17.10.2026, Motor-FW-Analyse, B) – HART,
# ohne Override:
# - 0x03 encoder_calibrate_official: open-loop bis ~63 Umdrehungen, Schutz
#   und Regel-IRQ aus, EN/Bus wirkungslos (~40 s), brennt phase_order_invert
#   (MCU-Flash) + 2x Encoder-IC-EEPROM.
# - 0x04 elec_offset_calibrate (auch die _ALL-Variante = je Motor unicast):
#   ±1 Umdrehung open-loop ohne Schutz, schreibt phase_order_invert immer,
#   bei Pass alle Parameter + Werks-Sicherungs-Record.
# - 0x07 flash_param: erlaubt NUR die lesenden Subs 02 (Flash-Prüfsumme
#   neu berechnen/vergleichen) und 06 (Flash-Wert einer ID lesen) – beide
#   ohne Schreibzugriff und ohne Regelungs-Eingriff. Gesperrt:
#   01 Werksreset (Flash) + Neustart, 03 alle Parameter in den Flash
#   (Regelung kurz aus), 05 RAM-Wert setzen (Regelung aus/Neu-Init),
#   04 „alle Parameter aus dem Flash laden“: schreibt zwar keinen Flash,
#   überschreibt aber die RAM-Werte, die der Boot per 0x06 abgeglichen hat
#   (Motor-Init) – Zustandsänderung am laufenden Regler, daher gesperrt.
#   Sub 00/>=07 (wirkungslos) und leere Payload: gesperrt (Whitelist).
# GILT NUR für Motor-Adressen: 0x80-0x88 (Host-Tabelle Motor_Addr NUM 0-8,
# X/Y/Z/Z1 = 0x81-0x84; Extruder transparent ebenfalls 0x81) und die
# Motor-Gruppenadresse 0xFD. Dieselben fun-Codes haben an anderen Geräten
# eine ANDERE, benutzte Bedeutung und bleiben frei: CFS-Box (0x01-0x04,
# 0xFE) 0x03 GET_REMAIN_LEN, 0x04 SET_BOX_MODE, 0x07 Hauptmotor
# (Func-Tabelle der Protokollanalyse); Belt (0x21/0x22) 0x04 WRITE_FLASH
# (k2_belt); Rack 0x11. 0xFF bleibt frei: die Motor-App ignoriert
# 0x03/0x04/0x07 per Broadcast (kein Broadcast-Flag, B).
TX_MOTOR_GUARD_ADDRS = frozenset(list(range(0x80, 0x89)) + [BROADCAST_CLM])
TX_FUNC_ENCODER_CALIBRATE = 0x03
TX_FUNC_ELEC_OFFSET_CALIBRATE = 0x04
TX_FUNC_FLASH_PARAM = 0x07
TX_MOTOR_BLOCKED_FUNCS = frozenset((TX_FUNC_ENCODER_CALIBRATE,
                                    TX_FUNC_ELEC_OFFSET_CALIBRATE))
TX_FLASH_PARAM_READ_SUBS = frozenset((0x02, 0x06))   # einzig erlaubt bei 0x07
# Nachtrag 26.10.2026 (Motor-FW-Analyse 25.10.2026, B): an Motor-
# Adressen außerdem nie fun 0x02 (encoder_calibrate: Encoder-Register-
# prüfung, bei Abweichung EEPROM-Brand + 6 s) und fun 0x12 (dev_uuid) nur
# exakt [02] = UUID lesen; Sub 01 + 8 B schreibt den Kalibrier-Record aus
# dem RAM-Puffer neu (Flash, ≈ 2,4 s). 0x12 auch an 0xFF nur [02]: ob die
# Motor-App 0x12 per Broadcast annimmt, ist nicht belegt, kein Sender nutzt
# 0x12 an 0xFF. 0x02 an 0xFF/0xFD ist schon über die RFID-Regel gesperrt.
TX_FUNC_ENCODER_REG_CHECK = 0x02
TX_FUNC_DEV_UUID = 0x12
TX_DEV_UUID_READ = (0x02,)                           # einzig erlaubt bei 0x12
# Update-/Bootloader-/Service-Sperre (18.10.2026, Analyse des Stock-Updatewegs
# und des CFS-Bootloaders, B) – HART, ohne Override, an JEDE Adresse:
# - fun 0xF0 (mcu_util_485-Update-Pfad, Bootloader): erlaubt NUR genau ein
#   Datenbyte Sub 00 (Version lesen), 02 (App starten; Stock bei jedem Boot
#   an alle adressierten Geräte) und 03 (Sektorgröße lesen). Gesperrt:
#   01 Update-Request (danach überschreiben Länge + Datenblöcke die App),
#   der 4-Byte-Längen-Frame, Datenblöcke (0x80 B), 06 = Erase der CFS-
#   Flash-DB-Page 0x0807F800 (Arbeitsmodus/Parameter auf Werkseinstellung,
#   kein Teil des App-Flashens), unbekannte Subs, leere Payload und jede
#   Sub mit Zusatzbytes (Whitelist: Payload exakt [00]/[02]/[03]).
# - fun 0xF2 (EEPROM-Dienst des CFS-Bootloaders, 24C16 per Bit-Bang-I²C;
#   op 0 schreibt mit 8-Byte-Schlüssel ab EEPROM-Adresse 0): komplett
#   gesperrt, Host nutzt ihn nie (mcu_util_485 kein `#242`).
# - Zieladresse 0xF0: adressunabhängige Service-Adresse des CFS-Bootloaders
#   (nur fun 0xF2 bedient, Antwort-Absender 0xF0); kein Gerät hat diese
#   Busadresse (CFS 0x01.., RFID 0x11.., Belt 0x23.., Motor 0x85..) ->
#   jedes fun an 0xF0 gesperrt.
# - CFS-App-Sonderadressen 0xEA/0xEB (jede Box nimmt sie wie die eigene
#   Adresse an, Func-Tabelle R153): 0x50 = Config #6
#   (Arbeitsmodus) persistent in die Flash-DB + SYSRESETREQ (CFS-FW, B),
#   0x51 = Debug-Schalter G[3] (nur 0xEB, keine Antwort),
#   0x56 = Status 0 + Reset (Hi-Mitschnitt: Reset in den Bootloader;
#   K2-Wirkung O). Alle drei schreibend/zurücksetzend, an 0xEA/0xEB
#   träfen sie ALLE Boxen gleichzeitig; kein legitimer Nutzer -> gesperrt.
#   (0x50 an Box-Unicast sperrt zusätzlich k2_box.FORBIDDEN_FUNS.)
TX_F0_ALLOWED_SUBS = frozenset((0x00, 0x02, 0x03))
TX_FUNC_EEPROM = 0xF2
TX_ADDR_EEPROM_SERVICE = 0xF0
TX_CFS_SPECIAL_ADDRS = frozenset((ADDR_EA, ADDR_EB))
TX_CFS_SPECIAL_BLOCKED_FUNCS = frozenset((0x50, 0x51, 0x56))
# Belt-Whitelist (18.10.2026, 2. Stufe; Belt-FW-Analyse, B) – HART, ohne Override, an die Belt-App-Adressen 0x21 (mdlx) /
# 0x22 (mdly). Die Belt-FW (CH32V003) kennt NUR fun 0/2/4/6/8, alles
# andere wird stumm verworfen (Host sähe nur Timeouts) -> Whitelist:
# - fun 0 (Version) / 6 (ADC): nur OHNE Daten (lesend).
# - fun 2 (Flash lesen): Daten exakt [03] – n > ~14 kopiert n·4 B in den
#   32-B-Anfragepuffer (RAM-Überlauf); n = 3 ist der einzige sichere
#   und der einzige vom Host benutzte Wert.
# - fun 4 (Flash schreiben, Page 0x08003C00): Daten exakt [03] + 12 Byte
#   (place/idl/full je 4 B BE). [00]/n < 3 löscht die Kalibrierung
#   (0xFFFFFFFF = key714/715), n > 16 schreibt nichts, meldet aber Erfolg.
#   Die Freigabe (allow_write) bleibt Sache von k2_belt.
# - fun 8 (Schieber fahren): Daten exakt [dir][dist BE32], dir 0/1,
#   1 <= dist <= TX_BELT_MOVE_MAX. Die FW hat KEINE Endlage und keine
#   Weggrenze außer 1..0xFFFE (≈ 7 min Dauerfahrt); live max. Regelschritt
#   12 Einheiten -> Grenze 50 je Befehl (ANNAHME). dist 0 fährt
#   in der FW nicht (nur Echo) und wird hier ebenfalls gesperrt.
# - Belt-Frames gesamt <= 32 B (Rx-Puffer 32 B, Index ungeprüft);
#   mit der Whitelist max. 19 B (fun 4), die Grenze bleibt als 2. Schranke.
TX_BELT_ADDRS = frozenset((0x21, 0x22))
TX_BELT_FRAME_MAX = 32             # Bytes gesamt (F7 addr len st fun .. crc)
TX_BELT_FUN_VERSION = 0x00
TX_BELT_FUN_READ_FLASH = 0x02
TX_BELT_FUN_WRITE_FLASH = 0x04
TX_BELT_FUN_ADC = 0x06
TX_BELT_FUN_MOVE = 0x08
TX_BELT_FLASH_NUM = 0x03           # einzig erlaubtes n bei fun 2/4
TX_BELT_FLASH_BYTES = 12           # fun 4: [03] + genau 12 Byte
TX_BELT_MOVE_MAX = 50              # fun 8: max. Weg je Befehl (Einheiten)
# Bootloader-Adressen (18.10.2026, Live-Gegenprobe zum Stock-Updateweg,
# B/L): Stock-mcu_util_485 vergibt im Boot per A0 Belt 0x23/
# 0x24 und Motor 0x85-0x88 (Bootloader), startet dort per F0 [02] die App;
# die App-Adressen sind 0x21/0x22 bzw. 0x81-0x84. Der Pi hat KEINEN
# Bootloader-Pfad (kein mcu_util_485-Ersatz; A0/A1 gehen an die Gruppen
# 0xFC/0xFD/0xFE, die Zieladresse steht nur in der Payload; auto_addr
# adressiert unicast nur CFS 0x01-0x04; Motor/MotorMini lehnen 0x85 ab;
# motor_init sendet nie an 0x85) -> im Betrieb JEDES fun an 0x23/0x24/
# 0x85-0x88 gesperrt. (CFS-/RFID-Bootloader liegen auf den App-Adressen
# 0x01/0x11 und sind über die fun-Regeln abgedeckt.)
TX_BOOTLOADER_ADDRS = frozenset((0x23, 0x24, 0x85, 0x86, 0x87, 0x88))
# RFID-Gerät 0x11 (18.10.2026, RFID-FW-Analyse, B): die App
# kennt nur 0x02 (Etikett-Cache lesen), 0x14 (Version) und 0x0B [00]
# (Reset in den Bootloader; ohne Payload wertet sie ein altes Pufferbyte
# aus). Whitelist: nur 0x02/0x14 OHNE Daten (filament_rack sendet
# `f7 11 03 ff 02 ..`); 0x0B und alles andere gesperrt. Die RFID-App nimmt
# auch Broadcast 0xFF an -> 0x02/0x14 nie an 0xFF/0xFD (Kollision der
# Antworten von RFID und anderen Geräten; Box nutzt 0x02/0x14 nur unicast).
TX_RFID_ADDR = 0x11
TX_RFID_ALLOWED_FUNS = frozenset((0x02, 0x14))
TX_BROADCAST_BLOCKED_FUNS = frozenset((0x02, 0x14))   # an 0xFF/0xFD
# Stand der Sendeschutz-Regeln (monoton steigende Zahl im Format JJJJMMTT;
# zweiter Regelstand am selben Tag = nächster Tag, hier 18.10.2026 Stufe 2
# -> 20261019). k2_transport nutzt serial485 nur, wenn dieser Stand >=
# seinem eigenen ist, sonst den lokalen Fallback.
TX_GUARD_REV = 20261027   # 26.10.2026 abends: Kalibrier-Einmalfreigabe
# Kalibrier-Einmalfreigabe (26.10.2026 abends, Regelstand 20261027): GENAU EIN
# Frame aus TX_CAL_FREIGABE_FRAMES an GENAU EINE Motor-App-Adresse 0x81-0x84
# darf die Motor-Sperre passieren – nur nach tx_kalibrier_freigabe() und
# höchstens TX_CAL_FREIGABE_MAX_S lang (Aufrufer: [k2_motor]
# KTWO_MOTOR_CAL_RUN/_RESTORE, die vorher alle Bedingungen prüfen und die
# Freigabe direkt nach der einen Sendung wieder löschen). Nicht freigebbar:
# 0xFD/0xFF/0x80/0x85-0x88, andere Payloads, 0x07 Sub 01/04/05, 0x02, 0x12.
TX_CAL_FREIGABE_ADDRS = frozenset((0x81, 0x82, 0x83, 0x84))
TX_CAL_FREIGABE_FRAMES = frozenset((
    (0x03, (0x00,)),        # encoder_calibrate_official
    (0x04, (0x00,)),        # elec_offset_calibrate
    (0x07, (0x03, 0x00)),   # flash_param: alle Parameter RAM -> Flash
))
TX_CAL_FREIGABE_MAX_S = 10.0

import logging
import threading
import time
from collections import deque

_cal_freigabe = None            # (addr, fun, data-Tupel, Ablauf monotonic)
_cal_freigabe_lock = threading.Lock()


def tx_kalibrier_freigabe(addr, fun, data, gueltig_s=5.0):
    """Erlaubt GENAU diesen Frame bis zum Ablauf (höchstens
    TX_CAL_FREIGABE_MAX_S) oder bis tx_kalibrier_freigabe_loeschen(). Nur
    Frames aus TX_CAL_FREIGABE_FRAMES an 0x81-0x84, sonst ValueError. Eine
    neue Freigabe ersetzt die alte."""
    global _cal_freigabe
    key = (int(fun), tuple(int(b) for b in data))
    if addr not in TX_CAL_FREIGABE_ADDRS or key not in TX_CAL_FREIGABE_FRAMES:
        raise ValueError("Kalibrier-Freigabe für addr 0x%02x fun 0x%02x [%s] nicht "
                         "zulässig" % (addr, fun, " ".join("%02x" % b for b in data)))
    gueltig_s = float(gueltig_s)
    if not 0.0 < gueltig_s <= TX_CAL_FREIGABE_MAX_S:
        raise ValueError("Kalibrier-Freigabe: Gültigkeit 0 < s <= %.0f"
                         % TX_CAL_FREIGABE_MAX_S)
    with _cal_freigabe_lock:
        _cal_freigabe = (int(addr), key[0], key[1], time.monotonic() + gueltig_s)


def tx_kalibrier_freigabe_loeschen():
    global _cal_freigabe
    with _cal_freigabe_lock:
        _cal_freigabe = None


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

_log = logging.getLogger(__name__)


def crc8_cal(data):
    """CRC-8/ATM (MSB-first, poly 0x07, init 0x00, kein xorout).

    1:1-Port von auto_addr_wrapper.py::crc8_cal. An 10+ Live-Frames verifiziert.
    """
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ POLY) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def build_frame(addr, fun, data=(), status=STATUS_OK):
    """Baut einen kompletten Sende-Frame: [F7][addr][len][status][fun][data][crc].

    len = len(data) + 3 (Beleg: auto_addr_wrapper.py::data_pack).
    CRC über [len, status, fun] + data (Beleg: cal_pack_crc).

    Neutraler Bau OHNE Sendeschutz (auch für Antwort-/Testframes bis
    len 255); Sendepfade nutzen build_tx_frame. len > 255 -> ValueError.
    """
    data = list(data)
    length = len(data) + 3
    if length > FRAME_LEN_MAX:
        raise ValueError("Frame zu lang: len %d > %d (%d Datenbytes)"
                         % (length, FRAME_LEN_MAX, len(data)))
    body = [length, status, fun] + data
    return bytes([PACK_HEAD, addr] + body + [crc8_cal(body)])


class TxGuardError(ValueError):
    """Sendeschutz verletzt (Längengrenze oder gesperrter Broadcast).
    Unterklasse von ValueError; es wurde NICHTS gesendet."""


def tx_data_max(fun):
    """Erlaubte Datenbytes eines Sende-Frames mit diesem fun (s. TX_*)."""
    return TX_DATA_MAX_UPDATE if fun == TX_FUNC_UPDATE else TX_DATA_MAX


def belt_move_dist(data):
    """fun-8-Payload [dir][dist BE32] -> (dir, dist) oder None bei falscher
    Länge (Belt-FW: Anfrage-Daten `[dir][dist 4]`, big-endian)."""
    data = list(data)
    if len(data) != 5:
        return None
    return data[0], int.from_bytes(bytes(data[1:5]), "big")


def _check_belt_frame(addr, fun, data, hexd):
    """Regel 6 (Belt 0x21/0x22), s. TX_BELT_ADDRS. Verstoß -> TxGuardError."""
    def _fail(why):
        raise TxGuardError(
            "Sendeschutz: Belt 0x%02x fun 0x%02x [%s%s] gesperrt – %s; "
            "nichts gesendet"
            % (addr, fun, hexd, " …" if len(data) > 8 else "", why))
    if len(data) + 6 > TX_BELT_FRAME_MAX:
        _fail("Frame %d B > %d B (Rx-Puffer der Belt-FW, Index ungeprüft)"
              % (len(data) + 6, TX_BELT_FRAME_MAX))
    if fun in (TX_BELT_FUN_VERSION, TX_BELT_FUN_ADC):
        if data:
            _fail("fun 0/6 nur ohne Daten")
    elif fun == TX_BELT_FUN_READ_FLASH:
        if data != [TX_BELT_FLASH_NUM]:
            _fail("fun 2 nur [03] (größeres n überläuft den RAM-Puffer)")
    elif fun == TX_BELT_FUN_WRITE_FLASH:
        if (len(data) != 1 + TX_BELT_FLASH_BYTES
                or data[0] != TX_BELT_FLASH_NUM):
            _fail("fun 4 nur [03] + genau 12 Byte (n != 3 löscht/verwirft "
                  "die Kalibrierung)")
    elif fun == TX_BELT_FUN_MOVE:
        mv = belt_move_dist(data)
        if mv is None or mv[0] not in (0, 1) \
                or not 1 <= mv[1] <= TX_BELT_MOVE_MAX:
            _fail("fun 8 nur [Richtung 0/1][Weg BE32 1..%d] (FW ohne Endlage)"
                  % TX_BELT_MOVE_MAX)
    else:
        _fail("Belt-FW kennt nur fun 0/2/4/6/8")


def check_tx_frame(addr, fun, data=()):
    """Sendeschutz für EINEN ausgehenden Frame (17.10.2026), ohne Bus-Zugriff.

    1. Länge: <= tx_data_max(fun) Datenbytes (100, bei fun 0xF0 0x80),
       Begründung s. TX_DATA_MAX; das len-Byte bleibt damit <= 0xFC.
    2. Broadcast: an 0xFF/0xFD nie fun 0x01 (reboot) und nie fun 0x0B
       (boot) – einzige Ausnahme 0xFF 0x0B [01] (CFS-Loader->App, live
       belegt; Motor-App ignoriert [01]). Belege s. TX_BOOT_GUARD_ADDRS.
    3. Motor (0x80-0x88, 0xFD): nie fun 0x03/0x04 (Kalibrierung), fun 0x07
       nur mit Sub 02/06 (lesend), nie fun 0x02 (Encoder-Register brennen),
       fun 0x12 nur [02] (UUID lesen, auch an 0xFF; 26.10.2026). Box/Belt/
       Rack unberührt. Belege s. TX_MOTOR_GUARD_ADDRS. Einzige Ausnahme:
       Kalibrier-Einmalfreigabe (tx_kalibrier_freigabe, 0x03 [00]/0x04 [00]/
       0x07 [03 00] an 0x81-0x84, ≤ 10 s).
    4. Update/Service (18.10.2026): nie an Adresse 0xF0, nie fun 0xF2;
       fun 0xF0 nur mit Payload exakt [00]/[02]/[03]; an 0xEA/0xEB nie
       fun 0x50/0x51/0x56. Belege s. TX_F0_ALLOWED_SUBS. Kein Override.
    5. Bootloader-Adressen (18.10.2026, Stufe 2): nie an 0x23/0x24 (Belt)
       und 0x85-0x88 (Motor). Belege s. TX_BOOTLOADER_ADDRS.
    6. Belt 0x21/0x22: Whitelist fun 0/6 ohne Daten, 2 nur [03], 4 nur
       [03]+12 B, 8 nur [dir 0/1][dist BE32 1..TX_BELT_MOVE_MAX]; Frame
       <= 32 B. Belege s. TX_BELT_ADDRS.
    7. RFID 0x11: nur fun 0x02/0x14 ohne Daten (0x0B = Bootloader-Reset
       gesperrt); 0x02/0x14 nie an 0xFF/0xFD. Belege s. TX_RFID_ADDR.
    Verstoß -> TxGuardError (ValueError). Rückgabe: Daten als Liste.
    """
    data = list(data)
    hexd = " ".join("%02x" % b for b in data[:8])
    if addr == TX_ADDR_EEPROM_SERVICE:
        raise TxGuardError(
            "Sendeschutz: Zieladresse 0xF0 fun 0x%02x gesperrt – Service-"
            "Adresse des CFS-Bootloaders (EEPROM-Dienst fun 0xF2, Schreiben "
            "mit Schlüssel), kein Gerät hat diese "
            "Adresse; nichts gesendet" % fun)
    if fun == TX_FUNC_EEPROM:
        raise TxGuardError(
            "Sendeschutz: addr 0x%02x fun 0xF2 (Bootloader-EEPROM-Dienst) "
            "gesperrt – schreibt das Board-EEPROM; nichts gesendet" % addr)
    if addr in TX_BOOTLOADER_ADDRS:
        raise TxGuardError(
            "Sendeschutz: Bootloader-Adresse 0x%02x fun 0x%02x gesperrt – "
            "0x23/0x24 = Belt-, 0x85-0x88 = Motor-Bootloader (nur im Stock-"
            "Update-Weg); der Pi hat keinen Bootloader-Pfad; "
            "nichts gesendet"
            % (addr, fun))
    if addr in TX_BELT_ADDRS:
        _check_belt_frame(addr, fun, data, hexd)
    if addr == TX_RFID_ADDR and not (
            fun in TX_RFID_ALLOWED_FUNS and not data):
        raise TxGuardError(
            "Sendeschutz: RFID 0x11 fun 0x%02x [%s] gesperrt – erlaubt nur "
            "0x02 (Etikett lesen) / 0x14 (Version) ohne Daten; 0x0B = Reset "
            "in den Bootloader; nichts gesendet"
            % (fun, hexd))
    if addr in TX_BOOT_GUARD_ADDRS and fun in TX_BROADCAST_BLOCKED_FUNS:
        raise TxGuardError(
            "Sendeschutz: Broadcast 0x%02x fun 0x%02x gesperrt – 0x02/0x14 "
            "beantwortet auch das RFID-Gerät 0x11 (Antwort-Kollision); "
            "nur unicast; nichts gesendet" % (addr, fun))
    if fun == TX_FUNC_UPDATE and not (
            len(data) == 1 and data[0] in TX_F0_ALLOWED_SUBS):
        raise TxGuardError(
            "Sendeschutz: addr 0x%02x fun 0xF0 [%s%s] gesperrt – erlaubt nur "
            "[00] Version, [02] App starten, [03] Sektorgröße; 01 Update-"
            "Request/Länge/Datenblöcke überschreiben die App, 06 löscht die "
            "CFS-DB-Page; nichts gesendet"
            % (addr, hexd, " …" if len(data) > 8 else ""))
    if addr in TX_CFS_SPECIAL_ADDRS and fun in TX_CFS_SPECIAL_BLOCKED_FUNCS:
        raise TxGuardError(
            "Sendeschutz: Sonderadresse 0x%02x fun 0x%02x gesperrt – 0x50 "
            "Config #6 in Flash + Reset, 0x51 Debug-Schalter, 0x56 Reset "
            "(Bootloader); träfe alle Boxen; nichts "
            "gesendet" % (addr, fun))
    limit = tx_data_max(fun)
    if len(data) > limit:
        raise TxGuardError(
            "Sendeschutz: addr 0x%02x fun 0x%02x mit %d Datenbytes (len %d) "
            "> Grenze %d – CFS-App puffert sauber nur 100 Datenbytes "
            "(Frame-Kontext 112 B), Bootloader verwirft len > 0xFC stumm, "
            "Update-Datenblöcke sind genau 0x80 B; nichts gesendet"
            % (addr, fun, len(data), len(data) + 3, limit))
    if len(data) + 3 > BOOTLOADER_LEN_MAX:  # Absicherung, falls TX_* geändert
        raise TxGuardError("Sendeschutz: len %d > 0xFC" % (len(data) + 3))
    if addr in TX_BOOT_GUARD_ADDRS:
        if fun == TX_FUNC_REBOOT:
            raise TxGuardError(
                "Sendeschutz: Broadcast 0x%02x fun 0x01 (reboot) gesperrt – "
                "startet ALLE Motoren neu (Motor-FW reboot FUN_f724, per 0xFF "
                "erreichbar); nichts gesendet" % addr)
        if fun == TX_FUNC_BOOT and not (
                addr == BROADCAST_ALL
                and tuple(data) == TX_LOADER_TO_APP_PAYLOAD):
            raise TxGuardError(
                "Sendeschutz: Broadcast 0x%02x fun 0x0B [%s] gesperrt – "
                "schickt ALLE Motoren in den Bootloader (Motor-FW boot "
                "FUN_104e6: Payload [00] -> boot_key 0x8567 + Watchdog-Reset; "
                "Haltemoment weg). Erlaubt ist nur 0xFF 0x0B [01] "
                "(CFS-Loader->App); nichts gesendet"
                % (addr, " ".join("%02x" % b for b in data)))
    if addr in TX_MOTOR_GUARD_ADDRS:
        if fun in TX_MOTOR_BLOCKED_FUNCS and _cal_freigabe_passt(addr, fun, data):
            return data                   # Kalibrier-Einmalfreigabe (s. oben)
        if fun in TX_MOTOR_BLOCKED_FUNCS:
            raise TxGuardError(
                "Sendeschutz: Motor 0x%02x fun 0x%02x (%s) gesperrt – "
                "open-loop-Bewegung ohne Schutz/Not-Aus, schreibt "
                "phase_order_invert/Encoder-EEPROM dauerhaft; "
                "nichts gesendet"
                % (addr, fun, "encoder_calibrate"
                   if fun == TX_FUNC_ENCODER_CALIBRATE
                   else "elec_offset_calibrate"))
        if fun == TX_FUNC_FLASH_PARAM and (
                not data or data[0] not in TX_FLASH_PARAM_READ_SUBS) \
                and not _cal_freigabe_passt(addr, fun, data):
            raise TxGuardError(
                "Sendeschutz: Motor 0x%02x fun 0x07 (flash_param) Sub [%s] "
                "gesperrt – erlaubt nur lesend Sub 02/06 (01 Werksreset, "
                "03 Flash schreiben, 04 Flash->RAM laden, 05 RAM schreiben); "
                "nichts gesendet"
                % (addr, " ".join("%02x" % b for b in data[:1])))
        if fun == TX_FUNC_ENCODER_REG_CHECK:
            raise TxGuardError(
                "Sendeschutz: Motor 0x%02x fun 0x02 (encoder_calibrate) "
                "gesperrt – brennt bei Abweichung die Encoder-Register ins "
                "EEPROM (Motor-FW-Analyse 25.10.2026); nichts gesendet"
                % addr)
    if (addr in TX_MOTOR_GUARD_ADDRS or addr == BROADCAST_ALL) \
            and fun == TX_FUNC_DEV_UUID and tuple(data) != TX_DEV_UUID_READ:
        raise TxGuardError(
            "Sendeschutz: addr 0x%02x fun 0x12 (dev_uuid) [%s] gesperrt – "
            "erlaubt nur [02] (UUID lesen); Sub 01 schreibt den "
            "Kalibrier-Record neu (Motor-FW-Analyse 25.10.2026); "
            "nichts gesendet" % (addr, hexd))
    return data


def build_tx_frame(addr, fun, data=(), status=STATUS_OK):
    """Sende-Frame = check_tx_frame (Länge + Broadcast-Schutz) + build_frame.
    Von allen Sendepfaden (transact*/raw_send*/Wrapper) genutzt."""
    return build_frame(addr, fun, check_tx_frame(addr, fun, data),
                       status=status)


def parse_frame(raw):
    """Prüft einen Empfangs-Frame, gibt (fun, daten) zurück (vgl. belt recv_sensor_data).

    Fehler -> ValueError mit Grund (head/länge/crc/fun).
    Erwartet mindestens [F7][addr][len][status][fun][crc].
    """
    if len(raw) < 6:
        raise ValueError("Frame zu kurz: %d Bytes" % len(raw))
    if raw[0] != PACK_HEAD:
        raise ValueError("head-Fehler: 0x%02x" % raw[0])
    length, status, fun = raw[2], raw[3], raw[4]
    if not FRAME_LEN_MIN <= length <= FRAME_LEN_MAX:
        raise ValueError("Längenfeld unplausibel: %d" % length)
    expect = length + 3  # head+addr+len (3) + length-Inhalt
    if len(raw) < expect:
        raise ValueError("unvollständig: %d < %d" % (len(raw), expect))
    body = list(raw[2:expect - 1])
    if crc8_cal(body) != raw[expect - 1]:
        raise ValueError("CRC-Fehler")
    return fun, bytes(raw[5:expect - 1])


def _scan_frame(buf):
    """Prüft den Pufferanfang auf EINEN Frame (Empfangs-Zustandsautomat).

    Gibt zurück:
      ("need", None)  – Frame-Anfang plausibel, aber noch unvollständig
                        -> weiterlesen (fragmentierte/lange Frames).
      ("bad", err)    – falscher Head, unplausibles Längenfeld oder
                        CRC-Fehler -> resynchronisieren.
      ("ok", (expect, addr, status, fun, daten)) – gültiger Frame der
                        Länge expect am Pufferanfang.
    """
    if not buf:
        return "need", None
    if buf[0] != PACK_HEAD:
        return "bad", ValueError("head-Fehler: 0x%02x" % buf[0])
    if len(buf) < 3:
        return "need", None
    length = buf[2]
    if not FRAME_LEN_MIN <= length <= FRAME_LEN_MAX:
        return "bad", ValueError("Längenfeld unplausibel: %d" % length)
    expect = length + 3
    if len(buf) < expect:
        return "need", None
    raw = bytes(buf[:expect])
    try:
        fun, data = parse_frame(raw)
    except ValueError as e:
        return "bad", e
    return "ok", (expect, raw[1], raw[3], fun, data)


def _resync(buf):
    """Verwirft Bytes bis zum nächsten PACK_HEAD (vgl. Review: Resync).

    Verhindert, dass Müll oder halbe Frames den Empfangspuffer bis zum
    Timeout verstopfen. Gibt den Restpuffer ab dem nächsten Head zurück.
    """
    for i in range(1, len(buf)):
        if buf[i] == PACK_HEAD:
            return buf[i:]
    return bytearray()


class Serial485:
    """RS485-Bus über pyserial. Entspricht Serialhdl_485/raw_send_wait_ack.

    Session-Schicht nach serial_485_wrapper.so-Symbolen (Beleg: strings-Dump
    vom 13.10.2026):
    raw_send/raw_send_wait_ack/connect_uart/_start_session/_bg_thread/
    get_response/register_response/handle_default/disconnect/stats.

    Halbduplex: genau ein Frame gleichzeitig auf dem Bus, alles unter
    self._lock (threading.Lock, Typ unverändert für Alttests).
    Statistik: sent/received/timeouts/retries/crc_errors (vgl.
    serial_485_queue_get_stats im .so).
    """

    def __init__(self, port, baud=230400, timeout=TIMEOUT_LONG, ser=None):
        self.port = port
        self.baud = baud
        self.default_timeout = timeout
        self._lock = threading.Lock()
        self._stats_lock = threading.Lock()
        self._stats = {"sent": 0, "received": 0, "timeouts": 0,
                       "retries": 0, "crc_errors": 0}
        self._handlers = {}          # (addr, fun) -> callback; None = Wildcard
        self._default_handler = None
        self._connected = False
        self._session_active = False
        self._injected = ser is not None
        self._bg_stop = threading.Event()
        self._bg_thread_obj = None
        self._rest_buf = b""
        # Fix B.2: Antwort-Warteschlange + Erwartungs-Registry (alles unter
        # _lock verwaltet – kein extra Lock nötig, alle Zugriffe halten _lock).
        self._resp_queue = deque()      # [(addr,status,fun,data,raw,ts)]
        self._pending_expect = deque()  # [(addr|None, fun|None, deadline)]
        if ser is not None:
            self.ser = ser            # injiziert (Tests), kein pyserial nötig
            self._connected = True
        else:
            try:
                import serial
            except ImportError as e:
                raise SystemExit("pyserial fehlt: pip install pyserial") from e
            self.ser = serial.Serial(port, baud, timeout=0.02)
            self._connected = True

    # --- interne Helfer ---
    def _inc(self, key, n=1):
        with self._stats_lock:
            self._stats[key] = self._stats.get(key, 0) + n

    def _dispatch(self, addr, status, fun, data):
        """Ruft registrierten Handler für (addr, fun), sonst Default."""
        cb = self._handlers.get((addr, fun))
        if cb is None:
            cb = self._handlers.get((addr, None))
        if cb is None:
            cb = self._handlers.get((None, fun))
        if cb is None:
            cb = self._default_handler
        if cb is None:
            return None
        return cb(addr, status, fun, data)

    def _dispatch_safe(self, addr, status, fun, data):
        """_dispatch ohne Exception-Propagation (Handler-Fehler werden
        geloggt, der Busverkehr läuft weiter). Aufruf NUR ohne gehaltenes
        _lock (Handler dürfen selbst Bus-Funktionen aufrufen)."""
        try:
            self._dispatch(addr, status, fun, data)
        except Exception:
            _log.exception("Bus-Handler für addr 0x%02x fun 0x%02x fehlgeschlagen",
                           addr, fun)

    # --- Fix B.2-Helfer (alles NUR unter gehaltenem _lock aufrufen) --------
    @staticmethod
    def _match_expect(exp_addr, exp_fun, addr, fun):
        if exp_fun is not None and fun != exp_fun:
            return False
        if exp_addr is not None and addr != exp_addr:
            return False
        return True

    def _prune_expect_queue_locked(self, now):
        """Abgelaufene Erwartungen + alte Queue-Einträge verwerfen (unter _lock)."""
        while self._pending_expect and self._pending_expect[0][2] < now:
            self._pending_expect.popleft()
        # Nicht von vorne (deadlines monoton, aber sicherheitshalber filtern):
        self._pending_expect = deque(
            e for e in self._pending_expect if e[2] >= now)
        while len(self._resp_queue) > RESP_QUEUE_MAX:
            self._resp_queue.popleft()
        cutoff = now - RESP_QUEUE_TTL_S
        self._resp_queue = deque(
            e for e in self._resp_queue if e[5] >= cutoff)

    def _take_queued_locked(self, expect_fun, expect_addr):
        """Ersten passenden Queue-Eintrag entnehmen oder None (unter _lock)."""
        for i, (a, s, f, d, r, _ts) in enumerate(self._resp_queue):
            if self._match_expect(expect_addr, expect_fun, a, f):
                del self._resp_queue[i]
                return a, s, f, d, r
        return None

    def _recv_frame_locked(self, deadline, expect_fun=None, expect_addr=None):
        """EIN gültiger Frame unter gehaltenem _lock. Gibt
        (addr, status, fun, daten, rohframe) zurück. Zählt crc_errors.
        Wirft TimeoutError bei abgelaufener Deadline.

        Ablauf: Puffer (Überhang + gelesene Chunks) wird VOLLSTÄNDIG
        abgearbeitet, bevor neu gelesen wird:
          - unvollständiger Frame-Anfang -> weiterlesen (Fragmente, >64 Byte)
          - falscher Head/unplausibles len/CRC-Fehler -> _resync (nächster 0xF7)
          - gültiger, aber nicht gewollter Frame (fun/addr-Mismatch, z.B.
            Herzschlag) -> als GANZES überspringen (Daten dürfen 0xF7
            enthalten, z.B. Belt-Flash); nicht dispatcht (wie bisher)
        Bei Deadline mit unvollständigem Rest wird hinter dem vermeintlichen
        Head nach einem kompletten Frame gesucht (Müll-Head, der einen echten
        Frame "verschluckt"). Findet sich keiner, bleibt der Rest als
        Überhang erhalten (get_response/_bg_thread können ihn vervollständigen;
        transact leert ihn ohnehin vor jedem Senden).
        """
        buf = bytearray(getattr(self, "_rest_buf", b"") or b"")
        self._rest_buf = b""
        last_err = None
        final = False          # Deadline erreicht: nur noch Lookahead
        pending = b""          # unvollständiger Rest zum Deadline-Zeitpunkt
        found_any = False      # Lookahead hat einen gültigen Frame gefunden
        while True:
            while buf:
                state, val = _scan_frame(buf)
                if state == "need":
                    if not final:
                        break
                    buf = _resync(buf)
                    continue
                if state == "bad":
                    if not final:  # Lookahead-Treffer nicht als Busfehler zählen
                        if "CRC" in str(val):
                            self._inc("crc_errors")
                        last_err = val
                    buf = _resync(buf)
                    continue
                expect, addr, status, rfun, rdata = val
                raw = bytes(buf[:expect])
                if final:
                    found_any = True
                if expect_fun is not None and rfun != expect_fun:
                    last_err = ValueError("fun-Mismatch: 0x%02x" % rfun)
                    del buf[:expect]
                    continue
                if expect_addr is not None and addr != expect_addr:
                    last_err = ValueError("addr-Mismatch: 0x%02x" % addr)
                    del buf[:expect]
                    continue
                self._rest_buf = bytes(buf[expect:])  # Überhang (Folgeframe)
                return addr, status, rfun, rdata, raw
            if final:
                break
            if time.monotonic() >= deadline:
                final = True
                pending = bytes(buf)
                continue
            chunk = self.ser.read(64)
            if chunk:
                buf += chunk
        if not found_any and pending:
            self._rest_buf = pending
        raise TimeoutError(last_err or "keine Antwort")

    def _transact_locked(self, addr, fun, data, timeout, retries,
                         expect_fun, status, want_full=False,
                         expect_addr=_AUTO):
        """Kern unter gehaltenem _lock. want_full -> (addr,status,fun,daten,roh),
        sonst nur daten. Zählt sent/retries/received/timeouts.

        expect_addr (Review M1): Default = reply_addr_for(addr), d.h. nur
        Frames mit Absender == Ziel gelten als Antwort (Broadcast-Ziele:
        beliebig). Fremde Frames mit passendem fun werden übersprungen.
        Explizit None = beliebiger Absender, int = genau dieser Absender.

        Dispatcht NICHT selbst: der Aufrufer ruft _dispatch_safe erst nach
        Freigabe von _lock (sonst Deadlock, wenn ein Handler Bus-Funktionen
        aufruft – threading.Lock ist nicht reentrant)."""
        expect_fun = fun if expect_fun is None else expect_fun
        if expect_addr is _AUTO:
            expect_addr = reply_addr_for(addr)
        frame = build_tx_frame(addr, fun, data, status=status)  # Sendeschutz
        last_err = None
        # TODO: am klippy.log nachzählen, ob 4 Versuche oder 1+4
        # (aktuell: retries=4 -> 1 Erstsendung + 4 Wiederholungen = 5 Sendungen).
        for attempt in range(retries + 1):
            if attempt > 0:
                self._inc("retries")
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass
            self._rest_buf = b""  # reset verwirft stale Bytes (Live-Verhalten)
            self.ser.write(frame)
            self._inc("sent")
            deadline = time.monotonic() + timeout
            try:
                raddr, rstatus, rfun, rdata, rraw = self._recv_frame_locked(
                    deadline, expect_fun=expect_fun, expect_addr=expect_addr)
            except TimeoutError as e:
                last_err = e
                continue
            self._inc("received")
            if want_full:
                return raddr, rstatus, rfun, rdata, rraw
            return rdata
        self._inc("timeouts")
        raise TimeoutError("addr 0x%02x fun 0x%02x: %s" % (addr, fun, last_err))

    # --- Belegnamen Serialhdl_485 ---
    def connect_uart(self, port=None, baud=None, timeout=None):
        """Öffnet (erneut) den UART (Beleg: Serialhdl_485.connect_uart)."""
        with self._lock:
            if port is not None:
                self.port = port
                self._injected = False
            if baud is not None:
                self.baud = baud
            if timeout is not None:
                self.default_timeout = timeout
            if self._injected and port is None:
                self._connected = True  # injizierter Test-Port bleibt
                return True
            try:
                old = getattr(self, "ser", None)
                if old is not None and self._connected:
                    try:
                        if hasattr(old, "close"):
                            old.close()
                    except Exception:
                        pass
            finally:
                self._connected = False
            try:
                import serial
            except ImportError as e:
                raise SystemExit("pyserial fehlt: pip install pyserial") from e
            self.ser = serial.Serial(self.port, self.baud, timeout=0.02)
            self._injected = False
            self._connected = True
            return True

    def disconnect(self):
        """Schließt Port + stoppt Hintergrund-Leser
        (Beleg: Serialhdl_485.disconnect)."""
        self._stop_bg()
        with self._lock:
            try:
                if (getattr(self, "ser", None) is not None
                        and not getattr(self, "_injected", False)):
                    try:
                        if hasattr(self.ser, "close"):
                            self.ser.close()
                    except Exception:
                        pass
            finally:
                self._connected = False
                self._session_active = False

    def _start_session(self, start_bg=False, bg_timeout=0.2):
        """Explizite Session-Eröffnung (Beleg: Serialhdl_485._start_session).

        Stellt sicher, dass der Port offen ist, leert den Empfangspuffer
        (Port + interner Überhang _rest_buf) und markiert die Session aktiv. Idempotent. Mit start_bg=True wird der
        Hintergrund-Leser (_bg_thread) als Daemon gestartet.
        """
        if not getattr(self, "_connected", False) or getattr(self, "ser", None) is None:
            self.connect_uart()
        else:
            try:
                self.ser.reset_input_buffer()
            except Exception:
                pass
        self._rest_buf = b""
        self._session_active = True
        if start_bg:
            self._start_bg(timeout=bg_timeout)
        return True

    def _start_bg(self, timeout=0.2):
        if self._bg_thread_obj and self._bg_thread_obj.is_alive():
            return
        self._bg_stop.clear()
        self._bg_thread_obj = threading.Thread(
            target=self._bg_thread, kwargs={"timeout": timeout}, daemon=True)
        self._bg_thread_obj.start()

    def _stop_bg(self):
        self._bg_stop.set()
        obj = getattr(self, "_bg_thread_obj", None)
        if obj and obj.is_alive() and obj is not threading.current_thread():
            obj.join(timeout=0.5)
        self._bg_thread_obj = None

    def _bg_thread(self, timeout=0.2):
        """Hintergrund-Leser (Beleg: Serialhdl_485._bg_thread).

        Pollt unsolicited Frames (z.B. Herzschlag 0x0a, Events mit
        status 0x30/0x16) und dispatchet sie an die Registry. Synchronous
        transact-Verkehr läuft weiter über _lock (Halbduplex-Serialisierung).
        Nur aktiv nach _start_session(start_bg=True); disconnect() stoppt.

        Fix B.2: Passt ein Frame zu einer per raw_send registrierten
        Erwartung, wird er NICHT dispatcht, sondern in _resp_queue geparkt –
        get_response holt ihn dort ab (kein Wegschnappen mehr).
        """
        while not self._bg_stop.wait(0.02):
            if not getattr(self, "_connected", False):
                continue
            try:
                with self._lock:
                    deadline = time.monotonic() + timeout
                    try:
                        raddr, rstatus, rfun, rdata, rraw = \
                            self._recv_frame_locked(deadline)
                    except TimeoutError:
                        continue
                    self._inc("received")
                    now = time.monotonic()
                    self._prune_expect_queue_locked(now)
                    parked = False
                    for exp_addr, exp_fun, _dl in list(self._pending_expect):
                        if self._match_expect(exp_addr, exp_fun,
                                              raddr, rfun):
                            self._pending_expect.remove(
                                (exp_addr, exp_fun, _dl))
                            self._resp_queue.append(
                                (raddr, rstatus, rfun, rdata, rraw, now))
                            parked = True
                            break
                    if parked:
                        continue
                # außerhalb von _lock (Handler dürfen Bus-Funktionen rufen)
                self._dispatch_safe(raddr, rstatus, rfun, rdata)
            except Exception:
                _log.exception("_bg_thread: Fehler beim Empfang")
                continue

    def raw_send(self, addr, fun, data=(), status=STATUS_OK,
                 expect_response=True, expect_timeout=None, expect_fun=None,
                 expect_addr=_AUTO):
        """Fire-and-forget-Senden (Beleg: Serialhdl_485.raw_send).

        Fix B.2: Mit expect_response=True (Default) wird atomar mit dem
        Schreiben eine erwartete Antwort registriert – der Hintergrund-Thread
        parkt sie dann in _resp_queue statt sie zu dispatchen, sodass ein
        anschließendes get_response sie auch bei Lücke sicher findet.
        Wer wirklich ohne Antwort sendet, übergibt expect_response=False
        (dann Verhalten wie bisher). expect_fun/expect_addr schränken die
        Erwartung ein (Default: gleicher fun, Absender wie Ziel bzw.
        beliebig bei Broadcast-Zielen).
        Sendeschutz (check_tx_frame): zu lange Frames und gesperrte
        Broadcasts -> TxGuardError, nichts gesendet.
        """
        frame = build_tx_frame(addr, fun, data, status=status)
        with self._lock:
            self.ser.write(frame)
            self._inc("sent")
            if expect_response:
                efun = fun if expect_fun is None else expect_fun
                if expect_addr is _AUTO:
                    eaddr = reply_addr_for(addr)
                else:
                    eaddr = expect_addr
                ttl = (self.default_timeout if expect_timeout is None
                       else expect_timeout)
                try:
                    ttl = float(ttl)
                except Exception:
                    ttl = BG_EXPECT_TTL_S
                if ttl <= 0:
                    ttl = BG_EXPECT_TTL_S
                now = time.monotonic()
                self._prune_expect_queue_locked(now)
                self._pending_expect.append((eaddr, efun, now + ttl))
        return frame

    def raw_send_wait_ack(self, addr, fun, data=(), timeout=None,
                          retries=MAX_RETRIES, expect_fun=None,
                          status=STATUS_OK, expect_addr=_AUTO):
        """Sendet, wartet auf Antwort mit passendem fun-Code UND passendem
        Absender (expect_addr, s. _transact_locked)
        (Beleg: Serialhdl_485.raw_send_wait_ack). Gibt Antwort-Daten zurück."""
        full = self._send_wait_full_nodispatch(addr, fun, data, timeout,
                                               retries, expect_fun, status,
                                               expect_addr=expect_addr)
        self._dispatch_safe(*full[:4])  # nach Freigabe von _lock
        return full[3]

    def raw_send_wait_ack_full(self, addr, fun, data=(), timeout=None,
                               retries=MAX_RETRIES, expect_fun=None,
                               status=STATUS_OK, expect_addr=_AUTO):
        """Wie raw_send_wait_ack, gibt aber (addr, status, fun, daten, rohframe)
        zurück (für Wrapper-Vollframes, vgl. auto_addr data_handler)."""
        full = self._send_wait_full_nodispatch(addr, fun, data, timeout,
                                               retries, expect_fun, status,
                                               expect_addr=expect_addr)
        self._dispatch_safe(*full[:4])  # nach Freigabe von _lock
        return full

    @staticmethod
    def _check_hold(timeout, retries):
        """Review M6: Haltedauer des Bus-Locks begrenzen (MAX_BUS_HOLD_S)."""
        if timeout < 0 or retries < 0:
            raise ValueError("timeout/retries dürfen nicht negativ sein: "
                             "%r/%r" % (timeout, retries))
        hold = timeout * (retries + 1)
        if hold > MAX_BUS_HOLD_S:
            raise ValueError(
                "Transaktion würde den Bus bis %.0f s blockieren "
                "(timeout %.1f s x %d Sendungen) > MAX_BUS_HOLD_S %.0f s"
                % (hold, timeout, retries + 1, MAX_BUS_HOLD_S))
        return hold

    def _send_wait_full_nodispatch(self, addr, fun, data=(), timeout=None,
                                   retries=MAX_RETRIES, expect_fun=None,
                                   status=STATUS_OK, expect_addr=_AUTO):
        """Intern: wie raw_send_wait_ack_full, aber OHNE Dispatch. Der
        Aufrufer muss _dispatch_safe(*full[:4]) selbst (ohne Locks) aufrufen
        (genutzt vom Wrapper, der unter _send_lock sendet).

        BUS-LOCK (Review M6, Entscheidung 16.10.2026): _lock bleibt für die
        GANZE Transaktion gehalten (Senden bis Antwort/Timeout), auch bei
        langen Kommandos (0x0d op 2/3 bis 1200 s, 0x11 bis 150 s). Andere
        Threads (Motoren, 0x0a-Poll, _bg_thread) warten solange. Gründe:
        (1) RS485 halbduplex: der Slave antwortet asynchron am Ende seines
            Vorgangs; sendet der Host in dieser Zeit, kollidieren die Frames
            (keine Arbitrierung, beide Frames kaputt -> Antwort verloren,
            bei retries=0 nicht wiederholbar).
        (2) Original genauso: live sendet der Host während 0x0d [01 02]
            (31-37 s) nichts, auch keine 0x0a-Polls (Live-Log
            13.10.2026).
        (3) Box-FW bearbeitet 0x0d op 2/3 synchron und ist solange stumm
            (live 0x04 5x unbeantwortet, 13:130637-130652).
        Absicherung: Haltedauer timeout*(retries+1) <= MAX_BUS_HOLD_S, sonst
        ValueError; ab LONG_HOLD_LOG_S INFO-Logzeile. Sendeschutz
        (check_tx_frame, 17.10.2026) VOR dem Lock: TxGuardError, nichts
        gesendet.
        TODO (M6): Pending-Registry (Senden unter Lock, Warten ohne Lock)
          wäre nur für Geräte sinnvoll, die während einer laufenden Box-
          Operation sicher senden dürfen – dafür fehlt ein Kollisions-Beleg;
          0x10 mode 4 (Box liest den Bus selbst, FW-Analyse) zeigt, dass die
          Box kurze Fragen während des Förderns beantwortet, aber nicht,
          ob fremde Slaves dazwischen senden dürfen.
        """
        timeout = self.default_timeout if timeout is None else timeout
        data = check_tx_frame(addr, fun, data)  # Sendeschutz vor dem Lock
        hold = self._check_hold(timeout, retries)
        if hold >= LONG_HOLD_LOG_S:
            _log.info("Bus-Lock bis %.0f s für addr 0x%02x fun 0x%02x",
                      hold, addr, fun)
        with self._lock:
            return self._transact_locked(addr, fun, list(data), timeout,
                                         retries, expect_fun, status,
                                         want_full=True,
                                         expect_addr=expect_addr)

    def get_response(self, timeout=None, expect_fun=None, expect_addr=None):
        """Liest EINEN gültigen Frame (Beleg: Serialhdl_485.get_response).

        Gibt (addr, status, fun, daten) zurück (daten als bytes).
        Rohframe = build_frame(addr, fun, daten, status=status).
        Wirft TimeoutError nach timeout, zählt crc_errors/received.

        Fix B.2: Zuerst wird _resp_queue geprüft (vom Hintergrund-Thread
        geparkte erwartete Antworten aus der raw_send/get_response-Lücke) –
        erst danach vom seriellen Port gelesen.
        """
        timeout = self.default_timeout if timeout is None else timeout
        with self._lock:
            self._prune_expect_queue_locked(time.monotonic())
            queued = self._take_queued_locked(expect_fun, expect_addr)
            if queued is not None:
                raddr, rstatus, rfun, rdata, _rraw = queued
            else:
                deadline = time.monotonic() + timeout
                try:
                    raddr, rstatus, rfun, rdata, _ = self._recv_frame_locked(
                        deadline, expect_fun=expect_fun, expect_addr=expect_addr)
                except TimeoutError:
                    self._inc("timeouts")
                    raise
                self._inc("received")
        self._dispatch_safe(raddr, rstatus, rfun, rdata)  # ohne _lock
        return raddr, rstatus, rfun, rdata

    def register_response(self, addr, fun, callback):
        """Registriert Antwort-Handler für (addr, fun)
        (Beleg: Serialhdl_485.register_response). None = Wildcard.
        callback(addr, status, fun, daten). callback=None löscht.

        Handler laufen NACH Freigabe des Bus-Locks (im Thread des Aufrufers
        von transact/raw_send_wait_ack/get_response bzw. im _bg_thread) und
        dürfen daher selbst Bus-Funktionen aufrufen. Exceptions im Handler
        werden geloggt (logging), nicht propagiert. Nur die Antwort, auf die
        gewartet wurde, wird dispatcht; übersprungene Fremd-Frames nicht."""
        with self._lock:
            if callback is None:
                self._handlers.pop((addr, fun), None)
            else:
                self._handlers[(addr, fun)] = callback

    def handle_default(self, addr, status, fun, data):
        """Default-Handler (Beleg: Serialhdl_485.handle_default). No-op."""
        return None

    def stats(self):
        """Zählerstand (Beleg: Serialhdl_485.stats / serial_485_queue_get_stats):
        {sent, received, timeouts, retries, crc_errors}."""
        with self._stats_lock:
            return dict(self._stats)

    def transact(self, addr, fun, data=(), timeout=None, retries=MAX_RETRIES,
                 expect_fun=None, status=STATUS_OK, expect_addr=_AUTO):
        """Sendet Frame, wartet auf Antwort mit passendem fun-Code und
        Absender (vgl. raw_send_wait_ack; expect_addr s. _transact_locked).

        Gibt die Antwort-Daten (bytes) zurück, wirft TimeoutError nach retries.
        Das Status-Byte der Antwort geht hier verloren -> transact_full.
        Der Empfangspuffer wird auf PACK_HEAD resynchronisiert, damit Müll oder
        halbe Frames vom Bus den Parser nicht blockieren.
        Bus-Lock für die ganze Dauer, begrenzt durch MAX_BUS_HOLD_S
        (s. _send_wait_full_nodispatch).
        """
        return self.raw_send_wait_ack(addr, fun, data, timeout=timeout,
                                      retries=retries, expect_fun=expect_fun,
                                      status=status, expect_addr=expect_addr)

    def transact_full(self, addr, fun, data=(), timeout=None,
                      retries=MAX_RETRIES, expect_fun=None, status=STATUS_OK,
                      expect_addr=_AUTO):
        """Wie transact, gibt aber (addr, status, fun, daten, rohframe)
        zurück – mit dem Status-Byte der Antwort (Review M2, 16.10.2026;
        = raw_send_wait_ack_full mit transact-Signatur)."""
        return self.raw_send_wait_ack_full(addr, fun, data, timeout=timeout,
                                           retries=retries,
                                           expect_fun=expect_fun,
                                           status=status,
                                           expect_addr=expect_addr)


class Serial_485_Wrapper:
    """High-Level-Wrapper mit Sende-Queue + Response-Registry.

    Entspricht Serial_485_Wrapper (Beleg: serial_485_wrapper.so-Symbole,
    strings-Dump 13.10.2026):
    cmd_send_data_with_response/cmd_485_send_data/add_send_data/
    remove_send_data/send_queue_process/register_response/
    is_exist_in_send_list/handle_callback (+ _connect/_disconnect/stats).

    Wire-Format wie im Live-Betrieb (Beleg: auto_addr_wrapper.py
    send_package + belt_mdl.py send_data): data_send = [addr][len][status]
    [fun][daten...] OHNE Head und OHNE CRC (CRC ergänzt der Treiber beim
    Senden). Rückgabe von cmd_send_data_with_response ist der VOLLE
    Antwort-Frame [F7][addr][len][status][fun][daten][crc] (Beleg:
    auto_addr data_handler ret[0]=head ... ret[-1]=crc) oder None bei
    Timeout (Beleg: send_package "Error: no response").

    Halbduplex: send_queue_process arbeitet FIFO ab, ein Eintrag gleichzeitig;
    Queue-Zugriffe sind Lock-geschützt, der Bus selbst über Serial485._lock.
    """

    def __init__(self, port="/dev/ttyS5", baud=230400, timeout=TIMEOUT_LONG,
                 bus=None, ser=None):
        if bus is not None:
            self.bus = bus
        elif isinstance(port, Serial485):
            self.bus = port
        elif ser is not None:
            self.bus = Serial485(port, baud, timeout, ser=ser)
        else:
            self.bus = Serial485(port, baud, timeout)
        self._queue = deque()
        self._qlock = threading.Lock()
        self._send_lock = threading.Lock()  # genau ein Drain gleichzeitig
        self._handlers = {}          # (addr, fun) -> callback(data_send, msg)
        self._default_handler = None
        self._done = {}              # Eintrags-ID -> Antwort (für Neben-Threads)
        self._done_time = {}         # Eintrags-ID -> monotonic-ts (Fix B.1)
        self._seq = 0

    # --- Fix B.1-Helfer (NUR unter gehaltenem _qlock aufrufen) --------------
    def _store_done_locked(self, eid, resp):
        """Antwort ablegen + verwaiste/überzählige Einträge aufräumen."""
        now = time.monotonic()
        self._done[eid] = resp
        self._done_time[eid] = now
        cutoff = now - WRAPPER_DONE_TTL_S
        for old in [k for k, t in list(self._done_time.items())
                    if t < cutoff]:
            self._done.pop(old, None)
            self._done_time.pop(old, None)
        while len(self._done) > WRAPPER_DONE_MAX:
            oldest = min(self._done)
            self._done.pop(oldest, None)
            self._done_time.pop(oldest, None)

    def _take_done_locked(self, eid, default=None):
        """Antwort abholen (verfallene gilt als fehlend)."""
        now = time.monotonic()
        t = self._done_time.get(eid)
        if t is not None and now - t > WRAPPER_DONE_TTL_S:
            self._done.pop(eid, None)
            self._done_time.pop(eid, None)
            return default
        self._done_time.pop(eid, None)
        return self._done.pop(eid, default)

    def cleanup_done(self):
        """Verwaiste _done-Einträge aufräumen (öffentlich, z.B. zyklisch).

        Gibt (entfernt, übrig) zurück.
        """
        with self._qlock:
            now = time.monotonic()
            cutoff = now - WRAPPER_DONE_TTL_S
            old = [k for k, t in list(self._done_time.items()) if t < cutoff]
            for k in old:
                self._done.pop(k, None)
                self._done_time.pop(k, None)
            while len(self._done) > WRAPPER_DONE_MAX:
                oldest = min(self._done)
                self._done.pop(oldest, None)
                self._done_time.pop(oldest, None)
                old.append(oldest)
            return len(old), len(self._done)

    # --- Queue (Belegnamen) ---
    def add_send_data(self, data, timeout=TIMEOUT_LONG, retransmit=True):
        """Reiht Wire-Bytes ein, gibt Eintrags-ID zurück
        (Beleg: Serial_485_Wrapper.add_send_data).

        Sendeschutz (check_tx_frame, 17.10.2026) schon beim Einreihen:
        gesperrte Broadcasts / zu lange Frames -> TxGuardError, der Eintrag
        kommt nie in die Queue (sonst bräche send_queue_process mittendrin
        ab). Erlaubt bleibt u. a. auto_addr 0xFF 0x0B [01]."""
        wire = bytes(data)
        if len(wire) >= 4:
            check_tx_frame(wire[0], wire[3], wire[4:])
        with self._qlock:
            self._seq += 1
            entry = {"id": self._seq, "data": bytes(data),
                     "timeout": timeout, "retransmit": bool(retransmit)}
            self._queue.append(entry)
            return entry["id"]

    def remove_send_data(self, ident):
        """Entfernt Eintrag per ID oder per Wire-Bytes. Gibt True/False
        (Beleg: Serial_485_Wrapper.remove_send_data)."""
        with self._qlock:
            if isinstance(ident, (bytes, bytearray, list)):
                want = bytes(ident)
                for i, e in enumerate(self._queue):
                    if e["data"] == want:
                        del self._queue[i]
                        return True
                return False
            for i, e in enumerate(self._queue):
                if e["id"] == ident:
                    del self._queue[i]
                    return True
            return False

    def is_exist_in_send_list(self, data):
        """True wenn Wire-Bytes bereits in der Queue
        (Beleg: Serial_485_Wrapper.is_exist_in_send_list)."""
        want = bytes(data)
        with self._qlock:
            return any(e["data"] == want for e in self._queue)

    @staticmethod
    def _split_wire(data_send):
        wire = bytes(data_send)
        if len(wire) < 4:
            raise ValueError("data_send zu kurz: %d" % len(wire))
        return wire[0], wire[1], wire[2], wire[3], wire[4:]

    def _send_entry(self, entry, deferred):
        """Sendet EINEN Queue-Eintrag, gibt Vollframe oder None (Timeout).

        Dispatch (Bus-Registry + handle_callback) wird NICHT hier ausgeführt,
        sondern als (eintrag, vollantwort) an deferred angehängt; der
        Aufrufer führt ihn nach Freigabe von _send_lock aus (_run_deferred),
        damit Handler selbst senden dürfen ohne Deadlock."""
        addr, _length, status, fun, payload = self._split_wire(entry["data"])
        retries = MAX_RETRIES if entry["retransmit"] else 0
        try:
            full = self.bus._send_wait_full_nodispatch(
                addr, fun, list(payload), timeout=entry["timeout"],
                retries=retries, expect_fun=fun, status=status)
        except TimeoutError:
            return None
        deferred.append((entry["data"], full))
        return full[4]

    def _run_deferred(self, deferred):
        """Führt gesammelte Dispatches aus (ohne gehaltene Locks), in
        Sende-Reihenfolge: erst Bus-Registry, dann Wrapper-Registry."""
        for data_send, full in deferred:
            self.bus._dispatch_safe(*full[:4])
            try:
                self.handle_callback(data_send, full[4])
            except Exception:
                _log.exception("Wrapper handle_callback fehlgeschlagen")

    def send_queue_process(self):
        """Arbeitet die Queue FIFO ab, ein Eintrag gleichzeitig
        (Beleg: Serial_485_Wrapper.send_queue_process). Gibt
        [(eintrags-id, antwort-vollframe-oder-None)] in Reihenfolge zurück."""
        results = []
        deferred = []
        with self._send_lock:
            while True:
                with self._qlock:
                    if not self._queue:
                        break
                    entry = self._queue.popleft()
                resp = self._send_entry(entry, deferred)
                with self._qlock:
                    self._store_done_locked(entry["id"], resp)
                results.append((entry["id"], resp))
        # Handler erst nach Freigabe von _send_lock (dürfen selbst senden)
        self._run_deferred(deferred)
        return results

    # --- Sende-Kommandos (Belegnamen) ---
    def cmd_send_data_with_response(self, data, timeout=1.0, retransmit=True):
        """Sendet Wire-Bytes, wartet auf Antwort-Vollframe
        (Beleg: Serial_485_Wrapper.cmd_send_data_with_response).

        Gibt den VOLLEN Antwort-Frame (bytes, inkl. Head+CRC) zurück oder
        None bei Timeout (vgl. auto_addr send_package). FIFO-fair: vorher
        eingereihte Einträge werden zuerst gesendet.
        """
        eid = self.add_send_data(data, timeout=timeout, retransmit=retransmit)
        deferred = []
        result = None
        with self._send_lock:
            with self._qlock:
                done = eid in self._done  # Neben-Thread hat ihn schon erledigt
                if done:
                    result = self._take_done_locked(eid)
            while not done:
                with self._qlock:
                    if not self._queue:
                        result = self._take_done_locked(eid, None)
                        break
                    entry = self._queue.popleft()
                resp = self._send_entry(entry, deferred)
                with self._qlock:
                    self._store_done_locked(entry["id"], resp)
                    if entry["id"] == eid:
                        result = self._take_done_locked(eid)
                        break
                    # fremde Antworten liegen für deren Threads in _done
        # Handler erst nach Freigabe von _send_lock (dürfen selbst senden)
        self._run_deferred(deferred)
        return result

    def cmd_485_send_data(self, data, timeout=1.0):
        """Fire-and-forget (Beleg: Serial_485_Wrapper.cmd_485_send_data).
        Gibt den gesendeten Frame zurück."""
        addr, _length, status, fun, payload = self._split_wire(data)
        return self.bus.raw_send(addr, fun, list(payload), status=status,
                                 expect_response=False)

    # --- Registry/Dispatch (Belegnamen) ---
    def register_response(self, addr, fun, callback):
        """Registriert Handler callback(data_send, msg) für (addr, fun)
        (Beleg: Serial_485_Wrapper.register_response). None = Wildcard,
        callback=None löscht."""
        with self._qlock:
            if callback is None:
                self._handlers.pop((addr, fun), None)
            else:
                self._handlers[(addr, fun)] = callback

    def handle_callback(self, data_send, msg):
        """Dispatchet Antwort-Vollframe msg an Registry
        (Beleg: Serial_485_Wrapper.handle_callback). Handler-Exceptions
        werden geloggt und nicht propagiert (Rückgabe dann None). Wird vom
        Wrapper ohne gehaltene Locks aufgerufen."""
        try:
            rfun, _rdata = parse_frame(bytes(msg))
        except ValueError:
            return None
        raw = bytes(msg)
        raddr = raw[1] if len(raw) > 1 else None
        for key in ((raddr, rfun), (raddr, None), (None, rfun), (None, None)):
            cb = self._handlers.get(key)
            if cb is not None:
                try:
                    return cb(bytes(data_send), bytes(msg))
                except Exception:
                    _log.exception("Wrapper-Handler %r fehlgeschlagen", key)
                    return None
        if self._default_handler is not None:
            try:
                return self._default_handler(bytes(data_send), bytes(msg))
            except Exception:
                _log.exception("Wrapper-Default-Handler fehlgeschlagen")
        return None

    # --- Lifecycle/Stats ---
    def _connect(self, *a, **k):
        return self.bus.connect_uart(*a, **k)

    def _disconnect(self):
        return self.bus.disconnect()

    def _shutdown(self):
        return self.bus.disconnect()

    def stats(self):
        """Bus-Zähler + queue_len/done_len (vgl. serial_485_queue_get_stats)."""
        d = self.bus.stats()
        with self._qlock:
            d["queue_len"] = len(self._queue)
            d["done_len"] = len(self._done)
        return d


# --- Selbsttest mit den mitgeschnittenen Live-Frames (keine Hardware nötig) ---
_LIVE_FRAMES = [
    # (hex-frame, Beschreibung)
    ("f7 21 07 00 00 01 02 03 04 f0", "belt Version-Resp"),
    ("f7 21 10 00 02 03 00 00 00 93 ff f7 b7 95 ff f3 51 4c 3d", "belt Flash-Resp"),
    ("f7 21 07 00 06 ff f8 c7 12 4c", "belt ADC-Resp"),
    ("f7 01 11 00 a2 01 00 a0 a1 a2 a3 a4 a5 a6 a7 a8 a9 aa ab f0", "Auto-Addr a2"),
    ("f7 01 07 00 03 ff ff ff 00 98", "GET_REMAIN_LEN leer"),
    ("f7 01 09 00 0a 13 34 00 00 00 00 d7", "Herzschlag 0x0a"),
    ("f7 01 03 00 02 b3", "GET_RFID leere Antwort (Status 00)"),
    ("f7 01 07 00 03 1a 00 ff 00 99", "Remain A=26%"),
    # Status 00 = ANTWORT ohne Daten (Requests tragen Status 0xff), Review G5
    ("f7 01 03 00 10 cd", "EXTRUDE leere Antwort (Status 00)"),
    ("f7 01 03 00 11 ca", "RETRUDE leere Antwort (Status 00)"),
    ("f7 22 10 00 04 03 00 00 00 8a ff f1 63 09 ff ed 12 bd 3c", "belt mdly write_flash"),
]


def selftest():
    fails = 0
    total = 0  # Anzahl aller Einzelchecks (Live-Frames, Asserts, check2)
    for hexframe, label in _LIVE_FRAMES:
        total += 1
        raw = bytes.fromhex(hexframe)
        # 1. CRC des geloggten Frames muss stimmen
        body = list(raw[2:-1])
        if crc8_cal(body) != raw[-1]:
            print("FAIL crc %s" % label)
            fails += 1
            continue
        # 2. Neu gebauter Frame (addr/fun/daten) muss identisch sein
        rebuilt = build_frame(raw[1], raw[4], raw[5:-1], status=raw[3])
        if rebuilt != raw:
            print("FAIL rebuild %s" % label)
            fails += 1
            continue
        # 3. Parsen muss fun + daten liefern
        fun, data = parse_frame(raw)
        assert fun == raw[4] and data == raw[5:-1], label
        print("ok %s" % label)
    # 4. Resync: Müll + halber Frame vor gültigem Frame werden verworfen
    noisy = (b"\x00\xff\x01\x02" +
             bytes.fromhex("f7 01 03 00 02 b3"))
    assert _resync(bytearray(noisy)) == bytes.fromhex("f7 01 03 00 02 b3")
    print("ok resync Müllpräfix")
    total += 1
    assert _resync(bytearray(b"\x00\x01\x02")) == bytearray()
    print("ok resync ohne Head")
    total += 1
    # 5. Transaktions-Lock existiert (Schutz bei parallelen Threads)
    import sys
    import types

    class _FakePort:
        def reset_input_buffer(self):
            pass

        def write(self, frame):
            self.written = frame

        def read(self, n):
            return b""

    fake_serial = types.ModuleType("serial")
    fake_serial.Serial = lambda *a, **k: _FakePort()
    sys.modules["serial"] = fake_serial
    try:
        inst = Serial485("/dev/ttyFAKE0")
        assert hasattr(inst, "_lock")
        import threading
        assert isinstance(inst._lock, type(threading.Lock()))
        print("ok transact-lock")
        total += 1
    finally:
        del sys.modules["serial"]

    # --- 6+. Session-Schicht (Belegnamen .so), hardwarelos mit FakeSerial ---
    class FakeSerial:
        """Simulierter Halbduplex-Port: spielt pro write() genau eine
        geskriptete Antwort ab (bytes-Vollframe oder None = Stille/Timeout).
        Zeichnet alle writes in Reihenfolge auf."""

        def __init__(self, script):
            self.script = list(script)
            self.written = []
            self._read_buf = bytearray()

        def reset_input_buffer(self):
            self._read_buf.clear()

        def write(self, frame):
            self.written.append(bytes(frame))
            self._read_buf.clear()
            if self.script:
                nxt = self.script.pop(0)
                if nxt is not None:
                    self._read_buf += bytes(nxt)

        def read(self, n):
            if self._read_buf:
                chunk = bytes(self._read_buf[:n])
                del self._read_buf[:n]
                return chunk
            time.sleep(0.002)
            return b""

        def close(self):
            pass

    def check2(name, cond):
        nonlocal fails, total
        total += 1
        print(("ok " if cond else "FAIL ") + name)
        fails += 0 if cond else 1

    # 6. connect/disconnect/_start_session/stats-Verwaltung
    bus0 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    check2("session start", bus0._start_session() is True
           and bus0._session_active is True)
    check2("session verbunden", bus0._connected is True)
    s0 = bus0.stats()
    check2("stats keys", set(s0) == {"sent", "received", "timeouts",
                                     "retries", "crc_errors"})
    check2("stats null", all(v == 0 for v in s0.values()))
    check2("methoden vorhanden",
           all(hasattr(bus0, m) for m in (
               "raw_send", "raw_send_wait_ack", "connect_uart",
               "_start_session", "_bg_thread", "get_response",
               "disconnect", "stats", "register_response")))
    bus0.disconnect()
    check2("disconnect", bus0._connected is False
           and bus0._session_active is False)
    bus0.connect_uart()  # injiziert: ohne pyserial/Hardware
    check2("reconnect", bus0._connected is True)
    bus0._start_session()

    # 7. Queue-Reihenfolge + Registry-Dispatch + Stats (Wrapper)
    resp1 = build_frame(0x01, 0x10, [0xA1])
    resp2 = build_frame(0x01, 0x10, [0xB2])
    resp3 = build_frame(0x01, 0x10, [0xC3])
    wire1 = bytes([0x01, 3 + 3, STATUS_OPERATIONAL, 0x10, 0x01, 0x05, 0x00])
    wire2 = bytes([0x01, 3 + 3, STATUS_OPERATIONAL, 0x10, 0x01, 0x06, 0x00])
    wire3 = bytes([0x01, 3 + 3, STATUS_OPERATIONAL, 0x10, 0x01, 0x07, 0x03])
    bus1 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([resp1, resp2, resp3]))
    bus1._start_session()
    wrap = Serial_485_Wrapper(bus=bus1)
    check2("wrapper methoden",
           all(hasattr(wrap, m) for m in (
               "cmd_send_data_with_response", "cmd_485_send_data",
               "add_send_data", "remove_send_data", "send_queue_process",
               "register_response", "is_exist_in_send_list")))
    hits = []
    wrap.register_response(0x01, 0x10, lambda ds, msg: hits.append((bytes(ds), bytes(msg))))
    bus_hits = []
    bus1.register_response(0x01, 0x10,
                           lambda a, s, f, d: bus_hits.append((a, s, f, bytes(d))))
    # remove/is_exist-Pfad (eigener Mini-Queue-Stand, stört Reihenfolge nicht)
    dummy = bytes([0x01, 3, STATUS_OK, 0xA2])
    did = wrap.add_send_data(dummy, timeout=0.05)
    check2("queue exist", wrap.is_exist_in_send_list(dummy) is True)
    check2("queue remove", wrap.remove_send_data(did) is True
           and wrap.is_exist_in_send_list(dummy) is False)
    check2("queue remove-miss", wrap.remove_send_data(999999) is False)
    id1 = wrap.add_send_data(wire1, timeout=0.5)
    id2 = wrap.add_send_data(wire2, timeout=0.5)
    id3 = wrap.add_send_data(wire3, timeout=0.5)
    check2("queue fifo-ids", (id1, id2, id3) == (id1, id1 + 1, id1 + 2))
    results = wrap.send_queue_process()
    check2("queue reihenfolge",
           [r for _, r in results] == [resp1, resp2, resp3]
           and [i for i, _ in results] == [id1, id2, id3])
    check2("queue wire-reihenfolge",
           [bytes(w)[5:-1] for w in bus1.ser.written] ==
           [bytes(wire1)[4:], bytes(wire2)[4:], bytes(wire3)[4:]]
           and [bytes(w)[4] for w in bus1.ser.written] == [0x10, 0x10, 0x10])
    check2("registry dispatch wrapper", len(hits) == 3
           and hits[0] == (wire1, resp1) and hits[2] == (wire3, resp3))
    check2("registry dispatch bus", len(bus_hits) == 3
           and bus_hits[1][3] == bytes([0xB2]))
    st1 = wrap.stats()
    check2("stats queue", st1["sent"] == 3 and st1["received"] == 3
           and st1["queue_len"] == 0)
    # Einzel-Call (auto_addr-Signatur: wire -> Vollframe oder None)
    bus1b = Serial485("/dev/ttyFAKE1", ser=FakeSerial([resp1]))
    bus1b._start_session()
    wrap1b = Serial_485_Wrapper(bus=bus1b)
    single = wrap1b.cmd_send_data_with_response(wire1, 0.5, False)
    check2("cmd_send_data_with_response", single == resp1)
    check2("cmd timeout->None",
           Serial_485_Wrapper(bus=Serial485(
               "/dev/ttyFAKE1", ser=FakeSerial([None]))).cmd_send_data_with_response(
                   wire1, 0.03, False) is None)

    # 8. Retry-Zählung: 1x Stille, dann Antwort
    good = build_frame(0x01, 0x03, [0x1A, 0x00, 0xFF, 0x00])
    bus2 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([None, good]))
    bus2._start_session()
    rdata = bus2.transact(0x01, 0x03, [0x0F], timeout=0.05, retries=2,
                          status=STATUS_OPERATIONAL)
    check2("retry antwort", rdata == bytes([0x1A, 0x00, 0xFF, 0x00]))
    st2 = bus2.stats()
    check2("retry zaehlung", st2["sent"] == 2 and st2["retries"] >= 1
           and st2["received"] == 1 and st2["timeouts"] == 0)

    # 9. Timeout- und CRC-Stats
    bus3 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([None, None]))
    bus3._start_session()
    try:
        bus3.transact(0x01, 0x03, [0x0F], timeout=0.03, retries=1)
        check2("timeout wirft", False)
    except TimeoutError:
        check2("timeout wirft", True)
    check2("timeout stats", bus3.stats()["timeouts"] == 1)
    bad = bytearray(build_frame(0x01, 0x03, [0x01]))
    bad[-1] ^= 0xFF  # CRC verderben
    bus4 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([bytes(bad), good]))
    bus4._start_session()
    rdata4 = bus4.transact(0x01, 0x03, [0x0F], timeout=0.2, retries=2)
    st4 = bus4.stats()
    check2("crc retry antwort", rdata4 == bytes([0x1A, 0x00, 0xFF, 0x00]))
    check2("crc stats", st4["crc_errors"] >= 1 and st4["retries"] >= 1)

    # 10. raw_send (ohne ACK) + get_response (ein Frame)
    bus5 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([resp2]))
    bus5._start_session()
    sent_frame = bus5.raw_send(0x01, 0x10, [0x01, 0x05, 0x00],
                               status=STATUS_OPERATIONAL)
    check2("raw_send frame", sent_frame == build_frame(
        0x01, 0x10, [0x01, 0x05, 0x00], status=STATUS_OPERATIONAL))
    check2("raw_send stats", bus5.stats()["sent"] == 1)
    bus5.ser._read_buf += bytearray(resp2)  # Antwort einlegen
    ga, gs, gf, gd = bus5.get_response(timeout=0.3)
    check2("get_response", (ga, gs, gf, gd) == (0x01, 0x00, 0x10, bytes([0xB2])))

    # 10b. Empfangs-Robustheit (Review K1-K3, G2, M1/M2)
    class ChunkSerial(FakeSerial):
        """Liefert den Empfangspuffer in festen Häppchen: chunks = Liste von
        Chunkgrößen je read() (letzte wiederholt sich)."""

        def __init__(self, script, chunks):
            FakeSerial.__init__(self, script)
            self.chunks = list(chunks)
            self._ci = 0

        def read(self, n):
            if self._read_buf:
                k = self.chunks[min(self._ci, len(self.chunks) - 1)]
                self._ci += 1
                return FakeSerial.read(self, min(n, k))
            return FakeSerial.read(self, n)

    gdata = bytes([0x1A, 0x00, 0xFF, 0x00])
    for name, chunks in (("3+rest", [3, 64]), ("4-byte", [4])):
        busf = Serial485("/dev/ttyFAKE1", ser=ChunkSerial([good], chunks))
        try:
            ok = busf.transact(0x01, 0x03, [0x0F], timeout=0.2, retries=0) == gdata
        except TimeoutError:
            ok = False
        check2("fragmentiert %s" % name, ok)
    bigdata = bytes(range(80))
    big = build_frame(0x01, 0x02, bigdata)  # 86 Byte > read(64)
    busb = Serial485("/dev/ttyFAKE1", ser=FakeSerial([big]))
    try:
        ok = busb.transact(0x01, 0x02, [0x01], timeout=0.2, retries=0) == bigdata
    except TimeoutError:
        ok = False
    check2("frame >64 byte", ok and busb.stats()["crc_errors"] == 0)
    hb = bytes.fromhex("f7 01 09 00 0a 13 34 00 00 00 00 d7")
    bush = Serial485("/dev/ttyFAKE1", ser=FakeSerial([hb + good]))
    t0 = time.monotonic()
    try:
        ok = bush.transact(0x01, 0x03, [0x0F], timeout=0.5, retries=0) == gdata
    except TimeoutError:
        ok = False
    check2("herzschlag+antwort ein chunk", ok and time.monotonic() - t0 < 0.4)
    flash = bytes.fromhex("f7 21 10 00 02 03 00 00 00 93 ff f7 b7 95 ff f3 51 4c 3d")
    adc = build_frame(0x21, 0x06, [0xFF, 0xF8, 0xC7, 0x12])
    busx = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    busx.ser._read_buf += flash + adc
    try:
        ok = busx.get_response(timeout=0.3, expect_fun=0x06) == (
            0x21, 0x00, 0x06, bytes([0xFF, 0xF8, 0xC7, 0x12]))
    except TimeoutError:
        ok = False
    check2("fremd-frame mit 0xF7 in daten", ok and busx.stats()["crc_errors"] == 0)
    junk = bytes.fromhex("f7 01 00 00 00 00")
    busj = Serial485("/dev/ttyFAKE1", ser=FakeSerial([junk + good]))
    try:
        ok = busj.transact(0x01, 0x03, [0x0F], timeout=0.2, retries=0) == gdata
    except Exception:  # vorher IndexError
        ok = False
    check2("müll len=0 vor antwort", ok)
    try:
        parse_frame(junk)
        ok = False
    except ValueError:
        ok = True
    check2("parse_frame len<3 -> ValueError", ok)
    busr = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    busr._rest_buf = good
    busr._start_session()
    try:
        busr.get_response(timeout=0.03)
        ok = False
    except TimeoutError:
        ok = True
    check2("_start_session leert _rest_buf", ok)
    # Handler ruft selbst Bus-/Wrapper-Funktionen (früher Deadlock)
    busd = Serial485("/dev/ttyFAKE1", ser=FakeSerial([good]))
    nested = []
    busd.register_response(0x01, 0x03,
                           lambda a, st, f, d: nested.append(busd.raw_send(0x01, 0x10)))
    thd = threading.Thread(target=lambda: busd.transact(
        0x01, 0x03, [0x0F], timeout=0.1, retries=0), daemon=True)
    thd.start()
    thd.join(1.5)
    check2("bus-handler ruft raw_send (kein deadlock)",
           not thd.is_alive() and len(nested) == 1)
    wbus = Serial485("/dev/ttyFAKE1", ser=FakeSerial([good, good]))
    wwrap = Serial_485_Wrapper(bus=wbus)
    wwire = bytes([0x01, 4, STATUS_OK, 0x03, 0x0F])
    wnested = []
    wflag = []

    def _wcb(ds, msg):
        if not wflag:  # nur einmal verschachtelt senden
            wflag.append(1)
            wnested.append(wwrap.cmd_send_data_with_response(wwire, 0.1, False))
    wwrap.register_response(0x01, 0x03, _wcb)
    thw = threading.Thread(target=lambda: wwrap.cmd_send_data_with_response(
        wwire, 0.1, False), daemon=True)
    thw.start()
    thw.join(1.5)
    check2("wrapper-handler sendet selbst (kein deadlock)",
           not thw.is_alive() and wnested == [good])
    # Handler-Exception wird geloggt, nicht propagiert
    buse = Serial485("/dev/ttyFAKE1", ser=FakeSerial([good]))

    def _boom(*a):
        raise RuntimeError("selbsttest: erwartete Handler-Exception")
    buse.register_response(0x01, 0x03, _boom)
    _lg = logging.getLogger(__name__)
    _old_disabled, _lg.disabled = _lg.disabled, True  # Traceback nicht ausgeben
    try:
        ok = buse.transact(0x01, 0x03, [0x0F], timeout=0.1, retries=0) == gdata
    except Exception:
        ok = False
    finally:
        _lg.disabled = _old_disabled
    check2("handler-exception nicht propagiert", ok)

    # 10c. Review M1 (16.10.2026): Absenderadresse muss zum Ziel passen
    box_wheel = bytes.fromhex("f7 01 07 00 0e c4 e6 fe 0b f3")  # Box, fun 0x0e
    mot_reply = build_frame(0x81, 0x0e, [0x11, 0x22])          # synthetisch
    busm = Serial485("/dev/ttyFAKE1", ser=FakeSerial([box_wheel + mot_reply]))
    try:
        ok = busm.transact(0x81, 0x0e, [0x02], timeout=0.2, retries=0) \
            == bytes([0x11, 0x22])
    except TimeoutError:
        ok = False
    check2("M1 fremder Frame (0x01) vor Motor-Antwort -> echte Antwort", ok)
    busm2 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([box_wheel]))
    try:
        busm2.transact(0x81, 0x0e, [0x02], timeout=0.05, retries=0)
        ok = False
    except TimeoutError as e:
        ok = "addr-Mismatch" in str(e)
    check2("M1 nur fremder Frame -> Timeout (addr-Mismatch)", ok)
    busm3 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([box_wheel]))
    try:
        ok = busm3.transact(0x81, 0x0e, [0x02], timeout=0.1, retries=0,
                            expect_addr=None) == box_wheel[5:-1]
    except TimeoutError:
        ok = False
    check2("M1 expect_addr=None -> beliebiger Absender (explizit)", ok)
    a1_fe = build_frame(0xFE, 0xA1, [0x01])
    a2_01 = build_frame(0x01, 0xA2, [0x01])
    busbc = Serial485("/dev/ttyFAKE1", ser=FakeSerial([a1_fe, a2_01]))
    try:
        ok = (busbc.transact(0xFE, 0xA1, [0xFE, 0xFE], timeout=0.1,
                             retries=0) == b"\x01"
              and busbc.transact(0xFF, 0xA2, [], timeout=0.1, retries=0)
              == b"\x01")
    except TimeoutError:
        ok = False
    check2("M1 Broadcast-Ziel 0xFE/0xFF: Absender beliebig", ok)
    check2("M1 reply_addr_for", reply_addr_for(0x81) == 0x81
           and all(reply_addr_for(a) is None
                   for a in (0xFE, 0xFF, 0xFD, 0xFC, 0xEA, 0xEB)))
    wrm = Serial_485_Wrapper(bus=Serial485(
        "/dev/ttyFAKE1", ser=FakeSerial([box_wheel + mot_reply])))
    check2("M1 Wrapper-Pfad überspringt fremden Absender",
           wrm.cmd_send_data_with_response(
               bytes([0x81, 4, STATUS_OK, 0x0e, 0x02]), 0.2, False)
           == mot_reply)

    # 10d. Review M2: transact_full liefert das Status-Byte (Live-Frame
    #      0x10 mode 5 mit Status 0x0c Odometer-Stillstand)
    st0c = bytes.fromhex("f7 01 07 0c 10 c3 30 40 af a4")
    bussf = Serial485("/dev/ttyFAKE1", ser=FakeSerial([st0c]))
    full = bussf.transact_full(0x01, 0x10, [0x02, 0x05, 0x00], timeout=0.1,
                               retries=0, status=STATUS_OPERATIONAL)
    check2("M2 transact_full Status 0x0c + Daten",
           full[:4] == (0x01, 0x0c, 0x10, bytes.fromhex("c3 30 40 af"))
           and full[4] == st0c)

    # 10e. Spätantwort nach Timeout bei retries=0: genau 1 Sendung (keine
    #      Mehrfachauslösung der Mechanik); die Spätantwort verwirft das
    #      reset_input_buffer der nächsten Transaktion, sofern sie VOR deren
    #      Senden eintrifft (sonst fun/addr-Filter, s. _recv_frame_locked)
    class LateSerial(FakeSerial):
        """Antwort auf die 1. Sendung erst nach delay s (nach dem Timeout)."""

        def __init__(self, late, delay, script=()):
            FakeSerial.__init__(self, script)
            self.late, self.delay, self.t_write = late, delay, None

        def write(self, frame):
            if self.t_write is None:
                self.t_write = time.monotonic()
                self.written.append(bytes(frame))
                return
            FakeSerial.write(self, frame)

        def read(self, n):
            if (self.late is not None and self.t_write is not None
                    and time.monotonic() - self.t_write >= self.delay):
                self._read_buf += self.late
                self.late = None
            return FakeSerial.read(self, n)

    late_ack = build_frame(0x01, 0x11, [])
    busl = Serial485("/dev/ttyFAKE1", ser=LateSerial(late_ack, 0.08))
    try:
        busl.transact(0x01, 0x11, [0x02, 0x01], timeout=0.04, retries=0,
                      status=STATUS_OPERATIONAL)
        ok = False
    except TimeoutError:
        ok = True
    check2("Spätantwort retries=0: Timeout, genau 1 Sendung",
           ok and len(busl.ser.written) == 1 and busl.stats()["sent"] == 1
           and busl.stats()["retries"] == 0)

    # 10f. Review M6: Bus-Lock-Obergrenze + kein Deadlock bei langem Halten
    busv = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    for bad_t, bad_r in ((MAX_BUS_HOLD_S + 1, 0), (300.0, 4), (-1.0, 0)):
        try:
            busv.transact(0x01, 0x0d, [0x0f, 0x02], timeout=bad_t,
                          retries=bad_r)
            ok = False
        except ValueError:
            ok = busv.stats()["sent"] == 0
        check2("M6 Haltedauer-Guard %.0f s x %d" % (bad_t, bad_r + 1), ok)

    class SlowSerial(FakeSerial):
        """1. Antwort kommt erst nach delay s (langes Kommando)."""

        def __init__(self, script, delay):
            FakeSerial.__init__(self, script)
            self.delay, self.t0, self.pending = delay, None, None

        def write(self, frame):
            FakeSerial.write(self, frame)
            if self.t0 is None:  # 1. Sendung: Antwort zurückhalten
                self.t0 = time.monotonic()
                self.pending, self._read_buf = bytes(self._read_buf), bytearray()

        def read(self, n):
            if self.pending and time.monotonic() - self.t0 >= self.delay:
                self._read_buf += self.pending
                self.pending = None
            return FakeSerial.read(self, n)

    ack0d = build_frame(0x01, 0x0d, [])
    busw = Serial485("/dev/ttyFAKE1", ser=SlowSerial([ack0d, good], 0.25))
    res = {}
    tl = threading.Thread(target=lambda: res.__setitem__("long", busw.transact(
        0x01, 0x0d, [0x01, 0x02], timeout=1.0, retries=0)), daemon=True)
    tl.start()
    time.sleep(0.05)
    t_b = time.monotonic()
    ts = threading.Thread(target=lambda: res.__setitem__("short", busw.transact(
        0x01, 0x03, [0x0F], timeout=0.3, retries=0)), daemon=True)
    ts.start()
    tl.join(2.0)
    ts.join(2.0)
    check2("M6 zweiter Thread wartet auf Lock, dann eigene Antwort",
           not tl.is_alive() and not ts.is_alive()
           and res.get("long") == b"" and res.get("short") == gdata
           and time.monotonic() - t_b >= 0.15
           and [bytes(w)[4] for w in busw.ser.written] == [0x0d, 0x03])

    # 11. Kreuzvalidierung gegen Fremd-Referenz ref-hi/ (GPL-3.0, nur lesen;
    #     Herkunft siehe ref-hi/HERKUNFT.txt). Hex-Strings enthalten teils
    #     Leerzeichen-Tippfehler -> vor bytes.fromhex alle Leerzeichen entfernen.
    import json
    import os

    def _hex(sv):
        return bytes.fromhex("".join(sv.split()))

    ref_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ref-hi")
    if not os.path.isdir(ref_dir):
        print("skip ref-hi (Ordner fehlt: %s)" % ref_dir)
    else:
        with open(os.path.join(ref_dir, "test_vectors.json"), encoding="utf-8") as fh:
            vectors = json.load(fh)
        for i, vec in enumerate(vectors):
            full = _hex(vec["full_frame_hex"])
            scope = _hex(vec["crc_scope_hex"])
            expected = int(vec["expected_crc"], 16)
            check2("ref-hi crc #%d %s" % (i, vec.get("description", "")),
                   crc8_cal(list(scope)) == expected
                   and full[2:-1] == scope and full[-1] == expected)
        with open(os.path.join(ref_dir, "valid_messages.json"), encoding="utf-8") as fh:
            messages = json.load(fh)
        for i, msg in enumerate(messages):
            raw = _hex(msg["hex"])
            func = int(msg["func"], 16)
            status = int(msg["status"], 16)
            data = _hex(msg["data_hex"])
            try:
                parsed = parse_frame(raw)
            except ValueError:
                parsed = None
            check2("ref-hi msg #%d %s" % (i, msg.get("description", "")),
                   build_frame(msg["addr"], func, data, status=status) == raw
                   and parsed == (func, data))

    # 12. Fix B.1: _done wächst nicht unbegrenzt (MAX + TTL + cleanup)
    wbus12 = Serial485("/dev/ttyFAKE1", ser=FakeSerial(
        [build_frame(0x01, 0x03, [0x00])] * 300))
    wbus12._start_session()
    wrap12 = Serial_485_Wrapper(bus=wbus12)
    wire12 = bytes([0x01, 4, STATUS_OK, 0x03, 0x0F])
    for _ in range(WRAPPER_DONE_MAX + 22):
        wrap12.add_send_data(wire12, timeout=0.05)
    res12 = wrap12.send_queue_process()
    check2("B.1 _done begrenzt (MAX %d)" % WRAPPER_DONE_MAX,
           len(res12) == WRAPPER_DONE_MAX + 22
           and len(wrap12._done) <= WRAPPER_DONE_MAX
           and wrap12.stats()["done_len"] <= WRAPPER_DONE_MAX)
    check2("B.1 älteste verdrängt, neueste da",
           min(wrap12._done) > 1 and max(wrap12._done) == wrap12._seq)
    # TTL: künstlich altern lassen -> cleanup räumt ab
    for k in list(wrap12._done_time):
        wrap12._done_time[k] -= (WRAPPER_DONE_TTL_S + 1.0)
    removed, left = wrap12.cleanup_done()
    check2("B.1 cleanup_done räumt Verfallene",
           removed >= 1 and left == 0 and len(wrap12._done) == 0)
    # Normalbetrieb nach Cleanup ungestört:
    wbus12b = Serial485("/dev/ttyFAKE1", ser=FakeSerial(
        [build_frame(0x01, 0x03, [0x07])]))
    wbus12b._start_session()
    wrap12b = Serial_485_Wrapper(bus=wbus12b)
    check2("B.1 Einzel-Call nach Cleanup",
           wrap12b.cmd_send_data_with_response(wire12, 0.2, False)
           == build_frame(0x01, 0x03, [0x07]))

    # 13. Fix B.2: bg-Thread schnappt get_response nichts mehr weg
    resp13 = build_frame(0x01, 0x08, [0x07])
    bus13 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    bus13._start_session(start_bg=True, bg_timeout=0.05)
    hits13 = []
    bus13.register_response(0x01, 0x08,
                            lambda a, s, f, d: hits13.append(bytes(d)))
    bus13.raw_send(0x01, 0x08, [0x00])  # registriert Erwartung (Default)
    time.sleep(0.12)  # Lücke: bg hätte früher die Antwort kassiert
    bus13.ser._read_buf += bytearray(resp13)  # Antwort trifft EIN
    time.sleep(0.12)  # bg liest zuerst – parkt statt zu dispatchen
    try:
        ga, gs, gf, gd = bus13.get_response(timeout=0.5, expect_fun=0x08)
        ok13 = (ga, gs, gf, gd) == (0x01, 0x00, 0x08, bytes([0x07]))
    except TimeoutError:
        ok13 = False
    check2("B.2 get_response findet geparkte Antwort (kein Timeout)", ok13)
    check2("B.2 geparkte Antwort NICHT doppelt dispatcht", hits13 == [bytes([0x07])])
    bus13.disconnect()
    # Ohne Erwartung: Verhalten wie bisher (Dispatch, kein Parken)
    resp13b = build_frame(0x01, 0x08, [0x05])
    bus13b = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    bus13b._start_session(start_bg=True, bg_timeout=0.05)
    hits13b = []
    bus13b.register_response(0x01, 0x08,
                             lambda a, s, f, d: hits13b.append(bytes(d)))
    bus13b.raw_send(0x01, 0x08, [0x00], expect_response=False)
    bus13b.ser._read_buf += bytearray(resp13b)
    time.sleep(0.25)
    try:
        bus13b.get_response(timeout=0.05, expect_fun=0x08)
        ok13b = False  # dürfte nichts mehr finden (bg hat dispatcht)
    except TimeoutError:
        ok13b = True
    check2("B.2 ohne Erwartung: bg dispatcht wie bisher", ok13b and hits13b == [bytes([0x05])])
    bus13b.disconnect()

    # 14. Sendeschutz 17.10.2026: Längengrenze (TX) + Broadcast-Schutz
    def _raises_tx(fn, *a, **kw):
        try:
            fn(*a, **kw)
        except TxGuardError:
            return True
        except Exception:
            return False
        return False

    check2("TX-Grenzen: 100 Datenbytes, fun 0xF0 0x80, len <= 0xFC",
           TX_DATA_MAX == 100 and TX_DATA_MAX_UPDATE == 0x80
           and tx_data_max(0x10) == 100 and tx_data_max(0xF0) == 0x80
           and TX_DATA_MAX_UPDATE + 3 <= BOOTLOADER_LEN_MAX
           and issubclass(TxGuardError, ValueError))
    check2("TX-Länge: 100 B ok (len 103), 101 B gesperrt",
           build_tx_frame(0x01, 0x10, bytes(100))[2] == 103
           and _raises_tx(build_tx_frame, 0x01, 0x10, bytes(101)))
    check2("TX fun 0xF0: Datenblock 0x80 und 0x81 gesperrt (seit 18.10.2026)",
           _raises_tx(build_tx_frame, 0x01, 0xF0, bytes(0x80))
           and _raises_tx(build_tx_frame, 0x01, 0xF0, bytes(0x81)))
    big = build_frame(0x01, 0x02, bytes(252))
    check2("RX unverändert bis len 255 (build/parse neutral)",
           big[2] == 255 and parse_frame(big) == (0x02, bytes(252))
           and _scan_frame(bytearray(big))[0] == "ok")
    try:
        build_frame(0x01, 0x02, bytes(253))
        ok14 = False
    except ValueError as e:
        ok14 = "zu lang" in str(e)
    check2("build_frame len > 255 -> klare ValueError", ok14)
    # Längste Live-Requests bleiben erlaubt: 0xA0 (13 B), 0x06-Schreiben
    check2("TX: längste Live-Requests erlaubt (0xA0 13 B, 0x06 6 B)",
           len(check_tx_frame(0xFE, 0xA0, bytes(13))) == 13
           and len(check_tx_frame(0x81, 0x06, [1, 70, 0, 0, 0x96, 0x43])) == 6)
    # Broadcast-Schutz: gesperrte Kombinationen, NICHTS geschrieben
    blocked = [(0xFF, 0x0B, [0x00]), (0xFF, 0x0B, []), (0xFF, 0x0B, [0x01, 0x00]),
               (0xFF, 0x0B, [0x02]), (0xFF, 0x01, [0x00]), (0xFF, 0x01, []),
               (0xFF, 0x01, [0x01]), (0xFD, 0x0B, [0x00]), (0xFD, 0x0B, [0x01]),
               (0xFD, 0x01, [0x00])]
    bus14 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    ok14 = True
    for a, f, d in blocked:
        ok14 &= _raises_tx(check_tx_frame, a, f, d)
        ok14 &= _raises_tx(bus14.transact, a, f, d, timeout=0.01, retries=0)
        ok14 &= _raises_tx(bus14.transact_full, a, f, d, timeout=0.01, retries=0)
        ok14 &= _raises_tx(bus14.raw_send, a, f, d, expect_response=False)
    check2("Broadcast 0xFF/0xFD + 0x01/0x0B gesperrt (transact/_full/raw_send)",
           ok14 and bus14.ser.written == [] and bus14.stats()["sent"] == 0)
    try:
        check_tx_frame(0xFF, 0x0B, [0x00])
        msg14 = ""
    except TxGuardError as e:
        msg14 = str(e)
    check2("Fehlermeldung nennt Bootloader + Ausnahme [01]",
           "Bootloader" in msg14 and "0xFF 0x0B [01]" in msg14)
    wrap14 = Serial_485_Wrapper(bus=bus14)
    check2("Wrapper: gesperrter Broadcast kommt nie in die Queue",
           _raises_tx(wrap14.add_send_data, bytes([0xFF, 4, 0, 0x0B, 0x00]))
           and _raises_tx(wrap14.cmd_send_data_with_response,
                          bytes([0xFF, 4, 0, 0x01, 0x00]), 0.01, False)
           and _raises_tx(wrap14.cmd_485_send_data, bytes([0xFF, 4, 0, 0x0B, 0x00]))
           and _raises_tx(wrap14.add_send_data, bytes([0x01, 0x6A, 0, 0x10])
                          + bytes(101))
           and wrap14.stats()["queue_len"] == 0 and bus14.ser.written == [])
    # Erlaubt: 0xFF 0x0B [01] (CFS-Loader->App, live `0xff 0x4 0x0 0xb 0x1`),
    # 0xFF 0x0E [01] (Motor-Adress-Broadcast, live), Unicast 0x0B an 0x01
    live_l2a = bytes.fromhex("f7 ff 04 00 0b 01") + bytes(
        [crc8_cal([0x04, 0x00, 0x0B, 0x01])])
    bus14b = Serial485("/dev/ttyFAKE1", ser=FakeSerial([None, None, None]))
    try:
        bus14b.transact(0xFF, 0x0B, [0x01], timeout=0.01, retries=0)
        ok14b = False
    except TimeoutError:
        ok14b = True  # keine Antwort = live-Verhalten
    try:
        bus14b.transact(0xFF, 0x0E, [0x01], timeout=0.01, retries=0)
    except TimeoutError:
        pass
    bus14b.raw_send(0x01, 0x0B, [0x0F, 0x02], expect_response=False)
    check2("Erlaubt: 0xFF 0x0B [01] / 0xFF 0x0E [01] / Unicast 0x0B",
           ok14b and bus14b.ser.written[0] == live_l2a
           and bus14b.ser.written[1] == bytes.fromhex("f7 ff 04 00 0e 01 89")
           and len(bus14b.ser.written) == 3)
    # auto_addr-Pfad (Port von auto_addr_wrapper) über den echten Wrapper:
    # Loader-Gerät -> 0x0B [01] an 0xFF geht unverändert raus, kein Fehler.
    try:
        import os
        _here = os.path.dirname(os.path.abspath(__file__))
        if _here not in sys.path:
            sys.path.append(_here)  # auto_addr.py liegt neben serial485.py
        import auto_addr as _aa
        bus14c = Serial485("/dev/ttyFAKE1", ser=FakeSerial([None]))
        aa14 = _aa.AutoAddr(Serial_485_Wrapper(bus=bus14c))
        aa14.tables[_aa.DEV_TYPE_MB][0].mode = _aa.MODE_LOADER
        sent14 = aa14.logic_loader_check(_aa.DEV_TYPE_MB)
        ok14c = sent14 is True and bus14c.ser.written == [live_l2a]
    except Exception as e:  # noqa: BLE001 – im Selbsttest als FAIL melden
        print("auto_addr-Pfad: %r" % (e,))
        ok14c = False
    check2("auto_addr Loader->App (0xFF 0x0B [01]) über Wrapper unverändert", ok14c)

    # 15. Motor-Kalibrier-/Flash-Sperre 17.10.2026
    mot_blocked = []
    for a in sorted(TX_MOTOR_GUARD_ADDRS):
        mot_blocked += [(a, 0x03, []), (a, 0x03, [0x00]), (a, 0x04, [0x00]),
                        (a, 0x04, []), (a, 0x07, []), (a, 0x07, [0x00])]
        mot_blocked += [(a, 0x07, [sub, 0x14]) for sub in (1, 3, 4, 5, 7, 0xFF)]
        mot_blocked += [(a, 0x02, []), (a, 0x02, [0x00]), (a, 0x12, []),
                        (a, 0x12, [0x01] + [0xAB] * 8), (a, 0x12, [0x02, 0x00]),
                        (a, 0x12, [0x00])]
    mot_blocked += [(0xFF, 0x12, [0x01] + [0xAB] * 8), (0xFF, 0x12, [])]
    bus15 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    wrap15 = Serial_485_Wrapper(bus=bus15)
    ok15 = len(TX_MOTOR_GUARD_ADDRS) == 10 and 0xFD in TX_MOTOR_GUARD_ADDRS
    for a, f, d in mot_blocked:
        ok15 &= _raises_tx(check_tx_frame, a, f, d)
        ok15 &= _raises_tx(bus15.transact, a, f, d, timeout=0.01, retries=0)
        ok15 &= _raises_tx(bus15.transact_full, a, f, d, timeout=0.01, retries=0)
        ok15 &= _raises_tx(bus15.raw_send, a, f, d, expect_response=False)
        ok15 &= _raises_tx(wrap15.add_send_data,
                           bytes([a, len(d) + 3, 0, f] + list(d)))
    check2("Motor 0x80-0x88/0xFD: 0x03/0x04, 0x07 Sub 01/03/04/05, 0x02, "
           "0x12 außer [02] gesperrt",
           ok15 and bus15.ser.written == [] and bus15.stats()["sent"] == 0
           and wrap15.stats()["queue_len"] == 0)
    try:
        check_tx_frame(0x81, 0x07, [0x05, 0x14, 0, 0, 0, 0])
        msg15 = ""
    except TxGuardError as e:
        msg15 = str(e)
    check2("Fehlermeldung Motor-0x07 nennt erlaubte Subs 02/06",
           "02/06" in msg15 and "0x81" in msg15)
    # erlaubt: lesend 0x07 Sub 02/06 an Motoren; gleiche fun-Codes an
    # Box (0x01-0x04, 0xFE), Belt (0x21/0x22), Rack (0x11) und 0xFF frei
    allowed15 = [(0x81, 0x07, [0x02]), (0x84, 0x07, [0x06, 0x14]),
                 (0xFD, 0x07, [0x06, 0x08]), (0x81, 0x06, [0x02, 0x46]),
                 (0x81, 0x12, [0x02]), (0x84, 0x12, [0x02]),
                 (0xFF, 0x12, [0x02])]
    for a in (0x01, 0x02, 0x03, 0x04, 0xFE):
        allowed15 += [(a, 0x03, [0x0F]), (a, 0x04, [0x00, 0x01]),
                      (a, 0x04, [0x0F, 0x00]), (a, 0x07, [0x01]),
                      (a, 0x07, [0x00]), (a, 0x12, [0x01, 0x02]),
                      (a, 0x02, [])]
    # Belt/Rack seit 18.10. (Stufe 2) nur mit Whitelist-Payload (§17)
    for a in (0x21, 0x22):
        allowed15 += [(a, 0x04, [0x03] + [0] * 11 + [0x8C]),
                      (a, 0x02, [0x03])]
    allowed15 += [(0x11, 0x02, []), (0xFF, 0x03, []), (0xFF, 0x04, [0]),
                  (0xFF, 0x07, [0x05]), (0xFC, 0x04, [0x00])]
    ok15b = all(check_tx_frame(a, f, d) == list(d) for a, f, d in allowed15)
    bus15b = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    bus15b.raw_send(0x01, 0x04, [0x00, 0x01], expect_response=False)
    bus15b.raw_send(0x21, 0x04, [0x03] + [0] * 11 + [0x8C],
                    expect_response=False)
    bus15b.raw_send(0x81, 0x07, [0x06, 0x14], expect_response=False)
    check2("Erlaubt: Motor 0x07 Sub 02/06; Box/Belt/Rack/0xFF mit 0x03/0x04/0x07",
           ok15b and len(bus15b.ser.written) == 3
           and bus15b.ser.written[0] == build_frame(0x01, 0x04, [0x00, 0x01]))

    # 15b. Kalibrier-Einmalfreigabe 26.10.2026 abends (Regelstand 20261027)
    ok15c = _raises_tx(check_tx_frame, 0x81, 0x04, [0x00])
    tx_kalibrier_freigabe(0x81, 0x04, [0x00], 2.0)
    ok15c &= check_tx_frame(0x81, 0x04, [0x00]) == [0x00]
    ok15c &= check_tx_frame(0x81, 0x04, [0x00]) == [0x00]     # mehrfach geprüft
    for a, f, d in ((0x82, 0x04, [0x00]), (0x81, 0x03, [0x00]), (0x81, 0x04, []),
                    (0x81, 0x04, [0x01]), (0xFD, 0x04, [0x00]), (0x81, 0x07, [0x03, 0x00]),
                    (0x81, 0x07, [0x01])):
        ok15c &= _raises_tx(check_tx_frame, a, f, d)
    tx_kalibrier_freigabe_loeschen()
    ok15c &= _raises_tx(check_tx_frame, 0x81, 0x04, [0x00])
    tx_kalibrier_freigabe(0x83, 0x07, [0x03, 0x00], 2.0)
    ok15c &= check_tx_frame(0x83, 0x07, [0x03, 0x00]) == [0x03, 0x00]
    ok15c &= _raises_tx(check_tx_frame, 0x83, 0x07, [0x05, 0x00])
    tx_kalibrier_freigabe(0x83, 0x07, [0x03, 0x00], 0.001)
    time.sleep(0.01)
    ok15c &= _raises_tx(check_tx_frame, 0x83, 0x07, [0x03, 0x00])  # abgelaufen
    for a, f, d, t in ((0xFD, 0x04, [0], 1), (0x85, 0x04, [0], 1), (0x81, 0x07, [1], 1),
                       (0x81, 0x02, [], 1), (0x81, 0x12, [1] + [0] * 8, 1),
                       (0x81, 0x04, [0], 11), (0x81, 0x04, [0], 0)):
        try:
            tx_kalibrier_freigabe(a, f, d, t)
            ok15c = False
        except ValueError:
            pass
    tx_kalibrier_freigabe_loeschen()
    check2("Kalibrier-Einmalfreigabe: nur exakter Frame 0x03/0x04 [00] bzw. 0x07 "
           "[03 00] an 0x81-0x84, ≤ 10 s, löschbar", ok15c)

    # 16. Update-/Bootloader-/Service-Sperre 18.10.2026
    upd_addrs = (0x01, 0x04, 0x11, 0x21, 0x23, 0x81, 0x85, 0xFE, 0xFD, 0xFC,
                 0xFF, 0xEA, 0xEB)
    blk16, ok_16 = [], []
    for a in upd_addrs:
        blk16 += [(a, 0xF0, []), (a, 0xF0, [0x01]), (a, 0xF0, [0x06]),
                  (a, 0xF0, [0x04]), (a, 0xF0, [0x05]), (a, 0xF0, [0x07]),
                  (a, 0xF0, [0xFF]), (a, 0xF0, [0x00, 0x00]),
                  (a, 0xF0, [0x02, 0x00]), (a, 0xF0, [0x03, 0x01]),
                  (a, 0xF0, [0x80, 0x01, 0x00, 0x00]),       # Längen-Frame
                  (a, 0xF0, list(bytes(0x80))),              # Datenblock
                  (a, 0xF2, []), (a, 0xF2, [0x01, 0x08]),
                  (a, 0xF2, [0x00] + [0] * 12)]
        if a in TX_BELT_ADDRS or a in TX_BOOTLOADER_ADDRS \
                or a == TX_RFID_ADDR:
            blk16 += [(a, 0xF0, [0x00]), (a, 0xF0, [0x02]), (a, 0xF0, [0x03])]
        else:   # Belt/Bootloader/RFID: F0 seit Stufe 2 komplett gesperrt
            ok_16 += [(a, 0xF0, [0x00]), (a, 0xF0, [0x02]),
                      (a, 0xF0, [0x03])]
    for f in (0x00, 0x01, 0x0B, 0x0E, 0xA0, 0xA1, 0xF0, 0xF2):
        blk16 += [(0xF0, f, []), (0xF0, f, [0x00]), (0xF0, f, [0x01])]
    for a in (0xEA, 0xEB):
        blk16 += [(a, 0x50, [0x00]), (a, 0x50, [0x01]), (a, 0x50, []),
                  (a, 0x51, [0x01]), (a, 0x56, []), (a, 0x56, [0x00])]
        ok_16 += [(a, 0x0A, []), (a, 0x0C, []), (a, 0x14, [])]
    ok_16 += [(0x01, 0x56, []), (0x81, 0x50, [0x00]), (0xEF, 0xA2, []),
              (0xF1, 0x0E, [0x01])]
    bus16 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    wrap16 = Serial_485_Wrapper(bus=bus16)
    okb16 = True
    for a, f, d in blk16:
        okb16 &= _raises_tx(check_tx_frame, a, f, d)
        okb16 &= _raises_tx(bus16.transact, a, f, d, timeout=0.01, retries=0)
        okb16 &= _raises_tx(bus16.transact_full, a, f, d, timeout=0.01,
                            retries=0)
        okb16 &= _raises_tx(bus16.raw_send, a, f, d, expect_response=False)
        okb16 &= _raises_tx(wrap16.add_send_data,
                            bytes([a, len(d) + 3, 0, f] + list(d)))
    check2("F0 außer [00]/[02]/[03], F2, Ziel 0xF0, 0xEA/0xEB 0x50/51/56 "
           "gesperrt (%d Fälle, alle Sendewege)" % len(blk16),
           okb16 and bus16.ser.written == [] and bus16.stats()["sent"] == 0
           and wrap16.stats()["queue_len"] == 0)
    check2("Erlaubt: F0 [00]/[02]/[03] je Adresse, 0xEA/0xEB lesend, "
           "0x50/0x56 außerhalb 0xEA/0xEB unberührt (%d Fälle)" % len(ok_16),
           all(check_tx_frame(a, f, d) == list(d) for a, f, d in ok_16))
    msgs16 = []
    for a, f, d in ((0x01, 0xF0, [0x06]), (0x01, 0xF2, [0x01]),
                    (0xF0, 0xF2, [0x01]), (0xEB, 0x56, [])):
        try:
            check_tx_frame(a, f, d)
        except TxGuardError as e:
            msgs16.append(str(e))
    check2("Fehlermeldungen F0/F2/0xF0/0xEB nennen Grund + nichts gesendet",
           len(msgs16) == 4 and all("nichts gesendet" in m for m in msgs16)
           and "DB-Page" in msgs16[0] and "EEPROM" in msgs16[1]
           and "0xF0" in msgs16[2] and "Reset" in msgs16[3])
    check2("TX_GUARD_REV gesetzt (Merkmal für k2_transport-Fallback)",
           TX_GUARD_REV >= 20261027)

    # 17. Stufe 2 (18.10.2026): Belt-Whitelist, Bootloader-Adressen, RFID
    def _mv(d, dist):
        return [d] + list(dist.to_bytes(4, "big"))
    blk17, ok17 = [], []
    for a in sorted(TX_BOOTLOADER_ADDRS):
        for f in (0x00, 0x02, 0x06, 0x08, 0x0B, 0x0E, 0x0C, 0xA2, 0xA3, 0xF0):
            blk17 += [(a, f, []), (a, f, [0x00]), (a, f, [0x02])]
    for a in sorted(TX_BELT_ADDRS):
        ok17 += [(a, 0x00, []), (a, 0x06, []), (a, 0x02, [0x03]),
                 (a, 0x04, [0x03] + [0xFF] * 12),
                 (a, 0x08, _mv(0, 1)), (a, 0x08, _mv(1, 1)),
                 (a, 0x08, _mv(1, 12)), (a, 0x08, _mv(0, TX_BELT_MOVE_MAX))]
        blk17 += [(a, 0x00, [0x00]), (a, 0x06, [0x01]), (a, 0x02, []),
                  (a, 0x02, [0x02]), (a, 0x02, [0x13]), (a, 0x02, [0x03, 0]),
                  (a, 0x04, []), (a, 0x04, [0x00]), (a, 0x04, [0x03]),
                  (a, 0x04, [0x02] + [0] * 8), (a, 0x04, [0x03] + [0] * 11),
                  (a, 0x04, [0x03] + [0] * 13), (a, 0x04, [0x10] + [0] * 64),
                  (a, 0x08, []), (a, 0x08, [0x01]), (a, 0x08, _mv(1, 0)),
                  (a, 0x08, _mv(0, 0)), (a, 0x08, _mv(1, TX_BELT_MOVE_MAX + 1)),
                  (a, 0x08, _mv(1, 0xFFFE)), (a, 0x08, _mv(1, 0x01000001)),
                  (a, 0x08, _mv(2, 5)), (a, 0x08, _mv(0xFF, 5)),
                  (a, 0x08, _mv(1, 5) + [0]), (a, 0x08, [1, 0, 5]),
                  (a, 0x00, [0] * 27)]
        blk17 += [(a, f, []) for f in range(0x100)
                  if f not in (0x00, 0x02, 0x04, 0x06, 0x08)]
    ok17 += [(0x11, 0x02, []), (0x11, 0x14, [])]
    blk17 += [(0x11, 0x0B, [0x00]), (0x11, 0x0B, []), (0x11, 0x0B, [0x01]),
              (0x11, 0x02, [0x00]), (0x11, 0x14, [0x00]), (0x11, 0xF0, [0x00]),
              (0x11, 0xA2, []), (0x11, 0x03, [0x01])]
    blk17 += [(a, f, d) for a in (0xFF, 0xFD) for f in (0x02, 0x14)
              for d in ([], [0x00])]
    # Box nutzt 0x02/0x14 unicast; 0xFE/0xFC bleiben frei
    ok17 += [(0x01, 0x02, [0x01]), (0x04, 0x14, []), (0xFE, 0x14, []),
             (0xFC, 0x02, []), (0x81, 0x14, []), (0x84, 0x08, [0x01]),
             (0x20, 0x0B, [0x00]), (0x25, 0xA2, []), (0x89, 0x0E, [0x01])]
    bus17 = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    wrap17 = Serial_485_Wrapper(bus=bus17)
    okb17 = True
    for a, f, d in blk17:
        okb17 &= _raises_tx(check_tx_frame, a, f, d)
        okb17 &= _raises_tx(bus17.transact_full, a, f, d, timeout=0.01,
                            retries=0)
        okb17 &= _raises_tx(bus17.raw_send, a, f, d, expect_response=False)
        okb17 &= _raises_tx(wrap17.add_send_data,
                            bytes([a, len(d) + 3, 0, f] + list(d)))
    check2("Stufe 2: Bootloader 0x23/0x24/0x85-0x88, Belt-Whitelist, RFID "
           "0x11, 0x02/0x14 an 0xFF/0xFD gesperrt (%d Fälle, alle Sendewege)"
           % len(blk17),
           okb17 and bus17.ser.written == [] and bus17.stats()["sent"] == 0
           and wrap17.stats()["queue_len"] == 0)
    check2("Stufe 2 erlaubt: Belt 0/6 leer, 2 [03], 4 [03]+12, 8 Weg 1..%d; "
           "RFID 0x02/0x14; Box/Motor/Gruppen unberührt (%d Fälle)"
           % (TX_BELT_MOVE_MAX, len(ok17)),
           all(check_tx_frame(a, f, d) == list(d) for a, f, d in ok17))
    # Live-Frames (RS485-Mitschnitt / filament_rack) gehen byte-genau raus
    bus17b = Serial485("/dev/ttyFAKE1", ser=FakeSerial([]))
    for a, f, d, st in ((0x21, 0x00, [], 0), (0x21, 0x02, [0x03], 0),
                        (0x22, 0x06, [], 0), (0x22, 0x08, _mv(1, 12), 0),
                        (0x11, 0x02, [], STATUS_OPERATIONAL)):
        bus17b.raw_send(a, f, d, expect_response=False, status=st)
    check2("Stufe 2: Belt-/Rack-Frames byte-genau gesendet",
           bus17b.ser.written[0] == build_frame(0x21, 0x00, [])
           and bus17b.ser.written[3] == build_frame(0x22, 0x08, _mv(1, 12))
           and bus17b.ser.written[4] == bytes.fromhex(
               "f7 11 03 ff 02") + bytes([crc8_cal([3, 0xFF, 2])])
           and len(bus17b.ser.written) == 5)
    msgs17 = []
    for a, f, d in ((0x23, 0x0B, [0x01]), (0x21, 0x08, _mv(1, 51)),
                    (0x21, 0x04, [0x00]), (0x11, 0x0B, [0x00]),
                    (0xFF, 0x02, [])):
        try:
            check_tx_frame(a, f, d)
        except TxGuardError as e:
            msgs17.append(str(e))
    check2("Stufe 2: Fehlermeldungen nennen Grund + nichts gesendet",
           len(msgs17) == 5 and all("nichts gesendet" in m for m in msgs17)
           and "Bootloader" in msgs17[0] and "1..50" in msgs17[1]
           and "[03]" in msgs17[2] and "0x0B" in msgs17[3]
           and "RFID" in msgs17[4])

    print("SELBSTTEST: %d/%d ok" % (total - fails, total))
    return fails


if __name__ == "__main__":
    raise SystemExit(selftest())
