#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Attrappen-Test für install.sh / uninstall.sh – Schwerpunkt Schritt 7
(Start-Wächter, Creality-Brücke + nginx-Upload-Block, Moonraker enable_api_key),
--bewegung-gesperrt, Rückbau und Schritt 7b (--traeger, --kiosk, --kamera,
--komplett) samt --betrieb.

Baut je Fall in einem Temp-Ordner:
  * ein Paket (aus dem Repo- oder dem Paket-Layout dieses Ordners) mit Dummy-
    Modulen, der echten config-pi/ und frischer SHA256SUMS,
  * ein Fake-HOME: git-Klipper (klippy/klippy.py), printer_data/config mit
    printer.cfg + moonraker.conf, moonraker-env/bin/python (python3 + Fake-tornado),
  * ein Fake-/etc: systemd/system/, nginx/sites-available/mainsail,
  * Fake-Befehle im PATH: sudo, systemctl, nginx, id (Benutzer "pi", UID 1000),
    dazu dpkg, apt-get und curl (Moonraker-Webcam-Liste) für Schritt 7b,
  * im Paket: die echten Dateien aus kiosk/ und eine Attrappe von
    traeger-alpha1/install-traeger.sh (protokolliert nur; das echte Skript läuft
    hier ausschließlich im Trockenlauf, Fall P),
  * leere bzw. gefüllte Ordner anstelle von /sys/class/backlight und /dev/v4l/by-id.
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
    KIOSK_SRC = os.path.join(HIER, "kiosk")
    TRAEGER_SRC = os.path.join(HIER, "traeger-alpha1")
else:
    PIHOST = os.path.dirname(HIER)                  # Repo-Layout: pi-host/install/
    KIOSK_SRC = os.path.join(PIHOST, "kiosk")
    TRAEGER_SRC = os.path.join(PIHOST, "traeger-alpha1")
