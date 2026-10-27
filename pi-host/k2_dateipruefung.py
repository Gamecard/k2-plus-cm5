# -*- coding: utf-8 -*-
"""k2_dateipruefung – Druckdatei vor dem Start prüfen (falsches Druckerprofil).

Vorfall 23.10.2026: In OrcaSlicer wurde eine MakerWorld-3MF GEÖFFNET (nicht
importiert). Orca übernahm dabei das Druckerprofil des Projekts (Bambu Lab
X1 Carbon). Die Datei wurde in Mainsail gestartet und lief mit Bambu-
Startcode an: M1006-Töne, G380, M620/M621 (AMS), M109 S250 und G1 E50 als
Spülung über dem Bett, PrTouch heiß bei 205 °C. Erst bei
``M221 S ; push soft endstop status`` brach Klipper mit „unable to parse“ ab.
Solche Dateien sollen gar nicht erst starten.

Ablauf: Das Makro ``SDCARD_PRINT_FILE`` (config-pi/macros.cfg,
``rename_existing: SDCARD_PRINT_FILE_BASE``) ruft zuerst
``KTWO_DATEI_PRUEFEN {rawparams}`` und erst danach
``SDCARD_PRINT_FILE_BASE {rawparams}``. Lehnt die Prüfung ab, bricht das
Makro mit dem Fehler ab – die Datei wird nicht geöffnet, nichts heizt oder
fährt.

Regeln:
 a) Steht ``; printer_model = …`` in der Datei (Slicer-Konfigblock: Orca und
    Creality Print am Dateiende, Bambu Studio am Anfang) und passt es zu
    KEINEM Eintrag aus ``erlaubte_modelle`` -> Abbruch.
 b) Fehlt printer_model (oder ist leer): Befehlszeilen am Dateianfang auf
    Befehle prüfen, die es nur bei Bambu Lab gibt (``bambu_befehle``) -> ab
    einem Treffer Abbruch, die gefundenen Befehle stehen in der Meldung.
    Kommentare (``; M620 …`` und alles nach ``;``) zählen nicht.
 c) Sonst wird gedruckt. Ohne printer_model und ohne Bambu-Befehl kommt der
    Hinweis „Druckerprofil unbekannt“ (k2_konsole).
 d) Allgemeines Klipper-Profil (Nachtrag 23.10.2026, z. B. Orca „MyKlipper“
    = „Generic Klipper Printer“): printer_model passt nicht, aber
    ``; gcode_flavor = klipper``, der Anfang ruft START_PRINT/PRINT_START auf
    und enthält keinen Bambu-Befehl -> wird MIT WARNUNG gedruckt (Bettgröße
    laut Datei, Düse vor START_PRINT geheizt, fehlende K2-Einstellungen).
    Abschaltbar mit ``klipper_generisch: False`` (dann Abbruch wie a).

Modellvergleich: Teilstring ohne Groß/Klein, der Treffer muss am Wortanfang
stehen (davor kein Buchstabe/keine Ziffer) – „K2“ passt zu „Creality K2
Plus“, „K2 Plus“, „K2Plus“, aber nicht zu „Original Prusa MK2.5S“.

Lesen: nie die ganze Datei. Per seek das Dateiende (ENDE_BYTES = 512 KiB,
Orca/Creality-Print-Konfigblock ≈ 20–30 KiB) und der Anfang: Blöcke zu
64 KiB, bis 64 KiB Befehlszeilen gesehen sind (Vorschaubilder und ein
vorangestellter Konfigblock sind Kommentare und zählen nicht), höchstens
KOPF_MAX = 1 MiB. Steht printer_model im Ende, wird der Anfang nicht
gelesen; steht es im Anfang, endet das Lesen dort. Angeschnittene Zeilen an den Blockgrenzen werden verworfen. Lesen
und Auswerten laufen im eigenen Thread (Klipper-``aio_executor``, wie
virtual_sdcard), der Reactor wartet nur; ohne aio_executor (ältere Klipper)
direkt – auch das sind nur wenige ms (20-MB-Datei: < 1,6 MiB gelesen).

Dateipfad wie SDCARD_PRINT_FILE (virtual_sdcard): FILENAME relativ zum
``[virtual_sdcard] path``, ein führendes ``/`` wird entfernt, Unterordner
erlaubt; existiert der Name nicht genau so, wird er wie bei virtual_sdcard
ohne Groß/Klein in der Dateiliste gesucht. Nur Endungen gcode/g/gco.

G-Code:
  KTWO_DATEI_PRUEFEN FILENAME=<datei>    – prüfen (Fehler = Abbruch)
  KTWO_DATEI_PRUEFUNG [AKTIV=0|1]        – ohne AKTIV: Zustand; AKTIV=0
                                           schaltet bis zum Neustart ab

Config (alle Optionen optional):
    [k2_dateipruefung]
    #aktiv: True
    #erlaubte_modelle: K2          # Komma-Liste
    #slicer_profil: Creality K2 Plus 0.4 nozzle - Pi   # nur für die Meldung
    #bambu_befehle: M1006, G380, …  # Komma-Liste, Default s. BAMBU_BEFEHLE
    #klipper_generisch: True       # Regel d) (allg. Klipper-Profil mit Warnung)

Status (printer["k2_dateipruefung"]): aktiv, erlaubte_modelle, letzte
{datei, ergebnis ok|generisch|abgelehnt|unbekannt|aus|fehler, modell, bambu,
gelesen, dauer_ms}.

Konsole (k2_konsole): Hinweis/OK als ``// [k2_dateipruefung] …``, Abschalten
als Warnung. Ablehnungen kommen als G-Code-Fehler ``!! [k2_dateipruefung] …``
(Klipper schreibt sie selbst ins klippy.log).

Ohne Klipper importierbar (Tests: ``python3 test_k2_dateipruefung.py``).
Python-3.9-kompatibel.
"""
import logging
import os
import re
import time

