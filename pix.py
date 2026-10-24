"""Geracao do PIX Copia e Cola (BR Code estatico) e do QR Code.

Implementa o padrao EMV do Banco Central para PIX estatico, sem depender de
nenhum gateway. A chave usada e sempre a do dono da loja, ou seja, o dinheiro
cai direto na conta dele.
"""

from __future__ import annotations

import re
import unicodedata
from decimal import Decimal, ROUND_HALF_UP
from io import BytesIO
from typing import Optional

# Codigos dos campos do BR Code
GUI_PIX = "br.gov.bcb.pix"
MOEDA_REAL = "986"
PAIS = "BR"


def _campo(id_campo: str, valor: str) -> str:
    """Monta um campo no formato EMV: ID + tamanho (2 digitos) + valor."""
    return f"{id_campo}{len(valor):02d}{valor}"


def _crc16(payload: str) -> str:
    """CRC16/CCITT-FALSE, exigido pelo padrao do PIX."""
    crc = 0xFFFF
    for byte in payload.encode("utf-8"):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else (crc << 1)
            crc &= 0xFFFF
    return f"{crc:04X}"


def _texto_seguro(texto: str, limite: int, padrao: str) -> str:
    """Remove acentos e caracteres que o banco nao aceita, e corta no limite."""
    sem_acento = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode("ascii")
    limpo = re.sub(r"[^A-Za-z0-9 ]", " ", sem_acento)
    limpo = re.sub(r"\s+", " ", limpo).strip().upper()
    return (limpo or padrao)[:limite]


def normalizar_chave(tipo: Optional[str], chave: str) -> str:
    """Deixa a chave no formato que o Banco Central exige. Levanta ValueError."""
    bruto = (chave or "").strip()
    if not bruto:
        raise ValueError("A chave PIX esta vazia.")

    if tipo == "email":
        if "@" not in bruto:
            raise ValueError("E-mail invalido para chave PIX.")
        return bruto.lower()

    if tipo in {"cpf", "cnpj"}:
        digitos = re.sub(r"\D", "", bruto)
        esperado = 11 if tipo == "cpf" else 14
        if len(digitos) != esperado:
            raise ValueError(f"A chave {tipo.upper()} precisa ter {esperado} digitos (voce enviou {len(digitos)}).")
        return digitos

    if tipo == "telefone":
        digitos = re.sub(r"\D", "", bruto)
        if len(digitos) in (10, 11):
            digitos = "55" + digitos
        if len(digitos) not in (12, 13) or not digitos.startswith("55"):
            raise ValueError("Telefone invalido. Use DDD + numero, exemplo: (11) 91234-5678.")
        return "+" + digitos

    # Chave aleatoria (EVP), no formato UUID
    if re.fullmatch(r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}", bruto):
        return bruto.lower()

    raise ValueError("Chave aleatoria invalida. Cole a chave no formato de UUID que o seu banco gerou.")


def _normalizar_txid(txid: Optional[str]) -> str:
    if not txid:
        return "***"
    limpo = re.sub(r"[^A-Za-z0-9]", "", txid)
    return limpo[:25] or "***"


def gerar_payload(
    tipo_chave: Optional[str],
    chave: str,
    titular: Optional[str],
    cidade: Optional[str],
    valor_reais: Optional[float] = None,
    txid: Optional[str] = None,
) -> str:
    """Devolve o texto do PIX Copia e Cola."""
    chave_normalizada = normalizar_chave(tipo_chave, chave)
    nome_titular = _texto_seguro(titular or "", 25, "RECEBEDOR")
    cidade_titular = _texto_seguro(cidade or "", 15, "BRASIL")

    conta_merchant = _campo("00", GUI_PIX) + _campo("01", chave_normalizada)

    payload = _campo("00", "01")
    payload += _campo("26", conta_merchant)
    payload += _campo("52", "0000")
    payload += _campo("53", MOEDA_REAL)

    if valor_reais is not None and Decimal(str(valor_reais)) > 0:
        valor = Decimal(str(valor_reais)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        payload += _campo("54", f"{valor:.2f}")

    payload += _campo("58", PAIS)
    payload += _campo("59", nome_titular)
    payload += _campo("60", cidade_titular)
    payload += _campo("62", _campo("05", _normalizar_txid(txid)))
    payload += "6304"

    return payload + _crc16(payload)


def gerar_qr_png(payload: str) -> BytesIO:
    """Gera a imagem do QR Code do payload, pronta para enviar no Discord."""
    import qrcode
    
    # Esta operacao e CPU-bound e síncrona. 
    # Idealmente deve ser chamada via run_in_executor para nao travar o bot.
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=8,
        border=2,
    )
    qr.add_data(payload)
    qr.make(fit=True)

    imagem = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    imagem.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer
