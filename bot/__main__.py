"""Bot da faculdade: roda uma vez por dia e manda o resumo no Telegram.

Uso:
    python -m bot            execução normal
    python -m bot --teste    não grava nada (Notion, Calendar, estado) e imprime a mensagem em vez de enviar
    python -m bot --forcar   roda mesmo se já tiver rodado hoje
"""
import argparse
import re
import sys
import traceback
from datetime import datetime, timedelta
from html import escape

from .claude import ClaudeIndisponivel, interpretar
from .destinos import calendario, notion, telegram
from .fontes.classroom import buscar_atividades, buscar_avisos
from .fontes.ecampus import ler_ecampus
from .fontes.gmail import buscar_emails
from .palavras_chave import detectar_urgentes
from .util import (CONFIG, agora, carregar_estado, data_br, dia_grade, mais_dias, nome_dia,
                   nome_disciplina, para_data, salvar_estado)

ICONES = {
    "aula_cancelada": "❌", "prova": "📝", "trabalho": "📎", "lista": "📄",
    "apresentacao": "🎤", "ponto_extra": "⭐", "mudanca": "🔁", "aviso": "📢",
}


# Tipos do Notion que entram em "Provas e trabalhos da semana".
TIPOS_DA_SEMANA = {"Prova", "Trabalho", "Lista", "Lab", "Apresentação", "Ponto extra"}
TIPO_NOTION_PARA_ICONE = {"Prova": "prova", "Trabalho": "trabalho", "Lista": "lista", "Lab": "trabalho",
                          "Apresentação": "apresentacao", "Ponto extra": "ponto_extra"}


def tipo_por_titulo(titulo: str) -> str:
    t = titulo.lower()
    for padrao, tipo in [(r"prova|avalia[çc][ãa]o", "prova"), (r"lista", "lista"),
                         (r"lab", "trabalho"), (r"semin[áa]rio|apresenta", "apresentacao"),
                         (r"ponto extra", "ponto_extra")]:
        if re.search(padrao, t):
            return tipo
    return "trabalho"


