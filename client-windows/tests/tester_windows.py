#!/usr/bin/env python3
"""Test du paquet Windows sous Wine (Python officiel embarqué du paquet), sans écran.

  tester_windows.py --zip cockpit-client-windows.zip [--travail DOSSIER] [--pwsh CHEMIN]

Vérifie : empreinte du Python embarqué = celle de l'installeur, --version, --ecrire-config
(sans clé), --verifier (clé sur l'entrée standard : OK, expiré, refusé, inactif, injoignable),
serveur lancé par pythonw.exe (sans console), --sonder, écran de verrouillage, déverrouillage,
routes /api/*, espace personnel local, Host/Origin, télémétrie Windows (ctypes) envoyée
seulement cockpit ouvert, absence de la clé ; arguments de l'installeur PowerShell (pwsh).
Les .ps1 eux-mêmes ne peuvent pas s'exécuter sous Wine (pas de PowerShell Windows).
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

ICI = os.path.dirname(os.path.abspath(__file__))
COMMUN = os.path.join(ICI, "..", "..", "client-commun", "tests")
sys.path.insert(0, COMMUN)
import verifs_api  # noqa: E402


def chemin_wine(p):
    return "Z:" + os.path.abspath(p).replace("/", "\\")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", required=True)
    ap.add_argument("--travail", default=None)
    ap.add_argument("--pwsh", default=shutil.which("pwsh"))
    a = ap.parse_args()
    w = tempfile.mkdtemp(prefix="e2e-win-", dir=a.travail)
    with zipfile.ZipFile(a.zip) as z:
        z.extractall(os.path.join(w, "extrait"))
    paquet = os.path.join(w, "extrait", "cockpit-client-windows")
    pf, pc = verifs_api.port_libre(), verifs_api.port_libre()
    cles_f = os.path.join(w, "cles.json")
    faux_err = open(os.path.join(w, "faux.err"), "a")

    def lancer_faux():
        pr = subprocess.Popen([sys.executable, "-I", os.path.join(COMMUN, "fausse_passerelle.py"), "--port", str(pf),
                               "--cles", cles_f], stdout=subprocess.DEVNULL, stderr=faux_err)
        assert verifs_api.attendre_port(pf), "fausse passerelle muette"
        return pr

    faux = lancer_faux()
    cles = json.load(open(cles_f))
    banc = verifs_api.Banc(pc, pf, cles)
    banc.controle("prochain_envoi", secondes=0)  # laisse le client cadencer à 3 s pour le test
    ok = banc.ok
    env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    env.update({"WINEPREFIX": os.path.join(w, "prefixe"), "WINEDEBUG": "-all", "WINEDLLOVERRIDES": "mscoree,mshtml="})
    serveur = None
    sorties = []
    try:
        # ---------- Python embarqué : empreinte conforme à l'installeur
        ps1 = open(os.path.join(paquet, "INSTALLER-WINDOWS.ps1"), encoding="utf-8-sig").read()
        attendu = re.search(r"\$PYTHON_SHA256 = '([0-9A-F]{64})'", ps1).group(1).lower()
        nom_zip = re.search(r"\$PYTHON_ZIP = '([^']+)'", ps1).group(1)
        zip_py = os.path.join(paquet, "python", nom_zip)
        reel = hashlib.sha256(open(zip_py, "rb").read()).hexdigest()
        ok("Python embarqué : empreinte = celle de l'installeur", reel == attendu, (reel, attendu))
        ok(".ps1 et A-LIRE en UTF-8 avec BOM, CRLF", all(open(os.path.join(paquet, f), "rb").read().startswith(b"\xef\xbb\xbf\x23") or
                                                        open(os.path.join(paquet, f), "rb").read().startswith(b"\xef\xbb\xbf")
                                                        for f in ("INSTALLER-WINDOWS.ps1", "DESINSTALLER-WINDOWS.ps1", "A-LIRE.txt"))
           and b"\r\n" in open(os.path.join(paquet, "INSTALLER-WINDOWS.bat"), "rb").read())
        py = os.path.join(w, "python")
        with zipfile.ZipFile(zip_py) as z:
            z.extractall(py)
        srv = chemin_wine(os.path.join(paquet, "client", "serveur.py"))
        config = os.path.join(w, "cfg", "config.json")

        def wpy(*args, entree=None, exe="python.exe", delai=120):
            r = subprocess.run(["wine", os.path.join(py, exe), "-I", srv, *args], env=env, input=entree,
                               capture_output=True, timeout=delai)
            sortie = r.stdout.decode("utf-8", "replace") + r.stderr.decode("utf-8", "replace")
            sorties.append(sortie)
            return r.returncode, sortie

        rc, s = wpy("--version")
        ok("Wine : python.exe --version", rc == 0 and verifs_api.VERSION_CLIENT in s, s[-200:])
        rc, s = wpy("--ecrire-config", "--config", chemin_wine(config), "--passerelle", "http://127.0.0.1:%d" % pf)
        conf = json.load(open(config))
        ok("--ecrire-config -> config SANS clé", rc == 0 and conf == {"passerelle": "http://127.0.0.1:%d" % pf},
           (rc, conf, s[-200:]))
        with open(config, "w") as f:   # configuration d'une version précédente, avec la clé dedans
            json.dump({"passerelle": "http://127.0.0.1:%d" % pf, "cle": cles["valide"]}, f)
        rc, s = wpy("--ecrire-config", "--config", chemin_wine(config))
        ok("--ecrire-config purge une clé laissée par l'ancienne version",
           rc == 0 and "cle" not in json.load(open(config)) and "clé retirée" in s, (rc, s[-200:]))
        rc, s = wpy("--verifier", "--config", chemin_wine(config))
        ok("--verifier sans clé -> 0, identité non testée", rc == 0 and "identité : non testée" in s, s[-300:])
        rc, s = wpy("--verifier", "--config", chemin_wine(config), entree=b"court\n")
        ok("--verifier clé mal formée -> 21", rc == 21, (rc, s[-200:]))
        rc, s = wpy("--verifier", "--config", chemin_wine(config), entree=(cles["valide"] + "\r\n").encode())
        ok("--verifier avec clé (CRLF toléré) -> 0 (accents lisibles)",
           rc == 0 and "identité (avec clé) : OK - Espace test" in s and "clé NON enregistrée" in s, s[-300:])
        ok("--verifier n'enregistre pas la clé", "cle" not in json.load(open(config)), open(config).read())

        # ---------- serveur lancé par pythonw.exe (sans console), télémétrie toutes les 3 s
        journal = os.path.join(w, "journaux", "client.log")
        serveur = subprocess.Popen(["wine", os.path.join(py, "pythonw.exe"), "-I", srv, "--port", str(pc), "--config",
                                    chemin_wine(config), "--journal", chemin_wine(journal), "--intervalle-telemetrie", "3"],
                                   env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        ok("pythonw.exe : le cockpit écoute", verifs_api.attendre_port(pc, 60), pc)
        rc, s = wpy("--sonder", "--port", str(pc))
        ok("--sonder -> 0 (cockpit + passerelle)", rc == 0, s[-200:])
        banc.statiques()
        banc.verrouillage()
        banc.hote_origine()
        banc.routes()
        banc.machine()
        banc.espace()
        banc.delais(longs=False)
        time.sleep(4)
        banc.telemetrie(minimum=2, os_attendu="Windows")
        tel = banc.controle("etat")["telemetrie"][-1]["mesures"]
        ok("télémétrie Windows : mémoire, CPU, disque, uptime mesurés (ctypes)",
           all(tel.get(k) is not None for k in ("ram_total_mo", "ram_utilisee_mo", "cpu_pct", "disque_total_go", "uptime_s", "cpu_modele")), tel)

        # ---------- verrouillage manuel : la télémétrie s'arrête aussitôt
        banc.verrouiller()
        banc.telemetrie_verrouillee(secondes=8)
        ok("réouverture après verrouillage", banc.deverrouiller())
        time.sleep(4)

        # ---------- expiration : routes 401, --verifier 13, télémétrie arrêtée
        banc.expiration()
        rc, s = wpy("--verifier", "--config", chemin_wine(config), entree=(cles["valide"] + "\n").encode())
        ok("--verifier accès expiré -> 13 + message", rc == 13 and "Votre accès a expiré" in s, (rc, s[-200:]))
        time.sleep(7)
        n1 = len(banc.controle("etat")["telemetrie"])
        time.sleep(7)
        n2 = len(banc.controle("etat")["telemetrie"])
        texte = open(journal, encoding="utf-8").read()
        ok("télémétrie arrêtée après expiration", n1 == n2 and "cockpit verrouillé" in texte, (n1, n2))
        banc.controle("retablir")
        for nom, code in (("revoquee", 14), ("inactive", 15)):
            rc, s = wpy("--verifier", "--config", chemin_wine(config), entree=(cles[nom] + "\n").encode())
            ok("--verifier clé %s -> %d" % (nom, code), rc == code, (rc, s[-200:]))
        faux.terminate()
        faux.wait(10)
        rc, s = wpy("--verifier", "--config", chemin_wine(config), entree=(cles["valide"] + "\n").encode())
        ok("--verifier passerelle arrêtée -> 11", rc == 11, (rc, s[-200:]))
        time.sleep(6)
        rc, s = wpy("--sonder", "--port", str(pc))
        ok("--sonder passerelle arrêtée -> 2", rc == 2, (rc, s[-200:]))
        faux = lancer_faux()
        banc.controle("prochain_envoi", secondes=0)

        # ---------- la clé n'apparaît nulle part
        banc.sans_fuite()
        texte = open(journal, encoding="utf-8").read()
        ok("aucune clé dans le journal ni dans les sorties", not any(c in texte or any(c in x for x in sorties)
                                                                     for c in cles.values()))
        ok("aucune clé dans la configuration ni dans les données locales",
           "cle" not in json.load(open(config))
           and not any(c in open(os.path.join(os.path.dirname(config), "donnees.json"), encoding="utf-8").read()
                       for c in cles.values()))

        # ---------- installeur PowerShell : analyse des arguments (pwsh, hors Windows)
        if a.pwsh:
            def pw(*args):
                r = subprocess.run([a.pwsh, "-NoProfile", "-NonInteractive", "-File",
                                    os.path.join(paquet, "INSTALLER-WINDOWS.ps1"), *args], capture_output=True, text=True, timeout=60)
                return r.returncode, r.stdout.strip().splitlines()
            rc, l = pw("--truc")
            ok("ps1 : option inconnue -> erreur + FINI KO", rc == 1 and l[-1] == "FINI - cockpit: KO - passerelle: KO"
               and any("Option inconnue" in x for x in l), l[-3:])
            rc, l = pw("--cle")
            ok("ps1 : --cle sans valeur -> erreur", rc == 1 and any("--cle attend" in x for x in l), l[-3:])
            rc, l = pw("--help")
            ok("ps1 : --help", rc == 0 and any("INSTALLER-WINDOWS.bat --cle" in x for x in l), l[:3])
            ok("ps1 : l'aide dit que la clé n'est pas enregistrée",
               any("jamais enregistr" in x for x in l), l[:12])
    finally:
        if serveur:
            serveur.terminate()
        ws = shutil.which("wineserver") or next(iter(sorted(glob.glob("/usr/lib/*/wine/wineserver*") +
                                                         glob.glob("/usr/lib/wine/wineserver*"))), None)
        if ws:
            subprocess.run([ws, "-k"], env=env, capture_output=True)
        try:
            faux.terminate()
            faux.wait(10)
        except Exception:
            pass

    total, ko = banc.bilan()
    for nom, res, detail in banc.resultats:
        print(("OK  " if res else "KO  ") + nom + ("" if res else "   -> " + str(detail)[:400]))
    print("\nBILAN windows (Wine) : %d vérifications, %d échec(s) ; travail : %s" % (total, len(ko), w))
    return 1 if ko else 0


if __name__ == "__main__":
    sys.exit(main())
