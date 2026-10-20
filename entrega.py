"""Entrega automatica do produto ao comprador (planos Media e Pro).

Quando o dono da loja confirma o pagamento, o bot:
1. verifica se o plano do servidor libera a entrega automatica;
2. separa a proxima unidade do estoque (link ou bloco de texto);
3. manda essa unidade na DM do comprador.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord

import config
from db import consumir_item_estoque, plano_do_servidor
from models import (
    ITEM_LINK,
    ItemEstoque,
    Pedido,
    plano_tem_entrega_automatica,
)
from utils import formatar_preco

log = logging.getLogger(__name__)

ENTREGUE = "entregue"
SEM_ESTOQUE = "sem_estoque"
PLANO_SEM_ENTREGA = "plano_sem_entrega"
DM_FALHOU = "dm_falhou"

LIMITE_DE_CARACTERES = 1900


def _formatar_conteudo(item: ItemEstoque) -> str:
    """Deixa o link clicavel e o bloco de texto em caixa de codigo."""
    conteudo = item.conteudo.strip()
    if item.tipo == ITEM_LINK:
        return conteudo
    if len(conteudo) > LIMITE_DE_CARACTERES:
        conteudo = conteudo[:LIMITE_DE_CARACTERES] + "\n... (conteudo cortado)"
    return f"```\n{conteudo}\n```"


async def _enviar_dm(bot: discord.Client, pedido: Pedido, item: ItemEstoque) -> bool:
    """Tenta entregar por DM. Devolve False se o comprador bloqueou a DM."""
    try:
        usuario = bot.get_user(pedido.comprador_id) or await bot.fetch_user(pedido.comprador_id)
    except discord.HTTPException:
        log.warning("Nao encontrei o comprador %s do pedido %s.", pedido.comprador_id, pedido.id)
        return False

    embed = discord.Embed(
        title="🎁 Sua compra foi entregue!",
        description=_formatar_conteudo(item),
        color=config.COR_SUCESSO,
    )
    embed.add_field(name="Produto", value=pedido.produto_nome[:100], inline=True)
    embed.add_field(name="Pedido", value=f"#{pedido.id}", inline=True)
    embed.add_field(name="Valor pago", value=formatar_preco(pedido.preco_centavos), inline=True)
    embed.set_footer(text="Guarde esta mensagem. Qualquer problema, fale com o dono da loja.")

    try:
        await usuario.send(embed=embed)
        return True
    except discord.HTTPException:
        log.warning("DM fechada para o comprador %s (pedido %s).", pedido.comprador_id, pedido.id)
        return False


async def entregar_pedido(
    bot: discord.Client, pedido: Pedido
) -> tuple[str, Optional[ItemEstoque]]:
    """Tenta entregar o pedido. Devolve (resultado, item_usado)."""
    plano = await plano_do_servidor(pedido.guild_id)
    if not plano_tem_entrega_automatica(plano):
        return PLANO_SEM_ENTREGA, None

    item = await consumir_item_estoque(pedido.guild_id, pedido.produto_id, pedido.id)
    if item is None:
        return SEM_ESTOQUE, None

    if await _enviar_dm(bot, pedido, item):
        return ENTREGUE, item

    return DM_FALHOU, item


def descrever_resultado(resultado: str) -> str:
    """Frase curta para o dono da loja entender o que aconteceu na entrega."""
    if resultado == ENTREGUE:
        return "🎁 Produto entregue automaticamente na DM do comprador."
    if resultado == SEM_ESTOQUE:
        return "⚠️ Estoque vazio para este produto — entregue manualmente."
    if resultado == PLANO_SEM_ENTREGA:
        return "ℹ️ Entrega automatica disponivel a partir do plano **Media** — entregue manualmente."
    if resultado == DM_FALHOU:
        return "⚠️ Nao consegui mandar a DM (o comprador pode ter a DM fechada). Envie o item abaixo."
    return ""


async def enviar_aviso_de_dm_falhou(
    interacao: discord.Interaction, item: ItemEstoque
) -> None:
    """Mostra o item ao dono da loja quando a DM nao puder ser entregue."""
    try:
        await interacao.followup.send(
            content=(
                "⚠️ A DM do comprador esta fechada. Copie e envie o item manualmente:\n"
                f"{_formatar_conteudo(item)[:1800]}"
            ),
            ephemeral=True,
        )
    except discord.HTTPException:
        log.warning("Nao consegui avisar o dono sobre a falha de DM.")
