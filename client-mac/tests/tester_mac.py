#!/usr/bin/env python3
"""Test de bout en bout du paquet macOS sous Linux (macOS simulé par des faux outils).

  tester_mac.py --zip cockpit-client-mac.zip [--python /chemin/python3.10] [--longs] [--travail DOSSIER]

Installe le paquet dans un HOME vierge contre la fausse passerelle, puis : écran de
verrouillage (aucune route sans session), déverrouillage par la clé, routes /api/*,
espace personnel local (tâches, planning, notes), Host/Origin, erreurs, délais, télémétrie,
réinstallation, passerelle arrêtée, Tailscale déconnecté/en attente, accès expiré, clé
refusée, accès inactif, interface (Chrome sans écran si présent), absence de la clé partout
(fichiers, journaux, ligne de commande), désinstallation. Code 0 = tout est OK.
"""
import argparse
import json
import os
import plistlib
import shutil
import signal
import socket
import stat
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
import zlib

ICI = os.path.dirname(os.path.abspath(__file__))
COMMUN = os.path.join(ICI, "..", "..", "client-commun", "tests")
sys.path.insert(0, COMMUN)
import verifs_api  # noqa: E402
import verifs_ui  # noqa: E402

STUBS = os.path.abspath(os.path.join(COMMUN, "stubs-mac"))
STUBS_ROOT = os.path.abspath(os.path.join(COMMUN, "stubs-root"))

# --- faux navigateurs et lecture des icônes (vérifications de l'application de bureau) ---
CORPS_NAVIGATEUR = {
    "vivant": "exec sleep 15",                     # la fenêtre reste ouverte
    "relai": "exit 0",                             # fenêtre déjà ouverte : rend la main
    "casse": "echo 'plantage simulé' >&2; exit 1",  # navigateur en échec
}


def faux_navigateur(dossier, nom, mode):
    """Crée DOSSIER/<nom>.app/Contents/MacOS/<nom> et renvoie le chemin du binaire."""
    binaire = os.path.join(dossier, nom + ".app", "Contents", "MacOS", nom)
    os.makedirs(os.path.dirname(binaire), exist_ok=True)
    with open(binaire, "w", encoding="utf-8") as f:
        f.write('#!/bin/bash\nprintf \'%s\\n\' "$0" "$@" >> "$HOME/.stub-navigateur.log"\n'
                'echo $$ >> "$HOME/.stub-navigateur.pids"\n' + CORPS_NAVIGATEUR[mode] + "\n")
    os.chmod(binaire, 0o755)
    return binaire


def lire_icns(chemin):
    """[(type, taille, png)] d'un fichier .icns, ou None s'il est absent/invalide."""
    if not os.path.exists(chemin):
        return None
    data = open(chemin, "rb").read()
    if data[:4] != b"icns" or struct.unpack(">I", data[4:8])[0] != len(data):
        return None
    blocs, o = [], 8
    while o + 8 <= len(data):
        nom, taille = data[o:o + 4].decode("ascii", "replace"), struct.unpack(">I", data[o + 4:o + 8])[0]
        if taille < 8 or o + taille > len(data):
            return None
        png = data[o + 8:o + taille]
        blocs.append((nom, struct.unpack(">II", png[16:24])[0] if png[:4] == b"\x89PNG" else 0, png))
        o += taille
    return blocs


