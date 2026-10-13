#!/bin/bash
# =============================================================================
#  Construit le paquet générique cockpit-client-mac.zip (AUCUNE clé dedans).
#
#    bash construire.sh [--sortie DOSSIER] [--sans-tests] [--longs]
#
#  Étapes (toute erreur arrête la construction) :
#    1. assemble paquet/ + ../client-commun/client dans un dossier temporaire ;
#    2. contrôles : bash -n, shellcheck, py_compile (Python courant), node --check ;
#    3. scan d'anonymat BLOQUANT (dossier puis archive) ; termes privés lus dans
#       $COCKPIT_TERMES_PRIVES ou ../client-commun/outils/termes-prives.txt (non publié) ;
#    4. archive zip déterministe (< 2 Mo) ;
#    5. test de bout en bout sous macOS simulé (tests/tester_mac.py) contre la fausse passerelle ;
#    6. copie dans --sortie (défaut : ./dist) + empreinte .sha256.
#  Variables : SHELLCHECK (chemin de shellcheck), COCKPIT_TEST_PYTHON (Python des tests, 3.10 conseillé).
# =============================================================================
set -euo pipefail
ICI="$(cd "$(dirname "$0")" && pwd)"
COMMUN="$(cd "$ICI/../client-commun" && pwd)"
NOM="cockpit-client-mac"
SORTIE="$ICI/dist"
TESTS=1
LONGS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --sortie) SORTIE="$2"; shift 2 ;;
    --sans-tests) TESTS=0; shift ;;
    --longs) LONGS=(--longs); shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "option inconnue : $1" >&2; exit 2 ;;
  esac
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
STAGE="$TMP/$NOM"
mkdir -p "$STAGE/client/web" "$STAGE/outils"
cp "$ICI/paquet/INSTALLER-MAC.command" "$ICI/paquet/DESINSTALLER-MAC.command" "$ICI/paquet/A-LIRE.txt" \
   "$ICI/paquet/lanceur.sh" "$STAGE/"
cp "$ICI/paquet/outils/icone_icns.py" "$STAGE/outils/"
cp "$COMMUN/client/serveur.py" "$STAGE/client/"
for f in index.html app.css app.js espace.js outils.js icone.svg; do
  cp "$COMMUN/client/web/$f" "$STAGE/client/web/$f"
done
chmod 755 "$STAGE"/*.command
chmod 644 "$STAGE/A-LIRE.txt" "$STAGE/lanceur.sh" "$STAGE/outils/icone_icns.py" \
          "$STAGE/client/serveur.py" "$STAGE/client/web/"*

echo "== 1. contrôles"
bash -n "$STAGE/INSTALLER-MAC.command"
bash -n "$STAGE/DESINSTALLER-MAC.command"
bash -n "$STAGE/lanceur.sh"
grep -q '^# --- corps ---$' "$STAGE/lanceur.sh" || { echo "lanceur.sh : repère « corps » absent" >&2; exit 1; }
SC="${SHELLCHECK:-$(command -v shellcheck || true)}"
if [ -n "$SC" ]; then
  "$SC" -s bash "$STAGE/INSTALLER-MAC.command" "$STAGE/DESINSTALLER-MAC.command" "$STAGE/lanceur.sh"
  echo "   shellcheck : OK"
else
  echo "   AVERTISSEMENT : shellcheck absent (variable SHELLCHECK)"
fi
PYTHONPYCACHEPREFIX="$TMP/pyc" python3 -m py_compile "$STAGE/client/serveur.py" "$STAGE/outils/icone_icns.py"
echo "   py_compile : OK"
if command -v node >/dev/null 2>&1; then
  for f in app.js espace.js outils.js; do node --check "$STAGE/client/web/$f"; done
  echo "   node --check : OK"
fi

echo "== 2. scan d'anonymat (bloquant)"
TERMES="${COCKPIT_TERMES_PRIVES:-$COMMUN/outils/termes-prives.txt}"
SCAN=(python3 -I "$COMMUN/outils/scan_anonymat.py")
if [ -f "$TERMES" ]; then SCAN+=(--termes "$TERMES"); else echo "   AVERTISSEMENT : fichier de termes privés absent ($TERMES)"; fi
"${SCAN[@]}" "$STAGE"

echo "== 3. archive"
find "$STAGE" -exec touch -h -t 202610130000 {} +
( cd "$TMP" && TZ=UTC zip -X -q -r "$NOM.zip" "$NOM" )
"${SCAN[@]}" "$TMP/$NOM.zip"
TAILLE="$(stat -c %s "$TMP/$NOM.zip" 2>/dev/null || stat -f %z "$TMP/$NOM.zip")"
[ "$TAILLE" -lt 2097152 ] || { echo "archive trop grosse : $TAILLE octets" >&2; exit 1; }
echo "   $NOM.zip : $TAILLE octets"

if [ "$TESTS" = "1" ]; then
  echo "== 4. test de bout en bout (macOS simulé)"
  PYT="${COCKPIT_TEST_PYTHON:-}"
  if [ -z "$PYT" ] && command -v uv >/dev/null 2>&1; then PYT="$(uv python find 3.10 2>/dev/null || true)"; fi
  PYT="${PYT:-$(command -v python3)}"
  python3 -I "$ICI/tests/tester_mac.py" --zip "$TMP/$NOM.zip" --python "$PYT" --travail "$TMP" "${LONGS[@]}"
fi

echo "== 5. livraison"
mkdir -p "$SORTIE"
install -m 644 "$TMP/$NOM.zip" "$SORTIE/$NOM.zip"
( cd "$SORTIE" && sha256sum "$NOM.zip" > "$NOM.zip.sha256" )
cat "$SORTIE/$NOM.zip.sha256"
echo "OK : $SORTIE/$NOM.zip"
