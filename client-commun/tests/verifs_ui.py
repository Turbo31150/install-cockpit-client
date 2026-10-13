"""Vérifications de l'INTERFACE déverrouillée, pilotée dans Chrome (module de test, jamais livré).

Le navigateur ne connaît jamais la clé : on lui donne seulement le cookie de session opaque
obtenu par le serveur local, exactement comme après un déverrouillage à l'écran.
"""
import os
import shutil
import tempfile
import time

import pilote_cdp


def navigateur_disponible():
    return shutil.which("google-chrome") or shutil.which("chromium")


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

        # ---- bouton « Verrouiller »
        nav.evaluer("document.querySelector('#verrouiller').click()")
        time.sleep(1.5)
        d = nav.dom()
        ok("interface : « Verrouiller » ramène à l'écran de verrouillage et efface tout",
           '<main id="principal" hidden=""' in d and "Collez votre clé" in d
           and "Marketing et commercialisation" not in d and "Compte rendu" not in d
           and "Préparer la facture" not in d, d[:200])
        ok("interface : aucune clé dans le DOM", not any(c in d for c in banc.cles.values()))
        banc.cookie = None
        return True
    finally:
        if nav:
            nav.fermer()
        if propre:
            shutil.rmtree(profil, ignore_errors=True)
