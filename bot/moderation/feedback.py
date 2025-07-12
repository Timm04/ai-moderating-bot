import logging
import numpy as np
from sqlalchemy.future import select
from sqlalchemy import update
from bot.rules.rule_model import Server, ModerationRule, FlaggedMessage
from learning import async_session_maker

_log = logging.getLogger(__name__)


async def get_feedback_similarities(server_id: int, approved: bool) -> list[float]:
    """
    Fetch similarity scores from flagged messages for a given server and approval status.
    """
    async with async_session_maker() as session:
        result = await session.execute(
            select(FlaggedMessage, ModerationRule.metadata)
            .join(ModerationRule, FlaggedMessage.rule_id == ModerationRule.id)
            .where(ModerationRule.server_id == server_id)
            .where(FlaggedMessage.approved == approved)
        )
        rows = result.all()

    similarities = []
    for flagged_msg, rule_meta in rows:
        sim = getattr(flagged_msg, "similarity", None)
        if sim is None and rule_meta and isinstance(rule_meta, dict):
            sim = rule_meta.get("similarity")
        if sim is not None:
            similarities.append(float(sim))
    return similarities


async def update_server_threshold_from_feedback(server_id: int, percentile: int = 25) -> None:
    """
    Update the server's similarity_threshold based on feedback similarities.
    """
    approved_scores = await get_feedback_similarities(server_id, approved=True)
    rejected_scores = await get_feedback_similarities(server_id, approved=False)

    if not approved_scores:
        _log.info(f"No approved feedback for server {server_id}, skipping threshold update.")
        return

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


async def record_feedback(
    message_id: str,
    rule_id: int,
    approved: bool,
    moderator_id: str,
    similarity: float | None = None
) -> None:
    """
    Record moderator feedback on a flagged message.
    """
    async with async_session_maker() as session:
        flagged = FlaggedMessage(
            message_id=message_id,
            rule_id=rule_id,
            approved=approved,
            moderator_id=moderator_id,
            similarity=similarity
        )
        session.add(flagged)
        await session.commit()

    _log.info(
        f"Feedback recorded: message={message_id}, rule={rule_id}, approved={approved}, moderator={moderator_id}"
    )
