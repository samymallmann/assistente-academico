"""Escrita no Notion (integração interna, token em NOTION_TOKEN)."""
from datetime import date

import requests

from ..util import CONFIG, env

API = "https://api.notion.com/v1"
VERSAO = "2022-06-28"

TIPOS = {
    "prova": "Prova",
    "trabalho": "Trabalho",
    "lista": "Lista",
    "apresentacao": "Apresentação",
    "ponto_extra": "Ponto extra",
    "aula_cancelada": "Aula cancelada",
    "mudanca": "Aviso",
    "aviso": "Aviso",
}


def _notion(metodo: str, caminho: str, corpo: dict | None = None) -> dict:
    resp = requests.request(
        metodo, f"{API}{caminho}", json=corpo, timeout=60,
        headers={"Authorization": f'Bearer {env("NOTION_TOKEN")}', "Notion-Version": VERSAO},
    )
    if resp.status_code == 404:
        raise RuntimeError("Notion sem acesso: conecte a integração bot-faculdade à página "
                           "'Faculdade – UFAM 2026/2' (••• → Conexões).")
    if resp.status_code == 401:
        raise RuntimeError("Token do Notion inválido: gere um novo e atualize o secret NOTION_TOKEN.")
    if not resp.ok:
        raise RuntimeError(f"Notion {resp.status_code} em {caminho}: {resp.text[:300]}")
    return resp.json()


def _texto(valor: str) -> list:
    return [{"type": "text", "text": {"content": valor[:2000]}}]


def buscar_por_id_externo(id_externo: str) -> dict | None:
    r = _notion("POST", f'/databases/{CONFIG["notion"]["avaliacoesDbId"]}/query', {
        "filter": {"property": "ID externo", "rich_text": {"equals": id_externo}},
        "page_size": 1,
    })
    return r["results"][0] if r["results"] else None


def atualizar_data(pagina_id: str, data: str, hora: str | None) -> None:
    """Muda só a data de um item (usado quando o professor altera o prazo no Classroom)."""
    inicio = f"{data}T{hora}:00-04:00" if hora else data
    _notion("PATCH", f"/pages/{pagina_id}", {"properties": {"Data": {"date": {"start": inicio}}}})


def salvar_avaliacao(id_externo: str, titulo: str, tipo: str, origem: str, disciplina: str | None,
                     data: str | None, hora: str | None, observacoes: str, data_fim: str | None = None) -> str:
    """Cria ou atualiza um item em "Avaliações e Notas". Retorna 'criado', 'atualizado' ou 'igual'."""
    propriedades = {
        "Avaliação": {"title": _texto(titulo)},
        "Tipo": {"select": {"name": TIPOS.get(tipo, "Aviso")}},
        "Origem": {"select": {"name": origem}},
        "Observações": {"rich_text": _texto(observacoes)},
        "ID externo": {"rich_text": _texto(id_externo)},
    }
    if disciplina and disciplina in CONFIG["disciplinas"]:
        propriedades["Disciplina"] = {"relation": [{"id": CONFIG["disciplinas"][disciplina]["notionPageId"]}]}
    if data:
        inicio = f"{data}T{hora}:00-04:00" if hora else data
        propriedades["Data"] = {"date": {"start": inicio}}
        if data_fim and data_fim != data and not hora:
            propriedades["Data"]["date"]["end"] = data_fim

    existente = buscar_por_id_externo(id_externo)
    if not existente:
        propriedades["Status"] = {"select": {"name": "A fazer"}}
        _notion("POST", "/pages", {"parent": {"database_id": CONFIG["notion"]["avaliacoesDbId"]}, "properties": propriedades})
        return "criado"

    data_antiga = (existente["properties"]["Data"]["date"] or {}).get("start")
    _notion("PATCH", f'/pages/{existente["id"]}', {"properties": propriedades})
    nova = propriedades.get("Data", {}).get("date", {}).get("start")
    return "atualizado" if data_antiga != nova else "igual"


def atualizar_notas(disciplina: str, media: float | None, faltas: float | None, hoje: date) -> None:
    propriedades = {"Última sincronização": {"date": {"start": hoje.isoformat()}}}
    if media is not None:
        propriedades["Média parcial"] = {"number": media}
    if faltas is not None:
        propriedades["Faltas"] = {"number": faltas}
    _notion("PATCH", f'/pages/{CONFIG["disciplinas"][disciplina]["notionPageId"]}', {"properties": propriedades})


