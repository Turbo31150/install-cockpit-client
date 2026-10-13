#!/usr/bin/env python3
"""Scan d'anonymat BLOQUANT d'un paquet client (dossier ou archive .zip).

  scan_anonymat.py CHEMIN [--termes FICHIER] [--terme MOT ...] [--exclure-contenu MOTIF ...]
                         [--ip-autorisee IP ...] [--autoriser CHAINE_EXACTE ...]

Cherche (insensible à la casse) : termes interdits, adresses IPv4 non autorisées, adresses
e-mail, chemins personnels, clés/jetons, chaînes à forte entropie. Analyse aussi les noms de
fichiers et le commentaire de l'archive. Les termes interdits propres à l'exploitant (noms,
machines, projets internes) sont lus dans un fichier privé (--termes, un terme par ligne,
« # » = commentaire) qui n'est jamais publié. Seules les chaînes de marque exactes de
MARQUES_AUTORISEES sont tolérées (sensible à la casse) ; toute autre occurrence d'un terme
interdit reste bloquante. Code de sortie : 0 propre, 1 fuite trouvée, 2 erreur.
--exclure-contenu ne dispense que du contenu (les noms restent analysés) : à réserver aux
binaires officiels vérifiés par empreinte.
"""
import argparse
import fnmatch
import ipaddress
import math
import os
import re
import sys
import unicodedata
import zipfile
from collections import Counter

TERMES = ["/" + "home" + "/"]
MARQUES_AUTORISEES = ["AlkymIA · Turbo OS", "AlkymIA", "Turbo OS"]
IP_AUTORISEES = {"127.0.0.1", "100.111.120.117"}
MOTIFS = {
    "cle_ck": r"\bck_[A-Za-z0-9_-]{16,}",
    "cle_api": r"\b(?:sk-(?:ant-)?|ghp_|gho_|github_pat_|xox[abprs]-|tskey-|hf_|AKIA|AIza|whsec_)[A-Za-z0-9_-]{10,}",
    "stripe": r"\b(?:sk|pk|rk)_(?:live|test)_[A-Za-z0-9]{8,}",
    "bearer": r"Bearer\s+[A-Za-z0-9._~+/=-]{20,}",
    "jwt": r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    "cle_privee": r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
    "hexa_long": r"\b[0-9a-fA-F]{40,}\b",
    "email": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
    "chemin_perso": r"/Users/(?!Shared\b)[A-Za-z][A-Za-z0-9._-]*|[A-Za-z]:\\Users\\(?!Public\b)[A-Za-z][^\\\s\"']*",
    "tailnet_dns": r"\.ts\.net\b",
    "mot_de_passe": r"(?i)\b(?:password|passwd|mdp|mot_de_passe|secret|api_?key|token)\s*[:=]\s*['\"][^'\"\s]{6,}['\"]",
}
RX_IP = re.compile(r"(?<![\d.])((?:\d{1,3}\.){3}\d{1,3})(?![\d.])")
RX_LONG = re.compile(r"[A-Za-z0-9+/_=-]{40,}")


def entropie(s):
    c = Counter(s)
    return -sum(v / len(s) * math.log2(v / len(s)) for v in c.values())


def normaliser(t):
    return unicodedata.normalize("NFC", t).casefold()


AUTORISEES = []  # chaînes exactes tolérées (--autoriser), p. ex. empreinte publiée d'un binaire officiel


