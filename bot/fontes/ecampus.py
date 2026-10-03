import hashlib
import os
import re
from urllib.parse import urljoin

import requests

from ..util import CONFIG, agora, env, html_para_texto

BASE = "https://ecampus.ufam.edu.br"


def _entrar() -> tuple[requests.Session, requests.Response]:
    s = requests.Session()
    s.headers["User-Agent"] = "Mozilla/5.0 (bot-faculdade)"
    s.get(f"{BASE}/ecampus/home/login", timeout=60)
    login = s.post(CONFIG["ecampus"]["urlLogin"],
                   data={"usuario": env("ECAMPUS_USUARIO"), "senha": env("ECAMPUS_SENHA")}, timeout=60)
    if 'id="formLogin"' in login.text:
        raise RuntimeError("Login no eCampus falhou (CPF/senha incorretos ou o site mudou o formulário).")
    if CONFIG["ecampus"].get("moduloNotas"):
        s.get(urljoin(BASE, CONFIG["ecampus"]["moduloNotas"]), timeout=60)
    return s, login


def _links(html: str, base: str) -> list[str]:
    return sorted({
        f"{re.sub(r'\s+', ' ', texto).strip()} -> {urljoin(base, href)}"
        for href, texto in re.findall(r'href="([^"#]+)"[^>]*>([^<]{3,80})<', html)
        if not href.endswith((".css", ".js", ".ico"))
    })


def _periodo_atual() -> tuple[str, str]:
    """Ano e código do período no eCampus (201 = 1º semestre, 202 = 2º semestre)."""
    cfg = CONFIG["ecampus"].get("periodoNotas", {})
    hoje = agora().date()
    ano = str(hoje.year) if cfg.get("ano", "auto") == "auto" else str(cfg["ano"])
    periodo = ("202" if hoje.month >= 7 else "201") if cfg.get("periodo", "auto") == "auto" else str(cfg["periodo"])
    return ano, periodo


def _pagina_de_notas(s: requests.Session, caminho: str) -> requests.Response:
    """Abre a página de notas e busca a tabela do período atual.

    A página carrega a tabela por JavaScript (js/app/notasParciais.js), com um POST para `getNotas`.
    """
    url = urljoin(BASE, caminho)
    s.get(url, timeout=60)
    ano, periodo = _periodo_atual()
    return s.post(urljoin(url, "getNotas"), data={"ano": ano, "periodo": periodo}, timeout=60,
                  headers={"X-Requested-With": "XMLHttpRequest", "Referer": url})


def _tabela_para_texto(html: str) -> str:
    """Converte cada linha de tabela em 'célula | célula | ...' (bem mais fácil de ler que o HTML)."""
    linhas = []
    for tr in re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", html, flags=re.I):
        celulas = [re.sub(r"\s+", " ", html_para_texto(c)).strip(" |")
                   for c in re.findall(r"<t[dh][^>]*>([\s\S]*?)</t[dh]>", tr, flags=re.I)]
        if any(celulas):
            linhas.append(" | ".join(celulas))
    return "\n".join(linhas) if linhas else html_para_texto(html)


def _limpar(texto: str) -> str:
    """Remove o menu lateral e o aviso de cookies, deixando só o conteúdo da página."""
    inicio = texto.rfind("Notas e Frequência")
    if inicio >= 0:
        texto = texto[inicio:]
    fim = texto.find("Este site usa cookies")
    if fim >= 0:
        texto = texto[:fim]
    return texto.strip()


def descobrir() -> None:
    """Ajuda a configurar: lista os links de cada módulo ou mostra o conteúdo da página de notas."""
    s, login = _entrar()
    cfg = CONFIG["ecampus"]
    if cfg["paginasNotas"]:
        for p in cfg["paginasNotas"]:
            r = _pagina_de_notas(s, p)
            print(f"\n=== {p} -> {r.url} (HTTP {r.status_code}) período {_periodo_atual()}")
            print(_tabela_para_texto(r.text)[:6000])
            print("\n--- links/paginação na resposta ---")
            print(re.findall(r'<a[^>]*href="([^"]+)"[^>]*>([^<]*)<', r.text)[:20])
        return
    modulos = sorted(set(re.findall(r'href="([^"]*setModulo/\d+)"', login.text)))
    for m in modulos:
        r = s.get(urljoin(login.url, m), timeout=60)
        print(f"\n=== {m} -> {r.url}")
        for link in _links(r.text, r.url):
            print("  ", link)


def _numero(valor: str) -> float | None:
    try:
        return float(valor.replace(",", "."))
    except ValueError:
        return None


def _extrair_notas(tabela: str) -> list[dict]:
    """Lê a tabela 'CÓD. | P1 | E1 | ... | ME | PF | MF | FT | ST | EF' e devolve uma entrada por disciplina."""
    por_codigo = {d["codigoEcampus"]: chave for chave, d in CONFIG["disciplinas"].items() if d.get("codigoEcampus")}
    colunas: list[str] = []
    notas = []
    for linha in tabela.splitlines():
        celulas = [c.strip() for c in linha.split("|")]
        if celulas and celulas[0] == "CÓD.":
            colunas = celulas
            continue
        if not colunas or not celulas or celulas[0] not in por_codigo:
            continue
        valores = dict(zip(colunas, celulas + [""] * (len(colunas) - len(celulas))))
        exercicios = [(c, _numero(v)) for c, v in valores.items() if re.fullmatch(r"E\d+", c) and v]
        lancadas = any(n for _, n in exercicios if n)  # todas 0.00 = ainda não lançadas
        media_final = _numero(valores.get("MF", ""))
        media = media_final if lancadas and media_final else _numero(valores.get("ME", ""))
        notas.append({
            "disciplina": por_codigo[celulas[0]],
            "media": media if lancadas else None,
            "faltas": _numero(valores.get("FT", "")) or 0,
            "detalhes": " · ".join(f"{c} {n:.2f}" for c, n in exercicios if n is not None) if lancadas else "notas ainda não lançadas",
            "situacao": valores.get("ST", ""),
        })
    return notas


def ler_ecampus() -> dict | None:
    """Faz login no eCampus e devolve notas e faltas de cada disciplina."""
    cfg = CONFIG["ecampus"]
    if not cfg["ativo"] or not os.environ.get("ECAMPUS_USUARIO"):
        return None

    s, login = _entrar()
    if not cfg["paginasNotas"]:
        return {"configurado": False, "links": _links(login.text, login.url)[:100]}

    tabela = "\n".join(_tabela_para_texto(_pagina_de_notas(s, p).text) for p in cfg["paginasNotas"])
    notas = _extrair_notas(tabela)
    if not notas:
        raise RuntimeError("Página de notas do eCampus mudou de formato: não encontrei nenhuma disciplina.")
    return {"configurado": True, "notas": notas}


if __name__ == "__main__":
    descobrir()
