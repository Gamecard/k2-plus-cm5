# BOOTSTART-WERKZEUG – Geräte aus dem Bootloader in die App starten (Werkzeug, 18.10.2026)

> **GERÄTETEST OFFEN – nur mit ausdrücklicher Freigabe.** Das Werkzeug ist offline gebaut und mit simuliertem
> Bus/UART getestet. Am Drucker oder Pi wurde nichts gesendet.
> Grundlage: Analyse der Creality-Werkzeuge `mcu_util` und `mcu_util_485` (Disassembly/Dekompilat, Literale im Binary)
> und der Bootloader-Protokolle aus der Protokollanalyse; die Arbeitsdateien liegen nicht im Repo.
> Keine SN, UID, IP oder Kennungen im Text. Belegstärken: **B** = belegt, **B+L** = belegt und live beobachtet,
> **V** = Deutung, **ANNAHME** = ungeprüft, **O** = offen.

## Kurzfassung

- **Werkzeug:** `pi-host/install/k2-bootstart.py` (CLI) + `nachbau/bootstart.py` (Logik, Sendeschutz). Läuft auf dem Pi
  **vor** Klipper, von Hand oder optional als `k2-bootstart.service` (`install.sh --bootstart`, Standard AUS).
- **Nur Start, nie Update:** UART nur `75`, `00 FF`, `02 FD`. RS485 nur A1, A0 und F0 `[00]`/`[03]`/`[02]`.
  Eine eigene, enge Whitelist prüft jeden Frame. Der Standard-Sendeschutz bleibt unverändert und gilt zusätzlich (§5).
- **Zuerst die App prüfen:** Antwortet die Main-/Düsen-MCU auf Klipper-`identify` @230400, wird an diesen Port nichts
  weiter gesendet. Auf dem Bus werden nur Geräte mit **mode 1** (Loader) angefasst.
- **Erkennungsmodus** `--nur-erkennen`: UART nur identify, `75`, `00 FF`; RS485 nur A1. Kein Start, auch im Sendeschutz
  gesperrt. Gedacht für den ersten Gerätetest (§7).
- **Belege (B):** A1-Daten sind `[Gruppe, Gruppe]` (nicht `<adr> 01` wie früher angenommen).
  A0 geht bei Motor/Belt nur **1×** raus, bei der CFS 2×. Der UART-Handshake wird **nicht** wiederholt.
  Die 3 Versuche mit 500 ms Pause gehören zum Transparentmodus (§2.2 unten).
- **Neue Deutung (V):** Dass live alle Motoren beim Stock-Boot `mode: boot` melden, liegt am Stock selbst: Er sendet
  vorher `FF 0B [00]` und schickt sie damit in den Bootloader. Über den Zustand nach reinem Einschalten sagt das nichts.
  **Belts** dagegen kennen keinen Bootloader-Eintritt aus der App. Sie standen also schon nach dem Einschalten im Loader.
  Für sie ist das Werkzeug am wahrscheinlichsten nötig (§6).

---

## 1. Dateien und Aufruf

| Datei | Zweck |
|---|---|
| `nachbau/bootstart.py` | Logik: `UartBootloader`, `uart_bootstart`, `Bus485Roh`, `rs485_bootstart`, Sendeschutz `check_bootstart_frame`/`check_uart_tx`, Pläne für den Trockenlauf, Selbsttest |
| `pi-host/install/k2-bootstart.py` | CLI: Ports, Vorprüfung (Port da, nicht belegt, nicht doppelt), Klipper-identify (über `k2-identify.py`), Ausgabe, Exit-Codes, `--dry-run`, `--nur-erkennen`, `--aus-config` |
| `pi-host/install/test_k2_bootstart.py` | Offline-Test (Teile A–F, s. §8) |
| `pi-host/install/k2-bootstart.service` | systemd-Vorlage (oneshot, `Before=klipper.service`), nur über `install.sh --bootstart` |

