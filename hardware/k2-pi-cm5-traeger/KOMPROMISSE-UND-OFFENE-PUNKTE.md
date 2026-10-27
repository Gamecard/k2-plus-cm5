# Kompromisse und offene Punkte – K2-CM5-Träger Alpha 1

Stand 24.10.2026 (Layout „Weg A“). Gesammelt aus `README.md` („Offene Punkte“, Kompromisse, Höhenbilanz,
Toolhead-Versorgung, 3D-Modelle), `KONZEPT.md` (Entscheidungen, Messungen M1–M12, Risiken) und einer Prüfung der
Fertigungsdaten am 24.10.2026. Jeder Punkt: **Risiko** – **schlimmstenfalls** – **wie prüfen**.

Kennzeichnung: **VOR BESTELLUNG** = klären, bevor bestellt wird · **AM MUSTER** = erst mit der bestückten Platine
prüfbar (Etappe in `AUFBAU-UND-INBETRIEBNAHME.md`) · **KOMPROMISS** = bewusst so entschieden · **GEKLÄRT** = in dieser
Prüfung erledigt. Schätzwerte sind als solche markiert.

## 1. Befunde dieser Prüfung (24.10.2026)

Ein **elektrischer** Layoutfehler wurde **nicht** gefunden. Gefunden wurden Unstimmigkeiten in Beschriftung und
Dokumentation. Nichts davon wurde im Layout geändert.