try:
    from . import k2_konsole as _K2K
except ImportError:
    import k2_konsole as _K2K

MODUL = "k2_dateipruefung"
ENDE_BYTES = 512 * 1024        # Dateiende (Slicer-Konfigblock)
KOPF_BEFEHLE = 64 * 1024       # so viele Bytes Befehlszeilen am Anfang
KOPF_MAX = 1024 * 1024         # höchstens so weit in den Anfang lesen
BLOCK = 64 * 1024
ERLAUBTE_STANDARD = "K2"
SLICER_PROFIL_STANDARD = "Creality K2 Plus 0.4 nozzle - Pi"
# Bambu-Lab-only (Bambu-Firmware; weder Klipper noch Creality kennen sie).
# Pflicht laut Auftrag: M1006 G380 M620 M621 M1002 M975 M960 G29.1 M142 M977.
# Ergänzt: M622/M623 (Bedingungsblöcke), M624/M625 (Objekt-Marken),
# M970/M974 (Resonanzmessung), M982 (Motorgeräusch), M991 (Schichtmeldung),
# M1007 (Massenschätzung), G392 (Verstopfungserkennung A1).
BAMBU_BEFEHLE = ("M1006", "G380", "M620", "M621", "M1002", "M975", "M960",
                 "G29.1", "M142", "M977", "M622", "M623", "M624", "M625",
                 "M970", "M974", "M982", "M991", "M1007", "G392")
VALID_GCODE_EXTS = ("gcode", "g", "gco")   # wie klippy/extras/virtual_sdcard

_MODELL_ZEILE_RE = re.compile(
    rb"^[ \t]*;[ \t]*printer_model[ \t]*=[ \t]*([^\r\n]*)", re.M)
_BEFEHL_RE = re.compile(rb"^(?:[Nn]\d+[ \t]*)?([GgMm]\d+(?:\.\d+)?)(?![\d.])")
_FLAVOR_RE = re.compile(
    rb"^[ \t]*;[ \t]*gcode_flavor[ \t]*=[ \t]*([^\r\n]*)", re.M)
_FLAECHE_RE = re.compile(
    rb"^[ \t]*;[ \t]*printable_area[ \t]*=[ \t]*([^\r\n]*)", re.M)
START_MAKROS = (b"START_PRINT", b"PRINT_START")
# Lange Definitionszeilen ohne Bewegung – zählen nicht zum Befehls-Budget
_NICHT_ZAEHLEN = (b"EXCLUDE_OBJECT_DEFINE", b"M486")


class PruefFehler(Exception):
    """Datei nicht prüfbar (fehlt, nicht lesbar)."""


