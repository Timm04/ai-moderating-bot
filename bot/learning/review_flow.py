import discord
from sqlalchemy.future import select
from ..rules.rule_model import Server, ModerationRule, FlaggedMessage, ServerConfiguration
from ..learning.db import async_session_maker
from ..learning.feedback import record_system_feedback, update_server_threshold_from_feedback
from discord.ui import Select
import logging

_log = logging.getLogger(__name__)
MOD_REVIEW_CHANNEL_NAME = "mod-review"


def confidence_to_color(confidence: float | None, threshold: float) -> discord.Color:
    if confidence is None:
        return discord.Color.orange()

    def interp(a, b, t): return tuple(int(x + (y - x) * t) for x, y in zip(a, b))
    dark_g, light_g = (0, 100, 0), (144, 238, 144)
    dark_r, light_r = (139, 0, 0), (255, 182, 193)

    if confidence >= threshold:
        denom = max(1 - threshold, 1e-6)
        t = min((confidence - threshold) / denom, 1.0)
        rgb = interp(light_g, dark_g, t)
    else:
        denom = max(threshold, 1e-6)
        t = min(confidence / denom, 1.0)
        rgb = interp(dark_r, light_r, t)

    return discord.Color.from_rgb(*rgb)


async def get_threshold_for_guild(guild_id: int) -> float:
    async with async_session_maker() as session:
        server = (await session.execute(
            select(Server).where(Server.discord_guild_id == int(guild_id))
        )).scalars().first()
        if server and server.configuration:
            return float(server.configuration.similarity_threshold or 0.75)
    return 0.75


