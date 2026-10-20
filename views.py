"""Menus, botoes e embeds usados pela lojinha."""

from __future__ import annotations

import logging

import discord
from sqlalchemy import select

import config
import pix
from db import (
    Session,
    contar_estoque,
    marcar_cancelado,
    marcar_pago,
    pegar_loja,
    plano_do_servidor,
    produto_usa_estoque,
)
from entrega import (
    DM_FALHOU,
    descrever_resultado,
    entregar_pedido,
    enviar_aviso_de_dm_falhou,
)
from models import (
    STATUS_CANCELADO,
    STATUS_PAGO,
    STATUS_PENDENTE,
    Categoria,
    Loja,
    Pedido,
    Produto,
    plano_tem_estoque,
)
from utils import eh_admin, formatar_preco

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Embeds
# --------------------------------------------------------------------------- #
def montar_card(pedido: Pedido) -> discord.Embed:
    """Card publico do pedido, com os botoes de aprovacao do dono."""
    if pedido.status == STATUS_PAGO:
        cor, rotulo = config.COR_SUCESSO, "✅ Pago"
    elif pedido.status == STATUS_CANCELADO:
        cor, rotulo = config.COR_ERRO, "✖️ Cancelado"
    else:
        cor, rotulo = config.COR_ALERTA, "⏳ Aguardando pagamento"

    embed = discord.Embed(title=f"Pedido #{pedido.id}", color=cor)
    embed.add_field(name="Produto", value=pedido.produto_nome[:100], inline=True)
    embed.add_field(name="Valor", value=formatar_preco(pedido.preco_centavos), inline=True)
    embed.add_field(name="Status", value=rotulo, inline=True)
    embed.add_field(name="Comprador", value=f"<@{pedido.comprador_id}>", inline=False)

    if pedido.status == STATUS_PENDENTE:
        embed.set_footer(text="Dono da loja: confira o comprovante e clique em Confirmar pagamento.")
    else:
        embed.set_footer(text="Pedido ja processado.")
    return embed


def montar_painel(loja: Loja) -> discord.Embed:
    """Painel fixo publicado no canal de vendas."""
    embed = discord.Embed(
        title=f"🛒 {loja.nome}",
        description="Clique no botao abaixo para ver os produtos disponiveis e comprar.",
        color=config.COR_LOJA,
    )
    embed.set_footer(text="Pagamento via PIX direto para o dono da loja.")
    return embed


async def atualizar_card_pedido(bot: discord.Client, pedido: Pedido) -> None:
    """Edita o card do pedido para refletir o novo status e remove os botoes."""
    if not pedido.canal_id or not pedido.mensagem_id:
        return

    canal = bot.get_channel(pedido.canal_id)
    if canal is None:
        try:
            canal = await bot.fetch_channel(pedido.canal_id)
        except discord.HTTPException:
            return

    try:
        mensagem = await canal.fetch_message(pedido.mensagem_id)
    except discord.HTTPException:
        return

    try:
        await mensagem.edit(embed=montar_card(pedido), view=None)
    except discord.HTTPException:
        log.warning("Nao consegui atualizar o card do pedido %s.", pedido.id)


async def registrar_log(bot: discord.Client, guild_id: int, texto: str) -> None:
    """Envia uma linha de log no canal de logs configurado, se existir."""
    async with Session() as sessao:
        loja = await pegar_loja(sessao, guild_id)

    if not loja.canal_logs_id:
        return

    canal = bot.get_channel(loja.canal_logs_id)
    if canal is None:
        try:
            canal = await bot.fetch_channel(loja.canal_logs_id)
        except discord.HTTPException:
            return

    try:
        await canal.send(texto)
    except discord.HTTPException:
        log.warning("Nao consegui registrar log no canal %s.", loja.canal_logs_id)


