"""Ponto de entrada: sobe o bot do Discord e a API interna no mesmo processo."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
import whitelabel
from cogs.assinatura import AssinarView
from db import ids_de_pedidos_pendentes, init_db, plano_do_servidor
from models import PLANO_BLOQUEADO
from views import AbrirLojaView, AprovacaoPedidoView

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
log = logging.getLogger("vendasbot")

EXTENSOES = (
    "cogs.catalogo",
    "cogs.loja",
    "cogs.vendas",
    "cogs.estoque",
    "cogs.admin",
    "cogs.master",
    "cogs.assinatura",
    "cogs.whitelabel",
    "cogs.ajuda",
)


class BotVendas(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix="!",
            intents=discord.Intents.default(),
            help_command=None,
        )

    async def setup_hook(self) -> None:
        await init_db()
        await self._registrar_views_persistentes()

        for extensao in EXTENSOES:
            await self.load_extension(extensao)

        self.tree.interaction_check = self._licenca_liberada
        await self._sincronizar_comandos()

        if config.API_ENABLED:
            asyncio.create_task(_iniciar_api(self))

        # Sobe os bots proprios dos clientes Pro (white-label) e vigia as
        # assinaturas para desliga-los quando o plano cair.
        asyncio.create_task(whitelabel.gerenciador.iniciar_todos())
        asyncio.create_task(whitelabel.gerenciador.vigiar())

    async def _registrar_views_persistentes(self) -> None:
        """Reativa os botoes que ja estavam no ar antes do reinicio."""
        self.add_view(AbrirLojaView())
        self.add_view(AssinarView())

        pendentes = await ids_de_pedidos_pendentes()
        for pedido_id in pendentes:
            self.add_view(AprovacaoPedidoView(pedido_id))

        log.info("Views persistentes registradas (%d pedido(s) pendente(s)).", len(pendentes))

    async def _licenca_liberada(self, interacao: discord.Interaction) -> bool:
        """Bloqueia os comandos em servidores com a loja desligada.

        O dono da plataforma (OWNER_IDS) nunca e barrado, para poder consertar
        qualquer loja pelo proprio Discord.
        """
        if interacao.guild is None or interacao.user.id in config.OWNER_IDS:
            return True

        if await plano_do_servidor(interacao.guild_id) != PLANO_BLOQUEADO:
            return True

        mensagem = (
            "🔒 A loja deste servidor esta **temporariamente desativada**.\n"
            "Fale com o dono da loja para regularizar a assinatura."
        )
        if interacao.response.is_done():
            await interacao.followup.send(mensagem, ephemeral=True)
        else:
            await interacao.response.send_message(mensagem, ephemeral=True)
        return False

    async def _sincronizar_comandos(self) -> None:
        if config.DEV_GUILD_IDS:
            for guild_id in config.DEV_GUILD_IDS:
                guild = discord.Object(id=guild_id)
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            log.info("Comandos sincronizados nos servidores de teste: %s", config.DEV_GUILD_IDS)
        else:
            await self.tree.sync()
            log.info("Comandos sincronizados globalmente (pode levar ate 1 hora para aparecer).")

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info("Bot online como %s (ID %s)", self.user, self.user.id)
        log.info("Presente em %d servidor(es).", len(self.guilds))
        await self.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name="/loja")
        )


async def _iniciar_api(bot: "BotVendas") -> None:
    """Roda o FastAPI no mesmo loop de eventos do bot."""
    import uvicorn

    from web import api

    api.registrar_bot(bot)

    servidor = uvicorn.Server(
        uvicorn.Config(api.app, host=config.API_HOST, port=config.API_PORT, log_level="warning")
    )
    log.info("API interna ouvindo em http://%s:%s", config.API_HOST, config.API_PORT)
    await servidor.serve()


bot = BotVendas()


@bot.tree.error
async def ao_dar_erro(interacao: discord.Interaction, erro: app_commands.AppCommandError) -> None:
    original = getattr(erro, "original", erro)

    if isinstance(original, app_commands.CheckFailure):
        mensagem = str(original) or "Voce nao tem permissao para usar este comando."
    else:
        log.error(
            "Erro no comando %s",
            getattr(interacao.command, "qualified_name", "desconhecido"),
            exc_info=original,
        )
        mensagem = "⚠️ Algo deu errado ao executar este comando. Tente novamente em instantes."

    if interacao.response.is_done():
        await interacao.followup.send(mensagem, ephemeral=True)
    else:
        await interacao.response.send_message(mensagem, ephemeral=True)


def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit(
            "BOT_TOKEN nao configurado.\n"
            "Copie o arquivo .env.example para .env e cole o token do seu bot."
        )
    bot.run(config.BOT_TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