# ---------------------------------------------------------------------
# Reine Funktionen (ohne Klipper; laufen im Hilfs-Thread)
# ---------------------------------------------------------------------
def _text(b, n=120):
    s = b.decode("utf-8", "replace").strip()
    return s if len(s) <= n else s[:n - 1] + "…"


def modell_erlaubt(modell, erlaubte):
    """Teilstring ohne Groß/Klein, Treffer am Wortanfang."""
    m = (modell or "").casefold()
    for e in erlaubte:
        e = (e or "").strip().casefold()
        if not e:
            continue
        i = m.find(e)
        while i >= 0:
            if i == 0 or not m[i - 1].isalnum():
                return True
            i = m.find(e, i + 1)
    return False


def letztes_modell(daten):
    """Wert der letzten ``; printer_model = …``-Zeile (str) oder None
    (fehlt/leer)."""
    wert = None
    for mo in _MODELL_ZEILE_RE.finditer(daten):
        wert = mo.group(1)
    if wert is None:
        return None
    return _text(wert) or None


def _letzter_wert(rx, daten):
    wert = None
    for mo in rx.finditer(daten):
        wert = mo.group(1)
    return _text(wert) if wert is not None else None


def flaeche_text(wert):
    """``0x0,250x0,250x250,0x250`` -> ``250 × 250 mm`` (None bei Unsinn)."""
    try:
        pkt = [tuple(float(v) for v in p.split("x"))
               for p in (wert or "").split(",") if p.strip()]
        xs, ys = [p[0] for p in pkt], [p[1] for p in pkt]
        return "%g × %g mm" % (max(xs) - min(xs), max(ys) - min(ys))
    except (ValueError, IndexError):
        return None


def befehl_der_zeile(zeile):
    """Befehlswort einer G-Code-Zeile (``b"M620 S1"`` -> ``"M620"``) oder
    None (Kommentar/leer/kein G/M-Befehl)."""
    s = zeile.strip()
    if not s or s.startswith(b";"):
        return None
    mo = _BEFEHL_RE.match(s)
    return mo.group(1).decode("ascii").upper() if mo else None


def pruefe_datei(pfad, befehle=BAMBU_BEFEHLE, ende_bytes=ENDE_BYTES,
                 kopf_befehle=KOPF_BEFEHLE, kopf_max=KOPF_MAX, erlaubte=None):
    """Ende + Anfang lesen. Ergebnis: dict groesse, modell (str|None),
    ort ("ende"|"anfang"|None), bambu (Liste in Reihenfolge des ersten
    Auftretens), gelesen (Bytes), befehlsbytes, dauer_ms, flavor,
    flaeche, start_makro, m109_vor_start. Mit ``erlaubte`` wird der Anfang
    auch gelesen, wenn das Modell aus dem Ende NICHT passt (Regel d).
    OSError geht an den Aufrufer."""
    t0 = time.monotonic()
    befehle = frozenset(b.strip().upper() for b in befehle if b.strip())
    res = {"groesse": 0, "modell": None, "ort": None, "bambu": [],
           "gelesen": 0, "befehlsbytes": 0, "dauer_ms": 0., "flavor": None,
           "flaeche": None, "start_makro": False, "m109_vor_start": False}
    with open(pfad, "rb") as f:
        f.seek(0, os.SEEK_END)
        groesse = f.tell()
        res["groesse"] = groesse
        start = max(0, groesse - ende_bytes)
        f.seek(start)
        ende = f.read(groesse - start)
        res["gelesen"] += len(ende)
        if start > 0:                     # angeschnittene erste Zeile weg
            nl = ende.find(b"\n")
            ende = ende[nl + 1:] if nl >= 0 else b""
        modell = letztes_modell(ende)
        res["flavor"] = _letzter_wert(_FLAVOR_RE, ende)
        res["flaeche"] = _letzter_wert(_FLAECHE_RE, ende)
        if modell is not None:
            res["modell"], res["ort"] = modell, "ende"
            if erlaubte is not None and not modell_erlaubt(modell, erlaubte):
                _kopf(f, res, befehle, kopf_befehle, kopf_max)
        else:
            _kopf(f, res, befehle, kopf_befehle, kopf_max)
    res["dauer_ms"] = round((time.monotonic() - t0) * 1000., 1)
    return res