def analyser(nom, texte, termes, ips, trouves):
    for chaine in AUTORISEES:
        texte = re.sub(re.escape(chaine), " ", texte, flags=re.IGNORECASE)
    neutre = texte
    for marque in MARQUES_AUTORISEES:
        neutre = neutre.replace(marque, " ")
    bas = normaliser(neutre)
    formes = {t: {normaliser(t), unicodedata.normalize("NFD", t).casefold()} for t in termes}
    for terme, variantes in formes.items():
        for v in variantes:
            i = bas.find(v)
            if i >= 0:
                extrait = neutre[max(0, i - 25): i + len(v) + 25].replace("\n", " ")
                trouves.append((nom, "terme « %s »" % terme, extrait))
                break
    for code, motif in MOTIFS.items():
        for m in re.finditer(motif, texte):
            trouves.append((nom, code, m.group(0)[:60]))
    for m in RX_IP.finditer(texte):
        ip = m.group(1)
        try:
            ipaddress.ip_address(ip)
        except ValueError:
            continue
        if ip not in ips:
            trouves.append((nom, "ip_non_autorisee", ip))
    for m in RX_LONG.finditer(texte):
        s = m.group(0)
        # chaîne de type clé : forte entropie ET au moins 4 chiffres (les chemins et mots composés n'en ont pas)
        if sum(ch.isdigit() for ch in s) >= 4 and entropie(s) > 4.2:
            trouves.append((nom, "forte_entropie", s[:50]))


def decoder(octets):
    try:
        return octets.decode("utf-8")
    except UnicodeDecodeError:
        return octets.decode("latin-1")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("chemin")
    p.add_argument("--termes", action="append", default=[], help="fichier de termes privés")
    p.add_argument("--terme", action="append", default=[])
    p.add_argument("--exclure-contenu", action="append", default=[])
    p.add_argument("--ip-autorisee", action="append", default=[])
    p.add_argument("--autoriser", action="append", default=[], help="chaîne exacte tolérée (empreinte publique...)")
    a = p.parse_args()
    AUTORISEES.extend(c for c in a.autoriser if c)
    termes = TERMES + [t for t in a.terme if t]
    for fichier in a.termes:
        with open(fichier, encoding="utf-8") as fh:
            termes += [l.strip() for l in fh if l.strip() and not l.lstrip().startswith("#")]
    termes = list(dict.fromkeys(termes))
    ips = IP_AUTORISEES | set(a.ip_autorisee)
    trouves, nb = [], 0

    def exclu(nom):
        return any(fnmatch.fnmatch(nom, m) or fnmatch.fnmatch(os.path.basename(nom), m) for m in a.exclure_contenu)

    if os.path.isdir(a.chemin):
        for racine, _, fichiers in os.walk(a.chemin):
            for f in sorted(fichiers):
                chemin = os.path.join(racine, f)
                rel = os.path.relpath(chemin, a.chemin).replace(os.sep, "/")
                analyser("[nom] " + rel, rel, termes, ips, trouves)
                if exclu(rel):
                    continue
                with open(chemin, "rb") as fh:
                    analyser(rel, decoder(fh.read()), termes, ips, trouves)
                nb += 1
    elif zipfile.is_zipfile(a.chemin):
        with zipfile.ZipFile(a.chemin) as z:
            if z.comment:
                analyser("[commentaire]", decoder(z.comment), termes, ips, trouves)
            for info in z.infolist():
                if info.filename.startswith("/") or ".." in info.filename.split("/"):
                    trouves.append((info.filename, "chemin_dangereux", info.filename))
                analyser("[nom] " + info.filename, info.filename, termes, ips, trouves)
                if info.extra:
                    analyser("[extra] " + info.filename, decoder(info.extra), termes, ips, trouves)
                if info.is_dir() or exclu(info.filename):
                    continue
                analyser(info.filename, decoder(z.read(info)), termes, ips, trouves)
                nb += 1
    else:
        print("chemin introuvable ou archive invalide : " + a.chemin, file=sys.stderr)
        return 2
    print("scan d'anonymat : %d fichier(s) analysé(s), %d terme(s) interdit(s), IP autorisées : %s"
          % (nb, len(termes), ", ".join(sorted(ips))))
    if trouves:
        for nom, code, extrait in trouves:
            print("  FUITE %-22s %s : %s" % (code, nom, extrait))
        print("RESULTAT : BLOQUE (%d occurrence(s))" % len(trouves))
        return 1
    print("RESULTAT : PROPRE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
