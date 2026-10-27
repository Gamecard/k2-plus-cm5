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

Ohne ``--aus`` wird nur geprüft und der Status gemeldet (nichts geschrieben).
Mit ``--aus`` wird AUS nur geschrieben, wenn sich etwas ändert.
"""

import os
import re
import shutil
import sys

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


def nginx_upload_entfernen(text):
    """-> (neuer_text, status)"""
    zeilen = text.splitlines(keepends=True)
    anf = end = None
    for i, z in enumerate(zeilen):
        s = z.strip()
        if anf is None and s.startswith(MARKE_BEGIN):
            anf = i
        elif anf is not None and s.startswith(MARKE_END):
            end = i
            break
    if anf is None or end is None:
        return text, "nicht_vorhanden"
    rest = zeilen[end + 1:]
    if rest and not rest[0].strip():
        rest = rest[1:]          # Leerzeile, die das Einfügen ergänzt hat
    return "".join(zeilen[:anf] + rest), "entfernt"


BEFEHLE = {
    "moonraker": (moonraker_api_key_aus, ("geaendert", "unveraendert", "kein_abschnitt")),
    "nginx": (nginx_upload_einfuegen, ("geaendert", "vorhanden", "vorhanden_ohne_marke")),
    "nginx-entfernen": (nginx_upload_entfernen, ("entfernt", "nicht_vorhanden")),
}


def main(argv):
    if len(argv) < 2 or argv[0] not in BEFEHLE or argv[0] in ("-h", "--hilfe"):
        print(__doc__)
        return 0 if argv[:1] in (["-h"], ["--hilfe"]) else 2
    befehl, datei = argv[0], argv[1]
    aus = None
    rest = argv[2:]
    if rest:
        if len(rest) != 2 or rest[0] != "--aus":
            print("FEHLER: Aufruf %s DATEI [--aus AUS]" % befehl)
            return 2
        aus = rest[1]
    try:
        with open(datei, encoding="utf-8", newline="") as fh:
            text = fh.read()
    except (OSError, UnicodeDecodeError) as e:
        print("FEHLER: %s nicht lesbar (%s)" % (datei, type(e).__name__))
        print("STATUS: unlesbar")
        return 2
    funktion, gut = BEFEHLE[befehl]
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
