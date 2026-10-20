"""Assinatura self-service: o cliente paga o bot pelo proprio Discord.

O PIX e gerado pelo Mercado Pago com o SEU token, entao o dinheiro cai direto
na sua conta. Quando o pagamento e aprovado, o plano daquele servidor e
liberado na hora (webhook) ou na checagem periodica.
"""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from io import BytesIO

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import cobranca
import mercadopago
import pix
from db import (
    criar_pagamento,
    atualizar_pix_do_pagamento,
    pagamento_pendente_do_guild,
    pegar_licenca_detalhada,
)
from models import PLANO_MEDIA, PLANO_PRO, ROTULOS_DE_PLANO
from utils import formatar_preco

log = logging.getLogger(__name__)

INTERVALO_DE_CHECAGEM = 2  # minutos


def _rotulo(plano: str) -> str:
    return ROTULOS_DE_PLANO.get(plano, plano)


def _descricao_do_plano(plano: str) -> str:
    if plano == PLANO_PRO:
        return (
            "Tudo do Media + **white-label**: use o **seu próprio bot** "
            "(nome e foto seus) **ou** continue com o bot da plataforma, como preferir."
        )
    return "**Entrega automatica** dos produtos (estoque por item, link ou texto)."


def montar_tabela_de_precos() -> discord.Embed:
    embed = discord.Embed(
        title="💎 Assinatura da plataforma",
        description=(
            "Assine para liberar os recursos do bot neste servidor.\n"
            "O pagamento e feito por **PIX** e a liberacao e automatica."
        ),
        color=config.COR_LOJA,
    )
    for plano in (PLANO_MEDIA, PLANO_PRO):
        embed.add_field(
            name=f"{_rotulo(plano)} — {formatar_preco(config.preco_do_plano(plano))}/mes",
            value=_descricao_do_plano(plano),
            inline=False,
        )
    embed.set_footer(text=f"Assinatura valida por {config.PLANO_DIAS} dias. Cancele quando quiser.")
    return embed


