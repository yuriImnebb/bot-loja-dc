"""Conexao com o banco e operacoes de escrita/leitura reutilizadas pelos cogs."""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from sqlalchemy import delete, func, inspect, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import config
from models import (
    PAGAMENTO_APROVADO,
    PAGAMENTO_EXPIRADO,
    PAGAMENTO_PENDENTE,
    PLANO_FREE,
    PLANO_PRO,
    STATUS_CANCELADO,
    STATUS_PAGO,
    STATUS_PENDENTE,
    Base,
    BotProprio,
    ConfigAutomacao,
    Cortesia,
    ItemEstoque,
    Licenca,
    Loja,
    Pagamento,
    Pedido,
    Produto,
    agora,
)

engine = create_async_engine(config.DATABASE_URL, echo=False, future=True)
Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def init_db() -> None:
    """Cria as tabelas que ainda nao existem e otimiza o SQLite."""
    async with engine.begin() as conexao:
        # Habilita o modo WAL para melhor performance em concorrência
        await conexao.exec_driver_sql("PRAGMA journal_mode=WAL")
        await conexao.exec_driver_sql("PRAGMA synchronous=NORMAL")
        await conexao.run_sync(Base.metadata.create_all)
        await conexao.run_sync(_migrar_colunas_produtos)
        await conexao.run_sync(_migrar_colunas_lojas)


def _migrar_colunas_produtos(conexao) -> None:
    """Adiciona campos de estoque sem apagar dados de bancos existentes."""
    colunas = {coluna["name"] for coluna in inspect(conexao).get_columns("produtos")}
    tinha_modo_estoque = "estoque_limitado" in colunas
    novas_colunas = {
        "estoque_limitado": "BOOLEAN NOT NULL DEFAULT FALSE",
        "entrega_infinita": "TEXT",
        "tipo_entrega_infinita": "VARCHAR(10)",
        "alerta_estoque_enviado": "BOOLEAN NOT NULL DEFAULT FALSE",
    }
    for nome, definicao in novas_colunas.items():
        if nome not in colunas:
            conexao.exec_driver_sql(f"ALTER TABLE produtos ADD COLUMN {nome} {definicao}")

    if not tinha_modo_estoque:
        conexao.exec_driver_sql(
            "UPDATE produtos SET estoque_limitado = TRUE "
            "WHERE EXISTS (SELECT 1 FROM estoque "
            "WHERE estoque.produto_id = produtos.id "
            "AND estoque.guild_id = produtos.guild_id)"
        )


def _migrar_colunas_lojas(conexao) -> None:
    """Adiciona configuracoes de icones sem alterar lojas existentes."""
    colunas = {coluna["name"] for coluna in inspect(conexao).get_columns("lojas")}
    novas_colunas = {
        "icone_catalogo": "VARCHAR(100)",
        "icone_suporte": "VARCHAR(100)",
        "icone_compra": "VARCHAR(100)",
        "icone_voltar": "VARCHAR(100)",
        "icones_botoes_json": "TEXT",
    }
    for nome, definicao in novas_colunas.items():
        if nome not in colunas:
            conexao.exec_driver_sql(f"ALTER TABLE lojas ADD COLUMN {nome} {definicao}")


async def obter_config_automacao(guild_id: int) -> ConfigAutomacao:
    """Retorna as preferencias do servidor; automacoes novas comecam desligadas."""
    async with Session() as sessao:
        configuracao = await sessao.get(ConfigAutomacao, guild_id)
        return configuracao or ConfigAutomacao(guild_id=guild_id)


async def alternar_automacao(guild_id: int, nome: str) -> ConfigAutomacao:
    """Inverte uma automacao suportada e persiste a preferencia."""
    campos = {
        "expirar_pedidos",
        "finalizar_sorteios",
        "alertar_estoque_baixo",
        "boas_vindas",
    }
    if nome not in campos:
        raise ValueError(f"Automacao desconhecida: {nome}")

    async with Session() as sessao:
        configuracao = await sessao.get(ConfigAutomacao, guild_id)
        if configuracao is None:
            configuracao = ConfigAutomacao(guild_id=guild_id)
            sessao.add(configuracao)
        setattr(configuracao, nome, not getattr(configuracao, nome))
        await sessao.commit()
        await sessao.refresh(configuracao)
        return configuracao