def _kopf(f, res, befehle, kopf_befehle, kopf_max):
    f.seek(0)
    rest = b""
    gelesen = 0
    gefunden = res["bambu"]
    fertig = False
    while not fertig and gelesen < kopf_max:
        block = f.read(min(BLOCK, kopf_max - gelesen))
        if block:
            gelesen += len(block)
            zeilen = (rest + block).split(b"\n")
            rest = zeilen.pop()           # unvollständig -> nächster Block
        else:                             # Dateiende: letzte Zeile ohne \n
            zeilen, rest, fertig = [rest], b"", True
        for z in zeilen:
            s = z.strip()
            if not s:
                continue
            if s.startswith(b";"):
                if b"gcode_flavor" in s and res["flavor"] is None:
                    res["flavor"] = _letzter_wert(_FLAVOR_RE, s)
                if b"printable_area" in s and res["flaeche"] is None:
                    res["flaeche"] = _letzter_wert(_FLAECHE_RE, s)
                if b"printer_model" in s and res["modell"] is None:
                    m = letztes_modell(s)
                    if m is not None:     # Modell entscheidet allein
                        res["modell"], res["ort"] = m, "anfang"
                        fertig = True
                        break
                continue
            wort = s.split(None, 1)[0].upper()
            if wort in _NICHT_ZAEHLEN:
                continue
            res["befehlsbytes"] += len(s)
            if wort in START_MAKROS:
                res["start_makro"] = True
            cmd = befehl_der_zeile(s)
            if cmd == "M109" and not res["start_makro"]:
                res["m109_vor_start"] = True
            if cmd in befehle and cmd not in gefunden:
                gefunden.append(cmd)
            if res["befehlsbytes"] >= kopf_befehle:
                fertig = True
                break
    res["gelesen"] += gelesen


def ist_generisch(befund):
    """Regel d): Klipper-Flavor, Startmakro am Anfang, kein Bambu-Befehl."""
    return ((befund.get("flavor") or "").strip().lower() == "klipper"
            and bool(befund.get("start_makro"))
            and not befund.get("bambu"))


def bewerte(befund, erlaubte, generisch=True):
    """-> "ok" | "generisch" | "abgelehnt_modell" | "abgelehnt_bambu" |
    "unbekannt"."""
    if befund.get("modell"):
        if modell_erlaubt(befund["modell"], erlaubte):
            return "ok"
        if generisch and ist_generisch(befund):
            return "generisch"
        return "abgelehnt_modell"
    if befund.get("bambu"):
        return "abgelehnt_bambu"
    return "unbekannt"


