#!/usr/bin/env bash
# =============================================================================
#  K2-CM5-Träger Alpha 1 – kompletter Neubau (23.10.2026)
#  Generator -> Schaltplan -> ERC -> Netzliste (+ Netzlisten- und CM5-Pin-Wächter) -> Platine (feste Paare,
#  CM5-Fanout, Leistungsstreifen) -> Freerouting -> GND-/Leistungsflächen (4 Lagen) -> DRC (inkl. Parität)
#  -> Lage-, Höhen-, Paar-, Siebdruck-Wächter -> Gerber/Bohrdaten/BOM/JLC-Bestückung/PDF/3D-Bilder
#
#  Aufruf:   ./bauen.sh
#  Optional: FREEROUTING_JAR=/pfad/freerouting-2.5.0.jar ./bauen.sh
#            K2_BAU_TMP=/pfad      (Ablage für KiCad-/Java-Einstellungen während des Baus)
#            K2_VARIANTEN=1        (Anzahl Freerouting-Läufe mit gemischter Netzreihenfolge)
#            K2_ZWISCHEN_BEHALTEN=1 (Zwischenstände nicht löschen)
#            K2_SEED_START=14      (erste Netzreihenfolge; 14 = Layout Alpha 1 nach Weg A 24.10.2026, vollständig geroutet;
#                                   vorher 136 (Umbau 24.10.) bzw. 21.
#                                   Suche nach Alternativen z. B. K2_SEED_START=0 K2_VARIANTEN=48 K2_PARALLEL=12)
#            K2_PARALLEL=6         (gleichzeitige Freerouting-Läufe)
#  Benötigt: KiCad 10 (kicad-cli + Python-Modul pcbnew), Java >= 21, python3, curl
# =============================================================================
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
P=k2-cm5-traeger
G=generator/k2_traeger.py
DIR="$PWD"
Z="$DIR/zwischen"
A="$DIR/ausgabe"
FR_VERSION=2.5.0
FR_SHA256=f6f51bb02245e8e717f9359bd260cc9c5c0b1bc0acc8b7cb2cd5b8ffeb5de3c7

mkdir -p "$Z" "$A"
UMG="${K2_BAU_TMP:-$Z/umgebung}"
mkdir -p "$UMG"/{kicad-config,kicad-cache,xdg-cache,home,xdg-config,xdg-state}
export KICAD_CONFIG_HOME="$UMG/kicad-config" KICAD_CACHE_HOME="$UMG/kicad-cache" XDG_CACHE_HOME="$UMG/xdg-cache"
# 3D-Bibliothek für Renders/Höhen-Info (frische KiCad-Konfiguration kennt sonst keinen Modellpfad)
export KICAD10_3DMODEL_DIR="${KICAD10_3DMODEL_DIR:-/usr/share/kicad/3dmodels}"

JAR="${FREEROUTING_JAR:-}"
if [[ -z "$JAR" ]]; then
  JAR="$DIR/werkzeuge/freerouting-$FR_VERSION.jar"
  if [[ ! -f "$JAR" ]]; then
    echo "== Freerouting $FR_VERSION wird geladen (github.com/freerouting/freerouting)"
    mkdir -p "$DIR/werkzeuge"
    curl -fsSL -o "$JAR.part" "https://github.com/freerouting/freerouting/releases/download/v$FR_VERSION/freerouting-$FR_VERSION.jar"
    echo "$FR_SHA256  $JAR.part" | sha256sum -c - >/dev/null
    mv "$JAR.part" "$JAR"
  fi
fi

schritt() { echo; echo "== $*"; }
# Freerouting ist je Netzreihenfolge wiederholbar (1 Thread): Reihenfolge 14 ergibt das freigegebene Layout (Weg A, 24.10.2026)
export K2_SEED_START="${K2_SEED_START:-14}"

schritt "1/10 Schaltplan, Projektdateien, Symbole und Footprints erzeugen"
python3 $G schaltplan

