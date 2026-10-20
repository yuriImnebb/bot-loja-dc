"""Tutorial dentro do proprio bot: /ajuda."""

from __future__ import annotations

import discord
from discord.ext import commands

import config

PAGINA_DONO = discord.Embed(
    title="🏪 Sou dono da loja — como montar minha lojinha",
    description="Siga nesta ordem. Leva poucos minutos.",
    color=config.COR_LOJA,
)
PAGINA_DONO.add_field(
    name="1️⃣ Configure o PIX e o nome da loja",
    value=(
        "`/painel configurar`\n"
        "Escolha o tipo da sua chave, cole a chave e o **nome do titular exatamente como aparece no app do banco**. "
        "É para essa conta que o dinheiro vai."
    ),
    inline=False,
)
PAGINA_DONO.add_field(
    name="2️⃣ Organize as categorias",
    value="`/categoria criar` — exemplo: `Assinaturas`, `Camisas`, `Gift Cards`.",
    inline=False,
)
PAGINA_DONO.add_field(
    name="3️⃣ Cadastre os produtos",
    value=(
        "`/produto criar` — preencha nome, preço (ex: `19,90`) e escolha a categoria.\n"
        "Depois use `/produto listar`, `/produto editar` e `/produto alternar` quando quiser mexer."
    ),
    inline=False,
)
PAGINA_DONO.add_field(
    name="4️⃣ Deixe o servidor pronto",
    value=(
        "`/configurar` — eu crio a categoria e os canais da loja e publico o painel sozinho.\n"
        "Ou faça na mão: `/painel canais vendas:#vendas logs:#logs` e `/painel publicar`."
    ),
    inline=False,
)
PAGINA_DONO.add_field(
    name="5️⃣ Entrega automatica (plano Media ou Pro)",
    value=(
        "`/estoque adicionar` — escolha o produto, o formato (**link** ou **texto**) "
        "e cole o conteudo. Quando voce confirmar o pagamento, eu entrego na DM do comprador.\n"
        "Confira com `/estoque listar`."
    ),
    inline=False,
)
PAGINA_DONO.add_field(
    name="6️⃣ Assinatura (planos Media e Pro)",
    value=(
        "`/assinar` — gere o **PIX da assinatura** e libere os recursos do bot neste servidor.\n"
        "O pagamento é automático: confirma sozinho em instantes. Veja seu plano em `/minha-assinatura`."
    ),
    inline=False,
)
PAGINA_DONO.set_footer(text="Confira tudo com /painel info")

PAGINA_CLIENTE = discord.Embed(
    title="🛍️ Quero comprar — como funciona",
    description="O pagamento é por PIX, direto para o dono da loja.",
    color=config.COR_SUCESSO,
)
PAGINA_CLIENTE.add_field(
    name="1️⃣ Abra a loja",
    value="Use `/loja` ou clique em **Ver produtos** no painel do servidor.",
    inline=False,
)
PAGINA_CLIENTE.add_field(
    name="2️⃣ Escolha o produto",
    value="Selecione a categoria e depois o produto para ver preço e descrição.",
    inline=False,
)
PAGINA_CLIENTE.add_field(
    name="3️⃣ Clique em **Gerar PIX**",
    value=(
        "Vou te mandar o QR Code e o **PIX Copia e Cola** (só você vê essa mensagem).\n"
        "Pague no app do seu banco pelo QR Code ou colando o código."
    ),
    inline=False,
)
PAGINA_CLIENTE.add_field(
    name="4️⃣ Envie o comprovante",
    value="Mande o comprovante no canal de vendas e aguarde a confirmação do vendedor.",
    inline=False,
)
PAGINA_CLIENTE.add_field(
    name="5️⃣ Confirmação",
    value="Quando o dono confirmar o pagamento, o bot avisa aqui mesmo no canal.",
    inline=False,
)
PAGINA_CLIENTE.set_footer(text="Dúvida? Fale com o dono da loja.")

PAGINA_AVISOS = discord.Embed(
    title="⚠️ Bom saber",
    color=config.COR_ALERTA,
)
PAGINA_AVISOS.add_field(
    name="Só quem paga recebe",
    value=(
        "O bot **não confirma pagamento sozinho** na versão atual: quem confere o comprovante e clica em "
        "**Confirmar pagamento** é o dono da loja (ou alguém com permissão de Gerenciar Servidor)."
    ),
    inline=False,
)
PAGINA_AVISOS.add_field(
    name="O dinheiro cai direto para o vendedor",
    value=(
        "A chave PIX usada é a do dono da loja. O bot não intermedeia valores, "
        "apenas gera o código de cobrança."
    ),
    inline=False,
)
PAGINA_AVISOS.add_field(
    name="Permissões do bot",
    value=(
        "Para tudo funcionar, garanta que eu tenho **Enviar Mensagens**, **Inserir Links** e "
        "**Anexar Arquivos** nos canais de vendas e de logs."
    ),
    inline=False,
)
PAGINA_AVISOS.add_field(
    name="Pedidos não somem",
    value="Se eu reiniciar, os botões dos pedidos pendentes continuam funcionando normalmente.",
    inline=False,
)

PAGINAS = {
    "dono": PAGINA_DONO,
    "cliente": PAGINA_CLIENTE,
    "avisos": PAGINA_AVISOS,
}


class AjudaView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=300)
        self.pagina = "dono"

        botoes = [
            ("🏪 Sou dono da loja", "dono", discord.ButtonStyle.primary),
            ("🛍️ Quero comprar", "cliente", discord.ButtonStyle.success),
            ("⚠️ Bom saber", "avisos", discord.ButtonStyle.secondary),
        ]
        for rotulo, chave, estilo in botoes:
            botao = discord.ui.Button(label=rotulo, style=estilo)
            botao.callback = self._criar_callback(chave)
            self.add_item(botao)

    def _criar_callback(self, chave: str):
        async def callback(interacao: discord.Interaction) -> None:
            self.pagina = chave
            await interacao.response.edit_message(embed=PAGINAS[chave], view=self)

        return callback


class Ajuda(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @discord.app_commands.command(
        name="ajuda", description="Tutorial completo: como montar e usar a lojinha"
    )
    @discord.app_commands.guild_only()
    async def ajuda(self, interacao: discord.Interaction) -> None:
        await interacao.response.send_message(
            embed=PAGINAS["dono"], view=AjudaView(), ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ajuda(bot))