async def automacao_ativa(guild_id: int, nome: str) -> bool:
    configuracao = await obter_config_automacao(guild_id)
    if not hasattr(configuracao, nome):
        raise ValueError(f"Automacao desconhecida: {nome}")
    return bool(getattr(configuracao, nome))


async def pegar_loja(sessao: AsyncSession, guild_id: int) -> Loja:
    """Busca a configuracao da loja; cria uma vazia se for a primeira vez."""
    loja = await sessao.get(Loja, guild_id)
    if loja is None:
        loja = Loja(guild_id=guild_id)
        sessao.add(loja)
        await sessao.commit()
        await sessao.refresh(loja)
    return loja


async def marcar_pago(pedido_id: int, confirmado_por: int) -> tuple[Optional[Pedido], str]:
    """Marca o pedido como pago. Retorna (pedido, motivo_da_falha)."""
    async with Session() as sessao:
        resultado = await sessao.execute(
            update(Pedido)
            .where(Pedido.id == pedido_id, Pedido.status == STATUS_PENDENTE)
            .values(
                status=STATUS_PAGO,
                pago_em=agora(),
                confirmado_por=confirmado_por,
            )
            .execution_options(synchronize_session=False)
        )
        if resultado.rowcount == 1:
            await sessao.commit()
            pedido = await sessao.get(Pedido, pedido_id)
            return pedido, ""

        pedido = await sessao.get(Pedido, pedido_id)
        if pedido is None:
            return None, "Pedido nao encontrado."
        return pedido, f"Este pedido ja esta como **{pedido.status}**."


async def marcar_cancelado(pedido_id: int, cancelado_por: int) -> tuple[Optional[Pedido], str]:
    """Marca o pedido como cancelado. Retorna (pedido, motivo_da_falha)."""
    async with Session() as sessao:
        pedido = await sessao.get(Pedido, pedido_id)
        if pedido is None:
            return None, "Pedido nao encontrado."
        if pedido.status != STATUS_PENDENTE:
            return pedido, f"Este pedido ja esta como **{pedido.status}**."

        pedido.status = STATUS_CANCELADO
        pedido.confirmado_por = cancelado_por
        await sessao.commit()
        await sessao.refresh(pedido)
        return pedido, ""


async def ids_de_pedidos_pendentes() -> list[int]:
    """IDs de todos os pedidos pendentes (usado ao ligar o bot)."""
    async with Session() as sessao:
        resultado = await sessao.scalars(select(Pedido.id).where(Pedido.status == STATUS_PENDENTE))
        return list(resultado.all())


# --------------------------------------------------------------------------- #
# Licencas / planos
# --------------------------------------------------------------------------- #
async def pegar_licenca(sessao: AsyncSession, guild_id: int) -> Licenca:
    """Busca a licenca do servidor; cria uma no plano Free se nao existir."""
    licenca = await sessao.get(Licenca, guild_id)
    if licenca is None:
        licenca = Licenca(guild_id=guild_id, plano=PLANO_FREE)
        sessao.add(licenca)
        await sessao.commit()
        await sessao.refresh(licenca)
    return licenca


async def em_cortesia(sessao: AsyncSession, guild_id: int) -> bool:
    """True se este servidor foi liberado de graca (amigos / cortesia)."""
    if guild_id in config.CORTESIA_GUILD_IDS:
        return True
    return await sessao.get(Cortesia, guild_id) is not None


# Cache simples para o plano do servidor (evita bater no banco em todo clique de botao)
# Estrutura: {guild_id: (plano, timestamp_expiracao)}
_cache_plano: dict[int, tuple[str, float]] = {}
CACHE_PLANO_DURACAO = 60.0 # 1 minuto

async def plano_do_servidor(guild_id: int) -> str:
    """Plano que vale agora neste servidor (ja contando bloqueio/expiracao)."""
    import time
    agora_ts = time.time()
    
    if guild_id in _cache_plano:
        plano, expira = _cache_plano[guild_id]
        if agora_ts < expira:
            return plano

    async with Session() as sessao:
        if await em_cortesia(sessao, guild_id):
            plano = PLANO_PRO
        else:
            licenca = await pegar_licenca(sessao, guild_id)
            plano = licenca.plano_efetivo()
        
    _cache_plano[guild_id] = (plano, agora_ts + CACHE_PLANO_DURACAO)
    return plano


