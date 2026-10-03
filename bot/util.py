import html
import json
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

RAIZ = Path(__file__).resolve().parent.parent
# config.json tem os seus dados (fica fora do git); config.example.json é o modelo.
_ARQUIVO_CONFIG = RAIZ / "config.json" if (RAIZ / "config.json").exists() else RAIZ / "config.example.json"
CONFIG = json.loads(_ARQUIVO_CONFIG.read_text(encoding="utf-8"))
FUSO = ZoneInfo(CONFIG["fusoHorario"])
ARQUIVO_ESTADO = RAIZ / "state.json"

NOMES_DIA = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"]

# Para testes no seu computador: carrega o arquivo .env (no GitHub os valores vêm dos secrets).
if (RAIZ / ".env").exists():
    for _linha in (RAIZ / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in _linha and not _linha.lstrip().startswith("#"):
            _chave, _valor = _linha.split("=", 1)
            os.environ.setdefault(_chave.strip(), _valor.strip())


def carregar_estado() -> dict:
    if not ARQUIVO_ESTADO.exists():
        return {}
    return json.loads(ARQUIVO_ESTADO.read_text(encoding="utf-8"))


def salvar_estado(estado: dict) -> None:
    ARQUIVO_ESTADO.write_text(json.dumps(estado, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def env(nome: str, obrigatorio: bool = True) -> str | None:
    valor = os.environ.get(nome)
    if obrigatorio and not valor:
        raise RuntimeError(f"Variável de ambiente ausente: {nome}")
    return valor


def agora() -> datetime:
    return datetime.now(FUSO)


def nome_dia(d: date) -> str:
    return NOMES_DIA[d.weekday()]


def data_br(d: date) -> str:
    return d.strftime("%d/%m")


def dia_grade(d: date) -> int:
    """Converte para a numeração usada na grade do config (1 = segunda ... 5 = sexta)."""
    return d.weekday() + 1


def nome_disciplina(chave: str | None) -> str:
    if not chave:
        return "Geral"
    return CONFIG["disciplinas"].get(chave, {}).get("nome", chave)


def html_para_texto(conteudo: str) -> str:
    conteudo = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", conteudo, flags=re.I)
    conteudo = re.sub(r"<br\s*/?>", "\n", conteudo, flags=re.I)
    conteudo = re.sub(r"</(p|div|tr|li|h\d)>", "\n", conteudo, flags=re.I)
    conteudo = re.sub(r"</t[dh]>", " | ", conteudo, flags=re.I)
    conteudo = re.sub(r"<[^>]+>", " ", conteudo)
    conteudo = html.unescape(conteudo)
    conteudo = re.sub(r"[ \t]+", " ", conteudo)
    conteudo = re.sub(r"\n\s*\n+", "\n", conteudo)
    return conteudo.strip()


def para_data(texto: str | None) -> date | None:
    if not texto:
        return None
    try:
        return date.fromisoformat(texto[:10])
    except ValueError:
        return None


def mais_dias(d: date, n: int) -> date:
    return d + timedelta(days=n)
