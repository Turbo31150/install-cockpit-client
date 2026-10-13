"""Pilote Chrome minimal par le protocole DevTools (WebSocket, bibliothèque standard).

Sert UNIQUEMENT aux tests : permet de poser le cookie de session puis de piloter la page.
"""
import base64
import json
import os
import secrets
import socket
import struct
import subprocess
import tempfile
import time
import urllib.request


class WS:
    def __init__(self, url, delai=20):
        sans = url.split("://", 1)[1]
        hote_port, _, chemin = sans.partition("/")
        hote, _, port = hote_port.partition(":")
        self.sock = socket.create_connection((hote, int(port or 80)), timeout=delai)
        cle = base64.b64encode(secrets.token_bytes(16)).decode()
        requete = ("GET /%s HTTP/1.1\r\nHost: %s\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                   "Sec-WebSocket-Key: %s\r\nSec-WebSocket-Version: 13\r\n\r\n" % (chemin, hote_port, cle))
        self.sock.sendall(requete.encode())
        self.tampon = b""
        while b"\r\n\r\n" not in self.tampon:
            self.tampon += self.sock.recv(4096)
        entetes, _, self.tampon = self.tampon.partition(b"\r\n\r\n")
        assert b"101" in entetes.split(b"\r\n")[0], entetes[:200]
        self.n = 0

    def _lire(self, n):
        while len(self.tampon) < n:
            morceau = self.sock.recv(65536)
            if not morceau:
                raise EOFError("connexion fermée")
            self.tampon += morceau
        d, self.tampon = self.tampon[:n], self.tampon[n:]
        return d

    def envoyer(self, objet):
        self.n += 1
        objet["id"] = self.n
        charge = json.dumps(objet).encode()
        entete = b"\x81"
        masque = secrets.token_bytes(4)
        taille = len(charge)
        if taille < 126:
            entete += bytes([0x80 | taille])
        elif taille < 65536:
            entete += bytes([0x80 | 126]) + struct.pack(">H", taille)
        else:
            entete += bytes([0x80 | 127]) + struct.pack(">Q", taille)
        masquee = bytes(o ^ masque[i % 4] for i, o in enumerate(charge))
        self.sock.sendall(entete + masque + masquee)
        return self.n

    def recevoir(self):
        while True:
            tete = self._lire(2)
            code = tete[0] & 0x0F
            taille = tete[1] & 0x7F
            if taille == 126:
                taille = struct.unpack(">H", self._lire(2))[0]
            elif taille == 127:
                taille = struct.unpack(">Q", self._lire(8))[0]
            charge = self._lire(taille) if taille else b""
            if code == 1:
                return json.loads(charge.decode("utf-8"))
            if code == 8:
                raise EOFError("fermeture")

    def appeler(self, methode, **params):
        ident = self.envoyer({"method": methode, "params": params})
        fin = time.time() + 60
        while time.time() < fin:
            msg = self.recevoir()
            if msg.get("id") == ident:
                if "error" in msg:
                    raise RuntimeError("%s : %s" % (methode, msg["error"]))
                return msg.get("result", {})
        raise TimeoutError(methode)

    def fermer(self):
        try:
            self.sock.close()
        except OSError:
            pass


class Navigateur:
    def __init__(self, chrome, profil=None):
        self.profil = profil or tempfile.mkdtemp(prefix="cdp-")
        self.proc = subprocess.Popen(
            [chrome, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
             "--disable-extensions", "--remote-debugging-port=0", "--user-data-dir=" + self.profil, "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=dict(os.environ, TMPDIR=self.profil))
        fichier = os.path.join(self.profil, "DevToolsActivePort")
        fin = time.time() + 40
        port = None
        while time.time() < fin:
            if os.path.exists(fichier):
                lignes = open(fichier).read().split("\n")
                if lignes and lignes[0].strip().isdigit():
                    port = int(lignes[0].strip())
                    break
            time.sleep(0.3)
        if port is None:
            raise RuntimeError("Chrome n'a pas annoncé son port DevTools")
        self.base = "http://127.0.0.1:%d" % port
        cible = None
        fin = time.time() + 20
        while time.time() < fin and cible is None:
            liste = json.load(urllib.request.urlopen(self.base + "/json/list", timeout=10))
            for t in liste:
                if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
                    cible = t
                    break
            time.sleep(0.3)
        if cible is None:
            raise RuntimeError("aucun onglet DevTools")
        self.ws = WS(cible["webSocketDebuggerUrl"])
        self.ws.appeler("Page.enable")
        self.ws.appeler("Runtime.enable")
        self.ws.appeler("Network.enable")

    def cookie(self, nom, valeur, url):
        self.ws.appeler("Network.setCookie", name=nom, value=valeur, url=url, path="/",
                        httpOnly=True, sameSite="Strict")

    def aller(self, url, attente=2.0):
        self.ws.appeler("Page.navigate", url=url)
        time.sleep(attente)

    def evaluer(self, expression, attente=0.0):
        if attente:
            time.sleep(attente)
        r = self.ws.appeler("Runtime.evaluate", expression=expression, returnByValue=True, awaitPromise=True)
        if r.get("exceptionDetails"):
            raise RuntimeError(json.dumps(r["exceptionDetails"])[:300])
        return r.get("result", {}).get("value")

    def dom(self):
        return self.evaluer("document.documentElement.outerHTML")

    def fermer(self):
        try:
            self.ws.fermer()
        finally:
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
