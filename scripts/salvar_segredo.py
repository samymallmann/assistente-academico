"""Guarda um token/senha no arquivo .env sem mostrar na tela.

Uso:
    python scripts/salvar_segredo.py CLAUDE_CODE_OAUTH_TOKEN
    python scripts/salvar_segredo.py NOTION_TOKEN
"""
import sys
from getpass import getpass
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def main():
    if len(sys.argv) != 2:
        sys.exit("Uso: python scripts/salvar_segredo.py NOME_DO_SEGREDO")
    nome = sys.argv[1]
    valor = getpass(f"Cole o valor de {nome} (não aparece enquanto digita) e aperte Enter: ").strip()
    if not valor:
        sys.exit("Nada foi colado.")

    arquivo = RAIZ / ".env"
    linhas = arquivo.read_text(encoding="utf-8").splitlines() if arquivo.exists() else []
    linhas = [l for l in linhas if not l.startswith(f"{nome}=")] + [f"{nome}={valor}"]
    arquivo.write_text("\n".join(linhas) + "\n", encoding="utf-8")
    print(f"{nome} gravado no .env ({len(valor)} caracteres).")


if __name__ == "__main__":
    main()
