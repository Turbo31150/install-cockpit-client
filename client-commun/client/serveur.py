#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cockpit client : interface locale + relais authentifié vers la passerelle.

Python 3.10 ou plus récent, bibliothèque standard uniquement (macOS, Windows, Linux).

La clé d'accès est le SECRET D'OUVERTURE du cockpit : elle est collée à chaque ouverture,
gardée en mémoire du processus seulement (jamais sur le disque, jamais dans le journal,
jamais envoyée au navigateur) et remplacée par un jeton de session opaque dans un cookie.
Sans clé en mémoire, le cockpit est verrouillé : toutes les routes répondent 401.

Deux familles de routes (toutes exigent la session) :
  - /api/moi, /api/catalogue, /api/demandes, /api/demander, /api/demande-acces
    sont relayées à la passerelle avec la clé d'accès ;
  - /api/taches, /api/notes, /api/creneaux, /api/espace restent ENTIÈREMENT sur
    cet ordinateur (fichier « donnees.json » à côté de la configuration, 600) et
    ne partent jamais vers le réseau.
Exceptions : POST /api/deverrouiller, POST /api/verrouiller et GET /api/etat (qui ne dit
que si le cockpit est verrouillé et si le service distant répond).

  serveur.py [--port 8610] [--config FICHIER] [--journal FICHIER]
             [--intervalle-telemetrie 60] [--minutes-verrou 30]
        lance le serveur local (écoute 127.0.0.1 uniquement)
  serveur.py --verifier [--config FICHIER]
        teste la passerelle : santé sans clé, puis identité si une clé est fournie
        sur l'entrée standard (la clé n'est jamais enregistrée)
  serveur.py --ecrire-config --config FICHIER [--passerelle URL]
        écrit la configuration (adresse de la passerelle seulement ; une clé
        laissée par une ancienne version est retirée du fichier)
  serveur.py --lanceur [--port 8610] [--config FICHIER] [--journal FICHIER]
        démarre le serveur s'il ne répond pas, puis ouvre le navigateur
  serveur.py --sonder [--port 8610]
        interroge le cockpit local déjà lancé (installeurs)

La clé d'accès ne reste QUE dans la mémoire de ce processus : elle n'est ni enregistrée,
ni envoyée au navigateur, ni écrite dans le journal. Elle disparaît à l'arrêt du processus.
"""
from __future__ import annotations

import argparse
import errno
import hmac
import http.client
import json
import logging
import logging.handlers
import os
import platform
import re
import secrets
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
from datetime import date, datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

VERSION = "1.5.0"
HOTE = "127.0.0.1"
PORT_DEFAUT = 8610
PASSERELLE_DEFAUT = "http://100.111.120.117:18900"

TAILLE_MAX_CORPS = 16 * 1024      # contrat de la passerelle
TAILLE_MAX_LOCAL = 256 * 1024     # écriture d'une tâche ou d'une note (reste sur cette machine)
TAILLE_MAX_IMPORT = 512 * 1024    # import d'une sauvegarde complète
TAILLE_MAX_REPONSE = 4 * 1024 * 1024
DELAI_CONNEXION = 6
DELAI_SANTE = 4
DELAI_LECTURE = 15
DELAI_RAPIDE = 30   # budget passerelle : 25 s
DELAI_LONG = 70     # budget passerelle : 55 s (approfondi, compétences)
DELAI_TABLE_RONDE = 200   # budget passerelle : 180 s (table ronde : 2 tours + synthèse)
DELAI_TELEMETRIE = 10
REFS_LONGUES = frozenset({"llm-local", "llm-code", "grand-conseil", "table-ronde"})
REFS_TRES_LONGUES = frozenset({"table-ronde"})
MAX_MESSAGES_CONTEXTE = 12   # 6 échanges = 12 messages {role: user|assistant, content}
MAX_QUESTION = 4000
MIN_QUESTION = 3
MAX_MOTIF = 1000
RX_CLE = re.compile(r"[\x21-\x7e]{32,256}")
RX_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}")

# ---- verrouillage : la clé est le secret d'ouverture, gardé en mémoire seulement
NOM_COOKIE = "cockpit_session"
MINUTES_VERROU_DEFAUT = 30.0
RX_JETON = re.compile(r"[A-Za-z0-9_-]{16,128}")

# ---- espace personnel local (tâches, planning, notes) : rien ne sort de cette machine
NOM_DONNEES = "donnees.json"
TAILLE_MAX_DONNEES = 4 * 1024 * 1024
MAX_PAR_RUBRIQUE = {"taches": 2000, "notes": 500, "creneaux": 2000}
MAX_TITRE = 200
MAX_DETAIL = 2000
MAX_NOTE = 20000
MAX_LIEU = 120
PRIORITES = ("basse", "normale", "haute")
RX_ID = re.compile(r"[0-9a-f]{16}")
RX_JOUR = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
RX_HEURE = re.compile(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]")

ICI = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(ICI, "web")
STATIQUES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/espace.js": ("espace.js", "text/javascript; charset=utf-8"),
    "/outils.js": ("outils.js", "text/javascript; charset=utf-8"),
    "/machine.js": ("machine.js", "text/javascript; charset=utf-8"),
    "/icone.svg": ("icone.svg", "image/svg+xml"),
}
# Routes locales : traitées par ce processus, JAMAIS relayées à la passerelle.
GET_LOCAL = frozenset(["/api/espace", "/api/espace/export", "/api/taches", "/api/creneaux", "/api/notes"])
POST_LOCAL = frozenset(["/api/espace/importer"]
                       + ["/api/%s/%s" % (r, a) for r in ("taches", "creneaux", "notes")
                          for a in ("creer", "modifier", "supprimer")])
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
       "connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
ENTETES_SECURITE = (
    ("Cache-Control", "no-store"),
    ("X-Content-Type-Options", "nosniff"),
    ("Referrer-Policy", "no-referrer"),
    ("X-Frame-Options", "DENY"),
    ("Cross-Origin-Resource-Policy", "same-origin"),
    ("Cross-Origin-Opener-Policy", "same-origin"),
    ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
    ("Content-Security-Policy", CSP),
)

MESSAGES = {
    "non_configure": "Ce cockpit n'est pas configuré : relancez l'installation.",
    "config_invalide": "La configuration du cockpit est illisible. Relancez l'installation.",
    "verrouille": "Cockpit verrouillé : collez votre clé d'accès pour l'ouvrir.",
    "cle_absente": "Collez votre clé d'accès pour ouvrir le cockpit.",
    "cle_format": "Cette clé n'a pas le bon format (32 à 256 caractères, sans espace). Copiez-la en entier.",
    "verrou_inactivite": "Cockpit reverrouillé après une période sans activité : collez votre clé pour continuer.",
    "verrou_demande": "Cockpit verrouillé. Votre clé n'est plus en mémoire.",
    "passerelle_injoignable": "Le service distant est injoignable. Vérifiez que Tailscale est ouvert et connecté.",
    "passerelle_indisponible": "Le service distant ne répond pas pour le moment. Réessayez dans quelques minutes.",
    "delai_depasse": "Le service distant a mis trop de temps à répondre. Réessayez dans un moment.",
    "passerelle_erreur": "Réponse inattendue du service distant. Réessayez dans un moment.",
    "acces_expire": "Votre accès a expiré.",
    "cle_refusee": "Votre clé d'accès est refusée.",
    "cle_invalide": "Votre clé d'accès est refusée (inconnue, mal copiée ou révoquée).",
    "non_accorde": "Cet élément ne fait pas partie de vos accès.",
    "introuvable": "Élément introuvable.",
    "limite": "Limite atteinte : quota du jour épuisé ou trop de demandes en une minute. Réessayez plus tard.",
    "deja_accorde": "Cet élément fait déjà partie de vos accès.",
    "acces_suspendu": "Votre accès est suspendu.",
    "requete_invalide": "Requête invalide.",
    "methode_refusee": "Méthode non autorisée.",
    "type_refuse": "Format de requête non pris en charge (JSON attendu).",
    "origine_refusee": "Requête refusée : origine non autorisée.",
    "hote_refuse": "Hôte non autorisé : ouvrez l'adresse http://127.0.0.1 du cockpit.",
    "trop_gros": "Requête trop volumineuse (16 Ko au maximum).",
    "donnees_illisibles": ("Vos tâches et vos notes ne sont pas lisibles : le fichier de données de ce "
                           "cockpit est abîmé. Une copie a été mise de côté et un espace vide a été créé."),
    "donnees_ecriture": "Enregistrement impossible sur cet ordinateur (disque plein ou dossier protégé ?).",
    "espace_plein": "Limite atteinte pour cette rubrique : supprimez des éléments avant d'en ajouter.",
}
ERREUR_PAR_STATUT = {400: "requete_invalide", 401: "cle_refusee", 403: "non_accorde",
                     404: "introuvable", 413: "trop_gros", 429: "limite"}
MESSAGES_TAILSCALE = {
    "Running": ("Tailscale est connecté, mais la passerelle ne répond pas : votre accès réseau n'est "
                "peut-être pas encore ouvert (adhésion en attente d'approbation), ou le service est "
                "momentanément arrêté. Si Tailscale est connecté à un autre compte, basculez sur le compte "
                "invité (menu Tailscale), puis attendez l'approbation."),
    "NeedsMachineAuth": ("Cet appareil attend l'approbation de l'administrateur du réseau Tailscale "
                         "(adhésion pas encore approuvée). Réessayez une fois l'approbation reçue. Si Tailscale "
                         "est connecté à un autre compte, basculez d'abord sur le compte invité."),
    "NeedsLogin": ("Tailscale n'est pas connecté : ouvrez Tailscale et connectez-vous avec le compte "
                   "qui a reçu l'invitation."),
    "Stopped": "Tailscale est déconnecté : ouvrez Tailscale et activez la connexion (« Connect »).",
    "Starting": "Tailscale démarre : patientez quelques secondes, puis réessayez.",
}

JOURNAL = logging.getLogger("cockpit")


# --------------------------------------------------------------------------- outils
def chemin_config_defaut() -> str:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
        return os.path.join(base, "Cockpit", "config.json")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Cockpit", "config.json")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "cockpit", "config.json")


def cle_valide(cle) -> bool:
    return isinstance(cle, str) and bool(RX_CLE.fullmatch(cle))


def nettoyer_cle(valeur) -> str:
    """Retire espaces, tabulations et retours à la ligne d'une clé collée."""
    if not isinstance(valeur, str):
        return ""
    return re.sub(r"\s+", "", valeur)[:400]


