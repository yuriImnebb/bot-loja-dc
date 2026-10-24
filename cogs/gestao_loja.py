"""Acoes de produtos, estoque e publicacao acessiveis pelo painel da loja."""

from __future__ import annotations

import discord
from sqlalchemy import func, select

import config
from db import (
    Session,
    adicionar_estoque,
    contar_estoque,
    pegar_loja,
    plano_do_servidor,
    remover_proxima_unidade_estoque,
)
from models import ITEM_LINK, ITEM_TEXTO, ItemEstoque, Produto, plano_tem_estoque
from utils import eh_admin, formatar_preco, parse_preco
from views import (
    PainelAdminView,
    PainelMestreView,
    aplicar_icones_da_loja,
    montar_painel,
    montar_painel_admin,
)


def _interacao_autorizada(interacao: discord.Interaction, guild_id: int) -> bool:
    return interacao.guild_id == guild_id and eh_admin(interacao)


async def _com_icones(guild_id: int, view: discord.ui.View) -> discord.ui.View:
    await aplicar_icones_da_loja(guild_id, view)
    return view


async def _mostrar_painel(interacao: discord.Interaction, guild_id: int) -> None:
    async with Session() as sessao:
        loja = await pegar_loja(sessao, guild_id)
    await interacao.response.edit_message(
        embed=montar_painel_admin(loja),
        view=await _com_icones(guild_id, PainelAdminView(loja)),
    )


async def montar_embed_gestao(guild_id: int) -> discord.Embed:
    async with Session() as sessao:
        loja = await pegar_loja(sessao, guild_id)
        produtos = await sessao.scalar(
            select(func.count()).select_from(Produto).where(Produto.guild_id == guild_id)
        )
        estoque = await sessao.scalar(
            select(func.count())
            .select_from(ItemEstoque)
            .where(ItemEstoque.guild_id == guild_id, ItemEstoque.entregue.is_(False))
        )

    cor = config.COR_PREMIUM
    if loja.cor_hex:
        try:
            cor = int(loja.cor_hex.removeprefix("#"), 16)
        except ValueError:
            pass

    embed = discord.Embed(
        title=loja.nome,
        description=(
            "Gerencie seu catálogo, estoque e pagamentos.\n"
            "Selecione uma ação no menu para continuar."
        ),
        color=cor,
    )
    embed.add_field(name="◈  Catálogo", value=f"**{produtos or 0}** produtos", inline=True)
    embed.add_field(name="◦  Estoque", value=f"**{estoque or 0}** unidades disponíveis", inline=True)
    embed.add_field(
        name="◦  Pagamentos",
        value="PIX pronto" if loja.pix_tipo and loja.pix_chave else "PIX pendente",
        inline=True,
    )
    embed.add_field(
        name="↗  Vitrine",
        value=(
            f"<#{loja.painel_canal_id}>"
            if loja.painel_mensagem_id and loja.painel_canal_id
            else "Escolha um canal para publicar"
        ),
        inline=True,
    )
    embed.add_field(
        name="◦  Registros",
        value=f"<#{loja.canal_logs_id}>" if loja.canal_logs_id else "Não configurado",
        inline=True,
    )
    if loja.logo_url:
        embed.set_thumbnail(url=loja.logo_url)
    if loja.banner_url:
        embed.set_image(url=loja.banner_url)
    embed.set_footer(text="Gestão centralizada · Estoque limitado ou entrega automática")
    return embed


def _embed_etapa(titulo: str, descricao: str) -> discord.Embed:
    embed = discord.Embed(
        title=titulo,
        description=descricao,
        color=config.COR_PREMIUM,
    )
    embed.set_footer(text="Gestão da loja · Configuração centralizada")
    return embed


async def _produtos_da_loja(guild_id: int) -> list[Produto]:
    async with Session() as sessao:
        return list(
            (
                await sessao.scalars(
                    select(Produto)
                    .where(Produto.guild_id == guild_id)
                    .order_by(Produto.nome)
                    .limit(25)
                )
            ).all()
        )


