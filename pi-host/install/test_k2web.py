#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline-Test für k2web.py (moonraker.conf / nginx-Upload-Block) und die
Dienst-Vorlagen k2-startwaechter.service / k2-creality-bruecke.service.
Synthetische Dateien, kein Netz, kein Root. Aufruf: python3 test_k2web.py"""
import ast
import os
import re
import subprocess
import sys
import tempfile

HIER = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HIER)
import k2web as w  # noqa: E402

ok = 0
fehler = []


def check(cond, name):
    global ok
    if cond:
        ok += 1
        print("ok    " + name)
    else:
        fehler.append(name)
        print("FEHLER " + name)


# --------------------------------------------------------------------- Moonraker
MR = """[server]
host: 0.0.0.0
port: 7125

[authorization]
trusted_clients:
    10.0.0.0/8
    127.0.0.0/8
cors_domains:
    *.local
    *://my.mainsail.xyz

[octoprint_compat]

[history]
"""
neu, st = w.moonraker_api_key_aus(MR)
check(st == "geaendert", "M1 Option fehlt -> geaendert")
check(neu.count("enable_api_key: False") == 1, "M1 genau eine Zeile enable_api_key")
abschnitt = neu.split("[authorization]")[1].split("[octoprint_compat]")[0]
check("enable_api_key: False" in abschnitt, "M1 Zeile steht im Abschnitt [authorization]")
check(neu.replace(w.APIKEY_KOMMENTAR + "\n", "").replace(w.APIKEY_ZEILE + "\n", "") == MR,
      "M1 sonst nichts verändert (Fortsetzungszeilen trusted_clients bleiben)")
neu2, st2 = w.moonraker_api_key_aus(neu)
check(st2 == "unveraendert" and neu2 == neu, "M2 zweiter Lauf: unveraendert, bytegleich")

mr_true = MR.replace("[authorization]\n", "[authorization]\nenable_api_key: True\n")
neu, st = w.moonraker_api_key_aus(mr_true)
check(st == "geaendert" and "enable_api_key: True" not in neu
      and neu.count("enable_api_key") == 1, "M3 True -> False ersetzt, nur eine Zeile")
for wert in ("False", "false", "no", "off", "0", "False  # schon aus"):
    t = MR.replace("[authorization]\n", "[authorization]\nenable_api_key: %s\n" % wert)
    n, s_ = w.moonraker_api_key_aus(t)
    check(s_ == "unveraendert" and n == t, "M4 schon aus (%s) -> unveraendert" % wert)
t = MR.replace("[authorization]\n", "[authorization]\nenable_api_key = yes\n")
n, s_ = w.moonraker_api_key_aus(t)
check(s_ == "geaendert" and "= yes" not in n, "M5 Schreibweise 'enable_api_key = yes' erkannt")
t = MR.replace("[history]\n", "[history]\nenable_api_key: True\n")
n, s_ = w.moonraker_api_key_aus(t)
check(s_ == "geaendert" and "[history]\nenable_api_key: True" in n,
      "M6 gleichnamige Option in anderem Abschnitt bleibt unberührt")
t = MR.replace("[authorization]\ntrusted", "[authorization]\n#enable_api_key: True\ntrusted")
n, s_ = w.moonraker_api_key_aus(t)
check(s_ == "geaendert" and "#enable_api_key: True" in n and "\nenable_api_key: False" in n,
      "M7 auskommentierte Zeile zählt nicht")
ohne = MR.replace("[authorization]\n", "[authorisierung_nicht]\n")
n, s_ = w.moonraker_api_key_aus(ohne)
check(s_ == "kein_abschnitt" and n == ohne, "M8 ohne [authorization]: nichts geändert")
crlf = MR.replace("\n", "\r\n")
n, s_ = w.moonraker_api_key_aus(crlf)
check(s_ == "geaendert" and "\r\nenable_api_key: False\r\n" in n and "\n" not in n.replace("\r\n", ""),
      "M9 CRLF-Datei bleibt CRLF")
n, s_ = w.moonraker_api_key_aus("[authorization]")
check(s_ == "geaendert" and n == "[authorization]\n%s\n%s\n" % (w.APIKEY_KOMMENTAR, w.APIKEY_ZEILE),
      "M10 Abschnitt ohne Zeilenende am Dateiende")

# --------------------------------------------------------------------- nginx
NG = """# /etc/nginx/sites-available/mainsail