def decoder_png(data):
    """(largeur, hauteur, [lignes RGBA]) d'un PNG RGBA 8 bits non entrelacé, filtre 0."""
    o, largeur, hauteur, idat = 8, 0, 0, b""
    while o + 8 <= len(data):
        n = struct.unpack(">I", data[o:o + 4])[0]
        nom, corps = data[o + 4:o + 8], data[o + 8:o + 8 + n]
        if nom == b"IHDR":
            largeur, hauteur = struct.unpack(">II", corps[:8])
        elif nom == b"IDAT":
            idat += corps
        o += 12 + n
    brut, pas, lignes = zlib.decompress(idat), largeur * 4, []
    for y in range(hauteur):
        debut = y * (pas + 1)
        if brut[debut] != 0:
            return largeur, hauteur, []      # filtre non géré par ce contrôle
        lignes.append(brut[debut + 1:debut + 1 + pas])
    return largeur, hauteur, lignes


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--zip", required=True)
    p.add_argument("--python", default=shutil.which("python3"))
    p.add_argument("--longs", action="store_true", help="tests de délais réels (28/35 s rapide, 60/75 s approfondi)")
    p.add_argument("--travail", default=None)
    a = p.parse_args()

    w = tempfile.mkdtemp(prefix="e2e-mac-", dir=a.travail)
    home = os.path.join(w, "home")
    os.makedirs(home)
    os.makedirs(os.path.join(w, "tmp"))
    with zipfile.ZipFile(a.zip) as z:
        z.extractall(os.path.join(w, "extrait"))
    paquet = os.path.join(w, "extrait", "cockpit-client-mac")
    pf, pc = verifs_api.port_libre(), verifs_api.port_libre()
    cles_f = os.path.join(w, "cles.json")
    sorties = []

    def lancer_faux():
        pr = subprocess.Popen([sys.executable, "-I", os.path.join(COMMUN, "fausse_passerelle.py"), "--port", str(pf),
                               "--cles", cles_f], stdout=subprocess.DEVNULL, stderr=open(os.path.join(w, "faux.err"), "a"))
        assert verifs_api.attendre_port(pf), "fausse passerelle muette"
        return pr

    faux = lancer_faux()
    cles = json.load(open(cles_f))
    banc = verifs_api.Banc(pc, pf, cles)
    ok = banc.ok

    def env(port=pc, chemin=None):
        return {"HOME": home, "USER": os.environ.get("USER", "test"), "LANG": "C.UTF-8", "TMPDIR": os.path.join(w, "tmp"),
                "PATH": chemin or (STUBS + ":/usr/bin:/bin:/usr/sbin:/sbin"), "COCKPIT_PYTHON": a.python,
                "COCKPIT_PORT": str(port)}

    def installer(*args, port=pc, chemin=None, entree=subprocess.DEVNULL, script="INSTALLER-MAC.command"):
        r = subprocess.run(["bash", os.path.join(paquet, script), *args], env=env(port, chemin), stdin=entree,
                           capture_output=True, text=True, timeout=300)
        sortie = r.stdout + r.stderr
        sorties.append(sortie)
        derniere = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
        return r.returncode, derniere, sortie

    passerelle = "http://127.0.0.1:%d" % pf
    support = os.path.join(home, "Library", "Application Support", "Cockpit")
    config = os.path.join(support, "config.json")
    plist = os.path.join(home, "Library", "LaunchAgents", "app.cockpit.client.plist")
    bundle = os.path.join(home, "Applications", "AlkymIA Cockpit.app")
    logs = os.path.join(home, "Library", "Logs", "Cockpit")

    def pid_service():
        f = os.path.join(home, ".stub-launchctl", "app.cockpit.client.pid")
        return int(open(f).read()) if os.path.exists(f) else None

    try:
        # ---------- refus attendus
        rc, der, s = installer("--cle", cles["valide"], chemin="/usr/bin:/bin")
        ok("refus hors macOS", rc != 0 and der == "FINI - cockpit: KO - passerelle: KO" and "destiné à macOS" in s, der)
        rc, der, s = installer("--cle", cles["valide"], chemin=STUBS_ROOT + ":" + STUBS + ":/usr/bin:/bin")
        ok("refus avec sudo", rc != 0 and "sudo" in s and der.startswith("FINI - cockpit: KO"), der)
        rc, der, s = installer("--cle", "court")
        ok("refus clé invalide", rc != 0 and "Clé d'accès invalide" in s and not os.path.exists(support), der)
        rc, der, s = installer("--cle", cles["valide"] + " x")
        ok("refus clé avec espace", rc != 0 and "Clé d'accès invalide" in s, der)
        rc, der, s = installer("--cle", cles["valide"], "--option-inconnue")
        ok("refus option inconnue", rc != 0 and "Option inconnue" in s, der)
        occupe = socket.socket()
        occupe.bind(("127.0.0.1", 0))
        occupe.listen(1)
        rc, der, s = installer("--cle", cles["valide"], "--passerelle", passerelle, "--sans-ouvrir", port=occupe.getsockname()[1])
        occupe.close()
        ok("refus port occupé par un autre programme", rc != 0 and "déjà utilisé" in s, der)

        # ---------- installation HOME vierge
        t = time.time()
        rc, der, s = installer("--cle", cles["valide"], "--passerelle", passerelle, "--sans-ouvrir")
        ok("installation HOME vierge -> OK/OK (%.0f s)" % (time.time() - t),
           rc == 0 and der == "FINI - cockpit: OK - passerelle: OK", der + "\n" + s[-1500:])
        ok("installation : identité vérifiée", "identité (avec clé) : OK - Espace test" in s and "valable jusqu'au" in s)
        mode = lambda c: stat.S_IMODE(os.stat(c).st_mode)
        ok("config.json 600, dossier 700", mode(config) == 0o600 and mode(support) == 0o700, (oct(mode(config)), oct(mode(support))))
        conf = json.load(open(config))
        ok("config.json = {passerelle} SANS la clé", conf == {"passerelle": passerelle}, conf)
        ok("aucun fichier du HOME ne contient la clé après installation",
           not any(c.encode() in open(os.path.join(r, f), "rb").read()
                   for r, _, fs in os.walk(home) for f in fs for c in cles.values()))
        ag = plistlib.load(open(plist, "rb"))
        ok("LaunchAgent app.cockpit.client", ag["Label"] == "app.cockpit.client" and ag["KeepAlive"] and ag["RunAtLoad"]
           and ag["ProgramArguments"][1:3] == ["-I", "-u"] and str(pc) in ag["ProgramArguments"]
           and ag["AssociatedBundleIdentifiers"] == ["app.cockpit.lanceur"], ag["ProgramArguments"])
        info = plistlib.load(open(os.path.join(bundle, "Contents", "Info.plist"), "rb"))
        lanceur = os.path.join(bundle, "Contents", "MacOS", "cockpit")
        ok("AlkymIA Cockpit.app : application visible (LSUIElement faux), lanceur exécutable",
           info["LSUIElement"] is False and info["CFBundleName"] == "AlkymIA Cockpit"
           and info["CFBundleDisplayName"] == "AlkymIA Cockpit" and info["CFBundlePackageType"] == "APPL"
           and info["NSHighResolutionCapable"] is True and os.access(lanceur, os.X_OK),
           {k: info.get(k) for k in ("LSUIElement", "CFBundleName", "NSHighResolutionCapable")})
        ok("AlkymIA Cockpit.app : PkgInfo APPL",
           open(os.path.join(bundle, "Contents", "PkgInfo")).read() == "APPL????")
        r = subprocess.run(["bash", "-n", lanceur], capture_output=True)
        ok("lanceur : syntaxe bash", r.returncode == 0, r.stderr)
        if shutil.which("shellcheck"):
            r = subprocess.run(["shellcheck", "-s", "bash", lanceur], capture_output=True, text=True)
            ok("lanceur : shellcheck", r.returncode == 0, r.stdout[-400:])
        ecoute = subprocess.run(["ss", "-ltnH"], capture_output=True, text=True).stdout
        adresses = sorted({l.split()[3] for l in ecoute.splitlines() if l.split()[3].endswith(":%d" % pc)})
        ok("écoute 127.0.0.1:%d uniquement" % pc, adresses == ["127.0.0.1:%d" % pc], adresses)
        pid1 = pid_service()

        # ---------- icône : .icns fabriqué à l'installation (sips + iconutil simulés)
        icns = os.path.join(bundle, "Contents", "Resources", "cockpit.icns")
        ok("icône : Info.plist déclare CFBundleIconFile", info.get("CFBundleIconFile") == "cockpit",
           info.get("CFBundleIconFile"))
        blocs = lire_icns(icns)
        ok("icône : cockpit.icns valide (sips + iconutil), tailles 16 à 1024",
           blocs and {t for _, t, _ in blocs} >= {16, 32, 64, 128, 256, 512, 1024}
           and all(p[:8] == b"\x89PNG\r\n\x1a\n" for _, _, p in blocs),
           [(n, t) for n, t, _ in (blocs or [])])
        ok("icône : la ligne d'installation annonce sips et iconutil",
           "icone: rendu=sips" in s and "icns=iconutil" in s,
           [l for l in s.splitlines() if "icone:" in l])

        # ---------- application de bureau : fenêtre d'application, détection, replis
        apps = os.path.join(w, "Applications")
        profil_nav = os.path.join(support, "navigateur")

        def cmd_navigateur():
            f = os.path.join(home, ".stub-navigateur.log")
            return open(f, encoding="utf-8").read().strip().splitlines() if os.path.exists(f) else []

        def ouvrir(dossiers=apps, navigateur=None, attente=90, repli=False):
            for f in (".stub-navigateur.log", ".stub-open.log"):
                if os.path.exists(os.path.join(home, f)):
                    os.remove(os.path.join(home, f))
            e = env()
            if dossiers:
                e["COCKPIT_DOSSIERS_APPS"] = dossiers
            if navigateur:
                e["COCKPIT_NAVIGATEUR"] = navigateur
            subprocess.run(["bash", lanceur], env=e, capture_output=True, timeout=attente)
            ouvert = os.path.join(home, ".stub-open.log")
            lire = lambda: open(ouvert, encoding="utf-8").read() if os.path.exists(ouvert) else ""
            for _ in range(50):     # le message d'explication part en arrière-plan
                if not repli or "osascript" in lire():
                    break
                time.sleep(0.1)
            return cmd_navigateur(), lire()

        nav_chrome = faux_navigateur(apps, "Google Chrome", "vivant")
        brave = faux_navigateur(apps, "Brave Browser", "vivant")
        # service arrêté : le lanceur doit le redémarrer avant d'ouvrir la fenêtre
        subprocess.run(["launchctl", "bootout", "gui/1/app.cockpit.client"], env=env())
        argv, ouvert = ouvrir()
        ligne_attendue = [nav_chrome, "--app=http://127.0.0.1:%d/" % pc, "--user-data-dir=" + profil_nav,
                    "--no-first-run", "--no-default-browser-check", "--no-service-autorun",
                    "--disable-extensions", "--disable-background-mode",
                    "--window-size=1180,900", "--window-position=90,60"]
        ok("application : redémarre le service et ouvre une FENÊTRE (Chrome, profil dédié)",
           verifs_api.attendre_port(pc, 5) and argv == ligne_attendue and not ouvert, (argv, ouvert))
        ok("application : rien n'est ouvert dans le navigateur par défaut", not ouvert, ouvert)
        ok("application : profil isolé créé en 700 dans le dossier du cockpit",
           os.path.isdir(profil_nav) and stat.S_IMODE(os.stat(profil_nav).st_mode) == 0o700, profil_nav)
        pid1 = pid_service()        # service relancé par l'application : il ne doit plus bouger
        argv, ouvert = ouvrir()
        ok("application : 2e ouverture, la géométrie mémorisée n'est plus imposée",
           argv == ligne_attendue[:-2] and not ouvert, argv[-3:])
        ok("application : Chrome préféré à Brave (ordre de recherche)", argv and argv[0] == nav_chrome, argv[:1])
        argv, _ = ouvrir(navigateur=brave)
        ok("application : COCKPIT_NAVIGATEUR impose le navigateur", argv and argv[0] == brave, argv[:1])
        seul = os.path.join(w, "Applications-brave")
        brave2 = faux_navigateur(seul, "Brave Browser", "vivant")
        argv, ouvert = ouvrir(dossiers=seul)
        ok("application : repli sur Brave quand Chrome est absent", argv and argv[0] == brave2 and not ouvert, argv[:1])
        edge = os.path.join(w, "Applications-edge")
        edge2 = faux_navigateur(edge, "Microsoft Edge", "vivant")
        faux_navigateur(edge, "Arc", "vivant")
        argv, _ = ouvrir(dossiers=edge)
        ok("application : Edge préféré à Arc", argv and argv[0] == edge2, argv[:1])
        relai = os.path.join(w, "Applications-relai")
        relai2 = faux_navigateur(relai, "Chromium", "relai")
        argv, ouvert = ouvrir(dossiers=relai)
        ok("application : fenêtre déjà ouverte (le navigateur rend la main) -> pas de repli",
           argv and argv[0] == relai2 and not ouvert, (argv[:1], ouvert))
        casse = os.path.join(w, "Applications-casse")
        casse2 = faux_navigateur(casse, "Google Chrome", "casse")
        argv, ouvert = ouvrir(dossiers=casse, repli=True)
        ok("repli 2 : navigateur en échec -> navigateur par défaut + message",
           argv and argv[0] == casse2 and "open http://127.0.0.1:%d/" % pc in ouvert
           and "osascript" in ouvert and "barre d'adresse" in ouvert, (argv[:1], ouvert[-300:]))
        vide = os.path.join(w, "Applications-vide")
        os.makedirs(vide, exist_ok=True)
        argv, ouvert = ouvrir(dossiers=vide, repli=True)
        ok("repli 3 : aucun navigateur compatible -> navigateur par défaut + message",
           not argv and "open http://127.0.0.1:%d/" % pc in ouvert and "Aucun navigateur compatible" in ouvert,
           (argv, ouvert[-300:]))
        perso = os.listdir(os.path.join(home, "Applications"))
        if not os.path.isdir("/Applications") and not any(n.endswith(".app") and "Cockpit" not in n for n in perso):
            argv, ouvert = ouvrir(dossiers=None, repli=True)   # liste par défaut : /Applications, ~/Applications
            ok("application : dossiers fouillés par défaut (aucun ici) -> repli propre",
               not argv and "open http://127.0.0.1:%d/" % pc in ouvert, (argv, ouvert[-200:]))
        journal_fenetre = os.path.join(logs, "fenetre.log")
        texte_fenetre = open(journal_fenetre, encoding="utf-8").read() if os.path.exists(journal_fenetre) else ""
        ok("application : journal des ouvertures (sans la clé)",
           "fenêtre : ouverte avec" in texte_fenetre and "aucun navigateur compatible" in texte_fenetre
           and not any(c in texte_fenetre for c in cles.values()), texte_fenetre[-300:])
        ok("application : le service n'a jamais été arrêté par les ouvertures/fermetures",
           pid_service() == pid1 and verifs_api.attendre_port(pc, 5), (pid1, pid_service()))

        # ---------- verrou : la fenêtre fermée ne laisse aucun accès derrière elle
        ok("verrou : session ouverte avant de fermer la fenêtre", banc.deverrouiller())
        biscuit = banc.cookie
        banc.verrouiller()          # fermeture de la fenêtre + expiration de la session
        argv, _ = ouvrir()
        banc.cookie = biscuit       # le profil du navigateur a gardé le cookie
        st, _, _, j = banc.lire("/api/moi")
        ok("verrou : le cookie gardé par le profil ne rouvre PAS le cockpit",
           st == 401 and (j or {}).get("erreur") == "verrouille", (st, j))
        banc.cookie = None
        ok("verrou : il faut recoller la clé dans la fenêtre rouverte", banc.deverrouiller())

        # ---------- icône sans sips ni iconutil (rendu et format ICNS internes)
        sans = os.path.join(w, "stubs-sans-icone")
        os.makedirs(sans, exist_ok=True)
        for nom in os.listdir(STUBS):
            if nom not in ("sips", "iconutil") and not os.path.exists(os.path.join(sans, nom)):
                os.symlink(os.path.join(STUBS, nom), os.path.join(sans, nom))
        rc, der, s2 = installer("--sans-ouvrir", chemin=sans + ":/usr/bin:/bin:/usr/sbin:/sbin")
        blocs = lire_icns(icns)
        tailles = {t for _, t, _ in (blocs or [])}
        ok("icône sans sips ni iconutil : ICNS écrit en Python (16 à 1024)",
           rc == 0 and blocs and tailles >= {16, 32, 64, 128, 256, 512, 1024}
           and "icone: rendu=interne" in s2 and "icns=interne" in s2, (rc, sorted(tailles)))
        image = [p for _, t, p in (blocs or []) if t == 128]
        larg, haut, lignes = decoder_png(image[0]) if image else (0, 0, [])
        centre = lignes[haut // 2][(larg // 2) * 4:(larg // 2) * 4 + 4] if lignes else b""
        coin = lignes[0][:4] if lignes else b""
        bord = lignes[haut // 2][32:36] if lignes else b""
        ok("icône : rendu conforme au SVG (coin transparent, fond bleu, trait blanc)",
           (larg, haut) == (128, 128) and coin[3] == 0 and bord == bytes((47, 93, 138, 255))
           and centre[3] == 255 and centre[0] > 200 and centre[1] > 200,
           (larg, haut, list(coin), list(bord), list(centre)))
        pid1 = pid_service()

        # ---------- routes, sécurité, délais, télémétrie
        banc.statiques()
        banc.verrouillage()
        banc.hote_origine()
        banc.routes()
        banc.machine()
        banc.espace()
        banc.delais(longs=a.longs)
        time.sleep(2)
        banc.telemetrie(minimum=1, os_attendu="Linux")
        mode_donnees = stat.S_IMODE(os.stat(os.path.join(support, "donnees.json")).st_mode)
        ok("donnees.json créé en 600", mode_donnees == 0o600, oct(mode_donnees))

        # ---------- interface (Chrome sans écran)
        chrome = shutil.which("google-chrome") or shutil.which("chromium")
        # profil Chrome dans un chemin court (socket Unix limitée à 108 caractères)
        profil = tempfile.mkdtemp(prefix="cc-", dir=os.environ.get("COCKPIT_TEST_TMP_COURT") or None)
        def dom():
            r = subprocess.run([chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                                "--user-data-dir=" + profil, "--virtual-time-budget=10000",
                                "--dump-dom", "http://127.0.0.1:%d/" % pc], capture_output=True, text=True, timeout=90,
                               env=dict(os.environ, TMPDIR=profil))
            if not r.stdout.strip():
                print("chrome muet :", r.stderr[-300:], file=sys.stderr)
            return r.stdout
        if chrome:
            d = dom()
            attendus = ["Collez votre clé d'accès", "Ouvrir", "AlkymIA · Turbo OS", "Assistants", "Mes tâches",
                        "Mon planning", "Mes notes", "Outils", "Mon accès", "Verrouiller", "Aucun nom de machine"]
            manquants = [m for m in attendus if m not in d]
            ok("interface : écran de verrouillage et rubriques présentes (Chrome)", not manquants, manquants)
            ok("interface : verrouillée, aucune donnée du compte affichée",
               "Espace test" not in d and "Marketing et commercialisation" not in d
               and "Droit des contrats" not in d and '<main id="principal" hidden=""' in d, d[:400])
            ok("interface : aucune clé ni jeton de session dans le DOM",
               not any(c in d for c in cles.values()) and "cockpit_session" not in d)

        # ---------- réinstallation (clé conservée)
        rc, der, s = installer("--sans-ouvrir")
        conf2 = json.load(open(config))
        ok("réinstallation SANS clé -> OK/OK (la clé est demandée à l'ouverture)",
           rc == 0 and der == "FINI - cockpit: OK - passerelle: OK" and "cle" not in conf2
           and "aucune clé fournie" in s and "identité : non testée" in s, (der, conf2, s[-400:]))
        ok("réinstallation : service redémarré", pid_service() not in (None, pid1), (pid1, pid_service()))
        banc.espace_apres_redemarrage_verrouille()
        ok("après redémarrage : il faut recoller la clé", banc.deverrouiller())
        banc.espace_apres_redemarrage()

        # ---------- interface déverrouillée (Chrome piloté : cookie de session, jamais la clé)
        if chrome:
            verifs_ui.interface_deverrouillee(banc, chrome)
            ok("réouverture après le scénario d'interface", banc.deverrouiller())

        # ---------- passerelle arrêtée
        faux.terminate()
        faux.wait(10)
        time.sleep(6)  # cache de /api/etat
        st, _, _, j = banc.lire("/api/etat")
        ok("passerelle arrêtée : /api/etat joignable=false", st == 200 and not j["passerelle"]["joignable"]
           and j["passerelle"]["erreur"] == "passerelle_indisponible", j)
        st, _, _, j = banc.lire("/api/moi")
        ok("passerelle arrêtée : /api/moi -> 502 message clair", st == 502 and "ne répond pas" in j.get("message", ""), j)
        rc, der, s = installer("--sans-ouvrir")
        ok("installeur, passerelle arrêtée -> cockpit OK, passerelle KO", rc == 2 and der == "FINI - cockpit: OK - passerelle: KO", der)
        faux = lancer_faux()

        # ---------- Tailscale déconnecté / en attente d'approbation (adresse sans réponse)
        muette = "http://198.51.100.7:18999"
        for etat_ts, attendu in (("Stopped", "Tailscale est déconnecté"), ("NeedsMachineAuth", "adhésion pas encore approuvée"),
                                 ("NeedsLogin", "connectez-vous avec le compte")):
            with open(os.path.join(home, ".stub-tailscale"), "w") as f:
                f.write(etat_ts)
            rc, der, s = installer("--passerelle", muette, "--sans-ouvrir")
            ok("Tailscale %s -> passerelle KO + message" % etat_ts, rc == 2 and der.endswith("passerelle: KO") and attendu in s,
               s[-600:])
        time.sleep(6)
        st, _, _, j = banc.lire("/api/etat", delai=60)
        ok("interface : état « Tailscale » expliqué par /api/etat", st == 200 and not j["passerelle"]["joignable"]
           and "Tailscale" in j["passerelle"].get("message", ""), j)
        os.remove(os.path.join(home, ".stub-tailscale"))
        rc, der, s = installer("--passerelle", passerelle, "--sans-ouvrir")
        ok("retour sur la passerelle -> OK/OK", rc == 0 and der == "FINI - cockpit: OK - passerelle: OK", der)

        # ---------- accès expiré (le cockpit a redémarré : il faut recoller la clé)
        banc.controle("debit")
        ok("réouverture après les réinstallations", banc.deverrouiller())
        banc.expiration()
        if chrome:
            d = dom()
            ok("interface : écran « Votre accès a expiré » annoncé, rien d'autre", "Votre accès a expiré." in d
               and '<main id="principal" hidden=""' in d and "Marketing et commercialisation" not in d, d[:300])
        banc.telemetrie_verrouillee(secondes=8)
        rc, der, s = installer("--cle", cles["valide"], "--sans-ouvrir")
        ok("installeur avec clé, accès expiré -> passerelle KO + message", rc == 2 and der.endswith("passerelle: KO")
           and "Votre accès a expiré" in s, s[-500:])
        banc.controle("retablir")

        # ---------- clé refusée / accès pas encore activé
        rc, der, s = installer("--cle", cles["revoquee"], "--sans-ouvrir")
        ok("clé inconnue -> passerelle KO « clé refusée »", rc == 2 and "clé refusée" in s, s[-400:])
        rc, der, s = installer("--cle", cles["inactive"], "--sans-ouvrir")
        ok("accès inactif -> passerelle KO « adhésion »", rc == 2 and "adhésion" in s, s[-400:])
        rc, der, s = installer("--cle", cles["valide"], "--sans-ouvrir")
        ok("retour clé valide -> OK/OK", rc == 0 and der == "FINI - cockpit: OK - passerelle: OK", der)

        # ---------- la clé n'apparaît NULLE PART, pas même dans config.json
        ok("session ouverte pour le dernier contrôle", banc.deverrouiller())
        banc.lire("/api/moi")
        banc.sans_fuite()
        fuites = []
        for racine, _, fichiers in os.walk(home):
            for f in fichiers:
                chemin = os.path.join(racine, f)
                contenu = open(chemin, "rb").read()
                fuites += [(os.path.relpath(chemin, home), n) for n, c in cles.items() if c.encode() in contenu]
        ok("aucune clé dans AUCUN fichier du HOME (config.json compris)", not fuites, fuites)
        ok("config.json sans clé après tous les scénarios", "cle" not in json.load(open(config)), open(config).read())
        ok("aucune clé dans la sortie des installeurs", not any(c in s for s in sorties for c in cles.values()))
        pid = pid_service()
        ligne = open("/proc/%d/cmdline" % pid, "rb").read() if pid else b""
        ok("aucune clé dans la ligne de commande du service", ligne and not any(c.encode() in ligne for c in cles.values()))
        environnement = open("/proc/%d/environ" % pid, "rb").read() if pid else b""
        ok("aucune clé dans l'environnement du service", environnement and not any(c.encode() in environnement for c in cles.values()))
        journal = open(os.path.join(logs, "client.log"), encoding="utf-8").read()
        ok("journal : méthode/chemin/statut sans corps", "POST /api/demander 200" in journal and "Quel prix" not in journal)
        ok("journal : ouverture et verrouillage tracés sans la clé",
           "cockpit déverrouillé" in journal and "cockpit verrouillé" in journal
           and not any(c in journal for c in cles.values()))
        # ---------- verrouillage manuel
        banc.verrouiller()
        ok("réouverture après verrouillage manuel", banc.deverrouiller())

        # ---------- verrou automatique après inactivité (cockpit d'essai, verrou de 6 s)
        dossier2 = os.path.join(w, "verrou")
        os.makedirs(dossier2, exist_ok=True)
        cfg2 = os.path.join(dossier2, "config.json")
        with open(cfg2, "w") as f:
            json.dump({"passerelle": passerelle}, f)
        p2 = verifs_api.port_libre()
        srv2 = subprocess.Popen([a.python, "-I", os.path.join(support, "app", "serveur.py"), "--port", str(p2),
                                 "--config", cfg2, "--journal", os.path.join(dossier2, "client.log"),
                                 "--intervalle-telemetrie", "99999", "--minutes-verrou", "0.1"],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            banc2 = verifs_api.Banc(p2, pf, cles)
            ok("cockpit d'essai démarré (verrou 6 s)", verifs_api.attendre_port(p2, 20), p2)
            banc2.deverrouiller()
            st, _, _, _ = banc2.lire("/api/moi")
            ok("essai : session active juste après le déverrouillage", st == 200, st)
            time.sleep(9)
            st, _, _, j = banc2.lire("/api/moi")
            ok("verrou automatique après 6 s sans activité", st == 401 and (j or {}).get("erreur") == "verrouille"
               and (j or {}).get("motif") == "verrou_inactivite", (st, j))
            st, _, _, j = banc2.lire("/api/etat")
            ok("essai : /api/etat annonce le verrou d'inactivité", st == 200 and j.get("verrouille") is True
               and j.get("motif") == "verrou_inactivite" and "sans activité" in (j.get("message") or ""), j)
            texte2 = open(os.path.join(dossier2, "client.log"), encoding="utf-8").read()
            ok("essai : journal sans la clé", not any(c in texte2 for c in cles.values()))
            banc.resultats.extend(banc2.resultats)
            banc.reponses.extend(banc2.reponses)
        finally:
            srv2.terminate()
            srv2.wait(10)

        # ---------- désinstallation
        r = subprocess.run(["bash", os.path.join(paquet, "DESINSTALLER-MAC.command")], env=env(), capture_output=True, text=True, timeout=60)
        restes = [c for c in (support, plist, bundle, logs) if os.path.exists(c)]
        ok("désinstallation -> tout supprimé, port fermé", r.returncode == 0 and r.stdout.strip().endswith("FINI - cockpit désinstallé")
           and not restes and not verifs_api.attendre_port(pc, 2), (r.stdout[-200:], restes))
    finally:
        subprocess.run(["launchctl", "bootout", "gui/1/app.cockpit.client"], env=env(), capture_output=True)
        pids = os.path.join(home, ".stub-navigateur.pids")      # faux navigateurs restés « ouverts »
        if os.path.exists(pids):
            for ligne in open(pids):
                try:
                    os.kill(int(ligne.strip()), signal.SIGKILL)
                except (ValueError, OSError):
                    pass
        if "profil" in locals():
            shutil.rmtree(profil, ignore_errors=True)
        try:
            faux.terminate()
            faux.wait(10)
        except Exception:
            pass

    total, ko = banc.bilan()
    for nom, res, detail in banc.resultats:
        print(("OK  " if res else "KO  ") + nom + ("" if res else "   -> " + str(detail)[:400]))
    print("\nBILAN mac : %d vérifications, %d échec(s) ; travail : %s" % (total, len(ko), w))
    return 1 if ko else 0


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(1))
    sys.exit(main())