class CriarProdutoModal(discord.ui.Modal, title="Adicionar produto"):
    def __init__(self, guild_id: int) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.nome = discord.ui.TextInput(label="Nome do produto", max_length=100)
        self.preco = discord.ui.TextInput(
            label="Valor em reais",
            placeholder="Exemplo: 19,90",
            max_length=20,
        )
        self.descricao = discord.ui.TextInput(
            label="Descricao (opcional)",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1000,
        )
        self.add_item(self.nome)
        self.add_item(self.preco)
        self.add_item(self.descricao)

    async def on_submit(self, interacao: discord.Interaction) -> None:
        if not _interacao_autorizada(interacao, self.guild_id):
            await interacao.response.send_message("Sem permissao para alterar esta loja.", ephemeral=True)
            return

        nome = self.nome.value.strip()
        if len(nome) < 2:
            await interacao.response.send_message("O nome precisa ter pelo menos 2 caracteres.", ephemeral=True)
            return
        try:
            preco_centavos = parse_preco(self.preco.value)
        except ValueError as erro:
            await interacao.response.send_message(f"Valor invalido: {erro}", ephemeral=True)
            return

        async with Session() as sessao:
            sessao.add(
                Produto(
                    guild_id=self.guild_id,
                    nome=nome[:100],
                    descricao=self.descricao.value.strip() or None,
                    preco_centavos=preco_centavos,
                )
            )
            await sessao.commit()

        await interacao.response.send_message(
            f"Produto **{nome}** criado por **{formatar_preco(preco_centavos)}**. "
            "Por padrao, ele fica com quantidade infinita e entrega manual. "
            "Adicione unidades pelo painel para mudar para estoque limitado.",
            embed=await montar_embed_gestao(self.guild_id),
            view=await _com_icones(self.guild_id, PainelGestaoLojaView(self.guild_id)),
            ephemeral=True,
        )


class AlterarPrecoModal(discord.ui.Modal, title="Alterar valor do produto"):
    def __init__(self, guild_id: int, produto_id: int, produto_nome: str) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.produto_id = produto_id
        self.valor = discord.ui.TextInput(
            label=f"Novo valor • {produto_nome[:35]}",
            placeholder="Exemplo: 29,90",
            max_length=20,
        )
        self.add_item(self.valor)

    async def on_submit(self, interacao: discord.Interaction) -> None:
        if not _interacao_autorizada(interacao, self.guild_id):
            await interacao.response.send_message("Sem permissao para alterar esta loja.", ephemeral=True)
            return
        try:
            centavos = parse_preco(self.valor.value)
        except ValueError as erro:
            await interacao.response.send_message(f"Valor invalido: {erro}", ephemeral=True)
            return

        async with Session() as sessao:
            produto = await sessao.get(Produto, self.produto_id)
            if produto is None or produto.guild_id != self.guild_id:
                await interacao.response.send_message("Produto nao encontrado nesta loja.", ephemeral=True)
                return
            produto.preco_centavos = centavos
            await sessao.commit()

        await interacao.response.send_message(
            f"Valor atualizado para **{formatar_preco(centavos)}**.",
            embed=await montar_embed_gestao(self.guild_id),
            view=await _com_icones(self.guild_id, PainelGestaoLojaView(self.guild_id)),
            ephemeral=True,
        )


