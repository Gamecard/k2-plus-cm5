#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
K2-CM5-Träger Alpha 1 – Generator (23.10.2026)

Eigene Trägerplatine für das Raspberry Pi Compute Module 5 (CM5) am Platz der T113-Platine (Upper-Board)
des Creality K2 Plus („Weg B“, siehe ../KONZEPT.md). Ersetzt CM5-IO-Board, Stapelleiste, USB-RS485-Adapter,
Pi-Netzteil und Zusatzkabel. Kabel: nur das Original-Flachkabel (J1, 50-pol. FFC 0,5 mm) und 24 V (J7, XT30).
4 Lagen (JLC04161H-7628): L1 Signale/Bauteile, L2 GND, L3 GND + 5-V-Fläche, L4 Kleinsignal/Bauteile unten.

  schaltplan           -> Projektdatei, Bibliothekstabellen, Footprints, Symbole, Schaltplan
  pruefe-netzliste N   -> Netzlisten-Wächter (Soll-Belegung + Sicherheitsregeln)
  pruefe-cm5 N         -> CM5-Pin-Wächter (Netz für Netz gegen die Datenblatt-Pinliste cm5_pins.json)
  platine N            -> Leiterplatte aus der Netzliste (platziert, feste Leitungen, DSN)
  freerouting ...      -> Freerouting-Varianten
  fertigstellen SES    -> Freerouting-Ergebnis einlesen, GND-/Versorgungsflächen, Stitching-Vias
  pruefe-lage          -> Bohrungen, J1-Lage, CM5-Lage, keine Bauteile unter dem CM5
  pruefe-hoehe         -> Höhen-Wächter (jedes Bauteil gegen das Höhenbudget, Datenblatt-Höhen; 3D nur Info)
  pruefe-paare         -> Längenabgleich der USB-/Ethernet-Paare (<= 0,15 mm)
  pruefe-siebdruck     -> Siebdruck-Wächter
  jlc-dateien          -> JLCPCB-Bestückungsdateien (BOM + CPL)

