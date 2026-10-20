"""Cliente da API do Mercado Pago para cobrar a assinatura via PIX.

O dinheiro cai direto na conta dona do token (MERCADOPAGO_ACCESS_TOKEN).
Aqui so criamos a cobranca e consultamos o status dela.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

import aiohttp

import config

log = logging.getLogger(__name__)

API_BASE = "https://api.mercadopago.com"
TEMPO_LIMITE = aiohttp.ClientTimeout(total=25)

# Status que o Mercado Pago devolve em /v1/payments
STATUS_APROVADO = "approved"
STATUS_PENDENTE = "pending"
STATUS_EM_PROCESSAMENTO = "in_process"
STATUS_AUTORIZADO = "authorized"
STATUS_REJEITADO = "rejected"
STATUS_CANCELADO = "cancelled"
STATUS_REEMBOLSADO = "refunded"
STATUS_ESTORNADO = "charged_back"

# Status que consideramos "encerrado sem pagamento"
STATUS_ENCERRADOS = (STATUS_CANCELADO, STATUS_REJEITADO, STATUS_REEMBOLSADO, STATUS_ESTORNADO)


class ErroMercadoPago(Exception):
    """Falha ao conversar com a API do Mercado Pago."""


@dataclass
class CobrancaPix:
    """Dados do PIX gerado, prontos para mostrar no Discord."""

    id: str
    copia_cola: str
    qr_base64: Optional[str]
    status: str
    expira_em: Optional[datetime]


def configurado() -> bool:
    """True se o token do Mercado Pago ja foi colocado no .env."""
    return bool(config.MERCADOPAGO_ACCESS_TOKEN)


def _cabecalhos() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {config.MERCADOPAGO_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


async def _requisicao(metodo: str, caminho: str, **kwargs: Any) -> dict[str, Any]:
    if not configurado():
        raise ErroMercadoPago("Token do Mercado Pago nao configurado (MERCADOPAGO_ACCESS_TOKEN).")

    cabecalhos = _cabecalhos()
    if metodo.upper() in ("POST", "PUT", "PATCH"):
        # O Mercado Pago exige uma chave de idempotencia nas gravacoes.
        cabecalhos["X-Idempotency-Key"] = str(uuid.uuid4())

    url = f"{API_BASE}{caminho}"
    try:
        async with aiohttp.ClientSession(timeout=TEMPO_LIMITE) as sessao:
            async with sessao.request(metodo, url, headers=cabecalhos, **kwargs) as resposta:
                texto = await resposta.text()
                if resposta.status >= 400:
                    log.error("Mercado Pago %s %s -> %s: %s", metodo, caminho, resposta.status, texto[:500])
                    raise ErroMercadoPago(
                        f"Mercado Pago recusou a requisicao ({resposta.status})."
                    )
                try:
                    return await resposta.json()
                except Exception as erro:  # noqa: BLE001
                    raise ErroMercadoPago("Resposta invalida do Mercado Pago.") from erro
    except aiohttp.ClientError as erro:
        raise ErroMercadoPago("Nao consegui falar com o Mercado Pago. Tente novamente.") from erro


def _parsear_data(bruto: Optional[str]) -> Optional[datetime]:
    if not bruto:
        return None
    try:
        return datetime.fromisoformat(bruto.replace("Z", "+00:00"))
    except ValueError:
        return None


async def criar_cobranca_pix(
    *,
    valor_centavos: int,
    descricao: str,
    referencia: str,
    email_pagador: str,
    expira_em_minutos: int = 30,
) -> CobrancaPix:
    """Cria uma cobranca PIX e devolve o codigo Copia e Cola + QR Code."""
    corpo: dict[str, Any] = {
        "transaction_amount": round(valor_centavos / 100, 2),
        "description": descricao[:250],
        "payment_method_id": "pix",
        "external_reference": referencia[:250],
        "payer": {"email": email_pagador},
    }

    if config.PUBLIC_URL:
        corpo["notification_url"] = f"{config.PUBLIC_URL}/webhook/mercadopago"

    if expira_em_minutos:
        from datetime import timedelta, timezone

        expira = datetime.now(timezone.utc) + timedelta(minutes=expira_em_minutos)
        corpo["date_of_expiration"] = expira.strftime("%Y-%m-%dT%H:%M:%S.000-00:00")

    dados = await _requisicao("POST", "/v1/payments", json=corpo)

    transacao = (dados.get("point_of_interaction") or {}).get("transaction_data") or {}
    copia_cola = transacao.get("qr_code")
    if not copia_cola:
        raise ErroMercadoPago("O Mercado Pago nao devolveu o codigo PIX. Confira o token e a conta.")

    return CobrancaPix(
        id=str(dados.get("id", "")),
        copia_cola=copia_cola,
        qr_base64=transacao.get("qr_code_base64"),
        status=str(dados.get("status", STATUS_PENDENTE)),
        expira_em=_parsear_data(dados.get("date_of_expiration")),
    )


async def consultar_pagamento(mp_payment_id: str) -> dict[str, Any]:
    """Busca o status atual de um pagamento pelo ID do Mercado Pago."""
    return await _requisicao("GET", f"/v1/payments/{mp_payment_id}")


def eh_aprovado(status: Optional[str]) -> bool:
    return status in (STATUS_APROVADO, STATUS_AUTORIZADO)


def eh_encerrado(status: Optional[str]) -> bool:
    return status in STATUS_ENCERRADOS
