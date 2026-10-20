"""API interna.

Roda no mesmo processo do bot. Serve para:

- `/health` — checagem simples de disponibilidade;
- `/webhook/licenca` — o seu gateway de pagamento avisa que o cliente pagou
  (ou deixou de pagar) e o plano daquele servidor e liberado/renovado na hora.
- `/webhook/mercadopago` — o Mercado Pago avisa que um PIX foi pago e a
  assinatura do servidor e liberada automaticamente.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from fastapi import FastAPI, Header, HTTPException, Request
from pydantic import BaseModel, Field

import config
from db import definir_bloqueio, definir_plano, pagamento_por_mp_id
from models import PLANOS_VENDAVEIS

log = logging.getLogger(__name__)

app = FastAPI(title="Bot de Vendas - API interna", version="0.3.0")

# Preenchido pelo main.py quando o bot sobe (usado para avisar o cliente).
bot_ref: Optional[discord.Client] = None


def registrar_bot(bot: discord.Client) -> None:
    global bot_ref
    bot_ref = bot


class EntradaDeLicenca(BaseModel):
    """O que o gateway manda quando uma assinatura muda de estado."""

    guild_id: int = Field(..., description="ID do servidor do cliente")
    plano: Optional[str] = Field(None, description="free, media ou pro")
    dias: Optional[int] = Field(None, ge=1, description="Dias de validade a partir de agora")
    ativo: Optional[bool] = Field(None, description="false bloqueia a loja, true libera")
    observacao: Optional[str] = Field(None, description="Anotacao interna (ex: id da transacao)")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook/licenca")
async def webhook_licenca(
    payload: EntradaDeLicenca,
    x_webhook_secret: str = Header(default=""),
) -> dict[str, object]:
    if not config.WEBHOOK_SECRET:
        raise HTTPException(
            status_code=503,
            detail="WEBHOOK_SECRET nao configurado no .env da plataforma.",
        )
    if x_webhook_secret != config.WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Assinatura do webhook invalida.")

    if payload.plano is not None and payload.plano not in PLANOS_VENDAVEIS:
        raise HTTPException(
            status_code=422,
            detail=f"Plano invalido: {payload.plano}. Use um de: {', '.join(PLANOS_VENDAVEIS)}.",
        )

    if payload.plano is None and payload.ativo is None:
        raise HTTPException(
            status_code=422,
            detail="Informe pelo menos 'plano' (para liberar/renovar) ou 'ativo' (para bloquear/liberar).",
        )

    resposta: dict[str, object] = {"guild_id": payload.guild_id}

    if payload.ativo is not None:
        licenca = await definir_bloqueio(payload.guild_id, not payload.ativo)
        resposta["ativo"] = licenca.ativo

    if payload.plano is not None:
        licenca = await definir_plano(
            payload.guild_id, payload.plano, payload.dias, payload.observacao
        )
        resposta["plano"] = licenca.plano
        resposta["expira_em"] = licenca.expira_em.isoformat() if licenca.expira_em else None

    log.info("Licenca atualizada via webhook: %s", resposta)
    return resposta


@app.post("/webhook/mercadopago")
async def webhook_mercadopago(request: Request) -> dict[str, object]:
    """O Mercado Pago avisa aqui quando um PIX muda de estado.

    A confirmacao e sempre refeita consultando a API com o SEU token, entao
    um aviso falso nao libera nada.
    """
    from cobranca import sincronizar_pagamento

    try:
        corpo = await request.json()
    except Exception:  # noqa: BLE001 - o MP as vezes manda so query string
        corpo = {}

    mp_id = None
    if isinstance(corpo, dict):
        dados = corpo.get("data")
        if isinstance(dados, dict):
            mp_id = dados.get("id")
        mp_id = mp_id or corpo.get("id")
    if mp_id is None:
        mp_id = request.query_params.get("data.id") or request.query_params.get("id")

    if not mp_id:
        return {"ok": True, "ignorado": "sem id de pagamento"}

    pagamento = await pagamento_por_mp_id(str(mp_id))
    if pagamento is None:
        return {"ok": True, "ignorado": "pagamento nao encontrado"}

    resultado = await sincronizar_pagamento(bot_ref, pagamento)
    log.info("Webhook Mercado Pago: pagamento %s -> %s", pagamento.id, resultado)
    return {"ok": True, "pagamento": pagamento.id, "resultado": resultado}
