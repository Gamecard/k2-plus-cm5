# -*- coding: utf-8 -*-
"""k2_lis2dw.py – LIS2DW-Beschleunigungssensor des K2 Plus fuer UPSTREAM-Klipper,
gesprochen wird das ALTE Creality-MCU-Protokoll (Duesen-MCU, Stock-Firmware).

Abschnitt ``[k2_lis2dw]`` (oder ``[k2_lis2dw <name>]``) – ersetzt den Stock-
Abschnitt ``[lis2dw]``. Nutzbar als ``accel_chip: k2_lis2dw`` in Upstream-
``[resonance_tester]`` (SHAPER_CALIBRATE, TEST_RESONANCES, MEASURE_AXES_NOISE)
sowie mit ACCELEROMETER_QUERY / ACCELEROMETER_MEASURE / ACCELEROMETER_DEBUG_*
(aus Upstream-``adxl345.AccelCommandHelper``) und dem Webhook
``lis2dw/dump_lis2dw`` (sensor=<name>).

Warum ein eigenes Modul: Upstream (Commit 9cb9975) sendet
``config_lis2dw oid bus_oid bus_oid_type lis_chip_type`` und
``query_lis2dw oid rest_ticks`` und erwartet ``sensor_bulk_data``/
``sensor_bulk_status``. Die Creality-Firmware (Dictionary Main+Duese,
Build 1.1.0.48-293-g493f9a0f) kennt nur:

    config_lis2dw oid=%c spi_oid=%c
    query_lis2dw oid=%c clock=%u rest_ticks=%u
    query_lis2dw_status oid=%c
    lis2dw_data oid=%c sequence=%hu data=%*s
    lis2dw_status oid=%c clock=%u query_ticks=%u next_sequence=%hu
                  buffered=%c fifo=%c limit_count=%hu

-> Upstream bricht mit "Command format mismatch: query_lis2dw" ab.

Aufbau (moeglichst viel Upstream wiederverwendet):
  * Klasse ``K2Lis2dw`` erbt von Upstream ``lis2dw.LIS2DW`` und uebernimmt
    unveraendert: ``read_reg``, ``set_reg``, ``start_internal_client``,
    ``_convert_samples`` (axes_map/Skalierung), ``_process_batch``.
    Neu sind nur ``__init__`` (alte config-/query-Befehle), ``_build_config``,
    ``_start_measurements``/``_finish_measurements``.
  * Klasse ``CrealityFixedFreqReader`` erbt von Upstream
    ``bulk_sensor.FixedFreqReader`` und uebernimmt unveraendert
    ``note_start``/``note_end``/``_update_clock``/``pull_samples``
    (Dekodierung "<hhh", Zeitachse, ClockSyncRegression). Angepasst ist nur
    die Protokollschicht: ``lis2dw_data`` statt ``sensor_bulk_data`` und eine
    Uebersetzung ``lis2dw_status`` -> ``sensor_bulk_status``
    (``translate_status``).

Belege (Details README-k2_lis2dw.md):
  * Dictionary-Formate (s. o.).
  * Duesen-Firmware ``noz0_130_G30-noz0_021_000.bin`` (Stock, gleicher Build
    wie das Live-Dictionary, Ladeadresse 0x08003000): Handler
    query_lis2dw_status @0x08004e1c, query_lis2dw @0x08004f58,
    config_lis2dw @0x080037a8, lis2dw_query @0x08004e78,
    lis2dw_report @0x08003454, lis2dw_event @0x080039b8, Start im Task
    @0x0800769c. Ergebnis: entspricht dem alten Upstream-sensor_lis2dw.c
    (2023): 48-Byte-Puffer = 8 Samples je lis2dw_data, Sample = 6 Byte
    XL,XH,YL,YH,ZL,ZH (Register 0x28.. per 7-Byte-SPI-Read), ``fifo`` =
    FIFO_SAMPLES (0x2F) & 0x1f, ``limit_count`` zaehlt FIFO-Overrun (Bit 6),
    Start zum Zeitpunkt ``clock`` schreibt FIFO_CTRL=0xC0, Stopp
    (rest_ticks=0) liest den Rest, schreibt FIFO_CTRL=0x00 und antwortet mit
    ``lis2dw_status``.

Messrate (Disassembly ``lis2dw_query`` @0x08004e78, 22.10.2026): Die
Firmware liest JE Timer-Ereignis genau EIN Sample und schlaeft dann
``rest_ticks``, solange noch Daten im FIFO liegen (FIFO_SAMPLES & 0x3f != 0
-> neuer Timer, 0x8004f04/0x8004f1e). Nur wenn der FIFO LEER gelesen wird,
weckt sie den Task sofort wieder (0x8004f44) – ohne Pause, Schleife bis ein
Sample uebrig bleibt. Folgen:
  * Lieferrate = 1/(rest_ticks + ~0,1 ms Laufzeit), nicht der ODR. Stock und
    der bisherige Host (rest 4/1600 s = 2,5 ms) kamen deshalb nur auf
    ~385 Hz (live 22.10.2026; Stock-Log 12.10.: ~2,9 kB/s lis2dw_data ->
    ~400 Samples/s).
  * Der FIFO (ODR 1600 Hz) laeuft voll und ueberschreibt; die gelieferten
    Samples sind die jeweils aeltesten im FIFO (konstant ~32/1600 s = 20 ms
    alt), also eine Dezimierung des 1600-Hz-Stroms. Das FIFO-Feld im Status
    (31 oder 32 -> &0x1f = 0) zaehlt deshalb NICHT zu den gelieferten
    Samples -> ``translate_status`` ignoriert es.
  * ``read_rest_ms`` (Default und Untergrenze 1,5 ms -> ~625 Hz): Beim
    Start (FIFO leer) und im Betrieb darf die Firmware den FIFO nie leer
    lesen: Dann weckt sie sich ohne Pause selbst, liefert Doppel-Samples
    (falsche Daten) und belastet die Duesen-MCU, bis zufaellig ein zweites
    Sample im FIFO steht (Emulation: Lesetakt 0,3 ms -> Hunderte Doppel in
    50 ms). Zum ersten Lesen muessen >= 2 Samples vorliegen: bei ODR -10 %
    (1440 Hz) 1,39 ms -> Untergrenze 1,5 ms (ANNAHME Toleranz). Danach
    waechst der FIFO, weil gelesen < erzeugt.

Python-3.9-kompatibel. Keine Seriennummern/UUIDs.
"""

