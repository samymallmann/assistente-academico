import base64
from datetime import datetime, timezone

from ..google_api import conta_configurada, google
from ..util import CONFIG, html_para_texto

BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
LIMITE_TEXTO = 4000


def _decodificar(dados: str) -> str:
    return base64.urlsafe_b64decode(dados + "=" * (-len(dados) % 4)).decode("utf-8", errors="replace")


def _extrair_corpo(parte: dict) -> str:
    if parte.get("mimeType") == "text/plain" and parte.get("body", {}).get("data"):
        return _decodificar(parte["body"]["data"])
    for sub in parte.get("parts", []):
        texto = _extrair_corpo(sub)
        if texto:
            return texto
    if parte.get("mimeType") == "text/html" and parte.get("body", {}).get("data"):
        return html_para_texto(_decodificar(parte["body"]["data"]))
    return ""


def _cabecalho(msg: dict, nome: str) -> str:
    for h in msg["payload"].get("headers", []):
        if h["name"].lower() == nome.lower():
            return h["value"]
    return ""


def buscar_emails(desde: datetime) -> list[dict]:
    """E-mails recebidos desde `desde` em cada conta configurada."""
    itens = []
    for conta, opcoes in CONFIG["email"].items():
        if not opcoes["ativo"] or not conta_configurada(conta):
            continue
        busca = f'{opcoes["busca"]} after:{int(desde.timestamp())}'
        lista = google(conta, f"{BASE}/messages", params={"q": busca, "maxResults": 30})
        for m in lista.get("messages", []):
            msg = google(conta, f'{BASE}/messages/{m["id"]}', params={"format": "full"})
            recebido = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc)
            itens.append({
                "id": f'email:{conta}:{m["id"]}',
                "origem": "E-mail",
                "conta": conta,
                "de": _cabecalho(msg, "From"),
                "assunto": _cabecalho(msg, "Subject"),
                "quando": recebido.isoformat(),
                "texto": (_extrair_corpo(msg["payload"]) or msg.get("snippet", ""))[:LIMITE_TEXTO],
            })
    return itens