# --------------------------------------------------------------------------- #
# Aprovacao de pedidos (view persistente: sobrevive a reinicio do bot)
# --------------------------------------------------------------------------- #
class AprovacaoPedidoView(discord.ui.View):
    def __init__(self, pedido_id: int) -> None:
        super().__init__(timeout=None)
        self.pedido_id = pedido_id

        confirmar = discord.ui.Button(
            label="Confirmar pagamento",
            emoji="✅",
            style=discord.ButtonStyle.success,
            custom_id=f"pedido:pagar:{pedido_id}",
        )
        cancelar = discord.ui.Button(
            label="Cancelar pedido",
            emoji="✖️",
            style=discord.ButtonStyle.danger,
            custom_id=f"pedido:cancelar:{pedido_id}",
        )
        confirmar.callback = self.confirmar
        cancelar.callback = self.cancelar
        self.add_item(confirmar)
        self.add_item(cancelar)

    async def confirmar(self, interacao: discord.Interaction) -> None:
        if not eh_admin(interacao):
            await interacao.response.send_message(
                "Somente quem tem a permissao **Gerenciar Servidor** pode confirmar pagamentos.",
                ephemeral=True,
            )
            return

        await interacao.response.defer()

        pedido, erro = await marcar_pago(self.pedido_id, interacao.user.id)
        if erro or pedido is None:
            await interacao.followup.send(f"⚠️ {erro or 'Pedido nao encontrado.'}", ephemeral=True)
            return

        await atualizar_card_pedido(interacao.client, pedido)

        resultado, item = await entregar_pedido(interacao.client, pedido)

        linhas = [
            f"✅ Pagamento do pedido **#{pedido.id}** confirmado por {interacao.user.mention}.",
            f"<@{pedido.comprador_id}>, seu pedido foi aprovado.",
        ]
        descricao = descrever_resultado(resultado)
        if descricao:
            linhas.append(descricao)
        await interacao.followup.send("\n".join(linhas))

        if resultado == DM_FALHOU and item is not None:
            await enviar_aviso_de_dm_falhou(interacao, item)

        await registrar_log(
            interacao.client,
            pedido.guild_id,
            f"💰 Pedido **#{pedido.id}** ({pedido.produto_nome} — {formatar_preco(pedido.preco_centavos)}) "
            f"confirmado por {interacao.user.mention}. {descricao}",
        )

    async def cancelar(self, interacao: discord.Interaction) -> None:
        if not eh_admin(interacao):
            await interacao.response.send_message(
                "Somente quem tem a permissao **Gerenciar Servidor** pode cancelar pedidos.",
                ephemeral=True,
            )
            return

        await interacao.response.defer()

        pedido, erro = await marcar_cancelado(self.pedido_id, interacao.user.id)
        if erro or pedido is None:
            await interacao.followup.send(f"⚠️ {erro or 'Pedido nao encontrado.'}", ephemeral=True)
            return

        await atualizar_card_pedido(interacao.client, pedido)
        await interacao.followup.send(f"✖️ Pedido **#{pedido.id}** cancelado por {interacao.user.mention}.")
        await registrar_log(
            interacao.client,
            pedido.guild_id,
            f"✖️ Pedido **#{pedido.id}** ({pedido.produto_nome}) cancelado por {interacao.user.mention}.",
        )


# --------------------------------------------------------------------------- #
# Fluxo de compra
# --------------------------------------------------------------------------- #
class SelecionarProduto(discord.ui.View):
    def __init__(self, guild_id: int, produtos: list[Produto]) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.produtos = {produto.id: produto for produto in produtos}

        opcoes = [
            discord.SelectOption(
                label=produto.nome[:100],
                value=str(produto.id),
                description=formatar_preco(produto.preco_centavos),
            )
            for produto in produtos[:25]
        ]
        menu = discord.ui.Select(placeholder="Escolha um produto...", options=opcoes)
        menu.callback = self.escolher
        self.add_item(menu)

    async def escolher(self, interacao: discord.Interaction) -> None:
        produto = self.produtos.get(int(interacao.data["values"][0]))
        if produto is None:
            await interacao.response.send_message("Produto indisponivel no momento.", ephemeral=True)
            return

        embed = discord.Embed(
            title=f"🧾 {produto.nome}",
            description=(produto.descricao or "Sem descricao.")[:1500],
            color=config.COR_LOJA,
        )
        embed.add_field(name="Preco", value=f"**{formatar_preco(produto.preco_centavos)}**", inline=True)

        esgotado = False
        plano = await plano_do_servidor(self.guild_id)
        if plano_tem_estoque(plano) and await produto_usa_estoque(self.guild_id, produto.id):
            disponivel = await contar_estoque(self.guild_id, produto.id)
            esgotado = disponivel <= 0
            embed.add_field(
                name="Estoque",
                value="❌ esgotado" if esgotado else f"**{disponivel}** disponivel(is)",
                inline=True,
            )

        embed.set_footer(text=f"Produto #{produto.id}")

        await interacao.response.edit_message(
            embed=embed,
            view=None if esgotado else ConfirmarCompra(self.guild_id, produto.id),
        )


