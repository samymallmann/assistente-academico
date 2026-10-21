"""Notícias do site da UFAM (feed RSS).

Suspensões gerais (qualidade do ar, greve, energia) às vezes não chegam por e-mail. Para não gastar o
Claude com toda notícia institucional, só passam as que batem com palavras-chave de suspensão de aula.
"""
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

import requests

from ..util import CONFIG, html_para_texto

LIMITE_TEXTO = 3000
# A notícia precisa falar de mudança (suspensão, remoto, greve...) E de aula/atividade/expediente.
MUDANCA = [
    r"suspen(s[ãa]o|de|dem|sas?|didas?)", r"\bremot[oa]s?\b", r"n[ãa]o presencia", r"\bsem aulas?\b",
    r"cancelad[ao]s?", r"\bgreve\b", r"paralisa[çc][ãa]o", r"ponto facultativo", r"qualidade do ar",
]
ALVO = [r"\baulas?\b", r"atividades? (acad[êe]micas?|presenciais?|letivas?)", r"\bexpediente\b", r"calend[áa]rio acad[êe]mico"]


def _interessa(texto: str) -> bool:
    return any(re.search(p, texto, re.I) for p in MUDANCA) and any(re.search(p, texto, re.I) for p in ALVO)


def _data(item: ET.Element) -> datetime | None:
    for tag in ("pubDate", "{http://purl.org/dc/elements/1.1/}date", "{http://www.w3.org/2005/Atom}updated"):
        valor = (item.findtext(tag) or "").strip()
        if not valor:
            continue
        try:
            return parsedate_to_datetime(valor)
        except (TypeError, ValueError):
            pass
        try:
            return datetime.fromisoformat(valor.replace("Z", "+00:00"))
        except ValueError:
            pass
    return None


def _ler_feed(url: str) -> list[dict]:
    r = requests.get(url, timeout=60, headers={"User-Agent": "Mozilla/5.0 (assistente-academico)"})
    r.raise_for_status()
    raiz = ET.fromstring(r.content)
    atom = "{http://www.w3.org/2005/Atom}"
    itens = []
    for it in raiz.iter("item"):
        itens.append({"titulo": (it.findtext("title") or "").strip(), "link": (it.findtext("link") or "").strip(),
                      "texto": html_para_texto(it.findtext("description") or ""), "data": _data(it)})
    for it in raiz.iter(f"{atom}entry"):
        link = it.find(f"{atom}link")
        itens.append({"titulo": (it.findtext(f"{atom}title") or "").strip(),
                      "link": link.get("href", "") if link is not None else "",
                      "texto": html_para_texto(it.findtext(f"{atom}summary") or it.findtext(f"{atom}content") or ""),
                      "data": _data(it)})
    return itens


def buscar_noticias(desde: datetime) -> list[dict]:
    """Notícias e portarias publicadas desde `desde` que falam de suspensão/alteração das aulas."""
    cfg = CONFIG.get("ufam", {})
    if not cfg.get("ativo"):
        return []
    novos, vistos = [], set()
    for fonte in cfg["feeds"]:
        for n in _ler_feed(fonte["url"]):
            if not n["data"] or n["data"] < desde or (n["link"] or n["titulo"]) in vistos:
                continue
            vistos.add(n["link"] or n["titulo"])
            if not _interessa(f'{n["titulo"]} {n["texto"]}'):
                continue
            novos.append({
                "id": f'ufam:{n["link"] or n["titulo"]}',
                "origem": fonte["nome"],
                "assunto": n["titulo"],
                "quando": n["data"].astimezone(timezone.utc).isoformat(),
                "texto": f'{n["texto"][:LIMITE_TEXTO]}\n{n["link"]}'.strip(),
            })
    return novos


def descobrir() -> None:
    """Ajuda a configurar: mostra os itens mais recentes de cada feed e quais passariam no filtro."""
    for fonte in CONFIG.get("ufam", {}).get("feeds", []):
        print(f'\n=== {fonte["nome"]}: {fonte["url"]}')
        try:
            itens = _ler_feed(fonte["url"])
        except Exception as e:  # mostra o erro e segue para o próximo feed
            print(f"  ERRO: {e}")
            continue
        print(f"  {len(itens)} itens")
        for n in itens[:15]:
            marca = "✔" if _interessa(f'{n["titulo"]} {n["texto"]}') else " "
            print(f'  {marca} {n["data"]} | {n["titulo"][:100]} | {n["link"]}')


if __name__ == "__main__":
    descobrir()
    sys.exit(0)