def passerelle_valide(url) -> bool:
    if not isinstance(url, str) or not 8 <= len(url) <= 200:
        return False
    try:
        u = urlsplit(url)
        u.port  # lève ValueError si le port est invalide
    except ValueError:
        return False
    return (u.scheme in ("http", "https") and bool(u.hostname) and not u.username and not u.password
            and u.path in ("", "/") and not u.query and not u.fragment)


def lire_config(chemin: str):
    """Retourne ({'passerelle': url}, None) ou (None, code_erreur). La clé n'est plus un réglage."""
    try:
        with open(chemin, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
    except FileNotFoundError:
        return None, "non_configure"
    except (OSError, ValueError):
        return None, "config_invalide"
    if not isinstance(d, dict) or not passerelle_valide(d.get("passerelle")):
        return None, "config_invalide"
    return {"passerelle": d["passerelle"].rstrip("/")}, None


def purger_cle_config(chemin: str) -> bool:
    """Retire une clé laissée dans le fichier par une version précédente (et l'ignore)."""
    try:
        with open(chemin, "r", encoding="utf-8-sig") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return False
    if not isinstance(d, dict) or "cle" not in d:
        return False
    d.pop("cle", None)
    temporaire = chemin + ".tmp"
    try:
        if os.path.exists(temporaire):
            os.remove(temporaire)
        fd = os.open(temporaire, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=True, indent=2)
            f.write("\n")
        os.replace(temporaire, chemin)
        if os.name != "nt":
            os.chmod(chemin, 0o600)
    except OSError:
        return False
    JOURNAL.info("clé retirée du fichier de configuration : elle n'est plus enregistrée sur ce disque")
    return True


def masquer(texte: str, cle: str) -> str:
    if cle:
        for forme in {cle, json.dumps(cle)[1:-1]}:
            if forme and forme in texte:
                texte = texte.replace(forme, "[masqué]")
    return texte


def date_locale(iso) -> str:
    if not isinstance(iso, str) or not iso:
        return ""
    try:
        d = datetime.fromisoformat(iso.strip().replace("Z", "+00:00"))
    except ValueError:
        return iso
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone().strftime("%d/%m/%Y %H:%M")


def maintenant_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- session (clé en mémoire)
class Session:
    """Clé d'accès en MÉMOIRE seulement + jeton de session opaque pour le navigateur.

    Rien n'est écrit sur le disque : à l'arrêt du processus, la clé disparaît et le
    cockpit se reverrouille. Le navigateur ne reçoit que le jeton (cookie HttpOnly).
    """

    def __init__(self, minutes=MINUTES_VERROU_DEFAUT):
        self.verrou = threading.Lock()
        self.minutes = max(0.05, float(minutes))   # 0,05 min = 3 s (tests) ; 30 min en service
        self._cle = None
        self._jeton = None
        self.ouvert_le = None
        self.dernier = 0.0
        self.motif = None          # pourquoi le cockpit est verrouillé (message pour l'écran de verrou)
        self.ouvertures = 0

    # ---- lecture
    @property
    def ouverte(self) -> bool:
        with self.verrou:
            return self._cle is not None

    def etat(self) -> dict:
        with self.verrou:
            if self._cle is None:
                return {"verrouille": True, "motif": self.motif}
            reste = int(max(0.0, self.minutes * 60 - (time.monotonic() - self.dernier)))
            return {"verrouille": False, "ouvert_le": self.ouvert_le, "verrou_dans_s": reste,
                    "minutes_verrou": self.minutes}

    # ---- écriture
    def ouvrir(self, cle: str) -> str:
        with self.verrou:
            self._cle = cle
            self._jeton = secrets.token_urlsafe(32)      # 256 bits d'aléa
            self.ouvert_le = maintenant_iso()
            self.dernier = time.monotonic()
            self.motif = None
            self.ouvertures += 1
            JOURNAL.info("cockpit déverrouillé (session %d)", self.ouvertures)
            return self._jeton

    def fermer(self, motif="verrou_demande") -> bool:
        with self.verrou:
            if self._cle is None:
                self.motif = self.motif or motif
                return False
            self._cle = None
            self._jeton = None
            self.ouvert_le = None
            self.motif = motif
            JOURNAL.info("cockpit verrouillé (%s)", motif)
            return True

    def verifier_inactivite(self) -> bool:
        with self.verrou:
            inactif = self._cle is not None and (time.monotonic() - self.dernier) > self.minutes * 60
        return self.fermer("verrou_inactivite") if inactif else False

    def cle(self, jeton, toucher=True):
        """Clé si le jeton présenté est celui de la session ouverte, sinon None."""
        if not isinstance(jeton, str) or not RX_JETON.fullmatch(jeton):
            return None
        self.verifier_inactivite()
        with self.verrou:
            if self._jeton is None or not hmac.compare_digest(jeton, self._jeton):
                return None
            if toucher:
                self.dernier = time.monotonic()
            return self._cle

    def cle_interne(self):
        """Clé pour les tâches de fond (télémétrie) : ne prolonge pas la session."""
        self.verifier_inactivite()
        with self.verrou:
            return self._cle


# --------------------------------------------------------------------------- espace personnel (local)
class ErreurEspace(Exception):
    """Problème de lecture ou d'écriture du fichier local (code de MESSAGES)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class ChampInvalide(Exception):
    """Donnée refusée : le message est destiné à l'utilisateur."""


RX_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def texte_propre(valeur, maxi, libelle, multiligne=False, obligatoire=False) -> str:
    if valeur is None:
        valeur = ""
    if not isinstance(valeur, str):
        raise ChampInvalide("Le champ « %s » doit être du texte." % libelle)
    valeur = RX_CONTROLE.sub("", valeur.replace("\r\n", "\n").replace("\r", "\n"))
    if not multiligne:
        valeur = valeur.replace("\n", " ").replace("\t", " ")
    valeur = valeur.strip()
    if len(valeur) > maxi:
        raise ChampInvalide("Le champ « %s » est trop long (%d caractères au maximum)." % (libelle, maxi))
    if obligatoire and not valeur:
        raise ChampInvalide("Le champ « %s » est obligatoire." % libelle)
    return valeur


def jour_propre(valeur, libelle, obligatoire=False) -> str:
    valeur = texte_propre(valeur, 10, libelle, obligatoire=obligatoire)
    if not valeur:
        return ""
    if not RX_JOUR.fullmatch(valeur):
        raise ChampInvalide("Le champ « %s » attend une date au format AAAA-MM-JJ." % libelle)
    try:
        date(*(int(x) for x in valeur.split("-")))
    except ValueError:
        raise ChampInvalide("Le champ « %s » n'est pas une date qui existe." % libelle)
    return valeur


def heure_propre(valeur, libelle, obligatoire=False) -> str:
    valeur = texte_propre(valeur, 5, libelle, obligatoire=obligatoire)
    if valeur and not RX_HEURE.fullmatch(valeur):
        raise ChampInvalide("Le champ « %s » attend une heure au format HH:MM." % libelle)
    return valeur


def booleen(valeur, libelle, defaut=False) -> bool:
    if valeur is None:
        return defaut
    if not isinstance(valeur, bool):
        raise ChampInvalide("Le champ « %s » attend oui ou non." % libelle)
    return valeur


class Espace:
    """Tâches, créneaux et notes de l'utilisateur, dans un seul fichier JSON local (600).

    Aucune de ces données ne part sur le réseau : les routes /api/taches, /api/creneaux,
    /api/notes et /api/espace sont traitées ici, jamais relayées à la passerelle.
    """

    RUBRIQUES = ("taches", "creneaux", "notes")

    def __init__(self, chemin: str):
        self.chemin = chemin
        self.verrou = threading.RLock()
        self.avertissement = None

    # ---- fichier
    @staticmethod
    def vide() -> dict:
        return {"version": 1, "taches": [], "creneaux": [], "notes": []}

    def _mettre_de_cote(self):
        try:
            os.replace(self.chemin, self.chemin + ".abime-" + time.strftime("%Y%m%d-%H%M%S"))
        except OSError:
            pass
        self.avertissement = "donnees_illisibles"
        JOURNAL.info("espace : fichier de données illisible, mis de côté")

    def charger(self) -> dict:
        try:
            with open(self.chemin, "r", encoding="utf-8-sig") as f:
                brut = f.read(TAILLE_MAX_DONNEES + 1)
        except FileNotFoundError:
            return self.vide()
        except OSError:
            raise ErreurEspace("donnees_ecriture")
        if len(brut) > TAILLE_MAX_DONNEES:
            self._mettre_de_cote()
            return self.vide()
        try:
            d = json.loads(brut) if brut.strip() else self.vide()
        except ValueError:
            self._mettre_de_cote()
            return self.vide()
        if not isinstance(d, dict) or not all(isinstance(d.get(r, []), list) for r in self.RUBRIQUES):
            self._mettre_de_cote()
            return self.vide()
        propre = self.vide()
        for rubrique in self.RUBRIQUES:
            vus = set()
            for element in d.get(rubrique) or []:
                if not isinstance(element, dict):
                    continue
                try:
                    net = NORMALISEURS[rubrique](element, None)
                except ChampInvalide:
                    continue
                identifiant = element.get("id")
                if not (isinstance(identifiant, str) and RX_ID.fullmatch(identifiant)) or identifiant in vus:
                    identifiant = secrets.token_hex(8)
                vus.add(identifiant)
                net["id"] = identifiant
                for champ in ("cree_le", "maj_le"):
                    valeur = element.get(champ)
                    net[champ] = valeur if (isinstance(valeur, str) and len(valeur) <= 40) else maintenant_iso()
                propre[rubrique].append(net)
            del propre[rubrique][MAX_PAR_RUBRIQUE[rubrique]:]
        return propre

    def enregistrer(self, donnees: dict):
        dossier = os.path.dirname(os.path.abspath(self.chemin))
        temporaire = self.chemin + ".tmp"
        texte = json.dumps(donnees, ensure_ascii=False, indent=1)
        if len(texte.encode("utf-8")) > TAILLE_MAX_DONNEES:
            raise ErreurEspace("espace_plein")
        try:
            os.makedirs(dossier, exist_ok=True)
            if os.name != "nt":
                os.chmod(dossier, 0o700)
            if os.path.exists(temporaire):
                os.remove(temporaire)
            fd = os.open(temporaire, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(texte + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporaire, self.chemin)
            if os.name != "nt":
                os.chmod(self.chemin, 0o600)
        except OSError:
            raise ErreurEspace("donnees_ecriture")

    # ---- opérations (tout sous verrou : lecture, modification, écriture)
    def tout(self) -> dict:
        with self.verrou:
            d = self.charger()
        return {"taches": d["taches"], "creneaux": d["creneaux"], "notes": d["notes"],
                "limites": dict(MAX_PAR_RUBRIQUE), "avertissement": self.avertissement}

    def creer(self, rubrique: str, corps: dict) -> dict:
        with self.verrou:
            d = self.charger()
            if len(d[rubrique]) >= MAX_PAR_RUBRIQUE[rubrique]:
                raise ErreurEspace("espace_plein")
            element = NORMALISEURS[rubrique](corps, None)
            element["id"] = secrets.token_hex(8)
            element["cree_le"] = element["maj_le"] = maintenant_iso()
            d[rubrique].append(element)
            self.enregistrer(d)
        return element

    def modifier(self, rubrique: str, corps: dict):
        identifiant = corps.get("id")
        if not isinstance(identifiant, str) or not RX_ID.fullmatch(identifiant):
            raise ChampInvalide("Identifiant d'élément invalide.")
        with self.verrou:
            d = self.charger()
            for i, ancien in enumerate(d[rubrique]):
                if ancien.get("id") == identifiant:
                    element = NORMALISEURS[rubrique](corps, ancien)
                    element["id"] = identifiant
                    element["cree_le"] = ancien.get("cree_le") or maintenant_iso()
                    element["maj_le"] = maintenant_iso()
                    d[rubrique][i] = element
                    self.enregistrer(d)
                    return element
        return None

    def supprimer(self, rubrique: str, corps: dict) -> bool:
        identifiant = corps.get("id")
        if not isinstance(identifiant, str) or not RX_ID.fullmatch(identifiant):
            raise ChampInvalide("Identifiant d'élément invalide.")
        with self.verrou:
            d = self.charger()
            restants = [e for e in d[rubrique] if e.get("id") != identifiant]
            if len(restants) == len(d[rubrique]):
                return False
            d[rubrique] = restants
            self.enregistrer(d)
        return True

    def exporter(self) -> dict:
        with self.verrou:
            d = self.charger()
        return {"format": "cockpit-espace", "version": 1, "exporte_le": maintenant_iso(),
                "taches": d["taches"], "creneaux": d["creneaux"], "notes": d["notes"]}

    def importer(self, corps: dict) -> dict:
        source = corps.get("donnees")
        if isinstance(source, str):
            try:
                source = json.loads(source)
            except ValueError:
                raise ChampInvalide("Le fichier choisi n'est pas un export JSON lisible.")
        if not isinstance(source, dict):
            raise ChampInvalide("Le fichier choisi n'est pas un export de ce cockpit.")
        if not any(isinstance(source.get(r), list) for r in self.RUBRIQUES):
            raise ChampInvalide("Le fichier choisi ne contient ni tâche, ni créneau, ni note.")
        mode = corps.get("mode", "fusionner")
        if mode not in ("fusionner", "remplacer"):
            raise ChampInvalide("Mode d'import inconnu (fusionner ou remplacer).")
        ajoutes = {r: 0 for r in self.RUBRIQUES}
        ignores = 0
        with self.verrou:
            d = self.vide() if mode == "remplacer" else self.charger()
            for rubrique in self.RUBRIQUES:
                connus = {e.get("id") for e in d[rubrique]}
                for element in (source.get(rubrique) or []):
                    if len(d[rubrique]) >= MAX_PAR_RUBRIQUE[rubrique]:
                        ignores += 1
                        continue
                    try:
                        propre = NORMALISEURS[rubrique](element if isinstance(element, dict) else {}, None)
                    except ChampInvalide:
                        ignores += 1
                        continue
                    identifiant = element.get("id") if isinstance(element, dict) else None
                    if not (isinstance(identifiant, str) and RX_ID.fullmatch(identifiant)) or identifiant in connus:
                        identifiant = secrets.token_hex(8)
                    connus.add(identifiant)
                    propre["id"] = identifiant
                    propre["cree_le"] = (element.get("cree_le") if isinstance(element.get("cree_le"), str)
                                         else None) or maintenant_iso()
                    propre["maj_le"] = maintenant_iso()
                    d[rubrique].append(propre)
                    ajoutes[rubrique] += 1
            self.enregistrer(d)
        return {"ok": True, "mode": mode, "importes": ajoutes, "ignores": ignores}


def normaliser_tache(corps: dict, ancien) -> dict:
    base = ancien or {}
    donne = (lambda nom: corps[nom] if nom in corps else base.get(nom))
    priorite = texte_propre(donne("priorite") or "normale", 20, "priorité")
    if priorite not in PRIORITES:
        raise ChampInvalide("Priorité inconnue (basse, normale ou haute).")
    faite = booleen(donne("faite"), "faite", bool(base.get("faite")))
    tache = {
        "titre": texte_propre(donne("titre"), MAX_TITRE, "titre", obligatoire=True),
        "detail": texte_propre(donne("detail"), MAX_DETAIL, "détail", multiligne=True),
        "priorite": priorite,
        "echeance": jour_propre(donne("echeance"), "échéance"),
        "heure": heure_propre(donne("heure"), "heure"),
        "faite": faite,
        "fait_le": "",
    }
    if faite:
        precedent = texte_propre(donne("fait_le"), 40, "date de réalisation")
        tache["fait_le"] = precedent or maintenant_iso()
    if tache["heure"] and not tache["echeance"]:
        raise ChampInvalide("Une heure demande aussi une date d'échéance.")
    return tache


def normaliser_creneau(corps: dict, ancien) -> dict:
    base = ancien or {}
    donne = (lambda nom: corps[nom] if nom in corps else base.get(nom))
    creneau = {
        "titre": texte_propre(donne("titre"), MAX_TITRE, "titre", obligatoire=True),
        "jour": jour_propre(donne("jour"), "jour", obligatoire=True),
        "debut": heure_propre(donne("debut"), "début", obligatoire=True),
        "fin": heure_propre(donne("fin"), "fin"),
        "lieu": texte_propre(donne("lieu"), MAX_LIEU, "lieu"),
        "detail": texte_propre(donne("detail"), MAX_DETAIL, "détail", multiligne=True),
    }
    if creneau["fin"] and creneau["fin"] <= creneau["debut"]:
        raise ChampInvalide("L'heure de fin doit suivre l'heure de début.")
    return creneau


def normaliser_note(corps: dict, ancien) -> dict:
    base = ancien or {}
    donne = (lambda nom: corps[nom] if nom in corps else base.get(nom))
    return {
        "titre": texte_propre(donne("titre"), MAX_TITRE, "titre", obligatoire=True),
        "texte": texte_propre(donne("texte"), MAX_NOTE, "texte", multiligne=True),
        "epingle": booleen(donne("epingle"), "épinglée", bool(base.get("epingle"))),
    }


NORMALISEURS = {"taches": normaliser_tache, "creneaux": normaliser_creneau, "notes": normaliser_note}


# --------------------------------------------------------------------------- passerelle
class ErreurAmont(Exception):
    def __init__(self, code: str, detail: str = ""):
        super().__init__(code)
        self.code = code
        self.detail = detail


def appeler(cfg, methode, chemin, corps=None, delai=DELAI_LECTURE, auth=True, delai_connexion=DELAI_CONNEXION):
    """Requête vers la passerelle : ni proxy système, ni redirection suivie. Retourne (statut, octets)."""
    u = urlsplit(cfg["passerelle"])
    securise = u.scheme == "https"
    port = u.port or (443 if securise else 80)
    if securise:
        conn = http.client.HTTPSConnection(u.hostname, port, timeout=delai_connexion,
                                           context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(u.hostname, port, timeout=delai_connexion)
    entetes = {"Accept": "application/json", "User-Agent": "cockpit-client/" + VERSION, "Connection": "close"}
    if auth:
        entetes["Authorization"] = "Bearer " + cfg["cle"]
    donnees = None
    if corps is not None:
        donnees = json.dumps(corps, ensure_ascii=False).encode("utf-8")
        entetes["Content-Type"] = "application/json; charset=utf-8"
    phase = "connexion"
    try:
        conn.connect()
        conn.sock.settimeout(delai)
        phase = "attente"
        conn.request(methode, chemin, body=donnees, headers=entetes)
        rep = conn.getresponse()
        brut = rep.read(TAILLE_MAX_REPONSE + 1)
        statut = rep.status
    except TimeoutError:
        raise ErreurAmont("passerelle_injoignable" if phase == "connexion" else "delai_depasse", "timeout")
    except ConnectionRefusedError:
        raise ErreurAmont("passerelle_indisponible", "ECONNREFUSED")
    except ssl.SSLError:
        raise ErreurAmont("passerelle_erreur", "tls")
    except (http.client.HTTPException, ConnectionError) as e:
        raise ErreurAmont("passerelle_erreur" if phase == "attente" else "passerelle_injoignable", type(e).__name__)
    except OSError as e:
        raise ErreurAmont("passerelle_injoignable", errno.errorcode.get(e.errno or 0, type(e).__name__))
    finally:
        conn.close()
    if len(brut) > TAILLE_MAX_REPONSE:
        raise ErreurAmont("passerelle_erreur", "reponse_trop_grande")
    return statut, brut


def message_reseau(e: ErreurAmont) -> str:
    msg = MESSAGES.get(e.code, MESSAGES["passerelle_erreur"])
    if e.code == "passerelle_injoignable" and sys.platform == "darwin" and e.detail == "EHOSTUNREACH":
        msg += (" Sur macOS 15 ou plus récent, autorisez aussi « python3 » dans Réglages Système > "
                "Confidentialité et sécurité > Réseau local.")
    return msg


def relayer(cfg, methode, chemin, corps=None, delai=DELAI_LECTURE, session=None):
    """Appelle la passerelle et retourne (statut_http, objet_json) prêt pour le navigateur."""
    try:
        statut, brut = appeler(cfg, methode, chemin, corps, delai)
    except ErreurAmont as e:
        return (504 if e.code == "delai_depasse" else 502), {"erreur": e.code, "message": message_reseau(e)}
    try:
        donnees = json.loads(brut.decode("utf-8")) if brut else None
    except (UnicodeDecodeError, ValueError):
        donnees = None
    if statut >= 500:
        d = donnees if isinstance(donnees, dict) else {}
        message = d.get("message") if isinstance(d.get("message"), str) else MESSAGES["passerelle_erreur"]
        return (503 if statut == 503 else 502), {"erreur": "passerelle_erreur", "message": message}
    if (300 <= statut < 400) or (statut < 400 and donnees is None):
        return 502, {"erreur": "passerelle_erreur", "message": MESSAGES["passerelle_erreur"]}
    if statut >= 400:
        d = donnees if isinstance(donnees, dict) else {}
        code = d.get("erreur") if isinstance(d.get("erreur"), str) else ERREUR_PAR_STATUT.get(statut, "requete_invalide")
        # La passerelle a tranché : l'accès ne vaut plus rien -> la clé quitte la mémoire.
        if session is not None and code in ("acces_expire", "acces_suspendu", "cle_invalide", "cle_refusee"):
            session.fermer(code)
        message = d.get("message") if isinstance(d.get("message"), str) else MESSAGES.get(code, MESSAGES["requete_invalide"])
        d = dict(d)
        d["erreur"], d["message"] = code, message
        return statut, d
    return statut, donnees


# --------------------------------------------------------------------------- Tailscale
def diagnostic_tailscale():
    """Retourne (etat, message) sans jamais modifier Tailscale (lecture de « status » seulement)."""
    candidats = []
    if sys.platform == "darwin":
        candidats.append("/Applications/Tailscale.app/Contents/MacOS/Tailscale")
    elif os.name == "nt":
        for var in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            if os.environ.get(var):
                candidats.append(os.path.join(os.environ[var], "Tailscale", "tailscale.exe"))
    trouve = shutil.which("tailscale")
    if trouve:
        candidats.append(trouve)
    options = {"creationflags": 0x08000000} if os.name == "nt" else {}
    for exe in candidats:
        if not os.path.isfile(exe):
            continue
        try:
            r = subprocess.run([exe, "status", "--json"], capture_output=True, text=True, timeout=5,
                               stdin=subprocess.DEVNULL, **options)
        except (OSError, subprocess.SubprocessError):
            continue
        try:
            d = json.loads(r.stdout)
        except ValueError:
            d = None
        etat = d.get("BackendState") if isinstance(d, dict) else None
        if isinstance(etat, str) and etat:
            return etat, MESSAGES_TAILSCALE.get(etat, "État de Tailscale : %s." % etat)
        if "stopped" in (r.stdout + r.stderr).lower():
            return "Stopped", MESSAGES_TAILSCALE["Stopped"]
    if any(os.path.exists(c) for c in candidats) or os.path.isdir("/Applications/Tailscale.app"):
        return "inconnu", "Vérifiez que Tailscale est ouvert et connecté."
    return "absent", ("Tailscale ne semble pas installé sur cet ordinateur : installez-le "
                      "(https://tailscale.com/download), puis connectez-vous avec l'invitation reçue.")


# --------------------------------------------------------------------------- télémétrie
def _cmd(args, delai=5) -> str:
    options = {"creationflags": 0x08000000} if os.name == "nt" else {}
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=delai, stdin=subprocess.DEVNULL, **options)
    except (OSError, subprocess.SubprocessError):
        return ""
    return r.stdout if r.returncode == 0 else ""


def _entier(texte):
    try:
        return int(str(texte).strip())
    except (TypeError, ValueError):
        return None


def _disque(chemin):
    try:
        u = shutil.disk_usage(chemin)
        return round(u.total / 1e9, 1), round(u.free / 1e9, 1)
    except OSError:
        return None, None


def mesures_macos() -> dict:
    m = {"os": "macOS", "version_os": platform.mac_ver()[0] or platform.release()}
    m["cpu_modele"] = _cmd(["sysctl", "-n", "machdep.cpu.brand_string"]).strip() or platform.machine() or None
    memoire = _entier(_cmd(["sysctl", "-n", "hw.memsize"]))
    m["ram_total_mo"] = memoire // 1048576 if memoire else None
    vm = _cmd(["vm_stat"])
    taille = re.search(r"page size of (\d+) bytes", vm)
    pages = {}
    for nom, motif in (("actives", r"Pages active:\s+(\d+)"), ("cablees", r"Pages wired down:\s+(\d+)"),
                       ("compressees", r"Pages occupied by compressor:\s+(\d+)")):
        t = re.search(motif, vm)
        pages[nom] = int(t.group(1)) if t else 0
    m["ram_utilisee_mo"] = (sum(pages.values()) * int(taille.group(1)) // 1048576) if taille and any(pages.values()) else None
    demarrage = re.search(r"sec = (\d+)", _cmd(["sysctl", "-n", "kern.boottime"]))
    m["uptime_s"] = max(0, int(time.time()) - int(demarrage.group(1))) if demarrage else None
    cpu = None
    lignes = re.findall(r"CPU usage:.*?([\d.]+)% idle", _cmd(["top", "-l", "2", "-n", "0", "-s", "1"], delai=10))
    if lignes:
        cpu = round(max(0.0, 100.0 - float(lignes[-1])), 1)
    elif hasattr(os, "getloadavg"):
        cpu = round(min(100.0, os.getloadavg()[0] / (os.cpu_count() or 1) * 100), 1)
    m["cpu_pct"] = cpu
    m["disque_total_go"], m["disque_libre_go"] = _disque(os.path.expanduser("~"))
    return m


def mesures_windows() -> dict:
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [("dwLength", wintypes.DWORD), ("dwMemoryLoad", wintypes.DWORD),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    m = {"os": "Windows"}
    version, construction = platform.version(), None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            def lire(nom):
                try:
                    return winreg.QueryValueEx(k, nom)[0]
                except OSError:
                    return None
            construction = _entier(lire("CurrentBuildNumber") or lire("CurrentBuild"))
            affichage = lire("DisplayVersion") or lire("ReleaseId")
        nom_version = "11" if construction and construction >= 22000 else (platform.release() or "?")
        version = "%s%s (build %s)" % (nom_version, " " + affichage if affichage else "", construction or "?")
    except (OSError, ImportError):
        pass
    m["version_os"] = version
    modele = None
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as k:
            modele = str(winreg.QueryValueEx(k, "ProcessorNameString")[0]).strip()
    except (OSError, ImportError):
        pass
    m["cpu_modele"] = modele or platform.processor() or None
    st = MEMORYSTATUSEX()
    st.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if k32.GlobalMemoryStatusEx(ctypes.byref(st)):
        m["ram_total_mo"] = st.ullTotalPhys // 1048576
        m["ram_utilisee_mo"] = (st.ullTotalPhys - st.ullAvailPhys) // 1048576
    else:
        m["ram_total_mo"] = m["ram_utilisee_mo"] = None
    k32.GetTickCount64.restype = ctypes.c_ulonglong
    m["uptime_s"] = int(k32.GetTickCount64() // 1000)

    def temps():
        inactif, noyau, utilisateur = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
        if not k32.GetSystemTimes(ctypes.byref(inactif), ctypes.byref(noyau), ctypes.byref(utilisateur)):
            return None
        return [(f.dwHighDateTime << 32) | f.dwLowDateTime for f in (inactif, noyau, utilisateur)]

    a = temps()
    time.sleep(1)
    b = temps()
    cpu = None
    if a and b:
        total = (b[1] - a[1]) + (b[2] - a[2])  # le temps noyau inclut le temps inactif
        if total > 0:
            cpu = round(max(0.0, min(100.0, 100.0 * (1 - (b[0] - a[0]) / total))), 1)
    m["cpu_pct"] = cpu
    m["disque_total_go"], m["disque_libre_go"] = _disque((os.environ.get("SystemDrive") or "C:") + "\\")
    return m


def mesures_linux() -> dict:
    m = {"os": "Linux"}
    try:
        m["version_os"] = platform.freedesktop_os_release().get("PRETTY_NAME") or platform.release()
    except (OSError, AttributeError):
        m["version_os"] = platform.release()
    modele = None
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
            for ligne in f:
                if ligne.lower().startswith("model name"):
                    modele = ligne.split(":", 1)[1].strip()
                    break
    except OSError:
        pass
    m["cpu_modele"] = modele or platform.processor() or platform.machine() or None
    info = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as f:
            for ligne in f:
                cle, _, valeur = ligne.partition(":")
                info[cle] = _entier(valeur.split()[0]) if valeur.split() else None
    except OSError:
        pass
    total, dispo = info.get("MemTotal"), info.get("MemAvailable")
    m["ram_total_mo"] = total // 1024 if total else None
    m["ram_utilisee_mo"] = (total - dispo) // 1024 if total and dispo is not None else None
    try:
        with open("/proc/uptime", encoding="utf-8") as f:
            m["uptime_s"] = int(float(f.read().split()[0]))
    except (OSError, ValueError, IndexError):
        m["uptime_s"] = None

    def temps():
        try:
            with open("/proc/stat", encoding="utf-8") as f:
                v = [int(x) for x in f.readline().split()[1:]]
            return v[3] + (v[4] if len(v) > 4 else 0), sum(v)
        except (OSError, ValueError, IndexError):
            return None

    a = temps()
    time.sleep(1)
    b = temps()
    m["cpu_pct"] = (round(100.0 * (1 - (b[0] - a[0]) / (b[1] - a[1])), 1)
                    if a and b and b[1] > a[1] else None)
    m["disque_total_go"], m["disque_libre_go"] = _disque("/")
    return m


def mesures() -> dict:
    try:
        if os.name == "nt":
            m = mesures_windows()
        elif sys.platform == "darwin":
            m = mesures_macos()
        else:
            m = mesures_linux()
    except Exception as e:  # la télémétrie ne doit jamais arrêter le cockpit
        JOURNAL.info("télémétrie : mesure impossible (%s)", type(e).__name__)
        m = {"os": platform.system() or None}
    m["coeurs"] = os.cpu_count()
    m["version_client"] = VERSION
    champs = ("os", "version_os", "cpu_modele", "coeurs", "cpu_pct", "ram_total_mo", "ram_utilisee_mo",
              "disque_total_go", "disque_libre_go", "uptime_s", "version_client")
    return {c: m.get(c) for c in champs}


def conseils_machine(m: dict) -> list:
    """Conseils lus sur les mesures de CET ordinateur. Calculés ici, affichés ici : rien ne sort.

    Ton volontairement neutre : c'est l'ordinateur personnel de l'utilisateur, pas le nôtre.
    """
    conseils = []
    appareil = {"macOS": "votre Mac", "Windows": "votre PC"}.get(m.get("os") or "", "votre ordinateur")
    total, libre = m.get("disque_total_go"), m.get("disque_libre_go")
    if total and libre is not None and total > 0:
        part = libre / total
        if part < 0.10:
            conseils.append({"sujet": "disque", "niveau": "attention", "texte":
                "Votre disque est presque plein (%s Go libres sur %s Go) : cela peut ralentir %s. "
                "Vider la corbeille et le dossier Téléchargements libère souvent beaucoup de place."
                % (_nombre(libre), _nombre(total), appareil),
                "tache": {"titre": "Libérer de la place sur le disque", "priorite": "haute", "detail":
                          "Vider la corbeille et le dossier Téléchargements, puis repérer les gros fichiers "
                          "devenus inutiles. Objectif : garder au moins 10 % du disque libre."}})
        elif part < 0.20:
            conseils.append({"sujet": "disque", "niveau": "info", "texte":
                "L'espace disque commence à se réduire (%s Go libres sur %s Go)." % (_nombre(libre), _nombre(total)),
                "tache": {"titre": "Faire un peu de place sur le disque", "priorite": "normale", "detail":
                          "Vider la corbeille et trier le dossier Téléchargements."}})
    ram_t, ram_u = m.get("ram_total_mo"), m.get("ram_utilisee_mo")
    if ram_t and ram_u and ram_u / ram_t > 0.90:
        conseils.append({"sujet": "memoire", "niveau": "info", "texte":
            "La mémoire est très sollicitée en ce moment : fermer les applications que vous n'utilisez pas "
            "rendra %s plus réactif." % appareil,
            "tache": {"titre": "Fermer les applications inutilisées", "priorite": "normale", "detail":
                      "Repérer les applications ouvertes en permanence et fermer celles qui ne servent pas."}})
    if ram_t:
        go = ram_t / 1024
        if go < 12:
            taille = "de 1 à 4 milliards de paramètres"
        elif go < 20:
            taille = "de 7 à 8 milliards de paramètres"
        elif go < 40:
            taille = "de 7 à 14 milliards de paramètres"
        elif go < 80:
            taille = "jusqu'à 32 milliards de paramètres"
        else:
            taille = "jusqu'à 70 milliards de paramètres"
        unifiee = " (mémoire unifiée, partagée avec la puce graphique)" if str(m.get("cpu_modele") or "").startswith("Apple") else ""
        conseils.append({"sujet": "ia-locale", "niveau": "info", "texte":
            "Avec %s Go de mémoire%s, un modèle d'IA local %s, quantifié en 4 bits, tourne confortablement "
            "sur %s." % (_nombre(round(go)), unifiee, taille, appareil),
            "tache": {"titre": "Essayer un modèle d'IA local %s" % taille, "priorite": "basse", "detail":
                      "Demander à l'assistant conversationnel du cockpit comment installer et lancer un modèle "
                      "%s sur %s (quantification 4 bits)." % (taille, appareil)}})
    uptime = m.get("uptime_s")
    if uptime and uptime > 14 * 86400:
        conseils.append({"sujet": "redemarrage", "niveau": "info", "texte":
            "%s n'a pas redémarré depuis %d jours : un redémarrage de temps en temps libère de la mémoire "
            "et applique les mises à jour." % (appareil[0].upper() + appareil[1:], uptime // 86400),
            "tache": {"titre": "Redémarrer %s" % appareil, "priorite": "normale", "detail":
                      "Enregistrer son travail puis redémarrer : cela libère la mémoire et applique les mises à jour."}})
    return conseils


def _nombre(v) -> str:
    """12.5 -> « 12,5 » ; 24.0 -> « 24 » (affichage français)."""
    try:
        v = float(v)
    except (TypeError, ValueError):
        return "?"
    return ("%d" % v) if v == int(v) else ("%.1f" % v).replace(".", ",")


class Telemetrie(threading.Thread):
    """Envoie l'état technique de la machine, SEULEMENT quand le cockpit est déverrouillé.

    Cockpit verrouillé (clé absente de la mémoire) = aucun envoi, donc rien ne sort.
    """

    def __init__(self, chemin_config: str, intervalle: float, session: Session):
        super().__init__(name="telemetrie", daemon=True)
        self.chemin_config = chemin_config
        self.intervalle = max(1.0, float(intervalle))
        self.session = session
        self.arret = threading.Event()
        self.envois = 0

    def run(self):
        dernier = None
        self.arret.wait(min(5.0, self.intervalle))
        while not self.arret.is_set():
            cfg, _ = lire_config(self.chemin_config)
            cle = self.session.cle_interne()
            etat = "non configuré" if cfg is None else "cockpit verrouillé"
            attente = self.intervalle
            if cfg and cle:
                cfg = {"passerelle": cfg["passerelle"], "cle": cle}
                try:
                    statut, brut = appeler(cfg, "POST", "/v1/telemetrie", mesures(), delai=DELAI_TELEMETRIE)
                    if statut in (401, 403):
                        try:
                            code = json.loads(brut.decode("utf-8")).get("erreur")
                        except (ValueError, UnicodeDecodeError, AttributeError):
                            code = None
                        self.session.fermer(code if code in MESSAGES else "cle_invalide")
                        JOURNAL.info("télémétrie : accès refusé (%s), cockpit verrouillé", code or "?")
                        etat = "verrouillé"
                        self.arret.wait(attente)
                        continue
                    etat = "ok" if 200 <= statut < 300 else "HTTP %d" % statut
                    if etat == "ok":
                        self.envois += 1
                        try:  # la passerelle peut demander d'espacer les envois (jamais de les rapprocher)
                            prochain = float(json.loads(brut.decode("utf-8")).get("prochain_envoi_s") or 0)
                        except (ValueError, UnicodeDecodeError, AttributeError, TypeError):
                            prochain = 0
                        attente = min(3600.0, max(self.intervalle, prochain))
                except ErreurAmont as e:
                    etat = e.code
            if etat != dernier:
                JOURNAL.info("télémétrie : %s", etat)
                dernier = etat
            self.arret.wait(attente)


# --------------------------------------------------------------------------- serveur HTTP
def valider_texte(v, maxi, mini=0):
    return isinstance(v, str) and mini <= len(v.strip()) and len(v) <= maxi


class Gestionnaire(BaseHTTPRequestHandler):
    server_version = "cockpit"
    sys_version = ""
    timeout = 30

    # ---- journal : méthode, chemin, statut, durée ; jamais d'en-tête ni de corps
    def setup(self):
        super().setup()
        self._debut = time.monotonic()

    def version_string(self):
        return "cockpit"

    def log_message(self, format, *args):  # noqa: A002 - signature imposée
        pass

    def log_error(self, format, *args):  # noqa: A002
        pass

    def log_request(self, code="-", size="-"):
        chemin = urlsplit(getattr(self, "path", "") or "").path or "-"
        chemin = re.sub(r"[^\x21-\x7e]", "?", chemin)[:80]
        JOURNAL.info("%s %s %s %d ms", getattr(self, "command", None) or "-", chemin, code,
                     int((time.monotonic() - self._debut) * 1000))

    # ---- réponses
    def envoyer(self, code, corps: bytes, type_contenu: str, entetes=()):
        try:
            self.send_response(code)
            self.send_header("Content-Type", type_contenu)
            self.send_header("Content-Length", str(len(corps)))
            for nom, valeur in ENTETES_SECURITE:
                self.send_header(nom, valeur)
            for nom, valeur in entetes:
                self.send_header(nom, valeur)
            self.end_headers()
            if getattr(self, "command", None) != "HEAD":
                self.wfile.write(corps)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            self.close_connection = True

    def envoyer_json(self, code, objet, cle="", entetes=()):
        texte = masquer(json.dumps(objet, ensure_ascii=False), cle)
        self.envoyer(code, texte.encode("utf-8"), "application/json; charset=utf-8", entetes)

    # ---- session : cookie opaque, jamais la clé
    def jeton_presente(self):
        for morceau in (self.headers.get("Cookie") or "").split(";"):
            nom, _, valeur = morceau.strip().partition("=")
            if nom == NOM_COOKIE and RX_JETON.fullmatch(valeur.strip()):
                return valeur.strip()
        return None

    @staticmethod
    def cookie_session(jeton):
        """Cookie de session : opaque, HttpOnly, SameSite=Strict (pas de Secure : http loopback)."""
        if jeton is None:
            return ("Set-Cookie", "%s=; Path=/; Max-Age=0; HttpOnly; SameSite=Strict" % NOM_COOKIE)
        return ("Set-Cookie", "%s=%s; Path=/; HttpOnly; SameSite=Strict" % (NOM_COOKIE, jeton))

    def session_cle(self):
        """Clé de la session en cours, ou None (401 verrouille déjà envoyé)."""
        cle = self.server.session.cle(self.jeton_presente())
        if cle is None:
            motif = (self.server.session.etat().get("motif") or "verrouille")
            self.envoyer_json(401, {"erreur": "verrouille", "motif": motif,
                                    "message": MESSAGES.get(motif, MESSAGES["verrouille"])},
                              entetes=[self.cookie_session(None)])
            return None
        return cle

    def erreur(self, code, nom, message=None):
        self.envoyer_json(code, {"erreur": nom, "message": message or MESSAGES.get(nom, MESSAGES["requete_invalide"])})

    def send_error(self, code, message=None, explain=None):
        self.close_connection = True
        nom = "methode_refusee" if code == 501 else ERREUR_PAR_STATUT.get(code, "requete_invalide")
        try:
            self.erreur(code if code != 501 else 405, nom)
        except OSError:
            pass

    # ---- contrôles anti DNS-rebinding / CSRF
    @property
    def origine(self):
        return "http://%s:%d" % (HOTE, self.server.server_address[1])

    def controle_hote(self) -> bool:
        attendu = "%s:%d" % (HOTE, self.server.server_address[1])
        if not self.path.startswith("/") or (self.headers.get("Host") or "").strip().lower() != attendu:
            self.erreur(403, "hote_refuse")
            return False
        return True

    def controle_lecture(self) -> bool:
        site = self.headers.get("Sec-Fetch-Site")
        origine = self.headers.get("Origin")
        if (site is not None and site not in ("same-origin", "none")) or (origine is not None and origine != self.origine):
            self.erreur(403, "origine_refusee")
            return False
        return True

    def controle_ecriture(self) -> bool:
        site = self.headers.get("Sec-Fetch-Site")
        origine = self.headers.get("Origin")
        if (site is not None and site != "same-origin") or (origine is not None and origine != self.origine):
            self.erreur(403, "origine_refusee")
            return False
        if (self.headers.get("Content-Type") or "").split(";")[0].strip().lower() != "application/json":
            self.erreur(415, "type_refuse")
            return False
        return True

    def lire_corps(self, maxi=TAILLE_MAX_CORPS):
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            self.erreur(411, "requete_invalide", "Longueur de requête manquante.")
            return None
        longueur = (self.headers.get("Content-Length") or "").strip()
        if not longueur.isdigit():
            self.close_connection = True
            self.erreur(411, "requete_invalide", "Longueur de requête manquante.")
            return None
        n = int(longueur)
        if n > maxi:
            self.close_connection = True
            self.erreur(413, "trop_gros", "Requête trop volumineuse (%d Ko au maximum)." % (maxi // 1024))
            return None
        try:
            objet = json.loads(self.rfile.read(n).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, OSError):
            self.erreur(400, "requete_invalide", "Corps JSON invalide.")
            return None
        if not isinstance(objet, dict):
            self.erreur(400, "requete_invalide", "Objet JSON attendu.")
            return None
        return objet

    # ---- espace personnel : tout reste sur cet ordinateur
    def lire_espace(self, chemin, requete):
        espace = self.server.espace
        if chemin == "/api/espace":
            return self.envoyer_json(200, espace.tout())
        if chemin == "/api/espace/export":
            donnees = espace.exporter()
            if (requete.get("fichier") or [""])[0] == "1":   # lien « Exporter » de l'interface
                nom = "mon-espace-%s.json" % time.strftime("%Y%m%d")
                return self.envoyer_json(200, donnees,
                                         entetes=[("Content-Disposition", 'attachment; filename="%s"' % nom)])
            return self.envoyer_json(200, donnees)
        rubrique = chemin[len("/api/"):]
        tout = espace.tout()[rubrique]
        elements = tout
        cherche = (requete.get("q") or [""])[0].strip().casefold()[:200]
        if rubrique == "taches":
            etat = (requete.get("etat") or ["tout"])[0]
            if etat in ("a_faire", "faite"):
                elements = [t for t in elements if t["faite"] == (etat == "faite")]
            priorite = (requete.get("priorite") or [""])[0]
            if priorite in PRIORITES:
                elements = [t for t in elements if t["priorite"] == priorite]
            champs = ("titre", "detail", "echeance")
        elif rubrique == "notes":
            champs = ("titre", "texte")
        else:
            champs = ("titre", "lieu", "detail", "jour")
            for borne, test in (("du", lambda j, b: j >= b), ("au", lambda j, b: j <= b)):
                valeur = (requete.get(borne) or [""])[0]
                if RX_JOUR.fullmatch(valeur or ""):
                    elements = [c for c in elements if test(c["jour"], valeur)]
        if cherche:
            elements = [e for e in elements if any(cherche in str(e.get(c, "")).casefold() for c in champs)]
        return self.envoyer_json(200, {rubrique: elements, "total": len(tout),
                                       "limite": MAX_PAR_RUBRIQUE[rubrique]})

    def ecrire_espace(self, chemin):
        rubrique, _, action = chemin[len("/api/"):].partition("/")
        espace = self.server.espace
        corps = self.lire_corps(TAILLE_MAX_IMPORT if chemin == "/api/espace/importer" else TAILLE_MAX_LOCAL)
        if corps is None:
            return
        if chemin == "/api/espace/importer":
            return self.envoyer_json(200, espace.importer(corps))
        if action == "creer":
            return self.envoyer_json(200, espace.creer(rubrique, corps))
        if action == "modifier":
            element = espace.modifier(rubrique, corps)
            if element is None:
                return self.erreur(404, "introuvable", "Cet élément n'existe plus sur cet ordinateur.")
            return self.envoyer_json(200, element)
        if not espace.supprimer(rubrique, corps):
            return self.erreur(404, "introuvable", "Cet élément n'existe plus sur cet ordinateur.")
        return self.envoyer_json(200, {"ok": True, "supprime": corps.get("id")})

    # ---- ouverture / fermeture du cockpit
    def deverrouiller(self):
        """Valide la clé collée auprès de la passerelle, puis la garde en mémoire seulement."""
        corps = self.lire_corps()
        if corps is None:
            return
        if not self.server.essai_deverrouillage():
            return self.erreur(429, "limite", "Trop d'essais : attendez une minute avant de recommencer.")
        cle = nettoyer_cle(corps.get("cle"))
        if not cle:
            return self.erreur(400, "cle_absente")
        if not cle_valide(cle):
            return self.erreur(400, "cle_format")
        cfg, err = lire_config(self.server.chemin_config)
        if cfg is None:
            return self.erreur(503, err)
        statut, objet = relayer({"passerelle": cfg["passerelle"], "cle": cle}, "GET", "/v1/moi")
        if statut != 200 or not isinstance(objet, dict) or not objet.get("alias"):
            d = objet if isinstance(objet, dict) else {}
            code = d.get("erreur") if isinstance(d.get("erreur"), str) else "cle_invalide"
            if code == "cle_absente":
                code = "cle_invalide"
            self.server.session.fermer(code)
            message = d.get("message") if isinstance(d.get("message"), str) else MESSAGES.get(code, MESSAGES["cle_invalide"])
            return self.envoyer_json(statut if 400 <= statut < 600 else 502,
                                     {"erreur": code, "message": message, "verrouille": True}, cle,
                                     entetes=[self.cookie_session(None)])
        jeton = self.server.session.ouvrir(cle)
        reponse = dict(objet)
        reponse.update({"ok": True, "verrouille": False})
        self.envoyer_json(200, reponse, cle, entetes=[self.cookie_session(jeton)])

    def verrouiller(self):
        self.server.session.fermer("verrou_demande")
        self.envoyer_json(200, {"ok": True, "verrouille": True, "message": MESSAGES["verrou_demande"]},
                          entetes=[self.cookie_session(None)])

    # ---- routes
    def do_GET(self):
        if not self.controle_hote():
            return
        chemin = urlsplit(self.path).path
        if chemin.startswith("/api/"):
            if not self.controle_lecture():
                return
            if chemin == "/api/etat":
                return self.envoyer_json(200, self.server.etat(self.jeton_presente()))
            cle = self.session_cle()
            if cle is None:
                return
            if chemin == "/api/machine":
                return self.envoyer_json(200, self.server.machine())
            if chemin in GET_LOCAL:
                try:
                    return self.lire_espace(chemin, parse_qs(urlsplit(self.path).query))
                except ErreurEspace as e:
                    return self.erreur(500, e.code)
            amont = {"/api/moi": "/v1/moi", "/api/catalogue": "/v1/catalogue", "/api/demandes": "/v1/demandes"}.get(chemin)
            if amont is None:
                return self.erreur(404, "introuvable")
            return self.relais("GET", amont, cle=cle)
        statique = STATIQUES.get(chemin)
        if statique is None:
            return self.erreur(404, "introuvable")
        try:
            with open(os.path.join(WEB, statique[0]), "rb") as f:
                corps = f.read()
        except OSError:
            return self.erreur(404, "introuvable")
        self.envoyer(200, corps, statique[1])

    def do_POST(self):
        if not self.controle_hote():
            return
        chemin = urlsplit(self.path).path
        if chemin in ("/api/deverrouiller", "/api/verrouiller"):
            if not self.controle_ecriture():
                return
            return self.deverrouiller() if chemin == "/api/deverrouiller" else self.verrouiller()
        if chemin in POST_LOCAL:
            if not self.controle_ecriture():
                return
            if self.session_cle() is None:
                return
            try:
                return self.ecrire_espace(chemin)
            except ChampInvalide as e:
                return self.erreur(400, "requete_invalide", str(e))
            except ErreurEspace as e:
                return self.erreur(409 if e.code == "espace_plein" else 500, e.code)
        if chemin not in ("/api/demander", "/api/demande-acces"):
            return self.erreur(404, "introuvable")
        if not self.controle_ecriture():
            return
        cle = self.session_cle()
        if cle is None:
            return
        corps = self.lire_corps()
        if corps is None:
            return
        ref = corps.get("ref")
        if not isinstance(ref, str) or not RX_REF.fullmatch(ref):
            return self.erreur(400, "requete_invalide", "Référence d'élément invalide.")
        if chemin == "/api/demande-acces":
            motif = corps.get("motif", "")
            if motif is None:
                motif = ""
            if not valider_texte(motif, MAX_MOTIF):
                return self.erreur(400, "requete_invalide", "Motif invalide (1000 caractères au maximum).")
            return self.relais("POST", "/v1/demande-acces", {"ref": ref, "motif": motif.strip()}, cle=cle)
        question, mode = corps.get("question"), corps.get("mode", "rapide")
        if not valider_texte(question, MAX_QUESTION, MIN_QUESTION):
            return self.erreur(400, "requete_invalide", "Question trop courte (3 caractères au moins) ou trop longue (4000 au plus).")
        if mode not in ("rapide", "approfondi"):
            return self.erreur(400, "requete_invalide", "Mode inconnu (rapide ou approfondi).")
        amont = {"ref": ref, "question": question.strip(), "mode": mode}
        contexte = corps.get("contexte")
        if contexte is not None:
            if not isinstance(contexte, list) or len(contexte) > MAX_MESSAGES_CONTEXTE or not all(
                    isinstance(t, dict) and t.get("role") in ("user", "assistant")
                    and valider_texte(t.get("content"), MAX_QUESTION) for t in contexte):
                return self.erreur(400, "requete_invalide", "Contexte invalide (6 échanges, soit 12 messages, au maximum).")
            amont["contexte"] = [{"role": t["role"], "content": t["content"]} for t in contexte]
        if len(json.dumps(amont, ensure_ascii=False).encode("utf-8")) > TAILLE_MAX_CORPS:
            return self.erreur(413, "trop_gros")
        delai = DELAI_TABLE_RONDE if ref in REFS_TRES_LONGUES else \
            DELAI_LONG if (mode == "approfondi" or ref in REFS_LONGUES) else DELAI_RAPIDE
        self.relais("POST", "/v1/demander", amont, delai, cle=cle)

    def do_HEAD(self):
        self.erreur(405, "methode_refusee")

    do_PUT = do_DELETE = do_PATCH = do_OPTIONS = do_TRACE = do_CONNECT = do_HEAD

    def relais(self, methode, amont, corps=None, delai=DELAI_LECTURE, cle=""):
        cfg, err = lire_config(self.server.chemin_config)
        if cfg is None:
            return self.erreur(503, err)
        session = self.server.session
        statut, objet = relayer({"passerelle": cfg["passerelle"], "cle": cle}, methode, amont, corps, delai, session)
        self.envoyer_json(statut, objet, cle, entetes=() if session.ouverte else [self.cookie_session(None)])


class Serveur(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    # Windows : SO_REUSEADDR permettrait à un 2e processus de partager le port -> accès exclusif.
    allow_reuse_address = os.name != "nt"

    def __init__(self, port, chemin_config, minutes_verrou=MINUTES_VERROU_DEFAUT):
        self.chemin_config = chemin_config
        self.espace = Espace(os.path.join(os.path.dirname(os.path.abspath(chemin_config)), NOM_DONNEES))
        self.session = Session(minutes_verrou)
        self._essais = []
        self._etat_cache = (0.0, None)
        self._etat_verrou = threading.Lock()
        self._machine_cache = (0.0, None)
        self._machine_verrou = threading.Lock()
        super().__init__((HOTE, port), Gestionnaire)

    def essai_deverrouillage(self, maxi=10) -> bool:
        """Au plus `maxi` essais de clé par minute (un programme local ne peut pas tâtonner)."""
        with self._etat_verrou:
            maintenant = time.monotonic()
            self._essais = [t for t in self._essais if maintenant - t < 60]
            if len(self._essais) >= maxi:
                return False
            self._essais.append(maintenant)
            return True

    def machine(self) -> dict:
        """Mesures de cet ordinateur pour la rubrique « Mon Mac / Mon PC » (cache 60 s, la mesure prend ~2 s).

        Mêmes champs que la télémétrie, plus les conseils : rien de nouveau ne sort, tout est servi en local.
        """
        with self._machine_verrou:
            horodatage, cache = self._machine_cache
            if cache is None or time.monotonic() - horodatage >= 60:
                m = mesures()
                cache = dict(m, conseils=conseils_machine(m), mesure_le=datetime.now(timezone.utc).isoformat(timespec="seconds"))
                self._machine_cache = (time.monotonic(), cache)
            return dict(cache)

    def server_bind(self):
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def etat(self, jeton=None):
        """État MINIMAL, seule route lisible sans session : verrou + joignabilité du service.

        Le verrou est celui vu par CE demandeur : un navigateur sans jeton valide est
        verrouillé, même si une session est ouverte ailleurs. La consultation ne prolonge
        pas la session (sinon le verrou d'inactivité ne tomberait jamais).
        """
        verrou = self.session.etat()
        if not verrou.get("verrouille") and self.session.cle(jeton, toucher=False) is None:
            verrou = {"verrouille": True, "motif": None}
        if verrou.get("verrouille"):
            verrou["message"] = MESSAGES.get(verrou.get("motif") or "verrouille", MESSAGES["verrouille"])
        with self._etat_verrou:
            horodatage, cache = self._etat_cache
            if cache is not None and time.monotonic() - horodatage < 5:
                return dict(cache, **verrou)
        cfg, err = lire_config(self.chemin_config)
        res = {"cockpit": "ok", "version": VERSION, "configure": cfg is not None}
        if cfg is None:
            res["passerelle"] = {"joignable": False, "erreur": err, "message": MESSAGES[err]}
        else:
            try:
                statut, _ = appeler(cfg, "GET", "/v1/sante", delai=DELAI_SANTE, auth=False, delai_connexion=DELAI_SANTE)
                if statut == 200:
                    res["passerelle"] = {"joignable": True}
                else:
                    res["passerelle"] = {"joignable": False, "erreur": "passerelle_erreur",
                                         "message": MESSAGES["passerelle_erreur"]}
            except ErreurAmont as e:
                message = message_reseau(e)
                if e.code == "passerelle_injoignable":
                    message = diagnostic_tailscale()[1] + " " + message
                res["passerelle"] = {"joignable": False, "erreur": e.code, "message": message}
        with self._etat_verrou:
            self._etat_cache = (time.monotonic(), res)
        return dict(res, **verrou)


# --------------------------------------------------------------------------- modes
def configurer_journal(chemin):
    JOURNAL.setLevel(logging.INFO)
    JOURNAL.propagate = False
    gestion = None
    if chemin:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(chemin)), exist_ok=True)
            gestion = logging.handlers.RotatingFileHandler(chemin, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        except OSError:
            gestion = None
    if gestion is None:
        gestion = logging.StreamHandler(sys.stderr) if sys.stderr is not None else logging.NullHandler()
    gestion.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%Y-%m-%dT%H:%M:%S"))
    JOURNAL.addHandler(gestion)
    if sys.stderr is None:  # pythonw (Windows) : pas de console, les erreurs vont au journal
        try:
            sys.stderr = open(chemin + ".err" if chemin else os.devnull, "a", encoding="utf-8")
        except OSError:
            sys.stderr = open(os.devnull, "w")
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w")


def servir(args) -> int:
    configurer_journal(args.journal)
    purger_cle_config(args.config)   # une clé laissée par une version précédente n'est plus utilisée
    try:
        serveur = Serveur(args.port, args.config, args.minutes_verrou)
    except OSError as e:
        JOURNAL.info("démarrage impossible : port %d indisponible (%s)", args.port,
                     errno.errorcode.get(e.errno or 0, type(e).__name__))
        return 2
    telemetrie = Telemetrie(args.config, args.intervalle_telemetrie, serveur.session)
    telemetrie.start()
    surveillant = threading.Event()

    def veiller():
        while not surveillant.wait(5.0):
            serveur.session.verifier_inactivite()

    threading.Thread(target=veiller, name="verrou", daemon=True).start()
    JOURNAL.info("cockpit %s démarré sur http://%s:%d (Python %s) — verrouillé, clé demandée à l'ouverture "
                 "(verrou automatique après %g min sans activité)",
                 VERSION, HOTE, args.port, platform.python_version(), serveur.session.minutes)

    def arreter(*_):
        threading.Thread(target=serveur.shutdown, daemon=True).start()

    for nom_signal in ("SIGINT", "SIGTERM"):
        if hasattr(signal, nom_signal):
            try:
                signal.signal(getattr(signal, nom_signal), arreter)
            except (OSError, ValueError):
                pass
    try:
        serveur.serve_forever(poll_interval=0.5)
    finally:
        telemetrie.arret.set()
        surveillant.set()
        serveur.session.fermer("arret")
        serveur.server_close()
        JOURNAL.info("cockpit arrêté (clé oubliée)")
    return 0


def sortie_utf8():
    for flux in (sys.stdout, sys.stderr):
        try:
            if flux is not None and not flux.isatty():
                flux.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass


def verifier(args) -> int:
    """Teste la passerelle ; la clé, si elle est fournie sur l'entrée standard, n'est PAS enregistrée.

    Codes : 0 OK · 10 config · 11 injoignable · 12 réponse anormale · 13 expiré · 14 clé refusée ·
    15 accès pas encore activé · 16 limite · 17 autre · 21 clé mal formée."""
    sortie_utf8()
    cfg, err = lire_config(args.config)
    if cfg is None:
        print("    " + MESSAGES[err])
        return 10
    print("    passerelle : " + cfg["passerelle"])
    try:
        statut, _ = appeler(cfg, "GET", "/v1/sante", delai=8, auth=False, delai_connexion=8)
    except ErreurAmont as e:
        print("    santé (sans clé) : injoignable (%s)" % (e.detail or e.code))
        print("    " + message_reseau(e))
        if e.code == "passerelle_injoignable":
            print("    " + diagnostic_tailscale()[1])
        return 11
    if statut != 200:
        print("    santé (sans clé) : réponse anormale (HTTP %d)" % statut)
        return 12
    print("    santé (sans clé) : OK")
    cle = nettoyer_cle(sys.stdin.readline() if sys.stdin is not None else "")
    if not cle:
        print("    identité : non testée (aucune clé fournie)")
        print("    la clé d'accès est demandée à chaque ouverture du cockpit : elle n'est jamais enregistrée.")
        return 0
    if not cle_valide(cle):
        print("    Clé d'accès invalide : 32 à 256 caractères, sans espace ni accent.")
        return 21
    cfg = {"passerelle": cfg["passerelle"], "cle": cle}
    try:
        statut, brut = appeler(cfg, "GET", "/v1/moi", delai=15)
    except ErreurAmont as e:
        print("    identité (avec clé) : " + message_reseau(e))
        return 11
    try:
        d = json.loads(brut.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        d = None
    if not isinstance(d, dict):
        d = {}
    if statut == 200:
        quota = d.get("quota_jour")
        texte = "    identité (avec clé) : OK - %s · abonnement %s · questions aujourd'hui %s/%s" % (
            d.get("alias") or "?", d.get("abonnement") or "?", d.get("utilise_aujourdhui", 0), quota if quota is not None else "?")
        print(masquer(texte, cfg["cle"]))
        if d.get("expire_le"):
            print("    accès valable jusqu'au %s (heure de cet ordinateur)" % date_locale(d.get("expire_le")))
        print("    clé NON enregistrée : elle sera demandée à chaque ouverture du cockpit.")
        return 0
    if statut == 401 and d.get("erreur") == "acces_expire":
        print("    identité (avec clé) : Votre accès a expiré.")
        print("    Demandez une nouvelle clé d'accès, puis collez-la à l'ouverture du cockpit.")
        return 13
    if statut == 401:
        print("    identité (avec clé) : clé refusée (inconnue, mal copiée ou révoquée).")
        print("    Vérifiez la clé reçue (copiez-la en entier, sans espace).")
        return 14
    if statut == 403:
        if d.get("erreur") == "acces_suspendu":
            print("    identité (avec clé) : accès suspendu. Contactez votre interlocuteur habituel.")
        else:
            print("    identité (avec clé) : accès pas encore activé (adhésion en attente d'approbation).")
            print("    Réessayez une fois l'approbation confirmée.")
        return 15
    if statut == 429:
        print("    identité (avec clé) : trop de requêtes, réessayez dans une minute.")
        return 16
    print("    identité (avec clé) : réponse inattendue (HTTP %d)" % statut)
    return 17


def ecrire_config(args) -> int:
    """Écrit l'adresse de la passerelle. AUCUNE clé n'est enregistrée (secret d'ouverture).

    Codes : 0 OK · 22 passerelle invalide · 23 écriture impossible."""
    sortie_utf8()
    ancienne = {}
    try:
        with open(args.config, "r", encoding="utf-8-sig") as f:
            ancienne = json.load(f)
        if not isinstance(ancienne, dict):
            ancienne = {}
    except (OSError, ValueError):
        ancienne = {}
    passerelle = (args.passerelle or "").strip()
    if not passerelle:
        passerelle = ancienne.get("passerelle") if passerelle_valide(ancienne.get("passerelle")) else PASSERELLE_DEFAUT
    if not passerelle_valide(passerelle):
        print("    Adresse de passerelle invalide (attendu http://adresse:port).")
        return 22
    dossier = os.path.dirname(os.path.abspath(args.config))
    temporaire = args.config + ".tmp"
    try:
        os.makedirs(dossier, exist_ok=True)
        if os.name != "nt":
            os.chmod(dossier, 0o700)
        if os.path.exists(temporaire):
            os.remove(temporaire)
        fd = os.open(temporaire, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"passerelle": passerelle.rstrip("/")}, f, ensure_ascii=True, indent=2)
            f.write("\n")
        os.replace(temporaire, args.config)
        if os.name != "nt":
            os.chmod(args.config, 0o600)
    except OSError as e:
        print("    Écriture de la configuration impossible (%s)." % type(e).__name__)
        return 23
    if "cle" in ancienne:
        print("    clé retirée de la configuration : elle n'est plus enregistrée sur ce disque.")
    print("    configuration enregistrée (%s, sans clé)" % passerelle.rstrip("/"))
    return 0


def sonder(args) -> int:
    """Interroge le cockpit local (/api/etat). Codes : 0 cockpit OK et passerelle joignable par le
    service · 1 cockpit muet · 2 cockpit OK mais passerelle injoignable depuis le service."""
    sortie_utf8()
    try:
        conn = http.client.HTTPConnection(HOTE, args.port, timeout=20)
        conn.request("GET", "/api/etat", headers={"Host": "%s:%d" % (HOTE, args.port), "Accept": "application/json"})
        rep = conn.getresponse()
        d = json.loads(rep.read().decode("utf-8"))
        conn.close()
    except (OSError, http.client.HTTPException, ValueError, UnicodeDecodeError):
        print("    cockpit local : ne répond pas")
        return 1
    if rep.status != 200 or not isinstance(d, dict) or d.get("cockpit") != "ok":
        print("    cockpit local : réponse anormale (HTTP %d)" % rep.status)
        return 1
    passerelle = d.get("passerelle") if isinstance(d.get("passerelle"), dict) else {}
    if passerelle.get("joignable"):
        print("    cockpit local : OK · passerelle joignable par le service")
        return 0
    print("    cockpit local : OK · passerelle injoignable par le service : %s" % (passerelle.get("message") or "?"))
    return 2


def en_ligne(port, delai=2.0) -> bool:
    try:
        conn = http.client.HTTPConnection(HOTE, port, timeout=delai)
        conn.request("GET", "/", headers={"Host": "%s:%d" % (HOTE, port)})
        ok = conn.getresponse().status == 200
        conn.close()
        return ok
    except (OSError, http.client.HTTPException):
        return False


def lanceur(args) -> int:
    import webbrowser
    url = "http://%s:%d/" % (HOTE, args.port)
    if not en_ligne(args.port):
        commande = [sys.executable, os.path.abspath(__file__), "--port", str(args.port), "--config", args.config]
        if args.journal:
            commande += ["--journal", args.journal]
        options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
                   "close_fds": True}
        if os.name == "nt":
            options["creationflags"] = 0x00000008 | 0x00000200 | 0x08000000
        else:
            options["start_new_session"] = True
        try:
            subprocess.Popen(commande, **options)
        except OSError:
            pass
        fin = time.monotonic() + 20
        while time.monotonic() < fin and not en_ligne(args.port):
            time.sleep(0.5)
    if en_ligne(args.port):
        webbrowser.open(url)
        return 0
    message = "Le cockpit ne répond pas. Journal : %s" % (args.journal or "indisponible")
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, message, "Cockpit", 0x10)
        except (OSError, AttributeError):
            pass
    else:
        print(message, file=sys.stderr)
    return 1


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Cockpit client local (127.0.0.1).")
    p.add_argument("--port", type=int, default=PORT_DEFAUT)
    p.add_argument("--config", default=None)
    p.add_argument("--journal", default=None)
    p.add_argument("--passerelle", default=None)
    p.add_argument("--intervalle-telemetrie", type=float, default=60.0)
    p.add_argument("--minutes-verrou", type=float, default=MINUTES_VERROU_DEFAUT,
                   help="verrouillage automatique après ce nombre de minutes sans activité")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--verifier", action="store_true")
    mode.add_argument("--ecrire-config", action="store_true")
    mode.add_argument("--lanceur", action="store_true")
    mode.add_argument("--sonder", action="store_true")
    mode.add_argument("--version", action="store_true")
    args = p.parse_args(argv)
    if not 1024 <= args.port <= 65535:
        p.error("port invalide (1024 à 65535)")
    args.config = args.config or chemin_config_defaut()
    if args.version:
        print(VERSION)
        return 0
    if args.verifier:
        return verifier(args)
    if args.ecrire_config:
        return ecrire_config(args)
    if args.lanceur:
        return lanceur(args)
    if args.sonder:
        return sonder(args)
    return servir(args)


if __name__ == "__main__":
    sys.exit(main())