| Nr. | Befund | Risiko | schlimmstenfalls | Vorschlag / Prüfung |
|---|---|---|---|---|
| N1 | **Messpads der Unterseite ohne Aufdruck:** TP10 (24 V), TP11 (BR5V), TP12 (PWR_FAIL), TP2 (GND) haben nur Fab-Beschriftung; Referenz und Wert sind im Siebdruck ausgeblendet. README und Software-README sprechen von „Messpunkt 24 V“, „TP12 ‚PWR_FAIL‘“. | Verwechslung der vier gleich aussehenden Pads | falscher Messwert, Fehlersuche in die falsche Richtung; kein Schaden an der Hardware | Vor der Bestellung im Generator vier Texte auf B.SilkS ergänzen (z. B. „24V“, „5D“, „PF“, „G“) **oder** nach Lieferung von Hand beschriften (Etappe 1, Skizze `aufbau-skizzen/lage-unten.png`) |
| N2 | **Zwölf statt drei Bohrungen mit 0,2 mm:** Die README nennt drei Vias mit 0,2 mm Bohrung; die Bohrdatei enthält **12** (10 × 0,6/0,2 mm, 2 × 0,5/0,2 mm; Netze GND, LED_NACT, DSI_SCL, DSI_SDA (3×), CAN_INT, UART0_RX, I2C1_SDA, CAM_GPIO0, UART2_RX_PUF, CAN_TX). Restring 0,20 bzw. 0,15 mm. | falsche Via-Option gewählt | Aufpreis oder Verzögerung | **Kein Aufpreis:** JLC berechnet 0,2-mm-Bohrungen nur bei Durchmesser < 0,45 mm extra; Standardoption beibehalten. Restring der zwei 0,5/0,2-Vias = 0,15 mm = JLC-Minimum. README-Angabe „3 Vias“ korrigieren |
| N3 | **Software-README misst eingebaut an Unterseiten-Pads:** Schritte 13 und 16 nennen „Messpunkt 24 V (Unterseite)“, „BR_5V (Unterseite)“, „TP12“. Eingebaut liegt die Unterseite auf den Befestigungspunkten. | Messung nicht durchführbar | – | Ersatzpunkte auf der Oberseite: 24 V am **rechten Clip von F1**, Drucker-5V am **unteren Pad von R38**, PWR_FAIL nur per `k2_stromausfall --status` (so in `AUFBAU-UND-INBETRIEBNAHME.md`, Etappe 11) |
| N4 | **JLC steckt keine Jumper-Kappen.** „JP_CAN_TERM ab Werk gesteckt“ heißt: vom Nutzer zu stecken. | CAN-Bus ohne Abschluss | CAN-Fehler, sobald ein Toolhead dranhängt | Kappen kaufen (`EINKAUFSLISTE.md`), Etappe 1 Schritt 5, Messung M2.14 |
| N5 | **`ausgabe/stueckliste.csv`** führt „J9b?“, „JP_485_TERM?“, „JP_BOOT?“, „JP_CAN_TERM?“ (Fragezeichen: KiCad-Export bei Referenzen ohne Endziffer). | nur Kosmetik | – | Für JLC zählen `jlcpcb_bom.csv`/`jlcpcb_cpl.csv`, dort korrekt |
| N6 | **README „Kosten“** nennt „Abstandshalter M2,5 × 5 mm“. Bei 1,5-mm-Steckverbindern ist der Spalt Träger–CM5 genau **1,5 mm**. Außerdem gibt es DIN 7984 (niedriger Kopf) in M2,5 nicht. | Abstandshalter falscher Höhe, zu hoher Schraubenkopf unten | 5-mm-Hülsen verbiegen den CM5 bzw. die Stecker greifen nicht; Kopf > 2,45 mm drückt auf die Auflage | 1,5-mm-Nylonscheiben M2,5, Außen-Ø ≤ 4,5 mm (Bezugsquelle in Kleinmenge **OFFEN**), Schrauben M2,5 × 6 ISO 14580 oder ISO 7380 + Mutter DIN 439 (`EINKAUFSLISTE.md`, Pos. 2–4) |
| N7 | **LCSC-Angabe „Melting I²t 20,16 A²s“ für 0157003.DR ist falsch** – der Wert gehört zur trägen Variante 0157003.DR**T** (Sicherung 454). Richtig: **1,65 A²s** (Sicherung 453, 3 A). | Fehleinschätzung der Pulsfestigkeit | – | Siehe G2 unten; Rechnung mit 1,65 A²s |
| N8 | **Steckerstrom 5 V:** Hirose DF40 ist mit 0,3 A je Kontakt angegeben; 6 × 5-V-Kontakte → 1,8 A (≈ 9 W). Gilt auch für den Stecker auf dem CM5 selbst. | Erwärmung der Kontakte bei Dauer-Volllast | Kontaktalterung; im Klipper-Betrieb (typ. 0,4–0,9 A laut Datenblatt) nicht erreicht | Strom im Betrieb mit INA226 0x41 beobachten (`curr1_input`); Dauerlast > 1,8 A vermeiden |
| N9 | **Lagerbestand knapp** bei JLC (24.10.2026): J12 FH12-22S **494**, U1 MCP2518FD **960**, Y1 **1 391**, L2 **1 456**, F1 **1 500**. | Teil vor der Bestellung ausverkauft | Bestückung verzögert sich oder Teil fehlt | Bestand direkt vor der Bestellung prüfen (Tabelle G3) |
| N10 | **Platz vor Kante A** für die XT30-Kabelbuchse: J7 steht 0,1 mm über die Kante, die gesteckte Kabelbuchse ragt weitere ≈ 15–20 mm nach links, dazu der Biegeradius des Kabels (Schätzung). Gemessen ist der Platz links vom Träger noch nicht (V1.5: Messung A4.5 „offen“). | Stecker passt nicht zwischen Träger und Bridge-Board | J7 nicht steckbar oder Kabel geknickt | **VOR BESTELLUNG messen:** freier Raum links der neuen Kante A (10 mm links der alten T113-Kante) in Höhe 2–17 mm von der Oberkante, ≥ 30 mm empfohlen (Schätzung) |
| N11 | **Impedanz:** JLC-Rechner (JLC04161H-7628, mit Lötstopp) ergibt USB 0,25/0,15 mm → **88,7 Ω** (Ziel 90), Ethernet/DSI 0,20/0,20 mm → **103,5 Ω** (Ziel 100) – innerhalb ± 10 % (siehe G4). | gering | bei den kurzen Wegen (≤ 6,3 cm) und 100 Mbit/s bzw. USB 2.0 praktisch folgenlos | Impedanzkontrolle mitbestellen, Ziele im Remark angeben; Layout nicht ändern |
| N12 | **Übertrager T1, Paar 0 durchgängig invertiert** (CM5 ETH0_P → Pin 8 = RD− → RX− → J1 Pin 5). Im Generator als „bewusst P/N getauscht“ vermerkt. | keine | – | 100BASE-TX ist polaritätsunabhängig (MLT-3), 10BASE-T korrigiert der PHY selbst – kein Handlungsbedarf |
| N13 | **Standard-Bestückung bei JLC verlangt ≥ 70 mm je Seite**; die Platine hat 56,1 mm → JLC setzt Fertigungsränder an. J1/J7 ragen 0,1–0,3 mm über Kante A, J11 über Kante D, J9b/J10/J12 liegen an der Kante (JLC: Bauteil ≥ 2,5 mm von der Kante, sonst Rand). | Rand stört die überstehenden Stecker; Vorrichtung nötig | ≈ 16 USD Aufpreis, Stege an der Kante nachfeilen | Remark „routed gap + mouse bites, no V-cut“, „Confirm Production File“ prüfen |
| N14 | **MSOP-10 (U4/U5):** kleinster Kupferabstand zwischen Nachbarpads 0,150 mm = JLC-Minimum (0,15 mm) – Standard-Footprint aus der KiCad-Bibliothek. Alle übrigen Bauteile ≥ 0,2 mm. | Lötbrücke | INA226 liefert falsche Werte | Sichtprüfung Etappe 1; JLC fertigt MSOP-10 routinemäßig |

