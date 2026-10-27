#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline-Prüfung des Softwarepakets „K2-CM5-Träger Alpha 1“ gegen den Schaltplan.

Quellen (nur lesend):
  hardware/k2-pi-cm5-traeger/generator/k2_traeger.py   (Schaltplan-Generator, maßgeblich)
  hardware/k2-pi-cm5-traeger/generator/cm5_pins.json   (CM5-Datenblatt: Pin -> Name)
  hardware/k2-pi-cm5-traeger/ausgabe/cm5-pins.txt      (Wächter-Ausgabe, Gegenprobe)
  hardware/k2-pi-cm5-traeger/ausgabe/stueckliste.csv   (Shunt-Werte)

Prüft:
 1. Schaltplan-Zuordnung GPIO -> Netz (Generator + Datenblatt) = SOLL dieses Pakets;
    cm5-pins.txt stimmt für alle benutzten GPIOs überein (veraltete Einträge nur Hinweis).
 2. config.txt-Ausschnitt: Marker, nur ASCII, Zeilen ≤ 76 Zeichen, jede Einstellung
    mit Kommentar davor; Overlay-Pins (Tabelle aus dem Overlay-README) = Schaltplan-Pins;
    keine GPIO-Doppelbelegung (Overlays + Stromausfall-Dienst); nur unbeschaltete Pins
    dürfen nebenbei belegt werden (GPIO16 CTS, GPIO7 CE1).
 3. GPIO6 und GPIO26 (früher Toolhead-eFuse) werden nirgends verwendet; im Schaltplan frei.
 4. CAN: spi0-0 = CAN_CS an GPIO8, interrupt=25 = CAN_INT, oscillator = Quarz Y1.
 5. RS485: THVD1450 DE und ~RE an RS485_DE = GPIO17 = UART0-RTS (uart0-pi5,rs485).
 6. INA226-Overlay: Adressen aus A1/A0, Shunts aus Generator + Stückliste, ALERT 0x40 ->
    PWR_FAIL_N -> GPIO27 = k2_stromausfall-Standard; kein 0x44 mehr.
 7. DSI: vc4-kms-dsi-7inch (DSI1) <-> MIPI1-Netze, I2C ID_SD/ID_SC = GPIO0/1.
 8. Klipper-Ausschnitte: ttyAMA<n> passt zu uart<n>-pi5; Main = UART4, Düse = UART2
    (Generator), RS485 = ttyAMA0 mit rs485_de: kernel (von k2_485.py akzeptiert),
    [mcu toolhead] nur auskommentiert.
 9. can0: 1 Mbit/s, txqueuelen 1024, RequiredForOnline=no.
10. Dienste/Pfade zwischen install-traeger.sh, Units und Skripten konsistent;
    Shell-Syntax (bash -n); config.txt-Editor aus install-traeger.sh: einfügen,
    alte Overlays auskommentieren, idempotent, alter Block ersetzt.
