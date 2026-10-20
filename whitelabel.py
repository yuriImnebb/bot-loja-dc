"""Hospedagem dos bots proprios dos clientes do plano Pro (white-label).

Cada servidor Pro que mandou o token do proprio bot ganha uma instancia
separada rodando aqui dentro, com a identidade que o cliente configurou no
Discord (nome, foto e bio dele).

Quando a assinatura Pro vence (ou a loja e bloqueada), a instancia e desligada
sozinha — o cliente nao consegue continuar usando sem pagar.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord
from discord.ext import commands

from db import (
    Session,
    ids_de_pedidos_pendentes_do_guild,
    listar_bots_proprios,
    marcar_erro_bot_proprio,
    plano_do_servidor,
    salvar_bot_proprio,
)
from models import PLANO_PRO
from views import AbrirLojaView, AprovacaoPedidoView

log = logging.getLogger(__name__)

# O bot do cliente tem os comandos da lojinha, mas NAO os de plataforma
# (/master e /assinar), que continuam exclusivos do bot principal.
EXTENSOES_DO_CLIENTE = (
    "cogs.catalogo",
    "cogs.loja",
    "cogs.vendas",
    "cogs.estoque",
    "cogs.admin",
    "cogs.ajuda",
)

INTERVALO_DE_VIGIA = 180  # segundos


class BotCliente(commands.Bot):
    """Uma instancia do bot com o token do cliente (white-label)."""

    def __init__(self, guild_id: int) -> None:
        super().__init__(command_prefix="!", intents=discord.Intents.default(), help_command=None)
        self.guild_id = guild_id

    async def setup_hook(self) -> None:
        self.add_view(AbrirLojaView())
        for pedido_id in await ids_de_pedidos_pendentes_do_guild(self.guild_id):
            self.add_view(AprovacaoPedidoView(pedido_id))

        for extensao in EXTENSOES_DO_CLIENTE:
            await self.load_extension(extensao)

        self.tree.interaction_check = self._licenca_liberada
        await self.tree.sync()

    async def _licenca_liberada(self, interacao: discord.Interaction) -> bool:
        """Recusa os comandos se a assinatura Pro deste servidor nao estiver ativa."""
        if interacao.guild is None:
            return True
        if await plano_do_servidor(interacao.guild_id) == PLANO_PRO:
            return True

        mensagem = (
            "🔒 A assinatura **Pro** deste servidor esta inativa, entao este bot foi desativado.\n"
            "Renove no bot principal (comando `/assinar`) para voltar a funcionar."
        )
        if interacao.response.is_done():
            await interacao.followup.send(mensagem, ephemeral=True)
        else:
            await interacao.response.send_message(mensagem, ephemeral=True)
        return False

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info(
            "Bot proprio do servidor %s online como %s (ID %s)",
            self.guild_id,
            self.user,
            self.user.id,
        )
        # Deixa os comandos disponiveis na hora neste servidor.
        for guild in self.guilds:
            try:
                self.tree.copy_global_to(guild=guild)
                await self.tree.sync(guild=guild)
            except discord.HTTPException:
                log.warning("Nao consegui sincronizar os comandos no servidor %s.", guild.id)


class GerenciadorWhiteLabel:
    """Liga, desliga e vigia as instancias dos bots dos clientes."""

    def __init__(self) -> None:
        self._bots: dict[int, BotCliente] = {}
        self._tarefas: dict[int, asyncio.Task] = {}

    # ------------------------------------------------------------------ #
    # Consultas
    # ------------------------------------------------------------------ #
    def esta_rodando(self, guild_id: int) -> bool:
        return guild_id in self._bots

    def quantos_rodando(self) -> int:
        return len(self._bots)

    # ------------------------------------------------------------------ #
    # Ligar / desligar
    # ------------------------------------------------------------------ #
    async def iniciar(
        self, guild_id: int, token: str, salvar: bool = True
    ) -> tuple[bool, str]:
        """Valida o token e sobe a instancia. Devolve (ok, mensagem/nome)."""
        token = token.strip()
        if not token:
            return False, "Token vazio."
        if guild_id in self._bots:
            return False, "Ja existe um bot proprio rodando neste servidor."

        for registro in await listar_bots_proprios():
            if registro.token == token and registro.guild_id != guild_id:
                return False, "Este token ja esta em uso por outro servidor."

        bot = BotCliente(guild_id)
        try:
            await bot.login(token)
        except Exception as erro:  # noqa: BLE001 - mensagem amigavel em qualquer falha
            await self._fechar(bot)
            if isinstance(erro, discord.LoginFailure):
                return False, "Token invalido. Confira se voce copiou o token do bot certo."
            if isinstance(erro, discord.HTTPException):
                return False, f"O Discord recusou o token (erro {erro.status}). Tente novamente."
            log.exception("Falha ao validar o token do servidor %s.", guild_id)
            return False, "Nao consegui falar com o Discord agora. Tente novamente em instantes."

        if bot.user is None or not bot.user.bot:
            await self._fechar(bot)
            return False, (
                "Esse token nao e de um bot (parece ser de usuario). "
                "Use o token que aparece em **Bot > Reset Token**, na aba Bot da sua aplicacao."
            )

        nome = str(bot.user)
        if salvar:
            await salvar_bot_proprio(guild_id, token, bot.user.id, nome)

        self._bots[guild_id] = bot
        self._tarefas[guild_id] = asyncio.create_task(self._rodar(guild_id, bot))
        return True, nome

    async def _rodar(self, guild_id: int, bot: BotCliente) -> None:
        try:
            await bot.connect()
        except Exception:  # noqa: BLE001 - a instancia nao pode derrubar o bot principal
            log.exception("Bot proprio do servidor %s caiu.", guild_id)
            await marcar_erro_bot_proprio(guild_id, "desconectado (veja os logs)")
        finally:
            self._bots.pop(guild_id, None)
            self._tarefas.pop(guild_id, None)
            log.info("Bot proprio do servidor %s encerrado.", guild_id)

    @staticmethod
    async def _fechar(bot: BotCliente) -> None:
        try:
            await bot.close()
        except Exception:  # noqa: BLE001
            pass

    async def parar(self, guild_id: int) -> bool:
        bot = self._bots.get(guild_id)
        if bot is None:
            return False
        try:
            await bot.close()
        except Exception:  # noqa: BLE001
            log.exception("Erro ao desligar o bot proprio do servidor %s.", guild_id)
        self._bots.pop(guild_id, None)
        return True

    # ------------------------------------------------------------------ #
    # Inicializacao e vigilancia
    # ------------------------------------------------------------------ #
    async def iniciar_todos(self) -> None:
        """Sobe os bots dos clientes que ja estavam configurados e sao Pro."""
        registros = await listar_bots_proprios()
        for registro in registros:
            if not registro.ativo:
                continue
            if await plano_do_servidor(registro.guild_id) != PLANO_PRO:
                log.info(
                    "Bot proprio do servidor %s ignorado (plano nao e Pro).", registro.guild_id
                )
                continue
            ok, mensagem = await self.iniciar(registro.guild_id, registro.token, salvar=False)
            if not ok:
                log.warning("Nao consegui subir o bot proprio do servidor %s: %s", registro.guild_id, mensagem)
                await marcar_erro_bot_proprio(registro.guild_id, mensagem)

    async def vigiar(self) -> None:
        """Desliga os bots proprios cujo servidor deixou de ser Pro."""
        while True:
            await asyncio.sleep(INTERVALO_DE_VIGIA)
            for guild_id in list(self._bots):
                if await plano_do_servidor(guild_id) == PLANO_PRO:
                    continue
                if await self.parar(guild_id):
                    await marcar_erro_bot_proprio(guild_id, "parado: assinatura Pro inativa")
                    log.info(
                        "Bot proprio do servidor %s desligado (assinatura Pro inativa).", guild_id
                    )


gerenciador = GerenciadorWhiteLabel()