## 2. Vor der Bestellung klären

| Nr. | Punkt | Risiko | schlimmstenfalls | wie prüfen / entscheiden |
|---|---|---|---|---|
| V1 | **Freiraum links von Kante A** (N10), Flachkabel-Weg (KONZEPT M7: Original-FFC 4,6 cm, durch die 10-mm-Verlängerung kürzerer Weg) | XT30-Stecker oder Flachkabel passen nicht | Platine passt mechanisch nicht | Am Drucker messen (Messschieber, Tiefenmaß) |
| V2 | **24-V-Abgriff** (KONZEPT M10): freie Klemmen +V/−V am Netzteil CHP-350-24, Schraubengröße, Kabelweg und Länge bis zur Display-Einheit | falscher Kabelschuh, zu kurzes Kabel | Nacharbeit am Kabel | Klemmenschraube und -breite messen, Kabelweg mit Schnur abmessen (+20 cm) |
| V3 | **Richtung der 10-mm-Verlängerung** (KONZEPT M1) | Umriss passt nicht in den Bauraum | Platine unbrauchbar | Falls noch nicht geschehen: Schablone 1:1 drucken (Umriss 90 × 56,1 mm, Bohrungen L1–L4) und am T113-Platz anlegen |
| V4 | **Höhe über der Auflage** (KONZEPT M4, A3 = 15,55 mm inkl. Karton, Planung 11,5 mm) | höchste Teile: Stiftleisten 10,1 mm, CM5 + Kühlkörper 11,5 mm | Gehäuse schließt nicht | A3.0 (Kartondicke) messen; Kühlkörper ≤ 4,96 mm |
| V5 | **Via-Option 0,2 mm** (N2) | falsche Option gewählt | Aufpreis oder Verzögerung | geklärt: kein Aufpreis, Standardoption |
| V6 | **Impedanzkontrolle** ja/nein | ohne Option kein garantierter Lagenaufbau | Impedanz weicht stärker ab | Empfehlung: JLC04161H-7628 mit Impedanzkontrolle bestellen |
| V7 | **Bestückungsumfang:** 2 von 5 Platinen bestücken (empfohlen), J7 selbst löten oder von JLC | Kosten | – | Entscheidung je nach Bedarf |
| V8 | **Display:** Einbauort des Waveshare-Displays, Kabellänge (12 cm im Lieferumfang, sonst 200 mm Raspberry-Pi-Kabel), Lage von CON1 an der T113 nicht vermessen | Kabel zu kurz | Nachkauf | Weg J12 → Display am Gerät abmessen |
| V9 | **WLAN-Antenne:** interne PCB-Antenne des CM5 oder externe U.FL-Antenne (KONZEPT §7.3, Messung M6). `dtparam=ant2` steht **nicht** im `config.txt`-Block der Software | schwaches WLAN in der Display-Einheit (Metall in der Nähe?) | Verbindungsabbrüche | Entscheiden; bei externer Antenne Software ergänzen |
| V10 | **Kühlkörper** ≤ 4,96 mm inkl. Pad, passend zum Lochbild – **kein Fertigprodukt gefunden** (offizieller CM5-Kühler 12,7 mm, Waveshare 5 mm ohne Pad) | zu hoch oder schlechte Kühlung; Kupferplatte über der PCB-Antenne verschlechtert das WLAN | Drosselung bei 85 °C, WLAN-Abbrüche | Notlösung Kupferplatte 40 × 40 × 2–3 mm + Pad 0,5 mm (`EINKAUFSLISTE.md`, Pos. 1a/1b), nicht über die Antennenecke legen; Höhe nachmessen; Temperatur beobachten (M12) |
| V11 | **Bauteilbestand** (N9) | Teil ausverkauft | Verzögerung | Tag der Bestellung prüfen |
| V12 | **N1 Beschriftung** der Unterseiten-Pads im Generator nachziehen? | – | – | Entscheidung: Generator ändern und neu bauen **oder** von Hand beschriften |