```bash
sudo systemctl stop klipper
~/klippy-env/bin/python ./k2-bootstart.py --dry-run --main <port> --duese <port> --rs485 <port>   # nichts geöffnet
~/klippy-env/bin/python ./k2-bootstart.py --nur-erkennen --rs485 <port>                          # nur A1
~/klippy-env/bin/python ./k2-bootstart.py --main <port> --duese <port> --rs485 <port>             # echter Lauf
~/klippy-env/bin/python ./k2-bootstart.py --aus-config ~/printer_data/config                      # Ports aus der Config
```

Exit: 0 = alles in der App bzw. nichts im Loader; 1 = mindestens ein Gerät nicht gestartet, stumm oder (mit
`--nur-erkennen`) im Loader; 2 = Aufruf-/Umgebungsfehler (Port fehlt/belegt/doppelt; dann wurde **nichts** gesendet);
3 = Gesamt-Timeout (Standard 240 s); 4 = Sendeschutz hat einen Frame abgelehnt (interner Fehler, Frame nicht gesendet).

---

## 2. UART: Main-/Düsen-MCU (`mcu_util`)

### 2.1 Bytes und Port (B)

| Was | Wert | Beleg |
|---|---|---|
| Port | 115200 8N1 roh, VMIN 1, VTIME 0, `tcflush` beim Öffnen | `FUN_00010aa0` (`cfsetospeed 0x1002`, `c_cflag … \| 0x30`) |
| Handshake | **1 Byte** `75` (Zustand 1) | Sendethread `FUN_00010eb0`, case 1 |
| Version | `00 FF` (Zustand 7, Halbwort `0xff00` little-endian) | `FUN_00010eb0`, case 7 |
| Start | `02 FD` (Zustand 0x11, Literal @`0x111f0` = `0xfffffd02`) | `FUN_00010eb0`, case 0x11 |
| nicht benutzt | `04 FB` @`0x111f8`, `05 FA` @`0x11208`, `01 FE` @`0x11218`, `03 FC` (Zustand 9), Länge, Daten | ebd. – im Werkzeug gesperrt |
| Sendetakt | Poll alle 100 ms (`0x186a0` @`0x11230`) | ebd. |

### 2.2 Antworten und Fehlerverhalten (B, Empfangsthread `FUN_00011234`)

- Empfang über `select` mit 1 s. **3 leere Fenster in Folge** führen zu Zustand 0x16, also Exit 1. Jedes empfangene Byte
  setzt den Zähler zurück. Bei Stille wird **nicht** neu gesendet.
- **Handshake** (Zustand 2): Das erste Byte muss `75` sein. Sonst Zustand 0x15, also Exit 2, ohne Wiederholung. Mit
  `-c` endet das Programm danach (Zustand 0x14).
  - **Achtung:** „bis 3 Versuche (Zustand 4: 500 ms Pause)“ gilt für den Transparentmodus
    (`04 FB`, Zustand 4, `usleep 0x7a120` @`0x118c4`), nicht für den Handshake.
- **Version** (Zustand 8): Es müssen mindestens **26 Byte** da sein. Byte 25 muss `~Σ` der Bytes 0–24 sein
  (`FUN_00010e84`). Zeichen außerhalb `[0-9A-Za-z_-]` werden zu `0`.
  - Prüfsumme falsch: Zähler +1, danach erneut `00 FF`. Ab dem 3. Fehler Exit 2.
- **Start** (Zustand 0x12): Es müssen mindestens 2 Byte da sein, und zwar `75` und `~75` = `8A`. Dann Zustand 0x13 (Exit 0).
  Sonst erneut `02 FD`, höchstens 3 Sendungen.
- **Skript** `/etc/init.d/mcu_update` (B): getrennte Aufrufe `mcu_util -c` (Z. 29–44), `-g` (Z. 46–61) und `-s`
  (Z. 117–132).
  - `-s` kommt nur, wenn die Version gelesen wurde **und** kein Update nötig ist (Z. 386–403 Main, Z. 444–461 Düse).
  - Zwischen Handshake und Start liegt beim Stock der ganze RS485-Lauf. Der Main-Bootloader wartet also mindestens so
    lange (B+L: live „startup app success“).