class RuleCorrectionSelect(Select):
    def __init__(self, flagged_message_id, db_session_maker, view, options):
        self.flagged_message_id = flagged_message_id
        self.db_session_maker = db_session_maker
        self.parent_view = view
        super().__init__(placeholder="Select correct rule...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        from ..rules.rule_model import ModerationRule, FlaggedMessage
        async with self.db_session_maker() as session:
            flagged_msg = await session.get(FlaggedMessage, self.flagged_message_id)
            if not flagged_msg:
                await interaction.response.send_message("Flagged message not found.", ephemeral=True)
                return

            new_rule_id = int(self.values[0])
            new_rule = await session.get(ModerationRule, new_rule_id)
            if not new_rule:
                await interaction.response.send_message("Selected rule not found.", ephemeral=True)
                return

            flagged_msg.rule_id = new_rule_id
            await session.commit()

        # Update embed fields
        embed = interaction.message.embeds[0]
        for i, field in enumerate(embed.fields):
            if field.name == "Rule Matched" or field.name == "Rule (initial)":
                embed.set_field_at(i, name="Rule Matched", value=new_rule.rule_text, inline=False)
        await interaction.message.edit(embed=embed)
        await interaction.response.send_message("Corrected matched rule!", ephemeral=True)


class FlagReviewButtons(discord.ui.View):
    def __init__(self, flagged_message_id: int, db_session_maker, bot: discord.Client, timeout=86400):
        super().__init__(timeout=timeout)
        self.flagged_message_id = flagged_message_id
        self.db_session_maker = db_session_maker
        self.bot = bot
        self.message: discord.Message | None = None
        self.rule_select: RuleCorrectionSelect | None = None

    async def get_moderators(self, guild: discord.Guild):
        return [m for m in guild.members if any(r.permissions.moderate_members for r in m.roles)]

    async def update_button_labels(self, session=None):
        from ..rules.rule_model import FlaggedMessageVote
        owns = session is None
        if owns:
            session = await self.db_session_maker().__aenter__()
        votes = (await session.execute(
            select(FlaggedMessageVote).filter_by(flagged_message_id=self.flagged_message_id)
        )).scalars().all()
        approve = sum(1 for v in votes if v.vote)
        reject = len(votes) - approve
        self.approve.label = f"✅ Approve Flag ({approve})"
        self.reject.label = f"❌ Reject Flag ({reject})"
        if owns:
            await session.__aexit__(None, None, None)

    @discord.ui.button(label="✅ Approve Flag", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, _):
        await self._record_vote_and_maybe_finalize(interaction, True)

    @discord.ui.button(label="❌ Reject Flag", style=discord.ButtonStyle.red)
    async def reject(self, interaction: discord.Interaction, _):
        await self._record_vote_and_maybe_finalize(interaction, False)

    async def _record_vote_and_maybe_finalize(self, interaction: discord.Interaction, approve: bool):
        from ..rules.rule_model import FlaggedMessageVote, FlaggedMessage, Server
        # record vote
        async with self.db_session_maker() as session:
            # upsert vote
            existing = (await session.execute(
                select(FlaggedMessageVote).filter_by(
                    flagged_message_id=self.flagged_message_id,
                    moderator_id=int(interaction.user.id)
                )
            )).scalars().first()
            if existing:
                existing.vote = approve
            else:
                session.add(FlaggedMessageVote(
                    flagged_message_id=self.flagged_message_id,
                    moderator_id=int(interaction.user.id),
                    vote=approve
                ))
            await session.commit()

        await self.update_button_labels()
        await interaction.response.edit_message(view=self)

        # check majority
        async with self.db_session_maker() as session:
            fm = await session.get(FlaggedMessage, self.flagged_message_id)
            if not fm:
                return
            guild = interaction.guild
            mods = await self.get_moderators(guild)
            total = len(mods) or 1

            votes = (await session.execute(
                select(FlaggedMessageVote).filter_by(flagged_message_id=self.flagged_message_id)
            )).scalars().all()
            approve_count = sum(1 for v in votes if v.vote)
            reject_count = len(votes) - approve_count

            majority = 0.75
            # optional: per-server majority from config
            server = (await session.execute(
                select(Server).where(Server.discord_guild_id == int(guild.id))
            )).scalars().first()
            if server and server.configuration and server.configuration.majority_required:
                majority = float(server.configuration.majority_required)

            if approve_count / total >= majority:
                await self._finalize(session, guild, approved=True)
            elif reject_count / total >= majority:
                await self._finalize(session, guild, approved=False)

    async def _finalize(self, session, guild: discord.Guild, approved: bool):
        from ..rules.rule_model import FlaggedMessage, ModerationRule, Server
        fm = await session.get(FlaggedMessage, self.flagged_message_id)
        if not fm:
            return
        fm.approved = approved
        await session.commit()

        # load server + config
        server = (await session.execute(
            select(Server).where(Server.discord_guild_id == int(guild.id))
        )).scalars().first()
        cfg = None
        old_thr = None
        if server:
            cfg = (await session.execute(
                select(ServerConfiguration).where(ServerConfiguration.server_id == server.id)
            )).scalars().first()
            if cfg:
                old_thr = cfg.similarity_threshold

        # trigger threshold update
        rule = await session.get(ModerationRule, fm.rule_id)
        if rule:
            self.bot.loop.create_task(update_server_threshold_from_feedback(rule.server_id))

        # fetch new threshold value
        if cfg:
            await session.refresh(cfg)
        new_thr = cfg.similarity_threshold if cfg else None

        await record_system_feedback(
            flagged_message_id=self.flagged_message_id,
            approved=approved,
            similarity=fm.similarity
        )

        # disable controls and annotate embed
        for c in self.children:
            c.disabled = True

        if self.message and self.message.embeds:
            embed = self.message.embeds[0]
            before = f"{old_thr:.2f}" if old_thr is not None else "N/A"
            after = f"{new_thr:.2f}" if new_thr is not None else "N/A"
            info = f"Threshold (before → after): {before} → {after}\nConfidence at flagging: {fm.similarity:.2f}"
            if fm.similarity is not None:
                info = (
                    f"Threshold (before → after): {before} → {after}\n"
                    f"Confidence at flagging: {fm.similarity:.2f}"
                )
            else:
                info = f"Threshold (before → after): {before} → {after}"
            embed.title = "APPROVED FLAGGED MESSAGE" if approved else "REJECTED FLAGGED MESSAGE"
            embed.color = discord.Color.light_gray()

            # upsert field
            idx = None
            for i, f in enumerate(embed.fields):
                if f.name == "Threshold Adjustment":
                    idx = i
                    break
            if idx is not None:
                embed.set_field_at(idx, name="Threshold Adjustment", value=info, inline=False)
            else:
                embed.add_field(name="Threshold Adjustment", value=info, inline=False)

            await self.message.edit(embed=embed, view=self)


async def post_review_message(
    bot: discord.Client,
    guild: discord.Guild,
    message: discord.Message,
    picked_rule: ModerationRule,
    rules_for_dropdown: list[ModerationRule],
    moderator_id: int | None,
    similarity: float | None,
    db_session_maker,
) -> None:
    """Creates FlaggedMessage, builds embed+view, and sends to #mod-review."""
    # insert DB record
    async with db_session_maker() as session:
        flagged = FlaggedMessage(
            message_id=int(message.id),
            rule_id=picked_rule.id,
            approved=None,
            moderator_id=int(moderator_id or 0),
            similarity=similarity,
            message_excerpt=message.content[:500]
        )
        session.add(flagged)
        await session.commit()
        await session.refresh(flagged)

    review_channel = discord.utils.get(guild.text_channels, name=MOD_REVIEW_CHANNEL_NAME)
    if not review_channel:
        _log.warning(f"No review channel named '{MOD_REVIEW_CHANNEL_NAME}' in {guild.name}.")
        return

    threshold = await get_threshold_for_guild(guild.id)
    color = confidence_to_color(similarity, threshold)

    embed = discord.Embed(
        title="🚩 Flagged Message" if moderator_id is None else "🚩 Flagged by Moderator",
        description=message.content,
        color=color
    )
    embed.add_field(name="Author", value=message.author.mention, inline=True)
    embed.add_field(name="Rule Matched" if moderator_id is None else "Rule (initial)", value=picked_rule.rule_text,
                    inline=False)
    if moderator_id:
        embed.add_field(name="Flagged By", value=f"<@{moderator_id}>", inline=True)
    if similarity is not None:
        embed.add_field(name="Confidence", value=f"{similarity:.2f}", inline=True)

    jump_url = f"https://discord.com/channels/{message.guild.id}/{message.channel.id}/{message.id}"
    embed.add_field(name="Jump to Message", value=f"[Click Here]({jump_url})", inline=False)
    embed.set_footer(text=f"Message ID: {message.id} | Rule ID: {picked_rule.id}")

    # Build view
    view = FlagReviewButtons(flagged.id, db_session_maker, bot)
    options = [discord.SelectOption(label=r.rule_text[:100], value=str(r.id)) for r in rules_for_dropdown]
    rule_select = RuleCorrectionSelect(flagged.id, db_session_maker, view, options)
    view.add_item(rule_select)
    view.rule_select = rule_select

    sent = await review_channel.send(embed=embed, view=view)
    view.message = sent
    await view.update_button_labels()
