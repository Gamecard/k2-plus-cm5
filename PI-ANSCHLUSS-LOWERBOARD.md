# Pi-Anschluss an das K2-Plus-Mainboard (Stand 17.10.2026 spät)

Ziel: Raspberry Pi (CM5) ersetzt das Upper-Board (Allwinner T113 in der Display-Einheit) und spricht mit
denselben Leitungen wie das Original: Main-MCU, Düsen-MCU, RS485-Bus (CFS/Motoren/Belt). MCU-Firmware bleibt
unverändert. Software (Upstream-Klipper 9cb9975 + k2-Module) und Installationspaket liegen fertig vor
(`pi-host/install/`, README dort).

## 1. Aufbau (aus Creality-Verkabelungsplan + Board-Foto)

```
Display-Einheit (oben)                         Mainboard CR4FN200338C15 (unten)
┌───────────────────────────────┐              ┌────────────────────────────────────────┐
│ Adapterplatine ═FFC═ Host-Board│◄── Kabel ───►│ J22  PHB 2,0 mm 2×6, mit Rastnase       │
│ (T113)          ═FFC═ Display  │              │ GD32F303RET6 (Main-MCU)                 │
└───────────────────────────────┘              │ „3232E“ = RS232-Wandler (SP/MAX3232E)   │
                                                │ J15 „RS232-BED“, „485“-Anschluss, CFS   │
                                                │ SWD-Pads NRST/GND/CLK/DIO/VCC (nur Debug)│
                                                └────────────────────────────────────────┘
```

- Upper-Board und Mainboard sind **getrennte Platinen** mit Kabel → kein Löten nötig.
- Stock-Config: `[mcu] /dev/ttyS2 230400`, `[mcu nozzle_mcu] /dev/ttyS3 230400`, `[serial_485] /dev/ttyS5`.
- SWD-Anschluss (NRST, GND, CLK, DIO, VCC) ist **nur** zum Flashen/Debuggen, nicht für den Pi.

## 2. Messung am Stecker J22 (Kabel zur Display-Einheit abgezogen, Mainboard an)

Nummerierung „von oben links“ (eigene Zählung; Lage der Rastnase im Foto noch festhalten):

```
obere Reihe:    1      2     3      4         5      6
              0,23V   GND   frei  RS232-RX   2,1V   3,4V
                                  (4,9 kΩ)
untere Reihe:   7      8     9     10        11     12
              3,2V   GND   frei  RS232-TX   GND    +5,2V ⚠
                                  (−5,5 V)
```

| Pin | DC gegen −V | Ω gegen −V (aus) | Bedeutung |
|---|---|---|---|
| 1 | 0,23 V | – | 3,3-V-Signal, Zweck offen |
| 2 | 0 | 0,1 Ω | GND |
| 3 | 0 | offen | nicht belegt |
| 4 | 0 | 4,9 kΩ | **RS232-Empfänger des Boards** |
| 5 | 2,1 V | – | 3,3-V-Signal, Zweck offen |
| 6 | 3,4 V | – | 3,3-V-Signal, Zweck offen |
| 7 | 3,2 V | – | 3,3-V-Signal, Zweck offen |
| 8 | 0 | 0,1 Ω | GND |
| 9 | 0 | offen | nicht belegt |
| 10 | **−5,5 V** | – | **RS232-Sender des Boards** |
| 11 | 0 | 0,1 Ω | GND |
| 12 | +5,2 V | – | 5-V-Versorgung Display – **NIE anschließen** |

## 3. Was getestet wurde (17.10.2026)

- USB-TTL (Waveshare (D), FT232RNL, 3,3 V) an Pin 1/7 und 5/6, je beide Richtungen, Baud 115200–1000000:
  **keine Antwort, 0 Bytes** → diese Pins sind nicht die MCU-UART bzw. kein TTL-UART.
- Loopback des USB-TTL-Adapters: funktioniert (Kontakte der 2-mm-Jumper wackelig).
- Motorbrummen beim Einschalten = normaler Selbsttest der Closed-Loop-Motoren.
- Fazit: Host-Link läuft über **RS232 (Pin 10/4)**, nicht TTL.

## 4. Benötigte Hardware

| Teil | Zweck | Status |
|---|---|---|
| Waveshare **USB TO RS232/485 (B)**, isoliert, Schraubklemmen TXD/RXD/GND | RS232-Link Pin 10/4 | nur für den RS232-Weg (Variante B braucht ihn nicht) |
| USB-A↔USB-B-Kabel | Konverter ↔ Pi | prüfen |
| Waveshare USB-RS485 (CH343G + SP485EEN) | RS485-Bus (CFS/Motoren/Belt) | vorhanden |
| 2× Waveshare USB-TTL (D) | ggf. für weitere TTL-Leitungen | vorhanden (an J22 nicht nötig) |
| PHB 2,0 mm 2×6 Buchse mit Kabel (Rastung) | sichere Verbindung zu J22 | gesucht (Notlösung: 2-mm-Jumper ohne Hülse + isoliert + fixiert) |

## 5. Anschlussplan RS232 (nächster Schritt)

```
Konverter-Klemmen          J22 Mainboard
RXD  ◄────────────────     Pin 10   (Board sendet)
TXD  ────────────────►     Pin 4    (Board empfängt)
GND  ─────────────────     Pin 2    (GND)
```

1. Drucker **aus**, Kabel zur Display-Einheit bleibt abgezogen.
2. Nur RS232-Klemmen benutzen (A/B frei), drei Drähte fest + isoliert; Pin 10 darf 11/12 nicht berühren.
3. USB an den Pi, Drucker an.
4. Identify-Test (nur lesend): `pi-host/install/k2-identify.py` auf dem Pi (README §4b) – zeigt, ob Main- (gd32f303xe) oder Düsen-MCU (gd32f303xb) antwortet.
5. Die zweite MCU-Leitung (vermutlich anderes Kabel) danach suchen.

