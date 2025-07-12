import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy.future import select
from ..rules.rule_model import Server, ModerationRule
from ..learning.db import async_session_maker
from ..learning.embedding import generate_embedding


class RuleManager(commands.Cog):
    def __init__(self, bot: commands.Bot, db_session_maker):
        self.bot = bot
        self.db_session_maker = db_session_maker

    @app_commands.command(
        name="addrule",
        description="Add a new moderation rule to this server."
    )
    @app_commands.describe(rule_text="The text description of the rule, e.g., 'No sarcasm'")
    async def add_rule(self, interaction: discord.Interaction, rule_text: str):
        await interaction.response.defer(ephemeral=True)

        guild_id = str(interaction.guild_id)
        if not guild_id:
            await interaction.followup.send("This command must be used in a server.", ephemeral=True)
            return

        # Generate embedding vector for the rule text (async)
        try:
            embedding_vector = await generate_embedding(rule_text)
        except Exception as e:
            await interaction.followup.send(f"Error generating embedding: {e}", ephemeral=True)
            return

        async with self.db_session_maker() as session:
            async with session.begin():
                result = await session.execute(select(Server).filter_by(discord_guild_id=guild_id))
                server = result.scalars().first()
                if server is None:
                    server = Server(discord_guild_id=guild_id)
                    session.add(server)
                    await session.flush()

                # Create ModerationRule
                new_rule = ModerationRule(
                    server_id=server.id,
                    rule_text=rule_text,
                    embedding_vector=embedding_vector,
                    active=True
                )
                session.add(new_rule)

        await interaction.followup.send(f"Rule added successfully: `{rule_text}`", ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RuleManager(bot, async_session_maker))
