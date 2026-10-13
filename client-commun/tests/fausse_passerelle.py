#!/usr/bin/env python3
"""Fausse passerelle de TEST (jamais livrée) : respecte le contrat /v1/* de la passerelle.

  fausse_passerelle.py --port N --cles FICHIER_JSON   (fichier existant = clés réutilisées)

Clés générées (128 caractères hexadécimaux) et écrites dans FICHIER_JSON :
  valide (abonnement pro, quota 8/jour, échéance +48 h), expiree, revoquee (inconnue), inactive.
Route de pilotage réservée aux tests : POST /_controle {"action": ...}
  etat · expirer {qui} · retablir · accorder {ref} · debit (remet à zéro débit + quota)
  · delai_moi {secondes} · prochain_envoi {secondes} · vider
"""
import argparse
import json
import secrets
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CATALOGUE = {
    "dom-marketing": {"nature": "domaine", "titre": "Marketing et commercialisation",
                      "description": "Positionnement, prix, canaux de vente.", "source_interne": "INTERNE-dom-1"},
    "guide-demarrage": {"nature": "guide", "titre": "Guide de démarrage",
                        "description": "Le départ, étape par étape.", "source_interne": "INTERNE-pack-2"},
    "llm-local": {"nature": "competence", "titre": "Assistant conversationnel",
                  "description": "Discussion suivie avec l'assistant.", "source_interne": "INTERNE-prompt-3"},
    "llm-code": {"nature": "competence", "titre": "Assistant de programmation",
                 "description": "Aide au code, discussion suivie.", "source_interne": "INTERNE-8"},
    "grand-conseil": {"nature": "competence", "titre": "Grand conseil",
                      "description": "Question soumise à plusieurs experts, réponse délibérée.", "source_interne": "INTERNE-4"},
    "table-ronde": {"nature": "competence", "titre": "Table ronde",
                    "description": "Confrontation de points de vue.", "source_interne": "INTERNE-5"},
    "dom-juridique": {"nature": "domaine", "titre": "Droit des contrats",
                      "description": "Clauses, obligations, litiges.", "source_interne": "INTERNE-6"},
    "dom-cache": {"nature": "domaine", "titre": "Interne", "description": "Non proposable.",
                  "source_interne": "INTERNE-7", "proposable": False},
}
JOURNAL_MAX = 500


class Etat:
    def __init__(self, cles=None):
        self.verrou = threading.Lock()
        expire_le = (datetime.now(timezone.utc) + timedelta(hours=48)).replace(microsecond=0)
        self.cles = dict(cles or {})
        for nom in ("valide", "expiree", "revoquee", "inactive"):
            self.cles.setdefault(nom, secrets.token_hex(64))
        self.comptes = {
            self.cles["valide"]: {"alias": "Espace test", "abonnement": "pro", "quota_jour": 8, "utilise": 0,
                                  "expire_le": expire_le.isoformat().replace("+00:00", "Z"),
                                  "accordes": ["dom-marketing", "guide-demarrage", "llm-local", "llm-code", "grand-conseil"],
                                  "etat": "ok"},
            self.cles["expiree"]: {"etat": "expire"},
            self.cles["inactive"]: {"etat": "inactif"},
        }
        self.demandes = []
        self.debit = {}
        self.journal = []
        self.telemetrie = []
        self.delai_moi = 0.0
        self.prochain_envoi = 30  # comme la vraie passerelle : « un envoi toutes les 30 s au plus »


ETAT = None


