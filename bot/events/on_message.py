import discord
from discord.ext import commands
from sqlalchemy.future import select
from rules.rule_model import Server, ModerationRule, FlaggedMessage
from learning import generate_embedding, async_session_maker
import torch
import torch.nn.functional as F

MOD_REVIEW_CHANNEL_NAME = "mod-review"
SIMILARITY_THRESHOLD = 0.75


class MessageMonitor(commands.Cog):
    def __init__(self, bot: commands.Bot, db_session_maker):
        self.bot = bot
        self.db_session_maker = db_session_maker

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        guild_id = str(message.guild.id)

        async with self.db_session_maker() as session:
            result = await session.execute(
                select(Server).filter_by(discord_guild_id=guild_id)
            )
            server = result.scalars().first()
            if server is None:
                return

            result = await session.execute(
                select(ModerationRule).filter_by(server_id=server.id, active=True)
            )
            rules = result.scalars().all()
            if not rules:
                return

        # Embed the incoming message
        try:
            msg_embedding = await generate_embedding(message.content)
        except Exception as e:
            print(f"[Embedding error] {e}")
            return

        msg_vector = torch.tensor(msg_embedding)

        # Find closest rule via cosine similarity
        flagged_rule = None
        highest_similarity = 0

        threshold = getattr(server, "similarity_threshold", None) or 0.75  # fallback default

        for rule in rules:
            rule_vector = torch.tensor(rule.embedding_vector)
            similarity = F.cosine_similarity(msg_vector, rule_vector, dim=0).item()
            if similarity > threshold and similarity > highest_similarity:
                highest_similarity = similarity
                flagged_rule = rule

        if flagged_rule:
            # Send to mod-review channel
            review_channel = discord.utils.get(message.guild.text_channels, name=MOD_REVIEW_CHANNEL_NAME)
            if not review_channel:
                return  # Channel doesn't exist

            embed = discord.Embed(
                title="🚩 Flagged Message",
                description=message.content,
                color=discord.Color.orange()
            )
            embed.add_field(name="Author", value=message.author.mention, inline=True)
            embed.add_field(name="Rule Matched", value=flagged_rule.rule_text, inline=False)
            embed.add_field(name="Confidence", value=f"{highest_similarity:.2f}", inline=True)
            embed.add_field(name="Reason",
                            value=f"Matched rule: `{flagged_rule.rule_text}`\nConfidence: `{highest_similarity:.2f}`")
            embed.set_footer(text=f"Message ID: {message.id} | Rule ID: {flagged_rule.id}")

            view = FlagReviewButtons(message.id, flagged_rule.id)
            await review_channel.send(embed=embed, view=view)


class FlagReviewButtons(discord.ui.View):
    def __init__(self, message_id: int, rule_id: int):
        super().__init__(timeout=None)
        self.message_id = message_id
        self.rule_id = rule_id

    @discord.ui.button(label="✅ Approve Flag", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.log_feedback(interaction, approved=True)

    @discord.ui.button(label="❌ Reject Flag", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.log_feedback(interaction, approved=False)

    async def log_feedback(self, interaction: discord.Interaction, approved: bool):
        async with async_session_maker() as session:
            async with session.begin():
                feedback = FlaggedMessage(
                    message_id=self.message_id,
                    rule_id=self.rule_id,
                    approved=approved,
                    moderator_id=interaction.user.id
                )
                session.add(feedback)

        await interaction.response.send_message(
            "Feedback recorded. ✅" if approved else "Flag rejected. ❌",
            ephemeral=True
        )
        self.disable_all_items()
        await interaction.message.edit(view=self)


async def setup(bot: commands.Bot):
    await bot.add_cog(MessageMonitor(bot, async_session_maker))
