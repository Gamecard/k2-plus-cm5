#!/usr/bin/env bash
# =============================================================================
#  K2-CM5-Träger: echte 3D-Modelle der Fremdteile von LCSC/EasyEDA laden (24.10.2026)
#  NUR FÜR DIE LOKALE DARSTELLUNG (Renders). Die Lizenz der EasyEDA/LCSC-Modelle erlaubt keine gesicherte
#  Weitergabe -> k2_traeger.3dshapes/lcsc/ steht in .gitignore und gehört nicht ins Repo.
#  Fehlen die Dateien, verwendet der Generator die schlichten Quader (Lötflächen und Prüfungen unverändert).
#
#  Aufruf:  EASYEDA2KICAD=/pfad/zum/venv/bin/easyeda2kicad ./lcsc_modelle_laden.sh
#           (easyeda2kicad in ein eigenes venv installieren: python3 -m venv v && v/bin/pip install easyeda2kicad)
#  Danach:  ./bauen.sh  (Footprints/Renders übernehmen die Modelle)
# =============================================================================
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"
E="${EASYEDA2KICAD:-easyeda2kicad}"
ZIEL="k2_traeger.3dshapes/lcsc"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
mkdir -p "$ZIEL"
# LCSC-Nummer  Teil
TEILE="C5187468:J10_USB-C_TYPE-C-16P-LTH6.8 C18584:T1_H1102NLT C187520:F1_Littelfuse_0157003.DR
C431092:J7_XT30PW-M C485916:U8_TPS2121_RUX C6890985:U7_LMR51450_WSON12 C12559:F3_PTC_1812 C597931:J2_DF40C-100DS"
for t in $TEILE; do
  id="${t%%:*}"
  echo "== $id (${t#*:})"
  mkdir -p "$TMP/$id"
  "$E" --lcsc_id "$id" --3d --output "$TMP/$id/m" >/dev/null
  cp "$TMP/$id"/m.3dshapes/*.wrl "$ZIEL/"
done
ls -l "$ZIEL"
