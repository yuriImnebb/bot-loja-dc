"""Funcoes pequenas compartilhadas entre os cogs."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

import discord
from discord import app_commands

import config


def formatar_preco(centavos: int) -> str:
    """1990 -> 'R$ 19,90'"""
    return f"R$ {centavos / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def parse_preco(bruto: str) -> int:
    """Aceita '19,90', '19.90', 'R$ 19,90' e devolve o valor em centavos."""
    texto = bruto.replace("R$", "").replace("r$", "").replace(" ", "").strip()
    if not texto:
        raise ValueError("Informe um preco. Exemplo: `19,90`")
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")

    try:
        valor = Decimal(texto)
    except InvalidOperation as erro:
        raise ValueError("Preco invalido. Use algo como `19,90`.") from erro

    if valor < Decimal("1.00"):
        raise ValueError("O preco minimo para venda e R$ 1,00 (limite da maioria dos bancos para PIX).")

    return int((valor * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def eh_admin(interacao: discord.Interaction) -> bool:
    """True se quem interagiu pode gerenciar o servidor."""
    if interacao.guild is None:
        return False
    return bool(interacao.permissions.manage_guild)


def exigir_admin():
    """Check reaproveitado pelos comandos de administracao da loja."""

    async def verificar(interacao: discord.Interaction) -> bool:
        if not eh_admin(interacao):
            raise app_commands.CheckFailure(
                "Voce precisa da permissao **Gerenciar Servidor** para usar este comando."
            )
        return True

    return app_commands.check(verificar)


def eh_dono(interacao: discord.Interaction) -> bool:
    """True se quem interagiu e o dono da plataforma (voce)."""
    return interacao.user.id in config.OWNER_IDS


def exigir_dono():
    """Check dos comandos master, que so o dono da plataforma pode usar."""

    async def verificar(interacao: discord.Interaction) -> bool:
        if not eh_dono(interacao):
            raise app_commands.CheckFailure("Este comando e exclusivo do dono da plataforma.")
        return True

    return app_commands.check(verificar)
