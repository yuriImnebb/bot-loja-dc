"""Tarefas automaticas que cada loja pode ativar ou desativar."""

from __future__ import annotations

import logging
import random
from datetime import timedelta

import discord
from discord.ext import commands, tasks
from sqlalchemy import select, update

import config
from db import Session, alternar_automacao, obter_config_automacao, pegar_loja
from models import (
    STATUS_CANCELADO,
    STATUS_PENDENTE,
    ConfigAutomacao,
    Giveaway,
    GiveawayParticipant,
    Pedido,
    agora,
)
from utils import eh_admin

log = logging.getLogger(__name__)

AUTOMACOES = (
    ("expirar_pedidos", "Cancelar pedidos pendentes apos 24 horas"),
    ("finalizar_sorteios", "Finalizar sorteios na data definida"),
    ("alertar_estoque_baixo", "Avisar quando restarem 5 unidades ou menos"),
    ("boas_vindas", "Enviar a mensagem de boas-vindas configurada"),
)


async def montar_embed_automacoes(guild_id: int) -> discord.Embed:
    configuracao = await obter_config_automacao(guild_id)
    embed = discord.Embed(
        title="Automações da loja",
        description=(
            "Escolha quais tarefas o bot executa por você.\n"
            "Clique em um botão para ativar ou pausar cada recurso. "
            "Sorteios só são anunciados automaticamente quando essa opção está ativa."
        ),
        color=config.COR_PREMIUM,
    )
    for campo, descricao in AUTOMACOES:
        ligada = getattr(configuracao, campo)
        estado = "Ativa" if ligada else "Desativada"
        embed.add_field(name=descricao, value=estado, inline=True)
    embed.set_footer(text="Novas automações começam desativadas")
    return embed


class PainelAutomacaoView(discord.ui.View):
    def __init__(self, guild_id: int) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id

    async def _alternar(self, interacao: discord.Interaction, nome: str) -> None:
        if interacao.guild_id != self.guild_id or not eh_admin(interacao):
            await interacao.response.send_message(
                "Somente administradores desta loja podem alterar as automacoes.",
                ephemeral=True,
            )
            return

        await alternar_automacao(self.guild_id, nome)
        await interacao.response.edit_message(
            embed=await montar_embed_automacoes(self.guild_id),
            view=self,
        )

    @discord.ui.button(label="◦  Pedidos", style=discord.ButtonStyle.secondary)
    async def pedidos(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        await self._alternar(interacao, "expirar_pedidos")

    @discord.ui.button(label="◇  Sorteios", style=discord.ButtonStyle.secondary)
    async def sorteios(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        await self._alternar(interacao, "finalizar_sorteios")

    @discord.ui.button(label="◦  Estoque baixo", style=discord.ButtonStyle.secondary)
    async def estoque(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        await self._alternar(interacao, "alertar_estoque_baixo")

    @discord.ui.button(label="⌁  Boas-vindas", style=discord.ButtonStyle.secondary)
    async def boas_vindas(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        await self._alternar(interacao, "boas_vindas")

    @discord.ui.button(label="‹  Painel principal", style=discord.ButtonStyle.secondary, row=1)
    async def voltar(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        if interacao.guild_id != self.guild_id or not eh_admin(interacao):
            await interacao.response.send_message(
                "Somente administradores desta loja podem usar este painel.",
                ephemeral=True,
            )
            return

        from views import PainelAdminView, montar_painel_admin

        async with Session() as sessao:
            loja = await pegar_loja(sessao, self.guild_id)
        await interacao.response.edit_message(
            embed=montar_painel_admin(loja),
            view=PainelAdminView(loja),
        )


class AutomacaoCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.limpar_pedidos.start()
        self.processar_sorteios.start()

    def cog_unload(self) -> None:
        self.limpar_pedidos.cancel()
        self.processar_sorteios.cancel()

    @tasks.loop(minutes=30)
    async def limpar_pedidos(self) -> None:
        limite = agora() - timedelta(hours=24)
        async with Session() as sessao:
            guilds_ativos = select(ConfigAutomacao.guild_id).where(
                ConfigAutomacao.expirar_pedidos.is_(True)
            )
            resultado = await sessao.execute(
                update(Pedido)
                .where(
                    Pedido.guild_id.in_(guilds_ativos),
                    Pedido.status == STATUS_PENDENTE,
                    Pedido.criado_em < limite,
                )
                .values(
                    status=STATUS_CANCELADO,
                )
            )
            await sessao.commit()
            quantidade = resultado.rowcount or 0

        if quantidade:
            log.info("Automacao: %d pedido(s) expirado(s) cancelado(s).", quantidade)

    @limpar_pedidos.before_loop
    async def aguardar_bot(self) -> None:
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1)
    async def processar_sorteios(self) -> None:
        async with Session() as sessao:
            guilds_ativos = select(ConfigAutomacao.guild_id).where(
                ConfigAutomacao.finalizar_sorteios.is_(True)
            )
            sorteios = list(
                (
                    await sessao.scalars(
                        select(Giveaway).where(
                            Giveaway.guild_id.in_(guilds_ativos),
                            Giveaway.finalizado.is_(False),
                            Giveaway.termina_em <= agora(),
                        )
                    )
                ).all()
            )

            for sorteio in sorteios:
                canal = self.bot.get_channel(sorteio.canal_id)
                if canal is None:
                    try:
                        canal = await self.bot.fetch_channel(sorteio.canal_id)
                    except discord.HTTPException as erro:
                        log.warning(
                            "Nao consegui buscar o canal do sorteio %s: %s",
                            sorteio.id,
                            erro,
                        )
                        continue

                participantes = list(
                    (
                        await sessao.scalars(
                            select(GiveawayParticipant.usuario_id).where(
                                GiveawayParticipant.giveaway_id == sorteio.id
                            )
                        )
                    ).all()
                )
                if not participantes:
                    mensagem = f"❌ O sorteio de **{sorteio.premio}** terminou, mas nao houve participantes."
                    embed = None
                else:
                    vencedores = random.sample(
                        participantes,
                        min(len(participantes), sorteio.vencedores_qtd),
                    )
                    mencoes = ", ".join(f"<@{usuario_id}>" for usuario_id in vencedores)
                    mensagem = mencoes
                    embed = discord.Embed(
                        title="SORTEIO FINALIZADO",
                        description=f"Prêmio: **{sorteio.premio}**\n\nVENCEDOR(ES)  •  {mencoes}",
                        color=0x00FF00,
                    )

                try:
                    await canal.send(content=mensagem, embed=embed)
                except discord.HTTPException as erro:
                    log.warning(
                        "Nao consegui publicar o resultado do sorteio %s: %s",
                        sorteio.id,
                        erro,
                    )
                    continue

                sorteio.finalizado = True

            if sorteios:
                await sessao.commit()

    @processar_sorteios.before_loop
    async def aguardar_bot_para_sorteios(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutomacaoCog(bot))