### 2.3 Ablauf im Werkzeug (`uart_bootstart`)

1. Klipper-`identify` @230400 über `k2-identify.py` (`identify_versuch`, nur lesend).
   - **Antwort:** Status `app`. An diesen Port geht **nichts** weiter.
   - **belegt/Fehler:** Umgebungsfehler (Exit 2). Nichts gesendet.
2. Sonst Port @115200: `75` → `75`, dann `00 FF` → 25 B + `~Σ`, dann Versionsprüfung (§4), dann `02 FD` → `75 8A`.
   Zeiten, Wiederholungen und Abbrüche wie §2.2.
3. Nachkontrolle: 1 s warten (**ANNAHME**), dann erneut identify @230400. Ohne Antwort: Fehler (Exit 1).
4. `--nur-erkennen`: Schluss nach der Version. `02 FD` ist dann auch im Sendeschutz gesperrt.

---

## 3. RS485: Motoren, Belts, CFS (`mcu_util_485`)

### 3.1 Rahmen, Status, Dispatch (B)

- Sendeframe `F7 addr len status fun data crc`, `len = n + 3`, CRC-8 (Poly 0x07, MSB-first, Init 0) über `len..data`.
  Das Werkzeug baut ihn mit `serial485.build_frame`.
  - Status ist immer 0: Alle Aufrufe von `FUN_000148a4` haben Parameter 3 = 0.
- Empfangsthread `FUN_00012414`, Dispatch-Tabelle @`0x2a008` (fun → Handler):
  - `A1` → `0x12ad4`, `A0` → `0x12894`, `A2` → `0x125b4`, `F0` → `0x11eac`.
  - Das Status-Byte der Antwort wird nicht geprüft.
  - Während der Discovery (Zustand 1) gibt es **kein** `sem_post`. Der Host sammelt also bis zum Timeout.

### 3.2 Discovery und Adressvergabe (B, `FUN_00014a08`)

- **A1:**
  - Daten `[Gruppe, Gruppe]`, z. B. `strh` des Literals @`0x159b4` = `0xfffffdfd` → `FD FD`; Belt `0xfffffcfc`.
  - Länge 2, Timeout 1000 ms (@`0x14b4c`–`0x14b68`), danach `usleep(1 s)` (`0xf4240`).
  - Weil der Empfangsthread weiter sammelt, ergibt das ≈ 2 s Fenster je Runde.
  - Runden: Motoren bis 3 (Ziel 4 Geräte Typ 2), Belts bis 3 (Ziel 2, Typ 3), RFID 1, CFS bis 3 (Ziel Typ 1 + Typ 10 = 4).
  - Gezählt werden **alle** Antworten des Typs, unabhängig vom mode.
  - **Korrektur:** Früher angenommen waren „2 Datenbytes `<adr> 01`“. Belegt sind `<adr> <adr>`. Das passt zu
    Klippys `0xa1 0xfe 0xfe` und zur Bereichsprüfung des CFS-Bootloaders.
- **A1-Antwort** (`FUN_00012ad4`):
  - Absender = Frame-Adresse, `data[0]` = Typ, `data[1]` = mode, `data[2..13]` = UID.
  - Gleicher Typ + gleiche UID → „duplicate uuid, skip!“.
  - **Kein Filter auf mode:** Stock nimmt auch Geräte in der App auf.
- **A0** (@`0x14fb4`–`0x15060`): Daten `[Adresse] + UID12` (len 13), Timeout `0x5dc` = 1500 ms, Status 0.
  - **Typ 1/10 (CFS):** immer A0 an 0xFE, **2 Versuche**.
  - **Typ 2 (Motor):** nur wenn der Eintrag die Absenderadresse 0xFD hat (`cmp r5,#253` @`0x14fd8`). Dann A0 an 0xFD,
    **1 Versuch** (`r8 = 1`).
  - **Typ 3 (Belt):** genauso mit 0xFC (@`0x15008`).
  - Hat der Motor bzw. Belt eine andere Absenderadresse, geht **kein** A0 raus, und Stock arbeitet mit dieser Adresse
    weiter (@`0x14fec`).
  - Andere Typen: Adresse 0, das Gerät wird übersprungen.
  - Erfolg: Startadresse +1 (Motor 0x85, Belt 0x23, RFID 0x11, CFS 0x01). Fehlschlag: Eintragsadresse 0, die Startadresse
    wird **nicht** erhöht.
