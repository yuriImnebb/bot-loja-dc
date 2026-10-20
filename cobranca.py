"""Sincroniza as cobrancas do Mercado Pago com as licencas dos servidores.

Quando um PIX e aprovado (pelo webhook ou pela checagem periodica), a licenca
do servidor e renovada e o cliente e avisado no Discord.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord

import config
import mercadopago
from db import (
    Session,
    marcar_pagamento_aprovado,
    marcar_pagamento_expirado,
    pagamento_venceu,
    pagamentos_pendentes,
    pegar_loja,
)
from models import PAGAMENTO_APROVADO, Pagamento, ROTULOS_DE_PLANO
from utils import formatar_preco

log = logging.getLogger(__name__)

# Resultados possiveis de uma sincronizacao
APROVADO = "aprovado"
PENDENTE = "pendente"
EXPIRADO = "expirado"
INDISPONIVEL = "indisponivel"
ERRO = "erro"


def _rotulo(plano: str) -> str:
    return ROTULOS_DE_PLANO.get(plano, plano)


async def sincronizar_pagamento(bot: Optional[discord.Client], pagamento: Pagamento) -> str:
    """Olha o status no Mercado Pago e, se pago, libera o plano.

    Devolve um dos resultados acima (APROVADO, PENDENTE, EXPIRADO...).
    """
    if not mercadopago.configurado():
        return INDISPONIVEL

    if pagamento.mp_payment_id:
        try:
            dados = await mercadopago.consultar_pagamento(pagamento.mp_payment_id)
        except mercadopago.ErroMercadoPago as erro:
            log.warning("Falha ao consultar pagamento %s: %s", pagamento.id, erro)
            return ERRO

        status = dados.get("status")
        if mercadopago.eh_aprovado(status):
            aprovado = await marcar_pagamento_aprovado(pagamento.id)
            if aprovado is not None and aprovado.status == PAGAMENTO_APROVADO:
                await avisar_liberacao(bot, aprovado)
            return APROVADO

        if mercadopago.eh_encerrado(status):
            await marcar_pagamento_expirado(pagamento.id)
            return EXPIRADO

        # Ainda pendente no MP: cai no teste de prazo abaixo.

    if pagamento_venceu(pagamento):
        await marcar_pagamento_expirado(pagamento.id)
        return EXPIRADO

    return PENDENTE


async def avisar_liberacao(bot: Optional[discord.Client], pagamento: Pagamento) -> None:
    """Manda um recado para o cliente e registra no canal de logs da loja."""
    texto = (
        f"🎉 Pagamento confirmado!\n"
        f"O plano **{_rotulo(pagamento.plano)}** do seu servidor esta ativo por "
        f"**{pagamento.dias} dias**.\n"
        f"Valor: {formatar_preco(pagamento.valor_centavos)} — obrigado!"
    )

    if bot is None:
        return

    try:
        usuario = bot.get_user(pagamento.solicitante_id) or await bot.fetch_user(
            pagamento.solicitante_id
        )
        await usuario.send(texto)
    except discord.HTTPException:
        log.warning("Nao consegui avisar o pagador %s.", pagamento.solicitante_id)

    try:
        async with Session() as sessao:
            loja = await pegar_loja(sessao, pagamento.guild_id)
        if loja.canal_logs_id:
            canal = bot.get_channel(loja.canal_logs_id)
            if canal is not None:
                await canal.send(texto)
    except discord.HTTPException:
        log.warning("Nao consegui registrar a liberacao no canal de logs.")

    if config.OWNER_IDS:
        for dono_id in config.OWNER_IDS:
            try:
                dono = bot.get_user(dono_id) or await bot.fetch_user(dono_id)
                await dono.send(
                    f"💰 Nova assinatura paga: **{_rotulo(pagamento.plano)}** "
                    f"({formatar_preco(pagamento.valor_centavos)}) — servidor `{pagamento.guild_id}`."
                )
            except discord.HTTPException:
                continue


async def processar_pendentes(bot: Optional[discord.Client], limite: int = 25) -> dict[str, int]:
    """Confere todas as cobrancas pendentes. Usada pela tarefa periodica."""
    resultado: dict[str, int] = {}
    for pagamento in await pagamentos_pendentes(limite):
        estado = await sincronizar_pagamento(bot, pagamento)
        resultado[estado] = resultado.get(estado, 0) + 1
    return resultado
