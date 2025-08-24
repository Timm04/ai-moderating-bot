import logging
import discord
from discord.ext import commands
from sqlalchemy.future import select
import torch
import torch.nn.functional as F

from ..learning.db import async_session_maker
from ..rules.rule_model import Server, ModerationRule
from ..learning.embedding import generate_embedding
from ..learning.review_flow import post_review_message

_log = logging.getLogger(__name__)
MOD_REVIEW_CHANNEL_NAME = "mod-review"
FLAG_EMOJI = "🚩"


class Message_flagging(commands.Cog):
    def __init__(self, bot: commands.Bot, db_session_maker):
        self.bot = bot
        self.db_session_maker = db_session_maker

    async def get_moderators(self, guild: discord.Guild) -> list[discord.Member]:
        """Return members that have moderate_members permission."""
        return [
            m for m in guild.members
            if any(r.permissions.moderate_members for r in m.roles)
        ]

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent):
        # Only handle the flag emoji
        if str(payload.emoji) != FLAG_EMOJI:
            return

        guild = self.bot.get_guild(payload.guild_id)
        if not guild:
            return

        member = guild.get_member(payload.user_id)
        if not member or member.bot:
            return

        # Ensure only moderators can trigger manual flagging
        moderators = await self.get_moderators(guild)
        if member not in moderators:
            return

        channel = guild.get_channel(payload.channel_id)
        if not channel:
            return

        try:
            message = await channel.fetch_message(payload.message_id)
        except discord.NotFound:
            return

        # Remove the reaction to prevent repeated triggers
        try:
            await message.remove_reaction(FLAG_EMOJI, member)
        except discord.Forbidden:
            pass
        except discord.HTTPException:
            pass

        # Load server + rules
        async with async_session_maker() as session:
            server = (await session.execute(
                select(Server).where(Server.discord_guild_id == int(guild.id))
            )).scalars().first()
            if not server:
                return

            rules = (await session.execute(
                select(ModerationRule)
                .where(
                    ModerationRule.server_id == server.id,
                    ModerationRule.active.is_(True)  # explicit SQL boolean
                )
                .order_by(ModerationRule.id.asc())
            )).scalars().all()

        if not rules:
            try:
                await channel.send("No rules configured for this server.", delete_after=8)
            except Exception:
                pass
            return

        # Auto-pick the first rule
        picked_rule = rules[0]

        # Optional: compute similarity vs picked rule; if it fails, continue
        similarity = None
        try:
            emb = await generate_embedding(message.content)
            msg_vec = torch.tensor(emb)
            msg_vec = msg_vec / msg_vec.norm()
            rule_vec = torch.tensor(picked_rule.embedding_vector)
            rule_vec = rule_vec / rule_vec.norm()
            similarity = F.cosine_similarity(msg_vec, rule_vec, dim=0).item()
        except Exception as e:
            _log.warning(f"[manualflagging] Similarity computation failed: {e}")

        # Reuse shared review flow (creates DB record, sends embed+view with dropdown)
        await post_review_message(
            bot=self.bot,
            guild=guild,
            message=message,
            picked_rule=picked_rule,
            rules_for_dropdown=rules,
            moderator_id=int(member.id),   # who flagged it
            similarity=similarity,
            db_session_maker=self.db_session_maker,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Message_flagging(bot, async_session_maker))
