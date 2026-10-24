"""Menus, botoes e embeds usados pela lojinha."""

from __future__ import annotations

import logging
import json
import re
import unicodedata

import discord
from sqlalchemy import func, select

import config
import pix
from db import (
    Session,
    contar_estoque,
    marcar_cancelado,
    marcar_pago,
    obter_config_automacao,
    pegar_loja,
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
    ItemEstoque,
    Loja,
    Pedido,
    Produto,
    agora,
)
from utils import eh_admin, formatar_preco, parse_preco

log = logging.getLogger(__name__)

CATEGORIAS_BOTOES: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...] = (
    ("todos", "Todos os botões", (("*", "Aplicar a todos os botões"),)),
    (
        "comprador",
        "Painel do comprador",
        (
            ("ver catalogo", "Ver catálogo"),
            ("suporte", "Suporte"),
            ("abrir canal", "Abrir canal"),
            ("categorias", "Categorias"),
            ("produtos", "Produtos"),
            ("gerar pix", "Gerar PIX"),
            ("voltar", "Voltar"),
            ("voltar ao produto", "Voltar ao produto"),
            ("cancelar", "Cancelar"),
        ),
    ),
    (
        "gerenciamento",
        "Painel de gerenciamento",
        (
            ("painel", "Painel"),
            ("painel principal", "Painel principal"),
            ("produtos", "Produtos"),
            ("tickets", "Tickets"),
            ("boas vindas", "Boas-vindas"),
            ("automacoes", "Automações"),
            ("aparencia", "Aparência"),
            ("icones", "Ícones"),
            ("vendas", "Vendas"),
            ("sorteios", "Sorteios"),
            ("configurar", "Configurar"),
            ("configurar pix", "Configurar PIX"),
            ("publicar painel", "Publicar painel"),
            ("publicar loja", "Publicar loja"),
            ("publicar vitrine", "Publicar vitrine"),
            ("canal de registros", "Canal de registros"),
            ("voltar", "Voltar"),
        ),
    ),
    (
        "vendas",
        "Vendas e pedidos",
        (
            ("confirmar pagamento", "Confirmar pagamento"),
            ("cancelar pedido", "Cancelar pedido"),
            ("voltar", "Voltar"),
        ),
    ),
    (
        "tickets",
        "Tickets",
        (
            ("abrir ticket", "Abrir Ticket"),
            ("fechar ticket", "Fechar Ticket"),
            ("publicar painel", "Publicar painel"),
            ("voltar", "Voltar"),
        ),
    ),
    (
        "assinatura",
        "Assinatura",
        (
            ("assinar media", "Assinar Media"),
            ("assinar pro", "Assinar Pro"),
            ("ja paguei", "Já paguei"),
        ),
    ),
    (
        "estoque",
        "Produtos e estoque",
        (
            ("cadastrar produto", "Cadastrar produto"),
            ("alterar preco", "Alterar preço"),
            ("adicionar estoque", "Adicionar estoque"),
            ("remover unidade do estoque", "Remover unidade do estoque"),
            ("entrega automatica", "Entrega automática"),
            ("link uma unidade por linha", "Link · uma unidade por linha"),
            ("texto conteudo unico", "Texto · conteúdo único"),
            ("texto uma unidade por linha", "Texto · uma unidade por linha"),
            ("link reutilizavel", "Link reutilizável"),
            ("texto reutilizavel", "Texto reutilizável"),
            ("sim limpar", "Sim, limpar"),
            ("cancelar", "Cancelar"),
            ("voltar", "Voltar"),
        ),
    ),
    (
        "automacoes",
        "Automações e sorteios",
        (
            ("pedidos", "Pedidos"),
            ("sorteios", "Sorteios"),
            ("estoque baixo", "Estoque baixo"),
            ("boas vindas", "Boas-vindas"),
            ("participar", "Participar"),
            ("painel principal", "Painel principal"),
            ("voltar", "Voltar"),
        ),
    ),
    (
        "ajuda",
        "Ajuda e plataforma",
        (
            ("sou dono da loja", "Sou dono da loja"),
            ("quero comprar", "Quero comprar"),
            ("bom saber", "Bom saber"),
            ("enviar token do meu bot", "Enviar token do meu bot"),
            ("sim limpar", "Sim, limpar"),
            ("sim tirar o bot", "Sim, tirar o bot"),
            ("cancelar", "Cancelar"),
        ),
    ),
    (
        "icones",
        "Personalização de ícones",
        (
            ("botoes", "Botões · navegação"),
            ("emojis", "Emojis · navegação"),
            ("painel principal", "Painel principal"),
            ("voltar", "Voltar"),
        ),
    ),
    ("outros", "Novos botões", ()),
)

_BOTOES_DESCobertos: dict[str, dict[str, str]] = {}


def _chave_icone_categoria(categoria: str, acao: str) -> str:
    return f"{categoria}::{acao}"


def _identificador_botao(item: discord.ui.Button) -> str:
    return (
        item.custom_id
        or item.url
        or getattr(item.callback, "__name__", "sem identificador")
    )


def _chave_botao(item: discord.ui.Button) -> str:
    chave = _chave_rotulo_botao(item.label)
    if chave:
        return chave
    return f"botao {_identificador_botao(item)}".casefold()


def _rotulo_botao(item: discord.ui.Button) -> str:
    rotulo = _rotulo_sem_icone(item.label)
    if rotulo:
        return rotulo
    return f"Botão sem texto · {_identificador_botao(item)[:60]}"


def _categorias_disponiveis() -> tuple[tuple[str, str], ...]:
    categorias = {
        chave: rotulo
        for chave, rotulo, acoes in CATEGORIAS_BOTOES
        if acoes or chave in _BOTOES_DESCobertos
    }
    for categoria in _BOTOES_DESCobertos:
        categorias.setdefault(categoria, "Novos botões")
    return tuple(categorias.items())


