"""Comandos do white-label (plano Pro): usar o proprio bot ou o da plataforma."""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
import whitelabel
from db import pegar_bot_proprio, plano_do_servidor, remover_bot_proprio
from models import PLANO_PRO
from utils import eh_dono, exigir_admin
from views import aplicar_icones_da_loja

log = logging.getLogger(__name__)


PASSOS_DA_APLICACAO = (
    "**1.** Abra o [Discord Developer Portal](https://discord.com/developers/applications) e clique em "
    "**New Application**.\n"
    "**2.** Coloque o **nome e a foto** que você quer que apareçam no seu bot e salve.\n"
    "**3.** Vá na aba **Bot** → clique em **Reset Token** → **Copie o token** "
    "(ele só aparece uma vez, guarde com cuidado).\n"
    "**4.** Ainda na aba **Bot**, não precisa mexer em nada de permissões/intents.\n"
    "**5.** Vá em **OAuth2 → General**, marque os escopos `bot` e `applications.commands`, "
    "escolha a permissão **Administrador** e abra o link gerado para adicionar o seu bot "
    "**no seu servidor**.\n"
    "**6.** Só depois disso, mande o token aqui no botão abaixo."
)


def montar_instrucoes() -> discord.Embed:
    embed = discord.Embed(
        title="🎨 White-label (plano Pro) — suas duas opções",
        description=(
            "No plano **Pro** você escolhe como quer o bot no seu servidor:"
        ),
        color=config.COR_LOJA,
    )
    embed.add_field(
        name="Opção 1 — Usar o bot da plataforma (não precisa fazer nada)",
        value=(
            "Continua tudo funcionando (estoque, entrega automática) com a **mesma foto e nome "
            "do bot oficial**. Ideal se você **não quer criar aplicação nenhuma**."
        ),
        inline=False,
    )
    embed.add_field(
        name="Opção 2 — Usar o SEU próprio bot (white-label de verdade)",
        value=(
            "Você cria a sua aplicação no Discord e o bot fica com **seu nome, sua foto e sua bio**. "
            "A hospedagem é feita por nós, então você **não precisa deixar nada ligado** no seu PC."
        ),
        inline=False,
    )
    embed.add_field(
        name="⚠️ Se você escolher a Opção 2, siga com atenção",
        value=PASSOS_DA_APLICACAO,
        inline=False,
    )
    embed.add_field(
        name="ℹ️ E se a minha assinatura vencer?",
        value=(
            "O seu bot é hospedado por nós. Quando o plano **Pro** vencer, ele é **desligado "
            "automaticamente** e só volta quando você renovar com `/assinar`. "
            "Ou seja: ninguém continua usando de graça."
        ),
        inline=False,
    )
    embed.set_footer(text="Dúvidas? Fale com o dono da plataforma.")
    return embed


class TokenBotModal(discord.ui.Modal, title="Conectar o seu bot"):
    campo_token = discord.ui.TextInput(
        label="Token do bot",
        placeholder="Cole aqui o token de Bot > Reset Token",
        min_length=30,
        max_length=200,
    )

    async def on_submit(self, interacao: discord.Interaction) -> None:
        if interacao.guild_id is None:
            await interacao.response.send_message("Use isto dentro de um servidor.", ephemeral=True)
            return

        await interacao.response.defer(ephemeral=True)

        ok, mensagem = await whitelabel.gerenciador.iniciar(
            interacao.guild_id, self.campo_token.value
        )

        if not ok:
            await interacao.followup.send(f"⚠️ {mensagem}", ephemeral=True)
            return

        registro = await pegar_bot_proprio(interacao.guild_id)
        link = None
        if registro is not None and registro.application_id:
            link = discord.utils.oauth_url(
                registro.application_id,
                permissions=discord.Permissions(administrator=True),
                scopes=("bot", "applications.commands"),
            )

        texto = (
            f"✅ Bot **{mensagem}** conectado com sucesso!\n\n"
            "Falta só adicioná-lo ao seu servidor:\n"
        )
        if link:
            texto += f"👉 **[Clique aqui para adicionar o seu bot]({link})**\n"
        texto += (
            "\nDepois de adicionar, os comandos da lojinha aparecem no seu bot "
            "(podem levar alguns minutos na primeira vez)."
        )
        await interacao.followup.send(texto, ephemeral=True)


class PrepararBotView(discord.ui.View):
    """Botao que abre o formulario do token."""

    def __init__(self) -> None:
        super().__init__(timeout=900)

    @discord.ui.button(
        label="Enviar token do meu bot", emoji="🔑", style=discord.ButtonStyle.primary
    )
    async def enviar(self, interacao: discord.Interaction, _: discord.ui.Button) -> None:
        await interacao.response.send_modal(TokenBotModal())


