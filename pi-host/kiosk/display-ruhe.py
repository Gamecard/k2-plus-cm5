#!/usr/bin/python3
"""DSI-Display nach Ruhezeit dunkel, Aufwecken per Touch.

- Keine Berührung fuer RUHE_S Sekunden -> Hintergrundlicht 0, Touch-Geraet wird exklusiv
  gegriffen (EVIOCGRAB), damit der Aufweck-Tipper NICHT in Mainsail ankommt.
- Erste Berührung im Dunkeln -> alte Helligkeit zurück; Griff wird erst gelöst, wenn der
  Finger wieder oben ist.
"""
import os, select, signal, sys, time
import evdev
from evdev import ecodes

RUHE_S = int(os.environ.get("K2_DISPLAY_RUHE_S", "600"))
BL = os.environ.get("K2_BACKLIGHT", "/sys/class/backlight/0-0045")
NAME = "ft5x06"


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


hell = None  # gemerkte Helligkeit, solange dunkel


def aufwecken():
    global hell
    if hell is not None:
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
                    log(f"dunkel (Helligkeit war {hell})")
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
                log("aufgeweckt")
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
