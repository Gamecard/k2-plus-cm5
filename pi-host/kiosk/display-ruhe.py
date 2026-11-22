#!/usr/bin/python3
"""DSI-Display nach Ruhezeit dunkel, Aufwecken per Touch.

- Keine Berührung fuer RUHE_S Sekunden -> Hintergrundlicht 0, Touch-Geraet wird exklusiv
  gegriffen (EVIOCGRAB), damit der Aufweck-Tipper NICHT in Mainsail ankommt.
- Erste Berührung im Dunkeln -> alte Helligkeit zurück; Griff wird erst gelöst, wenn der
  Finger wieder oben ist.
- Solange dunkel, werden die Prozesse des Kiosks eingefroren (cgroup v2, Datei cgroup.freeze):
  der Browser rechnet nicht weiter (spart CPU und Wärme). Beim Aufwecken zuerst auftauen,
  dann Licht an. Dienst per K2_KIOSK_EINFRIEREN (leer = nicht einfrieren).
  Eingefroren werden die Gruppen, in denen der Hauptprozess des Dienstes und seine Nachkommen
  wirklich laufen. "systemctl freeze <Dienst>" reicht nicht: Wegen PAMName=login liegen cage und
  der Browser in der Anmeldesitzung (session-N.scope) bzw. in app-Gruppen des Benutzers, die
  Gruppe des Dienstes selbst ist leer (Befund 22.11.2026: Browser lief im Dunkeln mit 20 % weiter).
- Nach dem Auftauen wird die Seite neu geladen (Taste F5 über ein eigenes virtuelles Eingabegerät):
  Während der Browser steht, schließt Moonraker dessen Verbindung, und Mainsail zeigt danach
  "Verbindung fehlgeschlagen – Erneut versuchen". Abschalten mit K2_KIOSK_NEULADEN=0.
"""
import os, select, signal, subprocess, sys, time
import evdev
from evdev import ecodes

RUHE_S = int(os.environ.get("K2_DISPLAY_RUHE_S", "600"))
BL = os.environ.get("K2_BACKLIGHT", "/sys/class/backlight/0-0045")
NAME = "ft5x06"
KIOSK = os.environ.get("K2_KIOSK_EINFRIEREN", "k2-kiosk.service")
NEULADEN = os.environ.get("K2_KIOSK_NEULADEN", "1") not in ("", "0")
taste = None  # virtuelles Eingabegerät für F5


def log(*a):
    print(*a, flush=True)


def bl_lesen():
    with open(f"{BL}/brightness") as f:
        return int(f.read().strip())


def bl_setzen(w):
    with open(f"{BL}/brightness", "w") as f:
        f.write(str(w))


def geraet_finden():
    while True:
        for p in evdev.list_devices():
            try:
                d = evdev.InputDevice(p)
            except OSError:
                continue
            if NAME in d.name:
                return d
            d.close()
        time.sleep(5)


CG = "/sys/fs/cgroup"
eingefroren = []  # cgroup-Verzeichnisse, die dieser Lauf eingefroren hat


def kiosk_baum():
    """(Hauptprozess, PIDs des Hauptprozesses und aller Nachkommen); (0, leer), wenn der Dienst nicht läuft."""
    try:
        r = subprocess.run(["systemctl", "show", "-p", "MainPID", "--value", KIOSK],
                           capture_output=True, text=True, timeout=15)
        haupt = int(r.stdout.strip() or 0)
    except (OSError, ValueError, subprocess.SubprocessError):
        return 0, set()
    if haupt <= 0:
        return 0, set()
    kinder = {}
    for p in os.listdir("/proc"):
        if not p.isdigit():
            continue
        try:
            with open(f"/proc/{p}/stat") as f:
                eltern = int(f.read().rsplit(")", 1)[1].split()[1])
        except (OSError, ValueError, IndexError):
            continue
        kinder.setdefault(eltern, []).append(int(p))
    baum, offen = set(), [haupt]
    while offen:
        p = offen.pop()
        if p not in baum:
            baum.add(p)
            offen.extend(kinder.get(p, []))
    return haupt, baum


def kiosk_gruppen():
    """cgroup-Verzeichnisse, in denen NUR Kiosk-Prozesse laufen (fremde Gruppen bleiben unberührt).
    Die Gruppe des Hauptprozesses (Anmeldesitzung des Kiosks bzw. die Dienstgruppe) gehört ganz zum
    Kiosk: Dort laufen auch Helfer, die sich vom Browser gelöst haben (chrome_crashpad_handler,
    Elternprozess 1) und deshalb nicht unter den Nachkommen auftauchen."""
    haupt, baum = kiosk_baum()
    if not haupt:
        return []
    try:
        with open(f"/proc/{haupt}/cgroup") as f:
            eigene = next((z[3:].strip() for z in f if z.startswith("0::")), "")
        if eigene.endswith((".scope", ".service")):
            with open(CG + eigene + "/cgroup.procs") as f:
                baum |= {int(x) for x in f.read().split()}
    except (OSError, ValueError):
        pass
    pfade = {}
    for p in baum:
        try:
            with open(f"/proc/{p}/cgroup") as f:
                for z in f:
                    if z.startswith("0::"):
                        pfade.setdefault(z[3:].strip(), set()).add(p)
        except OSError:
            continue
    gut = []
    for pfad in sorted(pfade):
        d = CG + pfad
        if pfad in ("", "/") or not os.path.exists(d + "/cgroup.freeze"):
            continue
        try:
            with open(d + "/cgroup.procs") as f:
                alle = {int(x) for x in f.read().split()}
        except (OSError, ValueError):
            continue
        fremd = alle - baum
        if fremd:
            log(f"Gruppe {pfad} enthält fremde Prozesse {sorted(fremd)[:5]} – nicht eingefroren")
            continue
        gut.append(d)
    return gut


