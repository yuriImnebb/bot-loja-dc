"""Configuracoes centrais da plataforma, lidas do arquivo .env."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

# Suporte ao PyInstaller: define a pasta base correta
if getattr(sys, 'frozen', False):
    # Se estiver rodando como .exe
    BASE_DIR = Path(sys._MEIPASS)
    # O .env e a pasta data devem ficar na mesma pasta do arquivo .exe (nao na temporaria)
    EXE_DIR = Path(sys.executable).parent
    load_dotenv(EXE_DIR / ".env")
    DATA_DIR = EXE_DIR / "data"
else:
    # Se estiver rodando como script normal
    BASE_DIR = Path(__file__).resolve().parent
    load_dotenv(BASE_DIR / ".env")
    DATA_DIR = BASE_DIR / "data"

DATA_DIR.mkdir(exist_ok=True)


def _booleano(nome: str, padrao: bool) -> bool:
    bruto = os.getenv(nome)
    if bruto is None:
        return padrao
    return bruto.strip().lower() in {"1", "true", "sim", "yes", "on"}


def _lista_de_ids(nome: str) -> list[int]:
    bruto = os.getenv(nome, "")
    return [int(parte) for parte in (x.strip() for x in bruto.split(",")) if parte.isdigit()]


BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()

DATABASE_URL: str = os.getenv("DATABASE_URL", "").strip() or (
    f"sqlite+aiosqlite:///{(DATA_DIR / 'lojas.db').as_posix()}"
)

API_ENABLED: bool = _booleano("API_ENABLED", True)
API_HOST: str = os.getenv("API_HOST", "0.0.0.0").strip() or "0.0.0.0"
API_PORT: int = int(os.getenv("API_PORT", "8000").strip() or 8000)

DEV_GUILD_IDS: list[int] = _lista_de_ids("DEV_GUILD_IDS")

# Dono da plataforma (voce). Quem estiver aqui usa os comandos /master.
OWNER_IDS: list[int] = _lista_de_ids("OWNER_IDS")

# Segredo compartilhado com o seu gateway de pagamento para liberar planos.
WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "").strip()

# --------------------------------------------------------------------------- #
# Cobranca das assinaturas (Mercado Pago)
# --------------------------------------------------------------------------- #
# Token de PRODUCAO da SUA conta Mercado Pago. O PIX cai direto na sua conta.
MERCADOPAGO_ACCESS_TOKEN: str = os.getenv("MERCADOPAGO_ACCESS_TOKEN", "").strip()

# URL publica desta API, para o Mercado Pago avisar o pagamento na hora.
# Sem isso o bot ainda funciona: ele mesmo confere os pagamentos pendentes.
# Ex: https://meu-bot.seudominio.com
PUBLIC_URL: str = os.getenv("PUBLIC_URL", "").strip().rstrip("/")

PLANO_MEDIA_PRECO_CENTAVOS: int = int(
    os.getenv("PLANO_MEDIA_PRECO_CENTAVOS", "2990").strip() or 2990
)
PLANO_PRO_PRECO_CENTAVOS: int = int(
    os.getenv("PLANO_PRO_PRECO_CENTAVOS", "5990").strip() or 5990
)

# Quantos dias cada assinatura libera.
PLANO_DIAS: int = int(os.getenv("PLANO_DIAS", "30").strip() or 30)

# Servidores com o bot liberado de graca (amigos/cortesia).
# Exemplo: 123456789012345678,987654321098765432
CORTESIA_GUILD_IDS: list[int] = _lista_de_ids("CORTESIA_GUILD_IDS")


def preco_do_plano(plano: str) -> int:
    """Preco da assinatura (em centavos) do plano informado."""
    if plano == "pro":
        return PLANO_PRO_PRECO_CENTAVOS
    return PLANO_MEDIA_PRECO_CENTAVOS


# Cores padrao das mensagens
COR_LOJA = 0x2B2D31
COR_SUCESSO = 0x23A559
COR_ALERTA = 0xF0B232
COR_ERRO = 0xF23F43
COR_PREMIUM = 0x7289DA