async def pegar_licenca_detalhada(guild_id: int) -> tuple[str, bool, Optional[object]]:
    """Devolve (plano_efetivo, ativo, expira_em) para telas de status."""
    async with Session() as sessao:
        if await em_cortesia(sessao, guild_id):
            return PLANO_PRO, True, None
        licenca = await pegar_licenca(sessao, guild_id)
        return licenca.plano_efetivo(), licenca.ativo, licenca.expira_em


# --------------------------------------------------------------------------- #
# Cortesia (liberar de graca para amigos)
# --------------------------------------------------------------------------- #
async def conceder_cortesia(
    guild_id: int, concedida_por: Optional[int] = None, motivo: Optional[str] = None
) -> Cortesia:
    """Libera o servidor de graca no plano Pro (sem prazo)."""
    async with Session() as sessao:
        cortesia = await sessao.get(Cortesia, guild_id)
        if cortesia is None:
            cortesia = Cortesia(guild_id=guild_id)
            sessao.add(cortesia)
        cortesia.concedida_por = concedida_por
        cortesia.motivo = (motivo or "")[:200] or None
        await sessao.commit()
        await sessao.refresh(cortesia)
        return cortesia


async def remover_cortesia(guild_id: int) -> bool:
    """Tira a cortesia do servidor. Devolve True se ela existia."""
    async with Session() as sessao:
        cortesia = await sessao.get(Cortesia, guild_id)
        if cortesia is None:
            return False
        await sessao.delete(cortesia)
        await sessao.commit()
        return True


async def listar_cortesias() -> list[Cortesia]:
    """Todos os servidores liberados de graca (do banco)."""
    async with Session() as sessao:
        return list((await sessao.scalars(select(Cortesia).order_by(Cortesia.criado_em))).all())


async def definir_plano(
    guild_id: int, plano: str, dias: Optional[int] = None, observacao: Optional[str] = None
) -> Licenca:
    """Define o plano de um servidor. `dias` renova a partir de agora."""
    async with Session() as sessao:
        licenca = await pegar_licenca(sessao, guild_id)
        licenca.plano = plano
        licenca.ativo = True

        if dias:
            licenca.expira_em = agora() + timedelta(days=dias)
        elif plano == PLANO_FREE:
            licenca.expira_em = None

        if observacao is not None:
            licenca.observacao = observacao[:200] or None

        licenca.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(licenca)
        return licenca


async def definir_bloqueio(guild_id: int, bloqueado: bool) -> Licenca:
    """Liga/desliga a loja de um servidor sem mexer no plano contratado."""
    async with Session() as sessao:
        licenca = await pegar_licenca(sessao, guild_id)
        licenca.ativo = not bloqueado
        licenca.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(licenca)
        return licenca


async def limpar_configuracao_loja(guild_id: int) -> Optional[Loja]:
    """Zera a configuracao da loja (nome, PIX e canais).

    Catalogo, estoque e pedidos ficam intactos. Devolve None se a loja
    nunca foi configurada nesse servidor.
    """
    async with Session() as sessao:
        loja = await sessao.get(Loja, guild_id)
        if loja is None:
            return None

        loja.nome = "Minha Loja"
        loja.pix_tipo = None
        loja.pix_chave = None
        loja.pix_titular = None
        loja.pix_cidade = None
        loja.canal_vendas_id = None
        loja.canal_logs_id = None

        await sessao.commit()
        await sessao.refresh(loja)
        return loja


async def listar_licencas() -> list[tuple[Licenca, Optional[str]]]:
    """Todas as licencas com o nome da loja (se ela ja foi configurada)."""
    async with Session() as sessao:
        resultado = await sessao.execute(
            select(Licenca, Loja.nome)
            .outerjoin(Loja, Loja.guild_id == Licenca.guild_id)
            .order_by(Licenca.guild_id)
        )
        return [(linha[0], linha[1]) for linha in resultado.all()]


async def lojas_bloqueadas() -> list[int]:
    async with Session() as sessao:
        resultado = await sessao.scalars(select(Licenca.guild_id).where(Licenca.ativo.is_(False)))
        return list(resultado.all())


# --------------------------------------------------------------------------- #
# Estoque (planos Media e Pro)
# --------------------------------------------------------------------------- #
async def adicionar_estoque(
    guild_id: int, produto_id: int, tipo: str, conteudos: list[str]
) -> int:
    """Adiciona unidades ao estoque de um produto. Devolve quantas entraram."""
    if not conteudos:
        return 0

    async with Session() as sessao:
        produto = await sessao.get(Produto, produto_id)
        if produto is None or produto.guild_id != guild_id:
            return 0

        itens = [
            ItemEstoque(guild_id=guild_id, produto_id=produto_id, tipo=tipo, conteudo=conteudo)
            for conteudo in conteudos
        ]
        sessao.add_all(itens)
        produto.estoque_limitado = True
        produto.alerta_estoque_enviado = False
        await sessao.commit()
    return len(itens)