- **A0-Antwort** (`FUN_00012894`): Der Eintrag mit gleicher UID bekommt die Absenderadresse der Antwort.

### 3.3 F0 Version, Sektor, Start (B, Handler @`0x11eac`, Sprungtabelle @`0x11ef4`)

- Alle drei gehen nur an Einträge mit Adresse 1..0xF0 (`(byte)(addr−1) < 0xF0`). Timeout 500 ms, Schleife
  `do { r = senden(); if (r == 0) break; } while (++i != 3)`. Wiederholt wird also **nur ohne Antwort**.
- `[00]` (Zustand 3): 25 Byte ab `data[0]` = Versionstext (@`0x11f1c`).
- `[03]` (Zustand 4): `data[0]` = Sektorbyte (@`0x11fb0`).
- `[02]` (Zustand 10): Erfolg nur bei `data[0] == 0x75` (@`0x12210` → Zustand 11). Sonst „startup app failed!“.
- Reihenfolge Stock: erst alle `[00]`, dann alle `[03]`, dann (nur CFS) alle `[06]`, dann die Update-Entscheidung, dann
  alle `[02]`.

### 3.4 Ablauf im Werkzeug (`rs485_bootstart`)

Gruppen in Stock-Reihenfolge 0xFD, 0xFC, 0xFE (RFID entfällt, s. §6). Je Gruppe:

1. A1-Runden wie Stock (Fenster 1,0 s + 1,0 s, Abbruch beim Ziel).
2. Einordnen:
   - Typ passt nicht zur Gruppe → `fremd`.
   - mode 0 → `app`, **nicht angefasst**.
   - mode 1 mit Absender = Gruppe → `loader`.
   - Motor/Belt mit Absender im Loader-Bereich → schon adressiert, ohne A0 weiter.
   - Alles andere → `fremd`.
3. A0 je Loader mit der nächsten freien Adresse (CFS 2 Versuche, Motor/Belt 1). Erfolg nur, wenn die Antwort vom
   **neuen** Absender mit **gleicher UID** kommt.

Danach für alle adressierten Geräte: `[00]`, dann `[03]`, dann `[02]` nur bei gültiger Version (§4). Zum Schluss die
Nachkontrolle: 3,5 s warten (CFS: Ack, 3 s, Sprung, plus 0,5 s **ANNAHME**), dann je
gestarteter Adresse 1× `[00]`. Kommt noch eine 25-Byte-Version, meldet das Werkzeug „Loader antwortet noch“ (Fehler).

---

## 4. Bewusste Abweichungen vom Stock (alle vorsichtiger)