class WhiteLabel(commands.Cog):
    """White-label do plano Pro."""

    grupo_whitelabel = app_commands.Group(
        name="whitelabel",
        description="Coloque o SEU proprio bot no seu servidor (plano Pro)",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def _exigir_pro(self, interacao: discord.Interaction) -> bool:
        if interacao.guild is None:
            return False
        if eh_dono(interacao) or await plano_do_servidor(interacao.guild_id) == PLANO_PRO:
            return True
        await interacao.response.send_message(
            "🔒 O white-label é exclusivo do plano **Pro**.\n"
            "Use `/assinar` para migrar para o Pro e liberar o seu próprio bot.",
            ephemeral=True,
        )
        return False

    # ------------------------------------------------------------------ #
    # /whitelabel instrucoes
    # ------------------------------------------------------------------ #
    @grupo_whitelabel.command(
        name="instrucoes", description="Explica as duas opcoes do plano Pro e como criar sua aplicacao"
    )
    @exigir_admin()
    async def whitelabel_instrucoes(self, interacao: discord.Interaction) -> None:
        await interacao.response.send_message(embed=montar_instrucoes(), ephemeral=True)

    # ------------------------------------------------------------------ #
    # /whitelabel ativar
    # ------------------------------------------------------------------ #
    @grupo_whitelabel.command(
        name="ativar", description="Conecta o token do seu proprio bot neste servidor"
    )
    @exigir_admin()
    async def whitelabel_ativar(self, interacao: discord.Interaction) -> None:
        if not await self._exigir_pro(interacao):
            return

        if whitelabel.gerenciador.esta_rodando(interacao.guild_id):
            await interacao.response.send_message(
                "✅ Seu bot próprio já está conectado neste servidor.\n"
                "Use `/whitelabel status` para ver os detalhes ou `/whitelabel remover` para trocar.",
                ephemeral=True,
            )
            return

        embed = montar_instrucoes()
        embed.title = "🔑 Conectar o SEU bot"
        embed.description = "Leia com atenção antes de mandar o token 👇"
        await interacao.response.send_message(
            embed=embed,
            view=await aplicar_icones_da_loja(
                interacao.guild_id,
                PrepararBotView(),
            ),
            ephemeral=True,
        )

    # ------------------------------------------------------------------ #
    # /whitelabel status
    # ------------------------------------------------------------------ #
    @grupo_whitelabel.command(name="status", description="Mostra qual bot esta atendendo este servidor")
    @exigir_admin()
    async def whitelabel_status(self, interacao: discord.Interaction) -> None:
        registro = await pegar_bot_proprio(interacao.guild_id)
        rodando = whitelabel.gerenciador.esta_rodando(interacao.guild_id)

        embed = discord.Embed(title="🎨 White-label — status", color=config.COR_LOJA)

        if registro is None:
            embed.add_field(
                name="Modo atual",
                value="**Bot da plataforma** (Opção 1)\nVocê não precisa fazer nada. Para trocar, use `/whitelabel ativar`.",
                inline=False,
            )
        else:
            embed.add_field(
                name="Modo atual",
                value="**Seu próprio bot** (Opção 2)",
                inline=False,
            )
            embed.add_field(name="Nome do bot", value=registro.nome_bot or "—", inline=True)
            embed.add_field(
                name="ID da aplicação", value=str(registro.application_id or "—"), inline=True
            )
            embed.add_field(
                name="Hospedagem",
                value="🟢 rodando" if rodando else "🔴 desligado",
                inline=True,
            )
            if registro.erro:
                embed.add_field(name="Último erro", value=registro.erro[:200], inline=False)

        embed.set_footer(text="O token fica guardado apenas no nosso banco de dados.")
        await interacao.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ #
    # /whitelabel remover
    # ------------------------------------------------------------------ #
    @grupo_whitelabel.command(
        name="remover", description="Desconecta o seu bot proprio e volta para o bot da plataforma"
    )
    @exigir_admin()
    async def whitelabel_remover(self, interacao: discord.Interaction) -> None:
        await interacao.response.defer(ephemeral=True)

        desligado = await whitelabel.gerenciador.parar(interacao.guild_id)
        removido = await remover_bot_proprio(interacao.guild_id)

        if not desligado and not removido:
            await interacao.followup.send(
                "Este servidor não tem nenhum bot próprio conectado.", ephemeral=True
            )
            return

        await interacao.followup.send(
            "✅ Bot próprio desconectado. Este servidor voltou a usar o **bot da plataforma**.\n"
            "_Você pode apagar a sua aplicação no Discord, se quiser._",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(WhiteLabel(bot))
