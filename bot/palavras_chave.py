"""Plano B quando o Claude não está disponível: detecta só o que é urgente."""
import re

PADROES = [
    r"n[ãa]o (haver[áa]|teremos|vai ter|ter[áa]) aula",
    r"aulas? (cancelada|suspensa|adiada)s?",
    r"suspens[ãa]o (das|de) aulas",
    r"\bsem aulas?\b",
    r"\bgreve\b",
    r"paralisa[çc][ãa]o",
    r"suspens[ãa]o (das|de) (atividades|aulas)",
    r"atividades? (acad[êe]micas? )?(suspensas?|n[ãa]o presenciais?)",
    r"(prova|avalia[çc][ãa]o|entrega).{0,40}(adiad|remarcad|antecipad)",
    r"mudan[çc]a de sala",
]


def detectar_urgentes(itens: list[dict]) -> list[dict]:
    urgentes = []
    for it in itens:
        conteudo = f'{it.get("assunto", "")} {it["texto"]}'
        if any(re.search(p, conteudo, re.I) for p in PADROES):
            trecho = re.sub(r"\s+", " ", it["texto"])[:200]
            if it.get("assunto"):
                trecho = f'{it["assunto"]} — {trecho}'
            urgentes.append({"item": it, "trecho": trecho})
    return urgentes
