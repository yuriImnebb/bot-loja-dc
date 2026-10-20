"""Estoque dos produtos (planos Media e Pro).

O dono da loja cadastra as unidades que serao entregues automaticamente
quando o pagamento for confirmado. Cada unidade pode ser:

- um **link** (jogo, foto, arquivo) — o bot manda o link na DM do comprador;
- um **bloco de texto** (conta + dados em varias linhas) — vai numa caixa de codigo.
"""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select

import config
from db import (
    Session,
    adicionar_estoque,
    contar_estoque,
    contar_estoque_por_produto,
    limpar_estoque,
    listar_estoque,
    plano_do_servidor,
    remover_item_estoque,
)
from models import ITEM_LINK, ITEM_TEXTO, Produto, plano_tem_estoque
from utils import exigir_admin, formatar_preco

LIMITE_DO_TEXTO = 4000


def _resumo(conteudo: str, tamanho: int = 70) -> str:
    """Primeira linha do item, cortada, para caber nos menus."""
    linha = conteudo.strip().splitlines()[0] if conteudo.strip() else ""
    return linha[:tamanho] + ("..." if len(linha) > tamanho else "")


class AdicionarEstoqueModal(discord.ui.Modal):
    def __init__(self, guild_id: int, produto_id: int, produto_nome: str, formato: str, modo: str):
        super().__init__(title=f"Estoque • {produto_nome[:32]}")
        self.guild_id = guild_id
        self.produto_id = produto_id
        self.formato = formato
        self.modo = modo

        if formato == ITEM_LINK:
            rotulo = "Cole o link (um por linha se escolheu por linha)"
            dica = "https://exemplo.com/produto"
        else:
            rotulo = "Cole os dados (login, senha, tudo)"
            dica = "login: fulano\nsenha: 1234\ncodigo: ABC-DEF"

        self.campo = discord.ui.TextInput(
            label=rotulo[:45],
            placeholder=dica[:100],
            style=discord.TextStyle.paragraph,
            max_length=LIMITE_DO_TEXTO,
            required=True,
        )
        self.add_item(self.campo)

    async def on_submit(self, interacao: discord.Interaction) -> None:
        conteudo = self.campo.value.strip()
        if not conteudo:
            await interacao.response.send_message("Voce nao colou nenhum conteudo.", ephemeral=True)
            return

        if self.modo == "por_linha":
            conteudos = [linha.strip() for linha in conteudo.splitlines() if linha.strip()]
        else:
            conteudos = [conteudo]

        adicionados = await adicionar_estoque(self.guild_id, self.produto_id, self.formato, conteudos)
        disponivel = await contar_estoque(self.guild_id, self.produto_id)

        await interacao.response.send_message(
            f"✅ **{adicionados}** unidade(s) adicionada(s) ao estoque.\n"
            f"Agora este produto tem **{disponivel}** unidade(s) disponivel(is).\n"
            "_Quando o pagamento for confirmado, eu entrego uma automaticamente na DM do comprador._",
            ephemeral=True,
        )