schritt "2/10 ERC"
kicad-cli sch erc --format json --severity-all -o "$Z/erc.json" "$DIR/$P.kicad_sch" >/dev/null
kicad-cli sch erc --format report --severity-all -o "$A/erc-bericht.txt" "$DIR/$P.kicad_sch" >/dev/null
python3 $G bericht-erc "$Z/erc.json"

schritt "3/10 Netzliste exportieren, Netzlisten-Wächter und CM5-Pin-Wächter"
kicad-cli sch export netlist -o "$Z/netzliste.net" "$DIR/$P.kicad_sch" >/dev/null
python3 $G pruefe-netzliste "$Z/netzliste.net"
cp "$Z/spiegelfall.txt" "$A/spiegelfall.txt"
python3 $G pruefe-cm5 "$Z/netzliste.net"

schritt "4/10 Platine: Platzierung, feste Paare (USB/Ethernet), CM5-Fanout, Leistungsstreifen, Specctra-DSN"
python3 $G platine "$Z/netzliste.net"

schritt "5/10 Autorouting mit Freerouting (${K2_VARIANTEN:-1} Netzreihenfolge(n) ab Nr. $K2_SEED_START)"
rm -f "$Z/$P.ses"
python3 $G freerouting "$JAR" "$Z/$P.dsn" "$Z/$P.ses" "$UMG" "${K2_VARIANTEN:-1}"
[[ -s "$Z/$P.ses" ]] || { echo "Freerouting hat keine SES-Datei erzeugt – siehe $Z/freerouting.log"; exit 1; }

schritt "6/10 Routing einlesen, GND-Flächen (4 Lagen), Leistungsflächen, Stitching-Vias (beste Varianten, bis der DRC sauber ist)"
cp "$DIR/$P.kicad_pcb" "$Z/platziert.kicad_pcb"
BEST_N=999999; BEST_SEED=""
for SEED in $(head -n "${K2_KANDIDATEN:-4}" "$Z/kandidaten.txt"); do
  cp "$Z/platziert.kicad_pcb" "$DIR/$P.kicad_pcb"
  cp "$Z/variante$SEED.ses" "$Z/$P.ses"
  cp "$Z/variante$SEED.log" "$Z/freerouting.log"
  echo "-- Variante $SEED"
  python3 $G fertigstellen "$Z/$P.ses"
  kicad-cli pcb drc --schematic-parity --refill-zones --format json --severity-all -o "$Z/drc.json" "$DIR/$P.kicad_pcb" >/dev/null
  N=$(python3 $G drc-fehler "$Z/drc.json")
  echo "   DRC-Fehler: $N"
  if (( N < BEST_N )); then BEST_N=$N; BEST_SEED=$SEED; fi
  [[ "$N" == "0" ]] && break
done
if [[ "$BEST_N" != "0" && -n "$BEST_SEED" && "$SEED" != "$BEST_SEED" ]]; then
  echo "-- keine Variante fehlerfrei; beste Variante $BEST_SEED ($BEST_N DRC-Fehler) wird übernommen"
  cp "$Z/platziert.kicad_pcb" "$DIR/$P.kicad_pcb"
  cp "$Z/variante$BEST_SEED.ses" "$Z/$P.ses"
  cp "$Z/variante$BEST_SEED.log" "$Z/freerouting.log"
  python3 $G fertigstellen "$Z/$P.ses"
fi

schritt "7/10 DRC (inkl. Schaltplan-Parität) und Wächter (Lage, Höhe, Paare, Siebdruck)"
kicad-cli pcb drc --schematic-parity --refill-zones --format json --severity-all -o "$Z/drc.json" "$DIR/$P.kicad_pcb" >/dev/null
kicad-cli pcb drc --schematic-parity --refill-zones --format report --severity-all -o "$A/drc-bericht.txt" "$DIR/$P.kicad_pcb" >/dev/null
python3 $G bericht-drc "$Z/drc.json" "$A/drc-zusammenfassung.txt"
python3 $G pruefe-lage "$A/lage.txt"
python3 $G pruefe-hoehe "$A/hoehen.txt"
python3 $G pruefe-paare "$A/paare.txt"
python3 $G pruefe-siebdruck "$A/siebdruck.txt"