class SelecionarCategoria(discord.ui.View):
    def __init__(
        self,
        guild_id: int,
        categorias: list[Categoria],
        contagem: dict[int, int],
        sem_categoria: int,
    ) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id

        opcoes: list[discord.SelectOption] = []
        for categoria in categorias[:24]:
            opcoes.append(
                discord.SelectOption(
                    label=categoria.nome[:100],
                    value=str(categoria.id),
                    description=f"{contagem.get(categoria.id, 0)} produto(s)",
                )
            )
        if sem_categoria:
            opcoes.append(
                discord.SelectOption(
                    label="Outros",
                    value="sem",
                    description=f"{sem_categoria} produto(s) sem categoria",
                )
            )
        if not opcoes:
            opcoes.append(discord.SelectOption(label="Todos os produtos", value="todos"))

        menu = discord.ui.Select(placeholder="Escolha uma categoria...", options=opcoes[:25])
        menu.callback = self.escolher
        self.add_item(menu)

    async def escolher(self, interacao: discord.Interaction) -> None:
        valor = interacao.data["values"][0]

        consulta = (
            select(Produto)
            .where(Produto.guild_id == self.guild_id, Produto.ativo.is_(True))
            .order_by(Produto.nome)
        )
        if valor == "sem":
            consulta = consulta.where(Produto.categoria_id.is_(None))
        elif valor != "todos":
            consulta = consulta.where(Produto.categoria_id == int(valor))

        async with Session() as sessao:
            produtos = list((await sessao.scalars(consulta.limit(25))).all())

        if not produtos:
            await interacao.response.send_message("Ainda nao ha produtos nesta categoria.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Escolha um produto",
            description="Selecione no menu abaixo para ver os detalhes e o preco.",
            color=config.COR_LOJA,
        )
        if len(produtos) == 25:
            embed.set_footer(text="Mostrando os 25 primeiros resultados.")

        await interacao.response.edit_message(embed=embed, view=SelecionarProduto(self.guild_id, produtos))


class ConfirmarCompra(discord.ui.View):
    def __init__(self, guild_id: int, produto_id: int) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.produto_id = produto_id

        botao = discord.ui.Button(label="Gerar PIX", emoji="💳", style=discord.ButtonStyle.success)
        botao.callback = self.gerar_pix
        self.add_item(botao)

    async def gerar_pix(self, interacao: discord.Interaction) -> None:
        await interacao.response.defer()

        async with Session() as sessao:
            loja = await pegar_loja(sessao, self.guild_id)
            produto = await sessao.get(Produto, self.produto_id)

        if produto is None or not produto.ativo:
            await interacao.followup.send("Este produto nao esta mais disponivel.", ephemeral=True)
            return

        if not loja.pix_chave:
            await interacao.followup.send(
                "⚠️ Esta loja ainda nao configurou a chave PIX.\n"
                "Avise o dono da loja para rodar `/painel configurar`.",
                ephemeral=True,
            )
            return

        plano = await plano_do_servidor(self.guild_id)
        if plano_tem_estoque(plano) and await produto_usa_estoque(self.guild_id, produto.id):
            if await contar_estoque(self.guild_id, produto.id) <= 0:
                await interacao.followup.send(
                    "😕 Este produto esta **esgotado** no momento.\n"
                    "Avise o dono da loja para repor o estoque.",
                    ephemeral=True,
                )
                return

        pedido = Pedido(
            guild_id=self.guild_id,
            produto_id=produto.id,
            produto_nome=produto.nome,
            preco_centavos=produto.preco_centavos,
            comprador_id=interacao.user.id,
            canal_id=loja.canal_vendas_id or interacao.channel_id,
            status=STATUS_PENDENTE,
        )
        async with Session() as sessao:
            sessao.add(pedido)
            await sessao.commit()
            await sessao.refresh(pedido)

        try:
            payload = pix.gerar_payload(
                loja.pix_tipo,
                loja.pix_chave,
                loja.pix_titular,
                loja.pix_cidade,
                produto.preco_centavos,
                txid=f"PEDIDO{pedido.id}",
            )
        except ValueError as erro:
            await marcar_cancelado(pedido.id, interacao.user.id)
            await interacao.followup.send(
                f"⚠️ Nao consegui gerar o PIX: {erro}\n"
                "Avise o dono da loja para revisar a chave em `/painel configurar`.",
                ephemeral=True,
            )
            return

        async with Session() as sessao:
            registro = await sessao.get(Pedido, pedido.id)
            registro.pix_payload = payload
            await sessao.commit()
        pedido.pix_payload = payload

        # Publica o card com os botoes de aprovacao do dono da loja
        canal = interacao.client.get_channel(pedido.canal_id)
        if canal is None:
            try:
                canal = await interacao.client.fetch_channel(pedido.canal_id)
            except discord.HTTPException:
                canal = None

        if canal is not None:
            try:
                mensagem = await canal.send(embed=montar_card(pedido), view=AprovacaoPedidoView(pedido.id))
                async with Session() as sessao:
                    registro = await sessao.get(Pedido, pedido.id)
                    registro.mensagem_id = mensagem.id
                    await sessao.commit()
            except discord.HTTPException:
                log.warning("Nao consegui publicar o card do pedido %s.", pedido.id)

        await interacao.edit_original_response(
            embed=discord.Embed(
                title=f"✅ Pedido #{pedido.id} criado",
                description="O PIX esta logo abaixo 👇 Pague e envie o comprovante no canal.",
                color=config.COR_SUCESSO,
            ),
            view=None,
        )

        embed_pix = discord.Embed(title="💳 Pague com PIX", color=config.COR_SUCESSO)
        embed_pix.add_field(name="Produto", value=produto.nome[:100], inline=True)
        embed_pix.add_field(name="Valor", value=f"**{formatar_preco(produto.preco_centavos)}**", inline=True)
        embed_pix.add_field(name="Pedido", value=f"#{pedido.id}", inline=True)
        embed_pix.add_field(name="PIX Copia e Cola", value=f"```\n{payload}\n```", inline=False)
        embed_pix.set_footer(text="Depois de pagar, envie o comprovante. O dono da loja confirma e libera o produto.")

        await interacao.followup.send(
            embed=embed_pix,
            file=discord.File(pix.gerar_qr_png(payload), filename=f"pix_pedido_{pedido.id}.png"),
            ephemeral=True,
        )


