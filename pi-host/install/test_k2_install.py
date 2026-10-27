#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Attrappen-Test für install.sh / uninstall.sh – Schwerpunkt Schritt 7
(Start-Wächter, Creality-Brücke + nginx-Upload-Block, Moonraker enable_api_key),
--bewegung-gesperrt und Rückbau.

Baut je Fall in einem Temp-Ordner:
  * ein Paket (aus dem Repo- oder dem Paket-Layout dieses Ordners) mit Dummy-
    Modulen, der echten config-pi/ und frischer SHA256SUMS,
  * ein Fake-HOME: git-Klipper (klippy/klippy.py), printer_data/config mit
    printer.cfg + moonraker.conf, moonraker-env/bin/python (python3 + Fake-tornado),
  * ein Fake-/etc: systemd/system/, nginx/sites-available/mainsail,
  * Fake-Befehle im PATH: sudo, systemctl, nginx, id (Benutzer "pi", UID 1000).
Kein Root, kein Netz, keine Hardware; braucht bash, git, coreutils, python3.
Läuft install.sh mit --arch-egal --ohne-tests --ohne-batch --klipper-nicht-anfassen.

Aufruf: python3 test_k2_install.py   (Exit 0 = alles grün)
"""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile

HIER = os.path.dirname(os.path.abspath(__file__))
if os.path.isdir(os.path.join(HIER, "pi-host")) and os.path.isfile(os.path.join(HIER, "install.sh")):
    PIHOST = os.path.join(HIER, "pi-host")          # Paket-Layout
else:
    PIHOST = os.path.dirname(HIER)                  # Repo-Layout: pi-host/install/
PAKET_DATEIEN = ("install.sh", "uninstall.sh", "k2cfg.py", "k2web.py",
                 "k2_startwaechter.py", "k2-startwaechter.service",
                 "k2_creality_bruecke.py", "k2-creality-bruecke.service")

ok = 0
fehler = []


def check(cond, name, info=""):
    global ok
    if cond:
        ok += 1
        print("ok    " + name)
    else:
        fehler.append(name)
        print("FEHLER " + name + ((" – " + info) if info else ""))


MOONRAKER_CONF = """[server]
host: 0.0.0.0
port: 7125

[authorization]
trusted_clients:
    127.0.0.0/8
    ::1/128
cors_domains:
    *.local

[octoprint_compat]
"""
NGINX_SITE = """server {
    listen 80 default_server;
    root /home/pi/mainsail;
    client_max_body_size 0;

    location / {
        try_files $uri $uri/ /index.html;
    }

    location /websocket {
        proxy_pass http://apiserver/websocket;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection $connection_upgrade;
    }

    location ~ ^/(printer|api|access|machine|server)/ {
        proxy_pass http://apiserver$request_uri;
    }
}
"""
FAKE = {
    "sudo": """#!/bin/bash
[ "$1" = -n ] && shift
[ "$1" = true ] && exit 0
exec "$@"
""",
    "id": """#!/bin/bash
case "$*" in -u) echo 1000 ;; -un) echo pi ;; *) exec /usr/bin/id "$@" ;; esac
""",
    "nginx": """#!/bin/bash
echo "nginx $*" >> "$FAKE_STATE.log"
case "$1" in
  -v) echo "nginx version: attrappe" >&2; exit 0 ;;
  -t) [ -f "$FAKE_STATE.nginx-t-fehler" ] && { echo "nginx: [emerg] attrappe" >&2; exit 1; }
      a=$(tr -cd '{' < "$NGINX_SITE" | wc -c); z=$(tr -cd '}' < "$NGINX_SITE" | wc -c)
      [ "$a" = "$z" ] ;;
  *) exit 1 ;;
esac
""",
    "systemctl": """#!/bin/bash