class AdicionarEstoquePainelModal(discord.ui.Modal, title="Adicionar unidades ao estoque"):
    def __init__(
        self, guild_id: int, produto_id: int, produto_nome: str, tipo: str, por_linha: bool
    ) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.produto_id = produto_id
        self.tipo = tipo
        self.por_linha = por_linha
        self.conteudo = discord.ui.TextInput(
            label=f"Unidades • {produto_nome[:30]}",
            placeholder=(
                "Cole uma unidade por linha"
                if por_linha
                else "Cole o conteudo completo de uma unidade"
            ),
            style=discord.TextStyle.paragraph,
            max_length=4000,
        )
        self.add_item(self.conteudo)

    async def on_submit(self, interacao: discord.Interaction) -> None:
        if not _interacao_autorizada(interacao, self.guild_id):
            await interacao.response.send_message("Sem permissao para alterar esta loja.", ephemeral=True)
            return

        bruto = self.conteudo.value.strip()
        if not bruto:
            await interacao.response.send_message("Cole ao menos uma unidade de estoque.", ephemeral=True)
            return
        conteudos = (
            [linha.strip() for linha in bruto.splitlines() if linha.strip()]
            if self.por_linha
            else [bruto]
        )
        adicionados = await adicionar_estoque(
            self.guild_id, self.produto_id, self.tipo, conteudos
        )
        if not adicionados:
            await interacao.response.send_message("Produto nao encontrado nesta loja.", ephemeral=True)
            return

        disponivel = await contar_estoque(self.guild_id, self.produto_id)
        await interacao.response.send_message(
            f"{adicionados} unidade(s) adicionada(s). Estoque atual: **{disponivel}**.",
            embed=await montar_embed_gestao(self.guild_id),
            view=await _com_icones(self.guild_id, PainelGestaoLojaView(self.guild_id)),
            ephemeral=True,
        )


class EntregaInfinitaModal(discord.ui.Modal, title="Configurar entrega infinita"):
    def __init__(self, guild_id: int, produto_id: int, produto_nome: str, tipo: str) -> None:
        super().__init__()
        self.guild_id = guild_id
        self.produto_id = produto_id
        self.tipo = tipo
        self.conteudo = discord.ui.TextInput(
            label=f"Conteudo reutilizado • {produto_nome[:25]}",
            placeholder="Conteudo enviado automaticamente em cada compra",
            style=discord.TextStyle.paragraph,
            max_length=4000,
        )
        self.add_item(self.conteudo)

    async def on_submit(self, interacao: discord.Interaction) -> None:
        if not _interacao_autorizada(interacao, self.guild_id):
            await interacao.response.send_message("Sem permissao para alterar esta loja.", ephemeral=True)
            return
        conteudo = self.conteudo.value.strip()
        if not conteudo:
            await interacao.response.send_message("O conteudo da entrega nao pode ficar vazio.", ephemeral=True)
            return
        if not plano_tem_estoque(await plano_do_servidor(self.guild_id)):
            await interacao.response.send_message(
                "Entrega automatica esta disponivel nos planos Media e Pro.",
                ephemeral=True,
            )
            return

        async with Session() as sessao:
            produto = await sessao.get(Produto, self.produto_id)
            if produto is None or produto.guild_id != self.guild_id:
                await interacao.response.send_message("Produto nao encontrado nesta loja.", ephemeral=True)
                return
            produto.estoque_limitado = False
            produto.entrega_infinita = conteudo
            produto.tipo_entrega_infinita = self.tipo
            await sessao.commit()

        await interacao.response.send_message(
            f"**{produto.nome}** agora vende sem limite e envia automaticamente o conteudo salvo.",
            embed=await montar_embed_gestao(self.guild_id),
            view=await _com_icones(self.guild_id, PainelGestaoLojaView(self.guild_id)),
            ephemeral=True,
        )