class PersonalizarIconesView(discord.ui.View):
    def __init__(
        self,
        guild_id: int,
        icones: dict[str, str],
        emojis: list[discord.Emoji],
        *,
        categoria_selecionada: str = "todos",
        acao_pagina: int = 0,
        emoji_pagina: int = 0,
        acao_selecionada: str = "*",
    ) -> None:
        super().__init__(timeout=300)
        self.guild_id = guild_id
        self.icones = icones
        self.emojis = emojis
        self.categoria_selecionada = categoria_selecionada
        self.acao_pagina = acao_pagina
        self.emoji_pagina = emoji_pagina
        self.acao_selecionada = acao_selecionada

        self.seletor_categorias = discord.ui.Select(
            placeholder="Selecione o painel ou tipo de botão",
            options=[
                discord.SelectOption(
                    label=rotulo,
                    value=chave,
                    default=chave == categoria_selecionada,
                )
                for chave, rotulo, _ in CATEGORIAS_BOTOES
            ],
            row=0,
        )
        self.seletor_categorias.callback = self.selecionar_categoria
        self.add_item(self.seletor_categorias)

        self.seletor_acoes = discord.ui.Select(
            placeholder="Escolha qual botão personalizar",
            options=[discord.SelectOption(label="Aplicar a todos os botões", value="*")],
            row=1,
        )
        self.seletor_acoes.callback = self.selecionar_acao
        self.add_item(self.seletor_acoes)

        self.seletor_emojis = discord.ui.Select(
            placeholder="Escolha um emoji do servidor",
            options=[discord.SelectOption(label="Padrão (remover personalização)", value="padrao")],
            row=2,
        )
        self.seletor_emojis.callback = self.selecionar_emoji
        self.add_item(self.seletor_emojis)

        self.anterior_acao = discord.ui.Button(
            label="‹ Botões", style=discord.ButtonStyle.secondary, row=3
        )
        self.anterior_acao.callback = self.pagina_anterior_acao
        self.add_item(self.anterior_acao)
        self.proxima_acao = discord.ui.Button(
            label="Botões ›", style=discord.ButtonStyle.secondary, row=3
        )
        self.proxima_acao.callback = self.pagina_proxima_acao
        self.add_item(self.proxima_acao)
        self.anterior_emoji = discord.ui.Button(
            label="‹ Emojis", style=discord.ButtonStyle.secondary, row=3
        )
        self.anterior_emoji.callback = self.pagina_anterior_emoji
        self.add_item(self.anterior_emoji)
        self.proximo_emoji = discord.ui.Button(
            label="Emojis ›", style=discord.ButtonStyle.secondary, row=3
        )
        self.proximo_emoji.callback = self.pagina_proxima_emoji
        self.add_item(self.proximo_emoji)

        voltar = discord.ui.Button(
            label="‹ Painel principal", style=discord.ButtonStyle.secondary, row=4
        )

        async def voltar_painel(interacao: discord.Interaction) -> None:
            async with Session() as sessao:
                loja = await pegar_loja(sessao, self.guild_id)
            await interacao.response.edit_message(
                embed=montar_painel_admin(loja),
                view=PainelAdminView(loja),
            )

        voltar.callback = voltar_painel
        self.add_item(voltar)
        setattr(self, "_categoria_icones", "icones")
        self._atualizar_pagina_acoes()
        self._atualizar_pagina_emojis(None)
        _aplicar_icones_view(self, icones)

    def _acoes_categoria(self) -> tuple[tuple[str, str], ...]:
        acoes_padrao = next(
            (
                acoes
                for chave, _, acoes in CATEGORIAS_BOTOES
                if chave == self.categoria_selecionada
            ),
            (),
        )
        acoes = dict(acoes_padrao)
        for chave, rotulo in _BOTOES_DESCobertos.get(
            self.categoria_selecionada,
            {},
        ).items():
            acoes.setdefault(chave, rotulo)
        return tuple(acoes.items())

    def _atualizar_pagina_acoes(self) -> None:
        acoes = self._acoes_categoria()
        inicio = self.acao_pagina * 25
        self.seletor_categorias.options = [
            discord.SelectOption(
                label=rotulo,
                value=chave,
                default=chave == self.categoria_selecionada,
            )
            for chave, rotulo in _categorias_disponiveis()
        ]
        opcoes = []
        for chave, rotulo in acoes[inicio : inicio + 25]:
            valor = (
                "*"
                if self.categoria_selecionada == "todos"
                else _chave_icone_categoria(self.categoria_selecionada, chave)
            )
            opcoes.append(
                discord.SelectOption(
                    label=rotulo,
                    value=valor,
                    default=valor == self.acao_selecionada,
                )
            )
        self.seletor_acoes.options = opcoes
        rotulo_categoria = dict(_categorias_disponiveis())[self.categoria_selecionada]
        total_paginas = max(1, (len(acoes) + 24) // 25)
        self.seletor_acoes.placeholder = (
            f"{self.acao_pagina + 1}/{total_paginas} · {rotulo_categoria}: escolha um botão"
        )
        self.anterior_acao.disabled = self.acao_pagina == 0
        self.proxima_acao.disabled = (self.acao_pagina + 1) * 25 >= len(acoes)

    def _atualizar_pagina_emojis(self, guild: discord.Guild | None) -> None:
        if guild is not None:
            self.emojis = sorted(guild.emojis, key=lambda emoji: (emoji.name.casefold(), emoji.id))
        inicio = self.emoji_pagina * 24
        emoji_atual = self.icones.get(self.acao_selecionada)
        if emoji_atual is None and self.acao_selecionada != "*":
            chave_antiga = self.acao_selecionada.split("::", 1)[-1]
            emoji_atual = self.icones.get(chave_antiga, self.icones.get("*"))
        elif emoji_atual is None:
            emoji_atual = self.icones.get("*")
        opcoes = [
            discord.SelectOption(
                label="Padrão (remover personalização)",
                value="padrao",
                default=not emoji_atual,
            )
        ]
        opcoes.extend(
            discord.SelectOption(
                label=emoji.name[:100],
                value=str(emoji.id),
                emoji=emoji,
                default=emoji_atual == str(emoji),
            )
            for emoji in self.emojis[inicio : inicio + 24]
        )
        self.seletor_emojis.options = opcoes
        total_paginas = max(1, (len(self.emojis) + 23) // 24)
        self.seletor_emojis.placeholder = (
            f"{self.emoji_pagina + 1}/{total_paginas} · Escolha um emoji do servidor"
        )
        self.anterior_emoji.disabled = self.emoji_pagina == 0
        self.proximo_emoji.disabled = (self.emoji_pagina + 1) * 24 >= len(self.emojis)

    def _embed(self) -> discord.Embed:
        rotulo_categoria = dict(_categorias_disponiveis())[self.categoria_selecionada]
        acoes = dict(self._acoes_categoria())
        chave_acao = (
            self.acao_selecionada.split("::", 1)[-1]
            if self.acao_selecionada != "*"
            else "*"
        )
        rotulo = "Todos os botões" if chave_acao == "*" else acoes.get(chave_acao, "Botão")
        embed = discord.Embed(
            title="Personalizar botões",
            description=(
                "Escolha uma categoria, depois o botão e o emoji do servidor. "
                "A alteração é salva na hora; deixe em **Padrão** para restaurar o ícone original.\n\n"
                f"Categoria: **{rotulo_categoria}**\nBotão selecionado: **{rotulo}**"
            ),
            color=config.COR_PREMIUM,
        )
        embed.set_footer(text="Cada categoria pode ter seus próprios ícones; setas navegam pelos botões e emojis")
        return embed

    async def selecionar_categoria(self, interacao: discord.Interaction) -> None:
        self.categoria_selecionada = interacao.data["values"][0]
        self.acao_pagina = 0
        acoes = self._acoes_categoria()
        primeira_acao = acoes[0][0] if acoes else "*"
        self.acao_selecionada = (
            "*"
            if self.categoria_selecionada == "todos"
            else _chave_icone_categoria(self.categoria_selecionada, primeira_acao)
        )
        self._atualizar_pagina_acoes()
        self._atualizar_pagina_emojis(interacao.guild)
        await interacao.response.edit_message(embed=self._embed(), view=self)

    async def selecionar_acao(self, interacao: discord.Interaction) -> None:
        self.acao_selecionada = interacao.data["values"][0]
        self._atualizar_pagina_emojis(interacao.guild)
        await interacao.response.edit_message(embed=self._embed(), view=self)

    async def selecionar_emoji(self, interacao: discord.Interaction) -> None:
        if not eh_admin(interacao) or interacao.guild_id != self.guild_id:
            await interacao.response.send_message(
                "Somente administradores deste servidor podem personalizar os botões.",
                ephemeral=True,
            )
            return

        escolhido = interacao.data["values"][0]
        if escolhido == "padrao":
            if self.acao_selecionada == "*":
                self.icones.pop("*", None)
            elif (
                "*" in self.icones
                or self.icones.get(self.acao_selecionada.split("::", 1)[-1])
                or self.acao_selecionada.split("::", 1)[-1]
                in {"ver catalogo", "suporte", "gerar pix", "voltar"}
            ):
                self.icones[self.acao_selecionada] = ""
            else:
                self.icones.pop(self.acao_selecionada, None)
        else:
            emoji = interacao.guild.get_emoji(int(escolhido))
            if emoji is None:
                await interacao.response.send_message(
                    "Esse emoji não está mais disponível neste servidor. Atualize a seleção e tente de novo.",
                    ephemeral=True,
                )
                return
            self.icones[self.acao_selecionada] = str(emoji)

        await interacao.response.defer()
        async with Session() as sessao:
            loja = await pegar_loja(sessao, self.guild_id)
            loja.icones_botoes_json = json.dumps(
                self.icones,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            await sessao.commit()
            await sessao.refresh(loja)

        self.icones = _icones_da_loja(loja)
        view = PersonalizarIconesView(
            self.guild_id,
            self.icones,
            list(interacao.guild.emojis),
            categoria_selecionada=self.categoria_selecionada,
            acao_pagina=self.acao_pagina,
            emoji_pagina=self.emoji_pagina,
            acao_selecionada=self.acao_selecionada,
        )
        await interacao.edit_original_response(embed=view._embed(), view=view)

        if loja.painel_canal_id and loja.painel_mensagem_id:
            canal = interacao.client.get_channel(loja.painel_canal_id)
            if canal is None:
                try:
                    canal = await interacao.client.fetch_channel(loja.painel_canal_id)
                except discord.HTTPException:
                    log.warning(
                        "Nao consegui buscar a vitrine ao atualizar os icones da loja %s.",
                        self.guild_id,
                    )
                    return
            try:
                mensagem = await canal.fetch_message(loja.painel_mensagem_id)
                await mensagem.edit(embed=montar_painel(loja), view=PainelMestreView(loja))
            except discord.HTTPException:
                log.warning(
                    "Nao consegui atualizar a vitrine com os icones da loja %s.",
                    self.guild_id,
                )

    async def _trocar_pagina(
        self,
        interacao: discord.Interaction,
        *,
        acao_delta: int = 0,
        emoji_delta: int = 0,
    ) -> None:
        total_acoes = len(self._acoes_categoria())
        self.acao_pagina = max(
            0,
            min(
                max(0, (total_acoes - 1) // 25),
                self.acao_pagina + acao_delta,
            ),
        )
        total_emojis = len(interacao.guild.emojis) if interacao.guild else 0
        self.emoji_pagina = max(
            0,
            min(
                max(0, (total_emojis - 1) // 24),
                self.emoji_pagina + emoji_delta,
            ),
        )
        self._atualizar_pagina_acoes()
        self._atualizar_pagina_emojis(interacao.guild)
        await interacao.response.edit_message(embed=self._embed(), view=self)

    async def pagina_anterior_acao(self, interacao: discord.Interaction) -> None:
        await self._trocar_pagina(interacao, acao_delta=-1)

    async def pagina_proxima_acao(self, interacao: discord.Interaction) -> None:
        await self._trocar_pagina(interacao, acao_delta=1)

    async def pagina_anterior_emoji(self, interacao: discord.Interaction) -> None:
        await self._trocar_pagina(interacao, emoji_delta=-1)

    async def pagina_proxima_emoji(self, interacao: discord.Interaction) -> None:
        await self._trocar_pagina(interacao, emoji_delta=1)


class PersonalizarLojaModal(discord.ui.Modal, title="Personalizar identidade visual"):
    banner = discord.ui.TextInput(label="URL do Banner", placeholder="Cole o link da imagem (1920x1080 recomendado)", required=False)
    logo = discord.ui.TextInput(label="URL da Logo", placeholder="Cole o link da imagem quadrada", required=False)
    cor = discord.ui.TextInput(label="Cor Hexadecimal", placeholder="Ex: #7289DA", min_length=7, max_length=7, required=False)

    def __init__(self, loja: Loja, view_original: discord.ui.View):
        super().__init__()
        self.loja = loja
        self.view_original = view_original
        self.banner.default = loja.banner_url
        self.logo.default = loja.logo_url
        self.cor.default = loja.cor_hex or "#7289DA"

    async def on_submit(self, interacao: discord.Interaction) -> None:
        async with Session() as sessao:
            loja_db = await sessao.get(Loja, interacao.guild_id)
            if self.banner.value: loja_db.banner_url = self.banner.value
            if self.logo.value: loja_db.logo_url = self.logo.value
            if self.cor.value: loja_db.cor_hex = self.cor.value
            await sessao.commit()
        
        embed = discord.Embed(title="✅ Sucesso!", description="Identidade visual atualizada com sucesso!", color=config.COR_SUCESSO)
        view = discord.ui.View()
        setattr(view, "_categoria_icones", "gerenciamento")
        btn_voltar = discord.ui.Button(label="‹  VOLTAR", style=discord.ButtonStyle.secondary)
        async def cb_voltar(i: discord.Interaction):
            await i.response.edit_message(embed=montar_painel_admin(loja_db), view=PainelAdminView(loja_db))
        btn_voltar.callback = cb_voltar
        view.add_item(btn_voltar)
        await aplicar_icones_da_loja(interacao.guild_id, view)
        await interacao.response.edit_message(embed=embed, view=view)

class ConfigWelcomeModal(discord.ui.Modal, title="Configurar boas-vindas"):
    canal_id = discord.ui.TextInput(label="ID do Canal", placeholder="ID do canal onde as mensagens serão enviadas", min_length=17, max_length=20)
    mensagem = discord.ui.TextInput(label="Mensagem", style=discord.TextStyle.paragraph, placeholder="Use {user} para mencionar o usuário", default="Seja bem-vindo(a) ao servidor, {user}!")

    async def on_submit(self, interacao: discord.Interaction) -> None:
        if not self.canal_id.value.isdigit():
            return await interacao.response.send_message("ID do canal inválido.", ephemeral=True)
        
        async with Session() as sessao:
            from models import ConfigWelcome
            conf = await sessao.get(ConfigWelcome, interacao.guild_id)
            if not conf:
                conf = ConfigWelcome(guild_id=interacao.guild_id, canal_id=int(self.canal_id.value), mensagem=self.mensagem.value, ativo=True)
                sessao.add(conf)
            else:
                conf.canal_id = int(self.canal_id.value)
                conf.mensagem = self.mensagem.value
                conf.ativo = True
            await sessao.commit()
        
        await interacao.response.send_message(
            "✅ Boas-vindas configuradas. Para enviar mensagens, ligue essa automacao no painel de automacoes.",
            ephemeral=True,
        )
class ParticiparView(discord.ui.View):
    def __init__(self, gw_id: int):
        super().__init__(timeout=None)
        self.gw_id = gw_id
        setattr(self, "_categoria_icones", "automacoes")
    
    @discord.ui.button(label="◇  PARTICIPAR", style=discord.ButtonStyle.success, custom_id="gw:join")
    async def entrar(self, i: discord.Interaction, b: discord.ui.Button):
        from models import Giveaway, GiveawayParticipant

        async with Session() as sessao:
            gw = await sessao.scalar(
                select(Giveaway).where(
                    Giveaway.guild_id == i.guild_id,
                    Giveaway.mensagem_id == i.message.id,
                    Giveaway.finalizado.is_(False),
                    Giveaway.termina_em > agora(),
                )
            )
            if not gw:
                return await i.response.send_message(
                    "Este sorteio já terminou ou não está mais disponível.",
                    ephemeral=True,
                )

            ja_existe = await sessao.scalar(
                select(GiveawayParticipant).where(
                    GiveawayParticipant.giveaway_id == gw.id,
                    GiveawayParticipant.usuario_id == i.user.id,
                )
            )
            if ja_existe:
                return await i.response.send_message("Você já está participando deste sorteio!", ephemeral=True)

            p = GiveawayParticipant(giveaway_id=gw.id, usuario_id=i.user.id)
            sessao.add(p)
            await sessao.commit()
        await i.response.send_message("✅ Você entrou no sorteio!", ephemeral=True)

class CriarGiveawayModal(discord.ui.Modal, title="Criar novo sorteio"):
    premio = discord.ui.TextInput(label="Prêmio", placeholder="Ex: Conta VIP 30 dias", min_length=3, max_length=100)
    qtd_vencedores = discord.ui.TextInput(label="Qtd. Vencedores", placeholder="Ex: 1", default="1")
    tempo = discord.ui.TextInput(label="Duração (minutos)", placeholder="Ex: 60 (para 1 hora)", default="60")

    async def on_submit(self, interacao: discord.Interaction) -> None:
        if not eh_admin(interacao):
            await interacao.response.send_message(
                "Somente administradores deste servidor podem criar sorteios.",
                ephemeral=True,
            )
            return
        if (
            interacao.guild_id is None
            or interacao.channel_id is None
            or not isinstance(interacao.channel, discord.abc.Messageable)
        ):
            await interacao.response.send_message(
                "Não consegui identificar um canal de texto deste servidor para publicar o sorteio.",
                ephemeral=True,
            )
            return

        try:
            vencedores_qtd = int(self.qtd_vencedores.value)
            duracao_minutos = int(self.tempo.value)
        except ValueError:
            await interacao.response.send_message(
                "Informe números válidos para vencedores e duração.",
                ephemeral=True,
            )
            return

        if vencedores_qtd < 1 or duracao_minutos < 1:
            await interacao.response.send_message(
                "A quantidade de vencedores e a duração devem ser maiores que zero.",
                ephemeral=True,
            )
            return
        if vencedores_qtd > 100 or duracao_minutos > 525600:
            await interacao.response.send_message(
                "O limite é de 100 vencedores e 525600 minutos (1 ano) por sorteio.",
                ephemeral=True,
            )
            return

        from datetime import timedelta
        from models import Giveaway

        termina_em = agora() + timedelta(minutes=duracao_minutos)
        await interacao.response.defer(ephemeral=True)

        async with Session() as sessao:
            giveaway = Giveaway(
                guild_id=interacao.guild_id,
                premio=self.premio.value,
                vencedores_qtd=vencedores_qtd,
                canal_id=interacao.channel_id,
                termina_em=termina_em
            )
            sessao.add(giveaway)
            await sessao.commit()
            await sessao.refresh(giveaway)

        embed = discord.Embed(
            title=f"◇  SORTEIO: {self.premio.value}",
            description=f"Clique no botão abaixo para participar!\n\n"
                        f"VENCEDORES  •  {vencedores_qtd}\n"
                        f"ENCERRA  •  <t:{int(termina_em.timestamp())}:R>",
            color=0xFFD700
        )

        try:
            msg = await interacao.channel.send(
                embed=embed,
                view=await aplicar_icones_da_loja(
                    interacao.guild_id,
                    ParticiparView(giveaway.id),
                ),
            )
        except discord.HTTPException:
            async with Session() as sessao:
                giveaway_db = await sessao.get(Giveaway, giveaway.id)
                if giveaway_db is not None:
                    await sessao.delete(giveaway_db)
                    await sessao.commit()
            log.exception("Nao consegui publicar o sorteio %s.", giveaway.id)
            await interacao.followup.send(
                "Não consegui publicar o sorteio neste canal. Confira minhas permissões e tente novamente.",
                ephemeral=True,
            )
            return

        async with Session() as sessao:
            gw_db = await sessao.get(Giveaway, giveaway.id)
            if gw_db is None:
                raise RuntimeError(f"Sorteio {giveaway.id} desapareceu antes de salvar a mensagem.")
            gw_db.mensagem_id = msg.id
            await sessao.commit()

        configuracao = await obter_config_automacao(interacao.guild_id)
        resultado_automacao = (
            "O encerramento automático está **ativo**."
            if configuracao.finalizar_sorteios
            else "Atenção: o encerramento automático está **desativado**. Ative **Sorteios** no painel de Automações para anunciar o resultado no fim."
        )
        await interacao.followup.send(
            f"✅ Sorteio publicado com sucesso. {resultado_automacao}",
            ephemeral=True,
        )
    nome = discord.ui.TextInput(label="Nome do Produto", placeholder="Ex: Conta de FF Nivel 50", min_length=3, max_length=100)
    preco = discord.ui.TextInput(label="Preço (R$)", placeholder="Ex: 19,90", min_length=1)
    descricao = discord.ui.TextInput(label="Descrição", style=discord.TextStyle.paragraph, placeholder="Detalhes do que você está vendendo...", required=False, max_length=1500)
    categoria_id = discord.ui.TextInput(label="ID da Categoria (opcional)", placeholder="Deixe vazio se não souber", required=False)

    async def on_submit(self, interacao: discord.Interaction) -> None:
        try:
            preco_centavos = parse_preco(self.preco.value)
        except ValueError as erro:
            await interacao.response.send_message(f"⚠️ {erro}", ephemeral=True)
            return

        cat_id = None
        if self.categoria_id.value.isdigit():
            cat_id = int(self.categoria_id.value)

        async with Session() as sessao:
            produto = Produto(
                guild_id=interacao.guild_id,
                categoria_id=cat_id,
                nome=self.nome.value,
                descricao=self.descricao.value,
                preco_centavos=preco_centavos,
            )
            sessao.add(produto)
            await sessao.commit()
            await sessao.refresh(produto)

        await interacao.response.send_message(f"✅ Produto **{self.nome.value}** (#{produto.id}) criado com sucesso!", ephemeral=True)

class CriarCategoriaModal(discord.ui.Modal, title="Nova categoria"):
    nome = discord.ui.TextInput(label="Nome da Categoria", placeholder="Ex: Contas, Diamantes, Skins...", min_length=2, max_length=100)
    ordem = discord.ui.TextInput(label="Ordem de Exibição", placeholder="Ex: 1 (menor aparece primeiro)", default="0", required=False)

    async def on_submit(self, interacao: discord.Interaction) -> None:
        ordem_int = 0
        if self.ordem.value.isdigit():
            ordem_int = int(self.ordem.value)

        async with Session() as sessao:
            categoria = Categoria(guild_id=interacao.guild_id, nome=self.nome.value, ordem=ordem_int)
            sessao.add(categoria)
            await sessao.commit()
            await sessao.refresh(categoria)

        await interacao.response.send_message(f"✅ Categoria **{self.nome.value}** (#{categoria.id}) criada!", ephemeral=True)

class AdicionarEstoqueModal(discord.ui.Modal, title="Adicionar ao estoque"):
    def __init__(self, produto_id: int, modo: str, tipo: str) -> None:
        super().__init__()
        self.produto_id = produto_id
        self.modo = modo
        self.tipo = tipo
        self.conteudo = discord.ui.TextInput(
            label="Itens do Estoque",
            style=discord.TextStyle.paragraph,
            placeholder="Cole os itens aqui. Se for 'Cada linha = 1 un', separe por quebra de linha.",
            min_length=1
        )
        self.add_item(self.conteudo)

    async def on_submit(self, interacao: discord.Interaction) -> None:
        await interacao.response.defer(ephemeral=True)
        from db import adicionar_estoque
        
        itens = []
        if self.modo == "linha":
            itens = [linha.strip() for linha in self.conteudo.value.split("\n") if linha.strip()]
        else:
            itens = [self.conteudo.value.strip()]

        total = await adicionar_estoque(interacao.guild_id, self.produto_id, itens, self.tipo)
        await interacao.followup.send(f"✅ Adicionado(s) **{len(itens)}** item(ns) ao estoque. Total agora: **{total}**.", ephemeral=True)

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
    cor = _cor_do_painel(loja)

    embed = discord.Embed(
        title=loja.nome,
        description=(
            f"**Bem-vindo(a)!**\n"
            f"Explore nosso catálogo, confira os detalhes dos produtos e fale com o suporte "
            f"sempre que precisar.\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Catálogo organizado · Atendimento disponível"
        ),
        color=cor,
    )

    if loja.banner_url:
        embed.set_image(url=loja.banner_url)
    
    if loja.logo_url:
        embed.set_thumbnail(url=loja.logo_url)

    embed.set_footer(text=f"{loja.nome}  •  Uma compra simples e segura")
    return embed


def _cor_do_painel(loja: Loja | None) -> int:
    if loja is not None and loja.cor_hex:
        try:
            return int(loja.cor_hex.removeprefix("#"), 16)
        except ValueError:
            log.warning("Cor personalizada invalida para a loja %s.", loja.guild_id)
    return config.COR_PREMIUM


def _chave_rotulo_botao(rotulo: str | None) -> str:
    texto = unicodedata.normalize("NFKD", rotulo or "")
    texto = "".join(caractere for caractere in texto if not unicodedata.combining(caractere))
    texto = re.sub(r"^[^a-zA-Z0-9]+", "", texto).casefold()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", texto).split())


def _rotulo_sem_icone(rotulo: str | None) -> str:
    texto = rotulo or ""
    return re.sub(r"^[^a-zA-Z0-9À-ÿ]+", "", texto).strip()


def _icones_da_loja(loja: Loja | None) -> dict[str, str]:
    if loja is None:
        return {}

    icones: dict[str, str] = {}
    if loja.icones_botoes_json:
        try:
            dados = json.loads(loja.icones_botoes_json)
        except json.JSONDecodeError:
            log.warning("Configuracao de icones invalida para a loja %s.", loja.guild_id)
        else:
            if isinstance(dados, dict):
                icones.update(
                    {
                        str(chave): valor
                        for chave, valor in dados.items()
                        if isinstance(chave, str) and isinstance(valor, str)
                    }
                )
            else:
                log.warning("Configuracao de icones invalida para a loja %s.", loja.guild_id)

    icones_legados = {
        "icone_catalogo": (loja.icone_catalogo, ("ver catalogo", "painel", "produtos")),
        "icone_suporte": (loja.icone_suporte, ("suporte", "tickets")),
        "icone_compra": (loja.icone_compra, ("gerar pix", "vendas")),
        "icone_voltar": (loja.icone_voltar, ("voltar",)),
    }
    for chave_legada, (emoji, acoes) in icones_legados.items():
        if emoji:
            icones[chave_legada] = emoji
            for acao in acoes:
                icones.setdefault(acao, emoji)
    return icones


def _aplicar_icones_view(view: discord.ui.View, icones: dict[str, str]) -> None:
    categoria = getattr(view, "_categoria_icones", None)
    if categoria is None:
        nome_classe = type(view).__name__
        modulo = type(view).__module__
        if nome_classe in {"PainelAdminView", "PainelGestaoLojaView"}:
            categoria = "gerenciamento"
        elif nome_classe in {
            "PainelMestreView",
            "AbrirLojaView",
            "SelecionarProduto",
            "SelecionarCategoria",
            "ConfirmarCompra",
            "PagamentoGeradoView",
        }:
            categoria = "comprador"
        elif nome_classe == "AprovacaoPedidoView":
            categoria = "vendas"
        elif nome_classe.startswith("Ticket"):
            categoria = "tickets"
        elif nome_classe == "AssinarView":
            categoria = "assinatura"
        elif nome_classe == "PainelAutomacaoView":
            categoria = "automacoes"
        elif nome_classe == "AjudaView":
            categoria = "ajuda"
        elif modulo.endswith(".ticket"):
            categoria = "tickets"
        elif modulo.endswith(".assinatura"):
            categoria = "assinatura"
        elif modulo.endswith(".automacao"):
            categoria = "automacoes"
        elif modulo.endswith(".estoque"):
            categoria = "estoque"
        elif modulo.endswith(".gestao_loja"):
            categoria = (
                "estoque"
                if nome_classe.startswith(("Formato", "SelecionarProduto"))
                else "gerenciamento"
            )
        elif modulo.endswith((".ajuda", ".master", ".whitelabel")):
            categoria = "ajuda"
        elif modulo.endswith(".loja"):
            categoria = "gerenciamento"

    if categoria is None:
        categoria = "outros"
    setattr(view, "_categoria_icones", categoria)

    for item in view.children:
        if not isinstance(item, discord.ui.Button):
            continue
        chave = _chave_botao(item)
        _BOTOES_DESCobertos.setdefault(categoria, {}).setdefault(
            chave,
            _rotulo_botao(item),
        )
        chave_categoria = _chave_icone_categoria(categoria, chave)
        if chave_categoria in icones:
            emoji = icones[chave_categoria]
        elif chave in icones:
            emoji = icones[chave]
        else:
            emoji = icones.get("*")
        if emoji:
            item.label = _rotulo_sem_icone(item.label)
            item.emoji = discord.PartialEmoji.from_str(emoji)


async def aplicar_icones_da_loja(
    guild_id: int,
    view: discord.ui.View,
) -> discord.ui.View:
    async with Session() as sessao:
        loja = await pegar_loja(sessao, guild_id)
    _aplicar_icones_view(view, _icones_da_loja(loja))
    return view


def _configurar_icone(
    botao: discord.ui.Button,
    rotulo: str,
    padrao: str,
) -> None:
    botao.label = f"{padrao}  {rotulo}"
    botao.emoji = None


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
# Aprovacao de pedidos (view persistente)
# --------------------------------------------------------------------------- #
class AprovacaoPedidoView(discord.ui.View):
    def __init__(self, pedido_id: int) -> None:
        super().__init__(timeout=None)
        self.pedido_id = pedido_id

    @discord.ui.button(label="✓  CONFIRMAR PAGAMENTO", style=discord.ButtonStyle.success, custom_id="pedido:pagar")
    async def confirmar(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        if not eh_admin(interacao):
            await interacao.response.send_message("Somente administradores podem confirmar pagamentos.", ephemeral=True)
            return
        await interacao.response.defer()
        pedido, erro = await marcar_pago(self.pedido_id, interacao.user.id)
        if erro or pedido is None:
            await interacao.followup.send(f"⚠️ {erro or 'Pedido nao encontrado.'}", ephemeral=True)
            return
        await atualizar_card_pedido(interacao.client, pedido)
        resultado, item = await entregar_pedido(interacao.client, pedido)
        linhas = [f"✅ Pagamento confirmado por {interacao.user.mention}.", f"<@{pedido.comprador_id}>, seu pedido foi aprovado."]
        descricao = descrever_resultado(resultado)
        if descricao: linhas.append(descricao)
        await interacao.followup.send("\n".join(linhas))
        if resultado == DM_FALHOU and item is not None:
            await enviar_aviso_de_dm_falhou(interacao, item)
        await registrar_log(interacao.client, pedido.guild_id, f"Pedido **#{pedido.id}** confirmado. {descricao}")

    @discord.ui.button(label="×  CANCELAR PEDIDO", style=discord.ButtonStyle.danger, custom_id="pedido:cancelar")
    async def cancelar(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        if not eh_admin(interacao):
            await interacao.response.send_message("Somente administradores podem cancelar pedidos.", ephemeral=True)
            return
        await interacao.response.defer()
        pedido, erro = await marcar_cancelado(self.pedido_id, interacao.user.id)
        if erro or pedido is None:
            await interacao.followup.send(f"⚠️ {erro or 'Pedido nao encontrado.'}", ephemeral=True)
            return
        await atualizar_card_pedido(interacao.client, pedido)
        await interacao.followup.send(f"✖️ Pedido **#{pedido.id}** cancelado.")
        await registrar_log(interacao.client, pedido.guild_id, f"✖️ Pedido **#{pedido.id}** cancelado.")


# --------------------------------------------------------------------------- #
# Fluxo de compra
# --------------------------------------------------------------------------- #
def _adicionar_botao_suporte(
    view: discord.ui.View,
    guild_id: int,
    embed_retorno: discord.Embed,
    view_retorno: discord.ui.View,
    cor: int,
    icones: dict[str, str | None] | None = None,
) -> None:
    configuracao_icones = icones or _icones_da_loja(None)
    botao = discord.ui.Button(style=discord.ButtonStyle.secondary)
    _configurar_icone(botao, "Suporte", "◦")

    async def abrir_suporte(interacao: discord.Interaction) -> None:
        async with Session() as sessao:
            from models import ConfigTicket

            configuracao = await sessao.get(ConfigTicket, guild_id)
            canal_id = configuracao.painel_canal_id if configuracao else None

        embed = discord.Embed(
            title="Suporte",
            description=(
                f"Fale com a equipe da loja em <#{canal_id}>."
                if canal_id
                else "O canal de suporte ainda não foi configurado nesta loja."
            ),
            color=cor,
        )
        painel_suporte = discord.ui.View(timeout=300)
        setattr(painel_suporte, "_categoria_icones", "comprador")
        if canal_id:
            painel_suporte.add_item(
                discord.ui.Button(
                    label="↗  Abrir canal",
                    style=discord.ButtonStyle.link,
                    url=f"https://discord.com/channels/{guild_id}/{canal_id}",
                )
            )

        voltar = discord.ui.Button(style=discord.ButtonStyle.secondary)
        _configurar_icone(voltar, "Voltar", "‹")

        async def voltar_callback(i: discord.Interaction) -> None:
            embed_atual = getattr(view_retorno, "embed", embed_retorno)
            await i.response.edit_message(embed=embed_atual, view=view_retorno)

        voltar.callback = voltar_callback
        painel_suporte.add_item(voltar)
        _aplicar_icones_view(painel_suporte, configuracao_icones)
        await interacao.response.edit_message(embed=embed, view=painel_suporte)

    botao.callback = abrir_suporte
    view.add_item(botao)
    _aplicar_icones_view(view, configuracao_icones)


def _embed_lista_produtos(nome_loja: str, produtos: list[Produto], cor: int) -> discord.Embed:
    embed = discord.Embed(
        title=nome_loja,
        description="Selecione um produto para ver os detalhes e as opções de compra.",
        color=cor,
    )
    embed.set_footer(text=f"{len(produtos)} produto(s) nesta seleção")
    return embed


class SelecionarProduto(discord.ui.View):
    def __init__(
        self,
        guild_id: int,
        produtos: list[Produto],
        categorias: SelecionarCategoria,
        nome_loja: str,
        cor: int,
        icones: dict[str, str | None] | None = None,
    ) -> None:
        super().__init__(timeout=300)
        setattr(self, "_categoria_icones", "comprador")
        self.guild_id = guild_id
        self.produtos = {produto.id: produto for produto in produtos}
        self.categorias = categorias
        self.nome_loja = nome_loja
        self.cor = cor
        self.icones = icones or _icones_da_loja(None)
        self.embed = _embed_lista_produtos(nome_loja, produtos, cor)

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

        voltar = discord.ui.Button(style=discord.ButtonStyle.secondary)
        _configurar_icone(voltar, "Categorias", "‹")

        async def voltar_categorias(interacao: discord.Interaction) -> None:
            await interacao.response.edit_message(
                embed=self.categorias.embed,
                view=self.categorias,
            )

        voltar.callback = voltar_categorias
        self.add_item(voltar)
        _adicionar_botao_suporte(self, guild_id, self.embed, self, cor, self.icones)
        _aplicar_icones_view(self, self.icones)

    async def escolher(self, interacao: discord.Interaction) -> None:
        produto = self.produtos.get(int(interacao.data["values"][0]))
        if produto is None:
            await interacao.response.edit_message(
                embed=discord.Embed(
                    title="Produto indisponível",
                    description="Este produto não está mais disponível. Volte às categorias e tente novamente.",
                    color=config.COR_ALERTA,
                ),
                view=self.categorias,
            )
            return

        embed = _montar_embed_produto(produto, self.cor)
        esgotado = False
        if produto.estoque_limitado:
            disponivel = await contar_estoque(self.guild_id, produto.id)
            esgotado = disponivel <= 0
            embed.add_field(name="Estoque", value="Esgotado" if esgotado else f"**{disponivel}** disponível(is)", inline=True)
        else:
            tipo_quantidade = (
                "Ilimitado · entrega automática"
                if produto.entrega_infinita and produto.tipo_entrega_infinita
                else "Ilimitado · entrega manual"
            )
            embed.add_field(name="Estoque", value=tipo_quantidade, inline=True)

        await interacao.response.edit_message(
            embed=embed,
            view=ConfirmarCompra(
                self.guild_id,
                produto,
                self,
                embed,
                esgotado,
                self.icones,
            ),
        )


def _montar_embed_produto(produto: Produto, cor: int) -> discord.Embed:
    embed = discord.Embed(
        title=produto.nome,
        description=(produto.descricao or "Sem descrição.")[:1500],
        color=cor,
    )
    embed.add_field(name="Preço", value=f"**{formatar_preco(produto.preco_centavos)}**", inline=True)
    embed.set_footer(text=f"Produto #{produto.id}")
    return embed


class SelecionarCategoria(discord.ui.View):
    def __init__(
        self,
        guild_id: int,
        categorias: list[Categoria],
        contagem: dict[int, int],
        sem_categoria: int,
        nome_loja: str,
        cor: int,
        icones: dict[str, str | None] | None = None,
    ) -> None:
        super().__init__(timeout=300)
        setattr(self, "_categoria_icones", "comprador")
        self.guild_id = guild_id
        self.nome_loja = nome_loja
        self.cor = cor
        self.icones = icones or _icones_da_loja(None)

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
                    label="Sem categoria",
                    value="sem",
                    description=f"{sem_categoria} produto(s) sem categoria",
                )
            )
        if not opcoes:
            opcoes.append(discord.SelectOption(label="Todos os produtos", value="todos"))

        menu = discord.ui.Select(placeholder="Escolha uma categoria...", options=opcoes[:25])
        menu.callback = self.escolher
        self.add_item(menu)

        self.embed = discord.Embed(
            title=nome_loja,
            description="Escolha uma categoria para explorar o catálogo.",
            color=cor,
        )
        self.embed.set_footer(text="Selecione uma opção para continuar")
        _adicionar_botao_suporte(self, guild_id, self.embed, self, cor, self.icones)
        _aplicar_icones_view(self, self.icones)

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
            await interacao.response.edit_message(
                embed=discord.Embed(
                    title="Nenhum produto nesta categoria",
                    description="Escolha outra categoria para continuar.",
                    color=self.cor,
                ),
                view=self,
            )
            return

        embed = _embed_lista_produtos(self.nome_loja, produtos, self.cor)
        if len(produtos) == 25:
            embed.set_footer(text="Mostrando os 25 primeiros produtos desta categoria")

        await interacao.response.edit_message(
            embed=embed,
            view=SelecionarProduto(
                self.guild_id,
                produtos,
                self,
                self.nome_loja,
                self.cor,
                self.icones,
            ),
        )


class ConfirmarCompra(discord.ui.View):
    def __init__(
        self,
        guild_id: int,
        produto: Produto,
        lista_produtos: SelecionarProduto,
        embed_produto: discord.Embed,
        esgotado: bool,
        icones: dict[str, str | None] | None = None,
    ) -> None:
        super().__init__(timeout=300)
        setattr(self, "_categoria_icones", "comprador")
        self.guild_id = guild_id
        self.produto_id = produto.id
        self.lista_produtos = lista_produtos
        self.embed_produto = embed_produto
        self.embed = embed_produto
        self.icones = icones or lista_produtos.icones

        if esgotado:
            self.embed_produto.add_field(
                name="Disponibilidade",
                value="Este produto está esgotado no momento.",
                inline=False,
            )
        else:
            comprar = discord.ui.Button(style=discord.ButtonStyle.primary)
            _configurar_icone(comprar, "Gerar PIX", "◦")
            comprar.callback = self.gerar_pix
            self.add_item(comprar)

        voltar = discord.ui.Button(style=discord.ButtonStyle.secondary)
        _configurar_icone(voltar, "Produtos", "‹")

        async def voltar_produtos(interacao: discord.Interaction) -> None:
            await interacao.response.edit_message(
                embed=self.lista_produtos.embed,
                view=self.lista_produtos,
            )

        voltar.callback = voltar_produtos
        self.add_item(voltar)
        _adicionar_botao_suporte(
            self,
            guild_id,
            self.embed_produto,
            self,
            self.lista_produtos.cor,
            self.icones,
        )
        _aplicar_icones_view(self, self.icones)

    async def gerar_pix(self, interacao: discord.Interaction) -> None:
        await interacao.response.defer()

        if interacao.guild_id != self.guild_id:
            await interacao.edit_original_response(
                embed=discord.Embed(
                    title="Loja inválida",
                    description="Este produto não pertence a esta loja.",
                    color=config.COR_ERRO,
                ),
                view=self,
            )
            return

        async with Session() as sessao:
            loja = await pegar_loja(sessao, self.guild_id)
            produto = await sessao.get(Produto, self.produto_id)

        if produto is None or produto.guild_id != self.guild_id or not produto.ativo:
            await interacao.edit_original_response(
                embed=discord.Embed(
                    title="Produto indisponível",
                    description="Este produto não está mais disponível.",
                    color=config.COR_ALERTA,
                ),
                view=self,
            )
            return

        if not loja.pix_chave:
            await interacao.edit_original_response(
                embed=discord.Embed(
                    title="PIX indisponível",
                    description="Esta loja ainda não configurou os recebimentos. Avise o responsável pela loja.",
                    color=config.COR_ALERTA,
                ),
                view=self,
            )
            return

        if produto.estoque_limitado:
            if await contar_estoque(self.guild_id, produto.id) <= 0:
                await interacao.edit_original_response(
                    embed=discord.Embed(
                        title="Produto esgotado",
                        description="Este produto acabou. Volte ao catálogo para escolher outro.",
                        color=config.COR_ALERTA,
                    ),
                    view=self,
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
            await interacao.edit_original_response(
                embed=discord.Embed(
                    title="Não foi possível gerar o PIX",
                    description=f"{erro}\nAvise o responsável pela loja para revisar os recebimentos.",
                    color=config.COR_ERRO,
                ),
                view=self,
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
                mensagem = await canal.send(
                    embed=montar_card(pedido),
                    view=await aplicar_icones_da_loja(
                        self.guild_id,
                        AprovacaoPedidoView(pedido.id),
                    ),
                )
                async with Session() as sessao:
                    registro = await sessao.get(Pedido, pedido.id)
                    registro.mensagem_id = mensagem.id
                    await sessao.commit()
            except discord.HTTPException:
                log.warning("Nao consegui publicar o card do pedido %s.", pedido.id)

        embed_pix = discord.Embed(
            title=f"Pedido #{pedido.id} · PIX gerado",
            description=(
                f"**{produto.nome}**\n"
                f"Valor: **{formatar_preco(produto.preco_centavos)}**\n\n"
                f"PIX copia e cola:\n```text\n{payload}\n```"
            ),
            color=config.COR_SUCESSO
        )
        embed_pix.set_footer(text="Pague e envie o comprovante para o responsável pela loja.")
        
        # Gera o QR Code em uma thread separada para não travar o bot (CPU-bound)
        loop = interacao.client.loop
        buffer = await loop.run_in_executor(None, pix.gerar_qr_png, payload)
        
        await interacao.edit_original_response(
            embed=embed_pix,
            attachments=[discord.File(buffer, filename=f"pix_pedido_{pedido.id}.png")],
            view=PagamentoGeradoView(embed_pix, self),
        )


class PagamentoGeradoView(discord.ui.View):
    def __init__(
        self,
        embed_pix: discord.Embed,
        view_produto: ConfirmarCompra,
    ) -> None:
        super().__init__(timeout=300)
        setattr(self, "_categoria_icones", "comprador")
        self.embed_pix = embed_pix
        self.view_produto = view_produto
        self.embed = embed_pix

        voltar = discord.ui.Button(style=discord.ButtonStyle.secondary)
        _configurar_icone(
            voltar,
            "Voltar ao produto",
            "‹",
        )

        async def voltar_produto(interacao: discord.Interaction) -> None:
            await interacao.response.edit_message(
                embed=self.view_produto.embed_produto,
                attachments=[],
                view=self.view_produto,
            )

        voltar.callback = voltar_produto
        self.add_item(voltar)
        _adicionar_botao_suporte(
            self,
            view_produto.guild_id,
            embed_pix,
            self,
            view_produto.lista_produtos.cor,
            view_produto.icones,
        )
        _aplicar_icones_view(self, view_produto.icones)


# --------------------------------------------------------------------------- #
# Painel Administrativo Central (O Coracao da Gestao)
# --------------------------------------------------------------------------- #
class PainelAdminView(discord.ui.View):
    """O painel onde o dono faz TUDO: add produtos, estoque, configurar, publicar."""
    def __init__(self, loja: Loja | None) -> None:
        super().__init__(timeout=None)
        setattr(self, "_categoria_icones", "gerenciamento")
        self.loja = loja
        icones = _icones_da_loja(loja)
        configuracao_botoes = {
            "admin:loja": ("Painel", "⌂"),
            "admin:gestao_loja": ("Produtos", "◈"),
            "admin:ticket": ("Tickets", "◦"),
            "admin:extrato": ("Vendas", "↗"),
        }
        for botao in self.children:
            configuracao = configuracao_botoes.get(botao.custom_id)
            if configuracao is not None:
                rotulo, padrao = configuracao
                _configurar_icone(botao, rotulo, padrao)
        _aplicar_icones_view(self, icones)

    @discord.ui.button(label="⌂  Painel", style=discord.ButtonStyle.secondary, custom_id="admin:loja", row=0)
    async def minha_loja(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        if not eh_admin(interacao):
            await interacao.response.send_message(
                "Somente administradores podem acessar esta visão geral.",
                ephemeral=True,
            )
            return

        await interacao.response.defer()
        async with Session() as sessao:
            from sqlalchemy import func

            loja = await pegar_loja(sessao, interacao.guild_id)
            consultas = select(
                select(func.count())
                .select_from(Categoria)
                .where(Categoria.guild_id == interacao.guild_id)
                .scalar_subquery(),
                select(func.count())
                .select_from(Produto)
                .where(Produto.guild_id == interacao.guild_id)
                .scalar_subquery(),
                select(func.count())
                .select_from(ItemEstoque)
                .where(
                    ItemEstoque.guild_id == interacao.guild_id,
                    ItemEstoque.entregue.is_(False),
                )
                .scalar_subquery(),
                select(func.count())
                .select_from(Pedido)
                .where(
                    Pedido.guild_id == interacao.guild_id,
                    Pedido.status == STATUS_PAGO,
                )
                .scalar_subquery(),
            )
            categorias, produtos, estoque, pedidos_pagos = (
                await sessao.execute(consultas)
            ).one()

        embed = discord.Embed(
            title="Visão geral",
            description=f"Indicadores atualizados de **{loja.nome}**.",
            color=_cor_do_painel(loja),
        )
        embed.add_field(name="Categorias", value=f"**{categorias or 0}**", inline=True)
        embed.add_field(name="Produtos", value=f"**{produtos or 0}**", inline=True)
        embed.add_field(name="Unidades disponíveis", value=f"**{estoque or 0}**", inline=True)
        embed.add_field(name="Vendas aprovadas", value=f"**{pedidos_pagos or 0}**", inline=True)
        embed.set_footer(text="Dados atualizados ao abrir esta tela")
        
        view = discord.ui.View()
        setattr(view, "_categoria_icones", "gerenciamento")
        btn_voltar = discord.ui.Button(label="‹  Voltar", style=discord.ButtonStyle.secondary)
        
        async def cb_voltar(i: discord.Interaction):
            async with Session() as sessao:
                loja_atual = await pegar_loja(sessao, i.guild_id)
            await i.response.edit_message(embed=montar_painel_admin(loja_atual), view=PainelAdminView(loja_atual))
        
        btn_voltar.callback = cb_voltar
        view.add_item(btn_voltar)
        _aplicar_icones_view(view, _icones_da_loja(loja))
        await interacao.edit_original_response(embed=embed, view=view)

    @discord.ui.button(label="◦  Tickets", style=discord.ButtonStyle.secondary, custom_id="admin:ticket", row=1)
    async def ticket(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        if not eh_admin(interacao): return
        
        embed = discord.Embed(
            title="Configuração de tickets",
            description="Selecione a categoria onde os tickets serão abertos e publique o painel.",
            color=config.COR_PREMIUM
        )
        view = discord.ui.View()
        setattr(view, "_categoria_icones", "tickets")
        
        # Filtra categorias do servidor
        categorias = [c for c in interacao.guild.categories][:25]
        if categorias:
            opcoes = [discord.SelectOption(label=c.name[:100], value=str(c.id)) for c in categorias]
            select_cat = discord.ui.Select(placeholder="Escolha a categoria...", options=opcoes)
            
            async def cb_select(i: discord.Interaction):
                cat_id = int(i.data["values"][0])
                async with Session() as sessao:
                    from models import ConfigTicket
                    conf = await sessao.get(ConfigTicket, i.guild_id)
                    if not conf:
                        conf = ConfigTicket(guild_id=i.guild_id, categoria_id=cat_id)
                        sessao.add(conf)
                    else:
                        conf.categoria_id = cat_id
                    await sessao.commit()
                
                embed_sucesso = discord.Embed(
                    title="✅ Categoria Definida",
                    description=f"A categoria de tickets foi definida com sucesso!",
                    color=config.COR_SUCESSO
                )
                view_sucesso = discord.ui.View()
                setattr(view_sucesso, "_categoria_icones", "tickets")
                btn_voltar_sucesso = discord.ui.Button(label="‹  Voltar", style=discord.ButtonStyle.secondary)
                async def cb_voltar_sucesso(i2: discord.Interaction):
                    await i2.response.edit_message(embed=embed, view=view)
                btn_voltar_sucesso.callback = cb_voltar_sucesso
                view_sucesso.add_item(btn_voltar_sucesso)
                await aplicar_icones_da_loja(i.guild_id, view_sucesso)
                await i.response.edit_message(embed=embed_sucesso, view=view_sucesso)
            select_cat.callback = cb_select
            view.add_item(select_cat)

        btn_publicar = discord.ui.Button(label="↗  Publicar painel", style=discord.ButtonStyle.success)
        async def cb_pub(i):
            from cogs.ticket import TicketCog
            cog = i.client.get_cog("TicketCog")
            if cog: await cog.publicar_painel(i)
            else: await i.response.send_message("Sistema de ticket carregando...", ephemeral=True)
        btn_publicar.callback = cb_pub

        btn_voltar = discord.ui.Button(label="‹  Voltar", style=discord.ButtonStyle.secondary)
        async def cb_voltar(i: discord.Interaction):
            async with Session() as sessao:
                loja_atual = await pegar_loja(sessao, i.guild_id)
            await i.response.edit_message(embed=montar_painel_admin(loja_atual), view=PainelAdminView(loja_atual))
        btn_voltar.callback = cb_voltar
        
        view.add_item(btn_publicar)
        view.add_item(btn_voltar)
        await aplicar_icones_da_loja(interacao.guild_id, view)
        await interacao.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="⌁  Boas-vindas", style=discord.ButtonStyle.secondary, custom_id="admin:welcome", row=2)
    async def boas_vindas(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        if not eh_admin(interacao): return
        await interacao.response.send_modal(ConfigWelcomeModal())

    @discord.ui.button(label="⟳  Automações", style=discord.ButtonStyle.secondary, custom_id="admin:auto", row=1)
    async def automacoes(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        if not eh_admin(interacao): return
        from cogs.automacao import PainelAutomacaoView, montar_embed_automacoes

        await interacao.response.edit_message(
            embed=await montar_embed_automacoes(interacao.guild_id),
            view=await aplicar_icones_da_loja(
                interacao.guild_id,
                PainelAutomacaoView(interacao.guild_id),
            ),
        )

    @discord.ui.button(
        label="◈  Produtos",
        style=discord.ButtonStyle.primary,
        custom_id="admin:gestao_loja",
        row=0,
    )
    async def gestao_loja(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        if not eh_admin(interacao):
            await interacao.response.send_message(
                "Somente administradores podem gerenciar a loja.",
                ephemeral=True,
            )
            return
        from cogs.gestao_loja import PainelGestaoLojaView, montar_embed_gestao

        await interacao.response.edit_message(
            embed=await montar_embed_gestao(interacao.guild_id),
            view=await aplicar_icones_da_loja(
                interacao.guild_id,
                PainelGestaoLojaView(interacao.guild_id),
            ),
        )

    @discord.ui.button(label="✧  Aparência", style=discord.ButtonStyle.secondary, custom_id="admin:tema", row=2)
    async def customizar(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        if not eh_admin(interacao): return
        async with Session() as sessao:
            loja = await pegar_loja(sessao, interacao.guild_id)
        await interacao.response.send_modal(PersonalizarLojaModal(loja, self))

    @discord.ui.button(label="◈  Ícones", style=discord.ButtonStyle.secondary, custom_id="admin:icones", row=2)
    async def personalizar_icones(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        if interacao.guild is None or not eh_admin(interacao):
            await interacao.response.send_message(
                "Somente administradores podem personalizar os ícones.",
                ephemeral=True,
            )
            return
        async with Session() as sessao:
            loja = await pegar_loja(sessao, interacao.guild_id)
        view = PersonalizarIconesView(
            interacao.guild_id,
            _icones_da_loja(loja),
            list(interacao.guild.emojis),
        )
        await interacao.response.edit_message(embed=view._embed(), view=view)

    @discord.ui.button(label="↗  Vendas", style=discord.ButtonStyle.secondary, custom_id="admin:extrato", row=0)
    async def extrato(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        if not eh_admin(interacao): return
        async with Session() as sessao:
            pedidos = list((await sessao.scalars(select(Pedido).where(Pedido.guild_id == interacao.guild_id, Pedido.status == STATUS_PAGO).order_by(Pedido.pago_em.desc()).limit(10))).all())
            total_centavos = await sessao.scalar(select(func.sum(Pedido.preco_centavos)).where(Pedido.guild_id == interacao.guild_id, Pedido.status == STATUS_PAGO)) or 0
        
        embed = discord.Embed(
            title="Extrato de vendas", 
            description=f"Total acumulado: **{formatar_preco(total_centavos)}**\nAbaixo as últimas 10 vendas aprovadas.",
            color=config.COR_PREMIUM
        )
        
        if not pedidos:
            embed.description = "Nenhuma venda realizada ainda."
        else:
            for p in pedidos:
                data_formatada = p.pago_em.strftime('%d/%m/%Y %H:%M') if p.pago_em else '?'
                embed.add_field(
                    name=f"Pedido #{p.id} - {p.produto_nome}", 
                    value=f"Valor: `{formatar_preco(p.preco_centavos)}` | Data: `{data_formatada}`", 
                    inline=False
                )
        
        view = discord.ui.View()
        setattr(view, "_categoria_icones", "vendas")
        btn_voltar = discord.ui.Button(label="‹  Voltar", style=discord.ButtonStyle.secondary)
        async def cb_voltar(i: discord.Interaction):
            async with Session() as sessao:
                loja_atual = await pegar_loja(sessao, i.guild_id)
            await i.response.edit_message(embed=montar_painel_admin(loja_atual), view=PainelAdminView(loja_atual))
        btn_voltar.callback = cb_voltar
        view.add_item(btn_voltar)
        await interacao.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="◇  Sorteios", style=discord.ButtonStyle.secondary, custom_id="admin:giveaway", row=2)
    async def giveaway(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        if not eh_admin(interacao): return
        await interacao.response.send_modal(CriarGiveawayModal())

    @discord.ui.button(label="⚙︎  Configurar", style=discord.ButtonStyle.secondary, custom_id="admin:config", row=1)
    async def configuracoes(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        if not eh_admin(interacao): return
        embed = discord.Embed(
            title="Configurações gerais",
            description="Ajuste os canais, chave PIX e publique sua loja.",
            color=config.COR_PREMIUM
        )
        view = discord.ui.View()
        setattr(view, "_categoria_icones", "gerenciamento")
        
        btn_pix = discord.ui.Button(label="◦  Configurar PIX", style=discord.ButtonStyle.primary)
        async def cb_pix(i: discord.Interaction):
            from cogs.loja import TipoChaveView
            embed_pix = discord.Embed(
                title="Configuração de PIX",
                description="Escolha o tipo de chave PIX abaixo para configurar seus recebimentos.",
                color=config.COR_PREMIUM
            )
            view_pix = TipoChaveView()
            setattr(view_pix, "_categoria_icones", "gerenciamento")
            btn_voltar_pix = discord.ui.Button(label="‹  Voltar", style=discord.ButtonStyle.secondary)
            async def cb_voltar_pix(i2: discord.Interaction):
                await i2.response.edit_message(embed=embed, view=view)
            btn_voltar_pix.callback = cb_voltar_pix
            view_pix.add_item(btn_voltar_pix)
            await aplicar_icones_da_loja(i.guild_id, view_pix)
            await i.response.edit_message(embed=embed_pix, view=view_pix)
        btn_pix.callback = cb_pix

        btn_publicar = discord.ui.Button(label="↗  Publicar loja", style=discord.ButtonStyle.success)
        async def cb_pub(i: discord.Interaction):
            await i.response.defer(ephemeral=True)
            from cogs.loja import Loja as LojaCog
            cog = i.client.get_cog("Loja")
            if cog:
                # Chama a lógica de publicação manualmente para controlar a resposta
                async with Session() as sessao:
                    loja = await pegar_loja(sessao, i.guild_id)
                
                from views import PainelMestreView
                view_mestre = PainelMestreView(loja)
                destino = i.channel
                
                painel_editado = False
                if loja.painel_mensagem_id and loja.painel_canal_id == destino.id:
                    try:
                        msg = await destino.fetch_message(loja.painel_mensagem_id)
                        await msg.edit(embed=montar_painel(loja), view=view_mestre)
                        painel_editado = True
                    except: pass

                if not painel_editado:
                    msg = await destino.send(embed=montar_painel(loja), view=view_mestre)
                    async with Session() as sessao:
                        loja_db = await pegar_loja(sessao, i.guild_id)
                        loja_db.painel_mensagem_id = msg.id
                        loja_db.painel_canal_id = destino.id
                        await sessao.commit()
                
                embed_sucesso = discord.Embed(
                    title="✅ Sucesso!",
                    description=f"Painel da loja {'atualizado' if painel_editado else 'publicado'} com sucesso em {destino.mention}!",
                    color=config.COR_SUCESSO
                )
                view_sucesso = discord.ui.View()
                setattr(view_sucesso, "_categoria_icones", "gerenciamento")
                btn_voltar_sucesso = discord.ui.Button(label="‹  Voltar", style=discord.ButtonStyle.secondary)
                async def cb_voltar_sucesso(i2: discord.Interaction):
                    await i2.response.edit_message(embed=embed, view=view)
                btn_voltar_sucesso.callback = cb_voltar_sucesso
                view_sucesso.add_item(btn_voltar_sucesso)
                await aplicar_icones_da_loja(i.guild_id, view_sucesso)
                await i.edit_original_response(embed=embed_sucesso, view=view_sucesso)
            else:
                await i.followup.send("Erro ao carregar módulo de loja.", ephemeral=True)
        
        btn_publicar.callback = cb_pub
        
        btn_voltar = discord.ui.Button(label="‹  Voltar", style=discord.ButtonStyle.secondary)
        async def cb_voltar(i: discord.Interaction):
            async with Session() as sessao:
                loja_atual = await pegar_loja(sessao, i.guild_id)
            await i.response.edit_message(embed=montar_painel_admin(loja_atual), view=PainelAdminView(loja_atual))
        btn_voltar.callback = cb_voltar
        
        view.add_item(btn_pix)
        view.add_item(btn_publicar)
        view.add_item(btn_voltar)
        await aplicar_icones_da_loja(interacao.guild_id, view)
        await interacao.response.edit_message(embed=embed, view=view)

def montar_painel_admin(loja: Loja) -> discord.Embed:
    """Embed base do painel administrativo."""
    embed = discord.Embed(
        title=loja.nome,
        description=(
            "Gerencie sua loja, acompanhe as vendas e personalize sua vitrine.\n"
            "Tudo o que você precisa, em um só lugar."
        ),
        color=_cor_do_painel(loja),
    )
    embed.add_field(
        name="Pagamentos",
        value="PIX configurado" if loja.pix_tipo and loja.pix_chave else "Configure sua chave PIX",
        inline=True,
    )
    embed.add_field(
        name="Vitrine",
        value=(
            f"Publicado em <#{loja.painel_canal_id}>"
            if loja.painel_mensagem_id and loja.painel_canal_id
            else "Ainda não publicada"
        ),
        inline=True,
    )
    embed.add_field(
        name="Registros",
        value=f"Canal <#{loja.canal_logs_id}>" if loja.canal_logs_id else "Canal de logs opcional",
        inline=True,
    )
    embed.add_field(
        name="Acesso rápido",
        value="Produtos · Automações · Aparência · Vendas",
        inline=False,
    )
    if loja.banner_url:
        embed.set_image(url=loja.banner_url)
    if loja.logo_url:
        embed.set_thumbnail(url=loja.logo_url)
    embed.set_footer(text="Painel administrativo · Configuração centralizada")
    return embed

# --------------------------------------------------------------------------- #
# Painel Mestre Interativo (Estilo Premium - Versao Publica para Clientes)
# --------------------------------------------------------------------------- #
class PainelMestreView(discord.ui.View):
    """O painel que o CLIENTE vê no canal de vendas."""
    def __init__(self, loja: Loja | None) -> None:
        super().__init__(timeout=None)
        setattr(self, "_categoria_icones", "comprador")
        self.loja = loja
        icones = _icones_da_loja(loja)
        for botao in self.children:
            if botao.custom_id == "painel:vitrine":
                _configurar_icone(botao, "Ver catálogo", "◈")
            elif botao.custom_id == "painel:suporte":
                _configurar_icone(botao, "Suporte", "◦")
        _aplicar_icones_view(self, icones)

    @discord.ui.button(label="◈  Ver catálogo", style=discord.ButtonStyle.primary, custom_id="painel:vitrine")
    async def vitrine(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        await abrir_catalogo(interacao)

    @discord.ui.button(label="◦  Suporte", style=discord.ButtonStyle.secondary, custom_id="painel:suporte")
    async def suporte(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        async with Session() as sessao:
            from models import ConfigTicket

            conf = await sessao.get(ConfigTicket, interacao.guild_id)
            loja = await pegar_loja(sessao, interacao.guild_id)

        canal_id = conf.painel_canal_id if conf and conf.painel_mensagem_id else None
        embed = discord.Embed(
            title="Suporte",
            description=(
                f"Fale com a equipe da loja em <#{canal_id}>."
                if canal_id
                else "O canal de suporte ainda não foi configurado nesta loja."
            ),
            color=_cor_do_painel(loja),
        )
        view = discord.ui.View(timeout=300)
        setattr(view, "_categoria_icones", "comprador")
        if canal_id:
            view.add_item(
                discord.ui.Button(
                    label="↗  Abrir canal",
                    style=discord.ButtonStyle.link,
                    url=f"https://discord.com/channels/{interacao.guild_id}/{canal_id}",
                )
            )

        catalogo = discord.ui.Button(style=discord.ButtonStyle.primary)
        _configurar_icone(
            catalogo,
            "Ver catálogo",
            "◈",
        )

        async def abrir_catalogo_callback(i: discord.Interaction) -> None:
            await abrir_catalogo(i, editar=True)

        catalogo.callback = abrir_catalogo_callback
        view.add_item(catalogo)
        _aplicar_icones_view(view, _icones_da_loja(loja))
        await interacao.response.send_message(embed=embed, view=view, ephemeral=True)

# --------------------------------------------------------------------------- #
# Painel fixo da loja
# --------------------------------------------------------------------------- #
class AbrirLojaView(discord.ui.View):
    """View persistente do painel publicado por /painel publicar."""
    def __init__(self) -> None:
        super().__init__(timeout=None)
        setattr(self, "_categoria_icones", "comprador")

    @discord.ui.button(label="◈  Ver catálogo", style=discord.ButtonStyle.primary, custom_id="loja:abrir")
    async def abrir(self, interacao: discord.Interaction, botao: discord.ui.Button) -> None:
        await abrir_catalogo(interacao)


async def abrir_catalogo(interacao: discord.Interaction, *, editar: bool = False) -> None:
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
        embed = discord.Embed(
            title="Loja sem produtos",
            description="Esta loja ainda não publicou nenhum produto.",
            color=_cor_do_painel(loja),
        )
        if editar:
            await interacao.response.edit_message(embed=embed, view=None)
        else:
            await interacao.response.send_message(embed=embed, ephemeral=True)
        return

    contagem: dict[int, int] = {categoria.id: 0 for categoria in categorias}
    for produto in produtos:
        if produto.categoria_id in contagem:
            contagem[produto.categoria_id] += 1
    sem_categoria = sum(1 for produto in produtos if produto.categoria_id is None)

    cor = _cor_do_painel(loja)
    embed = discord.Embed(
        title=loja.nome,
        description="Escolha uma categoria para explorar o catálogo.",
        color=cor,
    )
    embed.set_footer(text=f"{len(produtos)} produto(s) disponíveis")
    if loja.logo_url:
        embed.set_thumbnail(url=loja.logo_url)
    if loja.banner_url:
        embed.set_image(url=loja.banner_url)
    icones = _icones_da_loja(loja)

    painel = SelecionarCategoria(
        interacao.guild_id,
        categorias,
        contagem,
        sem_categoria,
        loja.nome,
        cor,
        icones,
    )
    painel.embed = embed
    if editar:
        await interacao.response.edit_message(embed=embed, view=painel)
    else:
        await interacao.response.send_message(embed=embed, view=painel, ephemeral=True)