## 6. Sicherheitsregeln

- Multimeter: DC-Volt (nicht V~, VFD, LoZ), Schwarz in COM an −V der 24-V-Klemme, Rot in V/Ω.
- Nur bei ausgeschaltetem Drucker stecken; GND zuerst.
- Pin 12 (+5 V) und Pin 10 (−5,5 V) nie mit TTL-Adaptern verbinden.
- Upper-Board und Pi nie gleichzeitig an derselben Leitung.
- Original-Kabel nicht zerschneiden (Rückweg zum Stock-Zustand).
- Vor dem Umbau gesichert: `material_database.json` + `remain_material_data.json` vom Stock-Board
  (lokal, nicht im Repo) → später `install.sh --material-db … --remain-material …`.

## 7. Offen

- Zweck der 3,3-V-Signale Pin 1/5/6/7 (evtl. Display/Touch/LED).
- Wo die zweite MCU-Leitung (Düse) verläuft.
- RS485-Anschluss: „485“-Stecker am Mainboard (HY2.0-5P) bzw. CFS-6-Pol-Stecker – vor Anschluss messen
  (Fremdmessung Hi: 1 A, 2/3 Buffer, 4 +24 V, 5 GND, 6 B).

## 8. Bridge-/Kommunikations-Board in der Display-Einheit (Bestückung)

Vier Platinen: Upper `CR0CN240319C13` (Allwinner T113-i + AP6256), Bridge/„Upper Communication Board“ `F008-PC V15`,
Lower `CR4FN200338C15`, Toolhead `CR1FN200338C16`. Abgelesene Chips auf dem Kommunikations-Board:

| Chip (Aufdruck) | Bedeutung | Folgerung |
|---|---|---|
| 2× 3PEAK `3232E` | RS232-Pegelwandler (TPT3232E, je 2 Sender + 2 Empfänger, MAX3232-kompatibel) | bis zu 4 RS232-Kanäle: Die T113-UARTs (`ttyS2` mcu, `ttyS3` nozzle_mcu, ggf. weitere) werden hier von 3,3 V auf RS232 gewandelt und auf dem Mainboard (3232 → GD32) zurück. Passt zu J22 Pin 10/4 (−5,5 V). |
| Genesys `GL852G` | USB-2.0-Hub (4 Ports) | Für Kamera/USB-Stick o. Ä. – **nicht** für die MCUs (Stock-Config: `/dev/ttyS2`/`ttyS3`, keine USB-Seriell). |
| NCE Power `NCEP40P80K` (Foto) | P-Kanal-MOSFET (TO-252) am 24-V-Eingang (Verpolschutz/Schalter) | für den Pi-Anschluss unwichtig |

**ANNAHMEN / offen:**
- Wohin der zweite 3232 führt (Düsen-MCU über anderes Kabel? Debug-Konsole?). J22 Pin 6/7 zeigen 3,2–3,4 V = TTL, nicht RS232.
- Alternative zum Konverter an J22: Pi-UARTs (3,3 V TTL) dort einspeisen, wo das T113-Board am Bridge-Board steckt – die
  RS232-Strecke bliebe original. Dafür nötig (stromlos, Durchgang): 3232-Pins **11/10 (T1IN/T2IN)** und **12/9 (R1OUT/R2OUT)**
  zum T113-Stecker verfolgen, **14/7 (T1OUT/T2OUT)** und **13/8 (R1IN/R2IN)** zu den Kabelsteckern; Pin 16 = VCC, 15 = GND
  (Standard-Pinbelegung 3232, am Datenblatt des TPT3232E gegenprüfen).

### 8.1 Foto Bridge-Board `F008-PC_V15` (Oberseite, 19.10.2026)

| Bauteil/Stecker | Beobachtung | Deutung (ANNAHME, nicht gemessen) |
|---|---|---|
| 2-pol. Stecker oben links, „24V +“, TVS `SMAJ28A` (D6), `NCEP40P80K`, U1 + L1 | 24-V-Eingang, Schaltregler | 24 V → 5 V für Hub/Upper-Board |
| **J10** (**12-pol.**, 2×6 – auf dem Foto nur eine Reihe sichtbar) | daneben **U6 = 3PEAK 3232E**, 4 ESD-Dioden **ESD7–ESD10** + Varistor RV6 | Gegenstück zu **J22** am Mainboard. KORREKTUR: Die 4 ESD-Dioden beweisen keine 4 RS232-Leitungen (der 3232**E** hat eigenen ESD-Schutz) – sie schützen vermutlich die 3,3-V-Leitungen (J22 Pin 1/5/6/7). |
| Stecker rechts oben, Aufdruck „24V“ – **2×7 = 14-pol., 2 Pins unbelegt** (auf dem Foto nur eine Reihe sichtbar) – **führt zum Toolhead** | daneben U7 (SOIC-16, Aufdruck unlesbar – vermutlich ein zweiter 3232), ESD5/ESD6 | 2 geschützte Leitungen + 24 V + GND → **Düsen-MCU-UART (`ttyS3`) läuft vermutlich als RS232 direkt Bridge → Toolhead**, nicht über J22. Kabel liefert auch die 24 V des Toolheads → für den Pi nicht einfach abziehen, sondern Zwischenadapter (24 V/GND durch, TX/RX zum Konverter). |
| **J14** (rechts) | FFC/FPC-Stecker, ≈ 50 Pole, 0,5 mm, ESD-Arrays D13/D14/D16/D17, Testpunkte „5V“/„GND“ | Flachband zum Upper-Board (T113) bzw. Display – Einspeisung nur mit FFC-Breakout, nicht mit Jumperkabeln |
| U8 `GL852G` + Y1 12,000 MHz | USB-2.0-Hub | → J18 (USB-A, Rückseite), J8, **CAM-X** (J15), USB im Toolhead-Kabel (KORREKTUR 21.10.2026: J3 unten links ist die **RJ45-Netzwerkbuchse**, nicht USB; CAM-T kommt direkt vom T113, nicht vom Hub) |
| J1 (3-pol.) | – | Zweck offen |
| unten Mitte: 7 vergossene Lötpunkte | Bauteil/Kabel von der Rückseite | Rückseitenfoto nötig |