## 3. Am Muster prüfen

| Nr. | Punkt | Risiko | schlimmstenfalls | wie prüfen |
|---|---|---|---|---|
| M1 | **XT30-Polarität** (README Offener Punkt 3): J7 Pin 2 = +24 V, Pin 1 = GND nach Footprint/Datenblatt; gegen Gehäuseaufdruck und Kabel nicht geprüft | verpoltes Kabel | D8 leitet, F1 löst aus (gewollt), im Pech auch D8 oder Leiterbahn beschädigt | Etappe 3.2 (Durchgang Ader ↔ F1-Clip) und 3.3 (Spannung am Stecker) |
| M2 | **D8 richtig herum** | 24 V über D8 kurzgeschlossen | F1 löst aus | Etappe 2, Diodentest M2.16/M2.17 |
| M3 | **Flachkabel J1 gespiegelt** (Spiegelfall, `ausgabe/spiegelfall.txt`): J1 hat Kontakte oben und unten, das Kabel passt auch gespiegelt. Drucker-5V landet dann auf Ethernet (über T1 getrennt, sicher), 5-V-Signale/GND auf den USB-Leitungen von Kamera und Hub | USB-Eingänge des CM5 bekommen 5-V-Pegel | Schaden am USB-Teil des CM5 (USB 2.0 fordert Kurzschlussfestigkeit gegen VBUS – für den CM5 nicht belegt) | Etappe 7: Orientierung **ohne CM5** prüfen, D10 leuchtet = richtig |
| M4 | **D10 „an = Kabel OK“**: rote LED über 2,2 kΩ an Drucker-5V (≈ 1,4 mA) | LED zu dunkel oder verdreht | falsche Schlussfolgerung | Etappe 7: zusätzlich Spannung am unteren Pad von R38 (M7.1) |
| M5 | **J9b/J11 (JST GH stehend):** Pin 1 liegt räumlich anders als bei der liegenden Variante; Siebdruck „H L G“ bzw. „G B A“ = Reihenfolge von links nach rechts | Kabel mit vertauschten Adern | CAN/RS485 ohne Funktion (CANH/CANL vertauscht), kein Schaden (keine Versorgung an diesen Steckern) | Durchgang Kabelader ↔ Lötfahne vor dem ersten Stecken |
| M6 | **DSI J12:** Kontaktseite/Pin 1 mit dem Pi-5-Kabel, `vc4-kms-dsi-7inch` an DSI1, nur 2 Lanes | kein Bild | – (falsche Kontaktseite berührt nur Isolierung) | Etappe 9.1 und 9.3 |
| M7 | **Touch-Controller** (Waveshare: Goodix 0x14 laut Wiki, Overlay bringt EDT-FT5406-Treiber) | Touch geht nicht ohne Zusatz | Software-Anpassung (Overlay) | Etappe 9.3 (`evtest`, `i2cdetect -y 0`) |
| M8 | **Ethernet über 2 Paare und die Bridge-Buchse** (KONZEPT §6): CM5-Gigabit-PHY muss auf 100 Mbit/s zurückfallen; Mittelanzapfungen auf dem Träger | kein Link | Ethernet ohne Funktion, WLAN bleibt | Software-README Schritt 15 (`ethtool`), notfalls `advertise 0x008` |
| M9 | **Haltezeit nach Stromausfall** unbekannt (Annahme: einige 10 ms), Supercap-Puffer nicht umgesetzt (README Punkt 12) | `sync` nicht fertig, bevor der CM5 ausgeht | Dateisystemschaden bei hartem Ausschalten (abgemildert durch `data=journal` und read-only Root, Software-README Teil C) | Etappe 12 (Oszilloskop oder Log). Reicht es nicht: externes Puffermodul (24 V) vor J7 oder Hochsetzsteller hinter Supercaps (README Punkt 12) |
| M10 | **J10 USB-C stehend** (KONZEPT M11): Erreichbarkeit im Gehäuse | Notfall-USB nur bei offenem Gehäuse | – (bewusst so akzeptiert) | beim Einbau ansehen |
| M11 | **J9b/J11 gesteckt** ≈ 13,6 mm über der Auflage (Budget 11,5 mm) | Deckel/Display drückt auf den Stecker | Kontakt abgeknickt | nur stecken, wo Platz ist; sonst Kabel direkt am Stecker umlegen |
| M12 | **Wärme** in der Display-Einheit (KONZEPT §3.3, M9), Kühlkörper nur ≤ 4,96 mm | SoC drosselt bei 85 °C | langsamere Antworten, kein Schaden | `vcgencmd measure_temp` im Druckbetrieb mit Kammerheizung |
| M13 | **Notfall-USB-C Rückspeisung**: TPS2121 schaltet nie beide Quellen parallel | Strom in den PC | – | Etappe 5 (M5.1/M5.2) |
| M14 | **5-A-Freigabe ohne USB-PD** (`PSU_MAX_CURRENT=5000`, Software-README Schritt 5): laut Bootloader-Doku „Raspberry Pi 5 only“ formuliert | CM5 meldet schwaches Netzteil, USB-Strom gedrosselt | Kamera/Hub (vom Bridge-Board versorgt) nicht betroffen; nur Warnungen | `vcgencmd get_config usb_max_current_enable`, `dmesg` nach Unterspannungs-/Netzteilwarnungen |
| M15 | **RS485-Ruhevorspannung** hängt an den Drucker-5V (R15 über das Flachkabel) | ohne Drucker-5V keine Vorspannung | nur ohne laufenden Drucker relevant | Software-README Schritt 16 (A über B > 0,2 V) |

