from datetime import date, datetime, timedelta

from ..google_api import conta_configurada, google
from ..util import CONFIG

BASE = "https://www.googleapis.com/calendar/v3/calendars"


def salvar_evento(estado: dict, id_externo: str, titulo: str, descricao: str,
                  data: str, hora: str | None, duracao_min: int = 60, data_fim: str | None = None) -> None:
    """Cria o evento ou atualiza o já criado para o mesmo item (mapeamento guardado no estado)."""
    conta = CONFIG["calendario"]["conta"]
    if not conta_configurada(conta):
        return
    if hora:
        inicio = datetime.fromisoformat(f"{data}T{hora}:00-04:00")
        corpo = {
            "start": {"dateTime": inicio.isoformat(), "timeZone": CONFIG["fusoHorario"]},
            "end": {"dateTime": (inicio + timedelta(minutes=duracao_min)).isoformat(), "timeZone": CONFIG["fusoHorario"]},
        }
    else:
        dia = date.fromisoformat(data)
        ultimo = date.fromisoformat(data_fim) if data_fim else dia
        corpo = {"start": {"date": dia.isoformat()}, "end": {"date": (ultimo + timedelta(days=1)).isoformat()}}
    if CONFIG["calendario"].get("cor"):
        corpo["colorId"] = CONFIG["calendario"]["cor"]  # todos os eventos da faculdade na mesma cor
    corpo |= {
        "summary": titulo,
        "description": descricao,
        "reminders": {"useDefault": False, "overrides": [{"method": "popup", "minutes": 24 * 60}]},
    }

    url = f'{BASE}/{CONFIG["calendario"]["calendarId"]}/events'
    eventos = estado.setdefault("eventosCalendario", {})
    if id_externo in eventos:
        google(conta, f"{url}/{eventos[id_externo]}", metodo="PATCH", corpo=corpo)
    else:
        criado = google(conta, url, metodo="POST", corpo=corpo)
        eventos[id_externo] = criado["id"]
