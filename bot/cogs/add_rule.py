import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy.future import select
from ..rules.rule_model import Server, ModerationRule, RuleType
from ..learning.db import async_session_maker
from ..learning.embedding import generate_embedding

import re
import json


class RuleManager(commands.Cog):
    def __init__(self, bot: commands.Bot, db_session_maker):
        self.bot = bot
        self.db_session_maker = db_session_maker

    @app_commands.command(
        name="addrule",
        description="Add a moderation rule (embedding, regex, keyword, classifier)."
    )
    @app_commands.describe(
        rule_type="Type of rule: embedding | regex | keyword | classifier",
        rule_text="The rule text or pattern",
        metadata="Additional JSON metadata for the rule (optional)"
    )
    @app_commands.choices(
        rule_type=[
            app_commands.Choice(name="Embedding", value="embedding"),
            app_commands.Choice(name="Regex", value="regex"),
            app_commands.Choice(name="Keyword", value="keyword"),
            app_commands.Choice(name="Classifier", value="classifier")
        ]
    )
    async def add_rule(
        self,
        interaction: discord.Interaction,
        rule_type: str,
        rule_text: str,
        metadata: str = None
    ):
        await interaction.response.defer(ephemeral=True)

        guild_id = str(interaction.guild_id)
        if not guild_id:
            await interaction.followup.send("This command must be used in a server.", ephemeral=True)
            return

        if rule_type.lower() not in [rt.value for rt in RuleType]:
            await interaction.followup.send(f"""Invalid rule_type '{rule_type}'.
                                            Must be one of: embedding, regex, keyword, classifier.""", ephemeral=True)
            return

        rule_type_enum = RuleType(rule_type.lower())

        if rule_type_enum == RuleType.regex:
            try:
                re.compile(rule_text)
            except re.error as e:
                await interaction.followup.send(f"Invalid regex pattern: {e}", ephemeral=True)
                return

        # Generate embedding vector for the rule text (async)
        embedding_vector = None
        try:
            embedding_vector = await generate_embedding(rule_text)
        except Exception as e:
            await interaction.followup.send(f"Error generating embedding: {e}", ephemeral=True)
            return

        rule_metadata = None
        if metadata:
            try:
                rule_metadata = json.loads(metadata)
            except Exception as e:
                await interaction.followup.send(f"Invalid JSON for metadata: {e}", ephemeral=True)
                return

        async with self.db_session_maker() as session:
            async with session.begin():
                result = await session.execute(select(Server).filter_by(discord_guild_id=guild_id))
                server = result.scalars().first()
                if server is None:
                    server = Server(discord_guild_id=guild_id)
                    session.add(server)
                    await session.flush()

                new_rule = ModerationRule(
                    server_id=server.id,
                    rule_text=rule_text,
                    embedding_vector=embedding_vector,
                    active=True,
                    rule_type=rule_type_enum,
                    rule_metadata=rule_metadata
                )
                session.add(new_rule)

        await interaction.followup.send(f"Rule added successfully: `{rule_text}` of type `{rule_type_enum.value}`",
                                        ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(RuleManager(bot, async_session_maker))