async def contar_estoque(guild_id: int, produto_id: int) -> int:
    """Unidades ainda disponiveis (nao entregues) de um produto."""
    async with Session() as sessao:
        total = await sessao.scalar(
            select(func.count())
            .select_from(ItemEstoque)
            .where(
                ItemEstoque.guild_id == guild_id,
                ItemEstoque.produto_id == produto_id,
                ItemEstoque.entregue.is_(False),
            )
        )
        return int(total or 0)


async def produto_usa_estoque(guild_id: int, produto_id: int) -> bool:
    """True quando o produto foi configurado com quantidade limitada."""
    async with Session() as sessao:
        produto = await sessao.get(Produto, produto_id)
        return bool(produto and produto.guild_id == guild_id and produto.estoque_limitado)


async def marcar_alerta_estoque_baixo(
    guild_id: int, produto_id: int, limite: int
) -> Optional[int]:
    """Marca um unico aviso quando o estoque limitado cruza o limite configurado."""
    async with Session() as sessao:
        produto = await sessao.get(Produto, produto_id)
        if (
            produto is None
            or produto.guild_id != guild_id
            or not produto.estoque_limitado
            or produto.alerta_estoque_enviado
        ):
            return None

        disponivel = int(
            await sessao.scalar(
                select(func.count())
                .select_from(ItemEstoque)
                .where(
                    ItemEstoque.guild_id == guild_id,
                    ItemEstoque.produto_id == produto_id,
                    ItemEstoque.entregue.is_(False),
                )
            )
            or 0
        )
        if disponivel > limite:
            return None

        produto.alerta_estoque_enviado = True
        await sessao.commit()
        return disponivel


async def contar_estoque_por_produto(guild_id: int, produto_ids: list[int]) -> dict[int, int]:
    """Disponibilidade de varios produtos de uma vez (telas de vitrine)."""
    if not produto_ids:
        return {}

    async with Session() as sessao:
        resultado = await sessao.execute(
            select(ItemEstoque.produto_id, func.count())
            .where(
                ItemEstoque.guild_id == guild_id,
                ItemEstoque.entregue.is_(False),
                ItemEstoque.produto_id.in_(produto_ids),
            )
            .group_by(ItemEstoque.produto_id)
        )
        return {produto_id: int(total) for produto_id, total in resultado.all()}


async def listar_estoque(
    guild_id: int,
    produto_id: Optional[int] = None,
    incluir_entregues: bool = False,
    limite: int = 25,
) -> list[ItemEstoque]:
    """Itens do estoque, do mais antigo para o mais novo."""
    consulta = select(ItemEstoque).where(ItemEstoque.guild_id == guild_id)
    if produto_id is not None:
        consulta = consulta.where(ItemEstoque.produto_id == produto_id)
    if not incluir_entregues:
        consulta = consulta.where(ItemEstoque.entregue.is_(False))

    async with Session() as sessao:
        return list((await sessao.scalars(consulta.order_by(ItemEstoque.id.desc()).limit(limite))).all())


async def remover_item_estoque(guild_id: int, item_id: int) -> bool:
    """Apaga um item do estoque (por id). Devolve True se existia."""
    async with Session() as sessao:
        item = await sessao.get(ItemEstoque, item_id)
        if item is None or item.guild_id != guild_id:
            return False
        await sessao.delete(item)
        await sessao.commit()
        return True


async def remover_proxima_unidade_estoque(guild_id: int, produto_id: int) -> bool:
    """Remove a unidade disponivel mais antiga, sem afetar itens ja entregues."""
    async with Session() as sessao:
        item_id = await sessao.scalar(
            select(ItemEstoque.id)
            .where(
                ItemEstoque.guild_id == guild_id,
                ItemEstoque.produto_id == produto_id,
                ItemEstoque.entregue.is_(False),
            )
            .order_by(ItemEstoque.id)
            .limit(1)
        )
        if item_id is None:
            return False

        resultado = await sessao.execute(
            delete(ItemEstoque).where(
                ItemEstoque.id == item_id,
                ItemEstoque.guild_id == guild_id,
                ItemEstoque.produto_id == produto_id,
                ItemEstoque.entregue.is_(False),
            )
        )
        if resultado.rowcount != 1:
            await sessao.rollback()
            return False

        await sessao.commit()
        return True