S=${FAKE_STATE:?}; echo "systemctl $*" >> "$S.log"
q=0; [ "${2:-}" = --quiet ] && q=1
u=${@: -1}; u=${u%.service}
case "$1" in
  cat) case "$u" in klipper|moonraker|nginx) exit 0 ;; *) [ -f "$SYSTEMD_DIR/$u.service" ] ;; esac ;;
  is-active) st=$(cat "$S.active.$u" 2>/dev/null || echo inactive); [ $q = 1 ] || echo "$st"; [ "$st" = active ] ;;
  is-enabled) st=$(cat "$S.enabled.$u" 2>/dev/null || echo disabled); echo "$st"; [ "$st" = enabled ] ;;
  enable) echo enabled > "$S.enabled.$u" ;;
  disable) echo disabled > "$S.enabled.$u" ;;
  stop) echo inactive > "$S.active.$u" ;;
  start|restart) echo active > "$S.active.$u"; echo x >> "$S.neustarts.$u" ;;
  try-restart) [ "$(cat "$S.active.$u" 2>/dev/null)" = active ] && echo x >> "$S.neustarts.$u"; exit 0 ;;
  reload) echo x >> "$S.reloads.$u" ;;
  daemon-reload) ;;
  *) exit 1 ;;
esac
""",
}


def schreibe(pfad, inhalt, modus=0o644):
    os.makedirs(os.path.dirname(pfad), exist_ok=True)
    with open(pfad, "w", encoding="utf-8") as fh:
        fh.write(inhalt)
    os.chmod(pfad, modus)


def lies(pfad):
    with open(pfad, encoding="utf-8") as fh:
        return fh.read()


class Attrappe:
    def __init__(self, tmp, moonraker_env=True, tornado=True, nginx=NGINX_SITE,
                 moonraker=MOONRAKER_CONF):
        self.t = tmp
        self.P = os.path.join(tmp, "paket")
        self.H = os.path.join(tmp, "home")
        self.E = os.path.join(tmp, "etc")
        self.B = os.path.join(tmp, "bin")
        self.S = os.path.join(tmp, "state")
        self.SD = os.path.join(self.E, "systemd", "system")
        self.NS = os.path.join(self.E, "nginx", "sites-available", "mainsail")
        self.C = os.path.join(self.H, "printer_data", "config")
        self.MRC = os.path.join(self.C, "moonraker.conf")
        self.TD = os.path.join(self.H, "k2-tools")
        # Paket
        for f in PAKET_DATEIEN:
            os.makedirs(self.P, exist_ok=True)
            shutil.copy2(os.path.join(HIER, f), os.path.join(self.P, f))
        shutil.copytree(os.path.join(PIHOST, "config-pi"), os.path.join(self.P, "pi-host", "config-pi"),
                        ignore=shutil.ignore_patterns("*.md"))
        schreibe(os.path.join(self.P, "pi-host", "k2_attrappe.py"), "# Dummy-Modul\n")
        schreibe(os.path.join(self.P, "pi-host", "k2_motor_init.py"), "# Dummy k2_motor\n")
        schreibe(os.path.join(self.P, "nachbau", "attrappe.py"), "# Dummy nachbau\n")
        zeilen = []
        for wurzel, _d, dateien in os.walk(self.P):
            for f in dateien:
                p = os.path.join(wurzel, f)
                rel = os.path.relpath(p, self.P)
                with open(p, "rb") as fh:
                    zeilen.append("%s  %s\n" % (hashlib.sha256(fh.read()).hexdigest(), rel))
        schreibe(os.path.join(self.P, "SHA256SUMS"), "".join(sorted(zeilen, key=lambda z: z[66:])))
        # Fake-HOME: Klipper als git-Checkout
        k = os.path.join(self.H, "klipper")
        schreibe(os.path.join(k, "klippy", "klippy.py"), "# Attrappe\n")
        schreibe(os.path.join(k, "klippy", "extras", "__init__.py"), "")
        g = ["git", "-C", k, "-c", "user.name=attrappe", "-c", "user.email=attrappe",
             "-c", "init.defaultBranch=master"]
        for cmd in (["init", "-q"], ["add", "-A"], ["commit", "-q", "-m", "attrappe"]):
            subprocess.run(g + cmd, check=True, capture_output=True)
        schreibe(os.path.join(self.C, "printer.cfg"), "[printer]\nkinematics: none\n")
        if moonraker is not None:
            schreibe(self.MRC, moonraker)
        if moonraker_env:
            schreibe(os.path.join(self.H, "moonraker-env", "bin", "python"),
                     "#!/bin/sh\nPYTHONPATH=%s exec python3 \"$@\"\n"
                     % os.path.join(self.H, "moonraker-env", "fake"), 0o755)
            if tornado:
                schreibe(os.path.join(self.H, "moonraker-env", "fake", "tornado", "__init__.py"), "")
        os.makedirs(self.SD)
        if nginx is not None:
            schreibe(self.NS, nginx)
        for n, inh in FAKE.items():
            schreibe(os.path.join(self.B, n), inh, 0o755)
        self.env = {"HOME": self.H, "PATH": self.B + ":/usr/bin:/bin", "LANG": "C.UTF-8",
                    "FAKE_STATE": self.S, "SYSTEMD_DIR": self.SD, "NGINX_SITE": self.NS,
                    "BOOTSTART_UNIT": os.path.join(self.SD, "k2-bootstart.service")}

    def lauf(self, skript, *opt, klipper=False, env=None):
        a = ["bash", "./" + skript] + list(opt)
        if skript == "install.sh":
            a += ["--arch-egal", "--ohne-tests", "--ohne-batch"]
            if not klipper:
                a.append("--klipper-nicht-anfassen")
        r = subprocess.run(a, cwd=self.P, env=dict(self.env, **(env or {})), capture_output=True,
                           text=True, timeout=300)
        return r.returncode, r.stdout + r.stderr

    def git(self, *a):
        return subprocess.run(["git", "-C", os.path.join(self.H, "klipper"), "-c", "user.name=attrappe",
                               "-c", "user.email=attrappe"] + list(a),
                              check=True, capture_output=True, text=True).stdout.strip()

    def neuer_commit(self):
        schreibe(os.path.join(self.H, "klipper", "klippy", "neu.py"), "# neuer Upstream-Stand\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "neuer")
        return self.git("rev-parse", "HEAD")

    def zaehler(self, art, u):
        try:
            return len(lies("%s.%s.%s" % (self.S, art, u)).split())
        except OSError:
            return 0

    def zustand(self, art, u):
        try:
            return lies("%s.%s.%s" % (self.S, art, u)).strip()
        except OSError:
            return ""

    def schnappschuss(self):
        res = {}
        for wurzel in (self.H, self.E):
            for w, dirs, files in os.walk(wurzel):
                dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
                for f in files + [d + "/" for d in dirs]:
                    p = os.path.join(w, f.rstrip("/"))
                    if f.endswith("/"):
                        res[p] = "dir"
                    else:
                        with open(p, "rb") as fh:
                            res[p] = hashlib.sha256(fh.read()).hexdigest()
        return res

    def unit(self, u):
        p = os.path.join(self.SD, u + ".service")
        return lies(p) if os.path.exists(p) else None


def fall(name):
    print("\n--- " + name)


def main():
    if shutil.which("git") is None or shutil.which("bash") is None:
        print("SKIP: git/bash fehlt")
        print("ERGEBNIS: ok (0/0 ok, SKIP)")
        return 0
    with tempfile.TemporaryDirectory(prefix="k2-attrappe-") as wurzel:
        # ============================================================ A–C, E
        fall("A Erstinstallation (Standard: alles AN)")
        a = Attrappe(os.path.join(wurzel, "a"))
        mr_orig, ng_orig = lies(a.MRC), lies(a.NS)
        rc, out = a.lauf("install.sh")
        check(rc == 0, "A install.sh Exit 0", out[-1500:] if rc else "")
        sw, br = a.unit("k2-startwaechter"), a.unit("k2-creality-bruecke")
        check(sw is not None and sw.startswith("# k2-pi-paket: k2-startwaechter"), "A Einheit Start-Wächter mit Paketmarke")
        check(sw is not None and not re.search(r"@[A-Z_]+@", sw) and "User=pi" in sw
              and ("ExecStart=%s %s/k2_startwaechter.py" % (shutil.which("python3", path="/usr/bin:/bin"), a.TD)) in sw,
              "A Start-Wächter: Platzhalter ersetzt, ExecStart python3 ~/k2-tools/k2_startwaechter.py")
        check(br is not None and ("ExecStart=%s/moonraker-env/bin/python %s/k2_creality_bruecke.py" % (a.H, a.TD)) in br
              and "After=moonraker.service" in br, "A Brücke: ExecStart mit ~/moonraker-env/bin/python")
        for f in ("k2_startwaechter.py", "k2_creality_bruecke.py"):
            check(lies(os.path.join(a.TD, f)) == lies(os.path.join(HIER, f)), "A %s in ~/k2-tools identisch" % f)
        for u in ("k2-startwaechter", "k2-creality-bruecke"):
            check(a.zustand("enabled", u) == "enabled" and a.zustand("active", u) == "active",
                  "A %s aktiviert und gestartet" % u)
        ng = lies(a.NS)
        check(ng.count("location ~ ^/upload/") == 1 and ng.index("location ~ ^/upload/") < ng.index("location /websocket"),
              "A nginx: genau ein Upload-Block, vor location /websocket")
        check(a.zaehler("reloads", "nginx") == 1, "A nginx einmal neu geladen (nach nginx -t)")
        mr = lies(a.MRC)
        check(len(re.findall(r"^enable_api_key: False$", mr, re.M)) == 1
              and mr.index("enable_api_key") > mr.index("[authorization]"), "A moonraker.conf: enable_api_key: False genau einmal in [authorization]")
        check(a.zaehler("neustarts", "moonraker") == 1, "A Moonraker einmal neu gestartet")
        check(a.zustand("active", "klipper") != "active", "A Klipper bleibt gestoppt (ohne --starten)")
        check("Wächter:   k2-startwaechter.service: installiert" in out and "Moonraker: enable_api_key: False gesetzt" in out
              and "nginx:     Upload-Block eingefügt" in out, "A Zusammenfassung nennt Dienste/nginx/Moonraker")
        check(re.search(r"^variable_allow_move: True$", lies(os.path.join(a.C, "homing.cfg")), re.M) is not None
              and "allow_move = True" in out, "A homing.cfg wie im Paket (allow_move True) + Hinweis")
        bk1 = os.path.realpath(os.path.join(a.H, "k2-pi-backup", "letzte"))
        st1 = lies(os.path.join(bk1, "state.env"))
        check("UNIT_VORHER_k2_startwaechter=fehlt" in st1 and "NGINX_VORHER=geaendert" in st1
              and "MOONRAKER_GEAENDERT=1" in st1 and lies(os.path.join(bk1, "moonraker", "moonraker.conf")) == mr_orig,
              "A state.env + Sicherung moonraker.conf im Backup")

        fall("B Wiederholung (idempotent)")
        snap_a = a.schnappschuss()
        n_sw, n_br = a.zaehler("neustarts", "k2-startwaechter"), a.zaehler("neustarts", "k2-creality-bruecke")
        rc, out = a.lauf("install.sh")
        check(rc == 0, "B install.sh Exit 0", out[-1500:] if rc else "")
        check(lies(a.NS) == ng and lies(a.MRC) == mr, "B nginx-Site und moonraker.conf bytegleich")
        check(lies(a.NS).count("location ~ ^/upload/") == 1 and lies(a.MRC).count("enable_api_key") == 1,
              "B nginx-Block und Moonraker-Option weiterhin genau einmal")
        check(a.unit("k2-startwaechter") == sw and a.unit("k2-creality-bruecke") == br, "B Einheiten bytegleich")
        check(a.zaehler("reloads", "nginx") == 1 and a.zaehler("neustarts", "moonraker") == 1,
              "B kein weiterer nginx-Reload / Moonraker-Neustart")
        check(a.zaehler("neustarts", "k2-startwaechter") == n_sw and a.zaehler("neustarts", "k2-creality-bruecke") == n_br,
              "B laufende, unveränderte Dienste nicht neu gestartet")
        check("k2-startwaechter: unverändert, aktiviert, läuft" in out and "schon vorhanden – unverändert" in out
              and "steht schon auf False – unverändert" in out, "B Ausgabe meldet 'unverändert'")
        snap_b = a.schnappschuss()
        geaendert = sorted(p for p in set(snap_a) | set(snap_b)
                           if snap_a.get(p) != snap_b.get(p)
                           and "/k2-pi-backup/" not in p and "/klippy/extras/" not in p)
        check(not [p for p in geaendert if p.startswith(a.E) or "/k2-tools/" in p],
              "B unter /etc-Attrappe und ~/k2-tools nichts geändert", ", ".join(geaendert)[:300])

        fall("C --bewegung-gesperrt")
        rc, out = a.lauf("install.sh", "--bewegung-gesperrt")
        hc = lies(os.path.join(a.C, "homing.cfg"))
        check(rc == 0 and re.search(r"^variable_allow_move: False$", hc, re.M) is not None
              and "allow_move = False" in out, "C homing.cfg _PI_FREIGABE allow_move False")
        km = os.path.join(a.C, "k2-motor.cfg")
        schreibe(km, lies(km).replace("\nallow_write: False\n", "\nallow_write: True\n", 1))
        rc, out = a.lauf("install.sh")
        check(rc == 0 and re.search(r"^variable_allow_move: False$", lies(os.path.join(a.C, "homing.cfg")), re.M),
              "C Lauf ohne Option: Sperre bleibt (Freigaben werden übernommen)")
        check(re.search(r"^allow_write: True$", lies(km), re.M) is not None
              and "[k2_motor] allow_write <- True (bisherige Datei" in out,
              "C von Hand gesetzte Freigabe [k2_motor] allow_write bleibt bei Wiederholung")

        fall("E Rückbau --aelteste")
        rc, out = a.lauf("uninstall.sh", "--aelteste", "--ja")
        check(rc == 0, "E uninstall.sh Exit 0", out[-1500:] if rc else "")
        check(lies(a.NS) == ng_orig, "E nginx-Site bytegleich zum Original")
        check(lies(a.MRC) == mr_orig, "E moonraker.conf bytegleich zum Original")
        check(a.unit("k2-startwaechter") is None and a.unit("k2-creality-bruecke") is None, "E beide Einheiten entfernt")
        check(not os.path.exists(a.TD), "E ~/k2-tools entfernt (gab es vorher nicht)")
        check(a.zustand("active", "k2-startwaechter") == "inactive" and a.zustand("enabled", "k2-creality-bruecke") == "disabled",
              "E Dienste gestoppt/deaktiviert")
        check(a.zaehler("reloads", "nginx") == 2 and a.zaehler("neustarts", "moonraker") == 2,
              "E nginx neu geladen, Moonraker neu gestartet (Konfig zurück)")

        # ============================================================ D
        fall("D alles abgeschaltet (--ohne-*), ohne moonraker-env")
        d = Attrappe(os.path.join(wurzel, "d"), moonraker_env=False)
        vorher = d.schnappschuss()
        rc, out = d.lauf("install.sh", "--ohne-startwaechter", "--ohne-creality-bruecke", "--ohne-moonraker-anpassung")
        check(rc == 0, "D install.sh Exit 0", out[-1500:] if rc else "")
        nachher = d.schnappschuss()
        check(lies(d.NS) == NGINX_SITE and lies(d.MRC) == MOONRAKER_CONF, "D nginx-Site und moonraker.conf unverändert")
        check(not os.listdir(d.SD) and not os.path.exists(d.TD), "D keine Einheiten, kein ~/k2-tools")
        check(not [p for p in nachher if p.startswith(d.E) and vorher.get(p) != nachher[p]], "D /etc-Attrappe unverändert")
        check(d.zaehler("neustarts", "moonraker") == 0 and d.zaehler("reloads", "nginx") == 0, "D kein Neustart/Reload")
        rc, out = d.lauf("uninstall.sh", "--ja")
        check(rc == 0 and lies(d.NS) == NGINX_SITE and lies(d.MRC) == MOONRAKER_CONF, "D Rückbau: unverändert")

        # ============================================================ F
        fall("F Live-Fall: von Hand eingerichtete Dienste/Block/Option (ohne Paketmarke)")
        hand_ng = NGINX_SITE.replace("    location /websocket {", "    location ~ ^/upload/ { rewrite ^ /server/files/upload break; proxy_pass http://apiserver; }\n\n    location /websocket {")
        hand_mr = MOONRAKER_CONF.replace("[authorization]\n", "[authorization]\nenable_api_key: False\n")
        f = Attrappe(os.path.join(wurzel, "f"), nginx=hand_ng, moonraker=hand_mr)
        hand_unit = "[Unit]\nDescription=von Hand\n[Service]\nExecStart=/usr/bin/python3 %s/k2_startwaechter.py\n" % f.TD
        schreibe(os.path.join(f.SD, "k2-startwaechter.service"), hand_unit)
        schreibe(os.path.join(f.TD, "k2_startwaechter.py"), "# alte Handversion\n")
        schreibe(f.S + ".enabled.k2-startwaechter", "enabled\n")
        schreibe(f.S + ".active.k2-startwaechter", "active\n")
        rc, out = f.lauf("install.sh")
        check(rc == 0, "F install.sh Exit 0", out[-1500:] if rc else "")
        check(lies(f.NS) == hand_ng and f.zaehler("reloads", "nginx") == 0, "F Hand-Block bleibt, nichts doppelt, kein Reload")
        check(lies(f.MRC) == hand_mr and f.zaehler("neustarts", "moonraker") == 0, "F Moonraker schon aus: unverändert, kein Neustart")
        check(f.unit("k2-startwaechter").startswith("# k2-pi-paket: k2-startwaechter")
              and lies(os.path.join(f.TD, "k2_startwaechter.py")) == lies(os.path.join(HIER, "k2_startwaechter.py")),
              "F Hand-Einheit/-Programm durch Paketstand ersetzt")
        bkf = os.path.realpath(os.path.join(f.H, "k2-pi-backup", "letzte"))
        check(lies(os.path.join(bkf, "dienste", "k2-startwaechter.service")) == hand_unit
              and lies(os.path.join(bkf, "k2-tools", "k2_startwaechter.py")) == "# alte Handversion\n"
              and "UNIT_VORHER_k2_startwaechter=enabled" in lies(os.path.join(bkf, "state.env")),
              "F Handstand im Backup gesichert")
        rc, out = f.lauf("uninstall.sh", "--ja")
        check(rc == 0, "F uninstall.sh Exit 0", out[-1500:] if rc else "")
        check(f.unit("k2-startwaechter") == hand_unit and lies(os.path.join(f.TD, "k2_startwaechter.py")) == "# alte Handversion\n"
              and f.zustand("enabled", "k2-startwaechter") == "enabled", "F Rückbau: Hand-Einheit/-Programm + enabled zurück")
        check(f.unit("k2-creality-bruecke") is None and not os.path.exists(os.path.join(f.TD, "k2_creality_bruecke.py")),
              "F Rückbau: neu eingerichtete Brücke entfernt")
        check(lies(f.NS) == hand_ng, "F Rückbau: Hand-Block bleibt stehen")

        # ============================================================ G
        fall("G Abbrüche vor jeder Änderung bzw. sauberes Zurücksetzen")
        g1 = Attrappe(os.path.join(wurzel, "g1"), tornado=False)
        vorher = g1.schnappschuss()
        rc, out = g1.lauf("install.sh")
        check(rc != 0 and "FEHLER in Schritt [1]" in out and "tornado fehlt" in out and g1.schnappschuss() == vorher,
              "G1 tornado fehlt: Abbruch in Schritt 1, nichts verändert")
        g2 = Attrappe(os.path.join(wurzel, "g2"), nginx=NGINX_SITE.replace("location /websocket", "location /ws"))
        vorher = g2.schnappschuss()
        rc, out = g2.lauf("install.sh")
        check(rc != 0 and "FEHLER in Schritt [1]" in out and "kein_websocket" in out and g2.schnappschuss() == vorher,
              "G2 nginx ohne /websocket: Abbruch in Schritt 1, nichts verändert")
        g3 = Attrappe(os.path.join(wurzel, "g3"))
        schreibe(g3.S + ".nginx-t-fehler", "1\n")
        rc, out = g3.lauf("install.sh")
        check(rc != 0 and "FEHLER in Schritt [7]" in out and "nginx -t" in out, "G3 nginx -t scheitert: Abbruch in Schritt 7")
        check(lies(g3.NS) == NGINX_SITE and g3.zaehler("reloads", "nginx") == 0, "G3 Original-Site zurückgespielt, kein Reload")
        rc, out = g3.lauf("uninstall.sh", "--ja")
        check(rc == 0 and lies(g3.MRC) == MOONRAKER_CONF, "G3 Rückbau danach: moonraker.conf wieder Original")
        g4 = Attrappe(os.path.join(wurzel, "g4"), moonraker=MOONRAKER_CONF.replace("[authorization]", "[authentifizierung]"))
        rc, out = g4.lauf("install.sh", "--ohne-creality-bruecke", "--ohne-startwaechter")
        check(rc == 0 and "kein [authorization]" in out and "enable_api_key" not in lies(g4.MRC),
              "G4 ohne [authorization]: Warnung, nichts angelegt")
        g5 = Attrappe(os.path.join(wurzel, "g5"), moonraker=None)
        vorher = g5.schnappschuss()
        rc, out = g5.lauf("install.sh")
        check(rc != 0 and "FEHLER in Schritt [1]" in out and "moonraker.conf" in out and g5.schnappschuss() == vorher,
              "G5 moonraker.conf fehlt: Abbruch in Schritt 1 (Hinweis --ohne-moonraker-anpassung)")

        # ============================================================ K
        fall("K Klipper-Checkout: kein Rückschritt bei neuerem Stand")
        k = Attrappe(os.path.join(wurzel, "k"))
        c1 = k.git("rev-parse", "HEAD")
        c2 = k.neuer_commit()
        rc, out = k.lauf("install.sh", "--ohne-startwaechter", "--ohne-creality-bruecke", "--ohne-moonraker-anpassung",
                         klipper=True, env={"K2_ZIEL_COMMIT": c1})
        check(rc == 0 and k.git("rev-parse", "HEAD") == c2 and "NEUER als der geprüfte Stand" in out,
              "K1 HEAD neuer als Zielstand (enthält ihn): bleibt, Warnung")
        bkk = os.path.realpath(os.path.join(k.H, "k2-pi-backup", "letzte"))
        check("KLIPPER_GEAENDERT=0" in lies(os.path.join(bkk, "state.env")), "K1 state.env: Checkout nicht verändert")
        k.git("reset", "-q", "--hard", c1)          # master zeigt wieder auf den älteren Stand
        rc, out = k.lauf("install.sh", "--ohne-startwaechter", "--ohne-creality-bruecke", "--ohne-moonraker-anpassung",
                         klipper=True, env={"K2_ZIEL_COMMIT": c2})
        check(rc == 0 and k.git("rev-parse", "HEAD") == c2 and "umgestellt auf" in out,
              "K2 HEAD älter als Zielstand: auf Zielstand umgestellt (detached)")
        rc, out = k.lauf("uninstall.sh", "--ja")
        check(rc == 0 and k.git("rev-parse", "HEAD") == c1 and k.git("symbolic-ref", "--short", "HEAD") == "master",
              "K2 Rückbau: zurück auf Branch master / alten Stand")
        k.git("reset", "-q", "--hard", c2)          # master wieder neuer als der Zielstand
        rc, out = k.lauf("install.sh", "--ohne-startwaechter", "--ohne-creality-bruecke", "--ohne-moonraker-anpassung",
                         "--klipper-auf-zielstand", klipper=True, env={"K2_ZIEL_COMMIT": c1})
        check(rc == 0 and "umgestellt auf" in out and k.git("rev-parse", "HEAD") == c1,
              "K3 --klipper-auf-zielstand stellt auch einen neueren Stand zurück")
    print("ERGEBNIS: %s (%d/%d ok)" % ("ok" if not fehler else "FEHLER", ok, ok + len(fehler)))
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