schritt "8/10 Fertigungsdaten (JLCPCB, 4 Lagen): Gerber + Bohrdaten"
rm -rf "$A/gerber" && mkdir -p "$A/gerber"
kicad-cli pcb export gerbers -o "$A/gerber/" \
  --layers F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.Silkscreen,B.Silkscreen,F.Mask,B.Mask,Edge.Cuts \
  --no-x2 --no-netlist --subtract-soldermask --check-zones "$DIR/$P.kicad_pcb" >/dev/null
kicad-cli pcb export drill -o "$A/gerber/" --format excellon --drill-origin absolute \
  --excellon-units mm --excellon-zeros-format decimal --excellon-oval-format alternate \
  --excellon-separate-th "$DIR/$P.kicad_pcb" >/dev/null
rm -f "$A/${P}_gerber_jlcpcb.zip"
python3 - "$A/gerber" "$A/${P}_gerber_jlcpcb.zip" <<'PY'
import sys, zipfile, pathlib
src, dst = pathlib.Path(sys.argv[1]), sys.argv[2]
with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
    for f in sorted(src.iterdir()):
        z.write(f, f.name)
print("Gerber-ZIP:", dst, "(", len(list(src.iterdir())), "Dateien )")
PY

schritt "9/10 Stückliste, JLCPCB-Bestückung (beide Seiten), Schaltplan-PDF, Lagen-PDF"
kicad-cli sch export bom -o "$A/stueckliste.csv" \
  --fields 'Reference,Value,Bemerkung,Bestueckung,LCSC,MPN,Manufacturer,Footprint,${QUANTITY},${DNP}' \
  --labels 'Referenzen,Wert,Bemerkung,Bestückung,LCSC,MPN,Hersteller,Footprint,Anzahl,DNP' \
  --group-by 'Value,Bemerkung,Bestueckung,Footprint,${DNP}' "$DIR/$P.kicad_sch" >/dev/null
kicad-cli pcb export pos -o "$Z/positionen.csv" --format csv --units mm --side both "$DIR/$P.kicad_pcb" >/dev/null
python3 $G jlc-dateien "$Z/positionen.csv" "$A"
kicad-cli sch export pdf -o "$A/schaltplan.pdf" "$DIR/$P.kicad_sch" >/dev/null
kicad-cli pcb export pdf -o "$A/platine_lagen.pdf" --mode-multipage \
  --layers F.Cu,In1.Cu,In2.Cu,B.Cu,F.Silkscreen,B.Silkscreen --common-layers Edge.Cuts \
  --include-border-title "$DIR/$P.kicad_pcb" >/dev/null 2>&1 || true

schritt "10/10 3D-Bilder (oben, unten, schräg)"
kicad-cli pcb render --side top --quality high --width 2000 --height 1300 --background opaque \
  -o "$A/render_oben.png" "$DIR/$P.kicad_pcb" >/dev/null 2>&1
kicad-cli pcb render --side bottom --quality high --width 2000 --height 1300 --background opaque \
  -o "$A/render_unten.png" "$DIR/$P.kicad_pcb" >/dev/null 2>&1
kicad-cli pcb render --side top --quality high --width 2000 --height 1400 --background opaque \
  --rotate '-40,0,20' --perspective -o "$A/render_schraeg.png" "$DIR/$P.kicad_pcb" >/dev/null 2>&1 || true

# Zwischenstände enthalten absolute Pfade dieses Rechners -> standardmäßig löschen
if [[ "${K2_ZWISCHEN_BEHALTEN:-0}" != "1" ]]; then
  rm -rf "$Z"
fi

echo
echo "== FERTIG. Ausgaben in ausgabe/:"
ls -1 "$A"
