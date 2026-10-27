# Einkaufsliste – alles, was nicht von JLCPCB kommt

Stand 24.10.2026. Für den K2-CM5-Träger Alpha 1. Preise brutto in Euro, „ca.“, Stand des Recherchetags; **„(unsicher)“**
= nur aus einem Suchtreffer, nicht auf der Produktseite bestätigt; **netto** = ohne MwSt. (DigiKey, RS). Händler sind
Beispiele, keine Empfehlung. **OFFEN** = kein belegbares Produkt bzw. kein belegbarer Preis gefunden – nichts davon ist
geraten.

Der CM5 (Rev 1.0, 4 GB, 64 GB eMMC, WLAN) ist vorhanden und wird weiterverwendet.

## 1. Für den Betrieb nötig

### 1.1 Kühlung und Befestigung des CM5

| Pos. | Was | Menge | Spezifikation | Beispielquelle | Preis ca. |
|---|---|---|---|---|---|
| 1 | Kühlkörper **≤ 4,96 mm inkl. Wärmeleitpad** | 1 | passiv, flach, liegt auf dem SoC (BCM2712, mit 2,2 mm das höchste Bauteil des CM5); darf die vier Eck-Bohrungen (Lochbild 48 × 33 mm) nicht überdecken bzw. nur mit Durchgangslöchern; nicht über der WLAN-Antenne des CM5 | **Fertigprodukt: OFFEN** – alle gefundenen sind zu hoch: offizieller „Raspberry Pi CM5 Passive Cooler“ SC1752 56 × 41 × **12,7 mm**, Waveshare CM4-Heatsink **5 mm ohne Pad**, 52Pi/Geekworm 15–16 mm | – |
| 1a | Notlösung: Kupferplatte | 1 | 40 × 40 × **2–3 mm** (mit Pad 2,5–3,5 mm hoch); mittig, lässt die Eckschrauben frei (≈ 43 mm Platz zwischen den Schraubenköpfen in Längsrichtung); Kanten entgraten | [ebay.de/itm/157227976824](https://www.ebay.de/itm/157227976824) | 3,94 € (unsicher) |
| 1b | Wärmeleitpad | 1 | 0,5 mm, ≥ 6 W/mK, auf die Größe des SoC zuschneiden (am Modul messen) | Thermal Grizzly Minus Pad 8, 30 × 30 × 0,5 mm, [aquatuning.com](https://www.aquatuning.com/it/thermal-conductive-material/thermal-pad/thermal-grizzly-minus-pad-8-30-30-0-5-mm) | 4,90 € |
| 2 | Abstandsscheiben zwischen Träger und CM5 | 4 (+ 2 Reserve) | **M2,5, Höhe 1,5 mm, Außen-Ø ≤ 4,5 mm**, Nylon (Raspberry Pi verwendet beim CM5-Kühler „M2.5 × H1.5 nylon washers“) | **Kleinmenge: OFFEN.** Möglichkeiten: (a) die Nylonscheiben aus dem Lieferumfang des offiziellen CM5 Passive Cooler SC1752 ([berrybase.de](https://www.berrybase.de/raspberry-pi-compute-module-5-passive-cooler), 5,50 €; Außen-Ø **nicht angegeben – messen**, Kühler selbst ist zu hoch); (b) Distanzhülsen 2,6 × 4,5 × 1,5 mm Messing (artcomponents 30496) bzw. 2,7 × 4,5 × 1,5 mm Alu (50056), Mindestmenge 100/1000, Preis OFFEN ([Katalog-PDF](https://artcomponents.com/media/static/pdf/Metall-Distanzhuelsen(DH10-13).pdf)) | 5,50 € (a) |
| 3 | Schrauben M2,5 mit **flachem Kopf** | 4 | **M2,5 × 6**, Kopf ≤ 5,5 mm Ø und ≤ 2,0 mm hoch: ISO 14580 (niedriger Zylinderkopf, 4,5 × 1,85 mm, TX8) oder ISO 7380 (Flachrundkopf, 4,6 × 1,5 mm). DIN 7984 gibt es in M2,5 nicht. **Kein** DIN 912 (Kopf 2,5 mm > 2,45 mm Luft) | CNC Kitchen ISO 14580 M2,5 × 6 A2, 25 Stück, [3djake.de](https://www.3djake.de/cnc-kitchen/m25-zylinderschrauben-niedriger-kopf-iso-14580-edelstahl-a2-70) | 6,29 € |
| 4 | Muttern M2,5 flach | 4 | **DIN 439** (Höhe 1,6 mm), Edelstahl A2. Länge: Träger 1,6 + Scheibe 1,5 + CM5 1,24 + Mutter 1,6 = 5,94 mm → M2,5 × 6 | RS PRO 122-4399, 100 Stück, [rs-online.com](https://at.rs-online.com/web/p/sechskantmuttern/1224399) | 3,72 € netto (unsicher) |

Hinweis zu Pos. 2: Würth WA-SMSI 9774015151R (Einlötbolzen M2,5 × 1,5 mm) passt **nicht**: Außen-Ø 5,1 mm und er braucht
ein Lötpad – die CM5-Bohrungen des Trägers sind ohne Kupfer (Ø 2,7 mm).

### 1.2 24-V-Kabel vom Drucker-Netzteil (Creality CHP-350-24) zu J7

Strom ≈ 0,6 A im Betrieb, ≤ 1,3 A im ungünstigsten Fall; 0,5 mm² würde reichen, empfohlen **0,75 mm² (AWG 18)**, weil das
Netzteil bis 14,6 A liefern kann und das Kabel bis zur Sicherung F1 auf dem Träger ungeschützt ist.

| Pos. | Was | Menge | Spezifikation | Beispielquelle | Preis ca. |
|---|---|---|---|---|---|
| 5 | XT30-Kabelbuchse | 2 (1 fürs Drucker-Kabel, 1 fürs Labornetzteil-Kabel) | **XT30U-F** bzw. XT30-F (Buchse zum Löten) = Gegenstück zu XT30PW-M; **nicht** XT30PW-F/XT30UPB-F (Platinenbuchsen) | AMASS XT30 Buchse, [berrybase.de](https://www.berrybase.de/amass-xt30-weiblicher-stecker-fuer-dc-stromversorgung-loetanschluss-15a-nennstrom) (Lagerbestand gering) | 0,60 €/Stk. |
| 5a | Alternative: XT30-Buchse mit Kabel | 1–2 | 150 mm, 16 AWG (dann mit 0,75 mm² verlängern) | Pichler 15007, [pichler.de](https://pichler.de/XT30-femal-plug-w-cable) | 1,95 € |
| 6 | Litze rot + schwarz | je **Länge messen** + 20 cm | 0,75 mm² (AWG 18), feindrähtig (Silikon- oder PVC-Litze), für 24 V | **OFFEN** (Fachhandel, Meterware) | OFFEN |
| 7 | Gabelkabelschuhe isoliert, rot | 4 (2 + 2 Reserve) | 0,5–1,5 mm²; **Schraubengröße der Netzteilklemme messen** (nicht dokumentiert) | M3: VOGT 3605C, [reichelt.de](https://www.reichelt.de/de/de/shop/produkt/gabel-kerbschuhe_fuer_m3_rot-231363), 0,15 €; M4: Würth K404040HS mit Schrumpfschlauch, [reichelt.de](https://www.reichelt.de/de/de/shop/produkt/gabelkabelschuh_mit_schrumpfschlauch_-_0_5_1_5_mm_m4_rot-189425), 0,48 €; M3,5: OFFEN | 0,15–0,48 €/Stk. |
| 8 | Schrumpfschlauch | 1 Sortiment | 3:1 mit Kleber, für XT30-Lötkelche und Litzen | QUAD 201711CA003, 100 Teile, [reichelt.de](https://www.reichelt.de/de/de/schrumpfschlauch-box-hohe-schrumpfrate-3-1-100-teilig-quad-201711ca003-p280196.html) | 9,15 € |

### 1.3 Kleinteile für die Platine

| Pos. | Was | Menge | Spezifikation | Beispielquelle | Preis ca. |
|---|---|---|---|---|---|
| 9 | Jumper (Kurzschlussbrücken) | 10 | Raster 2,54 mm, für JP_CAN_TERM (gesteckt), JP_485_TERM, JP_BOOT; JLC liefert **keine** Kappen | Reichelt „JUMPER 2,54 SW“, [reichelt.de](https://www.reichelt.de/de/de/shop/produkt/kurzschlussbruecke_schwarz_rm_2_54-9017) | 0,02 €/Stk. |
| 10 | Ersatzsicherung **träge** | 5 | Littelfuse **0452003.MRL**, Nano² 2410, 3 A Slo-Blo, 125 V (LCSC C99550) – für den Fall, dass die flinke beim Stecken unter Spannung auslöst | [reichelt.de](https://www.reichelt.de/de/de/shop/produkt/smd-sicherungen_melf_6125_traege_3_0_a_serie_452-228988) (LCSC 0,27 USD) | 1,15 €/Stk. |
| 11 | Ersatzsicherung **flink** (wie bestückt) | 5 | Littelfuse **0453003.MR**, Nano² 2410, 3 A Very Fast, 125 V (LCSC C178991) | DigiKey F3138CT-ND, [digikey.de](https://www.digikey.de/de/products/result?keywords=0453003.MR) (LCSC 0,25 USD; Reichelt führt sie nicht) | 1,97 € netto/Stk. |

### 1.4 Display

| Pos. | Was | Menge | Spezifikation | Beispielquelle | Preis ca. |
|---|---|---|---|---|---|
| 12 | Waveshare **4.3inch DSI LCD** (SKU 16239) | 1 | 800 × 480, kapazitiver Touch, 3,3 V ≈ 1,2 W; **Revision 2.2 nötig** (Rev 2.1 und älter starten an Pi 5/CM5 nicht) – kein deutscher Händler nennt die Revision, **vor dem Kauf nachfragen**. Laut Waveshare liegen ein 15-pol. 50-mm-Kabel, ein 15-pol. 160-mm-Kabel und ein „DSI-Cable-12cm“ (22↔15-pol.) bei; deutsche Händler listen nur das 50-mm-Kabel | Welectron, [welectron.com](https://welectron.com/Waveshare-16239-43inch-DSI-LCD) (im Zulauf); Berrybase 37,90 € (nicht lieferbar) | 39,90 € |
| 13 | Raspberry Pi **Display Cable Standard–Mini 200 mm** | 1 | **SC1131** (22-pol. 0,5 mm ↔ 15-pol. 1,0 mm; **nicht** SC1128 = Kamerakabel). Nur nötig, wenn 12 cm nicht reichen oder das Kabel nicht beiliegt | [berrybase.de](https://www.berrybase.de/en/raspberry-pi-display-cable-standard-mini-200mm) | 1,20 € |

## 2. Optional

| Pos. | Was | Menge | Spezifikation | Beispielquelle | Preis ca. |
|---|---|---|---|---|---|
| 14 | WLAN-Antenne | 1 | Raspberry Pi **Antenna Kit SC0480** (U.FL am CM5). Braucht `dtparam=ant2` in `config.txt` – **steht nicht** im Software-Paket des Trägers (Entscheidung V9 in `KOMPROMISSE-UND-OFFENE-PUNKTE.md`) | [welectron.com](https://www.welectron.com/Raspberry-Pi-Compute-Module-CM4-CM5-Antenna-Kit_1) | 4,90 € |
| 15 | JST-GH-Kabel 3-pol. | 2–3 | für J9b (CAN) und J11 (RS485-Abgriff), erst später nötig. Fertigkabel (Pinbelegung/Hersteller offen) **oder** Original-JST: Gehäuse GHR-03V-S + vorgecrimpte Einzeladern AGHGH28K203 (203 mm) → keine Crimpzange nötig. Vor dem ersten Stecken Ader ↔ Kontakt durchmessen (Siebdruck „H L G“ bzw. „G B A“ von links) | Funduino F23110493, 150 mm, [funduino.com](https://funduino.com/en/products/master-steckkabel-jst-gh1-25mm-stecker-150mm-kabel-28awg-2p-12p), 0,68–0,75 €; JST bei [digikey.de](https://www.digikey.de/de/products/result?keywords=AGHGH28K): Gehäuse 0,09 € netto + Ader 0,44 € netto | 0,7–1,5 €/Kabel |
| 16 | Reserve-Flachkabel 50-pol. | 1–2 | FFC 0,5 mm, **Typ A** (Kontakte gleiche Seite); **Länge erst messen** (Original 4,6 cm, KONZEPT M7). Nicht Molex 0151660538/0150200532 (Kontakte gegenüber) | GCT 05-50-A-0050-A-4-06-4-T (50 mm), [digikey.de](https://www.digikey.de/de/products/detail/gct/05-50-A-0050-A-4-06-4-T/21266595); Würth 687650100002 (100 mm) 3,26 € | 1,50 € |

### 2.1 Für später: Toolhead über CAN (Weg A)

| Pos. | Was | Menge | Spezifikation | Beispielquelle | Preis ca. |
|---|---|---|---|---|---|
| 17 | Toolhead-24-V-Leitung | Länge messen | 18 AWG (0,75–1 mm²) rot/schwarz, vom Netzteil (freie Klemme) zum Toolhead, **nicht** über den Träger | OFFEN | OFFEN |
| 18 | Inline-Sicherungshalter + Sicherung **5 A** | 1 | in der +24-V-Ader nahe am Netzteil; Flachsicherung (KFZ, ATO/Mini) oder 5 × 20 mm träge; Nennspannung ≥ 32 V DC am Datenblatt prüfen | Pollin S1038-3 (Standard-Flachsicherung, Leitung ≈ 2 mm²), [pollin.de](https://www.pollin.de/p/sicherungshalter-s1038-3-260546), 0,99 € + Sicherungssatz mit 20 × 5 A (Voelkner) 12,99 €; Halter für 5 × 20 mm bzw. Littelfuse 0FHM0003ZXJS: OFFEN | ≈ 14 € |
| 19 | CAN-Kabel J9b → Toolhead | 1 | JST GH 3-pol. (CANH, CANL, GND) am Träger, verdrillt bis zum Toolhead, Stecker am anderen Ende passend zum Toolhead | Teile wie Pos. 15 | – |
| 20 | Cartographer (CAN) | 1 | **V3 ist ausverkauft und durch V4 abgelöst** (V4: CAN oder USB per Firmware, Stecker Molex Sherlock, **ohne** GH-Kabel). Versorgung laut Herstellerdoku prüfen – über J9b kommen **keine** 24 V | Meltbro (DE), [meltbro.de](https://meltbro.de/cartographer-v4-high-temp-3d-probe-beacon-alternative-fuer-voron-2-4-trident-vzbot-mit-tap-funktion-und-adxl.html) | 34,99–44,99 € (unsicher) |

## 3. Werkzeug (falls nicht vorhanden)

| Pos. | Was | Spezifikation | Beispielquelle | Preis ca. |
|---|---|---|---|---|
| W1 | Multimeter | mit Diodentest, Durchgangsprüfer, spitzen Messspitzen | vorhanden (vorausgesetzt) | – |
| W2 | Mini-Greifer (IC-Klemmprüfspitze) | für die Masse an JP_BOOT | OFFEN | OFFEN |
| W3 | Labornetzteil | 0–30 V, ≥ 2 A, **einstellbare Strombegrenzung** (Etappen 4 und 9 der Aufbauanleitung; ohne geht es mit dem Druckernetzteil, aber ohne Strombegrenzung) | OFFEN | OFFEN |
| W4 | Lötkolben | ≥ 60 W, Meißelspitze (XT30-Lötkelche, J7) – z. B. Pinecil V2 + Spitze TS-D24 + Netzteil (USB-C-PD 65 W, z. B. Good Connections GC PCA-D008W, Reichelt 36,30 €, oder Mean Well GST90A24-P1M 24 V, 22,19 €) | [eleshop.eu](https://eleshop.eu/pinecil-smart-mini-portable-soldering-iron.html) | ≈ 39,90 € + Spitze 15,70 € netto (unsicher) + Netzteil |
| W5 | Crimpzange für isolierte Kabelschuhe | rot/blau/gelb, Ratsche | C.K T3682A, Reichelt 57,99 €; Knipex 97 52 36, [preisvergleich.heise.de](https://preisvergleich.heise.de/knipex-97-52-36-crimpzange-a1044803.html) 78,78–83,99 € (**nicht** 97 52 35) | 58–84 € |
| W6 | Crimpzange JST GH | nur bei selbst gecrimpten GH-Kontakten (SSHL-002T): Engineer PA-09 (Achtung Fälschungen) | [kiwi-electronics.com](https://www.kiwi-electronics.com/en/jst-crimping-tool-pa-09-916) | 47,59 € + Versand |
| W7 | Platinenhalter („dritte Hand“, Kunststoffbacken), Lupe, Folienstift | Messen an der Unterseite, Sichtprüfung | OFFEN | OFFEN |
| W8 | Oszilloskop | 2 Kanäle, nur für die Haltezeit-Messung (Etappe 12) – ohne geht es über das Log | – | – |

## 4. Summe (Schätzung)

| Block | Positionen | ca. |
|---|---|---|
| Nötig für den Betrieb ohne Display | 1a, 1b, 2 (a), 3, 4, 5 (×2), 6 (OFFEN, Schätzung 2–4 €), 7 (×4), 8, 9 (×10), 10 (×5), 11 (×5) | ≈ 56–62 € |
| Display | 12, 13 | ≈ 41 € |
| Versand (mehrere Händler; DigiKey verlangt unter 75 € Bestellwert 25 € Versand – Pos. 11 daher besser bei LCSC oder weglassen) | – | ≈ 20–35 € (Schätzung) |
| **Einkaufsliste gesamt (ohne Werkzeug, ohne Optionen)** | | **≈ 120–140 €** |
| Optional: Antenne, GH-Kabel, Reserve-FFC | 14–16 | ≈ 8–10 € |
| Für später: Toolhead-Leitung, Inline-Sicherung, Cartographer | 17–20 | ≈ 50–60 € + Leitung |
| Werkzeug, falls nichts vorhanden | W2–W7 | ≈ 150–250 € |

Zusammen mit der Fertigung bei JLC (5 Leiterplatten, davon 2 bestückt, Schätzung ≈ 285–340 €) ergibt sich für zwei bestückte
Platinen und den Betrieb mit Display **≈ 405–480 €** ohne Werkzeug.