| Punkt | Stock (B) | Werkzeug | Grund |
|---|---|---|---|
| Geräte in der App | A1 nimmt sie auf (kein mode-Filter); die CFS-App bekäme A0 | nur mode 1 wird angefasst | Aufgabe: Apps nicht anfassen; CFS-App adressiert k2_box |
| Startbedingung | RS485: Start auch ohne lesbare Version (Start-Schleife prüft nur Adresse und „nicht fail“); UART: Start nur nach gelesener Version und ohne Update-Bedarf | Start nur bei gültiger Version: 25 Zeichen, `-` an Stelle 12, App-Teil `_` an 4/8, App ≠ `000_000`, Präfix passt (`mcu0_`, `noz0_`, `mot`, `bet`, `cfs`) | Stock hätte in diesen Fällen geflasht, nie gestartet. Ein Start mit fehlender/fremder App ist unbelegt |
| F0 `[06]` (CFS-DB-Erase) | bei jeder CFS im Loader | nie (gesperrt) | löscht die CFS-Datenbank; kein Teil des Starts |
| `FF 0B [00]` vor dem Lauf | ja (`-c`) | nie (gesperrt) | schickt Motoren/RFID in den Bootloader |
| Extruder-Motor | Transparentmodus `04 FB` + `00 FF` über die Düsen-MCU | nicht enthalten (`04 FB` gesperrt) | Aufgabe: nur Handshake/Version/Start; s. §6 |
| A0 ohne Antwort | Startadresse wird nicht erhöht, das nächste Gerät bekommt dieselbe | Adresse gilt als verbraucht | Kam das A0 an und nur die Antwort ging verloren, gäbe es sonst eine Doppeladresse |
| schon adressierter Loader (z. B. 0x85) | Startadresse 0x85 bleibt, ein neues Gerät bekäme 0x85 | belegte Adressen werden übersprungen | keine Doppeladresse |
| A0-/F0-Antwort | jeder Frame mit passendem fun gilt (`sem_post`) | A0: Absender = neue Adresse **und** gleiche UID; F0: Absender = Zieladresse | keine Fremd-/Echoframes als Antwort |
| Echo | – | ein Byte-gleiches Echo des eigenen Frames wird verworfen | **ANNAHME:** Adapter ohne Echo; nur Vorsicht |
| Nachkontrolle | keine | UART: identify; RS485: 1× `[00]` | Start wirklich gelungen? |

---

## 5. Sendeschutz

**RS485 (`check_bootstart_frame`)**, in dieser Reihenfolge:

1. **Verbote** (jede Adresse, eigene Meldung):
   - Ziel 0xF0
   - fun 0xF2
   - fun 0xF0 mit anderem Inhalt als genau `[00]`/`[02]`/`[03]` (also nie `[01]`, Länge, Datenblöcke, `[06]`, leer,
     Zusatzbytes)
   - fun 0x0B (auch `[01]`)
   - fun 0x01
   - fun 0x03/0x04/0x07
2. **Whitelist:**
   - `A1` an 0xFD/0xFC/0xFE mit Daten genau `[g, g]`
   - `A0` an 0xFD/0xFC/0xFE mit `[a] + UID12` und `a` aus dem Loader-Bereich der Gruppe (Motor 0x85–0x88,
     Belt 0x23/0x24, CFS 0x01–0x04)
   - `F0 [00]/[03]/[02]` an diese Loader-Adressen
   - Beim echten Senden gilt immer die **Sitzung**: A0 nur für eine UID, die in diesem Lauf als Loader dieser Gruppe
     gemeldet wurde, und nur an eine Adresse, die nicht schon an eine andere UID vergeben ist. F0 nur an eine in diesem
     Lauf bestätigte Adresse. Mit `--nur-erkennen` nur A1.
3. **Standard-Sendeschutz** `serial485.check_tx_frame` gilt zusätzlich.
   - Einzige Ausnahme: F0 an 0x23/0x24/0x85–0x88. Dort ersetzt die Whitelist Regel 5 („Bootloader-Adressen gesperrt“).
   - `serial485.py`/`k2_transport.py` und `TX_GUARD_REV` (20261019) sind **unverändert**. Im normalen Betrieb bleiben
     diese Adressen gesperrt.

**UART (`check_uart_tx`):** nur `75`, `00 FF`, `02 FD`; mit `--nur-erkennen` ohne `02 FD`.

Alle Verstöße werfen `BootstartGuardError` (Unterklasse von `TxGuardError`). Es wird **nichts** gesendet, die CLI endet
mit Exit 4.

---

## 6. Offen (O), Annahmen und Deutungen (V)

