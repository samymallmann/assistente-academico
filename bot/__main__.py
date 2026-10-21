"""Bot da faculdade: roda uma vez por dia e manda o resumo no Telegram.

Uso:
    python -m bot            execução normal
    python -m bot --teste    não grava nada (Notion, Calendar, estado) e imprime a mensagem em vez de enviar
    python -m bot --forcar   roda mesmo se já tiver rodado hoje
    python -m bot --verificar  depois do resumo do dia: só avisa no Telegram se chegou algo novo
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
from .fontes.ufam import buscar_noticias
from .palavras_chave import detectar_urgentes
from .util import (CONFIG, agora, carregar_estado, data_br, mais_dias, nome_dia,
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
        # Avisos interpretados pelo Claude nesta execução: {"linha", "urgente", "tipo", "inicio", "fim"}
        self.avisos: list[dict] = []

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

    def mudar_data_registrada(self, ev, registrado, item):
        """O professor mudou a data de uma prova/entrega já registrada: atualiza em vez de duplicar."""
        nova, hora = ev.get("data"), ev.get("hora")
        antiga = para_data(registrado["data"])
        quem = ev.get("resumo", "")
        obs = (registrado["observacoes"] + "\n" if registrado["observacoes"] else "") + \
            f"Data alterada de {data_br(antiga)} para {data_br(para_data(nova))}: {quem}"
        if item.get("link"):
            obs += f'\n{item["link"]}'
        if not self.teste:
            notion.mudar_data(registrado["pagina"], nova, hora, obs)
            chave = registrado["id_externo"] if registrado["id_externo"] in self.estado.get("eventosCalendario", {}) else None
            if chave:  # evento criado pelo bot; os demais são atualizados pela sincronização logo depois
                self.tentar("Google Calendar", lambda: calendario.salvar_evento(
                    self.estado, chave, f'{ICONES.get(TIPO_NOTION_PARA_ICONE.get(registrado["tipo"], ""), "📌")} {registrado["titulo"]}',
                    obs, nova, hora), None)
        d = para_data(nova)
        linha = (f'🔁 <b>{escape(registrado["titulo"])}</b> mudou de {nome_dia(antiga)[:3]} {data_br(antiga)} '
                 f'para <b>{nome_dia(d)} {data_br(d)}</b>' + (f" às {hora}" if hora else "") +
                 f'\n    <i>{escape(quem)}</i>')
        self.avisos.append({"linha": linha, "urgente": bool(ev.get("urgente")), "tipo": "mudanca", "inicio": d, "fim": None})

    def processar_eventos(self, eventos, itens_por_id, registrados=None):
        registrados = registrados or []
        for ev in eventos:
            item = itens_por_id.get(ev.get("item"), {})
            ref = str(ev.get("substitui") or "")
            if ref.startswith("R") and ref[1:].isdigit() and 1 <= int(ref[1:]) <= len(registrados) and ev.get("data"):
                self.tentar("Notion (mudança de data)",
                            lambda: self.mudar_data_registrada(ev, registrados[int(ref[1:]) - 1], item), None)
                continue
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
            self.avisos.append({"linha": linha, "urgente": bool(ev.get("urgente")) or tipo == "aula_cancelada",
                                "tipo": tipo, "inicio": inicio, "fim": fim})

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

    def periodo_semana(self):
        """De hoje até domingo; no fim de semana, a semana seguinte inteira."""
        if self.hoje.weekday() >= 5:
            segunda = mais_dias(self.hoje, 7 - self.hoje.weekday())
            return segunda, mais_dias(segunda, 6), "da próxima semana"
        return self.hoje, mais_dias(self.hoje, 6 - self.hoje.weekday()), "da semana"

    def _linha_item(self, p, com_data: bool) -> str:
        """Uma prova/entrega do Notion. Prova ou apresentação de hoje ganha ⚠️."""
        d = para_data(p["data"])
        hora = f' {p["data"][11:16]}' if len(p["data"]) > 10 else ""
        quando = f"{nome_dia(d)[:3]} {data_br(d)}{hora}" if com_data else (hora.strip() or "")
        icone = ICONES.get(TIPO_NOTION_PARA_ICONE.get(p["tipo"], ""), "📌")
        if d == self.hoje and p["tipo"] in ("Prova", "Apresentação"):
            icone = "⚠️ " + icone
        disc = f' ({escape(nome_disciplina(p["disciplina"]))})' if p["disciplina"] else ""
        return f'  {icone} ' + (f"{quando} — " if quando else "") + f'{escape(p["titulo"])}{disc}'

    @staticmethod
    def _motivo(p) -> str:
        m = re.search(r"Motivo: (.+)", p.get("observacoes") or "")
        return f" — {escape(m.group(1).strip())}" if m else ""

    def _secao_demandas(self, demandas) -> str | None:
        """Suas próprias tarefas (banco Demandas): sempre no fim da mensagem."""
        if not demandas:
            return None
        linhas = []
        for d in demandas:
            prazo = para_data(d["prazo"]) if d["prazo"] else None
            if prazo is None:
                quando = ""
            elif prazo < self.hoje:
                quando = f" — ⚠️ <b>atrasada</b> (era {nome_dia(prazo)[:3]} {data_br(prazo)})"
            elif prazo == self.hoje:
                quando = " — ⚠️ <b>hoje</b>"
            elif prazo == mais_dias(self.hoje, 1):
                quando = " — ⚠️ <b>amanhã</b>"
            else:
                quando = f" — até {nome_dia(prazo)[:3]} {data_br(prazo)}"
            linhas.append(f'  ▫️ {escape(d["titulo"])}{quando}')
        return "✅ <b>Minhas demandas</b>\n" + "\n".join(linhas)

    def montar_mensagem(self, proximas, demandas=None) -> str:
        amanha = mais_dias(self.hoje, 1)
        _, fim_semana, titulo_semana = self.periodo_semana()

        def cobre(p, dia):
            inicio = para_data(p["data"])
            return inicio <= dia <= (para_data(p["fim"]) if p.get("fim") else inicio)

        canceladas = [p for p in proximas if p["tipo"] == "Aula cancelada"]
        tarefas = sorted((p for p in proximas if p["tipo"] in TIPOS_DA_SEMANA), key=lambda p: p["data"])
        partes = [f"🌅 <b>Bom dia! {nome_dia(self.hoje)}, {data_br(self.hoje)}</b>"]

        # Sem aula na UFAM toda hoje: é a primeira coisa da mensagem.
        for p in canceladas:
            if not p["disciplina"] and cobre(p, self.hoje):
                ate = f' (até {nome_dia(para_data(p["fim"]))[:3]} {data_br(para_data(p["fim"]))})' \
                    if p.get("fim") and para_data(p["fim"]) > self.hoje else ""
                partes.append(f'⚠️ <b>{escape(p["titulo"])}</b>{ate}{self._motivo(p)}')

        def secao_dia(titulo, dia):
            linhas = [f'  ⚠️ <b>{escape(p["titulo"])}</b>{self._motivo(p)}' for p in canceladas
                      if cobre(p, dia) and (p["disciplina"] or dia != self.hoje)]
            linhas += [self._linha_item(p, com_data=False) for p in tarefas if para_data(p["data"]) == dia]
            return f"{titulo}\n" + ("\n".join(linhas) if linhas else "  Nada marcado")

        partes.append(secao_dia(f"📌 <b>Hoje</b> ({nome_dia(self.hoje)[:3]} {data_br(self.hoje)})", self.hoje))
        partes.append(secao_dia(f"📆 <b>Amanhã</b> ({nome_dia(amanha)[:3]} {data_br(amanha)})", amanha))

        resto = [p for p in tarefas if amanha < para_data(p["data"]) <= fim_semana]
        partes.append(f"📅 <b>Provas e trabalhos {titulo_semana}</b>\n" + (
            "\n".join(self._linha_item(p, com_data=True) for p in resto) if resto else "  Nada além disso 🎉"))

        # Aula cancelada de hoje/amanhã já aparece nas seções acima; o resto dos avisos vai para Novidades.
        avisos = [a for a in self.avisos if not (a["tipo"] == "aula_cancelada" and a["inicio"]
                  and a["inicio"] <= amanha and (a["fim"] or a["inicio"]) >= self.hoje)]
        novidades = [("⚠️ " if a["urgente"] else "") + a["linha"] for a in avisos] + self.novidades
        if novidades:
            partes.append("🆕 <b>Novidades</b>\n" + "\n".join(novidades))
        if secao := self._secao_demandas(demandas):
            partes.append(secao)
        if self.erros:
            partes.append("🛠️ <b>Problemas nesta execução</b>\n" + "\n".join(f"  • {escape(e[:200])}" for e in self.erros))
        return "\n\n".join(partes)

    def enviar_atualizacao(self):
        """Mensagem curta da verificação extra: só sai se apareceu algo novo desde o resumo."""
        novidades = [("⚠️ " if a["urgente"] else "") + a["linha"] for a in self.avisos] + self.novidades
        if not novidades:
            print("Nada novo desde a última execução.")
            return
        partes = ["🔔 <b>Chegou coisa nova</b>", "\n".join(novidades)]
        if self.erros:
            partes.append("🛠️ <b>Problemas nesta execução</b>\n" + "\n".join(f"  • {escape(e[:200])}" for e in self.erros))
        self.enviar("\n\n".join(partes))

    def enviar(self, texto: str):
        if self.teste:
            print("\n----- MENSAGEM DO TELEGRAM -----\n" + texto + "\n--------------------------------")
        else:
            telegram.enviar(texto)

    # ---------- fluxo principal ----------

    def rodar(self, forcar: bool, verificar: bool = False):
        hoje_iso = self.hoje.isoformat()
        ja_rodou = self.estado.get("ultimoCompleto") == hoje_iso and not self.estado.get("pendente")
        # Verificação extra só faz sentido depois do resumo do dia; se ele ainda não saiu, roda o completo.
        verificar = verificar and ja_rodou and not forcar
        if not forcar and not verificar and ja_rodou:
            print("Já rodou hoje. Nada a fazer.")
            return

        desde = (datetime.fromisoformat(self.estado["desde"]) if "desde" in self.estado
                 else self.inicio - timedelta(days=1))
        print(f"Buscando novidades desde {desde.isoformat()}")

        emails = self.tentar("Gmail", lambda: buscar_emails(desde), [])
        avisos = self.tentar("Classroom (mural)", lambda: buscar_avisos(desde), [])
        atividades = self.tentar("Classroom (atividades)",
                                 lambda: buscar_atividades(self.estado.get("atividadesVistas", {})), [])
        noticias = self.tentar("Site da UFAM", lambda: buscar_noticias(desde), [])
        ecampus = self.tentar("eCampus", ler_ecampus, None)
        print(f"{len(emails)} e-mails, {len(avisos)} avisos, {len(noticias)} notícias da UFAM, "
              f"{len(atividades)} atividades novas/alteradas")

        self.tentar("Atividades do Classroom", lambda: self.processar_atividades(atividades), None)

        if ecampus and not ecampus["configurado"]:
            print("eCampus: login OK, mas a página de notas ainda não está no config. Links encontrados:")
            print("\n".join(ecampus["links"]))
        elif ecampus:
            self.tentar("Notas do eCampus", lambda: self.processar_notas(ecampus["notas"]), None)

        textos = emails + avisos + noticias
        if textos:
            registrados = self.tentar("Notion (pendentes)", lambda: notion.avaliacoes_pendentes(self.hoje), [])
            try:
                resultado = interpretar(self.hoje, textos, registrados)
            except ClaudeIndisponivel as e:
                if verificar:
                    # Não avança "desde": a próxima execução analisa de novo os mesmos e-mails/avisos.
                    print(f"Claude indisponível: {e}")
                    self.enviar_atualizacao()
                    if not self.teste:
                        salvar_estado(self.estado)
                    return
                self.modo_reserva(textos, str(e))
                return
            self.processar_eventos(resultado.get("eventos", []), {it["id"]: it for it in textos}, registrados)

        # Tudo que estiver no Notion (inclusive o que você adicionar à mão) vai para o Google Calendar.
        # Antes, o que já passou sai de "A fazer" (e some de Prioridades).
        if not self.teste:
            self.tentar("Notion (itens passados)", lambda: notion.marcar_passados(self.hoje), None)
            self.tentar("Google Calendar (sincronização)", self.sincronizar_calendario, None)

        if verificar:
            self.enviar_atualizacao()
        else:
            _, fim, _ = self.periodo_semana()
            # Começa uns dias antes para pegar suspensões de vários dias que já estão valendo.
            proximas = self.tentar("Notion (semana)", lambda: notion.proximas_avaliacoes(
                mais_dias(self.hoje, -14), max(fim, mais_dias(self.hoje, 1))), [])
            demandas = self.tentar("Notion (demandas)", notion.demandas_pendentes, [])
            self.enviar(self.montar_mensagem(proximas, demandas))

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
    parser.add_argument("--verificar", action="store_true", help="depois do resumo: só avisa se chegou algo novo")
    args = parser.parse_args()
    execucao = Execucao(teste=args.teste)
    try:
        execucao.rodar(forcar=args.forcar or args.teste, verificar=args.verificar)
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