import logging

# Nur als Klipper-extras-Modul lauffaehig (Upstream-Geschwister); die
# Offline-Tests laden es als Teil eines Pakets mit den Upstream-extras.
from . import adxl345, bulk_sensor, bus
from . import lis2dw as _up_lis2dw

# ---------------------------------------------------------------------------
# Protokoll der Creality-Firmware (Dictionary, exakt so)
# ---------------------------------------------------------------------------
CONFIG_FMT = "config_lis2dw oid=%d spi_oid=%d"
QUERY_FMT = "query_lis2dw oid=%c clock=%u rest_ticks=%u"
QUERY_STATUS_FMT = "query_lis2dw_status oid=%c"
DATA_FMT = "lis2dw_data oid=%c sequence=%hu data=%*s"
STATUS_FMT = ("lis2dw_status oid=%c clock=%u query_ticks=%u next_sequence=%hu"
              " buffered=%c fifo=%c limit_count=%hu")

BYTES_PER_SAMPLE = 6          # XL,XH,YL,YH,ZL,ZH (int16 LE je Achse)
SAMPLES_PER_BLOCK = 8         # Firmware-Puffer 48 Byte (struct 0x4c, data @+0x1b)
FIFO_MASK = 0x1f              # Firmware maskiert FIFO_SAMPLES mit 0x1f

# Register/Skalierung: Upstream-lis2dw (identisch zur Creality-Variante)
REG_WHO_AM_I = _up_lis2dw.REG_LIS2DW_WHO_AM_I_ADDR      # 0x0F
REG_CTRL1 = _up_lis2dw.REG_LIS2DW_CTRL_REG1_ADDR        # 0x20
REG_CTRL2 = _up_lis2dw.REG_LIS2DW_CTRL_REG2_ADDR        # 0x21
REG_CTRL6 = _up_lis2dw.REG_LIS2DW_CTRL_REG6_ADDR        # 0x25
REG_FIFO_CTRL = _up_lis2dw.REG_LIS2DW_FIFO_CTRL         # 0x2E
DEV_ID = _up_lis2dw.LIS2DW_DEV_ID                       # 0x44
SCALE = _up_lis2dw.LIS2DW_SCALE                         # 9.80665*1.952/4
BATCH_UPDATES = _up_lis2dw.BATCH_UPDATES                # 0.100 s