async def limpar_estoque(guild_id: int, produto_id: int) -> int:
    """Remove todas as unidades ainda nao entregues de um produto."""
    async with Session() as sessao:
        itens = list(
            (
                await sessao.scalars(
                    select(ItemEstoque).where(
                        ItemEstoque.guild_id == guild_id,
                        ItemEstoque.produto_id == produto_id,
                        ItemEstoque.entregue.is_(False),
                    )
                )
            ).all()
        )
        for item in itens:
            await sessao.delete(item)
        await sessao.commit()
        return len(itens)


async def consumir_item_estoque(
    guild_id: int, produto_id: int, pedido_id: int
) -> Optional[ItemEstoque]:
    """Separa a proxima unidade disponivel e marca como entregue.

    Retorna None quando o estoque acabou.
    """
    async with Session() as sessao:
        for _ in range(3):
            item = await sessao.scalar(
                select(ItemEstoque)
                .where(
                    ItemEstoque.guild_id == guild_id,
                    ItemEstoque.produto_id == produto_id,
                    ItemEstoque.entregue.is_(False),
                )
                .order_by(ItemEstoque.id)
                .limit(1)
            )
            if item is None:
                return None

            resultado = await sessao.execute(
                update(ItemEstoque)
                .where(
                    ItemEstoque.id == item.id,
                    ItemEstoque.entregue.is_(False),
                )
                .values(entregue=True, pedido_id=pedido_id, entregue_em=agora())
                .execution_options(synchronize_session=False)
            )
            if resultado.rowcount == 1:
                await sessao.commit()
                await sessao.refresh(item)
                return item
            await sessao.rollback()

        return None


# --------------------------------------------------------------------------- #
# Pagamentos das assinaturas (Mercado Pago -> sua conta)
# --------------------------------------------------------------------------- #
def _expirado(momento: Optional[object]) -> bool:
    """Compara datas com seguranca (o SQLite devolve datas sem fuso)."""
    if momento is None:
        return False
    from datetime import timezone as _tz

    if getattr(momento, "tzinfo", None) is None:
        momento = momento.replace(tzinfo=_tz.utc)  # type: ignore[attr-defined]
    return momento <= agora()


async def criar_pagamento(
    guild_id: int,
    solicitante_id: int,
    plano: str,
    valor_centavos: int,
    dias: int,
) -> Pagamento:
    """Cria uma cobranca pendente da assinatura de um servidor."""
    async with Session() as sessao:
        pagamento = Pagamento(
            guild_id=guild_id,
            solicitante_id=solicitante_id,
            plano=plano,
            valor_centavos=valor_centavos,
            dias=dias,
            status=PAGAMENTO_PENDENTE,
        )
        sessao.add(pagamento)
        await sessao.commit()
        await sessao.refresh(pagamento)
        return pagamento


async def atualizar_pix_do_pagamento(
    pagamento_id: int,
    mp_payment_id: Optional[str],
    copia_cola: Optional[str],
    expiracao: Optional[object] = None,
) -> None:
    """Guarda os dados do PIX devolvidos pelo Mercado Pago."""
    async with Session() as sessao:
        pagamento = await sessao.get(Pagamento, pagamento_id)
        if pagamento is None:
            return
        pagamento.mp_payment_id = mp_payment_id[:40] if mp_payment_id else None
        pagamento.pix_copia_cola = copia_cola
        pagamento.pix_expiracao = expiracao
        await sessao.commit()


async def pegar_pagamento(pagamento_id: int) -> Optional[Pagamento]:
    async with Session() as sessao:
        return await sessao.get(Pagamento, pagamento_id)


async def pagamento_por_mp_id(mp_payment_id: str) -> Optional[Pagamento]:
    async with Session() as sessao:
        return await sessao.scalar(
            select(Pagamento).where(Pagamento.mp_payment_id == str(mp_payment_id))
        )


async def pagamentos_pendentes(limite: int = 25) -> list[Pagamento]:
    """Cobrancas ainda nao pagas, das mais antigas para as mais novas."""
    async with Session() as sessao:
        return list(
            (
                await sessao.scalars(
                    select(Pagamento)
                    .where(Pagamento.status == PAGAMENTO_PENDENTE)
                    .order_by(Pagamento.id)
                    .limit(limite)
                )
            ).all()
        )


