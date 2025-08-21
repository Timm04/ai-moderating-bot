import discord
from discord.ext import commands
from sqlalchemy.future import select
from ..rules.rule_model import Server, ModerationRule, FlaggedMessage, FlaggedMessageVote, ServerConfiguration
from ..learning.db import async_session_maker
from ..learning.embedding import generate_embedding
from ..learning.feedback import record_vote_in_flagged_message, update_server_threshold_from_feedback, record_system_feedback
from ..learning.review_flow import post_review_message
from discord.ui import Select
from sqlalchemy.orm import joinedload
import logging
import torch
import torch.nn.functional as F

_log = logging.getLogger(__name__)

MOD_REVIEW_CHANNEL_NAME = "mod-review"
EXTEND_TIMEOUT_SECONDS = 3600


def confidence_to_color(confidence: float, threshold: float) -> discord.Color:
    """
    Map confidence and threshold to a color:
    - If confidence >= threshold: green scale (dark green for higher confidence)
    - Else: red scale (dark red for lower confidence)
    Both scales from 0.1 to 1.0 in intensity.
    """

    def interpolate_color(start_rgb, end_rgb, factor):
        return tuple(
            int(start + (end - start) * factor)
            for start, end in zip(start_rgb, end_rgb)
        )

    # Dark green and light green RGB
    dark_green = (0, 100, 0)
    light_green = (144, 238, 144)  # lightgreen

    # Dark red and light red RGB
    dark_red = (139, 0, 0)
    light_red = (255, 182, 193)  # lightpink

    if confidence >= threshold:
        # Normalize factor between 0 and 1 based on how much above threshold
        factor = min((confidence - threshold) / (1 - threshold), 1.0) if threshold < 1 else 1.0
        rgb = interpolate_color(light_green, dark_green, factor)
    else:
        # Normalize factor between 0 and 1 based on how far below threshold
        factor = min(confidence / threshold, 1.0) if threshold > 0 else 1.0
        rgb = interpolate_color(dark_red, light_red, factor)

    return discord.Color.from_rgb(*rgb)


