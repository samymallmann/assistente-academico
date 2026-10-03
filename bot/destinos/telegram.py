import requests

from ..util import env


def enviar(texto: str) -> None:
    """Envia mensagem (HTML) para o seu chat. Mensagens longas são divididas."""
    url = f'https://api.telegram.org/bot{env("TELEGRAM_BOT_TOKEN")}/sendMessage'
    for i in range(0, len(texto), 4000):
        resp = requests.post(url, timeout=30, json={
            "chat_id": env("TELEGRAM_CHAT_ID"),
            "text": texto[i:i + 4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        })
        if not resp.ok:
            raise RuntimeError(f"Telegram {resp.status_code}: {resp.text[:300]}")
