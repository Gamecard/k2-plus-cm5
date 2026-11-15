#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_mesh_guard.py – Ausreißer-Wächter für die Bettvermessung (K2 Plus).

Upstream-Klipper-Zusatzmodul ``[k2_mesh_guard]`` (k2-module.cfg),
README-k2_mesh_guard.md. Idee aus dem Projekt K2 OpenKlipper (GPLv3), kein
Code übernommen.

Problem: Beim K2 Plus ist die Düse der Taster ([k2_prtouch] = Objekt
``probe``). Hängt an der Düse ein Materialrest, liefert ein einzelner
Messpunkt der Bettvermessung einen zu hohen Wert – die erste Schicht liegt
dort falsch. Das soll VOR der ersten Schicht auffallen.

Verfahren:

1. Nach dem letzten Messpunkt wird jeder Punkt mit dem Median seiner
   direkten Nachbarn (links/rechts/vorn/hinten) verglichen. Weicht er um
   mehr als ``threshold`` ab, gilt er als Ausreißer.
   Ergänzung gegenüber der reinen Median-Regel (``edge_trend``, Default
   an): fehlt der gegenüberliegende Nachbar (Rand, Ecke, Lücke), wird der
   Nachbarwert linear über den Punkt hinaus fortgesetzt (2·n1 − n2). Ohne
   das hätte eine Ecke bei schrägem oder gewölbtem Bett von Haus aus eine
   Abweichung (Zahlen im README) – echte Bettform soll kein Ausreißer sein.
   Ein erkannter Ausreißer dient danach nicht mehr als Vergleichswert für
   seine Nachbarn (auffälligster Punkt zuerst), damit er weder seine Ecke
   noch einen Punkt zwischen zwei Ausreißern mit „ansteckt“.
2. ``mode: report`` (Default): nur melden, nichts neu antasten, nichts
   ändern. ``mode: act``: jeden Ausreißer erneut anfahren, ``repeats``-mal
   antasten und den Median aus altem und neuen Werten übernehmen.
   ``mode: off``: Modul hängt sich gar nicht ein.
3. Mehr als ``max_points`` Ausreißer: nichts neu antasten, nur melden
   („Bett/Düse prüfen“) – dann stimmt etwas Grundsätzliches nicht.

Einhängestelle (Klipper-Stand des Druckers, Dateien nur gelesen):

- ``bed_mesh.py:93``   ``BedMesh.bmc = BedMeshCalibrate(config, self)``
- ``bed_mesh.py:335``  ``BedMeshCalibrate.probe_mgr = ProbeManager(…,
  self.probe_finalize)``
- ``bed_mesh.py:823``  ``ProbeManager.probe_helper =
  probe.ProbePointsHelper(config, finalize_cb, [])`` (Offsets an: Z. 824)
- ``probe.py:431``     ``ProbePointsHelper.finalize_callback`` – wird in
  ``probe.py:471`` (``_invoke_callback``) bei JEDEM Aufruf frisch vom
  Objekt gelesen. Dieses Attribut wird nach ``klippy:connect`` umhüllt.
  Der Schnell-Scan (``bed_mesh.py:825/1224``, nur Wirbelstrom-Sonden)
  behält seinen eigenen Verweis und bleibt unberührt.
- ``probe.py:480``     ``ProbePointsHelper.start_probe(gcmd)`` wird nur
  umhüllt, um den G-Code-Befehl der laufenden Vermessung zu kennen (die
  Sonde braucht ihn für ``run_probe(gcmd)``); Ablauf unverändert.

Zeitpunkt des Rückrufs (``probe.py:503-515``): Werkzeug steht auf
``horizontal_move_z`` (Z. 504), die Ergebnisse sind abgeholt (Z. 506), die
Sonden-Sitzung ist NOCH OFFEN (Ende erst Z. 515). Nachgemessen wird über
genau diese Sitzung: ``probe.probe_session`` (``SampleAveragingHelper``,
``probe.py:318``; ``start_probe_session`` gibt ``self`` zurück, Z. 345).
Anfahren mit den Mitteln des Helfers selbst: ``_raise_tool()``
(``probe.py:460``) und ``_move_next(i)`` (``probe.py:473``, inkl.
Sonden-Versatz) – also gleiche XY-Position, Hubhöhe und Geschwindigkeit
wie bei der ursprünglichen Messung.

Sicherheit:

- Fehlt ein erwartetes Attribut oder tritt im Wächter-Teil eine Ausnahme
  auf, wird das einmal gemeldet und der Original-Rückruf bekommt die
  UNVERÄNDERTEN Ergebnisse. Die Bettvermessung scheitert durch dieses
  Modul nicht.
- Ausnahme: wirft die Sonde selbst beim Nachmessen (oder Klipper bei der
  Anfahrt einen G-Code-Fehler), wird dieser Fehler normal weitergereicht –
  wie bei jedem anderen Messpunkt der Vermessung.
- Die Ergebnisliste wird erst NACH vollständig gelungenem Nachmessen
  geändert (alle Werte auf einmal).

ANNAHMEN (am Drucker zu prüfen, s. README):

- ANNAHME: Die Ergebnisobjekte sind ``ProbeResult``-Tupel mit ``bed_x``,
  ``bed_y``, ``bed_z`` und ``_replace`` (``probe.py:21-31, 268``).
