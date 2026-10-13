#!/bin/bash
# =============================================================================
#  AlkymIA · Turbo OS — installation du cockpit (macOS, sans droits administrateur)
#
#  Dans le Terminal :
#      bash INSTALLER-MAC.command --cle 'VOTRE_CLE'
#
#  Options :
#      --cle CLE          clé d'accès reçue : elle sert UNIQUEMENT à tester l'accès
#                         pendant l'installation. Elle n'est jamais enregistrée : le
#                         cockpit la redemande à chaque ouverture.
#      --passerelle URL   adresse de la passerelle (défaut : http://100.111.120.117:18900)
#      --port N           port local du cockpit (défaut : 8610)
#      --sans-ouvrir      ne pas ouvrir le navigateur à la fin
#
#  Installe, dans votre session uniquement :
#      ~/Library/Application Support/Cockpit/app          le cockpit (Python standard)
#      ~/Library/Application Support/Cockpit/config.json  adresse du service (sans clé)
#      ~/Library/Application Support/Cockpit/donnees.json  vos tâches et vos notes (vous seul)
#      ~/Library/LaunchAgents/app.cockpit.client.plist    démarrage automatique
#      ~/Library/Application Support/Cockpit/navigateur   profil isolé de la fenêtre
#      ~/Library/Logs/Cockpit/                            journaux (jamais la clé)
#      ~/Applications/AlkymIA Cockpit.app                 l'application (icône, Dock)
#  Jamais : sudo, pare-feu, SSH, modification de Tailscale. Écoute sur 127.0.0.1 seulement.
#  Dernière ligne affichée :  FINI - cockpit: OK|KO - passerelle: OK|KO
#
#  La clé est personnelle et valable 48 h : elle ouvre le cockpit et n'est jamais
#  enregistrée sur ce Mac. Copiez-la en entier, sans espace, et ne la recopiez jamais
#  dans un compte rendu. Python 3.10+ requis (le python3 d'Apple, 3.9, ne suffit pas).
#  Fichier bloqué par macOS : lancez-le avec « bash » (ci-dessus).
# =============================================================================
set -u
umask 077

LABEL="app.cockpit.client"
LANCEUR_ID="app.cockpit.lanceur"
PORT="${COCKPIT_PORT:-8610}"
PASSERELLE=""
CLE=""
CLE_FOURNIE=0
OUVRIR=1
COCKPIT_ETAT="KO"
PASSERELLE_ETAT="KO"

etape() { printf '\n==> %s\n' "$*"; }
info()  { printf '    %s\n' "$*"; }
fini()  { printf 'FINI - cockpit: %s - passerelle: %s\n' "$COCKPIT_ETAT" "$PASSERELLE_ETAT"; }
echec() {
  printf '\n[ERREUR] %s\n' "$*" >&2
  fini
  exit 1
}

[ -n "${HOME:-}" ] && [ -d "$HOME" ] || echec "Dossier personnel (HOME) introuvable."

while [ $# -gt 0 ]; do
  case "$1" in
    --cle)          [ $# -ge 2 ] || echec "--cle attend une valeur."; CLE="$2"; CLE_FOURNIE=1; shift 2 ;;
    --cle=*)        CLE="${1#--cle=}"; CLE_FOURNIE=1; shift ;;
    --passerelle)   [ $# -ge 2 ] || echec "--passerelle attend une adresse."; PASSERELLE="$2"; shift 2 ;;
    --passerelle=*) PASSERELLE="${1#--passerelle=}"; shift ;;
    --port)         [ $# -ge 2 ] || echec "--port attend un numéro."; PORT="$2"; shift 2 ;;
    --port=*)       PORT="${1#--port=}"; shift ;;
    --sans-ouvrir)  OUVRIR=0; shift ;;
    -h|--help)      sed -n '2,30p' "$0"; exit 0 ;;
    *)              echec "Option inconnue : $1 (aide : bash INSTALLER-MAC.command --help)" ;;
  esac
done

