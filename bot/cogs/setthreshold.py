from discord import app_commands
from discord.ext import commands
import discord
from sqlalchemy.future import select
from ..learning.db import async_session_maker
from ..rules.rule_model import Server


class Threshold(commands.Cog):
    def __init__(self, bot: commands.Bot, db_session_maker):
        self.bot = bot
        self.db_session_maker = db_session_maker

    async def on_ready(self):
        await self.bot.tree.sync()

    @app_commands.command(name="setthreshold", description="Set the similarity threshold for moderation (0.0 - 1.0).")
    @app_commands.describe(threshold="How strict should the rule matching be?")
    async def set_threshold(self, interaction: discord.Interaction, threshold: float):
        if threshold < 0.0 or threshold > 1.0:
            await interaction.response.send_message("Threshold must be between 0.0 and 1.0.", ephemeral=True)
            return

        async with self.db_session_maker() as session:
            result = await session.execute(
                select(Server).filter_by(discord_guild_id=int(interaction.guild_id))
            )
            server = result.scalars().first()
            if not server:
                await interaction.response.send_message("This server is not yet initialized.", ephemeral=True)
                return

            server.configuration.similarity_threshold = threshold
            await session.commit()

        await interaction.response.send_message(f"Threshold updated to {threshold:.2f} ✅", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Threshold(bot, async_session_maker))
