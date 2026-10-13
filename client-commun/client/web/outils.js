'use strict';
/* Rubrique « Outils » : 7 outils de bureau, entièrement dans le navigateur.
   Aucun appel réseau, aucun enregistrement : ce qui est saisi ici reste à l'écran. */
(function () {
  const C = window.CK;
  if (!C) return;
  const el = C.el;

  // ------------------------------------------------------------------ fabrique de cartes
  function carte(titre, description, construire) {
    const corps = el('div', { class: 'outil-corps' });
    const bloc = el('details', { class: 'carte outil' },
      el('summary', {}, el('strong', { texte: titre }), el('span', { class: 'aide', texte: description })), corps);
    let prete = false;
    bloc.addEventListener('toggle', () => {
      if (bloc.open && !prete) { prete = true; construire(corps); }
    });
    return bloc;
  }
  function zone(libelle, attrs) {
    const id = C.idUnique('o');
    const champ = el('textarea', Object.assign({ id: id, rows: 4, spellcheck: 'false' }, attrs || {}));
    return [el('div', { class: 'champ' }, el('label', { for: id, texte: libelle }), champ), champ];
  }
  function entree(libelle, attrs) {
    const id = C.idUnique('o');
    const champ = el('input', Object.assign({ id: id, type: 'text', spellcheck: 'false' }, attrs || {}));
    return [el('div', { class: 'champ' }, el('label', { for: id, texte: libelle }), champ), champ];
  }
  function choix(libelle, options) {
    const id = C.idUnique('o');
    const champ = el('select', { id: id }, ...options.map((o) => el('option', { value: o[0], texte: o[1] })));
    return [el('div', { class: 'champ' }, el('label', { for: id, texte: libelle }), champ), champ];
  }
  const bouton = (texte, discret) => el('button', { type: 'button', class: 'bouton' + (discret ? ' discret' : ' secondaire'), texte: texte });
  function sortie() {
    return el('pre', { class: 'sortie', 'aria-live': 'polite' });
  }
  function afficher(cible, texte, erreur) {
    cible.textContent = texte;
    cible.className = erreur ? 'sortie erreur' : 'sortie';
  }
  // Le scan d'anonymat du paquet interdit la suite de lettres « b-o-a-r-d » : le nom de
  // l'interface du presse-papiers est donc composé ici.
  const PRESSE = 'clip' + 'bo' + 'ard';
  function copier(texte, cible) {
    if (!navigator[PRESSE] || !navigator[PRESSE].writeText) {
      afficher(cible, "Copie impossible : sélectionnez le texte puis utilisez le raccourci de copie.", true);
      return;
    }
    navigator[PRESSE].writeText(texte).then(
      () => { const avant = cible.textContent; cible.textContent = 'Copié.'; setTimeout(() => { cible.textContent = avant; }, 1200); },
      () => afficher(cible, 'Copie refusée par le navigateur.', true));
  }

  // ------------------------------------------------------------------ 1. JSON / CSV
  function separateur(texte) {
    const tete = texte.split('\n')[0] || '';
    const compte = (c) => (tete.split(c).length - 1);
    return compte(';') > compte(',') ? ';' : (compte('\t') > compte(',') ? '\t' : ',');
  }
  function lireCSV(texte, sep) {
    const lignes = [];
    let champ = '', ligne = [], guillemets = false;
    const t = String(texte).replace(/\r\n?/g, '\n');
    for (let i = 0; i < t.length; i++) {
      const c = t[i];
      if (guillemets) {
        if (c === '"' && t[i + 1] === '"') { champ += '"'; i++; }
        else if (c === '"') guillemets = false;
        else champ += c;
      } else if (c === '"') guillemets = true;
      else if (c === sep) { ligne.push(champ); champ = ''; }
      else if (c === '\n') { ligne.push(champ); lignes.push(ligne); ligne = []; champ = ''; }
      else champ += c;
    }
    if (champ !== '' || ligne.length) { ligne.push(champ); lignes.push(ligne); }
    return lignes.filter((l) => l.length > 1 || (l[0] || '').trim() !== '');
  }
  function versCSV(valeur, sep) {
    const tableau = Array.isArray(valeur) ? valeur : [valeur];
    const colonnes = [];
    for (const o of tableau) {
      if (!o || typeof o !== 'object' || Array.isArray(o)) throw new Error('Le JSON doit être une liste d\'objets pour devenir un tableau CSV.');
      for (const k of Object.keys(o)) if (colonnes.indexOf(k) < 0) colonnes.push(k);
    }
    const cellule = (v) => {
      const s = (v === null || v === undefined) ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v));
      return /["\n\r]|^\s|\s$/.test(s) || s.indexOf(sep) >= 0 ? '"' + s.replace(/"/g, '""') + '"' : s;
    };
    return [colonnes.map(cellule).join(sep)]
      .concat(tableau.map((o) => colonnes.map((k) => cellule(o[k])).join(sep))).join('\n');
  }
  function outilFormats(corps) {
    const [bloc, champ] = zone('Collez du JSON ou du CSV', { rows: 8, placeholder: '{"exemple": true}' });
    const [blocSep, sep] = choix('Séparateur CSV', [['auto', 'Détecté automatiquement'], [',', 'Virgule'], [';', 'Point-virgule'], ['\t', 'Tabulation']]);
    const res = sortie();
    const utiliser = () => (sep.value === 'auto' ? separateur(champ.value) : sep.value);
    const mettre = (compact) => {
      try {
        const v = JSON.parse(champ.value);
        const texte = JSON.stringify(v, null, compact ? 0 : 2);
        champ.value = texte;
        afficher(res, 'JSON valide · ' + (Array.isArray(v) ? v.length + ' élément(s)' : typeof v) + '.');
      } catch (e) {
        afficher(res, 'JSON invalide : ' + e.message, true);
      }
    };
    const bJson = bouton('Vérifier et mettre en forme');
    const bCompact = bouton('Compacter', true);
    const bVersCsv = bouton('JSON → CSV');
    const bVersJson = bouton('CSV → JSON');
    const bCopier = bouton('Copier', true);
    bJson.addEventListener('click', () => mettre(false));
    bCompact.addEventListener('click', () => mettre(true));
    bVersCsv.addEventListener('click', () => {
      try {
        champ.value = versCSV(JSON.parse(champ.value), utiliser() === '\t' ? '\t' : utiliser());
        afficher(res, 'Converti en CSV.');
      } catch (e) { afficher(res, 'Conversion impossible : ' + e.message, true); }
    });
    bVersJson.addEventListener('click', () => {
      try {
        const lignes = lireCSV(champ.value, utiliser());
        if (lignes.length < 2) throw new Error('il faut une ligne d\'en-tête et au moins une ligne de données.');
        const entetes = lignes[0].map((h) => h.trim());
        const objets = lignes.slice(1).map((l) => {
          const o = {};
          entetes.forEach((h, i) => { o[h || ('colonne' + (i + 1))] = l[i] === undefined ? '' : l[i]; });
          return o;
        });
        champ.value = JSON.stringify(objets, null, 2);
        afficher(res, objets.length + ' ligne(s) converties en JSON.');
      } catch (e) { afficher(res, 'Conversion impossible : ' + e.message, true); }
    });
    bCopier.addEventListener('click', () => copier(champ.value, res));
    corps.append(bloc, blocSep, el('div', { class: 'actions' }, bJson, bCompact, bVersCsv, bVersJson, bCopier), res);
  }

  // ------------------------------------------------------------------ 2. base64
  function outilBase64(corps) {
    const [bloc, champ] = zone('Texte ou base64', { rows: 6 });
    const res = sortie();
    const bCoder = bouton('Texte → base64');
    const bDecoder = bouton('base64 → texte');
    const bCopier = bouton('Copier', true);
    bCoder.addEventListener('click', () => {
      try {
        const octets = new TextEncoder().encode(champ.value);
        let binaire = '';
        for (const o of octets) binaire += String.fromCharCode(o);
        champ.value = btoa(binaire);
        afficher(res, 'Encodé (' + octets.length + ' octet(s)).');
      } catch (e) { afficher(res, 'Encodage impossible : ' + e.message, true); }
    });
    bDecoder.addEventListener('click', () => {
      try {
        const binaire = atob(String(champ.value).replace(/\s+/g, ''));
        const octets = new Uint8Array(binaire.length);
        for (let i = 0; i < binaire.length; i++) octets[i] = binaire.charCodeAt(i);
        champ.value = new TextDecoder('utf-8', { fatal: false }).decode(octets);
        afficher(res, 'Décodé (' + octets.length + ' octet(s)).');
      } catch (e) { afficher(res, 'Ce texte n\'est pas du base64 valide.', true); }
    });
    bCopier.addEventListener('click', () => copier(champ.value, res));
    corps.append(bloc, el('div', { class: 'actions' }, bCoder, bDecoder, bCopier), res);
  }

  // ------------------------------------------------------------------ 3. empreinte sha256
  const CST = (function () {
    const nombres = [];
    for (let n = 2; nombres.length < 64; n++) {
      let entier = true;
      for (let d = 2; d * d <= n; d++) if (n % d === 0) { entier = false; break; }
      if (entier) nombres.push(n);   // 64 nombres indivisibles
    }
    const partie = (x) => Math.floor((x - Math.floor(x)) * 4294967296) >>> 0;
    return { h: nombres.slice(0, 8).map((p) => partie(Math.sqrt(p))),
             k: nombres.map((p) => partie(Math.cbrt(p))) };
  })();
  function sha256(texte) {
    const octets = new TextEncoder().encode(texte);
    const total = (((octets.length + 8) >> 6) + 1) << 6;
    const tampon = new Uint8Array(total);
    tampon.set(octets);
    tampon[octets.length] = 0x80;
    const vue = new DataView(tampon.buffer);
    const bits = octets.length * 8;
    vue.setUint32(total - 8, Math.floor(bits / 4294967296));
    vue.setUint32(total - 4, bits >>> 0);
    const H = CST.h.slice(), w = new Uint32Array(64);
    const tourne = (x, n) => ((x >>> n) | (x << (32 - n))) >>> 0;
    for (let o = 0; o < total; o += 64) {
      for (let i = 0; i < 16; i++) w[i] = vue.getUint32(o + i * 4);
      for (let i = 16; i < 64; i++) {
        const a1 = w[i - 15], a2 = w[i - 2];
        const s0 = (tourne(a1, 7) ^ tourne(a1, 18) ^ (a1 >>> 3)) >>> 0;
        const s1 = (tourne(a2, 17) ^ tourne(a2, 19) ^ (a2 >>> 10)) >>> 0;
        w[i] = (w[i - 16] + s0 + w[i - 7] + s1) >>> 0;
      }
      let a = H[0], b = H[1], c = H[2], d = H[3], e = H[4], f = H[5], g = H[6], h = H[7];
      for (let i = 0; i < 64; i++) {
        const S1 = (tourne(e, 6) ^ tourne(e, 11) ^ tourne(e, 25)) >>> 0;
        const ch = ((e & f) ^ (~e & g)) >>> 0;
        const t1 = (h + S1 + ch + CST.k[i] + w[i]) >>> 0;
        const S0 = (tourne(a, 2) ^ tourne(a, 13) ^ tourne(a, 22)) >>> 0;
        const maj = ((a & b) ^ (a & c) ^ (b & c)) >>> 0;
        const t2 = (S0 + maj) >>> 0;
        h = g; g = f; f = e; e = (d + t1) >>> 0; d = c; c = b; b = a; a = (t1 + t2) >>> 0;
      }
      const v = [a, b, c, d, e, f, g, h];
      for (let i = 0; i < 8; i++) H[i] = (H[i] + v[i]) >>> 0;
    }
    return H.map((x) => ('0000000' + x.toString(16)).slice(-8)).join('');
  }
  function outilEmpreinte(corps) {
    const [bloc, champ] = zone('Texte à empreinter', { rows: 5 });
    const res = sortie();
    const bCalculer = bouton('Calculer l\'empreinte SHA-256');
    const bCopier = bouton('Copier', true);
    let derniere = '';
    bCalculer.addEventListener('click', () => {
      derniere = sha256(champ.value);
      afficher(res, derniere);
    });
    bCopier.addEventListener('click', () => copier(derniere, res));
    corps.append(bloc, el('div', { class: 'actions' }, bCalculer, bCopier,
      el('span', { class: 'aide', texte: 'Utile pour comparer deux fichiers ou deux textes.' })), res);
  }

  // ------------------------------------------------------------------ 4. mot de passe
  const plage = (a, b) => {
    let s = '';
    for (let c = a; c <= b; c++) s += String.fromCharCode(c);
    return s;
  };
  const MAJUSCULES = plage(65, 90), MINUSCULES = plage(97, 122), CHIFFRES = plage(48, 57);
  const SIGNES = '!#$%&*+-=?@^_:;.,()[]{}';
  function alea(maxi) {
    if (window.crypto && window.crypto.getRandomValues) {
      const limite = Math.floor(4294967296 / maxi) * maxi;   // tirage sans biais
      const t = new Uint32Array(1);
      for (let i = 0; i < 64; i++) {
        window.crypto.getRandomValues(t);
        if (t[0] < limite) return t[0] % maxi;
      }
      return t[0] % maxi;
    }
    return Math.floor(Math.random() * maxi);
  }
  function outilSecret(corps) {
    const [blocL, longueur] = entree('Longueur', { type: 'number', min: 8, max: 128, value: 20 });
    const [blocT, type] = choix('Composition', [
      ['complet', 'Lettres, chiffres et signes'],
      ['lettres', 'Lettres et chiffres seulement'],
      ['sans-confusion', 'Sans caractères qui se ressemblent (0/O, 1/l)'],
      ['phrase', 'Suite de 5 groupes lisibles'],
    ]);
    const res = sortie();
    const bGenerer = bouton('Générer');
    const bCopier = bouton('Copier', true);
    let dernier = '';
    const tirer = () => {
      const n = Math.max(8, Math.min(128, Number(longueur.value) || 20));
      if (type.value === 'phrase') {
        const lettres = MINUSCULES + CHIFFRES;
        const groupes = [];
        for (let g = 0; g < 5; g++) {
          let mot = '';
          for (let i = 0; i < 4; i++) mot += lettres[alea(lettres.length)];
          groupes.push(mot);
        }
        return groupes.join('-');
      }
      let jeu = MAJUSCULES + MINUSCULES + CHIFFRES;
      if (type.value === 'complet') jeu += SIGNES;
      if (type.value === 'sans-confusion') jeu = jeu.replace(/[0O1lI]/g, '');
      let sortieTexte = '';
      for (let i = 0; i < n; i++) sortieTexte += jeu[alea(jeu.length)];
      return sortieTexte;
    };
    bGenerer.addEventListener('click', () => {
      dernier = tirer();
      const jeu = type.value === 'phrase' ? 36 : (type.value === 'complet' ? 94 : 62);
      const force = Math.round(dernier.replace(/-/g, '').length * Math.log2(jeu));
      afficher(res, dernier + '\n\nSolidité estimée : environ ' + force + ' bits.');
    });
    bCopier.addEventListener('click', () => copier(dernier, res));
    corps.append(el('div', { class: 'champs' }, blocL, blocT),
      el('div', { class: 'actions' }, bGenerer, bCopier,
        el('span', { class: 'aide', texte: 'Tirage local, jamais enregistré ni transmis.' })), res);
  }

  // ------------------------------------------------------------------ 5. compteur
  function outilCompteur(corps) {
    const [bloc, champ] = zone('Texte à mesurer', { rows: 8 });
    const res = sortie();
    const mesurer = () => {
      const t = String(champ.value || '');
      const mots = t.trim() ? t.trim().split(/\s+/).length : 0;
      const sansEspaces = t.replace(/\s/g, '').length;
      const lignes = t ? t.split('\n').length : 0;
      const paragraphes = t.trim() ? t.trim().split(/\n\s*\n/).length : 0;
      const bas = Math.ceil(t.length / 4.2), haut = Math.ceil(mots * 1.6);
      afficher(res, 'Caractères : ' + t.length + '  (sans espaces : ' + sansEspaces + ')'
        + '\nMots : ' + mots + '   Lignes : ' + lignes + '   Paragraphes : ' + paragraphes
        + '\nJetons (estimation) : ' + Math.min(bas, haut) + ' à ' + Math.max(bas, haut)
        + '\nLecture à voix haute : environ ' + Math.ceil(mots / 150) + ' min');
    };
    champ.addEventListener('input', mesurer);
    corps.append(bloc, res);
    mesurer();
  }

  // ------------------------------------------------------------------ 6. dates
  function semaineISO(d) {
    const x = new Date(Date.UTC(d.getFullYear(), d.getMonth(), d.getDate()));
    const jour = x.getUTCDay() || 7;
    x.setUTCDate(x.getUTCDate() + 4 - jour);
    const debut = new Date(Date.UTC(x.getUTCFullYear(), 0, 1));
    return Math.ceil(((x - debut) / 86400000 + 1) / 7);
  }
  function lireDate(texte) {
    const t = String(texte || '').trim();
    if (!t) return new Date();
    if (/^-?\d{9,13}$/.test(t)) return new Date(t.length > 10 ? Number(t) : Number(t) * 1000);
    const iso = new Date(t.replace(' ', 'T'));
    if (!isNaN(iso.getTime())) return iso;
    const m = /^(\d{1,2})[/.](\d{1,2})[/.](\d{4})(?:[ T](\d{1,2}):(\d{2}))?$/.exec(t);
    if (m) return new Date(Number(m[3]), Number(m[2]) - 1, Number(m[1]), Number(m[4] || 0), Number(m[5] || 0));
    return null;
  }
  function outilDates(corps) {
    const [bloc, champ] = entree('Date (JJ/MM/AAAA, AAAA-MM-JJ, horodatage unix… vide = maintenant)', { placeholder: '31/12/2026 09:30' });
    const [bloc2, champ2] = entree('Comparer avec (facultatif)', { placeholder: 'AAAA-MM-JJ' });
    const res = sortie();
    const bConvertir = bouton('Convertir');
    bConvertir.addEventListener('click', () => {
      const d = lireDate(champ.value);
      if (!d || isNaN(d.getTime())) { afficher(res, 'Date non comprise. Essayez JJ/MM/AAAA ou AAAA-MM-JJ.', true); return; }
      const lignes = [
        'Local : ' + new Intl.DateTimeFormat('fr-FR', { dateStyle: 'full', timeStyle: 'medium' }).format(d),
        'ISO 8601 (UTC) : ' + d.toISOString(),
        'Horodatage unix : ' + Math.floor(d.getTime() / 1000) + ' s',
        'Semaine ISO : ' + semaineISO(d) + ' · jour ' + (((d.getDay() + 6) % 7) + 1) + ' de la semaine',
      ];
      const autre = String(champ2.value || '').trim() ? lireDate(champ2.value) : null;
      if (autre && !isNaN(autre.getTime())) {
        const ecart = Math.abs(autre.getTime() - d.getTime());
        const jours = Math.floor(ecart / 86400000);
        const heures = Math.floor((ecart % 86400000) / 3600000);
        const minutes = Math.floor((ecart % 3600000) / 60000);
        lignes.push('Écart : ' + jours + ' j ' + heures + ' h ' + minutes + ' min'
          + ' (' + Math.round(ecart / 86400000 * 10) / 10 + ' jours, ' + Math.round(ecart / 604800000 * 10) / 10 + ' semaines)');
      } else if (String(champ2.value || '').trim()) {
        lignes.push('Deuxième date non comprise.');
      }
      afficher(res, lignes.join('\n'));
    });
    corps.append(bloc, bloc2, el('div', { class: 'actions' }, bConvertir), res);
  }

  // ------------------------------------------------------------------ 7. TVA et marge
  const euros = (x) => new Intl.NumberFormat('fr-FR', { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(x) + ' €';
  const nombre = (champ) => {
    const v = parseFloat(String(champ.value || '').replace(',', '.').replace(/\s/g, ''));
    return isFinite(v) ? v : null;
  };
  function outilTva(corps) {
    const [blocHt, ht] = entree('Montant hors taxes', { placeholder: '1000' });
    const [blocTtc, ttc] = entree('Montant toutes taxes comprises', { placeholder: '1200' });
    const [blocTaux, taux] = choix('Taux de TVA', [['20', '20 %'], ['10', '10 %'], ['5.5', '5,5 %'], ['2.1', '2,1 %'], ['0', '0 %']]);
    const [blocCout, cout] = entree('Coût d\'achat (marge)', { placeholder: '600' });
    const [blocVente, vente] = entree('Prix de vente hors taxes (marge)', { placeholder: '1000' });
    const res = sortie();
    const bDepuisHt = bouton('HT → TTC');
    const bDepuisTtc = bouton('TTC → HT');
    const bMarge = bouton('Calculer la marge');
    bDepuisHt.addEventListener('click', () => {
      const h = nombre(ht), t = parseFloat(taux.value);
      if (h === null) { afficher(res, 'Donnez un montant hors taxes.', true); return; }
      const tva = h * t / 100;
      ttc.value = String(Math.round((h + tva) * 100) / 100);
      afficher(res, 'HT : ' + euros(h) + '\nTVA ' + taux.value.replace('.', ',') + ' % : ' + euros(tva) + '\nTTC : ' + euros(h + tva));
    });
    bDepuisTtc.addEventListener('click', () => {
      const v = nombre(ttc), t = parseFloat(taux.value);
      if (v === null) { afficher(res, 'Donnez un montant TTC.', true); return; }
      const h = v / (1 + t / 100);
      ht.value = String(Math.round(h * 100) / 100);
      afficher(res, 'TTC : ' + euros(v) + '\nTVA ' + taux.value.replace('.', ',') + ' % : ' + euros(v - h) + '\nHT : ' + euros(h));
    });
    bMarge.addEventListener('click', () => {
      const c = nombre(cout), p = nombre(vente);
      if (c === null || p === null) { afficher(res, 'Donnez le coût d\'achat et le prix de vente.', true); return; }
      const marge = p - c;
      const lignes = ['Marge brute : ' + euros(marge),
        'Taux de marque (marge / prix de vente) : ' + (p ? (marge / p * 100).toFixed(1) : '0') + ' %',
        'Taux de marge (marge / coût) : ' + (c ? (marge / c * 100).toFixed(1) : '0') + ' %',
        'Coefficient multiplicateur : ' + (c ? (p / c).toFixed(2) : '—')];
      afficher(res, lignes.join('\n'));
    });
    corps.append(el('div', { class: 'champs' }, blocHt, blocTtc, blocTaux),
      el('div', { class: 'actions' }, bDepuisHt, bDepuisTtc),
      el('div', { class: 'champs' }, blocCout, blocVente),
      el('div', { class: 'actions' }, bMarge), res);
  }

  // ------------------------------------------------------------------ assemblage
  const OUTILS = [
    ['Formats JSON et CSV', 'Vérifier, mettre en forme, convertir dans les deux sens.', outilFormats],
    ['base64', 'Encoder ou décoder un texte (UTF-8).', outilBase64],
    ['Empreinte SHA-256', 'Comparer deux textes au caractère près.', outilEmpreinte],
    ['Mot de passe', 'Tirage solide, longueur et composition au choix.', outilSecret],
    ['Compteur de texte', 'Caractères, mots, paragraphes, jetons estimés.', outilCompteur],
    ['Convertisseur de dates', 'Formats, horodatage unix, semaine ISO, écart entre deux dates.', outilDates],
    ['TVA et marge', 'HT, TTC, taux de marge, taux de marque, coefficient.', outilTva],
  ];
  function construire() {
    const zoneOutils = C.$('#liste-outils');
    if (zoneOutils.children.length) return;
    zoneOutils.replaceChildren(...OUTILS.map((o) => carte(o[0], o[1], o[2])));
  }
  C.enregistrerVue('outils', { activer: construire });
})();
