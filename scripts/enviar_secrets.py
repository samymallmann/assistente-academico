"""Envia os valores do arquivo .env para os Secrets do repositório no GitHub (sem mostrar na tela).

Uso:
    python scripts/enviar_secrets.py

Precisa do GitHub CLI logado (gh auth login). CPF e senha do eCampus não ficam no .env:
cadastre-os direto com `gh secret set ECAMPUS_USUARIO` e `gh secret set ECAMPUS_SENHA`.
"""
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
NOMES = [
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "CLAUDE_CODE_OAUTH_TOKEN", "NOTION_TOKEN",
    "GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN_PESSOAL", "GOOGLE_REFRESH_TOKEN_UFAM",
]


def main():
    gh = shutil.which("gh")
    if not gh:
        sys.exit("GitHub CLI (gh) não encontrado.")
    # Repositório de destino: o "origin" desta pasta (ou passe usuario/repo como argumento).
    REPO = sys.argv[1] if len(sys.argv) > 1 else subprocess.run(
        [gh, "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        cwd=RAIZ, capture_output=True, text=True).stdout.strip()
    if not REPO:
        sys.exit("Não descobri o repositório. Rode: python scripts/enviar_secrets.py usuario/repositorio")
    valores = {}
    for linha in (RAIZ / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in linha:
            chave, valor = linha.split("=", 1)
            valores[chave.strip()] = valor.strip()

    faltando = [n for n in NOMES if not valores.get(n)]
    for nome in NOMES:
        if nome in faltando:
            continue
        # O valor vai pela entrada padrão, nunca aparece na tela nem na lista de processos.
        r = subprocess.run([gh, "secret", "set", nome, "--repo", REPO], input=valores[nome],
                           capture_output=True, text=True)
        print(f"{'✓' if r.returncode == 0 else '✗'} {nome}" + (f"  ({r.stderr.strip()})" if r.returncode else ""))

    if faltando:
        print(f"\nAinda faltam no .env: {', '.join(faltando)}")
    print("\nAgora cadastre o eCampus (vai pedir o valor, sem mostrar):")
    print(f"  gh secret set ECAMPUS_USUARIO --repo {REPO}")
    print(f"  gh secret set ECAMPUS_SENHA --repo {REPO}")


if __name__ == "__main__":
    main()