SUPPORT="$HOME/Library/Application Support/Cockpit"
APPDIR="$SUPPORT/app"
CONFIG="$SUPPORT/config.json"
PROFIL_NAV="$SUPPORT/navigateur"
LOGS="$HOME/Library/Logs/Cockpit"
AGENTS="$HOME/Library/LaunchAgents"
PLIST="$AGENTS/$LABEL.plist"
BUNDLE="$HOME/Applications/AlkymIA Cockpit.app"
URL="http://127.0.0.1:$PORT"
ICI="$(cd "$(dirname "$0")" && pwd)"

cle_valide() {  # 32 à 256 caractères ASCII imprimables, sans espace
  local LC_ALL=C
  [ "${#1}" -ge 32 ] && [ "${#1}" -le 256 ] || return 1
  case "$1" in *[!!-~]*) return 1 ;; esac
  return 0
}

# --- 0. Contrôles ----------------------------------------------------------------
etape "AlkymIA · Turbo OS — contrôles"
[ "$(id -u)" != "0" ] || echec "Ne lancez PAS ce script avec sudo : l'installation se fait dans votre session."
[ "$(uname -s)" = "Darwin" ] || echec "Ce paquet est destiné à macOS (système détecté : $(uname -s))."
[ -f "$ICI/client/serveur.py" ] && [ -f "$ICI/client/web/index.html" ] || echec "Dossier client/ introuvable à côté du script ($ICI)."
[ -f "$ICI/lanceur.sh" ] && [ -f "$ICI/outils/icone_icns.py" ] || echec "Paquet incomplet : lanceur.sh ou outils/icone_icns.py manquant ($ICI)."
case "$PORT" in ''|*[!0-9]*) echec "Port invalide : $PORT" ;; esac
{ [ "$PORT" -ge 1024 ] && [ "$PORT" -le 65535 ]; } || echec "Port invalide : $PORT (1024 à 65535)."
if [ -n "$PASSERELLE" ]; then
  case "$PASSERELLE" in http://?*|https://?*) ;; *) echec "Adresse de passerelle invalide : $PASSERELLE (attendu http://adresse:port)." ;; esac
fi
if [ "$CLE_FOURNIE" = "1" ] && ! cle_valide "$CLE"; then
  echec "Clé d'accès invalide : elle doit faire 32 à 256 caractères, sans espace. Copiez-la en entier, entre apostrophes : --cle 'VOTRE_CLE'"
fi
info "macOS $(sw_vers -productVersion 2>/dev/null || echo '?') · $(uname -m) · utilisateur $(id -un)"
# Quarantaine Gatekeeper du dossier téléchargé (permet ensuite le double-clic sur les .command)
if command -v xattr >/dev/null 2>&1 && xattr -r -p com.apple.quarantine "$ICI" >/dev/null 2>&1; then
  xattr -dr com.apple.quarantine "$ICI" 2>/dev/null && info "quarantaine macOS retirée du dossier du paquet"
fi

# --- 1. Python >= 3.10 (sans déclencher l'installation des outils Apple) -------------
etape "Python 3.10 ou plus récent"
version_ok() { "$1" -I -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1; }
CANDIDATS=()
[ -n "${COCKPIT_PYTHON:-}" ] && CANDIDATS+=("$COCKPIT_PYTHON")
CANDIDATS+=(/opt/homebrew/bin/python3 /usr/local/bin/python3)
for v in 3.14 3.13 3.12 3.11 3.10; do
  CANDIDATS+=("/opt/homebrew/bin/python$v" "/usr/local/bin/python$v"
              "/Library/Frameworks/Python.framework/Versions/$v/bin/python3")
  c="$(command -v "python$v" 2>/dev/null || true)"; [ -n "$c" ] && CANDIDATS+=("$c")
done
c="$(command -v python3 2>/dev/null || true)"
[ -n "$c" ] && [ "$c" != "/usr/bin/python3" ] && CANDIDATS+=("$c")
if command -v uv >/dev/null 2>&1; then
  c="$(uv python find '>=3.10' 2>/dev/null || true)"; [ -n "$c" ] && CANDIDATS+=("$c")
