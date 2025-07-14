from sqlalchemy import (
    Column, Integer, String, Text, Boolean, DateTime, ForeignKey, JSON, Float, Enum
)
from sqlalchemy.orm import relationship, declarative_base
from sqlalchemy import UniqueConstraint
from datetime import datetime, timezone

import enum

Base = declarative_base()


class RuleType(enum.Enum):
    embedding = "embedding"
    regex = "regex"
    keyword = "keyword"
    classifier = "classifier"


class Server(Base):
    __tablename__ = "servers"
    id = Column(Integer, primary_key=True, autoincrement=True)
    discord_guild_id = Column(String(32), unique=True, nullable=False)
    name = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)

    rules = relationship("ModerationRule", back_populates="server", cascade="all, delete-orphan")
    similarity_threshold = Column(Float, nullable=False, default=0.75)


class ModerationRule(Base):
    __tablename__ = "moderation_rules"
    id = Column(Integer, primary_key=True, autoincrement=True)
    server_id = Column(Integer, ForeignKey("servers.id"), nullable=False)
    rule_text = Column(Text, nullable=False)
    embedding_vector = Column(JSON, nullable=True)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow,
                        onupdate=lambda: datetime.now(timezone.utc))
    rule_metadata = Column(JSON, nullable=True)
    rule_type = Column(Enum(RuleType), default=RuleType.embedding, nullable=False)

    server = relationship("Server", back_populates="rules")
    flagged_messages = relationship("FlaggedMessage", back_populates="rule", cascade="all, delete-orphan")


class FlaggedMessage(Base):
    __tablename__ = "flagged_messages"
    id = Column(Integer, primary_key=True, autoincrement=True)
    message_id = Column(String(64), nullable=False, index=True)  # Discord message ID
    rule_id = Column(Integer, ForeignKey("moderation_rules.id"), nullable=False, index=True)
    approved = Column(Boolean, nullable=True)  # None = pending, True = approved, False = rejected
    moderator_id = Column(String(64), nullable=False)
    similarity = Column(Float, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    message_excerpt = Column(Text, nullable=True)  # Optional: store message text (or snippet) for auditing

    rule = relationship("ModerationRule", back_populates="flagged_messages")


class FlaggedMessageVote(Base):
    __tablename__ = "flagged_message_votes"
    id = Column(Integer, primary_key=True)
    flagged_message_id = Column(Integer, ForeignKey("flagged_messages.id"), nullable=False)
    moderator_id = Column(String(64), nullable=False)
    vote = Column(Boolean, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("flagged_message_id", "moderator_id", name="unique_vote_per_mod"),
    )
