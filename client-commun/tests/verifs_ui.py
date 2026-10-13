"""Vérifications de l'INTERFACE déverrouillée, pilotée dans Chrome (module de test, jamais livré).

Le navigateur ne connaît jamais la clé : on lui donne seulement le cookie de session opaque
obtenu par le serveur local, exactement comme après un déverrouillage à l'écran.
"""
import base64
import os
import shutil
import tempfile
import time

import pilote_cdp


def navigateur_disponible():
    return shutil.which("google-chrome") or shutil.which("chromium")


def capture(nav, nom):
    """Capture PNG facultative (variable COCKPIT_CAPTURES = dossier) pour contrôler le rendu à l'œil."""
    dossier = os.environ.get("COCKPIT_CAPTURES")
    if not dossier:
        return
    os.makedirs(dossier, exist_ok=True)
    r = nav.ws.appeler("Page.captureScreenshot", format="png")
    with open(os.path.join(dossier, nom + ".png"), "wb") as f:
        f.write(base64.b64decode(r.get("data", "")))


def interface_deverrouillee(banc, chrome=None, profil=None):
    """Ouvre l'interface avec la session en cours et exerce chaque rubrique."""
    chrome = chrome or navigateur_disponible()
    ok = banc.ok
    if not chrome:
        return False
    if not banc.cookie and not banc.deverrouiller():
        return False
    url = "http://127.0.0.1:%d/" % banc.port
    propre = profil is None
    profil = profil or tempfile.mkdtemp(prefix="cdp-", dir=os.environ.get("COCKPIT_TEST_TMP_COURT") or None)
    nav = None
    try:
        nav = pilote_cdp.Navigateur(chrome, profil)
        nav.cookie("cockpit_session", banc.cookie, url)
        nav.aller(url, 3.0)
        d = nav.dom()
        ok("interface : ouverte avec la session, identité et catalogue affichés",
           "Espace test" in d and "Marketing et commercialisation" in d and "Grand conseil" in d
           and "Assistant conversationnel" in d, d[:200])
        ok("interface : aucune erreur d'affichage", "Erreur d'affichage" not in d)
        ok("interface : écran de verrouillage masqué", 'id="ecran-verrou" class="ecran-verrou" hidden=""' in d)
        ok("interface : échéance de l'accès et compte à rebours",
           "Accès valable jusqu" in d and "Il reste" in d, d[d.find('id="echeance-date"'):][:200])
        time.sleep(3.0)   # la première mesure de la machine prend ~1 à 2 s
        salut = nav.evaluer("document.querySelector('#salutation').textContent")
        ok("interface : salutation personnalisée avec l'alias",
           salut in ("Bonjour Espace test", "Bonsoir Espace test"), salut)
        resume = nav.evaluer("document.querySelector('#accueil-machine').textContent")
        ok("interface : résumé de la machine sous la salutation", "Go de mémoire" in (resume or "")
           and "voir le détail" in (resume or ""), resume)
        capture(nav, "1-accueil")

        # ---- Mes tâches
        nav.evaluer("document.querySelector('.rubrique[data-vue=\"taches\"]').click()")
        demain = time.strftime("%Y-%m-%d", time.localtime(time.time() + 86400))
        nav.evaluer("""(() => {
          document.querySelector('#tache-titre').value = 'Préparer la facture « Été »';
          document.querySelector('#tache-echeance').value = '%s';
          document.querySelector('#tache-priorite').value = 'haute';
          document.querySelector('#form-tache').dispatchEvent(new Event('submit', {cancelable: true}));
        })()""" % demain)
        time.sleep(1.5)
        d = nav.dom()
        ok("interface : tâche ajoutée et affichée (caractères accentués)",
           "Préparer la facture « Été »" in d and "Tâche ajoutée" in d, d[d.find('id="liste-taches"'):][:300])
        coche = nav.evaluer("""(() => {
          const c = document.querySelector('#liste-taches input[type=checkbox]');
          c.checked = true; c.dispatchEvent(new Event('change'));
          return true;
        })()""")
        time.sleep(1.2)
        d = nav.dom()
        ok("interface : tâche cochée -> « Faite »", coche and "Faite" in d, d[d.find('id="liste-taches"'):][:300])

        # ---- Mon planning
        nav.evaluer("document.querySelector('.rubrique[data-vue=\"planning\"]').click()")
        time.sleep(0.8)
        d = nav.dom()
        ok("interface : planning alimenté par l'échéance de la tâche", "Voir la tâche" in d,
           d[d.find('id="semaine"'):][:400])

        # ---- Mes notes (Markdown)
        nav.evaluer("document.querySelector('.rubrique[data-vue=\"notes\"]').click()")
        nav.evaluer("""(() => {
          document.querySelector('#note-titre').value = 'Compte rendu';
          document.querySelector('#note-texte').value = '# Titre\\n\\n- un **gras**\\n- deux `code`';
          document.querySelector('#form-note').dispatchEvent(new Event('submit', {cancelable: true}));
        })()""")
        time.sleep(1.5)
        nav.evaluer("document.querySelector('#note-apercu').click()")
        time.sleep(0.5)
        d = nav.dom()
        ok("interface : note enregistrée", "Note enregistrée." in d, d[d.find('id="note-message"'):][:200])
        ok("interface : aperçu Markdown en HTML (sans innerHTML)",
           "<h3>Titre</h3>" in d and "<strong>gras</strong>" in d and "<code>code</code>" in d,
           d[d.find('id="note-rendu"'):][:300])

        # ---- « Demander à l'assistant » depuis une note
        nav.evaluer("document.querySelector('#note-assistant').click()")
        time.sleep(0.5)
        valeur = nav.evaluer("document.querySelector('#liste-acces [data-fil] textarea').value")
        ok("interface : « Demander à l'assistant » pré-remplit l'assistant",
           bool(valeur) and "Compte rendu" in valeur and "Titre" in valeur, valeur)
        ok("interface : la rubrique Assistants est affichée",
           nav.evaluer("document.getElementById('vue-assistants').hidden") is False)

        # ---- liste d'assistants rangée (catégories repliables, recherche, une seule saisie)
        groupes = nav.evaluer("document.querySelectorAll('#liste-acces details.groupe').length")
        ok("interface : accès rangés par catégorie repliable", (groupes or 0) >= 2, groupes)
        saisies = nav.evaluer("document.querySelectorAll('#liste-acces textarea').length")
        ok("interface : une seule zone de saisie ouverte à la fois", saisies == 1, saisies)
        filtre = nav.evaluer("""(() => {
          const r = document.querySelector('#liste-acces input.recherche');
          const compter = () => [...document.querySelectorAll('#liste-acces ul.entrees > li')]
            .filter((li) => !li.hidden).length;
          r.value = 'programmation'; r.dispatchEvent(new Event('input'));
          const n = compter();
          r.value = ''; r.dispatchEvent(new Event('input'));
          return [n, compter()];
        })()""")
        ok("interface : recherche dans la liste (1 résultat, puis liste complète)",
           isinstance(filtre, list) and filtre[0] == 1 and filtre[1] >= 5, filtre)

        # ---- Outils
        nav.evaluer("document.querySelector('.rubrique[data-vue=\"outils\"]').click()")
        time.sleep(0.5)
        ok("interface : 7 outils", nav.evaluer("document.querySelectorAll('#liste-outils details').length") == 7)
        empreinte = nav.evaluer("""(() => {
          const o = [...document.querySelectorAll('#liste-outils details')].find((x) => x.textContent.indexOf('SHA-256') >= 0);
          o.open = true; o.dispatchEvent(new Event('toggle'));
          o.querySelector('textarea').value = 'abc';
          [...o.querySelectorAll('button')].find((b) => b.textContent.indexOf('Calculer') >= 0).click();
          return o.querySelector('.sortie').textContent;
        })()""")
        ok("interface : outil SHA-256 conforme (vecteur « abc »)",
           empreinte == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", empreinte)
        tva = nav.evaluer("""(() => {
          const o = [...document.querySelectorAll('#liste-outils details')].find((x) => x.textContent.indexOf('TVA') >= 0);
          o.open = true; o.dispatchEvent(new Event('toggle'));
          o.querySelectorAll('input')[0].value = '1000';
          [...o.querySelectorAll('button')].find((b) => b.textContent.indexOf('HT') === 0).click();
          return o.querySelector('.sortie').textContent;
        })()""")
        sans_espaces = "".join((tva or "").split()).replace("\u202f", "").replace("\u00a0", "")
        ok("interface : outil TVA (1000 HT -> 1200 TTC)", "1200,00" in sans_espaces, tva)

        # ---- Ma machine
        nav.evaluer("document.querySelector('.rubrique[data-vue=\"machine\"]').click()")
        time.sleep(2.5)
        m = nav.evaluer("""(() => ({
          vue: !document.getElementById('vue-machine').hidden,
          libelle: document.querySelector('#rubrique-machine').textContent,
          cpu: document.querySelector('#machine-cpu').textContent,
          ram: document.querySelector('#machine-ram').textContent,
          disque: document.querySelector('#machine-disque').textContent,
          os: document.querySelector('#machine-os').textContent,
          version: document.querySelector('#machine-version').textContent,
          conseils: document.querySelectorAll('#machine-conseils li.conseil').length,
          barres: [...document.querySelectorAll('#vue-machine progress')].filter((b) => !b.hidden).length,
        }))()""")
        ok("interface : rubrique machine affichée (libellé adapté au système)",
           m and m["vue"] and m["libelle"] in ("Mon Mac", "Mon PC", "Ma machine"), m)
        ok("interface : processeur, mémoire, disque et système renseignés",
           m and m["cpu"] not in ("", "–") and "Go" in m["ram"] and "Go libres" in m["disque"] and m["os"] not in ("", "–"), m)
        ok("interface : version du cockpit affichée", m and m["version"] == "1.5.0", m)
        ok("interface : conseils affichés et jauges visibles", m and m["conseils"] >= 1 and m["barres"] == 2, m)
        ajout = nav.evaluer("""(async () => {
          const b = [...document.querySelectorAll('#machine-conseils button')].find((x) => x.textContent === 'Ajouter à mes tâches');
          if (!b) return null;
          const titre = b.closest('li').querySelector('span').textContent;
          b.click();
          await new Promise((r) => setTimeout(r, 1200));
          return [b.textContent, titre];
        })()""")
        ok("interface : « Ajouter à mes tâches » depuis un conseil", ajout and ajout[0] == "Ajoutée à mes tâches", ajout)
        capture(nav, "2-machine")
        nav.evaluer("document.querySelector('.rubrique[data-vue=\"taches\"]').click()")
        time.sleep(1.2)
        d = nav.dom()
        ok("interface : la tâche issue du conseil apparaît dans « Mes tâches »",
           "Essayer un modèle d" in d, d[d.find('id="liste-taches"'):][:300])
        nav.evaluer("document.querySelector('.rubrique[data-vue=\"machine\"]').click()")
        time.sleep(1.5)
        deja = nav.evaluer("[...document.querySelectorAll('#machine-conseils button')].map((b) => b.textContent)")
        ok("interface : pas de doublon (« Déjà dans mes tâches »)", deja and "Déjà dans mes tâches" in deja, deja)

        # ---- bouton « Verrouiller »
        nav.evaluer("document.querySelector('#verrouiller').click()")
        time.sleep(1.5)
        d = nav.dom()
        ok("interface : « Verrouiller » ramène à l'écran de verrouillage et efface tout",
           '<main id="principal" hidden=""' in d and "Collez votre clé" in d
           and "Marketing et commercialisation" not in d and "Compte rendu" not in d
           and "Préparer la facture" not in d, d[:200])
        vide = nav.evaluer("""(() => [document.querySelector('#salutation').textContent,
          document.querySelector('#accueil-machine').textContent, document.querySelector('#machine-cpu').textContent,
          document.querySelectorAll('#machine-conseils li').length])()""")
        ok("interface : verrouillé, salutation et mesures de la machine effacées", vide == ["", "", "", 0], vide)
        ok("interface : aucune clé dans le DOM", not any(c in d for c in banc.cles.values()))
        banc.cookie = None
        return True
    finally:
        if nav:
            nav.fermer()
        if propre:
            shutil.rmtree(profil, ignore_errors=True)