ODR = 1600                    # CTRL1 0x94 = High-Performance 1600 Hz
DATA_RATE = ODR               # Rueckwaerts-Kompatibilitaet (Chip-ODR)
MIN_MSG_TIME = 0.100          # Startzeitpunkt-Vorlauf (wie alter Host)
# Firmware liest 1 Sample je Timer (s. Modul-Docstring): Wartezeit zwischen
# zwei Lesungen. Untergrenze 2/ODR (Leer-Schleife der Firmware vermeiden).
READ_REST_MS_DEFAULT = 1.5
# >= 2 Samples beim ersten Lesen auch bei ODR -10 % (2/1440 s = 1,39 ms)
READ_REST_MS_MIN = 1.5
READ_REST_MS_MAX = 10.0
# Laufzeit je Lesung (Ereignis + 2 Soft-SPI-Transfers), live 22.10.2026:
# rest 2,5 ms -> 385-388 Hz => ~0,1 ms (nur fuer die erwartete Rate)
READ_OVERHEAD_S = 0.0001
# CTRL6: BW_FILT (Bit 7:6) = ODR/2, /4, /10, /20; FS +-16 g, LOW_NOISE
CTRL6_BASE = 0x34
BANDWIDTH_BITS = {2: 0x00, 4: 0x40, 10: 0x80, 20: 0xc0}
BANDWIDTH_DEFAULT = 4         # 400 Hz: Alias-Schutz fuer ~625 Hz Lieferrate


def translate_status(params, count_fifo=False):
    """``lis2dw_status`` (alt) -> ``sensor_bulk_status`` (Upstream).

    Upstream rechnet chip_clock = seq*8 + buffered//6 + 1. Gezaehlt werden
    nur GELIEFERTE Samples (Firmware-Puffer): Die Firmware haelt den FIFO
    voll und liefert je Lesung das aelteste Sample, der FIFO-Inhalt wird
    grossteils ueberschrieben (s. Modul-Docstring). Der alte Host zaehlte
    ``fifo`` mit (seq*8 + buffered//6 + fifo + 1) – bei vollem FIFO springt
    das Feld zwischen 31 und 0 (Maske 0x1f) und verzerrt die Zeitachse;
    ``count_fifo=True`` bildet den alten Host nur noch fuer Vergleiche nach.
    ``limit_count`` (Zaehler FIFO-Overrun, 16 bit) = ``possible_overflows``
    (steigt im Betrieb staendig – erwartet, kein Fehler).
    """
    fifo = (params['fifo'] & FIFO_MASK) if count_fifo else 0
    out = {
        'oid': params.get('oid'),
        'clock': params['clock'],
        'query_ticks': params['query_ticks'],
        'next_sequence': params['next_sequence'],
        'buffered': params['buffered'] + fifo * BYTES_PER_SAMPLE,
        'possible_overflows': params['limit_count'],
    }
    for k in ('#name', '#sent_time', '#receive_time'):
        if k in params:
            out[k] = params[k]
    return out


class _StatusQuery:
    """Huelle um die Upstream-CommandQueryWrapper-Abfrage
    ``query_lis2dw_status`` -> ``lis2dw_status``; liefert das Upstream-Format.
    ``minclock`` wird fuer die erste Abfrage nach dem Start gesetzt
    (Start erfolgt in der Firmware erst zum Zeitpunkt ``clock``)."""

    def __init__(self, cmd):
        self.cmd = cmd
        self.minclock = 0
        self.last_raw = None

    def send(self, data=(), minclock=0, reqclock=0, retry=True):
        params = self.cmd.send(data, minclock=max(minclock, self.minclock),
                               reqclock=reqclock, retry=retry)
        self.last_raw = params
        return translate_status(params)


class CrealityFixedFreqReader(bulk_sensor.FixedFreqReader):
    """Upstream-FixedFreqReader mit Creality-Protokollschicht."""

    def __init__(self, mcu, chip_clock_smooth):
        bulk_sensor.FixedFreqReader.__init__(self, mcu, chip_clock_smooth,
                                             "<hhh")
        # Upstream: 51 // 6 = 8; Firmware: 48 // 6 = 8 -> identische Blockung
        if (self.bytes_per_sample != BYTES_PER_SAMPLE
                or self.samples_per_block != SAMPLES_PER_BLOCK):
            raise RuntimeError("k2_lis2dw: Blockgroesse passt nicht zu"
                               " Upstream-FixedFreqReader (%d/%d)" % (
                                   self.bytes_per_sample,
                                   self.samples_per_block))

    def setup_query_command(self, msgformat, oid, cq):
        # Gleiche Signatur wie Upstream, aber alte Antwort/Datenmeldung
        self.oid = oid
        cmd = self.mcu.lookup_query_command(msgformat, STATUS_FMT,
                                            oid=oid, cq=cq)
        self.query_status_cmd = _StatusQuery(cmd)
        self.bulk_queue = bulk_sensor.BulkDataQueue(self.mcu, DATA_FMT,
                                                    oid=oid)

    def note_start(self, start_clock=0):
        # Erste Status-Abfrage (Clock-Reset) erst nach dem Firmware-Start
        self.query_status_cmd.minclock = start_clock
        try:
            bulk_sensor.FixedFreqReader.note_start(self)
        finally:
            self.query_status_cmd.minclock = 0


