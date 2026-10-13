'use strict';
/* Votre cockpit : noyau de l'interface (rubriques, assistants, accès).
   Toutes les requêtes passent par le serveur local (/api/...), qui seul détient la clé d'accès.
   Les rubriques « Mes tâches », « Mon planning », « Mes notes » (espace.js) et « Outils »
   (outils.js) ne sortent jamais de cet ordinateur. Aucune ressource externe n'est chargée. */
(function () {
  const CLE_HISTO = 'cockpit.historique.v1';
  const CLE_FILS = 'cockpit.fils.v1';
  const CLE_THEME = 'cockpit.affichage.v1';
  const MAX_HISTO = 60;
  const MAX_TOURS_FIL = 40;
  const MAX_TOURS_CONTEXTE = 6;
  const MAX_QUESTION = 4000;
  const NATURES = { domaine: 'Domaine', guide: 'Guide', competence: 'Compétence' };
  const ABONNEMENTS = { aucun: 'Sans abonnement', decouverte: 'Découverte', pro: 'Pro', expert: 'Expert' };
  const STATUTS = { en_attente: 'En attente', accordee: 'Accordée', refusee: 'Refusée' };
  const BOUTONS_LONGS = { 'grand-conseil': 'Soumettre au conseil', 'table-ronde': 'Lancer la table ronde' };
  const CHATS = new Set(['llm-local', 'llm-code']);  // compétences en fil de discussion
  const TRES_LONGS = new Set(['table-ronde']);       // budget du serveur local : 200 s
  const RUBRIQUES = ['assistants', 'taches', 'planning', 'notes', 'outils', 'acces'];

  const etat = { expire: false, verrouille: true, expireLe: null, demandes: [], minuteurs: [], titres: {},
                 vue: null, connexion: null, boucles: false, pret: false };
  const vues = {};
  let compteur = 0;

  const $ = (s) => document.querySelector(s);
  function el(tag, attrs, ...enfants) {
    const n = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === false || v === null || v === undefined) continue;
      if (k === 'class') n.className = v;
      else if (k === 'texte') n.textContent = v;
      else n.setAttribute(k, v === true ? '' : String(v));
    }
    for (const e of enfants) if (e !== null && e !== undefined) n.append(e);
    return n;
  }
  const idUnique = (p) => p + '-' + (++compteur);
  const liste = (d, cle) => (Array.isArray(d) ? d : (d && Array.isArray(d[cle]) ? d[cle] : []));
  const tableau = (v) => (Array.isArray(v) ? v : []);

  // ------------------------------------------------------------------ stockage du navigateur (historique, affichage)
  function lire(cle, defaut) {
    try { const v = JSON.parse(localStorage.getItem(cle)); return v === null ? defaut : v; } catch (_) { return defaut; }
  }
  function ecrire(cle, valeur) {
    try { localStorage.setItem(cle, JSON.stringify(valeur)); } catch (_) { /* stockage plein ou interdit */ }
  }

  // ------------------------------------------------------------------ appels au serveur local
  async function api(chemin, options) {
    options = options || {};
    const init = { method: 'GET', headers: { Accept: 'application/json' }, cache: 'no-store', credentials: 'same-origin' };
    if (options.corps !== undefined) {
      init.method = 'POST';
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(options.corps);
    }
    const ctrl = new AbortController();
    const t = setTimeout(() => ctrl.abort(), options.delai || 30000);
    init.signal = ctrl.signal;
    let rep = null, donnees = null;
    try {
      rep = await fetch(chemin, init);
      try { donnees = await rep.json(); } catch (_) { donnees = null; }
    } catch (e) {
      const delai = e && e.name === 'AbortError';
      return { ok: false, statut: 0, donnees: {
        erreur: delai ? 'delai_depasse' : 'cockpit_injoignable',
        message: delai ? "Le délai d'attente est dépassé. Réessayez dans un moment."
                       : 'Le cockpit local ne répond pas. Patientez quelques secondes puis rechargez la page.' } };
    } finally {
      clearTimeout(t);
    }
    if (rep.status === 401 && donnees && donnees.erreur === 'acces_expire') basculerExpire();
    else if (rep.status === 401 && donnees && donnees.erreur === 'verrouille') basculerVerrou(donnees.message);
    return { ok: rep.ok, statut: rep.status, donnees: donnees || {} };
  }
  function messageErreur(r) {
    const d = r.donnees || {};
    if (d.message) return d.message;
    if (r.statut === 429) return 'Limite atteinte. Réessayez plus tard.';
    if (r.statut === 403) return 'Cet élément ne fait pas partie de vos accès.';
    return 'Une erreur est survenue (code ' + (r.statut || '?') + ').';
  }

  // ------------------------------------------------------------------ bandeau d'information
  const messagesBandeau = new Map();
  function bandeau(cle, texte) {
    if (texte) messagesBandeau.set(cle, texte); else messagesBandeau.delete(cle);
    const b = $('#bandeau');
    b.replaceChildren(...[...messagesBandeau.values()].map((m) => el('p', { texte: m })));
    b.hidden = etat.expire || messagesBandeau.size === 0;
  }

  // ------------------------------------------------------------------ erreurs d'affichage
  function signalerErreur(texte) {
    const n = $('#journal-erreurs');
    if (!n) return;
    n.hidden = false;
    n.textContent = "Erreur d'affichage : " + String(texte || 'inconnue').slice(0, 200)
      + ' — rechargez la page (Cmd+R ou Ctrl+R).';
  }
  window.addEventListener('error', (e) => signalerErreur(e && e.message ? e.message : 'inconnue'));
  window.addEventListener('unhandledrejection', () => signalerErreur('traitement interrompu'));

  // ------------------------------------------------------------------ rubriques
  function enregistrerVue(nom, gestion) {
    vues[nom] = gestion || {};
  }
  function aller(nom, options) {
    if (RUBRIQUES.indexOf(nom) < 0) nom = 'assistants';
    etat.vue = nom;
    for (const r of RUBRIQUES) {
      const section = document.getElementById('vue-' + r);
      if (section) section.hidden = r !== nom;
      const bouton = document.querySelector('.rubrique[data-vue="' + r + '"]');
      if (!bouton) continue;
      bouton.classList.toggle('actif', r === nom);
      if (r === nom) bouton.setAttribute('aria-current', 'page'); else bouton.removeAttribute('aria-current');
    }
    if (location.hash !== '#/' + nom) {
      try { history.replaceState(null, '', '#/' + nom); } catch (_) { location.hash = '#/' + nom; }
    }
    const v = vues[nom];
    if (v && v.activer) {
      try { v.activer(options || {}); } catch (e) { signalerErreur(e && e.message ? e.message : 'rubrique ' + nom); }
    }
  }
  function vueDemandee() {
    const h = String(location.hash || '').replace(/^#\/?/, '');
    return RUBRIQUES.indexOf(h) >= 0 ? h : 'assistants';
  }

  // ------------------------------------------------------------------ verrou (la clé ouvre le cockpit)
  function afficherEcran(nom) {
    $('#ecran-verrou').hidden = nom !== 'verrou';
    $('#principal').hidden = nom !== 'app';
    $('#ecran-expire').hidden = nom !== 'expire';
    $('#pied').hidden = nom === 'expire';
    $('#identite').hidden = nom !== 'app';
    $('#connexion').hidden = nom === 'expire';
    $('#verrouiller').hidden = nom !== 'app';
    const t = $('#theme');
    if (t) t.parentElement.hidden = nom === 'expire';
  }
  function viderEcran() {
    // Cockpit verrouillé : plus rien de personnel ne reste affiché ni saisi.
    for (const s of ['#liste-acces', '#liste-proposes', '#liste-demandes', '#historique', '#liste-taches',
                     '#liste-notes', '#semaine', '#liste-outils', '#note-rendu']) {
      const n = $(s);
      if (n) n.replaceChildren();
    }
    for (const s of ['#alias', '#abonnement', '#acces-alias', '#acces-abonnement', '#quota', '#echeance-date',
                     '#echeance-reste', '#acces-connexion', '#acces-connexion-note', '#taches-compte',
                     '#tache-message', '#note-message', '#creneau-message', '#message-donnees']) {
      const n = $(s);
      if (n) n.textContent = '';
    }
    for (const s of ['#note-titre', '#note-texte', '#tache-titre', '#tache-detail', '#note-recherche',
                     '#tache-recherche', '#creneau-titre', '#creneau-lieu', '#verrou-cle']) {
      const n = $(s);
      if (n) n.value = '';
    }
    const apercu = $('#note-apercu-zone');
    if (apercu) apercu.hidden = true;
    for (const nom of RUBRIQUES) {
      const v = vues[nom];
      if (v && v.oublier) { try { v.oublier(); } catch (_) { /* rien à oublier */ } }
    }
  }
  function basculerVerrou(message) {
    if (etat.expire) return;
    arreterBoucles();
    etat.verrouille = true;
    etat.pret = false;
    messagesBandeau.clear();
    bandeau('verrou', null);
    viderEcran();
    afficherEcran('verrou');
    $('#verrou-message').textContent = message || "Cockpit verrouillé : collez votre clé d'accès pour l'ouvrir.";
    $('#verrou-etat').textContent = '';
    $('#verrou-cle').value = '';
    document.title = 'AlkymIA · Turbo OS — Votre cockpit';
    try { $('#verrou-cle').focus(); } catch (_) { /* onglet en arrière-plan */ }
    if (!etat.sondeVerrou) etat.sondeVerrou = setInterval(sonderVerrou, 20000);
    sonderVerrou();
  }
  async function sonderVerrou() {
    const r = await api('/api/etat', { delai: 20000 });
    if (!etat.verrouille) return;
    const d = r.donnees || {};
    const pass = d.passerelle || {};
    $('#verrou-reseau').textContent = r.ok
      ? (pass.joignable ? 'Service distant joignable.' : (pass.message || 'Service distant injoignable.'))
      : (d.message || 'Le cockpit local ne répond pas.');
    if (r.ok && d.verrouille === false) ouvrirInterface();
  }
  function ouvrirInterface() {
    etat.verrouille = false;
    if (etat.sondeVerrou) { clearInterval(etat.sondeVerrou); etat.sondeVerrou = null; }
    afficherEcran('app');
    aller(etat.vue || vueDemandee());
    demarrerBoucles();
  }
  async function deverrouiller() {
    const champ = $('#verrou-cle');
    const bouton = $('#verrou-ouvrir');
    const info = $('#verrou-etat');
    const cle = String(champ.value || '').replace(/\s+/g, '');
    if (!cle) { info.textContent = "Collez votre clé d'accès."; champ.focus(); return; }
    bouton.disabled = true;
    info.textContent = 'Vérification de la clé…';
    const r = await api('/api/deverrouiller', { corps: { cle: cle }, delai: 30000 });
    bouton.disabled = false;
    champ.value = '';
    if (!r.ok) {
      info.textContent = '';
      $('#verrou-message').textContent = messageErreur(r);
      champ.focus();
      return;
    }
    info.textContent = '';
    ouvrirInterface();
    if (r.donnees && r.donnees.alias) afficherMoi(r.donnees);
  }
  async function verrouiller() {
    await api('/api/verrouiller', { corps: {} });
    basculerVerrou('Cockpit verrouillé. Votre clé n\'est plus en mémoire.');
  }

  // ------------------------------------------------------------------ état de connexion
  async function majConnexion() {
    if (etat.expire || etat.verrouille) return;
    const r = await api('/api/etat', { delai: 20000 });
    const p = $('#connexion');
    let texte = 'Hors ligne', classe = 'pastille ko', note = '';
    if (!r.ok) {
      texte = 'Cockpit local injoignable';
      note = messageErreur(r);
      bandeau('connexion', note);
    } else {
      const pass = r.donnees.passerelle || {};
      if (pass.joignable) {
        texte = 'Connecté';
        classe = 'pastille ok';
        bandeau('connexion', null);
      } else {
        note = pass.message || 'Le service distant est injoignable.';
        bandeau('connexion', note);
      }
    }
    p.textContent = texte;
    p.className = classe;
    etat.connexion = texte;
    $('#acces-connexion').textContent = texte;
    $('#acces-connexion-note').textContent = note;
  }

  // ------------------------------------------------------------------ identité, quota, échéance
  function afficherMoi(m) {
    const alias = m.alias || 'Votre espace';
    const abonnement = 'Abonnement ' + (ABONNEMENTS[m.abonnement] || m.abonnement || 'inconnu');
    $('#alias').textContent = alias;
    $('#abonnement').textContent = abonnement;
    $('#acces-alias').textContent = alias;
    $('#acces-abonnement').textContent = abonnement;
    const q = Number(m.quota_jour), u = Number(m.utilise_aujourdhui) || 0;
    const barre = $('#quota-barre');
    if (Number.isFinite(q) && q > 0) {
      $('#quota').textContent = u + ' sur ' + q;
      barre.max = q; barre.value = Math.min(u, q); barre.hidden = false;
    } else if (q === 0) {
      $('#quota').textContent = 'Aucune question incluse';
      barre.hidden = true;
    } else {
      $('#quota').textContent = String(u);
      barre.hidden = true;
    }
    const d = m.expire_le ? new Date(m.expire_le) : null;
    etat.expireLe = d && !isNaN(d.getTime()) ? d : null;
    majEcheance();
  }
  function duree(ms) {
    const min = Math.floor(ms / 60000);
    const j = Math.floor(min / 1440), h = Math.floor((min % 1440) / 60), mn = min % 60;
    if (j) return j + ' j ' + h + ' h ' + mn + ' min';
    if (h) return h + ' h ' + mn + ' min';
    if (mn) return mn + ' min';
    return "moins d'une minute";
  }
  function majEcheance() {
    if (etat.expire) return;
    const bloc = $('#echeance');
    if (!etat.expireLe) {
      $('#echeance-date').textContent = 'Sans date de fin indiquée';
      $('#echeance-reste').textContent = '';
      bloc.classList.remove('alerte');
      return;
    }
    const date = new Intl.DateTimeFormat('fr-FR', { dateStyle: 'full', timeStyle: 'short' }).format(etat.expireLe);
    const reste = etat.expireLe.getTime() - Date.now();
    $('#echeance-date').textContent = "Accès valable jusqu'au " + date;
    if (reste <= 0) {
      $('#echeance-reste').textContent = 'Échéance atteinte';
      bloc.classList.add('alerte');
      api('/api/moi');  // la passerelle confirme : l'écran « accès expiré » s'affiche si besoin
      return;
    }
    $('#echeance-reste').textContent = 'Il reste ' + duree(reste);
    bloc.classList.toggle('alerte', reste < 6 * 3600 * 1000);
  }
  async function rafraichirMoi() {
    const r = await api('/api/moi');
    if (r.ok && !etat.expire) afficherMoi(r.donnees);
  }

  // ------------------------------------------------------------------ réponses
  function indicateur(zone, texte) {
    const debut = Date.now();
    const p = el('p', { class: 'attente', role: 'status' });
    const maj = () => { p.textContent = texte + ' — ' + Math.round((Date.now() - debut) / 1000) + ' s'; };
    maj();
    zone.replaceChildren(p);
    const t = setInterval(maj, 1000);
    return () => clearInterval(t);
  }
  function blocReponse(d) {
    const bloc = el('div', { class: 'reponse' }, el('div', { class: 'reponse-texte', texte: d.reponse || '(réponse vide)' }));
    const cites = Array.isArray(d.citations) ? d.citations : [];
    if (cites.length) {
      bloc.append(el('p', { class: 'citations-titre', texte: 'Sources' }));
      const ul = el('ul', { class: 'citations' });
      cites.forEach((c, i) => ul.append(el('li', { texte: '[' + (c && c.n !== undefined ? c.n : i + 1) + '] ' + ((c && c.titre) || 'Source') })));
      bloc.append(ul);
    }
    if (typeof d.duree_ms === 'number' && isFinite(d.duree_ms)) {
      bloc.append(el('p', { class: 'meta', texte: 'Réponse obtenue en ' + (Number(d.duree_ms) / 1000).toFixed(1) + ' s' }));
    }
    return bloc;
  }
  async function demander(item, question, mode, zone, boutons, contexte) {
    if (question.length < 3) {
      zone.replaceChildren(el('p', { class: 'erreur', texte: 'Votre question est trop courte (3 caractères au moins).' }));
      return null;
    }
    const long = mode === 'approfondi' || item.nature === 'competence';
    let texte = long ? "Le conseil d'experts prépare une réponse détaillée (jusqu'à une minute)" : "Le conseil d'experts cherche la réponse";
    if (item.nature === 'competence') texte = CHATS.has(item.ref) ? "L'assistant rédige sa réponse" : "Délibération du conseil d'experts en cours (jusqu'à une minute)";
    boutons.forEach((b) => { b.disabled = true; });
    const arreter = indicateur(zone, texte);
    const corps = { ref: item.ref, question: question, mode: mode };
    if (contexte && contexte.length) corps.contexte = contexte;
    const tresLong = TRES_LONGS.has(item.ref);   // table ronde : 2 tours + synthèse
    const r = await api('/api/demander', { corps: corps, delai: tresLong ? 215000 : (long ? 90000 : 45000) });
    arreter();
    boutons.forEach((b) => { b.disabled = false; });
    if (etat.expire) return null;
    rafraichirMoi();
    if (!r.ok) {
      zone.replaceChildren(el('p', { class: 'erreur', texte: messageErreur(r) }));
      return null;
    }
    return r.donnees;
  }

  // ------------------------------------------------------------------ cartes « Vos accès »
  function enteteCarte(item) {
    return [
      el('div', { class: 'carte-entete' }, el('h3', { texte: item.titre || item.ref }),
        el('span', { class: 'badge', texte: NATURES[item.nature] || 'Élément' })),
      item.description ? el('p', { class: 'carte-description', texte: item.description }) : null,
    ];
  }
  function champ(libelle, attrs) {
    const id = idUnique('champ');
    const zone = el('textarea', Object.assign({ id: id }, attrs));
    return [el('label', { for: id, texte: libelle }), zone];
  }
  function raccourciEnvoi(zoneTexte, bouton) {
    zoneTexte.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey) && !bouton.disabled) { e.preventDefault(); bouton.click(); }
    });
  }

  function carteQuestion(item) {
    const [lab, zoneTexte] = champ('Votre question', { rows: 3, maxlength: MAX_QUESTION, placeholder: 'Posez votre question…' });
    const rapide = el('button', { type: 'button', class: 'bouton', texte: 'Réponse rapide' });
    const approfondi = el('button', { type: 'button', class: 'bouton secondaire', texte: 'Réponse approfondie' });
    const zone = el('div', { class: 'zone-reponse', 'aria-live': 'polite' });
    const poser = async (mode) => {
      const q = zoneTexte.value.trim();
      if (!q) { zone.replaceChildren(el('p', { class: 'erreur', texte: "Écrivez d'abord votre question." })); return; }
      const d = await demander(item, q, mode, zone, [rapide, approfondi]);
      if (!d) return;
      zone.replaceChildren(blocReponse(d));
      ajouterHistorique({ ts: Date.now(), ref: item.ref, titre: item.titre || item.ref, mode: mode, question: q,
        reponse: d.reponse || '', citations: Array.isArray(d.citations) ? d.citations : [] });
    };
    rapide.addEventListener('click', () => poser('rapide'));
    approfondi.addEventListener('click', () => poser('approfondi'));
    raccourciEnvoi(zoneTexte, rapide);
    return el('article', { class: 'carte', 'data-saisie': item.ref }, ...enteteCarte(item), lab, zoneTexte,
      el('div', { class: 'actions' }, rapide, approfondi, el('span', { class: 'aide', texte: 'Approfondie : jusqu\'à une minute.' })), zone);
  }

  function carteDeliberation(item) {
    const [lab, zoneTexte] = champ('Votre question', { rows: 4, maxlength: MAX_QUESTION, placeholder: 'Décrivez votre question ou votre situation…' });
    const bouton = el('button', { type: 'button', class: 'bouton', texte: BOUTONS_LONGS[item.ref] || 'Envoyer' });
    const zone = el('div', { class: 'zone-reponse', 'aria-live': 'polite' });
    bouton.addEventListener('click', async () => {
      const q = zoneTexte.value.trim();
      if (!q) { zone.replaceChildren(el('p', { class: 'erreur', texte: "Écrivez d'abord votre question." })); return; }
      const d = await demander(item, q, 'approfondi', zone, [bouton]);
      if (!d) return;
      zone.replaceChildren(blocReponse(d));
      ajouterHistorique({ ts: Date.now(), ref: item.ref, titre: item.titre || item.ref, mode: 'approfondi', question: q,
        reponse: d.reponse || '', citations: Array.isArray(d.citations) ? d.citations : [] });
    });
    raccourciEnvoi(zoneTexte, bouton);
    return el('article', { class: 'carte', 'data-saisie': item.ref }, ...enteteCarte(item), lab, zoneTexte,
      el('div', { class: 'actions' }, bouton, el('span', { class: 'aide', texte: 'Réponse délibérée : jusqu\'à une minute.' })), zone);
  }

  function lireFil(ref) { const f = lire(CLE_FILS, {}); return Array.isArray(f[ref]) ? f[ref] : []; }
  function ecrireFil(ref, tours) { const f = lire(CLE_FILS, {}); f[ref] = tours.slice(-MAX_TOURS_FIL); ecrire(CLE_FILS, f); }
  function contexteFil(tours) {
    // 6 derniers échanges au plus, au format messages {role: user|assistant, content}
    const paires = tours.slice(-MAX_TOURS_CONTEXTE)
      .filter((t) => t && String(t.question || '').trim() && String(t.reponse || '').trim())
      .map((t) => [{ role: 'user', content: String(t.question).slice(0, 1000) },
                   { role: 'assistant', content: String(t.reponse).slice(0, 1500) }]);
    while (paires.length && JSON.stringify(paires).length > 11000) paires.shift();
    return [].concat(...paires);
  }
  function carteFil(item) {
    const fil = el('div', { class: 'fil', role: 'log', 'aria-live': 'polite' });
    const dessiner = () => {
      fil.replaceChildren();
      for (const t of lireFil(item.ref)) {
        fil.append(el('div', { class: 'bulle moi', texte: t.question }), el('div', { class: 'bulle assistant', texte: t.reponse }));
      }
      fil.scrollTop = fil.scrollHeight;
    };
    const [lab, zoneTexte] = champ('Votre message', { rows: 2, maxlength: MAX_QUESTION, placeholder: 'Écrivez votre message…' });
    const envoyer = el('button', { type: 'button', class: 'bouton', texte: 'Envoyer' });
    const nouvelle = el('button', { type: 'button', class: 'bouton discret', texte: 'Nouvelle conversation' });
    const zone = el('div', { class: 'zone-reponse', 'aria-live': 'polite' });
    envoyer.addEventListener('click', async () => {
      const q = zoneTexte.value.trim();
      if (!q) return;
      const tours = lireFil(item.ref);
      const d = await demander(item, q, 'rapide', zone, [envoyer, nouvelle], contexteFil(tours));
      if (!d) return;
      zone.replaceChildren();
      tours.push({ ts: Date.now(), question: q, reponse: d.reponse || '' });
      ecrireFil(item.ref, tours);
      zoneTexte.value = '';
      dessiner();
    });
    nouvelle.addEventListener('click', () => { ecrireFil(item.ref, []); zone.replaceChildren(); dessiner(); });
    raccourciEnvoi(zoneTexte, envoyer);
    dessiner();
    return el('article', { class: 'carte', 'data-fil': item.ref, 'data-saisie': item.ref }, ...enteteCarte(item), fil, lab, zoneTexte,
      el('div', { class: 'actions' }, envoyer, nouvelle, el('span', { class: 'aide', texte: 'Les 6 derniers échanges sont pris en compte.' })), zone);
  }

  function carteAcces(item) {
    if (item.nature === 'competence' && CHATS.has(item.ref)) return carteFil(item);
    if (item.nature === 'competence') return carteDeliberation(item);
    return carteQuestion(item);
  }

  // ------------------------------------------------------------------ « Demander à l'assistant »
  function versAssistant(texte, titre) {
    const message = String(texte || '').trim().slice(0, MAX_QUESTION);
    if (!message) return false;
    aller('assistants');
    const carte = document.querySelector('#liste-acces [data-fil] textarea')
      || document.querySelector('#liste-acces [data-saisie] textarea');
    if (!carte) {
      bandeau('assistant', "Aucun assistant n'est ouvert sur votre accès pour le moment : demandez un accès dans « Assistants ».");
      return false;
    }
    bandeau('assistant', null);
    carte.value = (titre ? titre + '\n\n' : '') + message;
    carte.value = carte.value.slice(0, MAX_QUESTION);
    try { carte.scrollIntoView({ block: 'center' }); } catch (_) { /* navigateur ancien */ }
    carte.focus();
    carte.setSelectionRange(carte.value.length, carte.value.length);
    return true;
  }

  // ------------------------------------------------------------------ « Disponible sur demande »
  function carteProposee(item) {
    const enAttente = etat.demandes.some((d) => d && d.ref === item.ref && d.statut === 'en_attente');
    const carte = el('article', { class: 'carte' }, ...enteteCarte(item));
    if (enAttente) {
      carte.append(el('p', { class: 'aide', texte: 'Demande en attente de traitement.' }));
      return carte;
    }
    const [lab, zoneTexte] = champ('Motif (facultatif)', { rows: 2, maxlength: 1000, placeholder: 'Pourquoi cet accès vous serait utile…' });
    const bouton = el('button', { type: 'button', class: 'bouton secondaire', texte: "Demander l'accès" });
    const zone = el('div', { class: 'zone-reponse', 'aria-live': 'polite' });
    bouton.addEventListener('click', async () => {
      bouton.disabled = true;
      const r = await api('/api/demande-acces', { corps: { ref: item.ref, motif: zoneTexte.value.trim() } });
      if (etat.expire) return;
      if (!r.ok) {
        bouton.disabled = false;
        zone.replaceChildren(el('p', { class: 'erreur', texte: messageErreur(r) }));
        return;
      }
      zone.replaceChildren(el('p', { class: 'aide', texte: 'Demande envoyée : elle apparaît dans « Vos demandes ».' }));
      lab.hidden = true; zoneTexte.hidden = true; bouton.hidden = true;
      rafraichirDemandes();
    });
    carte.append(lab, zoneTexte, el('div', { class: 'actions' }, bouton), zone);
    return carte;
  }

  function afficherCatalogue(c) {
    const accordes = tableau(c && c.accordes);
    const proposes = tableau(c && c.proposes);
    etat.titres = {};
    for (const i of accordes.concat(proposes)) if (i && i.ref) etat.titres[i.ref] = i.titre || i.ref;
    const zoneA = $('#liste-acces');
    zoneA.replaceChildren(...accordes.filter((i) => i && i.ref).map(carteAcces));
    if (!zoneA.children.length) zoneA.append(el('p', { class: 'vide', texte: 'Aucun accès pour le moment. Vous pouvez en demander ci-dessous.' }));
    const zoneP = $('#liste-proposes');
    zoneP.replaceChildren(...proposes.filter((i) => i && i.ref).map(carteProposee));
    if (!zoneP.children.length) zoneP.append(el('p', { class: 'vide', texte: 'Aucun autre élément disponible pour le moment.' }));
    etat.catalogue = c;
  }

  // ------------------------------------------------------------------ « Vos demandes »
  function dateCourte(v) {
    const d = v ? new Date(v) : null;
    if (!d || isNaN(d.getTime())) return v ? String(v) : '';
    return new Intl.DateTimeFormat('fr-FR', { dateStyle: 'medium', timeStyle: 'short' }).format(d);
  }
  function afficherDemandes() {
    const zone = $('#liste-demandes');
    const dem = etat.demandes.filter((d) => d && typeof d === 'object');
    if (!dem.length) { zone.replaceChildren(el('p', { class: 'vide', texte: 'Aucune demande pour le moment.' })); return; }
    const corps = el('tbody');
    for (const d of dem) {
      corps.append(el('tr', {},
        el('td', { texte: d.titre || etat.titres[d.ref] || d.ref || '' }),
        el('td', { texte: dateCourte(d.cree_le) }),
        el('td', {}, el('span', { class: 'badge ' + (STATUTS[d.statut] ? d.statut : ''), texte: STATUTS[d.statut] || d.statut || '' }))));
    }
    zone.replaceChildren(el('table', {},
      el('thead', {}, el('tr', {}, el('th', { scope: 'col', texte: 'Élément' }), el('th', { scope: 'col', texte: 'Envoyée le' }), el('th', { scope: 'col', texte: 'Statut' }))),
      corps));
  }
  async function rafraichirDemandes() {
    const r = await api('/api/demandes');
    if (etat.expire || !r.ok) return;
    const signature = () => JSON.stringify(etat.demandes.map((d) => (d ? [d.id, d.statut] : null)));
    const avant = signature();
    etat.demandes = liste(r.donnees, 'demandes');
    afficherDemandes();
    if (avant !== signature()) chargerCatalogue();
  }

  // ------------------------------------------------------------------ historique local (navigateur)
  function ajouterHistorique(entree) {
    const h = lire(CLE_HISTO, []);
    const liste2 = Array.isArray(h) ? h : [];
    liste2.unshift(entree);
    ecrire(CLE_HISTO, liste2.slice(0, MAX_HISTO));
    afficherHistorique();
  }
  function afficherHistorique() {
    const zone = $('#historique');
    const h = lire(CLE_HISTO, []);
    const entrees = Array.isArray(h) ? h : [];
    if (!entrees.length) { zone.replaceChildren(el('p', { class: 'vide', texte: 'Aucune réponse enregistrée sur cet ordinateur.' })); return; }
    zone.replaceChildren(...entrees.map((e) => el('details', { class: 'histo' },
      el('summary', {}, el('span', { class: 'histo-date', texte: dateCourte(e.ts) }),
        document.createTextNode((e.titre || '') + ' — ' + String(e.question || '').slice(0, 90))),
      el('p', { class: 'aide', texte: 'Question : ' + (e.question || '') }),
      blocReponse({ reponse: e.reponse, citations: e.citations }))));
  }

  // ------------------------------------------------------------------ accès expiré
  function basculerExpire() {
    if (etat.expire) return;
    etat.expire = true;
    arreterBoucles();
    if (etat.sondeVerrou) { clearInterval(etat.sondeVerrou); etat.sondeVerrou = null; }
    $('#bandeau').hidden = true;
    afficherEcran('expire');
    document.title = 'AlkymIA · Turbo OS — Accès expiré';
    // Si une nouvelle clé est installée, la page revient d'elle-même.
    setInterval(async () => {
      try {
        const r = await fetch('/api/moi', { cache: 'no-store', headers: { Accept: 'application/json' } });
        if (r.ok) location.reload();
      } catch (_) { /* réessai au prochain passage */ }
    }, 5 * 60 * 1000);
  }

  // ------------------------------------------------------------------ affichage clair / sombre
  function appliquerTheme(valeur) {
    const choix = ['auto', 'clair', 'sombre'].indexOf(valeur) >= 0 ? valeur : 'auto';
    if (choix === 'auto') document.documentElement.removeAttribute('data-theme');
    else document.documentElement.setAttribute('data-theme', choix);
    const select = $('#theme');
    if (select) select.value = choix;
    ecrire(CLE_THEME, choix);
  }

  // ------------------------------------------------------------------ démarrage
  async function chargerCatalogue() {
    const r = await api('/api/catalogue');
    if (etat.expire) return;
    if (!r.ok) { bandeau('catalogue', messageErreur(r)); return; }
    bandeau('catalogue', null);
    afficherCatalogue(r.donnees);
  }
  async function chargerTout() {
    const moi = await api('/api/moi');
    if (etat.expire) return;
    if (!moi.ok) {
      bandeau('moi', messageErreur(moi));
      $('#alias').textContent = 'Accès indisponible';
      $('#acces-alias').textContent = 'Accès indisponible';
      for (const s of ['#liste-acces', '#liste-proposes', '#liste-demandes']) $(s).replaceChildren(el('p', { class: 'vide', texte: 'Indisponible pour le moment.' }));
      return false;
    }
    bandeau('moi', null);
    afficherMoi(moi.donnees);
    const dem = await api('/api/demandes');
    if (etat.expire) return false;
    etat.demandes = dem.ok ? liste(dem.donnees, 'demandes') : [];
    await chargerCatalogue();
    afficherDemandes();
    return true;
  }
  function demarrerBoucles() {
    if (etat.boucles || etat.expire || etat.verrouille) return;
    etat.boucles = true;
    majConnexion();
    if (!etat.pret) chargerTout().then((ok) => { etat.pret = !!ok; });
    etat.minuteurs.push(setInterval(majConnexion, 30000));
    etat.minuteurs.push(setInterval(majEcheance, 20000));
    etat.minuteurs.push(setInterval(() => {
      if (etat.pret) rafraichirDemandes(); else chargerTout().then((ok) => { etat.pret = !!ok; });
    }, 120000));
  }
  function arreterBoucles() {
    etat.minuteurs.forEach((t) => clearInterval(t));
    etat.minuteurs = [];
    etat.boucles = false;
  }
  function demarrer() {
    appliquerTheme(lire(CLE_THEME, 'auto'));
    $('#theme').addEventListener('change', (e) => appliquerTheme(e.target.value));
    afficherHistorique();
    $('#effacer-historique').addEventListener('click', () => {
      if (!window.confirm("Effacer l'historique des réponses et des conversations enregistré sur cet ordinateur ?")) return;
      try { localStorage.removeItem(CLE_HISTO); localStorage.removeItem(CLE_FILS); } catch (_) { /* rien */ }
      afficherHistorique();
      if (etat.catalogue) afficherCatalogue(etat.catalogue);
    });
    for (const bouton of document.querySelectorAll('.rubrique')) {
      bouton.addEventListener('click', () => aller(bouton.getAttribute('data-vue')));
    }
    window.addEventListener('hashchange', () => aller(vueDemandee()));
    $('#acces-actualiser').addEventListener('click', () => { majConnexion(); rafraichirMoi(); });
    for (const nom of RUBRIQUES) {
      const v = vues[nom];
      // une rubrique en panne n'arrête pas le cockpit, mais elle est signalée
      if (v && v.demarrer) { try { v.demarrer(); } catch (e) { signalerErreur(e && e.message ? e.message : 'rubrique ' + nom); } }
    }
    $('#form-verrou').addEventListener('submit', (e) => { e.preventDefault(); deverrouiller(); });
    $('#verrouiller').addEventListener('click', verrouiller);
    basculerVerrou(null);   // rien n'est affiché ni demandé avant que la clé soit collée
  }

  // noyau partagé avec les rubriques locales (espace.js, outils.js)
  window.CK = { $: $, el: el, api: api, messageErreur: messageErreur, bandeau: bandeau, dateCourte: dateCourte,
                enregistrerVue: enregistrerVue, aller: aller, versAssistant: versAssistant, etat: etat,
                lire: lire, ecrire: ecrire, idUnique: idUnique };

  // Les scripts « defer » s'exécutent alors que readyState vaut déjà « interactive » :
  // on attend DOMContentLoaded pour que espace.js et outils.js aient enregistré leurs rubriques.
  if (document.readyState === 'complete') demarrer();
  else document.addEventListener('DOMContentLoaded', demarrer, { once: true });
})();