- **O – Autostart der Bootloader** von Main-MCU, Düse, Motoren und Belts ohne Host. Bleibt der
  wichtigste Gerätetest. Neue Hinweise:
  - **V (Motoren/RFID):** Der live gesehene Zustand `mode: boot` erklärt sich durch Stocks `FF 0B [00]`.
    - Belege: Skript Z. 188/227/367 (`-c`). Motor-App: `0B [00]` setzt `boot_key` und löst einen Watchdog-Reset aus
      (Motor-Firmware). RFID-App: `0B [00]` = Reset in den Bootloader.
    - Ob Motoren nach reinem Einschalten im Loader stehen, ist damit offen.
    - **ANNAHME:** Der Motor-Bootloader bleibt nur bei gesetztem `boot_key` im Update-Modus.
  - **V (Belts):** Die Belt-App kennt keinen Bootloader-Eintritt (Belt-Firmware). Stock fand beide Belts im
    Loader (0x23/0x24). Sie standen also schon nach dem Einschalten dort, mindestens bis zum Boot-Check (≈ 10–20 s).
    - Ob sie danach selbst starten, ist offen.
    - Ohne Start bleiben `KTWO_BELT_*` stumm (key712/713). Für den Druck sind Belts nicht nötig.
  - **V (Extruder-Motor):** Stock liest ihn nur über den Transparentmodus mit dem UART-Protokoll (`00 FF`, Version
    `mot2_022_C30-…`). Er war beim Stock-Boot also im Loader und wird ohne Update **nie** per Start-Befehl gestartet
    (Skript Z. 170–241).
    - Weil er im Druck läuft, muss er von selbst in die App kommen (Zeitfenster?) oder über einen anderen Weg (**O**).
    - Das Werkzeug fasst ihn nicht an.
- **O – Main-/Düsen-Bootloader:** Kein Dump. Bytes und Antworten sind nur von der Host-Seite belegt.
  - Ob ein Handshake (`75`) einen Autostart-Timer anhält, ist offen (vgl. CFS: A0 beendet das 30-s-Fenster).
    Deshalb soll nach `--nur-erkennen` ein Lauf mit Start oder ein Aus/An folgen.
- **O – Wirkung von identify-Bytes @230400 auf einen Bootloader @115200:** Die Reihenfolge ist bewusst so
  festgelegt (zuerst die App prüfen). Ob Fremdbytes einen wartenden Bootloader beeinflussen, ist ohne Dump nicht prüfbar. Die
  Inbetriebnahme sendet diese Bytes ohnehin schon in Schritt 1.1 (`k2-identify.py`).
- **O – Motor-/Belt-Bootloader:** Ihre Antwort auf `[03]` und `[02]` kennen wir nur von der Host-Seite (Erwartung `0x75`)
  und aus dem Live-Log („startup app success“, B+L).
  - Das Zeitverhalten der A1-Antwort ist offen. Fenster 2 s wie Stock.
- **O – mehrere CFS bei Handaufruf:** Hält eine CFS-**App** aus einer früheren Klipper-Sitzung schon 0x01 und steht eine
  zweite CFS im Loader, vergibt das Werkzeug 0x01 doppelt (Stock genauso). Folge: Version unlesbar → kein Start, Box
  bleibt im Loader, k2_box holt sie per `0xFF 0x0B [01]`.
  - Vorgesehener Einsatz ist deshalb **direkt nach dem Einschalten** vor Klipper, sonst `--ohne-cfs`.
- **Nicht enthalten:** RFID (0xFB/0x11; der Pi nutzt `[filament_rack]` nicht), Extruder-Motor (s. o.), jedes Update.
- **ANNAHMEN:** 1 s vor der UART-Nachkontrolle, 0,5 s Reserve in der RS485-Nachkontrolle, Adapter ohne Echo.

---

## 7. Gerätetest – was zuerst zu prüfen ist (nur mit ausdrücklicher Freigabe)

1. **Offline auf dem Pi:** `install.sh` Schritt 6 grün (darin `test_k2_bootstart` und `nachbau/bootstart`).
   `k2-bootstart.py --dry-run --main … --duese … --rs485 …` zeigt die Ports und die geplanten Bytes und öffnet nichts.
