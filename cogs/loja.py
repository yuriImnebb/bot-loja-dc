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
from views import AbrirLojaView, abrir_catalogo, aplicar_icones_da_loja, montar_painel


# --------------------------------------------------------------------------- #
# Configuracao da loja (menu de tipo de chave -> formulario)
# --------------------------------------------------------------------------- #
class ConfigurarLojaModal(discord.ui.Modal, title="Configurar a loja"):
    def __init__(self, tipo_chave: str, voltar_ao_painel: bool = False) -> None:
        super().__init__()
        self.tipo_chave = tipo_chave
        self.voltar_ao_painel = voltar_ao_painel

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
                f"⚠️ {erro}\nAbra Configurar loja no painel e tente novamente.", ephemeral=True
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

        if self.voltar_ao_painel:
            from cogs.gestao_loja import PainelGestaoLojaView

            await interacao.response.send_message(
                f"✅ Loja **{nome}** e dados do PIX salvos. Continue configurando neste painel.",
                embed=discord.Embed(
                    title="Produtos e estoque",
                    description="Agora voce pode cadastrar produtos, ajustar valores e publicar a loja.",
                    color=config.COR_PREMIUM,
                ),
                view=await aplicar_icones_da_loja(
                    interacao.guild_id,
                    PainelGestaoLojaView(interacao.guild_id),
                ),
                ephemeral=True,
            )
        else:
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
    def __init__(self, voltar_ao_painel: bool = False) -> None:
        super().__init__(timeout=300)
        self.voltar_ao_painel = voltar_ao_painel
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
        await interacao.response.send_modal(
            ConfigurarLojaModal(tipo, voltar_ao_painel=self.voltar_ao_painel)
        )


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
    @grupo_painel.command(name="gerenciar", description="Abre o painel administrativo central da sua loja")
    @exigir_admin()
    async def painel_gerenciar(self, interacao: discord.Interaction) -> None:
        await interacao.response.defer(ephemeral=True, thinking=True)

        async with Session() as sessao:
            loja = await pegar_loja(sessao, interacao.guild_id)
        
        from views import PainelAdminView, montar_painel_admin
        await interacao.edit_original_response(
            embed=montar_painel_admin(loja), 
            view=PainelAdminView(loja), 
        )

    @grupo_painel.command(name="publicar", description="Publica o painel da loja para os clientes")
    @app_commands.describe(canal="Canal onde o painel sera publicado (padrao: o canal atual)")
    @exigir_admin()
    async def painel_publicar(
        self, interacao: discord.Interaction, canal: Optional[discord.TextChannel] = None
    ) -> None:
        destino = canal or interacao.channel
        
        # Defer para evitar timeout se precisar buscar mensagem antiga
        if not interacao.response.is_done():
            await interacao.response.defer(ephemeral=True)

        async with Session() as sessao:
            loja = await pegar_loja(sessao, interacao.guild_id)

        from views import PainelMestreView
        view = PainelMestreView(loja)

        # Tenta editar o painel antigo se ele existir no mesmo canal
        painel_editado = False
        if loja.painel_mensagem_id and loja.painel_canal_id == destino.id:
            try:
                msg = await destino.fetch_message(loja.painel_mensagem_id)
                await msg.edit(embed=montar_painel(loja), view=view)
                painel_editado = True
            except (discord.NotFound, discord.HTTPException):
                pass

        if not painel_editado:
            try:
                mensagem = await destino.send(embed=montar_painel(loja), view=view)
                async with Session() as sessao:
                    loja_db = await pegar_loja(sessao, interacao.guild_id)
                    loja_db.painel_mensagem_id = mensagem.id
                    loja_db.painel_canal_id = destino.id
                    await sessao.commit()
            except discord.HTTPException:
                await interacao.followup.send(
                    "Nao consegui publicar neste canal. Confira se eu tenho permissao de **Enviar Mensagens**. ",
                    ephemeral=True,
                )
                return

        await interacao.followup.send(
            f"✅ Painel {'atualizado' if painel_editado else 'publicado'} em {destino.mention}.",
            ephemeral=True,
        )

    @grupo_painel.command(name="personalizar", description="Personaliza o visual do seu painel (banner, logo e cor)")
    @app_commands.describe(
        banner="Imagem grande que fica no topo (anexe o arquivo)",
        logo="Imagem quadrada pequena no canto (anexe o arquivo)",
        cor="Cor da lateral da mensagem em Hex (Ex: #FF0000 para Vermelho)"
    )
    @exigir_admin()
    async def painel_personalizar(
        self,
        interacao: discord.Interaction,
        banner: Optional[discord.Attachment] = None,
        logo: Optional[discord.Attachment] = None,
        cor: Optional[str] = None
    ) -> None:
        if not any([banner, logo, cor]):
            await interacao.response.send_message(
                "Informe pelo menos uma coisa para mudar (anexe o banner, a logo ou digite a cor).",
                ephemeral=True
            )
            return

        await interacao.response.defer(ephemeral=True)

        async with Session() as sessao:
            loja = await pegar_loja(sessao, interacao.guild_id)
            
            if banner:
                if not banner.content_type or not banner.content_type.startswith("image/"):
                    await interacao.followup.send("O banner precisa ser uma imagem (PNG, JPG, etc).", ephemeral=True)
                    return
                loja.banner_url = banner.url
            
            if logo:
                if not logo.content_type or not logo.content_type.startswith("image/"):
                    await interacao.followup.send("A logo precisa ser uma imagem (PNG, JPG, etc).", ephemeral=True)
                    return
                loja.logo_url = logo.url
            
            if cor:
                cor = cor.strip()
                if not cor.startswith("#"):
                    cor = "#" + cor
                if len(cor) != 7:
                    await interacao.followup.send("Cor invalida. Use o formato Hex, ex: `#FF0000`.", ephemeral=True)
                    return
                loja.cor_hex = cor

            await sessao.commit()

        await interacao.followup.send(
            "✅ Personalizacao salva! Use `/painel publicar` para atualizar o painel no canal.",
            ephemeral=True
        )

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
