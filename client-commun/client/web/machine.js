'use strict';
/* Rubrique « Ma machine » (« Mon Mac » / « Mon PC ») : l'état de CET ordinateur et des conseils adaptés.
   Les mesures viennent du serveur local (/api/machine) : ce sont les mêmes champs que la supervision,
   rien de plus ne sort. Les conseils sont calculés sur cet ordinateur. */
(function () {
  const C = window.CK;
  if (!C) return;
  const $ = C.$, el = C.el;
  const RAFRAICHIR_MS = 60000;
  let minuteur = null;
  let dernier = null;

  const plateforme = String((navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '');
  const NOM = /mac/i.test(plateforme) ? 'Mon Mac' : (/win/i.test(plateforme) ? 'Mon PC' : 'Ma machine');

  function nombre(v, decimales) {
    if (v === null || v === undefined || !Number.isFinite(Number(v))) return '?';
    return new Intl.NumberFormat('fr-FR', { maximumFractionDigits: decimales === undefined ? 1 : decimales }).format(Number(v));
  }
  function depuis(secondes) {
    const s = Number(secondes);
    if (!Number.isFinite(s) || s <= 0) return '';
    const j = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600);
    if (j) return 'Allumé depuis ' + j + ' jour' + (j > 1 ? 's' : '') + (h ? ' ' + h + ' h' : '');
    const mn = Math.floor((s % 3600) / 60);
    return 'Allumé depuis ' + (h ? h + ' h ' : '') + mn + ' min';
  }
  function barre(id, valeur, max) {
    const b = $(id);
    if (!b) return;
    if (Number.isFinite(valeur) && Number.isFinite(max) && max > 0) {
      b.max = max; b.value = Math.min(valeur, max); b.hidden = false;
    } else {
      b.hidden = true;
    }
  }

  // Chaque conseil propose une tâche toute prête, créée dans « Mes tâches » (sur cet ordinateur) d'un clic.
  function boutonTache(t, existantes) {
    const deja = existantes.has(String(t.titre).toLowerCase());
    const b = el('button', { type: 'button', class: 'bouton secondaire petit', disabled: deja,
                             texte: deja ? 'Déjà dans mes tâches' : 'Ajouter à mes tâches' });
    b.addEventListener('click', async () => {
      b.disabled = true;
      const r = await C.api('/api/taches/creer', { corps: { titre: t.titre, detail: t.detail || '', priorite: t.priorite || 'normale' } });
      if (r.ok) {
        b.textContent = 'Ajoutée à mes tâches';
        existantes.add(String(t.titre).toLowerCase());
      } else {
        b.disabled = false;
        C.bandeau('machine', C.messageErreur(r));
      }
    });
    return b;
  }
  function ligneConseil(c, existantes) {
    const li = el('li', { class: 'conseil' + (c.niveau === 'attention' ? ' attention' : '') }, el('span', { texte: c.texte }));
    if (c.tache && c.tache.titre) li.append(el('div', { class: 'conseil-action' }, boutonTache(c.tache, existantes)));
    return li;
  }

  function afficher(m, existantes) {
    dernier = m;
    $('#machine-cpu').textContent = m.cpu_modele || 'Processeur';
    const charge = Number.isFinite(Number(m.cpu_pct)) && m.cpu_pct !== null ? ' · charge ' + nombre(m.cpu_pct, 0) + ' %' : '';
    $('#machine-cpu-note').textContent = (m.coeurs ? m.coeurs + ' cœurs' : '') + charge;

    const ramT = Number(m.ram_total_mo), ramU = Number(m.ram_utilisee_mo);
    $('#machine-ram').textContent = Number.isFinite(ramT) && ramT > 0
      ? (Number.isFinite(ramU) && m.ram_utilisee_mo !== null ? nombre(ramU / 1024) + ' Go utilisés sur ' : '') + nombre(ramT / 1024, 0) + ' Go'
      : 'Inconnue';
    barre('#machine-ram-barre', ramU, ramT);

    const dT = Number(m.disque_total_go), dL = Number(m.disque_libre_go);
    const disqueConnu = Number.isFinite(dT) && dT > 0 && m.disque_libre_go !== null && Number.isFinite(dL);
    $('#machine-disque').textContent = disqueConnu ? nombre(dL) + ' Go libres sur ' + nombre(dT, 0) + ' Go' : 'Inconnu';
    barre('#machine-disque-barre', disqueConnu ? dT - dL : NaN, dT);
    $('#machine-disque-note').textContent = disqueConnu ? 'Occupé à ' + nombre((dT - dL) / dT * 100, 0) + ' %' : '';
    $('#machine-disque-bloc').classList.toggle('alerte', disqueConnu && dL / dT < 0.10);

    $('#machine-os').textContent = [m.os, m.version_os].filter(Boolean).join(' ') || 'Inconnu';
    $('#machine-uptime').textContent = depuis(m.uptime_s);
    $('#machine-version').textContent = m.version_client || '–';

    const liste = $('#machine-conseils');
    const conseils = Array.isArray(m.conseils) ? m.conseils : [];
    liste.replaceChildren(...(conseils.length
      ? conseils.map((c) => ligneConseil(c, existantes || new Set()))
      : [el('li', { class: 'vide', texte: 'Tout va bien : aucun conseil particulier pour le moment.' })]));
    const d = m.mesure_le ? new Date(m.mesure_le) : null;
    $('#machine-date').textContent = d && !isNaN(d.getTime())
      ? 'Mesuré à ' + new Intl.DateTimeFormat('fr-FR', { timeStyle: 'short' }).format(d) : '';
    resumeAccueil();
  }

  // Une ligne sous la salutation de la page d'accueil : l'essentiel de la machine, sans chiffres inutiles.
  function resumeAccueil() {
    const zone = $('#accueil-machine');
    if (!zone || !dernier) return;
    const m = dernier;
    const morceaux = [];
    if (m.cpu_modele) morceaux.push(m.cpu_modele);
    if (m.ram_total_mo) morceaux.push(nombre(m.ram_total_mo / 1024, 0) + ' Go de mémoire');
    if (m.disque_total_go && m.disque_libre_go !== null && m.disque_libre_go !== undefined) {
      morceaux.push(m.disque_libre_go / m.disque_total_go < 0.10 ? 'disque presque plein' : nombre(m.disque_libre_go, 0) + ' Go libres');
    }
    zone.replaceChildren();
    if (!morceaux.length) return;
    zone.append(NOM + ' : ' + morceaux.join(' · ') + ' — ',
      el('a', { href: '#/machine', texte: 'voir le détail' }));
  }

  async function charger() {
    if (C.etat.verrouille || C.etat.expire) return;
    const r = await C.api('/api/machine', { delai: 20000 });
    if (C.etat.verrouille || C.etat.expire) return;
    if (!r.ok) { C.bandeau('machine', C.messageErreur(r)); return; }
    C.bandeau('machine', null);
    // tâches encore à faire : évite d'ajouter deux fois la même
    const t = await C.api('/api/taches?etat=a_faire');
    const existantes = new Set(t.ok && Array.isArray((t.donnees || {}).taches)
      ? t.donnees.taches.map((x) => String(x.titre || '').toLowerCase()) : []);
    afficher(r.donnees || {}, existantes);
  }
  function arreter() {
    if (minuteur) { clearInterval(minuteur); minuteur = null; }
  }

  window.CKMachine = { resume: () => { if (dernier) resumeAccueil(); else charger(); } };

  C.enregistrerVue('machine', {
    demarrer: () => {
      const bouton = $('#rubrique-machine');
      if (bouton) bouton.textContent = NOM;
      $('#t-machine').textContent = NOM;
      $('#machine-actualiser').addEventListener('click', charger);
    },
    activer: () => {
      charger();
      arreter();
      minuteur = setInterval(() => { if (C.etat.vue === 'machine') charger(); else arreter(); }, RAFRAICHIR_MS);
    },
    oublier: () => {   // cockpit verrouillé : rien ne reste affiché
      arreter();
      dernier = null;
      for (const s of ['#machine-cpu', '#machine-cpu-note', '#machine-ram', '#machine-disque', '#machine-disque-note',
                       '#machine-os', '#machine-uptime', '#machine-date', '#machine-version']) {
        const n = $(s);
        if (n) n.textContent = '';
      }
      const liste = $('#machine-conseils');
      if (liste) liste.replaceChildren();
      for (const s of ['#machine-ram-barre', '#machine-disque-barre']) { const b = $(s); if (b) b.hidden = true; }
    },
  });
})();