2. **Main/Düse nach Kaltstart:** zuerst wie geplant PI-INBETRIEBNAHME 1.1/1.2 (`k2-identify.py`). Antworten beide,
   braucht der UART-Teil das Werkzeug nicht (das Werkzeug würde ebenfalls nichts senden).
3. **RS485 erkennen** (Klipper gestoppt, Drucker frisch eingeschaltet, Zeit seit dem Einschalten notieren):
   `k2-bootstart.py --nur-erkennen --rs485 <port>`. Es gehen nur A1 an 0xFD/0xFC/0xFE raus.
   - Erwartung (V): Belts mode 1; Motoren offen; CFS innerhalb von 30 s mode 1, danach (App, unadressiert) mode 0.
   - Zu prüfen: Echo des Adapters? CRC-Fehler? Antworten innerhalb von 2 s?
4. **Nur falls MCU stumm:** `--nur-erkennen --main <port>` (identify, `75`, `00 FF`). Die Version muss mit `mcu0_`
   (Düse: `noz0_`) beginnen. Danach **sofort** Schritt 5 oder Drucker aus/an.
5. **Echter Lauf**, ein Teil nach dem anderen (z. B. zuerst `--rs485 <port> --ohne-cfs`): Ausgabe sichern.
   - Bei Exit 1/4 STOP und Rückweg zum Stock (PI-INBETRIEBNAHME Anhang C).
   - Danach PI-INBETRIEBNAHME 2.3/2.4/3.3.
6. **Erst danach**, falls nötig: `install.sh --bootstart`. Prüfen mit `journalctl -u k2-bootstart` nach einem Kaltstart.

---

## 8. Offline-Tests (Stand 18.10.2026)

- `pi-host/install/test_k2_bootstart.py`: **96/96** (im Paket 95, weil dort `pack.sh` fehlt).
  - **A** Frame-Bytes gegen eine unabhängige CRC-Referenz und 4 Live-Frames.
  - **B** Whitelist erlaubt / Verbote abgelehnt (F0 `[01]`/`[06]`/Länge/0x80-Datenblock, 0xF2, Ziel 0xF0, `0B [00]`,
    0x01, Motor 0x03/0x04/0x07, falsche Gruppen/Adressen, App-Adressen); Standard-Sendeschutz unverändert;
    UART-Verbote.
  - **C** UART-Fake (App, Loader, stumm, falsches Handshake-Byte, Prüfsumme 1×/3×, Version-Timeout, ungültige/fremde
    Version, Start-NAK, Nachkontrolle, Häppchen, `--nur-erkennen`).
  - **D** RS485-Fake (App-Motoren, Loader-Motoren/-Belts/-CFS, CFS-App, ungültige App, `0x1F`, stumme Geräte,
    CRC-Fehler, F0-Timeouts, schon adressierter Loader, fremder Typ/mode, Echo, Nachkontrolle, Gruppen aus, 5 Motoren,
    Doppel-/Kurzantwort, Zeitverhalten, `--nur-erkennen`).
  - **E** CLI (Fake-Umgebung, Unterprozess).
  - **F** Paket/Integration.
- `python3 nachbau/bootstart.py` (Selbsttest) 20/20.
- Unverändert grün: `pi-host/test_k2_485.py` 88/88, `pi-host/test_k2_box.py` 74/74, `nachbau/serial485.py` 122/122.
- Attrappe (Fake-`sudo`/`systemctl`, Unit-Pfad im Temp-Ordner): Die echten Blöcke aus `install.sh` und `uninstall.sh`
  wurden ausgeführt.
  - `--bootstart`: Unit-Text, Werkzeugordner, `enable`.
  - `--bootstart-aus`.
  - Ohne Option bleibt alles unverändert.
  - Nur Platzhalter in der Config: Abbruch, nichts installiert.
  - Rückbau: „gab es vorher nicht“ → entfernt; Kopie zurück samt Aktivierung; altes Backup ohne Schlüssel → entfernt;
    fremde Einheit ohne Paketmarke bleibt.