class SelecionarProdutoView(discord.ui.View):
    def __init__(self, guild_id: int, acao: str, produtos: list[Produto]) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.acao = acao
        if not produtos:
            return

        menu = discord.ui.Select(
            placeholder="Selecione um produto",
            options=[
                discord.SelectOption(
                    label=produto.nome[:100],
                    value=str(produto.id),
                    description=formatar_preco(produto.preco_centavos),
                )
                for produto in produtos
            ],
        )
        menu.callback = self.selecionar
        self.add_item(menu)
        botao_voltar = discord.ui.Button(label="‹  Voltar", style=discord.ButtonStyle.secondary, row=1)
        botao_voltar.callback = self.voltar
        self.add_item(botao_voltar)

    async def voltar(self, interacao: discord.Interaction) -> None:
        if not _interacao_autorizada(interacao, self.guild_id):
            await interacao.response.send_message("Sem permissao para usar este painel.", ephemeral=True)
            return
        await interacao.response.edit_message(
            embed=await montar_embed_gestao(self.guild_id),
            view=await _com_icones(self.guild_id, PainelGestaoLojaView(self.guild_id)),
        )

    async def selecionar(self, interacao: discord.Interaction) -> None:
        if not _interacao_autorizada(interacao, self.guild_id):
            await interacao.response.send_message("Sem permissao para alterar esta loja.", ephemeral=True)
            return
        produto_id = int(interacao.data["values"][0])
        async with Session() as sessao:
            produto = await sessao.get(Produto, produto_id)
            if produto is None or produto.guild_id != self.guild_id:
                await interacao.response.send_message("Produto nao encontrado nesta loja.", ephemeral=True)
                return

        if self.acao == "preco":
            await interacao.response.send_modal(
                AlterarPrecoModal(self.guild_id, produto.id, produto.nome)
            )
        elif self.acao == "estoque":
            if not plano_tem_estoque(await plano_do_servidor(self.guild_id)):
                await interacao.response.send_message(
                    "Estoque limitado esta disponivel nos planos Media e Pro.",
                    ephemeral=True,
                )
                return
            await interacao.response.edit_message(
                embed=_embed_etapa(
                    f"Adicionar estoque • {produto.nome}",
                    "Escolha o formato e defina se cada linha representa uma unidade.",
                ),
                view=await _com_icones(
                    self.guild_id,
                    FormatoEstoqueView(self.guild_id, produto),
                ),
            )
        elif self.acao == "entrega":
            if not plano_tem_estoque(await plano_do_servidor(self.guild_id)):
                await interacao.response.send_message(
                    "Entrega automatica esta disponivel nos planos Media e Pro.",
                    ephemeral=True,
                )
                return
            await interacao.response.edit_message(
                embed=_embed_etapa(
                    f"Entrega infinita • {produto.nome}",
                    "Escolha o formato do conteúdo reutilizado em cada compra.",
                ),
                view=await _com_icones(
                    self.guild_id,
                    FormatoEntregaInfinitaView(self.guild_id, produto),
                ),
            )
        elif self.acao == "remover":
            if not plano_tem_estoque(await plano_do_servidor(self.guild_id)):
                await interacao.response.send_message(
                    "Estoque limitado esta disponivel nos planos Media e Pro.",
                    ephemeral=True,
                )
                return
            removido = await remover_proxima_unidade_estoque(self.guild_id, produto.id)
            if not removido:
                await interacao.response.edit_message(
                    embed=discord.Embed(
                        title="Estoque vazio",
                        description=f"Nao ha unidades disponiveis para remover de **{produto.nome}**.",
                        color=config.COR_ALERTA,
                    ),
                    view=await _com_icones(
                        self.guild_id,
                        PainelGestaoLojaView(self.guild_id),
                    ),
                )
                return

            restante = await contar_estoque(self.guild_id, produto.id)
            await interacao.response.edit_message(
                embed=discord.Embed(
                    title="Unidade removida",
                    description=(
                        f"A unidade disponivel mais antiga de **{produto.nome}** foi removida.\n"
                        f"Estoque restante: **{restante}** unidade(s)."
                    ),
                    color=config.COR_SUCESSO,
                ),
                view=await _com_icones(
                    self.guild_id,
                    PainelGestaoLojaView(self.guild_id),
                ),
            )


