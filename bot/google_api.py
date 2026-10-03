"""Acesso às APIs do Google com uma chave de acesso (refresh token) por conta.

Variáveis de ambiente:
    GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
    GOOGLE_REFRESH_TOKEN_PESSOAL, GOOGLE_REFRESH_TOKEN_UFAM
"""
import os
import time

import requests

from .util import env

_cache: dict[str, tuple[str, float]] = {}


def conta_configurada(conta: str) -> bool:
    return bool(os.environ.get(f"GOOGLE_REFRESH_TOKEN_{conta.upper()}"))


def _token_de_acesso(conta: str) -> str:
    token, expira = _cache.get(conta, ("", 0.0))
    if time.time() < expira:
        return token
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": env("GOOGLE_CLIENT_ID"),
            "client_secret": env("GOOGLE_CLIENT_SECRET"),
            "refresh_token": env(f"GOOGLE_REFRESH_TOKEN_{conta.upper()}"),
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    dados = resp.json()
    if not resp.ok:
        raise RuntimeError(
            f'Google recusou o acesso da conta "{conta}" ({dados.get("error")}: {dados.get("error_description", "")}). '
            "Rode scripts/autorizar_google.py de novo para essa conta."
        )
    _cache[conta] = (dados["access_token"], time.time() + dados["expires_in"] - 60)
    return dados["access_token"]


def google(conta: str, url: str, metodo: str = "GET", params: dict | None = None, corpo: dict | None = None) -> dict | None:
    resp = requests.request(
        metodo,
        url,
        params=params,
        json=corpo,
        headers={"Authorization": f"Bearer {_token_de_acesso(conta)}"},
        timeout=60,
    )
    if resp.status_code == 204:
        return None
    if not resp.ok:
        raise RuntimeError(f"Google API {resp.status_code} em {url}: {resp.text[:300]}")
    return resp.json()
