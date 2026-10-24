"""Entrega automatica do produto ao comprador (planos Media e Pro).

Quando o dono da loja confirma o pagamento, o bot:
1. verifica se o plano do servidor libera a entrega automatica;
2. separa a proxima unidade do estoque (link ou bloco de texto);
3. manda essa unidade na DM do comprador.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import discord

import config
from db import (
    Session,
    automacao_ativa,
    consumir_item_estoque,
    marcar_alerta_estoque_baixo,
    plano_do_servidor,
)
from models import (
    ITEM_LINK,
    ItemEstoque,
    Loja,
    Pedido,
    Produto,
    plano_tem_entrega_automatica,
)
from utils import formatar_preco

log = logging.getLogger(__name__)

ENTREGUE = "entregue"
SEM_ESTOQUE = "sem_estoque"
PLANO_SEM_ENTREGA = "plano_sem_entrega"
DM_FALHOU = "dm_falhou"
ENTREGA_MANUAL = "entrega_manual"

LIMITE_DE_CARACTERES = 1900
LIMITE_ESTOQUE_BAIXO = 5


@dataclass(frozen=True)
class ItemParaEntrega:
    tipo: str
    conteudo: str


def _item_para_entrega(item: ItemEstoque) -> ItemParaEntrega:
    return ItemParaEntrega(tipo=item.tipo, conteudo=item.conteudo)


def _formatar_conteudo(item: ItemParaEntrega) -> str:
    """Deixa o link clicavel e o bloco de texto em caixa de codigo."""
    conteudo = item.conteudo.strip()
    if item.tipo == ITEM_LINK:
        return conteudo
    if len(conteudo) > LIMITE_DE_CARACTERES:
        conteudo = conteudo[:LIMITE_DE_CARACTERES] + "\n... (conteudo cortado)"
    return f"```\n{conteudo}\n```"


async def _enviar_dm(bot: discord.Client, pedido: Pedido, item: ItemParaEntrega) -> bool:
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
) -> tuple[str, Optional[ItemParaEntrega]]:
    """Tenta entregar o pedido. Devolve (resultado, item_usado)."""
    plano = await plano_do_servidor(pedido.guild_id)
    if not plano_tem_entrega_automatica(plano):
        return PLANO_SEM_ENTREGA, None

    async with Session() as sessao:
        produto = await sessao.get(Produto, pedido.produto_id)
    if produto is None or produto.guild_id != pedido.guild_id:
        log.error("Produto %s do pedido %s nao existe nesta loja.", pedido.produto_id, pedido.id)
        return ENTREGA_MANUAL, None

    if produto.estoque_limitado:
        item_estoque = await consumir_item_estoque(
            pedido.guild_id, produto.id, pedido.id
        )
        if item_estoque is None:
            return SEM_ESTOQUE, None
        item = _item_para_entrega(item_estoque)

        if await automacao_ativa(pedido.guild_id, "alertar_estoque_baixo"):
            restante = await marcar_alerta_estoque_baixo(
                pedido.guild_id, produto.id, LIMITE_ESTOQUE_BAIXO
            )
            if restante is not None:
                await _avisar_estoque_baixo(bot, pedido, produto.nome, restante)
    else:
        if not produto.entrega_infinita or not produto.tipo_entrega_infinita:
            return ENTREGA_MANUAL, None
        item = ItemParaEntrega(
            tipo=produto.tipo_entrega_infinita,
            conteudo=produto.entrega_infinita,
        )

    if await _enviar_dm(bot, pedido, item):
        return ENTREGUE, item

    return DM_FALHOU, item


async def _avisar_estoque_baixo(
    bot: discord.Client, pedido: Pedido, produto_nome: str, restante: int
) -> None:
    async with Session() as sessao:
        loja = await sessao.get(Loja, pedido.guild_id)

    canal = None
    if loja is not None and loja.canal_logs_id is not None:
        canal = bot.get_channel(loja.canal_logs_id)
        if canal is None:
            try:
                canal = await bot.fetch_channel(loja.canal_logs_id)
            except discord.HTTPException as erro:
                log.warning(
                    "Nao consegui buscar o canal de logs %s: %s",
                    loja.canal_logs_id,
                    erro,
                )

    mensagem = (
        f"⚠️ Estoque baixo: **{produto_nome}** tem **{restante}** unidade(s) "
        "disponivel(is). Reponha com `/estoque adicionar`."
    )
    if canal is not None:
        try:
            await canal.send(mensagem)
            return
        except discord.HTTPException as erro:
            log.warning("Nao consegui enviar aviso de estoque baixo: %s", erro)

    servidor = bot.get_guild(pedido.guild_id)
    if servidor is None:
        try:
            servidor = await bot.fetch_guild(pedido.guild_id)
        except discord.HTTPException as erro:
            log.warning("Nao consegui buscar o servidor %s para avisar estoque: %s", pedido.guild_id, erro)
            return

    try:
        dono = bot.get_user(servidor.owner_id) or await bot.fetch_user(servidor.owner_id)
        await dono.send(mensagem)
    except discord.HTTPException as erro:
        log.warning("Nao consegui avisar o dono sobre estoque baixo: %s", erro)


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
    if resultado == ENTREGA_MANUAL:
        return "⚠️ Este produto ainda nao tem conteudo de entrega configurado; entregue manualmente ou configure-o em `/produto editar`."
    return ""


async def enviar_aviso_de_dm_falhou(
    interacao: discord.Interaction, item: ItemParaEntrega
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