fi
# /usr/bin/python3 n'est essayé que si les outils de développement Apple sont présents :
# sinon macOS ouvre une fenêtre d'installation au lieu de répondre.
if [ -x /usr/bin/python3 ] && xcode-select -p >/dev/null 2>&1; then CANDIDATS+=(/usr/bin/python3); fi
PY=""
for c in "${CANDIDATS[@]}"; do
  if [ -x "$c" ] && version_ok "$c"; then
    PY="$("$c" -I -c 'import sys; print(sys.executable)' 2>/dev/null)"
    { [ -n "$PY" ] && [ -x "$PY" ]; } || PY="$c"
    break
  fi
done
if [ -z "$PY" ]; then
  cat >&2 <<'MSG'

[ERREUR] Python 3.10 ou plus récent est introuvable.
  Le python3 fourni par Apple (3.9) ne suffit pas. Installez-en un plus récent,
  puis relancez ce script :
    - installeur officiel :   https://www.python.org/downloads/macos/
    - ou avec Homebrew :      brew install python
    - ou, sans droits admin : uv python install 3.12
MSG
  fini
  exit 1
fi
info "Python retenu : $PY ($("$PY" -I -c 'import platform; print(platform.python_version())'))"

# --- 2. Clé d'accès ------------------------------------------------------------------
etape "Clé d'accès"
info "la clé est le secret d'ouverture du cockpit : elle n'est PAS enregistrée sur ce Mac."
if [ "$CLE_FOURNIE" = "1" ]; then
  info "clé fournie : elle sert seulement à tester l'accès maintenant"
elif [ -t 0 ]; then
  printf '    Collez votre clé pour tester l'"'"'accès tout de suite (Entrée pour passer) : '
  IFS= read -r -s CLE
  printf '\n'
  if [ -n "$CLE" ]; then
    cle_valide "$CLE" || echec "Clé d'accès invalide : 32 à 256 caractères, sans espace."
    CLE_FOURNIE=1
  fi
fi
if [ "$CLE_FOURNIE" = "0" ]; then
  info "aucune clé fournie : seule la santé du service sera testée"
fi
info "à chaque ouverture, le cockpit demande votre clé ; ne la recopiez jamais dans un compte rendu."

# --- 3. Arrêt d'une version précédente, contrôle du port ------------------------------
etape "Arrêt d'une éventuelle version précédente"
DOMAINE="gui/$(id -u)"
launchctl print "$DOMAINE" >/dev/null 2>&1 || DOMAINE="user/$(id -u)"   # session sans interface (ssh)
for d in "gui/$(id -u)" "user/$(id -u)"; do launchctl bootout "$d/$LABEL" >/dev/null 2>&1 || true; done
[ -f "$PLIST" ] && launchctl unload "$PLIST" >/dev/null 2>&1
if command -v lsof >/dev/null 2>&1; then
  i=0
  while [ $i -lt 10 ] && lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; do
    for pid in $(lsof -nP -t -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null); do
      case "$(ps -o command= -p "$pid" 2>/dev/null)" in
        *"Application Support/Cockpit/app/serveur.py"*) kill "$pid" 2>/dev/null ;;  # ancien cockpit orphelin
      esac
    done
    sleep 1; i=$((i + 1))
  done
  OCCUPANT="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -Fc 2>/dev/null | sed -n 's/^c//p' | head -1)"
  if [ -n "$OCCUPANT" ]; then
    echec "Le port $PORT est déjà utilisé par « $OCCUPANT ». Fermez ce programme ou relancez avec : bash INSTALLER-MAC.command --port 8620"
  fi
fi
info "port $PORT libre"