server {
    listen 80 default_server;

    access_log /var/log/nginx/mainsail-access.log;
    error_log /var/log/nginx/mainsail-error.log;

    # disable this section on smaller hardware like a pi zero
    gzip on;

    root /home/pi/mainsail;
    index index.html;
    server_name _;

    # disable max upload size checks
    client_max_body_size 0;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location = /index.html {
        add_header Cache-Control "no-store, no-cache, must-revalidate";
    }

    location /websocket {
        proxy_pass http://apiserver/websocket;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
        proxy_set_header Host $http_host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }

    location ~ ^/(printer|api|access|machine|server)/ {
        proxy_pass http://apiserver$request_uri;
        proxy_http_version 1.1;
        proxy_set_header Host $http_host;
    }
}
"""
neu, st = w.nginx_upload_einfuegen(NG)
check(st == "geaendert", "N1 Block fehlt -> geaendert")
check(len(re.findall(r"location ~ \^/upload/", neu)) == 1, "N1 genau ein Upload-Block")
check(neu.index("location ~ ^/upload/") < neu.index("location /websocket"), "N1 Block steht VOR location /websocket")
check(neu.index("server {") < neu.index("location ~ ^/upload/") < neu.rindex("}"), "N1 Block im Server-Block")
check("\n    location ~ ^/upload/ {\n        rewrite ^ /server/files/upload break;\n" in neu,
      "N1 Einzug wie location /websocket")
for z in ("rewrite ^ /server/files/upload break;", "proxy_pass http://apiserver;",
          "proxy_http_version 1.1;", "proxy_set_header Host $http_host;",
          'proxy_set_header Authorization "";', "proxy_set_header X-Real-IP $remote_addr;",
          "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;"):
    block = neu[neu.index(w.MARKE_BEGIN):neu.index(w.MARKE_END)]
    check(block.count(z) == 1, "N1 Inhalt: %s" % z)
check(neu.count("{") == neu.count("}"), "N1 Klammern ausgeglichen")
neu2, st2 = w.nginx_upload_einfuegen(neu)
check(st2 == "vorhanden" and neu2 == neu, "N2 zweiter Lauf: vorhanden, bytegleich")
weg, st3 = w.nginx_upload_entfernen(neu)
check(st3 == "entfernt" and weg == NG, "N3 Entfernen stellt das Original bytegleich her")
weg2, st4 = w.nginx_upload_entfernen(NG)
check(st4 == "nicht_vorhanden" and weg2 == NG, "N4 Entfernen ohne Block: unverändert")
hand = NG.replace("    location /websocket {", """    location ~ ^/upload/ { rewrite ^ /server/files/upload break; proxy_pass http://apiserver; }

    location /websocket {""")
n, s_ = w.nginx_upload_einfuegen(hand)
check(s_ == "vorhanden_ohne_marke" and n == hand, "N5 von Hand eingetragener Block (ohne Marke): nichts doppelt")
n, s_ = w.nginx_upload_entfernen(hand)
check(s_ == "nicht_vorhanden" and n == hand, "N5 Rückbau lässt Hand-Block stehen")
n, s_ = w.nginx_upload_einfuegen(NG.replace("location /websocket", "location /ws"))
check(s_ == "kein_websocket", "N6 ohne location /websocket -> kein_websocket")
n, s_ = w.nginx_upload_einfuegen(NG.replace("http://apiserver", "http://127.0.0.1:7125"))
check(s_ == "kein_apiserver", "N7 ohne apiserver-Upstream -> kein_apiserver")
n, s_ = w.nginx_upload_einfuegen(NG.replace("\n", "\r\n"))
check(s_ == "geaendert" and "\n" not in n.replace("\r\n", ""), "N8 CRLF bleibt CRLF")
n, s_ = w.nginx_upload_einfuegen(NG.replace("    location /websocket", "\tlocation /websocket"))
check("\n\tlocation ~ ^/upload/ {\n\t    rewrite" in n, "N9 Tab-Einzug übernommen")

# --------------------------------------------------------------------- CLI
with tempfile.TemporaryDirectory() as tmp:
    def cli(*a):
        r = subprocess.run([sys.executable, "-I", os.path.join(HIER, "k2web.py")] + list(a),
                           capture_output=True, text=True)
        return r.returncode, r.stdout
    mrc = os.path.join(tmp, "moonraker.conf")
    with open(mrc, "w") as fh:
        fh.write(MR)
    os.chmod(mrc, 0o640)
    rc, out = cli("moonraker", mrc)
    check(rc == 0 and out.strip().endswith("STATUS: geaendert") and open(mrc).read() == MR,
          "C1 ohne --aus: nur Prüfung, Datei unverändert")
    rc, out = cli("moonraker", mrc, "--aus", mrc)
    inhalt = open(mrc).read()
    check(rc == 0 and inhalt.count("enable_api_key: False") == 1 and (os.stat(mrc).st_mode & 0o777) == 0o640,
          "C2 --aus auf dieselbe Datei: geändert, Rechte 0640 erhalten")
    m1 = os.stat(mrc).st_mtime_ns
    rc, out = cli("moonraker", mrc, "--aus", mrc)
    check(rc == 0 and "STATUS: unveraendert" in out and os.stat(mrc).st_mtime_ns == m1,
          "C3 zweiter Lauf: unveraendert, Datei nicht neu geschrieben")
    site = os.path.join(tmp, "mainsail")
    with open(site, "w") as fh:
        fh.write(NG)
    aus = os.path.join(tmp, "site.neu")
    rc, out = cli("nginx", site, "--aus", aus)
    check(rc == 0 and "STATUS: geaendert" in out and open(site).read() == NG
          and open(aus).read().count("location ~ ^/upload/") == 1, "C4 nginx --aus: Original bleibt, Ausgabe mit Block")
    rc, out = cli("nginx", aus)
    check(rc == 0 and "STATUS: vorhanden" in out, "C5 nginx auf Ausgabe: vorhanden")
    rc, out = cli("nginx-entfernen", aus, "--aus", aus)
    check(rc == 0 and open(aus).read() == NG, "C6 nginx-entfernen --aus: Original zurück")
    with open(site, "w") as fh:
        fh.write(NG.replace("location /websocket", "location /x"))
    rc, out = cli("nginx", site, "--aus", aus)
    check(rc == 3 and "kein_websocket" in out, "C7 Exit 3 bei kein_websocket")
    rc, out = cli("moonraker", os.path.join(tmp, "fehlt.conf"))
    check(rc == 2 and "STATUS: unlesbar" in out, "C8 Exit 2 bei fehlender Datei")
    rc, out = cli("unbekannt", mrc)
    check(rc == 2, "C9 unbekannter Unterbefehl -> Exit 2")

# --------------------------------------------------------------------- Filament-Seite /cfs/
SITE = ("server {\n    listen 80;\n    root /home/pi/mainsail;\n\n    location / {\n"
        "        try_files $uri $uri/ /index.html;\n    }\n\n    location /websocket {\n"
        "        proxy_pass http://apiserver/websocket;\n    }\n}\n")
n, st = w.nginx_cfs_einfuegen(SITE, "/home/pi/cfs-faecher")
check(st == "geaendert" and n.count("location /cfs/ {") == 1
      and n.index("location /cfs/") < n.index("location /websocket")
      and "    alias /home/pi/cfs-faecher/;" in n, "CFS1 Block vor /websocket, alias mit /")
n2, st2 = w.nginx_cfs_einfuegen(n, "/home/pi/cfs-faecher")
check(st2 == "vorhanden" and n2 == n, "CFS2 zweiter Lauf: vorhanden, bytegleich")
n3, st3 = w.nginx_cfs_einfuegen(n, "/home/pi/anders")
check(st3 == "geaendert" and n3.count("location /cfs/") == 1 and "alias /home/pi/anders/;" in n3,
      "CFS3 anderer Ordner -> Block ersetzt, nicht doppelt")
hand = SITE.replace("    location /websocket", "    location /cfs/ { alias /x/; }\n\n    location /websocket")
check(w.nginx_cfs_einfuegen(hand, "/home/pi/cfs-faecher") == (hand, "vorhanden_ohne_marke"),
      "CFS4 von Hand eingetragener /cfs/-Block bleibt")
check(w.nginx_cfs_einfuegen(SITE.replace("location /websocket", "location /ws"), "/p")[1] == "kein_websocket",
      "CFS5 ohne /websocket: nichts geändert")
check(w.nginx_cfs_entfernen(n) == (SITE, "entfernt"), "CFS6 Rückbau bytegleich zum Original")
check(w.nginx_cfs_entfernen(SITE)[1] == "nicht_vorhanden", "CFS7 Rückbau ohne Block: nicht_vorhanden")
# Upload-Block und /cfs/ nebeneinander, Entfernen des einen lässt den anderen stehen
beide, _ = w.nginx_upload_einfuegen(n)
check(w.nginx_upload_entfernen(beide)[0] == n and w.nginx_cfs_entfernen(beide)[0] == w.nginx_upload_einfuegen(SITE)[0],
      "CFS8 Upload-Block und /cfs/ unabhängig entfernbar")

# Menü navi.json
V = [{"title": "CFS-Fächer", "href": "/cfs/", "target": "_self", "position": 65}]
t, s_ = w.navi_zusammenfuehren("", V)
check(s_ == "geaendert" and __import__("json").loads(t) == V, "N1 ohne Datei -> Eintrag angelegt")
check(w.navi_zusammenfuehren(t, V) == (t, "unveraendert"), "N2 zweiter Lauf unverändert")
eigen = '[{"title": "Kamera", "href": "/cam/"}, {"title": "alt", "href": "/cfs/"}]'
t, s_ = w.navi_zusammenfuehren(eigen, V)
j = __import__("json").loads(t)
check(s_ == "geaendert" and j[0]["href"] == "/cam/" and j[1] == V[0] and len(j) == 2,
      "N3 eigene Einträge bleiben, gleiches href ersetzt")
check(w.navi_zusammenfuehren("{kaputt", V) == ("{kaputt", "defekt"), "N4 kaputte Datei -> defekt, nichts geändert")

# Mainsail-Abfragen gegen einen kleinen Moonraker-Ersatz
import http.server  # noqa: E402
import json as _json  # noqa: E402
import threading  # noqa: E402
import urllib.parse as _up  # noqa: E402
DB = {}


class _H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _antwort(self, code, obj):
        b = _json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        u = _up.urlparse(self.path)
        if u.path == "/server/info":
            return self._antwort(200, {"result": {}})
        q = dict(_up.parse_qsl(u.query))
        if q.get("key") in DB:
            return self._antwort(200, {"result": {"value": DB[q["key"]]}})
        self._antwort(404, {"error": {"code": 404}})

    def do_POST(self):
        d = _json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        DB[d["key"]] = d["value"]
        self._antwort(200, {"result": d})

    def do_DELETE(self):
        q = dict(_up.parse_qsl(_up.urlparse(self.path).query))
        if q.get("key") in DB:
            del DB[q["key"]]
            return self._antwort(200, {"result": {}})
        self._antwort(404, {"error": {"code": 404}})


srv = http.server.HTTPServer(("127.0.0.1", 0), _H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = "http://127.0.0.1:%d" % srv.server_port
DB["uiSettings.confirmOnCancelJob"] = False        # im Menü bewusst aus
st, ges = w.notaus_setzen(URL, versuche=1)
check(st == "gesetzt" and ges == ["uiSettings.confirmOnEmergencyStop"]
      and DB["uiSettings.confirmOnEmergencyStop"] is True and DB["uiSettings.confirmOnCancelJob"] is False,
      "NA1 nur fehlende Abfrage gesetzt, bewusst abgeschaltete bleibt aus")
check(w.notaus_setzen(URL, versuche=1) == ("vorhanden", []), "NA2 zweiter Lauf: vorhanden, nichts gesetzt")
check(w.notaus_entfernen(URL, ges) == "entfernt" and "uiSettings.confirmOnEmergencyStop" not in DB
      and "uiSettings.confirmOnCancelJob" in DB, "NA3 Rückbau nimmt nur die selbst gesetzte zurück")
check(w.notaus_setzen("http://127.0.0.1:9", versuche=1, pause=0) == ("kein_moonraker", []),
      "NA4 Moonraker nicht erreichbar -> kein_moonraker")
r = subprocess.run([sys.executable, "-I", os.path.join(HIER, "k2web.py"), "notaus", URL, "--versuche", "1"],
                   capture_output=True, text=True)
check(r.returncode == 0 and "GESETZT: uiSettings.confirmOnEmergencyStop" in r.stdout
      and r.stdout.strip().endswith("STATUS: gesetzt"), "NA5 Aufruf von der Kommandozeile")
srv.shutdown()

# --------------------------------------------------------------------- Einheiten
ERSETZ = {"@USER@": "pi", "@PY@": "/usr/bin/python3", "@TOOLS@": "/home/x/k2-tools",
          "@DIENST@": "klipper", "@MOONRAKER_DIENST@": "moonraker"}
for name, prog in (("k2-startwaechter", "k2_startwaechter.py"),
                   ("k2-creality-bruecke", "k2_creality_bruecke.py")):
    vorlage = open(os.path.join(HIER, name + ".service"), encoding="utf-8").read()
    check(vorlage.startswith("# k2-pi-paket: %s" % name), "E %s: Paketmarke in Zeile 1 (Rückbau)" % name)
    check("/home/" not in vorlage and "User=@USER@" in vorlage, "E %s: Vorlage ohne feste Pfade/Benutzer" % name)
    fertig = vorlage
    for k, v in ERSETZ.items():
        fertig = fertig.replace(k, v)
    check(not re.search(r"@[A-Z_]+@", fertig), "E %s: alle Platzhalter ersetzbar" % name)
    check(re.search(r"^ExecStart=\S+ /home/x/k2-tools/%s$" % re.escape(prog), fertig, re.M) is not None,
          "E %s: ExecStart startet %s aus dem Werkzeugordner" % (name, prog))
    check("WantedBy=multi-user.target" in fertig and "Restart=always" in fertig,
          "E %s: aktivierbar, startet nach Absturz neu" % name)

for f in ("k2web.py", "test_k2web.py", "k2_startwaechter.py", "k2_creality_bruecke.py"):
    ast.parse(open(os.path.join(HIER, f), encoding="utf-8").read(), f, feature_version=(3, 9))
check(True, "Python-3.9-Syntax (Pi: Debian-Python ≥ 3.11, moonraker-env)")

print("ERGEBNIS: %s (%d/%d ok)" % ("ok" if not fehler else "FEHLER", ok, ok + len(fehler)))
sys.exit(1 if fehler else 0)