class K2Lis2dw(_up_lis2dw.LIS2DW):
    """LIS2DW an der Creality-Duesen-MCU (nur SPI, wie Stock)."""

    def __init__(self, config):
        # Upstream-__init__ NICHT aufrufen (sendet die neuen Befehle)
        self.printer = config.get_printer()
        adxl345.AccelCommandHelper(config, self)
        self.lis_type = _up_lis2dw.LIS2DW_TYPE
        self.axes_map = adxl345.read_axes_map(config, SCALE, SCALE, SCALE)
        # Wartezeit zwischen zwei Firmware-Lesungen (1 Sample je Lesung)
        self.read_rest_ms = config.getfloat(
            'read_rest_ms', READ_REST_MS_DEFAULT, minval=READ_REST_MS_MIN,
            maxval=READ_REST_MS_MAX)
        bw = config.getint('bandwidth', BANDWIDTH_DEFAULT)
        if bw not in BANDWIDTH_BITS:
            raise config.error("[%s] bandwidth muss 2, 4, 10 oder 20 sein"
                               " (Tiefpass ODR/n)" % (config.get_name(),))
        self.ctrl6 = CTRL6_BASE | BANDWIDTH_BITS[bw]
        # erwartete Lieferrate (fuer Glaettung der Zeitachse), nicht der ODR
        self.data_rate = 1. / (self.read_rest_ms / 1000. + READ_OVERHEAD_S)
        if config.get('cs_pin', None) is None:
            raise config.error("[%s] braucht cs_pin (die Creality-Firmware"
                               " kennt lis2dw nur an SPI)" % (
                                   config.get_name(),))
        self.bus_type = _up_lis2dw.SPI_SERIAL_TYPE
        # Stock: Software-SPI PA5/PA7/PA6, 5 MHz, Modus 3 (wie Upstream)
        self.bus = bus.MCU_SPI_from_config(config, 3, default_speed=5000000)
        # Startablauf: FIFO bis zum Firmware-Start im Bypass (leer) halten
        self.start_fifo_bypass = config.getboolean('start_fifo_bypass', True)
        self.mcu = mcu = self.bus.get_mcu()
        self.oid = oid = mcu.create_oid()
        self.query_lis2dw_cmd = None
        self.query_lis2dw_end_cmd = None
        mcu.add_config_cmd(CONFIG_FMT % (oid, self.bus.get_oid()))
        mcu.add_config_cmd("query_lis2dw oid=%d clock=0 rest_ticks=0"
                           % (oid,), on_restart=True)
        mcu.register_config_callback(self._build_config)
        # Sample-Lesen + Zeitachse (Upstream-Mechanik, alte Protokollschicht)
        chip_smooth = self.data_rate * BATCH_UPDATES * 2
        self.ffreader = CrealityFixedFreqReader(mcu, chip_smooth)
        self.last_error_count = 0
        self.last_start_clock = 0
        self.last_end_status = None
        # Batch-Verarbeitung (Upstream)
        self.batch_bulk = bulk_sensor.BatchBulkHelper(
            self.printer, self._process_batch,
            self._start_measurements, self._finish_measurements,
            BATCH_UPDATES)
        self.name = config.get_name().split()[-1]
        hdr = ('time', 'x_acceleration', 'y_acceleration', 'z_acceleration')
        self.batch_bulk.add_mux_endpoint("lis2dw/dump_lis2dw", "sensor",
                                         self.name, {'header': hdr})
        # Eigener Diagnose-Befehl (Name ohne Ziffern, Upstream-Namensregel)
        gcode = self.printer.lookup_object('gcode')
        gcode.register_mux_command("KTWO_ACCEL_STATUS", "CHIP", self.name,
                                   self.cmd_KTWO_ACCEL_STATUS,
                                   desc=self.cmd_KTWO_ACCEL_STATUS_help)
        if len(config.get_name().split()) == 1:
            gcode.register_mux_command("KTWO_ACCEL_STATUS", "CHIP", None,
                                       self.cmd_KTWO_ACCEL_STATUS,
                                       desc=self.cmd_KTWO_ACCEL_STATUS_help)

    def _build_config(self):
        cmdqueue = self.bus.get_command_queue()
        self.query_lis2dw_cmd = self.mcu.lookup_command(QUERY_FMT,
                                                        cq=cmdqueue)
        # Stopp (rest_ticks=0) antwortet mit lis2dw_status -> Query-Befehl
        self.query_lis2dw_end_cmd = self.mcu.lookup_query_command(
            QUERY_FMT, STATUS_FMT, oid=self.oid, cq=cmdqueue)
        self.ffreader.setup_query_command(QUERY_STATUS_FMT, oid=self.oid,
                                          cq=cmdqueue)

    # Register schreiben; im Batch-Modus (klippy -o) gibt es keine Antworten
    def _write_reg(self, reg, val):
        if self.mcu.is_fileoutput():
            self.bus.spi_send([reg, val & 0xFF])
            return
        self.set_reg(reg, val)

    def _start_measurements(self):
        if not self.mcu.is_fileoutput():
            dev_id = self.read_reg(REG_WHO_AM_I)
            logging.info("k2_lis2dw '%s' dev_id: %x", self.name, dev_id)
            if dev_id != DEV_ID:
                raise self.printer.command_error(
                    "Invalid lis2dw id (got %x vs %x).\n"
                    "This is generally indicative of connection problems\n"
                    "(e.g. faulty wiring) or a faulty lis2dw chip."
                    % (dev_id, DEV_ID))
        # Wie Upstream (SPI): I2C abschalten, Adress-Autoinkrement bleibt an
        self._write_reg(REG_CTRL2, 0x06)
        # FIFO_CTRL: Bypass (0x00) bis die Firmware zum Startzeitpunkt 0xC0
        # (Continuous) schreibt -> FIFO beginnt leer, Zaehlung exakt.
        # start_fifo_bypass: False = Ablauf wie alter Creality-Host (0xC0).
        self._write_reg(REG_FIFO_CTRL, 0x00 if self.start_fifo_bypass
                        else 0xC0)
        # Tiefpass (bandwidth, Default ODR/4), +-16g, Low-noise
        self._write_reg(REG_CTRL6, self.ctrl6)
        # High-Performance 1600 Hz, 14 bit
        self._write_reg(REG_CTRL1, 0x94)
        # Start zum Zeitpunkt reqclock (altes Protokoll mit clock)
        systime = self.printer.get_reactor().monotonic()
        print_time = self.mcu.estimated_print_time(systime) + MIN_MSG_TIME
        reqclock = self.mcu.print_time_to_clock(print_time)
        rest_ticks = self.mcu.seconds_to_clock(self.read_rest_ms / 1000.)
        self.query_lis2dw_cmd.send([self.oid, reqclock, rest_ticks],
                                   reqclock=reqclock)
        self.last_start_clock = reqclock
        logging.info("k2_lis2dw starting '%s' measurements", self.name)
        self.ffreader.note_start(reqclock)
        self.last_error_count = 0

    def _finish_measurements(self):
        # Stopp: Firmware leert FIFO, FIFO_CTRL=0x00, letzter Block + Status
        params = self.query_lis2dw_end_cmd.send([self.oid, 0, 0])
        self.last_end_status = translate_status(params)
        self.ffreader.note_end()
        logging.info("k2_lis2dw finished '%s' measurements", self.name)
        self._write_reg(REG_FIFO_CTRL, 0x00)

    cmd_KTWO_ACCEL_STATUS_help = ("k2_lis2dw: Rohstatus der Duesen-MCU"
                                  " (query_lis2dw_status) anzeigen")

    def cmd_KTWO_ACCEL_STATUS(self, gcmd):
        raw = self.ffreader.query_status_cmd.cmd.send([self.oid])
        conv = translate_status(raw)
        gcmd.respond_info(
            "k2_lis2dw '%s': clock=%d query_ticks=%d next_sequence=%d"
            " buffered=%d fifo=%d limit_count=%d (Upstream: buffered=%d"
            " possible_overflows=%d) messung=%s; Lesetakt %.2f ms ->"
            " erwartet ~%.0f Samples/s, Tiefpass CTRL6=0x%02x" % (
                self.name, raw['clock'], raw['query_ticks'],
                raw['next_sequence'], raw['buffered'], raw['fifo'],
                raw['limit_count'], conv['buffered'],
                conv['possible_overflows'], self.batch_bulk.is_started,
                self.read_rest_ms, self.data_rate, self.ctrl6))


def load_config(config):
    return K2Lis2dw(config)


def load_config_prefix(config):
    return K2Lis2dw(config)
