import discord
from discord.ext import commands
from sqlalchemy.future import select
from ..rules.rule_model import Server, ModerationRule, FlaggedMessage, FlaggedMessageVote, RuleType
from ..learning.db import async_session_maker
from ..learning.embedding import generate_embedding
from ..learning.feedback import record_vote_in_flagged_message, update_server_threshold_from_feedback, record_system_feedback

import re
import torch
import torch.nn.functional as F
import redis.asyncio as redis

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

    def __init__(self, bot: commands.Bot, db_session_maker, redis_url="redis://localhost"):
        self.bot = bot
        self.db_session_maker = db_session_maker
        self.redis_url = redis_url
        self.redis = None

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

    async def connect_redis(self):
        if self.redis is None:
            self.redis = await redis.from_url(
                self.redis_url, encoding="utf-8", decode_responses=True
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        guild_id = str(message.guild.id)

        rules = await self.get_server_rules_cached(guild_id)
        if not rules:
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

        threshold = await self.get_server_threshold_cached(guild_id)

        try:
            msg_embedding = await generate_embedding(message.content)
        except Exception as e:
            print(f"[Embedding error] {e}")
            return

        msg_vector = torch.tensor(msg_embedding)
        msg_vector = msg_vector / msg_vector.norm()

        flagged_rule = None
        highest_similarity = 0.0

        for rule in rules:
            if rule.rule_type == RuleType.embedding:
                rule_vector = torch.tensor(rule.embedding_vector)
                rule_vector = rule_vector / rule_vector.norm()

                similarity = F.cosine_similarity(msg_vector, rule_vector, dim=0).item()

                print(f"Message: {message.content[:50]}...")
                print(f"Similarity to rule '{rule.rule_text[:30]}...': {similarity:.4f}")

                if similarity > threshold and similarity > highest_similarity:
                    highest_similarity = similarity
                    flagged_rule = rule

            elif rule.rule_type == RuleType.regex:
                pattern = rule.rule_metadata.get("pattern") if rule.rule_metadata else rule.rule_text
                if re.search(pattern, message.content, re.IGNORECASE):
                    flagged_rule = rule
                    break

            elif rule.rule_type == RuleType.keyword:
                keywords = rule.rule_metadata.get("keywords") if rule.rule_metadata else [rule.rule_text]
                if any(kw.lower() in message.content.lower() for kw in keywords):
                    flagged_rule = rule
                    break

            elif rule.rule_type == RuleType.classifier:
                classifier_name = rule.rule_text
                violation = await self.run_classifier(classifier_name, message.content)
                if violation:
                    flagged_rule = rule
                    highest_similarity = 1.0
                    break

        if flagged_rule:
            review_channel = discord.utils.get(message.guild.text_channels, name=MOD_REVIEW_CHANNEL_NAME)
            if not review_channel:
                print(f"[Warning] No review channel named '{MOD_REVIEW_CHANNEL_NAME}' found in {message.guild.name}.")
                return

            color = confidence_to_color(highest_similarity, threshold)

            embed = discord.Embed(
                title="🚩 Flagged Message",
                description=message.content,
                color=color
            )

            embed.add_field(name="Author", value=message.author.mention, inline=True)
            explanation = FlagReviewButtons.explain_flag(flagged_rule["rule_text"], message.content)
            embed.add_field(name="Why Flagged?", value=explanation, inline=False)
            embed.add_field(name="Rule Matched", value=flagged_rule.rule_text, inline=False)
            embed.add_field(name="Confidence", value=f"{highest_similarity:.2f}", inline=True)

            message_url = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id}"
            embed.add_field(name="Jump to Message", value=f"[Click Here]({message_url})", inline=False)

            image_attachments = [a.url for a in message.attachments
                                 if a.content_type and a.content_type.startswith("image")]
            if image_attachments:
                embed.set_thumbnail(url=image_attachments[0])

            embed.set_footer(text=f"Message ID: {message.id} | Rule ID: {flagged_rule.id}")

            # Create flagged message record first, approved=None because waiting for poll
            async with self.db_session_maker() as session:
                flagged_message_db = FlaggedMessage(
                    message_id=str(message.id),
                    rule_id=flagged_rule.id,
                    approved=False,  # None because waiting on poll
                    moderator_id="system",  # placeholder or bot user ID
                    similarity=highest_similarity
                )
                session.add(flagged_message_db)
                await session.commit()
                await session.refresh(flagged_message_db)

            # Create voting view with 24 hour timeout
            view = FlagReviewButtons(flagged_message_db.id, self.db_session_maker)

            sent_message = await review_channel.send(embed=embed, view=view)
            view.message = sent_message  # keep message ref for editing on timeout
            await view.update_button_labels()  # initialize buttons with vote counts
            await sent_message.edit(view=view)


class FlagReviewButtons(discord.ui.View):
    def __init__(self, flagged_message_id: int, db_session_maker, timeout=86400):  # 24 hours
        super().__init__(timeout=timeout)
        self.flagged_message_id = flagged_message_id
        self.db_session_maker = db_session_maker
        self.message = None  # to be set after sending embed

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
        mod_id = str(interaction.user.id)
        await record_vote_in_flagged_message(
            flagged_message_id=self.flagged_message_id,
            moderator_id=mod_id,
            vote=approve
        )

        await self.update_button_labels()
        await interaction.response.edit_message(view=self)

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

            if approve_count == reject_count:
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
                        content="Poll extended due to tie. Please vote again!",
                        view=self
                    )
                    return  # Do not finalize poll on tie

            approved = approve_count > reject_count
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
