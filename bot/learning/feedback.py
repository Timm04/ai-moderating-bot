import logging
from sqlalchemy.future import select
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from bot.rules.rule_model import Server, ModerationRule, FlaggedMessage, FlaggedMessageVote
from ..learning.db import async_session_maker

_log = logging.getLogger(__name__)


async def get_feedback_similarities(server_id: int, approved: bool) -> list[float]:
    """
    Fetch similarity scores from flagged messages for a given server and approval status.
    """
    async with async_session_maker() as session:
        result = await session.execute(
            select(FlaggedMessage, ModerationRule)
            .join(ModerationRule, FlaggedMessage.rule_id == ModerationRule.id)
            .where(ModerationRule.server_id == server_id)
            .where(FlaggedMessage.approved == approved)
        )
        rows = result.all()

    similarities = []
    for flagged_msg, rule in rows:
        sim = getattr(flagged_msg, "similarity", None)
        if sim is not None:
            similarities.append(float(sim))
    return similarities


async def update_server_threshold_from_feedback(server_id: int, percentile: int = 25) -> None:
    """
    Update the server's similarity_threshold based on feedback similarities.
    Adjusts threshold to minimize false positives.
    """
    approved_scores = await get_feedback_similarities(server_id, approved=True)
    rejected_scores = await get_feedback_similarities(server_id, approved=False)

    if not approved_scores:
        _log.info(f"No approved feedback for server {server_id}, skipping threshold update.")
        return

    import numpy as np

    new_threshold = np.percentile(approved_scores, percentile)
    _log.info(f"Computed new threshold={new_threshold:.3f} (percentile={percentile}) for server {server_id}")

    if rejected_scores:
        max_rejected = max(rejected_scores)
        if new_threshold < max_rejected:
            new_threshold = max_rejected + 0.01
            _log.info(f"Adjusted new threshold to {new_threshold:.3f} to avoid false positives")

    async with async_session_maker() as session:
        await session.execute(
            update(Server)
            .where(Server.id == server_id)
            .values(similarity_threshold=new_threshold)
        )
        await session.commit()
    _log.info(f"Updated server {server_id} similarity_threshold to {new_threshold:.3f}")


async def record_vote_in_flagged_message(
    flagged_message_id: int,
    moderator_id: str,
    vote: bool
) -> None:
    """
    Record or update a moderator's vote on a flagged message.
    Prevent duplicate votes by the same moderator.
    """
    async with async_session_maker() as session:
        # Check if vote exists
        existing_vote_res = await session.execute(
            select(FlaggedMessageVote)
            .filter_by(flagged_message_id=flagged_message_id, moderator_id=moderator_id)
        )
        existing_vote = existing_vote_res.scalars().first()

        if existing_vote:
            if existing_vote.vote != vote:
                existing_vote.vote = vote
                await session.commit()
                _log.info(f"Updated vote by mod {moderator_id} on flagged_message {flagged_message_id} to {vote}")
        else:
            new_vote = FlaggedMessageVote(
                flagged_message_id=flagged_message_id,
                moderator_id=moderator_id,
                vote=vote
            )
            session.add(new_vote)
            try:
                await session.commit()
                _log.info(f"Recorded new vote by mod {moderator_id} on flagged_message {flagged_message_id}: {vote}")
            except IntegrityError:
                await session.rollback()
                # Race condition
                _log.warning(f"Vote by mod {moderator_id} on flagged_message {flagged_message_id} already exists.")


async def record_system_feedback(
    flagged_message_id: int,
    approved: bool,
    similarity: float | None = None
) -> None:
    """
    Record final system feedback on flagged message approval/rejection.
    Update the flagged message approved status too.
    """
    async with async_session_maker() as session:
        flagged_msg = await session.get(FlaggedMessage, flagged_message_id)
        if flagged_msg:
            flagged_msg.approved = approved
            if similarity is not None:
                flagged_msg.similarity = similarity
            await session.commit()
            _log.info(f"System feedback recorded on flagged_message {flagged_message_id}: approved={approved}")
        else:
            _log.warning(f"FlaggedMessage {flagged_message_id} not found for system feedback")