11. Overlay-Quelltext plausibel (mit dtc übersetzt, falls vorhanden).
12. Keine IPs, Hostnamen, Seriennummern, Home-Pfade des PCs, root-Home, E-Mail-Adressen.
"""

import ast
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HIER = os.path.dirname(os.path.abspath(__file__))
PIHOST = os.path.dirname(HIER)
WURZEL = os.path.dirname(PIHOST)
HW = os.path.join(WURZEL, "hardware", "k2-pi-cm5-traeger")
GEN = os.path.join(HW, "generator", "k2_traeger.py")

ERG = []


def check(name, cond, detail=""):
    ERG.append((name, bool(cond)))
    print("%s %s %s" % ("PASS" if cond else "FAIL", name, detail))


def hinweis(text):
    print("HINWEIS %s" % text)


def lies(p):
    with open(p, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------------
# SOLL dieses Pakets: GPIO -> (Netz im Schaltplan, Funktion, Quelle)
# ---------------------------------------------------------------------
SOLL = {
    0: ("DSI_SDA", "i2c_csi_dsi SDA (Display)", "vc4-kms-dsi-7inch"),
    1: ("DSI_SCL", "i2c_csi_dsi SCL (Display)", "vc4-kms-dsi-7inch"),
    2: ("I2C1_SDA", "I2C1 SDA (INA226)", "i2c_arm"),
    3: ("I2C1_SCL", "I2C1 SCL (INA226)", "i2c_arm"),
    4: ("UART2_TX", "UART2 TX (Düsen-MCU)", "uart2-pi5"),
    5: ("UART2_RX", "UART2 RX (Düsen-MCU)", "uart2-pi5"),
    7: (None, "SPI0 CE1 (unbenutzt, nicht beschaltet)", "mcp251xfd"),
    8: ("CAN_CS", "SPI0 CE0 (MCP2518FD)", "mcp251xfd"),
    9: ("CAN_MISO", "SPI0 MISO", "mcp251xfd"),
    10: ("CAN_MOSI", "SPI0 MOSI", "mcp251xfd"),
    11: ("CAN_SCK", "SPI0 SCLK", "mcp251xfd"),
    12: ("UART4_TX", "UART4 TX (Main-MCU)", "uart4-pi5"),
    13: ("UART4_RX", "UART4 RX (Main-MCU)", "uart4-pi5"),
    14: ("UART0_TX", "UART0 TX (RS485 DI)", "uart0-pi5"),
    15: ("UART0_RX", "UART0 RX (RS485 RO)", "uart0-pi5"),
    16: (None, "UART0 CTS (durch rs485 belegt, nicht beschaltet)", "uart0-pi5"),
    17: ("RS485_DE", "UART0 RTS = DE/~RE THVD1450", "uart0-pi5"),
    25: ("CAN_INT", "MCP2518FD nINT", "mcp251xfd"),
    27: ("PWR_FAIL_N", "INA226 0x40 ALERT (Stromausfall)", "k2_stromausfall"),
}
UNBESCHALTET_ERLAUBT = {7, 16}

# Overlay -> belegte GPIOs (raspberrypi/linux rpi-6.18.y overlays/README):
#  uart<n>-pi5: „Enable uart n on GPIOs a-b“, ctsrts „GPIOs c-d“, rs485 schaltet ctsrts ein
UART_PI5 = {0: ((14, 15), (16, 17)), 2: ((4, 5), (6, 7)), 4: ((12, 13), (14, 15))}
SPI0 = {"ce0": 8, "ce1": 7, "miso": 9, "mosi": 10, "sclk": 11}


def overlay_pins(name, params):
    """[(gpio, funktion)] für eine dtoverlay-/dtparam-Zeile."""
    if name.startswith("uart") and name.endswith("-pi5"):
        n = int(name[4:-4])
        txrx, cr = UART_PI5[n]
        aus = [(txrx[0], "uart%d TX" % n), (txrx[1], "uart%d RX" % n)]
        if "ctsrts" in params or "rs485" in params:
            aus += [(cr[0], "uart%d CTS" % n), (cr[1], "uart%d RTS" % n)]
        return aus
    if name == "mcp251xfd":
        aus = []
        if "spi0-0" in params:
            aus += [(SPI0["ce0"], "spi0 CE0"), (SPI0["ce1"], "spi0 CE1"),
                    (SPI0["miso"], "spi0 MISO"), (SPI0["mosi"], "spi0 MOSI"),
                    (SPI0["sclk"], "spi0 SCLK")]
        if "interrupt" in params:
            aus.append((int(params["interrupt"]), "mcp251xfd nINT"))
        return aus
    if name == "dtparam:i2c_arm":
        return [(2, "i2c1 SDA"), (3, "i2c1 SCL")]
    if name == "vc4-kms-dsi-7inch":
        # DSI1 + i2c_csi_dsi; CM5 mit cm5io-DT: „i2c-0 on 0 & 1“
        return [] if "dsi0" in params else [(0, "i2c_csi_dsi SDA"), (1, "i2c_csi_dsi SCL")]
    return []


# ---------------------------------------------------------------------
# Schaltplan lesen
# ---------------------------------------------------------------------

def generator_lesen():
    src = lies(GEN)
    baum = ast.parse(src)
    werte = {}
    for knoten in baum.body:
        if isinstance(knoten, ast.Assign) and len(knoten.targets) == 1 \
                and isinstance(knoten.targets[0], ast.Name) \
                and knoten.targets[0].id in ("CM5_SIGNALE", "CM5_ERWARTET", "BR_UART"):
            werte[knoten.targets[0].id] = ast.literal_eval(knoten.value)
    return src, werte


def teil_pins(src, ref):
    m = re.search(r'Teil\("%s",.*?\{(.*?)\}' % re.escape(ref), src, re.S)
    if not m:
        return None
    return dict(re.findall(r'"([0-9A-Z]+)":\s*(?:"([^"]*)"|None)', m.group(1)))


def widerstand(src, ref):
    m = re.search(r'R\("%s",\s*"([^"]+)",\s*"([^"]+)",\s*"([^"]+)"' % re.escape(ref), src)
    return m.groups() if m else None


def schaltplan_gpio(werte):
    pins = json.loads(lies(os.path.join(HW, "generator", "cm5_pins.json")))["pins"]
    aus = {}
    for pin, netz in werte["CM5_SIGNALE"].items():
        name = pins[str(pin)][0]
        m = re.match(r"^GPIO(\d+)$", name)
        if m:
            aus[int(m.group(1))] = (netz, pin)
        elif name == "ID_SD":
            aus[0] = (netz, pin)
        elif name == "ID_SC":
            aus[1] = (netz, pin)
    return aus, pins


def cm5_pins_txt():
    aus = {}
    p = os.path.join(HW, "ausgabe", "cm5-pins.txt")
    for z in lies(p).splitlines():
        m = re.match(r"^\s*(\d+)\s+(\S+)\s+->\s+(\S+)\s*$", z)
        if m:
            aus[int(m.group(1))] = (m.group(2), m.group(3))
    return aus


# ---------------------------------------------------------------------
# Paketdateien lesen
# ---------------------------------------------------------------------

def config_txt_zeilen():
    return lies(os.path.join(HIER, "config-txt-traeger.txt")).splitlines()


def config_eintraege(zeilen):
    """[(art, name, params)] für dtoverlay/dtparam."""
    aus = []
    for z in zeilen:
        z = z.strip()
        if z.startswith("dtoverlay="):
            teile = z[len("dtoverlay="):].split(",")
            params = {}
            for t in teile[1:]:
                k, _, v = t.partition("=")
                params[k] = v if v else True
            aus.append(("dtoverlay", teile[0], params))
        elif z.startswith("dtparam="):
            k, _, v = z[len("dtparam="):].partition("=")
            aus.append(("dtparam", k, {"wert": v}))
    return aus


def cfg_lesen(pfad):
    """einfacher Klipper-cfg-Leser: {abschnitt: {schluessel: wert}} (aktive Zeilen)."""
    abschnitte, akt = {}, None
    for z in lies(pfad).splitlines():
        s = z.split("#", 1)[0].rstrip()
        if not s.strip():
            continue
        m = re.match(r"^\[([^\]]+)\]\s*$", s)
        if m:
            akt = m.group(1).strip()
            abschnitte.setdefault(akt, {})
            continue
        if akt is not None and ":" in s:
            k, _, v = s.partition(":")
            abschnitte[akt][k.strip()] = v.strip()
    return abschnitte


# ---------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------

def test_schaltplan(src, werte):
    sp, _ = schaltplan_gpio(werte)
    for gpio, (netz, funk, quelle) in sorted(SOLL.items()):
        ist = sp.get(gpio, (None, None))[0]
        if netz is None:
            check("Schaltplan: GPIO%d nicht beschaltet (%s)" % (gpio, funk), ist is None, str(ist))
        else:
            check("Schaltplan: GPIO%d = %s (%s, %s)" % (gpio, netz, funk, quelle), ist == netz, str(ist))
    # Gegenprobe Datenblatt-Erwartung im Generator
    erw = werte["CM5_ERWARTET"]
    for gpio, (netz, _f, _q) in SOLL.items():
        if netz is None or gpio in (0, 1):
            continue
        check("Generator CM5_ERWARTET: GPIO%d -> %s" % (gpio, netz),
              erw.get("GPIO%d" % gpio) == netz, str(erw.get("GPIO%d" % gpio)))
    check("Generator CM5_ERWARTET: ID_SD/ID_SC = DSI_SDA/DSI_SCL",
          erw.get("ID_SD") == "DSI_SDA" and erw.get("ID_SC") == "DSI_SCL")
    # Alle GPIO-Netze des Schaltplans sind in SOLL erfasst (nichts vergessen)
    fremd = {g: n for g, (n, _p) in sp.items() if g not in SOLL}
    check("Schaltplan: keine GPIO-Netze ohne Software-Zuordnung", not fremd, str(fremd))
    # cm5-pins.txt (Wächter-Ausgabe) als Gegenprobe
    txt = cm5_pins_txt()
    for gpio, (netz, pin) in sp.items():
        ist = txt.get(pin, (None, None))[1]
        check("cm5-pins.txt: Pin %d = %s" % (pin, netz), ist == netz, str(ist))
    for pin, (name, netz) in sorted(txt.items()):
        m = re.match(r"^GPIO(\d+)$", name)
        if m and int(m.group(1)) not in sp:
            hinweis("cm5-pins.txt nennt Pin %d %s -> %s, im Generator frei: Ausgabe veraltet "
                    "(bauen.sh neu laufen lassen)" % (pin, name, netz))


def test_config_txt(src, werte):
    zeilen = config_txt_zeilen()
    check("config.txt: Marker Anfang/Ende",
          zeilen[0].startswith("# >>> K2-TRAEGER-ALPHA1") and zeilen[-1].startswith("# <<< K2-TRAEGER-ALPHA1"))
    check("config.txt: nur ASCII", all(all(ord(c) < 128 for c in z) for z in zeilen))
    lang = [i + 1 for i, z in enumerate(zeilen) if len(z) > 76]
    check("config.txt: Zeilen ≤ 76 Zeichen", not lang, str(lang))
    ohne = [z for i, z in enumerate(zeilen)
            if z.strip() and not z.startswith("#") and not z.startswith("[")
            and not (i > 0 and zeilen[i - 1].startswith("#"))]
    check("config.txt: jede Einstellung mit Kommentar davor", not ohne, str(ohne))
    check("config.txt: [all] vor den Einstellungen", "[all]" in zeilen)
    eintr = config_eintraege(zeilen)
    namen = [n for a, n, p in eintr if a == "dtoverlay"]
    check("config.txt: Overlays vollständig",
          namen == ["k2-traeger-ina226", "uart2-pi5", "uart4-pi5", "uart0-pi5", "mcp251xfd",
                    "vc4-kms-dsi-7inch"], str(namen))
    sp, _ = schaltplan_gpio(werte)
    belegt = {}
    for art, name, params in eintr:
        schl = name if art == "dtoverlay" else "dtparam:" + name
        if art == "dtparam" and params.get("wert") != "on":
            continue
        for gpio, funk in overlay_pins(schl, params):
            belegt.setdefault(gpio, []).append("%s (%s)" % (funk, schl))
    kfg_sa = re.search(r"^K2SA_GPIO_OFFSET=(\d+)", lies(os.path.join(HIER, "k2_stromausfall.default")), re.M)
    sa_gpio = int(kfg_sa.group(1))
    belegt.setdefault(sa_gpio, []).append("PWR_FAIL_N-Eingang (k2_stromausfall)")
    doppelt = {g: v for g, v in belegt.items() if len(v) > 1}
    check("GPIO: keine Doppelbelegung", not doppelt, str(doppelt))
    check("GPIO: belegte Pins = SOLL-Tabelle", set(belegt) == set(SOLL),
          "zu viel %s, fehlt %s" % (sorted(set(belegt) - set(SOLL)), sorted(set(SOLL) - set(belegt))))
    for gpio in sorted(belegt):
        netz = sp.get(gpio, (None, None))[0]
        if gpio in UNBESCHALTET_ERLAUBT:
            check("Overlay-Pin GPIO%d (%s) unbeschaltet" % (gpio, belegt[gpio][0]), netz is None, str(netz))
        else:
            check("Overlay-Pin GPIO%d (%s) = Schaltplan %s" % (gpio, belegt[gpio][0], netz),
                  netz is not None and netz == SOLL.get(gpio, (None,))[0])
    # Funktion passt zum Netz (TX an TX usw.)
    paare = [(4, "uart2 TX", "UART2_TX"), (5, "uart2 RX", "UART2_RX"), (12, "uart4 TX", "UART4_TX"),
             (13, "uart4 RX", "UART4_RX"), (14, "uart0 TX", "UART0_TX"), (15, "uart0 RX", "UART0_RX"),
             (17, "uart0 RTS", "RS485_DE"), (8, "spi0 CE0", "CAN_CS"), (9, "spi0 MISO", "CAN_MISO"),
             (10, "spi0 MOSI", "CAN_MOSI"), (11, "spi0 SCLK", "CAN_SCK"), (25, "mcp251xfd nINT", "CAN_INT"),
             (2, "i2c1 SDA", "I2C1_SDA"), (3, "i2c1 SCL", "I2C1_SCL"),
             (0, "i2c_csi_dsi SDA", "DSI_SDA"), (1, "i2c_csi_dsi SCL", "DSI_SCL")]
    for gpio, funk, netz in paare:
        check("Funktion GPIO%d %s -> %s" % (gpio, funk, netz),
              any(b.startswith(funk + " ") for b in belegt.get(gpio, [])) and sp.get(gpio, (None,))[0] == netz)
    params = {n: p for a, n, p in eintr if a == "dtoverlay"}
    check("RS485: uart0-pi5 mit rs485 (Kernel-DE über RTS)", params["uart0-pi5"].get("rs485") is True)
    check("RS485: keine invertierte RTS-Polarität (THVD1450 DE aktiv high)",
          "rs485_invert_rts" not in params["uart0-pi5"])
    check("UART2/UART4 ohne ctsrts (würde GPIO6/7 bzw. GPIO14/15 belegen)",
          not params["uart2-pi5"] and not params["uart4-pi5"])
    check("config.txt: force_eeprom_read=0 (kein HAT-EEPROM, ID_SD/SC = Display-I2C)",
          "force_eeprom_read=0" in zeilen)
    check("config.txt: usb_max_current_enable=1", "usb_max_current_enable=1" in zeilen)


def test_gpio6_26(src, werte):
    sp, _ = schaltplan_gpio(werte)
    for g in (6, 26):
        if g in sp:
            hinweis("Schaltplan hat GPIO%d noch beschaltet (%s) – Umbau läuft?" % (g, sp[g][0]))
        check("Schaltplan: GPIO%d frei (Toolhead-eFuse entfallen)" % g, g not in sp, str(sp.get(g)))
    muster = re.compile(r"(GPIO ?(6|26)\b|gpio=?(6|26)\b|interrupt=(6|26)\b|K2SA_GPIO_OFFSET=(6|26)\b|TH_AUS|TH_FLT)")
    treffer = []
    for wurzel, _d, dateien in os.walk(HIER):
        for d in dateien:
            if d.startswith("test_") or d.endswith((".pyc",)) or d == "README-traeger-alpha1.md":
                continue
            p = os.path.join(wurzel, d)
            for i, z in enumerate(lies(p).splitlines(), 1):
                if muster.search(z):
                    treffer.append("%s:%d" % (d, i))
    check("Paket: GPIO6/GPIO26/TH_AUS/TH_FLT nirgends verwendet", not treffer, str(treffer))


def test_can(src, werte):
    eintr = {n: p for a, n, p in config_eintraege(config_txt_zeilen()) if a == "dtoverlay"}
    p = eintr["mcp251xfd"]
    y1 = teil_pins(src, "Y1")
    m = re.search(r'Teil\("Y1",\s*"[^"]+",\s*"(\d+) MHz"', src)
    check("CAN: oscillator = Quarz Y1", m and int(p["oscillator"]) == int(m.group(1)) * 1000000,
          "%s / %s" % (p.get("oscillator"), m and m.group(1)))
    u1 = teil_pins(src, "U1")
    check("CAN: MCP2518FD nINT (Pin 4) = CAN_INT, nCS (13) = CAN_CS, OSC an Y1",
          u1.get("4") == "CAN_INT" and u1.get("13") == "CAN_CS"
          and {u1.get("5"), u1.get("6")} == {y1.get("1"), y1.get("3")}, str(u1))
    check("CAN: SDI(11)=MOSI, SDO(12)=MISO, SCK(10)",
          (u1.get("11"), u1.get("12"), u1.get("10")) == ("CAN_MOSI", "CAN_MISO", "CAN_SCK"))
    u2 = teil_pins(src, "U2")
    check("CAN: SN65HVD230 Rs (Pin 8) an GND = Highspeed", u2.get("8") == "GND", str(u2))
    net = lies(os.path.join(HIER, "can0", "80-k2-can0.network"))
    check("can0: Name=can0, BitRate=1M, RequiredForOnline=no",
          re.search(r"^Name=can0$", net, re.M) and re.search(r"^BitRate=1M$", net, re.M)
          and re.search(r"^RequiredForOnline=no$", net, re.M))
    rules = lies(os.path.join(HIER, "can0", "80-k2-can0.rules"))
    check("can0: udev tx_queue_len 1024", 'KERNEL=="can0"' in rules and 'ATTR{tx_queue_len}="1024"' in rules)


def test_rs485(src, werte):
    u3 = teil_pins(src, "U3")
    check("RS485: THVD1450 R=UART0_RX, ~RE=DE=RS485_DE, D=UART0_TX, A/B",
          (u3.get("1"), u3.get("2"), u3.get("3"), u3.get("4"), u3.get("6"), u3.get("7"))
          == ("UART0_RX", "RS485_DE", "RS485_DE", "UART0_TX", "RS485_A", "RS485_B"), str(u3))
    r13 = widerstand(src, "R13")
    check("RS485: R13 Pull-down auf DE (ohne CM5 nur Empfang)", r13 and r13[1:] == ("RS485_DE", "GND"), str(r13))


def test_ina(src, werte):
    dts = lies(os.path.join(HIER, "k2-traeger-ina226-overlay.dts"))
    knoten = re.findall(r"ina226@([0-9a-f]+)\s*\{(.*?)\};", dts, re.S)
    ov = {}
    for adr, inhalt in knoten:
        reg = int(re.search(r"reg\s*=\s*<(0x[0-9a-f]+)>", inhalt).group(1), 16)
        shunt = int(re.search(r"shunt-resistor\s*=\s*<(\d+)>", inhalt).group(1))
        check("Overlay: Knotenname ina226@%s = reg 0x%02x" % (adr, reg), int(adr, 16) == reg)
        check("Overlay: compatible ti,ina226 (0x%02x)" % reg, 'compatible = "ti,ina226"' in inhalt)
        ov[reg] = shunt
    check("Overlay: genau 0x40 und 0x41", sorted(ov) == [0x40, 0x41], str(ov))
    # Adressen aus A1 (Pin 1) / A0 (Pin 2): GND=0, VS(CM5_3V3)=1 (SDA/SCL hier nicht verwendet)
    wert = {"GND": 0, "CM5_3V3": 1}
    gefunden = {}
    for ref in ("U4", "U5", "U6"):
        p = teil_pins(src, ref)
        if p is None or p.get("6") != "CM5_3V3":
            continue
        gefunden[ref] = 0x40 + 4 * wert[p["1"]] + wert[p["2"]]
    check("Schaltplan: INA226 U4 = 0x40, U5 = 0x41, kein U6/0x44",
          gefunden == {"U4": 0x40, "U5": 0x41}, str(gefunden))
    u4, u5 = teil_pins(src, "U4"), teil_pins(src, "U5")
    check("Schaltplan: U4 ALERT (Pin 3) = PWR_FAIL_N", u4.get("3") == "PWR_FAIL_N")
    sp, _ = schaltplan_gpio(werte)
    check("Schaltplan: PWR_FAIL_N an GPIO27 (k2_stromausfall-Standard)",
          sp.get(27, (None,))[0] == "PWR_FAIL_N")
    r50 = widerstand(src, "R50")
    check("Schaltplan: R50 Pull-up PWR_FAIL_N nach CM5_3V3", r50 and set(r50[1:]) == {"CM5_3V3", "PWR_FAIL_N"}, str(r50))

    def ohm(t):
        return {"5m": 5000, "10m": 10000, "2m": 2000, "20m": 20000}.get(t)
    for ref, u, adr in (("R23", u4, 0x40), ("R24", u5, 0x41)):
        r = widerstand(src, ref)
        an_shunt = set(r[1:]) == {u.get("9"), u.get("10")}
        check("Shunt %s (%s) an IN+/IN- von 0x%02x, Overlay %d µΩ" % (ref, r[0], adr, ov.get(adr, 0)),
              an_shunt and ohm(r[0]) == ov.get(adr), str(r))
    bom = os.path.join(HW, "ausgabe", "stueckliste.csv")
    if os.path.isfile(bom):
        with open(bom, encoding="utf-8") as f:
            zeilen = {z[0]: z for z in csv.reader(f) if z}
        check("Stückliste: R23 5m, R24 10m",
              zeilen.get("R23", [None, None])[1] == "5m" and zeilen.get("R24", [None, None])[1] == "10m")
    sa = lies(os.path.join(HIER, "k2_stromausfall.py"))
    check("k2_stromausfall: Standard 0x40, I2C1, GPIO27, 20 V",
          '"K2SA_INA_ADR": "0x40"' in sa and '"K2SA_I2C_BUS": "1"' in sa
          and '"K2SA_GPIO_NAME": "GPIO27"' in sa and '"K2SA_GRENZE_MV": "20000"' in sa)


def test_dsi(src, werte):
    eintr = {n: p for a, n, p in config_eintraege(config_txt_zeilen()) if a == "dtoverlay"}
    check("DSI: vc4-kms-dsi-7inch ohne dsi0 (= DSI1)", "dsi0" not in eintr["vc4-kms-dsi-7inch"])
    netze = set(werte["CM5_SIGNALE"].values())
    check("DSI: MIPI1-Netze im Schaltplan (Takt, D0, D1)",
          {"DSI1_C_P", "DSI1_C_N", "DSI1_D0_P", "DSI1_D0_N", "DSI1_D1_P", "DSI1_D1_N"} <= netze)
    m = re.search(r"\n\s*j12 = \{(.*?)\}", src, re.S)
    j12 = dict(re.findall(r'"(\d+)":\s*"([^"]*)"', m.group(1))) if m else teil_pins(src, "J12")
    check("DSI: J12 SCL/SDA = DSI_SCL/DSI_SDA (Pin 20/21)",
          j12 and j12.get("20") == "DSI_SCL" and j12.get("21") == "DSI_SDA", str(j12))


def test_klipper(src, werte):
    cfg = cfg_lesen(os.path.join(HIER, "klipper-ausschnitte.cfg"))
    eintr = {n for a, n, p in config_eintraege(config_txt_zeilen()) if a == "dtoverlay"}
    zuordnung = {}
    for t in werte["BR_UART"].values():
        m = re.match(r"B\d+_UART(\d)_(TX|RX)", t)
        zuordnung.setdefault(int(m.group(1)), set()).add(t)
    uart_mcu = dict(re.findall(r'\("U1[0-3]", "R\d+", "R\d+", "UART(\d)_(?:TX|RX)", "B\d+_UART\d_(?:TX|RX)", "(?:tx|rx)", "(\w+)"\)',
                               src))
    check("Generator: UART2 = Düse, UART4 = Main", uart_mcu == {"2": "Düse", "4": "Main"}, str(uart_mcu))
    for abschnitt, n in (("mcu", 4), ("mcu nozzle_mcu", 2)):
        s = cfg.get(abschnitt, {})
        check("Klipper [%s]: serial /dev/ttyAMA%d, 230400" % (abschnitt, n),
              s.get("serial") == "/dev/ttyAMA%d" % n and s.get("baud") == "230400", str(s))
        check("Klipper [%s]: uart%d-pi5 in config.txt" % (abschnitt, n), "uart%d-pi5" % n in eintr)
    k = cfg.get("k2_485", {})
    check("Klipper [k2_485]: /dev/ttyAMA0, 230400, rs485_de: kernel",
          k.get("serial") == "/dev/ttyAMA0" and k.get("baud") == "230400" and k.get("rs485_de") == "kernel", str(k))
    check("Klipper [k2_485]: uart0-pi5 in config.txt", "uart0-pi5" in eintr)
    sys.path.insert(0, PIHOST)
    try:
        import k2_485
        check("k2_485.py kennt rs485_de: kernel", "kernel" in k2_485.RS485_DE_MODI)
    finally:
        sys.path.remove(PIHOST)
    check("Klipper: [mcu toolhead] nur als auskommentierte Vorlage",
          "mcu toolhead" not in cfg
          and re.search(r"^#\[mcu toolhead\]$", lies(os.path.join(HIER, "klipper-ausschnitte.cfg")), re.M)
          and re.search(r"^#canbus_uuid:", lies(os.path.join(HIER, "klipper-ausschnitte.cfg")), re.M))


def editor_aus_install():
    s = lies(os.path.join(HIER, "install-traeger.sh"))
    m = re.search(r"<<'PY'\n(.*?)\nPY\n", s, re.S)
    return m.group(1)


def test_install(src, werte):
    s = lies(os.path.join(HIER, "install-traeger.sh"))
    for f in ("install-traeger.sh", "k2_readonly.sh"):
        r = subprocess.run(["bash", "-n", os.path.join(HIER, f)], stderr=subprocess.PIPE,
                           universal_newlines=True)
        check("bash -n %s" % f, r.returncode == 0, r.stderr)
    r = subprocess.run(["sh", "-n", os.path.join(HIER, "k2_config_sicherung.sh")])
    check("sh -n k2_config_sicherung.sh", r.returncode == 0)
    unit = lies(os.path.join(HIER, "k2-stromausfall.service"))
    check("Dienst: ExecStart = Installationsziel /usr/local/sbin/k2_stromausfall.py",
          "/usr/local/sbin/k2_stromausfall.py" in unit and "/usr/local/sbin/k2_stromausfall.py" in s)
    check("Dienst: vor Klipper/Moonraker, EnvironmentFile /etc/default/k2_stromausfall",
          "Before=klipper.service moonraker.service" in unit and "/etc/default/k2_stromausfall" in unit)
    su = lies(os.path.join(HIER, "k2-config-sicherung.service"))
    check("Sicherung: Dienst ruft installiertes Skript", "/usr/local/sbin/k2_config_sicherung.sh" in su
          and "/usr/local/sbin/k2_config_sicherung.sh" in s)
    check("Sicherung: Timer täglich, Persistent", "OnCalendar=*-*-* 03:30:00" in lies(
        os.path.join(HIER, "k2-config-sicherung.timer")) and "Persistent=true" in lies(
        os.path.join(HIER, "k2-config-sicherung.timer")))
    check("install: --trocken und --readonly vorhanden, readonly nur mit Rückfrage",
          "--trocken)" in s and "--readonly)" in s and "JA eintippen" in s)
    check("install: read-only nicht ohne Schalter",
          re.search(r'if \[ "\$READONLY" = 1 \]; then', s) is not None)
    # config.txt-Editor ausführen
    code = editor_aus_install()
    tmp = tempfile.mkdtemp()
    try:
        cfg = os.path.join(tmp, "config.txt")
        open(cfg, "w").write("dtparam=audio=on\ndtoverlay=vc4-kms-v3d\n[all]\ndtoverlay=uart2-pi5\n"
                             "dtoverlay=uart4-pi5,ctsrts\ndtoverlay=dwc2\n")
        blk = os.path.join(HIER, "config-txt-traeger.txt")

        def lauf(eingabe, ausgabe):
            r = subprocess.run([sys.executable, "-c", code, eingabe, blk, ausgabe],
                               stderr=subprocess.PIPE, universal_newlines=True)
            return r.returncode, r.stderr
        rc, err = lauf(cfg, cfg + ".1")
        t1 = lies(cfg + ".1")
        check("Editor: läuft", rc == 0, err)
        check("Editor: alte UART-Overlays auskommentiert, fremde bleiben",
              "#K2T-alt# dtoverlay=uart2-pi5\n" in t1 and "#K2T-alt# dtoverlay=uart4-pi5,ctsrts\n" in t1
              and "\ndtoverlay=dwc2\n" in t1 and "\ndtoverlay=vc4-kms-v3d\n" in t1)
        check("Editor: Block am Ende", t1.rstrip("\n").endswith(config_txt_zeilen()[-1]))
        rc, err = lauf(cfg + ".1", cfg + ".2")
        check("Editor: zweiter Lauf ändert nichts (idempotent)", lies(cfg + ".2") == t1)
        alt = t1.replace("# >>> K2-TRAEGER-ALPHA1 (install-traeger.sh", "# >>> K2-TRAEGER-ALPHA1 (alte Fassung")
        alt = alt.replace("dtoverlay=uart0-pi5,rs485\n", "dtoverlay=uart0-pi5\n")
        open(cfg + ".3", "w").write(alt)
        rc, err = lauf(cfg + ".3", cfg + ".4")
        t4 = lies(cfg + ".4")
        check("Editor: alter Block wird ersetzt (kein doppelter Block)",
              t4.count("# >>> K2-TRAEGER-ALPHA1") == 1 and "dtoverlay=uart0-pi5,rs485" in t4
              and t4 == t1, "")
    finally:
        shutil.rmtree(tmp)


def test_dts():
    dts = lies(os.path.join(HIER, "k2-traeger-ina226-overlay.dts"))
    check("Overlay: /plugin/ und Ziel i2c_arm", "/plugin/;" in dts and "target = <&i2c_arm>;" in dts)
    ohne_kommentar = re.sub(r"//.*", "", dts)
    check("Overlay: Klammern ausgeglichen", ohne_kommentar.count("{") == ohne_kommentar.count("}"))
    dtc = shutil.which("dtc")
    if dtc:
        tmp = tempfile.mkdtemp()
        try:
            r = subprocess.run([dtc, "-q", "-@", "-I", "dts", "-O", "dtb", "-o",
                                os.path.join(tmp, "x.dtbo"), os.path.join(HIER, "k2-traeger-ina226-overlay.dts")],
                               stderr=subprocess.PIPE, universal_newlines=True)
            check("Overlay: dtc übersetzt", r.returncode == 0, r.stderr)
        finally:
            shutil.rmtree(tmp)
    else:
        print("SKIP dtc nicht installiert – Overlay wird auf dem Pi von install-traeger.sh übersetzt")


def test_regeln():
    verboten = [
        (re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"), "IPv4"),
        # Muster zusammengesetzt, damit diese Datei selbst den Repo-Leak-Grep besteht
        (re.compile("/home/" + "pro" + r"\b"), "Home-Pfad des Autors"),
        (re.compile(r"(?<![\w.])/root\b"), "/root"),
        (re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}"), "E-Mail"),
        (re.compile(r"by-id/usb-(?!…|ANNAHME|<|TEST)"), "by-id-Seriennummer"),
        (re.compile(r"\.local\b"), "Hostname .local"),
        (re.compile("cm5" + "-k2" + r"|K2Plus-[0-9A-F]{4}", re.I), "Hostname"),
        (re.compile(r"usb-FTDI_[A-Za-z0-9]"), "FTDI-Seriennummer"),
    ]
    treffer = []
    for wurzel, _d, dateien in os.walk(HIER):
        if "__pycache__" in wurzel:
            continue
        for d in dateien:
            p = os.path.join(wurzel, d)
            for i, z in enumerate(lies(p).splitlines(), 1):
                for rx, was in verboten:
                    if rx.search(z) and not (d == "test_traeger_config.py" and "re.compile" in z):
                        treffer.append("%s:%d %s" % (d, i, was))
    check("Regeln: keine IPs, Hostnamen, Seriennummern, PC-Home, root-Home, E-Mails", not treffer, str(treffer[:10]))


def main():
    if not os.path.isfile(GEN):
        print("FEHLER: Generator fehlt: %s" % GEN)
        return 2
    src, werte = generator_lesen()
    for fn, args in ((test_schaltplan, (src, werte)), (test_config_txt, (src, werte)),
                     (test_gpio6_26, (src, werte)), (test_can, (src, werte)), (test_rs485, (src, werte)),
                     (test_ina, (src, werte)), (test_dsi, (src, werte)), (test_klipper, (src, werte)),
                     (test_install, (src, werte)), (test_dts, ()), (test_regeln, ())):
        try:
            fn(*args)
        except Exception as exc:
            import traceback
            traceback.print_exc()
            check(fn.__name__ + " (Ausnahme)", False, repr(exc))
    fehler = [n for n, ok in ERG if not ok]
    print("ERGEBNIS: %s (%d/%d Prüfungen ok)" % (
        "ok" if not fehler else "%d FEHLER" % len(fehler), len(ERG) - len(fehler), len(ERG)))
    return 1 if fehler else 0


if __name__ == "__main__":
    raise SystemExit(main())