# ---------------------------------------------------------------------
# Klipper-Modul
# ---------------------------------------------------------------------
class K2Dateipruefung:
    cmd_PRUEFEN_help = ("Druckdatei prüfen: falsches Druckerprofil/Bambu-"
                        "Startcode -> Fehler (FILENAME=<datei>)")
    cmd_PRUEFUNG_help = ("Druckdatei-Prüfung: Zustand bzw. AKTIV=0|1 (bis "
                         "zum Neustart)")

    def __init__(self, config):
        self.printer = config.get_printer()
        self.aktiv = config.getboolean("aktiv", True)
        self.erlaubte = [e for e in config.getlist(
            "erlaubte_modelle", (ERLAUBTE_STANDARD,)) if e.strip()]
        if not self.erlaubte:
            raise config.error("[%s] erlaubte_modelle: mindestens ein "
                               "Eintrag" % (config.get_name(),))
        self.befehle = tuple(b.strip().upper() for b in config.getlist(
            "bambu_befehle", BAMBU_BEFEHLE) if b.strip())
        self.slicer_profil = config.get("slicer_profil",
                                        SLICER_PROFIL_STANDARD)
        self.generisch = config.getboolean("klipper_generisch", True)
        self.konsole = _K2K.Konsole(self.printer, MODUL)
        self.letzte = {}
        self.executor = None
        try:
            aio = self.printer.load_object(config, "aio_executor", None)
            if aio is not None:
                self.executor = aio.allocate_executor("k2_dateipruef")
        except Exception:
            logging.exception("[%s] aio_executor nicht nutzbar – lese "
                              "direkt" % (MODUL,))
            self.executor = None
        self.gcode = self.printer.lookup_object("gcode")
        self.gcode.register_command("KTWO_DATEI_PRUEFEN", self.cmd_PRUEFEN,
                                    desc=self.cmd_PRUEFEN_help)
        self.gcode.register_command("KTWO_DATEI_PRUEFUNG", self.cmd_PRUEFUNG,
                                    desc=self.cmd_PRUEFUNG_help)

    # --- Hilfen --------------------------------------------------------
    def _pfad(self, gcmd, name):
        """Wie virtual_sdcard SDCARD_PRINT_FILE: relativ zum G-Code-
        Verzeichnis, sonst ohne Groß/Klein aus der Dateiliste."""
        sd = self.printer.lookup_object("virtual_sdcard", None)
        sddir = getattr(sd, "sdcard_dirname", None)
        if sd is None or not sddir:
            raise gcmd.error("[%s] [virtual_sdcard] fehlt – Datei nicht "
                             "prüfbar, Druck nicht gestartet" % (MODUL,))
        if name.startswith("/"):
            name = name[1:]
        teile = name.split("/")
        if name and all(t not in ("", ".", "..") for t in teile) \
                and _endung_ok(teile[-1]):
            pfad = os.path.join(sddir, name)
            if os.path.isfile(pfad):
                return pfad
        try:
            liste = sd.get_file_list(check_subdirs=True)
        except Exception as exc:
            logging.exception("[%s] Dateiliste" % (MODUL,))
            raise gcmd.error("[%s] G-Code-Verzeichnis nicht lesbar (%s) – "
                             "Druck nicht gestartet" % (MODUL, exc))
        nach_klein = {}
        for eintrag in liste:
            nach_klein.setdefault(eintrag[0].lower(), eintrag[0])
        treffer = nach_klein.get(name.lower())
        if treffer is None:
            raise gcmd.error("[%s] Datei ‚%s‘ nicht im G-Code-Verzeichnis "
                             "gefunden – Druck nicht gestartet"
                             % (MODUL, name))
        return os.path.join(sddir, treffer)

    def _lesen(self, pfad):
        if self.executor is not None:
            return self.executor.submit(pruefe_datei, pfad, self.befehle,
                                        ENDE_BYTES, KOPF_BEFEHLE, KOPF_MAX,
                                        self.erlaubte)
        return pruefe_datei(pfad, self.befehle, erlaubte=self.erlaubte)

    def _merke(self, name, ergebnis, befund=None):
        befund = befund or {}
        self.letzte = {"datei": name, "ergebnis": ergebnis,
                       "modell": befund.get("modell"),
                       "bambu": list(befund.get("bambu") or []),
                       "gelesen": befund.get("gelesen", 0),
                       "dauer_ms": befund.get("dauer_ms", 0.)}

    def _neu_slicen(self):
        return ("im Slicer Drucker ‚%s‘ wählen und neu slicen (MakerWorld-"
                "3MF mit Strg+I importieren statt öffnen)"
                % (self.slicer_profil,))

    # --- G-Code ---------------------------------------------------------
    def cmd_PRUEFEN(self, gcmd):
        if not self.aktiv:
            self._merke(gcmd.get("FILENAME", ""), "aus")
            self.konsole.warnung(
                "Prüfung abgeschaltet (KTWO_DATEI_PRUEFUNG AKTIV=1 schaltet "
                "sie wieder ein) – Datei wird ungeprüft gedruckt")
            return
        name = gcmd.get("FILENAME", None)
        if not name:
            raise gcmd.error("[%s] FILENAME=<datei> fehlt – Druck nicht "
                             "gestartet" % (MODUL,))
        pfad = self._pfad(gcmd, name)
        try:
            befund = self._lesen(pfad)
        except OSError as exc:
            self._merke(name, "fehler")
            raise gcmd.error("[%s] Datei ‚%s‘ nicht lesbar (%s) – Druck "
                             "nicht gestartet" % (MODUL, name, exc))
        except Exception as exc:
            logging.exception("[%s] Prüfung fehlgeschlagen" % (MODUL,))
            self._merke(name, "fehler")
            raise gcmd.error(
                "[%s] Prüfung fehlgeschlagen (%s: %s) – Druck nicht "
                "gestartet. Ohne Prüfung drucken: KTWO_DATEI_PRUEFUNG AKTIV=0"
                % (MODUL, type(exc).__name__, exc))
        urteil = bewerte(befund, self.erlaubte, self.generisch)
        logging.info("[%s] %s: %s (Modell %r, Bambu %s, %d B gelesen, "
                     "%.1f ms)", MODUL, name, urteil, befund.get("modell"),
                     ",".join(befund["bambu"]) or "-", befund["gelesen"],
                     befund["dauer_ms"])
        if urteil == "abgelehnt_modell":
            self._merke(name, "abgelehnt", befund)
            raise gcmd.error(
                "[%s] Datei ist für ‚%s‘ gesliced, nicht für den K2 Plus – "
                "%s. Druck nicht gestartet."
                % (MODUL, befund["modell"], self._neu_slicen()))
        if urteil == "abgelehnt_bambu":
            self._merke(name, "abgelehnt", befund)
            raise gcmd.error(
                "[%s] Datei enthält Bambu-Lab-Befehle (%s) und kein "
                "Druckerprofil (printer_model) – sie ist für einen Bambu-"
                "Drucker gesliced, nicht für den K2 Plus. %s. Druck nicht "
                "gestartet." % (MODUL, ", ".join(befund["bambu"]),
                                _gross(self._neu_slicen())))
        if urteil == "generisch":
            self._merke(name, "generisch", befund)
            teile = ["Bettgröße laut Datei %s (K2: 350 × 350 mm) – Objekte "
                     "evtl. nicht mittig" % flaeche_text(befund["flaeche"])
                     if flaeche_text(befund.get("flaeche")) else None,
                     "Düse wird VOR START_PRINT aufgeheizt (tropft beim "
                     "Referenzieren, PrTouch tastet heiß)"
                     if befund.get("m109_vor_start") else None,
                     "keine K2-Geschwindigkeiten/-Beschleunigungen, kein "
                     "CFS-Mehrfarbwechsel"]
            self.konsole.warnung(
                "‚%s‘ ist für ‚%s‘ gesliced (allgemeines Klipper-Profil mit "
                "START_PRINT) – wird gedruckt. Hinweis: %s. Besser: %s"
                % (name, befund["modell"], "; ".join(t for t in teile if t),
                   self._neu_slicen()))
            return
        if urteil == "unbekannt":
            self._merke(name, "unbekannt", befund)
            self.konsole.info(
                "Hinweis: ‚%s‘ – Druckerprofil unbekannt (kein "
                "‚; printer_model‘, keine Bambu-Befehle am Anfang); Datei "
                "wird trotzdem gedruckt" % (name,))
            return
        self._merke(name, "ok", befund)
        self.konsole.info("‚%s‘ geprüft: Druckerprofil ‚%s‘ – ok"
                          % (name, befund["modell"]))

    def cmd_PRUEFUNG(self, gcmd):
        wert = gcmd.get("AKTIV", None)
        if wert is None:
            l_ = self.letzte
            zeilen = ["[%s] Prüfung %s, erlaubte Modelle: %s" % (
                MODUL, "AKTIV" if self.aktiv else "AUS (bis zum Neustart "
                "bzw. AKTIV=1)", ", ".join(self.erlaubte))]
            if l_:
                zeilen.append("Letzte Prüfung: ‚%s‘ -> %s%s%s" % (
                    l_["datei"], l_["ergebnis"],
                    (", Modell ‚%s‘" % l_["modell"]) if l_["modell"] else "",
                    (", Bambu-Befehle %s" % ", ".join(l_["bambu"]))
                    if l_["bambu"] else ""))
            gcmd.respond_info("\n".join(zeilen))
            return
        wert = str(wert).strip()
        if wert not in ("0", "1"):
            raise gcmd.error("[%s] AKTIV=0|1" % (MODUL,))
        self.aktiv = wert == "1"
        if self.aktiv:
            self.konsole.info("Druckdatei-Prüfung wieder EINGESCHALTET")
        else:
            self.konsole.warnung(
                "Druckdatei-Prüfung AUSGESCHALTET bis zum Neustart – Dateien "
                "werden ungeprüft gedruckt (KTWO_DATEI_PRUEFUNG AKTIV=1 "
                "schaltet sie wieder ein)")

    def get_status(self, eventtime=None):
        return {"aktiv": self.aktiv, "erlaubte_modelle": list(self.erlaubte),
                "letzte": dict(self.letzte)}


def _gross(s):
    return s[:1].upper() + s[1:]


def _endung_ok(name):
    return name[name.rfind(".") + 1:] in VALID_GCODE_EXTS


def load_config(config):
    return K2Dateipruefung(config)
