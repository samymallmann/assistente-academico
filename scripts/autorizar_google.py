"""Autoriza uma conta Google para o bot e mostra a chave de acesso (refresh token).

Uso (rode uma vez para cada conta):
    python scripts/autorizar_google.py pessoal
    python scripts/autorizar_google.py ufam

Antes, coloque o arquivo baixado do Google Cloud como `client_secret.json` na pasta do projeto.
O token também é gravado no arquivo `.env` (que não vai para o GitHub) para testes locais.
"""
import json
import secrets
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import requests

RAIZ = Path(__file__).resolve().parent.parent
PORTA = 8765
REDIRECT = f"http://localhost:{PORTA}"

ESCOPOS = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("pessoal", "ufam"):
        sys.exit("Uso: python scripts/autorizar_google.py pessoal|ufam")
    conta = sys.argv[1]

    segredo = json.loads((RAIZ / "client_secret.json").read_text(encoding="utf-8"))
    cliente = segredo.get("installed") or segredo.get("web")
    estado = secrets.token_urlsafe(16)

    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": cliente["client_id"],
        "redirect_uri": REDIRECT,
        "response_type": "code",
        "scope": " ".join(ESCOPOS),
        "access_type": "offline",
        "prompt": "consent",
        "state": estado,
    })

    recebido = {}

    class Receptor(BaseHTTPRequestHandler):
        def do_GET(self):
            recebido.update({k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Pronto! Pode fechar esta aba e voltar ao terminal.</h2>".encode())

        def log_message(self, *args):
            pass

    print(f"\nAbrindo o navegador para autorizar a conta '{conta}'.")
    print("Entre com a conta CERTA" + (" (@ufam.edu.br)." if conta == "ufam" else " (seu Gmail pessoal)."))
    print(f"Se o navegador não abrir, copie este link:\n{url}\n")
    webbrowser.open(url)
    HTTPServer(("localhost", PORTA), Receptor).handle_request()

    if recebido.get("state") != estado or "code" not in recebido:
        sys.exit(f"Autorização cancelada ou inválida: {recebido.get('error', recebido)}")

    resp = requests.post("https://oauth2.googleapis.com/token", timeout=30, data={
        "code": recebido["code"],
        "client_id": cliente["client_id"],
        "client_secret": cliente["client_secret"],
        "redirect_uri": REDIRECT,
        "grant_type": "authorization_code",
    })
    tokens = resp.json()
    if "refresh_token" not in tokens:
        sys.exit(f"O Google não devolveu a chave de acesso: {tokens}")

    email = requests.get("https://openidconnect.googleapis.com/v1/userinfo", timeout=30,
                         headers={"Authorization": f"Bearer {tokens['access_token']}"}).json().get("email")

    nome = f"GOOGLE_REFRESH_TOKEN_{conta.upper()}"
    arquivo_env = RAIZ / ".env"
    linhas = arquivo_env.read_text(encoding="utf-8").splitlines() if arquivo_env.exists() else []
    linhas = [l for l in linhas if not l.startswith(f"{nome}=")]
    for chave, valor in (("GOOGLE_CLIENT_ID", cliente["client_id"]), ("GOOGLE_CLIENT_SECRET", cliente["client_secret"])):
        if not any(l.startswith(f"{chave}=") for l in linhas):
            linhas.append(f"{chave}={valor}")
    linhas.append(f"{nome}={tokens['refresh_token']}")
    arquivo_env.write_text("\n".join(linhas) + "\n", encoding="utf-8")

    print(f"Conta autorizada: {email}")
    print(f"Chave gravada em .env como {nome}.")
    print(f"\nNo GitHub, crie o secret {nome} com o valor que está no arquivo .env.")


if __name__ == "__main__":
    main()