def gruppe_setzen(d, wert):
    try:
        with open(d + "/cgroup.freeze", "w") as f:
            f.write(wert)
        return True
    except OSError as e:
        log(f"{d}: cgroup.freeze={wert} fehlgeschlagen:", e)
        return False


def kiosk(aktion):
    """freeze/thaw der Kiosk-Prozesse; Fehler nur melden (Dienst evtl. nicht aktiv)."""
    global eingefroren
    if not KIOSK:
        return 0
    if aktion == "freeze":
        eingefroren = [d for d in kiosk_gruppen() if gruppe_setzen(d, "1")]
        if not eingefroren:
            log("Kiosk einfrieren: keine Gruppe gefunden (Dienst läuft nicht?)")
        return len(eingefroren)
    # thaw: gemerkte Gruppen und alles, was jetzt zum Kiosk gehört (auch nach Neustart dieses Wächters)
    n = 0
    for d in sorted(set(eingefroren) | set(kiosk_gruppen())):
        if os.path.exists(d + "/cgroup.freeze") and gruppe_setzen(d, "0"):
            n += 1
    eingefroren = []
    return n


def altlast_aufheben():
    """Frühere Fassung fror den Dienst mit systemctl ein; diesen Zustand einmal aufheben,
    sonst verweigert systemd Start/Stopp ('Cannot perform operation on frozen unit')."""
    try:
        subprocess.run(["systemctl", "thaw", KIOSK], capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        pass


def taste_anlegen():
    """Virtuelle Tastatur mit der Taste F5; früh anlegen, der Compositor braucht einen Moment, bis er sie kennt."""
    global taste
    if not (KIOSK and NEULADEN):
        return
    try:
        taste = evdev.UInput({ecodes.EV_KEY: [ecodes.KEY_F5]}, name="k2-kiosk-taste")
    except (OSError, evdev.UInputError) as e:
        taste = None
        log("Virtuelle Taste nicht verfügbar (Seite wird nach dem Aufwecken nicht neu geladen):", e)


def seite_neu_laden():
    if taste is None:
        return
    try:
        time.sleep(0.3)  # Browser läuft nach dem Auftauen erst wieder an
        taste.write(ecodes.EV_KEY, ecodes.KEY_F5, 1)
        taste.syn()
        time.sleep(0.05)
        taste.write(ecodes.EV_KEY, ecodes.KEY_F5, 0)
        taste.syn()
    except OSError as e:
        log("F5 senden fehlgeschlagen:", e)


hell = None  # gemerkte Helligkeit, solange dunkel


def aufwecken():
    global hell
    if hell is not None:
        if kiosk("thaw"):
            seite_neu_laden()
        try:
            bl_setzen(hell or 255)
        except OSError as e:
            log("Helligkeit setzen fehlgeschlagen:", e)
        hell = None


def ende(*_):
    aufwecken()
    sys.exit(0)


signal.signal(signal.SIGTERM, ende)
signal.signal(signal.SIGINT, ende)


def finger_oben(ev):
    return (ev.type == ecodes.EV_KEY and ev.code == ecodes.BTN_TOUCH and ev.value == 0) or (
        ev.type == ecodes.EV_ABS and ev.code == ecodes.ABS_MT_TRACKING_ID and ev.value == -1)


def main():
    global hell
    if KIOSK:
        altlast_aufheben()
    taste_anlegen()
    kiosk("thaw")  # falls ein früherer Lauf im Dunkeln beendet wurde
    while True:
        dev = geraet_finden()
        log(f"Touch: {dev.path} ({dev.name}), Ruhezeit {RUHE_S} s")
        letzte = time.monotonic()
        try:
            while True:
                rest = RUHE_S - (time.monotonic() - letzte)
                if hell is None and rest <= 0:
                    hell = bl_lesen()
                    dev.grab()
                    bl_setzen(0)
                    n = kiosk("freeze")
                    log(f"dunkel (Helligkeit war {hell}, Kiosk eingefroren: {n} Gruppen)")
                    rest = None
                r, _, _ = select.select([dev.fd], [], [], None if hell is not None else max(rest, 0.1))
                if not r:
                    continue
                evs = list(dev.read())
                letzte = time.monotonic()
                if hell is None:
                    continue
                # Aufwecken: Licht an, Finger abwarten, erst dann Griff lösen
                aufwecken()
                log("aufgeweckt (Kiosk aufgetaut, Seite neu geladen)" if taste is not None else "aufgeweckt (Kiosk aufgetaut)")
                oben = any(finger_oben(e) for e in evs)
                ruhig_ab = time.monotonic()
                while True:
                    if oben and time.monotonic() - ruhig_ab > 0.3:
                        break
                    r, _, _ = select.select([dev.fd], [], [], 0.3)
                    if r:
                        for e in dev.read():
                            if finger_oben(e):
                                oben = True
                        ruhig_ab = time.monotonic()
                    elif time.monotonic() - ruhig_ab > 1.0:
                        break  # keine Meldungen mehr -> Finger sicher oben
                dev.ungrab()
                letzte = time.monotonic()
        except OSError as e:
            log("Touch-Gerät weg:", e)
            aufwecken()
            try:
                dev.close()
            except Exception:
                pass
            time.sleep(2)


if __name__ == "__main__":
    main()