Offene Kernfrage: Wohin führen die Kabel von **J10** und vom **7-pol. Stecker**? An J22 (Mainboard) wurde nur **ein**
RS232-Paar gefunden (Pin 10/4) – J10 hat aber vier RS232-Leitungen. Messplan (stromlos, Durchgang): U6 Pin 14/7 (T1OUT/T2OUT)
und 13/8 (R1IN/R2IN) → J10-Pins; U6 Pin 11/10 (T1IN/T2IN) und 12/9 (R1OUT/R2OUT) → J14-Pins. Falls beide MCUs RS232 über J10
laufen: Der Waveshare (B) hat nur **einen** RS232-Kanal → zweiter Kanal nötig.

Folgerung 19.10.2026: Hypothese H1 „J22 Pin 6/7 = Düsen-UART“ ist damit unwahrscheinlich; der USB-TTL-Test an
6/7 ist nachrangig. **Nie** USB-TTL an die Toolhead-Leitungen (RS232-Pegel ±5,5 V). Nächste Messung (stromlos): U7 Pin 14/7 und
13/8 → Pins des 14-pol. Toolhead-Steckers; 24 V und GND am Stecker per Durchgang zuordnen. Offen: Ziel des J10-Kabels.

Stand 19.10.2026: **J10 → Mainboard.** Bild damit (ANNAHME bis zur Durchgangsmessung):
- Main-MCU: T113 `ttyS2` → U6 (3232) → J10 → Kabel → Mainboard (J22 Pin 10/4 gemessen) → 3232 → GD32 USART1.
- Düsen-MCU: T113 `ttyS3` → U7 (3232) → 14-pol. Toolhead-Kabel (2×7) → Toolhead-Board.
- Zweiter Kanal von U6 an J10: Ziel offen. Kandidat J22 Pin 3/9 („frei/offen“, kein Pegel) = auf dem K2 Plus unbelegter
  Kanal (Firmware kennt ein Ziel `bed0`; Mainboard hat J15 „RS232-BED“) – oder `ttyS5` (RS485) zum Mainboard.
- Messung (beide Stecker ab, stromlos): jede Ader J10-Pin → Mainboard-Pin; dazu U6 Pin 14/7/13/8 → J10-Pins.

KORREKTUR 19.10.2026: J10 ist 12-polig → Kabel J10 ↔ J22 (vermutlich 1:1, Pinfolge prüfen). Neues Bild (ANNAHME):
`ttyS2` = RS232 über J10/J22 Pin 10/4 (gemessen); `ttyS3` = RS232 über das Toolhead-Kabel; `ttyS5` (RS485-Bus) evtl. als
3,3-V-TTL über J22 Pin 6/7 zu einem RS485-Treiber auf dem Mainboard, Pin 1 evtl. Richtungssteuerung (DE). Je 3232 wäre dann nur
ein Kanal belegt. Für den Pi ohne Folgen (eigener USB-RS485-Adapter direkt am Bus).

### 8.2 Folgerung für den Pi-Anschluss (19.10.2026)

- Rastermaß bestätigt: J10 (2×6) und Toolhead-Stecker (2×7) = **2,0 mm**, wie J22 (PHB 2,0, zweireihig).
- Das Bridge-Board ist Knoten für den Toolhead (Kommunikation **und** 24 V über das 14-pol. Kabel) → es sollte im Pi-Betrieb
  **drin bleiben**; ersetzt wird nur das T113-Board.
- **Variante A** (bisheriger Plan, erweitert): J10/J22-Kabel ab, RS232-Konverter an J22 Pin 10/4 (Main) + **zweiter**
  RS232-Konverter für die Düse über einen 2×7-Zwischenadapter (PHB 2,0 Stecker + Buchse, 10 Adern 1:1, 2 Datenadern zum Konverter).
- **Variante B** (Kandidat, ungemessen): T113 ab, Pi-UARTs (3,3 V) über ein **FFC-Breakout an J14** direkt auf die TTL-Seite
  von U6/U7 → beide MCUs über die Original-Wege, **kein** RS232-Konverter, keine Arbeit an 24-V-Adern; ggf. auch `ttyS5`-Weg.
  Voraussetzungen: J14-Polzahl/Rastermaß/Kontaktseite, Pinbelegung (Durchgang U6/U7 Pin 11/12 → J14), Klärung, ob J14 auch
  Versorgung (5 V vom Bridge-Board) oder Steuersignale führt (z. B. Gate von `NCEP40P80K` über Q28 = 24-V-Schalter?).
- Offen: Herkunft des 2-pol. 24-V-Eingangs; ob die 24 V für den Toolhead vom T113 geschaltet werden.

### 8.3 Entscheidung Variante B + Messplan

J14-Flachkabel führt zum T113-Board; **Variante B bevorzugt**. J14 laut Foto ≈ **50 Pole, 0,5 mm** (Schätzung aus
Pinabstand; am Kabelaufdruck/Zählen bestätigen).