class G(BaseHTTPRequestHandler):
    server_version = "neutre"
    sys_version = ""

    def log_message(self, *a):
        pass

    def repondre(self, code, obj):
        corps = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(corps)))
        self.end_headers()
        try:
            self.wfile.write(corps)
        except OSError:
            pass

    def noter(self, corps):
        with ETAT.verrou:
            ETAT.journal.append({"t": time.time(), "methode": self.command, "chemin": self.path,
                                 "entetes": {k: v for k, v in self.headers.items()},
                                 "corps": corps})
            del ETAT.journal[:-JOURNAL_MAX]

    def corps(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n > 16384:
            return "trop_gros"
        brut = self.rfile.read(n) if n else b""
        try:
            return json.loads(brut.decode("utf-8")) if brut else None
        except ValueError:
            return "invalide"

    def compte(self):
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Bearer "):
            self.repondre(401, {"erreur": "cle_absente", "message": "Clé d'accès absente."})
            return None, None
        cle = auth[7:]
        c = ETAT.comptes.get(cle)
        if c is None:
            self.repondre(401, {"erreur": "cle_invalide", "message": "Clé d'accès refusée."})
            return None, None
        if c["etat"] == "expire":
            self.repondre(401, {"erreur": "acces_expire", "message": "Votre accès a expiré."})
            return None, None
        if c["etat"] == "inactif":
            self.repondre(403, {"erreur": "compte_inactif", "message": "Accès pas encore activé."})
            return None, None
        with ETAT.verrou:
            maintenant = time.time()
            fen = [t for t in ETAT.debit.get(cle, []) if maintenant - t < 60]
            fen.append(maintenant)
            ETAT.debit[cle] = fen
            if len(fen) > 30:
                self.repondre(429, {"erreur": "debit", "message": "Trop de requêtes : réessayez dans une minute."})
                return None, None
        return cle, c

    def items(self, c):
        accordes = [r for r in c["accordes"] if r in CATALOGUE]
        def pub(r):
            i = CATALOGUE[r]
            return {"ref": r, "nature": i["nature"], "titre": i["titre"], "description": i["description"]}
        proposes = [r for r, i in CATALOGUE.items() if r not in accordes and i.get("proposable", True)]
        return [pub(r) for r in accordes], [pub(r) for r in proposes]

    def do_GET(self):
        self.noter(None)
        if self.path == "/v1/sante":
            return self.repondre(200, {"ok": True})
        if self.path not in ("/v1/moi", "/v1/catalogue", "/v1/demandes"):
            return self.repondre(404, {"erreur": "introuvable"})
        cle, c = self.compte()
        if cle is None:
            return
        if self.path == "/v1/moi":
            if ETAT.delai_moi:
                time.sleep(ETAT.delai_moi)
            return self.repondre(200, {"alias": c["alias"], "abonnement": c["abonnement"], "quota_jour": c["quota_jour"],
                                       "utilise_aujourdhui": c["utilise"], "expire_le": c["expire_le"]})
        if self.path == "/v1/catalogue":
            a, p = self.items(c)
            return self.repondre(200, {"accordes": a, "proposes": p})
        with ETAT.verrou:
            liste = [{k: d[k] for k in ("id", "ref", "titre", "statut", "cree_le")} for d in ETAT.demandes if d["cle"] == cle]
        return self.repondre(200, liste)

    def do_POST(self):
        corps = self.corps()
        self.noter(corps)
        if self.path == "/_controle":
            return self.controle(corps or {})
        if self.path not in ("/v1/demander", "/v1/demande-acces", "/v1/telemetrie"):
            return self.repondre(404, {"erreur": "introuvable"})
        if corps == "trop_gros":
            return self.repondre(413, {"erreur": "trop_gros"})
        cle, c = self.compte()
        if cle is None:
            return
        if not isinstance(corps, dict):
            return self.repondre(400, {"erreur": "requete_invalide"})
        if self.path == "/v1/telemetrie":
            attendus = {"os", "version_os", "cpu_modele", "coeurs", "cpu_pct", "ram_total_mo", "ram_utilisee_mo",
                        "disque_total_go", "disque_libre_go", "uptime_s", "version_client"}
            if set(corps) != attendus:
                return self.repondre(400, {"erreur": "requete_invalide", "message": "champs inattendus"})
            with ETAT.verrou:
                ETAT.telemetrie.append({"t": time.time(), "mesures": corps})
            return self.repondre(200, {"ok": True, "prochain_envoi_s": ETAT.prochain_envoi})
        ref = corps.get("ref")
        if self.path == "/v1/demande-acces":
            if ref not in CATALOGUE or not CATALOGUE[ref].get("proposable", True):
                return self.repondre(404, {"erreur": "introuvable", "message": "Élément introuvable."})
            with ETAT.verrou:
                for d in ETAT.demandes:
                    if d["cle"] == cle and d["ref"] == ref and d["statut"] == "en_attente":
                        return self.repondre(200, {"id": d["id"], "statut": "en_attente"})
                d = {"id": len(ETAT.demandes) + 1, "cle": cle, "ref": ref, "titre": CATALOGUE[ref]["titre"],
                     "statut": "en_attente", "motif": corps.get("motif", ""),
                     "cree_le": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")}
                ETAT.demandes.append(d)
            return self.repondre(200, {"id": d["id"], "statut": "en_attente"})
        # /v1/demander
        if ref not in c["accordes"]:
            return self.repondre(403, {"erreur": "non_accorde", "message": "Cet élément ne fait pas partie de vos accès."})
        question, mode = corps.get("question"), corps.get("mode")
        if not isinstance(question, str) or not question.strip() or mode not in ("rapide", "approfondi"):
            return self.repondre(400, {"erreur": "requete_invalide"})
        contexte = corps.get("contexte")
        if contexte is not None and (not isinstance(contexte, list) or len(contexte) > 12 or not all(
                isinstance(m, dict) and m.get("role") in ("user", "assistant") and isinstance(m.get("content"), str)
                for m in contexte)):
            return self.repondre(400, {"erreur": "requete_invalide", "message": "contexte"})
        if len(question.strip()) < 3:
            return self.repondre(400, {"erreur": "requete_invalide"})
        with ETAT.verrou:
            if c["utilise"] >= c["quota_jour"]:
                return self.repondre(429, {"erreur": "quota", "message": "Quota du jour atteint."})
            c["utilise"] += 1
        debut = time.time()
        attente = {"rapide": 0.3, "approfondi": 1.5}[mode]
        if ref in ("grand-conseil", "table-ronde"):
            attente = 2.0
        if question.startswith("ATTENTE:"):
            attente = float(question.split(":", 1)[1].split()[0])
        time.sleep(attente)
        if ref in ("llm-local", "llm-code"):
            texte = "Réponse de l'assistant (contexte reçu : %d échange(s))." % (len(contexte or []) // 2)
            citations = []
        else:
            texte = "Réponse %s sur « %s » [1] avec une source web [2]." % (mode, CATALOGUE[ref]["titre"])
            citations = [{"n": 1, "titre": "Source 1"}, {"n": 2, "titre": "exemple.org"}]
        if question.startswith("ECHO_CLE"):
            texte += " Écho : " + (self.headers.get("Authorization") or "")
        return self.repondre(200, {"reponse": texte, "citations": citations, "ref": ref,
                                   "duree_ms": int((time.time() - debut) * 1000)})

    def controle(self, corps):
        action = corps.get("action")
        with ETAT.verrou:
            if action == "etat":
                return self.repondre(200, {"journal": ETAT.journal, "telemetrie": ETAT.telemetrie,
                                           "demandes": ETAT.demandes})
            if action == "expirer":
                ETAT.comptes[ETAT.cles[corps.get("qui", "valide")]]["etat"] = "expire"
            elif action == "retablir":
                ETAT.comptes[ETAT.cles["valide"]]["etat"] = "ok"
            elif action == "accorder":
                c = ETAT.comptes[ETAT.cles["valide"]]
                if corps["ref"] not in c["accordes"]:
                    c["accordes"].append(corps["ref"])
                for d in ETAT.demandes:
                    if d["ref"] == corps["ref"]:
                        d["statut"] = "accordee"
            elif action == "debit":
                ETAT.debit.clear()
                ETAT.comptes[ETAT.cles["valide"]]["utilise"] = 0
            elif action == "prochain_envoi":
                ETAT.prochain_envoi = int(corps.get("secondes", 30))
            elif action == "delai_moi":
                ETAT.delai_moi = float(corps.get("secondes", 0))
            elif action == "vider":
                ETAT.journal.clear()
                ETAT.telemetrie.clear()
            else:
                return self.repondre(400, {"erreur": "action"})
        return self.repondre(200, {"ok": True})


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--cles", required=True)
    a = p.parse_args()
    global ETAT
    anciennes = None
    try:
        with open(a.cles, encoding="utf-8") as f:
            anciennes = json.load(f)  # redémarrage : mêmes clés
    except (OSError, ValueError):
        pass
    ETAT = Etat(anciennes)
    with open(a.cles, "w", encoding="utf-8") as f:
        json.dump(ETAT.cles, f)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), G)
    srv.daemon_threads = True
    print("fausse passerelle prête sur 127.0.0.1:%d" % a.port, flush=True)
    try:
        srv.serve_forever(poll_interval=0.2)
    finally:
        srv.server_close()


if __name__ == "__main__":
    sys.exit(main())