# --------------------------------------------------------------------------- #
# Painel fixo da loja
# --------------------------------------------------------------------------- #
class AbrirLojaView(discord.ui.View):
    """View persistente do painel publicado por /painel publicar."""

    def __init__(self) -> None:
        super().__init__(timeout=None)
        botao = discord.ui.Button(label="Ver produtos", emoji="🛒", style=discord.ButtonStyle.primary, custom_id="loja:abrir")
        botao.callback = self.abrir
        self.add_item(botao)

    async def abrir(self, interacao: discord.Interaction) -> None:
        await abrir_catalogo(interacao)


async def abrir_catalogo(interacao: discord.Interaction) -> None:
    """Mostra a vitrine da loja (usado pelo /loja e pelo botao do painel)."""
    if interacao.guild is None:
        await interacao.response.send_message("Use isto dentro de um servidor.", ephemeral=True)
        return

    async with Session() as sessao:
        loja = await pegar_loja(sessao, interacao.guild_id)
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
            (
                await sessao.scalars(
                    select(Produto)
                    .where(Produto.guild_id == interacao.guild_id, Produto.ativo.is_(True))
                    .order_by(Produto.nome)
                )
            ).all()
        )

    if not produtos:
        await interacao.response.send_message(
            embed=discord.Embed(
                title="🛒 Loja sem produtos ainda",
                description=(
                    "Esta loja ainda nao publicou nenhum produto.\n\n"
                    "**E o dono da loja?** Use `/produto criar` para cadastrar o primeiro item "
                    "ou `/ajuda` para ver o passo a passo."
                ),
                color=config.COR_LOJA,
            ),
            ephemeral=True,
        )
        return

    contagem: dict[int, int] = {categoria.id: 0 for categoria in categorias}
    for produto in produtos:
        if produto.categoria_id in contagem:
            contagem[produto.categoria_id] += 1
    sem_categoria = sum(1 for produto in produtos if produto.categoria_id is None)

    embed = discord.Embed(
        title=f"🛒 {loja.nome}",
        description="Escolha uma categoria no menu abaixo para ver os produtos.",
        color=config.COR_LOJA,
    )
    embed.set_footer(text=f"{len(produtos)} produto(s) disponivel(is) nesta loja")

    await interacao.response.send_message(
        embed=embed,
        view=SelecionarCategoria(interacao.guild_id, categorias, contagem, sem_categoria),
        ephemeral=True,
    )