## 4. Bewusste Kompromisse

| Nr. | Kompromiss | Risiko | schlimmstenfalls | wie prüfen |
|---|---|---|---|---|
| K1 | **Eingangssicherung F1 3 A flink** (Halter 157 gibt es nur mit flinker Sicherung 453; träge 452 als Ersatz) | Auslösen beim Stecken unter Spannung | F1 tauschen (Ersatz liegt bereit) | G2: Einschaltpuls ≈ 2–4 % des Schmelz-I²t – unkritisch. Fliegt sie trotzdem: träge 0452003.MRL einsetzen |
| K2 | **Kein mechanischer Verdrehschutz** am Flachkabel, nur Anzeige D10 | siehe M3 | siehe M3 | Etappe 7 |
| K3 | **D1/D2/D3 (USBLC6) mit offenem VBUS-Pin** | etwas schwächerer ESD-Schutz (die interne Zener-Klemme wirkt über die Steuerdioden weiter) | ESD-Schaden bei grober Behandlung | – (Ziel: keine Rückspeisung in die CM5-3,3 V bei verdrehtem Kabel) |
| K4 | **INA226 U5 misst VBUS direkt an IN−** (ohne RC-Filter) | etwas mehr Rauschen in der 5-V-Messung | ungenauerer Wert | Vergleich INA226 0x41 mit Multimeter an TP3 |
| K5 | **Innenlage L3 = 3,3-V-Fläche** mit eingelegten 5-V- und 24-V-Bahnen; Freerouting nur auf L1/L4; Leistungsnetze im Router 0,25 mm, Strom über Kupferflächen | Rückstrompfade von L4-Signalen über Schlitze in L3; Erwärmung schmaler Stücke | Störungen auf langsamen L4-Signalen; bei Weg A nur ≈ 0,6 A auf 24 V (≤ 1,3 A ungünstigster Fall) | Betrieb beobachten; Temperatur am Wandlerbereich fühlen |
| K6 | **XT30 J7 ist das einzige THT-Teil** (Hand oder JLC-THT) | schlechte Lötstelle | Wackelkontakt im 24-V-Weg | Etappe 1 Schritt 3, M2.3/M2.4 |
| K7 | **Steckbrücken als stehende SMD-Stiftleisten** (8,5 mm, höchste Teile) | Abbrechen beim Hantieren | Jumper-Funktion fehlt | vorsichtig behandeln |
| K8 | **Kamera-USB-Paar unter F1** östlich an J7 vorbei; Eingangsvias unter dem Sicherungshalter (abgedeckt) | Kopplung 24 V ↔ USB (gering) | – | `lsusb`, Kamera-Bild |
| K9 | **Weg A:** kein 24-V-Ausgang für ein Toolhead; Toolhead direkt am Netzteil mit eigener Kabelsicherung ≈ 5 A; CAN über J9b | Toolhead-Kabel ohne Sicherung gebaut | Kabelbrand im Kurzschlussfall | Kabelsicherung 5 A Pflicht (`EINKAUFSLISTE.md`, optional später) |
| K10 | **DSI nur 2 Lanes** (D2/D3 nicht verlegt) | keine Displays mit 4 Lanes | – | Waveshare 4,3" nutzt 2 Lanes |
| K11 | **Ethernet nur 100 Mbit/s** (2 Paare über das Bridge-Board) | – | – | M8 |
| K12 | **Flashen normalerweise am IO-Board**, J10 + JP_BOOT nur Notweg | – | – | Etappe 9b |
| K13 | **F3 (PTC 0,5 A)** in den GND-Adern des Flachkabels | fehlt die GND-Ader von J7, fließt Rückstrom über das Flachkabel bis 0,5 A | PTC heizt, löst aus | Etappe 3.2 (M3.2) |
| K14 | **3D-Modelle** von LCSC/EasyEDA nur lokal (Lizenz ungeklärt), CM5 als Quader | nur Darstellung | – | – |
| K15 | **Supercap-Puffer nicht umgesetzt** (kein Platz, zu hoch, Energie zu klein) | siehe M9 | siehe M9 | Etappe 12 |

