#!/bin/bash
# =============================================================================
#  Construit le paquet générique cockpit-client-windows.zip (AUCUNE clé dedans).
#
#    bash construire.sh [--sortie DOSSIER] [--sans-tests]
#
#  Étapes (toute erreur arrête la construction) :
#    1. Python officiel embarqué (python.org) : téléchargé dans cache/ s'il manque, puis
#       vérifié : empreinte SHA-256 épinglée + signature GPG du responsable des versions
#       Windows (empreinte de clé épinglée) si gpg est présent ;
#    2. assemble paquet/ + ../client-commun/client (CRLF ; BOM UTF-8 pour .ps1 et A-LIRE) ;
#    3. contrôles : py_compile, .bat en ASCII pur, syntaxe PowerShell (pwsh si présent) ;
#    4. scan d'anonymat BLOQUANT (dossier puis archive ; le zip Python officiel n'est
#       dispensé que pour son contenu, vérifié par empreinte) ;
#    5. test sous Wine (tests/tester_windows.py) si wine est présent ;
#    6. copie dans --sortie (défaut : ./dist) + empreinte .sha256.
#  Variables : PWSH (chemin de pwsh), COCKPIT_TERMES_PRIVES (fichier de termes interdits).
# =============================================================================
set -euo pipefail
ICI="$(cd "$(dirname "$0")" && pwd)"
COMMUN="$(cd "$ICI/../client-commun" && pwd)"
NOM="cockpit-client-windows"
SORTIE="$ICI/dist"
TESTS=1
while [ $# -gt 0 ]; do
  case "$1" in
    --sortie) SORTIE="$2"; shift 2 ;;
    --sans-tests) TESTS=0; shift ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "option inconnue : $1" >&2; exit 2 ;;
  esac
done

PY_VERSION="3.12.10"   # dernière 3.12 publiée avec binaires Windows
PY_ZIP="python-$PY_VERSION-embed-amd64.zip"
PY_SHA256="4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
PY_GPG_EMPREINTE="7ED10B6531D7C8E1BC296021FC624643487034E5"
CACHE="$ICI/cache"
mkdir -p "$CACHE"

echo "== 1. Python embarqué $PY_VERSION"
if [ ! -f "$CACHE/$PY_ZIP" ]; then
  curl -fsS -m 300 -o "$CACHE/$PY_ZIP.part" "https://www.python.org/ftp/python/$PY_VERSION/$PY_ZIP"
  mv "$CACHE/$PY_ZIP.part" "$CACHE/$PY_ZIP"
fi
[ -f "$CACHE/$PY_ZIP.asc" ] || curl -fsS -m 60 -o "$CACHE/$PY_ZIP.asc" "https://www.python.org/ftp/python/$PY_VERSION/$PY_ZIP.asc" || true
echo "$PY_SHA256  $CACHE/$PY_ZIP" | sha256sum -c --quiet - || { echo "empreinte SHA-256 du Python embarqué inattendue" >&2; exit 1; }
echo "   SHA-256 épinglée : OK"
if command -v gpg >/dev/null 2>&1 && [ -f "$CACHE/$PY_ZIP.asc" ]; then
  CLE_GPG="$CACHE/cle-publication-python-windows.asc"
  [ -s "$CLE_GPG" ] || curl -fsS -m 60 -o "$CLE_GPG" "https://keyserver.ubuntu.com/pks/lookup?op=get&search=0x$PY_GPG_EMPREINTE"
  GNUPGHOME="$(mktemp -d)"; export GNUPGHOME
  gpg -q --import "$CLE_GPG" 2>/dev/null
  ETAT_GPG="$(gpg --status-fd 1 --verify "$CACHE/$PY_ZIP.asc" "$CACHE/$PY_ZIP" 2>/dev/null || true)"
  rm -rf "$GNUPGHOME"; unset GNUPGHOME
  if printf '%s\n' "$ETAT_GPG" | grep -q "^\[GNUPG:\] VALIDSIG $PY_GPG_EMPREINTE "; then
    echo "   signature GPG ($PY_GPG_EMPREINTE) : OK"
  else
    echo "signature GPG du Python embarqué invalide ou d'une autre clé" >&2; exit 1
  fi
else
  echo "   AVERTISSEMENT : gpg ou signature absente, seule l'empreinte épinglée est vérifiée"
