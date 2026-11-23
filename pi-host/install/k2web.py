#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2web.py – Hilfswerkzeug für install.sh/uninstall.sh (nur Standardbibliothek).

Ändert zwei Web-Einstellungen auf dem Pi, die OrcaSlicer braucht, und zwar
idempotent (zweiter Lauf = keine Änderung) und nur über eine Ausgabedatei
(``--aus``): das Schreiben an den Zielort und die Sicherung macht install.sh.

Unterbefehle (letzte Ausgabezeile immer ``STATUS: <wert>``):

  moonraker DATEI [--aus AUS]
      Setzt in moonraker.conf im Abschnitt [authorization] die Option
      ``enable_api_key: False``. Moonraker 0.11 lehnt sonst OrcaSlicer mit
      HTTP 401 „Invalid API Key“ ab.
        geaendert       Option fehlte (eingefügt) oder war wahr (ersetzt)
        unveraendert    Option steht schon auf False/no/off/0
        kein_abschnitt  kein [authorization] – NICHTS geändert (ohne diesen
                        Abschnitt prüft Moonraker keinen Schlüssel; einen neuen
                        Abschnitt legt das Werkzeug bewusst nicht an)
      Exit 0, bei unlesbarer Datei 2.

  nginx DATEI [--aus AUS]
      Fügt im Mainsail-Server-Block VOR ``location /websocket`` den Block
      ``location ~ ^/upload/ {…}`` ein (Orca-„CrealityPrint“-Upload ->
      Moonraker /server/files/upload), eingerahmt von Marken
      ``# k2-pi-paket BEGIN/END creality-upload``.
        geaendert              eingefügt
        vorhanden              Block mit Marken schon da
        vorhanden_ohne_marke   ein ``location ~ ^/upload/`` steht schon (z. B.
                               von Hand) – nichts geändert
        kein_websocket         keine ``location /websocket`` – nichts geändert
        kein_apiserver         kein ``proxy_pass http://apiserver`` – nichts
                               geändert (Upstream-Name unbekannt)
      Exit 0 bei geaendert/vorhanden/vorhanden_ohne_marke, sonst 3; 2 = unlesbar.

  nginx-entfernen DATEI [--aus AUS]
      Entfernt den mit Marken eingerahmten Block wieder (Rückbau).
        entfernt | nicht_vorhanden            Exit 0 (2 = unlesbar)

  nginx-cfs DATEI --pfad ORDNER [--aus AUS]
      Fügt VOR ``location /websocket`` den Block ``location /cfs/ { alias ORDNER/; }``
      ein (Filament-Seite „CFS-Fächer“, pi-host/cfs-faecher), eingerahmt von
      ``# k2-pi-paket BEGIN/END cfs-faecher``. Steht der Block schon mit anderem
      ORDNER da, wird er ersetzt.
        geaendert | vorhanden | vorhanden_ohne_marke   Exit 0
        kein_websocket                                 Exit 3

  nginx-cfs-entfernen DATEI [--aus AUS]
        entfernt | nicht_vorhanden            Exit 0

  navi DATEI --vorlage VORLAGE [--aus AUS]
      Mainsail-Seitenmenü (config/.theme/navi.json): Einträge aus VORLAGE
      hinzufügen bzw. gleichnamige (gleiches ``href``) ersetzen, eigene Einträge
      bleiben. DATEI darf fehlen.
        geaendert | unveraendert   Exit 0
        defekt                     DATEI ist keine JSON-Liste – nichts geändert, Exit 3

  notaus URL [--versuche N]
      Mainsail-Abfragen „Not-Aus bestätigen“ und „Druck abbrechen bestätigen“
      (Moonraker-Datenbank, Namensraum ``mainsail``, Schlüssel
      ``uiSettings.confirmOnEmergencyStop`` / ``uiSettings.confirmOnCancelJob``)
      auf true setzen – je Schlüssel NUR, wenn er noch nicht gesetzt ist (eine
      bewusste Wahl im Mainsail-Menü bleibt). URL = Moonraker, z. B.
      http://127.0.0.1:7125. Vorletzte Zeile ``GESETZT: <schlüssel,…>``.
        gesetzt          mindestens ein Schlüssel war nicht gesetzt, jetzt true
        vorhanden        alle schon gesetzt (true oder bewusst false) – nichts geändert
        kein_moonraker   Moonraker nicht erreichbar – nichts geändert, Exit 3

  notaus-entfernen URL [--keys SCHLÜSSEL,…]
      Löscht die Schlüssel wieder (Rückbau; Mainsail-Standard = keine Abfrage).
        entfernt | nicht_vorhanden   Exit 0; kein_moonraker Exit 3

