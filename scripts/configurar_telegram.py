"""Configura o Telegram: pede o token do @BotFather, descobre o seu chat_id e manda uma mensagem de teste.

Uso:
    python scripts/configurar_telegram.py

Antes de rodar, abra o seu bot no Telegram e mande qualquer mensagem para ele (ex.: "oi").
"""
import sys
from getpass import getpass
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent


def gravar_env(valores: dict) -> None:
    arquivo = RAIZ / ".env"
    linhas = arquivo.read_text(encoding="utf-8").splitlines() if arquivo.exists() else []
    linhas = [l for l in linhas if l.split("=", 1)[0] not in valores]
    linhas += [f"{k}={v}" for k, v in valores.items()]
    arquivo.write_text("\n".join(linhas) + "\n", encoding="utf-8")


def main():
    token = getpass("Cole o token do @BotFather (não aparece enquanto digita) e aperte Enter: ").strip()
    api = f"https://api.telegram.org/bot{token}"

    eu = requests.get(f"{api}/getMe", timeout=30).json()
    if not eu.get("ok"):
        sys.exit("Token inválido. Confira se copiou a linha inteira que o @BotFather mandou.")
    print(f"Bot encontrado: @{eu['result']['username']}")

    atualizacoes = requests.get(f"{api}/getUpdates", timeout=30).json().get("result", [])
    chats = {u["message"]["chat"]["id"]: u["message"]["chat"].get("first_name", "")
             for u in atualizacoes if "message" in u}
    if not chats:
        sys.exit(f"Ainda não chegou nenhuma mensagem. Abra https://t.me/{eu['result']['username']}, "
                 "mande um 'oi' e rode este script de novo.")
    chat_id, nome = list(chats.items())[-1]
    print(f"Conversa encontrada com: {nome} (chat_id {chat_id})")

    teste = requests.post(f"{api}/sendMessage", timeout=30, json={
        "chat_id": chat_id,
        "text": "✅ <b>Bot da faculdade conectado!</b>\nA partir de agora os resumos diários chegam aqui.",
        "parse_mode": "HTML",
    }).json()
    if not teste.get("ok"):
        sys.exit(f"Não consegui mandar a mensagem de teste: {teste}")

    gravar_env({"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": str(chat_id)})
    print("\nPronto! Mensagem de teste enviada e valores gravados no arquivo .env.")
    print("No passo do GitHub, você vai copiar TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID de lá.")


if __name__ == "__main__":
    main()
