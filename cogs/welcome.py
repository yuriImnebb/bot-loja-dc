import discord
from discord.ext import commands
from db import Session, automacao_ativa
from models import ConfigWelcome
import logging

log = logging.getLogger(__name__)

class WelcomeCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        if not await automacao_ativa(member.guild.id, "boas_vindas"):
            return

        async with Session() as sessao:
            conf = await sessao.get(ConfigWelcome, member.guild.id)
            if not conf or not conf.ativo or not conf.canal_id:
                return
            
            canal = member.guild.get_channel(conf.canal_id)
            if not canal:
                try:
                    canal = await self.bot.fetch_channel(conf.canal_id)
                except discord.HTTPException as erro:
                    log.warning(
                        "Nao consegui buscar o canal de boas-vindas %s: %s",
                        conf.canal_id,
                        erro,
                    )
                    return
            
            msg = conf.mensagem.replace("{user}", member.mention).replace("{server}", member.guild.name)
            try:
                await canal.send(msg)
            except discord.HTTPException as erro:
                log.warning(
                    "Nao consegui enviar boas-vindas no servidor %s: %s",
                    member.guild.id,
                    erro,
                )

async def setup(bot: commands.Bot):
    await bot.add_cog(WelcomeCog(bot))
