# AlkymIA · Turbo OS — cockpit client

Cockpit client léger pour macOS et Windows : une page locale (`http://127.0.0.1:8610`)
servie par un petit serveur Python (bibliothèque standard uniquement), qui relaie les
demandes du client vers la passerelle, avec sa clé d'accès, et héberge son espace de
travail (tâches, planning, notes, outils) entièrement sur sa machine.

- Passerelle par défaut : `http://100.111.120.117:18900` (joignable via Tailscale).
- **La clé d'accès est le secret d'ouverture** : elle n'est PAS enregistrée. Elle est collée
  à chaque ouverture, gardée en mémoire du processus seulement, jamais écrite sur le disque,
  jamais dans le journal, jamais envoyée au navigateur (qui ne reçoit qu'un cookie de session
  opaque de 256 bits, `HttpOnly`, `SameSite=Strict`). Cockpit verrouillé = aucune route
  accessible, aucune télémétrie.
- Verrouillage : bouton « Verrouiller », 30 min sans activité (`--minutes-verrou`), arrêt du
  processus, ou verdict `acces_expire` / `acces_suspendu` de la passerelle.
- Rubriques : Assistants, Mes tâches, Mon planning, Mes notes, Outils, Mon accès.
- Aucune ressource externe chargée par la page ; aucun module Python à installer.

## Installer (côté utilisateur)

### macOS (Python 3.10 ou plus récent requis)

```bash
cd ~/Downloads/cockpit-client-mac
bash INSTALLER-MAC.command --cle 'VOTRE_CLE'
```

Installe dans la session de l'utilisateur, sans sudo : `~/Library/Application Support/Cockpit`,
le LaunchAgent `app.cockpit.client`, `~/Applications/AlkymIA Cockpit.app`.
Désinstaller : `bash DESINSTALLER-MAC.command`.

### Windows 10/11 (Python officiel embarqué dans le paquet)

