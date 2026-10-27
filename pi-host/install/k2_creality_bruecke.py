#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""k2_creality_bruecke – nimmt die „CrealityPrint“-Verbindung von OrcaSlicer
an und reicht sie an Moonraker weiter (23.10.2026).

Hintergrund: Orca-Profile, die vom Creality-K2-Profil erben, verbinden sich
mit dem Creality-Agenten statt mit Moonraker:
  1. GET  http://<host>/info                     (Modell; Antwort darf fehlen)
  2. POST http://<host>/upload/<datei>           (multipart, Feld „file“)
  3. WebSocket ws://<host>:9999/  {"method":"set","params":
       {"opGcodeFile":"printprt:/mnt/UDISK/printer_data/gcodes/<datei>"}}
     bzw. bei CFS-Modellen „multiColorPrint“: {"gcode": <pfad>}
Schritt 2 leitet nginx an Moonraker (/server/files/upload) weiter
(location ^/upload/ in /etc/nginx/sites-available/mainsail). Schritt 3
übernimmt dieser Dienst: Er startet die hochgeladene Datei über Moonraker
(/printer/print/start) – also über SDCARD_PRINT_FILE samt k2_dateipruefung.

Nur Druckstart, sonst nichts (kein Abbruch, keine Box-Befehle). „colorMatch“
wird quittiert und ignoriert, die Farbzuordnung steht in der Datei.

Aufruf: k2_creality_bruecke.py [--port 9999] [--moonraker URL]
(Python aus moonraker-env, braucht tornado)
"""
import argparse
import json
import logging
import posixpath
import urllib.error
import urllib.parse
import urllib.request

PRAEFIXE = ("/mnt/UDISK/printer_data/gcodes/", "/usr/data/printer_data/gcodes/")


def datei_aus_pfad(pfad):
    """Creality-Pfad -> Dateiname relativ zum Moonraker-gcodes-Verzeichnis
    (None bei leerem/ungültigem Pfad)."""
    p = (pfad or "").strip()
    if p.startswith("printprt:"):
        p = p[len("printprt:"):]
    for pre in PRAEFIXE:
        if p.startswith(pre):
            p = p[len(pre):]
            break
    else:
        p = posixpath.basename(p)
    p = p.lstrip("/")
    teile = p.split("/")
    if not p or any(t in ("", ".", "..") for t in teile):
        return None
    return p


def druckauftrag(nachricht):
    """JSON-Text vom Orca-Agenten -> Dateiname zum Starten oder None."""
    try:
        d = json.loads(nachricht)
    except (TypeError, ValueError):
        return None
    if not isinstance(d, dict) or d.get("method") != "set":
        return None
    par = d.get("params") or {}
    if not isinstance(par, dict):
        return None
    if isinstance(par.get("opGcodeFile"), str):
        return datei_aus_pfad(par["opGcodeFile"])
    mcp = par.get("multiColorPrint")
    if isinstance(mcp, dict) and isinstance(mcp.get("gcode"), str):
        return datei_aus_pfad(mcp["gcode"])
    return None


def starte(moonraker, datei):
    url = "%s/printer/print/start?filename=%s" % (
        moonraker.rstrip("/"), urllib.parse.quote(datei))
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return True, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return False, "HTTP %d: %s" % (e.code, e.read().decode("utf-8",
                                                                "replace")[:300])
    except Exception as e:
        return False, str(e)


def main():
    import tornado.ioloop
    import tornado.web
    import tornado.websocket

    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=9999)
    ap.add_argument("--moonraker", default="http://127.0.0.1:7125")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="k2_creality_bruecke: %(levelname)s %(message)s")

    class Agent(tornado.websocket.WebSocketHandler):
        def check_origin(self, origin):
            return True

        async def on_message(self, nachricht):
            if isinstance(nachricht, bytes):
                nachricht = nachricht.decode("utf-8", "replace")
            datei = druckauftrag(nachricht)
            if datei is None:
                logging.info("ignoriert: %s", nachricht[:200])
                try:
                    self.write_message(json.dumps({"result": "ok"}))
                except tornado.websocket.WebSocketClosedError:
                    pass
                return
            loop = tornado.ioloop.IOLoop.current()
            ok, info = await loop.run_in_executor(None, starte, a.moonraker,
                                                  datei)
            if ok:
                logging.info("Druck gestartet: %s", datei)
            else:
                logging.warning("Druckstart abgelehnt (%s): %s", datei, info)
            try:
                self.write_message(json.dumps(
                    {"result": "ok" if ok else "error", "file": datei}))
            except tornado.websocket.WebSocketClosedError:
                pass

    app = tornado.web.Application([(r"/.*", Agent)])
    app.listen(a.port)
    logging.info("lauscht auf Port %d (Orca-CrealityPrint-Druckstart)", a.port)
    tornado.ioloop.IOLoop.current().start()


if __name__ == "__main__":
    main()