| Schritt | Zustand | Was | Wozu |
|---|---|---|---|
| B1 | stromlos | Kontakte des FFC zählen, Kabelaufdruck ablesen; notieren, auf welcher Seite die Kontakte/Versteifung an J14 und am T113 liegen | passendes FFC-Breakout (0,5 mm, n Pole) kaufen. Tipp: Original-Flachkabel nehmen und das T113-Ende ins Breakout stecken |
| B2 | Drucker an, Original läuft (nur messen, schwarz an Testpunkt „GND“ neben J14) | U6 und U7 **Pin 16 (VCC)**: 3,3 V oder 5 V? Pin 11 und 12 im Ruhezustand | **Bei 5 V** gibt Pin 12 (R1OUT) 5 V aus → **nie direkt an Pi-GPIO** (max. 3,3 V), Spannungsteiler/Pegelwandler nötig. Vorsicht: Pin 15 (GND) liegt direkt neben Pin 16 – nicht abrutschen |
| B3 | stromlos, T113 ab, Breakout am Original-FFC | Durchgang U6/U7 Pin 11/10 (TxIN) und 12/9 (RxOUT) → Breakout-Pins | UART-Pins für Pi (Pi-TX → TxIN, Pi-RX ← RxOUT) |
| B4 | stromlos | J14-Pins zu Testpunkten „5V“/„GND“, zu GL852G (USB D+/D−, Reset), zur Basis von Q28 (Gate `NCEP40P80K`) | was das T113 sonst liefert/steuert (5 V NICHT in den Pi einspeisen; USB-Hub ggf. an Pi-USB → Kameras) |

Pi-Seite (zu prüfen): CM5-UARTs per Overlay (Pi-5-Familie, z. B. `uart2-pi5`/`uart3-pi5`/`uart4-pi5`) – zwei freie UARTs für
`mcu` und `nozzle_mcu` (230400), Base-Board muss die GPIOs herausführen. RS485 weiter über eigenen USB-Adapter.
- KORREKTUR 21.10.2026: **J3 ist die RJ45-Netzwerkbuchse (Ethernet)**, keine USB-Buchse. Die USB-A-Buchse des Hubs ist **J18** (Rückseite) = Downstream-Port des Hubs GL852G (wie CAM-X/J8). J18 **nicht** für den Pi-Anschluss:
  Pi ist Host, ein USB-A↔USB-A-Kabel verbindet zwei 5-V-Quellen (Schadensgefahr) und die MCUs hängen ohnehin nicht am USB.
  Der Pi kann den Hub nur über dessen **Upstream** nutzen (vermutlich USB D+/D− über J14, Messschritt B4).

### 8.4 Rückseite Bridge-Board – Aufdruck der Steckerbelegung (Foto 19.10.2026)

Die Rückseite ist beschriftet (Ansicht von hinten; je Stecker 2 zusätzliche Löcher = Haltestifte):

**J10 (2×6, zum Mainboard J22)**

| linke Spalte | rechte Spalte |
|---|---|
| 5V-IN | SYNC |
| GND | SD |
| RX7 | TX7 |
| RX9 | TX9 |
| GND | GND |
| 485_A | 485_B |

**Toolhead-Stecker (2×7)**

| linke Spalte | rechte Spalte |
|---|---|
| GND | 24V (eckiges Pad) |
| GND | 24V |
| GND | 24V |
| RX1 | TX1 |
| GND | SYNC |
| VBUS2 | GND |
| D2− | D2+ |

**USB (GL852G):** 4-pol. „GND/DP/DM/VBUS0“ = vorne **CAM-T** (J16) – abweichende Benennung (DP/DM statt D1±) → vermutlich
**Hub-Eingang (Upstream)**; J18 USB-A hinten = Port 1 (VBUS1/D1±, Ausgang); J8 = Port 3; CAM-X (J15) = Port 4; Port 2 → Toolhead-Kabel.
**J1** (3-pol.) = GND/OC/3V3. Testpads an allen UART-Leitungen (RX1/TX1, RX7/TX7, RX9/TX9) sowie 5V-IN, SYNC, SD, 24V.

**Abgeleitete Belegung J22 (Mainboard)** – passt zu ALLEN Messwerten aus §2, wenn das Kabel J10↔J22 gespiegelt 1:1 verläuft
(J22 obere Reihe = rechte J10-Spalte von unten, untere Reihe = linke Spalte von unten):

| J22 | Messung (§2) | Signal (Aufdruck) |
|---|---|---|
| 1 | 0,23 V | 485_B |
| 2 | GND | GND |
| 3 | frei | TX9 (zweiter RS232-Kanal, am Mainboard **unbelegt**) |
| 4 | RS232-Empfänger (4,9 kΩ) | TX7 (Bridge sendet → Main-MCU) |
| 5 | 2,1 V | SD |
| 6 | 3,4 V | SYNC |
| 7 | 3,2 V | 485_A |
| 8 | GND | GND |
| 9 | frei | RX9 (unbelegt) |
| 10 | −5,5 V (RS232-Sender) | RX7 (Main-MCU sendet → Bridge) |
| 11 | GND | GND |
| 12 | +5,2 V | 5V-IN (Mainboard versorgt das Bridge-Board) |

Folgerungen (ANNAHME bis Durchgangsmessung, aber in sich stimmig):
- **RS485 liegt an J22 Pin 7 (A) / Pin 1 (B)**, nicht 5/1 (Korrektur einer früheren Annahme). Ruhe A−B ≈ +3 V (Failsafe).
- **Pin 6/7 sind kein Düsen-UART** (SYNC und 485_A) → USB-TTL-Test an 6/7 entfällt.
- Main-MCU = Kanal „7“ (U6), Düsen-MCU = Kanal „1“ (U7, Toolhead-Kabel), Kanal „9“ unbenutzt.
- **SYNC** läuft vom Toolhead über das Bridge-Board zum Mainboard – vermutlich das PrTouch-Auslösesignal (Endstop `!PC7` der
  Main-MCU). Muss im Pi-Betrieb durchverbunden bleiben (Variante B erfüllt das).