class MessageMonitor(commands.Cog):
    CACHE_TTL_SECONDS = 600  # 10 minutes cache

    def __init__(self, bot: commands.Bot, db_session_maker):
        self.bot = bot
        self.db_session_maker = db_session_maker

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        guild_id = int(message.guild.id)

        async with self.db_session_maker() as session:
            result = await session.execute(
                select(Server).options(joinedload(Server.configuration)).filter_by(discord_guild_id=guild_id)
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

        threshold = server.configuration.similarity_threshold
        print(f"Threshold for guild {guild_id}: {threshold}")
        try:
            msg_embedding = await generate_embedding(message.content)
        except Exception as e:
            print(f"[Embedding error] {e}")
            return

        msg_vector = torch.tensor(msg_embedding)
        msg_vector = msg_vector / msg_vector.norm()

        flagged_rule = None
        highest_similarity = 0.0
        _log.info(f"Using threshold: {threshold}")

        for rule in rules:
            # if rule.rule_type == RuleType.embedding:
            rule_vector = torch.tensor(rule.embedding_vector)
            rule_vector = rule_vector / rule_vector.norm()

            similarity = F.cosine_similarity(msg_vector, rule_vector, dim=0).item()

            _log.info(f"Message: {message.content[:50]}...")
            _log.info(f"Similarity to rule '{rule.rule_text[:30]}...': {similarity:.4f}")

            if similarity > threshold and similarity > highest_similarity:
                highest_similarity = similarity
                flagged_rule = rule

        if flagged_rule:
            async with self.db_session_maker() as session:
                rules = (await session.execute(
                    select(ModerationRule).where(
                        ModerationRule.server_id == flagged_rule.server_id,
                        ModerationRule.active.is_(True)  # SQLAlchemy boolean
                    ).order_by(ModerationRule.id.asc())
                )).scalars().all()

            await post_review_message(
                bot=self.bot,
                guild=message.guild,
                message=message,
                picked_rule=flagged_rule,
                rules_for_dropdown=rules,
                moderator_id=None,
                similarity=highest_similarity,
                db_session_maker=self.db_session_maker
            )


class RuleCorrectionSelect(Select):
    def __init__(self, flagged_message_id, db_session_maker, view, options):
        self.flagged_message_id = flagged_message_id
        self.db_session_maker = db_session_maker
        self.parent_view = view  # to call view update methods

        # We'll initialize options later in the view, to have all rules
        super().__init__(placeholder="Select correct rule...",
                         min_values=1,
                         max_values=1,
                         options=options)

    async def callback(self, interaction: discord.Interaction):
        new_rule_id = int(self.values[0])
        async with self.db_session_maker() as session:
            flagged_msg = await session.get(FlaggedMessage, self.flagged_message_id)
            if not flagged_msg:
                await interaction.response.send_message("Flagged message not found.", ephemeral=True)
                return

            # Update flagged message's rule_id
            flagged_msg.rule_id = new_rule_id
            await session.commit()

            # Get new rule text
            new_rule = await session.get(ModerationRule, new_rule_id)
            if not new_rule:
                await interaction.response.send_message("Selected rule not found.", ephemeral=True)
                return

            # Update the embed
            embed = interaction.message.embeds[0]
            # Update Rule Matched field
            for i, field in enumerate(embed.fields):
                if field.name == "Rule Matched":
                    embed.set_field_at(i, name="Rule Matched", value=new_rule.rule_text, inline=False)
                if field.name == "Why Flagged?":
                    explanation = self.parent_view.explain_flag(new_rule.rule_text, embed.description)
                    embed.set_field_at(i, name="Why Flagged?", value=explanation, inline=False)

            await interaction.message.edit(embed=embed)
            await interaction.response.send_message("Corrected matched rule!", ephemeral=True)


class FlagReviewButtons(discord.ui.View):
    def __init__(self, flagged_message_id: int, db_session_maker, bot: commands.Bot, timeout=86400):  # 24 hours
        super().__init__(timeout=timeout)
        self.flagged_message_id = flagged_message_id
        self.db_session_maker = db_session_maker
        self.bot = bot
        self.message = None  # to be set after sending embed
        self.rule_select = None

    async def get_moderators(self, guild: discord.Guild) -> list[discord.Member]:
        """
        Get a list of members with 'moderator' permissions in the guild.
        """
        return [
            member for member in guild.members
            if any(role.permissions.moderate_members for role in member.roles)
        ]

    @staticmethod
    def explain_flag(reason: str, message: str) -> str:
        """
        Simple overlap explanation – highlights shared tokens between rule and message.
        """
        rule_tokens = set(reason.lower().split())
        msg_tokens = set(message.lower().split())
        overlap = rule_tokens & msg_tokens

        if not overlap:
            return "Matched based on semantic similarity."

        return f"Matched on: {', '.join(sorted(overlap))}"

    async def update_button_labels(self, session=None):
        owns_session = session is None
        if owns_session:
            session = await self.db_session_maker().__aenter__()

        votes_res = await session.execute(
            select(FlaggedMessageVote).filter_by(flagged_message_id=self.flagged_message_id)
        )
        votes = votes_res.scalars().all()
        approve_count = sum(1 for v in votes if v.vote)
        reject_count = len(votes) - approve_count

        self.approve.label = f"✅ Approve Flag ({approve_count})"
        self.reject.label = f"❌ Reject Flag ({reject_count})"

        if owns_session:
            await session.__aexit__(None, None, None)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # Optional: Check if user has mod role here
        return True

    @discord.ui.button(label="✅ Approve Flag", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.record_vote(interaction, True)

    @discord.ui.button(label="❌ Reject Flag", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.record_vote(interaction, False)

    async def record_vote(self, interaction: discord.Interaction, approve: bool):
        mod_id = int(interaction.user.id)
        await record_vote_in_flagged_message(
            flagged_message_id=self.flagged_message_id,
            moderator_id=mod_id,
            vote=approve
        )

        await self.update_button_labels()
        await interaction.response.edit_message(view=self)

        async with self.db_session_maker() as session:
            votes_res = await session.execute(
                select(FlaggedMessageVote).filter_by(flagged_message_id=self.flagged_message_id)
            )
            votes = votes_res.scalars().all()
            approve_count = sum(1 for v in votes if v.vote)
            reject_count = len(votes) - approve_count

            # Get total number of moderators in the guild
            guild = interaction.guild
            mods = await self.get_moderators(guild)
            total_mods = len(mods)

            if total_mods == 0:
                return

            # Check if 75% of mods have voted the same way
            if approve_count / total_mods >= 0.75:
                await self.finalize_poll(session, approved=True, guild=guild)
            elif reject_count / total_mods >= 0.75:
                await self.finalize_poll(session, approved=False, guild=guild)

    async def finalize_poll(self, session, approved: bool, guild: discord.Guild):
        flagged_msg = await session.get(FlaggedMessage, self.flagged_message_id)
        if flagged_msg:
            flagged_msg.approved = approved
            await session.commit()

            server = (await session.execute(
                select(Server).where(Server.discord_guild_id == int(guild.id))
            )).scalar_one_or_none()

            cfg = None
            old_threshold = None
            new_threshold = None

            if server:
                cfg = (await session.execute(
                    select(ServerConfiguration).where(ServerConfiguration.server_id == server.id)
                )).scalar_one_or_none()
                if cfg:
                    old_threshold = cfg.similarity_threshold

            rule = await session.get(ModerationRule, flagged_msg.rule_id)
            if rule:
                self.bot.loop.create_task(update_server_threshold_from_feedback(rule.server_id))

            if cfg:
                await session.refresh(cfg)
                new_threshold = cfg.similarity_threshold

            await record_system_feedback(
                flagged_message_id=self.flagged_message_id,
                approved=approved,
                similarity=flagged_msg.similarity
            )

        for child in self.children:
            child.disabled = True

        if self.message and self.message.embeds:
            embed = self.message.embeds[0]

            before = f"{old_threshold:.2f}" if old_threshold is not None else "N/A"
            after = f"{new_threshold:.2f}" if new_threshold is not None else "N/A"
            confidence = f"{flagged_msg.similarity:.2f}"

            info_field_name = "Threshold Adjustment"
            info_field_value = f"Threshold (before → after): {before} → {after}\nConfidence at flagging: {confidence}"

            # Check if field already exists, replace it; else add new
            field_index = None
            for i, field in enumerate(embed.fields):
                if field.name == info_field_name:
                    field_index = i
                    break

            if field_index is not None:
                embed.set_field_at(field_index, name=info_field_name, value=info_field_value, inline=False)
            else:
                embed.add_field(name=info_field_name, value=info_field_value, inline=False)

            embed.title = "APPROVED FLAGGED MESSAGE" if approved else "REJECTED FLAGGED MESSAGE"
            embed.color = discord.Color.light_gray()
            await self.message.edit(embed=embed, view=self)

    async def on_timeout(self):
        async with self.db_session_maker() as session:
            flagged_msg = await session.get(FlaggedMessage, self.flagged_message_id)
            if not flagged_msg:
                return

            votes_res = await session.execute(
                select(FlaggedMessageVote).filter_by(flagged_message_id=self.flagged_message_id)
            )
            votes = votes_res.scalars().all()
            approve_count = sum(1 for v in votes if v.vote)
            reject_count = len(votes) - approve_count

            guild = flagged_msg.guild
            mods = await self.get_moderators(guild)
            total_mods = len(mods)

            if total_mods == 0:
                return

            approval_ratio = approve_count / total_mods
            rejection_ratio = reject_count / total_mods

            if approval_ratio >= 0.75:
                approved = True
            elif rejection_ratio >= 0.75:
                approved = False
            else:
                if self.message:
                    await self.message.channel.send(
                        f"@here The vote on flagged message ID {self.flagged_message_id} ended in a tie. "
                        "Please review and vote manually. Extending poll by 1 hour."
                    )
                    # Reset timeout and restart the timer
                    self.timeout = EXTEND_TIMEOUT_SECONDS
                    self._task = self.bot.loop.create_task(self._start_timeout())

                    # Enable buttons again for voting
                    for child in self.children:
                        child.disabled = False
                    # Edit the existing message to reflect poll extension and re-enable buttons
                    await self.message.edit(
                        content="Poll extended due to insufficient majority. Please vote again!",
                        view=self
                    )
                    return  # Do not finalize poll on tie

            flagged_msg.approved = approved
            await session.commit()

            rule = await session.get(ModerationRule, flagged_msg.rule_id)
            if rule:
                server_id = rule.server_id
                self.bot.loop.create_task(update_server_threshold_from_feedback(server_id))

        await record_system_feedback(
            flagged_message_id=self.flagged_message_id,
            approved=approved,
            similarity=flagged_msg.similarity
        )

        self.disable_all_items()
        for child in self.children:
            child.disabled = True

        result_text = "✅ Approved" if approved else "❌ Rejected"
        if self.message:
            await self.message.edit(
                content=f"Poll ended: {result_text}",
                view=self
                )


async def setup(bot: commands.Bot):
    await bot.add_cog(MessageMonitor(bot, async_session_maker))
