#!/bin/bash
# =============================================================================
#  AlkymIA · Turbo OS — désinstallation du cockpit (macOS, sans droits administrateur)
#
#  Dans le Terminal :  bash DESINSTALLER-MAC.command
#
#  Supprime : ~/Library/LaunchAgents/app.cockpit.client.plist,
#             ~/Library/Application Support/Cockpit (cockpit, configuration, profil de la
#             fenêtre d'application), ~/Library/Logs/Cockpit,
#             ~/Applications/AlkymIA Cockpit.app.
#  Ferme d'abord la fenêtre du cockpit ; votre navigateur habituel n'est pas touché.
#  Ne touche à rien d'autre (Tailscale, Python et vos fichiers restent en place).
# =============================================================================
set -u
[ -n "${HOME:-}" ] && [ -d "$HOME" ] || { echo "Dossier personnel (HOME) introuvable." >&2; exit 1; }
[ "$(id -u)" != "0" ] || { echo "Ne lancez pas ce script avec sudo." >&2; exit 1; }
case "${1:-}" in -h|--help) sed -n '2,12p' "$0"; exit 0 ;; esac

LABEL="app.cockpit.client"
SUPPORT="$HOME/Library/Application Support/Cockpit"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
BUNDLE="$HOME/Applications/AlkymIA Cockpit.app"
LOGS="$HOME/Library/Logs/Cockpit"

PROFIL_NAV="$SUPPORT/navigateur"

echo "==> Fermeture de la fenêtre du cockpit"
# Seuls les processus qui utilisent le profil dédié du cockpit sont visés :
# le navigateur personnel et ses onglets ne sont pas touchés.
if command -v pgrep >/dev/null 2>&1; then
  for pid in $(pgrep -f -- "--user-data-dir=$PROFIL_NAV" 2>/dev/null); do
    kill "$pid" 2>/dev/null
  done
fi

echo "==> Arrêt du service $LABEL"
for d in "gui/$(id -u)" "user/$(id -u)"; do
  launchctl bootout "$d/$LABEL" >/dev/null 2>&1 || true
done
[ -f "$PLIST" ] && launchctl unload "$PLIST" >/dev/null 2>&1
# Processus du cockpit encore vivant (lancé à la main, par exemple)
if command -v pgrep >/dev/null 2>&1; then
  for pid in $(pgrep -f "Application Support/Cockpit/app/serveur.py" 2>/dev/null); do
    kill "$pid" 2>/dev/null
  done
fi

echo "==> Suppression des fichiers"
rm -f "$PLIST"
rm -rf "$BUNDLE" "$LOGS" "$SUPPORT"

RESTE=0
for p in "$PLIST" "$BUNDLE" "$SUPPORT" "$LOGS"; do
  if [ -e "$p" ]; then echo "    encore présent : $p"; RESTE=1; fi
done
if launchctl list 2>/dev/null | grep -q "$LABEL"; then
  echo "    le service est encore chargé"; RESTE=1
fi
if [ "$RESTE" = "0" ]; then
  echo "FINI - cockpit désinstallé"
else
  echo "FINI - désinstallation incomplète (voir ci-dessus)"
  exit 1
fi