- Auf dem Bridge-Board ist kein RS485-Treiber erkennbar → der Treiber für `ttyS5` sitzt vermutlich auf dem T113-Board, A/B laufen
  über J14 durch. Variante B: USB-RS485-Adapter des Pi an 485_A/485_B (am Breakout oder an J10/J22) – prüfen (B4).

Nachtrag 19.10.2026: **CAM-T führt zur Kamera** → kein Hub-Eingang. Neue Deutung (ANNAHME): „VBUS0/DP/DM“ ist der
**zweite USB-Port des T113 direkt** (über J14 durchgereicht, daher andere Benennung); der Hub-Eingang des GL852G kommt ebenfalls
über J14 vom T113 (B4 prüfen). Für den Pi: Kamera am einfachsten **direkt** an einen Pi-USB-Port (Adapterkabel 4-pol. →
USB-A; Pin 1 = eckiges Pad = VBUS0, dann DM, DP, GND) – High-Speed-USB über Breakout und Steckbrücken ist unzuverlässig.
Hub-Ports (USB-A J18, J8, CAM-X, Toolhead-USB) sind für Klipper nicht nötig, niedrige Priorität.
- Toolhead-Kabel: **Reihe 1 (GND | 24V, eckiges Pad) im Kabel unbelegt** → genutzt: 2× 24V, 4× GND, RX1/TX1,
  SYNC, VBUS2/D2−/D2+. Der USB-Port 2 des Hubs führt also wirklich zum Toolhead; die Stock-Klipper-Config kennt aber kein
  USB-Gerät (kein ttyUSB/ttyACM/by-id) → für Klipper ohne Bedeutung, Zweck des USB am Toolhead offen.

### 8.5 J14-Flachkabel (Fotos 19.10.2026)

- **50 Kontakte**, Raster 0,5 mm (aus Foto gezählt: vier Spuren entlang der schrägen Kontaktreihe ergeben übereinstimmend 50;
  mittlerer Abstand passt zur Kabellänge). Kabel mit **Halteohren** an beiden Enden, Stecker = 0,5-mm-FPC mit Klappbügel.
- Kauf für Variante B: FPC/FFC-Adapterplatine **50 Pole, 0,5 mm → 2,54 mm**. Darauf achten: (1) Kontaktseite des Steckers
  muss zur Kabellage passen – am sichersten ein Stecker mit Kontakten oben **und** unten („dual contact“), sonst die
  Kontaktseite am T113-Stecker ablesen; (2) Steckeröffnung muss die Halteohren aufnehmen (Ohren **nicht** abschneiden –
  Rückweg zum Stock). Alternative: zusätzliches 50-pol. 0,5-mm-Kabel, Original unberührt lassen.
- Geeignet: Adapterplatine QUARKZMAN „FPC/FFC 50P 0,5 mm → DIP 2,54 mm“; Stecker **mit Haltenase** → Kabel mit Halteohren sollte einrasten.
- **Ethernet (J3, RJ45):** Die Netzwerk-Leitungen kommen vermutlich vom T113-Board (PHY dort) über J14 zur Buchse (ANNAHME, Pins unbekannt).
  Im Pi-Betrieb ist diese Druckerbuchse tot – der CM5 hat seine Netzwerkbuchse auf dem IO-Board (oder WLAN). Für V3
  (eigener Träger) die CM5-Netzwerkleitungen über J14 an J3 führen, dann bleibt die Original-Buchse nutzbar.
