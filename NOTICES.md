# Herkunfts- und Lizenzhinweise (NOTICES)

## Eigene Arbeit

- **Software** – Klipper-Module und Tests in `pi-host/` (inkl. `pi-host/install/` und `pi-host/traeger-alpha1/`),
  der Protokoll-Nachbau `nachbau/` sowie die Dokumentation außerhalb von `hardware/`: eigene, unabhängig erstellte
  Arbeit unter der **GNU General Public License v3.0 or later** (siehe `LICENSE`).
- **Hardware** – alles unter `hardware/` (Schaltplan, Layout, Fertigungsdaten, Stücklisten, Generator-Skripte,
  Bau- und Messanleitungen): **CC BY-NC-SA 4.0**, Alpha, Nutzung auf eigene Gefahr – siehe
  [`hardware/LIZENZ-UND-HAFTUNG.md`](hardware/LIZENZ-UND-HAFTUNG.md).

## Software Dritter, mit der dieses Projekt arbeitet (nicht enthalten)

- **Klipper** (<https://github.com/Klipper3d/klipper>, GPL-3.0): Die Module `pi-host/k2_*.py` sind
  Klipper-Erweiterungen (`klippy/extras/`) und laufen nur zusammen mit Upstream-Klipper; deshalb steht die Software
  dieses Repos unter GPL-3.0-or-later. Klipper selbst liegt nicht im Repo, `install.sh` nutzt den vorhandenen
  Checkout auf dem Pi.
- **Moonraker** und **Mainsail** werden auf dem Pi vorausgesetzt (MainsailOS), sind aber nicht Teil des Repos.

## Quellen Dritter in diesem Repo

- `belt_mdl-live.py`: Klartext-Python-Datei aus der Creality-K2-Firmware mit ausdrücklichem **GPLv3**-Header im
  Dateikopf, unverändert beigelegt als Vergleichsreferenz für `pi-host/test_k2_belt.py`.
- `nachbau/ref-hi/test_vectors.json`, `nachbau/ref-hi/valid_messages.json`: Testvektoren aus
  `gitstonelabs/creality-cfs-klipper` (<https://github.com/gitstonelabs/creality-cfs-klipper>, **GPL-3.0**),
  nur zur Kreuzvalidierung des CRC-/Frame-Parsers beigelegt (Herkunft: `nachbau/ref-hi/HERKUNFT.txt`).
- `pi-host/prtouch_mcu_formats.json`: Auszug der PrTouch-Befehls- und Antwortformate aus dem MCU-Datenwörterbuch der
  Creality-Firmware (Schnittstellenangaben für `pi-host/test_k2_prtouch.py`). Die vollständigen MCU-Dictionaries
  liegen **nicht** im Repo (`pack.sh --dicts ORDNER`).
- `auto_addr_wrapper.py` (Creality-Klartextquelle) ist **nicht** im Repo enthalten; der Port `nachbau/auto_addr.py`
  dokumentiert daraus nur Protokollfakten (Kommandos, Timeouts, Adressverfahren).
- Adressangaben (z. B. in `BOOTSTART-WERKZEUG.md`) sind eigene Belegstellen, auf das Nötigste gekürzt;
  keine Creality-Quelltexte.
- Hardware: Herkunft und Lizenz der 3D-Modelle, Footprint-Geometrien und Pinbelegungen stehen in
  [`hardware/k2-pi-cm5-traeger/README.md`](hardware/k2-pi-cm5-traeger/README.md) („3D-Modelle und Lizenzen“).
  Modelle mit ungeklärter Lizenz (LCSC/EasyEDA) sind nicht im Repo.

## Zum Abgleich genutzte Fremdarbeiten (keine Dateien übernommen)

- Protokoll-Kenntnisse aus `gitstonelabs/creality-cfs-klipper` (GPL-3.0) und Recherche-Dokumenten von
  `prestonbrown/helixscreen` wurden zum Abgleich und zur Korrektur eigener Ergebnisse verwendet.
- `grant0013/k2-reverse-engineering` (GPL-3.0, Creality K2): Motor-Parameter-IDs (z. B. ID 70 =
  Positionsregler-Kp) und Motor-Kommandobytes wurden zum Abgleich genutzt und als Fremd-Hypothesen gekennzeichnet.
- `grant0013/K2-OpenKlipper` (<https://github.com/grant0013/K2-OpenKlipper>, GPL-3.0, Creality K2/K2 Plus mit
  Mainline-Klipper; abgeglichen mit Stand v0.4.25-alpha.1, 09.11.2026). Kein Code übernommen – folgende Ideen und
  Messwerte von dort wurden eigenständig umgesetzt:
  - Lüfter-Wartezeit vor der Kraftsensor-Antastung auf 8 s (`fan_damping_settle_s`; dortige Messung: mit 3 s lösen
    die ersten Antastungen ohne Berührung aus),
  - am Druckende Einzugsfehler 0x09–0x0e der CFS-Box quittieren, bevor entladen wird
    (`end_print_clear_load_error`),
  - Dir-/Step-Strapping-Pins beim Wecken der Motoren als eigene Rohpins setzen und den vorherigen Pegel
    zurückstellen, statt sie als `MCU_digital_out` anzulegen (`k2_motor_init.py`),
  - Sendeschutz: Schreiben des FAL-Parameters (ID 94/110) an die Motoren gesperrt (dortiger Befund: legt den Regler
    bis zum Aus-/Einschalten lahm),
  - Einzug: Fehler 0x0c erst nach Blick auf den Kopfsensor werten (`extrude_stall_head_grace`) und ein Reststück im
    Kopf durch die Düse schieben (`stub_clear_*`),
  - Bauteillüfter beim Spülen je Material (`flush_fan_materials`), Orca-Filament-ID je Fach (`orca_filament_ids`),
  - Lüfter-Überwachung über die Tacho-Signale (`k2_fanwatch.py`) und Prüfung des Bett-Meshs auf Ausreißer
    (`k2_mesh_guard.py`).
- `fake-name/cfs-reverse-engineering` (<https://github.com/fake-name/cfs-reverse-engineering>, Connor Wolf; keine
  Lizenz außer GPL3 für die Adapter-PCBs): CFS-Hardware-/Firmware-Fakten (Flash-Map/Bootloader, Bauteile,
  RFID-vender-Feldlayout) nur als Fakten mit Quellenangabe übernommen; keine Texte, Fotos oder Dumps, keine
  Seriennummern.

## Nicht enthalten

Keine Creality-Firmware-Images, keine MCU-Dictionaries, keine Materialdatenbank (`material_database.json`), keine
Stock-Konfigurationen mit Geräte-Kennungen. `pi-host/install/pack.sh` bricht ab, falls solche Dateien ins Paket
gerieten.

## Marken

Dieses Projekt ist unabhängig und **nicht** mit Creality verbunden oder von Creality unterstützt. „Creality“,
„K2 Plus“, „CFS“, „Raspberry Pi“ und andere genannte Marken gehören ihren jeweiligen Inhabern.