class AssinarView(discord.ui.View):
    """Botoes de assinatura (persistente: sobrevive a reinicio do bot)."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Assinar Media",
        emoji="📦",
        style=discord.ButtonStyle.primary,
        custom_id="assinatura:plano:media",
    )
    async def assinar_media(self, interacao: discord.Interaction, _: discord.ui.Button) -> None:
        await iniciar_assinatura(interacao, PLANO_MEDIA)

    @discord.ui.button(
        label="Assinar Pro",
        emoji="👑",
        style=discord.ButtonStyle.success,
        custom_id="assinatura:plano:pro",
    )
    async def assinar_pro(self, interacao: discord.Interaction, _: discord.ui.Button) -> None:
        await iniciar_assinatura(interacao, PLANO_PRO)

    @discord.ui.button(
        label="Ja paguei",
        emoji="🔄",
        style=discord.ButtonStyle.secondary,
        custom_id="assinatura:verificar",
    )
    async def ja_paguei(self, interacao: discord.Interaction, _: discord.ui.Button) -> None:
        await verificar_pagamento(interacao)


async def iniciar_assinatura(interacao: discord.Interaction, plano: str) -> None:
    """Cria a cobranca PIX e mostra o QR Code para o cliente."""
    if interacao.guild is None:
        await interacao.response.send_message("Use isto dentro de um servidor.", ephemeral=True)
        return

    if not mercadopago.configurado():
        await interacao.response.send_message(
            "⚠️ A cobranca ainda nao esta configurada.\n"
            "O dono da plataforma precisa colocar o `MERCADOPAGO_ACCESS_TOKEN` no `.env`.",
            ephemeral=True,
        )
        return

    await interacao.response.defer(ephemeral=True)

    valor = config.preco_do_plano(plano)
    pagamento = await criar_pagamento(
        guild_id=interacao.guild_id,
        solicitante_id=interacao.user.id,
        plano=plano,
        valor_centavos=valor,
        dias=config.PLANO_DIAS,
    )

    try:
        cobranca_mp = await mercadopago.criar_cobranca_pix(
            valor_centavos=valor,
            descricao=f"Assinatura {_rotulo(plano)} - servidor {interacao.guild_id}",
            referencia=f"guild:{interacao.guild_id}:plano:{plano}:pag:{pagamento.id}",
            email_pagador=f"discord{interacao.user.id}@gmail.com",
        )
    except mercadopago.ErroMercadoPago as erro:
        await interacao.followup.send(
            f"⚠️ Nao consegui gerar o PIX agora: {erro}\nTente novamente em instantes.",
            ephemeral=True,
        )
        return

    await atualizar_pix_do_pagamento(
        pagamento.id, cobranca_mp.id, cobranca_mp.copia_cola, cobranca_mp.expira_em
    )

    embed = discord.Embed(
        title="💳 Pague para liberar o plano",
        description=(
            f"Plano: **{_rotulo(plano)}**\n"
            f"Valor: **{formatar_preco(valor)}**\n"
            f"Validade: **{config.PLANO_DIAS} dias**\n\n"
            "Pague o QR Code abaixo com o app do seu banco. "
            "A liberacao e **automatica** — em instantes o plano aparece ativo."
        ),
        color=config.COR_SUCESSO,
    )
    embed.add_field(name="PIX Copia e Cola", value=f"```\n{cobranca_mp.copia_cola}\n```", inline=False)
    embed.set_footer(text="Pagou agora? Clique em Ja paguei. Pode fechar esta mensagem.")

    await interacao.followup.send(
        embed=embed,
        file=_arquivo_qr(cobranca_mp),
        view=AssinarView(),
        ephemeral=True,
    )


def _arquivo_qr(cobranca_mp: mercadopago.CobrancaPix) -> discord.File:
    """Monta o arquivo PNG do QR Code (usa o do MP ou gera a partir do codigo)."""
    if cobranca_mp.qr_base64:
        try:
            dados = base64.b64decode(cobranca_mp.qr_base64)
            return discord.File(BytesIO(dados), filename="pix_assinatura.png")
        except Exception:  # noqa: BLE001 - se falhar, gera o QR localmente
            pass
    return discord.File(pix.gerar_qr_png(cobranca_mp.copia_cola), filename="pix_assinatura.png")


async def verificar_pagamento(interacao: discord.Interaction) -> None:
    """Botao "Ja paguei": consulta o Mercado Pago na hora."""
    if interacao.guild is None:
        await interacao.response.send_message("Use isto dentro de um servidor.", ephemeral=True)
        return

    if not mercadopago.configurado():
        await interacao.response.send_message(
            "⚠️ A cobranca ainda nao esta configurada no bot.", ephemeral=True
        )
        return

    await interacao.response.defer(ephemeral=True)

    pagamento = await pagamento_pendente_do_guild(interacao.guild_id)
    if pagamento is None:
        await interacao.followup.send(
            "Nao encontrei nenhuma cobranca pendente neste servidor.\n"
            "Use `/assinar` para gerar uma nova.",
            ephemeral=True,
        )
        return

    estado = await cobranca.sincronizar_pagamento(interacao.client, pagamento)

    if estado == cobranca.APROVADO:
        mensagem = (
            f"🎉 Pagamento confirmado! O plano **{_rotulo(pagamento.plano)}** "
            f"esta ativo por **{pagamento.dias} dias**."
        )
    elif estado == cobranca.EXPIRADO:
        mensagem = "⌛ Este PIX expirou. Use `/assinar` para gerar um novo."
    elif estado == cobranca.ERRO:
        mensagem = "⚠️ Nao consegui confirmar agora. Tente novamente em alguns instantes."
    elif estado == cobranca.INDISPONIVEL:
        mensagem = "⚠️ A cobranca ainda nao esta configurada no bot."
    else:
        mensagem = (
            "⏳ Ainda nao identifiquei o pagamento.\n"
            "Se voce acabou de pagar, aguarde alguns instantes e clique em **Ja paguei** de novo."
        )

    await interacao.followup.send(mensagem, ephemeral=True)


class Assinatura(commands.Cog):
    """Comandos de assinatura e a checagem automatica de pagamentos."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    async def cog_load(self) -> None:
        self.checar_pagamentos.start()

    def cog_unload(self) -> None:
        self.checar_pagamentos.cancel()

    # ------------------------------------------------------------------ #
    # /assinar
    # ------------------------------------------------------------------ #
    @app_commands.command(name="assinar", description="Assina um plano para liberar os recursos do bot neste servidor")
    @app_commands.guild_only()
    async def assinar(self, interacao: discord.Interaction) -> None:
        if not mercadopago.configurado():
            await interacao.response.send_message(
                "⚠️ A cobranca ainda nao esta configurada.\n"
                "O dono da plataforma precisa colocar o `MERCADOPAGO_ACCESS_TOKEN` no `.env`.",
                ephemeral=True,
            )
            return

        await interacao.response.send_message(
            embed=montar_tabela_de_precos(), view=AssinarView(), ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # /minha-assinatura
    # ------------------------------------------------------------------ #
    @app_commands.command(name="minha-assinatura", description="Mostra o plano atual deste servidor")
    @app_commands.guild_only()
    async def minha_assinatura(self, interacao: discord.Interaction) -> None:
        plano, ativo, expira_em = await pegar_licenca_detalhada(interacao.guild_id)
        embeds_disponiveis = plano in (PLANO_MEDIA, PLANO_PRO)

        embed = discord.Embed(title="💎 Sua assinatura", color=config.COR_LOJA)
        embed.add_field(name="Plano atual", value=f"**{_rotulo(plano)}**", inline=True)
        embed.add_field(name="Situacao", value="✅ ativa" if ativo else "🔴 bloqueada", inline=True)
        embed.add_field(
            name="Renova em",
            value=_formatar_data(expira_em) if expira_em else "sem prazo",
            inline=True,
        )

        pendente = await pagamento_pendente_do_guild(interacao.guild_id)
        view = None
        if pendente is not None:
            embed.add_field(
                name="Cobranca em aberto",
                value=(
                    f"Plano **{_rotulo(pendente.plano)}** — "
                    f"{formatar_preco(pendente.valor_centavos)} (aguardando PIX)"
                ),
                inline=False,
            )
            view = AssinarView()

        embed.set_footer(
            text="Use /assinar para renovar ou mudar de plano."
            if not embeds_disponiveis
            else "Use /assinar para renovar."
        )
        await interacao.response.send_message(embed=embed, view=view, ephemeral=True)

    # ------------------------------------------------------------------ #
    # Checagem periodica (fallback do webhook)
    # ------------------------------------------------------------------ #
    @tasks.loop(minutes=INTERVALO_DE_CHECAGEM)
    async def checar_pagamentos(self) -> None:
        if not mercadopago.configurado():
            return
        try:
            resultado = await cobranca.processar_pendentes(self.bot)
        except Exception:  # noqa: BLE001 - a tarefa nunca pode morrer
            log.exception("Erro ao checar pagamentos pendentes.")
            return
        if resultado:
            log.info("Checagem de pagamentos: %s", resultado)

    @checar_pagamentos.before_loop
    async def _esperar_bot(self) -> None:
        await self.bot.wait_until_ready()


def _formatar_data(momento: object) -> str:
    if isinstance(momento, datetime):
        if momento.tzinfo is None:
            momento = momento.replace(tzinfo=timezone.utc)
        return momento.strftime("%d/%m/%Y")
    return str(momento)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Assinatura(bot))
