"""Tabelas do banco de dados.

Tudo e separado por `guild_id`, ou seja, cada servidor tem a sua propria loja
dentro do mesmo bot. Isso ja deixa o caminho pronto para o modelo SaaS
(varios clientes usando o mesmo bot hospedado por voce).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text, false
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

STATUS_PENDENTE = "pendente"
STATUS_PAGO = "pago"
STATUS_CANCELADO = "cancelado"

TIPOS_DE_CHAVE = ("cpf", "cnpj", "email", "telefone", "aleatoria")

# --------------------------------------------------------------------------- #
# Planos da plataforma (o que voce vende para cada cliente)
# --------------------------------------------------------------------------- #
PLANO_FREE = "free"
PLANO_MEDIA = "media"
PLANO_PRO = "pro"

# "Plano" devolvido quando o dono da plataforma desliga a loja de proposito.
PLANO_BLOQUEADO = "bloqueado"

PLANOS_VENDAVEIS = (PLANO_FREE, PLANO_MEDIA, PLANO_PRO)

ROTULOS_DE_PLANO = {
    PLANO_FREE: "Free",
    PLANO_MEDIA: "Media",
    PLANO_PRO: "Pro",
    PLANO_BLOQUEADO: "Bloqueado",
}


def plano_tem_estoque(plano: str) -> bool:
    """Estoque/entrega automatica so existem a partir do plano Media."""
    return plano in (PLANO_MEDIA, PLANO_PRO)


def plano_tem_entrega_automatica(plano: str) -> bool:
    return plano in (PLANO_MEDIA, PLANO_PRO)


def plano_tem_whitelabel(plano: str) -> bool:
    """Trocar nome, foto, banner e bio do bot (a versao mais cara)."""
    return plano == PLANO_PRO


# Tipos de item do estoque
ITEM_LINK = "link"
ITEM_TEXTO = "texto"

TIPOS_DE_ITEM = (ITEM_LINK, ITEM_TEXTO)

# Status de um pagamento de assinatura (plataforma cobrando o cliente)
PAGAMENTO_PENDENTE = "pendente"
PAGAMENTO_APROVADO = "aprovado"
PAGAMENTO_EXPIRADO = "expirado"


def agora() -> datetime:
    """Data/hora atual em UTC (padrao do banco)."""
    return datetime.now(timezone.utc)


def _expirada(momento: Optional[datetime]) -> bool:
    """Compara datas com seguranca (o SQLite devolve datas sem fuso)."""
    if momento is None:
        return False
    if momento.tzinfo is None:
        momento = momento.replace(tzinfo=timezone.utc)
    return momento <= agora()


class Base(DeclarativeBase):
    pass


class Loja(Base):
    """Configuracao da loja de um servidor."""

    __tablename__ = "lojas"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    nome: Mapped[str] = mapped_column(String(100), default="Minha Loja")

    pix_tipo: Mapped[Optional[str]] = mapped_column(String(20))
    pix_chave: Mapped[Optional[str]] = mapped_column(String(200))
    pix_titular: Mapped[Optional[str]] = mapped_column(String(60))
    pix_cidade: Mapped[Optional[str]] = mapped_column(String(40))

    canal_vendas_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    canal_logs_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    # Personalizacao visual
    banner_url: Mapped[Optional[str]] = mapped_column(Text)
    logo_url: Mapped[Optional[str]] = mapped_column(Text)
    cor_hex: Mapped[Optional[str]] = mapped_column(String(7)) # Ex: #7289DA
    icone_catalogo: Mapped[Optional[str]] = mapped_column(String(100))
    icone_suporte: Mapped[Optional[str]] = mapped_column(String(100))
    icone_compra: Mapped[Optional[str]] = mapped_column(String(100))
    icone_voltar: Mapped[Optional[str]] = mapped_column(String(100))
    icones_botoes_json: Mapped[Optional[str]] = mapped_column(Text)

    # Controle para evitar duplicacao de paineis
    painel_mensagem_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    painel_canal_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class Categoria(Base):
    """Categoria usada para organizar os produtos."""

    __tablename__ = "categorias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    nome: Mapped[str] = mapped_column(String(80))
    ordem: Mapped[int] = mapped_column(Integer, default=0)
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class Produto(Base):
    """Produto vendido pela loja."""

    __tablename__ = "produtos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    categoria_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)

    nome: Mapped[str] = mapped_column(String(100))
    descricao: Mapped[Optional[str]] = mapped_column(Text)
    preco_centavos: Mapped[int] = mapped_column(Integer)
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    estoque_limitado: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    entrega_infinita: Mapped[Optional[str]] = mapped_column(Text)
    tipo_entrega_infinita: Mapped[Optional[str]] = mapped_column(String(10))
    alerta_estoque_enviado: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )

    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class Pedido(Base):
    """Pedido gerado quando um cliente clica em "Gerar PIX"."""

    __tablename__ = "pedidos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    produto_id: Mapped[int] = mapped_column(Integer, index=True)

    # Guardamos uma copia dos dados no momento da compra, para o historico
    # nao mudar se o dono editar ou apagar o produto depois.
    produto_nome: Mapped[str] = mapped_column(String(100))
    preco_centavos: Mapped[int] = mapped_column(Integer)

    comprador_id: Mapped[int] = mapped_column(BigInteger, index=True)
    canal_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    mensagem_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    status: Mapped[str] = mapped_column(String(20), default=STATUS_PENDENTE, index=True)
    pix_payload: Mapped[Optional[str]] = mapped_column(Text)

    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    pago_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    confirmado_por: Mapped[Optional[int]] = mapped_column(BigInteger)


class Licenca(Base):
    """Plano de assinatura do servidor (uma linha por guild).

    O plano controla o que a loja pode fazer: estoque e entrega automatica
    a partir do Media, white-label no Pro. Sem linha no banco, vale o Free.
    """

    __tablename__ = "licencas"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    plano: Mapped[str] = mapped_column(String(20), default=PLANO_FREE)

    # False = loja desligada pelo dono da plataforma (bloqueada).
    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    expira_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    observacao: Mapped[Optional[str]] = mapped_column(String(200))

    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)

    def plano_efetivo(self) -> str:
        """Plano que realmente vale agora (bloqueio e expiracao inclusos)."""
        if not self.ativo:
            return PLANO_BLOQUEADO
        if _expirada(self.expira_em):
            return PLANO_FREE
        return self.plano


class Cortesia(Base):
    """Servidor liberado de graca pelo dono da plataforma (amigos).

    Quem esta aqui usa o bot no plano Pro sem pagar nada, sem mexer na
    licenca normal. Alem disso, IDs no .env (CORTESIA_GUILD_IDS) tambem valem.
    """

    __tablename__ = "cortesias"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    concedida_por: Mapped[Optional[int]] = mapped_column(BigInteger)
    motivo: Mapped[Optional[str]] = mapped_column(String(200))
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class BotProprio(Base):
    """Bot proprio de um cliente Pro (white-label).

    O cliente cria a aplicacao dele no Discord e manda o token. Nos hospedamos
    esse bot com a identidade dele (nome, foto, bio). Se a assinatura Pro cair,
    a hospedagem e desligada automaticamente.
    """

    __tablename__ = "bots_proprios"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    token: Mapped[str] = mapped_column(Text)
    application_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    nome_bot: Mapped[Optional[str]] = mapped_column(String(100))

    ativo: Mapped[bool] = mapped_column(Boolean, default=True)
    erro: Mapped[Optional[str]] = mapped_column(String(300))

    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    atualizado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class ItemEstoque(Base):
    """Uma unidade entregavel de um produto.

    Pode ser um link (jogo, foto, arquivo) ou um bloco de texto (conta e
    dados em varias linhas). Cada linha da tabela = 1 unidade de estoque.
    """

    __tablename__ = "estoque"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    produto_id: Mapped[int] = mapped_column(Integer, index=True)

    tipo: Mapped[str] = mapped_column(String(10), default=ITEM_TEXTO)
    conteudo: Mapped[str] = mapped_column(Text)

    entregue: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    pedido_id: Mapped[Optional[int]] = mapped_column(Integer)

    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    entregue_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))


class Pagamento(Base):
    """Cobranca da assinatura do servidor (voce recebendo do seu cliente).

    Cada linha representa um PIX gerado pelo Mercado Pago. Quando o pagamento
    e aprovado, a licenca do servidor e renovada pelo numero de dias daqui.
    """

    __tablename__ = "pagamentos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    solicitante_id: Mapped[int] = mapped_column(BigInteger)

    plano: Mapped[str] = mapped_column(String(20))
    valor_centavos: Mapped[int] = mapped_column(Integer)
    dias: Mapped[int] = mapped_column(Integer, default=30)

    status: Mapped[str] = mapped_column(
        String(20), default=PAGAMENTO_PENDENTE, index=True
    )
    mp_payment_id: Mapped[Optional[str]] = mapped_column(String(40), index=True)
    pix_copia_cola: Mapped[Optional[str]] = mapped_column(Text)
    pix_expiracao: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    pago_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    observacao: Mapped[Optional[str]] = mapped_column(String(200))


class ConfigTicket(Base):
    """Configuracao do sistema de tickets da loja."""

    __tablename__ = "config_tickets"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    categoria_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    canal_logs_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    
    # Painel fixo de tickets
    painel_mensagem_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    painel_canal_id: Mapped[Optional[int]] = mapped_column(BigInteger)

    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)


class Ticket(Base):
    """Um ticket aberto por um cliente."""

    __tablename__ = "tickets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    usuario_id: Mapped[int] = mapped_column(BigInteger, index=True)
    canal_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    
    status: Mapped[str] = mapped_column(String(20), default="aberto") # aberto, fechado
    
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)
    fechado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    fechado_por: Mapped[Optional[int]] = mapped_column(BigInteger)

class ConfigWelcome(Base):
    """Configuracao de boas-vindas do servidor."""
    __tablename__ = "config_welcome"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    canal_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    mensagem: Mapped[str] = mapped_column(Text, default="Seja bem-vindo(a) ao servidor, {user}!")
    ativo: Mapped[bool] = mapped_column(Boolean, default=False)


class ConfigAutomacao(Base):
    """Preferencias de automacao definidas pelo dono de cada loja."""

    __tablename__ = "config_automacoes"

    guild_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    expirar_pedidos: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    finalizar_sorteios: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    alertar_estoque_baixo: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    boas_vindas: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )


class Giveaway(Base):
    """Sorteios realizados no servidor."""
    __tablename__ = "giveaways"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[int] = mapped_column(BigInteger, index=True)
    premio: Mapped[str] = mapped_column(String(100))
    vencedores_qtd: Mapped[int] = mapped_column(Integer, default=1)
    canal_id: Mapped[int] = mapped_column(BigInteger)
    mensagem_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    
    termina_em: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finalizado: Mapped[bool] = mapped_column(Boolean, default=False)
    
    criado_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=agora)

class GiveawayParticipant(Base):
    """Participantes de um sorteio."""
    __tablename__ = "giveaway_participants"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    giveaway_id: Mapped[int] = mapped_column(Integer, index=True)
    usuario_id: Mapped[int] = mapped_column(BigInteger)