def _titulo(props: dict) -> str:
    for p in props.values():
        if p["type"] == "title":
            return "".join(t["plain_text"] for t in p["title"])
    return ""


def _consultar_tudo(db_id: str, filtro: dict) -> list[dict]:
    resultados, cursor = [], None
    while True:
        corpo = {"filter": filtro, "page_size": 100}
        if cursor:
            corpo["start_cursor"] = cursor
        r = _notion("POST", f"/databases/{db_id}/query", corpo)
        resultados += r["results"]
        if not r.get("has_more"):
            return resultados
        cursor = r["next_cursor"]


def itens_com_data(hoje: date) -> list[dict]:
    """Avaliações 'A fazer' e demandas não feitas, com data de hoje em diante (para o Calendar)."""
    paginas_para_chave = {d["notionPageId"].replace("-", ""): k for k, d in CONFIG["disciplinas"].items()}

    def disciplina(props):
        rel = props.get("Disciplina", {}).get("relation", [])
        return paginas_para_chave.get(rel[0]["id"].replace("-", "")) if rel else None

    itens = []
    avaliacoes = _consultar_tudo(CONFIG["notion"]["avaliacoesDbId"], {"and": [
        {"property": "Status", "select": {"equals": "A fazer"}},
        {"property": "Data", "date": {"on_or_after": hoje.isoformat()}},
    ]})
    for p in avaliacoes:
        props = p["properties"]
        itens.append({
            "pagina": p["id"], "url": p["url"], "origem": "avaliacao", "titulo": _titulo(props),
            "tipo": (props["Tipo"]["select"] or {}).get("name"),
            "inicio": props["Data"]["date"]["start"], "fim": props["Data"]["date"].get("end"),
            "disciplina": disciplina(props),
            "id_externo": "".join(t["plain_text"] for t in props["ID externo"]["rich_text"]),
        })
    demandas = _consultar_tudo(CONFIG["notion"]["demandasDbId"], {"and": [
        {"property": "Feito", "checkbox": {"equals": False}},
        {"property": "Prazo", "date": {"on_or_after": hoje.isoformat()}},
    ]})
    for p in demandas:
        props = p["properties"]
        itens.append({
            "pagina": p["id"], "url": p["url"], "origem": "demanda", "titulo": _titulo(props), "tipo": None,
            "inicio": props["Prazo"]["date"]["start"], "fim": props["Prazo"]["date"].get("end"),
            "disciplina": disciplina(props), "id_externo": "",
        })
    return itens


def marcar_passados(hoje: date) -> int:
    """Muda para 'Passou' o que ainda está 'A fazer' com data anterior a hoje (sai de Prioridades).

    Períodos que ainda não terminaram (ex.: greve de vários dias) continuam 'A fazer'.
    """
    paginas = _consultar_tudo(CONFIG["notion"]["avaliacoesDbId"], {"and": [
        {"property": "Status", "select": {"equals": "A fazer"}},
        {"property": "Data", "date": {"before": hoje.isoformat()}},
    ]})
    marcados = 0
    for p in paginas:
        data = p["properties"]["Data"]["date"]
        if date.fromisoformat((data.get("end") or data["start"])[:10]) >= hoje:
            continue
        _notion("PATCH", f'/pages/{p["id"]}', {"properties": {"Status": {"select": {"name": "Passou"}}}})
        marcados += 1
    return marcados


def proximas_avaliacoes(hoje: date, ate: date) -> list[dict]:
    """Itens com status 'A fazer' entre hoje e `ate` (para o resumo do dia)."""
    r = _notion("POST", f'/databases/{CONFIG["notion"]["avaliacoesDbId"]}/query', {
        "filter": {"and": [
            {"property": "Status", "select": {"equals": "A fazer"}},
            {"property": "Data", "date": {"on_or_after": hoje.isoformat()}},
            {"property": "Data", "date": {"on_or_before": ate.isoformat()}},
        ]},
        "sorts": [{"property": "Data", "direction": "ascending"}],
    })
    paginas_para_chave = {d["notionPageId"].replace("-", ""): k for k, d in CONFIG["disciplinas"].items()}
    itens = []
    for p in r["results"]:
        props = p["properties"]
        relacao = props["Disciplina"]["relation"]
        itens.append({
            "titulo": "".join(t["plain_text"] for t in props["Avaliação"]["title"]),
            "tipo": (props["Tipo"]["select"] or {}).get("name"),
            "data": props["Data"]["date"]["start"],
            "disciplina": paginas_para_chave.get(relacao[0]["id"].replace("-", "")) if relacao else None,
        })
    return itens
