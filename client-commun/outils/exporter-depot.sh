#!/bin/bash
# =============================================================================
#  Exporte EXACTEMENT les sources publiables du cockpit client dans un dossier neuf
#  (racine d'un dépôt git), puis lance le scan d'anonymat bloquant sur ce dossier.
#
#    bash exporter-depot.sh DOSSIER_DESTINATION
#
#  Jamais exportés : termes privés, cache (Python embarqué), dist/, archives, __pycache__.
# =============================================================================
set -euo pipefail
[ $# -eq 1 ] || { echo "usage : bash exporter-depot.sh DOSSIER_DESTINATION" >&2; exit 2; }
DEST="$1"
COMMUN="$(cd "$(dirname "$0")/.." && pwd)"
RACINE="$(cd "$COMMUN/.." && pwd)"
if [ -e "$DEST" ] && [ -n "$(ls -A "$DEST" 2>/dev/null)" ]; then
  echo "destination non vide : $DEST" >&2; exit 1
fi

FICHIERS=(
  client-commun/client/serveur.py
  client-commun/client/web/index.html
  client-commun/client/web/app.css
  client-commun/client/web/app.js
  client-commun/client/web/espace.js
  client-commun/client/web/outils.js
  client-commun/client/web/icone.svg
  client-commun/outils/scan_anonymat.py
  client-commun/outils/exporter-depot.sh
  client-commun/tests/fausse_passerelle.py
  client-commun/tests/verifs_api.py
  client-commun/tests/verifs_ui.py
  client-commun/tests/pilote_cdp.py
  client-commun/tests/stubs-mac/launchctl
  client-commun/tests/stubs-mac/open
  client-commun/tests/stubs-mac/osascript
  client-commun/tests/stubs-mac/plutil
  client-commun/tests/stubs-mac/sw_vers
  client-commun/tests/stubs-mac/sysctl
  client-commun/tests/stubs-mac/tailscale
  client-commun/tests/stubs-mac/top
  client-commun/tests/stubs-mac/uname
  client-commun/tests/stubs-mac/vm_stat
  client-commun/tests/stubs-root/id
  client-mac/construire.sh
  client-mac/paquet/INSTALLER-MAC.command
  client-mac/paquet/DESINSTALLER-MAC.command
  client-mac/paquet/A-LIRE.txt
  client-mac/tests/tester_mac.py
  client-windows/construire.sh
  client-windows/paquet/INSTALLER-WINDOWS.bat
  client-windows/paquet/INSTALLER-WINDOWS.ps1
  client-windows/paquet/DESINSTALLER-WINDOWS.bat
  client-windows/paquet/DESINSTALLER-WINDOWS.ps1
  client-windows/paquet/A-LIRE.txt
  client-windows/tests/tester_windows.py
)
mkdir -p "$DEST"
for f in "${FICHIERS[@]}"; do
  mkdir -p "$DEST/$(dirname "$f")"
  cp -p "$RACINE/$f" "$DEST/$f"
done
cp "$COMMUN/README-depot.md" "$DEST/README.md"
cat > "$DEST/.gitignore" <<'EOF'
__pycache__/
*.pyc
dist/
client-windows/cache/
client-commun/outils/termes-prives.txt
*.zip
*.sha256
EOF

TERMES="${COCKPIT_TERMES_PRIVES:-$COMMUN/outils/termes-prives.txt}"
# Tolérés : adresse de documentation (RFC 5737) des tests, empreinte SHA-256 publiée du
# Python embarqué et empreinte de la clé GPG publique de publication de python.org.
SCAN=(python3 -I "$COMMUN/outils/scan_anonymat.py" --ip-autorisee 198.51.100.7
      --autoriser 4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3
      --autoriser 7ED10B6531D7C8E1BC296021FC624643487034E5
      --autoriser ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad)  # sha256("abc"), vecteur de test public
[ -f "$TERMES" ] && SCAN+=(--termes "$TERMES")
"${SCAN[@]}" "$DEST"
( cd "$DEST" && find . -type f | sed 's|^\./||' | sort )