Normalerweise nicht direkt aufrufen, sondern ../bauen.sh benutzen.
"""
import json
import math
import sys
import uuid
from pathlib import Path

# =============================================================================
#  PARAMETER – MECHANIK (Draufsicht von OBEN, x nach rechts, y nach unten; Koordinaten wie V1.5,
#  die Platine ist um 10 mm Richtung Bridge-Board/Flachkabel verlängert: x −10 … 80)
#  Kanten: A = links (x = −10, Flachkabel), B = oben (y = 0, Bohrung L1), C = rechts (x = 80), D = unten (y = 56,1)
# =============================================================================
X_LINKS = -10.0
X_RECHTS = 80.0
PLATINE_HOEHE = 56.1
ECKENRADIUS = 2.0
T113_LOECHER = [(8.80, 4.15), (76.90, 4.21), (3.01, 53.49), (63.41, 53.41)]    # L1 … L4 (V1.5, B)
T113_BOHRUNG = 3.5
T113_KOPF = 5.5              # Schraubenkopf M3 (an L1 höchstens 5,5 mm)
T113_FREI = 6.5              # Freihaltekreis: kein Kupfer, keine Bauteile

# CM5 (55 x 40 mm) um 90° gedreht: Footprint-Ursprung = CM5-Befestigungsloch (CM4IO-Koordinaten (0|0))
CM5_URSPRUNG = (60.4, 46.0)        # Modul x 8,9 … 63,9 / y 9,5 … 49,5
CM5_DREHUNG = 90
CM5_LOCH_D = 2.7
CM5_SCHRAUBE_UNTEN = 5.5     # Kopf der M2,5-Schraube unten (Flachkopf <= 2 mm hoch): Sperrkreis unten
CM5_ABSTANDSHALTER = 5.0     # runder Abstandshalter 1,5 mm (Ø <= 4,5) oben: Sperrkreis oben
CM5_MONTIERT = 4.94          # Höhe CM5 über Trägeroberseite mit 1,5-mm-Steckverbinder (DS)

# Höhenbudget (über der Auflage-Ebene = Unterseite der Trägerplatine)
HOEHE_GESAMT = 11.5
PLATINE_DICKE = 1.6
HOEHE_UNTEN = 2.0            # gemessene Luft 2,45 mm
# JST GH stehend (BM03B-GHS-TBT): Header 4,2 mm; gesteckt (GHR-03V-S) 7,3 mm laut eGH.pdf „Assembly layout“;
# + gerader Kabelaustritt ~1 mm und Bogen R 2–3 mm (AWG 26–30, Schätzung) -> Planwert 12,0 mm über der Platine
GH_GESTECKT = 12.0
HOEHE_J10 = 9.0              # stehende USB-C-Buchse gesamt

# =============================================================================
#  FFC-BUCHSE J1 (XUNPU FPC-05FB-50PH20, LCSC C2856838) – identisch zu V1.5, nur 10 mm nach links
# =============================================================================
FFC_PINS = 50
FFC_RASTER = 0.5
FFC_PAD = (0.30, 1.20)
FFC_PAD_Y = -2.65
FFC_LASCHE = (2.0, 1.8)
FFC_LASCHE_DX = ((FFC_PINS - 1) * FFC_RASTER + 3.1) / 2
FFC_LASCHE_Y = FFC_PAD_Y + 3.25
FFC_GEHAEUSE = ((FFC_PINS - 1) * FFC_RASTER + 4.0, 4.9)
FFC_HOEHE = 2.0
J1_MITTE_Y = 32.85
J1_DREHUNG = 270             # Kabelseite (+y lokal) -> links; Pin 1 (= Breakout 1) zur Unterkante D

PROJEKT = "k2-cm5-traeger"
TITEL = "K2-CM5-Träger Alpha 1"
STAND = "Alpha 1"
DATUM = "2026-10-23"
DATUM_DE = "23.10.2026"
FP_LIB = "k2_traeger"
FP_J1 = "FFC_XUNPU_FPC-05FB-50PH20_1x50_P0.5mm_Horizontal"
FP_CM5 = "CM5_Modul_2xDF40C-100DS-0.4V51_1.5mm"
FP_BOHRUNG = "Bohrung_3.5mm_T113_Kopf5.5mm"
FP_HALTER = "Sicherungshalter_Littelfuse_157_Nano2_2410"
WARNUNG_LANG = "ALPHA – NUR FÜR TESTZWECKE – NICHT FÜR KOMMERZIELLEN EINSATZ – NUTZUNG AUF EIGENE GEFAHR"
WARNUNG_KURZ = "ALPHA · NUR TEST · NICHT KOMMERZIELL · EIGENE GEFAHR"
URSPRUNG = (50.0, 40.0)      # Versatz in der KiCad-Datei (x = −10 liegt dann bei 40 mm)

# =============================================================================
#  BELEGUNG J1 (J1-Pin n = Breakout-Nummer n, wie V1.5; CON2-Nummer = 51 − n)
# =============================================================================
BR_GND_GEMESSEN = [3, 6, 16, 18, 21, 23, 25, 27, 29, 31, 33, 36, 39, 40, 41, 42, 43, 44]
BR_GND = [n for n in BR_GND_GEMESSEN if (51 - n) in BR_GND_GEMESSEN]     # -> [18, 33] (spiegelsicher)
BR_5V = [45, 46, 47, 48, 49, 50]
BR_5V_SIGNALE = [11, 12, 14, 15, 17]
BR_ETH = {1: "ETH_A_P", 2: "ETH_A_N", 4: "ETH_B_P", 5: "ETH_B_N"}         # Leitungsseite des Übertragers
BR_RS485 = {19: "RS485_B", 20: "RS485_A"}
BR_UART = {26: "B26_UART2_TX", 28: "B28_UART2_RX", 30: "B30_UART4_TX", 32: "B32_UART4_RX"}
BR_USB = {37: "CAM_DM", 38: "CAM_DP", 34: "HUB_DM", 35: "HUB_DP"}

# CM5-Pins (DS §4.2) -> Netz. Alles, was hier fehlt (außer GND/5V/3V3), bleibt offen.
CM5_SIGNALE = {
    4: "ETH1_P", 6: "ETH1_N", 10: "ETH0_N", 12: "ETH0_P",       # MDI-Paar 1 / 0 (100BASE-TX)
    15: "ETH_NLED3", 17: "ETH_NLED2", 21: "LED_NACT",
    # 24 (GPIO26) und 30 (GPIO6): seit 24.10.2026 FREI (vorher Toolhead-eFuse ~FLT/Abschaltung, J9 entfallen)
    48: "PWR_FAIL_N",                                             # GPIO27 <- INA226 0x40 ALERT (Stromausfall)
    28: "UART4_RX", 31: "UART4_TX", 34: "UART2_RX", 54: "UART2_TX",   # GPIO13/12/5/4
    55: "UART0_TX", 51: "UART0_RX", 50: "RS485_DE",              # GPIO14/15/17
    44: "CAN_MOSI", 40: "CAN_MISO", 38: "CAN_SCK", 39: "CAN_CS", 41: "CAN_INT",
    58: "I2C1_SDA", 56: "I2C1_SCL",
    78: "CM5_3V3",                                                # GPIO_VREF an CM5_3.3V (DS: Pflicht)
    92: "PWR_BUT", 93: "NRPIBOOT", 94: "CC1_CM5", 96: "CC2_CM5", 99: "PMIC_EN",
    103: "OTG_DM", 105: "OTG_DP",
    175: "DSI1_D0_N", 177: "DSI1_D0_P", 181: "DSI1_D1_N", 183: "DSI1_D1_P", 187: "DSI1_C_N", 189: "DSI1_C_P",
    35: "DSI_SCL", 36: "DSI_SDA", 97: "CAM_GPIO0", 100: "CAM_GPIO1",          # I2C von MIPI1 = ID_SC/ID_SD (DS 2.6.2)
    134: "CAM_DP", 136: "CAM_DM", 163: "HUB_DP", 165: "HUB_DM",
}
CM5_5V_PINS = [77, 79, 81, 83, 85, 87]
CM5_3V3_PINS = [84, 86]
# Erwartung je Datenblatt-Name (unabhängige Gegenprüfung im CM5-Pin-Wächter)
CM5_ERWARTET = {
    "GND": "GND", "5V (Input)": "CM5_5V", "CM5_3.3V (Output)": "CM5_3V3", "GPIO_VREF": "CM5_3V3",
    "Ethernet_Pair0_P": "ETH0_P", "Ethernet_Pair0_N": "ETH0_N", "Ethernet_Pair1_P": "ETH1_P",
    "Ethernet_Pair1_N": "ETH1_N", "Ethernet_nLED3": "ETH_NLED3", "Ethernet_nLED2": "ETH_NLED2",
    "LED_nACT": "LED_NACT", "GPIO4": "UART2_TX", "GPIO5": "UART2_RX", "GPIO12": "UART4_TX", "GPIO13": "UART4_RX",
    "GPIO14": "UART0_TX", "GPIO15": "UART0_RX", "GPIO17": "RS485_DE", "GPIO10": "CAN_MOSI", "GPIO9": "CAN_MISO",
    "GPIO11": "CAN_SCK", "GPIO8": "CAN_CS", "GPIO25": "CAN_INT", "GPIO2": "I2C1_SDA", "GPIO3": "I2C1_SCL",
    "GPIO27": "PWR_FAIL_N", "PWR_Button": "PWR_BUT", "nRPIBOOT": "NRPIBOOT", "CC1": "CC1_CM5",
    "CC2": "CC2_CM5", "PMIC_Enable": "PMIC_EN", "USB_N": "OTG_DM", "USB_P": "OTG_DP",
    "USB3-0-DP": "CAM_DP", "USB3-0-DM": "CAM_DM", "USB3-1-DP": "HUB_DP", "USB3-1-DM": "HUB_DM",
    "MIPI1_D0_N": "DSI1_D0_N", "MIPI1_D0_P": "DSI1_D0_P", "MIPI1_D1_N": "DSI1_D1_N", "MIPI1_D1_P": "DSI1_D1_P",
    "MIPI1_C_N": "DSI1_C_N", "MIPI1_C_P": "DSI1_C_P", "ID_SC": "DSI_SCL", "ID_SD": "DSI_SDA",
    "CAM_GPIO0": "CAM_GPIO0", "CAM_GPIO1": "CAM_GPIO1",
}

PASSERMARKEN = [(-7.8, 52.0), (40.0, 2.0), (78.2, 54.6)]
PASSERMARKEN_UNTEN = [(-7.5, 52.0), (61.5, 1.8), (78.8, 43.0)]

HIER = Path(__file__).resolve().parent
PROJ = HIER.parent
SYMDIR = Path("/usr/share/kicad/symbols")
FPDIR = Path("/usr/share/kicad/footprints")
MODDIR = Path("/usr/share/kicad/3dmodels")
NS = uuid.UUID("5b1d7c2e-8a43-4f0e-9c61-2e7a4d9b3f18")


def uid(*teile):
    return str(uuid.uuid5(NS, "/".join(str(t) for t in teile)))


ROOT_UUID = uid("root-sheet")


def q(s):
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n") + '"'


def f(v):
    return f"{v:.4f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def ffc_pad_lokal(n):
    x = (FFC_PINS - 1) * FFC_RASTER / 2 - (n - 1) * FFC_RASTER
    return (round(x, 4), FFC_PAD_Y)


def j1_ursprung():
    """Gehäusemitte J1: Vorderkante bündig mit der Kante A (x = −10)."""
    return (X_LINKS + FFC_GEHAEUSE[1] / 2, J1_MITTE_Y)


def cm5_pad_lokal(n):
    """CM5-Pad n in Footprint-Koordinaten (offizielle CM4/CM5-IO-Geometrie, Ursprung = Befestigungsloch):
    Steckverbinder A: ungerade x = −2,00, gerade x = +1,08; B: ungerade x = 31,92, gerade x = 35,00;
    Pins 1/2 bzw. 101/102 bei y = −31,3, Raster 0,4 mm in +y."""
    if n <= 100:
        k = (n - 1) // 2
        x = -2.0 if n % 2 else 1.08
    else:
        k = (n - 101) // 2
        x = 31.92 if n % 2 else 35.0
    return (x, round(-31.3 + 0.4 * k, 4))


def cm5_pad(n):
    """CM5-Pad n auf der Platine (Draufsicht). Drehung 90°: (x, y) -> (y, −x)."""
    lx, ly = cm5_pad_lokal(n)
    return (round(CM5_URSPRUNG[0] + ly, 4), round(CM5_URSPRUNG[1] - lx, 4))


def cm5_umriss():
    """Modulumriss (55 x 40 mm) auf der Platine: x0, y0, x1, y1."""
    return (CM5_URSPRUNG[0] - 51.5, CM5_URSPRUNG[1] - 36.5, CM5_URSPRUNG[0] + 3.5, CM5_URSPRUNG[1] + 3.5)


# =============================================================================
#  Bauteile + Soll-Netzliste
# =============================================================================
class Teil:
    def __init__(self, ref, lib, wert, fp, pins, gruppe, bem="", dnp=False, in_bom=True,
                 beschr="", hinweise=None, bestueckung="selbst", lcsc="", mpn="", hersteller="",
                 on_board=True, hoehe=0.0, unten_tht=0.0, gesteckt=None):
        self.ref, self.lib, self.wert, self.fp = ref, lib, wert, fp
        self.on_board = on_board
        self.pins = pins
        self.gruppe = gruppe
        self.sch = None
        self.bem, self.dnp, self.in_bom, self.beschr = bem, dnp, in_bom, beschr
        self.hinweise = hinweise or {}
        self.verdrahtet = set()
        self.bestueckung = bestueckung
        self.lcsc, self.mpn, self.hersteller = lcsc, mpn, hersteller
        self.hoehe = hoehe              # Bauhöhe über der Bestückungsseite (Datenblatt) [mm]
        self.unten_tht = unten_tht      # THT: Überstand der Anschlüsse unter der Unterseite [mm]
        self.gesteckt = gesteckt        # stehende Stecker: Höhe mit Gegenstecker + Kabelaustritt [mm] (nur Hinweis)


R_FP = "Resistor_SMD:R_0805_2012Metric"
C_FP = "Capacitor_SMD:C_0805_2012Metric"
C1206_FP = "Capacitor_SMD:C_1206_3216Metric"
LED_FP = "LED_SMD:LED_0805_2012Metric"
TP_FP = "TestPoint:TestPoint_Pad_D1.5mm"
SHUNT_FP = "Resistor_SMD:R_2512_6332Metric"
R_BEM = "Dickschicht 0805, 1 %, 125 mW"
HERSTELLER = "Hersteller (JLCPCB SMT)"
# LCSC-Nummern: per JLCPCB-Teilesuche am 23.10.2026 geprüft (Bestand ändert sich – vor Bestellung erneut prüfen)
SMD_TEILE = {
    "1k": ("C17513", "0805W8F1001T5E", "UNI-ROYAL", 0.6),
    "2k2": ("C17520", "0805W8F2201T5E", "UNI-ROYAL", 0.6),
    "120": ("C17437", "0805W8F1200T5E", "UNI-ROYAL", 0.6),
    "470": ("C17710", "0805W8F4700T5E", "UNI-ROYAL", 0.6),
    "10k": ("C17414", "0805W8F1002T5E", "UNI-ROYAL", 0.6),
    "100k": ("C149504", "0805W8F1003T5E", "UNI-ROYAL", 0.6),
    "3k6": ("", "0805W8F3601T5E", "UNI-ROYAL", 0.6),
    "18k7": ("C17499", "0805W8F1872T5E", "UNI-ROYAL", 0.6),
    "33k": ("C17633", "0805W8F3302T5E", "UNI-ROYAL", 0.6),
    "75": ("C17820", "0805W8F750JT5E", "UNI-ROYAL", 0.6),
    "5k1": ("C27834", "0805W8F5101T5E", "UNI-ROYAL", 0.6),
    "105k": ("C17410", "0805W8F1053T5E", "UNI-ROYAL", 0.6),
    "19k1": ("C17508", "0805W8F1912T5E", "UNI-ROYAL", 0.6),
    "rot": ("C84256", "NCD0805R1", "Foshan NationStar", 1.1),
    "grün": ("C2297", "KT-0805G", "Hubei KENTO", 1.1),
    "gelb": ("C2296", "KT-0805Y", "Hubei KENTO", 1.1),
    "100n": ("C49678", "CC0805KRX7R9BB104", "YAGEO", 1.0),
    "10u": ("C15850", "CL21A106KAYNNNE", "Samsung Electro-Mechanics", 1.35),
    "22p": ("C1804", "CL21C220JBANNNC", "Samsung Electro-Mechanics", 1.0),
    "33p": ("C107115", "CC0805JRNPO9BN330", "YAGEO", 1.0),
    "22n": ("C107147", "CC0805KRX7R9BB223", "YAGEO", 1.0),
    "10u50": ("C13585", "CL31A106KBHNNNE", "Samsung Electro-Mechanics", 1.8),
    "22u25": ("C12891", "CL31A226KAHNNNE", "Samsung Electro-Mechanics", 1.8),
    "1n2kV": ("C9196", "1206B102K202NT", "FH (Guangdong Fenghua)", 1.4),
    "5m": ("C185817", "RLM25FEER005", "Ralec", 0.65),
    "10m": ("C2962784", "HoLLR2512-2W-10mR-1%", "Hongtai", 0.65),
    "10A": ("C136354", "JFC1206-2100FS", "Jinrui", 1.1),
    # Littelfuse 157: Nano²-Clips MIT eingesetzter 10-A-Sicherung (453, sehr flink), Sicherung von Hand tauschbar;
    # 6,50 × 3,70 × 3,90 mm, 10 A / 125 V AC/DC, Abschaltvermögen 300 A @ 32 V DC (Datenblatt Rev. 09/24/21)
    "F157": ("C187520", "0157003.DR", "Littelfuse", 3.9),     # 3 A (Weg A, 24.10.2026; vorher 0157010.DR 10 A)
    "PTC": ("C12559", "SMD1812P050TF/30", "PTTC (Polytronics)", 1.4),
    "SMFJ28A": ("C5439963", "SMFJ28A", "BORN", 1.1),
    "SMF5.0A": ("C193402", "SMF5.0A", "MDD (Microdiode)", 1.1),
    "MCP2518FD": ("C626759", "MCP2518FDT-H/SL", "Microchip", 1.75),
    "SN65HVD230": ("C12084", "SN65HVD230DR", "Texas Instruments", 1.75),
    "40MHz": ("C426990", "S3240000151040", "JGHC", 0.8),
    "NUP2105L": ("C14486", "NUP2105LT1G", "onsemi", 1.1),
    "THVD1450": ("C2671361", "THVD1450DR", "Texas Instruments", 1.75),
    "INA226": ("C49851", "INA226AIDGSR", "Texas Instruments", 1.1),
    "SM712": ("C502564", "SM712", "MDD (Microdiode)", 1.1),
    "USBLC6": ("C7519", "USBLC6-2SC6", "STMicroelectronics", 1.45),
    "SRV05-4": ("C5240367", "SRV05-4", "TWGMC", 1.45),
    "TPD4E05U06": ("C138714", "TPD4E05U06DQAR", "Texas Instruments", 0.55),
    "LVC1G125": ("C23654", "SN74LVC1G125DBVR", "Texas Instruments", 1.45),
    "2N7002": ("C8545", "2N7002", "Changjiang Electronics", 1.1),
    "LMR51450": ("C6890985", "LMR51450SDRRR", "Texas Instruments", 0.8),
    "4u7": ("C167220", "FXL0630-4R7-M", "cjiang (Changjiang Microelectronics)", 3.0),
    "TPS2121": ("C485916", "TPS2121RUXR", "Texas Instruments", 1.0),
    "TPS26632": ("C1850278", "TPS26632RGER", "Texas Instruments", 1.0),
    "H1102": ("C18584", "H1102NLT", "Pulse Electronics", 6.35),
    "DF40": ("C597931", "DF40C-100DS-0.4V(51)", "Hirose (HRS)", 1.5),
    "FFC50": ("C2856838", "FPC-05FB-50PH20", "XUNPU", 2.0),
    "USBC": ("C5187468", "TYPE-C 16P LTH6.8", "SHOU HAN", 6.8),
    "HDR2": ("C41417359", "HX PZ2.54-1x2P TP-YQ", "HX (Hanxia)", 8.5),
    "GH3": ("C514175", "SM03B-GHS-TB(LF)(SN)", "JST", 4.25),
    "GH3V": ("C161691", "BM03B-GHS-TBT(LF)(SN)", "JST", 4.2),      # stehend (Top entry), eGH.pdf: 4,05 + 0,15 mm
    "FH12-22": ("C597981", "FH12-22S-0.5SH(55)", "Hirose (HRS)", 2.0),
    "TLV62569": ("C141836", "TLV62569DBVR", "Texas Instruments", 1.45),
    "2u2": ("C97031", "WPN252010H2R2MT", "lt. LCSC C97031", 1.0),
    "22u6": ("C5674", "CL21A226MQQNNNE", "Samsung Electro-Mechanics", 1.35),
    "22k": ("C17560", "0805W8F2202T5E", "UNI-ROYAL", 0.6),
    "XT30": ("C431092", "XT30PW-M30.G.Y", "Changzhou Amass", 5.0),
    "MX4": ("C3294383", "HC-MX3.0-2*2AW", "HCTL", 8.76),
}
# bei JLC als Basic geführt (sonst Extended)
BASIC_LCSC = {"C17513", "C17520", "C17437", "C17710", "C17414", "C149504", "C17633", "C27834", "C84256", "C2297",
              "C2296", "C49678", "C15850", "C1804", "C13585", "C12891", "C9196", "C8545", "C26010", "C17560"}


def smd(schluessel):
    lcsc, mpn, herst, h = SMD_TEILE[schluessel]
    return dict(bestueckung=HERSTELLER, lcsc=lcsc, mpn=mpn, hersteller=herst, hoehe=h)


def bauteile():
    T = []
    drähte = []

    def R(ref, wert, a, b, gr, beschr, fp=R_FP, bem=R_BEM, **kw):
        T.append(Teil(ref, "Device:R", wert, fp, {"1": a, "2": b}, gr, bem=bem, beschr=beschr,
                      **(kw or smd(wert))))

    def C(ref, wert, a, b, gr, beschr, fp=C_FP, bem=None):
        bem = bem or {"100n": "100 nF 50 V X7R 0805", "10u": "10 µF 25 V X5R 0805", "22p": "22 pF C0G 0805",
                      "22u6": "22 µF 6,3 V X5R 0805",
                      "33p": "33 pF C0G 0805", "22n": "22 nF 50 V X7R 0805", "10u50": "10 µF 50 V X5R 1206",
                      "22u25": "22 µF 25 V X5R 1206", "1n2kV": "1 nF 2 kV X7R 1206"}[wert]
        T.append(Teil(ref, "Device:C", wert, fp, {"1": a, "2": b}, gr, bem=bem, beschr=beschr, **smd(wert)))

    def LED(ref, farbe, anode, kathode, gr, beschr):
        T.append(Teil(ref, "Device:LED", f"LED 0805 {farbe}", LED_FP, {"1": kathode, "2": anode}, gr,
                      bem=f"LED 0805 {farbe}, Kathode = Pad 1", beschr=beschr, **smd(farbe)))

    def JP(ref, a, b, wert, offen, gr, bem, beschr, fp=None):
        art = "Open" if offen else "Bridged"
        T.append(Teil(ref, f"Jumper:SolderJumper_2_{art}", wert,
                      fp or f"Jumper:SolderJumper-2_P1.3mm_{art}_RoundedPad1.0x1.5mm", {"1": a, "2": b}, gr,
                      in_bom=False, bem=bem, beschr=beschr, bestueckung="– (Lötbrücke)"))

    def TP(ref, net, kurz):
        T.append(Teil(ref, "Connector:TestPoint", kurz, TP_FP, {"1": net}, "Messpunkte", in_bom=False,
                      bem="Messpad Ø 1,5 mm (kein Bauteil)", beschr=f"Messpunkt {net}",
                      bestueckung="– (Pad)"))

    # ---------------- J1: Original-Flachkabel ----------------
    j1, hinweise = {}, {}
    for p in range(1, 51):
        n = None
        if p in BR_GND:
            n = "J1_GND"
        elif p in BR_5V:
            n = "BR_5V"
        for tab in (BR_ETH, BR_RS485, BR_UART, BR_USB):
            n = tab.get(p, n)
        if p in BR_5V_SIGNALE:
            hinweise[p] = "5-V-Pegel – NIE anschließen"
        elif p in BR_GND_GEMESSEN and p not in BR_GND:
            hinweise[p] = f"GND, Spiegel {51 - p} kein GND -> NC"
        j1[str(p)] = n
    T.append(Teil("J1", "Connector_Generic:Conn_01x50", "FPC-05FB-50PH20", f"{FP_LIB}:{FP_J1}", j1, "Flachkabel",
                  bem="FFC/FPC-Buchse 50P 0,5 mm, Klappbügel, Kontakte oben+unten, H 2,0 mm (wie V1.5)",
                  beschr="Original-Flachkabel vom Bridge-Board an Kante A; Pin n = Breakout n, Pin 1 zur Kante D",
                  hinweise=hinweise, **smd("FFC50")))
    T.append(Teil("F3", "Device:Polyfuse", "PTC 0,5A 30V", "Fuse:Fuse_1812_4532Metric",
                  {"1": "J1_GND", "2": "GND"}, "Flachkabel",
                  bem="PTC 0,5 A Halte-/1 A Auslösestrom, 30 V, 1812, UNTERSEITE",
                  beschr="Schützt die zwei GND-Adern des Flachkabels (J1 18/33), falls die GND-Leitung am 24-V-"
                         "Stecker J7 fehlt und der Rückstrom sonst über das Flachkabel fließt", **smd("PTC")))
    # ---------------- J2: CM5 (beide DF40-Steckverbinder in einem Footprint) ----------------
    pins_cm5 = json.loads((HIER / "cm5_pins.json").read_text(encoding="utf-8"))["pins"]
    j2 = {}
    h2 = {24: "GPIO26 frei (ehem. Toolhead ~FLT)", 30: "GPIO6 frei (ehem. Toolhead-Abschaltung)"}
    for p in range(1, 201):
        name = pins_cm5[str(p)][0]
        n = None
        if name == "GND":
            n = "GND"
        elif p in CM5_5V_PINS:
            n = "CM5_5V"
        elif p in CM5_3V3_PINS:
            n = "CM5_3V3"
        n = CM5_SIGNALE.get(p, n)
        j2[str(p)] = n
    T.append(Teil("J2", f"{FP_LIB}:CM5", "CM5 (2x DF40C-100DS-0.4V(51))", f"{FP_LIB}:{FP_CM5}", j2, "CM5",
                  bem="2x Hirose DF40C-100DS-0.4V(51) (1,5 mm Stapelhöhe), Lage/Pads nach offiziellen CM-IO-"
                      "Designdaten; 4x M2,5 (Ø 2,7, ohne Kupfer)",
                  beschr="Raspberry Pi Compute Module 5 (Pin-Namen nach CM5-Datenblatt)", hinweise=h2,
                  **smd("DF40")))
    # ---------------- Ethernet: eigener 10/100-Übertrager T1 ----------------
    T.append(Teil("T1", "Transformer:PT61017PEL", "H1102NLT", f"{FP_LIB}:Uebertrager_SMD_H1102NLT_12.7x7.1mm",
                  {"1": "ETH1_P", "2": "ETH_CT_PHY", "3": "ETH1_N", "6": "ETH0_N", "7": "ETH_CT_PHY", "8": "ETH0_P",
                   "9": "ETH_B_N", "10": "ETH_CT_B", "11": "ETH_B_P", "14": "ETH_A_N", "15": "ETH_CT_A",
                   "16": "ETH_A_P"}, "Ethernet",
                  bem="10/100-Übertrager 1:1, SMD 16-pol. (Industriestandard-Belegung TD/RD 1–8, TX/RX 9–16 wie "
                      "Bourns PT61017PEL); Paar 0 bewusst P/N getauscht (DS: automatische Polaritätskorrektur)",
                  beschr="Ethernet CM5-PHY (Paar 1 -> TD, Paar 0 -> RD) -> FFC 1/2 und 4/5 (100BASE-TX)",
                  **smd("H1102")))
    C("C27", "100n", "ETH_CT_PHY", "GND", "Ethernet", "Mittelanzapfungen PHY-Seite (wie CM4IO: CT gemeinsam an C nach GND)")
    R("R36", "75", "ETH_CT_A", "ETH_BOB", "Ethernet", "Bob-Smith-Abschluss Leitungsseite Paar A")
    R("R37", "75", "ETH_CT_B", "ETH_BOB", "Ethernet", "Bob-Smith-Abschluss Leitungsseite Paar B")
    C("C28", "1n2kV", "ETH_BOB", "GND", "Ethernet", "Bob-Smith-Kondensator 1 nF / 2 kV", fp=C1206_FP)
    T.append(Teil("D7", "Power_Protection:TPD4E05U06DQA", "TPD4E05U06", "Package_SON:USON-10_2.5x1.0mm_P0.5mm",
                  {"1": "ETH1_P", "10": "ETH1_P", "2": "ETH1_N", "9": "ETH1_N", "3": "GND", "8": "GND",
                   "4": "ETH0_P", "7": "ETH0_P", "5": "ETH0_N", "6": "ETH0_N"}, "Ethernet",
                  bem="ESD 4 Kanäle 0,5 pF, USON-10, UNTERSEITE; Durchgangs-Layout (1–10, 2–9, 4–7, 5–6)",
                  beschr="ESD-Schutz MDI-Paare 0/1 auf der CM5-Seite (wie CM4IO)", **smd("TPD4E05U06")))
    # ---------------- USB (Kamera, Hub) vom Flachkabel ----------------
    for ref, a, b, kurz in (("D1", "CAM_DP", "CAM_DM", "Kamera"), ("D2", "HUB_DP", "HUB_DM", "Hub")):
        T.append(Teil(ref, "Power_Protection:USBLC6-2SC6", "USBLC6-2SC6", "Package_TO_SOT_SMD:SOT-23-6",
                      {"1": a, "6": a, "2": "GND", "3": b, "4": b, "5": None}, "USB",
                      bem="ESD USB-Paar, SOT-23-6, OBERSEITE, Durchgang 1–6 / 3–4; VBUS (Pin 5) frei: bei "
                          "verdrehtem Flachkabel keine Rückspeisung in die CM5-3,3 V",
                      beschr=f"ESD USB {kurz} (FFC-Seite)", **smd("USBLC6")))
    # ---------------- Notfall-USB-C J10 (OTG, rpiboot) ----------------
    T.append(Teil("J10", "Connector:USB_C_Receptacle_USB2.0_16P", "USB-C stehend",
                  f"{FP_LIB}:USB_C_Buchse_stehend_16P_TYPE-C-16P-LTH6.8",
                  {"A1": "GND", "A12": "GND", "B1": "GND", "B12": "GND", "A4": "J10_VBUS", "A9": "J10_VBUS",
                   "B4": "J10_VBUS", "B9": "J10_VBUS", "A5": "J10_CC1", "B5": "J10_CC2", "A6": "OTG_DP",
                   "B6": "OTG_DP", "A7": "OTG_DM", "B7": "OTG_DM", "A8": None, "B8": None, "SH": "GND"}, "USB-C",
                  bem="USB-C-Buchse 16-pol. STEHEND (Stecker von oben), H 6,8 mm, SMD + Schirmlaschen",
                  beschr="Notfall-USB-C an Kante B nahe L1: USB-2.0-OTG des CM5 (rpiboot/Gadget), 5-V-Notversorgung",
                  **smd("USBC")))
    T.append(Teil("D3", "Power_Protection:USBLC6-2SC6", "USBLC6-2SC6", "Package_TO_SOT_SMD:SOT-23-6",
                  {"1": "OTG_DM", "6": "OTG_DM", "2": "GND", "3": "OTG_DP", "4": "OTG_DP", "5": None}, "USB-C",
                  bem="ESD USB-Paar J10, SOT-23-6, UNTERSEITE; VBUS-Pin offen (wie D1/D2, Pin 5 liegt zwischen den Durchführungen)", beschr="ESD Notfall-USB-C",
                  **smd("USBLC6")))
    T.append(Teil("D15", "Device:D_TVS", "SMF5.0A", "Diode_SMD:D_SOD-123F", {"1": "J10_VBUS", "2": "GND"}, "USB-C",
                  bem="TVS 5 V unidirektional, SOD-123F, UNTERSEITE; Kathode = Pad 1", beschr="Überspannung VBUS J10",
                  **smd("SMF5.0A")))
    JP("JP6", "J10_CC1", "CC1_CM5", "CC1", False, "USB-C",
       "Lötbrücke, Standard GESCHLOSSEN: CC1 der Buchse an CM5-CC1 (wie IO-Board, CM5 handelt 5 V aus)",
       "CC1 zum CM5")
    JP("JP7", "J10_CC2", "CC2_CM5", "CC2", False, "USB-C",
       "Lötbrücke, Standard GESCHLOSSEN: CC2 der Buchse an CM5-CC2", "CC2 zum CM5")
    for ref, cc in (("R43", "J10_CC1"), ("R44", "J10_CC2")):
        T.append(Teil(ref, "Device:R", "5k1", R_FP, {"1": cc, "2": "GND"}, "USB-C", dnp=True,
                      bem=R_BEM + " – BESTÜCKOPTION (DNP): nur zusammen mit offener JP6/JP7",
                      beschr="Rückfall Rd 5,1 kΩ, falls die CM5-CC-Logik ohne PD-Netzteil nicht anspringt",
                      **smd("5k1")))
    JP("JP5", "J10_VBUS", "MUX_IN2", "VBUS", False, "USB-C",
       "Lötbrücke, Standard GESCHLOSSEN: 5-V-Notversorgung über USB-C (Power-Mux U8, keine Rückspeisung). "
       "Auftrennen = J10 nur Daten", "VBUS zum Power-Mux")
    T.append(Teil("JP_BOOT", "Connector_Generic:Conn_01x02", "RPIBOOT", f"{FP_LIB}:Stiftleiste_1x2_P2.54mm_SMD_stehend",
                  {"1": "NRPIBOOT", "2": "GND"}, "USB-C",
                  bem="Stiftleiste 1x2 RM 2,54 SMD stehend + Jumper (Jumper statt Taster)",
                  beschr="Boot-Auswahl: Jumper gesteckt = nRPIBOOT an GND (rpiboot über J10)", **smd("HDR2")))
    # ---------------- UART2/UART4: Ioff-Puffer + 1 kΩ + ESD ----------------
    uart = [("U10", "R1", "R11", "UART2_TX", "B26_UART2_TX", "tx", "Düse"),
            ("U11", "R2", "R9", "UART2_RX", "B28_UART2_RX", "rx", "Düse"),
            ("U12", "R3", "R12", "UART4_TX", "B30_UART4_TX", "tx", "Main"),
            ("U13", "R4", "R10", "UART4_RX", "B32_UART4_RX", "rx", "Main")]
    for u, r, rp, cm, br, art, mcu in uart:
        if art == "tx":
            ein, aus, mitte = cm, f"{cm}_PUF", f"{cm}_PUF"
            R(r, "1k", br, mitte, "UART", f"Serienwiderstand Puffer -> J1 ({mcu}-MCU)")
            # 10k statt 100k (24.10.2026): RP1 zieht GPIO9..27 nach dem Reset mit
            # ca. 50 kOhm nach GND -> mit 100k läge der Puffereingang bei ~1,1 V
            # (undefiniert, Main-MCU sähe Rauschen/Break); mit 10k ca. 2,75 V = high.
            R(rp, "10k", "CM5_3V3", cm, "UART", f"Pull-up Puffer-Eingang {cm} (überstimmt RP1-Pull-down beim Booten)")
        else:
            ein, aus = f"{cm}_PUF", cm
            R(r, "1k", br, ein, "UART", f"Serienwiderstand J1 -> Puffer ({mcu}-MCU)")
            R(rp, "100k", ein, "GND", "UART", f"Pull-down Puffer-Eingang (Flachkabel ab) – keine Rückspeisung")
        T.append(Teil(u, "74xGxx:SN74LVC1G125DBV", "SN74LVC1G125", "Package_TO_SOT_SMD:SOT-23-5",
                      {"1": "GND", "2": ein, "3": "GND", "4": aus, "5": "CM5_3V3"}, "UART",
                      bem="Puffer mit Ioff (abgeschaltet hochohmig), SOT-23-5, UNTERSEITE, Versorgung CM5_3V3",
                      beschr=f"{'TX' if art == 'tx' else 'RX'} {mcu}-MCU: Rückspeiseschutz CM5", **smd("LVC1G125")))
    for i, u in enumerate(("U10", "U11", "U12", "U13")):
        C(f"C{30 + i}", "100n", "CM5_3V3", "GND", "UART", f"Abblockung {u}")
    T.append(Teil("D4", "Power_Protection:SRV05-4", "SRV05-4", "Package_TO_SOT_SMD:SOT-23-6",
                  {"1": "B26_UART2_TX", "2": "GND", "3": "B28_UART2_RX", "4": "B30_UART4_TX", "5": None,
                   "6": "B32_UART4_RX"}, "UART",
                  bem="ESD 4 Leitungen, SOT-23-6, UNTERSEITE, Flachkabel-Seite; VP (Pin 5) frei", beschr="ESD UART",
                  **smd("SRV05-4")))
    # ---------------- RS485 (UART0 + DE) ----------------
    T.append(Teil("U3", "Interface_UART:THVD1450D", "THVD1450DR", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                  {"1": "UART0_RX", "2": "RS485_DE", "3": "RS485_DE", "4": "UART0_TX", "5": "GND",
                   "6": "RS485_A", "7": "RS485_B", "8": "CM5_3V3"}, "RS485",
                  bem="RS485-Halbduplex 3,3 V, ±18 kV ESD, SOIC-8, UNTERSEITE; DE/~RE an GPIO17 (RTS, Kernel-RS485)",
                  beschr="RS485 CFS/Motor/Riemen (Breakout 19 = B, 20 = A)", **smd("THVD1450")))
    C("C6", "100n", "CM5_3V3", "GND", "RS485", "Abblockung U3")
    R("R13", "10k", "RS485_DE", "GND", "RS485", "DE/~RE nach GND: ohne CM5 nur Empfang")
    R("R14", "10k", "CM5_3V3", "UART0_RX", "RS485", "Pull-up RO")
    R("R15", "1k", "BR_5V", "RS485_A_BIAS", "RS485", "Ruhevorspannung A (Drucker-5V, wie V1.5)")
    R("R16", "1k", "RS485_B_BIAS", "GND", "RS485", "Ruhevorspannung B")
    R("R17", "120", "RS485_A", "RS485_TERM", "RS485", "Abschluss 120 Ω (über JP1)")
    T.append(Teil("JP_485_TERM", "Connector_Generic:Conn_01x02", "485 120R", f"{FP_LIB}:Stiftleiste_1x2_P2.54mm_SMD_stehend",
                  {"1": "RS485_TERM", "2": "RS485_B"}, "RS485",
                  bem="Stiftleiste 1x2 RM 2,54 SMD + Jumper-Kappe, werkseitig OFFEN (Bus heute über das K2-Mainboard "
                      "abgeschlossen); stecken = 120 Ω zwischen A und B (Mainboard-Ersatz, Träger am Busende)",
                  beschr="RS485-Abschluss (JP_485_TERM)", **smd("HDR2")))
    T.append(Teil("J11", "Connector_Generic:Conn_01x03", "RS485 A/B/GND",
                  "Connector_JST:JST_GH_BM03B-GHS-TBT_1x03-1MP_P1.25mm_Vertical",
                  {"1": "RS485_A", "2": "RS485_B", "3": "GND"}, "RS485",
                  bem="JST GH 3-pol. STEHEND (Top entry, Kabel von oben; 1 = A, 2 = B, 3 = GND), parallel zum "
                      "THVD1450-Busausgang und FFC 19/20; ESD über D5 (SM712); gesteckt ≈ 7,3 mm + Kabelbogen",
                  beschr="RS485-Abgriff: CFS/Motoren/Riemen später direkt ohne K2-Mainboard (Option M8P)",
                  gesteckt=GH_GESTECKT, **smd("GH3V")))
    JP("JP2", "RS485_A_BIAS", "RS485_A", "Bias A", False, "RS485", "Lötbrücke, Standard GESCHLOSSEN", "RS485-Bias A")
    JP("JP3", "RS485_B", "RS485_B_BIAS", "Bias B", False, "RS485", "Lötbrücke, Standard GESCHLOSSEN", "RS485-Bias B")
    T.append(Teil("D5", "Diode:SM712_SOT23", "SM712", "Package_TO_SOT_SMD:SOT-23",
                  {"1": "RS485_A", "2": "RS485_B", "3": "GND"}, "RS485",
                  bem="TVS −7/+12 V für RS485, SOT-23, UNTERSEITE", beschr="Überspannungsschutz RS485",
                  **smd("SM712")))
    # ---------------- CAN (SPI0) ----------------
    T.append(Teil("U1", "Interface_CAN_LIN:MCP2517FD-xSL", "MCP2518FD", "Package_SO:SOIC-14_3.9x8.7mm_P1.27mm",
                  {"1": "CAN_TX", "2": "CAN_RX", "3": None, "4": "CAN_INT", "5": "CAN_OSC2", "6": "CAN_OSC1",
                   "7": "GND", "8": None, "9": None, "10": "CAN_SCK", "11": "CAN_MOSI", "12": "CAN_MISO",
                   "13": "CAN_CS", "14": "CM5_3V3"}, "CAN",
                  bem="CAN-FD-Controller SOIC-14, UNTERSEITE", beschr="SPI0 CE0, INT GPIO25 (mcp251xfd)",
                  **smd("MCP2518FD")))
    T.append(Teil("Y1", "Device:Crystal_GND24", "40 MHz", "Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm",
                  {"1": "CAN_OSC1", "2": "GND", "3": "CAN_OSC2", "4": "GND"}, "CAN",
                  bem="Quarz 40 MHz 3225, CL 15 pF, UNTERSEITE", beschr="Takt MCP2518FD", **smd("40MHz")))
    C("C1", "22p", "CAN_OSC1", "GND", "CAN", "Quarz-Last")
    C("C2", "22p", "CAN_OSC2", "GND", "CAN", "Quarz-Last")
    C("C3", "100n", "CM5_3V3", "GND", "CAN", "Abblockung U1")
    C("C4", "10u", "CM5_3V3", "GND", "CAN", "Puffer CM5_3V3")
    R("R18", "10k", "CM5_3V3", "CAN_INT", "CAN", "Pull-up ~INT")
    T.append(Teil("U2", "Interface_CAN_LIN:SN65HVD230", "SN65HVD230", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                  {"1": "CAN_TX", "2": "GND", "3": "CM5_3V3", "4": "CAN_RX", "5": None, "6": "CANL",
                   "7": "CANH", "8": "GND"}, "CAN",
                  bem="CAN-Transceiver 3,3 V, SOIC-8, UNTERSEITE", beschr="CAN-Transceiver", **smd("SN65HVD230")))
    C("C5", "100n", "CM5_3V3", "GND", "CAN", "Abblockung U2")
    T.append(Teil("D6", "Power_Protection:NUP2105L", "NUP2105L", "Package_TO_SOT_SMD:SOT-23",
                  {"1": "CANH", "2": "CANL", "3": "GND"}, "CAN", bem="ESD CAN, SOT-23, UNTERSEITE",
                  beschr="ESD CANH/CANL", **smd("NUP2105L")))
    R("R19", "120", "CANH", "CAN_TERM", "CAN", "CAN-Abschluss 120 Ω (über JP4)")
    T.append(Teil("JP_CAN_TERM", "Connector_Generic:Conn_01x02", "CAN 120R", f"{FP_LIB}:Stiftleiste_1x2_P2.54mm_SMD_stehend",
                  {"1": "CAN_TERM", "2": "CANL"}, "CAN",
                  bem="Stiftleiste 1x2 RM 2,54 SMD + Jumper-Kappe, werkseitig GESTECKT (Träger = Busende); ziehen, wenn "
                      "Toolhead und Mainboard-Ersatz (M8P) die Busenden bilden",
                  beschr="CAN-Abschluss 120 Ω (JP_CAN_TERM)", **smd("HDR2")))
    T.append(Teil("J9b", "Connector_Generic:Conn_01x03", "CAN H/L/GND",
                  "Connector_JST:JST_GH_BM03B-GHS-TBT_1x03-1MP_P1.25mm_Vertical",
                  {"1": "CANH", "2": "CANL", "3": "GND"}, "CAN",
                  bem="JST GH 3-pol. STEHEND (Top entry, Kabel von oben; 1 = CANH, 2 = CANL, 3 = GND), NUR DATEN – "
                      "keine 24 V (Toolhead-24 V direkt vom Netzteil, Kabelsicherung ≈ 5 A); ESD über D6 (NUP2105L); "
                      "gesteckt ≈ 7,3 mm + Kabelbogen",
                  beschr="CAN-Anschluss (Toolhead-Board bzw. Option Mainboard-Ersatz, z. B. BTT Manta M8P)",
                  gesteckt=GH_GESTECKT, **smd("GH3V")))
    # ---------------- 24-V-Eingang ----------------
    T.append(Teil("J7", "Connector_Generic:Conn_01x02", "XT30 24V", "Connector_AMASS:AMASS_XT30PW-M_1x02_P2.50mm_Horizontal",
                  {"1": "GND", "2": "VIN24_ROH"}, "24V",
                  bem="XT30PW-M liegend (15 A), Pin 2 = +24 V, Pin 1 = GND (wie „+“/„−“ im Footprint/Datenblatt XT30PW-M); "
                      "Polarität am Stecker prüfen",
                  beschr="24-V-Eingang (≥ 8–10 A) direkt vom Netzteil, 18/16 AWG",
                  bestueckung=HERSTELLER + " – THT, Handlöten",
                  lcsc="C431092", mpn="XT30PW-M30.G.Y", hersteller="Changzhou Amass", hoehe=5.0, unten_tht=0.4))
    T.append(Teil("F1", "Device:Fuse", "3A tauschbar", f"{FP_LIB}:{FP_HALTER}", {"1": "VIN24_ROH", "2": "VIN24_F"}, "24V",
                  bem="Sicherungshalter Littelfuse 157 (Nano²-Clips) MIT 3-A-Sicherung 453 (sehr flink, 125 V), OBERSEITE "
                      "(0157003.DR; den Halter gibt es nur mit flinker Sicherung); Sicherung von Hand tauschbar. Ersatz träge: "
                      "Littelfuse 0452003.MRL (Slo-Blo, LCSC C99550), gleich: 0453003.MR (C178991)",
                  beschr="Tauschbare Eingangssicherung +24 V (ersetzt F1/F2 1206; Verpolung: Kurzschluss über D8 -> F1)",
                  **smd("F157")))
    T.append(Teil("D8", "Device:D_TVS", "SMFJ28A", "Diode_SMD:D_SOD-123F", {"1": "VIN24_F", "2": "GND"}, "24V",
                  bem="TVS 28 V uni, SOD-123F, UNTERSEITE; Kathode = Pad 1", beschr="Überspannung 24-V-Eingang",
                  **smd("SMFJ28A")))
    R("R23", "5m", "VIN24_F", "VIN24", "24V", "Shunt Eingang (INA226 0x40, Messbereich bis ≈ 16 A)", fp=SHUNT_FP,
      bem="Shunt 5 mΩ 1 % 2 W 2512, UNTERSEITE")
    T.append(Teil("U4", "Sensor_Energy:INA226", "INA226", "Package_SO:MSOP-10_3x3mm_P0.5mm",
                  {"1": "GND", "2": "GND", "3": "PWR_FAIL_N", "4": "I2C1_SDA", "5": "I2C1_SCL", "6": "CM5_3V3",
                   "7": "GND", "8": "MESS_VIN24", "9": "VIN24", "10": "VIN24_F"}, "Messung",
                  bem="INA226 0x40 (A1 = A0 = GND), MSOP-10, UNTERSEITE; ALERT (open drain) = PWR_FAIL_N -> GPIO27",
                  beschr="Strom/Spannung 24-V-Eingang; Bus-Unterspannungs-Alarm = Stromausfall-Erkennung",
                  **smd("INA226")))
    R("R20", "1k", "VIN24", "MESS_VIN24", "Messung", "VBUS-Messeingang U4")
    C("C7", "100n", "CM5_3V3", "GND", "Messung", "Abblockung U4")
    TP("TP10", "VIN24", "24V")
    R("R50", "10k", "CM5_3V3", "PWR_FAIL_N", "Messung", "Pull-up ALERT U4 (PWR_FAIL_N, aktiv low) -> GPIO27")
    TP("TP12", "PWR_FAIL_N", "PWR_FAIL")
    # ---------------- 24 V -> 5,2 V (LMR51450) ----------------
    T.append(Teil("U7", f"{FP_LIB}:LMR51450", "LMR51450SDRRR", "Package_SON:WSON-12-1EP_3x3mm_P0.5mm_EP1.5x2.5mm",
                  {"1": "WANDLER_SW", "2": "WANDLER_SW", "3": "WANDLER_SW", "4": "WANDLER_BOOT", "5": None,
                   "6": None, "7": "WANDLER_FB", "8": "GND", "9": "VIN24", "10": "VIN24", "11": "VIN24",
                   "12": "VIN24", "13": "GND"}, "Wandler",
                  bem="Synchron-Abwärtswandler 5 A, WSON-12, UNTERSEITE (wie Entwurf 3); EN an VIN, RT offen = 500 kHz",
                  beschr="24 V -> 5,2 V für den CM5", **smd("LMR51450")))
    C("C9", "10u50", "VIN24", "GND", "Wandler", "Eingang Wandler", fp=C1206_FP)
    C("C10", "10u50", "VIN24", "GND", "Wandler", "Eingang Wandler", fp=C1206_FP)
    C("C11", "100n", "VIN24", "GND", "Wandler", "HF-Abblockung Wandler")
    C("C12", "100n", "WANDLER_SW", "WANDLER_BOOT", "Wandler", "Bootstrap")
    T.append(Teil("L1", "Device:L", "4,7µH", "Inductor_SMD:L_Changjiang_FXL0630", {"1": "WANDLER_SW", "2": "WANDLER_5V"},
                  "Wandler", bem="4,7 µH, Isat 9 A, 7 x 6,6 x 3 mm, OBERSEITE", beschr="Wandler-Drossel",
                  **smd("4u7")))
    R("R26", "105k", "WANDLER_5V", "WANDLER_FB", "Wandler", "FB oben: 0,8 V · (1 + 105k/19,1k) = 5,20 V")
    R("R27", "19k1", "WANDLER_FB", "GND", "Wandler", "FB unten")
    C("C13", "33p", "WANDLER_5V", "WANDLER_FF", "Wandler", "CFF")
    R("R28", "1k", "WANDLER_FF", "WANDLER_FB", "Wandler", "RFF")
    for i in range(4):
        C(f"C{14 + i}", "22u25", "WANDLER_5V", "GND", "Wandler", "Ausgang Wandler", fp=C1206_FP)
    # ---------------- 5-V-Power-Mux (Wandler hat Vorrang, USB-C als Ersatz) ----------------
    T.append(Teil("U8", f"{FP_LIB}:TPS2121", "TPS2121RUXR", f"{FP_LIB}:Texas_RUX_VQFN-HR-12_2x2.5mm_P0.5mm",
                  {"1": "MUX_OUT", "8": "MUX_OUT", "2": "MUX_IN2", "7": "WANDLER_5V", "3": "GND", "4": "GND",
                   "5": "GND", "6": "MUX_PR1", "9": "GND", "10": "MUX_ILM", "11": "MUX_SS", "12": "GND"}, "5V",
                  bem="Power-Mux 4,5 A, 56 mΩ, VQFN-HR-12, UNTERSEITE; Vorrang IN1 (PR1 > 1,06 V), CP2/OV1/OV2/ST an GND",
                  beschr="5 V CM5: Wandler (IN1) oder USB-C-VBUS (IN2), nie parallel", **smd("TPS2121")))
    R("R29", "33k", "WANDLER_5V", "MUX_PR1", "5V", "PR1-Teiler oben: Umschaltung bei IN1 < 4,56 V")
    R("R30", "10k", "MUX_PR1", "GND", "5V", "PR1-Teiler unten")
    R("R31", "18k7", "MUX_ILM", "GND", "5V", "Strombegrenzung 5,2 A typ. (4,6–5,8 A)")
    C("C8", "22n", "MUX_SS", "GND", "5V", "Einschwing-/Softstart-Zeit (≈ 1,4 ms auf 5 V)")
    C("C18", "10u", "MUX_IN2", "GND", "5V", "Eingang IN2 (VBUS)")
    R("R24", "10m", "MUX_OUT", "CM5_5V", "5V", "Shunt CM5-5V (INA226 0x41)", fp=SHUNT_FP,
      bem="Shunt 10 mΩ 1 % 2 W 2512, UNTERSEITE (5 A -> 0,25 W)")
    for i in range(3):
        C(f"C{19 + i}", "22u25", "CM5_5V", "GND", "5V", "Puffer an den CM5-5V-Pins", fp=C1206_FP)
    C("C22", "100n", "CM5_5V", "GND", "5V", "HF an den CM5-5V-Pins")
    T.append(Teil("U5", "Sensor_Energy:INA226", "INA226", "Package_SO:MSOP-10_3x3mm_P0.5mm",
                  {"1": "GND", "2": "CM5_3V3", "3": None, "4": "I2C1_SDA", "5": "I2C1_SCL", "6": "CM5_3V3",
                   "7": "GND", "8": "CM5_5V", "9": "CM5_5V", "10": "MUX_OUT"}, "Messung",
                  bem="INA226 0x41 (A1 = GND, A0 = VS), MSOP-10, UNTERSEITE", beschr="Strom/Spannung CM5-5V",
                  **smd("INA226")))
    # U5: VBUS (Pin 8) direkt an IN− (Pin 9, Nachbarpin) – RC-Filter laut Datenblatt optional, bei 5 V entbehrlich
    C("C25", "100n", "CM5_3V3", "GND", "Messung", "Abblockung U5")
    # ---------------- LEDs ----------------
    R("R38", "2k2", "BR_5V", "LED_BR5V", "LEDs", "Vorwiderstand Drucker-5V-LED")
    LED("D10", "rot", "LED_BR5V", "GND", "LEDs", "DRUCKER 5V (leuchtet = Flachkabel richtig herum)")
    R("R39", "470", "CM5_3V3", "LED_3V3", "LEDs", "Vorwiderstand 3V3-LED")
    LED("D11", "grün", "LED_3V3", "GND", "LEDs", "CM5 3V3")
    R("R40", "470", "CM5_3V3", "LED_ACT_A", "LEDs", "Vorwiderstand ACT-LED")
    LED("D12", "grün", "LED_ACT_A", "LED_NACT", "LEDs", "CM5 Aktivität (LED_nACT)")
    R("R41", "1k", "CM5_3V3", "LED_ETH3_A", "LEDs", "Vorwiderstand Ethernet-Aktivität")
    LED("D13", "grün", "LED_ETH3_A", "ETH_NLED3", "LEDs", "Ethernet Aktivität (nLED3)")
    R("R42", "1k", "CM5_3V3", "LED_ETH2_A", "LEDs", "Vorwiderstand Ethernet-Geschwindigkeit")
    LED("D14", "gelb", "LED_ETH2_A", "ETH_NLED2", "LEDs", "Ethernet Geschwindigkeit (nLED2)")
    # ---------------- DSI-Display (MIPI1 = DSI1, Belegung wie Raspberry Pi 5 / CM-IO 22-pol.) ----------------
    j12 = {"1": "GND", "2": "DSI1_D0_N", "3": "DSI1_D0_P", "4": "GND", "5": "DSI1_D1_N", "6": "DSI1_D1_P", "7": "GND",
           "8": "DSI1_C_N", "9": "DSI1_C_P", "10": "GND", "11": None, "12": None, "13": "GND", "14": None, "15": None,
           "16": "GND", "17": "CAM_GPIO0", "18": "CAM_GPIO1", "19": "GND", "20": "DSI_SCL", "21": "DSI_SDA",
           "22": "DSI_3V3"}
    T.append(Teil("J12", "Connector_Generic:Conn_01x22", "DSI 22-pol.", "Connector_FFC-FPC:Hirose_FH12-22S-0.5SH_1x22-1MP_P0.50mm_Horizontal",
                  j12, "DSI", hinweise={11: "D2 (nicht verlegt)", 12: "D2 (nicht verlegt)", 14: "D3 (nicht verlegt)",
                                        15: "D3 (nicht verlegt)"},
                  bem="FPC 22-pol. 0,5 mm Hirose FH12-22S-0.5SH(55), Kontakte unten, H 2,0 mm – Belegung wie Raspberry Pi 5 "
                      "/ CM-IO-Board (2 Datenlanes + Takt verlegt, D2/D3 offen)",
                  beschr="DSI1 (MIPI1) für Waveshare 4.3inch DSI LCD (22-auf-15-pol. Kabel), dtoverlay=vc4-kms-dsi-7inch",
                  **smd("FH12-22")))
    T.append(Teil("U14", "Regulator_Switching:TLV62569DBV", "TLV62569DBVR", "Package_TO_SOT_SMD:SOT-23-5",
                  {"1": "CM5_3V3", "2": "GND", "3": "DSI_SW", "4": "CM5_5V", "5": "DSI_FB"}, "DSI",
                  bem="Abwärtswandler 2 A, 1,5 MHz, SOT-23-5, UNTERSEITE; EN an CM5_3V3 (Display nur bei laufendem CM5 "
                      "versorgt -> keine Rückspeisung über I2C)", beschr="3,3 V für das DSI-Display (≥ 0,5 A, eigene Schiene)",
                  **smd("TLV62569")))
    T.append(Teil("L2", "Device:L", "2,2µH", "Inductor_SMD:L_1008_2520Metric", {"1": "DSI_SW", "2": "DSI_3V3"}, "DSI",
                  bem="2,2 µH 1,5 A 1008, H 1,0 mm, UNTERSEITE", beschr="Drossel DSI-3,3-V-Wandler", **smd("2u2")))
    C("C34", "10u", "CM5_5V", "GND", "DSI", "Eingang DSI-Wandler")
    C("C35", "22u6", "DSI_3V3", "GND", "DSI", "Ausgang DSI-3,3 V", bem="22 µF 6,3 V X5R 0805")
    C("C36", "22u6", "DSI_3V3", "GND", "DSI", "Ausgang DSI-3,3 V", bem="22 µF 6,3 V X5R 0805")
    R("R45", "100k", "DSI_3V3", "DSI_FB", "DSI", "FB oben: 0,6 V · (1 + 100k/22k) = 3,33 V")
    R("R46", "22k", "DSI_FB", "GND", "DSI", "FB unten")
    R("R48", "2k2", "CM5_3V3", "DSI_SCL", "DSI", "Pull-up I2C von MIPI1 (ID_SC)")
    R("R49", "2k2", "CM5_3V3", "DSI_SDA", "DSI", "Pull-up I2C von MIPI1 (ID_SD)")
    # ---------------- Messpunkte ----------------
    for ref, net, kurz in (("TP1", "GND", "GND"), ("TP2", "GND", "GND"), ("TP3", "CM5_5V", "5V"),
                           ("TP4", "CM5_3V3", "3V3"), ("TP5", "PMIC_EN", "RUN"), ("TP6", "NRPIBOOT", "BOOT"),
                           ("TP7", "PWR_BUT", "PWR"), ("TP11", "BR_5V", "BR5V")):
        TP(ref, net, kurz)
    # ---------------- ERC: Versorgungsnetze als getrieben markieren ----------------
    for i, net in enumerate(("GND", "CM5_5V", "VIN24", "WANDLER_5V", "MUX_IN2")):
        T.append(Teil(f"#FLG0{i + 1}", "power:PWR_FLAG", "PWR_FLAG", "", {"1": net}, "Flags", in_bom=False,
                      on_board=False, beschr=f"ERC: {net} wird versorgt", bestueckung="–"))
    # ---------------- Befestigung, Passermarken ----------------
    for i in range(4):
        T.append(Teil(f"H{i + 1}", "Mechanical:MountingHole", "Befestigung T113-Platz (M3)", f"{FP_LIB}:{FP_BOHRUNG}",
                      {}, "Mechanik", bem=f"Bohrung Ø {T113_BOHRUNG} mm ohne Kupfer (L{i + 1}); Schraubenkopf ≤ "
                      f"{T113_KOPF} mm", beschr=f"Befestigung L{i + 1}", bestueckung="– (Bohrung)"))
    for i in range(3):
        T.append(Teil(f"FID{i + 1}", "Mechanical:Fiducial", "Passermarke", "Fiducial:Fiducial_1mm_Mask2mm", {},
                      "Mechanik", in_bom=False, bem="Passermarke oben", beschr="Fiducial", bestueckung="–"))
    for i in range(3):
        T.append(Teil(f"FID{i + 4}", "Mechanical:Fiducial", "Passermarke", "Fiducial:Fiducial_1mm_Mask2mm", {},
                      "Mechanik", in_bom=False, bem="Passermarke unten", beschr="Fiducial", bestueckung="–"))
    byref = {t.ref: t for t in T}
    for a, pa, b, pb in drähte:
        assert byref[a].pins[pa] == byref[b].pins[pb]
        byref[a].verdrahtet.add(pa)
        byref[b].verdrahtet.add(pb)
    return T, drähte


def soll_netze(T):
    netze = {}
    nc = []
    for t in T:
        if t.ref.startswith("#"):
            continue
        for p, n in t.pins.items():
            if n is None:
                nc.append((t.ref, p))
            else:
                netze.setdefault(n, set()).add((t.ref, p))
    return netze, nc
# =============================================================================
#  S-Expression-Hilfen
# =============================================================================
def sx_tokens(s):
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in "()":
            yield c
            i += 1
        elif c.isspace():
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while s[j] != '"':
                if s[j] == "\\":
                    nxt = s[j + 1]
                    buf.append("\n" if nxt == "n" else nxt)
                    j += 2
                else:
                    buf.append(s[j])
                    j += 1
            yield ("S", "".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and not s[j].isspace() and s[j] not in "()":
                j += 1
            yield s[i:j]
            i = j


def sx_parse(s):
    st = [[]]
    for t in sx_tokens(s):
        if t == "(":
            st.append([])
        elif t == ")":
            x = st.pop()
            st[-1].append(x)
        else:
            st[-1].append(t[1] if isinstance(t, tuple) else t)
    return st[0][0]


def sx_find(node, key):
    return [x for x in node if isinstance(x, list) and x and x[0] == key]


def roh_symbol(text, name):
    """Rohtext eines Top-Level-Symbols aus einer .kicad_sym-Datei (stringbewusst)."""
    marker = f'(symbol "{name}"'
    i = 0
    depth = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == '"':
            i += 1
            while text[i] != '"':
                i += 2 if text[i] == "\\" else 1
        elif c == "(":
            if depth == 1 and text.startswith(marker, i) and text[i + len(marker)] in " \n\t\r":
                start = i
                d = 0
                j = i
                while True:
                    cj = text[j]
                    if cj == '"':
                        j += 1
                        while text[j] != '"':
                            j += 2 if text[j] == "\\" else 1
                    elif cj == "(":
                        d += 1
                    elif cj == ")":
                        d -= 1
                        if d == 0:
                            return text[start:j + 1]
                    j += 1
            depth += 1
        elif c == ")":
            depth -= 1
        i += 1
    raise KeyError(name)


_libcache = {}


def _top_kinder(raw):
    """Spannen (Start, Ende) der Elemente auf Tiefe 2 eines Symbol-Rohtexts (stringbewusst)."""
    teile, tiefe, start, i = [], 0, None, 0
    while i < len(raw):
        c = raw[i]
        if c == '"':
            i += 1
            while raw[i] != '"':
                i += 2 if raw[i] == "\\" else 1
        elif c == "(":
            tiefe += 1
            if tiefe == 2:
                start = i
        elif c == ")":
            if tiefe == 2:
                teile.append((start, i + 1))
            tiefe -= 1
        i += 1
    return teile


def flach_symbol(lib, name):
    """Rohtext eines Symbols; abgeleitete Symbole (extends) werden aufgelöst: Grafik/Pins vom
    Eltern-Symbol (rekursiv), Eigenschaften vom abgeleiteten Symbol (Entwurf 3: THVD1450D,
    USBLC6-2SC6)."""
    import re
    if lib not in _libcache:
        quelle = PROJ / f"{FP_LIB}.kicad_sym" if lib == FP_LIB else SYMDIR / f"{lib}.kicad_sym"
        if lib == FP_LIB:
            erzeuge_symbole()
        _libcache[lib] = quelle.read_text(encoding="utf-8")
    raw = roh_symbol(_libcache[lib], name)
    m = re.search(r'\(extends "([^"]+)"\)', raw)
    if not m:
        return raw
    eltern = m.group(1)
    praw = flach_symbol(lib, eltern)
    praw = praw.replace(f'(symbol "{eltern}"', f'(symbol "{name}"', 1)
    praw = re.sub(r'\(symbol "' + re.escape(eltern) + r'_(\d+)_(\d+)"', f'(symbol "{name}_\\1_\\2"', praw)
    kind_props = {}
    for a, b in _top_kinder(raw):
        el = raw[a:b]
        pm = re.match(r'\(property "([^"]+)"', el)
        if pm:
            kind_props[pm.group(1)] = el
    teile = _top_kinder(praw)
    neu, letzte = [], 0
    gesehen = set()
    for a, b in teile:
        el = praw[a:b]
        pm = re.match(r'\(property "([^"]+)"', el)
        neu.append(praw[letzte:a])
        if pm and pm.group(1) in kind_props:
            neu.append(kind_props[pm.group(1)])
            gesehen.add(pm.group(1))
        else:
            neu.append(el)
        letzte = b
    rest = "".join(f"\n\t\t{el}" for k, el in kind_props.items() if k not in gesehen)
    neu.append(praw[letzte:])
    text = "".join(neu)
    if rest:
        i = text.find("(property ")
        text = text[:i] + rest.strip() + "\n\t\t" + text[i:]
    return text




def _pin(art, x, y, winkel, name, nummer, versteckt=False, laenge=2.54):
    h = " (hide yes)" if versteckt else ""
    return (f'(pin {art} line (at {f(x)} {f(y)} {winkel}) (length {f(laenge)}){h} '
            f'(name {q(name)} (effects (font (size 1.27 1.27)))) '
            f'(number {q(nummer)} (effects (font (size 1.27 1.27)))))')


def _prop(name, wert, x, y, versteckt=False):
    h = " (hide yes)" if versteckt else ""
    return f'(property {q(name)} {q(wert)} (at {f(x)} {f(y)} 0){h} (effects (font (size 1.27 1.27))))'


def _symbol(name, ref, fp, beschr, breite, hoehe, pins, ds=""):
    return [f'(symbol {q(name)} (pin_names (offset 1.016)) (exclude_from_sim no) (in_bom yes) (on_board yes)',
            _prop("Reference", ref, 0, hoehe / 2 + 3.81), _prop("Value", name, 0, hoehe / 2 + 1.27),
            _prop("Footprint", fp, 0, -hoehe / 2 - 2.54, True), _prop("Datasheet", ds, 0, -hoehe / 2 - 5.08, True),
            _prop("Description", beschr, 0, -hoehe / 2 - 7.62, True),
            f'(symbol {q(name + "_0_1")} (rectangle (start {f(-breite / 2)} {f(hoehe / 2)}) (end {f(breite / 2)} '
            f'{f(-hoehe / 2)}) (stroke (width 0.254) (type default)) (fill (type background))))',
            f'(symbol {q(name + "_1_1")}', *pins, ')', '(embedded_fonts no)', ')']


def erzeuge_symbole():
    """Projekt-Symbolbibliothek: LMR51450 (wie Entwurf 3), TPS2121 (RUX) und CM5 (200 Pins, Namen aus dem DS)."""
    lmr = [_pin("power_in", -10.16, 7.62, 0, "VIN", "12"), _pin("power_in", -10.16, 5.08, 0, "VIN", "11"),
           _pin("power_in", -10.16, 2.54, 0, "VIN", "10"), _pin("input", -10.16, -2.54, 0, "EN", "9"),
           _pin("input", -10.16, -5.08, 0, "RT", "6"), _pin("open_collector", -10.16, -7.62, 0, "PG", "5"),
           _pin("passive", 10.16, 7.62, 180, "SW", "1"), _pin("passive", 10.16, 5.08, 180, "SW", "2"),
           _pin("passive", 10.16, 2.54, 180, "SW", "3"), _pin("passive", 10.16, -2.54, 180, "BOOT", "4"),
           _pin("input", 10.16, -7.62, 180, "FB", "7"),
           _pin("power_in", -2.54, -12.7, 90, "AGND", "8"), _pin("power_in", 2.54, -12.7, 90, "PGND", "13")]
    tps = [_pin("power_in", -10.16, 5.08, 0, "IN1", "7"), _pin("power_in", -10.16, 2.54, 0, "IN2", "2"),
           _pin("input", -10.16, -2.54, 0, "PR1", "6"), _pin("input", -10.16, -5.08, 0, "CP2", "3"),
           _pin("input", -10.16, -7.62, 0, "OV1", "5"), _pin("input", -10.16, -10.16, 0, "OV2", "4"),
           _pin("power_out", 10.16, 5.08, 180, "OUT", "1"), _pin("passive", 10.16, 2.54, 180, "OUT", "8"),
           _pin("passive", 10.16, -2.54, 180, "ST", "9"), _pin("passive", 10.16, -5.08, 180, "ILM", "10"),
           _pin("passive", 10.16, -7.62, 180, "SS", "11"), _pin("power_in", 0, -15.24, 90, "GND", "12")]
    pins_cm5 = json.loads((HIER / "cm5_pins.json").read_text(encoding="utf-8"))["pins"]
    cm5 = []
    for p in range(1, 201):
        name = pins_cm5[str(p)][0].replace(" (Input)", "").replace(" (Output)", "")
        art = {"GND": "power_in", "5V": "power_in", "GPIO_VREF": "power_in"}.get(name, "bidirectional")
        if p in (84, 88):
            art = "power_out"            # je Schiene nur ein Treiber-Pin (86/90 passiv), sonst ERC „Ausgang an Ausgang“
        elif p in (86, 90):
            art = "passive"
        if p <= 100:
            cm5.append(_pin(art, -38.1, 127.0 - 2.54 * p, 0, name, str(p)))
        else:
            cm5.append(_pin(art, 38.1, 127.0 - 2.54 * (p - 100), 180, name, str(p)))
    inhalt = ['(kicad_symbol_lib (version 20251024) (generator "k2_traeger_generator") (generator_version "10.0")',
              *_symbol("LMR51450", "U", "Package_SON:WSON-12-1EP_3x3mm_P0.5mm_EP1.5x2.5mm", "36-V-, 5-A-Abwärtswandler, "
                       "WSON-12 (DRR), Pad 13 = PGND", 15.24, 20.32, lmr, "https://www.ti.com/lit/ds/symlink/lmr51440.pdf"),
              *_symbol("TPS2121", "U", f"{FP_LIB}:Texas_RUX_VQFN-HR-12_2x2.5mm_P0.5mm", "Power-Mux 2,8–22 V, 4,5 A, "
                       "56 mΩ, VQFN-HR-12 (RUX)", 15.24, 22.86, tps, "https://www.ti.com/lit/ds/symlink/tps2121.pdf"),
              *_symbol("CM5", "J", f"{FP_LIB}:{FP_CM5}", "Raspberry Pi Compute Module 5, Steckverbinder A (1–100) und "
                       "B (101–200), Pin-Namen nach CM5-Datenblatt", 71.12, 259.08, cm5,
                       "https://datasheets.raspberrypi.com/cm5/cm5-datasheet.pdf"),
              ')']
    (PROJ / f"{FP_LIB}.kicad_sym").write_text("\n".join(inhalt) + "\n", encoding="utf-8")


class LibSym:
    def __init__(self, lib_id):
        lib, name = lib_id.split(":")
        raw = flach_symbol(lib, name)
        tree = sx_parse(raw)
        if sx_find(tree, "extends"):
            raise RuntimeError(f"{lib_id}: abgeleitetes Symbol (extends) nicht aufgelöst")
        self.lib_id = lib_id
        self.raw = raw.replace(f'(symbol "{name}"', f'(symbol "{lib_id}"', 1)
        self.pins = {}
        self.props = {}
        for p in sx_find(tree, "property"):
            at = sx_find(p, "at")[0]
            self.props[p[1]] = (float(at[1]), float(at[2]))

        def walk(x):
            if isinstance(x, list):
                if x and x[0] == "pin":
                    at = sx_find(x, "at")[0]
                    num = sx_find(x, "number")[0][1]
                    self.pins[num] = (float(at[1]), float(at[2]), float(at[3]))
                for y in x:
                    walk(y)
        walk(tree)




# =============================================================================
#  Schaltplan (ein Blatt A0; Symbole automatisch in Gruppen angeordnet, Verbindungen über globale Labels)
# =============================================================================
def rot(px, py, r):
    """Bibliothek (y nach oben) -> Schaltplan-Offset (y nach unten), Drehung r (gegen Uhrzeiger)."""
    x, y = px, -py
    c, s = round(math.cos(math.radians(r))), round(math.sin(math.radians(r)))
    return (x * c + y * s, -x * s + y * c)


def richtung(winkel):
    c, s = round(math.cos(math.radians(winkel))), round(math.sin(math.radians(winkel)))
    return (c, -s)


def r2(v):
    return round(v + 0.0, 2)


class Schaltplan:
    def __init__(self):
        self.el = []

    def draht(self, a, b):
        self.el.append(f'(wire (pts (xy {f(r2(a[0]))} {f(r2(a[1]))}) (xy {f(r2(b[0]))} {f(r2(b[1]))})) '
                       f'(stroke (width 0) (type default)) (uuid {q(uid("w", a, b))}))')

    def label(self, text, at, winkel):
        just = "left" if winkel in (0, 90) else "right"
        self.el.append(f'(global_label {q(text)} (shape passive) (at {f(r2(at[0]))} {f(r2(at[1]))} {winkel}) '
                       f'(fields_autoplaced yes) (effects (font (size 1.27 1.27)) (justify {just})) '
                       f'(uuid {q(uid("l", text, at))}) '
                       f'(property "Intersheetrefs" "${{INTERSHEET_REFS}}" (at {f(r2(at[0]))} {f(r2(at[1]))} 0) '
                       f'(effects (font (size 1.27 1.27)) (hide yes))))')

    def nc(self, at):
        self.el.append(f'(no_connect (at {f(r2(at[0]))} {f(r2(at[1]))}) (uuid {q(uid("nc", at))}))')

    def text(self, t, at, size=1.27, bold=False, just="left bottom"):
        b = " (bold yes)" if bold else ""
        self.el.append(f'(text {q(t)} (exclude_from_sim no) (at {f(r2(at[0]))} {f(r2(at[1]))} 0) '
                       f'(effects (font (size {size} {size}){b}) (justify {just})) (uuid {q(uid("t", t, at))}))')


def pin_info(sym, t, p):
    px, py, pw = sym.pins[p]
    x0, y0, r = t.sch
    dx, dy = rot(px, py, r)
    ende = (x0 + dx, y0 + dy)
    aussen = int((pw + 180 + r) % 360)
    return ende, aussen


def prop_pos(t, sym):
    x, y, r = t.sch
    if t.lib == "Device:R" and r == 0:
        return (x + 2.54, y - 1.27, "left"), (x + 2.54, y + 1.27, "left")
    if t.lib == "Connector:TestPoint":
        return (x, y - 5.08, ""), (x, y - 7.62, "")
    rp = sym.props.get("Reference", (0, 0))
    vp = sym.props.get("Value", (0, 0))
    a = rot(rp[0], rp[1], r)
    b = rot(vp[0], vp[1], r)
    return (x + a[0], y + a[1], ""), (x + b[0], y + b[1], "")


def symbol_instanz(t, sym):
    x, y, r = t.sch
    (rx, ry, rj), (vx, vy, vj) = prop_pos(t, sym)

    def eff(just, hide=False):
        j = f" (justify {just})" if just else ""
        h = " (hide yes)" if hide else ""
        return f"(effects (font (size 1.27 1.27)){j}{h})"
    out = [f'(symbol (lib_id {q(t.lib)}) (at {f(x)} {f(y)} {r}) (unit 1) (exclude_from_sim no) '
           f'(in_bom {"yes" if t.in_bom else "no"}) (on_board {"yes" if t.on_board else "no"}) (dnp {"yes" if t.dnp else "no"}) '
           f'(uuid {q(uid("sym", t.ref))})',
           f'(property "Reference" {q(t.ref)} (at {f(r2(rx))} {f(r2(ry))} {r}) {eff(rj)})',
           f'(property "Value" {q(t.wert)} (at {f(r2(vx))} {f(r2(vy))} {r}) {eff(vj)})',
           f'(property "Footprint" {q(t.fp)} (at {f(x)} {f(y)} 0) {eff("", True)})',
           f'(property "Datasheet" "~" (at {f(x)} {f(y)} 0) {eff("", True)})',
           f'(property "Description" {q(t.beschr)} (at {f(x)} {f(y)} 0) {eff("", True)})',
           f'(property "Bemerkung" {q(t.bem)} (at {f(x)} {f(y)} 0) {eff("", True)})',
           f'(property "Bestueckung" {q(t.bestueckung)} (at {f(x)} {f(y)} 0) {eff("", True)})',
           f'(property "LCSC" {q(t.lcsc)} (at {f(x)} {f(y)} 0) {eff("", True)})',
           f'(property "MPN" {q(t.mpn)} (at {f(x)} {f(y)} 0) {eff("", True)})',
           f'(property "Manufacturer" {q(t.hersteller)} (at {f(x)} {f(y)} 0) {eff("", True)})']
    for p in sorted(sym.pins, key=lambda s: (len(s), s)):
        out.append(f'(pin {q(p)} (uuid {q(uid("pin", t.ref, p))}))')
    out.append(f'(instances (project {q(PROJEKT)} (path {q("/" + ROOT_UUID)} (reference {q(t.ref)}) (unit 1))))')
    out.append(")")
    return "\n".join(out)


GRUPPEN = ["CM5", "Flachkabel", "Ethernet", "USB", "USB-C", "DSI", "UART", "RS485", "CAN", "24V", "Wandler", "5V",
           "Messung", "LEDs", "Messpunkte", "Mechanik", "Flags"]
GRUPPEN_TITEL = {
    "CM5": "CM5 – beide Steckverbinder (Pin-Namen aus dem Datenblatt)",
    "Flachkabel": "J1 – Original-Flachkabel (Pin n = Breakout n; GND nur 18/33, über PTC F3)",
    "Ethernet": "Ethernet – eigener 10/100-Übertrager T1 (Bridge-Board hat keinen), Bob-Smith-Abschluss, ESD",
    "USB": "USB Kamera (USB3-0, USB2-Lane) und Hub (USB3-1, USB2-Lane) – nur J1 <-> CM5 + ESD",
    "USB-C": "Notfall-USB-C J10 (stehend, OTG/rpiboot), Jumper JP_BOOT = nRPIBOOT",
    "DSI": "DSI-Display J12 an MIPI1 (DSI1): 2 Lanes + Takt, I2C ID_SC/ID_SD, eigene 3,3 V (TLV62569, 2 A)",
    "UART": "UART2 (Düse) / UART4 (Main): Ioff-Puffer + 1 kΩ + ESD",
    "RS485": "RS485 an UART0 + DE (GPIO17), Bias/Abschluss über Lötbrücken",
    "CAN": "CAN: MCP2518FD (SPI0, INT GPIO25) + SN65HVD230",
    "24V": "24-V-Eingang J7 (XT30, Kante A): tauschbare Sicherung F1, TVS, Shunt 5 mΩ",
    "Wandler": "24 V -> 5,2 V / 5 A (LMR51450)",
    "5V": "5-V-Power-Mux TPS2121 (Wandler vor USB-C), Shunt CM5-5V",
    "Messung": "INA226 an I2C1: 0x40 Eingang (ALERT = Stromausfall), 0x41 CM5-5V",
    "LEDs": "Anzeige-LEDs", "Messpunkte": "Messpunkte (Unterseite)", "Mechanik": "Befestigung, Passermarken",
    "Flags": "ERC-Markierungen",
}


def schaltplan_lage(T, syms):
    """CM5 links über die ganze Höhe, alle übrigen Symbole gruppenweise in Regalreihen rechts daneben (A0 quer).
    Platzbedarf je Symbol aus Pinlagen + Label-Längen."""
    rechts_max = 1160.0

    def huelle(t):
        sym = syms[t.lib]
        x0 = y0 = x1 = y1 = 0.0
        for p, (px, py, pw) in sym.pins.items():
            ex, ey = rot(px, py, 0)
            d = richtung(int((pw + 180) % 360))
            net = t.pins.get(p)
            lg = (2.54 + 0.95 * len(net) + 3.5) if net else 2.54
            ax, ay = ex + d[0] * lg, ey + d[1] * lg
            x0, y0, x1, y1 = min(x0, ex, ax), min(y0, ey, ay), max(x1, ex, ax), max(y1, ey, ay)
        return x0 - 3.81, y0 - 8.89, x1 + 3.81, y1 + 3.81
    titel = []
    cm5 = [t for t in T if t.gruppe == "CM5"]
    x_start = 25.4
    for t in cm5:
        x0, y0, x1, y1 = huelle(t)
        t.sch = (round((25.4 - x0) / 2.54) * 2.54, round((63.5 - y0) / 2.54) * 2.54, 0)
        titel.append((GRUPPEN_TITEL["CM5"], (25.4, 58.42)))
        x_start = 25.4 + (x1 - x0) + 12.7
    x, y, zeile_h = x_start, 58.42, 0.0
    for gr in GRUPPEN:
        teile = [t for t in T if t.gruppe == gr and gr != "CM5"]
        if not teile:
            continue
        breite = sum(huelle(t)[2] - huelle(t)[0] for t in teile[:3])
        if x + min(breite, 120) > rechts_max:
            y += zeile_h + 7.62
            x, zeile_h = x_start, 0.0
        titel.append((GRUPPEN_TITEL.get(gr, gr), (x, y)))
        for t in teile:
            x0, y0, x1, y1 = huelle(t)
            w, h = x1 - x0, y1 - y0 + 6.35
            if x + w > rechts_max:
                y += zeile_h + 7.62
                x, zeile_h = x_start, 0.0
            t.sch = (round((x - x0) / 2.54) * 2.54, round((y + 6.35 - y0) / 2.54) * 2.54, 0)
            x += w
            zeile_h = max(zeile_h, h)
        x += 10.16
    return titel, y + zeile_h


def erzeuge_schaltplan(T):
    sp = Schaltplan()
    syms = {}
    for t in T:
        if t.lib not in syms:
            syms[t.lib] = LibSym(t.lib)
    titel, unten = schaltplan_lage(T, syms)
    for t in T:
        sym = syms[t.lib]
        for p in sym.pins:
            if p not in t.pins:
                raise RuntimeError(f"{t.ref}: Symbol-Pin {p} ohne Zuordnung (None = offen)")
            ende, aussen = pin_info(sym, t, p)
            net = t.pins.get(p)
            if net is None:
                sp.nc(ende)
                h = t.hinweise.get(int(p)) if p.isdigit() else None
                if h:
                    d = richtung(aussen)
                    pos = (ende[0] + d[0] * 2.54, ende[1] + d[1] * 2.54 + 0.6)
                    sp.text(h, pos, size=1.0, just="right bottom" if d[0] < 0 else "left bottom")
                continue
            d = richtung(aussen)
            stub = (ende[0] + d[0] * 2.54, ende[1] + d[1] * 2.54)
            sp.draht(ende, stub)
            sp.label(net, stub, aussen)
        for p in t.pins:
            if p not in sym.pins and t.pins[p] is not None:
                raise RuntimeError(f"{t.ref}: Pin {p} hat ein Netz, existiert aber nicht im Symbol {t.lib}")
    for txt, (x, y) in titel:
        sp.text(txt, (x, y), size=2.54, bold=True)
    sp.text(f"{TITEL} – {DATUM_DE}", (25.4, 20.32), size=5.08, bold=True)
    sp.text(WARNUNG_LANG, (25.4, 30.48), size=3.0, bold=True)
    sp.text("Eigene Trägerplatine für das CM5 am T113-Platz im K2 Plus (Weg B, siehe KONZEPT.md). Kabel: Original-Flachkabel "
            "(J1) + 24 V (J7). Kein USB-RS485-Adapter, kein IO-Board, kein Pi-Netzteil.\n"
            "J1: Pin n = Breakout n (CON2 = 51 − n), Pin 1 (Ethernet) zur Kante D, Pins 45–50 (Drucker-5V) zur Kante B. "
            "GND nur an 18/33 (spiegelsicher) über PTC F3. BR_5V NIE an CM5-5V.\n"
            "CM5-5V nur vom eigenen Wandler (bzw. USB-C über den Power-Mux U8). 24 V nur an J7 und den Leistungsteilen (Toolhead-24 V direkt vom Netzteil).",
            (25.4, 38.1), size=1.8, just="left top")
    teile = [sym.raw for sym in syms.values()]
    sym_inst = [symbol_instanz(t, syms[t.lib]) for t in T]
    tb = (f'(title_block (title {q(TITEL)}) (date {q(DATUM)}) (rev {q(STAND)}) '
          f'(comment 1 {q(WARNUNG_KURZ)}) '
          f'(comment 2 "J1: Pin n = Breakout n, GND nur 18/33; BR_5V nie an CM5-5V") '
          f'(comment 3 "CM5 auf 2x DF40C (1,5 mm); 24 V nur J7; 4 Lagen JLC04161H-7628"))')
    inhalt = ["(kicad_sch (version 20250114) (generator \"eeschema\") (generator_version \"10.0\")",
              f"(uuid {q(ROOT_UUID)})", '(paper "A0")', tb,
              "(lib_symbols", *teile, ")", *sp.el, *sym_inst,
              '(sheet_instances (path "/" (page "1")))', "(embedded_fonts no)", ")"]
    (PROJ / f"{PROJEKT}.kicad_sch").write_text("\n".join(inhalt) + "\n", encoding="utf-8")
    if unten > 830:
        print(f"WARNUNG: Schaltplan reicht bis y = {unten:.0f} mm (A0 = 841 mm)")


# =============================================================================
#  Footprints und schlichte 3D-Quader (eigene Modelle, keine fremden 3D-Daten)
# =============================================================================
MODELL_DIR = "k2_traeger.3dshapes"
# Echte 3D-Modelle (24.10.2026, nur Darstellung – Höhen-Wächter rechnet mit Datenblatt-Höhen):
# - KiCad-Bibliothek (kicad-library-3d, CC-BY-SA 4.0 mit Ausnahme für Designs) über ${KICAD10_3DMODEL_DIR}
# - LCSC/EasyEDA-Modelle (Lizenz ungeklärt -> NUR LOKAL, nicht im öffentlichen Repo) in k2_traeger.3dshapes/lcsc/,
#   geladen mit ./lcsc_modelle_laden.sh (easyeda2kicad). Fehlt eine Datei, bleibt der schlichte Quader.
LCSC_DIR = f"{MODELL_DIR}/lcsc"
LCSC_MODELLE = {   # Footprint -> (Datei, Versatz xyz [mm, 3D-Achsen: y nach oben], Drehung xyz [°]), LCSC-Nummer
    "USB_C_Buchse_stehend_16P_TYPE-C-16P-LTH6.8": ("USB-C-SMD_TYPE-C-16P-LTH6.8.wrl", (0, 0, 0), (0, 0, 0)),     # C5187468
    "Uebertrager_SMD_H1102NLT_12.7x7.1mm": ("XFMR-SMD_L12.7-W7.1-H6.4-LS9.5-P1.27.wrl", (0, 0, 0), (0, 0, 0)),    # C18584
    "Texas_RUX_VQFN-HR-12_2x2.5mm_P0.5mm": ("VQFN-HR-12_L2.5-W2.0-H1.0-P0.50-BL.wrl", (0, 0, 0), (0, 0, 270)),  # C485916
    "Sicherungshalter_Littelfuse_157_Nano2_2410": ("FUSE-SMD_L6.5-W3.7-H3.9-LS7.3.wrl", (0, 0, 0), (0, 0, 0)),   # C187520
    # KiCad-Footprint AMASS_XT30PW-M = EasyEDA-Footprint um 180° gedreht und um (−2,5 | −5) verschoben
    "AMASS_XT30PW-M_1x02_P2.50mm_Horizontal": ("CONN-TH_XT30PW-M.wrl", (-2.5, 5.0, 0), (0, 0, 180)),           # C431092
    "WSON-12-1EP_3x3mm_P0.5mm_EP1.5x2.5mm": ("WSON-12_L3.0-W3.0-P0.50-TL-EP-2.wrl", (0, 0, 0), (0, 0, 0)),      # C6890985
    "Fuse_1812_4532Metric": ("F1812_L4.5-W3.2-H1.0.wrl", (0, 0, 0), (0, 0, 0)),                                # C12559
    "DF40C-100DS": ("CONN-SMD_DF40C-100DS-0.4V-51.wrl", (0, 0, 0), (0, 0, 90)),                                # C597931
}
# Stiftleisten JP_BOOT/JP_CAN_TERM/JP_485_TERM: KiCad-Modell (Pin1Right, um 90° gedreht = eigener Footprint)
STIFTLEISTE_MODELL = ("${KICAD10_3DMODEL_DIR}/Connector_PinHeader_2.54mm.3dshapes/"
                      "PinHeader_1x02_P2.54mm_Vertical_SMD_Pin1Right.step", (0, 0, 0), (0, 0, 90))


def lcsc_modell(fp_name, versatz=(0, 0, 0)):
    """(Pfad, Versatz, Drehung) des LCSC/EasyEDA-Modells, falls lokal vorhanden; sonst None (-> Quader)."""
    e = LCSC_MODELLE.get(fp_name)
    if e is None or not (PROJ / LCSC_DIR / e[0]).exists():
        return None
    return (f"${{KIPRJMOD}}/{LCSC_DIR}/{e[0]}", tuple(a + b for a, b in zip(e[1], versatz)), e[2])


def wrl_quader(name, quader):
    """VRML-Datei aus Quadern [(cx, cy, z0, sx, sy, sz, (r, g, b), transparenz)] in mm (Footprint-Koordinaten,
    y nach unten). KiCad-WRL: 1 Einheit = 2,54 mm, y nach oben."""
    d = PROJ / MODELL_DIR
    d.mkdir(exist_ok=True)
    zeilen = ["#VRML V2.0 utf8", f"# {name} – schlichter Quader (K2-CM5-Träger, eigene Darstellung)"]
    for cx, cy, z0, sx, sy, sz, farbe, tr in quader:
        zeilen.append(
            f"Transform {{ translation {cx / 2.54:.4f} {-cy / 2.54:.4f} {(z0 + sz / 2) / 2.54:.4f} children [ Shape {{ "
            f"appearance Appearance {{ material Material {{ diffuseColor {farbe[0]} {farbe[1]} {farbe[2]} "
            f"specularColor 0.2 0.2 0.2 shininess 0.3 transparency {tr} }} }} "
            f"geometry Box {{ size {sx / 2.54:.4f} {sy / 2.54:.4f} {sz / 2.54:.4f} }} }} ] }}")
    (d / f"{name}.wrl").write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    return f"${{KIPRJMOD}}/{MODELL_DIR}/{name}.wrl"


SCHWARZ, GRAU, BEIGE, GRUEN = (0.1, 0.1, 0.1), (0.55, 0.55, 0.58), (0.85, 0.8, 0.65), (0.1, 0.45, 0.2)


def _fp_kopf(name, beschr, tags, attr="smd", ref_y=-3.0, wert_y=3.0):
    return [f'(footprint {q(name)} (version 20260206) (generator "k2_traeger_generator") (layer "F.Cu")',
            f'(descr {q(beschr)})', f'(tags {q(tags)})',
            f'(property "Reference" "REF**" (at 0 {f(ref_y)} 0) (layer "F.SilkS") (effects (font (size 1 1) (thickness 0.15))))',
            f'(property "Value" {q(name)} (at 0 {f(wert_y)} 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))',
            f'(attr {attr})']


def _linie(a, b, layer, w=0.12):
    return (f'(fp_line (start {f(a[0])} {f(a[1])}) (end {f(b[0])} {f(b[1])}) '
            f'(stroke (width {w}) (type solid)) (layer "{layer}"))')


def _rechteck(x0, y0, x1, y1, layer, w):
    return [_linie((x0, y0), (x1, y0), layer, w), _linie((x1, y0), (x1, y1), layer, w),
            _linie((x1, y1), (x0, y1), layer, w), _linie((x0, y1), (x0, y0), layer, w)]


def _modell(pfad, offset=(0, 0, 0), drehung=(0, 0, 0)):
    return (f'(model {q(pfad)} (offset (xyz {f(offset[0])} {f(offset[1])} {f(offset[2])})) (scale (xyz 1 1 1)) '
            f'(rotate (xyz {f(drehung[0])} {f(drehung[1])} {f(drehung[2])})))')


def _silk_ohne_pads(segmente, pads, abstand=0.25):
    """Siebdruckstrecken in Stücke zerlegen, die mindestens 'abstand' von allen Pads (Rechteck-Hüllen) entfernt sind."""
    aus = []
    for (ax, ay), (bx, by) in segmente:
        laenge = math.hypot(bx - ax, by - ay)
        n = max(2, int(laenge / 0.05))
        frei = []
        for i in range(n + 1):
            t = i / n
            x, y = ax + (bx - ax) * t, ay + (by - ay) * t
            ok = all(not (px - sx / 2 - abstand <= x <= px + sx / 2 + abstand and
                          py - sy / 2 - abstand <= y <= py + sy / 2 + abstand) for px, py, sx, sy in pads)
            frei.append((ok, (x, y)))
        start = None
        for i, (ok, pt) in enumerate(frei):
            if ok and start is None:
                start = pt
            if (not ok or i == len(frei) - 1) and start is not None:
                ende = pt if ok else frei[i - 1][1]
                if math.hypot(ende[0] - start[0], ende[1] - start[1]) > 0.3:
                    aus.append((start, ende))
                start = None
    return aus


def erzeuge_footprints():
    d = PROJ / f"{FP_LIB}.pretty"
    d.mkdir(exist_ok=True)
    erzeuge_ffc(d)
    erzeuge_cm5(d)
    erzeuge_bohrung(d)
    erzeuge_halter(d)
    erzeuge_fremd(d)
    # Ersatzmodelle für KiCad-Footprints, deren 3D-Datei in der Bibliothek fehlt (nur Darstellung/Höhenprüfung)
    wrl_quader("Ersatz_XT30PW-M", [(-2.5, -4.5, 0, 12.3, 14.0, 5.0, BEIGE, 0)])
    wrl_quader("Ersatz_WSON-12_3x3", [(0, 0, 0, 3.0, 3.0, 0.8, SCHWARZ, 0)])
    wrl_quader("Ersatz_1812", [(0, 0, 0, 4.5, 3.2, 1.4, (0.2, 0.3, 0.6), 0)])


def erzeuge_ffc(d):
    """J1 wie V1.5 (XUNPU FPC-05FB-50PH20): Pads/Laschen nach Datenblatt, Ursprung = Gehäusemitte, Kabelseite +y."""
    L = []
    BW, BT = FFC_GEHAEUSE
    vorne, hinten = BT / 2, -BT / 2
    L += _rechteck(-BW / 2, hinten, BW / 2, vorne, "F.Fab", 0.1)
    L.append(_linie((-BW / 2 + 1.5, vorne - 0.4), (BW / 2 - 1.5, vorne - 0.4), "F.Fab", 0.1))
    lx = FFC_LASCHE_DX + FFC_LASCHE[0] / 2
    pad_hinten = FFC_PAD_Y - FFC_PAD[1] / 2
    L += _rechteck(-lx - 0.25, min(pad_hinten, hinten) - 0.25, lx + 0.25, vorne + 0.25, "F.CrtYd", 0.05)
    sx = BW / 2 + 0.12
    yl0 = FFC_LASCHE_Y - FFC_LASCHE[1] / 2 - 0.3
    L.append(_linie((-sx, hinten + 0.6), (-sx, yl0), "F.SilkS"))
    L.append(_linie((sx, hinten + 0.6), (sx, yl0), "F.SilkS"))
    L.append(_linie((-sx, hinten + 0.6), (-(FFC_PINS - 1) * FFC_RASTER / 2 - 0.6, hinten + 0.6), "F.SilkS"))
    L.append(_linie((sx, hinten + 0.6), ((FFC_PINS - 1) * FFC_RASTER / 2 + 0.6, hinten + 0.6), "F.SilkS"))
    x1, _ = ffc_pad_lokal(1)
    pts = [(x1, pad_hinten - 0.35), (x1 - 0.45, pad_hinten - 1.05), (x1 + 0.45, pad_hinten - 1.05)]
    L.append('(fp_poly (pts ' + " ".join(f"(xy {f(a)} {f(b)})" for a, b in pts) +
             ') (stroke (width 0.1) (type solid)) (fill yes) (layer "F.SilkS"))')
    L.append('(fp_text user "${REFERENCE}" (at 0 0.6 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))')
    for n in range(1, FFC_PINS + 1):
        x, y = ffc_pad_lokal(n)
        L.append(f'(pad "{n}" smd roundrect (at {f(x)} {f(y)}) (size {FFC_PAD[0]} {FFC_PAD[1]}) '
                 f'(layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.25))')
    for sxn in (-1, 1):
        L.append(f'(pad "MP" smd roundrect (at {f(sxn * FFC_LASCHE_DX)} {f(FFC_LASCHE_Y)}) '
                 f'(size {FFC_LASCHE[0]} {FFC_LASCHE[1]}) (layers "F.Cu" "F.Paste" "F.Mask") (roundrect_rratio 0.1))')
    modell = _modell("${KICAD10_3DMODEL_DIR}/Connector_FFC-FPC.3dshapes/Hirose_FH12-50S-0.5SH_1x50-1MP_P0.50mm_Horizontal.step",
                     (0, -(FFC_PAD_Y + 1.85), 0))
    inhalt = _fp_kopf(FP_J1, "FFC/FPC-Buchse 50P 0,5 mm, Klappbügel, Kontakte oben+unten, H 2,0 mm, XUNPU FPC-05FB-50PH20 "
                      "(LCSC C2856838); identisch zu K2-Pi-Basis V1.5", "FFC FPC 0.5mm 50P dual contact flip",
                      ref_y=hinten - 2.2, wert_y=vorne + 1.5) + L + ['(embedded_fonts no)', modell, ")"]
    (d / f"{FP_J1}.kicad_mod").write_text("\n".join(inhalt) + "\n", encoding="utf-8")


def erzeuge_cm5(d):
    """CM5: beide DF40C-100DS-0.4V(51) + 4 NPTH Ø 2,7 in einem Footprint, Koordinaten wie die offiziellen
    CM4-/CM5-IO-Designdaten (Ursprung = Loch links unten in Modulansicht). Courtyard = Modulumriss 40 x 55 mm:
    so meldet der DRC jedes Oberseiten-Bauteil unter dem CM5 als Courtyard-Überlappung."""
    L = []
    mx0, my0, mx1, my1 = -3.5, -51.5, 36.5, 3.5
    L += _rechteck(mx0, my0, mx1, my1, "F.CrtYd", 0.05)
    L += _rechteck(mx0, my0, mx1, my1, "F.Fab", 0.1)
    # Siebdruck: Modulumriss (Ecken) und Steckverbinder-Rahmen
    for cx, cy, sxn, syn in ((mx0, my0, 1, 1), (mx1, my0, -1, 1), (mx1, my1, -1, -1), (mx0, my1, 1, -1)):
        L.append(_linie((cx, cy), (cx + 2 * sxn, cy), "F.SilkS"))
        L.append(_linie((cx, cy), (cx, cy + 2 * syn), "F.SilkS"))
    for x0, x1 in ((-2.6, 1.68), (31.32, 35.6)):
        L += _rechteck(x0, -32.05, x1, -10.95, "F.SilkS", 0.12)
    # Pin-1-Markierungen (A: Pin 1, B: Pin 101)
    for x in (-3.0, 30.92):
        L.append(f'(fp_circle (center {f(x)} -31.3) (end {f(x + 0.2)} -31.3) (stroke (width 0.1) (type solid)) '
                 f'(fill yes) (layer "F.SilkS"))')
    # Sperrkreise für die Schraubenköpfe unten (B.CrtYd) und Lochlage
    for hx, hy in ((0, 0), (33, 0), (0, -48), (33, -48)):
        L.append(f'(fp_circle (center {hx} {hy}) (end {f(hx + CM5_SCHRAUBE_UNTEN / 2)} {hy}) (stroke (width 0.05) '
                 f'(type solid)) (fill no) (layer "B.CrtYd"))')
    L.append('(fp_text user "CM5" (at 16.5 -24 90) (layer "F.Fab") (effects (font (size 3 3) (thickness 0.3))))')
    for n in range(1, 201):
        x, y = cm5_pad_lokal(n)
        L.append(f'(pad "{n}" smd rect (at {f(x)} {f(y)}) (size 0.7 0.2) (layers "F.Cu" "F.Paste" "F.Mask"))')
    for hx, hy in ((0, 0), (33, 0), (0, -48), (33, -48)):
        L.append(f'(pad "" np_thru_hole circle (at {hx} {hy}) (size {CM5_LOCH_D} {CM5_LOCH_D}) (drill {CM5_LOCH_D}) '
                 '(layers "*.Cu" "*.Mask"))')
    df40 = wrl_quader("DF40C-100DS_1.5mm_Paar", [(-0.46, -21.5, 0, 3.4, 22.6, 1.0, BEIGE, 0),
                                                 (33.46, -21.5, 0, 3.4, 22.6, 1.0, BEIGE, 0)])
    modul = wrl_quader("CM5_Modul_Huelle", [(16.5, -24.0, 1.5, 40.0, 55.0, 1.24, GRUEN, 0.55)])
    inhalt = _fp_kopf(FP_CM5, "Raspberry Pi Compute Module 5 auf 2x Hirose DF40C-100DS-0.4V(51) (1,5 mm Stapelhöhe, "
                      "CM5 montiert 4,94 mm), Pads 0,2 x 0,7 mm Raster 0,4 mm, 4x NPTH Ø 2,7 (M2,5); Geometrie nach "
                      "offiziellen CM-IO-Designdaten", "CM5 compute module DF40", ref_y=5.0, wert_y=6.5) + L + \
        ['(embedded_fonts no)'] + \
        ([_modell(*lcsc_modell("DF40C-100DS", (x, 21.5, 0))) for x in (-0.46, 33.46)] if lcsc_modell("DF40C-100DS")
         else [_modell(df40)]) + [_modell(modul), ")"]
    (d / f"{FP_CM5}.kicad_mod").write_text("\n".join(inhalt) + "\n", encoding="utf-8")


def erzeuge_halter(d):
    """Littelfuse 157 Series (0157010.DR): Nano²-Clips mit eingesetzter 2410-Sicherung, SMD. Landmuster laut Datenblatt
    (Rev. 09/24/21, S. 3): 2 Pads 2,40 × 3,00 mm (2,40 in Sicherungsrichtung), Spalt 2,46 mm, außen 7,26 mm
    -> Mittenabstand 4,86 mm. Körper 6,50 × 3,70 mm, Höhe mit Sicherung 3,90 mm."""
    px, (sx, sy), (kx, ky), h = 2.43, (2.40, 3.00), (6.50, 3.70), 3.90
    cx, cy = max(px + sx / 2, kx / 2) + 0.25, max(sy, ky) / 2 + 0.25
    modell = wrl_quader("Sicherungshalter_157", [(0, 0, 0, kx, ky, 0.4, GRAU, 0),
                                                  (0, 0, 0.4, kx - 0.6, 2.69, 3.5, (0.85, 0.85, 0.8), 0)])
    z = _fp_kopf(FP_HALTER, "Littelfuse 157 Series Nano²-Sicherungsclips (2410) mit Sicherung, SMD; Sicherung tauschbar",
                 "fuse holder nano2 2410 littelfuse 157", ref_y=-(cy + 0.8), wert_y=cy + 0.8)
    z += _rechteck(-kx / 2, -ky / 2, kx / 2, ky / 2, "F.Fab", 0.1)
    z += _rechteck(-cx, -cy, cx, cy, "F.CrtYd", 0.05)
    for y in (-ky / 2 - 0.12, ky / 2 + 0.12):
        z.append(_linie((-(px - sx / 2) + 0.3, y), ((px - sx / 2) - 0.3, y), "F.SilkS", 0.12))
    z.append('(fp_text user "${REFERENCE}" (at 0 0 0) (layer "F.Fab") (effects (font (size 0.8 0.8) (thickness 0.12))))')
    for num, x in (("1", -px), ("2", px)):
        z.append(f'(pad "{num}" smd roundrect (at {f(x)} 0) (size {f(sx)} {f(sy)}) (layers "F.Cu" "F.Paste" "F.Mask") '
                 '(roundrect_rratio 0.1))')
    z += [_modell(*(lcsc_modell(FP_HALTER) or (modell,))), "(embedded_fonts no)", ")"]
    (d / f"{FP_HALTER}.kicad_mod").write_text("\n".join(z) + "\n", encoding="utf-8")


def erzeuge_bohrung(d):
    crt = T113_KOPF + 0.5
    inhalt = [f'(footprint "{FP_BOHRUNG}" (version 20260206) (generator "k2_traeger_generator") (layer "F.Cu")',
              f'(descr "Befestigung T113-Lochbild, Bohrung {T113_BOHRUNG} mm ohne Kupfer, Schraubenkopf M3 Ø <= {T113_KOPF} mm")',
              '(tags "mountinghole")',
              f'(property "Reference" "REF**" (at 0 {f(-T113_FREI / 2 - 1)} 0) (layer "F.SilkS") (hide yes) '
              '(effects (font (size 1 1) (thickness 0.15))))',
              f'(property "Value" "{FP_BOHRUNG}" (at 0 {f(T113_FREI / 2 + 1)} 0) (layer "F.Fab") '
              '(effects (font (size 1 1) (thickness 0.15))))',
              '(attr exclude_from_pos_files exclude_from_bom)',
              f'(fp_circle (center 0 0) (end {f(T113_FREI / 2)} 0) (stroke (width 0.1) (type solid)) (fill no) (layer "Cmts.User"))',
              f'(fp_circle (center 0 0) (end {f(crt / 2)} 0) (stroke (width 0.05) (type solid)) (fill no) (layer "F.CrtYd"))',
              f'(fp_circle (center 0 0) (end {f(crt / 2)} 0) (stroke (width 0.05) (type solid)) (fill no) (layer "B.CrtYd"))',
              '(fp_text user "${REFERENCE}" (at 0 0 0) (layer "F.Fab") (effects (font (size 1 1) (thickness 0.15))))',
              f'(pad "" np_thru_hole circle (at 0 0) (size {f(T113_BOHRUNG)} {f(T113_BOHRUNG)}) (drill {f(T113_BOHRUNG)}) '
              '(layers "*.Cu" "*.Mask"))', '(embedded_fonts no)', ")"]
    (d / f"{FP_BOHRUNG}.kicad_mod").write_text("\n".join(inhalt) + "\n", encoding="utf-8")


FREMD_FARBE = {"USB_C": GRAU, "Texas_RUX": SCHWARZ, "Uebertrager": SCHWARZ, "MicroFit": SCHWARZ, "Stiftleiste": SCHWARZ}


def erzeuge_fremd(d):
    """Footprints aus abgeleiteter Pad-Geometrie (aus LCSC/EasyEDA-Daten -> fremd_footprints.json; Importwerkzeug nicht im Repo)."""
    daten = json.loads((HIER / "fremd_footprints.json").read_text(encoding="utf-8"))
    for name, t in daten.items():
        L = []
        pads = []
        for p in t["pads"]:
            sx, sy = p["sx"], p["sy"]
            form = {"rect": "roundrect", "oval": "oval", "circle": "circle"}[p["form"]]
            rr = " (roundrect_rratio 0.15)" if form == "roundrect" else ""
            if p["tht"]:
                bx, by = p["bohrung"]
                bohr = f"(drill oval {f(bx)} {f(by)})" if abs(bx - by) > 1e-6 else f"(drill {f(bx)})"
                if form == "roundrect":
                    form, rr = "rect", ""
                L.append(f'(pad {q(p["nummer"])} thru_hole {form} (at {f(p["x"])} {f(p["y"])}) (size {f(sx)} {f(sy)}) '
                         f'{bohr} (layers "*.Cu" "*.Mask"){rr})')
            else:
                L.append(f'(pad {q(p["nummer"])} smd {form} (at {f(p["x"])} {f(p["y"])}) (size {f(sx)} {f(sy)}) '
                         f'(layers "F.Cu" "F.Paste" "F.Mask"){rr})')
            pads.append((p["x"], p["y"], sx, sy))
        for h in t["loecher"]:
            L.append(f'(pad "" np_thru_hole circle (at {f(h["x"])} {f(h["y"])}) (size {f(h["d"])} {f(h["d"])}) '
                     f'(drill {f(h["d"])}) (layers "*.Cu" "*.Mask"))')
            pads.append((h["x"], h["y"], h["d"], h["d"]))
        u = t["umriss"]
        ub, ut = u["b"], u["t"]
        ux0, uy0, ux1, uy1 = u["x"] - ub / 2, u["y"] - ut / 2, u["x"] + ub / 2, u["y"] + ut / 2
        L += _rechteck(ux0, uy0, ux1, uy1, "F.Fab", 0.1)
        xs = [ux0, ux1] + [px - sx / 2 for px, py, sx, sy in pads] + [px + sx / 2 for px, py, sx, sy in pads]
        ys = [uy0, uy1] + [py - sy / 2 for px, py, sx, sy in pads] + [py + sy / 2 for px, py, sx, sy in pads]
        L += _rechteck(round(min(xs) - 0.25, 2), round(min(ys) - 0.25, 2), round(max(xs) + 0.25, 2),
                       round(max(ys) + 0.25, 2), "F.CrtYd", 0.05)
        seg = [((ux0 - 0.15, uy0 - 0.15), (ux1 + 0.15, uy0 - 0.15)), ((ux1 + 0.15, uy0 - 0.15), (ux1 + 0.15, uy1 + 0.15)),
               ((ux1 + 0.15, uy1 + 0.15), (ux0 - 0.15, uy1 + 0.15)), ((ux0 - 0.15, uy1 + 0.15), (ux0 - 0.15, uy0 - 0.15))]
        for a, b in _silk_ohne_pads(seg, pads):
            L.append(_linie((round(a[0], 3), round(a[1], 3)), (round(b[0], 3), round(b[1], 3)), "F.SilkS"))
        p1 = [p for p in t["pads"] if p["nummer"] in ("1", "A1")]
        if p1:
            p1 = p1[0]
            seite = -1 if p1["x"] <= u["x"] else 1

            def abstand(x, y):
                return min(math.hypot(max(abs(x - px) - sx / 2, 0), max(abs(y - py) - sy / 2, 0)) for px, py, sx, sy in pads)
            kand = [(p1["x"] + seite * (p1["sx"] / 2 + d), p1["y"] + dy) for d in (0.5, 0.8, 1.1) for dy in (0, -0.6, 0.6)]
            mx, my = next(((x, y) for x, y in kand if abstand(x, y) >= 0.45), kand[0])
            L.append(f'(fp_circle (center {f(round(mx, 3))} {f(round(my, 3))}) (end {f(round(mx + 0.15, 3))} '
                     f'{f(round(my, 3))}) (stroke (width 0.1) (type solid)) (fill yes) (layer "F.SilkS"))')
        farbe = next((c for k, c in FREMD_FARBE.items() if name.startswith(k)), SCHWARZ)
        modell = (wrl_quader(name, [(u["x"], u["y"], 0, ub, ut, t["hoehe"], farbe, 0)]),)
        if name.startswith("Stiftleiste"):
            modell = STIFTLEISTE_MODELL
        elif lcsc_modell(name):
            modell = lcsc_modell(name)
        attr = "through_hole" if all(p["tht"] for p in t["pads"]) else "smd"
        inhalt = _fp_kopf(name, f'{t["bemerkung"]} (LCSC {t["lcsc"]}); Pad-Geometrie nach Hersteller-Datenblatt/LCSC-'
                          f'Bibliothek, Höhe laut Datenblatt {t["hoehe"]} mm', name, attr=attr,
                          ref_y=round(min(ys) - 1.2, 2), wert_y=round(max(ys) + 1.2, 2)) + L + \
            ['(embedded_fonts no)', _modell(*modell), ")"]
        (d / f"{name}.kicad_mod").write_text("\n".join(inhalt) + "\n", encoding="utf-8")


# =============================================================================
#  Projektdateien (4 Lagen)
# =============================================================================
NETZKLASSEN = (  # Name, Bahn, Abstand, Muster
    ("Default", 0.2, 0.15, []),
    ("GND", 0.3, 0.15, ["GND", "J1_GND"]),
    ("BR_5V", 0.4, 0.4, ["BR_5V"]),
    ("USB_90R", 0.25, 0.15, ["CAM_D*", "HUB_D*", "OTG_D*"]),
    ("ETH_100R", 0.2, 0.2, ["ETH0_*", "ETH1_*", "ETH_A_*", "ETH_B_*"]),
    # Freerouting-Breiten passend zu den Feinraster-Pads (MSOP-10 Raster 0,5: 0,5 − 0,175 − 0,125 = 0,2 ≥ 0,15);
    # den Strom tragen die Leistungsflächen. 24 V bei 0,15 mm: IPC-2221 außen (16–30 V) verlangt 0,1 mm.
    ("Leistung24", 0.25, 0.15, ["VIN24*"]),
    ("Leistung5", 0.25, 0.15, ["CM5_5V", "WANDLER_5V", "WANDLER_SW", "MUX_OUT", "MUX_IN2", "J10_VBUS"]),
    ("Versorgung3V3", 0.3, 0.15, ["CM5_3V3", "DSI_3V3", "DSI_SW"]),
    ("Bus", 0.3, 0.2, ["CANH", "CANL", "CAN_TERM", "RS485_A*", "RS485_B*", "RS485_TERM"]),
    ("MIPI_100R", 0.2, 0.2, ["DSI1_*"]),
)


def erzeuge_projekt():
    p = json.loads(Path("/usr/share/kicad/template/RaspberryPi-HAT/RaspberryPi-HAT.kicad_pro").read_text())
    p["meta"]["filename"] = f"{PROJEKT}.kicad_pro"
    p["sheets"] = [[ROOT_UUID, "Root"]]
    p["text_variables"] = {}
    p["boards"] = []
    ds = p["board"]["design_settings"]
    ds["rules"].update({
        # JLC 4-lagig: Bahn/Abstand ab 0,09 mm, Via 0,3 mm Bohrung ohne Aufpreis
        "min_clearance": 0.1, "min_track_width": 0.12, "min_via_diameter": 0.5,
        "min_through_hole_diameter": 0.2, "min_via_annular_width": 0.1,
        "min_copper_edge_clearance": 0.4, "min_hole_clearance": 0.2, "min_hole_to_hole": 0.25,
        "min_text_height": 0.8, "min_text_thickness": 0.12, "min_silk_clearance": 0.15,
        "min_connection": 0.15,
    })
    ds["track_widths"] = [0.0, 0.2, 0.25, 0.3, 0.5, 1.0]
    ds.setdefault("rule_severities", {})["footprint_type_mismatch"] = "ignore"     # J10: SMD mit Schirm-Lötlaschen
    ds["via_dimensions"] = [{"diameter": 0.0, "drill": 0.0}, {"diameter": 0.5, "drill": 0.3},
                            {"diameter": 0.6, "drill": 0.3}]
    ds["defaults"]["silk_text_size_h"] = 1.0
    ds["defaults"]["silk_text_size_v"] = 1.0
    ds["defaults"]["silk_text_thickness"] = 0.15
    basis = dict(p["net_settings"]["classes"][0])
    klassen, muster = [], []
    for i, (name, tw, cl, mu) in enumerate(NETZKLASSEN):
        k = dict(basis)
        k.update({"name": name, "track_width": tw, "clearance": cl, "via_diameter": 0.6, "via_drill": 0.3,
                  "priority": 2147483647 if name == "Default" else i})
        klassen.append(k)
        muster += [{"netclass": name, "pattern": m} for m in mu]
    p["net_settings"]["classes"] = klassen
    p["net_settings"]["netclass_patterns"] = muster
    p["net_settings"]["netclass_assignments"] = None
    sch = p["schematic"]
    sch["bom_export_filename"] = "ausgabe/stueckliste.csv"
    sch["legacy_lib_dir"] = ""
    sch["legacy_lib_list"] = []
    sch["plot_directory"] = "ausgabe/"
    (PROJ / f"{PROJEKT}.kicad_pro").write_text(json.dumps(p, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    (PROJ / f"{PROJEKT}.kicad_dru").write_text(
        "(version 1)\n"
        f"# {TITEL} – Zusatzregeln (spätere Regeln haben Vorrang)\n"
        "# Drucker-5V (BR_5V) hält überall >= 0,4 mm Abstand zu fremden Netzen (Ausnahme: Feinraster J1)\n"
        "(rule \"BR_5V Abstand\"\n"
        "  (condition \"A.NetName == 'BR_5V' && B.NetName != 'BR_5V'\")\n"
        "  (constraint clearance (min 0.4mm)))\n"
        "# Feinraster: FFC J1 (0,5 mm) und CM5-Steckverbinder J2 (0,4 mm) – Pad-Pad nach Bauteilgeometrie\n"
        "(rule \"Feinraster J1\"\n"
        "  (condition \"A.memberOfFootprint('J1') && B.memberOfFootprint('J1')\")\n"
        "  (constraint clearance (min 0.15mm)))\n"
        "(rule \"BR_5V-Kamm an J1\"\n"
        "  (condition \"A.NetName == 'BR_5V' && B.memberOfFootprint('J1') && B.NetName != 'BR_5V'\")\n"
        "  (constraint clearance (min 0.15mm)))\n"
        "(rule \"BR_5V-Bahnen an J1\"\n"
        "  (condition \"A.NetName == 'BR_5V' && B.NetName != 'BR_5V' && A.insideCourtyard('J1')\")\n"
        "  (constraint clearance (min 0.15mm)))\n"
        "(rule \"Feinraster J2\"\n"
        "  (condition \"A.memberOfFootprint('J2') && B.memberOfFootprint('J2')\")\n"
        "  (constraint clearance (min 0.15mm)))\n"
        "# Feinraster-ICs (MSOP-10, VQFN, USON) – Pad-Pad im Gehäuse\n"
        "(rule \"Feinraster ICs\"\n"
        "  (condition \"(A.memberOfFootprint('U4') && B.memberOfFootprint('U4')) || (A.memberOfFootprint('U5') && "
        "B.memberOfFootprint('U5')) || (A.memberOfFootprint('U6') && B.memberOfFootprint('U6')) || "
        "(A.memberOfFootprint('U8') && B.memberOfFootprint('U8')) || (A.memberOfFootprint('U9') && "
        "B.memberOfFootprint('U9')) || (A.memberOfFootprint('D7') && B.memberOfFootprint('D7')) || "
        "(A.memberOfFootprint('J10') && B.memberOfFootprint('J10'))\")\n"
        "  (constraint clearance (min 0.1mm)))\n"
        "# Vias von Bus-Netzen (RS485/CAN): 0,15 mm wie Standard (Freerouting gibt Vias nur den Standardabstand)\n"
        "(rule \"Bus-Vias\"\n"
        "  (condition \"A.Type == 'Via' && A.NetClass == 'Bus'\")\n"
        "  (constraint clearance (min 0.15mm)))\n"
        "# Bohrung–Bohrung wie JLCPCB: verschiedene Netze 0,5 mm\n"
        "(rule \"Bohrungen verschiedene Netze\"\n"
        "  (condition \"A.NetName != B.NetName\")\n"
        "  (constraint hole_to_hole (min 0.5mm)))\n"
        "# Leistungsteile ohne Wärmefallen\n"
        "(rule \"Leistung massiv\"\n"
        "  (condition \"A.memberOfFootprint('U7') || A.memberOfFootprint('U8') || A.memberOfFootprint('U9') || "
        "A.memberOfFootprint('F1') || A.memberOfFootprint('R23') || "
        "A.memberOfFootprint('R24') || A.memberOfFootprint('R25') || A.memberOfFootprint('J7') || "
        "A.memberOfFootprint('J9') || A.memberOfFootprint('L1') || A.memberOfFootprint('C1?') || "
        "A.memberOfFootprint('C2?')\")\n"
        "  (constraint zone_connection solid))\n"
        "# J1-Pads nicht an Flächen anbinden (GND 18/33 sind geroutet, über PTC F3)\n"
        "(rule \"J1 ohne Flaechenanbindung\"\n"
        "  (condition \"A.memberOfFootprint('J1')\")\n"
        "  (constraint zone_connection none))\n"
        "(rule \"Thermik ein Steg\"\n"
        "  (constraint min_resolved_spokes 1))\n", encoding="utf-8")

    fp_libs = sorted({"Resistor_SMD", "LED_SMD", "TestPoint", "MountingHole", "Jumper", "Fiducial", "Capacitor_SMD",
                      "Package_SO", "Package_TO_SOT_SMD", "Crystal", "Package_SON", "Inductor_SMD", "Fuse",
                      "Diode_SMD", "Package_DFN_QFN", "Connector_AMASS", "Connector_JST", "Connector_FFC-FPC"})
    zeilen = ["(fp_lib_table", "  (version 7)",
              f'  (lib (name "{FP_LIB}")(type "KiCad")(uri "${{KIPRJMOD}}/{FP_LIB}.pretty")(options "")(descr "Projektbibliothek {TITEL}"))']
    for lib in fp_libs:
        zeilen.append(f'  (lib (name "{lib}")(type "KiCad")(uri "${{KICAD10_FOOTPRINT_DIR}}/{lib}.pretty")(options "")(descr ""))')
    zeilen.append(")")
    (PROJ / "fp-lib-table").write_text("\n".join(zeilen) + "\n", encoding="utf-8")
    zeilen = ["(sym_lib_table", "  (version 7)"]
    for lib in ["Device", "Connector", "Connector_Generic", "Jumper", "Mechanical", "Interface_CAN_LIN",
                "Power_Protection", "power", "Interface_UART", "Sensor_Energy", "Diode", "Power_Management",
                "Transistor_FET", "Transformer", "74xGxx", "Regulator_Switching"]:
        zeilen.append(f'  (lib (name "{lib}")(type "KiCad")(uri "${{KICAD10_SYMBOL_DIR}}/{lib}.kicad_sym")(options "")(descr ""))')
    zeilen.append(f'  (lib (name "{FP_LIB}")(type "KiCad")(uri "${{KIPRJMOD}}/{FP_LIB}.kicad_sym")(options "")(descr "Projektsymbole"))')
    zeilen.append(")")
    (PROJ / "sym-lib-table").write_text("\n".join(zeilen) + "\n", encoding="utf-8")


# =============================================================================
#  Netzliste lesen / prüfen
# =============================================================================
def lies_netzliste(pfad):
    t = sx_parse(Path(pfad).read_text(encoding="utf-8"))
    comps = {}
    for c in sx_find(sx_find(t, "components")[0], "comp"):
        d = {"ref": sx_find(c, "ref")[0][1], "value": sx_find(c, "value")[0][1],
             "footprint": (sx_find(c, "footprint") or [[None, ""]])[0][1],
             "tstamp": sx_find(c, "tstamps")[0][1], "props": {}}
        for pr in sx_find(c, "property"):
            nm = sx_find(pr, "name")[0][1]
            val = sx_find(pr, "value")
            d["props"][nm] = val[0][1] if val else ""
        comps[d["ref"]] = d
    nets = {}
    for n in sx_find(sx_find(t, "nets")[0], "net"):
        name = sx_find(n, "name")[0][1]
        nets[name] = {(sx_find(nd, "ref")[0][1], sx_find(nd, "pin")[0][1]) for nd in sx_find(n, "node")}
    return comps, nets


SPIEGEL_BEWERTUNG = {   # J1-Pin -> Bewertung, wenn das Kabel gespiegelt steckt (Kontakt n -> 51 − n)
    "J1_GND": "GND auf GND (18 <-> 33) – sicher",
    "BR_5V": "Drucker-5V-Netz bekommt GND/Ethernet-Kontakte -> rote LED D10 bleibt AUS (Erkennung)",
    "ETH": "Übertrager trennt galvanisch; Gleichspannung lädt nur den Bob-Smith-Kondensator – sicher (B)",
    "USB": "5-V-Signal/GND auf USB-Leitung: USB 2.0 fordert Kurzschlussfestigkeit gegen VBUS (V); ESD-VBUS-Pin offen",
    "UART": "über 1 kΩ + Ioff-Puffer (5,5-V-tolerant) – sicher",
    "RS485": "Bus-Pins des THVD1450 −7…+12 V tolerant – sicher",
}


def pruefe_netzliste(pfad):
    T, _ = bauteile()
    soll, nc = soll_netze(T)
    comps, nets = lies_netzliste(pfad)
    fehler = []
    for name, knoten in soll.items():
        if nets.get(name) != knoten:
            fehler.append(f"Netz {name}: Soll {sorted(knoten)} / Ist {sorted(nets.get(name, []))}")
    for ref, p in nc:
        treffer = [n for n, k in nets.items() if (ref, p) in k]
        if len(treffer) != 1 or not treffer[0].startswith("unconnected-") or len(nets[treffer[0]]) != 1:
            fehler.append(f"NC-Pin {ref}.{p} ist nicht offen: {treffer}")
    for n in nets:
        if n not in soll and not n.startswith("unconnected-"):
            fehler.append(f"unerwartetes Netz {n}")

    def teile(n):
        return {r for r, _ in nets.get(n, set())}
    # --- 1. Flachkabel: GND nur an den spiegelsicheren Pins 18/33, und nur über den PTC F3 ---
    j1_gnd = sorted(int(p) for r, p in nets.get("J1_GND", set()) if r == "J1")
    if j1_gnd != [18, 33] or nets.get("J1_GND") != {("J1", "18"), ("J1", "33"), ("F3", "1")}:
        fehler.append(f"J1_GND: nur J1 18/33 + F3 erlaubt, Ist {sorted(nets.get('J1_GND', []))}")
    if any(r == "J1" for r, _ in nets.get("GND", set())):
        fehler.append("J1 hängt direkt an GND (nur über F3 erlaubt)")
    # --- 2. Drucker-5V: nur LED, Bias, Messpad; nie am CM5 ---
    br = nets.get("BR_5V", set())
    erlaubt = {("J1", str(p)) for p in BR_5V} | {("R38", "1"), ("R15", "1"), ("TP11", "1")}
    if br != erlaubt:
        fehler.append(f"BR_5V: Soll {sorted(erlaubt)} / Ist {sorted(br)}")
    cm5_5v_soll = {("J2", str(p)) for p in CM5_5V_PINS} | {("R24", "2"), ("U5", "9"), ("U5", "8"), ("C19", "1"),
                                                            ("C20", "1"), ("C21", "1"), ("C22", "1"), ("TP3", "1"),
                                                            ("U14", "4"), ("C34", "1")}
    if nets.get("CM5_5V") != cm5_5v_soll:
        fehler.append(f"CM5_5V: Soll {sorted(cm5_5v_soll)} / Ist {sorted(nets.get('CM5_5V', []))}")
    if teile("BR_5V") & teile("CM5_5V"):
        fehler.append(f"BR_5V und CM5_5V am selben Bauteil: {sorted(teile('BR_5V') & teile('CM5_5V'))}")
    # Pfad Wandler/USB-C -> CM5-5V nur über Mux U8 und Shunt R24
    if nets.get("MUX_OUT") != {("U8", "1"), ("U8", "8"), ("R24", "1"), ("U5", "10")}:
        fehler.append(f"MUX_OUT: nur U8-OUT, R24, U5 erlaubt: {sorted(nets.get('MUX_OUT', []))}")
    if nets.get("J10_VBUS") != {("J10", p) for p in ("A4", "A9", "B4", "B9")} | {("D15", "1"), ("JP5", "1")}:
        fehler.append(f"J10_VBUS: nur J10, ESD, TVS, JP5: {sorted(nets.get('J10_VBUS', []))}")
    if nets.get("MUX_IN2") != {("JP5", "2"), ("U8", "2"), ("C18", "1")}:
        fehler.append(f"MUX_IN2: nur JP5, U8-IN2, C18: {sorted(nets.get('MUX_IN2', []))}")
    # --- 3. 24 V nur an J7 (Eingang); nie an Signalsteckern; kein 24-V-Ausgang mehr (Weg A, 24.10.2026) ---
    for n in nets:
        if n.startswith(("VIN24", "TH_")) or n == "WANDLER_SW":
            k = teile(n)
            sig = {"J1", "J2", "J9", "J10", "J11", "J12", "J9b", "JP_BOOT", "JP_CAN_TERM", "JP_485_TERM"}
            if k & sig:
                fehler.append(f"24-V-Netz {n} an einem Signalstecker: {sorted(k & sig)}")
            if n.startswith("TH_"):
                fehler.append(f"Toolhead-24-V-Netz {n} vorhanden – J9/eFuse sind entfallen (Weg A)")
    stecker24 = sorted({r for n in nets if n.startswith("VIN24") for r, _ in nets[n] if r.startswith("J")})
    if stecker24 != ["J7"]:
        fehler.append(f"24 V an Steckern {stecker24} – erlaubt ist nur J7")
    if any(r in ("J9", "U9", "U6") for k in nets.values() for r, _ in k):
        fehler.append("J9 / eFuse U9 / INA226 0x44 (U6) noch in der Netzliste – sind entfallen (Weg A)")
    if nets.get("VIN24_ROH") != {("J7", "2"), ("F1", "1")} or ("J7", "1") not in nets.get("GND", set()):
        fehler.append("24-V-Eingang J7 nicht nur über die tauschbare Sicherung F1 (J7: Pin 2 = +24 V, Pin 1 = GND)")
    if nets.get("PWR_FAIL_N") != {("U4", "3"), ("R50", "2"), ("TP12", "1"), ("J2", "48")}:
        fehler.append(f"PWR_FAIL_N: nur U4-ALERT, Pull-up R50, TP12, CM5 48 (GPIO27): {sorted(nets.get('PWR_FAIL_N', []))}")
    # --- 4. USB-Paare nur zwischen J1/J10 und dem CM5 (+ ESD) ---
    usb_soll = {
        "CAM_DP": {("J1", "38"), ("J2", "134"), ("D1", "1"), ("D1", "6")},
        "CAM_DM": {("J1", "37"), ("J2", "136"), ("D1", "3"), ("D1", "4")},
        "HUB_DP": {("J1", "35"), ("J2", "163"), ("D2", "1"), ("D2", "6")},
        "HUB_DM": {("J1", "34"), ("J2", "165"), ("D2", "3"), ("D2", "4")},
        "OTG_DP": {("J10", "A6"), ("J10", "B6"), ("J2", "105"), ("D3", "3"), ("D3", "4")},
        "OTG_DM": {("J10", "A7"), ("J10", "B7"), ("J2", "103"), ("D3", "1"), ("D3", "6")},
    }
    for n, s in usb_soll.items():
        if nets.get(n) != s:
            fehler.append(f"USB-Netz {n}: Soll {sorted(s)} / Ist {sorted(nets.get(n, []))}")
    # --- 5. Ethernet nur über den Übertrager ---
    eth_soll = {"ETH_A_P": {("J1", "1"), ("T1", "16")}, "ETH_A_N": {("J1", "2"), ("T1", "14")},
                "ETH_B_P": {("J1", "4"), ("T1", "11")}, "ETH_B_N": {("J1", "5"), ("T1", "9")},
                "ETH1_P": {("J2", "4"), ("T1", "1"), ("D7", "1"), ("D7", "10")},
                "ETH1_N": {("J2", "6"), ("T1", "3"), ("D7", "2"), ("D7", "9")},
                "ETH0_P": {("J2", "12"), ("T1", "8"), ("D7", "4"), ("D7", "7")},
                "ETH0_N": {("J2", "10"), ("T1", "6"), ("D7", "5"), ("D7", "6")}}
    for n, s in eth_soll.items():
        if nets.get(n) != s:
            fehler.append(f"Ethernet-Netz {n}: Soll {sorted(s)} / Ist {sorted(nets.get(n, []))}")
    # --- 6. J1 <-> CM5 direkt nur USB; Spiegelfall bewerten ---
    for n, k in nets.items():
        if any(r == "J1" for r, _ in k) and any(r == "J2" for r, _ in k) and n not in usb_soll:
            fehler.append(f"Netz {n} verbindet J1 direkt mit dem CM5 (nur USB erlaubt)")
    spiegel = []
    j1_netz = {p: n for n, k in nets.items() for r, p in k if r == "J1"}
    for p in sorted(int(x) for x in j1_netz):
        n = j1_netz[str(p)]
        if n.startswith("unconnected-"):
            continue
        quelle = 51 - p
        art = ("5 V" if quelle in BR_5V else "5-V-Signal" if quelle in BR_5V_SIGNALE else
               "GND" if quelle in BR_GND_GEMESSEN else f"Breakout {quelle}")
        if n in ("J1_GND", "BR_5V"):
            bew = SPIEGEL_BEWERTUNG[n]
        elif n in BR_ETH.values():
            bew = SPIEGEL_BEWERTUNG["ETH"]
        elif n in BR_USB.values():
            bew = SPIEGEL_BEWERTUNG["USB"]
        elif n in BR_UART.values():
            bew = SPIEGEL_BEWERTUNG["UART"]
        elif n in BR_RS485.values():
            bew = SPIEGEL_BEWERTUNG["RS485"]
        else:
            bew = "UNBEWERTET"
            fehler.append(f"Spiegelfall J1-{p} ({n}) nicht bewertet")
        if n == "J1_GND" and (51 - p) not in BR_GND_GEMESSEN:
            fehler.append(f"J1-GND-Pin {p}: Spiegel {51 - p} ist kein GND!")
        spiegel.append(f"  J1-{p:2d} {n:14s} <- im Spiegelfall Breakout {quelle:2d} ({art}): {bew}")
    # --- 7. ESD-VBUS-Pins der FFC-USB-ESD offen (keine Rückspeisung in CM5-3,3 V im Spiegelfall) ---
    for ref in ("D1", "D2"):
        if any((ref, "5") in k for n, k in nets.items() if not n.startswith("unconnected-")):
            fehler.append(f"{ref} Pin 5 (VBUS) ist angeschlossen – muss offen bleiben")
    # --- 8. INA226-Adressen (A1 = Pin 1, A0 = Pin 2) ---
    adr = {"GND": 0, "CM5_3V3": 1}
    gefunden = {}
    for u in ("U4", "U5"):
        a1 = [n for n, k in nets.items() if (u, "1") in k][0]
        a0 = [n for n, k in nets.items() if (u, "2") in k][0]
        gefunden[u] = 0x40 + 4 * adr[a1] + adr[a0]
    if gefunden != {"U4": 0x40, "U5": 0x41}:
        fehler.append(f"INA226-Adressen falsch: {gefunden}")
    # --- 8b. Option Mainboard-Ersatz: J9b nur CAN-Daten, J11 RS485-Abgriff, Abschlüsse per Jumper ---
    for ref, soll_p in (("J9b", {"1": "CANH", "2": "CANL", "3": "GND"}), ("J11", {"1": "RS485_A", "2": "RS485_B", "3": "GND"}),
                        ("JP_CAN_TERM", {"1": "CAN_TERM", "2": "CANL"}), ("JP_485_TERM", {"1": "RS485_TERM", "2": "RS485_B"})):
        for p_, n_ in soll_p.items():
            if (ref, p_) not in nets.get(n_, set()):
                fehler.append(f"{ref} Pin {p_} nicht an {n_}")
    for n_, soll_k in (("CANH", {("U2", "7"), ("D6", "1"), ("R19", "1"), ("J9b", "1")}),
                       ("CANL", {("U2", "6"), ("D6", "2"), ("JP_CAN_TERM", "2"), ("J9b", "2")}),
                       ("RS485_A", {("U3", "6"), ("J1", "20"), ("D5", "1"), ("R17", "1"), ("JP2", "2"), ("J11", "1")}),
                       ("RS485_B", {("U3", "7"), ("J1", "19"), ("D5", "2"), ("JP_485_TERM", "2"), ("JP3", "1"), ("J11", "2")})):
        if nets.get(n_) != soll_k:
            fehler.append(f"Busnetz {n_}: Soll {sorted(soll_k)} / Ist {sorted(nets.get(n_, []))}")
    # --- 8c. DSI: Lanes nur CM5 <-> J12, 3,3 V nur aus dem eigenen Wandler, I2C = ID_SC/ID_SD ---
    dsi_soll = {"DSI1_D0_N": {("J2", "175"), ("J12", "2")}, "DSI1_D0_P": {("J2", "177"), ("J12", "3")},
                "DSI1_D1_N": {("J2", "181"), ("J12", "5")}, "DSI1_D1_P": {("J2", "183"), ("J12", "6")},
                "DSI1_C_N": {("J2", "187"), ("J12", "8")}, "DSI1_C_P": {("J2", "189"), ("J12", "9")},
                "DSI_SCL": {("J2", "35"), ("J12", "20"), ("R48", "2")}, "DSI_SDA": {("J2", "36"), ("J12", "21"), ("R49", "2")},
                "CAM_GPIO0": {("J2", "97"), ("J12", "17")}, "CAM_GPIO1": {("J2", "100"), ("J12", "18")},
                "DSI_3V3": {("L2", "2"), ("C35", "1"), ("C36", "1"), ("R45", "1"), ("J12", "22")}}
    for n_, s_ in dsi_soll.items():
        if nets.get(n_) != s_:
            fehler.append(f"DSI-Netz {n_}: Soll {sorted(s_)} / Ist {sorted(nets.get(n_, []))}")
    if sorted(int(p) for r, p in nets.get("GND", set()) if r == "J12") != [1, 4, 7, 10, 13, 16, 19]:
        fehler.append("J12: GND-Pins nicht 1/4/7/10/13/16/19")
    # --- 9. GPIO6 (CM5 30) und GPIO26 (CM5 24) seit Weg A frei (vorher Toolhead-eFuse) ---
    for p_ in ("24", "30"):
        if any(("J2", p_) in k for n, k in nets.items() if not n.startswith("unconnected-")):
            fehler.append(f"CM5-Pin {p_} (GPIO{'26' if p_ == '24' else '6'}) ist angeschlossen – seit Weg A frei")
    if fehler:
        print("NETZLISTEN-PRÜFUNG FEHLGESCHLAGEN:")
        for x in fehler:
            print("  -", x)
        sys.exit(1)
    print(f"Netzliste OK: {len(soll)} Netze, {len(nc)} offene Pins, {len(comps)} Bauteile – GND an J1 nur 18/33 (über F3), "
          "BR_5V nie am CM5, CM5-5V nur über Mux/Shunt, 24 V nur an J7 (kein 24-V-Ausgang), USB nur J1/J10 <-> CM5 "
          "(+ESD), Ethernet nur über T1, INA226 0x40/0x41, GPIO6/GPIO26 frei.")
    print("Spiegelfall (Flachkabel verdreht gesteckt, Kontakt n -> 51 − n):")
    print("\n".join(spiegel))
    (PROJ / "zwischen" / "spiegelfall.txt").write_text("\n".join(spiegel) + "\n", encoding="utf-8")


def pruefe_cm5(pfad):
    """Zweite, unabhängige CM5-Pin-Prüfung: jeder der 200 Pins gegen den Datenblatt-Namen (cm5_pins.json) und die
    Funktionstabelle CM5_ERWARTET (Name -> Netz). Alles, was dort nicht steht, muss offen sein."""
    daten = json.loads((HIER / "cm5_pins.json").read_text(encoding="utf-8"))["pins"]
    _, nets = lies_netzliste(pfad)
    ist = {}
    for n, k in nets.items():
        for r, p in k:
            if r == "J2":
                ist[int(p)] = None if n.startswith("unconnected-") else n
    fehler, zeilen = [], []
    zaehl = {}
    for p in range(1, 201):
        name = daten[str(p)][0]
        erwartet = CM5_ERWARTET.get(name)
        i = ist.get(p, "FEHLT")
        if i == "FEHLT":
            fehler.append(f"Pin {p} ({name}) fehlt in der Netzliste")
            continue
        if i != erwartet:
            fehler.append(f"Pin {p} ({name}): erwartet {erwartet or 'offen'}, ist {i or 'offen'}")
        if i:
            zaehl[i] = zaehl.get(i, 0) + 1
            zeilen.append(f"  {p:3d} {name:22s} -> {i}")
    for netz, anz in (("GND", 51), ("CM5_5V", 6), ("CM5_3V3", 3)):
        if zaehl.get(netz) != anz:
            fehler.append(f"{netz}: {zaehl.get(netz)} Pins statt {anz}")
    text = (f"CM5-Pin-Wächter: 200 Pins gegen Datenblatt geprüft, {sum(zaehl.values())} belegt "
            f"(GND {zaehl.get('GND')}, 5V {zaehl.get('CM5_5V')}, 3V3/VREF {zaehl.get('CM5_3V3')}), Rest offen.\n"
            "Belegte Signal-Pins:\n" + "\n".join(z for z in zeilen if not z.endswith(("GND", "CM5_5V"))))
    print(text)
    (PROJ / "ausgabe" / "cm5-pins.txt").write_text(text + "\n", encoding="utf-8")
    if fehler:
        print("CM5-PIN-PRÜFUNG FEHLGESCHLAGEN:")
        for x in fehler:
            print("  -", x)
        sys.exit(1)


def bericht(art, pfad, ausgabe=None):
    from collections import Counter
    d = json.loads(Path(pfad).read_text(encoding="utf-8"))
    zeilen = []
    if art == "erc":
        v = [x for sh in d.get("sheets", []) for x in sh.get("violations", [])]
        gruppen = {"ERC": v}
    else:
        gruppen = {"DRC": d.get("violations", []), "Nicht verbunden": d.get("unconnected_items", []),
                   "Schaltplan-Parität": d.get("schematic_parity", [])}
    fehler = 0
    for name, liste in gruppen.items():
        c = Counter(x["severity"] for x in liste)
        fehler += c.get("error", 0)
        zeilen.append(f"{name}: {len(liste)} Einträge (Fehler {c.get('error', 0)}, Warnungen {c.get('warning', 0)})")
        for (sev, typ), n in sorted(Counter((x["severity"], x["type"]) for x in liste).items()):
            zeilen.append(f"    {sev:8s} {typ:35s} {n}")
    text = "\n".join(zeilen)
    print(text)
    if ausgabe:
        Path(ausgabe).write_text(text + "\n", encoding="utf-8")
    if fehler:
        print(f"{art.upper()}: {fehler} FEHLER – Abbruch.")
        sys.exit(1)


# =============================================================================
#  Platine – Platzierung (Draufsicht von OBEN; Unterseiten-Teile ebenfalls in Draufsicht-Koordinaten)
# =============================================================================
R23_DREHUNG = 90              # Unterseite: Pad 1 (VIN24_F) Süd – im Probelauf geprüft (pruefe_platzierung)
U4_DREHUNG = 180              # Unterseite: Pins 6–10 nach Osten


def pcb_positionen():
    """{ref: (x, y, drehung, seite)}. Oberseite: Stecker, Übertrager, ESD der FFC-USB-Paare, Drossel, LEDs.
    Unterseite (<= 2 mm): alle übrigen Bauteile. Unter dem CM5 (x 8,9 … 63,9 / y 9,5 … 49,5) oben nichts."""
    P = {}
    jx, jy = j1_ursprung()
    o, u = "oben", "unten"
    P["J1"] = (jx, jy, J1_DREHUNG, o)
    P["J2"] = (CM5_URSPRUNG[0], CM5_URSPRUNG[1], CM5_DREHUNG, o)
    # ---- Oberseite ----
    P["T1"] = (3.4, 38.5, 90, o)            # Leitungsseite (9–16) links zu J1, PHY-Seite (1–8) rechts zum CM5
    P["D1"] = (1.5, 23.0, 90, o)            # ESD Kamera-USB (Durchgang von unten nach oben)
    P["D2"] = (6.0, 28.35, 0, o)            # ESD Hub-USB (Durchgang von links nach rechts)
    P["D10"] = (-2.0, 19.3, 270, o)         # rote LED Drucker-5V (neben J1, Pin-50-Ende, unter J7); Anode unten zu R38
    P["R38"] = (-2.0, 22.9, 90, o)          # Pad 1 (BR_5V) unten, gegenüber J1 46/47
    P["J10"] = (17.5, 3.6, 0, o)            # Notfall-USB-C stehend an Kante B nahe L1
    P["JP_BOOT"] = (25.6, 4.2, 0, o)
    P["L1"] = (50.0, 5.0, 0, o)
    P["J7"] = (3.5, 6.9, 90, o)             # XT30 liegend an Kante A (über J1), Stecköffnung nach links, Pin 2 = +24 V (unten), Pin 1 = GND
    P["F1"] = (2.9, 18.75, 0, o)            # tauschbare Eingangssicherung (Littelfuse 157), zwischen J7 und J1
    P["J9b"] = (59.5, 3.45, 0, o)           # CAN nur Daten, STEHEND an Kante B
    P["JP_CAN_TERM"] = (73.5, 52.75, 0, o)
    P["J11"] = (14.0, 52.85, 180, o)        # RS485-Abgriff, STEHEND an Kante D
    P["JP_485_TERM"] = (21.5, 52.75, 0, o)
    P["J12"] = (75.0, 30.65, 90, o)         # DSI an Kante C (wie CON1 der T113), Öffnung zur Kante, Pin 1 Süd
    P["U14"] = (52.6, 18.0, 0, u)
    P["L2"] = (56.3, 19.4, 90, u)
    P["C34"] = (49.4, 20.6, 90, u)
    P["C35"] = (59.2, 19.6, 90, u)
    P["C36"] = (61.4, 19.6, 90, u)
    P["R45"] = (52.6, 14.6, 0, u)
    P["R46"] = (48.6, 16.2, 90, u)
    P["R48"] = (25.4, 45.4, 0, u)
    P["R49"] = (25.4, 48.0, 0, u)
    for i, (d, r) in enumerate((("D11", "R39"), ("D12", "R40"), ("D13", "R41"), ("D14", "R42"))):
        P[d] = (28.1 + 4.0 * i, 52.2, 0, o)
        P[r] = (28.1 + 4.0 * i, 54.6, 0, o)
    for i, (x, y) in enumerate(T113_LOECHER):
        P[f"H{i + 1}"] = (x, y, 0, o)
    for i, (x, y) in enumerate(PASSERMARKEN):
        P[f"FID{i + 1}"] = (x, y, 0, o)
    for i, (x, y) in enumerate(PASSERMARKEN_UNTEN):
        P[f"FID{i + 4}"] = (x, y, 0, u)
    # ---- Unterseite: links (unter J1/T1) ----
    P["F3"] = (-7.2, 40.5, 90, u)
    P["D4"] = (-0.6, 31.5, 90, u)
    P["R36"] = (-0.8, 44.2, 90, u)
    P["R37"] = (-0.8, 37.2, 90, u)
    P["C28"] = (2.2, 40.6, 90, u)
    P["C27"] = (6.0, 38.5, 90, u)
    P["D7"] = (11.0, 38.5, 90, u)
    # ---- Unterseite: UART (Puffer, Serien-R, Pull-R) ----
    for ref, x, y in (("U10", 16.0, 23.5), ("U12", 16.0, 28.0), ("U11", 22.5, 23.5), ("U13", 22.5, 28.0)):
        P[ref] = (x, y, 0, u)
    for ref, x, y in (("C30", 19.3, 23.5), ("C32", 19.3, 28.0), ("C31", 25.8, 23.5), ("C33", 25.8, 28.0)):
        P[ref] = (x, y, 90, u)
    for i, ref in enumerate(("R1", "R2", "R3", "R4")):
        P[ref] = (12.6, 22.2 + 3.5 * i, 90, u)
    for i, ref in enumerate(("R11", "R9", "R12", "R10")):
        P[ref] = (28.6, 22.2 + 3.5 * i, 90, u)
    # ---- Unterseite: RS485 ----
    P["U3"] = (22.5, 38.6, 0, u)
    P["D5"] = (16.4, 36.6, 90, u)
    P["R17"] = (16.4, 41.0, 90, u)
    P["C6"] = (28.8, 36.6, 90, u)
    P["R13"] = (28.8, 40.2, 90, u)
    P["R14"] = (31.4, 36.6, 90, u)
    P["R15"] = (34.0, 36.6, 90, u)
    P["JP2"] = (34.0, 40.2, 90, u)
    P["R16"] = (36.6, 36.6, 90, u)
    P["JP3"] = (36.6, 40.2, 90, u)
    # ---- Unterseite: CAN ----
    P["U1"] = (47.0, 31.0, 180, u)
    P["Y1"] = (40.5, 29.0, 90, u)
    P["C1"] = (40.5, 32.6, 0, u)
    P["C2"] = (40.5, 25.4, 0, u)
    P["C3"] = (52.5, 27.6, 90, u)
    P["C4"] = (52.5, 31.1, 90, u)
    P["R18"] = (52.5, 34.6, 90, u)
    P["U2"] = (57.6, 31.0, 0, u)
    P["C5"] = (56.6, 25.0, 0, u)
    P["D6"] = (56.4, 37.6, 0, u)
    P["R19"] = (52.5, 38.4, 90, u)
    # ---- Messpads (Reihe unter dem CM5) ----
    for i, ref in enumerate(("TP3", "TP4", "TP5", "TP6", "TP7", "TP1")):
        P[ref] = (44.0 + 2.9 * i, 54.4, 0, o)            # oben, Rand D: 5V, 3V3, RUN, BOOT, PWR, GND
    P["TP2"] = (-1.6, 21.8, 0, u)
    P["TP11"] = (-1.6, 19.0, 0, u)
    P["TP10"] = (1.6, 16.3, 0, u)           # 24 V (nach dem Shunt), unten links neben R23
    # ---- Unterseite: Eingangs-TVS, Shunt und INA226 0x40 direkt hinter der Sicherung (links, unter J7) ----
    # Weg A (24.10.2026): kein 24-V-Stamm mehr quer über die Platine – gemessen wird am Eingang, der Stamm auf L3
    # führt nur noch VIN24 (≈ 0,6 A) zum Wandler U7.
    P["D8"] = (4.6, 19.3, 0, u)
    P["R23"] = (6.85, 12.2, R23_DREHUNG, u)  # senkrecht östlich der J7-Stifte: Pad 1 (VIN24_F) Süd, Pad 2 (VIN24) Nord
    P["U4"] = (-2.0, 9.6, U4_DREHUNG, u)    # unter dem J7-Körper; IN+/IN− (10/9) nach Osten zu R23
    P["C7"] = (-2.0, 5.9, 0, u)
    P["R20"] = (0.2, 13.3, 90, u)
    P["R50"] = (-7.2, 8.4, 90, u)           # Pull-up PWR_FAIL_N
    P["TP12"] = (-7.2, 12.4, 0, u)          # Messpad PWR_FAIL
    # ---- Unterseite: Notfall-USB-C ----
    P["D3"] = (19.6, 10.2, 90, u)
    P["JP6"] = (18.0, 45.4, 0, u)
    P["JP7"] = (18.0, 48.0, 0, u)
    P["R43"] = (21.6, 45.4, 0, u)
    P["R44"] = (21.6, 48.0, 0, u)
    P["JP5"] = (26.0, 6.8, 0, u)            # südlich des VIN24_F-Stamms (L3) an Kante B
    P["D15"] = (31.0, 4.8, 0, u)
    # ---- Unterseite: 5-V-Mux + Shunt + Messung CM5-5V ----
    P["U8"] = (54.6, 46.4, 0, u)
    P["R29"] = (50.6, 42.6, 90, u)
    P["R30"] = (54.0, 42.6, 0, u)
    P["R31"] = (51.0, 46.4, 90, u)
    P["C8"] = (56.8, 42.0, 90, u)
    P["C18"] = (56.6, 52.4, 90, u)
    P["R24"] = (50.0, 52.4, 180, u)         # Pad 2 (CM5_5V) West zu den 5-V-Vias, Pad 1 (MUX_OUT) Ost
    P["C19"] = (24.0, 52.0, 90, u)
    P["C20"] = (26.5, 52.0, 90, u)
    P["C21"] = (29.0, 52.0, 90, u)
    P["C22"] = (31.4, 52.0, 90, u)
    P["U5"] = (40.0, 53.6, 0, u)
    P["C25"] = (44.4, 53.6, 90, u)
    # ---- Unterseite: 24-V-Eingang ----
    # ---- Unterseite: Wandler ----
    P["U7"] = (43.0, 5.0, 180, u)
    P["C9"] = (39.6, 3.2, 90, u)
    P["C10"] = (39.6, 8.2, 90, u)
    P["C11"] = (42.6, 8.6, 0, u)
    P["C12"] = (45.8, 8.4, 90, u)
    P["R26"] = (34.6, 2.6, 90, u)
    P["R27"] = (36.6, 2.6, 90, u)
    P["R28"] = (34.6, 6.2, 90, u)
    P["C13"] = (36.6, 6.2, 90, u)
    P["C14"] = (53.5, 2.4, 0, u)
    P["C15"] = (53.5, 5.0, 0, u)
    P["C16"] = (53.5, 7.6, 0, u)
    P["C17"] = (57.8, 4.0, 90, u)
    return P


def board_mit_bauteilen(netzliste, pfad):
    """Neue Platine: 4 Lagen, Netze, alle Footprints platziert (Unterseite gespiegelt), Ersatz-3D-Modelle."""
    import pcbnew
    from pcbnew import FromMM as mm
    ox, oy = URSPRUNG
    comps, nets = lies_netzliste(netzliste)
    T, _ = bauteile()
    teil = {t.ref: t for t in T}
    if Path(pfad).exists():
        Path(pfad).unlink()
    board = pcbnew.NewBoard(str(pfad))
    board.SetCopperLayerCount(4)
    board.SetLayerName(pcbnew.In1_Cu, "In1.Cu")
    board.SetLayerName(pcbnew.In2_Cu, "In2.Cu")
    board.SetLayerType(pcbnew.In1_Cu, pcbnew.LT_POWER)
    board.SetLayerType(pcbnew.In2_Cu, pcbnew.LT_POWER)
    netinfo = {}
    for name in nets:
        ni = pcbnew.NETINFO_ITEM(board, name)
        board.Add(ni)
        netinfo[name] = ni
    pin_netz = {k: name for name, kn in nets.items() for k in kn}
    pos = pcb_positionen()
    fps = {}
    ersatz = {"AMASS_XT30PW-M_1x02_P2.50mm_Horizontal": "Ersatz_XT30PW-M",
              "WSON-12-1EP_3x3mm_P0.5mm_EP1.5x2.5mm": "Ersatz_WSON-12_3x3",
              "Fuse_1812_4532Metric": "Ersatz_1812"}
    for ref, c in comps.items():
        if not c["footprint"]:
            continue
        nick, name = c["footprint"].split(":")
        libpfad = str(PROJ / f"{FP_LIB}.pretty") if nick == FP_LIB else str(FPDIR / f"{nick}.pretty")
        fp = pcbnew.FootprintLoad(libpfad, name)
        if fp is None:
            raise RuntimeError(f"Footprint {c['footprint']} nicht gefunden")
        fp.SetFPID(pcbnew.LIB_ID(nick, name))
        fp.SetReference(ref)
        fp.SetValue(c["value"])
        fp.SetPath(pcbnew.KIID_PATH(f"/{c['tstamp']}"))
        fp.SetSheetname("Root")
        fp.SetSheetfile(f"{PROJEKT}.kicad_sch")
        t = teil[ref]
        fp.GetField("Description").SetText(t.beschr)
        fp.GetField("Datasheet").SetText("")
        for feld, wert in (("Bemerkung", t.bem), ("Bestueckung", t.bestueckung), ("LCSC", t.lcsc),
                           ("MPN", t.mpn), ("Manufacturer", t.hersteller)):
            fp.SetField(feld, wert)
            fl = fp.GetField(feld)
            fl.SetLayer(pcbnew.F_Fab)
            fl.SetVisible(False)
        fp.SetDNP(t.dnp)
        fp.SetExcludedFromBOM(not t.in_bom)
        if name in ersatz:
            m = fp.Models()
            while len(m):
                m.pop()
            neu = pcbnew.FP_3DMODEL()
            echt = lcsc_modell(name)
            if echt:                         # LCSC/EasyEDA-Modell (nur lokal), sonst Ersatz-Quader
                neu.m_Filename = echt[0]
                neu.m_Offset = pcbnew.VECTOR3D(*echt[1])
                neu.m_Rotation = pcbnew.VECTOR3D(*echt[2])
            else:
                neu.m_Filename = f"${{KIPRJMOD}}/{MODELL_DIR}/{ersatz[name]}.wrl"
            m.append(neu)
        x, y, r, seite = pos[ref]
        board.Add(fp)
        fp.SetPosition(pcbnew.VECTOR2I(mm(ox + x), mm(oy + y)))
        if seite == "unten":
            fp.Flip(fp.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        fp.SetOrientationDegrees(r)
        for pad in fp.Pads():
            n = pin_netz.get((ref, pad.GetNumber()))
            if n:
                pad.SetNet(netinfo[n])
        fps[ref] = fp
    board.SynchronizeNetsAndNetClasses(True)
    return board, fps, netinfo, nets


def pruefe_platzierung(board, fps):
    """Courtyard-Überlappungen je Seite, Sperrkreise der Bohrungen, Platinenrand – vor dem Routing."""
    import pcbnew
    from pcbnew import FromMM as mm
    ox, oy = URSPRUNG
    fehler = []
    liste = []
    for ref, fp in fps.items():
        for ly in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
            c = fp.GetCourtyard(ly)
            if c.OutlineCount():
                liste.append((ref, ly, c))
    for i in range(len(liste)):
        for j in range(i + 1, len(liste)):
            a, b = liste[i], liste[j]
            if a[0] == b[0] or a[1] != b[1]:
                continue
            s = pcbnew.SHAPE_POLY_SET(a[2])
            s.BooleanIntersection(b[2])
            if s.OutlineCount() and s.Area() > mm(0.01) * mm(0.01):
                fehler.append(f"Courtyard {a[0]} / {b[0]} ({'oben' if a[1] == pcbnew.F_CrtYd else 'unten'})")
    for ref, fp in fps.items():
        bb = fp.GetBoundingBox(False)
        x0, y0 = pcbnew.ToMM(bb.GetLeft()) - ox, pcbnew.ToMM(bb.GetTop()) - oy
        x1, y1 = pcbnew.ToMM(bb.GetRight()) - ox, pcbnew.ToMM(bb.GetBottom()) - oy
        for p in fp.Pads():
            pb = p.GetBoundingBox()
            px0, py0 = pcbnew.ToMM(pb.GetLeft()) - ox, pcbnew.ToMM(pb.GetTop()) - oy
            px1, py1 = pcbnew.ToMM(pb.GetRight()) - ox, pcbnew.ToMM(pb.GetBottom()) - oy
            if px0 < X_LINKS + 0.4 or py0 < 0.4 or px1 > X_RECHTS - 0.4 or py1 > PLATINE_HOEHE - 0.4:
                fehler.append(f"Pad {ref}.{p.GetNumber()} zu nah am Rand ({px0:.2f}…{px1:.2f} / {py0:.2f}…{py1:.2f})")
                break
    return fehler


# =============================================================================
#  Geometrie-Hilfen und feste Leitungen
# =============================================================================
def _pp(a, b):
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _ps(p, a, b):
    dx, dy = b[0] - a[0], b[1] - a[1]
    ll = dx * dx + dy * dy
    t = 0.0 if ll == 0 else max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / ll))
    return math.hypot(p[0] - a[0] - t * dx, p[1] - a[1] - t * dy)


def _schneiden(a, b, c, d):
    def o(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
    return (o(a, b, c) * o(a, b, d) < 0) and (o(c, d, a) * o(c, d, b) < 0)


def _ss(a, b, c, d):
    if _schneiden(a, b, c, d):
        return 0.0
    return min(_ps(a, c, d), _ps(b, c, d), _ps(c, a, b), _ps(d, a, b))


def _rp(p, r):
    dx = max(r[0] - p[0], 0, p[0] - r[2])
    dy = max(r[1] - p[1], 0, p[1] - r[3])
    return math.hypot(dx, dy)


def _rs(a, b, r):
    """Abstand Strecke a–b zum Rechteck r (0 bei Berührung/Überlappung)."""
    for p in (a, b):
        if r[0] <= p[0] <= r[2] and r[1] <= p[1] <= r[3]:
            return 0.0
    ecken = [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])]
    return min(_ss(a, b, ecken[i], ecken[(i + 1) % 4]) for i in range(4))


def _schnittpunkt(a1, b1, a2, b2):
    x1, y1, x2, y2 = a1[0], a1[1], b1[0], b1[1]
    x3, y3, x4, y4 = a2[0], a2[1], b2[0], b2[1]
    nenner = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(nenner) < 1e-12:
        return None
    t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / nenner
    return (x1 + t * (x2 - x1), y1 + t * (y2 - y1))


def versatz(pts, d):
    """Parallele Linie im Abstand d (positiv = rechts der Laufrichtung; y nach unten)."""
    linien = []
    for a, b in zip(pts, pts[1:]):
        dx, dy = b[0] - a[0], b[1] - a[1]
        lg = math.hypot(dx, dy)
        nx, ny = -dy / lg, dx / lg
        linien.append(((a[0] + nx * d, a[1] + ny * d), (b[0] + nx * d, b[1] + ny * d)))
    aus = [linien[0][0]]
    for (a1, b1), (a2, b2) in zip(linien, linien[1:]):
        aus.append(_schnittpunkt(a1, b1, a2, b2) or b1)
    aus.append(linien[-1][1])
    return [(round(x, 4), round(y, 4)) for x, y in aus]


def laenge(pts):
    return sum(_pp(a, b) for a, b in zip(pts, pts[1:]))


def beulen(pts, i, s0, n, extra, seite, c=0.25, breite=0.6):
    """In das achsparallele Segment i ab Koordinate s0 (x bei waagerecht, y bei senkrecht) n Rechteck-Beulen
    (45°-Ecken c) einsetzen, Gesamtzuwachs 'extra'. seite = ±1: Ausbeulung in Richtung kleinerer/größerer Quer-
    koordinate (−1 = nach oben bzw. links)."""
    a, b = pts[i], pts[i + 1]
    waag = abs(a[1] - b[1]) < 1e-6
    assert waag or abs(a[0] - b[0]) < 1e-6
    rich = (1 if b[0] > a[0] else -1) if waag else (1 if b[1] > a[1] else -1)
    h = (extra / n + 4 * c * (2 - math.sqrt(2))) / 2      # Zuwachs je Beule = 2h − 4c(2 − √2)
    if h < 2 * c + 0.02:                                    # kleiner Zuwachs: flachere Ecken
        c = 0.04
        h = (extra / n + 4 * c * (2 - math.sqrt(2))) / 2
    q = a[1] if waag else a[0]

    def P(s, quer):
        return (round(s, 4), round(quer, 4)) if waag else (round(quer, 4), round(s, 4))
    neu = [a]
    s = s0
    for _ in range(n):
        neu += [P(s, q), P(s + rich * c, q + seite * c), P(s + rich * c, q + seite * (h - c)),
                P(s + rich * 2 * c, q + seite * h), P(s + rich * (2 * c + breite), q + seite * h),
                P(s + rich * (3 * c + breite), q + seite * (h - c)), P(s + rich * (3 * c + breite), q + seite * c),
                P(s + rich * (4 * c + breite), q)]
        s += rich * (4 * c + breite + 0.8)
    return pts[:i] + neu + pts[i + 1:], h


def abgleichen(a, b, via_a=0, via_b=0, segment=None, s0=None, n=1, seite=None, c=0.25, breite=0.6):
    """Zwei Polylinien auf gleiche Länge bringen (inkl. Via-Längen): Beulen in die kürzere. segment/s0/seite:
    je Linie (dict 'a'/'b') das Segment, die Startkoordinate und die Ausbeulrichtung."""
    la, lb = laenge(a) + via_a * VIA_LAENGE, laenge(b) + via_b * VIA_LAENGE
    if abs(la - lb) <= 0.02:
        return a, b
    if la < lb:
        a, _ = beulen(a, segment["a"], s0["a"], n, lb - la, seite["a"], c, breite)
    else:
        b, _ = beulen(b, segment["b"], s0["b"], n, la - lb, seite["b"], c, breite)
    return a, b


_KLASSEN_CACHE = {}


def klassen_abstand(netz):
    """Mindestabstand eines Netzes laut Netzklasse (BR_5V: 0,4 mm laut DRU); unbekannte Netze 0."""
    import fnmatch
    if netz not in _KLASSEN_CACHE:
        wert = 0.4 if netz == "BR_5V" else 0.0
        for _, _, ab_, muster in NETZKLASSEN:
            if any(fnmatch.fnmatchcase(netz or "", m) for m in muster):
                wert = max(wert, ab_)
        _KLASSEN_CACHE[netz] = wert
    return _KLASSEN_CACHE[netz]


class Kupfer:
    """Feste (gesperrte) Leiterbahnen und Vias, vor dem Freerouting gesetzt."""
    def __init__(self):
        self.seg = []
        self.via = []

    def bahn(self, a, b, w, netz, lage="F", gruppe=None):
        if _pp(a, b) > 1e-6:
            self.seg.append({"a": (round(a[0], 4), round(a[1], 4)), "b": (round(b[0], 4), round(b[1], 4)), "w": w,
                             "netz": netz, "lage": lage, "gruppe": gruppe})

    def zug(self, pts, w, netz, lage="F", gruppe=None):
        for a, b in zip(pts, pts[1:]):
            self.bahn(a, b, w, netz, lage, gruppe)

    def via_setzen(self, p, netz, d=0.6, bohr=0.3, gruppe=None):
        self.via.append({"p": (round(p[0], 4), round(p[1], 4)), "d": d, "bohr": bohr, "netz": netz, "gruppe": gruppe})

    def frei(self, art, geo, netz, lage, pads, abstand=0.15):
        """Prüft ein neues Element gegen Pads (anderer Netze) und vorhandenes Kupfer (BR_5V: 0,4 mm, DRU)."""
        a0 = max(abstand, klassen_abstand(netz))

        def ab(n):                                  # 1 µm Toleranz gegen Rundungsrauschen bei exakt passenden Lücken
            return max(a0, klassen_abstand(n)) - 0.001
        if art == "via":
            p, r, bohr = geo
            for pr, ly, pn in pads:
                if pn != netz and _rp(p, pr) < r + ab(pn):
                    return False
            for s in self.seg:
                if s["netz"] != netz and _ps(p, s["a"], s["b"]) < r + s["w"] / 2 + ab(s["netz"]):
                    return False
            for v in self.via:
                if v["netz"] != netz:
                    if _pp(p, v["p"]) < r + v["d"] / 2 + ab(v["netz"]) or _pp(p, v["p"]) < bohr / 2 + v["bohr"] / 2 + 0.5 + 1e-3:
                        return False
                elif _pp(p, v["p"]) < bohr / 2 + v["bohr"] / 2 + 0.25:
                    return False
            return True
        a, b, w = geo
        for pr, ly, pn in pads:
            if pn != netz and lage in ly and _rs(a, b, pr) < w / 2 + ab(pn):
                return False
        for s in self.seg:
            if s["netz"] != netz and s["lage"] == lage and _ss(a, b, s["a"], s["b"]) < w / 2 + s["w"] / 2 + ab(s["netz"]):
                return False
        for v in self.via:
            if v["netz"] != netz and _ps(v["p"], a, b) < w / 2 + v["d"] / 2 + ab(v["netz"]):
                return False
        return True

    def setzen(self, board, netinfo):
        import pcbnew
        from pcbnew import FromMM as mm
        ox, oy = URSPRUNG
        lagen = {"F": pcbnew.F_Cu, "B": pcbnew.B_Cu, "I1": pcbnew.In1_Cu, "I2": pcbnew.In2_Cu}
        for s in self.seg:
            t = pcbnew.PCB_TRACK(board)
            t.SetStart(pcbnew.VECTOR2I(mm(ox + s["a"][0]), mm(oy + s["a"][1])))
            t.SetEnd(pcbnew.VECTOR2I(mm(ox + s["b"][0]), mm(oy + s["b"][1])))
            t.SetWidth(mm(s["w"]))
            t.SetLayer(lagen[s["lage"]])
            t.SetNet(netinfo[s["netz"]])
            t.SetLocked(True)
            board.Add(t)
        for v in self.via:
            t = pcbnew.PCB_VIA(board)
            t.SetPosition(pcbnew.VECTOR2I(mm(ox + v["p"][0]), mm(oy + v["p"][1])))
            t.SetWidth(mm(v["d"]))
            t.SetDrill(mm(v["bohr"]))
            t.SetNet(netinfo[v["netz"]])
            t.SetLocked(True)
            board.Add(t)


def pad_hindernisse(board):
    """[(Rechteck x0,y0,x1,y1), Lagen {'F','B'}, Netz] aller Pads (Platinenkoordinaten)."""
    import pcbnew
    ox, oy = URSPRUNG
    aus = []
    for fp in board.GetFootprints():
        for p in fp.Pads():
            bb = p.GetBoundingBox()
            r = (pcbnew.ToMM(bb.GetLeft()) - ox, pcbnew.ToMM(bb.GetTop()) - oy,
                 pcbnew.ToMM(bb.GetRight()) - ox, pcbnew.ToMM(bb.GetBottom()) - oy)
            ly = set()
            if p.IsOnLayer(pcbnew.F_Cu):
                ly.add("F")
            if p.IsOnLayer(pcbnew.B_Cu):
                ly.add("B")
            if p.GetDrillSize().x > 0:
                ly |= {"F", "B", "I1", "I2"}
            if not ly:
                continue                                   # reine Pasten-/Maskenpads (z. B. Wärmepad-Fenster)
            aus.append((r, ly, p.GetNetname() or f"#pad-{fp.GetReference()}-{p.GetNumber()}"))
    return aus


# Paare: Bahnbreite/Abstand (Näherung IPC-2141 für JLC04161H-7628, h = 0,21 mm, εr 4,4 – mit dem
# JLC-Impedanzrechner bestätigen): USB 90 Ω -> 0,25/0,15 mm (Raster 0,40), Ethernet 100 Ω -> 0,20/0,20 mm (0,40)
USB_W, ETH_W, PAAR_D = 0.25, 0.2, 0.2
VIA_LAENGE = 1.53          # Durchkontaktierung L1 -> L4 (für den Längenabgleich mitgezählt)
PAARE = {"CAM": ("CAM_DP", "CAM_DM"), "HUB": ("HUB_DP", "HUB_DM"), "OTG": ("OTG_DP", "OTG_DM"),
         "ETH1": ("ETH1_P", "ETH1_N"), "ETH0": ("ETH0_P", "ETH0_N"),
         "ETH_A": ("ETH_A_P", "ETH_A_N"), "ETH_B": ("ETH_B_P", "ETH_B_N"),
         "DSI_C": ("DSI1_C_P", "DSI1_C_N"), "DSI_D1": ("DSI1_D1_P", "DSI1_D1_N"), "DSI_D0": ("DSI1_D0_P", "DSI1_D0_N")}
DSI_W = 0.2                # DSI 100 Ω wie Ethernet: 0,20/0,20 mm


def feste_leitungen(K, pp):
    """Alle fest verlegten Verbindungen (Paare, J1-Fächer, J10, Ethernet, Leistungsstreifen) in K eintragen.
    pp(ref, pin) -> Padmitte (x, y)."""
    # ---------- J1: Drucker-5V-Kamm (45–50), Fluchtwege mit Vias ----------
    br = [pp("J1", str(n)) for n in BR_5V]
    for x, y in br:
        K.bahn((x, y), (-3.7, y), 0.25, "BR_5V")
    K.bahn((-3.7, min(y for _, y in br)), (-3.7, max(y for _, y in br)), 0.4, "BR_5V")
    r38_1 = pp("R38", "1")                  # LED-Vorwiderstand direkt an den Kamm (Freerouting fand den Weg nicht)
    K.zug([(-3.7, max(y for _, y in br)), (-3.7, r38_1[1]), r38_1], 0.4, "BR_5V")
    for n, netz in ((26, "B26_UART2_TX"), (28, "B28_UART2_RX"), (30, "B30_UART4_TX"), (32, "B32_UART4_RX")):
        x, y = pp("J1", str(n))
        K.zug([(x, y), (-3.3, y)], 0.2, netz)
        K.via_setzen((-3.3, y), netz, 0.5, 0.3)
    # unter dem Gehäuse nach links (Pad-Westende −5,5): GND 18/33 (spiegelsicher) und RS485 19/20
    for n, netz, ziel in ((33, "J1_GND", (-6.2, 29.1)), (20, "RS485_A", (-6.2, 35.6)), (19, "RS485_B", (-6.9, 36.35)),
                          (18, "J1_GND", (-6.2, 36.85))):
        x, y = pp("J1", str(n))
        K.zug([(x, y), (x - 0.6, y), ziel], 0.15, netz)
        K.via_setzen(ziel, netz, 0.5, 0.3)
    # ---------- Ethernet Leitungsseite: J1 1/2/4/5 -> T1 16/14/11/9 (verschachtelt, kreuzungsfrei) ----------
    t = {n: pp("T1", str(n)) for n in range(1, 17) if n not in (4, 5, 12, 13)}
    j = {n: pp("J1", str(n)) for n in (1, 2, 4, 5)}
    a_p = [j[1], (-2.1, j[1][1]), (-2.1, t[16][1] + 0.55), (-1.55, t[16][1]), t[16]]
    a_n = [j[2], (-2.7, j[2][1]), (-2.7, t[14][1] + 0.6), (-2.1, t[14][1]), t[14]]
    b_p = [j[4], (-3.3, j[4][1]), (-3.3, t[11][1] + 0.6), (-2.7, t[11][1]), t[11]]
    b_n = [j[5], (-3.9, j[5][1]), (-3.9, t[9][1] + 0.55), (-3.35, t[9][1]), t[9]]
    # Längenabgleich (≤ 0,15 mm): Beulen in die kürzere Leitung (A_P nach Süden, B_P nach Osten)
    a_p, a_n = abgleichen(a_p, a_n, segment={"a": 0, "b": 0}, s0={"a": -4.05, "b": -4.05}, seite={"a": 1, "b": -1})
    b_p, b_n = abgleichen(b_p, b_n, segment={"a": 1, "b": 1}, s0={"a": 39.9, "b": 40.5}, seite={"a": 1, "b": -1},
                          n=2, c=0.15, breite=0.3)
    K.zug(a_p, ETH_W, "ETH_A_P", gruppe="ETH_A")
    K.zug(a_n, ETH_W, "ETH_A_N", gruppe="ETH_A")
    K.zug(b_p, ETH_W, "ETH_B_P", gruppe="ETH_B")
    K.zug(b_n, ETH_W, "ETH_B_N", gruppe="ETH_B")
    # Mittelanzapfungen: Vias unter dem Übertragerkörper (Bob-Smith-R und CT-Kondensator unten)
    for n, netz, dx in ((15, "ETH_CT_A", 1.5), (10, "ETH_CT_B", 1.5), (2, "ETH_CT_PHY", -1.5), (7, "ETH_CT_PHY", -1.5)):
        x, y = t[n]
        K.zug([(x, y), (x + dx, y)], 0.3, netz)
        K.via_setzen((x + dx, y), netz, 0.6, 0.3)
    # ---------- Ethernet PHY-Seite: CM5 Paar 1 (4/6) -> T1 1/3, Paar 0 (10/12) -> T1 6/8 (P/N getauscht) ----------
    for name, (p_n, n_n), (tp, tn), y_m in (("ETH1", (4, 6), (1, 3), (t[1][1] + t[3][1]) / 2),
                                            ("ETH0", (10, 12), (6, 8), (t[6][1] + t[8][1]) / 2)):
        links_pin, rechts_pin = cm5_pad(p_n), cm5_pad(n_n)          # Pad mit kleinerem x = links bei Fahrt nach Norden
        xm = (links_pin[0] + rechts_pin[0]) / 2
        mitte = [(xm, links_pin[1]), (xm, y_m + 0.6), (xm - 0.6, y_m), (11.4, y_m)]
        li, re = versatz(mitte, -PAAR_D), versatz(mitte, PAAR_D)
        netz_li, netz_re = CM5_SIGNALE[p_n], CM5_SIGNALE[n_n]   # links der Fahrt nach Norden = Westpad
        # Fächer zu den Übertragerpads: links der Fahrtrichtung = Süden beim Lauf nach Westen
        t_sued, t_nord = (t[tp], t[tn]) if t[tp][1] > t[tn][1] else (t[tn], t[tp])
        ende_li = [(10.33, t_sued[1]), t_sued]
        ende_re = [(10.33, t_nord[1]), t_nord]
        lz, rz = li + ende_li, re + ende_re
        il = [k for k in range(len(lz) - 1) if abs(lz[k][1] - (y_m + PAAR_D)) < 1e-3 and abs(lz[k + 1][1] - (y_m + PAAR_D)) < 1e-3][0]
        ir = [k for k in range(len(rz) - 1) if abs(rz[k][1] - (y_m - PAAR_D)) < 1e-3 and abs(rz[k + 1][1] - (y_m - PAAR_D)) < 1e-3][0]
        lz, rz = abgleichen(lz, rz, segment={"a": il, "b": ir}, s0={"a": 22.0, "b": 22.0}, seite={"a": 1, "b": -1})
        K.zug(lz, ETH_W, netz_li, gruppe=name)
        K.zug(rz, ETH_W, netz_re, gruppe=name)
        for (x, y), netz in ((t_sued, netz_li), (t_nord, netz_re)):
            K.via_setzen((9.6, y), netz, 0.5, 0.3, gruppe="ESD-Abgriff")
    # ---------- USB Kamera: J1 38/37 -> D1 -> Ost (unter F1) -> Nord (x 8,8, östlich von J7) -> Ost -> (DM über L4)
    #            -> CM5 134/136 ----------
    d1 = {n: pp("D1", str(n)) for n in (1, 3, 4, 6)}
    j38, j37 = pp("J1", "38"), pp("J1", "37")
    p134, p136 = cm5_pad(134), cm5_pad(136)
    mitte = [(2.6, 20.9), (8.4, 20.9), (8.8, 20.5), (8.8, 10.6), (9.4, 10.0), (25.8, 10.0)]
    m_li, m_re = versatz(mitte, -PAAR_D), versatz(mitte, PAAR_D)      # links (Nord/West) = DP
    dp = [j38, (d1[1][0] - 0.8, j38[1]), (d1[1][0], j38[1] - 0.8), d1[1], d1[6], (d1[6][0], m_li[0][1] + 0.25),
          (d1[6][0] + 0.25, m_li[0][1])] + m_li + [(p134[0] - 0.5, m_li[-1][1]), (p134[0], m_li[-1][1] + 0.5), p134]
    dm_f1 = [j37, (d1[3][0] - 0.8, j37[1]), (d1[3][0], j37[1] - 0.8), d1[3], d1[4], (d1[4][0], m_re[0][1])] + m_re + \
        [(26.6, 10.8)]
    dm_b = [(26.6, 10.8), (28.0, 9.4), (p136[0] + 0.5, 9.4)]
    dm_f2 = [(p136[0] + 0.5, 9.4), (p136[0], 9.9), p136]
    # Längenabgleich: DM hat 2 Vias mehr -> Beulen in DP (Lauf y = 9,8, Richtung Norden)
    l_dm = laenge(dm_f1) + laenge(dm_b) + laenge(dm_f2) + 2 * VIA_LAENGE
    extra = l_dm - laenge(dp)
    if extra > 0.02:
        i = [k for k in range(len(dp) - 1) if abs(dp[k][1] - 9.8) < 1e-3 and abs(dp[k + 1][1] - 9.8) < 1e-3][0]
        dp, _ = beulen(dp, i, 13.6, 2, extra, -1)
    K.zug(dp, USB_W, "CAM_DP", gruppe="CAM")
    K.zug(dm_f1, USB_W, "CAM_DM", gruppe="CAM")
    K.zug(dm_b, USB_W, "CAM_DM", "B", gruppe="CAM")
    K.zug(dm_f2, USB_W, "CAM_DM", gruppe="CAM")
    K.via_setzen((26.6, 10.8), "CAM_DM", 0.5, 0.3, gruppe="CAM")
    K.via_setzen((p136[0] + 0.5, 9.4), "CAM_DM", 0.5, 0.3, gruppe="CAM")
    # ---------- USB Hub: J1 35/34 -> D2 -> Ost -> Nord -> CM5 163/165 ----------
    d2 = {n: pp("D2", str(n)) for n in (1, 3, 4, 6)}
    j35, j34 = pp("J1", "35"), pp("J1", "34")
    p163, p165 = cm5_pad(163), cm5_pad(165)
    yo = p163[1] + 0.35
    hdp = [j35, (d2[1][0] - 1.95, j35[1]), (d2[1][0] - 1.25, d2[1][1]), d2[1], d2[6], (d2[6][0] + 0.8, d2[6][1]),
           (d2[6][0] + 1.4, 28.0), (p163[0] - 0.7, 28.0), (p163[0], 27.3), (p163[0], yo), p163]
    hdm = [j34, (d2[3][0] - 1.95, j34[1]), (d2[3][0] - 1.25, d2[3][1]), d2[3], d2[4], (d2[4][0] + 0.8, d2[4][1]),
           (d2[4][0] + 1.7, 28.4), (p165[0] - 0.7, 28.4), (p165[0], 27.7), (p165[0], yo), p165]
    hdp, hdm = abgleichen(hdp, hdm, segment={"a": [k for k in range(len(hdp) - 1) if abs(hdp[k][1] - 28.0) < 1e-3 and abs(hdp[k + 1][1] - 28.0) < 1e-3][0], "b": [k for k in range(len(hdm) - 1) if abs(hdm[k][1] - 28.4) < 1e-3 and abs(hdm[k + 1][1] - 28.4) < 1e-3][0]},
                          s0={"a": 15.0, "b": 15.0}, seite={"a": -1, "b": 1})
    K.zug(hdp, USB_W, "HUB_DP", gruppe="HUB")
    K.zug(hdm, USB_W, "HUB_DM", gruppe="HUB")
    # ---------- Notfall-USB-C J10 (OTG): A6–B6 oben verbunden, A7–B7 über L4; Kreuzung P/N an der Buchse ----------
    a6, a7, b6, b7 = (pp("J10", n) for n in ("A6", "A7", "B6", "B7"))
    K.zug([a6, (a6[0], a6[1] + 0.36), (b6[0], b6[1] - 0.36), b6], 0.15, "OTG_DP", gruppe="OTG-A")
    va, vb = (a7[0], 1.75), (b6[0] + 0.2, 5.6)
    K.zug([a7, va], 0.15, "OTG_DM", gruppe="OTG-A")
    K.via_setzen(va, "OTG_DM", 0.5, 0.3, gruppe="OTG-A")
    K.zug([b6, vb], 0.15, "OTG_DP", gruppe="OTG")
    K.via_setzen(vb, "OTG_DP", 0.5, 0.3, gruppe="OTG")
    vdm = (18.75, 7.9)
    K.zug([b7, (b7[0], 6.4), (18.25, 7.4), vdm], 0.15, "OTG_DM", gruppe="OTG")
    K.via_setzen(vdm, "OTG_DM", 0.5, 0.3, gruppe="OTG")
    K.zug([va, (18.85, 1.75), (18.85, 7.65), vdm], 0.15, "OTG_DM", "B", gruppe="OTG-A")
    d3 = {n: pp("D3", str(n)) for n in (1, 3, 4, 6)}           # unten: 3/4 (West) = DP, 1/6 (Ost) = DM
    p103, p105 = cm5_pad(103), cm5_pad(105)
    v_n, v_p = (p103[0] - 0.2, p103[1] + 1.5), (p105[0] + 0.2, p105[1] + 2.2)
    odp = [vb, (vb[0], 8.0), (d3[4][0], 8.7), d3[4], d3[3], (d3[3][0], 12.4), (d3[3][0], 15.8), (d3[3][0] + 0.6, 16.4),
           (v_p[0] - 0.6, 16.4), v_p]
    odm = [vdm, (d3[6][0] - 0.65, vdm[1]), (d3[6][0], vdm[1] + 0.65), d3[6], d3[1], (d3[1][0], 12.4),
           (d3[3][0] + 0.4, 13.95), (d3[3][0] + 0.4, 15.4), (d3[3][0] + 0.4 + 0.6, 16.0), (v_n[0] - 0.4, 16.0), v_n]
    dp_f = _pp(b6, vb) + _pp(v_p, (p105[0], v_p[1] - 0.4)) + abs(v_p[1] - 0.4 - p105[1])
    dm_f = laenge([b7, (b7[0], 6.4), (18.25, 7.4), vdm]) + _pp(v_n, (p103[0], v_n[1] - 0.4)) + abs(v_n[1] - 0.4 - p103[1])
    zl = (laenge(odm) + dm_f) - (laenge(odp) + dp_f)
    if zl > 0.02:
        odp, _ = beulen(odp, [k for k in range(len(odp) - 1) if abs(odp[k][1] - 16.4) < 1e-3 and abs(odp[k + 1][1] - 16.4) < 1e-3][0], 21.0, 1, zl, 1)
    elif zl < -0.02:
        odm, _ = beulen(odm, [k for k in range(len(odm) - 1) if abs(odm[k][1] - 16.0) < 1e-3 and abs(odm[k + 1][1] - 16.0) < 1e-3][0], 21.0, 1, -zl, -1)
    K.zug(odp, USB_W, "OTG_DP", "B", gruppe="OTG")
    K.zug(odm, USB_W, "OTG_DM", "B", gruppe="OTG")
    for v, netz, pad in ((v_n, "OTG_DM", p103), (v_p, "OTG_DP", p105)):
        K.via_setzen(v, netz, 0.5, 0.3, gruppe="OTG")
        K.zug([v, (pad[0], v[1] - 0.4 if netz == "OTG_DM" else v[1] - 0.4), (pad[0], pad[1] + 0.35), pad], USB_W, netz,
              gruppe="OTG")
    # ---------- J10: VBUS (A4–B9, A9–B4) über zwei Vias + Brücke auf L3, CC1/CC2 heraus ----------
    a4, a9, b4, b9, a5, b5 = (pp("J10", n) for n in ("A4", "A9", "B4", "B9", "A5", "B5"))
    K.bahn(a4, b9, 0.2, "J10_VBUS")
    K.bahn(a9, b4, 0.2, "J10_VBUS")
    vw, vo = (15.5, 3.6), (19.6, 3.6)
    K.bahn((a4[0], 3.6), vw, 0.2, "J10_VBUS")
    K.bahn((a9[0], 3.6), vo, 0.2, "J10_VBUS")
    K.via_setzen(vw, "J10_VBUS", 0.5, 0.3)
    K.via_setzen(vo, "J10_VBUS", 0.5, 0.3)
    K.zug([vw, (16.0, 2.6), (19.0, 2.6), vo], 0.5, "J10_VBUS", "I2")
    K.zug([a5, (a5[0], 1.5)], 0.2, "J10_CC1")
    K.via_setzen((a5[0], 1.5), "J10_CC1", 0.5, 0.3)
    K.zug([b5, (b5[0], 4.0), (b5[0] - 0.1, 3.6)], 0.2, "J10_CC2")
    K.via_setzen((b5[0] - 0.1, 3.6), "J10_CC2", 0.5, 0.3)
    # ---------- Wandler U7: VIN 9–12 gebrückt (EN liegt an VIN) ----------
    u7v = [pp("U7", n) for n in ("9", "10", "11", "12")]
    K.bahn((u7v[0][0], min(y for _, y in u7v)), (u7v[0][0], max(y for _, y in u7v)), 0.25, "VIN24", "B")
    # ---------- CM5-5V: Kamm an den Pins 77–87 (Oberseite), Vias, Streifen unten zu R24 ----------
    p5 = [cm5_pad(n) for n in CM5_5V_PINS]
    for x, y in p5:
        K.bahn((x, y), (x, y + 0.85), 0.2, "CM5_5V")
    x0, x1 = min(x for x, _ in p5), max(x for x, _ in p5)
    K.bahn((x0, 49.25), (x1, 49.25), 0.9, "CM5_5V")
    for x in (x0 + 0.3, x1 - 0.3):
        K.bahn((x, 49.25), (x, 50.75), 0.8, "CM5_5V")
    K.bahn((x0 - 0.5, 50.75), (x1 + 0.7, 50.75), 1.2, "CM5_5V")
    for i in range(4):
        K.via_setzen((x0 - 0.3 + 1.0 * i, 50.75), "CM5_5V", 0.6, 0.3)
    r24_2, r24_1 = pp("R24", "2"), pp("R24", "1")
    K.bahn((x0 - 0.5, 50.75), (r24_2[0], 50.75), 1.2, "CM5_5V", "B")
    K.bahn((r24_2[0], 50.75), r24_2, 1.2, "CM5_5V", "B")
    # 3V3/GPIO_VREF: Pins 78, 84, 86 (innen) verbinden + Via
    p3 = [cm5_pad(n) for n in (78, 84, 86)]
    for x, y in p3:
        K.bahn((x, y), (x, y + 0.83), 0.25, "CM5_3V3")
    K.bahn((p3[0][0], p3[0][1] + 0.83), (p3[-1][0], p3[0][1] + 0.83), 0.25, "CM5_3V3")
    K.bahn((p3[1][0], p3[0][1] + 0.83), (p3[1][0], p3[0][1] + 1.5), 0.3, "CM5_3V3")
    K.via_setzen((p3[1][0], p3[0][1] + 1.5), "CM5_3V3", 0.5, 0.3)
    # ---------- Power-Mux U8 (unten): OUT -> R24, IN1 <- Wandler-Stamm (oben) ----------
    o1, o8, i7 = pp("U8", "1"), pp("U8", "8"), pp("U8", "7")
    K.bahn((o1[0], o1[1] - 0.4), (o8[0], o8[1] + 0.5), 0.4, "MUX_OUT", "B")
    K.zug([(o8[0], o8[1] + 0.5), (o8[0] - 0.25, 48.3), (r24_1[0], 49.6), (r24_1[0], r24_1[1])], 0.6, "MUX_OUT", "B")
    vin1 = (i7[0] + 0.05, 49.3)
    K.zug([(i7[0], i7[1] + 0.45), vin1], 0.4, "WANDLER_5V", "B")
    K.via_setzen(vin1, "WANDLER_5V", 0.6, 0.3)
    K.via_setzen((vin1[0] + 1.0, 49.3), "WANDLER_5V", 0.6, 0.3)
    K.bahn(vin1, (vin1[0] + 1.0, 49.3), 0.6, "WANDLER_5V", "B")
    l1_2, l1_1 = pp("L1", "2"), pp("L1", "1")
    # Stamm auf In2 (L3 = Versorgungslage), damit L1 für das DSI-Bündel frei bleibt; 4 Vias in der Spalte zwischen
    # C15/C16 (GND, unten) und C17, unterhalb der J9b-Lasche; 2 Vias an U8
    xs2 = 56.22
    K.bahn(l1_2, (xs2, l1_2[1]), 2.0, "WANDLER_5V")
    K.bahn((xs2, 4.3), (xs2, 7.3), 1.0, "WANDLER_5V")
    for yv in (4.3, 5.3, 6.3, 7.3):
        K.via_setzen((xs2, yv), "WANDLER_5V", 0.6, 0.3)
    K.zug([(xs2, 4.3), (xs2, 7.5), (56.0, 8.0), (56.0, 48.3), (vin1[0] + 1.0, 49.3)], 2.0, "WANDLER_5V", "I2")
    K.bahn((vin1[0] + 1.0, 49.3), vin1, 0.8, "WANDLER_5V")
    # ---------- Schaltknoten Wandler: U7 SW (unten) -> Vias -> L1 Pad 1 (oben) ----------
    sw = [pp("U7", n) for n in ("1", "2", "3")]
    xs = sw[0][0]
    K.bahn((xs, min(y for _, y in sw)), (xs, max(y for _, y in sw)), 0.25, "WANDLER_SW", "B")
    ym = sum(y for _, y in sw) / 3
    K.bahn((xs, ym), (xs + 0.5, ym), 0.3, "WANDLER_SW", "B")
    K.bahn((xs + 0.5, ym), (xs + 1.0, ym), 1.0, "WANDLER_SW", "B")
    for dy in (-0.5, 0.5):
        K.via_setzen((xs + 1.0, ym + dy), "WANDLER_SW", 0.6, 0.3)
        K.bahn((xs + 1.0, ym + dy), (l1_1[0], ym + dy), 0.8, "WANDLER_SW")
    K.bahn((xs + 1.0, ym - 0.5), (xs + 1.0, ym + 0.5), 1.0, "WANDLER_SW", "B")
    # ---------- DSI1 (MIPI1) -> J12 an Kante C: CM5 175/177 (D0), 181/183 (D1), 187/189 (Takt), 100 Ω 0,20/0,20 ----------
    # Aus der ungeraden Reihe B nach Süden, 45°-Knick nach Osten (Reihenfolge Takt/D1/D0 von Nord nach Süd wie an
    # J12, keine Kreuzung), nur L1 über der GND-Lage In1, keine Vias. Verschachtelte Paarbeulen (nach Norden) gleichen
    # Takt und D1 auf D0 ab, Innen-Abgleich je Paar im senkrechten Stück, vor J12 Auffächerung 0,4 -> 0,5 mm.
    dsi = (("DSI_C", 189, 187, "9", "8"), ("DSI_D1", 183, 181, "6", "5"), ("DSI_D0", 177, 175, "3", "2"))
    xf = pp("J12", "2")[0] - 1.4
    mitten = {}
    for name, pin_p, pin_n, j_p, j_n in dsi:
        xm, y0 = (cm5_pad(pin_p)[0] + cm5_pad(pin_n)[0]) / 2, cm5_pad(pin_p)[1]
        ym = (pp("J12", j_p)[1] + pp("J12", j_n)[1]) / 2
        mitten[name] = [(xm, y0), (xm, ym - 1.0), (xm + 1.0, ym), (xf, ym)]
    ziel, s_c = laenge(mitten["DSI_D0"]), 54.0
    mitten["DSI_D1"], _ = beulen(mitten["DSI_D1"], 2, s_c + 1.5, 1, ziel - laenge(mitten["DSI_D1"]), -1, 0.5, 1.0)
    mitten["DSI_C"], _ = beulen(mitten["DSI_C"], 2, s_c, 1, ziel - laenge(mitten["DSI_C"]), -1, 0.5, 4.0)
    for name, pin_p, pin_n, j_p, j_n in dsi:
        netz_p, netz_n = PAARE[name]
        m, ym, y0 = mitten[name], mitten[name][-1][1], mitten[name][0][1]
        a = versatz(m, -(DSI_W + PAAR_D) / 2) + [(xf + 0.3, ym - 0.25), pp("J12", j_p)]   # P: links der Laufrichtung
        b = versatz(m, (DSI_W + PAAR_D) / 2) + [(xf + 0.3, ym + 0.25), pp("J12", j_n)]
        a, b = abgleichen(a, b, segment={"a": 0, "b": 0}, s0={"a": y0 + 2.0, "b": y0 + 2.0}, seite={"a": 1, "b": -1}, n=2)
        K.zug(a, DSI_W, netz_p, gruppe=name)
        K.zug(b, DSI_W, netz_n, gruppe=name)


def j12_gnd(K, pads, pp):
    """GND-Pins 1/4/7/10 von J12 (zwischen den DSI-Paaren): Stich nach Westen + Via auf In1, Lage frei geprüft."""
    for n in ("1", "4", "7", "10"):
        x, y = pp("J12", n)
        dy = {"1": 0.25, "10": -0.25}.get(n, 0.0)
        for xv in [round(70.9 - 0.2 * i, 2) for i in range(14)]:
            v = (xv, y + dy)
            pfad = [(x, y), (x - 1.0, y), v] if dy else [(x, y), v]
            if K.frei("via", (v, 0.25, 0.3), "GND", None, pads) and \
                    all(K.frei("seg", (a, b, 0.2), "GND", "F", pads) for a, b in zip(pfad, pfad[1:])):
                K.zug(pfad, 0.2, "GND", gruppe="J12-GND")
                K.via_setzen(v, "GND", 0.5, 0.3, gruppe="J12-GND")
                break
        else:
            raise RuntimeError(f"J12 Pin {n}: kein Platz für das GND-Via")
    # GND-Kamm östlich der Padreihe (unter dem Steckergehäuse): alle GND-Pins 1…19 + 2 Vias nördlich der Padreihe
    # (Lage wie früher im Wärmepad der Toolhead-eFuse U9, die mit Weg A entfallen ist)
    xk = pp("J12", "1")[0] + 1.25
    y_ep = (25.3, 26.7)
    stuecke = [((pp("J12", n)[0], pp("J12", n)[1]), (xk, pp("J12", n)[1])) for n in ("1", "4", "7", "10", "13", "16", "19")]
    stuecke.append(((xk, y_ep[0]), (xk, pp("J12", "1")[1])))
    for a, b in stuecke:
        if not K.frei("seg", (a, b, 0.25), "GND", "F", pads):
            raise RuntimeError(f"J12-GND-Kamm: Abschnitt {a}–{b} nicht frei")
        K.bahn(a, b, 0.25, "GND", gruppe="J12-GND")
    for y in y_ep:
        if not K.frei("via", ((xk, y), 0.25, 0.3), "GND", None, pads):
            raise RuntimeError(f"J12-GND-Via ({xk}, {y}) nicht frei")
        K.via_setzen((xk, y), "GND", 0.5, 0.3, gruppe="J12-GND")


def cm5_5v_westen(K, pads, pp):
    """Stütz-C C19–C22 (unten an Kante D, westlich von U5): Streifen unten, 2 Vias, Abschnitt auf In2 bis zu den
    CM5-5V-Vias unter Reihe A (dort ist an Kante D unten kein Durchkommen an U5/C25 vorbei). U5 Pin 8/9 gebrückt."""
    c21, c22 = pp("C21", "1"), pp("C22", "1")
    r21 = (c22[0] + 3.8, c22[1] + 0.16)          # Ende des Streifens (früher R21 Pin 1)
    streifen = [pp("C19", "1"), pp("C20", "1"), c21, (c22[0] - 0.9, c21[1]), c22]
    if not all(K.frei("seg", (a, b, 0.8), "CM5_5V", "B", pads) for a, b in zip(streifen, streifen[1:])):
        raise RuntimeError("CM5-5V-Streifen C19–C22: kein Platz")
    u5_8, u5_9 = pp("U5", "8"), pp("U5", "9")
    K.bahn(u5_8, u5_9, 0.2, "CM5_5V", "B")
    K.zug(streifen, 0.8, "CM5_5V", "B")
    ziel = (44.0, 50.75)                       # westlichstes CM5-5V-Via unter Reihe A
    gesetzt = []
    for i in range(40):
        x = round(c22[0] + 0.9 + 0.1 * i, 2)
        y = round(c22[1] + (r21[1] - c22[1]) * (x - c22[0]) / (r21[0] - c22[0]), 3)
        v = (x, y)
        if gesetzt and _pp(v, gesetzt[-1]) < 0.9:
            continue
        knick = (round(x + (y - ziel[1]), 3), ziel[1])
        pfad = [v, knick, ziel]
        if K.frei("via", (v, 0.3, 0.3), "CM5_5V", None, pads) and K.frei("seg", (c22, v, 0.8), "CM5_5V", "B", pads) and \
                all(K.frei("seg", (a, b, 0.8), "CM5_5V", "I2", pads) for a, b in zip(pfad, pfad[1:])):
            K.bahn(c22, v, 0.8, "CM5_5V", "B")
            K.via_setzen(v, "CM5_5V", 0.6, 0.3)
            K.zug(pfad, 0.8, "CM5_5V", "I2")
            gesetzt.append(v)
            if len(gesetzt) == 2:
                return
    raise RuntimeError(f"CM5-5V-Westanbindung: nur {len(gesetzt)} Vias gefunden")


def vin24_wege(K, pads, pp):
    """24-V-Eingang an Kante A (Weg A, 24.10.2026): J7 Pin 2 (+24 V) -> F1 (Oberseite, 2,4 mm) -> F1 Pad 2 -> 6 Vias
    0,8/0,4 -> unten TVS D8 und Shunt R23 (senkrecht östlich der J7-Stifte, INA226 0x40 daneben unter J7) -> R23 Pad 2
    (VIN24) -> 3 Vias -> Stamm auf In2 (L3; östlich J7, unter H1/J10, an Kante B) bis vor den Wandler U7 -> 2 Vias ->
    C9/U7. Der Stamm führt nur noch den Wandlerstrom (≈ 0,6 A, Spitze ≈ 1,3 A); der frühere Abschnitt bis zur
    rechten Ecke (Toolhead J9) ist entfallen. Alles frei geprüft."""
    j7_p, f1_1, f1_2 = pp("J7", "2"), pp("F1", "1"), pp("F1", "2")     # J7 Pin 2 = +24 V
    r23_f, r23_v = pp("R23", "1"), pp("R23", "2")
    if not (r23_f[1] > r23_v[1] + 5.0 and abs(r23_f[0] - r23_v[0]) < 0.01):
        raise RuntimeError(f"R23: Pad 1 muss südlich von Pad 2 liegen ({r23_f}, {r23_v}) – R23_DREHUNG prüfen")
    c9_v = pp("C9", "1")
    via_l = [(4.6, 16.4), (5.6, 16.4), (6.6, 16.4), (7.4, 17.7), (7.4, 18.7), (7.4, 19.7)]
    xs = r23_v[0]                       # Stammachse = R23-Achse
    via_s = [(xs - 0.6, 7.9), (xs + 0.4, 7.9), (xs + 1.4, 7.9)]   # VIN24 nördlich von R23 (zwischen J7-GND-Pad und H1)
    ys = 9.9                            # Ostlauf unter H1 und J10
    xn, yn = 24.6, 1.9                  # Nordlauf vor JP_BOOT / Stamm an Kante B
    xw = round(c9_v[0] - 1.45, 2)       # Abstieg westlich von C9 (zwischen C13/R27 und C9)
    via_w = [(xw, c9_v[1] - 0.07), (xw, c9_v[1] + 0.93)]
    wege = [
        ("VIN24_ROH", "F", [j7_p, (f1_1[0], j7_p[1]), f1_1], 2.2, []),
        ("VIN24_F", "F", [(f1_2[0], f1_2[1] - 1.2), (f1_2[0], 16.4)], 1.2, []),
        ("VIN24_F", "F", [(4.6, 16.4), (6.6, 16.4)], 1.0, []),
        ("VIN24_F", "F", [(f1_2[0] + 0.9, f1_2[1]), (7.4, f1_2[1])], 1.2, []),
        ("VIN24_F", "F", [(7.4, 17.7), (7.4, 19.7)], 1.0, []),
        ("VIN24_F", "I2", [(4.6, 16.4), (6.6, 16.4)], 1.0, []),
        ("VIN24_F", "I2", [(7.4, 17.7), (7.4, 19.7)], 1.0, []),
        ("VIN24_F", "I2", [(6.6, 16.4), (7.4, 17.7)], 1.0, []),
        ("VIN24_F", "B", [(4.6, 16.4), (6.6, 16.4)], 1.0, []),
        ("VIN24_F", "B", [pp("D8", "1"), (pp("D8", "1")[0], 16.4), (4.6, 16.4)], 1.0, []),
        ("VIN24_F", "B", [(7.4, 17.7), (7.4, 19.7)], 1.0, []),
        ("VIN24_F", "B", [(6.6, 16.4), (7.4, 17.7)], 1.0, []),
        ("VIN24_F", "B", [(xs, 16.4), r23_f], 1.2, []),
        ("VIN24", "B", [r23_v, (xs, via_s[0][1])], 1.2, []),
        ("VIN24", "B", [via_s[0], via_s[-1]], 1.0, []),
        ("VIN24", "I2", [via_s[0], via_s[-1]], 1.0, []),
        ("VIN24", "I2", [(xs, via_s[0][1]), (xs, ys)], 1.6, []),     # 1,6 mm: Freihaltekreis H1 (Ø 6,5)
        ("VIN24", "I2", [(xs, ys), (xn, ys)], 2.0, []),
        ("VIN24", "I2", [(xn, ys), (xn, yn)], 2.0, []),
        ("VIN24", "I2", [(xn, yn), (xw, yn)], 2.0, []),
        ("VIN24", "I2", [(xw, yn), via_w[-1]], 1.0, []),
        ("VIN24", "B", [via_w[0], via_w[-1]], 0.8, []),
        ("VIN24", "B", [via_w[0], c9_v], 0.8, []),
    ]
    for netz, lage, pfad, w, _ in wege:
        for a, b in zip(pfad, pfad[1:]):
            if not K.frei("seg", (a, b, w), netz, lage, pads):
                raise RuntimeError(f"{netz}: Abschnitt {a}–{b} ({w} mm) auf {lage} nicht frei")
        K.zug(pfad, w, netz, lage, gruppe="24V-Eingang")
    for vp, netz, d, b in [(v, "VIN24_F", 0.8, 0.4) for v in via_l] + [(v, "VIN24", 0.8, 0.4) for v in via_s] + \
            [(v, "VIN24", 0.6, 0.3) for v in via_w]:
        if not K.frei("via", (vp, d / 2, b), netz, None, pads):
            raise RuntimeError(f"{netz}: Via {vp} nicht frei")
        K.via_setzen(vp, netz, d, b, gruppe="24V-Eingang")


def feste_kurzwege(K, pads, pp):
    """Kurze Verbindungen, die Freerouting zwischen Feinraster-Pins und Leistungsteilen nicht findet (frei geprüft):
    INA226-Messleitung U5 Pin 10 -> R24 (Kante D), ETH0_P an der ESD D7."""
    wege = []
    # U5 Pin 8/9 (CM5_5V, eingeschlossen von C25 und der Messleitung): Via zwischen Pinreihe und C25 + In2 zur
    # CM5-5V-Westanbindung
    u5_8 = pp("U5", "8")
    v5 = (round(u5_8[0] + 1.16, 3), u5_8[1])
    wege.append(("CM5_5V", "B", [u5_8, v5], 0.25, [v5]))
    wege.append(("CM5_5V", "I2", [v5, (v5[0], 51.4), (44.0, 50.75)], 0.5, []))
    u5_10, r24_1 = pp("U5", "10"), pp("R24", "1")
    wege.append(("MUX_OUT", "B", [u5_10, (u5_10[0] + 1.1, u5_10[1]), (u5_10[0] + 1.1, 55.58), (r24_1[0], 55.58),
                                  (r24_1[0], r24_1[1] + 1.2)], 0.2, []))
    # Ethernet-ESD D7 (unten): ETH0_P (Pad 7) zum Abgriff-Via – Freerouting fand den Weg zwischen ETH0_N-Via und
    # D7 in vielen Netzreihenfolgen nicht (24.10.2026)
    d7_7 = pp("D7", "7")
    v_e0p = [v["p"] for v in K.via if v["netz"] == "ETH0_P" and v["gruppe"] == "ESD-Abgriff"][0]
    wege.append(("ETH0_P", "B", [d7_7, (d7_7[0], 37.0), (d7_7[0] - 0.25, 36.75), (d7_7[0] - 0.25, v_e0p[1] + 0.45), v_e0p],
                 0.2, []))
    for netz, lage, pfad, w, vias in wege:
        for vp in vias:
            if not K.frei("via", (vp, 0.25, 0.3), netz, None, pads):
                raise RuntimeError(f"{netz}: Via {vp} nicht frei")
        for a, b in zip(pfad, pfad[1:]):
            if not K.frei("seg", (a, b, w), netz, lage, pads):
                raise RuntimeError(f"{netz}: Abschnitt {a}–{b} auf {lage} nicht frei")
        K.zug(pfad, w, netz, lage)
        for vp in vias:
            K.via_setzen(vp, netz, 0.5, 0.3)


def flaechen_fanout(K, pads, fps):
    """Vor dem Routing: Anschluss-Via (+ Stich) für jedes SMD-Pad von CM5_3V3 (Fläche In2) und für die GND-Pads der ICs
    und Dioden (Fläche In1) – Freerouting verlegt dann um sie herum. J1/J2/J12 haben eigene Anbindungen."""
    import pcbnew
    ox, oy = URSPRUNG
    sperr = [(x, y, T113_FREI / 2 + 0.35) for x, y in T113_LOECHER]
    ux0, uy0, ux1, uy1 = cm5_umriss()
    sperr += [(hx, hy, CM5_SCHRAUBE_UNTEN / 2 + 0.35) for hx, hy in
              ((ux0 + 3.5, uy0 + 3.5), (ux1 - 3.5, uy0 + 3.5), (ux0 + 3.5, uy1 - 3.5), (ux1 - 3.5, uy1 - 3.5))]
    sperr += [(fx, fy, 1.7 + 0.35) for fx, fy in PASSERMARKEN + PASSERMARKEN_UNTEN]

    def zulaessig(v):
        return X_LINKS + 0.8 <= v[0] <= X_RECHTS - 0.8 and 0.8 <= v[1] <= PLATINE_HOEHE - 0.8 and \
            all(math.hypot(v[0] - x, v[1] - y) >= r for x, y, r in sperr)
    fehlend = []
    for ref, fp in sorted(fps.items()):
        if ref in ("J1", "J2", "J12"):
            continue
        fc = _M(fp.GetPosition())
        for pad in fp.Pads():
            netz = pad.GetNetname()
            if pad.GetDrillSize().x > 0 or netz not in FLAECHENNETZE:
                continue
            passiv = netz == "GND" and not ref.startswith(("U", "D"))
            if passiv and ref not in GND_FANOUT_PASSIV:
                continue                                   # Passive binden über die GND-Flächen an
            bb = pad.GetBoundingBox()
            x0, y0 = pcbnew.ToMM(bb.GetLeft()) - ox, pcbnew.ToMM(bb.GetTop()) - oy
            x1, y1 = pcbnew.ToMM(bb.GetRight()) - ox, pcbnew.ToMM(bb.GetBottom()) - oy
            c = ((x0 + x1) / 2, (y0 + y1) / 2)
            if (x1 - x0) > 2.5 and (y1 - y0) > 2.5:
                continue                                   # Wärmepads: Flächenanbindung + Vias im Pad (fertigstellen)
            if any(min(_ps((sx, sy), s["a"], s["b"]) for sx, sy in [c]) < 0.05 and s["netz"] == netz for s in K.seg):
                continue                                   # schon fest angebunden
            lage = "B" if fp.IsFlipped() else "F"
            w = 0.2 if min(x1 - x0, y1 - y0) < 0.5 else 0.3
            # Richtungen: zuerst entlang der langen Padachse vom Bauteil weg, dann quer, dann die übrigen
            lang_x = (x1 - x0) >= (y1 - y0)
            sx = 1 if c[0] >= fc[0] else -1
            sy = 1 if c[1] >= fc[1] else -1
            richtungen = [(sx, 0), (0, sy), (0, -sy), (-sx, 0)] if lang_x else [(0, sy), (sx, 0), (-sx, 0), (0, -sy)]
            gefunden = None
            for dx, dy in richtungen:
                halb = (x1 - x0) / 2 if dx else (y1 - y0) / 2
                for k in range(8 if passiv else 16):
                    d = halb + 0.2 + 0.25 + 0.1 * k
                    for q in (0.0, 0.4, -0.4, 0.8, -0.8):
                        aus = (c[0] + dx * (halb + 0.05), c[1] + dy * (halb + 0.05))
                        v = (round(c[0] + dx * d + (q if dy else 0), 3), round(c[1] + dy * d + (q if dx else 0), 3))
                        pfad = [c, aus, v] if q else [c, v]
                        if not zulaessig(v) or not K.frei("via", (v, 0.25, 0.3), netz, None, pads):
                            continue
                        if all(K.frei("seg", (a, b, w), netz, lage, pads) for a, b in zip(pfad, pfad[1:])):
                            gefunden = (pfad, v)
                            break
                    if gefunden:
                        break
                if gefunden:
                    break
            if not gefunden:
                if not passiv:
                    fehlend.append(f"{ref}.{pad.GetNumber()}")
                continue
            pfad, v = gefunden
            K.zug(pfad, w, netz, lage, gruppe="Flaeche")
            K.via_setzen(v, netz, 0.5, 0.3, gruppe="Flaeche")
    return fehlend


def fanout_cm5(K, pads):
    """Fanout aller übrigen CM5-Pads: GND -> Via in die Lücke zwischen den Reihen; Signale -> Via nach außen
    (ungerade Reihe nach Süden, gerade nach Norden). GND gierig, Signale je Reihe mit Rücksetzsuche (Backtracking),
    alle Abstände geprüft (Pads aller Lagen für Vias, F.Cu für Stichleitungen)."""
    feste_pins = {4, 6, 10, 12, 103, 105, 134, 136, 163, 165, 175, 177, 181, 183, 187, 189, 78, 84, 86} | set(CM5_5V_PINS)
    pins = json.loads((HIER / "cm5_pins.json").read_text(encoding="utf-8"))["pins"]
    gnd, sig = [], []
    for n in range(1, 201):
        if n in feste_pins:
            continue
        name = pins[str(n)][0]
        netz = "GND" if name == "GND" else CM5_SIGNALE.get(n)
        if netz:
            (gnd if netz == "GND" else sig).append((n, netz))

    def kandidaten(n, netz):
        x, y = cm5_pad(n)
        luecke = -1 if n % 2 else 1          # Lücke zwischen den Reihen: ungerade nach Norden, gerade nach Süden
        richtung = luecke if netz == "GND" else -luecke
        ende_y = y + richtung * 0.35
        if netz == "GND":
            kand = [(0.4, 0.0), (0.4, 0.4), (0.4, -0.4), (0.9, 0.0), (0.9, 0.4), (0.9, -0.4)]
            w = 0.2
        else:
            kand = sorted(((tf, dx) for tf in (0.55, 1.15, 1.75, 2.35, 2.95, 3.55)
                           for dx in (0.0, -0.4, 0.4, -0.8, 0.8, -1.2, 1.2, -1.6, 1.6)), key=lambda c: (abs(c[1]), c[0]))
            w = 0.15
        for tief, dx in kand:
            v = (round(x + dx, 3), round(ende_y + richtung * tief, 3))
            knick = (x, round(ende_y + richtung * min(0.2, tief - 0.25), 3))
            pfad = [(x, y), knick, v] if dx else [(x, y), v]
            yield pfad, v, w

    def setzen(n, netz):
        for pfad, v, w in kandidaten(n, netz):
            if K.frei("via", (v, 0.25, 0.3), netz, None, pads) and \
                    all(K.frei("seg", (a, b, w), netz, "F", pads) for a, b in zip(pfad, pfad[1:])):
                K.zug(pfad, w, netz, gruppe="Fanout")
                K.via_setzen(v, netz, 0.5, 0.3, gruppe="Fanout")
                return True
        return False
    fehlend = [n for n, netz in gnd if not setzen(n, netz)]
    # Signale: je Reihe von West nach Ost, Rücksetzsuche
    reihen = {}
    for n, netz in sig:
        reihen.setdefault((n > 100, n % 2), []).append((n, netz))

    def suche(liste, i, budget):
        if i == len(liste):
            return True
        n, netz = liste[i]
        for pfad, v, w in kandidaten(n, netz):
            if budget[0] <= 0:
                return False
            budget[0] -= 1
            if K.frei("via", (v, 0.25, 0.3), netz, None, pads) and \
                    all(K.frei("seg", (a, b, w), netz, "F", pads) for a, b in zip(pfad, pfad[1:])):
                ns, nv = len(K.seg), len(K.via)
                K.zug(pfad, w, netz, gruppe="Fanout")
                K.via_setzen(v, netz, 0.5, 0.3, gruppe="Fanout")
                if suche(liste, i + 1, budget):
                    return True
                del K.seg[ns:]
                del K.via[nv:]
        return False
    for schl, liste in reihen.items():
        liste.sort(key=lambda a: cm5_pad(a[0])[0])
        if not suche(liste, 0, [200000]):
            # Rückfall: gierig, damit die übrigen Pins wenigstens gesetzt werden
            fehlend += [n for n, netz in liste if not setzen(n, netz)]
    return fehlend


def fest_aufteilen(board):
    """Feste (gesperrte) Leiterbahnen an jedem Knoten teilen: Endpunkte anderer Bahnen, Via-Mitten und
    Pad-Mitten desselben Netzes, die auf einer Bahn liegen. Freerouting erkennt Verbindungen nur an
    Bahnenden – T-Abzweige mitten auf einer Bahn zählt es sonst als unverlegt."""
    import pcbnew
    bahnen = [t for t in board.GetTracks() if t.GetClass() == "PCB_TRACK" and t.IsLocked()]
    vias = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA"]
    pads = [p_ for fp in board.GetFootprints() for p_ in fp.Pads()]
    n = 0
    for t in bahnen:
        # Kopien: GetStart/GetEnd liefern Verweise, SetEnd würde b_ sonst mit verändern
        a_ = pcbnew.VECTOR2I(t.GetStart().x, t.GetStart().y)
        b_ = pcbnew.VECTOR2I(t.GetEnd().x, t.GetEnd().y)
        dx, dy = b_.x - a_.x, b_.y - a_.y
        ll = dx * dx + dy * dy
        if ll == 0:
            continue
        kand = [o.GetStart() for o in bahnen if o is not t and o.GetNetCode() == t.GetNetCode()
                and o.GetLayer() == t.GetLayer()]
        kand += [o.GetEnd() for o in bahnen if o is not t and o.GetNetCode() == t.GetNetCode()
                 and o.GetLayer() == t.GetLayer()]
        kand += [v.GetPosition() for v in vias if v.GetNetCode() == t.GetNetCode()]
        kand += [p_.GetPosition() for p_ in pads if p_.GetNetCode() == t.GetNetCode() and p_.IsOnLayer(t.GetLayer())]
        teil = set()
        for q_ in kand:
            u_ = ((q_.x - a_.x) * dx + (q_.y - a_.y) * dy) / ll
            if not 1e-6 < u_ < 1 - 1e-6:
                continue
            px, py = a_.x + u_ * dx, a_.y + u_ * dy
            if abs(px - q_.x) <= 2000 and abs(py - q_.y) <= 2000:      # 2 µm
                teil.add((u_, q_.x, q_.y))
        if not teil:
            continue
        pkt = [pcbnew.VECTOR2I(x_, y_) for _, x_, y_ in sorted(teil)] + [b_]
        t.SetEnd(pkt[0])
        for c_, d_ in zip(pkt, pkt[1:]):
            neu = pcbnew.PCB_TRACK(board)
            neu.SetStart(c_)
            neu.SetEnd(d_)
            neu.SetWidth(t.GetWidth())
            neu.SetLayer(t.GetLayer())
            neu.SetNet(t.GetNet())
            neu.SetLocked(True)
            board.Add(neu)
        n += 1
    return n




def dsn_mischen(src, dst, seed):
    """Netzreihenfolge im DSN mischen (Startwert seed; 0 = unverändert). Freerouting verlegt je nach
    Reihenfolge unterschiedlich gut – mehrere Varianten, die beste wird genommen."""
    import random
    import re
    t = Path(src).read_text(encoding="utf-8")
    if seed:
        i = t.index("(network")
        j = t.index("(class", i)
        nets = re.findall(r"\s*\(net .*?\n\s*\)", t[i + len("(network"):j], re.S)
        random.Random(seed).shuffle(nets)
        t = t[:i] + "(network\n" + "".join(nets) + "\n    " + t[j:]
    Path(dst).write_text(t, encoding="utf-8")


def freerouting_varianten(jar, dsn, ses, umg, anzahl=24, parallel=6):
    """Freerouting mit `anzahl` Netzreihenfolgen laufen lassen (je `parallel` gleichzeitig) und die SES mit den
    wenigsten unverlegten Verbindungen (dann bester Punktzahl) nach `ses` kopieren."""
    import os
    import re
    import shutil
    import subprocess
    z = Path(dsn).parent
    env = dict(os.environ)
    for k_ in ("DISPLAY", "WAYLAND_DISPLAY"):
        env.pop(k_, None)
    env.update({"HOME": f"{umg}/home", "XDG_CONFIG_HOME": f"{umg}/xdg-config", "XDG_STATE_HOME": f"{umg}/xdg-state",
                "XDG_CACHE_HOME": f"{umg}/xdg-cache"})
    ergebnis = []
    s0 = int(os.environ.get("K2_SEED_START", "0"))     # weitere Netzreihenfolgen auf dieselbe Platine
    parallel = int(os.environ.get("K2_PARALLEL", parallel))
    for start in range(s0, s0 + anzahl, parallel):
        laeufe = []
        for seed in range(start, min(s0 + anzahl, start + parallel)):
            vd, vs, vl = z / f"variante{seed}.dsn", z / f"variante{seed}.ses", z / f"variante{seed}.log"
            dsn_mischen(dsn, vd, seed)
            vs.unlink(missing_ok=True)
            cmd = ["java", "-Djava.awt.headless=true", f"-Duser.home={umg}/home", "-jar", str(jar), "-de", str(vd),
                   "-do", str(vs), "--router.autorouter.max_passes=100", "--router.automatic_neckdown=false",
                   "--router.fanout.enabled=true", "--gui.enabled=false", "-mt", "1"]   # 1 Thread = wiederholbar
            laeufe.append((seed, vs, vl, subprocess.Popen(cmd, stdout=open(vl, "w"), stderr=subprocess.STDOUT, env=env)))
        for seed, vs, vl, pr in laeufe:
            pr.wait()
            m = re.findall(r"Auto-routing stage completed.*?final score: ([\d.]+) \((\d+) unrouted", vl.read_text())
            if m and vs.exists() and vs.stat().st_size:
                ergebnis.append((int(m[-1][1]), -float(m[-1][0]), seed))
                print(f"  Variante {seed}: {m[-1][1]} unverlegt, Punktzahl {m[-1][0]}")
            else:
                print(f"  Variante {seed}: kein Ergebnis")
    if not ergebnis:
        raise RuntimeError("Freerouting: keine Variante lieferte eine SES-Datei")
    # Reihenfolge für die Endauswahl (bauen.sh prüft die besten Kandidaten nacheinander mit DRC)
    (z / "kandidaten.txt").write_text("\n".join(str(sd) for _, _, sd in sorted(ergebnis)) + "\n")
    unv, _, seed = min(ergebnis)
    shutil.copy(z / f"variante{seed}.ses", ses)
    shutil.copy(z / f"variante{seed}.log", z / "freerouting.log")
    print(f"Freerouting: Variante {seed} gewählt ({unv} unverlegt).")


def ses_vias_bereinigen(board, ses):
    """Signal-Vias aus der SES-Datei streichen, an denen nur auf EINER Lage Leiterbahnen enden
    (Fanout-Reste von Freerouting). Objekte nach dem Import zu löschen bringt die Python-Anbindung
    von KiCad zum Absturz, darum wird die SES-Datei vor dem Import bereinigt."""
    import re
    import pcbnew
    text = Path(ses).read_text(encoding="utf-8")
    fest = {}                                   # (Netz, Lage) -> Endpunkte fester Bahnen (0,1 µm)
    for t in board.GetTracks():
        if t.GetClass() == "PCB_TRACK":
            for pt in (t.GetStart(), t.GetEnd()):
                fest.setdefault((t.GetNetname(), board.GetLayerName(t.GetLayer())), set()).add(
                    (round(pt.x / 100), round(-pt.y / 100)))
    tht = {(round(p_.GetPosition().x / 100), round(-p_.GetPosition().y / 100))
           for fp in board.GetFootprints() for p_ in fp.Pads() if p_.GetAttribute() == pcbnew.PAD_ATTRIB_PTH}
    raus = 0
    teile = re.split(r'(?=\(net ")', text)
    for i, teil in enumerate(teile):
        m = re.match(r'\(net "([^"]+)"', teil)
        if not m or m.group(1) == "GND":
            continue
        netz = m.group(1)
        punkte = {}
        for lage, pfad in re.findall(r'\(path (\S+) \d+\s+([-\d\s]+)\)', teil):
            z = [int(v) for v in pfad.split()]
            punkte.setdefault(lage, set()).update(zip(z[0::2], z[1::2]))
        for lage, pk in fest.items():
            if lage[0] == netz:
                punkte.setdefault(lage[1], set()).update(pk)

        def pruefe(mv):
            nonlocal raus
            xy = (int(mv.group(1)), int(mv.group(2)))
            lagen = [ly for ly, pk in punkte.items() if xy in pk]
            if len(lagen) <= 1 and xy not in tht:
                raus += 1
                return ""
            return mv.group(0)
        teile[i] = re.sub(r'\(via "[^"]+" (-?\d+) (-?\d+)\s*\)\s*', pruefe, teil)
    if raus:
        print(f"{raus} überflüssige Signal-Vias aus der SES gestrichen (nur auf einer Lage angeschlossen).")
    # Entwurf 3: Vias desselben Netzes, deren Bohrungen zu nah liegen (Mitte < 0,8 mm, Loch-Loch 0,5 mm),
    # zu einem Via zusammenlegen: zweites Via entfällt, seine Bahnenden springen auf das erste
    zus = 0
    for i, teil in enumerate(teile):
        vs = [(int(m.group(1)), int(m.group(2))) for m in re.finditer(r'\(via "[^"]+" (-?\d+) (-?\d+)\s*\)', teil)]
        ersetzt = {}
        for a_ in range(len(vs)):
            for b_ in range(a_ + 1, len(vs)):
                if vs[a_] in ersetzt or vs[b_] in ersetzt:
                    continue
                if 0 < math.hypot(vs[a_][0] - vs[b_][0], vs[a_][1] - vs[b_][1]) < 5500:     # 0,55 mm (0,1 µm)
                    ersetzt[vs[b_]] = vs[a_]
        if not ersetzt:
            continue
        for (bx, by), (ax, ay) in ersetzt.items():
            teil = re.sub(rf'\(via "[^"]+" {bx} {by}\s*\)\s*', "", teil, count=1)
            teil = re.sub(rf'(?<![\d-]){bx} {by}(?!\d)', f"{ax} {ay}", teil)
            zus += 1
        teile[i] = teil
    if zus:
        print(f"{zus} zu nahe Vias desselben Netzes zusammengelegt (Loch-Loch-Abstand).")
    # Vias verschiedener Netze mit Bohrungsabstand < 0,5 mm (Mitte < 0,8 mm): das Freerouting-Via um die
    # Differenz wegschieben (feste Vias der Platine bleiben), Bahnenden ziehen mit
    fest_v = {(round(t.GetPosition().x / 100), round(-t.GetPosition().y / 100))
              for t in board.GetTracks() if t.GetClass() == "PCB_VIA"}
    alle = []
    for i, teil in enumerate(teile):
        for m in re.finditer(r'\(via "[^"]+" (-?\d+) (-?\d+)\s*\)', teil):
            alle.append([i, int(m.group(1)), int(m.group(2))])
    alle += [[-1, x_, y_] for x_, y_ in fest_v]
    # Hindernisse für die neue Via-Lage (Einheit 0,1 µm, y nach oben wie in der SES): Bahnen der SES je Netzteil,
    # feste Bahnen und Pads der Platine je Netz
    ses_bahnen = []
    for i, teil in enumerate(teile):
        for lage, br, pfad in re.findall(r'\(path (\S+) (\d+)\s+([-\d\s]+)\)', teil):
            z = [int(v) for v in pfad.split()]
            for k_ in range(0, len(z) - 3, 2):
                ses_bahnen.append((i, z[k_], z[k_ + 1], z[k_ + 2], z[k_ + 3], int(br)))
    netz_von = {}
    for i, teil in enumerate(teile):
        m = re.match(r'\(net "([^"]+)"', teil)
        if m:
            netz_von[i] = m.group(1)
    platte = []
    for t_ in board.GetTracks():
        if t_.GetClass() == "PCB_TRACK":
            platte.append((t_.GetNetname(), round(t_.GetStart().x / 100), round(-t_.GetStart().y / 100),
                           round(t_.GetEnd().x / 100), round(-t_.GetEnd().y / 100), round(t_.GetWidth() / 100)))
    for fp in board.GetFootprints():
        for p_ in fp.Pads():
            bb = p_.GetBoundingBox()
            platte.append((p_.GetNetname(), round(bb.GetLeft() / 100), round(-bb.GetTop() / 100),
                           round(bb.GetRight() / 100), round(-bb.GetBottom() / 100), -1))

    def seg_abst(px, py, x1, y1, x2, y2):
        dx, dy = x2 - x1, y2 - y1
        ll = dx * dx + dy * dy
        u = 0 if ll == 0 else max(0, min(1, ((px - x1) * dx + (py - y1) * dy) / ll))
        return math.hypot(px - x1 - u * dx, py - y1 - u * dy)

    def reserve(nx, ny, eigen):
        """Kleinster Überschuss (0,1 µm) über die geforderten Abstände; ≥ 0 = Lage frei."""
        netz = netz_von.get(eigen, "")
        r_ = 1e9
        for v_ in alle:
            if v_[0] != eigen:
                r_ = min(r_, math.hypot(v_[1] - nx, v_[2] - ny) - 8000)
        for i, x1, y1, x2, y2, br in ses_bahnen:
            if i != eigen and netz_von.get(i) != netz:
                r_ = min(r_, seg_abst(nx, ny, x1, y1, x2, y2) - (3000 + br / 2 + 2000))
        for n_, x1, y1, x2, y2, br in platte:
            if n_ == netz:
                continue
            if br < 0:
                ddx = max(x1 - nx, 0, nx - x2)
                ddy = max(y2 - ny, 0, ny - y1)
                r_ = min(r_, math.hypot(ddx, ddy) - (3000 + 2000))
            else:
                r_ = min(r_, seg_abst(nx, ny, x1, y1, x2, y2) - (3000 + br / 2 + 2000))
        return r_
    geschoben = 0
    for a_ in range(len(alle) if VIAS_SCHIEBEN else 0):
        for b_ in range(len(alle)):
            if a_ == b_ or alle[b_][0] < 0 or alle[a_][0] == alle[b_][0]:
                continue
            ax, ay, bx, by = alle[a_][1], alle[a_][2], alle[b_][1], alle[b_][2]
            d_ = math.hypot(bx - ax, by - ay)
            if d_ == 0 or d_ >= 8000:
                continue
            # Notfall (Freerouting sieht Routing-Vias mit 0,8 mm und hält 0,5 mm Loch-Loch selbst ein): entlang der
            # Verbindungslinie auf 0,82 mm, bei Platzmangel bis 1,0 mm
            nx, ny = round(ax + (bx - ax) * 8200 / d_), round(ay + (by - ay) * 8200 / d_)
            for dist in (8200, 9000, 10000):
                kx, ky = round(ax + (bx - ax) * dist / d_), round(ay + (by - ay) * dist / d_)
                if reserve(kx, ky, alle[b_][0]) >= 0:
                    nx, ny = kx, ky
                    break
            teil = teile[alle[b_][0]]
            teil = re.sub(rf'(?<![\d-]){bx} {by}(?!\d)', f"{nx} {ny}", teil)
            teile[alle[b_][0]] = teil
            alle[b_][1], alle[b_][2] = nx, ny
            geschoben += 1
    if geschoben:
        print(f"{geschoben} Vias verschiedener Netze auseinandergeschoben (Loch-Loch 0,5 mm).")
    neu = Path(ses).with_name(Path(ses).stem + "_bereinigt.ses")
    neu.write_text("".join(teile), encoding="utf-8")
    return neu




def stummel_kuerzen(board):
    """Leiterbahnenden ohne Anschluss kürzen: endet ein Segment frei, wird es bis zur letzten
    Abzweigung (T-Stoß einer anderen Bahn bzw. Via) gekürzt. (Kein Entfernen von Objekten –
    das bringt die Python-Anbindung von KiCad zum Absturz.)"""
    import pcbnew

    def mm_(v):
        return (pcbnew.ToMM(v.x), pcbnew.ToMM(v.y))

    def auf_segment(p, a, b, tol):
        ax, ay = a
        bx, by = b
        px, py = p
        dx, dy = bx - ax, by - ay
        ll = dx * dx + dy * dy
        t = 0.0 if ll == 0 else max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / ll))
        qx, qy = ax + t * dx, ay + t * dy
        return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5 <= tol, t
    gesamt = 0
    for _ in range(80):
        spuren = [t for t in board.GetTracks() if t.GetClass() == "PCB_TRACK"]
        vias = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA"]
        pads = [p for fp in board.GetFootprints() for p in fp.Pads()]
        geaendert = 0
        for t in spuren:
            a, b = mm_(t.GetStart()), mm_(t.GetEnd())
            for ende, anderes in ((b, a), (a, b)):
                pt = pcbnew.VECTOR2I(pcbnew.FromMM(ende[0]), pcbnew.FromMM(ende[1]))
                verbunden = False
                for o in spuren:
                    if o is t or o.GetLayer() != t.GetLayer() or o.GetNetCode() != t.GetNetCode():
                        continue
                    ok, _ = auf_segment(ende, mm_(o.GetStart()), mm_(o.GetEnd()), pcbnew.ToMM(o.GetWidth()) / 2)
                    if ok:
                        verbunden = True
                        break
                if not verbunden:
                    for v in vias:
                        if v.GetNetCode() == t.GetNetCode():
                            vx, vy = mm_(v.GetPosition())
                            if ((vx - ende[0]) ** 2 + (vy - ende[1]) ** 2) ** 0.5 <= 0.3:
                                verbunden = True
                                break
                if not verbunden:
                    for p in pads:
                        if p.GetNetCode() == t.GetNetCode() and p.IsOnLayer(t.GetLayer()) and p.HitTest(pt):
                            verbunden = True
                            break
                if verbunden:
                    continue
                # freies Ende: nächstgelegene Abzweigung auf diesem Segment suchen
                bestes = None
                for o in spuren + vias:
                    if o is t or o.GetNetCode() != t.GetNetCode():
                        continue
                    if o.GetClass() == "PCB_TRACK" and o.GetLayer() != t.GetLayer():
                        continue
                    kand = [mm_(o.GetPosition())] if o.GetClass() == "PCB_VIA" else [mm_(o.GetStart()), mm_(o.GetEnd())]
                    for k in kand:
                        ok, tt = auf_segment(k, anderes, ende, 0.01)
                        if ok and tt < 0.999:
                            if bestes is None or tt > bestes[0]:
                                bestes = (tt, k)
                if bestes is None or bestes[0] < 0.001:
                    continue                      # nichts zu kürzen (DRC meldet es dann)
                else:
                    neu = pcbnew.VECTOR2I(pcbnew.FromMM(bestes[1][0]), pcbnew.FromMM(bestes[1][1]))
                    if ende == b:
                        t.SetEnd(neu)
                    else:
                        t.SetStart(neu)
                geaendert += 1
                break
            if geaendert:
                break
        if not geaendert:
            break
        gesamt += geaendert
    return gesamt




# =============================================================================
#  Platine aufbauen
# =============================================================================
def _V(x, y):
    import pcbnew
    return pcbnew.VECTOR2I(pcbnew.FromMM(URSPRUNG[0] + x), pcbnew.FromMM(URSPRUNG[1] + y))


def _M(v):
    import pcbnew
    return (round(pcbnew.ToMM(v.x) - URSPRUNG[0], 4), round(pcbnew.ToMM(v.y) - URSPRUNG[1], 4))


def _kreis(cx, cy, d, n=28):
    return [(cx + d / 2 * math.cos(2 * math.pi * k / n), cy + d / 2 * math.sin(2 * math.pi * k / n)) for k in range(n)]


def regelflaeche(board, name, punkte, lagen, bahnen=True, vias=True, flaeche=True, bauteile=False):
    import pcbnew
    z = pcbnew.ZONE(board)
    z.SetIsRuleArea(True)
    z.SetZoneName(name)
    ls = pcbnew.LSET()
    for ly in lagen:
        ls.AddLayer(ly)
    z.SetLayerSet(ls)
    z.SetDoNotAllowTracks(bahnen)
    z.SetDoNotAllowVias(vias)
    z.SetDoNotAllowZoneFills(flaeche)
    z.SetDoNotAllowPads(False)
    z.SetDoNotAllowFootprints(bauteile)
    ol = z.Outline()
    ol.NewOutline()
    for px, py in punkte:
        v = _V(px, py)
        ol.Append(v.x, v.y)
    board.Add(z)
    return z


SIEBDRUCK_OBEN = []          # (Text, x, y, Größe, Winkel, Ausrichtung)
SIEBDRUCK_UNTEN = []


def siebdruck_texte(pp):
    """Siebdruck (oben/unten). Warnhinweis ALPHA groß unter dem CM5 (bei abgenommenem Modul lesbar) und kurz unten."""
    o = [("K2-CM5-Träger Alpha 1", 36.0, 32.6, 1.6, 0, None),
         ("ALPHA – NUR FÜR TESTZWECKE", 36.0, 35.6, 1.2, 0, None),
         ("NICHT FÜR KOMMERZIELLEN EINSATZ", 36.0, 38.0, 1.2, 0, None),
         ("NUTZUNG AUF EIGENE GEFAHR", 36.0, 40.4, 1.2, 0, None),
         (f"CM5 · Kühlkörper ≤ 4,9 mm · {DATUM_DE}", 36.0, 24.5, 1.0, 0, None),
         ("D10 = Kabel OK", -4.0, 49.4, 0.8, 0, None),
         ("1", -2.2, 45.1, 0.8, 0, None), ("50", -5.2, 16.9, 0.8, 0, None),
         ("+24V", pp("J7", "2")[0] + 3.7, pp("J7", "2")[1] + 3.0, 0.8, 0, None),   # hinter dem Steckergehäuse sichtbar
         ("GND", pp("J7", "1")[0], pp("J7", "1")[1] - 4.3, 0.8, 0, None),         # über dem „−“ des Footprints
         ("RPIBOOT", 25.6, 8.2, 0.8, 0, None), ("J10 USB-C Notfall", 16.4, 8.1, 0.8, 0, None),
         ("CAN 120R", 69.9, 52.62, 0.8, 90, None), ("485 120R", 25.3, 52.6, 0.8, 90, None),
         ("J12 · DSI (Display)", 68.8, 30.65, 1.0, 90, None),
         ("3V3", 28.1, 50.3, 0.8, 0, None), ("ACT", 32.1, 50.3, 0.8, 0, None), ("LNK", 36.1, 50.3, 0.8, 0, None),
         ("SPD", 40.1, 50.3, 0.8, 0, None),
         # Messpunkt-Beschriftung in zwei versetzten Reihen (Raster 2,9 mm ist für nebeneinander zu eng)
         ("5V", 44.0, 52.6, 0.8, 0, None), ("3V3", 46.9, 50.9, 0.8, 0, None), ("RUN", 49.8, 52.6, 0.8, 0, None),
         ("BOOT", 52.7, 50.9, 0.8, 0, None), ("PWR", 55.6, 52.6, 0.8, 0, None), ("GND", 58.5, 50.9, 0.8, 0, None)]
    # Pin-Beschriftung J9b (CAN, seit Weg A der einzige CAN-Anschluss), J11 (RS485)
    # JST GH (Raster 1,25 mm): Pinnamen als EIN Text, Reihenfolge wie die Pins von links nach rechts (einzelne
    # Buchstaben wären zu eng); stehende Variante: Pin 1 liegt gegenüber der liegenden räumlich auf der anderen Seite
    def gh_text(ref, namen):
        return " ".join(namen[n] for n in sorted(namen, key=lambda n: pp(ref, n)[0]))
    o.append((gh_text("J9b", {"1": "H", "2": "L", "3": "G"}), pp("J9b", "2")[0], pp("J9b", "2")[1] + 1.8, 0.8, 0, None))
    o.append(("CAN", pp("J9b", "2")[0], pp("J9b", "2")[1] + 3.5, 0.8, 0, None))
    o.append((gh_text("J11", {"1": "A", "2": "B", "3": "G"}), pp("J11", "2")[0], pp("J11", "2")[1] - 1.75, 0.8, 0, None))
    o.append(("J11 RS485", pp("J11", "2")[0] - 6.6, pp("J11", "2")[1], 0.8, 90, None))
    # Unterseite: zwei freie Felder (zwischen D3 und der UART-Gruppe, links oben neben H1)
    u = [("K2-CM5-Träger Alpha 1 · Unterseite", 34.0, 11.2, 0.8, 0, None),
         ("ALPHA · NUR TEST", 34.0, 12.9, 0.8, 0, None),
         ("NICHT KOMMERZIELL · EIGENE GEFAHR", 34.0, 14.6, 0.8, 0, None),
         ("JP2/JP3 zu = RS485-Bias", 34.0, 16.3, 0.8, 0, None),
         ("JP5 zu = 5 V über USB-C", 34.0, 18.0, 0.8, 0, None),
         # seit Weg A (24.10.2026) rechts im frei gewordenen Feld der Toolhead-eFuse (links sitzen jetzt R23/U4)
         ("JP6/JP7 zu =", 72.0, 41.0, 0.8, 0, None),
         ("CC1/CC2 an CM5", 72.0, 42.7, 0.8, 0, None),
         ("R43/44 nur", 72.0, 44.4, 0.8, 0, None),
         ("bei JP offen", 72.0, 46.1, 0.8, 0, None),
         ("Teile ≤ 2,0 mm", 72.0, 47.8, 0.8, 0, None),
         ("F1: 3 A 2410", 4.6, 22.6, 0.8, 0, None)]
    # Messpads der Unterseite beschriften (N1)
    # Lagen per Freiraum-Suche mit echten Textmaßen (≥ 0,25 mm zu Lötstopp/Bohrung, ≥ 0,5 mm zur Kante)
    for ref, name, dx, dy, ang in (("TP2", "GND", -2.6, 0.0, 0), ("TP11", "BR5V", -2.9, 0.0, 0),
                                   ("TP10", "24V", -0.8, 2.0, 0), ("TP12", "PWR_FAIL", 2.4, 1.6, 90)):
        x_, y_ = pp(ref, "1")
        u.append((name, x_ + dx, y_ + dy, 0.8, ang, None))
    return o, u


def platine(netzliste):
    import pcbnew
    from pcbnew import FromMM as mm
    pfad = PROJ / f"{PROJEKT}.kicad_pcb"
    board, fps, netinfo, nets = board_mit_bauteilen(netzliste, pfad)
    fehler = pruefe_platzierung(board, fps)
    if fehler:
        raise RuntimeError("Platzierung: " + "; ".join(fehler))
    tb = board.GetTitleBlock()
    tb.SetTitle(TITEL)
    tb.SetDate(DATUM)
    tb.SetRevision(STAND)
    tb.SetComment(0, WARNUNG_KURZ)
    tb.SetComment(1, "4 Lagen JLC04161H-7628: L1 Signal, L2 GND, L3 5,2 V/3,3 V + GND, L4 Signal; "
                     "Impedanz USB 90 Ω / Ethernet + DSI 100 Ω")

    def pp(ref, num):
        p_ = [p for p in fps[ref].Pads() if p.GetNumber() == num][0]
        return _M(p_.GetPosition())

    S, BS = pcbnew.F_SilkS, pcbnew.B_SilkS

    def text(t, x, y, size=1.0, ang=0, just=None, layer=S):
        tx = pcbnew.PCB_TEXT(board)
        tx.SetText(t)
        tx.SetLayer(layer)
        tx.SetTextSize(pcbnew.VECTOR2I(mm(size), mm(size)))
        tx.SetTextThickness(mm(max(0.12, size * 0.15)))
        tx.SetPosition(_V(x, y))
        tx.SetTextAngleDegrees(ang)
        if layer == BS:
            tx.SetMirrored(True)
        tx.SetHorizJustify({"left": pcbnew.GR_TEXT_H_ALIGN_LEFT, "right": pcbnew.GR_TEXT_H_ALIGN_RIGHT}
                           .get(just, pcbnew.GR_TEXT_H_ALIGN_CENTER))
        board.Add(tx)

    def linie(a, b, layer, w=0.1):
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_SEGMENT)
        s.SetStart(_V(*a))
        s.SetEnd(_V(*b))
        s.SetLayer(layer)
        s.SetWidth(mm(w))
        board.Add(s)

    def bogen(c, r, a0, layer, w=0.1):
        s = pcbnew.PCB_SHAPE(board)
        s.SetShape(pcbnew.SHAPE_T_ARC)
        pts = [_V(c[0] + r * math.cos(math.radians(a)), c[1] + r * math.sin(math.radians(a))) for a in (a0, a0 + 45, a0 + 90)]
        s.SetArcGeometry(pts[0], pts[1], pts[2])
        s.SetLayer(layer)
        s.SetWidth(mm(w))
        board.Add(s)

    # ---------- Felder: Referenzen klein/aus, Werte auf Fab ----------
    for ref, fp in fps.items():
        fp.Value().SetLayer(pcbnew.B_Fab if fp.IsFlipped() else pcbnew.F_Fab)
        fp.Reference().SetVisible(ref in ("J1", "J7", "J9b", "JP_BOOT", "T1", "D10", "F1"))
        fp.Reference().SetTextSize(pcbnew.VECTOR2I(mm(0.8), mm(0.8)))
        fp.Reference().SetTextThickness(mm(0.12))
    ref_lage = {"J1": (-3.0, 51.6, 0), "J7": (-8.4, 16.6, 0), "J9b": (64.6, 3.6, 90),
                "J10": (12.4, 1.3, 0), "J11": (10.0, 49.8, 0), "JP_BOOT": (29.4, 4.2, 90),
                "JP_485_TERM": (25.2, 52.6, 90), "T1": (3.4, 31.2, 0), "D10": (-2.2, 16.9, 0), "F1": (2.0, 15.9, 0)}
    for ref, (x, y, a) in ref_lage.items():
        f_ = fps[ref].Reference()
        f_.SetPosition(_V(x, y))
        f_.SetTextAngleDegrees(a)
    # ---------- Umriss ----------
    E = pcbnew.Edge_Cuts
    x0, x1, H, R = X_LINKS, X_RECHTS, PLATINE_HOEHE, ECKENRADIUS
    linie((x0 + R, 0), (x1 - R, 0), E)
    linie((x1, R), (x1, H - R), E)
    linie((x1 - R, H), (x0 + R, H), E)
    linie((x0, H - R), (x0, R), E)
    for cx, cy, a0 in ((x0 + R, R, 180), (x1 - R, R, 270), (x1 - R, H - R, 0), (x0 + R, H - R, 90)):
        bogen((cx, cy), R, a0, E)
    # ---------- Sperrflächen: Schraubenköpfe (alle Kupferlagen), CM5-Abstandshalter/Schrauben ----------
    alle = (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu)
    for i, (hx, hy) in enumerate(T113_LOECHER):
        regelflaeche(board, f"Frei_L{i + 1}", _kreis(hx, hy, T113_FREI), alle)
    ux0, uy0, ux1, uy1 = cm5_umriss()
    for i, (hx, hy) in enumerate(((ux0 + 3.5, uy0 + 3.5), (ux1 - 3.5, uy0 + 3.5), (ux0 + 3.5, uy1 - 3.5), (ux1 - 3.5, uy1 - 3.5))):
        regelflaeche(board, f"CM5_Halter_{i + 1}_oben", _kreis(hx, hy, CM5_ABSTANDSHALTER), (pcbnew.F_Cu,))
        regelflaeche(board, f"CM5_Schraube_{i + 1}_unten", _kreis(hx, hy, CM5_SCHRAUBE_UNTEN), (pcbnew.B_Cu,))
    # ---------- feste Leitungen ----------
    K = Kupfer()
    feste_leitungen(K, pp)
    pads = pad_hindernisse(board)
    vin24_wege(K, pads, pp)
    j12_gnd(K, pads, pp)
    fehlend = fanout_cm5(K, pads)
    if fehlend:
        raise RuntimeError(f"CM5-Fanout: kein Platz für Pins {fehlend}")
    cm5_5v_westen(K, pads, pp)
    feste_kurzwege(K, pads, pp)
    offen = flaechen_fanout(K, pads, fps)
    if offen:
        print(f"Flächen-Fanout: kein Platz an {', '.join(offen)} (Anbindung später über Flächen/Nachbinden)")
    K.setzen(board, netinfo)
    (PROJ / "zwischen" / "paare.json").write_text(json.dumps(paar_laengen(K), indent=1) + "\n", encoding="utf-8")
    # ---------- Siebdruck ----------
    o, u = siebdruck_texte(pp)
    for t_, x, y, s, a, j in o:
        text(t_, x, y, s, a, j, S)
    for t_, x, y, s, a, j in u:
        text(t_, x, y, s, a, j, BS)
    board.BuildConnectivity()
    n_teil = fest_aufteilen(board)
    pcbnew.SaveBoard(str(pfad), board)
    print(f"{n_teil} feste Leiterbahnen an Knoten geteilt; {len(K.seg)} feste Segmente, {len(K.via)} feste Vias.")
    dsn = PROJ / "zwischen" / f"{PROJEKT}.dsn"
    if not pcbnew.ExportSpecctraDSN(board, str(dsn)):
        raise RuntimeError("DSN-Export fehlgeschlagen")
    dsn_anpassen(dsn)
    print("Platine platziert, feste Leitungen gesetzt, DSN exportiert.")


def paar_laengen(K):
    """Länge je Netz der festen Paar-Segmente (+ Via-Längen), für den Paar-Wächter."""
    aus = {}
    for name, (a, b) in PAARE.items():
        z = {}
        for n in (a, b):
            seg = [s for s in K.seg if s["netz"] == n and s["gruppe"] == name]
            vias = [v for v in K.via if v["netz"] == n and v["gruppe"] == name]
            z[n] = {"bahn": round(sum(_pp(s["a"], s["b"]) for s in seg), 4), "vias": len(vias),
                    "gesamt": round(sum(_pp(s["a"], s["b"]) for s in seg) + len(vias) * VIA_LAENGE, 4)}
        aus[name] = z
    return aus


def dsn_anpassen(dsn):
    """GND nicht an Freerouting (Flächen + Vias), Innenlagen als Versorgungslagen, Randstreifen/Passermarken/
    J1-Gehäuse als Sperrflächen."""
    import re
    text = Path(dsn).read_text(encoding="utf-8")
    for netz in FLAECHENNETZE:                 # GND (In1) und CM5_3V3 (In2): Flächen + eigene Anschluss-Vias
        i = text.find(f"(net {netz}\n")
        if i < 0:
            raise RuntimeError(f"Netz {netz} nicht im DSN")
        tiefe = 0
        for j in range(i, len(text)):
            if text[j] == "(":
                tiefe += 1
            elif text[j] == ")":
                tiefe -= 1
                if tiefe == 0:
                    break
        text = text[:i] + f"(net {netz}\n    )" + text[j + 1:]
    text = re.sub(r"\(layer In([12])\.Cu\s*\(type signal\)", r"(layer In\1.Cu\n      (type power)", text)
    ox, oy = URSPRUNG

    def r(x0, y0, x1, y1, lage="signal"):
        return (f'    (keepout "" (rect {lage} {(ox + x0) * 1000:.1f} {-(oy + y1) * 1000:.1f} '
                f'{(ox + x1) * 1000:.1f} {-(oy + y0) * 1000:.1f}))\n')
    W0, W1, H, b = X_LINKS, X_RECHTS, PLATINE_HOEHE, 0.35
    band = r(W0, 0, W1, b) + r(W0, H - b, W1, H) + r(W0, 0, W0 + b, H) + r(W1 - b, 0, W1, H)
    for (fx, fy) in PASSERMARKEN:
        band += r(fx - 1.7, fy - 1.7, fx + 1.7, fy + 1.7, "F.Cu")
    for (fx, fy) in PASSERMARKEN_UNTEN:
        band += r(fx - 1.7, fy - 1.7, fx + 1.7, fy + 1.7, "B.Cu")
    k = text.find("    (via ")
    text = text[:k] + band + text[k:]
    # Innenlagen als GND-Ebenen deklarieren -> Freerouting verlegt dort keine Signale
    ebene = " ".join(f"{(ox + x) * 1000:.1f} {-(oy + y) * 1000:.1f}" for x, y in ((W0, 0), (W1, 0), (W1, H), (W0, H)))
    k = text.find("    (boundary")
    text = text[:k] + f"    (plane GND (polygon In1.Cu 0  {ebene}))\n" + text[k:]
    # In2 (L3) = 3,3-V-Fläche mit den festen Versorgungsbahnen (Wandler-Stamm 5,2 V, CM5-5V-Westanbindung, J10-VBUS);
    # Freerouting verlegt nur auf L1/L4, damit die 3,3-V-Fläche nicht zerschnitten wird
    text = text.replace("(layer In2.Cu\n      (type power)", "(layer In2.Cu\n      (type signal)")
    # Routing-Via bleibt 0,6 mm (mit 0,8 mm hält Freerouting zwar Loch-Loch selbst ein, verlegt aber deutlich
    # schlechter); Loch-Loch-Konflikte löst ses_vias_bereinigen (geprüftes Wegschieben entlang der Verbindungslinie)
    if ROUTING_VIA_MM != 0.6:
        i0 = text.find('(padstack "Via[0-3]_600:300_um"')
        i1 = text.find("(attach off)", i0)
        text = text[:i0] + re.sub(r"\(circle (\S+) 600\)", rf"(circle \1 {round(ROUTING_VIA_MM * 1000)})",
                                  text[i0:i1]) + text[i1:]

    def klasse(m):
        return m.group(0).replace("(circuit\n", "(circuit\n        (use_layer F.Cu B.Cu)\n", 1)
    text = re.sub(r"\(class (\S+) .*?\(circuit\n", klasse, text, flags=re.S)
    Path(dsn).write_text(text, encoding="utf-8")


# =============================================================================
#  Fertigstellen: SES einlesen, GND-Flächen (4 Lagen), Leistungsflächen, Stitching, Nachbindung
# =============================================================================
KUPFERLAGEN = None


def _lagen():
    import pcbnew
    return (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu)


def gnd_haupt_netz(board):
    import pcbnew
    return netz_haupt(board, "GND", pcbnew.In1_Cu)


def netz_haupt(board, netz, hauptlage):
    """Zusammenhang eines Flächennetzes über alle 4 Lagen: Flächenstücke, Vias, Pads, Bahnen (Union-Find).
    Hauptkomponente = größtes Flächenstück der Hauptlage (GND: In1, CM5_3V3: In2)."""
    import pcbnew
    zonen = {z.GetLayer(): z for z in board.Zones() if not z.GetIsRuleArea() and z.GetNetname() == netz}
    stuecke = {}
    for ly, z in zonen.items():
        poly = z.GetFilledPolysList(ly)
        for i in range(poly.OutlineCount()):
            sp = pcbnew.SHAPE_POLY_SET()
            sp.AddOutline(poly.Outline(i))
            for j in range(poly.HoleCount(i)):
                sp.AddHole(poly.Hole(i, j))
            stuecke[(ly, i)] = sp
    eltern = {}

    def wurzel(k):
        while eltern.setdefault(k, k) != k:
            eltern[k] = eltern[eltern[k]]
            k = eltern[k]
        return k

    def verbinde(a, b):
        eltern[wurzel(a)] = wurzel(b)
    for k in stuecke:
        wurzel(("S",) + k)
    vias = [t for t in board.GetTracks() if t.GetClass() == "PCB_VIA" and t.GetNetname() == netz]
    for v in vias:
        kv = ("V", v.m_Uuid.AsString())
        wurzel(kv)
        for (ly, i), sp in stuecke.items():
            if sp.Contains(v.GetPosition()):
                verbinde(kv, ("S", ly, i))
    padpoly = {}
    for fp in board.GetFootprints():
        for p_ in fp.Pads():
            if p_.GetNetname() != netz:
                continue
            kp = ("P", fp.GetReference(), p_.GetNumber(), p_.m_Uuid.AsString())
            wurzel(kp)
            for ly in _lagen():
                if not p_.IsOnLayer(ly):
                    continue
                pp_ = p_.GetEffectivePolygon(ly)
                padpoly[(kp, ly)] = pp_
                for (sl, i), sp in stuecke.items():
                    if sl != ly:
                        continue
                    sch = pcbnew.SHAPE_POLY_SET(sp)
                    sch.BooleanIntersection(pp_)
                    if sch.OutlineCount():
                        verbinde(kp, ("S", sl, i))
    bahnen = [t for t in board.GetTracks() if t.GetClass() == "PCB_TRACK" and t.GetNetname() == netz]
    for a in bahnen:                                   # Bahnen, die sich berühren (Ende auf anderer Bahn)
        for b in bahnen:
            if a is not b and a.GetLayer() == b.GetLayer() and \
                    (b.HitTest(a.GetStart(), 1000) or b.HitTest(a.GetEnd(), 1000)):
                verbinde(("T", a.m_Uuid.AsString()), ("T", b.m_Uuid.AsString()))
    for t in bahnen:
        kt = ("T", t.m_Uuid.AsString())
        wurzel(kt)
        for ende in (t.GetStart(), t.GetEnd()):
            for v in vias:
                if abs(v.GetPosition().x - ende.x) < 10000 and abs(v.GetPosition().y - ende.y) < 10000:
                    verbinde(kt, ("V", v.m_Uuid.AsString()))
            for (kp, ly), pp_ in padpoly.items():
                if t.IsOnLayer(ly) and pp_.Contains(ende):
                    verbinde(kt, kp)
            for (sl, i), sp in stuecke.items():
                if t.IsOnLayer(sl) and sp.Contains(ende):
                    verbinde(kt, ("S", sl, i))
    in1 = [k for k in stuecke if k[0] == hauptlage] or list(stuecke)
    groesstes = max(in1, key=lambda k: stuecke[k].Area())
    return wurzel(("S",) + groesstes), wurzel, stuecke


def _bohrungen(board):
    import pcbnew
    b = [(t.GetPosition(), pcbnew.ToMM(t.GetDrillValue()) / 2) for t in board.GetTracks() if t.GetClass() == "PCB_VIA"]
    b += [(p_.GetPosition(), pcbnew.ToMM(max(p_.GetDrillSize().x, p_.GetDrillSize().y)) / 2)
          for fp in board.GetFootprints() for p_ in fp.Pads() if p_.GetDrillSize().x > 0]
    return b


def gnd_pads_nachbinden(board):
    import pcbnew
    return pads_nachbinden(board, "GND", pcbnew.In1_Cu)


def pads_nachbinden(board, netz, hauptlage):
    """Pads eines Flächennetzes ohne Verbindung zur Hauptfläche: kurzes Stück + Via in die Hauptfläche der Innenlage
    (GND: In1, CM5_3V3: In2). Feinraster-Pads (< 0,5 mm) mit 0,2-mm-Stich und 0,15 mm Abstand."""
    import pcbnew
    from pcbnew import FromMM as mm
    haupt, wurzel, stuecke = netz_haupt(board, netz, hauptlage)
    ziel = pcbnew.SHAPE_POLY_SET()
    for (sl, i), sp in stuecke.items():
        if sl == hauptlage and wurzel(("S", sl, i)) == haupt:
            ziel.BooleanAdd(sp)
    ziel.Deflate(mm(0.35), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, mm(0.005))
    sperren = [z.Outline() for z in board.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    gnd = board.FindNet(netz)
    bohr = _bohrungen(board)
    fremd = {ly: [] for ly in (pcbnew.F_Cu, pcbnew.B_Cu)}
    innen = []
    for t in board.GetTracks():
        if t.GetNetname() != netz:
            for ly in fremd:
                if t.IsOnLayer(ly):
                    fremd[ly].append(t)
            if t.GetClass() == "PCB_TRACK" and t.GetLayer() in (pcbnew.In1_Cu, pcbnew.In2_Cu):
                innen.append(t)
    for fp in board.GetFootprints():
        for p_ in fp.Pads():
            if p_.GetNetname() != netz:
                for ly in fremd:
                    if p_.IsOnLayer(ly):
                        fremd[ly].append(p_)
    n = 0
    for fp in board.GetFootprints():
        for pad in fp.Pads():
            if pad.GetNetname() != netz or pad.GetDrillSize().x > 0:
                continue
            if wurzel(("P", fp.GetReference(), pad.GetNumber(), pad.m_Uuid.AsString())) == haupt:
                continue
            ly = pcbnew.B_Cu if pad.IsOnLayer(pcbnew.B_Cu) else pcbnew.F_Cu
            c = pad.GetPosition()
            fein = min(pad.GetSize(ly).x, pad.GetSize(ly).y) < mm(0.5)
            bw, ba = (0.2, 0.15 + 0.1 + 0.02) if fein else (0.3, 0.2 + 0.2)   # Stichbreite, Prüfabstand Stich
            gefunden = None
            # zuletzt (24.10.2026): Via versetzt im Pad (ganz innerhalb), falls Zentrum und Ringe blockiert sind
            hx = pcbnew.ToMM(pad.GetBoundingBox().GetWidth()) / 2 - 0.3
            hy = pcbnew.ToMM(pad.GetBoundingBox().GetHeight()) / 2 - 0.3
            im_pad = [(dx, dy) for dx in (0.0, 0.1, -0.1, 0.2, -0.2, 0.3, -0.3) for dy in (0.0, 0.15, -0.15, 0.3, -0.3, 0.42, -0.42)
                      if (dx or dy) and abs(dx) <= hx + 1e-6 and abs(dy) <= hy + 1e-6]
            for rad in (0.0, 0.7, 0.9, 1.2, 1.5, 1.8, 2.2, 2.7, 3.2, 3.8, "pad"):
                for k in range(len(im_pad) if rad == "pad" else (1 if rad == 0 else 24)):
                    if rad == "pad":
                        v_ = pcbnew.VECTOR2I(c.x + mm(im_pad[k][0]), c.y + mm(im_pad[k][1]))
                    else:
                        w = 2 * math.pi * k / 24
                        v_ = pcbnew.VECTOR2I(c.x + mm(rad * math.cos(w)), c.y + mm(rad * math.sin(w)))
                    if rad in (0, "pad") and (pad.GetSize(ly).x < mm(0.95) or pad.GetSize(ly).y < mm(0.95)):
                        continue                         # Via im Pad nur bei großen Pads (Wärmepads, 0805/1206)
                    if not ziel.Contains(v_) or any(sp.Contains(v_) for sp in sperren):
                        continue
                    if any(math.hypot(pcbnew.ToMM(v_.x - q.x), pcbnew.ToMM(v_.y - q.y)) < 0.5 + r + 0.15 for q, r in bohr):
                        continue
                    pkt = [v_] + [pcbnew.VECTOR2I(int(c.x + (v_.x - c.x) * i / 12), int(c.y + (v_.y - c.y) * i / 12))
                                  for i in range(1, 12)]
                    abst = [mm(0.3 + 0.2)] + [mm(ba)] * 11
                    if any(it.HitTest(q_, a_ + (mm(0.2) if it.GetNetname() == "BR_5V" else 0))
                           for q_, a_ in zip(pkt, abst) for it in fremd[ly]):
                        continue
                    if any(it.HitTest(v_, mm(0.7) if it.GetNetname() == "BR_5V" else mm(0.5))
                           for it in fremd[pcbnew.F_Cu if ly == pcbnew.B_Cu else pcbnew.B_Cu] + innen):
                        continue
                    gefunden = v_
                    break
                if gefunden is not None:
                    break
            if gefunden is None:
                # Rückfall: Bahn auf der Padlage zum nächsten angebundenen Via/Pad desselben Netzes (gerade oder mit
                # einem Knick), frei geprüft gegen fremde Bahnen/Pads dieser Lage
                ziele = []
                for v2 in board.GetTracks():
                    if v2.GetClass() == "PCB_VIA" and v2.GetNetname() == netz and \
                            wurzel(("V", v2.m_Uuid.AsString())) == haupt:
                        ziele.append(v2.GetPosition())
                for fp2 in board.GetFootprints():
                    for p2 in fp2.Pads():
                        if p2.GetNetname() == netz and p2.IsOnLayer(ly) and p2 is not pad and \
                                wurzel(("P", fp2.GetReference(), p2.GetNumber(), p2.m_Uuid.AsString())) == haupt:
                            ziele.append(p2.GetPosition())
                ziele = sorted((z_ for z_ in ziele if math.hypot(pcbnew.ToMM(z_.x - c.x), pcbnew.ToMM(z_.y - c.y)) < 6.0),
                               key=lambda z_: math.hypot(z_.x - c.x, z_.y - c.y))

                def weg_frei(pkte):
                    for a_, b_ in zip(pkte, pkte[1:]):
                        n_ = max(2, int(math.hypot(b_.x - a_.x, b_.y - a_.y) / mm(0.1)))
                        for i_ in range(n_ + 1):
                            q_ = pcbnew.VECTOR2I(int(a_.x + (b_.x - a_.x) * i_ / n_), int(a_.y + (b_.y - a_.y) * i_ / n_))
                            if any(it.HitTest(q_, mm(0.125 + 0.15 + 0.02) + (mm(0.2) if it.GetNetname() == "BR_5V" else 0))
                                   for it in fremd[ly]):
                                return False
                    return True
                weg = None
                for z_ in ziele:
                    for pkte in ([c, z_], [c, pcbnew.VECTOR2I(c.x, z_.y), z_], [c, pcbnew.VECTOR2I(z_.x, c.y), z_]):
                        if weg_frei(pkte):
                            weg = pkte
                            break
                    if weg:
                        break
                if weg is None:
                    print(f"  {netz}-Pad {fp.GetReference()}.{pad.GetNumber()}: kein Platz für ein Anschluss-Via")
                    continue
                for a_, b_ in zip(weg, weg[1:]):
                    if a_ != b_:
                        t = pcbnew.PCB_TRACK(board)
                        t.SetStart(a_)
                        t.SetEnd(b_)
                        t.SetWidth(mm(0.25))
                        t.SetLayer(ly)
                        t.SetNet(gnd)
                        board.Add(t)
                print(f"  {netz}-Pad {fp.GetReference()}.{pad.GetNumber()}: über Bahn an das Netz angebunden")
                n += 1
                continue
            if gefunden != c:
                t = pcbnew.PCB_TRACK(board)
                t.SetStart(c)
                t.SetEnd(gefunden)
                t.SetWidth(mm(bw))
                t.SetLayer(ly)
                t.SetNet(gnd)
                board.Add(t)
            v = pcbnew.PCB_VIA(board)
            v.SetPosition(gefunden)
            v.SetWidth(mm(0.6))
            v.SetDrill(mm(0.3))
            v.SetNet(gnd)
            board.Add(v)
            bohr.append((gefunden, 0.15))
            n += 1
    return n


def gnd_fragmente_verbinden(board):
    """Abgetrennte GND-Flächenstücke (oben/unten) per Via mit den GND-Innenlagen verbinden."""
    import pcbnew
    from pcbnew import FromMM as mm
    haupt, wurzel, stuecke = gnd_haupt_netz(board)
    innen = pcbnew.SHAPE_POLY_SET()
    for (sl, i), sp in stuecke.items():
        if sl == pcbnew.In1_Cu and wurzel(("S", sl, i)) == haupt:
            innen.BooleanAdd(sp)
    bohr = _bohrungen(board)
    sperren = [z.Outline() for z in board.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    fremd_bahnen = [t for t in board.GetTracks() if t.GetNetname() != "GND"]
    fremd_pads = [p_ for fp in board.GetFootprints() for p_ in fp.Pads() if p_.GetNetname() != "GND"]
    gnd = board.FindNet("GND")
    erledigt, n = set(), 0
    for (ly, i), sp in sorted(stuecke.items(), key=lambda kv: -kv[1].Area()):
        if ly not in (pcbnew.F_Cu, pcbnew.B_Cu):
            continue
        w_ = wurzel(("S", ly, i))
        if w_ == haupt or w_ in erledigt:
            continue
        sch = pcbnew.SHAPE_POLY_SET(sp)
        sch.BooleanIntersection(innen)
        sch.Deflate(mm(0.4), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, mm(0.005))
        if sch.OutlineCount() == 0:
            continue
        bb = sch.BBox()
        kand = [sch.Outline(k).Centre() for k in range(sch.OutlineCount())]
        for gx in range(bb.GetX(), bb.GetRight() + 1, mm(0.25)):
            for gy in range(bb.GetY(), bb.GetBottom() + 1, mm(0.25)):
                kand.append(pcbnew.VECTOR2I(gx, gy))
        punkt = None
        for p_ in kand:
            if not sch.Contains(p_) or any(z.Contains(p_) for z in sperren):
                continue
            if any(math.hypot(pcbnew.ToMM(p_.x - q.x), pcbnew.ToMM(p_.y - q.y)) < 0.5 + r + 0.15 for q, r in bohr):
                continue
            if any(it.HitTest(p_, mm(0.5)) for it in fremd_bahnen) or any(it.HitTest(p_, mm(0.5)) for it in fremd_pads):
                continue
            punkt = p_
            break
        if punkt is None:
            continue
        v = pcbnew.PCB_VIA(board)
        v.SetPosition(punkt)
        v.SetWidth(mm(0.6))
        v.SetDrill(mm(0.3))
        v.SetNet(gnd)
        board.Add(v)
        bohr.append((punkt, 0.15))
        erledigt.add(w_)
        n += 1
    return n


FLAECHENNETZE = ("GND", "CM5_3V3")
ROUTING_VIA_MM = 0.6           # Via-Durchmesser, den Freerouting sieht (nach dem Import immer 0,6/0,3 mm)
VIAS_SCHIEBEN = False          # Loch-Loch-Konflikte nicht durch Verschieben, sondern mit 0,2-mm-Bohrung lösen
GND_FANOUT_PASSIV = {"R30", "R31", "C8", "C6", "C26", "R43", "R44", "R16"}   # Passive mit GND-Inseln in früheren Läufen
LEISTUNGSNETZE = ("VIN24_ROH", "VIN24_F", "VIN24", "WANDLER_5V", "MUX_OUT", "CM5_5V",
                  "WANDLER_SW")


def leistungsflaechen(board):
    """Für die Leistungsnetze zusätzliche Kupferflächen (oben/unten) entlang ihrer Pads und Bahnen
    (Rechtecke + 0,6 mm, vereinigt). Priorität über GND; gefüllt wird nur, wo Platz ist."""
    import pcbnew
    from pcbnew import FromMM as mm
    n = 0
    for prio, netz in enumerate(LEISTUNGSNETZE):
        ni = board.FindNet(netz)
        if ni is None:
            continue
        for ly in (pcbnew.F_Cu, pcbnew.B_Cu):
            poly = pcbnew.SHAPE_POLY_SET()
            for fp in board.GetFootprints():
                for p_ in fp.Pads():
                    if p_.GetNetname() == netz and p_.IsOnLayer(ly):
                        bb = p_.GetBoundingBox()
                        bb.Inflate(mm(0.8))
                        r = pcbnew.SHAPE_POLY_SET()
                        r.NewOutline()
                        for x_, y_ in ((bb.GetLeft(), bb.GetTop()), (bb.GetRight(), bb.GetTop()),
                                       (bb.GetRight(), bb.GetBottom()), (bb.GetLeft(), bb.GetBottom())):
                            r.Append(x_, y_)
                        poly.BooleanAdd(r)
            for t in board.GetTracks():
                if t.GetNetname() == netz and t.GetClass() == "PCB_TRACK" and t.GetLayer() == ly:
                    s_ = pcbnew.SHAPE_POLY_SET()
                    t.TransformShapeToPolygon(s_, ly, mm(1.0), mm(0.01), pcbnew.ERROR_INSIDE)
                    poly.BooleanAdd(s_)
            if poly.OutlineCount() == 0:
                continue
            poly.Simplify()
            z = pcbnew.ZONE(board)
            z.SetLayer(ly)
            z.SetNetCode(ni.GetNetCode())
            z.SetZoneName(f"{netz}_{'oben' if ly == pcbnew.F_Cu else 'unten'}")
            z.SetAssignedPriority(10 + prio)
            ol = z.Outline()
            for k in range(poly.OutlineCount()):
                ol.AddOutline(poly.Outline(k))
            z.SetLocalClearance(mm(0.2))
            z.SetMinThickness(mm(0.25))
            z.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
            z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
            board.Add(z)
            n += 1
    return n


def loch_abstand_bohrungen(board):
    """Vias verschiedener Netze mit Loch-Loch < 0,5 mm: Bohrung zuerst am gerouteten, dann am festen Via auf 0,2 mm
    verkleinern (Kupfer bleibt, Restring ≥ 0,15 mm) – keine Geometrieänderung, also keine neuen Abstandsfehler."""
    import pcbnew
    from pcbnew import FromMM as mm
    vias = [v for v in board.GetTracks() if v.GetClass() == "PCB_VIA"]
    n = 0

    def abstand(a, b):
        d = math.hypot(pcbnew.ToMM(a.GetPosition().x - b.GetPosition().x), pcbnew.ToMM(a.GetPosition().y - b.GetPosition().y))
        return d - pcbnew.ToMM(a.GetDrillValue()) / 2 - pcbnew.ToMM(b.GetDrillValue()) / 2
    for i, a in enumerate(vias):
        for b in vias[i + 1:]:
            if a.GetNetname() == b.GetNetname() or abstand(a, b) >= 0.5 - 1e-4:
                continue
            for v in sorted((a, b), key=lambda x: x.IsLocked()):
                if abstand(a, b) < 0.5 - 1e-4 and v.GetDrillValue() > mm(0.2):
                    v.SetDrill(mm(0.2))
                    n += 1
    return n


def fertigstellen(ses):
    import pcbnew
    from pcbnew import FromMM as mm
    pfad = PROJ / f"{PROJEKT}.kicad_pcb"
    board = pcbnew.LoadBoard(str(pfad))
    if ses and ses != "-":
        ses = ses_vias_bereinigen(board, ses)
        if not pcbnew.ImportSpecctraSES(board, str(ses)):
            raise RuntimeError("SES-Import fehlgeschlagen")
        for v in board.GetTracks():                    # Routing-Vias (falls im DSN größer) auf 0,6 mm
            if v.GetClass() == "PCB_VIA" and not v.IsLocked() and v.GetWidth(pcbnew.F_Cu) > mm(0.65):
                v.SetWidth(mm(0.6))
        n_bohr = loch_abstand_bohrungen(board)
        if n_bohr:
            print(f"{n_bohr} Via-Bohrungen auf 0,2 mm verkleinert (Loch-Loch ≥ 0,5 mm zu fremden Netzen).")
    n_kurz = stummel_kuerzen(board)
    if n_kurz:
        print(f"{n_kurz} Leiterbahn-Stummel gekürzt.")
    # R23 (Shunt, unten östlich der J7-Stifte): Siebdruckstriche der 2512-Bibliothek würden von der Lötstoppöffnung des
    # J7-Durchsteckpads beschnitten (DRC silk_over_copper) -> auf die Fab-Lage verschieben (Remove() stürzt in pcbnew
    # ab). Courtyard bleibt. Ändert das Routing nicht.
    for fp in board.GetFootprints():
        if fp.GetReference() == "R23":
            for g in fp.GraphicalItems():
                if g.GetLayer() == pcbnew.B_SilkS and g.GetClass() != "PCB_TEXT":
                    g.SetLayer(pcbnew.B_Fab)
                elif g.GetLayer() == pcbnew.F_SilkS and g.GetClass() != "PCB_TEXT":
                    g.SetLayer(pcbnew.F_Fab)
    gnd = board.FindNet("GND")
    for z in list(board.Zones()):
        if not z.GetIsRuleArea():
            board.Remove(z)
    n_l = leistungsflaechen(board)
    x0, x1, H = X_LINKS, X_RECHTS, PLATINE_HOEHE
    # L3 (In2) = 3,3-V-Fläche des CM5 über die ganze Platine (Priorität über GND, feste 5-V-Bahnen ausgespart)
    z3 = pcbnew.ZONE(board)
    z3.SetLayer(pcbnew.In2_Cu)
    z3.SetNetCode(board.FindNet("CM5_3V3").GetNetCode())
    z3.SetZoneName("CM5_3V3_In2")
    z3.SetAssignedPriority(5)
    ol = z3.Outline()
    ol.NewOutline()
    for x, y in ((x0, 0), (x1, 0), (x1, H), (x0, H)):
        v = _V(x, y)
        ol.Append(v.x, v.y)
    z3.SetLocalClearance(mm(0.2))
    z3.SetMinThickness(mm(0.25))
    z3.SetPadConnection(pcbnew.ZONE_CONNECTION_FULL)
    z3.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    board.Add(z3)
    for layer in _lagen():
        z = pcbnew.ZONE(board)
        z.SetLayer(layer)
        z.SetNetCode(gnd.GetNetCode())
        z.SetZoneName(f"GND_{board.GetLayerName(layer)}")
        z.SetAssignedPriority(0)
        ol = z.Outline()
        ol.NewOutline()
        for x, y in ((x0, 0), (x1, 0), (x1, H), (x0, H)):
            v = _V(x, y)
            ol.Append(v.x, v.y)
        z.SetLocalClearance(mm(0.2))
        z.SetMinThickness(mm(0.2))
        z.SetThermalReliefGap(mm(0.3))
        z.SetThermalReliefSpokeWidth(mm(0.4))
        z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
        z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
        board.Add(z)
    ds = board.GetDesignSettings()
    ds.m_TentViasFront = True
    ds.m_TentViasBack = True
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    # Stitching-Raster 4 mm: Via, wo oben und unten GND-Fläche liegt (verbindet alle 4 GND-Lagen)
    flaechen = {}
    for z in board.Zones():
        if not z.GetIsRuleArea() and z.GetNetname() == "GND" and z.GetLayer() in (pcbnew.F_Cu, pcbnew.B_Cu):
            poly = pcbnew.SHAPE_POLY_SET(z.GetFilledPolysList(z.GetLayer()))
            poly.Deflate(mm(0.45), pcbnew.CORNER_STRATEGY_ROUND_ALL_CORNERS, mm(0.005))
            flaechen[z.GetLayer()] = poly
    bohr = _bohrungen(board)
    sperren = [z.Outline() for z in board.Zones() if z.GetIsRuleArea() and z.GetDoNotAllowVias()]
    innen_bahnen = [b_ for b_ in board.GetTracks() if b_.GetClass() == "PCB_TRACK" and b_.GetNetname() != "GND" and
                    b_.GetLayer() in (pcbnew.In1_Cu, pcbnew.In2_Cu)]
    n_vias = 0
    y = 2.0
    while y < H - 1.5:
        x = x0 + 2.0
        while x < x1 - 1.5:
            pt = _V(x, y)
            frei = all(math.hypot(pcbnew.ToMM(pt.x - q.x), pcbnew.ToMM(pt.y - q.y)) > 1.0 + r for q, r in bohr) and \
                not any(sp.Contains(pt) for sp in sperren) and not any(b_.HitTest(pt, mm(0.6)) for b_ in innen_bahnen)
            if frei and all(flaechen[ly].Contains(pt) for ly in flaechen):
                v = pcbnew.PCB_VIA(board)
                v.SetPosition(pt)
                v.SetWidth(mm(0.6))
                v.SetDrill(mm(0.3))
                v.SetNet(gnd)
                board.Add(v)
                bohr.append((pt, 0.15))
                n_vias += 1
            x += 4.0
        y += 4.0
    filler.Fill(board.Zones())
    n_frag = n_pad = 0
    n_3v3 = pads_nachbinden(board, "CM5_3V3", pcbnew.In2_Cu)
    if n_3v3:
        filler.Fill(board.Zones())
    for _ in range(3):
        n_neu = gnd_pads_nachbinden(board)
        n_pad += n_neu
        if n_neu:
            filler.Fill(board.Zones())
        n_f = gnd_fragmente_verbinden(board)
        n_frag += n_f
        if not (n_neu or n_f):
            break
        filler.Fill(board.Zones())
    n_bohr2 = loch_abstand_bohrungen(board)
    if n_bohr2:
        print(f"{n_bohr2} weitere Via-Bohrungen auf 0,2 mm verkleinert.")
    board.BuildConnectivity()
    pcbnew.SaveBoard(str(pfad), board)
    stapel_eintragen(pfad)
    spuren = list(board.GetTracks())
    vias = [t for t in spuren if t.GetClass() == "PCB_VIA"]
    print(f"{n_l} Leistungsflächen, {n_vias} Stitching-Vias, {n_3v3} 3V3-Anbindungen (In2), {n_pad} GND-Pad-Anbindungen, "
          f"{n_frag} Fragment-Vias. "
          f"Fertig: {len(spuren) - len(vias)} Segmente, {len(vias)} Vias, Flächen auf 4 Lagen gefüllt.")


STAPEL = '''	(stackup
		(layer "F.SilkS" (type "Top Silk Screen"))
		(layer "F.Paste" (type "Top Solder Paste"))
		(layer "F.Mask" (type "Top Solder Mask") (thickness 0.01))
		(layer "F.Cu" (type "copper") (thickness 0.035))
		(layer "dielectric 1" (type "prepreg") (thickness 0.2104) (material "7628") (epsilon_r 4.4) (loss_tangent 0.02))
		(layer "In1.Cu" (type "copper") (thickness 0.0152))
		(layer "dielectric 2" (type "core") (thickness 1.065) (material "FR4") (epsilon_r 4.6) (loss_tangent 0.02))
		(layer "In2.Cu" (type "copper") (thickness 0.0152))
		(layer "dielectric 3" (type "prepreg") (thickness 0.2104) (material "7628") (epsilon_r 4.4) (loss_tangent 0.02))
		(layer "B.Cu" (type "copper") (thickness 0.035))
		(layer "B.Mask" (type "Bottom Solder Mask") (thickness 0.01))
		(layer "B.Paste" (type "Bottom Solder Paste"))
		(layer "B.SilkS" (type "Bottom Silk Screen"))
		(copper_finish "ENIG")
		(dielectric_constraints no)
	)
'''


def stapel_eintragen(pfad):
    """Lagenaufbau JLC04161H-7628 (1,6 mm) in die Platinendatei schreiben (nur Dokumentation/Impedanz)."""
    t = Path(pfad).read_text(encoding="utf-8")
    if "(stackup" in t:
        return
    i = t.find("(setup")
    if i < 0:
        return
    j = t.find("\n", i)
    t = t[:j + 1] + STAPEL + t[j + 1:]
    Path(pfad).write_text(t, encoding="utf-8")


# =============================================================================
#  Wächter: Lage, Höhe, Paare, Siebdruck
# =============================================================================
def pruefe_lage(ausgabe=None):
    """Bohrungen L1–L4, Freihaltekreise, J1-Lage/Pin 1, CM5-Lage (Pads gegen offizielle Geometrie), keine
    Oberseiten-Bauteile unter dem CM5, Stecker an den Kanten."""
    import pcbnew
    board = pcbnew.LoadBoard(str(PROJ / f"{PROJEKT}.kicad_pcb"))
    fps = {f.GetReference(): f for f in board.GetFootprints()}
    fehler, z = [], ["Lage-Wächter (Draufsicht von oben, mm; x −10 … 80, y 0 … 56,1):"]

    def pad(ref, num):
        return _M([p for p in fps[ref].Pads() if p.GetNumber() == num][0].GetPosition())
    # ---- Bohrungen ----
    for i, (sx, sy) in enumerate(T113_LOECHER):
        ref = f"H{i + 1}"
        p_ = list(fps[ref].Pads())[0]
        ix, iy = _M(p_.GetPosition())
        d_ist = pcbnew.ToMM(p_.GetDrillSize().x)
        ok = abs(ix - sx) < 0.01 and abs(iy - sy) < 0.01 and abs(d_ist - T113_BOHRUNG) < 0.01
        z.append(f"  {ref} (L{i + 1}): {ix:6.2f} | {iy:5.2f} Ø {d_ist:.2f}   Soll {sx:.2f} | {sy:.2f} Ø {T113_BOHRUNG}  "
                 f"{'OK' if ok else 'FALSCH'}")
        if not ok:
            fehler.append(f"Bohrung {ref}")
        r = T113_FREI / 2
        for fp in board.GetFootprints():
            if fp.GetReference() == ref:
                continue
            for q in fp.Pads():
                bb = q.GetBoundingBox()
                rr = (_M(bb.GetPosition())[0], _M(bb.GetPosition())[1], _M(bb.GetEnd())[0], _M(bb.GetEnd())[1])
                if _rp((sx, sy), rr) < r:
                    fehler.append(f"Pad {fp.GetReference()}.{q.GetNumber()} im Freihaltekreis {ref}")
            for ly in (pcbnew.F_CrtYd, pcbnew.B_CrtYd):
                c = fp.GetCourtyard(ly)
                if c.OutlineCount() and fp.GetReference() not in ("J2",):
                    kreis = pcbnew.SHAPE_POLY_SET()
                    kreis.NewOutline()
                    for px, py in _kreis(sx, sy, T113_KOPF, 32):
                        v = _V(px, py)
                        kreis.Append(v.x, v.y)
                    s = pcbnew.SHAPE_POLY_SET(c)
                    s.BooleanIntersection(kreis)
                    if s.OutlineCount() and s.Area() > 1e6:
                        fehler.append(f"Bauteil {fp.GetReference()} unter dem Schraubenkopf {ref}")
        for t in board.GetTracks():
            if t.GetClass() == "PCB_VIA":
                if _pp(_M(t.GetPosition()), (sx, sy)) < r + pcbnew.ToMM(t.GetWidth(pcbnew.F_Cu)) / 2:
                    fehler.append(f"Via im Freihaltekreis {ref}")
            elif _ps((sx, sy), _M(t.GetStart()), _M(t.GetEnd())) < r + pcbnew.ToMM(t.GetWidth()) / 2:
                fehler.append(f"Leiterbahn {t.GetNetname()} im Freihaltekreis {ref}")
    # ---- J1: Pin 1 (Breakout 1, Ethernet) zur Kante D, Pins 45–50 zur Kante B, Vorderkante bei x = −10 ----
    p1, p50 = pad("J1", "1"), pad("J1", "50")
    vk = _M(fps["J1"].GetPosition())[0] - FFC_GEHAEUSE[1] / 2
    z.append(f"  J1: Pin 1 {p1}, Pin 50 {p50} -> Pin 1 zur {'Kante D (unten)' if p1[1] > p50[1] else 'Kante B'}; "
             f"Vorderkante x = {vk:.2f}, Körper y {J1_MITTE_Y - FFC_GEHAEUSE[0] / 2:.2f} … {J1_MITTE_Y + FFC_GEHAEUSE[0] / 2:.2f}")
    if not (p1[1] > p50[1] and abs(vk - X_LINKS) < 0.01):
        fehler.append("J1-Lage/Pin-1-Richtung")
    # ---- CM5: Pads gegen offizielle Geometrie, Modulumriss, Schraubenköpfe ----
    j2 = fps["J2"]
    abw = max(_pp(_M(q.GetPosition()), cm5_pad(int(q.GetNumber()))) for q in j2.Pads() if q.GetNumber())
    ux0, uy0, ux1, uy1 = cm5_umriss()
    z.append(f"  CM5: Modul x {ux0:.1f} … {ux1:.1f} / y {uy0:.1f} … {uy1:.1f}, Drehung {j2.GetOrientationDegrees():.0f}°, "
             f"max. Pad-Abweichung zur offiziellen Geometrie {abw * 1000:.1f} µm; Pin 1 {pad('J2', '1')}, Pin 101 {pad('J2', '101')}")
    if abw > 0.001 or j2.IsFlipped():
        fehler.append("CM5-Pads weichen von der offiziellen Geometrie ab")
    for i, (sx, sy) in enumerate(T113_LOECHER):
        d = _rp((sx, sy), (ux0, uy0, ux1, uy1))
        if d < T113_KOPF / 2 + 0.3:
            fehler.append(f"CM5-Modul über Schraubenkopf L{i + 1} (Abstand {d:.2f} mm)")
        z.append(f"    Abstand Modulrand ↔ L{i + 1}-Mitte {d:.2f} mm (Kopf-Radius {T113_KOPF / 2:.2f})")
    modul = pcbnew.SHAPE_POLY_SET()
    modul.NewOutline()
    for px, py in ((ux0, uy0), (ux1, uy0), (ux1, uy1), (ux0, uy1)):
        v = _V(px, py)
        modul.Append(v.x, v.y)
    unter = []
    for fp in board.GetFootprints():
        if fp.GetReference() == "J2" or fp.IsFlipped():
            continue
        c = fp.GetCourtyard(pcbnew.F_CrtYd)
        if c.OutlineCount():
            s = pcbnew.SHAPE_POLY_SET(c)
            s.BooleanIntersection(modul)
            if s.OutlineCount() and s.Area() > 1e6:
                unter.append(fp.GetReference())
    z.append(f"  Oberseiten-Bauteile unter dem CM5: {', '.join(unter) or 'keine'}")
    if unter:
        fehler.append(f"Bauteile oben unter dem CM5: {unter}")
    # ---- Stecker an den Kanten ----
    # Steckrichtung zur Kante (Courtyard ≤ 1 mm vom Rand); J10 ist stehend (Stecker von oben) – nur Lageangabe;
    # J9b/J11 stehend (seit 24.10.2026), aber weiterhin an ihrer Kante (Pflicht); J7 seit 24.10.2026 an Kante A wie J1
    stehend = {"J10", "J9b", "J11"}
    for ref, kante, pflicht in (("J1", "A", True), ("J7", "A", True), ("J10", "B", False), ("J9b", "B", True),
                                ("J12", "C", True), ("J11", "D", True)):
        bb = fps[ref].GetCourtyard(pcbnew.F_CrtYd).BBox()
        x0_, y0_ = _M(bb.GetPosition())
        x1_, y1_ = _M(bb.GetEnd())
        abst = {"A": x0_ - X_LINKS, "B": y0_, "C": X_RECHTS - x1_, "D": PLATINE_HOEHE - y1_}[kante]
        z.append(f"  {ref}: Kante {kante}, Abstand Courtyard ↔ Kante {abst:.2f} mm" + (" (stehend)" if ref in stehend else ""))
        if pflicht and abst > 1.0:
            fehler.append(f"{ref} nicht an Kante {kante} ({abst:.2f} mm)")
    j7bb, j1bb = fps["J7"].GetCourtyard(pcbnew.F_CrtYd).BBox(), fps["J1"].GetCourtyard(pcbnew.F_CrtYd).BBox()
    luecke = _M(j1bb.GetPosition())[1] - _M(j7bb.GetEnd())[1]
    z.append(f"  J7 (XT30) über J1: Courtyard-Lücke J7 ↔ J1 {luecke:.2f} mm, Drehung {fps['J7'].GetOrientationDegrees():.0f}° "
             f"(Stecköffnung nach links), Pin 2 (+24 V) {pad('J7', '2')}, Pin 1 (GND) {pad('J7', '1')}")
    if luecke < 0 or abs(fps["J7"].GetOrientationDegrees() - 90) > 0.1:
        fehler.append("J7 überlappt J1 bzw. Stecköffnung nicht zur Kante A")
    l1 = T113_LOECHER[0]
    z.append(f"  J10 (USB-C stehend) Mitte {_M(fps['J10'].GetPosition())}, Abstand zu L1 "
             f"{_pp(_M(fps['J10'].GetPosition()), l1):.1f} mm")
    unten = sorted(f.GetReference() for f in board.GetFootprints() if f.IsFlipped())
    z.append(f"  Unterseite ({len(unten)}): {', '.join(unten)}")
    text = "\n".join(z)
    print(text)
    if ausgabe:
        Path(ausgabe).write_text(text + "\n", encoding="utf-8")
    if fehler:
        print("LAGE-PRÜFUNG FEHLGESCHLAGEN:", fehler)
        sys.exit(1)


def _modell_hoehe(pfad, fp):
    """Max. z (und min. z) eines 3D-Modells in mm (STEP: CARTESIAN_POINT; WRL: Quader), None wenn nicht lesbar."""
    import re
    p = pfad.replace("${KICAD10_3DMODEL_DIR}", str(MODDIR)).replace("${KIPRJMOD}", str(PROJ))
    if not Path(p).exists():
        return None
    t = Path(p).read_text(errors="replace")
    if p.lower().endswith((".step", ".stp")):
        zs = [float(m) for m in re.findall(r"CARTESIAN_POINT\s*\(\s*'[^']*'\s*,\s*\(\s*[-\d.Ee+]+\s*,\s*[-\d.Ee+]+\s*,\s*([-\d.Ee+]+)\s*\)", t)]
        return (min(zs), max(zs)) if zs else None
    boxen = re.findall(r"translation [-\d.]+ [-\d.]+ ([-\d.]+).*?size [-\d.]+ [-\d.]+ ([-\d.]+)", t)
    if not boxen:
        return None
    return (min((float(a) - float(b) / 2) * 2.54 for a, b in boxen), max((float(a) + float(b) / 2) * 2.54 for a, b in boxen))


def pruefe_hoehe(ausgabe=None):
    """Höhen-Wächter: jedes Bauteil (DATENBLATT-Höhe; die 3D-Modelle dienen nur der Darstellung und werden nur als
    Information mit ausgegeben) gegen das Budget.
    Oben ≤ 11,5 − 1,6 = 9,9 mm über der Platine (J10 ≤ 9,0 − 1,6 = 7,4 mm), unten ≤ 2,0 mm (inkl. THT-Überstand)."""
    import pcbnew
    board = pcbnew.LoadBoard(str(PROJ / f"{PROJEKT}.kicad_pcb"))
    T, _ = bauteile()
    teil = {t.ref: t for t in T}
    oben_max = HOEHE_GESAMT - PLATINE_DICKE
    fehler, zeilen = [], []
    for fp in sorted(board.GetFootprints(), key=lambda f: f.GetReference()):
        ref = fp.GetReference()
        t = teil.get(ref)
        if t is None or ref.startswith(("H", "FID", "TP", "JP")) and not ref.startswith(("JP_",)):
            continue
        seite = "unten" if fp.IsFlipped() else "oben"
        h_ds = t.hoehe
        h_mod = None
        for m in fp.Models():
            r = _modell_hoehe(m.m_Filename, fp)
            if r:
                h_mod = max(h_mod or 0, r[1] * m.m_Scale.z + m.m_Offset.z)
        h = h_ds if ref != "J2" else CM5_MONTIERT     # seit 24.10.2026 nur Datenblatt (echte 3D-Modelle = Optik)
        budget = HOEHE_UNTEN if seite == "unten" else ((HOEHE_J10 - PLATINE_DICKE) if ref == "J10" else oben_max)
        ueber = h + (0 if seite == "unten" else PLATINE_DICKE)
        zeilen.append((ref, seite, h_ds, h_mod, h, budget, ueber, t.unten_tht))
        if h > budget + 1e-6:
            fehler.append(f"{ref} ({seite}) {h:.2f} mm > Budget {budget:.2f} mm")
        if t.unten_tht > HOEHE_UNTEN:
            fehler.append(f"{ref}: THT-Überstand unten {t.unten_tht:.2f} mm > {HOEHE_UNTEN} mm")
    kuehler = HOEHE_GESAMT - PLATINE_DICKE - CM5_MONTIERT
    z = [f"Höhen-Wächter: Budget oben {oben_max:.1f} mm über der Platine (gesamt {HOEHE_GESAMT} mm über der Auflage), "
         f"J10 {HOEHE_J10 - PLATINE_DICKE:.1f} mm (gesamt {HOEHE_J10} mm), unten {HOEHE_UNTEN} mm (Luft 2,45 mm gemessen).",
         f"CM5: Platine {PLATINE_DICKE} + CM5 montiert {CM5_MONTIERT} (1,5-mm-Steckverbinder) = "
         f"{PLATINE_DICKE + CM5_MONTIERT:.2f} mm -> Kühlkörper inkl. Wärmeleitpad höchstens {kuehler:.2f} mm.",
         f"{'Ref':12s} {'Seite':6s} {'Datenbl.':>8s} {'3D(Info)':>8s} {'gewertet':>8s} {'Budget':>7s} {'über Aufl.':>10s} {'THT unten':>9s}"]
    for ref, seite, hd, hm, h, b, ue, tht in zeilen:
        z.append(f"{ref:12s} {seite:6s} {hd:8.2f} {('%.2f' % hm) if hm is not None else '–':>8s} {h:8.2f} {b:7.2f} "
                 f"{ue if seite == 'oben' else -h:10.2f} {tht:9.2f}")
    oben = [r for r in zeilen if r[1] == "oben" and r[0] != "J2"]
    unten = [r for r in zeilen if r[1] == "unten"]
    hoechst_o = max(oben, key=lambda r: r[4])
    hoechst_u = max(unten, key=lambda r: r[4])
    hoechst_t = max(zeilen, key=lambda r: r[7])
    for ref in sorted(r for r in teil if teil[r].gesteckt):
        g = teil[ref].gesteckt
        z.append(f"HINWEIS {ref} stehend: gesteckt (Header + Stecker + Kabelbogen) ≈ {g:.1f} mm über der Platine = "
                 f"{g + PLATINE_DICKE:.1f} mm über der Auflage" + (f" > {HOEHE_GESAMT} mm -> NUR STECKEN, WENN DARÜBER PLATZ IST"
                                                                    if g + PLATINE_DICKE > HOEHE_GESAMT else " ✓"))
    z.append(f"Höchstes Bauteil oben (außer CM5): {hoechst_o[0]} {hoechst_o[4]:.2f} mm -> {hoechst_o[4] + PLATINE_DICKE:.2f} mm "
             f"über der Auflage; unten: {hoechst_u[0]} {hoechst_u[4]:.2f} mm; größter THT-Überstand: {hoechst_t[0]} "
             f"{hoechst_t[7]:.2f} mm.")
    text = "\n".join(z)
    print(text)
    if ausgabe:
        Path(ausgabe).write_text(text + "\n", encoding="utf-8")
    if fehler:
        print("HÖHEN-PRÜFUNG FEHLGESCHLAGEN:")
        for x in fehler:
            print("  -", x)
        sys.exit(1)


def pruefe_paare(ausgabe=None):
    """Längenabgleich der festen Paare: aus der Platine nachgemessen (gesperrte Bahnen + Vias, ohne ESD-Abgriffe
    und die zweite Steckseite der USB-C-Buchse), Grenze 0,15 mm."""
    import pcbnew
    board = pcbnew.LoadBoard(str(PROJ / f"{PROJEKT}.kicad_pcb"))
    soll = json.loads((PROJ / "zwischen" / "paare.json").read_text(encoding="utf-8"))
    fehler, z = [], ["Paar-Wächter (Längenabgleich, Via = 1,53 mm; Bahnbreite/Abstand USB 0,25/0,15, "
                     "Ethernet und DSI 0,20/0,20):"]
    for name, werte in soll.items():
        (na, a), (nb, b) = werte.items()
        d = abs(a["gesamt"] - b["gesamt"])
        z.append(f"  {name:6s} {na:9s} {a['gesamt']:8.3f} mm ({a['vias']} Vias)   {nb:9s} {b['gesamt']:8.3f} mm "
                 f"({b['vias']} Vias)   Δ = {d:.3f} mm {'OK' if d <= 0.15 else 'ZU GROSS'}")
        if d > 0.15:
            fehler.append(name)
    # DSI: Lanes gegen den Takt (Mittel aus P und N), Grenze 0,5 mm (≈ 3,5 ps)
    lanes = {k: sum(w["gesamt"] for w in v.values()) / 2 for k, v in soll.items() if k.startswith("DSI_")}
    for k in sorted(lanes):
        if k != "DSI_C" and "DSI_C" in lanes:
            d = abs(lanes[k] - lanes["DSI_C"])
            z.append(f"  {k} gegen Takt DSI_C: {lanes[k]:.3f} / {lanes['DSI_C']:.3f} mm   Δ = {d:.3f} mm "
                     f"{'OK' if d <= 0.5 else 'ZU GROSS'} (Grenze 0,5 mm)")
            if d > 0.5:
                fehler.append(f"{k} gegen Takt")
    # Gegenprüfung an der fertigen Platine: gesperrte Bahnen je Netz unverändert vorhanden
    ist = {}
    for t in board.GetTracks():
        if t.GetClass() == "PCB_TRACK" and t.IsLocked():
            ist[t.GetNetname()] = ist.get(t.GetNetname(), 0) + pcbnew.ToMM(t.GetLength())
    for name, werte in soll.items():
        for n, w in werte.items():
            if ist.get(n, 0) + 1e-3 < w["bahn"] - 1e-3:
                fehler.append(f"{n}: feste Bahnen auf der Platine kürzer als erzeugt ({ist.get(n, 0):.3f} < {w['bahn']:.3f})")
    text = "\n".join(z)
    print(text)
    if ausgabe:
        Path(ausgabe).write_text(text + "\n", encoding="utf-8")
    if fehler:
        print("PAAR-PRÜFUNG FEHLGESCHLAGEN:", fehler)
        sys.exit(1)


def pruefe_siebdruck(ausgabe=None, mindest=0.3, pad_mindest=0.15):
    """Siebdruck-Wächter: Texte je Lage paarweise (Abstand), zu Lötstopp-Öffnungen, Platinenrand; Mindesthöhe 0,8 mm."""
    import itertools
    import pcbnew
    board = pcbnew.LoadBoard(str(PROJ / f"{PROJEKT}.kicad_pcb"))

    def box(bb, rand=0.0):
        a, b = _M(bb.GetPosition()), _M(bb.GetEnd())
        return (min(a[0], b[0]) - rand, min(a[1], b[1]) - rand, max(a[0], b[0]) + rand, max(a[1], b[1]) + rand)

    def abstand(a, c):
        dx = max(c[0] - a[2], a[0] - c[2])
        dy = max(c[1] - a[3], a[1] - c[3])
        if dx < 0 and dy < 0:
            return max(dx, dy)
        return (max(dx, 0) ** 2 + max(dy, 0) ** 2) ** 0.5
    seiten = {pcbnew.F_SilkS: ("oben", pcbnew.F_Mask), pcbnew.B_SilkS: ("unten", pcbnew.B_Mask)}
    texte, fehler = [], []
    for t in board.GetDrawings():
        if t.GetClass() == "PCB_TEXT" and t.GetLayer() in seiten and t.IsVisible():
            texte.append((t.GetLayer(), f"Text „{t.GetText().splitlines()[0]}“", box(t.GetBoundingBox()), id(t),
                          pcbnew.ToMM(t.GetTextSize().y)))
    for fp in board.GetFootprints():
        for f_ in fp.GetFields():
            if f_.IsVisible() and f_.GetLayer() in seiten:
                texte.append((f_.GetLayer(), f"{fp.GetReference()}.{f_.GetName()} „{f_.GetShownText(False)}“",
                              box(f_.GetBoundingBox()), fp.GetReference(), pcbnew.ToMM(f_.GetTextSize().y)))
    paare = []
    for a, c in itertools.combinations(texte, 2):
        if a[0] != c[0] or a[3] == c[3]:
            continue
        d = abstand(a[2], c[2])
        paare.append((d, seiten[a[0]][0], a[1], c[1]))
        if d < mindest:
            fehler.append(f"Texte zu nah ({d:.2f} mm, {seiten[a[0]][0]}): {a[1]} / {c[1]}")
    pad_paare = []
    for fp in board.GetFootprints():
        for p_ in fp.Pads():
            for lage, (seite, maske) in seiten.items():
                if not p_.IsOnLayer(maske):
                    continue
                pb = box(p_.GetBoundingBox(), pcbnew.ToMM(p_.GetSolderMaskExpansion(maske)))
                for t in texte:
                    if t[0] == lage:
                        d = abstand(t[2], pb)
                        pad_paare.append((d, seite, t[1], f"{fp.GetReference()}.{p_.GetNumber() or 'Loch'}"))
                        if d < pad_mindest:
                            fehler.append(f"Text an Lötstopp-Öffnung ({d:.2f} mm, {seite}): {t[1]} / "
                                          f"{fp.GetReference()}.{p_.GetNumber() or 'Loch'}")
    for t in texte:
        x0_, y0_, x1_, y1_ = t[2]
        if x0_ < X_LINKS + 0.3 or y0_ < 0.3 or x1_ > X_RECHTS - 0.3 or y1_ > PLATINE_HOEHE - 0.3:
            fehler.append(f"Text ragt an/über die Platinenkante: {t[1]}")
        if t[4] < 0.8 - 1e-6:
            fehler.append(f"Text kleiner als 0,8 mm: {t[1]}")
    warn = [t for t in texte if "ALPHA" in t[1] or "EIGENE GEFAHR" in t[1]]
    if not any(t[0] == pcbnew.F_SilkS for t in warn):
        fehler.append("Warnhinweis ALPHA fehlt auf der Oberseite")
    paare.sort()
    pad_paare.sort()
    z = [f"Siebdruck-Wächter: {len(texte)} sichtbare Texte, {len(paare)} Paare (Mindestabstand {mindest} mm, "
         f"{pad_mindest} mm zu Lötstopp-Öffnungen, Schrifthöhe ≥ 0,8 mm). Warnhinweis: "
         f"{', '.join(t[1] for t in warn)}", "Kleinste Abstände zwischen Texten:"]
    z += [f"  {d:5.2f} mm ({s}) {a} ↔ {c}" for d, s, a, c in paare[:5]]
    z.append("Kleinste Abstände Text ↔ Lötstopp-Öffnung:")
    z += [f"  {d:5.2f} mm ({s}) {a} ↔ {c}" for d, s, a, c in pad_paare[:3]]
    text = "\n".join(z)
    print(text)
    if ausgabe:
        Path(ausgabe).write_text(text + "\n", encoding="utf-8")
    if fehler:
        print("SIEBDRUCK-PRÜFUNG FEHLGESCHLAGEN:")
        for f_ in fehler:
            print("  -", f_)
        sys.exit(1)


# =============================================================================
#  JLCPCB-Dateien
# =============================================================================
def _sortkey(r):
    import re
    m = re.match(r"([A-Z_]+?)(\d+)(.*)", r)
    return (m.group(1), int(m.group(2)), m.group(3)) if m else (r, 0, "")


# JLC-Bestückungsvorschau: Die JLC-/EasyEDA-Bauteile haben je Teil eine eigene Nulllage; dazu spiegelt die
# Unterseite. Zuschlag auf die KiCad-Drehung je Bauteil (Grad; Oberseite gegen den Uhrzeigersinn von oben gesehen,
# Unterseite gegen den Uhrzeigersinn von unten gesehen), ermittelt an der Vorschau: Pin-1-Punkt bzw. Gehäusepunkt/
# Kathodenring des JLC-Modells gegen Pad 1, Beinchen gegen Pads. Nicht genannte Teile passten ohne Zuschlag.
# D8 zeigt in der Vorschau kein Modell (gleiche Bauform wie D15) -> im Produktionsbild von JLC gegenprüfen.
JLC_DREHUNG = {
    "J2": -90,                              # DF40C (J2A/J2B): JLC-Modell bei 0° waagerecht, Pin 1 links unten
    "D1": -90, "D2": -90, "D3": -90, "D4": -90,   # SOT-23-6: JLC-Beinchen oben/unten, KiCad links/rechts
    "D5": 180,                              # SM712 (SOT-23): Zweibein-Seite gegenüber
    "D6": 90,                               # NUP2105L (SOT-23)
    "D7": -90,                              # TPD4E05U06 (USON-10)
    "D8": 180, "D15": 180,                  # SOD-123F unten: Kathodenring sonst auf Pad 2
    "U1": 90, "U2": 90, "U3": 90,           # SOIC unten
    "U4": 90, "U5": 90,                     # MSOP-10 INA226 unten
    "U10": 90, "U11": 90, "U12": 90, "U13": 90,   # SN74LVC1G125 (SOT-23-5); U14 (TLV62569) passt ohne Zuschlag
    "U7": 180, "U8": 180,                   # WSON-12 / VQFN-HR-12: Pin 1 diagonal gegenüber
    "J9b": 180, "J11": 180,                 # JST GH stehend: sonst Kontaktbeinchen auf den Halte-Pads (MP)
    "J7": 180,                              # XT30: EasyEDA-Footprint um 180° gedreht (s. LCSC_MODELLE)
}


def jlc_dateien(pos_csv, ziel):
    """JLCPCB-BOM/CPL für alle vom Hersteller bestückten SMD-Teile (beide Seiten). Der CM5-Footprint J2 enthält zwei
    DF40-Steckverbinder -> zwei CPL-Zeilen J2A/J2B (Mitten aus der Footprint-Geometrie)."""
    import csv
    T, _ = bauteile()
    pos = pcb_positionen()
    smd_t = [t for t in T if t.bestueckung.startswith("Hersteller") and not t.dnp]
    zeilen = list(csv.DictReader(open(pos_csv, encoding="utf-8")))
    ziel = Path(ziel)
    gruppen = {}
    for t in smd_t:
        refs = ["J2A", "J2B"] if t.ref == "J2" else [t.ref]
        gruppen.setdefault((t.wert if t.ref != "J2" else "DF40C-100DS-0.4V(51)", t.fp, t.lcsc, t.mpn, t.hersteller), []).extend(refs)
    with open(ziel / "jlcpcb_bom.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Comment", "Designator", "Footprint", "LCSC Part #", "MPN", "Manufacturer", "Quantity", "Notes"])
        for (wert, fp, lcsc, mpn, herst), refs in sorted(gruppen.items(), key=lambda kv: _sortkey(sorted(kv[1], key=_sortkey)[0])):
            refs = sorted(refs, key=_sortkey)
            unten = [r for r in refs if pos.get(r, (0, 0, 0, ""))[3] == "unten"]
            notiz = ("Basic" if lcsc in BASIC_LCSC else "Extended") if lcsc else "LCSC-Nr. offen – passendes Teil wählen"
            if refs[0].startswith(("U", "D", "Q", "J", "T", "Y")):
                notiz += "; Pin 1/Drehung in der JLC-Vorschau prüfen"
            if unten:
                notiz += "; UNTERSEITE: " + ",".join(unten)
            if "J7" in refs:
                notiz += "; THT – Handlöten (hand soldering), Stecköffnung zeigt über Kante A nach außen"
            w.writerow([wert, ",".join(refs), fp.split(":")[1], lcsc, mpn, herst, len(refs), notiz])
    with open(ziel / "jlcpcb_cpl.csv", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
        for t in sorted(smd_t, key=lambda t: _sortkey(t.ref)):
            zl = [r for r in zeilen if r["Ref"] == t.ref]
            zusatz = JLC_DREHUNG.get(t.ref, 0)
            if zl and t.ref == "J7":
                # THT (Positionsexport ohne --smd-only): KiCad-Ursprung = Pin 1. JLC-Ursprung = EasyEDA-Ursprung =
                # Mitte der vier Löcher, Footprint-lokal (−2,5 | −5) -> bei 90° auf der Platine (−5,0 | +2,5) mm,
                # in Positionsdatei-Koordinaten (y nach oben) (−5,0 | −2,5). Drehung +180° (JLC_DREHUNG).
                zl = zl[0]
                w.writerow(["J7", f"{float(zl['PosX']) - 5.0:.4f}mm", f"{float(zl['PosY']) - 2.5:.4f}mm", "Top",
                            f"{(float(zl['Rot']) + zusatz) % 360:.0f}"])
                continue
            if not zl:
                raise RuntimeError(f"{t.ref} fehlt in der Positionsdatei")
            zl = zl[0]
            lage = "Top" if zl["Side"].lower().startswith("top") else "Bottom"
            drehung = (float(zl["Rot"]) + zusatz) % 360
            if t.ref == "J2":
                # Mitten der beiden Steckverbinder (Footprint-lokal (−0,46|−21,5) und (33,46|−21,5), Drehung 90°)
                bx, by = float(zl["PosX"]), float(zl["PosY"])
                for name, (lx, ly) in (("J2A", (-0.46, -21.5)), ("J2B", (33.46, -21.5))):
                    w.writerow([name, f"{bx + ly:.4f}mm", f"{by + lx:.4f}mm", lage, f"{drehung:.0f}"])
                continue
            w.writerow([t.ref, f"{float(zl['PosX']):.4f}mm", f"{float(zl['PosY']):.4f}mm", lage, f"{drehung:.0f}"])
    print(f"JLCPCB-Bestückungsdateien: {len(smd_t) + 1} SMD-Positionen in {len(gruppen)} Gruppen – jlcpcb_bom.csv, jlcpcb_cpl.csv")


# =============================================================================
def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    cmd = sys.argv[1]
    (PROJ / "zwischen").mkdir(exist_ok=True)
    (PROJ / "ausgabe").mkdir(exist_ok=True)
    if cmd == "schaltplan":
        T, _ = bauteile()
        erzeuge_projekt()
        erzeuge_symbole()
        erzeuge_footprints()
        erzeuge_schaltplan(T)
        print(f"Schaltplan erzeugt: {len([t for t in T if not t.ref.startswith('#')])} Bauteile")
    elif cmd == "pruefe-netzliste":
        pruefe_netzliste(sys.argv[2])
    elif cmd == "pruefe-cm5":
        pruefe_cm5(sys.argv[2])
    elif cmd == "platine":
        platine(sys.argv[2])
    elif cmd == "freerouting":
        freerouting_varianten(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5],
                              int(sys.argv[6]) if len(sys.argv) > 6 else 24)
    elif cmd == "drc-fehler":
        d = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
        n = sum(1 for v in d.get("violations", []) if v.get("severity") == "error")
        n += len(d.get("unconnected_items", [])) + len(d.get("schematic_parity", []))
        print(n)
    elif cmd == "fertigstellen":
        fertigstellen(sys.argv[2] if len(sys.argv) > 2 else "-")
    elif cmd == "pruefe-lage":
        pruefe_lage(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "pruefe-hoehe":
        pruefe_hoehe(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "pruefe-paare":
        pruefe_paare(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "pruefe-siebdruck":
        pruefe_siebdruck(sys.argv[2] if len(sys.argv) > 2 else None)
    elif cmd == "jlc-dateien":
        jlc_dateien(sys.argv[2], sys.argv[3])
    elif cmd in ("bericht-erc", "bericht-drc"):
        bericht(cmd.split("-")[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    else:
        print(__doc__)
        sys.exit(2)


if __name__ == "__main__":
    main()
