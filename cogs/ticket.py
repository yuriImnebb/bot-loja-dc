import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional
from sqlalchemy import select, update
from db import Session
from models import ConfigTicket, Ticket, agora
import config
from utils import exigir_admin, eh_admin
from views import aplicar_icones_da_loja

class TicketOpenView(discord.ui.View):
    """View com o botao de 'Abrir Ticket' que fica no canal publico."""
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Abrir Ticket", emoji="🎫", style=discord.ButtonStyle.primary, custom_id="ticket:abrir")
    async def abrir_ticket(self, interacao: discord.Interaction, botao: discord.ui.Button):
        async with Session() as sessao:
            conf = await sessao.get(ConfigTicket, interacao.guild_id)
            if not conf or not conf.categoria_id:
                return await interacao.response.send_message("O sistema de tickets nao foi configurado pelo administrador (categoria ausente).", ephemeral=True)
            
            # Verifica se ja tem ticket aberto
            ticket_aberto = await sessao.scalar(
                select(Ticket).where(Ticket.guild_id == interacao.guild_id, Ticket.usuario_id == interacao.user.id, Ticket.status == "aberto")
            )
            if ticket_aberto:
                canal = interacao.guild.get_channel(ticket_aberto.canal_id)
                if canal:
                    return await interacao.response.send_message(f"Voce ja possui um ticket aberto em {canal.mention}!", ephemeral=True)

        await interacao.response.defer(ephemeral=True)
        
        categoria = interacao.guild.get_channel(conf.categoria_id)
        if not categoria:
            return await interacao.followup.send("Erro: Categoria de tickets nao encontrada. Avise um administrador.", ephemeral=True)

        # Criar canal
        overwrites = {
            interacao.guild.default_role: discord.PermissionOverwrite(read_messages=False),
            interacao.user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
            interacao.guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True)
        }
        
        canal = await interacao.guild.create_text_channel(
            name=f"ticket-{interacao.user.name}",
            category=categoria,
            overwrites=overwrites,
            reason=f"Ticket aberto por {interacao.user}"
        )

        async with Session() as sessao:
            novo_ticket = Ticket(
                guild_id=interacao.guild_id,
                usuario_id=interacao.user.id,
                canal_id=canal.id,
                status="aberto"
            )
            sessao.add(novo_ticket)
            await sessao.commit()

        embed = discord.Embed(
            title=f"🎫 Ticket de {interacao.user.display_name}",
            description="Descreva seu problema ou dúvida abaixo. Nossa equipe entrará em contato em breve.\n\nPara fechar o ticket, use o botão abaixo.",
            color=config.COR_PREMIUM
        )
        view = TicketManageView(novo_ticket.id)
        await aplicar_icones_da_loja(interacao.guild_id, view)
        await canal.send(content=f"{interacao.user.mention} | <@&{interacao.guild.owner_id}>", embed=embed, view=view)
        
        await interacao.followup.send(f"✅ Ticket aberto em {canal.mention}!", ephemeral=True)

class TicketManageView(discord.ui.View):
    """View dentro do canal do ticket para fechar/gerenciar."""
    def __init__(self, ticket_id: int):
        super().__init__(timeout=None)
        self.ticket_id = ticket_id

    @discord.ui.button(label="Fechar Ticket", emoji="🔒", style=discord.ButtonStyle.danger, custom_id="ticket:fechar")
    async def fechar(self, interacao: discord.Interaction, botao: discord.ui.Button):
        await interacao.response.defer()
        
        async with Session() as sessao:
            ticket = await sessao.get(Ticket, self.ticket_id)
            if not ticket or ticket.status == "fechado":
                return
            
            ticket.status = "fechado"
            ticket.fechado_em = agora()
            ticket.fechado_por = interacao.user.id
            await sessao.commit()

        await interacao.followup.send("O ticket será fechado em 5 segundos...")
        import asyncio
        await asyncio.sleep(5)
        try:
            await interacao.channel.delete(reason=f"Ticket fechado por {interacao.user}")
        except:
            pass

class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def publicar_painel(self, interacao: discord.Interaction):
        """Chamado pelo botao no Painel Admin."""
        async with Session() as sessao:
            conf = await sessao.get(ConfigTicket, interacao.guild_id)
            if not conf:
                conf = ConfigTicket(guild_id=interacao.guild_id)
                sessao.add(conf)
                await sessao.commit()

        embed = discord.Embed(
            title="🎫 Central de Suporte",
            description="Clique no botão abaixo para abrir um ticket e falar com nossa equipe.",
            color=config.COR_PREMIUM
        )
        # Tenta usar banner/logo da loja se disponivel
        from models import Loja
        async with Session() as sessao:
            loja = await sessao.get(Loja, interacao.guild_id)
            if loja:
                if loja.banner_url: embed.set_image(url=loja.banner_url)
                if loja.logo_url: embed.set_thumbnail(url=loja.logo_url)

        view = TicketOpenView()
        await aplicar_icones_da_loja(interacao.guild_id, view)
        msg = await interacao.channel.send(embed=embed, view=view)
        
        async with Session() as sessao:
            await sessao.execute(
                update(ConfigTicket)
                .where(ConfigTicket.guild_id == interacao.guild_id)
                .values(painel_mensagem_id=msg.id, painel_canal_id=interacao.channel_id)
            )
            await sessao.commit()
            
        embed_sucesso = discord.Embed(
            title="✅ Sucesso!",
            description=f"Painel de tickets publicado com sucesso em {interacao.channel.mention}!",
            color=config.COR_SUCESSO
        )
        view_sucesso = discord.ui.View()
        setattr(view_sucesso, "_categoria_icones", "tickets")
        btn_voltar = discord.ui.Button(label="Voltar", emoji="⬅️", style=discord.ButtonStyle.danger)
        
        async def cb_voltar(i: discord.Interaction):
            from views import montar_painel_admin, PainelAdminView, pegar_loja
            async with Session() as sessao:
                loja = await pegar_loja(sessao, i.guild_id)
            await i.response.edit_message(embed=montar_painel_admin(loja), view=PainelAdminView(loja))
        
        btn_voltar.callback = cb_voltar
        view_sucesso.add_item(btn_voltar)
        await aplicar_icones_da_loja(interacao.guild_id, view_sucesso)

        if interacao.response.is_done():
            await interacao.edit_original_response(embed=embed_sucesso, view=view_sucesso)
        else:
            await interacao.response.edit_message(embed=embed_sucesso, view=view_sucesso)

    @commands.Cog.listener()
    async def on_ready(self):
        self.bot.add_view(TicketOpenView())
        # Nota: TicketManageView nao pode ser persistente global sem o ticket_id no custom_id, 
        # mas como ele so vive dentro do canal temporario, ta ok.

    @app_commands.command(name="ticket_config", description="Configura a categoria dos tickets")
    @app_commands.describe(categoria="Categoria onde os canais de ticket serao criados")
    @exigir_admin()
    async def ticket_config(self, interacao: discord.Interaction, categoria: discord.CategoryChannel):
        async with Session() as sessao:
            conf = await sessao.get(ConfigTicket, interacao.guild_id)
            if not conf:
                conf = ConfigTicket(guild_id=interacao.guild_id, categoria_id=categoria.id)
                sessao.add(conf)
            else:
                conf.categoria_id = categoria.id
            await sessao.commit()
            
        await interacao.response.send_message(f"✅ Categoria de tickets definida para **{categoria.name}**.", ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(TicketCog(bot))