# --- 4. Copie du cockpit et configuration ------------------------------------------------
etape "Installation dans $SUPPORT"
mkdir -p "$SUPPORT" "$LOGS" "$AGENTS" "$HOME/Applications" || echec "Création des dossiers impossible."
chmod 700 "$SUPPORT" "$LOGS"
rm -rf "$APPDIR"
{ mkdir -p "$APPDIR" && cp -R "$ICI/client/." "$APPDIR/"; } || echec "Copie du cockpit impossible."
find "$APPDIR" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null
find "$APPDIR" -name '.DS_Store' -delete 2>/dev/null
command -v xattr >/dev/null 2>&1 && xattr -dr com.apple.quarantine "$APPDIR" 2>/dev/null
chmod -R u=rwX,go= "$APPDIR"
"$PY" -I -c 'import ast, sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$APPDIR/serveur.py" \
  || echec "Le cockpit copié est illisible par Python."
ARGS_CONFIG=(--ecrire-config --config "$CONFIG")
[ -n "$PASSERELLE" ] && ARGS_CONFIG+=(--passerelle "$PASSERELLE")
"$PY" -I "$APPDIR/serveur.py" "${ARGS_CONFIG[@]}" </dev/null || echec "Configuration non enregistrée (voir ci-dessus)."
chmod 600 "$CONFIG"

