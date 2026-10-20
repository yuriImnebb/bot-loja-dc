"""Comandos do dono da loja (/painel) e a vitrine do cliente (/loja)."""

from __future__ import annotations

from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy import func, select

import config
import pix
from db import Session, pegar_loja, plano_do_servidor
from models import ROTULOS_DE_PLANO, Categoria, Produto, plano_tem_estoque
from utils import exigir_admin, formatar_preco
from views import AbrirLojaView, abrir_catalogo, montar_painel


# --------------------------------------------------------------------------- #
# Configuracao da loja (menu de tipo de chave -> formulario)
# --------------------------------------------------------------------------- #
class ConfigurarLojaModal(discord.ui.Modal, title="Configurar a loja"):
    def __init__(self, tipo_chave: str) -> None:
        super().__init__()
        self.tipo_chave = tipo_chave

        self.campo_nome = discord.ui.TextInput(
            label="Nome da loja",
            placeholder="Como o cliente vai ver sua loja",
            max_length=80,
        )
        self.campo_chave = discord.ui.TextInput(
            label=f"Chave PIX ({tipo_chave})",
            placeholder="Cole aqui a chave que vai receber o dinheiro",
            max_length=200,
        )
        self.campo_titular = discord.ui.TextInput(
            label="Nome do titular da conta",
            placeholder="Igual ao que aparece no app do seu banco",
            max_length=60,
        )
        self.campo_cidade = discord.ui.TextInput(
            label="Cidade do titular",
            placeholder="Ex: SAO PAULO",
            max_length=40,
            required=False,
            default="SAO PAULO",
        )
        self.add_item(self.campo_nome)
        self.add_item(self.campo_chave)
        self.add_item(self.campo_titular)
        self.add_item(self.campo_cidade)

    async def on_submit(self, interacao: discord.Interaction) -> None:
        nome = self.campo_nome.value.strip()
        chave = self.campo_chave.value.strip()
        titular = self.campo_titular.value.strip()
        cidade = (self.campo_cidade.value or "SAO PAULO").strip()

        if not nome:
            await interacao.response.send_message("O nome da loja nao pode ficar vazio.", ephemeral=True)
            return
        if not titular:
            await interacao.response.send_message("O nome do titular e obrigatorio para gerar o PIX.", ephemeral=True)
            return

        try:
            chave_normalizada = pix.normalizar_chave(self.tipo_chave, chave)
        except ValueError as erro:
            await interacao.response.send_message(
                f"⚠️ {erro}\nRode `/painel configurar` novamente e corrija a chave.", ephemeral=True
            )
            return

        async with Session() as sessao:
            loja = await pegar_loja(sessao, interacao.guild_id)
            loja.nome = nome[:100]
            loja.pix_tipo = self.tipo_chave
            loja.pix_chave = chave_normalizada
            loja.pix_titular = titular[:60]
            loja.pix_cidade = cidade[:40] or "SAO PAULO"
            await sessao.commit()

        await interacao.response.send_message(
            f"✅ Loja **{nome}** salva!\n\n"
            "**Proximos passos:**\n"
            "1️⃣ `/categoria criar` — organize seus produtos\n"
            "2️⃣ `/produto criar` — cadastre o que voce vende\n"
            "3️⃣ `/painel canais` — escolha o canal de vendas e o de logs\n"
            "4️⃣ `/painel publicar` — publique o painel para os clientes\n"
            "5️⃣ `/loja` — teste uma compra como se fosse um cliente\n\n"
            "O dinheiro cai direto na sua conta PIX. Use `/ajuda` para o tutorial completo.",
            ephemeral=True,
        )