class ConfirmarLimpezaView(discord.ui.View):
    def __init__(self, guild_id: int, produto_id: int, produto_nome: str) -> None:
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.produto_id = produto_id
        self.produto_nome = produto_nome

    @discord.ui.button(label="Sim, limpar", style=discord.ButtonStyle.danger, emoji="🗑️")
    async def confirmar(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        removidos = await limpar_estoque(self.guild_id, self.produto_id)
        await interacao.response.edit_message(
            content=f"🗑️ **{removidos}** unidade(s) removida(s) do estoque de **{self.produto_nome}**.",
            view=None,
        )

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        await interacao.response.edit_message(content="Operacao cancelada.", view=None)


class Estoque(commands.Cog):
    """Controle do estoque entregue automaticamente."""

    grupo_estoque = app_commands.Group(
        name="estoque",
        description="Cadastre os itens que serao entregues automaticamente (plano Media+)",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ #
    # Autocomplete
    # ------------------------------------------------------------------ #
    async def sugerir_produtos(
        self, interacao: discord.Interaction, valor_atual: str
    ) -> list[app_commands.Choice[int]]:
        if interacao.guild_id is None:
            return []

        consulta = select(Produto).where(Produto.guild_id == interacao.guild_id)
        if valor_atual:
            consulta = consulta.where(Produto.nome.ilike(f"%{valor_atual}%"))

        async with Session() as sessao:
            produtos = list((await sessao.scalars(consulta.order_by(Produto.nome).limit(25))).all())

        return [
            app_commands.Choice(
                name=f"{produto.nome[:80]} — {formatar_preco(produto.preco_centavos)}",
                value=produto.id,
            )
            for produto in produtos
        ]

    # ------------------------------------------------------------------ #
    # Portao do plano
    # ------------------------------------------------------------------ #
    async def _plano_liberado(self, interacao: discord.Interaction) -> bool:
        plano = await plano_do_servidor(interacao.guild_id)
        if plano_tem_estoque(plano):
            return True

        await interacao.response.send_message(
            embed=discord.Embed(
                title="🔒 Estoque e entrega automatica",
                description=(
                    "O cadastro de estoque faz parte do **plano Media**.\n\n"
                    "**No plano Media ou Pro** o bot entrega sozinho para quem comprou: "
                    "voce cadastra os itens aqui e, quando confirmar o pagamento, eu mando "
                    "o link ou os dados na DM do comprador.\n\n"
                    "Fale com o suporte para fazer o upgrade."
                ),
                color=config.COR_ALERTA,
            ),
            ephemeral=True,
        )
        return False

    # ------------------------------------------------------------------ #
    # /estoque adicionar
    # ------------------------------------------------------------------ #
    @grupo_estoque.command(name="adicionar", description="Adiciona itens ao estoque de um produto")
    @app_commands.describe(
        produto_id="Produto que vai receber o estoque",
        formato="O que o comprador recebe",
        modo="Como dividir o que voce vai colar",
    )
    @app_commands.choices(
        formato=[
            app_commands.Choice(name="Link (jogo, foto, arquivo)", value=ITEM_LINK),
            app_commands.Choice(name="Texto (conta, login e senha)", value=ITEM_TEXTO),
        ],
        modo=[
            app_commands.Choice(name="Tudo junto = 1 unidade", value="unico"),
            app_commands.Choice(name="Cada linha = 1 unidade", value="por_linha"),
        ],
    )
    @app_commands.autocomplete(produto_id=sugerir_produtos)
    @exigir_admin()
    async def estoque_adicionar(
        self,
        interacao: discord.Interaction,
        produto_id: int,
        formato: app_commands.Choice[str],
        modo: app_commands.Choice[str],
    ) -> None:
        if not await self._plano_liberado(interacao):
            return

        async with Session() as sessao:
            produto = await sessao.get(Produto, produto_id)

        if produto is None or produto.guild_id != interacao.guild_id:
            await interacao.response.send_message("Produto nao encontrado.", ephemeral=True)
            return

        await interacao.response.send_modal(
            AdicionarEstoqueModal(
                interacao.guild_id, produto.id, produto.nome, formato.value, modo.value
            )
        )

    # ------------------------------------------------------------------ #
    # /estoque listar
    # ------------------------------------------------------------------ #
    @grupo_estoque.command(name="listar", description="Mostra o estoque disponivel")
    @app_commands.describe(produto_id="Ver os itens de um produto (opcional)")
    @app_commands.autocomplete(produto_id=sugerir_produtos)
    @exigir_admin()
    async def estoque_listar(
        self, interacao: discord.Interaction, produto_id: Optional[int] = None
    ) -> None:
        if not await self._plano_liberado(interacao):
            return

        if produto_id is None:
            await self._listar_resumo(interacao)
            return

        async with Session() as sessao:
            produto = await sessao.get(Produto, produto_id)

        if produto is None or produto.guild_id != interacao.guild_id:
            await interacao.response.send_message("Produto nao encontrado.", ephemeral=True)
            return

        itens = await listar_estoque(interacao.guild_id, produto_id)
        if not itens:
            await interacao.response.send_message(
                f"O estoque de **{produto.nome}** esta vazio.\n"
                "Use `/estoque adicionar` para cadastrar os itens.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title=f"📦 Estoque — {produto.nome}",
            description=f"**{len(itens)}** unidade(s) disponivel(is) (mostrando ate 25).",
            color=config.COR_LOJA,
        )
        linhas = [
            f"`#{item.id}` {'🔗' if item.tipo == ITEM_LINK else '📝'} {_resumo(item.conteudo)}"
            for item in itens
        ]
        embed.description = (embed.description or "") + "\n\n" + "\n".join(linhas)[:3800]
        embed.set_footer(text="Use /estoque remover com o numero do item para apagar uma unidade.")
        await interacao.response.send_message(embed=embed, ephemeral=True)

    async def _listar_resumo(self, interacao: discord.Interaction) -> None:
        async with Session() as sessao:
            produtos = list(
                (
                    await sessao.scalars(
                        select(Produto)
                        .where(Produto.guild_id == interacao.guild_id)
                        .order_by(Produto.nome)
                    )
                ).all()
            )

        if not produtos:
            await interacao.response.send_message(
                "Voce ainda nao cadastrou produtos.\nComece por `/produto criar`.",
                ephemeral=True,
            )
            return

        contagens = await contar_estoque_por_produto(
            interacao.guild_id, [produto.id for produto in produtos]
        )

        embed = discord.Embed(
            title="📦 Estoque da loja",
            description="Unidades disponiveis por produto (as entregues sao descontadas).",
            color=config.COR_LOJA,
        )
        linhas = []
        for produto in produtos[:25]:
            quantidade = contagens.get(produto.id, 0)
            marca = "🟢" if quantidade else "🔴"
            linhas.append(f"{marca} **{produto.nome}** — {quantidade} unidade(s)")
        embed.description = (embed.description or "") + "\n\n" + "\n".join(linhas)[:3800]
        embed.set_footer(text="Use /estoque listar produto_id:<nome> para ver os itens de um produto.")
        await interacao.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ #
    # /estoque remover
    # ------------------------------------------------------------------ #
    @grupo_estoque.command(name="remover", description="Remove um item especifico do estoque")
    @app_commands.describe(item_id="Numero do item (aparece em /estoque listar)")
    @exigir_admin()
    async def estoque_remover(self, interacao: discord.Interaction, item_id: int) -> None:
        if not await self._plano_liberado(interacao):
            return

        removido = await remover_item_estoque(interacao.guild_id, item_id)
        if not removido:
            await interacao.response.send_message(
                "Item nao encontrado no estoque desta loja.", ephemeral=True
            )
            return

        await interacao.response.send_message(f"🗑️ Item `#{item_id}` removido do estoque.", ephemeral=True)

    # ------------------------------------------------------------------ #
    # /estoque limpar
    # ------------------------------------------------------------------ #
    @grupo_estoque.command(name="limpar", description="Apaga todas as unidades ainda disponiveis de um produto")
    @app_commands.describe(produto_id="Produto que sera esvaziado")
    @app_commands.autocomplete(produto_id=sugerir_produtos)
    @exigir_admin()
    async def estoque_limpar(self, interacao: discord.Interaction, produto_id: int) -> None:
        if not await self._plano_liberado(interacao):
            return

        async with Session() as sessao:
            produto = await sessao.get(Produto, produto_id)

        if produto is None or produto.guild_id != interacao.guild_id:
            await interacao.response.send_message("Produto nao encontrado.", ephemeral=True)
            return

        disponivel = await contar_estoque(interacao.guild_id, produto.id)
        if not disponivel:
            await interacao.response.send_message(
                f"O estoque de **{produto.nome}** ja esta vazio.", ephemeral=True
            )
            return

        await interacao.response.send_message(
            f"Tem certeza que quer apagar **{disponivel}** unidade(s) de **{produto.nome}**?\n"
            "_Itens ja entregues nao sao afetados._",
            view=ConfirmarLimpezaView(interacao.guild_id, produto.id, produto.nome),
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Estoque(bot))