class Execucao:
    def __init__(self, teste: bool):
        self.teste = teste
        self.estado = carregar_estado()
        self.inicio = agora()
        self.hoje = self.inicio.date()
        self.erros: list[str] = []
        self.novidades: list[str] = []
        self.urgentes: list[str] = []
        self.cancelamentos_hoje: dict[str, str | None] = {}  # disciplina -> motivo
        self.suspensao_geral: str | None = None  # motivo, se a UFAM toda estiver sem aula hoje

    def tentar(self, nome: str, funcao, padrao):
        try:
            return funcao()
        except Exception as e:  # uma fonte com problema não derruba as outras
            traceback.print_exc()
            self.erros.append(f"{nome}: {e}")
            return padrao

    # ---------- gravação ----------

    def gravar(self, id_externo, titulo, tipo, origem, disciplina, data, hora, observacoes, data_fim=None):
        if self.teste:
            print(f"[teste] Notion/Calendar: {titulo} ({data}{f' a {data_fim}' if data_fim else ''} {hora or ''})")
            return "criado"
        resultado = notion.salvar_avaliacao(id_externo, titulo, tipo, origem, disciplina, data, hora, observacoes, data_fim)
        if data:
            icone = ICONES.get(tipo, "📌")
            self.tentar("Google Calendar", lambda: calendario.salvar_evento(
                self.estado, id_externo, f"{icone} {titulo}", observacoes, data, hora, data_fim=data_fim), None)
        return resultado

    # ---------- etapas ----------

    def processar_atividades(self, atividades):
        # Na primeira execução tudo é "novo": importa em silêncio, sem encher o Telegram.
        primeira = not self.estado.get("atividadesVistas")
        vistas = self.estado.setdefault("atividadesVistas", {})
        prazos = self.estado.setdefault("prazosVistos", {})
        importadas = 0
        for at in atividades:
            prazo = at["prazo"]
            chave_prazo = f'{prazo["data"]} {prazo["hora"] or ""}'.strip() if prazo else None
            disc = escape(nome_disciplina(at["disciplina"]))
            titulo = escape(at["titulo"])
            futuro = prazo and para_data(prazo["data"]) >= self.hoje

            if futuro:
                quando = data_br(para_data(prazo["data"])) + (f' {prazo["hora"]}' if prazo["hora"] else "")
                pagina = notion.buscar_por_id_externo(at["id"])
                if not pagina:
                    obs = f'{at["descricao"][:500]}\n{at["link"] or ""}'.strip()
                    self.gravar(at["id"], at["titulo"], tipo_por_titulo(at["titulo"]), at["origem"],
                                at["disciplina"], prazo["data"], prazo["hora"], obs)
                    importadas += 1
                    if not primeira:
                        self.novidades.append(f'🆕 <b>{disc}</b>: atividade "{titulo}" — prazo {quando}')
                elif at["id"] in prazos and prazos[at["id"]] != chave_prazo:
                    # O professor mudou o prazo no Classroom. Itens já existentes só têm a data alterada
                    # (título e observações que você editou no Notion são preservados).
                    if not self.teste:
                        notion.atualizar_data(pagina["id"], prazo["data"], prazo["hora"])
                        if at["id"] in self.estado.get("eventosCalendario", {}):
                            self.tentar("Google Calendar", lambda: calendario.salvar_evento(
                                self.estado, at["id"], f'📎 {at["titulo"]}', at["link"] or "", prazo["data"], prazo["hora"]), None)
                    self.novidades.append(f'🔁 <b>{disc}</b>: prazo de "{titulo}" mudou para {quando}')
            elif at["nova"] and not primeira and not prazo:
                self.novidades.append(f'🆕 <b>{disc}</b>: atividade "{titulo}" (sem prazo)')

            prazos[at["id"]] = chave_prazo
            vistas[at["id"]] = at["updateTime"]

        if primeira and atividades:
            self.novidades.append(f"📥 Primeira execução: li {len(atividades)} atividades do Classroom "
                                  f"e adicionei {importadas} com prazo futuro ao Notion e ao Calendar.")

    def processar_eventos(self, eventos, itens_por_id):
        for ev in eventos:
            item = itens_por_id.get(ev.get("item"), {})
            tipo = ev.get("tipo", "aviso")
            geral = ev.get("abrangencia") == "ufam"
            disc = None if geral else ev.get("disciplina")
            inicio, fim = para_data(ev.get("data")), para_data(ev.get("data_fim"))
            motivo = ev.get("motivo")
            id_externo = f'{ev.get("item")}|{tipo}|{"ufam" if geral else disc}|{ev.get("data")}'
            origem = item.get("origem", "E-mail")
            obs = ev.get("resumo", "")
            if motivo:
                obs += f"\nMotivo: {motivo}"
            if item.get("link"):
                obs += f'\n{item["link"]}'
            if inicio:
                self.tentar("Notion", lambda: self.gravar(id_externo, ev["titulo"], tipo, origem, disc, ev.get("data"),
                                                          ev.get("hora"), obs, ev.get("data_fim")), None)

            # Cancelamento que vale para hoje: risca as aulas na mensagem.
            if tipo == "aula_cancelada" and inicio and inicio <= self.hoje <= (fim or inicio):
                if geral:
                    self.suspensao_geral = motivo or "motivo não informado"
                elif disc:
                    self.cancelamentos_hoje[disc] = motivo

            if inicio and fim and fim != inicio:
                quando = f" — de {nome_dia(inicio)[:3]} {data_br(inicio)} a {nome_dia(fim)[:3]} {data_br(fim)}"
            elif inicio:
                quando = f" — {nome_dia(inicio)} {data_br(inicio)}"
            else:
                quando = ""
            icone = "🏛️❌" if geral and tipo == "aula_cancelada" else ICONES.get(tipo, "📌")
            linha = f'{icone} <b>{escape(ev["titulo"])}</b>{quando}'
            if motivo:
                linha += f"\n    Motivo: <b>{escape(motivo)}</b>"
            if ev.get("resumo"):
                linha += f'\n    <i>{escape(ev["resumo"])}</i>'
            (self.urgentes if ev.get("urgente") or tipo == "aula_cancelada" else self.novidades).append(linha)

    def processar_notas(self, notas):
        """Atualiza o Notion e avisa no Telegram só quando nota ou falta muda no eCampus."""
        anteriores = self.estado.setdefault("notas", {})
        for n in notas:
            disc = n["disciplina"]
            atual = {"media": n["media"], "faltas": n["faltas"], "detalhes": n["detalhes"], "situacao": n["situacao"]}
            antes = anteriores.get(disc)
            if antes == atual:
                continue
            if not self.teste:
                notion.atualizar_notas(disc, atual["media"], atual["faltas"], self.hoje)
            if antes:
                partes = []
                if antes.get("media") != atual["media"]:
                    partes.append(f'nota nova! média <b>{atual["media"]:.2f}</b> ({escape(atual["detalhes"])})'
                                  if atual["media"] is not None else escape(atual["detalhes"]))
                elif antes.get("detalhes") != atual["detalhes"]:
                    partes.append(f'nota lançada: {escape(atual["detalhes"])}')
                if antes.get("faltas") != atual["faltas"]:
                    partes.append(f'faltas {antes.get("faltas"):g} → <b>{atual["faltas"]:g}</b>')
                if partes:
                    self.novidades.append(f'📊 <b>{escape(nome_disciplina(disc))}</b>: {"; ".join(partes)}')
            anteriores[disc] = atual

    # ---------- mensagem ----------

    def aulas_de_hoje(self) -> list[str]:
        linhas = []
        if self.suspensao_geral:
            linhas.append(f"  🏛️ <b>UFAM sem aulas hoje</b> — {escape(self.suspensao_geral)}")
        for g in CONFIG["grade"]:
            if g["dia"] != dia_grade(self.hoje):
                continue
            nome = escape(g.get("rotulo") or nome_disciplina(g["disciplina"]))
            sala = f' · {escape(g["sala"])}' if g["sala"] else ""
            if self.suspensao_geral:
                linhas.append(f'  <s>{g["inicio"]}–{g["fim"]} {nome}</s>')
            elif g["disciplina"] in self.cancelamentos_hoje:
                motivo = self.cancelamentos_hoje[g["disciplina"]]
                linhas.append(f'  <s>{g["inicio"]}–{g["fim"]} {nome}</s> ❌ cancelada' + (f" ({escape(motivo)})" if motivo else ""))
            else:
                linhas.append(f'  {g["inicio"]}–{g["fim"]} {nome}{sala}')
        return linhas

    def periodo_semana(self):
        """De hoje até domingo; no fim de semana, a semana seguinte inteira."""
        if self.hoje.weekday() >= 5:
            segunda = mais_dias(self.hoje, 7 - self.hoje.weekday())
            return segunda, mais_dias(segunda, 6), "da próxima semana"
        return self.hoje, mais_dias(self.hoje, 6 - self.hoje.weekday()), "da semana"

    def montar_mensagem(self, proximas) -> str:
        partes = [f"🌅 <b>Bom dia! {nome_dia(self.hoje)}, {data_br(self.hoje)}</b>"]
        if self.urgentes:
            partes.append("⚠️ <b>Atenção</b>\n" + "\n".join(self.urgentes))
        aulas = self.aulas_de_hoje()
        partes.append("📚 <b>Aulas hoje</b>\n" + ("\n".join(aulas) if aulas else "  Sem aulas hoje."))
        _, _, titulo_semana = self.periodo_semana()
        da_semana = [p for p in proximas if p["tipo"] in TIPOS_DA_SEMANA]
        if da_semana:
            linhas = []
            for p in da_semana:
                d = para_data(p["data"])
                hora = f' {p["data"][11:16]}' if len(p["data"]) > 10 else ""
                destaque = " ‼️ <b>HOJE</b>" if d == self.hoje else (" ⚠️ <b>amanhã</b>" if d == mais_dias(self.hoje, 1) else "")
                icone = ICONES.get(TIPO_NOTION_PARA_ICONE.get(p["tipo"], ""), "📌")
                linhas.append(f'  {icone} {nome_dia(d)[:3]} {data_br(d)}{hora} — {escape(p["titulo"])} '
                              f'({escape(nome_disciplina(p["disciplina"]))}){destaque}')
            partes.append(f"📅 <b>Provas e trabalhos {titulo_semana}</b>\n" + "\n".join(linhas))
        else:
            partes.append(f"📅 <b>Provas e trabalhos {titulo_semana}</b>\n  Nenhuma prova ou entrega 🎉")
        if self.novidades:
            partes.append("🆕 <b>Novidades</b>\n" + "\n".join(self.novidades))
        if self.erros:
            partes.append("🛠️ <b>Problemas nesta execução</b>\n" + "\n".join(f"  • {escape(e[:200])}" for e in self.erros))
        return "\n\n".join(partes)

    def enviar(self, texto: str):
        if self.teste:
            print("\n----- MENSAGEM DO TELEGRAM -----\n" + texto + "\n--------------------------------")
        else:
            telegram.enviar(texto)

    # ---------- fluxo principal ----------

    def rodar(self, forcar: bool):
        hoje_iso = self.hoje.isoformat()
        if not forcar and self.estado.get("ultimoCompleto") == hoje_iso and not self.estado.get("pendente"):
            print("Já rodou hoje. Nada a fazer.")
            return

        desde = (datetime.fromisoformat(self.estado["desde"]) if "desde" in self.estado
                 else self.inicio - timedelta(days=1))
        print(f"Buscando novidades desde {desde.isoformat()}")

        emails = self.tentar("Gmail", lambda: buscar_emails(desde), [])
        avisos = self.tentar("Classroom (mural)", lambda: buscar_avisos(desde), [])
        atividades = self.tentar("Classroom (atividades)",
                                 lambda: buscar_atividades(self.estado.get("atividadesVistas", {})), [])
        ecampus = self.tentar("eCampus", ler_ecampus, None)
        print(f"{len(emails)} e-mails, {len(avisos)} avisos, {len(atividades)} atividades novas/alteradas")

        self.tentar("Atividades do Classroom", lambda: self.processar_atividades(atividades), None)

        if ecampus and not ecampus["configurado"]:
            print("eCampus: login OK, mas a página de notas ainda não está no config. Links encontrados:")
            print("\n".join(ecampus["links"]))
        elif ecampus:
            self.tentar("Notas do eCampus", lambda: self.processar_notas(ecampus["notas"]), None)

        textos = emails + avisos
        if textos:
            try:
                resultado = interpretar(self.hoje, textos)
            except ClaudeIndisponivel as e:
                self.modo_reserva(textos, str(e))
                return
            self.processar_eventos(resultado.get("eventos", []), {it["id"]: it for it in textos})

        # Tudo que estiver no Notion (inclusive o que você adicionar à mão) vai para o Google Calendar.
        if not self.teste:
            self.tentar("Google Calendar (sincronização)", self.sincronizar_calendario, None)

        inicio, fim, _ = self.periodo_semana()
        proximas = self.tentar("Notion (semana)", lambda: notion.proximas_avaliacoes(inicio, fim), [])
        self.enviar(self.montar_mensagem(proximas))

        if not self.teste:
            self.estado.update({"desde": self.inicio.isoformat(), "ultimoCompleto": hoje_iso, "pendente": False})
            salvar_estado(self.estado)

    def sincronizar_calendario(self):
        """Garante um evento no Calendar para cada avaliação/demanda pendente com data no Notion."""
        ja_criados = self.estado.get("eventosCalendario", {})  # eventos que o próprio bot criou
        sincronizados = self.estado.setdefault("calendarioNotion", {})
        for item in notion.itens_com_data(self.hoje):
            if item["id_externo"] and item["id_externo"] in ja_criados:
                continue
            disc = nome_disciplina(item["disciplina"]) if item["disciplina"] else None
            icone = ICONES.get(TIPO_NOTION_PARA_ICONE.get(item["tipo"], ""), "✅" if item["origem"] == "demanda" else "📌")
            titulo = f'{icone} {item["titulo"]}' + (f" · {disc}" if disc else "")
            inicio = item["inicio"]
            data, hora = inicio[:10], (inicio[11:16] if len(inicio) > 10 else None)
            assinatura = f'{titulo}|{inicio}|{item["fim"] or ""}'
            anterior = sincronizados.get(item["pagina"])
            if anterior and anterior["assinatura"] == assinatura:
                continue
            chave = f'notion:{item["pagina"]}'
            if anterior:
                self.estado.setdefault("eventosCalendario", {})[chave] = anterior["evento"]
            calendario.salvar_evento(self.estado, chave, titulo, item["url"], data, hora,
                                     data_fim=(item["fim"] or "")[:10] or None)
            sincronizados[item["pagina"]] = {"evento": self.estado["eventosCalendario"][chave], "assinatura": assinatura}

    def modo_reserva(self, textos, motivo: str):
        """Claude indisponível: avisa só o urgente (palavras-chave) e tenta de novo na próxima hora."""
        print(f"Claude indisponível: {motivo}")
        hoje_iso = self.hoje.isoformat()
        if self.estado.get("alertaParcial") != hoje_iso:
            partes = [f"🌅 <b>Bom dia! {nome_dia(self.hoje)}, {data_br(self.hoje)}</b>",
                      "⏳ <i>O Claude está indisponível agora (limite do Pro?). Vou tentar de novo a cada hora.</i>"]
            urgentes = detectar_urgentes(textos)
            if urgentes:
                partes.append("⚠️ <b>Possíveis avisos urgentes</b>\n" + "\n".join(
                    f'  • {escape(u["trecho"])}' for u in urgentes))
            aulas = self.aulas_de_hoje()
            partes.append("📚 <b>Aulas hoje</b>\n" + ("\n".join(aulas) if aulas else "  Sem aulas hoje."))
            if self.novidades:
                partes.append("🆕 <b>Novidades</b>\n" + "\n".join(self.novidades))
            self.enviar("\n\n".join(partes))
            self.estado["alertaParcial"] = hoje_iso
        if not self.teste:
            # Não avança "desde": na próxima tentativa os mesmos e-mails/avisos serão analisados.
            self.estado["pendente"] = True
            salvar_estado(self.estado)


def main():
    parser = argparse.ArgumentParser(description="Bot da faculdade")
    parser.add_argument("--teste", action="store_true", help="não grava nada e imprime a mensagem")
    parser.add_argument("--forcar", action="store_true", help="roda mesmo se já rodou hoje")
    args = parser.parse_args()
    execucao = Execucao(teste=args.teste)
    try:
        execucao.rodar(forcar=args.forcar or args.teste)
    except Exception as e:
        traceback.print_exc()
        if not args.teste:
            try:
                telegram.enviar(f"🛠️ <b>O bot da faculdade falhou</b>\n{escape(str(e)[:500])}")
            except Exception:
                pass
        sys.exit(1)


if __name__ == "__main__":
    main()