KIOSK_DATEIEN = ("kiosk-start.sh", "display-ruhe.py", "k2-kiosk.service", "k2-display-ruhe.service")
KIOSK_PAKETE = "cage chromium fonts-dejavu-core python3-evdev"
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
    # Paketverwaltung: installierte Pakete stehen (eines je Zeile) in $FAKE_STATE.pakete
    "dpkg": """#!/bin/bash
[ "$1" = -s ] && grep -qx "$2" "${FAKE_STATE:?}.pakete" 2>/dev/null
""",
    "apt-get": """#!/bin/bash
S=${FAKE_STATE:?}; echo "apt-get $*" >> "$S.log"
[ -f "$S.apt-fehler" ] && exit 100
[ "$1" = install ] || exit 0
shift
for a in "$@"; do case "$a" in -*) ;; *) echo "$a" >> "$S.pakete" ;; esac; done
""",
    # Moonraker: Webcam-Liste = Zeilen in $FAKE_STATE.webcams (je Zeile ein Name)
    "curl": """#!/bin/bash
S=${FAKE_STATE:?}; echo "curl $*" >> "$S.log"
[ -f "$S.moonraker-aus" ] && exit 7
case " $* " in
  *" DELETE "*) n=$(printf '%s\\n' "$@" | sed -n 's/^name=//p'); grep -qx "$n" "$S.webcams" 2>/dev/null || { echo '{"error":{"code":404}}'; exit 0; }
               grep -vx "$n" "$S.webcams" > "$S.webcams.neu"; mv "$S.webcams.neu" "$S.webcams"
               echo '{"result":{"webcam":{"name":"'"$n"'"}}}' ;;
  *" POST "*)  n=$(printf '%s\\n' "$@" | sed -n 's/.*"name":"\\([^"]*\\)".*/\\1/p' | head -1)
               printf '%s\\n' "$@" | grep -m1 '"name"' > "$S.webcam-post"
               echo "$n" >> "$S.webcams"; echo '{"result":{"webcam":{"name":"'"$n"'"}}}' ;;
  *) l=""; while read -r n; do [ -n "$n" ] && l="$l${l:+,}{\\"name\\":\\"$n\\"}"; done < <(cat "$S.webcams" 2>/dev/null)
     echo '{"result":{"webcams":['"$l"']}}' ;;
esac
""",
}
# Attrappe von traeger-alpha1/install-traeger.sh: gleiche Meldungszeilen wie das Original
# („Sicherungen: …“, „Neustart nötig …“, „FEHLER …“), verändert nichts.
FAKE_TRAEGER = """#!/bin/bash
S=${FAKE_STATE:?}; echo "traeger $*" >> "$S.log"
if [ -f "$S.traeger-fehler" ]; then echo "FEHLER Attrappe: Paketdatei fehlt" >&2; exit 1; fi
echo "Sicherungen: /var/backups/k2-traeger/attrappe"
case " $* " in *" --trocken "*) echo "=== TROCKENLAUF: es wird NICHTS verändert ==="; echo "TROCKEN: config.txt"; exit 0 ;; esac
[ -f "$S.traeger-echt-fehler" ] && { echo "FEHLER Attrappe: dtc" >&2; exit 1; }
if [ -f "$S.traeger-fertig" ]; then echo "OK   unverändert: config.txt"
else touch "$S.traeger-fertig"; echo "OK   abgelegt: config.txt"; echo "Neustart nötig (config.txt/Overlays): sudo reboot"; fi
"""
CROWSNEST_CONF = """[crowsnest]
log_path: /home/pi/printer_data/logs/crowsnest.log

[cam 1]
mode: ustreamer
port: 8080
device: /dev/video0
resolution: 640x480
"""


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
        self.KD = os.path.join(self.H, "k2-kiosk")
        self.BL = os.path.join(tmp, "backlight")        # anstelle von /sys/class/backlight
        self.V4L = os.path.join(tmp, "v4l-by-id")       # anstelle von /dev/v4l/by-id
        self.CN = os.path.join(self.C, "crowsnest.conf")
        # Paket
        for f in PAKET_DATEIEN:
            os.makedirs(self.P, exist_ok=True)
            shutil.copy2(os.path.join(HIER, f), os.path.join(self.P, f))
        shutil.copytree(os.path.join(PIHOST, "config-pi"), os.path.join(self.P, "pi-host", "config-pi"),
                        ignore=shutil.ignore_patterns("*.md"))
        schreibe(os.path.join(self.P, "pi-host", "k2_attrappe.py"), "# Dummy-Modul\n")
        schreibe(os.path.join(self.P, "pi-host", "k2_motor_init.py"), "# Dummy k2_motor\n")
        schreibe(os.path.join(self.P, "nachbau", "attrappe.py"), "# Dummy nachbau\n")
        for f in KIOSK_DATEIEN:
            os.makedirs(os.path.join(self.P, "kiosk"), exist_ok=True)
            shutil.copy2(os.path.join(KIOSK_SRC, f), os.path.join(self.P, "kiosk", f))
        schreibe(os.path.join(self.P, "traeger-alpha1", "install-traeger.sh"), FAKE_TRAEGER, 0o755)
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
        os.makedirs(self.BL)
        os.makedirs(self.V4L)
        schreibe(self.S + ".enabled.getty@tty1", "enabled\n")
        self.env = {"HOME": self.H, "PATH": self.B + ":/usr/bin:/bin", "LANG": "C.UTF-8",
                    "FAKE_STATE": self.S, "SYSTEMD_DIR": self.SD, "NGINX_SITE": self.NS,
                    "BOOTSTART_UNIT": os.path.join(self.SD, "k2-bootstart.service"),
                    "BACKLIGHT_DIR": self.BL, "V4L_BYID_DIR": self.V4L, "MOONRAKER_VERSUCHE": "1"}

    def kamera(self, *namen, crowsnest=True):
        """Kameras unter der by-id-Attrappe anlegen (+ crowsnest.conf und -Dienst)."""
        for n in namen:
            schreibe(os.path.join(self.V4L, n), "")
        if crowsnest:
            schreibe(self.CN, CROWSNEST_CONF)
            schreibe(os.path.join(self.SD, "crowsnest.service"), "[Service]\nExecStart=/bin/true\n")

    def log(self):
        try:
            return lies(self.S + ".log").splitlines()
        except OSError:
            return []

    def opt(self, datei, abschnitt, schluessel):
        """Wert der ersten aktiven Zeile (wie k2cfg.py sie liest), sonst None."""
        return K2CFG.options(lies(os.path.join(self.C, datei)), ((abschnitt, schluessel),)).get((abschnitt, schluessel))

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


