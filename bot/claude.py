"""Interpretação de e-mails, avisos e notas usando o Claude Code com a assinatura Pro.

O token vem de `claude setup-token` e fica na variável CLAUDE_CODE_OAUTH_TOKEN.
"""
import json
import shutil
import subprocess
from datetime import date

from .util import CONFIG, data_br, nome_dia


class ClaudeIndisponivel(Exception):
    """Limite do Pro atingido, token inválido ou Claude Code fora do ar."""


def _rodar_claude(prompt: str) -> str:
    executavel = shutil.which("claude")
    if not executavel:
        raise ClaudeIndisponivel("Claude Code não está instalado (npm install -g @anthropic-ai/claude-code).")
    try:
        proc = subprocess.run(
            [executavel, "-p", "--output-format", "json", "--model", CONFIG["modeloClaude"]],
            input=prompt, capture_output=True, text=True, encoding="utf-8", timeout=600,
        )
    except subprocess.TimeoutExpired as e:
        raise ClaudeIndisponivel("Claude Code demorou mais de 10 minutos.") from e
    try:
        resultado = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ClaudeIndisponivel(f"Claude Code saiu com código {proc.returncode}: {(proc.stderr or proc.stdout)[:500]}") from e
    if resultado.get("is_error") or proc.returncode != 0:
        raise ClaudeIndisponivel(f'Claude indisponível: {str(resultado.get("result", proc.stderr))[:500]}')
    return resultado["result"]


def _extrair_json(texto: str) -> dict:
    inicio, fim = texto.find("{"), texto.rfind("}")
    if inicio < 0 or fim < inicio:
        raise ValueError(f"Resposta do Claude sem JSON: {texto[:300]}")
    return json.loads(texto[inicio:fim + 1])


def _descrever_grade() -> str:
    dias = ["", "Seg", "Ter", "Qua", "Qui", "Sex"]
    linhas = []
    for g in CONFIG["grade"]:
        nome = g.get("rotulo") or CONFIG["disciplinas"][g["disciplina"]]["nome"]
        linhas.append(f'- {dias[g["dia"]]} {g["inicio"]}–{g["fim"]}: {nome} [{g["disciplina"]}] {g["sala"]}')
    return "\n".join(linhas)


def _descrever_disciplinas() -> str:
    return "\n".join(
        f'- {chave}: {d["nome"]} (também chamada de: {", ".join(d["apelidos"])})'
        for chave, d in CONFIG["disciplinas"].items()
        if not d.get("somenteEcampus")  # laboratórios contam como a disciplina principal nos avisos
    )


def _descrever_item(it: dict) -> str:
    linhas = [f'### ITEM {it["id"]}', f'Origem: {it["origem"]}']
    if it.get("disciplina"):
        linhas[-1] += f' (disciplina: {it["disciplina"]})'
    elif it.get("turma"):
        linhas[-1] += f' (turma do Classroom: "{it["turma"]}" — deduza a disciplina pelo nome/texto, ou null)'
    if it.get("de"):
        linhas.append(f'De: {it["de"]}')
    if it.get("assunto"):
        linhas.append(f'Assunto: {it["assunto"]}')
    linhas += [f'Recebido em: {it["quando"]}', "Texto:", it["texto"]]
    return "\n".join(linhas)


def interpretar(hoje: date, itens: list[dict]) -> dict:
    prompt = f"""Você organiza a agenda acadêmica de quem estuda engenharia na UFAM.
Hoje é {nome_dia(hoje)}, {data_br(hoje)} ({hoje.isoformat()}). Fuso: Manaus.

Disciplinas (use SEMPRE a chave à esquerda):
{_descrever_disciplinas()}

Grade semanal fixa:
{_descrever_grade()}

Abaixo estão e-mails e avisos do mural do Classroom recebidos desde a última verificação.
Identifique tudo que afeta a agenda:
- aula cancelada / suspensa / "não haverá aula" — SEMPRE extraia o motivo (greve, paralisação, qualidade do ar,
  falta de energia, reunião, feriado, problema de saúde do professor etc.) quando o texto disser.
  Se for suspensão GERAL da UFAM (greve, nota oficial da reitoria, falta de energia no campus...), gere UM evento com
  "abrangencia": "ufam", "disciplina": null e o período em "data"/"data_fim". Se for só de uma disciplina, "abrangencia": "disciplina".
- prova, entrega, trabalho, lista, apresentação, ponto extra (com data)
- adiamento ou mudança de data, horário ou sala
- outro aviso importante que exija ação

Ignore propaganda, newsletters, eventos opcionais e qualquer coisa sem impacto na agenda.
Resolva datas relativas ("amanhã", "próxima segunda") a partir da data em que o item foi RECEBIDO.
Se o texto não disser a disciplina mas der para deduzir pelo horário, professor ou assunto, deduza; se não der, use null.

Responda APENAS com um JSON neste formato, sem texto antes ou depois:
{{
  "eventos": [
    {{
      "item": "<id do ITEM de origem>",
      "tipo": "aula_cancelada | prova | trabalho | lista | apresentacao | ponto_extra | mudanca | aviso",
      "disciplina": "<chave da disciplina ou null>",
      "abrangencia": "disciplina | ufam",
      "data": "AAAA-MM-DD ou null",
      "data_fim": "AAAA-MM-DD se for um período (ex.: greve de vários dias), senão null",
      "hora": "HH:MM ou null",
      "motivo": "<motivo do cancelamento/mudança, curto, ou null se o texto não disser>",
      "titulo": "<curto, ex.: 'Sem aula de Controle' ou '1ª prova de Sociologia'>",
      "resumo": "<uma frase explicando, citando quem avisou>",
      "urgente": <true se afeta hoje ou amanhã>
    }}
  ]
}}

{chr(10).join(_descrever_item(it) for it in itens)}
"""
    return _extrair_json(_rodar_claude(prompt))
