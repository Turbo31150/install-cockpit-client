"""Vérifications HTTP du serveur client contre la fausse passerelle (module de test, jamais livré)."""
import http.client
import json
import socket
import threading
import time


NOM_COOKIE = "cockpit_session"
VERSION_CLIENT = "1.1.0"


class Banc:
    """Client HTTP de test : garde toutes les réponses pour contrôler qu'aucune clé n'y figure.

    Le cockpit est verrouillé au démarrage : `deverrouiller()` colle la clé et garde le
    cookie de session opaque, comme le ferait le navigateur.
    """

    def __init__(self, port, port_faux, cles):
        self.port, self.port_faux, self.cles = port, port_faux, cles
        self.origine = "http://127.0.0.1:%d" % port
        self.reponses = []
        self.resultats = []
        self.cookie = None
        self.dernier_entetes = []
        self.verrou = threading.Lock()

    # ---- outils
    def req(self, methode, chemin, corps=None, entetes=None, hote=None, brut=None, delai=200, cookie=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=delai)
        conn.putrequest(methode, chemin, skip_host=True, skip_accept_encoding=True)
        if hote is not False:
            conn.putheader("Host", hote or "127.0.0.1:%d" % self.port)
        donnees = brut if brut is not None else (json.dumps(corps).encode() if corps is not None else None)
        ent = dict(entetes or {})
        if cookie and self.cookie and "Cookie" not in ent:
            ent["Cookie"] = "%s=%s" % (NOM_COOKIE, self.cookie)
        if donnees is not None:
            ent.setdefault("Content-Type", "application/json")
            ent["Content-Length"] = str(len(donnees))
        for k, v in ent.items():
            conn.putheader(k, v)
        conn.endheaders()
        if donnees is not None:
            try:
                conn.send(donnees)
            except (BrokenPipeError, ConnectionResetError):
                pass   # corps refusé (413) : le serveur a répondu puis fermé sans tout lire
        r = conn.getresponse()
        b = r.read()
        h = r.getheaders()
        conn.close()
        with self.verrou:
            self.reponses.append((methode, chemin, r.status, h, b))
            self.dernier_entetes = h
        for nom, valeur in h:
            if nom.lower() == "set-cookie" and valeur.strip().startswith(NOM_COOKIE + "="):
                self.cookie = valeur.split(";")[0].split("=", 1)[1].strip() or None
        try:
            j = json.loads(b.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            j = None
        return r.status, dict(h), b, j

    def poster(self, chemin, corps, **kw):
        ent = {"Origin": self.origine, "Sec-Fetch-Site": "same-origin"}
        ent.update(kw.pop("entetes", {}))
        return self.req("POST", chemin, corps, entetes=ent, **kw)

    def lire(self, chemin, **kw):
        ent = {"Sec-Fetch-Site": "same-origin"}
        ent.update(kw.pop("entetes", {}))
        return self.req("GET", chemin, entetes=ent, **kw)

    def controle(self, action, **kw):
        conn = http.client.HTTPConnection("127.0.0.1", self.port_faux, timeout=10)
        corps = json.dumps(dict(action=action, **kw)).encode()
        conn.request("POST", "/_controle", corps, {"Content-Type": "application/json"})
        r = conn.getresponse()
        j = json.loads(r.read().decode("utf-8"))
        conn.close()
        return j

    def ok(self, nom, condition, detail=""):
        with self.verrou:
            self.resultats.append((nom, bool(condition), detail))
        return bool(condition)

    def nb_amont(self):
        """Appels relayés vers /v1 (hors santé et télémétrie, qui partent d'eux-mêmes)."""
        return len([e for e in self.controle("etat")["journal"]
                    if e["chemin"].startswith("/v1/") and e["chemin"] not in ("/v1/sante", "/v1/telemetrie")])

    # ---- déverrouillage (la clé est le secret d'ouverture)
    def deverrouiller(self, nom="valide"):
        st, _, _, j = self.poster("/api/deverrouiller", {"cle": self.cles[nom]})
        self.ok("déverrouillage avec la clé %s -> session ouverte" % nom,
                st == 200 and (j or {}).get("alias") and self.cookie, (st, j))
        return st == 200

    # ---- scénarios
    def statiques(self):
        st, h, b, _ = self.req("GET", "/")
        self.ok("GET / -> 200 html", st == 200 and h.get("Content-Type", "").startswith("text/html"), st)
        self.ok("GET / : CSP stricte", "default-src 'none'" in h.get("Content-Security-Policy", ""))
        self.ok("GET / : en-tête Server neutre", h.get("Server") == "cockpit", h.get("Server"))
        self.ok("GET / : marque et titre", "AlkymIA · Turbo OS — Votre cockpit" in b.decode("utf-8"))
        page = b.decode("utf-8")
        for texte in ("Collez votre clé d'accès", "Assistants", "Mes tâches", "Mon planning", "Mes notes",
                      "Outils", "Mon accès", "Verrouiller"):
            self.ok("page : rubrique ou libellé « %s »" % texte, texte in page)
        externes = []
        for chemin in ("/", "/app.js", "/app.css", "/espace.js", "/outils.js"):
            st2, _, corps, _ = self.req("GET", chemin)
            texte = corps.decode("utf-8")
            for motif in ('src="http', "src='http", 'href="http', "href='http", "url(http", "@import", "//cdn"):
                if motif in texte:
                    externes.append((chemin, motif))
            self.ok("GET %s -> 200" % chemin, st2 == 200, st2)
        self.ok("aucune ressource externe (html/css/js)", not externes, externes)
        st, h, _, _ = self.req("GET", "/icone.svg")
        self.ok("GET /icone.svg -> 200 svg", st == 200 and "svg" in h.get("Content-Type", ""), st)
        for chemin in ("/serveur.py", "/../serveur.py", "/web/app.js", "/config.json", "/%2e%2e/serveur.py", "/app.js/",
                       "/donnees.json", "/../donnees.json"):
            st, _, _, _ = self.req("GET", chemin)
            self.ok("GET %s -> 404" % chemin, st == 404, st)

    def hote_origine(self):
        avant = self.nb_amont()
        for hote in ("evil.example:%d" % self.port, "localhost:%d" % self.port, "127.0.0.1", "127.0.0.1:1"):
            st, _, _, j = self.req("GET", "/api/moi", hote=hote)
            self.ok("Host %s refusé (403)" % hote, st == 403 and j and j.get("erreur") == "hote_refuse", st)
        st, _, _, _ = self.req("GET", "/", hote=False)
        self.ok("Host absent refusé", st in (400, 403), st)
        st, _, _, _ = self.req("GET", "http://evil.example/api/moi")
        self.ok("cible absolue refusée", st in (400, 403), st)
        st, _, _, j = self.req("POST", "/api/demander", {"ref": "dom-marketing", "question": "x", "mode": "rapide"},
                               entetes={"Origin": "http://evil.example"})
        self.ok("POST Origin étrangère -> 403", st == 403 and j and j.get("erreur") == "origine_refusee", st)
        st, _, _, _ = self.req("POST", "/api/demander", {"ref": "dom-marketing", "question": "x", "mode": "rapide"},
                               entetes={"Origin": "null"})
        self.ok("POST Origin null -> 403", st == 403, st)
        st, _, _, _ = self.req("POST", "/api/demande-acces", {"ref": "table-ronde"}, entetes={"Sec-Fetch-Site": "cross-site"})
        self.ok("POST Sec-Fetch-Site cross-site -> 403", st == 403, st)
        st, _, _, _ = self.req("POST", "/api/demander", {"ref": "dom-marketing", "question": "x", "mode": "rapide"},
                               entetes={"Origin": self.origine, "Content-Type": "text/plain"})
        self.ok("POST text/plain -> 415", st == 415, st)
        st, _, _, _ = self.req("GET", "/api/moi", entetes={"Sec-Fetch-Site": "cross-site"})
        self.ok("GET /api Sec-Fetch-Site cross-site -> 403", st == 403, st)
        st, _, _, _ = self.req("GET", "/api/moi", entetes={"Origin": "http://evil.example"})
        self.ok("GET /api Origin étrangère -> 403", st == 403, st)
        st, h, _, _ = self.req("OPTIONS", "/api/demander", entetes={"Origin": "http://evil.example",
                                                                   "Access-Control-Request-Method": "POST"})
        self.ok("OPTIONS (pré-vol CORS) refusé sans en-tête CORS", st in (403, 405) and
                not any(k.lower().startswith("access-control") for k in h), st)
        self.ok("requêtes refusées : rien n'est parti vers la passerelle", self.nb_amont() == avant,
                (avant, self.nb_amont()))

    def verrouillage(self):
        """Cockpit verrouillé : rien n'est lisible, rien ne part ; puis déverrouillage par la clé."""
        avant = self.nb_amont()
        self.cookie = None
        lectures = ("/api/moi", "/api/catalogue", "/api/demandes", "/api/espace", "/api/espace/export",
                    "/api/taches", "/api/creneaux", "/api/notes")
        for chemin in lectures:
            st, _, _, j = self.lire(chemin)
            self.ok("verrouillé : GET %s -> 401 verrouille" % chemin,
                    st == 401 and (j or {}).get("erreur") == "verrouille", (st, j))
        ecritures = (("/api/demander", {"ref": "dom-marketing", "question": "xyz", "mode": "rapide"}),
                     ("/api/demande-acces", {"ref": "table-ronde"}),
                     ("/api/taches/creer", {"titre": "fuite"}),
                     ("/api/notes/creer", {"titre": "fuite"}),
                     ("/api/creneaux/creer", {"titre": "fuite", "jour": "2026-10-20", "debut": "09:00"}),
                     ("/api/espace/importer", {"donnees": {"taches": []}}))
        for chemin, corps in ecritures:
            st, _, _, j = self.poster(chemin, corps)
            self.ok("verrouillé : POST %s -> 401 verrouille" % chemin,
                    st == 401 and (j or {}).get("erreur") == "verrouille", (st, j))
        self.ok("verrouillé : aucun appel n'est parti vers la passerelle", self.nb_amont() == avant,
                (avant, self.nb_amont()))
        st, _, b, j = self.lire("/api/etat")
        self.ok("verrouillé : /api/etat minimal (verrou + service), sans identité",
                st == 200 and j.get("verrouille") is True and isinstance(j.get("passerelle"), dict)
                and "alias" not in j and "abonnement" not in j and b"quota" not in b, j)
        st, _, _, j = self.poster("/api/deverrouiller", {"cle": "court"})
        self.ok("clé mal formée -> 400 cle_format", st == 400 and j.get("erreur") == "cle_format", (st, j))
        st, _, _, j = self.poster("/api/deverrouiller", {})
        self.ok("clé absente -> 400 cle_absente", st == 400 and j.get("erreur") == "cle_absente", (st, j))
        st, _, _, j = self.poster("/api/deverrouiller", {"cle": "Z" * 100})
        self.ok("clé inconnue -> 401, message « refusée »", st == 401 and "refus" in (j.get("message") or "").lower(), (st, j))
        st, _, _, j = self.poster("/api/deverrouiller", {"cle": self.cles["expiree"]})
        self.ok("clé expirée -> 401 acces_expire (message distinct)",
                st == 401 and j.get("erreur") == "acces_expire" and j.get("message") == "Votre accès a expiré.", (st, j))
        st, _, _, j = self.poster("/api/deverrouiller", {"cle": self.cles["inactive"]})
        self.ok("accès pas encore activé -> 403 (message distinct)",
                st == 403 and j.get("erreur") == "compte_inactif" and j.get("message") != "Votre accès a expiré.", (st, j))
        self.ok("aucune session après un échec de clé", self.cookie is None, self.cookie)
        st, _, _, j = self.lire("/api/moi")
        self.ok("toujours verrouillé après les échecs", st == 401 and j.get("erreur") == "verrouille", st)
        st, h, b, j = self.poster("/api/deverrouiller", {"cle": self.cles["valide"]})
        biscuits = [v for k, v in self.dernier_entetes if k.lower() == "set-cookie"]
        self.ok("bonne clé -> 200 avec identité", st == 200 and j.get("alias") == "Espace test"
                and j.get("verrouille") is False and j.get("expire_le"), (st, j))
        self.ok("cookie de session : HttpOnly, SameSite=Strict, Path=/, sans Secure",
                biscuits and "HttpOnly" in biscuits[0] and "SameSite=Strict" in biscuits[0]
                and "Path=/" in biscuits[0] and "Secure" not in biscuits[0], biscuits)
        self.ok("jeton de session opaque, sans rapport avec la clé",
                self.cookie and len(self.cookie) >= 32 and self.cles["valide"] not in (biscuits[0] if biscuits else ""),
                self.cookie)
        self.ok("la clé n'apparaît pas dans la réponse de déverrouillage",
                not any(c.encode() in b for c in self.cles.values()))
        vrai = self.cookie
        self.cookie = "Z" * 43
        st, _, _, j = self.lire("/api/moi")
        self.ok("jeton inventé -> 401 verrouille", st == 401 and j.get("erreur") == "verrouille", st)
        st, _, _, j = self.lire("/api/etat", cookie=False)
        self.ok("session ouverte ailleurs : /api/etat reste « verrouillé » sans le jeton",
                st == 200 and j.get("verrouille") is True, j)
        self.cookie = vrai
        st, _, _, j = self.lire("/api/moi")
        self.ok("jeton valide -> 200", st == 200 and j.get("alias") == "Espace test", st)
        st, _, _, j = self.lire("/api/etat")
        self.ok("avec le jeton : /api/etat annonce le cockpit ouvert",
                st == 200 and j.get("verrouille") is False and j.get("verrou_dans_s", 0) > 0, j)

    def verrouiller(self):
        st, _, _, j = self.poster("/api/verrouiller", {})
        self.ok("POST /api/verrouiller -> 200 et cookie effacé",
                st == 200 and j.get("verrouille") is True and self.cookie is None, (st, j))
        st, _, _, j = self.lire("/api/moi")
        self.ok("après « Verrouiller » : /api/moi -> 401 verrouille", st == 401 and j.get("erreur") == "verrouille", st)
        st, _, _, j = self.lire("/api/taches")
        self.ok("après « Verrouiller » : /api/taches -> 401 verrouille", st == 401 and j.get("erreur") == "verrouille", st)

    def espace(self, attendu_depart=True):
        """Tâches, créneaux et notes : routes 100 % locales (jamais relayées)."""
        avant = self.nb_amont()
        st, _, _, j = self.lire("/api/espace")
        if attendu_depart:
            self.ok("espace vide au départ", st == 200 and j.get("taches") == [] and j.get("notes") == []
                    and j.get("creneaux") == [], j)
        titre = "Relancer le devis « Dupont & fils » <script>"
        st, _, _, t = self.poster("/api/taches/creer", {"titre": titre, "priorite": "haute",
                                                        "echeance": "2026-10-20", "heure": "09:30",
                                                        "detail": "ligne 1\nligne 2\témoji 😀"})
        self.ok("tâche créée : caractères spéciaux et retours à la ligne conservés",
                st == 200 and t.get("id") and t.get("titre") == titre and "\n" in t.get("detail", "")
                and "😀" in t.get("detail", ""), t)
        ident = (t or {}).get("id")
        st, _, _, j = self.poster("/api/taches/modifier", {"id": ident, "faite": True})
        self.ok("tâche cochée : faite + date de réalisation", st == 200 and j.get("faite") is True and j.get("fait_le"), j)
        st, _, _, j = self.poster("/api/taches/modifier", {"id": ident, "titre": "Devis envoyé"})
        self.ok("tâche modifiée : les autres champs sont conservés",
                st == 200 and j.get("titre") == "Devis envoyé" and j.get("echeance") == "2026-10-20"
                and j.get("priorite") == "haute" and j.get("faite") is True, j)
        refus = (("titre vide", "/api/taches/creer", {"titre": "   "}),
                 ("titre trop long", "/api/taches/creer", {"titre": "x" * 201}),
                 ("priorité inconnue", "/api/taches/creer", {"titre": "x", "priorite": "urgente"}),
                 ("date qui n'existe pas", "/api/taches/creer", {"titre": "x", "echeance": "2026-02-31"}),
                 ("date mal formée", "/api/taches/creer", {"titre": "x", "echeance": "31/12/2026"}),
                 ("heure sans date", "/api/taches/creer", {"titre": "x", "heure": "09:00"}),
                 ("heure impossible", "/api/taches/creer", {"titre": "x", "echeance": "2026-10-20", "heure": "29:00"}),
                 ("titre non textuel", "/api/taches/creer", {"titre": 42}),
                 ("identifiant malformé", "/api/taches/modifier", {"id": "../x", "titre": "y"}),
                 ("créneau sans jour", "/api/creneaux/creer", {"titre": "x", "debut": "09:00"}),
                 ("créneau fin avant début", "/api/creneaux/creer", {"titre": "x", "jour": "2026-10-20",
                                                                     "debut": "11:00", "fin": "10:00"}),
                 ("note sans titre", "/api/notes/creer", {"texte": "abc"}),
                 ("note trop longue", "/api/notes/creer", {"titre": "x", "texte": "y" * 20001}),
                 ("mode d'import inconnu", "/api/espace/importer", {"donnees": {"taches": []}, "mode": "ecraser"}),
                 ("import illisible", "/api/espace/importer", {"donnees": "{pas du json"}))
        for nom, chemin, corps in refus:
            st, _, _, j = self.poster(chemin, corps)
            self.ok("espace refuse : %s -> 400" % nom, st == 400 and (j or {}).get("message"), (st, j))
        for nom, chemin in (("tâche", "/api/taches"), ("créneau", "/api/creneaux"), ("note", "/api/notes")):
            st, _, _, j = self.poster(chemin + "/modifier", {"id": "0" * 16, "titre": "x", "jour": "2026-10-20",
                                                             "debut": "09:00"})
            self.ok("modifier un %s inconnu -> 404" % nom, st == 404, st)
            st, _, _, j = self.poster(chemin + "/supprimer", {"id": "0" * 16})
            self.ok("supprimer un %s inconnu -> 404" % nom, st == 404, st)
        st, _, _, j = self.poster("/api/notes/creer", {"titre": "Compte rendu", "texte": "# Titre\n\n- un\n- deux"})
        note = j or {}
        self.ok("note créée", st == 200 and note.get("id") and note.get("texte", "").startswith("# Titre"), note)
        st, _, _, j = self.poster("/api/creneaux/creer", {"titre": "Point d'étape", "jour": "2026-10-20",
                                                          "debut": "09:00", "fin": "10:30", "lieu": "Bureau"})
        creneau = j or {}
        self.ok("créneau créé", st == 200 and creneau.get("id") and creneau.get("fin") == "10:30", creneau)
        st, _, _, j = self.lire("/api/taches?q=devis&etat=tout")
        self.ok("recherche de tâches", st == 200 and len(j.get("taches", [])) == 1, j)
        st, _, _, j = self.lire("/api/taches?etat=a_faire")
        self.ok("filtre « à faire »", st == 200 and j.get("taches") == [], j)
        st, _, _, j = self.lire("/api/notes?q=titre")
        self.ok("recherche plein texte dans les notes", st == 200 and len(j.get("notes", [])) == 1, j)
        st, _, _, j = self.lire("/api/creneaux?du=2026-10-19&au=2026-10-21")
        self.ok("créneaux d'une semaine", st == 200 and len(j.get("creneaux", [])) == 1, j)
        st, _, _, j = self.lire("/api/creneaux?du=2026-11-01")
        self.ok("créneaux hors période", st == 200 and j.get("creneaux") == [], j)
        st, h, b, j = self.lire("/api/espace/export")
        self.ok("export JSON complet", st == 200 and j.get("format") == "cockpit-espace"
                and len(j.get("taches", [])) == 1 and len(j.get("notes", [])) == 1
                and len(j.get("creneaux", [])) == 1, j)
        export = j
        st, h, _, _ = self.lire("/api/espace/export?fichier=1")
        self.ok("export en fichier : Content-Disposition",
                st == 200 and "attachment" in h.get("Content-Disposition", ""), h.get("Content-Disposition"))
        st, _, _, j = self.poster("/api/espace/importer", {"donnees": export, "mode": "fusionner"})
        self.ok("import fusionner : tout est ajouté une seconde fois",
                st == 200 and j.get("importes", {}).get("taches") == 1 and j.get("importes", {}).get("notes") == 1, j)
        st, _, _, j = self.lire("/api/espace")
        self.ok("après fusion : 2 tâches, 2 notes, 2 créneaux",
                len(j.get("taches", [])) == 2 and len(j.get("notes", [])) == 2 and len(j.get("creneaux", [])) == 2,
                [len(j.get(k, [])) for k in ("taches", "notes", "creneaux")])
        st, _, _, j = self.poster("/api/espace/importer", {"donnees": json.dumps(export), "mode": "remplacer"})
        self.ok("import remplacer (texte JSON accepté)", st == 200 and j.get("mode") == "remplacer", j)
        st, _, _, j = self.lire("/api/espace")
        self.ok("après remplacement : 1 de chaque", len(j.get("taches", [])) == 1 and len(j.get("notes", [])) == 1
                and len(j.get("creneaux", [])) == 1, [len(j.get(k, [])) for k in ("taches", "notes", "creneaux")])
        st, _, _, j = self.poster("/api/espace/importer", None,
                                  brut=json.dumps({"donnees": {"notes": [{"titre": "x", "texte": "y" * 600000}]}}).encode())
        self.ok("import > 512 Ko -> 413", st == 413, st)
        st, _, _, j = self.poster("/api/taches/creer", None,
                                  brut=json.dumps({"titre": "x", "detail": "y" * 300000}).encode())
        self.ok("corps local > 256 Ko -> 413", st == 413, st)
        st, _, _, j = self.poster("/api/taches/creer", {"titre": "venue d'ailleurs"},
                                  entetes={"Origin": "http://evil.example"})
        self.ok("POST tâche d'une autre origine -> 403", st == 403 and (j or {}).get("erreur") == "origine_refusee", st)
        st, _, _, j = self.poster("/api/notes/creer", {"titre": "venue d'ailleurs"},
                                  entetes={"Sec-Fetch-Site": "cross-site"})
        self.ok("POST note en cross-site -> 403", st == 403, st)
        st, _, _, j = self.lire("/api/espace", entetes={"Sec-Fetch-Site": "cross-site"})
        self.ok("GET espace en cross-site -> 403", st == 403, st)
        st, _, _, j = self.lire("/api/espace")
        self.ok("rien n'a été écrit par les requêtes refusées", len(j.get("taches", [])) == 1
                and len(j.get("notes", [])) == 1, [len(j.get(k, [])) for k in ("taches", "notes")])
        self.ok("espace personnel : AUCUN appel relayé à la passerelle", self.nb_amont() == avant,
                (avant, self.nb_amont()))
        return {"taches": 1, "notes": 1, "creneaux": 1}

    def espace_apres_redemarrage_verrouille(self):
        st, _, _, j = self.lire("/api/espace")
        self.ok("cockpit redémarré : verrouillé, la clé a été oubliée",
                st == 401 and (j or {}).get("erreur") == "verrouille", (st, j))
        self.cookie = None

    def espace_apres_redemarrage(self):
        st, _, _, j = self.lire("/api/espace")
        self.ok("après redémarrage du cockpit : tâches, créneaux et notes toujours là",
                st == 200 and len(j.get("taches", [])) == 1 and len(j.get("notes", [])) == 1
                and len(j.get("creneaux", [])) == 1, j if st != 200 else [len(j.get(k, [])) for k in
                                                                          ("taches", "notes", "creneaux")])

    def routes(self):
        st, _, _, j = self.lire("/api/etat")
        self.ok("GET /api/etat -> passerelle joignable", st == 200 and j["passerelle"]["joignable"] and j["configure"], j)
        st, _, _, j = self.lire("/api/moi")
        self.ok("GET /api/moi -> 200 + expire_le", st == 200 and j.get("alias") == "Espace test" and j.get("expire_le"), j)
        st, _, b, j = self.lire("/api/catalogue")
        refs_a = [i["ref"] for i in (j or {}).get("accordes", [])]
        refs_p = [i["ref"] for i in (j or {}).get("proposes", [])]
        self.ok("GET /api/catalogue -> accordés", st == 200 and {"llm-local", "grand-conseil", "dom-marketing"} <= set(refs_a), refs_a)
        self.ok("GET /api/catalogue -> proposés sans élément non proposable",
                "table-ronde" in refs_p and "dom-cache" not in refs_p, refs_p)
        self.ok("catalogue sans source interne", b"INTERNE" not in b and b"source_interne" not in b)
        st, _, _, j = self.lire("/api/demandes")
        self.ok("GET /api/demandes -> 200 liste", st == 200 and isinstance(j, list), j)
        st, _, _, j = self.poster("/api/demander", {"ref": "dom-marketing", "question": "Quel prix ?", "mode": "rapide"})
        self.ok("POST demander rapide -> réponse + citations", st == 200 and j.get("reponse") and len(j.get("citations", [])) == 2, j)
        t = time.time()
        st, _, _, j = self.poster("/api/demander", {"ref": "guide-demarrage", "question": "Par où commencer ?", "mode": "approfondi"})
        self.ok("POST demander approfondi -> 200", st == 200 and "approfondi" in j.get("reponse", "") and time.time() - t >= 1.4, st)
        st, _, _, j = self.poster("/api/demander", {"ref": "grand-conseil", "question": "Faut-il ouvrir une 2e boutique ?", "mode": "approfondi"})
        self.ok("POST compétence grand-conseil -> réponse délibérée", st == 200 and j.get("citations"), st)
        contexte = [m for i in range(6) for m in ({"role": "user", "content": "question %d" % i},
                                                   {"role": "assistant", "content": "réponse %d" % i})]
        st, _, _, j = self.poster("/api/demander", {"ref": "llm-local", "question": "Et ensuite ?", "mode": "rapide", "contexte": contexte})
        self.ok("POST llm-local + contexte 6 échanges", st == 200 and "6 échange" in j.get("reponse", ""), j)
        st, _, _, j = self.poster("/api/demander", {"ref": "llm-local", "question": "xyz", "mode": "rapide", "contexte": contexte + contexte[:2]})
        self.ok("POST llm-local contexte 7 échanges (14 messages) -> 400", st == 400, st)
        st, _, _, j = self.poster("/api/demander", {"ref": "llm-local", "question": "xyz", "mode": "rapide",
                                                    "contexte": [{"question": "q", "reponse": "r"}]})
        self.ok("POST llm-local contexte mal formé -> 400", st == 400, st)
        st, _, _, j = self.poster("/api/demander", {"ref": "dom-juridique", "question": "xyz", "mode": "rapide"})
        self.ok("POST demander non accordé -> 403 relayé", st == 403 and j.get("erreur") == "non_accorde", j)
        st, _, _, j = self.poster("/api/demander", {"ref": "table-ronde", "question": "xyz", "mode": "approfondi"})
        self.ok("POST table-ronde (non accordée) -> 403", st == 403, st)
        for nom, corps in (("question vide", {"ref": "dom-marketing", "question": "  ", "mode": "rapide"}),
                           ("question de 2 caractères", {"ref": "dom-marketing", "question": "ab", "mode": "rapide"}),
                           ("question > 4000", {"ref": "dom-marketing", "question": "x" * 4001, "mode": "rapide"}),
                           ("mode inconnu", {"ref": "dom-marketing", "question": "xyz", "mode": "eclair"}),
                           ("ref invalide", {"ref": "../x", "question": "x", "mode": "rapide"}),
                           ("ref absente", {"question": "x", "mode": "rapide"}),
                           ("motif non texte", None)):
            if corps is None:
                st, _, _, _ = self.poster("/api/demande-acces", {"ref": "table-ronde", "motif": 12})
            else:
                st, _, _, _ = self.poster("/api/demander", corps)
            self.ok("POST %s -> 400" % nom, st == 400, st)
        st, _, _, _ = self.poster("/api/demander", None, brut=b"{pas du json")
        self.ok("POST JSON invalide -> 400", st == 400, st)
        st, _, _, _ = self.poster("/api/demander", None, brut=b"[1, 2]")
        self.ok("POST JSON non objet -> 400", st == 400, st)
        st, _, _, j = self.poster("/api/demander", None, brut=json.dumps({"ref": "dom-marketing", "question": "x" * 17000, "mode": "rapide"}).encode())
        self.ok("POST > 16 Ko -> 413", st == 413, st)
        st, _, _, _ = self.lire("/api/inexistant")
        self.ok("GET /api/inexistant -> 404", st == 404, st)
        st, _, _, _ = self.poster("/api/inexistant", {})
        self.ok("POST /api/inexistant -> 404", st == 404, st)
        st, _, _, _ = self.req("PUT", "/api/moi")
        self.ok("PUT -> 405", st == 405, st)
        st, _, _, _ = self.req("DELETE", "/api/demandes")
        self.ok("DELETE -> 405", st == 405, st)
        st, _, _, j = self.poster("/api/demande-acces", {"ref": "table-ronde", "motif": "Pour comparer des options."})
        self.ok("POST demande-acces -> en_attente", st == 200 and j.get("statut") == "en_attente", j)
        id1 = (j or {}).get("id")
        st, _, _, j = self.poster("/api/demande-acces", {"ref": "table-ronde"})
        self.ok("POST demande-acces répétée -> même demande", st == 200 and j.get("id") == id1, j)
        st, _, _, j = self.lire("/api/demandes")
        self.ok("GET /api/demandes -> contient la demande", st == 200 and any(d.get("ref") == "table-ronde" and d.get("statut") == "en_attente" for d in j), j)
        st, _, _, j = self.poster("/api/demande-acces", {"ref": "dom-inconnu"})
        self.ok("POST demande-acces inconnu -> 404 relayé", st == 404, st)
        self.controle("accorder", ref="table-ronde")
        st, _, _, j = self.lire("/api/catalogue")
        self.ok("après accord : table-ronde dans « Vos accès »", any(i["ref"] == "table-ronde" for i in j["accordes"]), st)
        st, _, _, j = self.lire("/api/demandes")
        self.ok("après accord : statut accordee", any(d.get("ref") == "table-ronde" and d.get("statut") == "accordee" for d in j), j)
        st, _, b, j = self.poster("/api/demander", {"ref": "dom-marketing", "question": "ECHO_CLE test", "mode": "rapide"})
        self.ok("écho de la clé par la passerelle -> masquée", st == 200 and "[masqué]" in j.get("reponse", "")
                and not any(c.encode() in b for c in self.cles.values()), j.get("reponse") if j else st)
        # quota du jour
        codes = []
        for _ in range(12):
            st, _, _, j = self.poster("/api/demander", {"ref": "dom-marketing", "question": "quota", "mode": "rapide"})
            codes.append(st)
            if st == 429:
                break
        self.ok("quota du jour épuisé -> 429 relayé", codes[-1] == 429 and j.get("erreur") == "quota" and j.get("message"), codes)
        self.controle("debit")
        # débit 30/min
        codes = [self.lire("/api/moi")[0] for _ in range(34)]
        self.ok("débit > 30/min -> 429", 429 in codes and codes[0] == 200, codes.count(429))
        self.controle("debit")
        # en-têtes transmis à la passerelle
        journal = self.controle("etat")["journal"]
        amont = [e for e in journal if e["chemin"].startswith("/v1/") and e["chemin"] != "/v1/sante"]
        auth_ok = all(e["entetes"].get("Authorization", "").startswith("Bearer ") for e in amont) and any(
            e["entetes"].get("Authorization") == "Bearer " + self.cles["valide"] for e in amont)
        fuites = [k for e in amont for k in e["entetes"] if k.lower() in ("origin", "cookie", "sec-fetch-site", "referer")]
        self.ok("passerelle : Authorization Bearer sur chaque appel authentifié", amont and auth_ok, len(amont))
        self.ok("passerelle : aucun en-tête du navigateur relayé", not fuites, fuites)
        sante = [e for e in journal if e["chemin"] == "/v1/sante"]
        self.ok("passerelle : /v1/sante appelé sans clé", sante and all("Authorization" not in e["entetes"] for e in sante), len(sante))

    def delais(self, longs=False):
        self.controle("delai_moi", secondes=20)
        t = time.time()
        st, _, _, j = self.lire("/api/moi")
        duree = time.time() - t
        self.ok("lecture lente (>15 s) -> 504 delai_depasse", st == 504 and j.get("erreur") == "delai_depasse" and 14 <= duree <= 19, (st, round(duree, 1)))
        self.controle("delai_moi", secondes=0)
        if not longs:
            return
        resultats = {}

        def lancer(nom, secondes):
            t0 = time.time()
            st, _, _, j = self.poster("/api/demander", {"ref": "dom-marketing", "question": "ATTENTE:%d" % secondes, "mode": "approfondi"})
            resultats[nom] = (st, (j or {}).get("erreur"), round(time.time() - t0, 1))

        def lancer_rapide(nom, secondes):
            t0 = time.time()
            st, _, _, j = self.poster("/api/demander", {"ref": "dom-marketing", "question": "ATTENTE:%d" % secondes, "mode": "rapide"})
            resultats[nom] = (st, (j or {}).get("erreur"), round(time.time() - t0, 1))

        fils = [threading.Thread(target=lancer, args=("60", 60)), threading.Thread(target=lancer, args=("75", 75)),
                threading.Thread(target=lancer_rapide, args=("r28", 28)), threading.Thread(target=lancer_rapide, args=("r35", 35))]
        for f in fils:
            f.start()
        for f in fils:
            f.join()
        self.ok("approfondi de 60 s -> 200 (budget passerelle 55 s couvert)", resultats["60"][0] == 200, resultats["60"])
        st, err, d = resultats["75"]
        self.ok("approfondi de 75 s -> 504 vers 70 s", st == 504 and err == "delai_depasse" and 68 <= d <= 74, resultats["75"])
        self.ok("rapide de 28 s -> 200 (budget passerelle 25 s couvert)", resultats["r28"][0] == 200, resultats["r28"])
        st, err, d = resultats["r35"]
        self.ok("rapide de 35 s -> 504 vers 30 s", st == 504 and err == "delai_depasse" and 28 <= d <= 34, resultats["r35"])
        self.controle("debit")

    def expiration(self):
        self.controle("expirer", qui="valide")
        st, _, _, j = self.lire("/api/moi")
        self.ok("accès expiré : /api/moi -> 401 acces_expire", st == 401 and j.get("erreur") == "acces_expire"
                and j.get("message") == "Votre accès a expiré.", j)
        self.ok("accès expiré : la clé quitte la mémoire (cockpit reverrouillé)", self.cookie is None, self.cookie)
        for chemin in ("/api/catalogue", "/api/demandes", "/api/taches", "/api/espace"):
            st, _, _, j = self.lire(chemin)
            self.ok("accès expiré : %s -> 401 verrouille" % chemin,
                    st == 401 and (j or {}).get("erreur") == "verrouille", (chemin, st))
        st, _, _, j = self.poster("/api/demander", {"ref": "dom-marketing", "question": "xyz", "mode": "rapide"})
        self.ok("accès expiré : demander -> 401", st == 401, st)
        st, _, _, j = self.poster("/api/demande-acces", {"ref": "dom-juridique"})
        self.ok("accès expiré : demande-acces -> 401", st == 401, st)
        st, _, _, j = self.poster("/api/deverrouiller", {"cle": self.cles["valide"]})
        self.ok("accès expiré : impossible de rouvrir le cockpit avec la même clé",
                st == 401 and (j or {}).get("erreur") == "acces_expire", (st, j))

    def telemetrie(self, minimum=1, os_attendu=None):
        t = self.controle("etat")["telemetrie"]
        self.ok("télémétrie reçue par la passerelle (>= %d)" % minimum, len(t) >= minimum, len(t))
        if t:
            m = t[-1]["mesures"]
            plausible = (m["version_client"] == VERSION_CLIENT and (m["coeurs"] or 0) >= 1 and (m["ram_total_mo"] or 0) > 0
                         and m["uptime_s"] is not None and m["disque_total_go"])
            self.ok("télémétrie : champs du contrat plausibles", plausible, m)
            self.ok("télémétrie : aucun nom de machine", "nom_machine" not in m and
                    not any(isinstance(v, str) and v.lower() == socket.gethostname().lower() for v in m.values()), m)
            if os_attendu:
                self.ok("télémétrie : os = %s" % os_attendu, m["os"] == os_attendu, m["os"])
            journal = self.controle("etat")["journal"]
            tel = [e for e in journal if e["chemin"] == "/v1/telemetrie"]
            self.ok("télémétrie : envoyée avec la clé", tel and all(e["entetes"].get("Authorization", "").startswith("Bearer ") for e in tel))

    def telemetrie_verrouillee(self, secondes=8):
        """Cockpit verrouillé = rien ne sort : aucun nouvel envoi de télémétrie."""
        n1 = len(self.controle("etat")["telemetrie"])
        time.sleep(secondes)
        n2 = len(self.controle("etat")["telemetrie"])
        self.ok("cockpit verrouillé : aucune télémétrie envoyée en %d s" % secondes, n1 == n2, (n1, n2))

    def sans_fuite(self):
        fuites = []
        for methode, chemin, st, h, b in self.reponses:
            tout = b + "".join("%s: %s\n" % kv for kv in h).encode()
            for nom, c in self.cles.items():
                if c.encode() in tout:
                    fuites.append((methode, chemin, nom))
        self.ok("aucune clé dans %d réponses HTTP (corps + en-têtes)" % len(self.reponses), not fuites, fuites)

    def bilan(self):
        ko = [r for r in self.resultats if not r[1]]
        return len(self.resultats), ko


def port_libre(exclus=(8600, 8610, 18900)):
    while True:
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        p = s.getsockname()[1]
        s.close()
        if p not in exclus:
            return p


def attendre_port(port, delai=20):
    fin = time.time() + delai
    while time.time() < fin:
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return True
        except OSError:
            time.sleep(0.2)
    return False
