from datetime import datetime, timezone

from ..google_api import conta_configurada, google
from ..util import CONFIG, FUSO

BASE = "https://classroom.googleapis.com/v1/courses"


_cache_turmas: list[dict] | None = None


def _turmas() -> list[dict]:
    """Todas as turmas ativas das contas configuradas.

    Turmas do config.json vêm com a chave da disciplina; turmas novas (ex.: semestre seguinte)
    entram com chave None e o nome da turma, e o Claude deduz a disciplina pelo texto.
    """
    global _cache_turmas
    if _cache_turmas is not None:
        return _cache_turmas
    conhecidas = {d["classroom"]["courseId"]: chave
                  for chave, d in CONFIG["disciplinas"].items() if d.get("classroom")}
    contas = {d["classroom"]["conta"] for d in CONFIG["disciplinas"].values() if d.get("classroom")}
    contas |= {c for c in CONFIG["email"]}  # contas Google que o bot já usa
    turmas = []
    for conta in sorted(contas):
        if not conta_configurada(conta):
            continue
        # Só turmas em que a conta é aluna (turmas criadas por ela, como testes, ficam de fora).
        r = google(conta, BASE, params={"courseStates": "ACTIVE", "studentId": "me", "pageSize": 50})
        for c in r.get("courses", []):
            turmas.append({"chave": conhecidas.get(c["id"]), "conta": conta,
                           "courseId": c["id"], "nome": c.get("name", "")})
    _cache_turmas = turmas
    return turmas


def _ler_turma(t: dict, recurso: str, params: dict) -> dict:
    """Lê um recurso de uma turma; se só essa turma falhar, segue com as outras."""
    try:
        return google(t["conta"], f'{BASE}/{t["courseId"]}/{recurso}', params=params)
    except RuntimeError as e:
        print(f'Classroom: pulei a turma "{t["nome"]}" ({recurso}): {str(e)[:150]}')
        return {}


def _data_api(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


def buscar_avisos(desde: datetime) -> list[dict]:
    """Avisos do mural publicados ou editados desde `desde`."""
    itens = []
    for t in _turmas():
        r = _ler_turma(t, "announcements", {"pageSize": 20, "orderBy": "updateTime desc"})
        for a in r.get("announcements", []):
            if _data_api(a["updateTime"]) < desde:
                continue
            itens.append({
                "id": f'mural:{t["courseId"]}:{a["id"]}',
                "origem": "Classroom – mural",
                "disciplina": t["chave"],
                "turma": t["nome"],
                "quando": a["updateTime"],
                "texto": a.get("text", ""),
                "link": a.get("alternateLink"),
            })
    return itens


def _prazo_local(cw: dict) -> dict | None:
    """Converte dueDate/dueTime (UTC no Classroom) para data/hora de Manaus."""
    if "dueDate" not in cw:
        return None
    d, t = cw["dueDate"], cw.get("dueTime")
    utc = datetime(d["year"], d["month"], d["day"],
                   (t or {}).get("hours", 23), (t or {}).get("minutes", 59), tzinfo=timezone.utc)
    local = utc.astimezone(FUSO)
    return {"data": local.date().isoformat(), "hora": local.strftime("%H:%M") if t else None}


def buscar_atividades(vistas: dict) -> list[dict]:
    """Atividades novas ou alteradas desde a última execução (compara updateTime)."""
    itens = []
    for t in _turmas():
        r = _ler_turma(t, "courseWork", {"pageSize": 50, "orderBy": "updateTime desc"})
        for cw in r.get("courseWork", []):
            id_ = f'atividade:{t["courseId"]}:{cw["id"]}'
            if vistas.get(id_) == cw["updateTime"]:
                continue
            itens.append({
                "id": id_,
                "origem": "Classroom – atividade",
                "disciplina": t["chave"],
                "titulo": cw["title"],
                "descricao": cw.get("description", "")[:3000],
                "prazo": _prazo_local(cw),
                "pontos": cw.get("maxPoints"),
                "link": cw.get("alternateLink"),
                "updateTime": cw["updateTime"],
                "nova": id_ not in vistas,
            })
    return itens