- ANNAHME: Das Objekt ``probe`` hat ``probe_session`` mit
  ``hw_probe_session`` (offen = nicht None), ``run_probe`` und
  ``pull_probed_results`` – bei [k2_prtouch] der Fall.
- ANNAHME: ``max_shift`` begrenzt die Verschiebung „bestätigter Wert
  minus alter Wert“. Wird sie überschritten, gibt es eine Warnung; der
  bestätigte Wert wird trotzdem übernommen (er ist durch die Mehrheit der
  Messungen gedeckt).
- ANNAHME: Keine ``faulty_region_*`` in [bed_mesh]. Sind welche aktiv,
  wird die Prüfung übersprungen (Ersatzpunkte liegen neben dem Raster).

Status ``printer.k2_mesh_guard``: mode, hooked, threshold, min_neighbours,
max_points, repeats, max_shift, edge_trend, last_action, last_points,
last_outlier_count, last_outliers, last_remaining_count, counters.
G-Code: ``KTWO_MESH_GUARD_STATUS``.

Ohne Klipper und ohne Hardware testbar (test_k2_mesh_guard.py).
Python-3.9-kompatibel.
"""

import copy
import logging
import math

try:
    from . import k2_konsole as _K2K
except ImportError:
    import k2_konsole as _K2K

MODUL = "k2_mesh_guard"

MODE_OFF = "off"
MODE_REPORT = "report"
MODE_ACT = "act"
MODES = (MODE_OFF, MODE_REPORT, MODE_ACT)

# Entscheidungen (entscheide())
AKTION_AUS = "off"              # Modus off
AKTION_KEINE = "none"           # keine Ausreißer
AKTION_MELDEN = "reported"      # Ausreißer nur melden
AKTION_NACHMESSEN = "reprobed"  # Ausreißer neu antasten
AKTION_ZU_VIELE = "too_many"    # mehr als max_points -> nur melden
# weitere Werte von last_action (nur Klipper-Hülle)
AKTION_GESPERRT = "blocked"     # act gewünscht, Nachmessen nicht möglich
AKTION_UEBERSPRUNGEN = "skipped"
AKTION_FEHLER = "error"

STANDARD = {
    "mode": MODE_REPORT,
    "threshold": 0.25,
    "min_neighbours": 2,
    "max_points": 6,
    "repeats": 2,
    "max_shift": 1.0,
    "edge_trend": True,
}
# (kleinster, größter Wert, Typ, Erklärung für die Fehlermeldung)
GRENZEN = {
    "threshold": (0.05, 2.0, float,
                  "mm Abweichung vom Median der Nachbarn"),
    "min_neighbours": (1, 4, int,
                       "Nachbarn, die ein Punkt mindestens braucht"),
    "max_points": (1, 20, int,
                   "Ausreißer, die höchstens neu angetastet werden"),
    "repeats": (1, 5, int, "Nachmessungen je Ausreißer"),
    "max_shift": (0.05, 5.0, float,
                  "mm Verschiebung alter -> bestätigter Wert"),
}
_WAHR = ("1", "true", "yes", "on", "ja", "wahr")
_FALSCH = ("0", "false", "no", "off", "nein", "falsch")

KOORD_STELLEN = 2        # wie bed_mesh.py:656 (round(p.bed_x, 2))
KOORD_TOLERANZ = 0.05    # mm: Koordinaten gelten als dieselbe Zeile/Spalte
LUECKE_FAKTOR = 1.6      # Nachbar nur bis 1,6 × üblicher Rasterabstand
SICHER_AB = 3            # ab so vielen Nachbarwerten ist der Median robust
EPS = 1e-9
ZREF_PROBE = 2           # bed_mesh.py:321 ZrefMode.PROBE (Zusatzpunkt)
RICHTUNGEN = ((-1, 0), (1, 0), (0, -1), (0, 1))


class OptionsFehler(ValueError):
    """Ungültige Option in [k2_mesh_guard] (deutscher Klartext)."""


# =====================================================================
# Reine Logik (ohne Klipper testbar)
# =====================================================================

def _zahl(name, wert, typ):
    text = ("%s" % (wert,)).strip()
    try:
        if isinstance(wert, bool):
            raise ValueError(text)
        if typ is int:
            if isinstance(wert, float):
                if not wert.is_integer():
                    raise ValueError(text)
                zahl = int(wert)
            else:
                zahl = int(text)       # "2.0" oder "zwei" -> Fehler
        else:
            zahl = float(text)
    except (TypeError, ValueError, OverflowError):
        raise OptionsFehler(
            "[%s] Option '%s' = '%s' ist keine %s (Dezimaltrenner ist der "
            "Punkt)" % (MODUL, name, text,
                        "ganze Zahl" if typ is int else "Zahl"))
    if not math.isfinite(zahl):
        raise OptionsFehler("[%s] Option '%s' = '%s' ist keine endliche Zahl"
                            % (MODUL, name, text))
    return zahl


def optionen_pruefen(roh=None):
    """Roh-Optionen (Texte oder Zahlen, ``None`` = Default) prüfen.

    Rückgabe: vollständiges Wörterbuch mit geprüften Werten. Wirft
    ``OptionsFehler`` mit deutschem Klartext (Name, Wert, erlaubter
    Bereich)."""
    roh = dict(roh or {})
    unbekannt = sorted(k for k in roh if k not in STANDARD)
    if unbekannt:
        raise OptionsFehler(
            "[%s] unbekannte Option(en): %s – erlaubt sind %s"
            % (MODUL, ", ".join(unbekannt), ", ".join(sorted(STANDARD))))
    opt = dict(STANDARD)
    wert = roh.get("mode")
    if wert is not None:
        mode = ("%s" % (wert,)).strip().lower()
        if mode not in MODES:
            raise OptionsFehler(
                "[%s] Option 'mode' = '%s' ist ungültig – erlaubt sind "
                "off (aus), report (nur melden) und act (Ausreißer neu "
                "antasten)" % (MODUL, wert))
        opt["mode"] = mode
    for name, (lo, hi, typ, sinn) in GRENZEN.items():
        wert = roh.get(name)
        if wert is None:
            continue
        zahl = _zahl(name, wert, typ)
        if zahl < lo or zahl > hi:
            raise OptionsFehler(
                "[%s] Option '%s' = %s liegt außerhalb des erlaubten "
                "Bereichs %s bis %s (%s)" % (MODUL, name, ("%s" % (wert,)
                                                           ).strip(),
                                             lo, hi, sinn))
        opt[name] = zahl
    wert = roh.get("edge_trend")
    if wert is not None:
        if isinstance(wert, bool):
            opt["edge_trend"] = wert
        else:
            text = ("%s" % (wert,)).strip().lower()
            if text in _WAHR:
                opt["edge_trend"] = True
            elif text in _FALSCH:
                opt["edge_trend"] = False
            else:
                raise OptionsFehler(
                    "[%s] Option 'edge_trend' = '%s' ist ungültig – erlaubt "
                    "sind True und False" % (MODUL, wert))
    return opt


def median(werte):
    """Median einer nicht leeren Zahlenfolge (gerade Anzahl: Mittel der
    beiden mittleren Werte)."""
    geordnet = sorted(float(w) for w in werte)
    if not geordnet:
        raise ValueError("Median einer leeren Liste")
    mitte = len(geordnet) // 2
    if len(geordnet) % 2:
        return geordnet[mitte]
    return (geordnet[mitte - 1] + geordnet[mitte]) / 2.0


def _gruppiere(werte):
    """Koordinaten einer Achse zu Zeilen/Spalten zusammenfassen.

    Werte werden auf ``KOORD_STELLEN`` gerundet; Werte, die höchstens
    ``KOORD_TOLERANZ`` vom ersten Wert ihrer Gruppe entfernt liegen,
    gehören zur selben Gruppe. Rückgabe: (Gruppenindex je Wert, Mitten)."""
    gerundet = [round(w, KOORD_STELLEN) for w in werte]
    gruppen = []          # Listen eindeutiger Werte
    for w in sorted(set(gerundet)):
        if gruppen and w - gruppen[-1][0] <= KOORD_TOLERANZ + EPS:
            gruppen[-1].append(w)
        else:
            gruppen.append([w])
    nummer = {}
    for i, gruppe in enumerate(gruppen):
        for w in gruppe:
            nummer[w] = i
    mitten = [sum(g) / len(g) for g in gruppen]
    return [nummer[w] for w in gerundet], mitten


def _schritt(mitten):
    """Üblicher Abstand benachbarter Zeilen/Spalten (Median) oder None."""
    if len(mitten) < 2:
        return None
    return median([b - a for a, b in zip(mitten[:-1], mitten[1:])])


class Raster(object):
    """Messpunkte ``(x, y, z)`` einem Zeilen-/Spaltenraster zuordnen.

    Funktioniert für beliebige Punktzahlen, Zickzack-Reihenfolge und
    unvollständige Raster. Ein zweiter Punkt in derselben Zelle wird nicht
    bewertet (``ort[i] is None``)."""

    def __init__(self, punkte):
        self.x, self.y, self.z = [], [], []
        for p in punkte:
            x, y, z = float(p[0]), float(p[1]), float(p[2])
            if not (math.isfinite(x) and math.isfinite(y)
                    and math.isfinite(z)):
                raise ValueError("Messpunkt mit ungültigem Wert: %r" % (p,))
            self.x.append(x)
            self.y.append(y)
            self.z.append(z)
        spalte, self.x_mitten = _gruppiere(self.x)
        zeile, self.y_mitten = _gruppiere(self.y)
        self.schritt = (_schritt(self.x_mitten), _schritt(self.y_mitten))
        self.zellen = {}      # (Spalte, Zeile) -> Index
        self.ort = []         # Index -> (Spalte, Zeile) oder None (doppelt)
        for i in range(len(self.z)):
            zelle = (spalte[i], zeile[i])
            if zelle in self.zellen:
                self.ort.append(None)
            else:
                self.zellen[zelle] = i
                self.ort.append(zelle)

    def abstand(self, a, b):
        return (abs(self.x_mitten[a[0]] - self.x_mitten[b[0]])
                + abs(self.y_mitten[a[1]] - self.y_mitten[b[1]]))

    def nachbar(self, zelle, richtung):
        """Belegte Nachbarzelle in ``richtung`` oder None (auch None, wenn
        dazwischen eine Lücke von mehr als ``LUECKE_FAKTOR`` Schritten
        liegt)."""
        ziel = (zelle[0] + richtung[0], zelle[1] + richtung[1])
        if ziel not in self.zellen:
            return None
        schritt = self.schritt[0] if richtung[0] else self.schritt[1]
        if schritt is None:
            return None
        if self.abstand(zelle, ziel) > LUECKE_FAKTOR * schritt + EPS:
            return None
        return ziel

    def nachbarwerte(self, zelle, ohne=(), randtrend=True):
        """Vergleichswerte der direkten Nachbarn einer Zelle.

        ``ohne``: Indizes, die nicht als Stütze dienen dürfen (sicher
        erkannte Ausreißer). ``randtrend``: fehlt der gegenüberliegende
        Nachbar, den Nachbarwert linear fortsetzen (wenn der übernächste
        Punkt in derselben Richtung vorhanden ist)."""
        werte = []
        for richtung in RICHTUNGEN:
            n1 = self.nachbar(zelle, richtung)
            if n1 is None or self.zellen[n1] in ohne:
                continue
            wert = self.z[self.zellen[n1]]
            if randtrend:
                gegen = self.nachbar(zelle, (-richtung[0], -richtung[1]))
                if gegen is None or self.zellen[gegen] in ohne:
                    n2 = self.nachbar(n1, richtung)
                    if n2 is not None and self.zellen[n2] not in ohne:
                        d12 = self.abstand(n1, n2)
                        if d12 > EPS:
                            wert += ((wert - self.z[self.zellen[n2]])
                                     * self.abstand(zelle, n1) / d12)
            werte.append(wert)
        return werte


def analysiere(punkte, threshold=STANDARD["threshold"],
               min_neighbours=STANDARD["min_neighbours"], randtrend=True):
    """Ausreißer einer Vermessung bestimmen.

    ``punkte``: Folge von ``(x, y, z)`` in beliebiger Reihenfolge.
    Rückgabe ``(ausreisser, bewertet)``: ``ausreisser`` ist eine nach Index
    sortierte Liste von Wörterbüchern (index, x, y, z, median, deviation,
    neighbours); ``bewertet`` die Zahl der Punkte mit genug Nachbarn.

    Ablauf: Der auffälligste Punkt wird als Ausreißer festgehalten und
    dient danach nicht mehr als Stütze für seine Nachbarn; das wiederholt
    sich, bis kein Punkt mehr über der Schwelle liegt. Punkte mit
    mindestens ``SICHER_AB`` Nachbarwerten (robuster Median) kommen dabei
    vor Punkten mit weniger Nachbarn (Ecken). So zieht ein Ausreißer
    weder seine Ecke noch einen Punkt zwischen zwei Ausreißern mit."""
    raster = Raster(punkte)
    anzahl = len(raster.z)
    gefunden = {}             # Index -> Bewertung beim Festhalten

    def bewerte(i):
        zelle = raster.ort[i]
        if zelle is None:
            return None
        werte = raster.nachbarwerte(zelle, gefunden, randtrend)
        if not werte:
            return None
        mitte = median(werte)
        return (mitte, raster.z[i] - mitte, len(werte))

    bew = [bewerte(i) for i in range(anzahl)]
    while True:
        beste = None
        for i in range(anzahl):
            b = bew[i]
            if (b is None or i in gefunden or b[2] < min_neighbours
                    or abs(b[1]) <= threshold + EPS):
                continue
            rang = (b[2] >= SICHER_AB, abs(b[1]))
            if beste is None or rang > beste[0]:
                beste = (rang, i)
        if beste is None:
            break
        i = beste[1]
        gefunden[i] = bew[i]
        # nur Punkte neu bewerten, für die i Stütze war (bis 2 Zellen)
        zelle = raster.ort[i]
        for richtung in RICHTUNGEN:
            for weite in (1, 2):
                j = raster.zellen.get((zelle[0] + weite * richtung[0],
                                       zelle[1] + weite * richtung[1]))
                if j is not None and j not in gefunden:
                    bew[j] = bewerte(j)
    bewertet = len(gefunden) + sum(
        1 for i, b in enumerate(bew)
        if i not in gefunden and b is not None and b[2] >= min_neighbours)
    ausreisser = []
    for i in sorted(gefunden):
        b = gefunden[i]
        ausreisser.append({
            "index": i,
            "x": round(raster.x[i], KOORD_STELLEN),
            "y": round(raster.y[i], KOORD_STELLEN),
            "z": raster.z[i],
            "median": b[0],
            "deviation": b[1],
            "neighbours": b[2],
        })
    return ausreisser, bewertet


def finde_ausreisser(punkte, threshold=STANDARD["threshold"],
                     min_neighbours=STANDARD["min_neighbours"],
                     randtrend=True):
    """Nur die Ausreißer-Liste von ``analysiere()``."""
    return analysiere(punkte, threshold, min_neighbours, randtrend)[0]


def bestaetigter_wert(alt, neue):
    """Bestätigter Z-Wert: Median aus altem Wert und Nachmessungen.

    Zwei übereinstimmende Nachmessungen überstimmen einen einzelnen
    Fehlwert; bleiben die Nachmessungen beim alten Wert, bleibt er."""
    neue = [float(z) for z in neue]
    if not neue:
        return float(alt)
    return median([float(alt)] + neue)


def entscheide(mode, anzahl, max_points):
    """Was mit ``anzahl`` Ausreißern geschieht (AKTION_*)."""
    if mode == MODE_OFF:
        return AKTION_AUS
    if anzahl <= 0:
        return AKTION_KEINE
    if anzahl > max_points:
        return AKTION_ZU_VIELE
    if mode == MODE_ACT:
        return AKTION_NACHMESSEN
    return AKTION_MELDEN


def text_ausreisser(a):
    """Eine Zeile je Ausreißer: Position, alter Wert, Nachbar-Median,
    Abweichung."""
    return ("Ausreißer bei X=%.2f Y=%.2f: gemessen %+.3f mm, Nachbar-Median "
            "%+.3f mm (%d Nachbarn), Abweichung %+.3f mm"
            % (a["x"], a["y"], a["z"], a["median"], a["neighbours"],
               a["deviation"]))


def text_bestaetigung(a, neue, bestaetigt):
    """Eine Zeile je nachgemessenem Punkt: neue Werte, bestätigter Wert."""
    return ("X=%.2f Y=%.2f nachgemessen: %s mm -> bestätigt %+.3f mm "
            "(vorher %+.3f mm, Verschiebung %+.3f mm)"
            % (a["x"], a["y"], ", ".join("%+.3f" % z for z in neue),
               bestaetigt, a["z"], bestaetigt - a["z"]))


def text_zusammenfassung(aktion, anzahl, punkte, opt):
    """Kopfzeile der Meldung nach einer Vermessung (None = nichts melden)."""
    if aktion in (AKTION_AUS, AKTION_KEINE):
        return None
    kopf = ("%d von %d Messpunkten: Abweichung über %.2f mm vom Median "
            "der Nachbarn" % (anzahl, punkte, opt["threshold"]))
    if aktion == AKTION_ZU_VIELE:
        return ("%s – das sind mehr als max_points = %d. Es wird NICHT neu "
                "angetastet. Bett/Düse prüfen (Materialrest an der Düse, "
                "Schmutz auf dem Bett, Druckplatte richtig aufgelegt?)"
                % (kopf, opt["max_points"]))
    if aktion == AKTION_NACHMESSEN:
        return ("%s – wird je %d-mal neu angetastet (mode: act)"
                % (kopf, opt["repeats"]))
    return ("%s – nur Meldung (mode: report), die Vermessung bleibt "
            "unverändert. Düse auf Materialreste prüfen" % (kopf,))


def ersetze_z(ergebnis, z):
    """Ergebnisobjekt der Sonde mit neuem ``bed_z`` (Original unberührt)."""
    ersetzen = getattr(ergebnis, "_replace", None)
    if callable(ersetzen):
        return ersetzen(bed_z=z)
    neu = copy.copy(ergebnis)
    neu.bed_z = z
    return neu


# =====================================================================
# Klipper-Hülle
# =====================================================================

class WaechterFehler(Exception):
    """Fehler im Wächter-Teil (führt nie zum Abbruch der Vermessung)."""


class K2MeshGuard:
    OPTIONEN = tuple(sorted(STANDARD))

    def __init__(self, config):
        self.printer = printer = config.get_printer()
        self.name = config.get_name()
        roh = {}
        for name in self.OPTIONEN:
            roh[name] = config.get(name, None)
        try:
            self.opt = optionen_pruefen(roh)
        except OptionsFehler as exc:
            raise config.error(str(exc))
        self.mode = self.opt["mode"]
        self.konsole = _K2K.Konsole(printer, MODUL)
        # Einhängestelle (erst nach klippy:connect bekannt)
        self.hooked = False
        self._helper = None
        self._mgr = None
        self._orig_finalize = None
        self._orig_start = None
        self._gcmd = None
        self._weiterreichen = False
        # Status
        self.last_action = AKTION_AUS if self.mode == MODE_OFF \
            else AKTION_KEINE
        self.last_points = 0
        self.last_outliers = []
        self.last_remaining = 0
        self.counters = {
            "meshes": 0,            # geprüfte Vermessungen
            "outliers": 0,          # Ausreißer insgesamt
            "reprobed_points": 0,   # neu angetastete Punkte
            "replaced_values": 0,   # geänderte Werte
            "too_many": 0,          # Vermessungen über max_points
            "skipped": 0,           # nicht prüfbar (faulty_region …)
            "guard_errors": 0,      # Fehler im Wächter-Teil
        }
        printer.register_event_handler("klippy:connect",
                                       self._handle_connect)
        gcode = printer.lookup_object("gcode")
        gcode.register_command("KTWO_MESH_GUARD_STATUS", self.cmd_STATUS,
                               desc=self.cmd_STATUS_help)

    # ------------------------------------------------------------------
    # Einhängen
    # ------------------------------------------------------------------
    def _handle_connect(self):
        if self.mode == MODE_OFF:
            logging.info("[%s] mode: off – nicht eingehängt" % (MODUL,))
            return
        try:
            fehlt = self._einhaengen()
        except Exception as exc:
            logging.exception("[%s] Einhängen fehlgeschlagen" % (MODUL,))
            fehlt = "%s: %s" % (type(exc).__name__, exc)
        if fehlt:
            self.hooked = False
            self.konsole.warnung(
                "nicht aktiv – %s. Die Bettvermessung läuft unverändert, "
                "aber OHNE Ausreißer-Prüfung" % (fehlt,))
        else:
            logging.info("[%s] eingehängt (mode: %s, threshold %.2f mm)"
                         % (MODUL, self.mode, self.opt["threshold"]))

    def _einhaengen(self):
        """Rückruf der Bettvermessung umhüllen. Rückgabe: None = ok, sonst
        Text, was fehlt (dann ist NICHTS verändert)."""
        bed_mesh = self.printer.lookup_object("bed_mesh", None)
        if bed_mesh is None:
            return "kein [bed_mesh] in der Konfiguration"
        bmc = getattr(bed_mesh, "bmc", None)
        if bmc is None:
            return "bed_mesh hat kein Attribut 'bmc' (anderer Klipper-Stand?)"
        mgr = getattr(bmc, "probe_mgr", None)
        if mgr is None:
            return ("bed_mesh.bmc hat kein Attribut 'probe_mgr' (anderer "
                    "Klipper-Stand?)")
        helper = getattr(mgr, "probe_helper", None)
        if helper is None:
            return ("bed_mesh.bmc.probe_mgr hat kein Attribut "
                    "'probe_helper' (anderer Klipper-Stand?)")
        orig = getattr(helper, "finalize_callback", None)
        if not callable(orig):
            return ("der Messpunkt-Helfer von bed_mesh hat keinen Rückruf "
                    "'finalize_callback' (anderer Klipper-Stand?)")
        self._helper = helper
        self._mgr = mgr
        self._orig_finalize = orig
        start = getattr(helper, "start_probe", None)
        if self.mode == MODE_ACT and callable(start):
            # nur für das Nachmessen nötig (G-Code-Befehl der Vermessung)
            self._orig_start = start
            helper.start_probe = self._start_probe
        helper.finalize_callback = self._finalize
        self.hooked = True
        return None

    def _start_probe(self, gcmd):
        # nur den Befehl der laufenden Vermessung merken (probe.py:480)
        self._gcmd = gcmd
        try:
            return self._orig_start(gcmd)
        finally:
            self._gcmd = None

    # ------------------------------------------------------------------
    # Rückruf nach dem letzten Messpunkt
    # ------------------------------------------------------------------
    def _finalize(self, results):
        self._weiterreichen = False
        weiter = results
        try:
            neu = self._pruefen(results)
            if neu is not None:
                weiter = self._uebernehmen(results, neu)
        except Exception as exc:
            if self._weiterreichen:
                # Fehler der Sonde/der Anfahrt: wie bei jedem Messpunkt
                self._weiterreichen = False
                raise
            self.counters["guard_errors"] += 1
            self.last_action = AKTION_FEHLER
            logging.exception("[%s] Fehler im Wächter" % (MODUL,))
            self.konsole.warnung(
                "interner Fehler (%s: %s) – die Vermessung wird mit den "
                "UNVERÄNDERTEN Messwerten abgeschlossen, Details im "
                "klippy.log" % (type(exc).__name__, exc), log=False)
            self._heben_versuchen()
            weiter = results
        return self._orig_finalize(weiter)

    def _uebernehmen(self, results, neu):
        """Geprüfte Werte in die Ergebnisliste schreiben (auf einmal)."""
        if isinstance(results, list) and len(results) == len(neu):
            results[:] = neu
            return results
        return neu

    def _zeilen(self, zeilen, warnung=False):
        melde = self.konsole.warnung if warnung else self.konsole.info
        for zeile in zeilen:
            if zeile:
                melde(zeile)

    def _pruefen(self, results):
        """Ausreißer bestimmen, melden und (mode act) nachmessen.

        Rückgabe: None = Ergebnisse unverändert lassen, sonst neue Liste."""
        opt = self.opt
        anzahl = len(results)
        ende = anzahl
        mgr = self._mgr
        ersatz = getattr(mgr, "get_substitutes", None)
        if callable(ersatz) and ersatz():
            self.counters["skipped"] += 1
            self.last_action = AKTION_UEBERSPRUNGEN
            self.konsole.warnung(
                "[bed_mesh] nutzt faulty_region_* – die Ausreißer-Prüfung "
                "wird übersprungen (Ersatzpunkte liegen neben dem Raster)",
                schluessel="faulty", pause=3600.)
            return None
        zref = getattr(mgr, "get_zero_ref_mode", None)
        if callable(zref) and zref() == ZREF_PROBE:
            ende -= 1   # letzter Punkt = Nullpunkt außerhalb des Rasters
        soll = getattr(self._helper, "probe_points", None)
        try:
            nutze_soll = soll is not None and len(soll) == anzahl
        except TypeError:
            nutze_soll = False
        punkte = []
        for i in range(ende):
            r = results[i]
            if nutze_soll:
                punkte.append((soll[i][0], soll[i][1], r.bed_z))
            else:
                punkte.append((r.bed_x, r.bed_y, r.bed_z))
        ausreisser, _bewertet = analysiere(
            punkte, opt["threshold"], opt["min_neighbours"],
            opt["edge_trend"])
        aktion = entscheide(self.mode, len(ausreisser), opt["max_points"])
        eintraege = [self._eintrag(a) for a in ausreisser]
        self.counters["meshes"] += 1
        self.counters["outliers"] += len(ausreisser)
        if aktion == AKTION_ZU_VIELE:
            self.counters["too_many"] += 1
        self.last_points = len(punkte)
        self.last_outliers = eintraege
        self.last_remaining = len(ausreisser)
        self.last_action = aktion
        if aktion == AKTION_KEINE:
            logging.info("[%s] %d Messpunkte geprüft, kein Ausreißer"
                         % (MODUL, len(punkte)))
            return None
        self._zeilen([text_zusammenfassung(aktion, len(ausreisser),
                                           len(punkte), opt)]
                     + [text_ausreisser(a) for a in ausreisser],
                     warnung=True)
        if aktion != AKTION_NACHMESSEN:
            return None
        hindernis = self._hindernis(anzahl)
        if hindernis:
            self.last_action = AKTION_GESPERRT
            self.konsole.warnung(
                "Nachmessen nicht möglich – %s. Die Vermessung bleibt "
                "unverändert, Düse/Bett prüfen" % (hindernis,))
            return None
        neu = self._nachmessen(results, ausreisser, eintraege)
        # Kontrolle mit den bestätigten Werten
        punkte = [(p[0], p[1], neu[i].bed_z) for i, p in enumerate(punkte)]
        rest, _bewertet = analysiere(punkte, opt["threshold"],
                                     opt["min_neighbours"],
                                     opt["edge_trend"])
        self.last_remaining = len(rest)
        if rest:
            self._zeilen(
                ["%d Ausreißer bleiben auch nach dem Nachmessen – der Wert "
                 "ist echt oder der Materialrest hängt noch an der Düse. "
                 "Bett/Düse prüfen, dann neu vermessen" % (len(rest),)]
                + [text_ausreisser(a) for a in rest], warnung=True)
        else:
            self.konsole.info("nach dem Nachmessen ist kein Punkt mehr "
                              "auffällig (%d nachgemessen)"
                              % (len(ausreisser),))
        return neu

    @staticmethod
    def _eintrag(a):
        return {"x": a["x"], "y": a["y"], "z": round(a["z"], 6),
                "median": round(a["median"], 6),
                "deviation": round(a["deviation"], 6),
                "neighbours": a["neighbours"],
                "new_z": [], "confirmed": None, "replaced": False}

    def _sitzung(self):
        """Offene Sonden-Sitzung der laufenden Vermessung oder (None,
        Grund)."""
        probe = self.printer.lookup_object("probe", None)
        if probe is None:
            return None, "kein Objekt 'probe' (Sonde) vorhanden"
        sitzung = getattr(probe, "probe_session", None)
        if sitzung is None:
            return None, "die Sonde hat kein Attribut 'probe_session'"
        for name in ("run_probe", "pull_probed_results"):
            if not callable(getattr(sitzung, name, None)):
                return None, ("der Sonden-Sitzung fehlt die Methode '%s'"
                              % (name,))
        if getattr(sitzung, "hw_probe_session", None) is None:
            return None, ("es ist keine Sonden-Sitzung offen (manuelle "
                          "Vermessung oder anderer Klipper-Stand)")
        return sitzung, None

    def _hindernis(self, anzahl):
        """Text, warum nicht nachgemessen werden kann, oder None."""
        helper = self._helper
        if self._gcmd is None:
            return "der G-Code-Befehl der laufenden Vermessung ist unbekannt"
        for name in ("_raise_tool", "_move_next"):
            if not callable(getattr(helper, name, None)):
                return ("dem Messpunkt-Helfer fehlt die Methode '%s'"
                        % (name,))
        if not hasattr(helper, "horizontal_move_z"):
            return "die Hubhöhe horizontal_move_z ist unbekannt"
        soll = getattr(helper, "probe_points", None)
        try:
            if soll is None or len(soll) != anzahl:
                return ("die Punktliste des Helfers passt nicht zu den "
                        "Ergebnissen")
        except TypeError:
            return "die Punktliste des Helfers ist nicht lesbar"
        return self._sitzung()[1]

    # --- Hardware-Aufrufe: Fehler werden normal weitergereicht -----------
    def _sonde(self, fn, *args):
        try:
            return fn(*args)
        except Exception:
            self._weiterreichen = True
            raise

    def _fahre(self, fn, *args):
        try:
            return fn(*args)
        except Exception as exc:
            # nur echte G-Code-Fehler (z. B. „Move out of range“)
            if isinstance(exc, self.printer.command_error):
                self._weiterreichen = True
            raise

    def _heben_versuchen(self):
        """Nach einem Wächter-Fehler: Düse auf Hubhöhe (ohne zu werfen)."""
        try:
            heben = getattr(self._helper, "_raise_tool", None)
            if callable(heben) and hasattr(self._helper,
                                           "horizontal_move_z"):
                heben()
        except Exception:
            logging.exception("[%s] Anheben nach Fehler" % (MODUL,))

    def _nachmessen(self, results, ausreisser, eintraege):
        """Ausreißer neu antasten. Rückgabe: neue Ergebnisliste (die
        übergebene bleibt unverändert)."""
        opt = self.opt
        helper = self._helper
        gcmd = self._gcmd
        sitzung = self._sitzung()[0]
        if sitzung is None:
            raise WaechterFehler("Sonden-Sitzung nicht mehr offen")
        self._sonde(sitzung.pull_probed_results)   # erwartet: leer
        neu = list(results)
        for a, eintrag in zip(ausreisser, eintraege):
            i = a["index"]
            # wie probe.py:504/512: erst heben, dann XY (inkl. Versatz)
            self._fahre(helper._raise_tool)
            self._fahre(helper._move_next, i)
            for n in range(opt["repeats"]):
                if n:
                    self._fahre(helper._raise_tool)
                self._sonde(sitzung.run_probe, gcmd)
            werte = self._sonde(sitzung.pull_probed_results)
            if len(werte) != opt["repeats"]:
                raise WaechterFehler(
                    "Sonde lieferte %d statt %d Ergebnisse"
                    % (len(werte), opt["repeats"]))
            zs = [float(w.bed_z) for w in werte]
            bestaetigt = bestaetigter_wert(a["z"], zs)
            if not math.isfinite(bestaetigt):
                raise WaechterFehler("Nachmessung ohne gültigen Wert")
            self.counters["reprobed_points"] += 1
            eintrag["new_z"] = [round(z, 6) for z in zs]
            eintrag["confirmed"] = round(bestaetigt, 6)
            zeile = text_bestaetigung(a, zs, bestaetigt)
            warnung = False
            if abs(bestaetigt - a["z"]) > opt["max_shift"] + EPS:
                warnung = True
                zeile += (" – WARNUNG: Verschiebung über max_shift = %.2f "
                          "mm, Sonde/Bett prüfen" % (opt["max_shift"],))
            if abs(bestaetigt - a["z"]) > EPS:
                neu[i] = ersetze_z(results[i], bestaetigt)
                eintrag["replaced"] = True
            self._zeilen([zeile], warnung=warnung)
        # Endzustand wie nach dem letzten Messpunkt (probe.py:504, 467-469)
        self._fahre(helper._raise_tool)
        self._fahre(self.printer.lookup_object("toolhead")
                    .get_last_move_time)
        self.counters["replaced_values"] += sum(
            1 for e in eintraege if e["replaced"])
        return neu

    # ------------------------------------------------------------------
    # Status / G-Code
    # ------------------------------------------------------------------
    def get_status(self, eventtime=None):
        opt = self.opt
        return {
            "mode": self.mode,
            "hooked": self.hooked,
            "threshold": opt["threshold"],
            "min_neighbours": opt["min_neighbours"],
            "max_points": opt["max_points"],
            "repeats": opt["repeats"],
            "max_shift": opt["max_shift"],
            "edge_trend": opt["edge_trend"],
            "last_action": self.last_action,
            "last_points": self.last_points,
            "last_outlier_count": len(self.last_outliers),
            "last_outliers": [dict(e) for e in self.last_outliers],
            "last_remaining_count": self.last_remaining,
            "counters": dict(self.counters),
        }

    cmd_STATUS_help = "Zustand des Ausreißer-Wächters der Bettvermessung"

    def cmd_STATUS(self, gcmd):
        st = self.get_status()
        c = st["counters"]
        zeilen = [
            "[%s] Modus=%s eingehängt=%s Schwelle=%.2f mm Nachbarn>=%d "
            "max_points=%d repeats=%d max_shift=%.2f mm Randtrend=%s"
            % (MODUL, st["mode"], "ja" if st["hooked"] else "nein",
               st["threshold"], st["min_neighbours"], st["max_points"],
               st["repeats"], st["max_shift"],
               "an" if st["edge_trend"] else "aus"),
            "[%s] Zähler: Vermessungen=%d Ausreißer=%d nachgemessen=%d "
            "geändert=%d zu_viele=%d übersprungen=%d Wächter-Fehler=%d"
            % (MODUL, c["meshes"], c["outliers"], c["reprobed_points"],
               c["replaced_values"], c["too_many"], c["skipped"],
               c["guard_errors"]),
            "[%s] Letzte Vermessung: %d Punkte, %d Ausreißer, Ergebnis=%s, "
            "danach noch auffällig=%d"
            % (MODUL, st["last_points"], st["last_outlier_count"],
               st["last_action"], st["last_remaining_count"]),
        ]
        for e in st["last_outliers"]:
            zeile = ("[%s]   X=%.2f Y=%.2f alt %+.3f Median %+.3f "
                     "Abweichung %+.3f" % (MODUL, e["x"], e["y"], e["z"],
                                           e["median"], e["deviation"]))
            if e["confirmed"] is not None:
                zeile += (" neu %s bestätigt %+.3f%s"
                          % (", ".join("%+.3f" % z for z in e["new_z"]),
                             e["confirmed"],
                             "" if e["replaced"] else " (unverändert)"))
            zeilen.append(zeile)
        gcmd.respond_info("\n".join(zeilen))


def load_config(config):
    return K2MeshGuard(config)