class TipoChaveView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)
        menu = discord.ui.Select(
            placeholder="Qual e o tipo da sua chave PIX?",
            options=[
                discord.SelectOption(label="CPF", value="cpf", description="Chave cadastrada no CPF (11 numeros)"),
                discord.SelectOption(label="CNPJ", value="cnpj", description="Chave cadastrada no CNPJ (14 numeros)"),
                discord.SelectOption(label="E-mail", value="email", description="Chave do tipo e-mail"),
                discord.SelectOption(label="Telefone", value="telefone", description="DDD + numero"),
                discord.SelectOption(
                    label="Aleatoria (EVP)",
                    value="aleatoria",
                    description="A chave longa com letras, numeros e hifens",
                ),
            ],
        )
        menu.callback = self.escolher
        self.add_item(menu)

    async def escolher(self, interacao: discord.Interaction) -> None:
        tipo = interacao.data["values"][0]
        await interacao.response.send_modal(ConfigurarLojaModal(tipo))


# --------------------------------------------------------------------------- #
# Cog
# --------------------------------------------------------------------------- #
class Loja(commands.Cog):
    """Vitrine do cliente e painel de configuracao do dono."""

    grupo_painel = app_commands.Group(
        name="painel",
        description="Configuracao da sua loja (somente quem gerencia o servidor)",
        default_permissions=discord.Permissions(manage_guild=True),
        guild_only=True,
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------ #
    # /loja — visao do cliente
    # ------------------------------------------------------------------ #
    @app_commands.command(name="loja", description="Abre a vitrine desta loja para comprar")
    @app_commands.guild_only()
    async def loja(self, interacao: discord.Interaction) -> None:
        await abrir_catalogo(interacao)

    # ------------------------------------------------------------------ #
    # /painel
    # ------------------------------------------------------------------ #
    @grupo_painel.command(name="configurar", description="Configura o nome da loja e a chave PIX que recebe os pagamentos")
    @exigir_admin()
    async def painel_configurar(self, interacao: discord.Interaction) -> None:
        await interacao.response.send_message(
            embed=discord.Embed(
                title="⚙️ Configurar loja",
                description=(
                    "Primeiro escolha **o tipo da sua chave PIX** no menu abaixo.\n\n"
                    "A chave fica visivel apenas para quem compra (no QR Code e no Copia e Cola), "
                    "e o dinheiro cai direto na sua conta."
                ),
                color=config.COR_LOJA,
            ),
            view=TipoChaveView(),
            ephemeral=True,
        )

    @grupo_painel.command(name="canais", description="Escolhe o canal de vendas e o canal de logs")
    @app_commands.describe(
        vendas="Canal onde os cards de pedido aparecem para voce confirmar",
        logs="Canal onde ficam os registros de cada venda",
    )
    @exigir_admin()
    async def painel_canais(
        self,
        interacao: discord.Interaction,
        vendas: Optional[discord.TextChannel] = None,
        logs: Optional[discord.TextChannel] = None,
    ) -> None:
        if vendas is None and logs is None:
            await interacao.response.send_message(
                "Informe pelo menos um canal.\n"
                "Exemplo: `/painel canais vendas:#vendas logs:#logs`\n\n"
                "_Se voce nao escolher, os pedidos aparecem no mesmo canal onde a compra foi feita._",
                ephemeral=True,
            )
            return

        async with Session() as sessao:
            loja = await pegar_loja(sessao, interacao.guild_id)
            if vendas is not None:
                loja.canal_vendas_id = vendas.id
            if logs is not None:
                loja.canal_logs_id = logs.id
            await sessao.commit()

        partes = []
        if vendas is not None:
            partes.append(f"🧾 Pedidos vao para {vendas.mention}")
        if logs is not None:
            partes.append(f"📋 Logs vao para {logs.mention}")

        await interacao.response.send_message("✅ " + "\n".join(partes), ephemeral=True)

    @grupo_painel.command(name="publicar", description="Publica o painel da loja para os clientes")
    @app_commands.describe(canal="Canal onde o painel sera publicado (padrao: o canal atual)")
    @exigir_admin()
    async def painel_publicar(
        self, interacao: discord.Interaction, canal: Optional[discord.TextChannel] = None
    ) -> None:
        destino = canal or interacao.channel

        async with Session() as sessao:
            loja = await pegar_loja(sessao, interacao.guild_id)

        try:
            await destino.send(embed=montar_painel(loja), view=AbrirLojaView())
        except discord.HTTPException:
            await interacao.response.send_message(
                "Nao consegui publicar neste canal. Confira se eu tenho permissao de **Enviar Mensagens**. ",
                ephemeral=True,
            )
            return

        await interacao.response.send_message(
            f"✅ Painel publicado em {destino.mention}.\n"
            "Qualquer pessoa pode clicar em **Ver produtos** e comprar.",
            ephemeral=True,
        )

    @grupo_painel.command(name="info", description="Mostra um resumo da configuracao da loja")
    @exigir_admin()
    async def painel_info(self, interacao: discord.Interaction) -> None:
        async with Session() as sessao:
            loja = await pegar_loja(sessao, interacao.guild_id)
            categorias = await sessao.scalar(
                select(func.count()).select_from(Categoria).where(Categoria.guild_id == interacao.guild_id)
            )
            produtos = await sessao.scalar(
                select(func.count()).select_from(Produto).where(Produto.guild_id == interacao.guild_id)
            )

        if loja.pix_chave:
            chave_mostrada = f"{loja.pix_tipo} ••••{str(loja.pix_chave)[-4:]}"
        else:
            chave_mostrada = "❌ nao configurada"

        plano = await plano_do_servidor(interacao.guild_id)

        plano_rotulo = ROTULOS_DE_PLANO.get(plano, plano)
        tem_estoque = plano_tem_estoque(plano)

        embed = discord.Embed(title=f"📊 {loja.nome}", color=config.COR_LOJA)
        embed.add_field(
            name="Plano",
            value=f"**{plano_rotulo}**" + (" (estoque liberado)" if tem_estoque else ""),
            inline=False,
        )
        embed.add_field(name="Chave PIX", value=chave_mostrada, inline=False)
        embed.add_field(name="Titular", value=loja.pix_titular or "—", inline=True)
        embed.add_field(name="Cidade", value=loja.pix_cidade or "—", inline=True)
        embed.add_field(name="Categorias", value=str(categorias or 0), inline=True)
        embed.add_field(name="Produtos", value=str(produtos or 0), inline=True)
        embed.add_field(
            name="Canal de pedidos",
            value=f"<#{loja.canal_vendas_id}>" if loja.canal_vendas_id else "o mesmo da compra",
            inline=True,
        )
        embed.add_field(
            name="Canal de logs",
            value=f"<#{loja.canal_logs_id}>" if loja.canal_logs_id else "nao definido",
            inline=True,
        )

        pendencias = []
        if not loja.pix_chave:
            pendencias.append("`/painel configurar` — falta a chave PIX")
        if not produtos:
            pendencias.append("`/produto criar` — falta cadastrar produtos")
        embed.set_footer(text=("Falta: " + " • ".join(pendencias)) if pendencias else "Tudo pronto! Use /loja para testar.")

        await interacao.response.send_message(embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ #
    # Consulta de precos rapida
    # ------------------------------------------------------------------ #
    @app_commands.command(name="precos", description="Mostra a tabela de precos da loja")
    @app_commands.guild_only()
    async def precos(self, interacao: discord.Interaction) -> None:
        async with Session() as sessao:
            produtos = list(
                (
                    await sessao.scalars(
                        select(Produto)
                        .where(Produto.guild_id == interacao.guild_id, Produto.ativo.is_(True))
                        .order_by(Produto.preco_centavos)
                    )
                ).all()
            )

        if not produtos:
            await interacao.response.send_message("Esta loja ainda nao tem produtos ativos.", ephemeral=True)
            return

        embed = discord.Embed(title="💰 Tabela de precos", color=config.COR_LOJA)
        for produto in produtos[:25]:
            embed.add_field(name=produto.nome[:100], value=formatar_preco(produto.preco_centavos), inline=True)
        embed.set_footer(text="Use /loja para comprar")
        await interacao.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Loja(bot))