## 5. In dieser Prüfung geklärt

### G1 – Übertrager H1102NLT: Pinbelegung und Footprint

Quelle: Pulse H1102NL Datenblatt, Rev. A
([productfinder.pulseeng.com](https://productfinder.pulseeng.com/doc_type/WEB301/doc_num/H1102NL/doc_part/H1102NL.pdf));
Vergleich Bourns PT61017PEL ([bourns.com](https://www.bourns.com/docs/product-datasheets/pt61017pel.pdf)) – gleiche
Belegung.

| Pin (Datenblatt) | Funktion | Netz im Schaltplan | Bewertung |
|---|---|---|---|
| 1 / 2 / 3 | TD+ / CT / TD− | ETH1_P / ETH_CT_PHY / ETH1_N | passt |
| 6 / 7 / 8 | RD+ / CT / RD− | ETH0_N / ETH_CT_PHY / ETH0_P | Paar 0 invertiert (N12, unkritisch) |
| 16 / 15 / 14 | TX+ / CT / TX− (Leitung) | ETH_A_P / ETH_CT_A / ETH_A_N (J1 1/–/2) | passt |
| 11 / 10 / 9 | RX+ / CT / RX− (Leitung) | ETH_B_P / ETH_CT_B / ETH_B_N (J1 4/–/5) | passt |
| 4, 5, 12, 13 | nicht belegt | offen | passt |

- PHY-seitige Mittelanzapfungen 2/7 gemeinsam mit 100 nF (C27) nach GND – passend für den spannungsgesteuerten PHY des
  CM5 (wie CM4-IO-Board); leitungsseitig 15/10 über 75 Ω (R36/R37) an 1 nF/2 kV (C28, Bob-Smith).
- Footprint `Uebertrager_SMD_H1102NLT_12.7x7.1mm` (aus den LCSC/EasyEDA-Daten): Pads 0,76 × 1,66 mm, Raster 1,27 mm,
  Reihenmitte 8,64 mm; Datenblatt empfiehlt 0,76 × 1,65 mm und 8,51 mm. Jedes Pad liegt 0,06 mm weiter außen – über die
  Pinspitzen (9,40 ± 0,25 mm) ragen sie ≈ 0,45 mm hinaus, unter dem Körper (7,11 mm) bleiben sie 0,07 mm frei.
  Unkritisch. Pin 1 unten links, Zählung gegen den Uhrzeigersinn – passt.
- **Ergebnis: kein Fehler.** README Offener Punkt 5 ist erledigt.

### G2 – Sicherung F1: Schmelz-I²t gegen Einschaltstrom

Quellen: Littelfuse 451/453 Datenblatt
([CERN-Kopie](https://sy-dep-epc-lpc.web.cern.ch/components/datasheets/epc-lpc%20(converters)/451-453_Fuse_(LITTLEFUSE).pdf)),
452/454 ([LCSC-Kopie](https://atta.szlcsc.com/upload/public/pdf/source/20260608/56C5B04306599EC36D161AD164B470F4.pdf)),
157 ([Datenblatt-Kopie](https://cdn.eicom.ru/media/PDF/951438.pdf)), Fuseology Design Guide S. 8
([littelfuse.com](https://info.littelfuse.com/hubfs/Fuseology%20Design%20Guide.pdf)).

| Größe | 0157003.DR (bestückt, Sicherung 453 3 A, sehr flink) | 0452003.MRL (Ersatz, träge) |
|---|---|---|
| Schmelz-I²t (8 ms) | **1,65 A²s** | **20,16 A²s** |
| Kaltwiderstand | 0,0229 Ω | 0,0340 Ω |
| 200 % In | ≤ 5 s | 1–60 s |
| Abschaltvermögen | 50 A @ 125 V AC/DC, 300 A @ 32 V DC | 50 A @ 125 V AC/DC, 300 A @ 32 V DC |
| zulässiger Puls-I²t für 100 000 Pulse (22 %) | 0,36 A²s | 4,4 A²s |

- Einschaltpuls laut README ≈ 0,03–0,06 A²s (Schätzung aus C·U²/2R) = **2–4 %** des Schmelz-I²t der flinken Sicherung –
  weit unter der 22-%-Grenze für 100 000 Schaltspiele. **Die flinke 3-A-Sicherung passt.**
- Dauerstrom: Littelfuse empfiehlt für Sicherungen 25 % und für Halter 40 % Abwertung → 3 A × 0,6 = 1,8 A ≥ 1,3 A
  (ungünstigster Fall) bzw. ≈ 3-fache Reserve zum Normalbetrieb (0,6 A).
- LCSC nennt für 0157003.DR fälschlich 20,16 A²s (N7).

### G3 – Bauteilbestand bei JLC (abgefragt 24.10.2026, JLC-Teilesuche)

| Teil | LCSC | Typ | JLC-Lager | Bedarf je Platine |
|---|---|---|---|---|
| FH12-22S-0.5SH(55) (J12) | C597981 | Extended | **494** | 1 |
| MCP2518FDT-H/SL (U1) | C626759 | Extended | **960** | 1 |
| S3240000151040 40 MHz (Y1) | C426990 | Extended | 1 391 | 1 |
| WPN252010H2R2MT (L2) | C97031 | Extended | 1 456 | 1 |
| 0157003.DR (F1) | C187520 | Extended | 1 500 | 1 |
| HoLLR2512 10 mΩ (R24) | C2962784 | Extended | 3 948 | 1 |
| SMFJ28A (D8) | C5439963 | Extended | 4 062 | 1 |
| SRV05-4 (D4) | C5240367 | Extended | 5 196 | 1 |
| THVD1450DR (U3) | C2671361 | Extended | 5 217 | 1 |
| alle übrigen | – | Basic/Extended | > 7 000 | – |
| Ersatz 0452003.MRL / 0453003.MR | C99550 / C178991 | – | 20 407 / 1 414 | Einkaufsliste |

Alle 53 Sorten waren lieferbar (17 Basic, 34 Extended, 2 Preferred Extended). Vollständige Liste:
`ausgabe/jlcpcb_bom.csv`.

### G4 – Impedanzen (Abschätzung)

Lagenaufbau laut Job-Datei: L1 35 µm Kupfer, Prepreg 7628 **0,2104 mm** (εr ≈ 4,4), L2 GND. Gekoppelte
Microstrip-Paare auf L1.

| Paar | Bahn / Abstand | IPC-2141 (grob) | Kirschning-Jansen ohne Lötstopp | eigene Schätzung mit Lötstopp | **JLC-Rechner** (mit Lötstopp, Ätzflanke) | Ziel |
|---|---|---|---|---|---|---|
| USB (CAM, HUB, OTG) | 0,25 / 0,15 mm | 92 Ω | 100 Ω | ≈ 92–97 Ω | **88,7 Ω** | 90 Ω |
| Ethernet, DSI | 0,20 / 0,20 mm | 109 Ω | 115 Ω | ≈ 105–110 Ω | **103,5 Ω** | 100 Ω |

- Eigene Rechnung nach IPC-2141 und Kirschning-Jansen (gekoppelte Microstrip, εr 4,1–4,6 ändert ± 2–3 Ω); der
  Lötstopp-Abschlag war geschätzt und fiel zu klein aus. **Maßgeblich ist der JLC-Rechner** (Abfrage 24.10.2026 über
  [jlcpcb.com/pcb-impedance-calculator](https://jlcpcb.com/pcb-impedance-calculator), Modell „differential edge-coupled
  coated microstrip“): genau auf Ziel kämen 0,24/0,15 mm (90,05 Ω) und 0,22/0,20 mm (99,9 Ω).
- Bewertung: Abweichung −1,5 % bzw. +3,5 %. Die Leitungen sind kurz (USB ≤ 6,3 cm, Ethernet CM5 → T1 → J1 ≤ 4,6 cm, DSI 5,0 cm); das
  Flachkabel und das Bridge-Board liegen ohnehin außerhalb jeder Impedanzkontrolle. **Kein Grund, das Layout zu ändern.**
  Beide Paare liegen innerhalb der JLC-Toleranz von ± 10 %.

### G5 – Pegel der CM5-Steuerpins (für die Messungen)

Quelle: Raspberry Pi CM5 Datasheet ([datasheets.raspberrypi.com](https://datasheets.raspberrypi.com/cm5/cm5-datasheet.pdf)).
PWR_Button (Pin 92) 10 kΩ nach 5 V, nRPIBOOT (93) 10 kΩ nach 3,3 V, PMIC_Enable (99) 100 kΩ nach 5 V; 5-V-Eingang
4,75–5,25 V (abs. max. 6,0 V), typ. 0,4 A Leerlauf / 0,9 A Betrieb. Eingetragen in Etappe 9 (M9.3–M9.5).
