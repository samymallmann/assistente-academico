from datetime import datetime, timezone

from ..google_api import conta_configurada, google
from ..util import CONFIG, FUSO

BASE = "https://classroom.googleapis.com/v1/courses"


def _turmas() -> list[dict]:
    return [
        {"chave": chave, **d["classroom"]}
        for chave, d in CONFIG["disciplinas"].items()
        if d.get("classroom") and conta_configurada(d["classroom"]["conta"])
    ]


def _data_api(texto: str) -> datetime:
    return datetime.fromisoformat(texto.replace("Z", "+00:00"))


def buscar_avisos(desde: datetime) -> list[dict]:
    """Avisos do mural publicados ou editados desde `desde`."""
    itens = []
    for t in _turmas():
        r = google(t["conta"], f'{BASE}/{t["courseId"]}/announcements',
                   params={"pageSize": 20, "orderBy": "updateTime desc"})
        for a in r.get("announcements", []):
            if _data_api(a["updateTime"]) < desde:
                continue
            itens.append({
                "id": f'mural:{t["courseId"]}:{a["id"]}',
                "origem": "Classroom – mural",
                "disciplina": t["chave"],
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
        r = google(t["conta"], f'{BASE}/{t["courseId"]}/courseWork',
                   params={"pageSize": 50, "orderBy": "updateTime desc"})
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
