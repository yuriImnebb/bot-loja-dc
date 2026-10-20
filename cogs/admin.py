"""Ajuda de instalacao: montar os canais da loja e gerar o link do bot."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from db import Session, pegar_loja
from utils import exigir_admin
from views import AbrirLojaView, montar_painel

log = logging.getLogger(__name__)

NOME_CATEGORIA = "🛒 Loja"
NOME_CANAL_VENDAS = "🛒・vendas"
NOME_CANAL_LOGS = "📋・logs-vendas"


class Admin(commands.Cog):
    """Comandos que deixam o servidor pronto para vender."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ #
    # /convite — serve para qualquer pessoa adicionar o bot
    # ------------------------------------------------------------------ #
    @app_commands.command(
        name="convite", description="Gera o link para adicionar o bot no servidor com permissao de Administrador"
    )
    async def convite(self, interacao: discord.Interaction) -> None:
        if self.bot.user is None:
            await interacao.response.send_message("Bot ainda inicializando, tente de novo.", ephemeral=True)
            return

        link = discord.utils.oauth_url(
            self.bot.user.id,
            permissions=discord.Permissions(administrator=True),
            scopes=("bot", "applications.commands"),
        )

        embed = discord.Embed(
            title="➕ Adicionar o bot no seu servidor",
            description=(
                "Abra o link abaixo, escolha o servidor e autorize.\n\n"
                "Peço permissao de **Administrador** para criar os canais da loja, "
                "publicar o painel e apagar mensagens de pedidos antigos sem pedir nada a cada passo."
            ),
            color=config.COR_LOJA,
        )
        embed.add_field(name="Link de convite", value=f"[Clique aqui para me adicionar]({link})", inline=False)
        embed.set_footer(text="Primeiro uso: rode /configurar para eu montar os canais automaticamente.")
        await interacao.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ #
    # /configurar — monta os canais da loja
    # ------------------------------------------------------------------ #
    @app_commands.command(
        name="configurar", description="Cria os canais da loja automaticamente e publica o painel"
    )
    @app_commands.guild_only()
    @exigir_admin()
    async def configurar(self, interacao: discord.Interaction) -> None:
        assert interacao.guild is not None

        if not interacao.guild.me.guild_permissions.manage_channels:
            await interacao.response.send_message(
                "Preciso da permissao **Gerenciar Canais** para montar a loja.\n"
                "Reconvide-me com `/convite` (permissao de Administrador) ou marque essa opcao no meu cargo.",
                ephemeral=True,
            )
            return

        await interacao.response.defer(ephemeral=True)

        guild = interacao.guild
        async with Session() as sessao:
            loja = await pegar_loja(sessao, interacao.guild_id)
            vendas_id, logs_id = loja.canal_vendas_id, loja.canal_logs_id

        criados: list[str] = []

        categoria = discord.utils.get(guild.categories, name=NOME_CATEGORIA)
        if categoria is None:
            categoria = await guild.create_category(NOME_CATEGORIA)
            criados.append("categoria")

        canal_vendas = guild.get_channel(vendas_id) if vendas_id else None
        if canal_vendas is None:
            canal_vendas = discord.utils.get(guild.text_channels, name=NOME_CANAL_VENDAS)
        if canal_vendas is None:
            canal_vendas = await guild.create_text_channel(NOME_CANAL_VENDAS, category=categoria)
            criados.append("canal de vendas")

        canal_logs = guild.get_channel(logs_id) if logs_id else None
        if canal_logs is None:
            canal_logs = discord.utils.get(guild.text_channels, name=NOME_CANAL_LOGS)
        if canal_logs is None:
            canal_logs = await guild.create_text_channel(NOME_CANAL_LOGS, category=categoria)
            criados.append("canal de logs")

        async with Session() as sessao:
            loja = await pegar_loja(sessao, interacao.guild_id)
            loja.canal_vendas_id = canal_vendas.id
            loja.canal_logs_id = canal_logs.id
            await sessao.commit()

        publicado = True
        try:
            await canal_vendas.send(embed=montar_painel(loja), view=AbrirLojaView())
        except discord.HTTPException:
            publicado = False
            log.warning("Nao consegui publicar o painel no servidor %s.", guild.id)

        embed = discord.Embed(
            title="✅ Loja pronta para vender",
            description=(
                f"**Canal de vendas:** {canal_vendas.mention}\n"
                f"**Canal de logs:** {canal_logs.mention}\n\n"
                + ("Painel publicado no canal de vendas." if publicado else "⚠️ Nao consegui publicar o painel; use `/painel publicar`.")
            ),
            color=config.COR_SUCESSO,
        )
        if criados:
            embed.add_field(name="Criado agora", value=", ".join(criados), inline=False)
        embed.set_footer(text="Falta configurar a chave PIX com /painel configurar.")
        await interacao.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
