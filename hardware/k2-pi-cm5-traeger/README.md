# K2-CM5-Träger Alpha 1 (Weg B) – Layout-Entwurf (23.10.2026, Umbau 24.10.2026)

> **ALPHA – NUR FÜR TESTZWECKE – NICHT FÜR DEN KOMMERZIELLEN EINSATZ – NUTZUNG AUF EIGENE GEFAHR.**
> Lizenz und Haftungsausschluss: siehe [`../LIZENZ-UND-HAFTUNG.md`](../LIZENZ-UND-HAFTUNG.md).

> **Status: Alpha 1 – Layout vollständig geroutet, DRC/ERC 0 Fehler, alle Wächter grün; noch nicht gefertigt und nicht
> am Drucker geprüft.** Vor einer Bestellung die offenen Punkte am Ende abarbeiten (Pinbelegungen der Stecker gegenüber
> den Kabeln, Impedanz-Rechner, 0,2-mm-Bohrungen, Abstandshalter des CM5).

> **Unterlagen:** [Fertigungshinweise](#fertigungshinweise-leiterplatte-und-bestückung) · [`EINKAUFSLISTE.md`](EINKAUFSLISTE.md) (alles außer JLC) · [`AUFBAU-UND-INBETRIEBNAHME.md`](AUFBAU-UND-INBETRIEBNAHME.md)
> (Messungen in 12 Etappen) · [`KOMPROMISSE-UND-OFFENE-PUNKTE.md`](KOMPROMISSE-UND-OFFENE-PUNKTE.md) · Software:
> [`../../pi-host/traeger-alpha1/README-traeger-alpha1.md`](../../pi-host/traeger-alpha1/README-traeger-alpha1.md).

> **Umbau 24.10.2026 (Nutzerwunsch):** 24-V-Eingang **J7 an Kante A** (über J1, Kabel von links wie beim Flachkabel),
> **J9b/J11 stehend** (JST GH Top entry), **tauschbare Eingangssicherung** (Littelfuse 157 mit Nano²-Sicherung),
> **Stromausfall-Erkennung** (INA226-ALERT → GPIO27).

> **Weg A (Entscheidung 24.10.2026):** **J9 (Toolhead 24 V + CAN) entfällt** samt eFuse, INA226 0x44 und dem
> 24-V-Stamm quer über die Platine. Der Toolhead bekommt seine 24 V **direkt vom Drucker-Netzteil** (eigene Kabelsicherung
> ≈ 5 A, siehe „Toolhead-Versorgung“), CAN läuft über **J9b**. Eingangssicherung **F1 jetzt 3 A** (Platine braucht ≈ 10–15 W).
> Die Platzhalter-Quader der 3D-Ansicht sind, wo möglich, durch echte Modelle ersetzt (siehe „3D-Modelle und Lizenzen“).

Konzept und Begründungen: [`KONZEPT.md`](KONZEPT.md) (inkl. Tabelle „Entscheidungen“).

## Zweck

Der Träger ersetzt das T113-Upper-Board des K2 Plus an dessen vier Befestigungspunkten. Er trägt ein **Raspberry Pi
Compute Module 5 (CM5)** direkt (zwei 100-pol. Steckverbinder, 1,5 mm Bauhöhe) – ohne IO-Board. Das Original-Flachkabel
vom Bridge-Board steckt in **J1** (Lage wie CON2 der T113). Der Träger versorgt den CM5 selbst aus den 24 V des Druckers.

## Funktionen

| Block | Umsetzung | Anschluss / CM5-Pins |
|---|---|---|
| Flachkabel J1 | 50-pol. FPC 0,5 mm (XUNPU FPC-05FB-50PH20), Kante A; J1-Pin n = Breakout-Nummer n (CON2 = 51 − Breakout) | Breakout 45–50 = Drucker-5V (**nie** am CM5-5V), 1/2 + 4/5 Ethernet |
| UART2 / UART4 | je 1 kΩ + ESD (SRV05-4) + Ioff-Puffer SN74LVC1G125 (5,5-V-tolerant) | Breakout 26/28 (UART2), 30/32 (UART4); CM5 54/34, 31/28 |
| RS485 | THVD1450 an UART0, DE = GPIO17; Ruhevorspannung (JP2/JP3) und 120 Ω (JP_485_TERM) schaltbar | Breakout 19 = B, 20 = A; CM5 55/51/50 |
| RS485-Abgriff J11 | JST GH 3-pol. **stehend** (BM03B-GHS-TBT, LCSC C161691), A/B/GND parallel zum Bus, ESD | Kante D |
| CAN | MCP2518FD (SPI0, INT = GPIO25) + SN65HVD230, ESD NUP2105 | CM5 44/40/38/39/41 |
| **CAN-Anschluss J9b** | JST GH 3-pol. **stehend** (BM03B-GHS-TBT, LCSC C161691): CANH, CANL, GND (kein 24 V) – seit Weg A **der** CAN-Anschluss für ein späteres Toolhead-Board (24 V dafür direkt vom Netzteil) | **Kante B** (zwischen L-Spule und L2) |
| CAN-Abschluss | 120 Ω über Stiftleiste **JP_CAN_TERM** („CAN 120R“), Jumper **ab Werk gesteckt** | Kante D unten rechts |
| 24-V-Eingang J7 | XT30PW-M liegend (Stecköffnung nach links), **tauschbare Sicherung F1** (Littelfuse 157, **3 A**), TVS D8, Shunt R23 5 mΩ + INA226 0x40 direkt dahinter (unten, unter J7), 24-V-Stamm auf L3 nur noch bis zum Wandler | **Kante A oben** (über J1) |
| Stromausfall-Erkennung | INA226 0x40 ALERT (open drain) → **PWR_FAIL_N** → **GPIO27 (CM5-Pin 48)**, 10 k Pull-up R50, Messpad TP12 | siehe unten |
| 5,2 V für den CM5 | LMR51450 (5 A, Spule FXL0630) → Power-Mux TPS2121 → Shunt 10 mΩ (INA226 0x41) → CM5 77–87 | – |
| Notfall-USB-C J10 | stehend, Kante B bei L1: USB-OTG (CM5 103/105), CC 94/96 (5,1 k als Bestückoption), nRPIBOOT per Jumper **JP_BOOT**, 5 V über TPS2121, ESD USBLC6 | USB_OTG_ID 101 offen |
| Kamera-/Hub-USB | 90 Ω, CM5 134/136 bzw. 163/165 → J1 37/38 bzw. 34/35, ESD | – |
| Ethernet | CM5-Paar 0 (12/10) und 1 (4/6) → eigener Übertrager H1102NL → J1 1/2 und 4/5, Mittelanzapfung/Bob-Smith | – |
| **DSI-Display J12** | **bestückt**: 22-pol. FPC 0,5 mm Hirose FH12-22S-0.5SH(55), Belegung wie Raspberry Pi 5 / CM-IO; **MIPI1 = DSI1**, 2 Datenlanes + Takt | **Kante C** (wie CON1 der T113) |
| DSI-3,3 V | eigener Abwärtswandler TLV62569 (2 A) aus CM5-5V, EN an CM5-3,3V | J12 Pin 22 |
| LEDs | 3V3, nACT (21), Ethernet nLED3/nLED2 (15/17), D10 „Flachkabel richtig“ (Drucker-5V) | Kante D |
| Messpunkte | 5V, 3V3, RUN (99), BOOT (93), PWR (92), GND; unten 24 V, BR_5V, PWR_FAIL (TP12) | Kante D bzw. unten |
| Sonstiges | GPIO_VREF (78) an 3,3 V, EEPROM_nWP (20) offen; **GPIO6 (30) und GPIO26 (24) frei** (seit Weg A, im Schaltplan als „frei“ vermerkt) | – |

## DSI-Display (Nachtrag 2/3)

- Ziel-Display: **Waveshare 4.3inch DSI LCD** (800 × 480, Touch Goodix über I2C im Kabel, 3,3 V ≈ 1,2 W, Kabel 22 → 15 pol.).
  Für Pi 5/CM5 ist **Rev 2.2** des Displays nötig (Tabelle „Entscheidungen“ in `KONZEPT.md`).
- Stecker **J12 an Kante C**, senkrecht entlang der Kante zwischen den Bohrungen L2 und L4, Öffnung zur Kante, Kabel geht
  nach außen ab – dort, wo an der T113 der Display-Stecker **CON1** sitzt (am 23.10.2026 bestätigt).
- Kontaktseite und Pin 1 wie Raspberry Pi 5 bzw. CM-IO-Board: Es ist **derselbe Stecker (Hirose FH12-22S-0.5SH)** wie auf dem
  CM4-IO-Board (KiCad-Designdaten von Raspberry Pi), Pin 1 liegt am südlichen Ende (Richtung L4). Ein Pi-5-Displaykabel passt
  ohne Verdrehen.
- Belegung (wie Pi 5 / CM-IO, 22-pol.):

| Pin | Signal | CM5-Pin | Pin | Signal | CM5-Pin |
|---|---|---|---|---|---|
| 1 | GND | – | 12 | D2_P | nicht verlegt (offen) |
| 2 | D0_N | 175 (MIPI1_D0_N) | 13 | GND | – |
| 3 | D0_P | 177 | 14 | D3_N | nicht verlegt (offen) |
| 4 | GND | – | 15 | D3_P | nicht verlegt (offen) |
| 5 | D1_N | 181 | 16 | GND | – |
| 6 | D1_P | 183 | 17 | CAM_GPIO0 | 97 |
| 7 | GND | – | 18 | CAM_GPIO1 | 100 |
| 8 | C_N | 187 (MIPI1_C_N) | 19 | GND | – |
| 9 | C_P | 189 | 20 | SCL | 35 (ID_SC), 2,2 k nach 3,3 V |
| 10 | GND | – | 21 | SDA | 36 (ID_SD), 2,2 k nach 3,3 V |
| 11 | D2_N | nicht verlegt (offen) | 22 | 3,3 V | DSI-Wandler U14 |

- Port-Wahl: **MIPI1 (DSI1)**, weil seine Pins (175–189) in Reihe B direkt zur rechten Kante zeigen; die I2C von MIPI1 liegt
  laut CM5-Datenblatt auf **ID_SD/ID_SC (Pin 36/35)**. Software: `dtoverlay=vc4-kms-dsi-7inch` (= DSI1). Für DSI0 wäre es
  `dtoverlay=vc4-kms-dsi-7inch,dsi0` – auf diesem Träger **nicht** verdrahtet.
- Paare: Takt, D0, D1 als **100-Ω-Paare 0,20/0,20 mm** nur auf L1 über der GND-Lage, ohne Via; Länge je Leitung
  **49,870 mm**, Abweichung im Paar und zwischen den Lanes < 0,001 mm (verschachtelte Paarbeulen + Innenabgleich).
  D2/D3 sind nicht verlegt – das Waveshare-Kabel (15-pol. Ende) führt ohnehin nur 2 Lanes.
- **3,3-V-Budget:** eigener Abwärtswandler **TLV62569** (2 A, 1,5 MHz, 2,2 µH, 2 × 22 µF) aus CM5-5V; Bedarf des Displays
  ≈ 1,2 W ≈ 0,36 A ≥ 0,5 A Vorgabe ✓ (2 A Reserve). Er belastet die 5,2-V-Schiene mit ≈ 0,3 A. Die 3,3-V-Schiene des CM5
  wird nicht belastet (sie schaltet nur EN, damit das Display nicht in einen ausgeschalteten CM5 zurückspeist).

## Mechanik und Lage

Draufsicht von oben, Ursprung linke obere Ecke, x nach rechts, y nach unten, Maße in mm.

- Umriss **90,0 × 56,1** (x −10…80), Ecken R 2. Bohrungen NPTH Ø 3,5: L1 (8,80 | 4,15), L2 (76,90 | 4,21),
  L3 (3,01 | 53,49), L4 (63,41 | 53,41), Freihaltekreis Ø 6,5 auf allen Lagen; Schraubenkopf ≤ 5,5 mm.
- CM5 (55 × 40) bei x 8,9…63,9, y 9,5…49,5; Halter-Freihaltung oben Ø 5,0, Schraubenkopf unten Ø 5,5.
- **Kanten (Stand 24.10.2026):** A (links) **J7 XT30 oben** (x −10,1 … 5,8, y 2,3 … 16,6; Stecköffnung nach links,
  Pin 2 = +24 V unten, Pin 1 = GND oben) und J1 darunter (Pin 1 unten) · B (oben) J10 USB-C, JP_BOOT, **J9b stehend (CAN)** ·
  C (rechts) **J12 DSI Mitte** (J9 entfallen, Weg A) · D (unten) **J11 stehend**, JP_485_TERM, LEDs, Messpunkte, JP_CAN_TERM.
- Zwischen J7 und J1 sitzen oben die tauschbare Sicherung **F1** und die LED **D10**; L1 (Kopf Ø 5,5) und die
  Einsteckzone des Flachkabels (J1, y 17,8 … 47,9) bleiben frei. Unten unter dem J7-Körper sitzen seit Weg A der
  Eingangs-Shunt **R23** (östlich der J7-Stifte), der INA226 **U4** (0x40) mit C7/R20, Pull-up R50 und die Messpads TP10/TP12.

## Höhenbilanz

Budget: **≤ 11,5 mm über der Auflage-Ebene**, Platine 1,6 mm, oben ≤ 9,9 mm über der Platine, Notfall-USB-C J10 ≤ 9,0 mm
gesamt, unten ≤ 2,0 mm (gemessene Luft 2,45 mm). Geprüft vom Höhen-Wächter (`ausgabe/hoehen.txt`) mit den
**Datenblatt-Höhen**; die 3D-Modelle dienen nur der Darstellung (Spalte „3D(Info)“, seit 24.10.2026 nicht mehr gewertet).

| Teil | Höhe über Platine | über der Auflage | Budget |
|---|---|---|---|
| CM5 montiert (1,5-mm-DF40 + Modul) | 4,94 | 6,54 | Kühlkörper inkl. Wärmeleitpad **≤ 4,96 mm** → gesamt 11,5 |
| **J9b / J11 JST GH stehend – gesteckt** (Header 4,2 + Stecker GHR-03V-S, 7,3 mm laut eGH.pdf + Kabelbogen ≈ 4,7) | **≈ 12,0** | **≈ 13,6** | **> 11,5 – NUR STECKEN, WENN DARÜBER PLATZ IST** |
| J9b / J11 gesteckt ohne Kabelbogen (Stecker allein) | 7,3 | 8,9 | ✓ |
| Stiftleisten JP_BOOT / JP_CAN_TERM / JP_485_TERM (stehend, **höchste Teile** seit J9 entfallen ist) | 8,50 | 10,10 | ≤ 11,5 ✓ |
| J10 USB-C stehend | 6,80 | 8,40 | ≤ 9,0 ✓ |
| T1 Übertrager H1102NL | 6,35 | 7,95 | ✓ |
| J7 XT30 liegend | 5,00 | 6,60 | ✓ |
| J9b / J11 JST GH stehend (Header ungesteckt) | 4,20 | 5,80 | ✓ |
| F1 Sicherungshalter Littelfuse 157 mit Sicherung | 3,90 | 5,50 | ✓ |
| L1 Spule FXL0630 | 3,00 | 4,60 | ✓ |
| J1 FPC 50-pol., **J12 DSI FH12** | 2,00 | 3,60 | ✓ |
| Unterseite: höchstes SMD C10 (1206) | 1,80 | – | ≤ 2,0 ✓ |
| Unterseite: THT-Überstand J7 | 0,40 | – | ≤ 2,0 ✓ (bündig kürzen) |

**Höhe J9b/J11:** Die stehenden JST-GH-Stecker selbst bleiben im Budget, das senkrecht eingesteckte Kabel aber nicht:
Header 4,2 mm, gesteckter Stecker 7,3 mm (JST eGH.pdf, „Assembly layout“, Referenzmaß), dazu gerader Kabelaustritt
≈ 1 mm und Biegeradius 2–3 mm (AWG 26–30, Schätzung) → **≈ 12 mm über der Platine = ≈ 13,6 mm über der Auflage
(Budget 11,5 mm)**. **Nur stecken, wenn darüber Platz ist** (Gehäusedeckel/Display), sonst Kabel direkt am Stecker
umlegen (Zug auf dem Crimp vermeiden). Der Höhen-Wächter meldet das als Hinweis (`ausgabe/hoehen.txt`).

Keine Oberseiten-Bauteile unter dem CM5 (Lage-Wächter).

## Lagenaufbau und Impedanzen

| Lage | Nutzung |
|---|---|
| L1 (oben) | CM5-Steckverbinder, feste Paare (USB, Ethernet, DSI), Signale |
| L2 (In1) | durchgehende GND-Fläche (Bezug der Paare) |
| L3 (In2) | **3,3-V-Fläche des CM5** über die ganze Platine; darin feste 5-V-Bahnen (Wandler-Stamm 5,2 V 2 mm, CM5-5V-Anbindung der Stütz-C, J10-VBUS) und der **24-V-Stamm VIN24** (1,6–2,0 mm) vom Shunt R23 (Kante A) entlang Kante B **nur bis zum Wandler U7** (Weg A: der Abschnitt zur rechten Ecke ist entfallen) |
| L4 (unten) | Kleinsignale, Unterseiten-Bauteile, GND-/Leistungsflächen |

Freerouting verlegt nur auf L1/L4; GND (In1) und CM5_3V3 (In2) werden über eigene Anschluss-Vias an jedem Pad an die
Flächen gebunden (vor dem Routing gesetzt). Die 5-V-/24-V-Ströme tragen Kupferflächen entlang der Leistungsbahnen
(L1/L4) und die 2-mm-Stämme auf L3.

JLC04161H-7628 (1,6 mm). Richtwerte (Näherung IPC-2141, h = 0,21 mm, εr 4,4 – **mit dem JLC-Impedanzrechner bestätigen**):
USB 90 Ω = 0,25/0,15 mm, Ethernet und DSI 100 Ω = 0,20/0,20 mm. Längenabgleich im Paar ≤ 0,15 mm.

## 24-V-Eingang und tauschbare Sicherung (24.10.2026, Weg A)

- **Strompfad:** J7 (XT30, Kante A) → F.Cu-Bahn 2,2 mm → **F1** (tauschbar, 3 A) → 6 Vias 0,8/0,4 → unten TVS **D8** und
  Shunt **R23** 5 mΩ (senkrecht östlich der J7-Stifte; INA226 **U4** 0x40 daneben unter dem J7-Körper) → R23 Pad 2 (VIN24)
  → 3 Vias → **Stamm auf L3** (1,6–2,0 mm; östlich an J7 vorbei, unter H1/J10, entlang Kante B) → 2 Vias → C9/U7
  (Wandler 24 → 5,2 V). Das ist der einzige 24-V-Verbraucher der Platine.
- **Weg A:** Der frühere Abschnitt des Stamms bis zur rechten Ecke (Shunt + eFuse + J9) ist entfallen, ebenso der
  **Engpass „Toolhead-Strom über L3“** (bis ≈ 6,5 A auf einer Innenlage): Über die Platine fließen jetzt nur noch
  ≈ 0,4–0,6 A (10–15 W), im ungünstigsten Fall ≈ 1,3 A (CM5 5,2 V × 5 A Mux-Grenze + DSI 1,2 W, Wirkungsgrad 0,9).
  Dafür reichen die vorhandenen Bahnen und Flächen bei Weitem.
- **Sicherung F1 = Littelfuse 157 Series 0157003.DR** (LCSC **C187520**, JLC Extended, Bestand zuletzt ≈ 1 500): Nano²-
  Clips **mit** eingesetzter **3-A-Sicherung der Serie 453** (sehr flink, 125 V, Abschaltvermögen 50 A bei 125 V),
  gleiche Bauform wie bisher (6,50 × 3,70 mm, Höhe 3,9 mm) – Footprint `k2_traeger:Sicherungshalter_Littelfuse_157_Nano2_2410`
  unverändert. **Den Halter gibt es nur mit flinker Sicherung** (eine träge 157-Variante existiert nicht); 3,15 A gibt es in
  der Nano²-Reihe nicht, nächster Wert ist 3 A.
- **Begründung des Nennwerts (flink, 3 A):**
  - Dauerlast: Littelfuse empfiehlt ≤ 75 % des Nennstroms → 3 A × 0,75 = **2,25 A ≥ 1,3 A** (ungünstigster Fall) bzw.
    ≈ 4–5-fache Reserve zum Normalbetrieb (0,6 A).
  - **Einschaltstrom:** Eingangskondensatoren C9/C10 2 × 10 µF/50 V X7R (1206; bei 24 V wegen DC-Bias effektiv ≈ 2 × 4–5 µF)
    + C11 100 nF. Beim Einstecken unter Spannung lädt das Netzteil diese nur über Kabel, Stecker, F1 und R23
    (R ≈ 0,05–0,1 Ω, Kabelinduktivität ≈ 1 µH/m): Spitze einige 10 A für wenige µs, Energie-Integral
    **I²t = C·U²/(2R) ≈ 20 µF · (24 V)² / (2 · 0,1 Ω) ≈ 0,06 A²s** (mit Nenn-C, real ≈ 0,03 A²s). Der LMR51450 startet mit
    internem Softstart; die ≈ 160 µF hinter dem Wandler (5,2 V) laden dabei langsam – Eingangsstrom < 0,2 A, für die
    Sicherung unerheblich. Das Schmelz-I²t einer 3-A-Nano²-Sicherung liegt in der Größenordnung einiger Zehntel A²s
    (**vor der Bestellung im Littelfuse-Datenblatt 453 bestätigen**); die Einschaltspitze nutzt damit nur einen kleinen
    Bruchteil (Littelfuse-Faustregel für ≈ 100 000 Schaltspiele: Puls-I²t ≤ ≈ 22 % des Schmelz-I²t).
  - Kleiner (2 A) wäre bei Volllast des CM5 knapp (75 % = 1,5 A), größer (4–5 A) schützt die schmalen Bahnen hinter dem
    Shunt schlechter. → **3 A flink passt**; wer trotzdem träge will (z. B. häufiges Stecken unter Spannung), steckt die
    träge Ersatzsicherung in denselben Halter.
- **Ersatzsicherung (Einkaufsliste, nicht für die Bestückung):** **träge** Littelfuse **0452003.MRL** (Nano² 2410 Slo-Blo,
  3 A, 125 V, LCSC **C99550**) – passt in die 157-Clips; gleichwertig zur Bestückung: 0453003.MR (3 A sehr flink, C178991).
  JLC lötet Teile nur auf Pads und steckt keine Sicherung in den Halter – deshalb wird der Halter **mit** Sicherung bestückt,
  die Einzelsicherung kauft man als Ersatzteil dazu.
- **Verpolung:** Kurzschlussstrom über die TVS D8 (Durchlassrichtung) zwangsläufig durch F1 – eine Sicherung genügt
  (F2 entfiel schon am 24.10.2026). Fehlt die GND-Ader am J7, begrenzt weiterhin F3 (PTC 0,5 A in den GND-Adern des
  Flachkabels).

## Toolhead-Versorgung (Weg A, nur Doku – nicht auf der Platine)

- Ein späteres CAN-Toolhead-Board bekommt seine **24 V direkt vom Drucker-Netzteil** (freie Klemme bzw. Y-Abzweig), **nicht**
  über den Träger. Im Kabel zum Toolhead eine **eigene Sicherung ≈ 5 A** (z. B. fliegender Halter mit 5-A-Flachsicherung
  oder 5 × 20 mm träge), Leitung 18 AWG.
- **CAN über J9b** (JST GH stehend: CANH, CANL, GND). Die GND-Ader von J9b ist der Bezug für CAN; die Toolhead-Masse ist über
  das Netzteil-Minus ohnehin mit dem Träger verbunden. Abschluss 120 Ω über **JP_CAN_TERM** (ab Werk gesteckt, Träger = Busende).
- Transceiver (SN65HVD230), Controller (MCP2518FD) und ESD (NUP2105L) bleiben unverändert.

## Stromausfall-Erkennung (PWR_FAIL)

- Der CM5 hängt an den 24 V des Druckers; wird der Drucker ausgeschaltet, ist der CM5 sofort stromlos. Der **ALERT-Pin
  des INA226 0x40** (misst den 24-V-Eingang) liegt als **PWR_FAIL_N** (aktiv low, open drain, Pull-up **R50 10 kΩ** auf
  3,3 V, Messpad **TP12 „PWR_FAIL“** unten) an **GPIO27 = CM5-Pin 48** (vorher frei, vom CM5-Pin-Wächter geprüft).
- Software: im INA226 die **Bus-Unterspannungsgrenze** setzen, z. B. 20 V: Mask/Enable-Register 0x06 = 0x1000 (BUL,
  ALERT aktiv low; Achtung: der Linux-Treiber ina2xx schaltet beim Laden das ALERT-Latch EIN – die Software k2_stromausfall berücksichtigt das), Alert-Limit-Register 0x07 = 20 V / 1,25 mV = **16000 (0x3E80)**; GPIO27 als Eingang
  mit fallender Flanke (z. B. `gpio-keys` oder ein kleiner Dienst) → `sync` und Dateisysteme `remount,ro`.
- Pufferkondensatoren/Supercaps sind dafür nicht nötig (Hinweis zur Supercap-Option unter „Offene Punkte“).

## Jumper (ab Werk)

| Jumper | Funktion | ab Werk |
|---|---|---|
| JP_BOOT (Stiftleiste) | nRPIBOOT – Jumper stecken = CM5 startet in rpiboot (USB-Boot über J10) | **offen** (kein Jumper) |
| JP_CAN_TERM (Stiftleiste) | CAN-Abschluss 120 Ω | **gesteckt** |
| JP_485_TERM (Stiftleiste) | RS485-Abschluss 120 Ω | **offen** |
| JP2/JP3 (Lötbrücke) | RS485-Ruhevorspannung A/B | geschlossen |
| JP5 (Lötbrücke) | 5-V-Notversorgung über USB-C in den Power-Mux | geschlossen |
| JP6/JP7 (Lötbrücke) | CC1/CC2 der Buchse an den CM5 (R43/R44 5,1 k nur bestücken, wenn offen) | geschlossen |

## Spiegelfall (Flachkabel verdreht gesteckt)

Siehe `ausgabe/spiegelfall.txt` (Kontakt n → 51 − n, je Pin bewertet). Kurz: Drucker-5V landet nie am CM5; Ethernet ist
durch den Übertrager getrennt; UART über 1 kΩ + Ioff-Puffer; RS485 bus-tolerant; die rote LED D10 bleibt aus.
**Offen:** USB-Leitungen bekommen im Spiegelfall ein 5-V-Signal/GND – USB 2.0 fordert Kurzschlussfestigkeit (V), am
Muster prüfen.

## Prüfungen

Stand des freigegebenen Laufs nach Weg A 24.10.2026 (`./bauen.sh`, Netzreihenfolge **14**; vorher 136 bzw. 21):

| Prüfung | Ergebnis |
|---|---|
| ERC (Schaltplan) | **0 Fehler, 0 Warnungen** |
| Netzlisten-Wächter | OK – 101 Netze, 137 Bauteile; Drucker-5V nie am CM5, CM5-5V nur über Mux/Shunt, **24 V nur an J7 (kein 24-V-Ausgang, keine TH_-Netze, J9/U9/U6 entfallen)**, kein 24 V an J1/J2/J9b/J10/J11/J12/Jumpern, 24-V-Eingang nur über F1 (J7 Pin 2 = +24 V, Pin 1 = GND), PWR_FAIL_N nur U4-ALERT/R50/TP12/GPIO27, **GPIO6/GPIO26 frei**, CAN nur U2/D6/R19/JP_CAN_TERM/J9b, USB/Ethernet/DSI nur auf ihren Wegen, INA226 0x40/0x41, Spiegelfall-Tabelle |
| CM5-Pin-Wächter | OK – 200 Pins gegen Datenblatt, 103 belegt (GND 51, 5 V 6, 3V3/VREF 3; Pin 24 GPIO26 und Pin 30 GPIO6 jetzt offen), `ausgabe/cm5-pins.txt` |
| Autorouting | Freerouting 2.5.0, **0 unverlegte Verbindungen** |
| DRC inkl. Schaltplan-Parität | **0 Fehler**, 0 unverbunden, Parität 0 (12 Warnungen: 11 Vias ohne zweite Anbindung = Stitching/Fanout-Reste, 1 „Footprint weicht von der Bibliothek ab“ = R23, dessen Siebdruckstriche wegen des J7-Pads auf die Fab-Lage verschoben sind) |
| Lage-Wächter | OK – Bohrungen L1–L4 auf Sollmaß, keine Oberseiten-Bauteile unter dem CM5, Stecker an ihren Kanten (**J1 und J7 A**, J9b (stehend)/J10 B, J12 C, J11 (stehend) D); J7 ↔ J1 Courtyard-Lücke 1,16 mm, J7-Stecköffnung zur Kante A |
| Höhen-Wächter | OK (Datenblatt-Höhen) – höchste Teile die Stiftleisten JP_BOOT/JP_CAN_TERM/JP_485_TERM 10,10 mm über der Auflage (≤ 11,5), J10 8,40 mm (≤ 9,0), unten ≤ 1,80 mm (≤ 2,0), THT-Überstand J7 0,40 mm; **Hinweis J9b/J11 gesteckt ≈ 13,6 mm über der Auflage – nur stecken, wenn darüber Platz ist** |
| Paar-Wächter | OK – alle Paare Δ = 0,000 mm (USB CAM/HUB/OTG, Ethernet ×4, DSI Takt/D0/D1), DSI-Lanes gegen Takt Δ 0,000 mm |
| Siebdruck-Wächter | OK – 47 Texte ≥ 0,8 mm, Abstände ≥ 0,3 mm (Text–Text) / ≥ 0,15 mm (Text–Lötstopp), ALPHA-Warnung oben und unten |

## Ausgaben (`ausgabe/`)

| Datei | Inhalt |
|---|---|
| `k2-cm5-traeger_gerber_jlcpcb.zip`, `gerber/` | Gerber (4 Lagen, Siebdruck, Maske, Paste, Umriss) + Bohrdaten (Excellon, PTH/NPTH getrennt) |
| `jlcpcb_bom.csv`, `jlcpcb_cpl.csv` | JLCPCB-Stückliste und Bestückungsdaten (beide Seiten, CM5-Steckverbinder J2 als J2A/J2B) |
| `stueckliste.csv` | vollständige Stückliste mit Bemerkungen |
| `schaltplan.pdf`, `platine_lagen.pdf` | Schaltplan (A0) und Lagenbilder |
| `render_oben.png`, `render_unten.png`, `render_schraeg.png` | 3D-Bilder mit echten 3D-Modellen (Stecker beschriftet: J1, J7 +24V/GND (Kante A), F1, J9b CAN „H L G“, J10 USB-C Notfall, J11 RS485 „G B A“, J12 DSI (Display), Jumper) |
| `erc-bericht.txt`, `drc-bericht.txt`, `drc-zusammenfassung.txt` | ERC/DRC-Berichte |
| `cm5-pins.txt`, `spiegelfall.txt`, `lage.txt`, `hoehen.txt`, `paare.txt`, `siebdruck.txt` | Wächter-Ergebnisse |

## Fertigungshinweise (Leiterplatte und Bestückung)

Die Fertigungsdaten in `ausgabe/` sind auf JLCPCB zugeschnitten (Gerber-Paket, `jlcpcb_bom.csv`, `jlcpcb_cpl.csv`).
Andere Hersteller gehen ebenso, dann aber Lagenaufbau, Impedanzen und Teilenummern selbst abgleichen.

**Leiterplatte** – die Einstellungen, auf die es ankommt (alles Übrige auf dem Standard lassen):

| Einstellung | Wert | Warum |
|---|---|---|
| Lagen / Dicke | **4 Lagen, 1,6 mm** | Höhenbilanz und Impedanzen sind mit 1,6 mm gerechnet |
| Lagenaufbau | **JLC04161H-7628** (L1 = F_Cu, L2 = In1_Cu = GND, L3 = In2_Cu, L4 = B_Cu) | Prepreg L1–L2 0,2104 mm, εr 4,4 – darauf beruhen die Bahnbreiten der Leitungspaare |
| Impedanzkontrolle | **ja, ± 10 %** | Ziele im Bemerkungsfeld angeben (Text unten) |
| Oberfläche | **ENIG** (nicht HASL) | 0,4-mm-Raster der CM5-Stecker (DF40); HASL ist für so feine Raster ungeeignet |
| Kupfer außen / innen | 1 oz / 0,5 oz | Rechnung mit 35 µm außen |
| Vias | **abgedeckt (tented)** | Vias unter dem Sicherungshalter F1 |
| kleinste Bohrung | **Standardoption** | die zwölf 0,2-mm-Bohrungen haben 0,5–0,6 mm Durchmesser – laut JLC-Preisregel kein Aufpreis |
| Liefern als | Einzelplatine | Fertigungsränder für die Bestückung legt der Hersteller selbst an |

Bemerkung zur Impedanz (englisch, ins Bemerkungsfeld der Leiterplatte):

```
Impedance control, stackup JLC04161H-7628. Differential pairs on L1, reference plane L2 (GND).
USB pairs (CAM_DP/DM, HUB_DP/DM, OTG_DP/DM): 0.25 mm width / 0.15 mm gap, target 90 ohm differential.
Ethernet pairs (ETH0, ETH1, ETH_A, ETH_B) and DSI pairs (DSI1_C, DSI1_D0, DSI1_D1): 0.20 mm / 0.20 mm,
target 100 ohm differential. Your calculator gives 88.7 / 103.5 ohm, i.e. within +-10 %:
please keep the geometry unless outside tolerance. Via drills 0.2 mm have 0.5/0.6 mm pads.
```

**Bestückung:**

| Einstellung | Wert | Warum |
|---|---|---|
| Art | **Standard, beide Seiten** | Bauteile oben (25) und unten (85); die einfache Variante bestückt nur eine Seite |
| Ränder / Passermarken | vom Hersteller | die kurze Seite (56,1 mm) ist zu schmal für die Standard-Bestückung ohne Rand |
| Bestückungsbild zur Freigabe | **ja** | nur so lassen sich Drehung und Polung vor der Fertigung prüfen (Liste unten) |
| Ränder entfernen | ja | die kleinen Stege an der Kante danach mit einer Feile glätten |
| J7 (XT30, Durchsteckteil) | steht schon in BOM und CPL (Handlöten durch den Hersteller) | wer J7 selbst löten will: Zeile in **Kopien** von BOM und CPL löschen, Teil C431092 separat besorgen |

Bemerkung zur Bestückung (englisch):

```
Connectors J1, J7 (left edge) and J11 (bottom edge) overhang the board outline by up to 0.3 mm,
J9b/J10/J12 sit at the edge. Please use routed gap + mouse bites for the edge rails (no V-cut)
and keep the connector bodies free. J7 (XT30, THT) is hand-soldered as listed in the BOM.
```

**BOM und CPL:** Jede Zeile muss genau die LCSC-Nummer aus der Datei bekommen; vorgeschlagene Ersatzteile ablehnen,
außer es ist dieselbe Herstellernummer. R43/R44 fehlen absichtlich (Bestückoption), Messpads, Bohrungen, Lötbrücken
und Passermarken sind keine Bauteile. Passen BOM und CPL nicht zusammen: mit `./bauen.sh` neu erzeugen, nicht von Hand
flicken.

**Vorschau prüfen:** Die Drehkorrekturen für JLC sind in `jlcpcb_cpl.csv` schon eingerechnet (Tabelle `JLC_DREHUNG`
im Generator). Trotzdem in der Vorschau und im Bestückungsbild nachsehen, dass Pin 1 bzw. die Kathodenmarke auf dem
markierten Pad liegt – nur nach Sicht korrigieren, nie blind. Besonders wichtig:

| Bauteil | worauf achten (Draufsicht von oben, Kante A links) |
|---|---|
| **D8** SMFJ28A (unten) | Kathodenstrich **links zur Kante A** – verdreht schließt D8 die 24 V kurz; JLC hat für D8 kein 3D-Modell, also im Bestückungsbild prüfen |
| D15 SMF5.0A (unten) | Kathodenstrich links |
| U8 TPS2121 (unten) | Pin 1 oben links; passt wegen der ungleichen Pads nur in einer Lage |
| U1, U2–U5, U7, U10–U14 (unten) | Pin 1 laut Siebdruck; SOIC/MSOP/WSON oft um 90°/270°, SOT-23-5 um 180° vertauscht |
| J2A/J2B DF40 (oben) | Längsachse waagerecht, beide Reihen auf den Pads |
| J1, J12 (oben) | Öffnung von J1 zur Kante A, von J12 zur Kante C |
| J7 XT30 (oben) | Stecköffnung nach links über Kante A, Stifte in den zwei großen Bohrungen |
| LEDs D10–D14, Y1, T1 | Kathode bzw. Pin 1 laut Siebdruck |

**Teil nicht lagernd:** Bestand kurz vor dem Bezahlen prüfen. Fehlt ein Teil: beim Hersteller vorbestellen oder von
ihm beschaffen lassen (dauert länger). Ersatz nur bei gleicher Bauform **und** Belegung; für Steckverbinder (J1, J2,
J12), U7, U8 und T1 kein Ersatz ohne neue Prüfung. Leicht lötbare Teile (U1, Y1, F1, L2, D8, R24) kann man auch
unbestückt lassen und selbst löten, J2 (DF40) und U7/U8 nicht.

## Kosten (grob)

Grobe Schätzung (Preisstand 2026, nicht tagesaktuell; nur Größenordnung):

| Posten | Betrag |
|---|---|
| Bauteile je Platine (LCSC, inkl. 2 × DF40, MCP2518FD, 2 × INA226, TPS2121, LMR51450, H1102, FH12, Stecker; seit Weg A ohne eFuse, 3. INA226, Micro-Fit, TVS – 15 Teile weniger) | ≈ 21 € |
| Leiterplatte 4-lagig JLC04161H-7628, 90 × 56 mm, 5 Stück (Impedanzkontrolle) | ≈ 20–35 € |
| Bestückung beidseitig (2 Platinen), Einrichtung + „Extended Parts“ (≈ 37 Sorten à ≈ 3 $) | ≈ 115–160 € |
| THT-Teil XT30 J7 – Handlötung oder JLC-THT-Aufpreis | ≈ 0–10 € |
| Ersatzsicherungen 0452003.MRL (3 A träge, 2410, LCSC C99550), z. B. 5 Stück – Einkaufsliste, nicht Bestückung | ≈ 1–2 € |
| Versand + Einfuhrumsatzsteuer | ≈ 30–50 € |
| **Summe für 2 bestückte Platinen** | **≈ 205–300 €** |

Dazu Kühlkörper/Wärmeleitpad (≤ 4,96 mm inkl. Pad, s. EINKAUFSLISTE), Distanzscheiben M2,5 × 1,5 mm (Ø ≤ 4,5 mm), Kabel (XT30, JST-GH-CAN-Kabel, 22→15-pol. DSI-Kabel) ≈ 20–40 €;
für ein späteres Toolhead-Board zusätzlich die 24-V-Leitung vom Netzteil mit Kabelsicherung ≈ 5 A (≈ 5 €).
Die „Extended Parts“-Gebühren dominieren; bei mehr Platinen sinkt der Stückpreis deutlich.

## Neu erzeugen

```
./bauen.sh            # Generator -> ERC -> Netzliste/Wächter -> Platine -> Freerouting -> DRC -> Wächter -> Fertigungsdaten
```

Freerouting läuft mit einem Thread und ist je Netzreihenfolge wiederholbar; `./bauen.sh` nutzt Reihenfolge **14**, die das
freigegebene Layout (Stand Weg A, 24.10.2026) ergibt (Laufzeit ≈ 8 min; vorher 136). Andere Reihenfolgen suchen:
`K2_SEED_START=0 K2_VARIANTEN=48 K2_PARALLEL=12 K2_KANDIDATEN=8 ./bauen.sh` (Suche nach Weg A: unter 48 Reihenfolgen (0–47)
erreichten nur Nr. 14 und 22 0 offene Verbindungen; beide hatten nur noch einen Siebdruck-Treffer an R23, der jetzt nach dem
Routing entfernt wird – nach jeder Änderung an festen Leitungen/Platzierung neu suchen).

Benötigt KiCad 10 (kicad-cli + pcbnew-Python), Java ≥ 21, python3. Freerouting 2.5.0 lädt `bauen.sh` beim ersten Lauf mit
Prüfsumme nach `werkzeuge/` (nicht im Repo). Footprint-Geometrien der Fremdteile stammen aus den LCSC/EasyEDA-Bibliotheksdaten
(`generator/fremd_footprints.json`, nur abgeleitete Pad-Geometrie). Die echten 3D-Modelle
(nur Optik, siehe unten) holt `./lcsc_modelle_laden.sh` einmalig vor `./bauen.sh`; ohne sie zeigt die 3D-Ansicht Quader. CM5-Pinbelegung: Raspberry Pi CM5-Datenblatt (`generator/cm5_pins.json`); 22-pol. MIPI-Belegung:
KiCad-Designdaten des CM4-IO-Boards.

## 3D-Modelle und Lizenzen (24.10.2026)

Nur Darstellung – Lötflächen, Prüfungen und der Höhen-Wächter (Datenblatt-Höhen) bleiben unverändert.

| Teil | 3D-Modell | Quelle / Lizenz |
|---|---|---|
| R, C, LEDs, SOT/SOIC/MSOP/USON, Quarz, L1 FXL0630, L2, J1 (FH12-50S als Stellvertreter), J12 FH12-22S, **J9b/J11 JST GH stehend (BM03B-GHS-TBT)**, **JP_BOOT/JP_CAN_TERM/JP_485_TERM** (PinHeader 1×02 SMD Pin1Right, um 90° gedreht) | KiCad-Bibliothek `kicad-library-3d` 10.0.7 (`${KICAD10_3DMODEL_DIR}`, Renders setzen die Variable in `bauen.sh`) | **CC-BY-SA 4.0 mit Ausnahme** (Verwendung in eigenen Designs frei, Modelle selbst nicht im Repo) |
| **J10** USB-C stehend TYPE-C-16P-LTH6.8 (C5187468), **T1** H1102NLT (C18584), **F1** Littelfuse 0157003.DR (C187520), **J7** XT30PW-M (C431092; KiCad hat kein Modell), **U7** LMR51450 WSON-12 (C6890985), **U8** TPS2121 VQFN-HR-12 (C485916), **F3** PTC 1812 (C12559), **DF40C-100DS** ×2 im J2 (C597931) | LCSC/EasyEDA, geladen mit `easyeda2kicad` (`./lcsc_modelle_laden.sh`), Versatz/Drehung im Generator (`LCSC_MODELLE`) auf den eigenen Footprint gesetzt und im Render geprüft | **Lizenz ungeklärt → nur lokale Darstellung**: `k2_traeger.3dshapes/lcsc/` steht in `.gitignore` (nicht im Repo); fehlen die Dateien, nimmt der Generator automatisch die Quader |
| **CM5** (Hülle 55 × 40 mm, halbtransparent) | eigener Quader – ein offizielles CM5-3D-Modell war unter datasheets.raspberrypi.com nicht direkt auffindbar | eigene Darstellung |
| restliche eigene Quader (`k2_traeger.3dshapes/*.wrl`) | nur noch Rückfall, wenn die LCSC-Modelle fehlen | eigene Darstellung |

## Offene Punkte

1. **Impedanz** mit dem JLC-Rechner für JLC04161H-7628 bestätigen (USB 90 Ω 0,25/0,15; Ethernet/DSI 100 Ω 0,20/0,20 –
   hier nur nach IPC-2141 genähert).
2. **0,2-mm-Bohrungen:** 12 Vias haben 0,2 mm Bohrung (10 × Ø 0,6 mm, 2 × Ø 0,5 mm; Restring der beiden 0,5er = 0,15 mm =
   JLC-Minimum). Laut JLC-Preisregel ohne Aufpreis, da Durchmesser > 0,45 mm.
3. **Pinbelegungen der Kabel:** XT30-Polarität J7 – **seit 24.10.2026 Pin 2 = +24 V, Pin 1 = GND**, passend zu den
   „+“/„−“-Zeichen des KiCad-Footprints (nach Amass-Datenblatt XT30PW-M); vorher war Pin 1 = +24 V gesetzt, der
   Footprint-Siebdruck zeigte aber „−“ an Pin 1 (Widerspruch, jetzt behoben; „24V“/„GND“ stehen neben „+“/„−“). Am
   Muster mit dem Gehäuse-Aufdruck des Steckers vergleichen. JST-GH J9b (CAN zum Toolhead-Board) und J11 gegen die
   Gegenkabel prüfen. **Achtung:**
   bei der stehenden GH-Variante liegt Pin 1 räumlich auf der anderen Seite als bei der liegenden (Siebdruck „H L G“ an
   J9b bzw. „G B A“ an J11 gibt die Reihenfolge von links nach rechts an).
4. **DSI:** Kontaktseite/Pin 1 am Muster mit dem Pi-5-Displaykabel kontrollieren (gleicher Stecker wie CM4-IO, also
   erwartet passend); `dtoverlay=vc4-kms-dsi-7inch` am CM5 testen (I2C ID_SD/ID_SC); D2/D3 nicht verlegt.
   Die Lage von CON1 an der T113 ist nicht vermessen – J12 sitzt mittig an Kante C (y 22,1–39,2); bei Bedarf Kabellänge prüfen.
5. **Übertrager H1102NL:** Pinbelegung gegen das Datenblatt (Standard-Belegung wie PT61017) beim Bestücken prüfen.
6. **CM5-Montage:** Abstandshalter Ø ≤ 4,5 mm, Schrauben mit flachem Kopf (≤ 5,5 mm Ø unten), Kühlkörper inkl. Pad ≤ 4,96 mm.
7. **J10 (USB-C stehend):** Erreichbarkeit im Gehäuse prüfen (Stecker von oben, Gehäuse offen).
8. **Spiegelfall USB:** 5-V-Signal/GND auf den USB-Leitungen bei verdrehtem Flachkabel – Kurzschlussfestigkeit laut USB 2.0
   erwartet, am Muster prüfen.
9. **Sicherung F1 (Weg A):** Bestand des Halters **0157003.DR (C187520)** vor der Bestellung prüfen (zuletzt ≈ 1 500);
   **Schmelz-I²t der 3-A-Sicherung (Serie 453) im Littelfuse-Datenblatt bestätigen** (Abschätzung der Einschaltspitze
   ≈ 0,03–0,06 A²s, siehe „24-V-Eingang“). Fliegt die flinke Sicherung beim Stecken unter Spannung, träge 0452003.MRL
   (C99550) einsetzen. (R32 ohne LCSC-Nummer ist mit der eFuse entfallen.)
10. **Kompromisse dieses Entwurfs** (bewusst, dokumentiert): D3 (USB-ESD an J10) mit offenem VBUS-Pin wie D1/D2; INA226 U5
    misst VBUS direkt an IN− (R21-Filter entfallen); L3 als 3,3-V-Fläche mit festen 5-V-Bahnen (Freerouting nur L1/L4);
    INA226-/Leistungsnetze im Router 0,25 mm breit (Strom über Kupferflächen); THT-Teil J7 von Hand oder per
    JLC-THT löten; Steckbrücken JP_BOOT/JP_CAN_TERM/JP_485_TERM als stehende SMD-Stiftleisten (8,5 mm).
11. **Kompromisse des Umbaus 24.10.2026:**
    - ~~J9 (Toolhead) an Kante C, Toolhead-Strom ≈ 5 A über den L3-Stamm quer über die Platine~~ – **mit Weg A
      (24.10.2026) erledigt**: J9/eFuse entfallen, der Toolhead wird direkt vom Netzteil versorgt, der L3-Stamm führt nur
      noch ≈ 0,6 A zum Wandler.
    - Das Kamera-USB-Paar (J1 37/38 → CM5 134/136) läuft jetzt unter F1 hindurch östlich an J7 vorbei (x 8,8);
      Längenabgleich wie bisher (Paar-Wächter).
    - Die Eingangsvias liegen unter dem Halterkörper bzw. dicht daneben (tented).
13. **Weg A – Nachzüge:** Toolhead-Kabel (24 V vom Netzteil mit Kabelsicherung ≈ 5 A + CAN-Kabel an J9b) konfektionieren;
    GPIO6/GPIO26 sind frei (Software: kein `[power]`-Gerät mehr für den Toolhead).
14. **3D-Modelle:** Die LCSC/EasyEDA-Modelle stehen nur lokal zur Verfügung (Lizenz ungeklärt). Der CM5 bleibt eine
    halbtransparente Hülle; ein offizielles CM5-Modell (Raspberry Pi) bei Gelegenheit nachtragen.
12. **Supercap-Puffer (Nachtrag 2) – NICHT umgesetzt, nur gemeldet:** 2 × 10 F / 2,7 V (Ø 10 × 30 mm liegend) passen
    nicht: Außerhalb des CM5 gibt es oben keine freie Fläche ≥ 31 × 21 mm (größte freie Flächen ≈ 17 × 9 mm zwischen
    JP_BOOT und der Spule, ≈ 8 × 14 mm rechts oben), und liegend wären sie mit 10 mm + 1,6 mm Platine höher als 11,5 mm.
    Außerdem reicht die Energie ohne Hochsetzsteller kaum: 5 F zwischen 5,2 V und 4,75 V (Mindestspannung CM5) liefern
    nur ≈ 11 J → bei 5–8 W ≈ 1,5–2 s statt 3 s. Sinnvoller wäre ein externes Puffermodul oder ein Hochsetzsteller hinter
    den Supercaps. Die Stromausfall-Erkennung (PWR_FAIL_N) funktioniert auch ohne Puffer (Warnung, solange die 24 V
    noch sinken).