Ohne ``--aus`` wird nur geprüft und der Status gemeldet (nichts geschrieben).
Mit ``--aus`` wird AUS nur geschrieben, wenn sich etwas ändert.
"""

import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

MARKE_BEGIN = "# k2-pi-paket BEGIN creality-upload"
MARKE_END = "# k2-pi-paket END creality-upload"
UPLOAD_BLOCK = (
    MARKE_BEGIN + " (OrcaSlicer CrealityPrint-Upload -> Moonraker, install.sh)",
    "location ~ ^/upload/ {",
    "    rewrite ^ /server/files/upload break;",
    "    proxy_pass http://apiserver;",
    "    proxy_http_version 1.1;",
    "    proxy_set_header Host $http_host;",
    '    proxy_set_header Authorization "";',
    "    proxy_set_header X-Real-IP $remote_addr;",
    "    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
    "}",
    MARKE_END,
)
APIKEY_ZEILE = "enable_api_key: False"
APIKEY_KOMMENTAR = ("# k2-pi-paket: OrcaSlicer sendet keinen API-Key "
                    "(sonst HTTP 401 Invalid API Key), install.sh")

_SEC_RE = re.compile(r"^\[([^\]]+)\]")
_APIKEY_RE = re.compile(r"^enable_api_key\s*[:=]\s*(.*?)\s*$", re.IGNORECASE)
_WEBSOCKET_RE = re.compile(r"^(\s*)location\s+(=\s*)?/websocket\b")
_UPLOAD_RE = re.compile(r"^\s*location\s+~\*?\s+\^?/upload/?")
_APISERVER_RE = re.compile(r"proxy_pass\s+http://apiserver\b")
FALSCH = ("false", "no", "off", "0")


CFS_BEGIN = "# k2-pi-paket BEGIN cfs-faecher"
CFS_END = "# k2-pi-paket END cfs-faecher"
_CFS_RE = re.compile(r"^\s*location\s+(\^~\s+|=\s*)?/cfs/?(\s|\{|$)")
NOTAUS_NS = "mainsail"
NOTAUS_KEYS = ("uiSettings.confirmOnEmergencyStop", "uiSettings.confirmOnCancelJob")


def cfs_block(pfad):
    return (
        CFS_BEGIN + " (Filament-Seite /cfs/, install.sh)",
        "location /cfs/ {",
        "    alias %s/;" % pfad.rstrip("/"),
        "    index index.html;",
        '    add_header Cache-Control "no-store";',
        "}",
        CFS_END,
    )


def _zeilenende(text):
    return "\r\n" if "\r\n" in text else "\n"


def moonraker_api_key_aus(text):
    """-> (neuer_text, status)"""
    nl = _zeilenende(text)
    zeilen = text.splitlines(keepends=True)
    kopf = None
    for i, z in enumerate(zeilen):
        m = _SEC_RE.match(z)
        if m and m.group(1).strip().lower() == "authorization":
            kopf = i
            break
    if kopf is None:
        return text, "kein_abschnitt"
    ende = len(zeilen)
    for j in range(kopf + 1, len(zeilen)):
        if _SEC_RE.match(zeilen[j]):
            ende = j
            break
    for j in range(kopf + 1, ende):
        m = _APIKEY_RE.match(zeilen[j].rstrip("\r\n"))
        if not m:
            continue
        wert = m.group(1).split("#")[0].split(";")[0].strip().lower()
        if wert in FALSCH:
            return text, "unveraendert"
        neu = zeilen[:j] + [APIKEY_KOMMENTAR + nl, APIKEY_ZEILE + nl] + zeilen[j + 1:]
        return "".join(neu), "geaendert"
    kz = zeilen[kopf]
    if not kz.endswith(("\n", "\r")):
        kz += nl
    neu = zeilen[:kopf] + [kz, APIKEY_KOMMENTAR + nl, APIKEY_ZEILE + nl] + zeilen[kopf + 1:]
    return "".join(neu), "geaendert"


def nginx_upload_einfuegen(text):
    """-> (neuer_text, status)"""
    zeilen = text.splitlines(keepends=True)
    if any(z.strip().startswith(MARKE_BEGIN) for z in zeilen):
        return text, "vorhanden"
    if any(_UPLOAD_RE.match(z) for z in zeilen):
        return text, "vorhanden_ohne_marke"
    ziel = None
    for i, z in enumerate(zeilen):
        m = _WEBSOCKET_RE.match(z)
        if m:
            ziel, einzug = i, m.group(1)
            break
    if ziel is None:
        return text, "kein_websocket"
    if not _APISERVER_RE.search(text):
        return text, "kein_apiserver"
    nl = _zeilenende(text)
    block = [(einzug + z if z else z) + nl for z in UPLOAD_BLOCK] + [nl]
    return "".join(zeilen[:ziel] + block + zeilen[ziel:]), "geaendert"


def _block_finden(zeilen, begin, end):
    anf = ende = None
    for i, z in enumerate(zeilen):
        s = z.strip()
        if anf is None and s.startswith(begin):
            anf = i
        elif anf is not None and s.startswith(end):
            ende = i
            break
    return anf, ende


def _block_entfernen(text, begin, end):
    zeilen = text.splitlines(keepends=True)
    anf, ende = _block_finden(zeilen, begin, end)
    if anf is None or ende is None:
        return text, "nicht_vorhanden"
    rest = zeilen[ende + 1:]
    if rest and not rest[0].strip():
        rest = rest[1:]          # Leerzeile, die das Einfügen ergänzt hat
    return "".join(zeilen[:anf] + rest), "entfernt"


def nginx_upload_entfernen(text):
    """-> (neuer_text, status)"""
    return _block_entfernen(text, MARKE_BEGIN, MARKE_END)


def nginx_cfs_einfuegen(text, pfad):
    """-> (neuer_text, status); Block für die Filament-Seite /cfs/."""
    nl = _zeilenende(text)
    zeilen = text.splitlines(keepends=True)
    anf, ende = _block_finden(zeilen, CFS_BEGIN, CFS_END)
    if anf is not None and ende is not None:
        einzug = re.match(r"^(\s*)", zeilen[anf]).group(1)
        block = [(einzug + z) + nl for z in cfs_block(pfad)]
        if zeilen[anf:ende + 1] == block:
            return text, "vorhanden"
        return "".join(zeilen[:anf] + block + zeilen[ende + 1:]), "geaendert"
    if any(_CFS_RE.match(z) for z in zeilen):
        return text, "vorhanden_ohne_marke"
    ziel = None
    for i, z in enumerate(zeilen):
        m = _WEBSOCKET_RE.match(z)
        if m:
            ziel, einzug = i, m.group(1)
            break
    if ziel is None:
        return text, "kein_websocket"
    block = [(einzug + z) + nl for z in cfs_block(pfad)] + [nl]
    return "".join(zeilen[:ziel] + block + zeilen[ziel:]), "geaendert"


def nginx_cfs_entfernen(text):
    """-> (neuer_text, status)"""
    return _block_entfernen(text, CFS_BEGIN, CFS_END)


def navi_zusammenfuehren(text, vorlage):
    """text = bisherige navi.json ('' = fehlt), vorlage = Liste -> (neuer_text, status)"""
    if text.strip():
        try:
            liste = json.loads(text)
        except ValueError:
            return text, "defekt"
        if not isinstance(liste, list):
            return text, "defekt"
    else:
        liste = []
    neu = list(liste)
    for eintrag in vorlage:
        href = eintrag.get("href")
        for i, alt in enumerate(neu):
            if isinstance(alt, dict) and alt.get("href") == href:
                neu[i] = eintrag
                break
        else:
            neu.append(eintrag)
    if neu == liste and text.strip():
        return text, "unveraendert"
    return json.dumps(neu, ensure_ascii=False, indent=2) + "\n", "geaendert"


def _moonraker(url, methode, pfad, daten=None, zeit=5):
    """-> (http_code, json|None); Verbindungsfehler -> (None, None)"""
    req = urllib.request.Request(url.rstrip("/") + pfad, method=methode,
                                 data=None if daten is None else json.dumps(daten).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=zeit) as r:
            return r.status, json.loads(r.read().decode() or "null")
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, OSError, ValueError):
        return None, None


def _notaus_pfad(key):
    return "/server/database/item?" + urllib.parse.urlencode(
        {"namespace": NOTAUS_NS, "key": key})


def notaus_setzen(url, versuche=10, pause=2.0, keys=NOTAUS_KEYS):
    """-> (status, [gesetzte Schlüssel]); status gesetzt | vorhanden | kein_moonraker"""
    code = None
    for n in range(max(1, versuche)):
        code, _j = _moonraker(url, "GET", "/server/info")
        if code is not None:
            break
        if n + 1 < versuche:
            time.sleep(pause)
    if code != 200:
        return "kein_moonraker", []
    gesetzt = []
    for key in keys:
        code, _j = _moonraker(url, "GET", _notaus_pfad(key))
        if code == 200:
            continue                  # schon gesetzt (true oder bewusst false)
        if code != 404:
            return "kein_moonraker", gesetzt
        code, _j = _moonraker(url, "POST", "/server/database/item",
                              {"namespace": NOTAUS_NS, "key": key, "value": True})
        if code != 200:
            return "kein_moonraker", gesetzt
        gesetzt.append(key)
    return ("gesetzt" if gesetzt else "vorhanden"), gesetzt


def notaus_entfernen(url, keys=NOTAUS_KEYS):
    """-> status (entfernt | nicht_vorhanden | kein_moonraker)"""
    weg = False
    for key in keys:
        code, _j = _moonraker(url, "DELETE", _notaus_pfad(key))
        if code == 200:
            weg = True
        elif code != 404:
            return "kein_moonraker"
    return "entfernt" if weg else "nicht_vorhanden"


BEFEHLE = {
    "moonraker": (moonraker_api_key_aus, ("geaendert", "unveraendert", "kein_abschnitt")),
    "nginx": (nginx_upload_einfuegen, ("geaendert", "vorhanden", "vorhanden_ohne_marke")),
    "nginx-entfernen": (nginx_upload_entfernen, ("entfernt", "nicht_vorhanden")),
    "nginx-cfs": (nginx_cfs_einfuegen, ("geaendert", "vorhanden", "vorhanden_ohne_marke")),
    "nginx-cfs-entfernen": (nginx_cfs_entfernen, ("entfernt", "nicht_vorhanden")),
    "navi": (navi_zusammenfuehren, ("geaendert", "unveraendert")),
}
MOONRAKER_BEFEHLE = ("notaus", "notaus-entfernen")
# Zusatzoption je Befehl (Pflicht): nginx-cfs --pfad ORDNER, navi --vorlage DATEI
ZUSATZ = {"nginx-cfs": "--pfad", "navi": "--vorlage"}


def _moonraker_main(befehl, argv):
    url, versuche, keys = argv[0], 10, NOTAUS_KEYS
    rest = argv[1:]
    if befehl == "notaus" and len(rest) == 2 and rest[0] == "--versuche":
        try:
            versuche = int(rest[1])
        except ValueError:
            rest = ["?"]
        else:
            rest = []
    elif befehl == "notaus-entfernen" and len(rest) == 2 and rest[0] == "--keys":
        keys = tuple(k for k in rest[1].split(",") if k)
        rest = []
    if rest:
        print("FEHLER: Aufruf notaus URL [--versuche N] bzw. notaus-entfernen URL "
              "[--keys K,…]")
        return 2
    if befehl == "notaus":
        status, gesetzt = notaus_setzen(url, versuche)
        print("GESETZT: %s" % ",".join(gesetzt))
        gut = ("gesetzt", "vorhanden")
    else:
        status = notaus_entfernen(url, keys) if keys else "nicht_vorhanden"
        gut = ("entfernt", "nicht_vorhanden")
    print("STATUS: %s" % status)
    return 0 if status in gut else 3


def main(argv):
    bekannt = set(BEFEHLE) | set(MOONRAKER_BEFEHLE)
    if len(argv) < 2 or argv[0] not in bekannt or argv[0] in ("-h", "--hilfe"):
        print(__doc__)
        return 0 if argv[:1] in (["-h"], ["--hilfe"]) else 2
    befehl, datei = argv[0], argv[1]
    if befehl in MOONRAKER_BEFEHLE:
        return _moonraker_main(befehl, argv[1:])
    aus = zusatz = None
    rest = argv[2:]
    while rest:
        if len(rest) >= 2 and rest[0] == "--aus" and aus is None:
            aus = rest[1]
        elif len(rest) >= 2 and rest[0] == ZUSATZ.get(befehl) and zusatz is None:
            zusatz = rest[1]
        else:
            rest = ["?"]
            break
        rest = rest[2:]
    if rest or (befehl in ZUSATZ and zusatz is None):
        z = (" %s WERT" % ZUSATZ[befehl]) if befehl in ZUSATZ else ""
        print("FEHLER: Aufruf %s DATEI%s [--aus AUS]" % (befehl, z))
        return 2
    try:
        with open(datei, encoding="utf-8", newline="") as fh:
            text = fh.read()
    except FileNotFoundError:
        if befehl != "navi":
            print("FEHLER: %s nicht lesbar (FileNotFoundError)" % datei)
            print("STATUS: unlesbar")
            return 2
        text = ""                       # navi.json darf fehlen
    except (OSError, UnicodeDecodeError) as e:
        print("FEHLER: %s nicht lesbar (%s)" % (datei, type(e).__name__))
        print("STATUS: unlesbar")
        return 2
    funktion, gut = BEFEHLE[befehl]
    if befehl == "nginx-cfs":
        neu, status = funktion(text, zusatz)
    elif befehl == "navi":
        try:
            with open(zusatz, encoding="utf-8") as fh:
                vorlage = json.load(fh)
            assert isinstance(vorlage, list)
        except (OSError, ValueError, AssertionError) as e:
            print("FEHLER: Vorlage %s unbrauchbar (%s)" % (zusatz, type(e).__name__))
            print("STATUS: unlesbar")
            return 2
        neu, status = funktion(text, vorlage)
    else:
        neu, status = funktion(text)
    if aus and neu != text:
        tmp = aus + ".k2tmp"
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            fh.write(neu)
        if os.path.exists(aus):
            shutil.copymode(aus, tmp)       # Rechte der Zieldatei behalten
        os.replace(tmp, aus)
    print("STATUS: %s" % status)
    return 0 if status in gut else 3


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