# --- 5. Démarrage automatique (LaunchAgent) ------------------------------------------------
etape "Démarrage automatique ($PLIST)"
"$PY" -I - "$PLIST" "$PY" "$APPDIR" "$CONFIG" "$LOGS" "$PORT" "$LABEL" "$LANCEUR_ID" <<'PYEOF' || echec "Écriture du LaunchAgent impossible."
import os, plistlib, sys
plist, py, app, config, logs, port, label, lanceur = sys.argv[1:9]
agent = {
    "Label": label,
    "ProgramArguments": [py, "-I", "-u", os.path.join(app, "serveur.py"), "--port", port,
                         "--config", config, "--journal", os.path.join(logs, "client.log")],
    "WorkingDirectory": app,
    "EnvironmentVariables": {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin:/opt/homebrew/bin"},
    "RunAtLoad": True,
    "KeepAlive": True,
    "ThrottleInterval": 10,
    "StandardOutPath": os.path.join(logs, "client.out.log"),
    "StandardErrorPath": os.path.join(logs, "client.err.log"),
    "AssociatedBundleIdentifiers": [lanceur],
}
with open(plist, "wb") as f:
    plistlib.dump(agent, f)
with open(plist, "rb") as f:
    assert plistlib.load(f)["Label"] == label
PYEOF
chmod 644 "$PLIST"
if command -v plutil >/dev/null 2>&1; then
  plutil -lint "$PLIST" >/dev/null || echec "LaunchAgent invalide (plutil)."
fi
if ! launchctl bootstrap "$DOMAINE" "$PLIST" 2>/dev/null; then
  launchctl load -w "$PLIST" 2>/dev/null || echec "launchctl n'a pas pu charger $PLIST"
fi
launchctl kickstart "$DOMAINE/$LABEL" >/dev/null 2>&1 || true
info "service $LABEL chargé ($DOMAINE)"

# --- 6. Application « AlkymIA Cockpit » ---------------------------------------------------------
# Vraie application de bureau : fenêtre sans onglets ni barre d'adresse (mode --app d'un
# navigateur Chromium, profil isolé dans $PROFIL_NAV), icône, nom, entrée dans le Dock.
etape "Application $BUNDLE"
rm -rf "$BUNDLE"
mkdir -p "$BUNDLE/Contents/MacOS" "$BUNDLE/Contents/Resources" || echec "Création de l'application impossible."

# 6a. icône : .icns fabriqué depuis l'icône du cockpit (sips/iconutil si présents, sinon stdlib)
ICONE=""
if SORTIE_ICONE="$("$PY" -I "$ICI/outils/icone_icns.py" --svg "$ICI/client/web/icone.svg" \
                   --sortie "$BUNDLE/Contents/Resources/cockpit.icns" 2>&1)"; then
  ICONE="cockpit"
  info "${SORTIE_ICONE:-icône générée}"
else
  info "${SORTIE_ICONE:-icône non générée} (l'application garde l'icône générique)"
  rm -f "$BUNDLE/Contents/Resources/cockpit.icns"
fi

# 6b. Info.plist : application visible (Dock, Cmd-Tab, Spotlight), PAS un agent caché
"$PY" -I - "$BUNDLE" "$LANCEUR_ID" "$ICONE" <<'PYEOF' || echec "Écriture de Info.plist impossible."
import os, plistlib, sys
bundle, lanceur, icone = sys.argv[1:4]
info = {
    "CFBundleName": "AlkymIA Cockpit",
    "CFBundleDisplayName": "AlkymIA Cockpit",
    "CFBundleIdentifier": lanceur,
    "CFBundleVersion": "1.1.0",
    "CFBundleShortVersionString": "1.1.0",
    "CFBundleInfoDictionaryVersion": "6.0",
    "CFBundlePackageType": "APPL",
    "CFBundleSignature": "????",
    "CFBundleExecutable": "cockpit",
    "LSMinimumSystemVersion": "11.0",
    "LSUIElement": False,          # application de bureau : nom et icône dans le Dock
    "NSHighResolutionCapable": True,
}
if icone:
    info["CFBundleIconFile"] = icone
with open(os.path.join(bundle, "Contents", "Info.plist"), "wb") as f:
    plistlib.dump(info, f)
with open(os.path.join(bundle, "Contents", "PkgInfo"), "w", encoding="ascii") as f:
    f.write("APPL????")
with open(os.path.join(bundle, "Contents", "Info.plist"), "rb") as f:
    assert plistlib.load(f)["CFBundleExecutable"] == "cockpit"
PYEOF

# 6c. exécutable du bundle = lanceur.sh, avec ses cinq lignes de configuration
{
  printf '#!/bin/bash\n'
  printf 'URL=%q\nLABEL=%q\nPLIST=%q\nPROFIL=%q\nJOURNAL=%q\n' \
    "$URL/" "$LABEL" "$PLIST" "$PROFIL_NAV" "$LOGS/fenetre.log"
  sed -n '/^# --- corps ---$/,$p' "$ICI/lanceur.sh"
} > "$BUNDLE/Contents/MacOS/cockpit" || echec "Écriture du lanceur impossible."
chmod 755 "$BUNDLE/Contents/MacOS/cockpit"
bash -n "$BUNDLE/Contents/MacOS/cockpit" || echec "Lanceur illisible par bash."
grep -q '^# --- corps ---$' "$BUNDLE/Contents/MacOS/cockpit" || echec "Lanceur incomplet (corps absent)."
touch "$BUNDLE"
LSREG="/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"
[ -x "$LSREG" ] && "$LSREG" -f "$BUNDLE" >/dev/null 2>&1

# 6d. navigateur de la fenêtre d'application (information : la recherche a lieu à l'ouverture)
NAVIGATEUR=""
for d in /Applications "$HOME/Applications"; do
  for r in "Google Chrome.app/Contents/MacOS/Google Chrome" "Chromium.app/Contents/MacOS/Chromium" \
           "Brave Browser.app/Contents/MacOS/Brave Browser" "Microsoft Edge.app/Contents/MacOS/Microsoft Edge" \
           "Arc.app/Contents/MacOS/Arc"; do
    { [ -z "$NAVIGATEUR" ] && [ -x "$d/$r" ]; } && NAVIGATEUR="$d/$r"
  done
done
if [ -n "$NAVIGATEUR" ]; then
  info "fenêtre d'application : $(basename "$(dirname "$(dirname "$(dirname "$NAVIGATEUR")")")")"
else
  info "aucun navigateur Chromium trouvé : le cockpit s'ouvrira dans votre navigateur habituel"
  info "(installez Google Chrome pour une vraie fenêtre d'application, sans onglet ni barre d'adresse)"
fi
info "OK"

# --- 7. Test du cockpit local -------------------------------------------------------------------
etape "Test du cockpit ($URL)"
REPONSE="$(mktemp "${TMPDIR:-/tmp}/cockpit-etat.XXXXXX")"
trap 'rm -f "$REPONSE"' EXIT
etat_local() {  # $1 = clé JSON à tester (cockpit|passerelle)
  [ "$(curl -s -m 15 -o "$REPONSE" -w '%{http_code}' "$URL/api/etat" 2>/dev/null)" = "200" ] || return 1
  "$PY" -I -c 'import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
ok = d.get("cockpit") == "ok" if sys.argv[2] == "cockpit" else bool(d.get("passerelle", {}).get("joignable"))
sys.exit(0 if ok else 1)' "$REPONSE" "$1" 2>/dev/null
}
i=0
while [ $i -lt 30 ]; do
  if etat_local cockpit; then COCKPIT_ETAT="OK"; break; fi
  sleep 1; i=$((i + 1))
done
if [ "$COCKPIT_ETAT" = "OK" ]; then
  info "/api/etat : 200"
  if command -v lsof >/dev/null 2>&1; then
    ECOUTE="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | awk 'NR > 1 {print $9}' | sort -u | tr '\n' ' ')"
    info "écoute : ${ECOUTE:-?}"
    if [ "$ECOUTE" != "127.0.0.1:$PORT " ]; then
      COCKPIT_ETAT="KO"
      info "ATTENTION : écoute inattendue (attendu 127.0.0.1:$PORT uniquement)"
    fi
  fi
else
  info "le cockpit ne répond pas — dernières lignes du journal :"
  tail -n 15 "$LOGS/client.log" "$LOGS/client.err.log" 2>/dev/null | sed 's/^/      /'
fi

# --- 8. Test de la passerelle (santé sans clé, puis identité avec la clé) ---------------------------
etape "Test de la passerelle"
printf '%s\n' "$CLE" | "$PY" -I "$APPDIR/serveur.py" --verifier --config "$CONFIG"
RC=$?
CLE=""
if [ "$RC" = "0" ]; then
  PASSERELLE_ETAT="OK"
  # Le service lancé par macOS doit lui aussi joindre la passerelle (macOS 15+ : « Réseau local »).
  if [ "$COCKPIT_ETAT" = "OK" ]; then
    i=0; SERVICE_OK=0
    while [ $i -lt 4 ]; do
      if etat_local passerelle; then SERVICE_OK=1; break; fi
      sleep 3; i=$((i + 1))
    done
    if [ "$SERVICE_OK" = "1" ]; then
      info "le service du cockpit joint aussi la passerelle"
    else
      PASSERELLE_ETAT="KO"
      info "le Terminal joint la passerelle, mais PAS le service du cockpit : autorisez « python3 » dans"
      info "Réglages Système > Confidentialité et sécurité > Réseau local, puis relancez ce script."
    fi
  fi
else
  case "$RC" in
    13) info "=> Accès expiré : une nouvelle clé doit vous être fournie." ;;
    14) info "=> Clé refusée." ;;
    21) info "=> Clé mal copiée (format inattendu)." ;;
    15) info "=> Adhésion pas encore approuvée : réessayez plus tard." ;;
    11) info "=> Passerelle injoignable (voir la cause ci-dessus)." ;;
    *)  info "=> Passerelle non vérifiée (code $RC)." ;;
  esac
fi

if [ "$COCKPIT_ETAT" = "OK" ] && [ "$OUVRIR" = "1" ]; then
  open "$BUNDLE" >/dev/null 2>&1 || open "$URL/" >/dev/null 2>&1 || true
fi

printf '\nCockpit      : %s\nApplication  : %s\nJournaux     : %s\nDésinstaller : bash "%s/DESINSTALLER-MAC.command"\n' \
  "$URL" "$BUNDLE" "$LOGS" "$ICI"
fini
if [ "$COCKPIT_ETAT" != "OK" ]; then exit 1; fi
if [ "$PASSERELLE_ETAT" != "OK" ]; then exit 2; fi
exit 0