fi

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
STAGE="$TMP/$NOM"
mkdir -p "$STAGE/client/web" "$STAGE/python"
crlf() { sed 's/\r$//; s/$/\r/' "$1"; }
bom() { printf '\357\273\277'; }
for f in INSTALLER-WINDOWS.bat DESINSTALLER-WINDOWS.bat; do crlf "$ICI/paquet/$f" > "$STAGE/$f"; done
for f in INSTALLER-WINDOWS.ps1 DESINSTALLER-WINDOWS.ps1 A-LIRE.txt; do { bom; crlf "$ICI/paquet/$f"; } > "$STAGE/$f"; done
cp "$COMMUN/client/serveur.py" "$STAGE/client/"
for f in index.html app.css app.js espace.js outils.js machine.js icone.svg; do
  cp "$COMMUN/client/web/$f" "$STAGE/client/web/$f"
done
cp "$CACHE/$PY_ZIP" "$STAGE/python/"
chmod -R u=rwX,go=rX "$STAGE"

echo "== 2. contrôles"
PYTHONPYCACHEPREFIX="$TMP/pyc" python3 -m py_compile "$STAGE/client/serveur.py"
echo "   py_compile : OK"
if LC_ALL=C grep -qP '[^\x00-\x7F]' "$STAGE"/*.bat; then echo ".bat non ASCII" >&2; exit 1; fi
echo "   .bat ASCII : OK"
grep -qi "$PY_SHA256" "$STAGE/INSTALLER-WINDOWS.ps1" || { echo "empreinte Python absente de INSTALLER-WINDOWS.ps1" >&2; exit 1; }
grep -q "'$PY_ZIP'" "$STAGE/INSTALLER-WINDOWS.ps1" || { echo "nom du zip Python absent de INSTALLER-WINDOWS.ps1" >&2; exit 1; }
PW="${PWSH:-$(command -v pwsh || true)}"
if [ -n "$PW" ]; then
  for f in INSTALLER-WINDOWS.ps1 DESINSTALLER-WINDOWS.ps1; do
    "$PW" -NoProfile -NonInteractive -Command "\$t=\$null; \$e=\$null; [void][System.Management.Automation.Language.Parser]::ParseFile('$STAGE/$f', [ref]\$t, [ref]\$e); if (\$e.Count) { \$e | ForEach-Object { '{0}:{1} {2}' -f \$_.Extent.StartLineNumber, \$_.Extent.StartColumnNumber, \$_.Message }; exit 1 }"
  done
  echo "   syntaxe PowerShell : OK"
else
  echo "   AVERTISSEMENT : pwsh absent (variable PWSH), syntaxe PowerShell non vérifiée"
fi
if command -v node >/dev/null 2>&1; then
  for f in app.js espace.js outils.js machine.js; do node --check "$STAGE/client/web/$f"; done
  echo "   node --check : OK"
fi

echo "== 3. scan d'anonymat (bloquant)"
TERMES="${COCKPIT_TERMES_PRIVES:-$COMMUN/outils/termes-prives.txt}"
SCAN=(python3 -I "$COMMUN/outils/scan_anonymat.py" --exclure-contenu "python/$PY_ZIP" --exclure-contenu "*/python/$PY_ZIP"
      --autoriser "$PY_SHA256")
if [ -f "$TERMES" ]; then SCAN+=(--termes "$TERMES"); else echo "   AVERTISSEMENT : fichier de termes privés absent ($TERMES)"; fi
"${SCAN[@]}" "$STAGE"

echo "== 4. archive"
find "$STAGE" -exec touch -h -t 202610130000 {} +
( cd "$TMP" && TZ=UTC zip -X -q -r "$NOM.zip" "$NOM" )
"${SCAN[@]}" "$TMP/$NOM.zip"
echo "   $NOM.zip : $(stat -c %s "$TMP/$NOM.zip") octets"

if [ "$TESTS" = "1" ]; then
  if command -v wine >/dev/null 2>&1; then
    echo "== 5. test sous Wine"
    python3 -I "$ICI/tests/tester_windows.py" --zip "$TMP/$NOM.zip" --travail "$TMP" ${PW:+--pwsh "$PW"}
  else
    echo "== 5. AVERTISSEMENT : wine absent, test Windows non exécuté"
  fi
fi

echo "== 6. livraison"
mkdir -p "$SORTIE"
install -m 644 "$TMP/$NOM.zip" "$SORTIE/$NOM.zip"
( cd "$SORTIE" && sha256sum "$NOM.zip" > "$NOM.zip.sha256" )
cat "$SORTIE/$NOM.zip.sha256"
echo "OK : $SORTIE/$NOM.zip"