async def pagamento_pendente_do_guild(guild_id: int) -> Optional[Pagamento]:
    """Ultima cobranca pendente de um servidor (usado pelo botao "Ja paguei")."""
    async with Session() as sessao:
        return await sessao.scalar(
            select(Pagamento)
            .where(
                Pagamento.guild_id == guild_id,
                Pagamento.status == PAGAMENTO_PENDENTE,
            )
            .order_by(Pagamento.id.desc())
            .limit(1)
        )


async def marcar_pagamento_aprovado(pagamento_id: int) -> Optional[Pagamento]:
    """Confirma o pagamento e renova a licenca do servidor.

    Devolve None se a cobranca nao existe. Se ja estava aprovada, nao renova
    de novo (evita liberar o dobro quando o webhook chega duas vezes).
    """
    async with Session() as sessao:
        pagamento = await sessao.get(Pagamento, pagamento_id)
        if pagamento is None:
            return None
        if pagamento.status == PAGAMENTO_APROVADO:
            return pagamento

        pagamento.status = PAGAMENTO_APROVADO
        pagamento.pago_em = agora()
        await sessao.commit()
        await sessao.refresh(pagamento)

    await definir_plano(
        pagamento.guild_id,
        pagamento.plano,
        pagamento.dias,
        observacao=f"assinatura paga (pagamento #{pagamento.id})",
    )
    return pagamento


async def marcar_pagamento_expirado(pagamento_id: int) -> None:
    """Fecha uma cobranca que nao foi paga a tempo."""
    async with Session() as sessao:
        pagamento = await sessao.get(Pagamento, pagamento_id)
        if pagamento is None or pagamento.status != PAGAMENTO_PENDENTE:
            return
        pagamento.status = PAGAMENTO_EXPIRADO
        pagamento.observacao = "PIX nao pago a tempo"
        await sessao.commit()


def pagamento_venceu(pagamento: Pagamento) -> bool:
    """True se o prazo do PIX ja passou."""
    return pagamento.status == PAGAMENTO_PENDENTE and _expirado(pagamento.pix_expiracao)


# --------------------------------------------------------------------------- #
# Bot proprio do cliente (white-label do plano Pro)
# --------------------------------------------------------------------------- #
async def salvar_bot_proprio(
    guild_id: int, token: str, application_id: Optional[int], nome_bot: Optional[str]
) -> BotProprio:
    """Guarda (ou atualiza) o token do bot proprio de um servidor."""
    async with Session() as sessao:
        registro = await sessao.get(BotProprio, guild_id)
        if registro is None:
            registro = BotProprio(guild_id=guild_id)
            sessao.add(registro)
        registro.token = token
        registro.application_id = application_id
        registro.nome_bot = nome_bot
        registro.ativo = True
        registro.erro = None
        registro.atualizado_em = agora()
        await sessao.commit()
        await sessao.refresh(registro)
        return registro


async def pegar_bot_proprio(guild_id: int) -> Optional[BotProprio]:
    async with Session() as sessao:
        return await sessao.get(BotProprio, guild_id)


async def remover_bot_proprio(guild_id: int) -> bool:
    """Apaga o token guardado. Devolve True se existia."""
    async with Session() as sessao:
        registro = await sessao.get(BotProprio, guild_id)
        if registro is None:
            return False
        await sessao.delete(registro)
        await sessao.commit()
        return True


async def listar_bots_proprios() -> list[BotProprio]:
    async with Session() as sessao:
        return list((await sessao.scalars(select(BotProprio).order_by(BotProprio.criado_em))).all())


async def marcar_erro_bot_proprio(guild_id: int, erro: Optional[str]) -> None:
    async with Session() as sessao:
        registro = await sessao.get(BotProprio, guild_id)
        if registro is None:
            return
        registro.erro = (erro or "")[:300] or None
        registro.atualizado_em = agora()
        await sessao.commit()


async def ids_de_pedidos_pendentes_do_guild(guild_id: int) -> list[int]:
    """IDs dos pedidos pendentes de um servidor (usado ao ligar um bot proprio)."""
    async with Session() as sessao:
        resultado = await sessao.scalars(
            select(Pedido.id).where(
                Pedido.guild_id == guild_id, Pedido.status == STATUS_PENDENTE
            )
        )
        return list(resultado.all())