- **J1** (3-pol., Aufdruck Rückseite 3V3 / OC / GND, **unbestückt**): Funktion offen. Durchgang 21.10.: OC ↔ Breakout 7, 8, 10, 13,
  22, 24 und ↔ U8 Pin 19/21 (OVCUR2#/OVCUR1#) jeweils **kein** Durchgang. Noch ungeprüft: OC ↔ 9, 11, 12, 14, 15, 17 und ↔
  Fehlerausgang des Kamera-Lastschalters (U10/U5, ANNAHME). Für Klipper/Pi nicht nötig.
- CAM-X (J15, Hub-Port 4) ist **nicht belegt** → freier Reserve-Port des Hubs. Foto-Befund 21.10.2026: **CAM-X (J15) und J8 sind unbestückt** (nur Lötlöcher, kein Stecker) – für eine Nutzung müsste ein 4-pol. Stecker eingelötet werden.
- Original-Flachkabel (Fotos 21.10.2026): Aufdruck „F008-PC_FPC_V14“, Länge ≈ 4,6 cm, 50 Kontakte 0,5 mm, **Typ A**
  (Kontakte an beiden Enden auf derselben Seite). Eingebaut: Aufdruck oben, Kontakte unten. Ende „TO PB“ (mit
  Halteohren) → Bridge-Board J14, Ende „TO PC“ (ohne Halteohren) → Upper-Board. Korrektur zu oben: Halteohren nur am
  Ende „TO PB“. Ersatz für den Pi-Einbau: 50P, 0,5 mm, Typ A („gleiche Seite“), 10–15 cm.
- Breakout-Geometrie (Produktfoto mit Maßen, 21.10.2026): Platine **65 × 26 mm**, Aufdruck „FPC-50P 0.5MM“; Stifte als
  **2×25-Raster 2,54 mm** an der langen Kante gegenüber dem FFC-Stecker – Kantenreihe gerade Pins (50 … 2 von links nach
  rechts), innere Reihe ungerade (49 … 1), Pin 1 = quadratisches Pad (IDC-Zählweise: Spalte = Paar 2k−1/2k). Zwei
  Befestigungslöcher ≈ Ø3 mm, Abstand ≈ 60 mm (geschätzt). Für die Adapterplatine (hardware/k2-pi-adapter-v1) passt eine
  Standard-2×25-Buchsenleiste.

### 8.6 Messprotokoll J14 am FFC-Breakout (20.10.2026)

Aufbau: Original-Flachkabel J14 → statt ins T113 in das Breakout (sourcing map, 50P 0,5 mm → 2,54 mm). Stiftnummern = Aufdruck
des Breakouts auf der **Oberseite** (Seite mit dem FFC-Stecker); die Unterseite zählt gegenläufig (oben n = 51 − unten).

| Messung | Ergebnis |
|---|---|
| B4a (alt, 20.10.): GND – Nummern von der **Unterseite** abgelesen (Breakout zählt Ober-/Unterseite gegenläufig: oben n = 51 − unten) | 7–12, 15, 18, 20, 24, 28, 30, 35, 45 (unten) = 44–39, 36, 33, 31, 27, 23, 21, 16, 6 (oben) |
| **B4a (neu, 21.10., VERBINDLICH, Oberseite = Seite mit dem FFC-Stecker)** | GND = **3, 6, 16, 18, 21, 23, 25, 27, 29, 31, 33, 36, 39, 40, 41, 42, 43, 44** (18 Stück; 46 war Fehl-Piepser, ist 5 V) |
| Ω-Bestätigung (21.10.) | 45, 47, 48, 49, 50 ↔ 5V = 0,2 Ω; 39–44 ↔ GND = 0,1 Ω → **5 V = 45–50, GND-Block 39–44 bestätigt** (Pi-GND an 39–44) |
| Testpunkt RX7 (Rückseite) ↔ Breakout | nur Stift 21 = 5,18 kΩ – **Stift 21 ist GND** → das ist der ≈ 5-kΩ-Eingangswiderstand eines RS232-Empfängers (3232 R1IN) gegen Masse; **keine direkte Verbindung RX7 → Flachkabel** → RS232 wird auf dem Bridge-Board gewandelt (bestätigt) |
| Testpunkt TX7 ↔ Breakout (Nicht-GND-Stifte) | überall OL → kein direkter Weg (RS232-Sender von U6) |
| Testpunkt TX1 ↔ Breakout (Nicht-GND-Stifte) | überall OL |
| Testpunkt RX1 ↔ Breakout | 40/42/44 (GND) = 5,20 kΩ (Empfänger-Eingang U7 gegen Masse, wie RX7); 46 (5 V) = 19,55 kΩ; **15 = 29,97 kΩ, 17 = 23,47 kΩ** → keine direkte Verbindung, sondern Weg über Masse: Stifte 15/17 haben selbst ≈ 18–25 kΩ gegen GND (Pull-Widerstände → Kandidaten für Signalleitungen) |
| **Fazit Test RS232 ↔ Flachkabel** | TX7/RX7/TX1/RX1 haben **keine** direkte Verbindung zum Flachkabel; RX7/RX1 hängen an 3232-Empfängereingängen (≈ 5 kΩ gegen GND) → RS232 wird auf dem Bridge-Board gewandelt, **Variante B bleibt gültig**; zwischen 3232-TTL-Seite und J14 sitzt ein aktives Bauteil → Zuordnung der Pi-Stifte per Spannungsmessung/Signaltest |
| Spannung (21.10., Drucker an, T113 ab, Bezug GND 39) | siehe Spannungskarte unten |
| Nicht-GND (oben) | 1, 2, 4, 5, 7–15, 17, 19, 20, 22, 24, 26, 28, 30, 32, 34, 35, 37, 38, 45, 47–50 (31 Stück) |
| Kontrolltest | U6 Pin 15 (GND) ↔ Stift 7: Durchgang ✓ (Pin-Zählung und Kontakt bestätigt) |
| B3: U6 Pin 11/12, U7 Pin 11/12 (Durchgang/Piepser) gegen Stifte 1–6, 13, 14, 16, 17 | **kein Treffer** – Stifte 19–50 noch nicht geprüft; möglicherweise Serienwiderstände (Piepser erst < 30–50 Ω) → morgen im **Ω-Modus** alle Nicht-GND-Stifte (1–6, 13, 14, 16, 17, 19, 21–23, 25–27, 29, 31–34, 36–44, 46–50) |
| B3 (21.10., Ω-Modus): U6 Pin 12 ↔ Stift 49 = **3,254 MΩ** | **keine** echte Verbindung (Kriechstrom) – Suche läuft weiter |
| B3: U6 Pin 12 (R1OUT) gegen Nicht-GND-Stifte | nur MΩ-Werte (Stift 49: 3,25 MΩ, Stift 32: 12,9 MΩ) = keine Verbindung |
| B3: **U6 Pin 10 (T2IN, Kanal 2) ↔ Stift 30 = 228,8 Ω** | Stift 30 ist **GND** → unbenutzter Eingang von Kanal 2 liegt über ≈ 229 Ω an Masse (passt zu TX9/RX9 unbelegt) |
| B3 (fester kΩ-Bereich): **U6 Pin 12 (R1OUT) gegen alle 36 Nicht-GND-Stifte: überall OL** | **keine** direkte/ohmsche Verbindung zu J14 → zwischen U6 und J14 sitzt vermutlich ein aktives Bauteil (Puffer/Pegelwandler) oder die Leitung läuft anders – Spannungs-Kartierung J14 (0.8) als nächster Schritt |
| B3 (fester kΩ-Bereich): **U7 Pin 12 (R1OUT) gegen alle Nicht-GND-Stifte: überall OL** | wie U6: kein ohmscher Weg U6/U7-RxOUT → J14 → Zwischenbauteil wahrscheinlich (Pegelwandler/Puffer, falls 3232 mit 5 V läuft) |
| B3: **U7 Pin 11 (T1IN) gegen alle Nicht-GND-Stifte: überall OL** | auch der Eingang vom T113 hat keinen ohmschen Weg → beide Richtungen laufen über ein Zwischenbauteil |
| B4b (21.10., Durchgang zu Testpunkt „5V“, ohne Strom, Oberseite) | **5 V = 45, 46, 47, 48, 49, 50** (Block neben dem GND-Block 39–44) – Stift 46 stand auch in der GND-Liste → Widerspruch; Ω-Messung: 46 ↔ Testpunkt 5V = 0,2 Ω, 46 ↔ GND = 14,77 Ω → **46 ist 5 V**; die ≈ 15 Ω sind die Last der 5-V-Schiene gegen Masse (unversorgt), daher der Fehl-Piepser in der GND-Runde. GND-Liste ohne 46 = **18 Stifte**. **Sicherheitsregel: Stift 46 wie 5 V behandeln, nie als GND für den Pi verwenden**; Pi-GND z. B. an 39–44 |

Hinweis Zählung: Die Zeilen „Kontrolltest“ und „B3“ mit einzelnen Stiftnummern (7, 30, 32, 49) stammen noch aus der
**Unterseiten**-Zählung (oben = 51 − n: 7→44 GND, 30→21 GND, 32→19, 49→2). Die „überall OL“-Ergebnisse gelten unabhängig davon.

#### Pi-Test 22.10.2026 (k2-identify, nur `identify`) – **BESTÄTIGT: 30/32 = Main-MCU, 26/28 = Düsen-MCU**

Verdrahtung: Pi 7 (TXD2) –1 kΩ– B26, Pi 29 (RXD2) –1 kΩ– B28, Pi 32 (TXD4) –1 kΩ– B30, Pi 33 (RXD4) –1 kΩ– B32, Pi 39 – B39.
Drucker kalt eingeschaltet (ohne T113), danach Pi.

| Port | Breakout | Ergebnis |
|---|---|---|
| `/dev/ttyAMA4` (UART4) | TX → **30**, RX ← **32** | **Antwort 0,2 s: Main-MCU** gd32f303xe, 120 MHz, 230400, Firmware `1.1.0.48-293-g493f9a0f-dirty-20241220_140327`, 75 Befehle / 34 Antworten → **30/32 = Main-MCU**; Main-Bootloader springt nach Kaltstart **ohne Upper-Board selbst in die App** (CL Z. 671 für Main beantwortet) |
| `/dev/ttyAMA2` (UART2) | TX → **26**, RX ← **28** | zuerst stumm – **Ursache: Toolhead-Kabel war abgesteckt**. Nach dem Anstecken (Kaltstart): **Antwort 0,2 s: Düsen-MCU** gd32f303xb, 120 MHz, 230400, Firmware `1.1.0.48-293-g493f9a0f-dirty-20241220_143931` → **26/28 = Düsen-MCU**; Düsen-Bootloader springt ebenfalls **selbst in die App** |

#### Spannungskarte Breakout (21.10.2026, Drucker an, T113 abgesteckt, Oberseite)

| Gruppe | Stifte (V) | Deutung (ANNAHME) |
|---|---|---|
| 5 V Versorgung | 45–50 | 5V-IN des T113 – **nie an Pi** |
| GND | 3, 6, 16, 18, 21, 23, 25, 27, 29, 31, 33, 36, 39–44 | Masse, Pi-GND an 39–44 |
| **≈ 5 V Signal/Pull-up** | **11 (5,23), 12 (5,23), 14 (5,23), 15 (5,17), 17 (4,23–4,31)** | Leitungen mit 5-V-Pegel/Pull-up (Eingänge vom T113 oder 5-V-Steuersignale) – **nie direkt an Pi** |
| **≈ 3,3 V Ruhe-High** | **28 (3,30), 32 (3,30)**, 35 (3,35) | Kandidaten **Bridge → T113 (UART-RX Main/Düse)**; 35 ist **weder SYNC noch 3V3** (Durchgang je OL, 21.10.; stromlos ≈ 3,29 MΩ gegen GND/SD = hochohmig) → eigenständige Signalleitung, unter Spannung 3,35 V; Nachtrag: 35 = DP0 des Hubs (gemessen, siehe Zeile Hub-USB-Eingang) – **kein** UART-Kandidat |
| ≈ 0,1–0,2 V schwebend | 10, 13, 26, 30, 34, 37, 38 | unbeschaltete Eingänge (T113 fehlt) – Kandidaten **T113 → Bridge (UART-TX)**, v. a. **26, 30** (Muster 26/28/30/32 zwischen GND) |
| **RS485 (bestätigt)** | **20 = 485_A (1,80), 19 = 485_B (1,70)** | Durchgang 21.10. (0,0 kΩ, Gegenprobe OL) – der RS485-Bus läuft über J14 zum Treiber auf dem T113-Board |
| **SD (bestätigt)** | **9 (2,32)** | Durchgang zu Testpunkt „SD“ 0,0 kΩ; Funktion offen (Stock-Config nutzt keine Host-Pins `rpi:`) → im Pi-Betrieb vorerst **unbeschaltet** lassen |
| **Ethernet (Durchgang 21.10.)** | **1/2 und 4/5** (je ein Leitungspaar) | zur Netzwerkbuchse J3 (Lötstifte Rückseite, eigene Zählung 1–8 im Zickzack): J3-3 ↔ 1, J3-6 ↔ 2, J3-7 ↔ 4, J3-8 ↔ 5; J3-1/2/4/5 ohne Treffer (vermutlich Mittelanzapfungen/NC). PHY sitzt auf dem T113-Board → im Pi-Betrieb ist J3 tot |
| **Kamera-USB (Durchgang 21.10.)** | **37 = DM, 38 = DP** | zu CAM-T (J16, Rückseiten-Aufdruck VBUS0/DM/DP/GND) – USB direkt vom T113. Für V2: zu USB des Pi führen (Differenzpaar 90 Ω). **VBUS0** hat keine Verbindung zu 11/12/14/15/17/45–50 → Kamera-5V wird auf dem Bridge-Board erzeugt (vermutlich Lastschalter U10/U5, ANNAHME); gemessen 21.10.: **VBUS0 = 5,23 V auch ohne T113** (Drucker an, Bezug GND 39) → Kamera wird vom Bridge-Board versorgt; für V2 genügen DM/DP (37/38) + GND zum Pi-USB, kein VBUS vom Pi nötig |
| **Hub-USB-Eingang (Durchgang 21.10.)** | **34 = DM0** (GL852G U8 Pin 25, gemessen); **35 = DP0** (U8 Pin 26, gemessen 21.10.; Ruhe 3,35 V = 1,5-kΩ-Pull-up des Hubs am Upstream-D+) | USB-Upstream des Hubs → J18, J8, CAM-X, Toolhead-USB. **34/35 sind KEINE UART-Kandidaten** (frühere Reserve-Vermutung „Paar 34/35“ damit erledigt) |
| 0 V, noch offen | 7, 8 (Paar), 22, 24 | Funktion unbekannt (nicht Hub-Upstream) |

Muster 22/24/26/28/30/32 (jeweils zwischen GND-Stiften): 28/32 Ruhe-High 3,3 V, 26/30 schwebend → typisches Bild von zwei
UART-Paaren (RX zum Host High, TX vom Host offen). Endgültige Zuordnung Main/Düse: mit dem Pi per `k2-identify`
(Pi-RX über 1 kΩ an 28 bzw. 32, Pi-TX über 1 kΩ an 26 bzw. 30 durchprobieren; alle Kandidaten ≤ 3,3 V).

RS485-Ruhepegel 22.10.2026 (Drucker an, CFS an, kein Upper-Board, Pi-UARTs verbunden; GND = Breakout 40 bzw. Adapter-GND):

| Zustand | A (B20) | B (B19) | A − B |
|---|---|---|---|
| ohne USB-RS485-Adapter | 1,788 V | 1,700 V | ≈ 0,09 V |
| mit Waveshare USB-RS232/485 isoliert (FT232RNL, 120 Ω aus; A+→20, B−→19, GND→41) | 1,956 V | 1,800 V | **0,158 V** |
| zum Vergleich Stock mit Upper-Board (§2, J22) | ≈ 3,2 V | ≈ 0,23 V | ≈ +3 V |
| mit Adapter + Versuch R11 = 10 kΩ (B45 5 V → A), R12 = 10 kΩ (B → GND B42) | 2,060 V | 1,872 V | 0,188 V |
| **mit Adapter + R11 = 1 kΩ (B45 5 V → A), R12 = 1 kΩ (B → GND B42)** | 2,515 V | 2,070 V | **0,445 V** (Rechnung ≈ 0,45 V bestätigt; Gleichtakt ≈ 2,3 V, steigt, weil der Pull-up mehr Spannungsreserve hat als der Pull-down – unkritisch, RS485 erlaubt −7…+12 V) |

Passiv 10 s am Adapter (`/dev/ttyUSB0`, 230400, nichts gesendet): 0 Byte → kein Störempfang. A−B bleibt aber unter der
200-mV-Schwelle normaler (nicht „true fail-safe“) Empfänger → Ruhe-Vorspannung (R11 A→5 V, R12 B→GND) empfohlen; Werte per
Versuch mit 10 kΩ bzw. 4,7 kΩ bestimmen (Ziel A−B ≈ 0,5–1 V). Auswertung 10-kΩ-Versuch: A und B steigen fast gleich (Gleichtakt), A−B nur +30 mV bei ≈ 0,24 mA
Bias-Strom → zwischen A und B liegt wirksam ≈ 125 Ω → **auf dem Bus sitzt offenbar ein 120-Ω-Abschluss** (Ort unbekannt, vermutlich
am fernen Ende). Rechnung: R11 = R12 ≈ 1 kΩ → A−B ≈ 0,45 V, 680 Ω → ≈ 0,58 V – **Versuch 22.10. mit 1 kΩ: 0,445 V gemessen → R11 = R12 = 1 kΩ festgelegt.** Die ≈ 3 V
beim Stock deuten darauf hin, dass das Upper-Board seinen RS485-Sender in Ruhe aktiv hält (ANNAHME).

Folgerungen RS485 (21.10.2026):
- Pi-USB-RS485-Adapter direkt am Breakout: **A → Stift 20, B → Stift 19, GND → 39–44** (oder an J22 Pin 7/1 am Mainboard).
- Ohne T113 fehlt die Failsafe-Vorspannung des Busses (mit T113 an J22: A ≈ 3,2 V, B ≈ 0,23 V; jetzt 1,80/1,70 V, A−B ≈ +0,1 V
  < 0,2 V Empfängerschwelle). OFFEN: mit angeschlossenem Pi-Adapter Ruhe-Spannung A−B messen; liegt sie unter +0,2 V,
  braucht es Bias-Widerstände (Wert erst nach Messung festlegen, nichts auf Verdacht einlöten).
- SYNC liegt **nicht** auf Stift 35 (ob SYNC überhaupt auf J14 geht, ist offen; im Pi-Betrieb läuft SYNC Toolhead → Mainboard
  ohnehin über das Bridge-Board weiter).
