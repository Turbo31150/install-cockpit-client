'use strict';
/* Rubriques « Mes tâches », « Mon planning » et « Mes notes ».
   Tout passe par les routes locales /api/taches, /api/creneaux, /api/notes et /api/espace :
   ces données sont enregistrées sur cet ordinateur et ne partent jamais sur le réseau. */
(function () {
  const C = window.CK;
  if (!C) return;
  const $ = C.$, el = C.el, api = C.api;
  const PRIORITES = { haute: 'Haute', normale: 'Normale', basse: 'Basse' };
  const RANG = { haute: 0, normale: 1, basse: 2 };
  const JOURS = ['Lundi', 'Mardi', 'Mercredi', 'Jeudi', 'Vendredi', 'Samedi', 'Dimanche'];
  const MAX_IMPORT = 480 * 1024;

  const dossier = { taches: [], creneaux: [], notes: [], semaine: null, note: null, modifiee: false, charge: false };

  // ------------------------------------------------------------------ dates
  const deuxChiffres = (n) => (n < 10 ? '0' + n : String(n));
  function jourISO(d) {
    return d.getFullYear() + '-' + deuxChiffres(d.getMonth() + 1) + '-' + deuxChiffres(d.getDate());
  }
  function depuisISO(iso) {
    const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''));
    return m ? new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])) : null;
  }
  function lundi(d) {
    const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
    x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
    return x;
  }
  function jourCourt(iso) {
    const d = depuisISO(iso);
    if (!d) return '';
    return new Intl.DateTimeFormat('fr-FR', { weekday: 'short', day: '2-digit', month: 'short' }).format(d);
  }
  const aujourdhui = () => jourISO(new Date());

  // ------------------------------------------------------------------ appels locaux
  function message(id, texte, erreur) {
    const n = $(id);
    if (!n) return;
    n.textContent = texte || '';
    n.className = erreur ? 'erreur' : 'aide';
  }
  async function charger() {
    const r = await api('/api/espace');
    if (!r.ok) {
      C.bandeau('espace', C.messageErreur(r));
      return false;
    }
    C.bandeau('espace', r.donnees.avertissement ? "Vos tâches et vos notes n'ont pas pu être relues : "
      + 'un espace vide a été créé et l\'ancien fichier a été mis de côté.' : null);
    dossier.taches = Array.isArray(r.donnees.taches) ? r.donnees.taches : [];
    dossier.creneaux = Array.isArray(r.donnees.creneaux) ? r.donnees.creneaux : [];
    dossier.notes = Array.isArray(r.donnees.notes) ? r.donnees.notes : [];
    dossier.charge = true;
    return true;
  }
  async function envoyer(chemin, corps, idMessage) {
    const r = await api(chemin, { corps: corps });
    if (!r.ok) {
      if (idMessage) message(idMessage, C.messageErreur(r), true);
      return null;
    }
    await charger();
    dessinerTout();
    return r.donnees;
  }

  // ------------------------------------------------------------------ Mes tâches
  function trier(a, b) {
    if (!!a.faite !== !!b.faite) return a.faite ? 1 : -1;
    const ea = a.echeance || '9999-12-31', eb = b.echeance || '9999-12-31';
    if (ea !== eb) return ea < eb ? -1 : 1;
    const ha = a.heure || '99:99', hb = b.heure || '99:99';
    if (ha !== hb) return ha < hb ? -1 : 1;
    return (RANG[a.priorite] || 1) - (RANG[b.priorite] || 1);
  }
  function tachesFiltrees() {
    const q = String($('#tache-recherche').value || '').trim().toLowerCase();
    const etat = $('#tache-filtre-etat').value;
    const priorite = $('#tache-filtre-priorite').value;
    return dossier.taches.filter((t) => {
      if (etat === 'a_faire' && t.faite) return false;
      if (etat === 'faite' && !t.faite) return false;
      if (priorite && t.priorite !== priorite) return false;
      if (q && (String(t.titre || '') + ' ' + String(t.detail || '')).toLowerCase().indexOf(q) < 0) return false;
      return true;
    }).sort(trier);
  }
  function ligneTache(t) {
    const coche = el('input', { type: 'checkbox', checked: t.faite ? true : false,
                                'aria-label': 'Tâche faite : ' + (t.titre || '') });
    coche.addEventListener('change', () => {
      coche.disabled = true;
      envoyer('/api/taches/modifier', { id: t.id, faite: coche.checked }, '#tache-message');
    });
    const etiquettes = el('span', { class: 'etiquettes' },
      el('span', { class: 'badge p-' + t.priorite, texte: PRIORITES[t.priorite] || t.priorite }));
    if (t.echeance) {
      const retard = !t.faite && (t.echeance < aujourdhui());
      etiquettes.append(el('span', { class: 'badge' + (retard ? ' retard' : ''),
        texte: (retard ? 'En retard : ' : 'Pour le ') + jourCourt(t.echeance) + (t.heure ? ' à ' + t.heure : '') }));
    }
    if (t.faite) etiquettes.append(el('span', { class: 'badge faite', texte: 'Faite' }));

    const modifier = el('button', { type: 'button', class: 'bouton discret', texte: 'Modifier' });
    const supprimer = el('button', { type: 'button', class: 'bouton discret', texte: 'Supprimer' });
    const assistant = el('button', { type: 'button', class: 'bouton discret', texte: "Demander à l'assistant" });
    const corps = el('div', { class: 'tache-corps' },
      el('strong', { class: t.faite ? 'barree' : '', texte: t.titre || '' }), etiquettes,
      t.detail ? el('p', { class: 'aide pre', texte: t.detail }) : null);
    const ligne = el('div', { class: 'tache' + (t.faite ? ' terminee' : '') }, coche, corps,
      el('div', { class: 'actions' }, modifier, supprimer, assistant));

    supprimer.addEventListener('click', () => {
      if (!window.confirm('Supprimer définitivement la tâche « ' + (t.titre || '') + ' » ?')) return;
      envoyer('/api/taches/supprimer', { id: t.id }, '#tache-message');
    });
    assistant.addEventListener('click', () => {
      C.versAssistant('Tâche : ' + (t.titre || '') + (t.detail ? '\n' + t.detail : '')
        + (t.echeance ? '\nÉchéance : ' + t.echeance + (t.heure ? ' ' + t.heure : '') : '')
        + '\n\nAide-moi à préparer cette tâche : étapes concrètes et points de vigilance.');
    });
    modifier.addEventListener('click', () => ligne.replaceWith(formulaireTache(t, ligne)));
    return ligne;
  }
  function champTexte(libelle, valeur, attrs) {
    const id = C.idUnique('c');
    const entree = el('input', Object.assign({ type: 'text', id: id, value: valeur || '' }, attrs || {}));
    return [el('div', { class: 'champ' + (attrs && attrs.grand ? ' grand' : '') },
      el('label', { for: id, texte: libelle }), entree), entree];
  }
  function formulaireTache(t, ligne) {
    const [bTitre, titre] = champTexte('Tâche', t.titre, { maxlength: 200, grand: true });
    const [bEcheance, echeance] = champTexte('Échéance', t.echeance, { type: 'date' });
    const [bHeure, heure] = champTexte('Heure', t.heure, { type: 'time' });
    const priorite = el('select', {},
      ...Object.keys(PRIORITES).map((p) => el('option', { value: p, selected: p === t.priorite, texte: PRIORITES[p] })));
    const idP = C.idUnique('c');
    priorite.id = idP;
    const detail = el('textarea', { rows: 2, maxlength: 2000 });
    detail.value = t.detail || '';
    const idD = C.idUnique('c');
    detail.id = idD;
    const valider = el('button', { type: 'submit', class: 'bouton', texte: 'Enregistrer' });
    const annuler = el('button', { type: 'button', class: 'bouton discret', texte: 'Annuler' });
    const info = el('span', { class: 'aide' });
    const form = el('form', { class: 'bloc formulaire' },
      el('div', { class: 'champs' }, bTitre,
        el('div', { class: 'champ' }, el('label', { for: idP, texte: 'Priorité' }), priorite), bEcheance, bHeure),
      el('div', { class: 'champ' }, el('label', { for: idD, texte: 'Détail' }), detail),
      el('div', { class: 'actions' }, valider, annuler, info));
    annuler.addEventListener('click', () => form.replaceWith(ligne));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      valider.disabled = true;
      const r = await api('/api/taches/modifier', { corps: { id: t.id, titre: titre.value, priorite: priorite.value,
        echeance: echeance.value, heure: heure.value, detail: detail.value } });
      valider.disabled = false;
      if (!r.ok) { info.textContent = C.messageErreur(r); info.className = 'erreur'; return; }
      await charger();
      dessinerTout();
    });
    return form;
  }
  function dessinerTaches() {
    const zone = $('#liste-taches');
    const vues = tachesFiltrees();
    const restantes = dossier.taches.filter((t) => !t.faite).length;
    $('#taches-compte').textContent = dossier.taches.length
      ? vues.length + ' tâche(s) affichée(s) · ' + restantes + ' à faire sur ' + dossier.taches.length
      : '';
    if (!vues.length) {
      zone.replaceChildren(el('p', { class: 'vide', texte: dossier.taches.length
        ? 'Aucune tâche ne correspond à ce filtre.'
        : 'Aucune tâche pour le moment : ajoutez-en une ci-dessus.' }));
      return;
    }
    zone.replaceChildren(...vues.map(ligneTache));
  }

  // ------------------------------------------------------------------ Mon planning
  function dessinerSemaine() {
    if (!dossier.semaine) dossier.semaine = lundi(new Date());
    const debut = dossier.semaine;
    const fin = new Date(debut.getFullYear(), debut.getMonth(), debut.getDate() + 6);
    const fmt = new Intl.DateTimeFormat('fr-FR', { day: '2-digit', month: 'long' });
    $('#semaine-libelle').textContent = 'Du ' + fmt.format(debut) + ' au ' + fmt.format(fin) + ' ' + fin.getFullYear();
    const zone = $('#semaine');
    zone.replaceChildren();
    for (let i = 0; i < 7; i++) {
      const jour = new Date(debut.getFullYear(), debut.getMonth(), debut.getDate() + i);
      const iso = jourISO(jour);
      const colonne = el('div', { class: 'jour' + (iso === aujourdhui() ? ' aujourdhui' : '') },
        el('div', { class: 'jour-entete' }, el('strong', { texte: JOURS[i] }),
          el('span', { class: 'aide', texte: jour.getDate() + '/' + deuxChiffres(jour.getMonth() + 1) })));
      const elements = [];
      for (const c of dossier.creneaux) {
        if (c.jour === iso) elements.push({ h: c.debut || '00:00', noeud: blocCreneau(c) });
      }
      for (const t of dossier.taches) {
        if (t.echeance === iso) elements.push({ h: t.heure || '23:59', noeud: blocEcheance(t) });
      }
      elements.sort((a, b) => (a.h < b.h ? -1 : (a.h > b.h ? 1 : 0)));
      if (!elements.length) colonne.append(el('p', { class: 'vide', texte: '—' }));
      for (const e of elements) colonne.append(e.noeud);
      zone.append(colonne);
    }
  }
  function blocCreneau(c) {
    const supprimer = el('button', { type: 'button', class: 'bouton discret', texte: 'Retirer' });
    supprimer.addEventListener('click', () => {
      if (!window.confirm('Retirer le créneau « ' + (c.titre || '') + ' » ?')) return;
      envoyer('/api/creneaux/supprimer', { id: c.id }, '#creneau-message');
    });
    return el('div', { class: 'creneau' },
      el('strong', { texte: (c.debut || '') + (c.fin ? '–' + c.fin : '') }),
      el('span', { texte: ' ' + (c.titre || '') }),
      c.lieu ? el('p', { class: 'aide', texte: c.lieu }) : null,
      c.detail ? el('p', { class: 'aide pre', texte: c.detail }) : null,
      el('div', { class: 'actions' }, supprimer));
  }
  function blocEcheance(t) {
    const voir = el('button', { type: 'button', class: 'bouton discret', texte: 'Voir la tâche' });
    voir.addEventListener('click', () => {
      $('#tache-recherche').value = t.titre || '';
      $('#tache-filtre-etat').value = 'tout';
      C.aller('taches');
      dessinerTaches();
    });
    return el('div', { class: 'creneau echeance' + (t.faite ? ' terminee' : '') },
      el('strong', { texte: t.heure || 'Échéance' }),
      el('span', { texte: ' ' + (t.titre || '') }),
      el('span', { class: 'badge p-' + t.priorite, texte: PRIORITES[t.priorite] || '' }),
      el('div', { class: 'actions' }, voir));
  }

  // ------------------------------------------------------------------ Mes notes (Markdown)
  const RX_INLINE = /(\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|_[^_\n]+_|`[^`\n]+`|\[[^\]\n]+\]\([^)\s]+\))/g;
  function enligne(texte, parent) {
    const morceaux = String(texte).split(RX_INLINE);
    for (const m of morceaux) {
      if (!m) continue;
      if (/^(\*\*|__).+(\*\*|__)$/.test(m)) parent.append(el('strong', { texte: m.slice(2, -2) }));
      else if (/^[*_].+[*_]$/.test(m)) parent.append(el('em', { texte: m.slice(1, -1) }));
      else if (/^`.+`$/.test(m)) parent.append(el('code', { texte: m.slice(1, -1) }));
      else if (/^\[.+\]\(.+\)$/.test(m)) {
        const coupe = m.indexOf('](');
        // les liens ne sont pas cliquables : rien ne doit partir de ce cockpit
        parent.append(document.createTextNode(m.slice(1, coupe) + ' (' + m.slice(coupe + 2, -1) + ')'));
      } else parent.append(document.createTextNode(m));
    }
    return parent;
  }
  function rendreMarkdown(texte) {
    const racine = el('div', { class: 'markdown' });
    const lignes = String(texte || '').replace(/\r\n?/g, '\n').split('\n');
    let paragraphe = null, bloc = null, pile = null;
    const finirParagraphe = () => { paragraphe = null; };
    const finirListe = () => { pile = null; };
    for (let i = 0; i < lignes.length; i++) {
      const ligne = lignes[i];
      if (bloc !== null) {
        if (/^```/.test(ligne)) { racine.append(el('pre', {}, el('code', { texte: bloc.join('\n') }))); bloc = null; }
        else bloc.push(ligne);
        continue;
      }
      if (/^```/.test(ligne)) { finirParagraphe(); finirListe(); bloc = []; continue; }
      if (!ligne.trim()) { finirParagraphe(); finirListe(); continue; }
      let m = /^(#{1,4})\s+(.*)$/.exec(ligne);
      if (m) {
        finirParagraphe(); finirListe();
        racine.append(enligne(m[2], el('h' + Math.min(6, m[1].length + 2), {})));
        continue;
      }
      if (/^(-{3,}|\*{3,})\s*$/.test(ligne)) { finirParagraphe(); finirListe(); racine.append(el('hr', {})); continue; }
      m = /^>\s?(.*)$/.exec(ligne);
      if (m) {
        finirParagraphe(); finirListe();
        racine.append(enligne(m[1], el('blockquote', {})));
        continue;
      }
      m = /^\s*[-*+]\s+(.*)$/.exec(ligne);
      if (m) {
        finirParagraphe();
        if (!pile || pile.tagName !== 'UL') { pile = el('ul', {}); racine.append(pile); }
        pile.append(enligne(m[1], el('li', {})));
        continue;
      }
      m = /^\s*\d+[.)]\s+(.*)$/.exec(ligne);
      if (m) {
        finirParagraphe();
        if (!pile || pile.tagName !== 'OL') { pile = el('ol', {}); racine.append(pile); }
        pile.append(enligne(m[1], el('li', {})));
        continue;
      }
      finirListe();
      if (!paragraphe) { paragraphe = el('p', {}); racine.append(paragraphe); }
      else paragraphe.append(el('br', {}));
      enligne(ligne, paragraphe);
    }
    if (bloc !== null) racine.append(el('pre', {}, el('code', { texte: bloc.join('\n') })));
    if (!racine.children.length) racine.append(el('p', { class: 'vide', texte: 'Note vide.' }));
    return racine;
  }
  function notesFiltrees() {
    const q = String($('#note-recherche').value || '').trim().toLowerCase();
    return dossier.notes.filter((n) => !q
      || (String(n.titre || '') + ' ' + String(n.texte || '')).toLowerCase().indexOf(q) >= 0)
      .sort((a, b) => (!!b.epingle - !!a.epingle) || (String(b.maj_le) < String(a.maj_le) ? -1 : 1));
  }
  function dessinerNotes() {
    const zone = $('#liste-notes');
    const vues = notesFiltrees();
    if (!vues.length) {
      zone.replaceChildren(el('p', { class: 'vide', texte: dossier.notes.length
        ? 'Aucune note ne correspond à cette recherche.' : 'Aucune note : créez-en une.' }));
    } else {
      zone.replaceChildren(...vues.map((n) => {
        const ouvrir = el('button', { type: 'button', class: 'note-item' + (dossier.note === n.id ? ' actif' : '') },
          el('strong', { texte: n.titre || '(sans titre)' }),
          el('span', { class: 'aide', texte: C.dateCourte(n.maj_le) }),
          el('span', { class: 'aide extrait', texte: String(n.texte || '').slice(0, 90) }));
        ouvrir.addEventListener('click', () => ouvrirNote(n.id));
        return ouvrir;
      }));
    }
    const courante = dossier.notes.filter((n) => n.id === dossier.note)[0];
    if (dossier.note && !courante) nouvelleNote();
    $('#note-supprimer').hidden = !dossier.note;
    if ($('#note-apercu-zone').hidden === false) majApercu();
  }
  function ouvrirNote(id) {
    const n = dossier.notes.filter((x) => x.id === id)[0];
    if (!n) return;
    if (dossier.modifiee && !window.confirm('La note en cours n\'est pas enregistrée. Continuer sans l\'enregistrer ?')) return;
    dossier.note = id;
    dossier.modifiee = false;
    $('#note-titre').value = n.titre || '';
    $('#note-texte').value = n.texte || '';
    message('#note-message', 'Note enregistrée le ' + C.dateCourte(n.maj_le));
    dessinerNotes();
    majApercu();
  }
  function nouvelleNote() {
    dossier.note = null;
    dossier.modifiee = false;
    $('#note-titre').value = '';
    $('#note-texte').value = '';
    message('#note-message', '');
    dessinerNotes();
    majApercu();
    try { $('#note-titre').focus(); } catch (_) { /* rien */ }
  }
  function majApercu() {
    if ($('#note-apercu-zone').hidden) return;
    $('#note-rendu').replaceWith(Object.assign(rendreMarkdown($('#note-texte').value), { id: 'note-rendu' }));
  }
  async function enregistrerNote() {
    const titre = String($('#note-titre').value || '').trim();
    if (!titre) { message('#note-message', 'Donnez un titre à la note.', true); return; }
    const corps = { titre: titre, texte: $('#note-texte').value };
    let r;
    if (dossier.note) {
      corps.id = dossier.note;
      r = await api('/api/notes/modifier', { corps: corps });
    } else {
      r = await api('/api/notes/creer', { corps: corps });
    }
    if (!r.ok) { message('#note-message', C.messageErreur(r), true); return; }
    dossier.note = r.donnees.id;
    dossier.modifiee = false;
    await charger();
    dessinerTout();
    message('#note-message', 'Note enregistrée.');
  }

  // ------------------------------------------------------------------ export / import
  async function importer(fichier) {
    if (!fichier) return;
    if (fichier.size > MAX_IMPORT) {
      message('#message-donnees', 'Fichier trop volumineux (480 Ko au maximum).', true);
      return;
    }
    let texte = '';
    try {
      texte = await fichier.text();
    } catch (_) {
      message('#message-donnees', 'Fichier illisible.', true);
      return;
    }
    const r = await api('/api/espace/importer', { corps: { donnees: texte, mode: $('#mode-import').value },
                                                  delai: 60000 });
    if (!r.ok) { message('#message-donnees', C.messageErreur(r), true); return; }
    const n = r.donnees.importes || {};
    await charger();
    dessinerTout();
    message('#message-donnees', 'Import terminé : ' + (n.taches || 0) + ' tâche(s), ' + (n.creneaux || 0)
      + ' créneau(x), ' + (n.notes || 0) + ' note(s)' + (r.donnees.ignores ? ' · ' + r.donnees.ignores + ' ignoré(s)' : '') + '.');
  }

  // ------------------------------------------------------------------ assemblage
  function dessinerTout() {
    dessinerTaches();
    dessinerSemaine();
    dessinerNotes();
  }
  let branche = false;
  function brancher() {
    if (branche) return;   // la même gestion sert aux trois rubriques
    branche = true;
    $('#form-tache').addEventListener('submit', async (e) => {
      e.preventDefault();
      const titre = String($('#tache-titre').value || '').trim();
      if (!titre) { message('#tache-message', 'Écrivez la tâche à faire.', true); return; }
      const d = await envoyer('/api/taches/creer', { titre: titre, priorite: $('#tache-priorite').value,
        echeance: $('#tache-echeance').value, heure: $('#tache-heure').value, detail: $('#tache-detail').value },
        '#tache-message');
      if (!d) return;
      $('#tache-titre').value = '';
      $('#tache-detail').value = '';
      $('#tache-heure').value = '';
      message('#tache-message', 'Tâche ajoutée.');
      $('#tache-titre').focus();
    });
    for (const id of ['#tache-recherche', '#tache-filtre-etat', '#tache-filtre-priorite']) {
      $(id).addEventListener('input', dessinerTaches);
      $(id).addEventListener('change', dessinerTaches);
    }
    $('#form-creneau').addEventListener('submit', async (e) => {
      e.preventDefault();
      const d = await envoyer('/api/creneaux/creer', { titre: $('#creneau-titre').value, jour: $('#creneau-jour').value,
        debut: $('#creneau-debut').value, fin: $('#creneau-fin').value, lieu: $('#creneau-lieu').value },
        '#creneau-message');
      if (!d) return;
      const jour = depuisISO(d.jour);
      if (jour) dossier.semaine = lundi(jour);
      $('#creneau-titre').value = '';
      $('#creneau-lieu').value = '';
      message('#creneau-message', 'Créneau ajouté.');
      dessinerSemaine();
    });
    const bouger = (jours) => {
      const base = dossier.semaine || lundi(new Date());
      dossier.semaine = new Date(base.getFullYear(), base.getMonth(), base.getDate() + jours);
      dessinerSemaine();
    };
    $('#semaine-precedente').addEventListener('click', () => bouger(-7));
    $('#semaine-suivante').addEventListener('click', () => bouger(7));
    $('#semaine-courante').addEventListener('click', () => { dossier.semaine = lundi(new Date()); dessinerSemaine(); });

    $('#note-recherche').addEventListener('input', dessinerNotes);
    $('#note-nouvelle').addEventListener('click', nouvelleNote);
    $('#form-note').addEventListener('submit', (e) => { e.preventDefault(); enregistrerNote(); });
    $('#note-texte').addEventListener('input', () => { dossier.modifiee = true; majApercu(); });
    $('#note-titre').addEventListener('input', () => { dossier.modifiee = true; });
    $('#note-apercu').addEventListener('click', () => {
      const zone = $('#note-apercu-zone');
      zone.hidden = !zone.hidden;
      $('#note-apercu').textContent = zone.hidden ? 'Aperçu' : 'Masquer l\'aperçu';
      majApercu();
    });
    $('#note-supprimer').addEventListener('click', () => {
      if (!dossier.note) return;
      if (!window.confirm('Supprimer définitivement cette note ?')) return;
      envoyer('/api/notes/supprimer', { id: dossier.note }, '#note-message').then((ok) => { if (ok) nouvelleNote(); });
    });
    $('#note-assistant').addEventListener('click', () => {
      const texte = String($('#note-texte').value || '').trim();
      if (!texte) { message('#note-message', 'Écrivez d\'abord quelque chose dans la note.', true); return; }
      C.versAssistant(texte, 'À propos de ma note « ' + String($('#note-titre').value || '').trim() + ' » :');
    });
    $('#exporter').addEventListener('click', () => {
      const lien = el('a', { href: '/api/espace/export?fichier=1', download: 'mon-espace.json' });
      document.body.append(lien);
      lien.click();
      lien.remove();
      message('#message-donnees', 'Export demandé : le fichier est enregistré par votre navigateur.');
    });
    $('#choisir-import').addEventListener('click', () => $('#fichier-import').click());
    $('#fichier-import').addEventListener('change', (e) => {
      const f = e.target.files && e.target.files[0];
      e.target.value = '';
      importer(f);
    });
    window.addEventListener('beforeunload', (e) => {
      if (!dossier.modifiee) return;
      e.preventDefault();
      e.returnValue = '';
    });
  }

  const gestion = {
    demarrer: brancher,
    oublier: () => {   // cockpit verrouillé : plus rien en mémoire du navigateur
      dossier.taches = [];
      dossier.creneaux = [];
      dossier.notes = [];
      dossier.note = null;
      dossier.modifiee = false;
      dossier.charge = false;
    },
    activer: async () => {
      if (!dossier.semaine) dossier.semaine = lundi(new Date());
      if (!$('#creneau-jour').value) $('#creneau-jour').value = aujourdhui();
      if (await charger()) dessinerTout();
    },
  };
  C.enregistrerVue('taches', gestion);
  C.enregistrerVue('planning', gestion);
  C.enregistrerVue('notes', gestion);
})();
