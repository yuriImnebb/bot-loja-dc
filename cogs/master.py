"""Camada master: comandos do dono da plataforma (voce).

So quem estiver em OWNER_IDS no .env consegue usar. Serve para ver todas as
lojas hospedadas, mudar o plano de cada cliente e bloquear/liberar o acesso.
"""

from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select

import config
from db import (
    Session,
    conceder_cortesia,
    definir_bloqueio,
    definir_plano,
    em_cortesia,
    limpar_configuracao_loja,
    listar_cortesias,
    listar_licencas,
    pegar_licenca_detalhada,
    pegar_loja,
    remover_cortesia,
)
from models import (
    PLANO_BLOQUEADO,
    PLANO_FREE,
    PLANO_MEDIA,
    PLANO_PRO,
    PLANOS_VENDAVEIS,
    ROTULOS_DE_PLANO,
    Categoria,
    ItemEstoque,
    Pedido,
    Produto,
)
from utils import exigir_dono
from views import aplicar_icones_da_loja

log = logging.getLogger(__name__)


def _rotulo(plano: str) -> str:
    return ROTULOS_DE_PLANO.get(plano, plano)


class ConfirmarLimpezaLojaView(discord.ui.View):
    """Confirma a limpeza da configuracao antes de mexer no banco."""

    def __init__(self, guild_id: int, nome_servidor: str) -> None:
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.nome_servidor = nome_servidor

    @discord.ui.button(label="Sim, limpar", style=discord.ButtonStyle.danger, emoji="🧹")
    async def confirmar(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        loja = await limpar_configuracao_loja(self.guild_id)
        if loja is None:
            await interacao.response.edit_message(
                content="Esse servidor nao tem loja configurada no banco.", view=None
            )
            return

        await interacao.response.edit_message(
            content=(
                f"🧹 Configuracao da loja de **{self.nome_servidor}** zerada.\n"
                "_Nome, chave PIX e canais voltaram ao padrao. "
                "Categorias, produtos, estoque e pedidos continuam intactos._"
            ),
            view=None,
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        await interacao.response.edit_message(content="Operacao cancelada.", view=None)


class ConfirmarSaidaView(discord.ui.View):
    """Confirma a saida do bot antes de abandonar o servidor."""

    def __init__(self, guild_id: int, nome_servidor: str) -> None:
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.nome_servidor = nome_servidor

    @discord.ui.button(label="Sim, tirar o bot", style=discord.ButtonStyle.danger, emoji="🚪")
    async def confirmar(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        guild = interacao.client.get_guild(self.guild_id)
        if guild is None:
            await interacao.response.edit_message(
                content="O bot ja nao esta nesse servidor.", view=None
            )
            return

        try:
            await guild.leave()
        except discord.HTTPException:
            log.warning("Nao consegui sair do servidor %s.", self.guild_id)
            await interacao.response.edit_message(
                content="Nao consegui tirar o bot agora. Tente de novo em instantes.", view=None
            )
            return

        await interacao.response.edit_message(
            content=(
                f"🚪 Bot removido do servidor **{self.nome_servidor}**.\n"
                "_Se quiser, use `/master limpar-loja` para zerar a configuracao "
                "que ficou guardada no banco._"
            ),
            view=None,
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        await interacao.response.edit_message(content="Operacao cancelada.", view=None)


class Master(commands.Cog):
    """Administracao da plataforma inteira."""

    grupo_master = app_commands.Group(
        name="master",
        description="Administracao da plataforma (somente o dono do bot)",
        default_permissions=discord.Permissions(administrator=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ #
    # /master lojas
    # ------------------------------------------------------------------ #
    @grupo_master.command(name="lojas", description="Lista todas as lojas hospedadas e seus planos")
    @exigir_dono()
    async def master_lojas(self, interacao: discord.Interaction) -> None:
        licencas = await listar_licencas()
        if not licencas:
            await interacao.response.send_message(
                "Nenhuma loja usando o bot ainda.", ephemeral=True
            )
            return

        embed = discord.Embed(
            title=f"🏢 Lojas hospedadas ({len(licencas)})",
            color=config.COR_LOJA,
        )
        linhas = []
        for licenca, nome_loja in licencas[:25]:
            plano = licenca.plano_efetivo()
            marca = "🔴" if plano == PLANO_BLOQUEADO else "🟢"
            linhas.append(
                f"{marca} `{licenca.guild_id}` — {nome_loja or 'sem loja configurada'} — "
                f"**{_rotulo(plano)}**"
            )
        embed.description = "\n".join(linhas)
        if len(licencas) > 25:
            embed.set_footer(text=f"Mostrando 25 de {len(licencas)} lojas.")
        else:
            embed.set_footer(text="Use /master consultar para ver os detalhes de uma loja.")
        await interacao.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ #
    # /master consultar
    # ------------------------------------------------------------------ #
    @grupo_master.command(name="consultar", description="Mostra os detalhes de uma loja")
    @app_commands.describe(guild_id="ID do servidor do cliente")
    @exigir_dono()
    async def master_consultar(self, interacao: discord.Interaction, guild_id: str) -> None:
        if not guild_id.isdigit():
            await interacao.response.send_message("Informe o ID numerico do servidor.", ephemeral=True)
            return

        alvo = int(guild_id)
        async with Session() as sessao:
            loja = await pegar_loja(sessao, alvo)
            produtos = await sessao.scalar(
                select(func.count()).select_from(Produto).where(Produto.guild_id == alvo)
            )
            categorias = await sessao.scalar(
                select(func.count()).select_from(Categoria).where(Categoria.guild_id == alvo)
            )
            pedidos = await sessao.scalar(
                select(func.count()).select_from(Pedido).where(Pedido.guild_id == alvo)
            )
            estoque = await sessao.scalar(
                select(func.count())
                .select_from(ItemEstoque)
                .where(ItemEstoque.guild_id == alvo, ItemEstoque.entregue.is_(False))
            )

        plano, ativo, expira_em = await pegar_licenca_detalhada(alvo)
        async with Session() as sessao:
            cortesia = await em_cortesia(sessao, alvo)

        embed = discord.Embed(
            title=f"🔎 Loja {loja.nome}",
            description=f"Servidor `{alvo}`",
            color=config.COR_LOJA,
        )
        embed.add_field(name="Plano", value=f"**{_rotulo(plano)}**", inline=True)
        embed.add_field(name="Ativa", value="✅ sim" if ativo else "🔴 bloqueada", inline=True)
        embed.add_field(
            name="Expira em",
            value=expira_em.strftime("%d/%m/%Y") if expira_em else "sem validade",
            inline=True,
        )
        if cortesia:
            embed.add_field(name="Cortesia", value="🎁 liberado de graca", inline=False)
        embed.add_field(name="Chave PIX", value="configurada" if loja.pix_chave else "❌ falta", inline=True)
        embed.add_field(name="Categorias", value=str(categorias or 0), inline=True)
        embed.add_field(name="Produtos", value=str(produtos or 0), inline=True)
        embed.add_field(name="Pedidos", value=str(pedidos or 0), inline=True)
        embed.add_field(name="Estoque disponivel", value=str(estoque or 0), inline=True)
        embed.set_footer(text="Use /master definir, /master bloquear ou /master liberar.")

        await interacao.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ #
    # /master definir
    # ------------------------------------------------------------------ #
    @grupo_master.command(name="definir", description="Define o plano de um servidor")
    @app_commands.describe(
        guild_id="ID do servidor do cliente",
        plano="Plano que o cliente contratou",
        dias="Quantos dias de validade (vazio = sem expiracao)",
        observacao="Anotacao interna, ex: nome do cliente",
    )
    @app_commands.choices(
        plano=[
            app_commands.Choice(name="Free", value=PLANO_FREE),
            app_commands.Choice(name="Media (estoque + entrega automatica)", value=PLANO_MEDIA),
            app_commands.Choice(name="Pro (media + white-label)", value=PLANO_PRO),
        ]
    )
    @exigir_dono()
    async def master_definir(
        self,
        interacao: discord.Interaction,
        guild_id: str,
        plano: app_commands.Choice[str],
        dias: Optional[int] = None,
        observacao: Optional[str] = None,
    ) -> None:
        if not guild_id.isdigit():
            await interacao.response.send_message("Informe o ID numerico do servidor.", ephemeral=True)
            return
        if plano.value not in PLANOS_VENDAVEIS:
            await interacao.response.send_message("Plano invalido.", ephemeral=True)
            return
        if dias is not None and dias <= 0:
            await interacao.response.send_message("Os dias precisam ser maiores que zero.", ephemeral=True)
            return

        licenca = await definir_plano(int(guild_id), plano.value, dias, observacao)
        validade = (
            licenca.expira_em.strftime("%d/%m/%Y") if licenca.expira_em else "sem validade"
        )

        await interacao.response.send_message(
            f"✅ Servidor `{guild_id}` agora esta no plano **{_rotulo(plano.value)}** "
            f"(validade: {validade}).",
            ephemeral=True,
        )
        await self._avisar_cliente(int(guild_id), plano.value, validade)

    # ------------------------------------------------------------------ #
    # /master bloquear e /master liberar
    # ------------------------------------------------------------------ #
    @grupo_master.command(name="bloquear", description="Desliga a loja de um servidor (inadimplencia)")
    @app_commands.describe(guild_id="ID do servidor do cliente")
    @exigir_dono()
    async def master_bloquear(self, interacao: discord.Interaction, guild_id: str) -> None:
        if not guild_id.isdigit():
            await interacao.response.send_message("Informe o ID numerico do servidor.", ephemeral=True)
            return

        await definir_bloqueio(int(guild_id), True)
        await interacao.response.send_message(
            f"🔒 A loja do servidor `{guild_id}` foi bloqueada.", ephemeral=True
        )

    @grupo_master.command(name="liberar", description="Religa a loja de um servidor")
    @app_commands.describe(guild_id="ID do servidor do cliente")
    @exigir_dono()
    async def master_liberar(self, interacao: discord.Interaction, guild_id: str) -> None:
        if not guild_id.isdigit():
            await interacao.response.send_message("Informe o ID numerico do servidor.", ephemeral=True)
            return

        await definir_bloqueio(int(guild_id), False)
        await interacao.response.send_message(
            f"🔓 A loja do servidor `{guild_id}` foi liberada novamente.", ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # Cortesia: liberar de graca para amigos
    # ------------------------------------------------------------------ #
    @grupo_master.command(
        name="cortesia",
        description="Libera o bot de graca (plano Pro, sem prazo) para um servidor",
    )
    @app_commands.describe(
        guild_id="ID do servidor do seu amigo",
        motivo="Anotacao opcional, ex: nome do amigo",
    )
    @exigir_dono()
    async def master_cortesia(
        self, interacao: discord.Interaction, guild_id: str, motivo: Optional[str] = None
    ) -> None:
        if not guild_id.isdigit():
            await interacao.response.send_message("Informe o ID numerico do servidor.", ephemeral=True)
            return

        alvo = int(guild_id)
        await conceder_cortesia(alvo, interacao.user.id, motivo)
        await interacao.response.send_message(
            f"🎁 Servidor `{alvo}` liberado **de graca** no plano **Pro** (sem prazo).\n"
            "Use `/master cortesia-remover` quando quiser tirar.",
            ephemeral=True,
        )
        await self._avisar_cliente(alvo, PLANO_PRO, "cortesia — sem prazo")

    @grupo_master.command(name="cortesia-remover", description="Tira a cortesia (volta a valer a licenca normal)")
    @app_commands.describe(guild_id="ID do servidor")
    @exigir_dono()
    async def master_cortesia_remover(
        self, interacao: discord.Interaction, guild_id: str
    ) -> None:
        if not guild_id.isdigit():
            await interacao.response.send_message("Informe o ID numerico do servidor.", ephemeral=True)
            return

        if await remover_cortesia(int(guild_id)):
            await interacao.response.send_message(
                f"✅ Cortesia do servidor `{guild_id}` removida.", ephemeral=True
            )
        else:
            await interacao.response.send_message(
                f"Este servidor (`{guild_id}`) nao estava na lista de cortesia do banco.\n"
                "_Se ele esta no `.env` (CORTESIA_GUILD_IDS), remova de la e reinicie o bot._",
                ephemeral=True,
            )

    @grupo_master.command(name="cortesias", description="Lista os servidores liberados de graca")
    @exigir_dono()
    async def master_cortesias(self, interacao: discord.Interaction) -> None:
        do_banco = await listar_cortesias()
        do_env = config.CORTESIA_GUILD_IDS

        if not do_banco and not do_env:
            await interacao.response.send_message(
                "Nenhum servidor em cortesia.\nUse `/master cortesia <guild_id>` para liberar de graca.",
                ephemeral=True,
            )
            return

        linhas = [f"• `{c.guild_id}` — {c.motivo or 'sem motivo'}" for c in do_banco[:25]]
        if do_env:
            linhas.extend(f"• `{gid}` — do .env (CORTESIA_GUILD_IDS)" for gid in do_env[:25])

        embed = discord.Embed(
            title=f"🎁 Servidores em cortesia ({len(do_banco) + len(do_env)})",
            description="\n".join(linhas),
            color=config.COR_LOJA,
        )
        embed.set_footer(text="Esses servidores usam o bot de graca no plano Pro.")
        await interacao.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ #
    # /master banco: baixa o arquivo .db
    # ------------------------------------------------------------------ #
    @grupo_master.command(name="banco", description="Baixa o arquivo do banco de dados (somente dono)")
    @exigir_dono()
    async def master_banco(self, interacao: discord.Interaction) -> None:
        caminho_db = config.BASE_DIR / "data" / "lojas.db"
        
        if not caminho_db.exists():
            await interacao.response.send_message("O arquivo do banco nao foi encontrado.", ephemeral=True)
            return

        await interacao.response.send_message(
            "📦 Aqui esta o backup do banco de dados:",
            file=discord.File(str(caminho_db), filename="lojas.db"),
            ephemeral=True
        )

    # ------------------------------------------------------------------ #
    # /master limpar-loja: zera a configuracao de uma loja
    # ------------------------------------------------------------------ #
    async def _sugerir_servidores(
        self, interacao: discord.Interaction, valor_atual: str
    ) -> list[app_commands.Choice[str]]:
        """Lista os servidores pelo nome, para nao precisar do ID na mao."""
        termo = (valor_atual or "").lower()
        vistos: set[int] = set()
        escolhas: list[app_commands.Choice[str]] = []

        for guild in self.bot.guilds:
            if termo and termo not in guild.name.lower():
                continue
            vistos.add(guild.id)
            escolhas.append(
                app_commands.Choice(
                    name=f"{guild.name[:70]} ({guild.id})", value=str(guild.id)
                )
            )

        # Lojas que ja existem no banco, inclusive de servidores de onde o bot saiu.
        for licenca, nome_loja in await listar_licencas():
            if licenca.guild_id in vistos:
                continue
            rotulo = nome_loja or "loja sem nome"
            if termo and termo not in rotulo.lower():
                continue
            escolhas.append(
                app_commands.Choice(
                    name=f"{rotulo[:70]} ({licenca.guild_id})", value=str(licenca.guild_id)
                )
            )

        return escolhas[:25]

    @grupo_master.command(
        name="limpar-loja",
        description="Zera a configuracao de uma loja (nome, chave PIX e canais)",
    )
    @app_commands.describe(servidor="Servidor da loja (escolha pelo nome)")
    @app_commands.autocomplete(servidor=_sugerir_servidores)
    @exigir_dono()
    async def master_limpar_loja(
        self, interacao: discord.Interaction, servidor: str
    ) -> None:
        if not servidor.isdigit():
            await interacao.response.send_message(
                "Escolha o servidor na lista que aparece ao digitar.", ephemeral=True
            )
            return

        alvo = int(servidor)
        guild = self.bot.get_guild(alvo)
        nome_servidor = guild.name if guild else f"servidor {alvo}"

        await interacao.response.send_message(
            f"Tem certeza que quer **zerar a configuracao** da loja de "
            f"**{nome_servidor}**?\n"
            "_Nome, chave PIX e canais de venda/logs voltam ao padrao. "
            "Categorias, produtos, estoque e pedidos **nao** sao apagados._",
            view=await aplicar_icones_da_loja(
                alvo,
                ConfirmarLimpezaLojaView(alvo, nome_servidor),
            ),
            ephemeral=True,
        )

    # ------------------------------------------------------------------ #
    # /master sair-do-servidor: tira o bot de um servidor
    # ------------------------------------------------------------------ #
    async def _sugerir_guilds_do_bot(
        self, interacao: discord.Interaction, valor_atual: str
    ) -> list[app_commands.Choice[str]]:
        """Servidores onde o bot esta agora (so esses dao para sair)."""
        termo = (valor_atual or "").lower()
        escolhas = [
            app_commands.Choice(
                name=f"{guild.name[:70]} ({guild.id})", value=str(guild.id)
            )
            for guild in self.bot.guilds
            if not termo or termo in guild.name.lower()
        ]
        return escolhas[:25]

    @grupo_master.command(
        name="sair-do-servidor",
        description="Tira o bot de um servidor (somente o dono)",
    )
    @app_commands.describe(servidor="Servidor de onde o bot deve sair (escolha pelo nome)")
    @app_commands.autocomplete(servidor=_sugerir_guilds_do_bot)
    @exigir_dono()
    async def master_sair_do_servidor(
        self, interacao: discord.Interaction, servidor: str
    ) -> None:
        if not servidor.isdigit():
            await interacao.response.send_message(
                "Escolha o servidor na lista que aparece ao digitar.", ephemeral=True
            )
            return

        guild = self.bot.get_guild(int(servidor))
        if guild is None:
            await interacao.response.send_message(
                "O bot nao esta nesse servidor.", ephemeral=True
            )
            return

        await interacao.response.send_message(
            f"Tem certeza que quer **tirar o bot** do servidor **{guild.name}**?\n"
            "_A loja de la para de funcionar imediatamente. "
            "Isso nao apaga nada do banco._",
            view=await aplicar_icones_da_loja(
                guild.id,
                ConfirmarSaidaView(guild.id, guild.name),
            ),
            ephemeral=True,
        )

    # ------------------------------------------------------------------ #
    # Aviso automatico para o cliente
    # ------------------------------------------------------------------ #
    async def _avisar_cliente(self, guild_id: int, plano: str, validade: str) -> None:
        """Manda um recadinho no canal de logs da loja quando o plano muda."""
        try:
            async with Session() as sessao:
                loja = await pegar_loja(sessao, guild_id)
        except Exception:  # noqa: BLE001 - aviso e opcional, nao pode quebrar o comando
            log.warning("Nao consegui carregar a loja %s para avisar.", guild_id)
            return

        if not loja.canal_logs_id:
            return

        canal = self.bot.get_channel(loja.canal_logs_id)
        if canal is None:
            return

        try:
            await canal.send(
                f"🎉 O plano da loja foi atualizado para **{_rotulo(plano)}** (validade: {validade})."
            )
        except discord.HTTPException:
            log.warning("Nao consegui avisar o cliente %s sobre a mudanca de plano.", guild_id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Master(bot))