def _k2cfg():
    import importlib.util
    spec = importlib.util.spec_from_file_location("k2cfg", os.path.join(HIER, "k2cfg.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


K2CFG = _k2cfg()
BETRIEB_SCHALTER = (("homing.cfg", "gcode_macro _PI_FREIGABE", "variable_allow_move"),
                    ("k2-motor.cfg", "k2_motor", "allow_write"), ("k2-motor.cfg", "k2_motor", "auto_init"),
                    ("k2-module.cfg", "k2_box", "allow_write"), ("k2-module.cfg", "k2_box", "allow_move"),
                    ("printer-k2.cfg", "k2_prtouch", "allow_move"))


def ohne_backup(a, vorher, nachher):
    """Geänderte Pfade zwischen zwei Schnappschüssen, ohne ~/k2-pi-backup und klippy/extras."""
    return sorted(p for p in set(vorher) | set(nachher)
                  if vorher.get(p) != nachher.get(p)
                  and "/k2-pi-backup/" not in p and not p.endswith("/k2-pi-backup")
                  and "/klippy/extras/" not in p)


def faelle_7b(wurzel):
    """Schritt 7b: --komplett (= --traeger --kiosk --kamera --starten), --betrieb, Rückbau."""
    CRE = "usb-CREALITY_Kamera_TEST-video-index0"
    fall("L --komplett: Trägerplatine, Anzeige, Kamera in einem Aufruf")
    t = Attrappe(os.path.join(wurzel, "l"))
    t.kamera("usb-Fremd_Kamera_TEST-video-index0", CRE, "usb-CREALITY_Kamera_TEST-video-index1")
    rc, out = t.lauf("install.sh", "--komplett")
    check(rc == 0, "L install.sh --komplett Exit 0", out[-2500:] if rc else "")
    lg = t.log()
    tr = [z for z in lg if z.startswith("traeger ")]
    check(tr == ["traeger --trocken --benutzer pi", "traeger --benutzer pi"],
          "L install-traeger.sh: erst Trockenlauf, dann echter Lauf (je einmal)", str(tr))
    check(t.opt("printer-k2.cfg", "mcu", "serial") == "/dev/ttyAMA4"
          and t.opt("printer-k2.cfg", "mcu nozzle_mcu", "serial") == "/dev/ttyAMA2"
          and t.opt("k2-module.cfg", "k2_485", "serial") == "/dev/ttyAMA0",
          "L Anschlüsse: [mcu] ttyAMA4, [mcu nozzle_mcu] ttyAMA2, [k2_485] ttyAMA0")
    km = lies(os.path.join(t.C, "k2-module.cfg"))
    check(t.opt("k2-module.cfg", "k2_485", "rs485_de") == "kernel"
          and len(re.findall(r"^rs485_de\s*:", km, re.M)) == 1
          and "serial: /dev/ttyAMA0\nrs485_de: kernel\n" in km, "L [k2_485] rs485_de: kernel genau einmal, hinter serial")
    apt = [z for z in lg if z.startswith("apt-get install")]
    check(apt == ["apt-get install -y --no-install-recommends " + KIOSK_PAKETE], "L apt-get: genau die vier fehlenden Pakete", str(apt))
    check(all(open(os.path.join(t.KD, f), "rb").read() == open(os.path.join(KIOSK_SRC, f), "rb").read()
              and os.access(os.path.join(t.KD, f), os.X_OK) for f in ("kiosk-start.sh", "display-ruhe.py")),
          "L ~/k2-kiosk: kiosk-start.sh und display-ruhe.py bytegleich und ausführbar")
    uk, ur = t.unit("k2-kiosk") or "", t.unit("k2-display-ruhe") or ""
    check("ExecStart=/usr/bin/cage -d -s -- %s/kiosk-start.sh" % t.KD in uk and "%s/display-ruhe.py" % t.KD in ur
          and "/home/pi/" not in uk + ur and re.search(r"^User=pi$", uk, re.M) is not None,
          "L Einheiten: Pfade auf ~/k2-kiosk des Benutzers umgeschrieben, kein /home/pi")
    check(t.zustand("enabled", "k2-kiosk") == "enabled" and t.zustand("enabled", "k2-display-ruhe") == "enabled"
          and t.zustand("enabled", "getty@tty1") == "disabled", "L k2-kiosk + k2-display-ruhe aktiviert, getty@tty1 abgeschaltet")
    check(t.zaehler("neustarts", "k2-kiosk") == 0 and t.zaehler("neustarts", "k2-display-ruhe") == 0
          and "startet nach dem NEUSTART" in out, "L ohne DSI-Hintergrundlicht: Anzeige nur aktiviert, nicht gestartet")
    check(t.opt("crowsnest.conf", "cam 1", "device") == os.path.join(t.V4L, CRE) and t.zaehler("neustarts", "crowsnest") == 1,
          "L crowsnest.conf [cam 1] device = CREALITY-Kamera (…-video-index0), crowsnest neu gestartet")
    post = [z for z in lg if z.startswith("curl ") and " POST " in z]
    wp = lies(t.S + ".webcam-post") if os.path.exists(t.S + ".webcam-post") else ""
    check(len(post) == 1 and "/server/webcams/item" in post[0] and lies(t.S + ".webcams").split() == ["Bauraum"]
          and '"service":"mjpegstreamer-adaptive"' in wp and '"stream_url":"/webcam/?action=stream"' in wp
          and '"snapshot_url":"/webcam/?action=snapshot"' in wp, "L Moonraker: Webcam Bauraum einmal angelegt (Dienst + Adressen)", wp[:200])
    check(t.zaehler("neustarts", "klipper") == 0 and t.zustand("enabled", "klipper") == "enabled"
          and "*** NEUSTART NÖTIG:  sudo reboot  ***" in out and "aktiviert, startet nach dem NEUSTART" in out,
          "L --starten bei nötigem Neustart: Klipper nur aktiviert, Hinweis NEUSTART NÖTIG")
    check(all(re.search(r"^  %s: +\S" % w, out, re.M) for w in ("Träger", "Anzeige", "Kamera", "Freigaben"))
          and "NICHT von install.sh abgedeckt" in out and "PSU_MAX_CURRENT" in out,
          "L Zusammenfassung nennt Träger/Anzeige/Kamera/Freigaben und was Handarbeit bleibt")
    check(t.opt("k2-motor.cfg", "k2_motor", "allow_write") == "False" and t.opt("printer-k2.cfg", "k2_prtouch", "allow_move") == "False",
          "L ohne --betrieb bleiben die Freigaben auf Paketstand (gesperrt)")
    bk = os.path.realpath(os.path.join(t.H, "k2-pi-backup", "letzte"))
    st = lies(os.path.join(bk, "state.env"))
    check(all(x in st for x in ("TRAEGER_GELAUFEN=1", "TRAEGER_SICHERUNG=/var/backups/k2-traeger/attrappe", "KIOSK_INSTALLIERT=1",
                                "KIOSK_UNIT_VORHER_k2_kiosk=fehlt", "GETTY_VORHER=enabled", "KAMERA_WEBCAM_ANGELEGT=Bauraum"))
          and os.path.isfile(os.path.join(bk, "traeger", "trockenlauf.txt")) and os.path.isfile(os.path.join(bk, "traeger", "install-traeger.txt")),
          "L state.env + Protokolle des Träger-Skripts im Backup")

    fall("M --komplett wiederholt (Träger eingerichtet; erst ohne, dann mit Display)")
    rc, out = t.lauf("install.sh", "--komplett")
    check(rc == 0 and t.zaehler("neustarts", "klipper") == 1 and t.zaehler("neustarts", "k2-kiosk") == 0
          and "Anzeige: Das Bild erscheint erst nach dem Neustart" in out and "Trägerplatine: Die Anschlüsse" not in out,
          "M Träger schon aktuell, noch kein Display: Klipper startet, Anzeige wartet auf den Neustart",
          out[-2500:] if rc else "")
    schreibe(os.path.join(t.BL, "10-0045"), "")
    rc, out = t.lauf("install.sh", "--komplett")
    check(rc == 0, "M Lauf mit Display Exit 0", out[-2500:] if rc else "")
    check(t.zaehler("neustarts", "k2-kiosk") == 1 and t.zaehler("neustarts", "k2-display-ruhe") == 1
          and t.zaehler("neustarts", "klipper") == 2 and "NEUSTART NÖTIG" not in out,
          "M Display erkannt: Anzeige und Klipper gestartet, kein Neustart-Hinweis mehr")
    snap1 = t.schnappschuss()
    n_log = len(t.log())
    rc, out = t.lauf("install.sh", "--komplett")
    check(rc == 0, "M weiterer Lauf Exit 0", out[-2500:] if rc else "")
    diff = ohne_backup(t, snap1, t.schnappschuss())
    check(not diff, "M weiterer Lauf ändert nichts (Config, Einheiten, ~/k2-kiosk bytegleich)", ", ".join(diff)[:300])
    neu = t.log()[n_log:]
    check(not [z for z in neu if z.startswith("apt-get")] and not [z for z in neu if " POST " in z]
          and t.zaehler("neustarts", "crowsnest") == 1 and t.zaehler("neustarts", "k2-kiosk") == 1
          and lies(t.S + ".webcams").split() == ["Bauraum"],
          "M kein weiteres apt-get, kein zweiter Webcam-Eintrag, crowsnest/Anzeige nicht neu gestartet")
    km = lies(os.path.join(t.C, "k2-module.cfg"))
    check(len(re.findall(r"^rs485_de\s*:", km, re.M)) == 1 and t.opt("k2-module.cfg", "k2_485", "rs485_de") == "kernel"
          and "schon aktuell – nichts geändert" in out and "unverändert, aktiviert, läuft" in out
          and "Webcam(s) eingetragen – unverändert" in out, "M rs485_de weiterhin genau einmal; Ausgabe meldet 'unverändert'")
    rc, out = t.lauf("install.sh")
    km = lies(os.path.join(t.C, "k2-module.cfg"))
    check(rc == 0 and len(re.findall(r"^rs485_de\s*:", km, re.M)) == 1 and t.opt("k2-module.cfg", "k2_485", "serial") == "/dev/ttyAMA0"
          and t.opt("printer-k2.cfg", "mcu", "serial") == "/dev/ttyAMA4" and t.zustand("enabled", "k2-kiosk") == "enabled",
          "M Lauf OHNE Optionen: Anschlüsse, rs485_de: kernel und Anzeige bleiben")

    fall("N Rückbau --aelteste nach --komplett")
    rc, out = t.lauf("uninstall.sh", "--aelteste", "--ja")
    check(rc == 0, "N uninstall.sh Exit 0", out[-2500:] if rc else "")
    check(t.unit("k2-kiosk") is None and t.unit("k2-display-ruhe") is None and not os.path.exists(t.KD)
          and t.zustand("enabled", "k2-kiosk") == "disabled" and t.zustand("enabled", "getty@tty1") == "enabled",
          "N Anzeige: Einheiten und ~/k2-kiosk entfernt, getty@tty1 wieder aktiviert")
    check(lies(t.CN) == CROWSNEST_CONF and lies(t.S + ".webcams").split() == []
          and any(" DELETE " in z for z in t.log()), "N crowsnest.conf wie vorher, Webcam-Eintrag entfernt")
    check("wird NICHT zurückgebaut" in out and "/var/backups/k2-traeger/attrappe" in out,
          "N Trägerplatine: kein Rückbau, Hinweis auf die Sicherung")
    check(not os.path.exists(os.path.join(t.C, "k2-module.cfg")) and lies(os.path.join(t.C, "printer.cfg")) == "[printer]\nkinematics: none\n",
          "N Config-Ordner wie vor der ersten Installation")

    fall("O --betrieb / Aufruf-Fehler")
    b = Attrappe(os.path.join(wurzel, "o"))
    vorher = b.schnappschuss()
    rc, out = b.lauf("install.sh", "--betrieb", "--bewegung-gesperrt")
    check(rc == 2 and "schließen sich aus" in out and b.schnappschuss() == vorher,
          "O --betrieb mit --bewegung-gesperrt: Exit 2, nichts verändert", "rc=%d" % rc)
    rc, out = b.lauf("install.sh", "--betrieb")
    werte = [b.opt(*x) for x in BETRIEB_SCHALTER]
    check(rc == 0 and werte == ["True"] * 6, "O --betrieb setzt alle sechs Freigaben auf True", str(werte) + out[-800:] * (rc != 0))
    check("ACHTUNG --betrieb" in out and "SCHNELLSTART-K2-PLUS.md Abschnitt 5" in out and "BETRIEB=1" in
          lies(os.path.join(os.path.realpath(os.path.join(b.H, "k2-pi-backup", "letzte")), "state.env")),
          "O Abschluss warnt deutlich (nur für einen Drucker, der schon in Betrieb war)")
    check(not [z for z in b.log() if z.startswith(("traeger", "apt-get", "curl"))] and b.unit("k2-kiosk") is None,
          "O --betrieb allein fasst Träger, Anzeige und Kamera nicht an")
    rc, out = b.lauf("install.sh")
    check(rc == 0 and [b.opt(*x) for x in BETRIEB_SCHALTER] == ["True"] * 6,
          "O Lauf ohne Option: die sechs Freigaben bleiben (Erhaltungslogik)")
    rc, out = b.lauf("install.sh", "--bewegung-gesperrt")
    check(rc == 0 and b.opt("homing.cfg", "gcode_macro _PI_FREIGABE", "variable_allow_move") == "False"
          and b.opt("k2-motor.cfg", "k2_motor", "allow_write") == "True", "O --bewegung-gesperrt danach sperrt die Bewegung wieder")

    fall("P Kamera/Träger: Sonderfälle")
    c = Attrappe(os.path.join(wurzel, "p1"))
    rc, out = c.lauf("install.sh", "--kamera")
    check(rc == 0 and "keine Kamera erkannt" in out and "WARN" in out and not [z for z in c.log() if z.startswith("curl")],
          "P1 --kamera ohne Kamera und ohne crowsnest: nur Warnung, Exit 0", out[-800:] if rc else "")
    c2 = Attrappe(os.path.join(wurzel, "p2"))
    c2.kamera("usb-Fremd_Kamera_TEST-video-index0", "usb-Andere_Kamera_TEST-video-index0")
    schreibe(c2.S + ".webcams", "Vorhanden\n")
    rc, out = c2.lauf("install.sh", "--kamera")
    check(rc == 0 and c2.opt("crowsnest.conf", "cam 1", "device") == os.path.join(c2.V4L, "usb-Andere_Kamera_TEST-video-index0")
          and not [z for z in c2.log() if " POST " in z] and lies(c2.S + ".webcams").split() == ["Vorhanden"],
          "P2 ohne CREALITY-Kamera: erste (sortiert); vorhandener Webcam-Eintrag bleibt, kein zweiter")
    c3 = Attrappe(os.path.join(wurzel, "p3"))
    c3.kamera(CRE)
    schreibe(c3.S + ".moonraker-aus", "")
    rc, out = c3.lauf("install.sh", "--kamera")
    check(rc == 0 and "nicht erreichbar" in out and c3.opt("crowsnest.conf", "cam 1", "device") == os.path.join(c3.V4L, CRE),
          "P3 Moonraker nicht erreichbar: Gerätename eingetragen, Webcam nur Warnung")
    d = Attrappe(os.path.join(wurzel, "p4"))
    schreibe(d.S + ".traeger-fehler", "")
    vorher = d.schnappschuss()
    rc, out = d.lauf("install.sh", "--traeger")
    check(rc != 0 and "FEHLER in Schritt [1]" in out and "Trockenlauf" in out and d.schnappschuss() == vorher
          and [z for z in d.log() if z.startswith("traeger")] == ["traeger --trocken --benutzer pi"],
          "P4 Trockenlauf des Träger-Skripts meldet FEHLER: Abbruch in Schritt 1, nichts verändert")
    e = Attrappe(os.path.join(wurzel, "p5"))
    rc, out = e.lauf("install.sh", "--traeger", "--rs485-serial", "/dev/serial/by-id/usb-TEST_RS485-if00-port0",
                     "--main-serial", "/dev/serial/by-id/usb-TEST_Main-if00")
    check(rc == 0 and e.opt("k2-module.cfg", "k2_485", "serial") == "/dev/serial/by-id/usb-TEST_RS485-if00-port0"
          and e.opt("k2-module.cfg", "k2_485", "rs485_de") is None and e.opt("printer-k2.cfg", "mcu", "serial") == "/dev/serial/by-id/usb-TEST_Main-if00"
          and e.opt("printer-k2.cfg", "mcu nozzle_mcu", "serial") == "/dev/ttyAMA2",
          "P5 --traeger mit eigenen Anschlüssen: Angaben gewinnen, rs485_de nur beim Träger-Anschluss", out[-800:] if rc else "")
    g = Attrappe(os.path.join(wurzel, "p6"))
    schreibe(g.S + ".pakete", "\n".join(KIOSK_PAKETE.split()[:3]) + "\n")
    schreibe(os.path.join(g.BL, "10-0045"), "")
    rc, out = g.lauf("install.sh", "--kiosk")
    check(rc == 0 and [z for z in g.log() if z.startswith("apt-get install")] == ["apt-get install -y --no-install-recommends python3-evdev"]
          and g.zaehler("neustarts", "k2-kiosk") == 1 and g.zustand("active", "k2-display-ruhe") == "active",
          "P6 --kiosk: nur das fehlende Paket; mit Hintergrundlicht sofort gestartet", out[-800:] if rc else "")
    # Rückbau, wenn es die Anzeige schon VOR dem gewählten Lauf gab: Stand davor kommt zurück
    alt_start = "#!/bin/sh\n# ältere Fassung (Attrappe)\n"
    schreibe(os.path.join(g.KD, "kiosk-start.sh"), alt_start, 0o755)
    rc, out = g.lauf("install.sh", "--kiosk")
    neu_ok = open(os.path.join(g.KD, "kiosk-start.sh"), "rb").read() == open(os.path.join(KIOSK_SRC, "kiosk-start.sh"), "rb").read()
    rc2, out2 = g.lauf("uninstall.sh", "--ja")
    check(rc == 0 and neu_ok and rc2 == 0 and lies(os.path.join(g.KD, "kiosk-start.sh")) == alt_start
          and g.unit("k2-kiosk") is not None and g.zustand("enabled", "k2-kiosk") == "enabled"
          and g.zustand("enabled", "getty@tty1") == "disabled" and os.path.isfile(os.path.join(g.KD, "display-ruhe.py")),
          "P6 Rückbau des letzten Laufs: Anzeige bestand schon vorher -> bleibt, ältere Datei kommt zurück",
          (out + out2)[-800:] if rc or rc2 else "")
    # Paketinhalt und Schnittstelle zum echten Träger-Skript (nur Trockenlauf, verändert nichts)
    echt = os.path.join(TRAEGER_SRC, "install-traeger.sh")
    fehlt = [f for f in ("install-traeger.sh", "config-txt-traeger.txt", "k2_stromausfall.py", "k2_readonly.sh",
                         "README-traeger-alpha1.md", "can0/80-k2-can0.network") if not os.path.isfile(os.path.join(TRAEGER_SRC, f))]
    fehlt += [f for f in KIOSK_DATEIEN + ("LIESMICH.md",) if not os.path.isfile(os.path.join(KIOSK_SRC, f))]
    check(not fehlt, "P7 traeger-alpha1/ und kiosk/ mit den nötigen Dateien vorhanden", str(fehlt))
    if os.path.isfile(os.path.join(HIER, "SHA256SUMS")) or os.path.isdir(os.path.join(HIER, "pi-host")):
        muell = [os.path.join(w, f) for q in (TRAEGER_SRC, KIOSK_SRC) for w, _d, fs in os.walk(q)
                 for f in fs if ".bak" in f or "__pycache__" in w]
        check(not muell, "P7 Paket: keine Sicherungskopien und kein __pycache__ in traeger-alpha1/ und kiosk/", str(muell[:5]))
    if os.path.isfile(echt):
        r = subprocess.run(["bash", echt, "--trocken", "--benutzer", "pi"], capture_output=True, text=True, timeout=120,
                           cwd=tempfile.gettempdir())
        aus = r.stdout + r.stderr
        check(r.returncode == 0 and not re.search(r"^FEHLER", aus, re.M) and re.search(r"^Sicherungen: /", aus, re.M) is not None
              and re.search(r"^Neustart nötig", aus, re.M) is not None,
              "P8 echtes install-traeger.sh --trocken --benutzer: Exit 0, Zeilen 'Sicherungen:'/'Neustart nötig' wie von install.sh gelesen",
              aus[-400:])
    uk = lies(os.path.join(KIOSK_SRC, "k2-kiosk.service"))
    check(re.search(r"^User=pi$", uk, re.M) is not None and "/home/pi/k2-kiosk/kiosk-start.sh" in uk
          and "/home/pi/k2-kiosk/display-ruhe.py" in lies(os.path.join(KIOSK_SRC, "k2-display-ruhe.service")),
          "P9 Einheiten in kiosk/ haben die Stellen, die install.sh --kiosk umschreibt (User=pi, /home/pi/k2-kiosk)")


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
        # ============================================================ L–P
        faelle_7b(wurzel)
    print("ERGEBNIS: %s (%d/%d ok)" % ("ok" if not fehler else "FEHLER", ok, ok + len(fehler)))
    return 1 if fehler else 0


if __name__ == "__main__":
    sys.exit(main())
