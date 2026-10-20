"""Comandos de catalogo: /categoria e /produto."""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import select, update

import config
from db import Session, pegar_loja
from models import Categoria, Produto
from utils import exigir_admin, formatar_preco, parse_preco


class Catalogo(commands.Cog):
    """Montagem do catalogo da loja (categorias e produtos)."""

    grupo_categoria = app_commands.Group(
        name="categoria",
        description="Crie e organize as categorias da sua loja",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )
    grupo_produto = app_commands.Group(
        name="produto",
        description="Cadastre e gerencie os produtos da sua loja",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ #
    # Autocomplete
    # ------------------------------------------------------------------ #
    async def sugerir_categorias(
        self, interacao: discord.Interaction, valor_atual: str
    ) -> list[app_commands.Choice[int]]:
        if interacao.guild_id is None:
            return []

        consulta = select(Categoria).where(Categoria.guild_id == interacao.guild_id)
        if valor_atual:
            consulta = consulta.where(Categoria.nome.ilike(f"%{valor_atual}%"))

        async with Session() as sessao:
            categorias = list((await sessao.scalars(consulta.order_by(Categoria.nome).limit(25))).all())

        return [app_commands.Choice(name=categoria.nome[:100], value=categoria.id) for categoria in categorias]

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
                name=f"{produto.nome[:80]} — {formatar_preco(produto.preco_centavos)}"
                + ("" if produto.ativo else " (desativado)"),
                value=produto.id,
            )
            for produto in produtos
        ]

    # ------------------------------------------------------------------ #
    # /categoria
    # ------------------------------------------------------------------ #
    @grupo_categoria.command(name="criar", description="Cria uma categoria")
    @app_commands.describe(nome="Nome da categoria (ex: Camisas, Assinaturas, Gift Cards)")
    @exigir_admin()
    async def categoria_criar(self, interacao: discord.Interaction, nome: str) -> None:
        nome = nome.strip()
        if len(nome) < 2:
            await interacao.response.send_message("O nome da categoria e muito curto.", ephemeral=True)
            return

        async with Session() as sessao:
            await pegar_loja(sessao, interacao.guild_id)
            existente = await sessao.scalar(
                select(Categoria).where(
                    Categoria.guild_id == interacao.guild_id,
                    Categoria.nome.ilike(nome),
                )
            )
            if existente is not None:
                await interacao.response.send_message(
                    f"Ja existe uma categoria chamada **{existente.nome}**.", ephemeral=True
                )
                return

            categoria = Categoria(guild_id=interacao.guild_id, nome=nome[:80])
            sessao.add(categoria)
            await sessao.commit()
            await sessao.refresh(categoria)

        await interacao.response.send_message(
            f"✅ Categoria **{categoria.nome}** criada.\n"
            "Agora use `/produto criar` e escolha ela no campo `categoria`.",
            ephemeral=True,
        )

    @grupo_categoria.command(name="listar", description="Lista as categorias da loja")
    @exigir_admin()
    async def categoria_listar(self, interacao: discord.Interaction) -> None:
        async with Session() as sessao:
            categorias = list(
                (
                    await sessao.scalars(
                        select(Categoria)
                        .where(Categoria.guild_id == interacao.guild_id)
                        .order_by(Categoria.ordem, Categoria.nome)
                    )
                ).all()
            )
            produtos = list(
                (await sessao.scalars(select(Produto).where(Produto.guild_id == interacao.guild_id))).all()
            )

        if not categorias:
            await interacao.response.send_message(
                "Voce ainda nao criou nenhuma categoria.\nUse `/categoria criar` — exemplo: nome `Assinaturas`.",
                ephemeral=True,
            )
            return

        contagem: dict[int, int] = {}
        for produto in produtos:
            if produto.categoria_id is not None:
                contagem[produto.categoria_id] = contagem.get(produto.categoria_id, 0) + 1

        embed = discord.Embed(title="📂 Categorias da loja", color=config.COR_LOJA)
        for categoria in categorias[:25]:
            embed.add_field(
                name=categoria.nome,
                value=f"{contagem.get(categoria.id, 0)} produto(s)",
                inline=True,
            )
        embed.set_footer(text="Use /categoria renomear ou /categoria remover (o campo sugere as opcoes).")
        await interacao.response.send_message(embed=embed, ephemeral=True)

    @grupo_categoria.command(name="renomear", description="Renomeia uma categoria")
    @app_commands.describe(categoria_id="Categoria que sera renomeada", novo_nome="Novo nome")
    @app_commands.autocomplete(categoria_id=sugerir_categorias)
    @exigir_admin()
    async def categoria_renomear(
        self, interacao: discord.Interaction, categoria_id: int, novo_nome: str
    ) -> None:
        async with Session() as sessao:
            categoria = await sessao.get(Categoria, categoria_id)
            if categoria is None or categoria.guild_id != interacao.guild_id:
                await interacao.response.send_message("Categoria nao encontrada.", ephemeral=True)
                return
            categoria.nome = novo_nome.strip()[:80]
            await sessao.commit()

        await interacao.response.send_message(f"✅ Categoria renomeada para **{novo_nome.strip()}**.", ephemeral=True)

    @grupo_categoria.command(name="remover", description="Remove uma categoria")
    @app_commands.describe(categoria_id="Categoria que sera removida")
    @app_commands.autocomplete(categoria_id=sugerir_categorias)
    @exigir_admin()
    async def categoria_remover(self, interacao: discord.Interaction, categoria_id: int) -> None:
        async with Session() as sessao:
            categoria = await sessao.get(Categoria, categoria_id)
            if categoria is None or categoria.guild_id != interacao.guild_id:
                await interacao.response.send_message("Categoria nao encontrada.", ephemeral=True)
                return

            nome = categoria.nome
            await sessao.execute(
                update(Produto)
                .where(Produto.guild_id == interacao.guild_id, Produto.categoria_id == categoria_id)
                .values(categoria_id=None)
            )
            await sessao.delete(categoria)
            await sessao.commit()

        await interacao.response.send_message(
            f"🗑️ Categoria **{nome}** removida. Os produtos dela continuam ativos, agora em **Outros**.",
            ephemeral=True,
        )

    # ------------------------------------------------------------------ #
    # /produto
    # ------------------------------------------------------------------ #
    @grupo_produto.command(name="criar", description="Cadastra um produto")
    @app_commands.describe(
        nome="Nome do produto",
        preco="Preco em reais, exemplo: 19,90",
        categoria_id="Categoria do produto (opcional)",
        descricao="Texto que o comprador vai ler antes de pagar (opcional)",
    )
    @app_commands.autocomplete(categoria_id=sugerir_categorias)
    @exigir_admin()
    async def produto_criar(
        self,
        interacao: discord.Interaction,
        nome: str,
        preco: str,
        categoria_id: Optional[int] = None,
        descricao: Optional[str] = None,
    ) -> None:
        nome = nome.strip()
        if len(nome) < 2:
            await interacao.response.send_message("O nome do produto e muito curto.", ephemeral=True)
            return

        try:
            centavos = parse_preco(preco)
        except ValueError as erro:
            await interacao.response.send_message(f"⚠️ {erro}", ephemeral=True)
            return

        async with Session() as sessao:
            await pegar_loja(sessao, interacao.guild_id)

            if categoria_id is not None:
                categoria = await sessao.get(Categoria, categoria_id)
                if categoria is None or categoria.guild_id != interacao.guild_id:
                    categoria_id = None

            produto = Produto(
                guild_id=interacao.guild_id,
                categoria_id=categoria_id,
                nome=nome[:100],
                descricao=(descricao or "").strip() or None,
                preco_centavos=centavos,
            )
            sessao.add(produto)
            await sessao.commit()
            await sessao.refresh(produto)

        await interacao.response.send_message(
            f"✅ Produto **{produto.nome}** cadastrado por **{formatar_preco(produto.preco_centavos)}**.\n"
            "Ele ja aparece para os clientes em `/loja`.\n"
            "_Dica: o comprador ve a descricao do produto no momento da compra._",
            ephemeral=True,
        )

    @grupo_produto.command(name="listar", description="Lista os produtos da loja")
    @app_commands.describe(categoria_id="Filtrar por uma categoria (opcional)")
    @app_commands.autocomplete(categoria_id=sugerir_categorias)
    @exigir_admin()
    async def produto_listar(
        self, interacao: discord.Interaction, categoria_id: Optional[int] = None
    ) -> None:
        async with Session() as sessao:
            consulta = select(Produto).where(Produto.guild_id == interacao.guild_id)
            if categoria_id is not None:
                consulta = consulta.where(Produto.categoria_id == categoria_id)

            produtos = list((await sessao.scalars(consulta.order_by(Produto.nome))).all())
            categorias = list(
                (await sessao.scalars(select(Categoria).where(Categoria.guild_id == interacao.guild_id))).all()
            )

        if not produtos:
            await interacao.response.send_message(
                "Nenhum produto cadastrado ainda.\nUse `/produto criar` — exemplo: nome `Camisa`, preco `49,90`.",
                ephemeral=True,
            )
            return

        nomes_das_categorias = {categoria.id: categoria.nome for categoria in categorias}
        grupos: dict[str, list[Produto]] = {}
        for produto in produtos:
            chave = nomes_das_categorias.get(produto.categoria_id, "Outros")
            grupos.setdefault(chave, []).append(produto)

        embed = discord.Embed(title="📦 Produtos da loja", color=config.COR_LOJA)
        for nome_grupo, itens in list(grupos.items())[:25]:
            linhas = [
                f"{'🟢' if produto.ativo else '🔴'} **{produto.nome}** — {formatar_preco(produto.preco_centavos)}"
                for produto in itens[:10]
            ]
            embed.add_field(name=f"📂 {nome_grupo}", value="\n".join(linhas)[:1024], inline=False)

        embed.set_footer(text="🟢 ativo  🔴 desativado — use /produto editar, /produto alternar ou /produto remover.")
        await interacao.response.send_message(embed=embed, ephemeral=True)

    @grupo_produto.command(name="editar", description="Edita um produto ja cadastrado")
    @app_commands.describe(
        produto_id="Produto que sera editado",
        nome="Novo nome (opcional)",
        preco="Novo preco (opcional), exemplo: 29,90",
        descricao="Nova descricao (opcional)",
        categoria_id="Mover para outra categoria (opcional)",
    )
    @app_commands.autocomplete(produto_id=sugerir_produtos, categoria_id=sugerir_categorias)
    @exigir_admin()
    async def produto_editar(
        self,
        interacao: discord.Interaction,
        produto_id: int,
        nome: Optional[str] = None,
        preco: Optional[str] = None,
        descricao: Optional[str] = None,
        categoria_id: Optional[int] = None,
    ) -> None:
        if nome is None and preco is None and descricao is None and categoria_id is None:
            await interacao.response.send_message(
                "Informe pelo menos um campo para alterar (nome, preco, descricao ou categoria_id).",
                ephemeral=True,
            )
            return

        async with Session() as sessao:
            produto = await sessao.get(Produto, produto_id)
            if produto is None or produto.guild_id != interacao.guild_id:
                await interacao.response.send_message("Produto nao encontrado.", ephemeral=True)
                return

            if nome is not None:
                produto.nome = nome.strip()[:100]
            if preco is not None:
                try:
                    produto.preco_centavos = parse_preco(preco)
                except ValueError as erro:
                    await interacao.response.send_message(f"⚠️ {erro}", ephemeral=True)
                    return
            if descricao is not None:
                produto.descricao = descricao.strip() or None
            if categoria_id is not None:
                categoria = await sessao.get(Categoria, categoria_id)
                produto.categoria_id = categoria_id if categoria and categoria.guild_id == interacao.guild_id else None

            await sessao.commit()
            await sessao.refresh(produto)

        await interacao.response.send_message(
            f"✅ Produto **{produto.nome}** atualizado — agora custa **{formatar_preco(produto.preco_centavos)}**.",
            ephemeral=True,
        )

    @grupo_produto.command(name="alternar", description="Ativa ou desativa um produto na loja")
    @app_commands.describe(produto_id="Produto que sera ativado/desativado")
    @app_commands.autocomplete(produto_id=sugerir_produtos)
    @exigir_admin()
    async def produto_alternar(self, interacao: discord.Interaction, produto_id: int) -> None:
        async with Session() as sessao:
            produto = await sessao.get(Produto, produto_id)
            if produto is None or produto.guild_id != interacao.guild_id:
                await interacao.response.send_message("Produto nao encontrado.", ephemeral=True)
                return
            produto.ativo = not produto.ativo
            await sessao.commit()

            estado = "🟢 ativado e visivel para os clientes" if produto.ativo else "🔴 desativado (some da loja)"
            await interacao.response.send_message(f"Produto **{produto.nome}** {estado}.", ephemeral=True)

    @grupo_produto.command(name="remover", description="Remove um produto da loja")
    @app_commands.describe(produto_id="Produto que sera excluido")
    @app_commands.autocomplete(produto_id=sugerir_produtos)
    @exigir_admin()
    async def produto_remover(self, interacao: discord.Interaction, produto_id: int) -> None:
        async with Session() as sessao:
            produto = await sessao.get(Produto, produto_id)
            if produto is None or produto.guild_id != interacao.guild_id:
                await interacao.response.send_message("Produto nao encontrado.", ephemeral=True)
                return
            nome = produto.nome
            await sessao.delete(produto)
            await sessao.commit()

        await interacao.response.send_message(
            f"🗑️ Produto **{nome}** removido. Os pedidos antigos continuam no historico.",
            ephemeral=True,
        )

    @grupo_produto.command(name="limpar-todos", description="Remove TODOS os produtos da sua loja")
    @exigir_admin()
    async def produto_limpar_todos(self, interacao: discord.Interaction) -> None:
        async with Session() as sessao:
            resultado = await sessao.execute(
                select(Produto).where(Produto.guild_id == interacao.guild_id)
            )
            produtos = list(resultado.scalars().all())
            
            if not produtos:
                await interacao.response.send_message("Sua loja ja nao tem produtos.", ephemeral=True)
                return

            quantidade = len(produtos)
            for p in produtos:
                await sessao.delete(p)
            await sessao.commit()

        await interacao.response.send_message(
            f"🗑️ Foram removidos **{quantidade}** produtos da sua loja.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Catalogo(bot))