Double-cliquer sur `INSTALLER-WINDOWS.bat` (la clé demandée sert seulement à tester
l'accès ; Entrée pour passer), ou :

```bat
INSTALLER-WINDOWS.bat --cle VOTRE_CLE
```

Installe sans droits administrateur dans `%LOCALAPPDATA%\Cockpit`, avec un raccourci
« AlkymIA Cockpit » dans le dossier Démarrage et sur le Bureau.
Désinstaller : `DESINSTALLER-WINDOWS.bat`.

### Points d'attention

1. **Quarantaine macOS (Gatekeeper)** : lancer les scripts avec `bash`, ou clic droit > Ouvrir,
   ou `xattr -dr com.apple.quarantine ~/Downloads/cockpit-client-mac`.
2. **macOS 15 ou plus récent** : autoriser « python3 » dans Réglages Système >
   Confidentialité et sécurité > Réseau local (l'installeur le signale si besoin).
3. **Tailscale déjà connecté à un autre compte** : basculer sur le compte invité, puis attendre
   l'approbation de l'appareil.
4. **Python sur macOS** : le `python3` d'Apple (3.9) ne suffit pas ; installer Python 3.10 ou plus
   récent (python.org ou Homebrew).
5. **Clé d'accès** : valable 48 h, à copier en entier sans espace ; ne jamais la faire figurer dans
   un compte rendu, un ticket ou une capture. `--cle` ne sert qu'à un test immédiat pendant
   l'installation : la clé n'est jamais enregistrée et sera redemandée à chaque ouverture.
6. **Données locales** : tâches, créneaux et notes sont enregistrés en clair dans
   `donnees.json` (600) à côté de la configuration. Ils ne sont pas chiffrés par la clé — une
   clé perdue ne doit pas rendre ses notes irrécupérables — donc ils restent lisibles par qui
   a accès à la session de l'utilisateur. La désinstallation les supprime : exporter avant.

Options communes : `--passerelle URL`, `--port N` (défaut 8610), `--sans-ouvrir`.
Dernière ligne affichée par les installeurs : `FINI - cockpit: OK|KO - passerelle: OK|KO`.
En cas d'échec, la cause est expliquée juste au-dessus (Tailscale déconnecté, appareil en
attente d'approbation, clé refusée, accès expiré, port occupé...).

## Contenu du dépôt

| Chemin | Rôle |
|---|---|
| `client-commun/client/serveur.py` | serveur local + relais (macOS, Windows, Linux) |
| `client-commun/client/web/` | interface (HTML, CSS, JS locaux) |
| `client-commun/outils/scan_anonymat.py` | scan bloquant des paquets (fuites, IP, clés) |
| `client-commun/outils/exporter-depot.sh` | exporte exactement les sources publiables |
| `client-commun/tests/` | fausse passerelle de test, vérifications HTTP, interface pilotée (CDP), faux outils macOS |
| `client-mac/` | paquet macOS, `construire.sh`, test de bout en bout (macOS simulé) |
| `client-windows/` | paquet Windows, `construire.sh`, test sous Wine |

## Construire les paquets

```bash
bash client-mac/construire.sh [--sortie DOSSIER] [--longs]
bash client-windows/construire.sh [--sortie DOSSIER]
```

Chaque construction : contrôles (`bash -n`, shellcheck, `py_compile`, syntaxe PowerShell),
scan d'anonymat bloquant, archive, test de bout en bout contre la fausse passerelle, puis
copie dans `--sortie` (défaut `./dist`) avec l'empreinte `.sha256`.
Le Python embarqué Windows n'est pas versionné : `construire.sh` le télécharge depuis
python.org et vérifie son empreinte SHA-256 épinglée et sa signature GPG.
Des termes interdits supplémentaires peuvent être fournis au scan par un fichier privé
(variable `COCKPIT_TERMES_PRIVES`, un terme par ligne), jamais versionné.

## Sécurité du serveur local

- Écoute `127.0.0.1` uniquement ; en-tête `Host` exact exigé (anti DNS-rebinding).
- `POST` : `Origin` et `Sec-Fetch-Site` de même origine, `Content-Type: application/json`,
  corps ≤ 16 Ko vers la passerelle, ≤ 256 Ko pour une écriture locale, ≤ 512 Ko pour un
  import ; pas de CORS (pré-vols refusés).
- Toutes les routes `/api/*` exigent la session (cookie opaque) sauf `POST /api/deverrouiller`,
  `POST /api/verrouiller` et `GET /api/etat` (qui ne dit que le verrou et l'état du service).
  Sinon : `401 {"erreur": "verrouille"}`. Au plus 10 essais de clé par minute.
- Politique de sécurité de contenu stricte ; aucune ressource externe.
- Relais sans proxy système ni redirection ; la clé est masquée si une réponse la contenait.
- Journal : méthode, chemin, statut, durée — jamais d'en-tête ni de contenu.

## API locale (navigateur → serveur local)

| Route | Relais |
|---|---|
| `POST /api/deverrouiller` | `GET /v1/moi` avec la clé collée ; succès -> cookie de session |
| `POST /api/verrouiller` | aucun : efface la clé de la mémoire et la session |
| `GET /api/etat` | état local minimal (verrou, service) + `GET /v1/sante` (sans clé) |
| `GET /api/moi` | `GET /v1/moi` |
| `GET /api/catalogue` | `GET /v1/catalogue` |
| `GET /api/demandes` | `GET /v1/demandes` |
| `POST /api/demander` | `POST /v1/demander` `{ref, question, mode, contexte?}` — `contexte` : ≤ 12 messages `{role: user\|assistant, content}` |
| `POST /api/demande-acces` | `POST /v1/demande-acces` `{ref, motif}` |
| `GET /api/espace` · `GET /api/taches` · `GET /api/creneaux` · `GET /api/notes` | **aucun** : lecture locale (filtres `q`, `etat`, `priorite`, `du`, `au`) |
| `GET /api/espace/export` | **aucun** : sauvegarde JSON (`?fichier=1` pour le téléchargement) |
| `POST /api/{taches,creneaux,notes}/{creer,modifier,supprimer}` | **aucun** : écriture locale dans `donnees.json` (600) |
| `POST /api/espace/importer` | **aucun** : `{donnees, mode: fusionner\|remplacer}` |

Délais : lecture 15 s, réponse rapide 30 s, réponse approfondie et compétences 70 s
(supérieurs aux budgets de la passerelle : 25 s et 55 s).
Accès expiré (`401 acces_expire`) : l'interface n'affiche plus que « Votre accès a expiré ».

## Télémétrie

Toutes les 60 secondes, `POST /v1/telemetrie` : système et version, modèle et charge du
processeur, nombre de cœurs, mémoire totale et utilisée, disque total et libre, durée
depuis le démarrage, version du client. Aucun nom de machine, aucun fichier ni contenu
personnel. Envoi UNIQUEMENT quand le cockpit est déverrouillé (pas de clé en mémoire =
aucun envoi). Arrêt dès que l'accès est expiré ou la clé refusée.