class FormatoEstoqueView(discord.ui.View):
    def __init__(self, guild_id: int, produto: Produto) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.produto_id = produto.id
        self.produto_nome = produto.nome

    async def _abrir_modal(
        self, interacao: discord.Interaction, tipo: str, por_linha: bool
    ) -> None:
        if not _interacao_autorizada(interacao, self.guild_id):
            await interacao.response.send_message("Sem permissao para alterar esta loja.", ephemeral=True)
            return
        await interacao.response.send_modal(
            AdicionarEstoquePainelModal(
                self.guild_id, self.produto_id, self.produto_nome, tipo, por_linha
            )
        )

    @discord.ui.button(label="↗  Link · uma unidade por linha", style=discord.ButtonStyle.primary)
    async def links(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        await self._abrir_modal(interacao, ITEM_LINK, True)

    @discord.ui.button(label="◦  Texto · conteúdo único", style=discord.ButtonStyle.secondary)
    async def texto_unico(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        await self._abrir_modal(interacao, ITEM_TEXTO, False)

    @discord.ui.button(label="◦  Texto · uma unidade por linha", style=discord.ButtonStyle.secondary)
    async def textos_por_linha(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        await self._abrir_modal(interacao, ITEM_TEXTO, True)


class FormatoEntregaInfinitaView(discord.ui.View):
    def __init__(self, guild_id: int, produto: Produto) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.produto_id = produto.id
        self.produto_nome = produto.nome

    async def _abrir_modal(self, interacao: discord.Interaction, tipo: str) -> None:
        if not _interacao_autorizada(interacao, self.guild_id):
            await interacao.response.send_message("Sem permissao para alterar esta loja.", ephemeral=True)
            return
        await interacao.response.send_modal(
            EntregaInfinitaModal(
                self.guild_id,
                self.produto_id,
                self.produto_nome,
                tipo,
            )
        )

    @discord.ui.button(label="↗  Link reutilizável", style=discord.ButtonStyle.primary)
    async def link(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        await self._abrir_modal(interacao, ITEM_LINK)

    @discord.ui.button(label="◦  Texto reutilizável", style=discord.ButtonStyle.secondary)
    async def texto(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        await self._abrir_modal(interacao, ITEM_TEXTO)


class PainelGestaoLojaView(discord.ui.View):
    def __init__(self, guild_id: int) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        menu = discord.ui.Select(
            placeholder="Escolha uma ação para sua loja",
            options=[
                discord.SelectOption(label="＋  Cadastrar produto", value="criar", description="Adicionar um item ao catálogo"),
                discord.SelectOption(label="＄  Alterar preço", value="preco", description="Atualizar o valor de um produto"),
                discord.SelectOption(label="◦  Adicionar estoque", value="estoque", description="Inserir novas unidades disponíveis"),
                discord.SelectOption(
                    label="−  Remover unidade do estoque",
                    value="remover",
                    description="Remove a unidade disponível mais antiga",
                ),
                discord.SelectOption(label="∞  Entrega automática", value="entrega", description="Configurar conteúdo reutilizável"),
                discord.SelectOption(label="⚙︎  Configurar loja e PIX", value="configurar", description="Editar nome da loja e recebimentos"),
                discord.SelectOption(label="↗  Publicar vitrine", value="publicar", description="Selecionar canal de vendas"),
                discord.SelectOption(label="◦  Canal de registros", value="logs", description="Selecionar canal de logs"),
            ],
        )
        menu.callback = self.executar
        self.add_item(menu)
        voltar = discord.ui.Button(label="‹  Painel principal", style=discord.ButtonStyle.secondary, row=1)
        voltar.callback = self.voltar
        self.add_item(voltar)

    async def voltar(self, interacao: discord.Interaction) -> None:
        if not _interacao_autorizada(interacao, self.guild_id):
            await interacao.response.send_message("Sem permissao para usar este painel.", ephemeral=True)
            return
        await _mostrar_painel(interacao, self.guild_id)

    async def executar(self, interacao: discord.Interaction) -> None:
        if not _interacao_autorizada(interacao, self.guild_id):
            await interacao.response.send_message("Sem permissao para alterar esta loja.", ephemeral=True)
            return
        acao = interacao.data["values"][0]
        if acao == "criar":
            await interacao.response.send_modal(CriarProdutoModal(self.guild_id))
            return
        if acao == "configurar":
            from cogs.loja import TipoChaveView

            view = TipoChaveView(voltar_ao_painel=True)
            voltar = discord.ui.Button(label="‹  Voltar", style=discord.ButtonStyle.secondary, row=1)

            async def voltar_callback(i: discord.Interaction) -> None:
                if not _interacao_autorizada(i, self.guild_id):
                    await i.response.send_message("Sem permissao para usar este painel.", ephemeral=True)
                    return
                await i.response.edit_message(
                    embed=await montar_embed_gestao(self.guild_id),
                    view=await _com_icones(
                        self.guild_id,
                        PainelGestaoLojaView(self.guild_id),
                    ),
                )

            voltar.callback = voltar_callback
            view.add_item(voltar)
            await interacao.response.edit_message(
                embed=_embed_etapa(
                    "Configurar loja e PIX",
                    "Escolha o tipo da chave PIX. O formulário também permite atualizar o nome da loja.",
                ),
                view=await _com_icones(self.guild_id, view),
            )
            return
        if acao == "publicar":
            await interacao.response.edit_message(
                embed=_embed_etapa(
                    "Publicar vitrine",
                    "Selecione o canal onde seus clientes encontrarão o catálogo da loja.",
                ),
                view=await _com_icones(
                    self.guild_id,
                    SelecionarCanalPublicacaoView(self.guild_id),
                ),
            )
            return
        if acao == "logs":
            await interacao.response.edit_message(
                embed=_embed_etapa(
                    "Canal de registros",
                    "Selecione onde o bot enviará os avisos e registros importantes da loja.",
                ),
                view=await _com_icones(
                    self.guild_id,
                    SelecionarCanalLogsView(self.guild_id),
                ),
            )
            return

        produtos = await _produtos_da_loja(self.guild_id)
        if acao == "preco" and not produtos:
            await interacao.response.send_message("Cadastre um produto antes de alterar valores.", ephemeral=True)
            return
        if acao in {"estoque", "remover", "entrega"} and not produtos:
            await interacao.response.send_message("Cadastre um produto antes de gerenciar o estoque.", ephemeral=True)
            return

        texto_acao = {
            "preco": "Escolha o produto que tera o valor alterado.",
            "estoque": "Escolha o produto que recebera unidades.",
            "remover": "Escolha o produto do qual deseja remover uma unidade.",
            "entrega": "Escolha o produto que reutilizara um conteudo em cada venda.",
        }[acao]
        await interacao.response.edit_message(
            embed=_embed_etapa("Catálogo e estoque", texto_acao),
            view=await _com_icones(
                self.guild_id,
                SelecionarProdutoView(self.guild_id, acao, produtos),
            ),
        )


class SelecionarCanalPublicacaoView(discord.ui.View):
    def __init__(self, guild_id: int) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.seletor_canal = discord.ui.ChannelSelect(
            placeholder="Escolha o canal de vendas",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=1,
        )
        self.seletor_canal.callback = self.publicar
        self.add_item(self.seletor_canal)
        voltar = discord.ui.Button(label="‹  Voltar", style=discord.ButtonStyle.secondary, row=1)
        voltar.callback = self.voltar
        self.add_item(voltar)

    async def voltar(self, interacao: discord.Interaction) -> None:
        if not _interacao_autorizada(interacao, self.guild_id):
            await interacao.response.send_message("Sem permissao para usar este painel.", ephemeral=True)
            return
        await interacao.response.edit_message(
            embed=await montar_embed_gestao(self.guild_id),
            view=await _com_icones(
                self.guild_id,
                PainelGestaoLojaView(self.guild_id),
            ),
        )

    async def publicar(self, interacao: discord.Interaction) -> None:
        if not _interacao_autorizada(interacao, self.guild_id):
            await interacao.response.send_message("Sem permissao para publicar esta loja.", ephemeral=True)
            return

        destino = self.seletor_canal.values[0]
        if not isinstance(destino, (discord.TextChannel, discord.Thread)):
            await interacao.response.send_message("Selecione um canal de texto valido.", ephemeral=True)
            return

        async with Session() as sessao:
            loja = await pegar_loja(sessao, self.guild_id)

        publicou = False
        atualizou = False
        if loja.painel_mensagem_id and loja.painel_canal_id == destino.id:
            try:
                mensagem = await destino.fetch_message(loja.painel_mensagem_id)
                await mensagem.edit(embed=montar_painel(loja), view=PainelMestreView(loja))
                atualizou = True
            except discord.NotFound:
                pass
            except discord.HTTPException as erro:
                await interacao.response.send_message(
                    f"Nao consegui atualizar o painel no canal selecionado: {erro}",
                    ephemeral=True,
                )
                return

        if not atualizou:
            try:
                mensagem = await destino.send(embed=montar_painel(loja), view=PainelMestreView(loja))
            except discord.HTTPException as erro:
                await interacao.response.send_message(
                    f"Nao consegui publicar nesse canal. Verifique minhas permissoes de enviar mensagens e embeds. Erro: {erro}",
                    ephemeral=True,
                )
                return
            publicou = True

        async with Session() as sessao:
            loja_db = await pegar_loja(sessao, self.guild_id)
            loja_db.canal_vendas_id = destino.id
            if publicou:
                loja_db.painel_mensagem_id = mensagem.id
                loja_db.painel_canal_id = destino.id
            await sessao.commit()

        await interacao.response.edit_message(
            embed=discord.Embed(
                title="Loja publicada",
                description=f"Painel {'publicado' if publicou else 'atualizado'} em {destino.mention}.",
                color=config.COR_SUCESSO,
            ),
            view=await _com_icones(
                self.guild_id,
                PainelGestaoLojaView(self.guild_id),
            ),
        )


class SelecionarCanalLogsView(discord.ui.View):
    def __init__(self, guild_id: int) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.seletor_canal = discord.ui.ChannelSelect(
            placeholder="Escolha o canal de logs",
            channel_types=[discord.ChannelType.text, discord.ChannelType.news],
            min_values=1,
            max_values=1,
        )
        self.seletor_canal.callback = self.salvar
        self.add_item(self.seletor_canal)
        voltar = discord.ui.Button(label="‹  Voltar", style=discord.ButtonStyle.secondary, row=1)
        voltar.callback = self.voltar
        self.add_item(voltar)

    async def voltar(self, interacao: discord.Interaction) -> None:
        if not _interacao_autorizada(interacao, self.guild_id):
            await interacao.response.send_message("Sem permissao para usar este painel.", ephemeral=True)
            return
        await interacao.response.edit_message(
            embed=await montar_embed_gestao(self.guild_id),
            view=await _com_icones(
                self.guild_id,
                PainelGestaoLojaView(self.guild_id),
            ),
        )

    async def salvar(self, interacao: discord.Interaction) -> None:
        if not _interacao_autorizada(interacao, self.guild_id):
            await interacao.response.send_message("Sem permissao para alterar esta loja.", ephemeral=True)
            return
        canal = self.seletor_canal.values[0]
        async with Session() as sessao:
            loja = await pegar_loja(sessao, self.guild_id)
            loja.canal_logs_id = canal.id
            await sessao.commit()
        await interacao.response.edit_message(
            embed=discord.Embed(
                title="Canal de logs salvo",
                description=f"Os registros da loja serao enviados em {canal.mention}.",
                color=config.COR_SUCESSO,
            ),
            view=await _com_icones(
                self.guild_id,
                PainelGestaoLojaView(self.guild_id),
            ),
        )
