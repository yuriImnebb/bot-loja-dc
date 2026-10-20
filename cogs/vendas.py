"""Comandos de acompanhamento e aprovacao de pedidos."""

from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

import config
from db import Session, marcar_cancelado, marcar_pago
from entrega import DM_FALHOU, descrever_resultado, entregar_pedido, enviar_aviso_de_dm_falhou
from models import STATUS_PAGO, STATUS_PENDENTE, Pedido
from utils import exigir_admin, formatar_preco
from views import atualizar_card_pedido, registrar_log


class Vendas(commands.Cog):
    """Controle dos pedidos: quem pagou, quem esta pendente, quem foi cancelado."""

    grupo_pedidos = app_commands.Group(
        name="pedidos",
        description="Consulte e gerencie os pedidos da sua loja",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @grupo_pedidos.command(name="listar", description="Lista os pedidos recentes da loja")
    @app_commands.describe(status="Filtrar por status (opcional)")
    @app_commands.choices(
        status=[
            app_commands.Choice(name="Pendentes", value=STATUS_PENDENTE),
            app_commands.Choice(name="Pagos", value=STATUS_PAGO),
            app_commands.Choice(name="Cancelados", value="cancelado"),
        ]
    )
    @exigir_admin()
    async def pedidos_listar(
        self, interacao: discord.Interaction, status: app_commands.Choice[str] | None = None
    ) -> None:
        consulta = select(Pedido).where(Pedido.guild_id == interacao.guild_id)
        consulta = (
            consulta.where(Pedido.status == status.value) if status is not None else consulta
        )

        async with Session() as sessao:
            pedidos = list(
                (await sessao.scalars(consulta.order_by(Pedido.id.desc()).limit(20))).all()
            )

        if not pedidos:
            await interacao.response.send_message(
                "Nenhum pedido encontrado com esse filtro.", ephemeral=True
            )
            return

        rotulos = {
            STATUS_PENDENTE: "⏳ pendente",
            STATUS_PAGO: "✅ pago",
            "cancelado": "✖️ cancelado",
        }

        embed = discord.Embed(title="🧾 Ultimos pedidos", color=config.COR_LOJA)
        linhas = [
            f"`#{pedido.id}` {rotulos.get(pedido.status, pedido.status)} — "
            f"{pedido.produto_nome} — {formatar_preco(pedido.preco_centavos)} — <@{pedido.comprador_id}>"
            for pedido in pedidos
        ]
        embed.description = "\n".join(linhas)[:4000]
        embed.set_footer(text="Use /pedidos confirmar ou /pedidos cancelar com o numero do pedido.")
        await interacao.response.send_message(embed=embed, ephemeral=True)

    @grupo_pedidos.command(name="confirmar", description="Marca um pedido como pago (o cliente ja pagou)")
    @app_commands.describe(pedido_id="Numero do pedido, exemplo: 12")
    @exigir_admin()
    async def pedidos_confirmar(self, interacao: discord.Interaction, pedido_id: int) -> None:
        await interacao.response.defer(ephemeral=True)

        pedido, erro = await marcar_pago(pedido_id, interacao.user.id)
        if erro or pedido is None or pedido.guild_id != interacao.guild_id:
            await interacao.followup.send(
                f"⚠️ {erro or 'Pedido nao encontrado neste servidor.'}", ephemeral=True
            )
            return

        await atualizar_card_pedido(interacao.client, pedido)

        resultado, item = await entregar_pedido(interacao.client, pedido)
        descricao = descrever_resultado(resultado)

        await registrar_log(
            interacao.client,
            pedido.guild_id,
            f"💰 Pedido **#{pedido.id}** confirmado manualmente por {interacao.user.mention}. {descricao}",
        )
        await interacao.followup.send(
            f"✅ Pedido **#{pedido.id}** marcado como pago.\n{descricao}",
            ephemeral=True,
        )

        if resultado == DM_FALHOU and item is not None:
            await enviar_aviso_de_dm_falhou(interacao, item)

    @grupo_pedidos.command(name="cancelar", description="Cancela um pedido")
    @app_commands.describe(pedido_id="Numero do pedido, exemplo: 12")
    @exigir_admin()
    async def pedidos_cancelar(self, interacao: discord.Interaction, pedido_id: int) -> None:
        await interacao.response.defer(ephemeral=True)

        pedido, erro = await marcar_cancelado(pedido_id, interacao.user.id)
        if erro or pedido is None or pedido.guild_id != interacao.guild_id:
            await interacao.followup.send(
                f"⚠️ {erro or 'Pedido nao encontrado neste servidor.'}", ephemeral=True
            )
            return

        await atualizar_card_pedido(interacao.client, pedido)
        await registrar_log(
            interacao.client,
            pedido.guild_id,
            f"✖️ Pedido **#{pedido.id}** cancelado por {interacao.user.mention}.",
        )
        await interacao.followup.send(f"✖️ Pedido **#{pedido.id}** cancelado.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Vendas(bot))
